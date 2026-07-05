"""
main.py
=======
アプリケーションエントリーポイント。
tkinter ループを起動するだけ。依存の組み立て（DI）もここで行う。
"""

# --- 起動プロファイラ（環境変数 RADIOSIM_PROFILE が真のときのみ作動）-----------
# 本番では import time とゼロコストの no-op になるよう設計。バイナリ起動の
# 各フェーズ実時間を radiosim_profile.log に追記する。詳細計測用の一時計装。
import os as _os
import time as _time

_PROF_ON = bool(_os.environ.get("RADIOSIM_PROFILE"))
_PROF_T0 = _time.perf_counter()
_PROF_MARKS: list = []


def _prof(label: str) -> None:
    if _PROF_ON:
        _PROF_MARKS.append((label, _time.perf_counter() - _PROF_T0))


def _prof_bootloader_seconds() -> float:
    """プロセス生成〜本モジュール開始（ブートローダ＋インタプリタ起動）の秒数。

    Windows では GetProcessTimes でプロセス生成 FILETIME を取得し、現在時刻と
    比較して算出する。取得不可なら -1.0 を返す。"""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        # x64 で擬似ハンドル(-1)が 32bit に切り詰められないよう戻り値/引数型を明示
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.GetProcessTimes.argtypes = (
            [ctypes.c_void_p] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        )
        creation = wintypes.FILETIME()
        exit_ = wintypes.FILETIME()
        kernel_ = wintypes.FILETIME()
        user_ = wintypes.FILETIME()
        h = k32.GetCurrentProcess()
        if not k32.GetProcessTimes(
            h, ctypes.byref(creation), ctypes.byref(exit_),
            ctypes.byref(kernel_), ctypes.byref(user_)
        ):
            return -1.0
        # FILETIME(100ns since 1601-01-01) → Unix epoch 秒
        ft = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        created_unix = ft / 1e7 - 11644473600.0
        return _time.time() - created_unix
    except Exception:
        return -1.0


def _prof_flush() -> None:
    if not _PROF_ON:
        return
    boot = _prof_bootloader_seconds()
    lines = ["=== RadioSim startup profile ==="]
    if boot >= 0:
        lines.append(f"  bootloader+interp (proc start -> module start): {boot:7.3f}s")
    prev = 0.0
    for label, t in _PROF_MARKS:
        lines.append(f"  {label:<32} cum={t:7.3f}s  (+{t - prev:6.3f}s)")
        prev = t
    text = "\n".join(lines) + "\n"
    try:
        base = _os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                                else _os.path.abspath(__file__))
        with open(_os.path.join(base, "radiosim_profile.log"), "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    print(text)


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
import config
from views.launcher import SimLauncher

_prof("top-level imports done")


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
    _prof("main() enter")
    _setup_windows_platform()
    root = tk.Tk()
    _prof("tk.Tk() created")
    _set_window_icon(root)
    manager = _ThemeManager(root)
    cfg = config.load_config()
    i18n.set_lang(cfg.get("lang", "en"))
    _prof("config/i18n done")
    manager.apply(cfg.get("theme", "system"))
    _prof("sv-ttk theme applied")
    SimLauncher(root, manager.apply)
    _prof("SimLauncher built")
    if _PROF_ON:
        root.update()  # 初回描画を強制してレイアウト/ペイント時間を計測に含める
        _prof("first paint (update)")
        _prof_flush()
    root.mainloop()


if __name__ == "__main__":
    main()
