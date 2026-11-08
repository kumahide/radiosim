"""
tests/test_webui_project.py
===========================
新しい画面の **`.rsproj` の保存と開く**（Link 1.0 ステージ10d）＝`webui/project_io.py` と
`/api/project/save`・`/api/project/open`。

縛ること:
- 画面の入力 → 文字列 → 画面の入力で、面の入力と入力の集合が戻る（座標は DD で書く）
- 書いた文字列は `report.project.from_dict` が読める＝旧画面と同じファイル
- 読めない値のある面はその面だけ書かず、警告で言う（旧画面と同じ流儀）
- 計算方式はファイルのまま開き、そのまま書く（3.x の v1 は v1 で計算する＝2026-11-08 経営者の決定）
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from core import calc_profile
from core import config
from core import coords
from core import i18n
from report import project
from webui import project_io
from webui import server as webserver
from webui import sheets as sheets_mod
from webui.state import AppState
from webui.worker import Worker

_ROW = {"id": "A1", "start": "34.4, 132.45", "end": "34.42, 132.5",
        "h_tx": "10", "h_rx": "5.5", "freq": "920", "note": "メモ"}
_SHEETS = {
    "batch": [_ROW, {"id": "B2", "start": "34.41, 132.46", "end": "34.43, 132.51",
                     "h_tx": "12", "h_rx": "3", "gain_tx": "2.15"}],
    "scenario": {"mode": "sweep", "compare": [{"h_tx": "20"}],
                 "sweep": {"axis": "h_tx", "from": "5", "to": "30", "points": "6"}},
    "multihop": {"path_id": "R1", "note": "", "hop_rf": [{"freq": "", "gain_tx": "3", "gain_rx": ""}],
                 "waypoints": [{"name": "起点", "coord": "34.4, 132.45", "h": "10"},
                               {"name": "終点", "coord": "34.42, 132.5", "h": "5"}]},
}


def _inputs(**changes):
    st = AppState()
    st.update(changes)
    return st.document()[0]


def _checked(sheets):
    out = sheets_mod.empty()
    out.update(sheets)
    return {n: sheets_mod.check(n, v) for n, v in out.items()}


def test_save_then_open_gives_back_the_inputs_and_the_sheets():
    inputs = _inputs(freq="2400", h_tx="15")
    sheets = _checked(_SHEETS)
    text, warnings = project_io.dump(inputs, sheets, {"project_name": "案件"})
    assert warnings == []
    back, back_sheets, meta, profile = project_io.load(text)
    # 座標だけは DD の書式へ揃う（旧画面の `_coords_to_dd` と同じ）
    assert back == {**inputs, **{k: coords.to_dd_str(inputs[k]) for k in ("start", "end")}}
    assert back_sheets == sheets
    assert meta["project_name"] == "案件"
    assert profile == calc_profile.DEFAULT_PROFILE


def test_the_text_is_the_same_file_the_classic_screen_reads():
    text, _ = project_io.dump(_inputs(), _checked(_SHEETS))
    doc = project.from_dict(json.loads(text))
    assert doc.calc_profile == calc_profile.DEFAULT_PROFILE
    assert [r.path_id for r in doc.batch_rows] == ["A1", "B2"]
    assert doc.batch_rows[1].gain_tx == 2.15 and doc.batch_rows[0].freq_mhz == 920.0
    assert doc.scenario.mode == "sweep" and doc.scenario.sweep["points"] == "6"
    assert [w.name for w in doc.multihop.waypoints] == ["起点", "終点"]
    assert doc.multihop.hop_rf[0].gain_tx == 3.0 and doc.multihop.hop_rf[0].freq_mhz is None


def test_coordinates_are_written_in_decimal_degrees():
    text, _ = project_io.dump(_inputs(start="34°24'00\"N, 132°27'00\"E"), _checked({}))
    lat, lon = (float(v) for v in json.loads(text)["params"]["start"].split(","))
    assert lat == pytest.approx(34.4) and lon == pytest.approx(132.45)


def test_empty_sheets_write_no_batch_and_no_relay_section():
    data = json.loads(project_io.dump(_inputs(), _checked({}))[0])
    assert "batch" not in data and "multihop" not in data


@pytest.mark.parametrize("bad", [{"h_tx": "abc"}, {"start": "34.4"}, {"id": ""}, {"h_rx": "nan"}])
def test_an_unreadable_row_drops_only_the_table_with_a_warning(bad):
    sheets = _checked({**_SHEETS, "batch": [_ROW, {**_ROW, "id": "X9", **bad}]})
    text, warnings = project_io.dump(_inputs(), sheets)
    data = json.loads(text)                              # 書けた文字列は正しい JSON（NaN なし）
    assert "batch" not in data and "multihop" in data
    assert len(warnings) == 1
    assert ("X9" if bad != {"id": ""} else "#2") in warnings[0]


def test_an_unreadable_relay_point_drops_only_the_relay_with_a_warning():
    mh = {**_SHEETS["multihop"], "waypoints": [{"name": "", "coord": "x", "h": "1"}]}
    text, warnings = project_io.dump(_inputs(), _checked({**_SHEETS, "multihop": mh}))
    data = json.loads(text)
    assert "multihop" not in data and len(data["batch"]["rows"]) == 2
    assert len(warnings) == 1 and not warnings[0].startswith("\n")


def test_opening_resets_the_inputs_the_file_does_not_have():
    data = json.loads(project_io.dump(_inputs(), _checked({}))[0])
    data["params"] = {"freq": "433"}
    inputs, sheets, _, _ = project_io.load(json.dumps(data))
    assert inputs["freq"] == "433"
    assert inputs["h_tx"] == config.DEFAULT_CONFIG["h_tx"]
    assert set(inputs) == set(config.SIM_KEYS)
    assert sheets == sheets_mod.empty()


def test_numbers_come_back_without_a_trailing_zero():
    text, _ = project_io.dump(_inputs(), _checked({"batch": [{**_ROW, "h_tx": "10.0"}]}))
    _, sheets, _, _ = project_io.load(text)
    assert sheets["batch"][0]["h_tx"] == "10"


def test_a_v1_file_opens_as_v1_and_is_saved_as_v1():
    data = json.loads(project_io.dump(_inputs(), _checked({}))[0])
    data["calc_profile"] = calc_profile.V1
    _, _, _, profile = project_io.load(json.dumps(data))
    assert profile == calc_profile.V1
    del data["calc_profile"]                             # 方式の無い 3.x のファイル＝v1
    inputs, sheets, _, profile = project_io.load(json.dumps(data))
    assert profile == calc_profile.V1
    text, _ = project_io.dump(inputs, sheets, None, profile)   # 開いて保存しても v1 のまま
    assert json.loads(text)["calc_profile"] == calc_profile.V1


def test_the_state_hands_the_method_to_the_calculation_and_to_the_terrain_key():
    """方式は計算の写しから `SimParams` へ・地形の鍵（双線形か）も方式で変わる＝v2 の地形を使い回さない。"""
    from core import simulation as sim
    st = AppState()
    v2 = sim.SimParams(st.snapshot()[1])
    st.update({}, profile=calc_profile.V1)
    v1 = sim.SimParams(st.snapshot()[1])
    assert (v2.calc_profile, v1.calc_profile) == (calc_profile.V2, calc_profile.V1)
    assert sim._terrain_cache_key(v1) != sim._terrain_cache_key(v2)
    with pytest.raises(ValueError):
        st.update({}, profile="v9")
    assert st.document()[2] == calc_profile.V1          # 知らない方式は何も変えない


def test_an_unknown_calculation_method_is_not_written():
    with pytest.raises(ValueError):
        project_io.dump(_inputs(), _checked({}), None, "v9")


@pytest.mark.parametrize("text", ["", "{", "[]", json.dumps({"schema_version": 99}), "1" * 5000])
def test_a_broken_text_is_a_project_error(text):
    with pytest.raises(project.ProjectError):
        project_io.load(text)


def test_a_scenario_with_an_unknown_override_is_a_project_error():
    data = json.loads(project_io.dump(_inputs(), _checked({}))[0])
    data["scenario"]["compare"] = [{"theme": "dark"}]
    with pytest.raises(project.ProjectError):
        project_io.load(json.dumps(data))


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


def _call(srv, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    conn.request("POST", path, body=data, headers={
        "Host": f"127.0.0.1:{srv.port}", "X-Token": srv.token,
        "Content-Type": "application/json"})
    resp = conn.getresponse()
    out = (resp.status, json.loads(resp.read() or b"{}"))
    conn.close()
    return out


def test_the_api_saves_and_opens_a_project(web):
    web.worker.state.update({"freq": "2400"}, sheets={"batch": [_ROW]})
    code, saved = _call(web, "/api/project/save")
    assert code == 200 and saved["warnings"] == [] and saved["name"] == "project.rsproj"
    web.worker.state.update({"freq": "920"}, sheets={"batch": []})
    before = web.worker.state.version
    code, body = _call(web, "/api/project/open", {"text": saved["text"]})
    assert code == 200 and body["version"] == before + 1
    inputs, sheets, profile = web.worker.state.document()
    assert inputs["freq"] == "2400" and sheets["batch"][0]["id"] == "A1"
    assert profile == calc_profile.DEFAULT_PROFILE


def test_the_api_opens_a_v1_file_as_v1_and_a_v2_file_back_as_v2(web):
    """v1 を開くと計算の写しも v1（`SimParams` が拾う）・画面の入力の写しには混ぜない。"""
    data = json.loads(project_io.dump(_inputs(), _checked({}))[0])
    data["calc_profile"] = calc_profile.V1
    assert _call(web, "/api/project/open", {"text": json.dumps(data)})[0] == 200
    st = web.worker.state
    assert st.snapshot()[1]["calc_profile"] == calc_profile.V1
    assert "calc_profile" not in st.wait(0, timeout=0)["inputs"]
    assert st.wait(0, timeout=0)["calc_profile"] == calc_profile.V1
    code, saved = _call(web, "/api/project/save")
    assert json.loads(saved["text"])["calc_profile"] == calc_profile.V1
    before = st.version
    data["calc_profile"] = calc_profile.V2                 # 方式だけ違うファイル＝版が上がる
    assert _call(web, "/api/project/open", {"text": json.dumps(data)})[0] == 200
    assert st.version == before + 1
    assert st.snapshot()[1]["calc_profile"] == calc_profile.V2


def test_the_api_carries_the_opened_project_name_into_the_next_save(web):
    text, _ = project_io.dump(_inputs(), _checked({}), {"project_name": "現場A", "memo": "m"})
    assert _call(web, "/api/project/open", {"text": text})[0] == 200
    code, saved = _call(web, "/api/project/save")
    assert saved["name"] == "現場A.rsproj"
    assert json.loads(saved["text"])["meta"] == {"project_name": "現場A", "memo": "m"}


def test_the_api_says_why_it_cannot_open_and_changes_nothing(web):
    before = web.worker.state.version
    code, body = _call(web, "/api/project/open", {"text": "{}"})
    assert code == 422 and body["message"] == i18n.t("proj_err_not_project")
    assert _call(web, "/api/project/open", {"text": 3})[0] == 400
    assert web.worker.state.version == before
