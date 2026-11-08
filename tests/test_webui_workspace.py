"""
tests/test_webui_workspace.py
=============================
新しい画面の**作業面の入力**（Link 1.0 ステージ10a）＝`webui/sheets.py` と
`state.AppState` の面・面の入力・`/api/inputs` の口。
"""

from __future__ import annotations

import http.client
import json
import os
import pathlib
import subprocess
import sys
import threading

import pytest

from core import scenario as scn
from core.batch_csv_schema import CSV_COLUMNS
from webui import server as webserver
from webui import sheets
from webui.sheets import SheetError
from webui.state import AppState, UnknownKeyError
from webui.worker import Worker

_ROW = {"id": "A1", "start": "34.40, 132.45", "end": "34.42, 132.50",
        "h_tx": "10", "h_rx": "5"}
_WAYPOINTS = [{"name": "", "coord": "34.40, 132.45", "h": "10"},
              {"name": "中継", "coord": "34°25'12\"N, 132°28'48\"E", "h": "15"},
              {"name": "終点", "coord": "34.42, 132.50", "h": "5"}]


# ============================================================
# 入れ物（state）
# ============================================================
def test_the_workspace_starts_on_the_single_link_with_empty_sheets():
    st = AppState()
    version, ws, sh = st.workspace_snapshot()
    assert ws == "single" == sheets.WORKSPACES[0]
    assert sh == sheets.empty()
    assert set(sh) == set(sheets.SHEETS)


def test_switching_the_workspace_bumps_the_version_once():
    st = AppState()
    v = st.update({}, workspace="batch")
    assert v == 2 and st.workspace_snapshot()[1] == "batch"
    assert st.update({}, workspace="batch") == 2                 # 同じ面＝版は動かない


def test_one_update_bumps_once_whatever_it_changes():
    st = AppState()
    v = st.update({"h_tx": "35.0"}, workspace="batch", sheets={"batch": [_ROW]})
    assert v == 2
    _, ws, sh = st.workspace_snapshot()
    assert ws == "batch"
    assert sh["batch"][0]["id"] == "A1"
    assert set(sh["batch"][0]) == set(CSV_COLUMNS)                # 欠けた列は空でそろう
    assert sh["batch"][0]["freq"] == ""


def test_the_same_sheet_does_not_bump_the_version():
    st = AppState()
    st.update({}, sheets={"batch": [_ROW]})
    assert st.update({}, sheets={"batch": [dict(_ROW)]}) == 2


@pytest.mark.parametrize("kwargs", [
    {"workspace": "map"},
    {"sheets": {"single": {}}},
    {"sheets": {"batch": [{"id": 1}]}},
    {"sheets": {"batch": [{"lat": "1"}]}},
    {"sheets": {"batch": {"id": "A"}}},
    {"sheets": {"scenario": {"mode": "grid"}}},
    {"sheets": {"scenario": {"compare": [{"start": "1"}]}}},
    {"sheets": {"scenario": {"compare": [{}] * (scn.MAX_COMPARE_CONDITIONS + 1)}}},
    {"sheets": {"scenario": {"sweep": {"step": "1"}}}},
    {"sheets": {"multihop": {"waypoints": [{"coord": 1}]}}},
    {"sheets": {"multihop": {"waypoints": [{}] * (sheets.MAX_WAYPOINTS + 1)}}},
    {"sheets": {"multihop": {"route": "x"}}},
    {"sheets": []},
])
def test_a_bad_shape_is_rejected_and_nothing_changes(kwargs):
    st = AppState()
    before = st.workspace_snapshot()
    with pytest.raises(SheetError):
        st.update({"h_tx": "35.0"}, **kwargs)
    assert st.workspace_snapshot() == before
    assert st.snapshot()[1]["h_tx"] != "35.0"                    # 欄も変えない＝全部を見てから反映


def test_an_unknown_input_key_rejects_the_sheets_too():
    st = AppState()
    with pytest.raises(UnknownKeyError):
        st.update({"proxy_url": "x"}, sheets={"batch": [_ROW]})
    assert st.workspace_snapshot()[2]["batch"] == []


def test_the_snapshot_is_a_copy():
    st = AppState()
    st.update({}, sheets={"batch": [_ROW]})
    _, _, sh = st.workspace_snapshot()
    sh["batch"][0]["id"] = "Z"
    assert st.workspace_snapshot()[2]["batch"][0]["id"] == "A1"


def test_wait_carries_the_workspace_and_the_sheets():
    st = AppState()
    st.update({}, workspace="multihop")
    snap = st.wait(0, 0.01)
    assert snap["workspace"] == "multihop"
    assert snap["sheets"] == sheets.empty()


def test_the_limits_match_the_typed_side():
    from report import multihop as mh
    from report import project
    assert sheets.MODES == project.SCENARIO_MODES
    assert sheets.MAX_WAYPOINTS == mh.MAX_HOPS + 1
    assert sheets.empty()["scenario"]["sweep"]["axis"] in scn.SWEEP_AXES


# ============================================================
# 型への変換
# ============================================================
def test_batch_rows_become_path_rows_numbered_from_one():
    rows = sheets.batch_rows(sheets.check("batch", [_ROW]))
    assert rows[0].path_id == "A1" and rows[0].lat_rx == pytest.approx(34.42)
    assert rows[0].freq_mhz is None                              # 空欄＝共通設定を踏襲
    with pytest.raises(ValueError, match="2"):
        sheets.batch_rows(sheets.check("batch", [_ROW, {**_ROW, "h_tx": "abc"}]))


def test_compare_starts_with_the_base_and_skips_empty_fields():
    spec = sheets.check("scenario", {"compare": [{"freq_mhz": "2400", "env_type": "urban"},
                                                 {}]})
    conds, axis, values = sheets.conditions(spec)
    assert axis == "" and values == []
    assert [c.overrides for c in conds] == [{}, {"freq_mhz": 2400.0, "env_type": "urban"}, {}]


def test_compare_rejects_a_word_in_a_number_field_by_name():
    spec = sheets.check("scenario", {"compare": [{"p_tx": "abc"}]})
    with pytest.raises(ValueError, match="abc"):
        sheets.conditions(spec)


def test_compare_values_are_range_checked_by_the_condition():
    spec = sheets.check("scenario", {"compare": [{"freq_mhz": "0"}]})
    with pytest.raises(ValueError):
        sheets.conditions(spec)


def test_sweep_spreads_the_values_on_the_axis():
    spec = sheets.check("scenario", {"mode": "sweep", "sweep": {
        "axis": "h_tx", "from": "10", "to": "30", "points": "3"}})
    conds, axis, values = sheets.conditions(spec)
    assert axis == "h_tx" and values == [10.0, 20.0, 30.0]
    assert [c.overrides for c in conds] == [{"h_tx": v} for v in values]


@pytest.mark.parametrize("sweep", [
    {"axis": "h_tx", "from": "10", "to": "10", "points": "3"},
    {"axis": "h_tx", "from": "10", "to": "30", "points": "2.5"},
    {"axis": "h_tx", "from": "10", "to": "30", "points": "nan"},
    {"axis": "h_tx", "from": "10", "to": "30", "points": "1"},
    {"axis": "env_type", "from": "10", "to": "30", "points": "3"},
    {"axis": "h_tx", "from": "", "to": "30", "points": "3"},
])
def test_a_bad_sweep_is_a_message_not_a_crash(sweep):
    spec = sheets.check("scenario", {"mode": "sweep", "sweep": sweep})
    with pytest.raises(ValueError) as e:
        sheets.conditions(spec)
    assert "could not convert" not in str(e.value)


def test_the_relay_path_reads_dms_and_names_the_blank_point():
    sheet = sheets.check("multihop", {"path_id": " R1 ", "waypoints": _WAYPOINTS,
                                      "hop_rf": [{"freq": "920"}, {}]})
    path = sheets.multihop_path(sheet)
    assert path.path_id == "R1"
    assert [w.name for w in path.waypoints] == ["P1", "中継", "終点"]
    assert path.waypoints[1].lat == pytest.approx(34.42)
    assert path.hop_rf[0].freq_mhz == 920.0 and path.hop_rf[1].freq_mhz is None
    from report import multihop as mh
    assert mh.validate_path(path) == []


def test_a_bad_relay_point_names_its_number():
    bad = [*_WAYPOINTS[:1], {"name": "x", "coord": "34.4", "h": "10"}]
    with pytest.raises(ValueError, match="2"):
        sheets.multihop_path(sheets.check("multihop", {"waypoints": bad}))


def test_importing_the_sheets_does_not_load_the_report_side():
    # I-232＝`report.*` は matplotlib を引く＝型へ直すときまで読まない
    root = pathlib.Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root)}
    code = ("import sys, webui.state, webui.sheets, webui.server;"
            "print(' '.join(k for k in ('matplotlib', 'report') if k in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=root, capture_output=True,
                         text=True, timeout=60, check=True).stdout.split()
    assert out == []


# ============================================================
# HTTP
# ============================================================
class _Result:
    status = "OK"
    verdict = None
    actual_margin = p_rx = eirp = fspl = diff_loss = veg_loss = 0.0
    env_loss = rain_loss = gas_loss = total_loss = 0.0


@pytest.fixture
def web():
    st = AppState()
    w = Worker(st, fetch=lambda p, cb, cancel: "terrain", key=lambda p: None,
               calc=lambda t, p: _Result(), profile=lambda t, p: None)
    srv = webserver.WebServer(w, tile=lambda s, z, x, y: ("absent", None))
    th = threading.Thread(target=srv.httpd.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.httpd.shutdown()
    srv.httpd.server_close()


def _post(srv, body):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    data = json.dumps(body).encode("utf-8")
    conn.request("POST", "/api/inputs", body=data, headers={
        "Host": f"127.0.0.1:{srv.port}", "X-Token": srv.token,
        "Content-Type": "application/json"})
    resp = conn.getresponse()
    out = (resp.status, json.loads(resp.read() or b"{}"))
    conn.close()
    return out


def test_the_api_takes_the_workspace_and_the_sheets(web):
    code, body = _post(web, {"workspace": "batch", "sheets": {"batch": [_ROW]}})
    assert code == 200 and body["version"] == web.worker.state.version
    _, ws, sh = web.worker.state.workspace_snapshot()
    assert ws == "batch" and sh["batch"][0]["id"] == "A1"


@pytest.mark.parametrize("body", [
    {},
    {"workspace": 3},
    {"changes": []},
    {"workspace": "map"},
    {"sheets": {"batch": [{"id": 1}]}},
])
def test_the_api_rejects_a_bad_body(web, body):
    before = web.worker.state.version
    assert _post(web, body)[0] == 400
    assert web.worker.state.version == before


def test_the_api_takes_a_table_larger_than_the_old_limit(web):
    rows = [{**_ROW, "id": f"R{i}", "note": "x" * 40} for i in range(600)]
    assert len(json.dumps({"sheets": {"batch": rows}})) > 64 * 1024
    assert _post(web, {"sheets": {"batch": rows}})[0] == 200
