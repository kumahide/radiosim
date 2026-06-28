"""
mpl_fonts.py
============
matplotlib に日本語対応フォントを適用するヘッドレスヘルパ。

UI 知識ゼロ（matplotlib + i18n のみ依存）。グラフウィンドウ（views/graph.py）と
バッチのヘッドレス PNG 生成（batch.save_profile_png）の双方から呼ぶ。

背景: 日本語フォントの適用は元々 graph.py の `show_graph` でのみ行われ、その rcParams
設定がプロセス全体に残ることに依存していた。そのためバッチを個別グラフより先に実行すると
レポート PNG の日本語ラベルが豆腐（□）化していた。フォント適用を本モジュールへ単一ソース化し、
バッチのレンダリング経路でも明示的に呼ぶことで操作順に依存しないようにする。
"""

import logging

import matplotlib

import i18n

logger = logging.getLogger("radiosim")

# 軸ラベル・タイトル用（モダンな日本語フォントを優先）
_JAPANESE_FONTS = ["Yu Gothic", "Meiryo", "MS Gothic", "BIZ UDGothic", "Hiragino Sans"]

# 等幅パネル用: MS Gothic は半角=全角/2 が保証された真の CJK 等幅フォント
_MONOSPACE_CJK  = ["MS Gothic", "BIZ UDGothic"] + _JAPANESE_FONTS


def apply_japanese_font() -> None:
    """日本語モード時に日本語対応フォントを matplotlib に設定する（冪等）。

    英語モードでは何もしない。rcParams への設定はプロセス全体に効くため、
    複数回・複数経路から呼んでも安全。
    """
    if i18n.t("html_lang") != "ja":
        return
    from matplotlib import font_manager
    available = {f.name for f in font_manager.fontManager.ttflist}

    for font in _JAPANESE_FONTS:
        if font in available:
            matplotlib.rcParams["font.family"] = font
            break
    else:
        logger.warning("No Japanese font found for matplotlib; text may appear garbled.")
        return

    mono = [f for f in _MONOSPACE_CJK if f in available] + ["Courier New", "DejaVu Sans Mono"]
    matplotlib.rcParams["font.monospace"]     = mono
    matplotlib.rcParams["axes.unicode_minus"] = False
