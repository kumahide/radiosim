"""
tests/test_legacy_leftover_notice.py
=====================================
起動時の「旧配置に残っているデータ」案内（3.2・ロードマップ「旧配置の残骸を
検出して知らせる」）のガード。

`config.legacy_leftovers()` の検出結果を `views.launcher.SimLauncher.
_warn_about_legacy_data()` がどう画面へ出すかを確認する。検出そのものの
分岐（ポータブル判定・存在有無）は tests/test_write_locations.py が守る。
"""

import pytest

from core import config


@pytest.fixture
def app(monkeypatch):
    pytest.importorskip("tkinter")
    from tests.conftest import make_themed_root       # noqa: PLC0415
    from views.launcher import SimLauncher            # noqa: PLC0415

    root = make_themed_root()
    root.withdraw()
    launcher = SimLauncher(root, lambda _t: None)
    try:
        yield launcher
    finally:
        root.destroy()


def test_no_alert_when_nothing_left(app, monkeypatch):
    monkeypatch.setattr(config, "legacy_leftovers", lambda: {})
    calls = []
    monkeypatch.setattr(type(app), "_alert",
                         lambda self, t, m: calls.append((t, m)), raising=False)

    app._warn_about_legacy_data()

    assert calls == []


def test_alert_lists_both_paths_when_both_are_found(app, monkeypatch):
    monkeypatch.setattr(config, "legacy_leftovers", lambda: {
        "config":  r"C:\old\radiosim_conf.json",
        "results": r"C:\old\results",
    })
    calls = []
    monkeypatch.setattr(type(app), "_alert",
                         lambda self, t, m: calls.append((t, m)), raising=False)

    app._warn_about_legacy_data()

    assert len(calls) == 1
    title, message = calls[0]
    assert title == "Leftover data from the old location"
    assert r"C:\old\radiosim_conf.json" in message
    assert r"C:\old\results" in message


def test_alert_omits_the_path_that_was_not_found(app, monkeypatch):
    monkeypatch.setattr(config, "legacy_leftovers", lambda: {
        "config": r"C:\old\radiosim_conf.json",
    })
    calls = []
    monkeypatch.setattr(type(app), "_alert",
                         lambda self, t, m: calls.append((t, m)), raising=False)

    app._warn_about_legacy_data()

    assert len(calls) == 1
    _title, message = calls[0]
    assert r"C:\old\radiosim_conf.json" in message
    assert "results" not in message.lower(), (
        "見つかっていない旧 results への言及が本文に紛れ込んでいる"
    )
