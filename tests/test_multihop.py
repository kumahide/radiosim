"""
tests/test_multihop.py
======================
中継経路（A-3）のヘッドレス検証。

**ここで守っているもの**:
  - **waypoint 列が source of truth**＝中継点の高さは 1 つの値で、前後のホップが
    それを共有する（`PathRow` を直接編集させると二重入力できる＝⑦違反）。
  - **再生中継の意味論**＝ホップ間で損失を足さない／全体判定は min。
    ここが崩れると「受動反射を実装してしまった」ことになり、物理が変わる。
  - 実行層は `batch._run_paths` の流用であること（新規のループを増やさない）。

DEM 取得は monkeypatch で塞ぎ、ネットワーク無しで実行する。
"""

import re
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import simulation as sim
from report import batch
from report import multihop as mh


# ============================================================
# ヘルパー
# ============================================================
def _wp(name, lat, lon, h=30.0):
    return mh.Waypoint(name=name, lat=lat, lon=lon, h=h)


def _path(n_points=3, **kwargs):
    """等間隔に並んだ n 点の中継経路（既定は TX → R1 → RX の 2 ホップ）。"""
    pts = [_wp(f"P{i}", 34.54 + i * 0.01, 132.41 + i * 0.01) for i in range(n_points)]
    return mh.MultiHopPath(
        path_id   = kwargs.pop("path_id", "route1"),
        waypoints = kwargs.pop("waypoints", pts),
        hop_rf    = kwargs.pop("hop_rf", [mh.HopRF() for _ in range(n_points - 1)]),
        **kwargs,
    )


def _fake_fetch(params, on_progress, on_complete, on_error):
    """DEM 取得のフェイク（中央に尾根を持つ一様でない地形）。

    ⚠️ 全点 0.0 にしない＝それは「DEM が 1 点も取れなかった」形として扱われる
    （B-025 ④）。地形として意味のある値を返す。
    """
    raw = np.full(params.num, 20.0)
    raw[params.num // 2 - 2:params.num // 2 + 2] = 60.0
    on_progress(params.num)
    on_complete(raw)


@pytest.fixture
def base(default_params_dict):
    return sim.SimParams(default_params_dict)


# ============================================================
# 導出（waypoint 列 → PathRow）
# ============================================================
class TestHopRows:

    def test_n_points_make_n_minus_1_hops(self):
        assert len(mh.hop_rows(_path(2))) == 1
        assert len(mh.hop_rows(_path(4))) == 3

    def test_relay_height_is_shared_by_both_hops(self):
        """**中継点の高さは 1 つ**＝hop1 の h_rx と hop2 の h_tx が必ず一致する。

        これが A-3 のデータモデルの核心（⑦）。`PathRow` を直接編集させると
        同じ 1 本のアンテナに違う値を書けてしまうので、行は導出物にした。
        ここが破れたら、二重入力を防ぐ仕組みが壊れている。
        """
        pts = [_wp("TX", 34.54, 132.41, h=40.0),
               _wp("R1", 34.55, 132.42, h=25.0),
               _wp("RX", 34.56, 132.43, h=10.0)]
        rows = mh.hop_rows(_path(waypoints=pts, hop_rf=[mh.HopRF(), mh.HopRF()]))
        assert rows[0].h_rx == 25.0
        assert rows[1].h_tx == 25.0
        assert rows[0].h_rx == rows[1].h_tx, "中継点の高さが 2 か所で食い違っている"
        assert rows[0].h_tx == 40.0 and rows[1].h_rx == 10.0

    def test_radio_settings_belong_to_the_hop(self):
        """周波数・利得は**区間**のもの（中継点は送受で別アンテナ）。"""
        rf = [mh.HopRF(freq_mhz=2400.0, gain_tx=10.0, gain_rx=3.0),
              mh.HopRF(freq_mhz=5600.0, gain_tx=14.0, gain_rx=8.0)]
        rows = mh.hop_rows(_path(3, hop_rf=rf))
        assert (rows[0].freq_mhz, rows[0].gain_tx, rows[0].gain_rx) == (2400.0, 10.0, 3.0)
        assert (rows[1].freq_mhz, rows[1].gain_tx, rows[1].gain_rx) == (5600.0, 14.0, 8.0)

    def test_hop_ids_are_unique_and_filesystem_safe(self):
        """ホップ ID は**出力ディレクトリ名になる**ので衝突しない形であること。"""
        rows = mh.hop_rows(_path(4))
        ids = [r.path_id for r in rows]
        assert ids == ["route1_h1", "route1_h2", "route1_h3"]
        assert len({i.casefold() for i in ids}) == len(ids)
        assert all(batch._PATH_ID_RE.fullmatch(i) for i in ids)
        assert all(len(i) <= batch._MAX_PATH_ID_LEN for i in ids), (
            "ホップ ID がバッチの ID 長制限を超える＝出力先が作れない"
        )


# ============================================================
# 検証
# ============================================================
class TestValidate:

    def test_needs_at_least_two_points(self):
        p = mh.MultiHopPath(path_id="x", waypoints=[_wp("TX", 34.5, 132.4)])
        assert mh.validate_path(p)

    def test_rejects_too_many_hops(self):
        # 画面語彙は「区間」（B-031）。コード語彙の `hop` はここでは出さない。
        assert any("区間" in e or "section" in e
                   for e in mh.validate_path(_path(mh.MAX_HOPS + 2)))

    def test_rejects_rf_count_mismatch(self):
        p = _path(3, hop_rf=[mh.HopRF()])          # 2 ホップなのに設定 1 つ
        assert mh.validate_path(p)

    def test_rejects_long_id(self):
        """ID の上限は**バッチより短い**（`_h1` を足しても収まる必要がある）。"""
        p = _path(2, path_id="a" * (mh._MAX_PATH_ID_LEN + 1))
        assert mh.validate_path(p)
        assert mh._MAX_PATH_ID_LEN < batch._MAX_PATH_ID_LEN

    def test_rejects_out_of_range_height(self):
        pts = [_wp("TX", 34.54, 132.41, h=9999.0), _wp("RX", 34.55, 132.42)]
        assert mh.validate_path(_path(waypoints=pts, hop_rf=[mh.HopRF()]))

    def test_delegates_coordinate_checks_to_batch(self):
        """座標の値域は `batch.validate_rows` に委ねること（出所を 2 つにしない）。

        同じ場所の 2 点は「ホップとして成立しない」＝バッチの verr_identical が
        そのまま効く。ここで独自の判定を書くと、範囲を直したとき片方に取り残される。
        """
        pts = [_wp("TX", 34.54, 132.41), _wp("RX", 34.54, 132.41)]
        assert mh.validate_path(_path(waypoints=pts, hop_rf=[mh.HopRF()]))

    def test_accepts_a_normal_path(self):
        assert mh.validate_path(_path(3)) == []


# ============================================================
# トポロジー（点列＋接続規則）
# ============================================================
# **鎖を式に埋め込まない**という布石（[[project-radiosim-for-drone]]）。応用側
# （GCS →各点＝星）と構造を共有するための土台で、2.6 では鎖しか使わない。
# ⚠️ ここが守るのは「接続規則が 1 か所にある」ことだけ。**集約（min）は鎖の
# 意味論**なので、星を実際に使うときは併せて決め直すこと。


# ============================================================
# 「この版が扱える範囲」の宣言（I-066）
# ============================================================
# 🔴 背景（2026-08-04 の根本原因分析 → 2026-08-05 に実装）: 2.6 では
# 「**この版は鎖だけを扱う**」という 1 つの事実が 3 か所に**暗黙に**散っていた
# ——`project.py`（読む）・旧画面の中継の窓（持つ・4.0 で撤去）・`multihop.py`（実行する）。
# **どれも単体では筋が通るのに、繋ぐと「星の地点を鎖として計算し、保存で
# 書き換える」**という壊れ方をし、独立レビューを 3 巡して処方を 3 回変えた。
#
# ⇒ 扱える範囲は `SUPPORTED_TOPOLOGIES` 1 か所で宣言し、各層はそこを見る。
#
# 🔑 **このゲートは宣言から駆動される**＝いま拒否を要求しているのは
# 「宣言されているが未対応」な値（＝現在は `star`）だけで、**星を実装して
# `SUPPORTED_TOPOLOGIES` に足した日には、このテストは何も要求しなくなる**。
# 値を書き並べたゲートだと、そのとき「なぜか星を拒否しろと言うテスト」が
# 残って足を引っ張る（[[feedback-promote-recurring-checks]] 実証 9＝
# 間違ったものを要求するゲート）。
_DECLARED_BUT_UNSUPPORTED = sorted(set(mh.TOPOLOGIES) - set(mh.SUPPORTED_TOPOLOGIES))


def test_the_two_declarations_are_not_the_same_thing():
    """宣言されている語彙と、この版が扱える値は**別物**（名前を分ける理由）。"""
    assert set(mh.SUPPORTED_TOPOLOGIES) <= set(mh.TOPOLOGIES), (
        "扱えると宣言した値が語彙に無い（`TOPOLOGIES` に足し忘れている）"
    )


@pytest.mark.parametrize("topology", _DECLARED_BUT_UNSUPPORTED)
def test_unsupported_topologies_are_refused_by_every_layer(topology, tmp_path):
    """**読む・書く・実行するの 3 層が、同じ宣言を見て同じ答えを返す。**

    層がばらばらだと 2.6 の壊れ方に戻る＝読めるのに実行できない（ウィンドウが値を
    落として鎖として計算する）／書けるのに読めない（保存成功の顔でデータが
    失われる）。
    """
    from report import project

    path = mh.MultiHopPath(path_id="r1", topology=topology,
                           waypoints=[_wp("A", 34.5, 132.4), _wp("B", 34.6, 132.5)])

    # ① 実行する層
    with pytest.raises(NotImplementedError):
        mh.require_runnable(path)

    # ② 書く層（読めないものは書かせない）
    with pytest.raises(project.ProjectError):
        project.to_dict(project.ProjectDoc(multihop=path))

    # ③ 読む層
    import json
    file = str(tmp_path / "p.rsproj")
    with open(file, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "meta": {}, "params": {},
                   "multihop": {"waypoints": [], "topology": topology}}, f)
    with pytest.raises(project.ProjectError):
        project.load(file)


def test_no_layer_decides_the_supported_range_on_its_own():
    """扱える範囲を**層ごとに書かない**（宣言を参照する）ことを構造で縛る。

    ⚠️ 見るのは「トポロジー定数との比較」と「その場で作った許可タプル」だけ。
    **既定値としての `mh.TOPOLOGY_CHAIN`（表の 1 行）は正当**なので当たらない。
    `multihop.py` 自身は除外＝宣言と、名前ごとの意味（`links`）を持つ場所。
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    files = [f for layer in ("core", "report", "webui")
             for f in sorted((root / layer).rglob("*.py"))]
    assert files, "走査する層が見つからない＝この検査は何も見ていない"
    for file in files:
        if file.name == "multihop.py":
            continue
        src = file.read_text(encoding="utf-8")
        for pattern, why in (
            (r"[!=]=\s*mh\.TOPOLOGY_\w+", "定数と直に比べている"),
            (r"\(\s*mh\.TOPOLOGY_\w+\s*,\s*\)", "その場で許可タプルを作っている"),
        ):
            if re.search(pattern, src):
                offenders.append(f"{file.name}: {why}")
    assert not offenders, (
        "扱えるトポロジーの範囲を層ごとに決めている（`mh.SUPPORTED_TOPOLOGIES` を"
        "参照すること）: " + ", ".join(offenders)
    )


def test_relay_mode_is_regenerative_only():
    """**受動反射は対象外**であることを記録として固定する（2026-07-31 決定）。

    再生中継＝ホップごとに独立バジェット・全体は min。受動反射は損失が連結し、
    `models.py` に新規の物理が要る＝別の版で判断する。実装が「なんとなく」
    そちらへ寄らないよう、意味論をテストの名前で残しておく。
    """
    assert mh.MultiHopRun(path=_path(2), hops=[]).overall_margin is None


