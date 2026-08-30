"""
tests/test_multihop.py
======================
中継経路（A-3）のヘッドレス検証。

**ここで守っているもの**:
  - **waypoint 列が source of truth**＝中継点の高さは 1 つの値で、前後のホップが
    それを共有する（`PathRow` を直接編集させると二重入力できる＝⑦違反）。
  - **再生中継の意味論**＝ホップ間で損失を足さない／全体判定は min。
    ここが崩れると「受動反射を実装してしまった」ことになり、物理が変わる。
  - 実行層は `batch._process_one` の流用であること（新規のループを増やさない）。

DEM 取得は monkeypatch で塞ぎ、ネットワーク無しで実行する。
"""

import csv
import re
import os
import sys
import threading

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import config
from core import coords
from core import i18n
from core import simulation as sim
from report import batch
from core import units
from report import multihop as mh
from report import report_summary


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


def _run(path, base_params, tmp_path, monkeypatch, stub_visuals=True, **kwargs):
    """run_multihop を同期的に回して MultiHopRun を返す。

    `stub_visuals=False` で**本物の成果物生成を通す**（描画の失敗が結果に出るか
    を見るテスト＝I-010 はこちらを使う。既定は塞ぐ＝別テストの担当）。
    """
    monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)
    monkeypatch.setattr(sim, "_terrain_cache", {})
    monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path))
    if stub_visuals:
        monkeypatch.setattr("report.report_path.save_path_visuals", lambda *a, **k: None)
    monkeypatch.setattr(report_summary, "render_summary_map_b64", lambda r: None)

    out: list = []
    err: list = []
    done = threading.Event()
    mh.run_multihop(
        path, base_params,
        on_hop_start    = lambda i, n, pid: None,
        on_hop_progress = lambda done, tot: None,
        on_hop_complete = lambda i, n, pr: None,
        on_complete     = lambda run: (out.append(run), done.set()),
        on_error        = lambda ex: (err.append(ex), done.set()),
        **kwargs,
    )
    assert done.wait(timeout=60), "run_multihop が完了しない"
    if err:
        raise err[0]
    return out[0]


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
# 実行・集約（再生中継の意味論）
# ============================================================
class TestRunMultihop:

    def test_runs_every_hop(self, base, tmp_path, monkeypatch):
        run = _run(_path(4), base, tmp_path, monkeypatch)
        assert len(run.hops) == 3
        assert all(h.result is not None for h in run.hops)

    def test_losses_are_not_chained_between_hops(self, base, tmp_path, monkeypatch):
        """**ホップ間で損失を足さない**（再生中継＝2026-07-31 決定の核心）。

        各ホップは自分の送信電力から始まる独立バジェットなので、hop2 の EIRP は
        hop1 の損失に影響されない。ここが崩れていたら、実装したのは再生中継では
        なく**受動反射**（損失が連結する＝新規の物理が要る別物）。
        """
        run = _run(_path(3), base, tmp_path, monkeypatch)
        eirps = [h.result.eirp for h in run.hops]
        assert eirps[0] == pytest.approx(eirps[1]), (
            f"ホップごとに EIRP が変わっている（損失が連結している）: {eirps}"
        )
        # 受信レベルも「前ホップの受信レベルから始まる」形になっていないこと。
        assert run.hops[1].result.p_rx > run.hops[0].result.p_rx - 200

    def test_overall_margin_is_the_minimum(self, base, tmp_path, monkeypatch):
        """全体のマージン＝ホップ別の**最小値**（鎖は最も弱い輪で切れる）。"""
        run = _run(_path(4), base, tmp_path, monkeypatch)
        margins = [h.result.actual_margin for h in run.hops]
        assert run.overall_margin == pytest.approx(min(margins))
        assert run.worst is run.hops[margins.index(min(margins))]

    def test_overall_is_ng_when_any_hop_is_ng(self, base, tmp_path, monkeypatch):
        """1 ホップでも NG なら全体は NG。"""
        run = _run(_path(3), base, tmp_path, monkeypatch)
        assert run.ok == all(h.result.status == "OK" for h in run.hops)
        run.hops[0].result.status = "NG"
        assert not run.ok

    def test_a_failed_hop_makes_the_overall_unspeakable(self, base, tmp_path,
                                                        monkeypatch):
        """失敗したホップがあれば全体マージンは**語らない**（None）。

        数値が無いホップを飛ばして min を取ると、「一番苦しい区間を無視した
        楽観的な全体像」を出すことになる。
        """
        run = _run(_path(3), base, tmp_path, monkeypatch)
        run.hops[1].result = None
        assert run.overall_margin is None
        assert run.worst is run.hops[1]
        assert not run.ok

    def test_artifact_failure_is_not_a_healthy_number(self, base, tmp_path,
                                                      monkeypatch):
        """**成果物だけ失敗した区間**も、集約では失敗として扱うこと（I-010 ③）。

        🔴 2.7RC1 の独立レビューで見つかった食い違い＝`ok` は `status` を見て
        False を返すのに、`worst` / `overall_margin` は `result is None` しか
        失敗と見ておらず、**成果物が欠けた区間の RF 値を健全な値として比較に
        混ぜて**いた。その結果、全区間の余裕が正でも `ok` が False になり、
        `overall_display` が **「最大不足 −20.0 dB」という負の不足量**を出した
        （二重否定で読めない字＝I-052 が避けたかったものそのもの）。

        ⇒ 判定の出所は `status` の 1 か所（`ok` と同じ）に揃える。**語れないもの
        は「—」と言う**——間違った量を言うより安い。
        """
        run = _run(_path(3), base, tmp_path, monkeypatch)
        for h in run.hops:                       # 前提: 電波的にはどちらも成立
            h.result.actual_margin = 20.0
            h.result.status = "OK"
        run.hops[0].artifact_error = RuntimeError("仕組んだ描画失敗")

        assert not run.ok, "前提: 成果物が欠けた区間があれば全体は成功ではない"
        assert run.worst is run.hops[0], "正常な区間を最悪と呼んでいる"
        assert run.overall_margin is None, "語れないはずの全体余裕を語っている"
        key, text = mh.overall_display(run)
        assert key == mh.OVERALL_MARGIN_KEY and text == "—", (
            f"判定不能なのに不足量を出した: {key} / {text}"
        )

    def test_hops_csv_does_not_bypass_the_single_verdict(self, base, tmp_path,
                                                         monkeypatch):
        """`hops.csv` の判定と理由が、画面・HTML と食い違わないこと（I-010 ③）。

        🔴 同上の独立レビューで発見＝この 1 列だけ `pr.result.status` を直に
        読んでおり、**画面と HTML が ERROR の行を CSV だけ OK** と書いていた。
        理由欄も `pr.error` しか見ないので空のままで、CSV を読んだ人には
        「成果物が欠けた」ことを知る手段が無かった。
        """
        run = _run(_path(3), base, tmp_path, monkeypatch)
        run.hops[0].artifact_error = RuntimeError("仕組んだ描画失敗")
        mh._write_hops_csv(run, run.save_dir)

        with open(os.path.join(run.save_dir, "hops.csv"), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["status"] == run.hops[0].status == "ERROR", (
            "CSV の判定が画面と食い違っている（成果物の失敗が素通りしている）"
        )
        assert "仕組んだ描画失敗" in rows[0]["error"], "ERROR の理由欄が空"
        # 数値は残す（計算は通っているので消す理由が無い＝summary.csv と同じ約束）
        assert rows[0]["margin_db"] != ""

    def test_missing_artifacts_are_not_linked(self, base, tmp_path, monkeypatch):
        """成果物が無い区間から `report.html` へリンクしないこと。

        🔴 同上＝サムネイルだけ抑止しており、**区間名のリンクが生き残って**いた。
        成果物は `save_path_visuals` が一括で失敗するので、PNG が無い区間は
        `report.html` も無い（連結文書側も `sheet_html` が空で落ちるためアンカー
        先が無い）＝**どちらの形でもリンク切れ**になる。
        """
        from report import report_multihop

        run = _run(_path(3), base, tmp_path, monkeypatch)
        run.hops[0].artifact_error = RuntimeError("仕組んだ描画失敗")
        pid = run.hops[0].row.path_id

        for anchor_links in (False, True):
            html = report_multihop.route_sheet_html(run, anchor_links=anchor_links)
            href = f"#{pid}" if anchor_links else f"{pid}/report.html"
            assert f"href='{href}'" not in html, (
                f"成果物の無い区間へリンクしている（anchor_links={anchor_links}）"
            )
        # 健全な区間のリンクまで消していないこと（過剰な抑止も欠陥）
        html = report_multihop.route_sheet_html(run)
        assert f"{run.hops[1].row.path_id}/report.html" in html

    def test_writes_hops_csv_with_group_columns(self, base, tmp_path, monkeypatch):
        """`hops.csv` が「1 行 = 1 ホップ」で group_id / hop_index を持つこと。

        バッチの `summary.csv`（1 行 = 1 回線）とは**別ファイル**にしてある
        （2026-08-01 決定）＝あちらの出力契約を壊さないため。
        """
        run = _run(_path(3), base, tmp_path, monkeypatch)
        with open(os.path.join(run.save_dir, "hops.csv"), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert [r["hop_index"] for r in rows] == ["1", "2"]
        assert {r["group_id"] for r in rows} == {"route1"}
        assert rows[0]["from"] == "P0" and rows[0]["to"] == "P1"
        assert rows[0]["status"] in ("OK", "NG")

    def test_hops_csv_numbers_match_the_screen(self, base, tmp_path, monkeypatch):
        """`hops.csv` の**数値列が画面と同じ値**であること（B-060）。

        ⚠️ **行数・識別子・判定だけを見ていたので、100 倍の誤りが素通りしていた**
        （`f1_pct` に 3381.3＝画面の 33.8% の 100 倍が出ていた。`blocked_ratio` は
        models の時点で既に % なのに、書き出しでもう一度 100 倍していた）。
        ⇒ **整形は `units` が単一ソース**。CSV はその出力と一致していること。

        🔑 **「列が在る」ではなく「値が合っている」を見る**＝出力契約の検査は、
        形だけ見ると数字の意味が壊れても緑になる。
        """
        run = _run(_path(3), base, tmp_path, monkeypatch)
        with open(os.path.join(run.save_dir, "hops.csv"), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        for row, hop in zip(rows, run.hops):
            r = hop.result
            assert r is not None, "前提: 結果のあるホップで比べる"
            assert row["f1_pct"]   == units.csv_blocked_ratio(r.blocked_ratio)
            assert row["slant_m"]  == units.csv_distance(r.slant_dist_km)
            # 桁は `units.csv_db` が単一ソース（3.0a1＝0.1 dB）。**ここに数字を
            # 書き写さない**＝写すと、桁を動かした日に「どちらが正か」が 2 つになる。
            assert row["rx_dbm"]   == units.csv_db(r.p_rx)
            assert row["margin_db"] == units.csv_db(r.actual_margin)
            # 率を名乗る列は 100% を超えない（超えるのは侵入深さであって率ではない）
            assert float(row["f1_pct"]) <= units.BLOCKED_RATIO_MAX

    def test_uses_the_batch_processor_for_each_hop(self):
        """実行層は `batch._process_one` の流用であること（新規ループを増やさない）。

        ホップ 1 本＝バッチの 1 行、という等式が A-3 を軽くしている根拠なので、
        ここが独自実装に置き換わったら設計の前提が崩れている。
        """
        import ast
        import pathlib

        src = pathlib.Path(mh.__file__).read_text(encoding="utf-8")
        calls = {
            f"{n.func.value.id}.{n.func.attr}"
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
        }
        assert "batch._process_one" in calls, (
            "ホップの実行がバッチの経路を通っていない"
            "（1 ホップ＝バッチの 1 行、という前提が崩れる）"
        )


# ============================================================
# トポロジー（点列＋接続規則）
# ============================================================
# **鎖を式に埋め込まない**という布石（[[project-radiosim-for-drone]]）。応用側
# （GCS →各点＝星）と構造を共有するための土台で、2.6 では鎖しか使わない。
# ⚠️ ここが守るのは「接続規則が 1 か所にある」ことだけ。**集約（min）は鎖の
# 意味論**なので、星を実際に使うときは併せて決め直すこと。

class TestTopology:

    def test_chain_is_the_default(self):
        path = _path(4)
        assert path.topology == mh.TOPOLOGY_CHAIN
        assert mh.links(path) == [(0, 1), (1, 2), (2, 3)]
        assert path.hop_count == 3

    def test_star_links_every_point_to_the_hub(self):
        path = _path(4)
        path.topology = mh.TOPOLOGY_STAR
        assert mh.links(path) == [(0, 1), (0, 2), (0, 3)]
        assert path.hop_count == 3

    def test_unknown_topology_falls_back_to_chain(self):
        """未知の値は鎖（新しい版が書いたファイルで落ちない）。"""
        path = _path(3)
        path.topology = "hypercube"
        assert mh.links(path) == [(0, 1), (1, 2)]

    def test_rows_follow_the_links_not_the_order(self):
        """`hop_rows` が接続規則に従うこと（隣接ペア決め打ちでない）。"""
        path = _path(3)
        path.topology = mh.TOPOLOGY_STAR
        rows = mh.hop_rows(path)
        hub = path.waypoints[0]
        assert all(r.lat_tx == hub.lat and r.lon_tx == hub.lon for r in rows), (
            "星なのに送信側がハブになっていない＝鎖の式が残っている"
        )
        assert [r.lat_rx for r in rows] == [w.lat for w in path.waypoints[1:]]

    def test_aggregation_refuses_topologies_it_was_not_designed_for(self):
        """鎖以外では**黙って数字を返さず止める**（集約規則が未決定だから）。

        `topology` という入り口を作った以上、星を設定した誰かに対して
        `overall_margin` は何事もなかったように min を返してしまう。min は
        鎖（直列）の意味論で、星（独立した N 本）では**経路上の分布**という
        主役の情報が消える＝静かに誤る。制約をコメントに書くだけでは守られない
        （この布石自体が散文だったせいで 5a では打たれなかった）ので門にする。
        """
        run = mh.MultiHopRun(path=_path(3), hops=[])
        assert run.ok is False                      # 鎖は従来どおり答える
        run.path.topology = mh.TOPOLOGY_STAR
        for name in ("ok", "worst", "overall_margin"):
            with pytest.raises(NotImplementedError) as ex:
                getattr(run, name)
            assert "集約" in str(ex.value), "何が未決定なのかが伝わらない"

    def test_labels_come_from_the_links(self):
        """区間の見出しも接続規則から導くこと（表示側に式を書き写さない）。"""
        path = _path(3)
        assert mh.hop_label(path, 0) == f"{path.waypoints[0].name} → {path.waypoints[1].name}"
        path.topology = mh.TOPOLOGY_STAR
        assert mh.hop_label(path, 1) == f"{path.waypoints[0].name} → {path.waypoints[2].name}"
        assert mh.hop_label(path, 9, fallback="—") == "—"


# ============================================================
# 「この版が扱える範囲」の宣言（I-066）
# ============================================================
# 🔴 背景（2026-08-04 の根本原因分析 → 2026-08-05 に実装）: 2.6 では
# 「**この版は鎖だけを扱う**」という 1 つの事実が 3 か所に**暗黙に**散っていた
# ——`project.py`（読む）・`views/multihop.py`（持つ）・`multihop.py`（実行する）。
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

    層がばらばらだと 2.6 の壊れ方に戻る＝読めるのに実行できない（窓が値を
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
    for file in sorted(root.glob("*.py")) + sorted((root / "views").glob("*.py")):
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


# ============================================================
# 合成シート（内訳＋全体判定）
# ============================================================
class TestRouteSheet:
    """**min だけを出さない**ことがこの節の主題（②）。"""

    def _run_with_report(self, base, tmp_path, monkeypatch):
        monkeypatch.setattr("report.report_path.save_path_visuals", lambda *a, **k: None)
        return _run(_path(3), base, tmp_path, monkeypatch)

    def test_route_html_is_written(self, base, tmp_path, monkeypatch):
        run = self._run_with_report(base, tmp_path, monkeypatch)
        assert os.path.exists(os.path.join(run.save_dir, "route.html"))
        assert os.path.exists(os.path.join(run.save_dir, "report_all.html"))

    def test_sheet_shows_every_hop_not_just_the_worst(self, base, tmp_path,
                                                      monkeypatch):
        """全体判定だけでなく**ホップ別の内訳**が載ること。

        min だけ出すのは②違反＝「どこが一番苦しいか」が分からないと、中継点を
        どこに足すか・どの区間の空中線を上げるかという次の一手が決められない。
        """
        from report import report_multihop

        i18n.set_lang("ja")
        run = self._run_with_report(base, tmp_path, monkeypatch)
        html = report_multihop.route_sheet_html(run)
        for i in range(len(run.hops)):
            wp_from = run.path.waypoints[i].name
            wp_to   = run.path.waypoints[i + 1].name
            assert f"{wp_from} → {wp_to}" in html, f"ホップ {i+1} の行が無い"
        for pr in run.hops:
            assert f"{pr.result.actual_margin:+.1f}" in html, (
                "ホップ別のマージンが載っていない（min だけでは次の一手が決まらない）"
            )

    def test_sheet_marks_the_weakest_hop(self, base, tmp_path, monkeypatch):
        """全体判定を決めている区間が**目で拾える**こと。"""
        from report import report_multihop

        run = self._run_with_report(base, tmp_path, monkeypatch)
        html = report_multihop.route_sheet_html(run)
        assert "worst" in html, "最も苦しい区間に印が付いていない"
        assert i18n.t("mh_worst_hop") in html

    def test_sheet_states_the_relay_model(self, base, tmp_path, monkeypatch):
        """**再生中継であること**をレポートに明記する（受動反射は対象外）。

        「反射板でも使えますか」と聞かれたときに成果物側で答えられる状態にする
        ＝前提を紙に残すのは 3.1「刻印」トラックの精神でもある。
        """
        from report import report_multihop

        i18n.set_lang("ja")
        run = self._run_with_report(base, tmp_path, monkeypatch)
        html = report_multihop.route_sheet_html(run)
        assert "再生中継" in html and "受動反射" in html

    def test_ledger_matches_the_batch_ledger(self, base, tmp_path, monkeypatch):
        """ホップ台帳が**バッチ台帳と同じ項目**を持つこと（2026-08-01 決定）。

        同じ「1 本の回線を数字で見る」面なのに、中継だけ損失の内訳・備考・
        断面図サムネイルが無かった＝**同じ問いに答える 2 つの面が食い違う**。
        列を足すときは両方を見て決める、という約束をここで縛る。
        """
        from report import report_multihop
        from report import report_summary

        i18n.set_lang("ja")
        run = self._run_with_report(base, tmp_path, monkeypatch)
        html = report_multihop.route_sheet_html(run)

        shared = set(report_summary._SUMMARY_COL_KEYS) & \
            set(report_multihop._HOP_COL_KEYS)
        # ⚠️ 備考列は**意図的に外してある**＝中継の備考は `hop_rows` が入れる
        # 「A → B」の導出物で、載せると区間列と同じ文字が並ぶだけになる。
        missing = {k for k in ("html_col_fspl", "html_col_diff", "html_col_veg",
                               "html_col_env", "html_col_rain", "html_col_gas",
                               "html_col_total_loss",
                               "html_col_graph")} - shared
        assert "html_col_note" not in report_multihop._HOP_COL_KEYS, (
            "区間名と同じ文字が並ぶ備考列が復活している"
        )
        assert not missing, f"バッチ台帳にあってホップ台帳に無い列: {missing}"
        for pr in run.hops:
            assert f"{pr.result.total_loss:.1f}" in html, "合計損失が載っていない"
        assert "profile.png" in html, "断面図のサムネイルが無い"

    def test_sheet_links_to_the_all_pages_document(self, base, tmp_path,
                                                   monkeypatch):
        """単体シートから**全ページ連結**へ辿れること（バッチと同じ導線）。

        `report_all.html` は作られていたのに、どこからも開けなかった＝
        作った成果物に導線が無ければ「無い機能」と同じ。
        """
        from report import report_multihop

        run = self._run_with_report(base, tmp_path, monkeypatch)
        assert "report_all.html" in report_multihop.route_sheet_html(run)
        # 連結文書の中では出さない（自分自身への案内になる）。
        assert "report_all.html" not in report_multihop.route_sheet_html(
            run, anchor_links=True)

    def test_hop_with_missing_artifacts_is_not_reported_as_ok(
            self, base, tmp_path, monkeypatch):
        """**成果物が欠けた区間を「成功」に紛れさせない**（I-010 のクラス点検）。

        中継は実行層をバッチから流用している（`batch._process_one`）ので、
        バッチだけ直しても中継が同じ隠し方をしていたら意味が無い＝**同じ穴が
        2 か所ある**という形（[[feedback-user-examples-are-classes]]）。

        全体判定まで落ちるのは意図どおり＝計算に失敗した区間が既に全体を NG に
        しており、成果物の欠落も「その区間は納品できていない」という同じクラス。
        """
        from report import report_multihop

        def _boom(*_a, **_kw):
            raise RuntimeError("仕組んだ描画失敗")

        i18n.set_lang("ja")
        # ⚠️ **地形の尾根（60m）を越える高さにする**＝既定の 30m では区間が元から
        # NG で、全体判定が最初から False になる。それでは「成果物の欠落が全体を
        # 落とした」ことを一切確かめられない（ゲートの壊れ方③＝間違ったものを
        # 要求している。実際、最初の版はこの土俵で書いてしまい、`MultiHopRun.ok`
        # を旧実装へ戻す変異を素通りさせた）。
        tall = mh.MultiHopPath(
            path_id   = "route1",
            waypoints = [_wp(f"P{i}", 34.54 + i * 0.01, 132.41 + i * 0.01, h=150.0)
                         for i in range(3)],
            hop_rf    = [mh.HopRF() for _ in range(2)],
        )
        # positive control＝**同じ土俵で成果物が作れれば OK になる**こと。
        # これが無いと、他の理由（計算が落ちた等）で ERROR でも通ってしまう。
        healthy = _run(tall, base, tmp_path, monkeypatch, stub_visuals=False)
        assert all(pr.status == "OK" for pr in healthy.hops) and healthy.ok, \
            "成果物が作れているのに OK にならない（この土俵が壊れている）"

        monkeypatch.setattr("report.report_path.save_profile_png", _boom)
        run = _run(tall, base, tmp_path, monkeypatch, stub_visuals=False)

        assert all(pr.result is not None for pr in run.hops), \
            "計算まで倒れている（この土俵では倒さない）"
        assert all(pr.status == "ERROR" for pr in run.hops), \
            "成果物が欠けたのに区間の判定が ERROR でない"
        assert not run.ok, "区間が納品できていないのに全体判定が OK"

        html = report_multihop.route_sheet_html(run)
        assert "profile.png" not in html, \
            "作られていない断面図へリンクしている（リンク切れの画像で気づかせない）"
        assert i18n.t("html_artifact_missing") in html

    def test_css_is_scoped_to_the_sheet(self):
        """シート固有 CSS が `.sheet.multihop` へスコープされていること。

        未スコープが 1 つ残るだけで、連結文書（report_all.html）で後勝ちの上書きが
        起き、**画面では気づけず印刷で初めて壊れる**（a2 の分割で同じ形を踏んだ）。
        """

        from report import report_multihop

        css = report_multihop.route_sheet_css()
        selectors = [
            line.split("{")[0].strip()
            for line in css.splitlines()
            if "{" in line and not line.strip().startswith(("/*", "*"))
        ]
        unscoped = [s for s in selectors if not s.startswith(".sheet.multihop")]
        assert not unscoped, f"スコープされていないセレクタ: {unscoped}"


# ============================================================
# 中継経路ウィンドウ（入力面）
# ============================================================
class TestMultiHopWindow:
    """**並び順が経路そのもの**であることを守る（ここが崩れると経路が化ける）。"""

    def _win(self, default_params_dict):
        from conftest import make_themed_root
        from views.multihop import MultiHopWindow
        root = make_themed_root()
        root.withdraw()
        i18n.set_lang("ja")
        return root, MultiHopWindow(root, sim.SimParams(default_params_dict))

    def _names(self, win):
        return [v["name"].get() for v in win._wp_vars]

    def test_starts_with_tx_and_rx(self, default_params_dict):
        root, win = self._win(default_params_dict)
        try:
            assert self._names(win) == ["TX", "RX"]
        finally:
            win.destroy(); root.destroy()

    def test_added_point_becomes_a_relay_between_tx_and_rx(self, default_params_dict):
        """**足した点は受信点の手前に入る**（末尾に足さない）。

        末尾に足すと「TX → RX → R1」となり、**それまで受信点だった地点が中継点に
        化ける**。実装直後のスクリーンショットで実際にそうなっていた＝利用者の
        頭の中（送信点と受信点があって、その間に中継点を置く）と食い違う。
        """
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()
            assert self._names(win) == ["TX", "R1", "RX"]
            win._on_add_point()
            assert self._names(win) == ["TX", "R1", "R2", "RX"]
            assert self._names(win)[0] == "TX" and self._names(win)[-1] == "RX"
        finally:
            win.destroy(); root.destroy()

    def test_hop_labels_follow_the_order(self, default_params_dict):
        """区間の表示が並び順から導出されること（TX → R1 / R1 → RX）。"""
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()
            labels = [w.cget("text") for w in win._hop_grid.grid_slaves(column=0)
                      if int(w.grid_info()["row"]) > 0]
            assert sorted(labels) == sorted(["TX → R1", "R1 → RX"])
        finally:
            win.destroy(); root.destroy()

    def _hop_labels(self, win):
        return sorted(w.cget("text") for w in win._hop_grid.grid_slaves(column=0)
                      if int(w.grid_info()["row"]) > 0)

    def test_renaming_a_point_updates_the_hop_headings(self, default_params_dict):
        """地点名を変えたら、区間表の見出しがその場で追従すること（B-073）。

        ⚠️ **同じ窓の中で 2 つの表が別の名前を名乗る**のが害＝数字は正しいので、
        気づかないまま「どの区間の話か」を取り違える。地点を足す/消すと作り直されて
        直るので、**直っているように見える瞬間がある**のが厄介だった。
        """
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()
            assert self._hop_labels(win) == sorted(["TX → R1", "R1 → RX"])
            win._wp_vars[1]["name"].set("HIROSHIMA")
            assert self._hop_labels(win) == sorted(["TX → HIROSHIMA",
                                                    "HIROSHIMA → RX"])
        finally:
            win.destroy(); root.destroy()

    def test_renaming_a_point_does_not_drop_the_results(self, default_params_dict):
        """🔴 **名前を直しても結果は消えないこと**（B-058/B-059 の約束）。

        見出しを追従させる素朴な直し方は「名前でも `_sync_hops` を呼ぶ」だが、
        あれは**区間行を作り直す＝結果列も一緒に消す**。⇒ 見出しだけを触ること。
        ここが無いと、B-073 を直したつもりで B-058 型の欠陥（消しすぎ）を作る。
        """
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()
            for cells in win._hop_result_labels:
                cells["rx"].configure(text="-70.0")
                cells["status"].configure(text="OK")
            win._wp_vars[1]["name"].set("HIROSHIMA")
            assert [c["rx"].cget("text") for c in win._hop_result_labels] \
                == ["-70.0", "-70.0"], "名前を直しただけで結果が消えた"
            assert [c["status"].cget("text") for c in win._hop_result_labels] \
                == ["OK", "OK"]
        finally:
            win.destroy(); root.destroy()

    # --------------------------------------------------------
    # 実行中の編集（B-102）＝**そもそも返してよい行か**
    # --------------------------------------------------------
    # 🔑 I-041 の規則には 3 つの面がある＝①どこへ返すか（I-041）②いつ結果でなく
    # なるか（B-058/B-059）③**そもそも返してよい行か**（B-068＝複数経路 /
    # B-102＝中継経路）。中継は引き当てが**添字**なので、地点を消すと後ろの区間が
    # 繰り上がり、`TX → R1` の結果が**別の地点対の行**へ入る。
    # ⚠️ **対で書く**＝「書かない」だけを見ると、何も書かない実装で緑になる
    # （過剰な抑止も欠陥＝B-058 側の壊れ方）。
    @staticmethod
    def _fake_hop_result(p_rx=-70.0, margin=3.0, status="OK"):
        from types import SimpleNamespace
        return SimpleNamespace(
            status=status,
            result=SimpleNamespace(p_rx=p_rx, actual_margin=margin))

    def _start_run(self, win):
        """実行の開始だけを真似る（DEM を引かない）＝控えを取って走行中にする。"""
        win._running = True
        win._clear_hop_results()

    def test_a_hop_result_is_not_written_to_a_row_shifted_during_the_run(
            self, default_params_dict):
        """実行中に中継点を消したら、その結果を繰り上がった行へ書かないこと（B-102）。

        `TX / R1 / RX` の区間 1（`TX → R1`）が返る前に `R1` を消すと、区間 1 の行は
        `TX → RX` になる。**添字は範囲内に居続ける**ので早期 return は効かない
        ＝控えと照合しなければ、別の地点対の結果として貼られる。
        """
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()                     # TX / R1 / RX（区間 2 本）
            self._start_run(win)
            win._delete_waypoint(1)                 # 実行中に R1 を消す
            win._show_hop_result(1, self._fake_hop_result())

            cells = win._hop_result_labels[0]
            assert cells["status"].cget("text") == "", \
                "消した中継点の結果が、繰り上がった区間の行に貼られた"
            assert cells["rx"].cget("text") == ""
            assert getattr(cells["status"], "_hop_input", None) is None, \
                "現在の入力で出た結果として控えられた（以後の編集でも消えない）"
        finally:
            win.destroy(); root.destroy()

    def test_a_hop_result_is_written_when_the_route_is_untouched(
            self, default_params_dict):
        """🔴 **触っていなければ、結果はちゃんと入ること**（消しすぎの防止）。

        上の抑止だけを入れると「何も書かない」実装が緑になる＝**対で縛る**。
        """
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()
            self._start_run(win)
            win._show_hop_result(1, self._fake_hop_result())

            cells = win._hop_result_labels[0]
            assert cells["status"].cget("text") == "OK", "触っていない区間に結果が入らない"
            assert cells["rx"].cget("text") == units.format_db(-70.0)
            assert getattr(cells["status"], "_hop_input", None) \
                == win._hop_input(1), "結果を生んだ入力の控えが取れていない"
        finally:
            win.destroy(); root.destroy()

    def test_a_hop_result_is_not_written_when_its_own_inputs_were_edited(
            self, default_params_dict):
        """同じ添字のままでも、**その区間の入力が変われば書かない**（B-068 と同じ規則）。

        地点の増減だけでなく、座標・高さ・区間 RF の編集でも「実行に出した姿」では
        なくなる。⇒ 照合の対象は添字ではなく `_hop_input`。
        """
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()
            self._start_run(win)
            win._wp_vars[1]["height"].set("50")     # 実行中に R1 の高さを変えた
            win._show_hop_result(1, self._fake_hop_result())

            assert win._hop_result_labels[0]["status"].cget("text") == "", \
                "編集後の入力に、編集前の結果が貼られた"
        finally:
            win.destroy(); root.destroy()

    def test_delete_removes_a_relay_not_the_endpoint(self, default_params_dict):
        """削除で消えるのは**中継点**（送信点・受信点は残る）。"""
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()
            win._delete_waypoint(1)
            assert self._names(win) == ["TX", "RX"]
            win._delete_waypoint(0)                 # 送信点は消せない
            win._delete_waypoint(1)                 # 受信点も消せない
            assert self._names(win) == ["TX", "RX"]
        finally:
            win.destroy(); root.destroy()

    def test_map_picks_fill_blanks_in_order(self, default_params_dict):
        """地図のクリックは**空欄を順に埋める**＝TX → RX → 以後は中継点。"""
        root, win = self._win(default_params_dict)
        try:
            assert win.append_waypoint(34.54, 132.41) == "TX"
            assert win.append_waypoint(34.53, 132.40) == "RX"
            third = win.append_waypoint(34.535, 132.405)
            assert self._names(win) == ["TX", third, "RX"], (
                "3 点目が受信点の後ろに足されている（経路が化ける）"
            )
        finally:
            win.destroy(); root.destroy()

    # --------------------------------------------------------
    # 地図との配線（**両側を通す**）
    # --------------------------------------------------------
    # ⚠️ このクラスのテストは長らく `append_waypoint`（受け取る側）しか叩いて
    # おらず、**呼び出す側が一度も動いていなかった**ことを見逃した（5b の
    # `getattr(self.master, "open_map_for_waypoints")` は `self.master` が Tk の
    # ルートなので常に None → 「地図を開けません」で終わっていた）。以後は
    # ランチャーから地図までを通したところをゲートにする。

    def test_from_map_button_reaches_the_launcher(self, monkeypatch):
        """「地図から選択」がランチャー経由で地図へ届き、宛先がこの窓になること。"""
        from conftest import make_themed_root
        from views import dialogs
        from views.launcher import SimLauncher
        root = make_themed_root()
        root.withdraw()
        i18n.set_lang("ja")
        try:
            app = SimLauncher(root, lambda _t: None)
            app._on_open_multihop()
            win = app._multihop_win

            seen: dict = {}
            monkeypatch.setattr(dialogs, "alert",
                                lambda *a, **k: seen.setdefault("alert", True))

            class _FakeMap:
                def start_waypoint_mode(self, sink):
                    seen["sink"] = sink

            monkeypatch.setattr(app, "_on_open_map",
                                lambda: setattr(app, "_map_win", _FakeMap()))
            win._on_from_map()

            assert "alert" not in seen, "「地図を開けません」で終わっている（配線切れ）"
            assert seen.get("sink") is win
        finally:
            root.destroy()

    def test_map_can_open_the_relay_window_by_itself(self, monkeypatch):
        """地図のモードセレクタから中継点モードを選んでも受け皿が用意されること。

        受け皿が無いまま入ると、クリックしても**何も起きず・何も出ない**死んだ
        モードになる（連続追加は `append_provider` で受け皿を確保しているので、
        中継点モードだけが非対称だった）。
        """
        from conftest import make_themed_root
        from views import map_window
        from views.launcher import SimLauncher
        root = make_themed_root()
        root.withdraw()
        try:
            app = SimLauncher(root, lambda _t: None)
            captured: dict = {}

            class _RecordingMap:
                def __init__(self, parent, config, **kwargs):
                    captured.update(kwargs)
                    self._win = root

                def on_waypoints_changed(self):
                    pass                      # 地点列の通知（本物は描き直す）

            monkeypatch.setattr(map_window, "MapWindow", _RecordingMap)
            app._on_open_map()

            provider = captured.get("waypoint_provider")
            assert provider is not None, "地図に中継点モードの受け皿を渡していない"
            assert provider() is app._multihop_win
        finally:
            root.destroy()

    def test_map_layer_is_a_copy_of_the_waypoint_list(self, default_params_dict):
        """地図の中継点は**窓の地点列の写し**であること（地図は源泉を持たない）。

        以前はクリックのたびにマーカーを足すだけで消し方が無く、**窓で地点を
        削除しても地図にピンが残り続けた**（2026-08-01 実機確認）。バッチ →
        地図（`existing_paths`）と同じ「毎回引き直す」形に揃える。
        """
        root, win = self._win(default_params_dict)
        try:
            notified: list = []
            win._map_notify = lambda: notified.append(True)
            win.append_waypoint(34.54, 132.41)
            win.append_waypoint(34.53, 132.40)
            win._on_add_point()                       # 中継点を 1 つ足す
            win._wp_vars[1]["coord"].set("34.535, 132.405")
            assert [n for n, _, _ in win.waypoint_markers()] == ["TX", "R1", "RX"]

            win._delete_waypoint(1)                   # 中継点を消す
            assert [n for n, _, _ in win.waypoint_markers()] == ["TX", "RX"], (
                "削除が地点列に反映されていない（地図に残る）"
            )
            assert notified, "地点列が変わったのに地図へ通知していない"
        finally:
            win.destroy(); root.destroy()

    def test_map_layer_skips_unreadable_coordinates(self, default_params_dict):
        """座標を入力途中の地点は地図に出さない（読める点だけ描く）。"""
        root, win = self._win(default_params_dict)
        try:
            win._wp_vars[0]["coord"].set("34.54, 132.41")
            win._wp_vars[1]["coord"].set("34.5")       # 途中
            assert [n for n, _, _ in win.waypoint_markers()] == ["TX"]
        finally:
            win.destroy(); root.destroy()

    # --------------------------------------------------------
    # 地図から**置き直す**（I-098）
    # --------------------------------------------------------
    # 地図が持っているのは「写しの並びで何番目か」だけ。窓の行番号ではないので、
    # 読めない座標の行があると 2 つはずれる。**ずれたまま書き戻すと、黙って別の
    # 地点が動く**（B-068 / B-102 と同じ型）ので、位置の解き方と照合をここで固定する。

    def test_map_moves_the_point_the_map_actually_drew(self, default_params_dict):
        """写しの並びの位置が、**読めない行を飛ばした後の位置**として解けること。"""
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()                       # TX / R1 / RX
            win._wp_vars[0]["coord"].set("34.54, 132.41")
            win._wp_vars[1]["coord"].set("34.5")      # 入力途中＝地図に出ない
            win._wp_vars[2]["coord"].set("34.53, 132.40")
            drawn = win.waypoint_markers()
            assert [n for n, _, _ in drawn] == ["TX", "RX"]

            assert win.update_waypoint(1, 34.52, 132.39, "RX") is True
            assert win._wp_vars[1]["coord"].get() == "34.5", (
                "地図に出ていない行（R1）が動いた＝写しの位置を行番号として読んでいる"
            )
            assert [n for n, _, _ in win.waypoint_markers()][1] == "RX"
            lat, lon = coords.parse_pair(win._wp_vars[2]["coord"].get())
            assert (round(lat, 5), round(lon, 5)) == (34.52, 132.39)
        finally:
            win.destroy(); root.destroy()

    def test_map_refuses_to_move_a_point_that_moved_away(self, default_params_dict):
        """選んでから動かすまでに並びが変われば、**書き戻さずに断る**こと。"""
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()
            for i, c in enumerate(("34.54, 132.41", "34.535, 132.405",
                                   "34.53, 132.40")):
                win._wp_vars[i]["coord"].set(c)
            before = [v["coord"].get() for v in win._wp_vars]

            win._delete_waypoint(1)                   # 選んだ後に中継点が消えた
            assert win.update_waypoint(1, 34.52, 132.39, "R1") is False, (
                "消えた地点の位置へ書き戻した（別の地点が黙って動く）"
            )
            assert win.update_waypoint(9, 34.52, 132.39, "RX") is False, (
                "写しの並びの外を指しているのに書き戻した"
            )
            assert [v["coord"].get() for v in win._wp_vars] == [
                before[0], before[2]], "断ったのに座標が変わっている"
        finally:
            win.destroy(); root.destroy()

    def test_collected_path_shares_the_relay_height(self, default_params_dict):
        """画面 → モデルでも**中継点の高さは 1 つ**であること（⑦の端から端まで）。"""
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()
            coords = ["34.5429, 132.4118", "34.5410, 132.4090", "34.5389, 132.4050"]
            heights = ["40", "25", "10"]
            for vars_, c, h in zip(win._wp_vars, coords, heights):
                vars_["coord"].set(c)
                vars_["height"].set(h)
            rows = mh.hop_rows(win._collect_path())
            assert rows[0].h_rx == 25.0 and rows[1].h_tx == 25.0
        finally:
            win.destroy(); root.destroy()

    def test_blank_hop_fields_inherit_the_common_settings(self, default_params_dict):
        """区間の空欄は共通設定を引き継ぐ（`None` のまま渡す）。"""
        root, win = self._win(default_params_dict)
        try:
            for vars_ in win._wp_vars:
                vars_["coord"].set("34.5429, 132.4118")
            win._wp_vars[-1]["coord"].set("34.5389, 132.4050")
            path = win._collect_path()
            assert path.hop_rf[0].freq_mhz is None
            assert path.hop_rf[0].gain_tx is None
        finally:
            win.destroy(); root.destroy()


# ------------------------------------------------------------
# トポロジーの契約＝**読める / 実行できる を分け、止めるなら早く止める**
# ------------------------------------------------------------
# 🔴 背景（2026-08-04・独立レビュー Codex 6 巡目）: `links()` は未知のトポロジーを
# 鎖として扱う一方、`MultiHopRun._require_chain` は鎖以外を拒否する——**同じ
# モジュールに正反対の方針が同居**していた。その結果 `star` は実行前検証を通り、
# **全区間の DEM を引いた後**、レポート生成の集約で初めて落ちていた。
#
# 🔑 採る方針＝`_require_chain` 側（「静かに誤るより、その場で決定を強制する」）。
# **止めるなら、金と時間を使う前に止める。**
def test_unsupported_topology_stops_before_fetching_terrain(monkeypatch):
    """`star` は **DEM を 1 枚も引かずに** on_error へ落ちること。"""
    fetched: list[int] = []

    def _must_not_fetch(*_a, **_kw):
        fetched.append(1)
        raise AssertionError("実行できないトポロジーで DEM を引いている")

    monkeypatch.setattr(mh.batch, "_fetch_sync", _must_not_fetch,
                        raising=True)

    path = mh.MultiHopPath(
        path_id="r1", topology=mh.TOPOLOGY_STAR,
        waypoints=[mh.Waypoint(name=f"P{i}", lat=34.5 + i * 0.01,
                                     lon=132.4, h=30.0) for i in range(3)])
    done = threading.Event()
    box: list[Exception] = []

    mh.run_multihop(
        path, sim.SimParams(config.DEFAULT_CONFIG),
        on_hop_start    = lambda *a: None,
        on_hop_progress = lambda *a: None,
        on_hop_complete = lambda *a: None,
        on_complete     = lambda run: done.set(),
        on_error        = lambda ex: (box.append(ex), done.set()),
    )
    assert done.wait(timeout=30), "実行が終わらなかった"
    assert box and isinstance(box[0], NotImplementedError), \
        f"未対応トポロジーが早期に止まっていない（{box!r}）"
    assert not fetched, "DEM を引いてしまっている（止めるなら引く前）"


def test_unsupported_topology_still_reports_off_the_calling_thread(monkeypatch):
    """⚠️ **失敗の渡り方は経路によって変えない**（スレッド契約）。

    `run_multihop` は「バックグラウンドスレッドで開始する」API。未対応トポロジー
    のときだけ**呼び出し元スレッドで `on_error` を呼んで戻る**と、コールバックの
    順序とスレッドが経路ごとに変わり、再入の危険が出る（2026-08-04・独立レビュー
    Codex 7 巡目）。⇒ **どの失敗もワーカースレッドから**渡ってくること。
    """
    def _must_not_fetch(*_a, **_kw):
        raise AssertionError("DEM を引いている")

    monkeypatch.setattr(mh.batch, "_fetch_sync", _must_not_fetch, raising=True)
    path = mh.MultiHopPath(
        path_id="r1", topology=mh.TOPOLOGY_STAR,
        waypoints=[mh.Waypoint(name=f"P{i}", lat=34.5 + i * 0.01,
                               lon=132.4, h=30.0) for i in range(3)])
    caller = threading.current_thread().ident
    done = threading.Event()
    seen: list[int] = []

    mh.run_multihop(
        path, sim.SimParams(config.DEFAULT_CONFIG),
        on_hop_start    = lambda *a: None,
        on_hop_progress = lambda *a: None,
        on_hop_complete = lambda *a: None,
        on_complete     = lambda run: done.set(),
        on_error        = lambda ex: (seen.append(threading.current_thread().ident),
                                      done.set()),
    )
    assert done.wait(timeout=30), "実行が終わらなかった"
    assert seen, "on_error が呼ばれていない"
    assert seen[0] != caller, \
        "on_error が呼び出し元スレッドで実行された（他の失敗経路と契約が違う）"


# ============================================================
# 集約カードの語は判定で変わる（I-052・2.7 スライス D）
# ============================================================
class TestOverallDisplay:
    """**同じ数字が答えている問いが、判定で変わる**——だから語を切り替える。

    OK のときの min は「あと何 dB 積めるか」という連続量（設計余裕の KPI）だが、
    NG のときに要るのは「あと何 dB 足りないか」で、`−12.4` を「余裕」と書いた
    数字は読み違えの元になる。⇒ 値の出所は変えず、**語と符号だけ**を切り替える。
    """

    def test_ok_run_shows_the_margin_with_its_sign(self, base, tmp_path, monkeypatch):
        run = _run(_path(3), base, tmp_path, monkeypatch)
        for h in run.hops:
            h.result.actual_margin = 8.0
            h.result.status = "OK"
        key, text = mh.overall_display(run, digits=1)
        assert key == mh.OVERALL_MARGIN_KEY
        assert text == "+8.0", text

    def test_ng_run_shows_the_shortfall_as_a_positive_amount(self, base, tmp_path,
                                                             monkeypatch):
        """「不足 −12.4」は二重否定で読めない＝符号を反転して不足量そのものを出す。"""
        run = _run(_path(3), base, tmp_path, monkeypatch)
        for h in run.hops:
            h.result.actual_margin = 5.0
            h.result.status = "OK"
        run.hops[0].result.actual_margin = -12.4
        run.hops[0].result.status = "NG"
        key, text = mh.overall_display(run, digits=1)
        assert key == mh.OVERALL_SHORTFALL_KEY
        assert text == "12.4", text

    def test_an_unspeakable_overall_never_borrows_the_shortfall_wording(
            self, base, tmp_path, monkeypatch):
        """判定できない（ERR）ときに「最大不足 —」と書かない＝無い情報を語らない。"""
        run = _run(_path(3), base, tmp_path, monkeypatch)
        run.hops[1].result = None
        key, text = mh.overall_display(run)
        assert (key, text) == (mh.OVERALL_MARGIN_KEY, "—")

    def test_the_report_card_uses_the_same_wording_as_the_window(
            self, base, tmp_path, monkeypatch):
        """レポートのカードが `overall_display` の語を使うこと。

        ⚠️ **純関数のテストだけでは足りない**＝呼び出し側が旧キーを直に引いたまま
        でも、上の 3 本は緑のままになる（I-010 で見た「判定の出所が各所に散る」
        壊れ方と同じ）。**成果物の字**まで見て初めて単一ソースが証明される。
        """
        from report import report_multihop
        run = _run(_path(3), base, tmp_path, monkeypatch)
        for h in run.hops:
            h.result.actual_margin = 5.0
            h.result.status = "OK"
        run.hops[0].result.actual_margin = -12.4
        run.hops[0].result.status = "NG"
        html = report_multihop.route_sheet_html(run)
        assert i18n.t(mh.OVERALL_SHORTFALL_KEY) in html
        assert i18n.t(mh.OVERALL_MARGIN_KEY) not in html
        assert "12.4 dB" in html


# ============================================================
# 全体判定の状態語（B-071）
# ============================================================
class TestOverallStatus:
    """**判定不能を不成立と言い切らない**（B-071）。

    区間表と件数カードは `ERROR` と言っているのに、全体判定だけが `run.ok` の
    二値から語を作っていたので `NG` に潰れていた＝**同じ成果物の中で食い違う**。
    読み手は「計算できたが回線が成立しない」と「判定できなかった」を区別できない。

    ⚠️ **純関数のテストだけでは足りない**（`TestOverallDisplay` の最後の 1 本と
    同じ理由）＝呼び出し側が `"OK" if run.ok else "NG"` を書いたままでも純関数は
    緑のままになる。**画面の字とレポートの字**まで見て初めて単一ソースが立つ。
    """

    def test_healthy_run_keeps_the_two_ordinary_words(self, base, tmp_path,
                                                       monkeypatch):
        """ERR が無いときは従来どおり OK / NG（**語彙を増やしただけ**にしない）。"""
        run = _run(_path(3), base, tmp_path, monkeypatch)
        for h in run.hops:
            h.result.actual_margin = 8.0
            h.result.status = "OK"
        assert mh.overall_status(run) == "OK"
        run.hops[0].result.actual_margin = -12.4
        run.hops[0].result.status = "NG"
        assert mh.overall_status(run) == "NG"

    def test_a_failed_hop_makes_the_overall_error_not_ng(self, base, tmp_path,
                                                          monkeypatch):
        """計算に失敗した区間があれば全体は `ERROR`（`NG` ではない）。"""
        run = _run(_path(3), base, tmp_path, monkeypatch)
        for h in run.hops:
            h.result.actual_margin = 8.0
            h.result.status = "OK"
        run.hops[1].result = None
        assert run.hops[1].status == "ERROR", "前提: 区間表は ERROR と言っている"
        assert mh.overall_status(run) == "ERROR", (
            "判定不能な区間があるのに、全体判定が回線不成立（NG）と言い切っている"
        )

    def test_a_missing_artifact_also_makes_the_overall_error(self, base, tmp_path,
                                                              monkeypatch):
        """**成果物だけ欠けた区間**も同じ（判定の出所は `PathResult.status`・I-010 ③）。

        電波的には全区間 OK なので、`status` を見ずに `result` の有無だけを見ると
        ここが素通りする（`worst` / `overall_margin` が踏んだのと同じ穴）。
        """
        run = _run(_path(3), base, tmp_path, monkeypatch)
        for h in run.hops:
            h.result.actual_margin = 20.0
            h.result.status = "OK"
        run.hops[0].artifact_error = RuntimeError("仕組んだ描画失敗")
        assert mh.overall_status(run) == "ERROR"

    def test_a_run_without_hops_is_not_judged_either(self, base, tmp_path,
                                                      monkeypatch):
        """区間が 1 つも無い実行も `ERROR`＝**材料が無いことを NG と言わない**。

        `overall_margin` は同じ条件で既に `None`（語れない）を返しており、
        状態語だけが「不成立」と断定していると、そこでも 2 つの面が食い違う。
        """
        run = mh.MultiHopRun(path=_path(3), hops=[], save_dir=str(tmp_path))
        assert run.overall_margin is None, "前提: 全体マージンは語れない"
        assert mh.overall_status(run) == "ERROR"

    def test_the_report_card_says_error_and_is_not_painted_as_ng(
            self, base, tmp_path, monkeypatch):
        """レポートの集約カードが `ERROR` の字と `err` の色で出ること。

        字だけ直して色を直さないと、**判定不能が不成立と同じ赤**で塗られる
        （成果物の上では色も語のうち）。
        """
        from report import report_multihop
        run = _run(_path(3), base, tmp_path, monkeypatch)
        for h in run.hops:
            h.result.actual_margin = 20.0
            h.result.status = "OK"
        run.hops[1].result = None
        html = report_multihop.route_sheet_html(run)
        card = html.split(f'{i18n.t("mh_overall")}</div>', 1)[1][:120]
        assert ">ERROR<" in card, f"全体判定のカードが ERROR と言っていない: {card}"
        # ⚠️ **件数カードの `card ng`（NG が 0 件）と混ざる**ので、全体判定の
        # カードそのものを見る（`err` の有無を文書全体で数えると必ず素通りする）。
        overall_card = f'<div class="card err"><div class="lbl">{i18n.t("mh_overall")}'
        assert overall_card in html, "判定不能のカードが不成立の色で塗られている"

    def test_the_window_summary_says_the_same_word(self, default_params_dict,
                                                    base, tmp_path, monkeypatch):
        """画面のサマリ 1 行も同じ語（**成果物とだけ揃えても半分**）。"""
        from conftest import make_themed_root
        import views.multihop as vm
        run = _run(_path(3), base, tmp_path, monkeypatch)
        for h in run.hops:
            h.result.actual_margin = 20.0
            h.result.status = "OK"
        run.hops[1].result = None

        monkeypatch.setattr(vm.dialogs, "choose", lambda *a, **k: None)
        root = make_themed_root()
        root.withdraw()
        i18n.set_lang("ja")
        win = vm.MultiHopWindow(root, sim.SimParams(default_params_dict))
        try:
            win._on_complete(run)
            text = str(win._summary_label.cget("text"))
            assert "ERROR" in text, f"画面のサマリが ERROR と言っていない: {text}"
            assert "NG" not in text, f"判定不能を NG と言い切っている: {text}"
        finally:
            win.destroy(); root.destroy()


# ============================================================
# ERROR 行の理由が区間表の幅を押し広げないこと（B-145）
# ============================================================
class TestHopLedgerErrorReasonFitsOnPaper:
    """自由文の理由を colspan で流し込む面の**2 つ目**（1 つ目＝バッチ台帳）。

    見ているのは幅そのもの（クラスの有無ではない）。増分で測る理由は
    tests/table_fit.py の冒頭にある。
    """

    LONG_REASON = (
        "PermissionError: [WinError 5] "
        r"C:\Users\example\OneDrive\Documents\radiosim\results\route_20260830_093121"
        r"\route1_h1_very_long_identifier\report.html "
    ) * 3
    SHORT_REASON = "boom"

    def _html(self, base, tmp_path, monkeypatch, reason: str) -> str:
        from report import report_multihop

        monkeypatch.setattr("report.report_path.save_path_visuals", lambda *a, **k: None)
        i18n.set_lang("ja")
        run = _run(_path(3), base, tmp_path, monkeypatch)
        broken = run.hops[0]
        run.hops[0] = batch.PathResult(row=broken.row, result=None, params=None,
                                       error=RuntimeError(reason))
        return ("<style>" + report_multihop.route_sheet_css() + "</style>"
                + report_multihop.route_sheet_html(run))

    def test_a_long_error_reason_does_not_widen_the_hop_ledger(
            self, base, tmp_path, monkeypatch):
        from tests import table_fit

        sheet = (("section", frozenset({"sheet", "multihop"})),)
        narrow = table_fit.table_min_width_px(
            self._html(base, tmp_path, monkeypatch, self.SHORT_REASON),
            "hops", ancestors=sheet)
        wide = table_fit.table_min_width_px(
            self._html(base, tmp_path, monkeypatch, self.LONG_REASON),
            "hops", ancestors=sheet)
        assert wide == pytest.approx(narrow, abs=1.0), (
            "ERROR 区間の理由が長いだけで区間表が広がる＝右端の列が紙の外へ出る"
            f"（{narrow:.0f}px → {wide:.0f}px）"
        )
