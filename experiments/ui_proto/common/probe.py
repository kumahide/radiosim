"""動いている機械の事実を集める＝会社 PC ではスクショ 1 枚で読むためのもの。

経営者は VDI でスクリプトを回せないので、値はアプリの画面に出す。
⛔ プロキシは有無と種類だけ＝接続先の名前はスクショに写さない。
"""
from __future__ import annotations

import ctypes
import os
import platform
import sys
import time
import winreg
from ctypes import wintypes

_WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
_SM_REMOTESESSION = 0x1000
_SPI_GETWORKAREA = 0x0030

#: このプロセスの Python が走り始めた時刻＝なるべく早く読む。
PY_START = time.time()


def process_start_epoch() -> float:
    """プロセスの生成時刻（OS が持つ値）＝exe を叩いた時刻に最も近い。"""
    created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    ok = kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), ctypes.byref(created),
                                  ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user))
    if not ok:
        return PY_START
    ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
    return ticks / 10_000_000 - 11_644_473_600


def _read_reg(root: int, path: str, name: str):
    try:
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def webview2_version() -> str | None:
    """WebView2 Runtime の版＝無ければ None（配布の案内を決める事実）。"""
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_GUID}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_GUID}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2_GUID}"),
    ]
    for root, path in keys:
        value = _read_reg(root, path, "pv")
        if value and value != "0.0.0.0":  # nosec B104 ─ 未導入の印の版の字と比べるだけ（待ち受けではない）
            return str(value)
    return None


def find_edge() -> str | None:
    """msedge.exe の場所。App Paths を見てから既定の置き場を見る。"""
    app_paths = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        value = _read_reg(root, app_paths, "")
        if value and os.path.isfile(str(value)):
            return str(value)
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.environ.get("LOCALAPPDATA")):
        if base:
            candidate = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.isfile(candidate):
                return candidate
    return None


def proxy_kind() -> str:
    """プロキシの有無と種類（固定・自動構成・環境変数）。接続先は返さない。"""
    settings = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    found = []
    if _read_reg(winreg.HKEY_CURRENT_USER, settings, "ProxyEnable") == 1:
        found.append("固定")
    if _read_reg(winreg.HKEY_CURRENT_USER, settings, "AutoConfigURL"):
        found.append("自動構成")
    if any(os.environ.get(name) for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")):
        found.append("環境変数")
    return "・".join(found) if found else "なし"


def work_area() -> tuple[int, int]:
    """OS の作業領域の幅と高さ（タスクバーを除く）。"""
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(_SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
    return rect.right - rect.left, rect.bottom - rect.top


def refresh_hz() -> int:
    """主画面の更新の間隔（Hz）。リモートデスクトップでは 30 前後＝Web の描画の上限になる。"""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    hdc = user32.GetDC(None)
    try:
        return int(gdi32.GetDeviceCaps(hdc, 116))  # VREFRESH
    finally:
        user32.ReleaseDC(None, hdc)


def launch_count(candidate: str) -> int:
    """この候補を今の機械で起動した回数＝初回と 2 回目を画面で区別する。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "RadioSimUiProto", f"launch_{candidate}.txt")
    try:
        with open(path, encoding="ascii") as f:
            count = int(f.read().strip() or 0)
    except (OSError, ValueError):
        count = 0
    count += 1
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="ascii") as f:
            f.write(str(count))
    except OSError:
        pass
    return count


def facts(candidate: str, launch: int) -> dict:
    """画面の測定欄へ出す事実（ブラウザーの外から見えるもの）。"""
    area_w, area_h = work_area()
    edge = find_edge()
    return {
        "candidate": candidate,
        "launch": launch,
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": platform.python_version(),
        "windows": platform.version(),
        "remote_session": bool(ctypes.windll.user32.GetSystemMetrics(_SM_REMOTESESSION)),
        "session_name": os.environ.get("SESSIONNAME", ""),
        "webview2": webview2_version(),
        "edge_found": bool(edge),
        "proxy": proxy_kind(),
        "work_area": f"{area_w}×{area_h}",
        "refresh_hz": refresh_hz(),
    }
