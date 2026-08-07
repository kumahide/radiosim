"""
views/launcher_windows.py
=========================
ランチャーから開く**子窓の開閉と、窓どうしの通知**（`SimLauncher` の Mixin）。

一括／条件探索／中継経路／マップ。⚠️ **ランチャーから分岐する窓は凍結方式**＝
開く時にランチャーの値をスナップショットして渡し、以後は `↻` で更新する。

⚠️ **これは `SimLauncher` の一部**であって独立した部品ではない。切り出しは
2.7 スライス A（メソッド本文は 1 文字も変えていない＝「移動だけ」）。
"""

import os
import tkinter as tk

import config
import coords
import simulation as sim


class _ChildWindowsMixin:
    def _notify_map_cache_change(self) -> None:
        """シミュレーションのプリフェッチでキャッシュが増えた後、開いている
        マップウィンドウの統計・カバレッジ表示を更新する。"""
        if hasattr(self, "_map_win") and self._map_win._win.winfo_exists():
            self._map_win.on_external_cache_change()

    def _on_open_results(self) -> None:
        if os.path.exists(config.RESULTS_DIR):
            os.startfile(config.RESULTS_DIR)

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
            waypoint_provider=self._open_multihop_for_waypoints,
        )

    def _open_batch_for_append(self):
        """連続追加モードの append 先としてバッチウィンドウを開いて返す。"""
        return self.ensure_batch_window()

    def _open_multihop_for_waypoints(self):
        """中継点モードの宛先として中継経路ウィンドウを開いて返す（append と対称）。"""
        self._on_open_multihop()
        return self._multihop_win

    def _on_open_multihop(self) -> None:
        """中継経路ウィンドウを開く（唯一インスタンス。開いていれば前面化）。

        バッチ・地図・条件探索と同じ流儀＝**ランチャーが source of truth**で、
        共通設定と案件情報は現在値のスナップショットを渡す。
        """
        win = getattr(self, "_multihop_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        from views.multihop import MultiHopWindow
        try:
            params = sim.SimParams(self._current_config())
        except Exception:
            params = sim.SimParams(config.DEFAULT_CONFIG)
        self._multihop_win = MultiHopWindow(
            self.root, params,
            config_provider=self._current_config,
            meta_provider=self._current_meta,
            on_close=self._on_multihop_closed,
            # プロジェクトを読み込んでいれば、その経路で開く（凍結方式＝
            # 「開く＝スナップショット」とちょうど一致するので、開いている窓へ
            # 後から流し込む口を作らずに済む）。
            initial_path=self._project_doc().multihop,
            # ⚠️ 親ウィジェットから探させない（`self.master` は Tk のルートで
            # ランチャーではない＝5b の実装はここで黙って None になり、地図連携が
            # 一度も動かなかった）。バッチ・条件探索と同じく**注入**する。
            map_opener=self.open_map_for_waypoints,
            map_notify=self._notify_map_waypoints_changed,
            # 座標の表記も凍結して渡す（I-070）＝この窓だけ設定に従わず、
            # 常に十進度で出していた。
            coord_format=self._coord_fmt_var.get(),
        )

    def _on_multihop_closed(self) -> None:
        """閉じる前に経路を持ち越す（読めない値のときは前回値のまま）。"""
        win = self._open_window("_multihop_win")
        if win is not None:
            try:
                path = win.project_path()
            except ValueError:
                path = None          # 未完成の入力で保存済みの経路を壊さない
            if path is not None:
                self._project_doc().multihop = path
        self._multihop_win = None

    def open_map_for_append(self) -> None:
        """地図を**連続追加モード**で開く（複数経路の窓の「地図から取る」・I-043）。

        ⚠️ **「バッチからは地図を開かない」という決めごとを、ここで意図的に外した**
        ＝後から来た中継経路の窓が既に地図を開けるので、決めごとのほうが破られて
        いた（⑧＝窓によって出来ることが違うのが最も高い代償）。**揃える向きは
        「足す」**＝中継から地図を外すのは機能の後退（地点は地図で拾うのが自然）。
        新しい経路は増えない＝地図 → 複数経路の連続追加は既にあり、その逆向きを
        呼ぶだけ。
        """
        self._on_open_map()
        self._map_win.start_append_mode()

    def open_map_for_waypoints(self, sink) -> None:
        """地図を**中継点モード**で開いて宛先をこの sink にする（2.3 D2 の型）。

        地図はアプリ唯一のインスタンスで、モードで宛先を切り替える。中継経路の
        窓から直接 2 つ目の地図を作らない（D2 で一度そうして親子関係と描画系が
        歪んだ経緯がある）。
        """
        self._on_open_map()
        self._map_win.start_waypoint_mode(sink)

    def _on_open_scenario(self) -> None:
        """条件探索ウィンドウを開く（唯一インスタンス。開いていれば前面化）。

        バッチ・地図と同じく**ランチャーが source of truth**で、経路とパラメータは
        現在値のスナップショットを渡す（実行のたびに config_provider で取り直す）。
        起動時の import を増やさないため遅延 import（MapWindow/BatchBuilder と同方針）。
        """
        win = getattr(self, "_scenario_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return
        from views.scenario import ScenarioWindow
        try:
            params = sim.SimParams(self._current_config())
        except Exception:
            params = sim.SimParams(config.DEFAULT_CONFIG)
        self._scenario_win = ScenarioWindow(
            self.root, params,
            config_provider=self._current_config,
            meta_provider=self._current_meta,
            on_close=self._on_scenario_closed,
            initial_spec=self._project_doc().scenario,
            coord_format=self._coord_fmt_var.get(),
        )

    def _on_scenario_closed(self) -> None:
        """閉じる前に条件セットを持ち越す（窓を閉じただけで消さない）。"""
        win = self._open_window("_scenario_win")
        if win is not None:
            self._project_doc().scenario = win.project_spec()
        self._scenario_win = None

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
            initial_rows=self._project_doc().batch_rows,
            # app 設定（座標表記）も凍結して渡す＝窓に `config.load_config()` を
            # 読ませない（I-055 ②・2.7 スライス G2）。出所はランチャー 1 つ。
            coord_format=self._coord_fmt_var.get(),
            # 地図を連続追加モードで開く口（I-043）＝この窓にだけ無かった。
            map_opener=self.open_map_for_append,
        )
        return self._batch_win

    def _on_batch_closed(self) -> None:
        """バッチが閉じたとき: **行を持ち越し**、参照を手放し、地図が連続追加中なら
        座標入力へ戻させる。

        ⚠️ この通知はバッチ窓が**破棄される前**に来る（`_on_close_window`）。行の
        回収がここでできるのはそのためで、順序を戻すと「閉じただけで行が消えた
        プロジェクトを保存する」ことになる。
        """
        win = self._open_window("_batch_win")
        if win is not None:
            self._project_doc().batch_rows = win.project_rows()
        self._batch_win = None
        if hasattr(self, "_map_win") and self._map_win._win.winfo_exists():
            self._map_win.on_append_target_closed()

    def _notify_map_paths_changed(self) -> None:
        """バッチの行が変わったとき、地図の確定パス表示を追従させる。"""
        if hasattr(self, "_map_win") and self._map_win._win.winfo_exists():
            self._map_win.on_paths_changed()

    def _notify_map_waypoints_changed(self) -> None:
        """中継経路の地点列が変わったとき、地図の中継点表示を追従させる。"""
        if hasattr(self, "_map_win") and self._map_win._win.winfo_exists():
            self._map_win.on_waypoints_changed()

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
