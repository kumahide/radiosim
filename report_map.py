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
- タイル取得に全滅したら None を返す（呼び出し側は「地図取得不可」の注記を出す）。
- オーバーレイは TX/RX マーカー＋経路線＋距離ラベルのみ。
"""

import base64
import io
import logging

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

        # --- padded bbox の矩形にクロップ（クロップ左上 = (lat_n, lon_w) コーナー） ---
        nw_x, nw_y = infra.lonlat_to_pixel(bbox[0], bbox[2], zoom)  # lat_n, lon_w
        se_x, se_y = infra.lonlat_to_pixel(bbox[1], bbox[3], zoom)  # lat_s, lon_e
        origin_x, origin_y = x0 * _TILE_PX, y0 * _TILE_PX
        left   = max(0, int(round(nw_x - origin_x)))
        top    = max(0, int(round(nw_y - origin_y)))
        right  = min(w, int(round(se_x - origin_x)))
        bottom = min(h, int(round(se_y - origin_y)))
        if right - left < 2 or bottom - top < 2:   # 念のため退化ガード
            left, top, right, bottom = 0, 0, w, h
        cropped = canvas.crop((left, top, right, bottom))

        # --- オーバーレイ（座標 → クロップ画像内ピクセル） ---
        # クロップ左上の world px は (nw_x, nw_y)。点の画像内座標 = world - nw。
        def to_px(pt: _LatLon) -> tuple[int, int]:
            gx, gy = infra.lonlat_to_pixel(pt[0], pt[1], zoom)
            return int(round(gx - nw_x)), int(round(gy - nw_y))

        tx_px, rx_px = to_px(tx), to_px(rx)
        draw = ImageDraw.Draw(cropped)
        draw.line([tx_px, rx_px], fill=map_graphics.UISP_CYAN_HEX, width=3)

        for pt, hollow in ((tx, False), (rx, True)):
            icon = map_graphics.node_icon(hollow)
            px, py = to_px(pt)
            cropped.paste(icon, (px - icon.width // 2, py - icon.height // 2), icon)

        km = models.horizontal_distance_km(tx[0], tx[1], rx[0], rx[1])
        badge = map_graphics.distance_badge(map_graphics.distance_text(km))
        mid_x, mid_y = (tx_px[0] + rx_px[0]) // 2, (tx_px[1] + rx_px[1]) // 2
        cropped.paste(badge, (mid_x - badge.width // 2, mid_y - badge.height // 2), badge)

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
