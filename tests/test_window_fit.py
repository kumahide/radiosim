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
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
        theme.apply_fonts(root, dpi=96)
        opener, _ = _WINDOWS[name]
        win, _owner = (opener(root, monkeypatch) if name == "map" else opener(root))
        need_w, need_h = window_fit.required_size(win)
        lim_w, lim_h = _FHD_LIMIT
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
        monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
        theme.apply_fonts(root, dpi=dpi)
        opener, _ = _WINDOWS[name]
        win, _owner = (opener(root, monkeypatch) if name == "map" else opener(root))
        need_w, need_h = window_fit.required_size(win)
        lim_w, lim_h = _FHD_LIMIT
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

    monkeypatch.setattr(window_fit, "screen_size", lambda _w: _FHD_SCREEN)
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
        lim_h = _FHD_SCREEN[1] - window_fit.SCREEN_MARGIN
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
