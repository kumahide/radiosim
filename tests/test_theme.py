"""
tests/test_theme.py
===================
素の tk ウィジェット（tk.Menu / tk.Canvas）へ渡すテーマ色のゲート。

**なぜこのテストが要るか**（B-008・2026-07-22）：テーマ色は
`ttk.Style().lookup("TFrame", "background")` から取っていたが、sun-valley は
その属性を設定しないため lookup は **常に空文字**を返す。空でも例外にならず、
呼び出し側は「色が取れなかったので何もしない」と黙って握り潰していた。結果、
B-004（メニューの ✓）の修正と I-005（バッチのキャンバス背景）は 2 版のあいだ
何もしていなかった。**「色が実際に付くこと」「その色が背景から見分けられること」
を検証していれば初日に落ちた**ので、注意書きではなくゲートにする
（[[feedback-promote-recurring-checks]]）。
"""

import tkinter as tk
from tkinter import ttk

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from conftest import make_tk_root, set_theme
from views import theme

_THEMES = ("light", "dark")

# バッチ窓の生成に要る最小パラメータ（値そのものは配色検証に無関係）。
_PARAMS = {
    "start"      : "34.5429, 132.4118",
    "end"        : "34.5389, 132.4050",
    "h_tx"       : "30.0",
    "h_rx"       : "10.0",
    "freq"       : "2400.0",
    "p_tx"       : "20.0",
    "gain_tx"    : "3.0",
    "gain_rx"    : "3.0",
    "sens"       : "-85.0",
    "veg_h"      : "10.0",
    "k_factor"   : "10.0",
    "samples"    : "50",
    "diff_method": "deygout",
    "env_type"   : "los",
    "rain_rate"  : "0.0",
}


@pytest.fixture
def root():
    r = make_tk_root()
    r.withdraw()
    try:
        yield r
    finally:
        r.destroy()


def _relative_luminance(color: str) -> float:
    """WCAG の相対輝度。"""
    channels = []
    for i in (1, 3, 5):
        c = int(color[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg: str, bg: str) -> float:
    """WCAG のコントラスト比（1.0〜21.0）。"""
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    lo, hi = sorted((a, b))
    return (hi + 0.05) / (lo + 0.05)


def test_ttk_lookup_is_unusable_for_theme_colors(root):
    """sun-valley では ttk lookup から地の色が取れないこと（B-008 の根本原因）。

    これは仕様の記録でもある。将来 sv_ttk が `-background` を設定するように
    なればこのテストが落ち、views/theme.py の迂回を畳んでよいと分かる。
    """
    for name in _THEMES:
        set_theme(name)
        style = ttk.Style(master=root)
        assert style.lookup("TFrame", "background") == "", (
            "ttk lookup から背景色が取れるようになった。views/theme.py の "
            "sv_ttk 内部参照は不要かもしれない（前提の変化を確認すること）。"
        )


def test_palette_comes_from_sv_ttk_not_fallback(root):
    """パレットが sv_ttk の実値で埋まり、控えの値と一致すること。

    控え（_FALLBACK）が古くなると、sv_ttk のテーマ更新後に**見た目だけ**ズレる
    （例外は出ない）。両者の突合をここで強制する。
    """
    for name in _THEMES:
        set_theme(name)
        colors = theme.palette(root)
        assert set(colors) == set(theme._KEYS)
        for key, value in colors.items():
            assert value.startswith("#") and len(value) == 7, f"{name}/{key}={value!r}"

        # 出所が sv_ttk であることを確かめる。控えは実値と同じ値なので、
        # palette() の戻り値だけ見ても「控えに落ちている」ことは分からない
        # （B-008 と同じ silent degradation）。読み取り関数を直接呼ぶ。
        from_sv_ttk = theme.read_sv_ttk_colors(root, name)
        assert from_sv_ttk is not None, (
            f"sv_ttk の色配列（::ttk::theme::{theme._NAMESPACE[name]}::colors）が"
            "読めない。views/theme.py が控えの値へ黙って落ちている。"
        )
        assert colors == from_sv_ttk

        assert colors == theme._FALLBACK[name], (
            f"sv_ttk の {name} テーマ色が views/theme.py の控えと食い違う: "
            f"{colors} != {theme._FALLBACK[name]}。控えを実値へ更新すること。"
        )


def test_palette_differs_between_themes(root):
    """light と dark で地の色が実際に変わること（テーマ非追従の検出）。"""
    seen = {}
    for name in _THEMES:
        set_theme(name)
        seen[name] = theme.palette(root)["bg"]
    assert seen["light"] != seen["dark"], f"テーマを変えても背景色が同じ: {seen}"


@pytest.mark.parametrize("name", _THEMES)
def test_menu_colors_are_legible(root, name):
    """メニューの前景が、通常時もアクティブ時も背景から見分けられること。

    B-008 の本体＝アクティブ行のカスケード「▶」が既定の activeforeground（白）で
    描かれ、Win11 の淡いハイライト背景に溶けて見えなかった。tk.Menu の
    selectcolor（✓）は状態別に指定できないので、前景色は状態で変えず、アクティブ
    背景を地の色の濃淡にする方針を取っている（views/theme.py）。その方針が実際に
    コントラストを保っているかを数値で確かめる。
    """
    set_theme(name)
    options = theme.menu_options(root)
    for bg_key in ("background", "activebackground"):
        ratio = _contrast(options["foreground"], options[bg_key])
        assert ratio >= 4.5, (
            f"{name} テーマの {bg_key} と前景のコントラストが不足（{ratio:.1f}:1）。"
            "ラベル・カスケードの ▶・チェックの ✓ が背景と同化する。"
        )
    # ✓ とアクティブ前景も同じ前景色であること（片方だけ溶ける事故の防止）。
    assert options["selectcolor"] == options["foreground"]
    assert options["activeforeground"] == options["foreground"]


def test_apply_menu_theme_actually_sets_colors(root):
    """apply_menu_theme が tk.Menu に実際の色を設定すること。

    「色が取れなければ何もしない」で黙って無効化されたのが B-008 なので、
    設定後の cget が空でなく、パレット値と一致することまで見る。
    """
    set_theme("dark")
    menu = tk.Menu(root, tearoff=False)
    theme.apply_menu_theme([menu], root)
    expected = theme.menu_options(root)
    for option, value in expected.items():
        assert str(menu.cget(option)) == value, f"{option} が適用されていない"


def _attached_menus(root: tk.Misc) -> "list[tk.Menu]":
    """ウィンドウに実際に付いている全メニュー（メニューバー＋全サブメニュー）。

    アプリの登録リスト（`_themed_menus`）ではなく **Tk 側から辿る**。リストを
    見に行くと「メニューを足したのに登録し忘れた」ケースが検出できない
    （検証で実際に生き残った変異）。
    """
    name = root.cget("menu")
    if not name:
        return []
    found: list[tk.Menu] = []
    pending = [root.nametowidget(name)]
    while pending:
        menu = pending.pop()
        found.append(menu)
        end = menu.index("end")
        if end is None:
            continue
        for index in range(end + 1):
            if menu.type(index) == "cascade":
                child = menu.entrycget(index, "menu")
                if child:
                    pending.append(menu.nametowidget(child))
    return found


def test_launcher_menus_all_get_themed():
    """ランチャーが持つ全 tk.Menu に配色が適用されること（B-004/B-008）。

    メニューを1つ足したとき `_themed_menus` への追加を忘れると、その1枚だけ
    素の色になる（ダークで文字が読めない）。そのため検証対象は Tk から辿った
    実物のメニュー群にする。
    """
    root = make_tk_root()
    try:
        root.withdraw()
        set_theme("dark")
        from views.launcher import SimLauncher
        SimLauncher(root, lambda _t: None)   # メニューは生成の副作用として付く

        expected = theme.menu_options(root)
        menus = _attached_menus(root)
        assert len(menus) >= 6, f"メニューを辿れていない（{len(menus)} 枚）"
        for menu in menus:
            assert str(menu.cget("selectcolor")) == expected["selectcolor"]
            assert str(menu.cget("activeforeground")) == expected["activeforeground"]

        # テーマ切替で再適用されること（メニューからの明示切替も system 連動も
        # この経路）。**foreground だけを見てはいけない**：sv_ttk 自身が
        # <<ThemeChanged>> で tk_setPalette を呼び、background/foreground は
        # 勝手に追従してしまう。自前の適用が効いているかを判定できるのは
        # sv_ttk が別の値（白）を入れる activeforeground と selectcolor の側で、
        # 実際 foreground だけ見ていたテストは再適用を殺す変異を素通しした。
        set_theme("light")
        root.update()
        light = theme.menu_options(root)
        assert light["activeforeground"] != expected["activeforeground"], "前提: 色が変わるはず"
        for menu in _attached_menus(root):
            for option in ("foreground", "activeforeground", "selectcolor"):
                assert str(menu.cget(option)) == light[option], (
                    f"テーマ切替後にメニューの {option} が追従していない"
                )
    finally:
        root.destroy()


def test_batch_canvas_uses_theme_background():
    """バッチ表のキャンバス背景がテーマ色になること（I-005 の実効性）。"""
    root = make_tk_root()
    try:
        root.withdraw()
        set_theme("dark")
        import simulation as sim
        from views.batch_builder import BatchBuilderWindow
        win = BatchBuilderWindow(root, sim.SimParams(_PARAMS))
        expected = theme.palette(root)["bg"]
        assert str(win._canvas.cget("bg")) == expected, (
            "キャンバス背景がテーマ色になっていない（素の tk 既定のまま）"
        )
    finally:
        root.destroy()
