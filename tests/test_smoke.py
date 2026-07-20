"""
tests/test_smoke.py
===================
最小の GUI スモークテスト。

目的: import 時に壊れる類の回帰（シンボル改名・循環import・トップレベル副作用の
破綻）を、機能テストより手前で早期検出する。重い E2E ではなく「全モジュールが
import でき、tkinter のルートが生成できる」ことだけを確認する。

ヘッドレス CI（ディスプレイなし）では `tk.Tk()` が TclError になるため、その
ケースは skip する（import 検査は CI でも実行＝価値の中心はそちら）。
"""

import importlib
import subprocess

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ヘッドレスでも import 可能なモジュール（tk.Tk() を作らない限り tkinter import は安全）。
_HEADLESS_SAFE = [
    "main", "models", "simulation", "config", "dem",
    "batch", "report", "report_map", "map_graphics",
    "coords", "i18n", "mpl_fonts", "version",
    "views.launcher", "views.batch_builder",
    "views.map_window", "views.dialogs",
]

# import 時に matplotlib の TkAgg バックエンドをロードするためディスプレイを要する。
# ヘッドレス CI では backend ロードに失敗するので skip する（views は CI では
# pyright の静的検査でカバー）。
_DISPLAY_REQUIRED = ["views.graph"]


@pytest.mark.parametrize("mod", _HEADLESS_SAFE)
def test_module_imports(mod):
    """各モジュールが例外なく import できること（壊れた import の早期検出）。"""
    importlib.import_module(mod)


@pytest.mark.parametrize("mod", _DISPLAY_REQUIRED)
def test_gui_module_imports(mod):
    """ディスプレイ必須の GUI モジュールの import（ヘッドレスは backend 失敗で skip）。"""
    try:
        importlib.import_module(mod)
    except Exception as e:  # noqa: BLE001  backend 起因のみ skip・他は再送出
        msg = str(e).lower()
        backend_markers = ("tkagg", "interactive framework", "backend", "headless")
        if any(k in msg for k in backend_markers):
            pytest.skip(f"requires display backend: {e}")
        raise  # 真の import 回帰（モジュール名違い等）は失敗させる


# Web/PWA 再利用の生命線＝コア（ヘッドレス層）が GUI 非依存であること。
# この継ぎ目は従来「規約」でしか守られていなかったので、Tier-0 ゲートに昇格する。
# 本テストプロセス自体は上のスモークで views/tkinter を import 済みのため、
# 素の子プロセスで検証する（テスト実行順に依存しない）。
_HEADLESS_CORE = [
    "models", "simulation", "config", "dem", "batch", "report",
    "report_map", "map_graphics", "coords", "i18n", "mpl_fonts", "version",
]


def test_core_imports_do_not_pull_tkinter():
    """コア import 後の sys.modules に tkinter が居ないこと（GUI 混入の即検出）。"""
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


def test_tk_root_constructs():
    """tkinter のルートウィンドウが生成・破棄できること。

    ディスプレイのない環境（ヘッドレス CI）では skip する。
    """
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        root.withdraw()
    finally:
        root.destroy()


def test_report_meta_flows_from_launcher():
    """レポートの案件名・メモがランチャー（source of truth）→バッチへ伝播すること。

    「ランチャー＝source of truth／シングル・バッチはそこから踏襲」の配線が黙って
    壊れるのを検出する回帰ガード（feedback-design-philosophy ⑦）。ディスプレイの
    ない環境では skip する。
    """
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        root.withdraw()
        from views.launcher import SimLauncher
        app = SimLauncher(root, lambda _t: None)
        app._project_var.set("Proj-A")
        app._memo_var.set("memo-A")
        # バッチはランチャーのスナップショットを引き継ぐ
        bw = app.ensure_batch_window()
        assert bw._project_name_var.get() == "Proj-A"
        assert bw._memo_var.get() == "memo-A"
        # ランチャー変更 → ↻更新でバッチへ反映
        app._project_var.set("Proj-B")
        bw._refresh_common_from_launcher()
        assert bw._project_name_var.get() == "Proj-B"
    finally:
        root.destroy()


# ============================================================
# ネットワーク遮断ゲートの自己検査
# ============================================================
# conftest の _block_network が「効かなくなったこと」に気づけるようにする。
# ゲートは沈黙して失効しうる（テストは緑のまま外部 API を叩き始める）ため、
# ゲート自身にもガードを付ける。詳細な経緯は conftest.py の同節を参照。

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


def test_progress_poll_does_not_overwrite_completion_state():
    """完了表示が積み残しの進捗で上書きされないこと（2.4b2 実機βの回帰ガード）。

    停止した時点で after(50) 済みのポーリングが 1 回残るため、停止後に
    ポーリングが走っても描画してはいけない。実機では描画完了後もラベルが
    「地形データ取得中… 100%」のまま残った。

    不変条件そのものは ProgressPump 側（tests/test_progress.py）で
    フェイクを使って検証している。ここは**実物のランチャーに正しく配線
    されているか**を実 Tk で確認する（2.4b3 で進捗トランスポートを
    ProgressPump へ一本化した）。
    """
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        root.withdraw()
        from views.launcher import SimLauncher
        app = SimLauncher(root, lambda _t: None)

        # 取得中の状態を作り、進捗を積んでから停止する。
        app._pump.start()
        app._progress_push(200, "地形データ取得中… 100%")
        app._progress_stop()
        app.prog_label.config(text="準備完了")
        app.prog_bar.config(value=0)

        # 停止後に残存ポーリングが 1 回発火しても表示は変わらない。
        app._pump._poll()
        assert app.prog_label.cget("text") == "準備完了"
        assert float(app.prog_bar.cget("value")) == 0.0

        # ワーカースレッドは停止後にも進捗を push しうる（取得完了の通知と
        # 最後のサンプルの push は競合する）。その分も描画してはいけない
        # ＝キュー破棄だけでなくポーリング側の早期 return が要る。
        app._progress_push(200, "地形データ取得中… 100%")
        app._pump._poll()
        assert app.prog_label.cget("text") == "準備完了"
        assert float(app.prog_bar.cget("value")) == 0.0
    finally:
        root.destroy()


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
_SKIP_DIRS = {".venv", "build", "dist", "tests", "tools", "__pycache__",
              ".git", "results", "terrain_cache", "basemap_pale", "beta_evidence"}


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


# ============================================================
# 進捗トランスポートの配線（2.4b3）
# ============================================================
# 不変条件そのものは tests/test_progress.py がフェイクで検証する。ここは
# 「単一とバッチが同じ部品を、同じライフサイクルで使っているか」＝配線を見る。
# B-006 の停止バグは実装の中身ではなくライフサイクルの非対称から生まれた。


def test_single_and_batch_share_the_progress_transport():
    """単一・バッチとも ProgressPump を使い、実行中だけ回すこと。

    従来バッチのポーラは __init__ で起動して永久に回り、単一は実行ごとに
    起動・停止していた。この非対称が B-006 の停止バグを生んだので、
    「生成時は止まっている」ことを両者で固定する。
    """
    import tkinter as tk

    from views.progress import ProgressPump

    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        root.withdraw()
        from views.launcher import SimLauncher

        app = SimLauncher(root, lambda _t: None)
        assert isinstance(app._pump, ProgressPump)
        assert not app._pump.is_running, "ランチャーが実行前からポーリングしている"

        win = app.ensure_batch_window()
        try:
            assert isinstance(win._pump, ProgressPump)
            assert not win._pump.is_running, \
                "バッチが生成時からポーリングしている（B-006 のライフサイクル非対称）"

            # 閉じたらポーリングは止まる（破棄済みウィジェットへ after しない）。
            win._pump.start()
            win._on_close_window()
            assert not win._pump.is_running, "閉じてもポーリングが残っている"
        finally:
            try:
                win.destroy()
            except tk.TclError:
                pass          # _on_close_window で破棄済み
    finally:
        root.destroy()


def test_launcher_window_fits_its_content():
    """ランチャーの全ウィジェットがウィンドウ内に収まること。

    ウィンドウは resizable(False, False) の固定サイズなので、高さが足りないと
    最下段のウィジェットが黙って切り落とされ、ユーザーはリサイズで回避すら
    できない。実際 2.4 で「案件情報」グループを足したとき必要高さが 931px に
    なり、900px 固定のままだったため「マップウィンドウ」ボタンが丸ごと
    見えなくなっていた（ユーザー報告・2026-07-20）。

    入力欄を1グループ足すだけで再発する類なので、注意書きではなくゲートで守る。

    ⚠️ 検証するのは**選ばれた高さ**（_window_height）であって実現後のサイズでは
    ない。ウィンドウが未表示のあいだ `geometry()` は設定値ではなく自然サイズを
    返すため、それと比べるテストは壊れた実装でも緑になる（実際に一度そう書いた）。
    """
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        root.withdraw()
        from views.launcher import SimLauncher
        app = SimLauncher(root, lambda _t: None)
        root.update_idletasks()

        needed = root.winfo_reqheight()
        assert app._window_height >= needed, (
            f"ランチャーの中身がウィンドウに収まっていない（必要 {needed}px / "
            f"ウィンドウ {app._window_height}px）。下端のウィジェットが切れる。"
        )
    finally:
        root.destroy()
