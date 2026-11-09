"""
report_map.py
=============
経路を重ねた地図の静的画像を、完全ヘッドレス（PIL のみ）で生成する。

背景地図のタイルを取得・ステッチし、送受の点・経路線・判定の色を重ねた 1 枚の
PNG を返す（`render_paths_map`＝N 経路・回転なし〔north-up〕で全端点の外接矩形を
フィットさせる）。新しい画面の地図の PNG（`webui/exports.py`）がこれを焼き、
画面の地図の出典の字も `attribution_for` を引く。描画の見た目は map_graphics に集約。

設計判断（2026-06-15 確定）:
- ズームは経路にフィットする最大ズームを「タイル枚数上限」付きで自動選択（外部API配慮）。
- タイル取得率が閾値未満なら None を返す（呼び出し側は「地図取得不可」の注記を出す）。

1 経路を水平に回した地図（`render_path_map`）と帳票へ埋める base64 の口は Pro へ
移した（Link 1.0 ステージ11b）。
"""

import logging
import math
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageDraw

from core import dem
from core import i18n
from core import tile_sources
from report import map_graphics
from report import report_common

logger = logging.getLogger(__name__)

_TILE_PX     = 256
_MISSING_RGB = (229, 229, 229)  # 取得できなかったタイルの淡グレー埋め

# 回転後クロップのパラメータ（ピクセル）。
_ICON_HALF      = 13     # node_icon は 26x26（map_graphics）→ マーカーがはみ出ない余白
_MIN_HALF_PX    = 64     # 極短/退化経路でも確保する最小の半幅

# 出典表記を置く隅の余白（px）。北矢印は右上に貼るので**右下**に置く
# （地図出典の慣例位置でもある）。
_ATTR_MARGIN_PX = 4
#: 背景地図の既定 source_id（地図ウィンドウの選択が未保存のとき）。
_DEFAULT_BASEMAP_SOURCE_ID = "pale"

# 組み込み背景地図の出典表記キー。⚠️ **リテラルのモジュール定数で持つ**＝
# `t()` の引数は静的に読める形（リテラルかモジュール定数）でなければ
# ならない（B-101 の外部翻訳ゲート）。`map_graphics.ATTR_KEYS` と同じ値である
# ことは `tests/test_report_map.py` が突き合わせる。
_ATTR_KEY_PALE = "tm_attr_pale"
_ATTR_KEY_PHOTO = "tm_attr_photo"

_LatLon = tuple[float, float]


def attribution_for(source_id: str) -> str:
    """`source_id` → 出典表記の文言（B-248）。

    組み込み（`pale`/`photo`）は i18n キー越し。宣言した外部ソースは
    `spec.attribution`（生文字列・翻訳しない）。未知の `source_id` は `"pale"` へ
    フォールバック。

    🔑 帳票へ焼く地図と画面の地図（`webui/server.py` が最初の状態に載せる）が
    **この 1 本を引く**（B-133）＝航空写真を見ながら「出典: 淡色地図」を生まない。
    4.0 ステージ11a で旧画面と一緒に私有の名前（`_resolve_attribution`）から改めた。
    """
    if source_id == "pale":
        return i18n.t(_ATTR_KEY_PALE)
    if source_id == "photo":
        return i18n.t(_ATTR_KEY_PHOTO)
    for spec in tile_sources.all_sources():
        if spec.source_id == source_id:
            return spec.attribution
    return i18n.t(_ATTR_KEY_PALE)


def _clamp_zoom_range(
    source_id: str, min_zoom: int, max_zoom: int,
) -> tuple[int, int]:
    """ズームの範囲を背景地図ソースの最大ズームへ絞る（B-279）。

    宣言した `max_zoom` を超えるタイルは取れず、欠損扱いで地図ごと省かれる。
    `min_zoom` も上限以下へ抑える（範囲が空になって `choose_zoom` が
    上限を超えた `min_zoom` を返さないように）。
    """
    max_zoom = min(max_zoom, dem.basemap_max_zoom(source_id))
    return min(min_zoom, max_zoom), max_zoom


def _paste_attribution(img: "Image.Image", text: str) -> None:
    """出典表記を画像の右下へ焼き込む（B-133）。

    🔑 **地図を描く関数が必ずここを通る**のが対応のコア＝出典が抜けたのは
    「UI の地図には出したが、同じ絵を帳票へ焼くもう一方の経路が引き継がなかった」
    ためで、面ごとに書き足す形にすると**次の地図を足した人がまた落とす**。
    地図を描く関数（公開側の `render_paths_map`・Pro の `render_path_map`）は
    どれもここを通すので、1 か所で全面が満たされる。

    ⚠️ **文言はタイルのレイヤと対で保つ**＝背景地図の選択に追従する
    （B-248）ので、呼び出し側が `attribution_for(source_id)` で解決した
    ものをそのまま渡す（レイヤを替えた日に嘘の出典が焼かれないようにする）。
    """
    # 字の大きさは**この画像の幅**から決める（B-135）＝帳票では A4 幅へ縮めて
    # 載るので、固定 px だと経路の長さ（＝画像の幅）で実寸が変わってしまう。
    badge = map_graphics.attribution_badge(
        text, font_px=report_common.figure_text_px(img.width))
    img.paste(
        badge,
        (img.width - badge.width - _ATTR_MARGIN_PX,
         img.height - badge.height - _ATTR_MARGIN_PX),
        badge,
    )


# ============================================================
# 全パス1枚地図（summary.html 用・north-up）
# ============================================================
# 単一パス地図（Pro）が「経路を水平化するため回転＋バンド切り出し」なのに対し、
# こちらは複数経路を俯瞰するため **回転しない**（north-up）。全端点の外接矩形を
# 出力アスペクトへフィットさせるだけなので、回転由来の仕掛け（スーパーサンプル・
# expand 余白・インセット）はどれも不要＝単一パス版より素直に書ける。

# summary は A4 縦1枚が希望（[[project-roadmap]]）。地図が縦を食いすぎないよう
# 単一パス地図（15:5）よりわずかに背を高い程度に留める。
_PATHS_OUTPUT_ASPECT = 15 / 7
# path_id ラベルはこの本数までしか描かない（超えると地図が文字で埋まり、俯瞰と
# いう地図の役割を損なう。台帳＝summary の表が ID の正典なので地図は落として良い）。
_MAX_PATH_LABELS = 16


class PathSpec(NamedTuple):
    """全パス地図に描く1経路。呼ぶ側（`webui/exports.py`）が結果から詰め替えて渡す。

    report_map は batch に依存しないため、素のタプルで受ける。
    status は "OK" | "NG" | "REVIEW" | "ERROR"（map_graphics.STATUS_RGB）。
    """
    tx:     _LatLon
    rx:     _LatLon
    status: str = "OK"
    label:  str = ""


class _BboxPx(NamedTuple):
    """全端点を含む north-up 外接矩形（world ピクセル・出力アスペクトへ拡張済み）。"""
    cx:     float
    cy:     float
    half_w: float
    half_h: float


def _bbox_px(
    paths: "list[PathSpec]", zoom: int, margin_frac: float,
    aspect: float = _PATHS_OUTPUT_ASPECT,
) -> _BboxPx:
    """指定ズームで全端点を含む外接矩形を world ピクセルで求める。

    余白を足したうえで、幅/高さが aspect になるよう**広い方に合わせて拡張**する
    （縮めない＝どの端点も必ず枠内に残る）。
    """
    pts = [dem.lonlat_to_pixel(p[0], p[1], zoom)
           for spec in paths for p in (spec.tx, spec.rx)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    half_w = (max(xs) - min(xs)) / 2
    half_h = (max(ys) - min(ys)) / 2

    pad = max(max(half_w, half_h) * margin_frac, _MIN_HALF_PX) + _ICON_HALF
    half_w += pad
    half_h += pad
    # アスペクトへフィット（足りない側だけ広げる）。
    half_w = max(half_w, half_h * aspect)
    half_h = half_w / aspect
    return _BboxPx(cx, cy, half_w, half_h)


def _bbox_tiles(box: _BboxPx) -> tuple[int, int, int, int]:
    """外接矩形を覆うタイル範囲 (x0, x1, y0, y1) を返す（両端含む）。"""
    return (math.floor((box.cx - box.half_w) / _TILE_PX),
            math.floor((box.cx + box.half_w) / _TILE_PX),
            math.floor((box.cy - box.half_h) / _TILE_PX),
            math.floor((box.cy + box.half_h) / _TILE_PX))


def choose_zoom_paths(
    paths: "list[PathSpec]",
    max_tiles: int = 32, min_zoom: int = 5, max_zoom: int = 18,
    margin_frac: float = 0.15, aspect: float = _PATHS_OUTPUT_ASPECT,
) -> int:
    """外接矩形を覆うタイル総数 <= max_tiles の最大ズームを選ぶ。"""
    for zoom in range(max_zoom, min_zoom - 1, -1):
        x0, x1, y0, y1 = _bbox_tiles(_bbox_px(paths, zoom, margin_frac, aspect))
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= max_tiles:
            return zoom
    return min_zoom


def render_paths_map(
    paths: "list[PathSpec]", *,
    max_tiles: int = 32, margin_frac: float = 0.15,
    min_zoom: int = 5, max_zoom: int = 18,
    min_fetch_frac: float = 0.6, aspect: float = _PATHS_OUTPUT_ASPECT,
    basemap_source_id: str = _DEFAULT_BASEMAP_SOURCE_ID,
) -> "Image.Image | None":
    """全パスを1枚に俯瞰する north-up 地図を生成して PIL Image で返す。

    失敗時（paths が空・タイル取得率が min_fetch_frac 未満）は None を返し、
    呼び出し側が「地図取得不可」の注記を出す（単一パス地図と同じ方針）。

    Args:
        basemap_source_id: 背景地図の `source_id`（B-248）＝タイルと出典の字の両方をこれで選ぶ。
    """
    if not paths:
        return None
    try:
        min_zoom, max_zoom = _clamp_zoom_range(basemap_source_id, min_zoom, max_zoom)
        zoom = choose_zoom_paths(paths, max_tiles, min_zoom, max_zoom,
                                 margin_frac, aspect)
        box = _bbox_px(paths, zoom, margin_frac, aspect)
        x0, x1, y0, y1 = _bbox_tiles(box)

        tiles = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
        fetched = dem.fetch_basemap_tiles(tiles, zoom, basemap_source_id)
        if len(fetched) < max(1, round(len(tiles) * min_fetch_frac)):
            logger.warning(
                "report_map: only %d/%d basemap tiles fetched; skipping paths map",
                len(fetched), len(tiles),
            )
            return None

        w = (x1 - x0 + 1) * _TILE_PX
        h = (y1 - y0 + 1) * _TILE_PX
        canvas = Image.new("RGB", (w, h), _MISSING_RGB)
        for (x, y), arr in fetched.items():
            tile = Image.fromarray(np.asarray(arr)).convert("RGB")
            canvas.paste(tile, ((x - x0) * _TILE_PX, (y - y0) * _TILE_PX))

        # 外接矩形ぶんを切り出す（回転しないのでタイル格子と平行＝欠けは出ない）。
        origin_x, origin_y = x0 * _TILE_PX, y0 * _TILE_PX
        left   = int(round(box.cx - box.half_w)) - origin_x
        top    = int(round(box.cy - box.half_h)) - origin_y
        right  = int(round(box.cx + box.half_w)) - origin_x
        bottom = int(round(box.cy + box.half_h)) - origin_y
        cropped = canvas.crop((left, top, right, bottom))

        def _to_img(pt: _LatLon) -> tuple[int, int]:
            px, py = dem.lonlat_to_pixel(pt[0], pt[1], zoom)
            return int(round(px - origin_x - left)), int(round(py - origin_y - top))

        draw = ImageDraw.Draw(cropped)
        # 線 → ノード → ラベルの順に重ねる（後のものが上）。
        for spec in paths:
            color = map_graphics.STATUS_RGB.get(spec.status,
                                                map_graphics.STATUS_RGB["ERROR"])
            draw.line([_to_img(spec.tx), _to_img(spec.rx)], fill=color, width=3)

        for spec in paths:
            for pt, hollow in ((spec.tx, False), (spec.rx, True)):
                icon = map_graphics.node_icon(hollow)
                ix, iy = _to_img(pt)
                cropped.paste(
                    icon, (ix - icon.width // 2, iy - icon.height // 2), icon
                )

        if len(paths) <= _MAX_PATH_LABELS:
            for spec in paths:
                if not spec.label:
                    continue
                color = map_graphics.STATUS_RGB.get(spec.status,
                                                    map_graphics.STATUS_RGB["ERROR"])
                badge = map_graphics.pill_badge(
                    spec.label, outline=color, text_color=color
                )
                ax, ay = _to_img(spec.tx)
                bx, by = _to_img(spec.rx)
                cropped.paste(
                    badge,
                    ((ax + bx) // 2 - badge.width // 2,
                     (ay + by) // 2 - badge.height // 2),
                    badge,
                )

        # north-up だが「北が上」であること自体を明示する（上向き矢印）。
        arrow = map_graphics.north_arrow(0.0, -1.0)
        cropped.paste(arrow, (cropped.width - arrow.width - 6, 6), arrow)

        _paste_attribution(cropped, attribution_for(basemap_source_id))
        return cropped
    except Exception:
        logger.warning("report_map: failed to render paths map", exc_info=True)
        return None


