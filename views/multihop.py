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
import weakref
from tkinter import ttk
from typing import Callable

from core import coords
from core import failure
from core import i18n
from core import simulation as sim
from core import units
from report import multihop as mh
from views import dialogs, frozen_common, theme, window_fit
from views.multihop_map import _MapSinkMixin
from views.progress import ProgressPump

# 地点の既定の地上高（ランチャーの h_tx を初期値に使う）。
_DEFAULT_NAMES = ("TX", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "RX")

# 座標欄の幅は `coords` が単一ソース（B-046）＝窓ごとに数字を書かない。
_COORD_WIDTH = coords.DISPLAY_WIDTH_CHARS


class MultiHopWindow(_MapSinkMixin, tk.Toplevel):
    """中継経路ウィンドウ（ランチャーが唯一のインスタンスを持つ）。"""

    # 幅の**下限**（実寸は中身から決まる＝`_fit_to_content`）。980 だったころは
    # 下限が中身（ja で 879px・en 847px）を上回っており、**中身が何であっても
    # 980px の窓が開いていた**＝右に 100px 前後の死んだ余白（2026-08-08 ユーザー
    # 指摘・B-053）。下限は「これ以上細くしない」ためのもので、既定幅を決める場所
    # ではない。⇒ 中身より下に置く（`minsize` も同時に下げること＝あちらが上なら
    # `geometry()` を上書きして効かない）。
    # 🔴 **780 → 640 は B-108 の道連れ**（2026-08-18）＝案件情報の帯を折って幅を
    # 返した瞬間、こんどは**下限が窓幅を決める側に回った**（en の必要幅 683px に対し
    # 下限 780px＝97px の死んだ余白）。**B-053 で 980 → 780 に下げたとき、下限は
    # 「そのときの帯の要求幅」に合わせていた**ので、帯が痩せると取り残される。
    # ⇒ 中身のうち**最も細い言語**（en 683px・ja は 777px）より下に置く。
    _BASE_W = 640

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
        # ⚠️ `minsize` は `_BASE_W` より下に置く＝上にすると Tk が `geometry()` を
        # 上書きし、下限を下げても窓が細くならない（B-053 で実際に踏んだ）。
        self.minsize(620, 560)

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
        # 実行に出したときの各区間の入力（`_clear_hop_results` が控える）＝結果を
        # 行へ返してよいかの照合に使う（B-102・複数経路の `_run_inputs` と同じ形）。
        self._run_hop_inputs: list[tuple] = []

        # 画面の状態＝waypoint 列とホップ別 RF（**これが source of truth**）。
        self._wp_vars:  list[dict[str, tk.StringVar]] = []
        self._hop_vars: list[dict[str, tk.StringVar]] = []
        # 区間表の結果セル（`_sync_hops` が区間行と一緒に作り直す）＝結果は
        # **その結果を生んだ区間の行**に返る（I-041・結果一覧は廃止した）。
        self._hop_result_labels: list[dict[str, ttk.Label]] = []
        # 区間の見出し（`A → B`）＝**行とは別に持つ**（B-073）。地点名が変わった
        # ときに**見出しだけ**を書き換えるため＝行ごと作り直すと結果列も消える。
        self._hop_name_labels: list[ttk.Label] = []

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
            # ⚠️ **高さも送受で振り分ける**（B-055）＝座標は `start`/`end` を
            # 振り分けていたのに、高さは 2 点とも `h_tx` を入れていた。ランチャーで
            # 送信 30m / 受信 10m と入れても**受信点が 30m で始まる**（気づかず
            # 実行すると受信側を 30m として計算する）。複数経路の表は最初から
            # `h_tx` / `h_rx` を別々に凍結している（`_frozen_row`）＝⑧の漏れ。
            # ⚠️ **中継点は `h_tx` のまま**（ランチャーに対応する値が無い）。
            tx, rx = self._launcher_endpoints()
            self._add_waypoint(_DEFAULT_NAMES[0],  lat=tx[0] if tx else None,
                               lon=tx[1] if tx else None,
                               h=self._base_params.h_tx)
            self._add_waypoint(_DEFAULT_NAMES[-1], lat=rx[0] if rx else None,
                               lon=rx[1] if rx else None,
                               h=self._base_params.h_rx)
        self._fit_to_content()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # 以後の測り直しは畳んでよい（I-107）＝組み立て中は同期のまま。
        window_fit.ready(self)

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
            self._wp_vars.append(self._new_wp_vars(
                name=wp.name,
                coord=coords.format_pair(wp.lat, wp.lon, self._coord_format),
                height=f"{wp.h:.1f}"))
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
        self._run_btn = ttk.Button(bar, text=i18n.t("btn_run"),
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
        # ⚠️ **この窓だけ 2 行に折る**（2026-08-18・B-108）。欄の行と道具の行を
        # 分けると、帯の要求幅は**両者の max**になる（1 行に並べると和になる）。
        fields = ttk.Frame(case)
        fields.pack(fill="x")
        for key, var in (("batch_project_name", self._project_var),
                         ("batch_memo",         self._memo_var)):
            f = ttk.Frame(fields)
            f.pack(side="left", padx=6, fill="x", expand=(key == "batch_memo"))
            ttk.Label(f, text=i18n.t(key)).pack(side="left")
            ttk.Entry(f, textvariable=var, state="readonly", width=20).pack(
                side="left", padx=(2, 0), fill="x", expand=(key == "batch_memo"))
            ttk.Label(f, text="🔒").pack(side="left", padx=(2, 0))

        # 🔒 の意味と ↻ は**凍結した領域の先頭（案件情報の帯）の右側**（条件探索と
        # 同じ置き方＝B-052）。以前は共通設定の 2 行目にあり、`↻`（`width=18` の
        # 文字数固定で 158px）と `🔒 ランチャーの値`（98px）が **6 欄のうち後半 3 欄と
        # 同じ行を分け合って**いた。その結果**この帯だけで 859px を要求し、窓幅を
        # 決めていた**（区間表 738px・地点表 605px）。移すと帯は 620px 台になり、
        # **窓幅は区間表＝この窓の主たる中身が決める**。
        # ⚠️ この 2 つは帯 1 つではなく**凍結領域全体**に効く（`↻` は案件情報と共通
        # 設定をまとめて取り込む＝`_refresh_from_launcher`）ので、先頭のほうが実態に
        # 近い。⚠️ `width=` は付けない＝文字数で幅を固定すると中身より広い帯を要求
        # する（I-046 で「実行」から外したのと同じ理由）。
        #
        # 🔴 **ただし B-053 で移した先が、こんどはここで窓幅を決めていた**（B-108）＝
        # 欄 2 つ（各 20 文字）と ↻ と説明を**1 行に並べる**と帯は ja 764 / en 781px を
        # 要求し、区間表（ja 741 / en 647px）を上回る。**共通設定を軽くした分が素直に
        # こちらへ移っていた**＝帯を 1 つ測るだけでは凍結領域の要求幅は下がらない。
        # ⇒ **欄を細くするのではなく、道具を次の行へ折る**（案件名・メモは利用者の
        # 文字列をそのまま見せる欄で、下限を削ると読めなくなる＝B-046 と同じ筋）。
        # この窓は**横が狭く縦に余裕がある**ので、共通設定を 3 欄で折ったのと同じ判断。
        # ⚠️ **複数経路・条件探索は 1 行のまま**＝あちらは主たる中身（共通設定 1157〜
        # 1238px・比較グリッド）が帯より広く、折っても幅は 1px も減らないうえ、条件
        # 探索は高さ 986px で FHD の天井が近い（行を足すほうが害になる）。**帯に出す
        # 項目は正典（`frozen_common`）だが、並べ方は窓ごと**＝ここは並べ方の話。
        tools = ttk.Frame(case)
        tools.pack(fill="x", pady=(2, 0))
        ttk.Button(tools, text=i18n.t("btn_refresh_common"),
                   command=self._refresh_from_launcher).pack(side="right", padx=(6, 0))
        ttk.Label(tools, text=i18n.t("hint_common_readonly"),
                  foreground=theme.muted_foreground(tools)).pack(side="right", padx=6)

        common = ttk.LabelFrame(parent, text=i18n.t("batch_common_cfg"), padding=(8, 2))
        common.pack(fill="x", pady=(0, 6))
        # 🔴 **項目は複数経路と同じ 11 個**（2026-08-18・I-101）。長らく 6 個しか
        # 出しておらず、**K ファクターと回折モデルは実行に効くのに画面のどこにも
        # 出ていなかった**。⚠️ **一覧はここに書かない**＝出す項目の正典は
        # [views/frozen_common](frozen_common.py)（窓ごとに並べると、また片方だけ
        # 増えて気づかれない）。この窓が決めるのは**並べ方だけ**。
        # ⚠️ **3 欄で折る**＝複数経路のように 1 行へ並べると帯が窓幅を決めてしまう
        # （B-053 で 859 → 620px 台に下げた成果を食う方向。窓新設時にも 6 欄を 1 行に
        # 並べて 125%/150% で 2088px を要求し、横断ゲートが止めている）。**窓幅を
        # 決めてよいのは区間表**＝`test_the_frozen_header_does_not_decide_the_
        # multihop_window_width` がそれを固定する。高さは増えるが縦には余裕がある。
        self._common_vars = frozen_common.readonly_band(
            common, fold=3,
            widths={"freq_mhz": 7, "p_tx": 7, "sens": 7,
                    # 段階の欄は**ラベルの長さから引く**（B-149）＝既定の 6 では
                    # `高（約 5 m）` の時点で既に切れていた（B-148 でさらに悪化）。
                    "resolution": frozen_common.resolution_field_width(),
                    "env_type": 9, "diff_method": 9})
        self._update_frozen()

    def frozen_common_keys(self) -> "set[str]":
        """凍結帯「共通設定」に**実際に出している**項目のキー集合（I-101）。

        複数経路（`BatchBuilderWindow.frozen_common_keys`）と**同じ集合**であること
        を `tests/test_ui_consistency.py` が要求する。⚠️ **将来 6 つ目の実行フローを
        作るときも、帯の項目集合はそのゲートで自動的に縛られる**（それがこのメソッドを
        窓ごとに置く理由＝一覧を 1 か所に書くと、窓を足した人が登録し忘れる）。
        """
        return set(self._common_vars)

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
        """中身に合わせて測り直す。**連続した呼び出しは 1 回に畳む**（I-107）。

        ⚠️ 地点の増減・地点名の 1 文字ごと（`trace_add`）と**利用者の 1 操作ごとに
        走る**経路で、1 回 0.22 秒かかる（実測）。畳むのは回数だけで、追従そのものは
        落とさない（[views/window_fit](window_fit.py) の `fit_soon` の註）。
        """
        window_fit.fit_soon(self, self._fit_now)

    def _fit_now(self) -> None:
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
            var.set(frozen_common.display_value(attr, getattr(p, attr)))

    def _refresh_from_launcher(self) -> None:
        """ランチャーの現在値を取り込み直す（**黙って追従しない**＝凍結方式）。"""
        self._meta = self._snapshot_meta()
        if self._config_provider is not None:
            try:
                self._base_params = sim.SimParams(self._config_provider())
            except Exception as ex:
                dialogs.alert(self, i18n.t("dlg_input_error"), failure.message(
                    what=i18n.t("fail_refresh_common"),
                    hint=i18n.t("fix_check_input"), detail=str(ex)))
                return
        self._update_frozen()
        # 共通設定も区間結果の入力なので、取り込み直したら古い結果は結果でなくなる
        # （B-059）。⚠️ **凍結方式は「黙って追従しない」であって「古い数字を残す」
        # ではない**＝↻ を押した人は新しい前提を入れたつもりでいる。
        self._drop_stale_hop_results()

    # ----------------------------------------------------------
    # 地点の増減
    # ----------------------------------------------------------
    def _add_waypoint(self, name: str = "", lat: "float | None" = None,
                      lon: "float | None" = None, h: "float | None" = None,
                      index: "int | None" = None) -> None:
        """地点を足す。**2 点あるときは「間」に入れる**（＝中継点として足す）。

        ⚠️ 末尾に足すと「TX → RX → R1」という並びになり、**それまで受信点だった
        地点が中継点に化ける**（実装直後のスクリーンショットで実際にそうなった）。
        利用者の頭の中は「送信点と受信点があって、その間に中継点を置く」なので、
        **先頭＝送信点・末尾＝受信点を固定**し、新しい点はその手前へ挿す。

        `index` を渡すと**その位置へ**挿す（I-074＝行ごとの `＋`）。既定（`None`）は
        従来どおり受信点の手前。⚠️ **範囲は呼び出し側が保証する**のではなく、ここで
        `1 <= index <= last` に丸める＝送信点より前と受信点より後ろには挿さらない
        （先頭・末尾が固定という不変条件は、口が増えても 1 か所で守る）。
        """
        if len(self._wp_vars) >= mh.MAX_HOPS + 1:
            dialogs.alert(self, i18n.t("dlg_input_error"),
                          i18n.t("mh_err_too_many").format(max=mh.MAX_HOPS))
            return
        coord = ("" if lat is None or lon is None
                 else coords.format_pair(lat, lon, self._coord_format))
        # 高さの既定は `h_tx`。⚠️ **受信点だけは呼び出し側が `h_rx` を渡す**
        # （B-055）＝ここで「末尾なら h_rx」と判断してはいけない。この関数は
        # **中継点の挿入にも使われ、そのとき末尾は受信点のまま動かない**ので、
        # 位置から高さを決めると「地点を足したら誰かの高さが変わる」ことになる。
        vars_ = self._new_wp_vars(
            name=name or self._next_relay_name(),
            coord=coord,
            height=f"{self._base_params.h_tx if h is None else h:.1f}")
        if len(self._wp_vars) >= 2:
            last = len(self._wp_vars) - 1
            at = last if index is None else max(1, min(index, last))
            self._wp_vars.insert(at, vars_)                       # 既定＝受信点の手前
            # 🔴 **区間の設定も一緒に挿す＝`_delete_waypoint` の逆写像**（I-074）。
            # `_sync_hops` はホップ行を**先頭から詰め直す**（`old[i]` を位置で
            # 再利用する）ので、地点だけ挿すと**挿した位置より後ろの周波数・利得が
            # 1 つ後ろへずれる**。画面は自然に見えたまま、黙って別の区間の設定で
            # 計算する＝削除側で実際に起きた実害（I-045）と同型。
            #
            # 挿すのは **`at` 番目のホップ＝挿した地点から「出ていく」側を空欄で**。
            # `at - 1`（入ってくる側）に既存の設定が残るのは、そちらが分割前の区間と
            # **始点が同じ**だから＝利用者の入力の意味が変わらない。
            # ⇒ 同じ位置を消せば `_hop_vars.pop(at)` で**元へ戻る（往復で同一）**。
            self._hop_vars.insert(at, self._new_hop_vars())
        else:
            self._wp_vars.append(vars_)
        self._render_waypoints()

    # ----------------------------------------------------------
    # 入力の変数（**生成の口はこの 2 つだけ**）
    # ----------------------------------------------------------
    # 🔑 **口を 1 本にしてあるのは、結果を無効にする配線を貼る場所を 1 か所に
    # するため**（B-059）。地点の変数は以前 `_add_waypoint` と `_apply_path` の
    # 2 か所で作られており、そこへ個別に配線すると**3 か所目を足した人が黙って
    # 落とす**（列挙で塞いだ穴は名前 1 つで開く＝[[feedback-promote-recurring-checks]]）。
    def _new_wp_vars(self, name: str, coord: str, height: str) -> dict:
        """地点 1 行分の入力変数。**数値を生む 2 つだけ**が結果の無効化に効く。

        ⚠️ `name` は入れない＝地点名は**結果の数字を作らない**（区間の見出しに
        出るだけ）。名前を直しただけで受信レベルが消えるのは、消しすぎの側の
        欠陥（B-058）と同じ型になる。
        """
        vars_ = {"name":   tk.StringVar(value=name),
                 "coord":  tk.StringVar(value=coord),
                 "height": tk.StringVar(value=height)}
        for key in ("coord", "height"):
            self._watch_input(vars_[key])
        # 名前は**別系統**で見張る（B-073）＝区間の見出しに写るだけで、結果の鮮度には
        # 一切効かない。⚠️ `_watch_input` に相乗りさせてはいけない（そちらは結果を
        # 捨てる側なので、名前を直した瞬間に受信レベルが消える）。
        self._watch_name(vars_["name"])
        return vars_

    def _new_hop_vars(self) -> dict:
        """区間 1 行分の無線諸元。3 つとも結果の数字を作るので全部が効く。"""
        vars_ = {"freq": tk.StringVar(), "gain_tx": tk.StringVar(),
                 "gain_rx": tk.StringVar()}
        for var in vars_.values():
            self._watch_input(var)
        return vars_

    def _watch_input(self, var: tk.StringVar) -> None:
        """値が変わったら結果の鮮度を見直す（B-059）。

        🔴 **コールバックに `self` を持たせない**（B-050）。`trace_add` が登録する
        Tcl コマンドは **C レベルを経由して**変数 → 窓 → 変数と循環するので、
        窓を掴んだまま登録すると **Python の GC が切れず、閉じた窓が永久に残る**
        （条件探索が実際に踏んで 65 個/開閉で積み上がった穴）。
        ⚠️ **条件探索の処方（id を控えて `_on_close` で外す）はここでは効かない**＝
        地点の追加・削除で**変数そのものが動的に捨てられる**ので、後始末を書く
        場所が窓の閉じ際だけでは足りず、捨てる箇所を数え上げれば必ず漏れる。
        ⇒ **弱参照にして循環を作らない**＝後始末の場所が 1 つも要らなくなる。
        """
        ref = weakref.ref(self)
        def _on_write(*_args, _ref=ref) -> None:
            win = _ref()
            if win is not None:
                win._drop_stale_hop_results()
        var.trace_add("write", _on_write)

    def _watch_name(self, var: tk.StringVar) -> None:
        """地点名が変わったら、区間の見出しを追従させる（B-073）。

        ⚠️ **弱参照なのは `_watch_input` と同じ理由**（B-050＝`self` を掴むと窓が
        GC されない）。地点の追加・削除で変数ごと捨てられるので、後始末の場所を
        数え上げる方式は必ず漏れる。
        """
        ref = weakref.ref(self)
        def _on_write(*_args, _ref=ref) -> None:
            win = _ref()
            if win is not None:
                win._refresh_hop_labels()
        var.trace_add("write", _on_write)

    # ----------------------------------------------------------
    # 結果が結果でなくなる時（I-041 / B-059）
    # ----------------------------------------------------------
    def _hop_input(self, index: int) -> tuple:
        """区間 `index`（1 始まり）の結果を生んだ入力の控え。

        🔑 **中継はバッチと違い、1 つの結果の入力が 2 つの表にまたがる**＝区間 k は
        **地点 k と k+1**（座標・高さ）と**区間 k 自身**（周波数・利得）から出る。
        さらに**凍結した共通設定**（`↻ ランチャーから更新` で差し替わる）も入力
        なので、3 つ目として指紋を混ぜる。
        """
        wp = self._wp_vars
        return (
            tuple(wp[i][k].get() for i in (index - 1, index)
                  for k in ("coord", "height")),
            tuple(self._hop_vars[index - 1][k].get()
                  for k in ("freq", "gain_tx", "gain_rx")),
            self._common_fingerprint(),
        )

    def _hop_count(self) -> int:
        """`_hop_input(i)` を安全に呼べる区間の数（1..この値）。

        ⚠️ **3 つの列（区間行・区間変数・地点）の一番短いところで測る**＝表を
        作り直している最中に呼ばれても添字で落ちないこと（トレースは値が書かれた
        瞬間に走るので、地点表とホップ表が揃う前に来る経路があり得る）。
        """
        return min(len(self._hop_result_labels), len(self._hop_vars),
                   max(len(self._wp_vars) - 1, 0))

    def _common_fingerprint(self) -> tuple:
        """凍結した共通設定の**値**の指紋。

        ⛔ **`repr(self._base_params)` を使ってはいけない**＝`SimParams` は
        dataclass ではないので既定の `repr` は**オブジェクトの番地**になる。
        ↻ は毎回新しい `SimParams` を作るので、**中身が 1 つも変わっていなくても
        指紋が変わり、押すたびに結果が消える**（＝「毎回鳴る」壊れ方）。
        """
        return tuple(sorted((k, repr(v))
                            for k, v in vars(self._base_params).items()))

    def _drop_stale_hop_results(self, *_trace) -> None:
        """**それを生んだ入力が変わった区間**の結果だけを消す（I-041 の規則）。

        規則は B-058 と 1 本＝**結果は、それを生んだ入力が変わった時点で結果で
        なくなる**。B-058（複数経路）は「消しすぎ」＝キーを押しただけで消えた側で、
        こちらは「消さなすぎ」＝**入力を変えても前の区間結果が残る**側だった
        （消す口が実行の開始時にしか呼ばれていなかった）。

        ⚠️ **引き金は個々の欄ではなく「どれかが変わったら全区間を照合」**にする。
        地点を 1 つ動かすと**その前後 2 区間**が同時に古くなるので、欄と区間を
        対応づける配線を持つと**必ず片側を落とす**。区間は最大 8 なので総当たりで
        足りる。
        """
        # ⚠️ **表を作り直している最中に呼ばれても落ちないこと**＝トレースは値が
        # 書かれた瞬間に走るので、地点表とホップ表が揃う前に来る経路があり得る。
        hops = self._hop_count()
        for index, cells in enumerate(self._hop_result_labels[:hops], start=1):
            if not cells["status"].cget("text") and not cells["rx"].cget("text"):
                continue                    # そもそも結果が入っていない
            if getattr(cells["status"], "_hop_input", None) != self._hop_input(index):
                for cell in cells.values():
                    cell.config(text="")

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
            # 行ごとの追加（I-074）＝**この行の下に中継点を挿す**。`×` と対にする
            # ための列で、⚠️ **受信点の行には出さない**（末尾は受信点固定なので、
            # その下に挿せる場所が無い）。⇒ `＋` は送信点〜最後の中継点、`×` は
            # 中継点だけ＝**押せるのに何も起きないボタンを置かない**という同じ規則。
            if i < last:
                ttk.Button(self._wp_grid, text="＋", width=2, cursor="hand2",
                           command=lambda idx=i: self._add_waypoint(index=idx + 1)
                           ).grid(row=row, column=4, padx=(4, 0), pady=1)
            # 行ごとの削除（I-045）＝複数経路の表と同じ `×`。
            # ⚠️ **送信点・受信点には出さない**＝消せないという現在の不変条件を、
            # そのまま画面で表す（押せるのに何も起きないボタンを置かない）。
            if 0 < i < last:
                ttk.Button(self._wp_grid, text="×", width=2, cursor="hand2",
                           command=lambda idx=i: self._delete_waypoint(idx)).grid(
                    row=row, column=5, padx=4, pady=1)
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
        self._hop_name_labels = []
        for i in range(hops):
            vars_ = old[i] if i < len(old) else self._new_hop_vars()
            self._hop_vars.append(vars_)
            name_lbl = ttk.Label(self._hop_grid, text=self._hop_label_text(i))
            name_lbl.grid(row=i + 1, column=0, padx=4, pady=1, sticky="w")
            self._hop_name_labels.append(name_lbl)
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

    def _hop_label_text(self, i: int) -> str:
        """区間 `i` の見出し（`A → B`）。**文言の出所はここ 1 つ**。

        作る側（`_sync_hops`）と直す側（`_refresh_hop_labels`）で別々に組み立てると、
        片方だけ書式が動いたときに**同じ窓の中で見出しの形が 2 種類**になる。
        """
        return (f"{self._wp_vars[i]['name'].get()} → "
                f"{self._wp_vars[i + 1]['name'].get()}")

    def _refresh_hop_labels(self) -> None:
        """区間の見出しだけを地点名に追従させる（B-073）。

        🔴 **行を作り直さない**＝`_sync_hops` を呼ぶと結果列も一緒に消える。
        地点名は**結果の数字を作らない**ので（`_new_wp_vars` の約束・B-058/B-059）、
        名前を直しただけで受信レベルが消えるのは「消しすぎ」側の欠陥になる。
        ⇒ 触るのは `text` だけ。

        🔴 **触ったら測り直す**（2026-08-18・B-100）＝見出しは `地点名 → 地点名` で
        **利用者の入力に従って伸びる**のに、`text` を書き換えるだけでは窓に伝わらず、
        伸びたぶんが右へ押し出されて**「判定」列が見切れる**（実測＝区間表が
        647 → 993px に伸びても窓は 801px のまま）。⚠️ **行を作り直さないことと、
        測り直さないことは別**＝前者は B-073 で正しく、足りないのは後者の 1 手。
        ⚠️ 既定の地点名（`TX`/`R1`/`RX`）では表が窓より狭いままなので**再現しない**
        ＝固定データで試している限り出ない欠陥だった。
        """
        changed = False
        for i, lbl in enumerate(self._hop_name_labels):
            if i + 1 >= len(self._wp_vars):
                break               # 地点の増減の途中＝この後 `_sync_hops` が来る
            try:
                lbl.configure(text=self._hop_label_text(i))
                changed = True
            except tk.TclError:
                break               # 破棄済みの行（作り直しと競合した）＝次の描画に任せる
        if changed:
            # ⚠️ **1 文字ごとに走る**（`trace_add` 経由）が、`fit_to_content` は
            # `grow_only` なので**広がる方向にしか動かない**＝打っている最中に窓が
            # 伸び縮みして揺れることはない。
            try:
                self._fit_to_content()
            except tk.TclError:
                pass                # 破棄途中の窓（閉じ際に変数が書き換わる経路）

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
            dialogs.alert(self, i18n.t("dlg_input_error"),
                          failure.listing(str(ex).splitlines()))
            return
        errors = mh.validate_path(path)
        if errors:
            dialogs.alert(self, i18n.t("dlg_input_error"),
                          failure.listing(errors))
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
            on_hop_progress = lambda done, tot: None,
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

        ⚠️ **実行中も地点表は編集できる**（止めているのは実行ボタンだけ）。
        🔴 **添字だけで引いてはいけない**（B-102）＝地点を消すと後ろの区間が繰り上がる
        ので、`TX → R1` の結果が**別の地点対を表す行**（`TX → RX`）へ入る。下の早期
        return は**末尾が縮んだときにしか効かない**（範囲内に居続けるため）。
        ⇒ **実行に出したときの入力と照合し、違えば書かない**（複数経路＝B-068 と
        同じ処方・規則は B-058／B-059 と 1 本＝**結果は、それを生んだ入力が変わった
        時点で結果でなくなる**）。計算そのものは開始時に凍結した経路で走るので、
        **成果物（レポート・CSV）は元から正しい**＝直しているのは画面だけ。
        """
        if not (1 <= index <= len(self._hop_result_labels)):
            return                          # 実行中に地点が変わった＝返す先が無い
        sent = (self._run_hop_inputs[index - 1]
                if index <= len(self._run_hop_inputs) else None)
        if sent is not None and (index > self._hop_count()
                                 or self._hop_input(index) != sent):
            return                          # 実行に出した区間ではない（実行中に編集された）
        cells  = self._hop_result_labels[index - 1]
        status = pr.status
        r      = pr.result
        # 桁は `units.format_db` が単一ソース（0.1 dB）＝**画面とレポートで
        # 精度の主張を食い違わせない**（個別シミュレーションの欄は元から 0.1 dB）。
        cells["rx"].config(
            text=units.format_db(r.p_rx) if r is not None else "—")
        cells["margin"].config(
            text=units.format_db(r.actual_margin, signed=True)
            if r is not None else "—")
        cells["status"].config(
            text={"OK": "OK", "NG": "NG"}.get(status, "ERR"),
            foreground=theme.verdict_colors(self)[theme.verdict_key(status)])
        # **この結果を生んだ入力**を控える（B-059）。以後この控えから変わった
        # ときだけ消える＝欄を覗いただけ・名前を直しただけでは消えない。
        setattr(cells["status"], "_hop_input", self._hop_input(index))

    def _clear_hop_results(self) -> None:
        """区間表の結果列を空にする（実行の開始時＝前回の結果を残さない）。

        あわせて**実行に出した各区間の入力の控え**を取る（`_show_hop_result` が
        使う＝B-102）。複数経路の `_clear_verdicts` と同じ形で、控えるのは
        「開始時の姿」だけ＝実行中に画面がどう変わっても、この控えは動かない。
        """
        self._run_hop_inputs = [self._hop_input(i)
                                for i in range(1, self._hop_count() + 1)]
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
        # カードと同じ語・同じ数字になる。**状態語は `mh.overall_status`**（B-071）
        # ＝`run.ok` の二値から作ると ERROR が NG に潰れ、区間表と食い違う。
        label_key, margin_txt = mh.overall_display(run, digits=2)
        overall_status = mh.overall_status(run)
        worst  = run.worst
        worst_label = "—"
        if worst is not None:
            idx = run.hops.index(worst)
            worst_label = f"#{idx + 1} {mh.hop_label(run.path, idx)}"
        self._summary_label.config(text=i18n.t("mh_summary").format(
            status=overall_status,
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
        dialogs.alert(self, i18n.t("dlg_error"), failure.explain(
            ex, what=i18n.t("fail_run_multihop"), hint=i18n.t("fix_retry_or_log")))

    def close_window(self) -> None:
        """他所（ランチャーのプロジェクト読込）から閉じるための公開口
        （3 つの窓で名前を揃える＝内部ハンドラ名で分岐させない）。"""
        self._on_close()

    def _on_close(self) -> None:
        self._pump.stop()
        if self._on_close_cb is not None:
            self._on_close_cb()
        self.destroy()
