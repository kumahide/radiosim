"""Tk 9 で **③ フォント追従**と **B-119** が消えるかを測る（依存 0）。

なぜ要るか
----------
Tk 9 移行の再判断トリガーは 3 つあり（[[project-tk9-migration]]）、①（CPython 3.15
正式版）は 2026-10-01 に日付で発火する。残る **②依存 3 件**と**③モニタ間の
フォント追従**は「スパイクで実測・机上判断にしない」と決めたまま、一度も走らせて
いなかった。

🔑 **この探針はサードパーティを 1 つも import しない**＝②の結果（cp315 の wheel が
在るか）に足を取られずに③を測るため。**②が全滅しても③の答えは出る。**

測るもの（1 プロセス 1 インタプリタ）
------------------------------------
③ **フォントが自分で追従するか**＝表示スケールを変えた前後で `font.actual()` の
   実効サイズが**アプリが何もせずに**変わるか。
   ⚠️ **窓の寸法が変わるだけでは③は通らない**＝それは `watch_display` の画面サイズ側
   （B-022）で、Tk 9 でも要る。③が問うているのは *`theme.apply_fonts` の DPI 側
   （約 60 行）を削れるか* だけ。

B-119 **窓の寸法が 6px ずつ変わり続けるか**＝スケール変更後に窓を動かして数える。
   今日（2026-08-23）の 6 巡目で **`resizable(False, False)` の窓だけが暴走する**と
   実測したので、対照は**その 1 変数**で建てる（`BARE-fixed` / `BARE-resizable`）。
   Tk 8.6 の基準値は `issue_evidence/B-119_06_resizable-probe.log`＝**55 回 / 0 回**。

実行::

    # Tk 9 側（暴走の有無まで測る）
    & "D:/tools/py315rc/python.exe" experiments/tk9_dpi_probe.py
    # Tk 8.6 側（フォントの基準値だけ・窓は動かさない）
    & "$env:RADIOSIM_PYTHON" experiments/tk9_dpi_probe.py --no-drag

⚠️ **2 つを同時に起動してよい**（`--no-drag` の側は窓を動かさないので、移動モーダル
ループを取り合わない）＝**人にスケールを変えてもらう回数が 1 回で済む**。

人の操作: **表示スケールを 1 回変えるだけ**（100%↔150%）。あとは触らない。
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from tkinter import font as tkfont

for _stream in (sys.stdout, sys.stderr):
    try:                                    # 既定のコンソールは cp932（B-119 で踏んだ）
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    except Exception:
        pass

T0 = time.perf_counter()

#: 見る字（製品が実際に使うもの＝ここが動かなければ利用者には何も起きない）。
_FONTS = ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkFixedFont")

#: ドラッグ中に数えた 6px 刻み（窓ごと）。
_DRIFTS: "dict[str, int]" = {}


def log(tag: str, msg: str) -> None:
    print(f"{(time.perf_counter() - T0) * 1000:9.0f}ms  {tag:<6} {msg}", flush=True)


def set_dpi_awareness() -> str:
    """製品と**同じ** DPI 認識にする（比較にならないので既定のままにしない）。

    ⚠️ `main._set_dpi_awareness` を import しない＝あちらは製品の import 網に
    繋がっており、この探針の「依存 0」が壊れる。**呼ぶ API は同じ**（v2）。
    """
    import ctypes
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    return "none"


def snapshot(root: tk.Tk) -> "dict[str, object]":
    """いまの「見え方」を 1 枚に固める（前後で引き算する材料）。"""
    shot: "dict[str, object]" = {
        "scaling": round(float(root.tk.call("tk", "scaling")), 4),
        "dpi": int(root.winfo_fpixels("1i")),
    }
    for name in _FONTS:
        try:
            f = tkfont.nametofont(name)
            # **実効サイズ**を見る（`cget("size")` は要求値で、負値=ピクセル指定の
            # ときに DPI を反映しない＝ここを間違えると「追従した」と誤読する）。
            shot[name] = (f.actual("size"), f.metrics("linespace"))
        except tk.TclError:
            shot[name] = None
    return shot


def report(before: "dict[str, object]", after: "dict[str, object]") -> bool:
    """③ の判定＝**フォントの実効サイズが自分で変わったか**。"""
    print("\n=== ③ フォント追従 ===", flush=True)
    print(f"{'項目':<16}{'変更前':<22}{'変更後':<22}{'追従':<6}", flush=True)
    followed = False
    for key in ("scaling", "dpi", *_FONTS):
        b, a = before.get(key), after.get(key)
        same = b == a
        if key in _FONTS and not same:
            followed = True
        print(f"{key:<16}{str(b):<22}{str(a):<22}{'—' if same else '✅ 変わった':<6}",
              flush=True)
    print("", flush=True)
    if followed:
        print("✅ **③ 通過**＝アプリが何もせずに字が変わった "
              "⇒ `apply_fonts` の DPI 側を削れる可能性がある", flush=True)
    else:
        print("⛔ **③ 不通過**＝字は変わらない ⇒ Tk 9 でも DPI 追従の実装は要る "
              "（移行の実利がここで 1 つ消える）", flush=True)
    print("⚠️ `scaling` / `dpi` だけが動いても③は通らない＝それは画面サイズ側"
          "（B-022）で、Tk 9 でも別途要る。", flush=True)
    return followed


def watch(win: "tk.Misc", name: str) -> None:
    """`win` の**実サイズ**が 6 の倍数だけ動いたら数える（B-119 の印）。"""
    seen = {"size": None}

    def on_configure(event: "tk.Event") -> None:
        if event.widget is not win:
            return
        try:
            now = (win.winfo_width(), win.winfo_height())
        except tk.TclError:
            return
        was = seen["size"]
        if was is not None and now != was:
            dw = now[0] - was[0]
            if dw and abs(dw) % 6 == 0 and abs(dw) <= 24:
                _DRIFTS[name] = _DRIFTS.get(name, 0) + 1
                log("DRIFT", f"{name}  {was} -> {now}  (Δ{dw:+d}) ★6px 刻み")
        seen["size"] = now

    win.bind("<Configure>", on_configure, add="+")


def frame_hwnd(win: "tk.Misc") -> int:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    return int(user32.GetAncestor(win.winfo_id(), 2) or 0)      # GA_ROOT


def move_window(hwnd: int, seconds: float = 3.0) -> None:
    """タイトルバーのドラッグと同じ**移動モーダルループ**に入れて動かす。

    ⚠️ 合成マウスでは `Toplevel` が動かない（B-119 の探針で実測）ので
    `WM_SYSCOMMAND`／`SC_MOVE` ＋矢印キー。**Tk のスレッドから呼ばない。**
    """
    import ctypes
    user32 = ctypes.windll.user32
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.15)
    user32.PostMessageW(hwnd, 0x0112, 0xF010, 0)               # WM_SYSCOMMAND/SC_MOVE
    time.sleep(0.25)
    try:
        for i in range(max(int(seconds / 0.04), 1)):
            key = 0x27 if (i // 12) % 2 == 0 else 0x25          # →／←
            user32.keybd_event(key, 0, 0, 0)
            user32.keybd_event(key, 0, 0x0002, 0)
            time.sleep(0.04)
    finally:
        user32.keybd_event(0x0D, 0, 0, 0)                       # Enter で確定
        user32.keybd_event(0x0D, 0, 0x0002, 0)


def build_pair(root: tk.Tk) -> "list[tuple[str, tk.Toplevel]]":
    """中身も寸法も同じで、**リサイズ可否だけ違う** 2 枚（B-119 の分かれ目）。"""
    pair = []
    for resizable in (False, True):
        name = f"BARE-{'resizable' if resizable else 'fixed'}"
        win = tk.Toplevel(root)
        win.title(name)
        win.resizable(resizable, resizable)
        tk.Label(win, text=name).pack(padx=40, pady=60)
        win.geometry("500x400+%d+%d" % (140 if resizable else 660, 140))
        watch(win, name)
        pair.append((name, win))
    return pair


def drag_all(root: tk.Tk, pair: "list[tuple[str, tk.Toplevel]]") -> None:
    """2 枚を順に 3 秒ずつ動かし、判定を出して終わる。"""
    import threading
    queue = list(pair)

    def step() -> None:
        if not queue:
            verdict()
            return
        name, win = queue.pop(0)
        log("AUTO", f"{name} を 3 秒動かします（マウスに触らないでください）")
        thread = threading.Thread(target=move_window, args=(frame_hwnd(win),),
                                  daemon=True)
        thread.start()

        def wait() -> None:
            if thread.is_alive():
                root.after(200, wait)
                return
            # 締めるのは余韻の後（手を離しても暴走は続く＝B-119 の芯）。
            root.after(1000, lambda: (log("DROP", f"{name}  ★6px 刻み="
                                          f"{_DRIFTS.get(name, 0)} 回"), step()))

        root.after(200, wait)

    step()


def verdict() -> None:
    fixed = _DRIFTS.get("BARE-fixed", 0)
    resizable = _DRIFTS.get("BARE-resizable", 0)
    print("\n=== B-119（Tk 8.6 の基準値＝fixed 55 回 / resizable 0 回）===", flush=True)
    print(f"BARE-fixed      (0, 0)   {'🔴' if fixed else '✅'} {fixed} 回", flush=True)
    print(f"BARE-resizable  (1, 1)   {'🔴' if resizable else '✅'} {resizable} 回",
          flush=True)
    if fixed:
        print("\n⛔ **B-119 は Tk 9 でも残る**＝移行しても利用者への案内"
              "（表示スケールを変えたら再起動）は外せない", flush=True)
    else:
        print("\n✅ **B-119 が消えた**＝移行の費用対効果を決める最大の材料"
              "（ただし 8.6 で暴走することを同じ探針で確かめた上での比較か要確認）",
              flush=True)
    print("\n（窓は開いたままです＝閉じて構いません）", flush=True)


def main() -> None:
    no_drag = "--no-drag" in sys.argv
    awareness = set_dpi_awareness()
    root = tk.Tk()
    root.title(f"Tk {root.tk.call('info', 'patchlevel')} probe")
    root.geometry("520x260+140+560")
    tk.Label(root, text="表示スケールを 1 回変えてください\n"
                        "（そのあとは触らないでください）").pack(padx=30, pady=40)
    log("INIT", f"python={sys.version.split()[0]}  "
                f"Tk={root.tk.call('info', 'patchlevel')}  dpi={awareness}  "
                f"{'（フォントのみ・窓は動かさない）' if no_drag else '（暴走も測る）'}")

    before = snapshot(root)
    log("INIT", f"変更前: {before}")
    state = {"done": False}

    def poll() -> None:
        """**アプリは何もしない**＝スケールの変化は `winfo_fpixels` で見るだけ。

        🔑 ここで `apply_fonts` 相当のことを 1 行でもやると③の測定が壊れる
        （追従したのが Tk なのか我々なのか分からなくなる）。
        """
        if not state["done"] and int(root.winfo_fpixels("1i")) != before["dpi"]:
            state["done"] = True
            # 追従が非同期の場合に取りこぼさないよう、少し置いてから固める。
            root.after(1500, settle)
        root.after(200, poll)

    def settle() -> None:
        after = snapshot(root)
        log("INIT", f"変更後: {after}")
        report(before, after)
        if no_drag:
            print("（--no-drag ＝ここまで。窓は閉じて構いません）", flush=True)
            return
        pair = build_pair(root)
        root.after(2000, lambda: drag_all(root, pair))

    poll()
    print("\n--- 人の操作は **表示スケールを 1 回変えるだけ** ---\n", flush=True)
    root.mainloop()


if __name__ == "__main__":
    main()
