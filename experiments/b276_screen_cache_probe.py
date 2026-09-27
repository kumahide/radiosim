"""B-276 の実測＝画面が実行中に変わったとき、Tk の画面寸法は Win32 の実測に追従するか（依存 0）。

なぜ要るか
----------
B-276 は `views/window_fit.py` が**位置を Win32・大きさを Tk**（`winfo_screenwidth/height`）
から取っているために、実行中に画面が変わると 2 つが割れる、という欠陥（Tk 8.6 で踏んだ）。
Tk は**ディスプレイ接続を開いた時点**の値を持ち続ける、と読んでいる。Tk 9 でもそうかを
測らないと、直し方（対応案 1〜3）が決まらない。

測るもの（1 プロセス 1 インタプリタ）
------------------------------------
200ms ごとに、同じ瞬間の次の 3 つを並べる。

  * `tk`     ＝`winfo_screenwidth/height`（製品の `screen_size()` が返す値）
  * `win32`  ＝主モニタの矩形（`MonitorFromPoint(0,0)` → `GetMonitorInfo`＝製品の
               `_enumerate_monitors` と同じ API）
  * `metric` ＝`GetSystemMetrics(SM_CXSCREEN/SM_CYSCREEN)`

`win32` が変わったら 2 秒置いて判定を出し、さらに**同じプロセスで新しく建てた `Tk()`**
の値も並べる（「以後のルートも最初の値を見る」かどうか＝B-276 の原因欄の確かめ）。

DPI 認識は 2 通り＝既定（非認識＝B-276 を踏んだテストの状態）と `--aware`
（per-monitor v2＝製品と同じ）。⚠️ **表示スケールを変えて寸法が動くのは非認識の側だけ**
（認識する側は物理ピクセルで見るので、スケールでは寸法が変わらない）。認識する側を
動かすには解像度そのもの（リモートデスクトップの窓の寸法など）を変える。

実行::

    & "D:/tools/py315rc/python.exe" experiments/b276_screen_cache_probe.py
    & "$env:RADIOSIM_PYTHON" experiments/b276_screen_cache_probe.py --aware

⚠️ **何本同時に起動してもよい**（ウィンドウを動かさない）＝人の操作 1 回で
Tk 8.6 / 9 × 認識 2 通りを採れる。10 分で自分で終わる。
"""
from __future__ import annotations

import ctypes
import sys
import time
import tkinter as tk
from ctypes import wintypes

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    except Exception:
        pass

T0 = time.perf_counter()
_LIMIT_MS = 10 * 60 * 1000


def log(tag: str, msg: str) -> None:
    print(f"{(time.perf_counter() - T0) * 1000:9.0f}ms  {tag:<6} {msg}", flush=True)


def set_awareness(aware: bool) -> str:
    if not aware:
        return "unaware"
    user32 = ctypes.windll.user32
    user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    return "per-monitor-v2" if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)) else "failed"


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def win32_primary() -> "tuple[int, int]":
    user32 = ctypes.windll.user32
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    mon = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)     # MONITOR_DEFAULTTOPRIMARY
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        return (0, 0)
    r = info.rcMonitor
    return (r.right - r.left, r.bottom - r.top)


def metric() -> "tuple[int, int]":
    user32 = ctypes.windll.user32
    return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def tk_size(win: "tk.Misc") -> "tuple[int, int]":
    return (win.winfo_screenwidth(), win.winfo_screenheight())


def main() -> None:
    awareness = set_awareness("--aware" in sys.argv)
    root = tk.Tk()
    patch = root.tk.call("info", "patchlevel")
    root.title(f"B-276 Tk {patch} {awareness}")
    root.geometry("420x120+40+40")
    tk.Label(root, text=f"Tk {patch} / {awareness}\n画面の寸法かスケールを 1 回変えてください").pack(pady=30)
    first = {"tk": tk_size(root), "win32": win32_primary(), "metric": metric()}
    log("INIT", f"python={sys.version.split()[0]}  Tk={patch}  dpi={awareness}  "
                f"remote={ctypes.windll.user32.GetSystemMetrics(0x1000)}")
    log("INIT", f"変更前  tk={first['tk']}  win32={first['win32']}  metric={first['metric']}")
    state = {"changed_at": None, "last": None}

    def poll() -> None:
        now = {"tk": tk_size(root), "win32": win32_primary(), "metric": metric()}
        if now != state["last"]:
            log("SEEN", f"tk={now['tk']}  win32={now['win32']}  metric={now['metric']}")
            state["last"] = now
        if state["changed_at"] is None and now["win32"] != first["win32"]:
            state["changed_at"] = time.perf_counter()
            root.after(2000, verdict)
            return
        if (time.perf_counter() - T0) * 1000 > _LIMIT_MS:
            log("END", "10 分のあいだ Win32 の寸法が変わらなかった＝この巡は無効")
            root.destroy()
            return
        root.after(200, poll)

    def verdict() -> None:
        root.update()
        after = {"tk": tk_size(root), "win32": win32_primary(), "metric": metric()}
        fresh = tk.Tk()
        fresh.withdraw()
        fresh_size = tk_size(fresh)
        fresh.destroy()
        print("\n=== B-276 判定 ===", flush=True)
        print(f"Tk {patch} / {awareness}", flush=True)
        print(f"  変更前  tk={first['tk']}  win32={first['win32']}", flush=True)
        print(f"  変更後  tk={after['tk']}  win32={after['win32']}  metric={after['metric']}", flush=True)
        print(f"  同じプロセスで新しく建てた Tk()  tk={fresh_size}", flush=True)
        if after["tk"] == after["win32"]:
            print("  ✅ 追従した＝最初のルートの Tk の値が Win32 の実測と一致", flush=True)
        else:
            print("  ⛔ 割れた＝最初のルートの Tk は古い値のまま（B-276 の形）", flush=True)
        print("  新しいルート: " + ("✅ 新しい値" if fresh_size == after["win32"] else "⛔ 古い値のまま"), flush=True)
        root.destroy()

    poll()
    root.mainloop()


if __name__ == "__main__":
    main()
