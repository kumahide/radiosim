"""
views/tile_manager.py
=====================
タイルキャッシュ管理ウィンドウ。

地図上でbbox を指定し、DEMタイルの
  - カバレッジ確認（色付きオーバーレイ表示）
  - 不足タイルのダウンロード
  - 範囲削除 / 全削除
を行う。
"""

import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import i18n
import infrastructure as infra
from tkintermapview import TkinterMapView

# zoom-14 オーバーレイの色（最高精度レベルで色分け）
_LEVEL_COLORS: dict[str, str] = {
    "5a":   "#90EE90",  # 緑: 5m航空（dem5a_png）
    "5b":   "#FFD700",  # 黄: 5m写真（dem5b_png）
    "dem":  "#87CEEB",  # 水色: 10m（dem_png）
    "none": "#FFB6C1",  # ピンク: キャッシュなし
}

# キャッシュ済み領域の外周線の色
_OUTLINE_COLOR = "#0066CC"


class TileManagerWindow:
    def __init__(self, parent: tk.Misc, config: dict) -> None:
        self._config = config
        self._sync_proxy()

        self._win = tk.Toplevel(parent)
        self._win.title(i18n.t("tm_title"))
        self._win.geometry("900x680")
        self._win.minsize(720, 520)

        self._bbox_polygon = None
        self._tile_polygons: list = []

        # Ctrl＋ドラッグ矩形選択の状態
        self._sel_start = None    # (lat, lon) ドラッグ開始点
        self._sel_rect  = None    # ドラッグ中のライブ矩形ポリゴン

        self._lat1_var = tk.StringVar()
        self._lon1_var = tk.StringVar()
        self._lat2_var = tk.StringVar()
        self._lon2_var = tk.StringVar()

        self._action_btns: list[ttk.Button] = []
        self._overlay_after_id = None   # 自動カバレッジ表示のデバウンス用

        self._build_ui()
        self._refresh_stats()
        # 地図のレイアウト確定後に初回カバレッジを自動描画する。
        self._win.after(600, self._refresh_overlay)

    # ----------------------------------------------------------
    # プロキシ env 変数同期
    # ----------------------------------------------------------
    def _sync_proxy(self) -> None:
        proxy = self._config.get("proxy_url", "").strip()
        if proxy:
            os.environ["HTTP_PROXY"]  = proxy
            os.environ["HTTPS_PROXY"] = proxy
        else:
            os.environ.pop("HTTP_PROXY",  None)
            os.environ.pop("HTTPS_PROXY", None)

    # ----------------------------------------------------------
    # UI 構築
    # ----------------------------------------------------------
    def _build_ui(self) -> None:
        self._map = TkinterMapView(self._win, corner_radius=0)
        # 地図タイルは GSI 淡色地図に統一（DEM 出典と揃え、外部 API を GSI 一本化）。
        self._map.set_tile_server(
            "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", max_zoom=18
        )
        self._map.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        self._map.set_position(35.68, 139.77)
        self._map.set_zoom(8)

        cv = self._map.canvas
        # パン/ズーム終了時にカバレッジを自動再描画する。
        # tkintermapview 自身が canvas にバインド済みのため add="+" で相乗りする。
        for seq in ("<ButtonRelease-1>", "<MouseWheel>", "<Button-4>", "<Button-5>"):
            cv.bind(seq, self._schedule_overlay_refresh, add="+")
        # ジェスチャ（素のドラッグはパン）。tkinter は具体的なバインドを優先するため
        # 各修飾キー付きドラッグとパンは競合しない。
        #   Ctrl＋ドラッグ        = ダウンロード（通常）
        #   Ctrl+Alt＋ドラッグ    = ダウンロード（強制再取得）
        #   Shift+Ctrl＋ドラッグ  = 範囲削除
        for seq in ("<Control-Button-1>", "<Control-Alt-Button-1>", "<Shift-Control-Button-1>"):
            cv.bind(seq, self._sel_press, add="+")
        for seq in ("<Control-B1-Motion>", "<Control-Alt-B1-Motion>", "<Shift-Control-B1-Motion>"):
            cv.bind(seq, self._sel_drag, add="+")
        cv.bind("<Control-ButtonRelease-1>",
                lambda e: self._sel_release(e, "download"), add="+")
        cv.bind("<Control-Alt-ButtonRelease-1>",
                lambda e: self._sel_release(e, "download_force"), add="+")
        cv.bind("<Shift-Control-ButtonRelease-1>",
                lambda e: self._sel_release(e, "delete"), add="+")

        bottom = ttk.Frame(self._win)
        bottom.pack(fill="x", padx=6, pady=4)

        # 選択範囲のエリア数表示（範囲は Ctrl＋ドラッグで指定）
        coord_row = ttk.Frame(bottom)
        coord_row.pack(fill="x", pady=(0, 4))

        self._tile_count_label = ttk.Label(coord_row, text="")
        self._tile_count_label.pack(side="left")

        # ボタン行: DL/範囲削除/強制再取得はすべてジェスチャ化したため、
        # 残るのは範囲を持たない「全削除」のみ。
        btn_row = ttk.Frame(bottom)
        btn_row.pack(fill="x", pady=(0, 4))

        b_all = ttk.Button(btn_row, text=i18n.t("tm_btn_delete_all"), command=self._on_delete_all)
        b_all.pack(side="right", padx=(4, 0))
        self._action_btns.append(b_all)

        # プログレスバー
        self._progress_var = tk.IntVar(value=0)
        self._progress = ttk.Progressbar(bottom, variable=self._progress_var, maximum=100)
        self._progress.pack(fill="x", pady=(0, 2))

        # ステータス（カバレッジ確認・DL 結果を集約。複数行になるため justify=left）
        self._status_var = tk.StringVar(value="")
        self._status_label = ttk.Label(
            bottom, textvariable=self._status_var, anchor="w", justify="left",
            wraplength=860,
        )
        self._status_label.pack(fill="x")
        # ウィンドウ幅に追従して折り返し幅を更新する。
        self._status_label.bind(
            "<Configure>",
            lambda e: self._status_label.config(wraplength=max(200, e.width - 8)),
        )

        # キャッシュ統計
        self._stats_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self._stats_var, anchor="w").pack(fill="x")

        # 操作ヒント + 出典表記（GSI 帰属）
        ttk.Label(bottom, text=i18n.t("tm_hint"),
                  foreground="gray").pack(anchor="w")
        ttk.Label(bottom, text=i18n.t("tm_attribution"),
                  foreground="gray").pack(anchor="w")

    # ----------------------------------------------------------
    # Ctrl＋ドラッグによる矩形選択
    #
    # tkinter は「より具体的なバインド」を優先するため、<Control-B1-Motion>
    # を張ると Ctrl 押下中のドラッグでは素の <B1-Motion>（地図パン）が呼ばれ
    # ない。よってモード切替やパン無効化なしで「素のドラッグ＝パン／Ctrl＋
    # ドラッグ＝範囲選択」が両立する。
    # ----------------------------------------------------------
    def _sel_press(self, event) -> None:
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
        self._update_tile_count()

        bbox = (lat_n, lon_w, lat_s, lon_e)
        if action in ("download", "download_force"):
            force = action == "download_force"
            # 表示する対象数は force の有無で変わる:
            #   force ON  → 全エリア再取得（総数）
            #   force OFF → キャッシュ済みはスキップされるので新規分のみ
            total = infra.count_bbox_tiles(*bbox)
            n = total if force else total - infra.count_cached_areas(*bbox)
            title = i18n.t("tm_dl_force_title") if force else i18n.t("tm_dl_title")
            msg = (i18n.t("tm_dl_force_confirm") if force else i18n.t("tm_dl_confirm")).format(n=n)
            msg += "\n" + i18n.t("tm_dl_size_hint").format(mb=self._estimate_mb(n))
            if messagebox.askyesno(title, msg, parent=self._win):
                self._start_download(bbox, force)
            else:
                self._clear_selection()
        else:   # delete
            # 削除は実際にキャッシュ済みのエリアのみが対象
            n = infra.count_cached_areas(*bbox)
            if messagebox.askyesno(
                i18n.t("tm_delete_title"),
                i18n.t("tm_delete_confirm").format(n=n), parent=self._win,
            ):
                self._do_delete(bbox)
            else:
                self._clear_selection()

    # ----------------------------------------------------------
    # bbox 矩形描画 / タイル枚数更新
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

    def _update_tile_count(self) -> None:
        try:
            lat1 = float(self._lat1_var.get())
            lon1 = float(self._lon1_var.get())
            lat2 = float(self._lat2_var.get())
            lon2 = float(self._lon2_var.get())
        except ValueError:
            self._tile_count_label.config(text="")
            return
        n = infra.count_bbox_tiles(lat1, lon1, lat2, lon2)
        self._tile_count_label.config(text=i18n.t("tm_tile_count").format(n=n))

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
        if self._overlay_after_id is not None:
            self._win.after_cancel(self._overlay_after_id)
        self._overlay_after_id = self._win.after(300, self._refresh_overlay)

    def _refresh_overlay(self) -> None:
        self._overlay_after_id = None
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
        cells = infra.scan_cache_overlay(nw[0], nw[1], se[0], se[1], overlay_zoom)
        outline = infra.coverage_outline(nw[0], nw[1], se[0], se[1])
        self._win.after(0, self._draw_overlay_cells, cells, outline)

    def _draw_overlay_cells(self, cells: list, outline: list) -> None:
        self._clear_tile_overlays()
        # 半透明塗り（stipple はライブラリ既定）。セル境界線は描かず、
        # 隣接セルの塗りを繋げて内部グリッド線を出さない。
        for c in cells:
            x, y, z = c["x"], c["y"], c["zoom"]
            lat_n, lon_w = infra.tile_to_latlng(x,     y,     z)
            lat_s, lon_e = infra.tile_to_latlng(x + 1, y + 1, z)
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

    # ----------------------------------------------------------
    # ダウンロード（Ctrl＋ドラッグ → 確認 → 実行）
    # ----------------------------------------------------------
    # 1 エリア = 最大 4 サブタイル（5m系）と見なした安全側の容量見積り。
    _TILES_PER_AREA = 4
    _DEFAULT_TILE_BYTES = 25 * 1024   # 実キャッシュが無いときのフォールバック

    def _estimate_mb(self, n_areas: int) -> str:
        """DL 容量の目安 [MB] を文字列で返す。平均タイルサイズは実キャッシュから推定。"""
        stats = infra.get_cache_stats()
        avg = stats["size_bytes"] / stats["count"] if stats["count"] else self._DEFAULT_TILE_BYTES
        mb = n_areas * self._TILES_PER_AREA * avg / (1024 * 1024)
        return f"{mb:.1f}"

    def _start_download(self, bbox: tuple, force: bool) -> None:
        self._set_busy(True)
        self._progress_var.set(0)
        self._status_var.set(i18n.t("tm_downloading"))
        threading.Thread(target=self._download_worker, args=(bbox, force), daemon=True).start()

    def _download_worker(self, bbox: tuple, force: bool) -> None:
        def progress_cb(done: int, total: int) -> None:
            pct = int(done / total * 100) if total else 0
            self._win.after(0, self._progress_var.set, pct)
            self._win.after(0, self._status_var.set,
                i18n.t("tm_dl_progress").format(done=done, total=total, pct=pct))
        dl_result = infra.prefetch_tiles(*bbox, progress_cb=progress_cb, force=force)
        self._win.after(0, self._on_download_done, dl_result)

    def _on_download_done(self, dl_result: dict) -> None:
        self._set_busy(False)
        self._progress_var.set(100)
        self._status_var.set(i18n.t("tm_dl_done").format(
            dl5a=dl_result["downloaded_5a"],
            dl5b=dl_result["downloaded_5b"],
            dl_dem=dl_result["downloaded_dem"],
            skipped=dl_result["skipped"],
            failed=dl_result["failed"],
        ))
        self._refresh_stats()
        self._refresh_overlay()   # DL 結果を自動カバレッジ表示に反映
        self._clear_selection()   # DL 完了後は選択枠を消す

    def _clear_selection(self) -> None:
        """選択枠・座標・エリア数表示をクリアする。"""
        if self._bbox_polygon is not None:
            self._bbox_polygon.delete()
            self._bbox_polygon = None
        for var in (self._lat1_var, self._lon1_var, self._lat2_var, self._lon2_var):
            var.set("")
        self._tile_count_label.config(text="")

    # ----------------------------------------------------------
    # 範囲削除（Shift+Ctrl＋ドラッグ → 確認 → 実行）
    # ----------------------------------------------------------
    def _do_delete(self, bbox: tuple) -> None:
        result = infra.delete_tile_cache(*bbox)
        self._status_var.set(i18n.t("tm_delete_done").format(deleted=result["deleted"]))
        self._refresh_stats()
        self._refresh_overlay()   # 削除結果を自動表示に反映
        self._clear_selection()   # 削除後は選択枠を消す

    # ----------------------------------------------------------
    # 全削除
    # ----------------------------------------------------------
    def _on_delete_all(self) -> None:
        if not messagebox.askyesno(
            i18n.t("tm_delete_all_title"), i18n.t("tm_delete_all_confirm"), parent=self._win
        ):
            return
        result = infra.delete_all_tile_cache()
        self._clear_tile_overlays()
        self._status_var.set(i18n.t("tm_delete_all_done").format(deleted=result["deleted"]))
        self._refresh_stats()
        self._refresh_overlay()   # 全削除後の状態を自動表示に反映

    # ----------------------------------------------------------
    # キャッシュ統計
    # ----------------------------------------------------------
    def _refresh_stats(self) -> None:
        stats = infra.get_cache_stats()
        mb = stats["size_bytes"] / (1024 * 1024)
        self._stats_var.set(i18n.t("tm_stats").format(count=stats["count"], mb=f"{mb:.1f}"))

    # ----------------------------------------------------------
    # ビジー状態制御（アクションボタンのみ）
    # ----------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for btn in self._action_btns:
            btn.config(state=state)
