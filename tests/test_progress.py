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

from views.progress import ProgressPump


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
