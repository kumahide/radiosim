"""
views/scenario.py
=================
条件探索ウィンドウ（2.5 / A-1 比較・A-2 スイープ）。

**4 つ目の実行フロー**の View。ヘッドレスの実行は [scenario.py](scenario.py)、
出力は [report_scenario.py](report_scenario.py) が担い、ここは入力欄・進捗・
結果の要約表示だけを持つ（純コアのヘッドレス性を保つ継ぎ目）。

置き場の決定（2026-07-25・ユーザー選択）＝**独立した窓 1 つ**にタブで比較/スイープ
を同居させる。グラフ窓（既に 1000 行超）へ足すと肥り、バッチ窓へ足すと「N 本の
独立回線」と「1 本を掘る」というデータモデルが衝突する（設計哲学⑦）。

進捗は [views/progress.ProgressPump](views/progress.py) で受け、相の切り替え
（取得 → 計算 → レポート生成）はランナー側の宣言に従うだけ＝**重い相が管轄外に
置かれない**（B-006／I-008 の構造対策）。

⚠️ 素の tk ウィジェットは sv_ttk のテーマに追従しないので、**新規はすべて ttk**
で作る（[[feedback_radiosim_rules]]）。
"""

from __future__ import annotations

import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Callable

import config
import i18n
import report_scenario
import scenario as scn
import simulation as sim
import units
from views import dialogs
from views.progress import ProgressPump

# 比較タブで編集できる項目（順に並ぶ）。値は文字列で持ち、実行時に変換する
# （バッチのランチャー凍結と同じ流儀＝入力途中の値でパースを走らせない）。
_COMPARE_FIELDS: tuple[tuple[str, str], ...] = (
    ("freq_mhz", "num"), ("p_tx", "num"), ("gain_tx", "num"), ("gain_rx", "num"),
    ("sens", "num"), ("h_tx", "num"), ("h_rx", "num"), ("veg_h", "num"),
    ("rain_rate", "num"), ("env_type", "env"), ("diff_method", "diff"),
)

_ENV_VALUES  = ("los", "rural", "suburban", "urban")
_DIFF_VALUES = ("deygout", "single")


class ScenarioWindow(tk.Toplevel):
    """条件探索ウィンドウ（ランチャーが唯一のインスタンスを持つ）。"""

    _BASE_W = 860
    _BASE_H = 620

    def __init__(
        self,
        parent: tk.Tk,
        base_params: sim.SimParams,
        config_provider: "Callable[[], dict] | None" = None,
        meta_provider:   "Callable[[], dict] | None" = None,
        on_close:        "Callable[[], None] | None" = None,
    ) -> None:
        super().__init__(parent)
        self.title(i18n.t("scn_window_title"))
        self.geometry(f"{self._BASE_W}x{self._BASE_H}")
        self.minsize(760, 560)

        self._base_params     = base_params
        self._config_provider = config_provider
        self._meta_provider   = meta_provider
        self._on_close_cb     = on_close
        self._running = False
        self._last_run: "scn.ScenarioRun | None" = None
        self._last_dir = ""

        self._pump = ProgressPump(self, self._dispatch_event)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------------
    # 組み立て
    # ----------------------------------------------------------
    def _build(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        # 経路（固定された前提）＝ランチャーのスナップショット。
        p = self._base_params
        self._path_label = ttk.Label(
            outer,
            text=(f'{i18n.t("scn_fixed_path")}: '
                  f'{p.lat_tx:.5f}, {p.lon_tx:.5f} → {p.lat_rx:.5f}, {p.lon_rx:.5f}'
                  f'　/　{i18n.t("scn_samples")}: {p.num}'),
        )
        self._path_label.pack(anchor="w", pady=(0, 8))

        self._tabs = ttk.Notebook(outer)
        self._tabs.pack(fill="both", expand=True)
        self._tabs.add(self._build_compare_tab(), text=i18n.t("scn_tab_compare"))
        self._tabs.add(self._build_sweep_tab(), text=i18n.t("scn_tab_sweep"))

        # 実行 & 進捗
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(10, 4))
        self._run_btn = ttk.Button(bar, text=i18n.t("scn_run"), command=self._on_run)
        self._run_btn.pack(side="left")
        self._prog_bar = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self._prog_bar.pack(side="left", fill="x", expand=True, padx=10)
        self._prog_label = ttk.Label(bar, text="")
        self._prog_label.pack(side="left")

        # 結果の要約（実行後に埋まる）
        self._result_box = ttk.LabelFrame(outer, text=i18n.t("scn_compare_title"),
                                          padding=8)
        self._result_box.pack(fill="both", expand=False, pady=(6, 0))
        self._result_label = ttk.Label(self._result_box, text="", justify="left")
        self._result_label.pack(anchor="w")
        self._open_btn = ttk.Button(self._result_box, text=i18n.t("scn_save"),
                                    command=self._open_report, state="disabled")
        self._open_btn.pack(anchor="w", pady=(8, 0))

    def _build_compare_tab(self) -> ttk.Frame:
        """条件 A / B を縦に並べた差分入力（値はランチャーの現在値で初期化）。"""
        frame = ttk.Frame(self._tabs, padding=10)
        ttk.Label(frame, text="").grid(row=0, column=0)
        ttk.Label(frame, text=i18n.t("scn_cond_a")).grid(row=0, column=1, padx=6)
        ttk.Label(frame, text=i18n.t("scn_cond_b")).grid(row=0, column=2, padx=6)

        self._cmp_vars: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        for row, (key, kind) in enumerate(_COMPARE_FIELDS, start=1):
            ttk.Label(frame, text=i18n.t(f"scn_axis_{key}")).grid(
                row=row, column=0, sticky="w", pady=2)
            init = getattr(self._base_params, key)
            va, vb = tk.StringVar(value=str(init)), tk.StringVar(value=str(init))
            self._cmp_vars[key] = (va, vb)
            for col, var in ((1, va), (2, vb)):
                if kind == "num":
                    ttk.Entry(frame, textvariable=var, width=12).grid(
                        row=row, column=col, padx=6, pady=2)
                else:
                    values = _ENV_VALUES if kind == "env" else _DIFF_VALUES
                    ttk.Combobox(frame, textvariable=var, values=list(values),
                                 state="readonly", width=10).grid(
                        row=row, column=col, padx=6, pady=2)
        return frame

    def _build_sweep_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self._tabs, padding=10)
        ttk.Label(frame, text=i18n.t("scn_axis")).grid(row=0, column=0, sticky="w")
        self._axis_var = tk.StringVar(value="h_tx")
        self._axis_labels = {i18n.t(f"scn_axis_{a}"): a for a in scn.SWEEP_AXES}
        self._axis_box = ttk.Combobox(
            frame, values=list(self._axis_labels), state="readonly", width=20)
        self._axis_box.set(i18n.t("scn_axis_h_tx"))
        self._axis_box.grid(row=0, column=1, padx=6, pady=4, sticky="w")

        self._from_var   = tk.StringVar(value="10")
        self._to_var     = tk.StringVar(value="60")
        self._points_var = tk.StringVar(value="11")
        for col, (label, var) in enumerate((
            (i18n.t("scn_from"), self._from_var),
            (i18n.t("scn_to"), self._to_var),
            (i18n.t("scn_points"), self._points_var),
        )):
            ttk.Label(frame, text=label).grid(row=1 + col, column=0, sticky="w", pady=2)
            ttk.Entry(frame, textvariable=var, width=12).grid(
                row=1 + col, column=1, padx=6, pady=2, sticky="w")

        ttk.Label(
            frame,
            text=i18n.t("scn_err_points").format(max=scn.MAX_SWEEP_POINTS),
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        return frame

    # ----------------------------------------------------------
    # 実行
    # ----------------------------------------------------------
    def _current_base(self) -> sim.SimParams:
        """ランチャーの現在値で base を作り直す（開いている間の変更に追従）。"""
        if self._config_provider is None:
            return self._base_params
        try:
            return sim.SimParams(self._config_provider())
        except Exception:
            return self._base_params

    def _conditions(self) -> tuple[list[scn.Condition], str, list[float]]:
        """タブに応じた条件列を作る（入力エラーは ValueError で投げる）。"""
        if self._tabs.index(self._tabs.select()) == 0:
            return self._compare_conditions(), "", []
        return self._sweep_conditions()

    def _compare_conditions(self) -> list[scn.Condition]:
        conds = []
        for label_key, col in ((i18n.t("scn_cond_a"), 0), (i18n.t("scn_cond_b"), 1)):
            overrides: dict[str, float | str] = {}
            for key, kind in _COMPARE_FIELDS:
                raw = self._cmp_vars[key][col].get().strip()
                overrides[key] = raw if kind != "num" else float(raw)
            conds.append(scn.Condition(label=label_key, overrides=overrides))
        return conds

    def _sweep_conditions(self) -> tuple[list[scn.Condition], str, list[float]]:
        axis = self._axis_labels[self._axis_box.get()]
        start, stop = float(self._from_var.get()), float(self._to_var.get())
        if start == stop:
            raise ValueError(i18n.t("scn_err_range"))
        points = int(self._points_var.get())
        values = scn.linspace_values(start, stop, points)
        return scn.sweep_conditions(axis, values), axis, values

    def _on_run(self) -> None:
        if self._running:
            return
        try:
            conditions, axis, values = self._conditions()
        except ValueError as ex:
            dialogs.alert(self, i18n.t("dlg_input_error"), str(ex))
            return

        base = self._current_base()
        kind = "sweep" if axis else "compare"
        meta = self._meta_provider() if self._meta_provider else {}
        project = str(meta.get("project_name", ""))
        memo    = str(meta.get("memo", ""))

        save_dir = os.path.join(
            config.RESULTS_DIR,
            f"scenario_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        os.makedirs(save_dir, exist_ok=True)
        self._last_dir = save_dir

        self._running = True
        self._run_btn.config(state="disabled")
        self._open_btn.config(state="disabled")
        self._prog_bar.config(value=0)
        self._prog_label.config(text=i18n.t("scn_phase_fetch"))
        self._pump.start()
        push = self._pump.push

        # 成果物生成はランナーの RENDER 相＝ワーカースレッドで走る
        # （GUI を固めず、その時間も進捗率に乗る）。
        def _artifacts(run: scn.ScenarioRun) -> None:
            report_scenario.save_scenario_package(run, save_dir, project, memo)

        scn.run_scenario(
            base, conditions,
            on_complete = lambda run, p=push: p(("complete", (run,))),
            on_error    = lambda ex,  p=push: p(("error", (ex,))),
            kind        = kind,
            axis        = axis,
            axis_values = values,
            on_phase    = lambda name, p=push: p(("phase", (name,))),
            on_progress = lambda pct,  p=push: p(("progress", (pct,))),
            artifacts   = _artifacts,
        )

    # ----------------------------------------------------------
    # コールバック（メインスレッド）
    # ----------------------------------------------------------
    def _dispatch_event(self, item: tuple) -> None:
        event, args = item
        if event == "progress":
            self._prog_bar.config(value=args[0])
        elif event == "phase":
            self._prog_label.config(text=i18n.t(f"scn_phase_{args[0]}"))
        elif event == "complete":
            self._on_complete(*args)
        elif event == "error":
            self._on_error(*args)

    def _on_complete(self, run: scn.ScenarioRun) -> None:
        self._running = False
        self._pump.stop()
        self._run_btn.config(state="normal")
        self._prog_bar.config(value=100)
        self._prog_label.config(text="")
        self._last_run = run
        self._open_btn.config(state="normal")
        self._result_box.config(
            text=i18n.t("scn_sweep_title") if run.kind == "sweep"
            else i18n.t("scn_compare_title"))
        self._result_label.config(text=self._summary_text(run))

    def _summary_text(self, run: scn.ScenarioRun) -> str:
        """窓に出す要約（詳細は生成した A4 レポートが持つ）。"""
        lines = [
            f'{i18n.t("html_horiz_dist")}: '
            f'{units.format_distance(run.terrain.horiz_dist_km)}',
        ]
        for p in run.points:
            lines.append(
                f'  {p.label:<10}  {p.result.p_rx:9.2f} dBm  '
                f'{p.result.actual_margin:+8.2f} dB  {p.result.status}')
        idx = run.first_ok_index()
        if run.kind == "sweep" and idx >= 0:
            lines.append(i18n.t("scn_first_ok").format(value=run.points[idx].label))
        return "\n".join(lines)

    def _on_error(self, ex: Exception) -> None:
        self._running = False
        self._pump.stop()
        self._run_btn.config(state="normal")
        self._prog_bar.config(value=0)
        self._prog_label.config(text="")
        dialogs.alert(self, i18n.t("dlg_error"), str(ex))

    def _open_report(self) -> None:
        if not self._last_dir:
            dialogs.alert(self, i18n.t("dlg_input_error"), i18n.t("scn_err_no_result"))
            return
        os.startfile(os.path.join(self._last_dir, "scenario.html"))

    # ----------------------------------------------------------
    def _on_close(self) -> None:
        """閉じるときはポンプを止めてから破棄する。

        実行中に閉じると破棄済みウィジェットへ `after` し続ける経路が生まれる
        （2.4b3 で単一/バッチともに塞いだのと同じクラス）。
        """
        self._pump.stop()
        if self._on_close_cb:
            self._on_close_cb()
        self.destroy()
