"""
tests/test_paths.py
===================
書き込み先パスの基準が **カレントディレクトリに依存しない** ことのガード（B-014）。

アプリが書き込むもの（設定・結果・ログ・DEM キャッシュ）は、裸の相対パスだと
起動時の cwd 配下へ黙って移る。エラーも警告も出ず「設定が既定値に戻る」
「過去の結果が見つからない」として現れるため、**振る舞いをテストで固定する**
（コメントの禁止事項では強制されない＝[[feedback-radiosim-rules]]）。

このテストが守るのは 2 つ:
  1. cwd を変えても保存先が動かないこと（本丸）
  2. **通常起動では従来と完全に同じパスを指すこと**（＝互換性を壊していない。
     既存の設定・キャッシュ・結果が引き続き読める）

OS 標準の場所（%APPDATA% 等）への移設は将来版（3.0）の仕事で、ここでは
基準を固定するだけ＝挙動は不変。
"""

import importlib
import os
import pathlib

import config
import dem

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============================================================
# 解決器そのもの
# ============================================================
class TestResolver:

    def test_base_dir_is_source_dir_when_not_frozen(self):
        """凍結されていない（スクリプト実行）なら基準はソースの置き場所。"""
        assert config.app_base_dir() == REPO_ROOT

    def test_base_dir_is_executable_dir_when_frozen(self, monkeypatch):
        """凍結時（exe）は exe の隣。sys.executable の dir を使う。"""
        monkeypatch.setattr(config.sys, "frozen", True, raising=False)
        monkeypatch.setattr(config.sys, "executable", r"C:\Apps\RadioSimPro\RadioSimPro.exe")
        assert config.app_base_dir() == r"C:\Apps\RadioSimPro"

    def test_app_path_joins_under_base(self):
        assert config.app_path("a", "b.txt") == os.path.join(config.app_base_dir(), "a", "b.txt")

    def test_app_path_is_absolute(self):
        assert os.path.isabs(config.app_path("x"))


# ============================================================
# 本丸＝cwd 非依存
# ============================================================
class TestCwdIndependence:

    def test_app_path_ignores_cwd(self, monkeypatch, tmp_path):
        """cwd を変えても解決結果は変わらない。"""
        before = config.app_path("results")
        monkeypatch.chdir(tmp_path)
        assert config.app_path("results") == before

    def test_constants_survive_reimport_from_other_cwd(self, monkeypatch, tmp_path):
        """別の cwd で import し直しても定数は同じ場所を指す。

        定数は import 時に確定するので、cwd を変えた状態での再 import が
        「別ディレクトリから起動した」ことの再現になる。
        """
        before = (config.CONFIG_FILE, config.RESULTS_DIR, config.LOG_FILE)
        monkeypatch.chdir(tmp_path)
        importlib.reload(config)
        try:
            assert (config.CONFIG_FILE, config.RESULTS_DIR, config.LOG_FILE) == before
        finally:
            importlib.reload(config)

    def test_all_write_targets_are_absolute(self):
        """相対パスが1つでも残っていれば cwd 依存が復活する。"""
        for name in ("CONFIG_FILE", "RESULTS_DIR", "LOG_FILE"):
            value = getattr(config, name)
            assert os.path.isabs(value), f"config.{name} が相対パス: {value}"
        assert os.path.isabs(dem.CACHE_DIR), f"dem.CACHE_DIR が相対パス: {dem.CACHE_DIR}"


# ============================================================
# 互換性＝通常起動では従来と同じ場所
# ============================================================
class TestBackwardCompatibility:
    """基準を固定するだけで移設はしない。通常起動の保存先は従来どおり。"""

    def test_paths_match_legacy_layout(self):
        assert config.CONFIG_FILE == os.path.join(REPO_ROOT, "radiosim_conf.json")
        assert config.RESULTS_DIR == os.path.join(REPO_ROOT, "results")
        assert config.LOG_FILE    == os.path.join(REPO_ROOT, "radiosim.log")
        assert dem.CACHE_DIR      == os.path.join(REPO_ROOT, "terrain_cache")


# ============================================================
# ★クラス点検＝全フローが同じ解決器を通る
# ============================================================
class TestAllFlowsUseResolver:
    """単一 / バッチ / 地図（DEM）とログ・設定が同一基準に載っていること。"""

    def test_dem_cache_uses_resolver(self):
        """地図・DEM（3 フロー共有）。"""
        assert dem.CACHE_DIR == config.app_path("terrain_cache")

    def test_single_run_output_uses_resolver(self):
        """単一＝save_package の保存先は config.RESULTS_DIR 由来。"""
        import simulation
        src = pathlib.Path(simulation.__file__).read_text(encoding="utf-8")
        assert "config.RESULTS_DIR" in src

    def test_batch_output_uses_resolver(self):
        """バッチ＝run_batch の batch_dir は config.RESULTS_DIR 由来。"""
        import batch
        src = pathlib.Path(batch.__file__).read_text(encoding="utf-8")
        assert "config.RESULTS_DIR" in src

    def test_resolver_is_not_reimplemented_elsewhere(self):
        """解決器を二度書かない（重複が残ると片方だけ直す事故になる）。

        exe 位置を基準にする判定は config.py の 1 箇所だけ。main.py はかつて
        同じ式を持っていたが、解決器の呼び出しへ置き換えた。
        """
        offenders = []
        for path in sorted(pathlib.Path(REPO_ROOT).glob("*.py")) + \
                    sorted(pathlib.Path(REPO_ROOT, "views").glob("*.py")):
            if path.name == "config.py":
                continue
            if "sys.executable" in path.read_text(encoding="utf-8"):
                offenders.append(path.name)
        assert offenders == [], f"exe 基準の解決を再実装している: {offenders}"
