"""B-032 の候補処方を同じ土俵で並べる探針。

⚠️ 製品コードではない。`b032_deygout_probe.py` が現状を測る側、こちらが処方を試す側。

試した軸（**落ちた案も探索の記録として残す**）:
  endpoint    : "los"（現行＝元の LoS 高度）/ "peak"（古典＝主障害物の頂点高度）
                ⇒ "peak" は平坦面で J(0)≈6.9 dB を毎回足すので平地 227 dB。**失格**。
  nu_basis    : "segment"（現行）/ "full"（全経路の d1·d2 で ν を測る）
                ⇒ "full" は h_obs が区間で変わらず打ち切りが効かない。平地 67 dB。**失格**。
  causebrook  : 副障害物に T = 1 - exp(-L_p/6) を掛ける
                ⇒ 主障害物が深いと T→1 で減衰しない。広い丘 80m で 1020 dB。**失格**。
  max_depth   : 再帰の上限（現行 20 / 古典 Deygout の 1＝主+副 2）
  shadow_gate : 副障害物は「主障害物の頂点と端点を結ぶ弦」を超えなければ数えない
                ⇒ 一体化と併せると `Single` とほぼ同値まで落ちる（Bullington より下）
                ＝多重回折という Deygout の存在理由が消える。**採用しない**。
  one_edge    : **1 つの連続した遮蔽体は 1 枚のナイフエッジ**。範囲の切り方 3 通り:
                "los"     LoS を超える連続区間。⇒ 損失は ν > -0.8 で始まるのに範囲を
                          高さで決めており条件が食い違う（Codex 47 巡目 P2）。
                          閾値の近傍にある地形（hiroshima）が標本数で 39↔124 dB と跳ぶ。
                "nu[:θ]"  ν > θ の連続区間（既定 θ = -0.8 = `_NU_THRESHOLD`）。
                          **"los" は θ=0.0 と同じ**なので、これは連続したつまみ。
                "valley"  谷（極小点）で区切る。⇒ 単体では平坦面で極小が定まらず
                          キャノピーで 478 dB。**失格**。
  separation  : 二値の一体化を滑らかにする案＝副区間の寄与に
                w = clip((ν_sub - ν_valley)/SCALE, 0, 1) を掛ける。
                ⇒ 合成地形では全条件を通ったが、**実地形で失格**（hiroshima 1557 dB）。
                   実地形は細かい凹凸だらけで「単調に下る」判定がすぐ止まり、
                   谷が無数にできて副障害物も無数になる。
                   🔑 **滑らかな合成だけで処方を決めてはいけない。**

✅ **採用 = Kν（one_edge="nu"・θ = -0.8 のみ／陰ゲートも深さ制限も要らない）**

なぜ θ = -0.8 か＝**一体化の境界を「損失が始まるのと同じ物差し」で決める**ので、
境界と損失の有無が一致し、標本数で「繋がる/切れる」が入れ替わらない。
θ を -0.2〜-0.4 に取ると hiroshima が 39↔124 dB と跳ぶ（境界が損失条件とずれる）。

実データ 26 本＝判定 0 本変化／100 dB 超 13 → 2 本／hiroshima の標本数依存は
120〜960 点で 38.82〜39.16（現行は 1860→2325）。多重回折は 6 本で残る。

⚠️ **「Bullington < Ep-Pet < Kν」という順序は一般には成り立たない**
   （2026-08-25・Codex 48 巡目 P1）＝fuji では成り立つが hiroshima では
   Kν が Ep-Pet の 0.56 倍。書けるのは「多くの回線で Ep-Pet の 1.0〜1.7 倍、
   一部では `Single` と同値」まで。**1 行から一般化しない。**
   なお hiroshima が `Single` と同値なのは正しい＝9km 連続の山塊で谷でも
   LoS 差 +31〜42m（F1 半径の 1.3〜1.8 倍上）＝1 枚の巨大な障害物。
   詳細は `b032_terrain_probe.py` で断面を見る。

使い方:
    & "$env:RADIOSIM_PYTHON" experiments/b032_variants_probe.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import models  # noqa: E402

from b032_deygout_probe import (  # noqa: E402
    DIST_KM, FREQ_MHZ, H_RX, H_TX,
    _profile, flat, narrow_peak, two_peaks, wide_hill,
)


def make_variant(endpoint: str, max_depth: int, causebrook: bool, nu_basis: str = "segment", shadow_gate: str = "off", one_edge: str = "off",
                 separation: float = 0.0):
    """`models._deygout_loss` と差し替え可能な実装を返す。

    nu_basis: "segment"（現行＝区間ごとの d1·d2）/ "full"（全経路の d1·d2）
      "full" は「区間を割っても同じ障害物の ν が増えない」＝発散の機構そのものを断つ。
    """
    full_span_holder: list[float] = []

    def loss_fn(obs_surface, d_m_axis, tx_abs, rx_abs, lam, depth=0):
        N = len(obs_surface)
        if depth == 0:
            full_span_holder.clear()
            full_span_holder.append(float(d_m_axis[-1]) - float(d_m_axis[0]))
        if N < 3 or depth > max_depth:
            return 0.0

        d_start = float(d_m_axis[0])
        d_end   = float(d_m_axis[-1])
        span_m  = d_end - d_start
        if span_m < models._MIN_SEGMENT_M:
            return 0.0

        los    = np.linspace(tx_abs, rx_abs, N)
        if nu_basis == "full":
            full = full_span_holder[0]
            d1_arr = np.maximum(d_m_axis, 1.0)
            d2_arr = np.maximum(full - d_m_axis, 1.0)
        else:
            d_rel  = d_m_axis - d_start
            d1_arr = np.maximum(d_rel, 1.0)
            d2_arr = np.maximum(span_m - d_rel, 1.0)

        h_obs = obs_surface - los
        denom = np.maximum(lam * d1_arr * d2_arr / (d1_arr + d2_arr), 1e-9)
        v_arr = np.nan_to_num(h_obs * np.sqrt(2.0 / denom))

        inner_v    = v_arr[1:-1]
        peak_idx   = int(np.argmax(inner_v)) + 1
        v_peak     = float(v_arr[peak_idx])
        if v_peak <= models._NU_THRESHOLD:
            return 0.0

        loss = models._diffraction_loss_fk(v_peak)

        # 再帰の端点
        if endpoint == "peak":
            edge = float(obs_surface[peak_idx])
        else:
            edge = float(los[peak_idx])

        # ── 副障害物の採否ゲート（shadow）─────────────────────────
        # 古典 Deygout の幾何＝副障害物は「主障害物の陰」にあれば数えない。
        # 端点を主障害物の頂点に取った直線を超える点が無ければ再帰しない。
        peak_h = float(obs_surface[peak_idx])

        d_peak = float(d_m_axis[peak_idx])

        def _emerges(seg_obs, seg_d, a: float, b: float, side: str) -> bool:
            """副障害物が「主障害物の陰」から出ているか。

            ⚠️ 弦は **主峰の実際の距離 `d_peak`** へ向けて張る（2026-08-25・Codex 47 巡目 P1）。
            区間の端（`d[lo]` / `d[hi]`）に主峰高を置くと弦が急になり、独立した副峰を
            棄却しすぎる。`one_edge=False` のときは `lo==hi==peak_idx` なので差は出ない。
            """
            if shadow_gate == "off":
                return True
            m = len(seg_obs)
            if m < 3:
                return False
            x = seg_d[1:-1]
            if side == "left":
                span = d_peak - float(seg_d[0])
                gate = a + (peak_h - a) * (x - float(seg_d[0])) / max(span, 1e-9)
            else:
                span = float(seg_d[-1]) - d_peak
                gate = peak_h + (b - peak_h) * (x - d_peak) / max(span, 1e-9)
            h = seg_obs[1:-1] - gate
            if shadow_gate == "height":
                return bool(np.max(h) > 0.0)
            # "nu": 弦からのはみ出しを全経路のフレネル半径で正規化して判定する
            full = full_span_holder[0]
            g1 = np.maximum(x, 1.0)
            g2 = np.maximum(full - x, 1.0)
            gd = np.maximum(lam * g1 * g2 / (g1 + g2), 1e-9)
            return bool(np.max(np.nan_to_num(h * np.sqrt(2.0 / gd))) > models._NU_THRESHOLD)

        # ── K: 1 つの連続した遮蔽体は 1 枚のナイフエッジ ────────────
        # 主障害物が属する「LoS を超える連続区間」ごと再帰から外す。
        lo, hi = peak_idx, peak_idx
        if one_edge == "los":
            # 当初案＝LoS を超える連続区間で切る。
            # ⚠️ 損失は ν > -0.8 で始まるのに範囲は LoS 超過で決めており、条件が
            #    食い違う（2026-08-25・Codex 47 巡目 P2）＝F1 に食い込むだけの
            #    広い丘を一体として畳めない。
            over = obs_surface > los
            while lo > 0 and over[lo - 1]:
                lo -= 1
            while hi < N - 1 and over[hi + 1]:
                hi += 1
        elif one_edge.startswith("nu"):
            # ✅ 採用。**"los" は ν > 0 と同じ**なので、この閾値は
            # 「LoS 超過だけを 1 体と見る（0.0）」から「F1 の 57% まで 1 体と見る
            # （-0.8＝損失の開始条件と同じ物差し）」までの**連続したつまみ**。
            # θ = -0.8 だけが標本数に安定する（境界と損失の有無が一致するため）。
            thr = float(one_edge.split(":")[1]) if ":" in one_edge else models._NU_THRESHOLD
            over = v_arr > thr
            while lo > 0 and over[lo - 1]:
                lo -= 1
            while hi < N - 1 and over[hi + 1]:
                hi += 1
        elif one_edge == "valley":
            # ⛔ 失格（単体では平坦面で極小が定まらずキャノピー 478 dB／
            #    separation と組んでも実地形で発散）。探索の記録として残す。
            while lo > 0 and obs_surface[lo - 1] <= obs_surface[lo]:
                lo -= 1
            while hi < N - 1 and obs_surface[hi + 1] <= obs_surface[hi]:
                hi += 1

        left_obs,  left_d  = obs_surface[:lo + 1], d_m_axis[:lo + 1]
        right_obs, right_d = obs_surface[hi:],     d_m_axis[hi:]
        left_edge  = float(los[lo])
        right_edge = float(los[hi])
        if one_edge == "off":
            left_edge = right_edge = edge

        # ── S: 分離度による連続的な重み（二値の一体化を滑らかにしたもの）────
        # 副区間の主障害物候補を先に見つけ、**それと主障害物の間の谷の深さ**で
        # 寄与を減衰させる。`sep = ν_sub - ν_valley`＝谷が副障害物より F1 の
        # 何割ぶん低いか。二値の閾値と違い、境界の近傍で標本数によって
        # 39 dB ↔ 124 dB のように跳ばない（2026-08-25）。
        def _sep_weight(side: str) -> float:
            if separation <= 0.0:
                return 1.0
            # ⚠️ 「区間内の最大 ν」を副障害物にしてはいけない＝主峰から連続的に
            #    下るので**必ず主峰の隣**が選ばれ、谷が無く重みが常に 0 になる。
            #    ⇒ **主峰から下り続けた先の谷を越えてから**、その先の極大を探す。
            j = peak_idx
            if side == "left":
                while j > 0 and v_arr[j - 1] <= v_arr[j]:
                    j -= 1
                rest = v_arr[1:j]           # 谷より手前（端点は除く）
            else:
                while j < N - 1 and v_arr[j + 1] <= v_arr[j]:
                    j += 1
                rest = v_arr[j + 1:-1]      # 谷より先（端点は除く）
            if len(rest) == 0:
                return 0.0                  # 谷の向こうに障害物が無い＝同じ山体
            sep = float(np.max(rest)) - float(v_arr[j])
            return float(min(max(sep / separation, 0.0), 1.0))

        sub = 0.0
        if lo >= 2 and _emerges(left_obs, left_d, tx_abs, peak_h, "left"):
            sub += _sep_weight("left") * loss_fn(
                left_obs, left_d, tx_abs, left_edge, lam, depth + 1,
            )
        if hi <= N - 3 and _emerges(right_obs, right_d, peak_h, rx_abs, "right"):
            sub += _sep_weight("right") * loss_fn(
                right_obs, right_d, right_edge, rx_abs, lam, depth + 1,
            )

        if causebrook:
            sub *= 1.0 - math.exp(-loss / 6.0)

        return loss + sub

    return loss_fn


VARIANTS: dict[str, object] = {
    "現行":       None,   # models._deygout_loss をそのまま
    "G1:陰h+深1": make_variant("los", 1,  False, shadow_gate="height"),
    "✅Kν(θ=-0.8)": make_variant("los", 20, False, one_edge="nu:-0.8"),
    "ν>0.0(=LoS)": make_variant("los", 20, False, one_edge="los"),
    "ν>-0.4":     make_variant("los", 20, False, one_edge="nu:-0.4"),
    "VS:谷+分離0.8": make_variant("los", 20, False, one_edge="valley", separation=0.8),
}


def run(elevs, veg_h=0.0, dist_km=DIST_KM, variant=None) -> float:
    terrain = _profile(elevs, dist_km)
    orig = models._deygout_loss
    if variant is not None:
        models._deygout_loss = variant
    try:
        return models.calculate_propagation(
            terrain, H_TX, H_RX, FREQ_MHZ, veg_h, 4 / 3, diff_method="deygout"
        ).diff_loss
    finally:
        models._deygout_loss = orig


def single(elevs, veg_h=0.0, dist_km=DIST_KM) -> float:
    terrain = _profile(elevs, dist_km)
    return models.calculate_propagation(
        terrain, H_TX, H_RX, FREQ_MHZ, veg_h, 4 / 3, diff_method="single"
    ).diff_loss


CASES: list[tuple[str, np.ndarray, float, float]] = [
    ("平地 0m",       flat(),            0.0,  DIST_KM),
    ("広い丘 5m",     wide_hill(5),      0.0,  DIST_KM),
    ("広い丘 25m",    wide_hill(24.999), 0.0,  DIST_KM),
    ("広い丘 40m",    wide_hill(40),     0.0,  DIST_KM),
    ("広い丘 80m",    wide_hill(80),     0.0,  DIST_KM),
    ("細い峰 25m",    narrow_peak(25),   0.0,  DIST_KM),
    ("細い峰 60m",    narrow_peak(60),   0.0,  DIST_KM),
    ("2 つの峰 40m",  two_peaks(40),     0.0,  DIST_KM),
    ("2 つの峰 80m",  two_peaks(80),     0.0,  DIST_KM),
    ("キャノピー2km", np.zeros(200),     31.0, 2.0),
]


def main() -> None:
    names = list(VARIANTS)
    print(f"条件: {DIST_KM}km / {FREQ_MHZ}MHz / TX {H_TX}m / RX {H_RX}m / n=200")
    print("CB = Causebrook 減衰（副障害物 × (1 - exp(-L_p/6))）")
    print()
    head = f"{'形':<14}{'Single':>9}" + "".join(f"{n:>15}" for n in names)
    print(head)
    print("-" * len(head))
    for label, elevs, veg, dist in CASES:
        s = single(elevs, veg, dist)
        row = f"{label:<14}{s:>9.2f}"
        for n in names:
            row += f"{run(elevs, veg, dist, VARIANTS[n]):>15.2f}"
        print(row)

    # 標本数依存（深い遮蔽の顔）
    print()
    print("標本数依存（2km 平地・veg_h=31）")
    head = f"{'n':>5}{'Single':>9}" + "".join(f"{n:>15}" for n in names)
    print(head)
    print("-" * len(head))
    for nn in (51, 101, 201, 401, 801):
        e = np.zeros(nn)
        row = f"{nn:>5}{single(e, 31.0, 2.0):>9.2f}"
        for n in names:
            row += f"{run(e, 31.0, 2.0, VARIANTS[n]):>15.2f}"
        print(row)

    print()
    print("標本数依存（広い丘 25m・10km）")
    print(head)
    print("-" * len(head))
    for nn in (51, 101, 201, 401, 801):
        e = wide_hill(24.999, nn)
        row = f"{nn:>5}{single(e):>9.2f}"
        for n in names:
            row += f"{run(e, 0.0, DIST_KM, VARIANTS[n]):>15.2f}"
        print(row)


if __name__ == "__main__":
    main()
