"""
views/multihop.py
=================
中継経路ウィンドウ（A-3）＝**5 つ目の実行フロー**の View。

ヘッドレスの実行は [multihop.py](multihop.py)、出力は
[report_multihop.py](report_multihop.py) が担い、ここは入力欄・進捗・結果の
表示だけを持つ。

**結果の居場所（2.7 スライス B・I-041）**：区間ごとの結果は**区間表の右 3 列**に
返し、独立した結果一覧（Treeview）は持たない。全体判定（区間をまたいだ集約）だけが
下の枠に残る。

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

from core import coords
from core import i18n
from core import simulation as sim
from report import multihop as mh
from views import dialogs, theme, window_fit
from views.progress import ProgressPump

# 地点の既定の地上高（ランチャーの h_tx を初期値に使う）。
_DEFAULT_NAMES = ("TX", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "RX")

# 座標欄の幅は `coords` が単一ソース（B-046）＝窓ごとに数字を書かない。
_COORD_WIDTH = coords.DISPLAY_WIDTH_CHARS


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
        map_notify:      "Callable[[], None] | None" = None,
        coord_format:    str = "dd",
    ) -> None:
        super().__init__(parent)
        self.title(i18n.t("mh_window_title"))
        self.minsize(820, 560)

        self._base_params     = base_params
        self._config_provider = config_provider
        self._meta_provider   = meta_provider
        self._meta            = self._snapshot_meta()
        self._on_close_cb     = on_close
        # 地図を中継点モードで開く口／地点列が変わったことを知らせる口
        # （どちらもランチャーが注入する＝親ウィジェットから探さない）。
        self._map_opener      = map_opener
        self._map_notify      = map_notify
        # 座標の表記＝**開いた時点で凍結**（G2 と同じ形・I-070）。この窓は長らく
        # 受け取っておらず、設定を DMS にしてもここだけ十進度で出ていた。
        # ⚠️ **表記は表示だけの話**＝読む側は `coords.parse_pair` が両表記を受け、
        # 保存・計算へは常に DD で渡る（内部の正典は DD）。
        self._coord_format    = coord_format
        self._running  = False
        self._last_run: "mh.MultiHopRun | None" = None

        # 画面の状態＝waypoint 列とホップ別 RF（**これが source of truth**）。
        self._wp_vars:  list[dict[str, tk.StringVar]] = []
        self._hop_vars: list[dict[str, tk.StringVar]] = []
        # 区間表の結果セル（`_sync_hops` が区間行と一緒に作り直す）＝結果は
        # **その結果を生んだ区間の行**に返る（I-041・結果一覧は廃止した）。
        self._hop_result_labels: list[dict[str, ttk.Label]] = []

        self._pump = ProgressPump(self, self._dispatch_event)

        self._build()
        if initial_path is not None and initial_path.waypoints:
            # ⚠️ プロジェクトの経路があるときは**引き継ぎで上書きしない**（I-044）。
            self._apply_path(initial_path)
        else:
            # **TX / RX はランチャーの座標を初期値にする**（I-044）＝⑦ランチャーが
            # 唯一の源泉・⑧複数経路の表は既にそうしている。中継点は空のまま
            # （ランチャーに対応する値が無い）。
            # ⚠️ **これは「開いた時の初期値」だけ**＝`↻ ランチャーから更新` の
            # 対象にはしない。座標はこの窓で編集する値で、凍結帯（案件情報・
            # 共通設定）とは性格が違う＝↻ で経路が消えたら事故。
            tx, rx = self._launcher_endpoints()
            self._add_waypoint(_DEFAULT_NAMES[0],  lat=tx[0] if tx else None,
                               lon=tx[1] if tx else None)
            self._add_waypoint(_DEFAULT_NAMES[-1], lat=rx[0] if rx else None,
                               lon=rx[1] if rx else None)
        self._fit_to_content()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _launcher_endpoints(self):
        """ランチャーの送受信座標（読めなければ `None`）＝TX / RX の初期値（I-044）。

        ⚠️ **読めない値は静かに空欄にする**＝窓を開く途中でダイアログは出さない
        （ランチャー側の入力エラーは、ランチャーで実行したときに出る）。
        """
        if self._config_provider is None:
            return None, None
        try:
            cfg = self._config_provider()
        except Exception:
            return None, None
        out = []
        for key in ("start", "end"):
            try:
                out.append(coords.parse_pair(str(cfg.get(key, ""))))
            except ValueError:
                out.append(None)
        return out[0], out[1]

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

    def apply_project_path(self, path: "mh.MultiHopPath") -> None:
        """プロジェクトの経路をこの窓へ取り込む（I-061 の帯から呼ばれる）。

        **公開口にしてある**＝ランチャーが `_apply_path` のような内部名に
        依存しない（内部名への配線は黙って壊れる＝5b の `getattr` の教訓）。
        """
        self._apply_path(path)

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
                "coord":  tk.StringVar(
                    value=coords.format_pair(wp.lat, wp.lon, self._coord_format)),
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

        # 実行帯＝**上段にステータス 1 行／下段はバーの右に「実行」**（4 窓で同じ型＝
        # I-029 ＋ I-047）。ステータスをバーの横に置いていたころは、`n 区間目を計算中`
        # の文言が伸びるたびに実行ボタンが左へ動いた。
        prog_frame = ttk.Frame(outer)
        prog_frame.pack(fill="x", pady=(10, 6))

        row1 = ttk.Frame(prog_frame)
        row1.pack(fill="x")
        self._prog_label = ttk.Label(row1, text="", anchor="w")
        self._prog_label.pack(side="left", fill="x", expand=True)

        bar = ttk.Frame(prog_frame)
        bar.pack(fill="x", pady=(2, 0))
        self._run_btn = ttk.Button(bar, text=i18n.t("btn_run_sim"),
                                   command=self._on_run, style="Accent.TButton")
        self._run_btn.pack(side="right", padx=(10, 0))
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
        # ⚠️ **「中継点を削除」ボタンは置かない**（I-045）＝削除は行ごとの `×`
        # に移した。末尾固定の削除ボタンと行ごとの `×` が同居すると、「どちらが
        # 消えるのか」を毎回考えることになる（複数経路の表は `×` だけ）。
        for key, cmd in (("mh_add_point",  self._on_add_point),
                         ("mh_from_map",   self._on_from_map)):
            ttk.Button(btns, text=i18n.t(key), command=cmd).pack(side="left", padx=(0, 6))
        ttk.Label(btns, text=i18n.t("mh_hint_order"),
                  foreground=theme.muted_foreground(btns)).pack(side="left", padx=6)

    def _build_hops(self, parent: tk.Misc) -> None:
        """区間（ホップ）の無線諸元＝**地点の間に 1 行ずつ**。**結果もここへ返る**。

        ⚠️ ここに高さは出さない（高さは地点のもの）。**この分担が二重入力を
        構造的に防いでいる**ので、後から「ここにも高さがあると便利」を足さないこと。

        **右 3 列は読み取り専用の結果**（受信レベル / マージン / 判定＝I-041）。
        区間表は 1 行 = 1 区間 = 1 結果なので、統一規則「結果はその結果を生んだ行に
        返す」がそのまま当てはまる。**地点表ではない**＝地点 N に対し結果は N−1 で、
        1:1 が成り立たない。
        """
        box = ttk.LabelFrame(parent, text=i18n.t("mh_hops_group"), padding=(8, 4))
        box.pack(fill="x", pady=(6, 0))
        self._hop_grid = ttk.Frame(box)
        self._hop_grid.pack(fill="x")
        for col, key in enumerate(("mh_col_section", "lbl_b_freq",
                                   "lbl_b_gain_tx", "lbl_b_gain_rx",
                                   "html_rx_level", "html_act_margin",
                                   "html_status")):
            ttk.Label(self._hop_grid, text=i18n.t(key)).grid(
                          row=0, column=col, padx=4, pady=(0, 2), sticky="w")
        ttk.Label(box, text=i18n.t("mh_hint_inherit"),
                  foreground=theme.muted_foreground(box)).pack(anchor="w", pady=(4, 0))
        # 判定色はテーマから引く＝**結果を入れたときに**決まるので、テーマ切替では
        # 自動追従しない（Treeview のタグと違う）。貼り直す口をここで結んでおく。
        self.bind("<<ThemeChanged>>",
                  lambda _e: self._refresh_verdict_colors(), add="+")

    def _build_results(self, parent: tk.Misc) -> None:
        """**全体判定だけ**を置く枠（区間別の結果は区間表が持つ）。

        ⛔ **結果一覧（Treeview）はここに戻さない**（2.7 スライス B で廃止）。
        統一規則＝「結果はその結果を生んだ行に返す。1 行 = 1 結果が成り立つ入力表を
        持つ窓はその表へ返し、成り立たない窓だけが結果一覧を持つ」。中継は区間表が
        1 行 = 1 結果なので、一覧を持つと**同じ数字が窓の中に 2 か所**できる。
        一覧を持つのは条件探索（1 条件から結果が N 件出る＝1:1 でない）だけ。

        全体判定が残るのは重複ではない＝**区間をまたいだ集約**（最も苦しい区間が
        どれか）で、区間表のどの行にも書けない情報（[[glossary]] の「全体判定」）。
        """
        self._result_box = ttk.LabelFrame(parent, text=i18n.t("mh_result"), padding=8)
        self._result_box.pack(fill="x")
        self._summary_label = ttk.Label(self._result_box, text="")
        self._summary_label.pack(anchor="w")

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
        coord = ("" if lat is None or lon is None
                 else coords.format_pair(lat, lon, self._coord_format))
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
            name_ent = ttk.Entry(self._wp_grid, textvariable=vars_["name"], width=10)
            name_ent.grid(row=row, column=1, padx=4, pady=1)
            coord_ent = ttk.Entry(self._wp_grid, textvariable=vars_["coord"],
                                  width=_COORD_WIDTH)
            coord_ent.grid(row=row, column=2, padx=4, pady=1, sticky="ew")
            ttk.Entry(self._wp_grid, textvariable=vars_["height"], width=8).grid(
                row=row, column=3, padx=4, pady=1)
            # 手入力の確定でも地図を追従させる（バッチの座標セルと同じ流儀）。
            for ent in (name_ent, coord_ent):
                ent.bind("<FocusOut>", lambda _e: self._notify_map(), add="+")
                ent.bind("<Return>",   lambda _e: self._notify_map(), add="+")
            # 確定でこの窓の表記へ整形する（I-060 R3）＝整形されること自体が
            # 「読めた」という返事になる。読めなければ原文が残る。
            for seq in ("<FocusOut>", "<Return>"):
                coord_ent.bind(seq, lambda _e, v=vars_["coord"]: self._reformat(v),
                               add="+")
            # 行ごとの削除（I-045）＝複数経路の表と同じ `×`。
            # ⚠️ **送信点・受信点には出さない**＝消せないという現在の不変条件を、
            # そのまま画面で表す（押せるのに何も起きないボタンを置かない）。
            if 0 < i < last:
                ttk.Button(self._wp_grid, text="×", width=2, cursor="hand2",
                           command=lambda idx=i: self._delete_waypoint(idx)).grid(
                    row=row, column=4, padx=4, pady=1)
        self._sync_hops()
        self._fit_to_content()
        # 地点の増減・並びの変化はここに集約されている＝通知もここ 1 か所で足りる。
        self._notify_map()

    def _reformat(self, var: tk.StringVar) -> None:
        """座標欄 1 つを、この窓の表記へ整形する（読めなければ原文のまま）。"""
        shown = coords.reformat(var.get(), self._coord_format)
        if shown != var.get():
            var.set(shown)

    def _on_add_point(self) -> None:
        self._add_waypoint()

    def _delete_waypoint(self, index: int) -> None:
        """**任意の中継点**を消す（I-045）。送信点・受信点は消せない。

        🔴 **区間の設定も一緒に落とす**＝`_sync_hops` はホップ行を**先頭から
        詰め直す**ので、地点だけ消すと**消した地点より後ろの周波数・利得が 1 つ
        前へずれる**（末尾を消すだけだった頃は起きなかった）。ずれても画面は
        自然に見えるので、**黙って別の区間の設定で計算する**実害になる。

        落とすのは **`index` 番目のホップ＝消える地点から「出ていく」側**。
        残すのは「入ってくる」側（`index - 1`）で、こちらは合流後の区間と
        **始点が同じ**なので、利用者の入力の意味が変わらない。
        """
        last = len(self._wp_vars) - 1
        if len(self._wp_vars) <= 2 or not 0 < index < last:
            return
        self._wp_vars.pop(index)
        if index < len(self._hop_vars):
            self._hop_vars.pop(index)
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

    def waypoint_markers(self) -> "list[tuple[str, float, float]]":
        """地図が描き直すための現在の地点列（読めない座標の行は落とす）。

        **地図は写すだけ**＝ここが唯一の出所（`_WaypointSink` の実装）。座標を
        入力途中の行は座標として読めないので出さない（実行時の検証とは別物＝
        地図の表示は「今読める点」で足りる）。
        """
        points: list[tuple[str, float, float]] = []
        for vars_ in self._wp_vars:
            try:
                lat, lon = coords.parse_pair(vars_["coord"].get())
            except ValueError:
                continue
            points.append((vars_["name"].get(), lat, lon))
        return points

    def _notify_map(self) -> None:
        """地点列が変わったことを地図へ知らせる（開いていて中継点モードのときだけ効く）。

        ⚠️ **削除も編集も通知する**＝追加のときだけ描くと、窓で消した地点が地図に
        残る（2026-08-01 実機確認）。バッチ → 地図の `on_paths_changed` と同じ形。
        """
        if self._map_notify is not None:
            self._map_notify()

    def append_waypoint(self, lat: float, lon: float) -> str:
        """地図からの 1 点追加（`_WaypointSink` の実装）。

        空欄の地点があればそこを埋め、無ければ末尾に足す＝「TX と RX の枠だけ
        ある状態」から地図で順に埋めていける。
        """
        for vars_ in self._wp_vars:
            if not vars_["coord"].get().strip():
                vars_["coord"].set(
                    coords.format_pair(lat, lon, self._coord_format))
                return vars_["name"].get()
        # 空きが無ければ**中継点として**足す（受信点の手前＝_add_waypoint の約束）。
        before = len(self._wp_vars)
        self._add_waypoint(lat=lat, lon=lon)
        if len(self._wp_vars) == before:
            return ""                     # 上限に達していて足せなかった
        return self._wp_vars[len(self._wp_vars) - 2]["name"].get()

    def _sync_hops(self) -> None:
        """地点の数に合わせてホップ行を作り直す（**導出**＝地点が先）。

        ⚠️ **結果列（右 3 列）も一緒に作り直す＝ここで前回の結果が消える**。
        地点が増減すれば区間の意味そのものが変わるので、**古い結果を新しい区間の
        行に残さない**（I-041 の規則は「結果はその結果を生んだ行に返す」）。
        """
        for r in range(1, len(self._hop_vars) + 2):
            for w in self._hop_grid.grid_slaves(row=r):
                w.destroy()
        hops = max(len(self._wp_vars) - 1, 0)
        old = self._hop_vars
        self._hop_vars = []
        self._hop_result_labels = []
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
            # 結果（読み取り専用）＝実行するとこの 3 つに入る。数値は右寄せで
            # 桁を揃える（画面パネルと同じ約束＝単位は見出しが持つ）。
            cells: dict[str, ttk.Label] = {
                "rx":     ttk.Label(self._hop_grid, text="", anchor="e", width=12),
                "margin": ttk.Label(self._hop_grid, text="", anchor="e", width=12),
                "status": ttk.Label(self._hop_grid, text="", anchor="w", width=6),
            }
            for col, key in enumerate(("rx", "margin", "status"), start=4):
                cells[key].grid(row=i + 1, column=col, padx=4, pady=1, sticky="w")
            self._hop_result_labels.append(cells)

    # ----------------------------------------------------------
    # 実行
    # ----------------------------------------------------------
    def _collect_path(self) -> mh.MultiHopPath:
        """画面 → `MultiHopPath`（**ここでしか組み立てない**）。

        ⚠️ **座標は `coords.parse_pair` が読む**（I-070 ②）＝以前はここで
        `split(",")` ＋ `float()` と手読みしており、**DMS 表記を弾いていた**
        （「入力は DD / DMS のどちらも受ける」という約束が、この窓だけ
        成り立っていなかった）。**この窓が経路を組み立てる唯一の口**なので、
        ここを直せば実行・保存・`.rsproj` の全経路に一度に効く。
        """
        waypoints: list[mh.Waypoint] = []
        for i, vars_ in enumerate(self._wp_vars, start=1):
            try:
                lat, lon = coords.parse_pair(vars_["coord"].get())
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
        self._clear_hop_results()
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
            self._show_hop_result(i, pr)
            self._prog_bar.config(value=i)
        elif kind == "complete":
            self._on_complete(args[0])
        elif kind == "error":
            self._on_error(args[0])

    def _show_hop_result(self, index: int, pr) -> None:
        """区間 `index`（1 始まり）の行へ結果を返す（I-041）。

        区間名は行がすでに持っている（`_sync_hops` が「A → B」を書いた列）ので、
        ここでは値だけを入れる＝**同じ名前を 2 か所に出さない**。

        判定は `batch.PathResult.status`＝**成果物だけ失敗した区間も ERR** になる
        （I-010・出所は 1 か所）。
        """
        if not (1 <= index <= len(self._hop_result_labels)):
            return                          # 実行中に地点が変わった＝返す先が無い
        cells  = self._hop_result_labels[index - 1]
        status = pr.status
        r      = pr.result
        cells["rx"].config(text=f"{r.p_rx:.2f}" if r is not None else "—")
        cells["margin"].config(
            text=f"{r.actual_margin:+.2f}" if r is not None else "—")
        cells["status"].config(
            text={"OK": "OK", "NG": "NG"}.get(status, "ERR"),
            foreground=theme.verdict_colors(self)[theme.verdict_key(status)])

    def _clear_hop_results(self) -> None:
        """区間表の結果列を空にする（実行の開始時＝前回の結果を残さない）。"""
        for cells in self._hop_result_labels:
            for cell in cells.values():
                cell.config(text="")

    def _refresh_verdict_colors(self) -> None:
        """テーマ切替で判定色を貼り直す（表示中の字から色のキーを引く）。"""
        colors = theme.verdict_colors(self)
        for cells in self._hop_result_labels:
            text = str(cells["status"].cget("text"))
            if text:
                cells["status"].config(foreground=colors[theme.verdict_key(text)])

    def _on_complete(self, run: mh.MultiHopRun) -> None:
        self._running = False
        self._pump.stop()
        self._run_btn.config(state="normal")
        self._prog_bar.config(value=0)
        self._prog_label.config(text="")
        self._last_run = run

        # **全体判定＋どの区間が決めているか**を必ず併記する（min だけ出さない）。
        # 集約の語と符号は `mh.overall_display` が単一ソース（I-052）＝レポートの
        # カードと同じ語・同じ数字になる。
        label_key, margin_txt = mh.overall_display(run, digits=2)
        worst  = run.worst
        worst_label = "—"
        if worst is not None:
            idx = run.hops.index(worst)
            worst_label = f"#{idx + 1} {mh.hop_label(run.path, idx)}"
        self._summary_label.config(text=i18n.t("mh_summary").format(
            status="OK" if run.ok else "NG",
            label=i18n.t(label_key),
            margin=margin_txt,
            worst=worst_label))

        # **バッチと同じ 3 択**（合成シート / 全ページ / 保存先）。`report_all.html`
        # は作っていたのに開く導線がどこにも無く、事実上「無い機能」だった。
        choice = dialogs.choose(
            self, i18n.t("dlg_saved_title"),
            i18n.t("scn_saved_msg").format(dir=run.save_dir),
            [("report", i18n.t("dlg_open_report")),
             ("all",    i18n.t("dlg_open_all")),
             ("folder", i18n.t("dlg_open_folder"))],
        )
        if choice == "report":
            os.startfile(os.path.join(run.save_dir, "route.html"))
        elif choice == "all":
            os.startfile(os.path.join(run.save_dir, "report_all.html"))
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
