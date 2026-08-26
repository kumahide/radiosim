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
import tkinter

import pytest

from conftest import make_tk_root, make_themed_root

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

# ヘッドレスでも import 可能なモジュール（tk.Tk() を作らない限り tkinter import は安全）。
_HEADLESS_SAFE = (
    ["main"]
    + _layer_modules("core")
    + _layer_modules("report")
    + [
        "views.launcher", "views.batch_builder", "views.scenario",
        "views.map_window", "views.multihop", "views.dialogs",
    ]
)

# import 時に matplotlib の TkAgg バックエンドをロードするためディスプレイを要する。
# ヘッドレス CI では backend ロードに失敗するので skip する（views は CI では
# pyright の静的検査でカバー）。
_DISPLAY_REQUIRED = ["views.graph"]

# バッチ窓の生成に要る最小パラメータ（値そのものはレイアウト検証に無関係）。
_BATCH_PARAMS = {
    "start": "34.5429, 132.4118", "end": "34.5389, 132.4050",
    "h_tx": "30.0", "h_rx": "10.0", "freq": "2400.0", "p_tx": "20.0",
    "gain_tx": "3.0", "gain_rx": "3.0", "sens": "-85.0", "veg_h": "10.0",
    "k_factor": "10.0", "samples": "50", "diff_method": "bullington",
    "env_type": "los", "rain_rate": "0.0",
}


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
_HEADLESS_CORE = _layer_modules("core") + _layer_modules("report")


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


def test_tk_root_constructs():
    """tkinter のルートウィンドウが生成・破棄できること。

    ディスプレイのない環境（ヘッドレス CI）では skip する。
    """
    pytest.importorskip("tkinter")
    root = make_tk_root()
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
    pytest.importorskip("tkinter")
    root = make_tk_root()
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
# プロジェクト（`.rsproj`）の UI 配線（5c-2）
# ============================================================
# ヘッドレスの読み書きは tests/test_project.py が守る。ここが守るのは
# **窓 ↔ ランチャーの受け渡し**＝「開いている窓から集める／閉じている窓の節は
# 持ち越す／読み込んだ節は開いた窓へ入る」の 3 点。

def _launcher_with_windows(root):
    """ランチャー＋3 つの窓を開いた状態を作る（プロジェクト系テストの母体）。"""
    from report import batch
    from views.launcher import SimLauncher
    app = SimLauncher(root, lambda _t: None)
    app._project_var.set("案件 A")
    app._memo_var.set("メモ A")

    bw = app.ensure_batch_window()
    bw.replace_rows([
        batch.PathRow(path_id="P1", lat_tx=34.5, lon_tx=132.4,
                      lat_rx=34.6, lon_rx=132.5, h_tx=30.0, h_rx=10.0),
        batch.PathRow(path_id="P2", lat_tx=34.7, lon_tx=132.6,
                      lat_rx=34.8, lon_rx=132.7, h_tx=20.0, h_rx=5.0,
                      freq_mhz=400.0),
    ])
    app._on_open_scenario()
    app._scenario_win._mode.set("sweep")
    app._scenario_win._cmp_cols[0]["h_tx"].set("55")
    app._scenario_win._from_var.set("15")

    app._on_open_multihop()
    mw = app._multihop_win
    for vars_, coord in zip(mw._wp_vars, ("34.50, 132.40", "34.60, 132.50")):
        vars_["coord"].set(coord)
    mw._hop_vars[0]["freq"].set("5600")
    return app


def test_project_collects_from_open_windows_and_round_trips(tmp_path):
    """開いている 3 窓の内容が `.rsproj` へ入り、読み直しても同じであること。"""
    pytest.importorskip("tkinter")
    from report import project
    root = make_tk_root()
    try:
        root.withdraw()
        app = _launcher_with_windows(root)
        doc, warnings = app._collect_project()
        assert warnings == []
        assert doc.meta["project_name"] == "案件 A"
        assert [r.path_id for r in doc.batch_rows] == ["P1", "P2"]
        assert doc.scenario.mode == "sweep"
        assert doc.scenario.compare[0]["h_tx"] == "55"
        assert doc.scenario.sweep["from"] == "15"
        assert [w.lat for w in doc.multihop.waypoints] == [34.50, 34.60]
        assert doc.multihop.hop_rf[0].freq_mhz == 5600.0

        path = str(tmp_path / "p.rsproj")
        project.save(doc, path)
        again = project.load(path)
        assert [r.path_id for r in again.batch_rows] == ["P1", "P2"]
        assert again.scenario.sweep["from"] == "15"
        assert again.multihop.hop_rf[0].freq_mhz == 5600.0
    finally:
        root.destroy()


def test_project_keeps_sections_of_closed_windows():
    """**窓を閉じただけで節が消えないこと**（データ喪失の防止・譲らない性質）。

    バッチ窓を閉じてから保存すると行が消えたファイルを書く、という壊れ方を
    止める。バッチは通知が破棄より前に来る順序に依存しているので、順序が
    戻ればこのテストが落ちる。
    """
    pytest.importorskip("tkinter")
    root = make_tk_root()
    try:
        root.withdraw()
        app = _launcher_with_windows(root)
        app._batch_win.close_window()
        app._scenario_win.close_window()
        app._multihop_win.close_window()
        doc, warnings = app._collect_project()
        assert warnings == []
        assert [r.path_id for r in doc.batch_rows] == ["P1", "P2"]
        assert doc.scenario.compare[0]["h_tx"] == "55"
        assert [w.lat for w in doc.multihop.waypoints] == [34.50, 34.60]
    finally:
        root.destroy()


def test_project_sections_seed_reopened_windows():
    """読み込んだ節が、その窓を**開いたとき**に入ること（凍結方式と対）。"""
    pytest.importorskip("tkinter")
    root = make_tk_root()
    try:
        root.withdraw()
        app = _launcher_with_windows(root)
        doc, _ = app._collect_project()
        for win in (app._batch_win, app._scenario_win, app._multihop_win):
            win.close_window()

        app._project = doc
        bw = app.ensure_batch_window()
        assert [r.path_id for r in bw.project_rows()] == ["P1", "P2"]
        app._on_open_scenario()
        assert app._scenario_win._mode.get() == "sweep"
        assert app._scenario_win._cmp_cols[0]["h_tx"].get() == "55"
        assert app._scenario_win._from_var.get() == "15"
        app._on_open_multihop()
        assert [v["coord"].get() for v in app._multihop_win._wp_vars] == \
            ["34.500000, 132.400000", "34.600000, 132.500000"]
        assert app._multihop_win._hop_vars[0]["freq"].get() == "5600.0"
    finally:
        root.destroy()


def test_project_does_not_save_unreadable_batch_rows(tmp_path):
    """読めない値のある節は**保存せず警告する**（壊れた JSON を書かない）。"""
    pytest.importorskip("tkinter")
    root = make_tk_root()
    try:
        root.withdraw()
        app = _launcher_with_windows(root)
        app._batch_win._row_entries[0][3].delete(0, tkinter.END)
        app._batch_win._row_entries[0][3].insert(0, "abc")   # h_tx が読めない
        doc, warnings = app._collect_project()
        assert warnings and "P1" in warnings[0]
        assert doc.batch_rows is None      # 前回値が無いので節ごと出ない
        from report import project
        project.save(doc, str(tmp_path / "p.rsproj"))   # 例外なく書ける
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
    import tkinter as tk   # noqa: F401  （finally の TclError 捕捉で使う）

    # ⚠️ **テーマ済みのルートで測る**。素の Tk 既定フォントは実機より小さく、
    # そのまま測ると実物より狭い前提でこのゲートが緑になる（2.5b2 で条件探索側の
    # 同型ゲートが実際にそれで見切れを通した）。
    root = make_themed_root()
    try:
        root.withdraw()
        from views.launcher import SimLauncher
        app = SimLauncher(root, lambda _t: None)

        # 取得中の状態を作り、進捗を積んでから停止する。
        app._pump.start()
        app._progress_push(200, "地形データ取得中… 100%")
        app._progress_stop()
        app._prog_label.config(text="準備完了")
        app._prog_bar.config(value=0)

        # 停止後に残存ポーリングが 1 回発火しても表示は変わらない。
        app._pump._poll()
        assert app._prog_label.cget("text") == "準備完了"
        assert float(app._prog_bar.cget("value")) == 0.0

        # ワーカースレッドは停止後にも進捗を push しうる（取得完了の通知と
        # 最後のサンプルの push は競合する）。その分も描画してはいけない
        # ＝キュー破棄だけでなくポーリング側の早期 return が要る。
        app._progress_push(200, "地形データ取得中… 100%")
        app._pump._poll()
        assert app._prog_label.cget("text") == "準備完了"
        assert float(app._prog_bar.cget("value")) == 0.0
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

    root = make_tk_root()
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


# ⚠️ ランチャーの見切れゲートは **tests/test_window_fit.py へ移した**（2.5b2）。
# 窓ごとの手書きテストは「次の窓・次の増え方」で必ず穴が空くので、views の
# Toplevel を静的に洗い出して**全窓を横断で**検査する形に一本化した。
def test_graph_window_is_a_toplevel_that_does_not_block():
    """グラフ窓は**普通の Toplevel** で、開いても呼び出し元をブロックしないこと。

    以前は窓が丸ごと matplotlib の figure で、`plt.show()` の入れ子 mainloop が
    **閉じるまで返らなかった**。そのため「準備中」表示を戻すための `on_ready`
    フックが要り（戻り値を待つと閉じるまでラベルが残る）、終了時には pyplot の
    全 Figure を閉じる後始末も要った。B-024 の Tk 化でその 3 つがまとめて消える。

    ⚠️ **`pyplot` を使っていないことも見る**＝pyplot を 1 行でも使うとグローバルな
    図のレジストリと独自の Tk ルートが復活し、同じ構造に戻る。
    """
    import ast

    import numpy as np

    src = open(os.path.join(_APP_ROOT, "views", "graph.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    # ⚠️ 本文の文字列検索では駄目＝docstring が「pyplot を使わない理由」を
    # 説明しているので必ず引っかかる。**import 文を見る。**
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {f"{node.module}.{a.name}" for a in node.names if node.module}
    assert not any("pyplot" in name for name in imported), (
        f"views/graph.py が pyplot を import している（入れ子 mainloop と"
        f"後始末が戻る）: {sorted(n for n in imported if 'pyplot' in n)}"
    )
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "GraphWindow" in classes, "GraphWindow が無い"
    assert any(
        isinstance(b, ast.Attribute) and b.attr == "Toplevel"
        for b in classes["GraphWindow"].bases
    ), "GraphWindow が tk.Toplevel を継承していない（横断ゲートの傘に入らない）"

    root = make_tk_root()
    try:
        root.withdraw()
        from core import simulation as sim
        from views.graph import show_graph
        params = sim.SimParams({
            "start": "35.4258, 139.2131", "end": "35.4175, 139.2137",
            "h_tx": "30", "h_rx": "30", "freq": "2400", "p_tx": "13",
            "gain_tx": "2", "gain_rx": "2", "sens": "-90", "veg_h": "20",
            "k_factor": "10", "samples": "50", "env_type": "los",
            "rain_rate": "0", "diff_method": "bullington",
        })
        closed: list[str] = []
        # **ここが返ってくること自体**が検査（返らなければテストが固まる）。
        win = show_graph(root, params, np.zeros(50), on_close=lambda: closed.append("x"))
        try:
            assert isinstance(win, tkinter.Toplevel)
            assert win.winfo_exists()
            # 閉じたら呼び出し元へ知らせる（ランチャーが参照を外せる）。
            win._on_close()
            assert closed == ["x"]
        finally:
            if win.winfo_exists():
                win.destroy()
    finally:
        root.destroy()


def test_all_execution_flows_use_the_progress_pump():
    """実行フロー3つがいずれも ProgressPump で進捗を渡すこと。

    このアプリの「実行フロー」は 単一（launcher）／バッチ（batch_builder）／
    地図のタイル取得（map_window）の3つ。B-006（バッチ）と I-008（単一）は
    同じ欠陥クラスが別フローで再発したもので、原因の1つは**報告された1件だけ
    直してクラスとして直さなかった**こと。ここで3フローを固定し、進捗の受け渡し
    が各所で再発明されるのを防ぐ。

    ⚠️ このガードは**列挙したフローを固定するだけ**で、**新しいフローが
    ProgressPump を使わずに増えるのは検出できない**。実際 2.5a3 で条件探索
    （scenario）が4つ目のフローになり、実装は ProgressPump を使っていたのに
    **この一覧に登録されていなかった**（2026-07-26 に追加）。フローを増やす版は
    ここへの登録も完了条件に含めること。

    なお条件探索は進捗の**トランスポート**に ProgressPump を使い、**配分の意味論**
    は `scenario.Phases`（相の宣言）が持つ。既存3フローの Phases 移行は未実施。
    """
    import ast

    flows = {
        "views/launcher.py":      "単一実行",
        "views/batch_builder.py": "バッチ",
        "views/map_window.py":    "地図タイル取得",
        "views/scenario.py":      "条件探索",
    }
    missing = []
    for rel, label in flows.items():
        path = os.path.join(_APP_ROOT, rel)
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        used = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ProgressPump"
            for node in ast.walk(tree)
        )
        if not used:
            missing.append(f"{rel}（{label}）")
    assert not missing, (
        f"進捗の受け渡しが ProgressPump を通っていないフロー: {missing}"
    )


def test_graph_does_not_rewrite_the_diffraction_model():
    """グラフ画面は回折モデルを書き換える入力面を持たないこと（I-012 / 2.5a1）。

    回折モデルの source of truth はランチャー（`SimParams` を組む場所）。
    かつて断面グラフに切替ボタンがあり `self._params.diff_method` をその場で
    書き換えていたため、「ランチャーで Single を選んだのに保存レポートは
    Deygout」という齟齬が作れた。撤去したので、同じ経路が戻らないよう構造で
    固定する。

    ⚠️ rain_rate / h_tx / h_rx の what-if は残す設計なので対象外
    （値の書き換えを保存時に明示反映する＝モデル選択とは性質が違う）。
    """
    import ast

    path = os.path.join(_APP_ROOT, "views", "graph.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    offenders = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for t in targets:
            if (
                isinstance(t, ast.Attribute)
                and t.attr == "diff_method"
                and isinstance(t.value, ast.Attribute)
                and t.value.attr == "_params"
            ):
                offenders.append(getattr(node, "lineno", -1))

    assert not offenders, (
        "views/graph.py が params.diff_method を書き換えている"
        f"（行 {offenders}）。回折モデルの入力源はランチャー1箇所に保つこと。"
    )


# ⚠️ バッチの見切れゲートも tests/test_window_fit.py へ移した（上と同じ理由）。


# ============================================================
# ランチャーから分岐した窓の「凍結方式」
# ============================================================
# ランチャーの値を読むプロバイダを呼んでよい場所と、その理由。
#
# **判定の基準は「読んだ値をその場で画面へ書くか」**。書くなら＝ユーザーが見て
# いる値になるので凍結方式に反しない。書かずに使う（＝実行の瞬間に読む）と、
# 窓を開いたあとにランチャー側を変えた場合に画面と成果物が食い違う。
_FREEZE_ALLOWED_CALLERS = {
    "__init__":                      "窓を開く時の初期スナップショット",
    "_snapshot_meta":                "条件探索：取り込みの実体（呼ぶのは __init__ と ↻）",
    "_refresh_from_launcher":        "条件探索：↻ ランチャーから更新",
    "_refresh_common_from_launcher": "バッチ：↻ ランチャーから更新",
    "_build_case_info":              "バッチ：開く時の初期スナップショット",
    "_frozen_defaults":              "バッチ：行追加時に**その行のセルへ書き込む**値",
    "_frozen_row":                   "同上（セル文字列を組み立てて返すだけ）",
    "_update_row_rf":                "バッチ：行の RF 欄を**画面ごと**書き換える",
    "_launcher_endpoints":           "中継：TX / RX の初期値（呼ぶのは __init__ だけ・I-044）",
}


def _provider_call_sites() -> "list[str]":
    """`views/` で `self._config_provider()` / `self._meta_provider()` を
    **呼んでいる**場所を "ファイル:関数" で返す（渡しているだけの箇所は除く）。"""
    import ast

    views_dir = os.path.join(os.path.dirname(__file__), "..", "views")
    sites: list[str] = []
    for fname in sorted(os.listdir(views_dir)):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(views_dir, fname), encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=fname)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                fn = sub.func
                if (isinstance(fn, ast.Attribute)
                        and fn.attr in ("_config_provider", "_meta_provider")):
                    sites.append(f"{fname}:{node.name}")
    return sorted(set(sites))


def test_launcher_values_are_frozen_not_read_at_run_time():
    """ランチャーの値は**開く時と ↻ の時にだけ**読むこと。

    ランチャーから分岐する窓（バッチ・条件探索・以後に足す窓）は、ランチャーの
    現在値を**スナップショットして表示し、実行は表示中の値で行う**という方針で
    揃えてある（2026-07-26 ユーザー決定）。実行の瞬間に読み直すと、窓を開いた
    あとにランチャー側を変えた場合に **画面に出ていない値が成果物に載る**。

    実際に条件探索の案件情報がその状態で、しかも**画面に出ていなかったので
    誰も気づけなかった**。心がけでは防げないのでゲートにする
    （[[feedback-promote-recurring-checks]]）。新しい窓を足すときにここが落ちたら、
    「開く時に取り込む」「↻ で取り込み直す」のどちらかへ寄せること。
    """
    offenders = [s for s in _provider_call_sites()
                 if s.split(":", 1)[1] not in _FREEZE_ALLOWED_CALLERS]
    assert not offenders, (
        f"ランチャーの値を実行時に読み直している: {offenders}。"
        "スナップショット（開く時 / ↻）へ寄せること。"
    )


# ============================================================
# 「テストが止まらない」こと自体のゲート
# ============================================================
def test_modal_dialogs_and_os_handoff_are_blocked_in_tests(dialog_calls):
    """テスト中はモーダルダイアログと OS 委譲が塞がれていること。

    塞がれていないと `views.dialogs.confirm` の `wait_window()` が**人が
    ボタンを押すまで返らない**＝テストが止まる。2026-07-26 に条件探索の完了
    ダイアログ（「保存しました。開きますか？」）で実際に止まり、実行者が手で
    応答した。CI では表示できないので別の形で失敗し、原因が読みにくい。

    遮断は `tests/conftest.py` の autouse フィクスチャで行う（テストごとに
    monkeypatch を書くのは「思い出す規則」で、新しい GUI テストを書いた人が
    忘れた瞬間に再発する＝ネットワーク遮断と同じ扱いにした）。
    **この遮断自体が外れたことを検出する**のがこのテスト。
    """
    import os as _os
    from tkinter import filedialog

    from views import dialogs

    assert dialogs.confirm(None, "t", "m") is False, "confirm が塞がれていない"
    dialogs.alert(None, "t", "m")
    assert dialogs.choose(None, "t", "m", [("a", "A")]) is None
    _os.startfile("dummy")                       # 実行されるとブラウザが開く
    assert filedialog.askopenfilename() == ""
    assert dialog_calls.kinds() == [
        "confirm", "alert", "choose", "startfile", "askopenfilename"]
