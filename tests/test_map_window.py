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
from types import SimpleNamespace

from views.map_window import _MAP_DRAIN_MS, close_map_safely

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
    win._tx_icon = win._rx_icon = win._relay_icon = None
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
