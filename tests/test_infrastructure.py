"""
tests/test_infrastructure.py
============================
infrastructure.py のユニットテスト。
HTTP 通信は monkeypatch で差し替え、ネットワーク接続不要。
"""

import json
import os
import unittest.mock as mock

import numpy as np
import pytest
import requests

import infrastructure as infra


# ============================================================
# validate_config
# ============================================================
class TestValidateConfig:

    def _valid(self) -> dict[str, str]:
        return {
            "start"      : "34.5429, 132.4118",
            "end"        : "34.5389, 132.4050",
            "h_tx"       : "30.0",
            "h_rx"       : "10.0",
            "freq"       : "2400.0",
            "p_tx"       : "20.0",
            "gain_tx"    : "3.0",
            "gain_rx"    : "3.0",
            "sens"       : "-85.0",
            "veg_h"      : "10.0",
            "k_factor"   : "10.0",
            "samples"    : "200",
            "rain_rate"  : "0.0",
            "diff_method": "deygout",
        }

    def test_valid_input_no_errors(self):
        assert infra.validate_config(self._valid()) == []

    def test_freq_below_range(self):
        c = self._valid()
        c["freq"] = "0.5"
        assert any("freq" in e for e in infra.validate_config(c))

    def test_freq_above_range(self):
        c = self._valid()
        c["freq"] = "200000"
        assert any("freq" in e for e in infra.validate_config(c))

    def test_non_numeric_value(self):
        c = self._valid()
        c["p_tx"] = "abc"
        assert any("p_tx" in e for e in infra.validate_config(c))

    def test_invalid_coord_format_no_comma(self):
        c = self._valid()
        c["start"] = "34.5429"
        assert any("start" in e for e in infra.validate_config(c))

    def test_latitude_out_of_range(self):
        c = self._valid()
        c["start"] = "91.0, 132.0"
        errors = infra.validate_config(c)
        assert any("start" in e and "Latitude" in e for e in errors)

    def test_longitude_out_of_range(self):
        c = self._valid()
        c["end"] = "34.0, 181.0"
        errors = infra.validate_config(c)
        assert any("end" in e and "Longitude" in e for e in errors)

    def test_identical_coordinates(self):
        c = self._valid()
        c["end"] = c["start"]
        assert any("identical" in e.lower() for e in infra.validate_config(c))

    def test_all_validation_rule_keys_covered(self):
        """VALIDATION_RULES の全キーに対してエラー検出が機能すること。"""
        for key in infra.VALIDATION_RULES:
            c = self._valid()
            _, vmax, _ = infra.VALIDATION_RULES[key]
            c[key] = str(vmax + 1)
            errors = infra.validate_config(c)
            assert any(key in e for e in errors), (
                f"VALIDATION_RULES['{key}'] のエラー検出が機能していない"
            )

    def test_sens_lower_boundary_valid(self):
        c = self._valid()
        c["sens"] = "-130.0"
        assert infra.validate_config(c) == []

    def test_sens_below_lower_boundary(self):
        c = self._valid()
        c["sens"] = "-131.0"
        assert any("sens" in e for e in infra.validate_config(c))

    def test_samples_integer_string_is_valid(self):
        c = self._valid()
        c["samples"] = "10"
        assert infra.validate_config(c) == []

    def test_rain_rate_below_range(self):
        c = self._valid()
        c["rain_rate"] = "-1.0"
        assert any("rain_rate" in e for e in infra.validate_config(c))

    def test_rain_rate_above_range(self):
        c = self._valid()
        c["rain_rate"] = "201.0"
        assert any("rain_rate" in e for e in infra.validate_config(c))

    def test_rain_rate_zero_is_valid(self):
        c = self._valid()
        c["rain_rate"] = "0.0"
        assert infra.validate_config(c) == []

    def test_rain_rate_max_is_valid(self):
        c = self._valid()
        c["rain_rate"] = "200.0"
        assert infra.validate_config(c) == []

    def test_diff_method_invalid(self):
        c = self._valid()
        c["diff_method"] = "invalid"
        assert any("diff_method" in e for e in infra.validate_config(c))

    def test_diff_method_deygout_is_valid(self):
        c = self._valid()
        c["diff_method"] = "deygout"
        assert infra.validate_config(c) == []

    def test_diff_method_single_is_valid(self):
        c = self._valid()
        c["diff_method"] = "single"
        assert infra.validate_config(c) == []

    def test_latitude_86_rejected(self):
        """85.05° 超は Web Mercator 範囲外として拒否されること。"""
        c = self._valid()
        c["start"] = "86.0, 132.0"
        assert any("Latitude" in e for e in infra.validate_config(c))

    def test_latitude_85_0_accepted(self):
        """±85.0° は許可されること。"""
        c = self._valid()
        c["start"] = "85.0, 132.0"
        c["end"]   = "-85.0, 131.0"
        assert infra.validate_config(c) == []


# ============================================================
# load_config / save_config
# ============================================================
class TestConfigIO:

    def test_load_returns_default_when_file_absent(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        assert infra.load_config(path) == infra.DEFAULT_CONFIG

    def test_save_and_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "conf.json")
        conf = infra.DEFAULT_CONFIG.copy()
        conf["freq"] = "5800.0"
        infra.save_config(conf, path)
        loaded = infra.load_config(path)
        assert loaded["freq"] == "5800.0"

    def test_load_merges_with_defaults(self, tmp_path):
        """ファイルに一部キーしかなくてもデフォルトで補完される。"""
        path = str(tmp_path / "partial.json")
        with open(path, "w") as f:
            json.dump({"freq": "900.0"}, f)
        config = infra.load_config(path)
        assert config["freq"] == "900.0"
        assert "p_tx" in config

    def test_save_creates_valid_json(self, tmp_path):
        path = str(tmp_path / "out.json")
        infra.save_config(infra.DEFAULT_CONFIG, path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert "freq" in data


# ============================================================
# _decode_elevation
# ============================================================
class TestDecodeElevation:

    def test_invalid_pixel_128_0_0_returns_zero(self):
        """(128, 0, 0) は無効値 → 0.0 m。"""
        rgb = np.array([128, 0, 0], dtype=np.uint8)
        assert infra._decode_elevation(rgb) == pytest.approx(0.0)

    def test_zero_rgb_returns_zero(self):
        rgb = np.array([0, 0, 0], dtype=np.uint8)
        assert infra._decode_elevation(rgb) == pytest.approx(0.0)

    def test_positive_elevation(self):
        """x = 10000 → 100.00 m。"""
        x = 10000
        rgb = np.array([x >> 16, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        assert infra._decode_elevation(rgb) == pytest.approx(100.0, abs=0.01)

    def test_negative_elevation(self):
        """x = 16776216 → -10.00 m（海面下）。"""
        x = 16776216
        rgb = np.array([(x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        assert infra._decode_elevation(rgb) == pytest.approx(-10.0, abs=0.01)

    def test_boundary_x_8388607_positive(self):
        """x = 8388607 (< 8388608) → 正の標高。ただし (128,0,0) は無効値なので避ける。"""
        # x = 8388607 → r=(127, g=255, b=255) で無効値ピクセルには該当しない
        x = 8388607
        r = (x >> 16) & 0xFF   # 127
        g = (x >> 8)  & 0xFF   # 255
        b = x & 0xFF            # 255
        assert r != 128, "このテスト用ピクセルが無効値(128,0,0)と誤判定される"
        rgb = np.array([r, g, b], dtype=np.uint8)
        assert infra._decode_elevation(rgb) == pytest.approx(x * 0.01, abs=0.01)

    def test_boundary_x_8388608_negative(self):
        """x = 8388608 は RGB=(128,0,0) となり無効値扱いで 0.0 を返す（仕様）。
        代わりに x=8388609 で負の標高デコードを検証する。"""
        # x=8388608 → r=128,g=0,b=0 = 無効値ピクセル → 0.0 が正しい挙動
        x_invalid = 8388608
        r = (x_invalid >> 16) & 0xFF  # 128
        g = (x_invalid >> 8)  & 0xFF  # 0
        b = x_invalid & 0xFF           # 0
        rgb_invalid = np.array([r, g, b], dtype=np.uint8)
        assert infra._decode_elevation(rgb_invalid) == pytest.approx(0.0), (
            "x=8388608 は (128,0,0) = 無効値ピクセルなので 0.0 を返す"
        )

        # x=8388609 で負の標高デコードを確認
        x = 8388609
        rgb = np.array([(x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        assert infra._decode_elevation(rgb) == pytest.approx((x - 16777216) * 0.01, abs=0.01)


# ============================================================
# get_elevation / _fetch_tile（monkeypatch）
# ============================================================
class TestGetElevation:

    @pytest.fixture(autouse=True)
    def clear_tile_cache(self):
        """テスト間でメモリキャッシュをリセットする。"""
        infra._tile_cache.clear()
        infra._failed_tiles.clear()
        yield
        infra._tile_cache.clear()
        infra._failed_tiles.clear()

    def test_returns_float(self, monkeypatch):
        tile = np.full((256, 256, 3), [0, 39, 16], dtype=np.uint8)
        monkeypatch.setattr(infra, "_fetch_tile", lambda *a, **kw: tile)
        assert isinstance(infra.get_elevation(34.5429, 132.4118), float)

    def test_uses_decoded_pixel_value(self, monkeypatch):
        """_fetch_tile が返したピクセルを正しくデコードすること。"""
        x     = 10000  # 100.00 m
        pixel = np.array([x >> 16, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        tile  = np.full((256, 256, 3), pixel, dtype=np.uint8)
        monkeypatch.setattr(infra, "_fetch_tile", lambda *a, **kw: tile)
        assert infra.get_elevation(34.5429, 132.4118) == pytest.approx(100.0, abs=0.1)

    def test_returns_zero_when_fetch_returns_none(self, monkeypatch):
        """_fetch_tile が None を返したとき 0.0 になること。"""
        monkeypatch.setattr(infra, "_fetch_tile", lambda *a, **kw: None)
        assert infra.get_elevation(34.5429, 132.4118) == pytest.approx(0.0)

    def test_tile_cached_after_first_call(self, monkeypatch):
        """同じタイルへの2回目の呼び出しで _fetch_tile が呼ばれないこと。"""
        tile = np.full((256, 256, 3), [0, 39, 16], dtype=np.uint8)
        call_count = {"n": 0}

        def fake_fetch(*a, **kw):
            call_count["n"] += 1
            return tile

        monkeypatch.setattr(infra, "_fetch_tile", fake_fetch)
        infra.get_elevation(34.5429, 132.4118)
        infra.get_elevation(34.5429, 132.4118)
        assert call_count["n"] == 1


class TestFetchTile:

    def _mock_session(self, monkeypatch, *, side_effect=None, return_value=None):
        """_get_session() をモックセッションに差し替えるヘルパー。"""
        fake_session = mock.Mock()
        if side_effect is not None:
            fake_session.get.side_effect = side_effect
        else:
            fake_session.get.return_value = return_value
        monkeypatch.setattr(infra, "_get_session", lambda: fake_session)
        return fake_session

    def test_returns_none_on_network_error_no_cache(self, tmp_path, monkeypatch):
        """ネットワークエラー＆キャッシュなし → None。"""
        self._mock_session(monkeypatch, side_effect=requests.RequestException("timeout"))
        result = infra._fetch_tile(
            "dem_png", 14, 99999, 99999, str(tmp_path), str(tmp_path / "x.png")
        )
        assert result is None

    def test_uses_disk_cache_on_network_error(self, tmp_path, monkeypatch):
        """ネットワークエラー時にディスクキャッシュがあればそれを返す。"""
        from PIL import Image

        cache_path = tmp_path / "tile.png"
        Image.new("RGB", (256, 256), (0, 39, 16)).save(str(cache_path))

        self._mock_session(monkeypatch, side_effect=requests.RequestException("err"))
        arr = infra._fetch_tile("dem_png", 14, 0, 0, str(tmp_path), str(cache_path))
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
        infra._fetch_tile("dem5a_png", 15, 0, 0, str(tmp_path), cache_path)
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

        arr = infra._fetch_tile("dem5a_png", 15, 0, 0, str(tmp_path), str(tmp_path / "t.png"))
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

        arr = infra._fetch_tile("dem_png", 14, 0, 0, str(tmp_path), str(cache_path))
        assert arr is not None
        assert arr.shape == (256, 256, 3)


# ============================================================
# プロキシ / セッション管理
# ============================================================
class TestProxy:

    def test_proxy_url_in_default_config(self):
        """proxy_url が DEFAULT_CONFIG に含まれていること。"""
        assert "proxy_url" in infra.DEFAULT_CONFIG
        assert infra.DEFAULT_CONFIG["proxy_url"] == ""

    def test_load_config_fills_proxy_url(self, tmp_path):
        """proxy_url が未定義の古い config.json でもデフォルト補完されること。"""
        cfg_path = str(tmp_path / "conf.json")
        import json
        with open(cfg_path, "w") as f:
            json.dump({"freq": "2400.0"}, f)
        loaded = infra.load_config(cfg_path)
        assert "proxy_url" in loaded
        assert loaded["proxy_url"] == ""

    def test_set_proxy_resets_session(self):
        """set_proxy() を呼ぶと既存セッションが破棄されること。"""
        infra.set_proxy("")
        s1 = infra._get_session()
        infra.set_proxy("http://proxy.example.com:8080")
        assert infra._http_session is None  # リセット確認
        s2 = infra._get_session()
        assert s1 is not s2

    def test_get_session_singleton(self):
        """_get_session() は同一セッションを返すこと（再生成しない）。"""
        infra.set_proxy("")
        s1 = infra._get_session()
        s2 = infra._get_session()
        assert s1 is s2

    def teardown_method(self):
        """各テスト後にセッションをリセットしてテスト間干渉を防ぐ。"""
        infra.set_proxy("")


# ============================================================
# i18n キー網羅性
# ============================================================
class TestI18n:

    def test_all_en_keys_exist_in_ja(self):
        """英語キーがすべて日本語にも定義されていること。"""
        import i18n
        en_keys = set(i18n._STRINGS["en"].keys())
        ja_keys = set(i18n._STRINGS["ja"].keys())
        missing = en_keys - ja_keys
        assert not missing, f"'ja' に未定義のキー: {sorted(missing)}"

    def test_all_ja_keys_exist_in_en(self):
        """日本語キーがすべて英語にも定義されていること。"""
        import i18n
        en_keys = set(i18n._STRINGS["en"].keys())
        ja_keys = set(i18n._STRINGS["ja"].keys())
        missing = ja_keys - en_keys
        assert not missing, f"'en' に未定義のキー: {sorted(missing)}"

    def test_no_empty_values(self):
        """すべての翻訳値が空文字でないこと。"""
        import i18n
        for lang, strings in i18n._STRINGS.items():
            for key, val in strings.items():
                assert val != "", f"空の翻訳値: lang='{lang}' key='{key}'"


# ============================================================
# _enumerate_bbox / count_bbox_tiles
# ============================================================
class TestEnumerateBbox:

    def test_returns_6_tuple_per_tile(self):
        tiles = infra._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        assert all(len(t) == 6 for t in tiles)

    def test_covers_all_dem_layers(self):
        tiles = infra._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        layer_ids = {t[0] for t in tiles}
        assert layer_ids == {lid for lid, _ in infra.DEM_LAYERS}

    def test_at_least_one_tile_per_layer(self):
        tiles = infra._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        for layer_id, _ in infra.DEM_LAYERS:
            assert any(t[0] == layer_id for t in tiles)

    def test_inverted_coords_same_result(self):
        """lat1/lon1 が NW でなくても同じ結果を返す（入力順に依存しない）。"""
        tiles_nw_se = infra._enumerate_bbox(34.54, 132.40, 34.53, 132.41)
        tiles_se_nw = infra._enumerate_bbox(34.53, 132.41, 34.54, 132.40)
        assert set(t[:4] for t in tiles_nw_se) == set(t[:4] for t in tiles_se_nw)

    def test_larger_area_returns_more_tiles(self):
        small = infra._enumerate_bbox(34.540, 132.410, 34.539, 132.409)
        large = infra._enumerate_bbox(34.600, 132.500, 34.400, 132.300)
        assert len(large) > len(small)

    def test_tile_coords_in_valid_range(self):
        """タイル座標がズームレベルに対して有効な範囲内であること。"""
        tiles = infra._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        for layer_id, zoom, x, y, subdir, cache_path in tiles:
            assert 0 <= x < 2 ** zoom
            assert 0 <= y < 2 ** zoom

    def test_cache_path_contains_layer_and_coords(self):
        """cache_path が layer_id / x / y.png の構造を持つこと。"""
        tiles = infra._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        for layer_id, zoom, x, y, subdir, cache_path in tiles:
            assert layer_id in cache_path
            assert str(x) in cache_path
            assert cache_path.endswith(f"{y}.png")


class TestCountBboxTiles:

    def test_returns_zoom14_position_count(self):
        """count_bbox_tiles は zoom-14 位置数（エリア数）を返す。"""
        lat1, lon1, lat2, lon2 = 34.54, 132.41, 34.53, 132.40
        count = infra.count_bbox_tiles(lat1, lon1, lat2, lon2)
        positions = list(infra._iter_dem_positions(lat1, lon1, lat2, lon2))
        assert count == len(positions)

    def test_returns_positive_integer(self):
        count = infra.count_bbox_tiles(34.54, 132.41, 34.53, 132.40)
        assert isinstance(count, int)
        assert count > 0

    def test_inverted_coords_same_result(self):
        """入力座標の順序に依存しないこと。"""
        assert infra.count_bbox_tiles(34.54, 132.41, 34.53, 132.40) == \
               infra.count_bbox_tiles(34.53, 132.40, 34.54, 132.41)


# ============================================================
# _iter_dem_positions
# ============================================================
class TestIterDemPositions:

    def test_yields_tuples_with_correct_structure(self):
        """各 yield 値が (x14, y14, subdir, path, zoom15_tiles) の構造を持つ。"""
        positions = list(infra._iter_dem_positions(34.54, 132.41, 34.53, 132.40))
        assert len(positions) > 0
        for x14, y14, subdir, path, zoom15_tiles in positions:
            assert isinstance(x14, int)
            assert isinstance(y14, int)
            assert path.endswith(f"{y14}.png")
            assert str(x14) in path
            assert len(zoom15_tiles) >= 1

    def test_zoom15_tiles_are_sub_tiles_of_zoom14(self):
        """zoom-15 サブタイルが対応する zoom-14 の子タイル範囲内に収まること。"""
        positions = list(infra._iter_dem_positions(34.54, 132.41, 34.53, 132.40))
        for x14, y14, _, _, zoom15_tiles in positions:
            for x15, y15, *_ in zoom15_tiles:
                assert x14 * 2 <= x15 <= x14 * 2 + 1
                assert y14 * 2 <= y15 <= y14 * 2 + 1

    def test_inverted_coords_same_result(self):
        pos_ab = list(infra._iter_dem_positions(34.54, 132.41, 34.53, 132.40))
        pos_ba = list(infra._iter_dem_positions(34.53, 132.40, 34.54, 132.41))
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
        monkeypatch.setattr(infra, "_fetch_tile", lambda *a, **kw: None)
        counts = self._make_counts()
        lock = threading.Lock()
        infra._process_position(0, 0, str(tmp_path), str(dem_path), [], False, counts, lock)
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

        monkeypatch.setattr(infra, "_fetch_tile", mock_fetch)
        subdir5a = str(tmp_path / "5a" / "0"); subdir5b = str(tmp_path / "5b" / "0")
        zoom15 = [(0, 0, subdir5a, str(tmp_path / "5a.png"),
                         subdir5b, str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        infra._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
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

        monkeypatch.setattr(infra, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        infra._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert counts["downloaded_5b"] == 1
        assert counts["downloaded_dem"] == 0

    def test_falls_back_to_dem_when_both_5m_fail(self, tmp_path, monkeypatch):
        """5a・5b 両方失敗 → dem_png DL。"""
        import threading
        tile_arr = np.zeros((256, 256, 3), dtype=np.uint8)

        def mock_fetch(layer_id, *a, **kw):
            return tile_arr if layer_id == "dem_png" else None

        monkeypatch.setattr(infra, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        infra._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
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

        monkeypatch.setattr(infra, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        infra._process_position(0, 0, str(tmp_path), str(dem_path),
                                 zoom15, True, counts, lock)
        assert counts["skipped"] == 0
        assert counts["downloaded_5a"] == 1
