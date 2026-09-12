"""
tests/test_residuals.py
========================
`core/residuals.py`（3.4 段5・実測突合せの残差計算）の単体テスト。
純粋計算のみなので凍結データは不要。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import residuals as res


def test_band_label_matches_veg_coeff_range():
    """帯域の境目は `models.VEG_COEFF_RANGE_GHZ` と一致する（単一ソース）。"""
    assert res.band_label(900) == "<1GHz"
    assert res.band_label(1000) == "1-6GHz"
    assert res.band_label(2400) == "1-6GHz"
    assert res.band_label(6000) == "1-6GHz"
    assert res.band_label(6001) == ">6GHz"


def test_distance_band_label_edges():
    assert res.distance_band_label(4.9) == "<5km"
    assert res.distance_band_label(5.0) == "5-20km"
    assert res.distance_band_label(20.0) == "5-20km"
    assert res.distance_band_label(20.1) == ">20km"


def test_compute_residual_db_adds_feeder_loss_back_to_measured():
    """給電線損失は測定値へ加算してアンテナ端へ引き戻す。"""
    # predicted=-70, measured=-75 で feeder_loss=0 なら残差は predicted-measured=5
    assert res.compute_residual_db(-70.0, -75.0, 0.0) == pytest.approx(5.0)
    # feeder_loss=3 なら測定値は -72 相当に引き戻り、残差は 2
    assert res.compute_residual_db(-70.0, -75.0, 3.0) == pytest.approx(2.0)
    # feeder_loss 省略(None) は 0 と同じ扱い
    assert res.compute_residual_db(-70.0, -75.0, None) == pytest.approx(5.0)


def test_build_sample_normalizes_empty_env_class():
    s = res.build_sample(
        path_id="p1", predicted_dbm=-70.0, measured_dbm=-75.0, feeder_loss_db=None,
        env_class="", freq_mhz=2400, distance_km=10.0, meas_method="spot",
    )
    assert s.env_class == res.UNSPECIFIED_LABEL
    assert s.is_spot is True
    assert s.band == "1-6GHz"
    assert s.distance_band == "5-20km"
    assert s.residual_db == pytest.approx(5.0)


def test_build_sample_spot_label_case_insensitive_and_japanese():
    s1 = res.build_sample(
        path_id="p1", predicted_dbm=-70.0, measured_dbm=-75.0, feeder_loss_db=None,
        env_class="urban", freq_mhz=2400, distance_km=10.0, meas_method="Spot ",
    )
    s2 = res.build_sample(
        path_id="p2", predicted_dbm=-70.0, measured_dbm=-75.0, feeder_loss_db=None,
        env_class="urban", freq_mhz=2400, distance_km=10.0, meas_method="スポット",
    )
    s3 = res.build_sample(
        path_id="p3", predicted_dbm=-70.0, measured_dbm=-75.0, feeder_loss_db=None,
        env_class="urban", freq_mhz=2400, distance_km=10.0, meas_method="空間平均",
    )
    assert s1.is_spot is True
    assert s2.is_spot is True
    assert s3.is_spot is False


def test_compute_layered_stats_groups_and_counts():
    samples = [
        res.build_sample(
            path_id=f"p{i}", predicted_dbm=-70.0, measured_dbm=m, feeder_loss_db=None,
            env_class="urban", freq_mhz=2400, distance_km=10.0, meas_method="",
        )
        for i, m in enumerate([-75.0, -73.0, -77.0])
    ]
    stats = res.compute_layered_stats(samples)
    assert len(stats) == 1
    layer = stats[0]
    assert layer.n == 3
    assert layer.env_class == "urban"
    assert layer.band == "1-6GHz"
    assert layer.distance_band == "5-20km"
    # residuals = [5.0, 3.0, 7.0] -> median 5.0
    assert layer.median_db == pytest.approx(5.0)
    assert layer.iqr_db > 0.0


def test_compute_layered_stats_single_sample_has_zero_iqr():
    s = res.build_sample(
        path_id="p1", predicted_dbm=-70.0, measured_dbm=-75.0, feeder_loss_db=None,
        env_class="rural", freq_mhz=433, distance_km=2.0, meas_method="",
    )
    stats = res.compute_layered_stats([s])
    assert len(stats) == 1
    assert stats[0].n == 1
    assert stats[0].iqr_db == 0.0


def test_compute_layered_stats_exclude_spot():
    spot = res.build_sample(
        path_id="p1", predicted_dbm=-70.0, measured_dbm=-75.0, feeder_loss_db=None,
        env_class="urban", freq_mhz=2400, distance_km=10.0, meas_method="spot",
    )
    spatial = res.build_sample(
        path_id="p2", predicted_dbm=-70.0, measured_dbm=-73.0, feeder_loss_db=None,
        env_class="urban", freq_mhz=2400, distance_km=10.0, meas_method="空間平均",
    )
    stats = res.compute_layered_stats([spot, spatial], exclude_spot=True)
    assert len(stats) == 1
    assert stats[0].n == 1


def test_compute_layered_stats_empty_input():
    assert res.compute_layered_stats([]) == []


def test_compute_layered_stats_sorted_by_count_desc():
    many = [
        res.build_sample(
            path_id=f"a{i}", predicted_dbm=-70.0, measured_dbm=-75.0, feeder_loss_db=None,
            env_class="urban", freq_mhz=2400, distance_km=10.0, meas_method="",
        )
        for i in range(3)
    ]
    few = [
        res.build_sample(
            path_id="b1", predicted_dbm=-70.0, measured_dbm=-75.0, feeder_loss_db=None,
            env_class="rural", freq_mhz=433, distance_km=2.0, meas_method="",
        )
    ]
    stats = res.compute_layered_stats(many + few)
    assert stats[0].n == 3
    assert stats[1].n == 1
