"""B-119 の実機採取＝別 DPI のモニタで窓が 6px ずつ縮み続けているのは誰か。

実行（デュアルモニタの実機で）::

    & "$env:RADIOSIM_PYTHON" experiments/b119_frame_slip_probe.py


1 巡目の探針で分かったこと:
  * 幅が `<Configure>` のたびに **きっかり 6px** 減り続ける（高さは 27px×2 で止まる）
  * **手を離しても止まらない**＝ドラッグ（`_pointer_is_down`）は無関係
  * その間 `REFIT` は 1 度も走っていない＝`fit_to_content` ではない

ここで決めること＝**Python 側が geometry を書いているのか、書いていないのか**。
  * `WMGEO` 行が 6px ずつの縮みの直前に出る    ⇒ 我々のコード（stack に犯人が出る）
  * `SIZE` だけが並び `WMGEO` が出ない          ⇒ **Tk/Windows 側**（our code は無実）

`BARS` 行＝スクロールバーの出し入れ（`_ScrollEscape.sync`）。6px がバーの太さの差なら
ここが毎回めくれているはず。`MENU` 行＝メニューバーの高さ（Tk の geometry に入らない）。
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
import traceback
from pathlib import Path

REPO = Path(r"D:\dev\radiosim-repo")
sys.path.insert(0, str(REPO))

import main as app_main                      # noqa: E402
from views import theme, window_fit          # noqa: E402

T0 = time.perf_counter()


def log(kind: str, text: str) -> None:
    print(f"{(time.perf_counter() - T0) * 1000:8.0f}ms  {kind:<5} {text}", flush=True)


def install_geometry_probe() -> None:
    """`wm geometry` に書き込んだ者を、呼び出し元 3 段つきで記録する。"""
    orig = tk.Wm.wm_geometry

    # 引数名は Tk の `wm_geometry` に合わせる（キーワードで呼ばれても壊さないため）。
    def probed(self, newGeometry=None):       # noqa: N803
        if newGeometry is not None:
            where = " <- ".join(
                f"{Path(f.filename).name}:{f.lineno} {f.name}"
                for f in reversed(traceback.extract_stack()[-4:-1]))
            log("WMGEO", f"{newGeometry!r}   {where}")
        return orig(self, newGeometry)

    tk.Wm.wm_geometry = probed                # type: ignore[assignment]
    tk.Wm.geometry = probed                   # type: ignore[assignment]


def install_bars_probe() -> None:
    """スクロールバーの出し入れを記録する（6px の正体がバーかを見る）。"""
    orig = window_fit._ScrollEscape.sync

    def probed(self, *, overflow_v: bool, overflow_h: bool):
        before = self.active
        got = orig(self, overflow_v=overflow_v, overflow_h=overflow_h)
        if before != self.active:
            log("BARS", f"{before} -> {self.active}  pad={got}  "
                        f"need={getattr(self.win, '_fit_need', None)}")
        return got

    window_fit._ScrollEscape.sync = probed    # type: ignore[assignment]


def build() -> tk.Tk:
    import ctypes
    log("INIT", f"dpi awareness = {app_main._set_dpi_awareness(ctypes.windll)}")
    import sv_ttk
    from core import config, i18n
    from views.launcher import SimLauncher
    root = tk.Tk()
    cfg = config.load_config()
    i18n.set_lang(cfg.get("lang", "en"))
    sv_ttk.set_theme("light")
    theme.apply_fonts(root)
    SimLauncher(root, lambda *_a: None)
    root.title("B-119 probe 2（別 DPI のモニタへ引きずる）")
    return root


def main() -> None:
    install_bars_probe()
    root = build()
    install_geometry_probe()                  # 建て終えてから（起動時の分は要らない）

    def on_change(dpi: int, changed: bool) -> None:
        log("REFIT", f"dpi={dpi} changed={changed}")
        window_fit.refit_all(root, shrink=changed)

    theme.watch_display(root, on_change)

    last = {"size": (root.winfo_width(), root.winfo_height())}

    def on_configure(event: "tk.Event") -> None:
        if event.widget is not root:
            return
        now = (root.winfo_width(), root.winfo_height())
        if now != last["size"]:
            dw, dh = now[0] - last["size"][0], now[1] - last["size"][1]
            try:
                dpi = theme.window_dpi(root)
            except Exception:
                dpi = -1
            log("SIZE", f"{last['size']} -> {now}  (Δ{dw:+d},{dh:+d})  dpi={dpi}  "
                        f"need={getattr(root, '_fit_need', None)} "
                        f"fit={getattr(root, '_fit_size', None)}")
            last["size"] = now

    root.bind("<Configure>", on_configure, add="+")

    menu = root.nametowidget(root["menu"]) if root["menu"] else None
    log("MENU", f"menubar={'あり' if menu else 'なし'}  "
                f"minsize={root.wm_minsize()}  geometry={root.geometry()}  "
                f"start={last['size']}")
    print("\n--- ここから: 窓を掴んで WQHD/150% 側へ。縮み始めたら **手を離して 5 秒待つ** ---\n",
          flush=True)
    root.mainloop()


if __name__ == "__main__":
    main()
