"""
tests/test_report_sensitivity.py
=================================
ロードマップ 3.4 段6（帳票の面）の回帰テスト。

- `report_common.sensitivity_table_html` / `residuals_table_html`（体裁だけを
  持つ純関数）の単体テスト。
- 各帳票（per-path / summary / scenario / multihop）への配線が、期待どおりの
  条件でだけ現れる／消えることの統合テスト。

⚠️ **モデルは 1 行も変えていない**（3.4 の決定）＝ここで見るのは HTML の
体裁と、地面反射の disclosure ⇔ 感度表の切り替え条件だけ。数値そのものの
裏取りは `tests/test_sensitivity.py` / `tests/test_ground_reflection.py` /
`tests/test_residuals.py` / `tests/test_report_residuals.py` が担う。
"""
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import i18n
from core import models
from core import residuals as core_residuals
from core import scenario as scn
from core import sensitivity as sv
from core import simulation as sim
from report import batch
from report import multihop as mh
from report import report_common
from report import report_multihop
from report import report_path
from report import report_scenario
from report import report_summary

_DATA = os.path.join(os.path.dirname(__file__), "data", "golden_links.json")
with open(_DATA, encoding="utf-8") as _f:
    CORPUS = json.load(_f)
LINKS = {link["id"]: link for link in CORPUS["links"]}


def _params(inp: dict) -> sim.SimParams:
    return sim.SimParams({
        "start": f"{inp['lat_tx']}, {inp['lon_tx']}",
        "end":   f"{inp['lat_rx']}, {inp['lon_rx']}",
        "h_tx": str(inp["h_tx"]), "h_rx": str(inp["h_rx"]),
        "freq": str(inp["freq_mhz"]), "p_tx": str(inp["p_tx"]),
        "gain_tx": str(inp["gain_tx"]), "gain_rx": str(inp["gain_rx"]),
        "sens": str(inp["sens"]), "veg_h": str(inp["veg_h"]),
        "k_factor": str(inp["k_factor"]),
        **({"resolution": inp["resolution"]} if inp.get("resolution")
           else {"samples": str(inp["samples"])}),
        "env_type": inp["env_type"], "diff_method": inp["diff_method"],
        "rain_rate": str(inp["rain_rate"]),
    })


def _terrain(link: dict, params: sim.SimParams) -> models.TerrainProfile:
    return models.calculate_terrain_profile(
        raw_elevs=np.array(link["raw_elevs"], dtype=float),
        lat_tx=params.lat_tx, lon_tx=params.lon_tx,
        lat_rx=params.lat_rx, lon_rx=params.lon_rx,
        frac_axis=params.sample_fracs,
    )


def _hop_result(link_id: str, path_id: str) -> batch.PathResult:
    """golden_links.json の 1 本を `batch.PathResult`（成功）へ仕立てる。"""
    link = LINKS[link_id]
    params = _params(link["input"])
    terrain = _terrain(link, params)
    result = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
    row = batch.PathRow(
        path_id=path_id,
        lat_tx=params.lat_tx, lon_tx=params.lon_tx,
        lat_rx=params.lat_rx, lon_rx=params.lon_rx,
        h_tx=params.h_tx, h_rx=params.h_rx,
    )
    return batch.PathResult(row=row, result=result, terrain=terrain, params=params)


# ============================================================
# report_common.sensitivity_table_html / residuals_table_html（純関数）
# ============================================================
class TestSensitivityTableHtml:
    def setup_method(self):
        i18n.set_lang("en")

    def test_none_returns_empty_string(self):
        assert report_common.sensitivity_table_html(None) == ""

    def test_axes_are_rendered_as_rows(self):
        sens = sv.SensitivityResult(
            baseline_margin=1.0,
            axes={
                "dem_elev": sv.AxisRange("dem_elev", 1.0, -2.0, 3.0),
                "diff_method": sv.AxisRange("diff_method", 1.0, 0.0, 1.0),
                "diff_veg_compose": sv.AxisRange("diff_veg_compose", 1.0, 1.0, 4.0),
            },
            ground_reflection=None,
            resolution=None,
        )
        html = report_common.sensitivity_table_html(sens)
        assert 'class="sensitivity"' in html
        assert i18n.t("html_sensitivity_title") in html
        # DEM の摂動量が文言へ差し込まれている（値の直書きではない＝単一ソース）。
        assert f"{sv.DEM_PERTURB_M:g}" in html
        assert html.count("<tr>") == 4  # 見出し行 + 3 軸
        assert i18n.t("html_sens_axis_ground_reflection") not in html

    def test_ground_reflection_row_uses_envelope(self):
        env = sv.ground_reflection.TwoRayEnvelope(
            frac=0.5, sigma_h_m=1.0, grazing_deg=2.0, roughness="smooth",
            null_depth_db=12.0, constructive_gain_db=6.0,
        )
        sens = sv.SensitivityResult(
            baseline_margin=5.0, axes={}, ground_reflection=env, resolution=None,
        )
        html = report_common.sensitivity_table_html(sens)
        assert i18n.t("html_sens_axis_ground_reflection") in html
        # low = baseline - null_depth, high = baseline + constructive_gain
        assert "-7.0" in html   # 5.0 - 12.0
        assert "+11.0" in html  # 5.0 + 6.0

    def test_no_rows_returns_empty_string(self):
        sens = sv.SensitivityResult(
            baseline_margin=0.0, axes={}, ground_reflection=None, resolution=None,
        )
        assert report_common.sensitivity_table_html(sens) == ""

    def test_note_is_appended(self):
        sens = sv.SensitivityResult(
            baseline_margin=1.0,
            axes={"dem_elev": sv.AxisRange("dem_elev", 1.0, 0.0, 2.0)},
            ground_reflection=None, resolution=None,
        )
        html = report_common.sensitivity_table_html(sens, note="hello note")
        assert "hello note" in html


class TestResidualsTableHtml:
    def setup_method(self):
        i18n.set_lang("en")

    def test_empty_list_returns_empty_string(self):
        assert report_common.residuals_table_html([]) == ""

    def test_stats_are_rendered_as_rows(self):
        stats = [
            core_residuals.LayerStats("urban", "<1GHz", "<5km", 3, 2.5, 1.0),
            core_residuals.LayerStats("rural", "1-6GHz", "5-20km", 2, -1.0, 0.5),
        ]
        html = report_common.residuals_table_html(stats)
        assert 'class="residuals"' in html
        assert i18n.t("html_residuals_title") in html
        assert html.count("<tr>") == len(stats) + 1  # +1 は見出し行


# ============================================================
# report_path.py：地面反射の disclosure ⇔ 感度表の切り替え（段6の核心）
# ============================================================
class TestPathSheetGroundReflectionSwap:
    def setup_method(self):
        i18n.set_lang("en")

    def _sheet_html(self, link_id: str) -> str:
        link = LINKS[link_id]
        params = _params(link["input"])
        terrain = _terrain(link, params)
        result = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
        return report_path.path_sheet_html(
            terrain, result, params, params.h_tx, params.h_rx, img_b64="",
        )

    def test_envelope_present_replaces_the_disclosure_line(self):
        """反射点が両端から離れている回線＝幅が出るので「考慮していない」を外す。"""
        html = self._sheet_html("tokyo_urban_2400")
        assert i18n.t("html_scope_ground_reflection") not in html
        assert i18n.t("html_sens_axis_ground_reflection") in html

    def test_envelope_absent_keeps_the_disclosure_line(self):
        """反射点が端に寄る回線＝幅にできないので「考慮していない」を残す。"""
        html = self._sheet_html("hiroshima_short_grazing")
        assert i18n.t("html_scope_ground_reflection") in html
        assert i18n.t("html_sens_axis_ground_reflection") not in html

    def test_other_scope_notes_are_unaffected(self):
        """地面反射以外の刻印は従来どおり（フィルタが他の刻印まで巻き込まない）。"""
        html = self._sheet_html("tokyo_urban_2400")
        assert i18n.t("html_scope_dem_surface") in html
        assert i18n.t("html_scope_env_empirical") in html


# ============================================================
# report_scenario.py：ベース条件だけの感度表（注記つき）
# ============================================================
def test_scenario_sheet_shows_base_only_sensitivity_note():
    i18n.set_lang("en")
    link = LINKS["tokyo_urban_2400"]
    base_params = _params(link["input"])
    terrain = _terrain(link, base_params)
    result = sim.run_calculation(terrain, base_params.h_tx, base_params.h_rx, base_params)
    point = scn.ScenarioPoint(
        label="base", h_tx=base_params.h_tx, h_rx=base_params.h_rx,
        result=result, overrides={},
    )
    run = scn.ScenarioRun(
        kind="compare", base_params=base_params, terrain=terrain, points=[point],
    )
    html = report_scenario.scenario_sheet_html(run)
    assert i18n.t("html_sensitivity_title") in html
    assert i18n.t("html_sens_base_only") in html
    # 条件探索は N 条件の和集合なので、地面反射の disclosure はそのまま残る。
    assert i18n.t("html_scope_ground_reflection") in html


# ============================================================
# report_summary.py：残差の層別表は標本が1件でもあるときだけ
# ============================================================
def test_summary_sheet_has_no_residuals_section_without_measurements():
    i18n.set_lang("en")
    pr = _hop_result("tokyo_urban_2400", "p01")
    html = report_summary.summary_sheet_html([pr])
    assert 'class="residuals"' not in html


def test_summary_sheet_shows_residuals_section_with_measurements():
    i18n.set_lang("en")
    pr = _hop_result("tokyo_urban_2400", "p01")
    pr.row.meas_dbm = pr.result.p_rx - 3.0     # 適当な実測値（残差3dB相当）
    html = report_summary.summary_sheet_html([pr])
    assert 'class="residuals"' in html
    assert i18n.t("html_residuals_title") in html


# ============================================================
# report_multihop.py：律速区間の感度表＋argmin 注記
# ============================================================
def _mh_path(n: int) -> mh.MultiHopPath:
    pts = [mh.Waypoint(name=f"P{i}", lat=34.5 + i * 0.01, lon=132.4, h=10.0)
           for i in range(n)]
    return mh.MultiHopPath(path_id="route1", waypoints=pts,
                           hop_rf=[mh.HopRF() for _ in range(n - 1)])


class TestMultihopSensitivity:
    def setup_method(self):
        i18n.set_lang("en")

    def test_absent_when_a_hop_has_failed(self):
        """1 区間でも計算できていなければ、律速区間の入れ替わりは判定しない。"""
        ok_hop = _hop_result("tokyo_urban_2400", "route1_h1")
        failed_hop = batch.PathResult(
            row=ok_hop.row, result=None, error=RuntimeError("boom"),
        )
        run = mh.MultiHopRun(path=_mh_path(3), hops=[ok_hop, failed_hop])
        assert report_multihop._multihop_sensitivity_html(run, run.worst) == ""

    def test_present_when_all_hops_succeed(self):
        h1 = _hop_result("tokyo_urban_2400", "route1_h1")
        h2 = _hop_result("hiroshima_kure_ridge", "route1_h2")
        run = mh.MultiHopRun(path=_mh_path(3), hops=[h1, h2])
        html = report_multihop._multihop_sensitivity_html(run, run.worst)
        assert i18n.t("html_sensitivity_title") in html
        assert i18n.t("html_sens_worst_hop").format(
            hop=mh.hop_label(run.path, run.hops.index(run.worst))
        ).split(".")[0] in html


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
