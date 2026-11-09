"""
map_graphics.py
===============
地図オーバーレイ描画の純 PIL 実装（tkinter 非依存）。

地図の画像の生成（`report_map.py`・ヘッドレス）が使う単一ソース（旧画面の
マップウィンドウ〔Link 1.0 で撤去〕とも共有していた）。ここでは PIL の ``Image`` を
返すだけにとどめ、画面への載せ方は呼び出し側で行う。判定の色（`STATUS_RGB`）は
新しい画面の CSS と Pro の帳票もここに合わせる。
"""

import math

from PIL import Image, ImageDraw, ImageFont

# 地図表現の基調色（ノード・パス線・ハロー・距離バッジ枠で共通）。淡色地図と
# 航空写真のどちらの上でも沈まず、判定色（緑/赤）とも衝突しないシアン系を選んだ。
MAP_CYAN     = (25, 181, 230)     # RGB
MARKER_TEXT   = "#0E7CA0"          # ラベル文字（淡色地図でも読める濃いシアン）

# 判定ステータス別の経路色。台帳の判定の字（.s-ok / .s-ng / .s-err）とカードの
# 数字も同じ色を使い、全パス地図の線と表の行が同じ色で対応づくようにする
# （色の定義は HTML/PIL で二重管理せずここを単一ソースとする）。
# ⚠️ **帳票の CSS（Pro）は同じ色を `STATUS_HEX` で引く**＝以前は
# 「単一ソース」と書きながら CSS が色を直に書いており、ERROR だけ #e65100 と
# #bf360c の 2 色になっていた（B-207）。
#
# REVIEW（4.0 ステージ6・判定契約 v2＝前提で裏返る）は紫。緑・赤・ERROR の濃い橙の
# どれとも色相が離れ、色覚の型によらず緑と赤の潰れ（茶とオリーブ）から分かれる。
# 新しい画面（`webui/static/style.css`）も同じ色を使う＝Link 1.0 ステージ9d で決めた
# （ダークは旧画面の色を引き継いだ・`tests/test_webui.py` が CSS を見る）。
STATUS_RGB = {
    "OK":     (46, 125, 50),   # #2e7d32
    "NG":     (198, 40, 40),   # #c62828
    "REVIEW": (106, 27, 154),  # #6a1b9a
    "ERROR":  (191, 54, 12),   # #bf360c
}


ATTR_FG_RGB = (0x33, 0x33, 0x33)
ATTR_BG_RGB = (0xFF, 0xFF, 0xFF)

# 選択中の点を囲むリングの色（I-098＝地図で座標を置き直す）。
# **基調色のシアンにしない**＝選択は「いま何が起きるか」を表す状態で、点の
# 種別（送信点・受信点・中継点＝シアンの形で区別）とは別の軸。同じ色にすると
# 「選ばれている」ことと「そういう点である」ことが見分けられない。
# ⚠️ 判定色（緑/赤）も避ける＝地図の上で OK/NG と読まれる。
SELECT_RGB = (255, 145, 0)     # 琥珀色（淡色地図でも航空写真でも沈まない）


# 図の中に焼く文字のフォント候補。**ASCII 用と日本語用を分ける**＝出典表記
# （B-133）で初めて地図の上に日本語が乗った。Arial には仮名も漢字も無いので、
# 従来の候補だけだと「出典: 地理院タイル（淡色地図）」が**豆腐（□）で焼かれる**
# ＝表示義務を満たさない画像が出来上がる（読めない刻印は無いのと同じ）。
# ⚠️ ファイル名で引く（PIL は Windows のフォント置き場を名前で探す）＝
# matplotlib 側（Pro の `radiosim_pro/mpl_fonts.py`）は**フォント名**で引くので候補表は共有できない。
_ASCII_FONTS = ("arialbd.ttf", "arial.ttf")
_CJK_FONTS   = ("YuGothB.ttc", "YuGothM.ttc", "meiryob.ttc", "meiryo.ttc",
                "msgothic.ttc")


def load_font(px: int, text: str = "") -> "ImageFont.FreeTypeFont | ImageFont.ImageFont":
    """`text` を描けるフォントを `px` で返す（見つからなければ既定フォント）。

    **描く文字列を見て候補を選ぶ**のがこの関数の要点＝呼ぶ側が「これは日本語が
    来る欄だ」と覚えておく必要をなくす。訳を足した言語が非 ASCII でも同じ経路で
    拾える（[[feedback-writing]] の欄が増えても壊れない）。
    """
    names = (_CJK_FONTS + _ASCII_FONTS if not text.isascii()
             else _ASCII_FONTS)
    for name in names:
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _selection_ring(d: "ImageDraw.ImageDraw", c: float, radius: float,
                    scale: int) -> None:
    """選択中を表すリングを描く（白の下敷き＋琥珀の線）。

    白を 1 枚下に敷くのは、航空写真の明るい地面の上で琥珀が沈むため
    （出典表記に背景色を持たせているのと同じ理由＝地の色は選べない）。
    """
    for r_, color, w in ((radius + 1.2 * scale, (255, 255, 255, 235), 2.4),
                         (radius, SELECT_RGB + (255,), 2.2)):
        d.ellipse([c - r_, c - r_, c + r_, c + r_],
                  outline=color, width=int(w * scale))


def node_icon(hollow: bool, selected: bool = False) -> Image.Image:
    """端点のノードアイコン（RGBA PIL Image）を生成する。

    半透明シアンのハロー（電波点の表現）＋白縁取りのシアンノード。
    hollow=False（TX）は塗りつぶし、hollow=True（RX）は白抜きで区別する。
    supersample → 縮小でアンチエイリアスする。

    `selected=True` は**選択中**（次のクリックでここへ移す＝I-098）の見た目。
    ⚠️ 形も塗りも変えない＝役割（送信点／受信点）は形で読む約束なので、
    選択は**囲うだけ**で表す。
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
    if selected:
        _selection_ring(d, c, s * 0.40, scale)
    # ノード本体（白縁取りで地図上のコントラストを確保）。
    node_r = s * 0.22
    if hollow:   # RX: 白抜き（受信側）
        disc(node_r, fill=(255, 255, 255, 255),
             outline=(r, g, b, 255), width=int(2.2 * scale))
    else:        # TX: 塗り（送信側）
        disc(node_r, fill=(r, g, b, 255),
             outline=(255, 255, 255, 255), width=int(1.4 * scale))
    return img.resize((size, size), Image.Resampling.LANCZOS)


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
    font = load_font(13 * scale, text)
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
    font = load_font(int(s * 0.22), "N")
    nl_x, nl_y = c + ux * r * 1.55, c + uy * r * 1.55
    l, t, rr, bb = d.textbbox((0, 0), "N", font=font)
    d.text((nl_x - (rr - l) / 2 - l, nl_y - (bb - t) / 2 - t), "N", font=font, fill=ink)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def attribution_badge(text: str, font_px: float = 11.0,
                      scale: int = 2) -> Image.Image:
    """出典表記のバー（RGBA）を返す。**地図画像そのものへ焼き込む**ために使う。

    ⛔ **帳票側で HTML のキャプションにしない**（B-133 の対応方針）＝帳票の地図は
    画像だけを抜き出して資料へ貼られる使われ方をするので、絵と出典が別の要素だと
    **貼った先で出典が剥がれる**。焼き込めば画像が単独で義務を満たす。

    見た目は旧画面の地図の出典ラベル（4.0 で撤去）に合わせた＝同じ色・同じ右下・
    同じ文言（`ATTR_KEYS` から引いた 1 つの訳）。
    ⚠️ **ピルにしない**＝出典は地図の一部であって強調物ではない。距離バッジ
    （`pill_badge`）と同じ形にすると、読む人が「値のラベル」として拾ってしまう。

    ⚠️ **`font_px` は呼ぶ側が図の幅から決める**（B-135＝`report_common.figure_text_px`）。
    既定の 11px は**画像を等倍で見るとき**の値で、帳票のように縮めて載せる面が
    そのまま使うと読めない（実測 6.6〜7.8px まで落ちていた）。
    """
    font = load_font(round(font_px * scale), text)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    l, t, r, b = probe.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    padx, pady = 5 * scale, 3 * scale
    w, h = int(tw + padx * 2), int(th + pady * 2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 地の色は選べないので背景を持たせる（不透明＝タイルの模様が文字に混ざらない）。
    d.rectangle([0, 0, w - 1, h - 1], fill=ATTR_BG_RGB + (235,))
    d.text((padx - l, pady - t), text, font=font, fill=ATTR_FG_RGB + (255,))
    return img.resize((max(1, w // scale), max(1, h // scale)),
                      Image.Resampling.LANCZOS)
