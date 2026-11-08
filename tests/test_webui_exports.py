"""
tests/test_webui_exports.py
===========================
新しい画面の**作業面の成果物の保存**（Link 1.0 ステージ10d）＝`Worker.sheet_run` が今の版の
全件の結果を持ち、`webui.exports.sheet_csv` が旧画面と同じ書き手で出力契約の CSV を作る。
`/api/sheet/csv` は全件そろう前は 409。判断点 ②＝CSV の件数と語が画面の表と同じ。
"""

from __future__ import annotations

import csv
import http.client
import io
import json
import threading

import pytest

from core import output_contract
from core import verdict as vd
from report import report_summary
from webui import exports
from webui import server as webserver
from webui.state import AppState
from webui.worker import Worker

_ROW = {"id": "A1", "start": "34.40, 132.45", "end": "34.42, 132.50", "h_tx": "10", "h_rx": "5"}
_WAYPOINTS = [{"name": "", "coord": "34.40, 132.45", "h": "10"},
              {"name": "中継", "coord": "34.41, 132.47", "h": "15"},
              {"name": "終点", "coord": "34.42, 132.50", "h": "5"}]


class _Terrain:
    horiz_dist_km = 5.0
    fail_pct = 0.0


class _Res:
    """書き手が読む欄をそろえた結果（語は送信の高さで決める）。"""
    verdict = None
    eirp = fspl = diff_loss = veg_loss = env_loss = rain_loss = gas_loss = total_loss = 0.0
    slant_dist_km, blocked_ratio = 5.0, 10.0

    def __init__(self, h_tx: float) -> None:
        self.status = {10.0: vd.OK, 15.0: vd.REVIEW}.get(h_tx, vd.NG)
        self.actual_margin, self.p_rx = h_tx, -80.0


def _worker(st):
    return Worker(st, fetch=lambda p, cb, cancel: _Terrain(),
                  key=lambda p: (p.lat_tx, p.lon_tx, p.lat_rx, p.lon_rx),
                  calc=lambda t, p: _Res(float(p.h_tx)), profile=lambda t, p: None)


def _run(workspace, sheet, **changes):
    st = AppState()
    st.update(changes, workspace=workspace, sheets={workspace: sheet})
    w = _worker(st)
    w.step()
    return st, w


def _got(w):
    got = w.sheet_run()
    assert got is not None
    return got


def _rows(text):
    assert "\r\n" in text                                        # 旧画面と同じ行末
    return list(csv.DictReader(io.StringIO(text, newline="")))


def test_the_batch_csv_has_one_row_per_table_row_with_the_same_words():
    st, w = _run("batch", [_ROW, {**_ROW, "id": "B2", "h_tx": "abc", "note": "打ちかけ"},
                           {**_ROW, "id": "C3", "h_tx": "15"}])
    name, text = exports.sheet_csv(*_got(w))
    assert name == "summary.csv"
    assert text.splitlines()[0].split(",") == list(output_contract.SUMMARY_CSV_COLUMNS)
    rows = _rows(text)
    items = st.headline()["items"]
    assert [r["id"] for r in rows] == [it["label"] for it in items]          # 件数と順が画面と同じ
    assert [r["status"] for r in rows] == [it["word"] for it in items] == [vd.OK, vd.ERROR, vd.REVIEW]
    bad = rows[1]
    assert bad["h_tx"] == bad["h_rx"] == "" and "abc" in bad["error"] and bad["note"] == "打ちかけ"
    assert rows[0]["h_tx"] == "10.0" and rows[0]["horiz_m"] != ""


def test_the_relay_csv_is_hops_csv_one_row_per_hop():
    st, w = _run("multihop", {"path_id": "R1", "waypoints": _WAYPOINTS, "hop_rf": [{}, {}]})
    name, text = exports.sheet_csv(*_got(w))
    rows = _rows(text)
    assert name == "hops.csv" and [r["hop_index"] for r in rows] == ["1", "2"]
    assert [r["status"] for r in rows] == [it["word"] for it in st.headline()["items"]]
    assert {r["group_id"] for r in rows} == {"R1"} and rows[0]["to"] == "中継"


def test_the_scenario_csv_is_one_row_per_condition():
    st, w = _run("scenario", {"compare": [{"h_tx": "15"}, {"h_tx": "40"}]}, h_tx="10")
    name, text = exports.sheet_csv(*_got(w))
    rows = _rows(text)
    assert name == "scenario.csv"
    assert [r["status"] for r in rows] == [it["word"] for it in st.headline()["items"]]
    assert [r["h_tx"] for r in rows] == ["10", "15", "40"]


def test_no_run_before_the_step_after_an_edit_or_on_the_single_link():
    st = AppState()
    st.update({}, workspace="batch", sheets={"batch": [_ROW]})
    w = _worker(st)
    assert w.sheet_run() is None
    w.step()
    assert w.sheet_run() is not None
    st.update({}, sheets={"batch": [_ROW, {**_ROW, "id": "B2"}]})   # 版が進んだ＝古い結果は出さない
    assert w.sheet_run() is None


def test_the_old_writer_writes_the_same_bytes_as_the_text(tmp_path):
    _, w = _run("batch", [_ROW])
    results = _got(w)[1]
    report_summary._save_summary_csv(results, str(tmp_path))
    raw = (tmp_path / "summary.csv").read_bytes()
    assert raw == report_summary.summary_csv_text(results).encode("utf-8")
    assert raw.count(b"\r\n") == 2 and b"\r\r\n" not in raw


@pytest.fixture
def web():
    st = AppState()
    srv = webserver.WebServer(_worker(st), tile=lambda s, z, x, y: ("absent", None))
    th = threading.Thread(target=srv.httpd.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.httpd.shutdown()
    srv.httpd.server_close()


def _post_csv(srv):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    conn.request("POST", "/api/sheet/csv", body=b"{}", headers={
        "Host": f"127.0.0.1:{srv.port}", "X-Token": srv.token, "Content-Type": "application/json"})
    resp = conn.getresponse()
    out = (resp.status, json.loads(resp.read() or b"{}"))
    conn.close()
    return out


def test_the_api_answers_409_until_the_sheet_is_complete_then_the_csv(web):
    code, body = _post_csv(web)
    assert code == 409 and body["message"]                       # 1 回線の面＝作業面の結果が無い
    web.worker.submit({}, workspace="batch", sheets={"batch": [_ROW]})
    web.worker.step()
    code, body = _post_csv(web)
    assert code == 200 and body["name"] == "summary.csv"
    assert _rows(body["text"])[0]["status"] == vd.OK


def test_the_labels_carry_the_save_words():
    labels = webserver.labels()
    assert labels["save_csv"] and "{path}" in labels["file_saved"]
