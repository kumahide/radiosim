"""
views/batch_table.py
====================
バッチ窓の**入力表**（`BatchBuilderWindow` の Mixin）。

行の生成・複製・削除・並べ替え（ドラッグ＆ドロップ）・凍結列の表示・水平距離の
再計算・表からの読み取り。

⚠️ **これは `BatchBuilderWindow` の一部**であって独立した部品ではない
（`self._rows` / `self._canvas` などを共有する）。切り出しは 2.7 スライス A
（メソッド本文は 1 文字も変えていない＝「移動だけ」）。
"""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Callable

from core import coords
from core import i18n
from core import units
from core.models import horizontal_distance_km
from report import batch
from views import dialogs, theme, window_fit

if TYPE_CHECKING:
    from core import simulation as sim

# 宿主（`BatchBuilderWindow`）は `tk.Toplevel` の派生。Mixin は単体では
# ウィジェットではないので、**型検査のときだけ**宿主の基底を見せる（実行時は
# 素の Mixin のまま＝MRO も振る舞いも変わらない）。これが無いと `self` を
# `parent=` に渡す既存の呼び出しがすべて型エラーになる（B-049）。
if TYPE_CHECKING:
    _HostBase = tk.Toplevel
else:
    _HostBase = object


class _TableMixin(_HostBase):
    # 宿主から借りている面の宣言。**型検査のときだけ**存在する（実行時は 1 文字も
    # 定義しない）。理由は [views/map_picks.py](map_picks.py) の同じブロックに
    # 書いた（B-049）。
    if TYPE_CHECKING:
        _BASE_W: int
        _BASE_H: int
        _MIN_W: int
        _SCREEN_MARGIN: int
        _TABLE_PAD_W: int
        _WIDTHS: list[int]
        _base_params: "sim.SimParams"
        _common_vars: dict[str, tk.StringVar]
        _config_provider: "Callable[[], dict] | None"
        _load_params: "Callable[[dict], None] | None"
        _coord_format: str
        _row_entries: list[list[tk.Entry]]
        _row_frames: list[ttk.Frame]
        #: 実行に出した行の入力（ID → 値）。判定を書き戻してよいかの照合に使う。
        _run_inputs: dict[str, tuple[str, ...]]
        _drag_row_idx: "int | None"
        _drag_indicator: "tk.Frame | None"
        _sync_after_id: "str | None"
        _suspend_notify: bool

        # 列見出しは宿主側が **property**（i18n を都度引く）＝ここも property で
        # 宣言する（ただの属性にすると宿主の定義が「上書き」に見えて赤くなる）。
        @property
        def _COLS(self) -> list[str]: ...

        def _notify_paths_changed(self) -> None: ...

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
                anchor="w",
            ).grid(row=0, column=col, padx=2, pady=3, sticky="w")

        # スクロール可能なテーブル本体
        canvas_frame = ttk.Frame(outer)
        canvas_frame.pack(fill="both", expand=True)

        # tk.Canvas は ttk 管理外でテーマに追従しないため、生成時点のテーマ背景色を
        # 明示的に合わせる（I-005・sv_ttk のテーマ切替に伴う自動追従はしない）。
        # 色の出所は [views/theme.py](views/theme.py)＝ttk.Style().lookup は
        # sun-valley では常に空を返し、黙って無指定になっていた（B-008）。
        theme_bg = theme.palette(canvas_frame)["bg"]
        self._canvas = tk.Canvas(canvas_frame, borderwidth=0, highlightthickness=0, bg=theme_bg)
        vsb = ttk.Scrollbar(canvas_frame, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        # 初期幅の判定で「表の可視幅は窓幅より狭い」ぶんを実測するために保持する
        # （スクロールバー幅は DPI スケーリングで変わるので定数にしない）。
        self._vsb = vsb
        self._canvas.pack(side="left", fill="both", expand=True)

        self._table_frame = ttk.Frame(self._canvas)
        self._table_win   = self._canvas.create_window(
            (0, 0), window=self._table_frame, anchor="nw"
        )
        self._table_frame.bind("<Configure>", self._on_frame_configure)
        self._canvas.bind("<Configure>",      self._on_canvas_configure)
        # ⚠️ **`bind_all` を使わない**（B-050）。理由は 2 つあり、どちらも実測済み。
        #   ①**窓を閉じても解放されない**＝`bind_all` の登録先は**ルートの**
        #     `_tclCommands` で、しかも CPython の `Misc.unbind_all` は
        #     `bind all <seq> ''` を呼ぶだけで **`deletecommand` しない**（3.14 の
        #     ソースで確認）。⇒ コールバック（＝この窓への強参照）がアプリの寿命ぶん
        #     残り、開閉のたびに **40 個ずつ線形に積み上がる**（10 回で +400 個）。
        #   ②**他の窓と干渉する**＝[views/window_fit.py](window_fit.py) が同じ理由で
        #     既に `bind_all` を避けており、その注記が**この行を名指ししていた**。
        # バインドタグは「ウィジェット → クラス → トップレベル → all」なので、
        # **トップレベルに束ねればこの窓の中だけに効く**（＝従来と同じ効き方）。
        self.bind("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind("<<ThemeChanged>>",
                  lambda _e: self._refresh_verdict_colors(), add="+")

    def _on_frame_configure(self, _=None) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfig(self._table_win, width=event.width)

    def _on_mousewheel(self, event) -> None:
        # ポインタがテーブル（_canvas 配下）にある時だけスクロールする。窓に閉じた
        # バインドになった今も要る＝この窓の**他の領域**（共通設定欄など）でホイール
        # を回したときに表が動くと、見ている場所と動く場所がずれる。
        widget = self.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        path, cpath = str(widget), str(self._canvas)
        if widget is self._canvas or path == cpath or path.startswith(cpath + "."):
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

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
            row_frame, text="≡", cursor="fleur", width=2,
        )
        handle.grid(row=0, column=0, padx=2, pady=1)
        handle.bind("<ButtonPress-1>",   lambda e, f=row_frame: self._drag_start(e, f))
        handle.bind("<B1-Motion>",       lambda e: self._drag_motion(e))
        handle.bind("<ButtonRelease-1>", lambda e: self._drag_end(e))

        # cols 1…N-2: entry widgets (skip handle col 0 and last 2 button cols)
        entries: list[tk.Entry] = []
        for i, (w, val) in enumerate(zip(self._WIDTHS[1:-2], defaults)):
            e = ttk.Entry(row_frame, width=w)
            e.insert(0, val)
            e.grid(row=0, column=i + 1, padx=2, pady=1, sticky="w")
            entries.append(e)

        # 水平距離（読み取り専用・I-000）。座標の打ち間違いは距離が極端な値（0 や
        # 数千 km）になって現れるので、一覧に出しておくと実行前に気づける。
        # 表示は m 固定（units.format_distance＝I-014 で決めた表示単位の単一源泉）。
        dist_lbl = ttk.Label(row_frame, anchor="e", width=self._WIDTHS[-4])
        dist_lbl.grid(row=0, column=len(self._WIDTHS) - 4, padx=2, pady=1, sticky="w")

        # 判定（読み取り専用・I-041）＝**実行結果はこの行に返る**。実行前・編集後は
        # 空欄で、実行中に上から順に OK / NG / ERR が入る（進捗と結果が同じ場所に
        # 出る＝カウンタは「合計だけ」で場所を持っていなかった）。
        # ⚠️ **並行リストで持たない**＝判定ラベルの出所は行フレームのグリッド
        # そのもの（`_verdict_label`）。行を作る／消す／並べ替える口は既に 3 つ
        # あり、4 本目のリストを足すと**そのどれかで同期し忘れる**（実際、最初の
        # 実装は行を手で消すテストで破棄済みウィジェットを掴んで落ちた）。
        verdict_lbl = ttk.Label(row_frame, anchor="w", width=self._WIDTHS[-3])
        verdict_lbl.grid(row=0, column=len(self._WIDTHS) - 3, padx=2, pady=1, sticky="w")

        # 座標セル（col 1=start / 2=end）の編集確定で地図の確定パス表示と水平距離を
        # 追従させる。地図ラインは TX/RX 座標だけで決まるので、対象は start/end のみ。
        # FocusOut は他セル・他ウィンドウへ移った時、Return は明示確定時に発火する。
        # あわせて**確定した座標をこの窓の表記へ整形する**（I-060 R3）＝整形される
        # こと自体が「読めた」という返事になり、読めなければ原文が残るので parse の
        # 成否が目で分かる。表記は窓を開いた時点で凍結した `_coord_format`（G2）。
        def _coords_committed(_e=None, f=row_frame, es=entries):
            self._commit_row_coords(f, es)

        for col in (1, 2):
            entries[col].bind("<FocusOut>", _coords_committed, add="+")
            entries[col].bind("<Return>",   _coords_committed, add="+")
        self._update_row_distance(entries, dist_lbl)

        # 行を**変えたら**判定を消す＝**その結果を生んだ入力でなくなった行に、結果を
        # 残さない**（I-041 の規則は「結果はその結果を生んだ行に返す」なので、
        # 行が別物になった瞬間に居場所が無くなる）。
        # ⚠️ **引き金は「値が変わったこと」であって「キーが押されたこと」ではない**
        # （B-058）。`<Key>` で消していたころは **→ ← Tab Shift Home Ctrl でも消えた**＝
        # 実行直後に矢印キーで表を眺めただけで結果が読めなくなり、しかも消えるのは
        # 触った行だけなので、**表が「一部だけ判定がある」状態**になって読み手には
        # 実行し損ねた行と区別が付かなかった。
        def _clear_verdict_if_changed(_e=None, lbl=verdict_lbl, es=entries):
            if lbl.cget("text") and self._row_values(es) != getattr(lbl, "_verdict_input", None):
                lbl.config(text="")

        for e in entries:
            # KeyRelease＝押した瞬間ではなく**値が確定してから**比べる。
            # 貼り付け・切り取りはキーを経由しない口があるので仮想イベントも拾う。
            e.bind("<KeyRelease>", _clear_verdict_if_changed, add="+")
            e.bind("<<Paste>>", lambda ev, w=e, f=_clear_verdict_if_changed:
                   w.after_idle(f), add="+")
            e.bind("<<Cut>>", lambda ev, w=e, f=_clear_verdict_if_changed:
                   w.after_idle(f), add="+")

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

    def _fit_width_to_content(self) -> None:
        """表の中身が収まる幅までウィンドウを広げる（既定幅は下限として扱う）。

        列の実幅は**言語（日本語見出し）・DPI スケーリング・フォント**で変わるので、
        幅を定数で持つと必ずどこかの環境で見切れる（B-002 と同じクラス）。I-000 の
        水平距離列で 1080px の既定値が足りなくなったのを機に、定数合わせをやめた。

        ⚠️ **起動時だけでは足りない**（2.5b2・I-024 のクラス点検で判明）：CSV
        インポートで長い備考が入ると `_sync_header_columns` が列を広げるのに、窓は
        そのままだった＝あとから見切れる。**中身を入れ替える操作のあとにも呼ぶ**。

        測り方は [views/window_fit](views/window_fit.py) に集約（縮めない・画面幅で
        頭打ち・決めた寸法を `_fit_size` に残す＝横断ゲートが読む口）。

        ⚠️ **連続した呼び出しは 1 回に畳む**（I-107）。加算値（スクロールバー幅）は
        **畳んだ先で測る**＝予約した時点の値で測ると、そのあいだに DPI が変わった
        ときにバーの太さが古いまま焼き付く。
        """
        window_fit.fit_soon(self, self._fit_width_now)

    def _fit_width_now(self) -> None:
        # ⚠️ ここから `_run_sync()` を呼ばないこと（相互再帰になる）。同期の直後に
        # こちらが呼ばれる向きに一本化してある。
        # 行はキャンバスの中にあり、縦スクロールバーと外周 padding は
        # winfo_reqwidth() に載らないので extra_w で足す。
        window_fit.fit_to_content(
            self, min_w=self._BASE_W, min_h=self._BASE_H,
            extra_w=self._vsb.winfo_reqwidth() + self._TABLE_PAD_W,
        )

    def _commit_row_coords(self, frame: ttk.Frame, entries: list[tk.Entry]) -> None:
        """座標セルを確定したあとの後始末を**1 か所で**行う。

        整形（I-060 R3）→ 水平距離 → 判定の取り下げ（I-041）→ 地図への通知、の
        4 つが 1 組。**口は 2 つある**＝手で編集して確定したときと、地図で選んだ
        端点を置き直したとき（I-098）。別々に書くと、片方だけ後始末が欠けて
        「地図から直した行にだけ古い判定が残る」ような食い違いが生まれる。
        """
        for c in (1, 2):
            text = entries[c].get()
            shown = coords.reformat(text, self._coord_format)
            if shown != text:
                entries[c].delete(0, tk.END)
                entries[c].insert(0, shown)
        dist = self._distance_label(frame)
        if dist is not None:
            self._update_row_distance(entries, dist)
        # 判定は**その結果を生んだ入力でなくなった行**から取り下げる（B-058 と
        # 同じ判断＝値が変わったときだけ）。
        verdict = self._verdict_label(frame)
        if (verdict is not None and verdict.cget("text")
                and self._row_values(entries) != getattr(verdict, "_verdict_input", None)):
            verdict.config(text="")
        self._notify_paths_changed()

    def _distance_label(self, frame: ttk.Frame) -> "ttk.Label | None":
        """行フレームの水平距離ラベルを返す（引き方は `_verdict_label` と同じ）。"""
        slaves = frame.grid_slaves(row=0, column=len(self._WIDTHS) - 4)
        return slaves[0] if slaves and isinstance(slaves[0], ttk.Label) else None

    def _update_row_distance(self, entries: list[tk.Entry], label: ttk.Label) -> None:
        """行の TX/RX 座標から水平距離を計算して読み取り専用ラベルへ反映する。

        座標が未入力・不正な間は空欄にする（バリデーションは実行時に行う設計
        なので、ここでエラーを出して編集を邪魔しない）。表示単位は units 一任。
        """
        try:
            lat_tx, lon_tx = coords.parse_pair(entries[1].get())
            lat_rx, lon_rx = coords.parse_pair(entries[2].get())
        except Exception:            # noqa: BLE001 — 入力途中は日常的に不正
            label.config(text="")
            return
        km = horizontal_distance_km(lat_tx, lon_tx, lat_rx, lon_rx)
        label.config(text=units.format_distance(km))

    def _schedule_sync(self) -> None:
        """列幅同期をデバウンスして予約する（連続追加を 1 回に畳む）。"""
        if self._sync_after_id is not None:
            self.after_cancel(self._sync_after_id)
        self._sync_after_id = self.after(100, self._run_sync)

    def _run_sync(self) -> None:
        self._sync_after_id = None
        self._sync_header_columns()
        # 列が広がった直後は窓の必要幅も変わっている。ここで測り直さないと
        # 「起動時は正しいが、あとから見切れる」状態になる（I-024 のクラス）。
        # 列幅は窓幅から独立に決まる（行セルと見出しの実測）ので、ここで窓を
        # 広げても列幅は動かない＝再入して発散しない。
        self._fit_width_to_content()

    def _sync_header_columns(self) -> None:
        """ヘッダと各行の列幅を揃えてズレと見出しの見切れを解消する。

        各列の最小幅を「行セルの必要幅」と「見出しの必要幅」の大きい方に合わせ、
        ヘッダ・全行の両グリッドへ同じ minsize を掛ける。これで日本語見出しが
        入りきり（B-002）、かつ列がずれない（見出しは別グリッドのため両者に適用）。

        ⚠️ **セルは `grid_bbox`（実測）ではなく `winfo_reqwidth`（必要量）で測る**。
        bbox は前回この関数が掛けた `minsize` を含んだ「今の幅」なので、測って
        掛け直すたびに前回以上の値しか出ない＝**列幅が一方通行になる**。表示スケール
        を 150% → 100% へ戻しても表は 150% の幅を要求し続け、窓も戻らなかった
        （I-053）。見出し側が最初から必要量で測っているのと揃える。
        """
        if not self._row_frames:
            return
        self._table_frame.update_idletasks()
        self._hdr.update_idletasks()
        for col in range(len(self._WIDTHS)):
            # padx=2 の左右分を見込んで少し余裕を持たせる（行・見出しとも同じ）。
            cell   = self._row_frames[0].grid_slaves(row=0, column=col)
            cell_w = (cell[0].winfo_reqwidth() + 4) if cell else 0
            hdr    = self._hdr.grid_slaves(row=0, column=col)
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

    # ----------------------------------------------------------
    # 判定列（I-041＝結果はその結果を生んだ行に返す）
    # ----------------------------------------------------------
    def _verdict_label(self, frame: ttk.Frame) -> "ttk.Label | None":
        """行フレームの判定ラベルを返す（**出所は行そのもの**）。

        🔑 並行リストを持たないための引き方＝行が消えれば判定も消える。列の位置は
        `_WIDTHS` の 1 か所で決まるので、列を足しても添字を直すのはそこだけ。
        """
        slaves = frame.grid_slaves(row=0, column=len(self._WIDTHS) - 3)
        return slaves[0] if slaves and isinstance(slaves[0], ttk.Label) else None

    def _refresh_verdict_colors(self) -> None:
        """テーマ切替で判定色を貼り直す（`<<ThemeChanged>>` から）。

        ⚠️ ラベルの前景色は生成時ではなく**判定を入れたときに**決まるので、
        Treeview のタグと違い自動では追従しない（結果一覧を持つ窓は
        `apply_verdict_tags` を貼り直している＝同じ手当てをここにも置く）。
        表示中の字（OK / NG / ERR）から色のキーを引ける形にしてあるので、
        行ごとの状態を別に持たなくてよい。
        """
        colors = theme.verdict_colors(self)
        for frame in self._row_frames:
            lbl = self._verdict_label(frame)
            if lbl is None:
                continue
            text = str(lbl.cget("text"))
            if text:
                lbl.config(foreground=colors[theme.verdict_key(text)])

    @staticmethod
    def _row_values(entries: list[tk.Entry]) -> tuple[str, ...]:
        """行の入力値。**判定を消してよいか**の判断はこの値の変化だけで決める（B-058）。"""
        return tuple(e.get() for e in entries)

    def _clear_verdicts(self) -> None:
        """全行の判定を空にする（実行の開始時＝前回の結果を残さない）。

        あわせて**実行に出した入力の控え**を取る（下の `_set_row_verdict` が使う）。
        """
        self._run_inputs = {}
        for entries, frame in zip(self._row_entries, self._row_frames):
            lbl = self._verdict_label(frame)
            if lbl is not None:
                lbl.config(text="")
            pid = entries[0].get().strip().casefold()
            if pid:
                self._run_inputs[pid] = self._row_values(entries)

    def _set_row_verdict(self, path_id: str, status: str) -> None:
        """`path_id` の行へ判定を返す（`"OK"` / `"NG"` / `"ERROR"`）。

        **行番号ではなく ID で引く**＝空行は `_read_table_rows` が飛ばすので、
        実行順の番号と表の行番号は一致しない。ID は `validate_rows` が大小を
        区別せず一意に縛っているので、これで 1 行に定まる（実行中に行が消えても
        「見つからない＝返さない」で済み、別の行へ誤って返さない）。

        ⚠️ **実行中も表は編集できる**（止めているのは実行ボタンだけ）。ID が同じでも
        **その行の入力が実行に出したものと違えば、返ってきた判定はその行の結果では
        ない**ので書かない（2026-08-11・Codex 独立レビュー P1）。以前はここで現在の
        入力を控えていたため、**編集後の値で出た判定であるかのように貼られた**。
        ⇒ 規則は B-058／B-059 と同じ 1 本＝**結果は、それを生んだ入力が変わった
        時点で結果でなくなる**。計算そのものは開始時に凍結した行で走るので、
        **成果物（レポート・CSV）は元から正しい**＝直しているのは画面だけ。

        色は `theme.verdict_colors` が出所（画面ごとに色を書かない・I-005/B-008）。
        表記は進捗帯のカウンタ（`✓ n OK` / `⚠ n ERR`）と揃える。
        """
        key = path_id.strip().casefold()
        sent = getattr(self, "_run_inputs", {}).get(key)
        for entries, frame in zip(self._row_entries, self._row_frames):
            if entries[0].get().strip().casefold() != key:
                continue
            if sent is not None and self._row_values(entries) != sent:
                return          # 実行に出した行ではない（実行中に編集された）
            lbl = self._verdict_label(frame)
            if lbl is None:
                return
            lbl.config(text={"OK": "OK", "NG": "NG"}.get(status, "ERR"),
                       foreground=theme.verdict_colors(lbl)[theme.verdict_key(status)])
            # **この判定を生んだ入力**を控える（B-058）。以後この値から変わった
            # ときだけ判定を消す＝移動キーや修飾キーでは消えない。
            setattr(lbl, "_verdict_input", self._row_values(entries))
            return

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
        """行の右クリックメニューを表示する。

        🔴 **メニューの実体は窓に 1 つ**（2026-08-24・B-121 のクラス点検）＝毎回
        `tk.Menu` を作ると**親の子として残り続ける**（`finally` は `grab_release()`
        だけ）。行ごとに右クリックできるここは、**地図より速く積み上がる**。
        ⚠️ 中身（ラベルと、その行に束ねたコマンド）は**毎回組み直す**＝使い回すのは
        ウィジェットだけ。行を掴んだままのコマンドが残ると、消した行を操作できてしまう。
        """
        menu: "tk.Menu | None" = getattr(self, "_row_menu_widget", None)
        if menu is None or not menu.winfo_exists():
            menu = tk.Menu(self, tearoff=0)
            self._row_menu_widget = menu        # type: ignore[attr-defined]
        menu.delete(0, "end")
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
        # ランチャーのメニューと同じくテーマ非追従なので配色を明示する（B-008）。
        theme.apply_menu_theme([menu], self)
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
        self._run_sync()

    def _clear_all(self) -> None:
        """テーブルの全行を削除する（確認ダイアログあり）。"""
        if not self._row_frames:
            return
        if dialogs.confirm(
            self,
            i18n.t("btn_clear_all"),
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
    def replace_rows(self, rows: list) -> None:
        """表の全行を差し替える（CSV インポートの本体）。

        ファイル選択ダイアログから切り離してあるのは、**インポート後に窓が中身へ
        追従すること**をテストから叩けるようにするため（長い備考が入ると列幅同期が
        列を広げる＝窓の必要幅が変わる）。逐次通知を抑止して再構築し、終了後に
        1 回だけ地図へ通知する。
        """
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
        self._run_sync()             # 列幅同期 → 窓を中身へ追従（見切れ防止）
        self._notify_paths_changed()
