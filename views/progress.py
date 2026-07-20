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

import queue
from typing import Any, Callable


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
