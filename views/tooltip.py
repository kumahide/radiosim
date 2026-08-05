"""
views/tooltip.py
================
マウスホバーで入力ヒントを出す軽量ツールチップ。

**窓に依存しない独立した部品**なので、Mixin ではなく普通のモジュールとして置く
（切り出しは 2.7 スライス A・本文は 1 文字も変えていない＝先頭の `_` を落として
公開名にしただけ）。
"""

import tkinter as tk

from views import theme


class Tooltip:
    """マウスホバーで入力ヒントを表示する軽量ツールチップ。"""

    _DELAY_MS = 600

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget   = widget
        self._text     = text
        self._tip: tk.Toplevel | None = None
        self._after_id: str | None    = None
        widget.bind("<Enter>",    self._schedule)
        widget.bind("<Leave>",    self._cancel)
        widget.bind("<FocusOut>", self._cancel)

    def _schedule(self, _=None) -> None:
        self._cancel()
        self._after_id = self._widget.after(self._DELAY_MS, self._show)

    def _show(self) -> None:
        if self._tip:
            return
        x = self._widget.winfo_rootx() + self._widget.winfo_width() // 2
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        # 配色は前景・背景をまとめて theme から取る。以前は bg だけ
        # "SystemButtonFace" 固定で、fg は sv_ttk の tk_setPalette に追従したため、
        # ダークで白文字×淡い背景（コントラスト 1.06:1）＝判読不能だった（B-008 の同型）。
        colors = theme.tooltip_options(self._widget)
        tk.Label(
            self._tip, text=self._text,
            background=colors["background"], foreground=colors["foreground"],
            relief="solid", borderwidth=1,
            font=theme.ui_font(self._widget, "small"), padx=5, pady=3,
        ).pack()

    def _cancel(self, _=None) -> None:
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip:
            self._tip.destroy()
            self._tip = None
