"""B-032：候補処方の残り値が妥当かを、**系統の違う手法**と照らす探針。

⚠️ 製品コードではない。⚠️ **真値ではない**＝実測でも基準実装でもなく、
「独立に組んだ別の教科書手法が同程度を返すか」を見るだけ。
（→ [[feedback-promote-recurring-checks]]「ここまでは大丈夫」は反例 1 つで嘘になる）

並べる手法:
  Single          : 現行の単一障害物（最大 ν 1 点だけ）
  Bullington      : TX/RX から見た最大仰角の 2 直線の交点を等価ナイフエッジ 1 枚とみなし、
                    P.526 §4.5.1 の補正項を掛ける（一般地形法の芯。多重回折を
                    1 枚に潰すので**下寄り**）
  Epstein-Peterson: 各障害物を隣の障害物の頂点を端点として順に足す
                    （Deygout と並ぶ古典。障害物が離れていると妥当・近いと過大）
  θ=0.0           : 落選案＝LoS を超える連続範囲を 1 枚に畳む（標本数で跳ぶ）
  Kν              : ✅ 採用＝ν > -0.8 の連続範囲を 1 枚に畳む

🔑 この探針が落とした案 2 つ:
   ① **陰ゲート案 G1 は「LoS を超える障害物が 1 つしかない」回線でも
      3 手法が一致する値の 2 倍を返していた**（凸面の裾が陰ゲートをすり抜ける）。
   ② 一体化に陰ゲートを重ねると逆に **Bullington より下**まで落ち、多重回折が消える。

⚠️ **手法の大小に一般的な順序は無い**（2026-08-25・Codex 48 巡目 P1）＝
   `fuji_kawaguchi` では Bullington < Ep-Pet < Kν だが、`hiroshima_kure_ridge`
   では Kν = Single < Bullington < Ep-Pet と逆になる。**1 行から一般化しない。**

🔑 **`hiroshima` の Ep-Pet 70.44 と「障害物数 3」のほうが人工的**（地形を見て判明・
   `b032_terrain_probe.py`）＝この経路は 9km 連続の山塊で、谷でも LoS 差 +31〜42m
   （F1 半径 12〜24m の 1.3〜1.8 倍**上**）＝電波は一度も回り込めない。
   3 と数えたのは **LoS をわずか数 m 下回る点が 3 か所ある**ためで、
   F1 半径に対して無意味な凹み。⇒ Kν が 1 枚と見るほうが物理に合う。

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
KL = make_variant("los", 20, False, one_edge="los")      # θ=0.0
KN = make_variant("los", 20, False, one_edge="nu:-0.8")  # ✅ 採用 Kν


def _geometry(rec: dict):
    """calculate_propagation と同じ障害物面・LoS・波長を組み直す。"""
    inp     = rec["input"]
    elevs   = np.array(rec["raw_elevs"], dtype=float)
    # ⚠️ earth_k は渡さない＝既定 4/3（2026-08-25・Codex 48 巡目 P1）。
    #    `k_factor` は**ライス K の表示値で曲率とは無関係**（tests/golden_corpus_gen.py
    #    の同じ注意書きを読まずに渡していた）。渡すと製品と違う地形で測ることになり、
    #    実際 ライス K だけ違う fuji_kawaguchi / fuji_ricek_low が別の値に分かれていた。
    terrain = models.calculate_terrain_profile(
        elevs, inp["lat_tx"], inp["lon_tx"], inp["lat_rx"], inp["lon_rx"],
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
    """ITU-R P.526 §4.5.1 の Bullington（見通し側も ν を出し、補正項を掛ける）。

    ⚠️ **2026-08-25 訂正（Codex 47 巡目 P1）**＝当初の実装は
    ①見通し経路を即 0.0 とし ②全ケースに掛かる補正項を落としていた。
    どちらも P.526 の手順と違い、「標準手法との照合」を名乗れない値だった。
    """
    D = float(d_m[-1] - d_m[0])
    inner = slice(1, -1)
    d1 = np.maximum(d_m[inner] - d_m[0], 1.0)
    d2 = np.maximum(d_m[-1] - d_m[inner], 1.0)

    los_slope = (rx_abs - tx_abs) / D
    slope_tx  = (obs[inner] - tx_abs) / d1          # TX から見た仰角
    m_tx      = float(np.max(slope_tx))

    if m_tx <= los_slope:
        # 見通し＝地形が LoS を切らない。P.526 はここでも最大 ν を求めて J(ν) を出す
        # （F1 に食い込んでいれば損失が付く）。
        los  = tx_abs + los_slope * (d_m[inner] - d_m[0])
        nu   = (obs[inner] - los) * np.sqrt(
            2.0 / np.maximum(lam * d1 * d2 / (d1 + d2), 1e-9))
        l_uc = models._diffraction_loss_fk(float(np.max(np.nan_to_num(nu))))
    else:
        slope_rx = (obs[inner] - rx_abs) / d2        # RX から見た仰角
        m_rx     = float(np.max(slope_rx))
        denom    = m_tx + m_rx
        if denom <= 0:
            return 0.0
        # 2 直線 tx_abs + m_tx·x = rx_abs + m_rx·(D - x) の交点＝等価ナイフエッジ
        x_b   = min(max((rx_abs - tx_abs + m_rx * D) / denom, 1.0), D - 1.0)
        h_b   = tx_abs + m_tx * x_b
        los_b = tx_abs + los_slope * x_b
        l_uc  = models._diffraction_loss_fk(_nu(h_b - los_b, x_b, D - x_b, lam))

    if l_uc <= 0.0:
        return 0.0
    # P.526 の補正項（全ケースに掛かる）
    return l_uc + (1.0 - float(np.exp(-l_uc / 6.0))) * (10.0 + 0.02 * (D / 1000.0))


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
    # ⚠️ earth_k は渡さない＝既定 4/3（2026-08-25・Codex 48 巡目 P1）。
    #    `k_factor` は**ライス K の表示値で曲率とは無関係**（tests/golden_corpus_gen.py
    #    の同じ注意書きを読まずに渡していた）。渡すと製品と違う地形で測ることになり、
    #    実際 ライス K だけ違う fuji_kawaguchi / fuji_ricek_low が別の値に分かれていた。
    terrain = models.calculate_terrain_profile(
        elevs, inp["lat_tx"], inp["lon_tx"], inp["lat_rx"], inp["lon_rx"],
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
            f"{'Ep-Pet':>9}{'K:LoS体':>9}{'Kν':>9}{'現行':>11}")
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
            f"{run_variant(rec, KL):>9.2f}"
            f"{run_variant(rec, KN):>9.2f}"
            f"{run_variant(rec, None):>11.2f}"
        )


if __name__ == "__main__":
    main()
