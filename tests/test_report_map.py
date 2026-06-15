"""
tests/test_report_map.py
========================
report_map.py（ヘッドレス経路地図生成）と map_graphics.py（純 PIL 描画）の単体テスト。

純関数（投影・ズーム選択・bbox 余白・距離テキスト）を中心に検証し、
render_path_map は _fetch_tile を monkeypatch してネットワーク無しでスモークする。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from PIL import Image

import infrastructure as infra
import map_graphics
import report_map


# ============================================================
# map_graphics（純 PIL 描画）
# ============================================================
class TestMapGraphics:

    def test_node_icon_returns_pil_image(self):
        for hollow in (True, False):
            img = map_graphics.node_icon(hollow)
            assert isinstance(img, Image.Image)
            assert img.size == (26, 26)
            assert img.mode == "RGBA"

    def test_distance_badge_returns_pil_image(self):
        img = map_graphics.distance_badge("1.23 km")
        assert isinstance(img, Image.Image)
        assert img.width > 0 and img.height > 0

    def test_distance_text_meters_below_1km(self):
        assert map_graphics.distance_text(0.5) == "500 m"

    def test_distance_text_km_at_and_above_1km(self):
        assert map_graphics.distance_text(1.0) == "1.00 km"
        assert map_graphics.distance_text(2.345) == "2.35 km"


# ============================================================
# 投影（lonlat_to_pixel）
# ============================================================
class TestLonLatToPixel:

    def test_known_value_origin(self):
        # zoom 0: lon=0 → x=128, lat=0 → y=128（世界1タイル 256px の中心）。
        x, y = infra.lonlat_to_pixel(0.0, 0.0, 0)
        assert x == 128.0
        assert abs(y - 128.0) < 1e-6

    def test_x_increases_eastward(self):
        x_w, _ = infra.lonlat_to_pixel(35.0, 139.0, 12)
        x_e, _ = infra.lonlat_to_pixel(35.0, 140.0, 12)
        assert x_e > x_w

    def test_y_increases_southward(self):
        # 北が上＝緯度が高いほど y は小さい。
        _, y_n = infra.lonlat_to_pixel(36.0, 139.0, 12)
        _, y_s = infra.lonlat_to_pixel(35.0, 139.0, 12)
        assert y_s > y_n


# ============================================================
# _padded_bbox / choose_zoom（純関数）
# ============================================================
class TestPaddedBbox:

    def test_normal_orders_corners_and_pads(self):
        tx, rx = (34.54, 132.41), (34.53, 132.40)
        lat_n, lat_s, lon_w, lon_e = report_map._padded_bbox(tx, rx, 0.15)
        assert lat_n > lat_s
        assert lon_e > lon_w
        # 余白込みで元の範囲を必ず内包する。
        assert lat_n > max(tx[0], rx[0])
        assert lat_s < min(tx[0], rx[0])

    def test_degenerate_point_still_has_span(self):
        p = (34.54, 132.41)
        lat_n, lat_s, lon_w, lon_e = report_map._padded_bbox(p, p, 0.15)
        assert lat_n - lat_s >= report_map._MIN_SPAN_DEG
        assert lon_e - lon_w >= report_map._MIN_SPAN_DEG


class TestChooseZoom:

    def test_tile_count_within_cap(self):
        tx, rx = (34.54, 132.41), (34.40, 132.20)
        z = report_map.choose_zoom(tx, rx, max_tiles=16)
        bbox = report_map._padded_bbox(tx, rx, 0.15)
        x0, x1, y0, y1 = report_map._tile_range(bbox, z)
        assert (x1 - x0 + 1) * (y1 - y0 + 1) <= 16

    def test_closer_path_gets_higher_or_equal_zoom(self):
        near = report_map.choose_zoom((34.540, 132.410), (34.539, 132.409))
        far  = report_map.choose_zoom((34.6, 132.5), (34.2, 132.1))
        assert near >= far

    def test_degenerate_returns_valid_zoom(self):
        p = (34.54, 132.41)
        z = report_map.choose_zoom(p, p, max_tiles=16, min_zoom=5, max_zoom=18)
        assert 5 <= z <= 18


# ============================================================
# render_path_map（_fetch_tile を monkeypatch・ネットワーク無し）
# ============================================================
class TestRenderPathMap:

    def _fake_tile(self, *args, **kwargs):
        return np.full((256, 256, 3), 200, dtype=np.uint8)

    def test_returns_image_when_tiles_available(self, monkeypatch):
        monkeypatch.setattr(infra, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map((34.54, 132.41), (34.53, 132.40))
        assert isinstance(img, Image.Image)
        assert img.mode == "RGB"
        assert img.width > 0 and img.height > 0

    def test_returns_none_when_all_tiles_fail(self, monkeypatch):
        monkeypatch.setattr(infra, "_fetch_tile", lambda *a, **k: None)
        img = report_map.render_path_map((34.54, 132.41), (34.53, 132.40))
        assert img is None

    def test_b64_wrapper_none_when_render_fails(self, monkeypatch):
        monkeypatch.setattr(infra, "_fetch_tile", lambda *a, **k: None)
        assert report_map.render_path_map_b64((34.54, 132.41), (34.53, 132.40)) is None

    def test_b64_wrapper_returns_string(self, monkeypatch):
        monkeypatch.setattr(infra, "_fetch_tile", self._fake_tile)
        b64 = report_map.render_path_map_b64((34.54, 132.41), (34.53, 132.40))
        assert isinstance(b64, str) and len(b64) > 0
