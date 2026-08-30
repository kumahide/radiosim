"""
dem.py
======
国土地理院 DEM/淡色地図タイルの取得・ディスクキャッシュ・座標変換を担う。

  - HTTP セッション / プロキシ管理
  - GSI DEM PNG タイル取得（優先順位降下）と標高デコード
  - タイルの事前取得（プリフェッチ）・淡色地図タイル取得
  - タイル座標変換・キャッシュ走査 / カバレッジ輪郭 / キャッシュ削除

⚠️ **「1px が何 m か／何点で刻むか」はここに無い**＝純粋な層
（[core/terrain_grid.py](terrain_grid.py)）へ独立させた（I-069）。

ネットワーク（requests）・PIL・numpy への依存はここに閉じ込める。ロギングだけは
アプリ共通の logger（config.py）を借りる（config → dem の一方向依存・逆流なし）。
infrastructure.py を config.py ＋ dem.py（本体）へ分割した際に切り出した DEM 層。
"""

import io
import math
import os
import queue
import threading
import time
import urllib.request

import numpy as np
import requests
from PIL import Image

from core import terrain_grid
from core import version
from core.config import app_path, logger

# ============================================================
# DEM タイルキャッシュのルートディレクトリ
#   基準は config.app_path（＝exe／スクリプトの位置）で cwd に依存しない（B-014）。
#   テストは dem.CACHE_DIR を monkeypatch して一時ディレクトリへ差し替える。
# ============================================================
CACHE_DIR = app_path("terrain_cache")

# ============================================================
# DEM タイルクライアント
# ============================================================

# 利用する DEM レイヤーの優先順位リスト（高精度順）
# (layer_id, zoom)
#   layer_id は国土地理院タイルの正式 ID（末尾 _png が必須）
#   参照: https://maps.gsi.go.jp/development/ichiran.html
#
#   dem5a_png : 5m メッシュ（航空レーザ測量）  zoom=15  最優先
#   dem5b_png : 5m メッシュ（写真測量）        zoom=15  dem5a_png より広域
#   dem_png   : 10m メッシュ（基盤地図情報）   zoom=14  全国カバー
#
# ※ dem1a_png（1m）はカバレッジが限定的で取得失敗が頻発するため除外
DEM_LAYERS: list[tuple[str, int]] = [
    ("dem5a_png", 15),   # 5m  最優先（航空レーザ測量）
    ("dem5b_png", 15),   # 5m  次点（写真測量、dem5a_png より広域）
    ("dem_png",   14),   # 10m 全国カバー（最終フォールバック）
]
# ⚠️ **この層構成から導かれる「1px が何 m か」と「何点で刻むか」は
# [core/terrain_grid.py](terrain_grid.py)** ＝純粋な層として独立させてある
# （設定層がネットワーク依存なしに引けるようにするため・I-069）。

_MAX_PREFETCH_WORKERS: int = 8

# ディスクキャッシュのタイルを読むときの粘り（B-123）。
# `os.replace` が走っている一瞬だけ Windows は置換先を開かせないので、
# そこで諦めるとその点が 0.0 に化ける。置換は数ミリ秒で終わる。
_TILE_READ_ATTEMPTS: int = 3
_TILE_READ_RETRY_S: float = 0.01

# 淡色地図（レポート添付の経路オーバーレイ地図 = report_map.py が使用）。
# DEM レイヤーと違いズームが可変なので、キャッシュパスにズームを含めて
# 異なるズームの同一 (x, y) が衝突しないようにする（DEM は層ごとズーム固定）。
BASEMAP_LAYER:  str = "pale"
BASEMAP_SUBDIR: str = "basemap_pale"

# ============================================================
# HTTP セッション管理
# ============================================================
_proxy_url: str = ""
_http_session: "requests.Session | None" = None
_session_lock = threading.Lock()


def set_proxy(url: str) -> None:
    """プロキシURLを設定してセッションをリセットする。空文字はOSのプロキシ設定を使う。"""
    global _proxy_url, _http_session
    _proxy_url = url.strip()
    with _session_lock:
        if _http_session is not None:
            _http_session.close()
        _http_session = None
    with _cache_lock:
        _failed_tiles.clear()
    logger.info("Proxy configured: %r", _proxy_url or "(system)")


def _get_session() -> "requests.Session":
    global _http_session
    with _session_lock:
        if _http_session is None:
            s = requests.Session()
            s.headers.update({"User-Agent": version.USER_AGENT})
            if _proxy_url:
                s.proxies = {"http": _proxy_url, "https": _proxy_url}
            else:
                s.proxies = urllib.request.getproxies() or {}
            _http_session = s
        return _http_session


# キャッシュキーは (layer_id, xtile, ytile) の 3 要素
# _cache_lock は _tile_cache と _failed_tiles の両方を保護する。
# ロック保持中にネットワーク取得を行ってはいけない（並列化が無効になる）。
# ガード: tests/test_dem.py::TestGetElevation
#         ::test_network_fetch_runs_without_holding_the_cache_lock
_tile_cache: dict[tuple, np.ndarray] = {}
_cache_lock = threading.Lock()

# 恒久的に存在しないタイル（HTTP 404）のセット。再リクエスト防止のための
# 負キャッシュ。_cache_lock で保護する。
#   ★ここに入れてよいのは「取得しても永久に無い」タイルだけ（日本域外・海上で
#     GSI が 404 を返すもの）。タイムアウト・接続エラー・5xx/429 のような
#     "一時失敗" を入れてはならない（回復後もそのタイルを無視し続け、標高が
#     0.0 や粗レイヤ値に化けたまま黙って誤るため = B-010）。登録は _fetch_tile が
#     HTTP ステータスを見て 404 のときだけ行う（唯一ステータスを知る場所）。
# ガード: tests/test_dem.py::TestFailedTileNegativeCache
_failed_tiles: set[tuple] = set()


# ------------------------------------------------------------
# 「取れなかった」を戻り値を変えずに知らせる口（B-025 ②）
# ------------------------------------------------------------
# `get_elevation` は失敗しても 0.0 を返す（契約の是正は 3.x）。呼び出し側が
# 「取れなかった」ことに気づけないと、Proxy 未設定の環境で**平坦な地形を正しい顔で
# 配り続ける**。そこで戻り値には触らず、**直前の呼び出しが通信の失敗で終わったか**
# だけをスレッドローカルに置く。
#
# なぜスレッドローカルか：標高取得は 1 点 1 スレッドで並列に走る（simulation の
# ワーカー）ので、モジュール変数だと隣の点の結果を読む。呼ぶ側は自分のスレッドで
# `get_elevation` → `network_failed()` と続けて読むだけでよい。
#
# ⚠️ **記録するのは通信の失敗だけ**（タイムアウト・接続エラー・5xx/429）。
# **404 は含めない**＝あれは「そこに標高データが永久に無い」＝海上・日本域外で
# 正常に起きることで、通信は成功している。ここを混ぜると**海の上を通る経路が
# 「ネットワーク異常」として打ち切られる**（B-010 で負キャッシュに一時失敗を
# 混ぜたのと鏡像の誤り）。
_network_trouble = threading.local()


def network_failed() -> bool:
    """**このスレッドの直前の `get_elevation`** が通信の失敗で終わったか。"""
    return bool(getattr(_network_trouble, "flag", False))


# 🔑 **式の本体は `terrain_grid`**（B-150 で移した）＝*どの画素を読むか*は「格子の
# 事実」の側で、標本の置き方（`path_sample_fractions`）が同じ式を要る。ここは
# **別名**＝写しではないので、片方だけ動くことがない。タイル選択（`_tile_coords`）と
# レポート地図（`report_map.py`）の経路端点ピクセル投影は従来どおりこの名前を引く。
lonlat_to_pixel = terrain_grid.lonlat_to_pixel


def _tile_coords(lat: float, lon: float, zoom: int) -> tuple[int, int, int, int]:
    """緯度・経度・ズームレベルからタイル座標とタイル内ピクセル座標を返す。"""
    n       = 2.0 ** zoom
    wx, wy  = lonlat_to_pixel(lat, lon, zoom)
    xtile_f = wx / 256.0
    ytile_f = wy / 256.0
    xtile = min(int(xtile_f), int(n) - 1)
    ytile = min(int(ytile_f), int(n) - 1)
    px    = min(255, max(0, int((xtile_f - xtile) * 256)))
    py    = min(255, max(0, int((ytile_f - ytile) * 256)))
    return xtile, ytile, px, py


def get_elevation(lat: float, lon: float) -> float:
    """
    国土地理院 DEM PNG から標高 [m] を取得する。

    DEM_LAYERS の順（5m → 5m → 10m）に試み、
    タイル取得成功かつデコード値が有効（!= 0.0）なら返す。
    すべて失敗・無効値の場合は 0.0 を返す。

    ⚠️ **この 0.0 は「海抜 0m」と「取れなかった」を区別しない**（戻り値契約の
    是正は呼び出し側 ~30 箇所と出力契約に触るので 3.x＝ISSUES.md B-025 ①）。
    そのままだと Proxy 未設定などで取得が全滅したときに**平坦な地形が正常値の顔で
    出てくる**ので、**取れなかったことだけは別口で知らせる**＝直後に
    `network_failed()` を読むと、この呼び出しが**通信の失敗**で終わったかが分かる
    （B-025 ②）。戻り値には触っていないので既存の呼び出し側・テストのフェイクは
    そのまま動く（フェイクは「成功」として扱われる＝安全側）。
    """
    _network_trouble.flag = False
    try:
        for layer_id, zoom in DEM_LAYERS:
            xtile, ytile, px, py = _tile_coords(lat, lon, zoom)
            tile_key     = (layer_id, xtile, ytile)
            cache_subdir = os.path.join(CACHE_DIR, layer_id, str(xtile))
            cache_path   = os.path.join(cache_subdir, f"{ytile}.png")

            # ── キャッシュ確認（ロック保持は辞書参照のみ）────────────
            with _cache_lock:
                if tile_key in _failed_tiles:
                    continue
                cached = _tile_cache.get(tile_key)

            if cached is not None:
                elev = _decode_elevation(cached[py, px])
                if elev != 0.0:
                    _network_trouble.flag = False
                    return elev
                logger.debug(
                    "DEM layer '%s' returned invalid pixel at (%.6f,%.6f), trying next",
                    layer_id, lat, lon,
                )
                continue

            # ── キャッシュミス：ロックを解放してネットワーク取得 ─────
            arr = _fetch_tile(layer_id, zoom, xtile, ytile, cache_subdir, cache_path)

            # ── 取得結果を書き込み ────────────────────────────────────
            #   arr is None のときの負キャッシュ登録は _fetch_tile 側で行う
            #   （404 = 恒久欠落のときだけ。一時失敗は登録しない = B-010）。
            with _cache_lock:
                if arr is None:
                    logger.debug(
                        "DEM layer '%s' unavailable at tile(%d,%d), trying next",
                        layer_id, xtile, ytile,
                    )
                    continue
                _tile_cache.setdefault(tile_key, arr)  # 競合時は先着優先

            elev = _decode_elevation(arr[py, px])
            if elev != 0.0:
                # 別レイヤで通信に失敗していても、値が取れたなら失敗ではない。
                _network_trouble.flag = False
                return elev
            logger.debug(
                "DEM layer '%s' returned invalid pixel at (%.6f,%.6f), trying next",
                layer_id, lat, lon,
            )

        logger.warning(
            "All DEM layers exhausted for lat=%.6f lon=%.6f, returning 0.0",
            lat, lon,
        )
        return 0.0

    except Exception as e:
        logger.error(
            "Elevation decode error: lat=%.6f lon=%.6f error=%s", lat, lon, e
        )
        return 0.0


def _read_cached_tile(cache_path: str) -> "np.ndarray | None":
    """ディスクキャッシュのタイルを読む。**読めなければ None＝キャッシュミス扱い**。

    キャッシュは常に捨てて取り直せるものなので、**読み取りの失敗を致命にしない**。
    ここで例外を上へ投げると `get_elevation` の except に握られて **0.0**（＝海抜
    0m と区別されない）になる＝B-123 で塞いだ穴を読み側に開け直すことになる。

    ⚠️ **書き込みを原子的にしても、Windows には読めない一瞬が残る**（2026-08-24 に
    冷えたキャッシュ 61 タイルの実測で `[Errno 13] Permission denied` が 1 点＝
    その点が 0.0 になった）: `os.replace` が走っている最中、置換先を開こうとした
    スレッドは共有違反で弾かれる。**内容は壊れていない**ので、数ミリ秒おいて
    読み直せばまず取れる。⇒ 短く粘ってから諦める（諦めても呼び出し側は
    ネットワーク取得へ落ちるだけで、値は正しく埋まる）。
    """
    for attempt in range(_TILE_READ_ATTEMPTS):
        try:
            return np.array(Image.open(cache_path).convert("RGB"))
        except (OSError, ValueError) as e:
            # PIL の UnidentifiedImageError は OSError 派生。
            if attempt == _TILE_READ_ATTEMPTS - 1:
                logger.debug(
                    "cached tile unreadable (treated as cache miss): path=%s error=%s",
                    cache_path, e,
                )
                return None
            time.sleep(_TILE_READ_RETRY_S)
    return None


def _write_tile_atomic(cache_path: str, img_data: bytes, *,
                       replace_broken: bool = False) -> None:
    """タイル画像を**原子的に**ディスクキャッシュへ書く（B-123）。

    同一ディレクトリの一時ファイルへ書いてから `os.replace` する。⇒ 他のスレッド
    から見える `cache_path` は**常に「無いか、完全か」のどちらか**になる。

    なぜ必要か（2026-08-24 に実測）: 隣り合う標高サンプルは同じタイルを共有する
    ので、並列取得では**同じタイルを別スレッドが同時に要求する**。素の
    `open(path, "wb")` だと、書き込み途中（0 バイト〜途中まで）のファイルを
    `_fetch_tile` 冒頭の `Image.open` が掴み、復号に失敗する。失敗した点は
    `get_elevation` の except に握られて **0.0**（＝海抜 0m と区別されない）になり、
    落ちも止まりもせず**結果が黙って楽観側へ振れる**（26 回線で 4 点）。

    ⛔ ロックでは直さない（タイル取得は並列であることに意味がある）。⚠️ 一時ファイル
    名はプロセス／スレッドで衝突してはならない（`.tmp` 固定だと同じ競合を別の場所に
    作るだけ）。⚠️ 書けなかったこと自体は**致命ではない**（次回また取りに行くだけ）
    ので、失敗は握って記録に留める＝ここで例外を上げると `get_elevation` の except に
    落ちて**通信は成功しているのに 0.0 になる**（直す当の欠陥を別経路で作る）。

    ⚠️ **Windows は「読まれている最中のファイル」への置換を拒む**（WinError 5・
    2026-08-24 に冷えたキャッシュ 61 タイルの実測）。別スレッドが先に書き終えた同じ
    タイルを `_fetch_tile` 冒頭が開いている最中に起きる＝**内容は同じ**なので失う
    情報は無い。⇒ ①既に在るなら**書きに行かない** ②それでも競合したら **debug** へ
    （正常運用で warning を鳴らさない）。本当に書けない側〔ディスクフル・権限〕は
    `cache_path` が不在のまま残るので warning で区別できる。⚠️ **この①は「読めない」
    相手には成り立たない**（B-136＝壊れたキャッシュが永久に居座る）ので、**読み側が**
    読めないと確定した時だけ置換を許す（`replace_broken`）。
    """
    if os.path.exists(cache_path) and not replace_broken:
        # 同じ URL のタイル＝同じ内容。上書きしても得るものが無く、競合だけ増える。
        return

    tmp_path = f"{cache_path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(img_data)
        os.replace(tmp_path, cache_path)
    except OSError as e:
        # ⚠️ **握り潰してよいのは中身が読める時だけ**（B-136・置換の回は必ず在る）。
        if os.path.exists(cache_path) and _read_cached_tile(cache_path) is not None:
            logger.debug(
                "tile cache write skipped (already written by another thread): "
                "path=%s error=%s", cache_path, e,
            )
        else:
            logger.warning("tile cache write failed: path=%s error=%s", cache_path, e)
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _fetch_tile(
    layer_id: str,
    zoom: int,
    xtile: int,
    ytile: int,
    cache_subdir: str,
    cache_path: str,
) -> "np.ndarray | None":
    """タイル画像を取得して numpy 配列で返す。失敗時は None。

    失敗には2種類あり、負キャッシュ（_failed_tiles）の扱いが異なる:
      - 恒久欠落（HTTP 404）: このタイルは取得しても永久に無い（日本域外・
        海上）。_failed_tiles に登録し、以後の再リクエストを抑止する。
      - 一時失敗（タイムアウト・接続エラー・5xx/429 等）: 回復し得るので
        _failed_tiles には登録しない。登録すると回復後もそのタイルを無視し
        続け、標高が誤る（= B-010）。
    """
    url     = (
        f"https://cyberjapandata.gsi.go.jp/xyz/{layer_id}"
        f"/{zoom}/{xtile}/{ytile}.png"
    )
    # 読めなければ None＝ここでは return せず、そのまま取得へ落ちる（B-123）。
    # **読めなかったことは書き側へ持ち越す**＝壊れた相手だけ置換してよい（B-136）。
    cached = _read_cached_tile(cache_path) if os.path.exists(cache_path) else None
    if cached is not None:
        return cached
    cache_is_broken = os.path.exists(cache_path)

    try:
        logger.debug(
            "Fetching tile: layer=%s zoom=%d x=%d y=%d",
            layer_id, zoom, xtile, ytile,
        )
        res = _get_session().get(url, timeout=5)

        if res.status_code == 200:
            img_data = res.content
            arr = np.array(Image.open(io.BytesIO(img_data)).convert("RGB"))
            os.makedirs(cache_subdir, exist_ok=True)
            _write_tile_atomic(cache_path, img_data, replace_broken=cache_is_broken)
            return arr

        if res.status_code == 404:
            # 恒久欠落 = 負キャッシュに登録して再リクエストを抑止。
            with _cache_lock:
                _failed_tiles.add((layer_id, xtile, ytile))
            logger.debug(
                "tile absent (404) layer=%s tile=(%d,%d)",
                layer_id, xtile, ytile,
            )
            return None

        # 404 以外の非 200（5xx/429 等）は一時失敗として扱い、負キャッシュには
        # 登録しない（次回リトライで取得し直せるようにする）。
        logger.warning(
            "tile: unexpected status %d layer=%s tile=(%d,%d)",
            res.status_code, layer_id, xtile, ytile,
        )
        _network_trouble.flag = True
        return None

    except requests.RequestException as e:
        # 一時失敗。負キャッシュには登録しない。
        logger.warning(
            "tile download failed: layer=%s tile=(%d,%d) error=%s",
            layer_id, xtile, ytile, e,
        )
        if os.path.exists(cache_path):
            cached = _read_cached_tile(cache_path)
            if cached is not None:
                return cached
        _network_trouble.flag = True
        return None


def _enumerate_bbox(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> list[tuple]:
    """bbox 内の全タイル座標を (layer_id, zoom, x, y, subdir, cache_path) のリストで返す。

    Web Mercator では x が東向き増加、y が南向き増加。
    NW コーナー（最大緯度・最小経度）が最小の (x, y) になる。
    """
    lat_n = max(lat1, lat2)
    lat_s = min(lat1, lat2)
    lon_w = min(lon1, lon2)
    lon_e = max(lon1, lon2)
    tasks: list[tuple] = []
    for layer_id, zoom in DEM_LAYERS:
        x0, y0, _, _ = _tile_coords(lat_n, lon_w, zoom)  # NW: 最小 (x, y)
        x1, y1, _, _ = _tile_coords(lat_s, lon_e, zoom)  # SE: 最大 (x, y)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                subdir     = os.path.join(CACHE_DIR, layer_id, str(x))
                cache_path = os.path.join(subdir, f"{y}.png")
                tasks.append((layer_id, zoom, x, y, subdir, cache_path))
    return tasks


def _decode_elevation(rgb: np.ndarray) -> float:
    """RGB ピクセル値から標高 [m] をデコードする（国土地理院仕様）。"""
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    # 無効値ピクセル (128, 0, 0) = 海・データ欠損
    if r == 128 and g == 0 and b == 0:
        return 0.0
    x = r * 65536 + g * 256 + b
    if x < 8388608:
        return float(x * 0.01)
    return float((x - 16777216) * 0.01)


# ============================================================
# 淡色地図（basemap）タイル取得 — レポート添付の経路地図用
# ============================================================

def _basemap_tile_path(zoom: int, x: int, y: int) -> tuple[str, str]:
    """淡色地図タイルのキャッシュ (subdir, path) を返す（ズーム別ディレクトリ）。"""
    subdir = os.path.join(CACHE_DIR, BASEMAP_SUBDIR, str(zoom), str(x))
    return subdir, os.path.join(subdir, f"{y}.png")


def fetch_basemap_tiles(
    tiles: list[tuple[int, int]], zoom: int,
) -> dict[tuple[int, int], np.ndarray]:
    """淡色地図タイル群 (x, y) を **並列** 取得し {(x, y): RGB配列} を返す。

    レポート保存（メインスレッド）から呼ばれるため、逐次取得で GUI を固めない
    よう prefetch_tiles と同じワーカープール方式で並列化する。取得・キャッシュ
    の所在（layer/subdir/path）はこの層が所有する（呼び出し側は座標だけ渡す）。
    取得できなかったタイルは結果に含めない（呼び出し側が欠損として扱う）。
    """
    results: dict[tuple[int, int], np.ndarray] = {}
    if not tiles:
        return results
    lock   = threading.Lock()
    work_q: queue.Queue = queue.Queue()
    for t in tiles:
        work_q.put(t)

    def _worker() -> None:
        while True:
            try:
                x, y = work_q.get_nowait()
            except queue.Empty:
                return
            try:
                subdir, path = _basemap_tile_path(zoom, x, y)
                arr = _fetch_tile(BASEMAP_LAYER, zoom, x, y, subdir, path)
                if arr is not None:
                    with lock:
                        results[(x, y)] = arr
            except Exception as e:
                logger.warning("basemap tile worker error: %s", e)
            finally:
                work_q.task_done()

    num_workers = min(_MAX_PREFETCH_WORKERS, len(tiles))
    threads = [threading.Thread(target=_worker, daemon=True) for _ in range(num_workers)]
    for th in threads:
        th.start()
    work_q.join()
    return results


# ============================================================
# タイル座標変換（逆変換）
# ============================================================

def tile_to_latlng(x: int, y: int, zoom: int) -> tuple[float, float]:
    """タイル座標 (x, y, zoom) の NW コーナーの緯度経度を返す。"""
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lat_rad), lon


# ============================================================
# タイルキャッシュ管理
# ============================================================

# 精度レベルの優先順位（大きいほど高精度）: (layer_id, tile_zoom, level, priority)
_OVERLAY_LAYERS: list[tuple[str, int, str, int]] = [
    ("dem5a_png", 15, "5a",  3),
    ("dem5b_png", 15, "5b",  2),
    ("dem_png",   14, "dem", 1),
]
_PRIORITY_TO_LEVEL: dict[int, str] = {3: "5a", 2: "5b", 1: "dem"}


def _scan_cached_positions(
    lat_n: float, lat_s: float,
    lon_w: float, lon_e: float,
) -> dict[tuple[int, int], int]:
    """表示範囲内のキャッシュ済みタイルを zoom-14 セル単位で集約する。

    実在するキャッシュファイルだけを走査するため計算量はキャッシュ量に比例し、
    地理的範囲には比例しない。各レイヤーの x ディレクトリ一覧を起点に走査し、
    表示範囲外を間引く。

    Returns: {(x14, y14): 最高 priority}
    """
    base: dict[tuple[int, int], int] = {}
    for layer_id, tile_zoom, _level, priority in _OVERLAY_LAYERS:
        layer_dir = os.path.join(CACHE_DIR, layer_id)
        if not os.path.isdir(layer_dir):
            continue
        x_min, y_min, _, _ = _tile_coords(lat_n, lon_w, tile_zoom)
        x_max, y_max, _, _ = _tile_coords(lat_s, lon_e, tile_zoom)
        shift = tile_zoom - 14    # zoom-15(5a/5b)→1, zoom-14(dem)→0
        try:
            x_names = os.listdir(layer_dir)
        except OSError:
            continue
        for x_name in x_names:
            try:
                x = int(x_name)
            except ValueError:
                continue
            if x < x_min or x > x_max:
                continue
            x_dir = os.path.join(layer_dir, x_name)
            try:
                y_names = os.listdir(x_dir)
            except OSError:
                continue
            for fname in y_names:
                if not fname.endswith(".png"):
                    continue
                try:
                    y = int(fname[:-4])
                except ValueError:
                    continue
                if y < y_min or y > y_max:
                    continue
                key = (x >> shift, y >> shift)
                if base.get(key, 0) < priority:
                    base[key] = priority
    return base


def count_cached_areas(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> int:
    """bbox 内で実際にキャッシュ済みの zoom-14 エリア数を返す（削除対象の件数表示用）。

    count_bbox_tiles が範囲内の全エリア（未取得含む）を数えるのに対し、本関数は
    実在キャッシュのみを数える。
    """
    lat_n = max(lat1, lat2)
    lat_s = min(lat1, lat2)
    lon_w = min(lon1, lon2)
    lon_e = max(lon1, lon2)
    return len(_scan_cached_positions(lat_n, lat_s, lon_w, lon_e))


def scan_cache_overlay(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    overlay_zoom: int,
) -> list[dict]:
    """表示範囲内のキャッシュを「適応的粒度」のセルに集約して返す（自動表示用）。

    クアッドツリー方式: zoom-14 を最小単位とし、2×2 の子がすべて存在し
    かつ同一精度レベルのときだけ親セルに統合する。これを overlay_zoom まで
    繰り返す。完全に埋まった領域の内部は大きなセル（ポリゴン少）になり、
    部分的にしか埋まっていない領域（＝カバレッジのエッジ）は細かいセルの
    まま残るため、粗い表示でもカバレッジ範囲を過大に見せない。

    キャッシュ済みセルのみを返す（"none" は返さない）。

    Returns:
        [{"x": int, "y": int, "zoom": int, "level": str}, ...]
        zoom はセルごとに異なる（overlay_zoom 〜 14）。
        level は "5a" | "5b" | "dem"。
    """
    # dem_png は zoom-14 が上限のため overlay_zoom は 14 以下に丸める。
    overlay_zoom = max(2, min(14, overlay_zoom))
    lat_n = max(lat1, lat2)
    lat_s = min(lat1, lat2)
    lon_w = min(lon1, lon2)
    lon_e = max(lon1, lon2)

    current = _scan_cached_positions(lat_n, lat_s, lon_w, lon_e)   # zoom-14 base
    result: list[dict] = []

    # 14 → overlay_zoom へ向けてボトムアップに統合する。
    # 親に統合できない（=部分的な）セルはその時点の zoom で確定出力する。
    zoom = 14
    while zoom > overlay_zoom and current:
        groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for (x, y) in current:
            groups.setdefault((x >> 1, y >> 1), []).append((x, y))

        promoted: dict[tuple[int, int], int] = {}
        for parent, children in groups.items():
            levels = {current[c] for c in children}
            if len(children) == 4 and len(levels) == 1:
                # 4 子すべて存在・同一レベル → 親へ統合してさらに上を狙う
                promoted[parent] = next(iter(levels))
            else:
                # 部分的 or レベル混在 → このセル群は現 zoom で確定（エッジ）
                for c in children:
                    result.append(
                        {"x": c[0], "y": c[1], "zoom": zoom,
                         "level": _PRIORITY_TO_LEVEL[current[c]]}
                    )
        current = promoted
        zoom -= 1

    # 最後まで統合された（=完全に埋まった）セルを overlay_zoom で出力
    for (x, y), prio in current.items():
        result.append({"x": x, "y": y, "zoom": zoom, "level": _PRIORITY_TO_LEVEL[prio]})

    return result


def _simplify_grid_loop(pts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """格子座標の閉ループから一直線上の中間点を除く（角だけ残す）。"""
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    if n < 3:
        return pts
    out: list[tuple[int, int]] = []
    for i in range(n):
        prev = pts[(i - 1) % n]
        cur  = pts[i]
        nxt  = pts[(i + 1) % n]
        # 外積 0 = 3 点が一直線 → cur は角ではないので捨てる
        if (cur[0] - prev[0]) * (nxt[1] - cur[1]) - (cur[1] - prev[1]) * (nxt[0] - cur[0]) == 0:
            continue
        out.append(cur)
    return out


def coverage_outline(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> list[list[tuple[float, float]]]:
    """キャッシュ済み領域の和集合の外周（と穴の境界）を緯度経度ループで返す。

    zoom-14 単位セルの境界辺を「有向辺の相殺」で求める。隣接する 2 セルが
    共有する辺は逆向きの有向辺として打ち消し合い、残った辺が領域の外周
    （および内側の穴の境界）になる。これにより内部のグリッド線は出ず、
    外周線だけが得られる。

    Returns:
        [[(lat, lon), ...], ...]  各ループは閉路（始点と終点は重複しない）。
    """
    lat_n = max(lat1, lat2)
    lat_s = min(lat1, lat2)
    lon_w = min(lon1, lon2)
    lon_e = max(lon1, lon2)
    base = _scan_cached_positions(lat_n, lat_s, lon_w, lon_e)

    # 各セルの 4 辺を一定の回転方向で有向辺として登録し、逆向きがあれば相殺する。
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()

    def _toggle(a: tuple[int, int], b: tuple[int, int]) -> None:
        if (b, a) in edges:
            edges.discard((b, a))
        else:
            edges.add((a, b))

    for (x, y) in base:
        _toggle((x, y),         (x + 1, y))
        _toggle((x + 1, y),     (x + 1, y + 1))
        _toggle((x + 1, y + 1), (x, y + 1))
        _toggle((x, y + 1),     (x, y))

    # 残った有向辺を始点でインデックス化し、連結してループを作る。
    successors: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for a, b in edges:
        successors.setdefault(a, []).append(b)

    remaining = set(edges)
    loops: list[list[tuple[int, int]]] = []
    for start in list(edges):
        if start not in remaining:
            continue
        cur = start
        pts: list[tuple[int, int]] = [cur[0]]
        while cur in remaining:
            remaining.discard(cur)
            pts.append(cur[1])
            nxt = None
            for cand in successors.get(cur[1], ()):
                if (cur[1], cand) in remaining:
                    nxt = (cur[1], cand)
                    break
            if nxt is None:
                break
            cur = nxt
        simplified = _simplify_grid_loop(pts)
        if len(simplified) >= 3:
            loops.append(simplified)

    # 格子点 (col, row) はその zoom-14 タイルの NW 角に対応する。
    return [[tile_to_latlng(c, r, 14) for (c, r) in loop] for loop in loops]


def delete_tile_cache(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> dict:
    """bbox 内のキャッシュファイルを削除し、メモリキャッシュも消去する。

    Returns:
        {"deleted": int, "errors": int}
    """
    tiles = _enumerate_bbox(lat1, lon1, lat2, lon2)
    deleted = 0
    errors  = 0
    keys_to_clear: set[tuple] = set()
    for layer_id, _, x, y, _, cache_path in tiles:
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
                deleted += 1
                keys_to_clear.add((layer_id, x, y))
            except OSError as e:
                logger.warning("delete_tile_cache: %s", e)
                errors += 1
    with _cache_lock:
        for key in keys_to_clear:
            _tile_cache.pop(key, None)
            _failed_tiles.discard(key)

    # 淡色地図（basemap）タイルは範囲削除の対象にしない。範囲削除はマップ
    # ウィンドウで可視化される DEM カバレッジに対する操作であり、basemap は
    # そこに表示されない（背景地図は tkintermapview 自前タイル・カバレッジ塗りは
    # DEM のみ）。見えないものを範囲指定で黙って消すのを避け、件数表示
    # （count_cached_areas=DEM のみ）とも整合させる。basemap は「全キャッシュ
    # 削除」（delete_all_tile_cache）でのみ消える。
    logger.info("delete_tile_cache: deleted=%d errors=%d", deleted, errors)
    return {"deleted": deleted, "errors": errors}


def get_cache_stats() -> dict:
    """キャッシュディレクトリ全体の枚数と総バイト数を返す。

    Returns:
        {"count": int, "size_bytes": int}
    """
    count = 0
    size  = 0
    if os.path.exists(CACHE_DIR):
        for dirpath, _, filenames in os.walk(CACHE_DIR):
            for fname in filenames:
                if fname.endswith(".png"):
                    count += 1
                    try:
                        size += os.path.getsize(os.path.join(dirpath, fname))
                    except OSError:
                        pass
    return {"count": count, "size_bytes": size}


def delete_all_tile_cache() -> dict:
    """全キャッシュファイルを削除し、メモリキャッシュも消去する。

    Returns:
        {"deleted": int}
    """
    deleted = 0
    if os.path.exists(CACHE_DIR):
        for dirpath, _, filenames in os.walk(CACHE_DIR):
            for fname in filenames:
                if fname.endswith(".png"):
                    try:
                        os.remove(os.path.join(dirpath, fname))
                        deleted += 1
                    except OSError as e:
                        logger.warning("delete_all_tile_cache: %s", e)
    with _cache_lock:
        _tile_cache.clear()
        _failed_tiles.clear()
    logger.info("delete_all_tile_cache: deleted=%d", deleted)
    return {"deleted": deleted}
