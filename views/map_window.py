
"""
views/map_window.py
===================
マップウィンドウ。地図を軸にした補助レイヤ機能のホスト。

上部のモードセレクタでモードを切り替える。

- キャッシュ管理モード: 地図上で bbox を指定し DEM タイルの
    - カバレッジ確認（色付きオーバーレイ表示）
    - 不足タイルのダウンロード
    - 範囲削除 / 全削除
  を行う。
- 座標入力モード（Phase B）: 地図を素クリックして TX→RX を交互にピックし、
  ランチャーの座標欄（start/end）へ書き戻す。数値欄が常に source of truth で、
  地図はピッカーに徹する。
"""

import os
import threading
import tkinter as tk
from tkinter import ttk

from PIL import ImageTk

import i18n
import infrastructure as infra
import map_graphics
import models
from tkintermapview import TkinterMapView
from views import dialogs

# マーカー配色は map_graphics に集約（レポート地図生成 report_map.py と共通）。
_UISP_CYAN_HEX = map_graphics.UISP_CYAN_HEX
_MARKER_TEXT   = map_graphics.MARKER_TEXT

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
    def __init__(self, parent: tk.Misc, config: dict, launcher=None) -> None:
        self._config = config
        # 座標入力モードのピック結果を書き戻す先（SimLauncher）。None なら書き戻さない。
        self._launcher = launcher
        self._sync_proxy()

        self._win = tk.Toplevel(parent)
        self._win.title(i18n.t("map_title"))
        self._win.geometry("900x680")
        self._win.minsize(720, 520)

        # 現在のモード。"cache"=キャッシュ管理 / "coords"=座標入力（Phase B）。
        # 既定は coords＝座標入力（マップ連携の主機能。タイル管理は補助レイヤ）。
        self._mode = tk.StringVar(value="coords")

        # 座標入力モードの状態。次にどちらを置くか（交互）と、TX/RX のマーカー・線。
        self._pick_next = "tx"
        self._tx_coord: tuple | None = None
        self._rx_coord: tuple | None = None
        self._tx_marker = None
        self._rx_marker = None
        self._path_line = None
        self._dist_label = None         # path 中点の水平距離ラベル（マーカー）
        # UISP 風ノードアイコン（半透明ハロー＋シアンノード。TX=塗り / RX=白抜き）。
        # PhotoImage は GC されると消えるためインスタンスに保持する。
        self._tx_icon = self._make_node_icon(hollow=False)
        self._rx_icon = self._make_node_icon(hollow=True)
        # 距離ラベルのバッジ画像（テキスト＋半透明ピル背景）。距離ごとに作り直す。
        # PhotoImage は GC されると消えるためインスタンスに保持する。
        self._dist_badge = None

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
        # 既存の TX/RX 座標（数値欄）を取り込み、地図中心を合わせる。
        self._load_launcher_coords()
        # 地図レイアウト確定後に現在モードのレイヤを描画する
        # （cache=カバレッジ / coords=経路。既定は coords）。
        self._win.after(600, self._apply_mode_visibility)

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
    def _select_mode(self, value: str) -> None:
        """モードを選択し、セグメントボタンのスタイルを更新する。"""
        self._mode.set(value)
        for v, btn in self._mode_buttons.items():
            btn.configure(style="Accent.TButton" if v == value else "TButton")
        self._on_mode_change()

    def _on_mode_change(self) -> None:
        # UI 構築中（ステータスバー未生成）に呼ばれる初期スタイル反映時は何もしない。
        if not hasattr(self, "_status_label"):
            return
        self._apply_mode_visibility()
        # モードに応じてアイドルヒントを切り替える（_set_idle がモードを見る）。
        self._set_idle()

    def _apply_mode_visibility(self) -> None:
        """各モードは自分の関心レイヤだけを表示する（モードが見た目を決める）。

        - cache  : キャッシュカバレッジを描画し、経路レイヤ（マーカー/線/距離）は隠す。
        - coords : 経路レイヤを描画し、カバレッジ塗りは隠す。
        座標値（_tx_coord/_rx_coord）は保持するのでモードを往復しても失われない。
        """
        if self._mode.get() == "coords":
            self._clear_tile_overlays()
            self._show_coord_visuals()
        else:
            self._clear_coord_visuals()
            self._refresh_overlay()

    def _show_coord_visuals(self) -> None:
        """保持中の TX/RX 座標からマーカー・経路・距離ラベルを再構築する。"""
        if self._tx_coord is not None:
            self._set_pick_marker("tx", *self._tx_coord)
        if self._rx_coord is not None:
            self._set_pick_marker("rx", *self._rx_coord)

    def _clear_coord_visuals(self) -> None:
        """マーカー・経路・距離ラベルを地図から消す（座標値は保持する）。"""
        for obj in (self._tx_marker, self._rx_marker, self._path_line, self._dist_label):
            if obj is not None:
                obj.delete()
        self._tx_marker = None
        self._rx_marker = None
        self._path_line = None
        self._dist_label = None

    # ----------------------------------------------------------
    # 座標入力モード（地図クリックで TX/RX をピック → ランチャー数値欄へ書戻し）
    # 数値欄が source of truth。地図は交互ピッカーに徹する。
    # ----------------------------------------------------------
    def _click_on_zoom_button(self) -> bool:
        """直近の押下ピクセルが地図の +/- ズームボタン矩形内かを判定する。

        tkintermapview のズームボタンは canvas 埋込の CanvasButton で、自前の
        tag_bind とは別に canvas 全体の <Button-1>/<ButtonRelease-1> も発火する
        ため、ボタン上クリックが「移動なしクリック」として map_click_callback に
        流れ込み座標ピックされてしまう。押下位置がボタン矩形内なら無視する。"""
        pos = getattr(self._map, "last_mouse_down_position", None)
        if not pos:
            return False
        px, py = pos
        for name in ("button_zoom_in", "button_zoom_out"):
            btn = getattr(self._map, name, None)
            if btn is None:
                continue
            bx, by = btn.canvas_position
            if bx <= px <= bx + btn.width and by <= py <= by + btn.height:
                return True
        return False

    def _on_map_click(self, coords: tuple) -> None:
        """地図の素クリック。座標入力モードのときだけ TX→RX を交互にピックする。"""
        if self._mode.get() != "coords" or self._busy:
            return
        if self._click_on_zoom_button():
            return
        lat, lon = coords
        role = self._pick_next
        self._set_pick_marker(role, lat, lon)
        if self._launcher is not None:
            self._launcher.apply_map_pick(role, lat, lon)
        self._pick_next = "rx" if role == "tx" else "tx"
        self._set_idle()   # 次のピック対象をヒントに反映

    def _make_node_icon(self, hollow: bool) -> ImageTk.PhotoImage:
        """UISP 風のノードアイコンを Tk 用にラップして返す（描画は map_graphics）。"""
        return ImageTk.PhotoImage(map_graphics.node_icon(hollow))

    def _make_distance_badge(self, text: str) -> ImageTk.PhotoImage:
        """距離バッジを Tk 用にラップして返す（描画は map_graphics）。"""
        return ImageTk.PhotoImage(map_graphics.distance_badge(text))

    def _set_pick_marker(self, role: str, lat: float, lon: float) -> None:
        """TX/RX マーカーを設置（既存は置換）し、両方揃えばパス線を描く。"""
        if role == "tx":
            if self._tx_marker is not None:
                self._tx_marker.delete()
            self._tx_coord = (lat, lon)
            self._tx_marker = self._map.set_marker(
                lat, lon, text=i18n.t("map_marker_tx"),
                icon=self._tx_icon, icon_anchor="center",
                text_color=_MARKER_TEXT,
            )
        else:
            if self._rx_marker is not None:
                self._rx_marker.delete()
            self._rx_coord = (lat, lon)
            self._rx_marker = self._map.set_marker(
                lat, lon, text=i18n.t("map_marker_rx"),
                icon=self._rx_icon, icon_anchor="center",
                text_color=_MARKER_TEXT,
            )
        self._redraw_path()

    def _redraw_path(self) -> None:
        """TX/RX が揃っていれば 2 点を結ぶパス線と中点の距離ラベルを引き直す。"""
        if self._path_line is not None:
            self._path_line.delete()
            self._path_line = None
        if self._dist_label is not None:
            self._dist_label.delete()
            self._dist_label = None
        if self._tx_coord is not None and self._rx_coord is not None:
            # 既定 width=9 は太いので細線に。色は UISP 風シアンでノードと揃える。
            self._path_line = self._map.set_path(
                [self._tx_coord, self._rx_coord], color=_UISP_CYAN_HEX, width=3)
            # 水平距離ラベルを中点に重ねる（半透明ピル背景つき＝pan/zoom 追従）。
            mid = ((self._tx_coord[0] + self._rx_coord[0]) / 2,
                   (self._tx_coord[1] + self._rx_coord[1]) / 2)
            km = models.horizontal_distance_km(*self._tx_coord, *self._rx_coord)
            text = map_graphics.distance_text(km)
            self._dist_badge = self._make_distance_badge(text)
            self._dist_label = self._map.set_marker(
                mid[0], mid[1], icon=self._dist_badge, icon_anchor="center",
            )

    def _load_launcher_coords(self) -> None:
        """ランチャー数値欄の既存 TX/RX を取り込み、地図中心を合わせる。

        マーカー・経路の実描画はモードに応じて _apply_mode_visibility が行う
        （cache モードで開いたときは経路レイヤを出さない＝モード対称）。
        """
        if self._launcher is None:
            return
        coords = self._launcher.current_path_coords()
        tx, rx = coords.get("tx"), coords.get("rx")
        self._tx_coord, self._rx_coord = tx, rx
        # 次の入力対象: 未設定があればそれを優先、両方あれば TX から上書き再開。
        self._pick_next = "tx" if tx is None else ("rx" if rx is None else "tx")
        # 既存座標があれば中心を合わせる（両方あれば中点）。
        if tx is not None and rx is not None:
            self._map.set_position((tx[0] + rx[0]) / 2, (tx[1] + rx[1]) / 2)
        elif tx is not None:
            self._map.set_position(*tx)
        elif rx is not None:
            self._map.set_position(*rx)

    # ----------------------------------------------------------
    # UI 構築
    # ----------------------------------------------------------
    def _build_ui(self) -> None:
        # ---- 上部モードセレクタ（セグメントボタン列）---------------------
        # 地図を軸にした補助機能のモードを切り替える。選択中モードは Accent
        # （青塗り）で押下状態を表し、ボタンとして明確に認識できるようにする。
        # Phase A はキャッシュ管理の 1 つのみ。Phase B でリストに「座標入力」を足す。
        modebar = ttk.Frame(self._win)
        modebar.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(modebar, text=i18n.t("map_mode_label")).pack(side="left", padx=(2, 6))
        self._mode_buttons: dict[str, ttk.Button] = {}
        # 座標入力＝主機能なので左に並べる。
        for value, key in [("coords", "map_mode_coords"), ("cache", "map_mode_cache")]:
            b = ttk.Button(
                modebar, text=i18n.t(key),
                command=lambda v=value: self._select_mode(v),
            )
            b.pack(side="left", padx=2)
            self._mode_buttons[value] = b
        self._select_mode(self._mode.get())   # 初期選択のスタイルを反映

        self._map = TkinterMapView(self._win, corner_radius=0)
        # 地図タイルは GSI 淡色地図に統一（DEM 出典と揃え、外部 API を GSI 一本化）。
        self._map.set_tile_server(
            "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", max_zoom=18
        )
        self._map.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        self._map.set_position(35.68, 139.77)
        self._map.set_zoom(8)
        # 座標入力モードでの素クリック → ピック。ライブラリがドラッグ（パン）と
        # クリックを内部で区別するため、キャッシュ管理の修飾キー操作とは競合しない。
        self._map.add_left_click_map_command(self._on_map_click)

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
        if self._mode.get() == "coords":
            return   # 座標入力モードではカバレッジを描かない（無駄なタイマーも張らない）
        if self._overlay_after_id is not None:
            self._win.after_cancel(self._overlay_after_id)
        self._overlay_after_id = self._win.after(300, self._refresh_overlay)

    def _refresh_overlay(self) -> None:
        self._overlay_after_id = None
        if self._mode.get() == "coords":
            return   # 座標入力モードではカバレッジ描画をスキップ
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
        if self._mode.get() == "coords":
            return   # モード切替後に届いた旧ワーカー結果は捨てる（描画しない）
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
        """アイドル状態: モードに応じた操作ヒントをグレーで表示する。"""
        self._status_clear_id = None
        self._status_label.config(foreground="gray")
        if self._mode.get() == "coords":
            key = "map_coords_hint_tx" if self._pick_next == "tx" else "map_coords_hint_rx"
            self._status_var.set(i18n.t(key))
        else:
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
