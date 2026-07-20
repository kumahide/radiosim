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
