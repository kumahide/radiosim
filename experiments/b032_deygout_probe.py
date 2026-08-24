"""B-032（Deygout の発散）の処方探索用の探針。

⚠️ これは製品コードではない（`experiments/README.md` の位置づけ）。
ISSUES.md の B-032 に記録された実測表を**まず再現**し、そのうえで
候補処方を同じ土俵で並べるためのもの。

使い方:
    & "$env:RADIOSIM_PYTHON" experiments/b032_deygout_probe.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import models  # noqa: E402


# ──────────────────────────────────────────────────────────────
# 合成地形（ISSUES.md B-032 の 3 巡目の条件に合わせる）
#   10km・150MHz・TX 40m / RX 20m・n=200
# ──────────────────────────────────────────────────────────────
DIST_KM  = 10.0
FREQ_MHZ = 150.0
H_TX     = 40.0
H_RX     = 20.0


def _profile(elevs: np.ndarray, dist_km: float = DIST_KM) -> models.TerrainProfile:
    """緯度経度を経由せず TerrainProfile を直に組む（曲率補正は本物と同じ式）。"""
    n         = len(elevs)
    d_km_axis = np.linspace(0.0, dist_km, n)
    Re        = 6371.0 * (4 / 3)
    curve     = (d_km_axis * (dist_km - d_km_axis)) / (2 * Re) * 1000
    return models.TerrainProfile(
        raw_elevs        = elevs,
        elevs_with_curve = elevs + curve,
        d_km_axis        = d_km_axis,
        horiz_dist_km    = dist_km,
        num_samples      = n,
        earth_k          = 4 / 3,
    )


def flat(n: int = 200) -> np.ndarray:
    return np.zeros(n)


def wide_hill(h: float, n: int = 200) -> np.ndarray:
    """経路全体にまたがる滑らかな丘（raised cosine）。"""
    x = np.linspace(0.0, 1.0, n)
    return h * 0.5 * (1 - np.cos(2 * np.pi * x))


def narrow_peak(h: float, n: int = 200, width: float = 0.06) -> np.ndarray:
    """中央だけの細い峰（gaussian）。"""
    x = np.linspace(0.0, 1.0, n)
    return h * np.exp(-((x - 0.5) ** 2) / (2 * width ** 2))


def two_peaks(h: float, n: int = 200, width: float = 0.05) -> np.ndarray:
    """2 つの峰。

    ⚠️ **これが「独立した 2 峰」かはアンテナ高で決まる**（2026-08-25）。
    既定条件（TX 40m / RX 20m・10km・150MHz）では LoS が谷の 30m 上・F1 半径 70m
    ＝**谷でも F1 の 43% しかクリアしていない**（実務の「60% ルール」に届かない）
    ので、電波的には 1 つの広がった遮蔽であって独立した 2 峰ではない。
    本当に独立した形を見るには `two_peaks_clear()` を使う。
    """
    x = np.linspace(0.0, 1.0, n)
    return h * (
        np.exp(-((x - 0.30) ** 2) / (2 * width ** 2))
        + np.exp(-((x - 0.70) ** 2) / (2 * width ** 2))
    )


def two_peaks_clear(h: float = 250.0, n: int = 200, width: float = 0.05) -> np.ndarray:
    """**本当に独立した 2 峰**＝谷で第 1 フレネルゾーンが十分クリアになる形。

    `H_TX_HIGH` / `H_RX_HIGH` と組で使う（LoS を持ち上げて谷を相対的に深くする）。
    谷は LoS より約 150m 下＝F1 半径 70m に対し ν ≈ -3 ＝完全な見通し。
    ここで多重回折が出なければ、それは処方が独立した障害物を潰している証拠になる。
    """
    return two_peaks(h, n, width)


# 「本当に独立した 2 峰」を見るためのアンテナ高
H_TX_HIGH = 150.0
H_RX_HIGH = 150.0


# ──────────────────────────────────────────────────────────────
# 計測
# ──────────────────────────────────────────────────────────────
def measure(elevs: np.ndarray, veg_h: float = 0.0, dist_km: float = DIST_KM) -> dict:
    """single / deygout の回折損と、再帰の回数・ν の幅を返す。"""
    terrain = _profile(elevs, dist_km)

    calls: list[float] = []
    orig = models._diffraction_loss_fk

    def spy(v: float) -> float:
        calls.append(v)
        return orig(v)

    models._diffraction_loss_fk = spy
    try:
        deygout = models.calculate_propagation(
            terrain, H_TX, H_RX, FREQ_MHZ, veg_h, 4 / 3, diff_method="deygout"
        ).diff_loss
        nus = list(calls)
        calls.clear()
        single = models.calculate_propagation(
            terrain, H_TX, H_RX, FREQ_MHZ, veg_h, 4 / 3, diff_method="single"
        ).diff_loss
    finally:
        models._diffraction_loss_fk = orig

    return {
        "single":  single,
        "deygout": deygout,
        "calls":   len(nus),
        "nu_min":  min(nus) if nus else float("nan"),
        "nu_max":  max(nus) if nus else float("nan"),
    }


CASES: list[tuple[str, np.ndarray, float]] = [
    ("平地 0m",        flat(),          0.0),
    ("広い丘 5m",      wide_hill(5),    0.0),
    ("広い丘 15m",     wide_hill(15),   0.0),
    ("広い丘 20m",     wide_hill(20),   0.0),
    ("広い丘 25m",     wide_hill(24.999), 0.0),
    ("広い丘 40m",     wide_hill(40),   0.0),
    ("広い丘 80m",     wide_hill(80),   0.0),
    ("細い峰 25m",     narrow_peak(25), 0.0),
    ("2 つの峰 40m",   two_peaks(40),   0.0),
]


def main() -> None:
    print(f"条件: {DIST_KM}km / {FREQ_MHZ}MHz / TX {H_TX}m / RX {H_RX}m / n=200")
    print()
    print(f"{'形':<16}{'Single':>10}{'Deygout':>12}{'再帰':>7}{'ν の幅':>20}")
    print("-" * 66)
    for name, elevs, veg in CASES:
        r = measure(elevs, veg)
        print(
            f"{name:<16}{r['single']:>10.2f}{r['deygout']:>12.2f}{r['calls']:>7}"
            f"{r['nu_min']:>10.2f}..{r['nu_max']:>8.2f}"
        )

    # 標本数依存（深い遮蔽の顔）＝平地 + LoS をわずかに超えるキャノピー・2km
    print()
    print("標本数依存（2km 平地・veg_h=31・LoS をわずかに超えるキャノピー）")
    print(f"{'n':>6}{'Single':>10}{'Deygout':>12}{'再帰':>7}")
    print("-" * 35)
    for n in (51, 101, 201, 401, 801):
        r = measure(np.zeros(n), veg_h=31.0, dist_km=2.0)
        print(f"{n:>6}{r['single']:>10.2f}{r['deygout']:>12.2f}{r['calls']:>7}")

    # 広い丘 25m の標本数依存（こちらは安定するはず＝同じ欠陥の別の顔）
    print()
    print("標本数依存（広い丘 25m・10km）")
    print(f"{'n':>6}{'Single':>10}{'Deygout':>12}{'再帰':>7}")
    print("-" * 35)
    for n in (51, 101, 201, 401, 801):
        r = measure(wide_hill(24.999, n))
        print(f"{n:>6}{r['single']:>10.2f}{r['deygout']:>12.2f}{r['calls']:>7}")


if __name__ == "__main__":
    main()
