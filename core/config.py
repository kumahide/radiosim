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
import shutil
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
# ============================================================
def app_base_dir() -> str:
    """凍結時は exe の隣、そうでなければソースの隣（**ポータブル配置の基準**）。

    ⚠️ **「ソースの隣」＝リポジトリ直下**であって、このファイルの隣ではない。
    2.7 スライス H でこのモジュールが `core/` へ入ったので、`__file__` の親を
    そのまま使うと保存先が `<repo>/core/results` へ黙ってずれる。⇒ **層の
    ぶんだけ 1 つ上がる。**

    🔑 **3.1 以降、これは「書き込み先そのもの」ではなくポータブル配置の基準**
    （旧配置の移行元・`portable.txt` の探索場所・LANG_DIR・スクリプト実行時の
    書き込み先）。通常の（凍結・非ポータブル）書き込み先は `_config_base_dir()` /
    `cache_log_base_dir()` / `_results_dir()`（OS 標準の場所）を通る。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_path(*parts: str) -> str:
    """`app_base_dir()` を基準に絶対パスを組み立てる。ポータブル配置専用。"""
    return os.path.join(app_base_dir(), *parts)


# ------------------------------------------------------------
# ポータブル判定（3.1・旧未決 A-4）
#   スクリプト実行は常にポータブル扱い＝結果・設定・キャッシュはリポジトリ配下
#   に留まる（README がスクリプト版を「正式版」と位置づけている）。凍結時は
#   exe の隣に `portable.txt` があるかどうかだけで決める＝分岐点はここ1箇所。
# ------------------------------------------------------------
def is_portable() -> bool:
    """ポータブル配置（exe／スクリプトの隣に書く）なら真。"""
    if not getattr(sys, "frozen", False):
        return True
    return os.path.isfile(app_path("portable.txt"))


# ------------------------------------------------------------
# OS 標準フォルダ（3.1・非ポータブル配置の基準）
#   Windows の Known Folder API（SHGetKnownFolderPath）を使う＝環境変数や
#   `expanduser("~")` は OneDrive の Known Folder Move で実フォルダが移設され
#   ていても追従しない（企業環境で実際に起きる＝[[project_real_world_env_vdi]]
#   と同種の「実機は開発機と違う」罠）。取得できないとき（非 Windows／失敗）は
#   環境変数 → 最後は app_base_dir() へ段階的に落ちる。
# ------------------------------------------------------------
_FOLDERID_ROAMING_APP_DATA = "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}"
_FOLDERID_LOCAL_APP_DATA   = "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}"
_FOLDERID_DOCUMENTS        = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"


def _known_folder_path(guid: str) -> "str | None":
    """Windows の Known Folder を解決する。非 Windows／失敗時は None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class _GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8),
            ]

        clsid = _GUID()
        if ctypes.windll.ole32.CLSIDFromString(guid, ctypes.byref(clsid)) != 0:
            return None
        path_ptr = ctypes.c_wchar_p()
        hresult = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(clsid), 0, 0, ctypes.byref(path_ptr))
        if hresult != 0 or not path_ptr.value:
            return None
        path = path_ptr.value
        ctypes.windll.ole32.CoTaskMemFree(path_ptr)
        return path
    except Exception:
        return None


def _appdata_dir() -> str:
    return (_known_folder_path(_FOLDERID_ROAMING_APP_DATA)
            or os.environ.get("APPDATA") or app_base_dir())


def _local_appdata_dir() -> str:
    return (_known_folder_path(_FOLDERID_LOCAL_APP_DATA)
            or os.environ.get("LOCALAPPDATA") or app_base_dir())


def _documents_dir() -> str:
    return (_known_folder_path(_FOLDERID_DOCUMENTS)
            or os.path.join(os.path.expanduser("~"), "Documents"))


# ------------------------------------------------------------
# 書き込み先の基準（ポータブル／非ポータブルで分岐する唯一の場所）
#   設定＝%APPDATA%（ローミング対象＝軽量な JSON 1 個・複数端末で持ち歩く価値
#   がある）。キャッシュ・ログ＝%LOCALAPPDATA%（ローミング対象にすると企業
#   環境で profile を壊す）。結果＝ドキュメント（利用者が自分で開きに行く
#   成果物を隠しフォルダに置かない）。
# ------------------------------------------------------------
def _config_base_dir() -> str:
    return app_base_dir() if is_portable() else os.path.join(_appdata_dir(), "RadioSim")


def cache_log_base_dir() -> str:
    """ログと DEM キャッシュが共有する基準（dem.py の CACHE_DIR もこれを通す）。"""
    return app_base_dir() if is_portable() else os.path.join(_local_appdata_dir(), "RadioSim")


def _results_dir() -> str:
    if is_portable():
        return app_path("results")
    return os.path.join(_documents_dir(), "RadioSim")


# ============================================================
# 定数
# ============================================================
CONFIG_FILE = os.path.join(_config_base_dir(), "radiosim_conf.json")
RESULTS_DIR = _results_dir()
LOG_FILE    = os.path.join(cache_log_base_dir(), "radiosim.log")
#: 利用者が置く言語ファイル（`<コード>.json`）の置き場。**読むだけ**＝アプリは
#: ここへ書き込まないので、無ければ無いまま（作りに行かない）。ポータブル・
#: 非ポータブルを問わず exe／スクリプトの隣（同梱物を探す場所と同じ基準）。
LANG_DIR    = app_path("lang")


# ============================================================
# 旧配置からの移行（3.1）
#   旧配置＝ exe／スクリプトの隣（= app_path()、2.x 〜 3.0 の唯一の書き込み先）。
#   ポータブル判定が真の間は旧配置と新配置が同じ場所なので何もしない。
#   **コピーのみ・旧は残す**（削除は次版）＝移行失敗＝過去結果の喪失が最大
#   リスクなので、失敗しても壊れるのは「まだ移行できていない」だけに留める。
#   キャッシュは再生成可能なので移行対象に含めない（DEM は取得し直せばよい）。
# ============================================================
def _migrate_legacy_data() -> None:
    if is_portable():
        return
    _migrate_config_file()
    _migrate_results_dir()


def _migrate_config_file() -> None:
    old = app_path("radiosim_conf.json")
    if os.path.exists(CONFIG_FILE) or not os.path.isfile(old):
        return
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        shutil.copy2(old, CONFIG_FILE)
        logger.info("Migrated legacy config: %s -> %s", old, CONFIG_FILE)
    except OSError as e:
        logger.warning("Legacy config migration failed: %s", e)


def _migrate_results_dir() -> None:
    old = app_path("results")
    if not os.path.isdir(old):
        return
    try:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        names = os.listdir(old)
    except OSError as e:
        logger.warning("Legacy results migration failed: %s", e)
        return
    # ⚠️ **1 件ずつ独立して失敗させる**（Codex 独立レビュー round69 P1）＝以前は
    # 全件を 1 つの try で囲んでいたため、1 件の失敗（権限・容量不足等）で
    # 後続の未移行フォルダも移行されないまま打ち切られていた。
    for name in names:
        src = os.path.join(old, name)
        dst = os.path.join(RESULTS_DIR, name)
        if os.path.exists(dst):
            continue                              # 既に移行済み＝上書きしない
        try:
            if os.path.isdir(src):
                # **一時名へコピーしてから rename**＝`copytree` が途中で失敗すると
                # `dst` に不完全なフォルダだけが残り、次回起動時は
                # `os.path.exists(dst)` が真になって「移行済み」と誤認し、
                # 二度と直らなくなる（同レビューの指摘）。rename は同一ボリューム
                # 上ならほぼ原子的なので、`dst` は「完全な移行結果」でしか現れない。
                tmp_dst = dst + ".migrating"
                if os.path.exists(tmp_dst):
                    shutil.rmtree(tmp_dst, ignore_errors=True)
                shutil.copytree(src, tmp_dst)
                os.rename(tmp_dst, dst)
            else:
                shutil.copy2(src, dst)
        except OSError as e:
            logger.warning("Legacy results migration failed for %s: %s", name, e)


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
    # 新配置（%LOCALAPPDATA%\RadioSim 等）は初回起動でまだ存在しない＝
    # FileHandler は作成しないので先に掘る（B-014 の続き＝書き込み先の存在も
    # 解決器の責任にする）。
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
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
os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
_migrate_legacy_data()

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
# 初回起動の表示言語（3.2・I-127）
#   `DEFAULT_CONFIG["lang"]` は固定で "en"。これは**設定ファイルが既に在るとき
#   の欠損補完**としては正しい（2.x からの利用者の画面を勝手に変えない）が、
#   **まだ 1 度も起動していない人**には「利用者が既に示した情報」を無視した値に
#   なる。⇒ 設定ファイルが無いときだけ ①インストーラが置いた種 → ②OS の表示
#   言語 → ③"en" の順で解く。
#
#   ⚠️ **効くのは「設定ファイルがまだ存在しないとき」だけ**＝以後は利用者の
#      選択が常に優先（言語メニューと喧嘩しない）。判定を `load_config` の中や
#      `DEFAULT_CONFIG` 側に入れないのはこのため（あちらは欠損キーの補完器で、
#      既定値そのものを動的にすると「`lang` の既定は `en`」を前提にした検査が
#      環境依存になる）。
#   ⚠️ 候補は**同梱 2 言語だけ**。外部の `lang/<コード>.json` は対象にしない
#      （読めるとは限らず、初回既定が利用者の置いたファイル次第で変わる）。
# ============================================================
#: インストーラが置く「ウィザードで選ばれた言語」の種。**読むだけ**＝アプリは
#: ここへ書かない（LANG_DIR と同じ思想）。ポータブル zip には存在しない。
#: ⛔ インストーラから `radiosim_conf.json` を直接書く案は採らない＝ウィザードは
#:    管理者へ昇格され得る（別ユーザーの %APPDATA% に書く）うえ、上書き
#:    インストールで既存の設定を壊し、`save_config` の原子的書き込みの外から
#:    設定ファイルを触ることになる。
INSTALL_LANG_FILE = app_path("install_lang.txt")

#: Inno Setup の `[Languages]` の `Name` → アプリの言語コード。
_INSTALLER_LANG_CODES: dict[str, str] = {"japanese": "ja", "english": "en"}

#: LANGID の主要言語 ID（下位 10 bit）。ja-JP も ja-JP_radstr も 0x11。
_LANG_JAPANESE = 0x11


def _installer_lang() -> "str | None":
    """インストーラが置いた種を読む。無い／読めない／未知の値なら None。"""
    try:
        with open(INSTALL_LANG_FILE, "r", encoding="utf-8") as f:
            name = f.read().strip().lower()
    except OSError:
        return None
    return _INSTALLER_LANG_CODES.get(name)


def _os_ui_lang() -> "str | None":
    """OS の表示言語（ユーザー既定 UI 言語）を同梱言語へ丸める。

    ⚠️ 見るのは**表示言語**であって地域書式ではない（日本在住で英語 UI を選んで
    いる人は英語で出す）。非 Windows／取得失敗は None。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except Exception:
        return None
    if not langid:
        return None
    return "ja" if (langid & 0x3FF) == _LANG_JAPANESE else "en"


def initial_lang() -> str:
    """初回起動の表示言語。①インストーラの種 → ②OS の表示言語 → ③"en"。

    ①が②より先なのは「利用者が明示的に選んだ」ぶん証拠として強いから
    （英語 UI の Windows で日本語ウィザードを選ぶ人がいる）。
    """
    return _installer_lang() or _os_ui_lang() or DEFAULT_CONFIG["lang"]


def startup_lang(cfg: dict[str, str], path: str = CONFIG_FILE) -> str:
    """起動時に `i18n.set_lang` へ渡す言語コードを決める。

    設定ファイルが在れば**その中身が常に優先**（利用者の選択）。無いときだけ
    `initial_lang()` で解く。
    """
    if os.path.exists(path):
        return cfg.get("lang", DEFAULT_CONFIG["lang"])
    return initial_lang()


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
