"""
views/batch_io.py
=================
バッチ窓の **CSV 入出力とテンプレート**（`BatchBuilderWindow` の Mixin）。

⛔ CSV の列と正規化の規則は `batch.py` が単一ソース＝ここは窓とファイル選択の
つなぎだけを持つ（列名を書き写さない）。

⚠️ **これは `BatchBuilderWindow` の一部**であって独立した部品ではない。
切り出しは 2.7 スライス A（メソッド本文は 1 文字も変えていない＝「移動だけ」）。
"""

import tkinter as tk
from tkinter import filedialog
from typing import TYPE_CHECKING

from core import i18n
from report import batch
from views import dialogs

if TYPE_CHECKING:
    from core import simulation as sim

# 宿主（`BatchBuilderWindow`）は `tk.Toplevel` の派生。理由は
# [views/batch_table.py](batch_table.py) の同じ宣言に書いた（B-049）。
if TYPE_CHECKING:
    _HostBase = tk.Toplevel
else:
    _HostBase = object


class _CsvMixin(_HostBase):
    # 宿主から借りている面の宣言。**型検査のときだけ**存在する（実行時は 1 文字も
    # 定義しない）。理由は [views/map_picks.py](map_picks.py) の同じブロックに
    # 書いた（B-049）。
    if TYPE_CHECKING:
        _base_params: "sim.SimParams"
        _row_entries: list[list[tk.Entry]]

        def _read_table_rows(self) -> list[batch.PathRow]: ...
        def replace_rows(self, rows: list) -> None: ...

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
                i18n.t("btn_import_csv"),
                i18n.t("dlg_import_confirm").format(n=len(self._row_entries)),
            ):
                return

        self.replace_rows(rows)
        dialogs.alert(
            self,
            i18n.t("btn_import_csv"),
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
                i18n.t("btn_template"),
                i18n.t("dlg_template_saved").format(path=path),
            )
        except Exception as e:
            dialogs.alert(self, i18n.t("dlg_error"), str(e))
