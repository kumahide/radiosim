"""
views/map_window.py
===================
マップウィンドウ。地図を軸にした補助レイヤ機能のホスト。

上部のモードセレクタでモードを切り替える（Phase A 時点はキャッシュ管理のみ。
座標入力モードは Phase B で追加予定）。

キャッシュ管理モードでは、地図上で bbox を指定し DEM タイルの
  - カバレッジ確認（色付きオーバーレイ表示）
  - 不足タイルのダウンロード
  - 範囲削除 / 全削除
を行う。
"""

import os
import threading
import tkinter as tk
from tkinter import ttk

import i18n
import infrastructure as infra
from tkintermapview import TkinterMapView
from views import dialogs

# zoom-14 オーバーレイの色（最高精度レベルで色分け）。
# scan_cache_overlay はキャッシュ済みセルのみ返す（未取得は描画しない）ため、
# ここに含めるのは 5a/5b/dem の 3 レベルだけ。
_LEVEL_COLORS: dict[str, str] = {
    "5a":   "#90EE90",  # 緑: 5m航空（dem5a_png）
    "5b":   "#FFD700",  # 黄: 5m写真（dem5b_png）
    "dem":  "#87CEEB",  # 水色: 10m（dem_png）
}

# キャッシュ済み領域の外周線の色
_OUTLINE_COLOR = "#0066CC"


class MapWindow:
    def __init__(self, parent: tk.Misc, config: dict) -> None:
        self._config = config
        self._sync_proxy()

        self._win = tk.Toplevel(parent)
        self._win.title(i18n.t("map_title"))
        self._win.geometry("900x680")
        self._win.minsize(720, 520)

        # 現在のモード。Phase A はキャッシュ管理のみ。Phase B で "coords" 等を追加する。
        self._mode = tk.StringVar(value="cache")

        self._bbox_polygon = None
        self._tile_polygons: list = []

        # Ctrl＋ドラッグ矩形選択の状態
        self._sel_start = None    # (lat, lon) ドラッグ開始点
        self._sel_rect  = None    # ドラッグ中のライブ矩形ポリゴン

        self._lat1_var = tk.StringVar()
        self._lon1_var = tk.StringVar()
        self._lat2_var = tk.StringVar()
        self._lon2_var = tk.StringVar()

        self._busy = False              # DL 実行中フラグ（多重操作防止）
        self._overlay_after_id = None   # 自動カバレッジ表示のデバウンス用
        self._status_clear_id = None    # 結果文の自動クリア用 after ID

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
    # モード切替（Phase A はキャッシュ管理のみ。Phase B で分岐を追加）
    # ----------------------------------------------------------
    def _on_mode_change(self) -> None:
        # 現状は単一モードのため何もしない。Phase B 以降、ジェスチャの意味づけや
        # ステータス表示をモードに応じて切り替えるフックとして使う。
        pass

    # ----------------------------------------------------------
    # UI 構築
    # ----------------------------------------------------------
    def _build_ui(self) -> None:
        # ---- 上部モードセレクタ ------------------------------------------
        # 地図を軸にした補助機能のモードを切り替える。Phase A はキャッシュ管理の
        # 1 つのみ。Phase B で「座標入力」モードを add_radiobutton で追加する。
        modebar = ttk.Frame(self._win)
        modebar.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(modebar, text=i18n.t("map_mode_label")).pack(side="left", padx=(2, 6))
        for value, key in [("cache", "map_mode_cache")]:
            ttk.Radiobutton(
                modebar, text=i18n.t(key), value=value, variable=self._mode,
                style="Toolbutton", command=self._on_mode_change,
            ).pack(side="left", padx=2)

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

        # 出典表記（GSI 帰属）は地図右下にオーバーレイ表示する（地図出典の慣例位置）。
        # ウィジェットでは背景を透過できないため、canvas に直接テキスト描画する。
        self._attribution = cv.create_text(
            0, 0, text=i18n.t("tm_attribution"),
            anchor="se", fill="gray", tags="attribution",
        )
        cv.bind("<Configure>", self._reposition_attribution, add="+")

        # ---- 下部ステータスバー（1 本に集約）----------------------------
        # 出没でレイアウトが動かないよう、各要素の高さを予約して配置する。
        bottom = ttk.Frame(self._win)
        bottom.pack(fill="x", padx=6, pady=(2, 4))

        statusbar = ttk.Frame(bottom)
        statusbar.pack(fill="x")

        # 右: キャッシュ統計（常時表示のアンカー）
        self._stats_var = tk.StringVar(value="")
        ttk.Label(statusbar, textvariable=self._stats_var, anchor="e").pack(side="right")

        # 左: 動的メッセージ（アイドル時=操作ヒント / 操作中・直後=状態・結果）。
        # 複数行になり得るため justify=left。アイドル時はグレー表示。
        self._status_var = tk.StringVar(value="")
        self._status_label = ttk.Label(
            statusbar, textvariable=self._status_var, anchor="w", justify="left",
            wraplength=600,
        )
        self._status_label.pack(side="left", fill="x", expand=True)
        # 幅に追従して折り返し幅を更新（統計表示分を右に確保する）。
        statusbar.bind(
            "<Configure>",
            lambda e: self._status_label.config(wraplength=max(200, e.width - 200)),
        )

        # プログレスバー: 細線。アイドル時も高さを予約して畳み、DL 中のみ表示する。
        style = ttk.Style()
        style.configure("Thin.Horizontal.TProgressbar", thickness=6)
        self._progress_holder = ttk.Frame(bottom, height=8)
        self._progress_holder.pack(fill="x", pady=(2, 0))
        self._progress_holder.pack_propagate(False)   # 中身の有無に関わらず高さ固定
        self._progress_var = tk.IntVar(value=0)
        self._progress = ttk.Progressbar(
            self._progress_holder, variable=self._progress_var, maximum=100,
            style="Thin.Horizontal.TProgressbar",
        )

        self._set_idle()   # 起動時はヒントを表示

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
            total = infra.count_bbox_tiles(*bbox)
            n = total if force else total - infra.count_cached_areas(*bbox)
            title = i18n.t("tm_dl_force_title") if force else i18n.t("tm_dl_title")
            msg = (i18n.t("tm_dl_force_confirm") if force else i18n.t("tm_dl_confirm")).format(n=n)
            msg += "\n" + i18n.t("tm_dl_size_hint").format(mb=self._estimate_mb(n))
            if dialogs.confirm(self._win, title, msg):
                self._start_download(bbox, force)
            else:
                self._clear_selection()
        else:   # delete
            # 削除は実際にキャッシュ済みのエリアのみが対象
            n = infra.count_cached_areas(*bbox)
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

    def _reposition_attribution(self, event=None) -> None:
        """出典テキストを地図右下に再配置し、最前面へ持ち上げる。"""
        cv = self._map.canvas
        cv.coords(self._attribution, cv.winfo_width() - 4, cv.winfo_height() - 4)
        cv.tag_raise(self._attribution)

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
        # カバレッジ描画でタイル/ポリゴンが上に来るため出典を持ち上げ直す。
        self._reposition_attribution()

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

    # ----------------------------------------------------------
    # ステータス表示ヘルパー
    # ----------------------------------------------------------
    _STATUS_CLEAR_MS = 8000   # 結果文を自動的に消すまでの時間

    def _show_progress(self) -> None:
        """細線プログレスバーを高さ予約済みのホルダー内に表示する。"""
        if not self._progress.winfo_ismapped():
            self._progress.pack(fill="x")

    def _hide_progress(self) -> None:
        # ホルダーは pack_propagate(False) で高さを保つため、外してもリフローしない。
        if self._progress.winfo_ismapped():
            self._progress.pack_forget()

    def _set_idle(self) -> None:
        """アイドル状態: 操作ヒントをグレーで表示する。"""
        self._status_clear_id = None
        self._status_label.config(foreground="gray")
        self._status_var.set(i18n.t("tm_hint"))

    def _set_status(self, text: str, auto_clear: bool = False) -> None:
        """状態・結果文を通常色で設定。auto_clear=True なら一定時間後にヒントへ戻す。"""
        if self._status_clear_id is not None:
            self._win.after_cancel(self._status_clear_id)
            self._status_clear_id = None
        self._status_label.config(foreground="")   # テーマ既定色に戻す
        self._status_var.set(text)
        if auto_clear and text:
            self._status_clear_id = self._win.after(self._STATUS_CLEAR_MS, self._set_idle)

    def _start_download(self, bbox: tuple, force: bool) -> None:
        self._set_busy(True)
        self._progress_var.set(0)
        self._show_progress()
        self._set_status(i18n.t("tm_downloading"))
        threading.Thread(target=self._download_worker, args=(bbox, force), daemon=True).start()

    def _download_worker(self, bbox: tuple, force: bool) -> None:
        def progress_cb(done: int, total: int) -> None:
            pct = int(done / total * 100) if total else 0
            self._win.after(0, self._progress_var.set, pct)
            self._win.after(0, self._set_status,
                i18n.t("tm_dl_progress").format(done=done, total=total, pct=pct))
        dl_result = infra.prefetch_tiles(*bbox, progress_cb=progress_cb, force=force)
        self._win.after(0, self._on_download_done, dl_result)

    def _on_download_done(self, dl_result: dict) -> None:
        self._set_busy(False)
        self._hide_progress()
        self._set_status(i18n.t("tm_dl_done").format(
            dl5a=dl_result["downloaded_5a"],
            dl5b=dl_result["downloaded_5b"],
            dl_dem=dl_result["downloaded_dem"],
            skipped=dl_result["skipped"],
            failed=dl_result["failed"],
        ), auto_clear=True)
        self._refresh_stats()
        self._refresh_overlay()   # DL 結果を自動カバレッジ表示に反映
        self._clear_selection()   # DL 完了後は選択枠を消す

    def _clear_selection(self) -> None:
        """選択枠と座標をクリアする（ステータス文には触れない）。"""
        if self._bbox_polygon is not None:
            self._bbox_polygon.delete()
            self._bbox_polygon = None
        for var in (self._lat1_var, self._lon1_var, self._lat2_var, self._lon2_var):
            var.set("")

    # ----------------------------------------------------------
    # 範囲削除（Shift+Ctrl＋ドラッグ → 確認 → 実行）
    # ----------------------------------------------------------
    def _do_delete(self, bbox: tuple) -> None:
        result = infra.delete_tile_cache(*bbox)
        self._set_status(i18n.t("tm_delete_done").format(deleted=result["deleted"]), auto_clear=True)
        self._refresh_stats()
        self._refresh_overlay()   # 削除結果を自動表示に反映
        self._clear_selection()   # 削除後は選択枠を消す

    # ----------------------------------------------------------
    # 全削除（実行はランチャーの設定メニュー側。ここは外部削除後の再描画のみ）
    # ----------------------------------------------------------
    def on_external_delete_all(self, deleted: int) -> None:
        """ランチャーから全キャッシュ削除された後、開いている管理画面を更新する。"""
        self._clear_tile_overlays()
        self._set_status(i18n.t("tm_delete_all_done").format(deleted=deleted), auto_clear=True)
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
    # ビジー状態制御（DL 実行中は新たなジェスチャ操作を受け付けない）
    # ----------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
