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
def test_progress_bar_reaches_the_run_button(app_windows, name):
    """帯の下段は**バーと実行ボタンだけ**＝バーの右端がボタンに届くこと（I-047）。

    バッチだけ `N / M (P%)` のラベル（空でも `width=15` の箱）を 2 つのあいだに
    置いており、**その窓のバーだけ 15 文字ぶん短かった**（2026-08-07 実機確認・
    原文「バッチ窓のプログレスバーが他の窓より短い（実行ボタンのすぐ横まで届いて
    ない）」）。件数は上段（ステータスの行）へ移した。

    ⚠️ **「バーが伸びる」だけでは足りない**＝`expand=True` は既に別のゲートが
    見ているのに、この欠陥は素通りした（伸びてはいる＝残りを他のウィジェットが
    取っているだけ）。**隙間の実測**でしか捕まらない。
    """
    app, wins = app_windows
    win   = wins[name]
    owner = app if name == "launcher" else win
    bar, run_btn = owner._prog_bar, owner._run_btn

    win.update()
    gap = run_btn.winfo_rootx() - (bar.winfo_rootx() + bar.winfo_width())
    # 許容はボタン側の左余白（padx=10）ぶんだけ。
    assert gap <= 12, (
        f"{name}: 進捗バーと実行ボタンのあいだに {gap}px の隙間がある"
        "（帯の下段はバーと実行だけ＝第 3 のウィジェットを置かない）"
    )


@pytest.mark.parametrize("name", [n for n, _ in _WINDOWS])
def test_status_line_sits_above_the_progress_bar(app_windows, name):
    """ステータスは**進捗バーの上に 1 行**（I-047）＝4 窓とも同じ場所。

    以前は 4 窓で 3 通りだった（バッチ＝上／条件探索・中継＝バーの横／ランチャー＝
    上だが中央寄せ）。揃えるのは**場所**であって中身ではない＝OK/NG/ERR カウントは
    複数本を回す窓にしか意味が無いので、そこは揃えない。
    """
    app, wins = app_windows
    win   = wins[name]
    owner = app if name == "launcher" else win
    label, bar = owner._prog_label, owner._prog_bar

    assert label.winfo_parent() != bar.winfo_parent(), (
        f"{name}: ステータスが進捗バーと同じ帯にある（文言の長さでボタンが動く）"
    )
    win.update()
    assert label.winfo_rooty() < bar.winfo_rooty(), f"{name}: ステータスがバーの上に無い"
    # 左寄せ＝バーの左端と同じ位置から始まる（中央寄せだと窓ごとに始点が違う）。
    assert abs(label.winfo_rootx() - bar.winfo_rootx()) <= 2, (
        f"{name}: ステータスがバーの左端と揃っていない"
    )


@pytest.mark.parametrize("name", [n for n, _ in _WINDOWS])
def test_status_text_never_resizes_the_progress_bar(app_windows, name):
    """ステータスの**文言が伸びても実行帯の形が変わらない**こと（I-047 の理由）。

    ⚠️ 「別の帯にある」という構造だけでは足りない＝上の段に置いても、伸縮しない
    ラベルを右詰めにすれば帯の形は再び中身の量で動く。**位置ではなく振る舞い**で
    縛る。

    🔑 **当初はここで「実行ボタンが動かないこと」を見ようとしたが、それは一度も
    落ちないゲートだった**＝ボタンは帯の右端に*先に* pack されているので、同じ帯に
    長い文言が入っても動くのは**進捗バーの方**（バーが縮む）。実際に壊れる側を
    測る（[[feedback-promote-recurring-checks]] 壊れ方①）。
    """
    app, wins = app_windows
    win   = wins[name]
    owner = app if name == "launcher" else win
    label, bar = owner._prog_label, owner._prog_bar

    before = str(label.cget("text"))
    win.update()
    w0 = bar.winfo_width()
    label.config(text="あ" * 60)
    win.update()
    w1 = bar.winfo_width()
    label.config(text=before)
    win.update()
    assert w0 == w1, (
        f"{name}: ステータスの文言で進捗バーの幅が変わった（{w0} → {w1}）"
    )


@pytest.mark.parametrize("name", [n for n, _ in _WINDOWS])
def test_run_button_has_no_fixed_width(app_windows, name):
    """実行ボタンに**固定幅を与えない**こと（I-046）＝4 窓で同じ文字は同じ大きさ。

    ラベルの統一（I-029）は縛っていたのに大きさは縛っておらず、バッチだけ
    `width=14` で広かった。⚠️ 幅を外すと見切れる逆の実績があるので（`btn_import_csv`
    等）、外すのは**主操作 1 個だけ**で、それをここで固定する。
    """
    app, wins = app_windows
    owner = app if name == "launcher" else wins[name]
    width = str(owner._run_btn.cget("width"))
    assert width in ("", "0"), f"{name}: 実行ボタンに固定幅がある（width={width}）"


def test_no_button_is_stretched_to_stand_out(app_windows):
    """**大きさで主操作を表さない**＝行をまたいで引き伸ばしたボタンを置かない（I-049）。

    グラフ窓の保存がスライダー 3 行ぶんの高さを占めており、他窓の主操作（1 行）と
    不揃いだった。🔑 **名指しでなくクラスで縛る**＝「保存ボタンの rowspan」だけを
    禁じても、次に別のボタンを縦長にした瞬間に同じ欠陥が戻る（用語集ゲートで学んだ
    型と同じ）。強調は**位置**と Accent で表す。
    """
    import numpy as np
    app, wins = app_windows
    from views.graph import show_graph

    graph = show_graph(app.root, _params(), np.zeros(_params().num))
    try:
        targets = dict(wins)
        targets["graph"] = graph
        offenders: list[str] = []
        for name, win in targets.items():
            _collect_stretched_buttons(win, name, offenders)
        assert not offenders, (
            "行をまたいで引き伸ばしたボタンがある: " + ", ".join(offenders)
        )
    finally:
        graph.destroy()


def _collect_stretched_buttons(widget, name: str, out: list) -> None:
    """grid で 2 行以上をまたぐボタンを集める（別の Toplevel へは降りない）。"""
    import tkinter as tk
    from tkinter import ttk
    for child in widget.winfo_children():
        if isinstance(child, tk.Toplevel):
            continue
        if isinstance(child, (ttk.Button, tk.Button)):
            info = child.grid_info()
            if info and int(info.get("rowspan", 1)) > 1:
                out.append(f"{name}:{child.cget('text')}")
        _collect_stretched_buttons(child, name, out)


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

    **結果の返し先が窓ごとに違っても、色の出所は 1 つ**（2.7 スライス B）＝
    条件探索は結果一覧（Treeview のタグ）、バッチと中継は**入力表の行**（ラベルの
    前景色）。タグとラベルで機構が違うぶん、片方だけ theme から外れやすい。
    """
    from views import theme
    _, wins = app_windows
    expected = theme.verdict_colors(wins["scenario"])

    # ① 結果一覧を持つ窓＝タグの配色
    tree = wins["scenario"]._tree
    for key in ("ok", "ng"):
        got = str(tree.tag_configure(key, "foreground"))
        assert got == expected[key], (
            f"scenario: 判定色 {key} が theme と違う（{got!r} != {expected[key]!r}）"
        )

    # ② 表へ返す窓＝行に実際の判定を入れて、その前景色を測る（3 値とも）
    batch_win = wins["batch"]
    for status, key in (("OK", "ok"), ("NG", "ng"), ("ERROR", "err")):
        pid = batch_win._row_entries[0][0].get()
        batch_win._set_row_verdict(pid, status)
        lbl = batch_win._verdict_label(batch_win._row_frames[0])
        got = str(lbl.cget("foreground"))
        assert got == expected[key], (
            f"batch: 判定色 {key} が theme と違う（{got!r} != {expected[key]!r}）"
        )
    batch_win._clear_verdicts()

    mh = wins["multihop"]
    for status, key in (("OK", "ok"), ("NG", "ng"), ("ERROR", "err")):
        mh._show_hop_result(1, _fake_path_result("h1", status))
        got = str(mh._hop_result_labels[0]["status"].cget("foreground"))
        assert got == expected[key], (
            f"multihop: 判定色 {key} が theme と違う（{got!r} != {expected[key]!r}）"
        )
    mh._clear_hop_results()


# ============================================================
# 2c. 結果の居場所（2.7 スライス B・I-041）
# ============================================================
class _FakeResult:
    """`models.LinkBudgetResult` の代役（画面が読む 3 つの属性だけ持つ）。"""

    def __init__(self, status: str) -> None:
        self.status = status
        self.p_rx = -70.5
        self.actual_margin = 12.25


def _fake_path_result(path_id: str, status: str):
    """判定が `status` になる `batch.PathResult` を作る。

    `"ERROR"` は **2 通りの作り方**がある＝①計算そのものが落ちた ②計算は通ったが
    成果物が作れなかった（I-010）。ここでは後者で作る＝*計算が通っていても
    ERROR になり得る*ことを、画面のゲート側でも前提にしておくため。
    """
    import batch
    row = batch.PathRow(path_id=path_id, lat_tx=34.5, lon_tx=132.4,
                        lat_rx=34.6, lon_rx=132.5, h_tx=30.0, h_rx=10.0)
    if status == "ERROR":
        return batch.PathResult(row=row, result=_FakeResult("OK"),
                                artifact_error=RuntimeError("仕組んだ描画失敗"))
    return batch.PathResult(row=row, result=_FakeResult(status))


def test_result_comes_back_to_the_row_that_produced_it(app_windows):
    """**1 行 = 1 結果が成り立つ入力表を持つ窓は、その行へ結果を返す**（I-041）。

    バッチは経路の行へ、中継は**区間**の行へ返る（地点の行ではない＝地点 N に対し
    結果は N−1 で 1:1 が成り立たない）。カウンタや一覧は「合計」や「別の場所」しか
    持てず、どの入力がその結果を生んだのかを目で取り直す必要があった。
    """
    _, wins = app_windows

    # バッチ＝**ID で引く**（空行は実行対象から落ちるので、行番号は当てにならない）
    batch_win = wins["batch"]
    pid = batch_win._row_entries[0][0].get()
    batch_win._on_path_done(1, 1, _fake_path_result(pid, "NG"))
    lbl = batch_win._verdict_label(batch_win._row_frames[0])
    assert str(lbl.cget("text")) == "NG", "バッチ: 実行結果が入力表の行に返っていない"
    batch_win._clear_verdicts()

    # 中継＝区間表の行（既定は TX → RX の 1 区間）
    mh = wins["multihop"]
    mh._dispatch_event(("hop", (1, 1, _fake_path_result("route1_h1", "OK"))))
    cells = mh._hop_result_labels[0]
    assert str(cells["status"].cget("text")) == "OK", \
        "中継: 実行結果が区間表の行に返っていない"
    assert str(cells["rx"].cget("text")) and str(cells["margin"].cget("text")), \
        "中継: 受信レベル / マージンが区間の行に出ていない"
    mh._clear_hop_results()


def test_only_windows_without_a_one_to_one_table_keep_a_result_list(app_windows):
    """結果一覧（Treeview）を持ってよいのは**1 行 = 1 結果が成り立たない窓だけ**。

    規則の裏面まで縛る＝条件探索は 1 条件から結果が N 件出るので一覧を持つ。
    バッチと中継は入力表がそのまま結果の器なので、一覧を足すと**同じ数字が窓の
    中に 2 か所**できる（中継は 2.7 スライス B まで実際にそうなっていた）。

    ⚠️ 「`_tree` 属性が無いこと」では縛らない＝名前を変えれば通る。窓の配下に
    Treeview のウィジェットが**実在しないこと**で見る。
    """
    from tkinter import ttk
    _, wins = app_windows

    def _trees(widget) -> list:
        import tkinter as tk
        found = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Toplevel):
                continue                      # 別の窓へは降りない
            if isinstance(child, ttk.Treeview):
                found.append(str(child))
            found.extend(_trees(child))
        return found

    for name in ("batch", "multihop"):
        assert not _trees(wins[name]), (
            f"{name}: 入力表が 1 行 = 1 結果なのに結果一覧がある"
            "（結果はその結果を生んだ行へ返す＝I-041）"
        )
    assert _trees(wins["scenario"]), (
        "scenario: 1 条件から結果が N 件出る窓は結果一覧を持つ（規則の裏面）"
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


# ============================================================
# 6. 入口の語彙（2.7 スライス D）
# ============================================================
# ⚠️ ここは**語そのもの**ではなく「語で表した構造」を見る。語の統一は
# `tests/test_i18n_glossary.py` が用語集と突き合わせて守るので、二重に持たない。


def test_launcher_buttons_are_ordered_by_what_they_run(app_windows):
    """ランチャーの窓ボタン 4 つが「何を回すか」の軸で並んでいること（I-051）。

    ①経路を複数回す（複数経路 / 中継経路＝親戚）②1 経路を振る（条件探索）
    ③入力の道具（地図）。**地図は他 3 つの入力元**なので最後に置く——実行フロー
    と同じ列に並ぶと同格に見える（I-030「強調の軸を意味の軸に合わせる」の続き）。

    ⚠️ **座標（row, column）で見る**＝生成順で見ると、2 列 grid のどちらの列に
    載ったかを一切要求しないゲートになる（並び替えを検出できない）。
    """
    app, _ = app_windows
    expected = [
        ("btn_batch_mode", 0, 0), ("mh_open_btn",  0, 1),
        ("scn_open_btn",   1, 0), ("btn_open_map", 1, 1),
    ]
    placed = {}
    for w in app.root.winfo_children():
        for child in _walk(w):
            if child.winfo_class() == "TButton":
                info = child.grid_info()
                if info:
                    placed[(int(info["row"]), int(info["column"]))] = str(
                        child.cget("text"))
    actual = [placed.get((r, c)) for _k, r, c in expected]
    assert actual == [i18n.t(k) for k, _r, _c in expected], (
        f"窓ボタンの並びが意味の軸と合っていない: {actual}"
    )


def _walk(widget):
    """widget とその子孫を全部返す。"""
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def test_scenario_freezes_the_path_as_two_coordinate_fields(app_windows):
    """条件探索の凍結帯が、座標を **2 欄**で持つこと（I-048）。

    以前は `34.5, 132.4 → 34.5, 132.4` の 1 欄で、**他窓に無い第 3 の表記**だった。
    ⚠️ `→` そのものを追放したいのではない（中継の区間名 `A → B` は 2 点の*関係*
    を表す記号として情報を持つ）。ここは **2 つの入力値**なので欄で表す。
    """
    _app, wins = app_windows
    win = wins["scenario"]
    assert "→" not in win._tx_var.get() and "→" not in win._rx_var.get(), (
        "凍結帯の座標がまだ 1 欄に矢印で詰め込まれている"
    )
    labels = {str(w.cget("text")) for w in _walk(win) if w.winfo_class() == "TLabel"}
    for key in ("scn_tx_coord", "scn_rx_coord"):
        assert i18n.t(key) in labels, f"凍結帯に「{i18n.t(key)}」の欄が無い"


@pytest.mark.parametrize("mode,expect", [("dms", "°"), ("dd", ".")])
def test_committing_a_coordinate_reformats_it_to_the_current_notation(mode, expect):
    """入力を確定すると、その欄が現在の表記へ整形されること（I-060 R3）。

    **整形されること自体が「読めた」という返事**になる。これが無いと、DMS 表記を
    選んだ状態で DD を貼ったとき「受理された」のか「無視された」のかが画面から
    区別できない（実機で不具合として報告された）。
    ⚠️ 打鍵ごとには整形しない＝確定（Enter / focus 離脱）の 2 契機だけ。
    ⚠️ **窓を withdraw しない**＝可視でない widget には Tk がキーイベントを配送
    しないので、`_reformat_entry` を直に呼ぶだけの「配線を見ていないゲート」に
    化ける（実際、最初の実装は withdraw していて**何を書いても緑**だった）。
    """
    pytest.importorskip("tkinter")
    root = make_themed_root()
    try:
        app = _launcher(root)
        app._coord_fmt_var.set(mode)
        entry = app.entries["start"]
        entry.delete(0, "end")
        entry.insert(0, "34.8, 132.6")          # DD 表記で貼り付ける
        root.update()
        entry.focus_force()
        root.update()
        entry.event_generate("<Return>")
        root.update()
        assert expect in entry.get() and entry.get() != "34.8, 132.6", (
            f"確定しても表記が {mode} へ整形されていない: {entry.get()!r}"
        )
    finally:
        root.destroy()


def test_an_unreadable_coordinate_survives_being_committed():
    """読めない入力は**原文が残る**こと（I-060 R3 の裏面）。

    整形が返事なら、返事が来ないことが「読めなかった」の合図になる。ここで原文を
    捨てると、打ち間違いの現物が消えて直しようがなくなる。
    """
    pytest.importorskip("tkinter")
    root = make_themed_root()
    try:
        app = _launcher(root)
        app._coord_fmt_var.set("dms")
        entry = app.entries["start"]
        entry.delete(0, "end")
        entry.insert(0, "きた 34 ひがし 132")
        root.update()
        entry.focus_force()
        root.update()
        entry.event_generate("<Return>")
        root.update()
        assert entry.get() == "きた 34 ひがし 132"
    finally:
        root.destroy()


def test_committing_a_coordinate_reformats_it_in_the_batch_table_too():
    """同じ整形が**複数経路の表**でも起きること（I-060 のクラス点検）。

    手入力の座標欄はランチャーだけではない。1 か所だけ直すと「窓によって返事が
    返ったり返らなかったり」になり、⑧の観点では直す前より悪い。
    ⚠️ 表記はこの窓が**開いた時点で凍結した** `coord_format`（G2）を使う。
    """
    pytest.importorskip("tkinter")
    from views.batch_builder import BatchBuilderWindow
    root = make_themed_root()
    try:
        win = BatchBuilderWindow(root, _params(), coord_format="dms")
        entry = win._row_entries[0][1]          # 先頭行の start セル
        entry.delete(0, "end")
        entry.insert(0, "34.8, 132.6")
        root.update()
        entry.focus_force()
        root.update()
        entry.event_generate("<Return>")
        root.update()
        assert "°" in entry.get(), (
            f"複数経路の表で確定しても DMS へ整形されない: {entry.get()!r}"
        )
    finally:
        root.destroy()
