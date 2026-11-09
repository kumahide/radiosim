"""
tests/test_write_locations.py
=============================
OS 標準の書き込み先（%APPDATA% 等）への移設・ポータブル判定・旧配置からの
移行（3.1・[[project-roadmap]] §3.1 ステージ1）のガード。

tests/test_paths.py が守るのは「cwd 非依存」と「ポータブル配置は従来どおり」
の 2 点。ここで守るのは 3.1 で足した分:
  1. ポータブル判定（`is_portable`）＝スクリプト実行は常に真、配布物（exe・
     embeddable）は `portable.txt` の有無だけで決まる。配布形の 3 値（`runtime_env`）
  2. Known Folder 解決の段階的フォールバック（API失敗 → 環境変数 → 既定）
  3. 書き込み先の基準がポータブル/非ポータブルで正しく分岐する
  4. 旧配置（exe／スクリプトの隣）からの移行＝コピーのみ・旧は残す・
     既に新配置にあるものは上書きしない
"""

import os

from core import config
from core import runtime_env


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

    # --- embeddable Python（I-144・4.0 ステージ8d）＝`sys.frozen` は無く、
    #     実行ファイルの隣に `._pth` がある。ソース実行と読むと Program Files の
    #     隣へ設定を書こうとする（直す前の壊れ方）。
    @staticmethod
    def _embeddable(monkeypatch, tmp_path):
        monkeypatch.setattr(config.sys, "frozen", False, raising=False)
        monkeypatch.setattr(config.sys, "executable", str(tmp_path / "pythonw.exe"))
        xy = f"python{config.sys.version_info.major}{config.sys.version_info.minor}"
        (tmp_path / f"{xy}._pth").write_text("python315.zip\n.\napp\n", encoding="ascii")

    def test_embeddable_without_marker_is_not_portable(self, monkeypatch, tmp_path):
        self._embeddable(monkeypatch, tmp_path)
        assert config.is_portable() is False

    def test_embeddable_with_marker_is_portable(self, monkeypatch, tmp_path):
        self._embeddable(monkeypatch, tmp_path)
        (tmp_path / "portable.txt").write_text("", encoding="utf-8")
        assert config.is_portable() is True

    def test_embeddable_base_dir_is_next_to_the_interpreter(self, monkeypatch, tmp_path):
        """ソースは `app\\` の下にあるが、基準は実行ファイルの隣（`portable.txt` の場所）。"""
        self._embeddable(monkeypatch, tmp_path)
        assert config.app_base_dir() == str(tmp_path)


# ============================================================
# 配布形の 3 値（`core.runtime_env.packaging`）
# ============================================================
class TestPackaging:

    def test_frozen_is_exe(self, monkeypatch, tmp_path):
        monkeypatch.setattr(runtime_env.sys, "frozen", True, raising=False)
        monkeypatch.setattr(runtime_env.sys, "executable", str(tmp_path / "RadioSimPro.exe"))
        assert runtime_env.packaging() == runtime_env.PACKAGING_EXE
        assert runtime_env.is_packaged() is True

    def test_pth_next_to_the_interpreter_is_embeddable(self, monkeypatch, tmp_path):
        """`<実行ファイルの名>._pth` でも見つける（CPython の探す 2 つの名の片方）。"""
        monkeypatch.setattr(runtime_env.sys, "frozen", False, raising=False)
        monkeypatch.setattr(runtime_env.sys, "executable", str(tmp_path / "python.exe"))
        (tmp_path / "python._pth").write_text(".\n", encoding="ascii")
        assert runtime_env.packaging() == runtime_env.PACKAGING_EMBEDDABLE
        assert runtime_env.is_packaged() is True

    def test_plain_interpreter_is_source(self, monkeypatch, tmp_path):
        """`._pth` の無い Python（素のインストール・venv）はソース実行。"""
        monkeypatch.setattr(runtime_env.sys, "frozen", False, raising=False)
        monkeypatch.setattr(runtime_env.sys, "executable", str(tmp_path / "python.exe"))
        (tmp_path / "pyvenv.cfg").write_text("home = x\n", encoding="ascii")
        assert runtime_env.packaging() == runtime_env.PACKAGING_SOURCE
        assert runtime_env.is_packaged() is False

    def test_embeddable_never_warns_about_the_declared_interpreter(self, monkeypatch, tmp_path):
        """開発機で embeddable の配布物を試すときも、宣言の食い違いを言わない（exe と同じ）。"""
        monkeypatch.setattr(runtime_env.sys, "frozen", False, raising=False)
        monkeypatch.setattr(runtime_env.sys, "executable", str(tmp_path / "pythonw.exe"))
        xy = f"python{runtime_env.sys.version_info.major}{runtime_env.sys.version_info.minor}"
        (tmp_path / f"{xy}._pth").write_text(".\n", encoding="ascii")
        monkeypatch.setenv(runtime_env.DECLARED_ENV, "Z:/missing/python.exe")
        assert runtime_env.interpreter_mismatch() is None


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

    def test_config_base_is_appdata_radiosim_link_when_not_portable(self, monkeypatch):
        """4.0 ステージ8e: 設定は製品ごと＝`%APPDATA%\\RadioSim\\Link`。"""
        monkeypatch.setattr(config, "is_portable", lambda: False)
        monkeypatch.setattr(config, "_appdata_dir", lambda: r"C:\AppData\Roaming")
        assert config._config_base_dir() == os.path.join(
            r"C:\AppData\Roaming", "RadioSim", "Link")

    def test_local_base_is_app_base_dir_when_portable(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: True)
        assert config.local_base_dir() == config.app_base_dir()

    def test_local_base_is_local_appdata_radiosim_link_when_not_portable(self, monkeypatch):
        """ログと Edge の専用プロファイルは製品ごと＝`%LOCALAPPDATA%\\RadioSim\\Link`。"""
        monkeypatch.setattr(config, "is_portable", lambda: False)
        monkeypatch.setattr(config, "_local_appdata_dir", lambda: r"C:\AppData\Local")
        assert config.local_base_dir() == os.path.join(
            r"C:\AppData\Local", "RadioSim", "Link")

    def test_shared_cache_base_is_app_base_dir_when_portable(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: True)
        assert config.shared_cache_base_dir() == config.app_base_dir()

    def test_shared_cache_base_is_local_appdata_radiosim_when_not_portable(self, monkeypatch):
        """地形と地図のキャッシュは Pro 3.9 と共有＝製品名を足さない（ステージ8e）。"""
        monkeypatch.setattr(config, "is_portable", lambda: False)
        monkeypatch.setattr(config, "_local_appdata_dir", lambda: r"C:\AppData\Local")
        assert config.shared_cache_base_dir() == os.path.join(r"C:\AppData\Local", "RadioSim")

    def test_results_dir_is_app_path_results_when_portable(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: True)
        assert config._results_dir() == config.app_path("results")

    def test_results_dir_is_documents_radiosim_link_when_not_portable(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: False)
        monkeypatch.setattr(config, "_documents_dir", lambda: r"C:\Users\x\Documents")
        assert config._results_dir() == os.path.join(
            r"C:\Users\x\Documents", "RadioSim", "Link")

    def test_config_log_and_cache_bases_differ_when_not_portable(self, monkeypatch):
        """設定＝ローミング、ログ＝ローカルの製品の下、キャッシュ＝ローカルの共有。
        3 つとも別のフォルダを指し、ログの置き場は共有キャッシュの置き場の下にある
        （Link をアンインストールしてログを消しても 3.9 と共有の地形は残せる）。"""
        monkeypatch.setattr(config, "is_portable", lambda: False)
        monkeypatch.setattr(config, "_appdata_dir", lambda: r"C:\AppData\Roaming")
        monkeypatch.setattr(config, "_local_appdata_dir", lambda: r"C:\AppData\Local")
        bases = {config._config_base_dir(), config.local_base_dir(),
                 config.shared_cache_base_dir()}
        assert len(bases) == 3
        assert os.path.dirname(config.local_base_dir()) == config.shared_cache_base_dir()

    def test_user_lang_dir_is_app_path_lang_when_portable(self, monkeypatch):
        monkeypatch.setattr(config, "is_portable", lambda: True)
        assert config._user_lang_dir() == config.app_path("lang")

    def test_user_lang_dir_is_appdata_radiosim_link_lang_when_not_portable(self, monkeypatch):
        """I-130: 非ポータブルでは書込禁止のことがある `LANG_DIR`（exe隣）ではなく、
        設定と同じ `%APPDATA%\\RadioSim\\Link` 配下（利用者が書けることが保証された場所）
        を使うこと。"""
        monkeypatch.setattr(config, "is_portable", lambda: False)
        monkeypatch.setattr(config, "_appdata_dir", lambda: r"C:\AppData\Roaming")
        assert config._user_lang_dir() == os.path.join(
            r"C:\AppData\Roaming", "RadioSim", "Link", "lang")


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

    def test_legacy_leftover_notice_is_gone(self):
        """旧版の残りを知らせる口は Link 1.0 で消した（I-256 の③＝経営者の選択）。"""
        assert not hasattr(config, "legacy_leftovers")
