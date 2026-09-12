"""
tests/test_report_residuals.py
===============================
`report/residuals.py`（`PathResult` からの残差標本抽出）の単体テスト。
計算そのものの裏取りは `tests/test_residuals.py` が担うので、ここは
「どの行を取り込み・どの行を除外するか」だけを見る。
"""
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import models
from core import simulation as sim
from report import batch
from report import residuals as report_residuals


def _make_result(p_rx=-75.0):
    return models.LinkBudgetResult(
        eirp=23.0, fspl=100.0, diff_loss=0.0, veg_loss=0.0,
        env_loss=6.0, rain_loss=0.0, gas_loss=0.0,
        total_loss=106.0, p_rx=p_rx,
        actual_margin=2.0, status="OK",
        current_k=10.0, blocked_ratio=0.0, slant_dist_km=1.0,
        diff_method="single", env_type="los",
    )


def _row(**kwargs):
    defaults: dict[str, Any] = dict(
        path_id="p01", lat_tx=34.54, lon_tx=132.41, lat_rx=34.53, lon_rx=132.40,
        h_tx=30.0, h_rx=10.0,
    )
    defaults.update(kwargs)
    return batch.PathRow(**defaults)


def _params(default_params_dict):
    return sim.SimParams(default_params_dict)


def test_row_without_meas_dbm_is_excluded(default_params_dict):
    row = _row(meas_dbm=None)
    pr = batch.PathResult(row=row, result=_make_result(), params=_params(default_params_dict))
    assert report_residuals.samples_from_results([pr]) == []


def test_errored_row_is_excluded(default_params_dict):
    row = _row(meas_dbm=-80.0)
    pr = batch.PathResult(row=row, result=None, params=None,
                           error=RuntimeError("boom"))
    assert report_residuals.samples_from_results([pr]) == []


def test_row_with_meas_dbm_is_included(default_params_dict):
    row = _row(meas_dbm=-80.0, env_class="urban", meas_method="spot", feeder_loss_db=2.0)
    pr = batch.PathResult(row=row, result=_make_result(p_rx=-75.0),
                           params=_params(default_params_dict))
    samples = report_residuals.samples_from_results([pr])
    assert len(samples) == 1
    s = samples[0]
    assert s.path_id == "p01"
    assert s.env_class == "urban"
    assert s.is_spot is True
    # predicted=-75, measured=-80, feeder_loss=2 -> residual = -75 - (-80+2) = 3
    assert s.residual_db == 3.0
    # terrain が無い行は距離 0km として扱う（distance_band_label(0) == "<5km"）
    assert s.distance_band == "<5km"
