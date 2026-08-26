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
import math
import os
import sys
import tempfile

from core import i18n
from core import terrain_grid

# ============================================================
# パス解決
#   アプリが書き込むもの（設定・結果・ログ・DEM キャッシュ）の基準を1箇所に
#   固定する。基準は「実行ファイル／スクリプトの位置」であって
#   **カレントディレクトリではない**。裸の相対パスだと、ショートカットの作業
#   フォルダやコマンドラインの cwd 次第で保存先が黙って変わる（B-014）。
#
#   ⚠️ sys._MEIPASS と混同しないこと。あれは PyInstaller が同梱リソースを
#      展開する一時ディレクトリ＝読み出し専用で、プロセス終了時に消える。
#      書き込み先には絶対に使わない（同梱 README やアイコンの読み出しは
#      views/launcher.py が _MEIPASS を使う＝そちらが正しい用途）。
#
#   OS 標準の場所（%APPDATA% 等）への移設は将来版（3.0）の仕事。ここでは
#   基準を固定するだけで、通常起動時の保存先は従来と完全に同じになる。
# ============================================================
def app_base_dir() -> str:
    """アプリが書き込む先の基準ディレクトリ（凍結時は exe の隣、そうでなければソースの隣）。

    ⚠️ **「ソースの隣」＝リポジトリ直下**であって、このファイルの隣ではない。
    2.7 スライス H でこのモジュールが `core/` へ入ったので、`__file__` の親を
    そのまま使うと保存先が `<repo>/core/results` へ黙ってずれる（設定・ログ・
    DEM キャッシュも同じ経路）。⇒ **層のぶんだけ 1 つ上がる。**
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_path(*parts: str) -> str:
    """`app_base_dir()` を基準に絶対パスを組み立てる。書き込み先はすべてこれを通す。"""
    return os.path.join(app_base_dir(), *parts)


# ============================================================
# 定数
# ============================================================
CONFIG_FILE = app_path("radiosim_conf.json")
RESULTS_DIR = app_path("results")
LOG_FILE    = app_path("radiosim.log")
#: 利用者が置く言語ファイル（`<コード>.json`）の置き場。**読むだけ**＝アプリは
#: ここへ書き込まないので、無ければ無いまま（作りに行かない）。
LANG_DIR    = app_path("lang")


# ============================================================
# 実行ごとの出力ディレクトリ
# ------------------------------------------------------------
# バッチ（batch_…）と条件探索（scenario_…）は「接頭辞＋秒精度のタイムスタンプ」
# で dir を切る。⚠️ 秒精度は同秒起動で衝突する（B-013）。`exist_ok=True` で
# 作っていたため、2 回目の実行が 1 回目の成果物と同じ dir へ書き込み、
# ファイル名が同じもの（summary.csv 等）を黙って上書きしていた。
#
# 作成まで含めてここで面倒を見る＝「衝突しない名前を返す」だけの関数にすると
# 呼び出し側が makedirs するまでの隙間で再び衝突しうる（作成の成否そのものを
# 一意性の判定に使う）。
#
# 単一実行（simulation.py）はタイムスタンプに %f（マイクロ秒）を含むため
# この関数を通さない＝dir 名の形が違い、変えると既存の結果フォルダの並びが
# 変わる。衝突しない以上そのままでよい。
# ============================================================
def new_run_dir(prefix: str, timestamp: str) -> str:
    """`<results>/<prefix>_<timestamp>` を新規作成して返す（衝突時は連番を付す）。"""
    name = f"{prefix}_{timestamp}"
    suffix = 2
    while True:
        candidate = os.path.join(RESULTS_DIR, name)
        try:
            os.makedirs(candidate, exist_ok=False)
            return candidate
        except FileExistsError:
            name = f"{prefix}_{timestamp}_{suffix}"
            suffix += 1

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
    "rain_rate": (0.0,    200.0,    "err_rain_rate"),
}
# ⚠️ `samples` はここに**無い**（I-069・3.0）＝地形の標本数は利用者の入力ではなく
# **距離と解像度の段階から解かれる値**になった（→ `terrain_grid.recommended_samples`）。
# 値域の検査は「段階が語彙のどれか」＝`VALID_RESOLUTIONS` の側へ移した。

# `SimParams` の属性名 → `VALIDATION_RULES` のキー（名前が違うものだけ）。
# ランチャーは config のキー（freq）で持ち、計算側は属性名（freq_mhz）で持つという
# 歴史的なずれがあるので、**値域の出所を 1 つに保つための橋**。
# ⚠️ `num` → `samples` の橋は 3.0（I-069）で外した＝`num` はもう入力ではない。
_ATTR_TO_RULE_KEY = {"freq_mhz": "freq"}


def validate_value(attr: str, value: "float | str") -> "str | None":
    """`SimParams` の属性 1 つ分を検証する。問題なければ None、あればメッセージ。

    **値域の出所は `VALIDATION_RULES` ただ 1 つ**（単一実行のランチャーと同じ表）。
    条件探索（scenario.py）はここを呼ぶ＝フローごとに範囲がずれない。

    ⚠️ NaN / Inf を明示的に弾く：`float("nan")` も `float("inf")` も**パースは
    通る**ので、範囲比較（`vmin <= nan <= vmax` は常に False）に頼ると「範囲外」
    という的外れなメッセージになり、Inf は判定 OK まで出てしまう（B-016 の実測）。
    """
    if attr == "env_type":
        return None if value in VALID_ENV_TYPES else (
            f"{i18n.t('err_env_type')}: {sorted(VALID_ENV_TYPES)}")
    if attr == "diff_method":
        return None if value in VALID_DIFF_METHODS else (
            f"{i18n.t('err_diff_method')}: {sorted(VALID_DIFF_METHODS)}")
    if attr == "resolution":
        return None if value in VALID_RESOLUTIONS else (
            f"{i18n.t('err_resolution')}: {sorted(VALID_RESOLUTIONS)}")

    rule = VALIDATION_RULES.get(_ATTR_TO_RULE_KEY.get(attr, attr))
    if rule is None:
        return None                      # 値域を定めていない項目は素通し
    try:
        val = float(value)               # type: ignore[arg-type]
    except (TypeError, ValueError):
        return i18n.t("err_numeric")
    if not math.isfinite(val):
        return i18n.t("err_not_finite")
    vmin, vmax, msg_key = rule
    if not (vmin <= val <= vmax):
        return i18n.t(msg_key)
    return None


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
    # 地形の解像度（I-069）＝**段階の語**（点数は距離から解かれる）。既定は
    # 「中（約 10m）」＝全国カバーの 10m メッシュと同じ刻み。旧既定の「200 点」は
    # 5km で実効 25m・20km で実効 100m と**距離によって意味が変わっていた**。
    "resolution" : terrain_grid.RESOLUTION_DEFAULT,
    "env_type"   : "los",
    "rain_rate"  : "0.0",
    "diff_method": "bullington",
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
    """現在の設定を JSON で**原子的に**保存する（B-124）。

    同じディレクトリの一時ファイルへ書き切ってから `os.replace` する。⇒ **途中で
    死んでも前の設定が丸ごと残る**。`open(path, "w")` を直に開くと**開いた時点で
    中身が消える**ので、`json.dump` の最中に落ちれば空か途中までの JSON が残り、
    次の起動で `load_config` が握って**全設定が既定値へ戻る**（`proxy_url` が
    消えると DEM 取得が全滅する＝結果が黙って平坦になる入口）。

    **書き方は `report/project.py::save` に揃えてある**（2026-08-03 に同じ不変条件で
    先に直された面＝`tempfile.mkstemp` で名前を OS に作らせ、`fsync` してから
    `os.replace`）。⛔ **`dem._write_tile_atomic` は流用しない**: あちらは「既に在る
    なら書かない」（同じ URL のタイルは同じ内容）だが、**設定は上書きこそが目的**で真逆。

    ⚠️ **1 点だけ `project.py` と違う＝例外を上げずに握る。** あちらは「保存しました」
    と出す前に失敗を知る必要がある（`.rsproj` は唯一の永続化手段）が、設定の保存は
    **画面の操作の副作用**として起きるので、書けなかったからといってアプリを止めない
    （前の設定のまま動き続けられる）。従来の契約をそのまま保つ。
    """
    directory = os.path.dirname(os.path.abspath(path))
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".radiosim_conf-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())   # 電源断でも「空のファイルに置き換わる」を避ける
        os.replace(tmp, path)      # 同一ディレクトリ＝原子的。既存は最後まで無傷
    except Exception as e:
        logger.warning("Config save error: %s", e)
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


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
VALID_ENV_TYPES:    frozenset[str] = frozenset({"urban", "suburban", "rural", "los"})
# ⚠️ **`core/models.py` の `DIFF_METHOD_KEYS` の写し**（`VALID_ENV_TYPES` と同じ扱い＝
# この層は `models` を import しない）。**ずれは `tests/test_config.py` が機械で見る。**
VALID_DIFF_METHODS: frozenset[str] = frozenset({"single", "bullington"})
#: 旧名 → 新名（入力だけ受ける）。`models.DIFF_METHOD_ALIASES` の写し。
DIFF_METHOD_ALIASES: dict[str, str] = {"deygout": "bullington"}
# ⚠️ 段階の語彙は**写さずに引く**（I-069）＝`terrain_grid` は標準ライブラリだけの
# 純粋な層なので、この層から import してよい（`VALID_ENV_TYPES` が `models.ENV_KEYS`
# の写しになっているのとは違い、ここには写しが無い）。
VALID_RESOLUTIONS:  frozenset[str] = frozenset(terrain_grid.RESOLUTION_KEYS)
# 旧名（内部参照の互換）。新規コードは公開名を使うこと。
_VALID_ENV_TYPES    = VALID_ENV_TYPES
_VALID_DIFF_METHODS = VALID_DIFF_METHODS

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

    # 地形の解像度（段階）のバリデーション（I-069）
    res_raw = c.get("resolution", DEFAULT_CONFIG["resolution"]).strip()
    if res_raw not in VALID_RESOLUTIONS:
        errors.append(
            f"[resolution] {i18n.t('err_resolution')}: {sorted(VALID_RESOLUTIONS)}"
            f" (value: '{res_raw}')"
        )

    # diff_method のバリデーション
    # ⚠️ **旧名を受けてから検査する**＝2.x〜3.0a1 の `.rsproj`／設定／入力 CSV に
    #    `deygout` が残っている（B-130 で手法ごと差し替えた）。受けないと**保存済みの
    #    案件が「不正な値」で開けなくなる**。
    diff_raw = c.get("diff_method", "bullington").strip()
    diff_raw = DIFF_METHOD_ALIASES.get(diff_raw, diff_raw)
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
