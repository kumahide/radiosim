"""B-032：候補処方の残り値が妥当かを、**系統の違う手法**と照らす探針。

⚠️ 製品コードではない。⚠️ **真値ではない**＝実測でも基準実装でもなく、
「独立に組んだ別の教科書手法が同程度を返すか」を見るだけ。
（→ [[feedback-promote-recurring-checks]]「ここまでは大丈夫」は反例 1 つで嘘になる）

並べる手法:
  Single          : 現行の単一障害物（最大 ν 1 点だけ）
  Bullington      : TX/RX から見た最大仰角の 2 直線の交点を等価ナイフエッジ 1 枚とみなす
                    （ITU-R P.526 の一般地形法の芯。多重回折を 1 枚に潰すので**下寄り**）
  Epstein-Peterson: 各障害物を隣の障害物の頂点を端点として順に足す
                    （Deygout と並ぶ古典。障害物が離れていると妥当・近いと過大）
  G1              : 候補処方の第 1 案（陰ゲート + 深さ 1）＝**まだ甘い**（下記）
  KG              : 採用候補（1 体 1 枚 + 陰ゲート）

🔑 この探針が決めたこと＝**G1 は「LoS を超える障害物が 1 つしかない」回線でも
   3 手法が一致する値の 2 倍を返していた**（凸面の裾が陰ゲートをすり抜ける）。
   KG はその 4 本で `Single`/`Bullington`/`Epstein-Peterson` と完全一致する。

使い方:
    & "$env:RADIOSIM_PYTHON" experiments/b032_reference_probe.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import models  # noqa: E402

from b032_variants_probe import make_variant  # noqa: E402

GOLDEN = ROOT / "tests" / "data" / "golden_links.json"
G1 = make_variant("los", 1, False, shadow_gate="height")
KG = make_variant("los", 20, False, shadow_gate="height", one_edge=True)


def _geometry(rec: dict):
    """calculate_propagation と同じ障害物面・LoS・波長を組み直す。"""
    inp     = rec["input"]
    elevs   = np.array(rec["raw_elevs"], dtype=float)
    terrain = models.calculate_terrain_profile(
        elevs, inp["lat_tx"], inp["lon_tx"], inp["lat_rx"], inp["lon_rx"],
        earth_k=inp["k_factor"],
    )
    ec      = terrain.elevs_with_curve
    tx_abs  = float(ec[0])  + inp["h_tx"]
    rx_abs  = float(ec[-1]) + inp["h_rx"]
    obs     = ec + inp["veg_h"]
    d_m     = terrain.d_km_axis * 1000.0
    lam     = 299792458 / (inp["freq_mhz"] * 1e6)
    return obs, d_m, tx_abs, rx_abs, lam


def _nu(h: float, d1: float, d2: float, lam: float) -> float:
    d1 = max(d1, 1.0)
    d2 = max(d2, 1.0)
    return h * float(np.sqrt(2.0 / max(lam * d1 * d2 / (d1 + d2), 1e-9)))


def bullington(obs, d_m, tx_abs, rx_abs, lam) -> float:
    """等価ナイフエッジ 1 枚に潰す（ITU-R P.526 の Bullington 構成）。"""
    D = float(d_m[-1] - d_m[0])
    inner = slice(1, -1)
    d1 = np.maximum(d_m[inner] - d_m[0], 1.0)
    d2 = np.maximum(d_m[-1] - d_m[inner], 1.0)

    slope_tx = (obs[inner] - tx_abs) / d1          # TX から見た仰角
    slope_rx = (obs[inner] - rx_abs) / d2          # RX から見た仰角
    i_tx = int(np.argmax(slope_tx)); m_tx = float(slope_tx[i_tx])
    i_rx = int(np.argmax(slope_rx)); m_rx = float(slope_rx[i_rx])

    los_slope = (rx_abs - tx_abs) / D
    if m_tx <= los_slope:
        return 0.0                                  # 見通し（遮蔽なし）

    # 2 直線 tx_abs + m_tx·x = rx_abs + m_rx·(D - x) の交点
    denom = m_tx + m_rx
    if denom <= 0:
        return 0.0
    x_b = (rx_abs - tx_abs + m_rx * D) / denom
    x_b = min(max(x_b, 1.0), D - 1.0)
    h_b = tx_abs + m_tx * x_b                       # 等価エッジの絶対高度
    los_b = tx_abs + los_slope * x_b
    return models._diffraction_loss_fk(_nu(h_b - los_b, x_b, D - x_b, lam))


def _obstacles(obs, d_m, tx_abs, rx_abs) -> list[int]:
    """LoS を超える連続区間ごとに最大点を 1 つ選び、障害物列とする。"""
    n   = len(obs)
    los = np.linspace(tx_abs, rx_abs, n)
    over = obs > los
    out: list[int] = []
    i = 1
    while i < n - 1:
        if over[i]:
            j = i
            while j < n - 1 and over[j]:
                j += 1
            seg = obs[i:j] - los[i:j]
            out.append(i + int(np.argmax(seg)))
            i = j
        else:
            i += 1
    return out


def epstein_peterson(obs, d_m, tx_abs, rx_abs, lam) -> float:
    """各障害物を、隣の障害物の頂点を端点として順に足す。"""
    idx = _obstacles(obs, d_m, tx_abs, rx_abs)
    if not idx:
        return 0.0
    total = 0.0
    for k, p in enumerate(idx):
        a_h = tx_abs if k == 0 else float(obs[idx[k - 1]])
        a_d = float(d_m[0]) if k == 0 else float(d_m[idx[k - 1]])
        b_h = rx_abs if k == len(idx) - 1 else float(obs[idx[k + 1]])
        b_d = float(d_m[-1]) if k == len(idx) - 1 else float(d_m[idx[k + 1]])
        span = b_d - a_d
        if span <= 0:
            continue
        d1 = float(d_m[p]) - a_d
        d2 = b_d - float(d_m[p])
        h  = float(obs[p]) - (a_h + (b_h - a_h) * (d1 / span))
        total += models._diffraction_loss_fk(_nu(h, d1, d2, lam))
    return total


def run_variant(rec: dict, variant, method: str | None = None) -> float:
    inp     = rec["input"]
    elevs   = np.array(rec["raw_elevs"], dtype=float)
    terrain = models.calculate_terrain_profile(
        elevs, inp["lat_tx"], inp["lon_tx"], inp["lat_rx"], inp["lon_rx"],
        earth_k=inp["k_factor"],
    )
    orig = models._deygout_loss
    if variant is not None:
        models._deygout_loss = variant
    try:
        return models.calculate_propagation(
            terrain, inp["h_tx"], inp["h_rx"], inp["freq_mhz"], inp["veg_h"],
            inp["k_factor"], diff_method=(method or inp["diff_method"]),
            env_type=inp["env_type"], rain_rate=inp["rain_rate"],
        ).diff_loss
    finally:
        models._deygout_loss = orig


# G1 が Single を上回った 6 本 ＋ 対照として収束した 3 本
TARGETS = [
    "hiroshima_kure_ridge", "kyoto_hiei", "kagoshima_sakurajima",
    "takamatsu_yashima", "veg_low_antenna", "veg_none",
    "fuji_kawaguchi", "kobe_rokko", "sapporo_teine",
]


def main() -> None:
    data  = json.loads(GOLDEN.read_text(encoding="utf-8"))
    links = {r["id"]: r for r in data["links"]}

    print("⚠️ どれも真値ではない（実測・基準実装との照合ではない）。")
    print("   系統の違う手法が同程度を返すかだけを見る。")
    print()
    head = (f"{'id':<24}{'障害物数':>8}{'Single':>9}{'Bulling':>9}"
            f"{'Ep-Pet':>9}{'G1':>9}{'KG':>9}{'現行':>11}")
    print(head)
    print("-" * len(head))
    for tid in TARGETS:
        rec = links[tid]
        obs, d_m, tx_abs, rx_abs, lam = _geometry(rec)
        nobs = len(_obstacles(obs, d_m, tx_abs, rx_abs))
        print(
            f"{tid:<24}{nobs:>8}"
            f"{run_variant(rec, None, 'single'):>9.2f}"
            f"{bullington(obs, d_m, tx_abs, rx_abs, lam):>9.2f}"
            f"{epstein_peterson(obs, d_m, tx_abs, rx_abs, lam):>9.2f}"
            f"{run_variant(rec, G1):>9.2f}"
            f"{run_variant(rec, KG):>9.2f}"
            f"{run_variant(rec, None):>11.2f}"
        )


if __name__ == "__main__":
    main()
