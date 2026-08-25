"""I-069：解像度の段階（高/中/低）ごとに、実データで答えがどれだけ動くかを測る探針。

⚠️ 製品コードではない。
既定を決める前に測るためのもの（ISSUES.md I-069 の注意＝B-032 を直しても
「深く遮蔽された経路では標本数を増やすと回折損がまだ +18% 増える」）。
**段階を上げると判定が NG 側へ振れるかどうか**を、コーパスの 26 回線で見る。

⚠️ 実行すると GSI の DEM タイルを取得する（キャッシュ済みなら再取得しない）。
標本の座標は変わるがタイルは同じなので、コーパス生成後なら通信はほぼ無い。

使い方:
    & "$env:RADIOSIM_PYTHON" experiments/i069_resolution_probe.py
    & "$env:RADIOSIM_PYTHON" experiments/i069_resolution_probe.py hiroshima_kure_ridge
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import models              # noqa: E402
from core import simulation as sim   # noqa: E402

GOLDEN = ROOT / "tests" / "data" / "golden_links.json"

# 段階＝間隔 [m]（ISSUES.md I-069 提案 2・ユーザー確定の語）
LEVELS: list[tuple[str, float]] = [("高", 5.0), ("中", 10.0), ("低", 20.0)]

# 現行の入力欄の範囲（core/config.py の `samples` ルールと同じ）
N_MIN, N_MAX = 10, 2000


def resolve_n(dist_m: float, spacing_m: float) -> int:
    """距離と目標間隔から標本数を解く（製品に入れる純関数の試作）。"""
    return int(min(N_MAX, max(N_MIN, round(dist_m / spacing_m) + 1)))


def _params(inp: dict, n: int) -> sim.SimParams:
    return sim.SimParams({
        "start": f"{inp['lat_tx']}, {inp['lon_tx']}",
        "end":   f"{inp['lat_rx']}, {inp['lon_rx']}",
        "h_tx": str(inp["h_tx"]), "h_rx": str(inp["h_rx"]),
        "freq": str(inp["freq_mhz"]), "p_tx": str(inp["p_tx"]),
        "gain_tx": str(inp["gain_tx"]), "gain_rx": str(inp["gain_rx"]),
        "sens": str(inp["sens"]), "veg_h": str(inp["veg_h"]),
        "k_factor": str(inp["k_factor"]), "samples": str(n),
        "env_type": inp["env_type"], "diff_method": inp["diff_method"],
        "rain_rate": str(inp["rain_rate"]),
    })


def _fetch(params: sim.SimParams) -> np.ndarray:
    out: list[np.ndarray] = []
    err: list[Exception] = []
    done = threading.Event()
    sim.fetch_elevations(
        params,
        on_progress=lambda n: None,
        on_complete=lambda e: (out.append(e), done.set()),
        on_error=lambda ex: (err.append(ex), done.set()),
    )
    done.wait()
    if err:
        raise err[0]
    return out[0]


def _run(inp: dict, n: int) -> tuple:
    params  = _params(inp, n)
    raw     = np.round(_fetch(params), 2)
    terrain = models.calculate_terrain_profile(
        raw_elevs=raw,
        lat_tx=params.lat_tx, lon_tx=params.lon_tx,
        lat_rx=params.lat_rx, lon_rx=params.lon_rx,
    )
    res = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
    return terrain.horiz_dist_km, res


def main() -> None:
    data  = json.loads(GOLDEN.read_text(encoding="utf-8"))
    links = data["links"]
    if len(sys.argv) > 1:
        links = [r for r in links if r["id"] in sys.argv[1:]]

    print(f"{'id':<26} {'km':>6} | {'現行':>16} | " +
          " | ".join(f"{lv+'（'+str(int(sp))+'m）':>26}" for lv, sp in LEVELS))
    flips: list[str] = []
    saturated: list[str] = []

    for rec in links:
        inp    = rec["input"]
        base_n = inp["samples"]
        dist_km, base = _run(inp, base_n)
        dist_m = dist_km * 1000.0
        row = (f"{rec['id']:<26} {dist_km:6.2f} | "
               f"N={base_n:<4} {base.status:<3} {base.p_rx:7.1f} | ")

        cells = []
        for lv, spacing in LEVELS:
            n = resolve_n(dist_m, spacing)
            eff = dist_m / max(n - 1, 1)
            _, res = _run(inp, n)
            mark = " " if res.status == base.status else "*"
            cells.append(f"N={n:<4} 実効{eff:5.1f}m {res.status:<3}"
                         f"{res.p_rx:8.1f}{mark}")
            if res.status != base.status:
                flips.append(f"{rec['id']}: {lv} で {base.status} → {res.status} "
                             f"（p_rx {base.p_rx:.1f} → {res.p_rx:.1f} dBm / "
                             f"回折 {base.diff_loss:.1f} → {res.diff_loss:.1f} dB）")
            if n >= N_MAX:
                saturated.append(f"{rec['id']}: {lv} は上限 {N_MAX} に張り付き"
                                 f"（実効 {eff:.1f} m）")
        print(row + " | ".join(cells))

    print("\n--- 判定が動いた回線 ---")
    print("\n".join(flips) if flips else "なし")
    print("\n--- 上限 2000 に張り付いた段階（『高』と『中』が同じ結果になる面） ---")
    print("\n".join(saturated) if saturated else "なし")


if __name__ == "__main__":
    main()
