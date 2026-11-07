"""
webui/edge.py
=============
Edge を**専用プロファイルのアプリモード**で開き、閉じたら戻る（試作 `b_edge_app/main.py`）。

⚠️ 既に同じプロファイルの Edge が開いていると、起こした親はすぐ終わって窓は別の Edge へ
渡される＝親の終わりでは閉じたと分からない。そこで `_HANDOFF_S` より早く親が終わったら、
窓の閉じはページの `/api/bye`（`WebServer.page_left`）で知る。

起こす（`open_window`）と待つ（`wait_window`）を分けてある＝入口は core を読む前に窓を
起こし、読み込みと Edge の立ち上がりを重ねる（`webui/__main__.py`・I-232）。
"""

from __future__ import annotations

import os
import secrets
import socket
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


class Listener:
    """`127.0.0.1` の空きのポートで**待ち始めた**受け口と、起動ごとの鍵。

    core を読む前に作れる（標準ライブラリだけ）＝入口はこれの URL で先に Edge を起こし、
    サーバー（`WebServer`）が応え始めるまでの接続は待ち行列で待たせる。
    """

    def __init__(self) -> None:
        self.sock = socket.create_server(("127.0.0.1", 0))
        host, port = self.sock.getsockname()[:2]
        self.address: "tuple[str, int]" = (host, port)
        self.token = secrets.token_urlsafe(24)

    @property
    def url(self) -> str:
        """Edge に渡す URL（鍵は断片＝サーバーへは送られない・JS が読む）。"""
        return f"http://127.0.0.1:{self.address[1]}/#{self.token}"


class Window:
    """起こした Edge の親プロセスと、起こした時刻（渡しの見分けに使う）。"""

    def __init__(self, proc: "subprocess.Popen[bytes]") -> None:
        self.proc = proc
        self.started = time.monotonic()


def open_window(edge: str, url: str, profile_dir: str) -> Window:
    """窓を起こすだけ（待たない）＝サーバーが応え始める前に起こしてよい（接続は待ち行列で待つ）。"""
    os.makedirs(profile_dir, exist_ok=True)
    return Window(subprocess.Popen(edge_command(edge, url, profile_dir)))


def wait_window(win: Window, closed: threading.Event) -> None:
    """窓が閉じるまで待つ。`closed` はページの側の閉じ（`/api/bye`）で立つ。"""
    proc, started = win.proc, win.started
    while not closed.wait(0.5):
        if proc.poll() is not None:
            break
    if not closed.is_set() and time.monotonic() - started <= _HANDOFF_S:
        closed.wait()                                    # 別の Edge へ渡された＝ページの閉じを待つ
