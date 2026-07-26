"""
views/theme.py
==============
sv_ttk（Sun Valley）の**見た目（色とフォント）の単一ソース**。ttk 管理外の素の
tk ウィジェットへ色を渡す役と、全窓の既定フォント・表の余白を決める役を持つ。

フォントについては `ui_font` / `apply_fonts` / `table_style` の docstring を見ること
（2.5b2 / I-023：窓ごとに `("Arial", 8)` `("Arial", 9)` をベタ書きしていたのを、
配色と同じく「出所は sv_ttk・集約は theme.py」へ寄せた）。

**なぜ専用モジュールが要るか**：`ttk.Style().lookup("TFrame", "background")` は
sun-valley では **常に空文字を返す**。このテーマは `.` / `TFrame` / `TLabel` に
`-background` / `-foreground` を設定せず、外観をすべて画像スプライトで描くため
（色を持つのは Treeview など一部だけ）。lookup は空でも例外にならないので、
「テーマ色を明示適用する」つもりのコードが **黙って無効化** される。実際 B-004
（メニューの ✓）の修正と I-005（バッチのキャンバス背景）は、この理由で 2 版の
あいだ何もしていなかった（B-008 で判明）。

そこで sv_ttk 自身が持つ色配列（`::ttk::theme::sv_light::colors` など）を読む。
テーマ定義そのものが出所なので二重管理にならない。読めなかった場合の控えの値も
持つが、**控えが sv_ttk の実値と一致することはテストで強制する**（黙って古い色へ
落ちるのを防ぐ＝この不具合そのものの再発防止）。

配色の決め方（B-008）：アクティブ（ホバー中）の項目も **前景色を変えない**。
tk.Menu の `selectcolor`（ラジオ/チェックの ✓）は状態別に指定できないため、
アクティブ時だけ別の前景色にすると、✓ か ▶ のどちらかが必ず背景と同化する。
アクティブ背景を「地の色を前景側へ少し寄せた濃淡」にすれば、ラベル・▶・✓ の
すべてが同じ前景色のまま十分なコントラストを保てる。

B-008 の実際の症状＝アクティブ行のカスケード「▶」だけが白で描かれ、Win11 の
淡いハイライト背景に溶けた（非アクティブ行の ▶ は黒で見えていた）。白の出所は
2つあり、どちらの経路でも同じ結果になる：素の tk.Menu の既定 `activeforeground`
＝`SystemHighlightText`（白）と、sv_ttk が `<<ThemeChanged>>` で呼ぶ
`tk_setPalette activeForeground` ＝ `colors(-selfg)`（light/dark とも `#ffffff`）。
なお sv_ttk の `config_menus` は **win32 では即 return** する（メニューの配色は
Windows では最初からアプリ側の責任）。

適用順：`tk_setPalette` は sv_ttk の Tk クラスバインドから走り、こちらは root への
バインドなので**自前の適用が後勝ち**になる（実測で確認・tests/test_theme.py が
activeforeground で守る）。
"""

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

# 色キー（sv_ttk の colors 配列のキーから先頭の "-" を除いたもの）
_KEYS = ("fg", "bg", "selfg", "selbg", "disfg", "accent")

_NAMESPACE = {"light": "sv_light", "dark": "sv_dark"}

_THEME_NAME = {"sun-valley-light": "light", "sun-valley-dark": "dark"}

# sv_ttk が読めなかった場合の控え。**値の正しさはテストが sv_ttk 実値と突合する**
# （tests/test_theme.py::test_palette_comes_from_sv_ttk_not_fallback）。
_FALLBACK: dict[str, dict[str, str]] = {
    "light": {
        "fg": "#1c1c1c", "bg": "#fafafa", "selfg": "#ffffff",
        "selbg": "#2f60d8", "disfg": "#a0a0a0", "accent": "#005fb8",
    },
    "dark": {
        "fg": "#fafafa", "bg": "#1c1c1c", "selfg": "#ffffff",
        "selbg": "#2f60d8", "disfg": "#595959", "accent": "#57c8ff",
    },
}

# アクティブ背景を作るときに前景側へ寄せる割合。
_ACTIVE_MIX = 0.12

# 補助テキスト（コピーライト・ヒント・注記・地図のステータス）を地の色へ寄せる割合。
# 「補助情報は落として見せる」というテーマの設計言語に従いつつ、実測コントラストが
# 従来の固定色 `gray`（#808080＝ライト 3.78:1 / ダーク 4.32:1）を下回らない値を選ぶ
# （0.40 でライト 4.41:1 / ダーク 6.60:1）。sv_ttk の disabled（2.4〜2.5:1）とは
# はっきり差をつける＝補助テキストは「無効」ではなく「読めるが主役でない」。
_MUTED_MIX = 0.40


# ------------------------------------------------------------
# フォント（配色と同じく「出所は sv_ttk」）
# ------------------------------------------------------------
# sv_ttk が `sv.tcl` で作る名前付きフォント。Tk の名前付きフォントなので、
# ウィジェットの `font=` にはこの**名前をそのまま**渡せる（サイズを数値で持たない
# ＝二重管理にならず、テーマ側が変えれば全窓が追従する）。
#
# なぜ「アプリ側で ("Arial", 9) と書かない」か（2.5b2 / I-023）：
# sv_ttk はテーマ定義の中で Entry/Combobox/Treeview に `SunValleyBodyFont`
# （Segoe UI Variable Text / -14px）を当てるが、Label/Button には当てない
# （＝`TkDefaultFont` / Segoe UI 9pt のまま）。つまり**何も指定しない窓でも
# ラベルと入力欄で字面が揃わない**。さらにランチャー・バッチだけが
# `("Arial", 8)` `("Arial", 9)` をベタ書きしていたため、窓ごとに書体もサイズも
# バラバラだった（実機フィードバック 2026-07-26）。出所を1つにして解消する。
_SV_FONTS = {
    "body":  "SunValleyBodyFont",
    "small": "SunValleyCaptionFont",
    "bold":  "SunValleyBodyStrongFont",
}

# sv_ttk が読み込まれていない場合の控え（テーマ未適用のテスト等）。
_FONT_FALLBACK = {
    "body":  "TkDefaultFont",
    "small": "TkSmallCaptionFont",
    "bold":  "TkHeadingFont",
}


def ui_font(widget: tk.Misc, kind: str = "body") -> str:
    """ウィジェットの `font=` へ渡す**名前付きフォント名**を返す。

    Args:
        kind: ``body``（本文・入力欄）／``small``（密な表・注記）／``bold``（強調）
    """
    name = _SV_FONTS[kind]
    try:
        if name in widget.tk.call("font", "names"):
            return name
    except tk.TclError:
        pass
    return _FONT_FALLBACK[kind]


# 既定として書き換える Tk の名前付きフォント。
# `TkDefaultFont` ＝ ttk の Label/Button など、`TkTextFont` ＝ Entry/Combobox/
# Text/Listbox の既定。**メニュー（TkMenuFont）は触らない**＝OS のメニュー
# フォントに合わせるのが Windows の作法。
_DEFAULT_FONT_NAMES = ("TkDefaultFont", "TkTextFont")


# ------------------------------------------------------------
# DPI
# ------------------------------------------------------------
# sv_ttk のフォントは**ピクセル指定**（`-14` 等＝負値はピクセル）で、96dpi を前提に
# 書かれている。ピクセル指定は Tk の `tk scaling` の影響を受けない＝**DPI が変わって
# も字の大きさが 1px も変わらない**。一方 Windows（Per-Monitor DPI Aware）は窓の枠
# だけを拡大するので、「窓は大きくなったのに字は小さいまま」になる
# （2026-07-26 のユーザー報告）。
#
# そこで **96dpi 基準のピクセル数を保持し、実際の DPI 倍して当てる**。
# 「起動時の DPI」でなく「今その窓が載っているモニタの DPI」を見るので、
# 高 DPI 機での起動と、モニタ間の移動の**両方**に効く。
_DPI_BASE = 96

# 96dpi 基準のピクセル数（初回に sv_ttk の定義から読み、以後はここを基準に倍率を
# 掛ける）。**毎回 config() を読み直すと、拡大した値をさらに拡大してしまう**。
_base_px: "dict[str, int]" = {}


def window_dpi(widget: tk.Misc) -> int:
    """`widget` が載っているモニタの DPI（取れなければ 96）。

    `GetDpiForWindow` は Windows 10 1607+ で**モニタごと**の値を返す（Tk 8.6 の
    `winfo_fpixels("1i")` は起動時のスクリーン値で固定なので、モニタ間の移動を
    追えない）。取れない環境では順に劣化させる。
    """
    import sys

    if sys.platform == "win32":
        import ctypes
        try:
            hwnd = widget.winfo_toplevel().winfo_id()
            dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
            if dpi > 0:
                return dpi
        except Exception:
            pass
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())
            if dpi > 0:
                return dpi
        except Exception:
            pass
    try:
        return int(round(widget.winfo_fpixels("1i")))
    except tk.TclError:
        return _DPI_BASE


def _scaled_px(base_px: int, dpi: int) -> int:
    """96dpi 基準のピクセル数を、実 DPI でのピクセル数へ。

    Tk のフォントサイズは負値＝ピクセル。0 を返さないよう最小 1px で止める。
    """
    return -max(1, int(round(abs(base_px) * dpi / _DPI_BASE)))


def apply_fonts(root: tk.Misc, *, dpi: "int | None" = None) -> None:
    """全窓の既定フォントを sv_ttk の本文フォントに揃え、**DPI に合わせる**。

    やり方＝**名前付きフォントそのものを書き換える**。名前付きフォントは参照して
    いるウィジェット全部に即時反映され、**あとから作られるウィジェットにも効く**。

    ⚠️ **ttk スタイルへの `font` 設定では足りない**（2.5b2 で実測）。ttk の
    Entry/Combobox/Spinbox は `-font` を**ウィジェットオプション**として持ち、
    その既定値 `TkTextFont` がスタイル設定より優先される。実際、`style.configure`
    だけ入れた版では **あとから「条件を追加」で生やした列だけ字が小さい**（既存の
    列は sv_ttk が `<<ThemeChanged>>` 時にウィジェット単位で本文フォントを当てて
    いた）という形で表に出た。ガード＝tests/test_theme.py の
    test_dynamically_created_widgets_get_the_same_font。

    ロケール差も同時に消える：ja 環境の `TkDefaultFont` は Yu Gothic UI 9pt で、
    sv_ttk が入力欄に当てる Segoe UI Variable Text 10pt とそもそも別物だった。

    Args:
        dpi: 使う DPI。省略時は `root` が載っているモニタから取る（テストが
            高 DPI を再現できるよう注入可能にしてある）。
    """
    from tkinter import font as tkfont

    if dpi is None:
        dpi = window_dpi(root)

    names = list(_SV_FONTS.values())
    # ① sv_ttk のフォント自体を DPI に合わせる（Treeview・LabelFrame の見出し・
    #    入力欄はテーマがこれらを直接参照しているので、ここを直せば全部追従する）。
    for name in names:
        try:
            f = tkfont.nametofont(name, root=root)
        except tk.TclError:
            continue          # テーマ未適用（素の Tk）＝触らない
        if name not in _base_px:
            size = (f.config() or {}).get("size")
            if not isinstance(size, int) or size >= 0:
                continue      # pt 指定なら Tk の scaling に任せる（ここでは触らない）
            _base_px[name] = size
        f.configure(size=_scaled_px(_base_px[name], dpi))

    # ② Tk の既定フォントを本文フォントに合わせる。
    #    `TkDefaultFont` ＝ ttk の Label/Button など、`TkTextFont` ＝ Entry/
    #    Combobox/Text/Listbox の既定。**メニュー（TkMenuFont）は触らない**
    #    ＝OS のメニューフォントに合わせるのが Windows の作法。
    try:
        spec = tkfont.nametofont(ui_font(root), root=root).config() or {}
    except tk.TclError:
        return
    # サイズは `config()` から取る（`actual()` は px 指定を pt に丸めて返すので、
    # 転記すると 1pt ぶん太る）。書体とサイズだけを写し、下線などは触らない。
    family, size = spec.get("family"), spec.get("size")
    if not isinstance(family, str) or not isinstance(size, int):
        return
    for name in _DEFAULT_FONT_NAMES:
        try:
            tkfont.nametofont(name, root=root).configure(family=family, size=size)
        except tk.TclError:
            pass   # その環境に無い名前付きフォントは飛ばす

    # ③ 等幅（README ビューア）も同じ DPI で。pt 指定なので `tk scaling` を
    #    今の DPI に合わせておけば追従する。
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except tk.TclError:
        pass

    # ④ 表の行高はフォントの行送りから決まる＝フォントが変わったら貼り直す。
    table_style(root)


# <Configure> は移動・リサイズのたびに飛ぶので、まとめて 1 回にする間隔。
_DPI_DEBOUNCE_MS = 250


def watch_dpi(root: tk.Misc, on_change: "Callable[[int], None] | None" = None) -> None:
    """モニタ間の移動などで DPI が変わったらフォントを貼り直す。

    Tk 8.6 は `WM_DPICHANGED` を受けて**窓の大きさは**追従させるが、フォントは
    何もしない（`tk scaling` は起動時のスクリーン値で固定）。結果「窓だけ大きく
    なって字は小さいまま」になる。Tk 側に DPI 変更の通知イベントが無いので、
    **`<Configure>`（移動・リサイズ）を契機に実 DPI を見に行く**。

    `bind_all` で全ウィンドウ分をまとめて拾う＝窓を1つ足すたびに配線を思い出す
    必要がない（[[feedback-promote-recurring-checks]]：思い出す規則にしない）。

    Args:
        on_change: DPI が変わってフォントを貼り直した**あと**に呼ばれる。
            窓の寸法を測り直す（`views.window_fit.refit_all`）ために使う
            ＝字が大きくなれば必要な幅も高さも増えるので、追従しないと見切れる。
    """
    state = {"dpi": window_dpi(root), "after": None}

    def _check() -> None:
        state["after"] = None
        try:
            dpi = window_dpi(root)
        except tk.TclError:
            return            # 破棄済み
        if dpi == state["dpi"]:
            return
        state["dpi"] = dpi
        apply_fonts(root, dpi=dpi)
        if on_change is not None:
            on_change(dpi)

    def _on_configure(event: "tk.Event") -> None:
        # トップレベル自身の Configure だけ見る（子ウィジェットの分は無視）。
        widget = event.widget
        if not isinstance(widget, (tk.Tk, tk.Toplevel)):
            return
        if state["after"] is not None:
            try:
                root.after_cancel(state["after"])
            except tk.TclError:
                pass
        state["after"] = root.after(_DPI_DEBOUNCE_MS, _check)

    root.bind_all("<Configure>", _on_configure, add="+")


# 表（Treeview）の余白。sv_ttk の既定は行高 `linespace + 3`＝行が詰まり、文字は
# 枠にも列境界にも張り付く（2026-07-26 の実機フィードバック）。
#   _TABLE_PAD      … 表の外周（枠と中身の間）
#   _TABLE_CELL_PAD … **セルの中**（列境界と文字の間）。⚠️ ここが本命で、外周だけ
#                     広げても「右詰めの数字が隣の列にくっついている」のは直らない
#                     （b2 の最初の版で実際に「余白が効いていない」と再指摘された）。
#                     ttk のセルは `Cell` レイアウトの `Treedata.padding` 経由でしか
#                     内側の余白を持てない（列単位の padding オプションは無い）。
#   _TABLE_HEADPAD  … 見出しの厚み
_TABLE_PAD       = (4, 4)
_TABLE_CELL_PAD  = (10, 0)
_TABLE_HEADPAD   = (10, 6)
_TABLE_ROW_EXTRA = 10


def table_style(widget: tk.Misc, name: str = "App.Treeview") -> str:
    """余白を持たせた Treeview スタイルを用意し、その名前を返す。

    行高はフォントの行送りから決める（DPI・テーマのフォント差に追従させる）。
    スタイル名は `*.Treeview` である必要がある（ttk がレイアウトを名前で辿る）。
    """
    from tkinter import font as tkfont

    style = ttk.Style(master=widget)
    try:
        linespace = tkfont.Font(root=widget, font=ui_font(widget)).metrics("linespace")
    except tk.TclError:
        linespace = 18
    # apply_fonts と同じ理由で全テーマの辞書へ入れる（テーマを切り替えると
    # 現テーマにしか無い設定は消える）。
    settings: dict[str, Any] = {
        name: {"configure": {"padding": _TABLE_PAD,
                             "rowheight": linespace + _TABLE_ROW_EXTRA}},
        f"{name}.Cell":    {"configure": {"padding": _TABLE_CELL_PAD}},
        f"{name}.Heading": {"configure": {"padding": _TABLE_HEADPAD}},
    }
    for theme_name in style.theme_names():
        try:
            style.theme_settings(theme_name, settings)
        except tk.TclError:
            pass
    return name


def current_theme(widget: tk.Misc) -> str:
    """現在の sv_ttk テーマ名（"light" / "dark"）を返す。"""
    try:
        name = ttk.Style(master=widget).theme_use()
    except tk.TclError:
        return "light"
    return _THEME_NAME.get(name, "light")


def read_sv_ttk_colors(widget: tk.Misc, theme: str) -> "dict[str, str] | None":
    """sv_ttk の色配列を読む。1つでも読めなければ None（＝出所を使えない）。

    控え（`_FALLBACK`）は実値と同じ値を持つので、**戻り値を見ても控えに落ちた
    ことは分からない**。落ちたこと自体をテストから観測できるよう、読み取りを
    独立した関数に分けてある（tests/test_theme.py がこの関数を直接呼ぶ）。
    """
    ns = _NAMESPACE[theme]
    colors: dict[str, str] = {}
    for key in _KEYS:
        try:
            value = str(widget.tk.call("set", f"::ttk::theme::{ns}::colors(-{key})"))
        except tk.TclError:
            return None
        if not value:
            return None
        colors[key] = value
    return colors


def palette(widget: tk.Misc) -> dict[str, str]:
    """現在テーマの色を返す（sv_ttk の色配列が出所・読めなければ控え）。"""
    theme = current_theme(widget)
    return read_sv_ttk_colors(widget, theme) or dict(_FALLBACK[theme])


def _mix(color_a: str, color_b: str, ratio: float) -> str:
    """`color_a` を `ratio` の割合だけ `color_b` へ寄せた色（#rrggbb）。"""
    a = (int(color_a[1:3], 16), int(color_a[3:5], 16), int(color_a[5:7], 16))
    b = (int(color_b[1:3], 16), int(color_b[3:5], 16), int(color_b[5:7], 16))
    mixed = (round(x + (y - x) * ratio) for x, y in zip(a, b))
    return "#" + "".join(f"{v:02x}" for v in mixed)


def menu_options(widget: tk.Misc) -> dict[str, str]:
    """`tk.Menu.configure()` へ渡す配色オプション一式。

    アクティブ時も前景色を変えない（理由はモジュール docstring）。
    """
    colors = palette(widget)
    fg, bg = colors["fg"], colors["bg"]
    return {
        "background"        : bg,
        "foreground"        : fg,
        "activebackground"  : _mix(bg, fg, _ACTIVE_MIX),
        "activeforeground"  : fg,
        "disabledforeground": colors["disfg"],
        "selectcolor"       : fg,   # ラジオ/チェックの「✓」（B-004）
    }


def tooltip_options(widget: tk.Misc) -> dict[str, str]:
    """ツールチップ（`tk.Label`）へ渡す配色オプション。

    B-008 の掃き出しで見つかった同型の欠陥（2026-07-22）：背景を
    `bg="SystemButtonFace"` で固定し前景だけテーマに追従させていたため、
    ダークでは `#fafafa` の文字が `#f0f0f0` の背景に載り**コントラスト 1.06:1
    ＝完全に読めない**状態だった。前景・背景は必ず同じ出所から取る。

    地の色より少し浮かせて「窓の上に乗った面」に見せる（メニューのアクティブ
    背景と同じ濃淡の作り方）。
    """
    colors = palette(widget)
    return {
        "background": _mix(colors["bg"], colors["fg"], _ACTIVE_MIX),
        "foreground": colors["fg"],
    }


def muted_foreground(widget: tk.Misc) -> str:
    """補助テキスト（コピーライト・ヒント・注記・地図のステータス）の前景色。

    I-009（2.5a1）：これらは `foreground="gray"`（#808080）で**固定**されており、
    テーマ色一本化の方針から外れていた（配色の出所が theme.py でない＝B-008 と
    同じクラス）。ここで地の色を出所にし、テーマごとに前景を作る。

    **WCAG AA（4.5:1）を機械適用しない**のは意図的：sv_ttk 自身の disabled 色は
    2.4〜2.5:1 で、「補助情報は落として見せる」がテーマの設計言語だから。基準に
    合わせて全部を主文と同じ強さにすると設計意図と衝突する。代わりに
    **従来の固定色より暗く（読みにくく）しない**ことをテストで固定している。
    """
    colors = palette(widget)
    return _mix(colors["fg"], colors["bg"], _MUTED_MIX)


def apply_menu_theme(menus: "list[tk.Menu]", widget: tk.Misc) -> None:
    """与えられた tk.Menu 群へ現在テーマの配色を適用する。"""
    options = menu_options(widget)
    for menu in menus:
        try:
            menu.configure(**options)
        except tk.TclError:
            pass   # 破棄済みのメニューは無視する
