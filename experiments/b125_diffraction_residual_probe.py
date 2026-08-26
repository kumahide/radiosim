"""B-125〜B-128（B-032 を直した**後**に回折損へ残っているもの）を実データで数える探針。

⚠️ 製品コードではない。`tests/data/golden_links.json` は標高配列を凍結しているので、
**ネットワークに触らず**実経路 26 本で再計算できる（`b032_golden_probe.py` と同じ土俵）。

測るのは 4 つ＝**起票の重要度をこの数字で決めた**（2026-08-26）:

  ① [[B-127]] **再帰の深さ**が上限 `_MAX_DEPTH` に届いているか
     ⇒ 実データの最大は **3**（実効 5m でも 3）＝いまは触れていない ⇒ 重要度を「低」へ。
  ② [[B-126]] **`_MIN_SEGMENT_M`（50m）で打ち切った回数**
     ⇒ 実効 20m / 10m で **0 回**、実効 5m で **3 回**（fuji 2 本・kagoshima）
     ＝**「高」を選んだときだけ効く打ち切りがある**。50m という長さは標本間隔に対する
     点数が解像度で変わるので、幾何ではなく設定で発火が決まっている。
  ③ [[B-125]] **`single` と `deygout` の「見通し」の物差しの差**
     ＝`single` は ν<0 で 0 dB（[models.py] の `v_max < 0`）、`deygout` は ν<=-0.8。
     ⇒ **-0.8 < ν < 0 の帯を `single` だけが捨てる**。26 本中 **2 本が実際にその帯**
     （`nagoya_grazing_900` ν=-0.425 で 2.53 dB／`niigata_grazing_900` で 2.47 dB）。
     `single` は ν=0 に **6.03 dB の段差**を持つ（`deygout` 側は連続）。
  ④ [[B-128]] **解像度の段階（低 20m / 中 10m / 高 5m）で回折損がどれだけ動くか**
     ⇒ 動くのは深い遮蔽の 3 本だけ（`veg_low_antenna` 87.86→95.48 dB＝**+8.7%**、
     `shizuoka_nihondaira` +8.0%、`kobe_rokko` +3.2%）。残り 23 本は 1% 未満。

⚠️ **標本数の振り直しは凍結配列の線形内挿**＝本物の再取得ではない（`b032_golden_probe.py`
の `recompute` と同じ近似）。**依存が「在るか無いか」を見る目安**として使う。
⚠️ **`earth_k` は渡さない**＝既定 `EARTH_K_STANDARD`（`k_factor` はライス K で曲率とは
無関係＝B-032 の 48 巡目で踏んだ罠）。

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

_orig_deygout = models._deygout_loss
_stats: dict[str, int] = {}


def _traced(*args, **kwargs):
    """`_deygout_loss` を包んで、**打ち切りが効いた回数**を数える。

    ⚠️ 製品は打ち切りの痕跡を一切残さない（→ [[B-127]]）ので、外から包むしかない。
    """
    d_axis = kwargs.get("d_m_axis", args[1] if len(args) > 1 else None)
    _stats["max_depth"] = max(_stats.get("max_depth", 0), int(kwargs.get("depth", 0)))
    if d_axis is not None:
        if len(d_axis) < 3:
            _stats["n_cut"] = _stats.get("n_cut", 0) + 1
        elif float(d_axis[-1]) - float(d_axis[0]) < models._MIN_SEGMENT_M:
            _stats["span_cut"] = _stats.get("span_cut", 0) + 1
    return _orig_deygout(*args, **kwargs)


def _load() -> list[dict]:
    recs = json.loads(GOLDEN.read_text(encoding="utf-8"))
    if isinstance(recs, dict):
        recs = recs.get("links") or recs.get("cases") or list(recs.values())
    return list(recs)


def _name(rec: dict, idx: int) -> str:
    for key in ("name", "id", "label", "case"):
        value = rec.get(key)
        if isinstance(value, str):
            return value
    return f"#{idx}"


def _terrain(rec: dict, n: int | None = None):
    inp   = rec["input"]
    elevs = np.array(rec["raw_elevs"], dtype=float)
    if n is not None and n != len(elevs):
        elevs = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(elevs)), elevs)
    return inp, models.calculate_terrain_profile(
        elevs, inp["lat_tx"], inp["lon_tx"], inp["lat_rx"], inp["lon_rx"],
    )


def _prop(inp: dict, terrain, method: str):
    return models.calculate_propagation(
        terrain, inp["h_tx"], inp["h_rx"], inp["freq_mhz"], inp["veg_h"],
        inp["k_factor"], diff_method=method,
        env_type=inp["env_type"], rain_rate=inp["rain_rate"],
    )


def _nu_max(inp: dict, terrain) -> float:
    """`single` が主障害物として選ぶ点の ν（＝製品の `single` 枝と同じ式）。"""
    elevs = terrain.elevs_with_curve
    n     = terrain.num_samples
    los   = np.linspace(float(elevs[0]) + inp["h_tx"],
                        float(elevs[-1]) + inp["h_rx"], n)
    lam   = 299792458 / (inp["freq_mhz"] * 1e6)
    d_m   = terrain.d_km_axis * 1000
    total = terrain.horiz_dist_km * 1000
    d1    = np.maximum(d_m, 1.0)
    d2    = np.maximum(total - d_m, 1.0)
    sqrt_term = np.sqrt(np.maximum(0, 2 * (d1 + d2) / (lam * d1 * d2 + 1e-9)))
    nu = (elevs + inp["veg_h"] - los) * np.nan_to_num(sqrt_term)
    return float(np.nanmax(np.nan_to_num(nu)))


def main() -> None:
    recs = _load()

    print("=== ①②③ 現行の標本数（凍結配列そのまま）===")
    print(f"{'link':26s} {'dist_km':>7s} {'N':>5s} {'間隔m':>6s} {'deygout':>8s} "
          f"{'深さ':>4s} {'幅切':>4s} {'single':>7s} {'sgl@-0.8':>8s} {'差dB':>6s}")
    max_depth = span_cut = 0
    band: list[tuple[str, float, float]] = []
    for idx, rec in enumerate(recs):
        name = _name(rec, idx)
        inp, terrain = _terrain(rec)
        _stats.clear()
        models._deygout_loss = _traced
        try:
            deyg = _prop(inp, terrain, "deygout").diff_loss
        finally:
            models._deygout_loss = _orig_deygout
        sgl  = _prop(inp, terrain, "single").diff_loss
        nu   = _nu_max(inp, terrain)
        # `deygout` と同じ物差し（ν<=-0.8）で切った場合の `single`
        sgl_at_threshold = models._diffraction_loss_fk(nu)
        depth = _stats.get("max_depth", 0)
        cuts  = _stats.get("span_cut", 0)
        max_depth = max(max_depth, depth)
        span_cut += cuts
        if models._NU_THRESHOLD < nu < 0.0:
            band.append((name, nu, sgl_at_threshold))
        spacing = terrain.horiz_dist_km * 1000 / max(terrain.num_samples - 1, 1)
        print(f"{name[:26]:26s} {terrain.horiz_dist_km:7.2f} {terrain.num_samples:5d} "
              f"{spacing:6.1f} {deyg:8.2f} {depth:4d} {cuts:4d} "
              f"{sgl:7.2f} {sgl_at_threshold:8.2f} {sgl_at_threshold - sgl:6.2f}")

    print(f"\n① 最大再帰深さ = {max_depth} / 上限 {models._MAX_DEPTH}"
          f"（[[B-127]]＝上限に届いていないが、届いても痕跡は残らない）")
    print(f"② `_MIN_SEGMENT_M`({models._MIN_SEGMENT_M:g}m) の打ち切り = {span_cut} 回"
          f"（[[B-126]]・この標本数では）")
    print(f"③ ν が {models._NU_THRESHOLD} 〜 0 の帯にある回線 = {len(band)} 本"
          f"（[[B-125]]＝`single` だけが捨てている dB）")
    for name, nu, loss in band:
        print(f"     {name:26s} ν={nu:+.3f}  single=0.00 / J(ν)={loss:.2f} dB")
    print("   参考＝J(ν) の値: " + " / ".join(
        f"J({v:+.2f})={models._diffraction_loss_fk(v):.2f}"
        for v in (-0.80, -0.50, -0.20, -0.05, 0.00)
    ) + "  ⇒ `single` は ν=0 に 6.03 dB の段差を持つ")

    print("\n=== ④ 解像度の段階で回折損がどれだけ動くか（[[B-128]]）===")
    print(f"{'link':26s} " + " ".join(f"{label}({step:g}m)".rjust(9)
                                      for label, step in STEPS_M)
          + f" {'低→高':>7s} {'深さ':>4s} {'幅切':>4s}")
    worst: list[tuple[float, str]] = []
    for idx, rec in enumerate(recs):
        name = _name(rec, idx)
        _, base = _terrain(rec)
        dist_m = base.horiz_dist_km * 1000
        values: list[float] = []
        depth = cuts = 0
        for _, step in STEPS_M:
            n = max(3, min(int(dist_m / step) + 1, 20000))
            inp, terrain = _terrain(rec, n)
            _stats.clear()
            models._deygout_loss = _traced
            try:
                values.append(_prop(inp, terrain, "deygout").diff_loss)
            finally:
                models._deygout_loss = _orig_deygout
            depth = max(depth, _stats.get("max_depth", 0))
            cuts += _stats.get("span_cut", 0)
        ratio = (values[-1] / values[0] - 1.0) * 100 if values[0] > 0.01 else float("nan")
        if values[0] > 0.01:
            worst.append((ratio, name))
        print(f"{name[:26]:26s} " + " ".join(f"{v:9.2f}" for v in values)
              + f" {ratio:6.1f}% {depth:4d} {cuts:4d}")
    worst.sort(reverse=True)
    print("\n④ 動きの大きい順: " + " / ".join(f"{n} {r:+.1f}%" for r, n in worst[:3]))
    # ⚠️ 出力に絵文字を混ぜない＝Windows の cp932 コンソールで `UnicodeEncodeError`
    #    になり、**表を全部出したあとで落ちる**（2026-08-26 に踏んだ）。
    print("注意: 判定（OK/NG）への影響はこの探針では見ていない＝"
          "`b032_golden_probe.py` が同じコーパスで見る面（起票時は判定 0 本変化）。")


if __name__ == "__main__":
    main()
