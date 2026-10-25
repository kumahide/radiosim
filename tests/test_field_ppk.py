"""後処理の探針（`experiments/field_ppk.py`）の Multi-base PPK（実験機能・⑩）の純粋な関数の検査。

RTKLIB を回す所（`solve_base`）は外から呼ぶ exe と観測が要る＝ここでは見ない（実データの確かめは
探針の docstring の ⑩）。見るのは局の事前選択・統合・警告・回の数え方。
"""
from __future__ import annotations

import importlib.util
import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest


def _ppk():
    path = Path(__file__).resolve().parents[1] / "experiments" / "field_ppk.py"
    spec = importlib.util.spec_from_file_location("field_ppk", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = _ppk()


def _cand(code, dist, soft=(), fatal=(), name=""):
    return {"code": code, "name": name, "dist_km": dist, "dh": 0.0, "coverage": 1.0, "overlap": 100,
            "antenna": "atx", "soft": list(soft), "fatal": list(fatal)}


def _res(code, h, q=1):
    return {"code": code, "name": "", "q": q, "sol": (35.0, 140.0, h), "konki": (35.0, 140.0, h + 0.007)}


# --- 事前選択 -----------------------------------------------------------------------------


def test_auto_selection_takes_the_nearest_stations_without_a_reason_to_drop():
    cands = [_cand("A", 20.0), _cand("B", 6.8, soft=["高低差 +333 m"]), _cand("C", 8.1),
             _cand("D", 30.0), _cand("E", 1.0, fatal=["観測が無い"]), _cand("F", 25.0)]
    picked, ranked = P.select_bases(cands, n=3)
    assert [c["code"] for c in picked] == ["C", "A", "F"]
    # 順位＝解ける → 外す理由無し → 近い順（外した局も理由つきで並べる）
    assert [c["code"] for c in ranked] == ["C", "A", "F", "D", "B", "E"]


def test_auto_selection_returns_fewer_when_the_candidates_run_out():
    picked, _ = P.select_bases([_cand("A", 20.0), _cand("B", 6.8, soft=["高低差"])], n=3)
    assert [c["code"] for c in picked] == ["A"]


def test_manual_selection_keeps_the_given_order_and_overrides_soft_reasons():
    cands = [_cand("3036", 8.1, name="富津"), _cand("3030", 20.1), _cand("3045", 6.8, soft=["高低差"])]
    picked, _ = P.select_bases(cands, manual=["3045", "富津", "3045"])
    assert [c["code"] for c in picked] == ["3045", "3036"]     # 名前でも選べる・重なりは 1 回


def test_manual_selection_stops_on_an_unknown_or_unsolvable_station():
    cands = [_cand("3036", 8.1), _cand("9999", 3.0, fatal=["観測が無い"])]
    with pytest.raises(ValueError, match="bases にありません"):
        P.select_bases(cands, manual=["3030"])
    with pytest.raises(ValueError, match="観測が無い"):
        P.select_bases(cands, manual=["9999"])


# --- 統合と警告 ---------------------------------------------------------------------------


def test_combine_averages_fixed_stations_and_reports_the_spread():
    # 3857 の 1 本目（富津・市原1・鹿野山）の楕円体高
    comb = P.combine_results([_res("3036", 49.3545), _res("3030", 49.4461), _res("3045", 49.5292)])
    assert comb["used"] == ["3036", "3030", "3045"]
    assert comb["mean"]["sol"][2] == pytest.approx((49.3545 + 49.4461 + 49.5292) / 3)
    assert comb["median"]["sol"][2] == pytest.approx(49.4461)
    assert comb["median"]["konki"][2] == pytest.approx(49.4531)
    assert comb["adopted"] is comb["mean"]
    assert comb["spread"] == pytest.approx(0.1747)
    assert len(comb["warnings"]) == 1 and "精度を保証しない" in comb["warnings"][0]


def test_combine_can_adopt_the_median():
    comb = P.combine_results([_res("A", 1.0), _res("B", 1.02), _res("C", 1.09)], method="median")
    assert comb["adopted"] is comb["median"]
    assert comb["adopted"]["sol"][2] == pytest.approx(1.02)
    with pytest.raises(ValueError):
        P.combine_results([_res("A", 1.0)], method="mode")


def test_the_spread_warning_follows_the_threshold():
    quiet = P.combine_results([_res("A", 1.0), _res("B", 1.09)], spread_warn=0.10)
    assert quiet["warnings"] == []
    loud = P.combine_results([_res("A", 1.0), _res("B", 1.09)], spread_warn=0.05)
    assert any("0.090 m" in w for w in loud["warnings"])


def test_a_station_whose_last_epoch_is_float_is_left_out_of_the_combination():
    """`solve` は最後の回を Q に関係なく採る＝multi ではその回を統合に入れない。"""
    comb = P.combine_results([_res("A", 1.0), _res("B", 1.5, q=2), _res("C", 1.04)])
    assert comb["used"] == ["A", "C"] and comb["dropped"] == ["B"]
    assert comb["mean"]["sol"][2] == pytest.approx(1.02)
    assert comb["spread"] == pytest.approx(0.04)                # Float の局は最大差にも入れない
    assert len(comb["warnings"]) == 1 and "Q=2" in comb["warnings"][0]


def test_fewer_than_two_fixed_stations_is_warned():
    one = P.combine_results([_res("A", 1.0), _res("B", 1.2, q=2)])
    assert one["spread"] is None and one["mean"]["sol"][2] == pytest.approx(1.0)
    assert any("Fix の局が 1 局" in w for w in one["warnings"])
    none = P.combine_results([_res("A", 1.0, q=2)])
    assert none["adopted"] is None and any("Fix の局が 0 局" in w for w in none["warnings"])


# --- データ品質（窓の覆い・アンテナ） -----------------------------------------------------


def _epochs(start, n, step):
    return [start + timedelta(seconds=step * i) for i in range(n)]


def test_coverage_counts_base_epochs_inside_the_rover_window():
    t0 = datetime(2026, 10, 24, 22, 53, 6)
    rover = _epochs(t0, 3600, 1.0)                              # 1 Hz・約 60 分
    full = _epochs(t0 - timedelta(minutes=5), 140, 30.0)
    cov, n = P.coverage(rover, full)
    assert n == 120 and cov == pytest.approx(1.0)
    half = _epochs(t0 + timedelta(minutes=30), 60, 30.0)
    cov, n = P.coverage(rover, half)
    assert cov == pytest.approx(0.5, abs=0.02) and cov < P.MULTI_MIN_COVERAGE
    assert P.coverage(rover, _epochs(t0 + timedelta(hours=2), 50, 30.0)) == (0.0, 0)
    assert P.coverage([], full) == (0.0, 0)


def test_antenna_status_matches_pick_antenna():
    known = {"TRM59800.80     NONE", "JCA228F0001     NONE"}
    gsi = ("", {"TRM59800.80     GSI6": {}, "TRM59800.80     NONE": {}})
    assert P.antenna_status("JCA228F0001     NONE", known) == "atx"
    assert P.antenna_status("TRM59800.80     GSI6", known, gsi) == "gsi"
    assert P.antenna_status("TRM59800.80     GSI6", known) == "none"
    assert P.antenna_status("", known) == "unknown"
    assert P.antenna_status("XYZ             GSI6", known) == "missing"


def test_obs_epochs_reads_epoch_lines_and_skips_event_records(tmp_path):
    f = tmp_path / "base.26o"
    f.write_text(
        "     4.01           OBSERVATION DATA    M: Mixed            RINEX VERSION / TYPE\n"
        "> 1999 header-like text inside the header must not count  COMMENT\n"
        "                                                            END OF HEADER\n"
        "> 2026 10 24 22 49 30.0000000  0 30       0.000000000000\n"
        "G01  20000000.000\n"
        ">                              4  1\n"
        "inserted header line                                        COMMENT\n"
        "> 2026 10 24 22 50  0.0000000  0 29       0.000000000000\n",
        encoding="ascii")
    assert P.obs_epochs(f) == [datetime(2026, 10, 24, 22, 49, 30), datetime(2026, 10, 24, 22, 50, 0)]


def test_ecef_to_llh_inverts_the_geodetic_formula():
    lat, lon, h = 35.306023, 139.913907, 49.35
    n = P.radii(lat)[1]
    cl, sl = math.cos(math.radians(lat)), math.sin(math.radians(lat))
    x = (n + h) * cl * math.cos(math.radians(lon))
    y = (n + h) * cl * math.sin(math.radians(lon))
    z = (n * (1 - P.E2) + h) * sl
    got = P.ecef_to_llh(x, y, z)
    assert got[0] == pytest.approx(lat, abs=1e-9) and got[1] == pytest.approx(lon, abs=1e-9)
    assert got[2] == pytest.approx(h, abs=1e-4)
