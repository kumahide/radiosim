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
from typing import TYPE_CHECKING, NamedTuple

from PIL import ImageTk

from core import dem
from core import i18n
from core import models
from core import units
from report import map_graphics
from views.map_style import (_FIT_MARGIN, _FIT_MIN_SPAN, _MARKER_TEXT,
                             _SINGLE_ZOOM, _MAP_CYAN_HEX)

if TYPE_CHECKING:
    from tkintermapview import TkinterMapView

    from views.map_window import _AppendSink, _SingleSink, _WaypointSink


class _Selected(NamedTuple):
    """いま選ばれている点（I-098＝**選んでから置き直す**）。

    ⚠️ **`pos` は「地図が写した並びでの位置」**であって、窓の行番号ではない
    （読めない座標の行は写しに出ないので、両者は一致しない）。窓の側が同じ規則で
    位置を解くところまでが約束で、写しを作る口と解く口は**窓の中で 1 つ**にして
    ある（`_readable_*`）。地図が窓の内部の番号を知る形にすると、そこがずれた
    ときに**黙って別の点を動かす**＝B-068 / B-102 と同じ壊れ方になる。

    `ident` / `label` / `coord` は**選んだ時点の写し**＝窓が照合に使う名前（経路
    ID・地点名）、画面へ出す呼び名、移動量を測るための元の位置。
    ⚠️ **照合に使うのは `ident` であって `label` ではない**＝画面の呼び名は
    i18n で変わるので、窓に画面の字を照合させると訳を直しただけで動かなくなる。
    """
    layer: str          # "pick"（TX/RX のピック層）/ "committed" / "waypoints"
    pos: int            # 写しの並びでの位置（pick 層は -1＝役割で決まるので不要）
                        # ⚠️ `index` と名づけない＝`tuple.index` を隠す（pyright）
    role: str           # "tx" / "rx"（waypoints 層は ""）
    ident: str          # 窓の側の名前（経路 ID・地点名）＝書き戻す前の照合用
    label: str          # 状態バーに出す呼び名
    coord: tuple        # 選んだ時点の (lat, lon)


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
        _pick_next: "str | None"
        _selection: "_Selected | None"
        _select_guard: bool
        _single_sink: "_SingleSink | None"
        _append_sink: "_AppendSink | None"
        _waypoint_sink: "_WaypointSink | None"
        _tx_coord: "tuple | None"
        _rx_coord: "tuple | None"
        _pick_objects: list
        _pick_images: list
        _tx_icon: ImageTk.PhotoImage
        _rx_icon: ImageTk.PhotoImage
        _relay_icon: ImageTk.PhotoImage
        _tx_icon_sel: ImageTk.PhotoImage
        _rx_icon_sel: ImageTk.PhotoImage
        _relay_icon_sel: ImageTk.PhotoImage
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
        self._drop_selection("waypoints")
        self._refresh_waypoints()

    # ----------------------------------------------------------
    # 選択（I-098）＝**選んでから置き直す**
    # ----------------------------------------------------------
    # 地図でできるのは「置く」「足す」だけで**「直す」が無かった**。マーカーを
    # 掴んで引きずる形は採らない（素のドラッグはパンで埋まっており、奪うと
    # *入力が黙って書き換わる*誤操作を作る）。⇒ **1 度目のクリックで選び、
    # 2 度目のクリックでそこへ移す**。選択は 1 回で使い切る（＝置きっぱなしの
    # 選択が次のクリックを飲み込まない）。
    def _marker_command(self, sel: "_Selected"):
        """マーカーに張る「選ぶ」コマンド（`set_marker(command=…)`）。"""
        return lambda _marker=None, s=sel: self._on_marker_click(s)

    def _on_marker_click(self, sel: "_Selected") -> None:
        """マーカーが押された＝その点を選ぶ（もう一度押すと選択を解く）。

        🔴 **同じ押下から地図クリックも届く**＝マーカーの `<Button-1>` は
        *押した瞬間*に、地図の素クリックは*離した瞬間*に来る（tkintermapview は
        押下と離しが同じ位置なら「クリック」と見なす）。何もしないと**選んだ点を
        その場へ移す**空振りの移動になるので、次に届く地図クリックを 1 回だけ
        捨てる（ズームボタンの押下を無視しているのと同じ形＝`_click_on_zoom_button`）。

        ⚠️ **押下の位置では見分けられない**＝canvas のアイテムのバインドは
        ウィジェットのバインドより**先**に走るので、ここへ来た時点で地図の
        `mouse_click_position` はまだ*1 つ前*の押下のまま（実装中に取り違えた）。
        ⇒ 位置ではなく**印を 1 つ立てて、離したときに必ず降ろす**（`_clear_select_guard`
        が `<ButtonRelease-1>` から呼ばれる）＝掴んで地図を送った（クリックが
        届かなかった）ときも、印が次のクリックまで残らない。
        """
        if self._busy or self._mode.get() == "cache":
            return
        self._select_guard = True
        if self._same_point(self._selection, sel):
            self._deselect()
            return
        old, self._selection = self._selection, sel
        if sel.layer == "pick":
            # ピック層は既存の `_pick_next` がそのまま「次に書く先」＝**新しい
            # 概念を増やさない**（交互ピックの一般化）。
            self._pick_next = sel.role
        if old is not None and old.layer != sel.layer:
            self._redraw_layer(old.layer)
        self._redraw_layer(sel.layer)
        self._set_status(i18n.t("map_selected").format(label=sel.label))

    @staticmethod
    def _same_point(a: "_Selected | None", b: "_Selected | None") -> bool:
        """同じ点を指しているか（名前や座標の写しは比べない）。"""
        if a is None or b is None:
            return False
        return (a.layer, a.pos, a.role) == (b.layer, b.pos, b.role)

    def _forget_selection(self) -> "_Selected | None":
        """選択を落とし、**役割の指名も座標から derive し直す**（描き直しはしない）。

        🔴 **選択を落とす口はここ 1 つ**（B-112）＝選ぶ側は `_pick_next` に役割を
        書き込む（上の `_select`）のに、解く側は `_selection` しか落としていなかった。
        ⇒ Esc やモード切替で解いた直後の**素のクリックが、解いたはずの点を黙って
        動かす**——I-098 の芯（素のクリックでは黙って書き換わらない）の裏切りになる。
        ⇒ 指名は覚えておくものではなく**選択と一緒に derive し直すもの**として扱う。

        戻り値は落とした選択（描き直したいレイヤを呼び出し側が知るため）。
        """
        sel, self._selection = self._selection, None
        if sel is not None and sel.layer == "pick":
            self._advance_pick()
        return sel

    def _deselect(self) -> None:
        """選択を解いて、そのレイヤを描き直す（ハイライトを落とす）。"""
        sel = self._forget_selection()
        if sel is not None:
            self._redraw_layer(sel.layer)
        self._set_idle()

    def _drop_selection(self, layer: str) -> None:
        """そのレイヤの選択を**黙って落とす**（窓の側で並びが変わったとき）。

        ⚠️ 描き直しは呼び出し側が続けて行う＝ここで描くと、通知のたびに
        同じレイヤを 2 度描くことになる。
        """
        if self._selection is not None and self._selection.layer == layer:
            self._forget_selection()

    def _redraw_layer(self, layer: str) -> None:
        if layer == "waypoints":
            self._refresh_waypoints()
        elif layer == "committed":
            self._refresh_committed_paths()
        else:
            self._refresh_picks()

    def _consume_select_click(self) -> bool:
        """いまの地図クリックが「選ぶための押下」の続きなら True（＝捨てる）。"""
        guard, self._select_guard = self._select_guard, False
        return guard

    def _clear_select_guard(self, _event=None) -> None:
        """押下 → 離しの 1 巡が終わったら印を降ろす（`<ButtonRelease-1>` から）。

        ⚠️ **地図クリックが届いた後に走る**＝地図側の `<ButtonRelease-1>` は
        ライブラリが先に張っており（`add="+"` の後乗り）、クリックの配送はそこから
        起きる。順序が逆になると、捨てるはずのクリックが素通りする。
        """
        self._select_guard = False

    def _advance_pick(self) -> None:
        """次の素クリックが書く先を決める（**空いている方を埋める**）。

        🔴 **両方そろっていたら `None`**＝ここが I-098 の穴だった。以前は
        `TX→RX→TX…` と機械的に切り替えており、両方入った状態で開くと*次の
        クリックは必ず TX*＝「RX だけ置き直す」ができず、**先に TX を潰してから
        打ち直す**ことになっていた。⇒ 置く先が無いときは素クリックで何も書かず、
        「選んでから置き直す」へ案内する。
        """
        self._pick_next = ("tx" if self._tx_coord is None
                           else ("rx" if self._rx_coord is None else None))

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
            role = "tx" if i == 0 else ("rx" if i == last else "relay")
            sel = _Selected(
                "waypoints", i, "", name,
                i18n.t("map_label_waypoint").format(name=name or i + 1),
                (lat, lon))
            self._wp_objects.append(self._map.set_marker(
                lat, lon, text=name,
                icon=self._point_icon(role, self._same_point(self._selection, sel)),
                icon_anchor="center", text_color=_MARKER_TEXT,
                command=self._marker_command(sel)))

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

    def _point_icon(self, role: str, selected: bool = False):
        """役割（tx / rx / relay）と選択状態からアイコンを引く（**出所はここ 1 つ**）。

        3 つのレイヤが同じ引き方をすることで、「選択中の見た目」が層ごとに
        割れない（描き方の規則を 1 つに保つ＝2026-08-14 に矢じりを畳んだのと同じ）。
        """
        icons = {
            "tx":    (self._tx_icon,    self._tx_icon_sel),
            "rx":    (self._rx_icon,    self._rx_icon_sel),
            "relay": (self._relay_icon, self._relay_icon_sel),
        }
        return icons[role][1 if selected else 0]

    def on_paths_changed(self) -> None:
        """append 先（バッチ）のパス集合が変わったときの通知。確定パス表示を引き直す。

        連続追加モードでないとき（_append_sink is None）は _refresh_committed_paths
        が早期 return するので no-op。地図側で source of truth を持たないため、毎回
        複数経路の現在の行から描き直すだけ（削除・クリア・インポートに追従する）。
        """
        self._drop_selection("committed")
        self._refresh_committed_paths()

    def _path_point_label(self, pid: str, role: str) -> str:
        """確定パスの端点の呼び名（状態バー用）。ID が無い行は役割だけで呼ぶ。"""
        role_text = i18n.t("map_marker_tx" if role == "tx" else "map_marker_rx")
        if not pid:
            return role_text
        return i18n.t("map_label_path_point").format(pid=pid, role=role_text)

    def _show_coord_visuals(self) -> None:
        """保持中の TX/RX 座標からマーカー・経路・距離ラベルを再構築する。"""
        self._refresh_picks()

    def _clear_coord_visuals(self) -> None:
        """マーカー・経路・距離ラベル・確定パスを地図から消す（座標値は保持する）。"""
        self._clear_pick_visuals()
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
        だけ。パース不能行は除外済み。距離バッジに path_id を添えて、複数経路の
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
        for i, (pid, tx, rx) in enumerate(self._append_sink.existing_paths()):
            self._committed.append(
                self._map.set_path([tx, rx], color=_MAP_CYAN_HEX, width=3))
            # 端点はアクティブピックと同じアイコン（TX=塗り / RX=白抜き）。
            # ⚠️ **確定パスの端点にも「選ぶ」を張る**（I-098）＝複数経路は文字
            # ラベルを持たないので、掴んだ点がどれかは*選んで名乗らせる*しかない
            # （ドラッグを採らなかった理由の 3 つ目がここで解ける）。
            for role, (lat, lon) in (("tx", tx), ("rx", rx)):
                sel = _Selected("committed", i, role, pid,
                                self._path_point_label(pid, role), (lat, lon))
                self._committed.append(self._map.set_marker(
                    lat, lon,
                    icon=self._point_icon(
                        role, self._same_point(self._selection, sel)),
                    icon_anchor="center", command=self._marker_command(sel)))
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
        """地図の素クリック。**選択があればそこへ移し、無ければ置く／足す。**

        ⇒ 規則は 1 つ＝**素のクリックは足す・選んでからのクリックは直す**。
        置く先が無いとき（座標入力で TX/RX が両方そろっているとき）だけ、素の
        クリックは何も書かずに「選んでから」と案内する（I-098）。
        """
        if self._mode.get() == "cache" or self._busy:
            return
        if self._click_on_zoom_button():
            return
        if self._consume_select_click():
            return          # いま選んだところ＝同じ押下でその場へ移さない
        lat, lon = coords
        sel = self._selection
        if sel is not None and sel.layer != "pick":
            self._move_selected(sel, lat, lon)
            return
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
        if role is None:
            # 座標入力で両方そろっている＝素のクリックは*何も書かない*。
            self._set_status(i18n.t("map_select_hint"))
            return
        before = self._tx_coord if role == "tx" else self._rx_coord
        if self._place_pick(role, lat, lon):
            return          # ペアが成立して 1 行足した（状態は `_place_pick` が言う）
        if sel is not None:
            # 選んで動かした＝**選択は 1 回で使い切る**（置きっぱなしの選択が
            # 次のクリックを飲み込まない）。移動量は状態バーで返す。
            self._selection = None
            self._refresh_picks()
            self._report_move(sel.label, before, lat, lon)
            return
        self._set_idle()   # 次のピック対象をヒントに反映

    def _place_pick(self, role: str, lat: float, lon: float) -> bool:
        """TX/RX を (lat, lon) に置き、宛先へ書き戻す。**ペアが成立したら True**。

        素のクリックと右クリックメニュー（`_place_from_menu`）の**共通の書き口**＝
        「置く」規則を 2 か所に書かない（連続追加のペア成立・次のピック対象の
        進め方が片方だけ古くなる型を作らない）。
        """
        self._set_pick_marker(role, lat, lon)
        if self._append_sink is not None:
            # 連続追加（Phase D2）: 両方そろった時点でペア成立 → 1 行を append し、
            # アクティブなピックをリセットして次の TX 待ちに戻す（add 不要）。
            # 🔴 **「いま置いたのが RX か」では見ない**（B-113）＝素のクリックしか
            # 無かった頃は `TX → RX` の順しか作れず、それで代用できていた。B-111 の
            # 右クリックは**役割を名指しできる**ので `RX → TX` が実在する＝両方
            # そろっても行が足されず、`_pick_next` も None になる（行き止まり）。
            if self._tx_coord is not None and self._rx_coord is not None:
                pid = self._append_sink.append_path(self._tx_coord, self._rx_coord)
                # ⚠️ ペアが成立した時点で**アクティブなピックは無くなる**＝選んで
                # 動かしていた場合もここで選択ごと消える（`_reset_active_pick`）。
                self._reset_active_pick()
                self._refresh_committed_paths()
                if pid:
                    self._set_status(
                        i18n.t("map_append_added").format(pid=pid), auto_clear=True)
                else:
                    self._set_idle()
                return True
        elif self._single_sink is not None:
            # 単一書き戻し（ランチャー）: ピックごとに start/end 欄へ反映。
            self._single_sink.apply_map_pick(role, lat, lon)
        self._advance_pick()
        return False

    # ----------------------------------------------------------
    # 右クリック＝**ここに TX / RX を置く**（B-111）
    # ----------------------------------------------------------
    # 🔴 I-098 で素のクリックの上書きを止めたぶん、**両方そろった状態での置き直しは
    # 「マーカーを選ぶ」1 本だけ**になった。⇒ 地図を遠くへ送るとその唯一の入口が
    # *画面の外*にあり、新しい地点を置く動線ごと死ぬ（実機報告）。ここで足すのは
    # **マーカーの位置に依存しない入口**であって、素クリックの規則は変えない
    # （黙って書き換えない、という I-098 の芯はそのまま＝役割を明示して選ばせる）。
    def _on_right_click(self, event) -> None:
        """地図の右クリック → 置き直す役割を選ばせる。

        ⚠️ **ライブラリの右クリックメニューを置き換えている**（`add="+"` ではなく
        張り直し）＝tkintermapview の既定メニューは英語の「座標をクリップボードへ」
        1 本で、押すと英語のダイアログが出る（[[feedback-japanese-everywhere]]）。
        2 つのメニューが同時に出る形にはしない。
        """
        if self._busy or self._mode.get() not in ("coords", "append"):
            return
        try:
            lat, lon = self._map.convert_canvas_coords_to_decimal_coords(
                event.x, event.y)
        except Exception:
            return
        menu = tk.Menu(self._map, tearoff=0)
        for role, key in (("tx", "map_menu_place_tx"), ("rx", "map_menu_place_rx")):
            menu.add_command(
                label=i18n.t(key),
                command=lambda r=role, la=lat, lo=lon: self._place_from_menu(r, la, lo))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _place_from_menu(self, role: str, lat: float, lon: float) -> None:
        """右クリックメニューから TX/RX を置く（**役割は利用者が名指しした**）。"""
        if self._busy or self._mode.get() not in ("coords", "append"):
            return
        before = self._tx_coord if role == "tx" else self._rx_coord
        # 選択は持ち越さない＝役割を名指しした時点で「選んでから」の筋は終わっている
        # （ハイライトは続く `_place_pick` の描き直しで落ちる）。
        self._selection = None
        if self._place_pick(role, lat, lon):
            return
        # 置き直しなら移動量を返す（初めて置いたなら `_report_move` がヒントへ戻す）。
        self._report_move(
            i18n.t("map_marker_tx" if role == "tx" else "map_marker_rx"),
            before, lat, lon)

    def _move_selected(self, sel: "_Selected", lat: float, lon: float) -> None:
        """選択中の点を (lat, lon) へ移す（**書き戻す先は窓**＝地図は写し）。

        🔴 **書き戻す前に照合する**（B-068 / B-102 と同じ形）＝選んでから動かす
        までのあいだに窓の側で行が消える・並びが変わることがあり、位置だけで
        書き戻すと**黙って別の点を動かす**。窓が「その位置は違う」と言ったら
        （空文字）選び直してもらう。
        """
        self._selection = None
        moved = False
        if sel.layer == "committed" and self._append_sink is not None:
            moved = self._append_sink.update_path_point(
                sel.pos, sel.role, lat, lon, sel.ident)
        elif sel.layer == "waypoints" and self._waypoint_sink is not None:
            moved = self._waypoint_sink.update_waypoint(
                sel.pos, lat, lon, sel.ident)
        self._redraw_layer(sel.layer)
        if not moved:
            self._set_status(i18n.t("map_select_stale"), auto_clear=True)
            return
        self._report_move(sel.label, sel.coord, lat, lon)

    def _report_move(self, label: str, before: "tuple | None",
                     lat: float, lon: float) -> None:
        """動かした結果を状態バーで返す（**動かした量そのもの**を出す）。

        ⚠️ **地形の標本は 5〜10m メッシュから拾う**ので、それより細かく詰めても
        計算結果は変わらない＝「動かしたのに変わらない」に見える。⇒ *ズームで
        調整を禁じる*のではなく、**起きたときにその場で言う**（禁じる側は、
        地図を寄せただけで機能が消える死んだモードを作る）。
        判定に使うのは縮尺ではなく**実際の移動量**＝縮尺は近さの当て推量でしかない。
        """
        if before is None:
            self._set_idle()
            return
        km = models.horizontal_distance_km(before[0], before[1], lat, lon)
        text = i18n.t("map_moved").format(
            label=label, dist=units.format_distance(km))
        if km * units.KM_TO_M < dem.FINEST_MESH_M:
            text += " " + i18n.t("map_move_below_mesh").format(
                mesh=f"{dem.FINEST_MESH_M:g}")
        self._set_status(text, auto_clear=True)

    def _reset_active_pick(self) -> None:
        """アクティブな TX/RX ピック（マーカー・経路・座標）をクリアし TX 待ちへ戻す。

        確定済みパス（_committed）には触れない＝append 後も軌跡は地図に残る。
        """
        # **座標を先に落としてから描き直す**（B-104）＝地図は座標の写しなので、
        # 消し方を別に持たない（持つと再入したとき 2 つの手順が食い違う）。
        self._tx_coord = self._rx_coord = None
        self._drop_selection("pick")
        self._advance_pick()
        self._refresh_picks()

    def _make_node_icon(self, hollow: bool,
                        selected: bool = False) -> ImageTk.PhotoImage:
        """端点のノードアイコンを Tk 用にラップして返す（描画は map_graphics）。"""
        return ImageTk.PhotoImage(map_graphics.node_icon(hollow, selected))

    def _make_distance_badge(self, text: str) -> ImageTk.PhotoImage:
        """距離バッジを Tk 用にラップして返す（描画は map_graphics）。"""
        return ImageTk.PhotoImage(map_graphics.distance_badge(text))

    def _set_pick_marker(self, role: str, lat: float, lon: float) -> None:
        """TX/RX の座標を控え、ピック層（マーカー・経路線・距離ラベル）を描き直す。

        🔴 **座標を先に控えてから描き直す**（B-104）。以前はマーカーを消す→作る→
        参照を持ち替える、を役割ごとに手で書いていたが、`delete()` は中で
        `canvas.update()` を回すので**消している最中に次のクリックが届く**。内側が
        作ったマーカーの参照を外側が上書きし、**孤児マーカーが地図に残り**、
        `_tx_coord` も 1 回前のクリックに巻き戻っていた（素早い連続クリック）。
        ⇒ 座標だけを持ち、描画は他の 2 レイヤ（waypoints・committed）と同じ
        「台帳ごと消して座標から描き直す＋直列化」に揃える。
        """
        if role == "tx":
            self._tx_coord = (lat, lon)
        else:
            self._rx_coord = (lat, lon)
        self._refresh_picks()

    def _refresh_picks(self) -> None:
        """ピック層を描き直す（再入は `_redraw_serialized` が畳む＝B-104）。"""
        self._redraw_serialized("picks", self._draw_picks)

    def _clear_pick_visuals(self) -> None:
        # 台帳ごと取り出してから消す（理由は `_clear_waypoint_visuals`＝B-080）。
        objs, self._pick_objects = self._pick_objects, []
        self._pick_images = []
        for obj in objs:
            try:
                obj.delete()
            except Exception:
                pass

    def _draw_picks(self) -> None:
        """TX/RX の座標からマーカーを置き、両方揃えばパス線と距離ラベルを描く。"""
        self._clear_pick_visuals()
        if self._mode.get() in ("cache", "waypoints"):
            return          # モードが見た目を決める（`_apply_mode_visibility`）
        for role, coord in (("tx", self._tx_coord), ("rx", self._rx_coord)):
            if coord is None:
                continue
            label = i18n.t("map_marker_tx" if role == "tx" else "map_marker_rx")
            sel = _Selected("pick", -1, role, role, label, coord)
            self._pick_objects.append(self._map.set_marker(
                coord[0], coord[1], text=label,
                icon=self._point_icon(
                    role, self._same_point(self._selection, sel)),
                icon_anchor="center", text_color=_MARKER_TEXT,
                command=self._marker_command(sel),
            ))
        if self._tx_coord is not None and self._rx_coord is not None:
            # 既定 width=9 は太いので細線に。色は基調のシアンでノードと揃える。
            self._pick_objects.append(self._map.set_path(
                [self._tx_coord, self._rx_coord], color=_MAP_CYAN_HEX, width=3))
            # 水平距離ラベルを中点に重ねる（半透明ピル背景つき＝pan/zoom 追従）。
            mid = ((self._tx_coord[0] + self._rx_coord[0]) / 2,
                   (self._tx_coord[1] + self._rx_coord[1]) / 2)
            km = models.horizontal_distance_km(*self._tx_coord, *self._rx_coord)
            badge = self._make_distance_badge(units.format_distance(km))
            self._pick_images.append(badge)        # GC 防止に保持
            self._pick_objects.append(self._map.set_marker(
                mid[0], mid[1], icon=badge, icon_anchor="center",
            ))

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

    def on_single_coords_changed(self) -> None:
        """ランチャーの座標欄が変わったときの通知（手入力・消去・読み込み）。

        🔴 **ピック層はランチャー数値欄の写し**＝他の 2 層（確定パス＝バッチ表 /
        中継経路＝地点列）が既に通知で追従しているのに、**ここだけ「開いた時に
        1 度読む」きり**だった。⇒ 欄を空にしてもマーカーが残り、しかも地図の側は
        「TX/RX は指定済み」と思い続けるので**素のクリックが何も書かなくなる**
        （置く先が無い＝I-098 の案内が出たまま抜けられない）。B-110 / B-111 は
        どちらもこの 1 つの穴の別の顔。

        ⚠️ **視野は動かさない**＝ここが `_load_single_coords` との違い。打鍵ごとに
        届くので、中心やズームを合わせると*入力している最中に地図が飛ぶ*。
        """
        # 写しが要るのは座標入力モードだけ（連続追加のピックはバッチ行の下ごしらえで
        # あって数値欄の写しではない＝モードを戻すとき `_load_single_coords` が走る）。
        if self._mode.get() != "coords" or self._single_sink is None:
            return
        self._sync_single_coords()
        self._refresh_picks()
        self._set_idle()          # 置く先が戻れば、ヒントもそこへ戻る

    def _sync_single_coords(self) -> tuple:
        """ランチャー数値欄の TX/RX を取り込む（**視野には触れない**）。

        戻り値は取り込んだ `(tx, rx)`＝呼び出し側が視野合わせに使う。
        """
        if self._single_sink is None:
            return (None, None)
        coords = self._single_sink.current_path_coords()
        tx, rx = coords.get("tx"), coords.get("rx")
        self._tx_coord, self._rx_coord = tx, rx
        # 写しが入れ替わった＝**選択は持ち越さない**（選んだ点がもう無いことがある）。
        self._drop_selection("pick")
        # 次の入力対象＝**空いている方**（両方あれば「選んでから」＝I-098）。
        self._advance_pick()
        return (tx, rx)

    def _load_single_coords(self) -> None:
        """ランチャー数値欄の既存 TX/RX を取り込み、地図の中心とズームを合わせる。

        両方そろっていれば経路長に合わせて自動ズーム、片方だけなら近接ズームで寄せる。
        マーカー・経路の実描画はモードに応じて _apply_mode_visibility が行う。
        """
        if self._single_sink is None:
            return
        tx, rx = self._sync_single_coords()
        self._selection = None
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
