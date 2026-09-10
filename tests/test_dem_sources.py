"""
tests/test_dem_sources.py
=========================
dem_sources.py（DEM ソースの宣言ファイル・3.3 段4c）のユニットテスト。
"""

import pytest

from core import dem_sources


# ============================================================
# GSI_DEM / SOURCES
# ============================================================
class TestSources:

    def test_sources_contains_only_gsi_dem(self):
        """3.3 時点でアクティブなソースは国土地理院のみ。"""
        assert dem_sources.SOURCES == (dem_sources.GSI_DEM,)

    def test_gsi_dem_source_id_matches_output_contract_value(self):
        """`elev_source` 列の既存値 `"gsi_dem"` と一致する（core/output_contract.py）。"""
        assert dem_sources.GSI_DEM.source_id == "gsi_dem"

    def test_gsi_dem_layers_match_priority_order(self):
        assert dem_sources.GSI_DEM.layers == (
            ("dem5a_png", 15),
            ("dem5b_png", 15),
            ("dem_png", 14),
        )

    def test_url_template_renders_the_known_gsi_url(self):
        url = dem_sources.GSI_DEM.url_template.format(
            layer="dem5a_png", z=15, x=1, y=2
        )
        assert url == "https://cyberjapandata.gsi.go.jp/xyz/dem5a_png/15/1/2.png"

    def test_invalid_rgb_is_the_gsi_sea_marker(self):
        assert dem_sources.GSI_DEM.invalid_rgb == (128, 0, 0)


# ============================================================
# decode() ディスパッチ
# ============================================================
class TestDecodeDispatch:

    def test_decode_dispatches_to_gsi_dem(self):
        # (128, 1, 0) → x = 128*65536 + 1*256 = 8388864 ≥ 8388608 → (x-16777216)*0.01
        elev = dem_sources.decode(dem_sources.DecodeMethod.GSI_DEM, 128, 1, 0)
        assert elev == pytest.approx((8388864 - 16777216) * 0.01, abs=0.01)

    def test_decode_dispatches_to_terrarium(self):
        elev = dem_sources.decode(dem_sources.DecodeMethod.TERRARIUM, 128, 1, 0)
        assert elev == pytest.approx(1.0, abs=1e-9)

    def test_decode_dispatches_to_mapbox_terrain_rgb(self):
        elev = dem_sources.decode(dem_sources.DecodeMethod.MAPBOX_TERRAIN_RGB, 0, 0, 0)
        assert elev == pytest.approx(-10000.0, abs=1e-9)

    def test_every_decode_method_has_a_decoder(self):
        """列挙の全メンバーに実装が対応する（増やし忘れを検出）。"""
        for method in dem_sources.DecodeMethod:
            assert method in dem_sources._DECODERS


# ============================================================
# 個別デコード方式
# ============================================================
class TestDecodeGsiDem:

    def test_invalid_pixel_returns_zero(self):
        assert dem_sources._decode_gsi_dem(128, 0, 0) == pytest.approx(0.0)

    def test_positive_range_below_threshold(self):
        # x = 100*256 = 25600 < 8388608
        assert dem_sources._decode_gsi_dem(0, 100, 0) == pytest.approx(256.0, abs=0.01)

    def test_negative_range_above_threshold(self):
        # x = 255*65536 + 255*256 + 255 = 16777215 ≥ 8388608
        elev = dem_sources._decode_gsi_dem(255, 255, 255)
        assert elev == pytest.approx((16777215 - 16777216) * 0.01, abs=0.01)


class TestDecodeTerrarium:

    def test_sea_level_marker(self):
        # (R*256 + G + B/256) - 32768 = 0 のとき R=128, G=0, B=0
        assert dem_sources._decode_terrarium(128, 0, 0) == pytest.approx(0.0, abs=1e-9)

    def test_fractional_blue_channel(self):
        # B は 1/256 m 単位の端数を運ぶ
        elev = dem_sources._decode_terrarium(128, 0, 128)
        assert elev == pytest.approx(0.5, abs=1e-9)


class TestDecodeMapboxTerrainRgb:

    def test_void_minimum(self):
        assert dem_sources._decode_mapbox_terrain_rgb(0, 0, 0) == pytest.approx(-10000.0)

    def test_one_decimeter_step(self):
        # B を 1 増やすと 0.1m 増える
        base = dem_sources._decode_mapbox_terrain_rgb(0, 0, 0)
        stepped = dem_sources._decode_mapbox_terrain_rgb(0, 0, 1)
        assert stepped - base == pytest.approx(0.1, abs=1e-9)
