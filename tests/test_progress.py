"""
tests/test_progress.py
======================
ProgressPump（ワーカースレッド → メインスレッドの進捗トランスポート）のテスト。

単一実行とバッチで二重実装されていた進捗の受け渡しを 1 部品へ括り出したもの。
**ライフサイクルの非対称が B-006 の停止バグを生んだ**経緯があるため、開始・停止・
停止後の残存ポーリングの振る舞いを重点的に固定する。scheduler はダックタイプ
なので、フェイクでヘッドレスに検証できる。
"""

import threading

import pytest

from views.progress import ProgressPump


class TclError(Exception):
    """tkinter を import せずに `TclError` を模す（型名で判定している側の検証）。

    ⚠️ **クラス名そのものを `TclError` にする**＝`__name__ = "TclError"` を
    クラス本体に書いても効かない（`type.__name__` はメタクラス側の記述子が
    優先されるので、実際の型名が返る）。最初の実装がこれで落ちた。
    """


_FakeTclError = TclError



class FakeScheduler:
    """`.after(ms, cb)` を記録するフェイク。発火は明示的に行う。"""

    def __init__(self):
        self.scheduled: list = []

    def after(self, ms, cb):
        self.scheduled.append((ms, cb))
        return len(self.scheduled)

    def fire_all(self):
        """スケジュール済みのコールバックを 1 巡だけ発火する。"""
        pending, self.scheduled = self.scheduled, []
        for _, cb in pending:
            cb()


def _pump(**kwargs):
    sched = FakeScheduler()
    seen: list = []
    pump = ProgressPump(sched, seen.append, **kwargs)
    return pump, sched, seen


# ============================================================
# 基本の受け渡し
# ============================================================
def test_delivers_all_items_in_order_by_default():
    """既定（バッチ用）は 1 件も落とさず順序どおり渡す。"""
    pump, sched, seen = _pump()
    pump.start()
    for i in range(5):
        pump.push(("event", i))
    sched.fire_all()
    assert seen == [("event", i) for i in range(5)]


def test_latest_only_collapses_to_the_last_item():
    """latest_only（単一実行用）は溜まった分を捨てて最新 1 件だけ渡す。

    標高取得は 1 サンプルごとに push されるため、全件描くと Tcl 呼び出しが
    取得時間そのものを支配する（B-006 で実測 30 倍差）。
    """
    pump, sched, seen = _pump(latest_only=True)
    pump.start()
    for i in range(200):
        pump.push((i, f"{i}%"))
    sched.fire_all()
    assert seen == [(199, "199%")]


def test_nothing_is_delivered_before_start():
    """start 前の push は配送されない（ポーリングが走っていない）。"""
    pump, sched, seen = _pump()
    pump.push("early")
    assert seen == []
    assert sched.scheduled == []


def test_start_discards_items_pushed_while_stopped():
    """停止中に押された積み残しは次の start へ持ち越さない。"""
    pump, sched, seen = _pump()
    pump.push("stale")
    pump.start()
    sched.fire_all()
    assert seen == []


# ============================================================
# ライフサイクル（B-006 の停止バグの再発防止）
# ============================================================
def test_poll_after_stop_delivers_nothing():
    """停止後に残存ポーリングが 1 回発火しても何も描かない。

    これが B-006 の修正中に作り込んだ回帰の正体。stop はキューを捨てるが、
    その時点で after 済みのポーリングが 1 回残っており、キュー破棄だけでは
    ワーカーが停止後に push した分を止められない（早期 return が要る）。
    """
    pump, sched, seen = _pump()
    pump.start()
    pump.push("during")
    seen.clear()

    pump.stop()
    # ワーカースレッドは完了通知の後にも push しうる。
    pump.push("after stop")
    sched.fire_all()          # stop 時点で残っていたポーリングが発火する
    assert seen == []


def test_stop_ends_the_polling_chain():
    """stop 後はポーリングが再スケジュールされない（永久に回らない）。"""
    pump, sched, seen = _pump()
    pump.start()
    assert len(sched.scheduled) == 1
    pump.stop()
    sched.fire_all()
    assert sched.scheduled == [], "停止後もポーリングが再スケジュールされている"


def test_start_is_idempotent():
    """二重 start でポーリング連鎖が 2 本にならない。

    2 本になると片方が stop を見ずに生き残り、停止したはずの表示が動く。
    """
    pump, sched, _ = _pump()
    pump.start()
    pump.start()
    sched.fire_all()
    assert len(sched.scheduled) == 1, "ポーリング連鎖が二重に走っている"


def test_can_restart_after_stop():
    """停止 → 再開ができる（単一実行は実行ごとに開始・停止する）。"""
    pump, sched, seen = _pump()
    pump.start()
    pump.stop()
    pump.start()
    pump.push("second run")
    sched.fire_all()
    assert seen == ["second run"]


def test_handler_stopping_the_pump_halts_remaining_items():
    """handler の中で stop したら、同じ回の残りイベントは配送しない。

    バッチの complete / error は handler 内で停止する。続けて消費すると
    停止後のイベントが完了表示を上書きしうる。
    """
    sched = FakeScheduler()
    seen: list = []

    def handler(item):
        seen.append(item)
        if item == "complete":
            pump.stop()      # 呼ばれるのは構築後なので前方参照でよい

    pump = ProgressPump(sched, handler)
    pump.start()
    pump.push("progress")
    pump.push("complete")
    pump.push("late")
    sched.fire_all()
    assert seen == ["progress", "complete"]


def test_stops_quietly_when_the_widget_is_destroyed():
    """破棄済みウィジェットへの after で例外を撒かず静かに止まること。

    実行中にウィンドウを閉じると `after` が `invalid command name` になる。
    閉じる側で stop するのが正道だが、破棄経路が増えたときに例外を撒かない
    （マップ破棄と同じクラスの失敗＝map_window.close_map_safely 参照）。
    """
    class Widget:
        """途中で破棄されるウィジェット（after が失敗するようになる）。"""

        destroyed = False

        def after(self, ms, cb):
            if self.destroyed:
                raise RuntimeError("invalid command name")

    widget = Widget()
    seen: list = []
    pump = ProgressPump(widget, seen.append)
    pump.start()

    widget.destroyed = True   # 実行中にウィンドウを閉じた
    pump.push("item")
    pump._poll()              # 例外が漏れない
    assert seen == ["item"]
    assert not pump.is_running, "破棄検知後もポーリングが有効なまま"


# ============================================================
# フェーズ切替・スレッド安全性
# ============================================================
def test_clear_drops_pending_without_stopping():
    """clear は積み残しを捨てるがポーリングは止めない（フェーズ切替）。"""
    pump, sched, seen = _pump()
    pump.start()
    pump.push("old phase")
    pump.clear()
    pump.push("new phase")
    sched.fire_all()
    assert seen == ["new phase"]
    assert pump.is_running


def test_push_is_safe_from_worker_threads():
    """複数ワーカーからの push が失われない（キューのスレッド安全性）。"""
    pump, sched, seen = _pump()
    pump.start()

    def worker(base):
        for i in range(50):
            pump.push(base + i)

    threads = [threading.Thread(target=worker, args=(t * 100,), daemon=True)
               for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    sched.fire_all()
    assert sorted(seen) == sorted(
        base * 100 + i for base in range(4) for i in range(50)
    )


# ============================================================
# ワーカー → UI の投函（B-061 → B-079）
# ============================================================
@pytest.fixture(autouse=True)
def _empty_mailbox():
    """投函の待ち行列はプロセスに 1 本なので、テスト間で持ち越さない。"""
    from views import progress

    def _drain():
        while True:
            try:
                progress._UI_MAILBOX.get_nowait()
            except Exception:
                return

    _drain()
    yield
    _drain()


class _Root:
    """`drain_ui_mailbox` の予約先（メインスレッド役）。"""

    def __init__(self):
        self.scheduled: list = []

    def after(self, ms, cb):
        self.scheduled.append((ms, cb))


def test_post_to_ui_does_not_touch_tk_at_all():
    """★ **投函でワーカーが Tk に触れないこと**（B-079 の芯）。

    ⚠️ ここが `widget.after()` に戻ると、**破棄済みの窓へ投函したワーカーが
    1 件あたり約 1.04 秒止まる**（`_tkinter` が非メインスレッドからの呼び出しを
    100ms×10 回待ってから諦める）。例外を抑えても待ちは消えない＝B-061 の
    修正では届かなかった残りがこれ。
    """
    from views.progress import post_to_ui

    class Widget:
        def after(self, ms, cb):
            raise AssertionError("ワーカースレッドから Tk を呼んだ")

    post_to_ui(Widget(), lambda: None)          # 例外が出なければ良い


def test_the_mailbox_is_delivered_on_the_main_thread():
    """投函は、配達役（メインスレッド）に渡って初めて `after` になること。"""
    from views.progress import drain_ui_mailbox, post_to_ui

    class Widget:
        def __init__(self): self.calls = []
        def after(self, ms, cb): self.calls.append((ms, cb))

    w = Widget()
    post_to_ui(w, lambda: None)
    post_to_ui(w, lambda: None, delay_ms=120)
    assert w.calls == [], "配達前に届いている＝投函がその場で Tk を呼んでいる"

    drain_ui_mailbox(_Root())
    assert [ms for ms, _ in w.calls] == [0, 120]


def test_the_mailbox_keeps_each_item_addressed_to_its_own_widget():
    """宛先は要素の側が持つ＝窓が複数あっても取り違えないこと。"""
    from views.progress import drain_ui_mailbox, post_to_ui

    class Widget:
        def __init__(self): self.calls = []
        def after(self, ms, cb): self.calls.append(cb)

    a, b = Widget(), Widget()
    post_to_ui(a, "a")
    post_to_ui(b, "b")
    drain_ui_mailbox(_Root())
    assert a.calls == ["a"] and b.calls == ["b"]


@pytest.mark.parametrize("error", [RuntimeError("main thread is not in main loop"),
                                   _FakeTclError("invalid command name")])
def test_delivery_is_quiet_when_the_widget_is_already_gone(error):
    """**破棄済みへの配達は静かに諦める**こと（B-061）。

    取得中に窓を閉じるのは利用者が普通にやる操作で、そのとき投函先はもう無い。
    配達役はメインスレッドなので、ここで諦めても**ワーカーは待たされない**。
    """
    from views.progress import drain_ui_mailbox, post_to_ui

    class Dead:
        def after(self, ms, cb): raise error

    alive_calls = []

    class Alive:
        def after(self, ms, cb): alive_calls.append(cb)

    post_to_ui(Dead(), lambda: None)
    post_to_ui(Alive(), "後続")
    drain_ui_mailbox(_Root())
    assert alive_calls == ["後続"], "死んだ宛先 1 件で配達が止まっている"


def test_delivery_does_not_swallow_real_bugs():
    """**飲むのは「Tk が無い」合図だけ**＝コールバック側の本物のバグは通す。

    ここを `except Exception` にすると、投函の失敗と実装の誤りが区別できなくなる。
    """
    from views.progress import drain_ui_mailbox, post_to_ui

    class Broken:
        def after(self, ms, cb): raise ValueError("これは本物のバグ")

    post_to_ui(Broken(), lambda: None)
    root = _Root()
    with pytest.raises(ValueError):
        drain_ui_mailbox(root)
    assert root.scheduled, (
        "本物のバグで配達そのものが止まった＝以後の投函が誰にも届かない"
    )


def test_delivery_schedules_the_next_round():
    """一度きりで終わらないこと（投函はいつ来るか分からない）。"""
    from views.progress import drain_ui_mailbox

    root = _Root()
    drain_ui_mailbox(root, interval_ms=1234)
    assert [ms for ms, _ in root.scheduled] == [1234]


def test_delivery_stops_when_the_root_is_gone():
    """ルートが死んでいたら静かに止まること（終了時に例外を出さない）。"""
    from views.progress import drain_ui_mailbox

    class DeadRoot:
        def after(self, ms, cb): raise _FakeTclError("application has been destroyed")

    drain_ui_mailbox(DeadRoot())                # 例外が出なければ良い


def test_posting_from_many_workers_loses_nothing():
    """複数ワーカーからの投函が失われないこと（キューのスレッド安全性）。"""
    from views.progress import drain_ui_mailbox, post_to_ui

    class Widget:
        def __init__(self): self.calls = []
        def after(self, ms, cb): self.calls.append(cb)

    w = Widget()

    def worker(base):
        for i in range(50):
            post_to_ui(w, base * 100 + i)

    threads = [threading.Thread(target=worker, args=(t,), daemon=True)
               for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    drain_ui_mailbox(_Root())
    assert sorted(w.calls) == sorted(
        base * 100 + i for base in range(4) for i in range(50)
    )


def test_no_worker_posts_to_tk_without_going_through_post_to_ui():
    """**ワーカーの中で素の `after` を呼んでいない**こと（静的検査・B-061）。

    ⚠️ 各所に try/except を撒く形は「思い出す規則」で、次にワーカーを足した人が
    忘れた瞬間に再発する。**投函の口を 1 つに保つ**ことを機械に守らせる。
    """
    import ast
    import pathlib

    offenders: list[str] = []
    for path in sorted(pathlib.Path(__file__).resolve().parent.parent.glob("views/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        targets = {
            (getattr(kw.value, "id", None) or getattr(kw.value, "attr", None))
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Thread"
            for kw in node.keywords if kw.arg == "target"
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in targets:
                offenders += [
                    f"{path.name}:{c.lineno} ({node.name})"
                    for c in ast.walk(node)
                    if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "after"
                ]
    assert not offenders, (
        "ワーカーが Tk へ直に投函している（progress.post_to_ui を通すこと）: "
        + ", ".join(offenders)
    )
