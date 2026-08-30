"""B-150 探針: 「1px の半分」で刻んだ標本が **2 次元の格子で画素を飛ばす**ことと、
その取りこぼしが答え（回折損）をどれだけ動かすかを測る。

何を測るか
----------
1. **取りこぼしの数**＝経路が実際に通る z15/z14 画素と、標本が落ちる画素を突き合わせる
   （Codex の指摘の再現。製品と同じ `dem._tile_coords` を通す）。
2. **刻みへの収束**＝一様な刻みを 1/2 ずつ細かくして回折損を並べる
   （[[feedback-refine-the-step]]＝段差が刻みに比例して縮むか）。
3. **通過画素そのもので刻んだ場合**＝標本を画素の弦の中点／両端に置く。
   ⚠️ **通過画素は等間隔に並ばない**ので `d_km_axis` を明示して組む
   （この探針を書いた時点の `calculate_terrain_profile` は `linspace` 固定だった。
   直したあとは製品側も位置を受け取る＝`frac_axis`）。
4. `--levels` で **段階ごとの回折損**（開示の「最大 +N%」の測り直し）。

なぜ「両端」を測るか
--------------------
DEM の標高は**画素の中で一定の階段状の場**なので、経路が通る各画素は
「幅を持った棚」である。Bullington の接線を決めるのは*棚の縁*で、中点ではない。
一様な刻みを細かくすると値が上がり続けるのは、細かくするほど**縁に近い標本**が
現れるからで、峰を取りこぼしているからではない（最高標高は全段階で同じ）。
⇒ **縁を明示的に置けば、刻みに依らない値が 1 回で出る**はず。これが処方の当否を
分ける実測。

⚠️ **ネットワークに触る**（DEM タイル）＝キャッシュ済みなら再取得しない。
経路は 403m の 1 本だけなのでタイルは数枚（④ 外部 API 配慮）。

実行: `& $env:RADIOSIM_PYTHON experiments/b150_supercover_probe.py [--levels]`
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import dem            # noqa: E402
from core import diffraction    # noqa: E402
from core import models         # noqa: E402
from core import terrain_grid   # noqa: E402

# ゴールデンコーパスの `hiroshima_short_grazing`（B-148 で足した「短くてかすめる」）
LAT_TX, LON_TX = 34.538484, 132.398151
LAT_RX, LON_RX = 34.542107, 132.398108
H_TX, H_RX = 30.0, 10.0
FREQ_MHZ, VEG_H = 2400.0, 10.0

# 取りこぼしの数だけは Codex の指摘と同じ経路で数える（実機確認 3.0RC1 の path03）
MISS_LAT_TX, MISS_LON_TX = 34.5037, 132.4071
MISS_LAT_RX, MISS_LON_RX = 34.501027579, 132.410070741

LAM = 299792458.0 / (FREQ_MHZ * 1e6)


# ============================================================
# 経路の上の位置（t = 0..1）
# ============================================================
def at(t: float, a=(LAT_TX, LON_TX), b=(LAT_RX, LON_RX)) -> tuple[float, float]:
    """製品と同じ `np.linspace`（緯度・経度の線形内挿）での経路上の点。"""
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def pixel_of(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """製品と同じ整数画素（グローバル画素座標）。"""
    xt, yt, px, py = dem._tile_coords(lat, lon, zoom)
    return (xt * 256 + px, yt * 256 + py)


def boundaries(zoom: int, a, b, dense: int = 200_000) -> list[float]:
    """経路が画素をまたぐ `t` を全部拾う（粗い走査 → 二分で詰める）。"""
    out: list[float] = []
    prev_t = 0.0
    prev_p = pixel_of(*at(0.0, a, b), zoom)
    for i in range(1, dense + 1):
        t = i / dense
        p = pixel_of(*at(t, a, b), zoom)
        if p != prev_p:
            lo, hi = prev_t, t
            for _ in range(50):
                mid = (lo + hi) / 2.0
                if pixel_of(*at(mid, a, b), zoom) == prev_p:
                    lo = mid
                else:
                    hi = mid
            out.append(hi)
            prev_p = p
        prev_t = t
    return out


# ============================================================
# ① 取りこぼしの数
# ============================================================
def count_missing() -> None:
    print("① 通過画素と標本の突き合わせ（実機確認 3.0RC1 の path03・403m）")
    dist_m = models.horizontal_distance_km(
        MISS_LAT_TX, MISS_LON_TX, MISS_LAT_RX, MISS_LON_RX) * 1000.0
    a, b = (MISS_LAT_TX, MISS_LON_TX), (MISS_LAT_RX, MISS_LON_RX)
    for level in ("high", "medium"):
        zoom = terrain_grid.RESOLUTION_ZOOM[level]
        n = terrain_grid.recommended_samples(dist_m, level)
        bnd = boundaries(zoom, a, b)
        traversed = {pixel_of(*at(t, a, b), zoom)
                     for t in _midpoints([0.0] + bnd + [1.0])}
        sampled = {pixel_of(*at(i / (n - 1), a, b), zoom) for i in range(n)}
        print(f"   {level:6s} z{zoom} 1px={terrain_grid.pixel_size_m(a[0], zoom):.2f}m "
              f"目標={terrain_grid.RESOLUTION_SPACING_M[level]:.2f}m N={n:4d} "
              f"通過={len(traversed):3d} 標本が入る={len(traversed & sampled):3d} "
              f"欠落={len(traversed - sampled):3d}")


def _midpoints(edges: list[float]) -> list[float]:
    return [(edges[i] + edges[i + 1]) / 2.0 for i in range(len(edges) - 1)]


# ============================================================
# ②③ 回折損
# ============================================================
def diff_loss(ts: list[float]) -> tuple[float, float, int]:
    """経路上の位置 `t` の並びから回折損 [dB] を出す（`(損失, 最高標高, 点数)`）。

    製品の `calculate_propagation` と同じ組み方だが、**`d_km_axis` を明示する**
    （等間隔でない並びを渡せるようにするため）。
    """
    dist_km = models.horizontal_distance_km(LAT_TX, LON_TX, LAT_RX, LON_RX)
    raw = np.array([dem.get_elevation(*at(t)) for t in ts], dtype=float)
    d_km = np.array(ts, dtype=float) * dist_km
    re_km = 6371.0 * models.EARTH_K_STANDARD
    elevs = raw + (d_km * (dist_km - d_km)) / (2 * re_km) * 1000.0
    tx_abs = float(elevs[0]) + H_TX
    rx_abs = float(elevs[-1]) + H_RX
    loss = diffraction._multi_obstacle_loss(
        elevs + VEG_H, d_km * 1000.0, tx_abs, rx_abs, LAM)
    return float(loss), float(np.max(raw)), len(ts)


def uniform_ts(spacing_m: float) -> list[float]:
    dist_m = models.horizontal_distance_km(
        LAT_TX, LON_TX, LAT_RX, LON_RX) * 1000.0
    n = max(2, round(dist_m / spacing_m) + 1)
    return [i / (n - 1) for i in range(n)]


def level_span() -> None:
    """④ **開示の数字の測り直し**＝段階を細かくすると回折損がどれだけ増えるか。

    帳票の刻印は「実効 20m → 1.65m で最大 +28.6%」と名乗ってきたが、**その 1.65m は
    もう存在しない**（B-150 で「高」は画素の縁で刻む）。⇒ コーパスの入力を 3 段階で
    回し直して、**「低」→「高」の最大増加率**を測り直す。

    ⚠️ **回折損 0 dB の回線は率にできない**ので除く（0 → 何 dB でも ∞%）。
    ⚠️ 段階を経由する＝**製品の既定の経路**をそのまま通す（固定 N では測らない）。
    """
    import json
    import os
    import threading

    from core import simulation as sim

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "tests", "data", "golden_links.json")
    links = json.load(open(path, encoding="utf-8"))["links"]

    seen, rows = set(), []
    for rec in links:
        i = rec["input"]
        key = (i["lat_tx"], i["lon_tx"], i["lat_rx"], i["lon_rx"],
               i["h_tx"], i["h_rx"], i["freq_mhz"], i["veg_h"], i["diff_method"])
        if key in seen:
            continue
        seen.add(key)
        vals = {}
        for level in ("low", "medium", "high"):
            p = sim.SimParams({
                "start": f"{i['lat_tx']}, {i['lon_tx']}",
                "end":   f"{i['lat_rx']}, {i['lon_rx']}",
                "h_tx": str(i["h_tx"]), "h_rx": str(i["h_rx"]),
                "freq": str(i["freq_mhz"]), "p_tx": str(i["p_tx"]),
                "gain_tx": str(i["gain_tx"]), "gain_rx": str(i["gain_rx"]),
                "sens": str(i["sens"]), "veg_h": str(i["veg_h"]),
                "k_factor": str(i["k_factor"]), "resolution": level,
                "env_type": i["env_type"], "diff_method": i["diff_method"],
                "rain_rate": str(i["rain_rate"]),
            })
            out: list = []
            done = threading.Event()
            sim.fetch_elevations(p, lambda n: None,
                                 lambda e: (out.append(e), done.set()),
                                 lambda ex: (print("ERR", ex), done.set()))
            done.wait()
            terr = models.calculate_terrain_profile(
                np.round(out[0], 2), p.lat_tx, p.lon_tx, p.lat_rx, p.lon_rx,
                frac_axis=p.sample_fracs)
            res = sim.run_calculation(terr, p.h_tx, p.h_rx, p)
            vals[level] = (res.diff_loss, p.num, res.status)
        rows.append((rec["id"], vals))

    print("\n④ 段階ごとの回折損（開示の数字の測り直し・コーパスの経路）")
    print(f"{'id':<26} {'低':>18} {'中':>18} {'高':>18}   低→高")
    worst = ("", 0.0)
    for name, v in rows:
        lo, hi = v["low"][0], v["high"][0]
        rate = (hi - lo) / lo * 100.0 if lo > 0.01 else float("nan")
        if rate == rate and rate > worst[1]:
            worst = (name, rate)
        cells = "".join(f"{v[k][0]:9.2f}dB N={v[k][1]:<6d}"
                        for k in ("low", "medium", "high"))
        print(f"{name:<26} {cells}  {rate:+7.1f}%"
              if rate == rate else f"{name:<26} {cells}     ―")
    print(f"\n最大の増加＝{worst[0]}: {worst[1]:+.1f}%")


def main() -> None:
    if "--levels" in sys.argv:
        level_span()
        return
    count_missing()

    dist_m = models.horizontal_distance_km(
        LAT_TX, LON_TX, LAT_RX, LON_RX) * 1000.0
    print(f"\n② 一様な刻みでの収束（hiroshima_short_grazing・{dist_m:.1f}m）")
    print("   間隔[m]   点数   回折損[dB]  最高標高[m]")
    base = terrain_grid.RESOLUTION_SPACING_M["high"]
    spacing = base
    for _ in range(6):
        loss, hmax, n = diff_loss(uniform_ts(spacing))
        print(f"   {spacing:7.3f} {n:6d}   {loss:9.2f}   {hmax:8.2f}")
        spacing /= 2.0

    print("\n③ 通過画素で刻む（z15＝「高」が見る層）")
    a, b = (LAT_TX, LON_TX), (LAT_RX, LON_RX)
    bnd = boundaries(terrain_grid.RESOLUTION_ZOOM["high"], a, b)
    edges = [0.0] + bnd + [1.0]
    eps = 1e-9
    mids = _midpoints(edges)
    loss, hmax, n = diff_loss(mids if mids[0] > 0 else mids)
    print(f"   弦の中点だけ        点数={n:5d}   回折損={loss:9.2f} dB  最高={hmax:.2f}m")

    both: list[float] = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        both.append(min(lo + eps, hi))
        both.append(max(hi - eps, lo))
    loss, hmax, n = diff_loss(both)
    print(f"   弦の両端（棚の縁）  点数={n:5d}   回折損={loss:9.2f} dB  最高={hmax:.2f}m")

    bnd14 = boundaries(terrain_grid.RESOLUTION_ZOOM["medium"], a, b)
    edges14 = [0.0] + bnd14 + [1.0]
    both14: list[float] = []
    for i in range(len(edges14) - 1):
        lo, hi = edges14[i], edges14[i + 1]
        both14.append(min(lo + eps, hi))
        both14.append(max(hi - eps, lo))
    loss, hmax, n = diff_loss(both14)
    print(f"   z14 の弦の両端      点数={n:5d}   回折損={loss:9.2f} dB  最高={hmax:.2f}m")


if __name__ == "__main__":
    main()
