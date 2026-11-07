"""
webui/edge.py
=============
Edge を**専用プロファイルのアプリモード**で開き、閉じたら戻る（試作 `b_edge_app/main.py`）。

⚠️ 既に同じプロファイルの Edge が開いていると、起こした親はすぐ終わって窓は別の Edge へ
渡される＝親の終わりでは閉じたと分からない。そこで `_HANDOFF_S` より早く親が終わったら、
窓の閉じはページの `/api/bye`（`WebServer.page_left`）で知る。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

_HANDOFF_S = 3.0
_EDGE_REL = os.path.join("Microsoft", "Edge", "Application", "msedge.exe")
_APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"


def find_edge() -> "str | None":
    """msedge.exe の場所。App Paths（HKLM→HKCU）→既定の置き場の順。無ければ None。"""
    try:
        import winreg
    except ImportError:                                  # Windows の外
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, _APP_PATHS) as k:
                path, _ = winreg.QueryValueEx(k, "")
        except OSError:
            continue
        if path and os.path.isfile(path):
            return path
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            path = os.path.join(base, _EDGE_REL)
            if os.path.isfile(path):
                return path
    return None


def edge_command(edge: str, url: str, profile_dir: str) -> "list[str]":
    return [
        edge, f"--app={url}", f"--user-data-dir={profile_dir}",
        "--no-first-run", "--no-default-browser-check", "--disable-sync",
        "--start-maximized",
    ]


def run_window(edge: str, url: str, profile_dir: str, closed: threading.Event) -> None:
    """窓を開き、閉じるまで待つ。`closed` はページの側の閉じ（`/api/bye`）で立つ。"""
    os.makedirs(profile_dir, exist_ok=True)
    proc = subprocess.Popen(edge_command(edge, url, profile_dir))
    started = time.monotonic()
    while not closed.wait(0.5):
        if proc.poll() is not None:
            break
    if not closed.is_set() and time.monotonic() - started <= _HANDOFF_S:
        closed.wait()                                    # 別の Edge へ渡された＝ページの閉じを待つ
