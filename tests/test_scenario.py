"""
tests/test_scenario.py
======================
条件探索（2.5 / A-1 比較・A-2 スイープ）のヘッドレス検証。

対象は [scenario.py](scenario.py)（共有ランナー・フェーズの宣言）と
[report_scenario.py](report_scenario.py)（A4 シート・CSV）。DEM 取得は
monkeypatch で塞ぎ、ネットワーク無しで実行する。

**ここで守っているもの**:
  - 「DEM 取得 1 回 + run_calculation を N 回」＝2.5 の成立条件そのもの。
    取得が条件数ぶん走ったら、それは条件探索ではなくバッチの再実装になっている。
  - フェーズの宣言＝**重いフェーズが進捗の管轄外に置かれない**（B-006／I-008 の
    構造対策）。レポート生成まで含めて 100% に到達すること。
  - 上書きがベース params を汚さないこと（A-1/A-2 は同じ params を N 回使う）。
"""

import os
import sys
import threading

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import config
from core import i18n
from core import models
from core import scenario as scn
from core import simulation as sim
from core import units
from report import report_common
from report import report_scenario


# ============================================================
# ヘルパー
# ============================================================
def _fake_fetch(params, on_progress, on_complete, on_error):
    """DEM 取得のフェイク（ネットワーク無し・中央に尾根を持つ地形）。"""
    raw = np.zeros(params.num)
    raw[params.num // 2 - 3:params.num // 2 + 3] = 120.0
    on_progress(params.num)
    on_complete(raw)


def _run_sync(monkeypatch, base, conditions, **kwargs):
    """run_scenario を同期的に回して ScenarioRun を返す（失敗時は例外）。"""
    monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)
    monkeypatch.setattr(sim, "_terrain_cache", {})
    out, err = [], []
    done = threading.Event()
    scn.run_scenario(
        base, conditions,
        on_complete=lambda run: (out.append(run), done.set()),
        on_error=lambda ex: (err.append(ex), done.set()),
        **kwargs,
    )
    assert done.wait(timeout=30), "run_scenario が完了しない"
    if err:
        raise err[0]
    return out[0]


@pytest.fixture
def base(default_params_dict):
    return sim.SimParams(default_params_dict)


@pytest.fixture
def terrain(flat_terrain):
    return flat_terrain


# ============================================================
# Condition / 上書き
# ============================================================
class TestCondition:

    def test_rejects_unknown_override(self):
        # メッセージは i18n（言語で変わる）ので、**どの言語でも出る要素**＝
        # 弾かれた項目名で照合する。
        with pytest.raises(ValueError, match="samples"):
            scn.Condition(label="A", overrides={"samples": 100})

    def test_rejects_coordinate_override(self):
        """座標は固定＝経路自体の比較はバッチの仕事（2026-07-25 決定）。"""
        with pytest.raises(ValueError):
            scn.Condition(label="A", overrides={"lat_tx": 34.5})

    def test_accepts_all_overridable_keys(self, base):
        cond = scn.Condition(label="A", overrides={k: getattr(base, k)
                                                   for k in scn.OVERRIDABLE})
        assert set(cond.overrides) == set(scn.OVERRIDABLE)


class TestConditionValidatesValues:
    """値域の検証（B-016）。**DEM 取得の前に**弾くことが要点。

    検証が無かった頃の実測（2026-07-26）:
      - 周波数 0 → ZeroDivisionError / 負 → ValueError（生の英語・実行ごと失われる）
      - 高さ -50m・降雨 -10mm/h → **黙って計算が通る**（もっともらしい数字が出る）
      - `inf` → p_rx=inf で **判定 OK** まで出る
    範囲の出所は config.VALIDATION_RULES（単一実行のランチャーと同じ表）。
    """

    @pytest.mark.parametrize("key,value", [
        ("freq_mhz", 0.0),        # ZeroDivisionError を起こしていた値
        ("freq_mhz", -100.0),
        ("freq_mhz", 200000.0),
        ("h_tx", -50.0),          # 黙って通っていた値
        ("h_rx", 5000.0),
        ("rain_rate", -10.0),     # 黙って通っていた値
        ("veg_h", -1.0),
        ("sens", 0.0),            # 感度 0 dBm は範囲外（-130〜-20）
        ("gain_tx", -5.0),
        ("k_factor", 1000.0),
    ])
    def test_rejects_out_of_range(self, key, value):
        with pytest.raises(ValueError, match=str(value).rstrip("0").rstrip(".")):
            scn.Condition(label="A", overrides={key: value})

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_rejects_nan_and_inf(self, value):
        """`float()` は nan / inf を通す＝範囲比較だけでは守れない。

        inf は**判定 OK** を出していた（レポートにそのまま載る）。
        """
        with pytest.raises(ValueError):
            scn.Condition(label="A", overrides={"p_tx": value})

    @pytest.mark.parametrize("key,value", [
        # ⚠️ **実在しない値を選ぶ**＝"bullington" は 2026-08-26 に有効値になった
        #    （B-130）。ここが緑のまま意味を失う典型なので、未実装の手法名にした。
        ("env_type", "forest"), ("diff_method", "epstein_peterson"),
    ])
    def test_rejects_unknown_choice(self, key, value):
        with pytest.raises(ValueError, match=value):
            scn.Condition(label="A", overrides={key: value})

    def test_accepts_values_inside_the_range(self, base):
        """正常値は通る（過検出でスクリーニングの幅を狭めない）。"""
        scn.Condition(label="A", overrides={
            "freq_mhz": 1.0, "h_tx": 500.0, "rain_rate": 200.0,
            "sens": -130.0, "gain_rx": 60.0, "env_type": "los",
        })

    def test_range_source_is_config_not_a_copy(self):
        """範囲の出所が config.VALIDATION_RULES であること（二重管理の検出）。

        条件探索が独自の表を持つと、ランチャーで通る値が条件探索で弾かれる
        （またはその逆）というフロー間のずれが起きる。上限ちょうど＋1 で
        落ちることを、**config の値から計算して**確かめる。
        """
        vmax = config.VALIDATION_RULES["freq"][1]
        scn.Condition(label="ok", overrides={"freq_mhz": vmax})
        with pytest.raises(ValueError):
            scn.Condition(label="ng", overrides={"freq_mhz": vmax + 1})

    def test_every_overridable_numeric_key_has_a_range(self):
        """上書きできる数値項目には**必ず**値域があること。

        新しい項目を OVERRIDABLE に足したとき、値域を書き忘れると素通しになる
        （B-016 そのもの＝「検証を足し忘れても誰も気づかない」）。ここで落とす。
        """
        missing = [
            k for k in scn.OVERRIDABLE
            if k not in ("env_type", "diff_method")
            and config.VALIDATION_RULES.get(config._ATTR_TO_RULE_KEY.get(k, k)) is None
        ]
        assert not missing, (
            f"値域が定義されていない上書き項目がある: {missing}。"
            "config.VALIDATION_RULES に足すこと（範囲の出所は 1 つ）。"
        )

    def test_sweep_rejects_a_range_that_crosses_invalid_values(self):
        """軸の**途中**に不正値がある場合も弾くこと（端だけ見ない）。

        周波数 -100〜100 MHz のようなスイープは、途中で 0 を跨いで
        ZeroDivisionError を起こしていた。
        """
        values = scn.linspace_values(-100.0, 100.0, 5)
        with pytest.raises(ValueError):
            scn.sweep_conditions("freq_mhz", values)


class TestBaseParamsAreValidated:
    """ベース params 自体の検証（B-016 のクラス点検で見つけた穴）。

    比較の「ベース」列は上書きゼロの Condition なので、Condition の検証だけでは
    **ベースの値が 1 つも検査されない**。条件探索はランチャーの値を*実行せずに*
    スナップショットして持ってくるので、不正値のまま計算へ入り得る。
    """

    def test_invalid_base_is_reported_before_fetching_dem(self, monkeypatch,
                                                          default_params_dict):
        """DEM 取得**前**に on_error で落ちること（待たせてから落とさない）。"""
        fetched = []
        monkeypatch.setattr(sim, "fetch_elevations",
                            lambda *a, **k: fetched.append(1))
        bad = sim.SimParams({**default_params_dict, "freq": "0"})
        errs: list[Exception] = []
        scn.run_scenario(bad, [scn.Condition("base", {})],
                         on_complete=lambda run: None,
                         on_error=errs.append)
        assert errs, "不正なベースがそのまま実行された"
        assert not fetched, "DEM を取得してから落ちている（待ち時間を捨てさせる）"

    def test_valid_base_passes(self, default_params_dict):
        assert scn.validate_base(sim.SimParams(default_params_dict)) == []

    def test_reports_every_bad_field_at_once(self, default_params_dict):
        """複数の不正値をまとめて返すこと（1 つ直すたびに再実行させない）。"""
        bad = sim.SimParams({**default_params_dict, "freq": "0", "veg_h": "-5"})
        assert len(scn.validate_base(bad)) == 2


class TestApplyOverrides:

    def test_base_params_are_not_mutated(self, base):
        before = dict(vars(base))
        scn._apply(base, {"freq_mhz": 5600.0, "h_tx": 99.0})
        assert dict(vars(base)) == before, "ベース params が書き換えられた"

    def test_override_takes_effect(self, base):
        p = scn._apply(base, {"freq_mhz": 5600.0})
        assert p.freq_mhz == 5600.0

    def test_unrelated_fields_are_inherited(self, base):
        p = scn._apply(base, {"freq_mhz": 5600.0})
        assert p.p_tx == base.p_tx and p.sens == base.sens


# ============================================================
# evaluate（terrain 固定・純計算）
# ============================================================
class TestEvaluate:

    def test_one_point_per_condition(self, terrain, base):
        conds = [scn.Condition("A", {"h_tx": 10.0}),
                 scn.Condition("B", {"h_tx": 60.0})]
        points = scn.evaluate(terrain, base, conds)
        assert [p.label for p in points] == ["A", "B"]

    def test_height_override_reaches_run_calculation(self, terrain, base):
        """h_tx は params ではなく run_calculation の引数で効く経路を通ること。"""
        low  = scn.evaluate(terrain, base, [scn.Condition("l", {"h_tx": 2.0})])[0]
        high = scn.evaluate(terrain, base, [scn.Condition("h", {"h_tx": 120.0})])[0]
        assert high.result.actual_margin > low.result.actual_margin
        assert (low.h_tx, high.h_tx) == (2.0, 120.0)

    def test_terrain_is_not_mutated(self, terrain, base):
        raw = terrain.raw_elevs.copy()
        curve = terrain.elevs_with_curve.copy()
        scn.evaluate(terrain, base, [scn.Condition("A", {"rain_rate": 120.0})])
        assert np.array_equal(terrain.raw_elevs, raw)
        assert np.array_equal(terrain.elevs_with_curve, curve)

    def test_order_does_not_change_values(self, terrain, base):
        a = scn.Condition("A", {"freq_mhz": 900.0})
        b = scn.Condition("B", {"freq_mhz": 15000.0})
        fwd = scn.evaluate(terrain, base, [a, b])
        rev = scn.evaluate(terrain, base, [b, a])
        assert fwd[0].result.p_rx == rev[1].result.p_rx
        assert fwd[1].result.p_rx == rev[0].result.p_rx

    def test_progress_callback_counts_points(self, terrain, base):
        seen: list[tuple[int, int]] = []
        conds = [scn.Condition(str(i), {"h_tx": float(i)}) for i in (5, 15, 25)]
        scn.evaluate(terrain, base, conds, on_point=lambda d, t: seen.append((d, t)))
        assert seen == [(1, 3), (2, 3), (3, 3)]

    def test_cancel_stops_before_the_next_condition(self, terrain, base):
        """取り消しは次の条件の手前で `Cancelled` を投げる（4.0 ステージ6）。"""
        cancel = threading.Event()
        seen: list[int] = []
        conds = [scn.Condition(str(i), {"h_tx": float(i)}) for i in (5, 15, 25)]

        def _on_point(done, _total):
            seen.append(done)
            cancel.set()

        with pytest.raises(sim.Cancelled):
            scn.evaluate(terrain, base, conds, on_point=_on_point, cancel=cancel)
        assert seen == [1], "取り消した後の条件を計算した"


# ============================================================
# スイープ軸
# ============================================================
class TestSweepConditions:

    def test_rejects_categorical_axis(self):
        """env_type / diff_method は離散＝軸にせず比較（A-1）で扱う。"""
        with pytest.raises(ValueError, match="env_type"):
            scn.sweep_conditions("env_type", [1.0, 2.0])

    def test_rejects_single_point(self):
        with pytest.raises(ValueError):
            scn.sweep_conditions("h_tx", [10.0])

    def test_rejects_too_many_points(self):
        values = [float(i) for i in range(scn.MAX_SWEEP_POINTS + 1)]
        with pytest.raises(ValueError, match=str(scn.MAX_SWEEP_POINTS)):
            scn.sweep_conditions("h_tx", values)

    def test_labels_are_the_axis_values(self):
        conds = scn.sweep_conditions("h_tx", [10.0, 20.5])
        assert [c.label for c in conds] == ["10", "20.5"]
        assert conds[1].overrides == {"h_tx": 20.5}

    def test_linspace_values_are_inclusive(self):
        assert scn.linspace_values(10, 50, 5) == [10.0, 20.0, 30.0, 40.0, 50.0]


# ============================================================
# フェーズの宣言
# ============================================================
class TestPhases:

    def test_progress_is_weighted_across_phases(self):
        pcts: list[int] = []
        ph = scn.Phases([scn.Phase("fetch", 8.0), scn.Phase("calc", 2.0)],
                        on_progress=pcts.append)
        ph.start(scn.Phase("fetch", 8.0))
        ph.advance(1, 2)                 # fetch 半分 → 全体 40%
        assert pcts[-1] == 40
        ph.start(scn.Phase("calc", 2.0))  # fetch 完了 → 80%
        assert pcts[-1] == 80
        ph.advance(1, 1)
        assert pcts[-1] == 100

    def test_phase_names_are_reported(self):
        names: list[str] = []
        ph = scn.Phases([scn.FETCH, scn.CALC], on_phase=names.append)
        ph.start(scn.FETCH)
        ph.start(scn.CALC)
        assert names == ["fetch", "calc"]

    def test_out_of_order_start_is_rejected(self):
        ph = scn.Phases([scn.FETCH, scn.CALC])
        ph.start(scn.CALC)
        with pytest.raises(ValueError, match="順序"):
            ph.start(scn.FETCH)

    def test_advance_before_start_is_rejected(self):
        ph = scn.Phases([scn.FETCH])
        with pytest.raises(ValueError, match="start"):
            ph.advance(1, 2)

    def test_finish_reaches_100(self):
        pcts: list[int] = []
        ph = scn.Phases([scn.FETCH, scn.CALC], on_progress=pcts.append)
        ph.start(scn.FETCH)
        ph.finish()
        assert pcts[-1] == 100


# ============================================================
# run_scenario（DEM 取得 1 回 + N 回の純計算）
# ============================================================
class TestRunScenario:

    def test_fetches_terrain_once_for_all_conditions(self, base, monkeypatch):
        """**2.5 が成立する条件そのもの**＝取得も地形構築も 1 回、計算だけ N 回。

        ⚠️ 「DEM 取得の回数」だけを数えても足りない＝`fetch_elevations_cached` が
        効くと 2 回目以降は網に掛からず、条件ごとに取り直す実装でもテストが通って
        しまう（実際にこの変異を仕込んで素通りすることを確認した）。**地形構築
        （calculate_terrain_profile）と計算（run_calculation）の回数**まで見る。
        """
        fetches, terrains, calcs = [], [], []
        real_fetch = sim.fetch_elevations_cached
        real_terrain = models.calculate_terrain_profile
        real_calc = sim.run_calculation

        def _spy_fetch(params, on_progress, on_complete, on_error, **kw):
            fetches.append(1)
            return real_fetch(params, on_progress, on_complete, on_error, **kw)

        def _spy_terrain(*a, **k):
            terrains.append(1)
            return real_terrain(*a, **k)

        def _spy_calc(*a, **k):
            calcs.append(1)
            return real_calc(*a, **k)

        monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)
        monkeypatch.setattr(sim, "_terrain_cache", {})
        monkeypatch.setattr(sim, "fetch_elevations_cached", _spy_fetch)
        monkeypatch.setattr(scn.models, "calculate_terrain_profile", _spy_terrain)
        monkeypatch.setattr(scn.sim, "run_calculation", _spy_calc)

        out = []
        done = threading.Event()
        scn.run_scenario(
            base, scn.sweep_conditions("h_tx", [10.0, 20.0, 30.0, 40.0]),
            on_complete=lambda r: (out.append(r), done.set()),
            on_error=lambda ex: (out.append(ex), done.set()),
            kind="sweep", axis="h_tx", axis_values=[10.0, 20.0, 30.0, 40.0],
        )
        assert done.wait(timeout=30)
        assert len(fetches) == 1, f"標高取得が {len(fetches)} 回走った（1 回で足りる）"
        assert len(terrains) == 1, f"地形構築が {len(terrains)} 回走った（1 回で足りる）"
        assert len(calcs) == 4, f"純計算が {len(calcs)} 回（条件数と一致すべき）"

    def test_progress_reaches_100_including_artifacts(self, base, monkeypatch, tmp_path):
        """レポート生成まで進捗率に乗ること（重いフェーズを管轄外にしない）。"""
        pcts: list[int] = []
        phases: list[str] = []
        i18n.set_lang("en")
        run = _run_sync(
            monkeypatch, base, scn.sweep_conditions("h_tx", [10.0, 30.0]),
            kind="sweep", axis="h_tx", axis_values=[10.0, 30.0],
            on_progress=pcts.append, on_phase=phases.append,
            artifacts=lambda r: report_scenario.save_scenario_package(r, str(tmp_path)),
        )
        assert run.kind == "sweep"
        assert phases == ["fetch", "calc", "render"]
        assert pcts[-1] == 100
        assert pcts == sorted(pcts), f"進捗率が巻き戻った: {pcts}"

    def test_error_is_delivered_to_on_error(self, base, monkeypatch):
        def _boom(params, on_progress, on_complete, on_error):
            on_error(RuntimeError("DEM fetch failed (fake)"))

        monkeypatch.setattr(sim, "fetch_elevations", _boom)
        monkeypatch.setattr(sim, "_terrain_cache", {})
        errs = []
        done = threading.Event()
        scn.run_scenario(
            base, scn.sweep_conditions("h_tx", [10.0, 20.0]),
            on_complete=lambda r: done.set(),
            on_error=lambda ex: (errs.append(ex), done.set()),
            kind="sweep", axis="h_tx", axis_values=[10.0, 20.0],
        )
        assert done.wait(timeout=30)
        assert isinstance(errs[0], RuntimeError)

    def test_runs_off_the_main_thread(self, base, monkeypatch):
        """GUI を固めないこと（実行スレッドがメインでない）。"""
        seen: list[str] = []

        def _spy(params, on_progress, on_complete, on_error):
            seen.append(threading.current_thread().name)
            _fake_fetch(params, on_progress, on_complete, on_error)

        monkeypatch.setattr(sim, "fetch_elevations", _spy)
        monkeypatch.setattr(sim, "_terrain_cache", {})
        done = threading.Event()
        scn.run_scenario(
            base, scn.sweep_conditions("h_tx", [10.0, 20.0]),
            on_complete=lambda r: done.set(), on_error=lambda ex: done.set(),
            kind="sweep", axis="h_tx", axis_values=[10.0, 20.0],
        )
        assert done.wait(timeout=30)
        assert seen and seen[0] != threading.main_thread().name


# ============================================================
# 面をまたぐ一致（条件探索のベース ⇔ 個別）
# ============================================================
class TestScenarioBaseMatchesSingleRun:
    """**同じ経路・同じ条件なら、条件探索のベースと個別が同じ答えを出す**（B-157）。

    🔑 **これが無かったので B-157 が素通りした**＝面ごとに別々に検査していて、
    *面をまたいで同じ答えになること*を誰も見ていなかった。地形の作り方
    （`calculate_terrain_profile` へ何を渡すか）は 4 面に分かれて書かれており、
    1 面だけ引数を落としても、その面の中だけを見るテストは全部緑のままになる。
    ⚠️ **引数の有無を見る形にはしない**（`frac_axis=` を grep する検査は、
    渡した値が間違っていても通る）＝**出てくる値で比べる**。
    """

    # ⚠️ **段階（解像度）を指定する**＝固定 N モードは定義上どの面でも等間隔で、
    #    そこだけで比べると**一度も落ちないゲート**になる（B-150 以後の欠陥は
    #    「画素の縁＝非等間隔」でしか現れない）。
    def _staged_base(self, default_params_dict):
        d = dict(default_params_dict)
        d.pop("samples", None)
        d["resolution"] = "high"
        return sim.SimParams(d)

    def test_sample_positions_are_actually_uneven(self, default_params_dict):
        """前提の確認＝この経路の標本が**本当に非等間隔**であること。

        ここが等間隔だと下の一致検査は何も測らない（[[feedback-verification]]
        ＝対照のラベルが嘘をつく）。刻みの最大 / 最小で見る。
        """
        base = self._staged_base(default_params_dict)
        steps = np.diff(np.asarray(base.sample_fracs, dtype=float))
        assert base.num > 2
        assert steps.max() / steps.min() > 2.0, (
            "この経路の標本が等間隔＝B-157 の欠陥が現れない条件になっている"
            f"（刻み比 {steps.max() / steps.min():.3f}）")

    def test_base_point_matches_single_simulation(self, default_params_dict,
                                                  monkeypatch):
        """条件探索のベース点と、個別シミュレーションの受信レベルが一致する。"""
        base = self._staged_base(default_params_dict)

        # 個別の面（1 回線の計算と同じ形）で解いた答え
        raw = np.zeros(base.num)
        raw[base.num // 2 - 3:base.num // 2 + 3] = 120.0
        terrain_single = models.calculate_terrain_profile(
            raw_elevs=raw,
            lat_tx=base.lat_tx, lon_tx=base.lon_tx,
            lat_rx=base.lat_rx, lon_rx=base.lon_rx,
            frac_axis=base.sample_fracs,
        )
        expected = sim.run_calculation(
            terrain_single, base.h_tx, base.h_rx, base)

        # 条件探索の面（上書き無しのベース 1 点だけ）
        run = _run_sync(monkeypatch, base, [scn.Condition("base", {})])
        actual = run.points[0].result

        assert actual.actual_margin == pytest.approx(
            expected.actual_margin, abs=1e-9), (
            "条件探索のベースと個別でマージンが違う"
            f"（探索 {actual.actual_margin:.3f} dB / 個別 "
            f"{expected.actual_margin:.3f} dB）")
        assert actual.diff_loss == pytest.approx(expected.diff_loss, abs=1e-9)
        assert actual.blocked_ratio == pytest.approx(
            expected.blocked_ratio, abs=1e-9)
        # 🔑 **距離軸そのものを比べる**＝マージンが偶然一致しても、標本の位置が
        #    ずれていればここで落ちる（欠陥の所在に一番近い面）。
        np.testing.assert_allclose(
            np.asarray(run.terrain.d_km_axis),
            np.asarray(terrain_single.d_km_axis), atol=1e-12)


# ============================================================
# レポート（A4 シート・CSV）
# ============================================================
class TestScenarioReport:

    def _run(self, terrain, base, kind="sweep"):
        if kind == "sweep":
            values = [10.0, 30.0, 60.0]
            points = scn.evaluate(terrain, base, scn.sweep_conditions("h_tx", values))
            return scn.ScenarioRun(kind="sweep", base_params=base, terrain=terrain,
                                   points=points, axis="h_tx", axis_values=values)
        conds = [scn.Condition("A", {"freq_mhz": 900.0}),
                 scn.Condition("B", {"freq_mhz": 15000.0})]
        return scn.ScenarioRun(kind="compare", base_params=base, terrain=terrain,
                               points=scn.evaluate(terrain, base, conds))

    def test_sweep_sheet_has_chart_and_table(self, terrain, base):
        i18n.set_lang("ja")
        run = self._run(terrain, base, "sweep")
        html = report_scenario.scenario_sheet_html(
            run, chart_b64=report_scenario.render_sweep_png_b64(run))
        assert 'class="sheet scenario"' in html
        assert "data:image/png;base64," in html      # 折れ線
        assert html.count("<tr class='") >= 3        # 表（1 行 = 1 点）

    def test_compare_sheet_shows_deltas_in_cells(self, terrain, base):
        """差の在処と大きさはセル内の Δ が示す。"""
        i18n.set_lang("ja")
        html = report_scenario.scenario_sheet_html(self._run(terrain, base, "compare"))
        assert "class='delta'" in html, "Δ の併記が無い"

    def test_metric_rows_have_no_row_tint(self, terrain, base):
        """数値行に地の色を付けない（2026-07-26 撤去）。

        パラメータを 1 つ変えると損失の内訳が連鎖して動くため、行網掛けは
        **ほぼ全行が着色されて情報を持たなかった**（実測 11 行中 9 行）。
        2 条件時代（行が違う＝そのセルが違う）の名残で、N 条件では Δ の
        下位互換にしかならない。
        """
        i18n.set_lang("ja")
        html = report_scenario.scenario_sheet_html(self._run(terrain, base, "compare"))
        assert "class='diff'" not in html
        assert "tr.diff" not in report_scenario.scenario_sheet_css()

    def test_meta_block_shows_dem_fail_rate(self, terrain, base):
        """固定した地形の DEM 取得失敗率が経路情報に出ること（3.2 ステージ7・B-025 ③）。

        条件探索は地形を 1 回だけ取得して固定する（`run.terrain`）ので、
        `scenario.csv` の `dem_fail_pct` と同じ単一の値を 1 回だけ示す。
        """
        i18n.set_lang("en")
        run = self._run(terrain, base, "sweep")
        html = report_scenario.scenario_sheet_html(
            run, chart_b64=report_scenario.render_sweep_png_b64(run))
        assert i18n.t("pl_dem_fail") in html
        assert report_common.keep_unit_with_value(
            units.format_fail_pct(run.terrain.fail_pct)) in html

    def test_meta_block_keeps_label_value_and_unit_together(self, terrain, base):
        """「項目: 値 単位」の 1 組の中では折らないこと（B-251）。
        折れてよいのは組と組の区切りだけ。"""
        import re
        i18n.set_lang("ja")
        run = self._run(terrain, base, "compare")
        html = report_scenario.scenario_sheet_html(run)
        m = re.search(r'<div class="meta">(.*?)</div>', html)
        assert m is not None
        meta = m.group(1)
        kvs = re.findall(r'<span class="kv">(.*?)</span>', meta)
        assert len(kvs) == 4
        assert kvs[-1].startswith(i18n.t("pl_dem_fail") + ": ")
        # 塊の外に残るのは区切りだけ。
        assert re.sub(r'<span class="kv">.*?</span>', "", meta) == "　/　" * 3
        assert ".meta .kv{white-space:nowrap}" in report_scenario.scenario_sheet_css()

    def test_meta_block_keeps_each_value_with_its_unit(self, terrain, base):
        """バーは折り返してよいが、値と単位のあいだでは折らないこと（B-242）。

        3.4RC1 でバーが印字幅を 1px 超え、末尾の `0.0 %` の `%` だけが次の行へ
        落ちた。半角空白は HTML の折り返し可能点なので、値と単位の間は U+00A0。
        """
        i18n.set_lang("ja")
        run = self._run(terrain, base, "compare")
        meta = report_scenario._meta_block(run)
        dist = units.format_distance(run.terrain.horiz_dist_km)
        fail = units.format_fail_pct(run.terrain.fail_pct)
        assert dist not in meta and fail not in meta, "半角空白のままでは折れる"
        assert dist.replace(" ", " ") in meta
        assert fail.replace(" ", " ") in meta

    def test_compare_sheet_lists_the_changed_conditions(self, terrain, base):
        """表だけ見て再現できること（何を変えたかが載る）。"""
        i18n.set_lang("ja")
        html = report_scenario.scenario_sheet_html(self._run(terrain, base, "compare"))
        assert i18n.t("scn_axis_freq_mhz") in html

    def test_sheet_css_is_scoped(self):
        """連結・共存のため `.sheet.scenario` へスコープすること（2.5a2 の教訓）。"""
        import re
        css = report_scenario.scenario_sheet_css()
        body = re.sub(r"@media[^{]*\{", "", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
        sels = []
        for chunk in re.findall(r"([^{}]*)\{[^{}]*\}", body):
            if chunk.strip():
                sels.extend(s.strip() for s in chunk.strip().splitlines()[-1].split(","))
        unscoped = [s for s in sels if s and ".sheet.scenario" not in s]
        assert unscoped == [], f"未スコープのセレクタ: {unscoped}"

    def test_package_writes_html_and_csv(self, terrain, base, tmp_path):
        i18n.set_lang("ja")
        report_scenario.save_scenario_package(self._run(terrain, base, "sweep"),
                                              str(tmp_path))
        produced = set(os.listdir(str(tmp_path)))
        assert {"scenario.html", "scenario.csv"} <= produced

    def test_csv_has_one_row_per_point_and_raw_values(self, terrain, base, tmp_path):
        i18n.set_lang("ja")
        run = self._run(terrain, base, "sweep")
        report_scenario.save_scenario_csv(run, str(tmp_path))
        rows = (tmp_path / "scenario.csv").read_text(encoding="utf-8").splitlines()
        assert len(rows) == 1 + len(run.points)
        assert rows[0].startswith("label,axis_value,status")
        assert rows[0].endswith(",axis,dem_fail_pct,calc_profile,input_hash")
        # 桁区切りは入れない（表計算が数値として読めること）
        assert "," in rows[1] and '"' not in rows[1]

    def test_labels_are_html_escaped_in_both_tables(self, terrain, base):
        """ラベルの HTML エスケープを表ごとに非対称にしないこと。

        比較表のヘッダは escape 済みなのにスイープ表の 1 列目は生のままで、
        `<img … onerror=…>` がそのままレポートへ残った（2026-07-26 Codex
        レビュー指摘）。片方だけ直す形にしないため両方を 1 つのテストで縛る。
        """
        i18n.set_lang("ja")
        evil = '<img src=x onerror="alert(1)">'

        conds = [scn.Condition(evil, {"freq_mhz": 900.0})]
        compare = scn.ScenarioRun(kind="compare", base_params=base, terrain=terrain,
                                  points=scn.evaluate(terrain, base, conds))
        html = report_scenario.scenario_sheet_html(compare)
        assert evil not in html and "&lt;img" in html

        points = scn.evaluate(terrain, base, [scn.Condition(evil, {"h_tx": 30.0})])
        sweep = scn.ScenarioRun(kind="sweep", base_params=base, terrain=terrain,
                                points=points, axis="h_tx", axis_values=[30.0])
        html = report_scenario.scenario_sheet_html(sweep)
        assert evil not in html and "&lt;img" in html

    def test_csv_label_is_formula_safe(self, terrain, base, tmp_path):
        """B-012 のクラス点検＝CSV を吐くのはバッチだけではない（条件探索も吐く）。

        今のラベルは i18n 由来か数値なので実害は無いが、安全化を書き手ごとの
        判断にしない（→ report_common.csv_cell）。
        """
        i18n.set_lang("ja")
        conds = [scn.Condition('=HYPERLINK("http://x")', {"freq_mhz": 900.0})]
        run = scn.ScenarioRun(kind="compare", base_params=base, terrain=terrain,
                              points=scn.evaluate(terrain, base, conds))
        report_scenario.save_scenario_csv(run, str(tmp_path))
        body = (tmp_path / "scenario.csv").read_text(encoding="utf-8").splitlines()[1]
        assert body.startswith("\"'=HYPERLINK") or body.startswith("'=HYPERLINK")

    def test_csv_numeric_sweep_labels_stay_numeric(self, terrain, base, tmp_path):
        """スイープのラベルは値そのもの＝負値でもクォートしないこと。"""
        i18n.set_lang("ja")
        values = [-95.0, -90.0]
        points = scn.evaluate(terrain, base, scn.sweep_conditions("sens", values))
        run = scn.ScenarioRun(kind="sweep", base_params=base, terrain=terrain,
                              points=points, axis="sens", axis_values=values)
        report_scenario.save_scenario_csv(run, str(tmp_path))
        body = (tmp_path / "scenario.csv").read_text(encoding="utf-8").splitlines()[1]
        assert body.startswith("-95")

    def test_axis_label_carries_the_unit(self):
        """単位が付くこと。**括弧の字は言語ごとに違う**（2.8RC1）。

        ja は全角（`送信アンテナ高（m）`）・en は半角（`TX antenna height (m)`）で、
        括り方は i18n の `unit_wrap` が単一ソース。⇒ 字を直書きして照合しない。
        """
        i18n.set_lang("ja")
        assert report_scenario.axis_label("h_tx").endswith("（m）")
        assert report_scenario.axis_label("rain_rate").endswith("（mm/h）")
        i18n.set_lang("en")
        assert report_scenario.axis_label("h_tx").endswith(" (m)")
        i18n.set_lang("ja")

    def test_every_sweep_axis_has_a_label_and_unit_entry(self):
        """軸を足したとき i18n / 単位表の追随漏れを落とす。"""
        for axis in scn.SWEEP_AXES:
            assert axis in report_scenario.AXIS_UNITS, f"AXIS_UNITS に {axis} が無い"
            for lang in ("ja", "en"):
                assert f"scn_axis_{axis}" in i18n._STRINGS[lang], \
                    f"i18n[{lang}] に scn_axis_{axis} が無い"


# ============================================================
# GUI スモーク（表示があるときだけ・条件探索ウィンドウ）
# ============================================================
# ============================================================
# 結果表示と完了後の導線（2026-07-25 実機フィードバック）
# ============================================================
# ============================================================
# 実機フィードバック 第 2 弾（2026-07-25）のガード
# ============================================================
class TestBaseValuesAndLabels:
    """レポート単体で条件を再現できること・内部キーを見せないこと。"""

    @pytest.fixture(autouse=True)
    def _ja(self):
        prev = i18n._lang
        i18n.set_lang("ja")
        yield
        i18n.set_lang(prev)

    def _compare_run(self, terrain, base):
        conds = [
            scn.Condition("ベース", {}),
            scn.Condition("条件 1", {"freq_mhz": 5600.0, "env_type": "urban",
                                     "diff_method": "single"}),
        ]
        return scn.ScenarioRun(kind="compare", base_params=base, terrain=terrain,
                               points=scn.evaluate(terrain, base, conds))

    def test_base_column_shows_real_values_not_dashes(self, terrain, base):
        """ベース列は上書きを持たないが、**基準の実値**を出すこと。

        overrides から引くと全部「—」になり、表だけ見て条件を再現できない
        （実機で発覚）。
        """
        html = report_scenario.scenario_sheet_html(self._compare_run(terrain, base))
        row = [ln for ln in html.splitlines()
               if "cond" in ln and i18n.t("scn_axis_freq_mhz") in ln]
        assert row, "周波数の条件行が無い"
        assert f"{base.freq_mhz:g}" in row[0], f"ベースの実値が出ていない: {row[0]}"
        assert "—" not in row[0]

    def test_categorical_values_use_localized_labels(self, terrain, base):
        """環境・回折モデルは内部キー（los / bullington）でなく i18n ラベルで出す。"""
        html = report_scenario.scenario_sheet_html(self._compare_run(terrain, base))
        assert i18n.t("env_urban") in html
        assert i18n.t("html_model_single") in html
        for raw in (">urban<", ">single<", ">los<", ">bullington<"):
            assert raw not in html, f"内部キーが露出している: {raw}"


class TestSweepFitsOnOnePage:
    """点数を増やしても **1 列のまま** A4 1 枚に収める。

    スイープ表は「上から下へ連続的な変化を追う」のが価値なので、横に分割すると
    読み筋が切れる（2026-07-25 ユーザー判断で分割を撤回）。代わりに点数が多いときは
    行を詰める（dense）＋図を低くする。実測＝41 点で Edge print-to-pdf が 1 ページ、
    縮小フィットは未発動（文字サイズはそのまま）。
    """

    def _run(self, terrain, base, n):
        values = scn.linspace_values(10, 10 + n - 1, n)
        pts = scn.evaluate(terrain, base, scn.sweep_conditions("h_tx", values))
        return scn.ScenarioRun(kind="sweep", base_params=base, terrain=terrain,
                               points=pts, axis="h_tx", axis_values=values)

    @pytest.mark.parametrize("n", [2, 11, 21, 41])
    def test_table_is_always_a_single_column(self, terrain, base, n):
        html = report_scenario.scenario_sheet_html(self._run(terrain, base, n))
        assert html.count("<table class=") == 1, "表が分割されている（連続性が切れる）"
        assert "tables split" not in html

    def test_all_points_are_listed(self, terrain, base):
        run = self._run(terrain, base, scn.MAX_SWEEP_POINTS)
        html = report_scenario.scenario_sheet_html(run)
        for p in run.points:
            assert f"<td>{p.label}</td>" in html

    def test_dense_mode_kicks_in_only_when_needed(self, terrain, base):
        """行を詰めるのは収まらなくなる点数から（少ない点数は読みやすさ優先）。"""
        few = report_scenario.scenario_sheet_html(
            self._run(terrain, base, report_scenario._SWEEP_DENSE_ROWS))
        many = report_scenario.scenario_sheet_html(
            self._run(terrain, base, report_scenario._SWEEP_DENSE_ROWS + 1))
        assert 'class="scn"' in few and "dense" not in few
        assert 'class="scn dense"' in many
        # 断片に付くクラスへ対応するスタイルが CSS 側にあること（片方だけの改名を防ぐ）
        assert ".sheet.scenario table.scn.dense td{" in report_scenario.scenario_sheet_css()

    def test_chart_is_shorter_when_many_points(self, terrain, base):
        """図を低くして表に高さを譲る（1 枚に収めるための配分）。"""
        small = report_scenario.render_sweep_png_b64(self._run(terrain, base, 5))
        large = report_scenario.render_sweep_png_b64(
            self._run(terrain, base, scn.MAX_SWEEP_POINTS))
        import base64
        import io
        from PIL import Image
        h_small = Image.open(io.BytesIO(base64.b64decode(small))).height
        h_large = Image.open(io.BytesIO(base64.b64decode(large))).height
        assert h_large < h_small, f"点数が多いのに図が低くなっていない（{h_small} → {h_large}）"

    def test_no_first_ok_annotation(self, terrain, base):
        """「初めて OK」の明示は出さない（2026-07-25 ユーザー判断）。"""
        i18n.set_lang("ja")
        run = self._run(terrain, base, 11)
        html = report_scenario.scenario_sheet_html(
            run, chart_b64=report_scenario.render_sweep_png_b64(run))
        assert "◀" not in html
        assert "初めて" not in html


class TestSweepAxisSelection:
    """軸に並ぶのは**リンクバジェットに効く量だけ**。"""

    def test_k_factor_is_not_a_sweep_axis(self):
        """ライス K は計算に入らない（4.0 で表示の現在値も外した）＝振っても判定が動かない。

        効かない軸を並べると「効くはず」と誤読させる（2026-07-25 ユーザー指摘）。
        """
        assert "k_factor" not in scn.SWEEP_AXES
        # ⚠️ **自分で言語を決める**（2026-08-07）。ここは日本語の文面を assert して
        #    いるのに設定しておらず、**他のテストが漏らした `ja` に乗って**通って
        #    いた。conftest が言語を毎テスト戻すようにした途端に露見した
        #    （＝それまでは実行順しだいで赤くなり得た）。
        i18n.set_lang("ja")
        with pytest.raises(ValueError, match="スイープできない"):
            scn.sweep_conditions("k_factor", [1.0, 2.0])

    def test_k_factor_really_does_not_move_the_link_budget(self, terrain, base):
        """上の判断の根拠を数値で固定する（モデル側が変わったら気づけるように）。"""
        low  = scn.evaluate(terrain, base, [scn.Condition("l", {"k_factor": 1.0})])[0]
        high = scn.evaluate(terrain, base, [scn.Condition("h", {"k_factor": 20.0})])[0]
        assert low.result.p_rx == high.result.p_rx
        assert low.result.actual_margin == high.result.actual_margin
        assert low.result.status == high.result.status

    def test_every_axis_moves_something(self, terrain, base):
        """並んでいる軸はすべて受信レベルか判定を動かすこと。"""
        for axis in scn.SWEEP_AXES:
            lo, hi = {
                "freq_mhz": (400.0, 15000.0), "p_tx": (0.0, 40.0),
                "gain_tx": (0.0, 30.0), "gain_rx": (0.0, 30.0),
                "sens": (-120.0, -60.0), "h_tx": (2.0, 200.0),
                "h_rx": (2.0, 200.0), "veg_h": (0.0, 40.0),
                "rain_rate": (0.0, 150.0),
            }[axis]
            a = scn.evaluate(terrain, base, [scn.Condition("a", {axis: lo})])[0]
            b = scn.evaluate(terrain, base, [scn.Condition("b", {axis: hi})])[0]
            moved = (a.result.p_rx != b.result.p_rx
                     or a.result.actual_margin != b.result.actual_margin)
            assert moved, f"軸 {axis} は結果を動かさない（軸に並べる意味がない）"


class TestChangedParametersAreHighlighted:
    """ベースと違うパラメータの**セル**に印を付ける（2026-07-26 ユーザー要望）。

    条件が 3〜5 個あると「どの列のどの欄を変えたのか」が拾えない。数値行の
    行網掛けは列を特定できないうえ、ほぼ全行が着色されて情報を持たなかったので
    撤去した（2026-07-26）。入力の変更はセル単位で示す。
    """

    @pytest.fixture(autouse=True)
    def _ja(self):
        prev = i18n._lang
        i18n.set_lang("ja")
        yield
        i18n.set_lang(prev)

    def _run(self, terrain, base):
        conds = [
            scn.Condition("ベース", {}),
            scn.Condition("条件 1", {"freq_mhz": base.freq_mhz,        # 同値＝印なし
                                     "h_tx": base.h_tx + 40.0}),       # 変更＝印あり
            scn.Condition("条件 2", {"freq_mhz": 15000.0,
                                     "env_type": "urban"}),
        ]
        return scn.ScenarioRun(kind="compare", base_params=base, terrain=terrain,
                               points=scn.evaluate(terrain, base, conds))

    def _cond_row(self, html: str, key: str) -> str:
        label = i18n.t(f"scn_axis_{key}")
        rows = [ln for ln in html.splitlines() if "tr class='cond'" in ln and label in ln]
        assert rows, f"{key} の条件行が無い"
        return rows[0]

    def test_changed_cells_are_marked(self, terrain, base):
        html = report_scenario.scenario_sheet_html(self._run(terrain, base))
        assert self._cond_row(html, "h_tx").count("class='changed'") == 1
        assert self._cond_row(html, "env_type").count("class='changed'") == 1

    def test_cells_equal_to_base_are_not_marked(self, terrain, base):
        """値をコピーしただけの欄には印を付けない（変えた欄だけを目立たせる）。"""
        html = report_scenario.scenario_sheet_html(self._run(terrain, base))
        row = self._cond_row(html, "freq_mhz")
        # 条件 1 は同値・条件 2 のみ変更 → 印は 1 つ
        assert row.count("class='changed'") == 1

    def test_base_column_is_never_marked(self, terrain, base):
        """基準そのものに「違う」印は付かない（先頭列は常に素）。"""
        html = report_scenario.scenario_sheet_html(self._run(terrain, base))
        for key in ("h_tx", "freq_mhz", "env_type"):
            row = self._cond_row(html, key)
            first_cell = row.split("</td>")[1]      # 0=項目名, 1=ベース列
            assert "changed" not in first_cell, f"{key}: ベース列に印が付いている"

    def test_style_exists_for_the_marker(self):
        assert ".sheet.scenario tr.cond td.changed{" in \
            report_scenario.scenario_sheet_css()
