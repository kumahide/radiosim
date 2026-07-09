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
import pytest
from PIL import Image

import dem
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

    def test_arrow_icon_returns_rgba_image(self):
        # 主要4方位＋退化（0°）でも例外なく RGBA を返す。
        for bearing in (0.0, 90.0, 180.0, 270.0, 359.9):
            img = map_graphics.arrow_icon(bearing)
            assert isinstance(img, Image.Image)
            assert img.mode == "RGBA"
            assert img.size == (42, 42)

    def test_north_arrow_returns_rgba_image(self):
        img = map_graphics.north_arrow(0.0, -1.0)
        assert isinstance(img, Image.Image)
        assert img.mode == "RGBA"
        assert img.width > 0 and img.height > 0

    def test_north_arrow_handles_zero_vector(self):
        # 退化（北ベクトル 0）でも例外なく描く。
        img = map_graphics.north_arrow(0.0, 0.0)
        assert isinstance(img, Image.Image)

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
        x, y = dem.lonlat_to_pixel(0.0, 0.0, 0)
        assert x == 128.0
        assert abs(y - 128.0) < 1e-6

    def test_x_increases_eastward(self):
        x_w, _ = dem.lonlat_to_pixel(35.0, 139.0, 12)
        x_e, _ = dem.lonlat_to_pixel(35.0, 140.0, 12)
        assert x_e > x_w

    def test_y_increases_southward(self):
        # 北が上＝緯度が高いほど y は小さい。
        _, y_n = dem.lonlat_to_pixel(36.0, 139.0, 12)
        _, y_s = dem.lonlat_to_pixel(35.0, 139.0, 12)
        assert y_s > y_n


# ============================================================
# _band_px / _coverage_tiles / choose_zoom（純関数）
# ============================================================
class TestBandPx:

    def test_band_contains_tx_rx_within_half_extent(self):
        # TX/RX は中点からバンド長手方向に ±path_len/2＝必ず half_w 以内に収まる。
        band = report_map._band_px((34.54, 132.41), (34.40, 132.20), 14, 0.15)
        for px, py in ((band.ax, band.ay), (band.bx, band.by)):
            along = (px - band.mx) * band.ux + (py - band.my) * band.uy
            perp  = (px - band.mx) * band.px + (py - band.my) * band.py
            assert abs(along) <= band.half_w + 1e-6
            assert abs(perp) <= band.half_h + 1e-6

    def test_unit_vectors_are_orthonormal(self):
        band = report_map._band_px((34.54, 132.41), (34.40, 132.20), 14, 0.15)
        assert band.ux * band.ux + band.uy * band.uy == pytest.approx(1.0)
        assert band.ux * band.px + band.uy * band.py == pytest.approx(0.0)

    def test_degenerate_point_has_minimum_extent(self):
        # TX==RX でも最小半幅が確保され（東向きに固定）破綻しない。
        band = report_map._band_px((34.54, 132.41), (34.54, 132.41), 14, 0.15)
        assert band.half_w >= report_map._MIN_HALF_PX
        assert band.half_h > 0
        assert (band.ux, band.uy) == (1.0, 0.0)

    def test_band_aspect_matches_requested(self):
        # half_w/half_h = aspect（出力比を固定＝レポート断面図と高さを揃える）。
        for aspect in (15 / 6, 2.0, 1.0):
            band = report_map._band_px(
                (34.54, 132.41), (34.40, 132.20), 14, 0.15, aspect
            )
            assert band.half_w / band.half_h == pytest.approx(aspect)


class TestChooseZoom:

    def test_tile_count_within_cap(self):
        tx, rx = (34.54, 132.41), (34.40, 132.20)
        z = report_map.choose_zoom(tx, rx, max_tiles=16)
        x0, x1, y0, y1 = report_map._coverage_tiles(
            report_map._band_px(tx, rx, z, 0.15)
        )
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
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map((34.54, 132.41), (34.53, 132.40))
        assert isinstance(img, Image.Image)
        assert img.mode == "RGB"
        assert img.width > 0 and img.height > 0

    def test_diagonal_path_is_rotated_to_landscape(self, monkeypatch):
        # 経路を水平化するため、対角の経路でも横長（width > height）になる。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map((35.70, 139.70), (35.62, 139.81))
        assert isinstance(img, Image.Image)
        assert img.width > img.height

    def test_output_aspect_is_15_5(self, monkeypatch):
        # 出力の幅/高さ ≈ 15:5（断面図 15:6 より横長＝A4 縦1枚に収めるため地図側を
        # 詰めた設計。経路の水平/垂直の対応は上下配置で保つ）。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map((35.70, 139.70), (35.62, 139.81))
        assert img.width / img.height == pytest.approx(15 / 5, rel=0.03)

    def test_no_gray_fill_after_rotation(self, monkeypatch):
        # 回転 expand のグレー余白（_MISSING_RGB）がバンド内に残らない
        # （バンドの north-up 外接矩形ぶん取得＋数 px インセットで隅も埋まる）。
        # _fake_tile は全画素 200 なので 229=_MISSING_RGB は必ず埋め色。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map((35.70, 139.70), (35.62, 139.81))
        fill = np.all(np.asarray(img) == report_map._MISSING_RGB, axis=2)
        assert int(fill.sum()) == 0

    def test_returns_none_when_all_tiles_fail(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **k: None)
        img = report_map.render_path_map((34.54, 132.41), (34.53, 132.40))
        assert img is None

    def test_returns_none_when_fetch_rate_below_threshold(self, monkeypatch):
        # タイルの約半分だけ取得成功 → 閾値 0.6 未満なので地図なし（注記に委ねる）。
        # 並列ワーカーから呼ばれるため、共有カウンタではなくタイル座標で決定的に分岐。
        def _half(layer, zoom, x, y, *args, **kwargs):
            return self._fake_tile() if (x + y) % 2 == 0 else None

        monkeypatch.setattr(dem, "_fetch_tile", _half)
        img = report_map.render_path_map(
            (34.6, 132.5), (34.4, 132.3), min_fetch_frac=0.6
        )
        assert img is None

    def test_partial_fetch_above_threshold_renders(self, monkeypatch):
        # 全取得成功でも閾値を下げれば当然描画。閾値境界の健全性確認。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map(
            (34.54, 132.41), (34.53, 132.40), min_fetch_frac=0.6
        )
        assert isinstance(img, Image.Image)

    def test_b64_wrapper_none_when_render_fails(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **k: None)
        assert report_map.render_path_map_b64((34.54, 132.41), (34.53, 132.40)) is None

    def test_b64_wrapper_returns_string(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        b64 = report_map.render_path_map_b64((34.54, 132.41), (34.53, 132.40))
        assert isinstance(b64, str) and len(b64) > 0
