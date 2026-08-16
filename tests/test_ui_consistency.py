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

from core import config
from core import i18n
from core import simulation as sim
from report import batch as b
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
    # ⚠️ **言語は自分で戻す**＝conftest の autouse な復元は**届かない**（pytest は
    # 上位スコープのフィクスチャを先に組むので、あちらが「前の言語」を捕まえる
    # 時点でもう ja になっている＝差が無く、何も戻らない）。実測＝この漏れで
    # `test_batch` の英語メッセージ assert が 10 件落ちた（走らせる順による）。
    # 静的な守り＝`test_paths.py::…test_higher_scoped_fixtures_restore_the_language…`。
    lang_before = i18n._lang
    i18n.set_lang("ja")
    app = _launcher(root)
    try:
        yield app, {name: make(root, app) for name, make in _WINDOWS}
    finally:
        root.destroy()
        i18n.set_lang(lang_before)


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
    上だが中央寄せ）。揃えるのは**場所**であって中身ではない＝帯が何を言うかは
    窓ごとに違ってよい（中継は全体判定とどの区間が決めているか、複数経路は進捗だけ）。

    ⚠️ **旧註は「OK/NG/ERR カウントは複数本を回す窓にしか意味が無い」と書いていた**
    が、その集計は I-078（2026-08-13）で外した＝2.7 で行ごとの判定列が入り、帯の
    集計が**同じ事実の数え直し**になったため。⇒ 例として挙げるものが実装から消えた
    ので註を現状へ直した（**中身を揃えないという結論は変わっていない**）。
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
    from report import batch
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
# 4-b. 凍結帯の ↻ と 🔒 は 3 窓で同じ場所（B-053）
# ============================================================
_FROZEN_WINDOWS = ("batch", "scenario", "multihop")


@pytest.mark.parametrize("name", _FROZEN_WINDOWS)
def test_the_refresh_pair_sits_on_the_first_row_of_the_frozen_area(app_windows, name):
    """`↻ ランチャーから更新` と `🔒 ランチャーの値` は**案件情報の枠の中**にあること。

    この 2 つは**帯 1 つではなく凍結領域全体**に効く（↻ はどの窓でも案件情報と共通
    設定／経路をまとめて取り込む）。にもかかわらず置き場は 3 窓で 3 通りだった
    ＝条件探索は経路の行・中継経路は共通設定の 2 行目・複数経路は共通設定の中の
    独立行。**しかも置き場は幅の問題を連れてくる**＝条件探索（B-052）と中継経路
    （B-053）では、この 2 つが座標や 6 欄と同じ行を分け合って**帯が窓幅を決めていた**。

    ⇒ 「案件情報の枠の中」に揃える。⚠️ **並び順や padding までは縛らない**（窓ごとに
    右端の余りが違う）。縛るのは**どの枠に属するか**＝幅の問題が起きる場所そのもの。
    """
    _app, wins = app_windows
    win = wins[name]
    case_frames = [w for w in _walk(win)
                   if w.winfo_class() == "TLabelframe"
                   and str(w.cget("text")) == i18n.t("batch_case_info")]
    assert len(case_frames) == 1, f"{name}: 案件情報の枠が 1 つでない"
    case = str(case_frames[0])
    found = {}
    for w in _walk(win):
        text = str(w.cget("text")) if "text" in w.keys() else ""
        if text.startswith("↻"):
            found["refresh"] = w
        elif text.startswith("🔒") and len(text) > 2:   # 単独の 🔒 は各欄の印
            found["hint"] = w
    for key in ("refresh", "hint"):
        assert key in found, f"{name}: {key} が見つからない（文言が変わった？）"
        assert found[key].winfo_parent() == case, (
            f"{name}: {key} が案件情報の枠の外にある"
            f"（{found[key].winfo_parent()} ≠ {case}）"
        )


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
        ("batch_window_title", 0, 0), ("mh_window_title",  0, 1),
        ("scn_window_title",   1, 0), ("map_window_title", 1, 1),
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


# ============================================================
# 7. 窓をまたぐ導線（2.7 スライス E）
# ============================================================


def test_the_map_can_be_opened_from_every_window_that_places_points(app_windows):
    """**地点を置く窓は、どれも地図を開ける**こと（I-043）。

    中継経路には前から `地図から選択` があり、複数経路だけ無かった＝「バッチから
    地図を開かない」という古い決めごとが、後から来た窓に破られていた。
    ⚠️ **条件探索は対象外**＝経路をランチャーから凍結して受け取るので、地点を
    置く窓ではない（3 窓を機械的に揃えない）。
    """
    _app, wins = app_windows
    for name in ("batch", "multihop"):
        labels = [str(w.cget("text")) for w in _walk(wins[name])
                  if w.winfo_class() == "TButton"]
        assert i18n.t("mh_from_map") in labels, (
            f"{name}: 地図を開く口が無い（地点を置く窓には要る）"
        )


def test_deleting_a_relay_keeps_the_remaining_sections_aligned():
    """**任意の中継点**を消せて、区間の設定がずれないこと（I-045）。

    🔴 ここがこの項目の実害＝`_sync_hops` はホップ行を**先頭から詰め直す**ので、
    地点だけ消すと**消した地点より後ろの周波数が 1 つ前へずれる**。ずれても画面は
    自然に見えるため、**黙って別の区間の設定で計算する**。
    ⚠️ 「削除できること」だけを見るゲートでは捕まらない（削除は前から出来ていた）。
    """
    pytest.importorskip("tkinter")
    from views.multihop import MultiHopWindow
    root = make_themed_root()
    root.withdraw()
    try:
        win = MultiHopWindow(root, _params())
        for _ in range(2):
            win._on_add_point()                 # TX, R1, R2, RX
        assert len(win._hop_vars) == 3
        for i, freq in enumerate(("100", "200", "300")):
            win._hop_vars[i]["freq"].set(freq)

        win._delete_waypoint(1)                 # 先頭の中継点を消す
        # 残るのは「入ってくる側」＝TX → R1 の設定と、その後ろの区間。
        assert [v["freq"].get() for v in win._hop_vars] == ["100", "300"], (
            "中継点を消したら区間の周波数がずれた（別の区間の設定で計算される）"
        )
        # 送信点・受信点は消せない（画面にも `×` を出していない）。
        names_before = [v["name"].get() for v in win._wp_vars]
        win._delete_waypoint(0)
        win._delete_waypoint(len(win._wp_vars) - 1)
        assert [v["name"].get() for v in win._wp_vars] == names_before
    finally:
        root.destroy()


def test_inserting_a_relay_does_not_shift_the_later_sections():
    """**任意の位置に挿しても**、後ろの区間の設定がずれないこと（I-074）。

    🔴 削除側（I-045）の裏面＝`_sync_hops` は `old[i]` を**位置で**再利用するので、
    地点だけ挿すと**挿した位置より後ろの周波数が 1 つ後ろへずれる**。画面は自然に
    見えたまま、黙って別の区間の設定で計算する。
    ⚠️ 「任意の位置に挿せること」だけを見るゲートでは捕まらない（並びは正しく見える）。
    """
    pytest.importorskip("tkinter")
    from views.multihop import MultiHopWindow
    root = make_themed_root()
    root.withdraw()
    try:
        win = MultiHopWindow(root, _params())
        for _ in range(2):
            win._on_add_point()                 # TX, R1, R2, RX
        for i, freq in enumerate(("100", "200", "300")):
            win._hop_vars[i]["freq"].set(freq)

        win._add_waypoint(index=1)              # TX と R1 のあいだへ挿す
        assert len(win._wp_vars) == 5 and len(win._hop_vars) == 4
        # 挿した地点から「出ていく」区間だけが空欄。入ってくる側（TX → 新）は
        # 分割前と**始点が同じ**なので、利用者の入力の意味が変わらない。
        assert [v["freq"].get() for v in win._hop_vars] == ["100", "", "200", "300"], (
            "中継点を挿したら後ろの区間の周波数がずれた（別の区間の設定で計算される）"
        )
    finally:
        root.destroy()


def test_insert_and_delete_at_the_same_place_round_trips():
    """挿してから同じ位置を消すと、**地点も区間の設定も完全に元へ戻る**こと。

    🔑 **この 1 本で「ずれ」を封じられる**＝追加と削除が互いの逆写像であることを
    直接見るので、片方の規則だけが動いた日に必ず落ちる。
    """
    pytest.importorskip("tkinter")
    from views.multihop import MultiHopWindow
    root = make_themed_root()
    root.withdraw()
    try:
        win = MultiHopWindow(root, _params())
        for _ in range(2):
            win._on_add_point()
        for i, freq in enumerate(("100", "200", "300")):
            win._hop_vars[i]["freq"].set(freq)
        names_before = [v["name"].get() for v in win._wp_vars]
        freqs_before = [v["freq"].get() for v in win._hop_vars]

        for at in (1, 2, 3):                    # 先頭・中間・受信点の手前
            win._add_waypoint(index=at)
            win._delete_waypoint(at)
            assert [v["name"].get() for v in win._wp_vars] == names_before, (
                f"位置 {at} で挿して消したら地点の並びが戻らない"
            )
            assert [v["freq"].get() for v in win._hop_vars] == freqs_before, (
                f"位置 {at} で挿して消したら区間の設定が戻らない"
            )
    finally:
        root.destroy()


def test_the_endpoints_stay_fixed_however_the_insert_is_asked():
    """⛔ **送信点より前・受信点より後ろには挿さらない**こと。

    先頭＝送信点／末尾＝受信点は窓の不変条件で、`＋` の口が増えても変わらない。
    ⚠️ 画面のボタンは受信点の行に出していないが、**範囲は呼び出し側ではなく
    `_add_waypoint` が守る**（口が 3 つある＝行の `＋`・下部のボタン・地図）。
    """
    pytest.importorskip("tkinter")
    from views.multihop import MultiHopWindow
    root = make_themed_root()
    root.withdraw()
    try:
        win = MultiHopWindow(root, _params())
        tx, rx = (v["name"].get() for v in win._wp_vars)
        win._add_waypoint(index=0)              # 送信点より前は不可
        assert win._wp_vars[0]["name"].get() == tx
        win._add_waypoint(index=99)             # 受信点より後ろも不可
        assert win._wp_vars[-1]["name"].get() == rx
        assert len(win._hop_vars) == len(win._wp_vars) - 1
    finally:
        root.destroy()


def test_relay_window_inherits_the_launcher_endpoints():
    """中継の TX / RX は**開いた時**にランチャーの座標を引き継ぐこと（I-044）。

    ⚠️ **中継点は空のまま**（ランチャーに対応する値が無い）。
    """
    pytest.importorskip("tkinter")
    from views.multihop import MultiHopWindow
    root = make_themed_root()
    root.withdraw()
    try:
        cfg = dict(config.DEFAULT_CONFIG)
        cfg["start"] = "35.10000, 139.20000"
        cfg["end"]   = "35.20000, 139.30000"
        win = MultiHopWindow(root, _params(), config_provider=lambda: dict(cfg))
        shown = [v["coord"].get() for v in win._wp_vars]
        assert "35.1" in shown[0] and "35.2" in shown[1], (
            f"ランチャーの座標を引き継いでいない: {shown}"
        )
    finally:
        root.destroy()


def test_relay_window_inherits_the_launcher_heights_per_end():
    """中継の TX / RX は**送受それぞれの高さ**を引き継ぐこと（B-055）。

    🔴 **座標は振り分けていたのに、高さは 2 点とも `h_tx`** だった＝ランチャーで
    送信 30m / 受信 10m と入れても受信点が 30m で始まり、気づかず実行すると
    **受信側を 30m として計算する**（値は壊れていないが、入れた覚えのない前提で
    答えが出る）。複数経路の表は最初から `h_tx` / `h_rx` を別々に凍結している
    （`batch_table._frozen_row`）＝**揃っていなかったのは中継だけ**。

    ⚠️ **中継点は `h_tx` のまま**（ランチャーに対応する値が無い）＝ここも縛って
    おかないと「全点 h_rx」のような直し方でも緑になる。
    """
    pytest.importorskip("tkinter")
    from views.multihop import MultiHopWindow
    root = make_themed_root()
    root.withdraw()
    try:
        cfg = dict(config.DEFAULT_CONFIG)
        cfg["h_tx"], cfg["h_rx"] = "30.0", "10.0"
        win = MultiHopWindow(root, sim.SimParams(cfg),
                             config_provider=lambda: dict(cfg))
        assert [v["height"].get() for v in win._wp_vars] == ["30.0", "10.0"], (
            "送信点／受信点の初期高さがランチャーの送受と対応していない: "
            f"{[v['height'].get() for v in win._wp_vars]}"
        )
        win._add_waypoint()          # 中継点＝対応する値が無いので h_tx
        assert [v["height"].get() for v in win._wp_vars] == \
            ["30.0", "30.0", "10.0"], (
                "中継点の高さが h_tx でない／地点を足したら他の点の高さが動いた: "
                f"{[v['height'].get() for v in win._wp_vars]}"
            )
    finally:
        root.destroy()


def test_relay_window_reads_both_coordinate_notations():
    """中継が **DMS 入力も受ける**こと（I-070 ②＝R1 の穴）。

    🔴 以前は `split(",")` ＋ `float()` の手読みで、**正しい座標を拒否していた**
    （「入力は DD / DMS のどちらも受ける」がこの窓だけ成り立っていなかった）。
    ⚠️ 保存・計算へ渡るのは**常に DD**（内部の正典は変えない）。
    """
    pytest.importorskip("tkinter")
    from views.multihop import MultiHopWindow
    root = make_themed_root()
    root.withdraw()
    try:
        win = MultiHopWindow(root, _params(), coord_format="dms")
        win._wp_vars[0]["coord"].set("34°48'00.0\"N, 132°36'00.0\"E")
        win._wp_vars[1]["coord"].set("34.5, 132.4")     # DD も同時に受ける
        path = win._collect_path()
        assert path.waypoints[0].lat == pytest.approx(34.8)
        assert path.waypoints[0].lon == pytest.approx(132.6)
        assert path.waypoints[1].lat == pytest.approx(34.5)
    finally:
        root.destroy()


def _entries_bound_to(win, variables) -> list:
    """指定した StringVar に結びついた Entry を集める。

    ⚠️ **幅で座標欄を探さない**＝最初の実装は「width >= 20 の Entry」で拾おうと
    して、案件情報の読み取り専用欄（width=20）を掴んで落ちた（壊れ方③＝間違った
    ものを要求するゲート）。**何に結びついているか**で特定する。
    """
    names = {str(v) for v in variables}
    return [w for w in _walk(win)
            if w.winfo_class() == "TEntry" and str(w.cget("textvariable")) in names]


def _dms_samples() -> "list[str]":
    """画面に出る DMS 表記のうち**一番幅を食う**もの（要求量そのもの）。

    経度が 3 桁になる日本国内の座標（`132°36'00.0"E`）が最長。南緯・西経も
    入れる＝半球記号が変わるだけだが、**書式を変えた日に片方だけ伸びる**ことが
    あるので両方見る。
    """
    from core import coords as _coords
    return [_coords.format_dms(34.8, 132.6), _coords.format_dms(-34.8, -132.6)]


# 出荷し得る本文書体（`views/theme.py` が当てるもの＋その環境フォールバック）と、
# DPI 100/125/150% で取り得るサイズ。⚠️ **入っていない書体は自動で飛ばす**
# （`actual()` が別書体を返す＝その環境には無い）。
_SHIPPING_FAMILIES = ("Segoe UI Variable Text", "Segoe UI",
                      "Yu Gothic UI", "Meiryo UI")
_SHIPPING_SIZES = (-11, -14, -17, -21, 9, 12)


def _required_width_units(root, text: str) -> float:
    """`text` を描くのに要る Tk の `width` 単位数（出荷し得る書体・サイズの最悪）。

    🔑 **Tk の `width` の単位は「`0` の字の幅」**。だから必要量は
    `measure(text) / measure("0")` で出る＝**文字数ではない**。B-046 は
    「DMS は 27 文字だから幅 27」と決めたが、実際に要るのは **24.17 単位**で、
    3 文字ぶん過大に予約していた（B-057 で判明）。逆に ID 欄は「11 文字だから
    幅 11」で**足りなかった**（`asaminami24` は 11.67 単位要る）。同じ取り違えが
    両方向に出た。

    🔴 **なぜ実物の欄を測らないか**＝測ろうとして 2 度失敗した。ttk の Entry は
    フォントを**スタイル側**に持つので `cget("font")` は空を返し、`sv_ttk` の
    テーマ適用は**このリポジトリでは間欠的に失敗する**（`conftest.set_theme` が
    リトライしているのはそのため）。結果、同じ検査が単独では緑・一括では赤に
    なった。⇒ **書体を自分で列挙して比だけを見る**＝Tk の状態に一切依存しない。
    """
    import tkinter.font as tkfont

    worst = 0.0
    for family in _SHIPPING_FAMILIES:
        for size in _SHIPPING_SIZES:
            font = tkfont.Font(root=root, family=family, size=size)
            if font.actual("family").lower() != family.lower():
                continue                     # その環境に無い書体
            worst = max(worst, font.measure(text) / font.measure("0"))
    assert worst > 0, "測れる書体が 1 つも無い（環境が想定外）"
    return worst


def test_the_coordinate_fields_are_wide_enough_for_dms():
    """座標欄が **DMS 表記を切らない**幅であること（B-046 / B-057）。

    ⚠️ **値のゲートでは捕まらない**＝欄はスクロールするので `get()` は完全な値を
    返し、`winfo_reqwidth` も要求どおり。**切れているのは描画された字だけ**。

    🔑 **`coords.DISPLAY_WIDTH_CHARS` と比べてはいけない**＝それは*守る対象*で、
    基準にすると定数を 21 に戻しても緑のままになる（実測で確認した＝壊れ方①）。
    ここでは**書体から必要量を計算**して、定数がそれを満たすかを見る。

    🔴 **文字数で測るのはやめた**（2026-08-12・B-057）＝旧版は「DMS は 27 文字
    だから `width` 27 以上」を要求していたが、`width` の単位は `0` の字の幅で、
    DMS は `°` `'` `"` `.` と細い字ばかり。**実際に要るのは 24.17 単位**で、
    3 文字ぶん無駄に予約させていた（壊れ方③＝間違ったものを要求するゲート）。
    その 3 文字が ID 欄の見切れ（B-057）を直す原資になった。

    ⚠️ **南緯・西経も見る**＝半球記号が `S` / `W` に変わり、**`W` はこの文字列で
    一番太い字**。`N/E` だけ見ていると 24 単位でも通ってしまい、実際に踏んだ。
    """
    pytest.importorskip("tkinter")
    from core import coords as _coords

    root = make_themed_root()
    root.withdraw()
    try:
        for lat, lon, label in ((34.8, 132.6, "北緯・東経"),
                                (-34.8, -132.6, "南緯・西経")):
            text = _coords.format_dms(lat, lon)
            need = _required_width_units(root, text)
            assert need <= _coords.DISPLAY_WIDTH_CHARS, (
                f"座標欄が DMS（{label}）`{text}` を切る＝"
                f"必要 {need:.2f} 単位 > 幅 {_coords.DISPLAY_WIDTH_CHARS} 単位。"
                "`coords.DISPLAY_WIDTH_CHARS` を上げること。"
            )
    finally:
        root.destroy()


def test_every_coordinate_field_has_the_same_width():
    """**座標を入れる欄は、どの窓でも同じ幅**であること（B-046 / B-057）。

    ⚠️ **実際のウィジェットを見る**＝定数を参照しているかをソースで確かめるのでは
    なく、**組み上がった欄の `width`** を読む。宣言し忘れ（＝Tk 既定の 20 が
    黙って下限になる）は、定数の参照を見る検査では捕まらない。

    🔴 **ランチャーが実際にそうなっていた**（2026-08-12 発見）。他の 4 窓は
    `DISPLAY_WIDTH_CHARS` に揃っていたのに、**座標を実際に打つランチャーだけが
    幅を宣言しておらず**、Tk 既定の 20 文字が下限になって **150% 表示で DMS の
    末尾が切れていた**。`fill="x"` で伸びるので 100% では足りており、
    **「普段は足りている」が「宣言していない」を隠していた**。
    ⇒ 伸びるかどうかと、下限を宣言するかは別の話。

    ⚠️ **読み取り専用の凍結帯も対象**（条件探索）＝そこが切れると「何を固定した
    のか分からない」＝帯の意味が消える。
    """
    pytest.importorskip("tkinter")
    from core import coords as _coords
    from views.batch_builder import BatchBuilderWindow
    from views.launcher import SimLauncher
    from views.multihop import MultiHopWindow
    from views.scenario import ScenarioWindow

    want = _coords.DISPLAY_WIDTH_CHARS
    root = make_themed_root()
    root.withdraw()
    try:
        found: dict[str, list[int]] = {}

        app = SimLauncher(root, lambda _t: None)
        found["ランチャー"] = [int(app.entries[k].cget("width"))
                              for k in ("start", "end")]

        win = BatchBuilderWindow(root, _params())
        found["複数経路"] = [int(win._row_entries[0][c].cget("width"))
                            for c in (1, 2)]

        mh = MultiHopWindow(root, _params())
        found["中継経路"] = [int(e.cget("width")) for e in
                            _entries_bound_to(mh, [v["coord"] for v in mh._wp_vars])]

        scn = ScenarioWindow(root, _params())
        found["条件探索"] = [int(e.cget("width")) for e in
                            _entries_bound_to(scn, [scn._tx_var, scn._rx_var])]

        for name, widths in found.items():
            assert widths, f"{name}: 座標欄が 1 つも見つからない（探し方が古い）"
            assert all(w == want for w in widths), (
                f"{name} の座標欄が他の窓と違う幅（{widths} ≠ {want}）。"
                "`coords.DISPLAY_WIDTH_CHARS` から取ること＝**幅を宣言しない**と "
                "Tk 既定の 20 文字が下限になり、150% で DMS の末尾が切れる。"
            )
    finally:
        root.destroy()


def test_the_path_id_cell_shows_the_ids_the_validator_accepts():
    """ID 欄が、検証の通す ID を**切らずに描ける**こと（B-057）。

    ⚠️ **値のゲートでは捕まらない**＝欄はスクロールするので `get()` は完全な値を
    返す。切れているのは描画された字だけで、実害は「短く見える」ではなく
    **`asaminami24` が `asaminami2` という別の ID として読めてしまう**こと。

    🔴 **保証は 2 段ある**（比例フォントなので 1 本にはできない）。`_PATH_ID_RE` が
    通すのは `[A-Za-z0-9_-]` で、`W` / `M` は `0` の **1.83 倍**の幅がある＝
    「N 文字ぶんの幅」は N 文字を収める保証にならない。いまの幅での実力：

      - **どの字形でも `_ANY_GLYPH_LEN` 文字までは読める**（最悪 = `W` / `M` 連打）
      - **通常幅の字なら上限 `MAX_TYPED_ID_LEN` ちょうどまで読める**
        （`asaminami24` = 11.67 単位 / `hatsukaichi` = 10.00 単位 / 数字 = 11.00 単位）

    ⇒ **大文字ばかりの長い ID は今も切れる**（ISSUES.md の B-057 に残留として記録）。
    1 本にするには ID 欄を 21 単位にする（幅が無い）か等幅にする（書体を変えない
    方針で見送り）しかない。
    """
    pytest.importorskip("tkinter")
    from views.batch_builder import BatchBuilderWindow

    width = BatchBuilderWindow._WIDTHS[1]
    limit = b.MAX_TYPED_ID_LEN
    root = make_themed_root()
    root.withdraw()
    try:
        # ① どの字形でも読めると約束する長さ。⚠️ **上限より短いのは欠陥ではなく
        #    比例フォントの帰結**＝上げるには幅を増やすしかない。
        any_glyph_len = 7
        assert any_glyph_len <= limit, "保証が上限を超えている＝記録が古い"
        for ch in "WMO":
            text = ch * any_glyph_len
            need = _required_width_units(root, text)
            assert need <= width, (
                f"ID 欄が最悪字形 {any_glyph_len} 文字 `{text}` を切る"
                f"（必要 {need:.2f} 単位 > 幅 {width} 単位）。"
            )
        # ② 通常幅の字なら上限ちょうどまで（発見の発端が `asaminami24`）。
        for text in ("asaminami24", "hatsukaichi", "0" * limit, "_" * limit):
            need = _required_width_units(root, text)
            assert need <= width, (
                f"ID 欄が `{text}`（{len(text)} 文字）を切る＝**別の ID として"
                f"読めてしまう**（B-057・必要 {need:.2f} 単位 > 幅 {width} 単位）。"
                "ID 欄を広げるか、原資として `coords.DISPLAY_WIDTH_CHARS` を"
                "見直すこと。"
            )
        # ③ **この検査が落ち得ること**を確かめる（壊れ方①）＝幅を超える ID は
        #    必ず「切れる」と出ること。ここが緑だと上の主張は何も検査していない。
        over = _required_width_units(root, "W" * (limit + 4))
        assert over > width, (
            "幅を大きく超える ID でも「収まる」と出る＝この検査は常に緑になる。"
        )
    finally:
        root.destroy()


def test_the_id_column_is_wider_than_the_limit_it_must_show():
    """ID 列の幅が、通す上限より**広い**こと（B-057 の**補助**の網）。

    🔑 **等号ではない**＝Tk の `width` は*平均*文字幅の単位なので、「11 文字ぶんの
    幅」は 11 文字を収める保証にならない（`asaminami24` が幅 11 で切れた）。
    ⇒ 幅は上限より**広く**取る必要があり、どれだけ広ければ足りるかはフォント
    依存で静的には決まらない。

    ⚠️ **主たる守りは上の描画検査**（実際に ID を入れて切れないことを見る）。ここは
    「上限だけ上げて欄を広げ忘れた」形を名指しする補助で、単独では欠陥を検出しない。
    **主たる守りと補助を取り違えないこと**（test_window_fit.py の minsize と同じ扱い）。
    """
    from views.batch_builder import BatchBuilderWindow
    assert BatchBuilderWindow._WIDTHS[1] > b.MAX_TYPED_ID_LEN, (
        f"ID 列の幅（{BatchBuilderWindow._WIDTHS[1]}）が "
        f"batch.MAX_TYPED_ID_LEN（{b.MAX_TYPED_ID_LEN}）を上回っていない"
        "＝平均文字幅の単位なので、上限ちょうどの幅では上限の文字数を収められない。"
    )


def test_the_verdict_column_still_fits_its_own_text():
    """判定列が、そこに入る語（`OK` / `NG` / `ERR`）を切らないこと。

    ID を広げる原資をこの列から出した（6 → 3・実測 24px）ので、**削りすぎて
    いないことを対で置く**＝原資を出した側が新しい見切れになったら本末転倒。

    ⚠️ **語を実装のソースから正規表現で拾ってはいけない**＝実装中に踏んだ。
    `_set_row_verdict` の docstring に説明として `"ERROR"` と書いてあるため、
    ソース走査は**画面に出ない語**を要求量として拾い、幅 5 を要求して落ちた
    （壊れ方③＝間違ったものを要求するゲート）。⇒ **製品を動かして、実際に
    貼られた文字列**を測る。

    ⚠️ フォントも自分で測らない（上のゲートと同じ理由）＝**幅指定を外したときの
    自然な要求幅**と、いまの列幅での要求幅を比べる。

    🔴 **表示スケールを全部回すこと**＝実装中、この列を 3 まで詰めて 100% では
    緑だった。`ERR` が切れるのは **125% と 150% だけ**で、96dpi しか見ない検査は
    それを通す（見切れが実機でだけ出続けた B-021 と同じ形）。
    """
    pytest.importorskip("tkinter")
    from views import theme
    from views.batch_builder import BatchBuilderWindow

    prev = i18n._lang
    for lang in ("ja", "en"):
        for dpi in (96, 120, 144):
            root = make_themed_root()
            root.withdraw()
            try:
                i18n.set_lang(lang)
                theme.apply_fonts(root, dpi=dpi)
                win = BatchBuilderWindow(root, _params())
                root.update_idletasks()
                pid = win._row_entries[0][0].get()
                lbl = win._verdict_label(win._row_frames[0])
                assert lbl is not None, "判定ラベルが無い（行の組み立てが変わった）"
                fixed = lbl.winfo_reqwidth()       # 列幅で決まる確保量
                for status in ("OK", "NG", "ERROR"):
                    win._set_row_verdict(pid, status)
                    lbl.update_idletasks()
                    text = lbl.cget("text")
                    assert text, f"判定 `{status}` が画面に出ていない"
                    lbl.config(width=0)            # 幅指定を外した自然な要求量
                    lbl.update_idletasks()
                    natural = lbl.winfo_reqwidth()
                    lbl.config(width=BatchBuilderWindow._WIDTHS[-3])
                    assert natural <= fixed, (
                        f"[{lang}/{dpi}dpi] 判定列が `{text}`（{status} の表示）を"
                        f"切る（必要 {natural}px / 列 {fixed}px）"
                        "＝ID を広げるために削りすぎた。"
                    )
            finally:
                i18n.set_lang(prev)
                theme.apply_fonts(root, dpi=96)
                root.destroy()


def _load_project_into(app, doc):
    """`_on_open_project` の**読み込んだ後**の処理だけを起こす（ファイル選択は経ない）。"""
    app._project = doc
    app._apply_sim_config(doc.params)
    app._offer_project_to_open_windows()


def _notice_take_button(win):
    """お知らせの帯の「内容を取り込む」ボタン（無ければ None）。"""
    bar = getattr(win, "_notice_bar", None)
    if bar is None or not bar.winfo_exists():
        return None
    for w in _walk(bar):
        if w.winfo_class() == "TButton" and str(w.cget("text")) == i18n.t(
                "proj_notice_take"):
            return w
    return None


def test_loading_a_project_does_not_touch_open_windows_until_asked():
    """プロジェクトを読み込んでも、**押すまで**窓の中身は変わらないこと（I-061）。

    🔴 **これがこの項目の芯**＝要望は「閉じずに反映してほしい」だったが、黙って
    入れ替えると凍結方式（見えている値で実行する）が壊れる。⇒ 帯で知らせ、
    **押したときだけ**差し替える。
    ⚠️ 「窓が閉じないこと」だけを見るゲートでは足りない＝黙って書き換える実装でも
    緑になる。**押す前と押した後の両方**を見る。
    """
    pytest.importorskip("tkinter")
    from report import project
    root = make_themed_root()
    root.withdraw()
    try:
        app   = _launcher(root)
        batch = app.ensure_batch_window()
        batch._row_entries[0][0].delete(0, "end")
        batch._row_entries[0][0].insert(0, "mine")     # 作業中の行

        rows = [b.PathRow(path_id="fromfile", lat_tx=34.5, lon_tx=132.4,
                          lat_rx=34.6, lon_rx=132.5, h_tx=2.0, h_rx=2.0)]
        _load_project_into(app, project.ProjectDoc(params=dict(config.DEFAULT_CONFIG),
                                                   batch_rows=rows))

        assert batch.winfo_exists(), "窓が閉じられた（閉じないと決めた）"
        assert batch._row_entries[0][0].get() == "mine", (
            "押していないのに中身が差し替わった（凍結方式が壊れている）"
        )
        btn = _notice_take_button(batch)
        assert btn is not None, "取り込みの帯が出ていない"

        btn.invoke()
        root.update()
        assert batch._row_entries[0][0].get() == "fromfile", "押しても取り込まれない"
        assert getattr(batch, "_notice_bar", None) is None, (
            "取り込んだのに帯が残っている（誘い続ける）"
        )
    finally:
        root.destroy()


def test_a_stale_notice_never_survives_the_next_project():
    """**前の読込の帯が、次の読込をまたいで生き残らない**こと（Codex P1）。

    帯のクロージャは*読んだときの*プロジェクトの中身を掴んでいる。A の帯を出した
    まま、その節を持たない B を読み、あとから押せると **A の行が B へ入る**（次の
    保存で混在する）。⇒ 消す契機は「新しい帯を出すとき」ではなく「前の話が
    終わったとき」＝**節の有無にかかわらず先に消す**。
    """
    pytest.importorskip("tkinter")
    from report import project
    root = make_themed_root()
    root.withdraw()
    try:
        app   = _launcher(root)
        batch = app.ensure_batch_window()
        batch._row_entries[0][0].delete(0, "end")
        batch._row_entries[0][0].insert(0, "mine")

        rows_a = [b.PathRow(path_id="from_a", lat_tx=34.5, lon_tx=132.4,
                            lat_rx=34.6, lon_rx=132.5, h_tx=2.0, h_rx=2.0)]
        _load_project_into(app, project.ProjectDoc(params=dict(config.DEFAULT_CONFIG),
                                                   batch_rows=rows_a))
        assert _notice_take_button(batch) is not None, "前提: A の帯が出ていない"

        # B＝バッチの節を持たないプロジェクト（帯は出ない側）。
        _load_project_into(app, project.ProjectDoc(params=dict(config.DEFAULT_CONFIG),
                                                   batch_rows=None))
        assert _notice_take_button(batch) is None, (
            "A の帯が残っている（押すと A の行が B に入る）"
        )
        assert batch._row_entries[0][0].get() == "mine", "画面の内容が勝手に変わった"
    finally:
        root.destroy()


def test_no_notice_when_the_project_has_no_section_for_that_window():
    """**節を持たないファイルでは帯を出さない**こと（I-061）。

    `None`＝「その窓の情報を持たない」であって、空にする指示ではない
    （`project.py` の約束）。出してしまうと「取り込む」が**行の全消し**になる。
    """
    pytest.importorskip("tkinter")
    from report import project
    root = make_themed_root()
    root.withdraw()
    try:
        app   = _launcher(root)
        batch = app.ensure_batch_window()
        _load_project_into(app, project.ProjectDoc(params=dict(config.DEFAULT_CONFIG),
                                                   batch_rows=None))
        assert _notice_take_button(batch) is None, (
            "節が無いのに帯を出した（押すと行が全部消える）"
        )
    finally:
        root.destroy()


# ============================================================
# 3. 判定が消える引き金は「値が変わったこと」だけ（B-058）
# ============================================================
# 実行結果は、その結果を生んだ入力でなくなった行からは消える（I-041）。
# ⚠️ **消す条件を「キーが押された」で書くと、→ ← Tab Shift Home Ctrl でも消える**＝
# 実行直後に矢印キーで表を眺めただけで結果が読めなくなる。しかも消えるのは触った行
# だけなので、**表が「一部だけ判定がある」状態**になり、実行し損ねた行と区別が付かない。
# 実際に I-017 のスクリーンショットで 1 行だけ判定が空になって発覚した。
def test_a_verdict_is_not_written_to_a_row_edited_during_the_run():
    """**実行中に編集された行へは、返ってきた判定を書かない**こと（Codex P1）。

    止めているのは実行ボタンだけなので、計算中も表は編集できる。ID だけで行を
    引くと、**編集後の行に、編集前の入力で出た判定**が「その行の結果」として
    貼られる。⚠️ 計算は開始時に凍結した行で走るので**成果物は正しい**＝守るのは
    画面の側。規則は B-058／B-059 と同じ 1 本＝結果は、それを生んだ入力が変わった
    時点で結果でなくなる。
    """
    pytest.importorskip("tkinter")
    from views.batch_builder import BatchBuilderWindow

    root = make_themed_root()
    root.withdraw()
    try:
        win = BatchBuilderWindow(root, _params())
        win.update()
        pid = win._row_entries[0][0].get()
        win._clear_verdicts()          # 実行の開始時＝ここで入力を控える
        win.update()

        # 実行中に座標を書き換える（ID は変えない＝引き当ては成功する側）。
        entry = win._row_entries[0][1]
        entry.delete(0, "end")
        entry.insert(0, "35.000000, 135.000000")
        win.update()

        win._set_row_verdict(pid, "OK")
        win.update()
        lbl = win._verdict_label(win._row_frames[0])
        assert lbl is not None and lbl.cget("text") == "", (
            "編集後の行に、編集前の入力で出た判定が貼られた"
        )
    finally:
        root.destroy()


def test_a_verdict_is_written_when_the_row_is_untouched():
    """触っていない行には従来どおり書けること（過剰な抑止も欠陥）。"""
    pytest.importorskip("tkinter")
    from views.batch_builder import BatchBuilderWindow

    root = make_themed_root()
    root.withdraw()
    try:
        win = BatchBuilderWindow(root, _params())
        win.update()
        pid = win._row_entries[0][0].get()
        win._clear_verdicts()
        win._set_row_verdict(pid, "OK")
        win.update()
        lbl = win._verdict_label(win._row_frames[0])
        assert lbl is not None and lbl.cget("text") == "OK", "正常な行に判定が返らない"
    finally:
        root.destroy()


@pytest.mark.parametrize("key,column,changes", [
    ("Right",     0, False),   # 移動
    ("Home",      0, False),
    ("Shift_L",   0, False),   # 修飾
    ("Control_L", 0, False),
    ("BackSpace", 0, True),    # 編集
    ("5",         4, True),
])
def test_verdict_survives_keys_that_do_not_change_the_row(key, column, changes):
    pytest.importorskip("tkinter")
    from views.batch_builder import BatchBuilderWindow

    root = make_themed_root()
    root.withdraw()
    try:
        win = BatchBuilderWindow(root, _params())
        win.update()
        win._set_row_verdict(win._row_entries[0][0].get(), "OK")
        win.update()
        lbl = win._verdict_label(win._row_frames[0])
        assert lbl is not None and lbl.cget("text") == "OK", "前提: 判定が入っていない"

        entry = win._row_entries[0][column]
        entry.focus_force()
        entry.icursor("end")
        win.update()
        before = entry.get()
        entry.event_generate(f"<KeyPress-{key}>", when="now")
        entry.event_generate(f"<KeyRelease-{key}>", when="now")
        win.update()

        assert (entry.get() != before) is changes, (
            f"前提が崩れている: {key} で値が{'変わるはず' if changes else '変わらないはず'}"
        )
        if changes:
            assert lbl.cget("text") == "", f"{key} で行を変えたのに判定が残っている"
        else:
            assert lbl.cget("text") == "OK", (
                f"{key} は値を変えていないのに判定が消えた（結果が読めなくなる）"
            )
    finally:
        root.destroy()


# ------------------------------------------------------------
# 3b. 中継経路も同じ規則で消える（B-059＝B-058 の裏面）
# ------------------------------------------------------------
# 🔑 **規則は 1 本＝結果は、それを生んだ入力が変わった時点で結果でなくなる。**
# 2 つの窓は同じ規則を逆向きに破っていた＝複数経路は**キーを押しただけで消え**
# （B-058＝消しすぎ）、中継経路は**入力を変えても残った**（B-059＝消さなすぎ。
# 消す口が実行の開始時にしか呼ばれていなかった）。
# ⚠️ **中継はバッチと違い、1 つの結果の入力が 2 つの表にまたがる**＝区間 k は
# 地点 k・k+1（座標/高さ）と区間 k（周波数/利得）と**凍結した共通設定**から出る。
def _mh_win(root):
    from views.multihop import MultiHopWindow
    win = MultiHopWindow(root, _params())
    win.update()
    win._on_add_point()                     # TX → R1 → RX＝2 区間にする
    win.update()
    for index in (1, 2):
        win._show_hop_result(index, _FakeHopResult())
    win.update()
    return win


class _FakeHopResult:
    """`batch.PathResult` のうち、区間表が読む 2 つだけ。"""
    status = "OK"

    class result:
        p_rx = -70.0
        actual_margin = 5.0


def _hop_texts(win):
    return [c["status"].cget("text") for c in win._hop_result_labels]


@pytest.mark.parametrize("where,changes", [
    ("wp_coord",  True),    # 地点の座標＝前後 2 区間の入力
    ("wp_height", True),    # 地点の高さ
    ("hop_freq",  True),    # 区間の周波数
    ("wp_name",   False),   # 地点名＝結果の数字を作らない
    ("route_id",  False),   # 経路 ID＝識別子であって入力ではない
    ("note",      False),   # 備考
])
def test_hop_results_clear_only_when_their_own_input_changes(where, changes):
    """区間結果は、**それを生んだ入力**が変わったときだけ消えること（B-059）。

    ⚠️ 消えない側（`wp_name` / `route_id` / `note`）を必ず一緒に測る＝**全部消す
    実装でも「消える」側の検査だけなら緑になる**。B-058 が示したとおり、この規則は
    消しすぎでも壊れる。
    """
    pytest.importorskip("tkinter")
    root = make_themed_root()
    root.withdraw()
    try:
        win = _mh_win(root)
        assert _hop_texts(win) == ["OK", "OK"], "前提: 区間結果が入っていない"

        if where == "wp_coord":
            win._wp_vars[1]["coord"].set("34.500000, 132.500000")
        elif where == "wp_height":
            win._wp_vars[1]["height"].set("99.9")
        elif where == "hop_freq":
            win._hop_vars[0]["freq"].set("5800")
        elif where == "wp_name":
            win._wp_vars[1]["name"].set("R9")
        elif where == "route_id":
            win._route_id.set("route2")
        else:
            win._note.set("メモ")
        win.update()

        if changes:
            assert "" in _hop_texts(win), f"{where} を変えたのに区間結果が残っている"
        else:
            assert _hop_texts(win) == ["OK", "OK"], (
                f"{where} は結果の数字を作らないのに区間結果が消えた（消しすぎ）"
            )
    finally:
        root.destroy()


def test_a_moved_point_clears_both_of_its_sections():
    """地点を 1 つ動かしたら、**その前後 2 区間とも**消えること（B-059）。

    🔑 **ここが中継固有の落とし穴**＝欄と区間を 1 対 1 で配線すると、必ず片側が
    残る（区間 k の入力は地点 k と k+1 の**両方**）。R1 は区間 1 の終点であり
    区間 2 の始点なので、動かせば 2 つとも古くなる。
    """
    pytest.importorskip("tkinter")
    root = make_themed_root()
    root.withdraw()
    try:
        win = _mh_win(root)
        win._wp_vars[1]["coord"].set("34.500000, 132.500000")   # R1＝真ん中
        win.update()
        assert _hop_texts(win) == ["", ""], (
            f"前後 2 区間のうち片方しか消えていない: {_hop_texts(win)}"
        )
    finally:
        root.destroy()


@pytest.mark.parametrize("change,expected", [
    ("tx_coord", ["", "OK"]),    # 送信点＝区間 1 の入力にしか入らない
    ("rx_coord", ["OK", ""]),    # 受信点＝区間 2 だけ
    ("hop1_gain", ["", "OK"]),   # 区間 1 の利得＝その区間だけ
])
def test_only_the_affected_sections_clear(change, expected):
    """**関係ない区間の結果は残る**こと（B-059＝消しすぎない側の芯）。

    🔑 **この検査が無いと「どれか変わったら全部消す」実装が緑になる**。上の
    `test_a_moved_point_clears_both_of_its_sections` は真ん中の点を動かすので
    2 区間とも消えるのが正解＝**全消し実装と区別が付かない**。端の点と区間ごとの
    欄で測って初めて、控えとの突き合わせが働いていることが分かる。
    """
    pytest.importorskip("tkinter")
    root = make_themed_root()
    root.withdraw()
    try:
        win = _mh_win(root)
        if change == "tx_coord":
            win._wp_vars[0]["coord"].set("34.100000, 132.100000")
        elif change == "rx_coord":
            win._wp_vars[2]["coord"].set("34.900000, 132.900000")
        else:
            win._hop_vars[0]["gain_tx"].set("20")
        win.update()
        assert _hop_texts(win) == expected, (
            f"{change}: 関係ない区間まで消えた（または消し損ねた）"
        )
    finally:
        root.destroy()


def test_refreshing_common_settings_clears_results_only_when_they_differ():
    """↻ は、**共通設定が実際に変わったときだけ**区間結果を消すこと（B-059）。

    ⚠️ **`repr(SimParams)` で指紋を取ると、押すたびに消える**＝dataclass では
    ないので既定の `repr` はオブジェクトの番地になり、中身が同じでも別物に見える
    （＝「毎回鳴る」壊れ方）。だから**同じ設定で押した場合**を必ず測る。
    """
    pytest.importorskip("tkinter")
    from views.multihop import MultiHopWindow

    root = make_themed_root()
    root.withdraw()
    try:
        cfg = dict(config.DEFAULT_CONFIG)
        win = MultiHopWindow(root, _params(), config_provider=lambda: dict(cfg))
        win.update()
        win._on_add_point()
        win.update()
        for index in (1, 2):
            win._show_hop_result(index, _FakeHopResult())
        win.update()

        win._refresh_from_launcher()        # 同じ設定のまま押した
        win.update()
        assert _hop_texts(win) == ["OK", "OK"], (
            "共通設定が変わっていないのに ↻ で区間結果が消えた（毎回鳴る）"
        )

        cfg["h_tx"] = str(float(cfg["h_tx"]) + 5.0)
        win._refresh_from_launcher()        # 中身を変えて押した
        win.update()
        assert _hop_texts(win) == ["", ""], (
            "共通設定を取り込み直したのに古い区間結果が残っている"
        )
    finally:
        root.destroy()


# ============================================================
# 単位の括弧を直書きしない（2.8RC1）
# ============================================================
def test_unit_parentheses_are_not_hardcoded():
    """⛔ `名前 + " (dBm)"` の直書きを禁じる（括り方は i18n の `unit_wrap`）。

    🔴 **2.8 は「共通設定のラベルの括弧を全角に統一した」と宣言したのに、
    条件探索の画面とレポートだけ半角で残っていた**（画面 2 か所・レポート 3 か所）。
    同じ量の単位が、窓によって `周波数（MHz）` と `周波数 (MHz)` の 2 通りで出る。
    ⇒ 宣言は**口の全数を数えないと効かない**（[[feedback-user-examples-are-classes]]）。

    ⚠️ 括弧の字は**言語ごとに違う**（ja は全角・前の空白なし／en は半角・前に空白）
    ので、直書きは「日本語を直すと英語が壊れる」形でもある。

    🔴 **その宣言をした版の中で、さらに 2 口を数え落としていた**（2026-08-17・
    独立レビュー）＝条件探索の**比較レポート**の 10 行と条件行が
    `+ (f" ({unit})" if unit else "")` の形で残り、日本語だけ半角で出ていた。
    **最初のこの検査が正規表現で `" (dBm)"` の形しか見ていなかったから**＝
    単位を変数にした瞬間、検査からは消えて見える。⇒ **AST で「足している相手が
    括弧で始まって括弧で終わる文字列か」を見る**（中身が定数か差し込みかを問わない）。
    """
    import ast as _ast
    from pathlib import Path as _Path

    def _literal_text(node) -> "str | None":
        """定数文字列と f 文字列を、差し込みを `{}` に潰した字として読む。"""
        if isinstance(node, _ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, _ast.JoinedStr):
            out = ""
            for part in node.values:
                if isinstance(part, _ast.Constant) and isinstance(part.value, str):
                    out += part.value
                else:
                    out += "{}"
            return out
        return None

    def _looks_like_a_unit_suffix(node) -> bool:
        text = _literal_text(node)
        return bool(text) and text.strip().startswith("(") and text.strip().endswith(")")

    root = _Path(__file__).resolve().parents[1]
    bad: list[str] = []
    for d in ("views", "report"):
        for path in (root / d).glob("*.py"):
            tree = _ast.parse(path.read_text(encoding="utf-8"))
            for node in _ast.walk(tree):
                if not (isinstance(node, _ast.BinOp)
                        and isinstance(node.op, _ast.Add)):
                    continue
                # `名前 + " (m)"` と `名前 + (f" ({unit})" if unit else "")` の両方
                right = node.right
                candidates = ([right.body, right.orelse]
                              if isinstance(right, _ast.IfExp) else [right])
                if any(_looks_like_a_unit_suffix(c) for c in candidates):
                    bad.append(f"{path.relative_to(root)}:{node.lineno}: "
                               f"{_ast.unparse(node)[:90]}")
    assert not bad, (
        "単位の括弧が直書きされている（report_scenario.with_unit を使う）:\n"
        + "\n".join(bad)
    )
