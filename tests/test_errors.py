"""
tests/test_errors.py
====================
GUI のコールバックで起きた例外が**必ずログとダイアログに出る**ことのゲート（I-059）。

**なぜこのテストが要るか**（2026-08-03）: 2.6RC1 の実機で、単一実行が
「グラフを準備中…」のまま戻らなくなった。正体は `show_graph` の
`ModuleNotFoundError`（B-036）だが、**Tk の既定の `report_callback_exception` は
traceback を stderr へ書くだけ**で、windowed の exe には stderr が無い。結果、
**ログにも画面にも何も残らず、固まったようにしか見えなかった**。
同じ実行のバッチ側は `except` がログを 1 行残していたので原因に辿り着けた＝
**残っているかどうかで診断可能性が決まる**。

強制の仕方＝「ログを出すこと」を注意書きにせずテストにする
（[[feedback-promote-recurring-checks]] / 実行時制約はコメントでなくテストで表現）。
"""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from conftest import make_tk_root
from views import dialogs, errors


@pytest.fixture
def root():
    r = make_tk_root()
    r.withdraw()
    errors.install(r)
    try:
        yield r
    finally:
        r.destroy()


def _raise_in_callback(root, exc: Exception) -> None:
    """Tk のコールバックの中で例外を出す（`after` 経由＝実際の経路と同じ）。"""
    def _boom() -> None:
        raise exc
    root.after(0, _boom)
    root.update()


class TestTkCallbackExceptions:
    def test_exception_is_logged_with_traceback(self, root, caplog):
        with caplog.at_level(logging.ERROR, logger="radiosim"):
            _raise_in_callback(root, ModuleNotFoundError("No module named 'timeit'"))

        records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert records, "コールバックの例外がログに 1 行も残っていない"
        rec = records[0]
        # traceback が付いていること＝「誰が投げたか」が残る。B-036 で効いた差。
        assert rec.exc_info is not None, "exc_info が無い＝メッセージだけで traceback が残らない"
        assert "timeit" in caplog.text

    def test_user_sees_a_dialog_naming_the_log_file(self, root, dialog_calls):
        _raise_in_callback(root, RuntimeError("boom"))

        alerts = [c for c in dialog_calls if c[0] == "alert"]
        assert alerts, "例外が起きたのにダイアログを出そうとしていない"
        message = alerts[0][2]
        assert "RuntimeError" in message and "boom" in message
        # ログの場所を書く＝利用者が持ち帰れるものを示す（実機報告の再現性）。
        assert os.path.basename(config.LOG_FILE) in message

    def test_dialog_does_not_stack_when_errors_repeat(self, root, monkeypatch):
        """表示中に次の例外が来てもモーダルを積み上げない（再入防止）。

        描画のたびに例外が出る種類の故障（まさに B-036）では、これが無いと
        ダイアログが無限に生えて操作できなくなる。
        """
        shown: list[str] = []

        def _alert_that_reenters(parent, title, message):
            shown.append(title)
            # ダイアログ表示中に届いた 2 つ目の例外を模す。
            handler = root.report_callback_exception
            handler(ValueError, ValueError("second"), None)

        monkeypatch.setattr(dialogs, "alert", _alert_that_reenters)
        _raise_in_callback(root, RuntimeError("first"))

        assert len(shown) == 1, f"ダイアログが積み上がっている: {shown}"

    def test_handler_survives_a_broken_dialog(self, root, monkeypatch, caplog):
        """ダイアログが出せない状況でも、ログだけは残って例外は外へ出ない。"""
        def _alert_that_fails(parent, title, message):
            raise RuntimeError("no display")

        monkeypatch.setattr(dialogs, "alert", _alert_that_fails)
        with caplog.at_level(logging.ERROR, logger="radiosim"):
            _raise_in_callback(root, RuntimeError("original"))

        assert "original" in caplog.text
        assert "Failed to show the error dialog" in caplog.text


def test_install_replaces_the_default_handler():
    """`install` を呼ぶまでは Tk の既定＝stderr 直行のままであること。"""
    r = make_tk_root()
    r.withdraw()
    try:
        default = r.report_callback_exception
        errors.install(r)
        assert r.report_callback_exception is not default
    finally:
        r.destroy()
