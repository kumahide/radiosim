"""
tests/test_batch.py
===================
batch.py のユニットテスト。
対象: validate_rows / parse_csv / _find_obs_segments
"""

from typing import Any

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import batch
import i18n
import models
import simulation as sim
from batch import _find_obs_segments, _make_params


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


# ============================================================
# export_csv ラウンドトリップ
# ============================================================
class TestExportCsvRoundtrip:

    def test_roundtrip_preserves_values(self, tmp_path):
        """export → parse で全フィールドが保持されること。"""
        rows = [
            batch.PathRow("p01", 34.54, 132.41, 34.53, 132.40, 30.0, 10.0, 2400.0, "Main"),
            batch.PathRow("p02", 34.55, 132.42, 34.52, 132.39, 20.0, 15.0, None,   ""),
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
        assert reloaded[0].note     == "Main"
        assert reloaded[1].freq_mhz is None
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
        batch.save_path_html(
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
        batch.save_path_html(
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
