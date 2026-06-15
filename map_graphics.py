"""
map_graphics.py
===============
地図オーバーレイ描画の純 PIL 実装（tkinter 非依存）。

マップウィンドウ（`views/map_window.py`・UI）とレポート地図生成
（`report_map.py`・ヘッドレス）が共有する単一ソース。ここでは PIL の
``Image`` を返すだけにとどめ、Tk 連携（``ImageTk.PhotoImage`` ラップ）は
呼び出し側の UI 層で行う。これにより同じ見た目をヘッドレスでも再現できる。
"""

from PIL import Image, ImageDraw, ImageFont

# UISP/Ubiquiti 風の基調色（ノード・パス線・ハロー・距離バッジ枠で共通）。
UISP_CYAN     = (25, 181, 230)     # RGB
UISP_CYAN_HEX = "#19B5E6"
MARKER_TEXT   = "#0E7CA0"          # ラベル文字（淡色地図でも読める濃いシアン）


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


def distance_badge(text: str) -> Image.Image:
    """距離テキストを半透明の角丸ピル背景に載せたバッジ画像（RGBA）を生成する。

    淡色地図上でテキストを読みやすくするため、テキストごと PIL で描く。
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
    # 半透明の白ピル＋淡シアン枠。淡色地図でも経路線上で読める。
    d.rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=h / 2,
        fill=(255, 255, 255, 215), outline=UISP_CYAN + (255,), width=scale,
    )
    d.text((padx - l, pady - t), text, font=font, fill=MARKER_TEXT)
    return img.resize((w // scale, h // scale), Image.Resampling.LANCZOS)


def distance_text(km: float) -> str:
    """水平距離 [km] を表示用テキストに整形する（1km 未満は m 表記）。"""
    return f"{km * 1000:.0f} m" if km < 1 else f"{km:.2f} km"
