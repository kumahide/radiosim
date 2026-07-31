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
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
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
        ("env_type", "forest"), ("diff_method", "bullington"),
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
        """**テーマとフォントを適用してから**窓を作る。

        ⚠️ 素の Tk 既定フォントのまま測ると、実機（sv_ttk の本文フォント）より
        文字が小さく、寸法のゲートが**実物より狭い前提で緑になる**。実際、条件 5
        列で必要幅 947px のところ、テーマ無しでは 900px 未満に収まってしまい、
        幅を固定したままの実装（＝見切れる実装）でも通ってしまった。
        """
        from conftest import make_themed_root
        root = make_themed_root()
        root.withdraw()
        from views.scenario import ScenarioWindow
        win = ScenarioWindow(root, sim.SimParams(default_params_dict))
        return root, win

    def test_builds_both_modes(self, default_params_dict):
        i18n.set_lang("ja")
        root, win = self._win(default_params_dict)
        try:
            assert win._mode.get() == "compare"
            # スイープの軸は**行のラジオ**で選ぶ（I-032 でタブを比較タブの器へ
            # 寄せた）。既定は従来と同じ h_tx。
            assert win._sweep_axis.get() == "h_tx"
            # 画面の項目名は**単位つき**（出所は report_scenario.AXIS_UNITS ＝
            # レポートと同じ一覧。画面だけ単位が無いと何を入れる欄か分からない）。
            assert report_scenario.axis_label("h_tx") == i18n.t("scn_axis_h_tx") + " (m)"
        finally:
            win.destroy(); root.destroy()

    # ⚠️ 窓の見切れ（条件列を足すと右端が出る＝I-024）のゲートは
    # tests/test_window_fit.py へ移した（全窓横断の登録制ゲート）。

    def _win_with_meta(self, default_params_dict, meta: dict):
        """案件情報プロバイダ付きの窓（プロバイダの中身は後から差し替えられる）。"""
        from conftest import make_themed_root
        root = make_themed_root()
        root.withdraw()
        from views.scenario import ScenarioWindow
        win = ScenarioWindow(root, sim.SimParams(default_params_dict),
                             meta_provider=lambda: meta)
        return root, win

    def test_case_info_is_snapshotted_and_shown(self, default_params_dict):
        """案件情報を開いた時点で凍結し、画面にも出すこと。

        2026-07-26 のユーザー指摘＝バッチには出るのに条件探索には出ない。
        レポートの自己同定ヘッダには刻印されるので、**画面に無い値が成果物に
        載る**状態だった。
        """
        i18n.set_lang("ja")
        meta = {"project_name": "〇〇高校 無線化検討", "memo": "2 系統比較"}
        root, win = self._win_with_meta(default_params_dict, meta)
        try:
            assert win._meta == meta
            # I-031 で帯を「バッチと同じ readonly 欄＋🔒」へ揃えたので、
            # 表示の実体は 1 行テキストではなく欄の値。
            assert win._project_var.get() == "〇〇高校 無線化検討"
            assert win._memo_var.get() == "2 系統比較"
        finally:
            win.destroy(); root.destroy()

    def test_case_info_does_not_follow_the_launcher_silently(self, default_params_dict):
        """ランチャー側を変えても、↻ を押すまで写しは変わらないこと。

        経路（座標）と**同じ凍結方式**に揃える。実行の瞬間に読み直すと、窓を
        開いたあとに案件名を変えた場合「画面と成果物が食い違う」。
        """
        i18n.set_lang("ja")
        meta = {"project_name": "案件 A", "memo": ""}
        root, win = self._win_with_meta(default_params_dict, meta)
        try:
            meta["project_name"] = "案件 B"          # ランチャー側で変更した相当
            assert win._meta["project_name"] == "案件 A", "黙って追従している"
            assert win._project_var.get() == "案件 A"
            win._refresh_from_launcher()             # ↻ で明示的に取り込む
            assert win._meta["project_name"] == "案件 B"
            assert win._project_var.get() == "案件 B"
        finally:
            win.destroy(); root.destroy()

    def test_run_uses_the_snapshot_not_a_fresh_read(self, default_params_dict,
                                                    monkeypatch, tmp_path,
                                                    dialog_calls):
        """実行が使うのは**画面に出ている写し**であること（成果物の刻印）。

        ⚠️ このテストは完了まで走るので、**完了ダイアログ**（「保存しました。
        開きますか？」）に到達する。`dialog_calls` フィクスチャ（autouse）が
        塞いでいなければ、`wait_window()` で**人がボタンを押すまで止まる**
        （2026-07-26 に実際に止めた）。ここではダイアログが出たことも確かめる。
        """
        import config as cfg_mod
        import report_scenario

        i18n.set_lang("ja")
        meta = {"project_name": "案件 A", "memo": "メモ A"}
        root, win = self._win_with_meta(default_params_dict, meta)
        seen: dict = {}
        try:
            monkeypatch.setattr(cfg_mod, "RESULTS_DIR", str(tmp_path))
            monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)
            monkeypatch.setattr(sim, "_terrain_cache", {})
            monkeypatch.setattr(
                report_scenario, "save_scenario_package",
                lambda run, d, project, memo: seen.update(project=project, memo=memo))
            meta.update(project_name="案件 B", memo="メモ B")   # 実行の直前に変更
            win._on_run()
            deadline = time.time() + 30
            while not seen and time.time() < deadline:
                root.update()
                time.sleep(0.02)
            assert seen == {"project": "案件 A", "memo": "メモ A"}, (
                f"実行時にランチャーを読み直している: {seen}")
            # 完了ダイアログまで到達している＝遮断が効いていることの裏取り。
            root.update()
            # 完了ダイアログは「レポートを開く / 保存先を開く」の選択になった（I-030）。
            assert "choose" in dialog_calls.kinds()
        finally:
            win.destroy(); root.destroy()

    def test_input_widths_are_aligned_in_both_tabs(self, default_params_dict):
        """同じ列に縦積みした入力欄の**実幅**が揃うこと（比較・スイープとも）。

        `ttk.Entry(width=N)` と `ttk.Combobox(width=N)` は同じ文字数を指定しても
        実幅が一致しない（Combobox は矢印ボタンぶん広い）。文字数で合わせにいく
        限り、フォント・テーマ・言語が変わるたびにずれる。幅は grid の列に決め
        させ（`sticky="ew"`）、ここでは**結果**＝実際の割り当て幅を検査する。

        2026-07-25 の実機フィードバックで比較タブ（I-021）、2026-07-26 で
        スイープタブが指摘された＝**同じ欠陥が 2 つのタブに別々に存在した**ので、
        ゲートも両方をまとめて見る。
        """
        i18n.set_lang("ja")
        root, win = self._win(default_params_dict)
        try:
            # --- スイープ：開始 / 終了 / 点数 の Entry ---
            # ⚠️ **軸の Combobox は I-032 で消えた**（軸は行のラジオで選ぶ）＝
            # 「Combobox と Entry で実幅が揃わない」という元の欠陥は構造ごと無くなり、
            # 残るのは同じ種類・同じ幅の Entry 3 つが揃っているかだけ。ここを消さずに
            # 残すのは、選択行へ置き直す実装（`_on_sweep_axis_changed`）が列を
            # ずらして幅を壊し得るため。
            win._mode.set("sweep"); win._on_mode_changed()
            root.update_idletasks()
            widths = {ent.winfo_width() for _lab, ent in win._range_cells}
            assert len(widths) == 1, f"スイープの入力欄の幅が揃っていない: {widths}"

            # --- 比較：条件列の Entry と Combobox ---
            win._mode.set("compare"); win._on_mode_changed()
            root.update_idletasks()
            col = 2                      # 0=項目名 / 1=ベース / 2=条件 1
            cmp_widths = {w.winfo_width()
                          for w in win._cmp_grid.grid_slaves(column=col)
                          if w.winfo_class() in ("TEntry", "TCombobox")}
            assert len(cmp_widths) == 1, f"比較の入力欄の幅が揃っていない: {cmp_widths}"
        finally:
            win.destroy(); root.destroy()

    def test_only_the_active_panel_is_shown(self, default_params_dict):
        """ttk.Notebook を使わない理由＝行数の多い比較側に引きずられて
        スイープ側に死んだ余白ができる（2026-07-25 実機フィードバック）。
        表示中のパネルだけ pack し、空いた高さは結果一覧が使う。"""
        i18n.set_lang("ja")
        root, win = self._win(default_params_dict)
        try:
            root.update_idletasks()
            # pack されているのは表示中のパネルだけ（未表示は manager 無し）
            assert win._compare_panel.winfo_manager() == "pack"
            assert win._sweep_panel.winfo_manager() == ""
            win._mode.set("sweep"); win._on_mode_changed()
            root.update_idletasks()
            assert win._sweep_panel.winfo_manager() == "pack"
            assert win._compare_panel.winfo_manager() == ""
        finally:
            win.destroy(); root.destroy()

    def test_compare_starts_with_base_and_one_condition(self, default_params_dict):
        """既定はベース＋比較条件 1 列。ベースは編集させない（基準を動かさない）。"""
        i18n.set_lang("ja")
        root, win = self._win(default_params_dict)
        try:
            base = sim.SimParams(default_params_dict)
            assert len(win._cmp_cols) == 1
            assert win._cmp_cols[0]["freq_mhz"].get() == str(base.freq_mhz)
            conds = win._compare_conditions()
            assert len(conds) == 2                      # ベース＋条件1
            assert conds[0].overrides == {}, "ベースは上書きを持たない"
            assert conds[1].overrides["freq_mhz"] == base.freq_mhz
        finally:
            win.destroy(); root.destroy()

    def test_conditions_can_be_added_up_to_the_limit(self, default_params_dict):
        """ベース 1 個に対し比較対象を 3〜5 個並べられること（2026-07-25 要望）。"""
        i18n.set_lang("ja")
        root, win = self._win(default_params_dict)
        try:
            for _ in range(scn.MAX_COMPARE_CONDITIONS + 2):   # 上限超えを押しても増えない
                win._add_condition_column()
            assert len(win._cmp_cols) == scn.MAX_COMPARE_CONDITIONS
            assert str(win._add_btn["state"]) == "disabled"
            assert len(win._compare_conditions()) == scn.MAX_COMPARE_CONDITIONS + 1
            for _ in range(scn.MAX_COMPARE_CONDITIONS + 2):   # 0 列にはならない
                win._remove_condition_column()
            assert len(win._cmp_cols) == 1
            assert str(win._del_btn["state"]) == "disabled"
        finally:
            win.destroy(); root.destroy()

    def test_added_condition_columns_are_independent(self, default_params_dict):
        i18n.set_lang("ja")
        root, win = self._win(default_params_dict)
        try:
            win._add_condition_column()
            win._cmp_cols[0]["freq_mhz"].set("900")
            win._cmp_cols[1]["freq_mhz"].set("15000")
            conds = win._compare_conditions()
            assert conds[1].overrides["freq_mhz"] == 900.0
            assert conds[2].overrides["freq_mhz"] == 15000.0
        finally:
            win.destroy(); root.destroy()

    def test_sweep_inputs_produce_conditions(self, default_params_dict):
        i18n.set_lang("ja")
        root, win = self._win(default_params_dict)
        try:
            win._mode.set("sweep"); win._on_mode_changed()
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


# ============================================================
# 結果表示と完了後の導線（2026-07-25 実機フィードバック）
# ============================================================
class TestScenarioResultsAndDialog:
    """窓の高さを点数から切り離し、完了時の挙動を単一/バッチと揃える。

    実機フィードバックの内容:
      - スイープ点数が多いと縦長にしないと表示できない（FHD に収まらない）
      - 保存ボタンが窓から見切れる
      - 保存後にダイアログが出ない＝単一/バッチと挙動が違う
    """

    @pytest.fixture(autouse=True)
    def _restore_lang(self):
        prev = i18n._lang
        yield
        i18n.set_lang(prev)

    def _win(self, default_params_dict):
        from conftest import make_tk_root
        root = make_tk_root()
        root.withdraw()
        i18n.set_lang("ja")
        from views.scenario import ScenarioWindow
        return root, ScenarioWindow(root, sim.SimParams(default_params_dict))

    def _run(self, terrain, base, points):
        values = scn.linspace_values(10, 10 + points - 1, points)
        pts = scn.evaluate(terrain, base, scn.sweep_conditions("h_tx", values))
        return scn.ScenarioRun(kind="sweep", base_params=base, terrain=terrain,
                               points=pts, axis="h_tx", axis_values=values)

    def test_window_height_is_independent_of_point_count(
            self, default_params_dict, terrain, base, monkeypatch):
        """41 点でも窓の要求高は変わらない（一覧はスクロールする）。"""
        root, win = self._win(default_params_dict)
        try:
            import views.scenario as vs
            monkeypatch.setattr(vs.dialogs, "confirm", lambda *a, **k: False)
            win._last_dir = "dummy"
            root.update_idletasks()
            win._on_complete(self._run(terrain, base, 3))
            root.update_idletasks()
            small = win.winfo_reqheight()
            win._on_complete(self._run(terrain, base, scn.MAX_SWEEP_POINTS))
            root.update_idletasks()
            large = win.winfo_reqheight()
            assert large == small, (
                f"点数で窓の高さが変わる（{small}px → {large}px）＝"
                "一覧がスクロールせず伸びている"
            )
            # 全点は一覧に載る（スクロールで届く）
            assert len(win._tree.get_children()) == scn.MAX_SWEEP_POINTS
        finally:
            win.destroy(); root.destroy()

    def test_run_button_is_outside_the_scrolling_area(self, default_params_dict):
        """実行ボタンは常に見える帯に置く（一覧が伸びても見切れない）。"""
        root, win = self._win(default_params_dict)
        try:
            root.update_idletasks()
            assert win._run_btn.winfo_parent() != str(win._result_box)
        finally:
            win.destroy(); root.destroy()

    def test_no_standalone_open_button(self, default_params_dict):
        """レポートを開く導線は完了ダイアログ 1 本（バッチと対称・2026-07-25 決定）。

        条件探索にだけ常設ボタンがあるのは非対称で、「常設ボタンを増やさず
        完了時に選ばせる」というバッチ側の決定とも食い違っていた。
        """
        root, win = self._win(default_params_dict)
        try:
            assert not hasattr(win, "_open_btn")
        finally:
            win.destroy(); root.destroy()

    def test_progress_bar_resets_on_completion(
            self, default_params_dict, terrain, base, monkeypatch):
        """完了時にバーを 0 へ戻す（単一・バッチと挙動を揃える）。"""
        root, win = self._win(default_params_dict)
        try:
            import views.scenario as vs
            monkeypatch.setattr(vs.dialogs, "confirm", lambda *a, **k: False)
            win._last_dir = "d"
            win._on_complete(self._run(terrain, base, 3))
            assert win._prog_bar["value"] == 0
            assert win._prog_label["text"] == ""
        finally:
            win.destroy(); root.destroy()

    def test_completion_asks_before_opening_like_single_and_batch(
            self, default_params_dict, terrain, base, monkeypatch):
        root, win = self._win(default_params_dict)
        try:
            import views.scenario as vs
            asked, opened = [], []
            monkeypatch.setattr(
                vs.dialogs, "choose",
                lambda *a, **k: (asked.append(a[2]), "report")[1])
            monkeypatch.setattr(vs.os, "startfile", lambda p: opened.append(str(p)),
                                raising=False)
            win._last_dir = str(tmp := "some_dir")
            win._on_complete(self._run(terrain, base, 3))
            assert asked, "完了時に保存先を告げるダイアログが出ていない"
            assert tmp in asked[0]
            assert opened and opened[0].endswith("scenario.html")
        finally:
            win.destroy(); root.destroy()

    def test_declining_the_dialog_opens_nothing(
            self, default_params_dict, terrain, base, monkeypatch):
        root, win = self._win(default_params_dict)
        try:
            import views.scenario as vs
            opened = []
            monkeypatch.setattr(vs.dialogs, "confirm", lambda *a, **k: False)
            monkeypatch.setattr(vs.os, "startfile", lambda p: opened.append(str(p)),
                                raising=False)
            win._last_dir = "some_dir"
            win._on_complete(self._run(terrain, base, 3))
            assert opened == []
        finally:
            win.destroy(); root.destroy()


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
        """環境・回折モデルは内部キー（los / deygout）でなく i18n ラベルで出す。"""
        html = report_scenario.scenario_sheet_html(self._compare_run(terrain, base))
        assert i18n.t("env_urban") in html
        assert i18n.t("html_model_single") in html
        for raw in (">urban<", ">single<", ">los<", ">deygout<"):
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


class TestLauncherSnapshot:
    """ランチャー（source of truth）の取り込みは**明示的**に行う。

    以前は実行時に config_provider を黙って読み直しており、ランチャーで座標を
    変えると「画面に出ている経路」と「実際に計算した経路」が食い違い得た
    （表示 ≠ 計算＝気づけない種類の欠陥）。バッチの Common Settings と同じく
    開窓時スナップショット＋↻ ボタンに揃える。
    """

    @pytest.fixture(autouse=True)
    def _restore_lang(self):
        prev = i18n._lang
        yield
        i18n.set_lang(prev)

    def _win(self, cfg, provider):
        from conftest import make_tk_root
        root = make_tk_root()
        root.withdraw()
        i18n.set_lang("ja")
        from views.scenario import ScenarioWindow
        return root, ScenarioWindow(root, sim.SimParams(cfg),
                                    config_provider=provider)

    def test_refresh_pulls_coordinates_and_params(self, default_params_dict):
        cfg = dict(default_params_dict)
        live = dict(cfg)
        root, win = self._win(cfg, lambda: dict(live))
        try:
            live["start"] = "35.10000, 139.20000"
            live["freq"]  = "15000"
            win._refresh_from_launcher()
            assert win._base_params.lat_tx == pytest.approx(35.1)
            assert win._base_params.freq_mhz == 15000.0
            assert "35.10000" in win._path_var.get(), "経路表示が追従していない"
            assert win._base_vars["freq_mhz"].get() == "15000.0"
            # 触っていない条件欄はベースに追従する
            assert win._cmp_cols[0]["freq_mhz"].get() == "15000.0"
        finally:
            win.destroy(); root.destroy()

    def test_refresh_keeps_edited_condition_fields(self, default_params_dict):
        cfg = dict(default_params_dict)
        live = dict(cfg)
        root, win = self._win(cfg, lambda: dict(live))
        try:
            win._cmp_cols[0]["freq_mhz"].set("2400")     # ユーザーが編集した欄
            live["freq"] = "15000"
            win._refresh_from_launcher()
            assert win._cmp_cols[0]["freq_mhz"].get() == "2400", \
                "編集した条件が ↻ で巻き戻された"
        finally:
            win.destroy(); root.destroy()

    def test_run_uses_the_displayed_snapshot_not_a_silent_reread(
            self, default_params_dict, monkeypatch):
        """実行は画面に出ている値で行う（↻ を押すまでランチャーの変更は入らない）。"""
        cfg = dict(default_params_dict)
        live = dict(cfg)
        root, win = self._win(cfg, lambda: dict(live))
        try:
            import views.scenario as vs
            seen = {}
            monkeypatch.setattr(vs.scn, "run_scenario",
                                lambda base, conds, **k: seen.update(base=base))
            monkeypatch.setattr(vs.os, "makedirs", lambda *a, **k: None)
            live["start"] = "35.10000, 139.20000"        # ↻ を押さずに変更
            win._on_run()
            assert seen["base"].lat_tx == pytest.approx(
                sim.SimParams(cfg).lat_tx), "画面と違う座標で計算している"
        finally:
            win.destroy(); root.destroy()

    def test_categorical_fields_show_localized_labels_in_the_window(
            self, default_params_dict):
        root, win = self._win(dict(default_params_dict), None)
        try:
            base = sim.SimParams(default_params_dict)
            shown = win._cmp_cols[0]["env_type"].get()
            assert shown == i18n.t(f"env_{base.env_type}")
            assert shown != base.env_type, "内部キーがそのまま出ている"
            # 表示ラベルは内部キーへ戻して条件になる
            conds = win._compare_conditions()
            assert conds[1].overrides["env_type"] == base.env_type
        finally:
            win.destroy(); root.destroy()


class TestSweepAxisSelection:
    """軸に並ぶのは**リンクバジェットに効く量だけ**。"""

    def test_k_factor_is_not_a_sweep_axis(self):
        """ライス K は表示上の current_k にしか入らない＝振っても判定が動かない。

        効かない軸を並べると「効くはず」と誤読させる（2026-07-25 ユーザー指摘）。
        """
        assert "k_factor" not in scn.SWEEP_AXES
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
