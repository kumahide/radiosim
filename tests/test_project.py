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
import re
import sys

from pathlib import Path

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


def test_topology_round_trips_and_defaults_to_chain():
    """接続規則（トポロジー）が往復し、**古いファイルは鎖として読める**こと。

    2.6 は鎖しか使わないが、ファイルに書いておかないと「星を使う版」が出たとき
    に古いファイルの意味が変わる（未指定＝その版の既定、になってしまう）。
    """
    doc = _doc()
    assert doc.multihop is not None
    doc.multihop.topology = mh.TOPOLOGY_STAR
    got = project.from_dict(project.to_dict(doc))
    assert got.multihop is not None
    assert got.multihop.topology == mh.TOPOLOGY_STAR

    data = project.to_dict(_doc())
    del data["multihop"]["topology"]          # 2.6a8 以前が書いたファイル相当
    old = project.from_dict(data)
    assert old.multihop is not None
    assert old.multihop.topology == mh.TOPOLOGY_CHAIN


def test_unknown_keys_are_ignored():
    """未知キーは無視（新しい版が足したキーで古い版が落ちない）。"""
    data = project.to_dict(_doc())
    data["future_section"] = {"whatever": 1}
    data["meta"]["future_field"] = "x"
    got = project.from_dict(data)
    assert got.batch_rows is not None and len(got.batch_rows) == 2


# ------------------------------------------------------------
# 3b. 書けたファイルは必ず正しい JSON（NaN を書かない）
# ------------------------------------------------------------
# バッチの表は入力途中を許すため、読めない欄を NaN のまま持つ（`_read_table_rows`）。
# それを素の json.dump で書くと `NaN` リテラルが混ざり、**規格外の JSON**（他の
# ツールが読めないファイル）が黙って出来上がる。⇒ 保存側で弾き、UI は保存前に
# `unreadable_row` で気づいて**その節だけ保存しない**（警告つき）。

def test_unreadable_row_finds_non_finite_values():
    rows = [batch.PathRow(path_id="ok", lat_tx=34.5, lon_tx=132.4,
                          lat_rx=34.6, lon_rx=132.5, h_tx=30.0, h_rx=10.0),
            batch.PathRow(path_id="bad", lat_tx=34.5, lon_tx=132.4,
                          lat_rx=34.6, lon_rx=132.5,
                          h_tx=float("nan"), h_rx=10.0)]
    assert project.unreadable_row(rows) is rows[1]
    assert project.unreadable_row(rows[:1]) is None
    assert project.unreadable_row(None) is None
    # 任意欄（空欄＝共通設定の踏襲）の None は「読めない値」ではない。
    rows[1].h_tx = 30.0
    assert project.unreadable_row(rows) is None


def test_save_refuses_to_write_non_finite_numbers(tmp_path):
    doc = project.ProjectDoc(batch_rows=[
        batch.PathRow(path_id="bad", lat_tx=float("inf"), lon_tx=132.4,
                      lat_rx=34.6, lon_rx=132.5, h_tx=30.0, h_rx=10.0)])
    with pytest.raises(ValueError):
        project.save(doc, str(tmp_path / "p.rsproj"))


def _big_doc(n: int = 200) -> project.ProjectDoc:
    """1 回の書き込みでバッファに収まらない大きさのプロジェクト。

    ⚠️ **小さいと再現しない**＝全体がバッファに収まると、失敗しても未 flush の
    ままなのでファイルが無傷に「見える」。最初の実測でそれに騙されかけた。
    """
    return project.ProjectDoc(
        meta={"project_name": "大事な案件"},
        batch_rows=[batch.PathRow(path_id=f"p{i:03d}", lat_tx=34.5, lon_tx=132.4,
                                  lat_rx=34.6, lon_rx=132.5, h_tx=30.0, h_rx=10.0)
                    for i in range(n)])


def test_save_failure_leaves_the_existing_file_intact(tmp_path):
    """**保存に失敗しても、前のプロジェクトが壊れないこと。**

    背景（2026-08-03・独立レビュー Codex）: 以前は保存先を直接 `"w"` で開いており、
    **open した瞬間に既存が 0 バイトへ切り詰められて**いた。途中で失敗すると
    読めない残骸だけが残る（実測＝56,384 → 42,473 バイトの壊れた JSON）。
    **条件探索・中継経路にとって `.rsproj` は唯一の永続化手段**なので、
    「保存は失敗したが前のファイルは無事」を成立させる必要がある。

    ⚠️ **引き金は NaN だけではない**（ディスク不足・I/O エラーでも同じ）。ここでは
    再現しやすい NaN で代表させるが、守っているのは**書き込み全般**である。
    """
    path = str(tmp_path / "p.rsproj")
    project.save(_big_doc(), path)
    before = open(path, encoding="utf-8").read()
    assert len(before) > 8192, "テストの前提（バッファに収まらない大きさ）が崩れている"

    doomed = _big_doc()
    doomed.batch_rows[150].h_tx = float("nan")     # 途中で失敗させる
    with pytest.raises(ValueError):
        project.save(doomed, path)

    assert open(path, encoding="utf-8").read() == before, \
        "保存に失敗したのに既存ファイルが変わっている（原子的でない）"
    json.loads(before)                              # 壊れていないことも明示的に見る
    assert project.load(path).batch_rows[150].h_tx == 30.0, "前の内容が読み戻せない"


def test_save_leaves_no_temporary_file_behind(tmp_path):
    """成功・失敗のどちらでも一時ファイルを残さないこと。"""
    path = str(tmp_path / "p.rsproj")
    project.save(_big_doc(), path)
    assert os.listdir(tmp_path) == ["p.rsproj"], "成功時に一時ファイルが残っている"

    doomed = _big_doc()
    doomed.batch_rows[150].h_tx = float("nan")
    with pytest.raises(ValueError):
        project.save(doomed, path)
    assert os.listdir(tmp_path) == ["p.rsproj"], "失敗時に一時ファイルが残っている"


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


# ------------------------------------------------------------
# 型ガード＝「キーが無い」と「キーはあるが型が違う」を分ける
# ------------------------------------------------------------
# 背景（2026-08-03/04・独立レビュー Codex を 3 巡）: この層の契約は「**壊れた
# ファイルは ProjectError 一種類に畳む**」。ところが `data.get(key)` は
# **キー欠損と明示的な `null` を区別できない**ので、壊れた節が「その節は無い」
# として**読めてしまって**いた。
#
# 🔴 **読めてしまうことが危険**＝利用者は気づかず、**そのまま上書き保存すると
# 壊れていた節の中身が消える**（原子的保存で直したデータ損失と同じクラス）。
#
# 🔑 **線引きの根拠＝`to_dict` が実際に書く形**。節が None ならキーごと出さず、
# 内側は必ず list / dict。⇒ **`null` や裸の配列は我々が書かない**＝壊れている。
# 「欠損は既定値」の緩さは**キーが無いときにだけ**与える。
#
# ⚠️ **ここが無いまま 2 巡した**（実装だけ直してテストを書かなかった）。
# 実装を戻しても誰も気づかない状態を 2 回作ったので、ここで固定する。
_BASE_DOC = {"schema_version": 1, "meta": {}, "params": {}}

# 「壊れている」と言い切るべき入力（節・入れ子・要素の 3 段すべて）
_BROKEN = [
    ("節が null",             {"multihop": None}),
    ("節が配列",              {"scenario": []}),
    ("節が数値",              {"batch": 5}),
    ("節が文字列",            {"multihop": "x"}),
    ("入れ子が null",         {"batch": {"rows": None}}),
    ("入れ子が null(compare)", {"scenario": {"compare": None}}),
    ("入れ子が数値",          {"scenario": {"compare": 5}}),
    ("入れ子が配列(sweep)",   {"scenario": {"sweep": []}}),
    ("入れ子が null(hop_rf)", {"multihop": {"waypoints": [], "hop_rf": None}}),
    ("要素が数値",            {"scenario": {"compare": [5]}}),
    ("要素が文字列",          {"multihop": {"waypoints": ["x"]}}),
    ("meta が配列",           {"meta": []}),
    ("params が null",        {"params": None}),
]


@pytest.mark.parametrize("label,fragment", _BROKEN, ids=[x[0] for x in _BROKEN])
def test_broken_shapes_are_project_errors(tmp_path, label, fragment):
    """壊れた形は **`ProjectError` 一種類**に畳まれること（生の例外を漏らさない）。"""
    path = str(tmp_path / "p.rsproj")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**_BASE_DOC, **fragment}, f)
    with pytest.raises(project.ProjectError):
        project.load(path)


# 「キーが無い」だけは既定値で読めること（後方互換＝古いファイル・部分的なファイル）
_ABSENT = [
    ("節が全て無い",     {}),
    ("batch だけ",       {"batch": {"rows": []}}),
    ("rows キーが無い",  {"batch": {}}),
    ("compare キーが無い", {"scenario": {"mode": "compare"}}),
    ("meta キーが無い",  {"__drop__": "meta"}),
]


@pytest.mark.parametrize("label,fragment", _ABSENT, ids=[x[0] for x in _ABSENT])
def test_absent_keys_fall_back_to_defaults(tmp_path, label, fragment):
    """**キーが無い**のは壊れていない＝既定値で読める（`null` とは扱いが違う）。"""
    data = {**_BASE_DOC}
    drop = fragment.pop("__drop__", None)
    if drop:
        data.pop(drop)
    data.update(fragment)
    path = str(tmp_path / "p.rsproj")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    project.load(path)          # 例外が出ないことが仕様


def test_saved_file_never_contains_null_for_known_keys():
    """**書く側が `null` を出さない**ことを固定する（上の線引きの根拠そのもの）。

    ここが崩れると「`null` は壊れている」という判断の前提が消え、
    自分が書いたファイルを自分で拒否するようになる。
    """
    doc = project.ProjectDoc(
        meta={"project_name": "x"}, params={"freq_mhz": "2400"},
        batch_rows=[], scenario=project.ScenarioSpec(), multihop=None)
    data = project.to_dict(doc)
    assert "multihop" not in data, "節が無いときはキーごと出さない（null を書かない）"
    for key in ("meta", "params", "batch", "scenario"):
        assert data.get(key) is not None, f"{key} に null を書いている"
    assert isinstance(data["batch"]["rows"], list)
    assert isinstance(data["scenario"]["compare"], list)
    assert isinstance(data["scenario"]["sweep"], dict)


# ------------------------------------------------------------
# スカラー項目の型検査（4 巡目の指摘）
# ------------------------------------------------------------
# 🔴 背景（2026-08-04・独立レビュー Codex 4 巡目）: 3 巡目で厳密化したのは
# **dict / list の構造項目だけ**で、文字列項目は `str(...)` を通していた。
# ⇒ `"mode": []` が `"compare"` に、`"path_id": null` が**文字列 "None"** に
# 黙って化ける。**壊れた値を変換して受け入れ、そのまま再保存できる**ので、
# 構造項目で塞いだのと同じ穴がスカラー側に残っていた。
#
# ⚠️ **`null` を一律に破損とはできない**＝`to_dict` は **hop_rf の RF 値には
# `null` を書く**（空欄＝共通設定の踏襲という仕様）。**書く側が実際に書く形**で
# フィールドごとに線を引く（構造項目のときと同じ判断基準）。
_BROKEN_SCALARS = [
    ("mode が配列",       {"scenario": {"mode": []}}),
    ("mode が dict",      {"scenario": {"mode": {}}}),
    ("mode が null",      {"scenario": {"mode": None}}),
    ("path_id が null",   {"multihop": {"waypoints": [], "path_id": None}}),
    ("path_id が配列",    {"multihop": {"waypoints": [], "path_id": []}}),
    ("note が dict",      {"multihop": {"waypoints": [], "note": {}}}),
    ("topology が null",  {"multihop": {"waypoints": [], "topology": None}}),
    ("行の path_id が配列",
     {"batch": {"rows": [{"path_id": [], "lat_tx": 34.5, "lon_tx": 132.4,
                          "lat_rx": 34.6, "lon_rx": 132.5,
                          "h_tx": 30.0, "h_rx": 10.0}]}}),
]


@pytest.mark.parametrize("label,fragment", _BROKEN_SCALARS,
                         ids=[x[0] for x in _BROKEN_SCALARS])
def test_broken_scalars_are_project_errors(tmp_path, label, fragment):
    """**壊れたスカラーを黙って文字列へ変換しない**こと。"""
    path = str(tmp_path / "p.rsproj")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**_BASE_DOC, **fragment}, f)
    with pytest.raises(project.ProjectError):
        project.load(path)


def test_optional_rf_null_is_still_accepted(tmp_path):
    """⚠️ **RF の `null` は「空欄＝共通設定を踏襲」で、書く側が実際に書く形**。

    ここが落ちるようになったら、**自分で書いたファイルを自分で拒否している**。
    """
    path = str(tmp_path / "p.rsproj")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**_BASE_DOC, "multihop": {
            "waypoints": [], "path_id": "r1",
            "hop_rf": [{"freq_mhz": None, "gain_tx": None, "gain_rx": None}]}}, f)
    doc = project.load(path)
    assert doc.multihop is not None
    assert doc.multihop.hop_rf[0].freq_mhz is None


def test_scalar_numbers_are_accepted_as_text(tmp_path):
    """数値で書かれた文字列項目は受ける（情報が失われない・手書き救済）。"""
    path = str(tmp_path / "p.rsproj")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**_BASE_DOC, "multihop": {"waypoints": [], "path_id": 1}}, f)
    assert project.load(path).multihop.path_id == "1"


def test_scalar_roundtrip_survives_save_and_load(tmp_path):
    """自分で書いたファイルは必ず読み戻せる（厳密化のたびに確かめる）。"""
    doc = project.ProjectDoc(
        meta={"project_name": "案件"}, params={"freq_mhz": "2400"},
        batch_rows=[batch.PathRow(path_id="p01", lat_tx=34.5, lon_tx=132.4,
                                  lat_rx=34.6, lon_rx=132.5, h_tx=30.0, h_rx=10.0)],
        scenario=project.ScenarioSpec(mode="sweep"),
        multihop=mh.MultiHopPath(
            path_id="r1",
            waypoints=[mh.Waypoint(name="TX", lat=34.5, lon=132.4, h=30.0)],
            hop_rf=[mh.HopRF(freq_mhz=None, gain_tx=None, gain_rx=None)]))
    path = str(tmp_path / "p.rsproj")
    project.save(doc, path)
    back = project.load(path)
    assert back.scenario.mode == "sweep"
    assert back.multihop.path_id == "r1"
    assert back.batch_rows[0].path_id == "p01"


# ------------------------------------------------------------
# 列挙値とマップの値（5 巡目の指摘＝**変換の全経路を洗い出して閉じる**）
# ------------------------------------------------------------
# 🔴 背景（2026-08-04・独立レビュー Codex 5 巡目）: 4 巡目で入れた `_text` は
# **全文字列項目で数値を救済**していた。自由文字列（`path_id` / `note`）なら
# 妥当だが、**列挙項目に効かせると意味が変わる**＝`"mode": 1` が `"1"` になり、
# 直後の「知らない値なら compare」で**黙って `compare` に化ける**。
# `"topology": 1` は `"1"` として通り、**後段の集約で例外**になり得る。
#
# 併せて `_str_map` は値を無条件に文字列化していたので、
# `compare[].h_tx: null` → `""`（**条件指定が消える**）、`params.start: []` →
# `"[]"` が通っていた。
#
# 🔑 **今回は指摘の 2 件だけでなく `from_dict` の変換を全部洗い出した**（毎巡
# 1 件ずつ潰す形を終わらせるため）。残っていた緩い経路は**列挙 2 つとマップ 4 つ
# だけ**で、他（節・入れ子・要素・スカラー・数値）は既に閉じている。
_BROKEN_ENUMS = [
    ("mode が数値",       {"scenario": {"mode": 1}}),
    ("mode が未知の文字列", {"scenario": {"mode": "xyz"}}),
    ("topology が数値",   {"multihop": {"waypoints": [], "topology": 1}}),
    ("topology が真偽値", {"multihop": {"waypoints": [], "topology": True}}),
]

_BROKEN_MAP_VALUES = [
    ("compare の値が null", {"scenario": {"compare": [{"h_tx": None}]}}),
    ("compare の値が配列",  {"scenario": {"compare": [{"h_tx": []}]}}),
    ("sweep の値が null",   {"scenario": {"sweep": {"axis": None}}}),
    ("params の値が配列",   {"params": {"start": []}}),
    ("params の値が dict",  {"params": {"start": {}}}),
    ("meta の値が dict",    {"meta": {"project_name": {}}}),
]


@pytest.mark.parametrize("label,fragment", _BROKEN_ENUMS + _BROKEN_MAP_VALUES,
                         ids=[x[0] for x in _BROKEN_ENUMS + _BROKEN_MAP_VALUES])
def test_broken_enums_and_map_values_are_project_errors(tmp_path, label, fragment):
    """列挙項目とマップの値も、壊れていれば `ProjectError` に畳むこと。"""
    path = str(tmp_path / "p.rsproj")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**_BASE_DOC, **fragment}, f)
    with pytest.raises(project.ProjectError):
        project.load(path)


def test_unknown_topology_string_is_still_accepted(tmp_path):
    """⚠️ **`topology` は意図的に開いた集合**（未知の文字列は鎖として扱う）。

    後から星型を足した版のファイルを、古いアプリが**黙って壊さない**ための約束
    （意味づけは `multihop.links` の 1 か所）。ここが落ちるようになったら、
    前方互換の設計判断を壊している。**文字列であることだけ**を要求する。
    """
    path = str(tmp_path / "p.rsproj")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**_BASE_DOC,
                   "multihop": {"waypoints": [], "topology": "star"}}, f)
    assert project.load(path).multihop.topology == "star"


def test_map_values_written_as_numbers_are_accepted(tmp_path):
    """手書きの `"freq": 2400`（数値）は受ける（情報が失われない）。

    ⚠️ キーは **sim の実キー**（`config.DEFAULT_CONFIG`）でないと `select_sim` に
    落とされる＝このテスト自身が最初それで落ちた。
    """
    path = str(tmp_path / "p.rsproj")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**_BASE_DOC, "params": {"freq": 2400}}, f)
    assert project.load(path).params["freq"] == "2400"


def test_reader_never_converts_values_with_bare_str():
    """**読む側の変換は必ずガード付きヘルパーを通る**ことを構造で縛る。

    🔴 なぜ個別のケースだけでなくクラスで塞ぐか（2026-08-04・独立レビュー Codex を
    5 巡）: `null` → 節が消える／`[5]` → 要素が消える／`"None"` → 文字列に化ける／
    `mode: 1` → 別の相に化ける……と、**毎巡「同じ型の穴が別の場所で」出続けた**。
    どれも正体は 1 つ＝**読む側で素の `str(...)` を使うと、壊れた値が黙って
    正しそうな値に化ける**。⇒ 事例を 1 つずつ潰すのをやめ、**手口ごと禁じる**
    （[[feedback-promote-recurring-checks]] 実証10＝列挙で塞ぐ穴は名前 1 つで開く）。

    ⚠️ ここが落ちたら、`str(...)` を足したのが悪いのではなく**ガード付きの
    ヘルパー（`_text` / `_name` / `_enum` / `_num` / `_read_map` …）を増やすべき**
    という合図。
    """
    src = (Path(project.__file__)).read_text(encoding="utf-8")
    for func in ("from_dict", "_row_from_dict"):
        m = re.search(rf"\ndef {func}\b.*?(?=\ndef |\Z)", src, re.S)
        assert m, f"project.py に {func} が見つからない（このゲートが空振りする）"
        body = m.group(0)
        # ⚠️ **語境界で見る**＝部分一致だと `mh.Waypoint(` の "int(" に当たって
        # 毎回鳴る（このゲート自身が最初それで誤検知した＝壊れ方②）。
        assert not re.search(r"(?<![\w.])str\(", body), (
            f"{func} が素の `str(...)` で値を変換している。"
            "壊れた値が黙って文字列に化けるので、ガード付きヘルパーを使うこと"
            "（`_text` / `_name` / `_enum` / `_read_map`）。"
        )
        assert not re.search(r"(?<![\w.])(float|int)\(", body), (
            f"{func} が素の数値変換を使っている（`_num` / `_opt_num` を使うこと）"
        )
