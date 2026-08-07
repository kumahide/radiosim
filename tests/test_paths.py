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

import pytest

from core import config
from core import dem
from conftest import ORIGINAL_APP_PATHS, apply_app_path_isolation

#: `pytest.skip()` が投げる例外（`_no_display` の分岐を型で見分けるため）。
_SKIPPED = pytest.skip.Exception

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
        from core import simulation
        src = pathlib.Path(simulation.__file__).read_text(encoding="utf-8")
        assert "config.RESULTS_DIR" in src

    # 実行ごとの dir を切るフローは config.new_run_dir を通す（B-013 で導入）。
    # これ自体が config.RESULTS_DIR 由来なので、どちらの呼び方でも解決器の上にいる。
    _RESOLVER_CALLS = ("config.RESULTS_DIR", "config.new_run_dir")

    def test_batch_output_uses_resolver(self):
        """バッチ＝run_batch の batch_dir は解決器由来。"""
        from report import batch
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
        # 直下＋3 層すべてを見る（2.7 スライス H で層がディレクトリになった）。
        paths = sorted(pathlib.Path(REPO_ROOT).glob("*.py"))
        for layer in ("core", "report", "views"):
            paths += sorted(pathlib.Path(REPO_ROOT, layer).glob("*.py"))
        for path in paths:
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

# ============================================================
# 「緑」は「走った」を意味すること（2.7 スライス F で新設）
# ============================================================
class TestGreenMeansItRan:
    """テストが**走らなかった**ことを、緑で隠さないための 2 枚の網。

    🔴 2026-08-07 に実測した事故＝GUI テストが **112 本中 106 本 skip・終了
    コード 0** で終わり、その実行で変異検証を回してしまい**壊した実装が「緑」**
    と出た。skip は「表示が無い環境」でだけ正当なので、**環境を宣言させて**
    区別する（`RADIOSIM_PYTHON` と同じ流儀）＋ 理由を問わない**割合の網**を置く。
    """

    # -- 網① 宣言された環境だけ skip ---------------------------
    def test_display_failure_is_a_skip_only_when_headless_is_declared(self, monkeypatch):
        """宣言があれば skip（CI）・無ければ fail（開発機）。"""
        import conftest

        monkeypatch.setattr(conftest, "_HEADLESS_DECLARED", True)
        with pytest.raises(BaseException) as declared:   # Skipped/Failed は BaseException
            conftest._no_display("表示なし")
        assert declared.errisinstance(_SKIPPED), (
            "ヘッドレスを宣言した環境で skip 以外になった＝CI が赤くなる"
        )

        monkeypatch.setattr(conftest, "_HEADLESS_DECLARED", False)
        with pytest.raises(BaseException) as undeclared:
            conftest._no_display("表示なし")
        assert not undeclared.errisinstance(_SKIPPED), (
            "表示があるはずの環境で skip へ倒している＝GUI 配線を 1 つも検査せずに"
            "緑になる（2026-08-07 に実測した事故そのもの）。"
        )

    # -- 網② 大半が skip なら赤 --------------------------------
    @staticmethod
    def _finish(collected: int, skipped: int, exitstatus: int = 0) -> int:
        """`pytest_sessionfinish` を偽のセッションで回し、終了コードを返す。"""
        import conftest

        class _Reporter:
            stats = {"skipped": [object()] * skipped}
            def write_sep(self, *a, **k): pass

        class _PM:
            def get_plugin(self, _name): return _Reporter()

        class _Config:
            pluginmanager = _PM()

        class _Session:
            config = _Config()
            testscollected = collected

        session = _Session()
        session.exitstatus = exitstatus
        conftest.pytest_sessionfinish(session, exitstatus)
        return session.exitstatus

    def test_a_run_that_mostly_skipped_is_red(self):
        """事故の実測値（112 本中 106 本 skip）が赤になること。"""
        assert self._finish(collected=112, skipped=106) == 1

    def test_a_normal_run_stays_green(self):
        """正当な skip（実測＝1297 本中 5 本）は素通しすること。"""
        assert self._finish(collected=1297, skipped=5) == 0

    def test_a_partial_run_is_not_judged(self):
        """1 ファイル・`-k` の部分実行は見ない（正当な skip でも割合が跳ねる）。

        ⚠️ **下限（`_SKIP_BUDGET_MIN_TESTS`）が効いていることを見る値を選ぶ**＝
        `test_docs_consistency.py` 単独の実測（39 本中 5 本＝13%）は**割合の側で
        既に下回っている**ので、下限を外しても緑のまま＝この検査が下限について
        何も言わない（変異検証で実際に素通りした）。⇒ **割合を超える小さな実行**
        （10 本中 6 本＝60%）で見る。`-k` で数本に絞れば普通に起こる形。
        """
        assert self._finish(collected=39, skipped=5) == 0     # 割合も下回る
        assert self._finish(collected=10, skipped=6) == 0     # 下限だけが効く

    def test_an_already_red_run_is_left_alone(self):
        """既に赤い実行の終了コードを書き換えないこと。"""
        assert self._finish(collected=112, skipped=106, exitstatus=2) == 2


# ============================================================
# Tk オブジェクトを次のテストへ持ち越さない（I-019）
# ============================================================
class TestTkGarbageDoesNotEscape:
    """`destroy()` した Tk が**循環ゴミとして生き延びる**ことへの網。

    生き延びたゴミを製品のワーカースレッドの GC が拾うと、Tcl をメインスレッド
    外から叩く＝①1 個あたり約 1 秒ワーカーが止まる ②`Tk` 本体が混ざると
    `Tcl_AsyncDelete` でプロセスごと落ちる（I-019 の `Current thread's C stack
    trace`）。⇒ **毎テストの teardown で、メインスレッドで回収する。**

    ⚠️ 検査するのは「conftest がゴミを片付けること」＝**回避策の配線**であって、
    tkinter の実装ではない。①は前提が変わったら教えてくれる観測、②は「なぜ
    メインスレッドなのか」を固定する（外すと回避策が意味を失うため）。
    """

    @staticmethod
    def _make_tk_garbage(root, n_vars: int = 3):
        """GUI テストと同じ形のゴミを作る＝destroy 済みなのに循環で残る一式。"""
        import tkinter as tk

        class _Win:                       # 窓オブジェクトが widget を抱える形
            frame: "tk.Frame"
            vars: "list[tk.StringVar]"

        win = _Win()
        frame = tk.Frame(root)
        win.frame = frame
        win.vars = [tk.StringVar(master=root, value=f"v{i}") for i in range(n_vars)]
        setattr(frame, "win", win)        # ← 循環（tkinter 自身も親子で循環する）
        frame.destroy()

    @staticmethod
    def _live_tk_count() -> int:
        import gc
        import tkinter as tk

        return sum(1 for o in gc.get_objects()
                   if isinstance(o, (tk.Misc, tk.Variable, tk.Image)))

    # -- 前提 ---------------------------------------------------
    def test_destroy_alone_does_not_free_them(self):
        """`destroy()` だけでは消えない（＝回避策が要る理由）。

        CPython 側が直ってここが落ちるようになったら、**回避策を外してよい合図**。
        """
        import gc

        from conftest import make_tk_root

        root = make_tk_root()
        try:
            gc.collect()                                  # 先に場を掃く
            before = self._live_tk_count()
            self._make_tk_garbage(root)
            gc.disable()                                  # 自動 GC を挟ませない
            try:
                assert self._live_tk_count() > before, (
                    "destroy 済みの Tk オブジェクトが即座に消えた＝前提が変わった。"
                    "conftest の _tk_garbage_never_escapes は不要になった可能性がある"
                )
                assert gc.collect() > 0, "循環ゴミとして残っていない"
                assert self._live_tk_count() == before, (
                    "gc.collect() でも消えない＝どこかが実参照を持っている"
                )
            finally:
                gc.enable()
        finally:
            root.destroy()
            gc.collect()

    # -- なぜメインスレッドで回収するのか ------------------------
    # ⚠️ このテストは**わざと**別スレッドで `__del__` を走らせるので、
    #    `RuntimeError: main thread is not in main loop` が unraisable として出る
    #    ＝検査対象そのもの。ここだけ黙らせる（他の場所で出たら本物の欠陥）。
    @pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
    def test_collecting_from_another_thread_stalls_it(self):
        """別スレッドで回収すると Tcl 待ちで止まる（＝ワーカーに残せない）。

        `_tkinter` はメインスレッド外からの呼び出しを 100ms×10 回待ってから
        RuntimeError にする。**1 個で約 1 秒**＝GUI テストが残す 19〜31 個なら
        20〜30 秒で、レポート生成の 30 秒待ちを丸ごと食い潰す（実測で確認済み）。
        """
        import gc
        import threading
        import time

        from conftest import make_tk_root

        root = make_tk_root()               # ルートは生かす＝パニックさせずに測る
        try:
            gc.collect()
            self._make_tk_garbage(root, n_vars=1)
            elapsed = {}

            def _worker():
                t0 = time.perf_counter()
                gc.collect()
                elapsed["dt"] = time.perf_counter() - t0

            thread = threading.Thread(target=_worker, name="FakeReportWorker")
            thread.start()
            thread.join(timeout=30)
            assert "dt" in elapsed, "ワーカーの GC が 30 秒で終わらなかった"
            assert elapsed["dt"] > 0.5, (
                f"別スレッドの GC が {elapsed['dt']:.2f} 秒で終わった＝Tcl 待ちが"
                "無くなった。メインスレッドで回収する理由が消えたかを確認すること"
            )
        finally:
            root.destroy()
            gc.collect()

    # -- 配線 ---------------------------------------------------
    def test_teardown_collects_the_garbage(self):
        """conftest の teardown が実際にゴミを回収すること（本丸）。

        フィクスチャの本体を直に回す＝`gc.collect()` を消す変異でここが赤くなる。
        """
        import gc

        import conftest
        from conftest import make_tk_root

        root = make_tk_root()
        try:
            gc.collect()
            baseline = self._live_tk_count()

            fixture = conftest._tk_garbage_never_escapes._get_wrapped_function()()
            next(fixture)                                 # setup
            self._make_tk_garbage(root)
            gc.disable()                                  # 自動 GC に助けさせない
            try:
                assert self._live_tk_count() > baseline   # ゴミが積まれた
                next(fixture, None)                       # ★ teardown
                assert self._live_tk_count() == baseline, (
                    "teardown を通ってもゴミが残った＝次のテスト（や製品の"
                    "ワーカースレッド）へ Tk オブジェクトが漏れる"
                )
            finally:
                gc.enable()
        finally:
            root.destroy()
            gc.collect()

    def test_the_cleanup_is_autouse(self):
        """思い出す規則にしない＝全テストへ自動で掛かること。"""
        import conftest

        marker = conftest._tk_garbage_never_escapes._fixture_function_marker
        assert marker.autouse, (
            "autouse を外すと『新しい GUI テストを書いた人が忘れる』形に戻る"
        )
