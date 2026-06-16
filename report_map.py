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
  方角の手がかりに北矢印を重ねる（2026-06-16）。
"""

import base64
import io
import logging
import math

import numpy as np
from PIL import Image, ImageDraw

import infrastructure as infra
import map_graphics
import models

logger = logging.getLogger(__name__)

_TILE_PX     = 256
_MISSING_RGB = (229, 229, 229)  # 取得できなかったタイルの淡グレー埋め

# 退化（TX==RX や極近接）時でも周辺文脈が見える最小スパン（緯度経度・度）。
_MIN_SPAN_DEG = 0.003

# 回転後クロップのパラメータ（ピクセル）。
_ICON_HALF      = 13     # node_icon は 26x26（map_graphics）→ マーカーがはみ出ない余白
_MIN_HALF_PX    = 64     # 極短/退化経路でも確保する最小の半幅・半高
_BAND_HALF_FRAC = 0.35   # 経路長に対する上下バンドの半高比（周辺文脈の量）

_LatLon = tuple[float, float]


def _padded_bbox(
    tx: _LatLon, rx: _LatLon, margin_frac: float,
) -> tuple[float, float, float, float]:
    """TX/RX を含む bbox に余白を足して (lat_n, lat_s, lon_w, lon_e) を返す。"""
    lat_n = max(tx[0], rx[0])
    lat_s = min(tx[0], rx[0])
    lon_w = min(tx[1], rx[1])
    lon_e = max(tx[1], rx[1])
    span_lat = max(lat_n - lat_s, _MIN_SPAN_DEG)
    span_lon = max(lon_e - lon_w, _MIN_SPAN_DEG)
    # 退化時は中心から最小スパンの半分ずつ広げる。
    cy, cx = (lat_n + lat_s) / 2, (lon_w + lon_e) / 2
    lat_n, lat_s = cy + span_lat / 2, cy - span_lat / 2
    lon_w, lon_e = cx - span_lon / 2, cx + span_lon / 2
    pad_lat = span_lat * margin_frac
    pad_lon = span_lon * margin_frac
    return lat_n + pad_lat, lat_s - pad_lat, lon_w - pad_lon, lon_e + pad_lon


def _tile_range(
    bbox: tuple[float, float, float, float], zoom: int,
) -> tuple[int, int, int, int]:
    """padded bbox の (x0, x1, y0, y1) タイル範囲を返す（両端含む）。"""
    lat_n, lat_s, lon_w, lon_e = bbox
    x0, y0, _, _ = infra._tile_coords(lat_n, lon_w, zoom)  # NW = 最小 (x, y)
    x1, y1, _, _ = infra._tile_coords(lat_s, lon_e, zoom)  # SE = 最大 (x, y)
    return x0, x1, y0, y1


def choose_zoom(
    tx: _LatLon, rx: _LatLon,
    max_tiles: int = 16, min_zoom: int = 5, max_zoom: int = 18,
    margin_frac: float = 0.15,
) -> int:
    """経路にフィットする最大ズームを、タイル総数 <= max_tiles の範囲で選ぶ。"""
    bbox = _padded_bbox(tx, rx, margin_frac)
    for zoom in range(max_zoom, min_zoom - 1, -1):
        x0, x1, y0, y1 = _tile_range(bbox, zoom)
        if (x1 - x0 + 1) * (y1 - y0 + 1) <= max_tiles:
            return zoom
    return min_zoom


def render_path_map(
    tx: _LatLon, rx: _LatLon, *,
    max_tiles: int = 16, margin_frac: float = 0.15,
    min_zoom: int = 5, max_zoom: int = 18,
    min_fetch_frac: float = 0.6,
) -> "Image.Image | None":
    """経路オーバーレイ地図を生成して PIL Image で返す。失敗時は None。

    取得できたタイルの割合が min_fetch_frac 未満なら「地図取得不可」として
    None を返す（灰色の欠けが目立つ中途半端な地図を黙って埋め込まない）。
    """
    try:
        bbox = _padded_bbox(tx, rx, margin_frac)
        zoom = choose_zoom(tx, rx, max_tiles, min_zoom, max_zoom, margin_frac)
        x0, x1, y0, y1 = _tile_range(bbox, zoom)

        # --- タイルを並列取得してキャンバスにステッチ ---
        # 取得・キャッシュの所在は infrastructure が所有（並列＝メインスレッドでも
        # 逐次待ちで GUI を固めない）。座標だけ渡し、戻りの {(x,y):配列} を貼る。
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
        # 北上きキャンバス内の TX/RX ピクセル（キャンバス左上 = (x0,y0)タイル原点）。
        origin_x, origin_y = x0 * _TILE_PX, y0 * _TILE_PX

        def _canvas_px(pt: _LatLon) -> tuple[float, float]:
            gx, gy = infra.lonlat_to_pixel(pt[0], pt[1], zoom)
            return gx - origin_x, gy - origin_y

        ax, ay = _canvas_px(tx)
        bx, by = _canvas_px(rx)
        # path ベクトル角ぶん回す → TX→RX が水平右向きになる（atan2 の導出による）。
        deg = math.degrees(math.atan2(by - ay, bx - ax))
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

        txr = _to_rotated(ax, ay)
        rxr = _to_rotated(bx, by)

        # --- 経路を中心に水平バンドへクロップ（退化・極短経路でも最小ビューを確保）---
        path_len = math.hypot(rxr[0] - txr[0], rxr[1] - txr[1])
        pad_x  = max(path_len * margin_frac,  _MIN_HALF_PX) + _ICON_HALF
        half_h = max(path_len * _BAND_HALF_FRAC, _MIN_HALF_PX) + _ICON_HALF
        cy = (txr[1] + rxr[1]) / 2.0
        left   = max(0, int(round(min(txr[0], rxr[0]) - pad_x)))
        right  = min(rotated.width,  int(round(max(txr[0], rxr[0]) + pad_x)))
        top    = max(0, int(round(cy - half_h)))
        bottom = min(rotated.height, int(round(cy + half_h)))
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
