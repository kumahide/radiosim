"""
views/multihop.py
=================
中継経路ウィンドウ（A-3）＝**5 つ目の実行フロー**の View。

ヘッドレスの実行は [multihop.py](multihop.py)、出力は
[report_multihop.py](report_multihop.py) が担い、ここは入力欄・進捗・結果の
一覧表示だけを持つ。

**なぜ waypoint 列と行の二層なのか（⑦・書き落とすと一周戻る）**
--------------------------------------------------------------
画面で編集させるのは **waypoint（地点）** と **ホップ（区間）の無線諸元**だけ。
`PathRow` は `multihop.hop_rows()` の導出物で、**画面には出るが編集できない**。
行を直接編集させると、**中継点の高さが「前ホップの h_rx」と「次ホップの h_tx」に
別々の値で書ける**（同じ 1 本のアンテナに 2 つの値）＝入力の権限が二重化する。

**なぜバッチ窓のタブにしないのか**（2026-08-01 ユーザー決定）
バッチは「N 本の独立回線」、ここは「1 本の回線の内訳」。同じ表に同居させると
行が二役を持ち、⑦が名指しで避けている形になる。

⚠️ **中継点は「確定して置く」もので「動かして探る」ものにしない**（④）＝
ドラッグで動かすたびに新区間の DEM 取得が走る。探索は条件探索（A-2）の担当。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Callable

import i18n
import multihop as mh
import simulation as sim
from views import dialogs, theme, window_fit
from views.progress import ProgressPump

# 地点の既定の地上高（ランチャーの h_tx を初期値に使う）。
_DEFAULT_NAMES = ("TX", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "RX")


class MultiHopWindow(tk.Toplevel):
    """中継経路ウィンドウ（ランチャーが唯一のインスタンスを持つ）。"""

    _BASE_W = 980

    def __init__(
        self,
        parent: tk.Tk,
        base_params: sim.SimParams,
        config_provider: "Callable[[], dict] | None" = None,
        meta_provider:   "Callable[[], dict] | None" = None,
        on_close:        "Callable[[], None] | None" = None,
        initial_path:    "mh.MultiHopPath | None" = None,
        map_opener:      "Callable[[object], None] | None" = None,
    ) -> None:
        super().__init__(parent)
        self.title(i18n.t("mh_window_title"))
        self.minsize(820, 560)

        self._base_params     = base_params
        self._config_provider = config_provider
        self._meta_provider   = meta_provider
        self._meta            = self._snapshot_meta()
        self._on_close_cb     = on_close
        # 地図を中継点モードで開く口（ランチャーが注入する）。
        self._map_opener      = map_opener
        self._running  = False
        self._last_run: "mh.MultiHopRun | None" = None

        # 画面の状態＝waypoint 列とホップ別 RF（**これが source of truth**）。
        self._wp_vars:  list[dict[str, tk.StringVar]] = []
        self._hop_vars: list[dict[str, tk.StringVar]] = []

        self._pump = ProgressPump(self, self._dispatch_event)

        self._build()
        if initial_path is not None and initial_path.waypoints:
            self._apply_path(initial_path)
        else:
            self._add_waypoint(_DEFAULT_NAMES[0])
            self._add_waypoint(_DEFAULT_NAMES[-1])
        self._fit_to_content()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------------
    # プロジェクト（`.rsproj`）との受け渡し
    # ----------------------------------------------------------
    def project_path(self) -> "mh.MultiHopPath | None":
        """保存用に経路を取り出す。**まだ座標が 1 つも入っていなければ None**。

        `None`＝「この窓の情報を持たない」（project.py の約束）。開いただけの窓が
        空の経路でファイルの中身を上書きしないための区別で、`_collect_path` の
        「読めない値は名前つきで弾く」性質はそのまま使う（保存側は例外を受けて
        **その節だけ保存しない**＝黙って壊れた経路を書かない）。
        """
        if not any(v["coord"].get().strip() for v in self._wp_vars):
            return None
        return self._collect_path()

    def _apply_path(self, path: "mh.MultiHopPath") -> None:
        """プロジェクトの経路を画面へ流し込む（`project_path` と対）。

        ⚠️ **地点を先に作ってからホップ RF を入れる**＝ホップ行は地点数からの
        導出物（`_sync_hops`）なので、順序を逆にすると入れた値が消える。
        """
        self._route_id.set(path.path_id or "route1")
        self._note.set(path.note)
        self._wp_vars = []
        for wp in path.waypoints:
            self._wp_vars.append({
                "name":   tk.StringVar(value=wp.name),
                "coord":  tk.StringVar(value=f"{wp.lat:.6f}, {wp.lon:.6f}"),
                "height": tk.StringVar(value=f"{wp.h:.1f}"),
            })
        self._render_waypoints()          # → _sync_hops でホップ行が揃う
        for vars_, rf in zip(self._hop_vars, path.hop_rf):
            for key, value in (("freq", rf.freq_mhz), ("gain_tx", rf.gain_tx),
                               ("gain_rx", rf.gain_rx)):
                vars_[key].set("" if value is None else str(value))

    # ----------------------------------------------------------
    # 組み立て
    # ----------------------------------------------------------
    def _build(self) -> None:
        outer = window_fit.scrollable_body(self, padding=10)
        self._build_frozen_header(outer)

        # 経路 ID と備考（1 本の回線を識別する）。
        head = ttk.Frame(outer)
        head.pack(fill="x", pady=(0, 6))
        ttk.Label(head, text=i18n.t("mh_route_id")).pack(side="left")
        self._route_id = tk.StringVar(value="route1")
        ttk.Entry(head, textvariable=self._route_id, width=14).pack(
            side="left", padx=(4, 12))
        ttk.Label(head, text=i18n.t("col_note")).pack(side="left")
        self._note = tk.StringVar()
        ttk.Entry(head, textvariable=self._note).pack(
            side="left", padx=(4, 0), fill="x", expand=True)

        self._build_waypoints(outer)
        self._build_hops(outer)

        # 実行帯＝**進捗バーの右に「実行」**（3 フローと同じ型＝I-029）。
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(10, 6))
        # 右端から順に pack する（実行 → ステータス）＝ボタンが常に帯の右端。
        self._run_btn = ttk.Button(bar, text=i18n.t("btn_run_sim"),
                                   command=self._on_run, style="Accent.TButton")
        self._run_btn.pack(side="right", padx=(10, 0))
        self._prog_label = ttk.Label(bar, text="")
        self._prog_label.pack(side="right", padx=(10, 0))
        self._prog_bar = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self._prog_bar.pack(side="left", fill="x", expand=True)

        self._build_results(outer)

    def _build_frozen_header(self, parent: tk.Misc) -> None:
        """ランチャーから凍結した前提（**条件探索・バッチと同じ見せ方**＝I-031）。"""
        case = ttk.LabelFrame(parent, text=i18n.t("batch_case_info"), padding=(8, 2))
        case.pack(fill="x", pady=(0, 4))
        self._project_var = tk.StringVar()
        self._memo_var    = tk.StringVar()
        for key, var in (("batch_project_name", self._project_var),
                         ("batch_memo",         self._memo_var)):
            f = ttk.Frame(case)
            f.pack(side="left", padx=6, fill="x", expand=(key == "batch_memo"))
            ttk.Label(f, text=i18n.t(key)).pack(side="left")
            ttk.Entry(f, textvariable=var, state="readonly", width=20).pack(
                side="left", padx=(2, 0), fill="x", expand=(key == "batch_memo"))
            ttk.Label(f, text="🔒").pack(side="left", padx=(2, 0))

        common = ttk.LabelFrame(parent, text=i18n.t("batch_common_cfg"), padding=(8, 2))
        common.pack(fill="x", pady=(0, 6))
        # ⚠️ **2 行に折る**＝6 欄を 1 行に並べると 125%/150% で必要幅が 2000px を
        # 超え、FHD（使える幅 1830px）に入らなくなる（横断ゲートが検出した）。
        # 高さは 1 行ぶん増えるが、この窓は縦に余裕がある。
        rows = [ttk.Frame(common), ttk.Frame(common)]
        for r in rows:
            r.pack(fill="x")
        row = rows[1]
        self._common_vars: dict[str, tk.StringVar] = {}
        for n, (label_key, attr, width) in enumerate((
            ("lbl_b_p_tx",     "p_tx",     7),
            ("lbl_b_sens",     "sens",     7),
            ("lbl_b_veg_h",    "veg_h",    6),
            ("lbl_b_samples",  "num",      6),
            ("lbl_b_rain",     "rain_rate", 6),
            ("lbl_b_env_type", "env_type", 9),
        )):
            f = ttk.Frame(rows[0] if n < 3 else rows[1])
            f.pack(side="left", padx=6)
            ttk.Label(f, text=i18n.t(label_key)).pack(side="left")
            var = tk.StringVar()
            self._common_vars[attr] = var
            ttk.Entry(f, textvariable=var, state="readonly", width=width).pack(
                side="left", padx=(2, 0))
            ttk.Label(f, text="🔒").pack(side="left", padx=(2, 0))
        ttk.Button(row, text=i18n.t("scn_refresh"), width=18,
                   command=self._refresh_from_launcher).pack(side="right", padx=(6, 0))
        ttk.Label(row, text=i18n.t("hint_common_readonly"),
                  foreground=theme.muted_foreground(row)).pack(side="right", padx=6)
        self._update_frozen()

    def _build_waypoints(self, parent: tk.Misc) -> None:
        """地点の表（**ここだけが座標と高さの入力面**）。"""
        box = ttk.LabelFrame(parent, text=i18n.t("mh_waypoints"), padding=(8, 4))
        box.pack(fill="x")
        self._wp_grid = ttk.Frame(box)
        self._wp_grid.pack(fill="x")
        for col, key in enumerate(("mh_col_no", "mh_col_name", "mh_col_coord",
                                   "mh_col_height")):
            ttk.Label(self._wp_grid, text=i18n.t(key)).grid(
                          row=0, column=col, padx=4, pady=(0, 2), sticky="w")

        btns = ttk.Frame(box)
        btns.pack(fill="x", pady=(6, 0))
        for key, cmd in (("mh_add_point",  self._on_add_point),
                         ("mh_del_point",  self._on_del_point),
                         ("mh_from_map",   self._on_from_map)):
            ttk.Button(btns, text=i18n.t(key), command=cmd).pack(side="left", padx=(0, 6))
        ttk.Label(btns, text=i18n.t("mh_hint_order"),
                  foreground=theme.muted_foreground(btns)).pack(side="left", padx=6)

    def _build_hops(self, parent: tk.Misc) -> None:
        """区間（ホップ）の無線諸元＝**地点の間に 1 行ずつ**。

        ⚠️ ここに高さは出さない（高さは地点のもの）。**この分担が二重入力を
        構造的に防いでいる**ので、後から「ここにも高さがあると便利」を足さないこと。
        """
        box = ttk.LabelFrame(parent, text=i18n.t("mh_hops_group"), padding=(8, 4))
        box.pack(fill="x", pady=(6, 0))
        self._hop_grid = ttk.Frame(box)
        self._hop_grid.pack(fill="x")
        for col, key in enumerate(("mh_col_section", "lbl_b_freq",
                                   "lbl_b_gain_tx", "lbl_b_gain_rx")):
            ttk.Label(self._hop_grid, text=i18n.t(key)).grid(
                          row=0, column=col, padx=4, pady=(0, 2), sticky="w")
        ttk.Label(box, text=i18n.t("mh_hint_inherit"),
                  foreground=theme.muted_foreground(box)).pack(anchor="w", pady=(4, 0))

    def _build_results(self, parent: tk.Misc) -> None:
        self._result_box = ttk.LabelFrame(parent, text=i18n.t("mh_result"), padding=8)
        self._result_box.pack(fill="both", expand=True)
        self._summary_label = ttk.Label(self._result_box, text="")
        self._summary_label.pack(anchor="w", pady=(0, 6))

        cols = ("hop", "section", "rx", "margin", "status")
        self._tree = ttk.Treeview(self._result_box, columns=cols, show="headings",
                                  height=6, style=theme.table_style(self))
        for col, key, w in (
            ("hop",     "mh_col_no",       50),
            ("section", "mh_col_section", 240),
            ("rx",      "html_rx_level",  130),
            ("margin",  "html_act_margin", 130),
            ("status",  "html_status",     80),
        ):
            self._tree.heading(col, text=i18n.t(key))
            self._tree.column(col, width=w, stretch=True,
                              anchor="w" if col == "section" else "e")
        vsb = ttk.Scrollbar(self._result_box, orient="vertical",
                            command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        # 判定色（OK/緑・NG/赤）は条件探索と同じ出所から取る。行に `ok`/`ng` の
        # タグは付いていたのに**色を当てておらず、この窓だけ同色**だった
        # （レポート側は色分けしていたので、画面だけが落ちていた）。
        theme.apply_verdict_tags(self._tree)
        self.bind("<<ThemeChanged>>",
                  lambda _e: theme.apply_verdict_tags(self._tree), add="+")

    def _fit_to_content(self) -> None:
        window_fit.fit_to_content(self, min_w=self._BASE_W)

    # ----------------------------------------------------------
    # ランチャーからの凍結値
    # ----------------------------------------------------------
    def _snapshot_meta(self) -> dict[str, str]:
        meta = self._meta_provider() if self._meta_provider else {}
        return {"project_name": str(meta.get("project_name", "")),
                "memo":         str(meta.get("memo", ""))}

    def _update_frozen(self) -> None:
        self._project_var.set(self._meta["project_name"])
        self._memo_var.set(self._meta["memo"])
        p = self._base_params
        for attr, var in self._common_vars.items():
            value = getattr(p, attr)
            var.set(i18n.t(f"env_{value}") if attr == "env_type" else str(value))

    def _refresh_from_launcher(self) -> None:
        """ランチャーの現在値を取り込み直す（**黙って追従しない**＝凍結方式）。"""
        self._meta = self._snapshot_meta()
        if self._config_provider is not None:
            try:
                self._base_params = sim.SimParams(self._config_provider())
            except Exception as ex:
                dialogs.alert(self, i18n.t("dlg_input_error"), str(ex))
                return
        self._update_frozen()

    # ----------------------------------------------------------
    # 地点の増減
    # ----------------------------------------------------------
    def _add_waypoint(self, name: str = "", lat: "float | None" = None,
                      lon: "float | None" = None) -> None:
        """地点を足す。**2 点あるときは「間」に入れる**（＝中継点として足す）。

        ⚠️ 末尾に足すと「TX → RX → R1」という並びになり、**それまで受信点だった
        地点が中継点に化ける**（実装直後のスクリーンショットで実際にそうなった）。
        利用者の頭の中は「送信点と受信点があって、その間に中継点を置く」なので、
        **先頭＝送信点・末尾＝受信点を固定**し、新しい点はその手前へ挿す。
        """
        if len(self._wp_vars) >= mh.MAX_HOPS + 1:
            dialogs.alert(self, i18n.t("dlg_input_error"),
                          i18n.t("mh_err_too_many").format(max=mh.MAX_HOPS))
            return
        coord = f"{lat:.6f}, {lon:.6f}" if lat is not None and lon is not None else ""
        vars_ = {
            "name":   tk.StringVar(value=name or self._next_relay_name()),
            "coord":  tk.StringVar(value=coord),
            "height": tk.StringVar(value=f"{self._base_params.h_tx:.1f}"),
        }
        if len(self._wp_vars) >= 2:
            self._wp_vars.insert(len(self._wp_vars) - 1, vars_)   # 受信点の手前へ
        else:
            self._wp_vars.append(vars_)
        self._render_waypoints()

    def _next_relay_name(self) -> str:
        """既定の地点名。先頭＝TX／2 点目＝RX／以後は使っていない `R*` を選ぶ。"""
        if not self._wp_vars:
            return _DEFAULT_NAMES[0]
        if len(self._wp_vars) == 1:
            return _DEFAULT_NAMES[-1]
        used = {v["name"].get() for v in self._wp_vars}
        for candidate in _DEFAULT_NAMES[1:-1]:
            if candidate not in used:
                return candidate
        return f"R{len(self._wp_vars)}"

    def _render_waypoints(self) -> None:
        """地点の表を並び順どおりに描き直す（**モデルが先・表示は従属**）。

        挿入・削除で行がずれるので、部分更新ではなく毎回作り直す（地点は最大
        9 行なので安い）。行番号の隣に役割（送信 / 中継 / 受信）を出す＝並びが
        そのまま経路の順序であることを画面から読み取れるようにする。
        """
        for r in range(1, mh.MAX_HOPS + 3):
            for w in self._wp_grid.grid_slaves(row=r):
                w.destroy()
        last = len(self._wp_vars) - 1
        for i, vars_ in enumerate(self._wp_vars):
            row = i + 1
            role = (i18n.t("mh_role_tx") if i == 0 else
                    i18n.t("mh_role_rx") if i == last else i18n.t("mh_role_relay"))
            ttk.Label(self._wp_grid, text=f"{row}  {role}").grid(
                row=row, column=0, padx=4, pady=1, sticky="w")
            ttk.Entry(self._wp_grid, textvariable=vars_["name"], width=10).grid(
                row=row, column=1, padx=4, pady=1)
            ttk.Entry(self._wp_grid, textvariable=vars_["coord"], width=26).grid(
                row=row, column=2, padx=4, pady=1, sticky="ew")
            ttk.Entry(self._wp_grid, textvariable=vars_["height"], width=8).grid(
                row=row, column=3, padx=4, pady=1)
        self._sync_hops()
        self._fit_to_content()

    def _on_add_point(self) -> None:
        self._add_waypoint()

    def _on_del_point(self) -> None:
        """**中継点**を末尾から 1 つ削る（送信点・受信点は残す）。"""
        if len(self._wp_vars) <= 2:
            return
        self._wp_vars.pop(len(self._wp_vars) - 2)
        self._render_waypoints()

    def _on_from_map(self) -> None:
        """地図から順に拾う（宛先をこの窓へ切り替える）。

        地図は**アプリ唯一のインスタンス**で、モードで宛先を切り替える設計
        （2.3 D2）。ここはその 3 つ目のシンク＝**1 点ずつ順に足す**。

        ⚠️ **親ウィジェットからメソッドを探さない**（`getattr(self.master, …)`）。
        `self.master` は Tk のルートで、ランチャー（`SimLauncher`）はウィジェット
        ではないため**必ず None になり、この機能は一度も動かなかった**。依存は
        バッチの `load_params`・条件探索の `config_provider` と同じく**注入**する。
        """
        if self._map_opener is None:
            dialogs.alert(self, i18n.t("dlg_input_error"), i18n.t("mh_err_no_map"))
            return
        self._map_opener(self)

    def append_waypoint(self, lat: float, lon: float) -> str:
        """地図からの 1 点追加（`_WaypointSink` の実装）。

        空欄の地点があればそこを埋め、無ければ末尾に足す＝「TX と RX の枠だけ
        ある状態」から地図で順に埋めていける。
        """
        for vars_ in self._wp_vars:
            if not vars_["coord"].get().strip():
                vars_["coord"].set(f"{lat:.6f}, {lon:.6f}")
                return vars_["name"].get()
        # 空きが無ければ**中継点として**足す（受信点の手前＝_add_waypoint の約束）。
        before = len(self._wp_vars)
        self._add_waypoint(lat=lat, lon=lon)
        if len(self._wp_vars) == before:
            return ""                     # 上限に達していて足せなかった
        return self._wp_vars[len(self._wp_vars) - 2]["name"].get()

    def _sync_hops(self) -> None:
        """地点の数に合わせてホップ行を作り直す（**導出**＝地点が先）。"""
        for r in range(1, len(self._hop_vars) + 2):
            for w in self._hop_grid.grid_slaves(row=r):
                w.destroy()
        hops = max(len(self._wp_vars) - 1, 0)
        old = self._hop_vars
        self._hop_vars = []
        for i in range(hops):
            vars_ = old[i] if i < len(old) else {
                "freq":    tk.StringVar(),
                "gain_tx": tk.StringVar(),
                "gain_rx": tk.StringVar(),
            }
            self._hop_vars.append(vars_)
            label = (f"{self._wp_vars[i]['name'].get()} → "
                     f"{self._wp_vars[i + 1]['name'].get()}")
            ttk.Label(self._hop_grid, text=label).grid(
                row=i + 1, column=0, padx=4, pady=1, sticky="w")
            for col, key in enumerate(("freq", "gain_tx", "gain_rx"), start=1):
                ttk.Entry(self._hop_grid, textvariable=vars_[key], width=10).grid(
                    row=i + 1, column=col, padx=4, pady=1)

    # ----------------------------------------------------------
    # 実行
    # ----------------------------------------------------------
    def _collect_path(self) -> mh.MultiHopPath:
        """画面 → `MultiHopPath`（**ここでしか組み立てない**）。"""
        waypoints: list[mh.Waypoint] = []
        for i, vars_ in enumerate(self._wp_vars, start=1):
            text = vars_["coord"].get().strip()
            parts = text.split(",")
            if len(parts) != 2:
                raise ValueError(i18n.t("mh_err_coord").format(
                    no=i, name=vars_["name"].get()))
            try:
                lat, lon = float(parts[0]), float(parts[1])
                height   = float(vars_["height"].get())
            except ValueError:
                raise ValueError(i18n.t("mh_err_coord").format(
                    no=i, name=vars_["name"].get())) from None
            waypoints.append(mh.Waypoint(
                name=vars_["name"].get().strip() or f"P{i}",
                lat=lat, lon=lon, h=height))

        def _opt(text: str) -> "float | None":
            text = text.strip()
            return float(text) if text else None       # 空欄＝共通設定を踏襲

        hop_rf = [mh.HopRF(freq_mhz=_opt(v["freq"].get()),
                           gain_tx=_opt(v["gain_tx"].get()),
                           gain_rx=_opt(v["gain_rx"].get()))
                  for v in self._hop_vars]
        return mh.MultiHopPath(path_id=self._route_id.get().strip(),
                               waypoints=waypoints, hop_rf=hop_rf,
                               note=self._note.get().strip())

    def _on_run(self) -> None:
        if self._running:
            return
        try:
            path = self._collect_path()
        except ValueError as ex:
            dialogs.alert(self, i18n.t("dlg_input_error"), str(ex))
            return
        errors = mh.validate_path(path)
        if errors:
            dialogs.alert(self, i18n.t("dlg_validation_error"),
                          "\n".join(errors[:10]))
            return

        self._running = True
        self._run_btn.config(state="disabled")
        self._tree.delete(*self._tree.get_children())
        self._summary_label.config(text="")
        self._prog_bar.config(value=0, maximum=path.hop_count)
        self._pump.start()
        push = self._pump.push
        mh.run_multihop(
            path, self._base_params,
            on_hop_start    = lambda i, n, pid, p=push: p(("start", (i, n, pid))),
            on_hop_progress = lambda v: None,
            on_hop_complete = lambda i, n, pr, p=push: p(("hop", (i, n, pr))),
            on_complete     = lambda run, p=push: p(("complete", (run,))),
            on_error        = lambda ex,  p=push: p(("error", (ex,))),
            project_name    = self._meta["project_name"],
            memo            = self._meta["memo"],
        )

    # ----------------------------------------------------------
    # コールバック（メインスレッド）
    # ----------------------------------------------------------
    def _dispatch_event(self, item: tuple) -> None:
        kind, args = item
        if kind == "start":
            i, n, pid = args
            self._prog_label.config(text=i18n.t("mh_running").format(i=i, n=n))
        elif kind == "hop":
            i, _n, pr = args
            self._add_result_row(i, pr)
            self._prog_bar.config(value=i)
        elif kind == "complete":
            self._on_complete(args[0])
        elif kind == "error":
            self._on_error(args[0])

    def _add_result_row(self, index: int, pr) -> None:
        wp = self._wp_vars
        section = (f"{wp[index - 1]['name'].get()} → {wp[index]['name'].get()}"
                   if index < len(wp) else pr.row.path_id)
        r = pr.result
        if r is None:
            self._tree.insert("", "end", values=(index, section, "—", "—", "ERROR"),
                              tags=("ng",))
            return
        self._tree.insert("", "end", tags=("ok" if r.status == "OK" else "ng",),
                          values=(index, section, f"{r.p_rx:.2f}",
                                  f"{r.actual_margin:+.2f}", r.status))

    def _on_complete(self, run: mh.MultiHopRun) -> None:
        self._running = False
        self._pump.stop()
        self._run_btn.config(state="normal")
        self._prog_bar.config(value=0)
        self._prog_label.config(text="")
        self._last_run = run

        # **全体判定＋どの区間が決めているか**を必ず併記する（min だけ出さない）。
        margin = run.overall_margin
        worst  = run.worst
        worst_label = "—"
        if worst is not None:
            idx = run.hops.index(worst)
            worst_label = (f"#{idx + 1} {run.path.waypoints[idx].name} → "
                           f"{run.path.waypoints[idx + 1].name}")
        self._summary_label.config(text=i18n.t("mh_summary").format(
            status="OK" if run.ok else "NG",
            margin=f"{margin:+.2f}" if margin is not None else "—",
            worst=worst_label))

        choice = dialogs.choose(
            self, i18n.t("dlg_saved_title"),
            i18n.t("scn_saved_msg").format(dir=run.save_dir),
            [("report", i18n.t("dlg_open_report")),
             ("folder", i18n.t("dlg_open_folder"))],
        )
        if choice == "report":
            os.startfile(os.path.join(run.save_dir, "route.html"))
        elif choice == "folder":
            os.startfile(run.save_dir)

    def _on_error(self, ex: Exception) -> None:
        self._running = False
        self._pump.stop()
        self._run_btn.config(state="normal")
        self._prog_bar.config(value=0)
        self._prog_label.config(text="")
        dialogs.alert(self, i18n.t("dlg_error"), str(ex))

    def close_window(self) -> None:
        """他所（ランチャーのプロジェクト読込）から閉じるための公開口
        （3 つの窓で名前を揃える＝内部ハンドラ名で分岐させない）。"""
        self._on_close()

    def _on_close(self) -> None:
        self._pump.stop()
        if self._on_close_cb is not None:
            self._on_close_cb()
        self.destroy()
