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

# 地図表現の基調色（ノード・パス線・ハロー・距離バッジ枠で共通）。淡色地図と
# 航空写真のどちらの上でも沈まず、判定色（緑/赤）とも衝突しないシアン系を選んだ。
MAP_CYAN     = (25, 181, 230)     # RGB
MAP_CYAN_HEX = "#19B5E6"
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
    """端点のノードアイコン（RGBA PIL Image）を生成する。

    半透明シアンのハロー（電波点の表現）＋白縁取りのシアンノード。
    hollow=False（TX）は塗りつぶし、hollow=True（RX）は白抜きで区別する。
    supersample → 縮小でアンチエイリアスする。
    """
    size, scale = 26, 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2
    r, g, b = MAP_CYAN

    def disc(radius: float, **kw) -> None:
        d.ellipse([c - radius, c - radius, c + radius, c + radius], **kw)

    # ハロー（半透明・大）→ 電波を発する点であることを表す。
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


def relay_icon() -> Image.Image:
    """中継点（ホップの折れ点）のアイコン＝**ひし形**（RGBA PIL Image）。

    送信点（塗りの丸）・受信点（白抜きの丸）と**形で**区別する。丸のまま濃淡や
    大小で分けると、地図上で近接したときに見分けがつかない。ひし形は「経路の
    折れ点」という意味にも合う。

    ⚠️ 既定のマーカー（tkintermapview の赤い水滴）は使わない＝アプリの地図表現
    （基調色のシアン系）から浮く。2026-08-01 の実機確認で指摘された。
    """
    size, scale = 22, 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = s / 2
    r, g, b = MAP_CYAN

    # 淡いハロー（端点より控えめ＝主役は両端）。
    d.ellipse([c - s * 0.44, c - s * 0.44, c + s * 0.44, c + s * 0.44],
              fill=(r, g, b, 45))
    # 塗りのひし形＋白縁（淡色地図でのコントラスト確保）。
    half = s * 0.30
    pts = [(c, c - half), (c + half, c), (c, c + half), (c - half, c)]
    d.polygon(pts, fill=(r, g, b, 255))
    d.line(pts + [pts[0]], fill=(255, 255, 255, 255),
           width=int(1.6 * scale), joint="curve")
    return img.resize((size, size), Image.Resampling.LANCZOS)


def distance_badge(text: str) -> Image.Image:
    """距離テキストを半透明の角丸ピル背景に載せたバッジ画像（RGBA）を生成する。

    淡色地図上でテキストを読みやすくするため、テキストごと PIL で描く。
    """
    return pill_badge(text)


def pill_badge(
    text: str, *,
    outline: tuple[int, int, int] = MAP_CYAN,
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
        fill=(255, 255, 255, 180), outline=MAP_CYAN + (255,), width=scale,
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


