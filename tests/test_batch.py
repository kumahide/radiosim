"""
tests/test_batch.py
===================
batch.py のユニットテスト。
対象: validate_rows / parse_csv / _make_params / 実行エンジン
（run_batch / _process_one / _fetch_sync ＝ DEM 取得を monkeypatch し
ネットワーク無しで同期検証）
"""

import threading
from typing import Any

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import batch
import i18n
import config
import models
import report
import simulation as sim
from batch import _make_params
from report import _find_obs_segments


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


def _csv_file(tmp_path, content: str) -> str:
    """内容を書いた一時 CSV ファイルのパスを返す。"""
    p = tmp_path / "test.csv"
    p.write_text(content, encoding="utf-8")
    return str(p)


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
# parse_csv
# ============================================================

class TestParseCsv:

    _HEADER = "id,start,end,h_tx,h_rx"

    def _make_row(self, id_="p01", start="34.54, 132.41", end="34.53, 132.40",
                  h_tx="30.0", h_rx="10.0", extra=""):
        # 座標フィールドはカンマを含むため CSV クォートが必要
        return f'"{id_}","{start}","{end}",{h_tx},{h_rx}{extra}\n'

    def test_minimal_valid(self, tmp_path):
        content = self._HEADER + "\n" + self._make_row()
        rows = batch.parse_csv(_csv_file(tmp_path, content))
        assert len(rows) == 1
        assert rows[0].path_id == "p01"
        assert rows[0].lat_tx  == pytest.approx(34.54)
        assert rows[0].lon_tx  == pytest.approx(132.41)
        assert rows[0].h_tx    == pytest.approx(30.0)
        assert rows[0].freq_mhz is None

    def test_multiple_rows(self, tmp_path):
        content = self._HEADER + "\n" + self._make_row("p01") + self._make_row("p02")
        rows = batch.parse_csv(_csv_file(tmp_path, content))
        assert len(rows) == 2
        assert rows[1].path_id == "p02"

    def test_optional_freq_parsed(self, tmp_path):
        content = "id,start,end,h_tx,h_rx,freq\n" + self._make_row(extra=",2400")
        rows = batch.parse_csv(_csv_file(tmp_path, content))
        assert rows[0].freq_mhz == pytest.approx(2400.0)

    def test_optional_freq_empty_is_none(self, tmp_path):
        content = "id,start,end,h_tx,h_rx,freq\n" + self._make_row(extra=",")
        rows = batch.parse_csv(_csv_file(tmp_path, content))
        assert rows[0].freq_mhz is None

    def test_optional_note_parsed(self, tmp_path):
        content = "id,start,end,h_tx,h_rx,note\n" + self._make_row(extra=",test note")
        rows = batch.parse_csv(_csv_file(tmp_path, content))
        assert rows[0].note == "test note"

    def test_optional_gain_parsed(self, tmp_path):
        content = ("id,start,end,h_tx,h_rx,freq,gain_tx,gain_rx,note\n"
                   + self._make_row(extra=",2400,12.5,8.0,note"))
        rows = batch.parse_csv(_csv_file(tmp_path, content))
        assert rows[0].gain_tx == pytest.approx(12.5)
        assert rows[0].gain_rx == pytest.approx(8.0)

    def test_legacy_csv_without_gain_columns(self, tmp_path):
        """gain 列のない旧 CSV は後方互換で読め、gain は None（base 継承）になること。"""
        content = "id,start,end,h_tx,h_rx,freq,note\n" + self._make_row(extra=",2400,old")
        rows = batch.parse_csv(_csv_file(tmp_path, content))
        assert rows[0].freq_mhz == pytest.approx(2400.0)
        assert rows[0].gain_tx is None
        assert rows[0].gain_rx is None
        assert rows[0].note == "old"

    def test_optional_gain_empty_is_none(self, tmp_path):
        content = ("id,start,end,h_tx,h_rx,gain_tx,gain_rx\n"
                   + self._make_row(extra=",,"))
        rows = batch.parse_csv(_csv_file(tmp_path, content))
        assert rows[0].gain_tx is None
        assert rows[0].gain_rx is None

    def test_empty_file_raises(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="header"):
            batch.parse_csv(str(p))

    def test_missing_required_column_raises(self, tmp_path):
        content = "id,start,end,h_tx\n" + "p01,34.54,132.41,34.53,132.40,30\n"
        with pytest.raises(ValueError, match="[Mm]issing"):
            batch.parse_csv(_csv_file(tmp_path, content))

    def test_no_data_rows_raises(self, tmp_path):
        content = self._HEADER + "\n"
        with pytest.raises(ValueError, match="[Nn]o data"):
            batch.parse_csv(_csv_file(tmp_path, content))

    def test_empty_id_raises(self, tmp_path):
        content = self._HEADER + "\n" + self._make_row(id_="")
        with pytest.raises(ValueError, match="empty|is empty"):
            batch.parse_csv(_csv_file(tmp_path, content))

    def test_invalid_coord_format_raises(self, tmp_path):
        content = self._HEADER + "\n" + self._make_row(start="notacoord")
        with pytest.raises(ValueError, match="[Ff]ormat"):
            batch.parse_csv(_csv_file(tmp_path, content))

    def test_invalid_h_tx_raises(self, tmp_path):
        content = self._HEADER + "\n" + self._make_row(h_tx="abc")
        with pytest.raises(ValueError, match="[Nn]umber|not a number"):
            batch.parse_csv(_csv_file(tmp_path, content))

    def test_negative_h_rx_parsed(self, tmp_path):
        """parse_csv は値の範囲チェックをしない（validate_rows が担う）。"""
        content = self._HEADER + "\n" + self._make_row(h_rx="-5.0")
        rows = batch.parse_csv(_csv_file(tmp_path, content))
        assert rows[0].h_rx == pytest.approx(-5.0)

    def test_bom_utf8_header(self, tmp_path):
        """UTF-8 BOM 付き CSV でも正常にパースできる。"""
        content = self._HEADER + "\n" + self._make_row()
        p = tmp_path / "bom.csv"
        p.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))
        rows = batch.parse_csv(str(p))
        assert rows[0].path_id == "p01"


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

    def _base(self, env_type: str, rain_rate: float, diff_method: str) -> sim.SimParams:
        c = {
            "start": "34.54, 132.41", "end": "34.53, 132.40",
            "h_tx": "30.0", "h_rx": "10.0", "freq": "2400.0",
            "p_tx": "20.0", "gain_tx": "3.0", "gain_rx": "3.0",
            "sens": "-85.0", "veg_h": "10.0", "k_factor": "1.333",
            "samples": "50",
            "env_type": env_type,
            "rain_rate": str(rain_rate),
            "diff_method": diff_method,
        }
        return sim.SimParams(c)

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
        base = self._base(env_type="los", rain_rate=0.0, diff_method="deygout")
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
        base = self._base(env_type="los", rain_rate=0.0, diff_method="deygout")
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
        base = self._base(env_type="los", rain_rate=0.0, diff_method="deygout")
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
        base = self._base(env_type="los", rain_rate=0.0, diff_method="deygout")
        result = _make_params(row, base)
        assert result.gain_tx == pytest.approx(15.0)
        assert result.gain_rx == pytest.approx(9.0)


# ============================================================
# export_csv ラウンドトリップ
# ============================================================
class TestExportCsvRoundtrip:

    def test_roundtrip_preserves_values(self, tmp_path):
        """export → parse で全フィールドが保持されること。"""
        rows = [
            batch.PathRow("p01", 34.54, 132.41, 34.53, 132.40, 30.0, 10.0,
                          freq_mhz=2400.0, gain_tx=12.5, gain_rx=8.0, note="Main"),
            batch.PathRow("p02", 34.55, 132.42, 34.52, 132.39, 20.0, 15.0,
                          freq_mhz=None, gain_tx=None, gain_rx=None, note=""),
        ]
        csv_path = str(tmp_path / "out.csv")
        batch.export_csv(rows, csv_path)
        reloaded = batch.parse_csv(csv_path)

        assert len(reloaded) == 2
        assert reloaded[0].path_id  == "p01"
        assert reloaded[0].lat_tx   == pytest.approx(34.54)
        assert reloaded[0].lon_tx   == pytest.approx(132.41)
        assert reloaded[0].h_tx     == pytest.approx(30.0)
        assert reloaded[0].freq_mhz == pytest.approx(2400.0)
        assert reloaded[0].gain_tx  == pytest.approx(12.5)
        assert reloaded[0].gain_rx  == pytest.approx(8.0)
        assert reloaded[0].note     == "Main"
        assert reloaded[1].freq_mhz is None
        assert reloaded[1].gain_tx  is None
        assert reloaded[1].gain_rx  is None
        assert reloaded[1].note     == ""


# ============================================================
# save_path_html の地図埋め込み（map_b64）
# ============================================================
def _make_result():
    return models.LinkBudgetResult(
        eirp=23.0, fspl=100.0, diff_loss=0.0, veg_loss=0.0,
        env_loss=6.0, rain_loss=0.0, gas_loss=0.0,
        total_loss=106.0, p_rx=-83.0,
        actual_margin=2.0, status="OK",
        current_k=10.0, blocked_ratio=0.0, slant_dist_km=1.0,
        diff_method="single", env_type="los",
    )


class TestSavePathHtmlMap:

    def _render(self, tmp_path, flat_terrain, default_params_dict, map_b64):
        i18n.set_lang("en")
        params = sim.SimParams(default_params_dict)
        report.save_path_html(
            flat_terrain, _make_result(), params, 30.0, 10.0,
            str(tmp_path), "TERRAINB64", map_b64=map_b64,
        )
        with open(os.path.join(str(tmp_path), "report.html"), encoding="utf-8") as f:
            return f.read()

    def test_map_present_embeds_second_image(self, tmp_path, flat_terrain,
                                              default_params_dict):
        html = self._render(tmp_path, flat_terrain, default_params_dict, "MAPB64DATA")
        assert "data:image/png;base64,MAPB64DATA" in html
        assert "Map unavailable" not in html

    def test_map_none_shows_note_and_no_map_image(self, tmp_path, flat_terrain,
                                                  default_params_dict):
        html = self._render(tmp_path, flat_terrain, default_params_dict, None)
        assert "Map unavailable" in html
        assert "data:image/png;base64,MAPB64DATA" not in html
        # 地形グラフ自体は常に埋め込まれる。
        assert "data:image/png;base64,TERRAINB64" in html


class TestSavePathHtmlCoordFormat:
    """HTML レポートの座標セルが coord_format に従うこと（既定 DD）。"""

    def _render(self, tmp_path, flat_terrain, default_params_dict, coord_format):
        i18n.set_lang("en")
        params = sim.SimParams(default_params_dict)
        report.save_path_html(
            flat_terrain, _make_result(), params, 30.0, 10.0,
            str(tmp_path), "TERRAINB64", map_b64=None, coord_format=coord_format,
        )
        with open(os.path.join(str(tmp_path), "report.html"), encoding="utf-8") as f:
            return f.read()

    def test_default_dd(self, tmp_path, flat_terrain, default_params_dict):
        html = self._render(tmp_path, flat_terrain, default_params_dict, "dd")
        assert "34.542900, 132.411800" in html
        assert "°" not in html

    def test_dms(self, tmp_path, flat_terrain, default_params_dict):
        html = self._render(tmp_path, flat_terrain, default_params_dict, "dms")
        assert "34°32'34.4\"N, 132°24'42.5\"E" in html


# ============================================================
# サマリ出力に gain 列が含まれること（Phase D1）
# ============================================================
class TestSummaryGainColumns:

    def _result(self, default_params_dict, gain_tx="9.0", gain_rx="6.0"):
        d = dict(default_params_dict)
        d["gain_tx"] = gain_tx
        d["gain_rx"] = gain_rx
        params = sim.SimParams(d)
        row = batch.PathRow("p01", 34.54, 132.41, 34.53, 132.40, 30.0, 10.0,
                            gain_tx=float(gain_tx), gain_rx=float(gain_rx))
        return batch.PathResult(row=row, result=_make_result(), params=params)

    def test_summary_csv_has_gain_columns(self, tmp_path, default_params_dict):
        import csv as _csv
        report._save_summary_csv([self._result(default_params_dict)], str(tmp_path))
        with open(os.path.join(str(tmp_path), "summary.csv"), encoding="utf-8") as f:
            reader = _csv.reader(f)
            header = next(reader)
            data   = next(reader)
        assert "gain_tx_dbi" in header
        assert "gain_rx_dbi" in header
        assert data[header.index("gain_tx_dbi")] == "9.0"
        assert data[header.index("gain_rx_dbi")] == "6.0"

    def test_summary_html_has_gain_headers(self, tmp_path, default_params_dict):
        i18n.set_lang("en")
        report.save_summary_html([self._result(default_params_dict)], str(tmp_path))
        with open(os.path.join(str(tmp_path), "summary.html"), encoding="utf-8") as f:
            html = f.read()
        assert "TX Gain (dBi)" in html
        assert "RX Gain (dBi)" in html


# ============================================================
# レポート v2 ＝ A4 ドロップイン骨格（per-path / summary 共通）
# ============================================================
class TestReportV2A4Skeleton:
    """生成 HTML が portrait A4 の印刷確定枠（.sheet＋自己同定ヘッダ/フッタ）を
    持つこと。骨格土台化スライスの回帰ガード。"""

    def _path_html(self, tmp_path, flat_terrain, default_params_dict):
        i18n.set_lang("en")
        params = sim.SimParams(default_params_dict)
        report.save_path_html(
            flat_terrain, _make_result(), params, 30.0, 10.0,
            str(tmp_path), "TERRAINB64", map_b64=None,
        )
        with open(os.path.join(str(tmp_path), "report.html"), encoding="utf-8") as f:
            return f.read()

    def _summary_html(self, tmp_path, default_params_dict):
        i18n.set_lang("en")
        params = sim.SimParams(default_params_dict)
        row = batch.PathRow("p01", 34.54, 132.41, 34.53, 132.40, 30.0, 10.0)
        pr  = batch.PathResult(row=row, result=_make_result(), params=params)
        report.save_summary_html([pr], str(tmp_path))
        with open(os.path.join(str(tmp_path), "summary.html"), encoding="utf-8") as f:
            return f.read()

    def _assert_a4_frame(self, html):
        # portrait A4 の @page 宣言
        assert "@page" in html
        assert "A4 portrait" in html
        # 本文が A4 用紙（.sheet）で包まれている
        assert 'class="sheet"' in html
        # 自己同定ヘッダ/フッタ
        assert "page-header" in html
        assert "page-footer" in html
        # 案件名は本スライスでは空スロット（データ配線は次スライス）
        assert 'class="proj-name"' in html

    def test_path_html_has_a4_frame(self, tmp_path, flat_terrain, default_params_dict):
        self._assert_a4_frame(self._path_html(tmp_path, flat_terrain, default_params_dict))

    def test_summary_html_has_a4_frame(self, tmp_path, default_params_dict):
        self._assert_a4_frame(self._summary_html(tmp_path, default_params_dict))

    def test_summary_table_repeats_header_and_avoids_row_break(
        self, tmp_path, default_params_dict
    ):
        # 継続ページで thead 反復・行分断防止（1枚厳守が希望・超過時のみ継続）
        html = self._summary_html(tmp_path, default_params_dict)
        assert "table-header-group" in html
        assert "break-inside:avoid" in html


# ============================================================
# 実行エンジン（run_batch / _run_thread / _process_one / _fetch_sync）
# ============================================================
# sim.fetch_elevations の同期フェイク。バッチはワーカースレッドから呼ぶが、
# コールバックを即時発火させることで threading.Event の同期化ごと検証する。
# freq=999.0 の行だけ取得失敗にし、パス単位の失敗系を決定的に再現する。
_FAIL_FREQ = 999.0


def _fake_fetch(params, on_progress, on_complete, on_error):
    on_progress(50)
    if params.freq_mhz == _FAIL_FREQ:
        on_error(RuntimeError("DEM fetch failed (fake)"))
    else:
        on_complete(np.zeros(params.num))


class TestFetchSync:

    def test_returns_array_on_complete(self, default_params_dict, monkeypatch):
        monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)
        params = sim.SimParams(default_params_dict)
        progress: list[int] = []
        arr = batch._fetch_sync(params, progress.append)
        assert isinstance(arr, np.ndarray)
        assert len(arr) == params.num
        assert progress == [50]   # on_progress が素通しされる

    def test_raises_first_error(self, default_params_dict, monkeypatch):
        monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)
        d = dict(default_params_dict, freq=str(_FAIL_FREQ))
        with pytest.raises(RuntimeError, match="DEM fetch failed"):
            batch._fetch_sync(sim.SimParams(d), lambda p: None)


class TestProcessOne:

    def test_success_returns_ok_and_writes_artifacts(
            self, tmp_path, default_params_dict, monkeypatch):
        i18n.set_lang("en")
        monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)
        base = sim.SimParams(default_params_dict)
        pr = batch._process_one(_row(), base, str(tmp_path), lambda p: None)
        assert pr.ok and pr.error is None
        assert pr.terrain is not None and pr.params is not None
        assert pr.save_dir == os.path.join(str(tmp_path), "path01")
        produced = set(os.listdir(pr.save_dir))
        # PNG/HTML/KML はメインスレッド側（save_path_visuals）の責務なのでここには無い。
        assert {"report.txt", "terrain_profile.csv"} <= produced

    def test_fetch_failure_is_contained_in_result(
            self, tmp_path, default_params_dict, monkeypatch):
        """DEM 取得失敗は例外を漏らさず PathResult(error=...) に封じ込める。"""
        monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)
        base = sim.SimParams(default_params_dict)
        pr = batch._process_one(
            _row(path_id="bad", freq_mhz=_FAIL_FREQ), base, str(tmp_path),
            lambda p: None,
        )
        assert not pr.ok
        assert isinstance(pr.error, RuntimeError)
        assert pr.save_dir == ""
        assert not os.path.exists(os.path.join(str(tmp_path), "bad"))


class TestRunBatch:

    def _run(self, rows, tmp_path, default_params_dict, monkeypatch):
        """run_batch を実行し、全コールバックの記録を返す（完了まで待機）。"""
        i18n.set_lang("en")
        monkeypatch.setattr(sim, "fetch_elevations", _fake_fetch)
        monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path))
        base = sim.SimParams(default_params_dict)
        ev: dict[str, list] = {"start": [], "complete": [], "batch": [], "error": []}
        done = threading.Event()

        def _on_batch(batch_dir, results):
            ev["batch"].append((batch_dir, results))
            done.set()

        def _on_error(ex):
            ev["error"].append(ex)
            done.set()

        batch.run_batch(
            rows, base,
            on_path_start    = lambda i, n, pid: ev["start"].append((i, n, pid)),
            on_path_progress = lambda p: None,
            on_path_complete = lambda i, n, pr: ev["complete"].append((i, n, pr)),
            on_batch_complete = _on_batch,
            on_error          = _on_error,
        )
        assert done.wait(timeout=30), "batch thread did not finish in time"
        return ev

    def test_happy_path_callbacks_and_summary(
            self, tmp_path, default_params_dict, monkeypatch):
        rows = [_row(), _row(path_id="path02")]
        ev = self._run(rows, tmp_path, default_params_dict, monkeypatch)
        assert ev["error"] == []
        assert ev["start"] == [(1, 2, "path01"), (2, 2, "path02")]
        assert [(i, n) for i, n, _ in ev["complete"]] == [(1, 2), (2, 2)]
        (batch_dir, results), = ev["batch"]
        assert os.path.dirname(batch_dir) == str(tmp_path)
        assert os.path.basename(batch_dir).startswith("batch_")
        assert [pr.ok for pr in results] == [True, True]
        assert os.path.exists(os.path.join(batch_dir, "summary.csv"))

    def test_per_path_failure_does_not_abort_batch(
            self, tmp_path, default_params_dict, monkeypatch):
        """1 パスの失敗は結果に残し、残りのパスとサマリ生成は続行する。"""
        rows = [_row(), _row(path_id="bad", freq_mhz=_FAIL_FREQ),
                _row(path_id="path03")]
        ev = self._run(rows, tmp_path, default_params_dict, monkeypatch)
        assert ev["error"] == []
        (batch_dir, results), = ev["batch"]
        assert [pr.ok for pr in results] == [True, False, True]
        assert isinstance(results[1].error, RuntimeError)
        assert os.path.exists(os.path.join(batch_dir, "summary.csv"))

    def test_engine_failure_calls_on_error(
            self, tmp_path, default_params_dict, monkeypatch):
        """パス処理より外側の失敗（サマリ書き出し等）は on_error に届く。"""
        monkeypatch.setattr(
            report, "_save_summary_csv",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")),
        )
        ev = self._run([_row()], tmp_path, default_params_dict, monkeypatch)
        assert ev["batch"] == []
        assert len(ev["error"]) == 1
        assert "disk full" in str(ev["error"][0])


class TestParseCsvOptionalColumns:

    def _raw(self, **overrides) -> dict:
        raw = {
            "id": "p1", "start": "34.54, 132.41", "end": "34.53, 132.40",
            "h_tx": "30", "h_rx": "10",
        }
        raw.update(overrides)
        return raw

    def test_optional_float_bad_value_reports_line_and_key(self):
        with pytest.raises(ValueError, match=r"Row 2: 'gain_tx'"):
            batch._parse_csv_row(self._raw(gain_tx="abc"), line=2)

    def test_optional_float_blank_is_none(self):
        row = batch._parse_csv_row(self._raw(gain_tx="", freq=" "), line=2)
        assert row.gain_tx is None and row.freq_mhz is None
