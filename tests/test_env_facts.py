"""
tests/test_env_facts.py
========================
`core/env_facts.py`（環境事実の収集層・3.2 段5）のガード。

★ 芯は 2 つ＝①ログ・設定に混じる座標が漏れないこと（3.2 段4 で見つかった穴の
再発防止）②`collect()` が刻印・診断 ZIP・残骸通知が期待する形（5 キー）を返すこと。
"""

from __future__ import annotations

import ctypes

from core import env_facts


class TestRecentLogLines:
    def test_missing_file_returns_empty(self, tmp_path):
        path = str(tmp_path / "nonexistent.log")
        assert env_facts.recent_log_lines(path=path) == []

    def test_reads_tail_lines(self, tmp_path):
        path = tmp_path / "radiosim.log"
        path.write_text("\n".join(f"line{i}" for i in range(5)) + "\n", encoding="utf-8")
        assert env_facts.recent_log_lines(path=str(path), limit=2) == ["line3", "line4"]

    def test_redacts_start_end_coordinates(self, tmp_path):
        path = tmp_path / "radiosim.log"
        path.write_text(
            "Simulation started: start=(34.5,132.4) end=(34.6,132.5) freq=2400.0 MHz samples=10\n",
            encoding="utf-8",
        )
        lines = env_facts.recent_log_lines(path=str(path))
        assert len(lines) == 1
        assert "34.5" not in lines[0]
        assert "132.4" not in lines[0]
        assert "start=(***) end=(***)" in lines[0]
        # 座標以外の情報（周波数・件数）は残る＝診断としての価値を保つ。
        assert "freq=2400.0" in lines[0]
        assert "samples=10" in lines[0]

    def test_redacts_float_formatted_coordinates(self, tmp_path):
        path = tmp_path / "radiosim.log"
        path.write_text(
            "Terrain cache hit: start=(34.542900,132.411800) end=(34.538900,132.405000) samples=10\n",
            encoding="utf-8",
        )
        line = env_facts.recent_log_lines(path=str(path))[0]
        assert "34.542900" not in line
        assert "start=(***) end=(***)" in line

    def test_redacts_lat_lon_formatted_coordinates(self, tmp_path):
        """B-180＝`core/dem.py` の DEM 警告が使う `lat=... lon=...` 書式も伏せる。"""
        path = tmp_path / "radiosim.log"
        path.write_text(
            "All DEM layers exhausted for lat=35.123456 lon=139.123456 "
            "after network trouble, returning nan\n",
            encoding="utf-8",
        )
        line = env_facts.recent_log_lines(path=str(path))[0]
        assert "35.123456" not in line
        assert "139.123456" not in line
        assert "lat=*** lon=***" in line

    def test_redacts_proxy_credentials(self, tmp_path):
        """B-180＝プロキシ URL の認証情報（`user:password@host`）を伏せる。"""
        path = tmp_path / "radiosim.log"
        path.write_text(
            "Proxy configured: 'http://user:secret@proxy.example.com:8080'\n",
            encoding="utf-8",
        )
        line = env_facts.recent_log_lines(path=str(path))[0]
        assert "user:secret" not in line
        assert "://***@proxy.example.com:8080" in line

    def test_lines_without_coordinates_are_untouched(self, tmp_path):
        path = tmp_path / "radiosim.log"
        path.write_text("Terrain fetch complete: 10 samples\n", encoding="utf-8")
        assert env_facts.recent_log_lines(path=str(path)) == [
            "Terrain fetch complete: 10 samples"
        ]

    def test_unreadable_file_returns_empty(self, tmp_path, monkeypatch):
        path = tmp_path / "radiosim.log"
        path.write_text("line\n", encoding="utf-8")

        def _boom(*a, **k):
            raise OSError("locked")

        monkeypatch.setattr("builtins.open", _boom)
        assert env_facts.recent_log_lines(path=str(path)) == []


class TestSanitizedConfig:
    def test_masks_start_and_end(self, monkeypatch):
        monkeypatch.setattr(
            env_facts.config, "load_config",
            lambda: {"start": "34.5, 132.4", "end": "34.6, 132.5", "freq": "2400.0"},
        )
        cfg = env_facts.sanitized_config()
        assert cfg["start"] == "***"
        assert cfg["end"] == "***"
        assert cfg["freq"] == "2400.0"     # 座標以外は素通し

    def test_empty_coordinates_are_left_as_is(self, monkeypatch):
        monkeypatch.setattr(
            env_facts.config, "load_config", lambda: {"start": "", "end": ""},
        )
        cfg = env_facts.sanitized_config()
        assert cfg["start"] == ""
        assert cfg["end"] == ""

    def test_masks_proxy_credentials_but_keeps_host(self, monkeypatch):
        monkeypatch.setattr(
            env_facts.config, "load_config",
            lambda: {"proxy_url": "http://alice:hunter2@proxy.example.com:8080"},
        )
        cfg = env_facts.sanitized_config()
        assert cfg["proxy_url"] == "http://***@proxy.example.com:8080"

    def test_proxy_url_without_credentials_is_untouched(self, monkeypatch):
        monkeypatch.setattr(
            env_facts.config, "load_config",
            lambda: {"proxy_url": "http://proxy.example.com:8080"},
        )
        cfg = env_facts.sanitized_config()
        assert cfg["proxy_url"] == "http://proxy.example.com:8080"

    def test_does_not_mutate_the_loaded_dict(self, monkeypatch):
        original = {"start": "34.5, 132.4", "end": "34.6, 132.5"}
        monkeypatch.setattr(env_facts.config, "load_config", lambda: original)
        env_facts.sanitized_config()
        assert original["start"] == "34.5, 132.4"


class TestEnvironmentInfo:
    def test_has_expected_keys(self):
        info = env_facts.environment_info()
        assert set(info) == {
            "platform", "python_version", "frozen", "portable", "dpi",
            "proxy_configured",
        }

    def test_proxy_configured_reflects_config(self, monkeypatch):
        monkeypatch.setattr(
            env_facts.config, "load_config", lambda: {"proxy_url": "http://p:8080"},
        )
        assert env_facts.environment_info()["proxy_configured"] is True

    def test_proxy_not_configured_when_blank(self, monkeypatch):
        monkeypatch.setattr(
            env_facts.config, "load_config", lambda: {"proxy_url": "   "},
        )
        assert env_facts.environment_info()["proxy_configured"] is False

    def test_frozen_reflects_sys_frozen(self, monkeypatch):
        monkeypatch.setattr(env_facts.sys, "frozen", True, raising=False)
        assert env_facts.environment_info()["frozen"] is True

    def test_dpi_none_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(env_facts.sys, "platform", "linux")
        assert env_facts.environment_info()["dpi"] is None

    def test_dpi_reads_get_dpi_for_system_on_windows(self, monkeypatch):
        monkeypatch.setattr(env_facts.sys, "platform", "win32")

        class _FakeUser32:
            def GetDpiForSystem(self):
                return 144

        class _FakeWindll:
            user32 = _FakeUser32()

        monkeypatch.setattr(ctypes, "windll", _FakeWindll(), raising=False)
        assert env_facts.environment_info()["dpi"] == 144

    def test_dpi_none_when_api_unavailable(self, monkeypatch):
        monkeypatch.setattr(env_facts.sys, "platform", "win32")

        class _FakeUser32:
            def GetDpiForSystem(self):
                raise AttributeError("no such api")

        class _FakeWindll:
            user32 = _FakeUser32()

        monkeypatch.setattr(ctypes, "windll", _FakeWindll(), raising=False)
        assert env_facts.environment_info()["dpi"] is None


class TestCollect:
    def test_returns_the_five_shared_keys(self, monkeypatch):
        monkeypatch.setattr(env_facts, "recent_log_lines", lambda: ["line"])
        monkeypatch.setattr(env_facts.dem, "get_cache_stats",
                             lambda: {"count": 1, "size_bytes": 2})
        facts = env_facts.collect()
        assert set(facts) == {
            "version", "config", "recent_log", "cache_stats", "environment",
        }
        assert facts["recent_log"] == ["line"]
        assert facts["cache_stats"] == {"count": 1, "size_bytes": 2}

    def test_config_in_collect_is_sanitized(self, monkeypatch):
        monkeypatch.setattr(
            env_facts.config, "load_config",
            lambda: {"start": "34.5, 132.4", "end": "34.6, 132.5"},
        )
        facts = env_facts.collect()
        assert facts["config"]["start"] == "***"
        assert facts["config"]["end"] == "***"

    def test_version_matches_core_version(self):
        from core import version
        assert env_facts.collect()["version"] == version.APP_VERSION
