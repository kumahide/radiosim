"""
map_graphics.py
===============
地図オーバーレイ描画の純 PIL 実装（tkinter 非依存）。

マップウィンドウ（`views/map_window.py`・UI）とレポート地図生成
（`report_map.py`・ヘッドレス）が共有する単一ソース。ここでは PIL の
``Image`` を返すだけにとどめ、Tk 連携（``ImageTk.PhotoImage`` ラップ）は
呼び出し側の UI 層で行う。これにより同じ見た目をヘッドレスでも再現できる。
"""

import math

from PIL import Image, ImageDraw, ImageFont

# UISP/Ubiquiti 風の基調色（ノード・パス線・ハロー・距離バッジ枠で共通）。
UISP_CYAN     = (25, 181, 230)     # RGB
UISP_CYAN_HEX = "#19B5E6"
MARKER_TEXT   = "#0E7CA0"          # ラベル文字（淡色地図でも読める濃いシアン）

# 判定ステータス別の経路色。summary.html の台帳（tr.ok / tr.ng / tr.err と
# .s-ok / .s-ng / .s-err）と同じ色を使い、全パス地図の線と表の行が同じ色で
# 対応づくようにする（色の定義は HTML/PIL で二重管理せずここを単一ソースとする）。
STATUS_RGB = {
    "OK":    (46, 125, 50),    # #2e7d32
    "NG":    (198, 40, 40),    # #c62828
    "ERROR": (191, 54, 12),    # #bf360c
}


def node_icon(hollow: bool) -> Image.Image:
    """UISP 風のノードアイコン（RGBA PIL Image）を生成する。

    半透明シアンのハロー（電波点の表現）＋白縁取りのシアンノード。
    hollow=False（TX）は塗りつぶし、hollow=True（RX）は白抜きで区別する。
    supersample → 縮小でアンチエイリアスする。
    """
    size, scale = 26, 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2
    r, g, b = UISP_CYAN

    def disc(radius: float, **kw) -> None:
        d.ellipse([c - radius, c - radius, c + radius, c + radius], **kw)

    # ハロー（半透明・大）→ 電波を発する点という UISP の雰囲気を出す。
    disc(s * 0.46, fill=(r, g, b, 55))
    disc(s * 0.34, fill=(r, g, b, 90))
    # ノード本体（白縁取りで地図上のコントラストを確保）。
    node_r = s * 0.22
    if hollow:   # RX: 白抜き（受信側）
        disc(node_r, fill=(255, 255, 255, 255),
             outline=(r, g, b, 255), width=int(2.2 * scale))
    else:        # TX: 塗り（送信側）
        disc(node_r, fill=(r, g, b, 255),
             outline=(255, 255, 255, 255), width=int(1.4 * scale))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def arrow_icon(bearing_deg: float) -> Image.Image:
    """TX→RX の方位（真北 0°・東 90°・時計回り）を指す矢じりアイコン（RGBA）を返す。

    確定パスの RX 端点マーカーに使う。塗りドット（TX）と別形状にすることで、
    TX/RX が近接・同一座標でも「どちらが受信側か」「向き」を形で判別できる
    （文字ラベルや塗り/白抜きの重なり問題を回避する）。地図は北上固定なので
    地理方位≒画面角度として扱う。supersample → 縮小でアンチエイリアスする。

    **矢じりの先端を画像中心に置く**（呼び出し側が icon_anchor="center" で配置する
    ため、先端が RX 座標に一致する）。本体は中心から後方（TX 側）へ伸びるので、
    全方位で本体が収まるよう余白込みの正方キャンバスにする。
    """
    size, scale = 42, 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2
    r, g, b = UISP_CYAN
    # 画面座標（y 下向き）での進行方向ベクトル: 北=上=-y、東=右=+x。
    rad = math.radians(bearing_deg)
    ux, uy = math.sin(rad), -math.cos(rad)
    px, py = -uy, ux                      # 進行方向に直交（矢じりの底辺方向）
    tip  = (c, c)                         # 先端＝画像中心＝RX 座標位置
    base = (c - ux * s * 0.405, c - uy * s * 0.405)
    half = s * 0.16
    left  = (base[0] + px * half, base[1] + py * half)
    right = (base[0] - px * half, base[1] - py * half)
    # 塗りシアンの三角＋白縁（淡色地図上のコントラスト確保）。縁は線で描き
    # PIL バージョン差（polygon の width 対応）に依存しないようにする。
    d.polygon([tip, left, right], fill=(r, g, b, 255))
    d.line([tip, left, right, tip], fill=(255, 255, 255, 255),
           width=int(1.4 * scale), joint="curve")
    return img.resize((size, size), Image.Resampling.LANCZOS)


def distance_badge(text: str) -> Image.Image:
    """距離テキストを半透明の角丸ピル背景に載せたバッジ画像（RGBA）を生成する。

    淡色地図上でテキストを読みやすくするため、テキストごと PIL で描く。
    """
    return pill_badge(text)


def pill_badge(
    text: str, *,
    outline: tuple[int, int, int] = UISP_CYAN,
    text_color: "str | tuple[int, int, int]" = MARKER_TEXT,
) -> Image.Image:
    """テキストを半透明の角丸ピル背景に載せたバッジ画像（RGBA）を生成する。

    枠・文字の色を渡せる（既定＝距離バッジの淡シアン）。全パス地図の path_id
    ラベルはステータス色（STATUS_RGB）を渡して台帳の行色と対応づける。
    """
    scale = 2
    try:
        font = ImageFont.truetype("arialbd.ttf", 13 * scale)
    except OSError:
        try:
            font = ImageFont.truetype("arial.ttf", 13 * scale)
        except OSError:
            font = ImageFont.load_default()
    # テキスト寸法を計測してパディング込みのバッジサイズを決める。
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    l, t, r, b = probe.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    padx, pady = 8 * scale, 4 * scale
    w, h = int(tw + padx * 2), int(th + pady * 2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 半透明の白ピル＋枠。淡色地図でも経路線上で読める。
    d.rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=h / 2,
        fill=(255, 255, 255, 215), outline=outline + (255,), width=scale,
    )
    d.text((padx - l, pady - t), text, font=font, fill=text_color)
    return img.resize((w // scale, h // scale), Image.Resampling.LANCZOS)


def north_arrow(dx: float, dy: float) -> Image.Image:
    """北方向ベクトル (dx, dy)（画像座標・y 下向き）を指す方位記号（RGBA）を返す。

    レポート地図は経路を水平にするため回転され「北が上」でなくなる。方角の
    手がかりとして、半透明の白円板に矢印と「N」を載せた小記号を返す（呼び出し
    側が画像の隅に貼る）。supersample → 縮小でアンチエイリアスする。
    """
    size, scale = 46, 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2
    norm = math.hypot(dx, dy) or 1.0
    ux, uy = dx / norm, dy / norm
    ink = (40, 40, 40, 255)

    # 半透明の白円板（淡色地図上で記号を読みやすく）。
    d.ellipse(
        [c - s * 0.40, c - s * 0.40, c + s * 0.40, c + s * 0.40],
        fill=(255, 255, 255, 180), outline=UISP_CYAN + (255,), width=scale,
    )
    r = s * 0.26
    tip  = (c + ux * r, c + uy * r)
    tail = (c - ux * r, c - uy * r)
    d.line([tail, tip], fill=ink, width=int(2.2 * scale))
    # 矢じり（tip に三角）。
    ang = math.atan2(uy, ux)
    for da in (math.radians(148), math.radians(-148)):
        hx = tip[0] + math.cos(ang + da) * s * 0.11
        hy = tip[1] + math.sin(ang + da) * s * 0.11
        d.line([tip, (hx, hy)], fill=ink, width=int(2.2 * scale))
    # "N" を北（tip）側に置く。
    try:
        font = ImageFont.truetype("arialbd.ttf", int(s * 0.22))
    except OSError:
        font = ImageFont.load_default()
    nl_x, nl_y = c + ux * r * 1.55, c + uy * r * 1.55
    l, t, rr, bb = d.textbbox((0, 0), "N", font=font)
    d.text((nl_x - (rr - l) / 2 - l, nl_y - (bb - t) / 2 - t), "N", font=font, fill=ink)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def distance_text(km: float) -> str:
    """水平距離 [km] を表示用テキストに整形する（1km 未満は m 表記）。"""
    return f"{km * 1000:.0f} m" if km < 1 else f"{km:.2f} km"
