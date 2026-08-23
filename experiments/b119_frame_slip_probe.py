"""B-119 の実機採取＝ドラッグ中に窓の寸法を動かしているのは誰か。

実行（1 枚のモニタで足りる）::

    & "$env:RADIOSIM_PYTHON" experiments/b119_frame_slip_probe.py

再現手順（2026-08-23・ユーザー報告の RC6 版）:
  1. ズーム 100% で起動する
  2. **起動したまま** Windows の表示スケールを 150% に変える
  3. ランチャーをタイトルバーでドラッグする ⇒ 縮み続ける
  （150% で起動 → 100% に変える と、同じ操作で**拡大し続ける**＝向きが対称）

出るもの（1 行 1 事象・時刻はミリ秒）:
    SIZE   実サイズが変わった（Δ つき）
    WMGEO  `wm geometry` に**書き込んだ者**（呼び出し元 3 段つき）
    BARS   スクロールバーの出し入れ（`_ScrollEscape.sync`）
    CHECK  `watch_display` の測り直しが走った（掴んでいるかも出る）
    REFIT  我々が測り直した
    LAND   着地の確認（`correct_landing`）が走った
    FRAME  Tk が思っている枠の厚み（`winfo_rootx/rooty` と geometry の差）

読み方:
  * `SIZE` が並ぶのに `WMGEO` が無い     ⇒ **Tk/WM が動かしている**（我々ではない）
  * `SIZE` の直前に `WMGEO` がある       ⇒ その呼び出し元が犯人
  * `FRAME` が実際の DPI と食い違ったまま ⇒ **Tk の枠の厚みが古い**（本命の仮説）

環境変数:
  RADIOSIM_B119_REFRESH=1 …… DPI が変わったあとに窓を建て直して（withdraw →
      deiconify）**Tk に枠の厚みを測り直させる**。これでドラッグの暴走が止まる
      なら、直し方はそこだと確定する（候補の検証を 1 回の採取で済ませるため）。
"""
from __future__ import annotations

import os
import sys
import time
import tkinter as tk
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import main as app_main                      # noqa: E402
from views import theme, window_fit          # noqa: E402

T0 = time.perf_counter()
REFRESH = os.environ.get("RADIOSIM_B119_REFRESH") == "1"


def log(kind: str, text: str) -> None:
    print(f"{(time.perf_counter() - T0) * 1000:8.0f}ms  {kind:<6} {text}", flush=True)


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
    orig = window_fit._ScrollEscape.sync

    def probed(self, *, overflow_v: bool, overflow_h: bool):
        before = self.active
        got = orig(self, overflow_v=overflow_v, overflow_h=overflow_h)
        if before != self.active:
            log("BARS", f"{before} -> {self.active}  pad={got}  "
                        f"need={getattr(self.win, '_fit_need', None)}")
        return got

    window_fit._ScrollEscape.sync = probed    # type: ignore[assignment]


def install_landing_probe() -> None:
    orig = window_fit.correct_landing

    def probed(win, *, from_dpi=None):
        got = orig(win, from_dpi=from_dpi)
        log("LAND", f"{win} from_dpi={from_dpi} -> 言い直した={got}  "
                    f"fit={getattr(win, '_fit_size', None)} "
                    f"asked={getattr(win, '_fit_asked', None)}")
        return got

    window_fit.correct_landing = probed       # type: ignore[assignment]


def frame_of(win) -> str:
    """Tk が思っている枠の厚み（クライアント左上と装飾枠左上の差）。"""
    try:
        x, y = window_fit.window_position(win)
        return (f"左={win.winfo_rootx() - x} 上={win.winfo_rooty() - y}  "
                f"（OS の言う値={window_fit.decoration_size(win)} / "
                f"dpi={theme.window_dpi(win)}）")
    except Exception as exc:                  # 破棄途中など
        return f"(測れない: {exc})"


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
    root.title("B-119 probe（スケールを変えてからドラッグ）")
    return root


def main() -> None:
    install_bars_probe()
    install_landing_probe()
    root = build()
    install_geometry_probe()

    def on_change(dpi: int, changed: bool) -> None:
        log("REFIT", f"dpi={dpi} changed={changed}  frame: {frame_of(root)}")
        window_fit.refit_all(root, shrink=changed)
        if changed and REFRESH:
            # 候補の検証＝Tk に枠の厚みを測り直させる。
            root.after(300, lambda: (root.withdraw(), root.deiconify(),
                                     log("FRAME", f"建て直した後: {frame_of(root)}")))

    orig_ptr = theme._pointer_is_down

    def probed_pointer() -> bool:
        down = orig_ptr()
        log("CHECK", f"down={down}")
        return down

    theme._pointer_is_down = probed_pointer   # type: ignore[assignment]
    theme.watch_display(root, on_change)

    last = {"size": (root.winfo_width(), root.winfo_height())}

    def on_configure(event: "tk.Event") -> None:
        if event.widget is not root:
            return
        now = (root.winfo_width(), root.winfo_height())
        if now != last["size"]:
            dw, dh = now[0] - last["size"][0], now[1] - last["size"][1]
            log("SIZE", f"{last['size']} -> {now}  (Δ{dw:+d},{dh:+d})  "
                        f"need={getattr(root, '_fit_need', None)} "
                        f"fit={getattr(root, '_fit_size', None)}  {frame_of(root)}")
            last["size"] = now

    root.bind("<Configure>", on_configure, add="+")
    log("FRAME", f"起動時: {frame_of(root)}")
    log("INIT", f"REFRESH={'あり' if REFRESH else 'なし'}  start={last['size']}")
    print("\n--- ここから: 表示スケールを変更 → ランチャーをドラッグ ---\n", flush=True)
    root.mainloop()


if __name__ == "__main__":
    main()
