"""
views/title_bar.py
==================
**タイトルバー・窓枠（非クライアント領域）の配色**（I-132・B-176〜B-179）。

なぜ [theme.py](theme.py) と別モジュールなのか＝**描いている主体が違う**。
theme.py が扱うのは sv_ttk（Tk が描く中身）の色とフォントだが、タイトルバーと枠は
**Windows が描く**ので sv_ttk も `palette()` も届かない。届ける唯一の口が
`DwmSetWindowAttribute` で、そこには Win32 の作法（装飾側 HWND・COLORREF の
バイト順・マップの時点）が丸ごと付いてくる。⇒ 色の出所（`palette()`）は theme.py
から引き、**OS へ申告する仕事だけ**をここに置く。

⚠️ **新しい窓を作る側が呼ぶのは `follow_title_bar()` の 1 つだけ**。
`apply_title_bar_theme()` を生成直後に直呼びしても**空振りする**（B-179＝下の註）。
"""

import tkinter as tk
from typing import Any

from views import theme

# DWM へ申告する属性番号（`DwmSetWindowAttribute`）。
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20       # Windows 10 20H1+ / Windows 11
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19   # Windows 10 1809〜1909
_DWMWA_BORDER_COLOR = 34                  # Windows 11 のみ
_DWMWA_CAPTION_COLOR = 35                 # Windows 11 のみ
_DWMWA_TEXT_COLOR = 36                    # Windows 11 のみ

# `SetWindowPos` の非移動フラグ（B-177＝申告だけでは塗り替わらない窓を明示的に再描画）。
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOZORDER = 0x0001, 0x0002, 0x0004
_SWP_NOACTIVATE, _SWP_FRAMECHANGED = 0x0010, 0x0020


def _decorated_hwnd(win: tk.Misc, windll: "Any | None" = None) -> "int | None":
    """`win` の非クライアント領域を持つ HWND（Windows のみ・取れなければ None）。

    🔴 **`winfo_id()` はそのまま渡せない**（I-132 落とし穴①）＝Tk はトップレベルを
    もう1段 HWND で包んでおり、`winfo_id()` が返すのはクライアント領域側の子 HWND。
    それを渡しても `DwmSetWindowAttribute` は **`S_OK`（成功）を返すのに見た目は
    変わらない**（[[feedback-diff-before-gui-repro]]）。装飾を持つのは `GetParent()` の側。

    Args:
        windll: 差し替え用の `ctypes.windll` 代役（テスト用・省略時は実物）。
    """
    import sys
    if sys.platform != "win32":
        return None
    import ctypes
    dll = windll if windll is not None else ctypes.windll
    try:
        child = win.winfo_id()
        parent = dll.user32.GetParent(child)
        return int(parent) if parent else None
    except Exception:
        return None


def _colorref(rgb_hex: str) -> int:
    """`#rrggbb` → `COLORREF`（`0x00BBGGRR`＝RGB とバイト順が逆）。"""
    r, g, b = (int(rgb_hex[i:i + 2], 16) for i in (1, 3, 5))
    return (b << 16) | (g << 8) | r


def apply_title_bar_theme(win: tk.Misc, windll: "Any | None" = None) -> bool:
    """`win` のタイトルバー・枠を現在テーマへ合わせる（Windows のみ）。

    呼び出し元は 2 つに集約する＝`<<ThemeChanged>>`（テーマの切替）と新規トップレベルの
    **マップ**（`follow_title_bar`＝dialogs.py・map_window.py・launcher_menu.py 等）。
    片方だけだと「あとから開いた窓だけ白い」「切替の瞬間だけ直らない」が残る
    （[[feedback-promote-recurring-checks]]）。⚠️ **表示済みの窓は即座に塗り替わらない
    ことがある**（I-132 落とし穴③）＝下の `SetWindowPos(SWP_FRAMECHANGED)` で促す（B-177）。
    ⛔ **生成直後に呼んでも効かない**（B-179）＝装飾側 HWND がまだ無い。新規の窓は
    直に呼ばず `follow_title_bar()` を使うこと。

    Args:
        windll: 差し替え用の `ctypes.windll` 代役（テスト用・省略時は実物）。
    Returns:
        実際に申告できたか。ヘッドレスでは色まで測れないので、ゲートで見られるのは
        「呼んだか」まで（I-132 落とし穴⑤）。
    """
    import ctypes
    dll = windll if windll is not None else ctypes.windll
    hwnd = _decorated_hwnd(win, dll)
    if not hwnd:
        return False
    dwm = dll.dwmapi
    dark = theme.current_theme(win) == "dark"
    # `ctypes.pointer()`（`byref()` ではなく）＝テストの代役から中身を読めるようにする。
    value = ctypes.pointer(ctypes.c_int(1 if dark else 0))
    ok = dwm.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd), _DWMWA_USE_IMMERSIVE_DARK_MODE,
        value, ctypes.sizeof(value.contents)) == 0
    if not ok:
        ok = dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD,
            value, ctypes.sizeof(value.contents)) == 0
    # Windows 11 のみ＝任意色（`theme.palette()` をそのまま流す）。失敗しても致命的でない。
    colors = theme.palette(win)
    for attr, key in (
        (_DWMWA_CAPTION_COLOR, "bg"), (_DWMWA_TEXT_COLOR, "fg"),
        (_DWMWA_BORDER_COLOR, "bg"),
    ):
        cref = ctypes.pointer(ctypes.c_int(_colorref(colors[key])))
        dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), attr, cref, ctypes.sizeof(cref.contents))
    # 表示中の窓へ即座に反映させる（B-177）。移動・大きさ・フォーカスは変えない。
    try:
        dll.user32.SetWindowPos(
            ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
            _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
    except Exception:
        pass
    return ok


def follow_title_bar(win: tk.Misc, windll: "Any | None" = None) -> bool:
    """新しく作った窓のタイトルバーを、**表示され次第**テーマへ合わせる（B-179）。

    🔴 **生成直後には当てられない**（2026-09-06 実測・`_decorated_hwnd` の註の続き）＝
    Tk が装飾側の HWND（ラッパー）を作るのは**窓をマップするとき**で、それ以前に
    `GetParent()` が返すのは **0**。つまり `super().__init__()` の直後に
    `apply_title_bar_theme()` を呼んでも、送り先が無いので**何もせず False を返す**
    ＝B-178 の「表示前に当てる」は全窓で空振りしていた（症状＝複数経路・中継経路を
    開いても白いまま、あとで別の窓が `<<ThemeChanged>>` を撒いた瞬間にまとめて
    ダークになる）。⚠️ **`DwmSetWindowAttribute` が S_OK を返すのに変わらない**
    のとは別の失敗で、こちらは**呼びもしていない**＝戻り値を見ていれば分かった。

    ⇒ その場で 1 度試したうえで、**`<Map>` でも当て直す**。以後の
    アイコン化からの復帰でも当たるが、同じ値を送り直すだけなので害はない。

    Args:
        windll: 差し替え用の `ctypes.windll` 代役（テスト用・省略時は実物）。
    Returns:
        **その場で**当てられたか（まだマップされていなければ False＝`<Map>` 待ち）。
    """
    top = win.winfo_toplevel()

    def _on_map(event: "tk.Event") -> None:
        # ⚠️ **自分自身の `<Map>` だけ見る**＝子ウィジェットの bindtags にはこの
        # トップレベルのパス名が入るので、中身が 1 つ現れるたびに飛んでくる。
        if event.widget is top:
            apply_title_bar_theme(top, windll)

    try:
        top.bind("<Map>", _on_map, add="+")
    except tk.TclError:
        pass       # 破棄途中の窓
    return apply_title_bar_theme(top, windll)


def apply_title_bars(root: tk.Misc) -> None:
    """`root` と配下の全トップレベルへタイトルバーの配色を当て直す。

    `watch_display` と同じ窓の集め方（`window_fit.toplevels`）を使う（割れると穴になる）。
    """
    from views import window_fit          # 遅延 import（循環回避）

    for win in (root.winfo_toplevel(), *window_fit.toplevels(root)):
        try:
            apply_title_bar_theme(win)
        except tk.TclError:
            pass   # 破棄途中のウィジェット
