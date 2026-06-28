"""
tests/test_coords.py
====================
coords.py（DD/DMS 座標表記変換）の純関数テスト。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import coords


class TestParsePair:

    def test_parse_dd(self):
        lat, lon = coords.parse_pair("34.5429, 132.4118")
        assert lat == pytest.approx(34.5429)
        assert lon == pytest.approx(132.4118)

    def test_parse_negative_dd(self):
        lat, lon = coords.parse_pair("-34.5, -118.25")
        assert lat == pytest.approx(-34.5)
        assert lon == pytest.approx(-118.25)

    def test_parse_dms_with_symbols(self):
        lat, lon = coords.parse_pair("34°32'34.4\"N, 132°24'42.5\"E")
        assert lat == pytest.approx(34.542889, abs=1e-5)
        assert lon == pytest.approx(132.411806, abs=1e-5)

    def test_parse_dms_southern_western_hemisphere(self):
        lat, lon = coords.parse_pair("34°30'00.0\"S, 118°15'00.0\"W")
        assert lat == pytest.approx(-34.5)
        assert lon == pytest.approx(-118.25)

    def test_parse_dms_whitespace_separated(self):
        lat, lon = coords.parse_pair("34 32 34.4 N, 132 24 42.5 E")
        assert lat == pytest.approx(34.542889, abs=1e-5)
        assert lon == pytest.approx(132.411806, abs=1e-5)

    def test_parse_dms_degrees_minutes_only(self):
        lat, lon = coords.parse_pair("34°30'N, 132°15'E")
        assert lat == pytest.approx(34.5)
        assert lon == pytest.approx(132.25)

    def test_missing_comma_raises(self):
        with pytest.raises(ValueError):
            coords.parse_pair("34.5429 132.4118")

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            coords.parse_pair("hello, world")


class TestFormat:

    def test_format_dd_precision(self):
        assert coords.format_dd(34.5429, 132.4118) == "34.542900, 132.411800"

    def test_format_dms_basic(self):
        out = coords.format_dms(34.5, 132.25)
        assert out == "34°30'00.0\"N, 132°15'00.0\"E"

    def test_format_dms_southern_western(self):
        out = coords.format_dms(-34.5, -118.25)
        assert out == "34°30'00.0\"S, 118°15'00.0\"W"

    def test_format_dms_second_carry(self):
        # 59.97 秒は四捨五入で 60.0 → 桁上げされ 0.0 秒 + 1 分になる
        out = coords.format_dms(34.0 + 59.0 / 60.0 + 59.97 / 3600.0, 132.0)
        assert "60.0" not in out


class TestRoundtrip:

    @pytest.mark.parametrize("lat,lon", [
        (34.5429, 132.4118),
        (-34.5, -118.25),
        (0.0, 0.0),
        (45.123456, -179.987654),
    ])
    def test_dd_dms_dd_roundtrip(self, lat, lon):
        dms = coords.format_dms(lat, lon)
        back_lat, back_lon = coords.parse_pair(dms)
        # 秒小数1桁の丸めで ~0.0003 度（約 30m）以内に戻る
        assert back_lat == pytest.approx(lat, abs=5e-4)
        assert back_lon == pytest.approx(lon, abs=5e-4)


class TestFormatPair:

    def test_dd(self):
        assert coords.format_pair(34.5, 132.25, "dd") == "34.500000, 132.250000"

    def test_dms(self):
        assert coords.format_pair(34.5, 132.25, "dms") == "34°30'00.0\"N, 132°15'00.0\"E"

    def test_unknown_format_falls_back_to_dd(self):
        assert coords.format_pair(34.5, 132.25, "xyz") == "34.500000, 132.250000"


class TestReformatAndToDd:

    def test_reformat_dd_to_dms(self):
        assert coords.reformat("34.5, 132.25", "dms") == "34°30'00.0\"N, 132°15'00.0\"E"

    def test_reformat_dms_to_dd(self):
        out = coords.reformat("34°30'00.0\"N, 132°15'00.0\"E", "dd")
        assert out == "34.500000, 132.250000"

    def test_reformat_unparseable_returns_original(self):
        assert coords.reformat("not a coord", "dms") == "not a coord"

    def test_to_dd_str_from_dms(self):
        assert coords.to_dd_str("34°30'00.0\"N, 132°15'00.0\"E") == "34.500000, 132.250000"

    def test_to_dd_str_passthrough_on_bad_input(self):
        assert coords.to_dd_str("garbage input") == "garbage input"
