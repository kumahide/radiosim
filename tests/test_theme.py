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

from core import i18n
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


@pytest.mark.parametrize("name", _THEMES)
def test_tooltip_colors_are_legible(root, name):
    """ツールチップの文字が背景から読めること。

    B-008 の掃き出しで見つかった同型の欠陥（2026-07-22）：背景だけ
    `bg="SystemButtonFace"` で固定し、前景は sv_ttk の `tk_setPalette` に
    追従させていたため、ダークでは #fafafa の文字が #f0f0f0 の背景に載って
    **コントラスト 1.06:1＝判読不能**だった。
    """
    set_theme(name)
    options = theme.tooltip_options(root)
    ratio = _contrast(options["foreground"], options["background"])
    assert ratio >= 4.5, f"{name} テーマのツールチップのコントラスト不足（{ratio:.2f}:1）"


def test_tooltip_takes_both_colors_from_theme():
    """ランチャーのツールチップが前景・背景の両方をテーマから取ること。

    **この欠陥の型＝前景と背景を別々の出所から取ること**（片方だけテーマに
    追従すると、もう片方と衝突して必ずどこかのテーマで溶ける）。実物の
    ツールチップを生成し、システム色（SystemButtonFace 等）が残っていないこと、
    および実効色のコントラストを検証する。
    """
    root = make_tk_root()
    try:
        root.withdraw()
        set_theme("dark")
        from views.tooltip import Tooltip     # 2.7 スライス A で launcher から独立
        target = tk.Entry(root)
        target.pack()
        root.update_idletasks()

        tip = Tooltip(target, "テスト")
        tip._show()
        assert tip._tip is not None
        label = tip._tip.winfo_children()[0]

        for option in ("background", "foreground"):
            value = str(label.cget(option))
            assert not value.startswith("System"), (
                f"ツールチップの {option} がシステム色 {value} のまま＝テーマ非追従"
            )
        to_hex = lambda spec: "#%02x%02x%02x" % tuple(  # noqa: E731
            c // 257 for c in root.winfo_rgb(spec)
        )
        ratio = _contrast(to_hex(str(label.cget("foreground"))),
                          to_hex(str(label.cget("background"))))
        assert ratio >= 4.5, f"ツールチップの実効コントラストが不足（{ratio:.2f}:1）"
    finally:
        root.destroy()


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
        if option == "font":
            continue        # 色ではないので下で別に見る（Tk が文字列へ正規化する）
        assert str(menu.cget(option)) == value, f"{option} が適用されていない"

    # フォントも同じ経路で当たること（B-051）。
    # ⚠️ `tkfont.Font(font=…).config()` を通してはいけない＝**ピクセル指定を pt へ
    # 丸めて返す**ので（-12px → 9pt）、指定した値と比べられない。cget が返す
    # 記述子の文字列で見る。
    applied = str(menu.cget("font"))
    family, size = expected["font"]
    assert family in applied and str(size) in applied, (
        f"font が適用されていない: {applied!r} に {family!r}/{size} が無い"
    )


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
        from core import simulation as sim
        from views.batch_builder import BatchBuilderWindow
        win = BatchBuilderWindow(root, sim.SimParams(_PARAMS))
        expected = theme.palette(root)["bg"]
        assert str(win._canvas.cget("bg")) == expected, (
            "キャンバス背景がテーマ色になっていない（素の tk 既定のまま）"
        )
    finally:
        root.destroy()


# ============================================================
# 補助テキスト（I-009 / 2.5a1）
# ============================================================
# 旧実装の固定色。ここを基準に「読みにくくしていない」ことを固定する。
_LEGACY_MUTED = "#808080"


@pytest.mark.parametrize("name", _THEMES)
def test_muted_text_is_not_less_legible_than_the_old_fixed_gray(root, name):
    """補助テキストが従来の固定色 `gray` より読みにくくなっていないこと。

    I-009 の判断（2026-07-25）＝**WCAG AA の 4.5:1 は機械適用しない**。sv_ttk 自身の
    disabled 色が 2.4〜2.5:1 で、「補助情報は落として見せる」がテーマの設計言語だから。
    代わりに守るのは「テーマ色を出所にすること」と「従来より暗くしないこと」で、
    後者をここで数値として固定する（gray = ライト 3.78:1 / ダーク 4.32:1）。
    """
    set_theme(name)
    bg = theme.palette(root)["bg"]
    now = _contrast(theme.muted_foreground(root), bg)
    legacy = _contrast(_LEGACY_MUTED, bg)
    assert now >= legacy, (
        f"{name} テーマの補助テキストが従来の gray より読みにくい"
        f"（{now:.2f}:1 < {legacy:.2f}:1）"
    )


@pytest.mark.parametrize("name", _THEMES)
def test_muted_text_is_clearly_above_disabled(root, name):
    """補助テキストが「無効」に見えないこと。

    補助情報は落として見せるが、**読める**ことは要件（無効表示との差が消えると
    「押せない/使えない」という別の意味になる）。sv_ttk の disabled より明確に
    高いコントラストを保つ。
    """
    set_theme(name)
    colors = theme.palette(root)
    muted = _contrast(theme.muted_foreground(root), colors["bg"])
    disabled = _contrast(colors["disfg"], colors["bg"])
    assert muted >= disabled + 1.0, (
        f"{name} テーマで補助テキスト（{muted:.2f}:1）が disabled"
        f"（{disabled:.2f}:1）と区別できない"
    )


def test_muted_foreground_follows_the_theme(root):
    """補助テキスト色がテーマごとに変わること（固定色への逆戻り検出）。"""
    seen = {}
    for name in _THEMES:
        set_theme(name)
        seen[name] = theme.muted_foreground(root)
    assert seen["light"] != seen["dark"], f"テーマを変えても補助テキスト色が同じ: {seen}"


def test_no_hardcoded_gray_foreground_in_views():
    """補助テキストの配色が views の中で直書きへ戻っていないこと。

    B-008 と同じクラス（配色の出所が theme.py でない）なので、注意書きでなく
    構造で固定する。`foreground="gray"` / `fg="gray"` は theme.muted_foreground へ。
    """
    import re

    views_dir = os.path.join(os.path.dirname(__file__), "..", "views")
    pattern = re.compile(r'(?:fg|foreground)\s*=\s*["\']gray["\']')
    offenders = []
    for fname in sorted(os.listdir(views_dir)):
        # theme.py は色の出所そのもの（docstring で旧実装の直書きを説明している）。
        if not fname.endswith(".py") or fname == "theme.py":
            continue
        path = os.path.join(views_dir, fname)
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if pattern.search(line):
                    offenders.append(f"{fname}:{lineno}")
    assert not offenders, (
        f"補助テキストの色が直書きされている: {offenders}。"
        "views/theme.py の muted_foreground() から取ること。"
    )


# ============================================================
# フォント（配色と同じく「出所は theme.py 一本」）
# ============================================================
def test_ui_font_comes_from_sv_ttk(root):
    """本文フォントの出所が sv_ttk の名前付きフォントであること。

    2.5b2 / I-023：sv_ttk は Entry/Combobox/Treeview に `SunValleyBodyFont` を
    当てるが Label/Button には当てない。アプリ側が別の書体（`("Arial", 9)`）を
    ベタ書きすると、**窓ごと・ウィジェットごとに書体もサイズも揃わない**。
    """
    set_theme("dark")
    assert theme.ui_font(root)          == "SunValleyBodyFont"
    assert theme.ui_font(root, "small") == "SunValleyCaptionFont"


def _family(widget, kind: str) -> str:
    """名前付きフォントに設定されている書体名。"""
    from tkinter import font as tkfont

    return str(tkfont.nametofont(theme.ui_font(widget, kind), root=widget)
               .config()["family"])


def test_ui_font_has_no_bold(root):
    """**画面に太字は無い**（2026-08-01 ユーザー決定＝かえって読みにくい）。

    強調は配置・余白・区切り線で作る。`ui_font(…, "bold")` を復活させたくなったら
    先に ISSUES.md B-026 を読むこと＝Segoe UI Variable 系は日本語グリフを持たず、
    **漢字のフォントリンク先を決めるのは family ではなく weight** なので、太字に
    した瞬間に漢字だけ Malgun Gothic（韓国語）へ落ちる（2026-07-31 実測）。
    ⚠️ レポート HTML の `font-weight:bold` は別の設計言語なので対象外。
    """
    set_theme("dark")
    with pytest.raises(KeyError):
        theme.ui_font(root, "bold")


def test_japanese_locale_uses_a_japanese_capable_face(root):
    """日本語では**日本語グリフを持つ書体**を本文にすること（B-026 の処方②）。

    sv_ttk の Segoe UI Variable 系は日本語を持たず、不足分は Windows の
    フォントリンク任せになる＝漢字だけ別書体で描かれ、しかも太字にすると
    Malgun Gothic（韓国語）へ落ちる。**指定の側で日本語を持つ書体を選べば、
    リンクに落ちる経路そのものが無くなる。**

    ⚠️ ここで守れるのは「アプリが何を指定したか」まで。**実際に何で描かれるか**
    （`font actual … -- 漢`）は OS のフォントリンク次第でヘッドレスでは測れない
    ので、そちらは `experiments/font_fallback_probe.py` で手元実測する
    （①を実装したのに症状が動かなかったのは、この層を測らずに処方を決めたため）。
    """
    from tkinter import font as tkfont

    prev = i18n.current_lang()
    set_theme("dark")
    try:
        i18n.set_lang("ja")
        theme.apply_fonts(root, dpi=96)
        family = _family(root, "body")
        if not (set(_theme_families(root)) & set(theme._JA_FAMILIES)):
            pytest.skip("この環境に日本語書体が 1 つも無い")
        assert family in theme._JA_FAMILIES, (
            f"日本語なのに本文が {family}＝日本語グリフを持たない書体だと、"
            "漢字が Windows のフォントリンクで別書体（太字では韓国語）に落ちる。"
        )
        for kind in ("small", "_theme_strong"):
            assert _family(root, kind) == family, (
                f"{kind} だけ別書体（{_family(root, kind)}）＝そこだけ字形が割れる。"
            )
        for name in ("TkDefaultFont", "TkTextFont"):
            got = tkfont.nametofont(name, root=root).config()["family"]
            assert got == family, f"{name} が本文と別書体（{got}）"
    finally:
        i18n.set_lang(prev)
        theme.apply_fonts(root, dpi=96)


def test_english_locale_keeps_the_sv_ttk_face(root):
    """英語では sv_ttk の書体のままであること（差し替えを一方通行にしない）。

    日本語で書体を差し替えたあと英語へ戻したときに元へ戻らないと、**en の見た目が
    ja のセッションを開いたかどうかで変わる**（名前付きフォントはプロセスで共有）。
    """
    prev = i18n.current_lang()
    set_theme("dark")
    try:
        i18n.set_lang("en")
        theme.apply_fonts(root, dpi=96)
        base = _family(root, "body")
        i18n.set_lang("ja")
        theme.apply_fonts(root, dpi=96)
        i18n.set_lang("en")
        theme.apply_fonts(root, dpi=96)
        assert _family(root, "body") == base, (
            f"英語へ戻したのに書体が {_family(root, 'body')} のまま"
            f"（本来 {base}）＝日本語を一度開いたかどうかで見た目が変わる。"
        )
    finally:
        i18n.set_lang(prev)
        theme.apply_fonts(root, dpi=96)


def _theme_families(root) -> tuple:
    from tkinter import font as tkfont
    return tkfont.families(root)


def _effective(widget) -> tuple:
    """ウィジェットが実際に描画に使うフォントの（書体, サイズ）。

    比較は**名前ではなく実値**で行う。名前で比べると `TkTextFont` と
    `SunValleyBodyFont` が「違う」と出るが、既定フォントを書き換えた後は
    同じ書体・同じサイズ＝見た目は揃っている（逆に、名前が同じでも実値が
    違うことはない）。
    """
    from tkinter import font as tkfont

    spec = str(widget.cget("font"))
    if not spec:
        # ウィジェットオプションが空＝スタイル、それも無ければ Tk の既定。
        spec = ttk.Style(master=widget).lookup(widget.winfo_class(), "font")             or "TkDefaultFont"
    f = tkfont.Font(root=widget, font=spec)
    return f.actual("family"), f.actual("size")


def test_labels_and_entries_render_in_the_same_font(root):
    """ラベルと入力欄が同じ書体・同じサイズで描かれること。

    sv_ttk は Entry/Combobox/Treeview にだけ本文フォントを当て、Label/Button
    には当てない（＝ja 環境では Yu Gothic UI 9pt と Segoe UI Variable Text
    10pt が同じ窓に混在する）。1 つの窓の中で字面が揃うことを固定する。
    """
    set_theme("dark")
    theme.apply_fonts(root)
    label = ttk.Label(root, text="x")
    entry = ttk.Entry(root)
    combo = ttk.Combobox(root)
    assert _effective(label) == _effective(entry) == _effective(combo), (
        f"ラベル {_effective(label)} / 入力欄 {_effective(entry)} / "
        f"選択欄 {_effective(combo)} が揃っていない"
    )


def test_dynamically_created_widgets_get_the_same_font(root):
    """**あとから作った**ウィジェットも同じフォントになること。

    2.5b2 の最初の版はスタイル（`style.configure("TEntry", font=…)`）だけで
    揃えようとしたが、ttk の Entry/Combobox は `-font` を**ウィジェット
    オプション**として持ち、その既定値 `TkTextFont` がスタイルより優先される。
    結果、条件探索で「条件を追加」して生やした列だけ字が小さいまま出た
    （既存の列は sv_ttk が `<<ThemeChanged>>` 時に個別に当てていた）。
    **窓を組み立てた直後に測るテストでは落ちない**ので、生成の遅い側を明示的に
    測る。
    """
    set_theme("dark")
    theme.apply_fonts(root)
    first = ttk.Entry(root)
    root.update_idletasks()
    later = ttk.Entry(root)          # 起動後・テーマ変更後に生成
    assert _effective(later) == _effective(first), (
        f"あとから作った入力欄のフォントが違う: {_effective(later)} != {_effective(first)}"
    )


def test_fonts_survive_a_theme_switch(root):
    """テーマを切り替えても字面が揃ったままであること。

    ttk のスタイル設定は**テーマごとに独立した辞書**なので、`style.configure()`
    で揃える実装はライト→ダークで黙って壊れる（配色で踏んだ B-004 と同型）。
    既定フォント自体を書き換える方式ならテーマに依存しないことを固定する。
    """
    set_theme("light")
    theme.apply_fonts(root)
    set_theme("dark")
    root.update()
    assert _effective(ttk.Label(root, text="x")) == _effective(ttk.Entry(root))


def test_no_hardcoded_font_family_in_views():
    """views の中でフォント書体・サイズが直書きされていないこと。

    実機フィードバック（2026-07-26）＝「ウィンドウ毎にフォントサイズが統一されて
    いない」。原因はランチャー／バッチだけが `("Arial", 8)` `("Arial", 9)` を
    ベタ書きしていたこと。配色（`gray` 直書き）と同じクラスなので、同じように
    構造で固定する＝フォントは `theme.ui_font()` か Tk の名前付きフォントから取る。
    """
    import re

    views_dir = os.path.join(os.path.dirname(__file__), "..", "views")
    # font= に書体名リテラル（("Arial", 9) 等）が来ているものを拾う。
    # `font=theme.ui_font(...)` / `font="TkFixedFont"` は許す。
    pattern = re.compile(r'font\s*=\s*\(\s*["\']')
    offenders = []
    for fname in sorted(os.listdir(views_dir)):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(views_dir, fname)
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if pattern.search(line):
                    offenders.append(f"{fname}:{lineno}")
    assert not offenders, (
        f"フォントが直書きされている: {offenders}。"
        "views/theme.py の ui_font() から取ること。"
    )


def test_table_padding_survives_a_theme_switch(root):
    """結果表の余白（行高・padding）もテーマ切替で消えないこと。

    2026-07-26 の実機フィードバック「余白を設定する」への対処。スタイル設定が
    テーマごとの辞書である以上、フォントとまったく同じクラスの落とし穴がある。
    """
    set_theme("light")
    name = theme.table_style(root, "TestTable.Treeview")
    set_theme("dark")
    root.update()
    style = ttk.Style(master=root)
    assert style.lookup(name, "rowheight"), "テーマ切替で表の行高指定が消えた"
    assert style.lookup(name, "padding"),   "テーマ切替で表の外周余白が消えた"
    # **セルの中**の余白が本命＝外周だけ広げても、右詰めの数字は隣の列に
    # くっついたまま（b2 の最初の版で「余白が効いていない」と再指摘された）。
    assert style.lookup(f"{name}.Cell", "padding"), "セル内の余白が設定されていない"
    assert style.lookup(f"{name}.Heading", "padding"), "見出しの余白が設定されていない"


# ============================================================
# DPI 追従（2026-07-26 のユーザー報告：窓は変わるのに字が変わらない）
# ============================================================
def _px(widget, kind: str = "body") -> int:
    """名前付きフォントに設定されているピクセル数（負値＝px 指定）。"""
    from tkinter import font as tkfont

    return abs(int(tkfont.nametofont(theme.ui_font(widget, kind), root=widget)
                   .config()["size"]))


def test_fonts_scale_with_dpi(root):
    """DPI を上げたらフォントも大きくなること。

    sv_ttk のフォントは**ピクセル指定**（`-14`）で、Tk の `tk scaling` の影響を
    受けない＝放っておくと DPI が変わっても 1px も変わらない。一方 Windows は
    Per-Monitor DPI Aware の窓を拡大するので、「窓は大きくなったのに字は小さい
    まま」になる（2026-07-26 のユーザー報告）。
    """
    set_theme("dark")
    theme.apply_fonts(root, dpi=96)
    at96 = _px(root)
    theme.apply_fonts(root, dpi=144)          # 150% スケール
    at144 = _px(root)
    assert at144 > at96, f"DPI を上げてもフォントが変わらない（{at96}px のまま）"
    assert at144 == round(at96 * 1.5), f"倍率が DPI 比に一致しない: {at96} → {at144}"


def test_dpi_scaling_is_not_cumulative(root):
    """同じ DPI で何度貼り直しても字が太らないこと。

    実測値を毎回読み直して掛けると、貼り直すたびに拡大が積み上がる（DPI 変更は
    移動のたびに走り得るので、これは致命的になる）。基準は 96dpi のピクセル数で
    固定する。
    """
    set_theme("dark")
    theme.apply_fonts(root, dpi=144)
    once = _px(root)
    for _ in range(3):
        theme.apply_fonts(root, dpi=144)
    assert _px(root) == once, "同じ DPI の貼り直しでフォントが太った（倍率の累積）"


def test_dpi_change_is_reversible(root):
    """高 DPI から戻ったら元のサイズに戻ること。"""
    set_theme("dark")
    theme.apply_fonts(root, dpi=96)
    at96 = _px(root)
    theme.apply_fonts(root, dpi=192)
    theme.apply_fonts(root, dpi=96)
    assert _px(root) == at96


def test_every_ui_font_scales_together(root):
    """本文・小・太字がすべて同じ倍率で動くこと（DPI で字面が崩れない）。"""
    set_theme("dark")
    theme.apply_fonts(root, dpi=96)
    # `_theme_strong` は画面では使わないが、sv_ttk 自身がテーマ定義から参照する
    # ので追従の対象に含める（ここを外すと Treeview 等が DPI で取り残される）。
    base = {k: _px(root, k) for k in ("body", "small", "_theme_strong")}
    theme.apply_fonts(root, dpi=192)
    for kind, was in base.items():
        assert _px(root, kind) == was * 2, f"{kind} が DPI に追従していない"


def test_table_row_height_follows_the_font(root):
    """表の行高もフォントに追従すること（行送りから算出しているため）。"""
    set_theme("dark")
    theme.apply_fonts(root, dpi=96)
    style = ttk.Style(master=root)
    low = int(style.lookup(theme.table_style(root), "rowheight"))
    theme.apply_fonts(root, dpi=192)
    high = int(style.lookup("App.Treeview", "rowheight"))
    assert high > low, f"DPI を上げても行高が変わらない（{low}px のまま）"


def test_watch_display_actually_fires_on_a_configure_event(root):
    """配線が実際に動くこと（監視 → 検知 → 貼り直し → 通知）。

    ⚠️ **ここが本丸**。「イベントに繋いだつもりで一度も発火しない」は本プロジェクト
    が繰り返し踏んでいる形で、直近も `<<ThemeChanged>>` での貼り直しが root に
    届いておらず、フォント統一が黙って無効化されていた（2.5b2）。監視の配線は
    実装ではなくテストで裏を取る。
    """
    import tkinter as tk

    set_theme("dark")
    theme.apply_fonts(root, dpi=96)
    at96 = _px(root)

    fake = {"dpi": 96}
    monkey = theme.window_dpi
    theme.window_dpi = lambda _w: fake["dpi"]      # type: ignore[assignment]
    notified: "list[tuple[int, bool]]" = []
    try:
        theme.watch_display(root, lambda d, c: notified.append((d, c)))
        fake["dpi"] = 144                          # 別 DPI のモニタへ移した相当
        win = tk.Toplevel(root)
        win.geometry("200x100+10+10")              # 移動＝<Configure>
        root.update()
        # デバウンス（_DISPLAY_DEBOUNCE_MS）を消化する
        root.after(theme._DISPLAY_DEBOUNCE_MS + 80, root.quit)
        root.mainloop()
        assert notified == [(144, True)], (
            f"DPI 変化が通知されていない、または「DPI が変わった」と伝わっていない:"
            f" {notified}（後者だと窓が縮む方向に測り直されない＝I-053）。"
        )
        assert _px(root) == round(at96 * 1.5), "通知は来たがフォントが貼り直されていない"
    finally:
        theme.window_dpi = monkey                  # type: ignore[assignment]
        theme.apply_fonts(root, dpi=96)


def test_watch_display_notices_a_resolution_change_with_the_same_dpi(root, monkeypatch):
    """**DPI が変わらない画面サイズの変化**でも測り直しが走ること（B-022）。

    旧 `watch_dpi` は `<Configure>` を受け取りながら「DPI が同じなら即 return」で
    捨てていた＝解像度変更・VDI の動的解像度・リモート再接続が素通りしていた。
    害は両方向で、狭くなれば窓がデスクトップの外へ出たまま、広くなればクランプ
    された小さいまま**二度と戻らない**（アプリの再起動しか回復手段が無かった）。

    ⚠️ **フォントは貼り直さないこと**も同時に見る＝解像度だけの変化で全窓の
    フォントを触るのは無駄で、副作用の面だけが広がる。
    """
    import tkinter as tk

    from views import window_fit

    set_theme("dark")
    theme.apply_fonts(root, dpi=96)
    before_px = _px(root)

    screen = {"size": (1920, 1080)}
    monkeypatch.setattr(theme, "window_dpi", lambda _w: 96)
    monkeypatch.setattr(window_fit, "screen_size", lambda _w: screen["size"])

    notified: "list[tuple[int, bool]]" = []
    theme.watch_display(root, lambda d, c: notified.append((d, c)))
    screen["size"] = (1280, 720)                   # 解像度だけが変わった
    win = tk.Toplevel(root)
    win.geometry("200x100+10+10")                  # <Configure> の契機
    root.update()
    root.after(theme._DISPLAY_DEBOUNCE_MS + 80, root.quit)
    root.mainloop()

    assert notified == [(96, False)], (
        f"画面サイズの変化が素通りしている、または「DPI が変わった」と誤って"
        f"伝えている: {notified}（前者は B-022＝窓が新しい画面に対して測り直され"
        "ないまま残る。後者は I-053＝解像度が変わっただけで、ユーザーが手で広げた"
        "窓まで既定サイズへ縮む）。"
    )
    assert _px(root) == before_px, (
        "解像度だけの変化でフォントまで貼り直している（DPI は変わっていない）。"
    )


# ============================================================
# DPI 認識のレベル（I-054＝メニューバーを OS に追従させる唯一の口）
# ============================================================
# ⚠️ **ここは「実際に効いたか」を見るテストではない**（プロセスの DPI 認識は
# 起動時に一度だけ決まり、pytest のプロセスでは既に確定している）。見るのは
# **試す順序**＝v2 を先に試すこと・使えない Windows でも黙って落ちないこと。
#
# なぜ順序が守る価値のあるものか＝メニューバー（ファイル / 設定 / ヘルプ）は
# Tk の管轄外で、`TkMenuFont` を書き換えても `tk.Menu(font=…)` を直に指定しても
# 1px も変わらない（2026-08-07 実測。変わるのはドロップダウンだけ）。**OS が
# 非クライアント領域をスケールしてくれる v2 でしか追従しない**ので、v1 へ静かに
# 落ちると I-054 は「直したつもりで直っていない」に戻る。
class _FakeWindll:
    """`ctypes.windll` の代役（どの API まで生えているかを差し替える）。"""

    def __init__(self, *, v2: "bool | None", v1: bool, system: bool = True) -> None:
        self.calls: list[str] = []
        self._v2, self._v1, self._system = v2, v1, system
        outer = self

        class _User32:
            SetProcessDpiAwarenessContext = None   # 属性差し替えを許すための箱

            def SetProcessDPIAware(self):          # noqa: N802 — Win32 の名前
                outer.calls.append("system")
                if not outer._system:
                    raise OSError("no such API")
                return True

        class _Shcore:
            def SetProcessDpiAwareness(self, _level):   # noqa: N802
                outer.calls.append("v1")
                if not outer._v1:
                    raise OSError("no such API")
                return 0

        def _ctx(_handle):
            outer.calls.append("v2")
            if outer._v2 is None:
                raise OSError("no such API")
            return outer._v2

        self.user32 = _User32()
        self.user32.SetProcessDpiAwarenessContext = _ctx
        self.shcore = _Shcore()


def test_dpi_awareness_prefers_per_monitor_v2():
    """v2 が使えるなら v2 で止まること（v1 へ落ちない）。"""
    import main

    fake = _FakeWindll(v2=True, v1=True)
    assert main._set_dpi_awareness(fake) == "per-monitor-v2"
    assert fake.calls == ["v2"], (
        f"v2 の後まで試している: {fake.calls}"
        "（あとから弱い認識を設定しても効かないが、順序が壊れている印）。"
    )


@pytest.mark.parametrize("v2, v1, system, expected, first", [
    (None,  True,  True,  "per-monitor", "v2"),   # v2 が無い Windows 8.1
    (False, True,  True,  "per-monitor", "v2"),   # v2 はあるが設定に失敗
    (None,  False, True,  "system",      "v2"),   # shcore ごと無い Vista/7
    (None,  False, False, "none",        "v2"),   # 何も無い（Windows 以外の実験）
])
def test_dpi_awareness_falls_back_in_order(v2, v1, system, expected, first):
    """使えない環境では静かに次へ落ちること（例外を漏らさない）。"""
    import main

    fake = _FakeWindll(v2=v2, v1=v1, system=system)
    assert main._set_dpi_awareness(fake) == expected
    assert fake.calls[0] == first, f"最初に試したのが v2 でない: {fake.calls}"


# ============================================================
# メニューの字が表示スケールに追従する（B-051）
# ============================================================
# 🔴 **メニューの字は 2 つの別々の仕組みで決まる**（2026-08-08 実測）:
#   帯（ファイル / 設定 / ヘルプ）＝Windows が描く HMENU ＝ OS が拡大する（I-054・PMv2）。
#   ドロップダウン＝Tk が描くが、**`TkMenuFont` にも `tk scaling` にも従わない**
#   （scaling を 1.33→2.00 にしても要求高は 97px のまま）。効くのは
#   **ウィジェットへの直接指定 `font=`** だけ。
# ⇒ I-054 は帯だけを直したので「帯は大きいのに中身は小さい」が残った
#   （ユーザーが 150% のスクショで報告）。ここはその回帰ガード。
#
# ⚠️ **見るのは font オプションではなく要求サイズ**＝`font=` を設定しただけの
# ゲートは、設定が効かない書き方（pt 指定・名前付きフォント）でも緑になる。
# 実際に**メニューが要求する高さ**が DPI 比で伸びることまで見る。

def _sample_menu(root: tk.Misc) -> tk.Menu:
    menu = tk.Menu(root, tearoff=0)
    for label in ("テーマ", "言語", "座標の表示形式", "プロキシ設定...", "全キャッシュ削除..."):
        menu.add_command(label=label)
    theme.apply_menu_theme([menu], root)
    root.update_idletasks()
    return menu


def test_dropdown_grows_with_dpi(root):
    """既に開いている窓のメニューも、DPI が変わったら大きくなること。

    ⚠️ **これが本命**＝利用者は「アプリを起動したまま表示スケールを変える」。

    ⚠️ **入れ子（カスケードの子）まで見る**＝メニューバーの子メニューは*その親の*
    子ウィジェットなので、木を 1 段しか見ない実装だと「帯を開いた 1 段目は大きいのに
    サブメニューだけ小さい」が残る。
    """
    theme.apply_fonts(root, dpi=96)
    menu = _sample_menu(root)
    child = tk.Menu(menu, tearoff=0)
    for label in ("ライト", "ダーク", "システム"):
        child.add_radiobutton(label=label)
    menu.add_cascade(label="テーマ", menu=child)
    theme.apply_menu_theme([child], root)
    root.update_idletasks()
    at96, child96 = menu.winfo_reqheight(), child.winfo_reqheight()

    theme.apply_fonts(root, dpi=144)
    root.update_idletasks()
    at144, child144 = menu.winfo_reqheight(), child.winfo_reqheight()

    assert at96 > 0 and child96 > 0
    assert at144 >= at96 * 1.4, (
        f"ドロップダウンが DPI に追従していない（96dpi={at96}px / 144dpi={at144}px）。"
        "TkMenuFont や tk scaling では動かない＝tk.Menu へ直に font= を渡すこと。"
    )
    assert child144 >= child96 * 1.4, (
        f"サブメニューが追従していない（96dpi={child96}px / 144dpi={child144}px）。"
        "メニューの木を 1 段しか辿っていない可能性がある。"
    )


def test_dropdown_created_later_uses_the_same_scale(root):
    """あとから作るメニュー（右クリック）も同じ基準で作られること。

    バッチ表の per-row メニューは**その場で生成**されるので、生成時の基準が
    ずれていると「帯は大きいのに右クリックだけ小さい」が別の形で復活する。

    ⚠️ **測る順序に注意**＝`apply_fonts` は現存するメニューを全部貼り直すので、
    2 枚を並べて持ったまま DPI を変えると**両方が新しい DPI に揃う**（それが正しい
    振る舞い）。⇒ **各 DPI で「作って測って捨てる」**。
    """
    theme.apply_fonts(root, dpi=96)
    small = _sample_menu(root)
    at96 = small.winfo_reqheight()
    small.destroy()

    theme.apply_fonts(root, dpi=144)
    later = _sample_menu(root)          # DPI を上げた**あと**に生まれたメニュー
    at144 = later.winfo_reqheight()

    assert at144 >= at96 * 1.4, (
        f"あとから作ったメニューの基準がずれている（96dpi 時={at96}px / 144dpi 時={at144}px）"
    )
    # ⚠️ **大きさだけでは足りない**＝生成経路（menu_options）が font を渡さなくても、
    # たまたま近い値になることがある。**明示指定そのもの**が当たっていることまで見る
    # （変異検証で、大きさだけの assert が font を外す変異を素通りさせた）。
    _family, size = theme.menu_font(root, dpi=144)
    assert str(size) in str(later.cget("font")), (
        f"生成時に font が明示されていない: {later.cget('font')!r}（期待サイズ {size}）"
    )


def test_dropdown_font_is_pixel_specified(root):
    """フォントは**ピクセル指定（負値）**であること。

    pt で渡すと `tk scaling` 頼みになり、メニューはそれを見ないので静かに
    追従しなくなる（この項目の原因そのもの）。
    """
    _family, size = theme.menu_font(root, dpi=144)
    assert isinstance(size, int) and size < 0, f"pt 指定になっている: {size}"


def test_launcher_dropdowns_follow_dpi():
    """ランチャーの実物のメニュー（帯＋全サブメニュー）が追従すること。"""
    root = make_tk_root()
    try:
        root.withdraw()
        set_theme("light")
        from views.launcher import SimLauncher
        SimLauncher(root, lambda _t: None)

        theme.apply_fonts(root, dpi=96)
        root.update_idletasks()
        before = [m.winfo_reqheight() for m in _attached_menus(root)]

        theme.apply_fonts(root, dpi=144)
        root.update_idletasks()
        after = [m.winfo_reqheight() for m in _attached_menus(root)]

        assert before and len(before) == len(after)
        grew = [(b, a) for b, a in zip(before, after) if a >= b * 1.4]
        assert len(grew) == len(before), (
            f"追従していないメニューがある: 96dpi={before} / 144dpi={after}"
        )
    finally:
        root.destroy()
