"""
tests/test_title_bar.py
=======================
タイトルバー・窓枠（非クライアント領域）の申告のゲート＝[views/title_bar.py](../views/title_bar.py)。

⚠️ **ヘッドレスでは実際の色は測れない**（I-132 落とし穴⑤）ので、ここで見られるのは
「**届く状態で申告したか**」まで。⛔ **「呼んだか」だけを見るゲートにしないこと**
＝それは製品が壊れたまま緑になる（B-179＝実例。装飾側 HWND は窓をマップするまで
存在せず、生成直後の申告は送り先の無いまま終わっていた）。実際の色は
`experiments` の画素実測か実機で見る。
"""

import sys
import tkinter as tk
from tkinter import ttk
from typing import Any

import pytest

from conftest import make_tk_root, set_theme
from views import title_bar


@pytest.fixture
def root():
    r = make_tk_root()
    r.withdraw()
    try:
        yield r
    finally:
        r.destroy()

# ============================================================
# 非クライアント領域は Windows が描くので sv_ttk も palette() も届かない。
# ヘッドレスでは実際の色は測れないので、**ここで見られるのは「呼んだか」まで**
# （I-132 実装上の落とし穴⑤）。


def test_colorref_reverses_byte_order():
    """`#rrggbb` → `COLORREF`（`0x00BBGGRR`）へバイト順を反転すること。"""
    assert title_bar._colorref("#112233") == 0x00332211
    assert title_bar._colorref("#ff0000") == 0x000000ff
    assert title_bar._colorref("#00ff00") == 0x0000ff00
    assert title_bar._colorref("#0000ff") == 0x00ff0000


class _FakeDwmDll:
    """`ctypes.windll` の代役＝`GetParent` と `DwmSetWindowAttribute` の呼び出しを記録する。"""

    # `dwmapi` は試験ごとに別の代役クラスへ差し替える（属性差し替えを許すための
    # 型宣言＝上の `_FakeWindll._User32.SetProcessDpiAwarenessContext` と同じ形）。
    dwmapi: Any

    def __init__(self, *, parent_hwnd: int = 4242, dwm_ok: bool = True) -> None:
        self.calls: list[tuple] = []
        self._parent_hwnd = parent_hwnd
        self._dwm_ok = dwm_ok
        outer = self

        class _User32:
            def GetParent(self, child):        # noqa: N802 — Win32 の名前
                outer.calls.append(("GetParent", child))
                return outer._parent_hwnd

            def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):  # noqa: N802
                outer.calls.append(("SetWindowPos", hwnd.value, flags))
                return 1

        class _Dwmapi:
            def DwmSetWindowAttribute(self, hwnd, attr, value_ref, size):  # noqa: N802
                raw = value_ref.contents.value
                outer.calls.append(("DwmSetWindowAttribute", hwnd.value, attr, raw))
                return 0 if outer._dwm_ok else 1     # S_OK = 0

        self.user32 = _User32()
        self.dwmapi = _Dwmapi()


def test_decorated_hwnd_asks_get_parent_not_winfo_id(root):
    """`winfo_id()` を直に渡さず `GetParent` の返す HWND を使うこと（実装上の落とし穴①）。

    間違えて `winfo_id()` を渡しても `DwmSetWindowAttribute` は成功を返すので、
    「呼んだ HWND」まで見ないとこの取り違えは検出できない
    （[[feedback-diff-before-gui-repro]]）。
    """
    fake = _FakeDwmDll(parent_hwnd=9999)
    hwnd = title_bar._decorated_hwnd(root, fake)
    assert hwnd == 9999
    assert fake.calls == [("GetParent", root.winfo_id())]


def test_apply_title_bar_theme_sends_dark_mode_to_the_decorated_hwnd(root):
    """ダークテーマなら `GetParent` の返す HWND へ「有効」を送ること。"""
    set_theme("dark")
    fake = _FakeDwmDll(parent_hwnd=7777)
    ok = title_bar.apply_title_bar_theme(root, fake)
    assert ok is True
    dwm_calls = [c for c in fake.calls if c[0] == "DwmSetWindowAttribute"]
    assert dwm_calls, "DwmSetWindowAttribute が一度も呼ばれていない"
    hwnd, attr, value = dwm_calls[0][1], dwm_calls[0][2], dwm_calls[0][3]
    assert hwnd == 7777, "GetParent が返した装飾側 HWND ではなく別の値へ送っている"
    assert attr == title_bar._DWMWA_USE_IMMERSIVE_DARK_MODE
    assert value == 1, "ダークテーマなのに 1（有効）を送っていない"


def test_apply_title_bar_theme_light_sends_disabled(root):
    """ライトテーマなら「無効」（0）を送ること。"""
    set_theme("light")
    fake = _FakeDwmDll(parent_hwnd=7777)
    title_bar.apply_title_bar_theme(root, fake)
    dwm_calls = [c for c in fake.calls if c[0] == "DwmSetWindowAttribute"]
    assert dwm_calls[0][3] == 0, "ライトテーマなのに 0（無効）を送っていない"


def test_apply_title_bar_theme_falls_back_to_the_old_attribute_number(root):
    """新しい属性番号（20）が失敗したら旧番号（19・Win10 1809〜1909）を試すこと。"""
    class _OldOnlyDwmapi:
        def __init__(self):
            self.attrs_tried: list[int] = []

        def DwmSetWindowAttribute(self, hwnd, attr, value_ref, size):
            self.attrs_tried.append(attr)
            return 0 if attr == title_bar._DWMWA_USE_IMMERSIVE_DARK_MODE_OLD else 1

    fake = _FakeDwmDll(parent_hwnd=1)
    fake.dwmapi = _OldOnlyDwmapi()
    ok = title_bar.apply_title_bar_theme(root, fake)
    assert ok is True
    assert title_bar._DWMWA_USE_IMMERSIVE_DARK_MODE in fake.dwmapi.attrs_tried
    assert title_bar._DWMWA_USE_IMMERSIVE_DARK_MODE_OLD in fake.dwmapi.attrs_tried


def test_apply_title_bar_theme_without_hwnd_sends_nothing(root):
    """`GetParent` が HWND を返さない（0）ときは何も送らず False を返すこと。"""
    fake = _FakeDwmDll(parent_hwnd=0)
    ok = title_bar.apply_title_bar_theme(root, fake)
    assert ok is False
    assert not any(c[0] == "DwmSetWindowAttribute" for c in fake.calls), (
        "HWND が無いのに DwmSetWindowAttribute を呼んでいる"
    )


def test_apply_title_bar_theme_forces_a_repaint_of_the_decorated_hwnd(root):
    """申告後に `SetWindowPos(SWP_FRAMECHANGED)` で表示済みの窓を再描画させること（B-177）。

    `DwmSetWindowAttribute` は成功（`S_OK`）を返すのに、表示済みの窓は何かが
    再描画を誘発するまで見た目が変わらないことがある。ランチャー（`root`）だけが
    テーマ設定ダイアログを閉じた際のフォーカス復帰でたまたま再描画され、他の
    開いている窓（マップ・ダイアログ等）は追従しなかった。
    """
    set_theme("dark")
    fake = _FakeDwmDll(parent_hwnd=5555)
    title_bar.apply_title_bar_theme(root, fake)
    pos_calls = [c for c in fake.calls if c[0] == "SetWindowPos"]
    assert pos_calls, "SetWindowPos を一度も呼んでいない"
    hwnd, flags = pos_calls[0][1], pos_calls[0][2]
    assert hwnd == 5555, "GetParent が返した装飾側 HWND ではなく別の値へ送っている"
    assert flags & title_bar._SWP_FRAMECHANGED, "SWP_FRAMECHANGED を立てていない"
    assert flags & title_bar._SWP_NOMOVE and flags & title_bar._SWP_NOSIZE, (
        "位置・大きさを変えない指定が抜けている"
    )
    assert flags & title_bar._SWP_NOACTIVATE, "フォーカスを奪う指定になっている"


def test_apply_title_bar_theme_without_hwnd_does_not_call_set_window_pos(root):
    """HWND が取れないときは `SetWindowPos` も呼ばないこと。"""
    fake = _FakeDwmDll(parent_hwnd=0)
    title_bar.apply_title_bar_theme(root, fake)
    assert not any(c[0] == "SetWindowPos" for c in fake.calls)


@pytest.mark.skipif(sys.platform != "win32", reason="装飾側 HWND は Windows のみ")
def test_a_fresh_toplevel_has_no_decorated_hwnd_until_it_is_mapped(root):
    """🔴 **この項目の linchpin**（B-179）＝生成直後の窓に装飾側 HWND は**無い**。

    Tk がラッパー（非クライアント領域を持つ HWND）を作るのは**マップするとき**で、
    それ以前の `GetParent()` は 0 を返す。⇒ `super().__init__()` の直後に
    `apply_title_bar_theme()` を呼んでも**送り先が無い**（B-178 の直しが全窓で
    空振りしていた理由）。ここが変わったら B-179 の対策ごと考え直すこと。
    """
    top = tk.Toplevel(root)
    try:
        assert title_bar._decorated_hwnd(top) is None, (
            "生成直後に装飾側 HWND が取れている＝この項目の前提が変わった"
        )
        top.update()                      # ここで初めてラッパーができる
        assert title_bar._decorated_hwnd(top) is not None, "マップ後も HWND が取れない"
    finally:
        top.destroy()


class _LateWrapperDwmDll(_FakeDwmDll):
    """実物と同じく、**マップされるまで `GetParent` が 0** を返す代役（B-179）。"""

    def __init__(self, win: tk.Misc, **kw) -> None:
        super().__init__(**kw)
        outer, real = self, self.user32

        class _User32:
            def GetParent(self, child):        # noqa: N802 — Win32 の名前
                if not win.winfo_ismapped():
                    outer.calls.append(("GetParent", child))
                    return 0
                return real.GetParent(child)

            def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):  # noqa: N802
                return real.SetWindowPos(hwnd, after, x, y, cx, cy, flags)

        self.user32 = _User32()


def test_follow_title_bar_applies_when_the_window_is_actually_mapped(root):
    """まだマップされていない窓は `<Map>` を待って当て直すこと（B-179）。

    生成直後の 1 回きりだと**何も送られない**（上の linchpin）。症状＝複数経路・
    中継経路を開いても白いまま、あとで別の窓が `<<ThemeChanged>>` を撒いた瞬間に
    まとめてダークになる（2026-09-06 のユーザー報告）。
    """
    set_theme("dark")
    top = tk.Toplevel(root)
    top.withdraw()                        # まだ画面に出していない＝ラッパー無し
    try:
        fake = _LateWrapperDwmDll(top, parent_hwnd=3131)
        applied = title_bar.follow_title_bar(top, fake)
        assert applied is False, "マップ前なのに当てられたと報告している"
        assert not [c for c in fake.calls if c[0] == "DwmSetWindowAttribute"], (
            "送り先が無いのに DwmSetWindowAttribute を呼んでいる"
        )
        top.deiconify()
        top.update()                      # <Map> をここで処理させる
        dwm = [c for c in fake.calls if c[0] == "DwmSetWindowAttribute"]
        assert dwm, "マップされても当て直していない（B-179 の再発）"
        assert dwm[0][1] == 3131, "装飾側 HWND ではない先へ送っている"
        assert dwm[0][3] == 1, "ダークテーマなのに 1（有効）を送っていない"
    finally:
        top.destroy()


def test_follow_title_bar_ignores_map_events_from_child_widgets(root):
    """子ウィジェットの `<Map>` では当て直さないこと（bindtags で飛んでくる）。

    ⚠️ 中身が 1 つ現れるたびに申告し直すと、窓を建てるだけで `SetWindowPos` が
    何十回も走る。**自分自身の `<Map>` だけ**を見ていることを固定する。
    """
    set_theme("dark")
    top = tk.Toplevel(root)
    try:
        fake = _FakeDwmDll(parent_hwnd=4141)
        title_bar.follow_title_bar(top, fake)
        top.update()
        before = len([c for c in fake.calls if c[0] == "DwmSetWindowAttribute"])
        for _ in range(5):
            ttk.Label(top, text="x").pack()
        top.update()
        after = len([c for c in fake.calls if c[0] == "DwmSetWindowAttribute"])
        assert after == before, "子ウィジェットの <Map> で当て直している"
    finally:
        top.destroy()


def test_new_windows_do_not_call_apply_title_bar_theme_directly():
    """新規の窓は `follow_title_bar` を通すこと（B-179＝生成直後の直呼びは空振り）。

    直呼びは戻り値が False になるだけで**静かに失敗する**ので、画面を見るまで
    分からない。⇒ 書き方の側を固定する（[[feedback-promote-recurring-checks]]）。
    ⚠️ `main.py` の root だけは例外＝`-alpha 0` + `deiconify()` でラッパーを
    先に作ってあるので直呼びしてよい（B-176）。
    """
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in sorted((repo / "views").glob("*.py")):
        if path.name == "title_bar.py":
            continue
        for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "apply_title_bar_theme(" in line:
                offenders.append(f"{path.name}:{num}")
    assert not offenders, (
        "新規の窓が apply_title_bar_theme を直接呼んでいる（生成直後は空振りする）＝"
        f"follow_title_bar を使うこと: {offenders}"
    )


def test_apply_title_bars_covers_root_and_open_toplevels(root):
    """`apply_title_bars` が root と配下の全トップレベルへ当てること（実装上の落とし穴②）。

    ①`<<ThemeChanged>>` ②新規トップレベルの生成直後、の**両方**から同じ口を通す
    設計なので、この関数自体は「開いている窓すべて」を漏らさず拾えることだけを見る。
    """
    seen: list[tk.Misc] = []
    original = title_bar.apply_title_bar_theme

    def spy(win, windll=None):
        seen.append(win)
        return True

    child = tk.Toplevel(root)
    try:
        title_bar.apply_title_bar_theme = spy
        try:
            title_bar.apply_title_bars(root)
        finally:
            title_bar.apply_title_bar_theme = original
        assert root in seen, "root 自身へ当てていない"
        assert child in seen, "開いているトップレベルへ当てていない"
    finally:
        child.destroy()
