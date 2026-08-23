"""
tests/test_window_fit.py
========================
**全ウィンドウ横断**の見切れゲート。

なぜ窓ごとの手書きテストでは駄目だったか
----------------------------------------
見切れはこのプロジェクトで最も繰り返している不具合クラス（B-002 / B-007 /
I-000 / I-023 / I-024）で、そのつど「その窓だけ」を実測追従に直し、「その窓
だけ」のテストを足してきた。結果、

  - **次の窓**（地図ウィンドウは 2.5b2 までゲートが 1 本も無く、`geometry("900x680")`
    のリテラルのままだった）
  - **次の増え方**（起動時しか測らない＝バッチは CSV インポートで列が広がっても
    窓は広がらないままだった）

で必ず再発した。つまり欠けていたのは個々の修正ではなく、**新しい窓・新しい
増え方が自動的に検査対象になる仕組み**。ここでは 3 つを固定する：

  1. **登録された全窓**が、開いた時点で中身を収めていること。
  2. **中身が増える操作のあと**も収めていること（窓ごとに「増やし方」を書く）。
  3. **登録漏れが起きないこと**＝`views/` で Toplevel を作る場所を静的に洗い出し、
     登録も除外もされていない窓があればここが落ちる（新しい窓を足した人が
     テストを書き忘れても気づける＝[[feedback-promote-recurring-checks]] の昇格）。

⚠️ 測定は必ず `make_themed_root()` で行う（素の Tk 既定フォントは実機より小さく、
実物より狭い前提でゲートが緑になる。2.5b2 に実際そうなっていた）。
"""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import i18n
from core import simulation as sim
from conftest import make_themed_root
from views import window_fit

_VIEWS_DIR = os.path.join(os.path.dirname(__file__), "..", "views")

_PARAMS = {
    "start": "34.5429, 132.4118", "end": "34.5389, 132.4050",
    "h_tx": "30.0", "h_rx": "10.0", "freq": "2400.0", "p_tx": "20.0",
    "gain_tx": "3.0", "gain_rx": "3.0", "sens": "-85.0", "veg_h": "10.0",
    "k_factor": "10.0", "samples": "50", "diff_method": "deygout",
    "env_type": "los", "rain_rate": "0.0",
}


# ============================================================
# 窓のレジストリ（新しい窓はここへ足す）
# ============================================================
def _open_launcher(root):
    from views.launcher import SimLauncher
    app = SimLauncher(root, lambda _t: None)
    return root, app


def _open_batch(root):
    from views.batch_builder import BatchBuilderWindow
    win = BatchBuilderWindow(root, sim.SimParams(_PARAMS))
    return win, win


def _open_scenario(root):
    from views.scenario import ScenarioWindow
    win = ScenarioWindow(root, sim.SimParams(_PARAMS))
    return win, win


def _open_graph(root):
    """グラフ窓（B-024 で matplotlib の窓から Toplevel になり、対象窓が 4→5 へ）。

    ⚠️ **逃げ道（B-021②）が入るまでこの登録はできなかった**＝入らないときの
    受け皿が無いまま対象を増やすと「ゲートに入れた瞬間に赤」を自分で作る。
    """
    import numpy as np
    from views.graph import show_graph
    win = show_graph(root, sim.SimParams(_PARAMS), np.zeros(int(_PARAMS["samples"])))
    return win, win


def _open_multihop(root):
    """中継経路窓（A-3 で 6 つ目の登録窓になった）。"""
    from views.multihop import MultiHopWindow
    win = MultiHopWindow(root, sim.SimParams(_PARAMS))
    return win, win


def _open_map(root, monkeypatch):
    """地図ウィンドウ（`TkinterMapView` はフェイクに差し替える）。

    差し替えるのは**タイル取得のためにネットワークへ出る部品だけ**で、見切れの
    起点であるモードバー・各モードのパネル・ステータス行は本物を組み立てる。
    """
    import tkinter as tk
    from tkinter import ttk
    from types import SimpleNamespace

    class _FakeMap(ttk.Frame):
        def __init__(self, master, **_kw):
            super().__init__(master)
            self.canvas = tk.Canvas(self, width=200, height=150)
            self.canvas.pack(fill="both", expand=True)
            self.running = False

        def set_tile_server(self, *a, **k): pass
        def set_position(self, *a, **k): pass
        def set_zoom(self, *a, **k): pass
        def set_address(self, *a, **k): pass
        def add_left_click_map_command(self, *a, **k): pass
        def set_polygon(self, *a, **k): return SimpleNamespace(delete=lambda: None)
        def set_marker(self, *a, **k): return SimpleNamespace(delete=lambda: None)
        def set_path(self, *a, **k): return SimpleNamespace(delete=lambda: None)
        def convert_canvas_coords_to_decimal_coords(self, *a, **k): return (35.0, 139.0)
        def get_position(self): return (35.0, 139.0)
        def get_zoom(self): return 8

    import views.map_window as mw
    monkeypatch.setattr(mw, "TkinterMapView", _FakeMap)
    win = mw.MapWindow(root, {"proxy_url": ""})
    return win._win, win


# name -> (窓を開く関数, 中身を増やす操作 or None)
#
# 「中身を増やす操作」は **その窓で実際に中身が増える経路**を書く（2 の検査）。
# None は「開いたあとに中身が増える経路を持たない窓」の明示。
_WINDOWS = {
    "launcher": (_open_launcher, None),
    "batch":    (_open_batch,    lambda win, owner: _grow_batch(owner)),
    "scenario": (_open_scenario, lambda win, owner: _grow_scenario(owner)),
    "map":      (_open_map,      None),
    "graph":    (_open_graph,    None),
    "multihop": (_open_multihop, lambda win, owner: _grow_multihop(owner)),
}


def _grow_multihop(win) -> None:
    """地点を上限まで足す＝表が縦に伸びる経路（中継経路の「増え方」）。"""
    from report import multihop as mh
    while len(win._wp_vars) < mh.MAX_HOPS + 1:
        win._add_waypoint()


def _grow_batch(win) -> None:
    """CSV インポート（**長い備考**で列が広がる）。

    ⚠️ ここで `_fit_width_to_content()` を呼んではいけない。**製品の経路**
    （`replace_rows` ＝ インポート本体）を叩き、その中で窓が追従することを見る。
    テスト側が fit を呼ぶと、製品が呼び忘れていても緑になる。

    2.5b2 まで、起動後に列が広がっても窓は広がらなかった（`_fit_initial_width`
    が `__init__` からしか呼ばれていなかった）。
    """
    win.replace_rows([[
        "path-with-a-very-long-identifier",
        "34.542900, 132.411800", "34.538900, 132.405000",
        "30", "10", "2400", "3", "3",
        "非常に長い備考テキスト：列幅同期がここまで列を広げる",
    ]])


def _grow_scenario(win) -> None:
    """比較条件を上限（5 列）まで足す＝実機で条件 5 が見切れた経路（I-024）。"""
    from core import scenario as scn
    while len(win._cmp_cols) < scn.MAX_COMPARE_CONDITIONS:
        win._add_condition_column()


# ============================================================
# 1・2: 全窓が中身を収めていること
# ============================================================
def _assert_fits(win, label: str) -> None:
    """窓が「測った必要量」を実際に確保しているか、**入らないなら手が届く**こと。

    ⚠️ **かつてここには免除条項があった（B-021 で一度これに騙された）**＝
    `size >= min(need, lim)` と書いてあり、「画面に入らないのだから仕方ない」で
    緑になった。実機で 33px 溢れて最下段のボタン列が数 px に潰れていても検査を
    通り、しかも開発機は WQHD（上限 1350px）なので `lim` に当たることすら無く、
    **免除が働いていることにも気づけなかった**（見切れ 6 回目）。

    **2.6a1 で撤去した**。入らないときの答えは「免除」ではなく**逃げ道**
    （`window_fit.scrollable_body` のスクロール）で、入らないなら**入らないなりに
    全部触れること**を要求する。逃げ道が無い窓は、入らなければ赤。
    """
    need_w, need_h = window_fit.required_size(win)
    size = getattr(win, "_fit_size", None)
    assert size is not None, (
        f"[{label}] 窓が window_fit.fit_to_content() を通っていない"
        "（寸法をリテラルで持つと中身が増えた日に黙って切れる）。"
    )
    _assert_content_is_reachable(win, label, need_w, need_h, size)


def _assert_content_is_reachable(win, label, need_w, need_h, size) -> None:
    """中身が窓に入っているか、入らないならスクロールで届くこと。"""
    escape = getattr(win, "_fit_scroll", None)
    # ⚠️ `escape.active` は **(縦, 横)** の順（バーの向き）で、寸法の (幅, 高さ) とは
    # 逆。実装中に取り違えて「溢れているのにバーが出ていない」と誤検出した。
    for bar, need, got, edge in (
        (1, need_w, size[0], "右端"),      # 幅が足りない → 横バー
        (0, need_h, size[1], "下端"),      # 高さが足りない → 縦バー
    ):
        if got >= need:
            continue
        assert escape is not None, (
            f"[{label}] 中身が窓に入らない（必要 {need}px / 窓 {got}px）＝"
            f"{edge}のウィジェットが見切れる。画面に入らないこと自体が避けられない"
            "なら、window_fit.scrollable_body の逃げ道を与えること"
            "（「入らないのだから仕方ない」で見逃さない＝B-021）。"
        )
        assert escape.active[bar], (
            f"[{label}] 溢れている（必要 {need}px / 窓 {got}px）のにスクロール"
            f"バーが出ていない＝{edge}へ到達できない。"
        )
    if escape is not None:
        region = [int(v) for v in str(escape.canvas.cget("scrollregion")).split()]
        assert region[3] >= escape.body.winfo_reqheight(), (
            f"[{label}] スクロール領域が中身に届いていない"
            f"（領域 {region[3]}px / 中身 {escape.body.winfo_reqheight()}px）。"
        )


# ============================================================
# 窓は中身に**追従**する（広すぎない）
# ============================================================
# 下限（`_BASE_W` / `minsize`）が中身を上回っていても許される窓＝**中身が
# キャンバスで、要求サイズが「描ける最小」しか語らない**もの。理由ごと残す。
# ⚠️ ここは `_GROW_EXEMPT` と違い**逆向きの検査を置いていない**（「除外側は余りが
# 消えていないか見る」をしない）＝余りは言語と DPI で動く量なので「常に余る」とは
# 言えず、置くと毎回鳴る側（壊れ方②）になる。⇒ 除外を外す契機は人の判断。
_WIDTH_FLOOR_EXEMPT = {
    "graph":
        "図（matplotlib）は要求幅が実質 0 で、`_BASE_W = 1040` が**描ける大きさ**を"
        "決めている（断面図・スライダー 3 段が読める幅）",
    "map":
        "地図はタイルを描く面積そのものが機能で、`_BASE_W = 900` が最小の作業面"
        "（フェイク地図では要求幅がさらに小さく出る）",
}

# 下限が中身を上回っていることを「余り」として許す上限。丸めと枠の分だけ。
_WIDTH_FLOOR_TOLERANCE = 40


@pytest.mark.parametrize("name", sorted(_WINDOWS))
def test_the_width_floor_does_not_decide_the_window_width(name, monkeypatch):
    """**下限が窓幅を決めていない**こと（窓幅 ≒ 中身の必要幅）。

    `_BASE_W` は「これ以上細くしない」ための下限であって、**既定幅を決める場所では
    ない**。中身より大きい下限を置くと、中身が何であっても同じ幅の窓が開き、右に
    死んだ余白が残る＝**中継経路が実際にそうなっていた**（`_BASE_W = 980` に対し
    必要幅は ja 879 / en 847px＝2026-08-08 ユーザー指摘・B-053）。

    ⚠️ **下限は 2 か所にある**＝`_BASE_W` と `minsize`。`minsize` が上だと Tk が
    `geometry()` を上書きするので、`_BASE_W` を下げても窓は細くならない（B-053 の
    実装中に実際に踏んだ）。⚠️ ただし**この形を捕まえるのは下の「余り」の assert**
    （窓幅が minsize まで押し上げられるので余りとして出る）。`minsize` の assert は
    **除外窓でも効く二重の網**で、単独ではこの欠陥を検出しない＝**主たる守りと
    補助を取り違えないこと。**
    """
    opener, _grow = _WINDOWS[name]
    root = make_themed_root()
    root.withdraw()
    try:
        win, _owner = (opener(root, monkeypatch) if name == "map" else opener(root))
        win.update()
        size, need = win._fit_size, win._fit_need
        min_w = int(win.minsize()[0])
        assert min_w <= size[0], (
            f"[{name}] minsize({min_w}px) が窓幅({size[0]}px)を上回っている＝"
            "Tk が geometry() を上書きするので、下限を下げても細くならない"
        )
        if name in _WIDTH_FLOOR_EXEMPT:
            return
        assert size[0] - need[0] <= _WIDTH_FLOOR_TOLERANCE, (
            f"[{name}] 窓が必要幅より {size[0] - need[0]}px 広い"
            f"（窓 {size[0]}px / 必要 {need[0]}px）＝下限が既定幅を決めている。"
            f"下限を中身より下げるか、理由を書いて _WIDTH_FLOOR_EXEMPT へ入れること。"
        )
    finally:
        root.destroy()


def _scenario_band_width(win) -> int:
    """凍結帯（案件情報・経路）が要求する幅（＝この 2 つの LabelFrame の最大）。"""
    bands = [c for c in win._fit_scroll.body.winfo_children()
             if c.winfo_class() == "TLabelframe"]
    assert len(bands) >= 2, "凍結帯が見つからない（案件情報・経路）"
    return max(b.winfo_reqwidth() for b in bands[:2])


def test_the_frozen_header_does_not_decide_the_scenario_window_width():
    """条件探索の窓幅を**凍結帯が決めていない**こと（帯 ≦ 条件 5 列のグリッド）。

    実機フィードバック（2026-08-01）＝「横幅が広すぎる。条件 5 列でも右が余る」。
    直した（帯を詰めて 1070 → 870/940px）が、**2026-08-08 に再来**した＝B-046 で
    座標欄を 27 文字にしたとき帯が 870 → 1085px に太り、また帯が窓幅を決めた
    （B-052）。⇒ 窓幅を決めてよいのは**列数で変わる中身**（比較グリッド）だけ。

    ⚠️ **1 回目のゲートはこの再来を捕まえられなかった**＝「窓幅 ≦ *一番広い中身*
    + 60」を見ており、**一番広い中身が帯そのもの**なので、帯が太るとゲートの基準も
    一緒に太った（[[feedback-promote-recurring-checks]] 実証30／壊れ方③）。ここでは
    **帯とグリッドという別々の量**を比べる＝どちらが太っても片方は動かない。
    ⚠️ **座標欄を細くせよとは要求しない**（B-046 の下限と矛盾させない）。帯が太い
    なら、**幅を食っている物を帯から出す**（↻ と 🔒 の説明は案件情報の行へ移した）。
    """
    root = make_themed_root()
    root.withdraw()
    try:
        win, owner = _open_scenario(root)
        _grow_scenario(owner)              # 条件を上限まで並べる
        win.update()
        band = _scenario_band_width(win)
        grid = win._cmp_grid.winfo_reqwidth()
        assert band <= grid, (
            f"凍結帯が窓幅を決めている（帯 {band}px > 条件 5 列 {grid}px）"
        )
    finally:
        root.destroy()


def test_the_frozen_header_does_not_decide_the_multihop_window_width():
    """中継経路の窓幅を**凍結帯が決めていない**こと（帯 ≦ 区間表）。

    B-053 で帯を 859 → 620px 台へ下げ、「窓幅は区間表が決める」状態にしたばかり。
    **I-101 で 6 → 11 欄にするのは、その成果を食う方向の変更**なので、ここで固定する
    （窓新設時にも 6 欄を 1 行に並べて 125%/150% で 2088px を要求し、横断ゲートが
    止めている）。⚠️ **欄を減らせとは要求しない**＝入れるなら**折り返して**入れる
    （複数経路のように 1 行へ並べない）。

    ⚠️ **見るのは「共通設定」の帯だけ**＝案件情報の帯は I-101 が触らないうえ、
    当時は **en 781px / ja 764px と区間表を上回っていた**（↻ ボタン＋説明＋幅 20
    文字の欄 2 つ）。ここへ混ぜると**この変更と無関係な理由で最初から赤**になり、
    ゲートが何を要求しているのか読めなくなる（壊れ方③）。⇒ 案件情報の側は
    **別項目として起票し（B-108）、下の別ゲートで見る**。**赤の原因を 1 つに保つ。**
    """
    root = make_themed_root()
    root.withdraw()
    try:
        win, _ = _open_multihop(root)
        win.update()
        bands = [c for c in win._fit_scroll.body.winfo_children()
                 if c.winfo_class() == "TLabelframe"
                 and str(c.cget("text")) == i18n.t("batch_common_cfg")]
        assert len(bands) == 1, "共通設定の帯が 1 つでない（見出しが変わった？）"
        band = bands[0].winfo_reqwidth()
        table = win._hop_grid.winfo_reqwidth()
        assert band <= table, (
            f"共通設定の帯が窓幅を決めている（帯 {band}px > 区間表 {table}px）＝"
            "B-053 で下げた成果を食っている。欄を折り返して入れること。"
        )
    finally:
        root.destroy()


@pytest.mark.parametrize("lang", ["ja", "en"])
def test_the_case_info_band_does_not_decide_the_multihop_window_width(lang):
    """中継経路の窓幅を**案件情報の帯が決めていない**こと（帯 ≦ 区間表）。

    **B-053 で潰したはずの形の、もう 1 面**（B-108）。あのとき下げたのは共通設定の
    帯（859 → 620px 台）で、案件情報の帯は測っていなかった。実測（2026-08-18）＝
    **帯 ja 764 / en 781px に対し区間表は ja 741 / en 647px** ＝帯が窓幅を決めていた。
    ⚠️ **↻ と 🔒 の説明をこの帯へ移してきたのが B-053 そのもの**なので、共通設定を
    軽くした分が素直にこちらへ移っていた＝**帯を 1 つ測るだけでは、凍結領域全体の
    要求幅は下がらない。**

    ⚠️ **言語で worst case が入れ替わる**＝日本語は帯も表も長く差は 23px だが、英語
    は区間表だけが短くなる（647px）ので超過が 134px に開く。**ja だけ見ると「あと
    少し」に見えて処方の大きさを読み違える**ので両方を回す。

    ⚠️ **欄を細くせよとは要求しない**＝案件名・メモは利用者の文字列をそのまま見せる
    欄で、下限を削ると読めなくなる（B-046 と同じ筋）。**この窓は横が狭く縦に余裕が
    ある**ので、入れるなら**折り返して**入れる（共通設定を 3 欄で折ったのと同じ）。

    ⚠️ **複数経路（BatchBuilderWindow）は同じ構造だが該当しない**＝帯の作りは同一
    （2026-08-18 実測で 764/781px と一致）だが、あの窓は共通設定が 1157〜1238px を
    要求するので**帯が窓幅を決める余地が無い**。⇒ クラスとしては点検済みで、直す
    のはこの窓だけ＝**帯の項目は正典（`frozen_common`）だが、並べ方は窓ごと。**
    """
    prev = i18n._lang
    root = make_themed_root()
    root.withdraw()
    try:
        i18n.set_lang(lang)
        win, _ = _open_multihop(root)
        win.update()
        bands = [c for c in win._fit_scroll.body.winfo_children()
                 if c.winfo_class() == "TLabelframe"
                 and str(c.cget("text")) == i18n.t("batch_case_info")]
        assert len(bands) == 1, "案件情報の帯が 1 つでない（見出しが変わった？）"
        band = bands[0].winfo_reqwidth()
        table = win._hop_grid.winfo_reqwidth()
        assert band <= table, (
            f"[{lang}] 案件情報の帯が窓幅を決めている"
            f"（帯 {band}px > 区間表 {table}px）＝B-053 で潰した形がこの面に残って"
            "いる。欄を細くするのではなく、折り返して入れること。"
        )
    finally:
        i18n.set_lang(prev)
        root.destroy()


def test_scenario_window_width_follows_the_condition_count():
    """条件を増やしたときだけ窓が広がること（1 列の時点で最大幅になっていない）。

    「窓が広すぎる」の**症状そのもの**を見る＝帯や下限が幅を決めていると、条件 1 列
    でも最大幅になり、ここが等しくなる。⚠️ `_BASE_W`（下限）を上げただけでも赤に
    なる＝下限は「これ以上細くしない」ためのもので、既定幅を決める場所ではない。
    """
    root = make_themed_root()
    root.withdraw()
    try:
        win, owner = _open_scenario(root)
        win.update()
        narrow = win._fit_size[0]
        _grow_scenario(owner)
        win.update()
        wide = win._fit_size[0]
        assert narrow < wide, (
            f"条件を 1 → 5 列にしても窓幅が変わらない（{narrow}px → {wide}px）"
            "＝幅を決めているのは条件列ではない"
        )
    finally:
        root.destroy()


# ============================================================
# 組み立てたあとに**中身の幅が伸びる**経路（B-100）
# ============================================================
# 🔴 **これまでの「増え方」は全部“行・列が増える”形だった**（地点を足す・条件列を
# 足す・CSV を読み込む）。B-100 はそれとは別のクラス＝**行も列も増えないまま、
# 既にあるセルの中身だけが伸びる**（区間の見出しが `TX → R1` から利用者の入力した
# 地点名へ変わる）。窓は測り直しを呼ばないので、伸びたぶんが右へ押し出されて
# **「判定」列が見切れる**。⚠️ 利用者は判定列があることを知らないので、
# **見切れていること自体に気づけない。**
#
# 🔑 **固定データで試している限り永久に出ない**＝既定の地点名（`TX`/`R1`/`RX`）では
# 表が窓より狭いままではみ出さない。**環境の差ではなく入力の差**だった
# （「実機で再現しない」と報告され、一度 [[project-real-world-env-vdi]] クラスと
# 誤って書いた＝入力を動かして初めて開発機で再現した）。
#
# ⚠️ **処方を 2 回出して 2 回とも外した経緯があるので、ゲートを先に書いて赤を
# 出してから処方する**（2 回目は変異検証で空振りと判明＝ゲートが無ければ
# 「直った」と報告していた）。
_LONG_NAME = "広島県安芸郡海田町役場庁舎"        # 13 字（B-100 の再現に使った名前）


def test_the_multihop_window_follows_a_longer_place_name():
    """🔴 **地点名を書き換えたら窓も測り直すこと**（B-100）。

    ⚠️ **開いた後に書き換えるのが肝**＝プロジェクトを開いて最初から長い名前が
    入っている場合は、組み立て後の `_fit_to_content()` が測るので再現しない。
    """
    root = make_themed_root()
    root.withdraw()
    try:
        win, _ = _open_multihop(root)
        win.update()
        before = win._hop_grid.winfo_reqwidth()
        for i, wp in enumerate(win._wp_vars):
            wp["name"].set(f"{_LONG_NAME}{i}")   # 製品の経路（欄に打つのと同じ）
        win.update()
        grid = win._hop_grid.winfo_reqwidth()
        assert grid > before, (
            f"前提が崩れている（区間表が {before}px から伸びていない）"
            "＝はみ出さない条件で試している。名前をもっと長くすること。"
        )
        assert grid <= win._fit_size[0], (
            f"区間表（{grid}px）が窓（{win._fit_size[0]}px）に入っていない＝右端の"
            "「判定」列が見切れる。利用者は判定列があることを知らないので、"
            "**見切れていること自体に気づけない**（B-100）。"
        )
        _assert_fits(win, "multihop（長い地点名）")
    finally:
        root.destroy()


def test_required_size_sees_content_that_grew_without_a_refit():
    """🔴 **横断ゲートの目が塞がっていた**（B-100 の systemic な半分）。

    逃げ道（`scrollable_body`）を持つ窓では、窓の `winfo_reqwidth()` が**受け皿の
    キャンバスの要求幅で頭打ちになる**。それを更新するのは `_ScrollEscape.remeasure()`
    だけで、呼ぶのは `fit_to_content` だけ＝**測り直しを呼び忘れた窓では、
    `required_size()` も伸びる前の値を返し続ける**（実測＝区間表が 647 → 993px に
    伸びても 801px のまま）。

    ⇒ **窓の測り忘れという欠陥クラスが、ゲートから原理的に見えなかった。**
    申告（`_fit_need`）も実測（`winfo_req*`）も同じ嘘をつくので「大きい方を採る」
    では救われない（[[feedback-promote-recurring-checks]] の壊れ方③）。

    ⚠️ ここは**製品が測り直しを呼ばない状態を作って**検査する＝呼んでしまうと
    `fit_to_content` が `remeasure()` を通すので、この欠陥は再現しない。
    """
    root = make_themed_root()
    root.withdraw()
    try:
        win, _ = _open_multihop(root)
        win.update()
        # 製品の測り直しを止めた状態で中身だけを伸ばす（＝呼び忘れの再現）。
        win._fit_to_content = lambda: None
        for i, wp in enumerate(win._wp_vars):
            wp["name"].set(f"{_LONG_NAME}{i}")
        win.update()
        grid = win._hop_grid.winfo_reqwidth()
        need_w = window_fit.required_size(win)[0]
        assert need_w >= grid, (
            f"必要幅が中身に届いていない（申告 {need_w}px / 区間表 {grid}px）＝"
            "受け皿のキャンバス幅で頭打ちになっている。この状態では**窓が測り直しを"
            "呼び忘れても横断ゲートが緑になる**（B-100 を通した経路そのもの）。"
        )
    finally:
        root.destroy()


def test_the_multihop_window_follows_the_results_that_arrive():
    """**結果が届いて区間表が伸びる**面（B-100 のクラス点検・同型の疑い）。

    区間表は結果セル（受信レベル・マージン・判定）が空のまま組み立てられ、実行後に
    数字が入る＝**行も列も増えないまま中身だけが伸びる**という B-100 と同じ形。

    ✅ **実測では伸びない**（2026-08-18＝993px のまま）。理由は結果セルが
    `width=12/12/6` と**字数で場所を先に確保している**こと。⇒ ここは
    **「伸びない」ほうを固定する**＝伸びるようになった日にこのゲートが赤くなり、
    `_show_hop_result` 側にも測り直しが要ることに気づける。⚠️ **`_assert_fits` を
    置くだけでは何も検査しない**（伸びないのだから常に緑）＝壊れ方①。
    """
    from types import SimpleNamespace

    root = make_themed_root()
    root.withdraw()
    try:
        win, _ = _open_multihop(root)
        win.update()
        before = win._hop_grid.winfo_reqwidth()
        pr = SimpleNamespace(
            status="OK",
            result=SimpleNamespace(p_rx=-105.32, actual_margin=-20.32))
        win._clear_hop_results()
        for index in range(1, win._hop_count() + 1):
            win._show_hop_result(index, pr)
        win.update()
        assert win._hop_grid.winfo_reqwidth() == before, (
            f"結果が届いて区間表が伸びるようになった（{before}px → "
            f"{win._hop_grid.winfo_reqwidth()}px）＝結果セルが字数で場所を確保して"
            "いる前提が崩れた。**B-100 と同じ形**なので、`_show_hop_result` の側にも"
            "測り直し（`_fit_to_content`）が要る。"
        )
        _assert_fits(win, "multihop（結果あり）")
    finally:
        root.destroy()


def test_the_batch_table_reserves_its_cell_widths_up_front():
    """**複数経路で、既存セルの中身だけが伸びる**面（B-100 のクラス点検）。

    列の増減では `_fit_refit` を呼んでいる＝**未確認だったのは「列も行も増えず、
    セルの中身だけが長くなる」場合**（長い経路 ID・長い備考）。

    ✅ **実測では伸びない**（2026-08-18）。複数経路の行は `tk.Entry` で、
    `_WIDTHS` が**字数で幅を決めている**＝中身が何文字でも要求幅は動かない
    （中身は欄の中でスクロールする）。⇒ 中継経路と同じく**「伸びない」ほうを
    固定する**＝可変幅のセルへ変えた日にここが赤くなる。
    """
    root = make_themed_root()
    root.withdraw()
    try:
        win, owner = _open_batch(root)
        _grow_batch(owner)                       # 行を 1 本入れておく
        win.update()
        table = owner._table_frame
        before = table.winfo_reqwidth()
        entries = owner._row_entries[0]
        entries[0].delete(0, "end")
        entries[0].insert(0, "x" * 60)           # 経路 ID
        entries[-1].delete(0, "end")
        entries[-1].insert(0, "や" * 40)          # 備考
        win.update()
        assert table.winfo_reqwidth() == before, (
            f"セルの中身だけで表が伸びるようになった（{before}px → "
            f"{table.winfo_reqwidth()}px）＝`_WIDTHS` が字数で幅を決めている前提が"
            "崩れた。**B-100 と同じ形**なので、欄を編集する経路にも測り直しが要る。"
        )
        _assert_fits(win, "batch（セルの中身を伸ばした）")
    finally:
        root.destroy()


# ============================================================
# 実機の画面に収まること（B-021）
# ============================================================
# 出荷先の画面＝**FHD（1920×1080）100%**。実効上限は SCREEN_MARGIN を引いた値で、
# 高さは 990px になる。
#
# ⚠️ **開発機の画面で測ってはいけない**。開発機は WQHD（上限 1350px）なので、
# ランチャーが必要とする 1023px は楽々入ってしまい、実機で 33px 溢れて最下段の
# ボタンが数 px に潰れていることに**永久に気づけなかった**（B-021＝見切れ 6 回目）。
# だから画面サイズを実測に頼らず、**出荷先の寸法を定数として与える**。
#
# ⚠️ **範囲は 2.6a1 で 125%/150% まで広げた**（2026-07-31）。2.5RC2 の時点では
# 96dpi（100%）に意図して区切ってあった＝125%/150% では条件探索が 105px / 233px
# 溢れ、それを緑にするには逃げ道（`scrollable_body`）が要ったため。逃げ道が
# 入ったので、ここが本来見るべき範囲に戻った（区切っていた残り＝B-023）。
#
# **横（幅）は逃げ道に頼らせない**。溢れているのは縦だけで、幅は 150% でも
# バッチの 1679px が最大＝1830px に収まる。横スクロールは「読む方向」に対して
# 体験が悪いので、幅は素で入ることを要求し続ける（入らなくなった日に赤くなって
# 判断を迫るのが正しい＝黙って横バーで逃げない）。
_FHD_LIMIT = (1920 - window_fit.SCREEN_MARGIN, 1080 - window_fit.SCREEN_MARGIN)
_FHD_SCREEN = (1920, 1080)

# 出荷先で起こり得る表示スケール（100% / 125% / 150%）。
_SHIP_DPIS = (96, 120, 144)

# 🔴 **上限は DPI で動く**（2026-08-18・B-084）。`_FHD_LIMIT`（＝定数 90px を引いた
# 990px）は **100% 表示だけの値**だった：出荷先のタスクバーは 100% で 48px、150% では
# 72px あり、装飾（タイトルバー＋枠）も 39px → 63px と拡大する。定数はどちらの拡大も
# 賄えないので、**150% では使える高さを 45px 過大に見積もっていた**（実機では窓の下端
# 33px がタスクバーの裏）。⇒ 出荷先の条件は**偽のタスクバーを注入して**作る。
#
# ⚠️ **`_FHD_LIMIT` を消さない**＝あちらは「OS に聞けない環境の見積り」として
# `usable_area` の保険側にまだ生きている（`test_the_constant_margin_is_kept_...`）。
_FHD_TASKBAR_96 = 48                        # 出荷先のタスクバー（100% での実測）


def _ship_on_fhd(monkeypatch, dpi):
    """出荷先（FHD・指定 DPI）の画面とタスクバーを注入する。"""
    taskbar = int(round(_FHD_TASKBAR_96 * dpi / 96))
    monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
    monkeypatch.setattr(window_fit, "monitors", lambda _w: [(0, 0, *_FHD_SCREEN)])
    monkeypatch.setattr(window_fit, "work_areas",
                        lambda: {(0, 0, *_FHD_SCREEN): (0, 0, 1920, 1080 - taskbar)})


def _ship_limits(win):
    """出荷先で窓の**中身**に使える `(幅, 高さ)`（装飾のぶんは引いてある）。"""
    left, top, right, bottom = window_fit.usable_area(win, (0, 0, *_FHD_SCREEN))
    return (right - left, bottom - top)


@pytest.mark.parametrize("lang", ["ja", "en"])
@pytest.mark.parametrize("name", sorted(_WINDOWS))
def test_every_window_fits_without_scrolling_at_100_percent(name, lang, monkeypatch):
    """**出荷先の標準環境（FHD 100%）では逃げ道に頼らない**こと。

    逃げ道（`scrollable_body`）は「入らない画面でも壊れない」ための保険であって、
    **標準環境で常時スクロールさせてよい**という意味ではない。ここを緩めると、
    UI を足すたびに数十 px ずつ食って「実機では最初からスクロール」の窓が
    できあがる（実際 I-029〜I-031 の実装中に、ランチャーが 966→994px、条件探索が
    941→1033px まで膨らんで一度この線を越えた＝このテストが捕まえた）。

    125%/150% は逃げ道で担保する（`test_every_window_is_usable_on_fhd`）。
    ⚠️ このテストが赤くなったら「余白を削る」より先に**足した UI が本当に要るか**
    を問うこと（B-021 でロゴを縮めて 33px 返したのと同じ順序）。
    """
    from views import theme

    prev = i18n._lang
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang(lang)
        _ship_on_fhd(monkeypatch, 96)
        theme.apply_fonts(root, dpi=96)
        opener, _ = _WINDOWS[name]
        win, _owner = (opener(root, monkeypatch) if name == "map" else opener(root))
        need_w, need_h = window_fit.required_size(win)
        lim_w, lim_h = _ship_limits(win)
        assert need_h <= lim_h, (
            f"[{name}/{lang}] FHD 100% で画面に入らない"
            f"（必要 {need_h}px / 使える高さ {lim_h}px ＝ {need_h - lim_h}px 超過）。"
            "逃げ道は 125% 以上のための保険で、標準環境で常時スクロールさせる"
            "ためのものではない。"
        )
        assert need_w <= lim_w, (
            f"[{name}/{lang}] FHD 100% で画面に幅が入らない"
            f"（必要 {need_w}px / 使える幅 {lim_w}px ＝ {need_w - lim_w}px 超過）。"
        )
    finally:
        i18n.set_lang(prev)
        theme.apply_fonts(root, dpi=96)
        root.destroy()


@pytest.mark.parametrize("dpi", _SHIP_DPIS)
@pytest.mark.parametrize("lang", ["ja", "en"])
@pytest.mark.parametrize("name", sorted(_WINDOWS))
def test_every_window_is_usable_on_fhd(name, lang, dpi, monkeypatch):
    """実機（FHD）で全窓が使えること＝**入るか、入らないなら手が届くか**。

    「入らないのだから仕方ない」で見逃さない、が本テストの本体（B-021）。
    高さは逃げ道（スクロール）で許すが、**幅は素で収まることを要求する**
    （上の `_FHD_LIMIT` のコメント）。
    """
    from views import theme

    prev = i18n._lang
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang(lang)
        _ship_on_fhd(monkeypatch, dpi)
        theme.apply_fonts(root, dpi=dpi)
        opener, _ = _WINDOWS[name]
        win, _owner = (opener(root, monkeypatch) if name == "map" else opener(root))
        need_w, need_h = window_fit.required_size(win)
        lim_w, lim_h = _ship_limits(win)
        label = f"{name}/{lang}/{dpi}dpi"
        assert need_w <= lim_w, (
            f"[{label}] 実機（FHD）の画面に幅が入らない"
            f"（必要 {need_w}px / 使える幅 {lim_w}px ＝ {need_w - lim_w}px 超過）。"
            "**横スクロールで逃げない**＝列を減らすか、幅の要求そのものを削ること。"
        )
        if need_h > lim_h:
            # 入らない窓は、入らないなりに最後まで手が届くこと。
            assert getattr(win, "_fit_scroll", None) is not None, (
                f"[{label}] 画面に入らない（必要 {need_h}px / 使える高さ {lim_h}px ＝ "
                f"{need_h - lim_h}px 超過）のに逃げ道が無い。溢れた分は下端の"
                "ウィジェットから削られる（B-021 では最下段のボタン列が数 px の帯に"
                "潰れ、マップウィンドウ・条件探索へ到達できなくなった）。"
                "window_fit.scrollable_body の中へ組み立てること。"
            )
        _assert_content_is_reachable(win, label, need_w, need_h, win._fit_size)
    finally:
        i18n.set_lang(prev)
        theme.apply_fonts(root, dpi=96)   # 名前付きフォントは他テストと共有
        root.destroy()


# ============================================================
# 入らないときの逃げ道（B-021② / B-023）
# ============================================================
# ⚠️ **ここは開発機の画面では絶対に発火しない**（WQHD ＝ 使える高さ 1350px）。
# 出荷先の画面を `window_fit.screen_size` へ差し込んで、**溢れる状況を作ってから**
# 検査する。溢れさせずに「逃げ道がある」ことだけ確かめるテストは、逃げ道が
# 壊れていても緑になる（＝これまで見切れを 6 回通した形そのもの）。
#
# 逃げ道を持つべき窓と、その窓が実機 FHD で溢れる DPI。
# **溢れない窓（バッチ・地図）を入れていないのは意図的**＝中身が伸縮する窓で、
# 二重のスクロール容器に入れると内側の表が潰れる。溢れた日には
# `test_every_window_fits_on_fhd_at_96dpi`（と 125/150% への拡張）が赤くなるので、
# そのとき改めて判断する（黙って見逃す経路にはならない）。
_ESCAPE_WINDOWS = {"launcher": 120, "scenario": 144}


def _open_on_fhd(root, name, dpi, monkeypatch):
    """FHD の画面・指定 DPI で窓を開く（`_fit_size` はその前提で決まる）。"""
    from views import theme

    _ship_on_fhd(monkeypatch, dpi)
    theme.apply_fonts(root, dpi=dpi)
    opener, _ = _WINDOWS[name]
    win, owner = (opener(root, monkeypatch) if name == "map" else opener(root))
    return win, owner


@pytest.mark.parametrize("name,dpi", sorted(_ESCAPE_WINDOWS.items()))
def test_window_that_cannot_fit_can_still_be_scrolled(name, dpi, monkeypatch):
    """画面に入らない窓が、**スクロールで最後まで届く**こと。

    B-021 の実害は「入らない」ことではなく、**入らなかった分が下端のウィジェット
    から黙って削られる**ことだった（最下段のボタン列が数 px の帯に潰れ、マップ
    ウィンドウ・条件探索へ到達できなくなった）。ここで見るのは 3 点：

      1. 窓は画面の上限にクランプされている（＝溢れる状況を再現できている）
      2. 縦スクロールバーが出ている
      3. **スクロール領域が中身の必要量を丸ごと覆っている**＝一番下まで届く
    """
    prev = i18n._lang
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        win, _owner = _open_on_fhd(root, name, dpi, monkeypatch)
        need_h = window_fit.required_size(win)[1]
        lim_h = _ship_limits(win)[1]
        assert need_h > lim_h, (
            f"[{name}/{dpi}dpi] 溢れない条件でテストしている"
            f"（必要 {need_h}px ≤ 上限 {lim_h}px）＝逃げ道が壊れていても緑になる。"
            "DPI を上げるか、この窓を _ESCAPE_WINDOWS から外すこと。"
        )
        assert win._fit_size[1] == lim_h
        escape = getattr(win, "_fit_scroll", None)
        assert escape is not None, (
            f"[{name}] 画面に入らないのに逃げ道が無い"
            "（window_fit.scrollable_body の中へ組み立てること）。"
        )
        assert escape.active[0] and escape.vsb.grid_info(), (
            f"[{name}/{dpi}dpi] 溢れているのに縦スクロールバーが出ていない"
            "＝溢れた分は下端のウィジェットから削られる（B-021 と同じ形）。"
        )
        region = [int(v) for v in str(escape.canvas.cget("scrollregion")).split()]
        assert region[3] >= escape.body.winfo_reqheight(), (
            f"[{name}/{dpi}dpi] スクロール領域が中身に届いていない"
            f"（領域 {region[3]}px / 中身 {escape.body.winfo_reqheight()}px）。"
            "バーは出るが最下段までスクロールできない状態になる。"
        )
    finally:
        i18n.set_lang(prev)
        from views import theme
        theme.apply_fonts(root, dpi=96)   # 名前付きフォントは他テストと共有
        root.destroy()


def test_mouse_wheel_moves_the_escape_only_when_nothing_inside_scrolls(monkeypatch):
    """ホイールの担当分け＝**受け皿が動くのは、中の一覧が動けないときだけ**。

    受け皿はトップレベルにバインドするので、中身のどこで回してもイベントが届く。
    結果一覧（Treeview）のように自前でスクロールする部品の上でも受け皿が動くと、
    一度のホイールで**二重に流れて**行き先が分からなくなる。

    ⚠️ 窓を実体化（`deiconify`）しないと検証にならない＝未表示のキャンバスは
    「中身が全部見えている」状態を返し、スクロール量が常に 0 になる。
    """
    from types import SimpleNamespace

    prev = i18n._lang
    root = make_themed_root()
    try:
        i18n.set_lang("ja")
        win, _owner = _open_on_fhd(root, "launcher", 120, monkeypatch)
        root.deiconify()
        root.update()
        escape = win._fit_scroll
        assert escape.active[0], "前提が崩れている（溢れていない）"

        top_before = escape.canvas.yview()[0]
        escape._on_mousewheel(SimpleNamespace(widget=escape.body, delta=-120))
        root.update()
        assert escape.canvas.yview()[0] > top_before, (
            "受け皿の上でホイールを回しても動かない＝溢れた下端へ到達できない。"
        )

        # 自前でスクロールできる部品の上では手を出さないこと。
        moved = escape.canvas.yview()[0]
        inner = SimpleNamespace(
            widget=SimpleNamespace(yview=lambda: (0.2, 0.6), master=escape.body),
            delta=-120)
        escape._on_mousewheel(inner)
        root.update()
        assert escape.canvas.yview()[0] == moved, (
            "自前でスクロールする一覧の上でも受け皿が動いている＝二重に流れる。"
        )
    finally:
        i18n.set_lang(prev)
        from views import theme
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_shrinking_a_window_by_hand_shows_the_escape():
    """**手で小さくしたとき**もバーが出ること。

    `fit_to_content` が走るのは「開いたとき」と「中身が増えたとき」だけなので、
    ユーザーがマウスで窓を縮めた場合はそこを通らない。ここが無いと
    「小さくしたら下端が消えて、しかもスクロールもできない」になる
    （リサイズできる窓＝グラフ窓で露見。他の窓は固定 or 下限が大きく気づけなかった）。
    """
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        win, _ = _open_scenario(root)
        escape = win._fit_scroll
        assert not escape.active[0], "前提が崩れている（最初から溢れている）"

        need_h = win._fit_need[1]
        root.deiconify()
        win.geometry(f"{win._fit_size[0]}x{need_h - 200}")   # 手で縮めた相当
        root.update()
        assert escape.active[0], (
            "手で縮めてもスクロールバーが出ない＝下端に手が届かない。"
        )
    finally:
        root.destroy()


def test_scroll_escape_stays_hidden_while_the_content_fits():
    """**入るあいだはスクロールバーを出さない**こと。

    逃げ道は「入らない時だけ」のもので、常時バーが出るなら受け皿を挟んだ時点で
    見た目の回帰になる（幅を食い、ホイールが効き、印象も変わる）。
    """
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        win, _ = _open_scenario(root)          # 96dpi・開発機の画面＝余裕がある
        escape = win._fit_scroll
        need_h = window_fit.required_size(win)[1]
        assert need_h <= win._fit_size[1], "前提が崩れている（この画面で既に溢れている）"
        assert escape.active == (False, False)
        assert not escape.vsb.grid_info() and not escape.hsb.grid_info()
    finally:
        root.destroy()


@pytest.mark.parametrize("lang", ["ja", "en"])
@pytest.mark.parametrize("name", sorted(_WINDOWS))
def test_window_fits_its_content(name, lang, monkeypatch):
    """開いた時点で全窓が中身を収めていること（言語を変えても）。

    日本語見出しは英語より 1 割ほど広い＝どちらか片方でしか測らないと、もう
    片方で見切れる（B-002 がまさにそれ）。
    """
    prev = i18n._lang
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang(lang)
        opener, _ = _WINDOWS[name]
        win, _owner = (opener(root, monkeypatch) if name == "map" else opener(root))
        _assert_fits(win, f"{name}/{lang}")
    finally:
        i18n.set_lang(prev)
        root.destroy()


@pytest.mark.parametrize(
    "name", sorted(n for n, (_, grow) in _WINDOWS.items() if grow is not None))
def test_window_still_fits_after_content_grows(name, monkeypatch):
    """**あとから中身が増えても**収まっていること。

    起動時だけ測る実装は「開いた瞬間は正しい」ので、初期表示しか見ないゲートでは
    永久に捕まらない。I-024（条件 5 の見切れ）は実際にその形で出た。
    """
    prev = i18n._lang
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        opener, grow = _WINDOWS[name]
        win, owner = opener(root)
        grow(win, owner)
        root.update_idletasks()
        _assert_fits(win, f"{name}（中身を増やしたあと）")
    finally:
        i18n.set_lang(prev)
        root.destroy()


# 「増やす操作」が**必要サイズを動かさない**窓＝理由を書いてここへ入れる
# （2.7 スライス F で新設）。⚠️ 空欄にできない＝**理由ごと残して見直せる**ように
# するのがこの表の目的（`_LINE_LIMIT_EXEMPT` と同じ扱い）。
_GROW_EXEMPT = {
    "batch":
        "入力表は受け皿（キャンバス）の中にあり、行を足しても窓の必要サイズは動かない"
        "（実測 2026-08-07＝1 行 / 12 行 / 30 行とも 1378x612）。列幅も入力欄の"
        "文字数で決まるので、長い備考を入れても広がらない",
    # ⚠️ **`scenario` は 2026-08-08 に除外から戻した**（B-052）＝凍結帯から ↻ と
    # 🔒 の説明を出して帯を 1085 → 829px に細くしたので、**窓幅はまた条件列で
    # 決まる**（実測 ja/dd＝1 列 859px → 5 列 947px）。この除外表がその場で赤に
    # なって知らせた＝「除外は免除の永久パスではなく観測の記録」が働いた実例。
}


@pytest.mark.parametrize(
    "name", sorted(n for n, (_, grow) in _WINDOWS.items() if grow is not None))
def test_the_grow_operation_actually_grows(name):
    """登録した「中身を増やす操作」が、**本当に必要サイズを増やす**こと。

    🔴 **上の `test_window_still_fits_after_content_grows` は、増やす操作が
    何も増やさなくなっても緑のまま**＝「増えても収まる」を主張しているのに、
    増えていないので何も検査していない（[[feedback-promote-recurring-checks]]
    壊れ方②）。実際 2026-08-07 に測ったら **3 本中 2 本が動いていなかった**
    （batch / scenario）。窓が育つほど、増やす操作は静かに吸収されていく。

    ⇒ **個別のテストではなく登録表（`_WINDOWS`）そのものを検査する**ので、
    窓を足す人が「増やす操作」を書いた時点で、それが効いているかを機械が言う。

    ⚠️ **除外側も検査する**（増えるようになったら除外が古い）＝除外表は
    「今は動かない」という**観測の記録**であって、免除の永久パスではない。
    """
    prev = i18n._lang
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        opener, grow = _WINDOWS[name]
        win, owner = opener(root)
        root.update_idletasks()
        before = window_fit.required_size(win)
        grow(win, owner)
        root.update_idletasks()
        after = window_fit.required_size(win)

        if name in _GROW_EXEMPT:
            assert after == before, (
                f"{name} の「増やす操作」が必要サイズを動かすようになった"
                f"（{before} → {after}）。_GROW_EXEMPT から外すこと"
                f"（除外の理由: {_GROW_EXEMPT[name]}）。"
            )
        else:
            assert after != before, (
                f"{name} の「増やす操作」が必要サイズを 1px も動かしていない"
                f"（{before} のまま）＝`test_window_still_fits_after_content_grows` は"
                "この窓について何も検査していない。増える操作へ直すか、"
                "理由を書いて _GROW_EXEMPT へ入れること。"
            )
    finally:
        i18n.set_lang(prev)
        root.destroy()


def test_grow_exemptions_still_refer_to_registered_windows():
    """除外表に、登録されていない窓・増やす操作の無い窓が残っていないこと。"""
    stale = [name for name in _GROW_EXEMPT
             if name not in _WINDOWS or _WINDOWS[name][1] is None]
    assert not stale, f"除外表に古い項目がある: {stale}"


# ⛔ `test_growing_content_does_not_shrink_the_window` は 2.7 スライス F で削除。
#    条件列を足しても引いても条件探索の必要サイズが動かない（上記の除外理由）ので、
#    「広げた窓を狭めない」を**恒真**に主張していた。同じ約束は、下の
#    `test_refit_all_does_not_shrink_unless_asked` が DPI 経由で非自明に見ている。


# ============================================================
# DPI が変わったら測り直すこと
# ============================================================
def test_windows_are_refitted_when_dpi_grows(monkeypatch):
    """DPI が上がってフォントが大きくなったら、窓も広がること。

    2026-07-26 のユーザー報告は「窓は DPI に追従して変わるのに字が変わらない」
    だった。字を追従させると今度は**必要な幅も高さも増える**ので、貼り直しと
    測り直しは対で要る（片方だけ直すと、字は大きくなったが窓は元のままで
    見切れる＝これまでと同じクラスの不具合になる）。
    """
    from views import theme

    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        theme.apply_fonts(root, dpi=96)
        win, owner = _open_scenario(root)
        before = win._fit_size

        theme.apply_fonts(root, dpi=144)     # 150% のモニタへ移した相当
        window_fit.refit_all(root)
        root.update_idletasks()

        assert win._fit_size[0] > before[0], (
            f"DPI が上がってフォントが大きくなったのに窓幅が変わらない"
            f"（{before[0]}px のまま）。右端が見切れる。"
        )
        _assert_fits(win, "scenario（DPI 144）")
    finally:
        theme.apply_fonts(root, dpi=96)      # 名前付きフォントは他テストと共有
        root.destroy()


def test_windows_shrink_back_when_dpi_falls(monkeypatch):
    """DPI が**下がったら**窓も戻ること（I-053＝150% → 100% の一方通行）。

    ⚠️ **上の `test_windows_are_refitted_when_dpi_grows` と対で置く**＝あちらは
    増える方向しか見ておらず、`grow_only`（縮めない約束）を DPI 経路でも尊重して
    いた実装が**そのまま緑**だった（片側しか見ていないゲート）。表示スケールを
    戻したのに窓が大きいままなのは、狭い画面では見切れの原因そのものになる。
    """
    from views import theme

    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        theme.apply_fonts(root, dpi=96)
        win, _ = _open_scenario(root)
        at96 = win._fit_size

        theme.apply_fonts(root, dpi=144)          # 150% のモニタへ移した
        window_fit.refit_all(root, shrink=True)
        root.update_idletasks()
        assert win._fit_size[0] > at96[0], "前提が崩れている（DPI を上げても広がらない）"

        theme.apply_fonts(root, dpi=96)           # 100% へ戻した
        window_fit.refit_all(root, shrink=True)
        root.update_idletasks()
        assert win._fit_size == at96, (
            f"表示スケールを戻したのに窓が {win._fit_size} のまま"
            f"（100% では {at96} で足りる）＝広がる方向の一方通行。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)           # 名前付きフォントは他テストと共有
        root.destroy()


def test_a_window_with_its_own_refit_shrinks_too(monkeypatch):
    """**自前の再測を持つ窓**（バッチ）も縮む方向に追従すること。

    `refit_all` は `_fit_refit` があればそちらを呼ぶ＝窓が自分の `grow_only` で
    `fit_to_content` を呼び直すので、**引数では「今回は縮んでよい」が伝わらない**。
    ⚠️ ここが抜けると「4 窓は戻るのにバッチだけ戻らない」という、直そうとして
    いる一方通行の一部だけが生き残る（⑧＝同じ表示環境なら同じ規則で追従する）。
    """
    from views import theme

    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        theme.apply_fonts(root, dpi=96)
        win, _ = _open_batch(root)
        assert getattr(win, "_fit_refit", None) is not None, "前提（自前の再測）が無い"
        at96 = win._fit_size

        theme.apply_fonts(root, dpi=144)
        window_fit.refit_all(root, shrink=True)
        root.update_idletasks()
        assert win._fit_size[0] > at96[0], "前提が崩れている（DPI を上げても広がらない）"

        theme.apply_fonts(root, dpi=96)
        window_fit.refit_all(root, shrink=True)
        root.update_idletasks()
        assert win._fit_size == at96, (
            f"自前の再測を持つ窓が縮んでいない（{win._fit_size} のまま／100% では "
            f"{at96}）＝`shrink` が `_fit_refit` の先まで届いていない。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_refit_all_does_not_shrink_unless_asked(monkeypatch):
    """`shrink` を渡さない経路では**縮めない**こと（既存の約束を壊さない）。

    縮めてよいのは DPI が変わった瞬間だけ＝解像度の変化やテーマの貼り直しで
    「手で広げた窓が勝手に既定サイズへ戻る」と、I-053 で直したい一方通行の
    ちょうど裏返しの嫌がらせになる。
    """
    from views import theme

    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        theme.apply_fonts(root, dpi=96)
        win, _ = _open_scenario(root)
        at96 = win._fit_size

        theme.apply_fonts(root, dpi=144)
        window_fit.refit_all(root, shrink=True)
        root.update_idletasks()
        wide = win._fit_size
        assert wide[0] > at96[0], "前提が崩れている（DPI を上げても広がらない）"

        theme.apply_fonts(root, dpi=96)
        window_fit.refit_all(root)                 # 既定＝縮めない
        root.update_idletasks()
        assert win._fit_size == wide, (
            f"頼まれていないのに窓を狭めた（{win._fit_size} ／ {wide} だった）。"
        )

        window_fit.refit_all(root, shrink=True)    # 頼まれたら縮む
        root.update_idletasks()
        assert win._fit_size == at96, (
            f"`shrink=True` でも戻らない（{win._fit_size} のまま）＝上の主張が"
            "「一度も落ちないゲート」になっていないかを見る対の検査。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_windows_follow_a_screen_that_shrinks_and_grows_back(monkeypatch):
    """**画面が変わったら**測り直しで追従すること（B-022 の測り直し側）。

    害は両方向に出る＝狭くなれば窓がデスクトップの外へ出たまま、広くなれば
    クランプされた小さいまま二度と戻らない（再起動しか回復手段が無い）。
    ⚠️ 「広くなったら戻る」は `grow_only`（縮めない約束）と紛らわしいが別物＝
    こちらは**画面の上限が動いた**話で、ユーザーが広げた窓を狭める話ではない。

    契機の側（`<Configure>` で画面サイズの変化に気づくこと）は
    tests/test_theme.py::test_watch_display_notices_a_resolution_change_with_the_same_dpi。
    """
    screen = {"size": (1920, 1080)}
    monkeypatch.setattr(window_fit, "screen_size", lambda _w: screen["size"])
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        win, _ = _open_scenario(root)
        need_h = window_fit.required_size(win)[1]
        assert win._fit_size[1] == need_h, "前提が崩れている（最初から入っていない）"

        screen["size"] = (1920, 800)               # 解像度が下がった
        window_fit.refit_all(root)
        assert win._fit_size[1] == 800 - window_fit.SCREEN_MARGIN, (
            f"狭くなった画面に追従していない（{win._fit_size[1]}px のまま）"
            "＝窓の下端がデスクトップの外に残る。"
        )
        assert win._fit_scroll.active[0], "入らなくなったのに逃げ道が出ていない"

        screen["size"] = (1920, 1080)              # 元に戻った（再接続など）
        window_fit.refit_all(root)
        assert win._fit_size[1] == need_h, (
            f"広くなった画面に戻っていない（{win._fit_size[1]}px のまま）"
            "＝クランプされた小さいままアプリの再起動しか回復手段が無くなる。"
        )
        assert not win._fit_scroll.active[0], "入るようになったのにバーが残っている"
    finally:
        root.destroy()


# ============================================================
# 位置（B-083）— 大きさが入っていても、置き場所で外へ出る
# ============================================================
# 🔑 **ここまでのゲートは全部「大きさ」しか見ていなかった。**
# `fit_to_content` は `geometry(f"{w}x{h}")` と**大きさだけ**を渡すので、窓が
# どこに置かれるかは Windows 任せ＝**カスケード配置**（実測で `+78+78` → `+156+156`
# → `+234+234` と 78px ずつ下がる）。ランチャーは高さ 973px あるので、2 番目の
# スロットに置かれた時点で `156 + 装飾 31 + 973 = 1160px` となり FHD の外へ出る。
#
# ⚠️ **逃げ道（B-021②）はこの壊れ方に効かない**＝スクロールバーは
# `need_h > h`（＝**窓の大きさ**に入らない）ときだけ出る。ここは大きさとしては
# 足りているので受け皿は「入っている」と判断し、バーは出ないまま下端が画面外へ
# 出る。**大きさの問題と位置の問題は別物**で、片方のゲートはもう片方を守らない。
#
# 実測（2026-08-14・`wm geometry` の意味）:
#   - `geometry()` の `+x+y` と `winfo_x/y` は**装飾枠の左上**
#   - `winfo_rootx/rooty` は**クライアント領域の左上**（差＝上 31px / 左 8px）
#   ⇒ 装飾のぶんは下端の余白（`SCREEN_MARGIN`）で吸収する。
def _cascade(win, x=156, y=156):
    """Windows のカスケード配置を再現する（窓を斜め下へ置く）。

    ⚠️ **置けたことを必ず確かめる**＝`winfo_x/y` は未表示のあいだ 0 を返すので、
    それで検査すると窓は原点にあることになり**ゲートが黙って空振りする**
    （実装中に実際に起きた＝6 窓のうち 5 窓が「はみ出していない」と報告した）。
    """
    win.geometry(f"+{x}+{y}")
    win.update_idletasks()
    assert window_fit.window_position(win) == (x, y), (
        "前提が崩れている（窓を動かせていない）＝このテストは何も検査していない"
    )


@pytest.mark.parametrize("name", sorted(_WINDOWS))
def test_every_window_stays_inside_the_screen(name, monkeypatch):
    """**どの窓も、置かれた場所ごとデスクトップの中に収まっていること。**

    大きさのゲート（`test_every_window_is_usable_on_fhd`）が全部緑でも、この
    ゲートは落ちうる＝それが B-083。⚠️ **開発機の画面では絶対に発火しない**
    （WQHD は縦 1350px 使える）ので、出荷先の画面を差し込んでから測る。
    """
    from views import theme

    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
        theme.apply_fonts(root, dpi=96)
        opener, _ = _WINDOWS[name]
        win, _owner = (opener(root, monkeypatch) if name == "map" else opener(root))
        screen_w, screen_h = _FHD_SCREEN
        # ⚠️ **窓ごとに「確実に外へ出る位置」を計算して置く**。実機で起きたのは
        # `+156+156`（カスケードの 2 番目）だが、その値を全窓へ当てると**背の低い窓は
        # そこでも入ってしまい、その窓のぶんは何も検査しないテストになる**（実装中に
        # 実際そうなった＝6 窓中 4 窓が素通り）。⇒ 各窓の高さから外へ出る位置を作る。
        _cascade(win,
                 x=max(0, screen_w - win._fit_size[0] + 40),
                 y=screen_h - window_fit.SCREEN_MARGIN - win._fit_size[1] + 40)
        window_fit.refit_all(root)          # 窓ごとの事情そのままで測り直させる

        x, y = win._fit_pos
        w, h = win._fit_size
        assert x >= 0 and y >= 0, (
            f"[{name}] 窓の左上が画面の外にある（{x}, {y}）＝タイトルバーを"
            "掴めず、動かすこともできない。"
        )
        assert y + h <= screen_h - window_fit.SCREEN_MARGIN, (
            f"[{name}] 窓の下端がデスクトップの外へ出ている"
            f"（上端 {y}px + 高さ {h}px = {y + h}px / 使える高さ "
            f"{screen_h - window_fit.SCREEN_MARGIN}px）。**大きさは入っているので"
            "逃げ道（スクロール）は出ない**＝下端のボタン列に手が届かない（B-083）。"
        )
        assert x + w <= screen_w, (
            f"[{name}] 窓の右端がデスクトップの外へ出ている"
            f"（左端 {x}px + 幅 {w}px = {x + w}px / 画面 {screen_w}px）。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_a_window_that_already_fits_is_not_moved(monkeypatch):
    """**入っている窓は動かさないこと**（ゲートの壊れ方②＝毎回鳴る／勝手に動く）。

    位置を毎回書き直す実装にすると、ユーザーが置いた場所が測り直しのたびに
    リセットされる（DPI 変更・画面変更でも `refit_all` が走る）。**触るのは
    外へ出ているときだけ。**
    """
    from views import theme

    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
        theme.apply_fonts(root, dpi=96)
        win, _ = _open_multihop(root)
        _cascade(win, x=40, y=30)           # 十分に上＝そのままで入る置き場所
        window_fit.refit_all(root)
        assert win._fit_pos == (40, 30), (
            f"入っている窓を動かした（{win._fit_pos} ≠ (40, 30)）"
            "＝ユーザーが置いた場所が測り直しのたびに失われる。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


# ------------------------------------------------------------
# 複数モニタ（B-085）— 収める先は「窓が載っているモニタ」
# ------------------------------------------------------------
# 🔴 **B-083 の修正が作った退行。** `screen_size()` が返すのは**プライマリモニタ
# 1 枚**（Windows の Tk は `winfo_screenwidth/height` に `SM_CXSCREEN/SM_CYSCREEN`
# を返す＝2026-08-14 に `ctypes` と突き合わせて実測）。そこへ無条件に `max(0, …)`
# を当てると、**別モニタに置かれた正当な窓が主画面へ引き戻される**。
#
# 🔴 **最初の直しは「主画面の外なら別モニタ」で済ませ、2 巡目の独立レビューで
# 差し戻された**（2026-08-15）＝**サブモニタを外したあと残った窓**は*どこにも無い
# 場所*に居るので、別モニタ扱いで放置すると掴めないまま＝B-083 の救済の取り消し。
# ⇒ 判定は**実在する矩形と重なるか**で行い、どれとも重ならない窓だけを引き戻す。
# ⚠️ **そのとき足したゲート（「動かさないこと」）は捨てた**＝あれは*退行を正しい
# 振る舞いとして固定する*網で、[[feedback-promote-recurring-checks]] の壊れ方③
# （間違ったものを要求している）そのものだった。**直すときに邪魔をする網は、
# 直す前に外す。**
#
# 🔑 **偽のモニタ構成を注入する**＝そうしないと複数モニタの論理は「2 枚つないだ
# 機械」でしか検査できず、実質だれも検査しない（開発機は実測で `SM_CMONITORS = 1`）。
_LEFT_MONITOR  = (-1920, 0, 0, 1080)        # 主画面の左に 1 枚
_RIGHT_MONITOR = (1920, 0, 3840, 1080)      # 主画面の右に 1 枚
_PRIMARY       = (0, 0, 1920, 1080)


def _fake_monitors(monkeypatch, rects):
    monkeypatch.setattr(window_fit, "monitors", lambda _w: list(rects))


@pytest.mark.parametrize("where,rects,x,y", [
    ("左のモニタ", (_PRIMARY, _LEFT_MONITOR),  -1400, 100),
    ("右のモニタ", (_PRIMARY, _RIGHT_MONITOR),  2200, 100),
])
def test_a_window_on_another_monitor_stays_on_that_monitor(where, rects, x, y,
                                                           monkeypatch):
    """**別モニタの窓は、そのモニタの中に収める**（主画面へ引き戻さない）。

    引き戻すと、利用者がサブモニタへ避けておいた窓が測り直しのたびに主画面へ
    飛んでくる（`refit_all` は DPI 変更・画面変更でも走る）。⚠️ **「動かさない」
    ことを検査しない**＝そのモニタの下端から溢れていれば、そこでは引き上げるのが
    正しい。検査するのは**どのモニタの中に居るか**。
    """
    from views import theme

    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
        _fake_monitors(monkeypatch, rects)
        theme.apply_fonts(root, dpi=96)
        win, _ = _open_multihop(root)
        win.geometry(f"+{x}+{y}")
        win.update_idletasks()
        if window_fit.window_position(win) != (x, y):
            pytest.skip(f"WM が窓を {where} へ置かせない＝この検査は成立しない")
        window_fit.refit_all(root)

        left, top, right, bottom = rects[1]
        px, py = win._fit_pos
        w, h = win._fit_size
        assert left <= px and px + w <= right, (
            f"[{where}] 窓が別のモニタへ移された（x={px}, 幅 {w} / このモニタは "
            f"{left}〜{right}）＝サブモニタへ避けた窓が測り直しのたびに"
            "主画面へ飛んでくる（B-085）。"
        )
        assert top <= py and py + h <= bottom - window_fit.SCREEN_MARGIN, (
            f"[{where}] 窓がそのモニタの外へ出ている（y={py}, 高さ {h} / "
            f"使える下端 {bottom - window_fit.SCREEN_MARGIN}）。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_a_window_on_a_monitor_that_is_gone_is_pulled_back(monkeypatch):
    """🔴 **消えたモニタに残った窓は主画面へ引き戻すこと**（B-085・2 巡目）。

    サブモニタを外すと、そこに置いてあった窓の座標は*どのモニタにも属さない場所*
    になる。この窓は画面のどこにも描かれないので、**タイトルバーを掴むことすら
    できない**＝B-083 が救済したのとまったく同じ状態。「主画面の外だから触らない」
    と書くと、この救済が黙って取り消される（**最初の直しがそうだった**）。
    """
    from views import theme

    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
        _fake_monitors(monkeypatch, (_PRIMARY,))      # サブモニタを外した状態
        theme.apply_fonts(root, dpi=96)
        win, _ = _open_multihop(root)
        win.geometry("+2200+100")                     # 外したモニタに残った座標
        win.update_idletasks()
        if window_fit.window_position(win) != (2200, 100):
            pytest.skip("WM が窓を画面外へ置かせない＝この検査は成立しない")
        window_fit.refit_all(root)

        px, py = win._fit_pos
        w, h = win._fit_size
        assert 0 <= px and px + w <= _FHD_SCREEN[0], (
            f"消えたモニタに残った窓を引き戻していない（x={px}, 幅 {w}）"
            "＝画面のどこにも描かれず、掴むこともできない（B-083 の救済の取り消し）。"
        )
        assert 0 <= py and py + h <= _FHD_SCREEN[1] - window_fit.SCREEN_MARGIN, (
            f"引き戻した先が画面の外（y={py}, 高さ {h}）。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_a_window_is_sized_for_the_monitor_it_sits_on(monkeypatch):
    """🔴 **大きさの上限も、載っているモニタから取ること**（B-087）。

    位置だけをモニタ基準にして大きさをプライマリ基準のままにすると、**解像度の
    違うモニタで必ず溢れる**＝主画面 2560×1440 / サブ 1920×1080 の構成では、
    必要高 1023px のランチャーが**主画面基準で 1023px のまま作られ**、サブ画面の
    使える高さ 990px に対して 33px はみ出す。⚠️ **位置の調整では直らない**
    （窓自体がモニタより高いので、上端を原点へ寄せても下端が出る）＝だから
    `test_every_window_stays_inside_the_screen` では捕まらない。

    ⚠️ **2 枚を同じ解像度にしたテストではこの欠陥は出ない**（独立レビュー 3 巡目の
    指摘）＝**違う大きさのモニタを与えることが検査の中身**。
    """
    from views import theme

    big   = (0, 0, 2560, 1440)                  # 主画面（広い）
    small = (2560, 0, 4480, 1080)               # サブ画面（狭い）
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: (2560, 1440))
        _fake_monitors(monkeypatch, (big, small))
        # ⚠️ **溢れる条件を作ってから測る**＝実装中に 2 度空振りさせた（[[
        # feedback-promote-recurring-checks]] の壊れ方①）。①中継経路は元から
        # 990px に収まるので、上限をどちらから取っても同じ大きさになる
        # ②ランチャーも 96dpi では必要高 916px（実測）で収まってしまう。
        # ⇒ **いちばん背の高い窓（ランチャー）を 144dpi で**開く＝実測で必要高
        # 1197px となり、サブ画面の上限 990px を確実に超える。
        theme.apply_fonts(root, dpi=144)
        win, _ = _open_launcher(root)
        win.geometry(f"+{small[0] + 60}+40")     # サブ画面に置く
        win.update_idletasks()
        if window_fit.window_position(win) != (small[0] + 60, 40):
            pytest.skip("WM が窓をサブ画面へ置かせない＝この検査は成立しない")
        window_fit.refit_all(root)

        _w, h = win._fit_size
        _px, py = win._fit_pos
        limit = small[3] - small[1] - window_fit.SCREEN_MARGIN
        assert h <= limit, (
            f"サブ画面（高さ {small[3] - small[1]}px）に置いた窓が、主画面基準の"
            f"大きさで作られている（高さ {h}px / このモニタの上限 {limit}px）"
            "＝下端がはみ出し、しかも**大きさとしては足りている**ので"
            "逃げ道（スクロール）も出ない（B-087）。"
        )
        assert py + h <= small[3] - window_fit.SCREEN_MARGIN, (
            f"サブ画面の下端から出ている（上端 {py}px + 高さ {h}px）。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_a_manually_shrunk_window_on_a_left_monitor_is_not_pulled_back(monkeypatch):
    """🔴 **手で縮めた窓を、別モニタから引き戻さないこと**（B-089）。

    `_fit_size` は**最後に自動調整した寸法**で、**利用者が手で縮めても更新されない**。
    矩形をそこから作る（実寸との「大きい方」を採る）と、**右・下へ水増しされた
    矩形**になる。左のサブモニタの境界付近に置いた窓では、その水増し分だけが
    プライマリと重なるので `host_monitor` がプライマリを選び、**手で縮めただけの
    窓が主画面へ飛ぶ**。⚠️ **B-088 で監視に契機を足したので、ドラッグしなくても
    発火し得る**＝直前の版より起きやすくなっている。
    """
    import tkinter as tk

    from views import theme

    left = (-1920, 0, 0, 1080)
    root = make_themed_root()
    try:
        root.withdraw()
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
        _fake_monitors(monkeypatch, (_PRIMARY, left))
        theme.apply_fonts(root, dpi=96)
        win = tk.Toplevel(root)
        tk.Frame(win, width=700, height=500).pack()
        window_fit.fit_to_content(win)
        root.update()

        # 左のモニタの右端寄りへ置き、**手で小さくする**（_fit_size は 700×500 の
        # まま残る＝ここが水増しの種）。
        win.geometry("200x200+-300+100")
        root.update()
        if window_fit.window_position(win) != (-300, 100):
            pytest.skip("WM が窓を左のモニタへ置かせない＝この検査は成立しない")
        assert win.winfo_width() < win._fit_size[0], (
            "前提が崩れている（手で縮められていない）＝水増しが起きないので"
            "このテストは何も検査しない。"
        )
        window_fit.refit_all(root)

        px, _py = win._fit_pos
        assert px < 0, (
            f"手で縮めただけの窓が主画面へ引き戻された（x={px}）＝矩形を "
            "`_fit_size` から水増しして作ったため、`host_monitor` が"
            "プライマリを選んでいる（B-089）。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_the_primary_monitor_agrees_with_what_tk_reports():
    """**列挙したプライマリの矩形と Tk の `screen_size()` が一致すること。**

    位置は `monitors()`（Win32 の実測ピクセル）で決め、大きさは `screen_size()`
    （Tk）で決めている＝**2 つの座標系が食い違うと、入っているつもりの窓が出る**。
    DPI 仮想化の設定が変わるとここが割れうるので、実機で突き合わせておく。

    ⚠️ **モニタが 2 枚以上あるときは検査しない**＝プライマリは「原点が (0,0) の
    矩形」だが、それを名指しで選ぶより*1 枚のときに厳密に見る*ほうが確実で、
    食い違いが起きるとしたら DPI の話なので枚数には依らない。
    """
    root = make_themed_root()
    try:
        root.withdraw()
        rects = window_fit._enumerate_monitor_rects()
        if len(rects) != 1:
            pytest.skip(f"モニタが {len(rects)} 枚＝この検査は 1 枚のときだけ成立する")
        assert rects[0] == (0, 0, *window_fit.screen_size(root)), (
            f"Win32 の矩形 {rects[0]} と Tk の画面 {window_fit.screen_size(root)} が"
            "食い違っている＝位置と大きさが別の座標系で決まっており、"
            "「入っているつもりの窓」が画面の外へ出る。"
        )
    finally:
        root.destroy()


# ------------------------------------------------------------
# 余白は定数ではなく作業領域から（B-084）
# ------------------------------------------------------------
# 🔴 **`SCREEN_MARGIN = 90` は 100% 表示専用の見積りだった。** 内訳は「タスクバー
# 約 48px ＋ 装飾 31px」で、**どちらも DPI で拡大する**のに定数のまま。150% では
# 装飾だけで 51px あり、残り 39px では 72px のタスクバーを賄えない
# ⇒ 窓の下端 **33px が常にタスクバーの裏**（実測・2026-08-14）。
#
# ⚠️ **既存のゲートはこれを一度も見ていない**＝全部 `SCREEN_MARGIN` を期待値の
# 側にも使っており、**定数が間違っていても定数どおりなら緑**になる（＝
# [[feedback-promote-recurring-checks]] の壊れ方③＝間違ったものを要求している）。
# ここで見るのは「定数どおりか」ではなく **OS が使えると言った矩形に収まるか**。
#
# 🔑 **偽のタスクバーを注入する**（`work_areas` を差し替える）＝そうしないと
# 「タスクバーを避けられているか」は*この機械のタスクバーの高さ*でしか検査できず、
# 出荷先（FHD・150% で 72px）の条件を再現できない。conftest の
# `_taskbar_is_never_the_dev_machines` が既定で空にしてあるので、**見たいゲートだけ
# が明示的に注入する**形になっている。
def _fake_work_area(monkeypatch, monitor, work):
    monkeypatch.setattr(window_fit, "work_areas", lambda: {monitor: work})


def test_the_usable_height_comes_from_the_work_area(monkeypatch):
    """🔴 **上限はタスクバーの実寸から決まること**（B-084）。

    150% のタスクバーは 72px。定数 90px からは装飾 51px を引いて 39px しか
    残らないので、**33px がタスクバーの裏に入る**。作業領域から決めていれば
    上限はそのぶん下がる＝**古い定数より小さくなること**まで見る（そこを見ないと
    「作業領域を読んだが結局同じ値」でも緑になる）。
    """
    from views import theme

    work = (0, 0, 1920, 1008)                   # 150% のタスクバー（72px）
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
        _fake_monitors(monkeypatch, (_PRIMARY,))
        _fake_work_area(monkeypatch, _PRIMARY, work)
        theme.apply_fonts(root, dpi=144)        # 150%＝出荷先で起こり得る最大
        win, _ = _open_launcher(root)

        dec_w, dec_h = window_fit.decoration_size(win)
        assert dec_h > 0, (
            "装飾の寸法が 0＝クライアント領域と枠の差を測れていない。"
            "この状態では上限がタイトルバーのぶんだけ常に大きすぎる。"
        )
        need_h = window_fit.required_size(win)[1]
        assert need_h > work[3], (
            f"溢れない条件でテストしている（必要 {need_h}px ≤ 作業領域 {work[3]}px）"
            "＝上限の計算が壊れていても緑になる。"
        )
        x, y = win._fit_pos
        w, h = win._fit_size
        assert y + dec_h + h <= work[3], (
            f"窓の下端がタスクバーの裏に入っている（上端 {y}px ＋ 装飾 {dec_h}px ＋ "
            f"高さ {h}px = {y + dec_h + h}px / 作業領域の下端 {work[3]}px）＝B-084。"
        )
        assert x + dec_w + w <= work[2], (
            f"窓の右端が作業領域から出ている（{x + dec_w + w}px / {work[2]}px）。"
        )
        assert h < _FHD_SCREEN[1] - window_fit.SCREEN_MARGIN, (
            f"上限が古い定数のまま（高さ {h}px ≧ "
            f"{_FHD_SCREEN[1] - window_fit.SCREEN_MARGIN}px）＝作業領域を読んでいない。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_a_taskbar_on_the_left_edge_is_avoided_too(monkeypatch):
    """**タスクバーが下以外の辺にあっても避けること**（B-084 のクラス点検）。

    定数の余白は「下だけ 90px」なので、左・上・右にタスクバーを置いた構成では
    **窓がその裏に入る**。作業領域は辺の位置ごと OS が面倒を見るので、乗り換えれば
    この面もまとめて解ける＝**解けていることをここで確かめる**（解けていなければ
    「下だけ直した」実装が通ってしまう）。
    """
    from views import theme

    work = (120, 0, 1920, 1080)                 # 左端に 120px のタスクバー
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
        _fake_monitors(monkeypatch, (_PRIMARY,))
        _fake_work_area(monkeypatch, _PRIMARY, work)
        theme.apply_fonts(root, dpi=96)
        win, _ = _open_multihop(root)
        win.geometry("+0+40")                   # タスクバーの裏へ置いてみる
        win.update_idletasks()
        if window_fit.window_position(win) != (0, 40):
            pytest.skip("WM が窓を左端へ置かせない＝この検査は成立しない")
        window_fit.refit_all(root)

        x, _y = win._fit_pos
        assert x >= work[0], (
            f"窓の左端がタスクバーの裏に入っている（x={x} / 作業領域の左端 "
            f"{work[0]}）＝余白を「下だけ」で考えている（B-084）。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_the_decoration_allowance_grows_with_dpi():
    """**装飾の見積りが DPI に従うこと**（実測が取れない窓＝保険側の経路）。

    実測（`winfo_rootx/rooty` との差）が取れる窓では本物が返るが、まだ実現して
    いない窓では定数へ落ちる。⚠️ **そこが 96dpi 固定だと、B-084 の本体（余白が
    DPI に依らない）を保険の中に作り直すことになる。**
    """
    import tkinter as tk

    from views import theme

    root = make_themed_root()
    try:
        root.withdraw()
        win = tk.Toplevel(root)
        win.withdraw()                          # 実現させない＝実測は取れない
        theme.apply_fonts(root, dpi=96)
        at96 = window_fit.decoration_size(win)
        theme.apply_fonts(root, dpi=144)
        at144 = window_fit.decoration_size(win)
        assert at96[1] > 0 and at144[1] > at96[1], (
            f"装飾の見積りが DPI で変わらない（96dpi {at96} / 144dpi {at144}）"
            "＝150% ではタイトルバーが 51px あるので、96dpi 基準の 39px では"
            "足りず、そのぶん窓がタスクバーの裏へ入る（B-084）。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_the_constant_margin_is_kept_when_the_os_cannot_be_asked(monkeypatch):
    """**OS に聞けないときは従来どおり**（Windows 以外・API 無し）。

    ⚠️ ここで装飾を*さらに*引くと二重になる＝`SCREEN_MARGIN = 90` は
    「タスクバー 48 ＋ 装飾 31」を**既に含んだ**値。**保険の経路が本番より厳しく
    なると、聞けない環境でだけ窓が理由なく縮む。**
    """
    from views import theme

    root = make_themed_root()
    try:
        root.withdraw()
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
        _fake_monitors(monkeypatch, (_PRIMARY,))
        monkeypatch.setattr(window_fit, "work_areas", dict)     # 何も聞けない
        theme.apply_fonts(root, dpi=96)
        win, _ = _open_multihop(root)
        assert window_fit.usable_area(win, _PRIMARY) == (
            0, 0, 1920, 1080 - window_fit.SCREEN_MARGIN), (
            "作業領域を聞けないのに従来の見積り（SCREEN_MARGIN）へ落ちていない"
            f"＝{window_fit.usable_area(win, _PRIMARY)}。"
        )
    finally:
        theme.apply_fonts(root, dpi=96)
        root.destroy()


def test_a_monitor_whose_work_area_is_unknown_is_not_reported_as_known(monkeypatch):
    """🔴 **「聞けなかった」をモニタ矩形で代用しないこと**（B-114）。

    `work_areas` の docstring は「聞けなかった」と「タスクバーが無い」を同じ形で
    返さないと約束しているのに、列挙の側は `GetMonitorInfoW` が失敗すると
    **`rcMonitor` をそのまま作業領域として登録**していた。⇒ `usable_area` は
    「作業領域が取れた」と読み、`SCREEN_MARGIN` の保険を通らない＝**そのモニタ
    でだけ B-084 が黙って戻る**（下端がタスクバーの裏）。

    ⚠️ **モニタの列挙まで巻き添えにしない**（B-085＝別モニタに置かれた窓を主画面へ
    引き戻さない）＝そこが元の代用の理由なので、両方を同じ回で見る。
    """
    import ctypes

    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):            # Windows 以外
        pytest.skip("Win32 API のある環境でのみ意味がある")

    monkeypatch.setattr(user32, "GetMonitorInfoW", lambda *_a: 0, raising=False)
    found = window_fit._enumerate_monitors()
    assert found, "モニタの列挙まで消えている（B-085 の巻き添え）"
    assert all(work is None for _monitor, work in found), (
        f"作業領域を聞けなかったのに値を持っている: {found}")
    assert window_fit.work_areas() == {}, (
        "聞けなかったモニタが「作業領域が取れた」として並んでいる"
        "＝usable_area が SCREEN_MARGIN の保険を通らない")
    assert window_fit._enumerate_monitor_rects(), (
        "作業領域が不明なせいでモニタ矩形まで落ちている")


def test_dialogs_are_pulled_back_onto_the_screen(monkeypatch):
    """ダイアログも画面の中へ（親が下に寄っていれば子も外へ出る）。

    `_center_on` は**親の中央**へ置くだけで画面を見ていなかった＝親が画面の
    下端側にあると、子はその中央から自分の高さの半分だけ下へ出る。**同じ式が
    `launcher_menu` にも複製されていた**ので、直すなら 1 本に寄せるところまでが
    範囲（B-083 のクラス点検）。
    """
    import tkinter as tk
    from tkinter import ttk

    from views import dialogs

    root = make_themed_root()
    try:
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
        root.geometry("400x300+200+1000")           # 親が画面の下端に寄っている
        root.update_idletasks()
        dlg = tk.Toplevel(root)
        ttk.Label(dlg, text="x" * 40).pack()
        tk.Frame(dlg, height=400, width=300).pack()
        dlg.update_idletasks()
        dialogs.center_on(root, dlg)
        dlg.update_idletasks()
        x, y = dlg.winfo_x(), dlg.winfo_y()
        h = dlg.winfo_height()
        assert y >= 0 and y + h <= _FHD_SCREEN[1] - window_fit.SCREEN_MARGIN, (
            f"ダイアログが画面の外へ出ている（上端 {y}px + 高さ {h}px）"
            "＝ボタンに手が届かない。"
        )
        assert x >= 0, f"ダイアログの左端が画面の外（{x}px）"
    finally:
        root.destroy()


def test_the_escape_bar_does_not_widen_the_next_measurement(monkeypatch):
    """**出したバーを、次に測るときの必要量へ持ち越さないこと**（B-074(a)）。

    受け皿のバーは窓の中身なので、出したまま `winfo_reqwidth()` を読むと
    **バー 1 本ぶんが必要量に載る**。すると「入らなくなって出したバー」が次の
    測定を太らせ、**入るようになっても幅が戻らない**（実測 12px）。

    ⚠️ **上の `..._screen_that_shrinks_and_grows_back` では捕まらなかった**＝
    あちらは高さしか assert しておらず、**溢れるのは縦・残るのは幅**という
    ずれた面に出る（縦バーが横幅を食う）。同じ往復を**幅で**見る。
    ⚠️ DPI 経由の `..._shrink_back_when_dpi_falls` も同じ欠陥で赤くなるが、
    あちらは「表示スケールを戻したら窓も戻る」という**約束**の検査で、原因を
    名指ししていない（フォントの大きさが同時に動くので切り分けられない）。
    """
    screen = {"size": (1920, 1080)}
    monkeypatch.setattr(window_fit, "screen_size", lambda _w: screen["size"])
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        win, _ = _open_scenario(root)
        need_w = win._fit_need[0]
        assert not win._fit_scroll.active[0], "前提が崩れている（最初からバーが出ている）"

        screen["size"] = (1920, 800)               # 縦に入らなくなる＝縦バーが出る
        window_fit.refit_all(root)
        assert win._fit_scroll.active[0], "前提が崩れている（溢れたのにバーが出ない）"
        assert win._fit_need[0] == need_w, (
            "バーを出したこと自体が必要量を変えている"
            f"（{need_w} → {win._fit_need[0]}）＝必要量は中身の話で、"
            "バーはその結果でしかない。"
        )

        screen["size"] = (1920, 1080)              # また入るようになった
        window_fit.refit_all(root)
        assert win._fit_need[0] == need_w, (
            f"必要幅が {win._fit_need[0]}px へ太ったまま（本来 {need_w}px）"
            "＝出したままのバーを測ってしまい、そのぶんが必要量に焼き付いている。"
            "窓幅は `grow_only` に守られて一度太ると戻らない。"
        )
    finally:
        root.destroy()


def test_refit_all_keeps_each_window_s_own_conditions(monkeypatch):
    """測り直しで窓ごとの下限・加算が失われないこと。

    `refit_all` は窓ごとの事情（下限幅・スクロールバー分の加算）を知らないので、
    `fit_to_content` が残した `_fit_kwargs` をそのまま使う。ここが失われると
    バッチだけスクロールバー分だけ狭くなる、といった形で静かに壊れる。
    """
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        win, _ = _open_batch(root)
        kwargs = dict(win._fit_kwargs)
        window_fit.refit_all(root)
        assert win._fit_kwargs == kwargs
        assert kwargs["extra_w"] > 0, "バッチの加算（スクロールバー＋外周）が消えている"
        # バッチは「列幅同期からやり直す」再測を自前で持つ＝DPI が変わって
        # スクロールバー自体が太っても正しく測れる（保存済みの加算値は古くなる）。
        assert getattr(win, "_fit_refit", None) is not None
    finally:
        root.destroy()


# ============================================================
# 3: 登録漏れが起きないこと（新しい窓を自動で対象にする）
# ============================================================
# Toplevel を作るが「窓の見切れ」の対象外にするもの。**理由を必ず書く**
# （空の除外リストは運用で必ず膨らむので、理由ごと残して見直せるようにする）。
_EXEMPT = {
    ("dialogs.py", "_make"):
        "モーダルダイアログ＝サイズを指定せず自然サイズで開く（geometry は位置のみ）。"
        "中身はメッセージ 1 つとボタンで、増える経路が無い。",
    ("tooltip.py", "_show"):
        "ツールチップ（overrideredirect）。自然サイズ・位置のみ指定。",
    ("launcher_menu.py", "_on_proxy_settings"):
        "プロキシ設定ダイアログ＝サイズを指定せず自然サイズで開く（位置のみ）。",
    ("launcher_menu.py", "_show_readme_text"):
        "README ビューア＝スクロール前提の閲覧窓（resizable・中身は本文テキスト）。",
}


def _toplevel_sites() -> "list[tuple[str, str]]":
    """`views/` で `tk.Toplevel` を作っている場所を (ファイル, 関数/クラス名) で返す。

    - `tk.Toplevel(...)` の呼び出し
    - `class X(tk.Toplevel)` の宣言
    の両方を拾う（バッチ・条件探索は後者、地図・ダイアログは前者）。
    """
    sites: list[tuple[str, str]] = []
    for fname in sorted(os.listdir(_VIEWS_DIR)):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(_VIEWS_DIR, fname)
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=fname)
        parents: dict[ast.AST, str] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    parents[child] = node.name
                elif node in parents:
                    parents[child] = parents[node]
        for node in ast.walk(tree):
            hit = False
            if isinstance(node, ast.ClassDef):
                hit = any(
                    isinstance(b, ast.Attribute) and b.attr == "Toplevel"
                    for b in node.bases
                )
                if hit:
                    sites.append((fname, node.name))
                    continue
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute) and fn.attr == "Toplevel":
                    sites.append((fname, parents.get(node, "<module>")))
    return sorted(set(sites))


# レジストリの窓 → 実装上の Toplevel 生成箇所の対応（登録済みであることの証明）。
_REGISTERED_SITES = {
    ("graph.py",         "GraphWindow"):        "graph",
    ("multihop.py",      "MultiHopWindow"):     "multihop",
    ("batch_builder.py", "BatchBuilderWindow"): "batch",
    ("scenario.py",      "ScenarioWindow"):     "scenario",
    ("map_window.py",    "__init__"):           "map",
}


def test_every_window_is_registered_or_exempt():
    """`views/` の Toplevel 生成箇所が、登録か除外のどちらかであること。

    **これがこのファイルの本体**。個別の窓のテストは書き忘れれば存在しないが、
    ここは views を静的に走査するので、新しい窓を足した時点で必ず落ちる。
    落ちたら「レジストリ `_WINDOWS` に足す」か「理由つきで `_EXEMPT` に足す」の
    どちらかを選ぶことになる＝判断が記録に残る。
    """
    unknown = [s for s in _toplevel_sites()
               if s not in _REGISTERED_SITES and s not in _EXEMPT]
    assert not unknown, (
        f"見切れゲートの対象に入っていない窓がある: {unknown}。"
        "tests/test_window_fit.py の _WINDOWS へ登録するか、理由を書いて "
        "_EXEMPT へ入れること。"
    )


def test_registry_has_no_stale_entries():
    """レジストリ・除外リストに、実在しない窓が残っていないこと（掃除漏れ検出）。"""
    sites = set(_toplevel_sites())
    stale = [s for s in list(_REGISTERED_SITES) + list(_EXEMPT) if s not in sites]
    assert not stale, f"実在しない窓が登録されたまま: {stale}"


def test_launcher_is_covered_even_though_it_is_the_root():
    """ランチャーは `tk.Tk`（Toplevel ではない）なので静的走査に出てこない。

    走査に出ないものは**明示的に**押さえておかないと、レジストリから消えても
    誰も気づかない（「静的ガードがあるから大丈夫」の死角）。
    """
    assert "launcher" in _WINDOWS


# ============================================================
# 4: 閉じた窓が解放されること（B-050）
# ============================================================
# 🔴 **`destroy()` は Tk オブジェクトを消さない。** tkinter は親子で相互参照する
# ので、窓を閉じても Python 側は循環ゴミとして残り、**`gc.collect()` を回すまで
# 生きている**。そこまでは仕様どおりだが、この 2 つは欠陥だった（実測 2026-08-07）:
#
#   ①**そもそも解放されない窓が 2 つあった**＝バッチ（`bind_all`）と条件探索
#     （`trace_add`）。どちらも Tcl 側に参照が残り、開閉のたびに 40 / 65 個ずつ
#     **線形に積み上がる**（10 回で +400 / +650 個）。
#   ②**残骸を製品のワーカースレッドの GC が拾うと、そこが 20〜30 秒止まる**
#     （`_tkinter` はメインスレッド外からの Tcl 呼び出しを 1 個あたり約 1 秒待つ）。
#
# ⇒ ①はここで（全窓横断で）押さえ、②は `sweep_tk_garbage` の側で押さえる。
# ⚠️ **①と②は打ち消し合う**＝漏れている窓は「回収され得ない」から止まらない。
#    ①だけ直すと②が新たに出る（実測＝条件探索が 0 秒 → 35.8 秒）。**両方要る。**
#
# この検査を `_WINDOWS` の上で回すのが要点＝**新しい窓は自動で対象になる**。

#: 窓ごとの「製品の閉じる経路」。⚠️ `destroy()` を直接呼ばない＝それは利用者が
#: 通る道ではなく、後始末（trace の解除・地図の after ループ停止）を飛ばす。
_CLOSERS = {
    "batch":    lambda owner: owner._on_close_window(),
    "scenario": lambda owner: owner._on_close(),
    "graph":    lambda owner: owner._on_close(),
    "multihop": lambda owner: owner._on_close(),
    "map":      lambda owner: owner._on_close(),
}

_RELEASE_LIMIT_S = 5.0


def _live_tk_count() -> int:
    import gc
    import tkinter as tk

    return sum(1 for o in gc.get_objects()
               if isinstance(o, (tk.Misc, tk.Variable, tk.Image)))


def test_every_window_has_a_close_path_registered():
    """閉じ方を書き忘れた窓が無いこと（ランチャー＝ルートだけ対象外）。

    これが無いと、窓を足した人が `_CLOSERS` に書き忘れたとき**下の検査が黙って
    その窓を飛ばす**＝ゲートが痩せたことに誰も気づかない。
    """
    missing = [n for n in _WINDOWS if n != "launcher" and n not in _CLOSERS]
    assert not missing, (
        f"閉じ方が登録されていない窓がある: {missing}。"
        "tests/test_window_fit.py の _CLOSERS へ製品の閉じる経路を書くこと"
    )


@pytest.mark.parametrize("name", [n for n in _WINDOWS if n != "launcher"])
def test_closed_window_is_released(name, monkeypatch):
    """閉じた窓が解放されること（Tcl 側に参照を残さないこと）。

    ⚠️ **閉じるのは同期処理ではない**（測定で 3 回誤判定した）＝地図は破棄を
    `after` に載せ、その先でスレッドの終了を待つ。だから固定時間で測らず、
    **解放されるまでイベントループと GC を回し、上限で打ち切る**。
    """
    import gc
    import time
    import weakref

    root = make_themed_root()
    root.withdraw()
    i18n.set_lang("ja")
    try:
        opener = _WINDOWS[name][0]
        win, owner = (opener(root, monkeypatch) if name == "map" else opener(root))
        ref = weakref.ref(owner)
        _CLOSERS[name](owner)
        del win, owner

        deadline = time.perf_counter() + _RELEASE_LIMIT_S
        while time.perf_counter() < deadline:
            root.update()
            gc.collect()                 # ★ メインスレッドで回す（これが正しい姿）
            if ref() is None:
                break
            time.sleep(0.02)

        assert ref() is None, (
            f"{name} の窓が、閉じても解放されない（{_RELEASE_LIMIT_S:.0f} 秒待った）。"
            "Tcl 側に参照が残っている＝`bind_all` / `trace_add` / `after` を"
            "登録したまま閉じていないか。開閉のたびに積み上がる（B-050）"
        )
    finally:
        root.destroy()
        gc.collect()


class TestTkGarbageIsSweptOnTheMainThread:
    """残骸を**メインスレッドで**掃く仕掛けが生きていること（B-050 の②）。

    ⚠️ ①（解放されること）だけでは足りない＝解放できる状態にしたぶん、
    **ワーカースレッドの GC がそれを拾えるようになる**（拾うと 20〜30 秒止まる）。
    """

    def test_sweeping_collects_tk_garbage(self):
        """掃くと、閉じた窓の残骸が実際に消えること。"""
        import gc

        from views import progress

        root = make_themed_root()
        root.withdraw()
        try:
            from views.multihop import MultiHopWindow
            gc.collect()
            base = _live_tk_count()
            win = MultiHopWindow(root, sim.SimParams(_PARAMS))
            win._on_close()
            del win
            root.update()
            gc.disable()                 # 自動 GC に助けさせない
            try:
                assert _live_tk_count() > base, "残骸が積まれていない＝前提が変わった"
                progress.sweep_tk_garbage(root, interval_ms=10_000)
                assert _live_tk_count() == base, (
                    "掃いても残骸が残った＝ワーカースレッドの GC が拾える状態のまま"
                )
            finally:
                gc.enable()
        finally:
            root.destroy()
            gc.collect()

    def test_sweeping_schedules_the_next_round(self):
        """一度きりで終わらないこと（地図はゴミになるまで約 2.4 秒かかる）。"""
        calls = []

        class _FakeRoot:
            def after(self, ms, cb):
                calls.append(ms)

        from views import progress
        progress.sweep_tk_garbage(_FakeRoot(), interval_ms=1234)
        assert calls == [1234], (
            "次回を予約していない＝1 回で止まる。閉じた瞬間にはゴミになっていない窓"
            "（地図＝スレッドの終了待ち）を永久に取りこぼす"
        )

    def test_sweeping_stops_when_the_root_is_gone(self):
        """ルートが死んでいたら静かに止まること（終了時に例外を出さない）。"""
        import tkinter as tk

        class _DeadRoot:
            def after(self, ms, cb):
                raise tk.TclError("application has been destroyed")

        from views import progress
        progress.sweep_tk_garbage(_DeadRoot())      # 例外が出なければ良い

    def test_the_launcher_starts_the_sweeper(self):
        """配線＝ランチャーが掃除役を起動すること。

        窓ごとに書かず**ルートに 1 つ**置く形なので、ここが外れると
        「全窓ぶんが一斉に効かなくなる」。
        """
        from views import launcher as launcher_mod
        from views import progress

        started = []
        root = make_themed_root()
        root.withdraw()
        try:
            original = progress.sweep_tk_garbage
            launcher_mod.progress.sweep_tk_garbage = (
                lambda r, **k: started.append(r))
            try:
                launcher_mod.SimLauncher(root, lambda _t: None)
            finally:
                launcher_mod.progress.sweep_tk_garbage = original
            assert started, (
                "ランチャーが sweep_tk_garbage を起動していない＝閉じた窓の残骸が"
                "ワーカースレッドの GC に拾われる（進捗が 20〜30 秒止まる）"
            )
        finally:
            root.destroy()

    def test_the_launcher_starts_the_mailbox_delivery(self):
        """配線＝ランチャーが**投函の配達役**も起動すること（B-079）。

        投函はキューへの put だけになったので、ここが外れると
        **例外も出ないまま全窓ぶんの通知が誰にも届かない**（完了しても画面が
        変わらない）。掃除役と同じくルートに 1 つ置く形なので、外れ方も一斉。
        """
        from views import launcher as launcher_mod
        from views import progress

        started = []
        root = make_themed_root()
        root.withdraw()
        try:
            original = progress.drain_ui_mailbox
            launcher_mod.progress.drain_ui_mailbox = (
                lambda r, **k: started.append(r))
            try:
                launcher_mod.SimLauncher(root, lambda _t: None)
            finally:
                launcher_mod.progress.drain_ui_mailbox = original
            assert started, (
                "ランチャーが drain_ui_mailbox を起動していない＝ワーカーからの"
                "投函が静かに溜まり続ける（完了・エラーが画面に出ない）"
            )
        finally:
            root.destroy()

    def test_a_worker_post_reaches_the_ui_through_the_real_tk_chain(self):
        """実 Tk で、**ワーカーからの投函が実際に画面側で実行される**こと。

        フェイクの scheduler では「予約した」までしか見えない。ここは
        ランチャーが起動した配達の連鎖（`after` ポーリング）を本物で回し、
        **ワーカースレッドが Tk に触れずに UI へ届く**ことを端から端まで見る。
        """
        import threading
        import time

        from views import launcher as launcher_mod
        from views import progress

        root = make_themed_root()
        root.withdraw()
        try:
            launcher_mod.SimLauncher(root, lambda _t: None)
            arrived: list = []
            threading.Thread(
                target=lambda: progress.post_to_ui(root, lambda: arrived.append(1)),
                daemon=True,
            ).start()

            deadline = time.perf_counter() + 5.0
            while not arrived and time.perf_counter() < deadline:
                root.update()
                time.sleep(0.01)
            assert arrived, "ワーカーの投函が UI まで届かない（配達の連鎖が回っていない）"
        finally:
            root.destroy()


def _swallow(win, dw: int, dh: int) -> None:
    """WM が要求より `(dw, dh)` 小さい窓を返した状態を作る（B-119 の再現）。"""
    win.update_idletasks()
    win.geometry(f"{win.winfo_width() - dw}x{win.winfo_height() - dh}")
    win.update()


def test_a_frame_that_swallows_pixels_is_compensated_on_the_next_fit():
    """🔴 **要求した寸法に着地しなかったぶんを、次の要求で足すこと**（B-119）。

    実機（FHD/100% ＋ WQHD/150%）で撮ったログ＝別 DPI のモニタへ移った直後、
    `geometry("602x1197")` を出しても返ってくる窓は **`596x1197`**。Tk 8.6 が
    **窓枠の厚みを移る前の DPI のまま**持っているためで、6px は装飾幅が
    16 → 22 に増えた差そのもの。

    ⚠️ **呑まれた 6px を放っておくと止まらない**＝`need_w(602) > 実幅(596)` が
    永久に成立し、Tk が要求を出し直すたびにまた 6px 減る ⇒ **手を離しても
    6px ずつ縮み続ける**（実測＝602 → 596 → 590 → 584 …）。測り直しは同じ寸法を
    要求するだけなので**巻き戻しにしかならない**（ログで 2 度確認）。
    """
    import tkinter as tk
    from tkinter import ttk

    root = make_themed_root()
    root.withdraw()
    try:
        win = tk.Toplevel(root)
        ttk.Label(win, text="中身" * 20).pack()
        w, h = window_fit.fit_to_content(win, min_w=300, min_h=200)
        win.update()

        _swallow(win, 6, 0)                      # 枠が幅を 6px 呑んだ
        assert window_fit.learn_landing_slip(win) is True, (
            "要求した寸法に着地していないのに、ずれを覚えていない"
        )
        assert win._fit_slip == (6, 0), f"覚えた下駄が違う: {win._fit_slip}"

        asked: list = []
        real = tk.Toplevel.geometry
        win.geometry = (                          # 何を要求したかだけ記録する
            lambda spec=None: (asked.append(spec) if spec else None) or real(win, spec))
        window_fit.fit_to_content(win, min_w=300, min_h=200)

        sizes = [s for s in asked if "x" in s]
        assert sizes, "測り直しが大きさを要求していない"
        assert sizes[0] == f"{w + 6}x{h}", (
            f"呑まれた 6px を足さずに同じ寸法を要求している: {sizes[0]}（期待 {w + 6}x{h}）"
            "＝Tk がまた 6px 呑むので、窓は縮み続ける（B-119 の本体）。"
        )
    finally:
        root.destroy()


def test_a_size_the_user_changed_is_not_learned_as_a_frame_slip():
    """↑の裏＝**大きなずれは「枠の取り分」として覚えないこと**（B-119）。

    ⚠️ ここに上限が無いと、利用者が手で縮めた寸法を**毎回の下駄**として履き、
    以後すべての測り直しがその分ずれる（＝直したい欠陥より広い害になる）。
    枠の差は実測で幅 6px / 高さ 27px なので、桁 1 つぶんの余裕が `_FIT_SLIP_MAX`。
    """
    import tkinter as tk
    from tkinter import ttk

    root = make_themed_root()
    root.withdraw()
    try:
        win = tk.Toplevel(root)
        ttk.Label(win, text="中身" * 20).pack()
        window_fit.fit_to_content(win, min_w=400, min_h=300)
        win.update()

        _swallow(win, window_fit._FIT_SLIP_MAX + 20, 0)   # 利用者が手で大きく縮めた
        assert window_fit.learn_landing_slip(win) is False, (
            "枠の差では説明できない大きさのずれを、下駄として覚えている"
        )
        assert getattr(win, "_fit_slip", (0, 0)) == (0, 0), (
            f"下駄を履いてしまった: {win._fit_slip}"
        )
    finally:
        root.destroy()
