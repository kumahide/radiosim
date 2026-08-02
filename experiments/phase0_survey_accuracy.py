"""
experiments/phase0_survey_accuracy.py
=====================================
**離陸点の測量精度 ±N m が余裕度[dB]を何 dB 動かすか**の感度逆算＝段階 0。
RadioSim Tracer の精度目標（RTK が要るか）を勘でなく感度で決めるための材料。

**これは製品コードではない。** どのモジュールからも import されず、CI のゲート対象でも
ない（`experiments/` は「判断の根拠を再実行できる形で残す」ための置き場）。
**ネットワークに触らない**＝`tests/data/golden_links.json` の実 DEM 由来の標高配列から
再計算するだけ（回帰コーパスと同じ入力・同じ計算経路）。

## なぜ h（アンテナ高）を振ると「測量精度」の話になるのか
memory: project-radiosim-tracer §9 の方式＝**標高（ジオイド基準）を作業座標にし、
垂直は「離陸点の標高 ＋ 気圧相対高度」**。この方式の効果は「誤差の場所が移る」こと＝
機体の垂直位置の絶対精度を支配するのは気圧計ではなく**離陸点の標高の絶対精度**で、
その誤差はフライト全体に**一定オフセット**として乗る。
⇒ 離陸点の測量誤差 δ [m] ＝ 機体側アンテナ高 h_rx の誤差 δ [m]。

## 何を測るか
「真の高さ h」で測ったサンプルを「誤った高さ h+δ」の予測と突き合わせると、
差 |margin(h+δ) − margin(h)| がそのまま**残差に混入する見かけの誤差**になる。
これは 3.5（ローカル較正）が環境係数へ焼き込んでしまう量そのもの
（§10 が「較正を汚染から守る」と書いているのと同じ構造）。

## 判定の物差し（budget）
§7-3 の測定側の目標＝**受信レベルの絶対確度 ±1 dB**。測量誤差が測定系の誤差の
**支配項になってはいけない**ので、
  - 目標  : 0.30 dB 以下（±1 dB に対して二乗和でほぼ効かない＝1.04 dB に留まる）
  - 上限  : 1.00 dB 以下（測定系の誤差と同格＝ここを超えたら測量が支配項）
判定は**「測定可能な回線」の worst case** で行う。

⚠️ **2026-08-02 の実行で前提が 1 つ壊れた**：memory §12 は「深く塞がれた回線は
diff_loss が飽和するのでかすめ帯が目標を支配する」としていたが、**このモデルの
diff_loss は飽和しない**（ナイフエッジが単調に伸びる）。実測すると深く塞がれた回線
ほど勾配が大きく（例 fuji_kawaguchi＝32 dB/m）、かすめ帯（1.3 dB/m）を桁で上回る。
しかしそれらは余裕度が −1900 dB 級＝**感度以下で永久に測れない**（§5-4 の打ち切り）。
較正の残差に混入しうるのは**測れた回線だけ**なので、
  - 母集団 ＝ 余裕度が受信機の窓に入る回線（`MEASURABLE_FLOOR_DB` 以上）
  - その中の worst は結局かすめ帯が取る
⇒ **結論としての「かすめ帯が支配する」は正しいが、理由は「飽和」ではなく
「深い側は測れない＝母集団から落ちる」**。理由が違うと母集団の切り方を間違える。

## 実行
    python experiments/phase0_survey_accuracy.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import models              # noqa: E402
import simulation as sim   # noqa: E402

_CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "tests", "data", "golden_links.json")

# 振り幅の格子 [m]。±12 m を 0.1 m 刻み＝単独測位 GNSS の垂直誤差帯まで覆う。
STEP_M = 0.1
MAX_M = 12.0

# 報告する測量精度 ±N m と、その N が現実にどの測量手段に当たるか。
METHODS = [
    (0.05, "RTK 静止観測 / 既存の水準点・三角点からの水準測量"),
    (0.10, "ネットワーク型 RTK（短時間観測）"),
    (0.30, "5m メッシュ DEM（航空レーザ測量部）読み取り"),
    (1.00, "気圧高度計の閉合誤差級 / 良好な条件の DGNSS"),
    (3.00, "10m メッシュ DEM 読み取り / 地形図読み取り"),
    (5.00, "単独測位 GNSS（垂直・良好時）"),
    (10.00, "単独測位 GNSS（垂直・悪条件）/ スマホ・FC の高度"),
]

TARGET_DB = 0.30   # 目標＝支配項にしない
CEILING_DB = 1.00  # 上限＝測定系と同格

# かすめ帯の定義（コーパスの網羅性テストと同じ規則）。
GRAZING_LO, GRAZING_HI = 0.0, 6.0

# 「測定可能」の床 [dB]。余裕度がこれを下回る回線は感度以下＝打ち切り（§5-4）で、
# 受信レベルが読めない以上、較正の残差に寄与しない＝精度目標を決める母集団に入らない。
# −20 dB は「予測が 20 dB 悲観側に外れていれば実際には測れたかもしれない」余地。
MEASURABLE_FLOOR_DB = -20.0


def _params(inp: dict) -> sim.SimParams:
    return sim.SimParams({
        "start": f"{inp['lat_tx']}, {inp['lon_tx']}",
        "end":   f"{inp['lat_rx']}, {inp['lon_rx']}",
        "h_tx": str(inp["h_tx"]), "h_rx": str(inp["h_rx"]),
        "freq": str(inp["freq_mhz"]), "p_tx": str(inp["p_tx"]),
        "gain_tx": str(inp["gain_tx"]), "gain_rx": str(inp["gain_rx"]),
        "sens": str(inp["sens"]), "veg_h": str(inp["veg_h"]),
        "k_factor": str(inp["k_factor"]), "samples": str(inp["samples"]),
        "env_type": inp["env_type"], "diff_method": inp["diff_method"],
        "rain_rate": str(inp["rain_rate"]),
    })


def _sweep(link: dict, deltas: np.ndarray) -> dict:
    """1 回線について δ を振り、余裕度と判定を集める。

    ⚠️ terrain は 1 回だけ作って使い回す（`TestRunCalculationIsPure` が固定した
    前提＝地形は h に依存しない／run_calculation は純関数）。
    """
    params = _params(link["input"])
    terrain = models.calculate_terrain_profile(
        raw_elevs=np.array(link["raw_elevs"], dtype=float),
        lat_tx=params.lat_tx, lon_tx=params.lon_tx,
        lat_rx=params.lat_rx, lon_rx=params.lon_rx,
    )
    base = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)

    margins, statuses, diffs = [], [], []
    for d in deltas:
        # 機体側（RX）の高さだけを動かす＝離陸点の標高誤差がフライト全体へ乗る形。
        h_rx = params.h_rx + float(d)
        r = sim.run_calculation(terrain, params.h_tx, h_rx, params)
        margins.append(r.actual_margin)
        statuses.append(r.status)
        diffs.append(r.diff_loss)

    return {
        "id": link["id"],
        "dist_km": terrain.horiz_dist_km,
        "freq_mhz": params.freq_mhz,
        "h_rx": params.h_rx,
        "base_margin": base.actual_margin,
        "base_status": base.status,
        "base_diff_loss": base.diff_loss,
        "grazing": GRAZING_LO < base.diff_loss < GRAZING_HI,
        "margins": np.array(margins),
        "statuses": statuses,
        "diff_losses": np.array(diffs),
    }


def _worst_within(sw: dict, deltas: np.ndarray, n: float) -> float:
    """|δ| ≤ N の範囲で余裕度が基準からずれる最大量 [dB]。"""
    m = np.abs(deltas) <= n + 1e-9
    base = sw["margins"][np.argmin(np.abs(deltas))]
    return float(np.max(np.abs(sw["margins"][m] - base)))


def _flips_within(sw: dict, deltas: np.ndarray, n: float) -> bool:
    """|δ| ≤ N の範囲で OK/NG 判定が反転するか。"""
    m = np.abs(deltas) <= n + 1e-9
    base = sw["base_status"]
    return any(s != base for s, keep in zip(sw["statuses"], m) if keep)


def _threshold_cohort(links: list[dict], deltas: np.ndarray,
                      h_lo: float, h_hi: float, veg_guard: bool) -> list[dict]:
    """各回線の**地形はそのまま**に、機体高さを「判定しきい値」へ寄せたコホート。

    なぜ要るか＝コーパスの 26 本はアンテナ高が OK/NG を作るために振ってあり、
    測定可能かつ回折が効く回線が 3 本しか残らない（母数が薄い）。しかし
    **較正データは「繋がるかどうかの境目」で取る**（そこが知りたい所だから）。
    ⇒ 実 DEM の地形断面 26 本を活かしたまま、h_rx を余裕度 0 dB を跨ぐ最小の高さへ
    合わせ直せば、**「境目に置かれた実地形」**という設計判断に効く母集団になる。

    ⚠️ `veg_guard`＝**h_rx が植生高を跨ぐ回線を外す**。跨ぐと veg_loss が 0→36 dB 級で
    **階段状に飛ぶ**（2026-08-02 の分解で確認・ISSUES.md B-033 と同じ場所）。これは
    地形回折の感度ではなく**モデルの不連続**なので、測量精度の逆算に混ぜると桁を
    間違える。`h_lo` も同じ理由＝アンテナ高 2 m 級の解は機体の運用高度ではない。
    """
    out = []
    for link in links:
        params = _params(link["input"])
        terrain = models.calculate_terrain_profile(
            raw_elevs=np.array(link["raw_elevs"], dtype=float),
            lat_tx=params.lat_tx, lon_tx=params.lon_tx,
            lat_rx=params.lat_rx, lon_rx=params.lon_rx,
        )

        def margin_at(h: float) -> float:
            return sim.run_calculation(terrain, params.h_tx, h, params).actual_margin

        lo_bound = h_lo
        if veg_guard:
            # ±MAX_M 振っても樹冠へ入らない高さから上だけを探す。
            lo_bound = max(lo_bound, params.veg_h + MAX_M + 1.0)
        if lo_bound >= h_hi:
            continue

        # 粗探索（2 m 刻み）→ 二分法で余裕度 0 dB の高さを詰める。
        lo = None
        for h in np.arange(lo_bound, h_hi + 0.001, 2.0):
            if margin_at(float(h)) >= 0.0:
                lo = float(h)
                break
        if lo is None or lo <= lo_bound:
            continue    # 帯の上端でも繋がらない／下端で既に繋がる＝帯内に境目が無い
        a, b = lo - 2.0, lo
        for _ in range(20):
            mid = (a + b) / 2
            if margin_at(mid) >= 0.0:
                b = mid
            else:
                a = mid
        h_th = b

        margins, statuses, diffs = [], [], []
        for d in deltas:
            r = sim.run_calculation(terrain, params.h_tx, h_th + float(d), params)
            margins.append(r.actual_margin)
            statuses.append(r.status)
            diffs.append(r.diff_loss)
        base = sim.run_calculation(terrain, params.h_tx, h_th, params)
        out.append({
            "id": link["id"], "dist_km": terrain.horiz_dist_km,
            "freq_mhz": params.freq_mhz, "h_rx": h_th, "veg_h": params.veg_h,
            "base_margin": base.actual_margin, "base_status": base.status,
            "base_diff_loss": base.diff_loss,
            "grazing": GRAZING_LO < base.diff_loss < GRAZING_HI,
            "measurable": True,
            "margins": np.array(margins), "statuses": statuses,
            "diff_losses": np.array(diffs),
        })
    return out


def main() -> None:
    with open(_CORPUS, encoding="utf-8") as f:
        corpus = json.load(f)
    links = corpus["links"]

    deltas = np.round(np.arange(-MAX_M, MAX_M + STEP_M / 2, STEP_M), 3)
    print(f"コーパス {len(links)} 回線 × δ {len(deltas)} 点 "
          f"(±{MAX_M:.0f} m / {STEP_M} m 刻み) を計算中 ...\n")

    sweeps = [_sweep(link, deltas) for link in links]
    for s in sweeps:
        s["measurable"] = bool(s["base_margin"] >= MEASURABLE_FLOOR_DB)
    graz = [s for s in sweeps if s["grazing"]]
    meas = [s for s in sweeps if s["measurable"]]
    censored = [s for s in sweeps if not s["measurable"]]
    print(f"母集団｜測定可能（余裕度 ≥ {MEASURABLE_FLOOR_DB:.0f} dB）={len(meas)} 本 / "
          f"打ち切り={len(censored)} 本 / うちかすめ帯={len(graz)} 本\n")

    # ---- 回線ごとの勾配（δ=0 近傍）と ±1 m の振れ -------------------------
    print("=" * 100)
    print("回線ごと｜勾配 dMargin/dh（δ=0 近傍・中心差分 ±0.5m）と ±N m の worst |ΔMargin|")
    print("=" * 100)
    print(f"{'id':<26}{'km':>7}{'MHz':>7}{'margin':>9}{'diff':>8}{'区分':>7}"
          f"{'d/dh':>9}{'±0.3m':>8}{'±1m':>8}{'±3m':>8}{'±10m':>8}")
    ihi = int(np.argmin(np.abs(deltas - 0.5)))
    ilo = int(np.argmin(np.abs(deltas + 0.5)))
    for s in sorted(sweeps, key=lambda x: (-x["measurable"],
                                           -_worst_within(x, deltas, 1.0))):
        grad = (s["margins"][ihi] - s["margins"][ilo]) / (deltas[ihi] - deltas[ilo])
        kind = "かすめ" if s["grazing"] else ("測定可" if s["measurable"] else "打切")
        print(f"{s['id']:<26}{s['dist_km']:>7.2f}{s['freq_mhz']:>7.0f}"
              f"{s['base_margin']:>9.1f}{s['base_diff_loss']:>8.1f}{kind:>7}"
              f"{grad:>9.3f}"
              f"{_worst_within(s, deltas, 0.3):>8.2f}"
              f"{_worst_within(s, deltas, 1.0):>8.2f}"
              f"{_worst_within(s, deltas, 3.0):>8.2f}"
              f"{_worst_within(s, deltas, 10.0):>8.2f}")
    print(f"\n（d/dh の単位は dB/m。区分＝かすめ帯 0<diff_loss<{GRAZING_HI:.0f} dB ／ "
          f"打切＝余裕度 < {MEASURABLE_FLOOR_DB:.0f} dB で測れない）")

    # ---- ±N m ごとの集計 --------------------------------------------------
    print("\n" + "=" * 100)
    print("測量精度 ±N m ごと｜余裕度の振れ [dB]（かすめ帯 worst が判定を支配する）")
    print("=" * 100)
    print(f"{'±N m':>7}{'測定可 worst':>13}{'測定可 中央':>12}{'かすめ worst':>13}"
          f"{'打切 worst':>12}{'判定反転':>9}  判定 / 対応する測量手段")
    rows = []
    for n, method in METHODS:
        mw = max(_worst_within(s, deltas, n) for s in meas)
        mm = statistics.median(_worst_within(s, deltas, n) for s in meas)
        gw = max(_worst_within(s, deltas, n) for s in graz)
        cw = max(_worst_within(s, deltas, n) for s in censored)
        flips = [s["id"] for s in meas if _flips_within(s, deltas, n)]
        verdict = ("○ 目標内" if mw <= TARGET_DB
                   else "△ 上限内" if mw <= CEILING_DB else "× 支配項")
        rows.append({"n_m": n, "measurable_worst_db": mw, "measurable_median_db": mm,
                     "grazing_worst_db": gw, "censored_worst_db": cw,
                     "flips": flips, "verdict": verdict, "method": method})
        print(f"{n:>7.2f}{mw:>13.2f}{mm:>12.2f}{gw:>13.2f}{cw:>12.1f}"
              f"{len(flips):>9d}  {verdict} / {method}")
    print("\n（'打切' 列は参考＝感度以下で測れない回線。勾配は最大だが較正には入らない）")
    for r in rows:
        if r["flips"]:
            print(f"  ±{r['n_m']:.2f} m で判定が反転する回線: {', '.join(r['flips'])}")

    # ---- しきい値へ寄せたコホート（母数を厚くする） ------------------------
    # 帯 A＝全高度（参考。植生の階段と地上高 2 m 級の解を含むので逆算には使わない）
    # 帯 B＝機体の運用高度帯（30〜150m AGL）かつ樹冠から離れている＝設計判断に使う
    th_all = _threshold_cohort(links, deltas, 1.0, 400.0, veg_guard=False)
    th = _threshold_cohort(links, deltas, 30.0, 150.0, veg_guard=True)

    print("\n" + "=" * 100)
    print("しきい値コホート｜実 DEM 地形はそのまま・機体高さを余裕度 0 dB へ合わせ直す")
    print("=" * 100)
    print(f"{'id':<26}{'km':>7}{'MHz':>7}{'h_th m':>9}{'veg':>6}{'diff':>8}"
          f"{'d/dh':>9}{'±0.1m':>8}{'±0.3m':>8}{'±1m':>8}{'±3m':>8}")
    print("--- 帯 B＝運用高度 30〜150m AGL・樹冠から離す（← 逆算はこれで行う）")
    for s in sorted(th, key=lambda x: -_worst_within(x, deltas, 1.0)):
        grad = (s["margins"][ihi] - s["margins"][ilo]) / (deltas[ihi] - deltas[ilo])
        print(f"{s['id']:<26}{s['dist_km']:>7.2f}{s['freq_mhz']:>7.0f}"
              f"{s['h_rx']:>9.1f}{s['veg_h']:>6.0f}{s['base_diff_loss']:>8.1f}{grad:>9.3f}"
              f"{_worst_within(s, deltas, 0.1):>8.2f}"
              f"{_worst_within(s, deltas, 0.3):>8.2f}"
              f"{_worst_within(s, deltas, 1.0):>8.2f}"
              f"{_worst_within(s, deltas, 3.0):>8.2f}")
    print(f"帯 B＝{len(th)} 本 / 帯 A（全高度・参考）＝{len(th_all)} 本 / コーパス {len(links)} 本")

    th_rows = []
    print(f"\n{'±N m':>7}{'帯B worst':>11}{'帯B p90':>10}{'帯B 中央':>10}"
          f"{'帯A worst':>11}  判定（帯 B）")
    for n, _method in METHODS:
        w = [_worst_within(s, deltas, n) for s in th]
        worst, p90 = max(w), float(np.percentile(w, 90))
        med = statistics.median(w)
        aw = max(_worst_within(s, deltas, n) for s in th_all)
        verdict = ("○ 目標内" if worst <= TARGET_DB
                   else "△ 上限内" if worst <= CEILING_DB else "× 支配項")
        th_rows.append({"n_m": n, "worst_db": worst, "p90_db": p90,
                        "median_db": med, "band_a_worst_db": aw, "verdict": verdict})
        print(f"{n:>7.2f}{worst:>11.2f}{p90:>10.2f}{med:>10.2f}{aw:>11.1f}  {verdict}")
    print("\n（帯 A との差が、植生の階段（B-033）と地上高 2m 級の解が持ち込む見かけの感度）")

    # ---- 逆算＝budget を満たす最大の N ------------------------------------
    print("\n" + "=" * 100)
    print("逆算｜budget を満たす最大の測量誤差")
    print("=" * 100)
    fine = np.round(np.arange(0.05, MAX_M + 0.001, 0.05), 3)
    for name, pop in ((f"コーパスそのまま（測定可 {len(meas)} 本）", meas),
                      (f"しきい値コホート 帯 B（境目・運用高度 {len(th)} 本）", th)):
        print(f"  【{name}】")
        for label, budget in (("目標 0.30 dB（支配項にしない）", TARGET_DB),
                              ("上限 1.00 dB（測定系と同格）", CEILING_DB)):
            ok = [n for n in fine
                  if max(_worst_within(s, deltas, float(n)) for s in pop) <= budget]
            best = max(ok) if ok else 0.0
            binding = max(pop, key=lambda s: _worst_within(s, deltas, max(best, 0.05)))
            print(f"    {label:<32} → ±{best:.2f} m まで許容（律速＝{binding['id']}）")

    # ⚠️ 結果はファイルへ書かない（experiments/README.md が宣言している不変条件＝
    #    「純計算・ネットワーク／GUI／ファイル書き込みなし」）。数値は全て上に出ている。


if __name__ == "__main__":
    main()
