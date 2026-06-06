"""
tests/conftest.py
=================
test_models.py / test_simulation.py で共有するフィクスチャ。
"""

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import models


@pytest.fixture
def default_params_dict():
    """SimParams / validate_config に渡す標準パラメータ辞書。"""
    return {
        "start"      : "34.5429, 132.4118",
        "end"        : "34.5389, 132.4050",
        "h_tx"       : "30.0",
        "h_rx"       : "10.0",
        "freq"       : "2400.0",
        "p_tx"       : "20.0",
        "gain_tx"    : "3.0",
        "gain_rx"    : "3.0",
        "sens"       : "-85.0",
        "veg_h"      : "10.0",
        "k_factor"   : "10.0",
        "samples"    : "50",
        "diff_method": "deygout",
        "env_type"   : "los",
        "rain_rate"  : "0.0",
    }


@pytest.fixture
def flat_terrain():
    """平坦地形（標高 0m 均一、100 サンプル）。"""
    raw = np.zeros(100)
    return models.calculate_terrain_profile(raw, 34.5429, 132.4118, 34.5389, 132.4050)
