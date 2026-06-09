"""
infrastructure.py
=================
外部依存をすべてここに閉じ込める。
  - ロギング設定
  - GSI DEM PNG タイル取得 / ディスクキャッシュ
  - アプリ設定ファイル (JSON) の読み書き
  - 入力バリデーションルール定義

他モジュールは requests / PIL / os.path をここ経由で使う。
"""

import io
import json
import logging
import math
import os
import queue
import threading
import urllib.request

import numpy as np
import requests
from PIL import Image

import i18n
import version

# ============================================================
# 定数
# ============================================================
CONFIG_FILE = "radiosim_conf.json"
CACHE_DIR   = "terrain_cache"
RESULTS_DIR = "results"
LOG_FILE    = "radiosim.log"

# ============================================================
# ロギング設定
#   DEBUG   : Fresnel・ν等の計算値（開発時）
#   INFO    : タイル取得・シミュレーション開始/完了
#   WARNING : タイル取得失敗（キャッシュ代替）
#   ERROR   : 致命的エラー（保存失敗・計算例外）
# ============================================================
def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("radiosim")

logger = setup_logging()

# ============================================================
# 入力バリデーションルール
#   {key: (min, max, error_message)}
# ============================================================
VALIDATION_RULES: dict[str, tuple] = {
    "freq"     : (1.0,    100000.0, "err_freq"),
    "p_tx"     : (-30.0,  60.0,     "err_p_tx"),
    "gain_tx"  : (0.0,    60.0,     "err_gain_tx"),
    "gain_rx"  : (0.0,    60.0,     "err_gain_rx"),
    "sens"     : (-130.0, -20.0,    "err_sens"),
    "h_tx"     : (0.0,    500.0,    "err_h_tx"),
    "h_rx"     : (0.0,    500.0,    "err_h_rx"),
    "veg_h"    : (0.0,    100.0,    "err_veg_h"),
    "k_factor" : (0.0,    30.0,     "err_k_factor"),
    "samples"  : (10,     2000,     "err_samples"),
    "rain_rate": (0.0,    200.0,    "err_rain_rate"),
}

DEFAULT_CONFIG: dict[str, str] = {
    "start"      : "34.5429, 132.4118",
    "end"        : "34.5389, 132.4050",
    "h_tx"       : "30.0",
    "h_rx"       : "10.0",
    "freq"       : "2400.0",
    "p_tx"       : "20.0",
    "gain_tx"    : "3.0",
    "gain_rx"    : "3.0",
    "sens"       : "-85.0",
    "veg_h"      : "10.0",
    "k_factor"   : "10.0",
    "samples"    : "200",
    "env_type"   : "los",
    "rain_rate"  : "0.0",
    "diff_method": "deygout",
    "theme"      : "system",
    "lang"       : "en",
    "proxy_url"  : "",
}


# ============================================================
# 設定ファイル
# ============================================================
def load_config(path: str = CONFIG_FILE) -> dict[str, str]:
    """保存済み設定を読み込む。失敗時はデフォルトを返す。

    ファイルに存在しないキーは DEFAULT_CONFIG の値で補完する。
    これにより古い settings.json（rain_rate 等が未定義）でもエラーにならない。
    """
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            # DEFAULT_CONFIG のキーのみ上書き（未知キーは無視、欠損キーはデフォルト維持）
            for key in DEFAULT_CONFIG:
                if key in loaded:
                    config[key] = loaded[key]
        except Exception as e:
            logger.warning("Config load error: %s", e)
    return config


def save_config(config: dict[str, str], path: str = CONFIG_FILE) -> None:
    """現在の設定を JSON で保存する。"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.warning("Config save error: %s", e)


# バリデーション用許容値セット（validate_config で参照）
_VALID_ENV_TYPES:    frozenset[str] = frozenset({"urban", "suburban", "rural", "los"})
_VALID_DIFF_METHODS: frozenset[str] = frozenset({"single", "deygout"})

# ============================================================
# 入力バリデーション
# ============================================================
def validate_config(c: dict[str, str]) -> list[str]:
    """
    入力値を検証し、エラーメッセージのリストを返す。
    空リストなら正常。
    """
    errors: list[str] = []

    for key, (vmin, vmax, msg_key) in VALIDATION_RULES.items():
        raw = c.get(key, "").strip()
        try:
            val = float(raw)
            if not (vmin <= val <= vmax):
                errors.append(f"[{key}] {i18n.t(msg_key)} (value: {val})")
        except ValueError:
            errors.append(f"[{key}] {i18n.t('err_numeric')} (value: '{raw}')")

    for coord_key, lbl_key in [("start", "err_label_start"), ("end", "err_label_end")]:
        label = i18n.t(lbl_key)
        raw = c.get(coord_key, "").strip()
        parts = raw.split(",")
        if len(parts) != 2:
            errors.append(f"[{coord_key}] {label} {i18n.t('err_coord_format')}")
            continue
        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
        except ValueError:
            errors.append(f"[{coord_key}] {label} {i18n.t('err_coord_invalid')}")
            continue
        if not (-85.05 <= lat <= 85.05):
            errors.append(f"[{coord_key}] {i18n.t('err_lat_range')} (value: {lat})")
        if not (-180.0 <= lon <= 180.0):
            errors.append(f"[{coord_key}] {i18n.t('err_lon_range')} (value: {lon})")

    # env_type のバリデーション（許容値リストとの照合）
    env_raw = c.get("env_type", "suburban").strip()
    if env_raw not in _VALID_ENV_TYPES:
        errors.append(
            f"[env_type] {i18n.t('err_env_type')}: {sorted(_VALID_ENV_TYPES)}"
            f" (value: '{env_raw}')"
        )

    # diff_method のバリデーション
    diff_raw = c.get("diff_method", "deygout").strip()
    if diff_raw not in _VALID_DIFF_METHODS:
        errors.append(
            f"[diff_method] {i18n.t('err_diff_method')}: {sorted(_VALID_DIFF_METHODS)}"
            f" (value: '{diff_raw}')"
        )

    # TX と RX が同一点でないかチェック
    if not errors:
        try:
            s_lat, s_lon = [float(x.strip()) for x in c["start"].split(",")]
            e_lat, e_lon = [float(x.strip()) for x in c["end"].split(",")]
            if abs(s_lat - e_lat) < 1e-7 and abs(s_lon - e_lon) < 1e-7:
                errors.append(i18n.t("err_coord_identical"))
        except Exception:
            pass

    return errors


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

_MAX_PREFETCH_WORKERS: int = 8

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
_tile_cache: dict[tuple, np.ndarray] = {}
_cache_lock = threading.Lock()

# 取得失敗タイルのセット（再リクエスト防止）。_cache_lock で保護する。
_failed_tiles: set[tuple] = set()


def _tile_coords(lat: float, lon: float, zoom: int) -> tuple[int, int, int, int]:
    """緯度・経度・ズームレベルからタイル座標とピクセル座標を返す。"""
    n       = 2.0 ** zoom
    xtile_f = (lon + 180.0) / 360.0 * n
    ytile_f = (
        1.0
        - math.log(
            math.tan(math.radians(lat))
            + 1 / math.cos(math.radians(lat))
        ) / math.pi
    ) / 2.0 * n
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
    """
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
                    return elev
                logger.debug(
                    "DEM layer '%s' returned invalid pixel at (%.6f,%.6f), trying next",
                    layer_id, lat, lon,
                )
                continue

            # ── キャッシュミス：ロックを解放してネットワーク取得 ─────
            arr = _fetch_tile(layer_id, zoom, xtile, ytile, cache_subdir, cache_path)

            # ── 取得結果を書き込み ────────────────────────────────────
            with _cache_lock:
                if arr is None:
                    _failed_tiles.add(tile_key)
                    logger.debug(
                        "DEM layer '%s' unavailable at tile(%d,%d), trying next",
                        layer_id, xtile, ytile,
                    )
                    continue
                _tile_cache.setdefault(tile_key, arr)  # 競合時は先着優先

            elev = _decode_elevation(arr[py, px])
            if elev != 0.0:
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


def _fetch_tile(
    layer_id: str,
    zoom: int,
    xtile: int,
    ytile: int,
    cache_subdir: str,
    cache_path: str,
) -> "np.ndarray | None":
    """タイル画像を取得して numpy 配列で返す。失敗時は None。"""
    url     = (
        f"https://cyberjapandata.gsi.go.jp/xyz/{layer_id}"
        f"/{zoom}/{xtile}/{ytile}.png"
    )
    if os.path.exists(cache_path):
        return np.array(Image.open(cache_path).convert("RGB"))

    try:
        logger.debug(
            "Fetching DEM tile: layer=%s zoom=%d x=%d y=%d",
            layer_id, zoom, xtile, ytile,
        )
        res = _get_session().get(url, timeout=5)

        if res.status_code == 200:
            img_data = res.content
            arr = np.array(Image.open(io.BytesIO(img_data)).convert("RGB"))
            os.makedirs(cache_subdir, exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(img_data)
            return arr

        logger.warning(
            "DEM tile: unexpected status %d layer=%s tile=(%d,%d)",
            res.status_code, layer_id, xtile, ytile,
        )
        return None

    except requests.RequestException as e:
        logger.warning(
            "DEM tile download failed: layer=%s tile=(%d,%d) error=%s",
            layer_id, xtile, ytile, e,
        )
        if os.path.exists(cache_path):
            return np.array(Image.open(cache_path).convert("RGB"))
        return None


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
# タイル事前取得
# ============================================================

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

    x14_nw, y14_nw, _, _ = _tile_coords(lat_n, lon_w, 14)
    x14_se, y14_se, _, _ = _tile_coords(lat_s, lon_e, 14)
    x15_nw, y15_nw, _, _ = _tile_coords(lat_n, lon_w, 15)
    x15_se, y15_se, _, _ = _tile_coords(lat_s, lon_e, 15)

    for x14 in range(x14_nw, x14_se + 1):
        for y14 in range(y14_nw, y14_se + 1):
            dem14_subdir = os.path.join(CACHE_DIR, "dem_png", str(x14))
            dem14_path   = os.path.join(dem14_subdir, f"{y14}.png")

            x15_lo = max(x14 * 2,     x15_nw)
            x15_hi = min(x14 * 2 + 1, x15_se)
            y15_lo = max(y14 * 2,     y15_nw)
            y15_hi = min(y14 * 2 + 1, y15_se)

            zoom15_tiles = []
            for x15 in range(x15_lo, x15_hi + 1):
                for y15 in range(y15_lo, y15_hi + 1):
                    subdir5a = os.path.join(CACHE_DIR, "dem5a_png", str(x15))
                    path5a   = os.path.join(subdir5a, f"{y15}.png")
                    subdir5b = os.path.join(CACHE_DIR, "dem5b_png", str(x15))
                    path5b   = os.path.join(subdir5b, f"{y15}.png")
                    zoom15_tiles.append((x15, y15, subdir5a, path5a, subdir5b, path5b))

            yield x14, y14, dem14_subdir, dem14_path, zoom15_tiles


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
    force=False かつ dem_png がキャッシュ済みなら位置全体をスキップ。
    """
    if not force and os.path.exists(dem14_path):
        with lock:
            counts["skipped"] += 1
        return

    need_dem = False
    for x15, y15, subdir5a, path5a, subdir5b, path5b in zoom15_tiles:
        if not force and (os.path.exists(path5a) or os.path.exists(path5b)):
            continue
        arr = _fetch_tile("dem5a_png", 15, x15, y15, subdir5a, path5a)
        if arr is not None:
            with lock:
                counts["downloaded_5a"] += 1
            continue
        arr = _fetch_tile("dem5b_png", 15, x15, y15, subdir5b, path5b)
        if arr is not None:
            with lock:
                counts["downloaded_5b"] += 1
            continue
        need_dem = True

    if need_dem:
        arr = _fetch_tile("dem_png", 14, x14, y14, dem14_subdir, dem14_path)
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
    x14_nw, y14_nw, _, _ = _tile_coords(lat_n, lon_w, 14)
    x14_se, y14_se, _, _ = _tile_coords(lat_s, lon_e, 14)
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

    num_workers = min(_MAX_PREFETCH_WORKERS, total)
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

def check_cache_coverage(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> dict:
    """bbox 内のキャッシュカバレッジを確認する（ネットワーク通信なし）。

    zoom-14 位置を管理単位とし、各位置の最高精度レベルを返す。

    Returns:
        {
            "area_total": int,
            "area_5a":    int,   # 5m航空（dem5a_png）がキャッシュ済みのエリア数
            "area_5b":    int,   # 5m写真（dem5b_png）のみのエリア数
            "area_dem":   int,   # 10m（dem_png）のみのエリア数
            "area_none":  int,   # 何もキャッシュなしのエリア数
            "positions":  [{"x14": int, "y14": int, "level": str}, ...]
                          level: "5a" | "5b" | "dem" | "none"
        }
    """
    area_5a = area_5b = area_dem = area_none = 0
    positions: list[dict] = []

    for x14, y14, _, dem14_path, zoom15_tiles in _iter_dem_positions(lat1, lon1, lat2, lon2):
        has_5a  = any(os.path.exists(path5a) for _, _, _, path5a, _, _    in zoom15_tiles)
        has_5b  = any(os.path.exists(path5b) for _, _, _, _, _, path5b    in zoom15_tiles)
        has_dem = os.path.exists(dem14_path)

        if has_5a:
            area_5a  += 1
            level = "5a"
        elif has_5b:
            area_5b  += 1
            level = "5b"
        elif has_dem:
            area_dem += 1
            level = "dem"
        else:
            area_none += 1
            level = "none"

        positions.append({"x14": x14, "y14": y14, "level": level})

    return {
        "area_total": len(positions),
        "area_5a":    area_5a,
        "area_5b":    area_5b,
        "area_dem":   area_dem,
        "area_none":  area_none,
        "positions":  positions,
    }


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
