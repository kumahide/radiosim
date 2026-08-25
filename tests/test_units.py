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


# ============================================================
# F1 侵入深さ（I-077 / 3.0a1）
# ============================================================
class TestFormatF1Depth:
    """頭打ちで消えていた情報を、**単位の違う別の量**として出す。

    争点は「100%」が *ちょうど完全遮蔽* と *深く突き抜けている* の 2 つの意味を
    持つこと（B-032 の発散は 3.0a1 で塞いだので、発散の印としては要らない）。
    """

    def test_it_is_the_ratio_in_units_of_the_f1_radius(self):
        assert units.f1_depth(250.0) == 2.5
        assert units.format_f1_depth(250.0) == "2.50 ×F1"

    def test_below_full_obstruction(self):
        assert units.format_f1_depth(63.2) == "0.63 ×F1"

    def test_zero(self):
        assert units.format_f1_depth(0.0) == "0.00 ×F1"

    @pytest.mark.parametrize("raw", [150.0, 7109.9, 1e6])
    def test_never_clamped(self, raw):
        """🔑 **こちらは頭打ちしない**（頭打ちする側は `format_blocked_ratio`）。"""
        assert float(units.format_f1_depth(raw, unit=False)) > 1.0
        assert units.f1_depth(raw) == raw / 100.0

    def test_the_printed_resolution_is_one_percent(self):
        """⚠️ **2 桁なので 1% 未満の超過は `1.00` に丸まる**（書式も契約＝規約 3）。

        この量が意味を持つのは 1.0 を超えてからなので、桁の少なさを取っている
        （率が 0.1% 刻みなのに対し、こちらは 1% 刻み相当）。
        """
        assert units.format_f1_depth(100.0001, unit=False) == "1.00"
        assert units.f1_depth(100.0001) > 1.0, "生値のほうは丸めていない"

    def test_no_unit_variant_for_tables_with_unit_in_header(self):
        assert units.format_f1_depth(7109.9, unit=False) == "71.10"

    def test_csv_matches_display(self):
        for raw in (12.3, 100.0, 7109.9):
            assert units.csv_f1_depth(raw) == units.format_f1_depth(raw, unit=False)

    def test_it_separates_what_the_ratio_cannot(self):
        """I-077 の本体＝**率が同じ字になる 2 つの経路が、深さでは分かれる**こと。"""
        exact, deep = 100.0, 2500.0
        assert units.format_blocked_ratio(exact) == units.format_blocked_ratio(deep)
        assert units.format_f1_depth(exact) != units.format_f1_depth(deep)
        assert units.format_f1_depth(exact) == "1.00 ×F1"

    def test_the_unit_is_not_a_percent(self):
        """⚠️ **率を 2 列並べない**のが処方の芯（単位が違えば取り違えない）。"""
        assert "%" not in units.format_f1_depth(7109.9)
        assert units.F1_DEPTH_UNIT in units.format_f1_depth(7109.9)


def _shows(text: str) -> tuple[bool, bool]:
    """ソース 1 本が「率を出しているか / 深さを出しているか」を返す。

    判定を関数に切り出してあるのは、**この判定そのものを変異検証できる**ように
    するため（下の `test_the_face_detector_catches_what_it_claims`）。
    """
    import re
    return (
        re.search(r"units\.(format|csv)_blocked_ratio", text) is not None,
        re.search(r"units\.(format|csv)_f1_depth", text) is not None,
    )


class TestEveryFaceThatShowsTheRatioAlsoShowsTheDepth:
    """**率を出す面は、必ず深さも出す**（I-077 のクラス点検を機械で持つ）。

    ⚠️ 面を列挙して数えると、**次に足した 1 面で穴が開く**
    （→ [[feedback-user-examples-are-classes]]）ので、**実装側を数えてから引く**
    ＝率を呼んでいるファイルを見つけ、そのファイルに深さが無ければ落とす。
    率を出さない面（per-path レポート＝I-099）には何も要求しない。
    """

    @staticmethod
    def _sources():
        root = os.path.join(os.path.dirname(__file__), "..")
        out = []
        for layer in ("core", "report", "views"):
            d = os.path.join(root, layer)
            for name in sorted(os.listdir(d)):
                if not name.endswith(".py") or name == "units.py":
                    continue
                path = os.path.join(d, name)
                out.append((f"{layer}/{name}",
                            open(path, encoding="utf-8").read()))
        return out

    def test_the_scan_finds_faces_at_all(self):
        """ゲートが空振りしていないこと（呼び方を変えたら読み方も直す合図）。"""
        faces = [n for n, t in self._sources() if _shows(t)[0]]
        assert len(faces) >= 5, f"率を出す面を数え切れていない: {faces}"

    def test_no_face_shows_the_ratio_alone(self):
        offenders = [n for n, t in self._sources()
                     if _shows(t) == (True, False)]
        assert not offenders, (
            f"F1 遮蔽率だけを出している面がある: {offenders}。"
            "100% 頭打ちはそこで 2 つの意味を持つので、`units.format_f1_depth` /"
            "`units.csv_f1_depth` も並べること（I-077）"
        )

    @pytest.mark.parametrize("text,expected", [
        ("units.format_blocked_ratio(r.blocked_ratio)", (True, False)),
        ("units.csv_blocked_ratio(x)", (True, False)),
        ("units.format_f1_depth(x)", (False, True)),
        ("units.csv_blocked_ratio(x); units.csv_f1_depth(x)", (True, True)),
        ("# blocked_ratio は models の生値", (False, False)),
    ])
    def test_the_face_detector_catches_what_it_claims(self, text, expected):
        assert _shows(text) == expected
