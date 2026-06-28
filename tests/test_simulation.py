"""
tests/test_simulation.py
========================
simulation.py（ViewModel）のユニットテスト。
DEM 取得は monkeypatch でモックし、ネットワーク不要。

変更履歴:
  - SimParams.diff_method フィールド追加に対応
  - run_calculation の diff_method 反映を検証するテストを追加
  - LinkBudgetResult の diff_method フィールド追加に対応（save_package テスト）
  - settings.json / report.txt への diff_method 出力を検証するテストを追加
  - import os を末尾から先頭に移動
"""

import os
import json
import threading

import numpy as np
import pytest

import infrastructure as infra
import models
import simulation as sim


# ============================================================
# SimParams
# ============================================================
class TestSimParams:

    def test_parses_coords_correctly(self, default_params_dict):
        p = sim.SimParams(default_params_dict)
        assert p.lat_tx == pytest.approx(34.5429)
        assert p.lon_tx == pytest.approx(132.4118)
        assert p.lat_rx == pytest.approx(34.5389)
        assert p.lon_rx == pytest.approx(132.4050)

    def test_parses_numeric_fields(self, default_params_dict):
        p = sim.SimParams(default_params_dict)
        assert p.freq_mhz == pytest.approx(2400.0)
        assert p.p_tx     == pytest.approx(20.0)
        assert p.gain_tx  == pytest.approx(3.0)
        assert p.gain_rx  == pytest.approx(3.0)
        assert p.sens     == pytest.approx(-85.0)

    def test_samples_minimum_10(self, default_params_dict):
        default_params_dict["samples"] = "3"
        p = sim.SimParams(default_params_dict)
        assert p.num == 10

    def test_diff_method_single(self, default_params_dict):
        """diff_method="single" が正しくパースされる。"""
        default_params_dict["diff_method"] = "single"
        p = sim.SimParams(default_params_dict)
        assert p.diff_method == "single"

    def test_diff_method_deygout(self, default_params_dict):
        """diff_method="deygout" が正しくパースされる。"""
        default_params_dict["diff_method"] = "deygout"
        p = sim.SimParams(default_params_dict)
        assert p.diff_method == "deygout"

    def test_diff_method_default_is_deygout(self, default_params_dict):
        """diff_method キーが存在しない場合のデフォルトは "deygout"。"""
        default_params_dict.pop("diff_method", None)
        p = sim.SimParams(default_params_dict)
        assert p.diff_method == "deygout"

    def test_env_type_parsed(self, default_params_dict):
        """env_type が正しくパースされる。"""
        for env in ["urban", "suburban", "rural", "los"]:
            default_params_dict["env_type"] = env
            p = sim.SimParams(default_params_dict)
            assert p.env_type == env

    def test_env_type_default_is_los(self, default_params_dict):
        """env_type キーが存在しない場合のデフォルトは "los"。"""
        default_params_dict.pop("env_type", None)
        p = sim.SimParams(default_params_dict)
        assert p.env_type == "los"

    def test_rain_rate_parsed(self, default_params_dict):
        """rain_rate が正しくパースされる。"""
        default_params_dict["rain_rate"] = "25.0"
        p = sim.SimParams(default_params_dict)
        assert p.rain_rate == pytest.approx(25.0)

    def test_rain_rate_default_is_zero(self, default_params_dict):
        """rain_rate キーが存在しない場合のデフォルトは 0.0。"""
        default_params_dict.pop("rain_rate", None)
        p = sim.SimParams(default_params_dict)
        assert p.rain_rate == pytest.approx(0.0)


# ============================================================
# fetch_elevations
# ============================================================
class TestFetchElevations:

    def test_calls_on_complete_with_array(self, default_params_dict, monkeypatch):
        """on_complete が numpy 配列で呼ばれること。"""
        monkeypatch.setattr(infra, "get_elevation", lambda la, lo: 100.0)

        results = {}
        done    = threading.Event()

        def on_complete(elevs):
            results["elevs"] = elevs
            done.set()

        params = sim.SimParams(default_params_dict)
        sim.fetch_elevations(
            params      = params,
            on_progress = lambda v: None,
            on_complete = on_complete,
            on_error    = lambda ex: None,
        )

        done.wait(timeout=5)
        assert "elevs" in results
        assert isinstance(results["elevs"], np.ndarray)
        assert len(results["elevs"]) == params.num

    def test_on_progress_called_for_each_sample(self, default_params_dict, monkeypatch):
        """on_progress がサンプル数だけ呼ばれること。"""
        monkeypatch.setattr(infra, "get_elevation", lambda la, lo: 0.0)
        default_params_dict["samples"] = "20"

        progress_calls = []
        done = threading.Event()

        def on_complete(elevs):
            done.set()

        params = sim.SimParams(default_params_dict)
        sim.fetch_elevations(
            params      = params,
            on_progress = lambda v: progress_calls.append(v),
            on_complete = on_complete,
            on_error    = lambda ex: None,
        )

        done.wait(timeout=5)
        assert len(progress_calls) == params.num

    def test_on_error_called_on_exception(self, default_params_dict, monkeypatch):
        """例外発生時に on_error が呼ばれること。"""
        monkeypatch.setattr(
            infra, "get_elevation",
            lambda la, lo: (_ for _ in ()).throw(RuntimeError("network fail")),
        )

        errors = {}
        done   = threading.Event()

        def on_error(ex):
            errors["ex"] = ex
            done.set()

        params = sim.SimParams(default_params_dict)
        sim.fetch_elevations(
            params      = params,
            on_progress = lambda v: None,
            on_complete = lambda e: None,
            on_error    = on_error,
        )

        done.wait(timeout=5)
        assert "ex" in errors


# ============================================================
# fetch_elevations_cached
# ============================================================
class TestFetchElevationsCached:

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """テスト間でキャッシュをリセットする。"""
        sim.clear_terrain_cache()
        yield
        sim.clear_terrain_cache()

    def test_cache_miss_calls_get_elevation(self, default_params_dict, monkeypatch):
        """キャッシュミス時は get_elevation が呼ばれること。"""
        call_count = {"n": 0}
        def counting_get(la, lo):
            call_count["n"] += 1
            return 100.0
        monkeypatch.setattr(infra, "get_elevation", counting_get)

        done = threading.Event()
        params = sim.SimParams(default_params_dict)
        sim.fetch_elevations_cached(
            params      = params,
            on_progress = lambda v: None,
            on_complete = lambda e: done.set(),
            on_error    = lambda ex: None,
        )
        done.wait(timeout=5)
        assert call_count["n"] == params.num

    def test_cache_hit_skips_get_elevation(self, default_params_dict, monkeypatch):
        """同一パラメータで2回目の呼び出しは get_elevation を呼ばないこと。"""
        call_count = {"n": 0}
        def counting_get(la, lo):
            call_count["n"] += 1
            return 100.0
        monkeypatch.setattr(infra, "get_elevation", counting_get)

        params = sim.SimParams(default_params_dict)

        # 1回目（キャッシュミス）
        done1 = threading.Event()
        sim.fetch_elevations_cached(
            params=params, on_progress=lambda v: None,
            on_complete=lambda e: done1.set(), on_error=lambda ex: None,
        )
        done1.wait(timeout=5)
        first_count = call_count["n"]
        assert first_count == params.num

        # 2回目（キャッシュヒット）
        done2 = threading.Event()
        sim.fetch_elevations_cached(
            params=params, on_progress=lambda v: None,
            on_complete=lambda e: done2.set(), on_error=lambda ex: None,
        )
        done2.wait(timeout=5)
        assert call_count["n"] == first_count  # 追加呼び出しなし

    def test_cache_hit_returns_same_array(self, default_params_dict, monkeypatch):
        """キャッシュヒット時に返る配列が1回目と同じ値であること。"""
        monkeypatch.setattr(infra, "get_elevation", lambda la, lo: 42.0)
        params = sim.SimParams(default_params_dict)

        results = {}
        for run in ("first", "second"):
            done = threading.Event()
            sim.fetch_elevations_cached(
                params=params, on_progress=lambda v: None,
                on_complete=lambda e, r=run: (results.__setitem__(r, e), done.set()),
                on_error=lambda ex: None,
            )
            done.wait(timeout=5)

        np.testing.assert_array_equal(results["first"], results["second"])

    def test_different_coords_not_shared(self, default_params_dict, monkeypatch):
        """TX/RX 座標が異なる場合はキャッシュを共有しないこと。"""
        call_count = {"n": 0}
        def counting_get(la, lo):
            call_count["n"] += 1
            return 0.0
        monkeypatch.setattr(infra, "get_elevation", counting_get)

        params_a = sim.SimParams(default_params_dict)

        other = default_params_dict.copy()
        other["end"] = "34.5000, 132.4000"
        params_b = sim.SimParams(other)

        for params in (params_a, params_b):
            done = threading.Event()
            sim.fetch_elevations_cached(
                params=params, on_progress=lambda v: None,
                on_complete=lambda e: done.set(), on_error=lambda ex: None,
            )
            done.wait(timeout=5)

        # 2つの異なるルート分が取得されている
        assert call_count["n"] == params_a.num + params_b.num

    def test_cache_hit_calls_on_progress_with_total(self, default_params_dict, monkeypatch):
        """キャッシュヒット時は on_progress(num) が呼ばれてプログレスバーが満杯になること。"""
        monkeypatch.setattr(infra, "get_elevation", lambda la, lo: 0.0)
        params = sim.SimParams(default_params_dict)

        # 1回目でキャッシュ生成
        done1 = threading.Event()
        sim.fetch_elevations_cached(
            params=params, on_progress=lambda v: None,
            on_complete=lambda e: done1.set(), on_error=lambda ex: None,
        )
        done1.wait(timeout=5)

        # 2回目: on_progress の値を記録
        progress_vals = []
        done2 = threading.Event()
        sim.fetch_elevations_cached(
            params=params,
            on_progress=lambda v: progress_vals.append(v),
            on_complete=lambda e: done2.set(),
            on_error=lambda ex: None,
        )
        done2.wait(timeout=5)
        assert params.num in progress_vals  # 満杯値が渡されている


# ============================================================
# run_calculation
# ============================================================
class TestRunCalculation:

    def test_returns_link_budget_result(self, flat_terrain, default_params_dict):
        params = sim.SimParams(default_params_dict)
        result = sim.run_calculation(flat_terrain, 30.0, 10.0, params)
        assert isinstance(result, models.LinkBudgetResult)

    def test_status_ok_or_ng(self, flat_terrain, default_params_dict):
        params = sim.SimParams(default_params_dict)
        result = sim.run_calculation(flat_terrain, 30.0, 10.0, params)
        assert result.status in ("OK", "NG")

    def test_slant_dist_positive(self, flat_terrain, default_params_dict):
        params = sim.SimParams(default_params_dict)
        result = sim.run_calculation(flat_terrain, 10.0, 10.0, params)
        assert result.slant_dist_km > 0

    def test_diff_method_single_reflected(self, flat_terrain, default_params_dict):
        """params.diff_method="single" が結果の diff_method に引き継がれる。"""
        default_params_dict["diff_method"] = "single"
        params = sim.SimParams(default_params_dict)
        result = sim.run_calculation(flat_terrain, 10.0, 10.0, params)
        assert result.diff_method == "single"

    def test_diff_method_deygout_reflected(self, flat_terrain, default_params_dict):
        """params.diff_method="deygout" が結果の diff_method に引き継がれる。"""
        default_params_dict["diff_method"] = "deygout"
        params = sim.SimParams(default_params_dict)
        result = sim.run_calculation(flat_terrain, 10.0, 10.0, params)
        assert result.diff_method == "deygout"

    def test_deygout_diff_loss_gte_single_on_ridge(self, default_params_dict):
        """尾根地形で Deygout の回折損 >= Single の回折損。"""
        raw = np.zeros(201)
        raw[100] = 50.0
        terrain = models.calculate_terrain_profile(
            raw, 34.5429, 132.4118, 34.5389, 132.4050
        )
        default_params_dict["diff_method"] = "single"
        r_single = sim.run_calculation(terrain, 10.0, 10.0, sim.SimParams(default_params_dict))

        default_params_dict["diff_method"] = "deygout"
        r_deygout = sim.run_calculation(terrain, 10.0, 10.0, sim.SimParams(default_params_dict))

        assert r_deygout.diff_loss >= r_single.diff_loss - 0.5

    def test_env_type_reflected_in_result(self, flat_terrain, default_params_dict):
        """params.env_type が結果の env_type に引き継がれる。"""
        for env in ["urban", "suburban", "rural", "los"]:
            default_params_dict["env_type"] = env
            result = sim.run_calculation(flat_terrain, 10.0, 10.0,
                                         sim.SimParams(default_params_dict))
            assert result.env_type == env

    def test_urban_env_loss_gt_los(self, flat_terrain, default_params_dict):
        """Urban の env_loss は LoS より大きい。"""
        default_params_dict["env_type"] = "urban"
        r_urban = sim.run_calculation(flat_terrain, 10.0, 10.0,
                                      sim.SimParams(default_params_dict))
        default_params_dict["env_type"] = "los"
        r_los = sim.run_calculation(flat_terrain, 10.0, 10.0,
                                    sim.SimParams(default_params_dict))
        assert r_urban.env_loss > r_los.env_loss

    def test_rain_rate_via_slider_arg(self, flat_terrain, default_params_dict):
        """run_calculation の rain_rate 引数がスライダー値として機能する。"""
        params = sim.SimParams({**default_params_dict, "freq": "11000"})
        r_dry  = sim.run_calculation(flat_terrain, 10.0, 10.0, params, rain_rate=0.0)
        r_rain = sim.run_calculation(flat_terrain, 10.0, 10.0, params, rain_rate=50.0)
        assert r_rain.rain_loss > r_dry.rain_loss
        assert r_rain.total_loss > r_dry.total_loss

    def test_rain_rate_none_uses_params(self, flat_terrain, default_params_dict):
        """rain_rate=None のとき params.rain_rate が使われる。"""
        default_params_dict["rain_rate"] = "30.0"
        default_params_dict["freq"]      = "11000"
        params = sim.SimParams(default_params_dict)
        r = sim.run_calculation(flat_terrain, 10.0, 10.0, params, rain_rate=None)
        assert r.rain_loss > 0.0


# ============================================================
# save_package（ファイル生成確認）
# ============================================================
def _make_result(diff_method="single", env_type="los"):
    """テスト用 LinkBudgetResult を生成するヘルパー。"""
    return models.LinkBudgetResult(
        eirp=23.0, fspl=100.0, diff_loss=0.0, veg_loss=0.0,
        env_loss=6.0, rain_loss=0.0, gas_loss=0.0,
        total_loss=106.0, p_rx=-83.0,
        actual_margin=2.0, status="OK",
        current_k=10.0, blocked_ratio=0.0, slant_dist_km=1.0,
        diff_method=diff_method, env_type=env_type,
    )


class TestSavePackage:

    @pytest.fixture(autouse=True)
    def _setup_mpl(self):
        """matplotlib を Agg バックエンドで初期化。"""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        self.plt = plt

    def _run_save(self, tmp_path, flat_terrain, default_params_dict, monkeypatch,
                  diff_method="single", coord_format="dd"):
        monkeypatch.setattr(infra, "RESULTS_DIR", str(tmp_path))
        default_params_dict["diff_method"] = diff_method
        params = sim.SimParams(default_params_dict)
        result = _make_result(diff_method)
        fig, _ = self.plt.subplots()
        save_dir = sim.save_package(fig, flat_terrain, result, params, 30.0, 10.0,
                                    coord_format=coord_format)
        self.plt.close(fig)
        return save_dir

    def test_creates_all_expected_files(self, tmp_path, flat_terrain,
                                        default_params_dict, monkeypatch):
        """save_package が PNG / CSV / JSON / TXT を生成すること。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict, monkeypatch)
        assert os.path.exists(os.path.join(save_dir, "profile.png"))
        assert os.path.exists(os.path.join(save_dir, "terrain_profile.csv"))
        assert os.path.exists(os.path.join(save_dir, "settings.json"))
        assert os.path.exists(os.path.join(save_dir, "report.txt"))

    def test_report_contains_status(self, tmp_path, flat_terrain,
                                    default_params_dict, monkeypatch):
        """report.txt に Status 行が含まれること。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict, monkeypatch)
        with open(os.path.join(save_dir, "report.txt"), encoding="utf-8") as f:
            content = f.read()
        assert "Status        : OK" in content

    def test_report_contains_diff_model_single(self, tmp_path, flat_terrain,
                                               default_params_dict, monkeypatch):
        """report.txt に Diff Model: single が含まれること。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch, diff_method="single")
        with open(os.path.join(save_dir, "report.txt"), encoding="utf-8") as f:
            content = f.read()
        assert "Diff Model    : single" in content

    def test_report_contains_diff_model_deygout(self, tmp_path, flat_terrain,
                                                default_params_dict, monkeypatch):
        """report.txt に Diff Model: deygout が含まれること。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch, diff_method="deygout")
        with open(os.path.join(save_dir, "report.txt"), encoding="utf-8") as f:
            content = f.read()
        assert "Diff Model    : deygout" in content

    def test_report_dd_by_default(self, tmp_path, flat_terrain,
                                  default_params_dict, monkeypatch):
        """既定では report.txt の座標は DD（度分秒記号を含まない）。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict, monkeypatch)
        with open(os.path.join(save_dir, "report.txt"), encoding="utf-8") as f:
            content = f.read()
        assert "TX Site       : 34.542900, 132.411800" in content
        assert "°" not in content

    def test_report_honors_dms_coord_format(self, tmp_path, flat_terrain,
                                            default_params_dict, monkeypatch):
        """coord_format='dms' のとき report.txt の座標が DMS 表記になる。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch, coord_format="dms")
        with open(os.path.join(save_dir, "report.txt"), encoding="utf-8") as f:
            content = f.read()
        assert "TX Site       : 34°32'34.4\"N, 132°24'42.5\"E" in content

    def test_settings_json_stays_dd_even_in_dms_mode(self, tmp_path, flat_terrain,
                                                     default_params_dict, monkeypatch):
        """coord_format='dms' でも settings.json は DD 固定（再読込のため）。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch, coord_format="dms")
        with open(os.path.join(save_dir, "settings.json"), encoding="utf-8") as f:
            settings = json.load(f)
        assert settings["start"] == "34.5429, 132.4118"
        assert "°" not in settings["start"]

    def test_settings_json_contains_diff_method(self, tmp_path, flat_terrain,
                                                default_params_dict, monkeypatch):
        """settings.json に diff_method キーが保存されること。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch, diff_method="deygout")
        with open(os.path.join(save_dir, "settings.json"), encoding="utf-8") as f:
            settings = json.load(f)
        assert "diff_method" in settings
        assert settings["diff_method"] == "deygout"

    def test_settings_json_roundtrip(self, tmp_path, flat_terrain,
                                     default_params_dict, monkeypatch):
        """settings.json を読み込んで SimParams を再構築できること。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch, diff_method="deygout")
        with open(os.path.join(save_dir, "settings.json"), encoding="utf-8") as f:
            saved = json.load(f)

        restored = {
            "start"      : saved["start"],
            "end"        : saved["end"],
            "h_tx"       : str(saved["h_tx"]),
            "h_rx"       : str(saved["h_rx"]),
            "freq"       : str(saved["freq"]),
            "p_tx"       : str(saved["p_tx"]),
            "gain_tx"    : str(saved["gain_tx"]),
            "gain_rx"    : str(saved["gain_rx"]),
            "sens"       : str(saved["sens"]),
            "veg_h"      : str(saved["veg_h"]),
            "k_factor"   : str(saved["k_factor"]),
            "samples"    : str(saved["samples"]),
            "diff_method": saved["diff_method"],
            "env_type"   : saved.get("env_type", "los"),
            "rain_rate"  : str(saved.get("rain_rate", 0.0)),
        }
        p = sim.SimParams(restored)
        assert p.diff_method == "deygout"
        assert p.env_type == default_params_dict.get("env_type", "los")
        assert p.rain_rate == pytest.approx(0.0)

    def test_terrain_csv_has_header_and_rows(self, tmp_path, flat_terrain,
                                              default_params_dict, monkeypatch):
        """terrain_profile.csv がヘッダーと正しい行数を持つこと。"""
        import csv
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict, monkeypatch)
        with open(os.path.join(save_dir, "terrain_profile.csv"),
                  newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert rows[0] == ["Distance_km", "Elevation_m"]
        assert len(rows) - 1 == flat_terrain.num_samples
