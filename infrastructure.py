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
    logger.info("Proxy configured: %r", _proxy_url or "(system)")


def _get_session() -> "requests.Session":
    global _http_session
    with _session_lock:
        if _http_session is None:
            s = requests.Session()
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

    headers = {"User-Agent": version.USER_AGENT}

    try:
        logger.debug(
            "Fetching DEM tile: layer=%s zoom=%d x=%d y=%d",
            layer_id, zoom, xtile, ytile,
        )
        res = _get_session().get(url, timeout=5, headers=headers)

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


def _download_tile_set(
    tiles: list[tuple],
    progress_cb=None,  # callback(done: int, total: int) | None
) -> dict[str, int]:
    """タイル座標セットを並列 DL してディスクキャッシュに保存する共通コア。

    Returns:
        {"total": int, "downloaded": int, "cached": int, "failed": int}
    """
    total = len(tiles)
    if total == 0:
        return {"total": 0, "downloaded": 0, "cached": 0, "failed": 0}

    counts = {"done": 0, "downloaded": 0, "cached": 0, "failed": 0}
    lock   = threading.Lock()
    work_q: queue.Queue = queue.Queue()
    for t in tiles:
        work_q.put(t)

    def _worker() -> None:
        while True:
            try:
                layer_id, zoom, x, y, subdir, cache_path = work_q.get_nowait()
            except queue.Empty:
                return
            already = os.path.exists(cache_path)
            try:
                arr = _fetch_tile(layer_id, zoom, x, y, subdir, cache_path)
                with lock:
                    if arr is not None:
                        counts["cached" if already else "downloaded"] += 1
                    else:
                        counts["failed"] += 1
            except Exception as e:
                logger.warning("prefetch worker error: %s", e)
                with lock:
                    counts["failed"] += 1
            finally:
                work_q.task_done()
                with lock:
                    counts["done"] += 1
                    if progress_cb:
                        progress_cb(counts["done"], total)

    num_workers = min(_MAX_PREFETCH_WORKERS, total)
    threads = [threading.Thread(target=_worker, daemon=True) for _ in range(num_workers)]
    for th in threads:
        th.start()
    work_q.join()

    logger.info(
        "Prefetch complete: total=%d downloaded=%d cached=%d failed=%d",
        total, counts["downloaded"], counts["cached"], counts["failed"],
    )
    return {
        "total"     : total,
        "downloaded": counts["downloaded"],
        "cached"    : counts["cached"],
        "failed"    : counts["failed"],
    }


def count_bbox_tiles(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
) -> int:
    """bbox 内のタイル総枚数を返す。プログレスバーの maximum 設定等に使う。"""
    return len(_enumerate_bbox(lat1, lon1, lat2, lon2))


def prefetch_tiles(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    progress_cb=None,  # callback(done: int, total: int) | None
) -> dict[str, int]:
    """bbox 内の全 DEM タイルをダウンロードしてディスクキャッシュに保存する。

    Returns:
        {"total": int, "downloaded": int, "cached": int, "failed": int}
    """
    tiles = _enumerate_bbox(lat1, lon1, lat2, lon2)
    return _download_tile_set(tiles, progress_cb)
