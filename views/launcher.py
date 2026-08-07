"""
views/launcher.py
=================
入力フォームウィンドウ（SimLauncher）。

計算・通信・ファイル I/O は一切行わない。
simulation・config・dem の各モジュールを呼ぶだけ。
"""

import os
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Callable

import config
import coords
import dem
import i18n
import simulation as sim
import version
from models import ENV_DEFAULT, ENV_KEYS
from views import dialogs, theme, window_fit
from views.launcher_menu import _MenuMixin
from views.launcher_project import _ProjectMixin
from views.launcher_windows import _ChildWindowsMixin
from views.tooltip import Tooltip
from views.progress import ProgressPump

if TYPE_CHECKING:              # 型注釈のためだけ＝起動時の import 連鎖を増やさない
    import project

# 入力キー → i18n ツールチップキーのマッピング
_TIP_KEYS: dict[str, str] = {
    "start":    "tip_start",
    "end":      "tip_end",
    "h_tx":     "tip_h_tx",
    "h_rx":     "tip_h_rx",
    "freq":     "tip_freq",
    "p_tx":     "tip_p_tx",
    "gain_tx":  "tip_gain_tx",
    "gain_rx":  "tip_gain_rx",
    "sens":     "tip_sens",
    "veg_h":    "tip_veg_h",
    "k_factor": "tip_k_factor",
    "samples":  "tip_samples",
    "rain_rate": "tip_rain_rate",
}




class SimLauncher(_MenuMixin, _ProjectMixin, _ChildWindowsMixin):
    """メインウィンドウ：入力フォーム・進捗バー・実行ボタン。"""

    # ウィンドウ幅（固定）と、内容が少ないときでも下回らない高さ。
    _WIN_WIDTH  = 450
    _MIN_HEIGHT = 900

    # ロゴの表示上限。**高さ側が本質**＝ランチャーは縦がいちばん苦しい窓で、FHD
    # 100% の使える高さ 990px に対して必要高が 1023px あり、33px が下端のボタン列
    # から削られていた。ロゴは窓の中で唯一「機能を持たない帯」なので、足りない分は
    # まずここから返す（入力欄・ボタンの余白を削って情報密度を上げる前に）。
    # ⚠️ 高さの上限を外す/緩めるときは必ず tests/test_window_fit.py の FHD ゲートを
    #    回すこと（緩めた分だけ最下段のボタンが削られる＝見切れ 7 回目になる）。
    _LOGO_MAX_W = 460
    _LOGO_MAX_H = 56

    def __init__(self, root: tk.Tk, on_theme: Callable[[str], None]) -> None:
        self.root = root
        root.title(version.APP_FULL)
        root.resizable(False, False)

        self.config    = config.load_config()
        dem.set_proxy(self.config.get("proxy_url", ""))
        self.entries:  dict[str, tk.Entry] = {}
        self._on_theme = on_theme
        # プロジェクト（`.rsproj`）の保持者はランチャー。**閉じている窓の節は
        # ここに持ち越す**＝バッチ窓を閉じただけで行が消えたファイルを上書き保存
        # する事故を防ぐ（project.py の「節が無い＝空ではない」と対）。
        # 型は project.ProjectDoc。起動時に project を import しないため遅延生成
        # （project → batch → report 系と import 連鎖が伸び、初回描画が遅くなる）。
        self._project: "project.ProjectDoc | None" = None

        self._build_ui()
        self._fit_window_to_content()
        # 終了時、開いたままのマップウィンドウの after ループを止めてから破棄する
        # （tkintermapview の `invalid command name ...update_canvas_tile_images`
        # 対策。MapWindow._on_close と対）。
        root.protocol("WM_DELETE_WINDOW", self._on_app_close)

    def _fit_window_to_content(self) -> None:
        """ウィンドウを中身の必要量に合わせる（端の切り落とし防止）。

        ウィンドウは `resizable(False, False)` の固定サイズなので、**寸法を
        リテラルで持つと入力欄を1グループ足すたびに端が黙って切れる**。実際
        2.4 で「案件情報」グループ（案件名・メモ）を足したとき必要高さが 931px に
        なり、900px 固定のままだったため最下段の「マップウィンドウ」ボタンが
        丸ごと見えなくなっていた（ユーザー報告・2026-07-20）。ユーザーは
        リサイズもできないので回避手段が無い。幅も同じで、2.5b2 のフォント統一で
        必要幅が 464px になり 450px 固定では右端が詰まった（I-023）。

        測り方は [views/window_fit](views/window_fit.py) に集約してある
        （見切れは窓ごとに直しては再発してきたクラスなので、実装を 1 つにする）。
        `_WIN_WIDTH` / `_MIN_HEIGHT` は**下限**として残す。

        ⚠️ 検証するのは**選んだ寸法**（`window_fit` が `_fit_size` に残す）で
        あって実現後のサイズではない。ウィンドウが未表示のあいだ `geometry()` は
        設定値ではなく自然サイズを返すため、それと比べるテストは壊れた実装でも
        緑になる（最初に書いたガードはこれで壊れた実装のまま通った）。
        ガード＝tests/test_window_fit.py（全窓横断）。
        """
        self._window_width, self._window_height = window_fit.fit_to_content(
            self.root, min_w=self._WIN_WIDTH, min_h=self._MIN_HEIGHT,
        )

    def _on_app_close(self) -> None:
        # 破棄前にポーリングを止める（破棄済み root への after を避ける）。
        self._pump.stop()
        map_widget = None
        win = getattr(self, "_map_win", None)
        if win is not None:
            try:
                if win._win.winfo_exists():
                    map_widget = win._map
            except Exception:
                pass
        # ⚠️ ここには「pyplot の全 Figure を閉じる」後始末があった。グラフ窓が
        # pyplot の窓（独自の Tk ルート＋入れ子 mainloop）で、閉じないとプロセスが
        # 終了しなかったため。**B-024 で Toplevel になり pyplot を使わなくなった**
        # ので不要＝root の破棄で一緒に片付く（残すと matplotlib を遅延 import する
        # 意味も薄れる）。
        # マップが開いたままなら、再スケジュールを止めてから猶予をおいて root を
        # 破棄する（破棄手順は map_window.close_map_safely に集約。直後に destroy
        # すると tkintermapview の `...update_canvas_tile_images` が破棄後に発火する）。
        if map_widget is not None:
            from views.map_window import close_map_safely
            close_map_safely(self.root, map_widget, self.root.destroy)
            return
        self.root.destroy()

    # ----------------------------------------------------------
    # UI 構築
    # ----------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_menu()
        # 中身は**スクロールの受け皿の中**へ組み立てる（B-021②）。ランチャーは
        # 100% の FHD でも必要高 973px（125% では 1148px）で、画面の使える高さ
        # 990px を超えた分は**下詰めのフッタに押されてボタン列から削られる**。
        # 受け皿があれば溢れてもスクロールで届く（入るあいだはバーも出ない）。
        # ⚠️ メニューバーは wm の持ち物なので受け皿の外（`self.root` のまま）。
        body = window_fit.scrollable_body(self.root)

        # side="bottom" はパック順が逆（先にパックしたものが下）
        # copyright → logo の順にパックすると copyright が最下部、logo がその直上になる
        tk.Label(
            body,
            text=version.COPYRIGHT,
            fg=theme.muted_foreground(body),
            font=theme.ui_font(body, "small"),
        ).pack(side="bottom", pady=(0, 6))
        self._build_logo(body)

        container = ttk.Frame(body, padding=(20, 10))
        container.pack(fill="both", expand=True)

        self._build_site_group(container)
        self._build_radio_group(container)
        self._build_env_group(container)
        self._build_case_group(container)
        self._build_status(container)
        self._build_buttons(container)

        # 保存済み座標形式が DMS なら、起動時に start/end 欄を DMS 表記へ整形する。
        self._refresh_coord_display()






    def _build_site_group(self, parent: tk.Widget) -> None:
        g = ttk.LabelFrame(parent, text=i18n.t("grp_site_info"), padding=5)
        g.pack(fill="x", pady=5)

        for lbl_key, entry_key in [
            ("lbl_start", "start"),
            ("lbl_end",   "end"),
            ("lbl_h_tx",  "h_tx"),
            ("lbl_h_rx",  "h_rx"),
        ]:
            self._add_row(g, i18n.t(lbl_key), entry_key)

    def _build_radio_group(self, parent: tk.Widget) -> None:
        g = ttk.LabelFrame(parent, text=i18n.t("grp_radio_settings"), padding=5)
        g.pack(fill="x", pady=5)
        for lbl_key, entry_key in [
            ("lbl_freq",    "freq"),
            ("lbl_p_tx",    "p_tx"),
            ("lbl_gain_tx", "gain_tx"),
            ("lbl_gain_rx", "gain_rx"),
            ("lbl_sens",    "sens"),
        ]:
            self._add_row(g, i18n.t(lbl_key), entry_key)

    def _build_env_group(self, parent: tk.Widget) -> None:
        g = ttk.LabelFrame(parent, text=i18n.t("grp_environment"), padding=5)
        g.pack(fill="x", pady=5)

        # 環境区分 Combobox（Entry ではなく選択式）
        f_env = ttk.Frame(g)
        f_env.pack(fill="x", pady=2, padx=10)
        ttk.Label(
            f_env, text=i18n.t("lbl_env_type"), width=22, anchor="w"
        ).pack(side="left")
        # 表示ラベルは i18n の env_<key> を単一ソースに（言語連動）。内部は常にキー。
        self._env_key_to_label = {k: i18n.t(f"env_{k}") for k in ENV_KEYS}
        self._env_label_to_key = {v: k for k, v in self._env_key_to_label.items()}
        saved_key     = self.config.get("env_type", ENV_DEFAULT)
        saved_label   = self._env_key_to_label.get(
            saved_key, self._env_key_to_label[ENV_DEFAULT]
        )

        self._env_var = tk.StringVar(value=saved_label)
        ttk.Combobox(
            f_env,
            textvariable = self._env_var,
            values       = list(self._env_key_to_label.values()),
            state        = "readonly",
            width        = 16,
        ).pack(side="right", expand=True, fill="x")

        # 降雨強度（方針A: 従来はランチャー欄が無く config 補完だった）
        self._add_row(g, i18n.t("lbl_rain"), "rain_rate")

        # 回折モデル Combobox（方針A: env_type と同じ readonly 選択式）
        f_diff = ttk.Frame(g)
        f_diff.pack(fill="x", pady=2, padx=10)
        ttk.Label(
            f_diff, text=i18n.t("lbl_diff_method"), width=22, anchor="w",
        ).pack(side="left")
        self._diff_key_to_label = {
            "deygout": i18n.t("diff_opt_deygout"),
            "single":  i18n.t("diff_opt_single"),
        }
        self._diff_label_to_key = {v: k for k, v in self._diff_key_to_label.items()}
        saved_diff = self.config.get("diff_method", "deygout")
        self._diff_var = tk.StringVar(
            value=self._diff_key_to_label.get(
                saved_diff, self._diff_key_to_label["deygout"]
            )
        )
        cb_diff = ttk.Combobox(
            f_diff,
            textvariable = self._diff_var,
            values       = list(self._diff_key_to_label.values()),
            state        = "readonly",
            width        = 16,
        )
        cb_diff.pack(side="right", expand=True, fill="x")
        Tooltip(cb_diff, i18n.t("tip_diff_method"))

        for lbl_key, entry_key in [
            ("lbl_veg_h",    "veg_h"),
            ("lbl_k_factor", "k_factor"),
            ("lbl_samples",  "samples"),
        ]:
            self._add_row(g, i18n.t(lbl_key), entry_key)

    def _build_case_group(self, parent: tk.Widget) -> None:
        """案件名・自由メモ（レポートの自己同定ヘッダに載る任意メタ情報）。

        RF/環境パラメータと同じく **ランチャーが source of truth**。ここで一度入力すれば
        シングル（保存時）もバッチ（Common Settings と同じくスナップショット）も同じ値を
        踏襲する。計算には影響しない報告書メタ（数フィールド＋自由メモに厳格限定＝
        テンプレエディタ化しない）。セッション内保持で永続化はしない。
        """
        g = ttk.LabelFrame(parent, text=i18n.t("batch_case_info"), padding=5)
        g.pack(fill="x", pady=5)

        self._project_var = tk.StringVar()
        self._memo_var    = tk.StringVar()

        f_proj = ttk.Frame(g)
        f_proj.pack(fill="x", pady=2, padx=10)
        ttk.Label(f_proj, text=i18n.t("batch_project_name"), width=22,
                  anchor="w").pack(side="left")
        ttk.Entry(f_proj, textvariable=self._project_var).pack(
            side="right", expand=True, fill="x")

        f_memo = ttk.Frame(g)
        f_memo.pack(fill="x", pady=2, padx=10)
        ttk.Label(f_memo, text=i18n.t("batch_memo"), width=22,
                  anchor="w").pack(side="left")
        ttk.Entry(f_memo, textvariable=self._memo_var).pack(
            side="right", expand=True, fill="x")

    def _current_meta(self) -> dict[str, str]:
        """レポート用の任意メタ（案件名・自由メモ）の現在値を返す。

        バッチが Common Settings と同じく「ランチャー（source of truth）の
        スナップショット」として取り込むための provider。
        """
        return {
            "project_name": self._project_var.get().strip(),
            "memo":         self._memo_var.get().strip(),
        }

    def _build_status(self, parent: tk.Widget) -> None:
        # 実行帯は**上段＝ステータス 1 行／下段＝バー（左・伸縮）＋実行（右端）**
        # の 2 段（I-047・4 窓共通）。ステータスをバーの*横*に置くと、文言の長さで
        # 実行ボタンの位置が動く（左寄せの 1 行なら文言が伸びても帯の形は変わらない）。
        prog_frame = ttk.Frame(parent)
        prog_frame.pack(fill="x", pady=(10, 5))

        row1 = ttk.Frame(prog_frame)
        row1.pack(fill="x")
        self._prog_label = ttk.Label(row1, text=i18n.t("status_ready"), anchor="w")
        self._prog_label.pack(side="left", fill="x", expand=True)

        # 進捗バーと「実行」を同じ帯に置く（I-029）。**3 つの実行フローで同じ名前・
        # 同じ位置**＝バッチが既にこの配置なので、一番情報量の多い窓を動かさずに
        # 単一と条件探索の 2 窓だけを揃える側に回した。
        bar = ttk.Frame(prog_frame)
        bar.pack(fill="x", pady=(2, 0))
        self._prog_bar = ttk.Progressbar(
            bar, orient="horizontal", length=350, mode="determinate"
        )
        self._prog_bar.pack(side="left", fill="x", expand=True)
        # Accent（青）は**「走らせる」ボタンだけ**に使う（I-029/I-030）。以前は
        # 「一括シミュレーション」（＝窓を開く操作）にも付いており、強調の軸が
        # 意味の軸と直交していた。
        self._run_btn = ttk.Button(bar, text=i18n.t("btn_run_sim"),
                                  command=self._on_run, style="Accent.TButton")
        self._run_btn.pack(side="right", padx=(10, 0))

        # 進捗はワーカースレッドから ProgressPump 経由で受け取る。バーとラベルは
        # 「最新の状態」だけが意味を持つので latest_only（中間値を全部描いても
        # 見えないうえ、取得は 1 サンプルごとに push される）。
        self._pump = ProgressPump(
            self.root, self._render_progress, latest_only=True
        )

    # ----------------------------------------------------------
    # 進捗表示（ワーカースレッド → ProgressPump → メインスレッド）
    # ----------------------------------------------------------
    def _render_progress(self, item: tuple) -> None:
        """ポンプから届いた進捗を描画する（メインスレッドで呼ばれる）。"""
        value, text = item
        self._prog_bar.config(value=value)
        self._prog_label.config(text=text)

    def _progress_push(self, value: float, text: str) -> None:
        """ワーカースレッドから進捗を送る。Tk には一切触れない。

        以前は 1 サンプルごとに root.after(0, ...) を呼んでいたが、
        simulation.fetch_elevations は on_progress を**グローバルロック保持中**に
        呼ぶ（simulation.py の _fetch_one）ため、Tcl 呼び出しのコストが全ワーカーを
        直列化し取得時間そのものを支配していた。実測（2.4b1・200 サンプル・
        キャッシュ暖機済み）で約 1.0s → キュー化で約 0.035s。
        """
        self._pump.push((value, text))

    def _progress_reset(self, maximum: int, text: str) -> None:
        """バーの目盛りを切り替える（メインスレッドから呼ぶ）。

        フェーズ切替時に前フェーズの積み残しが新しい maximum で描画されるのを
        避けるため、キューを捨ててから設定する。
        """
        self._pump.clear()
        self._prog_bar.config(maximum=max(maximum, 1), value=0)
        self._prog_label.config(text=text)

    def _progress_stop(self) -> None:
        """進捗ポーリングを止め、積み残しを捨てる（メインスレッドから呼ぶ）。"""
        self._pump.stop()

    def _build_buttons(self, parent: tk.Widget) -> None:
        """**別の窓を開く**ボタンだけを置く（I-030）。

        ランチャーの操作は意味で 3 カテゴリーに分かれる:

          1. **走らせる**（1 個）→ 進捗バーの右・`Accent.TButton` はここだけ（I-029）
          2. **別の窓を開く**（3 個）→ ここ
          3. **ファイル／OS へ出る**（2 個）→ **メニューバーの「ファイル」**

        以前は 3×2 の 6 個が同じ見た目の並びに混在し、`Accent` が「走らせる」と
        「窓を開く」をまたいでいた＝**強調の軸が意味の軸と直交していた**。
        3 の 2 個をメニューへ移した根拠は「結果フォルダが要る瞬間は*実行した直後*に
        時間的に局在している」＝恒久ボタンは場所が間違っており、必要が発生する
        その場所（完了ダイアログの「保存先を開く」）に置く。
        """
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(6, 4))

        # **2 列 × 2 行**＝行き先が 4 つになった時点で縦積みは背が高すぎる
        # （この窓は FHD 100% の使える高さ 990px に対して余裕が十数 px しかない
        # ＝B-021。4 つ目を縦に足した瞬間に上限を超えた）。2 列なら日本語ラベルも
        # 入る（元の 3×2 レイアウトで実績がある幅）。
        # ⚠️ 5 つ目を足すときは、また高さの予算を測ること
        # （tests/test_window_fit.py の 100% ゲートが止めてくれる）。
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        # **並びは「何を回すか」の軸**（I-051）＝①経路を複数回す（複数経路／中継経路
        # ＝親戚）②1 経路を振る（条件探索）③入力の道具（地図）。地図を最後に置くのは
        # **他 3 つの入力元**だからで、実行フローと同じ列に並ぶと同格に見える
        # （I-030「強調の軸を意味の軸に合わせる」の続き）。
        # ⚠️ 区切り線や見出しは足さない＝4 個に見出しを付けるのは⑤に反する。
        # **並び順だけで意味を表す。**
        for i, (key, command) in enumerate((
            ("btn_batch_mode", self._on_batch),
            ("mh_open_btn",    self._on_open_multihop),
            ("scn_open_btn",   self._on_open_scenario),
            ("btn_open_map",   self._on_open_map),
        )):
            ttk.Button(frame, text=i18n.t(key), command=command).grid(
                row=i // 2, column=i % 2, sticky="ew",
                padx=(0, 2) if i % 2 == 0 else (2, 0), pady=1, ipady=4)

    def _build_logo(self, parent: tk.Misc) -> None:
        """logo.png をボタン下の余白に表示する。ファイルがなければ何もしない。"""
        import sys
        base = getattr(
            sys, "_MEIPASS",
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        logo_path = os.path.join(base, "logo.png")
        if not os.path.exists(logo_path):
            return
        try:
            from PIL import Image, ImageTk
            img = Image.open(logo_path).convert("RGBA")
            w, h = img.size
            # 幅・高さの両方に収まる倍率を採る（縦横比は保つ）。拡大はしない。
            scale = min(self._LOGO_MAX_W / w, self._LOGO_MAX_H / h, 1.0)
            if scale < 1.0:
                w, h = max(int(w * scale), 1), max(int(h * scale), 1)
                img = img.resize((w, h), Image.Resampling.LANCZOS)
            self._logo_image = ImageTk.PhotoImage(img)
            tk.Label(parent, image=self._logo_image).pack(side="bottom", pady=(4, 8))
        except Exception as e:
            config.logger.warning("Logo load failed: %s", e)

    def _add_row(self, parent: tk.Widget, label: str, key: str) -> None:
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=2, padx=10)
        ttk.Label(
            f, text=label, width=22, anchor="w"
        ).pack(side="left")
        # ⚠️ 素の tk.Entry はスタイルに追従しないので font を明示する（新規は ttk で）。
        e = tk.Entry(f, font=theme.ui_font(parent))
        e.insert(0, self.config[key])
        e.pack(side="right", expand=True, fill="x")
        self.entries[key] = e
        if key in _TIP_KEYS:
            Tooltip(e, i18n.t(_TIP_KEYS[key]))
        if key in ("start", "end"):
            # **入力の確定で現在の表記へ整形する**（I-060 R3）。整形されること
            # 自体が「読めた」という返事で、読めなければ原文が残る＝parse の成否が
            # 目で分かる。これが無いと、DMS 表記を選んだ状態で DD を入れたとき
            # 「受理された」のか「無視された」のかが画面から区別できない。
            # ⚠️ 打鍵ごとには整形しない（編集できなくなる）＝確定の 2 契機だけ。
            e.bind("<FocusOut>", lambda _ev, k=key: self._reformat_entry(k), add="+")
            e.bind("<Return>",   lambda _ev, k=key: self._reformat_entry(k), add="+")

    # ----------------------------------------------------------
    # 座標形式（DD/DMS）切替
    # 数値欄は常に source of truth。表示 notation だけを変える。
    # ----------------------------------------------------------
    def _on_coord_format_change(self) -> None:
        """DD/DMS ラジオ切替時：start/end 欄を新表記へ整形し、選択を永続化する。"""
        mode = self._coord_fmt_var.get()
        self._refresh_coord_display()
        self.config["coord_format"] = mode
        config.save_app(self.config)

    def _refresh_coord_display(self) -> None:
        """start/end 欄の文字列を現在の座標形式へ整形する（パース不能なら原文維持）。"""
        for key in ("start", "end"):
            self._reformat_entry(key)

    def _reformat_entry(self, key: str) -> None:
        """1 欄だけを現在の座標形式へ整形する（パース不能なら原文のまま）。

        **入力の確定（Enter / focus 離脱）と、表記の切替の両方がここを通る**
        （I-060 R3）＝整形の規則を 2 か所に書かない。
        """
        entry = self.entries.get(key)
        if entry is None:
            return
        new_text = coords.reformat(entry.get(), self._coord_fmt_var.get())
        if new_text == entry.get():
            return                      # 変わらないなら触らない（カーソルを飛ばさない）
        entry.delete(0, tk.END)
        entry.insert(0, new_text)

    def _coords_to_dd(self, c: dict[str, str]) -> None:
        """config dict 中の start/end を DD 文字列へ正規化する（in-place）。

        DMS 表記で入力されていても downstream（SimParams/validate_config）には
        常に DD を渡す。不正値は原文のまま残し validate に委ねる。
        """
        for key in ("start", "end"):
            if key in c:
                c[key] = coords.to_dd_str(c[key])

    # ----------------------------------------------------------
    # ダイアログ位置制御
    # ----------------------------------------------------------
    def _alert(self, title: str, message: str) -> None:
        """ランチャー中央にモーダルダイアログを表示する。"""
        dialogs.alert(self.root, title, message)

    def _confirm(self, title: str, message: str) -> bool:
        """ランチャー中央に Yes/No 確認ダイアログを表示し、Yes なら True を返す。"""
        return dialogs.confirm(self.root, title, message)



    # ----------------------------------------------------------
    # イベントハンドラ
    # ----------------------------------------------------------
    def _on_run(self) -> None:
        c = {k: self.entries[k].get() for k in self.entries}
        c["env_type"] = self._env_label_to_key.get(self._env_var.get(), "suburban")
        c["diff_method"] = self._diff_label_to_key.get(self._diff_var.get(), "deygout")
        self._coords_to_dd(c)  # DMS 入力でも downstream には DD を渡す

        errors = config.validate_config(c)
        if errors:
            self._alert(i18n.t("dlg_input_error"), "\n".join(errors))
            config.logger.warning("Validation failed: %s", errors)
            return

        try:
            params = sim.SimParams(c)
        except Exception as ex:
            self._alert(i18n.t("dlg_error"), str(ex))
            return

        # sim キーのみ保存。app 設定（theme/lang/proxy_url）は save_sim 内で保持される。
        config.save_sim(c)
        self._run_btn.config(state="disabled")

        # Phase 1: bbox 内の DEM タイルを事前取得
        tile_count = dem.count_bbox_tiles(
            params.lat_tx, params.lon_tx,
            params.lat_rx, params.lon_rx,
        )
        self._progress_reset(tile_count, i18n.t("status_prefetch"))
        self._pump.start()

        def _prefetch_progress(done: int, total: int) -> None:
            pct = int(done / total * 100)
            self._progress_push(
                done, i18n.t("status_prefetch_pct").format(pct=pct)
            )

        def _run_prefetch() -> None:
            try:
                dem.prefetch_tiles(
                    params.lat_tx, params.lon_tx,
                    params.lat_rx, params.lon_rx,
                    progress_cb=_prefetch_progress,
                )
            except Exception as ex:
                config.logger.warning("Prefetch error (continuing): %s", ex)
            self.root.after(0, self._notify_map_cache_change)
            self.root.after(0, lambda: self._start_simulation(params))

        threading.Thread(target=_run_prefetch, daemon=True).start()

    def _start_simulation(self, params: sim.SimParams) -> None:
        """Phase 2: 標高取得 → グラフ表示。"""
        # 通常は Phase 1 で開始済みだが、start は冪等なので各フェーズが自前で
        # 開始してよい（この相だけを呼ぶ経路が増えても進捗が黙って消えない）。
        self._pump.start()
        self._progress_reset(params.num, i18n.t("status_fetching"))

        def _on_progress(v: int) -> None:
            pct = int(v / params.num * 100)
            self._progress_push(
                v, i18n.t("status_fetching_pct").format(pct=pct)
            )

        def _on_complete(elevs) -> None:
            self.root.after(0, lambda: self._on_fetch_complete(params, elevs))

        def _on_error(ex: Exception) -> None:
            self.root.after(0, lambda: self._on_fetch_error(ex))

        sim.fetch_elevations_cached(
            params      = params,
            on_progress = _on_progress,
            on_complete = _on_complete,
            on_error    = _on_error,
        )

    def _on_fetch_complete(self, params: sim.SimParams, raw_elevs) -> None:
        self._progress_stop()
        self._run_btn.config(state="normal")
        # ここから先（matplotlib の遅延 import＋グラフ構築）が単一実行の体感時間の
        # 大半を占める。実測（キャッシュ暖機済み・200 サンプル）で取得 0.035s に対し
        # import 0.26s＋構築 0.34s ＝ 約 0.6s。従来はこの直前に「準備完了」へ戻して
        # いたため、**実際にはメインスレッドが固まっている 0.6 秒のあいだ「準備完了」と
        # 表示していた**（B-006 と同じ配分ミスが単一側に残っていた）。
        #
        # グラフは pyplot/TkAgg なのでワーカースレッドへは出せない＝ここは本物の
        # メインスレッド制約。したがって偽の進捗は出さず、バッチの段階ラベルと同じく
        # 「何をしているか」だけを示す（→ batch の batch_stage_render）。
        self._prog_label.config(text=i18n.t("status_rendering"))
        self.root.update_idletasks()   # 描画に入る前にラベルを実際に出す

        # phase 境界ログ。バッチには b3 で入れたが単一側は無く、この 0.6 秒が
        # ログ上まったく見えなかった（→ 開発環境 C-b3② を3フロー対称化）。
        self._t_render = time.perf_counter()

        # matplotlib/TkAgg/numpy はここで初めて要る（ランチャー表示前に
        # ロードしないため遅延 import。MapWindow/BatchBuilder と同じ方針）
        from views.graph import show_graph
        meta = self._current_meta()
        # **唯一インスタンス**＝実行のたびに開き直す（バッチ・地図・条件探索と
        # 同じ流儀）。B-024 で Tk 化してブロックしなくなったので、放っておくと
        # 実行のたびに窓が増える。**結果を並べて比べるのは条件探索の担当**
        # （回折トグルを撤去したのと同じ線引き＝比較の器を 2 つ持たない）。
        old = getattr(self, "_graph_win", None)
        if old is not None and old.winfo_exists():
            old.destroy()
        self._graph_win = show_graph(
            self.root, params, raw_elevs, meta["project_name"], meta["memo"],
            on_close = self._on_graph_closed,
            # app 設定（座標表記）は開く時点で凍結して渡す＝窓が保存のたびに
            # `config.load_config()` を読み直さない（I-055 ②・2.7 スライス G2）。
            coord_format = self._coord_fmt_var.get(),
        )
        # 窓が出たので待機状態へ戻す。⚠️ 以前は `plt.show()` がここでブロック
        # したため、表示直前に呼ばれる `on_ready` フックが要った（戻り値を待つと
        # グラフを閉じるまで「準備中」が残った）。Toplevel になって不要になった。
        config.logger.info(
            "Graph render complete in %.2fs",
            time.perf_counter() - self._t_render,
        )
        self._prog_label.config(text=i18n.t("status_ready"))
        self._prog_bar.config(value=0)

    def _on_graph_closed(self) -> None:
        self._graph_win = None

    def _on_fetch_error(self, ex: Exception) -> None:
        self._progress_stop()
        self._alert(i18n.t("dlg_error"), str(ex))
        self._run_btn.config(state="normal")
        self._prog_label.config(text=i18n.t("status_ready"))
        self._prog_bar.config(value=0)


    def _apply_sim_config(self, conf: dict) -> None:
        """sim パラメータを入力欄へ流し込む（settings.json とプロジェクトの共通口）。

        **app 設定（theme/lang/proxy_url）は `select_sim` が落とす**＝他人のファイルを
        開いた瞬間に言語やプロキシが変わらない（`.rsproj` 側では project.py も同じ
        関門を通す＝二重の守り）。
        """
        conf = config.select_sim(conf)
        for k, v in conf.items():
            if k in self.entries:
                self.entries[k].delete(0, tk.END)
                self.entries[k].insert(0, str(v))
        if "env_type" in conf:
            label = self._env_key_to_label.get(
                conf["env_type"], self._env_key_to_label["suburban"]
            )
            self._env_var.set(label)
        if "diff_method" in conf:
            self._diff_var.set(
                self._diff_key_to_label.get(
                    str(conf["diff_method"]),
                    self._diff_key_to_label["deygout"],
                )
            )
        # ファイルの座標は DD。現在の表示形式（DMS かも）へ整形し直す。
        self._refresh_coord_display()








    def _current_config(self) -> dict[str, str]:
        """現在のエントリ値を config dict として返す（バリデーションなし）。"""
        c = {k: self.entries[k].get() for k in self.entries}
        c["env_type"]    = self._env_label_to_key.get(self._env_var.get(), "los")
        c["diff_method"] = self._diff_label_to_key.get(self._diff_var.get(), "deygout")
        self._coords_to_dd(c)
        return c
