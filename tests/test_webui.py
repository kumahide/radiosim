"""
tests/test_webui.py
===================
新しい画面（`webui/`・4.0 ステージ8b）の約束。ネットワークも Edge も使わない。

🔒 縛るもの:
- 見出しは**今の版の入力で出したものだけ**を見せる（古い版の結果を今の判定に見せない）
- 入力は知らない鍵を受けない（1 つでもあれば何も変えない）
- 地形に効く欄が変わったら取得中の地形を取り消す・効かない欄では取り消さない
- HTTP は `Host` 違いと鍵なしを 403 で断る・静的ファイルの外へ出ない
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest

from core import simulation as sim
from core import units
from webui import server as webserver
from webui.state import AppState, UnknownKeyError
from webui.worker import Worker


class _Result:
    status = "OK"
    verdict = None
    actual_margin = 12.34
    p_rx = -70.0
    fspl = 100.0
    diff_loss = 3.0
    veg_loss = 0.0
    total_loss = 103.0


def _endpoints_key(params):
    return (params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx)


# ============================================================
# 見出しの不変条件
# ============================================================
def test_headline_shows_the_result_of_the_current_version():
    st = AppState()
    st.publish(st.version, "verdict", word="OK")
    assert st.headline()["kind"] == "verdict"


def test_headline_hides_a_result_from_an_older_version():
    st = AppState()
    v = st.version
    st.update({"h_tx": "31.0"})
    st.publish(v, "verdict", word="OK")                 # 古い版の結果が後から届いた
    head = st.headline()
    assert head["kind"] == "computing"
    assert head["version"] == st.version


def test_editing_after_a_verdict_turns_the_headline_to_computing():
    st = AppState()
    st.publish(st.version, "verdict", word="OK")
    st.update({"freq": "920.0"})
    assert st.headline()["kind"] == "computing"


def test_progress_rides_on_computing_only_for_the_current_version():
    st = AppState()
    v = st.version
    st.set_progress(v, 10, 100)
    assert (st.headline()["done"], st.headline()["total"]) == (10, 100)
    st.update({"h_rx": "11.0"})
    st.set_progress(v, 50, 100)                          # 古い版の進み具合は捨てる
    assert "done" not in st.headline()


def test_progress_wakes_the_page_only_when_the_shown_percent_moves():
    # 1 点ごとに起こすと 1 回の取得で千回を超えた（実機の 2217 点）＝画面の百分率が動くときだけ
    st = AppState()
    v = st.version
    st.set_progress(v, 0, 2217)
    rev = st.wait(0, 0)["revision"]
    st.set_progress(v, 1, 2217)                          # 0% のまま
    st.set_progress(v, 22, 2217)                         # 0% のまま
    assert st.wait(0, 0)["revision"] == rev
    assert st.headline()["done"] == 22                   # 値は最新を持つ
    st.set_progress(v, 23, 2217)                         # 1% へ
    assert st.wait(0, 0)["revision"] == rev + 1


def test_computing_cannot_be_published():
    with pytest.raises(ValueError):
        AppState().publish(1, "computing")


# ============================================================
# 入力の集合
# ============================================================
def test_unknown_key_is_rejected_and_nothing_changes():
    st = AppState()
    v, before = st.snapshot()
    with pytest.raises(UnknownKeyError):
        st.update({"h_tx": "40.0", "lang": "ja"})        # lang は計算の鍵ではない
    assert st.snapshot() == (v, before)


def test_non_string_value_is_rejected():
    with pytest.raises(UnknownKeyError):
        AppState().update({"h_tx": 40.0})


def test_same_value_does_not_bump_the_version():
    st = AppState()
    v, inputs = st.snapshot()
    assert st.update({"h_tx": inputs["h_tx"]}) == v


def test_wait_returns_as_soon_as_something_changed():
    st = AppState()
    first = st.wait(0, timeout=0.01)
    threading.Timer(0.05, lambda: st.update({"h_tx": "33.0"})).start()
    later = st.wait(first["revision"], timeout=5)
    assert later["revision"] > first["revision"]
    assert later["inputs"]["h_tx"] == "33.0"


# ============================================================
# 計算のスレッド
# ============================================================
def test_invalid_input_publishes_an_input_headline():
    st = AppState({"h_tx": "abc"})
    Worker(st, key=_endpoints_key, calc=lambda t, p: _Result()).step()
    head = st.headline()
    assert head["kind"] == "input"
    assert "h_tx" in head["message"]


def test_a_valid_input_publishes_a_verdict():
    st = AppState()
    w = Worker(st, fetch=lambda p, cb, cancel: "terrain", key=_endpoints_key,
               calc=lambda t, p: _Result())
    w.step()
    head = st.headline()
    assert head["kind"] == "verdict" and head["word"] == "OK"
    assert head["margin"] == round(12.34, units.DB_DECIMALS)


def test_unreachable_dem_publishes_a_terrain_headline():
    def fetch(p, cb, cancel):
        raise sim.DemUnreachableError("unreachable")

    st = AppState()
    Worker(st, fetch=fetch, key=_endpoints_key, calc=lambda t, p: _Result()).step()
    assert st.headline()["kind"] == "terrain"


def test_terrain_is_fetched_once_while_only_the_link_budget_changes():
    calls = []
    st = AppState()
    w = Worker(st, fetch=lambda p, cb, cancel: calls.append(1) or "terrain",
               key=_endpoints_key, calc=lambda t, p: _Result())
    w.step()
    w.submit({"p_tx": "13.0"})
    w.step()
    assert len(calls) == 1


class _BlockingFetch:
    """取得中の状態を作る＝`release` か `cancel` が立つまで戻らない。"""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancel: "threading.Event | None" = None

    def __call__(self, params, on_progress, cancel):
        self.cancel = cancel
        self.started.set()
        while not self.release.wait(0.01):
            if cancel.is_set():
                raise sim.Cancelled()
        return "terrain"


def _start_fetching():
    st = AppState()
    fetch = _BlockingFetch()
    w = Worker(st, fetch=fetch, key=_endpoints_key, calc=lambda t, p: _Result())
    th = threading.Thread(target=w.step, daemon=True)
    th.start()
    assert fetch.started.wait(5)
    return st, w, fetch, th


def test_changing_a_terrain_field_cancels_the_running_fetch():
    st, w, fetch, th = _start_fetching()
    w.submit({"start": "34.6000, 132.4118"})
    th.join(5)
    assert fetch.cancel is not None and fetch.cancel.is_set()
    assert st.headline()["kind"] == "computing"         # 取り消しは失敗ではない


def test_changing_a_budget_field_keeps_the_running_fetch():
    st, w, fetch, th = _start_fetching()
    w.submit({"sens": "-90.0"})
    assert fetch.cancel is not None and not fetch.cancel.is_set()
    fetch.release.set()
    th.join(5)
    assert st.headline()["kind"] == "computing"         # 古い版の計算は載せない
    w.step()                                            # 次の手順で最新の入力を計算する
    assert st.headline()["kind"] == "verdict"


# ============================================================
# HTTP
# ============================================================
@pytest.fixture
def web():
    st = AppState()
    w = Worker(st, fetch=lambda p, cb, cancel: "terrain", key=_endpoints_key,
               calc=lambda t, p: _Result())
    srv = webserver.WebServer(w, tile=lambda s, z, x, y: ("absent", None))
    th = threading.Thread(target=srv.httpd.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.httpd.shutdown()
    srv.httpd.server_close()


def _request(srv, method, path, *, host=None, token=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{srv.port}"}
    if token:
        headers["X-Token"] = token
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    out = (resp.status, resp.read())
    conn.close()
    return out


def test_a_foreign_host_is_forbidden(web):
    assert _request(web, "GET", "/", host="evil.example:80")[0] == 403
    assert _request(web, "GET", "/api/state", host="localhost", token=web.token)[0] == 403


def test_the_api_needs_the_token(web):
    assert _request(web, "GET", "/api/state")[0] == 403
    assert _request(web, "GET", "/api/state", token="wrong")[0] == 403
    assert _request(web, "POST", "/api/inputs", body={"changes": {}})[0] == 403


def test_the_first_state_carries_the_labels(web):
    code, body = _request(web, "GET", "/api/state?after=0", token=web.token)
    snap = json.loads(body)
    assert code == 200
    assert snap["labels"]["computing"]
    assert snap["headline"]["kind"] in ("computing", "verdict")


def test_inputs_reject_unknown_keys(web):
    code, _ = _request(web, "POST", "/api/inputs", token=web.token,
                       body={"changes": {"proxy_url": "http://x"}})
    assert code == 400
    code, body = _request(web, "POST", "/api/inputs", token=web.token,
                          body={"changes": {"h_tx": "35.0"}})
    assert code == 200 and json.loads(body)["version"] == web.worker.state.version


def test_static_files_are_served_and_cannot_escape(web):
    assert _request(web, "GET", "/")[0] == 200
    assert _request(web, "GET", "/app.js")[0] == 200
    assert _request(web, "GET", "/../server.py")[0] == 404
    assert webserver.resolve_static("/../server.py") is None
    assert webserver.resolve_static("/..%2Fserver.py") is None
    assert webserver.resolve_static("/index.html") is not None


def test_an_absent_tile_is_a_404(web):
    assert _request(web, "GET", "/tile/pale/5/28/12.png")[0] == 404
    assert _request(web, "GET", "/tile/../5/28/12.png")[0] in (403, 404)


def test_leaving_the_page_quits_unless_a_page_comes_back(monkeypatch):
    quit_called = threading.Event()
    st = AppState()
    srv = webserver.WebServer(Worker(st), on_quit=quit_called.set)
    monkeypatch.setattr(webserver, "_LEAVE_GRACE_S", 0.05)
    try:
        srv.page_left()
        srv.page_seen()                                  # 読み直し＝次のページが来た
        assert not quit_called.wait(0.3)
    finally:
        srv.httpd.server_close()
