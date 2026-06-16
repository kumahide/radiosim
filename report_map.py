"""
report_map.py
=============
レポート（report.html）へ埋め込む「経路オーバーレイ地図」の静的画像を、
完全ヘッドレス（tkinter 非依存・PIL のみ）で生成する。

GSI 淡色地図タイルを取得・ステッチし、TX/RX マーカー・経路線・距離ラベルを
重ねた 1 枚の PNG を返す。マップウィンドウ（views/map_window.py・UI）とは独立。
描画の見た目は map_graphics に集約し UI と共有する。

設計判断（2026-06-15 確定）:
- ズームは経路にフィットする最大ズームを「タイル枚数上限」付きで自動選択（外部API配慮）。
- タイル取得率が閾値未満なら None を返す（呼び出し側は「地図取得不可」の注記を出す）。
- オーバーレイは TX/RX マーカー＋経路線＋距離ラベルのみ。
- 経路が常に水平（TX 左・RX 右）になるよう画像を回転し、北が上でなくなるため
  方角の手がかりに北矢印を重ねる（2026-06-16）。回転で生じる余白（グレー）を
  出さないよう、経路に沿った水平バンドの north-up 外接矩形ぶんを取得し、回転後に
  バンドだけを切り出す。
"""

import base64
import io
import logging
import math
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageDraw

import infrastructure as infra
import map_graphics
import models

logger = logging.getLogger(__name__)

_TILE_PX     = 256
_MISSING_RGB = (229, 229, 229)  # 取得できなかったタイルの淡グレー埋め

# 回転後クロップのパラメータ（ピクセル）。
_ICON_HALF      = 13     # node_icon は 26x26（map_graphics）→ マーカーがはみ出ない余白
_MIN_HALF_PX    = 64     # 極短/退化経路でも確保する最小の半幅・半高
_BAND_HALF_FRAC = 0.35   # 経路長に対する上下バンドの半高比（周辺文脈の量）
# バンド隅がタイル境界に整列したとき、回転の BICUBIC 端に出る 1–2px のグレー
# フリンジを避けるため、最終バンドをこの px ぶん内側へ詰めて切り出す（バンドは
# north-up 外接矩形ぶん取得済みで内側にあり、数 px の縮小は実害なし）。
_CROP_INSET_PX  = 2

_LatLon = tuple[float, float]


class _BandPx(NamedTuple):
    """指定ズームでの「経路に沿った水平バンド」幾何（すべて world ピクセル）。

    回転後はこのバンドをそのまま水平に切り出す。half_w/half_h はバンドの半幅/
    半高（pad・最小値込み）で、回転は等長変換なので回転後フレームでも有効。
    """
    ax: float    # TX
    ay: float
    bx: float    # RX
    by: float
    mx: float    # 中点
    my: float
    ux: float    # TX→RX 単位ベクトル（退化時は東向き）
    uy: float
    px: float    # ux に直交する単位ベクトル
    py: float
    half_w: float
    half_h: float


def _band_px(tx: _LatLon, rx: _LatLon, zoom: int, margin_frac: float) -> _BandPx:
    """指定ズームでの経路バンド幾何を world ピクセルで求める。"""
    ax, ay = infra.lonlat_to_pixel(tx[0], tx[1], zoom)
    bx, by = infra.lonlat_to_pixel(rx[0], rx[1], zoom)
    path_len = math.hypot(bx - ax, by - ay)
    if path_len < 1.0:                       # 退化/極近接 → 東向きに固定
        ux, uy, path_len = 1.0, 0.0, 0.0
    else:
        ux, uy = (bx - ax) / path_len, (by - ay) / path_len
    pad_x  = max(path_len * margin_frac,     _MIN_HALF_PX) + _ICON_HALF
    half_h = max(path_len * _BAND_HALF_FRAC, _MIN_HALF_PX) + _ICON_HALF
    half_w = path_len / 2 + pad_x
    return _BandPx(ax, ay, bx, by, (ax + bx) / 2, (ay + by) / 2,
                   ux, uy, -uy, ux, half_w, half_h)


def _coverage_tiles(band: _BandPx) -> tuple[int, int, int, int]:
    """バンド4隅を内包する north-up タイル範囲 (x0, x1, y0, y1) を返す（両端含む）。

    バンドは経路に沿って傾いているため、その north-up 外接矩形ぶんのタイルを
    取得する。これで回転後にバンドを切り出してもグレーの欠けが出ない。
    """
    corners = [
        (band.mx + s1 * band.ux * band.half_w + s2 * band.px * band.half_h,
         band.my + s1 * band.uy * band.half_w + s2 * band.py * band.half_h)
        for s1 in (-1, 1) for s2 in (-1, 1)
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (math.floor(min(xs) / _TILE_PX), math.floor(max(xs) / _TILE_PX),
            math.floor(min(ys) / _TILE_PX), math.floor(max(ys) / _TILE_PX))


def choose_zoom(
    tx: _LatLon, rx: _LatLon,
    max_tiles: int = 32, min_zoom: int = 5, max_zoom: int = 18,
    margin_frac: float = 0.15,
) -> int:
    """回転後バンドを覆う north-up タイル総数 <= max_tiles の最大ズームを選ぶ。

    回転バンドの north-up 外接矩形は素の TX/RX bbox より広いため、同じ解像度
    （ズーム）を保つには上限を従来(16)より大きく取る。basemap タイルは軽量＋
    キャッシュ済みで、レポート保存時の一度きりの取得なので外部API負荷は小さい。
    """
    for zoom in range(max_zoom, min_zoom - 1, -1):
        x0, x1, y0, y1 = _coverage_tiles(_band_px(tx, rx, zoom, margin_frac))
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= max_tiles:
            return zoom
    return min_zoom


def render_path_map(
    tx: _LatLon, rx: _LatLon, *,
    max_tiles: int = 32, margin_frac: float = 0.15,
    min_zoom: int = 5, max_zoom: int = 18,
    min_fetch_frac: float = 0.6,
) -> "Image.Image | None":
    """経路オーバーレイ地図を生成して PIL Image で返す。失敗時は None。

    取得できたタイルの割合が min_fetch_frac 未満なら「地図取得不可」として
    None を返す（灰色の欠けが目立つ中途半端な地図を黙って埋め込まない）。
    """
    try:
        zoom = choose_zoom(tx, rx, max_tiles, min_zoom, max_zoom, margin_frac)
        band = _band_px(tx, rx, zoom, margin_frac)
        x0, x1, y0, y1 = _coverage_tiles(band)

        # --- バンドの north-up 外接矩形ぶんを並列取得してステッチ ---
        # 取得・キャッシュの所在は infrastructure が所有（並列＝メインスレッドでも
        # 逐次待ちで GUI を固めない）。座標だけ渡し、戻りの {(x,y):配列} を貼る。
        # バンドより広く取るので、回転後に切り出してもグレーの欠けが出ない。
        tiles = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
        fetched = infra.fetch_basemap_tiles(tiles, zoom)

        if len(fetched) < max(1, round(len(tiles) * min_fetch_frac)):
            # 取得率が閾値未満（全滅含む）→ 地図なし（呼び出し側が注記を出す）
            logger.warning(
                "report_map: only %d/%d basemap tiles fetched; skipping map",
                len(fetched), len(tiles),
            )
            return None

        w = (x1 - x0 + 1) * _TILE_PX
        h = (y1 - y0 + 1) * _TILE_PX
        canvas = Image.new("RGB", (w, h), _MISSING_RGB)
        for (x, y), arr in fetched.items():
            tile = Image.fromarray(np.asarray(arr)).convert("RGB")
            canvas.paste(tile, ((x - x0) * _TILE_PX, (y - y0) * _TILE_PX))

        # --- 経路が水平（TX 左・RX 右）になるよう全体を回転 ---
        origin_x, origin_y = x0 * _TILE_PX, y0 * _TILE_PX
        ax_c, ay_c = band.ax - origin_x, band.ay - origin_y   # キャンバス内 TX
        bx_c, by_c = band.bx - origin_x, band.by - origin_y   # キャンバス内 RX
        mx_c, my_c = band.mx - origin_x, band.my - origin_y   # キャンバス内 中点
        # path ベクトル角ぶん回す → TX→RX が水平右向きになる（atan2 の導出による）。
        deg = math.degrees(math.atan2(by_c - ay_c, bx_c - ax_c))
        rotated = canvas.rotate(
            deg, resample=Image.Resampling.BICUBIC, expand=True,
            fillcolor=_MISSING_RGB,
        )

        # PIL.rotate(expand=True) は入力中心を出力中心へ写す。これを使って任意点を
        # 回転後座標へ再投影する（deg>0 = 反時計回り・y 下向き座標系）。
        cin_x, cin_y = w / 2.0, h / 2.0
        cout_x, cout_y = rotated.width / 2.0, rotated.height / 2.0
        a = math.radians(deg)
        cos_a, sin_a = math.cos(a), math.sin(a)

        def _to_rotated(px: float, py: float) -> tuple[float, float]:
            dx, dy = px - cin_x, py - cin_y
            return (cout_x + dx * cos_a + dy * sin_a,
                    cout_y - dx * sin_a + dy * cos_a)

        txr = _to_rotated(ax_c, ay_c)
        rxr = _to_rotated(bx_c, by_c)

        # --- 経路中点を中心に、バンド半幅・半高ぴったりにクロップ ---
        # バンド ⊂ キャンバス なので回転 expand のグレー余白は入らない。
        cmx, cmy = _to_rotated(mx_c, my_c)
        left   = max(0, int(round(cmx - band.half_w)) + _CROP_INSET_PX)
        right  = min(rotated.width,  int(round(cmx + band.half_w)) - _CROP_INSET_PX)
        top    = max(0, int(round(cmy - band.half_h)) + _CROP_INSET_PX)
        bottom = min(rotated.height, int(round(cmy + band.half_h)) - _CROP_INSET_PX)
        cropped = rotated.crop((left, top, right, bottom))

        # --- オーバーレイ（回転後の正立座標で描画） ---
        def _shift(p: tuple[float, float]) -> tuple[int, int]:
            return int(round(p[0] - left)), int(round(p[1] - top))

        tx_px, rx_px = _shift(txr), _shift(rxr)
        draw = ImageDraw.Draw(cropped)
        draw.line([tx_px, rx_px], fill=map_graphics.UISP_CYAN_HEX, width=3)

        for px_, hollow in ((tx_px, False), (rx_px, True)):
            icon = map_graphics.node_icon(hollow)
            cropped.paste(
                icon, (px_[0] - icon.width // 2, px_[1] - icon.height // 2), icon
            )

        km = models.horizontal_distance_km(tx[0], tx[1], rx[0], rx[1])
        badge = map_graphics.distance_badge(map_graphics.distance_text(km))
        mid_x, mid_y = (tx_px[0] + rx_px[0]) // 2, (tx_px[1] + rx_px[1]) // 2
        cropped.paste(badge, (mid_x - badge.width // 2, mid_y - badge.height // 2), badge)

        # 回転で北が上でなくなるため方角の手がかりを右上隅に重ねる。
        # 北（北上き画像で -y 方向）は回転後 (-sin a, -cos a) を向く。
        arrow = map_graphics.north_arrow(-sin_a, -cos_a)
        cropped.paste(arrow, (cropped.width - arrow.width - 6, 6), arrow)

        return cropped
    except Exception:
        logger.warning("report_map: failed to render path map", exc_info=True)
        return None


def render_path_map_b64(tx: _LatLon, rx: _LatLon, **kwargs) -> "str | None":
    """render_path_map の結果を PNG → base64 文字列で返す。失敗時は None。"""
    img = render_path_map(tx, rx, **kwargs)
    if img is None:
        return None
    try:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        logger.warning("report_map: failed to encode path map", exc_info=True)
        return None
