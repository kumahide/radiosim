"""
views/map_style.py
==================
地図窓の**描画定数の単一ソース**（色・余白・ズーム）。

`map_window.py` とその Mixin（`map_picks` / `map_cache`）が共有するので、
どれか 1 つに置くと import が循環する。⇒ **値だけを持つ最下層**として分けた
（切り出しは 2.7 スライス A・値は 1 つも変えていない）。

⛔ ここに関数やウィジェットを置かない（置いた瞬間に循環が戻る）。
"""

from report import map_graphics

# マーカー配色は map_graphics に集約（レポート地図生成 report_map.py と共通）。
_MAP_CYAN_HEX = map_graphics.MAP_CYAN_HEX
_MARKER_TEXT   = map_graphics.MARKER_TEXT

# キャッシュ済み領域の外周線の色
_OUTLINE_COLOR = "#0066CC"

# zoom-14 オーバーレイの色（最高精度レベルで色分け）。
# scan_cache_overlay はキャッシュ済みセルのみ返す（未取得は描画しない）ため、
# ここに含めるのは 5a/5b/dem の 3 レベルだけ。
_LEVEL_COLORS: dict[str, str] = {
    "5a":   "#90EE90",  # 緑: 5m航空（dem5a_png）
    "5b":   "#FFD700",  # 黄: 5m写真（dem5b_png）
    "dem":  "#87CEEB",  # 水色: 10m（dem_png）
}

_FIT_MARGIN   = 0.25    # bbox を広げる余白（経路が縁に張り付かないように）
_FIT_MIN_SPAN = 0.002   # 退化（同緯度/同経度の経路）回避の最小スパン（度）
_SINGLE_ZOOM  = 13      # TX/RX 片方だけ設定済みのときの初期ズーム
