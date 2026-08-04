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
import logging
import os
import pathlib

import config
import dem
from conftest import ORIGINAL_APP_PATHS, apply_app_path_isolation

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⚠️ **`config.CONFIG_FILE` 等は、テスト中は一時ディレクトリを指している**
#    （conftest の隔離＝I-055 ①）。「通常起動でどこを指すか」を見るテストは
#    隔離前に控えてある `ORIGINAL_APP_PATHS` を使う＝**製品の値はこちら**。
#    定数を直に読むと、検証しているつもりで隔離後の値を見ることになる。


# ============================================================
# 解決器そのもの
# ============================================================
class TestResolver:

    def test_base_dir_is_source_dir_when_not_frozen(self):
        """凍結されていない（スクリプト実行）なら基準はソースの置き場所。"""
        assert config.app_base_dir() == REPO_ROOT

    def test_base_dir_is_executable_dir_when_frozen(self, monkeypatch):
        """凍結時（exe）は exe の隣。sys.executable の dir を使う。

        パスは `os.path.join` で組む＝Windows のリテラル（`C:\\Apps\\...`）を
        直書きすると、区切り文字を認識しない CI（Linux）で `dirname` が空を返す。
        """
        base = os.path.join(os.sep, "Apps", "RadioSimPro")
        monkeypatch.setattr(config.sys, "frozen", True, raising=False)
        monkeypatch.setattr(config.sys, "executable", os.path.join(base, "RadioSimPro.exe"))
        assert config.app_base_dir() == base

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
        expected = (ORIGINAL_APP_PATHS["CONFIG_FILE"],
                    ORIGINAL_APP_PATHS["RESULTS_DIR"],
                    ORIGINAL_APP_PATHS["LOG_FILE"])
        monkeypatch.chdir(tmp_path)
        importlib.reload(config)          # ＝別ディレクトリから起動した状態の再現
        try:
            assert (config.CONFIG_FILE, config.RESULTS_DIR, config.LOG_FILE) == expected
        finally:
            importlib.reload(config)
            apply_app_path_isolation()    # reload が実パスへ戻すので隔離を掛け直す

    def test_all_write_targets_are_absolute(self):
        """相対パスが1つでも残っていれば cwd 依存が復活する。"""
        for name in ("CONFIG_FILE", "RESULTS_DIR", "LOG_FILE", "CACHE_DIR"):
            value = ORIGINAL_APP_PATHS[name]
            assert os.path.isabs(value), f"{name} が相対パス: {value}"


# ============================================================
# 互換性＝通常起動では従来と同じ場所
# ============================================================
class TestBackwardCompatibility:
    """基準を固定するだけで移設はしない。通常起動の保存先は従来どおり。"""

    def test_paths_match_legacy_layout(self):
        assert ORIGINAL_APP_PATHS["CONFIG_FILE"] == os.path.join(REPO_ROOT, "radiosim_conf.json")
        assert ORIGINAL_APP_PATHS["RESULTS_DIR"] == os.path.join(REPO_ROOT, "results")
        assert ORIGINAL_APP_PATHS["LOG_FILE"]    == os.path.join(REPO_ROOT, "radiosim.log")
        assert ORIGINAL_APP_PATHS["CACHE_DIR"]   == os.path.join(REPO_ROOT, "terrain_cache")


# ============================================================
# ★クラス点検＝全フローが同じ解決器を通る
# ============================================================
class TestAllFlowsUseResolver:
    """単一 / バッチ / 地図（DEM）とログ・設定が同一基準に載っていること。"""

    def test_dem_cache_uses_resolver(self):
        """地図・DEM（3 フロー共有）。"""
        assert ORIGINAL_APP_PATHS["CACHE_DIR"] == config.app_path("terrain_cache")

    def test_single_run_output_uses_resolver(self):
        """単一＝save_package の保存先は config.RESULTS_DIR 由来。"""
        import simulation
        src = pathlib.Path(simulation.__file__).read_text(encoding="utf-8")
        assert "config.RESULTS_DIR" in src

    # 実行ごとの dir を切るフローは config.new_run_dir を通す（B-013 で導入）。
    # これ自体が config.RESULTS_DIR 由来なので、どちらの呼び方でも解決器の上にいる。
    _RESOLVER_CALLS = ("config.RESULTS_DIR", "config.new_run_dir")

    def test_batch_output_uses_resolver(self):
        """バッチ＝run_batch の batch_dir は解決器由来。"""
        import batch
        src = pathlib.Path(batch.__file__).read_text(encoding="utf-8")
        assert any(c in src for c in self._RESOLVER_CALLS)

    def test_scenario_output_uses_resolver(self):
        """条件探索（4 つ目のフロー）＝save_dir も解決器由来。"""
        import views.scenario
        src = pathlib.Path(views.scenario.__file__).read_text(encoding="utf-8")
        assert any(c in src for c in self._RESOLVER_CALLS)

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


# ============================================================
# テスト実行の隔離＝開発機の実体に触らない（I-055 ①）
# ============================================================
class TestTestRunIsolation:
    """**テストが開発機の設定を読まず、実リポジトリへ書かない**ことのゲート。

    これが緑であることが、以降のすべてのテストの緑を「証拠」にする前提。
    読む側を塞いだ理由（B-034 が長期間生き延びた）と書く側を塞いだ理由
    （8.1MB ログ誤 push の原料）は conftest の該当節に書いてある。

    **変異検証済み（2026-08-05）**＝conftest の隔離を 1 手ずつ外すと、対応する
    ゲートだけが落ちる: 既定引数の差し替えをやめる → 下の 2 本／ログハンドラの
    差し替えをやめる → ログの 1 本／定数を実パスへ戻す → 書き込み先の 1 本。

    ⚠️ **ゲートが「定数」ではなく*実際に使われる出口*を見ているのが要点**＝
    既定引数（`def load_config(path=CONFIG_FILE)`）と、開いている `FileHandler`。
    定数だけを見るゲートにしていたら、**実設定を読み続けたまま緑**になっていた
    （実際、最初の実装がその状態で、この 2 本が赤にして教えた）。
    """

    def _is_inside_repo(self, path: str) -> bool:
        return os.path.normcase(os.path.abspath(path)).startswith(
            os.path.normcase(REPO_ROOT) + os.sep)

    def test_write_targets_are_outside_the_repository(self):
        """4 つの書き込み先が、テスト中はリポジトリの外を指していること。"""
        live = {
            "config.CONFIG_FILE": config.CONFIG_FILE,
            "config.RESULTS_DIR": config.RESULTS_DIR,
            "config.LOG_FILE":    config.LOG_FILE,
            "dem.CACHE_DIR":      dem.CACHE_DIR,
        }
        inside = {n: v for n, v in live.items() if self._is_inside_repo(v)}
        assert not inside, f"テストの書き込み先がリポジトリ内を向いている: {inside}"

    def test_config_readers_do_not_default_to_the_real_file(self):
        """**引数なしの `load_config()` が実設定を読まない**こと。

        🔑 定数の差し替えだけでは足りない＝`def load_config(path=CONFIG_FILE)` は
        **def 時に値を焼き込む**ので、`config.CONFIG_FILE` を後から変えても
        引数なしの呼び出しは古いパスを使い続ける。**窓が直に呼ぶのはこの形**
        （＝G2 で配線を直すまで、ここが実設定への唯一の入口）。
        """
        import inspect
        offenders = []
        for name, func in vars(config).items():
            if not inspect.isfunction(func):
                continue
            for pname, param in inspect.signature(func).parameters.items():
                if isinstance(param.default, str) and self._is_inside_repo(param.default):
                    offenders.append(f"config.{name}({pname}={param.default})")
        assert not offenders, (
            "既定引数に実リポジトリのパスが焼き込まれたままの関数がある"
            "（conftest の隔離が届いていない）: " + ", ".join(offenders)
        )

    def test_loaded_config_is_the_default_regardless_of_the_dev_machine(self):
        """開発機の設定がどうであれ、テストが見る設定は既定値。

        ⚠️ これが破れると「同じコミットが開発機の設定次第で緑にも赤にもなる」
        （B-034 は `coord_format` が `dms` の日にだけ落ちた）。
        """
        assert config.load_config() == config.DEFAULT_CONFIG

    def test_file_logging_goes_outside_the_repository(self):
        """ログの出口（実際に開いているファイル）がリポジトリの外であること。

        定数ではなく **`FileHandler` が開いている実ファイル**を見る＝`config.py` は
        import 時にハンドラを開くので、定数だけ直しても出口は変わらない。
        """
        offenders = [h.baseFilename for h in logging.root.handlers
                     if isinstance(h, logging.FileHandler)
                     and self._is_inside_repo(h.baseFilename)]
        assert not offenders, f"テストのログが実リポジトリへ流れている: {offenders}"
