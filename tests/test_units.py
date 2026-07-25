"""
tests/test_units.py
===================
距離の表示整形（units.py）の検証。

守っている設計（I-014 / 2.5a1）:
  - 人が読む距離は **m 固定**。「1km 未満だけ m」のような分岐は持たない
    （旧 map_graphics.distance_text の挙動＝表示単位が値で変わっていた）。
  - 内部・物理式は km 据え置き＝換算は表示層でのみ行う。
  - 人が読む面は桁区切りあり、CSV は生値（表計算が数値として読める形）。
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import units  # noqa: E402


class TestKmToM:
    def test_scalar(self):
        assert units.km_to_m(1.5) == 1500.0

    def test_zero(self):
        assert units.km_to_m(0.0) == 0.0

    def test_array_is_converted_elementwise(self):
        """グラフの距離軸をまとめて換算するので配列も通ること。"""
        out = units.km_to_m(np.array([0.0, 0.5, 2.0]))
        assert isinstance(out, np.ndarray)
        assert np.allclose(out, [0.0, 500.0, 2000.0])


class TestFormatDistance:
    def test_meters_with_unit(self):
        assert units.format_distance(0.5) == "500 m"

    def test_digit_grouping(self):
        assert units.format_distance(20.153) == "20,153 m"

    def test_no_unit_variant_for_tables_with_unit_in_header(self):
        assert units.format_distance(20.153, unit=False) == "20,153"

    @pytest.mark.parametrize("km", [0.0005, 0.5, 1.0, 2.345, 20.153, 120.0])
    def test_always_meters_never_km(self, km):
        """値の大小で単位が切り替わらないこと（旧実装の 1km 分岐の回帰ガード）。"""
        text = units.format_distance(km)
        assert text.endswith(" m")
        assert "km" not in text

    def test_rounds_to_whole_meters(self):
        assert units.format_distance(1.0004) == "1,000 m"
        assert units.format_distance(1.0006) == "1,001 m"


class TestCsvDistance:
    def test_no_grouping_so_spreadsheets_read_it_as_a_number(self):
        assert units.csv_distance(20.153) == "20153"

    def test_no_unit_suffix(self):
        assert units.csv_distance(2.0) == "2000"

    def test_decimals_keep_sub_meter_resolution(self):
        assert units.csv_distance(0.0123, decimals=1) == "12.3"
