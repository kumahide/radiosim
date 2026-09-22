"""
ground_reflection.py
=====================
地面反射（2波干渉）の**振幅の包絡線**だけを持つ層。純粋計算のみ・副作用ゼロ。

🔑 **受信電力そのものには一切混ぜない**（ロードマップ 3.4 決定）＝ヌルの「位置」は
GCS/アンテナ高が数十cm動くだけで総取っ替えになり、10mメッシュのDEMで当てるのは
原理的に不可能（`experiments/phase0_two_ray.py` の結論）。予測してよいのは
「振幅がどれだけ振れ得るか」という**幅**だけ。

式の出典は `experiments/phase0_two_ray.py`（2026-07-26・段階0の机上検証）と同じ:
  - 平面大地の鏡面反射点・擦過角
  - Ament の粗面低減 rho_s = exp(-2*(2*pi*sigma*sin(psi)/lambda)**2)
  - レイリーの粗度基準 sigma < lambda/(8*sin(psi))

`core/diffraction.py`（B-130 分割）と同じ位置づけの独立モジュール。3.4 ステージ4
では `models.py` から呼ばない・再輸出もしない＝配線は帳票の面（ステージ6）で行う。
"""
import math
from dataclasses import dataclass

import numpy as np

C_LIGHT: float = 299_792_458.0

#: 乾いた地面・水平偏波の擦過入射における反射係数の代表値（|Gamma|）。
#: `experiments/phase0_two_ray.py` と同じ値（2026-07-26 決定の運用値）。
GAMMA0: float = 0.95

#: 反射点から両端までの距離がこれ未満なら「幅」を出さない [m]。
#: 🔑 **判断根拠**＝ロードマップの「ドローンのように反射点が端に寄る幾何
#: （GCS前方3〜80m）は10mメッシュDEMで分解できない」に対応する下限。
#: 200m は該当帯（〜80m）から十分離れており、後述の ~100m デトレンドウィンドウが
#: 経路の外にはみ出さない余裕も持つ（判断は経験則・数値ではなく文献値ではない）。
MIN_ARM_M: float = 200.0

#: sigma_h を推定するデトレンドウィンドウの半幅 [m]（`experiments/phase0_two_ray.py` の
#: 実 DEM 粗さ測定と同じ ~100m ウィンドウ）。
SIGMA_WINDOW_HALF_M: float = 50.0

#: 破壊的側（ヌル）の頭打ち [dB]。無限大は「参考値」として意味を持たない
#: （植生減衰の上限45dBと同じ考え方＝頭打ちが無いと極端な数字が独り歩きする）。
NULL_DEPTH_CAP_DB: float = 40.0

#: 建設的側（2波が完全同相のときの物理上限）[dB] = 20*log10(1+1)。
#: ⚠️ **粗さに依らず固定**＝同相条件が成立する位置を予測することは、まさに
#: 「予測してはいけないヌルの位置」と同じ問題なので、粗さで割り引かない
#: 絶対上限をそのまま出す（destructive 側は逆に粗さで見積もる＝非対称は意図的）。
CONSTRUCTIVE_GAIN_DB: float = 6.0


@dataclass
class TwoRayEnvelope:
    """1回線ぶんの地面反射（2波干渉）振幅包絡線。"""
    frac:                 float   # 反射点の経路上の位置（TX=0.0〜RX=1.0）
    sigma_h_m:            float   # DEM から自動推定した地表のうねり（RMS）[m]
    grazing_deg:          float   # 擦過角 [deg]
    roughness:            str     # "smooth" | "rough"（レイリー粗度基準との比較）
    null_depth_db:        float   # 破壊的側の振幅低下の見積り [dB]（0〜40）
    constructive_gain_db: float   # 建設的側の物理上限 [dB]（固定 6.0）


def specular_point(dist_m: float, h_near: float, h_far: float) -> tuple[float, float]:
    """平面大地の鏡面反射点までの水平距離 [m]（`h_near` 側から測る）と擦過角 [rad]。"""
    d1 = dist_m * h_near / (h_near + h_far)
    psi = math.atan((h_near + h_far) / dist_m)
    return d1, psi


def rayleigh_sigma_max(psi: float, lam: float) -> float:
    """鏡面反射が成立する粗さの上限 [m]（レイリーの粗度基準）。"""
    return lam / (8.0 * math.sin(psi))


def specular_reduction(sigma_m: float, psi: float, lam: float) -> float:
    """粗面による鏡面成分の低減係数 rho_s（Ament）。"""
    g = (2.0 * math.pi * sigma_m * math.sin(psi)) / lam
    return math.exp(-2.0 * g * g)


def null_depth_db(gamma_eff: float) -> float:
    """実効反射係数 |Gamma_eff| のときのヌルの深さ [dB]（直接波を 0dB 基準）。"""
    residual = abs(1.0 - abs(gamma_eff))
    if residual <= 0.0:
        return float("inf")
    return -20.0 * math.log10(residual)


def detrended_rms(x: np.ndarray, values: np.ndarray) -> float:
    """一次傾斜を除いた残差の RMS [m]（区間の「うねり」＝ sigma_h）。

    ⚠️ **`x` は標本の連番ではなく実距離**（B-234）＝標本は等間隔とは限らない
    （B-150）ので、標本の並び順を等間隔と仮定して `np.arange(n)` を回帰の
    x 軸にすると、完全な直線斜面（平面）でも間隔の粗密が傾きの誤差として
    residual に漏れ、RMS が偽の粗さを示す。
    """
    n = len(values)
    if n < 3:
        return 0.0
    x = np.asarray(x, dtype=float)
    mean_x = x.mean()
    mean_y = float(values.mean())
    sxx = float(np.sum((x - mean_x) ** 2))
    if sxx <= 0.0:
        return 0.0
    sxy = float(np.sum((x - mean_x) * (values - mean_y)))
    slope = sxy / sxx
    res = values - (mean_y + slope * (x - mean_x))
    return float(np.sqrt(np.mean(res ** 2)))


def compute_two_ray_envelope(
    terrain,
    h_tx: float,
    h_rx: float,
    freq_mhz: float,
) -> "TwoRayEnvelope | None":
    """固定回線（両端に高さがある）を前提に、地面反射の振幅包絡線を見積もる。

    ⚠️ **ドローンのように反射点が端へ寄る幾何は対象外**＝`MIN_ARM_M` 未満なら
    `None` を返す（RadioSim for Drone 側は地表種別をユーザーが選ぶ別実装になる）。

    Args:
        terrain:  `models.TerrainProfile`（`raw_elevs` / `d_km_axis` / `horiz_dist_km` を使う）
        h_tx:     TX アンテナの地上高 [m]
        h_rx:     RX アンテナの地上高 [m]
        freq_mhz: 周波数 [MHz]
    """
    dist_m = float(terrain.horiz_dist_km) * 1000.0
    if dist_m <= 0.0 or h_tx <= 0.0 or h_rx <= 0.0:
        return None

    d1, psi = specular_point(dist_m, float(h_tx), float(h_rx))
    d2 = dist_m - d1
    if d1 < MIN_ARM_M or d2 < MIN_ARM_M:
        return None
    if psi <= 0.0:
        return None

    lam = C_LIGHT / (float(freq_mhz) * 1e6)

    # 反射点周辺 ±SIGMA_WINDOW_HALF_M の標本から sigma_h を自動推定する。
    # ⚠️ 標本は等間隔とは限らない（B-150）ので、距離ウィンドウで選ぶ（インデックスウィンドウは使わない）。
    d_m_axis = np.asarray(terrain.d_km_axis, dtype=float) * 1000.0
    in_window = np.abs(d_m_axis - d1) <= SIGMA_WINDOW_HALF_M
    window_x = d_m_axis[in_window]
    window_elevs = np.asarray(terrain.raw_elevs, dtype=float)[in_window]
    valid = ~np.isnan(window_elevs)
    window_x = window_x[valid]
    window_elevs = window_elevs[valid]
    if len(window_elevs) < 3:
        return None
    sigma_h_m = detrended_rms(window_x, window_elevs)

    sigma_max = rayleigh_sigma_max(psi, lam)
    roughness = "smooth" if sigma_h_m < sigma_max else "rough"

    rho_s = specular_reduction(sigma_h_m, psi, lam)
    gamma_eff = GAMMA0 * rho_s
    depth_db = min(null_depth_db(gamma_eff), NULL_DEPTH_CAP_DB)

    return TwoRayEnvelope(
        frac                 = d1 / dist_m,
        sigma_h_m            = sigma_h_m,
        grazing_deg          = math.degrees(psi),
        roughness            = roughness,
        null_depth_db        = depth_db,
        constructive_gain_db = CONSTRUCTIVE_GAIN_DB,
    )
