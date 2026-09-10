"""
views/map_adapter.py
====================
tkintermapview への**唯一の依存点**（3.3 段2・防火扉②の対）。

`views/` 配下で tkintermapview を import してよいのはこのファイルだけ
（`tests/test_layers.py` の `test_only_map_adapter_imports_tkintermapview`
がゲート）。ライブラリを差し替えるときは、このファイルの実装を書き換える
だけで呼び出し側（map_window.py / map_picks.py / map_cache.py）は触らない
——という約束を守るための境界。

いまは `TkinterMapView` の薄いサブクラスに留める（メソッド名の再抽象化は
しない）＝呼び出し側が広く直接メソッドを呼んでおり、1段抽象化しても
「差し替え可能性」には効かず変更量だけ増えるため。
"""

from tkintermapview import TkinterMapView
from tkintermapview.canvas_polygon import CanvasPolygon

__all__ = ["MapWidget", "MapPolygon"]


class MapWidget(TkinterMapView):
    """アプリが使う地図ウィジェット。振る舞いは `TkinterMapView` のまま。"""


MapPolygon = CanvasPolygon
