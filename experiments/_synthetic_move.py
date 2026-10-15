"""探り針がウィンドウを**移動モーダルループに入れて動かす**ための合成入力（依存 0）。

`b119_resizable_windows_probe.py` と `tk9_dpi_probe.py` が同じものを別々に持っていて、
片方だけ直された（B-320＝2026-09-27 の独立レビューが指摘）。⇒ 1 か所にまとめた。

🔴 **キーを送る前ごとに前面を確かめる**（B-320）＝`SC_MOVE` を送った後の矢印と、確定の
Enter は `keybd_event`＝**前面にいるウィンドウへ入る**。最初に 1 回確かめるだけでは、
送っている約 3 秒のあいだに別のアプリが前面になると、そのアプリへキーが入る。
⇒ 1 つ送るたびに `GetForegroundWindow()` を見て、外れたらそこでやめる。確定の Enter も
**対象が前面のときだけ**送る（外れた時点で移動ループは OS が解いている＝送る先が無い）。

⚠️ **Tk のスレッドから呼ばない**＝移動ループの間 Tk の `after` は動けない。Tk の API は
ここから 1 つも触らない（ハンドルは呼ぶ前に測って渡す）。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

_WM_SYSCOMMAND, _SC_MOVE = 0x0112, 0xF010
VK_LEFT, VK_RIGHT, VK_RETURN = 0x25, 0x27, 0x0D
_KEYUP = 0x0002


def foreground() -> int:
    """いま前面にいるウィンドウのハンドル（無ければ 0）。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    return int(user32.GetForegroundWindow() or 0)


def bring_to_front(hwnd: int) -> bool:
    """`hwnd` を前面にし、**本当に前面になったか**を返す。

    🔴 **`SetForegroundWindow` だけでは取れない**（2026-09-27・リレーから起こした
    セッションで実測）＝前面にいるのが別のプロセスだと、Windows の前面ロックで戻り値 0 の
    まま何も起きない。そのまま `SC_MOVE` とキーを送ると、移動ループに入らず
    （b119 の自己検査の②で `Toplevel` が 0px）、**矢印と Enter は前面の別のアプリへ入る**。
    ⇒ 前面のウィンドウのスレッドと入力をつないでから呼ぶ（`AttachThreadInput`＝同じ
    実測で取れた）。つないだ入力は必ず外す。
    """
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    other = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other and other != me and user32.AttachThreadInput(me, other, True))
    try:
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, other, False)
    return foreground() == int(hwnd)


def tap(vk: int) -> None:
    """キーを 1 回押して離す（前面のウィンドウへ入る）。"""
    import ctypes
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, _KEYUP, 0)


def move_window(hwnd: int, seconds: float = 3.0, *,
                is_front: Optional[Callable[[], bool]] = None,
                send_key: Callable[[int], None] = tap,
                post_move: Optional[Callable[[], None]] = None,
                log: Callable[[str], None] = lambda _msg: None) -> int:
    """`hwnd` を移動ループに入れ、左右へ往復させて Enter で確定する。送ったキーの数を返す。

    `is_front`・`send_key`・`post_move` は差し替え口＝自己検査が本物のキーを 1 つも
    送らずに「前面を外れたら止まる」を確かめるため（既定は本物の Win32）。

    ⛔ **前面を取れなければ 1 つも送らない**・**途中で外れたらそこでやめる**（モジュールの註）。
    """
    if is_front is None:
        if not bring_to_front(hwnd):
            log("前面を取れなかった＝キーを送らない")
            return 0
        is_front = lambda: foreground() == int(hwnd)    # noqa: E731
    if post_move is None:
        import ctypes
        post_move = lambda: ctypes.windll.user32.PostMessageW(   # noqa: E731
            hwnd, _WM_SYSCOMMAND, _SC_MOVE, 0)
    time.sleep(0.15)
    post_move()
    time.sleep(0.25)
    sent = 0
    for i in range(max(int(seconds / 0.04), 1)):
        if not is_front():
            log(f"前面が外れた＝{sent} 個で送るのをやめた（Enter も送らない）")
            return sent
        # 往復させる＝画面外へ出さず、かつ**動き続ける**（止まると WM の
        # 再適用も止まり、観測のウィンドウが閉じる）。
        send_key(VK_RIGHT if (i // 12) % 2 == 0 else VK_LEFT)
        sent += 1
        time.sleep(0.04)
    if is_front():
        send_key(VK_RETURN)                  # 移動を確定して抜ける
        sent += 1
    else:
        log("確定の直前に前面が外れた＝Enter を送らない")
    return sent
