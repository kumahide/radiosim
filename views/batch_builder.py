"""
views/batch_builder.py
======================
バッチパス入力ウィンドウ（BatchBuilderWindow）。

2種類の入力方法を提供する：
  1. GUI テーブルへの直接入力
  2. CSV インポート（テーブルに展開）

実行は batch.run_batch() に委譲する。
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable

from core import coords
from core import i18n
from core import simulation as sim
from core.models import ENV_KEYS
from report import batch
from views import theme
from views.batch_io import _CsvMixin
from views.batch_run import _RunMixin
from views.batch_table import _TableMixin
from views.progress import ProgressPump


class BatchBuilderWindow(_TableMixin, _CsvMixin, _RunMixin, tk.Toplevel):
    """バッチ実行用のパス入力ウィンドウ。ランチャーから生成される。"""

    # 末尾から4列目＝水平距離、3列目＝判定（どちらも読み取り専用ラベル）。
    # _add_row の Entry 生成は zip(_WIDTHS[1:-2], defaults) で defaults 側（9列）に
    # 切られるため、この 2 列に Entry は作られない。
    # ⚠️ 判定列は**計算で出る列**＝水平距離（I-000）と同じ性格で、入力ではない。
    # だから CSV エクスポート（入力の契約）には出ない（I-041）。
    # ⚠️ 座標 2 列（添字 2 / 3）の幅は `coords` が単一ソース（B-046）＝ここに
    # 数字を書くと、DMS 表記のときに末尾の `E` / `W` が黙って切れる。
    _CW = coords.DISPLAY_WIDTH_CHARS
    _WIDTHS = [2, 9, _CW, _CW, 6, 6, 8, 7, 7, 11, 9, 6, 2, 2]

    # ウィンドウ既定サイズ。**幅・高さとも下限**（実寸は _fit_width_to_content が中身に合わせる）。
    _BASE_W = 1080
    _BASE_H = 700
    _MIN_W  = 880
    # 表の外周 padding（outer の padx=8 左右）。スクロールバー幅は実測する
    # （DPI スケーリングで変わるため定数にしない）。
    _TABLE_PAD_W = 16
    # 画面いっぱいまでは広げない（タスクバー・ウィンドウ枠のぶんを残す）。
    _SCREEN_MARGIN = 80

    # Common Settings の StringVar 属性名 → ランチャー config dict のキー対応。
    # 共通欄は SimParams の属性名で持つが、ランチャーは config キーで持つため変換が要る。
    _COMMON_CFG_MAP = {
        "freq_mhz": "freq",   "p_tx":     "p_tx",   "gain_tx": "gain_tx",
        "gain_rx":  "gain_rx", "sens":    "sens",   "veg_h":   "veg_h",
        "k_factor": "k_factor", "num":    "samples", "rain_rate": "rain_rate",
    }

    @property
    def _COLS(self) -> list[str]:
        return [
            "", i18n.t("col_id"), i18n.t("col_start"), i18n.t("col_end"),
            i18n.t("col_h_tx"), i18n.t("col_h_rx"), i18n.t("col_freq"),
            i18n.t("col_gain_tx"), i18n.t("col_gain_rx"), i18n.t("col_note"),
            i18n.t("col_dist"), i18n.t("html_status"), "", "",
        ]

    def __init__(
        self,
        parent: tk.Tk,
        base_params: sim.SimParams,
        config_provider: "Callable[[], dict] | None" = None,
        meta_provider:   "Callable[[], dict] | None" = None,
        load_params:     "Callable[[dict], None] | None" = None,
        on_close:        "Callable[[], None] | None" = None,
        on_paths_changed: "Callable[[], None] | None" = None,
        initial_rows:    "list | None" = None,
        coord_format:    str = "dd",
        map_opener:      "Callable[[], None] | None" = None,
    ) -> None:
        super().__init__(parent)
        self.title(i18n.t("batch_window_title"))
        # 共通設定を RF系／環境系サブグループ化した分（I-003・LabelFrame の見出し2つ分）
        # 高さが増えたため、既定サイズ・最小サイズを拡張して進捗バー行（row2）が
        # 窓外へ圧迫されない余裕を確保する（+50px では row2 がほぼ0pxに潰れ再発）。
        # ⚠️ 幅は下限でしかない＝実幅は _fit_width_to_content() が中身から決める。
        self.geometry(f"{self._BASE_W}x{self._BASE_H}")
        self.resizable(True, True)
        self.minsize(self._MIN_W, 520)

        # ランチャー連携（凍結方式）。省略時は従来挙動（往復・凍結なし）。
        self._config_provider = config_provider
        # 案件名・自由メモも Common Settings と同じくランチャー（source of truth）の
        # スナップショット。省略時は空メタ。
        self._meta_provider   = meta_provider
        self._load_params     = load_params
        # 地図を連続追加モードで開く口（I-043）＝**ランチャーが注入する**。
        # 親ウィジェットから探させない（中継窓の map_opener と同じ流儀）。
        self._map_opener      = map_opener
        # 閉じたときにランチャーへ通知するコールバック（地図の連続追加先を手放させる）。
        self._on_close        = on_close
        # パス集合（行）が変わったときにランチャー→地図へ通知するコールバック。
        # 地図の確定パス表示をリアルタイムに追従させる（削除・クリア・インポート等）。
        self._on_paths_changed = on_paths_changed
        # 一括再構築（並べ替え・インポート）中は逐次通知を抑止し、終了後に1回だけ通知する。
        self._suspend_notify   = False

        self._base_params  = base_params
        # 座標表記は app 設定に従う（人が読む report.txt/HTML のみ。データは DD 固定）。
        # 値は**開いた時点のスナップショット**をランチャーから受け取る＝窓が
        # `config.load_config()` を直に読まない（I-055 ②・2.7 スライス G2）。
        self._coord_format = coord_format
        self._row_entries: list[list[tk.Entry]] = []
        self._row_frames:  list[ttk.Frame]      = []
        self._running      = False
        # 進捗・イベントの受け渡しは単一実行と同じ部品を使う（views/progress.py）。
        # バッチのイベントは done/complete が結果を運ぶので 1 件も落とせない
        # ＝latest_only は使わない。実行ごとに start/stop する（従来は __init__ で
        # 起動して永久に回っており、単一実行とのライフサイクル非対称が B-006 の
        # 停止バグを生んだ）。
        self._pump = ProgressPump(self, self._dispatch_event)
        self._drag_row_idx: int | None          = None
        self._drag_indicator: tk.Frame | None   = None
        # 列幅同期（_sync_header_columns）のデバウンス用 after ID。
        self._sync_after_id: str | None          = None
        self._ok_count  = 0
        self._ng_count  = 0
        self._err_count = 0
        # 実行中のパス内進捗（標高取得 done/samples）をバーへ滑らかに反映するための
        # 実行時状態（_on_path_progress_tick が参照）。
        self._run_cur     = 0
        self._run_samples = 0

        self._build_ui()
        # DPI 変更などで貼り直すときは、列幅同期からやり直す（フォントが変われば
        # 列の実測幅もスクロールバーの太さも変わる）。window_fit.refit_all が使う。
        self._fit_refit = self._run_sync
        # プロジェクト（`.rsproj`）から開いたときは、その行で始める（凍結した
        # ランチャー値の 1 行ではなく）。**空リストも「行が無い」という情報**なので
        # None と区別する（`None`＝節を持たない＝従来どおり 1 行で始める）。
        if initial_rows is None:
            self._add_row()
        else:
            for row in initial_rows:
                self._add_row(row)
        self._run_sync()          # 同期 → その中で窓を中身に合わせる
        # 閉じたらランチャーへ通知する（地図が連続追加中なら座標入力へ戻す）。
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)

    # ----------------------------------------------------------
    # UI 構築
    # ----------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_case_info()
        self._build_common_settings()
        self._build_table()
        self._build_bottom()

    def _build_case_info(self) -> None:
        """案件名・自由メモ（レポートの自己同定ヘッダに載る任意メタ情報）。

        計算に影響しない報告書メタだが、**ランチャーが source of truth**＝Common
        Settings と同じくランチャーのスナップショットを 🔒 読み取り専用で表示し、
        「↻ランチャーから更新」で取り込む（シングルもバッチも同じ値を踏襲する）。
        バッチ側で直接編集はしない（source of truth を2箇所に増やさない）。案件名＝
        per-path/summary 両ヘッダ、メモ＝summary のみ。
        """
        frame = ttk.LabelFrame(self, text=i18n.t("batch_case_info"), padding=(8, 4))
        frame.pack(fill="x", padx=8, pady=(8, 0))

        meta = self._meta_provider() if self._meta_provider is not None else {}
        self._project_name_var = tk.StringVar(value=meta.get("project_name", ""))
        self._memo_var         = tk.StringVar(value=meta.get("memo", ""))

        f_proj = ttk.Frame(frame)
        f_proj.pack(side="left", padx=6)
        ttk.Label(f_proj, text=i18n.t("batch_project_name")).pack(side="left")
        ttk.Entry(f_proj, textvariable=self._project_name_var,
                  width=20, state="readonly").pack(side="left", padx=(2, 0))
        ttk.Label(f_proj, text="🔒").pack(side="left", padx=(2, 0))

        f_memo = ttk.Frame(frame)
        f_memo.pack(side="left", padx=6, fill="x", expand=True)
        ttk.Label(f_memo, text=i18n.t("batch_memo")).pack(side="left")
        ttk.Entry(f_memo, textvariable=self._memo_var,
                  state="readonly").pack(side="left", padx=(2, 0), fill="x", expand=True)
        ttk.Label(f_memo, text="🔒").pack(side="left", padx=(2, 0))

        # 🔒 の意味と ↻ は**凍結した領域の 1 行目（この行）の右側**＝条件探索・中継
        # 経路と同じ置き場（2026-08-08・B-053 のクラス点検で 3 窓を揃えた）。
        # **以前は共通設定の中の独立行**で、理由は「row1 は日本語ラベルで幅が逼迫し、
        # 右詰めのボタンが窓外へ押し出される（B-002 系）」だった。⇒ **その理由は
        # ここへ移すと消える**（この行は 2 欄しか無い）。行が 1 つ減るので窓も低くなる。
        # ⚠️ この 2 つは帯 1 つでなく**凍結領域全体**に効く（`↻` は共通設定と案件情報を
        # まとめて取り込む＝`_refresh_common_from_launcher`）ので、1 行目が実態に近い。
        if self._config_provider is not None:
            ttk.Button(
                frame, text=i18n.t("btn_refresh_common"),
                command=self._refresh_common_from_launcher,
            ).pack(side="right", padx=(6, 0))
            ttk.Label(
                frame, text=i18n.t("hint_common_readonly"),
                foreground=theme.muted_foreground(frame),
            ).pack(side="right", padx=6)

    def _build_common_settings(self) -> None:
        frame = ttk.LabelFrame(
            self, text=i18n.t("batch_common_cfg"),
            padding=(8, 4),
        )
        frame.pack(fill="x", padx=8, pady=(8, 0))

        self._common_vars: dict[str, tk.StringVar] = {}

        # RF系／環境系をサブグループ化して境界を明示する（I-003）。見出しは
        # ランチャー側の同義グループ名（grp_radio_settings/grp_environment）を
        # 再利用し、アプリ全体で用語を揃える。
        grp_rf = ttk.LabelFrame(frame, text=i18n.t("grp_radio_settings"), padding=(6, 2))
        grp_rf.pack(fill="x")
        grp_env = ttk.LabelFrame(frame, text=i18n.t("grp_environment"), padding=(6, 2))
        grp_env.pack(fill="x", pady=(4, 0))

        row0 = ttk.Frame(grp_rf)
        row0.pack(fill="x")
        row1 = ttk.Frame(grp_env)
        row1.pack(fill="x")

        def _field(parent: tk.Widget, label: str, attr: str, width: int = 8) -> None:
            f = ttk.Frame(parent)
            f.pack(side="left", padx=6)
            ttk.Label(f, text=label).pack(side="left")
            var = tk.StringVar(value=str(getattr(self._base_params, attr)))
            self._common_vars[attr] = var
            # 共通設定はランチャー（source of truth）のスナップショット。直接編集
            # させず「↻ランチャーから更新」で取り込む（凍結方式の対称化）。
            # 素の tk.Entry＋背景色ハードコードだと sv_ttk のテーマに追従せず、
            # ダークモードで薄グレー背景×白文字になり読めない（B-001）。ttk.Entry の
            # readonly ならライト/ダーク双方でテーマ配色になる。
            ttk.Entry(
                f, textvariable=var, width=width,
                state="readonly",
            ).pack(side="left", padx=(2, 0))
            # 🔒はテーマ配色に依存しないグリフなので、ライト/ダーク双方で
            # 「編集不可（ランチャーからの凍結値）」の視覚キューになる（I-004）。
            ttk.Label(f, text="🔒").pack(side="left", padx=(2, 0))

        _field(row0, i18n.t("lbl_b_freq"),    "freq_mhz")
        _field(row0, i18n.t("lbl_b_p_tx"),   "p_tx")
        _field(row0, i18n.t("lbl_b_gain_tx"), "gain_tx")
        _field(row0, i18n.t("lbl_b_gain_rx"), "gain_rx")
        _field(row0, i18n.t("lbl_b_sens"),    "sens")

        _field(row1, i18n.t("lbl_b_veg_h"),    "veg_h")
        _field(row1, i18n.t("lbl_b_k_factor"), "k_factor")
        _field(row1, i18n.t("lbl_b_samples"),  "num", width=6)
        _field(row1, i18n.t("lbl_b_rain"),     "rain_rate")

        # Env Type Combobox
        f_env = ttk.Frame(row1)
        f_env.pack(side="left", padx=6)
        ttk.Label(f_env, text=i18n.t("lbl_env_type")).pack(side="left")
        # 表示ラベルは i18n の env_<key> を単一ソースに（言語連動）。内部は常にキー。
        self._env_key_to_label = {k: i18n.t(f"env_{k}") for k in ENV_KEYS}
        self._env_label_to_key = {v: k for k, v in self._env_key_to_label.items()}
        self._env_var = tk.StringVar(
            value=self._env_key_to_label.get(
                self._base_params.env_type, self._env_key_to_label["los"]
            )
        )
        ttk.Combobox(
            f_env, textvariable=self._env_var,
            values=list(self._env_key_to_label.values()),
            state="readonly", width=9,
        ).pack(side="left", padx=(2, 0))

        # Diff Model Combobox
        f_diff = ttk.Frame(row1)
        f_diff.pack(side="left", padx=6)
        ttk.Label(f_diff, text=i18n.t("lbl_b_diff_model")).pack(side="left")
        self._diff_var = tk.StringVar(value=self._base_params.diff_method)
        ttk.Combobox(
            f_diff, textvariable=self._diff_var, values=["deygout", "single"],
            state="readonly", width=9,
        ).pack(side="left", padx=(2, 0))

    def _refresh_common_from_launcher(self) -> None:
        """ランチャーの現在値で Common Settings を上書きする（凍結方式の取り込み）。

        案件名・自由メモ（案件情報）も同じスナップショットとして一緒に取り込む。
        """
        if self._config_provider is None:
            return
        c = self._config_provider()
        for attr, ckey in self._COMMON_CFG_MAP.items():
            if ckey in c and attr in self._common_vars:
                self._common_vars[attr].set(str(c[ckey]))
        env = c.get("env_type", "los")
        self._env_var.set(
            self._env_key_to_label.get(env, self._env_key_to_label["los"])
        )
        self._diff_var.set(c.get("diff_method", self._diff_var.get()))
        if self._meta_provider is not None:
            meta = self._meta_provider()
            self._project_name_var.set(meta.get("project_name", ""))
            self._memo_var.set(meta.get("memo", ""))





    # ⚠️ ここに `_on_destroy`（`unbind_all("<MouseWheel>")`）があったが、B-050 で
    #    削除した。ホイールを窓に閉じたバインドへ変えたので**外す相手がもう無い**
    #    のに加え、`unbind_all` は**他の窓の分まで消す**（window_fit の注記③）。
    #    しかも「外した」つもりで参照は残っていた＝リークの本体はこちらだった。

    def _build_bottom(self) -> None:
        bottom = ttk.Frame(self, padding=(0, 6))
        bottom.pack(fill="x", padx=8)

        left = ttk.Frame(bottom)
        left.pack(side="left")

        # 固定文字幅（width=）は日本語ラベル（例「CSVエクスポート」）が入りきらず
        # 見切れる（B-002 系）。幅は付けず内容に合わせて自動サイズさせる。
        ttk.Button(left, text=i18n.t("btn_add_row"),    command=self._add_row     ).pack(side="left", padx=2)
        # 地図から連続追加する口（I-043）＝**行を足す操作**なので `+ 行を追加` の隣。
        # ⚠️ 中継経路の窓には前からあり、この窓だけ無かった（⑧）。
        if self._map_opener is not None:
            ttk.Button(left, text=i18n.t("mh_from_map"),
                       command=self._map_opener).pack(side="left", padx=2)
        ttk.Button(left, text=i18n.t("btn_import_csv"), command=self._import_csv  ).pack(side="left", padx=2)
        ttk.Button(left, text=i18n.t("btn_export_csv"), command=self._export_csv  ).pack(side="left", padx=2)
        ttk.Button(left, text=i18n.t("btn_template"),   command=self._save_template).pack(side="left", padx=2)
        ttk.Button(left, text=i18n.t("btn_clear_all"),  command=self._clear_all   ).pack(side="left", padx=2)

        # ⚠️ 実行ボタンは**ここには置かない**＝進捗帯の右端（下記 row2）が
        # アプリ共通の位置（I-029）。この列に置いていたころは、同じ見た目の
        # 「行を追加 / インポート / …」と並んで**走らせる操作が編集操作に紛れて**
        # いた。位置は tests/test_ui_consistency.py が全窓横断で縛る。

        # 進捗エリア（2段構成）
        prog_frame = ttk.Frame(self)
        prog_frame.pack(fill="x", padx=8, pady=(0, 8))

        # 上段: 現在パス名 (左) + OK/NG/ERR カウント + N/M (P%)（右端）
        row1 = ttk.Frame(prog_frame)
        row1.pack(fill="x")
        self._prog_label = ttk.Label(row1, text="", anchor="w")
        self._prog_label.pack(side="left", fill="x", expand=True)
        # 件数は**ステータスの行**に置く（I-047 の実機確認で発覚）＝ここを帯の下段に
        # 置いていたころは、空でも 15 文字ぶんの箱を占めるので**バーだけが 1 窓だけ
        # 短く**、実行ボタンに届いていなかった。下段はどの窓も「バーと実行だけ」。
        # ⚠️ `width=15` は残す＝進捗の文言（`0 / 12  (0%)`）が伸び縮みするたびに
        # 左隣の OK/NG/ERR が横へ動くのを止めるため（幅を固定するのは*動かさない*
        # ためであって、大きさで強調するためではない＝I-046 とは目的が違う）。
        self._prog_count_label = ttk.Label(row1, text="", width=15, anchor="e")
        self._prog_count_label.pack(side="right", padx=(6, 0))
        # OK/NG/ERR は個別の色分けをやめ、他の ttk.Label と同一スタイルへ統一する
        # （I-005・従来の tk.Label＋前景色ハードコードはダークテーマに追従しない）。
        # ⛔ 太字は使わない（画面の強調は配置と余白で作る＝theme.py の決定）。
        self._ok_label  = ttk.Label(row1, text="")
        self._ok_label.pack(side="left", padx=(8, 2))
        self._ng_label  = ttk.Label(row1, text="")
        self._ng_label.pack(side="left", padx=2)
        self._err_label = ttk.Label(row1, text="")
        self._err_label.pack(side="left", padx=(2, 0))

        # 下段: バー (左・伸縮) + **実行**（右端＝全フロー共通の位置）。
        # ⛔ この行に**第 3 のウィジェットを足さない**＝バーの右端がボタンまで
        # 届かなくなり、その窓のバーだけ短く見える（実機確認で発覚）。
        row2 = ttk.Frame(prog_frame)
        row2.pack(fill="x", pady=(2, 0))
        self._prog_bar = ttk.Progressbar(row2, orient="horizontal", mode="determinate")
        self._prog_bar.pack(side="left", fill="x", expand=True)
        # ⛔ `width=` は付けない（I-046）＝ラベルは 4 窓とも「実行」で揃えてあるのに、
        # ここだけ 14 文字ぶんの箱で**同じ文字が違う大きさ**に見えていた。主操作で
        # あることは位置（帯の右端）と Accent で表す＝大きさは強調の軸ではない。
        self._run_btn = ttk.Button(
            row2, text=i18n.t("btn_run"), command=self._on_run,
            style="Accent.TButton",
        )
        self._run_btn.pack(side="right", padx=(10, 0))














    # ----------------------------------------------------------
    # 地図連携（座標シンク・Phase D2 連続追加）
    # 地図（ランチャー所有・唯一インスタンス）の連続追加モードから呼ばれる受け皿。
    # バッチ表が source of truth。RF はランチャー（config_provider）の現在値で凍結。
    # ----------------------------------------------------------
    def append_path(self, tx: tuple, rx: tuple) -> str:
        """地図で確定した TX→RX ペアをバッチ行として追加し、付与した path_id を返す。

        座標はバッチ表示形式へ整形し、RF はランチャーの現在値で凍結する
        （_frozen_row に集約）。地図の連続追加モードから呼ばれる。
        """
        idx   = len(self._row_frames)
        start = coords.format_pair(tx[0], tx[1], self._coord_format)
        end   = coords.format_pair(rx[0], rx[1], self._coord_format)
        defaults = self._frozen_row(idx, start, end)
        self._add_row(defaults)
        return defaults[0]

    def existing_paths(self) -> list[tuple]:
        """表の各行の (path_id, TX座標, RX座標) を [(pid, tx, rx), ...] で返す
        （パース不能行は除外）。

        地図が確定済みパスを地図上に表示するために引く。path_id も返すのは、
        地図上で「どの行のパスか」を距離バッジへ添えて分かるようにするため
        （I-001・バッチ表の各pathとマップウィンドウのpathの対応が不明瞭という指摘）。
        """
        out: list[tuple] = []
        for entries in self._row_entries:
            try:
                tx = coords.parse_pair(entries[1].get())
                rx = coords.parse_pair(entries[2].get())
            except ValueError:
                continue
            out.append((entries[0].get().strip(), tx, rx))
        return out

    def _notify_paths_changed(self) -> None:
        """パス集合が変わった旨をランチャー→地図へ通知する（確定パス表示の追従）。

        一括再構築中（_suspend_notify）は逐次通知せず、呼び出し側が終了後に1回呼ぶ。
        地図が連続追加モードでない／閉じている場合は受け側で no-op になる。
        """
        if self._suspend_notify or self._on_paths_changed is None:
            return
        self._on_paths_changed()

    def close_window(self) -> None:
        """他所（ランチャーのプロジェクト読込）から閉じるための公開口。

        3 つの窓で**名前を揃える**＝内部ハンドラ名で分岐させない（この窓は
        `_on_close` を*コールバックの保管場所*として使っているので、名前で
        分岐すると「閉じずにコールバックだけ呼ぶ」に化ける）。
        """
        self._on_close_window()

    def _on_close_window(self) -> None:
        """ウィンドウを閉じる。閉じる旨をランチャーへ通知する（地図の連携解除）。"""
        cb = self._on_close
        # 破棄前にポーリングを止める。実行中に閉じられると、破棄済みウィジェットへ
        # after し続けて `invalid command name` になる（マップ破棄と同じクラスの
        # 失敗＝map_window.close_map_safely 参照）。ワーカースレッドは走り続けるが、
        # 成果物生成はワーカー内で完結するので実行そのものは完走する。
        self._pump.stop()
        # ⚠️ **通知は破棄より前**（条件探索・中継経路と同じ順序）。ランチャーは
        # この通知で行を回収して `.rsproj` へ持ち越すので、先に destroy すると
        # 「バッチ窓を閉じただけで行が消えたファイルを保存する」ことになる
        # （project.py が「節が無い＝空ではない」で守っている性質と対）。
        if cb is not None:
            cb()
        self.destroy()








    def project_rows(self) -> list[batch.PathRow]:
        """プロジェクト保存のための行の写し（`replace_rows` と対）。

        `_read_table_rows` と同じもの＝**保存のために別の読み方を作らない**
        （行の意味が 2 通りになると、CSV に出る行と `.rsproj` に入る行が
        黙ってずれる）。パースできない欄は NaN のまま入り、実行時に
        `validate_rows` が 1 か所で弾く（保存はいつでも通す）。
        """
        return self._read_table_rows()
