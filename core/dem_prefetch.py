"""
dem_prefetch.py
===============
DEM タイルの**面での事前取得**（プリフェッチ）。1 点の取得は `core/dem.py`。

  - bbox → zoom-14 位置の列挙（zoom-15 の 4 枚は位置にぶら下がる）
  - 優先順位つき降下（dem5a → dem5b → dem_png）と欠損マスク
  - ワーカープールと進捗・件数の集計

⚠️ **`core/dem.py` から切り出した**（B-141・2026-08-29）＝分割閾値に当たったため
だが、割った線は**関心事**＝*いま要る 1 点*と*これから要る面*で寿命が違う
（前者は計算の最中に呼ばれ、後者は利用者が明示的に始める長い操作）。

⚠️ **`dem` の名前は属性参照で引く**（`from core.dem import CACHE_DIR` にしない）
＝テストが `dem.CACHE_DIR` や `dem._fetch_tile` を差し替えるので、束縛を写すと
差し替えが効かなくなる（＝**代役が本物より寛容**の裏返しで、こちらは本物が
差し替わらない形の空振り）。
"""

import os
import queue
import threading

import numpy as np
from PIL import Image

from core import dem
from core.config import logger


def _void_mask(arr: np.ndarray) -> np.ndarray:
    """無効値ピクセル (128, 0, 0) = 海・データ欠損 の真偽マスクを返す。

    arr は (H, W, 3) の RGB 配列。get_elevation の実行時フォールバックと
    同一のセマンティクスで「無効」を定義し、プリフェッチの降下判定に使う。
    """
    return (arr[:, :, 0] == 128) & (arr[:, :, 1] == 0) & (arr[:, :, 2] == 0)


# ============================================================
# タイル事前取得
# ============================================================

def _iter_dem_positions(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
):
    """bbox 内の zoom-14 位置を順次 yield する。

    Yields:
        (x14, y14, dem14_subdir, dem14_path, zoom15_tiles)
        zoom15_tiles = [(x15, y15, subdir5a, path5a, subdir5b, path5b), ...]

    zoom-15 sub-tiles は bbox にクリップされる（端の zoom-14 位置では最大4枚→1〜4枚）。
    """
    lat_n = max(lat1, lat2)
    lat_s = min(lat1, lat2)
    lon_w = min(lon1, lon2)
    lon_e = max(lon1, lon2)

    x14_nw, y14_nw, _, _ = dem._tile_coords(lat_n, lon_w, 14)
    x14_se, y14_se, _, _ = dem._tile_coords(lat_s, lon_e, 14)
    x15_nw, y15_nw, _, _ = dem._tile_coords(lat_n, lon_w, 15)
    x15_se, y15_se, _, _ = dem._tile_coords(lat_s, lon_e, 15)

    for x14 in range(x14_nw, x14_se + 1):
        for y14 in range(y14_nw, y14_se + 1):
            dem14_subdir = os.path.join(dem.CACHE_DIR, "dem_png", str(x14))
            dem14_path   = os.path.join(dem14_subdir, f"{y14}.png")

            x15_lo = max(x14 * 2,     x15_nw)
            x15_hi = min(x14 * 2 + 1, x15_se)
            y15_lo = max(y14 * 2,     y15_nw)
            y15_hi = min(y14 * 2 + 1, y15_se)

            zoom15_tiles = []
            for x15 in range(x15_lo, x15_hi + 1):
                for y15 in range(y15_lo, y15_hi + 1):
                    subdir5a = os.path.join(dem.CACHE_DIR, "dem5a_png", str(x15))
                    path5a   = os.path.join(subdir5a, f"{y15}.png")
                    subdir5b = os.path.join(dem.CACHE_DIR, "dem5b_png", str(x15))
                    path5b   = os.path.join(subdir5b, f"{y15}.png")
                    zoom15_tiles.append((x15, y15, subdir5a, path5a, subdir5b, path5b))

            yield x14, y14, dem14_subdir, dem14_path, zoom15_tiles


def _is_cached(path: str) -> bool:
    """**使える**キャッシュが在るか（B-141）。単なる存在では不十分。

    🔴 **存在＝有効と読むと、壊れたタイルが「取得済み」として素通りする**
    （B-136 と同じ不変条件の、事前取得側の口）。事前取得の目的は*オフラインで
    使えること*なので、ここで見逃すと**利用者は面を取り切ったつもりで、
    現地で粗い層か標高 0 に落ちる**。

    ⚠️ **全復号はしない**＝実測（1033 タイル・2026-08-29）で
    存在のみ 3.9µs / CRC まで 69µs / 全復号 810µs（1000 タイルあたり +0.81 秒）。
    **書き込み途中で切れた PNG は CRC で捕まる**（実測で確認）ので、10 倍以上
    高い全復号は要らない。取りこぼしても計算経路（`dem._fetch_tile`）が直す
    ＝ここは*選別*であって最後の砦ではない。
    """
    if not os.path.exists(path):
        return False
    try:
        Image.open(path).verify()
        return True
    except Exception as e:
        logger.debug("cached tile is broken (will refetch): path=%s error=%s", path, e)
        return False


def _process_position(
    x14: int, y14: int,
    dem14_subdir: str, dem14_path: str,
    zoom15_tiles: list,
    force: bool,
    counts: dict,
    lock: threading.Lock,
) -> None:
    """1 zoom-14 位置の優先順位付きダウンロード処理。

    優先順位: dem5a（5m航空）→ dem5b（5m写真）→ dem_png（10m）

    実行時 get_elevation はピクセル単位で無効値 (128,0,0) を下位レイヤーへ
    フォールバックする。これと整合させるため、上位レイヤーのタイルが取得でき
    ても内部に欠損ピクセルが残る限り下位レイヤーを取得する（欠損が解消した分
    だけ降りるので DL は最小）。dem_png（最下層）まで降りればそれ以上の手段は
    無く、残った欠損は恒久的な無効値（海など）として確定する。

    force=False かつ dem_png が**読める形で**キャッシュ済みなら位置全体をスキップ
    する。dem_png の存在は「最下層まで降下済み＝解決済み」の終端マーカーであり、
    欠損のある位置は必ず dem_png までキャッシュされるため、この早期リターンが
    再プリフェッチ時の「解決済みは無視」を成立させる。⚠️ **見るのは存在ではなく
    可読性**（B-141）＝壊れたタイルを終端マーカーと読むと、そこだけ永久に埋まらない。
    """
    dem14_ok = _is_cached(dem14_path)
    if not force and dem14_ok:
        with lock:
            counts["skipped"] += 1
        return

    # 🔴 **壊れて残っている dem_png は、5m が読めても必ず取り直す**（B-142）＝
    #    上の早期 return を通り抜けた理由は 2 通りある（**不在** か **壊れている**）。
    #    下の降下は *不在* のほうだけを想定しており、5a/5b が読めれば `continue` して
    #    `need_dem` が立たない ⇒ **壊れた 10m タイルが取り直されないまま残る**
    #    （5m に欠損があって 10m まで降りた位置＝ごく普通のキャッシュ状態で起きる）。
    # ⚠️ **`force` かどうかで条件を分けない**（B-144＝B-142 の直しが `force` を
    #    素通りさせた）＝`force` は*強制再取得*なのだから、壊れたものが残るのは
    #    その名前に反する。**見るのは「在るのに読めない」だけ**で、`force` は
    #    「読めても取り直す」を足すだけの独立した軸。
    need_dem = os.path.exists(dem14_path) and not dem14_ok
    for x15, y15, subdir5a, path5a, subdir5b, path5b in zoom15_tiles:
        # dem_png 不在でここに到達した位置は、不変条件「欠損あり⟹dem_png取得」
        # より、キャッシュ済み 5a/5b は欠損なしと判断できる。再読込せず安全に
        # スキップしてよい（DL build 前の旧キャッシュは force 再取得で healing）。
        if not force and (_is_cached(path5a) or _is_cached(path5b)):
            continue

        arr5a = dem._fetch_tile("dem5a_png", 15, x15, y15, subdir5a, path5a)
        if arr5a is not None:
            with lock:
                counts["downloaded_5a"] += 1
            remaining = _void_mask(arr5a)
            if not remaining.any():
                continue   # 欠損なし: この位置は 5a で完結
        else:
            remaining = None   # 5a 自体が取得不可: 全画素を未解決として扱う

        # 5a に欠損が残る（または 5a 不在）→ 5b で埋まる分を解消
        arr5b = dem._fetch_tile("dem5b_png", 15, x15, y15, subdir5b, path5b)
        if arr5b is not None:
            with lock:
                counts["downloaded_5b"] += 1
            void5b = _void_mask(arr5b)
            still_void = void5b if remaining is None else (remaining & void5b)
            if not still_void.any():
                continue   # 5a の欠損を 5b が完全に補完
        # 5b 不在、または 5a∩5b に欠損が残る → dem_png へ降りる
        need_dem = True

    if need_dem:
        arr = dem._fetch_tile("dem_png", 14, x14, y14, dem14_subdir, dem14_path)
        with lock:
            if arr is not None:
                counts["downloaded_dem"] += 1
            else:
                counts["failed"] += 1


def count_bbox_tiles(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> int:
    """bbox 内の zoom-14 位置数を返す（プログレスバーの maximum 設定等に使う）。"""
    lat_n = max(lat1, lat2)
    lat_s = min(lat1, lat2)
    lon_w = min(lon1, lon2)
    lon_e = max(lon1, lon2)
    x14_nw, y14_nw, _, _ = dem._tile_coords(lat_n, lon_w, 14)
    x14_se, y14_se, _, _ = dem._tile_coords(lat_s, lon_e, 14)
    return (x14_se - x14_nw + 1) * (y14_se - y14_nw + 1)


def prefetch_tiles(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    progress_cb=None,   # callback(done: int, total: int) | None
    force: bool = False,
) -> dict:
    """bbox 内の DEM タイルを優先順位付きでダウンロードしてキャッシュに保存する。

    優先順位: dem5a（5m航空）→ dem5b（5m写真）→ dem_png（10m）
    force=False のとき、既にキャッシュ済みの位置はスキップする。

    Returns:
        {"area_total": int, "downloaded_5a": int, "downloaded_5b": int,
         "downloaded_dem": int, "skipped": int, "failed": int}
    """
    positions = list(_iter_dem_positions(lat1, lon1, lat2, lon2))
    total = len(positions)
    if total == 0:
        return {
            "area_total": 0, "downloaded_5a": 0, "downloaded_5b": 0,
            "downloaded_dem": 0, "skipped": 0, "failed": 0,
        }

    counts = {
        "done": 0, "downloaded_5a": 0, "downloaded_5b": 0,
        "downloaded_dem": 0, "skipped": 0, "failed": 0,
    }
    lock   = threading.Lock()
    work_q: queue.Queue = queue.Queue()
    for pos in positions:
        work_q.put(pos)

    def _worker() -> None:
        while True:
            try:
                x14, y14, dem14_subdir, dem14_path, zoom15_tiles = work_q.get_nowait()
            except queue.Empty:
                return
            try:
                _process_position(
                    x14, y14, dem14_subdir, dem14_path, zoom15_tiles,
                    force, counts, lock,
                )
            except Exception as e:
                logger.warning("prefetch worker error: %s", e)
                with lock:
                    counts["failed"] += 1
            finally:
                with lock:
                    counts["done"] += 1
                    done_snap = counts["done"]
                if progress_cb:
                    progress_cb(done_snap, total)
                work_q.task_done()

    num_workers = min(dem._MAX_PREFETCH_WORKERS, total)
    threads = [threading.Thread(target=_worker, daemon=True) for _ in range(num_workers)]
    for th in threads:
        th.start()
    work_q.join()

    logger.info(
        "prefetch complete: total=%d 5a=%d 5b=%d dem=%d skipped=%d failed=%d",
        total, counts["downloaded_5a"], counts["downloaded_5b"],
        counts["downloaded_dem"], counts["skipped"], counts["failed"],
    )
    return {
        "area_total":     total,
        "downloaded_5a":  counts["downloaded_5a"],
        "downloaded_5b":  counts["downloaded_5b"],
        "downloaded_dem": counts["downloaded_dem"],
        "skipped":        counts["skipped"],
        "failed":         counts["failed"],
    }
