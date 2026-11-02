"""候補 C＝Tk 9（今の道具・物差し）。同じ課題を製品と同じ部品（tkintermapview・matplotlib・sv_ttk）で作る。

地図のタイルだけは A・B と同じ `127.0.0.1` の口から読む＝オフラインの差を消す。
"""
import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(_HERE), os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))]

from common import boot  # noqa: E402

_FONT = "Yu Gothic UI"
_COLORS = {"ok": "#17794a", "ng": "#b3261e", "pending": "#5d6673"}
_NUMERIC = [("送信アンテナ高 [m]", "h_tx"), ("受信アンテナ高 [m]", "h_rx"),
            ("周波数 [MHz]", "freq"), ("送信電力 [dBm]", "p_tx"),
            ("送信利得 [dBi]", "gain_tx"), ("受信利得 [dBi]", "gain_rx"),
            ("受信感度 [dBm]", "sens"), ("植生の高さ [m]", "veg_h"),
            ("降雨 [mm/h]", "rain_rate")]


class App:
    def __init__(self, ctx) -> None:
        import matplotlib
        matplotlib.use("TkAgg")
        import sv_ttk
        import tkintermapview
        from matplotlib import rcParams
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        from matplotlib.figure import Figure

        from common import engine, tiles
        self.engine, self.tiles = engine, tiles
        self.ctx = ctx
        self.results: queue.Queue = queue.Queue()
        self.seq = 0
        self.first_verdict: float | None = None
        self.metrics: dict = {}
        self.verdict_serial = 0

        root = self.root = tk.Tk()
        root.title("RadioSim 画面の試作 C")
        if ctx.size:
            root.geometry(f"{ctx.size[0]}x{ctx.size[1]}+0+0")
        else:
            root.state("zoomed")
        sv_ttk.set_theme("light")
        rcParams["font.family"] = ["Yu Gothic", "Meiryo", "DejaVu Sans"]

        self.head = tk.Frame(root, bg=_COLORS["pending"])
        self.head.pack(fill="x")
        self.word = tk.Label(self.head, text="計算中", fg="white", bg=_COLORS["pending"],
                             font=(_FONT, 22, "bold"), width=6, anchor="w")
        self.word.pack(side="left", padx=(12, 8))
        self.detail = tk.Label(self.head, text="地形を取得しています", fg="white",
                               bg=_COLORS["pending"], font=(_FONT, 12))
        self.detail.pack(side="left")

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)
        left = self.left = ttk.Frame(body, padding=8, width=268)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        diag = ttk.Frame(body, padding=8, width=268)
        diag.pack(side="right", fill="y")
        diag.pack_propagate(False)
        center = ttk.Frame(body)
        center.pack(side="left", fill="both", expand=True)

        self.vars: dict[str, tk.StringVar] = {}
        self.endpoints_dirty = False
        for label, key in (("送信点（緯度, 経度）", "start"), ("受信点（緯度, 経度）", "end")):
            self._field(left, label, key)
        for label, key in _NUMERIC:
            self._field(left, label, key)
        self.breakdown = ttk.Label(left, justify="left", font=(_FONT, 9))
        self.breakdown.pack(anchor="w", pady=(6, 0))

        self.pick = tk.StringVar(value="start")
        pick_row = ttk.Frame(center)
        pick_row.pack(fill="x")
        ttk.Label(pick_row, text="地図のクリックで動かす点:").pack(side="left", padx=6)
        ttk.Radiobutton(pick_row, text="送信点", variable=self.pick, value="start").pack(side="left")
        ttk.Radiobutton(pick_row, text="受信点", variable=self.pick, value="end").pack(side="left")

        self.map = tkintermapview.TkinterMapView(center, corner_radius=0)
        self.map.pack(fill="both", expand=True)
        self.map.set_tile_server(
            f"http://127.0.0.1:{ctx.app.port}/tile/pale/{{z}}/{{x}}/{{y}}.png", max_zoom=18)
        self.map.add_left_click_map_command(self._map_click)
        self.tx_marker = self.rx_marker = self.path = None

        self.fig = Figure(figsize=(6, 3), dpi=96, layout="constrained")
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=center)
        self.canvas.get_tk_widget().configure(height=310)
        self.canvas.get_tk_widget().pack(fill="x")

        ttk.Label(diag, text="測定（この欄をスクショに入れる）", font=(_FONT, 9, "bold")).pack(anchor="w")
        self.diag_text = ttk.Label(diag, justify="left", font=(_FONT, 9), wraplength=250)
        self.diag_text.pack(anchor="w")
        ttk.Label(diag, text="プロキシ URL（要るときだけ）", font=(_FONT, 9)).pack(anchor="w", pady=(10, 0))
        self.proxy = ttk.Entry(diag)
        self.proxy.pack(fill="x")
        ttk.Button(diag, text="適用", command=self._apply_proxy).pack(anchor="e", pady=3)

        self._sync_map(fit=True)
        self._poll()
        self.request("地形を取得しています")

    def _field(self, parent, label: str, key: str) -> None:
        var = tk.StringVar(value=self.engine.DEFAULT_INPUTS[key])
        self.vars[key] = var
        ttk.Label(parent, text=label, font=(_FONT, 9)).pack(anchor="w")
        entry = ttk.Entry(parent, textvariable=var)
        entry.pack(fill="x", pady=(0, 3))
        if key in ("start", "end"):
            self.__dict__.setdefault("committed", {})[key] = var.get()
            var.trace_add("write", lambda *_: self._endpoint_edit())
            entry.bind("<Return>", lambda _e: self._commit_endpoints())
            entry.bind("<FocusOut>", lambda _e: self._commit_endpoints())
        else:
            var.trace_add("write", lambda *_: self.request("再計算しています"))

    def _endpoint_edit(self) -> None:
        self.endpoints_dirty = True
        self.request("")

    def _commit_endpoints(self) -> None:
        if not self.endpoints_dirty:
            return
        self.endpoints_dirty = False
        self.committed = {k: self.vars[k].get() for k in ("start", "end")}
        self._sync_map()
        self.request("地形を取得しています")

    def _map_click(self, coords) -> None:
        self.vars[self.pick.get()].set(f"{coords[0]:.5f}, {coords[1]:.5f}")
        self._commit_endpoints()

    def _sync_map(self, fit: bool = False) -> None:
        try:
            a = tuple(float(v) for v in self.vars["start"].get().split(","))
            b = tuple(float(v) for v in self.vars["end"].get().split(","))
        except ValueError:
            return
        for attr in ("tx_marker", "rx_marker", "path"):
            if getattr(self, attr) is not None:
                getattr(self, attr).delete()
        self.tx_marker = self.map.set_marker(*a, text="T", marker_color_circle="#1f5fbf")
        self.rx_marker = self.map.set_marker(*b, text="R", marker_color_circle="#a4470f")
        self.path = self.map.set_path([a, b], color="#1f5fbf", width=3)
        if fit:
            self.map.fit_bounding_box((max(a[0], b[0]) + 0.003, min(a[1], b[1]) - 0.003),
                                      (min(a[0], b[0]) - 0.003, max(a[1], b[1]) + 0.003))

    def _show(self, state: str, word: str, detail: str) -> None:
        color = _COLORS[state]
        self.head.configure(bg=color)
        self.word.configure(bg=color, text=word)
        self.detail.configure(bg=color, text=detail)

    def request(self, waiting: str) -> None:
        self.seq += 1
        if self.endpoints_dirty:
            self._show("pending", "計算中", "端点を確定してください（Enter か、欄の外をクリック）")
            return
        self._show("pending", "計算中", waiting)
        mine = self.seq
        inputs = {k: v.get() for k, v in self.vars.items()}

        def work() -> None:
            self.results.put((mine, self.engine.calculate(inputs)))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                mine, res = self.results.get_nowait()
                if mine == self.seq:
                    self._apply(res)
        except queue.Empty:
            pass
        self.root.after(5, self._poll)

    def _apply(self, res: dict) -> None:
        if not res["ok"]:
            self._show("pending", "入力待ち" if res["kind"] == "input" else "地形なし", res["message"])
            return
        failed = f"・地形の取得失敗 {res['fail_pct']}%" if res["fail_pct"] > 0 else ""
        self._show("ok" if res["status"] == "OK" else "ng", res["status"],
                   f"余裕 {res['margin_db']:.1f} dB・受信電力 {res['p_rx_dbm']:.1f} dBm・"
                   f"距離 {res['distance_km']:.2f} km{failed}")
        self.breakdown.configure(text=(
            f"自由空間損失 {res['fspl_db']:.1f} dB\n回折損 {res['diff_loss_db']:.1f} dB\n"
            f"植生減衰 {res['veg_loss_db']:.1f} dB\n総損失 {res['total_loss_db']:.1f} dB\n"
            f"Fresnel 遮蔽 {res['blocked_pct']:.1f} %\n標本数 {res['samples']} 点"))
        self._draw(res["profile"])
        self.verdict_serial += 1
        if self.first_verdict is None:
            self.root.update_idletasks()
            self.first_verdict = time.time()
            self.root.after(50, self._after_first)

    def _draw(self, p: dict) -> None:
        ax = self.ax
        ax.clear()
        xs = p["d_km"]
        ground = [v if v is not None else float("nan") for v in p["ground"]]
        ax.fill_between(xs, min(g for g in ground if g == g) - 5, ground, color="#c8bfa9")
        ax.plot(xs, p["los"], color="#1f5fbf", lw=1.5)
        ax.plot(xs, p["fresnel_lower"], color="#1f5fbf", lw=1.2, ls="--")
        ax.set_xlabel("距離 [km]")
        ax.set_ylabel("高さ [m]")
        ax.grid(alpha=0.3)
        self.canvas.draw()

    def _apply_proxy(self) -> None:
        from core import dem
        dem.set_proxy(self.proxy.get().strip())
        self.map.delete_all_marker()
        self.map.set_zoom(self.map.zoom)

    def _after_first(self) -> None:
        self.root.update()
        start_ms = self.ctx.app.facts["process_start_epoch_ms"]
        self.metrics["startup_ms"] = round(self.first_verdict * 1000 - start_ms)
        self.metrics["pan_fps"] = self._pan_fps()
        self.metrics["input_ms"] = self._input_latency(30)
        self.metrics["client"] = f"{self.root.winfo_width()}×{self.root.winfo_height()}"
        self.metrics["screen"] = f"{self.root.winfo_screenwidth()}×{self.root.winfo_screenheight()}"
        self.metrics["dpi_scale"] = round(self.root.winfo_fpixels("1i") / 96, 3)
        self.metrics["tk"] = self.root.tk.call("info", "patchlevel")
        self.metrics["overflow"] = {"inputs": max(0, self.left.winfo_reqheight() - self.left.winfo_height())}
        self.ctx.app.metrics.update(self.metrics)
        self._show_diag()
        if self.ctx.measure_out:
            boot.finish(self.ctx)
            self.root.after(100, self.root.destroy)

    def _pan_fps(self) -> int:
        lat, lon = self.map.get_position()
        t0 = time.perf_counter()
        frames = 0
        for i in range(24):
            self.map.set_position(lat, lon + (0.0008 if i % 2 else -0.0008))
            self.root.update()
            frames += 1
        self.map.set_position(lat, lon)
        return round(frames / (time.perf_counter() - t0))

    def _input_latency(self, count: int) -> dict:
        var = self.vars["p_tx"]
        original = var.get()
        samples = []
        for i in range(count):
            serial = self.verdict_serial
            t0 = time.perf_counter()
            var.set(str(10 + i % 20))
            while self.verdict_serial == serial:
                self.root.update()
            self.root.update()
            samples.append((time.perf_counter() - t0) * 1000)
        var.set(original)
        samples.sort()
        return {"p50": samples[count // 2], "p95": samples[int(count * 0.95)], "max": samples[-1]}

    def _show_diag(self) -> None:
        f, m, t = self.ctx.app.facts, self.metrics, self.tiles.stats()
        over = m["overflow"]["inputs"]
        self.diag_text.configure(text="\n".join([
            f"候補: {f['candidate']}・{f['launch']} 回目の起動",
            f"起動→判定: {m['startup_ms'] / 1000:.2f} 秒",
            f"入力→判定 中央: {m['input_ms']['p50']:.0f} ms",
            f"入力→判定 95%: {m['input_ms']['p95']:.0f} ms",
            f"地図の移動: {m['pan_fps']} fps",
            f"描画: GDI（Tk {m['tk']}）",
            f"WebView2: {f['webview2'] or '無し'}",
            f"画面: {m['screen']}",
            f"使える領域: OS {f['work_area']}",
            f"窓の内側: {m['client']}",
            f"DPI 倍率: {m['dpi_scale']}",
            f"はみ出し: 入力 {over} px",
            f"タイル: 手元 {t['cache']}・取得 {t['fetched']}・失敗 {t['failed']}",
            f"タイルの失敗: {t['last_error'] or 'なし'}",
            f"プロキシ: {f['proxy']}",
            f"遠隔の接続: {'はい' if f['remote_session'] else 'いいえ'}（{f['session_name'] or '—'}）",
            f"Windows: {f['windows']}",
            f"Python: {f['python']}{'（固めた exe）' if f['frozen'] else ''}",
        ]))
        self.root.after(2000, self._show_diag)


def main() -> None:
    ctx = boot.start("C Tk 9")
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass
    try:
        app = App(ctx)
    except Exception as exc:  # noqa: BLE001 ─ `--noconsole` でも理由を見せる
        boot.fatal(f"窓を作れません: {exc!r}\n{json.dumps(ctx.app.facts, ensure_ascii=False)}")
    app.root.mainloop()


if __name__ == "__main__":
    main()
