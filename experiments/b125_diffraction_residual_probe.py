"""回折損の「残り」を実データで数える探針（B-125〜B-130）。

⚠️ 製品コードではない。`tests/data/golden_links.json` は標高配列を凍結しているので、
**ネットワークに触らず**実経路 26 本で再計算できる（`b032_golden_probe.py` と同じ土俵）。

⚠️ **2026-08-26 に製品の回折モデルが Bullington へ替わった**（B-130）。この探針も
そこで作り替えた。**測れなくなった 2 つを先に書いておく**（同じ問いが再燃したときの記録）:

  ⛔ ①**再帰の深さ**（`_MAX_DEPTH`）と ②**区間幅の下限**（`_MIN_SEGMENT_M`）は
     **もう存在しない**＝どちらも独自 Deygout 実装の再帰に付いていた打ち切りで、
     Bullington は再帰しないので概念ごと消えた。
     - 当時の実測＝**深さは最大 3 / 上限 20**（B-127＝上限には届いていなかった）。
     - **幅の下限は値を 1 つも決めていなかった**（B-126＝0m/50m/200m で結果が一致）。

いま測るのは 3 つ:

  ③ [[B-125]] **`single` と複数障害物モデルが「見通し」と呼ぶ範囲が一致しているか**
     ＝かつて `single` だけが ν<0 で切っており、-0.8 < ν < 0 の帯（26 本中 2 本が
     実際にそこにいた）で 0 dB と出していた。**いまは J(ν) の 1 本に寄せてある。**
  ④ [[B-128]] **解像度の段階（低 20m / 中 10m / 高 5m）で回折損がどれだけ動くか**
     ⚠️ **Bullington にして悪化した**＝1% を超えて動く回線が 3 → 6 本、
     最大が +8.7% → **+14.8%**（`veg_low_antenna`）。**開示に書いてある数字はこれ。**
  ⑤ [[B-130]] **入力に対する連続性**＝植生高・アンテナ高を掃いて、
     **刻みを 1/10 にしたとき最大段差も 1/10 になるか**を見る。
     🔑 **これが跳び（崖）と急な坂を区別する唯一の方法**＝1m 刻みの差分を跳びと
     呼ぶと読み違える（2026-08-26 に実際に踏んだ）。
     実測＝旧実装 **比 1.00（崖・最大 84.6 dB）** / Bullington **比 0.19**。

使い方:
    & "$env:RADIOSIM_PYTHON" experiments/b125_diffraction_residual_probe.py
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

#: 解像度の段階（I-069）＝実効間隔 [m]。名前は画面の語に合わせる。
STEPS_M: tuple[tuple[str, float], ...] = (("低", 20.0), ("中", 10.0), ("高", 5.0))


def _load() -> list[dict]:
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return doc["links"] if isinstance(doc, dict) and "links" in doc else doc


def _terrain(rec: dict, n: int | None = None):
    inp   = rec["input"]
    elevs = np.array(rec["raw_elevs"], dtype=float)
    if n is not None and n != len(elevs):
        elevs = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(elevs)), elevs)
    return inp, models.calculate_terrain_profile(
        elevs, inp["lat_tx"], inp["lon_tx"], inp["lat_rx"], inp["lon_rx"],
    )


def _loss(rec, method, n=None, veg=None, dh=0.0) -> float:
    inp, terrain = _terrain(rec, n)
    return models.calculate_propagation(
        terrain, inp["h_tx"] + dh, inp["h_rx"] + dh, inp["freq_mhz"],
        inp["veg_h"] if veg is None else veg, inp["k_factor"],
        diff_method=method, env_type=inp["env_type"], rain_rate=inp["rain_rate"],
    ).diff_loss


def _nu_max(rec) -> float:
    inp, terrain = _terrain(rec)
    elevs = terrain.elevs_with_curve
    n     = terrain.num_samples
    los   = np.linspace(float(elevs[0]) + inp["h_tx"],
                        float(elevs[-1]) + inp["h_rx"], n)
    lam   = 299792458 / (inp["freq_mhz"] * 1e6)
    d_m   = terrain.d_km_axis * 1000
    total = terrain.horiz_dist_km * 1000
    d1    = np.maximum(d_m, 1.0)
    d2    = np.maximum(total - d_m, 1.0)
    nu = (elevs + inp["veg_h"] - los) * np.nan_to_num(
        np.sqrt(np.maximum(0, 2 * (d1 + d2) / (lam * d1 * d2 + 1e-9))))
    return float(np.nanmax(np.nan_to_num(nu)))


def _report_band(recs) -> None:
    print("=== ③ 2 つのモデルが「見通し」と呼ぶ範囲は一致しているか（B-125）===")
    band = disagree = 0
    for rec in recs:
        nu  = _nu_max(rec)
        sgl = _loss(rec, "single")
        mul = _loss(rec, models.DIFF_METHOD_MULTI)
        if models._NU_THRESHOLD < nu < 0.0:
            band += 1
            print(f"  帯の中: {rec['id']:24s} nu={nu:+.3f}  single={sgl:6.2f}  "
                  f"{models.DIFF_METHOD_MULTI}={mul:6.2f}")
        if (sgl == 0.0) != (mul == 0.0):
            disagree += 1
            print(f"  [!] 食い違い: {rec['id']:24s} single={sgl:.2f} / {mul:.2f}")
    print(f"  => nu が {models._NU_THRESHOLD}〜0 の帯にいる回線 {band} 本 / "
          f"0 dB かどうかが食い違う回線 {disagree} 本（0 であること）")


def _report_resolution(recs) -> None:
    print("\n=== ④ 解像度の段階で回折損がどれだけ動くか（B-128）===")
    print(f"{'link':24s} " + " ".join(f"{lb}({s:g}m)".rjust(9) for lb, s in STEPS_M)
          + f" {'低→高':>7s}")
    moved = []
    for rec in recs:
        _, base = _terrain(rec)
        dist_m = base.horiz_dist_km * 1000
        vals = []
        for _, step in STEPS_M:
            n = max(3, min(int(dist_m / step) + 1, 20000))
            vals.append(_loss(rec, models.DIFF_METHOD_MULTI, n=n))
        if vals[0] > 0.5:
            ratio = (vals[2] / vals[0] - 1.0) * 100
            moved.append((abs(ratio), rec["id"], ratio))
            if abs(ratio) > 1.0:
                print(f"{rec['id'][:24]:24s} " + " ".join(f"{v:9.2f}" for v in vals)
                      + f" {ratio:+6.1f}%")
    moved.sort(reverse=True)
    print(f"  => 1% を超えて動く回線 {sum(1 for m, _, _ in moved if m > 1.0)} / "
          f"{len(moved)} 本（最大 {moved[0][2]:+.1f}% = {moved[0][1]}）")


def _report_continuity(recs) -> None:
    print("\n=== ⑤ 入力に対する連続性（B-130）===")
    print("  刻みを 1/10 にして最大段差も 1/10 になるか（比が小さいほど連続）")
    worst = (0.0, "", "")
    for rec in recs:
        for sweep in ("veg_h", "antenna"):
            def at(x, _rec=rec, _sweep=sweep):
                return (_loss(_rec, models.DIFF_METHOD_MULTI, veg=float(x))
                        if _sweep == "veg_h"
                        else _loss(_rec, models.DIFF_METHOD_MULTI, dh=float(x)))

            def max_step(step, _at=at):
                xs = np.arange(0.0, 20.0 + step / 2, step)
                vals = [_at(x) for x in xs]
                return max(abs(vals[j + 1] - vals[j]) for j in range(len(vals) - 1))

            coarse = max_step(1.0)
            if coarse < 0.5:
                continue
            fine  = max_step(0.1)
            ratio = fine / coarse
            if ratio > worst[0]:
                worst = (ratio, rec["id"], sweep)
            if ratio > 0.3:
                print(f"{rec['id'][:24]:24s} {sweep:>8s} 刻1.0m={coarse:7.2f} "
                      f"刻0.1m={fine:7.2f} 比={ratio:5.2f}  [!] 不連続の疑い")
    print(f"  => 最悪の比 {worst[0]:.2f}（{worst[1]} / {worst[2]}）"
          f"  ＝ 0.3 未満なら連続、1.0 付近なら崖")


def main() -> None:
    recs = _load()
    _report_band(recs)
    _report_resolution(recs)
    _report_continuity(recs)
    print("\n注意: 判定（OK/NG）への影響はこの探針では見ていない＝"
          "`b032_golden_probe.py` が同じコーパスで見る面。")


if __name__ == "__main__":
    main()
