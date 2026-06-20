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

from types import SimpleNamespace

from views.map_window import _MAP_DRAIN_MS, close_map_safely


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
