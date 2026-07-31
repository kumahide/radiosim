"""日本語の書体が本当に揃っているかを実測する（B-026）。

**なぜスクリプトで残すか**：この判定は Windows のフォントリンクの挙動そのもの
なので、ヘッドレス CI では確かめられない（tests/test_theme.py が守れるのは
「アプリが何を指定したか」までで、「その指定で実際に何が描かれるか」は OS が
決める）。当初案①が「実装したのに症状が 1mm も動かない」で棄却されたのは、
まさにこの層を測らずに処方を決めたためなので、測り方を残しておく。

見るのは `font actual <name> -- 漢` の family＝**その名前付きフォントで漢字を
描いたときに実際に使われる書体**。ラテン（`-- A`）と食い違っていれば、その
フォントは日本語をフォントリンクで借りている。

実行:

    python experiments/font_fallback_probe.py          # 今のアプリの設定で測る
    python experiments/font_fallback_probe.py --raw    # sv_ttk 素の書体も測る
"""

import os
import sys
import tkinter as tk
from tkinter import font as tkfont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import i18n                                    # noqa: E402
from views import theme                        # noqa: E402


def actual(root: tk.Misc, spec, char: str) -> str:
    """`spec`（名前付きフォント名 or (family, size, weight)）で `char` を描く書体。"""
    # 引数の順は `font actual <font> ?option? -- <char>`（option が先）。
    return root.tk.call("font", "actual", spec, "-family", "--", char)


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    import sv_ttk
    sv_ttk.set_theme("dark")

    if "--raw" in sys.argv:
        print("== sv_ttk 素の状態（apply_fonts を通さない）==")
    else:
        i18n.set_lang("ja")
        theme.apply_fonts(root, dpi=96)
        print("== 日本語ロケールで apply_fonts を通した状態 ==")

    rows = [
        ("本文", theme.ui_font(root, "body")),
        ("小",   theme.ui_font(root, "small")),
        ("強調", theme.ui_font(root, "bold")),
        ("既定(TkDefaultFont)", "TkDefaultFont"),
        ("入力(TkTextFont)",    "TkTextFont"),
    ]
    width = max(len(label) for label, _ in rows)
    print(f"{'用途'.ljust(width)}  {'指定した書体':<32} {'漢の書体':<24} ラテンの書体")
    ok = True
    for label, name in rows:
        try:
            spec = tkfont.nametofont(name, root=root).config() or {}
        except tk.TclError:
            continue
        kanji, latin = actual(root, name, "漢"), actual(root, name, "A")
        print(f"{label.ljust(width)}  {str(spec.get('family')):<32} "
              f"{kanji:<24} {latin}")
        ok = ok and kanji == latin
    print()
    print("漢字とラテンが同じ書体で描かれている: " + ("はい" if ok else "**いいえ**"))
    print("（いいえ＝どこかでフォントリンクに落ちている＝B-026 の症状が残っている）")
    root.destroy()


if __name__ == "__main__":
    main()
