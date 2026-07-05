"""
tests/test_config.py
====================
config.py のユニットテスト（アプリ設定 I/O・入力バリデーション）。

i18n キーの網羅性チェック（TestI18n）は、検証メッセージ（validate_config）の
主要な i18n 消費者である config 側にまとめてここへ置く。
"""

import json

import config


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
        assert config.validate_config(self._valid()) == []

    def test_freq_below_range(self):
        c = self._valid()
        c["freq"] = "0.5"
        assert any("freq" in e for e in config.validate_config(c))

    def test_freq_above_range(self):
        c = self._valid()
        c["freq"] = "200000"
        assert any("freq" in e for e in config.validate_config(c))

    def test_non_numeric_value(self):
        c = self._valid()
        c["p_tx"] = "abc"
        assert any("p_tx" in e for e in config.validate_config(c))

    def test_invalid_coord_format_no_comma(self):
        c = self._valid()
        c["start"] = "34.5429"
        assert any("start" in e for e in config.validate_config(c))

    def test_latitude_out_of_range(self):
        c = self._valid()
        c["start"] = "91.0, 132.0"
        errors = config.validate_config(c)
        assert any("start" in e and "Latitude" in e for e in errors)

    def test_longitude_out_of_range(self):
        c = self._valid()
        c["end"] = "34.0, 181.0"
        errors = config.validate_config(c)
        assert any("end" in e and "Longitude" in e for e in errors)

    def test_identical_coordinates(self):
        c = self._valid()
        c["end"] = c["start"]
        assert any("identical" in e.lower() for e in config.validate_config(c))

    def test_all_validation_rule_keys_covered(self):
        """VALIDATION_RULES の全キーに対してエラー検出が機能すること。"""
        for key in config.VALIDATION_RULES:
            c = self._valid()
            _, vmax, _ = config.VALIDATION_RULES[key]
            c[key] = str(vmax + 1)
            errors = config.validate_config(c)
            assert any(key in e for e in errors), (
                f"VALIDATION_RULES['{key}'] のエラー検出が機能していない"
            )

    def test_sens_lower_boundary_valid(self):
        c = self._valid()
        c["sens"] = "-130.0"
        assert config.validate_config(c) == []

    def test_sens_below_lower_boundary(self):
        c = self._valid()
        c["sens"] = "-131.0"
        assert any("sens" in e for e in config.validate_config(c))

    def test_samples_integer_string_is_valid(self):
        c = self._valid()
        c["samples"] = "10"
        assert config.validate_config(c) == []

    def test_rain_rate_below_range(self):
        c = self._valid()
        c["rain_rate"] = "-1.0"
        assert any("rain_rate" in e for e in config.validate_config(c))

    def test_rain_rate_above_range(self):
        c = self._valid()
        c["rain_rate"] = "201.0"
        assert any("rain_rate" in e for e in config.validate_config(c))

    def test_rain_rate_zero_is_valid(self):
        c = self._valid()
        c["rain_rate"] = "0.0"
        assert config.validate_config(c) == []

    def test_rain_rate_max_is_valid(self):
        c = self._valid()
        c["rain_rate"] = "200.0"
        assert config.validate_config(c) == []

    def test_diff_method_invalid(self):
        c = self._valid()
        c["diff_method"] = "invalid"
        assert any("diff_method" in e for e in config.validate_config(c))

    def test_diff_method_deygout_is_valid(self):
        c = self._valid()
        c["diff_method"] = "deygout"
        assert config.validate_config(c) == []

    def test_diff_method_single_is_valid(self):
        c = self._valid()
        c["diff_method"] = "single"
        assert config.validate_config(c) == []

    def test_latitude_86_rejected(self):
        """85.05° 超は Web Mercator 範囲外として拒否されること。"""
        c = self._valid()
        c["start"] = "86.0, 132.0"
        assert any("Latitude" in e for e in config.validate_config(c))

    def test_latitude_85_0_accepted(self):
        """±85.0° は許可されること。"""
        c = self._valid()
        c["start"] = "85.0, 132.0"
        c["end"]   = "-85.0, 131.0"
        assert config.validate_config(c) == []


# ============================================================
# load_config / save_config
# ============================================================
class TestConfigIO:

    def test_load_returns_default_when_file_absent(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        assert config.load_config(path) == config.DEFAULT_CONFIG

    def test_save_and_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "conf.json")
        conf = config.DEFAULT_CONFIG.copy()
        conf["freq"] = "5800.0"
        config.save_config(conf, path)
        loaded = config.load_config(path)
        assert loaded["freq"] == "5800.0"

    def test_load_merges_with_defaults(self, tmp_path):
        """ファイルに一部キーしかなくてもデフォルトで補完される。"""
        path = str(tmp_path / "partial.json")
        with open(path, "w") as f:
            json.dump({"freq": "900.0"}, f)
        cfg = config.load_config(path)
        assert cfg["freq"] == "900.0"
        assert "p_tx" in cfg

    def test_save_creates_valid_json(self, tmp_path):
        path = str(tmp_path / "out.json")
        config.save_config(config.DEFAULT_CONFIG, path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert "freq" in data


# ============================================================
# save_sim / save_app（キー群の論理分離・部分保存）
# ============================================================
class TestPartialConfigSave:

    def test_app_and_sim_keys_are_disjoint_and_cover_defaults(self):
        assert config.APP_KEYS.isdisjoint(config.SIM_KEYS)
        assert config.APP_KEYS | config.SIM_KEYS == frozenset(config.DEFAULT_CONFIG)

    def test_coord_format_is_app_key(self):
        """座標形式は表示の好み＝app 設定（sim パラメータには混ざらない）。"""
        assert "coord_format" in config.APP_KEYS
        assert "coord_format" not in config.SIM_KEYS
        assert config.DEFAULT_CONFIG["coord_format"] == "dd"
        assert "coord_format" in config.select_app({"coord_format": "dms"})
        assert "coord_format" not in config.select_sim({"coord_format": "dms"})

    def test_save_sim_preserves_app_keys(self, tmp_path):
        """sim キー保存で app 設定（theme/lang/proxy_url）が消えないこと。"""
        path = str(tmp_path / "conf.json")
        seed = config.DEFAULT_CONFIG.copy()
        seed["theme"] = "dark"
        seed["proxy_url"] = "http://proxy:8080"
        config.save_config(seed, path)

        config.save_sim({"freq": "5800.0", "theme": "light"}, path)  # theme は無視される
        loaded = config.load_config(path)
        assert loaded["freq"] == "5800.0"          # sim キーは更新
        assert loaded["theme"] == "dark"           # app キーは保持（light で上書きされない）
        assert loaded["proxy_url"] == "http://proxy:8080"

    def test_save_app_preserves_sim_keys(self, tmp_path):
        """app キー保存で直近の sim パラメータが消えないこと。"""
        path = str(tmp_path / "conf.json")
        seed = config.DEFAULT_CONFIG.copy()
        seed["freq"] = "900.0"
        config.save_config(seed, path)

        config.save_app({"theme": "dark", "freq": "1.0"}, path)      # freq は無視される
        loaded = config.load_config(path)
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
        out = config.select_sim(incoming)
        assert out == {"freq": "5800.0", "h_tx": "40.0", "env_type": "rural"}
        assert config.APP_KEYS.isdisjoint(out)

    def test_keeps_all_sim_keys_and_ignores_unknown(self):
        full = {k: config.DEFAULT_CONFIG[k] for k in config.SIM_KEYS}
        full["bogus"] = "x"                        # 未知キーも落ちる
        out = config.select_sim(full)
        assert set(out) == set(config.SIM_KEYS)


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
        out = config.select_app(incoming)
        assert out == {"theme": "dark", "lang": "ja", "proxy_url": "http://p:8080"}
        assert config.SIM_KEYS.isdisjoint(out)

    def test_select_sim_and_select_app_partition_inputs(self):
        """同一入力に対し select_sim と select_app は素集合かつ既知キーを網羅。"""
        full = dict(config.DEFAULT_CONFIG)
        sim, app = config.select_sim(full), config.select_app(full)
        assert set(sim).isdisjoint(app)
        assert set(sim) | set(app) == set(config.DEFAULT_CONFIG)


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
