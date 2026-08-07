"""
tests/conftest.py
=================
共有フィクスチャと、テストを実行環境から切り離すゲート群
（外部ネットワーク・モーダルダイアログ・**開発機の設定と書き込み先**）。
"""

import logging
import shutil
import socket
import tempfile
import time

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import models
from core import simulation as sim


# ============================================================
# 外部ネットワーク遮断ゲート
# ============================================================
# ユニットテストから GSI（cyberjapandata.gsi.go.jp）へ実リクエストが飛ぶのを
# 止める。注意書きではなくゲートにしてあるのは、この混入が「失敗せずに」
# 起きるため（→ [[feedback-promote-recurring-checks]]）。
#
# 実例（B-006・2026-07-20）: バッチの成果物生成をワーカースレッドへ移した際、
# run_batch がサマリ地図の淡色地図タイル取得を含むようになり、ユニットテストが
# 実ネットワークを叩き始めた。report_map はベストエフォート設計で取得失敗を
# None に落として続行するため、オンラインでもオフラインでもテストは緑のまま
# 通る。所要時間の変化に気づかない限り検出できない。
#
# 遮断は socket 層で行う。requests / urllib / tkintermapview のどの経路から
# 来ても最終的にここへ落ちるため、呼び出し側を個別に塞ぐ必要がない。
# localhost は通す（将来のローカルサーバ系テストを巻き込まないため）。
# 正当な理由で外部通信するテストは @pytest.mark.network を付けて明示する。

_real_socket_connect     = socket.socket.connect
_real_create_connection  = socket.create_connection


def _is_local(address) -> bool:
    """接続先が localhost / UNIX ソケットなら True。"""
    if not isinstance(address, tuple) or not address:
        return True   # AF_UNIX 等はアドレスがタプルでない＝外部通信ではない
    host = address[0]
    return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "")


class NetworkAccessBlocked(RuntimeError):
    """テスト中に外部ネットワークアクセスが試みられた。"""


def _blocked(address):
    raise NetworkAccessBlocked(
        f"テストから外部ネットワークへの接続が試みられました: {address}\n"
        "ユニットテストは外部 API（GSI 等）を叩いてはいけません。"
        "取得層を monkeypatch するか、意図的な通信なら "
        "@pytest.mark.network を付けてください。"
    )


@pytest.fixture(autouse=True)
def _block_network(request):
    """全テストで外部ネットワークを遮断する（@pytest.mark.network で解除）。"""
    if request.node.get_closest_marker("network"):
        yield
        return

    def _guarded_connect(self, address):
        if not _is_local(address):
            _blocked(address)
        return _real_socket_connect(self, address)

    def _guarded_create_connection(address, *args, **kwargs):
        if not _is_local(address):
            _blocked(address)
        return _real_create_connection(address, *args, **kwargs)

    socket.socket.connect = _guarded_connect
    socket.create_connection = _guarded_create_connection
    try:
        yield
    finally:
        socket.socket.connect = _real_socket_connect
        socket.create_connection = _real_create_connection


# ============================================================
# 実行環境の一致ゲート（宣言した venv でしかテストを回させない）
# ============================================================
# 背景（2026-08-03・独立レビュー Codex 由来）: `RADIOSIM_PYTHON` は「**検証にも
# ビルドにも使う唯一の環境**」の宣言で、`build.bat` は未設定なら止まる。ところが
# **pytest 側には何の強制も無く**、README のテスト手順も裸の `python -m pytest` の
# ままだった。⇒ **別の環境で検証して、別の環境で exe を焼ける**。
#
# 🔴 実際に起きた（2026-08-03・このゲートを入れた当日）: Claude が
# `2.6` のリリース準備で全スイートをシステムの Python 3.14 で回し、依存版のずれで
# 落ちた 4 件を「既知の課題」と誤読して「全件緑（既知分を除外）」と報告した。
# 宣言環境では 10 件とも一致して**除外ゼロで緑**だった。**赤の意味を取り違えた**
# のではなく、**そもそも違う環境の赤を見ていた**。
#
# 判定は「宣言があるときだけ」＝`RADIOSIM_PYTHON` 未設定の環境（CI・他マシンの
# clone）では何もしない。宣言と食い違うときだけ、理由と直し方を出して止める。
def _same_interpreter(a: str, b: str) -> bool:
    """パスの表記ゆれ（大小・区切り・相対）を吸収して同一性を見る。"""
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def pytest_configure(config):
    """収集の前に走る唯一の口＝環境の宣言を検査し、書き込み先を隔離する。

    ⚠️ **隔離は検査の後・テストモジュールの import より前**。ここより後ろに置くと、
    import 時に実パスを掴むテストモジュールが出る。
    """
    _require_declared_interpreter()
    _isolate_app_paths()


def _require_declared_interpreter():
    declared = os.environ.get("RADIOSIM_PYTHON", "").strip().strip('"')
    if not declared:
        return                                  # 宣言が無い環境＝CI 等。何もしない
    if not os.path.exists(declared):
        # 🔴 **ここで return してはいけない**（2026-08-04・独立レビュー Codex）。
        # venv を移動・削除した／設定を打ち間違えた状態で、**ゲートが黙って
        # 無効になる**＝このゲートが防ごうとしている「別環境での偽の成功」が
        # そのまま再発する（実測＝`Z:\missing\python.exe` を宣言しても、
        # 別の Python で収集が成功した）。**宣言が壊れているなら止める。**
        raise pytest.UsageError(
            "RADIOSIM_PYTHON が指す Python が見つかりません。\n"
            f"  RADIOSIM_PYTHON : {declared}\n"
            "venv を作り直したか、パスを打ち間違えています。"
            "正しい python.exe を指すよう設定し直してください"
            "（未設定にすればこの検査は行われません）。"
        )
    if _same_interpreter(declared, sys.executable):
        return None
    raise pytest.UsageError(
        "宣言された環境と違う Python でテストを回しています。\n"
        f"  RADIOSIM_PYTHON : {declared}\n"
        f"  いま実行中       : {sys.executable}\n"
        "この環境の依存版は requirements.txt のピンと一致しない可能性があり、"
        "**検証した版と配布する exe の版がずれます**（build.bat は上の環境で焼きます）。\n"
        "次のように回してください:\n"
        '  & "$env:RADIOSIM_PYTHON" -m pytest\n'
        "意図して別環境で回すなら、そのシェルで RADIOSIM_PYTHON を空にしてください。"
    )


# ============================================================
# 開発機の設定と書き込み先からの隔離（I-055 ①）
# ============================================================
# テストは **利用者の実体に一切触らない**。触っていたのは 2 方向とも:
#
#   読む側: 窓が `config.load_config()` を直に呼ぶため、GUI テストは
#     リポジトリ直下の `radiosim_conf.json`（開発機の実設定）を読んでいた。
#     ⇒ ①同じコミットが開発機の設定次第で緑にも赤にもなる ②CI は常に既定値で
#     走るので、既定でない側の経路が**一度も実行されない**。B-034（DMS の座標が
#     4 割壊れる）が長期間生き延びた直接の理由で、**発見は運**だった。
#
#   書く側: `LOG_FILE` / `RESULTS_DIR` / `CACHE_DIR` / `CONFIG_FILE` を一つも
#     差し替えていなかったため、**pytest を回すたびに実リポジトリへ書いていた**
#     （実測 2026-08-03: `radiosim.log` が 3.75MB）。🔑 これは 8.1MB ログ誤 push
#     事故の原料そのもの＝流出したログの中身は pytest のネットワーク遮断警告で、
#     書き込み先を隔離していればあのファイルは生まれていない。
#
# ⚠️ **製品コードは触らない**。保存先そのものの移設は 3.0 の仕事（利用者への
#    約束が変わる）。ここでやるのは**テスト実行時だけの付け替え**で、解決器
#    （`config.app_base_dir()` / `app_path()`）は素のまま残す＝「通常起動では
#    従来と同じ場所」を tests/test_paths.py が引き続き検証できる。
#
# 🔑 **定数の代入だけでは足りない**（`config.py` の関数は
#    `def load_config(path: str = CONFIG_FILE)` と**既定値を def 時に焼き込む**）。
#    後から `config.CONFIG_FILE` を差し替えても、引数なしの呼び出しは**古いパスを
#    使い続ける**。⇒ 関数の `__defaults__` / `__kwdefaults__` まで書き換える。
#    列挙ではなく**値が一致するものを全部**置き換える形にしてあるので、パス既定を
#    持つ関数が増えても自動で乗る（[[feedback-promote-recurring-checks]] 実証 10）。
#
# 隔離は `pytest_configure`（**テストモジュールの import より前**）で 1 回行い、
# 定数の再適用だけを毎テスト前に行う＝`importlib.reload(config)` で素へ戻る
# テストがあるため（reload は定数を実パスへ書き戻す）。

_ISOLATED_TMP_DIR: "str | None" = None

#: 隔離前の本来の値。**通常起動の保存先はこれ**＝互換性の検証はこちらを見る。
ORIGINAL_APP_PATHS: dict[str, str] = {}

#: 隔離後の値（定数の再適用に使う）。
_ISOLATED_APP_PATHS: dict[str, str] = {}


def _repo_modules():
    """リポジトリ配下から読み込まれたモジュールだけを返す（site-packages を除く）。"""
    root = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if path and os.path.normcase(os.path.abspath(path)).startswith(root + os.sep):
            yield module


def _isolate_app_paths() -> None:
    """設定・結果・ログ・DEM キャッシュの行き先を一時ディレクトリへ向ける。"""
    global _ISOLATED_TMP_DIR

    from core import config
    from core import dem

    _ISOLATED_TMP_DIR = tempfile.mkdtemp(prefix="radiosim-tests-")
    ORIGINAL_APP_PATHS.update({
        "CONFIG_FILE": config.CONFIG_FILE,
        "RESULTS_DIR": config.RESULTS_DIR,
        "LOG_FILE":    config.LOG_FILE,
        "CACHE_DIR":   dem.CACHE_DIR,
    })
    _ISOLATED_APP_PATHS.update({
        "CONFIG_FILE": os.path.join(_ISOLATED_TMP_DIR, "radiosim_conf.json"),
        "RESULTS_DIR": os.path.join(_ISOLATED_TMP_DIR, "results"),
        "LOG_FILE":    os.path.join(_ISOLATED_TMP_DIR, "radiosim.log"),
        "CACHE_DIR":   os.path.join(_ISOLATED_TMP_DIR, "terrain_cache"),
    })
    apply_app_path_isolation()
    _redirect_file_logging(ORIGINAL_APP_PATHS["LOG_FILE"], _ISOLATED_APP_PATHS["LOG_FILE"])


def apply_app_path_isolation() -> None:
    """定数と既定引数を隔離後の値へ（再）適用する。

    **`importlib.reload(config)` の後にも呼べる**ことが要点＝reload は
    モジュールを再実行するので、定数も*関数も*実パスを掴んだ状態で作り直される
    （実際 tests/test_paths.py に reload するテストがあり、そこを境に隔離が
    抜けていた＝この関数が「両方」を面倒みる理由）。
    """
    if not _ISOLATED_APP_PATHS:
        return
    from core import config
    from core import dem

    config.CONFIG_FILE = _ISOLATED_APP_PATHS["CONFIG_FILE"]
    config.RESULTS_DIR = _ISOLATED_APP_PATHS["RESULTS_DIR"]
    config.LOG_FILE    = _ISOLATED_APP_PATHS["LOG_FILE"]
    dem.CACHE_DIR      = _ISOLATED_APP_PATHS["CACHE_DIR"]
    _rebind_path_defaults(
        {ORIGINAL_APP_PATHS[k]: _ISOLATED_APP_PATHS[k] for k in ORIGINAL_APP_PATHS})


def _rebind_path_defaults(replacements: dict) -> None:
    """関数の既定引数に焼き込まれた実パスを、隔離後のパスへ差し替える。"""
    def _swap(values):
        return tuple(replacements.get(v, v) if isinstance(v, str) else v for v in values)

    for module in _repo_modules():
        for obj in list(vars(module).values()):
            if not callable(obj) or not hasattr(obj, "__defaults__"):
                continue
            if obj.__defaults__:
                obj.__defaults__ = _swap(obj.__defaults__)
            if getattr(obj, "__kwdefaults__", None):
                obj.__kwdefaults__ = {
                    k: replacements.get(v, v) if isinstance(v, str) else v
                    for k, v in obj.__kwdefaults__.items()
                }


def _redirect_file_logging(old_log: str, new_log: str) -> None:
    """既に開いている実ログのハンドラを閉じ、一時ディレクトリのログへ差し替える。

    `config.py` は import 時に `setup_logging()` を呼び、その場で
    `FileHandler` が実ログを**開く**。定数を差し替えても開いたままなので、
    ハンドラそのものを取り替えないとテストの出力は実ログへ流れ続ける。
    """
    for handler in list(logging.root.handlers):
        if not isinstance(handler, logging.FileHandler):
            continue
        if os.path.normcase(handler.baseFilename) != os.path.normcase(os.path.abspath(old_log)):
            continue
        logging.root.removeHandler(handler)
        handler.close()
        replacement = logging.FileHandler(new_log, encoding="utf-8")
        replacement.setFormatter(handler.formatter)
        replacement.setLevel(handler.level)
        logging.root.addHandler(replacement)


@pytest.fixture(autouse=True)
def _app_paths_stay_isolated():
    """毎テスト前に定数を再適用する（`importlib.reload` 後の復帰）。"""
    apply_app_path_isolation()
    yield


# ============================================================
# skip 予算（2.7 スライス F で新設）
# ============================================================
# 🔴 **「ほとんど走らなかった」を終了コード 0 で返させない。**
# 上の `_no_display` は Tk 由来の大量 skip を塞ぐが、**skip の出所は他にも増える**
# ので、理由を問わない網をもう 1 枚置く（[[feedback-promote-recurring-checks]]
# ＝列挙で塞ぐ穴は名前 1 つで開く）。
#
# 桁で区別する＝正当な skip は 2026-08-07 実測で **1297 本中 5 本（0.4%）**
# （alpha 段階の doc 追従猶予）。事故は **112 本中 106 本（95%）**だった。
# ⚠️ **小さな部分実行には効かせない**＝1 ファイルだけ回すと正当な skip でも
# 割合が跳ねる（`test_docs_consistency.py` 単独で 39 本中 5 本＝13%）。
_SKIP_BUDGET_MIN_TESTS = 100      # これ未満の実行は部分実行とみなして見ない
_SKIP_BUDGET_RATIO     = 0.25     # 全体実行でこれを超えたら「走っていない」


def pytest_sessionfinish(session, exitstatus):
    """大半が skip なら、終了コードを失敗にする（緑に化けさせない）。"""
    if exitstatus != 0:
        return                      # 既に赤＝上書きしない
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    total = session.testscollected
    if reporter is None or total < _SKIP_BUDGET_MIN_TESTS:
        return
    skipped = len(reporter.stats.get("skipped", []))
    if skipped <= total * _SKIP_BUDGET_RATIO:
        return
    reporter.write_sep(
        "=",
        f"skip が多すぎます（{skipped}/{total} 本）。"
        f"上限は {_SKIP_BUDGET_RATIO:.0%}＝この実行はテストとして成立していません。"
        "（GUI の skip なら表示環境を疑うこと。ヘッドレスで回すなら "
        "RADIOSIM_HEADLESS=1 を宣言する）",
        red=True, bold=True,
    )
    session.exitstatus = 1

def pytest_unconfigure(config):
    """一時ディレクトリを片付ける（ログハンドラを閉じてから）。"""
    if not _ISOLATED_TMP_DIR:
        return
    for handler in list(logging.root.handlers):
        if isinstance(handler, logging.FileHandler):
            logging.root.removeHandler(handler)
            handler.close()
    shutil.rmtree(_ISOLATED_TMP_DIR, ignore_errors=True)


# ============================================================
# モーダルダイアログ・OS 委譲の遮断ゲート
# ============================================================
# GUI テストからモーダルダイアログが出ると、`wait_window()` が**人がボタンを
# 押すまで返らない**＝テストが止まる。CI では気づけず（表示できないので別の形で
# 失敗する）、ローカルでは実行者が手で押す羽目になる。実際 2026-07-26 に条件探索の
# 完了ダイアログ（「保存しました。開きますか？」）で止まり、ユーザーが手動で
# 応答した。しかも「はい」を押すと `os.startfile` でブラウザが開く＝テストが
# 環境の外へ出る。
#
# ネットワーク遮断と**同じ理由・同じ形**で塞ぐ（→ [[feedback-promote-recurring-checks]]）：
# テストごとに monkeypatch を書くのは「思い出す規則」で、新しい GUI テストを
# 書いた人が忘れた瞬間に再発する。既定で塞ぎ、必要なら明示的に外す。
#
# 塞ぐのは「人の操作を待つ」「OS へ制御を渡す」の 2 種類:
#   - views.dialogs の alert / confirm / choose（自前モーダル＝wait_window）
#   - os.startfile（ブラウザ・エクスプローラ起動）
#   - tkinter.filedialog の各種（ファイル選択ダイアログ）
# 応答は**最も安全な既定**＝confirm は False（「いいえ」）、choose は None
# （キャンセル）、ファイル選択は "" （キャンセル）を返す。
#
# 呼ばれた内容を検証したいテストは `dialog_calls` フィクスチャを受け取る。


class _DialogCalls(list):
    """記録された呼び出し（`("confirm", title, message)` 等）。"""

    def kinds(self) -> list:
        return [c[0] for c in self]


@pytest.fixture(autouse=True)
def dialog_calls(request, monkeypatch):
    """モーダルダイアログと OS 委譲を塞ぎ、呼び出しを記録する。

    テストがダイアログを**出そうとしたこと自体**を検証できるよう、戻り値を
    そのままフィクスチャとして提供する。実物を出したいテストは
    `@pytest.mark.real_dialogs` を付ける（人が操作する前提の手動確認用）。
    """
    calls = _DialogCalls()
    if request.node.get_closest_marker("real_dialogs"):
        yield calls
        return

    from tkinter import filedialog

    from views import dialogs

    def _alert(parent, title, message):
        calls.append(("alert", title, message))

    def _confirm(parent, title, message):
        calls.append(("confirm", title, message))
        return False                     # 既定は「いいえ」＝副作用の少ない側

    def _choose(parent, title, message, options, cancel_label=None):
        calls.append(("choose", title, message))
        return None                      # 既定はキャンセル

    monkeypatch.setattr(dialogs, "alert", _alert)
    monkeypatch.setattr(dialogs, "confirm", _confirm)
    monkeypatch.setattr(dialogs, "choose", _choose)
    monkeypatch.setattr(os, "startfile",
                        lambda path, *a, **k: calls.append(("startfile", str(path))),
                        raising=False)
    for name in ("askopenfilename", "asksaveasfilename", "askdirectory"):
        monkeypatch.setattr(filedialog, name,
                            lambda *a, _n=name, **k: (calls.append((_n,)), "")[1])
    # matplotlib の `plt.show()` も入れ子 mainloop でブロックする（グラフ窓）。
    # ⚠️ ここで pyplot を import しない（重い＝matplotlib を使わないテストにまで
    # 起動コストを乗せる）。既に読み込まれている時だけ塞ぐ＝グラフ窓へ到達し得る
    # テストは、そのモジュールを import した時点で pyplot も読み込んでいる。
    plt = sys.modules.get("matplotlib.pyplot")
    if plt is not None:
        monkeypatch.setattr(plt, "show",
                            lambda *a, **k: calls.append(("plt.show",)))
    yield calls


# ============================================================
# プロセス横断状態のリセット
# ============================================================
@pytest.fixture(autouse=True)
def _clear_terrain_cache():
    """テスト間で地形キャッシュを空にする。

    `simulation._terrain_cache` はプロセス全体で共有され、キーは座標＋サンプル数
    のみ（周波数等は地形に影響しないので含まない）。テストの多くが同一座標を
    使うため、消さないと前のテストの結果が次のテストへ漏れる。実際、バッチを
    キャッシュ付き取得へ切り替えた際、取得失敗を検証するテストが「前のテストの
    キャッシュにヒットして取得自体が走らない」ため緑になってしまった。
    """
    sim.clear_terrain_cache()
    yield
    sim.clear_terrain_cache()


@pytest.fixture
def default_params_dict():
    """SimParams / validate_config に渡す標準パラメータ辞書。"""
    return {
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
        "samples"    : "50",
        "diff_method": "deygout",
        "env_type"   : "los",
        "rain_rate"  : "0.0",
    }


@pytest.fixture
def flat_terrain():
    """平坦地形（標高 0m 均一、100 サンプル）。"""
    raw = np.zeros(100)
    return models.calculate_terrain_profile(raw, 34.5429, 132.4118, 34.5389, 132.4050)


# ============================================================
# Tk ルート生成（間欠的な初期化失敗のリトライ）
# ============================================================
# この環境では Tk の初期化が間欠的に失敗する。**毎回違う .tcl が「読めない」と
# 言われ、errno が "No error" や実在ファイルへの "no such file" になる**のが特徴で、
# ファイル自体は存在しディスクにも余裕がある（実測）。リアルタイムスキャン等に
# よる一過性の read 失敗と考えられ、アプリ側では根治できない。
#
# 放置すると表示依存テストが **黙って skip** される＝「緑に見えて実は GUI 配線を
# 1つも検証していない」状態になり、本プロジェクトが最も警戒する失効ゲートそのもの
# （[[feedback-promote-recurring-checks]]）。一過性なら再試行で通るので、skip へ
# 倒す前に数回やり直す。それでも駄目なら従来どおり skip。

_TK_INIT_ATTEMPTS = 5

# 🔴 **skip は「表示が無い環境」でだけ正当**（＝ヘッドレス CI）。開発機で 5 回とも
# 失敗したのなら、それは**一過性の失敗が収まらなかった**という結果であって、
# 「この環境では検査しなくてよい」ではない。区別が付かないまま skip へ倒すと、
# **大量 skip・終了コード 0 の「緑」**が出る（2026-08-07 に実測＝112 本中 106 本が
# skip。しかも変異検証をその実行で回してしまい、壊した実装が「緑」と出た）。
#
# ⇒ **環境を宣言させる**（`RADIOSIM_PYTHON` と同じ流儀・B-041）。CI は
# `RADIOSIM_HEADLESS=1` を宣言しているので従来どおり skip、宣言の無い環境では
# **fail** させて緑に化けないようにする。
_HEADLESS_DECLARED = bool(os.environ.get("RADIOSIM_HEADLESS"))


def _no_display(reason: str):
    """表示が使えないときの終わり方（宣言された環境だけ skip・他は fail）。"""
    if _HEADLESS_DECLARED:
        pytest.skip(reason)
    pytest.fail(
        f"{reason}\n"
        "＝表示があるはずの環境で Tk を起こせなかった。ヘッドレスで回すなら "
        "`RADIOSIM_HEADLESS=1` を宣言すること（宣言の無い環境で skip へ倒すと、"
        "GUI 配線を 1 つも検査しないまま『緑』になる）。",
        pytrace=False,
    )


def make_tk_root(pytest_module=None):
    """tkinter のルートを生成する。間欠的な初期化失敗は数回リトライする。

    全て失敗したときだけ skip する（ヘッドレス CI もここに落ちる）。
    """
    import tkinter as tk

    last = None
    for _ in range(_TK_INIT_ATTEMPTS):
        try:
            return tk.Tk()
        except tk.TclError as e:
            last = e
            time.sleep(0.05)
    _no_display(f"no display available ({_TK_INIT_ATTEMPTS} 回試行): {last}")


def make_themed_root(theme_name: str = "dark"):
    """テーマとアプリのフォント設定まで済ませた Tk ルートを返す。

    **窓の寸法を検証するテストは必ずこちらを使う**。素の Tk 既定フォントは実機
    （sv_ttk の本文フォント）より小さく、そのまま測ると**実物より狭い前提**で
    ゲートが緑になる。実際 2.5b2 で、条件探索の必要幅 947px を「テーマ無しなら
    900px 未満」と測ってしまい、右端の条件列が見切れる実装を通した。
    """
    from views import theme as views_theme

    root = make_tk_root()
    set_theme(theme_name)
    views_theme.apply_fonts(root)
    return root


def set_theme(name: str) -> None:
    """sv_ttk のテーマを適用する。`sv.tcl` の間欠的な読み込み失敗を再試行する。

    上の `make_tk_root` と同じ一過性の read 失敗（"couldn't read file ...: No error"）が
    テーマ tcl の source でも起きる。ここで諦めると配色テストが落ちるので、
    Tk 初期化と同じくリトライで吸収する。
    """
    import tkinter as tk

    import sv_ttk

    last = None
    for _ in range(_TK_INIT_ATTEMPTS):
        try:
            sv_ttk.set_theme(name)
            return
        except tk.TclError as e:
            last = e
            time.sleep(0.05)
    _no_display(f"sv_ttk テーマを読み込めない（{_TK_INIT_ATTEMPTS} 回試行）: {last}")
