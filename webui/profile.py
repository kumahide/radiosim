"""
webui/profile.py
================
新しい画面の**断面図の数**（Link 1.0 ステージ9b）。

帳票の断面図（`report/report_path.py` の `save_report_png`）と**同じ意味の線**を、画面へ送る形にする:

- 地表と植生の上端＝画素の弦の両端を畳んだ折れ線（B-160＝表示専用のコピーにだけ通す）
- 見通し線と第 1 フレネルゾーン（±F1 の帯）＝**標本の位置**で引く（B-150＝等間隔ではない）
- 空中線＝両端の足元から絶対高までの縦線

距離は m（帳票の軸 `graph_dist_axis` と同じ）。点数は `MAX_POINTS` 以下に間引く（両端は必ず残す）。
計算の値には触れない＝ここで作るのは絵の材料だけ。
"""

from __future__ import annotations

import math
from typing import Any

from core import models
from core import terrain_grid
from core import units

#: 1 本の折れ線の点数の上限（画面の幅より多くても見えない・見出しは毎回送る）。
MAX_POINTS = 1200


def thin(n: int, limit: int = MAX_POINTS) -> "list[int]":
    """0〜n-1 から `limit` 個以下の添字を等間隔に選ぶ（先頭と末尾は必ず入る）。"""
    if n <= limit:
        return list(range(n))
    stride = math.ceil((n - 1) / (limit - 1))
    idx = list(range(0, n - 1, stride))
    idx.append(n - 1)
    return idx


def _num(v: Any) -> "float | None":
    f = float(v)
    return round(f, 2) if math.isfinite(f) else None


def profile_fields(terrain: Any, params: Any) -> "dict[str, Any]":
    """見出しの `profile` に載せる断面（JSON にできる形）。

    - `ground_d`／`ground`＝地表の折れ線（m）。植生の上端は `ground + veg_h`
    - `d`／`los`／`f1`＝標本の位置・見通し線・第 1 フレネルの半径（帯は `los ± f1`）
    - `tx`／`rx`＝空中線の [距離, 足元の標高, 絶対高]
    """
    elevs = terrain.elevs_with_curve
    d_m = units.km_to_m(terrain.d_km_axis)
    groups = params.sample_pixel_groups
    ground_d = terrain_grid.collapse_pixel_groups(list(d_m), groups)
    ground = terrain_grid.collapse_pixel_groups(list(elevs), groups)
    tx_abs = float(elevs[0]) + float(params.h_tx)
    rx_abs = float(elevs[-1]) + float(params.h_rx)
    los = terrain.los_line(tx_abs, rx_abs)
    f1 = models.fresnel_zone_radii(terrain.d_km_axis, terrain.horiz_dist_km, params.freq_mhz)
    length = float(units.km_to_m(terrain.horiz_dist_km))

    gi = thin(len(ground))
    si = thin(len(d_m))
    return {
        "ground_d": [_num(ground_d[i]) for i in gi],
        "ground": [_num(ground[i]) for i in gi],
        "veg_h": _num(params.veg_h),
        "d": [_num(d_m[i]) for i in si],
        "los": [_num(los[i]) for i in si],
        "f1": [_num(f1[i]) for i in si],
        "tx": [0.0, _num(elevs[0]), _num(tx_abs)],
        "rx": [_num(length), _num(elevs[-1]), _num(rx_abs)],
    }
