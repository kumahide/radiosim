"""
tests/test_smoke.py
===================
最小のスモークテスト。

目的: import 時に壊れる類の回帰（シンボル改名・循環import・トップレベル副作用の
破綻）を、機能テストより手前で早期検出する。重い E2E ではなく「全モジュールが
import でき、どの層も tkinter を引かない」ことだけを確認する
（旧画面＝Tk は 4.0 ステージ11a で撤去）。
"""

import importlib
import subprocess

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ヘッドレス層＝`core/` と `report/` の**中身そのもの**（2.7 スライス H・I-058）。
# ⚠️ **手で並べない**＝ここは 2.5a2 で一度腐った形（`report.py` を 3 分割したとき
# リストが追従せず、出力層がまるごと検査の外に出た）。層をディレクトリで表した
# ので、**そのディレクトリを読む**のが正しい。モジュールを足す・割るたびに
# ここを直す作業は消える。
def _layer_modules(pkg: str) -> list[str]:
    d = os.path.join(_LAYER_ROOT, pkg)
    return [f"{pkg}.{n[:-3]}" for n in sorted(os.listdir(d))
            if n.endswith(".py") and n != "__init__.py"]


_LAYER_ROOT = os.path.join(os.path.dirname(__file__), "..")

# import できるべきモジュール＝各層の中身そのもの。
_HEADLESS_SAFE = (
    _layer_modules("core")
    + _layer_modules("report")
    + _layer_modules("webui")
)

@pytest.mark.parametrize("mod", _HEADLESS_SAFE)
def test_module_imports(mod):
    """各モジュールが例外なく import できること（壊れた import の早期検出）。"""
    importlib.import_module(mod)


# Web/PWA 再利用の生命線＝コア（ヘッドレス層）が GUI 非依存であること。
# この継ぎ目は従来「規約」でしか守られていなかったので、Tier-0 ゲートに昇格する。
# 素の子プロセスで検証する（同じプロセスの他の試験が何を import したかに左右されない）。
# 新しい画面（`webui/`・4.0 ステージ8）も tkinter を引かない（I-144 の土台）。
_HEADLESS_CORE = _layer_modules("core") + _layer_modules("report") + _layer_modules("webui")


def test_core_imports_do_not_pull_tkinter():
    """`core/` と `report/` の import 後に tkinter が居ないこと（GUI 混入の即検出）。

    ⚠️ **対象はディレクトリの中身そのもの**（2.7 スライス H）＝新しく足した
    モジュールが自動で検査対象になる。「一覧に登録し忘れたので黙って対象外」
    という抜け道が構造的に無い。
    """
    assert _HEADLESS_CORE, "core/ report/ が空＝この検査は何も見ていない"
    code = (
        "import sys; "
        f"import {', '.join(_HEADLESS_CORE)}; "
        "bad = [m for m in sys.modules if m == 'tkinter' or m.startswith('tkinter.')]; "
        "sys.stderr.write('GUI leak into core: %r' % bad) if bad else None; "
        "sys.exit(1 if bad else 0)"
    )
    env = dict(os.environ, MPLBACKEND="Agg")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0, (
        f"コアモジュールの import が tkinter を引き込んだ: {proc.stderr or proc.stdout}"
    )


# ============================================================
# ネットワーク遮断ゲートの自己検査
# ============================================================
# conftest の _block_network が「効かなくなったこと」に気づけるようにする。
# ゲートは沈黙して失効しうる（テストは緑のまま外部 API を叩き始める）ため、
# ゲート自身にもガードを付ける。詳細な経緯は conftest.py の同セクションを参照。

def test_network_guard_blocks_external_connections():
    """外部宛の接続が NetworkAccessBlocked で止まる（実通信は発生しない）。"""
    import socket

    from conftest import NetworkAccessBlocked

    with pytest.raises(NetworkAccessBlocked):
        socket.create_connection(("cyberjapandata.gsi.go.jp", 443), timeout=1)

    s = socket.socket()
    try:
        with pytest.raises(NetworkAccessBlocked):
            s.connect(("93.184.216.34", 80))   # 外部 IP（DNS も引かない）
    finally:
        s.close()


def test_network_guard_allows_localhost():
    """localhost は遮断しない（将来のローカルサーバ系テストを巻き込まない）。

    接続の成否は問わない。遮断ゲートが誤って localhost を止めていないこと
    ＝ NetworkAccessBlocked が飛ばないことだけを確認する。
    """
    import socket

    from conftest import NetworkAccessBlocked

    s = socket.socket()
    s.settimeout(0.2)
    try:
        s.connect(("127.0.0.1", 1))   # 通常は誰も listen していない
    except NetworkAccessBlocked:
        pytest.fail("ゲートが localhost を遮断している")
    except OSError:
        pass   # 接続拒否・タイムアウトは想定内（遮断されていないことが要点）
    finally:
        s.close()


# ============================================================
# スレッド生成規約の静的ガード（Tier-0）
# ============================================================
# 「ThreadPoolExecutor は使用禁止・daemon=True の Thread を使う」は従来メモリ上の
# 規約でしかなく、コードにも痕跡が無かった。ThreadPoolExecutor のワーカーは
# daemon=False のため、ウィンドウクローズ時に tkinter が
# `RuntimeError: main thread is not in main loop` を出す。実装が規約に従っている
# 今のうちにゲート化する（[[feedback-radiosim-rules]]）。
_APP_ROOT = os.path.join(os.path.dirname(__file__), "..")

# アプリ本体のソース（tests / .venv / build 成果物は対象外）。
# 🔴 **`experiments/` も対象外**（2026-08-23）＝あそこは製品コードではなく
# （アプリのどこからも import されず・配布物にも型検査にもカバレッジにも入らない
# ＝`experiments/README.md`）、**スレッド規約はアプリの終了時の話**なので探針には
# 掛からない。⇒ ここに残しておくと「探針を 1 行直すたびに全スイートが走る」
# （QA ゲートの pytest は working tree の内容で決まるため）。**検証の速度を
# 落としているだけで、守っているものが無い。**
_SKIP_DIRS = {".venv", "build", "dist", "tests", "tools", "__pycache__",
              ".git", "results", "terrain_cache", "basemap_pale", "issue_evidence",
              "experiments"}


def _app_sources():
    for dirpath, dirnames, filenames in os.walk(_APP_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def test_thread_pool_executor_is_not_used():
    """ThreadPoolExecutor を使わないこと（ワーカーが非 daemon＝終了時に tkinter が落ちる）。"""
    import ast

    offenders = []
    for path in _app_sources():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Attribute):
                name = node.attr
            elif isinstance(node, ast.alias):
                name = node.name.rsplit(".", 1)[-1]
            elif isinstance(node, ast.Name):
                name = node.id
            if name == "ThreadPoolExecutor":
                offenders.append(f"{os.path.relpath(path, _APP_ROOT)}:{node.lineno}")
    assert not offenders, (
        "ThreadPoolExecutor は使用禁止（daemon=True の threading.Thread を使う）: "
        f"{offenders}"
    )


def test_all_threads_are_daemon():
    """threading.Thread は必ず daemon=True で生成すること。

    非 daemon スレッドが残るとウィンドウを閉じてもプロセスが終わらず、
    tkinter が破棄済みのメインループへ触れて RuntimeError を出す。
    """
    import ast

    offenders = []
    for path in _app_sources():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            target = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else None
            )
            if target != "Thread":
                continue
            daemon = next((kw.value for kw in node.keywords if kw.arg == "daemon"), None)
            if not (isinstance(daemon, ast.Constant) and daemon.value is True):
                offenders.append(f"{os.path.relpath(path, _APP_ROOT)}:{node.lineno}")
    assert not offenders, f"daemon=True でない Thread 生成: {offenders}"
