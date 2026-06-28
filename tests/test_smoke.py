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

# トップレベル（ヘッドレス層）＋ views（GUI 層）を網羅。views も import 自体は
# ディスプレイ不要（tk.Tk() を作らない限り安全）。
_MODULES = [
    "main", "models", "simulation", "infrastructure",
    "batch", "report", "report_map", "map_graphics",
    "coords", "i18n", "mpl_fonts", "version",
    "views.launcher", "views.graph", "views.batch_builder",
    "views.map_window", "views.dialogs",
]


@pytest.mark.parametrize("mod", _MODULES)
def test_module_imports(mod):
    """各モジュールが例外なく import できること（壊れた import の早期検出）。"""
    importlib.import_module(mod)


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
