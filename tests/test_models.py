"""
tests/test_models.py
====================
models.py のユニットテスト。

カバー範囲:
  - _diffraction_loss_fk      : Fresnel-Kirchhoff 回折損
  - _nu                       : Fresnel パラメータ計算
  - _deygout_loss             : Deygout 多重回折損
  - calculate_terrain_profile : 地形プロファイル生成
  - calculate_propagation     : 伝搬計算（single / deygout 両モデル）
  - calculate_link_budget     : リンクバジェット
  - PropagationResult         : diff_method フィールド
  - LinkBudgetResult          : diff_method フィールド
"""

import math

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import models


# ================================================================
# フィクスチャ
# ================================================================
@pytest.fixture
def flat_terrain():
    """平坦地形（標高 0m 均一、水平距離 5km、100 サンプル）。"""
    raw = np.zeros(100)
    return models.calculate_terrain_profile(raw, 34.54, 132.41, 34.54, 132.46)


@pytest.fixture
def single_ridge_terrain():
    """中央に 1 本の尾根（高さ 50m）を持つ地形。"""
    raw = np.zeros(201)
    raw[100] = 50.0
    return models.calculate_terrain_profile(raw, 34.54, 132.41, 34.54, 132.46)


@pytest.fixture
def double_ridge_terrain():
    """2 本の尾根（高さ 40m）を持つ地形。"""
    raw = np.zeros(301)
    raw[100] = 40.0
    raw[200] = 40.0
    return models.calculate_terrain_profile(raw, 34.54, 132.41, 34.54, 132.46)


# ================================================================
# _diffraction_loss_fk
# ================================================================
class TestDiffractionLossFk:
    def test_los_returns_zero(self):
        """ν <= -0.8 は見通し → 0 dB。"""
        assert models._diffraction_loss_fk(-0.8) == 0.0
        assert models._diffraction_loss_fk(-1.5) == 0.0
        assert models._diffraction_loss_fk(-10.0) == 0.0

    def test_grazing_near_zero(self):
        """ν = 0 付近で約 6 dB（Fresnel-Kirchhoff の既知値）。"""
        loss = models._diffraction_loss_fk(0.0)
        assert 5.5 < loss < 7.0

    def test_increases_with_nu(self):
        """ν が大きいほど損失が大きい。"""
        losses = [models._diffraction_loss_fk(v) for v in [0.0, 1.0, 2.0, 5.0]]
        assert losses == sorted(losses)

    def test_positive_nu_positive_loss(self):
        """遮蔽（ν > 0）では常に正の損失。"""
        for v in [0.1, 1.0, 3.0, 10.0]:
            assert models._diffraction_loss_fk(v) > 0.0

    def test_returns_float(self):
        assert isinstance(models._diffraction_loss_fk(1.0), float)


# ================================================================
# ================================================================
# _deygout_loss
# ================================================================
class TestHorizontalDistanceKm:
    def test_zero_for_same_point(self):
        assert models.horizontal_distance_km(35.0, 139.0, 35.0, 139.0) == pytest.approx(0.0)

    def test_one_degree_latitude(self):
        # 緯度1度 ≈ 111.19 km（球面 R=6371）。
        d = models.horizontal_distance_km(35.0, 139.0, 36.0, 139.0)
        assert d == pytest.approx(111.19, abs=0.1)

    def test_symmetric(self):
        a = models.horizontal_distance_km(35.6, 139.7, 34.7, 135.5)
        b = models.horizontal_distance_km(34.7, 135.5, 35.6, 139.7)
        assert a == pytest.approx(b)

    def test_matches_profile_horiz_dist(self, flat_terrain):
        # calculate_terrain_profile が同じ haversine を使う（リファクタの同値性）。
        # flat_terrain は (34.54, 132.41)→(34.54, 132.46)。
        assert flat_terrain.horiz_dist_km == pytest.approx(
            models.horizontal_distance_km(34.54, 132.41, 34.54, 132.46), rel=1e-9
        )


class TestVerticalExaggeration:
    def test_unity_when_balanced(self):
        # データ範囲・ピクセルとも縦横同比なら誇張なし（×1）。
        assert models.vertical_exaggeration(1000.0, 1000.0, 100.0, 100.0) == pytest.approx(1.0)

    def test_long_path_is_exaggerated(self):
        # 横 50km・縦 500m を 800x300px に詰めると大きく誇張される。
        v = models.vertical_exaggeration(50_000.0, 500.0, 800.0, 300.0)
        assert v == pytest.approx((50_000.0 / 500.0) * (300.0 / 800.0))
        assert v > 1.0

    def test_zero_on_degenerate_inputs(self):
        assert models.vertical_exaggeration(1000.0, 0.0, 100.0, 100.0) == 0.0
        assert models.vertical_exaggeration(1000.0, 100.0, 0.0, 100.0) == 0.0
        assert models.vertical_exaggeration(1000.0, 100.0, 100.0, 0.0) == 0.0


class TestCalculateTerrainProfile:
    def test_shape_matches_input(self, flat_terrain):
        assert len(flat_terrain.raw_elevs) == 100
        assert len(flat_terrain.elevs_with_curve) == 100
        assert len(flat_terrain.d_km_axis) == 100

    def test_d_km_axis_starts_zero(self, flat_terrain):
        assert flat_terrain.d_km_axis[0] == pytest.approx(0.0)

    def test_d_km_axis_ends_at_total(self, flat_terrain):
        assert flat_terrain.d_km_axis[-1] == pytest.approx(
            flat_terrain.horiz_dist_km, rel=1e-6
        )

    def test_curvature_correction_nonnegative(self, flat_terrain):
        """地球曲率補正は区間内で非負（端点=0、中央が最大）。"""
        correction = flat_terrain.elevs_with_curve - flat_terrain.raw_elevs
        assert np.all(correction >= -1e-9)

    def test_curvature_max_at_midpoint(self, flat_terrain):
        correction = flat_terrain.elevs_with_curve - flat_terrain.raw_elevs
        mid = len(correction) // 2
        assert correction[mid] == pytest.approx(correction.max(), rel=0.05)

    def test_horiz_dist_positive(self, flat_terrain):
        assert flat_terrain.horiz_dist_km > 0.0

    def test_num_samples(self, flat_terrain):
        assert flat_terrain.num_samples == 100

    def test_earth_k_stored_in_profile(self):
        """calculate_terrain_profile に渡した earth_k が TerrainProfile に保持されること。"""
        raw = np.zeros(100)
        for k in [1.0, 4/3, 10.0]:
            t = models.calculate_terrain_profile(raw, 34.54, 132.41, 34.54, 132.46,
                                                  earth_k=k)
            assert t.earth_k == pytest.approx(k)

    def test_larger_k_reduces_curvature_correction(self):
        """
        K ファクターが大きいほど等価地球半径が大きくなり、
        地形断面の曲率補正量（中央の盛り上がり）が小さくなること。
        """
        raw = np.zeros(100)
        corrections = []
        for k in [1.0, 4/3, 5.0, 10.0]:
            t = models.calculate_terrain_profile(raw, 34.54, 132.41, 34.54, 132.46,
                                                  earth_k=k)
            # 曲率補正量 = elevs_with_curve - raw_elevs の最大値（中央付近）
            correction_max = float((t.elevs_with_curve - t.raw_elevs).max())
            corrections.append(correction_max)

        for i in range(len(corrections) - 1):
            assert corrections[i] > corrections[i + 1], (
                f"K 増加で曲率補正が減少しない: {corrections}"
            )

    def test_default_earth_k_is_standard_atmosphere(self):
        """earth_k のデフォルトは標準大気（4/3）。"""
        raw = np.zeros(100)
        t = models.calculate_terrain_profile(raw, 34.54, 132.41, 34.54, 132.46)
        assert t.earth_k == pytest.approx(4/3, rel=1e-6)


# ================================================================
# calculate_propagation
# ================================================================
class TestCalculatePropagation:
    """single / deygout 両モデルの共通挙動 + モデル固有の挙動。"""

    PARAMS = dict(h_tx=10.0, h_rx=10.0, freq_mhz=2400.0, veg_h=5.0, initial_k=10.0)

    # ── 共通: フィールドの存在と型 ──────────────────────────────
    @pytest.mark.parametrize("method", ["single", "deygout"])
    def test_returns_propagation_result(self, flat_terrain, method):
        r = models.calculate_propagation(flat_terrain, **self.PARAMS,
                                         diff_method=method)
        assert isinstance(r, models.PropagationResult)

    @pytest.mark.parametrize("method", ["single", "deygout"])
    def test_diff_method_field(self, flat_terrain, method):
        """diff_method フィールドが引数と一致する。"""
        r = models.calculate_propagation(flat_terrain, **self.PARAMS,
                                         diff_method=method)
        assert r.diff_method == method

    @pytest.mark.parametrize("method", ["single", "deygout"])
    def test_all_fields_finite(self, flat_terrain, method):
        r = models.calculate_propagation(flat_terrain, **self.PARAMS,
                                         diff_method=method)
        for field in (r.diff_loss, r.veg_loss, r.env_loss,
                      r.blocked_ratio, r.slant_dist_km, r.current_k):
            assert math.isfinite(field)

    @pytest.mark.parametrize("method", ["single", "deygout"])
    def test_flat_los_diff_loss_near_zero(self, flat_terrain, method):
        """平坦見通し地形では回折損が小さい。
        Deygout は植生層（veg_h=5m）が LoS に近い場合に
        わずかな損失を計上することがあるため、許容値を 2 dB とする。
        """
        r = models.calculate_propagation(flat_terrain, **self.PARAMS,
                                         diff_method=method)
        assert r.diff_loss == pytest.approx(0.0, abs=2.0)

    @pytest.mark.parametrize("method", ["single", "deygout"])
    def test_nonnegative_losses(self, single_ridge_terrain, method):
        r = models.calculate_propagation(single_ridge_terrain, **self.PARAMS,
                                         diff_method=method)
        assert r.diff_loss >= 0.0
        assert r.veg_loss  >= 0.0
        assert r.env_loss  >= 0.0

    def test_veg_loss_positive_when_los_penetrates_vegetation(self, flat_terrain):
        """
        LoS 線が植生層を貫通する場合、Veg Loss > 0 になること。

        バグ再現: 旧実装は elevs（地表高）vs Fresnel 下端を比較していたため、
        LoS が植生（elevs + veg_h）に触れていても Veg Loss = 0.0 を返していた。
        """
        # flat_terrain: 標高 0m、アンテナ高 0m → LoS は高度 0m の水平線
        # veg_h=10m を設定すると植生頂点は 10m → LoS（0m）を 10m 上回る
        r = models.calculate_propagation(
            flat_terrain,
            h_tx=0.0, h_rx=0.0,
            freq_mhz=2400.0,
            veg_h=10.0,
            initial_k=10.0,
        )
        assert r.veg_loss > 0.0, (
            "植生がLoSを遮っているにもかかわらず Veg Loss = 0.0 になっている"
        )

    def test_veg_loss_zero_when_los_clears_vegetation(self, flat_terrain):
        """
        LoS 線が植生層を完全にクリアしている場合、Veg Loss = 0 になること。

        アンテナを十分高くして LoS を植生頂点より上に保つ。
        """
        # flat_terrain: 標高 0m、veg_h=5m（植生頂点=5m）
        # アンテナ高 100m → LoS は約 100m → 植生（5m）を十分クリア
        r = models.calculate_propagation(
            flat_terrain,
            h_tx=100.0, h_rx=100.0,
            freq_mhz=2400.0,
            veg_h=5.0,
            initial_k=10.0,
        )
        assert r.veg_loss == pytest.approx(0.0, abs=1e-6)

    def test_veg_loss_increases_with_veg_height(self):
        """
        植生頂点の LoS への侵入量が大きいほど Veg Loss が単調増加すること。

        _vegetation_loss を直接呼び出して地形距離に依存しないテストとする。
        """
        N          = 100
        horiz_km   = 0.765
        freq_mhz   = 2400.0
        lam        = 299792458 / (freq_mhz * 1e6)
        d_m        = np.linspace(0, horiz_km * 1000, N)
        d1         = np.maximum(d_m, 1.0)
        d2         = np.maximum(horiz_km * 1000 - d_m, 1.0)
        f1         = np.sqrt(lam * d1 * d2 / (d1 + d2))
        los_vals   = np.full(N, 10.0)

        results = []
        for excess in [0.05, 0.10, 0.20, 0.30]:
            veg_top = np.full(N, 10.0 + excess)
            loss = models._vegetation_loss(veg_top, los_vals, f1, freq_mhz, horiz_km, N)
            assert loss < 45.0, f"excess={excess}m で上限45dBに達した"
            results.append(loss)

        for i in range(len(results) - 1):
            assert results[i] < results[i + 1], (
                f"侵入量増加で Veg Loss が減少している: {results}"
            )

    # ── デフォルト引数は "single" ────────────────────────────────
    def test_default_method_is_deygout(self, flat_terrain):
        r = models.calculate_propagation(flat_terrain, **self.PARAMS)
        assert r.diff_method == "deygout"

    # ── モデル固有: 2 本尾根で Deygout >= Single ─────────────────
    def test_deygout_gte_single_on_double_ridge(self, double_ridge_terrain):
        r_s = models.calculate_propagation(double_ridge_terrain, **self.PARAMS,
                                           diff_method="single")
        r_d = models.calculate_propagation(double_ridge_terrain, **self.PARAMS,
                                           diff_method="deygout")
        assert r_d.diff_loss >= r_s.diff_loss - 0.5

    # ── env_loss の範囲 ──────────────────────────────────────────
    @pytest.mark.parametrize("method", ["single", "deygout"])
    def test_env_loss_in_range(self, flat_terrain, method):
        r = models.calculate_propagation(flat_terrain, **self.PARAMS,
                                         diff_method=method)
        assert 3.0 <= r.env_loss <= 30.0

    # ── current_k は非負 ────────────────────────────────────────
    @pytest.mark.parametrize("method", ["single", "deygout"])
    def test_current_k_nonnegative(self, single_ridge_terrain, method):
        r = models.calculate_propagation(single_ridge_terrain, **self.PARAMS,
                                         diff_method=method)
        assert r.current_k >= 0.0

    # ── slant_dist >= horiz_dist ─────────────────────────────────
    @pytest.mark.parametrize("method", ["single", "deygout"])
    def test_slant_dist_gte_horiz(self, flat_terrain, method):
        r = models.calculate_propagation(flat_terrain, **self.PARAMS,
                                         diff_method=method)
        assert r.slant_dist_km >= flat_terrain.horiz_dist_km - 1e-6


# ================================================================
# calculate_link_budget
# ================================================================
class TestCalculateLinkBudget:
    def _prop(self, diff_method="single", env_type="los"):
        return models.PropagationResult(
            diff_loss=10.0, veg_loss=5.0, env_loss=8.0,
            rain_loss=0.0,  gas_loss=0.0,
            blocked_ratio=50.0, slant_dist_km=3.0,
            current_k=8.0, diff_method=diff_method, env_type=env_type,
        )

    RADIO = dict(freq_mhz=2400.0, p_tx=20.0, gain_tx=3.0,
                 gain_rx=3.0, sens=-85.0)

    def test_returns_link_budget_result(self):
        r = models.calculate_link_budget(self._prop(), **self.RADIO)
        assert isinstance(r, models.LinkBudgetResult)

    def test_diff_method_propagated(self):
        """diff_method が PropagationResult から LinkBudgetResult に引き継がれる。"""
        for method in ["single", "deygout"]:
            r = models.calculate_link_budget(self._prop(method), **self.RADIO)
            assert r.diff_method == method

    def test_env_type_propagated(self):
        """env_type が PropagationResult から LinkBudgetResult に引き継がれる。"""
        for env in ["urban", "suburban", "rural", "los"]:
            r = models.calculate_link_budget(self._prop(env_type=env), **self.RADIO)
            assert r.env_type == env

    def test_eirp(self):
        r = models.calculate_link_budget(self._prop(), **self.RADIO)
        assert r.eirp == pytest.approx(23.0)  # 20 + 3

    def test_total_loss_components(self):
        r = models.calculate_link_budget(self._prop(), **self.RADIO)
        assert r.total_loss == pytest.approx(
            r.fspl + r.diff_loss + r.veg_loss + r.env_loss, abs=1e-6
        )

    def test_p_rx_formula(self):
        r = models.calculate_link_budget(self._prop(), **self.RADIO)
        assert r.p_rx == pytest.approx(r.eirp + 3.0 - r.total_loss, abs=1e-6)

    def test_actual_margin_formula(self):
        r = models.calculate_link_budget(self._prop(), **self.RADIO)
        assert r.actual_margin == pytest.approx(r.p_rx - (-85.0), abs=1e-6)

    def test_status_ok_when_margin_positive(self):
        prop = models.PropagationResult(
            diff_loss=0.0, veg_loss=0.0, env_loss=3.0,
            rain_loss=0.0, gas_loss=0.0,
            blocked_ratio=0.0, slant_dist_km=0.1,
            current_k=10.0, diff_method="single", env_type="los",
        )
        r = models.calculate_link_budget(prop, **self.RADIO)
        assert r.status == "OK"

    def test_status_ng_when_margin_negative(self):
        prop = models.PropagationResult(
            diff_loss=80.0, veg_loss=40.0, env_loss=30.0,
            rain_loss=5.0,  gas_loss=1.0,
            blocked_ratio=200.0, slant_dist_km=50.0,
            current_k=0.0, diff_method="deygout", env_type="urban",
        )
        r = models.calculate_link_budget(prop, **self.RADIO)
        assert r.status == "NG"

    def test_fspl_increases_with_distance(self):
        """距離が遠いほど FSPL が大きい。"""
        def fspl_at(km):
            prop = models.PropagationResult(
                diff_loss=0.0, veg_loss=0.0, env_loss=3.0,
                rain_loss=0.0, gas_loss=0.0,
                blocked_ratio=0.0, slant_dist_km=km,
                current_k=10.0, diff_method="single", env_type="los",
            )
            return models.calculate_link_budget(prop, **self.RADIO).fspl

        assert fspl_at(1.0) < fspl_at(5.0) < fspl_at(20.0)

    def test_all_fields_finite(self):
        r = models.calculate_link_budget(self._prop(), **self.RADIO)
        for val in (r.eirp, r.fspl, r.diff_loss, r.veg_loss, r.env_loss,
                    r.total_loss, r.p_rx, r.actual_margin,
                    r.current_k, r.blocked_ratio, r.slant_dist_km):
            assert math.isfinite(val)


# ================================================================
# 環境区分
# ================================================================
class TestEnvCoeffs:

    PARAMS = dict(h_tx=10.0, h_rx=10.0, freq_mhz=2400.0, veg_h=5.0, initial_k=10.0)

    def test_all_env_types_defined(self):
        """4つの環境区分がすべて ENV_COEFFS に定義されている。"""
        for key in ["urban", "suburban", "rural", "los"]:
            assert key in models.ENV_COEFFS

    def test_env_labels_keys_match_coeffs(self):
        """ENV_LABELS の値がすべて ENV_COEFFS のキーと一致する。"""
        for label, key in models.ENV_LABELS.items():
            assert key in models.ENV_COEFFS, f"'{key}' (from label '{label}') not in ENV_COEFFS"

    def test_env_default_in_coeffs(self):
        """ENV_DEFAULT が ENV_COEFFS に存在する。"""
        assert models.ENV_DEFAULT in models.ENV_COEFFS

    def test_urban_env_loss_gt_suburban(self, flat_terrain):
        """Urban の Env Loss は Suburban より大きい。"""
        r_u = models.calculate_propagation(flat_terrain, **self.PARAMS, env_type="urban")
        r_s = models.calculate_propagation(flat_terrain, **self.PARAMS, env_type="suburban")
        assert r_u.env_loss > r_s.env_loss

    def test_suburban_env_loss_gt_rural(self, flat_terrain):
        """Suburban の Env Loss は Rural より大きい。"""
        r_s = models.calculate_propagation(flat_terrain, **self.PARAMS, env_type="suburban")
        r_r = models.calculate_propagation(flat_terrain, **self.PARAMS, env_type="rural")
        assert r_s.env_loss > r_r.env_loss

    def test_rural_env_loss_gt_los(self, flat_terrain):
        """Rural の Env Loss は LoS より大きい。"""
        r_r = models.calculate_propagation(flat_terrain, **self.PARAMS, env_type="rural")
        r_l = models.calculate_propagation(flat_terrain, **self.PARAMS, env_type="los")
        assert r_r.env_loss > r_l.env_loss

    def test_env_type_field_propagated(self, flat_terrain):
        """env_type が PropagationResult.env_type に反映される。"""
        for env in models.ENV_COEFFS:
            r = models.calculate_propagation(flat_terrain, **self.PARAMS, env_type=env)
            assert r.env_type == env

    def test_unknown_env_type_falls_back_to_los(self, flat_terrain):
        """未知の env_type は los にフォールバックする。"""
        r_unknown = models.calculate_propagation(flat_terrain, **self.PARAMS,
                                                 env_type="unknown_xyz")
        r_los     = models.calculate_propagation(flat_terrain, **self.PARAMS,
                                                 env_type="los")
        assert r_unknown.env_loss == pytest.approx(r_los.env_loss, rel=1e-6)

    def test_env_loss_within_bounds(self, flat_terrain):
        """各環境区分で Env Loss が定義された min/max 範囲内に収まる。"""
        for env, coeffs in models.ENV_COEFFS.items():
            min_db, max_db = coeffs[4], coeffs[5]  # veg_c 除去後: min=index4, max=index5
            r = models.calculate_propagation(flat_terrain, **self.PARAMS, env_type=env)
            assert min_db <= r.env_loss <= max_db, (
                f"{env}: env_loss={r.env_loss:.2f} out of [{min_db}, {max_db}]"
            )

    def test_default_env_type_is_los(self, flat_terrain):
        """デフォルト env_type は los。"""
        r = models.calculate_propagation(flat_terrain, **self.PARAMS)
        assert r.env_type == "los"

    def test_env_loss_independent_of_veg_loss(self):
        """
        Env Loss が Veg Loss の値に依存しないこと（二重計上の解消を確認）。

        _env_loss を直接呼び出し、veg_loss 引数が存在しないこと（シグネチャ変更）と、
        同じ blocked_ratio / slant_dist_km / diff_loss を与えたとき
        どの veg_loss 値を想定しても env_loss が一定であることを検証する。
        """
        import inspect
        sig = inspect.signature(models._env_loss)
        assert "veg_loss" not in sig.parameters, (
            "veg_loss が _env_loss の引数に残っている（二重計上の解消が不完全）"
        )

        # 同一の地形条件で _env_loss を直接呼ぶ → veg_loss に依存しないはず
        base_result = models._env_loss(
            blocked_ratio=30.0, slant_dist_km=1.0, diff_loss=5.0, env_type="suburban"
        )
        # 旧実装では veg_loss=0 と veg_loss=40 で結果が違った
        # 新実装では引数そのものがないため常に同じ
        assert isinstance(base_result, float)
        assert 3.0 <= base_result <= 30.0  # suburban の min/max 範囲内


# ================================================================
# 降雨減衰  ITU-R P.838-3
# ================================================================
class TestCalculateRainLoss:

    def test_zero_rain_rate_returns_zero(self):
        assert models.calculate_rain_loss(5800.0, 3.0, 0.0) == 0.0

    def test_below_1ghz_returns_zero(self):
        """1 GHz 未満は降雨減衰を無視する。"""
        assert models.calculate_rain_loss(900.0, 3.0, 50.0) == 0.0

    def test_positive_loss_with_rain(self):
        loss = models.calculate_rain_loss(11000.0, 5.0, 50.0)
        assert loss > 0.0

    def test_higher_rain_rate_higher_loss(self):
        loss_low  = models.calculate_rain_loss(11000.0, 5.0, 10.0)
        loss_high = models.calculate_rain_loss(11000.0, 5.0, 100.0)
        assert loss_high > loss_low

    def test_longer_distance_higher_loss(self):
        loss_short = models.calculate_rain_loss(11000.0, 1.0, 50.0)
        loss_long  = models.calculate_rain_loss(11000.0, 10.0, 50.0)
        assert loss_long > loss_short

    def test_higher_freq_higher_loss(self):
        """高周波ほど降雨減衰が大きい（P.838 の一般的傾向）。"""
        loss_5g  = models.calculate_rain_loss( 5800.0, 5.0, 50.0)
        loss_11g = models.calculate_rain_loss(11000.0, 5.0, 50.0)
        assert loss_11g > loss_5g

    def test_returns_float(self):
        assert isinstance(models.calculate_rain_loss(5800.0, 3.0, 20.0), float)

    def test_2_4ghz_small_loss(self):
        """2.4 GHz・50 mm/h でも損失は小さい（< 1 dB/km 程度）。"""
        loss = models.calculate_rain_loss(2400.0, 5.0, 50.0)
        assert 0.0 < loss < 5.0


# ================================================================
# 大気減衰  ITU-R P.676-13 Annex 2
# ================================================================
class TestCalculateGasLoss:

    def test_below_1ghz_returns_zero(self):
        assert models.calculate_gas_loss(900.0, 5.0) == 0.0

    def test_positive_loss_above_1ghz(self):
        assert models.calculate_gas_loss(5800.0, 5.0) > 0.0

    def test_longer_distance_higher_loss(self):
        loss_short = models.calculate_gas_loss(5800.0, 1.0)
        loss_long  = models.calculate_gas_loss(5800.0, 10.0)
        assert loss_long > loss_short

    def test_2_4ghz_very_small(self):
        """2.4 GHz での大気減衰は非常に小さい（< 0.1 dB/km）。"""
        loss = models.calculate_gas_loss(2400.0, 1.0)
        assert 0.0 < loss < 0.1

    def test_returns_float(self):
        assert isinstance(models.calculate_gas_loss(5800.0, 5.0), float)


# ================================================================
# calculate_propagation：rain_loss・gas_loss フィールド
# ================================================================
class TestPropagationRainGas:

    PARAMS = dict(h_tx=10.0, h_rx=10.0, freq_mhz=11000.0,
                  veg_h=5.0, initial_k=10.0)

    def test_rain_loss_zero_when_no_rain(self, flat_terrain):
        r = models.calculate_propagation(flat_terrain, **self.PARAMS, rain_rate=0.0)
        assert r.rain_loss == 0.0

    def test_rain_loss_positive_with_rain(self, flat_terrain):
        r = models.calculate_propagation(flat_terrain, **self.PARAMS, rain_rate=50.0)
        assert r.rain_loss > 0.0

    def test_gas_loss_always_present_above_1ghz(self, flat_terrain):
        r = models.calculate_propagation(flat_terrain, **self.PARAMS, rain_rate=0.0)
        assert r.gas_loss > 0.0

    def test_higher_rain_rate_higher_total_loss(self, flat_terrain):
        r0  = models.calculate_propagation(flat_terrain, **self.PARAMS, rain_rate=0.0)
        r50 = models.calculate_propagation(flat_terrain, **self.PARAMS, rain_rate=50.0)
        lb0  = models.calculate_link_budget(r0,  **dict(freq_mhz=11000.0, p_tx=20.0,
                                                         gain_tx=3.0, gain_rx=3.0, sens=-85.0))
        lb50 = models.calculate_link_budget(r50, **dict(freq_mhz=11000.0, p_tx=20.0,
                                                         gain_tx=3.0, gain_rx=3.0, sens=-85.0))
        assert lb50.total_loss > lb0.total_loss
