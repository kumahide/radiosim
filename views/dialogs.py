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

import i18n


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
