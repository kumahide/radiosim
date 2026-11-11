"""
tests/test_webui_review_contract.py
===================================
判断点 ② の完了条件（Link 1.0 ステージ10e）＝**同じ回線は、1 回線・複数経路・条件探索・中継の
どの面でも同じ REVIEW を表す**。面の語・件数・CSV の判定の列・地図の線・KML のフォルダ・色・
帯の件数の並び（凡例）を、本物の計算コアで 1 本の回線に通して縛る。

地形はゴールデンの標高（`tests/data/golden_links.json`）を経路の標本へ写すだけ＝ネットワーク不要。
計算は差し替えない（`engine.calculate`＝判定契約 v2 の `verdict.judge` まで本物）。
"""

from __future__ import annotations

import csv
import io
import json
import os

import numpy as np
import pytest

from core import engine, models
from core import verdict as vd
from webui import exports
from webui.state import AppState
from webui.worker import Worker

_DATA = os.path.join(os.path.dirname(__file__), "data", "golden_links.json")
with open(_DATA, encoding="utf-8") as _f:
    _LINK = {link["id"]: link for link in json.load(_f)["links"]}["nara_ikoma"]
_IN = _LINK["input"]                    # 環境区分だけで裏返る REVIEW（ゴールデンの測り）
_TX = f"{_IN['lat_tx']}, {_IN['lon_tx']}"
_RX = f"{_IN['lat_rx']}, {_IN['lon_rx']}"
_H_TX, _H_RX = str(_IN["h_tx"]), str(_IN["h_rx"])
_COMMON = {                              # 面に依らない共通設定（端点と空中線高は面ごとに渡す）
    "freq": str(_IN["freq_mhz"]), "p_tx": str(_IN["p_tx"]),
    "gain_tx": str(_IN["gain_tx"]), "gain_rx": str(_IN["gain_rx"]),
    "sens": str(_IN["sens"]), "veg_h": str(_IN["veg_h"]), "k_factor": str(_IN["k_factor"]),
    "env_type": _IN["env_type"], "diff_method": _IN["diff_method"],
    "rain_rate": str(_IN["rain_rate"]),
}
_STATIC = os.path.join(os.path.dirname(__file__), "..", "webui", "static")


def _fetch(p, cb=None, cancel=None):
    """ゴールデンの標高を経路の標本の位置へ写す（どの面でも同じ端点なら同じ地形）。"""
    raw = np.asarray(_LINK["raw_elevs"], dtype=float)
    fracs = np.asarray(p.sample_fracs, dtype=float)
    elevs = np.interp(fracs, np.linspace(0.0, 1.0, len(raw)), raw)
    return models.calculate_terrain_profile(
        raw_elevs=elevs, lat_tx=p.lat_tx, lon_tx=p.lon_tx, lat_rx=p.lat_rx, lon_rx=p.lon_rx,
        frac_axis=p.sample_fracs)


def _run(workspace: str, sheet=None, **changes):
    st = AppState()
    st.update({**_COMMON, **changes}, workspace=workspace,
              sheets=None if sheet is None else {workspace: sheet})
    w = Worker(st, fetch=_fetch, calc=engine.calculate,
               key=lambda p: (p.lat_tx, p.lon_tx, p.lat_rx, p.lon_rx), profile=lambda t, p: None)
    w.step()
    return st, w


_SHEETS = {
    "batch": ({}, [{"id": "P1", "start": _TX, "end": _RX, "h_tx": _H_TX, "h_rx": _H_RX}]),
    # 条件は基準と同じ空中線高＝2 行とも同じ回線
    "scenario": ({"start": _TX, "end": _RX, "h_tx": _H_TX, "h_rx": _H_RX},
                 {"compare": [{"h_tx": _H_TX}]}),
    "multihop": ({}, {"path_id": "R1", "hop_rf": [{}],
                      "waypoints": [{"name": "", "coord": _TX, "h": _H_TX},
                                    {"name": "終点", "coord": _RX, "h": _H_RX}]}),
}


@pytest.fixture(scope="module")
def single():
    st, _ = _run("single", start=_TX, end=_RX, h_tx=_H_TX, h_rx=_H_RX)
    got = st.headline()
    assert got["kind"] == "verdict", got
    return got


def test_the_single_link_is_review_with_the_flipping_assumption(single):
    """前提＝1 回線の面でこの回線が REVIEW（裏返す前提を名乗る）。以下はこれと同じ語を見る。"""
    assert single["word"] == vd.REVIEW
    assert single["flipped"] and single["worst"] < 0 <= single["margin"]


@pytest.mark.parametrize("workspace", list(_SHEETS))
def test_every_sheet_shows_the_same_review_in_rows_counts_csv_and_map(single, workspace):
    changes, sheet = _SHEETS[workspace]
    st, w = _run(workspace, sheet, **changes)
    got = st.headline()
    assert got["kind"] == "sheet" and got["done"] == got["total"], got
    items = got["items"]
    # 表の行＝語と余裕度が 1 回線と同じ
    assert [it["word"] for it in items] == [vd.REVIEW] * len(items)
    assert all(it["margin"] == single["margin"] for it in items)
    # 件数（帯の内訳）と全体の語
    assert got["overall"] == vd.REVIEW
    assert got["counts"] == {vd.OK: 0, vd.REVIEW: len(items), vd.NG: 0, vd.ERROR: 0}
    # CSV の判定の列（出力契約の `status`）
    _, text = exports.sheet_csv(*w.sheet_run())
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    assert [r["status"] for r in rows] == [it["word"] for it in items]
    # 地図の線（画面と画像の保存が同じ線を引く）
    lines = exports.map_lines(got, st.snapshot()[1])
    assert lines and {word for _, _, word, _ in lines} == {vd.REVIEW}


def test_the_batch_kml_puts_the_row_in_the_review_folder():
    _, w = _run("batch", _SHEETS["batch"][1])
    got = w.sheet_run()
    assert got is not None
    made = exports.sheet_kml(*got)
    assert made is not None                         # 複数経路は KML を出せる面
    _, text = made
    folder = text.split("<name>REVIEW</name>", 1)[1].split("</Folder>", 1)[0]
    assert "<name>P1</name>" in folder and "#rev" in folder


def _kml_abgr(hex_rgb: str) -> str:
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"ff{b}{g}{r}".lower()


def _purple() -> str:
    """REVIEW の色＝地図の PNG の表（帳票の `STATUS_HEX` は Pro へ移した＝Link 1.0 ステージ11b）。"""
    from report import map_graphics
    return "#%02x%02x%02x" % map_graphics.STATUS_RGB[vd.REVIEW]


def test_the_single_link_saves_the_same_review_in_its_csv_and_path_kml(single):
    """1 回線の保存（10f＝2026-11-09 経営者）＝`summary.csv` の 1 行と `path.kml`。"""
    _, w = _run("single", start=_TX, end=_RX, h_tx=_H_TX, h_rx=_H_RX)
    got = w.sheet_run()
    assert got is not None and got[0] == "single"
    name, text = exports.sheet_csv(*got)
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    assert name == "summary.csv" and len(rows) == 1
    assert rows[0]["status"] == vd.REVIEW and rows[0]["id"] == exports.SINGLE_PATH_ID
    assert float(rows[0]["margin_db"]) == pytest.approx(single["margin"], abs=0.05)
    assert rows[0]["h_tx"] == f"{float(_H_TX):.1f}"
    name, kml = exports.sheet_kml(*got)
    assert name == "path.kml" and "<name>LoS (REVIEW)</name>" in kml
    assert f'<Style id="los"><LineStyle><color>{_kml_abgr(_purple())}</color>' in kml


def test_review_is_one_purple_on_the_screen_the_map_image_and_both_kml():
    """色＝画面の CSS・JS の対応表・地図の画像（`STATUS_RGB`）・2 つの KML が同じ紫。

    帳票（Pro）の色が同じ表を引くことは Pro 側の試験が見る。
    """
    import re
    from report import report_path, report_summary
    purple = _purple()
    with open(os.path.join(_STATIC, "style.css"), encoding="utf-8") as f:
        css = f.read()
    with open(os.path.join(_STATIC, "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert re.search(r"--review:\s*" + purple + ";", css)
    assert '.grid td.word[data-word="REVIEW"] { color: var(--review); }' in css   # 表の行
    assert '#band[data-word="REVIEW"] #band-word { color: var(--review); }' in css  # 帯
    assert 'REVIEW: "--review"' in js                                              # 地図の線
    kml = report_summary.summary_kml_text([])
    assert f'<Style id="rev"><LineStyle><color>{_kml_abgr(purple)}</color>' in kml
    with open(report_path.__file__, encoding="utf-8") as f:
        assert "los_color = kml_color(result.status)" in f.read()                 # path.kml


def test_every_word_has_one_color_in_both_kml_and_the_map_image():
    """4 語の色＝2 つの KML が地図の画像（`STATUS_RGB`）と同じ（B-378）。

    以前は KML が色を直に書いていて、NG＝橙・ERROR＝赤（画面と地図の画像は NG＝赤・
    ERROR＝濃い橙）と向きが逆だった。`path.kml` は NG と ERROR を同じ橙で描いていた。
    """
    import re
    from report import map_graphics, report_path, report_summary
    style_ids = {vd.OK: "ok", vd.REVIEW: "rev", vd.NG: "ng", vd.ERROR: "err"}
    assert set(style_ids) == set(vd.WORDS)
    kml = report_summary.summary_kml_text([])
    for word, sid in style_ids.items():
        want = _kml_abgr("#%02x%02x%02x" % map_graphics.STATUS_RGB[word])
        assert map_graphics.kml_color(word) == want, word
        assert f'<Style id="{sid}"><LineStyle><color>{want}</color>' in kml, word
    assert len({map_graphics.kml_color(w) for w in vd.WORDS}) == len(vd.WORDS)
    assert map_graphics.kml_color("?") == map_graphics.kml_color(vd.ERROR)
    # 判定の色を KML の本文に直に書かない（ほかの線＝地形・フレネル・遮蔽区間は判定の色でない）
    for mod in (report_summary, report_path):
        with open(mod.__file__, encoding="utf-8") as f:
            src = f.read()
        assert not re.search(r"<Style id=\"(ok|rev|ng|err|los)\"><LineStyle><color>[0-9a-f]{8}", src), mod


def test_the_band_counts_name_every_word_in_the_report_order():
    """凡例＝帯の件数は 4 語の全部を同じ順で出す（0 件の REVIEW も消さない）。"""
    assert list(vd.counts([vd.OK])) == list(vd.WORDS)
    assert vd.REVIEW in vd.WORDS
