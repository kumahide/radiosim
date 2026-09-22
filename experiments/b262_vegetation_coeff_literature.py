"""B-262 の裏取り＝植生係数の 3 分岐を、一次情報の実測点と突き合わせる探針。

**なぜ測るか**＝B-262 の対応案 1（⛔ まず裏取り）。`_vegetation_loss` の係数は
1 GHz と 6 GHz で分岐が替わり、境目で跳ぶ（[[B-262]] の ① で崖と確定済み）。
**どの枝が正しいかはコーパスからは出ない**ので、外から数字を持ってくる。

ここが持っている一次情報は **1 つだけ**＝ITU-R P.833-10（2021-09）§2.1 の
TABLE 1「mixed coniferous-deciduous vegetation (mixed forest) near
St. Petersburg (Russia)」の実測値。原文の数字をそのまま写す（→ [[feedback_quote_the_source]]）:

    Frequency (MHz)   105.9(H)  466.475(S)  949.0(S)  1852.2(S)  2117.5(S)
    γ (dB/m)             0.04       0.12      0.17      0.30       0.34

🔑 **この 5 点が 1 GHz を跨いでいる**＝製品が崖を置いている境目の
**両側に実測点がある**ので、「境目で跳ぶのか」を外から判定できる。

⚠️ **比べ方の前提**（ここを外すと数字が嘘をつく → [[feedback_synthetic_cases_lie]]）:
  - 原文の γ は「very short vegetative paths」の単位長あたりの減衰 [dB/m]。
    製品の `coeff` も [dB/m] だが、掛ける長さは **Fresnel 半径で重みを付けた
    有効植生長**であって幾何長そのものではない。⇒ **絶対値が一致する義理は無い。**
  - ⇒ 判定の主役は**形（周波数依存）**のほう＝境目で跳ぶか、指数はいくつか。
    絶対値は「桁が合うか」までしか言わない。
  - 105.9 MHz だけ水平偏波、残りは斜め。原文は「Below about 1 GHz there is a
    tendency for vertically polarized signals to experience higher attenuation
    than horizontally」とも言う＝**偏波で散る量が、崖の幅と同じ桁にある**。

⛔ **この探針は上側の縁（6 GHz）を判定しない**＝手元の 5 点は 2.1 GHz で終わる。
   無い数字で結論を書かない（→ [[feedback_dont_count_other_systems]]）。

⛔ 合成地形は使わない＝`tests/data/golden_links.json` の凍結標高で回す。ネットワーク不要。

使い方:
    & "$env:RADIOSIM_PYTHON" experiments/b262_vegetation_coeff_literature.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import models  # noqa: E402
from phase0_920mhz_model_domain import (  # noqa: E402
    FIELD_FREQ_MHZ,
    VEG_H_M,
    Link,
    _load,
    _veg_coeff,
)

for _stream in (sys.stdout, sys.stderr):
    try:                                    # 既定のコンソールは cp932（B-261）
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    except Exception:
        pass

#: ITU-R P.833-10（2021-09）§2.1 TABLE 1 の実測値。(周波数 MHz, γ dB/m, 偏波)
#: ⚠️ **写しであって当てはめではない**＝ここを触ったら出典も一緒に直す。
P833_TABLE1: tuple[tuple[float, float, str], ...] = (
    (105.9,  0.04, "水平"),
    (466.475, 0.12, "斜め"),
    (949.0,  0.17, "斜め"),
    (1852.2, 0.30, "斜め"),
    (2117.5, 0.34, "斜め"),
)

#: 出典（帳票にも探針にも同じ字で出す）。
P833_CITE = "ITU-R P.833-10 (09/2021) §2.1 TABLE 1"

#: 製品の頭打ち（`_vegetation_loss` の末尾）。
VEG_CAP_DB = 45.0


def _lower_branch(freq_ghz: float) -> float:
    """製品の**下側の枝**（1 GHz 未満用）。"""
    return 0.12 * (freq_ghz ** 0.5)


def _inner_branch(freq_ghz: float) -> float:
    """製品の**内側の枝**（1〜6 GHz 用）。"""
    return 0.20 * (freq_ghz ** 0.7)


def _upper_branch(freq_ghz: float) -> float:
    """製品の**上側の枝**（6 GHz 超用）。"""
    return 0.35 * (freq_ghz ** 0.9)


# ============================================================
# ① 実測点は境目で跳んでいるか
# ============================================================
def measure_anchor_continuity() -> tuple[float, float]:
    """TABLE 1 の 5 点に単一のべき乗則を当てはめ、残差を返す。"""
    print("=" * 76)
    print(f"① 一次情報の実測点は 1 GHz で跳んでいるか（出典: {P833_CITE}）")
    print("=" * 76)
    f_ghz = np.array([f / 1000.0 for f, _, _ in P833_TABLE1])
    gamma = np.array([g for _, g, _ in P833_TABLE1])
    b, log_a = np.polyfit(np.log(f_ghz), np.log(gamma), 1)
    a = float(np.exp(log_a))
    fitted = a * f_ghz ** b
    print("  単一のべき乗則を当てはめる（log-log の最小二乗・5 点まとめて 1 本）")
    print(f"    γ = {a:.5f} × f[GHz]^{b:.4f}")
    print()
    print(f"  {'f[MHz]':>9} {'偏波':>4} {'実測γ':>8} {'当てはめ':>9} {'比':>7} "
          f"{'製品(下枝)':>10} {'製品(内枝)':>10}")
    for (f_mhz, g, pol), fit in zip(P833_TABLE1, fitted):
        fg = f_mhz / 1000.0
        print(f"  {f_mhz:9.1f} {pol:>4} {g:8.3f} {fit:9.3f} {fit / g:7.2f} "
              f"{_lower_branch(fg):10.3f} {_inner_branch(fg):10.3f}")
    resid = np.abs(fitted / gamma - 1.0)
    print(f"  当てはめの誤差 = {resid.min() * 100:.1f} 〜 {resid.max() * 100:.1f}%"
          f"（5 点とも 1 本の直線に載るか）")
    print()
    print("  🔑 **判定**＝5 点は 1 GHz の両側にまたがっているのに、"
          "**1 本のべき乗則で揃う**。")
    print("     ⇒ この一次情報は「1 GHz で係数が跳ぶ」を**支持していない**。")
    print("     ⛔ ただし『跳ばない』を証明したわけでもない＝支持が無い、が正確。")
    print()
    return a, float(b)


# ============================================================
# ② 製品の 3 枝はどれが実測点に近いか
# ============================================================
def measure_branch_fit() -> None:
    print("=" * 76)
    print("② 製品の枝を実測点に当てる（形＝指数・絶対値＝倍率）")
    print("=" * 76)
    f_ghz = np.array([f / 1000.0 for f, _, _ in P833_TABLE1])
    gamma = np.array([g for _, g, _ in P833_TABLE1])
    branches = (
        ("下側の枝 0.12 f^0.5", _lower_branch, 0.5),
        ("内側の枝 0.20 f^0.7", _inner_branch, 0.7),
        ("上側の枝 0.35 f^0.9", _upper_branch, 0.9),
    )
    b_fit = float(np.polyfit(np.log(f_ghz), np.log(gamma), 1)[0])
    print(f"  実測点の指数 = {b_fit:.4f}")
    print(f"  {'枝':<22} {'指数':>6} {'指数の差':>9} {'γ比の幅':>16} {'ばらつき':>9}")
    for label, fn, expo in branches:
        ratio = np.array([fn(fg) for fg in f_ghz]) / gamma
        print(f"  {label:<22} {expo:6.2f} {expo - b_fit:+9.4f} "
              f"{ratio.min():7.2f} 〜 {ratio.max():5.2f} "
              f"{ratio.max() / ratio.min():9.2f}")
    print("  ⚠️ 『γ比の幅』が 1.0 から離れるのは絶対値のずれ＝**重み付き長さで掛ける**")
    print("     製品では一致しなくてよい。**見るのは右端（ばらつき）**＝1.0 に近いほど")
    print("     周波数依存の形が実測と揃っている（倍率 1 つで乗る）。")
    print()
    print(f"  ◆ Field の帯域 {FIELD_FREQ_MHZ:.0f} MHz で 3 者を比べる")
    fg = FIELD_FREQ_MHZ / 1000.0
    now, inner = _veg_coeff(fg), _inner_branch(fg)
    near = min(P833_TABLE1, key=lambda r: abs(r[0] - FIELD_FREQ_MHZ))
    print(f"    製品がいま使う係数（下側の枝） = {now:.5f}")
    print(f"    内側の枝を伸ばした係数         = {inner:.5f}（{inner / now:.2f} 倍）")
    print(f"    最寄りの実測点 {near[0]:.1f} MHz    = {near[1]:.5f}"
          f"（実測は内側の枝の {near[1] / inner:.2f} 倍 / 下側の枝の {near[1] / now:.2f} 倍）")
    print()


# ============================================================
# ③ コーパスで dB に直す
# ============================================================
def _veg_loss_with_coeff(lk: Link, freq_mhz: float, veg_h: float,
                         coeff_new: float) -> tuple[float, bool]:
    """係数だけを差し替えたときの植生減衰 [dB] と、頭打ちかどうか。

    製品は `min(有効植生長 × coeff, 45)`。⇒ **頭打ちでない回線なら有効植生長は
    `veg_loss / coeff` で厳密に戻せる**。頭打ちの回線は長さが戻せないが、
    **係数を増やす向き**なら頭打ちのまま＝45 dB と確定する（減らす向きには使えない）。
    ⚠️ 比で外挿しないのは [[feedback_synthetic_cases_lie]] の
    「上限に張り付いた対照は何も測っていない」を踏まないため。
    """
    p = lk.prop(freq_mhz, veg_h, 0.0)
    coeff_now = _veg_coeff(freq_mhz / 1000.0)
    if p.veg_loss >= VEG_CAP_DB - 0.01:
        if coeff_new >= coeff_now:
            return VEG_CAP_DB, True
        raise ValueError("頭打ちの回線は係数を下げる向きには戻せない")
    length = p.veg_loss / coeff_now
    return min(length * coeff_new, VEG_CAP_DB), False


def measure_corpus(links: list[Link], a: float, b: float) -> None:
    print("=" * 76)
    print(f"③ 枝の選び方が帳票の dB をどれだけ動かすか"
          f"（{FIELD_FREQ_MHZ:.0f} MHz・veg_h={VEG_H_M:.0f} m・コーパス {len(links)} 本）")
    print("=" * 76)
    fg = FIELD_FREQ_MHZ / 1000.0
    coeff_inner = _inner_branch(fg)
    #: 一次情報に当てはめたべき乗則を、**製品の倍率へ揃えてから**使う。
    #: ⚠️ 絶対値は比べられない（重み付き長さ）ので、**内側の枝の 1 GHz での値**で
    #: 正規化し、「形だけ実測に差し替えたらどうなるか」を見る。
    scale = _inner_branch(1.0) / (a * 1.0 ** b)
    coeff_fit = scale * a * fg ** b
    print(f"  いまの係数(下枝) = {_veg_coeff(fg):.5f} / 内枝 = {coeff_inner:.5f} / "
          f"実測の形 = {coeff_fit:.5f}")
    print(f"  （実測の形は 1 GHz で内枝と同じ値になるよう倍率 {scale:.3f} を掛けた）")
    print()
    rows, capped = [], 0
    for lk in links:
        p = lk.prop(FIELD_FREQ_MHZ, VEG_H_M, 0.0)
        if p.veg_loss <= 0.0:
            continue
        v_inner, cap = _veg_loss_with_coeff(lk, FIELD_FREQ_MHZ, VEG_H_M, coeff_inner)
        v_fit, _ = _veg_loss_with_coeff(lk, FIELD_FREQ_MHZ, VEG_H_M, coeff_fit)
        capped += int(cap)
        rows.append((lk.id, p.veg_loss, v_inner, v_fit))
    if not rows:
        print("  植生減衰が立つ回線が 1 本も無い（判定できない）")
        print()
        return
    rows.sort(key=lambda r: -(r[2] - r[1]))
    print(f"  {'回線':<28} {'いまの枝':>9} {'内側の枝':>9} {'実測の形':>9} {'最大差':>8}")
    for rid, now, inner, fit in rows[:10]:
        span = max(now, inner, fit) - min(now, inner, fit)
        print(f"  {rid:<28} {now:9.2f} {inner:9.2f} {fit:9.2f} {span:8.2f}")
    spans = [max(r[1:]) - min(r[1:]) for r in rows]
    print(f"  植生減衰が立つ回線 {len(rows)} / {len(links)} 本")
    print(f"  3 者の開き = {min(spans):.2f} 〜 {max(spans):.2f} dB"
          f"（中央 {sorted(spans)[len(spans) // 2]:.2f} dB）")
    print(f"  ⚠️ 上限 {VEG_CAP_DB:.0f} dB に張り付いた回線 {capped} 本"
          f"（張り付いている間は差が見えない）")
    print()


# ============================================================
# ④ 上側の縁（6 GHz）について言えること
# ============================================================
def report_upper_edge() -> None:
    print("=" * 76)
    print("④ 上側の縁（6 GHz）＝手元の一次情報で判定できるか")
    print("=" * 76)
    hi_mhz = max(f for f, _, _ in P833_TABLE1)
    veg_lo, veg_hi = models.VEG_COEFF_RANGE_GHZ
    print(f"  手元の実測点の上端 = {hi_mhz:.1f} MHz / 判定したい縁 = {veg_hi * 1000:.0f} MHz")
    print("  ⇒ **判定できない**（実測点が縁に届いていない）。")
    print("  ⛔ 無い数字で結論を書かない＝上側の縁は『未判定』のまま置く。")
    print(f"  🔑 効く場面も限られる＝Field の 1 回目は {FIELD_FREQ_MHZ:.0f} MHz で、")
    print(f"     下側の縁（{veg_lo * 1000:.0f} MHz）の外側にしか当たらない。")
    print()


def main() -> None:
    links = [Link(r) for r in _load()]
    print()
    print(f"ゴールデンコーパス {len(links)} 本 / 出典 {P833_CITE}")
    print()
    a, b = measure_anchor_continuity()
    measure_branch_fit()
    measure_corpus(links, a, b)
    report_upper_edge()


if __name__ == "__main__":
    main()
