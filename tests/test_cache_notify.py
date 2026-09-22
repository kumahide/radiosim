"""
tests/test_cache_notify.py
==========================
**実行でキャッシュが増えたことが、開いている地図ウィンドウへ必ず届く**ことの回帰テスト
（B-265）。

🔑 **守っている不変条件は「フローの完了ハンドラを通ったら通知が 1 回出る」**であって、
「特定の工程（事前取得）を通ったら」ではない。B-265 はまさにそこがずれていた＝通知が
単一実行の*事前取得の直後*に置かれていたため、事前取得を飛ばす経路（外部 DEM ソース＝
B-235）とバッチ・条件探索・中継では一度も出ず、地図の「キャッシュ総量」が 0 枚のまま
だった（実機・2026-09-22）。

⚠️ **GUI は起こさない**＝ハンドラはただのメソッドなので、宿主の面だけをフェイクにして
`MethodType` で縛って呼ぶ。⚠️ **成功・失敗の両方を見る**＝途中で失敗しても、そこまでに
取れたタイルはキャッシュに残っているので通知は要る。
"""

from types import MethodType, SimpleNamespace

import pytest

from views.batch_run import _RunMixin
from views.launcher import SimLauncher
from views.multihop import MultiHopWindow
from views.scenario import ScenarioWindow


class _Widget:
    """`.config(...)` を受け流すだけのウィジェット代役。"""

    def config(self, **kwargs) -> None:
        pass


def _host(cls, **extra):
    """完了ハンドラが触る面だけを持つ宿主のフェイク。

    ⚠️ `_notify_cache_change` は**本物を縛って持たせる**（差し替えない）＝
    通知の有無だけを見たいのであって、ガード（`_cache_notify is None`）まで
    テスト用の別実装に置き換えると、守りたい経路がテストの中から消える。
    """
    notified: list[str] = []
    host = SimpleNamespace(
        _running=True,
        _pump=SimpleNamespace(stop=lambda: None, start=lambda: None),
        _run_btn=_Widget(),
        _prog_bar=_Widget(),
        _prog_label=_Widget(),
        _prog_count_label=_Widget(),
        _summary_label=_Widget(),
        _last_run=None,
        _last_dir="",
        _cache_notify=lambda: notified.append("cache"),
        **extra,
    )
    host._notify_cache_change = MethodType(cls._notify_cache_change, host)
    return host, notified


# ==============================================================
# 単一実行（ランチャー）— 報告された面
# ==============================================================
def _launcher_host():
    notified: list[str] = []
    host = SimpleNamespace(
        _progress_stop=lambda: None,
        _run_btn=_Widget(),
        _prog_bar=_Widget(),
        _prog_label=_Widget(),
        _graph_win=None,
        _coord_fmt_var=SimpleNamespace(get=lambda: "dd"),
        _current_meta=lambda: {"project_name": "", "memo": ""},
        _on_graph_closed=lambda: None,
        _alert=lambda *a, **k: None,
        root=SimpleNamespace(update_idletasks=lambda: None),
        _notify_map_cache_change=lambda: notified.append("cache"),
    )
    return host, notified


def test_single_run_tells_the_map_that_the_cache_grew(monkeypatch):
    """単一実行が終わったら通知が出る（**DEM ソースを問わない**のがこのテストの眼目）。

    以前の実装は事前取得の直後にしか通知を置いておらず、外部ソースでは
    `_start_simulation` へ直行するのでここを通らなかった。通知を完了ハンドラへ
    移したので、事前取得の有無と無関係に 1 回出る。
    """
    import views.graph

    monkeypatch.setattr(views.graph, "show_graph", lambda *a, **k: _Widget(),
                        raising=False)
    host, notified = _launcher_host()
    params = SimpleNamespace(num=10)
    MethodType(SimLauncher._on_fetch_complete, host)(params, [0.0, 1.0], None)
    assert notified == ["cache"], (
        "単一実行の完了で地図へキャッシュ変更を知らせていない（B-265 の再発）"
    )


def test_single_run_tells_the_map_even_when_it_failed():
    """失敗しても通知は出る＝落ちるまでに取れたタイルはキャッシュに残っている。"""
    host, notified = _launcher_host()
    MethodType(SimLauncher._on_fetch_error, host)(RuntimeError("boom"))
    assert notified == ["cache"]


# ==============================================================
# バッチ / 条件探索 / 中継 — 「一度も通知していなかった」3 フロー
# ==============================================================
def test_batch_run_tells_the_map_that_the_cache_grew(monkeypatch):
    import views.batch_run as batch_run

    monkeypatch.setattr(batch_run.dialogs, "choose", lambda *a, **k: None)
    host, notified = _host(_RunMixin)
    MethodType(_RunMixin._on_batch_complete, host)("out/batch_001", [])
    assert notified == ["cache"]


def test_batch_run_tells_the_map_even_when_it_failed(monkeypatch):
    import views.batch_run as batch_run

    monkeypatch.setattr(batch_run.dialogs, "alert", lambda *a, **k: None)
    host, notified = _host(_RunMixin)
    MethodType(_RunMixin._on_error, host)(RuntimeError("boom"))
    assert notified == ["cache"]


def test_scenario_run_tells_the_map_that_the_cache_grew(monkeypatch):
    import views.scenario as scenario

    monkeypatch.setattr(scenario.dialogs, "choose", lambda *a, **k: None)
    host, notified = _host(ScenarioWindow, _fill_results=lambda run: None)
    MethodType(ScenarioWindow._on_complete, host)(SimpleNamespace())
    assert notified == ["cache"]


def test_scenario_run_tells_the_map_even_when_it_failed(monkeypatch):
    import views.scenario as scenario

    monkeypatch.setattr(scenario.dialogs, "alert", lambda *a, **k: None)
    host, notified = _host(ScenarioWindow)
    MethodType(ScenarioWindow._on_error, host)(RuntimeError("boom"))
    assert notified == ["cache"]


def test_multihop_run_tells_the_map_that_the_cache_grew(monkeypatch):
    import views.multihop as multihop

    monkeypatch.setattr(multihop.dialogs, "choose", lambda *a, **k: None)
    monkeypatch.setattr(multihop.mh, "overall_display",
                        lambda run, digits=2: ("mh_margin", "0.0"))
    monkeypatch.setattr(multihop.mh, "overall_status", lambda run: "OK")
    host, notified = _host(MultiHopWindow)
    MethodType(MultiHopWindow._on_complete, host)(
        SimpleNamespace(worst=None, hops=[], save_dir="out/mh_001"))
    assert notified == ["cache"]


def test_multihop_run_tells_the_map_even_when_it_failed(monkeypatch):
    import views.multihop as multihop

    monkeypatch.setattr(multihop.dialogs, "alert", lambda *a, **k: None)
    host, notified = _host(MultiHopWindow)
    MethodType(MultiHopWindow._on_error, host)(RuntimeError("boom"))
    assert notified == ["cache"]


# ==============================================================
# 配線（コールバックを渡し忘れたら、上の 6 本は全部通ったまま無通知になる）
# ==============================================================
@pytest.mark.parametrize("window_cls", [ScenarioWindow, MultiHopWindow])
def test_child_windows_accept_the_cache_notify_port(window_cls):
    """3 つの子ウィンドウが同じ名前の口を持つ（⑧＝ウィンドウごとに違う名前にしない）。"""
    import inspect

    assert "cache_notify" in inspect.signature(window_cls.__init__).parameters


def test_batch_window_accepts_the_cache_notify_port():
    import inspect

    from views.batch_builder import BatchBuilderWindow

    assert "cache_notify" in inspect.signature(
        BatchBuilderWindow.__init__).parameters


def test_the_launcher_wires_every_child_window_to_the_notifier():
    """ランチャーが**3 つとも**繋いでいること。

    ⚠️ ここを静的に見る理由＝口があるだけでは通知は出ない。実際 B-265 は
    「通知の関数は在るのに、呼ばれる経路が 1 本しかなかった」欠陥だった。
    """
    import ast
    import os

    src = os.path.join(os.path.dirname(__file__), "..", "views",
                       "launcher_windows.py")
    with open(src, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    wired = {
        node.func.id: True
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and any(kw.arg == "cache_notify" for kw in node.keywords)
    }
    assert set(wired) == {"BatchBuilderWindow", "ScenarioWindow",
                          "MultiHopWindow"}, (
        f"cache_notify を渡していない子ウィンドウがある: {sorted(wired)}"
    )
