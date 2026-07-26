"""
experiments/phase0_two_ray.py
=============================
2 波干渉（地面反射マルチパス）が対空リンクで無視できるかの **机上判定＝段階 0**。

**これは製品コードではない。** RadioSim / RadioSim for Drone のどのモジュールからも
import されず、CI のゲート対象でもない（`experiments/` は「判断の根拠を再実行できる
形で残す」ための置き場）。純計算のみ＝ネットワーク・GUI・書き込みなし。

## なぜこれが要るのか
RadioSim は 2 波モデルを持たないため、**高度スイープを回しても構造上なめらかな曲線
しか出ない**。「地面反射で落ち込む高度帯があるか」は RadioSim 自身では答えられず、
独立に計算した 2 波の答えと突き合わせて初めて検証になる。段階 0 はその前段で、
**実装に入る前に「懸念が現実的か」を机上で絞る**ためのもの。

## 実行
    python experiments/phase0_two_ray.py
既定の条件は 2026-07-26 にユーザーが指定した運用値（2.4GHz / GCS 高 2〜3m /
飛行高度 30〜150m / 水平距離 〜1000m）。定数を書き換えれば他の条件も出せる。

## 結論（2026-07-26 の実行結果・詳細は memory: project-radiosim-for-drone）
- 飛行帯 30〜150m に理想 2 波のヌルが **4〜29 個**入る＝「高く飛べば良くなる」は不成立。
- 鏡面反射点は **GCS の前方 3〜80m**＝経路上の地形ではなく**離陸地点の足元**が効く。
- ヌルの深さは地表の粗さで決まる＝舗装/水面で 24〜26dB、草地で 0.6〜16dB、樹林で 0dB。
  **低高度・遠距離ほど危険**（擦過角が浅く鏡面反射が生き残る）。
- ⇒ 懸念は棄却できない。ただし **ヌルの「位置」は予測してはいけない**（Δh が 4〜31m
  ＝GCS を 1m 動かすと総取っ替え。10m メッシュの DEM で当てるのは原理的に不可能）。
  予測すべきは**振幅の包絡線＝不確かさ帯**。

## 出典（式）
- 2 波モデルのローブ間隔・鏡面点・擦過角＝平面大地の幾何。
- 粗面による鏡面成分の低減 rho_s = exp(-2 * (2*pi*sigma*sin(psi)/lambda)**2)（Ament）。
- 鏡面反射の成立条件＝レイリーの粗度基準 sigma < lambda / (8 sin psi)。
"""

import json
import math
import pathlib

C_LIGHT = 299_792_458.0

# --- 条件（2026-07-26 ユーザー指定の運用値）--------------------------------
FREQ_HZ      = 2.4e9
GCS_HEIGHTS  = (2.0, 3.0)          # GCS アンテナ地上高 [m]
GCS_TYPICAL  = 2.5                 # 代表値 [m]
DISTANCES    = (200.0, 500.0, 1000.0)   # 水平距離 [m]
ALTITUDES    = (30.0, 80.0, 150.0)      # 機体高度 [m AGL]
BAND_M       = 150.0 - 30.0             # 飛行帯の幅 [m]

# 乾いた地面・水平偏波の擦過入射における反射係数の代表値（|Gamma|）。
GAMMA0 = 0.95

# 地表の種類と代表的な粗さ sigma_h [m]（オーダーの目安）。
SURFACES = (
    ("水面・湿地",         0.005),
    ("アスファルト・舗装",  0.01),
    ("平坦な裸地・短草",    0.05),
    ("草地・畑",           0.15),
    ("荒地・低木",         0.50),
    ("樹林・市街",         2.00),
)

LAMBDA = C_LIGHT / FREQ_HZ
ROOT = pathlib.Path(__file__).resolve().parents[1]


def lobe_spacing(dist_m: float, h_gcs: float) -> float:
    """受信高を変えたときのヌル間隔 dh ~ lambda*d/(2*h_gcs) [m]。"""
    return LAMBDA * dist_m / (2.0 * h_gcs)


def specular_point(dist_m: float, h_gcs: float, h_air: float) -> tuple[float, float]:
    """平面大地の鏡面反射点までの水平距離 [m] と擦過角 [rad] を返す。"""
    d1 = dist_m * h_gcs / (h_gcs + h_air)
    psi = math.atan((h_gcs + h_air) / dist_m)
    return d1, psi


def rayleigh_sigma_max(psi: float) -> float:
    """鏡面反射が成立する粗さの上限 [m]（レイリーの粗度基準）。"""
    return LAMBDA / (8.0 * math.sin(psi))


def specular_reduction(sigma_m: float, psi: float) -> float:
    """粗面による鏡面成分の低減係数 rho_s（Ament）。"""
    g = (2.0 * math.pi * sigma_m * math.sin(psi)) / LAMBDA
    return math.exp(-2.0 * g * g)


def null_depth_db(gamma_eff: float) -> float:
    """実効反射係数 |Gamma_eff| のときのヌルの深さ [dB]（直接波を 0dB 基準）。"""
    residual = abs(1.0 - abs(gamma_eff))
    if residual <= 0.0:
        return float("inf")
    return -20.0 * math.log10(residual)


def detrended_rms(values: list[float]) -> float:
    """一次傾斜を除いた残差の RMS [m]（区間の「うねり」＝ sigma_h）。"""
    n = len(values)
    if n < 3:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    sxx = sum((i - mean_x) ** 2 for i in range(n))
    sxy = sum((i - mean_x) * (values[i] - mean_y) for i in range(n))
    slope = sxy / sxx if sxx else 0.0
    res = [values[i] - (mean_y + slope * (i - mean_x)) for i in range(n)]
    return math.sqrt(sum(r * r for r in res) / n)


def _rule() -> None:
    print("=" * 74)


def report_lobes() -> None:
    _rule()
    print(f"(1) ローブ間隔と飛行帯 30-150m（幅 {BAND_M:.0f}m）に入るヌルの数")
    _rule()
    print(f"{'水平距離m':>10} {'GCS高m':>7} {'間隔dh m':>10} {'ヌル数':>8}")
    for dist in DISTANCES:
        for h_gcs in GCS_HEIGHTS:
            dh = lobe_spacing(dist, h_gcs)
            print(f"{dist:>10.0f} {h_gcs:>7.1f} {dh:>10.1f} {BAND_M / dh:>8.1f}")


def report_geometry() -> None:
    _rule()
    print("(2) 鏡面反射点の位置（GCS からの水平距離）と擦過角")
    _rule()
    print(f"{'水平距離m':>10} {'機体高m':>8} {'鏡面点m':>9} {'擦過角deg':>10} {'粗さ上限cm':>11}")
    for dist in DISTANCES:
        for h_air in ALTITUDES:
            d1, psi = specular_point(dist, GCS_TYPICAL, h_air)
            print(f"{dist:>10.0f} {h_air:>8.0f} {d1:>9.1f} "
                  f"{math.degrees(psi):>10.2f} {rayleigh_sigma_max(psi) * 100:>11.1f}")


def report_null_depth() -> None:
    _rule()
    print(f"(3) 地表の粗さ -> 鏡面成分 -> ヌルの深さ（d=1000m・GCS {GCS_TYPICAL}m・"
          f"|Gamma0|={GAMMA0}）")
    _rule()
    for h_air in (ALTITUDES[0], ALTITUDES[-1]):
        _, psi = specular_point(1000.0, GCS_TYPICAL, h_air)
        print(f"\n  機体高 {h_air:.0f}m（擦過角 {math.degrees(psi):.2f} deg・"
              f"粗さ上限 {rayleigh_sigma_max(psi) * 100:.1f}cm）")
        print(f"  {'地表':<20} {'sigma cm':>9} {'rho_s':>8} {'|G_eff|':>9} {'ヌル深さdB':>11}")
        for name, sigma in SURFACES:
            rho = specular_reduction(sigma, psi)
            gamma = GAMMA0 * rho
            print(f"  {name:<20} {sigma * 100:>9.1f} {rho:>8.3f} {gamma:>9.3f} "
                  f"{null_depth_db(gamma):>11.1f}")


def report_real_terrain() -> None:
    """実 DEM（ゴールデンコーパス）の地形うねりを測る。ネットワーク不要。"""
    _rule()
    print("(4) 実 DEM の粗さ（tests/data/golden_links.json・26 本・API 不要）")
    print("    一次傾斜を除いた残差 RMS を約 100m 窓で評価")
    _rule()
    corpus = ROOT / "tests" / "data" / "golden_links.json"
    data = json.loads(corpus.read_text(encoding="utf-8"))

    rows = []
    for link in data["links"]:
        elevs = link["raw_elevs"]
        dx = link["expected"]["horiz_dist_km"] * 1000.0 / (len(elevs) - 1)
        win = max(3, int(round(100.0 / dx)))
        step = max(1, win // 2)
        sigmas = [detrended_rms(elevs[i:i + win])
                  for i in range(0, max(1, len(elevs) - win), step)]
        if sigmas:
            rows.append((link["id"], dx, sum(sigmas) / len(sigmas), max(sigmas)))

    rows.sort(key=lambda r: r[2])
    print(f"{'link':<26} {'標本間隔m':>10} {'sigma平均cm':>12} {'最大cm':>9}")
    for link_id, dx, avg, hi in rows:
        print(f"{link_id:<26} {dx:>10.1f} {avg * 100:>12.0f} {hi * 100:>9.0f}")
    overall = sum(r[2] for r in rows) / len(rows)
    print(f"\n  26 本の平均 sigma_h = {overall * 100:.0f} cm")
    print("  注意: DEM 10m グリッドは草・砂利・作物の微細な粗さを持たない＝これは下限。")
    print("  注意: 効くのは経路上の地形ではなく GCS 前方 3-80m。運用では平坦で開けた")
    print("        場所に GCS を置く＝選択的に最も滑らかな地面を選ぶので、この統計を")
    print("        根拠に安心してはいけない。")


def main() -> None:
    print(f"lambda = {LAMBDA * 100:.2f} cm （{FREQ_HZ / 1e9:.1f} GHz）\n")
    report_lobes()
    print()
    report_geometry()
    print()
    report_null_depth()
    print()
    report_real_terrain()


if __name__ == "__main__":
    main()
