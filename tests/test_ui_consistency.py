"""
tests/test_ui_consistency.py
============================
**窓をまたいで同じであるべきもの**を縛る横断ゲート。

なぜ要るか
----------
2.6 の UI/UX パスで「実行ボタンは 3 フローとも同じ表記・同じ位置（進捗バーの
右）」と決めて記録したのに、**実際に揃っていたのはランチャーだけ**だった
（条件探索はバーの左、バッチは下段の編集ボタン列の右端）。決めごとを散文でしか
持っていないと、次の窓を作るときに**型が静かにずれる**——しかも窓ごとに見れば
どれも「おかしくない」ので、実際に並べて触るまで気づけない。

⇒ 「揃っている」ことを**全窓を実際に起こして機械的に確認**する。窓が増えたら
`_WINDOWS` に足す（足し忘れは新しい窓が検査されないという形で効くので、
window_fit の登録漏れ検出と同じ理由でここも一覧を 1 つに保つ）。

ここで守る型
------------
1. **実行ボタンは進捗バーと同じ帯にあり、帯の右端**（I-029）。
2. **判定色（OK / NG）の出所は theme**（画面のハードコードを増やさない）。
3. **画面で bold を使わない**（強調は配置と余白で作る＝2026-08-01 ユーザー決定）。
   レポート HTML の太字は**別の設計言語**（印刷物）なのでここでは見ない。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import i18n
import simulation as sim
from conftest import make_themed_root


def _launcher(root):
    from views.launcher import SimLauncher
    return SimLauncher(root, lambda _t: None)


def _params():
    return sim.SimParams(config.DEFAULT_CONFIG)


# 実行フローを持つ窓＝(名前, 生成関数)。生成関数は (root, launcher) を受けて窓を返す。
_WINDOWS = [
    ("launcher", lambda root, app: app.root),
    ("batch",    lambda root, app: app.ensure_batch_window()),
    ("scenario", lambda root, app: (app._on_open_scenario(), app._scenario_win)[1]),
    ("multihop", lambda root, app: (app._on_open_multihop(), app._multihop_win)[1]),
]


@pytest.fixture(scope="module")
def app_windows():
    """ランチャーと 3 つの窓を開いた状態。

    ⚠️ **module スコープ**＝ここのテストは「窓を眺めて型を確かめる」だけで中身を
    書き換えないので、窓は 1 組で足りる。テストごとに Tk のルートを作り直すと、
    ルートの生成が詰まって**表示依存テストが黙って skip される**（conftest の
    リトライで拾いきれない）／ごく稀に Tcl が落ちる（I-019）という形で、この
    ファイルが他のゲートを巻き添えにする。
    """
    pytest.importorskip("tkinter")
    root = make_themed_root()
    root.withdraw()
    i18n.set_lang("ja")
    app = _launcher(root)
    try:
        yield app, {name: make(root, app) for name, make in _WINDOWS}
    finally:
        root.destroy()


# ============================================================
# 1. 実行ボタンの位置
# ============================================================
@pytest.mark.parametrize("name", [n for n, _ in _WINDOWS])
def test_run_button_sits_at_the_right_end_of_the_progress_bar(app_windows, name):
    """実行ボタンは**進捗バーと同じ帯の右端**にあること。

    「同じ帯」まで縛るのが要点＝バッチは実行ボタンを別フレーム（編集ボタン列）に
    置いており、window_fit のような寸法ゲートでは検出できなかった。
    """
    app, wins = app_windows
    win = wins[name]
    owner = app if name == "launcher" else win
    # 属性名も 4 窓で同じ（`_run_btn` / `_prog_bar`）＝ランチャーだけ `run_btn` と
    # 公開名で持っており、横断で検査しようとして初めて食い違いが見えた。
    run_btn  = owner._run_btn
    prog_bar = owner._prog_bar

    assert run_btn.winfo_parent() == prog_bar.winfo_parent(), (
        f"{name}: 実行ボタンが進捗バーと別の帯にある"
    )
    assert run_btn.pack_info()["side"] == "right", f"{name}: 実行ボタンが右端でない"
    assert prog_bar.pack_info()["side"] == "left", f"{name}: 進捗バーが左で伸びていない"
    assert prog_bar.pack_info()["expand"] in ("1", 1, True), (
        f"{name}: 進捗バーが伸びない（ボタン位置が中身の量で動く）"
    )
    # 実測でも右にあること（pack の順序を間違えると side=right でも内側に来る）。
    # ⚠️ `update_idletasks` では足りない＝スクロール受け皿（Canvas の中に窓を置く
    # 形）の中身は **Configure イベントが回るまで幅が 1 のまま**で、実測が全部 0 に
    # なる。イベントまで回す `update()` を使う。
    win.update()
    assert run_btn.winfo_x() > prog_bar.winfo_x(), f"{name}: 実行ボタンがバーより左"


@pytest.mark.parametrize("name", [n for n, _ in _WINDOWS])
def test_run_button_is_the_only_accent_button(app_windows, name):
    """Accent（塗り）は**「走らせる」ボタンだけ**（強調の軸＝意味の軸）。"""
    app, wins = app_windows
    win = wins[name]
    owner = app if name == "launcher" else win
    accents = _accent_buttons(win if name != "launcher" else app.root)
    assert accents, f"{name}: Accent ボタンが 1 つも無い"
    assert accents == [str(owner._run_btn)], (
        f"{name}: 走らせる以外にも Accent が付いている: {accents}"
    )


def _accent_buttons(widget) -> list:
    """配下の Accent.TButton を文字列パスで集める。

    ⚠️ 別の Toplevel（他の窓・ダイアログ）へは降りない＝Tk では Toplevel も
    ルートの子なので、素直に再帰すると全窓を数えてしまう。
    """
    import tkinter as tk
    found = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Toplevel):
            continue
        try:
            if str(child.cget("style")) == "Accent.TButton":
                found.append(str(child))
        except Exception:
            pass
        found.extend(_accent_buttons(child))
    return found


# ============================================================
# 2. 判定色の出所
# ============================================================
def test_verdict_colors_come_from_theme_in_every_window(app_windows):
    """OK / NG の色は `theme.verdict_colors` を出所にし、**全窓で同じ**であること。

    ⚠️ 条件探索だけが色分けされ、中継経路は同色だった（レポート側は両方とも
    色分けしている＝画面だけが落ちていた）。色を窓ごとに足すと出所が増えるので、
    出所を 1 つにしたうえで「使っていること」をここで縛る。
    """
    from views import theme
    _, wins = app_windows
    expected = theme.verdict_colors(wins["scenario"])
    for name in ("scenario", "multihop"):
        tree = wins[name]._tree
        for key in ("ok", "ng"):
            got = str(tree.tag_configure(key, "foreground"))
            assert got == expected[key], (
                f"{name}: 判定色 {key} が theme と違う（{got!r} != {expected[key]!r}）"
            )


# ============================================================
# 2b. 数値パネルの桁揃え（グラフ窓）
# ============================================================
def test_graph_panel_keeps_units_out_of_the_values():
    """リンクバジェットの値に**単位を混ぜない**こと（桁が揃わなくなる）。

    `-76.4 dBm` と `12.3 dB` を 1 つの文字列にして右寄せすると、揃うのは単位の
    右端で**小数点は揃わない**（2026-08-01 実機フィードバック）。単位は
    `_panel_rows` が別の列に固定するので、値は数値だけを持つ。
    """
    pytest.importorskip("tkinter")
    import numpy as np
    root = make_themed_root()
    root.withdraw()
    try:
        from views.graph import show_graph
        params = _params()
        win = show_graph(root, params, np.zeros(params.num))
        win.update()
        numeric = ("eirp", "fspl", "diff_loss", "total_loss", "gain_rx",
                   "p_rx", "sens", "margin", "k_factor", "f1_obs", "slant")
        for key in numeric:
            value = win._vars[key].get()
            assert value.replace(",", "").replace("+", "").replace("-", "") \
                .replace(".", "").isdigit(), f"{key} に数値以外が混ざっている: {value!r}"

        # 値は列 1 に右寄せ・単位は列 2（列が崩れれば桁も崩れる）。
        values, units_ = _panel_columns(win)
        assert values, "値の列が見つからない（パネルの組み方が変わった）"
        assert all(a == "e" for a in values), f"値の列が右寄せでない: {values}"
        assert units_, "単位の列が無い（値に単位を混ぜていないか）"
    finally:
        root.destroy()


def test_graph_legend_does_not_cover_the_plot():
    """凡例が**プロット領域に重ならない**こと（軸の外・上＝レポート図と同じ）。

    図の中の右上に置くと、経路は左下→右上に描かれるので**受信側のアンテナと
    受信端が必ず凡例の下に入る**（2026-08-01 実機フィードバック）。`loc="best"`
    は出る場所が毎回変わるので採らない＝位置の一貫性そのものが要件（⑧）。
    """
    pytest.importorskip("tkinter")
    import numpy as np
    root = make_themed_root()
    root.withdraw()
    try:
        from views.graph import show_graph
        params = _params()
        win = show_graph(root, params, np.zeros(params.num))
        win.update()
        fig = win._fig
        fig.canvas.draw()
        legend = win._ax.get_legend()
        lg = legend.get_window_extent(fig.canvas.get_renderer())
        ax = win._ax.get_window_extent()
        assert lg.y0 >= ax.y1 - 1, (
            f"凡例がプロット領域に重なっている（凡例 y0={lg.y0:.0f} / 軸 y1={ax.y1:.0f}）"
        )
    finally:
        root.destroy()


def _panel_columns(win) -> tuple:
    """パネルの列 1（値）の anchor 一覧と、列 2（単位）のテキスト一覧。"""
    from tkinter import ttk
    values, units_ = [], []
    for frame in _labelframes(win):
        for child in frame.grid_slaves():
            info = child.grid_info()
            if not isinstance(child, ttk.Label):
                continue
            if int(info.get("column", 0)) == 1:
                values.append(str(child.cget("anchor")))
            elif int(info.get("column", 0)) == 2:
                units_.append(str(child.cget("text")))
    return values, units_


def _labelframes(widget) -> list:
    from tkinter import ttk
    found = []
    for child in widget.winfo_children():
        if isinstance(child, ttk.LabelFrame):
            found.append(child)
        found.extend(_labelframes(child))
    return found


# ============================================================
# 3. 画面では bold を使わない
# ============================================================
def test_no_bold_font_on_screen(app_windows):
    """どの窓のウィジェットにも太字を使わないこと（2026-08-01 ユーザー決定）。

    理由＝**かえって読みにくい**。強調は配置・余白・区切り線で作る。
    ⚠️ レポート HTML（印刷物）の `font-weight:bold` は別の設計言語なので対象外。
    """
    _, wins = app_windows
    offenders: list[str] = []
    for name, win in wins.items():
        _collect_bold(win, name, offenders)
    assert not offenders, "画面で bold が使われている: " + ", ".join(offenders)


def _collect_bold(widget, name: str, out: list) -> None:
    for child in widget.winfo_children():
        try:
            font = child.cget("font")
        except Exception:
            font = ""
        if font:
            spec = str(widget.tk.call("font", "actual", font))
            if "bold" in spec:
                out.append(f"{name}:{child.winfo_class()}")
        _collect_bold(child, name, out)


# ============================================================
# 4. 画面語彙（1 つのものを 2 語で呼ばない）
# ============================================================
# ⚠️ **ここにあった「ホップ」1 語のゲートは `tests/test_i18n_glossary.py` へ昇格した**
#    （2026-08-05・2.7 の用語集スライス）。`docs/glossary.md` の `区間` 行が
#    `ホップ / hop` を禁止語として持ち、変異検証も引き継いである。
#
#    🔑 **列挙で塞いだ穴は、名前 1 つで開く**——「ホップ」だけを名指しで禁じても、
#    次に別の語が 2 通りに分かれた瞬間に同じ欠陥が戻る。語の統一は*クラス*
#    （用語集の表）で縛るのが正しい形なので、1 語ぶんのゲートは残さない。


# ============================================================
# 5. app 設定は「開いた時点」で凍結される（2.7 スライス G2＝I-055 ②）
# ============================================================
# 窓は `config.load_config()` を直に読まず、**ランチャーが読んだ値を引数で
# 受け取る**（[[project-radiosim]] の凍結方式を設定へ広げただけ）。
# ⚠️ 静的ゲート（tests/test_repo_hygiene.py::TestConfigHasOneSource）は「直に
# 読んでいない」ことしか言えない。**渡した値が実際に効いている**ことは、窓を
# 起こして確かめないと分からない（＝渡した引数を無視する実装でも静的には緑）。
def test_batch_window_uses_the_injected_coord_format():
    """バッチ窓が、渡された座標表記で行を組むこと（DMS を渡せば DMS で入る）。"""
    pytest.importorskip("tkinter")
    from views.batch_builder import BatchBuilderWindow
    root = make_themed_root()
    root.withdraw()
    try:
        win = BatchBuilderWindow(root, _params(), coord_format="dms")
        win.append_path((35.4258, 139.2131), (35.4175, 139.2137))
        # 窓は既定で 1 行を持って開くので、見るのは**追加した行**（末尾）。
        texts = [e.get() for e in win._row_entries[-1]]
        assert any("°" in t for t in texts), (
            f"渡した coord_format='dms' が行に効いていない: {texts}"
        )
    finally:
        root.destroy()


def test_graph_window_saves_with_the_coord_format_it_was_opened_with(monkeypatch,
                                                                    tmp_path):
    """グラフ窓の保存が、**開いた時点**の座標表記を使うこと。

    ⚠️ 以前はここで毎回 `config.load_config()` を読み直していた＝保存の瞬間の
    設定ファイルの中身に依存し、テストの結果が開発機の設定で変わった（I-055）。
    """
    pytest.importorskip("tkinter")
    import numpy as np

    from views import graph as g
    root = make_themed_root()
    root.withdraw()
    try:
        params = _params()
        win = g.show_graph(root, params, np.zeros(params.num), coord_format="dms")
        seen: dict = {}

        def _spy_save_package(**kw):
            seen["coord_format"] = kw.get("coord_format")
            return str(tmp_path)

        monkeypatch.setattr(g.sim, "save_package", _spy_save_package)
        monkeypatch.setattr(g.report_path, "save_profile_png", lambda *a, **k: None)
        monkeypatch.setattr(g.report_path, "save_path_kml", lambda *a, **k: None)
        monkeypatch.setattr(g.dialogs, "choose", lambda *a, **k: None)
        win._on_save()
        assert seen.get("coord_format") == "dms", (
            f"保存が開いた時点の表記を使っていない: {seen}"
        )
    finally:
        root.destroy()
