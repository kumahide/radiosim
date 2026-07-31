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
  - 項目名は**単位つき**（出所は report_scenario の単位表＝画面とレポートで表記が
    ずれない）、入力欄の幅は**文字数でなく grid の列**に決めさせる、結果表の余白は
    [views/theme.table_style](views/theme.py) から取る（2026-07-26 実機 FB）。

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
from views import dialogs, theme, window_fit
from views.progress import ProgressPump

# 比較タブで編集できる項目（順に並ぶ）。値は文字列で持ち、実行時に変換する
# （バッチのランチャー凍結と同じ流儀＝入力途中の値でパースを走らせない）。
_COMPARE_FIELDS: tuple[tuple[str, str], ...] = (
    ("freq_mhz", "num"), ("p_tx", "num"), ("gain_tx", "num"), ("gain_rx", "num"),
    ("sens", "num"), ("h_tx", "num"), ("h_rx", "num"), ("veg_h", "num"),
    ("rain_rate", "num"), ("env_type", "env"), ("diff_method", "diff"),
)

# 選択肢は**内部キーで持ち、表示は i18n ラベル**（ランチャーと同じ流儀）。
# 以前はキー（los / deygout …）をそのまま見せており、ja でも英語のままだった。
_ENV_KEYS  = ("los", "rural", "suburban", "urban")
_DIFF_KEYS = ("deygout", "single")


def _axis_label(key: str) -> str:
    """項目名の表示（**単位つき**）。

    単位の出所は report_scenario.AXIS_UNITS ＝ レポート（HTML/PNG/CSV）と同じ
    一覧を使う（2026-07-26 の実機フィードバック：画面だけ単位が無く、dBm と dBi と
    m が同じ見た目の数字で並んでいて何を入れる欄か分からない）。
    **画面とレポートで単位表記がずれない**ことが、出所を1つにする理由。
    """
    return report_scenario.axis_label(key)


def _number(text: str, label: str) -> float:
    """入力欄の文字列を数値へ。読めない値は**その欄の名前つき**で弾く。

    素の `float()` に任せると Python 生の英語（`could not convert string to
    float: 'abc'`）がダイアログに出る＝言語設定にも [[feedback_japanese_everywhere]]
    にも従わない（B-016）。`nan` / `inf` は `float()` を通ってしまうので、
    値域と一緒に config.validate_value 側で弾く。
    """
    try:
        return float(text)
    except ValueError:
        raise ValueError(i18n.t("scn_err_value").format(
            label=label, reason=i18n.t("err_numeric"), value=repr(text))) from None


def _env_labels() -> dict[str, str]:
    return {k: i18n.t(f"env_{k}") for k in _ENV_KEYS}


def _diff_labels() -> dict[str, str]:
    return {k: i18n.t(f"diff_opt_{k}") for k in _DIFF_KEYS}

# 結果一覧の見える行数（超えた分はスクロール）。窓の高さを点数から切り離す。
_RESULT_ROWS = 8


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
        # 案件情報（案件名・自由メモ）も**ランチャーのスナップショット**として持つ。
        # ここで凍結し、↻ で明示的に取り込み直す（実行時に読み直さない＝下記）。
        self._meta: dict[str, str] = self._snapshot_meta()
        self._on_close_cb     = on_close
        self._running = False
        self._last_run: "scn.ScenarioRun | None" = None
        self._last_dir = ""

        self._pump = ProgressPump(self, self._dispatch_event)

        self._build()
        self._fit_to_content()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _snapshot_meta(self) -> dict[str, str]:
        """ランチャーの案件情報を取り込む（**この窓が持つのは常にこの写し**）。"""
        meta = self._meta_provider() if self._meta_provider else {}
        return {
            "project_name": str(meta.get("project_name", "")),
            "memo":         str(meta.get("memo", "")),
        }

    def _update_meta_label(self) -> None:
        """案件情報の表示を、いま持っているスナップショットに合わせる。

        **なぜ表示するか**（2026-07-26・ユーザー指摘）：案件名とメモはレポートの
        自己同定ヘッダに刻印されるのに、この窓には出ていなかった。しかも実行の
        瞬間にランチャーを読み直していたので、**画面に無い値が成果物に載る**
        （窓を開いたあとにランチャー側で案件名を変えると、気づく手段が無い）。
        経路と同じく「凍結して見せる」に揃える。
        """
        dash = "—"
        self._meta_label.config(
            text=(f'{i18n.t("batch_project_name")}: '
                  f'{self._meta["project_name"] or dash}'
                  f'　/　{i18n.t("batch_memo")}: {self._meta["memo"] or dash}'))

    def _update_path_label(self) -> None:
        p = self._base_params
        self._path_label.config(
            text=(f'{i18n.t("scn_fixed_path")}: '
                  f'{p.lat_tx:.5f}, {p.lon_tx:.5f} → {p.lat_rx:.5f}, {p.lon_rx:.5f}'
                  f'　/　{i18n.t("scn_samples")}: {p.num}'))

    def _refresh_from_launcher(self) -> None:
        """ランチャーの現在値（**座標を含む**）を取り込み直す。

        取り込むのは経路表示とベース列、そして比較条件の初期値。条件列で編集済みの
        値まで巻き戻すと編集内容が消えるので、**ベースと同じ値だった欄だけ**を
        追従させる（触った欄はユーザーの意図として残す）。
        """
        # 案件情報は座標と独立に取り込む（`config_provider` の有無に引きずらない）。
        self._meta = self._snapshot_meta()
        self._update_meta_label()
        if self._config_provider is None:
            return
        try:
            new_base = sim.SimParams(self._config_provider())
        except Exception as ex:
            dialogs.alert(self, i18n.t("dlg_input_error"), str(ex))
            return
        old_base = self._base_params
        self._base_params = new_base
        self._update_path_label()
        for key, _kind in _COMPARE_FIELDS:
            new_val = getattr(new_base, key)
            self._base_vars[key].set(self._display(key, new_val))
            for cvars in self._cmp_cols:
                if cvars[key].get() == self._display(key, getattr(old_base, key)):
                    cvars[key].set(self._display(key, new_val))

    @staticmethod
    def _display(key: str, value) -> str:
        """内部値 → 画面表示（環境・回折モデルは i18n ラベル）。"""
        if key == "env_type":
            return _env_labels().get(str(value), str(value))
        if key == "diff_method":
            return _diff_labels().get(str(value), str(value))
        return str(value)

    @staticmethod
    def _to_value(key: str, text: str) -> "float | str":
        """画面表示 → 内部値（i18n ラベルをキーへ戻す）。"""
        if key == "env_type":
            return {v: k for k, v in _env_labels().items()}.get(text, text)
        if key == "diff_method":
            return {v: k for k, v in _diff_labels().items()}.get(text, text)
        return _number(text, _axis_label(key))

    def _fit_to_content(self) -> None:
        """**中身に合わせて開き、足りなくなったら広げる**（縮めない）。

        高さ：定数で決めると結果一覧が潰れる（比較モード）かスイープで余白が出る。
        幅　：`_BASE_W` は**下限**でしかない。比較条件は最大 5 列まで増やせるので、
        幅を固定すると **「条件を追加」で生やした右端の列が窓外へ出て見えなくなる**
        （I-024・2026-07-26 の実機フィードバックで実際に条件 5 が見切れた）。列を
        増減するたびに測り直す。

        測り方そのものは [views/window_fit](views/window_fit.py) に集約してある
        （見切れは窓ごとに直しては再発してきたクラスなので、実装を 1 つにする）。
        """
        window_fit.fit_to_content(self, min_w=self._BASE_W)

    # ----------------------------------------------------------
    # 組み立て
    # ----------------------------------------------------------
    def _build(self) -> None:
        # 中身はスクロールの受け皿の中へ（B-023＝B-021② と同一パス）。この窓は
        # 100% では入るが 125% で 1095px、150% で 1223px を要求し、実機（FHD）の
        # 使える高さ 990px を超える。外周 padding は受け皿の内側へ移す（外に残すと
        # スクロール領域の外で下端を隠す）。
        outer = window_fit.scrollable_body(self, padding=10)

        # 経路（固定された前提）＝ランチャーのスナップショット。
        # ⚠️ **実行はここに出ている値で行う**（黙って読み直さない）。以前は実行時に
        # config_provider を読み直しており、ランチャーで座標を変えると「画面の経路と
        # 計算した経路が違う」状態になり得た。更新は ↻ ボタンで明示的に行う
        # （バッチの Common Settings と同じ流儀＝ランチャーが source of truth）。
        head = ttk.Frame(outer)
        head.pack(fill="x", pady=(0, 8))
        self._path_label = ttk.Label(head, text="")
        self._path_label.pack(side="left")
        ttk.Button(head, text=i18n.t("scn_refresh"), width=18,
                   command=self._refresh_from_launcher).pack(side="right", padx=(12, 0))
        self._update_path_label()

        # 案件情報＝経路と同じ「ランチャーからの凍結値」。レポートに刻印される値を
        # 画面にも出す（補助情報なので落として見せる）。
        self._meta_label = ttk.Label(
            outer, text="", foreground=theme.muted_foreground(outer))
        self._meta_label.pack(fill="x", pady=(0, 8))
        self._update_meta_label()

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
        self._prog_label = ttk.Label(bar, text="")
        self._prog_label.pack(side="right")
        self._prog_bar = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self._prog_bar.pack(side="left", fill="x", expand=True, padx=10)

        # 結果一覧（残りの高さを使う＝点数が増えてもスクロールで収まる）
        self._result_box = ttk.LabelFrame(outer, text=i18n.t("scn_result"), padding=8)
        self._result_box.pack(fill="both", expand=True)
        self._summary_label = ttk.Label(self._result_box, text="")
        self._summary_label.pack(anchor="w", pady=(0, 6))

        # 余白つきの表スタイル（既定は行が詰まり、文字が枠・列境界に張り付く）。
        cols = ("label", "rx", "margin", "status")
        self._tree = ttk.Treeview(self._result_box, columns=cols, show="headings",
                                  height=_RESULT_ROWS,
                                  style=theme.table_style(self))
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
        """項目名の列とベース列（読み取り専用・↻ で更新される）を作る。"""
        ttk.Label(self._cmp_grid, text="").grid(row=0, column=0)
        ttk.Label(self._cmp_grid, text=i18n.t("scn_base"), anchor="center").grid(
            row=0, column=1, padx=6, sticky="ew")
        self._base_vars: dict[str, tk.StringVar] = {}
        for row, (key, _kind) in enumerate(_COMPARE_FIELDS, start=1):
            ttk.Label(self._cmp_grid, text=_axis_label(key)).grid(
                row=row, column=0, sticky="w", pady=1)
            var = tk.StringVar(value=self._display(key, getattr(self._base_params, key)))
            self._base_vars[key] = var
            ttk.Label(self._cmp_grid, textvariable=var,
                      anchor="e", width=11).grid(row=row, column=1, padx=6, pady=1,
                                                 sticky="ew")

    def _add_condition_column(self) -> None:
        if len(self._cmp_cols) >= scn.MAX_COMPARE_CONDITIONS:
            return
        col = len(self._cmp_cols) + 2         # 0=項目名 / 1=ベース
        ttk.Label(self._cmp_grid, text=i18n.t("scn_cond_n").format(n=col - 1),
                  anchor="center").grid(row=0, column=col, padx=6, sticky="ew")
        cvars: dict[str, tk.StringVar] = {}
        for row, (key, kind) in enumerate(_COMPARE_FIELDS, start=1):
            var = tk.StringVar(
                value=self._display(key, getattr(self._base_params, key)))
            cvars[key] = var
            if kind == "num":
                w = ttk.Entry(self._cmp_grid, textvariable=var, width=11)
            else:
                labels = (_env_labels() if kind == "env" else _diff_labels())
                w = ttk.Combobox(self._cmp_grid, textvariable=var,
                                 values=list(labels.values()),
                                 state="readonly", width=9)
            # sticky="ew" ＝ **列の幅に合わせて広げる**。Entry(width=11) と
            # Combobox(width=9＋矢印ボタン) は同じ width 指定でも実幅が揃わないので、
            # 幅は grid の列（＝その列で一番広いウィジェット）に決めさせる
            # （2026-07-26 の実機フィードバック：欄の右端がガタついて見える）。
            w.grid(row=row, column=col, padx=6, pady=1, sticky="ew")
        self._cmp_cols.append(cvars)
        self._sync_cond_buttons()
        self._fit_to_content()

    def _remove_condition_column(self) -> None:
        if len(self._cmp_cols) <= 1:          # 比較対象ゼロにはしない
            return
        col = len(self._cmp_cols) + 1
        for w in self._cmp_grid.grid_slaves(column=col):
            w.destroy()
        self._cmp_cols.pop()
        self._sync_cond_buttons()
        self._fit_to_content()

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
        self._axis_labels = {_axis_label(a): a for a in scn.SWEEP_AXES}
        self._axis_box = ttk.Combobox(
            grid, values=list(self._axis_labels), state="readonly", width=24)
        self._axis_box.set(_axis_label("h_tx"))
        # sticky="ew" ＝ **列の幅に合わせて広げる**（比較タブと同じ流儀＝I-021）。
        # 軸の Combobox は width=24、開始/終了/点数の Entry は width=12 で、
        # 文字数で揃えても実幅は一致しない（Combobox は矢印ボタンぶん広い）。
        # 幅は grid の列（＝その列で一番広いウィジェット）に決めさせる。
        self._axis_box.grid(row=0, column=1, padx=6, pady=3, sticky="ew")

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
                row=i, column=1, padx=6, pady=2, sticky="ew")

        ttk.Label(frame, text=i18n.t("scn_err_points").format(
            max=scn.MAX_SWEEP_POINTS)).pack(anchor="w", pady=(8, 0))
        return frame

    # ----------------------------------------------------------
    # 実行
    # ----------------------------------------------------------
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
            for key, _kind in _COMPARE_FIELDS:
                overrides[key] = self._to_value(key, cvars[key].get().strip())
            conds.append(scn.Condition(
                label=i18n.t("scn_cond_n").format(n=i), overrides=overrides))
        return conds

    def _sweep_conditions(self) -> tuple[list[scn.Condition], str, list[float]]:
        axis = self._axis_labels[self._axis_box.get()]
        start = _number(self._from_var.get(), i18n.t("scn_from"))
        stop  = _number(self._to_var.get(),   i18n.t("scn_to"))
        if start == stop:
            raise ValueError(i18n.t("scn_err_range"))
        points = int(_number(self._points_var.get(), i18n.t("scn_points")))
        values = scn.linspace_values(start, stop, points)
        # 軸の値域（周波数 0 や負の高さ）は Condition が弾く＝**DEM 取得の前**に
        # 落ちる。ここで別途チェックを書くと範囲の出所が二重になる（B-016）。
        return scn.sweep_conditions(axis, values), axis, values

    def _on_run(self) -> None:
        if self._running:
            return
        try:
            conditions, axis, values = self._conditions()
        except ValueError as ex:
            dialogs.alert(self, i18n.t("dlg_input_error"), str(ex))
            return

        base = self._base_params
        kind = "sweep" if axis else "compare"
        # ⚠️ **実行はここに出ている値で行う**（経路と同じ扱い）。以前は実行の瞬間に
        # meta_provider を読み直しており、窓を開いたあとにランチャーで案件名を
        # 変えると「画面に無い案件名がレポートに刻印される」状態だった。
        project = self._meta["project_name"]
        memo    = self._meta["memo"]

        save_dir = config.new_run_dir(
            "scenario", datetime.now().strftime("%Y%m%d_%H%M%S"))
        self._last_dir = save_dir

        self._running = True
        self._run_btn.config(state="disabled")
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
        # 完了時はバーを 0 に戻す（単一・バッチと挙動を揃える）。結果はダイアログと
        # 下の一覧が伝えるので、バーに完了状態を残さない。
        self._prog_bar.config(value=0)
        self._prog_label.config(text="")
        self._last_run = run
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
        self._summary_label.config(
            text=f'{i18n.t("html_horiz_dist")}: '
                 f'{units.format_distance(run.terrain.horiz_dist_km)}')

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
