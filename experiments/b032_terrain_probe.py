"""B-032：ある回線の地形と ν の分布を直に見る探針。

⚠️ 製品コードではない。
「なぜこの回線では Deygout が `Single` と同じ値になるのか」を、
**一体化の範囲がどこまで伸びているか**まで含めて確かめるためのもの。

使い方:
    & "$env:RADIOSIM_PYTHON" experiments/b032_terrain_probe.py hiroshima_kure_ridge
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import models  # noqa: E402

GOLDEN = ROOT / "tests" / "data" / "golden_links.json"


def profile(rec: dict):
    inp   = rec["input"]
    elevs = np.array(rec["raw_elevs"], dtype=float)
    terr  = models.calculate_terrain_profile(
        elevs, inp["lat_tx"], inp["lon_tx"], inp["lat_rx"], inp["lon_rx"],
    )
    ec     = terr.elevs_with_curve
    n      = terr.num_samples
    tx_abs = float(ec[0])  + inp["h_tx"]
    rx_abs = float(ec[-1]) + inp["h_rx"]
    obs    = ec + inp["veg_h"]
    los    = np.linspace(tx_abs, rx_abs, n)
    d_m    = terr.d_km_axis * 1000.0
    lam    = 299792458 / (inp["freq_mhz"] * 1e6)

    D  = float(d_m[-1])
    d1 = np.maximum(d_m, 1.0)
    d2 = np.maximum(D - d_m, 1.0)
    nu = np.nan_to_num((obs - los) * np.sqrt(
        2.0 / np.maximum(lam * d1 * d2 / (d1 + d2), 1e-9)))
    f1 = np.sqrt(lam * d1 * d2 / (d1 + d2))
    return terr, obs, los, nu, f1, d_m


def main() -> None:
    tid  = sys.argv[1] if len(sys.argv) > 1 else "hiroshima_kure_ridge"
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    rec  = {r["id"]: r for r in data["links"]}[tid]
    terr, obs, los, nu, f1, d_m = profile(rec)
    n = len(obs)

    peak = int(np.argmax(nu[1:-1])) + 1
    print(f"{tid}: {terr.horiz_dist_km:.2f} km / {rec['input']['freq_mhz']:.0f} MHz "
          f"/ n={n} / veg_h={rec['input']['veg_h']:.0f} m")
    print(f"主障害物 = 第 {peak} 点（{d_m[peak]/1000:.2f} km）ν={nu[peak]:.2f} "
          f"地表+植生 {obs[peak]:.1f} m / LoS {los[peak]:.1f} m / F1 半径 {f1[peak]:.1f} m")

    # 一体化の範囲（ν > _NU_THRESHOLD が連続する範囲）
    over = nu > models._NU_THRESHOLD
    lo = hi = peak
    while lo > 0 and over[lo - 1]:
        lo -= 1
    while hi < n - 1 and over[hi + 1]:
        hi += 1
    print(f"一体化の範囲 = 第 {lo}〜{hi} 点（{d_m[lo]/1000:.2f}〜{d_m[hi]/1000:.2f} km "
          f"= 全体の {100*(hi-lo+1)/n:.0f}%）")
    print(f"  範囲外に残る点 = 左 {lo} 点 / 右 {n-1-hi} 点"
          f"  ⇒ そこでの ν の最大 = "
          f"{max(list(nu[1:lo]) + list(nu[hi+1:-1]), default=float('nan')):.2f}")
    print(f"  LoS を超える点 = {int(np.sum(obs > los))} / {n}")
    print()

    # ν の断面（20 点ごと）
    print(f"{'点':>5}{'距離km':>9}{'標高+植生':>11}{'LoS':>9}{'LoS差':>9}{'F1半径':>9}{'ν':>8}  一体化")
    print("-" * 74)
    step = max(1, n // 40)
    for i in range(0, n, step):
        mark = "■" if lo <= i <= hi else ("·" if nu[i] > models._NU_THRESHOLD else " ")
        star = " ←主" if i == peak else ""
        print(f"{i:>5}{d_m[i]/1000:>9.2f}{obs[i]:>11.1f}{los[i]:>9.1f}"
              f"{obs[i]-los[i]:>9.1f}{f1[i]:>9.1f}{nu[i]:>8.2f}  {mark}{star}")


if __name__ == "__main__":
    main()
