"""
tests/test_smoke.py
===================
最小の GUI スモークテスト。

目的: import 時に壊れる類の回帰（シンボル改名・循環import・トップレベル副作用の
破綻）を、機能テストより手前で早期検出する。重い E2E ではなく「全モジュールが
import でき、tkinter のルートが生成できる」ことだけを確認する。

ヘッドレス CI（ディスプレイなし）では `tk.Tk()` が TclError になるため、その
ケースは skip する（import 検査は CI でも実行＝価値の中心はそちら）。
"""

import importlib

import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ヘッドレスでも import 可能なモジュール（tk.Tk() を作らない限り tkinter import は安全）。
_HEADLESS_SAFE = [
    "main", "models", "simulation", "infrastructure",
    "batch", "report", "report_map", "map_graphics",
    "coords", "i18n", "mpl_fonts", "version",
    "views.launcher", "views.batch_builder",
    "views.map_window", "views.dialogs",
]

# import 時に matplotlib の TkAgg バックエンドをロードするためディスプレイを要する。
# ヘッドレス CI では backend ロードに失敗するので skip する（views は CI では
# pyright の静的検査でカバー）。
_DISPLAY_REQUIRED = ["views.graph"]


@pytest.mark.parametrize("mod", _HEADLESS_SAFE)
def test_module_imports(mod):
    """各モジュールが例外なく import できること（壊れた import の早期検出）。"""
    importlib.import_module(mod)


@pytest.mark.parametrize("mod", _DISPLAY_REQUIRED)
def test_gui_module_imports(mod):
    """ディスプレイ必須の GUI モジュールの import（ヘッドレスは backend 失敗で skip）。"""
    try:
        importlib.import_module(mod)
    except Exception as e:  # noqa: BLE001  backend 起因のみ skip・他は再送出
        msg = str(e).lower()
        backend_markers = ("tkagg", "interactive framework", "backend", "headless")
        if any(k in msg for k in backend_markers):
            pytest.skip(f"requires display backend: {e}")
        raise  # 真の import 回帰（モジュール名違い等）は失敗させる


def test_tk_root_constructs():
    """tkinter のルートウィンドウが生成・破棄できること。

    ディスプレイのない環境（ヘッドレス CI）では skip する。
    """
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        root.withdraw()
    finally:
        root.destroy()
