"""
tests/test_mpl_fonts.py
=======================
mpl_fonts.apply_japanese_font のユニットテスト。

フォントのインストール有無に依存しないよう font_manager をモックして、
「日本語モードで利用可能な日本語フォントを font.family に設定する／英語モードでは
何もしない」というロジックを決定論的に検証する。
"""

import sys
import os
from types import SimpleNamespace

import matplotlib
import matplotlib.font_manager  # noqa: F401  (fontManager へアクセスするため明示 import)
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import i18n
import mpl_fonts


def _fake_fontlist(*names):
    return [SimpleNamespace(name=n) for n in names]


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    """各テスト後に lang と rcParams を元へ戻す。"""
    lang_before   = i18n.t("html_lang")
    family_before = matplotlib.rcParams["font.family"]
    yield
    i18n.set_lang("en" if lang_before == "en" else "ja")
    matplotlib.rcParams["font.family"] = family_before


def _patch_fonts(monkeypatch, *names):
    monkeypatch.setattr(
        matplotlib.font_manager.fontManager, "ttflist", _fake_fontlist(*names)
    )


def test_english_mode_is_noop(monkeypatch):
    i18n.set_lang("en")
    matplotlib.rcParams["font.family"] = ["sentinel"]
    _patch_fonts(monkeypatch, "Meiryo")
    mpl_fonts.apply_japanese_font()
    assert matplotlib.rcParams["font.family"] == ["sentinel"]


def test_japanese_mode_sets_available_font(monkeypatch):
    i18n.set_lang("ja")
    _patch_fonts(monkeypatch, "Arial", "Meiryo")
    mpl_fonts.apply_japanese_font()
    assert matplotlib.rcParams["font.family"] == ["Meiryo"]


def test_japanese_mode_prefers_first_in_priority(monkeypatch):
    """優先順（Yu Gothic > Meiryo > ...）の先頭に来るものが選ばれること。"""
    i18n.set_lang("ja")
    _patch_fonts(monkeypatch, "Meiryo", "Yu Gothic")
    mpl_fonts.apply_japanese_font()
    assert matplotlib.rcParams["font.family"] == ["Yu Gothic"]


def test_japanese_mode_no_font_leaves_family_unchanged(monkeypatch):
    i18n.set_lang("ja")
    matplotlib.rcParams["font.family"] = ["sentinel"]
    _patch_fonts(monkeypatch, "Arial", "DejaVu Sans")
    mpl_fonts.apply_japanese_font()
    assert matplotlib.rcParams["font.family"] == ["sentinel"]
