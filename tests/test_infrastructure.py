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
# save_sim / save_app（キー群の論理分離・部分保存）
# ============================================================
class TestPartialConfigSave:

    def test_app_and_sim_keys_are_disjoint_and_cover_defaults(self):
        assert infra.APP_KEYS.isdisjoint(infra.SIM_KEYS)
        assert infra.APP_KEYS | infra.SIM_KEYS == frozenset(infra.DEFAULT_CONFIG)

    def test_save_sim_preserves_app_keys(self, tmp_path):
        """sim キー保存で app 設定（theme/lang/proxy_url）が消えないこと。"""
        path = str(tmp_path / "conf.json")
        seed = infra.DEFAULT_CONFIG.copy()
        seed["theme"] = "dark"
        seed["proxy_url"] = "http://proxy:8080"
        infra.save_config(seed, path)

        infra.save_sim({"freq": "5800.0", "theme": "light"}, path)  # theme は無視される
        loaded = infra.load_config(path)
        assert loaded["freq"] == "5800.0"          # sim キーは更新
        assert loaded["theme"] == "dark"           # app キーは保持（light で上書きされない）
        assert loaded["proxy_url"] == "http://proxy:8080"

    def test_save_app_preserves_sim_keys(self, tmp_path):
        """app キー保存で直近の sim パラメータが消えないこと。"""
        path = str(tmp_path / "conf.json")
        seed = infra.DEFAULT_CONFIG.copy()
        seed["freq"] = "900.0"
        infra.save_config(seed, path)

        infra.save_app({"theme": "dark", "freq": "1.0"}, path)      # freq は無視される
        loaded = infra.load_config(path)
        assert loaded["theme"] == "dark"           # app キーは更新
        assert loaded["freq"] == "900.0"           # sim キーは保持


# ============================================================
# select_sim（「パラメータ読込」は sim 限定）
# ============================================================
class TestSelectSim:

    def test_drops_app_keys(self):
        """app キー（theme/lang/proxy_url）は取り込まれない。"""
        incoming = {
            "freq": "5800.0", "h_tx": "40.0", "env_type": "rural",
            "theme": "dark", "lang": "ja", "proxy_url": "http://evil:8080",
        }
        out = infra.select_sim(incoming)
        assert out == {"freq": "5800.0", "h_tx": "40.0", "env_type": "rural"}
        assert infra.APP_KEYS.isdisjoint(out)

    def test_keeps_all_sim_keys_and_ignores_unknown(self):
        full = {k: infra.DEFAULT_CONFIG[k] for k in infra.SIM_KEYS}
        full["bogus"] = "x"                        # 未知キーも落ちる
        out = infra.select_sim(full)
        assert set(out) == set(infra.SIM_KEYS)


# ============================================================
# select_app（「アプリ設定読込」は app 限定）
# ============================================================
class TestSelectApp:

    def test_drops_sim_keys(self):
        """sim キー（freq/env_type 等）は取り込まれない。"""
        incoming = {
            "theme": "dark", "lang": "ja", "proxy_url": "http://p:8080",
            "freq": "5800.0", "env_type": "rural", "bogus": "x",
        }
        out = infra.select_app(incoming)
        assert out == {"theme": "dark", "lang": "ja", "proxy_url": "http://p:8080"}
        assert infra.SIM_KEYS.isdisjoint(out)

    def test_select_sim_and_select_app_partition_inputs(self):
        """同一入力に対し select_sim と select_app は素集合かつ既知キーを網羅。"""
        full = dict(infra.DEFAULT_CONFIG)
        sim, app = infra.select_sim(full), infra.select_app(full)
        assert set(sim).isdisjoint(app)
        assert set(sim) | set(app) == set(infra.DEFAULT_CONFIG)


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

        monkeypatch.setattr(infra, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        infra._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
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

        monkeypatch.setattr(infra, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        infra._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
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

        monkeypatch.setattr(infra, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        infra._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert fetch_calls == ["dem5a_png"]
        assert counts["downloaded_dem"] == 0

    def test_void_mask_matches_decode_semantics(self):
        """_void_mask が (128,0,0) のみを True とすること。"""
        arr = np.zeros((2, 2, 3), dtype=np.uint8)
        arr[0, 0] = (128, 0, 0)   # 無効値
        arr[0, 1] = (0, 0, 1)     # 標高 0.01m（有効）
        arr[1, 0] = (128, 0, 1)   # 有効（b!=0）
        mask = infra._void_mask(arr)
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
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        assert infra.scan_cache_overlay(
            self.LAT, self.LON, self.LAT - 0.01, self.LON + 0.01, 14
        ) == []

    def test_5a_wins_over_dem_at_same_cell(self, tmp_path, monkeypatch):
        """同じ zoom-14 セルに 5a と dem があれば最高精度 5a を返す。"""
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = infra._tile_coords(self.LAT, self.LON, 14)
        x15, y15, _, _ = infra._tile_coords(self.LAT, self.LON, 15)
        self._touch(tmp_path, "dem_png", x14, y14)
        self._touch(tmp_path, "dem5a_png", x15, y15)
        cells = infra.scan_cache_overlay(
            self.LAT + 0.01, self.LON - 0.01,
            self.LAT - 0.01, self.LON + 0.01, 14,
        )
        match = [c for c in cells if c["x"] == x14 and c["y"] == y14]
        assert len(match) == 1
        assert match[0]["level"] == "5a"
        assert match[0]["zoom"] == 14

    def test_dem_only_area(self, tmp_path, monkeypatch):
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = infra._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        cells = infra.scan_cache_overlay(
            self.LAT + 0.01, self.LON - 0.01,
            self.LAT - 0.01, self.LON + 0.01, 14,
        )
        assert all(c["level"] == "dem" for c in cells)
        assert any(c["x"] == x14 and c["y"] == y14 for c in cells)

    def test_tiles_outside_view_excluded(self, tmp_path, monkeypatch):
        """表示範囲外のキャッシュは返さない。"""
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = infra._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        # はるか遠方の小範囲を指定（対象タイルを含まない）
        cells = infra.scan_cache_overlay(43.07, 141.34, 43.06, 141.35, 14)
        assert cells == []

    # 日本全域を覆う bbox（filtering の端数で対象タイルを落とさないため広めに取る）
    WIDE = (46.0, 128.0, 30.0, 146.0)

    def _aligned_block_origin(self, span):
        """span×span に整列した zoom-14 ブロックの原点 (x0, y0) を返す。"""
        x14, y14, _, _ = infra._tile_coords(self.LAT, self.LON, 14)
        return (x14 // span) * span, (y14 // span) * span

    def test_full_aligned_block_merges_to_single_coarse_cell(self, tmp_path, monkeypatch):
        """完全に埋まった整列 4×4 ブロックは zoom-12 の単一セルへ統合される。"""
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        x0, y0 = self._aligned_block_origin(4)   # 4 = 2^(14-12)
        for dx in range(4):
            for dy in range(4):
                self._touch(tmp_path, "dem_png", x0 + dx, y0 + dy)
        cells = infra.scan_cache_overlay(*self.WIDE, 12)
        assert len(cells) == 1
        assert cells[0]["zoom"] == 12
        assert cells[0]["level"] == "dem"

    def test_partial_block_keeps_edges_fine(self, tmp_path, monkeypatch):
        """欠けのあるブロックは粗く統合されず、エッジは zoom-14 のまま残る。"""
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        x0, y0 = self._aligned_block_origin(4)
        for dx in range(4):
            for dy in range(4):
                if dx == 0 and dy == 0:
                    continue   # 1 隅を欠けさせる → 全体統合は不可
                self._touch(tmp_path, "dem_png", x0 + dx, y0 + dy)
        cells = infra.scan_cache_overlay(*self.WIDE, 12)
        # 単一の粗いセルにはならない（過大表示を防ぐ）
        assert len(cells) > 1
        # 細粒度（zoom-14）のセルが残る
        assert any(c["zoom"] == 14 for c in cells)
        # 欠けた隅 (x0, y0) は covered として返らない
        assert not any(c["zoom"] == 14 and c["x"] == x0 and c["y"] == y0 for c in cells)

    def test_count_cached_areas_counts_only_cached(self, tmp_path, monkeypatch):
        """count_cached_areas は実在キャッシュのみ数える（未取得は含めない）。"""
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = infra._tile_coords(self.LAT, self.LON, 14)
        # 2 エリアだけキャッシュ
        self._touch(tmp_path, "dem_png", x14, y14)
        self._touch(tmp_path, "dem_png", x14 + 1, y14)
        wide = (self.LAT + 0.1, self.LON - 0.1, self.LAT - 0.1, self.LON + 0.1)
        cached = infra.count_cached_areas(*wide)
        total = infra.count_bbox_tiles(*wide)
        assert cached == 2
        assert total > cached   # 範囲総数は未取得を含むので多い

    def test_count_cached_areas_zero_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        assert infra.count_cached_areas(*self.WIDE) == 0


class TestCoverageOutline:

    LAT, LON = 34.54, 132.41
    WIDE = (46.0, 128.0, 30.0, 146.0)

    def _touch(self, root, layer_id, x, y):
        d = os.path.join(root, layer_id, str(x))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{y}.png"), "wb") as f:
            f.write(b"\x89PNG")

    def test_empty_cache_no_loops(self, tmp_path, monkeypatch):
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        assert infra.coverage_outline(*self.WIDE) == []

    def test_single_cell_is_rectangle(self, tmp_path, monkeypatch):
        """単一セル → 4 頂点の矩形ループ1個。"""
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = infra._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        loops = infra.coverage_outline(*self.WIDE)
        assert len(loops) == 1
        assert len(loops[0]) == 4

    def test_adjacent_cells_merge_to_one_outline(self, tmp_path, monkeypatch):
        """隣接2セルは内部線なしの単一矩形（4頂点）になる。"""
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = infra._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        self._touch(tmp_path, "dem_png", x14 + 1, y14)
        loops = infra.coverage_outline(*self.WIDE)
        assert len(loops) == 1
        assert len(loops[0]) == 4   # 内部の共有辺は相殺され角は4つ

    def test_l_shape_has_six_corners(self, tmp_path, monkeypatch):
        """L字（2×2 から1セル欠け）は6頂点のループ。"""
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = infra._tile_coords(self.LAT, self.LON, 14)
        for dx in (0, 1):
            for dy in (0, 1):
                if dx == 1 and dy == 1:
                    continue
                self._touch(tmp_path, "dem_png", x14 + dx, y14 + dy)
        loops = infra.coverage_outline(*self.WIDE)
        assert len(loops) == 1
        assert len(loops[0]) == 6


class TestBasemapTiles:
    """淡色地図（レポート地図）タイルの取得・キャッシュ・削除。"""

    LAT, LON = 34.54, 132.41
    WIDE = (34.6, 132.3, 34.4, 132.5)

    def test_tile_path_includes_zoom(self):
        """キャッシュパスにズームが入る（異なるズームの同一(x,y)が衝突しない）。"""
        subdir, path = infra._basemap_tile_path(14, 100, 200)
        assert os.path.join(infra.BASEMAP_SUBDIR, "14", "100") in subdir
        assert path.endswith(os.path.join("100", "200.png"))
        # ズーム違いはパスが異なる。
        _, path15 = infra._basemap_tile_path(15, 100, 200)
        assert path != path15

    def test_fetch_basemap_tiles_parallel_returns_dict(self, monkeypatch):
        """並列取得が成功タイルだけを {(x,y):配列} で返す。"""
        def fake(layer_id, zoom, x, y, subdir, path):
            return np.full((256, 256, 3), 100, dtype=np.uint8)
        monkeypatch.setattr(infra, "_fetch_tile", fake)
        tiles = [(1, 2), (3, 4), (5, 6)]
        out = infra.fetch_basemap_tiles(tiles, 14)
        assert set(out.keys()) == set(tiles)

    def test_fetch_basemap_tiles_empty_input(self):
        assert infra.fetch_basemap_tiles([], 14) == {}

    def test_fetch_basemap_tiles_skips_failures(self, monkeypatch):
        """取得失敗（None）のタイルは結果に含めない。"""
        monkeypatch.setattr(infra, "_fetch_tile", lambda *a, **k: None)
        assert infra.fetch_basemap_tiles([(1, 2)], 14) == {}

    def test_delete_tile_cache_removes_basemap(self, tmp_path, monkeypatch):
        """エリア範囲削除が basemap タイル（ズーム別）も回収する。"""
        monkeypatch.setattr(infra, "CACHE_DIR", str(tmp_path))
        z = 14
        x, y, _, _ = infra._tile_coords(self.LAT, self.LON, z)
        subdir, path = infra._basemap_tile_path(z, x, y)
        os.makedirs(subdir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"\x89PNG")
        assert os.path.exists(path)
        infra.delete_tile_cache(*self.WIDE)
        assert not os.path.exists(path)
