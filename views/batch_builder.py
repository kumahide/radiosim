"""
views/batch_builder.py
======================
バッチパス入力ウィンドウ（BatchBuilderWindow）。

2種類の入力方法を提供する：
  1. GUI テーブルへの直接入力
  2. CSV インポート（テーブルに展開）

実行は batch.run_batch() に委譲する。
"""

import os
import queue
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable

import batch
import config
import coords
import i18n
import report
import simulation as sim
from models import ENV_KEYS
from views import dialogs


class BatchBuilderWindow(tk.Toplevel):
    """バッチ実行用のパス入力ウィンドウ。ランチャーから生成される。"""

    _WIDTHS = [2, 9, 21, 21, 6, 6, 8, 7, 7, 11, 2, 2]

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
            i18n.t("col_gain_tx"), i18n.t("col_gain_rx"), i18n.t("col_note"), "", "",
        ]

    def __init__(
        self,
        parent: tk.Tk,
        base_params: sim.SimParams,
        config_provider: "Callable[[], dict] | None" = None,
        load_params:     "Callable[[dict], None] | None" = None,
        on_close:        "Callable[[], None] | None" = None,
        on_paths_changed: "Callable[[], None] | None" = None,
    ) -> None:
        super().__init__(parent)
        self.title(i18n.t("batch_title"))
        # 共通設定を RF系／環境系サブグループ化した分（I-003・LabelFrame の見出し2つ分）
        # 高さが増えたため、既定サイズ・最小サイズを拡張して進捗バー行（row2）が
        # 窓外へ圧迫されない余裕を確保する（+50px では row2 がほぼ0pxに潰れ再発）。
        self.geometry("1080x700")
        self.resizable(True, True)
        self.minsize(880, 520)

        # ランチャー連携（凍結方式）。省略時は従来挙動（往復・凍結なし）。
        self._config_provider = config_provider
        self._load_params     = load_params
        # 閉じたときにランチャーへ通知するコールバック（地図の連続追加先を手放させる）。
        self._on_close        = on_close
        # パス集合（行）が変わったときにランチャー→地図へ通知するコールバック。
        # 地図の確定パス表示をリアルタイムに追従させる（削除・クリア・インポート等）。
        self._on_paths_changed = on_paths_changed
        # 一括再構築（並べ替え・インポート）中は逐次通知を抑止し、終了後に1回だけ通知する。
        self._suspend_notify   = False

        self._base_params  = base_params
        # 座標表記は app 設定に従う（人が読む report.txt/HTML のみ。データは DD 固定）
        self._coord_format = config.load_config().get("coord_format", "dd")
        self._row_entries: list[list[tk.Entry]] = []
        self._row_frames:  list[ttk.Frame]      = []
        self._running      = False
        self._event_queue: queue.Queue          = queue.Queue()
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
        self._add_row()
        self._poll_queue()
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

        RF/環境パラメータと違い計算には影響しない報告書メタなので、ランチャー凍結
        （🔒）ではなくバッチ側で直接編集する自由文字列。数フィールド＋自由メモに厳格
        限定（テンプレエディタ化しない）。案件名＝per-path/summary 両ヘッダ、メモ＝
        summary のみ。sv_ttk テーマ追従のため素 tk でなく ttk.Entry を使う。
        """
        frame = ttk.LabelFrame(self, text=i18n.t("batch_case_info"), padding=(8, 4))
        frame.pack(fill="x", padx=8, pady=(8, 0))

        self._project_name_var = tk.StringVar()
        self._memo_var         = tk.StringVar()

        f_proj = ttk.Frame(frame)
        f_proj.pack(side="left", padx=6)
        ttk.Label(f_proj, text=i18n.t("batch_project_name"), font=("Arial", 8)).pack(side="left")
        ttk.Entry(f_proj, textvariable=self._project_name_var, font=("Arial", 8),
                  width=20).pack(side="left", padx=(2, 0))

        f_memo = ttk.Frame(frame)
        f_memo.pack(side="left", padx=6, fill="x", expand=True)
        ttk.Label(f_memo, text=i18n.t("batch_memo"), font=("Arial", 8)).pack(side="left")
        ttk.Entry(f_memo, textvariable=self._memo_var, font=("Arial", 8)).pack(
            side="left", padx=(2, 0), fill="x", expand=True)

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
        # 「↻ランチャーから更新」用の専用行。row1 は日本語ラベルで横幅が逼迫し、
        # 右詰めのボタンが窓外へ押し出される（B-002 系）。独立行なら言語に依らず収まる。
        # 左には凍結欄の意味（🔒）を説明するヒントを置き、空きスペース化を防ぐ（I-003）。
        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=(4, 0))

        def _field(parent: tk.Widget, label: str, attr: str, width: int = 8) -> None:
            f = ttk.Frame(parent)
            f.pack(side="left", padx=6)
            ttk.Label(f, text=label, font=("Arial", 8)).pack(side="left")
            var = tk.StringVar(value=str(getattr(self._base_params, attr)))
            self._common_vars[attr] = var
            # 共通設定はランチャー（source of truth）のスナップショット。直接編集
            # させず「↻ランチャーから更新」で取り込む（凍結方式の対称化）。
            # 素の tk.Entry＋背景色ハードコードだと sv_ttk のテーマに追従せず、
            # ダークモードで薄グレー背景×白文字になり読めない（B-001）。ttk.Entry の
            # readonly ならライト/ダーク双方でテーマ配色になる。
            ttk.Entry(
                f, textvariable=var, font=("Arial", 8), width=width,
                state="readonly",
            ).pack(side="left", padx=(2, 0))
            # 🔒はテーマ配色に依存しないグリフなので、ライト/ダーク双方で
            # 「編集不可（ランチャーからの凍結値）」の視覚キューになる（I-004）。
            ttk.Label(f, text="🔒", font=("Arial", 8)).pack(side="left", padx=(2, 0))

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
        ttk.Label(f_env, text=i18n.t("lbl_b_env_type"), font=("Arial", 8)).pack(side="left")
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
            state="readonly", font=("Arial", 8), width=9,
        ).pack(side="left", padx=(2, 0))

        # Diff Model Combobox
        f_diff = ttk.Frame(row1)
        f_diff.pack(side="left", padx=6)
        ttk.Label(f_diff, text=i18n.t("lbl_b_diff_model"), font=("Arial", 8)).pack(side="left")
        self._diff_var = tk.StringVar(value=self._base_params.diff_method)
        ttk.Combobox(
            f_diff, textvariable=self._diff_var, values=["deygout", "single"],
            state="readonly", font=("Arial", 8), width=9,
        ).pack(side="left", padx=(2, 0))

        # ランチャー（source of truth）から共通設定を取り込む。専用行に右詰め、
        # 左には🔒の意味を説明するヒントを添える。
        if self._config_provider is not None:
            ttk.Label(
                row2, text=i18n.t("hint_common_readonly"),
                font=("Arial", 8), foreground="gray",
            ).pack(side="left", padx=6)
            ttk.Button(
                row2, text=i18n.t("btn_refresh_common"),
                command=self._refresh_common_from_launcher,
            ).pack(side="right", padx=6)

    def _refresh_common_from_launcher(self) -> None:
        """ランチャーの現在値で Common Settings を上書きする（凍結方式の取り込み）。"""
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

    def _build_table(self) -> None:
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=8, pady=6)

        # ヘッダ行（列幅は _sync_header_columns で見出しと行の実測値に合わせる）。
        # 固定文字幅（width=w）を掛けると日本語見出しが文字数不足で見切れる（B-002）。
        # 幅は付けず全文を表示させ、列の整合は _sync_header_columns に任せる。
        self._hdr = ttk.Frame(outer)
        self._hdr.pack(fill="x")
        for col, label in enumerate(self._COLS):
            ttk.Label(
                self._hdr, text=label,
                font=("Arial", 9, "bold"), anchor="w",
            ).grid(row=0, column=col, padx=2, pady=3, sticky="w")

        # スクロール可能なテーブル本体
        canvas_frame = ttk.Frame(outer)
        canvas_frame.pack(fill="both", expand=True)

        # tk.Canvas は ttk 管理外でテーマに追従しないため、生成時点の ttk 背景色を
        # 明示的に合わせる（I-005・sv_ttk のテーマ切替に伴う自動追従はしない）。
        theme_bg = ttk.Style().lookup("TFrame", "background")
        self._canvas = tk.Canvas(canvas_frame, borderwidth=0, highlightthickness=0, bg=theme_bg)
        vsb = ttk.Scrollbar(canvas_frame, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self._table_frame = ttk.Frame(self._canvas)
        self._table_win   = self._canvas.create_window(
            (0, 0), window=self._table_frame, anchor="nw"
        )
        self._table_frame.bind("<Configure>", self._on_frame_configure)
        self._canvas.bind("<Configure>",      self._on_canvas_configure)
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind("<Destroy>", self._on_destroy)

    def _on_frame_configure(self, _=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfig(self._table_win, width=event.width)

    def _on_mousewheel(self, event) -> None:
        # bind_all はアプリ全体（子 Toplevel のマップ含む）に効くため、ポインタが
        # テーブル（_canvas 配下）にある時だけスクロールする。これがないとマップ
        # ウィンドウのズーム（ホイール）でバッチ表まで一緒にスクロールしてしまう。
        widget = self.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        path, cpath = str(widget), str(self._canvas)
        if widget is self._canvas or path == cpath or path.startswith(cpath + "."):
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            try:
                self._canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass

    def _build_bottom(self) -> None:
        bottom = ttk.Frame(self, padding=(0, 6))
        bottom.pack(fill="x", padx=8)

        left = ttk.Frame(bottom)
        left.pack(side="left")

        # 固定文字幅（width=）は日本語ラベル（例「CSVエクスポート」）が入りきらず
        # 見切れる（B-002 系）。幅は付けず内容に合わせて自動サイズさせる。
        ttk.Button(left, text=i18n.t("btn_add_row"),    command=self._add_row     ).pack(side="left", padx=2)
        ttk.Button(left, text=i18n.t("btn_import_csv"), command=self._import_csv  ).pack(side="left", padx=2)
        ttk.Button(left, text=i18n.t("btn_export_csv"), command=self._export_csv  ).pack(side="left", padx=2)
        ttk.Button(left, text=i18n.t("btn_template"),   command=self._save_template).pack(side="left", padx=2)
        ttk.Button(left, text=i18n.t("btn_clear_all"),  command=self._clear_all   ).pack(side="left", padx=2)

        right = ttk.Frame(bottom)
        right.pack(side="right")
        self._run_btn = ttk.Button(
            right, text=i18n.t("btn_run_batch"), command=self._on_run, width=14,
            style="Accent.TButton",
        )
        self._run_btn.pack(side="right", padx=4)

        # 進捗エリア（2段構成）
        prog_frame = ttk.Frame(self)
        prog_frame.pack(fill="x", padx=8, pady=(0, 8))

        # 上段: 現在パス名 (左) + OK/NG/ERR カウント (右)
        row1 = ttk.Frame(prog_frame)
        row1.pack(fill="x")
        self._prog_label = ttk.Label(row1, text="", font=("Arial", 9), anchor="w")  # noqa: E501
        self._prog_label.pack(side="left", fill="x", expand=True)
        # OK/NG/ERR は個別の色分けをやめ、他の ttk.Label と同一スタイルへ統一する
        # （I-005・従来の tk.Label＋前景色ハードコードはダークテーマに追従しない）。
        self._ok_label  = ttk.Label(row1, text="", font=("Arial", 9, "bold"))
        self._ok_label.pack(side="left", padx=(8, 2))
        self._ng_label  = ttk.Label(row1, text="", font=("Arial", 9, "bold"))
        self._ng_label.pack(side="left", padx=2)
        self._err_label = ttk.Label(row1, text="", font=("Arial", 9, "bold"))
        self._err_label.pack(side="left", padx=(2, 0))

        # 下段: バー (左・伸縮) + N/M (P%) (右)
        row2 = ttk.Frame(prog_frame)
        row2.pack(fill="x", pady=(2, 0))
        self._prog_bar = ttk.Progressbar(row2, orient="horizontal", mode="determinate")
        self._prog_bar.pack(side="left", fill="x", expand=True)
        self._prog_count_label = ttk.Label(
            row2, text="", font=("Arial", 9), width=15, anchor="e"
        )
        self._prog_count_label.pack(side="right", padx=(6, 0))

    # ----------------------------------------------------------
    # テーブル行の追加・削除
    # ----------------------------------------------------------
    def _frozen_row(self, idx: int, start: str, end: str) -> list[str]:
        """座標 start/end と、その時点のランチャー RF を凍結したセル文字列を返す。

        座標は呼び出し側で整形済み文字列を渡す（ランチャー欄からの凍結も地図からの
        append も RF の凍結ロジックは同一なのでここに集約する）。
        """
        c = self._config_provider() if self._config_provider else {}
        return [
            f"path{idx + 1:02d}",
            start,
            end,
            str(c.get("h_tx", "")),
            str(c.get("h_rx", "")),
            str(c.get("freq", "")),
            str(c.get("gain_tx", "")),
            str(c.get("gain_rx", "")),
            "",
        ]

    def _frozen_defaults(self, idx: int) -> list[str]:
        """ランチャーの現在値（座標＋RF）を凍結したセル文字列リストを返す。"""
        c = self._config_provider() if self._config_provider else {}
        # ランチャー config は座標を DD 正規化済み。バッチ表示の座標形式へ整形する。
        start = coords.reformat(c.get("start", ""), self._coord_format)
        end   = coords.reformat(c.get("end", ""),   self._coord_format)
        return self._frozen_row(idx, start, end)

    def _add_row(self, row_data: "batch.PathRow | list[str] | None" = None) -> None:
        idx      = len(self._row_frames)
        row_frame = ttk.Frame(self._table_frame)
        row_frame.pack(fill="x")
        self._row_frames.append(row_frame)

        if isinstance(row_data, list):
            defaults = row_data
        elif row_data is not None:
            defaults = [
                row_data.path_id,
                coords.format_pair(row_data.lat_tx, row_data.lon_tx, self._coord_format),
                coords.format_pair(row_data.lat_rx, row_data.lon_rx, self._coord_format),
                str(row_data.h_tx),
                str(row_data.h_rx),
                str(row_data.freq_mhz) if row_data.freq_mhz is not None else "",
                str(row_data.gain_tx) if row_data.gain_tx is not None else "",
                str(row_data.gain_rx) if row_data.gain_rx is not None else "",
                row_data.note,
            ]
        elif self._config_provider is not None:
            # 凍結方式：その時点のランチャー欄（座標＋RF）を文字列コピーして固定。
            defaults = self._frozen_defaults(idx)
        else:
            # ランチャー非連携時のフォールバック：直前行 → Common Settings の値。
            prev = self._row_entries[-1] if self._row_entries else None
            defaults = [
                f"path{idx + 1:02d}",
                "",
                "",
                prev[3].get() if prev else str(self._base_params.h_tx),
                prev[4].get() if prev else str(self._base_params.h_rx),
                prev[5].get() if prev else self._common_vars["freq_mhz"].get(),
                prev[6].get() if prev else self._common_vars["gain_tx"].get(),
                prev[7].get() if prev else self._common_vars["gain_rx"].get(),
                "",
            ]

        # col 0: drag handle（他の ttk ウィジェットとスタイルを統一・I-005）
        handle = ttk.Label(
            row_frame, text="≡", cursor="fleur",
            font=("Arial", 9, "bold"), width=2,
        )
        handle.grid(row=0, column=0, padx=2, pady=1)
        handle.bind("<ButtonPress-1>",   lambda e, f=row_frame: self._drag_start(e, f))
        handle.bind("<B1-Motion>",       lambda e: self._drag_motion(e))
        handle.bind("<ButtonRelease-1>", lambda e: self._drag_end(e))

        # cols 1…N-2: entry widgets (skip handle col 0 and last 2 button cols)
        entries: list[tk.Entry] = []
        for i, (w, val) in enumerate(zip(self._WIDTHS[1:-2], defaults)):
            e = ttk.Entry(row_frame, font=("Arial", 9), width=w)
            e.insert(0, val)
            e.grid(row=0, column=i + 1, padx=2, pady=1, sticky="w")
            entries.append(e)

        # 座標セル（col 1=start / 2=end）の編集確定で地図の確定パス表示を追従させる。
        # 地図ラインは TX/RX 座標だけで決まるので、対象は start/end のみ。FocusOut は
        # 他セル・他ウィンドウへ移った時、Return は明示確定時に発火する。
        for col in (1, 2):
            entries[col].bind("<FocusOut>", lambda _e: self._notify_paths_changed(), add="+")
            entries[col].bind("<Return>",   lambda _e: self._notify_paths_changed(), add="+")

        def _dup(es=entries):
            self._dup_row(es)

        def _del(f=row_frame, es=entries):
            self._remove_row(f, es)

        ttk.Button(
            row_frame, text="⧉", command=_dup, cursor="hand2", width=2,
        ).grid(row=0, column=len(self._WIDTHS) - 2, padx=2, pady=1)
        ttk.Button(
            row_frame, text="×", command=_del, cursor="hand2", width=2,
        ).grid(row=0, column=len(self._WIDTHS) - 1, padx=2, pady=1)

        # 右クリックで per-row 往復メニュー（→シングルへ／⟳RF更新／複製／削除）。
        def _menu(e, f=row_frame, es=entries):
            self._show_row_menu(e, f, es)
        handle.bind("<Button-3>", _menu)
        row_frame.bind("<Button-3>", _menu)
        for e in entries:
            e.bind("<Button-3>", _menu)

        self._row_entries.append(entries)
        self._canvas.update_idletasks()
        self._canvas.yview_moveto(1.0)
        # 追加した行にも列 minsize を反映するため毎回同期する（新行がヘッダとズレ
        # ないように）。バルク投入（インポート・並べ替え）はデバウンスで 1 回に畳む。
        self._schedule_sync()
        self._notify_paths_changed()

    def _schedule_sync(self) -> None:
        """列幅同期をデバウンスして予約する（連続追加を 1 回に畳む）。"""
        if self._sync_after_id is not None:
            self.after_cancel(self._sync_after_id)
        self._sync_after_id = self.after(100, self._run_sync)

    def _run_sync(self) -> None:
        self._sync_after_id = None
        self._sync_header_columns()

    def _sync_header_columns(self) -> None:
        """ヘッダと各行の列幅を揃えてズレと見出しの見切れを解消する。

        各列の最小幅を「行セルの実測幅」と「見出しの必要幅」の大きい方に合わせ、
        ヘッダ・全行の両グリッドへ同じ minsize を掛ける。これで日本語見出しが
        入りきり（B-002）、かつ列がずれない（見出しは別グリッドのため両者に適用）。
        """
        if not self._row_frames:
            return
        self._table_frame.update_idletasks()
        self._hdr.update_idletasks()
        for col in range(len(self._WIDTHS)):
            bbox   = self._row_frames[0].grid_bbox(column=col, row=0)
            cell_w = bbox[2] if bbox else 0
            hdr    = self._hdr.grid_slaves(row=0, column=col)
            # padx=2 の左右分を見込んで少し余裕を持たせる。
            hdr_w  = (hdr[0].winfo_reqwidth() + 4) if hdr else 0
            target = max(cell_w, hdr_w)
            self._hdr.grid_columnconfigure(col, minsize=target)
            for frame in self._row_frames:
                frame.grid_columnconfigure(col, minsize=target)

    def _remove_row(self, frame: ttk.Frame, entries: list[tk.Entry]) -> None:
        if entries in self._row_entries:
            self._row_entries.remove(entries)
        if frame in self._row_frames:
            self._row_frames.remove(frame)
        frame.destroy()
        self._notify_paths_changed()

    def _dup_row(self, entries: list[tk.Entry]) -> None:
        """選択行の値をコピーして末尾に新しい行を追加する。ID は _copy サフィックスを付与。"""
        vals = [e.get() for e in entries]
        orig_pid = vals[0].strip()
        existing = {rows[0].get().strip() for rows in self._row_entries}
        new_pid = f"{orig_pid}_copy"
        n = 2
        while new_pid in existing:
            new_pid = f"{orig_pid}_copy{n}"
            n += 1
        vals = list(vals)
        vals[0] = new_pid
        self._add_row(vals)

    # ----------------------------------------------------------
    # per-row 往復（右クリックメニュー・案A）
    # ----------------------------------------------------------
    def _show_row_menu(self, event, frame: ttk.Frame, entries: list[tk.Entry]) -> None:
        """行の右クリックメニューを表示する。"""
        menu = tk.Menu(self, tearoff=0)
        if self._load_params is not None:
            menu.add_command(
                label=i18n.t("menu_send_to_single"),
                command=lambda: self._send_row_to_single(entries),
            )
        if self._config_provider is not None:
            menu.add_command(
                label=i18n.t("menu_update_rf"),
                command=lambda: self._update_row_rf(entries),
            )
        if menu.index("end") is not None:
            menu.add_separator()
        menu.add_command(label=i18n.t("menu_dup"), command=lambda: self._dup_row(entries))
        menu.add_command(label=i18n.t("menu_del"),
                         command=lambda: self._remove_row(frame, entries))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _send_row_to_single(self, entries: list[tk.Entry]) -> None:
        """行の座標＋RF をランチャー（シングル）へロードして調整できるようにする。"""
        if self._load_params is None:
            return
        v = [e.get() for e in entries]
        self._load_params({
            "start":   v[1], "end":     v[2],
            "h_tx":    v[3], "h_rx":    v[4],
            "freq":    v[5], "gain_tx": v[6], "gain_rx": v[7],
        })

    def _update_row_rf(self, entries: list[tk.Entry]) -> None:
        """ランチャーの現在 RF を行へ書き戻す。座標（start/end）は保持する。"""
        if self._config_provider is None:
            return
        c = self._config_provider()
        # 列 3=h_tx, 4=h_rx, 5=freq, 6=gain_tx, 7=gain_rx（座標 1/2 は触らない）
        for col, key in ((3, "h_tx"), (4, "h_rx"), (5, "freq"),
                         (6, "gain_tx"), (7, "gain_rx")):
            if key in c:
                entries[col].delete(0, tk.END)
                entries[col].insert(0, str(c[key]))

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

    def _on_close_window(self) -> None:
        """ウィンドウを閉じる。閉じる旨をランチャーへ通知する（地図の連携解除）。"""
        cb = self._on_close
        self.destroy()
        if cb is not None:
            cb()

    # ----------------------------------------------------------
    # ドラッグ&ドロップ並び替え
    # ----------------------------------------------------------
    def _drag_start(self, _, frame: ttk.Frame) -> None:
        if frame in self._row_frames:
            self._drag_row_idx = self._row_frames.index(frame)

    def _drag_motion(self, event) -> None:
        if self._drag_row_idx is None:
            return
        self._show_drag_indicator(self._find_drop_index(event.y_root))

    def _drag_end(self, event) -> None:
        if self._drag_row_idx is None:
            return
        target = self._find_drop_index(event.y_root)
        src    = self._drag_row_idx
        self._drag_row_idx = None
        if self._drag_indicator:
            self._drag_indicator.destroy()
            self._drag_indicator = None
        if target != src and target != src + 1:
            self._move_row(src, target)

    def _find_drop_index(self, root_y: int) -> int:
        """マウスの root_y 座標からドロップ先インデックスを返す。"""
        canvas_root_y = self._canvas.winfo_rooty()
        scroll_frac   = self._canvas.yview()[0]
        table_h       = self._table_frame.winfo_reqheight()
        local_y       = root_y - canvas_root_y + scroll_frac * table_h
        for i, frame in enumerate(self._row_frames):
            mid = frame.winfo_y() + max(frame.winfo_reqheight(), 1) / 2
            if local_y < mid:
                return i
        return len(self._row_frames)

    def _show_drag_indicator(self, insert_idx: int) -> None:
        """ドロップ位置を示す水平線を table_frame 内に表示する。"""
        if self._drag_indicator:
            self._drag_indicator.destroy()
            self._drag_indicator = None
        if not self._row_frames:
            return
        insert_idx = max(0, min(insert_idx, len(self._row_frames)))
        if insert_idx < len(self._row_frames):
            y = self._row_frames[insert_idx].winfo_y() - 1
        else:
            last = self._row_frames[-1]
            y = last.winfo_y() + last.winfo_reqheight() - 1
        self._drag_indicator = tk.Frame(self._table_frame, bg="#2196F3", height=2)
        self._drag_indicator.place(x=0, y=y, relwidth=1)

    def _move_row(self, from_idx: int, to_idx: int) -> None:
        """from_idx の行を to_idx 位置に移動してテーブルを再構築する。"""
        all_vals = [[e.get() for e in entries] for entries in self._row_entries]
        item = all_vals.pop(from_idx)
        adjusted = to_idx - 1 if to_idx > from_idx else to_idx
        all_vals.insert(adjusted, item)
        # 並べ替えはパス集合が不変なので地図再描画は不要。逐次通知を抑止して
        # 再構築し、終了後の通知も行わない（_add_row 側の通知も抑止される）。
        self._suspend_notify = True
        try:
            for f in list(self._row_frames):
                f.destroy()
            self._row_frames.clear()
            self._row_entries.clear()
            for vals in all_vals:
                self._add_row(vals)
        finally:
            self._suspend_notify = False

    def _clear_all(self) -> None:
        """テーブルの全行を削除する（確認ダイアログあり）。"""
        if not self._row_frames:
            return
        if dialogs.confirm(
            self,
            i18n.t("dlg_clear_title"),
            i18n.t("dlg_clear_msg").format(n=len(self._row_frames)),
        ):
            for f in list(self._row_frames):
                f.destroy()
            self._row_frames.clear()
            self._row_entries.clear()
            self._notify_paths_changed()

    def _read_table_rows(self) -> list[batch.PathRow]:
        """テーブルの入力内容を PathRow リストに変換する。NaN でパース失敗を表現する。"""
        rows: list[batch.PathRow] = []
        for entries in self._row_entries:
            vals = [e.get().strip() for e in entries]
            pid, start, end, h_tx_s, h_rx_s, freq_s, gain_tx_s, gain_rx_s, note = vals

            if not pid and not start and not end:
                continue  # 完全空行はスキップ

            def _parse_coord(s: str) -> tuple[float, float]:
                # DD / DMS のどちらの表記でも受理する（座標形式設定に合わせて入力可能）。
                try:
                    return coords.parse_pair(s)
                except ValueError:
                    return float("nan"), float("nan")

            def _parse_float(s: str) -> float:
                try:
                    return float(s)
                except ValueError:
                    return float("nan")

            def _parse_opt_float(s: str) -> "float | None":
                if not s:
                    return None  # 空欄 → Common Settings の値を使用
                try:
                    return float(s)
                except ValueError:
                    return float("nan")  # 不正値 → validate_rows で検出

            lat_tx, lon_tx = _parse_coord(start)
            lat_rx, lon_rx = _parse_coord(end)
            rows.append(batch.PathRow(
                path_id  = pid,
                lat_tx   = lat_tx,
                lon_tx   = lon_tx,
                lat_rx   = lat_rx,
                lon_rx   = lon_rx,
                h_tx     = _parse_float(h_tx_s),
                h_rx     = _parse_float(h_rx_s),
                freq_mhz = _parse_opt_float(freq_s),
                gain_tx  = _parse_opt_float(gain_tx_s),
                gain_rx  = _parse_opt_float(gain_rx_s),
                note     = note,
            ))
        return rows

    # ----------------------------------------------------------
    # Import / Export / Template
    # ----------------------------------------------------------
    def _import_csv(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title=i18n.t("select_batch_csv"),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            rows = batch.parse_csv(path)
        except Exception as e:
            dialogs.alert(self, i18n.t("dlg_import_error"), str(e))
            return

        if self._row_entries:
            if not dialogs.confirm(
                self,
                i18n.t("dlg_import_title"),
                i18n.t("dlg_import_confirm").format(n=len(self._row_entries)),
            ):
                return

        # 一括入れ替え。逐次通知を抑止して再構築し、終了後に1回だけ地図へ通知する。
        self._suspend_notify = True
        try:
            for f in list(self._row_frames):
                f.destroy()
            self._row_frames.clear()
            self._row_entries.clear()
            for row in rows:
                self._add_row(row)
        finally:
            self._suspend_notify = False
        self._notify_paths_changed()
        dialogs.alert(
            self,
            i18n.t("dlg_import_title"),
            i18n.t("dlg_import_success").format(n=len(rows)),
        )

    def _export_csv(self) -> None:
        rows = self._read_table_rows()
        if not rows:
            dialogs.alert(self, i18n.t("dlg_export_title"), i18n.t("dlg_export_empty"))
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="batch_paths.csv",
        )
        if not path:
            return
        try:
            batch.export_csv(rows, path)
            dialogs.alert(
                self,
                i18n.t("dlg_export_title"),
                i18n.t("dlg_export_saved").format(path=path),
            )
        except Exception as e:
            dialogs.alert(self, i18n.t("dlg_export_error"), str(e))

    def _save_template(self) -> None:
        """ランチャーの現在値を 1 行目に書いたテンプレート CSV を保存する。"""
        path = filedialog.asksaveasfilename(
            parent=self,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile="batch_template.csv",
        )
        if not path:
            return
        bp = self._base_params
        template = batch.PathRow(
            path_id = "path01",
            lat_tx  = bp.lat_tx,
            lon_tx  = bp.lon_tx,
            lat_rx  = bp.lat_rx,
            lon_rx  = bp.lon_rx,
            h_tx    = bp.h_tx,
            h_rx    = bp.h_rx,
            note    = "Example path",
        )
        try:
            batch.export_csv([template], path)
            dialogs.alert(
                self,
                i18n.t("dlg_template_title"),
                i18n.t("dlg_template_saved").format(path=path),
            )
        except Exception as e:
            dialogs.alert(self, i18n.t("dlg_error"), str(e))

    # ----------------------------------------------------------
    # 実行
    # ----------------------------------------------------------
    def _read_base_params(self) -> sim.SimParams:
        """Common Settings の現在値から SimParams を生成する。"""
        c: dict[str, str] = {
            "start"      : f"{self._base_params.lat_tx}, {self._base_params.lon_tx}",
            "end"        : f"{self._base_params.lat_rx}, {self._base_params.lon_rx}",
            "h_tx"       : str(self._base_params.h_tx),
            "h_rx"       : str(self._base_params.h_rx),
            "freq"       : self._common_vars["freq_mhz"].get(),
            "p_tx"       : self._common_vars["p_tx"].get(),
            "gain_tx"    : self._common_vars["gain_tx"].get(),
            "gain_rx"    : self._common_vars["gain_rx"].get(),
            "sens"       : self._common_vars["sens"].get(),
            "veg_h"      : self._common_vars["veg_h"].get(),
            "k_factor"   : self._common_vars["k_factor"].get(),
            "samples"    : self._common_vars["num"].get(),
            "rain_rate"  : self._common_vars["rain_rate"].get(),
            "env_type"   : self._env_label_to_key.get(self._env_var.get(), "los"),
            "diff_method": self._diff_var.get(),
        }
        return sim.SimParams(c)

    def _on_run(self) -> None:
        if self._running:
            return

        rows = self._read_table_rows()
        errors = batch.validate_rows(rows)
        if errors:
            dialogs.alert(self, i18n.t("dlg_validation_error"), "\n".join(errors[:10]))
            return

        try:
            base_params = self._read_base_params()
        except Exception as e:
            dialogs.alert(self, i18n.t("dlg_common_cfg_error"), str(e))
            return

        self._running   = True
        self._ok_count  = 0
        self._ng_count  = 0
        self._err_count = 0
        self._run_cur     = 0
        self._run_samples = base_params.num
        self._run_btn.config(state="disabled")
        self._prog_bar.config(maximum=len(rows), value=0)
        self._prog_label.config(text=i18n.t("batch_starting"))
        self._prog_count_label.config(text=f"0 / {len(rows)}  (0%)")
        self._ok_label.config(text="✓ 0 OK")
        self._ng_label.config(text="✗ 0 NG")
        self._err_label.config(text="⚠ 0 ERR")

        q = self._event_queue
        batch.run_batch(
            rows              = rows,
            base_params       = base_params,
            on_path_start     = lambda cur, tot, pid, q=q: q.put(("start",    (cur, tot, pid))),
            on_path_progress  = lambda done, q=q: q.put(("progress", (done,))),
            on_path_complete  = lambda cur, tot, pr,  q=q: q.put(("done",     (cur, tot, pr))),
            on_batch_complete = lambda d,   rs,        q=q: q.put(("complete", (d, rs))),
            on_error          = lambda ex,             q=q: q.put(("error",    (ex,))),
            coord_format      = self._coord_format,
        )

    # ----------------------------------------------------------
    # コールバック（メインスレッドから呼ばれる）
    # ----------------------------------------------------------
    def _poll_queue(self) -> None:
        """メインスレッドでキューを消費する（50ms ポーリング）。"""
        try:
            while True:
                event, args = self._event_queue.get_nowait()
                if event == "start":
                    self._on_path_start(*args)
                elif event == "progress":
                    self._on_path_progress_tick(*args)
                elif event == "done":
                    self._on_path_done(*args)
                elif event == "complete":
                    self._on_batch_complete(*args)
                elif event == "error":
                    self._on_error(*args)
        except queue.Empty:
            pass
        self.after(50, self._poll_queue)

    def _on_path_start(self, cur: int, tot: int, pid: str) -> None:
        self._run_cur = cur
        pct = int((cur - 1) / tot * 100) if tot else 0
        self._prog_bar.config(value=cur - 1)
        self._prog_label.config(text=f"▶  {pid}")
        self._prog_count_label.config(text=f"{cur - 1} / {tot}  ({pct}%)")

    def _on_path_progress_tick(self, done: int) -> None:
        """パス内の標高取得進捗（0..samples）をバーへ小数値として反映する。

        バーの目盛り（maximum）はパス本数のまま、現在パスの内部進捗を小数部として
        加算する。従来 on_path_progress が no-op でパス境界でしかバーが動かず、
        DEM キャッシュ済みだと一瞬で完了してバーが「機能していない」ように見えた
        （β報告）。単発シングル実行（launcher._on_progress）と同じ fetch 進捗を使う。
        """
        if not self._running or self._run_samples <= 0:
            return
        frac = min(done / self._run_samples, 1.0)
        self._prog_bar.config(value=(self._run_cur - 1) + frac)

    def _on_path_done(self, cur: int, tot: int, pr: batch.PathResult) -> None:
        pct = int(cur / tot * 100) if tot else 0
        self._prog_bar.config(value=cur)
        if pr.result is not None:
            if pr.result.status == "OK":
                self._ok_count += 1
            else:
                self._ng_count += 1
            status_text = pr.result.status
        else:
            self._err_count += 1
            status_text = "ERROR"
        self._prog_label.config(text=f"   {pr.row.path_id}  →  {status_text}")
        self._prog_count_label.config(text=f"{cur} / {tot}  ({pct}%)")
        self._ok_label.config(text=f"✓ {self._ok_count} OK")
        self._ng_label.config(text=f"✗ {self._ng_count} NG")
        self._err_label.config(text=f"⚠ {self._err_count} ERR")
        report.save_path_visuals(pr, self._coord_format,
                                 self._project_name_var.get().strip())

    def _on_batch_complete(self, batch_dir: str, results: list) -> None:
        # 全パス俯瞰地図（ベストエフォート・失敗時 None → summary は注記のみ）。
        map_b64 = report.render_summary_map_b64(results)
        report.save_summary_html(results, batch_dir,
                                 self._project_name_var.get().strip(),
                                 self._memo_var.get().strip(),
                                 map_b64)
        report.save_summary_kml(results, batch_dir)
        self._running = False
        self._run_btn.config(state="normal")
        # 完了時はバーを 0 に戻す（シングル側 _on_fetch_complete と挙動を揃える）。
        # 進捗カウントもバーに合わせて消し、結果サマリは OK/NG/ERR ラベルに残す。
        self._prog_bar.config(value=0)
        self._prog_label.config(text=f"Done: {os.path.basename(batch_dir)}")
        self._prog_count_label.config(text="")
        if dialogs.confirm(
            self,
            i18n.t("dlg_batch_complete"),
            i18n.t("dlg_batch_complete_msg").format(dir=batch_dir),
        ):
            os.startfile(os.path.join(batch_dir, "summary.html"))

    def _on_error(self, ex: Exception) -> None:
        self._running = False
        self._run_btn.config(state="normal")
        # 失敗時もバーを 0 に戻す（シングル側 _on_fetch_error と挙動を揃える）。
        self._prog_bar.config(value=0)
        self._prog_count_label.config(text="")
        self._prog_label.config(text=i18n.t("batch_error_msg"))
        dialogs.alert(self, i18n.t("dlg_batch_error"), str(ex))
