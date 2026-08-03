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

import numpy as np

import batch
import config
import multihop
import report_summary
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


_FAKE_GROUND_M = 100.0


def _isolate(tmp_path, monkeypatch):
    """**実ネットワーク・実 DEM キャッシュ・本番の出力先から切り離す。**

    🔴 ここが無いまま書いて実害を出した（2026-08-04・独立レビュー Codex 4 巡目）＝
    ①出力先の差し替えを **`config.results_dir` という存在しない名前**へ当てており
    （`monkeypatch.setattr(..., raising=False)` が**タイプミスを黙って受け入れた**）、
    **本番の `results/batch_*` が実際に作られた**。②標高取得を塞いでいなかったので、
    **手元のディスクキャッシュが温まっていたから通っていただけ**——クリーンな環境
    では conftest のネットワーク遮断に先に当たり、仕込んだ失敗へ到達しない。

    ⚠️ **`raising=False` は既定にしない**＝差し替え先が実在することまで含めて
    テストの前提。存在しない属性へ当てても静かに成功するので、**間違いが緑で通る**。
    """
    def _fake_fetch(params, on_progress, on_complete, on_error):
        on_complete(np.full(params.num, _FAKE_GROUND_M))

    monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)          # 実在必須
    monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path))          # 実在必須
    # サマリ地図は淡色タイルを取りに行く唯一の経路（塞がないと実ネットワーク）。
    monkeypatch.setattr(report_summary, "render_summary_map_b64",
                        lambda results: None)


def _run_batch(tmp_path, params, monkeypatch):
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
    _isolate(tmp_path, monkeypatch)

    def _raise(*_a, **_kw):
        raise _Boom("仕組まれた失敗")
    monkeypatch.setattr(config, "new_run_dir", _raise)   # raising=True＝実在を要求

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


def test_per_path_failure_logs_traceback(tmp_path, default_params_dict,
                                         monkeypatch, caplog):
    """**経路 1 本の失敗**も traceback つきで残ること。

    🔴 背景（2026-08-04・独立レビュー Codex 3 巡目）: ここが無かったせいで、
    **`_process_one` の行を `logger.error` に戻してもテストが緑のまま**だった
    （モジュール内に `logger.exception` が 1 個でもあれば通る書き方をしていた）。
    ゲートの壊れ方③「**間違ったものを要求している**」の実例——落ちることも
    誤検知しないことも確かめたのに、**要求の粒度が粗くて素通りしていた。**

    ⚠️ **最上位の except では代用できない**＝経路 1 本の失敗はここで
    `PathResult(ok=False)` に畳まれ、**バッチは完走する**（それが仕様）。
    計算・保存・レポート描画の失敗は全部この経路へ入る。
    """
    import report_path

    _isolate(tmp_path, monkeypatch)          # 実ネットワーク・本番の出力先を断つ

    def _raise(*_a, **_kw):
        raise _Boom("描画で仕組んだ失敗")
    # 標高取得はフェイクで通し、**描画段だけ**を倒す（そこまでは正常に進む必要がある）。
    monkeypatch.setattr(report_path, "save_path_visuals", _raise, raising=True)

    ev: dict = {}
    done = threading.Event()

    def on_batch_complete(_dir, results):
        ev["results"] = results
        done.set()

    with caplog.at_level("ERROR"):
        batch.run_batch(
            [batch.PathRow(path_id="path01", lat_tx=34.5, lon_tx=132.4,
                           lat_rx=34.6, lon_rx=132.5, h_tx=30.0, h_rx=10.0)],
            _params(default_params_dict),
            on_path_start     = lambda *a: None,
            on_path_progress  = lambda *a: None,
            on_path_complete  = lambda *a: None,
            on_batch_complete = on_batch_complete,
            on_error          = lambda ex: (ev.setdefault("error", ex), done.set()),
        )
        assert done.wait(timeout=60), "バッチが時間内に終わらなかった"

    assert "results" in ev, f"バッチが完走していない（on_error={ev.get('error')!r}）"
    (pr,) = ev["results"]
    assert not pr.ok and isinstance(pr.error, _Boom), \
        "仕込んだ失敗が PathResult.error に入っていない"

    records = _records_with_traceback(caplog)
    assert records, (
        "経路単位の失敗が logger.exception で残っていない"
        "（logger.error だと投げ元が分からない）"
    )
    assert any(r.exc_info and r.exc_info[0] is _Boom for r in records), \
        "記録された traceback が、仕込んだ例外のものではない"


@pytest.mark.parametrize("module", [batch, scenario, multihop])
def test_runners_do_not_swallow_the_traceback(module):
    """3 ランナーが**捕捉した例外を `logger.error` で握らない**こと。

    ⚠️ **「`logger.exception` が 1 個あること」では縛れない**（3 巡目の指摘）＝
    最上位を直しただけで通ってしまい、**他の except が戻ったのを見逃す**。
    ⇒ **`logger.error(` が 1 つも無いこと**を要求する（このモジュール群では、
    捕捉した例外を残す口は全て `logger.exception` であるべき）。

    実行して確かめるのが本筋（上の 2 つ）だが、**条件探索と中継経路は実行の
    下ごしらえが重い**のでここはソースで縛る。両輪で見る。
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
