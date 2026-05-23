"""
views/graph.py
==============
グラフウィンドウ（matplotlib）の構築・スライダー操作・保存。

計算ロジックは simulation.run_calculation() に委譲する。
このファイルは「表示」と「ユーザー操作の受け取り」のみを担う。
"""

import logging

import matplotlib
matplotlib.use("TkAgg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from matplotlib.widgets import Button, Slider
from tkinter import messagebox

import models
import simulation as sim

logger = logging.getLogger("radiosim")


def show_graph(params: sim.SimParams, raw_elevs: np.ndarray) -> None:
    """
    地形断面グラフウィンドウを開く（ブロッキング）。

    Args:
        params:    シミュレーションパラメータ
        raw_elevs: 取得済み生標高配列
    """
    terrain = models.calculate_terrain_profile(
        raw_elevs = raw_elevs,
        lat_tx    = params.lat_tx,
        lon_tx    = params.lon_tx,
        lat_rx    = params.lat_rx,
        lon_rx    = params.lon_rx,
    )
    _GraphWindow(params, terrain).show()


# ============================================================
# グラフウィンドウ（内部クラス）
# ============================================================
class _GraphWindow:
    """
    matplotlib ウィンドウを管理する内部クラス。
    show_graph() からのみ生成される。
    """

    _PANEL_X  = 0.79
    _PANEL_W  = 0.18

    def __init__(self, params: sim.SimParams, terrain: models.TerrainProfile) -> None:
        self._params  = params
        self._terrain = terrain
        self._last_result: models.LinkBudgetResult | None = None
        self._pending_timer = None

        self._fig, self._ax = plt.subplots(figsize=(15, 8))
        self._fig.patch.set_facecolor("#EAEAEA")
        self._ax.set_facecolor("#F2F2F2")
        plt.subplots_adjust(left=0.07, right=0.77, top=0.88, bottom=0.26)

        self._build_static_terrain()
        self._build_panels()
        self._build_sliders()
        self._build_dynamic_objects()
        self._connect_events()

    # ----------------------------------------------------------
    # 静的描画（起動時1回のみ）
    # ----------------------------------------------------------
    def _build_static_terrain(self) -> None:
        t = self._terrain
        y_min = float(np.min(t.raw_elevs)) - 30

        veg_top = t.elevs_with_curve + self._params.veg_h

        self._ax.fill_between(
            t.d_km_axis, t.elevs_with_curve, y_min,
            color="#8B4513", alpha=0.4,
        )
        self._ax.fill_between(
            t.d_km_axis, veg_top, t.elevs_with_curve,
            color="green", alpha=0.3,
        )

        self._ax.set_title(f"Radio Prop Sim ({self._params.freq_mhz} MHz)")
        self._ax.set_xlabel("Distance [km]")
        self._ax.set_ylabel("Altitude [m]")
        self._ax.grid(True, alpha=0.2)

    def _build_panels(self) -> None:
        # リンクバジェットパネル（[Diff Model] 行を含めた高さに拡張）
        info_ax = self._fig.add_axes([self._PANEL_X, 0.22, self._PANEL_W, 0.46])
        info_ax.set_facecolor("white")
        for spine in info_ax.spines.values():
            spine.set_edgecolor("lightgray")
        info_ax.set_xticks([])
        info_ax.set_yticks([])
        self._res_text = info_ax.text(
            0.05, 0.97, "", va="top", family="monospace", fontsize=10
        )

        # 凡例パネル
        legend_ax = self._fig.add_axes([self._PANEL_X, 0.70, self._PANEL_W, 0.18])
        legend_ax.set_facecolor("white")
        for spine in legend_ax.spines.values():
            spine.set_edgecolor("lightgray")
        legend_ax.set_xticks([])
        legend_ax.set_yticks([])
        legend_ax.set_xlim(0, 1)
        legend_ax.set_ylim(0, 1)
        self._draw_legend(legend_ax)

        # 回折モデル切り替えボタン
        ax_model = self._fig.add_axes([self._PANEL_X, 0.13, self._PANEL_W, 0.055])
        self._btn_model = Button(
            ax_model,
            self._model_label(),
            color="#E8F5E9",
            hovercolor="#C8E6C9",
        )
        self._btn_model.on_clicked(self._on_toggle_model)

        # 保存ボタン
        ax_save = self._fig.add_axes([self._PANEL_X, 0.06, self._PANEL_W, 0.055])
        self._btn_save = Button(ax_save, "SAVE PACKAGE", color="#E0E0E0", hovercolor="#D0D0D0")
        self._btn_save.on_clicked(self._on_save)

    def _draw_legend(self, ax) -> None:
        items = [
            (0.78, "#8B4513", 0.4, "Terrain"),
            (0.58, "green",   0.3, "Vegetation"),
            (0.22, "cyan",    0.25, "1st Fresnel Zone"),
        ]
        for y, color, alpha, label in items:
            ax.add_patch(plt.Rectangle((0.08, y), 0.12, 0.08, color=color, alpha=alpha))
            ax.text(0.28, y + 0.04, label, fontsize=10, va="center")

        # LoS 線
        ax.plot([0.08, 0.20], [0.40, 0.40], color="red", linestyle="--", lw=2)
        ax.text(0.28, 0.40, "Line of Sight", fontsize=10, va="center")

    def _build_sliders(self) -> None:
        ax_htx  = plt.axes([0.16, 0.17, 0.55, 0.03])
        ax_hrx  = plt.axes([0.16, 0.12, 0.55, 0.03])
        ax_rain = plt.axes([0.16, 0.07, 0.55, 0.03])
        self._slider_htx  = Slider(ax_htx,  "TX Height [m]",   0,   150, valinit=self._params.h_tx)
        self._slider_hrx  = Slider(ax_hrx,  "RX Height [m]",   0,   150, valinit=self._params.h_rx)
        self._slider_rain = Slider(ax_rain, "Rain Rate [mm/h]", 0.0, 100.0,
                                   valinit=self._params.rain_rate, valstep=1.0)

    def _build_dynamic_objects(self) -> None:
        """更新のたびに描き直すオブジェクトを初期化する。"""
        self._los_line, = self._ax.plot([], [], color="red", linestyle="--", lw=1.5)
        self._fresnel_fill = None
        self._antenna_bars = None

    # ----------------------------------------------------------
    # イベント接続
    # ----------------------------------------------------------
    def _connect_events(self) -> None:
        self._slider_htx.on_changed(self._request_update)
        self._slider_hrx.on_changed(self._request_update)
        self._slider_rain.on_changed(self._request_update)
        self._fig.canvas.mpl_connect("close_event", self._on_close)

    # ----------------------------------------------------------
    # 計算・描画更新
    # ----------------------------------------------------------
    def _request_update(self, _=None) -> None:
        """スライダー変更時に 50ms デバウンスしてから更新する。"""
        if self._pending_timer is not None:
            try:
                self._pending_timer.stop()
            except Exception:
                pass
            self._pending_timer = None

        timer = self._fig.canvas.new_timer(interval=50)
        timer.single_shot = True
        timer.add_callback(self._update_core)
        timer.start()
        self._pending_timer = timer

    def _update_core(self) -> None:
        if self._pending_timer is not None:
            try:
                self._pending_timer.stop()
            except Exception:
                pass
            self._pending_timer = None

        h_tx      = self._slider_htx.val
        h_rx      = self._slider_hrx.val
        rain_rate = self._slider_rain.val

        result = sim.run_calculation(self._terrain, h_tx, h_rx, self._params,
                                     rain_rate=rain_rate)
        self._last_result = result

        self._redraw_dynamic(h_tx, h_rx, result)
        self._update_panel(result)
        self._fig.canvas.draw_idle()

    def _redraw_dynamic(
        self,
        h_tx: float,
        h_rx: float,
        result: models.LinkBudgetResult,
    ) -> None:
        """LoS線・Fresnelゾーン・アンテナバーを描き直す。"""
        t     = self._terrain
        elevs = t.elevs_with_curve
        N     = t.num_samples

        tx_abs = float(elevs[0])  + h_tx
        rx_abs = float(elevs[-1]) + h_rx
        los_vals = np.linspace(tx_abs, rx_abs, N)

        self._los_line.set_data(t.d_km_axis, los_vals)

        # Fresnel ゾーン（再計算）
        lam  = 299792458 / (self._params.freq_mhz * 1e6)
        d_m  = t.d_km_axis * 1000
        tot  = t.horiz_dist_km * 1000
        d1   = np.maximum(d_m, 1.0)
        d2   = np.maximum(tot - d_m, 1.0)
        f1   = np.nan_to_num(np.sqrt(lam * d1 * d2 / (d1 + d2)))

        if self._fresnel_fill is not None:
            try:
                self._fresnel_fill.remove()
            except Exception:
                pass
        self._fresnel_fill = self._ax.fill_between(
            t.d_km_axis, los_vals - f1, los_vals + f1, color="cyan", alpha=0.25
        )

        # アンテナバー
        if self._antenna_bars is not None:
            try:
                self._antenna_bars.remove()
            except Exception:
                pass
        self._antenna_bars = self._ax.vlines(
            [0, t.horiz_dist_km],
            [float(elevs[0]),  float(elevs[-1])],
            [tx_abs, rx_abs],
            color="black", lw=3,
        )

    def _update_panel(self, r: models.LinkBudgetResult) -> None:
        model_label  = "Deygout" if r.diff_method == "deygout" else "Single"
        rain_label   = f"Rain Loss : {r.rain_loss:.1f} dB"
        rain_note    = " (*)" if self._params.freq_mhz < 6000.0 else ""
        self._res_text.set_text(
            "[Link Budget]\n"
            f"EIRP      : {r.eirp:.1f} dBm\n"
            f"FSPL      : {r.fspl:.1f} dB\n"
            f"Diff Loss : {r.diff_loss:.1f} dB\n"
            f"Veg Loss  : {r.veg_loss:.1f} dB\n"
            f"Env Loss  : {r.env_loss:.1f} dB\n"
            f"{rain_label}{rain_note}\n"
            f"Gas Loss  : {r.gas_loss:.1f} dB\n"
            f"Total Loss: {r.total_loss:.1f} dB\n"
            f"RX Ant.G  : +{self._params.gain_rx:.1f} dBi\n"
            "----------\n"
            f"RX Level  : {r.p_rx:.1f} dBm\n"
            f"Threshold : {self._params.sens:.1f} dBm\n"
            f"Act Margin: {r.actual_margin:.1f} dB\n"
            f"Status    : {r.status}\n"
            "\n"
            "[Environment]\n"
            f"Env Type  : {r.env_type.capitalize()}\n"
            f"Diff Model: {model_label}\n"
            f"K-Factor  : {r.current_k:.1f}\n"
            f"F1 Obs    : {r.blocked_ratio:.1f} %\n"
            f"Slant Dist: {r.slant_dist_km:.3f} km\n"
        )

    # ----------------------------------------------------------
    # 保存・クローズ
    # ----------------------------------------------------------
    def _model_label(self) -> str:
        if self._params.diff_method == "deygout":
            return "Diff: Deygout ✓"
        return "Diff: Single ✓"

    def _on_toggle_model(self, _) -> None:
        """回折モデルを single ↔ deygout で切り替えて再計算する。"""
        if self._params.diff_method == "single":
            self._params.diff_method = "deygout"
        else:
            self._params.diff_method = "single"
        self._btn_model.label.set_text(self._model_label())
        self._request_update()

    def _on_save(self, _) -> None:
        if self._last_result is None:
            messagebox.showwarning(
                "Not Ready",
                "シミュレーション結果がありません。\n先にRUN SIMULATIONを実行してください。",
            )
            return
        try:
            # スライダーの現在値を params に反映してから保存
            self._params.rain_rate = self._slider_rain.val
            save_dir = sim.save_package(
                fig     = self._fig,
                terrain = self._terrain,
                result  = self._last_result,
                params  = self._params,
                h_tx    = self._slider_htx.val,
                h_rx    = self._slider_hrx.val,
            )
            messagebox.showinfo("Saved", f"Package saved:\n{save_dir}")
        except Exception as e:
            logger.error("Save package failed: %s", e)
            messagebox.showerror("Save Error", str(e))

    def _on_close(self, _=None) -> None:
        if self._pending_timer is not None:
            try:
                self._pending_timer.stop()
            except Exception:
                pass
            self._pending_timer = None

    # ----------------------------------------------------------
    # 表示
    # ----------------------------------------------------------
    def show(self) -> None:
        """初期計算を実行し、ウィンドウを表示する（ブロッキング）。"""
        self._update_core()
        self._fig.canvas.draw()
        self._fig.canvas.flush_events()
        plt.show()
