"""
tests/test_batch.py
===================
batch.py のユニットテスト。
対象: validate_rows / _make_params / 遮蔽区間の切り出し / CSV のセルの安全化。
CSV の読み込みと実行エンジンは帳票と一緒に Pro へ移した（Link 1.0 ステージ11b）。
"""

from typing import Any

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import models
from core import simulation as sim
from core import terrain_grid
from report import batch
from report.batch import _make_params
from report.report_path import _find_obs_segments


# ============================================================
# ヘルパー
# ============================================================

def _row(**kwargs) -> batch.PathRow:
    """テスト用 PathRow。引数で任意フィールドを上書きできる。"""
    defaults: dict[str, Any] = dict(
        path_id  = "path01",
        lat_tx   = 34.54,
        lon_tx   = 132.41,
        lat_rx   = 34.53,
        lon_rx   = 132.40,
        h_tx     = 30.0,
        h_rx     = 10.0,
        freq_mhz = None,
        note     = "",
    )
    defaults.update(kwargs)
    return batch.PathRow(**defaults)


# ============================================================
# validate_rows
# ============================================================

class TestValidateRows:

    def test_valid_single_row(self):
        assert batch.validate_rows([_row()]) == []

    def test_valid_multiple_rows(self):
        rows = [_row(path_id="p1"), _row(path_id="p2")]
        assert batch.validate_rows(rows) == []

    def test_empty_list(self):
        errs = batch.validate_rows([])
        assert len(errs) == 1
        assert "empty" in errs[0].lower()

    def test_duplicate_id(self):
        rows = [_row(path_id="p1"), _row(path_id="p1")]
        errs = batch.validate_rows(rows)
        assert any("Duplicate" in e for e in errs)

    def test_duplicate_id_ignores_case(self):
        """B-013: `p01`/`P01` は Windows の FS では同じ dir＝重複として弾くこと。

        通してしまうと両者の成果物が同一パスへ書かれ、黙って上書きされる。
        """
        rows = [_row(path_id="p01"), _row(path_id="P01")]
        errs = batch.validate_rows(rows)
        assert any("Duplicate" in e for e in errs)

    def test_case_differing_ids_are_not_flagged_when_distinct(self):
        """大小以外でも違う ID は重複ではない（casefold の巻き込み防止）。"""
        assert batch.validate_rows([_row(path_id="p01"), _row(path_id="P02")]) == []

    def test_invalid_id_slash(self):
        errs = batch.validate_rows([_row(path_id="path/01")])
        assert any("ID" in e for e in errs)

    def test_invalid_id_dotdot(self):
        errs = batch.validate_rows([_row(path_id="../etc")])
        assert any("ID" in e for e in errs)

    def test_nan_in_coords(self):
        errs = batch.validate_rows([_row(lat_tx=float("nan"))])
        assert any("Invalid" in e for e in errs)

    def test_lat_tx_out_of_range(self):
        errs = batch.validate_rows([_row(lat_tx=91.0)])
        assert any("TX latitude" in e for e in errs)

    def test_lon_tx_out_of_range(self):
        errs = batch.validate_rows([_row(lon_tx=181.0)])
        assert any("TX longitude" in e for e in errs)

    def test_lat_rx_out_of_range(self):
        errs = batch.validate_rows([_row(lat_rx=-91.0)])
        assert any("RX latitude" in e for e in errs)

    def test_lon_rx_out_of_range(self):
        errs = batch.validate_rows([_row(lon_rx=-181.0)])
        assert any("RX longitude" in e for e in errs)

    def test_tx_rx_identical(self):
        errs = batch.validate_rows([_row(lat_rx=34.54, lon_rx=132.41)])
        assert any("identical" in e for e in errs)

    def test_h_tx_out_of_range_high(self):
        errs = batch.validate_rows([_row(h_tx=501.0)])
        assert any("h_tx" in e for e in errs)

    def test_h_rx_out_of_range_negative(self):
        errs = batch.validate_rows([_row(h_rx=-1.0)])
        assert any("h_rx" in e for e in errs)

    def test_freq_out_of_range_high(self):
        errs = batch.validate_rows([_row(freq_mhz=200000.0)])
        assert any("freq" in e for e in errs)

    def test_freq_none_is_valid(self):
        assert batch.validate_rows([_row(freq_mhz=None)]) == []

    def test_gain_tx_out_of_range_high(self):
        errs = batch.validate_rows([_row(gain_tx=61.0)])
        assert any("TX gain" in e for e in errs)

    def test_gain_rx_out_of_range_negative(self):
        errs = batch.validate_rows([_row(gain_rx=-1.0)])
        assert any("RX gain" in e for e in errs)

    def test_gain_none_is_valid(self):
        assert batch.validate_rows([_row(gain_tx=None, gain_rx=None)]) == []

    def test_gain_boundary_values_accepted(self):
        assert batch.validate_rows([_row(gain_tx=0.0, gain_rx=60.0)]) == []

    def test_boundary_h_tx_zero(self):
        assert batch.validate_rows([_row(h_tx=0.0)]) == []

    def test_boundary_h_tx_500(self):
        assert batch.validate_rows([_row(h_tx=500.0)]) == []

    def test_multiple_errors_reported(self):
        errs = batch.validate_rows([_row(h_tx=999.0, h_rx=999.0)])
        assert len(errs) >= 2

    def test_path_id_too_long_rejected(self):
        errs = batch.validate_rows(
            [_row(path_id="p" * (batch.MAX_TYPED_ID_LEN + 1))])
        assert any("too long" in e for e in errs)

    def test_path_id_at_max_length_accepted(self):
        assert batch.validate_rows(
            [_row(path_id="p" * batch.MAX_TYPED_ID_LEN)]) == []

    def test_the_default_limit_is_the_one_a_person_can_read(self):
        """既定の ID 上限は**表で読める長さ**であること（B-057）。

        ⚠️ **上の 2 本だけでは守れない**＝どちらも `MAX_TYPED_ID_LEN` を基準に
        書いてあるので、既定が出力用の 16 に戻されても**定数ごと動いて緑のまま**
        になる（[[feedback-promote-recurring-checks]] 壊れ方①）。ここは
        **表に出せない長さが既定で通らないこと**を、もう一方の定数で名指しする。
        """
        assert batch.MAX_TYPED_ID_LEN < batch._MAX_PATH_ID_LEN
        errs = batch.validate_rows(
            [_row(path_id="p" * batch._MAX_PATH_ID_LEN)])
        assert any("too long" in e for e in errs), (
            "出力ディレクトリ名の上限（16）が、人が打つ側の既定にも使われている"
            "＝複数経路の表で 5 文字ぶん読めない ID が通る（B-057 の再発）"
        )

    def test_generated_ids_may_use_the_longer_output_limit(self):
        """**生成された** ID は出力用の上限まで通ること（中継の `_h8` 付き）。

        逆向きの対＝上の検査だけだと「両方 11 に揃える」でも緑になり、そのとき
        中継の経路 ID は黙って 8 文字まで縮む（無傷のウィンドウを壊す）。
        """
        pid = "p" * batch._MAX_PATH_ID_LEN
        assert batch.validate_rows(
            [_row(path_id=pid)], max_id_len=batch._MAX_PATH_ID_LEN) == []

    def test_note_too_long_rejected(self):
        errs = batch.validate_rows([_row(note="x" * (batch._MAX_NOTE_LEN + 1))])
        assert any("Note too long" in e for e in errs)

    def test_note_at_max_length_accepted(self):
        assert batch.validate_rows([_row(note="x" * batch._MAX_NOTE_LEN)]) == []

    def test_freq_in_range_boundary_low(self):
        assert batch.validate_rows([_row(freq_mhz=1.0)]) == []

    def test_freq_in_range_boundary_high(self):
        assert batch.validate_rows([_row(freq_mhz=100000.0)]) == []

    def test_lat_tx_86_rejected(self):
        """TX 緯度 85.05° 超は拒否されること。"""
        errs = batch.validate_rows([_row(lat_tx=86.0)])
        assert any("TX latitude" in e for e in errs)

    def test_lat_rx_neg_86_rejected(self):
        """RX 緯度 −85.05° 未満は拒否されること。"""
        errs = batch.validate_rows([_row(lat_rx=-86.0)])
        assert any("RX latitude" in e for e in errs)

    def test_lat_85_0_accepted(self):
        """±85.0° は許可されること。"""
        assert batch.validate_rows([_row(lat_tx=85.0, lat_rx=-85.0)]) == []


# ============================================================
# _find_obs_segments
# ============================================================

class TestFindObsSegments:

    def test_all_false(self):
        assert _find_obs_segments(np.zeros(5, dtype=bool)) == []

    def test_all_true(self):
        result = _find_obs_segments(np.ones(3, dtype=bool))
        assert result == [(0, 2)]

    def test_single_true_at_start(self):
        assert _find_obs_segments(np.array([True, False, False])) == [(0, 0)]

    def test_single_true_at_middle(self):
        assert _find_obs_segments(np.array([False, True, False])) == [(1, 1)]

    def test_single_true_at_end(self):
        assert _find_obs_segments(np.array([False, False, True])) == [(2, 2)]

    def test_two_segments(self):
        mask = np.array([True, True, False, True, True])
        assert _find_obs_segments(mask) == [(0, 1), (3, 4)]

    def test_three_segments(self):
        mask = np.array([True, False, True, False, True])
        assert _find_obs_segments(mask) == [(0, 0), (2, 2), (4, 4)]

    def test_head_and_tail(self):
        mask = np.array([True, False, False, True])
        assert _find_obs_segments(mask) == [(0, 0), (3, 3)]

    def test_empty_array(self):
        assert _find_obs_segments(np.array([], dtype=bool)) == []

    def test_single_element_false(self):
        assert _find_obs_segments(np.array([False])) == []

    def test_single_element_true(self):
        assert _find_obs_segments(np.array([True])) == [(0, 0)]

    def test_segment_length(self):
        """区間の端点インデックスが inclusive であることを確認。"""
        mask = np.array([False, True, True, True, False])
        segs = _find_obs_segments(mask)
        assert segs == [(1, 3)]
        s, e = segs[0]
        assert e - s + 1 == 3   # 3 要素分

    def test_large_array(self):
        mask = np.zeros(200, dtype=bool)
        mask[50:100] = True
        mask[150:200] = True
        segs = _find_obs_segments(mask)
        assert segs == [(50, 99), (150, 199)]


# ============================================================
# _make_params: env_type / rain_rate / diff_method は base 由来
# ============================================================
class TestMakeParams:

    def _base(self, env_type: str, rain_rate: float, diff_method: str,
              resolution: str = "") -> sim.SimParams:
        c = {
            "start": "34.54, 132.41", "end": "34.53, 132.40",
            "h_tx": "30.0", "h_rx": "10.0", "freq": "2400.0",
            "p_tx": "20.0", "gain_tx": "3.0", "gain_rx": "3.0",
            "sens": "-85.0", "veg_h": "10.0", "k_factor": "1.333",
            "samples": "50", "resolution": resolution,
            "env_type": env_type,
            "rain_rate": str(rain_rate),
            "diff_method": diff_method,
        }
        return sim.SimParams(c)

    def test_each_row_resolves_its_own_sample_count(self):
        """**行ごとに点数が解かれる**こと（I-069 のコア）。

        🔴 以前は共通設定の 1 つの数（既定 200）が**全行に一律で当たっていた**＝
        500m の行では実効 2.5m、20km の行では実効 100m（尾根を 1 点で跨ぐ）と、
        **同じ入力が行によって別の意味**になっていた。段階を渡す形にしたので、
        長い行ほど点数が増える。⚠️ ここが壊れると症状は「長距離だけ精度が落ちる」
        という、成果物を見ても気づけない形で出る。
        """
        base = self._base(env_type="los", rain_rate=0.0, diff_method="bullington",
                          resolution="medium")
        short = batch.PathRow(path_id="s", lat_tx=34.54, lon_tx=132.41,
                              lat_rx=34.545, lon_rx=132.41,
                              h_tx=30.0, h_rx=10.0)      # 約 0.55 km
        long_ = batch.PathRow(path_id="l", lat_tx=34.54, lon_tx=132.41,
                              lat_rx=34.72, lon_rx=132.41,
                              h_tx=30.0, h_rx=10.0)      # 約 20 km
        n_short = _make_params(short, base).num
        n_long  = _make_params(long_,  base).num
        assert n_long > n_short * 10, (
            f"行の長さが点数に効いていない（短 {n_short} / 長 {n_long}）"
        )
        # どちらも**同じ刻み**であること＝それが「段階」の意味。
        # 🔴 **見る量が変わった**（B-150）＝段階は等間隔ではなく **DEM 画素の縁**に
        # 標本を置くので、`距離 ÷ (点数−1)` は*平均*でしかない（画素ごとに違う）。
        # ⇒ 「1 画素あたり何点か」を見る＝行の長さが変わっても 2 点/画素で一定。
        # ⚠️ 刻みの値は**段階の側から引く**（数を書き写さない）＝B-148 で「中」の
        # 目標間隔が公称 10m から実 1px の半分へ変わったとき、ここに書いた 10.0 が
        # 古くなった（写した数はこうしてずれる）。
        for row, n in ((short, n_short), (long_, n_long)):
            dist_m = models.horizontal_distance_km(
                row.lat_tx, row.lon_tx, row.lat_rx, row.lon_rx) * 1000.0
            px_m = terrain_grid.grid_step_m(row.lat_tx, "medium")
            per_pixel = n / (dist_m / px_m)
            assert per_pixel == pytest.approx(2.0, rel=0.1), (
                f"{row.path_id}: 画素 1 つあたり {per_pixel:.2f} 点"
                "（縁の 2 点で刻んでいない）"
            )

    def test_env_rain_diff_always_from_base(self):
        """env_type / rain_rate / diff_method は PathRow にかかわらず base から取得されること。"""
        row = batch.PathRow(
            path_id="p1",
            lat_tx=34.54, lon_tx=132.41,
            lat_rx=34.53, lon_rx=132.40,
            h_tx=30.0, h_rx=10.0,
        )
        base = self._base(env_type="urban", rain_rate=50.0, diff_method="single")
        result = _make_params(row, base)
        assert result.env_type    == "urban"
        assert result.rain_rate   == pytest.approx(50.0)
        assert result.diff_method == "single"

    def test_freq_falls_back_to_base_when_none(self):
        """`freq_mhz=None` のとき base の周波数が使われること。"""
        row  = batch.PathRow(
            path_id="p1",
            lat_tx=34.54, lon_tx=132.41,
            lat_rx=34.53, lon_rx=132.40,
            h_tx=30.0, h_rx=10.0, freq_mhz=None,
        )
        base = self._base(env_type="los", rain_rate=0.0, diff_method="bullington")
        result = _make_params(row, base)
        assert result.freq_mhz == pytest.approx(2400.0)

    def test_freq_overrides_base_when_set(self):
        """`freq_mhz` が指定されているとき base の周波数より優先されること。"""
        row  = batch.PathRow(
            path_id="p1",
            lat_tx=34.54, lon_tx=132.41,
            lat_rx=34.53, lon_rx=132.40,
            h_tx=30.0, h_rx=10.0, freq_mhz=5800.0,
        )
        base = self._base(env_type="los", rain_rate=0.0, diff_method="bullington")
        result = _make_params(row, base)
        assert result.freq_mhz == pytest.approx(5800.0)

    def test_gain_falls_back_to_base_when_none(self):
        """`gain_tx`/`gain_rx=None` のとき base の利得が使われること（per-row 継承）。"""
        row = batch.PathRow(
            path_id="p1",
            lat_tx=34.54, lon_tx=132.41,
            lat_rx=34.53, lon_rx=132.40,
            h_tx=30.0, h_rx=10.0,
        )
        base = self._base(env_type="los", rain_rate=0.0, diff_method="bullington")
        result = _make_params(row, base)
        assert result.gain_tx == pytest.approx(3.0)
        assert result.gain_rx == pytest.approx(3.0)

    def test_gain_overrides_base_when_set(self):
        """`gain_tx`/`gain_rx` 指定時は base より優先されること（リンク識別属性）。"""
        row = batch.PathRow(
            path_id="p1",
            lat_tx=34.54, lon_tx=132.41,
            lat_rx=34.53, lon_rx=132.40,
            h_tx=30.0, h_rx=10.0, gain_tx=15.0, gain_rx=9.0,
        )
        base = self._base(env_type="los", rain_rate=0.0, diff_method="bullington")
        result = _make_params(row, base)
        assert result.gain_tx == pytest.approx(15.0)
        assert result.gain_rx == pytest.approx(9.0)


# ============================================================
# save_path_html の地図埋め込み（map_b64）
# ============================================================
def _make_result():
    return models.LinkBudgetResult(
        eirp=23.0, fspl=100.0, diff_loss=0.0, veg_loss=0.0,
        env_loss=6.0, rain_loss=0.0, gas_loss=0.0,
        total_loss=106.0, p_rx=-83.0,
        actual_margin=2.0, status="OK",
        blocked_ratio=0.0, slant_dist_km=1.0,
        diff_method="single", env_type="los",
    )


class TestCsvCellHelper:
    """report_common.csv_cell 単体（書き手が複数あるので規則をここで固定する）。"""

    def test_risky_prefixes_are_quoted(self):
        from report import report_common
        for text in ("=1+1", "+SUM(1)", "-1+1", "@x", "\t=x", "\r=x", "\n=x"):
            assert report_common.csv_cell(text) == "'" + text

    def test_leading_whitespace_does_not_bypass(self):
        """先頭空白で判定を回避できないこと（2026-07-26 Codex レビュー指摘）。

        表計算ソフトは前置の空白を無視して数式評価し得るので、`" =1+1"` は
        素通しできない。`'` は元の文字列に付ける＝空白ごと文字列として見せる。
        """
        from report import report_common
        for text in (" =1+1", "  =HYPERLINK(\"http://x\")", "\t @SUM(1)"):
            assert report_common.csv_cell(text) == "'" + text

    def test_leading_whitespace_number_still_passes(self):
        """空白始まりでも数値なら数式ではない＝クォートしない。"""
        from report import report_common
        assert report_common.csv_cell(" -93.20") == " -93.20"

    def test_numeric_values_pass_through(self):
        from report import report_common
        for text in ("-93.20", "-0.5", "+12", "-1e3"):
            assert report_common.csv_cell(text) == text

    def test_none_and_plain_text(self):
        from report import report_common
        assert report_common.csv_cell(None) == ""
        assert report_common.csv_cell("p01") == "p01"
