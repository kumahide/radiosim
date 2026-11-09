"""
tests/test_runner_logging.py
============================
**バックグラウンド実行の失敗が、投げ元まで辿れる形でログに残るか**を縛る。

背景（2026-08-03・独立レビュー Codex 由来）
--------------------------------------------
ランナーは、ワーカースレッドの最上位で例外を捕まえて `on_error` へ渡す。このとき
`logger.error("... %s", ex)` だと**例外の文字列しか残らない**——ダイアログにも
`str(ex)` しか出ないので、**どこで投げられたのかがログからもダイアログからも分からない**。

これは `2.6RC1` の診断を困難にしたのと同じクラスの欠陥（症状と原因の距離が最大化
される）。**コメントで「exception を使うこと」と書いても次の except で戻る**ので、
[[feedback-radiosim-rules]]「実行時制約はコメントでなくテストで表現する」に従って
ここで縛る。

公開側に残るランナーは条件探索（`core/scenario.py`）。バッチと中継経路のランナーは
帳票と一緒に Pro へ移した（Link 1.0 ステージ11b）＝同じ縛りと、実行して traceback を
確かめる試験は Pro 側の同名の試験にある。

何を見るか
----------
`logger.exception(...)` は `exc_info` を伴う LogRecord を作る。**その 1 点だけ**を
見る（メッセージ文言や呼び出し方には触れない＝文言を変えても落ちないようにする）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import scenario


@pytest.mark.parametrize("module", [scenario])
def test_runners_do_not_swallow_the_traceback(module):
    """ランナーが**捕捉した例外を `logger.error` で握らない**こと。

    ⚠️ **「`logger.exception` が 1 個あること」では縛れない**（3 巡目の指摘）＝
    最上位を直しただけで通ってしまい、**他の except が戻ったのを見逃す**。
    ⇒ **`logger.error(` が 1 つも無いこと**を要求する（このモジュール群では、
    捕捉した例外を残す口は全て `logger.exception` であるべき）。

    実行して確かめるのが本筋だが、**条件探索は実行の下ごしらえが重い**ので
    ここはソースで縛る。
    """
    src = open(module.__file__, encoding="utf-8").read()
    assert "logger.exception(" in src, (
        f"{os.path.basename(module.__file__)}: 失敗を logger.exception で残していない"
    )
    assert "logger.error(" not in src, (
        f"{os.path.basename(module.__file__)}: `logger.error(` が残っている。"
        "捕捉した例外は `logger.exception` で残すこと（traceback が消える）。"
        "例外に無関係な ERROR ログを足す必要が出たら、この規則ごと見直すこと。"
    )
