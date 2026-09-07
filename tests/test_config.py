"""
tests/test_config.py
====================
config.py のユニットテスト（アプリ設定 I/O・入力バリデーション）。

i18n キーの網羅性チェック（TestI18n）は、検証メッセージ（validate_config）の
主要な i18n 消費者である config 側にまとめてここへ置く。
"""

import json
import os
import re
import sys
import types
import unittest.mock as mock
from pathlib import Path

import pytest

from core import config

ROOT = Path(__file__).resolve().parent.parent


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
            "diff_method": "bullington",
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

    def test_diff_method_bullington_is_valid(self):
        c = self._valid()
        c["diff_method"] = "bullington"
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
# 設定の原子的な保存 — B-124 回帰ガード
# ============================================================
class TestAtomicConfigSave:
    """**壊れた不変条件**＝「書き換えの途中で死んでも、前の内容は失われない」。

    `open(path, "w")` を直に開くと**開いた時点で中身が消える**ので、`json.dump` の
    最中に落ちれば空か途中までの JSON が残り、次の起動で `load_config` が握って
    **全設定が既定値へ戻る**（`proxy_url` が消えると DEM 取得が全滅する）。

    ⚠️ **「保存中に落ちる」は自然には再現しない**ので注入で書く＝ゲートが本当に
    効いているかは変異検証（素の `open(path,"w")` に戻すと落ちる）で担保する。
    """

    def _seed(self, path):
        seed = config.DEFAULT_CONFIG.copy()
        seed["proxy_url"] = "http://proxy:8080"
        seed["freq"] = "900.0"
        config.save_config(seed, path)
        return seed

    def test_crash_midway_leaves_the_previous_config_intact(self, tmp_path, monkeypatch):
        """保存の最中に落ちても、**前の設定が丸ごと残る**こと。"""
        path = str(tmp_path / "conf.json")
        self._seed(path)

        def exploding_dump(*a, **kw):
            raise KeyboardInterrupt("死んだことにする")

        # json.dump の最中に死ぬ＝旧ファイルを開いて捨てたあとに落ちる形。
        monkeypatch.setattr(config.json, "dump", exploding_dump)
        with pytest.raises(KeyboardInterrupt):
            config.save_config({"freq": "5800.0"}, path)

        loaded = config.load_config(path)
        assert loaded["proxy_url"] == "http://proxy:8080", "前の設定が消えた"
        assert loaded["freq"] == "900.0"

    def test_save_overwrites_an_existing_file(self, tmp_path):
        """既存ファイルがあっても**上書きする**こと。

        ⚠️ B-123 のヘルパ（`dem._write_tile_atomic`＝既に在るなら書かない）を
        そのまま流用すると、ここが落ちる。**設定は上書きこそが目的。**
        """
        path = str(tmp_path / "conf.json")
        self._seed(path)

        updated = config.DEFAULT_CONFIG.copy()
        updated["freq"] = "5800.0"
        config.save_config(updated, path)

        assert config.load_config(path)["freq"] == "5800.0"

    def test_no_temp_file_is_left_behind(self, tmp_path, monkeypatch):
        """成功しても失敗しても、一時ファイルを残さないこと。"""
        path = str(tmp_path / "conf.json")
        config.save_config(config.DEFAULT_CONFIG, path)
        assert [p.name for p in tmp_path.iterdir()] == ["conf.json"]

        def failing_dump(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(config.json, "dump", failing_dump)
        config.save_config(config.DEFAULT_CONFIG, path)   # 例外は握られる
        assert [p.name for p in tmp_path.iterdir()] == ["conf.json"]

    def test_save_failure_does_not_raise(self, tmp_path, monkeypatch):
        """保存に失敗してもアプリを止めない（従来どおり警告に留める）こと。"""
        path = str(tmp_path / "conf.json")
        monkeypatch.setattr(
            config.json, "dump",
            mock.Mock(side_effect=OSError("read-only file system")),
        )
        config.save_config(config.DEFAULT_CONFIG, path)   # 例外が出なければ合格
        assert not os.path.exists(path)


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
        from core import i18n
        en_keys = set(i18n._STRINGS["en"].keys())
        ja_keys = set(i18n._STRINGS["ja"].keys())
        missing = en_keys - ja_keys
        assert not missing, f"'ja' に未定義のキー: {sorted(missing)}"

    def test_all_ja_keys_exist_in_en(self):
        """日本語キーがすべて英語にも定義されていること。"""
        from core import i18n
        en_keys = set(i18n._STRINGS["en"].keys())
        ja_keys = set(i18n._STRINGS["ja"].keys())
        missing = ja_keys - en_keys
        assert not missing, f"'en' に未定義のキー: {sorted(missing)}"

    def test_no_empty_values(self):
        """すべての翻訳値が空文字でないこと。"""
        from core import i18n
        for lang, strings in i18n._STRINGS.items():
            for key, val in strings.items():
                assert val != "", f"空の翻訳値: lang='{lang}' key='{key}'"


# ============================================================
# 実行ごとの出力ディレクトリ（B-013）
# ============================================================
class TestNewRunDir:
    """秒精度のタイムスタンプが衝突しても成果物を上書きしないこと。

    バッチと条件探索が同じ欠陥を持っていたので、解決器を config へ一本化した
    （単一実行は %f を含むタイムスタンプなのでこの関数を通らない）。
    """

    def test_creates_prefixed_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path))
        d = config.new_run_dir("batch", "20260726_120000")
        assert os.path.isdir(d)
        assert os.path.basename(d) == "batch_20260726_120000"

    def test_same_timestamp_gets_distinct_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path))
        first  = config.new_run_dir("batch", "20260726_120000")
        second = config.new_run_dir("batch", "20260726_120000")
        third  = config.new_run_dir("batch", "20260726_120000")
        assert first != second != third
        assert len({first, second, third}) == 3
        assert all(os.path.isdir(d) for d in (first, second, third))

    def test_prefixes_do_not_collide(self, tmp_path, monkeypatch):
        """バッチと条件探索が同秒に走っても互いを踏まないこと。"""
        monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path))
        b = config.new_run_dir("batch", "20260726_120000")
        s = config.new_run_dir("scenario", "20260726_120000")
        assert os.path.basename(b) == "batch_20260726_120000"
        assert os.path.basename(s) == "scenario_20260726_120000"

    def test_creates_results_dir_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "RESULTS_DIR", str(tmp_path / "results"))
        d = config.new_run_dir("batch", "20260726_120000")
        assert os.path.isdir(d)


# ============================================================
# 初回起動の表示言語（I-127）
# ============================================================
class TestStartupLang:
    """設定ファイルが**まだ無いとき**だけ、既に手元にある環境情報から解く。

    ⚠️ 逆側（設定ファイルが在るときは何があっても中身が勝つ）のほうが重い＝
    利用者が言語メニューで選んだ結果を、インストーラの種や OS の言語で
    上書きしてはいけない。
    """

    def test_existing_config_file_wins_over_seeds(self, tmp_path, monkeypatch):
        """設定ファイルが在れば、種があっても cfg の値をそのまま返す。"""
        path = tmp_path / "radiosim_conf.json"
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(config, "_installer_lang", lambda: "ja")
        monkeypatch.setattr(config, "_os_ui_lang", lambda: "ja")
        assert config.startup_lang({"lang": "en"}, str(path)) == "en"

    def test_existing_config_file_without_lang_key_falls_back_to_default(self, tmp_path):
        path = tmp_path / "radiosim_conf.json"
        path.write_text("{}", encoding="utf-8")
        assert config.startup_lang({}, str(path)) == config.DEFAULT_CONFIG["lang"]

    def test_missing_config_file_resolves_initial(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "_installer_lang", lambda: "ja")
        assert config.startup_lang({"lang": "en"}, str(tmp_path / "none.json")) == "ja"

    # --- 解決の順序 ---------------------------------------------------
    def test_installer_seed_beats_os(self, monkeypatch):
        """利用者が明示的に選んだ種のほうが、OS の言語より強い証拠。"""
        monkeypatch.setattr(config, "_installer_lang", lambda: "ja")
        monkeypatch.setattr(config, "_os_ui_lang", lambda: "en")
        assert config.initial_lang() == "ja"

    def test_os_used_when_no_seed(self, monkeypatch):
        """ポータブル zip（種が無い）は OS の表示言語で決まる。"""
        monkeypatch.setattr(config, "_installer_lang", lambda: None)
        monkeypatch.setattr(config, "_os_ui_lang", lambda: "ja")
        assert config.initial_lang() == "ja"

    def test_falls_back_to_default_when_nothing_known(self, monkeypatch):
        monkeypatch.setattr(config, "_installer_lang", lambda: None)
        monkeypatch.setattr(config, "_os_ui_lang", lambda: None)
        assert config.initial_lang() == config.DEFAULT_CONFIG["lang"]

    def test_resolved_lang_is_always_bundled(self, monkeypatch):
        """解決結果は同梱言語のいずれか（未知のコードを set_lang へ渡さない）。"""
        from core import i18n
        monkeypatch.setattr(config, "_installer_lang", lambda: None)
        assert config.initial_lang() in i18n._BUILTIN_LANGS

    # --- インストーラが置く種 -----------------------------------------
    @pytest.mark.parametrize("written,expected", [
        ("japanese", "ja"),
        ("english", "en"),
        ("  Japanese\r\n", "ja"),   # 前後の空白・改行・大小は無視する
        ("french", None),           # [Languages] を増やして写し忘れたとき
        ("", None),
    ])
    def test_installer_seed_values(self, tmp_path, monkeypatch, written, expected):
        seed = tmp_path / "install_lang.txt"
        seed.write_text(written, encoding="utf-8")
        monkeypatch.setattr(config, "INSTALL_LANG_FILE", str(seed))
        assert config._installer_lang() == expected

    def test_installer_seed_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "INSTALL_LANG_FILE", str(tmp_path / "nope.txt"))
        assert config._installer_lang() is None

    def test_installer_seed_unreadable_is_not_fatal(self, tmp_path, monkeypatch):
        """読めない種（フォルダを掴んだ等）で起動が落ちないこと。"""
        d = tmp_path / "install_lang.txt"
        d.mkdir()
        monkeypatch.setattr(config, "INSTALL_LANG_FILE", str(d))
        assert config._installer_lang() is None

    # --- OS の表示言語 -------------------------------------------------
    @pytest.mark.parametrize("langid,expected", [
        (0x0411, "ja"),   # ja-JP
        (0x0409, "en"),   # en-US
        (0x0809, "en"),   # en-GB
        (0x0407, "en"),   # de-DE ＝同梱していないので英語へ丸める
        (0, None),        # 取得失敗
    ])
    def test_os_ui_lang_mapping(self, monkeypatch, langid, expected):
        monkeypatch.setattr(config.os, "name", "nt")
        monkeypatch.setitem(sys.modules, "ctypes", _fake_ctypes(langid))
        assert config._os_ui_lang() == expected

    def test_os_ui_lang_non_windows(self, monkeypatch):
        monkeypatch.setattr(config.os, "name", "posix")
        assert config._os_ui_lang() is None

    def test_os_ui_lang_survives_ctypes_failure(self, monkeypatch):
        """API が無い／落ちる環境でも None を返すだけ（起動を止めない）。"""
        monkeypatch.setattr(config.os, "name", "nt")
        monkeypatch.setitem(sys.modules, "ctypes", _fake_ctypes(boom=True))
        assert config._os_ui_lang() is None


class _FakeKernel32:
    def __init__(self, langid: int, boom: bool):
        self._langid, self._boom = langid, boom

    def GetUserDefaultUILanguage(self) -> int:
        if self._boom:
            raise OSError("no such entry point")
        return self._langid


def _fake_ctypes(langid: int = 0, boom: bool = False):
    """`import ctypes` を差し替えるためのスタブ（答えだけ固定する）。"""
    mod = types.ModuleType("ctypes")
    mod.windll = types.SimpleNamespace(kernel32=_FakeKernel32(langid, boom))
    return mod


class TestInstallerLangSeedContract:
    """インストーラ（.iss）とアプリ（config.py）の**言語名の写し**が一致すること。

    ウィザードに言語を足したのに `_INSTALLER_LANG_CODES` へ写し忘れると、
    その言語を選んだ人だけが黙って英語で起動する（実害が出るまで気づけない）。
    """

    ISS = ROOT / "installer" / "radiosim.iss"

    def _iss(self) -> str:
        return self.ISS.read_text(encoding="utf-8-sig")

    def test_wizard_languages_match_the_mapping(self):
        names = set(re.findall(r'^Name:\s*"([^"]+)";\s*MessagesFile:',
                               self._iss(), re.MULTILINE))
        assert names == set(config._INSTALLER_LANG_CODES), (
            "installer/radiosim.iss の [Languages] と "
            "config._INSTALLER_LANG_CODES がずれている")

    def test_installer_writes_the_seed_app_reads(self):
        """種のファイル名が両側で同じであること。"""
        name = os.path.basename(config.INSTALL_LANG_FILE)
        iss = self._iss()
        assert "{app}\\" + name in iss
        assert "ActiveLanguage()" in iss

    def test_seed_is_removed_on_uninstall(self):
        """[Code] が作るので uninsdeletefile が効かない＝明示的な削除が要る。"""
        iss = self._iss()
        name = os.path.basename(config.INSTALL_LANG_FILE)
        assert re.search(r"^\[UninstallDelete\]", iss, re.MULTILINE)
        assert re.search(r'^Type:\s*files;\s*Name:\s*"\{app\}\\' + re.escape(name),
                         iss, re.MULTILINE)


# 日本語のグリフを**自前で持っている** UI フォント（GDI のフォントリンクに
# 頼らずに描ける）。ここに無いフォント（Segoe UI・Tahoma・MS Shell Dlg 2 など）を
# 指定する／指定しないと、フォントリンクの無い環境で日本語が全部トーフになる。
_JA_CAPABLE_UI_FONTS = {
    "Yu Gothic UI", "Meiryo UI", "MS UI Gothic",
    "Yu Gothic", "Meiryo", "MS Gothic",
}


class TestInstallerJapaneseDialogFont:
    """日本語ウィザードを**日本語グリフを自前で持つフォント**で描くこと（B-192）。

    Inno の既定は 9pt Segoe UI で、Segoe UI に日本語のグリフは無い。普段それが
    読めているのは GDI のフォントリンク（`FontLink\\SystemLink`）が Segoe UI から
    日本語フォントへ橋渡ししているからにすぎず、**この橋は環境によって無い**。
    実測（2026-09-07）＝開発機の SystemLink は 83 項目、Windows Sandbox は 1 項目
    だけで、同じインストーラのウィザードがそこでは全面トーフになった（日本語
    フォントの実体は在る＝欠けているのは橋だけ）。同梱の Japanese.isl は
    DialogFontName を設定しないので、**.iss の [LangOptions] で明示するしかない。**

    ⚠️ ビルドでは絶対に分からない（ISCC は何も言わず、開発機では橋があるので
    見た目も正常）＝ここで留めないと配布物になるまで気づけない。
    """

    ISS = ROOT / "installer" / "radiosim.iss"

    def _iss(self) -> str:
        return self.ISS.read_text(encoding="utf-8-sig")

    def test_japanese_dialog_font_is_declared_and_can_draw_japanese(self):
        iss = self._iss()
        m = re.search(r"^japanese\.DialogFontName\s*=\s*(.+?)\s*$",
                      iss, re.MULTILINE)
        assert m, (
            "installer/radiosim.iss の [LangOptions] に "
            "japanese.DialogFontName が無い＝Inno の既定（Segoe UI）で日本語を"
            "描くことになり、フォントリンクの無い環境でトーフになる（B-192）")
        assert m.group(1) in _JA_CAPABLE_UI_FONTS, (
            f"japanese.DialogFontName={m.group(1)} は日本語グリフを自前で持つ"
            f"フォントではない＝フォントリンク頼みのままになる。"
            f"使えるのは {sorted(_JA_CAPABLE_UI_FONTS)}")


# Inno Setup が呼び出すイベント関数の名前（6.x / 7.x 共通）。ここに無い名前の
# ルーチンは、自分で呼ばない限り**誰にも呼ばれない**（ISCC は警告も出さない）。
_INNO_EVENT_FUNCTIONS = {
    # セットアップ側
    "InitializeSetup", "DeinitializeSetup", "InitializeWizard",
    "CurStepChanged", "CurPageChanged", "CurInstallProgressChanged",
    "NextButtonClick", "BackButtonClick", "CancelButtonClick",
    "ShouldSkipPage", "CheckPassword", "CheckSerial", "NeedRestart",
    "UpdateReadyMemo", "RegisterPreviousData", "PrepareToInstall",
    "GetCustomSetupExitCode",
    # アンインストーラ側
    "InitializeUninstall", "DeinitializeUninstall",
    "InitializeUninstallProgressForm", "CurUninstallStepChanged",
    "UninstallNeedRestart",
}


class TestInstallerCodeIsReachable:
    """`.iss` の [Code] に書いたルーチンが本当に呼ばれること（B-186）。

    Inno のイベント関数は**名前の一致だけ**で結び付く。綴りを 1 文字違えても
    ISCC は黙ってコンパイルを通し、そのルーチンは一度も実行されない。
    I-137（アンインストール時の削除確認）を `UninstallStepChanged` という
    存在しない名前で書いてしまい、実機のアンインストールでダイアログが
    出ないまま出荷しかけた＝**テストが無ければ実機でしか気づけない**。
    """

    ISS = ROOT / "installer" / "radiosim.iss"

    def test_iss_is_utf8_with_bom(self):
        """ISCC は BOM が無い `.iss` を**システムの ANSI コードページ**で読む。

        開発機・実機とも日本語 Windows（CP932）なので、BOM 無しの UTF-8 だと
        [CustomMessages] の日本語がそのまま文字化けして画面に出る。ISCC は
        エラーにしないので、**BOM の有無はビルドでは絶対に分からない**。
        """
        head = self.ISS.read_bytes()[:3]
        assert head == b"\xef\xbb\xbf", (
            "installer/radiosim.iss に UTF-8 BOM が無い＝日本語メッセージが "
            "CP932 として読まれて文字化けする")

    def test_every_uncalled_routine_is_a_real_event_name(self):
        iss = self.ISS.read_text(encoding="utf-8-sig")
        code = iss.split("[Code]", 1)[1] if "[Code]" in iss else ""
        defs = re.findall(r"^\s*(?:procedure|function)\s+(\w+)", code, re.MULTILINE)
        assert defs, "[Code] にルーチンが 1 つも見つからない（節の切り出しが壊れた？）"
        for name in defs:
            if name in _INNO_EVENT_FUNCTIONS:
                continue
            # 自分の定義行を除いて、どこかから呼ばれていれば助っ人関数として正当
            calls = len(re.findall(r"\b" + re.escape(name) + r"\b", iss))
            assert calls > 1, (
                f"[Code] の {name} は Inno のイベント名でもなく、"
                "どこからも呼ばれていない＝黙って死んでいる")

    #: I-139 の選択肢（[CustomMessages] のキー名）と、それぞれが消す実体の目印。
    #: 目印は core/config.py の保存先解決と 1 対 1 に対応する＝どちらかを動かしたら
    #: 対がずれる（インストーラだけ古い場所を消しに行く事故を止める）。
    _UNINSTALL_CHOICES = {
        "UninstDataSettings": "{userappdata}\\RadioSim\\radiosim_conf.json",
        "UninstDataCache":    "{localappdata}\\RadioSim",
        "UninstDataResults":  "{userdocs}\\RadioSim",
        "UninstDataLang":     "{userappdata}\\RadioSim\\lang",
    }

    @staticmethod
    def _flatten_path_expr(line: str) -> str:
        """[Code] のパス式を素の文字列へ潰す（`ConfigBase + '\\lang'` → 実体）。"""
        line = line.replace("ConfigBase", "ExpandConstant('{userappdata}\\RadioSim')")
        # `ExpandConstant('...')` を中身へ、`' + '` の連結を消す
        line = re.sub(r"ExpandConstant\('([^']*)'\)", r"'\1'", line)
        return line.replace("' + '", "")

    def test_uninstall_confirmation_is_wired(self):
        """I-137→I-139 の削除ダイアログが、実際に走る経路に載っていること。"""
        iss = self.ISS.read_text(encoding="utf-8-sig")
        assert "procedure CurUninstallStepChanged" in iss
        body = iss.split("procedure CurUninstallStepChanged", 1)[1]
        assert "usPostUninstall" in body
        # ダイアログを建てる側と、選択を実行する側の両方が呼ばれていること
        assert "AskRemovableData" in body
        assert "DeleteSelectedData" in body
        # サイレントアンインストールでモーダルを出して固まらせない（明示的に抜ける）
        assert "UninstallSilent" in body

    def test_every_choice_is_offered_and_translated(self):
        """4 つの選択肢が、日英そろって定義され、[Code] から参照されていること。"""
        iss = self.ISS.read_text(encoding="utf-8-sig")
        code = iss.split("[Code]", 1)[1]
        for key in self._UNINSTALL_CHOICES:
            assert re.search(r"^japanese\." + key + "=", iss, re.MULTILINE), key
            assert re.search(r"^english\." + key + "=", iss, re.MULTILINE), key
            assert f"'{key}'" in code, f"{key} が [Code] のどこからも積まれていない"

    def test_each_choice_targets_the_path_config_py_uses(self):
        """選択肢の削除対象が、アプリが実際に書く場所と一致していること。"""
        code = self.ISS.read_text(encoding="utf-8-sig").split("[Code]", 1)[1]
        for key, marker in self._UNINSTALL_CHOICES.items():
            line = next((ln for ln in code.splitlines() if f"'{key}'" in ln), None)
            assert line is not None, key
            flat = self._flatten_path_expr(line)
            assert marker in flat, f"{key} の削除対象が {marker} ではない: {line!r}"

    def test_choice_count_fits_the_checkbox_array(self):
        """選択肢の数が、ダイアログのチェックボックス配列の上限を超えないこと。

        `Boxes: array[0..N] of TNewCheckBox` は固定長。選択肢だけ増やしても
        ISCC は通り、**実行時に添字が飛ぶ**（実機のアンインストールでしか出ない）。
        """
        code = self.ISS.read_text(encoding="utf-8-sig").split("[Code]", 1)[1]
        choices = len(re.findall(r"^\s*AddRemovableData\(", code, re.MULTILINE))
        assert choices == len(self._UNINSTALL_CHOICES)
        m = re.search(r"Boxes:\s*array\[0\.\.(\d+)\]\s*of\s*TNewCheckBox", code)
        assert m, "チェックボックス配列の宣言が見つからない"
        assert choices <= int(m.group(1)) + 1, (
            f"選択肢 {choices} 件に対し Boxes の上限が {int(m.group(1)) + 1} 件しかない")

    def test_settings_and_lang_are_not_deleted_via_their_parent(self):
        """設定と追加言語は同じ親（%APPDATA%\\RadioSim）にいる＝親ごと消さないこと。

        親を `DelTree` すると、「設定だけ消す」を選んだ人の追加言語ファイルまで
        巻き添えで消える（逆も同じ）。
        """
        code = self.ISS.read_text(encoding="utf-8-sig").split("[Code]", 1)[1]
        for line in code.splitlines():
            if "DelTree" in line:
                assert "userappdata" not in line, (
                    f"%APPDATA%\\RadioSim を丸ごと消している: {line!r}")
        # 空になった親の後片付けは RemoveDir（空でなければ何もしない）で行う
        assert re.search(r"RemoveDir\(ExpandConstant\('\{userappdata\}\\RadioSim'\)\)",
                         code)

    def test_elevated_uninstall_deletes_nothing(self):
        """管理者へ昇格したアンインストールでは 1 件も消さないこと（B-188）。

        プロファイル系の定数（userappdata / localappdata / userdocs）は
        **アンインストーラを実行している側**を指す。標準ユーザーが管理者の資格情報を
        入れて消すと、見ているのはアプリを使っていた人ではなく管理者のプロファイル＝
        候補が 1 件も出ない（「もう残っていない」と読める）か、その管理者自身の
        無関係なデータを消す。⇒ 昇格時は場所の案内だけ出して手を出さない。

        ⚠️ この分岐は **CollectRemovableData / AskRemovableData より前**でなければ
        意味が無い（後ろに置くと、間違ったプロファイルを走査した結果を見せてしまう）。

        🔴 **判定は `IsAdmin` であって `IsAdminInstallMode` ではない**（Codex round81）
        ＝後者は「インストールが全ユーザー向けモードだったか」であって実行中の権限では
        ない。取り違えると、ユーザー向けに入れたアンインストーラを「管理者として実行」
        したときに条件が偽のまま素通りし、**防ごうとしている誤削除がそのまま起きる**。
        ⇒ ここは名前を**縛る**（初版はこの取り違えを仕様として固定していた）。
        """
        code = self.ISS.read_text(encoding="utf-8-sig").split("[Code]", 1)[1]
        body = code.split("procedure CurUninstallStepChanged", 1)[1]
        m = re.search(r"\bif IsAdmin then\b", body)
        assert m, "昇格の分岐が `if IsAdmin then` になっていない"
        guard = m.start()
        assert not re.search(r"\bif IsAdminInstallMode then\b", body), (
            "IsAdminInstallMode は『全ユーザー向けモードだったか』であって実行中の権限ではない"
        )
        for later in ("CollectRemovableData", "AskRemovableData", "DeleteSelectedData"):
            pos = body.find(later)
            assert pos > guard, f"{later} が昇格の分岐より前にある"
        # 分岐の中で Exit している＝素通りして削除へ進まない。
        assert re.search(r"\bif IsAdmin then\b.*?Exit;", body, re.S), \
            "昇格の分岐が Exit で抜けていない"

    def test_failed_deletions_are_reported(self):
        """消せなかったものを黙って成功にしないこと（B-189）。

        Inno の削除 API は例外を投げず**戻り値で失敗を返す**ので、見なければ失敗が
        消える。⚠️ 戻り値ではなく実体（DirExists / FileExists）を見る＝DelTree は
        「一部だけ消せた」場合も True を返し得る。
        """
        code = self.ISS.read_text(encoding="utf-8-sig").split("[Code]", 1)[1]
        body = code.split("procedure DeleteSelectedData", 1)[1].split("\nprocedure ", 1)[0]
        assert re.search(r"if DirExists\(Path\) or FileExists\(Path\) then", body), \
            "削除後に実体が残っていないか確かめていない"
        assert "UninstDataFailed" in body, "消せなかったパスを利用者へ見せていない"

    #: ISCC が section tag として読む見出し（このファイルが実際に使うもの）。
    _ISS_SECTIONS = frozenset({
        "[Setup]", "[Languages]", "[LangOptions]", "[CustomMessages]",
        "[Files]", "[Icons]", "[Tasks]", "[Run]", "[UninstallDelete]",
        "[Code]",
    })

    def test_no_line_starts_with_a_bracket_outside_a_section_tag(self):
        """行頭の `[` は必ずセクション見出しであること。

        🔴 **同じクラスで 2 度落ちている**＝ISCC は**行頭（前の空白は無視）の角括弧を
        セクションタグとして読む**ので、そこに配列リテラルやコメント中の `[Foo]` が
        来ると `Invalid section tag` でコンパイルごと止まる。
          1. `1710b9f`＝コメントの中に `[UninstallDelete]` と書いた。
          2. 2026-09-07＝`FmtMessage(..., [ManualCleanupPaths])` を折り返して
             `[` が行頭に来た（B-188 の実装）。
        ⚠️ **2 度ともフルテストは全緑のままで、実際のビルドでしか出なかった**＝
        pytest には ISCC が無く、`.iss` は「文字列として」しか見ていない。
        ⇒ [[feedback_promote_recurring_checks]] に従い、注意書きではなくここで縛る。
        """
        for n, line in enumerate(self.ISS.read_text(encoding="utf-8-sig").splitlines(), 1):
            if line.lstrip().startswith("["):
                assert line.strip() in self._ISS_SECTIONS, (
                    f"{n} 行目の行頭に `[` がある＝ISCC はセクション見出しとして読む"
                    f"（Invalid section tag でビルドが落ちる）: {line!r}"
                )

    def test_elevated_message_does_not_expand_the_wrong_profile(self):
        """昇格時の案内が、実体のパスへ展開されていないこと（B-188）。

        ExpandConstant で展開すると**管理者のプロファイル**が出る＝案内としてまさに
        間違ったものを見せる。環境変数の書き方のまま出して、読む人が自分のアカウントで
        開けるようにする。
        """
        code = self.ISS.read_text(encoding="utf-8-sig").split("[Code]", 1)[1]
        body = code.split("function ManualCleanupPaths", 1)[1].split("\nprocedure ", 1)[0]
        assert "ExpandConstant" not in body, "昇格時の案内が実体のパスへ展開されている"
        assert "%APPDATA%" in body and "%LOCALAPPDATA%" in body
