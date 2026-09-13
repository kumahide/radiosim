"""
sensitivity.py
==============
既存パイプライン（`models.calculate_terrain_profile` / `calculate_propagation` /
`calculate_link_budget`）を摂動条件で数回まわし、余裕度[dB]の**幅**を出す層。

🔑 **モデルは1行も変えない**（ロードマップ 3.4 決定＝「モデル無変更で測るだけ」）。
新しい式は増やさず、既存の純粋関数を素通しで呼ぶだけ＝採否の判断（3.5）はここでは
行わない。`core/diffraction.py`（B-130 分割）と同じ位置づけの独立モジュール
（`models.py` は残り行数が少なく、かつこの層は「計算の組み合わせ」であって
「新しい物理」ではないので models.py には足さない）。

軸（ロードマップ 3.4 段4 で確定）:
  DEM 標高±／植生高±／環境区分1段／回折 single⇄bullington／
  回折+植生の合成（B-132・和⇄大きいほう）／解像度（B-128・要 DEM 再取得）／
  地面反射の包絡線（`core/ground_reflection.py`）。
  多ホップは「全体の min」だけでなく「悲観条件でどのホップが律速か（argmin）」も出す。

この段（段4）は**計算のみ**。帳票への表示配線は段6。
"""
import dataclasses
from dataclasses import dataclass
from typing import Callable

import numpy as np

from core import ground_reflection
from core import models
from core import terrain_grid

#: DEM 標高の摂動量 [m]。根拠: `experiments/phase0_survey_accuracy.py` の
#: 「10m メッシュ DEM 読み取り誤差 ±3.00m」（ユーザー確認済み・2026-09-13）。
DEM_PERTURB_M: float = 3.0

#: 植生高の摂動率。絶対量でなく相対値にする理由＝低木〜高木まで一律の絶対量では
#: 非現実的な幅になる（ユーザー確認済み・2026-09-13）。
VEG_PERTURB_FRAC: float = 0.30


@dataclass
class AxisRange:
    """1軸ぶんの余裕度[dB]の幅。"""
    label:    str
    baseline: float
    low:      float   # margin[dB]（小さい側）
    high:     float   # margin[dB]（大きい側）


@dataclass
class SensitivityResult:
    """1本の回線ぶんの感度計算結果。"""
    baseline_margin:   float
    axes:              "dict[str, AxisRange]"
    ground_reflection: "ground_reflection.TwoRayEnvelope | None"
    resolution:        "AxisRange | None"


@dataclass
class HopInputs:
    """多ホップの1ホップぶんの入力（`compute_multihop_argmin` 用）。"""
    terrain:     models.TerrainProfile
    h_tx:        float
    h_rx:        float
    freq_mhz:    float
    veg_h:       float
    initial_k:   float
    diff_method: str
    env_type:    str
    rain_rate:   float
    p_tx:        float
    gain_tx:     float
    gain_rx:     float
    sens:        float


def _margin_for(
    terrain: models.TerrainProfile,
    h_tx: float, h_rx: float, freq_mhz: float, veg_h: float, initial_k: float,
    diff_method: str, env_type: str, rain_rate: float,
    p_tx: float, gain_tx: float, gain_rx: float, sens: float,
) -> float:
    """既存パイプラインを1回まわして余裕度[dB]だけを返す（新しい式は無い）。"""
    prop = models.calculate_propagation(
        terrain, h_tx, h_rx, freq_mhz, veg_h, initial_k, diff_method, env_type, rain_rate,
    )
    result = models.calculate_link_budget(prop, freq_mhz, p_tx, gain_tx, gain_rx, sens)
    return result.actual_margin


def _margin_for_hop(hop: HopInputs) -> float:
    return _margin_for(
        hop.terrain, hop.h_tx, hop.h_rx, hop.freq_mhz, hop.veg_h, hop.initial_k,
        hop.diff_method, hop.env_type, hop.rain_rate,
        hop.p_tx, hop.gain_tx, hop.gain_rx, hop.sens,
    )


def rebuild_terrain(
    terrain: models.TerrainProfile,
    lat_tx: float, lon_tx: float, lat_rx: float, lon_rx: float,
    raw_elevs: np.ndarray,
) -> models.TerrainProfile:
    """`terrain` と同じ緯度経度・曲率・標本位置のまま、標高だけ差し替えて作り直す。

    🔑 **公開関数にした理由（段6）**＝多ホップの argmin（`report_multihop.py`）が
    「全ホップへ同じ DEM オフセットを一括適用する」ために同じ再構築を要る。
    private のままだと帳票層が同じロジックを書き写すことになる（二重管理）。
    """
    return models.calculate_terrain_profile(
        raw_elevs = raw_elevs,
        lat_tx = lat_tx, lon_tx = lon_tx, lat_rx = lat_rx, lon_rx = lon_rx,
        earth_k = terrain.earth_k,
        frac_axis = terrain.frac_axis,
    )


def shift_dem(terrain: models.TerrainProfile, delta_m: float) -> models.TerrainProfile:
    """`terrain` の標高を一律 `delta_m` だけ底上げ/沈める（緯度経度を使わない軽量版）。

    🔑 **多ホップの argmin（段6・`report_multihop.py`）専用**＝`rebuild_terrain` は
    緯度経度から水平距離・曲率補正を作り直すが、DEM の系統誤差は**曲率と無関係に
    標高だけ動く**ので、既存の曲率補正量（`elevs_with_curve − raw_elevs`、`nan` は
    0 扱い）を保ったまま両方へ同じ量を足すだけでよい。`report.batch.PathResult`
    は緯度経度を `row` 側に持ち `HopInputs` には無いので、これなら座標を
    引き回さずに全ホップへ同じ摂動を一括適用できる。

    `nan`（DEM 取得失敗の標本）はそのまま `nan` を保つ（`fail_pct` の算出源）。
    """
    raw = np.asarray(terrain.raw_elevs, dtype=float)
    curve = np.asarray(terrain.elevs_with_curve, dtype=float)
    curvature_correction = curve - np.where(np.isnan(raw), 0.0, raw)
    new_raw = raw + delta_m
    new_calc_base = np.where(np.isnan(new_raw), 0.0, new_raw)
    return dataclasses.replace(
        terrain,
        raw_elevs        = new_raw,
        elevs_with_curve = new_calc_base + curvature_correction,
    )


def compute_link_sensitivity(
    terrain: models.TerrainProfile,
    lat_tx: float, lon_tx: float, lat_rx: float, lon_rx: float,
    h_tx: float, h_rx: float, freq_mhz: float, veg_h: float, initial_k: float,
    diff_method: str, env_type: str, rain_rate: float,
    p_tx: float, gain_tx: float, gain_rx: float, sens: float,
    *,
    fetch_alt_resolution: "Callable[[str], np.ndarray] | None" = None,
) -> SensitivityResult:
    """1本の回線について、摂動軸ごとの余裕度[dB]の幅を計算する。

    Args:
        lat_tx/lon_tx/lat_rx/lon_rx: `terrain` を再構築する（DEM 標高±・解像度の軸で
            `calculate_terrain_profile` を作り直すため）緯度経度。
        fetch_alt_resolution: 解像度軸を計算する場合のみ渡す。`level` を受け取り
            その解像度の標高配列を返す callable（DEM 再取得＝I/O はこの層に持ち込まず
            呼び出し側が担当する）。`None` なら解像度軸は省く。
    """
    baseline_margin = _margin_for(
        terrain, h_tx, h_rx, freq_mhz, veg_h, initial_k, diff_method, env_type, rain_rate,
        p_tx, gain_tx, gain_rx, sens,
    )

    def margin_with(*, raw_elevs=None, veg_h_v=None, env_type_v=None, diff_method_v=None) -> float:
        t = terrain
        if raw_elevs is not None:
            t = rebuild_terrain(terrain, lat_tx, lon_tx, lat_rx, lon_rx, raw_elevs)
        return _margin_for(
            t, h_tx, h_rx, freq_mhz,
            veg_h if veg_h_v is None else veg_h_v,
            initial_k,
            diff_method if diff_method_v is None else diff_method_v,
            env_type if env_type_v is None else env_type_v,
            rain_rate, p_tx, gain_tx, gain_rx, sens,
        )

    axes: "dict[str, AxisRange]" = {}

    # ── DEM 標高± ──────────────────────────────────────────
    m_lo = margin_with(raw_elevs=np.asarray(terrain.raw_elevs, dtype=float) - DEM_PERTURB_M)
    m_hi = margin_with(raw_elevs=np.asarray(terrain.raw_elevs, dtype=float) + DEM_PERTURB_M)
    axes["dem_elev"] = AxisRange("dem_elev", baseline_margin, min(m_lo, m_hi), max(m_lo, m_hi))

    # ── 植生高± ────────────────────────────────────────────
    # 🔑 入力していない量は振らない（`scope_notes` の `veg_uniform` と同じ原則）。
    if veg_h > 0.0:
        m_lo = margin_with(veg_h_v=veg_h * (1.0 - VEG_PERTURB_FRAC))
        m_hi = margin_with(veg_h_v=veg_h * (1.0 + VEG_PERTURB_FRAC))
        axes["veg_h"] = AxisRange("veg_h", baseline_margin, min(m_lo, m_hi), max(m_lo, m_hi))

    # ── 環境区分1段 ─────────────────────────────────────────
    # models.ENV_KEYS の並びは既に urban(損失大) → … → los(損失小)。
    resolved_env = env_type if env_type in models.ENV_COEFFS else models.ENV_DEFAULT
    idx = models.ENV_KEYS.index(resolved_env)
    neighbors = [models.ENV_KEYS[i] for i in (idx - 1, idx + 1) if 0 <= i < len(models.ENV_KEYS)]
    if neighbors:
        margins = [margin_with(env_type_v=e) for e in neighbors] + [baseline_margin]
        axes["env_type"] = AxisRange("env_type", baseline_margin, min(margins), max(margins))

    # ── 回折 single⇄bullington ─────────────────────────────
    other_method = (
        models.DIFF_METHOD_SINGLE
        if models.normalize_diff_method(diff_method) == models.DIFF_METHOD_MULTI
        else models.DIFF_METHOD_MULTI
    )
    m_other = margin_with(diff_method_v=other_method)
    axes["diff_method"] = AxisRange(
        "diff_method", baseline_margin, min(m_other, baseline_margin), max(m_other, baseline_margin),
    )

    # ── 回折+植生の合成（B-132：和 ⇄ 大きいほう）──────────────
    # 🔑 再計算不要＝baseline の PropagationResult から後処理するだけ。
    prop = models.calculate_propagation(
        terrain, h_tx, h_rx, freq_mhz, veg_h, initial_k, diff_method, env_type, rain_rate,
    )
    baseline_result = models.calculate_link_budget(prop, freq_mhz, p_tx, gain_tx, gain_rx, sens)
    total_loss_max_compose = (
        baseline_result.fspl
        + max(prop.diff_loss, prop.veg_loss)
        + prop.env_loss + prop.rain_loss + prop.gas_loss
    )
    p_rx_max_compose = baseline_result.eirp + gain_rx - total_loss_max_compose
    margin_max_compose = p_rx_max_compose - sens
    axes["diff_veg_compose"] = AxisRange(
        "diff_veg_compose", baseline_margin,
        min(baseline_margin, margin_max_compose), max(baseline_margin, margin_max_compose),
    )

    # ── 解像度（B-128）──────────────────────────────────────
    resolution_axis = None
    if fetch_alt_resolution is not None:
        margins = [baseline_margin]
        for level in terrain_grid.RESOLUTION_KEYS:
            alt_raw = fetch_alt_resolution(level)
            alt_terrain = rebuild_terrain(terrain, lat_tx, lon_tx, lat_rx, lon_rx, alt_raw)
            margins.append(_margin_for(
                alt_terrain, h_tx, h_rx, freq_mhz, veg_h, initial_k,
                diff_method, env_type, rain_rate, p_tx, gain_tx, gain_rx, sens,
            ))
        resolution_axis = AxisRange("resolution", baseline_margin, min(margins), max(margins))

    # ── 地面反射の包絡線 ────────────────────────────────────
    envelope = ground_reflection.compute_two_ray_envelope(terrain, h_tx, h_rx, freq_mhz)

    return SensitivityResult(
        baseline_margin   = baseline_margin,
        axes              = axes,
        ground_reflection = envelope,
        resolution        = resolution_axis,
    )


def compute_multihop_argmin(
    hops: "list[HopInputs]",
    perturb: "Callable[[HopInputs], HopInputs]",
) -> "tuple[int, float]":
    """全ホップへ同じ摂動を適用し直し、(律速ホップの index, その余裕度[dB]) を返す。

    🔑 **全ホップへ同じ向きの摂動を一括適用する**＝DEM の系統誤差・植生高の一律読み
    取り誤差は、経路ごとに独立ではなく全ホップに同じ向きで乗るのが物理的に妥当
    （多ホップの「min の感度」は「感度の min」ではない＝ロードマップ 3.4）。
    """
    perturbed = [perturb(h) for h in hops]
    margins = [_margin_for_hop(h) for h in perturbed]
    idx = int(np.argmin(margins))
    return idx, margins[idx]
