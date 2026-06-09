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


class TileManagerWindow:
    def __init__(self, parent: tk.Misc, config: dict) -> None:
        self._config = config
        self._sync_proxy()

        self._win = tk.Toplevel(parent)
        self._win.title(i18n.t("tm_title"))
        self._win.geometry("900x680")
        self._win.minsize(720, 520)

        self._click_state  = 0   # 0=NW待ち, 1=SE待ち
        self._bbox_polygon = None
        self._tile_polygons: list = []
        self._nw_marker    = None
        self._se_marker    = None

        self._lat1_var = tk.StringVar()
        self._lon1_var = tk.StringVar()
        self._lat2_var = tk.StringVar()
        self._lon2_var = tk.StringVar()

        self._action_btns: list[ttk.Button] = []
        self._force_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._refresh_stats()

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
        self._map.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        self._map.set_position(35.68, 139.77)
        self._map.set_zoom(8)
        self._map.add_left_click_map_command(self._on_map_click)

        bottom = ttk.Frame(self._win)
        bottom.pack(fill="x", padx=6, pady=4)

        # 座標入力行
        coord_row = ttk.Frame(bottom)
        coord_row.pack(fill="x", pady=(0, 4))

        ttk.Label(coord_row, text=i18n.t("tm_nw")).pack(side="left")
        ttk.Entry(coord_row, textvariable=self._lat1_var, width=10).pack(side="left", padx=(2, 0))
        ttk.Entry(coord_row, textvariable=self._lon1_var, width=10).pack(side="left", padx=(2, 10))
        ttk.Label(coord_row, text=i18n.t("tm_se")).pack(side="left")
        ttk.Entry(coord_row, textvariable=self._lat2_var, width=10).pack(side="left", padx=(2, 0))
        ttk.Entry(coord_row, textvariable=self._lon2_var, width=10).pack(side="left", padx=(2, 8))
        ttk.Button(coord_row, text=i18n.t("tm_btn_update_bbox"),
                   command=self._on_update_bbox).pack(side="left", padx=(0, 10))
        self._tile_count_label = ttk.Label(coord_row, text="")
        self._tile_count_label.pack(side="left")

        # ボタン行
        btn_row = ttk.Frame(bottom)
        btn_row.pack(fill="x", pady=(0, 4))

        for label_key, cmd in [
            ("tm_btn_check",    self._on_check),
            ("tm_btn_download", self._on_download),
            ("tm_btn_delete",   self._on_delete),
        ]:
            b = ttk.Button(btn_row, text=i18n.t(label_key), command=cmd)
            b.pack(side="left", padx=(0, 4))
            self._action_btns.append(b)

        ttk.Checkbutton(
            btn_row, text=i18n.t("tm_force_download"),
            variable=self._force_var,
        ).pack(side="left", padx=(0, 8))

        ttk.Separator(btn_row, orient="vertical").pack(side="left", fill="y", padx=8)

        b_all = ttk.Button(btn_row, text=i18n.t("tm_btn_delete_all"), command=self._on_delete_all)
        b_all.pack(side="left")
        self._action_btns.append(b_all)

        # プログレスバー
        self._progress_var = tk.IntVar(value=0)
        self._progress = ttk.Progressbar(bottom, variable=self._progress_var, maximum=100)
        self._progress.pack(fill="x", pady=(0, 2))

        # ステータス
        self._status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self._status_var, anchor="w",
                  wraplength=860).pack(fill="x")

        # キャッシュ統計
        self._stats_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self._stats_var, anchor="w").pack(fill="x")

        # 操作ヒント
        ttk.Label(bottom, text=i18n.t("tm_hint"),
                  foreground="gray").pack(anchor="w")

    # ----------------------------------------------------------
    # 地図クリック
    # ----------------------------------------------------------
    def _on_map_click(self, coords: tuple) -> None:
        lat, lon = coords
        if self._click_state == 0:
            self._lat1_var.set(f"{lat:.6f}")
            self._lon1_var.set(f"{lon:.6f}")
            if self._nw_marker:
                self._nw_marker.delete()
            self._nw_marker  = self._map.set_marker(lat, lon, text="NW")
            self._click_state = 1
        else:
            self._lat2_var.set(f"{lat:.6f}")
            self._lon2_var.set(f"{lon:.6f}")
            if self._se_marker:
                self._se_marker.delete()
            self._se_marker  = self._map.set_marker(lat, lon, text="SE")
            self._click_state = 0
            self._draw_bbox_rect()
            self._update_tile_count()

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

    def _on_update_bbox(self) -> None:
        self._draw_bbox_rect()
        self._update_tile_count()

    # ----------------------------------------------------------
    # bbox 取得ヘルパー
    # ----------------------------------------------------------
    def _get_bbox(self) -> tuple[float, float, float, float] | None:
        try:
            return (
                float(self._lat1_var.get()),
                float(self._lon1_var.get()),
                float(self._lat2_var.get()),
                float(self._lon2_var.get()),
            )
        except ValueError:
            messagebox.showerror(
                i18n.t("dlg_error"), i18n.t("tm_err_no_bbox"), parent=self._win
            )
            return None

    # ----------------------------------------------------------
    # タイルオーバーレイ
    # ----------------------------------------------------------
    def _clear_tile_overlays(self) -> None:
        for p in self._tile_polygons:
            p.delete()
        self._tile_polygons.clear()

    def _draw_tile_overlays(self, positions: list) -> None:
        for pos in positions:
            x14, y14 = pos["x14"], pos["y14"]
            lat_n, lon_w = infra.tile_to_latlng(x14,     y14,     14)
            lat_s, lon_e = infra.tile_to_latlng(x14 + 1, y14 + 1, 14)
            color = _LEVEL_COLORS.get(pos["level"], "#CCCCCC")
            p = self._map.set_polygon(
                [(lat_n, lon_w), (lat_n, lon_e), (lat_s, lon_e), (lat_s, lon_w)],
                fill_color=color,
                outline_color=color,
            )
            self._tile_polygons.append(p)

    # ----------------------------------------------------------
    # カバレッジ確認
    # ----------------------------------------------------------
    def _on_check(self) -> None:
        bbox = self._get_bbox()
        if bbox is None:
            return
        self._set_busy(True)
        self._status_var.set(i18n.t("tm_checking"))
        threading.Thread(target=self._check_worker, args=(bbox,), daemon=True).start()

    def _check_worker(self, bbox: tuple) -> None:
        result = infra.check_cache_coverage(*bbox)
        self._win.after(0, self._on_check_done, result)

    def _on_check_done(self, result: dict) -> None:
        self._set_busy(False)
        self._status_var.set(i18n.t("tm_check_result").format(
            area_5a=result.get("area_5a", 0),
            area_5b=result.get("area_5b", 0),
            area_dem=result.get("area_dem", 0),
            area_none=result.get("area_none", 0),
            area_total=result.get("area_total", 0),
        ))
        self._clear_tile_overlays()
        self._draw_tile_overlays(result.get("positions", []))

    # ----------------------------------------------------------
    # ダウンロード
    # ----------------------------------------------------------
    def _on_download(self) -> None:
        bbox = self._get_bbox()
        if bbox is None:
            return
        self._set_busy(True)
        self._progress_var.set(0)
        self._status_var.set(i18n.t("tm_downloading"))
        force = self._force_var.get()
        threading.Thread(target=self._download_worker, args=(bbox, force), daemon=True).start()

    def _download_worker(self, bbox: tuple, force: bool) -> None:
        def progress_cb(done: int, total: int) -> None:
            pct = int(done / total * 100) if total else 0
            self._win.after(0, self._progress_var.set, pct)
            self._win.after(0, self._status_var.set,
                i18n.t("tm_dl_progress").format(done=done, total=total, pct=pct))
        dl_result  = infra.prefetch_tiles(*bbox, progress_cb=progress_cb, force=force)
        cov_result = infra.check_cache_coverage(*bbox)
        self._win.after(0, self._on_download_done, dl_result, cov_result)

    def _on_download_done(self, dl_result: dict, cov_result: dict) -> None:
        self._set_busy(False)
        self._progress_var.set(100)
        dl_msg = i18n.t("tm_dl_done").format(
            dl5a=dl_result["downloaded_5a"],
            dl5b=dl_result["downloaded_5b"],
            dl_dem=dl_result["downloaded_dem"],
            skipped=dl_result["skipped"],
            failed=dl_result["failed"],
        )
        self._refresh_stats()
        self._on_check_done(cov_result)
        self._status_var.set(dl_msg + "\n" + self._status_var.get())

    # ----------------------------------------------------------
    # 範囲削除
    # ----------------------------------------------------------
    def _on_delete(self) -> None:
        bbox = self._get_bbox()
        if bbox is None:
            return
        if not messagebox.askyesno(
            i18n.t("tm_delete_title"), i18n.t("tm_delete_confirm"), parent=self._win
        ):
            return
        result = infra.delete_tile_cache(*bbox)
        self._clear_tile_overlays()
        self._status_var.set(i18n.t("tm_delete_done").format(deleted=result["deleted"]))
        self._refresh_stats()

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
