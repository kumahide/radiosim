"""
views/progress.py
=================
ワーカースレッド → メインスレッドへ進捗・イベントを渡す唯一の部品。

**なぜ1部品なのか（2.4b3・B-006 の反省）**

単一実行（launcher）とバッチ（batch_builder）は同じ「キュー＋`after` ポーリング」
を各々で実装していたが、**ライフサイクルが非対称**だった。バッチのポーラは
`__init__` で 1 回起動して永久に回り、単一のポーラは実行ごとに起動・停止する。
この非対称そのものが B-006 の停止バグ（停止後に残った 1 回のポーリングが完了表示を
上書きする）を生んだ。ライフサイクルを 1 つに畳めば、同種のバグが構造的に
存在できなくなる。

**なぜ完全には畳めないのか**

畳めるのは「トランスポート」だけで、パイプライン本体ではない。単一の出口は
what-if スライダ付きの対話グラフ、バッチの出口は確定成果物ファイルであり、
「シングル＝条件を詰める場／バッチ＝確定条件で成果物を作る場」という設計思想
そのものが違う。2 者の差はこのモジュールでは 2 つのパラメータに閉じている：

- `latest_only=True`（単一）: バーとラベルは「最新の状態」だけが意味を持つので、
  溜まった分は捨てて最後の 1 件だけ渡す。標高取得は 1 サンプルごとに push される
  ため、全件描くと Tcl 呼び出しが取得時間そのものを支配する（実測 30 倍差）。
- `latest_only=False`（バッチ）: `done` / `complete` が結果を運ぶので 1 件も
  落とせない。順序どおり全件渡す。

Tk 依存はダックタイピングの `scheduler`（`.after(ms, cb)` を持つ）だけで、
tkinter を import しない＝フェイクでヘッドレスに検証できる（close_map_safely と
同じ方針）。
"""

import gc
import queue
from typing import Any, Callable


# ============================================================
# 閉じた窓の残骸を、メインスレッドで掃く（B-050）
# ============================================================
# **同じ「メインスレッドでしか Tk に触れない」規律の裏側**なのでここに置く。
# ProgressPump が守るのは「ワーカーから Tk を呼ばせない」で、こちらが守るのは
# 「**ワーカーに Tk を掃除させない**」＝どちらも同じ境界の話。
#
# 🔴 何が起きるか（実測 2026-08-07）: `destroy()` は Tk オブジェクトを消さない。
# tkinter は親子で相互参照するので、窓を閉じても Python 側は**循環ゴミとして残る**。
# そのゴミを**ワーカースレッドの GC が拾うと**、`tkinter.Variable.__del__` が
# Tcl を呼び、CPython の `_tkinter` が**メインスレッド以外からの呼び出しを
# 100ms×10 回待ってから** RuntimeError にする（実測 1.036 秒/回）。
#
#   窓を 1 枚閉じた後、ワーカーの `gc.collect()` にかかった時間（実測）:
#     条件探索 35.8 秒 ／ 地図 29.2 秒 ／ グラフ 23.6 秒 ／ 中継経路 19.8 秒
#     ／ バッチ 13.6 秒
#
# ⇒ **バッチや条件探索の進捗が、理由の分からないまま 20〜30 秒止まる**（落ちはしない
#   ＝ルートが生きているので `Tcl_AsyncDelete` には至らない）。
#
# **なぜ「窓を閉じるときに 1 回」ではなく定期なのか**＝地図窓は閉じても
# `pre_cache` スレッドが `running=False` に気づくまで**約 2.4 秒**かかり、その間
# widget を掴んでいる。**ゴミになるのは閉じた瞬間ではない**ので、閉じる処理の中で
# 掃いても地図だけ取りこぼす（測定でも一度これで誤判定した）。
#
# **なぜ窓ごとに書かないのか**＝それは「思い出す規則」で、窓を 1 つ足した人が
# 忘れた瞬間に再発する（→ [[feedback-promote-recurring-checks]]）。ルートに 1 つ
# 置けば、**これから増える窓にも自動で効く**。
#
# コストは実測 `gc.collect()` 1 回 **8.9ms**（全テストを読み込んだ重い状態での値
# ＝アプリ本体はこれより軽い）。2 秒間隔なら 0.5% 未満。
_TK_SWEEP_MS = 2000


def sweep_tk_garbage(root, *, interval_ms: int = _TK_SWEEP_MS) -> None:
    """閉じた窓の残骸をメインスレッドで回収し続ける（自分で次回を予約する）。

    引数:
      root        : `.after(ms, cb)` を持つ生存中の tk ルート。
      interval_ms : 掃く間隔。

    ルートが破棄されたら（＝アプリ終了）静かに止まる。
    """
    gc.collect()
    try:
        root.after(interval_ms,
                   lambda: sweep_tk_garbage(root, interval_ms=interval_ms))
    except Exception:
        pass          # ルートが無い＝終了処理中。掃く相手も、次回も無い


# ============================================================
# ワーカーから UI へ投函する（B-061 → B-079）
# ============================================================
# ワーカーが `root.after(...)` を直に呼ぶと、**取得中に窓を閉じた瞬間**に
# 投函先が消えている＝`RuntimeError: main thread is not in main loop` が
# ワーカースレッドで上がる。**例外は画面に出ずログに残るだけ**なので、
# 気づかれないまま配布される（Codex の独立レビューで発覚）。
#
# **なぜ各所に try/except を撒かないのか**＝それは列挙で塞ぐ穴で、次に
# ワーカーを足した人が忘れた瞬間に再発する（→ [[feedback-promote-recurring-checks]]）。
# 投函の口を 1 つにして、**これから増えるワーカーにも自動で効く**形にする。
#
# 🔴 **B-079＝口を 1 本にしても、中身がまだ `widget.after()` だった**。例外は
# 抑えられていたが、**破棄済みの窓へ投函すると `_tkinter` がメインスレッド以外
# からの呼び出しを 100ms×10 回待ってから諦める**ので、ワーカーは 1 件あたり
# 約 1.04 秒止まる（Codex が実 Tk で再現）。上の `sweep_tk_garbage` が潰した
# のと**同じ待ちループ**＝ワーカーが Tcl に触れる限り消えない。
#
# ⇒ **投函は Queue への `put` だけにし、Tk を触るのはメインスレッドの
# `drain_ui_mailbox`（`after` ポーリング）に一本化する**。これは進捗の側で
# 2.4b3 に既に実現している形（`ProgressPump`）と同じで、新しい設計ではない。
# メインスレッドからの `after` は死んだ窓でも**即座に**失敗する（待ちループは
# 非メインスレッドの呼び出しにしか無い）。
#
# ⚠️ **`winfo_exists()` で生死を確かめてから投函しない**＝それ自体が
# ワーカースレッドからの Tcl 呼び出しで、防ごうとしている危険そのものを増やす。
# **投函を試して、死んでいた合図（例外）を受け取る**ほうが Tcl への接触が少ない
# （その「試す」を、メインスレッド側へ移したのがこの形）。
# ⚠️ **飲むのは 2 種類だけ**（`RuntimeError` / `TclError`＝インタプリタが無い合図）。
# 例外を広く飲むと、コールバック側の本物のバグまで静かに消える。
# ⚠️ 型名で判定しているのは、このモジュールが **tkinter を import しない**約束
# （フェイクでヘッドレスに検証できる）を守るため。
_DEAD_TK_EXCEPTIONS = ("RuntimeError", "TclError")

# 投函の待ち行列。**プロセスに 1 本**＝窓ごとに持つと、窓を足した人が配線を
# 忘れた瞬間に「投函しても誰も配らない」静かな故障になる（ルートに 1 つ置く
# `sweep_tk_garbage` と同じ理由）。宛先は要素の側が持つ。
_UI_MAILBOX: "queue.Queue[tuple[Any, Callable[[], None], int]]" = queue.Queue()

# 配達の間隔。進捗（`ProgressPump` の既定 50ms）と揃える＝ここを流れるのは
# 完了・エラーの通知なので、人には気づけない遅れ。
_UI_MAILBOX_MS = 50


def post_to_ui(widget, func, *, delay_ms: int = 0) -> None:
    """ワーカースレッドから UI スレッドへコールバックを投函する。

    引数:
      widget   : `.after(ms, cb)` を持つウィジェット（破棄済みでもよい）。
      func     : UI スレッドで実行したい呼び出し可能オブジェクト。
      delay_ms : 遅延（既定 0＝次の空き時間）。

    **Tk には一切触れない**（キューに積むだけ）ので、投函先が生きているかに
    関わらず即座に戻る。実際に配るのは `drain_ui_mailbox`（メインスレッド）で、
    宛先が既に破棄されていればそこで静かに捨てられる＝**結果を捨ててよい場面
    でしか使わない**のは B-061 から変わらない。
    """
    _UI_MAILBOX.put((widget, func, delay_ms))


def drain_ui_mailbox(root, *, interval_ms: int = _UI_MAILBOX_MS) -> None:
    """溜まった投函をメインスレッドで配り続ける（自分で次回を予約する）。

    引数:
      root        : `.after(ms, cb)` を持つ生存中の tk ルート。
      interval_ms : 配達の間隔。

    ルートが破棄されたら（＝アプリ終了）静かに止まる。
    """
    try:
        while True:
            try:
                widget, func, delay_ms = _UI_MAILBOX.get_nowait()
            except queue.Empty:
                break
            try:
                widget.after(delay_ms, func)
            except Exception as ex:
                if type(ex).__name__ not in _DEAD_TK_EXCEPTIONS:
                    raise
                # 宛先の窓はもう無い＝配る先が無いだけ。捨てて次へ。
    finally:
        # ⚠️ 次回の予約は `finally` に置く＝**本物のバグ（上で再送出した例外）が
        # 配達そのものを止めない**。ここが try の後ろだと、1 度の実装ミスで以後の
        # 投函が全部届かなくなる（しかも静かに）。
        try:
            root.after(interval_ms,
                       lambda: drain_ui_mailbox(root, interval_ms=interval_ms))
        except Exception:
            pass      # ルートが無い＝終了処理中。配る相手も、次回も無い


class ProgressPump:
    """ワーカースレッドからの投入をメインスレッドで消費するポンプ。

    `push` だけがワーカースレッドから呼べる（Tk に一切触れない）。それ以外は
    すべてメインスレッドから呼ぶこと。
    """

    def __init__(
        self,
        scheduler:   Any,
        handler:     Callable[[Any], None],
        *,
        interval_ms: int  = 50,
        latest_only: bool = False,
    ) -> None:
        """
        引数:
          scheduler   : `.after(ms, cb)` を持つ生存中の tk ウィジェット。
          handler     : 取り出した 1 件を処理する関数（メインスレッドで呼ばれる）。
          interval_ms : ポーリング間隔。
          latest_only : True なら溜まった分を捨てて最新 1 件だけ handler へ渡す。
        """
        self._scheduler   = scheduler
        self._handler     = handler
        self._interval_ms = interval_ms
        self._latest_only = latest_only
        self._queue: queue.Queue = queue.Queue()
        self._active = False

    # ------------------------------------------------------------
    # ワーカースレッドから呼んでよい唯一の API
    # ------------------------------------------------------------
    def push(self, item: Any) -> None:
        """進捗・イベントを投入する（スレッドセーフ・Tk に触れない）。

        停止中に押されたものは次の start までキューに残るが、start は
        キューを空にしてから始めるので持ち越されない。
        """
        self._queue.put(item)

    # ------------------------------------------------------------
    # メインスレッドから呼ぶ API
    # ------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._active

    def start(self) -> None:
        """ポーリングを開始する（多重起動しない）。

        既に走っているなら何もしない。ここで二重にスケジュールすると、以後
        `stop` しても片方が生き残る（ポーリング連鎖が 2 本になる）。
        """
        if self._active:
            return
        self.clear()
        self._active = True
        self._poll()

    def stop(self) -> None:
        """ポーリングを止め、積み残しを捨てる。

        ⚠️ キューを捨てるだけでは不十分。停止した時点で `after` 済みの
        ポーリングが 1 回残っており、それが積み残しを描画して完了表示を
        上書きする（2.4b2 実機βで発生）。`_poll` の早期 return と
        合わせた**二重の防御**でこれを止める。
        """
        self._active = False
        self.clear()

    def clear(self) -> None:
        """停止せずに積み残しだけ捨てる（フェーズ切替時）。

        フェーズが変わると前フェーズの進捗値は新しい目盛りの上で無意味になる。
        """
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _poll(self) -> None:
        """キューを消費して handler へ渡し、自分を再スケジュールする。"""
        if not self._active:
            return

        if self._latest_only:
            latest = None
            while True:
                try:
                    latest = self._queue.get_nowait()
                except queue.Empty:
                    break
            if latest is not None:
                self._handler(latest)
        else:
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                self._handler(item)
                # handler が stop を呼んだら（完了・エラー）そこで消費を止める。
                # 続けると停止後のイベントが完了表示を上書きしうる。
                if not self._active:
                    return

        # ウィジェットが破棄された後の after は `invalid command name` になる。
        # 閉じる側で stop するのが正道だが、取りこぼしても静かに止まるようにして
        # おく（破棄経路が増えても例外を撒かない＝close_map_safely と同じ方針）。
        try:
            self._scheduler.after(self._interval_ms, self._poll)
        except Exception:
            self._active = False
