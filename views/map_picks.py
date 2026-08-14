"""
views/map_picks.py
==================
地図窓の**地点の指定と経路の描画**（`MapWindow` の Mixin）。

クリックでの TX/RX 指定・中継点の追加・確定済み経路と距離バッジの描画・
表示範囲の合わせ込み。

⚠️ **これは `MapWindow` の一部**であって独立した部品ではない。切り出しは
2.7 スライス A（メソッド本文は 1 文字も変えていない＝「移動だけ」）。
"""

import tkinter as tk
from typing import TYPE_CHECKING

from PIL import ImageTk

from core import i18n
from core import models
from core import units
from report import map_graphics
from views.map_style import (_FIT_MARGIN, _FIT_MIN_SPAN, _MARKER_TEXT,
                             _SINGLE_ZOOM, _MAP_CYAN_HEX)

if TYPE_CHECKING:
    from tkintermapview import TkinterMapView
    from tkintermapview.canvas_path import CanvasPath
    from tkintermapview.canvas_position_marker import CanvasPositionMarker

    from views.map_window import _AppendSink, _SingleSink, _WaypointSink


class _PickMixin:
    # 宿主（`MapWindow`）から借りている面の宣言。**型検査のときだけ**存在する
    # （実行時は 1 文字も定義しない＝Mixin の振る舞いは変わらない）。
    # Mixin は `self.*` を宿主と共有する形で切り出したので、これを書かないと
    # 型検査器から見て `self._map` は「無い属性」になる（2.7 スライス A の分割で
    # pyright が 365 件のエラーを出した＝B-049）。**借りている面を明示的に並べる
    # ことが、Mixin と宿主のあいだの契約そのもの**でもある。
    if TYPE_CHECKING:
        _map: "TkinterMapView"
        _mode: tk.StringVar
        _busy: bool
        _pick_next: str
        _single_sink: "_SingleSink | None"
        _append_sink: "_AppendSink | None"
        _waypoint_sink: "_WaypointSink | None"
        _tx_coord: "tuple | None"
        _rx_coord: "tuple | None"
        _tx_marker: "CanvasPositionMarker | None"
        _rx_marker: "CanvasPositionMarker | None"
        _path_line: "CanvasPath | None"
        _dist_label: "CanvasPositionMarker | None"
        _dist_badge: "ImageTk.PhotoImage | None"
        _tx_icon: ImageTk.PhotoImage
        _rx_icon: ImageTk.PhotoImage
        _relay_icon: ImageTk.PhotoImage
        _committed: list
        _committed_images: list
        _wp_objects: list
        _wp_images: list
        _redraw_state: dict

        def _select_mode(self, value: str) -> None: ...
        def _set_idle(self) -> None: ...
        def _set_status(self, text: str, auto_clear: bool = False) -> None: ...

    def on_waypoints_changed(self) -> None:
        """中継経路ウィンドウの地点列が変わったときの通知（追加・削除・編集）。"""
        self._refresh_waypoints()

    def _redraw_serialized(self, key: str, draw) -> None:
        """レイヤの描き直しを**直列化**する（B-080）。

        描き直しの途中で `CanvasPositionMarker.delete()` が `canvas.update()` を
        回すため、**消している最中に次の通知が届いて再入する**（素早い追加・削除で
        実際に起きる）。再入をそのまま走らせると、内側が描いた分の上に外側が
        もう 1 組を重ね、同じ経路が二重に載る（しかも外側の方が**古い状態**）。

        ⇒ **走っている最中の要求は「あとで 1 回」に畳む**＝最後に読んだ地点列が
        そのまま画面になる（地図は写しであって source of truth ではない、という
        既存の約束をそのまま守る）。
        """
        state = self._redraw_state
        if state.get(key) is not None:
            state[key] = "pending"          # 割り込みは覚えておき、最後にやり直す
            return
        state[key] = "drawing"
        try:
            draw()
            while state[key] == "pending":
                state[key] = "drawing"
                draw()
        finally:
            state[key] = None

    def _refresh_waypoints(self) -> None:
        """宛先の地点列から中継経路レイヤを描き直す。"""
        self._redraw_serialized("waypoints", self._draw_waypoints)

    def _draw_waypoints(self) -> None:
        self._clear_waypoint_visuals()
        if self._mode.get() != "waypoints" or self._waypoint_sink is None:
            return
        points = self._waypoint_sink.waypoint_markers()
        coords = [(lat, lon) for _name, lat, lon in points]
        if len(coords) >= 2:
            # 折れ線＝**並びがそのまま経路**であることを地図でも見せる。
            self._wp_objects.append(
                self._map.set_path(coords, color=_MAP_CYAN_HEX, width=3))
            # 区間ごとの水平距離を中点に置く（他の 2 モードと同じ部品・同じ位置）。
            # ⚠️ **この距離は画面のここにしか出ない**＝区間表の列は周波数・利得・
            # 結果だけで距離を持たず、レポートに出るのは*斜距離*（用語集で別語）。
            # ⇒ 重複表示ではなく、地図で初めて読める情報。
            # ⚠️ 区間番号は添えない＝折れ線で順番が読め、地点名も既に出ている
            # （複数経路の経路 ID は N 本を見分けるために要るが、ここは要らない）。
            for a, b in zip(coords, coords[1:]):
                mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                km = models.horizontal_distance_km(a[0], a[1], b[0], b[1])
                badge = self._make_distance_badge(units.format_distance(km))
                self._wp_images.append(badge)      # GC 防止に保持
                self._wp_objects.append(self._map.set_marker(
                    mid[0], mid[1], icon=badge, icon_anchor="center"))
        last = len(points) - 1
        for i, (name, lat, lon) in enumerate(points):
            # 先頭＝送信点（塗り）／末尾＝受信点（白抜き）／間＝中継点（リング）。
            # 形だけで役割が読めるようにする（窓の役割ラベルと同じ意味を地図でも）。
            if i == 0:
                icon = self._tx_icon
            elif i == last:
                icon = self._rx_icon
            else:
                icon = self._relay_icon
            self._wp_objects.append(self._map.set_marker(
                lat, lon, text=name, icon=icon, icon_anchor="center",
                text_color=_MARKER_TEXT))

    def _clear_waypoint_visuals(self) -> None:
        # 🔴 **消す前に台帳ごと取り出す**（B-080）＝回しながら消してはいけない。
        # `CanvasPositionMarker.delete()` は中で `canvas.update()` を回す
        # （tkintermapview の実装）ので、**消している最中にイベントが 1 巡する**。
        # そこへ地点列の変更通知が届くと `_refresh_waypoints` が再入し、内側が
        # このリストを空にする ⇒ 外側の `for` は同じリストを見ているので途中で
        # 終わり、**未削除のまま `clear()` で参照だけ捨てる**。捨てられた
        # `CanvasPath` は `canvas_path_list` に生き残り、以後ずっと描かれ続ける
        # （＝過去の経路が地図に残り、パンすると一緒に動く・2026-08-13 実機報告）。
        objs, self._wp_objects = self._wp_objects, []
        self._wp_images = []
        for obj in objs:
            try:
                obj.delete()
            except Exception:
                pass

    def on_append_target_closed(self) -> None:
        """append 先（バッチ）が閉じたときの通知。連続追加中なら座標入力へ戻す。"""
        self._append_sink = None
        if self._mode.get() == "append":
            self._select_mode("coords")

    def on_paths_changed(self) -> None:
        """append 先（バッチ）のパス集合が変わったときの通知。確定パス表示を引き直す。

        連続追加モードでないとき（_append_sink is None）は _refresh_committed_paths
        が早期 return するので no-op。地図側で source of truth を持たないため、毎回
        バッチの現在の行から描き直すだけ（削除・クリア・インポートに追従する）。
        """
        self._refresh_committed_paths()

    def _show_coord_visuals(self) -> None:
        """保持中の TX/RX 座標からマーカー・経路・距離ラベルを再構築する。"""
        if self._tx_coord is not None:
            self._set_pick_marker("tx", *self._tx_coord)
        if self._rx_coord is not None:
            self._set_pick_marker("rx", *self._rx_coord)

    def _clear_coord_visuals(self) -> None:
        """マーカー・経路・距離ラベル・確定パスを地図から消す（座標値は保持する）。"""
        # **手放してから消す**（B-080）＝消している最中に再入されても、内側が
        # 作り直した新しいマーカー・線を外側が None で上書きして取り落とさない。
        objs = (self._tx_marker, self._rx_marker, self._path_line, self._dist_label)
        self._tx_marker = None
        self._rx_marker = None
        self._path_line = None
        self._dist_label = None
        for obj in objs:
            if obj is not None:
                obj.delete()
        self._clear_committed_paths()

    # ----------------------------------------------------------
    # 追記モード（Phase D2）: 確定済みパスのライン表示
    # ----------------------------------------------------------
    def _clear_committed_paths(self) -> None:
        # 台帳ごと取り出してから消す（理由は `_clear_waypoint_visuals`＝B-080）。
        # ここもマーカーを含むので、消す途中でイベントが回る面が同じだけある。
        objs, self._committed = self._committed, []
        self._committed_images = []
        for obj in objs:
            obj.delete()

    def _refresh_committed_paths(self) -> None:
        """シンクが持つ既存パス（バッチ各行の座標）を地図上に表示する。

        確定パスは **TX=塗りドット／RX=白抜きドット** ＋経路線＋中点の水平距離
        バッジで残す＝**座標入力モードと同じ描き方**（文字ラベルだけは出さない）。
        追記モードでのみ意味を持ち、バッチ表が source of truth なので毎回引き直す
        だけ。パース不能行は除外済み。距離バッジに path_id を添えて、バッチ表の
        どの行に対応するパスかを地図上で判別できるようにする（I-001）。

        🔁 **RX は 2.8b1 まで「方位矢じり」だった**（2026-08-14 にユーザー判断で
        撤回）。矢じりは *TX/RX が同一座標に重なっても判別できる* ための例外で、
        複数経路を中継の代わりに使っていた時期に要ったもの。中継経路ウィンドウが
        できて**その使い方をしなくなった**ので、例外を残す根拠が消えた。
        ⇒ **根拠の消えた例外は畳む**（残る不統一のほうが読み手のコストになる）。
        ⚠️ 失うのは「重なったときの判別」だけ＝送受の区別（塗り/白抜き）も向き
        （塗り→白抜き）も形で読める。⚠️ **文字ラベルは付けない**＝N 本あるので、
        全端点に `TX`/`RX` を出すと地図が字で埋まる。識別は距離バッジの path_id。
        """
        self._redraw_serialized("committed", self._draw_committed_paths)

    def _draw_committed_paths(self) -> None:
        self._clear_committed_paths()
        if self._append_sink is None:
            return
        for pid, tx, rx in self._append_sink.existing_paths():
            self._committed.append(
                self._map.set_path([tx, rx], color=_MAP_CYAN_HEX, width=3))
            # 端点はアクティブピックと同じアイコン（TX=塗り / RX=白抜き）。
            self._committed.append(self._map.set_marker(
                tx[0], tx[1], icon=self._tx_icon, icon_anchor="center"))
            self._committed.append(self._map.set_marker(
                rx[0], rx[1], icon=self._rx_icon, icon_anchor="center"))
            # 中点に path_id ＋ 水平距離バッジ。
            mid = ((tx[0] + rx[0]) / 2, (tx[1] + rx[1]) / 2)
            km = models.horizontal_distance_km(tx[0], tx[1], rx[0], rx[1])
            dist_text = units.format_distance(km)
            label = f"{pid}  {dist_text}" if pid else dist_text
            badge = self._make_distance_badge(label)
            self._committed_images.append(badge)   # GC 防止に保持
            self._committed.append(self._map.set_marker(
                mid[0], mid[1], icon=badge, icon_anchor="center"))

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
        """地図の素クリック。座標入力／連続追加モードで TX→RX を交互にピックする。"""
        if self._mode.get() == "cache" or self._busy:
            return
        if self._click_on_zoom_button():
            return
        lat, lon = coords
        if self._mode.get() == "waypoints":
            # 中継点＝**1 点ずつ順に足す**（TX/RX の交互ピックではない）。
            if self._waypoint_sink is None:
                return
            name = self._waypoint_sink.append_waypoint(lat, lon)
            # ⚠️ ここでマーカーを**足さない**＝窓へ入れてから写しを描き直す。
            # 足すだけにすると、窓側で地点を削除しても地図に残る（消し方が無い）。
            self._refresh_waypoints()
            self._set_status(i18n.t("map_append_added").format(pid=name),
                             auto_clear=True)
            return
        role = self._pick_next
        self._set_pick_marker(role, lat, lon)
        if self._append_sink is not None:
            # 連続追加（Phase D2）: RX 確定でペア成立 → 1 行を append し、
            # アクティブなピックをリセットして次の TX 待ちに戻す（add 不要）。
            if role == "rx" and self._tx_coord is not None and self._rx_coord is not None:
                pid = self._append_sink.append_path(self._tx_coord, self._rx_coord)
                self._reset_active_pick()
                self._refresh_committed_paths()
                if pid:
                    self._set_status(
                        i18n.t("map_append_added").format(pid=pid), auto_clear=True)
            else:
                self._pick_next = "rx"
        else:
            # 単一書き戻し（ランチャー）: ピックごとに start/end 欄へ反映。
            if self._single_sink is not None:
                self._single_sink.apply_map_pick(role, lat, lon)
            self._pick_next = "rx" if role == "tx" else "tx"
        self._set_idle()   # 次のピック対象をヒントに反映

    def _reset_active_pick(self) -> None:
        """アクティブな TX/RX ピック（マーカー・経路・座標）をクリアし TX 待ちへ戻す。

        確定済みパス（_committed）には触れない＝append 後も軌跡は地図に残る。
        """
        objs = (self._tx_marker, self._rx_marker, self._path_line, self._dist_label)
        self._tx_marker = self._rx_marker = self._path_line = self._dist_label = None
        for obj in objs:                       # 手放してから消す（B-080）
            if obj is not None:
                obj.delete()
        self._tx_coord = self._rx_coord = None
        self._pick_next = "tx"

    def _make_node_icon(self, hollow: bool) -> ImageTk.PhotoImage:
        """端点のノードアイコンを Tk 用にラップして返す（描画は map_graphics）。"""
        return ImageTk.PhotoImage(map_graphics.node_icon(hollow))

    def _make_distance_badge(self, text: str) -> ImageTk.PhotoImage:
        """距離バッジを Tk 用にラップして返す（描画は map_graphics）。"""
        return ImageTk.PhotoImage(map_graphics.distance_badge(text))

    def _set_pick_marker(self, role: str, lat: float, lon: float) -> None:
        """TX/RX マーカーを設置（既存は置換）し、両方揃えばパス線を描く。"""
        if role == "tx":
            old, self._tx_marker = self._tx_marker, None   # 手放してから消す（B-080）
            if old is not None:
                old.delete()
            self._tx_coord = (lat, lon)
            self._tx_marker = self._map.set_marker(
                lat, lon, text=i18n.t("map_marker_tx"),
                icon=self._tx_icon, icon_anchor="center",
                text_color=_MARKER_TEXT,
            )
        else:
            old, self._rx_marker = self._rx_marker, None   # 手放してから消す（B-080）
            if old is not None:
                old.delete()
            self._rx_coord = (lat, lon)
            self._rx_marker = self._map.set_marker(
                lat, lon, text=i18n.t("map_marker_rx"),
                icon=self._rx_icon, icon_anchor="center",
                text_color=_MARKER_TEXT,
            )
        self._redraw_path()

    def _redraw_path(self) -> None:
        """TX/RX が揃っていれば 2 点を結ぶパス線と中点の距離ラベルを引き直す。"""
        line, self._path_line = self._path_line, None    # 手放してから消す（B-080）
        label, self._dist_label = self._dist_label, None
        if line is not None:
            line.delete()
        if label is not None:
            label.delete()
        if self._tx_coord is not None and self._rx_coord is not None:
            # 既定 width=9 は太いので細線に。色は基調のシアンでノードと揃える。
            self._path_line = self._map.set_path(
                [self._tx_coord, self._rx_coord], color=_MAP_CYAN_HEX, width=3)
            # 水平距離ラベルを中点に重ねる（半透明ピル背景つき＝pan/zoom 追従）。
            mid = ((self._tx_coord[0] + self._rx_coord[0]) / 2,
                   (self._tx_coord[1] + self._rx_coord[1]) / 2)
            km = models.horizontal_distance_km(*self._tx_coord, *self._rx_coord)
            text = units.format_distance(km)
            self._dist_badge = self._make_distance_badge(text)
            self._dist_label = self._map.set_marker(
                mid[0], mid[1], icon=self._dist_badge, icon_anchor="center",
            )

    def _fit_to_existing_paths(self) -> None:
        """append 先（バッチ）の既存パス群の外接 bbox に地図をフィットする。"""
        if self._append_sink is None:
            return
        paths = self._append_sink.existing_paths()
        if not paths:
            return
        lats = [p for _, tx, rx in paths for p in (tx[0], rx[0])]
        lons = [p for _, tx, rx in paths for p in (tx[1], rx[1])]
        self._fit_to_path((max(lats), min(lons)), (min(lats), max(lons)))

    def _load_single_coords(self) -> None:
        """ランチャー数値欄の既存 TX/RX を取り込み、地図の中心とズームを合わせる。

        両方そろっていれば経路長に合わせて自動ズーム、片方だけなら近接ズームで寄せる。
        マーカー・経路の実描画はモードに応じて _apply_mode_visibility が行う。
        """
        if self._single_sink is None:
            return
        coords = self._single_sink.current_path_coords()
        tx, rx = coords.get("tx"), coords.get("rx")
        self._tx_coord, self._rx_coord = tx, rx
        # 次の入力対象: 未設定があればそれを優先、両方あれば TX から上書き再開。
        self._pick_next = "tx" if tx is None else ("rx" if rx is None else "tx")
        # 既存座標があれば中心とズームを合わせる。
        if tx is not None and rx is not None:
            self._fit_to_path(tx, rx)
        elif tx is not None:
            self._map.set_zoom(_SINGLE_ZOOM)
            self._map.set_position(*tx)
        elif rx is not None:
            self._map.set_zoom(_SINGLE_ZOOM)
            self._map.set_position(*rx)

    def _fit_to_path(self, tx: tuple, rx: tuple) -> None:
        """TX/RX を余白込みで収める bbox に地図をフィット（経路長に応じ自動ズーム）。

        tkintermapview の fit_bounding_box は top_left=(緯度大, 経度小) /
        bottom_right=(緯度小, 経度大) で、かつ両者が厳密に大小である必要がある。
        純東西/南北の経路は span が 0 で退化するため最小スパンと余白でパディングする。
        （内部で after(100) し寸法確定後にズーム決定される。）
        """
        lat_n, lat_s = max(tx[0], rx[0]), min(tx[0], rx[0])
        lon_w, lon_e = min(tx[1], rx[1]), max(tx[1], rx[1])
        span_lat = max(lat_n - lat_s, _FIT_MIN_SPAN)
        span_lon = max(lon_e - lon_w, _FIT_MIN_SPAN)
        cy, cx = (lat_n + lat_s) / 2, (lon_w + lon_e) / 2
        half_lat = span_lat / 2 * (1 + _FIT_MARGIN)
        half_lon = span_lon / 2 * (1 + _FIT_MARGIN)
        self._map.fit_bounding_box(
            (cy + half_lat, cx - half_lon), (cy - half_lat, cx + half_lon)
        )
