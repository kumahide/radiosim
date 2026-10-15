"""B-276 の実測＝画面が実行中に変わったとき、Tk の画面寸法は Win32 の実測に追従するか（依存 0）。

なぜ要るか
----------
B-276 は `views/window_fit.py` が**位置を Win32・大きさを Tk**（`winfo_screenwidth/height`）
から取っているために、実行中に画面が変わると 2 つが割れる、という欠陥（Tk 8.6 で踏んだ）。
Tk は**ディスプレイ接続を開いた時点**の値を持ち続ける、と読んでいた。Tk 9 でもそうかを
測らないと、直し方（対応案 1〜3）が決まらない。

測るもの（1 プロセス 1 インタプリタ）
------------------------------------
同じ瞬間の次の 3 つを並べる。

  * `tk`     ＝`winfo_screenwidth/height`（製品の `screen_size()` が返す値）
  * `win32`  ＝主モニタの矩形（`MonitorFromPoint(0,0)` → `GetMonitorInfo`＝製品の
               `_enumerate_monitors` と同じ API）
  * `metric` ＝`GetSystemMetrics(SM_CXSCREEN/SM_CYSCREEN)`

`win32` が変わってから 2 秒置いて判定を出す。

モード（`--mode`）＝**変更の瞬間に Tk が何をしていたか**（B-276 の仮説＝赤の回はテストの
プロセスが画面の変化の後に Tk のイベントを回していなかった、を確かめる）

  * `loop`（既定）＝メインループを回したまま 200ms ごとに採る（2026-09-27 の 4 本はこれ）。
    🔴 **判定まで採取を続け、割れた標本を数える**（B-321＝以前は変化を見つけた標本で採取を
    やめていた＝「割れは変更直後の 1 標本だけ」はその結果で、測っていなかった）。
    さらに**同じプロセスで新しく建てた `Tk()`** の値も並べる。
  * `idle`＝ルートは在るが、変更をまたいでイベントを 1 度も回さない（`time.sleep` で待つ）。
    2 秒後に**回す前**の値を読み、`update()` を 1 度回して**回した後**の値も読む。
  * `none`＝変更の瞬間に **Tk のウィンドウが 1 つも無い**（最初のルートは壊してある）。
    2 秒後に新しく `Tk()` を建てて**すぐ**読む（＝`tests/test_window_fit.py` の
    `test_the_primary_monitor_agrees_with_what_tk_reports` と同じ読み方）、`update()`
    の後、さらに 2 秒回した後も読む。

DPI 認識は 2 通り＝既定（非認識＝B-276 を踏んだテストの状態）と `--aware`
（per-monitor v2＝製品と同じ）。⚠️ **表示スケールを変えて寸法が動くのは非認識の側だけ**
（認識する側は物理ピクセルで見るので、スケールでは寸法が変わらない）。認識する側を
動かすには解像度そのもの（リモートデスクトップの窓の寸法など）を変える。

実行::

    & "$env:RADIOSIM_PYTHON" experiments/b276_screen_cache_probe.py --mode loop
    & "$env:RADIOSIM_PYTHON" experiments/b276_screen_cache_probe.py --mode idle
    & "$env:RADIOSIM_PYTHON" experiments/b276_screen_cache_probe.py --mode none

⚠️ **何本同時に起動してもよい**（ウィンドウを動かさない）＝人の操作 1 回で
モード × Tk 8.6 / 9 × 認識 2 通りを採れる。10 分で自分で終わる。
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
_STEP_S = 0.2
_SETTLE_S = 2.0
_MODES = ("loop", "idle", "none")


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


def sample(win: "tk.Misc | None") -> "dict[str, tuple[int, int] | None]":
    return {"tk": tk_size(win) if win is not None else None,
            "win32": win32_primary(), "metric": metric()}


def agree(label: str, tk_value: "tuple[int, int]", win32: "tuple[int, int]") -> None:
    mark = "✅ 一致" if tk_value == win32 else "⛔ 割れた（B-276 の形）"
    print(f"  {label}\n      tk={tk_value}  win32={win32}  {mark}", flush=True)


def make_root(awareness: str, mode: str) -> tk.Tk:
    root = tk.Tk()
    patch = root.tk.call("info", "patchlevel")
    root.title(f"B-276 {mode} Tk {patch} {awareness}")
    root.geometry("440x120+40+40")
    tk.Label(root, text=f"{mode} / Tk {patch} / {awareness}\n"
                        "画面の寸法かスケールを 1 回変えてください").pack(pady=30)
    return root


def wait_for_change(first_win32: "tuple[int, int]", win: "tk.Misc | None") -> bool:
    """**Tk のイベントを 1 度も回さずに** Win32 の寸法が変わるまで待つ（`idle`・`none`）。"""
    last = None
    while (time.perf_counter() - T0) * 1000 <= _LIMIT_MS:
        now = sample(win)
        if now != last:
            log("SEEN", f"tk={now['tk']}  win32={now['win32']}  metric={now['metric']}")
            last = now
        if now["win32"] != first_win32:
            return True
        time.sleep(_STEP_S)
    log("END", "10 分のあいだ Win32 の寸法が変わらなかった＝この巡は無効")
    return False


def run_loop(root: tk.Tk, first: dict, header: str) -> None:
    state: dict = {"changed_at": None, "last": None, "samples": 0, "split": 0,
                   "last_split_ms": None}

    def poll() -> None:
        now = sample(root)
        if now != state["last"]:
            log("SEEN", f"tk={now['tk']}  win32={now['win32']}  metric={now['metric']}")
            state["last"] = now
        if state["changed_at"] is None and now["win32"] != first["win32"]:
            state["changed_at"] = time.perf_counter()
            root.after(int(_SETTLE_S * 1000), verdict)
        if state["changed_at"] is not None:
            # 🔴 判定まで採取を続ける（B-321）＝割れが何標本・何 ms 続いたかを数える。
            state["samples"] += 1
            if now["tk"] != now["win32"]:
                state["split"] += 1
                state["last_split_ms"] = (time.perf_counter() - state["changed_at"]) * 1000
        elif (time.perf_counter() - T0) * 1000 > _LIMIT_MS:
            log("END", "10 分のあいだ Win32 の寸法が変わらなかった＝この巡は無効")
            root.destroy()
            return
        state["poll"] = root.after(int(_STEP_S * 1000), poll)

    def verdict() -> None:
        root.after_cancel(state["poll"])
        root.update()
        after = sample(root)
        fresh = tk.Tk()
        fresh.withdraw()
        fresh_size = tk_size(fresh)
        fresh.destroy()
        print(header, flush=True)
        print(f"  変更前  tk={first['tk']}  win32={first['win32']}", flush=True)
        print(f"  変更後 {_SETTLE_S:.0f} 秒の採取＝{state['samples']} 標本のうち割れた標本 "
              f"{state['split']}"
              + (f"（最後に割れていたのは変化を見つけてから "
                 f"{state['last_split_ms']:.0f}ms）" if state["split"] else ""),
              flush=True)
        agree(f"{_SETTLE_S:.0f} 秒後の最初のルート", after["tk"], after["win32"])
        agree("同じプロセスで新しく建てた Tk()", fresh_size, after["win32"])
        root.destroy()

    poll()
    root.mainloop()


def main() -> None:
    mode = "loop"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    if mode not in _MODES:
        raise SystemExit(f"--mode は {'／'.join(_MODES)} のどれか（受け取ったのは {mode!r}）")
    awareness = set_awareness("--aware" in sys.argv)
    root = make_root(awareness, mode)
    patch = root.tk.call("info", "patchlevel")
    first = sample(root)
    log("INIT", f"python={sys.version.split()[0]}  Tk={patch}  dpi={awareness}  mode={mode}  "
                f"remote={ctypes.windll.user32.GetSystemMetrics(0x1000)}")
    log("INIT", f"変更前  tk={first['tk']}  win32={first['win32']}  metric={first['metric']}")
    header = f"\n=== B-276 判定 ===\nTk {patch} / {awareness} / mode={mode}"

    if mode == "loop":
        run_loop(root, first, header)
        return

    root.update()                                 # ウィンドウを出し切ってから黙る
    if mode == "none":
        root.destroy()                            # 変更の瞬間に Tk のウィンドウを 1 つも残さない
        log("INIT", "ルートを壊した＝ここから変更まで Tk のウィンドウは無い")
        if not wait_for_change(first["win32"], None):
            return
    else:
        log("INIT", "ここから変更まで Tk のイベントを 1 度も回さない")
        if not wait_for_change(first["win32"], root):
            root.destroy()
            return
    time.sleep(_SETTLE_S)                         # ここでも回さない
    if mode == "none":
        root = tk.Tk()                            # テストの `make_themed_root()` と同じ瞬間
        root.withdraw()
    before = sample(root)
    root.update()
    after_one = sample(root)
    end = time.perf_counter() + _SETTLE_S
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.02)
    after_pump = sample(root)
    root.destroy()
    print(header, flush=True)
    print(f"  変更前  tk={first['tk']}  win32={first['win32']}", flush=True)
    label = "建てた直後（テストと同じ読み方）" if mode == "none" else "回す前（2 秒黙った後）"
    agree(label, before["tk"], before["win32"])
    agree("update() を 1 度回した後", after_one["tk"], after_one["win32"])
    agree(f"さらに {_SETTLE_S:.0f} 秒回した後", after_pump["tk"], after_pump["win32"])


if __name__ == "__main__":
    main()
