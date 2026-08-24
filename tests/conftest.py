"""
tests/conftest.py
=================
共有フィクスチャと、テストを実行環境から切り離すゲート群
（外部ネットワーク・モーダルダイアログ・**開発機の設定と書き込み先**）。
"""

import datetime
import gc
import json
import logging
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time
import typing

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
# ⚠️ **判定の規則は製品側（`core/runtime_env.py`）が持つ**＝起動時にも同じ問いを
# するようになったので（B-056）、正規化を 2 か所に書かない。ここに残るのは
# 「テストのときは**止める**」という*ふるまい*だけ（起動側は警告のみ）。
from core import runtime_env  # noqa: E402  （sys.path 追加の後に import する）


def pytest_configure(config):
    """収集の前に走る唯一の口＝環境の宣言を検査し、書き込み先を隔離する。

    ⚠️ **隔離は検査の後・テストモジュールの import より前**。ここより後ろに置くと、
    import 時に実パスを掴むテストモジュールが出る。
    """
    _require_declared_interpreter()
    _isolate_app_paths()


def _require_declared_interpreter():
    declared = runtime_env.declared_interpreter()
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
    if runtime_env.same_interpreter(declared, sys.executable):
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

#: **その環境では原理的に走らない**と宣言された skip の印。
#
# 🔴 **予算はこれを数えない**（2026-08-12・B-074(b) の先取り）。予算が問うのは
# 「**この実行は何か検査したか**」で、事故（112 本中 106 本＝環境が壊れて全滅）を
# 捕まえるための網。ところが CI には**宣言済みの構造的 skip が大量にある**——
# 表示が無い（`RADIOSIM_HEADLESS=1`）と、git-ignore の道具（`.claude/` `tools/`）。
# これらを同じ分母で数えると、**割合が「実行の健全さ」ではなく「CI に無いものの
# 多さ」を測る**ことになる。
#
# ⚠️ **実際に破綻した**＝2026-08-11 の CI が **385/1537 = 25.05%** で赤くなった。
# 中身は正常（1152 本が実行され通っている）で、増えたのは**CI で走らないモジュール
# へのテスト 6 本**。⇒ **テストを足すほど赤に近づく網**になっていた（正当な作業を
# 罰する＝[[feedback-promote-recurring-checks]] の「毎回鳴る」壊れ方の一歩手前）。
# ⛔ **閾値を上げて逃げない**＝それは網を緩めるだけで、次にテストを足せばまた同じ
# 場所に来る。**数え方のほうが間違っている。**
#
# ⚠️ **印を付ける先は「宣言された skip」だけ**＝理由の分からない skip は従来どおり
# 数える（それこそが事故の形）。
STRUCTURAL_SKIP = "[環境に無い]"


def structural_skip(reason: str) -> str:
    """「この環境では原理的に走らない」と宣言する skip 理由を作る（→ `STRUCTURAL_SKIP`）。"""
    return f"{STRUCTURAL_SKIP} {reason}"


def _unexpected_skips(reporter) -> int:
    """宣言されていない skip の本数（予算が見るのはこれだけ）。"""
    return sum(1 for r in reporter.stats.get("skipped", [])
               if STRUCTURAL_SKIP not in str(getattr(r, "longrepr", "")))


# ============================================================
# 構造的に覆っていない面の報告と、表示のある機械の刻印（B-074(b)）
# ============================================================
# 🔴 **「走らなかった面がある」ことを、誰も報告していなかった。**
# 上の予算が答えるのは「**この実行は何か検査したか**」で、B-074(b) が問うのは
# 「**どの面を CI が構造的に一度も覆っていないか**」＝別の問い。実際 2.7 の目玉で
# あるスケール追従のゲートは、CI で skip され続けたまま**開発機で赤**だった
# （2026-08-11 に偶然フルスイートを回すまで、赤が存在しないのと同じだった）。
#
# ⛔ **「CI で GUI を走らせる」は解にならない**＝CI は ubuntu-latest で、
# tests/test_window_fit.py が assert するのは **Windows のフォント実測ピクセル**。
# xvfb を足しても数字が別物になるだけで、同じ検査にはならない。
# ⇒ **求めるのは網羅ではなく可視性**＝走っていない面が CI の側から見え、
# **誰がいつ回すか**が決まっていること（[[feedback-promote-recurring-checks]]
# ＝注意書きを足すのではなく引き金へ昇格させる）。
#
# 対で 2 つ置く:
#   ①**報告**（どの環境でも）＝構造的 skip をファイル単位で数えて出す。CI では
#     さらに GITHUB_STEP_SUMMARY へ書き、**実行ページを開けば読める**ようにする。
#   ②**刻印**（表示のある機械だけ）＝フルスイートが緑で終わったとき、その commit を
#     `.qa/display_run.json` へ残す。release-check がこれを読み、HEAD と違えば
#     「表示依存の面は回っていない」と声に出す。⚠️ **チェックリストの一行では
#     足りない**＝読み飛ばしても何も残らない（それが 2026-08-11 の形）。
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: 表示のある機械でフルスイートが通った事実の刻印（`.qa/` は git-ignore 済み）。
DISPLAY_RUN_STAMP = _REPO_ROOT / ".qa" / "display_run.json"


def _structural_skips_by_file(reporter) -> dict[str, int]:
    """宣言済み skip を**テストファイル単位**で数える（＝覆っていない面の内訳）。"""
    faces: dict[str, int] = {}
    for r in reporter.stats.get("skipped", []):
        if STRUCTURAL_SKIP not in str(getattr(r, "longrepr", "")):
            continue
        face = str(getattr(r, "nodeid", "?")).split("::")[0]
        faces[face] = faces.get(face, 0) + 1
    return faces


def _write_step_summary(faces: dict[str, int], total: int) -> None:
    """CI の実行ページに内訳を出す（GitHub Actions のときだけ）。

    ⚠️ ここで失敗してもテストは落とさない＝**報告のための道具が、報告される中身を
    壊してはいけない**。書けなければ端末の字だけが残る。
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    ran = total - sum(faces.values())
    lines = [
        "### この実行が構造的に覆っていない面",
        "",
        f"実行 {ran} 本 / 収集 {total} 本"
        f"（**{sum(faces.values())} 本はこの環境では原理的に走らない**）",
        "",
        "| テストファイル | 走らなかった本数 |",
        "| --- | ---: |",
    ]
    lines += [f"| `{f}` | {n} |" for f, n in sorted(faces.items(), key=lambda kv: -kv[1])]
    lines += [
        "",
        "⚠️ **これは緑の一部ではない**＝上の面は、表示のある機械で誰かが回すまで"
        "結果が存在しない。回した事実は `.qa/display_run.json` に刻まれる。",
    ]
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


def _is_whole_suite(config) -> bool:
    """スイート全体を回したか（＝刻印してよいか）。

    🔴 **予算の `_SKIP_BUDGET_MIN_TESTS` を流用してはいけない。** あれは「部分実行を
    見ない」ための floor であって、**全体を回した証明ではない**。実測 2026-08-12＝
    `tests/test_window_fit.py` 1 ファイルだけで 100 本＝floor を超える。⇒ その 1 本
    を回しただけで「表示のある機械で全部通った」と刻んでしまう＝**刻印が嘘をつく**
    （[[feedback-promote-recurring-checks]] の「間違ったものを要求するゲート」）。
    ⇒ **絞り込みが宣言されていないこと**を条件にする（選び方の側を見る）。
    """
    o = getattr(config, "option", None)
    params = getattr(config, "invocation_params", None)
    if o is None or params is None:
        # 実際の pytest では必ずどちらも在る。分からないときに**刻まない**側へ倒すのは、
        # 嘘の刻印（回っていないのに「回った」）のほうが、刻印が無いことより悪いため。
        return False
    positional = [a for a in params.args if not a.startswith("-")]
    return not positional and not (
        getattr(o, "keyword", "") or getattr(o, "markexpr", "")
        or getattr(o, "deselect", None) or getattr(o, "lf", False)
        or getattr(o, "failedfirst", False)
    )


def _stamp_display_run(total: int, faces: dict[str, int]) -> None:
    """表示のある機械でフルスイートが通ったことを刻む（→ `DISPLAY_RUN_STAMP`）。"""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=10, check=False,
        )
        commit = head.stdout.strip() if head.returncode == 0 else ""
        DISPLAY_RUN_STAMP.parent.mkdir(parents=True, exist_ok=True)
        DISPLAY_RUN_STAMP.write_text(json.dumps({
            "commit"    : commit,
            "when"      : datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "collected" : total,
            "ran"       : total - sum(faces.values()),
            # 表示のある機械でも git-ignore の道具（.claude/ tools/）は無い場合がある
            # ＝**0 を要求しない**。何が残ったかを書いて、読む側に判断させる。
            "structural": faces,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, subprocess.SubprocessError):
        pass        # 刻めなくてもテストの結果は変えない（release-check が「無い」と言う）


def pytest_sessionfinish(session, exitstatus):
    """大半が skip なら失敗させ、覆っていない面を報告し、表示のある実行を刻む。"""
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    total = session.testscollected
    full_run = reporter is not None and total >= _SKIP_BUDGET_MIN_TESTS

    # ①報告と②刻印＝**部分実行では何も言わない**（`-k` の 1 ファイルを「覆っていない
    # 面」と呼ぶと、毎回鳴る網になる＝[[feedback-promote-recurring-checks]]）。
    if full_run:
        faces = _structural_skips_by_file(reporter)
        if faces:
            reporter.write_sep(
                "-",
                f"この実行が構造的に覆っていない面: {sum(faces.values())}/{total} 本"
                f"（{len(faces)} ファイル）"
                + "".join(f"\n    {f}  {n} 本"
                          for f, n in sorted(faces.items(), key=lambda kv: -kv[1])),
                yellow=True,
            )
            _write_step_summary(faces, total)
        if exitstatus == 0 and not _HEADLESS_DECLARED and _is_whole_suite(session.config):
            _stamp_display_run(total, faces)

    if exitstatus != 0:
        return                      # 既に赤＝上書きしない
    if not full_run:
        return
    skipped = _unexpected_skips(reporter)
    if skipped <= total * _SKIP_BUDGET_RATIO:
        return
    declared = len(reporter.stats.get("skipped", [])) - skipped
    reporter.write_sep(
        "=",
        f"**宣言の無い** skip が多すぎます（{skipped}/{total} 本"
        f"／別に宣言済みが {declared} 本＝これは数えていない）。"
        f"上限は {_SKIP_BUDGET_RATIO:.0%}＝この実行はテストとして成立していません。"
        "（GUI の skip なら表示環境を疑うこと。ヘッドレスで回すなら "
        "RADIOSIM_HEADLESS=1 を宣言する。その環境では原理的に走らないものは "
        "conftest.structural_skip() で宣言する）",
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


def _no_display(reason: str) -> "typing.NoReturn":
    """表示が使えないときの終わり方（宣言された環境だけ skip・他は fail）。

    ⚠️ **`NoReturn` の注釈は飾りではない**＝これが無いと `make_tk_root` の戻り値が
    `Tk | None` に見え、**呼び出し側全部で `root.destroy()` が型エラーになる**。
    """
    if _HEADLESS_DECLARED:
        # 宣言された環境の skip＝**構造的**（→ `STRUCTURAL_SKIP`）。予算はこれを
        # 数えない＝「表示が無い」ことは実行の不健全さではないため。
        pytest.skip(structural_skip(reason))
    pytest.fail(
        f"{reason}\n"
        "＝表示があるはずの環境で Tk を起こせなかった。ヘッドレスで回すなら "
        "`RADIOSIM_HEADLESS=1` を宣言すること（宣言の無い環境で skip へ倒すと、"
        "GUI 配線を 1 つも検査しないまま『緑』になる）。",
        pytrace=False,
    )


# ============================================================
# Tk オブジェクトを次のテストへ持ち越さない（I-019 の根治・2.7a2）
# ============================================================
# 🔴 **`destroy()` は Tk オブジェクトを消さない。** tkinter は親子で相互参照する
# ので、テストが `win.destroy(); root.destroy()` まで正しく書いても、Python 側の
# オブジェクトは**循環ゴミとして生き残る**（実測 2026-08-07・test_multihop.py＝
# 1 テストあたり 41〜97 個。内訳の例＝`Tk` 1・`StringVar` 26・`Frame` 26・
# `PhotoImage` 1。`gc.collect()` を明示的に回すと全部消える＝GC 待ちだった）。
#
# 残ったゴミは「いつか誰かの GC」が拾う。**その誰かが製品のワーカースレッドだと、
# 壊れる**:
#
#   ① `tkinter.Variable.__del__` は無条件に `self._tk.call("info", "exists", …)`
#      を呼ぶ。CPython の `_tkinter` は**メインスレッド以外からの呼び出しを
#      100ms×10 回待ってから** RuntimeError にする（実測 **1.036 秒/回**）。
#      ⇒ StringVar 20 個をワーカーで回収させると **21.2 秒**（実測）。
#      これが「レポート生成が 30 秒で終わらない」の正体で、`savefig` は無実。
#   ② ゴミに `Tk` 本体（tkapp）が含まれると、Tcl インタプリタの解放が誤った
#      スレッドで走り **`Tcl_AsyncDelete: async handler deleted by the wrong
#      thread`** で **プロセスごと落ちる**（実測＝exit 3）。これが I-019 の見出しの
#      `Current thread's C stack trace` そのもの。
#
# 実スイートで捕まえた証拠（2026-08-07・`-p no:randomly` の全体実行）＝落ちた
# スレッドのスタックの先頭が **`Garbage-collecting`**（`Thread-374 (_work)` の中）。
#
# ⇒ **メインスレッドで、毎テスト、確定的に回収する。** ここで回収する限り
#    `__del__` の Tcl 呼び出しはメインスレッドで一瞬に済み、ワーカーには何も残らない。
#    列挙（「GUI テストにだけ付ける」）にしないのは、**新しい GUI テストを書いた人が
#    忘れた瞬間に再発する**ため（→ [[feedback-promote-recurring-checks]]）。
#    コストは実測 `gc.collect()` 1 回 8.9ms ＝全 1300 本で約 12 秒。
# ============================================================
# 表示言語がテストをまたいで漏れない（2.7a2 で昇格）
# ============================================================
# `i18n` の言語は**プロセス共有のグローバル**で、GUI を組むテストは軒並み
# `set_lang("ja")` する（実測＝tests 配下に 99 か所）。戻さないと、後続の
# `test_config` が英語メッセージを assert して落ちる。
#
# 🔴 **注意書きでは守れないことが実証された**＝この規則は `tests/test_batch.py` に
# 「戻さないと後続の test_config が落ちる」と**はっきり書いてあった**のに、同じ
# ファイルへ新しいクラスを足したときに写し忘れ、**実際に 3 件落とした**
# （2026-08-07・B-050 の作業中）。99 か所が設定して 2 か所しか戻していない状態は、
# 定義上「思い出す規則」（→ [[feedback-promote-recurring-checks]]）。
# ⇒ **既定で戻す**。クラスごとの `_restore_lang` は残しても害はない（二重に戻る
#    だけ）が、新しいテストはもう書かなくてよい。
@pytest.fixture(autouse=True)
def _language_never_leaks():
    """テストが変えた表示言語を、そのテストの中に閉じ込める。"""
    from core import i18n

    before = i18n._lang
    yield
    if i18n._lang != before:
        i18n.set_lang(before)


@pytest.fixture(autouse=True)
def _taskbar_is_never_the_dev_machines():
    """**この機械のタスクバーを測定に混ぜない**（2026-08-18・B-084）。

    `window_fit.usable_area()` は OS の作業領域（`rcWork`）から上限を決めるので、
    既定のままだと**走らせた機械のタスクバーの高さ**が窓の寸法に入る。すると
    寸法ゲートの数字が機械ごとに変わり、[[project-real-world-env-vdi]] で 6 回
    踏んだ「開発機で緑・実機で壊れる」を**測り方の側から作り直す**ことになる
    （とくに開発機がたまたま FHD なら、`screen_size` を FHD へ差し替えている
    既存ゲート 20 本以上の期待値が黙って動く）。

    ⇒ **既定は「OS に何も聞けない」**＝`SCREEN_MARGIN` による従来の見積り。
    作業領域そのものを見たいゲートは、`window_fit.work_areas` を明示的に
    差し替えて**偽のタスクバー**を注入する（tests/test_window_fit.py）。
    """
    from views import window_fit

    real = window_fit.work_areas
    window_fit.work_areas = lambda: {}          # type: ignore[assignment]
    try:
        yield
    finally:
        window_fit.work_areas = real            # type: ignore[assignment]


# ============================================================
# 🔴 この機械そのものを測定に混ぜない（2026-08-24・I-108）
# ============================================================
# 🔑 **前提は主張ではない。** 寸法ゲートには「開いた直後はまだ溢れていない」の
# ような**前提の行**があり、それは*走らせた機械の画面がその窓を持てるとき*にだけ
# 成り立つ。2026-08-23 に実際に破れた＝表示スケール 150% の機械では条件探索の
# 要求 `need=(843, 989)` に対し Tk が見る画面が **1707x960**（150% で縮む）しか
# なく、装飾を引いた 921 で頭打ちする ⇒ **製品ではなく実行環境が QA ゲートを
# 赤にした**（I-108・ブロッキングのフックが 150% のときだけ落ちた）。
#
# 上の `_taskbar_is_never_the_dev_machines` は同じ話の**タスクバーの口だけ**を
# 塞いだものだった。機械が測定へ入り込む口は実測で 3 つある:
#   ① 作業領域（タスクバーの実寸）… B-084 で塞いだ（上の fixture）
#   ② **画面の大きさ**（`window_fit.screen_size`）… 表示スケールで縮む
#   ③ **字が従う DPI**（`views.theme.window_dpi`）… `apply_fonts` の既定値で、
#      これが動くと**必要量そのもの**（フォントの実測ピクセル）が動く
# ⇒ ②③も**基準機（開発機 WQHD・100% ＝ 使える高さ 1350px）へ固定**する。
#
# ⚠️ **応急の手当て（前提を skip にする）では覆いが消える向きに壊れた**＝画面が
# 狭い機械ほど検査が走らなくなり、[[project-real-world-env-vdi]]（実機は開発機
# より 360px 低い）＝*いちばん壊れやすい機械でいちばん検査が薄い*。固定なら
# **どの機械でも同じ本数が同じ数字で走る**。
# ⚠️ 出荷先の条件（FHD・125%/150%）を見たいゲートは**従来どおり自分で差し替える**
# （`_ship_on_fhd` 等・あとから当てた patch が勝つ）。ここは「何も指定しない
# テストが黙って機械を読む」ことだけを止める。
# ⚠️ **本物の機械を見たいテストは `@pytest.mark.real_machine`**（Tk と Win32 の
# 座標系の突き合わせ）。数を増やさないこと＝付けたぶんだけ「この機械でしか意味の
# ない検査」が戻る。台帳＝tests/test_window_fit.py の `_REAL_MACHINE_TESTS`。

#: 測定の基準機＝開発機（WQHD・100%）。ここを動かすと寸法ゲートの数字が全部動く。
REFERENCE_SCREEN = (2560, 1440)
REFERENCE_DPI = 96


def _pinned_screen(_win) -> "tuple[int, int]":
    """基準機の画面（`window_fit.screen_size` の差し替え）。"""
    return REFERENCE_SCREEN


def _pinned_dpi(win) -> int:
    """基準機の DPI（`views.theme.window_dpi` の差し替え）。

    ⚠️ **答えだけを固定し、途中の副作用は本物のまま残す**（2026-08-24 に実測で
    踏んだ）＝本物は `winfo_toplevel().winfo_id()` で Win32 に窓のハンドルを聞く。
    この呼び出しは**その窓を実体化させる**ので、省くと Tk が版組みを確定させる
    *時点*がずれ、まだ表示していない窓の最初の測定が **28px 太い**まま残った
    （`grow_only` が floor に拾い、以後 3 本のゲートが赤）。
    ⇒ 差し替えるのは「モニタが何と言ったか」だけにする。
    """
    try:
        win.winfo_toplevel().winfo_id()
    except Exception:
        pass
    return REFERENCE_DPI


# 固定が**効いているか**をゲートから見分けるための印（値の一致だけで見ると、
# たまたま基準機と同じ機械では固定を外しても緑になる＝壊れ方①）。
_pinned_screen.is_the_pin = True                # type: ignore[attr-defined]
_pinned_dpi.is_the_pin = True                   # type: ignore[attr-defined]


#: 本物の口（最初のテストの setup で 1 度だけ捕まえる＝まだ誰も差し替えていない）。
_REAL_DOORS: "dict[str, object]" = {}


def _real_doors() -> "dict[str, object]":
    from views import theme, window_fit

    if not _REAL_DOORS:
        _REAL_DOORS["screen"] = window_fit.screen_size
        _REAL_DOORS["dpi"] = theme.window_dpi
    return _REAL_DOORS


@pytest.fixture(autouse=True)
def _the_machine_is_never_the_dev_machines(request):
    """**画面の大きさと DPI を基準機へ固定する**（2026-08-24・I-108）。

    🔴 **後始末では直せない**（実測で踏んだ）＝`monkeypatch` は先に立ち上がる
    autouse fixture（`dialog_calls`）が要求しているので、**その undo はここの
    teardown より後に走る**。`monkeypatch.setattr(window_fit, "screen_size", …)`
    を使う既存ゲートは 20 本以上あり、そこで捕まる「元の値」は*この固定*なので、
    teardown で本物へ戻しても**直後に固定が入れ直される**＝以後ずっと漏れる。
    実害＝`real_machine` のテストが**固定を見たまま緑**になっていた（壊れ方①）。
    ⇒ **後始末に頼らず、毎テストの setup で入れ直す**（本物は最初に捕まえた
    ものを使う）。次のテストが必ず正しい状態から始まるので、間の漏れは効かない。
    """
    from views import theme, window_fit

    real = _real_doors()
    want_real = "real_machine" in request.keywords
    window_fit.screen_size = (                  # type: ignore[assignment]
        real["screen"] if want_real else _pinned_screen)
    theme.window_dpi = (                        # type: ignore[assignment]
        real["dpi"] if want_real else _pinned_dpi)
    yield


# 表示環境の監視（`views.theme.watch_display`）は「静けさ」を待つ設計なので、
# テストは**実時間**を待たされる。⇒ 待ち時間の定数を**同じ比のまま** 1/25 に縮める。
# 🔴 **速さに賭けるのとは違う**（B-082 の教訓は保つ）＝待ち方は `pump_until`
# （条件で待つ）のままで、縮めるのは「アプリが自分に課している間隔」だけ。
# 実測＝これだけで test_theme が 16.2 → 8.4 秒（2026-08-23）。
FAST_DISPLAY_MS = {
    "_DISPLAY_DEBOUNCE_MS"    : 10,    # 実物 250
    "_DISPLAY_LANDING_MS"     : 10,    # 実物 250
    "_DISPLAY_DEBOUNCE_MAX_MS": 30,    # 実物 750
    "_DISPLAY_SETTLE_MS"      : 32,    # 実物 800
}


@pytest.fixture(autouse=True)
def _fast_display_constants(request, monkeypatch):
    """既定で待ち時間を縮める。**実物の値で通す経路は `real_debounce` で残す。**

    ⚠️ **既定を「速い方」にするのが要点**＝監視のテストを新しく書く人が「速くする
    指定」を思い出す必要が無い（[[feedback-promote-recurring-checks]]＝思い出す
    規則にしない）。逆に、**出荷する値そのもので通す 1 本**が明示的に手を挙げる
    （`@pytest.mark.real_debounce`）。その 1 本が無いと「縮めた値でしか通らない
    実装」が素通りする。
    """
    if "real_debounce" in request.keywords:
        return                                     # 出荷する値そのもので走る
    from views import theme as _theme
    for name, value in FAST_DISPLAY_MS.items():
        monkeypatch.setattr(_theme, name, value)


@pytest.fixture(autouse=True)
def _tk_garbage_never_escapes():
    """テストが残した循環ゴミを、**メインスレッドで**片付けてから次へ進む。"""
    yield
    gc.collect()


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


class PoisonedInterpreter(Exception):
    """テーマ tcl が**途中まで**入った Tk インタプリタ（B-082②）。

    直し方は「作り直す」しか無いので、**一過性の read 失敗とは別の例外**にする
    （同じ `TclError` のままだと、呼ぶ側が待って直そうとしてしまう）。
    """


def make_themed_root(theme_name: str = "dark"):
    """テーマとアプリのフォント設定まで済ませた Tk ルートを返す。

    **窓の寸法を検証するテストは必ずこちらを使う**。素の Tk 既定フォントは実機
    （sv_ttk の本文フォント）より小さく、そのまま測ると**実物より狭い前提**で
    ゲートが緑になる。実際 2.5b2 で、条件探索の必要幅 947px を「テーマ無しなら
    900px 未満」と測ってしまい、右端の条件列が見切れる実装を通した。

    🔴 **汚れたインタプリタは捨てて作り直す**（B-082②）＝テーマ tcl が途中まで
    入った Tk は、何度 `set_theme` を呼んでも直らない。**新しい Tk なら Tcl
    インタプリタごと新品**なので、そこで読み直せば通る。
    """
    from views import theme as views_theme

    last = None
    for _ in range(_TK_INIT_ATTEMPTS):
        root = make_tk_root()
        try:
            set_theme(theme_name)
        except PoisonedInterpreter as e:
            last = e
            root.destroy()                          # ⚠️ インタプリタごと捨てる
            time.sleep(0.05)
            continue
        views_theme.apply_fonts(root)
        return root
    _no_display(f"sv_ttk テーマを読み込めない（作り直し {_TK_INIT_ATTEMPTS} 回）: {last}")


# 汚れたインタプリタの見分け方（B-082 の症状②）。⚠️ **この文字列は sv.tcl の
# 再 source が返す Tcl のメッセージ**（"Theme sun-valley-light already exists"）。
_THEME_ALREADY_EXISTS = "already exists"


def _interpreter_is_poisoned(err: "Exception") -> bool:
    """テーマ tcl の**途中まで**が既にこのインタプリタへ入っているか（B-082②）。

    🔴 **待っても消えない失敗**。`sv_ttk._load_theme` は「読み込み済み」の印を
    **source が最後まで通ったときだけ** Tk ルートに付ける。ところが `sv.tcl` は
    `light.tcl` → `dark.tcl` の順に source し、**`dark.tcl` の画像読み込みで
    一過性の read 失敗が起きる**と、`sun-valley-light` だけが作られた状態で
    印が付かないまま抜ける。⇒ 次の試行は `light.tcl` を頭から回し、
    `ttk::style theme create sun-valley-light` で **"already exists"** に
    ぶつかる＝**リトライは原理的に成功しない**（2026-08-14 に 5 回とも空回り）。

    ⚠️ **Tcl にテーマを消す手段は無い**ので、直し方は 1 つだけ＝
    **そのインタプリタ（＝Tk ルート）を捨てて作り直す**（`make_themed_root`）。
    """
    return _THEME_ALREADY_EXISTS in str(err)


def set_theme(name: str) -> None:
    """sv_ttk のテーマを適用する。`sv.tcl` の間欠的な読み込み失敗を再試行する。

    上の `make_tk_root` と同じ一過性の read 失敗（"couldn't read file ...: No error"）が
    テーマ tcl の source でも起きる。ここで諦めると配色テストが落ちるので、
    Tk 初期化と同じくリトライで吸収する。

    ⛔ **ただし「既にある」はリトライの対象ではない**（B-082②）＝`_interpreter_is_poisoned`
    の註のとおり、待っても消えない。**5 回空回りしてから読み込み失敗として
    落ちる**と、原因が「読めなかった」に見えて調査が丸ごと逸れるので、
    ここで**別の失敗として**打ち切る。
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
            if _interpreter_is_poisoned(e):
                raise PoisonedInterpreter(str(e)) from e
            time.sleep(0.05)
    _no_display(f"sv_ttk テーマを読み込めない（{_TK_INIT_ATTEMPTS} 回試行）: {last}")


# 表示環境の監視（`views.theme.watch_display`）を検証するテストは、**デバウンスを
# 挟んだ非同期の通知**を待つ。ここを「決め打ちの締め切りまで `mainloop` を回す」
# 形で書くと、機械の速さに結果を賭けることになる（B-082）。
_PUMP_POLL_MS = 10


def pump_until(root, done, timeout_ms: int = 5000, poll_ms: int = _PUMP_POLL_MS) -> bool:
    """**条件が立つまで** Tk のイベントを回す（時間では待たない）。

    ⚠️ **旧い書き方**＝`root.after(デバウンス + 80, root.quit)` → `root.mainloop()`。
    これは「デバウンスの消化が締め切りに間に合う」ことに賭けている。実測した余裕は
    **77ms しかなく**（B-082 の調査・単独実行 20 回）、デバウンスは `<Configure>`
    が 1 つ来るたびに**測り直される**ので、`update()` の後に窓の配置が確定して
    もう 1 つ届けば、その場で締め切りを超える。⇒ **製品は正しいのにゲートだけが
    赤くなる**（[[feedback-promote-recurring-checks]] の壊れ方②）。

    ⛔ **待ち時間を延ばして誤魔化さない。** `timeout_ms` は「壊れているものを
    赤くする」ための上限であって、**待ちの長さで結果が変わってはいけない**
    （立つ条件なら 260ms 前後で返る）。

    Args:
        root: イベントを回す Tk ウィジェット。
        done: 立ったかどうかを返す呼び出し可能。**副作用を持たせない**。
        timeout_ms: 立たなかったと諦めるまでの上限。
        poll_ms: 回す間隔。

    Returns:
        条件が立てば `True`、上限まで立たなければ `False`。⚠️ **戻り値は捨ててよい**
        ＝呼び出し側は今までどおり assert で落ちる（上限まで待った後に、同じ
        メッセージで赤くなる）。
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        root.update()                                  # 溜まったイベント（`after` 含む）を消化
        if done():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_ms / 1000)
