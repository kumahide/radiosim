"""
views/map_cache.py
==================
地図窓の**キャッシュ管理モード**（`MapWindow` の Mixin）。

範囲のドラッグ選択・DEM の一括ダウンロード／強制再取得／削除・取得済み領域の
オーバーレイ描画。⚠️ 通信は必ずワーカースレッドで、描画はメインスレッドへ戻す。

⚠️ **これは `MapWindow` の一部**であって独立した部品ではない。切り出しは
2.7 スライス A（メソッド本文は 1 文字も変えていない＝「移動だけ」）。
"""

import threading
import time
import tkinter as tk
from typing import TYPE_CHECKING

from core import dem
from core import i18n
from views import dialogs
from views import progress
from views.map_style import _LEVEL_COLORS, _OUTLINE_COLOR

if TYPE_CHECKING:
    from tkintermapview import TkinterMapView
    from tkintermapview.canvas_polygon import CanvasPolygon

    from views.progress import ProgressPump

logger = __import__("logging").getLogger("radiosim")


class _CacheMixin:
    # 宿主（`MapWindow`）から借りている面の宣言。**型検査のときだけ**存在する
    # （実行時は 1 文字も定義しない）。理由は
    # [views/map_picks.py](map_picks.py) の同じブロックに書いた（B-049）。
    if TYPE_CHECKING:
        _win: tk.Toplevel
        _map: "TkinterMapView"
        _mode: tk.StringVar
        _pump: "ProgressPump"
        _busy: bool
        _sel_start: "tuple | None"
        _sel_rect: "CanvasPolygon | None"
        _bbox_polygon: "CanvasPolygon | None"
        _tile_polygons: list
        _overlay_after_id: "str | None"
        _lat1_var: tk.StringVar
        _lon1_var: tk.StringVar
        _lat2_var: tk.StringVar
        _lon2_var: tk.StringVar
        _progress_var: tk.IntVar
        _TILES_PER_AREA: int
        _DEFAULT_TILE_BYTES: int

        def _set_busy(self, busy: bool) -> None: ...
        def _set_status(self, text: str, auto_clear: bool = False) -> None: ...
        def _show_progress(self) -> None: ...
        def _hide_progress(self) -> None: ...
        def _clear_selection(self) -> None: ...
        def _do_delete(self, bbox: tuple) -> None: ...
        def _on_download_done(self, dl_result: dict) -> None: ...

    # ----------------------------------------------------------
    # Ctrl＋ドラッグによる矩形選択
    #
    # tkinter は「より具体的なバインド」を優先するため、<Control-B1-Motion>
    # を張ると Ctrl 押下中のドラッグでは素の <B1-Motion>（地図パン）が呼ばれ
    # ない。よってモード切替やパン無効化なしで「素のドラッグ＝パン／Ctrl＋
    # ドラッグ＝範囲選択」が両立する。
    # ----------------------------------------------------------
    def _sel_press(self, event) -> None:
        if self._busy:
            return   # DL 実行中は新たな範囲選択を開始しない
        self._sel_start = self._map.convert_canvas_coords_to_decimal_coords(event.x, event.y)

    def _sel_drag(self, event) -> None:
        if self._sel_start is None:
            return
        cur = self._map.convert_canvas_coords_to_decimal_coords(event.x, event.y)
        lat_n = max(self._sel_start[0], cur[0]); lat_s = min(self._sel_start[0], cur[0])
        lon_w = min(self._sel_start[1], cur[1]); lon_e = max(self._sel_start[1], cur[1])
        if self._sel_rect is not None:
            self._sel_rect.delete()
        self._sel_rect = self._map.set_polygon(
            [(lat_n, lon_w), (lat_n, lon_e), (lat_s, lon_e), (lat_s, lon_w)],
            fill_color="", outline_color="#0066CC", border_width=2,
        )

    def _sel_release(self, event, action: str) -> None:
        """ドラッグ確定。action: "download"（Ctrl）/ "delete"（Shift+Ctrl）。"""
        start = self._sel_start
        self._sel_start = None
        if self._sel_rect is not None:
            self._sel_rect.delete()
            self._sel_rect = None
        if start is None:
            return
        cur = self._map.convert_canvas_coords_to_decimal_coords(event.x, event.y)
        if abs(start[0] - cur[0]) < 1e-9 or abs(start[1] - cur[1]) < 1e-9:
            return   # クリックのみ（面積ゼロ）は無視
        lat_n = max(start[0], cur[0]); lat_s = min(start[0], cur[0])
        lon_w = min(start[1], cur[1]); lon_e = max(start[1], cur[1])
        # NW を (lat1, lon1)、SE を (lat2, lon2) として確定し、枠とエリア数を表示
        self._lat1_var.set(f"{lat_n:.6f}")
        self._lon1_var.set(f"{lon_w:.6f}")
        self._lat2_var.set(f"{lat_s:.6f}")
        self._lon2_var.set(f"{lon_e:.6f}")
        self._draw_bbox_rect()
        # 選択エリア数はこの直後の確認ダイアログが必ず提示するため、別途の表示はしない。

        bbox = (lat_n, lon_w, lat_s, lon_e)
        if action in ("download", "download_force"):
            force = action == "download_force"
            # 表示する対象数は force の有無で変わる:
            #   force ON  → 全エリア再取得（総数）
            #   force OFF → キャッシュ済みはスキップされるので新規分のみ
            total = dem.count_bbox_tiles(*bbox)
            n = total if force else total - dem.count_cached_areas(*bbox)
            title = i18n.t("tm_dl_force_title") if force else i18n.t("tm_dl_title")
            msg = (i18n.t("tm_dl_force_confirm") if force else i18n.t("tm_dl_confirm")).format(n=n)
            msg += "\n" + i18n.t("tm_dl_size_hint").format(mb=self._estimate_mb(n))
            if dialogs.confirm(self._win, title, msg):
                self._start_download(bbox, force)
            else:
                self._clear_selection()
        else:   # delete
            # 削除は実際にキャッシュ済みのエリアのみが対象
            n = dem.count_cached_areas(*bbox)
            if dialogs.confirm(
                self._win, i18n.t("tm_delete_title"),
                i18n.t("tm_delete_confirm").format(n=n),
            ):
                self._do_delete(bbox)
            else:
                self._clear_selection()

    # ----------------------------------------------------------
    # bbox 矩形描画
    # ----------------------------------------------------------
    def _draw_bbox_rect(self) -> None:
        try:
            lat1 = float(self._lat1_var.get())
            lon1 = float(self._lon1_var.get())
            lat2 = float(self._lat2_var.get())
            lon2 = float(self._lon2_var.get())
        except ValueError:
            return
        if self._bbox_polygon:
            self._bbox_polygon.delete()
        lat_n = max(lat1, lat2); lat_s = min(lat1, lat2)
        lon_w = min(lon1, lon2); lon_e = max(lon1, lon2)
        self._bbox_polygon = self._map.set_polygon(
            [(lat_n, lon_w), (lat_n, lon_e), (lat_s, lon_e), (lat_s, lon_w)],
            fill_color="",
            outline_color="#0066CC",
            border_width=2,
        )

    # ----------------------------------------------------------
    # タイルオーバーレイ
    # ----------------------------------------------------------
    def _clear_tile_overlays(self) -> None:
        for p in self._tile_polygons:
            p.delete()
        self._tile_polygons.clear()

    # ----------------------------------------------------------
    # 自動カバレッジ表示（地図のパン/ズームに追従）
    # ----------------------------------------------------------
    def _schedule_overlay_refresh(self, event=None) -> None:
        """パン/ズーム連打をデバウンスして再描画する。"""
        if self._mode.get() != "cache":
            return   # キャッシュ管理以外ではカバレッジを描かない（無駄なタイマーも張らない）
        if self._overlay_after_id is not None:
            self._win.after_cancel(self._overlay_after_id)
        self._overlay_after_id = self._win.after(300, self._refresh_overlay)

    def _refresh_overlay(self) -> None:
        self._overlay_after_id = None
        if self._mode.get() != "cache":
            return   # キャッシュ管理以外ではカバレッジ描画をスキップ
        try:
            w = self._map.canvas.winfo_width()
            h = self._map.canvas.winfo_height()
            if w < 2 or h < 2:
                return
            nw = self._map.convert_canvas_coords_to_decimal_coords(0, 0)
            se = self._map.convert_canvas_coords_to_decimal_coords(w, h)
            # セル粒度は表示ズームに追従させ、ポリゴン数を画面タイル数程度に保つ。
            overlay_zoom = max(2, min(14, int(round(self._map.zoom))))
        except Exception:
            return
        threading.Thread(
            target=self._overlay_worker, args=(nw, se, overlay_zoom), daemon=True
        ).start()

    def _overlay_worker(self, nw: tuple, se: tuple, overlay_zoom: int) -> None:
        cells = dem.scan_cache_overlay(nw[0], nw[1], se[0], se[1], overlay_zoom)
        outline = dem.coverage_outline(nw[0], nw[1], se[0], se[1])
        # 走査中に地図窓を閉じられている可能性がある（B-061）
        progress.post_to_ui(self._win,
                            lambda: self._draw_overlay_cells(cells, outline))

    def _draw_overlay_cells(self, cells: list, outline: list) -> None:
        if self._mode.get() != "cache":
            return   # モード切替後に届いた旧ワーカー結果は捨てる（描画しない）
        self._clear_tile_overlays()
        # 半透明塗り（stipple はライブラリ既定）。セル境界線は描かず、
        # 隣接セルの塗りを繋げて内部グリッド線を出さない。
        for c in cells:
            x, y, z = c["x"], c["y"], c["zoom"]
            lat_n, lon_w = dem.tile_to_latlng(x,     y,     z)
            lat_s, lon_e = dem.tile_to_latlng(x + 1, y + 1, z)
            color = _LEVEL_COLORS.get(c["level"], "#CCCCCC")
            p = self._map.set_polygon(
                [(lat_n, lon_w), (lat_n, lon_e), (lat_s, lon_e), (lat_s, lon_w)],
                fill_color=color,
                outline_color="",
                border_width=0,
            )
            self._tile_polygons.append(p)
        # 領域の外周線のみを描く。
        for loop in outline:
            p = self._map.set_polygon(
                loop,
                fill_color="",
                outline_color=_OUTLINE_COLOR,
                border_width=2,
            )
            self._tile_polygons.append(p)

    def _estimate_mb(self, n_areas: int) -> str:
        """DL 容量の目安 [MB] を文字列で返す。平均タイルサイズは実キャッシュから推定。"""
        stats = dem.get_cache_stats()
        avg = stats["size_bytes"] / stats["count"] if stats["count"] else self._DEFAULT_TILE_BYTES
        mb = n_areas * self._TILES_PER_AREA * avg / (1024 * 1024)
        return f"{mb:.1f}"

    def _start_download(self, bbox: tuple, force: bool) -> None:
        self._set_busy(True)
        self._progress_var.set(0)
        self._show_progress()
        self._set_status(i18n.t("tm_downloading"))
        self._pump.start()
        threading.Thread(target=self._download_worker, args=(bbox, force), daemon=True).start()

    def _download_worker(self, bbox: tuple, force: bool) -> None:
        # 進捗はポンプ経由で渡す。従来はタイルごとに `after(0, ...)` を2回呼んで
        # おり、ワーカースレッドから Tcl を叩く点でも他フローで廃した書き方だった
        # （単一実行では同じ形が取得時間そのものを支配していた＝B-006）。ここは
        # progress_cb がロック外で呼ばれるので直列化の実害は無かったが、書き方は
        # 3フローで揃える。
        def progress_cb(done: int, total: int) -> None:
            pct = int(done / total * 100) if total else 0
            self._pump.push((pct, i18n.t("tm_dl_progress").format(
                done=done, total=total, pct=pct)))

        t0 = time.perf_counter()
        dl_result = dem.prefetch_tiles(*bbox, progress_cb=progress_cb, force=force)
        logger.info("Tile download complete in %.2fs: %s",
                    time.perf_counter() - t0, dl_result)
        progress.post_to_ui(self._win, lambda: self._on_download_done(dl_result))

    def _render_progress(self, item: tuple) -> None:
        """ポンプから届いた進捗を描画する（メインスレッドで呼ばれる）。"""
        pct, text = item
        self._progress_var.set(pct)
        self._set_status(text)
