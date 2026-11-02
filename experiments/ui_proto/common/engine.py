"""画面の道具の試作（4.0 ステージ2）の計算の口＝3 候補が同じものを呼ぶ。

計算は `core/` をそのまま呼ぶ。⛔ 判定をここで作らない（`LinkBudgetResult` を運ぶだけ）。
地形は `simulation.fetch_elevations_for_resolution` のキャッシュに任せる＝端点が
変わらない限り取り直さない（哲学④）。
"""
from __future__ import annotations

import math
import time

import numpy as np

from core import config, models, simulation

#: 試作の入力＝製品の既定値のうち、画面に出す欄だけ。
DEFAULT_INPUTS: dict[str, str] = {
    key: config.DEFAULT_CONFIG[key]
    for key in ("start", "end", "h_tx", "h_rx", "freq", "p_tx", "gain_tx",
                "gain_rx", "sens", "veg_h", "k_factor", "resolution",
                "env_type", "rain_rate", "diff_method", "dem_source")
}

#: 断面図へ渡す点の上限＝画面の幅より細かくても見えない。
_PROFILE_POINTS_MAX = 1200

_NUMERIC_FIELDS = ("h_tx", "h_rx", "freq", "p_tx", "gain_tx", "gain_rx",
                   "sens", "veg_h", "k_factor", "rain_rate")


def _clean(arr: np.ndarray, stride: int) -> list:
    return [None if math.isnan(v) else round(float(v), 3)
            for v in np.asarray(arr, dtype=float)[::stride]]


def _input_problem(cfg: dict[str, str]) -> str | None:
    """欄の問題を 1 文で返す＝判定を出さない理由になる（正常値の顔で返さない）。"""
    for key in _NUMERIC_FIELDS:
        try:
            value = float(cfg[key])
        except (KeyError, TypeError, ValueError):
            return f"{key} が数値ではありません"
        if not math.isfinite(value):
            return f"{key} が数値ではありません"
    for key in ("start", "end"):
        parts = str(cfg.get(key, "")).split(",")
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except (IndexError, ValueError):
            return f"{key} は「緯度, 経度」で入れてください"
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return f"{key} の緯度経度が範囲の外です"
    if str(cfg["start"]).replace(" ", "") == str(cfg["end"]).replace(" ", ""):
        return "送信点と受信点が同じ場所です"
    return None


def calculate(inputs: dict[str, str]) -> dict:
    """入力の集合 1 つから判定と断面図を作る（別スレッドから呼んでよい）。"""
    cfg = {**DEFAULT_INPUTS, **{k: str(v) for k, v in inputs.items()}}
    problem = _input_problem(cfg)
    if problem:
        return {"ok": False, "kind": "input", "message": problem}

    t0 = time.perf_counter()
    params = simulation.SimParams(cfg)
    try:
        raw = simulation.fetch_elevations_for_resolution(params, params.resolution)
    except simulation.DemUnreachableError as exc:
        return {"ok": False, "kind": "terrain", "message": str(exc)}
    t1 = time.perf_counter()

    terrain = models.calculate_terrain_profile(
        raw, params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx,
        frac_axis=params.sample_fracs)
    result = simulation.run_calculation(terrain, params.h_tx, params.h_rx, params)
    t2 = time.perf_counter()

    tx_abs = float(terrain.elevs_with_curve[0]) + params.h_tx
    rx_abs = float(terrain.elevs_with_curve[-1]) + params.h_rx
    los = terrain.los_line(tx_abs, rx_abs)
    fresnel = models.fresnel_zone_radii(
        terrain.d_km_axis, terrain.horiz_dist_km, params.freq_mhz)
    stride = max(1, math.ceil(terrain.num_samples / _PROFILE_POINTS_MAX))

    return {
        "ok": True,
        "status": result.status,
        "margin_db": round(result.actual_margin, 2),
        "p_rx_dbm": round(result.p_rx, 2),
        "fspl_db": round(result.fspl, 2),
        "diff_loss_db": round(result.diff_loss, 2),
        "veg_loss_db": round(result.veg_loss, 2),
        "total_loss_db": round(result.total_loss, 2),
        "blocked_pct": round(result.blocked_ratio, 1),
        "distance_km": round(terrain.horiz_dist_km, 3),
        "samples": terrain.num_samples,
        "fail_pct": round(terrain.fail_pct, 1),
        "terrain_ms": round((t1 - t0) * 1000, 1),
        "calc_ms": round((t2 - t1) * 1000, 1),
        "profile": {
            "d_km": _clean(terrain.d_km_axis, stride),
            "ground": _clean(terrain.elevs_with_curve, stride),
            "los": _clean(los, stride),
            "fresnel_lower": _clean(los - fresnel, stride),
        },
    }
