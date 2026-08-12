"""tools/gui_shots.py — 窓を起こして PNG に撮る（**画面を見られない環境用**）。

なぜ要るか
----------
2026-08-07、会社から VS Code のトンネルで自宅 PC を操作していて、GUI を目視でき
なかった。`CopyFromScreen`（画面キャプチャ）は **セッションがロックされていると
真っ黒**になるので使えない。⇒ `PrintWindow` は「窓よ、この DC へ自分を描け」と
頼む方式なので、**画面に映っていなくても中身が取れる**（実測で成功）。

これで撮った PNG は VS Code の中でそのまま開けるので、トンネル越しに目視できる。

⛔ **これは「実機確認」ではない**
--------------------------------
撮れるのは**開発機の解像度・DPI での見た目**であって、実機（会社の AVD ＝
1920×1080 / 100%・使える高さ 990px）の見切れは**この方法では証明できない**
（[[project-real-world-env-vdi]]）。見切れの門は `tests/test_window_fit.py`。
⇒ 用途は「**語・並び・レイアウトの形が意図どおりか**」の目視確認まで。

使い方
------
    $env:RADIOSIM_PYTHON で declare した python から:
    python tools/gui_shots.py [出力ディレクトリ]

既定の出力先は `issue_evidence/gui_shots_<日付>/`（git 管理外）。
"""

import ctypes
import datetime as _dt
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

import win32gui
import win32ui
from PIL import Image

from core import i18n

PW_RENDERFULLCONTENT = 0x00000002


def capture(win, path: str) -> str:
    """Toplevel（または Tk ルート）を PNG へ。戻り値は実測サイズの説明。

    ⚠️ **`winfo_id()` ではなく `wm_frame()`** を使う＝前者は Tk の中身の窓で、
    タイトルバーと枠が入らない。実機のスクショと突き合わせるときに、
    **Tk の geometry がメニューバーを含まない**のと同じ型の食い違いになる。
    """
    ctypes.windll.user32.SetProcessDPIAware()
    hwnd = int(win.wm_frame(), 16)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    dc  = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(dc)
    mem = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    mem.SelectObject(bmp)
    ok = ctypes.windll.user32.PrintWindow(hwnd, mem.GetSafeHdc(),
                                          PW_RENDERFULLCONTENT)
    info = bmp.GetInfo()
    Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                     bmp.GetBitmapBits(True), "raw", "BGRX", 0, 1).save(path)
    win32gui.DeleteObject(bmp.GetHandle())
    mem.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(hwnd, dc)
    if not ok:
        return f"{w}x{h} ⚠️ PrintWindow が失敗（真っ黒かもしれない）"
    return f"{w}x{h}"


def _settle(win, seconds: float = 0.5) -> None:
    """描画が落ち着くまで待つ（撮るのが早すぎると中身が空で写る）。"""
    win.update()
    time.sleep(seconds)
    win.update()


def main(outdir: str) -> int:
    os.makedirs(outdir, exist_ok=True)
    from conftest import make_themed_root
    from views.launcher import SimLauncher

    i18n.set_lang("ja")
    root = make_themed_root()
    app  = SimLauncher(root, lambda _t: None)

    shots = []
    try:
        _settle(root)
        shots.append(("01_launcher", root))

        batch = app.ensure_batch_window()
        _settle(batch)
        shots.append(("02_multiple_paths", batch))

        app._on_open_scenario()
        _settle(app._scenario_win)
        shots.append(("03_scenario", app._scenario_win))

        app._on_open_multihop()
        _settle(app._multihop_win)
        shots.append(("04_multihop", app._multihop_win))

        for name, win in shots:
            path = os.path.join(outdir, f"{name}.png")
            print(f"{name:20s} {capture(win, path)}  -> {path}")
            # 中身の必要量も併記する＝見た目と「入りきるか」は別の問い。
            print(f"{'':20s} reqsize={win.winfo_reqwidth()}x"
                  f"{win.winfo_reqheight()}px（実機の高さ予算は 990px）")
    finally:
        root.destroy()
    return 0


if __name__ == "__main__":
    default = os.path.join(
        os.path.dirname(__file__), "..", "issue_evidence",
        f"gui_shots_{_dt.date.today():%Y-%m-%d}")
    sys.exit(main(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else default)))
