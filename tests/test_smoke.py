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

    _progress_stop の時点で after(50) 済みのポーリングが 1 回残るため、
    停止後に _poll_progress が走っても描画してはいけない。実機では
    描画完了後もラベルが「地形データ取得中… 100%」のまま残った。
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
        app._prog_active = True
        app._progress_push(200, "地形データ取得中… 100%")
        app._progress_stop()
        app.prog_label.config(text="準備完了")
        app.prog_bar.config(value=0)

        # 停止後に残存ポーリングが 1 回発火しても表示は変わらない。
        app._poll_progress()
        assert app.prog_label.cget("text") == "準備完了"
        assert float(app.prog_bar.cget("value")) == 0.0

        # ワーカースレッドは停止後にも進捗を push しうる（取得完了の通知と
        # 最後のサンプルの push は競合する）。その分も描画してはいけない
        # ＝キュー破棄だけでなく _poll_progress の早期 return が要る。
        app._progress_push(200, "地形データ取得中… 100%")
        app._poll_progress()
        assert app.prog_label.cget("text") == "準備完了"
        assert float(app.prog_bar.cget("value")) == 0.0
    finally:
        root.destroy()
