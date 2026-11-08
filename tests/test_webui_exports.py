"""
tests/test_webui_exports.py
===========================
新しい画面の**作業面の成果物の保存**（Link 1.0 ステージ10d）＝`Worker.sheet_run` が今の版の
全件の結果を持ち、`webui.exports.sheet_csv` が旧画面と同じ書き手で出力契約の CSV を作る。
`/api/sheet/csv` は全件そろう前は 409。判断点 ②＝CSV の件数と語が画面の表と同じ。
KML（`summary.kml`）は複数経路だけ＝旧画面と同じ。REVIEW は自分のフォルダ（紫）。
地図の画像は画面の地図と同じ線を帳票と同じ絵で焼く（`/api/image/map`＝判定の前は 409・背景が無ければ 503）。
"""

from __future__ import annotations

import csv
import http.client
import base64
import io
import json
import threading
import xml.etree.ElementTree as ET

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
    raw_elevs = (20.0, 30.0)                                     # KML の線の高さ（両端）


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


_KML = "{http://www.opengis.net/kml/2.2}"


def _kml_folders(text):
    """フォルダ名 → その中の (Placemark 名, 線があるか) の並び。"""
    out = {}
    for f in ET.fromstring(text).iter(_KML + "Folder"):
        out[f.findtext(_KML + "name")] = [
            (pm.findtext(_KML + "name"), pm.find(_KML + "LineString") is not None)
            for pm in f.iter(_KML + "Placemark")]
    return out


def _kml(w):
    made = exports.sheet_kml(*_got(w))
    assert made is not None
    return made


def test_the_batch_kml_sorts_rows_into_the_four_words_with_review_in_purple():
    st, w = _run("batch", [_ROW, {**_ROW, "id": "B2", "h_tx": "abc"},
                           {**_ROW, "id": "C3", "h_tx": "15"}, {**_ROW, "id": "D4", "h_tx": "3"}])
    name, text = _kml(w)
    assert name == "summary.kml"
    folders = _kml_folders(text)
    assert list(folders) == ["OK", "REVIEW", "NG", "Error"]
    assert folders == {"OK": [("A1", True)], "REVIEW": [("C3", True)], "NG": [("D4", True)],
                       "Error": [("B2", False)]}                 # 座標の無い誤りの行は線を引かない
    assert "ff9a1b6a" in text and "nan" not in text.lower()
    # 判断点 ②＝件数と語が画面の表と同じ
    words = {"OK": vd.OK, "REVIEW": vd.REVIEW, "NG": vd.NG, "Error": vd.ERROR}
    got = {pm: words[f] for f, pms in folders.items() for pm, _ in pms}
    assert got == {it["label"]: it["word"] for it in st.headline()["items"]}


def test_the_relay_and_scenario_sheets_have_no_kml_as_in_the_old_screen():
    _, w = _run("multihop", {"path_id": "R1", "waypoints": _WAYPOINTS, "hop_rf": [{}, {}]})
    assert exports.sheet_kml(*_got(w)) is None
    _, w = _run("scenario", {"compare": [{"h_tx": "15"}]}, h_tx="10")
    assert exports.sheet_kml(*_got(w)) is None


def test_the_old_kml_writer_writes_the_text(tmp_path):
    _, w = _run("batch", [_ROW, {**_ROW, "id": "C3", "h_tx": "15"}])
    results = _got(w)[1]
    report_summary.save_summary_kml(results, str(tmp_path))
    written = _kml_folders((tmp_path / "summary.kml").read_text(encoding="utf-8"))
    assert written == _kml_folders(report_summary.summary_kml_text(results))
    assert written["REVIEW"] == [("C3", True)]                   # 旧画面も REVIEW を Error へ落とさない


def test_the_map_lines_are_the_screen_lines_with_the_words():
    st, _ = _run("batch", [_ROW, {**_ROW, "id": "B2", "h_tx": "abc"}, {**_ROW, "id": "C3", "h_tx": "15"}])
    _, inputs = st.snapshot()
    lines = exports.map_lines(st.headline(), inputs)
    # 座標の無い誤りの行は描かない（画面と同じ）・語と名前は表の行
    assert [(w, n) for _, _, w, n in lines] == [(vd.OK, "A1"), (vd.REVIEW, "C3")]
    assert lines[0][:2] == ((34.40, 132.45), (34.42, 132.50))
    st, _ = _run("scenario", {"compare": [{"h_tx": "15"}, {"h_tx": "40"}]}, h_tx="10")
    got = st.headline()
    lines = exports.map_lines(got, st.snapshot()[1])
    assert len(lines) == 1 and lines[0][2] == got["overall"]    # 条件探索は 1 本を全体の語で


def test_no_map_lines_before_the_sheet_is_complete_or_without_a_verdict():
    assert exports.map_lines({"kind": "computing"}, {}) is None
    got = {"kind": "sheet", "workspace": "batch", "done": 1, "total": 2,
           "items": [{"label": "A1", "word": vd.OK, "tx": [34.4, 132.45], "rx": [34.42, 132.5]}]}
    assert exports.map_lines(got, {}) is None
    got = {**got, "done": 1, "total": 1, "items": [{"label": "B2", "word": vd.ERROR}]}
    assert exports.map_lines(got, {}) is None                    # 描ける線が無い


def test_the_map_png_is_the_report_paths_map(monkeypatch):
    from PIL import Image

    from report import report_map
    got = {}

    def fake(specs, basemap_source_id):
        got["specs"], got["basemap"] = specs, basemap_source_id
        return Image.new("RGB", (4, 2))
    monkeypatch.setattr(report_map, "render_paths_map", fake)
    png = exports.map_png([((34.4, 132.45), (34.42, 132.5), vd.REVIEW, "A1")], "std")
    assert png.startswith(b"\x89PNG") and got["basemap"] == "std"
    assert got["specs"] == [report_map.PathSpec(tx=(34.4, 132.45), rx=(34.42, 132.5),
                                                status=vd.REVIEW, label="A1")]
    monkeypatch.setattr(report_map, "render_paths_map", lambda specs, basemap_source_id: None)
    assert exports.map_png([((0.0, 0.0), (0.0, 0.1), vd.OK, "")], "pale") is None


@pytest.fixture
def web():
    st = AppState()
    srv = webserver.WebServer(_worker(st), tile=lambda s, z, x, y: ("absent", None),
                              map_png=lambda lines, basemap: b"PNG:" + repr(lines).encode())
    th = threading.Thread(target=srv.httpd.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.httpd.shutdown()
    srv.httpd.server_close()


def _post_csv(srv, path="/api/sheet/csv"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    conn.request("POST", path, body=b"{}", headers={
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


def test_the_kml_api_answers_for_the_batch_sheet_only(web):
    code, body = _post_csv(web, "/api/sheet/kml")
    assert code == 409 and body["message"]                       # 結果が無い
    web.worker.submit({}, workspace="multihop",
                      sheets={"multihop": {"path_id": "R1", "waypoints": _WAYPOINTS, "hop_rf": [{}, {}]}})
    web.worker.step()
    code, body = _post_csv(web, "/api/sheet/kml")
    assert code == 409 and body["message"] != _post_csv(web)[1].get("message")   # 面が違う旨
    web.worker.submit({}, workspace="batch", sheets={"batch": [_ROW]})
    web.worker.step()
    code, body = _post_csv(web, "/api/sheet/kml")
    assert code == 200 and body["name"] == "summary.kml"
    assert _kml_folders(body["text"])["OK"] == [("A1", True)]


def test_the_map_api_answers_409_before_a_result_503_without_tiles_then_the_png(web):
    code, body = _post_csv(web, "/api/image/map")
    assert code == 409 and body["message"]                       # まだ判定が無い
    web.worker.submit({}, workspace="batch", sheets={"batch": [_ROW]})
    web.worker.step()
    code, body = _post_csv(web, "/api/image/map")
    assert code == 200 and body["name"] == "map.png"
    png = base64.b64decode(body["png"])
    assert png.startswith(b"PNG:") and b"'A1'" in png
    web._map_png = lambda lines, basemap: None                   # 背景のタイルが取れない
    code, body = _post_csv(web, "/api/image/map")
    assert code == 503 and body["message"]


def test_the_labels_carry_the_save_words():
    labels = webserver.labels()
    assert labels["save_csv"] and labels["save_kml"] and "{path}" in labels["file_saved"]
    assert labels["save_map_png"] and labels["save_profile_png"] and labels["no_profile"]
