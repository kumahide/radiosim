"""
tests/test_runner_logging.py
============================
**バックグラウンド実行の失敗が、投げ元まで辿れる形でログに残るか**を縛る。

背景（2026-08-03・独立レビュー Codex 由来）
--------------------------------------------
バッチ・条件探索・中継経路の 3 つのランナーは、ワーカースレッドの最上位で例外を
捕まえて `on_error` へ渡す。このとき `logger.error("... %s", ex)` だと**例外の
文字列しか残らない**——ダイアログにも `str(ex)` しか出ないので、**どこで投げられた
のかがログからもダイアログからも分からない**。

⚠️ **`views/errors.py` の未捕捉ハンドラでは救えない**＝ここは*捕捉済み*なので、
Tk の `report_callback_exception` には届かない。3 つのランナーは自分で残すしかない。

これは `2.6RC1` の診断を困難にしたのと同じクラスの欠陥（症状と原因の距離が最大化
される）。**コメントで「exception を使うこと」と書いても次の except で戻る**ので、
[[feedback-radiosim]]「実行時制約はコメントでなくテストで表現する」に従って
ここで縛る。

何を見るか
----------
`logger.exception(...)` は `exc_info` を伴う LogRecord を作る。**その 1 点だけ**を
見る（メッセージ文言や呼び出し方には触れない＝文言を変えても落ちないようにする）。
"""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import batch
import config
import multihop
import scenario
import simulation as sim


class _Boom(RuntimeError):
    """このテスト専用の、確実に識別できる失敗。"""


def _params(default_params_dict) -> sim.SimParams:
    return sim.SimParams(default_params_dict)


def _wait_for_error(start, timeout=30.0):
    """ランナーを起動し、on_error に渡った例外を返す（届かなければ fail）。"""
    done = threading.Event()
    box: list[Exception] = []

    def on_error(ex):
        box.append(ex)
        done.set()

    start(on_error)
    assert done.wait(timeout=timeout), "ランナーが時間内に終わらなかった"
    assert box, "on_error が呼ばれていない"
    return box[0]


def _records_with_traceback(caplog):
    """`exc_info` を持つ ERROR レコード（＝logger.exception で出たもの）。"""
    return [r for r in caplog.records
            if r.levelname == "ERROR" and r.exc_info is not None]


@pytest.fixture
def _blow_up(monkeypatch):
    """3 ランナーが共通で通る地形取得を、必ず失敗する形に差し替える。"""
    def _raise(*_a, **_kw):
        raise _Boom("仕組まれた失敗")
    monkeypatch.setattr(sim, "run_simulation", _raise, raising=False)
    return _raise


def _run_batch(tmp_path, params, monkeypatch):
    monkeypatch.setattr(config, "results_dir", lambda: str(tmp_path), raising=False)

    def start(on_error):
        batch.run_batch(
            [batch.PathRow(path_id="path01", lat_tx=34.5, lon_tx=132.4,
                           lat_rx=34.6, lon_rx=132.5, h_tx=30.0, h_rx=10.0)],
            params,
            on_path_start     = lambda *a: None,
            on_path_progress  = lambda *a: None,
            on_path_complete  = lambda *a: None,
            on_batch_complete = lambda *a: None,
            on_error          = on_error,
        )
    return start


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def test_batch_runner_logs_traceback(tmp_path, default_params_dict,
                                     monkeypatch, caplog):
    """バッチの失敗が traceback つきで残ること。

    ⚠️ **経路 1 本の失敗では届かない**＝`_process_one` の失敗はその経路の
    `PathResult(ok=False)` に畳まれ、バッチは完走する（それが仕様）。ここで見たいの
    は**バッチそのものが倒れる**経路なので、ループの手前（出力先の用意）で倒す。
    """
    def _raise(*_a, **_kw):
        raise _Boom("仕組まれた失敗")
    monkeypatch.setattr(config, "new_run_dir", _raise, raising=False)

    with caplog.at_level("ERROR"):
        ex = _wait_for_error(
            _run_batch(tmp_path, _params(default_params_dict), monkeypatch))

    assert isinstance(ex, _Boom), "仕組んだ失敗が on_error まで届いていない"
    records = _records_with_traceback(caplog)
    assert records, (
        "バッチの失敗が logger.exception で残っていない"
        "（logger.error だと traceback が付かず、投げ元が分からない）"
    )
    assert any("_Boom" in str(r.exc_info[0]) for r in records if r.exc_info), (
        "記録された traceback が、仕組んだ例外のものではない"
    )


@pytest.mark.parametrize("module", [batch, scenario, multihop])
def test_runners_do_not_swallow_the_traceback(module):
    """3 ランナーの最上位 except が `logger.exception` を使っていること。

    ⚠️ 実行して確かめるのが本筋（上のテスト）だが、**条件探索と中継経路は
    実行に実 DEM 相当の下ごしらえが要る**ので、ここはソースで縛る。
    片方だけだと「1 つだけ直して他が戻る」を許すため両輪で見る。
    """
    src = open(module.__file__, encoding="utf-8").read()
    assert "logger.exception(" in src, (
        f"{os.path.basename(module.__file__)}: ワーカー最上位の失敗を "
        "logger.exception で残していない（logger.error では traceback が消える）"
    )
