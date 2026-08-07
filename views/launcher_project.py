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
from views import dialogs


class _ProjectMixin:
    # ----------------------------------------------------------
    # プロジェクト（`.rsproj`）＝入力一式を 1 つに束ねる
    #
    # 保持者はランチャー。**開いている窓からは現在値を集め、閉じている窓の節は
    # 前回値を持ち越す**（窓を閉じただけで内容が消えたファイルを書かない）。
    # 読込は**窓を閉じず、開いている窓には帯で知らせて押されたときだけ差し替える**
    # （I-061・2026-08-07）。凍結方式（開く＝スナップショット／見えている値で
    # 実行する）は保たれる＝帯を押さなければ画面は動かない。
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

        # **窓は閉じない**（I-061・2026-08-07 ユーザー決定）。開いている窓には
        # 「取り込める」ことだけ知らせ、**押したときだけ**差し替える。
        # ⚠️ 以前は「確認して閉じ、開き直す」形だった＝凍結方式（開く＝スナップ
        # ショット）とちょうど一致するので流し込み口が要らなかった。作業の流れが
        # 切れる（窓の位置・スクロール位置・選択が失われる）のが代償で、そちらを
        # 取った。**凍結方式そのものは改版していない**＝窓が黙って書き換わることは
        # 起きない（帯を押さなければ画面は 1px も動かない）。
        self._project = doc
        self._apply_sim_config(doc.params)
        self._project_var.set(doc.meta.get("project_name", ""))
        self._memo_var.set(doc.meta.get("memo", ""))
        self._offer_project_to_open_windows()
        self._alert(i18n.t("dlg_success"), i18n.t("proj_loaded"))

    def _offer_project_to_open_windows(self) -> None:
        """開いている窓に「内容を取り込む」帯を出す（I-061）。

        ⛔ **既存の `↻ ランチャーから更新` には載せない**＝あちらは凍結帯（共通
        設定・案件情報）だけを触り、利用者が入れた行・地点には手を出さない。
        そこへ差し替えを足すと「共通設定を更新するつもりで押したら行が消えた」
        という、この項目で直そうとしている事故を自分で作ることになる。

        ⚠️ **節を持たないファイルでは帯を出さない**＝`None` は「その窓の情報を
        持たない」という意味（`project.py` の約束）で、空にする指示ではない。
        出してしまうと「取り込む」が**行の全消し**として働く。

        ⚠️ **取り込まないまま窓を閉じたら、画面に見えている方（窓の内容）が残る**
        ＝閉じる処理が今までどおりその窓の節を `self._project` へ持ち越すため。
        凍結方式（見えている値が正）と同じ向きなので、これでよい。
        """
        doc = self._project
        targets = (
            ("_batch_win",    doc.batch_rows,
             lambda win, rows=doc.batch_rows: win.replace_rows(rows)),
            ("_scenario_win", doc.scenario,
             lambda win, spec=doc.scenario: win.apply_project_spec(spec)),
            ("_multihop_win", doc.multihop,
             lambda win, path=doc.multihop: win.apply_project_path(path)),
        )
        for attr, section, take in targets:
            win = self._open_window(attr)
            if win is None or section is None:
                continue
            dialogs.show_notice(
                win, i18n.t("proj_notice"), i18n.t("proj_notice_take"),
                lambda w=win, t=take: t(w))
