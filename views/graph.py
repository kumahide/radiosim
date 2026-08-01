"""
views/graph.py
==============
地形断面ウィンドウ（**Tk の Toplevel**）。

計算ロジックは simulation.run_calculation() に委譲する。
このファイルは「表示」と「ユーザー操作の受け取り」のみを担う。

なぜ Tk なのか（2.6a1 / B-024）
--------------------------------
以前はこの窓が**丸ごと matplotlib の figure** で、スライダー・数値入力・保存
ボタン・リンクバジェットのパネルまで figure の中に置いていた。レイアウトが
figure 相対座標（`subplots_adjust` / `add_axes`）なのに**文字は pt 固定**なので、
窓を小さくすると枠だけが縮んで文字が縮まず、要素が重なった（B-024＝凡例の
はみ出し・保存ボタンとパネルの重なり・軸ラベルとスライダーの食い込み）。

処方は「フォントを窓の大きさに追従させる」ではなく**症状を生む構造を消す**：

  - **matplotlib はプロット領域だけ**を担当する（地形・植生・LoS・F1・アンテナ
    バー・軸・曲率注記・凡例）。
  - **操作系とリンクバジェットは Tk（ttk）へ出す**＝重なっていた 4 箇所のうち
    3 つは Tk のジオメトリマネージャが逃がすので**構造的に消える**。凡例は
    matplotlib 標準の legend が自前で畳む。

一緒に片付いたもの:

  1. **横断ゲートの傘に入った**＝本物の Toplevel になったので
     `window_fit.refit_all`（Toplevel 総なめ）と `theme` が自動で効く。
     B-015 型の再発（窓は追従するのに文字が追従しない）が構造的に潰れる。
  2. **`plt.show()` の入れ子 mainloop が消えた**＝ブロックしないので、
     呼び出し元の「準備中」表示を戻すための `on_ready` フックが要らなくなった。
  3. リンクバジェットが Tk のラベルになり、**東アジア文字幅を数えて桁を揃える
     処理が丸ごと不要**になった（数値を選択してコピーできる副産物つき）。

⚠️ **図はライト固定**（テーマに追従させない・2026-07-28 決定）。理由＝
`report.html` / `profile.png` は常にライトなので**画面と成果物の見た目が
一致する**（⑧）。地形 `#8B4513`・植生 `green`・LoS 赤・F1 シアンは白背景を
前提に選んだ色で、暗背景にするとコントラスト比のゲートを 2 系統ぶん通し直す
ことになる。**白いのは手抜きではなく「印刷される成果物のプレビュー」**という
意図なので、枠と余白でカードらしく見せる。
"""

from __future__ import annotations

import logging
import os
import tkinter as tk
from tkinter import ttk
from typing import Callable

import matplotlib

matplotlib.use("TkAgg")

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

import config
import i18n
import models
import mpl_fonts
import report_path
import simulation as sim
import units
from views import dialogs, window_fit

logger = logging.getLogger("radiosim")

# 図の配色（**ライト固定**＝上記の理由）。レポート図と同じ値を使う。
_TERRAIN_COLOR = "#8B4513"
_VEG_COLOR     = "green"
_LOS_COLOR     = "red"
_F1_COLOR      = "cyan"


def show_graph(
    parent: tk.Misc,
    params: sim.SimParams,
    raw_elevs: np.ndarray,
    project_name: str = "",
    memo: str = "",
    on_close: "Callable[[], None] | None" = None,
) -> "GraphWindow":
    """地形断面ウィンドウを開く（**ブロックしない**）。

    Args:
        parent:       親ウィンドウ（ランチャーの root）。
        params:       シミュレーションパラメータ
        raw_elevs:    取得済み生標高配列
        project_name: レポートの案件名（ランチャーから踏襲・空可）
        memo:         レポートの自由メモ（ランチャーから踏襲・空可）
        on_close:     窓が閉じられたときに呼ばれる（呼び出し元の参照を外す用）。
    """
    mpl_fonts.apply_japanese_font()
    terrain = models.calculate_terrain_profile(
        raw_elevs = raw_elevs,
        lat_tx    = params.lat_tx,
        lon_tx    = params.lon_tx,
        lat_rx    = params.lat_rx,
        lon_rx    = params.lon_rx,
    )
    return GraphWindow(parent, params, terrain, project_name, memo, on_close)


class GraphWindow(tk.Toplevel):
    """地形断面と what-if 操作の窓（ランチャーが唯一のインスタンスを持つ）。"""

    # 開いたときの下限サイズ。**中身の要求サイズではない**＝図は要求を小さく
    # 持たせて窓に合わせて伸縮させるので、ここは「気持ちよく見える初期値」。
    _BASE_W, _BASE_H = 1040, 660
    # 等価地球曲率注記を出す最小経路長 [km]（これ未満はふくらみが視認できず注記不要）
    _CURVE_NOTE_MIN_KM = 30.0
    # what-if スライダーの値域（従来の Slider と同じ）。
    _H_RANGE    = (0.0, 150.0)
    _RAIN_RANGE = (0.0, 100.0)

    def __init__(
        self,
        parent: tk.Misc,
        params: sim.SimParams,
        terrain: models.TerrainProfile,
        project_name: str = "",
        memo: str = "",
        on_close: "Callable[[], None] | None" = None,
    ) -> None:
        super().__init__(parent)
        # タイトルはソフト名を名乗らず**周波数だけ**（I-034）。レポート図が既に
        # そうしており、画面側だけが揃っていなかった。
        self.title(f"{params.freq_mhz} MHz")

        self._params  = params
        self._terrain = terrain
        self._report_project = project_name
        self._report_memo    = memo
        self._on_close_cb = on_close
        self._last_result: "models.LinkBudgetResult | None" = None
        self._pending: "str | None" = None

        self._build()
        self._update_core()
        self._fit_to_content()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------------
    # 組み立て
    # ----------------------------------------------------------
    def _build(self) -> None:
        body = window_fit.scrollable_body(self, padding=8)

        main = ttk.Frame(body)
        main.pack(fill="both", expand=True)
        self._build_plot(main)
        self._build_panel(main)
        self._build_controls(body)

    def _build_plot(self, parent: tk.Misc) -> None:
        """プロット領域（matplotlib はここだけを担当する）。

        ⚠️ **`pyplot` を使わない**＝pyplot は自前で Tk の窓とグローバルな図の
        レジストリを持つ（それが入れ子 mainloop と `plt.close("all")` の後始末を
        呼んでいた）。`Figure` + `FigureCanvasTkAgg` なら普通の Tk ウィジェット
        として親の寿命に従う。

        figsize は**小さめ**に取る＝これが Tk への「要求サイズ」になるので、
        大きく取ると窓を縮めたときに図が縮まずスクロールへ逃げてしまう
        （プロットは縮んでほしい側）。実際の表示サイズは窓に追従する。
        """
        # 枠と余白で「印刷される成果物のプレビュー」に見せる（図はライト固定）。
        card = ttk.Frame(parent, relief="solid", borderwidth=1, padding=1)
        card.pack(side="left", fill="both", expand=True)

        self._fig = Figure(figsize=(5.0, 3.0), dpi=100, facecolor="white")
        self._ax  = self._fig.add_subplot(111, facecolor="white")
        # 余白は figure 相対で持つが、**中に置くのは軸だけ**になったので
        # 上下左右とも詰められる（I-035＝上部余白が広すぎる、はここで解ける。
        # 従来は top=0.88／bottom=0.26／right=0.77 で操作系とパネルの場所を
        # 空けていた）。
        # 上だけは凡例のぶんを空ける（`_build_legend` が軸の外・上へ出す）。
        self._fig.subplots_adjust(left=0.08, right=0.98, top=0.89, bottom=0.14)

        self._canvas = FigureCanvasTkAgg(self._fig, master=card)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)

        self._build_static_terrain()
        self._build_dynamic_objects()
        self._build_legend()

    def _build_panel(self, parent: tk.Misc) -> None:
        """リンクバジェット・伝搬環境（**Tk のラベル**＝figure の中に描かない）。

        figure 内の monospace テキストだったころは、東アジア文字の表示幅を数えて
        桁を揃える処理（`_dw` と `{:8.1f}` の手合わせ）が必要だった。grid の列に
        任せればその塊はまるごと不要になり、テーマ・DPI にも追従し、数値を選択して
        コピーできるようになる。

        ⚠️ **数値が `savefig` に写らなくなるのは許容**＝もともと成果物には写って
        いない（画面図の保存はレポート専用図に上書きされていた＝I-036）。数値は
        `report.html` / `report.txt` / `settings.json` が担っており失われない。
        """
        side = ttk.Frame(parent, padding=(8, 0, 0, 0))
        side.pack(side="left", fill="y")

        self._vars: dict[str, tk.StringVar] = {}
        budget = ttk.LabelFrame(side, text=i18n.t("panel_link_budget"), padding=(8, 4))
        budget.pack(fill="x")
        self._panel_rows(budget, (
            ("eirp",       "pl_eirp",       "dBm"),
            ("fspl",       "pl_fspl",       "dB"),
            ("diff_loss",  "pl_diff_loss",  "dB"),
            ("veg_loss",   "pl_veg_loss",   "dB"),
            ("env_loss",   "pl_env_loss",   "dB"),
            ("rain_loss",  "pl_rain_loss",  "dB"),
            ("gas_loss",   "pl_gas_loss",   "dB"),
            ("total_loss", "pl_total_loss", "dB"),
            ("gain_rx",    "pl_rx_ant_g",   "dBi"),
            (None,         None,            ""),   # 区切り
            ("p_rx",       "pl_rx_level",   "dBm"),
            ("sens",       "pl_threshold",  "dBm"),
            ("margin",     "pl_act_margin", "dB"),
            (None,         None,            ""),
            ("status",     "pl_status",     ""),
        ))

        env = ttk.LabelFrame(side, text=i18n.t("panel_environment"), padding=(8, 4))
        env.pack(fill="x", pady=(6, 0))
        self._panel_rows(env, (
            ("env_type",   "pl_env_type",   ""),
            ("diff_model", "pl_diff_model", ""),
            ("k_factor",   "pl_k_factor",   ""),
            ("f1_obs",     "pl_f1_obs",     "%"),
            ("slant",      "pl_slant_dist", "m"),
        ))

    def _panel_rows(self, parent: ttk.LabelFrame, rows) -> None:
        """`(キー, i18n ラベルキー, 単位)` の並びを **3 列**の grid にする。

        **単位を値と別の列にするのが桁揃えの本体**（2026-08-01 実機フィードバック）。
        `-76.4 dBm` と `12.3 dB` を 1 つの文字列にして右寄せすると、**揃うのは
        単位の右端**で小数点は揃わない。値の列だけを右寄せし、単位は左寄せの
        別列に固定すると、数字の桁がそのまま縦に並ぶ。

        ⚠️ **等幅フォントは持ち込まない**（書体の出所が増える＝B-015/B-026 の
        再来）。数字の幅は本文書体のまま、列で揃える。
        """
        parent.columnconfigure(1, weight=1)
        for r, (key, label_key, unit) in enumerate(rows):
            if key is None:
                ttk.Separator(parent, orient="horizontal").grid(
                    row=r, column=0, columnspan=3, sticky="ew", pady=3)
                continue
            ttk.Label(parent, text=i18n.t(label_key)).grid(
                row=r, column=0, sticky="w", padx=(0, 10))
            var = tk.StringVar()
            self._vars[key] = var
            ttk.Label(parent, textvariable=var, anchor="e").grid(
                row=r, column=1, sticky="e")
            if unit:
                ttk.Label(parent, text=unit).grid(
                    row=r, column=2, sticky="w", padx=(4, 0))

    def _build_controls(self, parent: tk.Misc) -> None:
        """what-if の操作系（スライダー＋数値入力）と保存ボタン。

        ⚠️ 回折モデルの切替は**置かない**＝ランチャー（source of truth）でのみ
        選ぶ。ここに置くと `_params.diff_method` をその場で書き換えられ、
        「ランチャーで Single を選んだのに保存レポートは Deygout」という齟齬を
        作れる（2.5 で撤去済み）。読み取り専用の表示はパネルが担う。
        """
        bar = ttk.Frame(parent, padding=(0, 8, 0, 0))
        bar.pack(fill="x")
        bar.columnconfigure(1, weight=1)

        self._scales:  dict[str, ttk.Scale]    = {}
        self._entries: dict[str, ttk.Entry]    = {}
        self._values:  dict[str, tk.StringVar] = {}
        self._fmt:     dict[str, str]          = {}
        for r, (key, label_key, rng, fmt, init) in enumerate((
            ("h_tx",  "slider_htx",  self._H_RANGE,    ".1f", self._params.h_tx),
            ("h_rx",  "slider_hrx",  self._H_RANGE,    ".1f", self._params.h_rx),
            ("rain",  "slider_rain", self._RAIN_RANGE, ".0f", self._params.rain_rate),
        )):
            ttk.Label(bar, text=i18n.t(label_key)).grid(
                row=r, column=0, sticky="w", padx=(0, 8), pady=2)
            scale = ttk.Scale(bar, from_=rng[0], to=rng[1], value=init,
                              command=lambda _v, k=key: self._on_scale(k))
            scale.grid(row=r, column=1, sticky="ew", pady=2)
            var = tk.StringVar(value=format(init, fmt))
            entry = ttk.Entry(bar, textvariable=var, width=7, justify="right")
            entry.grid(row=r, column=2, sticky="w", padx=(8, 0), pady=2)
            # Enter でも フォーカスを外したときでも確定する（片方だけだと
            # 「入力したのに反映されない」に見える）。
            entry.bind("<Return>",   lambda _e, k=key: self._on_entry(k))
            entry.bind("<FocusOut>", lambda _e, k=key: self._on_entry(k))
            self._scales[key], self._entries[key], self._values[key] = scale, entry, var
            self._fmt[key] = fmt

        # 保存は 3 行ぶんの高さを持つ 1 個（右端）。width を与えないと日本語 2 文字が
        # 縦に潰れて読めない。
        ttk.Button(bar, text=i18n.t("btn_save_pkg"), command=self._on_save,
                   width=12).grid(row=0, column=3, rowspan=3, sticky="ns",
                                  padx=(12, 0), pady=2)

    def _fit_to_content(self) -> None:
        """窓を中身に合わせる（**下限は「見やすい初期値」**）。

        ⚠️ リサイズ不可にはしない（判断ごと残す）＝①拡大して地形を細かく見る
        操作を殺す（グラフ窓は拡大の需要がある数少ない窓）②実機 FHD の高さ上限
        990px や高 DPI で入らないときの逃げ道が無くなる。
        """
        window_fit.fit_to_content(self, min_w=self._BASE_W, min_h=self._BASE_H)

    # ----------------------------------------------------------
    # 静的描画（起動時 1 回のみ）
    # ----------------------------------------------------------
    def _build_static_terrain(self) -> None:
        t = self._terrain
        y_min = float(np.min(t.raw_elevs)) - 30
        veg_top = t.elevs_with_curve + self._params.veg_h

        # 距離軸は表示のみ m へ換算する（内部・物理式は km 据え置き＝units 参照）。
        d_m = units.km_to_m(t.d_km_axis)

        self._ax.fill_between(d_m, t.elevs_with_curve, y_min,
                              color=_TERRAIN_COLOR, alpha=0.4)
        self._ax.fill_between(d_m, veg_top, t.elevs_with_curve,
                              color=_VEG_COLOR, alpha=0.3)

        self._ax.set_xlabel(i18n.t("graph_dist_axis"))
        self._ax.set_ylabel(i18n.t("graph_alt_axis"))
        self._ax.grid(True, alpha=0.2)

        # 等価地球曲率補正で地形が実標高から乖離するため、ふくらみが視認できる
        # 距離（≈30km〜）でのみ「補正済み座標」と明示し、実地形との誤読を防ぐ。
        if t.horiz_dist_km >= self._CURVE_NOTE_MIN_KM:
            bulge = float(np.max(t.elevs_with_curve - t.raw_elevs))
            # 縦倍率＝見かけの誇張の主因。横軸 数万m を縦軸 数百m と同程度の画面
            # 幅に詰めるため曲率のふくらみがドーム状に見える。
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
                    k=t.earth_k, bulge=bulge, vexag=vexag),
                transform=self._ax.transAxes, va="top", ha="left",
                fontsize=8, style="italic", color="0.45",
            )

    def _build_dynamic_objects(self) -> None:
        """更新のたびに描き直すオブジェクトを初期化する。"""
        self._los_line, = self._ax.plot([], [], color=_LOS_COLOR,
                                        linestyle="--", lw=1.5)
        self._fresnel_fill = None
        self._antenna_bars = None

    def _build_legend(self) -> None:
        """matplotlib 標準の legend（figure 内の自作パネルはやめた）。

        レポート図が既に標準 legend を使っており、画面だけ自作パネルだった＝⑧。
        標準 legend は**窓が小さくなると自分で畳む**ので、B-024 の「凡例が枠を
        突き抜ける」は処方の副産物として消える。

        **位置は軸の外・上（横 1 列）＝レポート図と同じ**（2026-08-01 実機
        フィードバック）。図の中の右上に置くと、**RX 側のアンテナ支柱と受信端が
        必ずそこに来る**ので凡例に隠れる（経路は左下→右上に描かれるため、右上は
        構造的に一番混む場所）。`loc="best"` は「毎回どこに出るか分からない」
        ＝⑧（一貫性は人の速度）と衝突するので採らない。

        ハンドルは**代理（proxy）**で作る＝F1 ゾーンは更新のたびに描き直す
        （`remove()` → `fill_between`）ので、実オブジェクトを渡すと legend が
        破棄済みのハンドルを指す。
        """
        self._ax.legend(
            handles=[
                Patch(facecolor=_TERRAIN_COLOR, alpha=0.4, label=i18n.t("legend_terrain")),
                Patch(facecolor=_VEG_COLOR, alpha=0.3, label=i18n.t("legend_vegetation")),
                Line2D([], [], color=_LOS_COLOR, linestyle="--", lw=1.5,
                       label=i18n.t("legend_los")),
                Patch(facecolor=_F1_COLOR, alpha=0.25, label=i18n.t("legend_fresnel")),
            ],
            # レポート図（report_path.save_profile_png）と同じ指定。
            loc="lower right", bbox_to_anchor=(1.0, 1.02), ncol=4,
            fontsize=9, framealpha=0.9, borderaxespad=0,
        )

    # ----------------------------------------------------------
    # 操作 → 再計算（デバウンス）
    # ----------------------------------------------------------
    def _on_scale(self, key: str) -> None:
        """スライダーを動かした＝数値欄へ写して再計算を予約する。"""
        self._values[key].set(format(self._scales[key].get(), self._fmt[key]))
        self._request_update()

    def _on_entry(self, key: str) -> None:
        """数値欄で確定した＝値域へ丸めてスライダーへ写す。

        読めない値は**直前の値へ戻す**（黙って 0 にしない＝入力ミスで計算条件が
        変わるほうが害が大きい）。
        """
        lo, hi = self._H_RANGE if key != "rain" else self._RAIN_RANGE
        try:
            val = max(lo, min(hi, float(self._values[key].get())))
        except ValueError:
            self._values[key].set(format(self._scales[key].get(), self._fmt[key]))
            return
        self._scales[key].set(val)          # → _on_scale が走り再計算まで連鎖する

    def _request_update(self) -> None:
        """50ms デバウンスしてから再計算する（ドラッグ中の連打を畳む）。"""
        if self._pending is not None:
            try:
                self.after_cancel(self._pending)
            except tk.TclError:
                pass
        self._pending = self.after(50, self._update_core)

    def _update_core(self) -> None:
        self._pending = None
        h_tx = self._scales["h_tx"].get()
        h_rx = self._scales["h_rx"].get()
        rain = self._scales["rain"].get()

        result = sim.run_calculation(self._terrain, h_tx, h_rx, self._params,
                                     rain_rate=rain)
        self._last_result = result
        self._redraw_dynamic(h_tx, h_rx)
        self._update_panel(result)
        self._canvas.draw_idle()

    def _redraw_dynamic(self, h_tx: float, h_rx: float) -> None:
        """LoS 線・Fresnel ゾーン・アンテナバーを描き直す。"""
        t     = self._terrain
        elevs = t.elevs_with_curve
        tx_abs = float(elevs[0])  + h_tx
        rx_abs = float(elevs[-1]) + h_rx
        los_vals = np.linspace(tx_abs, rx_abs, t.num_samples)

        d_m = units.km_to_m(t.d_km_axis)
        self._los_line.set_data(d_m, los_vals)

        f1 = models.fresnel_zone_radii(t.d_km_axis, t.horiz_dist_km,
                                       self._params.freq_mhz)
        if self._fresnel_fill is not None:
            self._fresnel_fill.remove()
        self._fresnel_fill = self._ax.fill_between(
            d_m, los_vals - f1, los_vals + f1, color=_F1_COLOR, alpha=0.25)

        if self._antenna_bars is not None:
            self._antenna_bars.remove()
        self._antenna_bars = self._ax.vlines(
            [0, units.km_to_m(t.horiz_dist_km)],
            [float(elevs[0]), float(elevs[-1])],
            [tx_abs, rx_abs], color="black", lw=3)

    def _update_panel(self, r: models.LinkBudgetResult) -> None:
        """パネルの数値を更新する。

        ⚠️ **単位を値に混ぜない**（単位は `_panel_rows` が別列に固定している）。
        混ぜると右寄せしても小数点が揃わない＝桁揃えが崩れる。
        """
        p = self._params
        self._vars["eirp"].set(f"{r.eirp:.1f}")
        for key, value in (
            ("fspl", r.fspl), ("diff_loss", r.diff_loss), ("veg_loss", r.veg_loss),
            ("env_loss", r.env_loss), ("rain_loss", r.rain_loss),
            ("gas_loss", r.gas_loss), ("total_loss", r.total_loss),
        ):
            self._vars[key].set(f"{value:.1f}")
        self._vars["gain_rx"].set(f"{p.gain_rx:+.1f}")
        self._vars["p_rx"].set(f"{r.p_rx:.1f}")
        self._vars["sens"].set(f"{p.sens:.1f}")
        self._vars["margin"].set(f"{r.actual_margin:.1f}")
        self._vars["status"].set(r.status)

        self._vars["env_type"].set(i18n.t(f"env_{r.env_type}"))
        self._vars["diff_model"].set(
            i18n.t("html_model_deygout") if r.diff_method == "deygout"
            else i18n.t("html_model_single"))
        self._vars["k_factor"].set(f"{r.current_k:.1f}")
        # 単位は列が持つので `unit=False`（この 2 つは units.py が書式の出所）。
        self._vars["f1_obs"].set(
            units.format_blocked_ratio(r.blocked_ratio, unit=False))
        self._vars["slant"].set(units.format_distance(r.slant_dist_km, unit=False))

    # ----------------------------------------------------------
    # 保存・クローズ
    # ----------------------------------------------------------
    def _on_save(self) -> None:
        if self._pending is not None:      # 予約済みの再計算を先に片付ける
            self._update_core()
        if self._last_result is None:
            dialogs.alert(self, i18n.t("dlg_not_ready_title"),
                          i18n.t("dlg_not_ready_msg"))
            return
        try:
            self._params.rain_rate = self._scales["rain"].get()
            h_tx = self._scales["h_tx"].get()
            h_rx = self._scales["h_rx"].get()
            # 座標表記は app 設定に従う（人が読む report.txt のみ。データは DD 固定）
            coord_format = config.load_config().get("coord_format", "dd")
            save_dir = sim.save_package(
                terrain = self._terrain,
                result  = self._last_result,
                params  = self._params,
                h_tx    = h_tx,
                h_rx    = h_rx,
                coord_format = coord_format,
            )
            # `profile.png` は**レポート専用図**が書く（画面の図は保存しない）。
            # 以前は save_package が画面の図を書いた直後にここが同じパスを
            # 上書きしており、1 回目は捨てられていた（I-036）。
            report_path.save_profile_png(
                self._terrain, self._last_result, self._params,
                h_tx, h_rx, save_dir, coord_format,
                self._report_project, self._report_memo,
            )
            report_path.save_path_kml(
                self._terrain, self._last_result, self._params,
                h_tx, h_rx, save_dir,
            )
            # 単一・バッチ・条件探索で同じ流儀＝保存先を告げ、レポートか
            # フォルダかを選ばせる（I-030。恒久ボタンを外した代わりの受け皿）。
            choice = dialogs.choose(
                self, i18n.t("dlg_saved_title"),
                i18n.t("dlg_saved_msg").format(dir=save_dir),
                [("report", i18n.t("dlg_open_report")),
                 ("folder", i18n.t("dlg_open_folder"))],
            )
            if choice == "report":
                os.startfile(os.path.join(save_dir, "report.html"))
            elif choice == "folder":
                os.startfile(save_dir)
        except Exception as e:
            logger.error("Save package failed: %s", e)
            dialogs.alert(self, i18n.t("dlg_save_error"), str(e))

    def _on_close(self) -> None:
        if self._pending is not None:
            try:
                self.after_cancel(self._pending)
            except tk.TclError:
                pass
            self._pending = None
        if self._on_close_cb is not None:
            self._on_close_cb()
        self.destroy()
