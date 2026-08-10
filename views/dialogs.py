"""
views/dialogs.py
================
親ウィンドウ中央に表示するモーダルダイアログ。

tkinter の messagebox は Windows ネイティブ API のため表示位置を制御できず、
親ウィンドウ（ランチャー／マップウィンドウ）の中央に出せない。そこで tk.Toplevel
＋ geometry() で明示的に中央配置する共通実装をここに集約する。
"""

import tkinter as tk
from tkinter import ttk

from core import i18n


def _center_on(parent: tk.Misc, dlg: tk.Toplevel) -> None:
    """dlg を parent（が属するウィンドウ）の中央に配置する。"""
    dlg.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width()  - dlg.winfo_width())  // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - dlg.winfo_height()) // 2
    dlg.geometry(f"+{x}+{y}")


def _make(parent: tk.Misc, title: str, message: str) -> tuple[tk.Toplevel, ttk.Frame]:
    dlg = tk.Toplevel(parent)
    dlg.transient(parent.winfo_toplevel())
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.grab_set()
    ttk.Label(
        dlg, text=message, wraplength=340, justify="left", padding=(20, 16, 20, 12)
    ).pack()
    btns = ttk.Frame(dlg)
    btns.pack(pady=(0, 12))
    return dlg, btns


def alert(parent: tk.Misc, title: str, message: str) -> None:
    """parent 中央に OK ダイアログを表示する（モーダル）。"""
    dlg, btns = _make(parent, title, message)
    ttk.Button(btns, text=i18n.t("dlg_ok"), command=dlg.destroy).pack()
    _center_on(parent, dlg)
    dlg.wait_window()


def confirm(parent: tk.Misc, title: str, message: str) -> bool:
    """parent 中央に Yes/No 確認ダイアログを表示し、Yes なら True を返す。"""
    dlg, btns = _make(parent, title, message)
    result = {"ok": False}

    def _yes() -> None:
        result["ok"] = True
        dlg.destroy()

    ttk.Button(btns, text=i18n.t("dlg_yes"), command=_yes).pack(side="left", padx=6)
    ttk.Button(btns, text=i18n.t("dlg_no"), command=dlg.destroy).pack(side="left", padx=6)
    _center_on(parent, dlg)
    dlg.wait_window()
    return result["ok"]


def choose(parent: tk.Misc, title: str, message: str,
           options: "list[tuple[str, str]]",
           cancel_label: "str | None" = None) -> "str | None":
    """選択肢が 3 つ以上あるときのダイアログ。選ばれたキーを返す（閉じたら None）。

    `options` は ``[(キー, ボタンのラベル), …]``。左から順に並べ、最後に
    キャンセル（`cancel_label`・既定は「閉じる」相当）を置く。

    Yes/No で足りない場面はここに集約する＝**呼び出し側で Toplevel を組み直さない**
    （ダイアログの見た目・中央配置・モーダル制御を 1 か所に保つ）。
    """
    dlg, btns = _make(parent, title, message)
    result: dict[str, "str | None"] = {"key": None}

    def _pick(key: str) -> None:
        result["key"] = key
        dlg.destroy()

    for key, label in options:
        ttk.Button(btns, text=label, command=lambda k=key: _pick(k)).pack(
            side="left", padx=6)
    ttk.Button(btns, text=cancel_label or i18n.t("dlg_close"),
               command=dlg.destroy).pack(side="left", padx=6)
    _center_on(parent, dlg)
    dlg.wait_window()
    return result["key"]


def notice_bar(parent: tk.Misc, message: str, action: str,
               command) -> ttk.Frame:
    """窓の上に出す**お知らせの帯**（押したときだけ何かが起きる）。

    なぜダイアログでないか（I-061・2026-08-07 ユーザー決定）
    ------------------------------------------------------
    プロジェクトを読み込んだとき、開いている窓の中身を**黙って入れ替えない**。
    かといってモーダルで問い直すと、読み込みの瞬間に窓の数だけ質問が並ぶ。
    ⇒ **帯で知らせ、押したときだけ取り込む**＝[[project-radiosim]] の凍結方式
    （窓は開いた時点の値で凍結し、見えている値で実行する）を壊さない形。

    ⛔ **既存の `↻ ランチャーから更新` に相乗りさせない**＝あちらは凍結帯
    （共通設定・案件情報）だけを触り、**利用者が入れた行・地点には手を出さない**。
    そこへ差し替えを足すと、「共通設定を更新するつもりで押したら行が消えた」
    という、この項目で直そうとしている事故を自分で作ることになる。

    帯は**閉じられる**（`×`）＝取り込まないという選択も 1 操作で終わる。
    ⚠️ 呼び出し側は返ってきた帯を保持し、取り込んだら `destroy()` すること
    （消し忘れると「取り込み済みなのに誘い続ける帯」が残る）。
    """
    bar = ttk.Frame(parent, padding=(8, 4))
    ttk.Label(bar, text=message).pack(side="left")
    ttk.Button(bar, text="×", width=2, cursor="hand2",
               command=bar.destroy).pack(side="right", padx=(6, 0))
    ttk.Button(bar, text=action, cursor="hand2",
               command=command).pack(side="right")
    return bar


def clear_notice(win: tk.Misc) -> None:
    """その窓のお知らせの帯を（あれば）消す。

    ⚠️ **帯を出す側と別に、消す口が要る**（2026-08-11・Codex 独立レビュー P1）＝
    `show_notice` の中でだけ古い帯を壊していたころは、**新しい読込に出す帯が無い
    ときに古い帯が残った**（プロジェクト A の帯を出したまま、その節を持たない B を
    読むと A の帯が居座り、押すと **A の中身が B へ入る**）。⇒ 消すのは「新しい帯を
    出すとき」ではなく「**前の話が終わったとき**」。
    """
    old = getattr(win, "_notice_bar", None)
    if old is not None and old.winfo_exists():
        old.destroy()
    if hasattr(win, "_notice_bar"):
        win._notice_bar = None      # type: ignore[attr-defined]


def show_notice(win: tk.Misc, message: str, action: str, command) -> None:
    """窓の**いちばん上**にお知らせの帯を出す（同じ窓に 2 本並べない）。

    帯は `win._notice_bar` に持たせる＝窓ごとに同じ 4 行を書き写さないため
    （3 窓に配ると必ずどれかで消し忘れる）。押したら `command` を呼び、
    **帯は自分で消える**（取り込み済みなのに誘い続ける帯を残さない）。
    """
    clear_notice(win)

    def _run() -> None:
        command()
        bar.destroy()
        win._notice_bar = None      # type: ignore[attr-defined]

    bar = notice_bar(win, message, action, _run)
    # 既存の中身より前に差し込む（`pack` は宣言順に積むので `before` が要る）。
    packed = [w for w in win.winfo_children()
              if w is not bar and w.winfo_manager() == "pack"]
    if packed:
        bar.pack(fill="x", side="top", before=packed[0])
    else:
        bar.pack(fill="x", side="top")
    win._notice_bar = bar           # type: ignore[attr-defined]
