# pyright: reportMissingImports=false
"""Field の台座＝GNSS アンテナ JCA228F と箱 EXPE7-4-6（縦置き）を載せ、カメラ三脚のクイックシューに付ける板。

製品の外の venv で回す（manifold3d と numpy だけ・B-rep 系の OCP はこの PC のアプリ制御で DLL が止められる）:
    D:\\tools\\cadvenv\\Scripts\\python.exe tools\\field_mount\\field_mount.py [出力先]
出力＝field_mount.stl（JLC3DP へ出す）と field_mount_drawing.svg（寸法の入った図・断面はモデルを切って描く）。
はめ合いの検査＝相手の部品を箱で包んだ形とモデルの重なりの体積を数え、0 でなければ止める。

座標＝原点は 5/8 の棒の軸と台座の上面の交点。台座の上面＝ARP の面＝箱の背面が載る面。
x＝GNSS→箱（素子）の向き・z＝上・y＝右手系（-y の側にクイックシューのレバー）。単位は mm。
"""

from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, JoinType, Manifold

SEG = 96

# ---- 相手の部品（出典つき・⚠️ は実物で当てる値）---------------------------------------------
GNSS_FOOT_R = 18.0        # ⚠️ JCA228F の底の脚 約 Φ36（図の画素から）
GNSS_RIM_R = 75.75        # JCA228F Φ151.5
GNSS_RIM_Z = 37.0         # ⚠️ 縁の下面の高さ（ARP から・図の画素から）
GNSS_TOP_Z = 66.5         # JCA228F 高さ
TNC_X = -35.0             # ⚠️ TNC の中心（脚の外・中心から約 35・下向き）
TNC_TIP_Z = 8.5           # ⚠️ TNC の先端は ARP の面の約 8.5 上
TNC_PLUG_R = 7.5          # ⚠️ ケーブル側の TNC プラグの太さ Φ15 以下と置く
ROD_R = 15.875 / 2        # 5/8-11UNC
ROD_IN_ANT = 14.0         # アンテナの穴は深さ 16±0.5・底に空気抜き＝14 で止める
JAM_AC = 27.5             # 5/8-11 薄型（ジャム）ナット 二面幅 23.8（15/16"）の対角
JAM_H = 9.53              # 同 厚さ 3/8"
QR_X = (29.0, 81.0)       # ⚠️ クイックシューの板 200PL-14＝52×42×10（販売店の表記）・中心 x 55
QR_HW = 21.0
QR_SCREW_UP = 4.7         # 板の 1/4 ねじの突き出し（実測）
NUT14_AF = 11.11          # 1/4-20 六角ナット 二面幅 7/16"
NUT14_H = 5.56            # 同 厚さ 7/32"
BOX_X = (82.0, 126.0)     # 箱 EXPE7-4-6 の枠 44（x）×69（y）・背面を下に
BOX_HW = 34.5
BODY_SHOULDER = 4.5       # 胴 60×35＝枠の肩が各辺 4.5
FRAME_H = 12.0            # 枠（コーナーガード）の高さ
BOX_H = 60.0              # 縦置きの全高（パネル間 46＋枠）
WIN_HX, WIN_HY = 15.0, 22.5   # ⚠️ 背面の枠の窓 約 30（x）×45（y）
USB_PLUG = (12.0, 6.8)    # UGREEN の USB-C の胴（実測）

# ---- 台座 -----------------------------------------------------------------------------------
T = 8.0                   # 板の厚さ（上面 z=0）
X0, X1 = -50.0, 139.0     # 長さ
W2 = 38.8                 # 半幅（＝受けの壁の外面）
BOSS_R, BOSS_H = 22.0, 8.0          # 棒のまわりの下への膨らみ（板の下面から）
ROD_HOLE_D = 16.4
JAM_POCKET_D, JAM_POCKET_H = 29.0, 11.0
TNC_SLOT_HW, TNC_SLOT_END = 8.5, -32.0   # TNC の逃げ＝幅 17・丸い端の中心 x
QR_CX = 55.0
TRIPOD_HOLE_D = 6.8
NUT14_POCKET_AF, NUT14_FLOOR = 11.4, 1.2
NOTCH_X, NOTCH_Y = (33.0, 77.0), -23.0   # レバーの側の切り欠き
VIAL_C, VIAL_D, VIAL_H = (50.0, 22.0), 15.4, 4.0
C = 0.3                   # 箱とのすきま
WALL = 4.0
LIP_Z0 = FRAME_H + 0.6    # 返しの下面（枠の上面から 0.6 上）
WALL_H = LIP_Z0 + 4.0
LIP_IN = BODY_SHOULDER - 0.5        # 返しの出（胴とのすきま 0.5）
SLOT_HW = 15.0            # USB の溝（+x の端へ抜ける・箱の向きがどちらでも入る幅）
STOP_C = (131.5, 27.0)    # 止めのつまみねじ M3（頭 Φ10 が箱の面に当たる位置）
M3_HOLE_D, M3_NUT_AF, M3_NUT_H = 3.4, 5.8, 2.6
TIE = (4.5, 2.0)          # 結束バンドの穴（x × y）
TIE_USB = (134.5, 17.5)
TIE_GNSS = (-44.0, 12.5)

RX0 = BOX_X[0] - C - WALL           # 受けの -x の壁の外面
RIN = BOX_HW + C                    # 受けの内面（y）


def block(x0, x1, y0, y1, z0, z1) -> Manifold:
    return Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def cyl(cx, cy, r, z0, z1, seg=SEG) -> Manifold:
    return Manifold.cylinder(z1 - z0, r, r, seg).translate((cx, cy, z0))


def hexagon(cx, cy, af, z0, z1) -> Manifold:
    return CrossSection.circle(af / math.sqrt(3), 6).extrude(z1 - z0).translate((cx, cy, z0))


def rrect(x0, x1, y0, y1, r) -> CrossSection:
    return CrossSection.square((x1 - x0 - 2 * r, y1 - y0 - 2 * r)).translate((x0 + r, y0 + r)).offset(
        r, JoinType.Round, circular_segments=SEG)


def plan() -> CrossSection:
    p = rrect(X0, X1, -W2, W2, 5.0)
    p = p - rrect(NOTCH_X[0], NOTCH_X[1], -W2 - 10, NOTCH_Y, 3.0)
    slot = CrossSection.square((TNC_SLOT_END - X0 + 1, 2 * TNC_SLOT_HW)).translate((X0 - 1, -TNC_SLOT_HW))
    p = p - (slot + CrossSection.circle(TNC_SLOT_HW, SEG).translate((TNC_SLOT_END, 0)))
    return p.offset(-1, JoinType.Round, circular_segments=SEG).offset(1, JoinType.Round, circular_segments=SEG)


def arrow(x_tail, x_tip, y_face, sign) -> Manifold:
    """側面に刻む向きの矢印（GNSS→箱）。深さ 0.8。"""
    head = x_tip - 10
    cs = CrossSection([[(x_tail, -4.75), (head, -4.75), (head, -6.5), (x_tip, -4.0),
                        (head, -1.5), (head, -3.25), (x_tail, -3.25)]])
    m = cs.extrude(0.9).rotate((90, 0, 0))            # y∈[-0.9, 0]
    return m.translate((0, y_face + 0.1, 0)) if sign > 0 else m.translate((0, y_face + 0.8, 0))


def build() -> Manifold:
    pl = plan()
    m = pl.extrude(T).translate((0, 0, -T))
    m = m + cyl(0, 0, BOSS_R, -T - BOSS_H, -T + 0.1)
    # 箱の受け＝三方の壁と返し（+x は口）
    walls = (block(RX0, X1, RIN, W2, -1, WALL_H) + block(RX0, X1, -W2, -RIN, -1, WALL_H)
             + block(RX0, RX0 + WALL, -W2, W2, -1, WALL_H)
             + block(RX0, X1, RIN - LIP_IN - 0.3, RIN, LIP_Z0, WALL_H)
             + block(RX0, X1, -RIN, -(RIN - LIP_IN - 0.3), LIP_Z0, WALL_H)
             + block(RX0, BOX_X[0] + LIP_IN, -RIN, RIN, LIP_Z0, WALL_H))
    m = m + (walls ^ pl.extrude(WALL_H + 2).translate((0, 0, -1)))
    cut = [
        cyl(0, 0, ROD_HOLE_D / 2, -40, 40),
        cyl(0, 0, JAM_POCKET_D / 2, -JAM_POCKET_H, 1),
        cyl(QR_CX, 0, TRIPOD_HOLE_D / 2, -40, 1),
        hexagon(QR_CX, 0, NUT14_POCKET_AF, -T + NUT14_FLOOR, 1),
        cyl(VIAL_C[0], VIAL_C[1], VIAL_D / 2, -VIAL_H, 1),
        block(BOX_X[0] + 22 - WIN_HX, BOX_X[0] + 22 + WIN_HX, -WIN_HY, WIN_HY, -40, 0.5),
        block(BOX_X[0] + 22 - WIN_HX, X1 + 1, -SLOT_HW, SLOT_HW, -40, 0.5),
    ]
    for s in (1, -1):
        cut += [cyl(STOP_C[0], s * STOP_C[1], M3_HOLE_D / 2, -40, 1),
                hexagon(STOP_C[0], s * STOP_C[1], M3_NUT_AF, -T - 1, -T + M3_NUT_H),
                block(TIE_USB[0] - TIE[0] / 2, TIE_USB[0] + TIE[0] / 2,
                      s * TIE_USB[1] - TIE[1] / 2, s * TIE_USB[1] + TIE[1] / 2, -40, 1),
                block(TIE_GNSS[0] - TIE[0] / 2, TIE_GNSS[0] + TIE[0] / 2,
                      s * TIE_GNSS[1] - TIE[1] / 2, s * TIE_GNSS[1] + TIE[1] / 2, -40, 1)]
    cut += [arrow(-10, 60, W2, +1), arrow(-40, 25, -W2, -1)]
    for c in cut:
        m = m - c
    return m


# ---- はめ合いの検査 -------------------------------------------------------------------------
def envelopes() -> list[tuple[str, Manifold]]:
    """相手の部品を包む形。台座と重なれば当たる。"""
    bx0, bx1 = BOX_X
    sh = BODY_SHOULDER
    env = [
        ("箱（据えた位置）", block(bx0, bx1, -BOX_HW, BOX_HW, 0, FRAME_H)
         + block(bx0 + sh, bx1 - sh, -BOX_HW + sh, BOX_HW - sh, FRAME_H, BOX_H - FRAME_H)
         + block(bx0, bx1, -BOX_HW, BOX_HW, BOX_H - FRAME_H, BOX_H)),
        ("箱を +x から滑り込ませる道", block(bx0, 400, -BOX_HW, BOX_HW, 0, FRAME_H)
         + block(bx0 + sh, 400, -BOX_HW + sh, BOX_HW - sh, FRAME_H, BOX_H)),
        ("USB-C の胴とケーブル（溝を通る道・箱がどちら向きでも）",
         block(bx0 + 22 - 6, 400, -7 - USB_PLUG[0] / 2 - 0.5, 7 + USB_PLUG[0] / 2 + 0.5, -40, 0)),
        ("GNSS の脚と縁", cyl(0, 0, GNSS_FOOT_R, 0, GNSS_RIM_Z) + cyl(0, 0, GNSS_RIM_R, GNSS_RIM_Z, GNSS_TOP_Z)),
        ("TNC プラグ（中心 r 32〜41.5 のどこでも）",
         cyl(TNC_X, 0, TNC_PLUG_R, -45, TNC_TIP_Z) + cyl(-32, 0, TNC_PLUG_R, -45, TNC_TIP_Z)
         + cyl(-41.5, 0, TNC_PLUG_R, -45, TNC_TIP_Z)),
        ("5/8 の棒", cyl(0, 0, ROD_R, -40, 0)),
        ("5/8 の薄ナット", cyl(0, 0, JAM_AC / 2, -JAM_H, 0)),
        ("クイックシューの板", block(QR_X[0], QR_X[1], -QR_HW, QR_HW, -T - 10, -T)),
        ("板の 1/4 ねじ", cyl(QR_CX, 0, 3.2, -T, -T + QR_SCREW_UP)),
        ("1/4-20 ナット", hexagon(QR_CX, 0, NUT14_AF, -T + NUT14_FLOOR, -T + NUT14_FLOOR + NUT14_H)),
        ("レバーへ伸ばす指", block(NOTCH_X[0] + 2, NOTCH_X[1] - 2, -W2 - 5, NOTCH_Y - 0.5, -T - 20, 0)),
    ]
    return env


def check(m: Manifold) -> list[str]:
    out = []
    bad = []
    for name, e in envelopes():
        v = (m ^ e).volume()
        out.append(f"  {'OK' if v < 1e-6 else '当たる'}  {name}（重なり {v:.3f} mm³）")
        if v >= 1e-6:
            bad.append(name)
    # 返しが枠の肩に掛かるか（持ち上がらない）
    lip = m ^ block(BOX_X[0], BOX_X[1], -BOX_HW, BOX_HW, 0, FRAME_H + 0.5)
    out.append(f"  {'OK' if lip.volume() < 1e-6 else '当たる'}  返しと枠の上面のすきま 0.5 以上")
    hold = m ^ block(BOX_X[0], BOX_X[1], -BOX_HW, BOX_HW, FRAME_H, FRAME_H + 5)
    out.append(f"  {'OK' if hold.volume() > 0 else '掛からない'}  返しが枠の肩の上にある（体積 {hold.volume():.0f} mm³）")
    if lip.volume() >= 1e-6 or hold.volume() <= 0:
        bad.append("返し")
    # 相手の部品どうし（台座と関係なく当たる組）
    env = dict(envelopes())
    v = (env["箱（据えた位置）"] ^ env["GNSS の脚と縁"]).volume()
    out.append(f"  {'OK' if v < 1e-6 else '当たる'}  箱と GNSS の縁（重なり {v:.3f} mm³）")
    if v >= 1e-6:
        bad.append("箱と GNSS の縁")
    # 長さで決まるもの
    grip = QR_SCREW_UP - NUT14_FLOOR
    out.append(f"  {'OK' if grip >= 3.0 else '足りない'}  1/4 ねじがナットに掛かる長さ {grip:.1f}（3.0 以上）")
    if grip < 3.0:
        bad.append("1/4 ねじの掛かり")
    if bad:
        raise SystemExit("はめ合いの検査で止めた: " + "・".join(bad) + "\n" + "\n".join(out))
    return out


# ---- STL -----------------------------------------------------------------------------------
def write_stl(m: Manifold, path: Path) -> int:
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    t = np.asarray(mesh.tri_verts, dtype=np.int64)
    tri = v[t]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-30
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, tri
    with open(path, "wb") as f:
        f.write(b"RadioSim Field mount (mm)".ljust(80, b" "))
        f.write(struct.pack("<I", len(t)))
        f.write(rec.tobytes())
    return len(t)


# ---- 図（SVG・A3 横・1:1）------------------------------------------------------------------
class View:
    def __init__(self, ox, oy, s=1.0, flip_u=False):
        self.ox, self.oy, self.s, self.fu = ox, oy, s, flip_u

    def p(self, u, v):
        return (self.ox + (-u if self.fu else u) * self.s, self.oy - v * self.s)


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Sheet:
    def __init__(self):
        self.el: list[str] = []

    def path(self, view: View, polys, cls):
        d = []
        for poly in polys:
            pts = [view.p(float(a), float(b)) for a, b in poly]
            d.append("M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in pts) + " Z")
        if d:
            self.el.append(f'<path class="{cls}" d="{" ".join(d)}"/>')

    def polyline(self, view: View, pts, cls):
        d = " L".join(f"{x:.3f},{y:.3f}" for x, y in (view.p(a, b) for a, b in pts))
        self.el.append(f'<path class="{cls}" d="M{d}"/>')

    def line(self, x1, y1, x2, y2, cls="thin"):
        self.el.append(f'<line class="{cls}" x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}"/>')

    def text(self, x, y, s, cls="t", rot=0, anchor="start"):
        tr = f' transform="rotate({rot} {x:.3f} {y:.3f})"' if rot else ""
        self.el.append(f'<text class="{cls}" x="{x:.3f}" y="{y:.3f}" text-anchor="{anchor}"{tr}>{esc(s)}</text>')

    def circle(self, x, y, r, cls):
        self.el.append(f'<circle class="{cls}" cx="{x:.3f}" cy="{y:.3f}" r="{r:.3f}"/>')

    def rect(self, view: View, u0, u1, v0, v1, cls):
        self.path(view, [[(u0, v0), (u1, v0), (u1, v1), (u0, v1)]], cls)

    def dim_h(self, view: View, u1, u2, v, off, label):
        """水平の寸法（u1〜u2・v の高さの点から off だけ離して引く）。"""
        (x1, y1), (x2, _) = view.p(u1, v), view.p(u2, v)
        y = y1 - off
        self.line(x1, y1 - math.copysign(1, off), x1, y - math.copysign(1.5, off))
        self.line(x2, y1 - math.copysign(1, off), x2, y - math.copysign(1.5, off))
        self.el.append(f'<line class="dim" x1="{x1:.3f}" y1="{y:.3f}" x2="{x2:.3f}" y2="{y:.3f}" '
                       'marker-start="url(#a)" marker-end="url(#a)"/>')
        self.text((x1 + x2) / 2, y - 0.8, label, "d", anchor="middle")

    def dim_v(self, view: View, v1, v2, u, off, label):
        (x1, y1), (_, y2) = view.p(u, v1), view.p(u, v2)
        x = x1 + off
        self.line(x1 + math.copysign(1, off), y1, x + math.copysign(1.5, off), y1)
        self.line(x1 + math.copysign(1, off), y2, x + math.copysign(1.5, off), y2)
        self.el.append(f'<line class="dim" x1="{x:.3f}" y1="{y1:.3f}" x2="{x:.3f}" y2="{y2:.3f}" '
                       'marker-start="url(#a)" marker-end="url(#a)"/>')
        self.text(x - 0.8, (y1 + y2) / 2, label, "d", rot=-90, anchor="middle")

    def callout(self, view: View, u, v, n):
        x, y = view.p(u, v)
        self.circle(x, y, 2.3, "co")
        self.text(x, y + 1.0, str(n), "con", anchor="middle")

    def svg(self, w=420, h=297) -> str:
        style = """
  text{font-family:'Yu Gothic','Meiryo',sans-serif;fill:#111}
  .t{font-size:2.6px}.d{font-size:2.3px}.h{font-size:4.2px;font-weight:bold}.h2{font-size:3.2px;font-weight:bold}
  .n{font-size:2.35px}.con{font-size:2.6px;font-weight:bold;fill:#fff}
  .out{fill:#e8eef5;stroke:#111;stroke-width:0.35}.edge{fill:none;stroke:#111;stroke-width:0.25}
  .hid{fill:none;stroke:#555;stroke-width:0.2;stroke-dasharray:1.2 0.8}
  .sec{fill:url(#hatch);stroke:#111;stroke-width:0.35;fill-rule:evenodd}
  .ghost{fill:none;stroke:#2a6fb0;stroke-width:0.3;stroke-dasharray:2 1}
  .gtxt{font-size:2.3px;fill:#2a6fb0}
  .thin{stroke:#111;stroke-width:0.13}.dim{stroke:#111;stroke-width:0.15}
  .cl{stroke:#c33;stroke-width:0.15;stroke-dasharray:4 1 0.8 1}
  .co{fill:#c33;stroke:none}.frame{fill:none;stroke:#111;stroke-width:0.5}"""
        defs = ('<defs><marker id="a" viewBox="0 0 6 6" refX="3" refY="3" markerWidth="2.2" markerHeight="2.2" '
                'orient="auto-start-reverse"><path d="M0,0 L6,3 L0,6 Z" fill="#111"/></marker>'
                '<pattern id="hatch" width="1.6" height="1.6" patternUnits="userSpaceOnUse" '
                'patternTransform="rotate(45)"><rect width="1.6" height="1.6" fill="#f3f3f3"/>'
                '<line x1="0" y1="0" x2="0" y2="1.6" stroke="#333" stroke-width="0.25"/></pattern></defs>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">'
                f'<style>{style}</style>{defs}<rect width="{w}" height="{h}" fill="#fff"/>'
                + "\n".join(self.el) + "</svg>\n")


def polys(cs: CrossSection):
    return [np.asarray(p) for p in cs.to_polygons()]


def section_y0(m: Manifold):     # y=0 で切る → (x, z)
    return polys(m.rotate((-90, 0, 0)).slice(0.0))


def section_x(m: Manifold, x: float):   # x で切る → (y, z)
    return [np.column_stack([p[:, 1], p[:, 0]]) for p in polys(m.rotate((0, 90, 0)).slice(-x))]


def side_silhouette(m: Manifold):   # -y から見た外形 → (x, z)
    return polys(m.rotate((-90, 0, 0)).project())


def drawing(m: Manifold, mass_g: float, checks: list[str]) -> str:
    sh = Sheet()
    sh.el.append('<rect class="frame" x="5" y="5" width="410" height="287"/>')
    sh.text(12, 14, "RadioSim Field 台座（GNSS JCA228F＋箱 EXPE7-4-6 縦置き・クイックシュー 200PL-14）", "h")
    sh.text(12, 19.5, "尺度 1:1（A3 横で 100% 印刷すると実物大）・単位 mm・原点＝5/8 の棒の軸 × 台座の上面（＝ARP の面＝箱の背面が載る面）"
            "・x＝GNSS→箱の向き", "t")

    # 平面図
    top = View(30 - X0, 76)
    sh.text(X1 - X0 + 40, 40, "平面図（上から）", "h2")
    sh.path(top, polys(m.project()), "out")
    sh.path(top, polys(m.slice(-0.01)), "edge")
    sh.path(top, polys(m.slice(LIP_Z0 + 1)), "edge")
    sh.path(top, polys(m.slice(-T - BOSS_H / 2)), "hid")
    for u0, u1, v0, v1 in [(*QR_X, -QR_HW, QR_HW)]:
        sh.rect(top, u0, u1, v0, v1, "ghost")
    x, y = top.p(QR_X[1] - 1, -QR_HW + 1.5)
    sh.text(x, y, "クイックシューの板（下・52×42）", "gtxt", anchor="end")
    for side in (1, -1):    # GNSS の縁は台座の幅の中だけ描く
        ys = np.linspace(-W2 - 2, W2 + 2, 60)
        sh.polyline(top, [(side * math.sqrt(GNSS_RIM_R ** 2 - v * v), v) for v in ys], "ghost")
    sh.rect(top, *BOX_X, -BOX_HW, BOX_HW, "ghost")
    sh.line(*top.p(X0 - 3, 0), *top.p(X1 + 3, 0), "cl")
    for cx in (0, QR_CX, sum(BOX_X) / 2):
        sh.line(*top.p(cx, -W2 - 3), *top.p(cx, W2 + 3), "cl")
    # 縦座標（x の位置）
    ords = [X0, TNC_SLOT_END, 0, NOTCH_X[0], QR_CX, NOTCH_X[1], RX0, BOX_X[0], sum(BOX_X) / 2, BOX_X[1], STOP_C[0], X1]
    yl = top.p(0, W2 + 9)[1]
    for u in ords:
        x, y0 = top.p(u, W2 + 0.8)
        sh.line(x, y0, x, yl + 0.5)
        sh.text(x + 0.8, yl - 0.3, f"{u:g}", "d", rot=-90)
    sh.line(top.p(X0, 0)[0], yl + 0.5, top.p(X1, 0)[0], yl + 0.5)
    sh.text(top.p(X1, 0)[0] + 2, yl + 0.5, "x の位置（原点から）", "d")
    sh.dim_v(top, -W2, W2, X1, 6, f"{2 * W2:g}")
    sh.dim_v(top, NOTCH_Y, -W2, NOTCH_X[1] - 6, -0.01, "")
    x, y = top.p(NOTCH_X[0] + 1.5, NOTCH_Y - 3)
    sh.text(x, y, f"y {NOTCH_Y:g} まで", "d")
    x, y = top.p(VIAL_C[0] + VIAL_D / 2 + 1, VIAL_C[1] + 1)
    sh.text(x, y, f"({VIAL_C[0]:g}, {VIAL_C[1]:g})", "d")
    for n, (u, v) in enumerate([(0, 0), (TNC_SLOT_END - 9, 0), (TIE_GNSS[0], -TIE_GNSS[1] - 4),
                                (QR_CX, 0), (sum(NOTCH_X) / 2, -W2 + 4), VIAL_C,
                                (BOX_X[0] + 4, BOX_HW - 8), (sum(BOX_X) / 2, 0), (STOP_C[0], STOP_C[1]),
                                (TIE_USB[0], -TIE_USB[1] - 4.5), (25, -W2 + 1.6)], start=1):
        sh.callout(top, u + (7 if n in (1, 4, 6) else 0), v + (7 if n in (1, 4) else 0), n)
    sh.text(X1 - X0 + 40, 45, "破線の青＝相手の部品", "gtxt")
    sh.text(X1 - X0 + 40, 48.5, "（GNSS の縁 Φ151.5・箱の枠 44×69・板）", "gtxt")

    # 底面図
    bot = View(30 - X0, 166)
    sh.text(30, 124, "底面図（下から・y は裏返し）", "h2")
    flip = lambda ps: [np.column_stack([p[:, 0], -p[:, 1]]) for p in ps]  # noqa: E731
    sh.path(bot, flip(polys(m.project())), "out")
    sh.path(bot, flip(polys(m.slice(-T + 0.01))), "edge")
    sh.path(bot, flip(polys(m.slice(-T - BOSS_H + 0.01))), "edge")
    sh.rect(bot, *QR_X, -QR_HW, QR_HW, "ghost")
    sh.line(*bot.p(X0 - 3, 0), *bot.p(X1 + 3, 0), "cl")
    sh.dim_h(bot, -BOSS_R, BOSS_R, -W2, -6, f"膨らみ Φ{2 * BOSS_R:g}（下へ {BOSS_H:g}）")
    sh.dim_h(bot, BOX_X[0] + 22 - WIN_HX, BOX_X[0] + 22 + WIN_HX, -W2, -6, f"窓 {2 * WIN_HX:g}")
    sh.dim_h(bot, X0, X1, -W2, -13, f"全長 {X1 - X0:g}")
    sh.dim_v(bot, -SLOT_HW, SLOT_HW, X1, 5, f"溝 {2 * SLOT_HW:g}")
    sh.dim_v(bot, -WIN_HY, WIN_HY, X1, 12, f"窓 {2 * WIN_HY:g}")
    x, y = bot.p(QR_X[0] + 1, QR_HW - 3)
    sh.text(x, y, "ここに 1/4 ねじ（x 55）・ナットは上から", "gtxt")

    # 断面 A-A
    aa = View(30 - X0, 254)
    sh.text(30, 232, "断面 A-A（y=0・-y の側から見る＝箱の下は窓と溝で抜けている）", "h2")
    sh.path(aa, section_y0(m), "sec")
    sh.line(*aa.p(X0 - 3, 0), *aa.p(X1 + 3, 0), "cl")
    sh.text(aa.p(X1 + 4, 0)[0], aa.p(0, 0)[1] + 0.8, "z=0（ARP・箱の背面）", "d")
    sh.dim_v(aa, -T, 0, X0, -4, f"{T:g}")
    sh.dim_v(aa, -T - BOSS_H, 0, -BOSS_R, -13, f"{T + BOSS_H:g}")
    sh.dim_v(aa, -JAM_POCKET_H, 0, JAM_POCKET_D / 2, 6, f"{JAM_POCKET_H:g}")
    sh.dim_h(aa, -JAM_POCKET_D / 2, JAM_POCKET_D / 2, 0, 5, f"Φ{JAM_POCKET_D:g}")
    sh.dim_h(aa, -ROD_HOLE_D / 2, ROD_HOLE_D / 2, -T - BOSS_H, -4, f"Φ{ROD_HOLE_D:g} 貫通")
    sh.dim_v(aa, -T, -T + NUT14_FLOOR, QR_CX + 6, 4, "")
    x, y = aa.p(QR_CX + 11, -T - 4)
    sh.text(x, y, f"底の肉 {NUT14_FLOOR:g}・六角の穴 二面幅 {NUT14_POCKET_AF:g}", "d")
    sh.dim_v(aa, 0, WALL_H, BOX_X[0] - C - WALL, -4, f"{WALL_H:g}")
    sh.dim_v(aa, 0, LIP_Z0, BOX_X[0] + LIP_IN, 3, f"{LIP_Z0:g}")
    sh.dim_h(aa, RX0, BOX_X[0] + LIP_IN, WALL_H, 4, f"{BOX_X[0] + LIP_IN - RX0:g}")
    sh.dim_h(aa, X0, TNC_SLOT_END + TNC_SLOT_HW, -T, -9, "TNC の逃げ（y=0 では切れている）")

    # 断面 B-B（箱の中心）
    bb = View(330, 62)
    xb = sum(BOX_X) / 2
    sh.text(285, 27, f"断面 B-B（x={xb:g}＝箱の中心・+x から見る）", "h2")
    sh.path(bb, section_x(m, xb), "sec")
    sh.rect(bb, -BOX_HW, BOX_HW, 0, FRAME_H, "ghost")
    sh.rect(bb, -BOX_HW + BODY_SHOULDER, BOX_HW - BODY_SHOULDER, FRAME_H, 18, "ghost")
    sh.line(*bb.p(-W2 - 3, 0), *bb.p(W2 + 3, 0), "cl")
    sh.dim_h(bb, -RIN, RIN, WALL_H, 12, f"内のり {2 * RIN:g}（枠 69＋0.6）")
    sh.dim_h(bb, -(RIN - LIP_IN - 0.3), RIN - LIP_IN - 0.3, WALL_H, 6, f"返しの間 {2 * (RIN - LIP_IN - 0.3):g}（胴 60）")
    sh.dim_h(bb, -W2, W2, -T, -6, f"{2 * W2:g}")
    sh.dim_h(bb, -SLOT_HW, SLOT_HW, -T, -11, f"USB の溝 {2 * SLOT_HW:g}")
    sh.dim_v(bb, 0, LIP_Z0, W2, 4, f"{LIP_Z0:g}")
    sh.dim_v(bb, 0, WALL_H, W2, 10, f"{WALL_H:g}")
    sh.dim_v(bb, -T, 0, -W2, -4, f"{T:g}")

    # 組み立て図（1:2）
    el = View(300, 150, s=0.5)
    sh.text(262, 103, "組み立て図（1:2・-y の側から）", "h2")
    sh.path(el, side_silhouette(m), "out")
    g = [(-GNSS_FOOT_R, 0), (GNSS_FOOT_R, 0), (GNSS_FOOT_R, 25), (GNSS_RIM_R, GNSS_RIM_Z), (GNSS_RIM_R, 52),
         (55, GNSS_TOP_Z), (-55, GNSS_TOP_Z), (-GNSS_RIM_R, 52), (-GNSS_RIM_R, GNSS_RIM_Z), (-GNSS_FOOT_R, 25)]
    sh.path(el, [np.array(g)], "ghost")
    sh.rect(el, TNC_X - TNC_PLUG_R, TNC_X + TNC_PLUG_R, -30, TNC_TIP_Z, "ghost")
    sh.rect(el, -ROD_R, ROD_R, -T - BOSS_H - 22, ROD_IN_ANT, "ghost")
    sh.rect(el, -16, 16, -T - BOSS_H - 3, -T - BOSS_H, "ghost")
    sh.rect(el, -14, 14, -T - BOSS_H - 14, -T - BOSS_H - 3, "ghost")
    sh.rect(el, *QR_X, -T - 10, -T, "ghost")
    sh.rect(el, *BOX_X, 0, FRAME_H, "ghost")
    sh.rect(el, BOX_X[0] + BODY_SHOULDER, BOX_X[1] - BODY_SHOULDER, FRAME_H, BOX_H - FRAME_H, "ghost")
    sh.rect(el, *BOX_X, BOX_H - FRAME_H, BOX_H, "ghost")
    sh.line(*el.p(xb, BOX_H), *el.p(xb, BOX_H + 22), "cl")
    sh.line(*el.p(X0 - 4, 0), *el.p(X1 + 6, 0), "cl")
    sh.text(el.p(X1 + 7, 0)[0], el.p(0, 0)[1] + 0.8, "z=0", "d")
    for (u, v, s, a) in [(0, GNSS_TOP_Z + 3, "JCA228F（略形）", "middle"), (xb, BOX_H + 24, "素子（真上・z は机で 1 回測る）", "middle"),
                         (TNC_X - 10, -27, "TNC", "end"), (0, -T - BOSS_H - 27, "5/8-11 の棒 50・座金・蝶ナット", "middle"),
                         (QR_X[1] + 3, -T - 8, "200PL-14", "start"), (xb, 30, "箱", "middle")]:
        x, y = el.p(u, v)
        sh.text(x, y, s, "gtxt", anchor=a)
    sh.dim_v(el, 0, BOX_H, BOX_X[1], 4, f"{BOX_H:g}")
    sh.dim_v(el, 0, GNSS_RIM_Z, -GNSS_RIM_R, -4, f"約 {GNSS_RIM_Z:g}")

    # 注記
    nx, ny = 250, 176
    lines = [
        ("h2", "番号の中身"),
        ("n", f"① 5/8-11 の棒の穴 Φ{ROD_HOLE_D:g} 貫通。上から薄ナットの座ぐり Φ{JAM_POCKET_D:g}・深さ {JAM_POCKET_H:g}"),
        ("n", f"② TNC の逃げ 幅 {2 * TNC_SLOT_HW:g}（プラグ Φ15 以下・中心 r 32〜41.5 で入る）"),
        ("n", "③ GNSS ケーブルの結束穴 2×4.5（2 個）"),
        ("n", f"④ 三脚の 1/4-20：穴 Φ{TRIPOD_HOLE_D:g}・上から六角ナット（二面幅 {NUT14_POCKET_AF:g}）"),
        ("n", f"⑤ レバーの切り欠き x {NOTCH_X[0]:g}〜{NOTCH_X[1]:g}・y {NOTCH_Y:g} まで（-y の側）"),
        ("n", f"⑥ 丸水準器 Φ15 の凹み Φ{VIAL_D:g}・深さ {VIAL_H:g}（接着）"),
        ("n", f"⑦ 箱の受け：壁 {WALL:g}・高さ {WALL_H:g}・返し z {LIP_Z0:g}〜{WALL_H:g}（+x は口）"),
        ("n", f"⑧ 床の窓 {2 * WIN_HX:g}×{2 * WIN_HY:g}＋USB の溝 幅 {2 * SLOT_HW:g}（+x の端へ抜ける）"),
        ("n", f"⑨ 止めのつまみねじ M3（穴 Φ{M3_HOLE_D:g}・下から M3 ナット 二面幅 {M3_NUT_AF:g}）"),
        ("n", "⑩ USB ケーブルの結束穴 2×4.5（2 個）"),
        ("n", "⑪ 向きの矢印（両側面の刻み 0.8）＝GNSS→箱"),
        ("h2", "作り方と部品"),
        ("n", f"JLC3DP・MJF PA12（素地）・この STL のまま・約 {mass_g:.0f} g"),
        ("n", "5/8-11 全ねじ 50（14 をアンテナへ・薄ナットで締めて留める）"),
        ("n", "5/8 の座金（外径 32 前後）＋蝶ナット＝下から締めて引き付ける"),
        ("n", "1/4-20 六角ナット 1・M3 ローレットつまみねじ×10 と M3 ナット 各 2"),
        ("h2", "実物で当てる値（外れたら直して出し直す）"),
        ("n", "JCA228F の脚 Φ36・TNC の位置 r 35・縁の下面 37（図の画素）"),
        ("n", "TNC プラグの太さ・200PL-14 の 52×42・枠の窓 30×45"),
        ("h2", "はめ合いの自動検査（モデルとの重なり）"),
    ] + wrap_checks(checks)
    for cls, s in lines:
        if cls == "h2":
            ny += 1.2
        sh.text(nx, ny, s, cls)
        ny += 4.4 if cls == "h2" else 3.35
    return sh.svg()


def wrap_checks(checks: list[str], width: int = 52) -> list[tuple[str, str]]:
    """検査の結果を短く畳む（重なりの数値は端末の出力に残す）。"""
    names = [c.strip().split(None, 1) for c in checks]
    ng = [n for ok, n in names if ok != "OK"]
    head = f"{len(names)} 項目すべて OK：" if not ng else f"NG {len(ng)} 項目："
    body = "・".join(n.split("（重なり")[0] for _, n in names)
    text = head + body
    return [("n", text[i:i + width]) for i in range(0, len(text), width)]


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:\dev\field-data\mount")
    out.mkdir(parents=True, exist_ok=True)
    m = build()
    if m.status().name != "NoError":
        raise SystemExit(f"モデルが閉じていない: {m.status()}")
    checks = check(m)
    vol_cm3 = m.volume() / 1000
    mass = vol_cm3 * 1.01
    bb = m.bounding_box()
    ntri = write_stl(m, out / "field_mount.stl")
    (out / "field_mount_drawing.svg").write_text(drawing(m, mass, checks), encoding="utf-8")
    print(f"外形 x {bb[0]:.2f}〜{bb[3]:.2f}  y {bb[1]:.2f}〜{bb[4]:.2f}  z {bb[2]:.2f}〜{bb[5]:.2f}")
    print(f"体積 {vol_cm3:.1f} cm³（PA12 で約 {mass:.0f} g）・三角形 {ntri}・穴の数（genus）{m.genus()}")
    print("\n".join(checks))
    print(f"出力 → {out}")


if __name__ == "__main__":
    main()
