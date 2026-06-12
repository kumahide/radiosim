"""
views/launcher.py
=================
入力フォームウィンドウ（SimLauncher）。

計算・通信・ファイル I/O は一切行わない。
simulation モジュールと infrastructure モジュールを呼ぶだけ。
"""

import json
import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable

import i18n
import infrastructure as infra
import simulation as sim
import version
from models import ENV_DEFAULT, ENV_LABELS
from views.graph import show_graph

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
        root.geometry("450x820")
        root.resizable(False, False)

        self.config    = infra.load_config()
        infra.set_proxy(self.config.get("proxy_url", ""))
        self.entries:  dict[str, tk.Entry] = {}
        self._on_theme = on_theme

        self._build_ui()

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
        self._build_status(container)
        self._build_buttons(container)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        settings_menu = tk.Menu(menubar, tearoff=False)

        theme_menu = tk.Menu(settings_menu, tearoff=False)
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

        settings_menu.add_separator()
        settings_menu.add_command(
            label   = i18n.t("menu_proxy_settings"),
            command = self._on_proxy_settings,
        )
        settings_menu.add_separator()
        settings_menu.add_command(
            label   = i18n.t("menu_tile_manager"),
            command = self._on_tile_manager,
        )
        settings_menu.add_command(
            label   = i18n.t("menu_delete_all_cache"),
            command = self._on_delete_all_cache,
        )
        menubar.add_cascade(label=i18n.t("menu_settings"), menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
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

    def _on_theme_select(self, mode: str) -> None:
        self.config["theme"] = mode
        self._on_theme(mode)
        infra.save_config(self.config)

    def _on_lang_select(self, lang: str) -> None:
        self.config["lang"] = lang
        infra.save_config(self.config)
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
            infra.save_config(self.config)
            infra.set_proxy(url)
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
        env_labels    = list(ENV_LABELS.keys())
        _key_to_label = {v: k for k, v in ENV_LABELS.items()}
        saved_key     = self.config.get("env_type", ENV_DEFAULT)
        saved_label   = _key_to_label.get(saved_key, "Suburban")

        self._env_var = tk.StringVar(value=saved_label)
        ttk.Combobox(
            f_env,
            textvariable = self._env_var,
            values       = env_labels,
            state        = "readonly",
            font         = ("Arial", 9),
            width        = 16,
        ).pack(side="right", expand=True, fill="x")

        for lbl_key, entry_key in [
            ("lbl_veg_h",    "veg_h"),
            ("lbl_k_factor", "k_factor"),
            ("lbl_samples",  "samples"),
        ]:
            self._add_row(g, i18n.t(lbl_key), entry_key)

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

        self.run_btn.grid(row=0, column=0, sticky="ew", padx=(0, 2), pady=(0, 4), ipady=10)
        btn_batch.grid   (row=0, column=1, sticky="ew", padx=(2, 0), pady=(0, 4), ipady=10)
        btn_load.grid    (row=1, column=0, sticky="ew", padx=(0, 2),               ipady=6)
        btn_open.grid    (row=1, column=1, sticky="ew", padx=(2, 0),               ipady=6)

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
            infra.logger.warning("Logo load failed: %s", e)

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
    # ダイアログ位置制御
    # ----------------------------------------------------------
    def _alert(self, title: str, message: str) -> None:
        """ランチャー中央にモーダルダイアログを表示する。
        messagebox はWindowsネイティブAPIで位置を制御できないため
        tk.Toplevel で実装し geometry() で明示的に中央配置する。"""
        dlg = tk.Toplevel(self.root)
        dlg.transient(self.root)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.grab_set()

        ttk.Label(
            dlg, text=message, wraplength=340, justify="left", padding=(20, 16, 20, 12)
        ).pack()
        ttk.Button(dlg, text=i18n.t("dlg_ok"), command=dlg.destroy).pack(pady=(0, 12))

        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width()  - dlg.winfo_width())  // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")
        dlg.wait_window()

    def _confirm(self, title: str, message: str) -> bool:
        """ランチャー中央に Yes/No 確認ダイアログを表示し、Yes なら True を返す。"""
        dlg = tk.Toplevel(self.root)
        dlg.transient(self.root)
        dlg.title(title)
        dlg.resizable(False, False)
        dlg.grab_set()

        result = {"ok": False}
        ttk.Label(
            dlg, text=message, wraplength=340, justify="left", padding=(20, 16, 20, 12)
        ).pack()
        btns = ttk.Frame(dlg)
        btns.pack(pady=(0, 12))

        def _yes() -> None:
            result["ok"] = True
            dlg.destroy()

        ttk.Button(btns, text=i18n.t("dlg_yes"), command=_yes).pack(side="left", padx=6)
        ttk.Button(btns, text=i18n.t("dlg_no"), command=dlg.destroy).pack(side="left", padx=6)

        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width()  - dlg.winfo_width())  // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")
        dlg.wait_window()
        return result["ok"]

    def _on_delete_all_cache(self) -> None:
        """全 DEM/地図タイルキャッシュを削除する（設定メニューから実行）。"""
        if not self._confirm(
            i18n.t("tm_delete_all_title"), i18n.t("tm_delete_all_confirm")
        ):
            return
        result = infra.delete_all_tile_cache()
        # タイル管理ウィンドウが開いていれば表示を更新する。
        if hasattr(self, "_tile_mgr_win") and self._tile_mgr_win._win.winfo_exists():
            self._tile_mgr_win.on_external_delete_all(result["deleted"])
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
        c["env_type"] = ENV_LABELS.get(self._env_var.get(), "suburban")
        # rain_rate と diff_method は UI に Entry がないため config から補完する
        c.setdefault("rain_rate",   self.config.get("rain_rate",   "0.0"))
        c.setdefault("diff_method", self.config.get("diff_method", "deygout"))

        errors = infra.validate_config(c)
        if errors:
            self._alert(i18n.t("dlg_input_error"), "\n".join(errors))
            infra.logger.warning("Validation failed: %s", errors)
            return

        try:
            params = sim.SimParams(c)
        except Exception as ex:
            self._alert(i18n.t("dlg_error"), str(ex))
            return

        c["theme"]     = self.config.get("theme",     "system")
        c["lang"]      = self.config.get("lang",       "en")
        c["proxy_url"] = self.config.get("proxy_url",  "")
        infra.save_config(c)
        self.run_btn.config(state="disabled")

        # Phase 1: bbox 内の DEM タイルを事前取得
        tile_count = infra.count_bbox_tiles(
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
                infra.prefetch_tiles(
                    params.lat_tx, params.lon_tx,
                    params.lat_rx, params.lon_rx,
                    progress_cb=_prefetch_progress,
                )
            except Exception as ex:
                infra.logger.warning("Prefetch error (continuing): %s", ex)
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
        show_graph(params, raw_elevs)

    def _on_fetch_error(self, ex: Exception) -> None:
        self._alert(i18n.t("dlg_error"), str(ex))
        self.run_btn.config(state="normal")
        self.prog_label.config(text=i18n.t("status_ready"))

    def _on_load_settings(self) -> None:
        file_path = filedialog.askopenfilename(
            initialdir = infra.RESULTS_DIR,
            title      = i18n.t("dlg_select_settings"),
            filetypes  = [("JSON files", "*.json")],
            parent     = self.root,
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                new_conf = json.load(f)
            for k, v in new_conf.items():
                if k in self.entries:
                    self.entries[k].delete(0, tk.END)
                    self.entries[k].insert(0, str(v))
            if "env_type" in new_conf:
                _key_to_label = {v: k for k, v in ENV_LABELS.items()}
                label = _key_to_label.get(new_conf["env_type"], "Suburban")
                self._env_var.set(label)
            for key in ("rain_rate", "diff_method"):
                if key in new_conf:
                    self.config[key] = str(new_conf[key])
            self._alert(i18n.t("dlg_success"), i18n.t("dlg_settings_ok"))
        except Exception as e:
            self._alert(i18n.t("dlg_error"), str(e))

    def _on_open_results(self) -> None:
        if os.path.exists(infra.RESULTS_DIR):
            os.startfile(infra.RESULTS_DIR)

    def _current_config(self) -> dict[str, str]:
        """現在のエントリ値を config dict として返す（バリデーションなし）。"""
        c = {k: self.entries[k].get() for k in self.entries}
        c["env_type"]    = ENV_LABELS.get(self._env_var.get(), "los")
        c["rain_rate"]   = self.config.get("rain_rate",   "0.0")
        c["diff_method"] = self.config.get("diff_method", "deygout")
        return c

    def _on_tile_manager(self) -> None:
        from views.tile_manager import TileManagerWindow
        if hasattr(self, "_tile_mgr_win") and self._tile_mgr_win._win.winfo_exists():
            self._tile_mgr_win._win.focus()
            return
        self._tile_mgr_win = TileManagerWindow(self.root, self.config)

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
            infra.logger.warning("README markdown render failed: %s", ex)

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
        """Batch Builder ウィンドウを開く。ランチャーの現在値を初期値として引き継ぐ。"""
        from views.batch_builder import BatchBuilderWindow
        try:
            params = sim.SimParams(self._current_config())
        except Exception:
            params = sim.SimParams(infra.DEFAULT_CONFIG)
        BatchBuilderWindow(self.root, params)
