"""
views/launcher_project.py
=========================
ランチャーが持つ**プロジェクト（`.rsproj`）の収集・保存・読込**（`SimLauncher` の Mixin）。

🔑 **プロジェクトの保持者はランチャー**＝閉じている窓の節は前回値を持ち越す
（`project.py` の「節が無い＝空ではない」と対）。ここはその窓口。

⚠️ **これは `SimLauncher` の一部**であって独立した部品ではない。切り出しは
2.7 スライス A（メソッド本文は 1 文字も変えていない＝「移動だけ」）。
"""

import tkinter as tk
from tkinter import filedialog

import config
import i18n


class _ProjectMixin:
    # ----------------------------------------------------------
    # プロジェクト（`.rsproj`）＝入力一式を 1 つに束ねる
    #
    # 保持者はランチャー。**開いている窓からは現在値を集め、閉じている窓の節は
    # 前回値を持ち越す**（窓を閉じただけで内容が消えたファイルを書かない）。
    # 読込は「確認して閉じ、開き直す」＝凍結方式（開く＝スナップショット）と
    # ちょうど一致するので、各窓に流し込み口を作らずに済む。
    # ----------------------------------------------------------
    def _project_doc(self) -> "project.ProjectDoc":
        """保持中の ProjectDoc（無ければ空で作る）。project は遅延 import。"""
        import project
        if self._project is None:
            self._project = project.ProjectDoc()
        return self._project

    def _open_window(self, attr: str):
        """開いている窓を返す（閉じていれば None）。"""
        win = getattr(self, attr, None)
        try:
            return win if win is not None and win.winfo_exists() else None
        except tk.TclError:
            return None

    def _collect_project(self) -> "tuple[project.ProjectDoc, list[str]]":
        """現在の入力一式を ProjectDoc に集める。戻り値＝(doc, 警告文リスト)。

        **警告は「保存しなかった節」を伝えるためのもの**＝読めない値を黙って
        書いて壊れたファイルを作るくらいなら、その節だけ保存せずに知らせる。
        保存そのものは通す（入力途中でも保存できることを優先＝I-010 の逆側で、
        黙って失敗しないことが要点）。
        """
        import project
        doc = self._project_doc()
        doc.meta   = self._current_meta()
        doc.params = self._current_config()
        doc.app_version = ""            # 保存のたびに現在の版で刻印し直す
        doc.saved_at    = ""
        warnings: list[str] = []

        bw = self._open_window("_batch_win")
        if bw is not None:
            rows = bw.project_rows()
            bad  = project.unreadable_row(rows)
            if bad is None:
                doc.batch_rows = rows
            else:
                warnings.append(i18n.t("proj_warn_batch").format(
                    id=bad.path_id or "-"))

        sw = self._open_window("_scenario_win")
        if sw is not None:
            doc.scenario = sw.project_spec()

        mw = self._open_window("_multihop_win")
        if mw is not None:
            try:
                path = mw.project_path()
            except ValueError as ex:
                warnings.append(i18n.t("proj_warn_multihop").format(reason=ex))
            else:
                if path is not None:
                    doc.multihop = path
        return doc, warnings

    def _on_save_project(self) -> None:
        import project
        doc, warnings = self._collect_project()
        file_path = filedialog.asksaveasfilename(
            parent           = self.root,
            title            = i18n.t("dlg_save_project"),
            defaultextension = project.FILE_EXT,
            filetypes        = [(i18n.t("proj_filetype"), "*" + project.FILE_EXT)],
            initialfile      = project.default_filename(
                doc.meta.get("project_name", "")),
        )
        if not file_path:
            return
        try:
            project.save(doc, file_path)
        except Exception as e:
            # 書けなかったことを黙らない（I-010 と同クラス）。
            config.logger.warning("Project save failed: %s", e)
            self._alert(i18n.t("dlg_error"), str(e))
            return
        self._alert(i18n.t("dlg_success"),
                    i18n.t("proj_saved").format(path=file_path) + "".join(warnings))

    def _on_open_project(self) -> None:
        import project
        file_path = filedialog.askopenfilename(
            parent    = self.root,
            title     = i18n.t("dlg_select_project"),
            filetypes = [(i18n.t("proj_filetype"), "*" + project.FILE_EXT)],
        )
        if not file_path:
            return
        try:
            doc = project.load(file_path)
        except project.ProjectError as e:
            self._alert(i18n.t("dlg_error"), str(e))
            return
        except Exception as e:
            config.logger.warning("Project load failed: %s", e)
            self._alert(i18n.t("dlg_error"), str(e))
            return

        # 開いている窓があるなら、閉じてよいか先に聞く（編集中の内容が消えるので）。
        open_windows = [w for w in (self._open_window("_batch_win"),
                                    self._open_window("_scenario_win"),
                                    self._open_window("_multihop_win"))
                        if w is not None]
        if open_windows and not self._confirm(i18n.t("proj_close_title"),
                                              i18n.t("proj_close_confirm")):
            return
        for win in open_windows:
            # ⚠️ 各窓の close ハンドラ名に依存しない（バッチは `_on_close` を
            # **コールバックの保管場所**として使っており、名前で分岐すると
            # 「閉じずにコールバックだけ呼ぶ」に化ける）。公開口は close_window。
            win.close_window()

        # ⚠️ **窓を閉じてから doc を差し替える**＝閉じる処理は「その窓の節を
        # 持ち越す」ので、先に差し替えると読み込んだ内容が閉じた窓の値で上書きされる。
        self._project = doc
        self._apply_sim_config(doc.params)
        self._project_var.set(doc.meta.get("project_name", ""))
        self._memo_var.set(doc.meta.get("memo", ""))
        self._alert(i18n.t("dlg_success"), i18n.t("proj_loaded"))
