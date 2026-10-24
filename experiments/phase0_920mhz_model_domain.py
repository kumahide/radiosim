"""920 MHz が本体のモデルの定義域の外側に当たる量を測る探針。

**なぜ測るか**＝RadioSim Field の 1 回目の実測は「較正が要るかの判定」で、
帯域は 920 MHz に決まった（2026-09-21 ユーザー決定）。判定は
**実測 − 本体の予測** の残差を見る作業なので、予測の側が定義域の外で
出している量が分かっていないと、系統差を「モデルの誤差」と読めない。

本体の定数（`core/models.py`）が言う 920 MHz（= 0.92 GHz）の立場:

  - 植生減衰 `VEG_COEFF_RANGE_GHZ = (1.0, 6.0)` の **下側の外**（外挿）
  - 降雨減衰 `RAIN_MIN_GHZ = 1.0` の **下**（0 dB に固める）
  - 大気減衰 `GAS_RANGE_GHZ = (1.0, 350.0)` の **下側の外**（0 dB に固める）

測るのは 4 つ:

  ① **係数の縁は崖か坂か**＝`_vegetation_loss` の係数は 1 GHz で分岐が替わる。
     周波数の刻みを 1/10 にして掃き、段差が刻みに比例して縮むか見る
     （→ [[feedback_refine_the_step]]）。⚠️ **範囲は 1/10 にしない**。
  ② **外挿ぶんの大きさ**＝同じ幾何を 920 MHz で回し、植生減衰を
     「内側の枝をそのまま 0.92 GHz へ伸ばした場合」と比べる。
     ⛔ どちらが正しいかはここでは決めない（文献の裏取りは B-132 の宿題）。
  ③ **0 dB に固めた 2 項が捨てている量**＝降雨・大気を 920 MHz で 0 にする
     ことで、予測から何 dB 落ちるか。既定（降雨 0 mm/h）では降雨は
     そもそも計算しないので、**降雨を入れた条件でも測る**。
  ④ **開示は出るか**＝`scope_notes` が 920 MHz で 3 つの刻印を出すことを、
     Field の測定条件（植生あり／なし・降雨あり／なし）ごとに確かめる。

⛔ **合成地形は使わない**＝`tests/data/golden_links.json` の凍結標高で回す
（→ [[feedback_synthetic_cases_lie]]）。ネットワーク不要。

使い方:
    & "$env:RADIOSIM_PYTHON" experiments/phase0_920mhz_model_domain.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import models  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:                                    # 既定のコンソールは cp932（B-261）
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    except Exception:
        pass

GOLDEN = ROOT / "tests" / "data" / "golden_links.json"

#: Field が測る帯域（E220-900T22S(JP) の下側の区分・920.6〜923.4 MHz の中ほど）。
FIELD_FREQ_MHZ = 922.0

#: 比較用＝本体の既定（2.4 GHz）。Field の 2.4 GHz 側の対照もこの周波数。
REF_FREQ_MHZ = 2400.0

#: 植生高の代表値。⚠️ 45 dB の頭打ちに触らせない（B-129 の 1 巡目の失敗）。
VEG_H_M = 3.0

#: 降雨率の代表値（本体の既定値の並びにある値）。
RAIN_MM_H = 50.0


def _load() -> list[dict]:
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return doc["links"] if isinstance(doc, dict) else doc


def _veg_coeff(freq_ghz: float) -> float:
    """`_vegetation_loss` の係数の枝を、そのまま写す（製品と同じ式）。"""
    veg_lo, veg_hi = models.VEG_COEFF_RANGE_GHZ
    if freq_ghz < veg_lo:
        return 0.12 * (freq_ghz ** 0.5)
    if freq_ghz < veg_hi:
        return 0.20 * (freq_ghz ** 0.7)
    return 0.35 * (freq_ghz ** 0.9)


def _inner_branch_coeff(freq_ghz: float) -> float:
    """**内側の枝**（1〜6 GHz 用）を 0.92 GHz へそのまま伸ばした係数。"""
    return 0.20 * (freq_ghz ** 0.7)


# ============================================================
# ① 係数の縁は崖か坂か
# ============================================================
def _edge(edge_mhz: float, label: str) -> None:
    print(f"  ◆ {label}（{edge_mhz:.0f} MHz）")
    for step in (100.0, 10.0, 1.0):
        lo = _veg_coeff((edge_mhz - step) / 1000.0)
        hi = _veg_coeff((edge_mhz + step) / 1000.0)
        print(f"    刻み {step:6.1f} MHz: "
              f"{edge_mhz - step:7.1f} → {edge_mhz + step:7.1f} MHz で "
              f"係数 {lo:.5f} → {hi:.5f}（段差 {hi - lo:+.5f}・{hi / lo:.2f} 倍）")
    d100 = _veg_coeff((edge_mhz - 100) / 1000) - _veg_coeff((edge_mhz + 100) / 1000)
    d10  = _veg_coeff((edge_mhz - 10) / 1000)  - _veg_coeff((edge_mhz + 10) / 1000)
    ratio = abs(d10 / d100) if d100 else float("nan")
    print(f"    刻みを 1/10 にしたときの段差の比 = {ratio:.3f}"
          f"（0.3 未満なら坂・1.0 付近なら崖）")


def measure_edge() -> None:
    print("=" * 72)
    print("① 植生係数の分岐は崖か坂か＝刻みを 1/10 にして測る")
    print("=" * 72)
    print("  ⚠️ **範囲は 1/10 にしない**＝別の区間を見て崖を跨がないため。")
    veg_lo, veg_hi = models.VEG_COEFF_RANGE_GHZ
    _edge(veg_lo * 1000.0, "下側の縁（0.12 f^0.5 ↔ 0.20 f^0.7）")
    _edge(veg_hi * 1000.0, "上側の縁（0.20 f^0.7 ↔ 0.35 f^0.9）")
    print(f"  Field の帯域 {FIELD_FREQ_MHZ:.1f} MHz の係数 = {_veg_coeff(FIELD_FREQ_MHZ / 1000):.5f}")
    print(f"    内側の枝を伸ばした場合      = {_inner_branch_coeff(FIELD_FREQ_MHZ / 1000):.5f}"
          f"（比 {_inner_branch_coeff(FIELD_FREQ_MHZ / 1000) / _veg_coeff(FIELD_FREQ_MHZ / 1000):.2f} 倍）")
    print()


# ============================================================
# ②③ コーパスで外挿ぶん・0 dB に固めたぶんを測る
# ============================================================
class Link:
    """製品の呼び出しをそのまま写した土台（→ [[feedback_synthetic_cases_lie]]）。"""

    def __init__(self, rec: dict) -> None:
        self.id  = rec["id"]
        self.inp = rec["input"]
        elevs = np.array(rec["raw_elevs"], dtype=float)
        self.terrain = models.calculate_terrain_profile(
            elevs, self.inp["lat_tx"], self.inp["lon_tx"],
            self.inp["lat_rx"], self.inp["lon_rx"],
        )

    def prop(self, freq_mhz: float, veg_h: float, rain: float):
        return models.calculate_propagation(
            self.terrain,
            self.inp["h_tx"], self.inp["h_rx"],
            freq_mhz, veg_h,
            models.EARTH_K_STANDARD,
            rain_rate=rain,
        )

    def veg_loss_with_inner_branch(self, freq_mhz: float, veg_h: float) -> float:
        """内側の枝（1〜6 GHz 用）を 0.92 GHz へ伸ばした場合の植生減衰。

        ⚠️ **頭打ち（45 dB）があるので係数の比では出せない**＝張り付いた回線は
        係数を変えても値が動かない（→ [[feedback_synthetic_cases_lie]] の
        「上限に張り付いた対照は何も測っていない」）。⇒ 実際に計算し直す。

        ⚠️ **等価な周波数で呼ぶ手は使えない**＝逆算すると 2.48 GHz になり、
        枝をまたぐ上に F1（幾何）まで変わる。⇒ **枝の境目の定数だけ**を
        一時的に下げ、922 MHz を内側の枝に入れて同じ関数を呼ぶ。
        """
        lo, hi = models.VEG_COEFF_RANGE_GHZ
        elevs = self.terrain.elevs_with_curve
        tx_abs = float(elevs[0])  + self.inp["h_tx"]
        rx_abs = float(elevs[-1]) + self.inp["h_rx"]
        los_vals = self.terrain.los_line(tx_abs, rx_abs)
        f1 = models.fresnel_zone_radii(
            self.terrain.d_km_axis, self.terrain.horiz_dist_km, freq_mhz
        )
        d_m_axis = self.terrain.d_km_axis * 1000
        models.VEG_COEFF_RANGE_GHZ = (0.0, hi)   # 内側の枝を下へ伸ばす
        try:
            return float(models._vegetation_loss(
                elevs + veg_h, veg_h, los_vals, f1, freq_mhz, d_m_axis,
            ))
        finally:
            models.VEG_COEFF_RANGE_GHZ = (lo, hi)


def measure_corpus(links: list[Link]) -> None:
    print("=" * 72)
    print(f"② 植生減衰の外挿ぶん（{FIELD_FREQ_MHZ:.0f} MHz・veg_h={VEG_H_M:.0f} m・コーパス {len(links)} 本）")
    print("=" * 72)
    rows = []
    for lk in links:
        p = lk.prop(FIELD_FREQ_MHZ, VEG_H_M, 0.0)
        inner = lk.veg_loss_with_inner_branch(FIELD_FREQ_MHZ, VEG_H_M)
        if p.veg_loss <= 0.0:
            continue
        rows.append((lk.id, p.veg_loss, inner, inner - p.veg_loss, p.diff_loss))
    if not rows:
        print("  植生減衰が立つ回線が 1 本も無い（判定できない）")
        print()
        return
    rows.sort(key=lambda r: -abs(r[3]))
    print(f"  植生減衰が立つ回線 {len(rows)} / {len(links)} 本")
    print(f"  {'回線':<28} {'いまの枝':>9} {'内側の枝':>9} {'差':>8} {'回折損':>9}")
    for rid, now, inner, d, diff in rows[:10]:
        print(f"  {rid:<28} {now:9.2f} {inner:9.2f} {d:+8.2f} {diff:9.2f}")
    diffs = [r[3] for r in rows]
    capped = sum(1 for r in rows if r[1] >= 44.99)
    print(f"  差の幅 = {min(diffs):+.2f} 〜 {max(diffs):+.2f} dB"
          f"（中央 {sorted(diffs)[len(diffs) // 2]:+.2f} dB）")
    print(f"  ⚠️ 上限 45 dB に張り付いた回線 {capped} 本"
          f"（張り付いている間は差が見えない）")
    print()


def measure_zeroed(links: list[Link]) -> None:
    print("=" * 72)
    print(f"③ 0 dB に固めた 2 項が捨てている量（{FIELD_FREQ_MHZ:.0f} MHz vs {REF_FREQ_MHZ:.0f} MHz）")
    print("=" * 72)
    print("  ⚠️ 920 MHz 側は必ず 0.00 dB（定数がそう決めている）＝")
    print("     見るのは『2.4 GHz なら何 dB 載っていたか』のほう。")
    print(f"  {'回線':<28} {'距離km':>7} {'降雨920':>8} {'降雨2.4G':>9} {'大気920':>8} {'大気2.4G':>9}")
    rain_ref, gas_ref = [], []
    for lk in links:
        p920 = lk.prop(FIELD_FREQ_MHZ, 0.0, RAIN_MM_H)
        p24  = lk.prop(REF_FREQ_MHZ,   0.0, RAIN_MM_H)
        rain_ref.append(p24.rain_loss)
        gas_ref.append(p24.gas_loss)
        if len(rain_ref) <= 6:
            print(f"  {lk.id:<28} {lk.terrain.horiz_dist_km:7.2f} "
                  f"{p920.rain_loss:8.2f} {p24.rain_loss:9.2f} "
                  f"{p920.gas_loss:8.2f} {p24.gas_loss:9.2f}")
    print(f"  降雨（{RAIN_MM_H:.0f} mm/h）: 2.4 GHz なら "
          f"{min(rain_ref):.2f} 〜 {max(rain_ref):.2f} dB / 920 MHz は全本 0.00 dB")
    print(f"  大気         : 2.4 GHz なら "
          f"{min(gas_ref):.3f} 〜 {max(gas_ref):.3f} dB / 920 MHz は全本 0.00 dB")
    print("  🔑 大気は 2.4 GHz でも小さい＝920 MHz で 0 に固めても残差には効かない。")
    print("     降雨は条件次第で効くが、**Field の測定は降雨中に行わない**ので")
    print("     1 回目の実測の残差には入らない（入れるなら条件を記録する側の話）。")
    print()


# ============================================================
# ④ 開示は出るか
# ============================================================
def measure_disclosure() -> None:
    print("=" * 72)
    print("④ 920 MHz で刻印（開示）が出るか＝`scope_notes` をそのまま呼ぶ")
    print("=" * 72)
    cases = [
        ("植生なし・降雨なし（Field の素の測定）", 0.0, 0.0),
        ("植生あり・降雨なし",                     VEG_H_M, 0.0),
        ("植生あり・降雨あり",                     VEG_H_M, RAIN_MM_H),
    ]
    for label, veg_h, rain in cases:
        notes = models.scope_notes(
            FIELD_FREQ_MHZ, rain_rate=rain, veg_h=veg_h, resolution="high",
        )
        want = {"gas_zeroed"}
        if veg_h > 0:
            want.add("veg_extrapolated")
        if rain > 0:
            want.add("rain_zeroed")
        missing = want - set(notes)
        mark = "✅" if not missing else "🔴"
        print(f"  {mark} {label}")
        print(f"      刻印 = {', '.join(notes)}")
        if missing:
            print(f"      🔴 出ていない = {', '.join(sorted(missing))}")
    print()


def main() -> None:
    records = _load()
    links = [Link(r) for r in records]
    print()
    print(f"ゴールデンコーパス {len(links)} 本 / Field の帯域 {FIELD_FREQ_MHZ:.1f} MHz")
    print()
    measure_edge()
    measure_corpus(links)
    measure_zeroed(links)
    measure_disclosure()


if __name__ == "__main__":
    main()
