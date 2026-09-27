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

import dataclasses
import io
import os
import json
import threading
import types
from unittest import mock

import numpy as np
import pytest

from core import config
from core import dem
from core import dem_cache
from core import dem_prefetch
from core import dem_sources
from core import models
from core import simulation as sim
from core import terrain_grid as tg
from core import units


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

    def test_fixed_n_has_no_pixel_groups(self, default_params_dict):
        """固定 N（段階を名乗らない口）は画素セルの対応表を持たない（B-160）。"""
        default_params_dict.pop("resolution", None)
        p = sim.SimParams(default_params_dict)
        assert p.sample_pixel_groups is None

    def test_a_resolution_level_has_pixel_groups_aligned_with_sample_fracs(
        self, default_params_dict
    ):
        """段階（高・中）を選んだ実行は `sample_pixel_groups` を
        `sample_fracs` と同じ長さで持つ（B-160＝表示側が畳むための対応表）。
        """
        default_params_dict["resolution"] = "high"
        p = sim.SimParams(default_params_dict)
        assert p.sample_pixel_groups is not None
        assert len(p.sample_pixel_groups) == len(p.sample_fracs)


# ============================================================
# resolve_samples
# ============================================================
class TestResolveSamples:
    def test_normal_run_reports_the_pixel_size(self):
        """天井に当たらない実行は、従来どおり**画素の寸法**を返す（B-150）。"""
        n, spacing = sim.resolve_samples(34.54, 132.41, 34.545, 132.415, "high")
        assert spacing == pytest.approx(
            tg.grid_step_m((34.54 + 34.545) / 2.0, "high"))

    def test_ceiling_fallback_reports_the_effective_spacing_not_the_pixel_size(
        self,
    ):
        """🔴 **B-159**＝天井で等間隔へ落ちた実行は、間隔として画素の寸法を
        返していた（`resolve_samples` の第2戻り値が常に `grid_step_m`）。
        等間隔へ落ちた以上、返すべきは *距離 ÷（点数−1）* の実効間隔。
        """
        # 44°N から方位 45° へ 150km＝B-158 の実測で天井（90000）に当たる経路。
        import math

        def _dest(lat, lon, brng, km):
            r = 6371.0
            d = km / r
            b = math.radians(brng)
            p1 = math.radians(lat)
            p2 = math.asin(math.sin(p1) * math.cos(d)
                           + math.cos(p1) * math.sin(d) * math.cos(b))
            l2 = math.radians(lon) + math.atan2(
                math.sin(b) * math.sin(d) * math.cos(p1),
                math.cos(d) - math.sin(p1) * math.sin(p2))
            return math.degrees(p2), math.degrees(l2)

        lat_rx, lon_rx = _dest(44.0, 141.0, 45.0, 150.0)
        n, spacing = sim.resolve_samples(44.0, 141.0, lat_rx, lon_rx, "high")
        assert n == tg.SAMPLES_CEILING
        assert not tg.samples_are_pixel_edges("high", n)

        dist_m = models.horizontal_distance_km(
            44.0, 141.0, lat_rx, lon_rx) * units.KM_TO_M
        expected = tg.effective_spacing_m(dist_m, n)
        assert spacing == pytest.approx(expected)
        assert spacing != pytest.approx(
            tg.grid_step_m((44.0 + lat_rx) / 2.0, "high"))


# ============================================================
# fetch_elevations
# ============================================================
class TestFetchElevations:

    def test_calls_on_complete_with_array(self, default_params_dict, monkeypatch):
        """on_complete が numpy 配列で呼ばれること。"""
        monkeypatch.setattr(dem, "get_elevation", lambda la, lo, *_a: 100.0)

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
        monkeypatch.setattr(dem, "get_elevation", lambda la, lo, *_a: 0.0)
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
            lambda la, lo, *_a: (_ for _ in ()).throw(RuntimeError("network fail")),
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
        def _get(la, lo, *_a):
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

        out = self._run(params, elevation=lambda la, lo, *_a: 120.0, failed=failed)
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
        def counting_get(la, lo, *_a):
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
        def counting_get(la, lo, *_a):
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

    def _fetch_once(self, params) -> None:
        done = threading.Event()
        sim.fetch_elevations_cached(
            params=params, on_progress=lambda v: None,
            on_complete=lambda e: done.set(), on_error=lambda ex: None,
        )
        done.wait(timeout=5)

    @pytest.mark.parametrize("delete", [
        lambda: dem_cache.delete_all_tile_cache(),
        lambda: dem_cache.delete_tile_cache(34.540, 132.410, 34.539, 132.409),
    ], ids=["delete_all", "delete_bbox"])
    def test_deleting_the_tile_cache_also_drops_the_terrain_cache(
            self, delete, default_params_dict, tmp_path, monkeypatch):
        """キャッシュを削除したら、次の計算で DEM を取り直すこと（B-249）。

        **症状そのものを測る**（[[feedback-verification]]）＝見るのは
        `_terrain_cache` が空かどうかではなく、削除の**後**に実際に取りに行くか。
        実機で起きた形＝全キャッシュ削除（`deleted=193`）の 2 分後に同じ経路が
        `Terrain cache hit` になり、タイルがディスクへ戻らなかった。

        ⚠️ **範囲削除でも落ちる**＝地形キャッシュの鍵は経路の端点と標本数で、
        タイルの bbox と突き合わせられないので範囲削除でも全部捨てる（取り直す
        だけで計算の数字は変わらない＝同じソースの同じ地形）。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        call_count = {"n": 0}

        def counting_get(la, lo, *_a):
            call_count["n"] += 1
            return 100.0

        monkeypatch.setattr(dem, "get_elevation", counting_get)
        params = sim.SimParams(default_params_dict)

        self._fetch_once(params)
        first = call_count["n"]
        assert first == params.num
        self._fetch_once(params)
        assert call_count["n"] == first, "前提が崩れている＝2 回目はキャッシュヒット"

        delete()

        self._fetch_once(params)
        assert call_count["n"] == first * 2, (
            "キャッシュ削除後も地形キャッシュが残り、DEM を取り直していない"
        )

    @staticmethod
    def _serve(monkeypatch, served: dict) -> None:
        """`_get_session` を偽物にする＝`served["value"]` を青に持つタイルを返す。"""
        from PIL import Image

        class _Res:
            status_code = 200

            def __init__(self):
                buf = io.BytesIO()
                Image.new("RGB", (256, 256), (0, 0, served["value"])).save(buf, format="PNG")
                self.content = buf.getvalue()

        class _Session:
            def get(self, url, timeout=None):
                return _Res()
        monkeypatch.setattr(dem, "_get_session", lambda: _Session())

    def _fetch_elevs(self, params) -> np.ndarray:
        out: dict = {}
        done = threading.Event()

        def on_complete(e):
            out["elevs"] = e
            done.set()
        sim.fetch_elevations_cached(
            params=params, on_progress=lambda v: None,
            on_complete=on_complete, on_error=lambda ex: done.set(),
        )
        assert done.wait(timeout=10)
        return out["elevs"]

    def test_force_refetch_reaches_the_next_calculation(
            self, default_params_dict, tmp_path, monkeypatch):
        """強制再取得のあと、同じ条件で計算し直すと**取り直した標高**を使う（B-288）。

        CHANGELOG `[3.6]` の B-280 の項が約束している形＝画面の順序（計算 → 地図の
        強制再取得 → 同じ条件で再計算）をそのまま通す。B-283 の検査は `get_elevation`
        の層までしか見ておらず、その上の `_terrain_cache` に当たる経路を見ていなかった。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "_tile_cache", {})
        monkeypatch.setattr(dem, "_failed_tiles", set())
        served = {"value": 10}
        self._serve(monkeypatch, served)
        params = sim.SimParams(default_params_dict)

        before = self._fetch_elevs(params)
        served["value"] = 20
        dem_prefetch.prefetch_tiles(
            params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx, force=True)
        after = self._fetch_elevs(params)

        assert not np.array_equal(before, after), \
            "強制再取得したのに、地形キャッシュの取り直す前の標高で計算している"

    def test_force_refetch_racing_a_cache_hit_does_not_return_stale_terrain(
            self, default_params_dict, tmp_path, monkeypatch):
        """地形キャッシュを引く最中に強制再取得が終わっても、取り直す前の地形を
        返さない（B-289＝B-288 の直しの命中側）。

        世代を読んでから辞書を引くまでの間に無効化が入ると、項目の世代が先に読んだ
        値と一致して古い地形に命中する。ここでは辞書の `get` に強制再取得を割り込ませて、
        その順序を決定的に作る。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "_tile_cache", {})
        monkeypatch.setattr(dem, "_failed_tiles", set())
        served = {"value": 10}
        self._serve(monkeypatch, served)
        params = sim.SimParams(default_params_dict)
        before = self._fetch_elevs(params)

        state = {"armed": True}

        class _RacingDict(dict):
            def get(self, key, default=None):
                if state["armed"]:
                    state["armed"] = False
                    served["value"] = 20
                    dem_prefetch.prefetch_tiles(
                        params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx,
                        force=True)
                return super().get(key, default)
        monkeypatch.setattr(sim, "_terrain_cache", _RacingDict(sim._terrain_cache))

        after = self._fetch_elevs(params)

        assert not state["armed"]
        assert not np.array_equal(before, after), \
            "引く最中に強制再取得が終わったのに、取り直す前の地形に命中している"

    @pytest.mark.parametrize("how", ("force", "range", "all"))
    def test_invalidation_during_a_calculation_is_not_cached(
            self, how, default_params_dict, tmp_path, monkeypatch):
        """計算の途中でタイルが無効化されたら、その計算の地形は登録しない（B-288）。

        無効化より前の標高を読んだかもしれない結果が地形キャッシュに残ると、無効化の
        あとの同じ条件の計算がそれに当たる（[[B-286]] と同じ形の 1 段上）。地図の DL・
        削除は計算と別スレッドなので、この順序は実際に起こり得る。ここでは最初の標本の
        取得に無効化を割り込ませて、その順序を決定的に作る。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "_tile_cache", {})
        monkeypatch.setattr(dem, "_failed_tiles", set())
        self._serve(monkeypatch, {"value": 10})
        params = sim.SimParams(default_params_dict)
        call_count = {"n": 0}
        state = {"armed": True}
        lock = threading.Lock()

        def get_then_invalidate(la, lo, *_a):
            with lock:
                call_count["n"] += 1
                fire, state["armed"] = state["armed"], False
            if fire:
                if how == "force":
                    dem_prefetch.prefetch_tiles(
                        params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx,
                        force=True)
                elif how == "range":
                    dem_cache.delete_tile_cache(
                        params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx)
                else:
                    dem_cache.delete_all_tile_cache()
            return 100.0

        monkeypatch.setattr(dem, "get_elevation", get_then_invalidate)

        self._fetch_elevs(params)
        assert not state["armed"]
        first = call_count["n"]
        assert first == params.num
        self._fetch_elevs(params)

        assert call_count["n"] == first * 2, \
            "無効化と並んだ計算の地形が登録され、次の計算がそれに当たっている"

    def test_cache_hit_returns_same_array(self, default_params_dict, monkeypatch):
        """キャッシュヒット時に返る配列が1回目と同じ値であること。"""
        monkeypatch.setattr(dem, "get_elevation", lambda la, lo, *_a: 42.0)
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
        def counting_get(la, lo, *_a):
            call_count["n"] += 1
            # ⚠️ nan を返さない：全点 nan は「DEM 全滅」としてキャッシュされない
            # （B-025・3.2）ので、nan だとキャッシュの共有可否を検査できなくなる。
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

    def test_different_dem_source_not_shared(self, default_params_dict, monkeypatch):
        """DEM ソースが異なる場合はキャッシュを共有しないこと（B-225）。

        座標・標本数・段階が同一でも、DEM ソースを切り替えたら地形を
        取り直す。初版はキャッシュキーに `dem_source` が入っておらず、
        ソースを切り替えても前回ソースの標高を黙って使い回していた。
        """
        call_count = {"n": 0}
        def counting_get(la, lo, *_a):
            call_count["n"] += 1
            return 120.0
        monkeypatch.setattr(dem, "get_elevation", counting_get)
        # `resolve` を恒等写像にする：組み込みソースが GSI_DEM 一つしかなく、
        # 未知の source_id は本来フォールバックしてしまうため、ここでは
        # 「解決結果が異なる2ソース」を直接作って検査する。
        monkeypatch.setattr(
            dem_sources, "resolve",
            lambda source_id: types.SimpleNamespace(source_id=source_id),
        )

        gsi = default_params_dict.copy()
        gsi["dem_source"] = "gsi_dem"
        params_gsi = sim.SimParams(gsi)

        aws = default_params_dict.copy()
        aws["dem_source"] = "terrarium_aws"
        params_aws = sim.SimParams(aws)

        for params in (params_gsi, params_aws):
            done = threading.Event()
            sim.fetch_elevations_cached(
                params=params, on_progress=lambda v: None,
                on_complete=lambda e: done.set(), on_error=lambda ex: None,
            )
            done.wait(timeout=5)

        # 2つの異なるソース分が取得されている（使い回していない）
        assert call_count["n"] == params_gsi.num + params_aws.num

    def test_cache_hit_calls_on_progress_with_total(self, default_params_dict, monkeypatch):
        """キャッシュヒット時は on_progress(num) が呼ばれてプログレスバーが満杯になること。"""
        # ⚠️ nan を返さない：全点 nan はキャッシュされない（B-025・3.2）ため、
        # 2回目がキャッシュヒットにならず、この検査が素通りしてしまう。
        monkeypatch.setattr(dem, "get_elevation", lambda la, lo, *_a: 120.0)
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

    # --- DEM 全滅（all nan）を焼き付けない -----------------------------------
    # B-025：`dem.get_elevation` は全レイヤが通信の失敗で終わると `nan` を返す
    # （3.2 で契約を是正済み・以前は「取れなかった」ことが `0.0` と区別できな
    # かった）。Proxy 未設定などで取得が全滅すると標高 0m の平坦地形が正常値の
    # 顔で出ていたのがこの不具合で、それが地形キャッシュに入ると**Proxy を
    # 直してもアプリを再起動するまで直らない**（キャッシュはプロセス常駐）。

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
        """全点 nan（通信の失敗）の結果はキャッシュに入らず、次回はやり直すこと。"""
        call_count = {"n": 0}
        def failing_get(la, lo, *_a):
            call_count["n"] += 1
            return np.nan                   # ＝全レイヤが通信の失敗で終わった戻り値（3.2）
        monkeypatch.setattr(dem, "get_elevation", failing_get)

        params = sim.SimParams(default_params_dict)
        self._run_once(params)
        after_first = call_count["n"]
        assert after_first == params.num

        self._run_once(params)
        assert call_count["n"] == after_first + params.num, (
            "全点 nan の地形がキャッシュされている"
            "＝Proxy を直しても再起動するまで平坦地形が返り続ける"
        )

    def test_all_zero_result_is_still_delivered(self, default_params_dict, monkeypatch):
        """キャッシュしないだけで、結果自体は今までどおり返ること（値は nan のまま）。

        ここで握り潰すと「実行したのに何も起きない」になる。失敗の伝播と画面での
        提示は別の対応（B-025 の ②③）で、この変更の担当ではない。
        """
        monkeypatch.setattr(dem, "get_elevation", lambda la, lo, *_a: np.nan)
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
        def flaky_get(la, lo, *_a):
            state["n"] += 1
            return np.nan if state["fail"] else 120.0
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
        def mostly_zero_get(la, lo, *_a):
            call_count["n"] += 1
            return 0.0 if call_count["n"] > 1 else 30.0
        monkeypatch.setattr(dem, "get_elevation", mostly_zero_get)

        params = sim.SimParams(default_params_dict)
        self._run_once(params)
        after_first = call_count["n"]

        self._run_once(params)
        assert call_count["n"] == after_first, "一部だけ 0.0 の地形までキャッシュを拒んでいる"


# ============================================================
# _format_dem_source_line（report.txt の DEM ソース刻印）
# ============================================================
class TestFormatDemSourceLine:
    """I-147 残り(a)＝外部ソースだけ宣言内容のハッシュを末尾に添えること。"""

    def test_gsi_source_has_no_fingerprint_suffix(self):
        line = sim._format_dem_source_line(dem_sources.GSI_DEM.source_id)
        assert line == f"DEM Source    : {dem_sources.GSI_DEM.display_name} ({dem_sources.GSI_DEM.attribution})\n"
        assert "[" not in line

    def test_external_source_has_a_fingerprint_suffix(self, monkeypatch):
        fake = dem_sources.DemSourceSpec(
            source_id="fake_src", display_name="Fake Source",
            layers=(("fake_layer", 12),), url_template="https://example.invalid/{z}/{x}/{y}.png",
            decode=dem_sources.DecodeMethod.TERRARIUM, invalid_rgb=None,
            attribution="Fake Attribution", terms_url="https://example.invalid",
        )
        monkeypatch.setattr(dem_sources, "_user_sources", [fake])
        line = sim._format_dem_source_line("fake_src")
        fp = dem_sources.definition_fingerprint(fake)
        assert line == f"DEM Source    : Fake Source (Fake Attribution) [{fp}]\n"

    def test_fingerprint_changes_when_the_declaration_changes(self, monkeypatch):
        """`source_id` を変えずに URL だけ書き換えたら別のハッシュになること
        （B-236 のキャッシュ無効化と同じ根拠）。"""
        base = dem_sources.DemSourceSpec(
            source_id="fake_src", display_name="Fake Source",
            layers=(("fake_layer", 12),), url_template="https://example.invalid/a/{z}/{x}/{y}.png",
            decode=dem_sources.DecodeMethod.TERRARIUM, invalid_rgb=None,
            attribution="Fake", terms_url="https://example.invalid",
        )
        rewritten = dataclasses.replace(
            base, url_template="https://example.invalid/b/{z}/{x}/{y}.png")

        monkeypatch.setattr(dem_sources, "_user_sources", [base])
        line_before = sim._format_dem_source_line("fake_src")
        monkeypatch.setattr(dem_sources, "_user_sources", [rewritten])
        line_after = sim._format_dem_source_line("fake_src")
        assert line_before != line_after


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


class TestDemAcquiredFollowsTheTileActuallyUsed:
    """刻印「DEM Acquired」は**標高を返したタイル**の取得日であること（B-213）。

    🔴 **保存時に座標からタイルを引き直していた**（3.3 ステージ4e の初版）＝5m タイルが
    一時失敗して 10m の古いタイルで計算した標本も、同じ回の後の標本で 5m が取れて
    いると、保存時には 5m の日付に化けた（一時失敗は負キャッシュしない＝B-010
    なので、後の標本では 5m が取れる）。見るのは「古いタイルで計算した標本の日付が
    範囲に残るか」。
    """

    OLD = "2026-08-01"

    @pytest.fixture(autouse=True)
    def _isolated_tiles(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path / "dem"))
        # 1 本のワーカーで順に取る＝「先の標本で失敗・後の標本で成功」を決定的にする
        monkeypatch.setattr(sim, "_MAX_FETCH_WORKERS", 1)
        dem._tile_cache.clear()
        dem._failed_tiles.clear()
        yield
        dem._tile_cache.clear()
        dem._failed_tiles.clear()

    def _fake_fetch_tile(self):
        """5a＝最初の 1 回だけ一時失敗・以後は今日のタイル／5b＝データ無し（404）／
        10m＝8 月 1 日に取った古いタイル。"""
        import datetime as dt
        from PIL import Image

        first_5m = [True]
        valid = (0, 39, 16)          # 有効な標高（!= 0.0）になる画素

        def fake(layer_id, zoom, xtile, ytile, cache_subdir, cache_path, source=None):
            if layer_id == dem.DEM_LAYERS[0][0]:
                if first_5m[0]:
                    first_5m[0] = False
                    dem._network_trouble.flag = True
                    return None
            elif layer_id != dem.DEM_LAYERS[-1][0]:
                with dem._cache_lock:
                    dem._failed_tiles.add(("gsi_dem", layer_id, xtile, ytile))
                return None
            if not os.path.exists(cache_path):
                os.makedirs(cache_subdir, exist_ok=True)
                Image.new("RGB", (256, 256), valid).save(cache_path)
                if layer_id == dem.DEM_LAYERS[-1][0]:
                    old = dt.datetime.fromisoformat(self.OLD + "T12:00:00").timestamp()
                    os.utime(cache_path, (old, old))
            return np.full((256, 256, 3), valid, dtype=np.uint8)

        return fake

    @staticmethod
    def _fetch(params) -> dict:
        """`fetch_elevations_cached` を 1 回回し、`on_acquired` と `on_complete` の
        受け取りを順に記録して返す。"""
        done = threading.Event()
        box: dict = {"order": []}

        def _acquired(value) -> None:
            box["order"].append("acquired")
            box["acquired"] = value

        def _complete(elevs) -> None:
            box["order"].append("complete")
            done.set()

        def _error(ex: Exception) -> None:
            box["error"] = ex
            done.set()

        sim.fetch_elevations_cached(params, lambda n: None, _complete, _error,
                                    on_acquired=_acquired)
        assert done.wait(timeout=10) and "error" not in box, box
        return box

    def test_fallback_tile_date_survives_a_later_success(self, default_params_dict,
                                                         monkeypatch):
        import datetime as dt

        monkeypatch.setattr(dem, "_fetch_tile", self._fake_fetch_tile())
        box = self._fetch(sim.SimParams(default_params_dict))
        acquired = box["acquired"]
        assert acquired == (self.OLD, dt.date.today().isoformat()), (
            "10m の古いタイルで計算した標本の取得日が消えている"
            f"（保存時にタイルを引き直している＝{acquired}）"
        )
        # ウィンドウは on_complete で開く＝その時点で取得日が手元に無いと抱えられない
        assert box["order"] == ["acquired", "complete"], box["order"]

    def test_cache_hit_hands_over_the_date_of_the_fetch_that_filled_it(
            self, default_params_dict, monkeypatch):
        """地形キャッシュに命中した実行も、その地形を取った回の取得日を受け取ること
        （同じタイルで計算しているので正しい）。"""
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_fetch_tile())
        params = sim.SimParams(default_params_dict)
        first = self._fetch(params)["acquired"]
        # 2 回目はタイルを 1 枚も読まない（読めば今日のタイルが混ざり得る）
        monkeypatch.setattr(dem, "_fetch_tile",
                            lambda *a, **k: pytest.fail("キャッシュ命中なのにタイルを読んだ"))
        hit = self._fetch(params)
        assert hit["acquired"] == first, hit
        assert hit["order"] == ["acquired", "complete"], hit["order"]


class TestSavePackage:

    def _run_save(self, tmp_path, flat_terrain, default_params_dict, monkeypatch,
                  diff_method="single", coord_format="dd", dem_source=None):
        monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path))
        # 空のキャッシュ（=常に「取得日不明」）に固定＝実機のキャッシュに
        # たまたま同じ座標が残っていても結果が揺れないようにする（3.3 ステージ4e）。
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path / "empty_dem_cache"))
        default_params_dict["diff_method"] = diff_method
        if dem_source is not None:
            default_params_dict["dem_source"] = dem_source
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

    def test_report_spacing_comes_from_the_samples_actually_used(
        self, tmp_path, flat_terrain, default_params_dict, monkeypatch
    ):
        """帳票の実効間隔は**実際に刻んだ点数**から出ること（B-137）。

        🔴 **これは配線の検査**＝`simulation.effective_spacing` を直接呼ぶ検査は、
        `_save_report` が*段階から計算し直す*形へ戻っても緑のままだった
        （実測＝変異 M2 が素通り）。「計算」を検査するテストは「呼ばれること」を
        検査しない（→ [[feedback-independent-review]]）。
        """
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch)
        text = open(os.path.join(save_dir, "report.txt"), encoding="utf-8").read()

        params = sim.SimParams(default_params_dict)
        expected = units.format_spacing(sim.effective_spacing(params))
        assert f"Samples       : {params.num} ({expected} m spacing)" in text, text

        # 段階から計算し直した値とは実際に食い違う＝この検査が空振りでないこと。
        _, from_level = sim.resolve_samples(
            params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx,
            tg.RESOLUTION_DEFAULT)
        assert units.format_spacing(from_level) != expected

    def test_report_does_not_claim_a_level_it_did_not_use(
        self, tmp_path, flat_terrain, default_params_dict, monkeypatch
    ):
        """段階を経由しない実行の帳票は、段階を**名乗らない**こと（B-137）。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch)
        text = open(os.path.join(save_dir, "report.txt"), encoding="utf-8").read()
        assert "Terrain Res   : (fixed sample count)" in text, text
        for level in tg.RESOLUTION_KEYS:
            assert f"Terrain Res   : {level}" not in text

    def test_report_contains_dem_fail_rate(self, tmp_path, flat_terrain,
                                           default_params_dict, monkeypatch):
        """report.txt に DEM Fail Rate 行が含まれること（3.2 ステージ7・B-025 ③）。

        `flat_terrain` は全点取得済み（nan なし）なので 0.0 %。単一ソースは
        `models.TerrainProfile.fail_pct`（CSV 出力契約の `dem_fail_pct` と同じ）。
        """
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch)
        text = open(os.path.join(save_dir, "report.txt"), encoding="utf-8").read()
        assert "DEM Fail Rate : 0.0 %" in text, text

    def test_report_dem_fail_rate_reflects_actual_failures(
        self, tmp_path, default_params_dict, monkeypatch
    ):
        """一部の標本が nan（通信失敗）の地形では、その割合を名乗ること。"""
        monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path / "empty_dem_cache"))
        raw = np.zeros(100)
        raw[:25] = np.nan
        terrain = models.calculate_terrain_profile(
            raw, 34.5429, 132.4118, 34.5389, 132.4050)
        params = sim.SimParams(default_params_dict)
        result = _make_result(params.diff_method)
        save_dir = sim.save_package(terrain, result, params, 30.0, 10.0)
        text = open(os.path.join(save_dir, "report.txt"), encoding="utf-8").read()
        assert "DEM Fail Rate : 25.0 %" in text, text

    def test_report_contains_dem_acquired_date(self, tmp_path, flat_terrain,
                                               default_params_dict, monkeypatch):
        """report.txt に DEM Acquired 行が含まれること（3.3 ステージ4e＝出所刻印の
        最後の要素）。**実行日（Date:）ではなくタイルの取得日**＝値は標高を
        取った時点で確定し地形と一緒に運ばれたもの（`TerrainProfile.dem_acquired`・
        B-213。取得側の検査は TestDemAcquiredFollowsTheTileActuallyUsed）。
        """
        terrain = dataclasses.replace(flat_terrain,
                                      dem_acquired=("2026-08-01", "2026-08-01"))
        save_dir = self._run_save(tmp_path, terrain, default_params_dict,
                                  monkeypatch)
        text = open(os.path.join(save_dir, "report.txt"), encoding="utf-8").read()
        assert "DEM Acquired  : 2026-08-01" in text, text

    def test_report_dem_acquired_shows_range_when_tiles_differ(
        self, tmp_path, flat_terrain, default_params_dict, monkeypatch
    ):
        """標本ごとにタイルの取得日が違えば、最古〜最新の範囲で示す（広域の経路は
        タイルが別日にまたがり得る）。"""
        terrain = dataclasses.replace(flat_terrain,
                                      dem_acquired=("2026-08-01", "2026-09-10"))
        save_dir = self._run_save(tmp_path, terrain, default_params_dict,
                                  monkeypatch)
        text = open(os.path.join(save_dir, "report.txt"), encoding="utf-8").read()
        assert "DEM Acquired  : 2026-08-01 to 2026-09-10" in text, text

    def test_report_dem_acquired_survives_clearing_the_terrain_cache(
        self, tmp_path, flat_terrain, default_params_dict, monkeypatch
    ):
        """地形キャッシュを消しても、手元の結果の取得日は report.txt に出ること
        （Codex 99 巡目＝結果のウィンドウを開いたままプロキシ設定の OK を押すと
        `clear_terrain_cache` が走る。初版の修正は取得日を鍵で引く共有の辞書に
        置いていたので、ここで行が消えた）。"""
        terrain = dataclasses.replace(flat_terrain,
                                      dem_acquired=("2026-08-01", "2026-08-01"))
        sim.clear_terrain_cache()
        save_dir = self._run_save(tmp_path, terrain, default_params_dict,
                                  monkeypatch)
        text = open(os.path.join(save_dir, "report.txt"), encoding="utf-8").read()
        assert "DEM Acquired  : 2026-08-01" in text, text

    def test_report_omits_dem_acquired_when_unavailable(
        self, tmp_path, flat_terrain, default_params_dict, monkeypatch
    ):
        """1点も取得日が分からなければ行ごと出さない（`flat_terrain` は取得を
        経ていない＝`dem_acquired` は None）。"""
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch)
        text = open(os.path.join(save_dir, "report.txt"), encoding="utf-8").read()
        assert "DEM Acquired" not in text, text

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

    def test_settings_json_contains_dem_source(self, tmp_path, flat_terrain,
                                               default_params_dict, monkeypatch):
        """settings.json に dem_source キーが保存されること（B-230）。

        修正前はこのキーが無く、「パラメータ読込」で外部 DEM ソースの計算結果を
        読み込むと国土地理院へ戻ってしまい、計算を再現できなかった。
        """
        save_dir = self._run_save(tmp_path, flat_terrain, default_params_dict,
                                  monkeypatch, dem_source="terrarium_aws")
        with open(os.path.join(save_dir, "settings.json"), encoding="utf-8") as f:
            settings = json.load(f)
        assert settings.get("dem_source") == "terrarium_aws"

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
        assert rows[0] == ["Distance_m", "Elevation_m", "elev_source"]
        assert len(rows) - 1 == flat_terrain.num_samples

    def test_terrain_csv_marks_failed_samples_as_unavailable(
        self, tmp_path, default_params_dict, monkeypatch,
    ):
        """nan の標本は `Elevation_m=`（空欄）・`elev_source=unavailable` になること
        （ISSUES.md B-025 ③・3.2 で予告・3.3 で実施＝規約2）。実値の標本は `gsi_dem`。
        `0.0` は海抜0mの正当な値の意味に一本化された。
        """
        import csv
        raw = np.array([10.0, np.nan, 20.0])
        terrain = models.calculate_terrain_profile(
            raw, 34.5429, 132.4118, 34.5389, 132.4050,
        )
        monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path / "empty_dem_cache"))
        params = sim.SimParams(default_params_dict)
        result = _make_result("single")
        save_dir = sim.save_package(terrain, result, params, 30.0, 10.0)
        with open(os.path.join(save_dir, "terrain_profile.csv"),
                  newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert rows[1][1:] == ["10.0", "gsi_dem"]
        assert rows[2][1:] == ["", "unavailable"]
        assert rows[3][1:] == ["20.0", "gsi_dem"]
