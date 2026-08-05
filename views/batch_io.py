"""
views/batch_io.py
=================
バッチ窓の **CSV 入出力とテンプレート**（`BatchBuilderWindow` の Mixin）。

⛔ CSV の列と正規化の規則は `batch.py` が単一ソース＝ここは窓とファイル選択の
つなぎだけを持つ（列名を書き写さない）。

⚠️ **これは `BatchBuilderWindow` の一部**であって独立した部品ではない。
切り出しは 2.7 スライス A（メソッド本文は 1 文字も変えていない＝「移動だけ」）。
"""

from tkinter import filedialog

import batch
import i18n
from views import dialogs


class _CsvMixin:
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

        self.replace_rows(rows)
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
