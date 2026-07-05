"""
tests/test_dem.py
=================
dem.py のユニットテスト（DEM/淡色地図タイル取得・標高デコード・キャッシュ）。
HTTP 通信は monkeypatch で差し替え、ネットワーク接続不要。
"""

import json
import os
import unittest.mock as mock

import numpy as np
import pytest
import requests

import config
import dem


# ============================================================
# _decode_elevation
# ============================================================
class TestDecodeElevation:

    def test_invalid_pixel_128_0_0_returns_zero(self):
        """(128, 0, 0) は無効値 → 0.0 m。"""
        rgb = np.array([128, 0, 0], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx(0.0)

    def test_zero_rgb_returns_zero(self):
        rgb = np.array([0, 0, 0], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx(0.0)

    def test_positive_elevation(self):
        """x = 10000 → 100.00 m。"""
        x = 10000
        rgb = np.array([x >> 16, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx(100.0, abs=0.01)

    def test_negative_elevation(self):
        """x = 16776216 → -10.00 m（海面下）。"""
        x = 16776216
        rgb = np.array([(x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx(-10.0, abs=0.01)

    def test_boundary_x_8388607_positive(self):
        """x = 8388607 (< 8388608) → 正の標高。ただし (128,0,0) は無効値なので避ける。"""
        # x = 8388607 → r=(127, g=255, b=255) で無効値ピクセルには該当しない
        x = 8388607
        r = (x >> 16) & 0xFF   # 127
        g = (x >> 8)  & 0xFF   # 255
        b = x & 0xFF            # 255
        assert r != 128, "このテスト用ピクセルが無効値(128,0,0)と誤判定される"
        rgb = np.array([r, g, b], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx(x * 0.01, abs=0.01)

    def test_boundary_x_8388608_negative(self):
        """x = 8388608 は RGB=(128,0,0) となり無効値扱いで 0.0 を返す（仕様）。
        代わりに x=8388609 で負の標高デコードを検証する。"""
        # x=8388608 → r=128,g=0,b=0 = 無効値ピクセル → 0.0 が正しい挙動
        x_invalid = 8388608
        r = (x_invalid >> 16) & 0xFF  # 128
        g = (x_invalid >> 8)  & 0xFF  # 0
        b = x_invalid & 0xFF           # 0
        rgb_invalid = np.array([r, g, b], dtype=np.uint8)
        assert dem._decode_elevation(rgb_invalid) == pytest.approx(0.0), (
            "x=8388608 は (128,0,0) = 無効値ピクセルなので 0.0 を返す"
        )

        # x=8388609 で負の標高デコードを確認
        x = 8388609
        rgb = np.array([(x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx((x - 16777216) * 0.01, abs=0.01)


# ============================================================
# get_elevation / _fetch_tile（monkeypatch）
# ============================================================
class TestGetElevation:

    @pytest.fixture(autouse=True)
    def clear_tile_cache(self):
        """テスト間でメモリキャッシュをリセットする。"""
        dem._tile_cache.clear()
        dem._failed_tiles.clear()
        yield
        dem._tile_cache.clear()
        dem._failed_tiles.clear()

    def test_returns_float(self, monkeypatch):
        tile = np.full((256, 256, 3), [0, 39, 16], dtype=np.uint8)
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **kw: tile)
        assert isinstance(dem.get_elevation(34.5429, 132.4118), float)

    def test_uses_decoded_pixel_value(self, monkeypatch):
        """_fetch_tile が返したピクセルを正しくデコードすること。"""
        x     = 10000  # 100.00 m
        pixel = np.array([x >> 16, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        tile  = np.full((256, 256, 3), pixel, dtype=np.uint8)
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **kw: tile)
        assert dem.get_elevation(34.5429, 132.4118) == pytest.approx(100.0, abs=0.1)

    def test_returns_zero_when_fetch_returns_none(self, monkeypatch):
        """_fetch_tile が None を返したとき 0.0 になること。"""
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **kw: None)
        assert dem.get_elevation(34.5429, 132.4118) == pytest.approx(0.0)

    def test_tile_cached_after_first_call(self, monkeypatch):
        """同じタイルへの2回目の呼び出しで _fetch_tile が呼ばれないこと。"""
        tile = np.full((256, 256, 3), [0, 39, 16], dtype=np.uint8)
        call_count = {"n": 0}

        def fake_fetch(*a, **kw):
            call_count["n"] += 1
            return tile

        monkeypatch.setattr(dem, "_fetch_tile", fake_fetch)
        dem.get_elevation(34.5429, 132.4118)
        dem.get_elevation(34.5429, 132.4118)
        assert call_count["n"] == 1


class TestFetchTile:

    def _mock_session(self, monkeypatch, *, side_effect=None, return_value=None):
        """_get_session() をモックセッションに差し替えるヘルパー。"""
        fake_session = mock.Mock()
        if side_effect is not None:
            fake_session.get.side_effect = side_effect
        else:
            fake_session.get.return_value = return_value
        monkeypatch.setattr(dem, "_get_session", lambda: fake_session)
        return fake_session

    def test_returns_none_on_network_error_no_cache(self, tmp_path, monkeypatch):
        """ネットワークエラー＆キャッシュなし → None。"""
        self._mock_session(monkeypatch, side_effect=requests.RequestException("timeout"))
        result = dem._fetch_tile(
            "dem_png", 14, 99999, 99999, str(tmp_path), str(tmp_path / "x.png")
        )
        assert result is None

    def test_uses_disk_cache_on_network_error(self, tmp_path, monkeypatch):
        """ネットワークエラー時にディスクキャッシュがあればそれを返す。"""
        from PIL import Image

        cache_path = tmp_path / "tile.png"
        Image.new("RGB", (256, 256), (0, 39, 16)).save(str(cache_path))

        self._mock_session(monkeypatch, side_effect=requests.RequestException("err"))
        arr = dem._fetch_tile("dem_png", 14, 0, 0, str(tmp_path), str(cache_path))
        assert arr is not None
        assert arr.shape == (256, 256, 3)

    def test_saves_tile_to_disk_on_200(self, tmp_path, monkeypatch):
        """HTTP 200 レスポンス時にタイルをディスクに保存すること。"""
        from PIL import Image
        import io

        img = Image.new("RGB", (256, 256), (0, 39, 16))
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        fake_response = mock.Mock()
        fake_response.status_code = 200
        fake_response.content     = buf.getvalue()
        self._mock_session(monkeypatch, return_value=fake_response)

        cache_path = str(tmp_path / "tile.png")
        dem._fetch_tile("dem5a_png", 15, 0, 0, str(tmp_path), cache_path)
        assert os.path.exists(cache_path)

    def test_returns_array_on_200(self, tmp_path, monkeypatch):
        """HTTP 200 レスポンス時に numpy 配列を返すこと。"""
        from PIL import Image
        import io

        img = Image.new("RGB", (256, 256), (10, 20, 30))
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        fake_response = mock.Mock()
        fake_response.status_code = 200
        fake_response.content     = buf.getvalue()
        self._mock_session(monkeypatch, return_value=fake_response)

        arr = dem._fetch_tile("dem5a_png", 15, 0, 0, str(tmp_path), str(tmp_path / "t.png"))
        assert isinstance(arr, np.ndarray)
        assert arr.shape == (256, 256, 3)

    def test_304_uses_existing_cache(self, tmp_path, monkeypatch):
        """HTTP 304 時（If-Modified-Since）はキャッシュファイルを使うこと。"""
        from PIL import Image

        cache_path = tmp_path / "tile.png"
        Image.new("RGB", (256, 256), (0, 39, 16)).save(str(cache_path))

        fake_response = mock.Mock()
        fake_response.status_code = 304
        self._mock_session(monkeypatch, return_value=fake_response)

        arr = dem._fetch_tile("dem_png", 14, 0, 0, str(tmp_path), str(cache_path))
        assert arr is not None
        assert arr.shape == (256, 256, 3)


# ============================================================
# プロキシ / セッション管理
# ============================================================
class TestProxy:

    def test_proxy_url_in_default_config(self):
        """proxy_url が DEFAULT_CONFIG に含まれていること。"""
        assert "proxy_url" in config.DEFAULT_CONFIG
        assert config.DEFAULT_CONFIG["proxy_url"] == ""

    def test_load_config_fills_proxy_url(self, tmp_path):
        """proxy_url が未定義の古い config.json でもデフォルト補完されること。"""
        cfg_path = str(tmp_path / "conf.json")
        with open(cfg_path, "w") as f:
            json.dump({"freq": "2400.0"}, f)
        loaded = config.load_config(cfg_path)
        assert "proxy_url" in loaded
        assert loaded["proxy_url"] == ""

    def test_set_proxy_resets_session(self):
        """set_proxy() を呼ぶと既存セッションが破棄されること。"""
        dem.set_proxy("")
        s1 = dem._get_session()
        dem.set_proxy("http://proxy.example.com:8080")
        assert dem._http_session is None  # リセット確認
        s2 = dem._get_session()
        assert s1 is not s2

    def test_get_session_singleton(self):
        """_get_session() は同一セッションを返すこと（再生成しない）。"""
        dem.set_proxy("")
        s1 = dem._get_session()
        s2 = dem._get_session()
        assert s1 is s2

    def teardown_method(self):
        """各テスト後にセッションをリセットしてテスト間干渉を防ぐ。"""
        dem.set_proxy("")


# ============================================================
# _enumerate_bbox / count_bbox_tiles
# ============================================================
class TestEnumerateBbox:

    def test_returns_6_tuple_per_tile(self):
        tiles = dem._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        assert all(len(t) == 6 for t in tiles)

    def test_covers_all_dem_layers(self):
        tiles = dem._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        layer_ids = {t[0] for t in tiles}
        assert layer_ids == {lid for lid, _ in dem.DEM_LAYERS}

    def test_at_least_one_tile_per_layer(self):
        tiles = dem._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        for layer_id, _ in dem.DEM_LAYERS:
            assert any(t[0] == layer_id for t in tiles)

    def test_inverted_coords_same_result(self):
        """lat1/lon1 が NW でなくても同じ結果を返す（入力順に依存しない）。"""
        tiles_nw_se = dem._enumerate_bbox(34.54, 132.40, 34.53, 132.41)
        tiles_se_nw = dem._enumerate_bbox(34.53, 132.41, 34.54, 132.40)
        assert set(t[:4] for t in tiles_nw_se) == set(t[:4] for t in tiles_se_nw)

    def test_larger_area_returns_more_tiles(self):
        small = dem._enumerate_bbox(34.540, 132.410, 34.539, 132.409)
        large = dem._enumerate_bbox(34.600, 132.500, 34.400, 132.300)
        assert len(large) > len(small)

    def test_tile_coords_in_valid_range(self):
        """タイル座標がズームレベルに対して有効な範囲内であること。"""
        tiles = dem._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        for layer_id, zoom, x, y, subdir, cache_path in tiles:
            assert 0 <= x < 2 ** zoom
            assert 0 <= y < 2 ** zoom

    def test_cache_path_contains_layer_and_coords(self):
        """cache_path が layer_id / x / y.png の構造を持つこと。"""
        tiles = dem._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        for layer_id, zoom, x, y, subdir, cache_path in tiles:
            assert layer_id in cache_path
            assert str(x) in cache_path
            assert cache_path.endswith(f"{y}.png")


class TestCountBboxTiles:

    def test_returns_zoom14_position_count(self):
        """count_bbox_tiles は zoom-14 位置数（エリア数）を返す。"""
        lat1, lon1, lat2, lon2 = 34.54, 132.41, 34.53, 132.40
        count = dem.count_bbox_tiles(lat1, lon1, lat2, lon2)
        positions = list(dem._iter_dem_positions(lat1, lon1, lat2, lon2))
        assert count == len(positions)

    def test_returns_positive_integer(self):
        count = dem.count_bbox_tiles(34.54, 132.41, 34.53, 132.40)
        assert isinstance(count, int)
        assert count > 0

    def test_inverted_coords_same_result(self):
        """入力座標の順序に依存しないこと。"""
        assert dem.count_bbox_tiles(34.54, 132.41, 34.53, 132.40) == \
               dem.count_bbox_tiles(34.53, 132.40, 34.54, 132.41)


# ============================================================
# _iter_dem_positions
# ============================================================
class TestIterDemPositions:

    def test_yields_tuples_with_correct_structure(self):
        """各 yield 値が (x14, y14, subdir, path, zoom15_tiles) の構造を持つ。"""
        positions = list(dem._iter_dem_positions(34.54, 132.41, 34.53, 132.40))
        assert len(positions) > 0
        for x14, y14, subdir, path, zoom15_tiles in positions:
            assert isinstance(x14, int)
            assert isinstance(y14, int)
            assert path.endswith(f"{y14}.png")
            assert str(x14) in path
            assert len(zoom15_tiles) >= 1

    def test_zoom15_tiles_are_sub_tiles_of_zoom14(self):
        """zoom-15 サブタイルが対応する zoom-14 の子タイル範囲内に収まること。"""
        positions = list(dem._iter_dem_positions(34.54, 132.41, 34.53, 132.40))
        for x14, y14, _, _, zoom15_tiles in positions:
            for x15, y15, *_ in zoom15_tiles:
                assert x14 * 2 <= x15 <= x14 * 2 + 1
                assert y14 * 2 <= y15 <= y14 * 2 + 1

    def test_inverted_coords_same_result(self):
        pos_ab = list(dem._iter_dem_positions(34.54, 132.41, 34.53, 132.40))
        pos_ba = list(dem._iter_dem_positions(34.53, 132.40, 34.54, 132.41))
        assert [(x, y) for x, y, *_ in pos_ab] == [(x, y) for x, y, *_ in pos_ba]


# ============================================================
# _process_position
# ============================================================
class TestProcessPosition:

    def _make_counts(self):
        return {"downloaded_5a": 0, "downloaded_5b": 0, "downloaded_dem": 0,
                "skipped": 0, "failed": 0}

    def test_skips_when_dem_cached_and_no_force(self, tmp_path, monkeypatch):
        """dem_png キャッシュあり・force=False → skipped。"""
        import threading
        from PIL import Image
        dem_path = tmp_path / "dem.png"
        Image.new("RGB", (256, 256)).save(str(dem_path))
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **kw: None)
        counts = self._make_counts()
        lock = threading.Lock()
        dem._process_position(0, 0, str(tmp_path), str(dem_path), [], False, counts, lock)
        assert counts["skipped"] == 1
        assert counts["downloaded_5a"] == counts["downloaded_5b"] == counts["downloaded_dem"] == 0

    def test_downloads_5a_when_available(self, tmp_path, monkeypatch):
        """5a DL 成功 → downloaded_5a 増加・5b/dem は試みない。"""
        import threading
        tile_arr = np.zeros((256, 256, 3), dtype=np.uint8)
        fetch_calls = []

        def mock_fetch(layer_id, *a, **kw):
            fetch_calls.append(layer_id)
            return tile_arr if layer_id == "dem5a_png" else None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        subdir5a = str(tmp_path / "5a" / "0"); subdir5b = str(tmp_path / "5b" / "0")
        zoom15 = [(0, 0, subdir5a, str(tmp_path / "5a.png"),
                         subdir5b, str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert counts["downloaded_5a"] == 1
        assert counts["downloaded_5b"] == 0
        assert "dem5b_png" not in fetch_calls

    def test_falls_back_to_5b_when_5a_fails(self, tmp_path, monkeypatch):
        """5a 失敗 → 5b 試みる → downloaded_5b 増加。"""
        import threading
        tile_arr = np.zeros((256, 256, 3), dtype=np.uint8)

        def mock_fetch(layer_id, *a, **kw):
            return tile_arr if layer_id == "dem5b_png" else None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert counts["downloaded_5b"] == 1
        assert counts["downloaded_dem"] == 0

    def test_falls_back_to_dem_when_both_5m_fail(self, tmp_path, monkeypatch):
        """5a・5b 両方失敗 → dem_png DL。"""
        import threading
        tile_arr = np.zeros((256, 256, 3), dtype=np.uint8)

        def mock_fetch(layer_id, *a, **kw):
            return tile_arr if layer_id == "dem_png" else None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert counts["downloaded_dem"] == 1
        assert counts["failed"] == 0

    def test_force_ignores_existing_cache(self, tmp_path, monkeypatch):
        """force=True: dem_png キャッシュがあっても再取得する。"""
        import threading
        from PIL import Image
        dem_path = tmp_path / "dem.png"
        Image.new("RGB", (256, 256)).save(str(dem_path))
        tile_arr = np.zeros((256, 256, 3), dtype=np.uint8)

        def mock_fetch(layer_id, *a, **kw):
            return tile_arr if layer_id == "dem5a_png" else None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem._process_position(0, 0, str(tmp_path), str(dem_path),
                                 zoom15, True, counts, lock)
        assert counts["skipped"] == 0
        assert counts["downloaded_5a"] == 1

    @staticmethod
    def _void_tile(void=True):
        """全画素 (128,0,0) の欠損タイル、または全画素有効(0,0,0)のタイル。"""
        arr = np.zeros((256, 256, 3), dtype=np.uint8)
        if void:
            arr[:, :, 0] = 128
        return arr

    def test_descends_to_5b_when_5a_has_void(self, tmp_path, monkeypatch):
        """5a 取得成功だが欠損あり・5b が補完 → 5b も取得し dem は不要。"""
        import threading
        valid = np.zeros((256, 256, 3), dtype=np.uint8)

        def mock_fetch(layer_id, *a, **kw):
            if layer_id == "dem5a_png":
                return self._void_tile(void=True)    # 5a は全欠損
            if layer_id == "dem5b_png":
                return valid                          # 5b が補完
            return None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert counts["downloaded_5a"] == 1
        assert counts["downloaded_5b"] == 1
        assert counts["downloaded_dem"] == 0

    def test_descends_to_dem_when_5a_and_5b_void(self, tmp_path, monkeypatch):
        """5a・5b とも同一画素が欠損 → dem_png まで降りる（終端確定）。"""
        import threading

        def mock_fetch(layer_id, *a, **kw):
            if layer_id in ("dem5a_png", "dem5b_png"):
                return self._void_tile(void=True)    # 両方とも全欠損
            if layer_id == "dem_png":
                return np.zeros((256, 256, 3), dtype=np.uint8)
            return None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert counts["downloaded_5a"] == 1
        assert counts["downloaded_5b"] == 1
        assert counts["downloaded_dem"] == 1

    def test_no_descent_when_5a_void_free(self, tmp_path, monkeypatch):
        """5a が欠損なし → 5b/dem は一切試みない（DL 最小）。"""
        import threading
        fetch_calls = []

        def mock_fetch(layer_id, *a, **kw):
            fetch_calls.append(layer_id)
            return np.zeros((256, 256, 3), dtype=np.uint8) if layer_id == "dem5a_png" else None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert fetch_calls == ["dem5a_png"]
        assert counts["downloaded_dem"] == 0

    def test_void_mask_matches_decode_semantics(self):
        """_void_mask が (128,0,0) のみを True とすること。"""
        arr = np.zeros((2, 2, 3), dtype=np.uint8)
        arr[0, 0] = (128, 0, 0)   # 無効値
        arr[0, 1] = (0, 0, 1)     # 標高 0.01m（有効）
        arr[1, 0] = (128, 0, 1)   # 有効（b!=0）
        mask = dem._void_mask(arr)
        assert mask[0, 0] and not mask[0, 1] and not mask[1, 0] and not mask[1, 1]


# ============================================================
# scan_cache_overlay（実キャッシュ走査・自動カバレッジ表示用）
# ============================================================

class TestScanCacheOverlay:

    # 走査対象の代表座標（広島県付近）
    LAT, LON = 34.54, 132.41

    def _touch(self, root, layer_id, x, y):
        d = os.path.join(root, layer_id, str(x))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{y}.png"), "wb") as f:
            f.write(b"\x89PNG")

    def test_empty_cache_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        assert dem.scan_cache_overlay(
            self.LAT, self.LON, self.LAT - 0.01, self.LON + 0.01, 14
        ) == []

    def test_5a_wins_over_dem_at_same_cell(self, tmp_path, monkeypatch):
        """同じ zoom-14 セルに 5a と dem があれば最高精度 5a を返す。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        x15, y15, _, _ = dem._tile_coords(self.LAT, self.LON, 15)
        self._touch(tmp_path, "dem_png", x14, y14)
        self._touch(tmp_path, "dem5a_png", x15, y15)
        cells = dem.scan_cache_overlay(
            self.LAT + 0.01, self.LON - 0.01,
            self.LAT - 0.01, self.LON + 0.01, 14,
        )
        match = [c for c in cells if c["x"] == x14 and c["y"] == y14]
        assert len(match) == 1
        assert match[0]["level"] == "5a"
        assert match[0]["zoom"] == 14

    def test_dem_only_area(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        cells = dem.scan_cache_overlay(
            self.LAT + 0.01, self.LON - 0.01,
            self.LAT - 0.01, self.LON + 0.01, 14,
        )
        assert all(c["level"] == "dem" for c in cells)
        assert any(c["x"] == x14 and c["y"] == y14 for c in cells)

    def test_tiles_outside_view_excluded(self, tmp_path, monkeypatch):
        """表示範囲外のキャッシュは返さない。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        # はるか遠方の小範囲を指定（対象タイルを含まない）
        cells = dem.scan_cache_overlay(43.07, 141.34, 43.06, 141.35, 14)
        assert cells == []

    # 日本全域を覆う bbox（filtering の端数で対象タイルを落とさないため広めに取る）
    WIDE = (46.0, 128.0, 30.0, 146.0)

    def _aligned_block_origin(self, span):
        """span×span に整列した zoom-14 ブロックの原点 (x0, y0) を返す。"""
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        return (x14 // span) * span, (y14 // span) * span

    def test_full_aligned_block_merges_to_single_coarse_cell(self, tmp_path, monkeypatch):
        """完全に埋まった整列 4×4 ブロックは zoom-12 の単一セルへ統合される。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x0, y0 = self._aligned_block_origin(4)   # 4 = 2^(14-12)
        for dx in range(4):
            for dy in range(4):
                self._touch(tmp_path, "dem_png", x0 + dx, y0 + dy)
        cells = dem.scan_cache_overlay(*self.WIDE, 12)
        assert len(cells) == 1
        assert cells[0]["zoom"] == 12
        assert cells[0]["level"] == "dem"

    def test_partial_block_keeps_edges_fine(self, tmp_path, monkeypatch):
        """欠けのあるブロックは粗く統合されず、エッジは zoom-14 のまま残る。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x0, y0 = self._aligned_block_origin(4)
        for dx in range(4):
            for dy in range(4):
                if dx == 0 and dy == 0:
                    continue   # 1 隅を欠けさせる → 全体統合は不可
                self._touch(tmp_path, "dem_png", x0 + dx, y0 + dy)
        cells = dem.scan_cache_overlay(*self.WIDE, 12)
        # 単一の粗いセルにはならない（過大表示を防ぐ）
        assert len(cells) > 1
        # 細粒度（zoom-14）のセルが残る
        assert any(c["zoom"] == 14 for c in cells)
        # 欠けた隅 (x0, y0) は covered として返らない
        assert not any(c["zoom"] == 14 and c["x"] == x0 and c["y"] == y0 for c in cells)

    def test_count_cached_areas_counts_only_cached(self, tmp_path, monkeypatch):
        """count_cached_areas は実在キャッシュのみ数える（未取得は含めない）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        # 2 エリアだけキャッシュ
        self._touch(tmp_path, "dem_png", x14, y14)
        self._touch(tmp_path, "dem_png", x14 + 1, y14)
        wide = (self.LAT + 0.1, self.LON - 0.1, self.LAT - 0.1, self.LON + 0.1)
        cached = dem.count_cached_areas(*wide)
        total = dem.count_bbox_tiles(*wide)
        assert cached == 2
        assert total > cached   # 範囲総数は未取得を含むので多い

    def test_count_cached_areas_zero_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        assert dem.count_cached_areas(*self.WIDE) == 0


class TestCoverageOutline:

    LAT, LON = 34.54, 132.41
    WIDE = (46.0, 128.0, 30.0, 146.0)

    def _touch(self, root, layer_id, x, y):
        d = os.path.join(root, layer_id, str(x))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{y}.png"), "wb") as f:
            f.write(b"\x89PNG")

    def test_empty_cache_no_loops(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        assert dem.coverage_outline(*self.WIDE) == []

    def test_single_cell_is_rectangle(self, tmp_path, monkeypatch):
        """単一セル → 4 頂点の矩形ループ1個。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        loops = dem.coverage_outline(*self.WIDE)
        assert len(loops) == 1
        assert len(loops[0]) == 4

    def test_adjacent_cells_merge_to_one_outline(self, tmp_path, monkeypatch):
        """隣接2セルは内部線なしの単一矩形（4頂点）になる。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        self._touch(tmp_path, "dem_png", x14 + 1, y14)
        loops = dem.coverage_outline(*self.WIDE)
        assert len(loops) == 1
        assert len(loops[0]) == 4   # 内部の共有辺は相殺され角は4つ

    def test_l_shape_has_six_corners(self, tmp_path, monkeypatch):
        """L字（2×2 から1セル欠け）は6頂点のループ。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        for dx in (0, 1):
            for dy in (0, 1):
                if dx == 1 and dy == 1:
                    continue
                self._touch(tmp_path, "dem_png", x14 + dx, y14 + dy)
        loops = dem.coverage_outline(*self.WIDE)
        assert len(loops) == 1
        assert len(loops[0]) == 6


class TestBasemapTiles:
    """淡色地図（レポート地図）タイルの取得・キャッシュ・削除。"""

    LAT, LON = 34.54, 132.41
    WIDE = (34.6, 132.3, 34.4, 132.5)

    def test_tile_path_includes_zoom(self):
        """キャッシュパスにズームが入る（異なるズームの同一(x,y)が衝突しない）。"""
        subdir, path = dem._basemap_tile_path(14, 100, 200)
        assert os.path.join(dem.BASEMAP_SUBDIR, "14", "100") in subdir
        assert path.endswith(os.path.join("100", "200.png"))
        # ズーム違いはパスが異なる。
        _, path15 = dem._basemap_tile_path(15, 100, 200)
        assert path != path15

    def test_fetch_basemap_tiles_parallel_returns_dict(self, monkeypatch):
        """並列取得が成功タイルだけを {(x,y):配列} で返す。"""
        def fake(layer_id, zoom, x, y, subdir, path):
            return np.full((256, 256, 3), 100, dtype=np.uint8)
        monkeypatch.setattr(dem, "_fetch_tile", fake)
        tiles = [(1, 2), (3, 4), (5, 6)]
        out = dem.fetch_basemap_tiles(tiles, 14)
        assert set(out.keys()) == set(tiles)

    def test_fetch_basemap_tiles_empty_input(self):
        assert dem.fetch_basemap_tiles([], 14) == {}

    def test_fetch_basemap_tiles_skips_failures(self, monkeypatch):
        """取得失敗（None）のタイルは結果に含めない。"""
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **k: None)
        assert dem.fetch_basemap_tiles([(1, 2)], 14) == {}

    def test_delete_tile_cache_keeps_basemap(self, tmp_path, monkeypatch):
        """エリア範囲削除は basemap タイルを消さない（DEM カバレッジ専用の操作）。

        basemap はマップウィンドウで可視化されないため、範囲指定で黙って消すのを
        避ける。basemap は「全キャッシュ削除」でのみ消える（下記テスト参照）。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        z = 14
        x, y, _, _ = dem._tile_coords(self.LAT, self.LON, z)
        subdir, path = dem._basemap_tile_path(z, x, y)
        os.makedirs(subdir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"\x89PNG")
        assert os.path.exists(path)
        dem.delete_tile_cache(*self.WIDE)
        assert os.path.exists(path)

    def test_delete_all_tile_cache_removes_basemap(self, tmp_path, monkeypatch):
        """全キャッシュ削除は basemap タイルも消す。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        z = 14
        x, y, _, _ = dem._tile_coords(self.LAT, self.LON, z)
        subdir, path = dem._basemap_tile_path(z, x, y)
        os.makedirs(subdir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"\x89PNG")
        assert os.path.exists(path)
        dem.delete_all_tile_cache()
        assert not os.path.exists(path)


# ============================================================
# キャッシュ削除・統計（delete_tile_cache / get_cache_stats / delete_all_tile_cache）
# ============================================================
class TestCacheDeletion:
    """ユーザーデータ（DEM キャッシュ）を消す操作の不変条件。

    basemap の扱いは TestBasemapTiles 側で担保済み。ここでは DEM タイルに
    ついて「bbox 内だけ消える・メモリキャッシュも連動して消える・件数が
    実削除数を報告する」を守る（削除系は誤ると再取得コストがユーザーに跳ねる）。
    """

    BBOX = (34.540, 132.410, 34.539, 132.409)

    def _seed_bbox_tiles(self) -> list[tuple]:
        """bbox 内の全 DEM タイルを実ファイルとして作成し、タイルリストを返す。"""
        tiles = dem._enumerate_bbox(*self.BBOX)
        for _, _, _, _, subdir, cache_path in tiles:
            os.makedirs(subdir, exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(b"\x89PNG")
        return tiles

    def _fresh_memory_cache(self, monkeypatch):
        monkeypatch.setattr(dem, "_tile_cache", {})
        monkeypatch.setattr(dem, "_failed_tiles", set())

    def test_deletes_only_bbox_files_and_memory_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._fresh_memory_cache(monkeypatch)
        tiles = self._seed_bbox_tiles()

        # bbox 外のタイルは残ること（範囲削除が全消しに化けない）。
        outside_dir = os.path.join(str(tmp_path), "dem_png", "0")
        os.makedirs(outside_dir, exist_ok=True)
        outside_file = os.path.join(outside_dir, "0.png")
        with open(outside_file, "wb") as f:
            f.write(b"\x89PNG")

        # メモリキャッシュ: bbox 内キーは消え、bbox 外キーは残ること。
        layer_id, _, x, y, _, _ = tiles[0]
        dem._tile_cache[(layer_id, x, y)] = np.zeros(1)
        dem._tile_cache[("dem_png", 0, 0)] = np.zeros(1)
        dem._failed_tiles.add((layer_id, x, y))

        res = dem.delete_tile_cache(*self.BBOX)

        assert res == {"deleted": len(tiles), "errors": 0}
        assert all(not os.path.exists(p) for *_, p in tiles)
        assert os.path.exists(outside_file)
        assert (layer_id, x, y) not in dem._tile_cache
        assert ("dem_png", 0, 0) in dem._tile_cache
        assert (layer_id, x, y) not in dem._failed_tiles

    def test_missing_files_count_zero(self, tmp_path, monkeypatch):
        """未取得エリアの範囲削除は deleted=0（存在しないものを数えない）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._fresh_memory_cache(monkeypatch)
        assert dem.delete_tile_cache(*self.BBOX) == {"deleted": 0, "errors": 0}

    def test_get_cache_stats_missing_dir_is_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path / "no_such_dir"))
        assert dem.get_cache_stats() == {"count": 0, "size_bytes": 0}

    def test_get_cache_stats_counts_png_only(self, tmp_path, monkeypatch):
        """枚数・総バイト数は .png のみ集計（ログ等の同居ファイルを数えない）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        d = tmp_path / "dem_png" / "123"
        d.mkdir(parents=True)
        (d / "1.png").write_bytes(b"abc")
        (d / "2.png").write_bytes(b"abcde")
        (d / "note.txt").write_bytes(b"zz")
        assert dem.get_cache_stats() == {"count": 2, "size_bytes": 8}

    def test_delete_all_removes_png_and_clears_memory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._fresh_memory_cache(monkeypatch)
        dem._tile_cache[("dem_png", 1, 2)] = np.zeros(1)
        dem._failed_tiles.add(("dem_png", 1, 2))
        d = tmp_path / "dem_png" / "1"
        d.mkdir(parents=True)
        (d / "2.png").write_bytes(b"\x89PNG")
        (tmp_path / "keep.txt").write_bytes(b"keep")

        res = dem.delete_all_tile_cache()

        assert res == {"deleted": 1}
        assert not (d / "2.png").exists()
        assert (tmp_path / "keep.txt").exists()   # .png 以外は消さない
        assert dem._tile_cache == {}
        assert dem._failed_tiles == set()
