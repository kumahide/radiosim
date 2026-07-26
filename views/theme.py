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
from typing import Any

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


def apply_fonts(root: tk.Misc) -> None:
    """全窓の既定フォントを sv_ttk の本文フォントに揃える（アプリ起動時に1回）。

    やり方＝**Tk の既定フォント（名前付きフォント）そのものを書き換える**。
    名前付きフォントは参照しているウィジェット全部に即時反映され、**あとから
    作られるウィジェットにも効く**。

    ⚠️ **ttk スタイルへの `font` 設定では足りない**（2.5b2 で実測）。ttk の
    Entry/Combobox/Spinbox は `-font` を**ウィジェットオプション**として持ち、
    その既定値 `TkTextFont` がスタイル設定より優先される。実際、`style.configure`
    だけ入れた版では **あとから「条件を追加」で生やした列だけ字が小さい**（既存の
    列は sv_ttk が `<<ThemeChanged>>` 時にウィジェット単位で本文フォントを当てて
    いた）という形で表に出た。ガード＝tests/test_theme.py の
    test_dynamically_created_widgets_get_the_same_font。

    ロケール差も同時に消える：ja 環境の `TkDefaultFont` は Yu Gothic UI 9pt で、
    sv_ttk が入力欄に当てる Segoe UI Variable Text 10pt とそもそも別物だった。
    """
    from tkinter import font as tkfont

    try:
        source = tkfont.nametofont(ui_font(root), root=root)
        spec = source.config() or {}
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
