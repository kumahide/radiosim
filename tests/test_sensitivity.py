"""
tests/test_sensitivity.py
==========================
`core/sensitivity.py`（3.4 段4・感度計算エンジン）の回帰テスト。

ネットワーク不要＝`tests/data/golden_links.json` から再計算するだけ。
**モデルは1行も変えていない**ことも、既存回帰（test_golden_links.py）が
別途裏取りする＝ここでは「摂動条件の再計算が正しい向き・正しい形で幅を
出すか」だけを見る。
"""
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import models
from core import sensitivity as sv
from core import simulation as sim
from core import terrain_grid

_DATA = os.path.join(os.path.dirname(__file__), "data", "golden_links.json")
with open(_DATA, encoding="utf-8") as _f:
    CORPUS = json.load(_f)
LINKS = {link["id"]: link for link in CORPUS["links"]}


def _params(inp: dict) -> sim.SimParams:
    return sim.SimParams({
        "start": f"{inp['lat_tx']}, {inp['lon_tx']}",
        "end":   f"{inp['lat_rx']}, {inp['lon_rx']}",
        "h_tx": str(inp["h_tx"]), "h_rx": str(inp["h_rx"]),
        "freq": str(inp["freq_mhz"]), "p_tx": str(inp["p_tx"]),
        "gain_tx": str(inp["gain_tx"]), "gain_rx": str(inp["gain_rx"]),
        "sens": str(inp["sens"]), "veg_h": str(inp["veg_h"]),
        "k_factor": str(inp["k_factor"]),
        **({"resolution": inp["resolution"]} if inp.get("resolution")
           else {"samples": str(inp["samples"])}),
        "env_type": inp["env_type"], "diff_method": inp["diff_method"],
        "rain_rate": str(inp["rain_rate"]),
    })


def _terrain(link: dict, params: sim.SimParams) -> models.TerrainProfile:
    return models.calculate_terrain_profile(
        raw_elevs=np.array(link["raw_elevs"], dtype=float),
        lat_tx=params.lat_tx, lon_tx=params.lon_tx,
        lat_rx=params.lat_rx, lon_rx=params.lon_rx,
        frac_axis=params.sample_fracs,
    )


def _compute(link_id: str, **fetch_kw):
    link = LINKS[link_id]
    params = _params(link["input"])
    terrain = _terrain(link, params)
    return sv.compute_link_sensitivity(
        terrain, params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx,
        params.h_tx, params.h_rx, params.freq_mhz, params.veg_h, params.k_factor,
        params.diff_method, params.env_type, params.rain_rate,
        params.p_tx, params.gain_tx, params.gain_rx, params.sens,
        **fetch_kw,
    )


@pytest.mark.parametrize("link_id", list(LINKS.keys()))
def test_axes_bracket_baseline(link_id):
    """全軸で low <= baseline <= high（向きの取り違えがないこと）。"""
    result = _compute(link_id)
    for key, axis in result.axes.items():
        assert axis.low <= axis.baseline + 1e-9, (link_id, key, axis)
        assert axis.baseline - 1e-9 <= axis.high, (link_id, key, axis)
    if result.resolution is not None:
        r = result.resolution
        assert r.low <= r.baseline + 1e-9 <= r.high + 1e-9


def test_veg_axis_absent_when_veg_h_zero():
    result = _compute("tokyo_urban_2400")
    link = LINKS["tokyo_urban_2400"]
    assert link["input"]["veg_h"] == 0.0
    assert "veg_h" not in result.axes


def test_veg_axis_present_when_veg_h_nonzero():
    result = _compute("hiroshima_kure_ridge")
    assert "veg_h" in result.axes


def test_resolution_axis_absent_without_callback():
    result = _compute("tokyo_urban_2400")
    assert result.resolution is None


def test_resolution_axis_present_with_stub_callback():
    link = LINKS["tokyo_urban_2400"]
    raw = np.array(link["raw_elevs"], dtype=float)

    def _stub(level: str) -> np.ndarray:
        assert level in terrain_grid.RESOLUTION_KEYS
        return raw  # スタブ＝同じ標高を返すだけ（呼び出し経路の確認が目的）

    result = _compute("tokyo_urban_2400", fetch_alt_resolution=_stub)
    assert result.resolution is not None


def test_diff_veg_compose_matches_b132_max_rule():
    """B-132＝『和』でなく『大きいほう』にすると margin が改善する向きに出る。

    hiroshima_kure_ridge は diff_loss=51.97dB・veg_loss=45.0dB（両方 >0）なので
    `max(diff,veg)` は `diff+veg` より確実に小さい＝margin は改善するはず。
    """
    result = _compute("hiroshima_kure_ridge")
    axis = result.axes["diff_veg_compose"]
    assert axis.high > axis.baseline
    assert axis.high == pytest.approx(axis.baseline + 45.0, abs=1e-6)


def test_dem_elev_perturbation_uses_documented_magnitude():
    assert sv.DEM_PERTURB_M == 3.0


@pytest.mark.parametrize("link_id", list(LINKS.keys()))
def test_dem_elev_axis_is_not_emitted(link_id):
    """DEM 標高の軸は出さない（B-232）。

    出すと帳票が「変化なし：DEM 標高 ±3 m」と**事実に反する開示**を書く
    （下の `test_uniform_dem_shift_is_inert` が、その幅が原理的にゼロである
    ことを示す）。3.5 で摂動の与え方を設計し直したら、このテストを外して
    「幅が出ること」を要求する側へ書き換える。
    """
    assert "dem_elev" not in _compute(link_id).axes


@pytest.mark.parametrize("link_id", list(LINKS.keys()))
def test_uniform_dem_shift_is_inert(link_id):
    """標高を経路全体へ一律に動かしても余裕度は 1 mdB も動かない（B-232 の根拠）。

    h_tx/h_rx は地表からの相対高なので、地形と両端が同じ量だけ動けば見通し線と
    地形の相対関係は変わらない＝**「DEM の系統誤差」を一律シフトで表すと、
    検出できない摂動を選んでいることになる**。この性質が崩れた日（＝摂動の
    与え方を変えた日）に、上の「軸を出さない」判断ごと見直させるための固定。
    """
    link = LINKS[link_id]
    params = _params(link["input"])
    terrain = _terrain(link, params)

    def _margin(t: models.TerrainProfile) -> float:
        prop = models.calculate_propagation(
            t, params.h_tx, params.h_rx, params.freq_mhz, params.veg_h,
            params.k_factor, params.diff_method, params.env_type, params.rain_rate,
        )
        return models.calculate_link_budget(
            prop, params.freq_mhz, params.p_tx, params.gain_tx, params.gain_rx,
            params.sens,
        ).actual_margin

    baseline = _margin(terrain)
    for delta in (-sv.DEM_PERTURB_M, sv.DEM_PERTURB_M):
        shifted = _margin(sv.shift_dem(terrain, delta))
        assert shifted == pytest.approx(baseline, abs=1e-6), (link_id, delta)


def test_multihop_argmin_can_flip():
    """全ホップへ同じ摂動を適用すると、律速ホップ（argmin）が入れ替わり得る。"""
    def _hop(link_id: str) -> sv.HopInputs:
        link = LINKS[link_id]
        params = _params(link["input"])
        terrain = _terrain(link, params)
        return sv.HopInputs(
            terrain=terrain, h_tx=params.h_tx, h_rx=params.h_rx,
            freq_mhz=params.freq_mhz, veg_h=params.veg_h, initial_k=params.k_factor,
            diff_method=params.diff_method, env_type=params.env_type,
            rain_rate=params.rain_rate, p_tx=params.p_tx, gain_tx=params.gain_tx,
            gain_rx=params.gain_rx, sens=params.sens,
        )

    hops = [_hop("tokyo_urban_2400"), _hop("hiroshima_kure_ridge")]

    def _identity(h: sv.HopInputs) -> sv.HopInputs:
        return h

    baseline_idx, _ = sv.compute_multihop_argmin(hops, _identity)
    assert baseline_idx == 1  # hiroshima_kure_ridge が律速（大きく NG）

    def _flip(h: sv.HopInputs) -> sv.HopInputs:
        # tokyo_urban_2400（余裕あり）側だけ極端に悪化させ、律速ホップを入れ替える。
        if h is hops[0]:
            import dataclasses
            return dataclasses.replace(h, sens=h.sens + 200.0)
        return h

    flipped_idx, _ = sv.compute_multihop_argmin(hops, _flip)
    assert flipped_idx == 0
