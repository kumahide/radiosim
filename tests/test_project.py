"""
test_project.py
===============
`project.py`（`.rsproj` の読み書き）のガード。

**このテストが厚い理由**＝条件探索の条件セットと中継経路の waypoint 列は
**窓の中身以外に器が無い**（CSV も settings.json も受け皿にならない）ため、
`.rsproj` がその 2 機能の**唯一の永続化手段**になる。後方互換の約束が重い。

守っている性質（変えるときは設計判断が要る）:
  1. 往復で値が変わらない（`None` は `None` のまま＝共通設定の踏襲が崩れない）
  2. **app キー（theme/lang/proxy_url）を絶対に取り込まない**
  3. 節が無い＝「その窓の情報を持たない」（`None`）であって「空」ではない
  4. 自分より新しい schema は**拒否する**
  5. 我々のファイルでないもの（settings.json 等）は**プロジェクトとして読まない**
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import batch
import multihop as mh
import project


def _doc() -> project.ProjectDoc:
    """全節が埋まったプロジェクト（往復テストの母体）。"""
    return project.ProjectDoc(
        meta   = {"project_name": "○○市 中継検討", "memo": "メモ 1 行目"},
        params = {"start": "34.5429, 132.4118", "end": "34.5389, 132.4050",
                  "h_tx": "30.0", "h_rx": "10.0", "freq": "2400.0",
                  "p_tx": "20.0", "gain_tx": "3.0", "gain_rx": "3.0",
                  "sens": "-85.0", "veg_h": "10.0", "k_factor": "10.0",
                  "samples": "200", "env_type": "los", "rain_rate": "0.0",
                  "diff_method": "deygout"},
        batch_rows = [
            batch.PathRow(path_id="P1", lat_tx=34.5, lon_tx=132.4,
                          lat_rx=34.6, lon_rx=132.5, h_tx=30.0, h_rx=10.0,
                          note="1 本目"),
            batch.PathRow(path_id="P2", lat_tx=34.7, lon_tx=132.6,
                          lat_rx=34.8, lon_rx=132.7, h_tx=20.0, h_rx=5.0,
                          freq_mhz=400.0, gain_tx=12.0, gain_rx=2.0),
        ],
        scenario = project.ScenarioSpec(
            mode="sweep",
            compare=[{"h_tx": "40", "freq_mhz": ""}],
            sweep={"axis": "h_tx", "from": "10", "to": "50", "points": "5"}),
        multihop = mh.MultiHopPath(
            path_id="R1",
            waypoints=[mh.Waypoint("TX", 34.5, 132.4, 30.0),
                       mh.Waypoint("R1", 34.55, 132.45, 40.0),
                       mh.Waypoint("RX", 34.6, 132.5, 10.0)],
            hop_rf=[mh.HopRF(), mh.HopRF(freq_mhz=5600.0, gain_tx=20.0)],
            note="中継 1 段"),
    )


# ------------------------------------------------------------
# 1. 往復
# ------------------------------------------------------------
def test_roundtrip_preserves_every_section():
    """全節が dict 往復で等価（**核心**）。"""
    src = _doc()
    got = project.from_dict(project.to_dict(src))
    assert got.meta       == src.meta
    assert got.params     == src.params
    assert got.batch_rows == src.batch_rows
    assert got.scenario   == src.scenario
    assert got.multihop   == src.multihop


def test_roundtrip_through_file(tmp_path):
    """実ファイル往復（UTF-8・日本語の案件名が壊れない）。"""
    path = str(tmp_path / "p.rsproj")
    project.save(_doc(), path)
    got = project.load(path)
    assert got.meta["project_name"] == "○○市 中継検討"
    assert got.multihop is not None and got.multihop.path_id == "R1"
    # 刻印は書かれるが、読込の判定には使わない（存在だけ確認する）。
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["schema_version"] == project.SCHEMA_VERSION
    assert raw["app_version"] and raw["saved_at"]


def test_optional_numbers_stay_none():
    """`freq_mhz` 等の `None` が `None` のまま（**共通設定の踏襲が崩れない**）。

    ⚠️ ここが 0.0 に化けると「共通設定を使う」が「0 MHz を指定した」に変わる。
    """
    got = project.from_dict(project.to_dict(_doc()))
    assert got.batch_rows is not None
    assert got.batch_rows[0].freq_mhz is None
    assert got.batch_rows[0].gain_tx  is None
    assert got.multihop is not None
    assert got.multihop.hop_rf[0].freq_mhz is None
    assert got.multihop.hop_rf[1].gain_rx  is None
    assert got.multihop.hop_rf[1].freq_mhz == 5600.0


def test_waypoint_heights_survive_and_derive_rows():
    """waypoint の高さが往復し、そこから `PathRow` を導出できる。

    **高さは地点にしかない**＝中継点 R1 の 40m が hop1 の `h_rx` と hop2 の
    `h_tx` の両方になる（二重入力ではなく 1 つの値の参照）。
    """
    got = project.from_dict(project.to_dict(_doc()))
    assert got.multihop is not None
    rows = mh.hop_rows(got.multihop)
    assert len(rows) == 2
    assert rows[0].h_rx == 40.0 and rows[1].h_tx == 40.0


# ------------------------------------------------------------
# 2. app キーを取り込まない
# ------------------------------------------------------------
def test_app_keys_are_never_loaded():
    """ファイルに theme/lang/proxy_url が混ざっていても params に入らない。

    **他人のプロジェクトを開いた瞬間に言語やプロキシが変わるのは事故。**
    """
    data = project.to_dict(_doc())
    data["params"] = dict(data["params"], theme="dark", lang="ja",
                          proxy_url="http://evil:8080")
    got = project.from_dict(data)
    for key in ("theme", "lang", "proxy_url"):
        assert key not in got.params
    assert got.params["freq"] == "2400.0"      # sim キーは通る


def test_app_keys_are_never_saved():
    """書く側でも app キーを落とす（app キー入りの .rsproj を作らない）。"""
    doc = _doc()
    doc.params = dict(doc.params, theme="dark", lang="ja")
    data = project.to_dict(doc)
    assert "theme" not in data["params"] and "lang" not in data["params"]


# ------------------------------------------------------------
# 3. 節の欠損＝「持たない」
# ------------------------------------------------------------
def test_missing_sections_are_none_not_empty():
    """節が無いファイルは `None`（＝呼び出し側はその窓に触らない）。

    ⚠️ ここを空リストにすると、UI 側が「空の窓を復元する」＝**バッチ窓を閉じた
    まま保存した人の行を消す**方向へ倒れる。
    """
    doc = project.from_dict({"schema_version": 1, "meta": {}, "params": {}})
    assert doc.batch_rows is None
    assert doc.scenario   is None
    assert doc.multihop   is None


def test_none_sections_are_not_written():
    """`None` の節はキーごと出さない（空の節を書くと上と区別できなくなる）。"""
    data = project.to_dict(project.ProjectDoc(meta={}, params={}))
    assert "batch" not in data and "scenario" not in data and "multihop" not in data


def test_empty_batch_rows_is_distinct_from_missing():
    """**空リストは「行が 0 本」で `None`（持たない）とは別物**。"""
    doc = project.ProjectDoc(batch_rows=[])
    got = project.from_dict(project.to_dict(doc))
    assert got.batch_rows == []


def test_unknown_keys_are_ignored():
    """未知キーは無視（新しい版が足したキーで古い版が落ちない）。"""
    data = project.to_dict(_doc())
    data["future_section"] = {"whatever": 1}
    data["meta"]["future_field"] = "x"
    got = project.from_dict(data)
    assert got.batch_rows is not None and len(got.batch_rows) == 2


# ------------------------------------------------------------
# 4. schema version
# ------------------------------------------------------------
def test_newer_schema_is_rejected():
    """自分より新しい版は拒否（新しいファイルを古いアプリで黙って壊さない）。"""
    data = project.to_dict(_doc())
    data["schema_version"] = project.SCHEMA_VERSION + 1
    with pytest.raises(project.ProjectError):
        project.from_dict(data)


# ------------------------------------------------------------
# 5. 我々のファイルでないもの
# ------------------------------------------------------------
@pytest.mark.parametrize("data", [
    {"freq": "2400.0", "h_tx": "30.0"},          # settings.json を誤って開いた
    {"schema_version": "1"},                     # 版が文字列
    {"schema_version": 0},
    [],
])
def test_non_project_files_are_rejected(data):
    with pytest.raises(project.ProjectError):
        project.from_dict(data)


def test_broken_json_is_wrapped(tmp_path):
    """壊れた JSON も `ProjectError` に畳む（生の英語例外を出さない）。"""
    path = tmp_path / "broken.rsproj"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(project.ProjectError):
        project.load(str(path))


def test_missing_file_is_wrapped(tmp_path):
    with pytest.raises(project.ProjectError):
        project.load(str(tmp_path / "nope.rsproj"))


def test_bad_number_is_wrapped():
    """数値であるべき欄が文字列でも `ProjectError`（生の ValueError を出さない）。"""
    data = project.to_dict(_doc())
    data["batch"]["rows"][0]["lat_tx"] = "北緯 34 度"
    with pytest.raises(project.ProjectError):
        project.from_dict(data)


# ------------------------------------------------------------
# 保存の失敗は握り潰さない（I-010 と同クラス）
# ------------------------------------------------------------
def test_save_failure_raises(tmp_path):
    """書けない場所への保存は例外を上げる（黙って「保存しました」にしない）。"""
    with pytest.raises(OSError):
        project.save(_doc(), str(tmp_path / "no_such_dir" / "p.rsproj"))


# ------------------------------------------------------------
# 既定ファイル名
# ------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("○○市 中継検討", "○○市 中継検討.rsproj"),
    ("a/b:c*d",        "abcd.rsproj"),
    ("",               "project.rsproj"),
    ("   ",            "project.rsproj"),
])
def test_default_filename(name, expected):
    """案件名は自由文字列＝ファイル名に使えない文字が来る。"""
    assert project.default_filename(name) == expected
