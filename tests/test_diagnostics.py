"""
tests/test_diagnostics.py
==========================
`core/diagnostics.py`（診断パッケージ・3.2 段9）のガード。

★ 芯は 3 つ＝①パス中のユーザー名が伏せ字になること ②既定では成果物が 1 件も
入らないこと（判断点②＝成果物は明示的に選んだ分だけ） ③ZIP 生成が原子的であること
（失敗時に壊れた ZIP や一時ファイルを残さない・[[feedback-atomic-writes]]）。
"""

from __future__ import annotations

import json
import os
import zipfile

import pytest

from core import diagnostics


class TestRedactUsername:
    def test_masks_backslash_users_segment(self):
        path = r"C:\Users\alice\AppData\Roaming\RadioSim\radiosim_conf.json"
        assert diagnostics.redact_username(path) == \
            r"C:\Users\***\AppData\Roaming\RadioSim\radiosim_conf.json"

    def test_masks_forward_slash_users_segment(self):
        path = "/Users/alice/Documents/RadioSim"
        assert diagnostics.redact_username(path) == "/Users/***/Documents/RadioSim"

    def test_path_without_users_segment_is_untouched(self):
        path = r"D:\RadioSim\results"
        assert diagnostics.redact_username(path) == path

    def test_does_not_touch_the_rest_of_the_path(self):
        path = r"C:\Users\bob-2\Desktop\radiosim.log"
        result = diagnostics.redact_username(path)
        assert "bob-2" not in result
        assert result.endswith(r"\Desktop\radiosim.log")
        assert result.startswith(r"C:\Users\***")


class TestRedactedPaths:
    def test_masks_username_in_every_path(self, monkeypatch):
        monkeypatch.setattr(diagnostics.config, "CONFIG_FILE",
                             r"C:\Users\alice\AppData\Roaming\RadioSim\radiosim_conf.json")
        monkeypatch.setattr(diagnostics.config, "RESULTS_DIR",
                             r"C:\Users\alice\Documents\RadioSim")
        monkeypatch.setattr(diagnostics.config, "LOG_FILE",
                             r"C:\Users\alice\AppData\Local\RadioSim\radiosim.log")
        monkeypatch.setattr(diagnostics.config, "PROFILE_LOG_FILE",
                             r"C:\Users\alice\AppData\Local\RadioSim\radiosim_profile.log")
        monkeypatch.setattr(diagnostics.config, "USER_LANG_DIR",
                             r"C:\Users\alice\AppData\Roaming\RadioSim\lang")
        monkeypatch.setattr(diagnostics.dem, "CACHE_DIR",
                             r"C:\Users\alice\AppData\Local\RadioSim\terrain_cache")
        paths = diagnostics.redacted_paths()
        assert set(paths) == {
            "config_file", "results_dir", "log_file",
            "profile_log_file", "cache_dir", "user_lang_dir",
        }
        for value in paths.values():
            assert "alice" not in value
            assert "***" in value


class TestListResultRuns:
    def test_missing_results_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(diagnostics.config, "RESULTS_DIR",
                             str(tmp_path / "nonexistent"))
        assert diagnostics.list_result_runs() == []

    def test_lists_entries_sorted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(diagnostics.config, "RESULTS_DIR", str(tmp_path))
        (tmp_path / "batch_20260101_120000").mkdir()
        (tmp_path / "single_20260102_120000").mkdir()
        assert diagnostics.list_result_runs() == [
            "batch_20260101_120000", "single_20260102_120000",
        ]


@pytest.fixture
def _fake_facts(monkeypatch):
    facts = {
        "version": "3.2a1",
        "config": {"freq": "2400.0"},
        "recent_log": ["line1"],
        "cache_stats": {"count": 1, "size_bytes": 2},
        "environment": {"platform": "win32"},
    }
    monkeypatch.setattr(diagnostics.env_facts, "collect", lambda: dict(facts))
    monkeypatch.setattr(diagnostics, "redacted_paths",
                         lambda: {"results_dir": r"C:\Users\***\Documents\RadioSim"})
    return facts


class TestBuildPackage:
    def test_default_includes_all_fact_items(self, tmp_path, _fake_facts):
        zip_path = str(tmp_path / "diag.zip")
        diagnostics.build_package(zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            data = json.loads(zf.read("diagnostics.json"))
        assert set(data) == set(diagnostics.FACT_ITEMS)

    def test_selected_facts_filters_the_rest_out(self, tmp_path, _fake_facts):
        zip_path = str(tmp_path / "diag.zip")
        diagnostics.build_package(zip_path, selected_facts={"version", "paths"})
        with zipfile.ZipFile(zip_path) as zf:
            data = json.loads(zf.read("diagnostics.json"))
        assert set(data) == {"version", "paths"}
        assert data["version"] == "3.2a1"

    def test_no_results_included_by_default(self, tmp_path, _fake_facts, monkeypatch):
        results_dir = tmp_path / "results"
        (results_dir / "batch_1").mkdir(parents=True)
        (results_dir / "batch_1" / "summary.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        monkeypatch.setattr(diagnostics.config, "RESULTS_DIR", str(results_dir))

        zip_path = str(tmp_path / "diag.zip")
        diagnostics.build_package(zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert names == ["diagnostics.json"]

    def test_selected_results_are_included_under_results_prefix(
            self, tmp_path, _fake_facts, monkeypatch):
        results_dir = tmp_path / "results"
        (results_dir / "batch_1").mkdir(parents=True)
        (results_dir / "batch_1" / "summary.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        monkeypatch.setattr(diagnostics.config, "RESULTS_DIR", str(results_dir))

        zip_path = str(tmp_path / "diag.zip")
        diagnostics.build_package(zip_path, selected_results=["batch_1"])
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert names == {"diagnostics.json",
                          os.path.join("results", "batch_1", "summary.csv").replace(os.sep, "/")}

    def test_failure_leaves_no_partial_zip_or_temp_file(self, tmp_path, _fake_facts, monkeypatch):
        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(diagnostics.zipfile, "ZipFile", _boom)
        zip_path = str(tmp_path / "diag.zip")
        with pytest.raises(OSError):
            diagnostics.build_package(zip_path)
        assert not os.path.exists(zip_path)
        assert os.listdir(tmp_path) == []

    def test_saving_into_the_selected_result_dir_does_not_include_itself(
            self, tmp_path, _fake_facts, monkeypatch):
        """B-183＝保存先が選択済み成果物と同じディレクトリでも、生成中の ZIP
        自身（一時ファイル・最終ファイルとも）を取り込まない。"""
        results_dir = tmp_path / "results"
        run_dir = results_dir / "batch_1"
        run_dir.mkdir(parents=True)
        (run_dir / "summary.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        monkeypatch.setattr(diagnostics.config, "RESULTS_DIR", str(results_dir))

        zip_path = str(run_dir / "diag.zip")
        diagnostics.build_package(zip_path, selected_results=["batch_1"])
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert names == {"diagnostics.json",
                          os.path.join("results", "batch_1", "summary.csv").replace(os.sep, "/")}

    def test_saving_over_the_selected_result_file_does_not_include_itself(
            self, tmp_path, _fake_facts, monkeypatch):
        """B-183 の再発防止（Codex round78）＝単一ファイルとして選択した
        成果物そのものへ上書き保存しても、既存の中身を巻き込まない。"""
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        target = results_dir / "old_diag.zip"
        target.write_bytes(b"stale zip bytes")
        monkeypatch.setattr(diagnostics.config, "RESULTS_DIR", str(results_dir))

        diagnostics.build_package(str(target), selected_results=["old_diag.zip"])
        with zipfile.ZipFile(str(target)) as zf:
            names = zf.namelist()
        assert names == ["diagnostics.json"]

    def test_self_exclusion_is_case_insensitive_on_windows(
            self, tmp_path, _fake_facts, monkeypatch):
        """Codex round78＝保存先パスの大小文字が成果物側と食い違っても自己除外が効くこと。

        `os.path.abspath()` はケースを正規化しないため、素の集合比較だと
        ドライブ文字や大小が違う同一ファイルを別物として扱ってしまう。
        """
        results_dir = tmp_path / "results"
        run_dir = results_dir / "batch_1"
        run_dir.mkdir(parents=True)
        (run_dir / "summary.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        monkeypatch.setattr(diagnostics.config, "RESULTS_DIR", str(results_dir))

        zip_path = str(run_dir / "diag.zip")
        # ディレクトリ名の大小文字だけ違う「別表記の同じパス」を保存先として
        # 渡す（NTFS は大小文字を区別しないため、物理的には同じファイルに書ける）。
        zip_path_other_case = str(results_dir / "BATCH_1" / "diag.zip")
        diagnostics.build_package(zip_path_other_case, selected_results=["batch_1"])
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert names == {"diagnostics.json",
                          os.path.join("results", "batch_1", "summary.csv").replace(os.sep, "/")}

    def test_writes_atomically_final_file_is_never_truncated(self, tmp_path, _fake_facts):
        """`os.replace` を使うので、書き終わるまで `zip_path` に断片が現れない。"""
        zip_path = str(tmp_path / "diag.zip")
        diagnostics.build_package(zip_path)
        # 一時ファイルが残っていないこと（成功時は tmp が置換されて消える）。
        leftovers = [n for n in os.listdir(tmp_path) if n != "diag.zip"]
        assert leftovers == []
