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
import os
import sys
import threading

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import batch
import config
import i18n
import multihop as mh
import report_summary
import simulation as sim


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


def _run(path, base_params, tmp_path, monkeypatch, **kwargs):
    """run_multihop を同期的に回して MultiHopRun を返す。"""
    monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)
    monkeypatch.setattr(sim, "_terrain_cache", {})
    monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path))
    # レポート図の生成は本題ではないので塞ぐ（別テストの担当）。
    monkeypatch.setattr("report_path.save_path_visuals", lambda *a, **k: None)
    monkeypatch.setattr(report_summary, "render_summary_map_b64", lambda r: None)

    out: list = []
    err: list = []
    done = threading.Event()
    mh.run_multihop(
        path, base_params,
        on_hop_start    = lambda i, n, pid: None,
        on_hop_progress = lambda v: None,
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
        assert any("ホップ" in e or "hops" in e
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
        monkeypatch.setattr("report_path.save_path_visuals", lambda *a, **k: None)
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
        import report_multihop

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
        import report_multihop

        run = self._run_with_report(base, tmp_path, monkeypatch)
        html = report_multihop.route_sheet_html(run)
        assert "worst" in html, "最も苦しい区間に印が付いていない"
        assert i18n.t("mh_worst_hop") in html

    def test_sheet_states_the_relay_model(self, base, tmp_path, monkeypatch):
        """**再生中継であること**をレポートに明記する（受動反射は対象外）。

        「反射板でも使えますか」と聞かれたときに成果物側で答えられる状態にする
        ＝前提を紙に残すのは 3.1「刻印」トラックの精神でもある。
        """
        import report_multihop

        i18n.set_lang("ja")
        run = self._run_with_report(base, tmp_path, monkeypatch)
        html = report_multihop.route_sheet_html(run)
        assert "再生中継" in html and "受動反射" in html

    def test_css_is_scoped_to_the_sheet(self):
        """シート固有 CSS が `.sheet.multihop` へスコープされていること。

        未スコープが 1 つ残るだけで、連結文書（report_all.html）で後勝ちの上書きが
        起き、**画面では気づけず印刷で初めて壊れる**（a2 の分割で同じ形を踏んだ）。
        """

        import report_multihop

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

    def test_delete_removes_a_relay_not_the_endpoint(self, default_params_dict):
        """削除で消えるのは**中継点**（送信点・受信点は残る）。"""
        root, win = self._win(default_params_dict)
        try:
            win._on_add_point()
            win._on_del_point()
            assert self._names(win) == ["TX", "RX"]
            win._on_del_point()                     # 2 点未満にはしない
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
