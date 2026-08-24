"""
tests/test_map_window.py
========================
マップウィンドウの安全破棄ロジックの回帰テスト。

GUI 自体はヘッドレスで起こせないが、tkintermapview の after ループを止めてから
破棄する手順（`close_map_safely`）は純粋なロジックなので、フェイクで固定する。
このクラスのバグ（`invalid command name ...update_canvas_tile_images`）は機能追加の
たびに破棄経路から猶予が抜け落ちて再発してきたため、手順を 1 関数に集約した上で
不変条件をテストで守る。
"""

import ast
import os
import re
from types import MethodType, SimpleNamespace

from views.map_window import _MAP_DRAIN_MS, MapWindow, close_map_safely

_VIEWS_DIR = os.path.join(os.path.dirname(__file__), "..", "views")


def _fake_scheduler():
    """`.after(ms, cb)` を記録するだけのフェイク tk ウィジェット。"""
    calls = []
    return SimpleNamespace(after=lambda ms, cb: calls.append((ms, cb))), calls


def test_stops_loop_before_scheduling_destroy():
    """破棄前に必ず running=False（再スケジュールを断つ）。"""
    map_widget = SimpleNamespace(running=True)
    scheduler, _ = _fake_scheduler()
    close_map_safely(scheduler, map_widget, lambda: None)
    assert map_widget.running is False


def test_destroy_is_delayed_not_synchronous():
    """破棄は同期実行せず、_MAP_DRAIN_MS の猶予をおいてスケジュールする。"""
    map_widget = SimpleNamespace(running=True)
    scheduler, calls = _fake_scheduler()
    destroyed = []
    close_map_safely(scheduler, map_widget, lambda: destroyed.append(True))
    # まだ破棄していない（キュー済み after を消化させる猶予中）。
    assert destroyed == []
    assert len(calls) == 1
    ms, cb = calls[0]
    assert ms == _MAP_DRAIN_MS
    # 猶予後にスケジュールされたコールバックが実破棄を行う。
    cb()
    assert destroyed == [True]


def test_destroy_runs_synchronously_when_scheduling_fails():
    """after が使えない（例: 親が既に破棄）ときは即時破棄にフォールバックする。"""
    def boom(ms, cb):
        raise RuntimeError("after unavailable")

    map_widget = SimpleNamespace(running=True)
    scheduler = SimpleNamespace(after=boom)
    destroyed = []
    close_map_safely(scheduler, map_widget, lambda: destroyed.append(True))
    assert destroyed == [True]


def test_resilient_when_map_widget_has_no_running():
    """map 実体が壊れていても破棄スケジュールは進める（破棄を妨げない）。"""
    class NoRunning:
        @property
        def running(self):
            raise AttributeError

        @running.setter
        def running(self, value):
            raise AttributeError

    scheduler, calls = _fake_scheduler()
    close_map_safely(scheduler, NoRunning(), lambda: None)
    assert len(calls) == 1 and calls[0][0] == _MAP_DRAIN_MS


# ============================================================
# 中継経路レイヤ（地図は写すだけ・モードが見た目を決める）
# ============================================================
# 実物の MapWindow は tkintermapview が要るのでここでは起こさない。**描き直しの
# 規則**（毎回消してから描く／モードを抜けたら消す／宛先が無ければ何も描かない）
# だけをフェイクで固定する。2026-08-01 の実機確認で出た 2 件
#   ①窓で地点を削除しても地図のピンが残る（足すだけで消し方が無かった）
#   ②どのモードでも中継点ピンが出る（モード別の表示制御に入っていなかった）
# は、どちらもこの規則の不在そのもの。

class _FakeMapWidget:
    """`set_marker` / `set_path` を記録し、`delete()` で消えるフェイク。"""

    def __init__(self):
        self.objects: list = []

    def _make(self, kind):
        obj = SimpleNamespace(kind=kind, deleted=False)
        obj.delete = lambda o=obj: setattr(o, "deleted", True)
        self.objects.append(obj)
        return obj

    def set_marker(self, *a, **k):
        obj = self._make("marker")
        obj.args, obj.kwargs = a, k       # 何をどこへ置いたかを見るテスト用
        return obj

    def set_path(self, *a, **k):
        obj = self._make("path")
        obj.args, obj.kwargs = a, k
        return obj

    @property
    def alive(self):
        return [o for o in self.objects if not o.deleted]


def _map_stub(mode: str, points):
    """`_refresh_waypoints` を呼ぶのに要る最小限だけを持つ MapWindow。"""
    from views.map_window import MapWindow

    win = MapWindow.__new__(MapWindow)
    win._map = _FakeMapWidget()
    win._wp_objects = []
    win._mode = SimpleNamespace(get=lambda: mode)
    win._waypoint_sink = (None if points is None else
                          SimpleNamespace(waypoint_markers=lambda: points))
    win._tx_icon, win._rx_icon, win._relay_icon = "TX_ICON", "RX_ICON", "RELAY_ICON"
    win._tx_icon_sel, win._rx_icon_sel, win._relay_icon_sel = (
        "TX_SEL", "RX_SEL", "RELAY_SEL")
    win._selection = None
    win._wp_images = []
    # 距離バッジの画像化だけは差し替える＝`ImageTk.PhotoImage` は Tk の root を
    # 要求するので、root 無しのこの層では作れない（アイコンを None にしているのと
    # 同じ理由）。ここが見たいのは**描き直しの規則**であって画像そのものではない。
    win._make_distance_badge = lambda _text: None
    win._redraw_state = {}
    return win


_THREE = [("TX", 34.5, 132.4), ("R1", 34.55, 132.45), ("RX", 34.6, 132.5)]


def test_waypoint_layer_is_redrawn_not_appended():
    """描き直しは**毎回消してから**（足すだけにすると削除が地図に届かない）。"""
    win = _map_stub("waypoints", _THREE)
    win._refresh_waypoints()
    first = len(win._map.alive)
    assert first == 6, f"折れ線 1 本＋地点 3 つ＋区間の距離バッジ 2 つのはずが {first}"

    win._waypoint_sink = SimpleNamespace(waypoint_markers=lambda: _THREE[:2])
    win._refresh_waypoints()
    assert len(win._map.alive) == 4, "古いマーカーが残っている（削除が反映されない）"


def test_waypoint_layer_is_cleared_outside_its_mode():
    """中継点モードを抜けたら消えること（モードが見た目を決める）。"""
    win = _map_stub("waypoints", _THREE)
    win._refresh_waypoints()
    assert win._map.alive
    win._mode = SimpleNamespace(get=lambda: "coords")
    win._refresh_waypoints()
    assert not win._map.alive, "他のモードでも中継点が残っている"


def test_waypoint_layer_needs_a_destination():
    """宛先（中継経路ウィンドウ）が無ければ何も描かない。"""
    win = _map_stub("waypoints", None)
    win._refresh_waypoints()
    assert not win._map.alive


# ------------------------------------------------------------
# 消している最中の割り込み（B-080）
# ------------------------------------------------------------
# tkintermapview の `CanvasPositionMarker.delete()` は中で `canvas.update()` を
# 呼ぶ＝**消している最中にイベントが 1 巡する**。素早い追加・削除では、そこへ
# 地点列の変更通知が届いて描き直しが再入した。**これはコメントでは守れない**
# （実行時の制約はテストで表現する）ので、再入をフェイクの delete から起こす。
class _ReentrantMapWidget(_FakeMapWidget):
    """マーカーを消すと 1 度だけ描き直しが割り込むフェイク（`canvas.update()` の模型）。"""

    def __init__(self):
        super().__init__()
        self.on_delete = None       # 割り込ませる呼び出し（1 回だけ）

    def _make(self, kind):
        obj = super()._make(kind)

        def _delete(o=obj):
            o.deleted = True
            if o.kind != "marker":
                return          # 割り込むのはマーカーの削除だけ（本物と同じ）
            cb, self.on_delete = self.on_delete, None
            if cb is not None:
                cb()
        obj.delete = _delete
        return obj


def test_waypoint_layer_survives_a_redraw_during_the_clear():
    """消している最中に描き直しが割り込んでも、線を**取り落とさない**こと。

    取り落とした `CanvasPath` は tkintermapview の `canvas_path_list` に生き残り、
    **以後ずっと描かれ続ける**（過去の経路が地図に残り、パンすると一緒に動く＝
    2026-08-13 の実機報告そのもの）。⇒ 生きているオブジェクトは必ず台帳
    （`_wp_objects`）に載っていること、が守るべき不変条件。
    """
    win = _map_stub("waypoints", _THREE)
    win._map = _ReentrantMapWidget()
    win._map.on_delete = win._refresh_waypoints      # 消去中に 1 回割り込む
    win._refresh_waypoints()
    win._refresh_waypoints()

    orphans = [o for o in win._map.alive if o not in win._wp_objects]
    assert not orphans, f"台帳から外れた地図オブジェクトが {len(orphans)} 個残っている"
    assert len(win._map.alive) == 6, (
        f"折れ線 1 本＋地点 3 つ＋区間の距離バッジ 2 つのはずが {len(win._map.alive)}"
        "（割り込みぶんが二重に描かれている）"
    )


def test_clearing_the_waypoint_layer_survives_a_redraw_of_its_own():
    """**消去そのもの**が再入に耐えること（直列化の外から来る割り込み）。

    描き直しの直列化（`_redraw_serialized`）は同じレイヤの再入しか畳めない。
    消している最中のイベント 1 巡では**モード切替**のように別の口からも入って
    来られる（`_apply_mode_visibility` は消去を直接呼ぶ）ので、消去ループ自身が
    「台帳ごと取り出してから消す」形になっていないと、同じ取り落としが起きる。
    """
    win = _map_stub("waypoints", _THREE)
    win._map = _ReentrantMapWidget()
    win._refresh_waypoints()                          # 4 個ぶん置く
    drawn = list(win._wp_objects)
    win._map.on_delete = win._draw_waypoints          # 消去中に直接描き直される
    win._clear_waypoint_visuals()

    assert all(o.deleted for o in drawn), "古い地図オブジェクトが消え残っている"
    orphans = [o for o in win._map.alive if o not in win._wp_objects]
    assert not orphans, f"台帳から外れた地図オブジェクトが {len(orphans)} 個残っている"


# ============================================================
# 3 つのモードで同じ描き方をすること（2026-08-14・ユーザー指摘）
# ============================================================
# 地図は 3 通りの描き分けをしていた＝座標入力「線＋端点＋距離バッジ」／複数経路
# 「線＋塗り＋**方位矢じり**＋バッジ」／中継「折れ線＋地点、**距離なし**」。
# 矢じりは *TX/RX が同一座標に重なっても判別する* ための例外で、複数経路を中継の
# 代わりに使っていた時期のもの。中継ウィンドウができてその使い方が無くなり、
# **根拠の消えた例外だけが不統一として残った**。
#
# 🔑 **規則は「線＋端点アイコン＋中点に水平距離バッジ」の 1 つ**。散文で書くと
# また 3 通りへ散るので、ここで固定する（[[feedback-promote-recurring-checks]]）。

def _committed_stub(paths):
    """`_refresh_committed_paths` を呼ぶのに要る最小限だけを持つ MapWindow。"""
    from views.map_window import MapWindow

    win = MapWindow.__new__(MapWindow)
    win._map = _FakeMapWidget()
    win._committed = []
    win._committed_images = []
    win._append_sink = SimpleNamespace(existing_paths=lambda: paths)
    win._tx_icon, win._rx_icon = "TX_ICON", "RX_ICON"    # 同一性だけ見る目印
    win._tx_icon_sel, win._rx_icon_sel = "TX_SEL", "RX_SEL"
    win._relay_icon = win._relay_icon_sel = None
    win._selection = None
    win._make_distance_badge = lambda text: f"BADGE:{text}"
    win._redraw_state = {}
    return win


def test_committed_paths_use_the_same_endpoint_icons_as_the_active_pick():
    """複数経路の確定パスの端点が、座標入力モードと**同じアイコン**であること。

    ⛔ RX を別形状（方位矢じり）に戻さない＝2026-08-14 に撤回した例外。重なりの
    判別と引き換えに、地図の描き方が 2 通りに割れていた。
    """
    win = _committed_stub([("p1", (34.5, 132.4), (34.6, 132.5))])
    win._refresh_committed_paths()

    icons = [o.kwargs.get("icon") for o in win._map.alive if o.kind == "marker"]
    assert "TX_ICON" in icons, "TX が座標入力モードと同じアイコンで描かれていない"
    assert "RX_ICON" in icons, (
        "RX が座標入力モードと同じアイコンで描かれていない"
        "（方位矢じりのような別形状へ戻っていないか）"
    )


def test_a_relay_path_labels_every_section_with_its_distance():
    """中継経路は**区間ごと**に距離バッジを持つこと（地点 N なら N−1 個）。

    ⚠️ この距離は**画面のここにしか出ない**＝区間表の列は周波数・利得・結果だけで
    距離を持たず、レポートに出るのは*斜距離*（用語集で別語）。⇒ 消すと読めなくなる。
    """
    win = _map_stub("waypoints", _THREE)
    badges = []
    win._make_distance_badge = lambda text: badges.append(text) or f"BADGE:{text}"
    win._refresh_waypoints()

    assert len(badges) == len(_THREE) - 1, (
        f"地点 {len(_THREE)} に対し区間の距離バッジが {len(badges)} 個"
        "（区間の数＝地点数 − 1 と一致していない）"
    )
    assert all(b for b in badges), "距離バッジの字が空"
    # 置く場所は区間の中点（両端の平均）。
    mids = [o.args[:2] for o in win._map.alive
            if o.kind == "marker" and str(o.kwargs.get("icon", "")).startswith("BADGE:")]
    expected = [((a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
                for a, b in zip(_THREE, _THREE[1:])]
    assert mids == expected, f"距離バッジが区間の中点に無い: {mids} != {expected}"


# ============================================================
# 選んでから置き直す（I-098）
# ============================================================
# 地図でできるのは長らく「置く」「足す」だけで、**「直す」が無かった**（座標を
# 微調整する動線が数値欄の打ち直ししかない）。ドラッグは採らない＝素のドラッグは
# パンで埋まっており、奪うと*入力が黙って書き換わる*誤操作を作る。
# ⇒ **1 度目のクリックで選び、2 度目のクリックでそこへ移す**。
#
# ここで固定するのは 4 つ:
#   ① 選ぶための押下から届く地図クリックを**その場への移動にしない**
#   ② 2 度目のクリックが**選んだ点だけ**を動かす（素のクリックは今までどおり足す）
#   ③ 座標入力で TX/RX が両方そろっているとき、素のクリックが**TX を潰さない**
#   ④ 窓の側が変わっていたら**書き戻さずに断る**
class _FakeWaypointSink:
    """中継経路ウィンドウの代役（写しを返し、位置と名前で書き戻す）。"""

    def __init__(self, points):
        self.points = list(points)

    def waypoint_markers(self):
        return list(self.points)

    def append_waypoint(self, lat, lon):
        self.points.append((f"R{len(self.points)}", lat, lon))
        return self.points[-1][0]

    def update_waypoint(self, index, lat, lon, expect):
        if not 0 <= index < len(self.points) or self.points[index][0] != expect:
            return False
        self.points[index] = (expect, lat, lon)
        return True


def _edit_stub(mode, *, waypoints=None, single=None):
    """クリックの筋（選ぶ → 移す）を通すのに要る最小限だけを持つ MapWindow。"""
    from views.map_window import MapWindow

    win = MapWindow.__new__(MapWindow)
    win._map = _FakeMapWidget()
    win._map.fit_bounding_box = lambda *a, **k: None   # 表示範囲合わせは見ない
    win._mode = SimpleNamespace(get=lambda: mode)
    win._busy = False
    win._selection = None
    win._select_guard = False
    win._pick_next = "tx"
    win._tx_coord = win._rx_coord = None
    win._pick_objects, win._pick_images = [], []
    win._wp_objects, win._wp_images = [], []
    win._committed, win._committed_images = [], []
    win._tx_icon, win._rx_icon, win._relay_icon = "TX_ICON", "RX_ICON", "RELAY_ICON"
    win._tx_icon_sel, win._rx_icon_sel, win._relay_icon_sel = (
        "TX_SEL", "RX_SEL", "RELAY_SEL")
    win._single_sink = single
    win._append_sink = None
    win._waypoint_sink = waypoints
    win._make_distance_badge = lambda text: f"BADGE:{text}"
    win._redraw_state = {}
    win.status = []
    win._set_status = lambda text, auto_clear=False: win.status.append(text)
    win._set_idle = lambda: win.status.append("<idle>")
    # 押下 → 離しの 1 巡（本物は canvas の `<ButtonRelease-1>` が呼ぶ）。
    win._release = lambda w=win: w._clear_select_guard()
    return win


def _click_marker(win, n: int = 0):
    """描かれたマーカーの n 番目を押す（tkintermapview は押下で command を呼ぶ）。"""
    markers = [o for o in win._map.alive
               if o.kind == "marker" and o.kwargs.get("command") is not None]
    markers[n].kwargs["command"](markers[n])


def test_selecting_a_marker_does_not_move_it_onto_itself():
    """①選ぶための押下から届く地図クリックを捨てること。

    🔴 マーカーの `<Button-1>` は**押した瞬間**に、地図の素クリックは**離した
    瞬間**に来る（tkintermapview は押下と離しが同じ位置なら「クリック」と見なす）。
    捨てないと、選んだ直後に**その場へ移す空振りの移動**が必ず 1 回入る。
    """
    sink = _FakeWaypointSink([("TX", 34.5, 132.4), ("RX", 34.6, 132.5)])
    win = _edit_stub("waypoints", waypoints=sink)
    win._refresh_waypoints()

    _click_marker(win, 0)                      # 押下＝選ぶ
    win._on_map_click((34.55, 132.45))         # 離し＝同じクリックの続き
    win._clear_select_guard()                  # 離しの後始末（本物は canvas から）

    assert sink.points[0] == ("TX", 34.5, 132.4), "選んだだけで座標が動いた"
    assert len(sink.points) == 2, "選ぶクリックが地点の追加として数えられた"
    assert win._selection is not None, "選択が 1 回のクリックで消えている"


def test_the_next_click_moves_only_the_selected_point():
    """②2 度目のクリックは**選んだ点だけ**を動かし、選択は使い切ること。"""
    sink = _FakeWaypointSink([("TX", 34.5, 132.4), ("R1", 34.55, 132.45),
                              ("RX", 34.6, 132.5)])
    win = _edit_stub("waypoints", waypoints=sink)
    win._refresh_waypoints()

    _click_marker(win, 1)                      # R1 を選ぶ
    win._release(win)                          # 選んだ押下の 1 巡が終わる
    win._on_map_click((34.7, 132.7))           # 次のクリック＝ここへ移す

    assert sink.points[1] == ("R1", 34.7, 132.7), "選んだ点が移っていない"
    assert sink.points[0] == ("TX", 34.5, 132.4)
    assert sink.points[2] == ("RX", 34.6, 132.5)
    assert len(sink.points) == 3, "移動のはずが地点を足している"
    assert win._selection is None, "選択が残っている（次のクリックまで飲み込む）"


def test_a_plain_click_still_appends_a_waypoint():
    """②の裏＝**素のクリックは今までどおり足す**（規則は 1 つ: 素は足す・選んだら直す）。"""
    sink = _FakeWaypointSink([("TX", 34.5, 132.4), ("RX", 34.6, 132.5)])
    win = _edit_stub("waypoints", waypoints=sink)
    win._refresh_waypoints()

    win._on_map_click((34.7, 132.7))

    assert len(sink.points) == 3, "素のクリックで地点が足せなくなっている"


def test_a_plain_click_no_longer_overwrites_tx_when_both_are_set():
    """③座標入力で TX/RX が両方そろっているとき、素のクリックが TX を潰さないこと。

    これが I-098 の穴そのもの＝`TX→RX→TX…` と機械的に切り替えていたので、両方
    入った状態で開くと**次のクリックは必ず TX**。「RX だけ置き直す」ができず、
    先に TX を潰してから打ち直すことになっていた。
    """

    picks: list = []
    single = SimpleNamespace(
        apply_map_pick=lambda role, lat, lon: picks.append((role, lat, lon)),
        current_path_coords=lambda: {"tx": (34.5, 132.4), "rx": (34.6, 132.5)},
    )
    win = _edit_stub("coords", single=single)
    win._load_single_coords()
    win._refresh_picks()                        # 本物は `_apply_mode_visibility` が呼ぶ
    assert win._pick_next is None, "置く先が無いのに次のピック対象が残っている"

    win._on_map_click((34.7, 132.7))
    assert picks == [], "素のクリックが TX を上書きした（打ち直しを強いる穴）"
    assert win.status[-1] == _idle_hint(None)

    # 選んでからなら、その点だけが動く（RX を選んで RX だけ置き直す）。
    _click_marker(win, 1)                       # 描画順は TX → RX
    win._release(win)
    win._on_map_click((34.7, 132.7))
    assert picks == [("rx", 34.7, 132.7)], f"RX だけを置き直せていない: {picks}"


def test_a_move_finer_than_the_terrain_mesh_says_so():
    """⑤地形メッシュより小さい移動は、**そう言う**こと（画面は動くが結果は動かない）。

    DEM は 5〜10m メッシュなので、それより細かく詰めても標高は同じ格子から拾われる。
    ⇒ *縮尺で調整を禁じる*のではなく（地図を寄せただけで機能が消える死んだモードに
    なる）、**起きたときにその場で言う**。判定に使うのは縮尺ではなく実際の移動量。
    """
    from core import dem
    from core import i18n

    sink = _FakeWaypointSink([("TX", 34.5, 132.4), ("RX", 34.6, 132.5)])
    win = _edit_stub("waypoints", waypoints=sink)
    win._refresh_waypoints()

    _click_marker(win, 0)
    win._release(win)
    win._on_map_click((34.50002, 132.4))        # 約 2m＝メッシュより細かい
    note = i18n.t("map_move_below_mesh").format(mesh=f"{dem.FINEST_MESH_M:g}")
    assert note in win.status[-1], f"細かすぎる移動を黙って受けた: {win.status[-1]}"

    _click_marker(win, 0)
    win._release(win)
    win._on_map_click((34.51, 132.4))           # 約 1.1km＝十分に動いた
    assert note not in win.status[-1], (
        f"十分に動いたのに「変わらないかも」と言っている: {win.status[-1]}")


def test_a_stale_selection_is_refused_instead_of_written():
    """④窓の側が変わっていたら書き戻さず、選び直してもらうこと。

    位置だけで書き戻すと**黙って別の点を動かす**（B-068 / B-102 と同じ型）。
    """
    from core import i18n

    sink = _FakeWaypointSink([("TX", 34.5, 132.4), ("R1", 34.55, 132.45),
                              ("RX", 34.6, 132.5)])
    win = _edit_stub("waypoints", waypoints=sink)
    win._refresh_waypoints()
    _click_marker(win, 1)                       # R1 を選ぶ

    sink.points.pop(1)                          # 窓の側で R1 が消えた
    win._release(win)
    win._on_map_click((34.7, 132.7))

    assert sink.points == [("TX", 34.5, 132.4), ("RX", 34.6, 132.5)], (
        "消えた地点の位置へ書き戻した（別の地点が黙って動く）"
    )
    assert win.status[-1] == i18n.t("map_select_stale")
    assert win._selection is None


def test_the_selected_point_is_drawn_with_its_own_icon():
    """選択中は**見た目で分かる**こと（状態バーだけでは選択に気づけない）。

    ⚠️ 形は変えない＝役割（送信点／受信点／中継点）は形で読む約束なので、
    選択は囲うだけで表す。ここが見るのは「別のアイコンを引いていること」。
    """
    sink = _FakeWaypointSink([("TX", 34.5, 132.4), ("R1", 34.55, 132.45),
                              ("RX", 34.6, 132.5)])
    win = _edit_stub("waypoints", waypoints=sink)
    win._refresh_waypoints()
    _click_marker(win, 1)

    icons = [o.kwargs.get("icon") for o in win._map.alive
             if o.kind == "marker" and o.kwargs.get("command") is not None]
    assert icons == ["TX_ICON", "RELAY_SEL", "RX_ICON"], (
        f"選択中の点が他と同じ見た目のまま: {icons}")

    win._deselect()
    icons = [o.kwargs.get("icon") for o in win._map.alive
             if o.kind == "marker" and o.kwargs.get("command") is not None]
    assert icons == ["TX_ICON", "RELAY_ICON", "RX_ICON"], (
        f"選択を解いてもハイライトが残っている: {icons}")


# ============================================================
# ピック層はランチャー数値欄の写し（B-110 / B-111）
# ============================================================
# 地図が写している 3 つの層のうち、確定パス（バッチ表）と中継経路（地点列）は
# 通知で追従していたのに、**ピック層だけ「開いた時に 1 度読む」きり**だった。
# ⇒ 欄を空にしてもマーカーが残り、しかも地図は「TX/RX は指定済み」と思い続ける
# ので**素のクリックが何も書かなくなる**（置く先が無いという案内から抜けられない）。
def _single_stub(coords_ref: dict):
    """数値欄の代役（`coords_ref` を書き換えると欄を編集したことになる）。"""
    picks: list = []
    sink = SimpleNamespace(
        apply_map_pick=lambda role, lat, lon: picks.append((role, lat, lon)),
        current_path_coords=lambda: dict(coords_ref),
    )
    win = _edit_stub("coords", single=sink)
    win._load_single_coords()
    win._refresh_picks()          # 本物は `_apply_mode_visibility` が呼ぶ
    return win, picks


def _marker_count(win) -> int:
    return len([o for o in win._map.alive
                if o.kind == "marker" and o.kwargs.get("command") is not None])


def test_clearing_the_launcher_coords_clears_the_map():
    """①欄を消したら地図からも消えること（B-110）。"""
    ref = {"tx": (34.5, 132.4), "rx": (34.6, 132.5)}
    win, _picks = _single_stub(ref)
    assert _marker_count(win) == 2

    ref.clear()                                 # 欄を空にした
    win.on_single_coords_changed()

    assert _marker_count(win) == 0, "座標を消したのにマーカーが残っている"
    assert win._tx_coord is None and win._rx_coord is None
    assert not [o for o in win._map.alive if o.kind == "path"], (
        "座標を消したのに経路線が残っている")


def test_clearing_the_launcher_coords_reopens_the_plain_click():
    """②消した後は、離れた場所への素のクリックがまた置けること（B-111）。

    ここが「消しても描画が消えない」の実害＝残った写しのせいで `_pick_next` が
    None のままになり、**新しい地点を置く動線そのものが死ぬ**。
    """
    ref = {"tx": (34.5, 132.4), "rx": (34.6, 132.5)}
    win, picks = _single_stub(ref)
    assert win._pick_next is None               # 両方そろっている＝置く先が無い

    ref.clear()
    win.on_single_coords_changed()
    assert win._pick_next == "tx", "欄を空にしても「置く先が無い」ままになっている"

    win._on_map_click((36.0, 140.0))            # 遠く離れた場所
    assert picks == [("tx", 36.0, 140.0)], f"新しい地点を置けていない: {picks}"


def test_right_click_places_a_point_whose_marker_is_off_screen():
    """④両方そろっていても、右クリックなら**その場に置き直せる**こと（B-111）。

    I-098 で素クリックの上書きを止めたぶん、置き直しの入口が「マーカーを選ぶ」
    1 本になった＝地図を遠くへ送ると*その入口が画面の外*にあり、動線ごと死ぬ。
    右クリックはマーカーの位置に依存しない（役割は利用者が名指しする）。
    """
    ref = {"tx": (34.5, 132.4), "rx": (34.6, 132.5)}
    win, picks = _single_stub(ref)
    assert win._pick_next is None               # 素のクリックは何も書かない状態

    win._place_from_menu("tx", 36.0, 140.0)     # 遠く離れた場所へ TX を置き直す

    assert picks == [("tx", 36.0, 140.0)], f"右クリックで置き直せていない: {picks}"
    assert win._tx_coord == (36.0, 140.0)
    assert win._rx_coord == (34.6, 132.5), "名指ししていない側まで動いた"
    from core import i18n
    assert win.status[-1].startswith(
        i18n.t("map_moved").format(label=i18n.t("map_marker_tx"), dist="")[:6]), (
        f"どれをどれだけ動かしたかを返していない: {win.status[-1]}")


def test_the_right_click_menu_is_reused_instead_of_piling_up(monkeypatch):
    """**右クリックのたびにメニューを作らない**こと（2026-08-24・B-121）。

    以前は `_on_right_click` が毎回 `tk.Menu` を作り、`finally` は
    `grab_release()` だけだった＝メニューは**親（地図）の子として残り続ける**ので、
    長く開けたまま右クリックを繰り返すと Tcl ウィジェットとクロージャが積み上がり、
    DPI・テーマ変更時の走査対象（`theme._walk_menus`）も増え続ける。

    ⚠️ **ラベルは使い回さない**＝言語を切り替えたら次に開くメニューは新しい言語で
    出ること（使い回すのはウィジェットであって、その中身ではない）。
    """
    import tkinter as tk
    from tkinter import ttk

    import pytest

    from core import i18n

    pytest.importorskip("tkinter")
    root = tk.Tk()
    root.withdraw()
    try:
        i18n.set_lang("ja")
        holder = ttk.Frame(root)
        holder.convert_canvas_coords_to_decimal_coords = (   # type: ignore[attr-defined]
            lambda x, y: (35.0, 139.0))
        win = MapWindow.__new__(MapWindow)
        win._map = holder
        win._mode = SimpleNamespace(get=lambda: "coords")
        win._busy = False
        # メニューを実際に出すと掴み（grab）が残るので、出す口だけ塞ぐ。
        monkeypatch.setattr(tk.Menu, "tk_popup", lambda self, *a, **k: None)
        event = SimpleNamespace(x=10, y=20, x_root=100, y_root=200)

        for _ in range(5):
            win._on_right_click(event)

        menus = [w for w in holder.winfo_children() if isinstance(w, tk.Menu)]
        assert len(menus) == 1, (
            f"右クリック 5 回でメニューが {len(menus)} 個残っている（B-121）＝"
            "地図を閉じるまで解放されず、テーマ・DPI 変更時の走査対象も増え続ける。"
        )
        assert menus[0].index("end") == 1, (
            "使い回したメニューの項目が積み上がっている（毎回 2 項目のはず）。"
        )
        assert menus[0].entrycget(0, "label") == i18n.t("map_menu_place_tx")

        i18n.set_lang("en")
        win._on_right_click(event)
        assert menus[0].entrycget(0, "label") == i18n.t("map_menu_place_tx"), (
            "言語を切り替えてもメニューの字が古いまま＝ウィジェットと一緒に"
            "**ラベルまで**使い回している（B-121 の処方は毎回組み直すこと）。"
        )
    finally:
        i18n.set_lang("ja")
        root.destroy()


def test_right_click_placement_is_refused_outside_the_pick_modes():
    """④の裏＝ピック層を持たないモードでは書かないこと。

    キャッシュ管理・中継点モードで TX/RX を書くと、**そのモードでは見えない値**が
    黙って変わる（モードが見た目を決める、という原則の裏返し）。
    """
    ref = {"tx": (34.5, 132.4), "rx": (34.6, 132.5)}
    win, picks = _single_stub(ref)
    win._mode = SimpleNamespace(get=lambda: "cache")

    win._place_from_menu("tx", 36.0, 140.0)

    assert picks == [], "キャッシュ管理モードで座標が書き換わった"
    assert win._tx_coord == (34.5, 132.4)


def test_editing_one_launcher_coord_does_not_move_the_view():
    """③打鍵ごとに届く通知で**視野を動かさない**こと。

    `_load_single_coords` は中心とズームを合わせるが、それを編集のたびにやると
    *入力している最中に地図が飛ぶ*。追従するのは写しだけ。
    """
    ref = {"tx": (34.5, 132.4), "rx": (34.6, 132.5)}
    win, _picks = _single_stub(ref)
    moves: list = []
    win._map.set_position = lambda *a, **k: moves.append(a)
    win._map.set_zoom = lambda *a, **k: moves.append(a)
    win._map.fit_bounding_box = lambda *a, **k: moves.append(a)

    ref["rx"] = (35.9, 139.9)
    win.on_single_coords_changed()

    assert moves == [], f"編集のたびに地図の視野が動いた: {moves}"
    assert win._rx_coord == (35.9, 139.9), "写しが新しい値になっていない"


# ============================================================
# 選択を解いたら「置く先」も元に戻す（B-112）
# ============================================================
# 選ぶと `_pick_next` にその役割が入る（＝交互ピックの一般化）。ところが解く側は
# `_selection` しか落としておらず、**役割の指名だけが残る**。⇒ Esc で解いた直後の
# 素のクリックが、解いたはずの点を黙って動かす（I-098 の芯＝「素のクリックでは
# 黙って書き換わらない」の裏切り）。
def test_escape_also_forgets_which_point_was_selected():
    """①Esc で解いたら、素のクリックはまた「何も書かない」に戻ること。"""

    ref = {"tx": (34.5, 132.4), "rx": (34.6, 132.5)}
    win, picks = _single_stub(ref)
    assert win._pick_next is None               # 両方そろっている＝置く先が無い

    _click_marker(win, 0)                       # TX を選ぶ
    win._release(win)
    win._deselect()                             # Esc（本物は `<Escape>` から）

    win._on_map_click((36.0, 140.0))
    assert picks == [], f"解いたはずの点が素のクリックで動いた: {picks}"
    assert win.status[-1] == _idle_hint(None)


def test_clicking_the_same_marker_twice_also_forgets_it():
    """①の裏＝同じマーカーをもう一度押して解いたときも同じであること。"""
    ref = {"tx": (34.5, 132.4), "rx": (34.6, 132.5)}
    win, picks = _single_stub(ref)

    _click_marker(win, 1)                       # RX を選ぶ
    win._release(win)
    _click_marker(win, 1)                       # もう一度押す＝解く
    win._release(win)

    assert win._selection is None
    assert win._pick_next is None, "選択を解いても役割の指名が残っている"
    win._on_map_click((36.0, 140.0))
    assert picks == [], f"解いたはずの点が素のクリックで動いた: {picks}"


# ============================================================
# 連続追加のペア成立は「順番」ではなく「そろったこと」で決まる（B-113）
# ============================================================
# 素のクリックしか無かった頃は `TX → RX` の順しか作れなかったので、行を足す条件を
# 「いま置いたのが RX」で代用できていた。B-111 の右クリックは**役割を名指しできる**
# ＝`RX → TX` の順が実在するようになり、両方そろっても行が足されないまま
# `_pick_next` が None になる（＝その先どこも押せない行き止まり）。
def _append_stub():
    added: list = []
    win = _edit_stub("append")
    win._append_sink = SimpleNamespace(
        append_path=lambda tx, rx: (added.append((tx, rx)), f"P{len(added)}")[1],
        existing_paths=lambda: [],
    )
    win._pick_next = "tx"
    return win, added


def test_the_pair_is_committed_whichever_role_is_placed_last():
    """RX を先に置いても、TX がそろった時点で 1 行足すこと。"""
    win, added = _append_stub()

    win._place_from_menu("rx", 34.6, 132.5)     # 右クリック＝役割を名指し
    win._place_from_menu("tx", 34.5, 132.4)

    assert added == [((34.5, 132.4), (34.6, 132.5))], (
        f"RX → TX の順だと経路が足されない: {added}")
    assert win._pick_next == "tx", "次の TX 待ちに戻っていない（行き止まり）"


def test_the_usual_order_still_commits_the_pair():
    """裏＝従来どおり TX → RX の順でも 1 行だけ足すこと。"""
    win, added = _append_stub()

    win._on_map_click((34.5, 132.4))
    win._on_map_click((34.6, 132.5))

    assert added == [((34.5, 132.4), (34.6, 132.5))], f"従来の順が壊れた: {added}"
    assert win._pick_next == "tx"


# ============================================================
# 破棄経路の静的ガード
# ============================================================
# close_map_safely の docstring は「マップを破棄し得る経路は必ずこの関数を通す」
# と要求するが、これは関数の中身のテストでは守れない（新しい経路が手順をコピー
# したときに落ちるものが無い）。マップ実体を直接 destroy してよい唯一の場所を
# ここに固定し、増えたら落とす。
_ALLOWED_MAP_DESTROY = {("map_window.py", "MapWindow._destroy")}

_MAP_DESTROY_RE = re.compile(r"\b(?:self\._map|map_widget)\s*\.\s*destroy\s*\(")


def _enclosing_qualname(tree: ast.Module, lineno: int) -> str:
    """行番号を含む最も内側の class/def を "Class.func" 形式で返す。"""
    best, best_span = "<module>", None
    stack: list[tuple[ast.AST, str]] = [(tree, "")]
    while stack:
        node, prefix = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                end = child.end_lineno or child.lineno
                if child.lineno <= lineno <= end:
                    span = end - child.lineno
                    if best_span is None or span <= best_span:
                        best, best_span = name, span
                stack.append((child, f"{name}."))
            else:
                stack.append((child, prefix))
    return best


def test_map_widget_is_destroyed_only_through_close_map_safely():
    """マップ実体の destroy を呼ぶ場所が増えていないこと。

    tkintermapview の after ループを止めずに破棄すると
    `invalid command name ...update_canvas_tile_images` が出る。この破棄バグは
    機能追加のたびに新しい経路から再発してきたため、手順は close_map_safely に
    集約されている。新たな破棄経路を足すときは、この関数を通した上で
    _ALLOWED_MAP_DESTROY を更新すること。
    """
    found: set[tuple[str, str]] = set()
    for name in sorted(os.listdir(_VIEWS_DIR)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(_VIEWS_DIR, name)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        for i, line in enumerate(src.splitlines(), start=1):
            if _MAP_DESTROY_RE.search(line):
                found.add((name, _enclosing_qualname(tree, i)))

    assert found == _ALLOWED_MAP_DESTROY, (
        "マップ実体の破棄経路が変化した。close_map_safely を通しているか確認し、"
        f"意図した変更なら _ALLOWED_MAP_DESTROY を更新すること: {found}"
    )


# ============================================================
# 状態バーの字は `_pick_next` の従属変数（B-115）
# ============================================================
# 🔴 **開いた瞬間のヒントだけが取り残されていた**＝`_build_ui` が
# `_pick_next = "tx"`（初期値）のままヒントを出し、その後の `_load_single_coords`
# が写しを取り込んで `_pick_next` を derive し直すのに、**字を書き直さない**。
# ⇒ ランチャーに座標が入っている（＝いちばん普通の）開き方では、両方のマーカーが
# 出ているのに「地図をクリックして TX を指定します」と出続ける。**その案内どおりに
# クリックしても何も起きない**うえ、右クリックで置き直せること（B-111）も伝わらない。
#
# ⚠️ 通知の側（`on_single_coords_changed`）は 2026-08-22 の時点で既に `_set_idle()`
# を呼んでおり、コメントで理由まで書いてあった＝**同じ仕事の 2 つの入口のうち
# 片方だけが直っていた**。B-112 のクラス点検が「選択を落とす場所」に閉じてしまい、
# 本当のクラス（**`_pick_next` が動いたのに字が動かない場所**）を見ていなかった。
def _hint_stub(monkeypatch, coords_ref: dict):
    """本物の `_set_idle` を通す代役（字は `win.status` へ積む）。"""
    from views import theme

    monkeypatch.setattr(theme, "muted_foreground", lambda _w: "#808080")
    picks: list = []
    sink = SimpleNamespace(
        apply_map_pick=lambda role, lat, lon: picks.append((role, lat, lon)),
        current_path_coords=lambda: dict(coords_ref),
    )
    win = _edit_stub("coords", single=sink)
    win._map.set_zoom = lambda *a, **k: None
    win._map.set_position = lambda *a, **k: None
    win._status_clear_id = None
    win._status_var = SimpleNamespace(set=lambda t: win.status.append(t))
    win._status_label = SimpleNamespace(config=lambda **_k: None)
    # 本物を差し戻す（`_edit_stub` は筋を見るために差し替えている）。
    win._set_idle = MethodType(MapWindow._set_idle, win)
    win._set_status = MethodType(MapWindow._set_status, win)
    return win, picks


def _idle_hint(key_or_none):
    """`_set_idle` が出すはずの字（None＝両方そろっている）。"""
    from core import i18n

    if key_or_none is None:
        return (i18n.t("map_pair_set") + " " + i18n.t("map_move_affordance")
                + " " + i18n.t("map_place_affordance"))
    return i18n.t(key_or_none) + " " + i18n.t("map_move_affordance")


def test_opening_the_map_with_both_coords_says_they_are_already_set(monkeypatch):
    """①ランチャーに両方入った状態で開いたら、開いた瞬間からそう言うこと。

    実機の RC1 スクリーンショット（2026-08-22）で発覚。マーカーは 2 つ出ているのに
    「地図をクリックして TX（送信点）を指定します」と出ていた。
    """
    ref = {"tx": (34.3966, 132.4596), "rx": (34.3714, 132.5347)}
    win, _picks = _hint_stub(monkeypatch, ref)

    win._set_idle()             # 本物は `_build_ui` の末尾（`_pick_next` は初期値 "tx"）
    win._load_single_coords()   # 本物は `__init__` の続き（写しを取り込む）

    assert win._pick_next is None, "両方そろっているのに置く先が残っている"
    assert win.status[-1] == _idle_hint(None), (
        f"開いた瞬間のヒントが取り残されている: {win.status[-1]}")


def test_opening_the_map_with_one_coord_still_asks_for_the_other(monkeypatch):
    """①の裏＝片方だけなら、空いている方を指す字のままであること。"""
    ref = {"rx": (34.3714, 132.5347)}
    win, _picks = _hint_stub(monkeypatch, ref)

    win._set_idle()
    win._load_single_coords()

    assert win._pick_next == "tx"
    assert win.status[-1] == _idle_hint("map_coords_hint_tx"), (
        f"空いている方を指していない: {win.status[-1]}")


def test_opening_the_map_with_no_coords_keeps_the_plain_hint(monkeypatch):
    """①の裏 2＝何も入っていない開き方（従来どおり）。"""
    win, _picks = _hint_stub(monkeypatch, {})

    win._set_idle()
    win._load_single_coords()

    assert win._pick_next == "tx"
    assert win.status[-1] == _idle_hint("map_coords_hint_tx")


# ============================================================
# 選択を落とす経路の静的ガード（B-112）
# ============================================================
# 選ぶと `_pick_next` に役割が入るので、**落とす側も同じだけの仕事をしないと**
# 指名だけが残る（＝素のクリックが黙って書き換える）。口は `_forget_selection`
# 1 つに寄せてあるが、これは中身のテストでは守れない——新しい経路が
# `self._selection = None` を書き写しても、落ちるものが無い。⇒ 直に落としてよい
# 場所をここに固定し、増えたら落とす。
#
# ⚠️ 下の 4 か所が許されているのは「**直後か直前に座標が動き、`_advance_pick` が
# 必ず走る**」から（＝指名は derive し直されている）。新しい経路を足すときは、
# その条件を満たすか確かめ、満たさないなら `_forget_selection` を通すこと。
_ALLOWED_SELECTION_CLEAR = {
    ("map_picks.py", "_PickMixin._forget_selection"),   # 唯一の正規の口
    ("map_picks.py", "_PickMixin._on_map_click"),       # 移動が成立した直後
    ("map_picks.py", "_PickMixin._place_from_menu"),    # 直後に `_place_pick`
    ("map_picks.py", "_PickMixin._move_selected"),      # ピック層以外だけが来る
    ("map_window.py", "MapWindow.__init__"),            # 初期化（`_pick_next` も隣で置く）
}

# ⚠️ **タプル代入も拾う**＝`_forget_selection` 自身が
# `sel, self._selection = self._selection, None` の形なので、`= None` だけを見ると
# **正規の口も、それを書き写した新しい経路も、どちらも見えない**（ゲートの壊れ方①
# ＝一度も落ちない）。値の側に `None` があることだけを条件にする。
_SELECTION_CLEAR_RE = re.compile(r"\bself\._selection\s*=[^=]*\bNone\b")


def test_the_selection_is_cleared_only_where_the_pick_target_is_re_derived():
    """選択を直に落とす場所が増えていないこと（B-112 の再発防止）。"""
    found: set[tuple[str, str]] = set()
    for name in sorted(os.listdir(_VIEWS_DIR)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(_VIEWS_DIR, name)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        for i, line in enumerate(src.splitlines(), start=1):
            if _SELECTION_CLEAR_RE.search(line):
                found.add((name, _enclosing_qualname(tree, i)))

    assert found == _ALLOWED_SELECTION_CLEAR, (
        "選択を直に落とす場所が変化した。`_pick_next` が derive し直されるか確認し"
        "（されないなら `_forget_selection` を通す）、意図した変更なら "
        f"_ALLOWED_SELECTION_CLEAR を更新すること: {found}"
    )


# ============================================================
# 背景タイルの 2 択（I-028）
# ============================================================
def _open_map_window(monkeypatch):
    """地図窓をフェイクのタイル部品で開く（ネットワークへ出ない）。

    差し替えるのは `TkinterMapView` だけ＝レイヤ切り替えの配線（Combobox →
    `set_tile_server` → 出典表記）は本物を通す。
    """
    import tkinter as tk
    from tkinter import ttk

    import pytest as _pytest
    from conftest import make_themed_root

    import views.map_window as mw

    class _FakeMap(ttk.Frame):
        def __init__(self, master, **_kw):
            super().__init__(master)
            self.canvas = tk.Canvas(self, width=200, height=150)
            self.canvas.pack(fill="both", expand=True)
            self.running = False
            self.tile_calls: list[tuple] = []

        def set_tile_server(self, url, max_zoom=None):
            self.tile_calls.append((url, max_zoom))

        def set_position(self, *a, **k): pass
        def set_zoom(self, *a, **k): pass
        def add_left_click_map_command(self, *a, **k): pass
        def set_polygon(self, *a, **k): return SimpleNamespace(delete=lambda: None)
        def set_marker(self, *a, **k): return SimpleNamespace(delete=lambda: None)
        def set_path(self, *a, **k): return SimpleNamespace(delete=lambda: None)
        def convert_canvas_coords_to_decimal_coords(self, *a, **k): return (35.0, 139.0)
        def get_position(self): return (35.0, 139.0)
        def get_zoom(self): return 8

    monkeypatch.setattr(mw, "TkinterMapView", _FakeMap)
    root = make_themed_root()
    root.withdraw()
    win = mw.MapWindow(root, {"proxy_url": ""})
    return root, win, _pytest


def test_layer_switch_changes_tiles_and_attribution(monkeypatch):
    """背景を切り替えたら**タイルと出典表記の両方**が変わること。

    ⚠️ 表記の追従がここの本体＝タイルだけ替えると「航空写真を見ながら
    『出典: 淡色地図』」という**事実と食い違う刻印**になる（3.1「刻印」トラックの
    精神にも反する）。片方だけ直る余地を残さないため、1 つのテストで両方を見る。
    """
    from core import i18n


    prev = i18n.current_lang()
    i18n.set_lang("ja")
    root, win, _pytest = _open_map_window(monkeypatch)
    try:
        assert win._layer == "pale"
        assert "淡色" in win._attribution.cget("text")

        win._layer_box.set(i18n.t("map_layer_photo"))
        win._on_layer_changed()

        assert win._layer == "photo"
        url, _zoom = win._map.tile_calls[-1]
        assert "seamlessphoto" in url, f"航空写真のタイルを見ていない: {url}"
        assert "航空写真" in win._attribution.cget("text"), (
            "タイルだけ替わって出典表記が淡色地図のまま＝事実と食い違う刻印"
        )
        # 戻れること（写真には地名が写らないので淡色との行き来が要件）。
        win._layer_box.set(i18n.t("map_layer_pale"))
        win._on_layer_changed()
        assert "pale" in win._map.tile_calls[-1][0]
        assert "淡色" in win._attribution.cget("text")
    finally:
        i18n.set_lang(prev)
        root.destroy()


def test_attribution_is_a_widget_over_the_canvas(monkeypatch):
    """出典表記が**canvas の中身ではなくウィジェット**であること（B-027）。

    canvas に `create_text` で描くと、**あとから足されるタイル画像に埋もれる**。
    実際そうなっており、実画面では出典が 1 度も見えていなかった（地理院タイルの
    出典表記は表示義務があるので、見えないのは機能欠落ではなく規約の問題）。
    子ウィジェットは canvas の中身より常に上に描かれるので、`place` すれば
    z 順の争いが構造的に消える。

    併せて**背景色を持つこと**も見る＝淡色地図（明るい）と航空写真（暗い）で
    地の色が真逆なので、地に直接描くとどちらかで必ず読めなくなる。
    """
    import tkinter as tk

    root, win, _pytest = _open_map_window(monkeypatch)
    try:
        assert isinstance(win._attribution, tk.Widget), (
            "出典が canvas アイテムのまま＝タイル画像に埋もれて見えない"
        )
        assert win._attribution.winfo_manager() == "place", (
            "地図の上に置かれていない（レイアウトに流されると地図外に出る）"
        )
        assert win._attribution.cget("bg"), "背景色が無い＝暗いタイルの上で沈む"
    finally:
        root.destroy()


def test_the_selection_guard_is_lowered_on_button_release(monkeypatch):
    """「選ぶための押下」の印が、押下 → 離しの 1 巡で必ず降りること（I-098）。

    印は*次に届く地図クリックを 1 回だけ捨てる*ためのもの。マーカーを押したまま
    地図を送った（クリックが届かなかった）ときに降ろす口が無いと、**次の本物の
    クリックが飲み込まれる**＝押しても何も起きない状態が 1 回混ざる。
    """
    root, win, _pytest = _open_map_window(monkeypatch)
    try:
        root.update()                    # 窓を実体化してからでないと届かない
        win._select_guard = True
        win._map.canvas.event_generate("<ButtonRelease-1>", x=5, y=5)
        root.update()
        assert win._select_guard is False, (
            "離しても印が降りていない＝次のクリックが 1 回飲み込まれる"
        )
    finally:
        root.destroy()


def test_only_two_basemaps_are_offered():
    """背景の選択肢は**2 つだけ**（YAGNI の釘・I-028）。

    標準地図・白地図・陰影起伏…と足せる作りにすると、次に必ず「全部出せる
    ように」が来る。**増やすなら判断ごと**、というのをテストで留める。
    """
    from views.map_window import _TILE_LAYERS

    assert set(_TILE_LAYERS) == {"pale", "photo"}, (
        "背景の選択肢を増やすなら、UI の器（Combobox）と出典表記の対応、"
        "そして『2 つに留める』と決めた判断（ISSUES.md I-028）を見直すこと。"
    )


# ------------------------------------------------------------
# 座標入力モードのピック層も同じ再入に耐えること（B-104＝B-080 の残り面）
# ------------------------------------------------------------
# B-080 の処方（台帳ごと取り出してから消す／描き直しを直列化する）は waypoints と
# committed の 2 面で止まっていた。**同じ機構は TX/RX のピックマーカーと経路線・
# 距離ラベルにもある**＝`delete()` が `canvas.update()` を回すので、素早い連続
# クリックでは消している最中に次のクリックが届く。
#
# 🔑 守るべき不変条件は 2 つ＝**①最後のクリックが座標に載る**（数値欄が
# source of truth で、地図はそれを写す）**②生きている地図オブジェクトが
# 台帳から外れない**（外れた分は消し手が無く、以後ずっと地図に残る）。

def _pick_stub():
    """`_set_pick_marker` を呼ぶのに要る最小限だけを持つ MapWindow。"""
    from views.map_window import MapWindow

    win = MapWindow.__new__(MapWindow)
    win._map = _ReentrantMapWidget()
    win._mode = SimpleNamespace(get=lambda: "coords")
    win._tx_coord = None
    win._rx_coord = None
    win._pick_objects = []
    win._pick_images = []
    win._tx_icon, win._rx_icon = "TX_ICON", "RX_ICON"
    win._tx_icon_sel, win._rx_icon_sel = "TX_SEL", "RX_SEL"
    win._relay_icon = win._relay_icon_sel = None
    win._selection = None
    win._make_distance_badge = lambda text: f"BADGE:{text}"
    win._redraw_state = {}
    return win


def test_pick_marker_survives_a_click_during_the_clear():
    """TX の置き換え中に次のクリックが割り込んでも、最後の 1 点だけが残ること。"""
    win = _pick_stub()
    win._set_pick_marker("tx", 34.5, 132.4)          # 1 回目（消すものは無い）
    # 2 回目の「古い TX を消す」最中に 3 回目のクリックが届く。
    win._map.on_delete = lambda: win._set_pick_marker("tx", 34.7, 132.7)
    win._set_pick_marker("tx", 34.6, 132.5)

    assert win._tx_coord == (34.7, 132.7), (
        f"最後のクリックが座標に反映されていない（{win._tx_coord}）")
    alive = win._map.alive
    assert len(alive) == 1, (
        f"地図に {len(alive)} 個残っている（TX は 1 つのはず＝孤児マーカー）")


def test_pick_path_survives_a_click_during_the_clear():
    """経路線・距離ラベルの引き直し中に割り込まれても、二重に載らないこと。"""
    win = _pick_stub()
    win._set_pick_marker("tx", 34.5, 132.4)
    win._set_pick_marker("rx", 34.6, 132.5)          # 線＋距離ラベルが出る
    win._map.on_delete = lambda: win._set_pick_marker("rx", 34.8, 132.8)
    win._set_pick_marker("rx", 34.7, 132.7)

    assert win._rx_coord == (34.8, 132.8), (
        f"最後のクリックが座標に反映されていない（{win._rx_coord}）")
    kinds = sorted(o.kind for o in win._map.alive)
    assert kinds == ["marker", "marker", "marker", "path"], (
        f"TX・RX・距離ラベル・線の 4 つのはずが {kinds}（割り込みぶんが残っている）")


def test_resetting_the_pick_layer_survives_a_click_of_its_own():
    """**リセットそのもの**が再入に耐えること（ペア確定 → 次の TX 待ちへ戻す途中）。

    ここで割り込むクリックは利用者の 3 回目のクリックそのものなので、**捨てずに
    新しい TX として生かす**（座標が正典・地図はその写し、という約束のまま）。
    見るのは「最後のクリックが座標に載り、地図がその座標ちょうどの写しであること」。
    """
    win = _pick_stub()
    win._set_pick_marker("tx", 34.5, 132.4)
    win._set_pick_marker("rx", 34.6, 132.5)
    drawn = list(win._map.alive)
    win._map.on_delete = lambda: win._set_pick_marker("tx", 34.9, 132.9)
    win._reset_active_pick()

    assert all(o.deleted for o in drawn), "リセット前の地図オブジェクトが消え残っている"
    assert win._tx_coord == (34.9, 132.9), (
        f"割り込んだクリックが座標に載っていない（{win._tx_coord}）")
    assert win._rx_coord is None, "RX がリセットされていない"
    alive = win._map.alive
    assert len(alive) == 1, (
        f"地図に {len(alive)} 個残っている（TX 1 つだけが座標の写し）")
