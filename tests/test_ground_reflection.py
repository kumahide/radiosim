"""
tests/test_ground_reflection.py
================================
`core/ground_reflection.py`（3.4 ステージ4・地面反射の振幅包絡線）の回帰テスト。

ネットワーク不要＝`tests/data/golden_links.json`（実 DEM 由来の凍結標高）から
再計算するだけ。**このモジュールは既存の計算経路を一切変えない**（受信電力へ
混ぜない）ことも、ここで裏取りする。
"""
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import ground_reflection as gr
from core import models
from core import simulation as sim

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


def test_far_from_both_ends_returns_envelope():
    """両端から十分離れた反射点を持つ回線では包絡線が返る（tokyo_urban_2400）。"""
    link = LINKS["tokyo_urban_2400"]
    params = _params(link["input"])
    terrain = _terrain(link, params)
    env = gr.compute_two_ray_envelope(terrain, params.h_tx, params.h_rx, params.freq_mhz)
    assert env is not None
    assert 0.0 <= env.frac <= 1.0
    assert 0.0 <= env.null_depth_db <= gr.NULL_DEPTH_CAP_DB
    assert env.constructive_gain_db == gr.CONSTRUCTIVE_GAIN_DB
    assert env.roughness in ("smooth", "rough")


def test_reflection_near_end_returns_none():
    """反射点が端に寄る短距離・非対称高の回線では `None`（10mDEMで分解不能・ロードマップ判断）。"""
    link = LINKS["hiroshima_short_grazing"]
    params = _params(link["input"])
    terrain = _terrain(link, params)
    env = gr.compute_two_ray_envelope(terrain, params.h_tx, params.h_rx, params.freq_mhz)
    assert env is None


def test_does_not_change_existing_pipeline():
    """このモジュールを import・呼び出しても既存の計算結果は1桁も動かない（回帰）。"""
    link = LINKS["tokyo_urban_2400"]
    params = _params(link["input"])
    terrain = _terrain(link, params)
    result = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
    gr.compute_two_ray_envelope(terrain, params.h_tx, params.h_rx, params.freq_mhz)
    result_after = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
    assert result.actual_margin == result_after.actual_margin
    assert result.total_loss == result_after.total_loss


@pytest.mark.parametrize("dist_m,h_near,h_far", [(1000.0, 2.5, 30.0), (1000.0, 2.5, 150.0)])
def test_specular_point_matches_experiment_formula(dist_m, h_near, h_far):
    """`experiments/phase0_two_ray.py` の式と同じ結果を返す（移植の裏取り）。"""
    import math
    d1, psi = gr.specular_point(dist_m, h_near, h_far)
    assert d1 == pytest.approx(dist_m * h_near / (h_near + h_far))
    assert psi == pytest.approx(math.atan((h_near + h_far) / dist_m))


def test_null_depth_db_zero_reflection_is_zero_db():
    assert gr.null_depth_db(0.0) == 0.0


def test_null_depth_db_full_reflection_is_infinite():
    assert gr.null_depth_db(1.0) == float("inf")


def test_detrended_rms_uses_real_distance_not_sample_index():
    """非等間隔標本の完全な直線斜面は RMS が 0 に近いこと（B-234）。

    `core/ground_reflection.py:89`（旧）が `np.arange(n)`（標本の連番）を
    回帰の x 軸にしており、実際の距離軸ではなかった。距離 `[0, 1, 10]`・
    標高 `2x+5`（完全な直線斜面）の非等間隔標本を渡すと、間隔の粗密が
    傾きの誤差として残差に漏れ、RMS が `3.771m` になっていた（Codex 実測）。
    """
    x = np.array([0.0, 1.0, 10.0])
    values = 2.0 * x + 5.0
    assert gr.detrended_rms(x, values) == pytest.approx(0.0, abs=1e-9)
