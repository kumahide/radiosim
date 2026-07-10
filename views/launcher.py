"""
views/launcher.py
=================
入力フォームウィンドウ（SimLauncher）。

計算・通信・ファイル I/O は一切行わない。
simulation・config・dem の各モジュールを呼ぶだけ。
"""

import json
import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable

import config
import coords
import dem
import i18n
import simulation as sim
import version
from models import ENV_DEFAULT, ENV_KEYS
from views import dialogs

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


class _Tooltip:
    """マウスホバーで入力ヒントを表示する軽量ツールチップ。"""

    _DELAY_MS = 600

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget   = widget
        self._text     = text
        self._tip: tk.Toplevel | None = None
        self._after_id: str | None    = None
        widget.bind("<Enter>",    self._schedule)
        widget.bind("<Leave>",    self._cancel)
        widget.bind("<FocusOut>", self._cancel)

    def _schedule(self, _=None) -> None:
        self._cancel()
        self._after_id = self._widget.after(self._DELAY_MS, self._show)

    def _show(self) -> None:
        if self._tip:
            return
        x = self._widget.winfo_rootx() + self._widget.winfo_width() // 2
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tip, text=self._text,
            bg="SystemButtonFace", relief="solid", borderwidth=1,
            font=("Arial", 8), padx=5, pady=3,
        ).pack()

    def _cancel(self, _=None) -> None:
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip:
            self._tip.destroy()
            self._tip = None


class SimLauncher:
    """メインウィンドウ：入力フォーム・進捗バー・実行ボタン。"""

    def __init__(self, root: tk.Tk, on_theme: Callable[[str], None]) -> None:
        self.root = root
        root.title(version.APP_FULL)
        root.geometry("450x900")
        root.resizable(False, False)

        self.config    = config.load_config()
        dem.set_proxy(self.config.get("proxy_url", ""))
        self.entries:  dict[str, tk.Entry] = {}
        self._on_theme = on_theme

        self._build_ui()
        # 終了時、開いたままのマップウィンドウの after ループを止めてから破棄する
        # （tkintermapview の `invalid command name ...update_canvas_tile_images`
        # 対策。MapWindow._on_close と対）。
        root.protocol("WM_DELETE_WINDOW", self._on_app_close)

    def _on_app_close(self) -> None:
        map_widget = None
        win = getattr(self, "_map_win", None)
        if win is not None:
            try:
                if win._win.winfo_exists():
                    map_widget = win._map
            except Exception:
                pass
        # 地形グラフ（matplotlib pyplot）は独自の Tk ルートとネストした mainloop を
        # 持つため、ランチャーを閉じても plt.show() のループが残りプロセスが終了
        # しない。全 Figure を閉じてループを抜けさせる（matplotlib は遅延 import）。
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:
            pass
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
        # side="bottom" はパック順が逆（先にパックしたものが下）
        # copyright → logo の順にパックすると copyright が最下部、logo がその直上になる
        tk.Label(
            self.root,
            text=version.COPYRIGHT,
            fg="gray",
            font=("Arial", 8),
        ).pack(side="bottom", pady=(0, 6))
        self._build_logo(self.root)

        container = ttk.Frame(self.root, padding=(20, 10))
        container.pack(fill="both", expand=True)

        self._build_site_group(container)
        self._build_radio_group(container)
        self._build_env_group(container)
        self._build_case_group(container)
        self._build_status(container)
        self._build_buttons(container)

        # 保存済み座標形式が DMS なら、起動時に start/end 欄を DMS 表記へ整形する。
        self._refresh_coord_display()

    def _build_menu(self) -> None:
        # tk.Menu は sv_ttk（ttk 専用）のテーマに追従しないため、生成した全メニューを
        # 保持し、テーマ変更ごとに配色を明示適用する（_apply_menu_theme・B-004）。
        self._themed_menus: list[tk.Menu] = []
        menubar = tk.Menu(self.root)
        self._themed_menus.append(menubar)

        settings_menu = tk.Menu(menubar, tearoff=False)
        self._themed_menus.append(settings_menu)

        theme_menu = tk.Menu(settings_menu, tearoff=False)
        self._themed_menus.append(theme_menu)
        self._theme_var = tk.StringVar(value=self.config.get("theme", "system"))
        for label, value in [
            (i18n.t("menu_system"), "system"),
            (i18n.t("menu_light"),  "light"),
            (i18n.t("menu_dark"),   "dark"),
        ]:
            theme_menu.add_radiobutton(
                label    = label,
                variable = self._theme_var,
                value    = value,
                command  = lambda v=value: self._on_theme_select(v),
            )
        settings_menu.add_cascade(label=i18n.t("menu_theme"), menu=theme_menu)

        lang_menu = tk.Menu(settings_menu, tearoff=False)
        self._themed_menus.append(lang_menu)
        self._lang_var = tk.StringVar(value=self.config.get("lang", "en"))
        for label, value in [
            (i18n.t("lang_en"), "en"),
            (i18n.t("lang_ja"), "ja"),
        ]:
            lang_menu.add_radiobutton(
                label    = label,
                variable = self._lang_var,
                value    = value,
                command  = lambda v=value: self._on_lang_select(v),
            )
        settings_menu.add_cascade(label=i18n.t("menu_language"), menu=lang_menu)

        coord_fmt_menu = tk.Menu(settings_menu, tearoff=False)
        self._themed_menus.append(coord_fmt_menu)
        self._coord_fmt_var = tk.StringVar(value=self.config.get("coord_format", "dd"))
        for value in ("dd", "dms"):
            coord_fmt_menu.add_radiobutton(
                label    = i18n.t(f"coord_fmt_{value}"),
                variable = self._coord_fmt_var,
                value    = value,
                command  = self._on_coord_format_change,
            )
        settings_menu.add_cascade(label=i18n.t("lbl_coord_format"), menu=coord_fmt_menu)

        settings_menu.add_separator()
        settings_menu.add_command(
            label   = i18n.t("menu_proxy_settings"),
            command = self._on_proxy_settings,
        )
        settings_menu.add_command(
            label   = i18n.t("menu_load_app_settings"),
            command = self._on_load_app_settings,
        )
        settings_menu.add_separator()
        settings_menu.add_command(
            label   = i18n.t("menu_delete_all_cache"),
            command = self._on_delete_all_cache,
        )
        menubar.add_cascade(label=i18n.t("menu_settings"), menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        self._themed_menus.append(help_menu)
        help_menu.add_command(
            label   = i18n.t("menu_open_readme"),
            command = self._on_open_readme,
        )
        help_menu.add_separator()
        help_menu.add_command(
            label   = i18n.t("menu_about"),
            command = self._on_about,
        )
        menubar.add_cascade(label=i18n.t("menu_help"), menu=help_menu)

        self.root.configure(menu=menubar)

        # 現在テーマを即反映し、以降のテーマ変更は <<ThemeChanged>> で拾う。これで
        # メニューからの明示切替も、system 連動（darkdetect の OS 追従）も一括対応。
        self._apply_menu_theme()
        self.root.bind("<<ThemeChanged>>", self._apply_menu_theme, add="+")

    def _apply_menu_theme(self, _event: object = None) -> None:
        """tk.Menu へ現在の ttk テーマ色を明示適用する（B-004）。

        sv_ttk は ttk ウィジェットのみ再スタイルし、ネイティブ tk.Menu は追従しない。
        特に選択インジケータ色（selectcolor＝ラジオ/チェックの「✓」）は既定のまま
        だとダーク背景と同化して選択中が判別できない。前景色へ揃えて視認可能にする。
        """
        style = ttk.Style()
        bg = style.lookup("TFrame", "background")
        fg = style.lookup("TLabel", "foreground")
        active_bg = style.lookup("Accent.TButton", "background") or bg
        opts: dict[str, str] = {}
        if bg:
            opts["background"] = bg
            opts["activebackground"] = active_bg or bg
        if fg:
            opts["foreground"] = fg
            opts["activeforeground"] = fg
            opts["selectcolor"] = fg   # 「✓」を前景色にしてダークでも見えるように
        if not opts:
            return
        for menu in self._themed_menus:
            try:
                menu.configure(**opts)
            except tk.TclError:
                pass

    def _on_theme_select(self, mode: str) -> None:
        self.config["theme"] = mode
        self._on_theme(mode)
        config.save_app(self.config)

    def _on_lang_select(self, lang: str) -> None:
        self.config["lang"] = lang
        config.save_app(self.config)
        self._alert(i18n.t("lang_changed_title"), i18n.t("lang_changed_msg"))

    def _on_proxy_settings(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.transient(self.root)
        dlg.title(i18n.t("dlg_proxy_title"))
        dlg.resizable(False, False)
        dlg.grab_set()

        ttk.Label(dlg, text=i18n.t("dlg_proxy_url_label")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(16, 4),
        )

        url_var = tk.StringVar(value=self.config.get("proxy_url", ""))
        entry = ttk.Entry(dlg, textvariable=url_var, width=46)
        entry.grid(row=1, column=0, columnspan=3, padx=16, pady=(0, 4))

        ttk.Label(dlg, text=i18n.t("dlg_proxy_hint"), foreground="gray").grid(
            row=2, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 12),
        )

        def _on_clear() -> None:
            url_var.set("")
            entry.focus_set()

        def _on_ok() -> None:
            url = url_var.get().strip()
            self.config["proxy_url"] = url
            config.save_app(self.config)
            dem.set_proxy(url)
            sim.clear_terrain_cache()
            dlg.destroy()

        ttk.Button(dlg, text=i18n.t("btn_clear"),  command=_on_clear).grid(
            row=3, column=0, padx=(16, 4), pady=(0, 16), sticky="e",
        )
        ttk.Button(dlg, text=i18n.t("btn_cancel"), command=dlg.destroy).grid(
            row=3, column=1, padx=4, pady=(0, 16),
        )
        ttk.Button(dlg, text=i18n.t("dlg_ok"), style="Accent.TButton", command=_on_ok).grid(
            row=3, column=2, padx=(4, 16), pady=(0, 16), sticky="w",
        )
        dlg.columnconfigure(0, weight=1)
        dlg.columnconfigure(1, weight=0)
        dlg.columnconfigure(2, weight=1)

        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width()  - dlg.winfo_width())  // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")

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
            f_env, text=i18n.t("lbl_env_type"), width=22, anchor="w", font=("Arial", 9)
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
            font         = ("Arial", 9),
            width        = 16,
        ).pack(side="right", expand=True, fill="x")

        # 降雨強度（方針A: 従来はランチャー欄が無く config 補完だった）
        self._add_row(g, i18n.t("lbl_rain"), "rain_rate")

        # 回折モデル Combobox（方針A: env_type と同じ readonly 選択式）
        f_diff = ttk.Frame(g)
        f_diff.pack(fill="x", pady=2, padx=10)
        ttk.Label(
            f_diff, text=i18n.t("lbl_diff_method"), width=22, anchor="w",
            font=("Arial", 9),
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
            font         = ("Arial", 9),
            width        = 16,
        )
        cb_diff.pack(side="right", expand=True, fill="x")
        _Tooltip(cb_diff, i18n.t("tip_diff_method"))

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
        ttk.Label(f_proj, text=i18n.t("batch_project_name"), width=22, anchor="w",
                  font=("Arial", 9)).pack(side="left")
        ttk.Entry(f_proj, textvariable=self._project_var, font=("Arial", 9)).pack(
            side="right", expand=True, fill="x")

        f_memo = ttk.Frame(g)
        f_memo.pack(fill="x", pady=2, padx=10)
        ttk.Label(f_memo, text=i18n.t("batch_memo"), width=22, anchor="w",
                  font=("Arial", 9)).pack(side="left")
        ttk.Entry(f_memo, textvariable=self._memo_var, font=("Arial", 9)).pack(
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
        self.prog_label = ttk.Label(parent, text=i18n.t("status_ready"), font=("Arial", 9))
        self.prog_label.pack(pady=(10, 0))

        self.prog_bar = ttk.Progressbar(
            parent, orient="horizontal", length=350, mode="determinate"
        )
        self.prog_bar.pack(pady=5, fill="x")

    def _build_buttons(self, parent: tk.Widget) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=10)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        self.run_btn = ttk.Button(frame, text=i18n.t("btn_run_sim"),       command=self._on_run,            style="Accent.TButton")
        btn_batch    = ttk.Button(frame, text=i18n.t("btn_batch_mode"),    command=self._on_batch,          style="Accent.TButton")
        btn_load     = ttk.Button(frame, text=i18n.t("btn_load_settings"), command=self._on_load_settings)
        btn_open     = ttk.Button(frame, text=i18n.t("btn_open_results"),  command=self._on_open_results)
        btn_map      = ttk.Button(frame, text=i18n.t("btn_open_map"),      command=self._on_open_map)

        self.run_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2), pady=(0, 4), ipady=10)
        btn_batch.grid   (row=0, column=1, sticky="ew", padx=(2, 0), pady=(0, 4), ipady=10)
        btn_load.grid    (row=1, column=0, sticky="ew", padx=(0, 2),               ipady=6)
        btn_open.grid    (row=1, column=1, sticky="ew", padx=(2, 0),               ipady=6)
        btn_map.grid     (row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0),  ipady=6)

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
            max_w = 460
            w, h = img.size
            if w > max_w:
                h = int(h * max_w / w)
                w = max_w
                img = img.resize((w, h), Image.Resampling.LANCZOS)
            self._logo_image = ImageTk.PhotoImage(img)
            tk.Label(parent, image=self._logo_image).pack(side="bottom", pady=(4, 8))
        except Exception as e:
            config.logger.warning("Logo load failed: %s", e)

    def _add_row(self, parent: tk.Widget, label: str, key: str) -> None:
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=2, padx=10)
        ttk.Label(
            f, text=label, width=22, anchor="w", font=("Arial", 9)
        ).pack(side="left")
        e = tk.Entry(f, font=("Arial", 9))
        e.insert(0, self.config[key])
        e.pack(side="right", expand=True, fill="x")
        self.entries[key] = e
        if key in _TIP_KEYS:
            _Tooltip(e, i18n.t(_TIP_KEYS[key]))

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
        mode = self._coord_fmt_var.get()
        for key in ("start", "end"):
            entry = self.entries.get(key)
            if entry is None:
                continue
            new_text = coords.reformat(entry.get(), mode)
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

    def _notify_map_cache_change(self) -> None:
        """シミュレーションのプリフェッチでキャッシュが増えた後、開いている
        マップウィンドウの統計・カバレッジ表示を更新する。"""
        if hasattr(self, "_map_win") and self._map_win._win.winfo_exists():
            self._map_win.on_external_cache_change()

    def _on_delete_all_cache(self) -> None:
        """全 DEM/地図タイルキャッシュを削除する（設定メニューから実行）。"""
        if not self._confirm(
            i18n.t("tm_delete_all_title"), i18n.t("tm_delete_all_confirm")
        ):
            return
        result = dem.delete_all_tile_cache()
        # マップウィンドウが開いていれば表示を更新する。
        if hasattr(self, "_map_win") and self._map_win._win.winfo_exists():
            self._map_win.on_external_delete_all(result["deleted"])
        else:
            self._alert(
                i18n.t("tm_delete_all_title"),
                i18n.t("tm_delete_all_done").format(deleted=result["deleted"]),
            )

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
        self.run_btn.config(state="disabled")

        # Phase 1: bbox 内の DEM タイルを事前取得
        tile_count = dem.count_bbox_tiles(
            params.lat_tx, params.lon_tx,
            params.lat_rx, params.lon_rx,
        )
        self.prog_bar.config(maximum=max(tile_count, 1), value=0)
        self.prog_label.config(text=i18n.t("status_prefetch"))

        def _prefetch_progress(done: int, total: int) -> None:
            pct = int(done / total * 100)
            def _update(done: int = done, pct: int = pct) -> None:
                self.prog_bar.config(value=done)
                self.prog_label.config(
                    text=i18n.t("status_prefetch_pct").format(pct=pct)
                )
            self.root.after(0, _update)

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
        self.prog_bar.config(maximum=params.num, value=0)
        self.prog_label.config(text=i18n.t("status_fetching"))

        def _on_progress(v: int) -> None:
            pct = int(v / params.num * 100)
            def _update(v: int = v, pct: int = pct) -> None:
                self.prog_bar.config(value=v)
                self.prog_label.config(
                    text=i18n.t("status_fetching_pct").format(pct=pct)
                )
            self.root.after(0, _update)

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
        self.run_btn.config(state="normal")
        self.prog_label.config(text=i18n.t("status_ready"))
        self.prog_bar.config(value=0)
        # matplotlib/pyplot/TkAgg/numpy はここで初めて要る（ランチャー表示前に
        # ロードしないため遅延 import。MapWindow/BatchBuilder と同じ方針）
        from views.graph import show_graph
        meta = self._current_meta()
        show_graph(params, raw_elevs, meta["project_name"], meta["memo"])

    def _on_fetch_error(self, ex: Exception) -> None:
        self._alert(i18n.t("dlg_error"), str(ex))
        self.run_btn.config(state="normal")
        self.prog_label.config(text=i18n.t("status_ready"))
        self.prog_bar.config(value=0)

    def _on_load_settings(self) -> None:
        file_path = filedialog.askopenfilename(
            initialdir = config.RESULTS_DIR,
            title      = i18n.t("dlg_select_settings"),
            filetypes  = [("JSON files", "*.json")],
            parent     = self.root,
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                new_conf = json.load(f)
            # sim キーのみ取り込む（app 設定 theme/lang/proxy_url は無視する）。
            new_conf = config.select_sim(new_conf)
            for k, v in new_conf.items():
                if k in self.entries:
                    self.entries[k].delete(0, tk.END)
                    self.entries[k].insert(0, str(v))
            if "env_type" in new_conf:
                label = self._env_key_to_label.get(
                    new_conf["env_type"], self._env_key_to_label["suburban"]
                )
                self._env_var.set(label)
            if "diff_method" in new_conf:
                self._diff_var.set(
                    self._diff_key_to_label.get(
                        str(new_conf["diff_method"]),
                        self._diff_key_to_label["deygout"],
                    )
                )
            # ファイルの座標は DD。現在の表示形式（DMS かも）へ整形し直す。
            self._refresh_coord_display()
            self._alert(i18n.t("dlg_success"), i18n.t("dlg_settings_ok"))
        except Exception as e:
            self._alert(i18n.t("dlg_error"), str(e))

    def _on_load_app_settings(self) -> None:
        """ファイルから app 設定（theme/lang/proxy_url）のみ取り込む。

        sim パラメータは無視する（select_app）。settings.json を読んでも
        シミュレーション条件は変わらない。_on_load_settings と対称。
        """
        file_path = filedialog.askopenfilename(
            title     = i18n.t("dlg_select_app_settings"),
            filetypes = [("JSON files", "*.json")],
            parent    = self.root,
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                app = config.select_app(json.load(f))
            # 実際に1つでも app 設定を適用したか。未適用なら成功表示しない。
            applied = False
            # 不正値は無視して安全に適用する（テーマ/言語は既知値のみ）。
            if app.get("theme") in ("system", "light", "dark"):
                self.config["theme"] = app["theme"]
                self._theme_var.set(app["theme"])
                self._on_theme(app["theme"])
                applied = True
            if "proxy_url" in app:
                self.config["proxy_url"] = str(app["proxy_url"])
                dem.set_proxy(self.config["proxy_url"])
                sim.clear_terrain_cache()
                applied = True
            lang_changed = app.get("lang") in ("en", "ja") and \
                app["lang"] != self.config.get("lang")
            if app.get("lang") in ("en", "ja"):
                self.config["lang"] = app["lang"]
                self._lang_var.set(app["lang"])
                applied = True

            if not applied:
                # ファイルに有効な app 設定がない＝何も取り込んでいない。誤解を招く
                # 成功表示を避け、その旨を伝える（save_app も呼ばない）。
                self._alert(i18n.t("dlg_app_settings_none_title"),
                            i18n.t("dlg_app_settings_none"))
                return
            config.save_app(self.config)
            if lang_changed:
                self._alert(i18n.t("lang_changed_title"), i18n.t("lang_changed_msg"))
            else:
                self._alert(i18n.t("dlg_success"), i18n.t("dlg_app_settings_ok"))
        except Exception as e:
            self._alert(i18n.t("dlg_error"), str(e))

    def _on_open_results(self) -> None:
        if os.path.exists(config.RESULTS_DIR):
            os.startfile(config.RESULTS_DIR)

    def _current_config(self) -> dict[str, str]:
        """現在のエントリ値を config dict として返す（バリデーションなし）。"""
        c = {k: self.entries[k].get() for k in self.entries}
        c["env_type"]    = self._env_label_to_key.get(self._env_var.get(), "los")
        c["diff_method"] = self._diff_label_to_key.get(self._diff_var.get(), "deygout")
        self._coords_to_dd(c)
        return c

    def _on_open_map(self) -> None:
        from views.map_window import MapWindow
        if hasattr(self, "_map_win") and self._map_win._win.winfo_exists():
            self._map_win._win.focus()
            return
        # 地図はアプリ唯一のインスタンス（ランチャー所有）。座標入力＝ランチャーへの
        # 単一書き戻し（single_sink=self）、連続追加＝バッチへ append（append_provider
        # がバッチを開いて受け皿を返す）。バッチからは地図を開かない（本筋はランチャー）。
        self._map_win = MapWindow(
            self.root, self.config,
            single_sink=self,
            append_provider=self._open_batch_for_append,
        )

    def _open_batch_for_append(self):
        """連続追加モードの append 先としてバッチウィンドウを開いて返す。"""
        return self.ensure_batch_window()

    # ----------------------------------------------------------
    # マップウィンドウ（座標入力モード）との連携
    # 数値欄が常に source of truth。地図はピッカーとして書き戻すだけ。
    # ----------------------------------------------------------
    def apply_map_pick(self, role: str, lat: float, lon: float) -> None:
        """地図でピックした TX/RX 座標を対応する数値欄へ書き戻す。

        role は "tx"（start 欄）/ "rx"（end 欄）。形式は既存の "lat, lon"。
        """
        key = "start" if role == "tx" else "end"
        entry = self.entries.get(key)
        if entry is None:
            return
        text = coords.format_pair(lat, lon, self._coord_fmt_var.get())
        entry.delete(0, tk.END)
        entry.insert(0, text)

    def current_path_coords(self) -> dict:
        """数値欄の TX/RX 座標を {"tx": (lat, lon)|None, "rx": ...} で返す。

        マップウィンドウが開いた時点で既存座標のマーカーを表示するために使う。
        パースできない欄は None（地図側は無視する）。
        """
        def _parse(key: str):
            try:
                return coords.parse_pair(self.entries[key].get())
            except (ValueError, KeyError):
                return None
        return {"tx": _parse("start"), "rx": _parse("end")}

    def _on_about(self) -> None:
        self._alert(
            i18n.t("menu_about"),
            i18n.t("dlg_about_msg").format(
                app  = version.APP_NAME,
                ver  = version.APP_VERSION,
                copy = version.COPYRIGHT,
            ),
        )

    def _on_open_readme(self) -> None:
        import sys
        base = getattr(
            sys, "_MEIPASS",
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        key  = "readme_binary_filename" if getattr(sys, "frozen", False) else "readme_filename"
        path = os.path.join(base, i18n.t(key))
        if not os.path.exists(path):
            self._alert(i18n.t("menu_help"), i18n.t("dlg_readme_missing"))
            return

        # Tier 1: markdown ライブラリで HTML 変換 → ブラウザ表示
        try:
            import atexit
            import tempfile
            import unicodedata as _ud
            import webbrowser
            import markdown as _md
            from markdown.extensions.toc import TocExtension
            from pathlib import Path

            def _slugify(value: str, sep: str) -> str:
                """GitHub 互換のアンカー ID 生成。文字・数字は保持し句読点は除去。"""
                result = []
                for ch in value.lower():
                    cat = _ud.category(ch)
                    if cat.startswith("L") or cat.startswith("N") or cat == "Pc":
                        result.append(ch)
                    elif ch in (" ", "\t"):
                        result.append(sep)
                return "".join(result)

            with open(path, encoding="utf-8") as f:
                body = _md.markdown(
                    f.read(),
                    extensions=["tables", "fenced_code", TocExtension(slugify=_slugify)],
                )
            # logo.png を base64 に変換して埋め込む（<base href> 不要・アンカーリンク保護）
            import base64 as _b64
            logo_path = os.path.join(base, "logo.png")
            if os.path.exists(logo_path):
                with open(logo_path, "rb") as _f:
                    _b64str = _b64.b64encode(_f.read()).decode("ascii")
                body = body.replace(
                    'src="logo.png"',
                    f'src="data:image/png;base64,{_b64str}"',
                )
            html = (
                f'<!DOCTYPE html><html lang="{i18n.t("html_lang")}">'
                '<head><meta charset="UTF-8"><style>'
                "body{font-family:Arial,sans-serif;margin:40px;max-width:900px;line-height:1.6}"
                "h1,h2,h3{color:#333}"
                "code{font-family:'BIZ UDGothic','MS Gothic',Consolas,'Courier New',monospace;"
                "background:#f4f4f4;padding:2px 6px;border-radius:3px}"
                "pre{font-family:'BIZ UDGothic','MS Gothic',Consolas,'Courier New',monospace;"
                "background:#f4f4f4;padding:12px;border-radius:4px;overflow-x:auto;line-height:1.5}"
                "img{width:100%;height:auto}"
                "table{border-collapse:collapse;width:100%}"
                "th,td{border:1px solid #ddd;padding:8px;text-align:left}"
                "th{background:#455a64;color:white}"
                f"</style></head><body>{body}</body></html>"
            )
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".html", encoding="utf-8", delete=False
            ) as tmp:
                tmp.write(html)
                tmp_path = tmp.name
            atexit.register(lambda p=tmp_path: os.unlink(p) if os.path.exists(p) else None)
            webbrowser.open(Path(tmp_path).as_uri())
            return
        except ImportError:
            pass
        except Exception as ex:
            config.logger.warning("README markdown render failed: %s", ex)

        # Tier 2: OS デフォルトアプリで .md を直接開く
        try:
            os.startfile(path)
            return
        except Exception:
            pass

        # Tier 3: アプリ内テキストビューア
        self._show_readme_text(path)

    def _show_readme_text(self, path: str) -> None:
        from tkinter.scrolledtext import ScrolledText
        win = tk.Toplevel(self.root)
        win.transient(self.root)
        win.title("README")
        win.geometry("800x600")
        win.minsize(500, 400)
        st = ScrolledText(win, font=("Courier New", 10), wrap="word")
        st.pack(fill="both", expand=True, padx=10, pady=10)
        with open(path, encoding="utf-8") as f:
            st.insert("1.0", f.read())
        st.config(state="disabled")

    def _on_batch(self) -> None:
        """Batch Builder ウィンドウを開く（既に開いていれば前面化）。"""
        self.ensure_batch_window()

    def ensure_batch_window(self):
        """バッチウィンドウを開いて返す（唯一インスタンス。開いていれば前面化）。

        ランチャーの現在値を初期値として引き継ぐ。config_provider / load_params を
        注入し、バッチ各行を「ランチャー（source of truth）のスナップショット」として
        凍結できるようにする（Phase D1）。地図の連続追加モードの append 先も兼ねる。
        """
        win = getattr(self, "_batch_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return win
        from views.batch_builder import BatchBuilderWindow
        try:
            params = sim.SimParams(self._current_config())
        except Exception:
            params = sim.SimParams(config.DEFAULT_CONFIG)
        self._batch_win = BatchBuilderWindow(
            self.root, params,
            config_provider=self._current_config,
            meta_provider=self._current_meta,
            load_params=self.load_batch_row,
            on_close=self._on_batch_closed,
            on_paths_changed=self._notify_map_paths_changed,
        )
        return self._batch_win

    def _on_batch_closed(self) -> None:
        """バッチが閉じたとき: 参照を手放し、地図が連続追加中なら座標入力へ戻させる。"""
        self._batch_win = None
        if hasattr(self, "_map_win") and self._map_win._win.winfo_exists():
            self._map_win.on_append_target_closed()

    def _notify_map_paths_changed(self) -> None:
        """バッチの行が変わったとき、地図の確定パス表示を追従させる。"""
        if hasattr(self, "_map_win") and self._map_win._win.winfo_exists():
            self._map_win.on_paths_changed()

    def load_batch_row(self, row: dict) -> None:
        """バッチ行（座標＋RF）をランチャーの数値欄へロードする（→シングルへ送る）。

        座標は現在の coord_format 表記へ整形して start/end 欄へ、RF/h は対応 Entry へ
        書き込む。空欄の項目は据え置く。ランチャーを前面化する。
        """
        fmt = self._coord_fmt_var.get()
        for key, val in row.items():
            entry = self.entries.get(key)
            if entry is None or val in (None, ""):
                continue
            text = coords.reformat(val, fmt) if key in ("start", "end") else str(val)
            entry.delete(0, tk.END)
            entry.insert(0, text)
        self.root.lift()
        self.root.focus_force()
