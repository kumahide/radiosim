"""
tests/test_verdict.py
=====================
判定の口（`core/verdict.py`・4.0 ステージ6）の試験。ネットワーク不要＝ゴールデンの標高から作る。
"""
import copy
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import calc_profile, dem, models, verdict
from core import simulation as sim

_DATA = os.path.join(os.path.dirname(__file__), "data", "golden_links.json")
with open(_DATA, encoding="utf-8") as _f:
    LINKS = {link["id"]: link for link in json.load(_f)["links"]}


def _params(inp: dict, profile: str = calc_profile.V2) -> sim.SimParams:
    p = sim.SimParams({
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
    return sim.with_calc_profile(p, profile)


def _run(link_id: str, profile: str = calc_profile.V2, raw=None, status=None):
    link = LINKS[link_id]
    p = _params(link["input"], profile)
    t = models.calculate_terrain_profile(
        raw_elevs=np.array(link["raw_elevs"] if raw is None else raw, dtype=float),
        lat_tx=p.lat_tx, lon_tx=p.lon_tx, lat_rx=p.lat_rx, lon_rx=p.lon_rx,
        frac_axis=p.sample_fracs)
    t.dem_status = status
    return sim.run_calculation(t, p.h_tx, p.h_rx, p)


class TestAggregate:
    def test_ng_beats_review_beats_ok(self):
        assert verdict.aggregate(["OK", "REVIEW", "OK"]) == "REVIEW"
        assert verdict.aggregate(["OK", "REVIEW", "NG"]) == "NG"
        assert verdict.aggregate(["OK", "OK"]) == "OK"

    def test_error_and_empty_are_error(self):
        assert verdict.aggregate(["NG", "ERROR"]) == "ERROR"
        assert verdict.aggregate([]) == "ERROR"

    def test_counts_name_every_word(self):
        assert verdict.counts(["OK", "NG", "NG"]) == {"OK": 1, "REVIEW": 0, "NG": 2, "ERROR": 0}


@pytest.mark.parametrize("link_id", list(LINKS))
def test_status_is_the_verdict_word_and_brackets_the_base(link_id):
    r = _run(link_id)
    v = r.verdict
    assert r.status == v.word and v.base_margin == r.actual_margin
    assert v.worst_margin <= r.actual_margin <= v.best_margin
    flips = (v.worst_margin < 0) if r.actual_margin >= 0 else (v.best_margin >= 0)
    assert (v.word == "REVIEW") == flips, "REVIEW は両向き（基準 OK で最悪が NG・基準 NG で最良が OK）"
    assert bool(v.flipped_by) == (v.word == "REVIEW")


@pytest.mark.parametrize("link_id", [i for i, link in LINKS.items()
                                     if not link["input"].get("resolution")])
def test_v1_keeps_the_39_verdict(link_id):
    """v1 は 3.9 のまま＝基準の余裕度だけで OK／NG（判断点 ⑨）。

    段階で解いた回線は v1 と v2 で標本の置き方が違う（B-339）＝点数固定の回線だけで見る。
    """
    r = _run(link_id, calc_profile.V1)
    assert r.status == ("OK" if r.actual_margin >= 0 else "NG")
    assert r.verdict.worst_margin is None


def test_fetch_failure_is_error_in_v2_only():
    raw = np.array(LINKS["nara_ikoma"]["raw_elevs"], dtype=float)
    raw[5] = np.nan
    assert _run("nara_ikoma", raw=raw).status == "ERROR"
    assert _run("nara_ikoma", calc_profile.V1, raw=raw).status in ("OK", "NG")


def test_invalid_pixels_are_filled_by_interpolation_and_counted():
    raw = np.array([10.0, 0.0, 0.0, 40.0, 0.0])
    st = np.array([dem.ElevStatus.VALUE, dem.ElevStatus.INVALID_PIXEL, dem.ElevStatus.INVALID_PIXEL,
                   dem.ElevStatus.VALUE, dem.ElevStatus.NO_TILE], dtype=np.int8)
    np.testing.assert_allclose(verdict.fill_invalid_pixels(raw, st), [10.0, 20.0, 30.0, 40.0, 0.0])
    assert verdict.fill_invalid_pixels(raw, None) is None
    link = LINKS["morioka_iwate"]
    n = len(link["raw_elevs"])
    status = np.zeros(n, dtype=np.int8)
    status[3:6] = int(dem.ElevStatus.INVALID_PIXEL)
    status[-1] = int(dem.ElevStatus.NO_TILE)
    v = _run("morioka_iwate", status=status).verdict
    assert (v.dem_invalid_pixel, v.dem_no_tile, v.dem_fetch_failed) == (3, 1, 0)


def test_invalid_pixel_that_flips_is_review():
    """埋め直しで裏返る回線は REVIEW（I-247 の (b)）＝深く埋めた谷を「無効画素」と名乗らせる。"""
    link = LINKS["morioka_iwate"]
    raw = np.array(link["raw_elevs"], dtype=float)
    n = len(raw)
    mid = slice(n // 2 - 3, n // 2 + 3)
    blocked = raw.copy()
    blocked[mid] = raw.max() + 2000.0          # 無効画素が山として読まれた
    status = np.zeros(n, dtype=np.int8)
    status[mid] = int(dem.ElevStatus.INVALID_PIXEL)
    r = _run("morioka_iwate", raw=blocked, status=status)
    assert r.actual_margin < 0
    assert r.status == "REVIEW" and "dem_invalid_pixel" in r.verdict.flipped_by


def test_golden_review_count_matches_the_stage6_measurement():
    """着手の測り（I-235 の「対応」＝31 本で REVIEW 13 本）と同じ数。"""
    words = [_run(i).status for i in LINKS]
    assert verdict.counts(words)["REVIEW"] == 13


@pytest.mark.parametrize("word", verdict.WORDS)
def test_every_layer_maps_each_word_to_the_same_colour_key(word):
    """帳票の CSS クラス・旧画面の配色キー・表のセルの字が、4 語のどれでも同じ鍵に落ちる。

    REVIEW を NG や ERR へ潰す対応表が 1 つでも残ると、同じ回線が面ごとに違う色になる。
    """
    from report import map_graphics, report_common
    from views import theme
    key = report_common.verdict_class(word)
    assert key == theme.verdict_key(word) == theme.verdict_key(theme.verdict_label(word))
    assert word in map_graphics.STATUS_HEX and word in report_common.VERDICT_ROW_BG
    assert len(theme.verdict_label(word)) <= 3, "バッチ表の判定列（幅 4）に収まらない"
    assert len(set(report_common.VERDICT_CLASS.values())) == len(verdict.WORDS)


def test_scenario_and_params_are_not_mutated():
    link = LINKS["nara_ikoma"]
    p = _params(link["input"])
    before = copy.copy(p.__dict__)
    t = models.calculate_terrain_profile(
        raw_elevs=np.array(link["raw_elevs"], dtype=float),
        lat_tx=p.lat_tx, lon_tx=p.lon_tx, lat_rx=p.lat_rx, lon_rx=p.lon_rx, frac_axis=p.sample_fracs)
    sim.run_calculation(t, p.h_tx, p.h_rx, p)
    assert p.__dict__ == before
