"""
views/graph.py
==============
グラフウィンドウ（matplotlib）の構築・スライダー操作・保存。

計算ロジックは simulation.run_calculation() に委譲する。
このファイルは「表示」と「ユーザー操作の受け取り」のみを担う。
"""

import gc
import logging
import os

import matplotlib
matplotlib.use("TkAgg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle
from matplotlib.widgets import Button, Slider, TextBox
from tkinter import messagebox

import batch
import i18n
import infrastructure as infra
import models
import mpl_fonts
import simulation as sim
import version

logger = logging.getLogger("radiosim")


def _apply_font() -> None:
    """日本語フォントを matplotlib に適用する（ヘッドレス共通ヘルパへ委譲）。"""
    mpl_fonts.apply_japanese_font()


def show_graph(params: sim.SimParams, raw_elevs: np.ndarray) -> None:
    """
    地形断面グラフウィンドウを開く（ブロッキング）。

    Args:
        params:    シミュレーションパラメータ
        raw_elevs: 取得済み生標高配列
    """
    _apply_font()
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
    # 等価地球曲率注記を出す最小経路長 [km]（これ未満はふくらみが視認できず注記不要）
    _CURVE_NOTE_MIN_KM = 30.0

    def __init__(self, params: sim.SimParams, terrain: models.TerrainProfile) -> None:
        self._params  = params
        self._terrain = terrain
        self._last_result: models.LinkBudgetResult | None = None
        self._pending_timer = None

        self._fig, self._ax = plt.subplots(figsize=(15, 8))
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

        self._ax.set_title(f"{version.APP_NAME} ({self._params.freq_mhz} MHz)")
        self._ax.set_xlabel(i18n.t("graph_dist_axis"))
        self._ax.set_ylabel(i18n.t("graph_alt_axis"))
        self._ax.grid(True, alpha=0.2)

        # 等価地球曲率補正で地形が実標高から乖離するため、ふくらみが視認できる
        # 距離（≈30km〜）でのみ「補正済み座標」と明示し、実地形との誤読を防ぐ。
        if t.horiz_dist_km >= self._CURVE_NOTE_MIN_KM:
            bulge = float(np.max(t.elevs_with_curve - t.raw_elevs))
            # 縦倍率＝見かけの誇張の主因。横軸 数万m を縦軸 数百m と同程度の画面
            # 幅に詰めるため曲率のふくらみがドーム状に見える。軸の描画ピクセルは
            # 図サイズと axes 位置から算出（draw 前でも確定・リサイズ前提の概算）。
            fig_w_in, fig_h_in = self._fig.get_size_inches()
            pos = self._ax.get_position()
            w_px = fig_w_in * self._fig.dpi * pos.width
            h_px = fig_h_in * self._fig.dpi * pos.height
            vexag = models.vertical_exaggeration(
                t.horiz_dist_km * 1000.0,
                float(np.max(veg_top)) - y_min,
                w_px, h_px,
            )
            self._ax.text(
                0.012, 0.985,
                i18n.t("graph_curve_note").format(
                    k=t.earth_k, bulge=bulge, vexag=vexag
                ),
                transform=self._ax.transAxes, va="top", ha="left",
                fontsize=8, style="italic", color="0.45",
            )

    def _build_panels(self) -> None:
        # リンクバジェットパネル（[Diff Model] 行を含めた高さに拡張）
        info_ax = self._fig.add_axes((self._PANEL_X, 0.20, self._PANEL_W, 0.50))
        info_ax.set_facecolor("0.95")
        for spine in info_ax.spines.values():
            spine.set_edgecolor("lightgray")
        info_ax.set_xticks([])
        info_ax.set_yticks([])
        self._res_text = info_ax.text(
            0.05, 0.97, "", va="top", family="monospace", fontsize=10
        )

        # 凡例パネル
        legend_ax = self._fig.add_axes((self._PANEL_X, 0.70, self._PANEL_W, 0.18))
        legend_ax.set_facecolor("0.95")
        for spine in legend_ax.spines.values():
            spine.set_edgecolor("lightgray")
        legend_ax.set_xticks([])
        legend_ax.set_yticks([])
        legend_ax.set_xlim(0, 1)
        legend_ax.set_ylim(0, 1)
        self._draw_legend(legend_ax)

        # 回折モデル切り替えボタン
        ax_model = self._fig.add_axes((self._PANEL_X, 0.13, self._PANEL_W, 0.055))
        self._btn_model = Button(
            ax_model,
            self._model_label(),
            color="lightgrey",
            hovercolor="white",
        )
        self._btn_model.on_clicked(self._on_toggle_model)

        # 保存ボタン
        ax_save = self._fig.add_axes((self._PANEL_X, 0.06, self._PANEL_W, 0.055))
        self._btn_save = Button(ax_save, i18n.t("btn_save_pkg"), color="lightgrey", hovercolor="white")
        self._btn_save.on_clicked(self._on_save)

    def _draw_legend(self, ax) -> None:
        items = [
            (0.78, "#8B4513", 0.4, i18n.t("legend_terrain")),
            (0.58, "green",   0.3, i18n.t("legend_vegetation")),
            (0.22, "cyan",    0.25, i18n.t("legend_fresnel")),
        ]
        for y, color, alpha, label in items:
            ax.add_patch(Rectangle((0.08, y), 0.12, 0.08, color=color, alpha=alpha))
            ax.text(0.28, y + 0.04, label, fontsize=10, va="center")

        # LoS 線
        ax.plot([0.08, 0.20], [0.40, 0.40], color="red", linestyle="--", lw=2)
        ax.text(0.28, 0.40, i18n.t("legend_los"), fontsize=10, va="center")

    def _build_sliders(self) -> None:
        ax_htx  = plt.axes((0.16, 0.17, 0.55, 0.03))
        ax_hrx  = plt.axes((0.16, 0.12, 0.55, 0.03))
        ax_rain = plt.axes((0.16, 0.07, 0.55, 0.03))
        self._slider_htx  = Slider(ax_htx,  i18n.t("slider_htx"),  0,   150, valinit=self._params.h_tx)
        self._slider_hrx  = Slider(ax_hrx,  i18n.t("slider_hrx"),  0,   150, valinit=self._params.h_rx)
        self._slider_rain = Slider(ax_rain, i18n.t("slider_rain"), 0.0, 100.0,
                                   valinit=self._params.rain_rate, valstep=1.0)
        self._slider_htx.valtext.set_visible(False)
        self._slider_hrx.valtext.set_visible(False)
        self._slider_rain.valtext.set_visible(False)

        # 数値直接入力 TextBox（スライダー右横）幅は最大値 "150.0" の5文字に合わせて統一
        ax_tb_htx  = plt.axes((0.715, 0.17, 0.040, 0.03))
        ax_tb_hrx  = plt.axes((0.715, 0.12, 0.040, 0.03))
        ax_tb_rain = plt.axes((0.715, 0.07, 0.040, 0.03))
        self._tb_htx  = TextBox(ax_tb_htx,  "", initial=f"{self._params.h_tx:.1f}")
        self._tb_hrx  = TextBox(ax_tb_hrx,  "", initial=f"{self._params.h_rx:.1f}")
        self._tb_rain = TextBox(ax_tb_rain, "", initial=f"{self._params.rain_rate:.0f}")
        for tb in (self._tb_htx, self._tb_hrx, self._tb_rain):
            tb.text_disp.set_horizontalalignment("right")
            tb.text_disp.set_x(0.975)

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
        self._tb_htx.on_submit(
            lambda t: self._on_tb_submit(t, self._slider_htx, self._tb_htx, 0.0, 150.0)
        )
        self._tb_hrx.on_submit(
            lambda t: self._on_tb_submit(t, self._slider_hrx, self._tb_hrx, 0.0, 150.0)
        )
        self._tb_rain.on_submit(
            lambda t: self._on_tb_submit(t, self._slider_rain, self._tb_rain, 0.0, 100.0, ".0f")
        )
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

        # スライダーに合わせて TextBox 値を同期（set_val は on_submit を発火しない）
        self._tb_htx.set_val(f"{h_tx:.1f}")
        self._tb_hrx.set_val(f"{h_rx:.1f}")
        self._tb_rain.set_val(f"{rain_rate:.0f}")

        result = sim.run_calculation(self._terrain, h_tx, h_rx, self._params,
                                     rain_rate=rain_rate)
        self._last_result = result

        self._redraw_dynamic(h_tx, h_rx)
        self._update_panel(result)
        self._fig.canvas.draw()  # draw() not draw_idle() — no pending after-callbacks to orphan

    def _redraw_dynamic(
        self,
        h_tx: float,
        h_rx: float,
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
        f1 = models.fresnel_zone_radii(t.d_km_axis, t.horiz_dist_km, self._params.freq_mhz)

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
        import unicodedata

        def _dw(s: str) -> int:
            return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)

        model_label = i18n.t("html_model_deygout") if r.diff_method == "deygout" else i18n.t("html_model_single")
        env_label   = i18n.t(f"env_{r.env_type}")   # HTML レポートと同じ言語連動ラベル

        budget_rows: list[tuple[str, str]] = [
            (i18n.t("pl_eirp"),      f"{r.eirp:8.1f} dBm"),
            (i18n.t("pl_fspl"),      f"{r.fspl:8.1f} dB"),
            (i18n.t("pl_diff_loss"), f"{r.diff_loss:8.1f} dB"),
            (i18n.t("pl_veg_loss"),  f"{r.veg_loss:8.1f} dB"),
            (i18n.t("pl_env_loss"),  f"{r.env_loss:8.1f} dB"),
            (i18n.t("pl_rain_loss"), f"{r.rain_loss:8.1f} dB"),
            (i18n.t("pl_gas_loss"),  f"{r.gas_loss:8.1f} dB"),
            (i18n.t("pl_total_loss"),f"{r.total_loss:8.1f} dB"),
            (i18n.t("pl_rx_ant_g"), f"{self._params.gain_rx:+8.1f} dBi"),
        ]
        # _ONES_W: {:8.1f} の一の位は position 5 → テキストを width 6 で右揃えすると右端が揃う
        _ONES_W = 6
        sep_rows: list[tuple[str, str]] = [
            (i18n.t("pl_rx_level"),   f"{r.p_rx:8.1f} dBm"),
            (i18n.t("pl_threshold"),  f"{self._params.sens:8.1f} dBm"),
            (i18n.t("pl_act_margin"), f"{r.actual_margin:8.1f} dB"),
        ]
        status_rows: list[tuple[str, str]] = [
            (i18n.t("pl_status"),     f"{r.status:>{_ONES_W}}"),
        ]
        val_w = max(len(val) for _, val in budget_rows + sep_rows + status_rows)

        env_rows: list[tuple[str, str]] = [
            (i18n.t("pl_env_type"),   f"{env_label:>{_ONES_W}}"),
            (i18n.t("pl_diff_model"), f"{model_label:>{_ONES_W}}"),
            (i18n.t("pl_k_factor"),   f"{r.current_k:8.1f}"),
            (i18n.t("pl_f1_obs"),     f"{r.blocked_ratio:8.1f} %"),
            (i18n.t("pl_slant_dist"), f"{r.slant_dist_km:8.3f} km"),
        ]

        w = max(_dw(label) for label, _ in budget_rows + sep_rows + status_rows + env_rows)

        def fmt(rows: list[tuple[str, str]]) -> str:
            return "\n".join(f"{label}{' ' * (w - _dw(label))}: {val}" for label, val in rows)

        sep = "-" * (w + 2 + val_w)
        self._res_text.set_text(
            f"[{i18n.t('panel_link_budget')}]\n"
            + fmt(budget_rows) + "\n"
            + sep + "\n"
            + fmt(sep_rows) + "\n"
            + sep + "\n"
            + fmt(status_rows) + "\n"
            "\n"
            f"[{i18n.t('panel_environment')}]\n"
            + fmt(env_rows) + "\n"
        )

    # ----------------------------------------------------------
    # 保存・クローズ
    # ----------------------------------------------------------
    def _on_tb_submit(
        self,
        text:   str,
        slider: Slider,
        tb:     TextBox,
        vmin:   float,
        vmax:   float,
        fmt:    str = ".1f",
    ) -> None:
        """TextBox に入力された値を検証してスライダーに反映する。
        slider.set_val → on_changed → _request_update と連鎖するため
        ここでは _request_update を呼ばない。"""
        try:
            val = max(vmin, min(vmax, float(text)))
            slider.set_val(val)
        except ValueError:
            tb.set_val(f"{slider.val:{fmt}}")
            self._fig.canvas.draw()

    def _model_label(self) -> str:
        if self._params.diff_method == "deygout":
            return i18n.t("diff_deygout")
        return i18n.t("diff_single")

    def _on_toggle_model(self, _) -> None:
        """回折モデルを single ↔ deygout で切り替えて再計算する。"""
        if self._params.diff_method == "single":
            self._params.diff_method = "deygout"
        else:
            self._params.diff_method = "single"
        self._btn_model.label.set_text(self._model_label())
        self._request_update()

    def _on_save(self, _) -> None:
        self._update_core()   # flush any pending slider changes before saving
        if self._last_result is None:
            messagebox.showwarning(
                i18n.t("dlg_not_ready_title"),
                i18n.t("dlg_not_ready_msg"),
            )
            return
        try:
            self._params.rain_rate = self._slider_rain.val
            h_tx = self._slider_htx.val
            h_rx = self._slider_hrx.val
            # 座標表記は app 設定に従う（人が読む report.txt のみ。データは DD 固定）
            coord_format = infra.load_config().get("coord_format", "dd")
            save_dir = sim.save_package(
                fig     = self._fig,
                terrain = self._terrain,
                result  = self._last_result,
                params  = self._params,
                h_tx    = h_tx,
                h_rx    = h_rx,
                coord_format = coord_format,
            )
            batch.save_profile_png(
                self._terrain, self._last_result, self._params,
                h_tx, h_rx, save_dir, coord_format,
            )
            batch.save_path_kml(
                self._terrain, self._last_result, self._params,
                h_tx, h_rx, save_dir,
            )
            if messagebox.askyesno(
                i18n.t("dlg_saved_title"),
                i18n.t("dlg_saved_msg").format(dir=save_dir),
            ):
                os.startfile(os.path.join(save_dir, "report.html"))
        except Exception as e:
            logger.error("Save package failed: %s", e)
            messagebox.showerror(i18n.t("dlg_save_error"), str(e))

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
        gc.collect()  # force cycle GC in main thread; prevents Python 3.14 bg-thread __del__
