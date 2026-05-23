"""
views/launcher.py
=================
入力フォームウィンドウ（SimLauncher）。

計算・通信・ファイル I/O は一切行わない。
simulation モジュールと infrastructure モジュールを呼ぶだけ。
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import infrastructure as infra
import simulation as sim
from views.graph import show_graph


class SimLauncher:
    """メインウィンドウ：入力フォーム・進捗バー・実行ボタン。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Radio Sim Pro R1.0")
        root.geometry("450x820")
        root.resizable(False, False)

        self.config  = infra.load_config()
        self.entries: dict[str, tk.Entry] = {}

        self._build_ui()

    # ----------------------------------------------------------
    # UI 構築
    # ----------------------------------------------------------
    def _build_ui(self) -> None:
        container = tk.Frame(self.root, padx=20, pady=10)
        container.pack(fill="both", expand=True)

        self._build_site_group(container)
        self._build_radio_group(container)
        self._build_env_group(container)
        self._build_status(container)
        self._build_buttons(container)

        tk.Label(
            self.root,
            text="© 2026 BearValley Corp. All rights reserved.",
            fg="gray",
            font=("Arial", 8),
        ).pack(side="bottom", pady=10)

    def _build_site_group(self, parent: tk.Frame) -> None:
        g = tk.LabelFrame(parent, text=" Site Info ", fg="blue", pady=5)
        g.pack(fill="x", pady=5)
        for label, key in [
            ("Start Coords (Lat, Lon)", "start"),
            ("End Coords (Lat, Lon)",   "end"),
            ("TX Antenna Height (m)",   "h_tx"),
            ("RX Antenna Height (m)",   "h_rx"),
        ]:
            self._add_row(g, label, key)

    def _build_radio_group(self, parent: tk.Frame) -> None:
        g = tk.LabelFrame(parent, text=" Radio Settings ", fg="darkgreen", pady=5)
        g.pack(fill="x", pady=5)
        for label, key in [
            ("Frequency (MHz)",     "freq"),
            ("TX Power (dBm)",      "p_tx"),
            ("TX Antenna Gain (dBi)", "gain_tx"),
            ("RX Antenna Gain (dBi)", "gain_rx"),
            ("Sensitivity (dBm)",   "sens"),
        ]:
            self._add_row(g, label, key)

    def _build_env_group(self, parent: tk.Frame) -> None:
        g = tk.LabelFrame(parent, text=" Environment ", fg="brown", pady=5)
        g.pack(fill="x", pady=5)

        # 環境区分 Combobox（Entry ではなく選択式）
        f_env = tk.Frame(g)
        f_env.pack(fill="x", pady=2, padx=10)
        tk.Label(
            f_env, text="Env Type", width=22, anchor="w", font=("Arial", 9)
        ).pack(side="left")
        from models import ENV_LABELS, ENV_DEFAULT
        env_labels   = list(ENV_LABELS.keys())           # ["Urban","Suburban","Rural","LoS"]
        # 保存値（キー）からラベルへの逆引き
        _key_to_label = {v: k for k, v in ENV_LABELS.items()}
        saved_key     = self.config.get("env_type", ENV_DEFAULT)
        saved_label   = _key_to_label.get(saved_key, "Suburban")

        self._env_var = tk.StringVar(value=saved_label)
        cb = ttk.Combobox(
            f_env,
            textvariable = self._env_var,
            values       = env_labels,
            state        = "readonly",
            font         = ("Arial", 9),
            width        = 16,
        )
        cb.pack(side="right", expand=True, fill="x")

        for label, key in [
            ("Vegetation Height (m)", "veg_h"),
            ("Initial K-Factor (dB)", "k_factor"),
            ("Sampling Points",       "samples"),
        ]:
            self._add_row(g, label, key)

    def _build_status(self, parent: tk.Frame) -> None:
        self.prog_label = tk.Label(parent, text="Ready", font=("Arial", 9))
        self.prog_label.pack(pady=(10, 0))

        self.prog_bar = ttk.Progressbar(
            parent, orient="horizontal", length=350, mode="determinate"
        )
        self.prog_bar.pack(pady=5, fill="x")

    def _build_buttons(self, parent: tk.Frame) -> None:
        self.run_btn = tk.Button(
            parent,
            text="RUN SIMULATION",
            command=self._on_run,
            bg="#2196F3",
            fg="white",
            font=("Arial", 12, "bold"),
            height=2,
        )
        self.run_btn.pack(pady=10, fill="x")

        f2 = tk.Frame(parent)
        f2.pack(fill="x")
        tk.Button(
            f2,
            text="LOAD SETTINGS",
            command=self._on_load_settings,
            bg="#FF9800",
            fg="white",
        ).pack(side="left", expand=True, fill="x", padx=(0, 2))
        tk.Button(
            f2,
            text="OPEN RESULTS",
            command=self._on_open_results,
            bg="#607D8B",
            fg="white",
        ).pack(side="left", expand=True, fill="x", padx=(2, 0))

    def _add_row(self, parent: tk.Frame, label: str, key: str) -> None:
        f = tk.Frame(parent)
        f.pack(fill="x", pady=2, padx=10)
        tk.Label(
            f, text=label, width=22, anchor="w", font=("Arial", 9)
        ).pack(side="left")
        e = tk.Entry(f, font=("Arial", 9))
        e.insert(0, self.config[key])
        e.pack(side="right", expand=True, fill="x")
        self.entries[key] = e

    # ----------------------------------------------------------
    # イベントハンドラ
    # ----------------------------------------------------------
    def _on_run(self) -> None:
        from models import ENV_LABELS
        c = {k: self.entries[k].get() for k in self.entries}
        c["env_type"] = ENV_LABELS.get(self._env_var.get(), "suburban")

        errors = infra.validate_config(c)
        if errors:
            messagebox.showerror("Input Error", "\n".join(errors))
            infra.logger.warning("Validation failed: %s", errors)
            return

        try:
            params = sim.SimParams(c)
        except Exception as ex:
            messagebox.showerror("Error", str(ex))
            return

        infra.save_config(c)
        self.run_btn.config(state="disabled")
        self.prog_bar.config(maximum=params.num, value=0)
        self.prog_label.config(text="Fetching terrain…")

        sim.fetch_elevations(
            params      = params,
            on_progress = lambda v: self.root.after(
                0, lambda v=v: self.prog_bar.config(value=v)
            ),
            on_complete = lambda elevs: self.root.after(
                0, lambda: self._on_fetch_complete(params, elevs)
            ),
            on_error    = lambda ex: self.root.after(
                0, lambda: self._on_fetch_error(ex)
            ),
        )

    def _on_fetch_complete(self, params: sim.SimParams, raw_elevs) -> None:
        self.run_btn.config(state="normal")
        self.prog_label.config(text="Ready")
        show_graph(params, raw_elevs)

    def _on_fetch_error(self, ex: Exception) -> None:
        messagebox.showerror("Error", str(ex))
        self.run_btn.config(state="normal")
        self.prog_label.config(text="Ready")

    def _on_load_settings(self) -> None:
        file_path = filedialog.askopenfilename(
            initialdir=infra.RESULTS_DIR,
            title="Select settings.json",
            filetypes=[("JSON files", "*.json")],
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                new_conf = json_load_safe(f)
            for k, v in new_conf.items():
                if k in self.entries:
                    self.entries[k].delete(0, tk.END)
                    self.entries[k].insert(0, str(v))
            # env_type を Combobox に復元
            if "env_type" in new_conf:
                from models import ENV_LABELS
                _key_to_label = {v: k for k, v in ENV_LABELS.items()}
                label = _key_to_label.get(new_conf["env_type"], "Suburban")
                self._env_var.set(label)
            messagebox.showinfo("Success", "Settings imported.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _on_open_results(self) -> None:
        if os.path.exists(infra.RESULTS_DIR):
            os.startfile(infra.RESULTS_DIR)


# json.load のラッパー（import json を views 内に閉じ込める）
import json

def json_load_safe(f):
    return json.load(f)
