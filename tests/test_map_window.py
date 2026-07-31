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
    import i18n


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
