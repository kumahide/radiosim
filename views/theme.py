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
#
# ⛔ **画面で太字は使わない**（2026-08-01 ユーザー決定＝「かえって読みにくい」）。
#    強調は配置・余白・区切り線で作る。したがって `ui_font` に "bold" は無い。
#    ⚠️ レポート HTML の `font-weight:bold` は**別の設計言語**（印刷物・小さな
#    文字を紙で読ませる）なので、この決定は持ち込まない。
#    ⚠️ ここに太字を戻したくなったら、先に ISSUES.md B-026 を読むこと＝Segoe UI
#    Variable 系は日本語グリフを持たず、**漢字のフォントリンク先を決めるのは
#    family ではなく weight** なので、太字にした瞬間に漢字だけ Malgun Gothic
#    （韓国語）へ落ちる（2026-07-31 実測・`experiments/font_fallback_probe.py`）。
_SV_FONTS = {
    "body":  "SunValleyBodyFont",
    "small": "SunValleyCaptionFont",
    # ⚠️ 画面では使わないが**テーブルには残す**＝sv_ttk 自身がテーマ定義の中で
    # 参照しているので、DPI・書体の追従ループ（apply_fonts ①）が舐める必要がある。
    "_theme_strong": "SunValleyBodyStrongFont",
}

# sv_ttk が読み込まれていない場合の控え（テーマ未適用のテスト等）。
_FONT_FALLBACK = {
    "body":  "TkDefaultFont",
    "small": "TkSmallCaptionFont",
}

def ui_font(widget: tk.Misc, kind: str = "body") -> str:
    """ウィジェットの `font=` へ渡す**名前付きフォント名**を返す。

    Args:
        kind: ``body``（本文・入力欄）／``small``（密な表・注記）

    ⛔ ``bold`` は**無い**（画面で太字を使わない＝上記の決定）。呼ぶと KeyError に
       なるのは意図的で、「強調したい」が出てきたら配置か余白で解くこと。
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
# Text/Listbox の既定。**メニュー（TkMenuFont）は入れない＝入れても効かない**。
#
# 🔴 **メニューの字は 2 つの別々の仕組みで決まる**（2026-08-08 実測・B-051）：
#   - **帯**（ファイル / 設定 / ヘルプ）＝Windows が描く HMENU で Tk の管轄外。
#     追従は OS にやらせる＝[main.py](../main.py) の `_set_dpi_awareness` が
#     Per-Monitor **v2** を立て、非クライアント領域を Windows 側にスケールさせる。
#   - **ドロップダウン**（帯を開いた中身・右クリックメニュー）＝Tk が描くが、
#     **`TkMenuFont` にも `tk scaling` にも従わない**。実測＝scaling を 1.33→2.00 に
#     しても、既存メニューも新規メニューも要求高は 97px のまま。**唯一効くのは
#     ウィジェットへの直接指定 `tk.Menu(font=…)`**（40pt 指定で 97→377px）。
# ⇒ だからドロップダウンだけは `menu_font()` を `menu_options()` で明示的に当て、
#    DPI が変わったら `apply_fonts` が既存のメニューにも貼り直す。
# ⚠️ I-054 の対策（PMv2）は帯だけを直したので、**帯が大きく中身が小さい**という
#    ちぐはぐが残っていた（2026-08-08 にユーザーが 150% のスクショで報告）。
_DEFAULT_FONT_NAMES = ("TkDefaultFont", "TkTextFont")

# ドロップダウンの基準（96dpi でのピクセル）。初回に `TkMenuFont` から読む＝
# **OS のメニューフォントに合わせる**（帯は OS が同じ元から描くので、揃う）。
_menu_base: "dict[str, int | str]" = {}


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

# sv_ttk が定義した元の書体（初回に読む）。日本語ロケールで差し替えたあとに
# 英語へ戻せるよう、**書き換える前の値を必ず持っておく**。
_base_family: "dict[str, str]" = {}

# 最後に `apply_fonts` が使った DPI＝**いまアプリの字が従っている値**。
# 🔴 **なぜ「アプリに 1 つ」なのか**（B-065）：Tk の名前付きフォントは
# **インタプリタに 1 組**しかなく、`TkDefaultFont` を窓ごとに別の大きさにはできない。
# ⇒ 複数モニタで DPI が違っても、**字の大きさはアプリ全体で 1 つ**にしかできない。
# 追従は「**最後に表示環境が変わった窓**の DPI へ全体を合わせる」形になる
# （2026-08-12 ユーザー決定＝動かした窓が正しくなるほうを取る）。
_applied_dpi: "dict[str, int]" = {}


# ------------------------------------------------------------
# 日本語の書体（B-026）
# ------------------------------------------------------------
# sv_ttk の本文書体（Segoe UI Variable Text）は**日本語グリフを持たない**。
# Tk 8.6 は不足分を Windows のフォントリンクへ丸投げするので、日本語だけが
# 別書体で描かれる。しかも**落ちる先を決めるのは family ではなく weight** で、
# 本文が Segoe 系である限り**強調にした瞬間に漢字が Malgun Gothic（韓国語）へ
# 落ちる**（2026-07-31 の実測。表は ISSUES.md B-026）。
#
# ⇒ 日本語では**最初から日本語を持つ書体を本文にする**。これらは normal / bold・
# 漢字 / ラテンのどの組み合わせでも同じ書体を保つので、強調も本文も揃う。
# （I-023 のフォント統一で全窓の日本語が Yu Gothic UI → ＭＳ Ｐゴシックへ落ちて
# いた副作用も、これで同時に解ける。）
_JA_FAMILIES = ("Yu Gothic UI", "Meiryo UI", "MS UI Gothic", "ＭＳ Ｐゴシック")


def _japanese_family(root: tk.Misc) -> "str | None":
    """この環境で使える日本語書体（無ければ None＝元の書体のまま）。"""
    try:
        from tkinter import font as tkfont
        installed = set(tkfont.families(root))
    except tk.TclError:
        return None
    for family in _JA_FAMILIES:
        if family in installed:
            return family
    return None


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


def applied_dpi(widget: "tk.Misc | None" = None) -> int:
    """**いまアプリの字が従っている DPI**（まだ当てていなければモニタの実 DPI）。

    `window_dpi()` との違いは「モニタが何を言っているか」ではなく「**アプリが何に
    合わせて描いているか**」を返すこと。⚠️ **寸法の見積りはこちらを見る**（B-084 の
    装飾余白）＝字と枠が別の DPI を基準にすると、テストが `apply_fonts(dpi=144)` で
    150% を再現しても余白だけ 100% のまま計算され、**再現したはずの条件が半分しか
    再現しない**（＝ゲートが本番の壊れ方を見ない）。
    """
    got = _applied_dpi.get("value")
    if got:
        return int(got)
    if widget is None:
        return _DPI_BASE
    return window_dpi(widget)


def _scaled_px(base_px: int, dpi: int) -> int:
    """96dpi 基準のピクセル数を、実 DPI でのピクセル数へ。

    Tk のフォントサイズは負値＝ピクセル。0 を返さないよう最小 1px で止める。
    """
    return -max(1, int(round(abs(base_px) * dpi / _DPI_BASE)))


def _walk_menus(root: tk.Misc) -> "list[tk.Menu]":
    """`root` 以下に現存する `tk.Menu` を全部集める（メニューバーも子ウィジェット）。"""
    found: "list[tk.Menu]" = []

    def walk(widget: tk.Misc) -> None:
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return                    # 破棄途中のウィジェット
        for child in children:
            if isinstance(child, tk.Menu):
                found.append(child)
            walk(child)

    walk(root)
    return found


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

    ⚠️ **書体は UI 言語で変わる**（B-026）：日本語では sv_ttk の Segoe UI Variable
    Text を**日本語を持つ書体へ差し替える**（`_JA_FAMILIES`）。Segoe 系のままだと
    漢字が Windows のフォントリンクで別書体に落ち、強調では Malgun Gothic
    （韓国語）になる。⇒ **言語を変えたら呼び直すこと**（現状は言語切替が再起動を
    伴うので起動時の 1 回で足りる）。**字幅が変わる**ので、変更時は必ず
    tests/test_window_fit.py（実機 FHD × 125%/150%）を回すこと。

    Args:
        dpi: 使う DPI。省略時は `root` が載っているモニタから取る（テストが
            高 DPI を再現できるよう注入可能にしてある）。
    """
    from tkinter import font as tkfont

    if dpi is None:
        dpi = window_dpi(root)
    _applied_dpi["value"] = dpi        # いま字が従っている値（B-065）

    names = list(_SV_FONTS.values())
    # 日本語のときは本文書体そのものを日本語を持つ書体へ差し替える（B-026）。
    # ⚠️ **ここでしか差し替えない**＝窓やウィジェットごとに書体を選ぶと、
    # I-023 で潰したはずの「窓ごとにバラバラ」が別の形で戻る。
    from core import i18n
    ja_family = _japanese_family(root) if i18n.current_lang() == "ja" else None

    # ① sv_ttk のフォント自体を DPI に合わせる（Treeview・LabelFrame の見出し・
    #    入力欄はテーマがこれらを直接参照しているので、ここを直せば全部追従する）。
    for name in names:
        try:
            f = tkfont.nametofont(name, root=root)
        except tk.TclError:
            continue          # テーマ未適用（素の Tk）＝触らない
        conf = f.config() or {}
        if name not in _base_px:
            size = conf.get("size")
            if not isinstance(size, int) or size >= 0:
                continue      # pt 指定なら Tk の scaling に任せる（ここでは触らない）
            _base_px[name] = size
        if name not in _base_family:
            family = conf.get("family")
            if isinstance(family, str):
                _base_family[name] = family
        # 英語へ戻したときに sv_ttk の書体へ戻す＝差し替えを一方通行にしない。
        want = ja_family or _base_family.get(name)
        if want:
            f.configure(family=want)
        f.configure(size=_scaled_px(_base_px[name], dpi))

    # ② Tk の既定フォントを本文フォントに合わせる。
    #    `TkDefaultFont` ＝ ttk の Label/Button など、`TkTextFont` ＝ Entry/
    #    Combobox/Text/Listbox の既定。**メニュー（TkMenuFont）は触らない**
    #    ＝触っても効かない（理由は `_DEFAULT_FONT_NAMES` の注記・I-054）。
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

    # ②' ドロップダウンは名前付きフォントを見ないので、**現存するメニューへ直に**
    #     貼り直す（B-051）。ウィジェット木を歩くのは `bind_all` と同じ理由＝
    #     メニューを 1 つ足すたびに登録を思い出す運用にしない。
    font = menu_font(root, dpi=dpi)
    _menu_base["dpi"] = dpi          # 以後に作られるメニューも同じ基準で作る
    for menu in _walk_menus(root):
        try:
            menu.configure(font=font)
        except tk.TclError:
            pass   # 破棄済み／再入で消えたメニューは無視する

    # ③ 等幅（README ビューア）も同じ DPI で。pt 指定なので `tk scaling` を
    #    今の DPI に合わせておけば追従する。
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except tk.TclError:
        pass

    # ④ 表の行高はフォントの行送りから決まる＝フォントが変わったら貼り直す。
    table_style(root)


# <Configure> は移動・リサイズのたびに飛ぶので、まとめて 1 回にする間隔。
_DISPLAY_DEBOUNCE_MS = 250

# 🔴 **追従を一発勝負にしないための「落ち着いた頃の確かめ」**（2026-08-23・B-118）。
# 表示スケールの変更は**デスクトップ全体の作り直し**で、上のデバウンス（250ms の
# 静けさ）の後にも OS 側の測り直しが届く。⇒ 我々が選んだ大きさが**上書きされる**
# ことがあり、そのとき DPI はもう変わらないので `_check` は二度と発火しない
# ＝**アプリを再起動するまで直らない**（実機で再現・再起動すれば正しい大きさになる）。
_DISPLAY_SETTLE_MS = 800


def _pointer_is_down() -> bool:
    """左ボタンが押されている＝**利用者がいま窓を掴んでいる**（Windows のみ）。

    🔴 **掴んでいる最中に窓を測り直してはいけない**（2026-08-23・B-119）＝
    タイトルバーで窓を別 DPI のモニタへドラッグしていく途中は、
    ①手が一瞬止まるたびにデバウンスが明けて ②境界をまたいでいる間は
    「載っているモニタ」も DPI も振れる ⇒ **握っている窓が何度も測り直され、
    引きずるほど縮んでいく**（実機報告）。⇒ **手を離してから測る。**

    ⚠️ **取れなければ False**（＝掴んでいない扱い）。ここを True 側へ倒すと、
    Win32 が使えない環境で**追従そのものが永久に止まる**——追従しないことの害
    （見切れ・画面外）の方が、ドラッグ中に 1 度測り直す害より大きい。

    ⚠️ Tk のイベントでは分からない＝タイトルバーのドラッグは**ウィンドウマネージャ
    の中で完結**しており、`<Button-1>` も `<B1-Motion>` も飛んでこない
    （`<Configure>` だけが結果として届く）。だから OS に直接聞く。
    """
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
    except Exception:
        return False


def watch_display(
    root: tk.Misc, on_change: "Callable[[int, bool], None] | None" = None
) -> None:
    """**表示環境が変わったら**フォントを貼り直し、窓を測り直させる。

    見るのは 3 つ＝**DPI**・**画面サイズ**・**窓が載っているモニタ**。どれも窓の
    寸法の入力なので、1 つでも見落とすとその変化が素通りする。⚠️ **3 つ目は
    2026-08-15 に足した**（B-088）＝`screen_size()` は*プライマリ*の値なので、
    **同じ DPI で解像度だけ違うモニタ**へ窓を動かしても 1 つ目も 2 つ目も動かない。

    - **DPI**（モニタ間の移動）: Tk 8.6 は `WM_DPICHANGED` を受けて**窓の
      大きさは**追従させるが、フォントは何もしない（`tk scaling` は起動時の
      スクリーン値で固定）。結果「窓だけ大きくなって字は小さいまま」になる
      （B-015）。Tk 側に DPI 変更の通知イベントが無いので、**`<Configure>`
      （移動・リサイズ）を契機に実 DPI を見に行く**。
    - **画面サイズ**（解像度変更・VDI の動的解像度・リモート再接続）: `fit_to_content`
      は画面の高さを上限に使っているので、**画面が変われば選ぶべき寸法も変わる**。
      ⚠️ **旧 `watch_dpi` はここを見ておらず、`<Configure>` は来ているのに
      「DPI が同じなら即 return」で捨てていた**（B-022）。害は両方向に出た＝狭く
      なれば窓がデスクトップの外へ出たまま、広くなればクランプされた小さいまま
      二度と戻らない（`fit_to_content` が二度と呼ばれないため、再接続しても
      直らずアプリの再起動しか回復手段が無かった）。

    ⚠️ **フォントの貼り直しは DPI が変わったときだけ**（解像度だけ変わった時に
    貼り直すのは無駄な上に、全窓のフォントを触るので副作用の面が広い）。
    測り直しは**どちらの契機でも**呼ぶ。

    `bind_all` で全ウィンドウ分をまとめて拾う＝窓を1つ足すたびに配線を思い出す
    必要がない（[[feedback-promote-recurring-checks]]：思い出す規則にしない）。

    ⚠️ **測るのは窓ごと**（B-065）。旧実装は `<Configure>` を全トップレベルから
    拾いながら、DPI を**常に `root` から**測っていた＝ランチャーを 100% 側に置いた
    まま子窓だけを 150% 側へドラッグしても値が動かず、何も起きなかった。**契機の
    網は正しく、測る先だけが 1 つに固定されていた**（＝拾えているので気づけない）。
    🔴 **ただし追従できるのは「大きさ 1 つ」まで**＝Tk の名前付きフォントは
    インタプリタに 1 組なので（`_applied_dpi` の註）、**動かした窓に全体を合わせる**。
    別モニタに残した窓の字も一緒に変わるのは、この作りでは避けられない副作用。

    Args:
        on_change: 表示環境が変わった**あと**に呼ばれる。引数＝`(その時点の DPI,
            DPI が変わったか)`。窓の寸法を測り直す（`views.window_fit.refit_all`）
            ために使う＝字が大きくなれば必要な幅も高さも増えるので、追従しないと
            見切れる。**2 つ目の引数で 2 つの契機を呼び分ける**＝DPI が変わった
            ときだけ縮む方向にも測り直す（I-053。上の「フォントの貼り直しは DPI
            のときだけ」と同じ分岐で、判定を呼ばれた側に再現させない）。
    """
    from views import window_fit                    # 遅延 import（循環回避）

    # 窓ごとの前回値。⚠️ **キーはウィジェットでなく Tk のパス名**＝ここは窓より
    # 長生きする状態なので、ウィジェットを掴むと閉じた窓が解放されない（B-050 で
    # 実際に踏んだ形）。消えた窓の分は毎回の走査で落とす。
    seen: "dict[str, tuple]" = {}
    state: "dict[str, Any]" = {"after": None, "settle": None}

    def _windows() -> "list[tk.Tk | tk.Toplevel]":
        # ⚠️ `winfo_toplevel()` を通すのは**型を正しく絞るため**＝`root` は `Misc`
        # として受けているが、位置や矩形を読む関数はトップレベルしか受けない
        # （`geometry()` を持つのはそちらだけ）。`root` が Tk ならそれ自身が返る。
        return [root.winfo_toplevel(), *window_fit.toplevels(root)]

    def _measure(win: "tk.Tk | tk.Toplevel") -> "tuple | None":
        try:
            return (window_dpi(win), window_fit.screen_size(win),
                    window_fit.host_monitor(win, window_fit.window_rect(win)))
        except tk.TclError:
            return None                             # 破棄途中の窓
    for win in _windows():
        got = _measure(win)
        if got is not None:
            seen[str(win)] = got

    def _check() -> None:
        state["after"] = None
        if _pointer_is_down():
            # 🔴 **掴んでいる間は測らない**（B-119）＝ここで `seen` を更新すると
            # 変化を**消費**してしまい、手を離した後に測り直す機会も消える。
            # ⇒ 何も見ずに待ち直す（次のデバウンスでまた来る）。
            state["after"] = root.after(_DISPLAY_DEBOUNCE_MS, _check)
            return
        moved_dpi: "int | None" = None
        screen_changed = False
        alive: "set[str]" = set()
        for win in _windows():
            got = _measure(win)
            if got is None:
                continue
            name = str(win)
            alive.add(name)
            dpi, screen, area = got
            prev = seen.get(name)
            seen[name] = got
            if prev is None:
                # 初めて見る窓。**いま字が従っている DPI と違えば契機**＝別 DPI の
                # モニタ側で開かれた窓が、開いた瞬間だけ取り残されるのを防ぐ。
                if dpi != _applied_dpi.get("value", dpi):
                    moved_dpi = dpi
                continue
            if dpi != prev[0]:
                moved_dpi = dpi
            if screen != prev[1] or area != prev[2]:
                # 🔴 **載っているモニタが変わった**のも契機（2026-08-15・B-088）。
                # `screen_size()` はプライマリの値なので、**同じ DPI で解像度だけ
                # 違うモニタ**へ窓をドラッグしても DPI も画面サイズも動かない
                # ＝窓は広いほうの画面向けの大きさのまま残り、狭い画面で下端が
                # はみ出す（B-087 の直しが**実運用では発火しない**状態だった）。
                screen_changed = True
        for name in set(seen) - alive:
            del seen[name]                          # 閉じた窓を溜めない
        if moved_dpi is None and not screen_changed:
            return
        if moved_dpi is not None:
            apply_fonts(root, dpi=moved_dpi)
        if on_change is not None:
            args = (moved_dpi if moved_dpi is not None else window_dpi(root),
                    moved_dpi is not None)
            on_change(*args)
            _arm_settle(args)

    def _overridden(win: "tk.Tk | tk.Toplevel") -> bool:
        """**選んだ大きさが残っていない**か（＝誰かが上書きした）。

        🔴 **両方向を見る**（2026-08-23・独立レビュー 35 巡目で直した）＝最初は
        「小さくされたときだけ」にしていたが、**害の出る向きは契機によって逆になる**:

        - **DPI が上がった**（スケール 150%）: 害は**小さいまま**＝中身が入らず
          スクロールバー越しになる。
        - **画面が狭くなった**（解像度変更・リモート再接続）: `fit_to_content` は
          窓を縮めるので、そこへ OS が**元の大きい寸法を戻す**と害が出る＝窓が
          画面からはみ出す。**「小さい方向だけ」では、この向きが素通りする**
          （＝「解像度変更もまとめて塞いだ」という当初の主張は誤りだった）。

        ⇒ 見るのは向きではなく「**選んだ大きさのままか**」。

        ⚠️ **利用者が変更から 800ms 以内に窓の大きさを変えた場合も、これに当たる**
        （その操作は取り消される）。**受け入れる副作用**＝表示スケールを変えた直後は
        「窓の大きさが変わる」ことが期待そのもので、既に `refit_all(shrink=True)` が
        同じ理由で「手で広げた窓も既定サイズへ戻す」ことを選んでいる（I-053）。
        ⚠️ **位置だけを動かしたときは当たらない**（下のゲートで固定）。
        """
        chosen = getattr(win, "_fit_size", None)
        if not chosen:
            return False            # `fit_to_content` を通っていない窓は対象外
        try:
            return (win.winfo_width(), win.winfo_height()) != tuple(chosen)
        except tk.TclError:
            return False            # 破棄途中の窓

    def _arm_settle(args: tuple) -> None:
        if state["settle"] is not None:
            try:
                root.after_cancel(state["settle"])
            except tk.TclError:
                pass
        state["settle"] = root.after(_DISPLAY_SETTLE_MS, lambda: _settle(args))

    def _settle(args: tuple) -> None:
        """落ち着いた頃に**選んだ大きさが残っているか**を確かめ、消えていたら測り直す。

        🔴 **これが無いと追従は一発勝負**（B-118）＝デバウンスの静けさの後に OS が
        窓を測り直すと、以後 DPI は変わらないので `_check` は二度と発火せず、
        **再起動するまで直らない**（字だけ大きくなって窓は元のまま＝両方向に
        スクロールバーが出る）。
        ⚠️ **無条件にもう一度やるのではなく、上書きされたときだけ**＝素通りの
        再実行にすると、利用者が変更直後に窓を動かしただけで元へ戻ってしまう。
        """
        state["settle"] = None
        if on_change is None:
            return
        if _pointer_is_down():
            _arm_settle(args)       # 掴んでいる間は待つ（B-119）
            return
        if not any(_overridden(win) for win in _windows()):
            return
        on_change(*args)

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
        state["after"] = root.after(_DISPLAY_DEBOUNCE_MS, _check)

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
# ⚠️ 10 → 8（2.7 スライス F）。行高の計算が使う行送りの測り方を直した副作用で
# 実測が 2px 太くなったので、**100% での行高（27px）が 1px も動かない**値へ戻した
# ＝直したのは「DPI を往復すると戻らない」ことであって、行の見た目ではない。
_TABLE_ROW_EXTRA = 8


def table_style(widget: tk.Misc, name: str = "App.Treeview") -> str:
    """余白を持たせた Treeview スタイルを用意し、その名前を返す。

    行高はフォントの行送りから決める（DPI・テーマのフォント差に追従させる）。
    スタイル名は `*.Treeview` である必要がある（ttk がレイアウトを名前で辿る）。
    """
    from tkinter import font as tkfont

    style = ttk.Style(master=widget)
    try:
        # ⚠️ **名前付きフォントを直に測る**（`tkfont.Font(font=...)` で写しを作らない）。
        # 写しは `actual()` 経由＝ピクセル指定を**そのときの `tk scaling` で pt へ
        # 丸めて**持つので、DPI を変えて戻すと同じ設定でも別の行送りが返る（実測＝
        # 96dpi で 17px → 144dpi を経由して 96dpi へ戻すと 12px。144dpi では 55px と
        # 過大にもなった）。結果、**表の行高が DPI 往復で戻らない**＝I-053 で窓を
        # 縮められるようにしても中身が元の高さを要求しない。
        linespace = tkfont.nametofont(ui_font(widget), root=widget).metrics("linespace")
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


def menu_font(widget: tk.Misc, *, dpi: "int | None" = None) -> tuple:
    """ドロップダウンへ**直に**渡すフォント（family, ピクセルサイズ）。

    **ピクセル指定（負値）で返す**＝メニューは `tk scaling` を見ないので、pt で
    渡すと DPI が変わっても字が動かない（`_DEFAULT_FONT_NAMES` の注記・B-051）。

    基準は `TkMenuFont`＝**OS のメニューフォント**。帯は Windows が同じ元から
    描くので、こうすると帯と中身の大きさが揃う。
    """
    from tkinter import font as tkfont

    if not _menu_base:
        try:
            conf = tkfont.nametofont("TkMenuFont", root=widget).config() or {}
        except tk.TclError:
            conf = {}
        size = conf.get("size", 9)
        # pt（正値）は 96dpi のピクセルへ直してから基準にする。
        _menu_base["px"] = int(size) if isinstance(size, int) and size < 0 else -int(
            round(abs(int(size or 9)) * _DPI_BASE / 72)
        )
        _menu_base["family"] = conf.get("family") or "Segoe UI"

    from core import i18n
    family = _menu_base["family"]
    if i18n.current_lang() == "ja":
        family = _japanese_family(widget) or family   # 漢字が別書体へ落ちない（B-026）
    # ⚠️ 既定は **最後に `apply_fonts` が使った DPI**（無ければその窓の実測値）。
    # ここで毎回 `window_dpi` を引くと、あとから開いた右クリックメニューだけが
    # 「他のフォントと違う基準」で作られる（テストが DPI を注入した回に露見した）。
    if dpi is None:
        dpi = int(_menu_base.get("dpi") or window_dpi(widget))
    return (family, _scaled_px(int(_menu_base["px"]), dpi))


def menu_options(widget: tk.Misc) -> dict:
    """`tk.Menu.configure()` へ渡す配色＋フォントのオプション一式。

    アクティブ時も前景色を変えない（理由はモジュール docstring）。
    **フォントもここに含める**＝配色と同じ経路で当たるので、メニューを足した人が
    「フォントも当てる」を思い出さなくてよい（[[feedback-promote-recurring-checks]]）。
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
        "font"              : menu_font(widget),
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


# 判定（OK / NG / ERR）の色。**アプリ全体で 1 か所**＝画面・レポートで同じ意味に
# 同じ色を使う（⑧）。ライトは Material の 800〜900、ダークは 300〜400 相当＝暗い地の
# 上で沈まない値。
# ⚠️ ここを増やすときは、必ず両テーマぶん決めてコントラストを測ること
# （B-009＝ダークでツールチップが 1.06:1 だった件と同じクラスの失敗を招く）。
# `err` は 2.7 スライス B で追加（I-010＝成果物の生成に失敗した経路の印）。実測
# コントラスト＝ライト #bf360c on #fafafa で 5.4:1／ダーク #ffa726 on #1c1c1c で
# 8.8:1（どちらも ng と同等）。⚠️ レポート HTML の err（#e65100）とは値が違う＝
# あちらは**白地・太字**の印刷物で、同じ値だと画面の地の上で 3.6:1 まで落ちる。
_VERDICT = {
    "light": {"ok": "#2e7d32", "ng": "#c62828", "err": "#bf360c"},
    "dark":  {"ok": "#66bb6a", "ng": "#ef5350", "err": "#ffa726"},
}


def verdict_colors(widget: tk.Misc) -> "dict[str, str]":
    """判定色（`{"ok": …, "ng": …, "err": …}`）を現在テーマで返す。

    **窓ごとに色を書かない**ための出所。条件探索だけが緑/赤で中継経路は同色、
    という不揃いが実機フィードバックで出た（レポート側は両方色分けしていたので、
    画面だけが落ちていた＝配色の出所が散っていると片方だけ直る）。

    `err`（成果物の生成に失敗）を引くのは結果を**表の行へ返す**窓＝バッチと中継。
    結果一覧（Treeview）を持つ条件探索は ok/ng しか出さないが、**出所を分ける
    理由にはならない**（色が意味に紐づいている限り、使う側が選べばよい）。
    """
    return dict(_VERDICT[current_theme(widget)])


def verdict_key(status: str) -> str:
    """判定文字列（`"OK"` / `"NG"` / `"ERROR"`）→ 配色キー（`ok` / `ng` / `err`）。

    **画面側で `if status == "OK"` を書かないための口**＝バッチ表・中継の区間表・
    結果一覧が同じ対応表を各自持つと、ERR を足したときに片方だけ直る
    （2.6 で「タグは付いているのに色が当たっていない」を踏んだのと同じクラス）。
    """
    return {"OK": "ok", "NG": "ng"}.get(status, "err")


def apply_verdict_tags(tree: "ttk.Treeview") -> None:
    """結果一覧（Treeview）に `ok` / `ng` / `err` タグの配色を当てる。

    **窓ごとに `tag_configure` を書かない**ための口。行を入れる側は
    `tags=("ok",)` を付けるだけでよく、色は 1 か所（`_VERDICT`）で決まる。
    """
    colors = verdict_colors(tree)
    for key, color in colors.items():
        tree.tag_configure(key, foreground=color)


def apply_menu_theme(menus: "list[tk.Menu]", widget: tk.Misc) -> None:
    """与えられた tk.Menu 群へ現在テーマの配色を適用する。"""
    options = menu_options(widget)
    for menu in menus:
        try:
            menu.configure(**options)
        except tk.TclError:
            pass   # 破棄済みのメニューは無視する
