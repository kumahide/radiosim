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


