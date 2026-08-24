"""B-032 の候補処方を、ゴールデン 26 本の**実データ**で測る探針。

⚠️ 製品コードではない。`tests/data/golden_links.json` は標高配列を凍結して
いるので、**ネットワークに触らず**実経路で再計算できる。

見るのは 3 つ:
  ① diff_loss がどう動くか（異常値 13 本が落ちるか）
  ② p_rx（受信レベル）がどう動くか
  ③ **OK / NG の判定が変わる本数**（ここが「利用者への約束」に効く）

使い方:
    & "$env:RADIOSIM_PYTHON" experiments/b032_golden_probe.py
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

VARIANTS = {
    "現行":       None,
    "G1:陰h+深1": make_variant("los", 1,  False, shadow_gate="height"),
    "ν>-0.8(二値)": make_variant("los", 20, False, one_edge="nu:-0.8"),
    "VS:0.4":     make_variant("los", 20, False, one_edge="valley", separation=0.4),
    "VS:0.8":     make_variant("los", 20, False, one_edge="valley", separation=0.8),
    "VS:1.6":     make_variant("los", 20, False, one_edge="valley", separation=1.6),
}


def recompute(rec: dict, variant, n: int | None = None, force_single: bool = False) -> dict:
    inp     = rec["input"]
    elevs   = np.array(rec["raw_elevs"], dtype=float)
    if n is not None and n != len(elevs):
        # ⚠️ 近似＝凍結配列を線形補間して標本数だけ振る（本物の再取得ではない）。
        #    標本数依存が「在るか無いか」を見るための目安として使う。
        elevs = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(elevs)), elevs)
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
        prop = models.calculate_propagation(
            terrain, inp["h_tx"], inp["h_rx"], inp["freq_mhz"], inp["veg_h"],
            inp["k_factor"], diff_method=("single" if force_single else inp["diff_method"]),
            env_type=inp["env_type"], rain_rate=inp["rain_rate"],
        )
        budget = models.calculate_link_budget(
            prop, inp["freq_mhz"], inp["p_tx"],
            inp["gain_tx"], inp["gain_rx"], inp["sens"],
        )
    finally:
        models._deygout_loss = orig
    return {
        "diff_loss": prop.diff_loss,
        "p_rx":      budget.p_rx,
        "status":    budget.status,
    }


def main() -> None:
    data  = json.loads(GOLDEN.read_text(encoding="utf-8"))
    links = data["links"]
    names = list(VARIANTS)

    print(f"ゴールデン {len(links)} 本・凍結標高から再計算（ネットワーク不使用）")
    print()
    head = (f"{'id':<26}{'method':<9}{'Single':>10}{'現行':>15}"
            + "".join(f"{n:>14}" for n in names[1:]))
    print(head)
    print("-" * len(head))

    flips: dict[str, list[str]] = {n: [] for n in names[1:]}
    stats: dict[str, list[float]] = {n: [] for n in names[1:]}

    for rec in links:
        base = recompute(rec, None)
        sgl = recompute(rec, None, force_single=True)
        row = (f"{rec['id']:<26}{rec['input']['diff_method']:<9}"
               f"{sgl['diff_loss']:>10.2f}{base['diff_loss']:>15.2f}")
        for n in names[1:]:
            r = recompute(rec, VARIANTS[n])
            row += f"{r['diff_loss']:>14.2f}"
            stats[n].append(base["diff_loss"] - r["diff_loss"])
            if r["status"] != base["status"]:
                flips[n].append(f"{rec['id']}: {base['status']}→{r['status']}")
        print(row)

    print()
    print("判定（OK/NG）が変わった本数と中身")
    for n in names[1:]:
        print(f"  {n:<12} {len(flips[n]):>2} 本" + ("  " + " / ".join(flips[n]) if flips[n] else ""))

    print()
    print("回折損 100 dB 超の本数（＝異常値として固定されている回線）")
    over_base = sum(1 for rec in links if recompute(rec, None)["diff_loss"] > 100)
    print(f"  現行         {over_base:>2} 本")
    for n in names[1:]:
        over = sum(1 for rec in links if recompute(rec, VARIANTS[n])["diff_loss"] > 100)
        print(f"  {n:<12} {over:>2} 本")


def sample_dependence() -> None:
    """実データで標本数依存が消えるか（近似・線形補間で標本数だけ振る）。"""
    data  = json.loads(GOLDEN.read_text(encoding="utf-8"))
    links = {r["id"]: r for r in data["links"]}
    targets = ["hiroshima_kure_ridge", "kobe_rokko", "fuji_kawaguchi"]
    print()
    print("標本数依存（⚠️ 凍結配列の線形補間による近似）")
    names = [n for n in VARIANTS if n != "現行"]
    print(f"{'id':<24}{'n':>6}{'Single':>10}{'現行':>12}"
          + "".join(f"{n:>14}" for n in names))
    print("-" * (62 + 14 * len(names)))
    for tid in targets:
        for n in (120, 180, 240, 360, 480, 720, 960):
            rec = links[tid]
            sg = recompute(rec, None, n=n, force_single=True)
            b  = recompute(rec, None, n=n)
            row = (f"{tid:<24}{n:>6}{sg['diff_loss']:>10.2f}{b['diff_loss']:>12.2f}")
            for nm in names:
                row += f"{recompute(rec, VARIANTS[nm], n=n)['diff_loss']:>14.2f}"
            print(row)
        print()


if __name__ == "__main__":
    main()
    sample_dependence()
