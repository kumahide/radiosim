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

import i18n
import simulation as sim
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
    import multihop as mh
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
    import scenario as scn
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
def test_scenario_window_width_follows_its_content():
    """条件探索の幅が中身に追従すること（余った幅を抱え込まない）。

    実機フィードバック（2026-08-01）＝「横幅が広すぎる。条件 5 列でも右が余る」。
    原因は**凍結帯**が 1050px を要求していたこと（読み取り専用のヒント文と、
    文字数で固定した ↻ ボタン）で、比較条件が 1 列でも窓は 1070px あった。
    見切れゲートは「足りているか」しか見ないので、**広すぎる方向は素通り**する。

    ⚠️ 下限（`_BASE_W`）を下げるだけでは直らない（帯の要求が下限を上回るため）。
    ここでは「窓幅 ≦ 一番広い中身 ＋ わずかな余白」を縛る。
    """
    root = make_themed_root()
    root.withdraw()
    try:
        win, _ = _open_scenario(root)
        win.update()
        body = win._fit_scroll.body
        widest = max(c.winfo_reqwidth() for c in body.winfo_children())
        assert win._fit_size[0] <= widest + 60, (
            f"窓が中身より広い（窓 {win._fit_size[0]}px / 一番広い中身 {widest}px）"
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


def test_growing_content_does_not_shrink_the_window(monkeypatch):
    """広がった窓を、あとの操作で勝手に狭めないこと。

    ユーザーが手で広げた窓／中身に合わせて広げた窓を縮めるのは、見切れの逆側の
    嫌がらせになる（条件を 1 つ消したら窓が縮んで他の入力が見えなくなる、等）。
    """
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        win, owner = _open_scenario(root)
        _grow_scenario(owner)
        root.update_idletasks()
        wide = win._fit_size[0]
        owner._remove_condition_column()
        root.update_idletasks()
        assert win._fit_size[0] == wide
    finally:
        root.destroy()


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
