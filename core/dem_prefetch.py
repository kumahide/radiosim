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
from core import dem_cache
from core import dem_sources
from core.config import logger


def _void_mask(arr: np.ndarray) -> np.ndarray:
    """計算が下のレイヤへ降りる画素の真偽マスクを返す（プリフェッチの降下判定に使う）。

    arr は (H, W, 3) の RGB 配列。`dem.get_elevation` は**復号して 0.0 になる画素**で
    次のレイヤへ進む＝無効値 (128, 0, 0)（海・データ欠損）と、ちょうど 0 m の
    (0, 0, 0) の 2 つ（国土地理院の式で 0.0 になる RGB はこの 2 つだけ）。
    🔴 **(0, 0, 0) を落とさない**（B-304）＝以前は (128, 0, 0) だけを見ていたので、
    5a に 0 m の画素がある位置を「5a で完結」と読み、強制再取得で**計算がまだ読む
    5b・dem_png を消していた**（通常の事前取得でも 5b を取らずオフラインで欠けた）。
    """
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    return ((r == 128) | (r == 0)) & (g == 0) & (b == 0)


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


def _drop_unread_tile(layer_id: str, x: int, y: int, path: str) -> None:
    """降下が要らなくなった下位レイヤのタイルを消す（B-285）。

    計算は上位レイヤで止まるので**標高の値は動かない**＝メモリの写しも外すが、
    世代（`dem._cache_epoch`）は進めない。消せなくても致命ではない（読まれない
    ファイルが残るだけ）ので、記録に留めて続ける。
    """
    if not os.path.exists(path):
        return
    try:
        os.remove(path)
    except OSError as e:
        logger.warning("prefetch: could not remove an unread lower-layer tile: %s", e)
        return
    with dem._cache_lock:
        dem._tile_cache.pop((dem_sources.GSI_DEM.source_id, layer_id, x, y), None)
        dem._tile_validity_memo.pop(path, None)
    logger.debug("prefetch: removed unread lower-layer tile: %s", path)


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

    実行時 get_elevation はピクセル単位で、復号して 0.0 になる画素（無効値
    (128,0,0) とちょうど 0 m の (0,0,0)＝`_void_mask`）を下位レイヤーへ
    フォールバックする。これと整合させるため、上位レイヤーのタイルが取得でき
    ても内部に欠損ピクセルが残る限り下位レイヤーを取得する（欠損が解消した分
    だけ降りるので DL は最小）。dem_png（最下層）まで降りればそれ以上の手段は
    無く、残った欠損は恒久的な無効値（海など）として確定する。

    force=False かつ dem_png が**読める形で**キャッシュ済みなら位置全体をスキップ
    する。dem_png の存在は「最下層まで降下済み＝解決済み」の終端マーカーであり、
    欠損のある位置は必ず dem_png までキャッシュされるため、この早期リターンが
    再プリフェッチ時の「解決済みは無視」を成立させる。⚠️ **見るのは存在ではなく
    可読性**（B-141）＝壊れたタイルを終端マーカーと読むと、そこだけ永久に埋まらない。

    降下が要らないと分かった下位レイヤは消す（B-285）＝強制再取得で上位の欠損が
    無くなった位置に、読まれない古い 5b・dem_png を残さない。⚠️ **dem_png を消すのは
    4 枚の zoom-15 を全部この回で取って、どれも降りなかったときだけ**＝範囲の端で
    一部しか見ていない位置は、範囲外の 1 枚が 10m を要るかもしれない。そこで消すと
    上の不変条件「欠損あり⟹dem_png 取得」が崩れ、次の通常の事前取得は読める 5a を
    見て `continue` するので**10m を二度と取りに行かない**（オフラインで欠損が残る）。
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
    resolved_above = 0   # この回に取って、dem_png まで降りずに済んだ zoom-15 の枚数
    for x15, y15, subdir5a, path5a, subdir5b, path5b in zoom15_tiles:
        # dem_png 不在でここに到達した位置は、不変条件「欠損あり⟹dem_png取得」
        # より、キャッシュ済み 5a/5b は欠損なしと判断できる。再読込せず安全に
        # スキップしてよい（DL build 前の旧キャッシュは force 再取得で healing）。
        if not force and (_is_cached(path5a) or _is_cached(path5b)):
            continue

        arr5a = dem._fetch_tile("dem5a_png", 15, x15, y15, subdir5a, path5a,
                                force=force)
        if arr5a is not None:
            with lock:
                counts["downloaded_5a"] += 1
            remaining = _void_mask(arr5a)
            if not remaining.any():
                # 欠損なし: この位置は 5a で完結＝5b は読まれない（B-285）
                _drop_unread_tile("dem5b_png", x15, y15, path5b)
                resolved_above += 1
                continue
        else:
            remaining = None   # 5a 自体が取得不可: 全画素を未解決として扱う

        # 5a に欠損が残る（または 5a 不在）→ 5b で埋まる分を解消
        arr5b = dem._fetch_tile("dem5b_png", 15, x15, y15, subdir5b, path5b,
                                force=force)
        if arr5b is not None:
            with lock:
                counts["downloaded_5b"] += 1
            void5b = _void_mask(arr5b)
            still_void = void5b if remaining is None else (remaining & void5b)
            if not still_void.any():
                resolved_above += 1
                continue   # 5a の欠損を 5b が完全に補完
        # 5b 不在、または 5a∩5b に欠損が残る → dem_png へ降りる
        need_dem = True

    if not need_dem and resolved_above == 4:
        # 4 枚とも上位で埋まった＝dem_png は読まれない（B-285・端の位置は docstring）
        _drop_unread_tile("dem_png", x14, y14, dem14_path)

    if need_dem:
        arr = dem._fetch_tile("dem_png", 14, x14, y14, dem14_subdir, dem14_path,
                              force=force)
        with lock:
            if arr is not None:
                counts["downloaded_dem"] += 1
            else:
                counts["failed"] += 1


def count_bbox_tiles(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    source: "dem_sources.DemSourceSpec | None" = None,
) -> int:
    """bbox 内の位置数を返す（プログレスバーの maximum・DL 確認ダイアログの件数表示に使う）。

    3.6 ステージ1（I-147 残り(b)・B-253）＝`source` で単位セルの大きさを合わせる。
    位置の単位は `dem_cache._base_zoom(source)`（`dem_cache.count_cached_areas` と
    同じ基準セル）＝国土地理院は従来どおり zoom-14 のまま（省略時・引数無しの
    既存呼び出しは 1 桁も動かない）。**キャッシュ済みとの差分（DL 確認ダイアログの
    新規分）を取るとき、両辺が同じ単位でないと数が合わない**（従来はここが
    暗黙に国土地理院固定だった＝B-253 の一面）。
    """
    lat_n = max(lat1, lat2)
    lat_s = min(lat1, lat2)
    lon_w = min(lon1, lon2)
    lon_e = max(lon1, lon2)
    base_zoom = dem_cache._base_zoom(source)
    x0, y0, _, _ = dem._tile_coords(lat_n, lon_w, base_zoom)
    x1, y1, _, _ = dem._tile_coords(lat_s, lon_e, base_zoom)
    return (x1 - x0 + 1) * (y1 - y0 + 1)


def prefetch_tiles(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    progress_cb=None,   # callback(done: int, total: int) | None
    force: bool = False,
    source: "dem_sources.DemSourceSpec | None" = None,
) -> dict:
    """bbox 内の DEM タイルをダウンロードしてキャッシュに保存する（B-253・I-147 残り(b)）。

    `source` を省略する（または国土地理院を指定する）と従来どおりの優先順位付き
    降下（`_prefetch_gsi`）。**それ以外のソースを指定したときも、実際にそのソースの
    タイルを取りに行くようになった**（従来はどのソースを選んでいても国土地理院
    決め打ちで取っていた＝B-253）。外部ソースは降下ロジックも欠損マスクも
    持たない前提（`dem_sources` の宣言に優先順位はあっても、国土地理院のような
    ピクセル単位の欠損フォールバックの意味論を宣言する項目が無い）ので、
    宣言した各レイヤのタイルをそのまま取得する `_prefetch_generic` を使う。

    Returns:
        国土地理院: {"area_total", "downloaded_5a", "downloaded_5b",
                     "downloaded_dem", "skipped", "failed"}（従来どおり）
        それ以外  : {"area_total", "downloaded", "skipped", "failed"}
    """
    src = source if source is not None else dem_sources.GSI_DEM
    if src.source_id == dem_sources.GSI_DEM.source_id:
        return _prefetch_gsi(lat1, lon1, lat2, lon2, progress_cb, force)
    return _prefetch_generic(src, lat1, lon1, lat2, lon2, progress_cb, force)


def _prefetch_generic(
    src: "dem_sources.DemSourceSpec",
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    progress_cb,
    force: bool,
) -> dict:
    """国土地理院以外のソース向けの単純な取得経路（3.6 ステージ1）。

    宣言した各レイヤの bbox 内タイルを（`dem_cache._enumerate_bbox` と同じ列挙で）
    そのまま取りに行く＝GSI の優先順位降下・欠損マスクは持たない。**単層宣言**
    （マニュアルの記入例＝Terrarium 等）ならレイヤは 1 つだけなので、これは
    そのまま「1 タイル＝1 位置」の素直な取得になる。複数レイヤを宣言した場合は
    レイヤごとに独立して全タイルを取りに行く（層間のスキップはしない）。
    """
    tasks = dem_cache._enumerate_bbox(lat1, lon1, lat2, lon2, src)
    total = len(tasks)
    if total == 0:
        return {"area_total": 0, "downloaded": 0, "skipped": 0, "failed": 0}

    counts = {"done": 0, "downloaded": 0, "skipped": 0, "failed": 0}
    lock = threading.Lock()
    work_q: queue.Queue = queue.Queue()
    for task in tasks:
        work_q.put(task)

    def _worker() -> None:
        while True:
            try:
                layer_id, zoom, x, y, subdir, cache_path = work_q.get_nowait()
            except queue.Empty:
                return
            try:
                if not force and _is_cached(cache_path):
                    with lock:
                        counts["skipped"] += 1
                else:
                    arr = dem._fetch_tile(layer_id, zoom, x, y, subdir, cache_path,
                                          source=src, force=force)
                    with lock:
                        if arr is not None:
                            counts["downloaded"] += 1
                        else:
                            counts["failed"] += 1
            except Exception as e:
                logger.warning("prefetch worker error (generic): %s", e)
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
        "prefetch complete (generic source=%s): total=%d downloaded=%d skipped=%d failed=%d",
        src.source_id, total, counts["downloaded"], counts["skipped"], counts["failed"],
    )
    return {
        "area_total": total,
        "downloaded": counts["downloaded"],
        "skipped":    counts["skipped"],
        "failed":     counts["failed"],
    }


def _prefetch_gsi(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    progress_cb=None,   # callback(done: int, total: int) | None
    force: bool = False,
) -> dict:
    """国土地理院専用＝優先順位付き降下（dem5a→dem5b→dem_png）と欠損マスク。

    ⚠️ **本体は 3.5 以前と1文字も変えていない**（3.6 ステージ1で `prefetch_tiles`
    から切り出しただけ）＝降下ロジックそのものは国土地理院専用のまま残す判断
    （B-253 対応案②）。

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
