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

from core import units  # noqa: E402


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


# ============================================================
# F1 遮蔽率の表示クランプ（I-018 / 2.5a3）
# ============================================================
class TestFormatBlockedRatio:
    """「率(%)」と名乗る面では 100% を超えさせない（表示のみ・models は生値）。"""

    def test_normal_value_passes_through(self):
        assert units.format_blocked_ratio(63.2) == "63.2 %"

    def test_zero(self):
        assert units.format_blocked_ratio(0.0) == "0.0 %"

    @pytest.mark.parametrize("raw", [100.0001, 150.0, 7109.9, 1e6])
    def test_clamped_at_100(self, raw):
        """深い山越えは F1 半径の数十倍食い込む＝率として出すと誤読される。"""
        assert units.format_blocked_ratio(raw) == "100.0 %"

    def test_exactly_100_is_not_altered(self):
        assert units.format_blocked_ratio(100.0) == "100.0 %"

    def test_no_unit_variant_for_tables_with_unit_in_header(self):
        assert units.format_blocked_ratio(7109.9, unit=False) == "100.0"

    def test_csv_matches_display(self):
        """CSV と台帳で値が食い違わないこと（同じ上限を通す）。"""
        for raw in (12.3, 100.0, 7109.9):
            assert units.csv_blocked_ratio(raw) == \
                units.format_blocked_ratio(raw, unit=False)

    def test_csv_has_no_unit_or_grouping(self):
        assert units.csv_blocked_ratio(7109.9) == "100.0"


class TestBlockedRatioFormattingIsNotScattered:
    """遮蔽率の書式が表示側のインライン f-string へ戻らないこと。

    距離書式（I-014）と同じ再発の型＝置き場が無いと次の表示追加でまた散り、
    ある面だけクランプされない状態が生まれる。**models は生値のままにする**
    設計なので、素の `blocked_ratio` を直接書式化する面が 1 つでもあると
    そこだけ 7109.9% が出る。
    """

    _DISPLAY_MODULES = [
        "report/report_summary.py", "core/simulation.py",
        "views/graph.py", "report/report_path.py",
    ]

    @pytest.mark.parametrize("mod", _DISPLAY_MODULES)
    def test_no_inline_format_of_blocked_ratio(self, mod):
        import re
        root = os.path.join(os.path.dirname(__file__), "..")
        text = open(os.path.join(root, mod), encoding="utf-8").read()
        # `{...blocked_ratio...:...}` のうち units を通していないものを検出
        # （units.format_blocked_ratio(...) の結果に幅指定を足すのは整形ではない）。
        bad = [m for m in re.findall(r"\{[^{}]*blocked_ratio[^{}]*:[^{}]*\}", text)
               if "units." not in m]
        assert not bad, f"{mod}: 遮蔽率をインライン整形している {bad}（units へ寄せる）"

    def test_models_keeps_the_raw_value(self):
        """クランプは表示だけ＝models の値は生のまま（情報を捨てない）。"""
        import numpy as np

        from core import models
        raw = np.zeros(120)
        raw[55:65] = 400.0          # 深い尾根＝F1 半径を大きく超える
        terrain = models.calculate_terrain_profile(raw, 34.54, 132.41, 34.53, 132.40)
        prop = models.calculate_propagation(
            terrain=terrain, h_tx=5.0, h_rx=5.0, freq_mhz=2400.0,
            veg_h=0.0, initial_k=1.33, diff_method="deygout",
            env_type="rural", rain_rate=0.0,
        )
        assert prop.blocked_ratio > units.BLOCKED_RATIO_MAX, \
            "テスト地形が浅すぎる（クランプの検証にならない）"
        assert units.format_blocked_ratio(prop.blocked_ratio) == "100.0 %"
