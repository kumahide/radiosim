"""
config.py
=========
アプリ設定・入力バリデーション・ロギングを担う。

  - ロギング設定（logger）
  - アプリ設定ファイル (JSON) の読み書き（app/sim キーの論理分離）
  - 入力バリデーションルール定義と検証

外部依存は標準ライブラリ（json/logging/os）と i18n のみ。ネットワーク・PIL・
numpy には一切依存しない（DEM タイル取得は dem.py が担う）。infrastructure.py を
config.py（本体）＋ dem.py へ分割した際に切り出した設定・検証層。
"""

import json
import logging
import os

import i18n

# ============================================================
# 定数
# ============================================================
CONFIG_FILE = "radiosim_conf.json"
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
    "coord_format": "dd",
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


# ------------------------------------------------------------
# 設定キーの論理分類（案1: ファイルは flat のまま、コードで責務分離）
#   APP_KEYS … アプリ環境設定（ユーザー全体・メニューで変更・永続）
#   SIM_KEYS … 直近のシミュレーションパラメータ（実行ごとにフォームから更新）
# 将来マップウィンドウ設定を足すときは APP_KEYS に追加し DEFAULT_CONFIG にも
# 既定値を1行加える（段階移行で app/sim ネスト構造へ昇格する余地は残す）。
# ------------------------------------------------------------
APP_KEYS: frozenset[str] = frozenset({"theme", "lang", "proxy_url", "coord_format"})
SIM_KEYS: frozenset[str] = frozenset(DEFAULT_CONFIG) - APP_KEYS


def _save_subset(values: dict[str, str], keys: frozenset[str],
                 path: str = CONFIG_FILE) -> None:
    """指定キー群だけを更新して保存する。他のキーは既存ファイルの値を保持する。

    これにより「フォームから sim キーを保存しても app キーは消えない」「メニューで
    app キーを変えても sim キーは保持される」を、呼び出し側の手動再合流なしで実現する。
    """
    merged = load_config(path)
    for k in keys:
        if k in values:
            merged[k] = values[k]
    save_config(merged, path)


def save_sim(values: dict[str, str], path: str = CONFIG_FILE) -> None:
    """シミュレーションパラメータのみ保存（app 設定は保持）。"""
    _save_subset(values, SIM_KEYS, path)


def save_app(values: dict[str, str], path: str = CONFIG_FILE) -> None:
    """アプリ環境設定のみ保存（直近の sim パラメータは保持）。"""
    _save_subset(values, APP_KEYS, path)


def select_sim(values: dict) -> dict:
    """入力 dict から sim キーだけを抜き出す（app キーは捨てる）。

    「パラメータ読込」が他人の設定ファイル（app キー混在の radiosim_conf.json 等）を
    読んでも theme/lang/proxy_url を取り込まないことを、呼び出し側に依存せず保証する。
    """
    return {k: v for k, v in values.items() if k in SIM_KEYS}


def select_app(values: dict) -> dict:
    """入力 dict から app キーだけを抜き出す（sim キーは捨てる）。

    「アプリ設定読込」が settings.json（sim 限定）を読んでも sim パラメータを
    取り込まないことを、呼び出し側に依存せず保証する。select_sim と対称。
    """
    return {k: v for k, v in values.items() if k in APP_KEYS}


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
