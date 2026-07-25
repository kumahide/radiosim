"""
views/scenario.py
=================
条件探索ウィンドウ（2.5 / A-1 比較・A-2 スイープ）。

**4 つ目の実行フロー**の View。ヘッドレスの実行は [scenario.py](scenario.py)、
出力は [report_scenario.py](report_scenario.py) が担い、ここは入力欄・進捗・
結果の一覧表示だけを持つ（純コアのヘッドレス性を保つ継ぎ目）。

置き場の決定（2026-07-25・ユーザー選択）＝**独立した窓 1 つ**にタブで比較/スイープ
を同居させる。グラフ窓（既に 1000 行超）へ足すと肥り、バッチ窓へ足すと「N 本の
独立回線」と「1 本を掘る」というデータモデルが衝突する（設計哲学⑦）。

**レイアウトの原則（2026-07-25 の実機フィードバックで確定）**:
  - 入力（タブ）は**内容の高さに収める**＝タブを expand させると空白だけが伸びる。
  - 結果は **Treeview（固定高＋スクロール）**＝点数が増えても窓を縦に伸ばさない
    （41 点でも FHD に収まる）。以前は Label に全点を流し込み、点数次第で
    窓外へ溢れて保存ボタンまで見切れた。
  - 実行・レポートのボタンは**常に見える帯**（タブの外・下端固定）に置く。

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

# 結果一覧の見える行数（超えた分はスクロール）。窓の高さを点数から切り離す。
_RESULT_ROWS = 8

# 画面いっぱいまでは広げない（タスクバー・ウィンドウ枠のぶんを残す）。
# バッチ窓の _SCREEN_MARGIN と同じ考え方。
_SCREEN_MARGIN = 90


class ScenarioWindow(tk.Toplevel):
    """条件探索ウィンドウ（ランチャーが唯一のインスタンスを持つ）。"""

    _BASE_W = 900

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
        self.minsize(780, 520)

        self._base_params     = base_params
        self._config_provider = config_provider
        self._meta_provider   = meta_provider
        self._on_close_cb     = on_close
        self._running = False
        self._last_run: "scn.ScenarioRun | None" = None
        self._last_dir = ""

        self._pump = ProgressPump(self, self._dispatch_event)

        self._build()
        self._fit_initial_height()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _fit_initial_height(self) -> None:
        """**中身に合わせて開く**（比較モードは 11 行 × 列で背が高い）。

        高さを定数で決めると、結果一覧が潰れる（比較モードで実測）か、
        スイープモードで余白が出る。中身の要求高で開き、画面からははみ出さない。
        """
        self.update_idletasks()
        needed = self.winfo_reqheight()
        limit  = self.winfo_screenheight() - _SCREEN_MARGIN
        self.geometry(f"{self._BASE_W}x{min(needed, limit)}")

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

        # モード切替＝**表示中のフレームだけを pack する**。ttk.Notebook は
        # 「一番背の高いタブ」に合わせて全タブの高さが決まるため、行数の多い比較タブに
        # 引きずられてスイープ側に死んだ余白ができる（実機フィードバック）。
        # フレームを差し替えれば、空いた高さは下の結果一覧が使う。
        self._mode = tk.StringVar(value="compare")
        switch = ttk.Frame(outer)
        switch.pack(fill="x")
        for value, key in (("compare", "scn_tab_compare"), ("sweep", "scn_tab_sweep")):
            # sv_ttk の "Toggle.TButton" ＝押されている側が塗られるトグル外観。
            # 素の Radiobutton だと「ただの文字」に見えて切り替えと気づけない。
            ttk.Radiobutton(switch, text=i18n.t(key), value=value,
                            variable=self._mode, style="Toggle.TButton",
                            command=self._on_mode_changed).pack(
                                side="left", padx=(0, 6))

        self._panels = ttk.Frame(outer)
        self._panels.pack(fill="x", pady=(6, 0))
        self._compare_panel = self._build_compare_tab()
        self._sweep_panel   = self._build_sweep_tab()
        self._on_mode_changed()

        # 実行 & 進捗 & レポート（常に見える帯）
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(10, 6))
        self._run_btn = ttk.Button(bar, text=i18n.t("scn_run"), command=self._on_run,
                                   style="Accent.TButton")
        self._run_btn.pack(side="left")
        self._open_btn = ttk.Button(bar, text=i18n.t("scn_open_report"),
                                    command=self._open_report, state="disabled")
        self._open_btn.pack(side="left", padx=(6, 0))
        self._prog_label = ttk.Label(bar, text="")
        self._prog_label.pack(side="right")
        self._prog_bar = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self._prog_bar.pack(side="left", fill="x", expand=True, padx=10)

        # 結果一覧（残りの高さを使う＝点数が増えてもスクロールで収まる）
        self._result_box = ttk.LabelFrame(outer, text=i18n.t("scn_result"), padding=8)
        self._result_box.pack(fill="both", expand=True)
        self._summary_label = ttk.Label(self._result_box, text="")
        self._summary_label.pack(anchor="w", pady=(0, 6))

        cols = ("label", "rx", "margin", "status")
        self._tree = ttk.Treeview(self._result_box, columns=cols, show="headings",
                                  height=_RESULT_ROWS)
        headings = (i18n.t("scn_col_label"), i18n.t("html_rx_level") + " (dBm)",
                    i18n.t("html_act_margin") + " (dB)", i18n.t("html_status"))
        widths = (220, 130, 130, 80)
        for col, head, w in zip(cols, headings, widths):
            self._tree.heading(col, text=head)
            self._tree.column(col, width=w,
                              anchor="w" if col == "label" else "e", stretch=True)
        vsb = ttk.Scrollbar(self._result_box, orient="vertical",
                            command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    def _build_compare_tab(self) -> ttk.Frame:
        """ベース列（読み取り専用）＋比較条件 N 列（2026-07-25 要望で N 可変）。

        ベースはランチャーの現在値そのもの＝**基準を編集させない**（編集できると
        「何と比べたのか」が曖昧になる）。比較条件はベース値で初期化し、変えたい
        欄だけ触る使い方を想定する。
        """
        frame = ttk.Frame(self._panels, padding=(10, 8))
        self._cmp_grid = ttk.Frame(frame)
        self._cmp_grid.pack(anchor="w")

        # 列を足す/減らすボタン（上限は scenario.MAX_COMPARE_CONDITIONS）。
        btns = ttk.Frame(frame)
        btns.pack(anchor="w", pady=(8, 0))
        self._add_btn = ttk.Button(btns, text=i18n.t("scn_add_cond"),
                                  command=self._add_condition_column, width=14)
        self._add_btn.pack(side="left")
        self._del_btn = ttk.Button(btns, text=i18n.t("scn_del_cond"),
                                  command=self._remove_condition_column, width=14)
        self._del_btn.pack(side="left", padx=(6, 0))

        self._cmp_cols: list[dict[str, tk.StringVar]] = []
        self._build_compare_grid()
        self._add_condition_column()      # 既定は比較条件 1 列
        return frame

    def _build_compare_grid(self) -> None:
        """項目名の列とベース列（読み取り専用）を作る。"""
        ttk.Label(self._cmp_grid, text="").grid(row=0, column=0)
        ttk.Label(self._cmp_grid, text=i18n.t("scn_base")).grid(
            row=0, column=1, padx=6)
        for row, (key, _kind) in enumerate(_COMPARE_FIELDS, start=1):
            ttk.Label(self._cmp_grid, text=i18n.t(f"scn_axis_{key}")).grid(
                row=row, column=0, sticky="w", pady=1)
            ttk.Label(self._cmp_grid, text=str(getattr(self._base_params, key)),
                      anchor="e", width=11).grid(row=row, column=1, padx=6, pady=1)

    def _add_condition_column(self) -> None:
        if len(self._cmp_cols) >= scn.MAX_COMPARE_CONDITIONS:
            return
        col = len(self._cmp_cols) + 2         # 0=項目名 / 1=ベース
        ttk.Label(self._cmp_grid, text=i18n.t("scn_cond_n").format(n=col - 1)).grid(
            row=0, column=col, padx=6)
        cvars: dict[str, tk.StringVar] = {}
        for row, (key, kind) in enumerate(_COMPARE_FIELDS, start=1):
            var = tk.StringVar(value=str(getattr(self._base_params, key)))
            cvars[key] = var
            if kind == "num":
                w = ttk.Entry(self._cmp_grid, textvariable=var, width=11)
            else:
                values = _ENV_VALUES if kind == "env" else _DIFF_VALUES
                w = ttk.Combobox(self._cmp_grid, textvariable=var,
                                 values=list(values), state="readonly", width=9)
            w.grid(row=row, column=col, padx=6, pady=1)
        self._cmp_cols.append(cvars)
        self._sync_cond_buttons()

    def _remove_condition_column(self) -> None:
        if len(self._cmp_cols) <= 1:          # 比較対象ゼロにはしない
            return
        col = len(self._cmp_cols) + 1
        for w in self._cmp_grid.grid_slaves(column=col):
            w.destroy()
        self._cmp_cols.pop()
        self._sync_cond_buttons()

    def _sync_cond_buttons(self) -> None:
        n = len(self._cmp_cols)
        self._add_btn.config(
            state="disabled" if n >= scn.MAX_COMPARE_CONDITIONS else "normal")
        self._del_btn.config(state="disabled" if n <= 1 else "normal")

    def _build_sweep_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self._panels, padding=(10, 8))
        grid = ttk.Frame(frame)
        grid.pack(anchor="w")

        ttk.Label(grid, text=i18n.t("scn_axis")).grid(row=0, column=0, sticky="w")
        self._axis_labels = {i18n.t(f"scn_axis_{a}"): a for a in scn.SWEEP_AXES}
        self._axis_box = ttk.Combobox(
            grid, values=list(self._axis_labels), state="readonly", width=20)
        self._axis_box.set(i18n.t("scn_axis_h_tx"))
        self._axis_box.grid(row=0, column=1, padx=6, pady=3, sticky="w")

        self._from_var   = tk.StringVar(value="10")
        self._to_var     = tk.StringVar(value="60")
        self._points_var = tk.StringVar(value="11")
        for i, (label, var) in enumerate((
            (i18n.t("scn_from"), self._from_var),
            (i18n.t("scn_to"), self._to_var),
            (i18n.t("scn_points"), self._points_var),
        ), start=1):
            ttk.Label(grid, text=label).grid(row=i, column=0, sticky="w", pady=2)
            ttk.Entry(grid, textvariable=var, width=12).grid(
                row=i, column=1, padx=6, pady=2, sticky="w")

        ttk.Label(frame, text=i18n.t("scn_err_points").format(
            max=scn.MAX_SWEEP_POINTS)).pack(anchor="w", pady=(8, 0))
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

    def _on_mode_changed(self) -> None:
        """選ばれたモードのパネルだけを表示する（余白を作らない）。"""
        for panel in (self._compare_panel, self._sweep_panel):
            panel.pack_forget()
        panel = (self._compare_panel if self._mode.get() == "compare"
                 else self._sweep_panel)
        panel.pack(fill="x")

    def _conditions(self) -> tuple[list[scn.Condition], str, list[float]]:
        """モードに応じた条件列を作る（入力エラーは ValueError で投げる）。"""
        if self._mode.get() == "compare":
            return self._compare_conditions(), "", []
        return self._sweep_conditions()

    def _compare_conditions(self) -> list[scn.Condition]:
        """ベース（上書きなし）＋比較条件 N 個。基準は常に先頭。"""
        conds = [scn.Condition(label=i18n.t("scn_base"), overrides={})]
        for i, cvars in enumerate(self._cmp_cols, start=1):
            overrides: dict[str, float | str] = {}
            for key, kind in _COMPARE_FIELDS:
                raw = cvars[key].get().strip()
                overrides[key] = raw if kind != "num" else float(raw)
            conds.append(scn.Condition(
                label=i18n.t("scn_cond_n").format(n=i), overrides=overrides))
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
        self._tree.delete(*self._tree.get_children())
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
        self._fill_results(run)

        # 単一・バッチと同じ流儀＝保存先を告げてから開くか尋ねる
        # （実機フィードバック：ここだけダイアログが出ないのは挙動が揃っていない）。
        if dialogs.confirm(self, i18n.t("dlg_saved_title"),
                           i18n.t("scn_saved_msg").format(dir=self._last_dir)):
            self._open_report()

    def _fill_results(self, run: scn.ScenarioRun) -> None:
        """結果一覧を埋める（点数が多くてもスクロールで収まる）。"""
        self._result_box.config(
            text=i18n.t("scn_sweep_title") if run.kind == "sweep"
            else i18n.t("scn_compare_title"))
        summary = (f'{i18n.t("html_horiz_dist")}: '
                   f'{units.format_distance(run.terrain.horiz_dist_km)}')
        idx = run.first_ok_index()
        if run.kind == "sweep" and idx >= 0:
            summary += "　/　" + i18n.t("scn_first_ok").format(
                value=run.points[idx].label)
        self._summary_label.config(text=summary)

        self._tree.delete(*self._tree.get_children())
        self._tree.tag_configure("ok", foreground="#2e7d32")
        self._tree.tag_configure("ng", foreground="#c62828")
        for p in run.points:
            self._tree.insert("", "end", values=(
                p.label, f"{p.result.p_rx:.2f}", f"{p.result.actual_margin:+.2f}",
                p.result.status,
            ), tags=("ok" if p.ok else "ng",))

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
