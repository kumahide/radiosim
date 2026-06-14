"""
models.py
=========
純粋な計算ロジックのみ。副作用ゼロ・GUI依存ゼロ。
numpy / math のみに依存し、matplotlib / tkinter / requests を一切 import しない。

構成:
  TerrainProfile  : 地形プロファイル・地球曲率補正・ハバーサイン距離
  PropagationResult (dataclass): 伝搬計算の中間値をまとめて保持
  LinkBudgetResult  (dataclass): リンクバジェット最終結果
  calculate_terrain_profile()  : TerrainProfile を生成
  calculate_propagation()      : Fresnel / 回折損 / 植生減衰を計算
                                  diff_method="single"|"deygout" で切り替え
  calculate_rain_loss()        : 降雨減衰 (ITU-R P.838-3)
  calculate_gas_loss()         : 大気減衰 (ITU-R P.676-13 Annex 2)
  calculate_link_budget()      : EIRP → P_rx → Act Margin を計算

回折モデル:
  single  : 単一障害物 Fresnel-Kirchhoff（従来）
  deygout : Deygout 法（ITU-R P.526 準拠、多重回折対応）
            最大遮蔽点を主障害物として再帰的に分割し損失を加算する。
            再帰打ち切り条件: ν < NU_THRESHOLD (-0.8) または区間幅 < MIN_SEGMENT_M。
"""
# NOTE:
# 古典的な Deygout 実装では、
# 主障害物の頂点高度（peak height）を
# 再帰区間の端点として使用する流儀も存在する。
#
# しかし高解像度 DEM や植生レイヤを含む実地形では、
# サブ区間 LoS が段階的に持ち上がり、
# ν パラメータと回折損が再帰的に過大化しやすい。
#
# その結果、多重回折損が非現実的に増大し、
# 見通し地形でも数十?数百 dB の異常値を
# 生じるケースがある。
#
# 本実装では数値安定性と実用的な伝搬特性を優先し、
# 主障害物位置における「元の LoS 高度」を
# 再帰端点として使用する。
#
# これにより：
#   - 見通し地形で 0 dB に自然収束
#   - 過大 pessimistic を抑制
#   - 高サンプル密度でも安定
#   - 実運用リンク設計に近い挙動
#
# を得ることを目的としている。

import bisect
import math
from dataclasses import dataclass

import numpy as np


# ============================================================
# 環境区分
# ============================================================

# 環境区分ごとの Env Loss 係数テーブル
# {env_type: (base_dB, blocked_coeff, dist_coeff, diff_coeff, min_dB, max_dB)}
#
#   base_dB      : ベース損失（マルチパス・散乱の最低限）
#   blocked_coeff: Fresnel 遮蔽率 [%] に対する係数
#   dist_coeff   : スラント距離 [km] に対する係数
#   diff_coeff   : 回折損 [dB] に対する係数
#   min_dB       : Env Loss の下限 [dB]
#   max_dB       : Env Loss の上限 [dB]
#
# 根拠:
#   urban   : 高密度建物・多重反射。ベース高め、遮蔽と距離の影響大。
#   suburban: 中密度。標準的な値。
#   rural   : 農村・開けた土地。反射体が少なくベース低め。
#   los     : 見通し（水上・平地）。散乱・反射が最小。
ENV_COEFFS: dict[str, tuple] = {
    #           base  blk   dist  diff  min   max
    # veg_c を除去: Veg Loss は独立項として total_loss に加算するため
    "urban"   : (10.0, 0.08, 1.20, 0.15, 6.0, 30.0),
    "suburban": ( 6.0, 0.05, 0.80, 0.10, 3.0, 30.0),
    "rural"   : ( 4.0, 0.03, 0.50, 0.08, 2.0, 25.0),
    "los"     : ( 2.0, 0.01, 0.30, 0.05, 1.0, 15.0),
}

# ランチャー表示用ラベル → env_type キーのマッピング
ENV_LABELS: dict[str, str] = {
    "Urban"   : "urban",
    "Suburban": "suburban",
    "Rural"   : "rural",
    "LoS"     : "los",
}

# デフォルト環境区分
ENV_DEFAULT: str = "los"


# ============================================================
# データクラス
# ============================================================
@dataclass
class TerrainProfile:
    """地形プロファイルと座標情報。"""
    raw_elevs:         np.ndarray   # 生標高 [m]
    elevs_with_curve:  np.ndarray   # 地球曲率補正済み標高 [m]
    d_km_axis:         np.ndarray   # 各サンプル点の水平距離 [km]
    horiz_dist_km:     float        # 水平総距離 [km]
    num_samples:       int
    earth_k:           float = 4/3  # 等価地球半径係数（ライスKとは無関係）


@dataclass
class PropagationResult:
    """伝搬計算の中間値。"""
    diff_loss:     float   # 回折損 [dB]
    veg_loss:      float   # 植生減衰 [dB]
    env_loss:      float   # 環境損失推定値 [dB]
    rain_loss:     float   # 降雨減衰 [dB]  (ITU-R P.838-3)
    gas_loss:      float   # 大気減衰 [dB]  (ITU-R P.676-13 Annex 2)
    blocked_ratio: float   # Fresnel 第1ゾーン遮蔽率 [%]
    slant_dist_km: float   # スラント距離 [km]
    current_k:     float   # 推定ライスKファクター（表示専用、計算不使用）
    diff_method:   str     # 使用した回折モデル ("single" | "deygout")
    env_type:      str     # 環境区分 ("urban"|"suburban"|"rural"|"los")


@dataclass
class LinkBudgetResult:
    """リンクバジェット最終結果。"""
    eirp:          float
    fspl:          float
    diff_loss:     float
    veg_loss:      float
    env_loss:      float
    rain_loss:     float   # 降雨減衰 [dB]
    gas_loss:      float   # 大気減衰 [dB]
    total_loss:    float
    p_rx:          float
    actual_margin: float
    status:        str          # "OK" or "NG"
    # 環境情報（表示用）
    current_k:     float
    blocked_ratio: float
    slant_dist_km: float
    diff_method:   str          # 使用した回折モデル ("single" | "deygout")
    env_type:      str          # 環境区分 ("urban"|"suburban"|"rural"|"los")


# ============================================================
# 地形プロファイル
# ============================================================
def horizontal_distance_km(
    lat_tx: float, lon_tx: float, lat_rx: float, lon_rx: float
) -> float:
    """2地点間の水平距離 [km]（haversine, 球面半径 6371km）。"""
    R_earth = 6371.0
    dlat = math.radians(lat_rx - lat_tx)
    dlon = math.radians(lon_rx - lon_tx)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat_tx))
        * math.cos(math.radians(lat_rx))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * R_earth * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def calculate_terrain_profile(
    raw_elevs: np.ndarray,
    lat_tx: float,
    lon_tx: float,
    lat_rx: float,
    lon_rx: float,
    earth_k: float = 4 / 3,
) -> TerrainProfile:
    """
    生標高配列から TerrainProfile を生成する。

    地球曲率補正に等価地球半径係数 earth_k を使用する。
    earth_k が大きいほど等価地球半径が大きくなり、地形の見かけの凸量が減る。
    ライスKファクター（SimParams.k_factor）とは別物。

    Args:
        earth_k: 等価地球半径係数。標準大気 = 4/3 ≈ 1.333。
                 ダクト条件では大きく（10 以上）、負の屈折では小さくなる。
                 デフォルトは標準大気（4/3）。
    """
    R_earth = 6371.0
    Re      = R_earth * max(earth_k, 0.1)  # 0 除算防止

    horiz_dist_km = horizontal_distance_km(lat_tx, lon_tx, lat_rx, lon_rx)

    num_samples = len(raw_elevs)
    d_km_axis   = np.linspace(0, horiz_dist_km, num_samples)

    curvature_correction = (
        d_km_axis * (horiz_dist_km - d_km_axis)
    ) / (2 * Re) * 1000

    return TerrainProfile(
        raw_elevs        = raw_elevs,
        elevs_with_curve = raw_elevs + curvature_correction,
        d_km_axis        = d_km_axis,
        horiz_dist_km    = horiz_dist_km,
        num_samples      = num_samples,
        earth_k          = earth_k,
    )


# ============================================================
# Fresnel 第1ゾーン半径（共有ユーティリティ）
# ============================================================
def fresnel_zone_radii(
    d_km_axis: np.ndarray,
    horiz_dist_km: float,
    freq_mhz: float,
) -> np.ndarray:
    """
    Fresnel 第1ゾーン半径 [m] を各サンプル点で返す（ITU-R P.526）。

    端点で分母がゼロになるのを防ぐため d1・d2 を最低 1m でクリップする。
    """
    lam = 299792458 / (freq_mhz * 1e6)
    d_m = d_km_axis * 1000
    tot = horiz_dist_km * 1000
    d1  = np.maximum(d_m, 1.0)
    d2  = np.maximum(tot - d_m, 1.0)
    return np.nan_to_num(np.sqrt(lam * d1 * d2 / (d1 + d2)))


# ============================================================
# 伝搬計算
# ============================================================
def calculate_propagation(
    terrain: TerrainProfile,
    h_tx: float,
    h_rx: float,
    freq_mhz: float,
    veg_h: float,
    initial_k: float,
    diff_method: str = "deygout",
    env_type: str = ENV_DEFAULT,
    rain_rate: float = 0.0,
) -> PropagationResult:
    """
    Fresnel 第1ゾーン・回折損・植生減衰・Env Loss・降雨減衰・大気減衰を計算する。

    Args:
        diff_method: "single" = 単一障害物 Fresnel-Kirchhoff（従来）
                     "deygout" = Deygout 法（ITU-R P.526、多重回折対応）
        env_type   : "urban" | "suburban" | "rural" | "los"
        rain_rate  : 降雨率 [mm/h]（0 = 降雨なし）
    """
    elevs = terrain.elevs_with_curve
    d_km  = terrain.d_km_axis
    N     = terrain.num_samples

    tx_abs = float(elevs[0])  + h_tx
    rx_abs = float(elevs[-1]) + h_rx

    los_vals = np.linspace(tx_abs, rx_abs, N)

    lam           = 299792458 / (freq_mhz * 1e6)
    d_m_axis      = d_km * 1000
    total_dist_m  = terrain.horiz_dist_km * 1000

    # 端点発散対策: d1・d2 を最低 1m でクリップ（sqrt_term の分母保護）
    d1 = np.maximum(d_m_axis, 1.0)
    d2 = np.maximum(total_dist_m - d_m_axis, 1.0)

    f1 = fresnel_zone_radii(d_km, terrain.horiz_dist_km, freq_mhz)

    # veg_h は validate_config で 0〜100m に制限されているため
    # elevs + veg_h >= elevs が常に成立し np.maximum は不要。
    obstruction_surface = elevs + veg_h

    # ── 回折損の計算（モデル切り替え） ──────────────────────────
    if diff_method == "deygout":
        diff_loss = _deygout_loss(
            obstruction_surface, d_m_axis, tx_abs, rx_abs, lam
        )
    else:
        # 従来の単一障害物モデル
        clearance = obstruction_surface - los_vals
        sqrt_term = np.sqrt(
            np.maximum(0, 2 * (d1 + d2) / (lam * d1 * d2 + 1e-9))
        )
        sqrt_term = np.nan_to_num(sqrt_term)
        v_params  = np.nan_to_num(clearance * sqrt_term)

        if len(v_params) == 0 or np.all(np.isnan(v_params)):
            diff_loss = 0.0
        else:
            v_max     = float(np.nanmax(v_params))
            diff_loss = (
                0.0 if v_max < 0
                else _diffraction_loss_fk(v_params[int(np.nanargmax(v_params))])
            )

    # ── Fresnel 遮蔽率（両モデル共通、表示用） ──────────────────
    fresnel_lower = los_vals - f1
    f1_intrusion  = obstruction_surface - fresnel_lower
    worst_idx     = int(np.argmax(f1_intrusion))
    safe_f1_w     = max(float(f1[worst_idx]), 1e-6)
    blocked_ratio = max(0.0, (f1_intrusion[worst_idx] / safe_f1_w) * 100)

    # 植生減衰（植生頂点 = 地表高 + veg_h を LoS 線と比較）
    veg_top  = elevs + veg_h
    veg_loss = _vegetation_loss(
        veg_top, los_vals, f1, freq_mhz, terrain.horiz_dist_km, N
    )

    slant_dist_km = math.sqrt(
        terrain.horiz_dist_km ** 2
        + ((tx_abs - rx_abs) / 1000) ** 2
    )

    # Env Loss（環境区分別推定）
    env_loss  = _env_loss(blocked_ratio, slant_dist_km, diff_loss, env_type)
    # current_k: 表示・参考用のライスファクター推定値。
    # ライスファクター K は見通し成分と散乱成分の電力比 [dB] であり、
    # 障害物による回折損が増えると見通し成分が失われ K が低下する。
    # diff_loss / 3 は経験的な低下量の近似係数（計算には使用しない）。
    current_k = max(0.0, initial_k - (diff_loss / 3))

    # 降雨減衰・大気減衰
    rain_loss = calculate_rain_loss(freq_mhz, slant_dist_km, rain_rate)
    gas_loss  = calculate_gas_loss(freq_mhz, slant_dist_km)

    return PropagationResult(
        diff_loss     = diff_loss,
        veg_loss      = veg_loss,
        env_loss      = env_loss,
        rain_loss     = rain_loss,
        gas_loss      = gas_loss,
        blocked_ratio = blocked_ratio,
        slant_dist_km = slant_dist_km,
        current_k     = current_k,
        diff_method   = diff_method,
        env_type      = env_type,
    )


# ──────────────────────────────────────────────────────────────
# 回折損ヘルパー
# ──────────────────────────────────────────────────────────────

# Deygout 再帰の打ち切り閾値
_NU_THRESHOLD:    float = -0.8   # これ以下は回折損 0 dB として打ち切る（ITU-R P.526 の見通し判定相当）
_MIN_SEGMENT_M:   float = 50.0   # セグメント幅がこれ未満なら打ち切る
_MAX_DEPTH:       int   = 20     # 再帰上限（無限ループ防止）


def _diffraction_loss_fk(v: float) -> float:
    """
    Fresnel-Kirchhoff 回折損 J(ν)。
    ν <= -0.8 のとき損失なし（完全見通し）。
    """
    if v <= _NU_THRESHOLD:
        return 0.0
    return float(
        6.9
        + 20 * math.log10(
            math.sqrt((v - 0.1) ** 2 + 1) + v - 0.1
        )
    )



def _deygout_loss(
    obs_surface: np.ndarray,
    d_m_axis:    np.ndarray,
    tx_abs:      float,
    rx_abs:      float,
    lam:         float,
    depth:       int = 0,
) -> float:
    """
    Deygout 法による多重回折損 [dB]（ITU-R P.526 準拠）。

    アルゴリズム:
      1. 区間内で ν が最大の点（主障害物 Pm）を探す。
      2. ν(Pm) < _NU_THRESHOLD ならこの区間の損失は 0 dB。
      3. そうでなければ J(ν(Pm)) を加算し、
         左区間 [TX, Pm] と右区間 [Pm, RX] を再帰処理する。

    Args:
        obs_surface : 障害物面（地形 + 植生）の絶対高度 [m]（全区間）
        d_m_axis    : 各サンプル点の TX からの水平距離 [m]（全区間）
        tx_abs      : TX アンテナ絶対高度 [m]
        rx_abs      : RX アンテナ絶対高度 [m]
        lam         : 波長 [m]
        depth       : 現在の再帰深さ（上限 _MAX_DEPTH）
    """
    N = len(obs_surface)
    if N < 3 or depth >= _MAX_DEPTH:
        return 0.0

    d_start = float(d_m_axis[0])
    d_end   = float(d_m_axis[-1])
    span_m  = d_end - d_start

    if span_m < _MIN_SEGMENT_M:
        return 0.0

    # ── LoS（直線）高さを各サンプル点で計算 ─────────────────────
    los = np.linspace(tx_abs, rx_abs, N)

    # ── 各サンプル点の ν を計算 ──────────────────────────────────
    # d_m_axis は区間内の絶対距離なので TX 基準に正規化する
    d_rel   = d_m_axis - d_start          # TX からの相対距離 [m]
    d1_arr  = np.maximum(d_rel, 1.0)
    d2_arr  = np.maximum(span_m - d_rel, 1.0)

    h_obs   = obs_surface - los           # 正=遮蔽
    denom   = lam * d1_arr * d2_arr / (d1_arr + d2_arr)
    denom   = np.maximum(denom, 1e-9)
    v_arr   = h_obs * np.sqrt(2.0 / denom)
    v_arr   = np.nan_to_num(v_arr)

    # 端点を除いた内側サンプルのみを主障害物候補とする
    if N <= 2:
        return 0.0
    inner_v   = v_arr[1:-1]
    peak_inner = int(np.argmax(inner_v))
    peak_idx   = peak_inner + 1           # 全体インデックスに変換
    v_peak     = float(v_arr[peak_idx])

    # ν < 閾値 → この区間は見通し
    if v_peak <= _NU_THRESHOLD:
        return 0.0

    # 主障害物の回折損
    loss = _diffraction_loss_fk(v_peak)

    # 主障害物位置での LoS 高度（再帰の端点として使用）
    # ※ 障害物頂点高度ではなく LoS 上の点を使うことで
    #    サブ区間の LoS が元の LoS に対して連続的になる
    los_at_peak = float(los[peak_idx])

    # ── 左区間（TX 〜 主障害物）の再帰 ──────────────────────────
    if peak_idx >= 2:
        loss += _deygout_loss(
            obs_surface = obs_surface[:peak_idx + 1],
            d_m_axis    = d_m_axis[:peak_idx + 1],
            tx_abs      = tx_abs,
            rx_abs      = los_at_peak,
            lam         = lam,
            depth       = depth + 1,
        )

    # ── 右区間（主障害物 〜 RX）の再帰 ──────────────────────────
    if peak_idx <= N - 3:
        loss += _deygout_loss(
            obs_surface = obs_surface[peak_idx:],
            d_m_axis    = d_m_axis[peak_idx:],
            tx_abs      = los_at_peak,
            rx_abs      = rx_abs,
            lam         = lam,
            depth       = depth + 1,
        )

    return float(loss)


def _vegetation_loss(
    veg_top: np.ndarray,
    los_vals: np.ndarray,
    f1: np.ndarray,
    freq_mhz: float,
    horiz_dist_km: float,
    num_samples: int,
) -> float:
    """
    植生頂点が LoS 線に侵入した深さから減衰量 [dB] を推定する。
    上限 45 dB。

    Args:
        veg_top:  植生頂点高度配列 = 地表高 + veg_h  [m]
        los_vals: LoS 線高度配列 [m]
        f1:       Fresnel 第1ゾーン半径配列 [m]
    """
    freq_ghz = freq_mhz / 1000.0
    if freq_ghz < 1.0:
        coeff = 0.12 * (freq_ghz ** 0.5)
    elif freq_ghz < 6.0:
        coeff = 0.20 * (freq_ghz ** 0.7)
    else:
        coeff = 0.35 * (freq_ghz ** 0.9)

    sample_spacing_m = (horiz_dist_km * 1000) / max(num_samples - 1, 1)

    # 植生頂点が LoS 線を超えた量（正値 = LoS に侵入している）
    penetration   = veg_top - los_vals
    intrusion_depth = np.maximum(0, penetration)

    # Fresnel 半径で正規化して重み付け（0〜1）
    f1_safe    = np.maximum(f1, 1e-6)
    veg_weight = np.clip(intrusion_depth / f1_safe, 0, 1.0)

    veg_effective_length = np.sum(veg_weight) * sample_spacing_m
    return min(veg_effective_length * coeff, 45.0)


def _env_loss(
    blocked_ratio: float,
    slant_dist_km: float,
    diff_loss: float,
    env_type: str = ENV_DEFAULT,
) -> float:
    """
    環境損失推定値（マルチパス・大気変動・散乱等の経験的近似）。

    Veg Loss は total_loss に独立項として加算されるため、ここには含めない。

    Args:
        env_type: "urban" | "suburban" | "rural" | "los"
                  ENV_COEFFS で定義された環境区分。未知の値は ENV_DEFAULT ("los") にフォールバック。
    """
    base, blk_c, dist_c, diff_c, min_db, max_db = ENV_COEFFS.get(
        env_type, ENV_COEFFS[ENV_DEFAULT]
    )
    env = base
    env += min(blocked_ratio, 100.0) * blk_c
    env += slant_dist_km * dist_c
    env += diff_loss  * diff_c
    return float(max(min_db, min(max_db, env)))


# ============================================================
# 降雨減衰  ITU-R P.838-3
# ============================================================

# 係数テーブル (freq_GHz, kH, aH, kV, aV)
# 出典: ITU-R P.838-3 Table 1 & 2（水平・垂直偏波）
# 対数補間用に log10(freq) を使う
_P838_TABLE: list[tuple] = [
    #  GHz      kH        aH       kV        aV
    (  1.0, 0.0000387, 0.912,  0.0000352, 0.880),
    (  2.0, 0.000154,  0.963,  0.000138,  0.923),
    (  4.0, 0.000650,  1.121,  0.000591,  1.075),
    (  6.0, 0.00175,   1.308,  0.00155,   1.265),
    (  7.0, 0.00301,   1.332,  0.00265,   1.312),
    (  8.0, 0.00454,   1.327,  0.00395,   1.310),
    ( 10.0, 0.0101,    1.276,  0.00887,   1.264),
    ( 12.0, 0.0188,    1.217,  0.0168,    1.200),
    ( 15.0, 0.0367,    1.154,  0.0335,    1.128),
    ( 20.0, 0.0751,    1.099,  0.0691,    1.065),
    ( 25.0, 0.124,     1.061,  0.113,     1.030),
    ( 30.0, 0.187,     1.021,  0.167,     1.000),
    ( 35.0, 0.263,     0.979,  0.233,     0.963),
    ( 40.0, 0.350,     0.939,  0.310,     0.929),
]


def _p838_coeffs(freq_mhz: float) -> tuple[float, float]:
    """
    P.838-3 の係数 k, α を対数補間で返す（水平偏波固定）。
    範囲外は端値でクランプ。
    """
    freq_ghz = freq_mhz / 1000.0
    freqs = [r[0] for r in _P838_TABLE]

    if freq_ghz <= freqs[0]:
        row = _P838_TABLE[0]
        return row[1], row[2]
    if freq_ghz >= freqs[-1]:
        row = _P838_TABLE[-1]
        return row[1], row[2]

    # 対数補間
    idx = bisect.bisect_left(freqs, freq_ghz)
    f0, kH0, aH0 = freqs[idx-1], _P838_TABLE[idx-1][1], _P838_TABLE[idx-1][2]
    f1, kH1, aH1 = freqs[idx],   _P838_TABLE[idx][1],   _P838_TABLE[idx][2]

    t = math.log10(freq_ghz / f0) / math.log10(f1 / f0)
    k = 10 ** (math.log10(kH0) + t * (math.log10(kH1) - math.log10(kH0)))
    a = aH0 + t * (aH1 - aH0)
    return k, a


def calculate_rain_loss(
    freq_mhz: float,
    slant_dist_km: float,
    rain_rate: float,
) -> float:
    """
    降雨減衰 [dB]（ITU-R P.838-3）。

    Args:
        freq_mhz     : 周波数 [MHz]
        slant_dist_km: スラント距離 [km]
        rain_rate    : 降雨率 [mm/h]（0 なら 0 dB を返す）

    Notes:
        - 偏波: 水平（H）固定。
                H は V より減衰が大きい傾向があり、
                スクリーニング用途では安全側の推定になる。
                V 列は _P838_TABLE に定義済みだが現在未使用。
        - 等価路程長補正: 地上短距離用途では d_eff = d_slant で近似
        - 1 GHz 未満: 降雨減衰は実用上無視できるため 0 dB
    """
    if rain_rate <= 0.0 or freq_mhz < 1000.0:
        return 0.0
    k, alpha = _p838_coeffs(freq_mhz)
    gamma_r  = k * (rain_rate ** alpha)   # 比減衰量 [dB/km]
    return float(gamma_r * slant_dist_km)


# ============================================================
# 大気減衰  ITU-R P.676-13 Annex 2（簡易式）
# ============================================================

def calculate_gas_loss(
    freq_mhz: float,
    slant_dist_km: float,
    temperature_c: float = 20.0,
    water_vapor_density: float = 7.5,
) -> float:
    """
    大気減衰 [dB]（ITU-R P.676-13 Annex 2 簡易式）。

    酸素吸収と水蒸気吸収の比減衰量を計算し、スラント距離をかける。

    Args:
        freq_mhz            : 周波数 [MHz]
        slant_dist_km       : スラント距離 [km]
        temperature_c       : 気温 [℃]（デフォルト 20℃）
        water_vapor_density : 水蒸気密度 [g/m³]（デフォルト 7.5 g/m³）

    Notes:
        - 有効範囲: 1〜350 GHz（P.676-13 Annex 2）
        - 1 GHz 未満は誤差が大きくなるため 0 dB を返す
        - 60 GHz 付近の酸素吸収ピーク（約 15 dB/km）も再現する
    """
    freq_ghz = freq_mhz / 1000.0
    if freq_ghz < 1.0:
        return 0.0

    T  = temperature_c + 273.15   # 絶対温度 [K]
    p  = 1013.25                   # 標準大気圧 [hPa]
    rho = water_vapor_density      # 水蒸気密度 [g/m³]

    # ── 酸素吸収（P.676-13 Annex 2 式 (3)）──────────────────────
    f  = freq_ghz
    r_p = p / 1013.25
    r_t = 288.15 / T

    # 式 (3a)
    # NOTE: (54.0 - f) は f > 54 GHz で負になるため abs() で実数化する。
    #       P.676-13 Annex 2 の経験式は 54 GHz 付近の酸素吸収ピークを
    #       近似するもので、高周波側では絶対値を取るのが正しい扱い。
    gamma_O2 = (
        (7.2 * r_t**2.8) / (f**2 + 0.34 * r_p**2 * r_t**1.6)
        + (0.62 * _xi3(r_p)) / (abs(54.0 - f) ** (1.16 * _xi1(r_p)) + 0.83 * _xi2(r_p))
    ) * f**2 * r_p**2 * r_t**3 * 1e-3

    # ── 水蒸気吸収（P.676-13 Annex 2 式 (4)）───────────────────
    eta1 = 0.955 * r_p * r_t**0.68 + 0.006 * rho
    eta2 = 0.735 * r_p * r_t**0.50 + 0.0353 * r_t**4 * rho

    gamma_H2O = (
        (3.98 * eta1 * math.exp(2.23 * (1 - r_t)))
        / ((f - 22.235)**2 + 9.42 * eta1**2) * _g(f, 22.0)
        + (11.96 * eta1 * math.exp(0.7 * (1 - r_t)))
        / ((f - 183.310)**2 + 11.14 * eta1**2)
        + (0.081 * eta1 * math.exp(6.44 * (1 - r_t)))
        / ((f - 321.226)**2 + 6.29 * eta1**2)
        + (3.66 * eta1 * math.exp(1.6 * (1 - r_t)))
        / ((f - 325.153)**2 + 9.22 * eta1**2)
        + (25.37 * eta1 * math.exp(1.09 * (1 - r_t)))
        / ((f - 380.0)**2 + 1e-6)
        + (17.4 * eta1 * math.exp(1.46 * (1 - r_t)))
        / ((f - 448.0)**2 + 1e-6)
        + (844.6 * eta1 * math.exp(0.17 * (1 - r_t)))
        / ((f - 557.0)**2 + 1e-6) * _g(f, 557.0)
        + (290.0 * eta1 * math.exp(0.41 * (1 - r_t)))
        / ((f - 752.0)**2 + 1e-6) * _g(f, 752.0)
        + (8.3328e4 * eta2 * math.exp(0.99 * (1 - r_t)))
        / ((f - 1780.0)**2 + 1e-6) * _g(f, 1780.0)
    ) * f**2 * r_t**2.5 * rho * 1e-4

    gamma_total = max(0.0, gamma_O2) + max(0.0, gamma_H2O)
    return float(gamma_total * slant_dist_km)


def _xi1(r_p: float) -> float:
    return _phi(r_p, 1.0, 0.0717, -1.8132, 0.0156, -1.6515)

def _xi2(r_p: float) -> float:
    return _phi(r_p, 1.0, 0.5146, -4.6368, -0.1921, -5.7416)

def _xi3(r_p: float) -> float:
    return _phi(r_p, 1.0, 0.3026, -4.4511, -0.1896, -4.8662)

def _phi(r_p: float, r_t: float, a: float, b: float, c: float, d: float) -> float:
    return r_p**a * r_t**b * math.exp(c * (1 - r_p) + d * (1 - r_t))

def _g(f: float, f_i: float) -> float:
    """P.676-13 式 (4) の線形形状因子 g(f, f_i)。"""
    return 1.0 + ((f - f_i) / (f + f_i)) ** 2


# ============================================================
# リンクバジェット
# ============================================================
def calculate_link_budget(
    prop: PropagationResult,
    freq_mhz: float,
    p_tx: float,
    gain_tx: float,
    gain_rx: float,
    sens: float,
) -> LinkBudgetResult:
    """
    伝搬計算結果からリンクバジェットを計算する。

    構造:
        EIRP       = P_tx + G_tx
        total_loss = FSPL + Diff Loss + Veg Loss + Env Loss
                   + Rain Loss + Gas Loss
        P_rx       = EIRP + G_rx - total_loss
        Act Margin = P_rx - Sensitivity
    """
    eirp = p_tx + gain_tx

    fspl = (
        20 * math.log10(max(1.0, prop.slant_dist_km * 1000))
        + 20 * math.log10(freq_mhz * 1e6)
        - 147.55
    )

    total_loss = (
        fspl
        + prop.diff_loss
        + prop.veg_loss
        + prop.env_loss
        + prop.rain_loss
        + prop.gas_loss
    )
    p_rx          = eirp + gain_rx - total_loss
    actual_margin = p_rx - sens
    status        = "OK" if actual_margin >= 0 else "NG"

    return LinkBudgetResult(
        eirp          = eirp,
        fspl          = fspl,
        diff_loss     = prop.diff_loss,
        veg_loss      = prop.veg_loss,
        env_loss      = prop.env_loss,
        rain_loss     = prop.rain_loss,
        gas_loss      = prop.gas_loss,
        total_loss    = total_loss,
        p_rx          = p_rx,
        actual_margin = actual_margin,
        status        = status,
        current_k     = prop.current_k,
        blocked_ratio = prop.blocked_ratio,
        slant_dist_km = prop.slant_dist_km,
        diff_method   = prop.diff_method,
        env_type      = prop.env_type,
    )
