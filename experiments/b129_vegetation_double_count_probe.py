"""植生が「回折の障害物面」と「植生減衰」の両方に入っているかを数える探針（B-129）。

⚠️ 製品コードではない。`tests/data/golden_links.json` は標高配列を凍結しているので、
**ネットワークに触らず**実経路 26 本で再計算できる（`b125_diffraction_residual_probe.py`
と同じ土俵）。⛔ **合成地形は使わない**＝植生は「一律 `veg_h` を経路全体へ」という
仮定なので、合成条件は自分の仮説をそのまま映す（→ [[feedback_synthetic_cases_lie]]）。

🔁 **これは測定 2 巡目**。1 巡目（2026-08-26）は [[B-130]] の不連続に掃引が汚染されて
いて判定できなかった（`veg_h` を足したのに回折損が 84.6 dB *減る* 回線があった）。
B-130 が Bullington へ替わって連続になった（比 0.17）ので、掃引が成り立つ。

測るのは 5 つ:

  ① **汚染が消えたか**＝1 巡目で崖だった 2 本の同じ刻みを測り直す。
     ここが直っていなければ、以下は全部無効。
  ② **増分の並置**＝`veg_h` を振って `diff_loss` と `veg_loss` の増分を並べる
     （台帳の対応方針 1 そのもの）。⚠️ **45 dB の頭打ちを避けて刻む**＝
     1 巡目は 26 本中 14 本が `veg_h=10m` で既に張り付いて増分が見えなかった。
  ③ **`veg_loss` が立つ範囲**＝どの標本が植生減衰を生んでいるか。
  ④ **形の違い（本題）**＝**回折は幾何（何点で決まるか）／吸収は長さ（何 m か）**。
     Bullington の回折損は**接線を決める 1〜2 標本だけ**で決まるので、
     「その窓だけ植生」と「その窓以外だけ植生」に割れば、2 つの項が
     **同じ面のどこを見ているか**が分離できる。
  ⑤ **長さ比例の確認**＝幾何を固定したまま植生の長さだけ伸ばす。
     `veg_loss` が長さに比例し `diff_loss` が動かなければ、**別の物理**。

使い方:
    & "$env:RADIOSIM_PYTHON" experiments/b129_vegetation_double_count_probe.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import diffraction, models  # noqa: E402

GOLDEN = ROOT / "tests" / "data" / "golden_links.json"

#: 頭打ち（45 dB）に触らせないための刻み。1 巡目の 0/5/10/20/30 は粗すぎた。
VEG_STEPS_M: tuple[float, ...] = (0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 30.0)

VEG_CAP_DB = 45.0


# ============================================================
# 土台
# ============================================================
def _load() -> list[dict]:
    doc = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return doc["links"] if isinstance(doc, dict) else doc


class Link:
    """1 回線ぶんの、製品と同じ前処理を済ませた土台。

    ⚠️ **製品の呼び出しと引数を突き合わせてある**＝`calculate_propagation` が
    `elevs_with_curve` / `los_vals` / `f1` をどう作るかをそのまま写した
    （→ [[feedback_synthetic_cases_lie]] の「探針は製品の呼び出しを突き合わせる」）。
    """

    def __init__(self, rec: dict) -> None:
        self.id  = rec["id"]
        inp      = rec["input"]
        self.inp = inp
        elevs    = np.array(rec["raw_elevs"], dtype=float)
        self.terrain = models.calculate_terrain_profile(
            elevs, inp["lat_tx"], inp["lon_tx"], inp["lat_rx"], inp["lon_rx"],
        )
        self.elevs  = self.terrain.elevs_with_curve
        self.n      = self.terrain.num_samples
        self.tx_abs = float(self.elevs[0])  + inp["h_tx"]
        self.rx_abs = float(self.elevs[-1]) + inp["h_rx"]
        self.los    = np.linspace(self.tx_abs, self.rx_abs, self.n)
        self.d_m    = self.terrain.d_km_axis * 1000
        self.lam    = 299792458 / (inp["freq_mhz"] * 1e6)
        self.f1     = models.fresnel_zone_radii(
            self.terrain.d_km_axis, self.terrain.horiz_dist_km, inp["freq_mhz"]
        )
        self.spacing = (self.terrain.horiz_dist_km * 1000) / max(self.n - 1, 1)

    # -- 2 つの項を、任意の植生マスクで別々に出す ------------------
    def diff_loss(self, veg_h: float, mask: np.ndarray | None = None) -> float:
        return float(diffraction._multi_obstacle_loss(
            self._surface(veg_h, mask), self.d_m, self.tx_abs, self.rx_abs, self.lam,
        ))

    def veg_loss(self, veg_h: float, mask: np.ndarray | None = None) -> float:
        return float(models._vegetation_loss(
            self._surface(veg_h, mask), veg_h, self.los, self.f1,
            self.inp["freq_mhz"], self.terrain.horiz_dist_km, self.n,
        ))

    def _surface(self, veg_h: float, mask: np.ndarray | None) -> np.ndarray:
        if mask is None:
            return self.elevs + veg_h
        return self.elevs + veg_h * mask

    # -- Bullington が実際に見ている標本 ---------------------------
    def governing_idx(self, veg_h: float) -> tuple[int, ...]:
        """回折損を決めている標本の添字（接線を決める点）。

        `_bullington_loss` の argmax をそのまま写す＝**枝も同じ**
        （LoS を切らない側は最大 ν の 1 点、切る側は TX/RX 両側の 2 点）。
        """
        obs   = self._surface(veg_h, None)
        inner = slice(1, -1)
        total = float(self.d_m[-1] - self.d_m[0])
        d1 = np.maximum(self.d_m[inner] - self.d_m[0], 1.0)
        d2 = np.maximum(self.d_m[-1] - self.d_m[inner], 1.0)
        los_slope = (self.rx_abs - self.tx_abs) / total
        slope_tx  = (obs[inner] - self.tx_abs) / d1
        if float(np.max(slope_tx)) <= los_slope:
            los = self.tx_abs + los_slope * (self.d_m[inner] - self.d_m[0])
            nu  = (obs[inner] - los) * np.sqrt(
                2.0 / np.maximum(self.lam * d1 * d2 / (d1 + d2), 1e-9))
            return (int(np.argmax(np.nan_to_num(nu))) + 1,)
        slope_rx = (obs[inner] - self.rx_abs) / d2
        return (int(np.argmax(slope_tx)) + 1, int(np.argmax(slope_rx)) + 1)

    def intruding(self, veg_h: float) -> np.ndarray:
        """`veg_loss` が実際に積んでいる標本（重み > 0）。"""
        return np.clip(
            np.maximum(0.0, self._surface(veg_h, None) - self.los)
            / np.maximum(self.f1, 1e-6), 0.0, 1.0)


def _mask(n: int, idx) -> np.ndarray:
    m = np.zeros(n)
    m[list(idx)] = 1.0
    return m


# ============================================================
# ① 掃引が成り立つか（1 巡目の崖を測り直す）
# ============================================================
#: 1 巡目（旧 Deygout）で「植生を足したのに回折損が減った」2 本と、その刻み。
CONTAMINATED = (("fukuoka_suburb_2400", 0.0, 1.0, -31.3),
                ("hiroshima_kure_ridge", 10.0, 11.0, -84.6))


def _report_sweep_is_valid(links: dict[str, Link]) -> bool:
    print("=== ① 掃引が成り立つか（1 巡目で崖だった刻みを測り直す）===")
    ok = True
    for lid, lo, hi, was in CONTAMINATED:
        lk = links[lid]
        d_lo, d_hi = lk.diff_loss(lo), lk.diff_loss(hi)
        now = d_hi - d_lo
        bad = now < -0.5
        ok &= not bad
        print(f"  {lid:24s} veg_h {lo:g}->{hi:g}m  回折損 {d_lo:7.2f} -> {d_hi:7.2f} "
              f"= {now:+7.2f} dB  （1 巡目 {was:+.1f} dB）{'  [!] まだ減る' if bad else ''}")
    print(f"  => {'掃引は成り立つ（植生を足して回折損が減る回線は無い）' if ok else '⛔ 無効'}")
    return ok


# ============================================================
# ② 増分の並置（台帳の対応方針 1）
# ============================================================
def _report_increments(links: dict[str, Link]) -> list[str]:
    print("\n=== ② `veg_h` を振ったときの 2 項の増分（頭打ちを避けた刻み）===")
    print("  ⚠️ `veg_loss` は 45 dB で頭打ち＝張り付いた後の増分は意味を持たない")
    both: list[str] = []
    print(f"{'link':24s} {'veg_h':>6s} {'回折損':>8s} {'植生損':>8s} "
          f"{'Δ回折':>8s} {'Δ植生':>8s}  頭打ち")
    for lk in links.values():
        rows, prev = [], None
        capped_at = None
        for v in VEG_STEPS_M:
            d, g = lk.diff_loss(v), lk.veg_loss(v)
            dd = dg = float("nan")
            if prev is not None:
                dd, dg = d - prev[0], g - prev[1]
            if capped_at is None and g >= VEG_CAP_DB - 1e-9:
                capped_at = v
            rows.append((v, d, g, dd, dg))
            prev = (d, g)
        # 「同じ 1 つのキャノピーが 2 つの項を同時に増やす」回線だけ出す
        grew = [r for r in rows[1:] if r[3] > 0.5 and r[4] > 0.5]
        if not grew:
            continue
        both.append(lk.id)
        for v, d, g, dd, dg in rows:
            cap = "  ← 頭打ち" if g >= VEG_CAP_DB - 1e-9 else ""
            head = lk.id[:24] if v == rows[0][0] else ""
            print(f"{head:24s} {v:6g} {d:8.2f} {g:8.2f} "
                  f"{dd:8.2f} {dg:8.2f}{cap}")
        print(f"{'':24s} └ 頭打ち開始 = "
              f"{('veg_h ' + format(capped_at, 'g') + 'm') if capped_at is not None else 'なし'}")
    print(f"  => 2 項が同時に増える回線 {len(both)} / {len(links)} 本")
    return both


# ============================================================
# ③ `veg_loss` が立つ範囲
# ============================================================
def _report_where_veg_fires(links: dict[str, Link], veg_h: float = 10.0) -> None:
    print(f"\n=== ③ `veg_loss` はどこで立つか（veg_h={veg_h:g}m）===")
    print("  検査: 「F1 に食い込むが LoS より下」の標本は植生減衰を生むか")
    n_below = n_fire = 0
    for lk in links.values():
        surf = lk.elevs + veg_h
        in_f1 = (surf > lk.los - lk.f1) & (surf <= lk.los)   # F1 内・LoS 下
        w = lk.intruding(veg_h)
        n_below += int(np.count_nonzero(in_f1))
        n_fire  += int(np.count_nonzero(w[in_f1] > 0))
    print(f"  F1 に食い込むが LoS より下の標本 {n_below} 点 → "
          f"うち植生減衰を生んだ点 {n_fire} 点")
    print("  => `veg_loss` は **LoS より上でしか立たない**"
          if n_fire == 0 else "  => LoS 下でも立つ")

    # 🔴 植生が 1 本も無くても立つか（`veg_top = elevs + veg_h` の veg_h=0）
    print("\n  🔴 追加検査: **植生が 1 本も無い（veg_h=0）ときに `veg_loss` は 0 か**")
    bare = [(lk.veg_loss(0.0), lid) for lid, lk in links.items()]
    hit  = sorted([b for b in bare if b[0] > 0.01], reverse=True)
    for val, lid in hit:
        print(f"    {lid:24s} veg_h=0 の植生損失 = {val:6.2f} dB"
              f"{'  ← 上限に張り付き' if val >= VEG_CAP_DB - 1e-9 else ''}")
    print(f"    => {len(hit)} / {len(links)} 本が、**植生が無いのに植生減衰を出す**"
          if hit else "    => 該当なし")


# ============================================================
# ④ 形の違い（本題）＝幾何 か 長さ か
# ============================================================
def _report_shape(links: dict[str, Link], targets: list[str], veg_h: float = 10.0) -> None:
    print(f"\n=== ④ 2 項は面のどこを見ているか（veg_h={veg_h:g}m・本題）===")
    print("  Bullington の回折損は**接線を決める 1〜2 標本**だけで決まる。")
    print("  「その窓だけ植生」と「その窓以外だけ植生」に割って、2 項の出方を見る。")
    print("  ⚠️ **裸地の下駄を引く**＝`veg_h=0` でも 2 項とも値を持つ回線がある")
    print("     （地形そのものが LoS を切っている）。引かないと『決定点が植生損の")
    print("     89% を占める』という**地形の値を植生の手柄にした数字**が出る。")
    print(f"{'link':22s} {'決定点':>6s} {'積む点':>6s} | "
          f"{'Δ回折':>7s} {'窓のみ':>7s} {'窓以外':>7s} | "
          f"{'Δ植生':>7s} {'窓のみ':>7s} {'窓以外':>7s} | {'重なり':>6s}")
    shares = []
    for lid in targets:
        lk   = links[lid]
        idx  = lk.governing_idx(veg_h)
        n_acc = int(np.count_nonzero(lk.intruding(veg_h)))
        win  = _mask(lk.n, idx)
        out  = 1.0 - win
        out[0] = out[-1] = 0.0            # 端点は LoS を動かすので触らない
        d0, v0 = lk.diff_loss(0.0), lk.veg_loss(0.0)
        dd = (lk.diff_loss(veg_h) - d0,
              lk.diff_loss(veg_h, win) - d0, lk.diff_loss(veg_h, out) - d0)
        dv = (lk.veg_loss(veg_h) - v0,
              lk.veg_loss(veg_h, win) - v0, lk.veg_loss(veg_h, out) - v0)
        share = dv[1] / dv[0] if dv[0] > 0.01 else float("nan")
        if dv[0] > 0.01:
            shares.append((share, lid))
        print(f"{lid[:22]:22s} {len(idx):6d} {n_acc:6d} | "
              + " ".join(f"{x:7.2f}" for x in dd) + " | "
              + " ".join(f"{x:7.2f}" for x in dv)
              + f" | {share*100:5.1f}%")
    if shares:
        shares.sort(reverse=True)
        print(f"  => 回折損を決めている標本が**植生ぶんの増分**に占める割合＝"
              f"最大 {shares[0][0]*100:.1f}%（{shares[0][1]}）")


# ============================================================
# ⑤ 長さ比例（幾何を固定して長さだけ伸ばす）
# ============================================================
def _report_length(links: dict[str, Link], targets: list[str], veg_h: float = 10.0) -> None:
    print(f"\n=== ⑤ 幾何を固定して植生の長さだけ伸ばす（veg_h={veg_h:g}m）===")
    print("  回折損が動かず植生損だけ伸びれば、2 項は別の量を見ている。")
    fracs = (0.05, 0.1, 0.25, 0.5, 0.75, 1.0)
    print(f"{'link':24s} {'量':>4s} " + " ".join(f"{f*100:6.0f}%" for f in fracs))
    for lid in targets:
        lk  = links[lid]
        idx = lk.governing_idx(veg_h)
        # 回折を決める標本を必ず含む連続帯を、両側へ伸ばす
        c   = int(round(sum(idx) / len(idx)))
        ds, vs = [], []
        for f in fracs:
            half = max(1, int(lk.n * f / 2))
            m = np.zeros(lk.n)
            m[max(1, c - half): min(lk.n - 1, c + half + 1)] = 1.0
            m[list(idx)] = 1.0            # 幾何は常に固定
            ds.append(lk.diff_loss(veg_h, m))
            vs.append(lk.veg_loss(veg_h, m))
        print(f"{lid[:24]:24s} {'回折':>4s} " + " ".join(f"{x:6.2f}" for x in ds))
        print(f"{'':24s} {'植生':>4s} " + " ".join(f"{x:6.2f}" for x in vs))


# ============================================================
# ⑥ 合成の規則＝直列に足すか、良いほうを採るか
# ============================================================
def _report_combination(recs: list[dict], links: dict[str, Link]) -> None:
    print("\n=== ⑥ いま製品は 2 項を**直列に足している**＝効き（コーパスの既定値で）===")
    print("  `calculate_link_budget` は total_loss = FSPL + diff + veg + … と足す。")
    print("  ⚠️ **これは是非の判定ではなく、規則を替えたときの振れ幅の測定**。")
    print(f"{'link':22s} {'veg_h':>5s} {'回折':>7s} {'植生':>7s} "
          f"{'和':>7s} {'大きい方':>8s} {'差':>7s} {'余裕(和)':>9s} {'判定':>10s}")
    both = flips = 0
    worst = (0.0, "")
    for rec in recs:
        lid, inp = rec["id"], rec["input"]
        lk = links[lid]
        prop = models.calculate_propagation(
            lk.terrain, inp["h_tx"], inp["h_rx"], inp["freq_mhz"],
            inp["veg_h"], inp["k_factor"], diff_method=models.DIFF_METHOD_MULTI,
            env_type=inp["env_type"], rain_rate=inp["rain_rate"],
        )
        d, v = prop.diff_loss, prop.veg_loss
        if d <= 0.01 or v <= 0.01:
            continue
        both += 1
        gap = (d + v) - max(d, v)
        bud = models.calculate_link_budget(
            prop, inp["freq_mhz"], inp["p_tx"], inp["gain_tx"], inp["gain_rx"],
            inp["sens"])
        # 「良いほうを採る」規則にしたときの余裕＝差だけ戻る
        alt_status = "OK" if bud.actual_margin + gap >= 0 else "NG"
        flip = alt_status != bud.status
        flips += flip
        if gap > worst[0]:
            worst = (gap, lid)
        print(f"{lid[:22]:22s} {inp['veg_h']:5g} {d:7.2f} {v:7.2f} "
              f"{d + v:7.2f} {max(d, v):8.2f} {gap:7.2f} "
              f"{bud.actual_margin:9.2f} {bud.status:>4s}"
              f"{' → ' + alt_status if flip else '':>6s}")
    print(f"  => 2 項が同時に効く回線 {both} / {len(recs)} 本／"
          f"最大の差 {worst[0]:.2f} dB（{worst[1]}）／**判定が裏返る回線 {flips} 本**")


def main() -> None:
    recs  = _load()
    links = {r["id"]: Link(r) for r in recs}

    if not _report_sweep_is_valid(links):
        print("\n⛔ 掃引が汚染されたままなので以降は測らない（1 巡目と同じ理由）。")
        return

    both = _report_increments(links)
    _report_where_veg_fires(links)
    targets = both[:6] if both else list(links)[:6]
    _report_shape(links, targets)
    _report_length(links, targets)
    _report_combination(recs, links)

    print("\n注意: この探針は**モデルの中で 2 項が何を見ているか**を測る。"
          "真値との照合は持っていない（3.4 / Tracer 待ち）。")


if __name__ == "__main__":
    main()
