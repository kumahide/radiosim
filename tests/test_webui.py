"""
tests/test_webui.py
===================
新しい画面（`webui/`・4.0 ステージ8b）の約束。ネットワークも Edge も使わない。

🔒 縛るもの:
- 見出しは**今の版の入力で出したものだけ**を見せる（古い版の結果を今の判定に見せない）
- 入力は知らない鍵を受けない（1 つでもあれば何も変えない）
- 地形に効く欄が変わったら取得中の地形を取り消す・効かない欄では取り消さない
- HTTP は `Host` 違いと鍵なしを 403 で断る・静的ファイルの外へ出ない
- 起動の経路で通信の部品（`truststore`・`urllib3`・`requests`）を読まない（I-232）
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
    eirp = 23.0
    fspl = 100.0
    diff_loss = 3.0
    veg_loss = 0.0
    env_loss = 0.0
    rain_loss = 0.0
    gas_loss = 0.0
    total_loss = 103.0


def _endpoints_key(params):
    return (params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx)


def _no_profile(terrain, params):
    """地形が文字列の偽物のとき＝断面図は作らない（断面図の試験は下の節）。"""
    return None


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
    Worker(st, key=_endpoints_key, calc=lambda t, p: _Result(), profile=_no_profile).step()
    head = st.headline()
    assert head["kind"] == "input"
    assert "h_tx" in head["message"]


def test_a_valid_input_publishes_a_verdict():
    st = AppState()
    w = Worker(st, fetch=lambda p, cb, cancel: "terrain", key=_endpoints_key,
               calc=lambda t, p: _Result(), profile=_no_profile)
    w.step()
    head = st.headline()
    assert head["kind"] == "verdict" and head["word"] == "OK"
    assert head["margin"] == round(12.34, units.DB_DECIMALS)


def test_a_review_carries_the_range_and_the_flipping_assumptions():
    # 判定の帯（ステージ9c）＝前提を振った幅と、REVIEW を裏返す前提の 1 行（帳票と同じ字）
    from core import disclosure, i18n
    from core.verdict import Verdict

    class _Review(_Result):
        verdict = Verdict(word="REVIEW", base_margin=1.2, worst_margin=-2.04,
                          best_margin=3.96, flipped_by=("veg_h", "diff_method"), reviewed=True)

    st = AppState()
    Worker(st, fetch=lambda p, cb, cancel: "terrain", key=_endpoints_key,
           calc=lambda t, p: _Review(), profile=_no_profile).step()
    head = st.headline()
    assert head["word"] == "REVIEW"
    assert (head["worst"], head["best"]) == (round(-2.04, units.DB_DECIMALS),
                                             round(3.96, units.DB_DECIMALS))
    assert head["flipped"] == i18n.t("html_verdict_flipped").format(
        list=disclosure.flipped_list_text({"veg_h", "diff_method"}))
    lab = webserver.labels()
    assert "{worst}" in lab["margin_range"] and "{best}" in lab["margin_range"]


def test_ok_and_v1_carry_no_flipping_line():
    st = AppState()
    Worker(st, fetch=lambda p, cb, cancel: "terrain", key=_endpoints_key,
           calc=lambda t, p: _Result(), profile=_no_profile).step()
    head = st.headline()
    assert "flipped" not in head and "worst" not in head     # v1（verdict なし）は幅を出さない


def test_unreachable_dem_publishes_a_terrain_headline():
    def fetch(p, cb, cancel):
        raise sim.DemUnreachableError("unreachable")

    st = AppState()
    Worker(st, fetch=fetch, key=_endpoints_key, calc=lambda t, p: _Result(), profile=_no_profile).step()
    assert st.headline()["kind"] == "terrain"


def test_terrain_is_fetched_once_while_only_the_link_budget_changes():
    calls = []
    st = AppState()
    w = Worker(st, fetch=lambda p, cb, cancel: calls.append(1) or "terrain",
               key=_endpoints_key, calc=lambda t, p: _Result(), profile=_no_profile)
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
    w = Worker(st, fetch=fetch, key=_endpoints_key, calc=lambda t, p: _Result(), profile=_no_profile)
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
# 入力の列と損失の内訳（Link 1.0 ステージ9a）
# ============================================================
def _form_keys(groups):
    return [f["key"] for g in groups for f in g["fields"]]


def test_the_form_lists_the_fields_in_the_agreed_order():
    # 回線 → 無線機 → 環境 → 「詳細」（DEM は選べるソースが 1 つなら出さない＝I-153）
    from core import dem_sources
    groups = webserver.form()
    assert [g["advanced"] for g in groups] == [False, False, False, True]
    expected = ["start", "end", "h_tx", "h_rx",
                "freq", "p_tx", "gain_tx", "gain_rx", "sens",
                "env_type", "veg_h", "rain_rate",
                "diff_method", "resolution"]
    if len(dem_sources.all_sources()) > 1:
        expected.append("dem_source")
    assert _form_keys(groups) == expected


def test_every_input_but_the_rice_k_is_on_the_form():
    # ステージ6 の決定④＝ライス K の初期値（k_factor）は新しい画面に出さない。入力の集合には残る
    from core import config
    keys = _form_keys(webserver.form())
    assert len(keys) == len(set(keys))
    assert set(keys) | {"k_factor", "dem_source"} == set(config.SIM_KEYS)
    assert "k_factor" in AppState().snapshot()[1]


def test_the_dem_source_field_appears_only_with_a_choice(monkeypatch):
    from core import dem_sources
    one = dem_sources.all_sources()[:1]
    monkeypatch.setattr(dem_sources, "all_sources", lambda: one)
    assert "dem_source" not in _form_keys(webserver.form())
    monkeypatch.setattr(dem_sources, "all_sources", lambda: one * 2)
    assert "dem_source" in _form_keys(webserver.form())


def test_every_choice_is_a_value_the_validator_accepts():
    from core import config
    allowed = {"env_type": config.VALID_ENV_TYPES, "diff_method": config.VALID_DIFF_METHODS,
               "resolution": config.VALID_RESOLUTIONS}
    seen = set()
    for g in webserver.form():
        for f in g["fields"]:
            if f["key"] in allowed:
                seen.add(f["key"])
                values = [v for v, _ in f["options"]]
                assert set(values) == set(allowed[f["key"]]), f["key"]
                assert all(text for _, text in f["options"])
                assert config.DEFAULT_CONFIG[f["key"]] in values
            elif "options" not in f:
                assert f["mode"] in ("text", "decimal")
    assert seen == set(allowed)


def test_every_breakdown_row_has_a_value_in_the_verdict():
    st = AppState()
    Worker(st, fetch=lambda p, cb, cancel: "terrain", key=_endpoints_key,
           calc=lambda t, p: _Result(), profile=_no_profile).step()
    head = st.headline()
    rows = webserver.breakdown()
    assert [r["name"] for r in rows if r["name"] not in head] == []
    assert head["sens"] == float(st.snapshot()[1]["sens"])     # 入力から取る行
    assert all(r["label"] and r["unit"] for r in rows)


# ============================================================
# 断面図（Link 1.0 ステージ9b）
# ============================================================
def _real_terrain(n_override=None):
    import numpy as np

    from core import config, models
    params = sim.SimParams(dict(config.DEFAULT_CONFIG))
    n = len(params.sample_fracs)
    raw = 50.0 + 20.0 * np.sin(np.linspace(0.0, 6.0, n))
    terrain = models.calculate_terrain_profile(
        raw, params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx,
        frac_axis=params.sample_fracs)
    return terrain, params


def test_thinning_keeps_both_ends_and_the_limit():
    from webui.profile import thin
    for n in (1, 2, 1200, 1201, 2217, 5000):
        idx = thin(n, 1200)
        assert idx[0] == 0 and idx[-1] == n - 1
        assert len(idx) <= 1200 and idx == sorted(set(idx))


def test_the_profile_draws_the_same_lines_as_the_report():
    from core import units
    from webui.profile import MAX_POINTS, profile_fields
    terrain, params = _real_terrain()
    p = profile_fields(terrain, params)
    assert len(p["ground"]) <= MAX_POINTS and len(p["d"]) <= MAX_POINTS
    assert len(p["ground_d"]) == len(p["ground"])
    assert len(p["d"]) == len(p["los"]) == len(p["f1"])
    # 両端＝地表の折れ線と標本の線が空中線の足元に届く（B-160 の端の画素）
    length = round(float(units.km_to_m(terrain.horiz_dist_km)), 2)
    assert p["ground_d"][0] == 0.0 and p["ground_d"][-1] == length
    assert p["d"][0] == 0.0 and p["d"][-1] == length
    assert p["tx"][1] == p["ground"][0] and p["rx"][1] == p["ground"][-1]
    # 見通し線は空中線の先から先へ
    assert p["los"][0] == p["tx"][2] and p["los"][-1] == p["rx"][2]
    assert p["tx"][2] == round(p["tx"][1] + params.h_tx, 2)
    assert p["veg_h"] == params.veg_h


def test_a_verdict_carries_the_profile():
    terrain, params = _real_terrain()
    st = AppState()
    Worker(st, fetch=lambda p, cb, cancel: terrain, key=_endpoints_key,
           calc=lambda t, p: _Result()).step()
    head = st.headline()
    assert head["kind"] == "verdict"
    assert len(json.dumps(head["profile"])) < 100_000      # 見出しは毎回送る


def test_the_profile_words_come_from_the_report():
    # 断面図の凡例と軸は帳票と同じ語（成果物の語を画面でも使う）
    from core import i18n
    lab = webserver.labels()
    for key in ("legend_terrain", "legend_vegetation", "legend_los", "legend_fresnel"):
        assert lab[key] == i18n.t(key)
    assert lab["dist_axis"] == i18n.t("graph_dist_axis")
    assert lab["alt_axis"] == i18n.t("graph_alt_axis")
    html = (pathlib.Path(webserver.STATIC_ROOT) / "index.html").read_text(encoding="utf-8")
    for key in ("legend_terrain", "legend_vegetation", "legend_los", "legend_fresnel"):
        assert f'data-label="{key}"' in html


def _css_vars(block: str) -> dict[str, str]:
    import re
    return {k: v.lower() for k, v in re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", block)}


def test_the_verdict_colors_are_the_report_colors():
    # 判定の色は帳票と同じ（ライト＝`STATUS_HEX`・ダーク＝旧画面の `_VERDICT["dark"]`）。
    # 色は語に添えるだけ＝語ごとの規則が 4 語とも CSS にある（9d・色覚）
    import re
    from report import map_graphics
    from views import theme
    css = (pathlib.Path(webserver.STATIC_ROOT) / "style.css").read_text(encoding="utf-8")
    root = re.search(r"^:root\s*\{(.*?)\}", css, flags=re.S | re.M).group(1)
    dark = re.search(r"prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}", css,
                     flags=re.S).group(1)
    names = {"OK": "ok", "NG": "ng", "REVIEW": "review", "ERROR": "err"}
    keys = {"OK": "ok", "NG": "ng", "REVIEW": "rev", "ERROR": "err"}
    light_vars, dark_vars = _css_vars(root), _css_vars(dark)
    for word, name in names.items():
        assert light_vars[name] == map_graphics.STATUS_HEX[word].lower(), word
        assert dark_vars[name] == theme._VERDICT["dark"][keys[word]].lower(), word
        assert f'[data-word="{word}"] #band-word {{ color: var(--{name}); }}' in css, word
    assert set(names) == set(map_graphics.STATUS_HEX)


def test_the_page_carries_the_product_name_and_the_brand():
    # 窓の題は製品名・ロゴは brand.svg をそのまま描く・窓の絵は同じ SVG の `icon` から（I-238）
    from core import version
    root = pathlib.Path(webserver.STATIC_ROOT)
    html = (root / "index.html").read_text(encoding="utf-8")
    assert f"<title>{version.APP_NAME}</title>" in html
    assert '<img id="brand" src="brand.svg"' in html
    js = (root / "app.js").read_text(encoding="utf-8")
    assert 'fetch("brand.svg")' in js and 'getElementById("icon")' in js
    svg = (root / "brand.svg").read_text(encoding="utf-8")
    assert '<g id="icon"' in svg and "data-viewbox=" in svg


def test_the_script_has_no_japanese():
    # 字はサーバーの表から＝JS と HTML に日本語を直書きしない（注記は除く）
    import re
    root = pathlib.Path(webserver.STATIC_ROOT)
    for name, comment in (("app.js", r"//[^\n]*"), ("index.html", r"<!--.*?-->")):
        text = re.sub(comment, "", (root / name).read_text(encoding="utf-8"), flags=re.S)
        cjk = [ch for ch in text if "぀" <= ch <= "鿿"]
        assert cjk == [], name


# ============================================================
# HTTP
# ============================================================
@pytest.fixture
def web():
    st = AppState()
    w = Worker(st, fetch=lambda p, cb, cancel: "terrain", key=_endpoints_key,
               calc=lambda t, p: _Result(), profile=_no_profile)
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
    assert snap["form"] and snap["breakdown"]
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


_STARTUP_PATH = """
import sys
import webui.__main__ as m
m._prepare_network()
from core import dem
from webui import edge, server, state, worker
print(" ".join(k for k in ("truststore", "urllib3", "requests") if k in sys.modules))
"""


def test_the_startup_path_does_not_load_the_network_parts():
    # I-232＝truststore・urllib3・requests は初めての通信まで読まない（約 0.13 秒）。
    # requests の遅延は 3.15 の `__lazy_modules__`（core/dem.py）＝それより前は先に読む
    root = pathlib.Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root)}
    out = subprocess.run([sys.executable, "-c", _STARTUP_PATH], env=env, cwd=root,
                         capture_output=True, text=True, timeout=60, check=True).stdout.split()
    expected = [] if sys.version_info >= (3, 15) else ["requests"]
    assert out == expected
