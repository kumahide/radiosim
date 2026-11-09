"""
tests/test_calc_profile.py
==========================
計算プロファイル ID と 2 つのハッシュ（4.0 ステージ3）のガード。

守ること＝①入力を足したのにハッシュへ足し忘れない ②旧プロジェクトは既定が変わっても v1 で
開き、保存し直しても v1 のまま ③成果物 CSV の全 4 本の末尾に同じ 2 列 ④帳票の全部の面に刻印
⑤v2 の枝が式・刻印・感度へ届き、製品の呼び出しが既定へ落ちない（ステージ4）。
"""

from __future__ import annotations

import ast
import csv
import json
import os

import numpy as np
import pytest

from core import calc_profile
from core import config
from core import disclosure
from core import i18n
from core import models
from core import output_contract
from core import scenario as scn
from core import sensitivity
from core import simulation as sim
from report import batch
from report import project


def _params(**over) -> sim.SimParams:
    return sim.SimParams({**config.DEFAULT_CONFIG, **over})


def _terrain(params: sim.SimParams, elev: float = 50.0) -> models.TerrainProfile:
    return models.calculate_terrain_profile(
        np.full(params.num, elev), params.lat_tx, params.lon_tx,
        params.lat_rx, params.lon_rx, frac_axis=params.sample_fracs)


def _result(params, terrain) -> models.LinkBudgetResult:
    return scn.evaluate(terrain, params, [scn.Condition("x")])[0].result


# ------------------------------------------------------------
# 入力設定のハッシュ
# ------------------------------------------------------------
def test_every_simparams_attribute_is_accounted_for():
    """🔒 `SimParams` に入力を足したら、ハッシュに入れるか理由を書くまで赤。"""
    attrs = set(vars(_params()))
    declared = set(calc_profile._INPUT_ATTRS) | set(calc_profile.NOT_HASHED)
    assert attrs == declared


@pytest.mark.parametrize("key, value", [
    ("start", "34.5, 132.4"), ("end", "34.6, 132.5"),
    ("h_tx", "31.0"), ("h_rx", "11.0"), ("freq", "920.0"), ("p_tx", "13.0"),
    ("gain_tx", "4.0"), ("gain_rx", "5.0"), ("sens", "-90.0"), ("veg_h", "5.0"),
    ("k_factor", "3.0"), ("resolution", "low"), ("env_type", "urban"),
    ("diff_method", "single"), ("rain_rate", "5.0"),
])
def test_each_input_moves_the_hash(key, value):
    base = _params()
    assert calc_profile.input_hash(_params(**{key: value})) != calc_profile.input_hash(base)


def test_same_input_gives_the_same_hash_and_the_label_is_out():
    """案件名のような計算に効かない値では動かない・接頭辞で表計算の数に化けない。"""
    first = calc_profile.input_hash(_params())
    assert first == calc_profile.input_hash(_params(project_name="別の案件"))
    assert first.startswith("i1-") and len(first) == 15


def test_profile_is_not_part_of_the_input_hash(monkeypatch):
    """🔑 同じ入力を別の方式で計算した行を突き合わせられること。"""
    monkeypatch.setattr(calc_profile, "PROFILES", ("v1", "v2"))
    assert (calc_profile.input_hash(_params(calc_profile="v1"))
            == calc_profile.input_hash(_params(calc_profile="v2")))


def test_unknown_dem_source_hashes_like_the_source_it_falls_back_to():
    assert (calc_profile.input_hash(_params(dem_source="消された宣言"))
            == calc_profile.input_hash(_params()))


def test_fixed_sample_count_is_an_input_but_the_resolved_count_is_not():
    def fixed(n):
        return _params(resolution="", samples=str(n))
    assert calc_profile.input_hash(fixed(100)) != calc_profile.input_hash(fixed(200))
    assert calc_profile.input_hash(fixed(100)) != calc_profile.input_hash(_params())


def test_overrides_equal_to_the_base_change_nothing():
    base = _params()
    assert (calc_profile.input_hash(base, {"freq_mhz": int(base.freq_mhz)})
            == calc_profile.input_hash(base))
    assert (calc_profile.input_hash(base, {"freq_mhz": 920.0})
            == calc_profile.input_hash(_params(freq="920.0")))


# ------------------------------------------------------------
# 地形のハッシュ
# ------------------------------------------------------------
def test_terrain_hash_sees_positions_elevations_and_holes():
    lats, lons, elevs = [34.0, 34.1], [132.0, 132.1], [10.0, 20.0]
    first = calc_profile.terrain_hash(lats, lons, elevs)
    assert first.startswith("t1-")
    assert first == calc_profile.terrain_hash(lats, lons, [10.001, 20.0])  # 丸めの内
    assert first != calc_profile.terrain_hash(lats, lons, [10.0, 20.1])
    assert first != calc_profile.terrain_hash([34.0, 34.2], lons, elevs)
    assert first != calc_profile.terrain_hash(lats, [132.0, 132.2], elevs)
    # 🔑 穴（取得の失敗）は 0 m と別物。
    holed = calc_profile.terrain_hash(lats, lons, [float("nan"), 20.0])
    assert holed != calc_profile.terrain_hash(lats, lons, [0.0, 20.0])


# ------------------------------------------------------------
# プロファイルの器
# ------------------------------------------------------------
def test_unknown_profile_is_refused_not_defaulted():
    with pytest.raises(ValueError):
        _params(calc_profile="v99")


def test_profile_flows_through_the_batch_row(monkeypatch):
    monkeypatch.setattr(calc_profile, "PROFILES", ("v1", "v2"))
    row = batch.PathRow("p", 34.5, 132.4, 34.6, 132.5, 10.0, 10.0)
    assert batch._make_params(row, _params(calc_profile="v2")).calc_profile == "v2"


def test_old_project_stays_on_v1_after_the_default_moves(tmp_path, monkeypatch):
    """🔑 判断点 ⑨＝既定が v2 へ動いても、プロファイルの無いファイルは v1 で開いて v1 で残る。"""
    monkeypatch.setattr(calc_profile, "PROFILES", ("v1", "v2"))
    monkeypatch.setattr(calc_profile, "DEFAULT_PROFILE", "v2")
    monkeypatch.setattr(project, "_DOC_FIELDS", tuple(
        project.Field(f.key, f.kind, f.default, ("v1", "v2"))
        if f.key == "calc_profile" else f for f in project._DOC_FIELDS))
    assert project.ProjectDoc().calc_profile == "v2"            # 新しいプロジェクトは既定

    doc = project.from_dict({"schema_version": 1, "meta": {}, "params": {}})
    assert doc.calc_profile == "v1"
    path = str(tmp_path / "old.rsproj")
    project.save(doc, path)
    assert project.load(path).calc_profile == "v1"

    # ⚠️ v1 だけで確かめると、書き忘れても読み手の既定で緑になる。
    project.save(project.ProjectDoc(calc_profile="v2"), path)
    assert project.load(path).calc_profile == "v2"


def test_switching_the_profile_re_solves_the_sample_positions():
    """🔑 方式だけ書き換えると標本の位置が古い方式のまま残る（v2 は双線形の置き方）。"""
    v1 = _params(calc_profile="v1", resolution="high")
    v2 = _params(calc_profile="v2", resolution="high")
    assert v1.sample_fracs != v2.sample_fracs        # 差が無ければこの試験は何も見ていない
    moved = sim.with_calc_profile(v1, "v2")
    assert moved.calc_profile == "v2"
    assert moved.sample_fracs == v2.sample_fracs
    assert moved.sample_pixel_groups == v2.sample_pixel_groups
    assert moved.num == v2.num
    assert v1.calc_profile == "v1"                    # 元は変わらない
    assert v1.sample_fracs == _params(calc_profile="v1", resolution="high").sample_fracs
    with pytest.raises(ValueError):
        sim.with_calc_profile(v1, "v99")


def test_switching_the_profile_keeps_a_fixed_sample_count():
    fixed = _params(calc_profile="v1", resolution="", samples="300")
    moved = sim.with_calc_profile(fixed, "v2")
    assert moved.num == 300 and moved.resolution == "" and moved.calc_profile == "v2"


def test_profile_is_not_a_sim_key():
    """⚠️ 設定へ残ると、開いたファイルの方式が次の起動の既定に化ける。"""
    assert "calc_profile" not in config.SIM_KEYS
    assert "calc_profile" not in project.to_dict(
        project.ProjectDoc(params={"calc_profile": "v1"}))["params"]


def test_unknown_profile_in_a_file_is_broken():
    with pytest.raises(project.ProjectError):
        project.from_dict({"schema_version": 2, "calc_profile": "v99"})


def test_new_files_are_refused_by_the_previous_reader():
    """🔴 版 1 の読み手は未知キーを捨てる＝版を上げて、保存で方式が消えるのを防ぐ。"""
    assert project.to_dict(project.ProjectDoc())["schema_version"] >= 2


def test_settings_json_records_the_profile_without_reading_it_back(tmp_path):
    params = _params()
    sim._save_settings(params, params.h_tx, params.h_rx, str(tmp_path))
    with open(tmp_path / "settings.json", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["calc_profile"] == calc_profile.DEFAULT_PROFILE
    assert "calc_profile" not in config.select_sim(saved)


# ------------------------------------------------------------
# 成果物 CSV
# ------------------------------------------------------------
@pytest.mark.parametrize("contract", output_contract.CSV_CONTRACTS, ids=lambda c: c.filename)
def test_every_csv_ends_with_the_two_columns(contract):
    assert contract.columns[-2:] == ("calc_profile", "input_hash")


def _rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_terrain_csv_carries_the_stamp(tmp_path):
    params = _params(resolution="low")
    sim._save_terrain_csv(_terrain(params), str(tmp_path), params)
    rows = _rows(tmp_path / "terrain_profile.csv")
    assert {r["calc_profile"] for r in rows} == {calc_profile.DEFAULT_PROFILE}
    assert {r["input_hash"] for r in rows} == {calc_profile.input_hash(params)}


def _path_result(row_id="p", **over):
    params = _params(resolution="low", **over)
    terrain = _terrain(params)
    row = batch.PathRow(row_id, params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx,
                        params.h_tx, params.h_rx)
    return batch.PathResult(row=row, result=_result(params, terrain),
                            params=params, terrain=terrain)


def _scenario_run() -> scn.ScenarioRun:
    base = _params(resolution="low")
    terrain = _terrain(base)
    conditions = [scn.Condition(label="基準", overrides={}),
                  scn.Condition(label="高い", overrides={"freq_mhz": 920.0})]
    points = scn.evaluate(terrain, base, conditions)
    return scn.ScenarioRun(kind="compare", base_params=base, terrain=terrain, points=points)


# ------------------------------------------------------------
# 帳票の刻印
# ------------------------------------------------------------
def test_provenance_line_names_one_value_or_the_combined_value():
    single = disclosure.provenance_line(["v1"], ["i1-a"], ["t1-b"])
    assert single == (f'{i18n.t("html_calc_profile")}: v1 / '
                      f'{i18n.t("html_input_hash")}: i1-a / '
                      f'{i18n.t("html_terrain_hash")}: t1-b')
    several = disclosure.provenance_line(["v1", "v1"], ["i1-a", "i1-b"], [])
    assert calc_profile.combined_hash(["i1-a", "i1-b"]) in several
    assert i18n.t("html_terrain_hash") not in several, "地形が無ければ欄を出さない"


def test_combined_hash_keeps_the_order():
    assert calc_profile.combined_hash(["a", "b"]) != calc_profile.combined_hash(["b", "a"])


def test_mixed_profiles_are_not_hidden_behind_one():
    assert "v1, v2" in disclosure.provenance_line(["v2", "v1"], ["i1-a"], [])


def test_report_txt_carries_the_stamp(tmp_path):
    good = _path_result()
    sim._save_report(good.result, good.params, good.params.h_tx, good.params.h_rx,
                     str(tmp_path), terrain=good.terrain)
    text = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert calc_profile.input_hash(good.params) in text
    assert sim.terrain_hash(good.params, good.terrain) in text


# ------------------------------------------------------------
# v2 の枝（B-262・ステージ4）
# ------------------------------------------------------------
def test_v2_folds_the_1ghz_cliff_and_v1_keeps_it():
    """🔒 v2 は 2 MHz 動かして跳ばない。v1 の崖は旧プロジェクトの再現のために残す。"""
    assert models._veg_coeff(1.001, "v2") / models._veg_coeff(0.999, "v2") == pytest.approx(1.0, abs=0.01)
    assert models._veg_coeff(1.001, "v1") / models._veg_coeff(0.999, "v1") == pytest.approx(1.669, abs=0.01)


def test_the_922mhz_coefficient_moves_to_the_inner_branch():
    """台帳の数字＝v1 0.11522・v2 0.18895（1.64 倍）。"""
    assert models._veg_coeff(0.922, "v1") == pytest.approx(0.11522, abs=1e-5)
    assert models._veg_coeff(0.922, "v2") == pytest.approx(0.18895, abs=1e-5)


@pytest.mark.parametrize("profile", ["v1", "v2"])
def test_the_6ghz_jump_is_not_folded(profile):
    """🔒 上側は畳まない＝縁の向こう側の実測点が無い（B-262 の処方）。"""
    assert models._veg_coeff(6.001, profile) / models._veg_coeff(5.999, profile) == pytest.approx(2.505, abs=0.01)


@pytest.mark.parametrize("freq_mhz, profile, extrapolated, discontinuous", [
    (430.0,  "v1", True,  False),
    (2400.0, "v1", False, False),
    (6000.0, "v1", True,  False),
    (430.0,  "v2", False, False),
    (2400.0, "v2", False, False),
    (5999.0, "v2", False, False),
    (6000.0, "v2", False, True),
])
def test_the_vegetation_note_follows_the_profile(freq_mhz, profile, extrapolated, discontinuous):
    keys = models.scope_notes(freq_mhz, veg_h=5.0, profile=profile)
    assert ("veg_extrapolated" in keys) is extrapolated
    assert ("veg_discontinuous" in keys) is discontinuous


def test_no_v2_note_without_vegetation():
    assert "veg_discontinuous" not in models.scope_notes(6000.0, veg_h=0.0, profile="v2")


def _vegetated(profile: str) -> sim.SimParams:
    """植生が LoS に入り、上限 45 dB に張り付かない短い回線（922 MHz）。"""
    return _params(calc_profile=profile, start="35.0, 139.0", end="35.002, 139.0",
                   freq="922", veg_h="20", h_tx="5", h_rx="5")


def test_the_profile_reaches_the_calculation_and_the_sensitivity():
    """🔒 方式が計算と感度の両方へ届く＝帳票の余裕度と感度の基準が同じ方式になる。"""
    margins = {}
    for profile in calc_profile.PROFILES:
        params = _vegetated(profile)
        terrain = _terrain(params)
        margin = sim.run_calculation(terrain, params.h_tx, params.h_rx, params).actual_margin
        sens = sensitivity.compute_link_sensitivity(
            terrain, params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx,
            params.h_tx, params.h_rx, params.freq_mhz, params.veg_h, params.k_factor,
            params.diff_method, params.env_type, params.rain_rate,
            params.p_tx, params.gain_tx, params.gain_rx, params.sens,
            profile=profile,
        )
        assert sens.axes["veg_h"].baseline == pytest.approx(margin)
        margins[profile] = margin
    assert margins["v2"] < margins["v1"] - 1.0


def test_every_product_call_passes_the_profile():
    """🔒 既定へ落ちると、開いたプロジェクトと違う方式で黙って計算する（既定は試験と探針のため）。"""
    root = os.path.join(os.path.dirname(__file__), "..")
    # 呼ぶ口 → 方式を運ぶ引数の名前（B-339＝地形の読みと標本の置き方は `bilinear`）
    names = {"calculate_propagation": "profile", "scope_notes": "profile",
             "compute_link_sensitivity": "profile", "resolve_samples": "profile",
             "path_sample_fractions": "bilinear", "sample_pixel_groups": "bilinear",
             "get_elevation": "bilinear"}
    found, missing = 0, []
    for folder in ("core", "report", "webui"):
        for base, _dirs, files in os.walk(os.path.join(root, folder)):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(base, name)
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read())
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    if called in names:
                        found += 1
                        if not any(k.arg == names[called] for k in node.keywords):
                            missing.append(f"{name}:{node.lineno}")
    # 帳票の呼び口（5 か所）は Pro へ移した（Link 1.0 ステージ11b）＝Pro 側の同名の試験が数える
    assert found >= 11
    assert missing == []
