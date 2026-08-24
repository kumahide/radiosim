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
                "los"    LoS を超える連続区間 ⇒ 損失は ν > -0.8 で始まるのに範囲を
                         高さで決めており条件が食い違う（Codex 47 巡目 P2）。
                "nu"     ν > -0.8 の連続区間＝**損失の開始条件と同じ物差し**。✅
                "valley" 谷（極小点）で区切る ⇒ 完全に平坦な面では極小が定まらず
                         キャノピーで 478 dB。**失格**。

✅ **採用候補 = Kν（one_edge="nu" のみ・陰ゲートなし）**
   実データ 26 本＝判定 0 本変化／100 dB 超 13 → 2 本／標本数依存は最大 +18%
   （現行は +630%）。Bullington < Epstein-Peterson < Kν の順序が保たれる
   ＝Deygout は Ep-Pet より上に出る手法なので、性格として正しい側に居る。

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


def make_variant(endpoint: str, max_depth: int, causebrook: bool, nu_basis: str = "segment", shadow_gate: str = "off", one_edge: str = "off"):
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
        elif one_edge == "nu":
            # 損失の開始条件と揃えた案。⛔ **行き過ぎ**＝ν > -0.8 は緩いので
            #    2 つの峰の間の谷まで 1 体に畳み、多重回折が消えて `Single` と
            #    同値になる（＝Deygout の存在理由が無くなる）。**失格**。
            over = v_arr > models._NU_THRESHOLD
            while lo > 0 and over[lo - 1]:
                lo -= 1
            while hi < N - 1 and over[hi + 1]:
                hi += 1
        elif one_edge == "valley":
            # ✅ 採用案＝**同じ山体は谷（極小点）で区切る**。主峰から下り続ける
            #    限り同じ障害物、上がり始めたらそこから先は別の障害物。
            #    条件の食い違い（P2）は「範囲を高さで決めない」ことで解ける。
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

        sub = 0.0
        if lo >= 2 and _emerges(left_obs, left_d, tx_abs, peak_h, "left"):
            sub += loss_fn(
                left_obs, left_d, tx_abs, left_edge, lam, depth + 1,
            )
        if hi <= N - 3 and _emerges(right_obs, right_d, peak_h, rx_abs, "right"):
            sub += loss_fn(
                right_obs, right_d, right_edge, rx_abs, lam, depth + 1,
            )

        if causebrook:
            sub *= 1.0 - math.exp(-loss / 6.0)

        return loss + sub

    return loss_fn


VARIANTS: dict[str, object] = {
    "現行":       None,   # models._deygout_loss をそのまま
    "G1:陰h+深1": make_variant("los", 1,  False, shadow_gate="height"),
    "K:LoS体":    make_variant("los", 20, False, one_edge="los"),
    "Kν:ν体":     make_variant("los", 20, False, one_edge="nu"),
    "V:谷体":     make_variant("los", 20, False, one_edge="valley"),
    "VG:谷+陰h":  make_variant("los", 20, False, shadow_gate="height", one_edge="valley"),
    "VG1:VG+深1": make_variant("los", 1,  False, shadow_gate="height", one_edge="valley"),
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
