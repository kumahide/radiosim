"""
test_engine.py
==============
計算コアへの呼び口（`core/engine.py`・4.0 ステージ6）と、その下の同期の取得
（`simulation.fetch_elevations_sync`）。取得は `sim.fetch_elevations` を偽物に差し替える。
"""

import threading

import numpy as np
import pytest

from core import config
from core import engine
from core import simulation as sim

_FAIL_LAT = 35.25


def _fake_fetch(params, on_progress, on_complete, on_error, **_kw):
    on_progress(params.num)
    if params.lat_tx == _FAIL_LAT:
        on_error(RuntimeError("DEM fetch failed (fake)"))
    else:
        on_complete(np.full(params.num, 10.0))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)
    monkeypatch.setattr(sim, "_terrain_cache", {})


def _params(lat_tx=35.0):
    d = dict(config.DEFAULT_CONFIG)
    d["start"] = f"{lat_tx}, 135.0"
    d["end"] = "35.02, 135.02"
    return sim.SimParams(d)


class TestFetchSync:

    def test_returns_the_array_and_passes_progress(self):
        p = _params()
        seen: list[int] = []
        arr = sim.fetch_elevations_sync(p, seen.append)
        assert isinstance(arr, np.ndarray) and len(arr) == p.num
        assert seen == [p.num]

    def test_raises_the_error(self):
        with pytest.raises(RuntimeError, match="DEM fetch failed"):
            sim.fetch_elevations_sync(_params(_FAIL_LAT))

    def test_passes_only_the_given_arguments(self, monkeypatch):
        """偽物の取得が受けない引数（`cancel`・`on_status`）を勝手に渡さない。"""
        def _narrow(params, on_progress, on_complete, on_error):
            on_complete(np.zeros(params.num))
        monkeypatch.setattr(sim, "fetch_elevations_cached", _narrow)
        assert len(sim.fetch_elevations_sync(_params())) > 0


class TestFetchTerrain:

    def test_carries_the_dates_and_the_status_onto_the_terrain(self, monkeypatch):
        """取得日（B-213）と標本ごとの事情（I-247）を、標高と一緒に地形へ運ぶ。"""
        status = np.zeros(1, dtype=np.int8)

        def _cached(params, on_progress, on_complete, on_error, on_acquired=None,
                    on_status=None, cancel=None):
            on_status(np.zeros(params.num, dtype=np.int8))
            on_acquired(("2026-08-01", "2026-08-02"))
            on_complete(np.full(params.num, 10.0))
        monkeypatch.setattr(sim, "fetch_elevations_cached", _cached)
        p = _params()
        t = engine.fetch_terrain(p)
        assert len(t.raw_elevs) == p.num
        assert t.dem_acquired == ("2026-08-01", "2026-08-02")
        assert t.dem_status is not None and t.dem_status.dtype == status.dtype


class TestRunPaths:

    def test_yields_each_item_in_order(self):
        starts: list[tuple] = []
        out = list(engine.run_paths(
            [35.0, 35.1], _params, label=lambda v: f"p{v}",
            on_start=lambda i, n, name: starts.append((i, n, name))))
        assert [item for item, _ in out] == [35.0, 35.1]
        assert all(o.error is None and o.result is not None for _, o in out)
        assert starts == [(1, 2, "p35.0"), (2, 2, "p35.1")]

    def test_one_failure_is_folded_into_its_outcome(self):
        out = list(engine.run_paths([_FAIL_LAT, 35.0], _params))
        assert isinstance(out[0][1].error, RuntimeError) and out[0][1].result is None
        assert out[1][1].error is None

    def test_cancel_stops_before_the_next_item(self):
        ev = threading.Event()
        seen: list = []
        with pytest.raises(engine.Cancelled):
            for item, _ in engine.run_paths([35.0, 35.1, 35.2], _params, cancel=ev):
                seen.append(item)
                ev.set()
        assert seen == [35.0]

    def test_unreachable_dem_stops_everything(self, monkeypatch):
        def _unreachable(params, on_progress, on_complete, on_error, **_kw):
            on_error(sim.DemUnreachableError("unreachable"))
        monkeypatch.setattr(sim, "fetch_elevations", _unreachable)
        with pytest.raises(sim.DemUnreachableError):
            list(engine.run_paths([35.0, 35.1], _params))


class TestTerrainKey:
    """`terrain_key`＝地形に効く欄だけで変わる（新しい画面が取り直すかの判定・4.0 ステージ8b）。"""

    def test_same_path_same_key_and_heights_do_not_matter(self):
        a, b = _params(), _params()
        b.h_tx, b.freq_mhz = 99.0, 920.0
        assert engine.terrain_key(a) == engine.terrain_key(b)

    def test_moving_an_end_changes_the_key(self):
        assert engine.terrain_key(_params(35.0)) != engine.terrain_key(_params(35.001))
