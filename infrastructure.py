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
import threading
from email.utils import formatdate

import numpy as np
import requests
from PIL import Image

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
    "freq"    : (1.0,    100000.0, "Frequency must be between 1 and 100000 MHz"),
    "p_tx"    : (-30.0,  60.0,     "TX Power must be between -30 and 60 dBm"),
    "gain_tx" : (0.0,    60.0,     "TX Antenna Gain must be between 0 and 60 dBi"),
    "gain_rx" : (0.0,    60.0,     "RX Antenna Gain must be between 0 and 60 dBi"),
    "sens"    : (-130.0, -20.0,    "Sensitivity must be between -130 and -20 dBm"),
    "h_tx"    : (0.0,    500.0,    "TX Antenna Height must be between 0 and 500 m"),
    "h_rx"    : (0.0,    500.0,    "RX Antenna Height must be between 0 and 500 m"),
    "veg_h"   : (0.0,    100.0,    "Vegetation Height must be between 0 and 100 m"),
    "k_factor": (0.0,    30.0,     "K-Factor must be between 0 and 30"),
    "samples" : (10,     2000,     "Sampling Points must be between 10 and 2000"),
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
}


# ============================================================
# 設定ファイル
# ============================================================
def load_config(path: str = CONFIG_FILE) -> dict[str, str]:
    """保存済み設定を読み込む。失敗時はデフォルトを返す。"""
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                config.update(json.load(f))
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


# ============================================================
# 入力バリデーション
# ============================================================
def validate_config(c: dict[str, str]) -> list[str]:
    """
    入力値を検証し、エラーメッセージのリストを返す。
    空リストなら正常。
    """
    errors: list[str] = []

    for key, (vmin, vmax, msg) in VALIDATION_RULES.items():
        raw = c.get(key, "").strip()
        try:
            val = float(raw)
            if not (vmin <= val <= vmax):
                errors.append(f"[{key}] {msg} (value: {val})")
        except ValueError:
            errors.append(f"[{key}] A numeric value is required (value: '{raw}')")

    for coord_key, label in [("start", "Start"), ("end", "End")]:
        raw = c.get(coord_key, "").strip()
        parts = raw.split(",")
        if len(parts) != 2:
            errors.append(
                f"[{coord_key}] {label} Coords must be in \"lat, lon\" format"
            )
            continue
        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
        except ValueError:
            errors.append(
                f"[{coord_key}] {label} Coords contain an invalid numeric value"
            )
            continue
        if not (-90.0 <= lat <= 90.0):
            errors.append(
                f"[{coord_key}] Latitude must be between -90 and 90 (value: {lat})"
            )
        if not (-180.0 <= lon <= 180.0):
            errors.append(
                f"[{coord_key}] Longitude must be between -180 and 180 (value: {lon})"
            )

    # env_type のバリデーション（許容値リストとの照合）
    valid_env_types = {"urban", "suburban", "rural", "los"}
    env_raw = c.get("env_type", "suburban").strip()
    if env_raw not in valid_env_types:
        errors.append(
            f"[env_type] Must be one of {sorted(valid_env_types)} (value: '{env_raw}')"
        )

    # TX と RX が同一点でないかチェック
    if not errors:
        try:
            s_lat, s_lon = [float(x.strip()) for x in c["start"].split(",")]
            e_lat, e_lon = [float(x.strip()) for x in c["end"].split(",")]
            if abs(s_lat - e_lat) < 1e-7 and abs(s_lon - e_lon) < 1e-7:
                errors.append(
                    "Start and End coordinates are identical."
                    " Please specify different locations."
                )
        except Exception:
            pass

    return errors


# ============================================================
# DEM タイルクライアント
# ============================================================
_tile_cache: dict[tuple, np.ndarray] = {}
_cache_lock = threading.Lock()


def get_elevation(lat: float, lon: float) -> float:
    """
    国土地理院 DEM PNG から標高 [m] を取得する。
    取得失敗時は 0.0 を返す。
    """
    try:
        zoom    = 14
        n       = 2.0 ** zoom
        xtile_f = (lon + 180.0) / 360.0 * n
        ytile_f = (
            1.0
            - math.log(
                math.tan(math.radians(lat))
                + 1 / math.cos(math.radians(lat))
            ) / math.pi
        ) / 2.0 * n

        xtile = int(xtile_f)
        ytile = int(ytile_f)
        px = min(255, max(0, int((xtile_f - xtile) * 256)))
        py = min(255, max(0, int((ytile_f - ytile) * 256)))

        tile_key     = (xtile, ytile)
        cache_subdir = os.path.join(CACHE_DIR, str(zoom), str(xtile))
        cache_path   = os.path.join(cache_subdir, f"{ytile}.png")

        with _cache_lock:
            if tile_key not in _tile_cache:
                arr = _fetch_tile(zoom, xtile, ytile, cache_subdir, cache_path)
                if arr is None:
                    return 0.0
                _tile_cache[tile_key] = arr

        return _decode_elevation(_tile_cache[tile_key][py, px])

    except Exception as e:
        logger.error(
            "Elevation decode error: lat=%.6f lon=%.6f error=%s", lat, lon, e
        )
        return 0.0


def _fetch_tile(
    zoom: int,
    xtile: int,
    ytile: int,
    cache_subdir: str,
    cache_path: str,
) -> "np.ndarray | None":
    """タイル画像を取得して numpy 配列で返す。失敗時は None。"""
    url     = (
        f"https://cyberjapandata.gsi.go.jp/xyz/dem_png"
        f"/{zoom}/{xtile}/{ytile}.png"
    )
    headers = {"User-Agent": "Mozilla/5.0 RadioSim/3.1"}

    if os.path.exists(cache_path):
        headers["If-Modified-Since"] = formatdate(
            os.path.getmtime(cache_path), usegmt=True
        )

    try:
        logger.debug("Fetching DEM tile: zoom=%d x=%d y=%d", zoom, xtile, ytile)
        res = requests.get(url, timeout=5, headers=headers)

        if res.status_code == 200:
            img_data = res.content
            arr = np.array(Image.open(io.BytesIO(img_data)).convert("RGB"))
            os.makedirs(cache_subdir, exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(img_data)
            return arr

        # 304 等 → ローカルキャッシュを使用
        if os.path.exists(cache_path):
            return np.array(Image.open(cache_path).convert("RGB"))

        logger.warning(
            "DEM tile: unexpected status %d tile=(%d,%d)",
            res.status_code, xtile, ytile,
        )
        return None

    except requests.RequestException as e:
        logger.warning(
            "DEM tile download failed: tile=(%d,%d) error=%s", xtile, ytile, e
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
