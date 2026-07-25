"""
tests/test_scenario.py
======================
条件探索（2.5 / A-1 比較・A-2 スイープ）のヘッドレス検証。

対象は [scenario.py](scenario.py)（共有ランナー・相の宣言）と
[report_scenario.py](report_scenario.py)（A4 シート・CSV）。DEM 取得は
monkeypatch で塞ぎ、ネットワーク無しで実行する。

**ここで守っているもの**:
  - 「DEM 取得 1 回 + run_calculation を N 回」＝2.5 の成立条件そのもの。
    取得が条件数ぶん走ったら、それは条件探索ではなくバッチの再実装になっている。
  - 相（phase）の宣言＝**重い相が進捗の管轄外に置かれない**（B-006／I-008 の
    構造対策）。レポート生成まで含めて 100% に到達すること。
  - 上書きがベース params を汚さないこと（A-1/A-2 は同じ params を N 回使う）。
"""

import os
import sys
import threading

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import i18n
import models
import report_scenario
import scenario as scn
import simulation as sim


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
        with pytest.raises(ValueError, match="上書きできない"):
            scn.Condition(label="A", overrides={"samples": 100})

    def test_rejects_coordinate_override(self):
        """座標は固定＝経路自体の比較はバッチの仕事（2026-07-25 決定）。"""
        with pytest.raises(ValueError):
            scn.Condition(label="A", overrides={"lat_tx": 34.5})

    def test_accepts_all_overridable_keys(self, base):
        cond = scn.Condition(label="A", overrides={k: getattr(base, k)
                                                   for k in scn.OVERRIDABLE})
        assert set(cond.overrides) == set(scn.OVERRIDABLE)


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


# ============================================================
# スイープ軸
# ============================================================
class TestSweepConditions:

    def test_rejects_categorical_axis(self):
        """env_type / diff_method は離散＝軸にせず比較（A-1）で扱う。"""
        with pytest.raises(ValueError, match="スイープできない"):
            scn.sweep_conditions("env_type", [1.0, 2.0])

    def test_rejects_single_point(self):
        with pytest.raises(ValueError):
            scn.sweep_conditions("h_tx", [10.0])

    def test_rejects_too_many_points(self):
        values = [float(i) for i in range(scn.MAX_SWEEP_POINTS + 1)]
        with pytest.raises(ValueError, match="点数"):
            scn.sweep_conditions("h_tx", values)

    def test_labels_are_the_axis_values(self):
        conds = scn.sweep_conditions("h_tx", [10.0, 20.5])
        assert [c.label for c in conds] == ["10", "20.5"]
        assert conds[1].overrides == {"h_tx": 20.5}

    def test_linspace_values_are_inclusive(self):
        assert scn.linspace_values(10, 50, 5) == [10.0, 20.0, 30.0, 40.0, 50.0]


# ============================================================
# 相（phase）の宣言
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

        def _spy_fetch(params, on_progress, on_complete, on_error):
            fetches.append(1)
            return real_fetch(params, on_progress, on_complete, on_error)

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
        """レポート生成まで進捗率に乗ること（重い相を管轄外にしない）。"""
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


class TestScenarioRun:

    def test_first_ok_index_finds_the_threshold(self, terrain, base):
        conds = scn.sweep_conditions("h_tx", [1.0, 2.0, 200.0, 300.0])
        points = scn.evaluate(terrain, base, conds)
        run = scn.ScenarioRun(kind="sweep", base_params=base, terrain=terrain,
                              points=points, axis="h_tx",
                              axis_values=[1.0, 2.0, 200.0, 300.0])
        idx = run.first_ok_index()
        assert idx >= 0
        assert all(not p.ok for p in points[:idx])
        assert points[idx].ok

    def test_first_ok_index_is_minus_one_when_all_ng(self, terrain, base):
        points = scn.evaluate(terrain, base,
                              [scn.Condition("x", {"sens": -10.0})])
        run = scn.ScenarioRun(kind="compare", base_params=base, terrain=terrain,
                              points=points)
        assert run.first_ok_index() == -1


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

    def test_compare_sheet_marks_rows_that_differ(self, terrain, base):
        i18n.set_lang("ja")
        html = report_scenario.scenario_sheet_html(self._run(terrain, base, "compare"))
        assert "class='diff'" in html, "差の出た行が強調されていない"
        assert "class='delta'" in html, "2 条件のとき Δ 列が無い"

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
        assert rows[0].startswith("label,h_tx,status")
        # 桁区切りは入れない（表計算が数値として読めること）
        assert "," in rows[1] and '"' not in rows[1]

    def test_axis_label_carries_the_unit(self):
        i18n.set_lang("ja")
        assert report_scenario.axis_label("h_tx").endswith("(m)")
        assert report_scenario.axis_label("rain_rate").endswith("(mm/h)")

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
class TestScenarioWindowSmoke:
    """窓が組み上がること＝i18n キー欠落・レイアウト例外の即検出。

    実行はしない（DEM 取得と GUI ループを回さない）。ヘッドレス CI では
    make_tk_root が skip する。
    """

    @pytest.fixture(autouse=True)
    def _restore_lang(self):
        """言語を戻す（i18n はプロセス共有＝戻さないと後続テストを汚す）。"""
        prev = i18n._lang
        yield
        i18n.set_lang(prev)

    def _win(self, default_params_dict):
        from conftest import make_tk_root
        root = make_tk_root()
        root.withdraw()
        from views.scenario import ScenarioWindow
        win = ScenarioWindow(root, sim.SimParams(default_params_dict))
        return root, win

    def test_builds_both_tabs(self, default_params_dict):
        i18n.set_lang("ja")
        root, win = self._win(default_params_dict)
        try:
            assert len(win._tabs.tabs()) == 2
            assert win._axis_box.get() == i18n.t("scn_axis_h_tx")
        finally:
            win.destroy(); root.destroy()

    def test_compare_fields_start_from_launcher_values(self, default_params_dict):
        i18n.set_lang("ja")
        root, win = self._win(default_params_dict)
        try:
            base = sim.SimParams(default_params_dict)
            va, vb = win._cmp_vars["freq_mhz"]
            assert va.get() == str(base.freq_mhz) == vb.get()
            conds = win._compare_conditions()
            assert len(conds) == 2
            assert conds[0].overrides["freq_mhz"] == base.freq_mhz
        finally:
            win.destroy(); root.destroy()

    def test_sweep_inputs_produce_conditions(self, default_params_dict):
        i18n.set_lang("ja")
        root, win = self._win(default_params_dict)
        try:
            win._from_var.set("10"); win._to_var.set("50"); win._points_var.set("5")
            conds, axis, values = win._sweep_conditions()
            assert axis == "h_tx"
            assert values == [10.0, 20.0, 30.0, 40.0, 50.0]
            assert [c.label for c in conds] == ["10", "20", "30", "40", "50"]
        finally:
            win.destroy(); root.destroy()

    def test_equal_from_to_is_rejected(self, default_params_dict):
        i18n.set_lang("ja")
        root, win = self._win(default_params_dict)
        try:
            win._from_var.set("30"); win._to_var.set("30")
            with pytest.raises(ValueError):
                win._sweep_conditions()
        finally:
            win.destroy(); root.destroy()
