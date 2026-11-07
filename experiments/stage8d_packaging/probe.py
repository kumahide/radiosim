"""新しい画面の起動の時刻を測る（4.0 ステージ8d・PyInstaller と embeddable で同じものを走らせる）。

プロセス生成→Edge を起こす→ページが状態を取りに来る→最初の判定、の時刻（ミリ秒）と、
ページが来た時点で通信の部品（`requests`・`urllib3`・`truststore`・`ssl`）が読まれて
いるか（I-232 の残り ①＝配布の形でも起動の経路で読まないか）を 1 行の JSON で書く。

書き先は環境変数 `RADIOSIM_STARTUP_PROBE` のパス（`--noconsole` の exe には stdout が無い）。
⚠️ 入口が読んだ後で差し替える（先に `webui.server` を読むと core ごと読まれて値が汚れる）。
窓は測り終えたら閉じの印を立て、Edge の木ごと落とす。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess  # nosec B404 ─ Edge の木を落とすだけ
import sys
import threading
import time

T_WALL0 = time.time()
T0 = time.perf_counter()
_NET = ("requests", "urllib3", "truststore", "ssl")


def _proc_created_wall() -> float:
    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
    c, e, kk, u = (wt.FILETIME() for _ in range(4))
    k32.GetProcessTimes(k32.GetCurrentProcess(), ctypes.byref(c), ctypes.byref(e),
                        ctypes.byref(kk), ctypes.byref(u))
    ft = (c.dwHighDateTime << 32) | c.dwLowDateTime
    return ft / 1e7 - 11644473600.0


marks: dict = {}
procs: list = []
closed_ev: list = []
net_at_page: list = []
done = threading.Event()


def mark(name: str) -> None:
    marks.setdefault(name, round(1000 * (time.perf_counter() - T0), 1))


def run() -> int:
    import webui.__main__ as m
    from webui import edge

    popen = subprocess.Popen

    def rec_popen(*a, **k):
        mark("edge_popen")
        p = popen(*a, **k)
        procs.append(p)
        return p

    edge.subprocess.Popen = rec_popen  # type: ignore[attr-defined]
    wait = edge.wait_window

    def rec_wait(win, closed):
        closed_ev.append(closed)
        return wait(win, closed)

    edge.wait_window = rec_wait

    def late_patch() -> None:
        while "webui.state" not in sys.modules or not hasattr(sys.modules["webui.state"], "AppState"):
            time.sleep(0.0005)
        stt = sys.modules["webui.state"]
        pub = stt.AppState.publish

        def rec_pub(self, version, kind, **f):
            mark(f"first_{kind}")
            return pub(self, version, kind, **f)

        stt.AppState.publish = rec_pub
        while "webui.server" not in sys.modules or not hasattr(sys.modules["webui.server"], "WebServer"):
            time.sleep(0.0005)
        srv = sys.modules["webui.server"]
        seen = srv.WebServer.page_seen

        def rec_seen(self):
            mark("page_state")
            if not net_at_page:
                net_at_page.append([n for n in _NET if n in sys.modules])
            done.set()
            return seen(self)

        srv.WebServer.page_seen = rec_seen

    def closer() -> None:
        done.wait(30)
        time.sleep(0.3)
        for ev in closed_ev:
            ev.set()
        for p in procs:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],  # nosec B603 B607
                           capture_output=True)

    threading.Thread(target=late_patch, daemon=True).start()
    threading.Thread(target=closer, daemon=True).start()
    rc = m.main()
    out = {"interp_ms": round(1000 * (T_WALL0 - _proc_created_wall()), 1), **marks,
           "net_loaded_at_page": net_at_page[0] if net_at_page else None,
           "frozen": bool(getattr(sys, "frozen", False)), "rc": rc}
    with open(os.environ["RADIOSIM_STARTUP_PROBE"], "a", encoding="utf-8") as f:
        f.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(run())
