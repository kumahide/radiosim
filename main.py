"""
main.py
=======
アプリケーションエントリーポイント。
tkinter ループを起動するだけ。依存の組み立て（DI）もここで行う。
"""

# OS の証明書ストアを ssl モジュールに注入する（企業プロキシ環境の SSL エラー対策）
# 他の import より先に実行しなければならない
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import os
import sys
import threading
import tkinter as tk

import darkdetect
import sv_ttk

import i18n
import infrastructure as infra
from views.launcher import SimLauncher


class _ThemeManager:
    """system / light / dark の切替と darkdetect リスナーを管理する。"""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._mode = "system"
        self._listener_started = False

    def apply(self, mode: str) -> None:
        """mode: 'system' | 'light' | 'dark'"""
        self._mode = mode
        if mode == "system":
            sv_ttk.set_theme("dark" if darkdetect.isDark() else "light")
            if not self._listener_started:
                self._listener_started = True
                self._start_listener()
        else:
            sv_ttk.set_theme(mode)

    def _start_listener(self) -> None:
        def _cb(theme: str) -> None:
            if self._mode == "system":
                self._root.after(0, sv_ttk.set_theme, theme.lower())
        threading.Thread(target=darkdetect.listener, args=(_cb,), daemon=True).start()


def _setup_windows_platform() -> None:
    """DPI 対応とタスクバーグループ化（Windows のみ）。tk.Tk() より前に呼ぶこと。"""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor DPI Aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()   # Vista/7 フォールバック
        except Exception:
            pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "BearValleyCorp.RadioSimPro"
        )
    except Exception:
        pass


def _set_window_icon(root: tk.Tk) -> None:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(base, "icon.png")
    if not os.path.exists(icon_path):
        return
    try:
        from PIL import Image, ImageTk
        img = Image.open(icon_path)
        photo = ImageTk.PhotoImage(img)
        root.iconphoto(True, photo)  # type: ignore[arg-type]
        root._icon_photo = photo  # type: ignore[attr-defined]  # GC 対策
    except Exception:
        pass


def main() -> None:
    _setup_windows_platform()
    root = tk.Tk()
    _set_window_icon(root)
    manager = _ThemeManager(root)
    cfg = infra.load_config()
    i18n.set_lang(cfg.get("lang", "en"))
    manager.apply(cfg.get("theme", "system"))
    SimLauncher(root, manager.apply)
    root.mainloop()


if __name__ == "__main__":
    main()
