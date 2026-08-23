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
  * `FRAME` は**参考値**＝ここが正しくてもドリフトする（`rebuild` で確認）。⇒ **枠の値の
    新旧では説明が付かない**ことの確認に使う

確定していること（2026-08-23 の採取 9 本・**結論＝アプリからは直せない**）:
  * ドリフト量は**両方向とも 6px** ＝ `装飾幅(144) − 装飾幅(96)`。周期は約 100ms
    （ドラッグ中に Tk が張るサービス用タイマー）。
  * 暴走中に `WMGEO` は 1 行も出ない ⇒ **我々のコードは再適用していない**。
  * `correct_landing`（RC6）は正しく動き、**ドラッグが始まるまでは安定している**。
  * 🔴 **原因の説明は未解明のまま**＝当初は「Tk が変更前の枠の厚みを保持している」と
    書いたが、**`rebuild` で棄却した**（下）。ずれ量が 96↔144 の装飾差と一致する事実は
    変わらないが、**その理由は分かっていない**。⇒ **Tk 8.6 のより深い層**にあり、
    アプリからは触れない（[[project-tk9-migration]] の再判断トリガーへ昇格）。

環境変数（`RADIOSIM_B119_FIX` で 1 つ選ぶ）:
  nomenu   …… **メニューバーを外して**起動する（切り分け）。これで暴走しなければ、
               Tk の高さ計算（メニュー込み）が絡んでいると分かる。⚠️ 直し方ではなく
               **原因の切り分け**（製品からメニューを外すことはしない）。
  menu     …… DPI が変わったあとに**メニューバーを付け直す**（Tk に枠を測り直させる候補）。
  override …… DPI が変わったあとに `overrideredirect` を往復させる（Tk の内部で
               ラッパー窓を作り直させる候補。装飾が一瞬消える）。
  refresh  …… withdraw → deiconify（**棄却済み**。対照として残してある）。
  both     …… 大きさと位置を**1 つの要求**で出し直す（`WxH+X+Y`）。製品は大きさだけ
               `fit_to_content` が、位置だけ `place_within_screen` が別々に出しており、
               Tk がその 2 つを別の枠の値で解釈している可能性を潰す。**製品に入れやすい**。
  twice    …… `w+1` → `w` と 2 度出す（Tk に往復させて枠を合わせ直させる）。**製品に入れやすい**。
  release  …… `wm geometry ""` で要求そのものを手放す（Tk が中身の要求サイズに従う）。
               ⚠️ 画面上限へのクランプが効かなくなるので、効いても採用は要検討。

  minmax   …… 決めた寸法で `wm minsize` / `wm maxsize` を**固定**する。⛔ **棄却**（下）。
  toolwindow …… `wm attributes -toolwindow` を往復させる（`overrideredirect` とは別の
               経路で Tk にラッパー窓を作り直させる候補）。
  rebuild  …… **DPI が変わったあとに新しい窓を建てる**（切り分け）。⚠️ ここで確かめるのは
               「**変更後に作った窓はずれるのか**」の 1 点だけ。ずれなければ「窓を作り直す」
               方向（＝製品ではランチャーを建て直す）に望みがあり、ずれれば**その道も死ぬ**。
               ⇒ 出てきた **NEW と書かれた窓をドラッグ**して、`NEWSZ` 行が出るかを見る。

⛔ **棄却済み**（同じ発想に戻らないための記録）:
  * `nomenu`  …… メニューバーを外しても**幅は 6px ずつ縮み続けた** ⇒ メニューは無関係。
                  （ただし高さのドリフト 27px は +3px に変わった ⇒ **高さ側だけ**はメニュー由来）
  * `refresh`  …… withdraw → deiconify では止まらない。
  * `both`     …… 大きさと位置を 1 つの要求で出しても止まらない。
  * `twice`    …… `w+1` → `w` の往復でも止まらない。
  * `override` …… ラッパー窓を作り直しても止まらない。
  * `noescape` …… **受け皿の `<Configure>` 処理を全部止めても幅は減り続ける**
                   ⇒ 燃料は我々の受け皿ではない。⚠️ ただし**高さのドリフト（27px）は消えた**
                   ＝高さ側だけは受け皿由来。**幅の 6px は Tk/WM の中だけで完結している。**
  * `noitem`   …… `itemconfig` を値が変わるときだけにしても止まらない。
  * `release`  …… `wm geometry ""` は**悪化**（608 ⇄ 596 を延々往復する振動になる）。
  * `toolwindow` …… 効果なし。
  * ⛔ `minmax` …… **片方向でしか効かない**。100%→150% では止まったが、**150%→100% では
                   `maxsize` を掛けているのに 674px まで太り続けた**（＝Tk/WM 側の
                   再適用は min/max を尊重しない。利用者の操作にしか効かない）。
                   さらに止まった側でも**決めた寸法に戻らず 6px 足りないまま固まり**、
                   `need > 実幅` が成立し続けて**横スクロールバーが出たまま**になった。
                   ⇒ 製品に入れた `pin_size()` は 2026-08-23 に**撤回**。
  * ⛔ `rebuild` …… **これが決定打**。**スケール変更後に新しく建てたウィンドウ**
                   （枠の値は正しく 左=11 上=45）が、**同じ 6px ずつ縮む**。
                   ⇒ ①ウィンドウを作り直す方向に望みは無い
                     ②「Tk が古い枠の値を持っている」という説明**そのものが誤り**
                   （値は正しいのにずれ量だけが装飾差と一致する）。
  🔑 **3 つに共通していたこと**＝手当てで寸法を出し直した**直後に必ず 6px 減る**。
     ⇒ 手当ての中身ではなく「**寸法を要求する行為そのもの**」が 1 回ぶんのずれを生む。
     ⇒ 直すべきは「ずれを打ち消す」ことではなく「**要求を繰り返させない**」こと。
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
FIX = os.environ.get("RADIOSIM_B119_FIX", "").strip().lower()


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


def patch_escape() -> None:
    """受け皿の `<Configure>` 処理に手を入れる（**窓を建てる前に**呼ぶ＝`bind` は
    生成時にメソッドを捕まえるので、後から差し替えても既存の配線には効かない）。"""
    esc = window_fit._ScrollEscape
    if FIX == "noescape":
        esc._on_canvas_configure = lambda self, event: None   # type: ignore[assignment]
        esc._on_body_configure = lambda self, event=None: None  # type: ignore[assignment]
        esc._on_win_configure = lambda self, event: None      # type: ignore[assignment]
        log("INIT", "受け皿の Configure 処理を全部止めた（切り分け）")
    elif FIX == "noitem":
        def idempotent(self, event):
            want_w = max(event.width, self.body.winfo_reqwidth())
            want_h = max(event.height, self.body.winfo_reqheight())
            now = self.canvas.itemcget(self.item, "width"),                 self.canvas.itemcget(self.item, "height")
            if (str(want_w), str(want_h)) == (str(now[0]), str(now[1])):
                return                       # 変わらないなら要求を出さない
            self.canvas.itemconfig(self.item, width=want_w, height=want_h)
        esc._on_canvas_configure = idempotent  # type: ignore[assignment]
        log("INIT", "itemconfig を『変わるときだけ』にした（候補）")


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
    if FIX == "nomenu":
        # 切り分け＝メニューバーを外すと暴走が消えるか。
        try:
            root.config(menu=tk.Menu(root))     # 空のメニュー＝帯が無くなる
            log("INIT", "メニューバーを外した（切り分け）")
        except tk.TclError as exc:
            log("INIT", f"メニューを外せない: {exc}")
    return root


def apply_fix(root: tk.Tk) -> None:
    """DPI が変わったあとに、候補の手当てを 1 つ試す。"""
    if FIX == "refresh":
        root.withdraw(); root.deiconify()
    elif FIX == "menu":
        # メニューバーを付け直す＝Tk に高さを測り直させる候補。
        menu = root["menu"]
        if menu:
            root.config(menu="")
            root.update_idletasks()
            root.config(menu=menu)
    elif FIX == "override":
        # ラッパー窓を作り直させる候補（装飾が一瞬消える）。
        root.overrideredirect(True)
        root.update_idletasks()
        root.overrideredirect(False)
    elif FIX == "both":
        # 大きさと位置を 1 つの要求で出し直す。
        w, h = getattr(root, "_fit_size", (root.winfo_width(), root.winfo_height()))
        x, y = window_fit.window_position(root)
        root.geometry(f"{w}x{h}+{x}+{y}")
    elif FIX == "twice":
        w, h = getattr(root, "_fit_size", (root.winfo_width(), root.winfo_height()))
        root.geometry(f"{w + 1}x{h}")
        root.update_idletasks()
        root.geometry(f"{w}x{h}")
    elif FIX == "release":
        root.geometry("")               # 要求を手放す（中身の要求サイズに従う）
    elif FIX == "minmax":
        w, h = getattr(root, "_fit_size", (root.winfo_width(), root.winfo_height()))
        root.minsize(w, h)
        root.maxsize(w, h)
    elif FIX == "rebuild":
        # 「DPI 変更後に作った窓はずれるか」を見るための、まっさらな窓。
        fresh = tk.Toplevel(root)
        fresh.title("NEW（この窓をドラッグしてください）")
        fresh.resizable(False, False)
        tk.Label(fresh, text="DPI 変更後に建てた窓／これをドラッグ").pack(
            padx=40, pady=60)
        fresh.geometry("500x400+120+120")
        fresh.update_idletasks()
        seen = {"size": (fresh.winfo_width(), fresh.winfo_height())}
        log("FRAME", f"NEW の枠: {frame_of(fresh)}  size={seen['size']}")

        def watch(_event=None) -> None:
            now = (fresh.winfo_width(), fresh.winfo_height())
            if now != seen["size"]:
                dw, dh = now[0] - seen["size"][0], now[1] - seen["size"][1]
                log("NEWSZ", f"{seen['size']} -> {now}  (Δ{dw:+d},{dh:+d})")
                seen["size"] = now

        fresh.bind("<Configure>", watch, add="+")
    elif FIX == "toolwindow":
        try:
            root.attributes("-toolwindow", True)
            root.update_idletasks()
            root.attributes("-toolwindow", False)
        except tk.TclError as exc:
            log("FRAME", f"toolwindow を切り替えられない: {exc}")
    else:
        return
    root.update_idletasks()
    log("FRAME", f"手当て({FIX})の後: {frame_of(root)}")


def main() -> None:
    patch_escape()
    install_bars_probe()
    install_landing_probe()
    root = build()
    install_geometry_probe()

    def on_change(dpi: int, changed: bool) -> None:
        log("REFIT", f"dpi={dpi} changed={changed}  frame: {frame_of(root)}")
        window_fit.refit_all(root, shrink=changed)
        if changed and FIX:
            root.after(400, lambda: apply_fix(root))

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
    log("INIT", f"FIX={FIX or 'なし'}  start={last['size']}")
    print("\n--- ここから: 表示スケールを変更 → ランチャーをドラッグ ---\n", flush=True)
    root.mainloop()


if __name__ == "__main__":
    main()
