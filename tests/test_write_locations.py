"""
tests/test_write_locations.py
=============================
OS 標準の書き込み先（%APPDATA% 等）への移設・ポータブル判定・旧配置からの
移行（3.1・[[project_roadmap]] §3.1 段1）のガード。

tests/test_paths.py が守るのは「cwd 非依存」と「ポータブル配置は従来どおり」
の 2 点。ここで守るのは 3.1 で足した分:
  1. ポータブル判定（`is_portable`）＝スクリプト実行は常に真、凍結時は
     `portable.txt` の有無だけで決まる
  2. Known Folder 解決の段階的フォールバック（API失敗 → 環境変数 → 既定）
  3. 書き込み先の基準がポータブル/非ポータブルで正しく分岐する
  4. 旧配置（exe／スクリプトの隣）からの移行＝コピーのみ・旧は残す・
     既に新配置にあるものは上書きしない
"""

import os

from core import config


# ============================================================
# ポータブル判定
# ============================================================
class TestPortableDetection:

    def test_script_execution_is_always_portable(self, monkeypatch):
        monkeypatch.setattr(config.sys, "frozen", False, raising=False)
        assert config.is_portable() is True

    def test_frozen_without_marker_is_not_portable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config.sys, "frozen", True, raising=False)
        monkeypatch.setattr(config.sys, "executable", str(tmp_path / "RadioSimPro.exe"))
        assert config.is_portable() is False

    def test_frozen_with_marker_is_portable(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config.sys, "frozen", True, raising=False)
        monkeypatch.setattr(config.sys, "executable", str(tmp_path / "RadioSimPro.exe"))
        (tmp_path / "portable.txt").write_text("", encoding="utf-8")
        assert config.is_portable() is True

    def test_marker_must_be_a_file_not_a_directory(self, monkeypatch, tmp_path):
        """同名のディレクトリでは真にならない（利用者の誤操作を弾く）。"""
        monkeypatch.setattr(config.sys, "frozen", True, raising=False)
        monkeypatch.setattr(config.sys, "executable", str(tmp_path / "RadioSimPro.exe"))
        (tmp_path / "portable.txt").mkdir()
        assert config.is_portable() is False


# ============================================================
# Known Folder 解決の段階的フォールバック
# ============================================================
class TestKnownFolderFallback:

    def test_appdata_prefers_known_folder_over_env(self, monkeypatch):
        monkeypatch.setattr(config, "_known_folder_path", lambda guid: r"D:\KF\Roaming")
        monkeypatch.setenv("APPDATA", r"C:\Env\Roaming")
        assert config._appdata_dir() == r"D:\KF\Roaming"

    def test_appdata_falls_back_to_env_when_known_folder_fails(self, monkeypatch):
        monkeypatch.setattr(config, "_known_folder_path", lambda guid: None)
        monkeypatch.setenv("APPDATA", r"C:\Env\Roaming")
        assert config._appdata_dir() == r"C:\Env\Roaming"

    def test_appdata_falls_back_to_app_base_dir_when_nothing_else_works(self, monkeypatch):
        monkeypatch.setattr(config, "_known_folder_path", lambda guid: None)
        monkeypatch.delenv("APPDATA", raising=False)
        assert config._appdata_dir() == config.app_base_dir()

    def test_local_appdata_falls_back_to_env_var(self, monkeypatch):
        monkeypatch.setattr(config, "_known_folder_path", lambda guid: None)
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Env\Local")
        assert config._local_appdata_dir() == r"C:\Env\Local"

    def test_documents_falls_back_to_expanduser(self, monkeypatch):
        monkeypatch.setattr(config, "_known_folder_path", lambda guid: None)
        expected = os.path.join(os.path.expanduser("~"), "Documents")
        assert config._documents_dir() == expected

    def test_known_folder_path_is_none_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(config.os, "name", "posix")
        assert config._known_folder_path(config._FOLDERID_DOCUMENTS) is None


# ============================================================
# 書き込み先の基準＝ポータブル/非ポータブルで分岐する唯一の場所
# ============================================================
class TestWriteBaseSwitchesOnPortability:

    def test_config_base_is_app_base_dir_when_portable(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: True)
        assert config._config_base_dir() == config.app_base_dir()

    def test_config_base_is_appdata_radiosim_when_not_portable(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: False)
        monkeypatch.setattr(config, "_appdata_dir", lambda: r"C:\AppData\Roaming")
        assert config._config_base_dir() == os.path.join(r"C:\AppData\Roaming", "RadioSim")

    def test_cache_log_base_is_app_base_dir_when_portable(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: True)
        assert config.cache_log_base_dir() == config.app_base_dir()

    def test_cache_log_base_is_local_appdata_radiosim_when_not_portable(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: False)
        monkeypatch.setattr(config, "_local_appdata_dir", lambda: r"C:\AppData\Local")
        assert config.cache_log_base_dir() == os.path.join(r"C:\AppData\Local", "RadioSim")

    def test_results_dir_is_app_path_results_when_portable(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: True)
        assert config._results_dir() == config.app_path("results")

    def test_results_dir_is_documents_radiosim_when_not_portable(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: False)
        monkeypatch.setattr(config, "_documents_dir", lambda: r"C:\Users\x\Documents")
        assert config._results_dir() == os.path.join(r"C:\Users\x\Documents", "RadioSim")

    def test_config_and_cache_log_bases_differ_when_not_portable(self, monkeypatch):
        """設定＝ローミング、キャッシュ・ログ＝ローカル。別のフォルダを指すこと。"""
        monkeypatch.setattr(config, "is_portable", lambda: False)
        monkeypatch.setattr(config, "_appdata_dir", lambda: r"C:\AppData\Roaming")
        monkeypatch.setattr(config, "_local_appdata_dir", lambda: r"C:\AppData\Local")
        assert config._config_base_dir() != config.cache_log_base_dir()


# ============================================================
# 旧配置からの移行＝コピーのみ・旧は残す・新は上書きしない
# ============================================================
class TestLegacyMigration:

    def test_portable_never_migrates(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: True)
        called = []
        monkeypatch.setattr(config, "_migrate_config_file", lambda: called.append("config"))
        monkeypatch.setattr(config, "_migrate_results_dir", lambda: called.append("results"))
        config._migrate_legacy_data()
        assert called == []

    def test_config_file_is_copied_when_new_is_missing(self, monkeypatch, tmp_path):
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        old_file = old_dir / "radiosim_conf.json"
        old_file.write_text('{"lang": "ja"}', encoding="utf-8")
        new_file = tmp_path / "new" / "radiosim_conf.json"

        monkeypatch.setattr(config, "app_base_dir", lambda: str(old_dir))
        monkeypatch.setattr(config, "CONFIG_FILE", str(new_file))
        config._migrate_config_file()

        assert new_file.read_text(encoding="utf-8") == '{"lang": "ja"}'
        assert old_file.exists(), "旧ファイルを消してはいけない（削除は次版）"

    def test_config_file_migration_does_not_overwrite_existing_new_file(
        self, monkeypatch, tmp_path
    ):
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        (old_dir / "radiosim_conf.json").write_text('{"lang": "old"}', encoding="utf-8")
        new_dir = tmp_path / "new"
        new_dir.mkdir()
        new_file = new_dir / "radiosim_conf.json"
        new_file.write_text('{"lang": "new"}', encoding="utf-8")

        monkeypatch.setattr(config, "app_base_dir", lambda: str(old_dir))
        monkeypatch.setattr(config, "CONFIG_FILE", str(new_file))
        config._migrate_config_file()

        assert new_file.read_text(encoding="utf-8") == '{"lang": "new"}', (
            "既に新配置にある設定を旧配置で上書きしてはいけない"
        )

    def test_config_file_migration_is_a_no_op_without_a_legacy_file(
        self, monkeypatch, tmp_path
    ):
        old_dir = tmp_path / "old"
        old_dir.mkdir()
        new_file = tmp_path / "new" / "radiosim_conf.json"

        monkeypatch.setattr(config, "app_base_dir", lambda: str(old_dir))
        monkeypatch.setattr(config, "CONFIG_FILE", str(new_file))
        config._migrate_config_file()

        assert not new_file.exists()

    def test_results_dir_is_merged_without_overwriting_existing_entries(
        self, monkeypatch, tmp_path
    ):
        old_dir = tmp_path / "old"
        old_results = old_dir / "results"
        old_results.mkdir(parents=True)
        (old_results / "run_a").mkdir()
        (old_results / "run_a" / "report.txt").write_text("old-a", encoding="utf-8")
        (old_results / "run_b").mkdir()
        (old_results / "run_b" / "report.txt").write_text("old-b", encoding="utf-8")

        new_results = tmp_path / "new" / "results"
        new_results.mkdir(parents=True)
        (new_results / "run_b").mkdir()             # 既に同名あり＝上書きしない
        (new_results / "run_b" / "marker.txt").write_text("kept", encoding="utf-8")

        monkeypatch.setattr(config, "app_base_dir", lambda: str(old_dir))
        monkeypatch.setattr(config, "RESULTS_DIR", str(new_results))
        config._migrate_results_dir()

        assert (new_results / "run_a" / "report.txt").read_text(encoding="utf-8") == "old-a"
        assert (new_results / "run_b" / "marker.txt").exists(), (
            "同名の run_b が既にあるのに旧の中身で置き換えた（上書き）"
        )
        assert not (new_results / "run_b" / "report.txt").exists()
        assert (old_results / "run_a" / "report.txt").exists(), "旧を消してはいけない"

    def test_results_dir_migration_is_a_no_op_without_a_legacy_dir(
        self, monkeypatch, tmp_path
    ):
        old_dir = tmp_path / "old"          # results サブフォルダを作らない
        old_dir.mkdir()
        new_results = tmp_path / "new" / "results"

        monkeypatch.setattr(config, "app_base_dir", lambda: str(old_dir))
        monkeypatch.setattr(config, "RESULTS_DIR", str(new_results))
        config._migrate_results_dir()

        assert not new_results.exists()

    def test_cache_is_not_migrated(self):
        """キャッシュは再生成可能＝移行対象に含めない（roadmap の明示判断）。"""
        assert not hasattr(config, "_migrate_cache")
