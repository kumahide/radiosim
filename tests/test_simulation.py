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
from unittest import mock

import numpy as np
import pytest

from core import config
from core import dem
from core import models
from core import simulation as sim


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

    def test_diff_method_bullington(self, default_params_dict):
        """diff_method="bullington" が正しくパースされる。"""
        default_params_dict["diff_method"] = "bullington"
        p = sim.SimParams(default_params_dict)
        assert p.diff_method == "bullington"

    def test_diff_method_default_is_bullington(self, default_params_dict):
        """diff_method キーが存在しない場合のデフォルトは "bullington"。"""
        default_params_dict.pop("diff_method", None)
        p = sim.SimParams(default_params_dict)
        assert p.diff_method == "bullington"

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
        monkeypatch.setattr(dem, "get_elevation", lambda la, lo: 100.0)

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
        monkeypatch.setattr(dem, "get_elevation", lambda la, lo: 0.0)
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
            dem, "get_elevation",
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
# DEM が取れないときの打ち切り（B-025 ②）
# ============================================================
class TestDemCircuitBreaker:
    """「取れないまま黙って完走する」を止めること。

    ⚠️ **フェイクは `dem.network_failed` まで差し替える**＝製品では
    `get_elevation` が 0.0 を返しつつ「通信で失敗した」を別口で立てる。戻り値だけ
    0.0 にしたフェイクは**成功扱い**になる（既存テストのフェイクを壊さないための
    設計＝安全側）ので、それでは打ち切りを再現できない。
    """

    def _run(self, params, *, elevation, failed, samples_seen=None):
        """フェイクの DEM で 1 回走らせ、(完了した配列, 例外) を返す。"""
        def _get(la, lo):
            if samples_seen is not None:
                samples_seen.append((la, lo))
            return elevation(la, lo) if callable(elevation) else elevation

        out: dict = {}
        done = threading.Event()
        with mock.patch.object(dem, "get_elevation", _get), \
             mock.patch.object(dem, "network_failed", failed):
            sim.fetch_elevations(
                params      = params,
                on_progress = lambda v: None,
                on_complete = lambda e: (out.__setitem__("elevs", e), done.set()),
                on_error    = lambda ex: (out.__setitem__("error", ex), done.set()),
            )
            done.wait(timeout=10)
        return out

    def test_total_network_failure_aborts_instead_of_returning_flat_terrain(
            self, default_params_dict):
        """1 点も取れないなら**エラーで止める**（平坦な地形を返さない）。

        従来は全点 0.0 の「標高 0m の平坦地形」が正常値の顔で完走し、判定は
        見通し良好側＝安全側に外れていた（B-025・実地で発生）。
        """
        default_params_dict["samples"] = "50"
        params = sim.SimParams(default_params_dict)
        out = self._run(params, elevation=0.0, failed=lambda: True)

        assert "elevs" not in out, (
            "取得が全滅したのに完走している＝標高 0m の平坦地形が結果として出る。"
        )
        assert isinstance(out.get("error"), sim.DemUnreachableError), (
            f"打ち切りの型が違う: {out.get('error')!r}"
        )
        assert "DEM" in str(out["error"]), "メッセージが原因を指していない"

    def test_it_stops_early_instead_of_waiting_for_every_sample(
            self, default_params_dict):
        """**全点ぶん待たない**こと（遅さの訴えはここで消える）。

        1 点あたり最大 15 秒（timeout 5 秒 × レイヤ 3 段）かかるので、200 点を
        最後まで試すと数十分になる。敷居に達したら投げるのをやめる。
        """
        default_params_dict["samples"] = "200"
        params = sim.SimParams(default_params_dict)
        seen: list = []
        self._run(params, elevation=0.0, failed=lambda: True, samples_seen=seen)

        assert len(seen) < params.num, "打ち切らずに全点を試している"
        assert len(seen) <= sim._DEM_FAILURE_LIMIT + sim._MAX_FETCH_WORKERS, (
            f"打ち切りが遅すぎる（{len(seen)} 点も試した）"
        )

    def test_a_few_failures_do_not_abort_a_working_run(self, default_params_dict):
        """**取れている実行は落とさない**＝失敗が数点あっても続ける。

        タイル 1 枚のタイムアウトや混雑時の 429 は日常的に起きる。それで経路
        全体をエラーにすると、これまで動いていた実行が突然使えなくなる。
        打ち切るのは「そもそも外へ出られていない」＝成功 0 件の形だけ。
        """
        default_params_dict["samples"] = "50"
        params = sim.SimParams(default_params_dict)
        state = {"n": 0}

        def failed():
            state["n"] += 1
            return state["n"] % 3 == 0      # 3 点に 1 点は通信失敗

        out = self._run(params, elevation=lambda la, lo: 120.0, failed=failed)
        assert "error" not in out, f"取れているのに落ちた: {out.get('error')!r}"
        assert len(out["elevs"]) == params.num

    def test_sea_tiles_are_not_treated_as_a_network_failure(self, default_params_dict):
        """海上（404 で標高が無い）は打ち切らないこと。

        404 は「そこにデータが永久に無い」＝通信は成功しており、`network_failed`
        は立たない（dem 側の約束）。ここを混ぜると**海の上を通る経路が
        「ネットワーク異常」で落ちる**（B-010 の鏡像）。
        """
        params = sim.SimParams(default_params_dict)
        out = self._run(params, elevation=0.0, failed=lambda: False)
        assert "error" not in out, "海上の 0m を通信失敗と取り違えている"
        assert list(out["elevs"]) == [0.0] * params.num


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
        monkeypatch.setattr(dem, "get_elevation", counting_get)

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
        monkeypatch.setattr(dem, "get_elevation", counting_get)

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
        monkeypatch.setattr(dem, "get_elevation", lambda la, lo: 42.0)
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
            # ⚠️ 0.0 を返さない：全点 0.0 は「DEM 全滅」としてキャッシュされない
            # （B-025）ので、0.0 だとキャッシュの共有可否を検査できなくなる。
            return 120.0
        monkeypatch.setattr(dem, "get_elevation", counting_get)

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
        # ⚠️ 0.0 を返さない：全点 0.0 はキャッシュされない（B-025）ため、2回目が
        # キャッシュヒットにならず、この検査が素通りしてしまう。
        monkeypatch.setattr(dem, "get_elevation", lambda la, lo: 120.0)
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

    # --- DEM 全滅（all 0）を焼き付けない -----------------------------------
    # B-025：`dem.get_elevation` は全レイヤ失敗時に「取れなかった」ではなく 0.0 を
    # 返すため、Proxy 未設定などで取得が全滅すると標高 0m の平坦地形が正常値の顔で
    # 出る。それが地形キャッシュに入ると、**Proxy を直してもアプリを再起動するまで
    # 直らない**（キャッシュはプロセス常駐）。戻り値契約そのものの是正は 3.x。

    def _run_once(self, params, on_complete=None):
        done = threading.Event()
        def _complete(elevs):
            if on_complete is not None:
                on_complete(elevs)
            done.set()
        sim.fetch_elevations_cached(
            params=params, on_progress=lambda v: None,
            on_complete=_complete, on_error=lambda ex: None,
        )
        done.wait(timeout=5)

    def test_all_zero_result_is_not_cached(self, default_params_dict, monkeypatch):
        """全点 0.0 の結果はキャッシュに入らず、次回はやり直すこと。"""
        call_count = {"n": 0}
        def failing_get(la, lo):
            call_count["n"] += 1
            return 0.0                      # ＝全レイヤ失敗時の戻り値
        monkeypatch.setattr(dem, "get_elevation", failing_get)

        params = sim.SimParams(default_params_dict)
        self._run_once(params)
        after_first = call_count["n"]
        assert after_first == params.num

        self._run_once(params)
        assert call_count["n"] == after_first + params.num, (
            "全点 0.0 の地形がキャッシュされている"
            "＝Proxy を直しても再起動するまで平坦地形が返り続ける"
        )

    def test_all_zero_result_is_still_delivered(self, default_params_dict, monkeypatch):
        """キャッシュしないだけで、結果自体は今までどおり返ること。

        ここで握り潰すと「実行したのに何も起きない」になる。失敗の伝播と画面での
        提示は別の対応（B-025 の ②③）で、この変更の担当ではない。
        """
        monkeypatch.setattr(dem, "get_elevation", lambda la, lo: 0.0)
        params = sim.SimParams(default_params_dict)

        got = {}
        self._run_once(params, on_complete=lambda e: got.__setitem__("elevs", e))
        assert "elevs" in got, "on_complete が呼ばれていない"
        assert len(got["elevs"]) == params.num

    def test_recovery_is_cached_after_a_failed_run(self, default_params_dict, monkeypatch):
        """全滅のあと取得が回復したら、その結果はキャッシュされること。

        「怪しいから一切キャッシュしない」にすると復旧後も毎回取り直しになり、
        地理院サーバーへ余計な負荷をかける（設計方針④）。
        """
        state = {"fail": True, "n": 0}
        def flaky_get(la, lo):
            state["n"] += 1
            return 0.0 if state["fail"] else 120.0
        monkeypatch.setattr(dem, "get_elevation", flaky_get)

        params = sim.SimParams(default_params_dict)
        self._run_once(params)              # 全滅（キャッシュされない）

        state["fail"] = False
        self._run_once(params)              # 回復（ここでキャッシュされるはず）
        after_recovery = state["n"]

        self._run_once(params)              # 3回目はキャッシュヒット
        assert state["n"] == after_recovery, "回復後の結果がキャッシュされていない"

    def test_partial_failure_is_cached(self, default_params_dict, monkeypatch):
        """一部だけ 0.0 の経路は今までどおりキャッシュすること。

        海抜 0m の点は実在する（海上・埋立地）。判定は「全点 0.0」に限る＝
        部分的な 0 を疑い始めると正当な地形を捨てることになる。
        """
        call_count = {"n": 0}
        def mostly_zero_get(la, lo):
            call_count["n"] += 1
            return 0.0 if call_count["n"] > 1 else 30.0
        monkeypatch.setattr(dem, "get_elevation", mostly_zero_get)

        params = sim.SimParams(default_params_dict)
        self._run_once(params)
        after_first = call_count["n"]

        self._run_once(params)
        assert call_count["n"] == after_first, "一部だけ 0.0 の地形までキャッシュを拒んでいる"


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

    def test_diff_method_bullington_reflected(self, flat_terrain, default_params_dict):
        """params.diff_method="bullington" が結果の diff_method に引き継がれる。"""
        default_params_dict["diff_method"] = "bullington"
        params = sim.SimParams(default_params_dict)
        result = sim.run_calculation(flat_terrain, 10.0, 10.0, params)
        assert result.diff_method == "bullington"

    def test_bullington_diff_loss_gte_single_on_ridge(self, default_params_dict):
        """尾根地形で Deygout の回折損 >= Single の回折損。"""
        raw = np.zeros(201)
        raw[100] = 50.0
        terrain = models.calculate_terrain_profile(
            raw, 34.5429, 132.4118, 34.5389, 132.4050
        )
        default_params_dict["diff_method"] = "single"
        r_single = sim.run_calculation(terrain, 10.0, 10.0, sim.SimParams(default_params_dict))

        default_params_dict["diff_method"] = "bullington"
        r_bullington = sim.run_calculation(terrain, 10.0, 10.0, sim.SimParams(default_params_dict))

        assert r_bullington.diff_loss >= r_single.diff_loss - 0.5

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

    def _run_save(self, tmp_path, flat_terrain, default_params_dict, monkeypatch,
                  diff_method="single", coord_format="dd"):
        monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path))
        default_params_dict["diff_method"] = diff_method
        params = sim.SimParams(default_params_dict)
        result = _make_result(diff_method)
        save_dir = sim.save_package(flat_terrain, result, params, 30.0, 10.0,
                                    coord_format=coord_format)
        return save_dir

    def test_creates_all_expected_files(self, tmp_path, flat_terrain,
                                        default_params_dict, monkeypatch):
        """save_package が CSV / JSON / TXT を生成すること。

        ⚠️ **`profile.png` はここでは作られない**（2.6a1 / I-036）。以前は画面の
        図を `savefig` していたが、呼び出し側が直後に `report_path.save_profile_png`
        で**同じパスを上書き**しており、1 回目は必ず捨てられていた。図の保存は
        レポート専用図の担当に一本化した。
        """
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict, monkeypatch)
        assert not os.path.exists(os.path.join(save_dir, "profile.png")), (
            "save_package が画面の図を書いている（レポート図に上書きされる二重書き）"
        )
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

    def test_report_contains_diff_model_bullington(self, tmp_path, flat_terrain,
                                                default_params_dict, monkeypatch):
        """report.txt に Diff Model: bullington が含まれること。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch, diff_method="bullington")
        with open(os.path.join(save_dir, "report.txt"), encoding="utf-8") as f:
            content = f.read()
        assert "Diff Model    : bullington" in content

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
                                  monkeypatch, diff_method="bullington")
        with open(os.path.join(save_dir, "settings.json"), encoding="utf-8") as f:
            settings = json.load(f)
        assert "diff_method" in settings
        assert settings["diff_method"] == "bullington"

    def test_settings_json_roundtrip(self, tmp_path, flat_terrain,
                                     default_params_dict, monkeypatch):
        """settings.json を読み込んで SimParams を再構築できること。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch, diff_method="bullington")
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
        assert p.diff_method == "bullington"
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
        assert rows[0] == ["Distance_m", "Elevation_m"]
        assert len(rows) - 1 == flat_terrain.num_samples
