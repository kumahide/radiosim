"""
tests/test_webui_workspace.py
=============================
新しい画面の**作業面の入力**（Link 1.0 ステージ10a）＝`webui/sheets.py` と
`state.AppState` の面・面の入力・`/api/inputs` の口。
**作業面の実行**（ステージ10b）＝`Worker.step` が面ごとに回し、1 件ごとに `sheet` の見出しを
載せる・全件そろったら集約と件数・入力の誤りは `input`・版が進めば取り消す。
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

from core import config
from core import scenario as scn
from core import simulation as sim
from core import verdict as vd
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
    slant_dist_km = 1.5


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


# ============================================================
# 作業面の実行（ステージ10b）
# ============================================================
class _Word:
    """語だけを持つ結果（`_verdict_fields` は `verdict` が無ければ `status` を読む）。"""
    verdict = None
    eirp = fspl = diff_loss = veg_loss = env_loss = rain_loss = gas_loss = total_loss = 0.0
    slant_dist_km = 1.5

    def __init__(self, word: str, margin: float) -> None:
        self.status, self.actual_margin, self.p_rx = word, margin, -80.0


def _by_height(t, p):
    """送信の高さで語を決める（10 m＝OK・15 m＝REVIEW・ほか＝NG）。"""
    return _Word({10.0: vd.OK, 15.0: vd.REVIEW}.get(float(p.h_tx), vd.NG), float(p.h_tx))


class _Fetch:
    """地形の取得の偽物＝呼ばれた回数を数え、`hook` があれば取得の途中で呼ぶ。"""

    def __init__(self, hook=None) -> None:
        self.calls, self.hook = 0, hook

    def __call__(self, p, cb, cancel):
        self.calls += 1
        if cb:
            cb(int(p.num))
        if self.hook:
            self.hook(self.calls)
        if cancel is not None and cancel.is_set():
            raise sim.Cancelled()
        return "terrain"


def _worker(st, fetch=None, calc=_by_height):
    return Worker(st, fetch=fetch or _Fetch(), key=lambda p: (p.lat_tx, p.lon_tx, p.lat_rx, p.lon_rx),
                  calc=calc, profile=lambda t, p: None)


def _sheet_state(workspace, sheet, **changes):
    st = AppState()
    st.update(changes, workspace=workspace, sheets={workspace: sheet})
    return st


def test_the_batch_runs_every_row_and_aggregates():
    st = _sheet_state("batch", [_ROW, {**_ROW, "id": "B2", "h_tx": "15"},
                                {**_ROW, "id": "C3", "h_tx": "30"}])
    _worker(st).step()
    head = st.headline()
    assert head["kind"] == "sheet" and head["workspace"] == "batch"
    assert [it["label"] for it in head["items"]] == ["A1", "B2", "C3"]
    assert [it["word"] for it in head["items"]] == [vd.OK, vd.REVIEW, vd.NG]
    assert head["done"] == head["total"] == 3
    assert head["overall"] == vd.NG                               # NG＞REVIEW＞OK
    assert head["counts"] == {vd.OK: 1, vd.REVIEW: 1, vd.NG: 1, vd.ERROR: 0}
    assert head["items"][0]["tx"] == [pytest.approx(34.40), pytest.approx(132.45)]
    assert head["items"][0]["margin"] == 10.0
    # 「距離」の項目は選んだ行の値＝1 件ごとに斜距離と水平距離（字は桁区切りの m）を持つ
    from core import models
    it = head["items"][0]
    horiz = models.horizontal_distance_km(*it["tx"], *it["rx"])
    assert it["slant"] == "1,500" and it["horiz"] == f"{round(horiz * 1000):,}"


def test_the_batch_publishes_each_row_and_the_overall_only_at_the_end():
    st = _sheet_state("batch", [_ROW, {**_ROW, "id": "B2"}])
    seen = []
    publish = st.publish
    st.publish = lambda v, kind, **f: (seen.append(dict(f)), publish(v, kind, **f))
    _worker(st).step()
    assert [(f["done"], f["total"]) for f in seen] == [(1, 2), (2, 2)]
    assert "overall" not in seen[0] and "counts" not in seen[0]
    assert seen[1]["overall"] == vd.OK


def test_a_failing_row_is_an_error_item_and_the_overall_is_error():
    def calc(t, p):
        if float(p.h_tx) == 30.0:
            raise ZeroDivisionError("boom")
        return _by_height(t, p)

    st = _sheet_state("batch", [_ROW, {**_ROW, "id": "B2", "h_tx": "30"}])
    _worker(st, calc=calc).step()
    head = st.headline()
    assert head["items"][1]["word"] == vd.ERROR and "boom" in head["items"][1]["message"]
    assert head["overall"] == vd.ERROR and head["counts"][vd.ERROR] == 1


def test_an_empty_batch_is_an_input_headline_before_any_fetch():
    fetch = _Fetch()
    st = _sheet_state("batch", [])
    _worker(st, fetch=fetch).step()
    head = st.headline()
    assert head["kind"] == "input" and head["message"]
    assert fetch.calls == 0


@pytest.mark.parametrize("bad, word_in_message", [
    ({**_ROW, "id": "B2", "h_tx": "abc"}, "abc"),                # 読めない数
    ({**_ROW, "id": "B2", "start": "abc"}, "start"),             # 読めない座標
    (dict(_ROW), "A1"),                                          # 同じ ID（後の行が ERROR）
    ({**_ROW, "id": "a b"}, "a b"),                              # 使えない字
    ({**_ROW, "id": "B2", "h_tx": "-5"}, "B2"),                  # 値域
])
def test_a_bad_row_is_an_error_item_and_the_other_rows_still_run(bad, word_in_message):
    # 2026-11-08 経営者の決定＝誤りの行だけ ERROR・ほかの行は計算する（旧画面は全体を止めた）
    fetch = _Fetch()
    st = _sheet_state("batch", [_ROW, bad, {**_ROW, "id": "C3", "h_tx": "15"}])
    w = _worker(st, fetch=fetch)
    w.step()
    head = st.headline()
    assert head["kind"] == "sheet" and head["done"] == head["total"] == 3
    assert [it["word"] for it in head["items"]] == [vd.OK, vd.ERROR, vd.REVIEW]
    assert word_in_message in head["items"][1]["message"]
    assert "tx" not in head["items"][1]                          # 地図に描かない
    assert head["overall"] == vd.ERROR and head["counts"][vd.ERROR] == 1
    assert fetch.calls == 2                                      # 誤りの行は地形を取らない
    assert w._shapes[1][1] is None and w._shapes[1][2] is not None   # 誤りの行は断面の元が無い


def test_a_row_with_no_id_is_named_by_its_line():
    st = _sheet_state("batch", [_ROW, {**_ROW, "id": ""}])
    _worker(st).step()
    head = st.headline()
    assert [it["label"] for it in head["items"]] == ["A1", "#2"]
    assert head["items"][1]["word"] == vd.ERROR


def test_every_row_bad_is_all_error_without_any_fetch():
    fetch = _Fetch()
    st = _sheet_state("batch", [{**_ROW, "h_tx": "abc"}])
    _worker(st, fetch=fetch).step()
    head = st.headline()
    assert head["kind"] == "sheet" and head["overall"] == vd.ERROR
    assert fetch.calls == 0


def test_the_batch_checks_the_common_settings_but_not_the_single_link_ends():
    st = _sheet_state("batch", [_ROW], start="abc", end="abc")   # 1 回線の端点は飾り
    _worker(st).step()
    assert st.headline()["kind"] == "sheet"
    st.update({"freq": "0"})
    _worker(st).step()
    head = st.headline()
    assert head["kind"] == "input" and "[freq]" in head["message"]


def test_the_relay_runs_each_hop_named_by_its_ends():
    st = _sheet_state("multihop", {"path_id": "R1", "waypoints": _WAYPOINTS,
                                   "hop_rf": [{}, {}]})
    _worker(st).step()
    head = st.headline()
    assert head["kind"] == "sheet" and head["workspace"] == "multihop"
    assert [it["label"] for it in head["items"]] == ["P1 → 中継", "中継 → 終点"]
    assert [it["word"] for it in head["items"]] == [vd.OK, vd.REVIEW]
    assert head["overall"] == vd.REVIEW


def test_a_relay_with_one_point_is_an_input_headline():
    st = _sheet_state("multihop", {"path_id": "R1", "waypoints": _WAYPOINTS[:1]})
    _worker(st).step()
    assert st.headline()["kind"] == "input"


def test_compare_reuses_the_single_link_terrain_and_publishes_all_at_once():
    fetch = _Fetch()
    st = _sheet_state("scenario", {"compare": [{"h_tx": "15"}, {"h_tx": "40"}]}, h_tx="10")
    w = _worker(st, fetch=fetch)
    w.step()
    head = st.headline()
    assert head["kind"] == "sheet" and head["mode"] == "compare"
    assert [it["word"] for it in head["items"]] == [vd.OK, vd.REVIEW, vd.NG]
    assert head["items"][1]["overrides"] == {"h_tx": 15.0}
    assert head["done"] == head["total"] == 3 and head["overall"] == vd.NG
    st.update({}, sheets={"scenario": {"compare": [{"h_tx": "15"}]}})
    w.step()
    assert st.headline()["overall"] == vd.REVIEW
    assert fetch.calls == 1                                      # 地形の鍵が同じ＝取り直さない


def test_the_sweep_carries_its_axis_and_values():
    st = _sheet_state("scenario", {"mode": "sweep", "sweep": {
        "axis": "h_tx", "from": "10", "to": "20", "points": "3"}})
    _worker(st).step()
    head = st.headline()
    assert head["mode"] == "sweep" and head["axis"] == "h_tx"
    assert head["values"] == [10.0, 15.0, 20.0]
    assert [it["word"] for it in head["items"]] == [vd.OK, vd.REVIEW, vd.NG]


@pytest.mark.parametrize("spec, changes", [
    ({"compare": [{"freq_mhz": "0"}]}, {}),                      # 条件の値域
    ({"compare": []}, {"freq": "0"}),                             # 基準の値域
    ({"compare": []}, {"start": "abc"}),                          # 1 回線の端点（条件探索は使う）
])
def test_a_bad_scenario_is_an_input_headline_before_any_fetch(spec, changes):
    fetch = _Fetch()
    st = _sheet_state("scenario", spec, **changes)
    _worker(st, fetch=fetch).step()
    assert st.headline()["kind"] == "input"
    assert fetch.calls == 0


def test_an_edit_during_the_batch_cancels_it_and_wakes_the_thread():
    holder = {}

    def hook(calls):
        if calls == 1:                                           # 1 件目の取得の途中で表を直す
            holder["w"].submit({}, sheets={"batch": [{**_ROW, "id": "Z9"}]})

    fetch = _Fetch(hook)
    st = _sheet_state("batch", [_ROW, {**_ROW, "id": "B2"}])
    w = holder["w"] = _worker(st, fetch=fetch)
    w._wake.clear()
    w.step()
    assert st.headline()["kind"] == "computing"                   # 古い版の結果は見せない
    assert w._wake.is_set() and fetch.calls == 1                  # 2 件目へ進まない
    w.step()
    head = st.headline()
    assert [it["label"] for it in head["items"]] == ["Z9"]


def test_an_edit_on_the_single_link_also_cancels_the_batch():
    # 1 回線の「地形の鍵が違うときだけ」とは別＝作業面は版が進めば無条件に止める
    holder = {}
    fetch = _Fetch(lambda calls: holder["w"].submit({"rain_rate": "5"}) if calls == 1 else None)
    st = _sheet_state("batch", [_ROW, {**_ROW, "id": "B2"}])
    w = holder["w"] = _worker(st, fetch=fetch)
    w.step()
    assert fetch.calls == 1 and st.headline()["kind"] == "computing"


def test_an_unreachable_dem_stops_the_batch_with_a_terrain_headline():
    def fetch(p, cb, cancel):
        raise sim.DemUnreachableError("unreachable")

    st = _sheet_state("batch", [_ROW, {**_ROW, "id": "B2"}])
    _worker(st, fetch=fetch).step()
    head = st.headline()
    assert head["kind"] == "terrain" and head["message"] == "unreachable"


def test_the_first_fetch_shows_its_progress_until_the_first_row():
    st = _sheet_state("batch", [_ROW])
    seen = []

    def hook(calls):
        seen.append(st.headline())

    _worker(st, fetch=_Fetch(hook)).step()
    assert seen[0]["kind"] == "computing" and seen[0]["total"] > 0
    assert seen[0]["done"] == seen[0]["total"]


# ---- ステージ10c の裏側（行ごとの断面図・CSV の読み込み・表の列） --------------------
def test_a_row_profile_comes_only_for_the_current_version():
    st = _sheet_state("batch", [_ROW, {**_ROW, "id": "B2", "h_tx": "15"}])
    w = Worker(st, fetch=_Fetch(), key=lambda p: None, calc=_by_height,
               profile=lambda t, p: {"h_tx": float(p.h_tx), "terrain": t})
    w.step()
    v = st.version
    assert w.profile_for(v, 1) == {"h_tx": 15.0, "terrain": "terrain"}
    assert w.profile_for(v, 2) is None                           # 無い件
    assert w.profile_for(v - 1, 0) is None                       # 古い版
    st.update({"freq": "925"})
    assert w.profile_for(v, 0) is None                           # 版が進んだ＝古い入力の断面


def test_a_failed_row_has_no_profile():
    def calc(t, p):
        if float(p.h_tx) == 15.0:
            raise RuntimeError("boom")
        return _by_height(t, p)
    st = _sheet_state("batch", [_ROW, {**_ROW, "id": "B2", "h_tx": "15"}])
    w = Worker(st, fetch=_Fetch(), key=lambda p: None, calc=calc, profile=lambda t, p: {"ok": 1})
    w.step()
    v = st.version
    assert w.profile_for(v, 0) == {"ok": 1}
    assert w.profile_for(v, 1) == {"ok": 1}                      # 地形はある＝計算だけ失敗


def test_a_scenario_row_profile_uses_its_overridden_height():
    st = _sheet_state("scenario", {"compare": [{"h_tx": "40"}]}, h_tx="10")
    w = Worker(st, fetch=_Fetch(), key=lambda p: None, calc=_by_height,
               profile=lambda t, p: float(p.h_tx))
    w.step()
    assert [w.profile_for(st.version, i) for i in (0, 1)] == [10.0, 40.0]


def test_csv_text_becomes_the_batch_table_as_strings():
    text = "\ufeffID, Start ,end,h_tx,h_rx,extra\nA1,\"34.4, 132.45\",\"34.41, 132.46\",10,5,x\n"
    rows = sheets.batch_from_csv(text)
    assert rows == [{**{k: "" for k in CSV_COLUMNS}, "id": "A1", "start": "34.4, 132.45",
                     "end": "34.41, 132.46", "h_tx": "10", "h_rx": "5"}]


@pytest.mark.parametrize("text", ["", "id,start\nA1,1\n", "id,start,end,h_tx,h_rx\n"])
def test_a_bad_csv_is_a_message(text):
    with pytest.raises(ValueError):
        sheets.batch_from_csv(text)


def _call(srv, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    conn.request(method, path, body=data, headers={
        "Host": f"127.0.0.1:{srv.port}", "X-Token": srv.token,
        "Content-Type": "application/json"})
    resp = conn.getresponse()
    out = (resp.status, json.loads(resp.read() or b"{}"))
    conn.close()
    return out


def test_the_api_imports_a_csv_into_the_batch_sheet(web):
    code, body = _call(web, "POST", "/api/batch_csv",
                       {"text": "id,start,end,h_tx,h_rx\nA1,\"1, 2\",\"1, 3\",10,5\n"})
    assert code == 200 and body["rows"] == 1
    _, _, sh = web.worker.state.workspace_snapshot()
    assert sh["batch"][0]["id"] == "A1"
    code, body = _call(web, "POST", "/api/batch_csv", {"text": "id\n"})
    assert code == 422 and body["message"]
    assert _call(web, "POST", "/api/batch_csv", {"text": 3})[0] == 400


def test_the_api_answers_404_for_a_profile_it_does_not_have(web):
    assert _call(web, "GET", f"/api/profile?v={web.worker.state.version}&item=0")[0] == 404
    assert _call(web, "GET", "/api/profile?v=x&item=0")[0] == 400


def test_the_sheet_spec_follows_the_workspaces_and_the_typed_side():
    spec = webserver.sheet_spec()
    assert [w["key"] for w in spec["workspaces"]] == list(sheets.WORKSPACES)
    assert [c["key"] for c in spec["compare"]] == list(scn.OVERRIDABLE)
    assert [k for k, _ in spec["sweep_axes"]] == list(scn.SWEEP_AXES)
    assert {c["key"] for c in spec["batch"]} <= set(CSV_COLUMNS)
    assert [c["key"] for c in spec["waypoints"]] == ["name", "coord", "h"]
    assert all(c["label"] for part in ("batch", "compare", "waypoints", "hops")
               for c in spec[part])
    assert {c["input"] for c in spec["compare"]} <= set(config.SIM_KEYS)
    assert {c["key"] for c in spec["hops"]} <= set(config.SIM_KEYS)   # 区間の薄字＝共通設定
    assert spec["limits"] == {"compare": scn.MAX_COMPARE_CONDITIONS,
                              "waypoints": sheets.MAX_WAYPOINTS}
