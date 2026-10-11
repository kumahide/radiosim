"""Field のステージ2（1 回目の実測）の回線候補を、堤防をまたぐ経路から探す。

なぜ堤防か（2026-10-11）:
    回線の条件（Field の正典 §7＝回折が効く・遮蔽は地形・両端が急斜面でない・
    上空が開けている・両端に同日に入れる）のうち 4 つは**遮蔽物の種類**で決まり、
    急斜面は**端点の置き方**で決まる。大河川の堤防は「草地の土の尾根 1 本」で、
    平野部なので DEM5A（航空レーザ）があり天端の高さが確か、堤内が田畑なら端点は
    平らで上空も開ける。⇒ 遮蔽物を先に選び、端点を後から置く。

手順:
    scan   … 箱ごとに dem5a_png z15 をモザイクにし、周囲より 4 m 以上高い細い尾根
             （天端）を 100 m 格子で拾う。天端の軸に直交する経路（両端 2 m・
             片側 150/250/400 m）を作り、製品の計算コアで回折損を出してふるう。
    sheet  … ふるった候補を 800 m 以上離れた場所ごとに 1 本へまとめ、地理院の
             航空写真に経路を描いた一覧画像を作る（DEM5A は建物・樹木を除いた
             地表なので、**道路や鉄道の盛土・家並み・並木は写真でしか落とせない**）。

🔑 ふるいの条件と、その形にした理由:
    - **`single` と、補正前の Bullington（`Luc`）がほぼ一致すること**＝遮蔽が 1 枚。
      ⚠️ 製品の `bullington` そのものとは比べない＝P.526 §4.5.1 の補正項
      `L = Luc + (1 - exp(-Luc/6))(10 + 0.02 D)` で、**平地に置いたナイフエッジ
      1 枚でも `single` より 8〜9 dB 大きく出る**（初回はこれで全経路が落ちた）。
      裏返すと、きれいな 1 枚の遮蔽では 2 つのモデルが 8〜9 dB 離れる＝±1 dB の
      実測で**どちらが合うか分かれる**（較正の判断点にとって使える観測）。
    - **「天端以外が F1 の 0.6 倍に入らない」は使わない**＝アンテナ 2 m では
      平らな地面そのものが F1 に入るので、どの経路でも当たる（初回の誤り）。
      代わりに「天端の前後 60 m より外に、端点より 1 m 以上高い起伏が無い」。
    - 端点の周り 40 m 四方の起伏 1 m 以下・両端の標高差 3 m 以下・`single` 8〜18 dB。

実行（製品の venv で。ネットワークで地理院タイルを取る＝出力先にキャッシュする）:
    python experiments/field_levee_site_scan.py scan  [--out DIR]
    python experiments/field_levee_site_scan.py sheet [--out DIR] [--start 0 --count 6 --zoom 16]

    出力先の既定は `%TEMP%/radiosim_levee_scan`（リポジトリへは書かない）。

結果（2026-10-11・4 区域）: 評価した経路 15,423 本 → ふるい 1,514 本 → 場所ごとに 62 か所。
    上位 30 か所を写真で確かめ、残ったのは 4 か所（座標と所見はメモリの
    `project_radiosim_field.md` §7）。落ちた理由の大半は高速道路・鉄道の盛土、家並み、並木。
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np
import requests
from numpy.lib.stride_tricks import sliding_window_view
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from core import models  # noqa: E402

DEM_Z = 15
UA = {"User-Agent": "RadioSim-levee-scan (personal research)"}
FREQ_MHZ = 920.0
ANT_H = 2.0

BOXES = {
    # 名前: (lat_min, lat_max, lon_min, lon_max)
    "利根川_栗橋五霞": (36.10, 36.16, 139.68, 139.77),
    "荒川_吉見鴻巣": (36.00, 36.06, 139.44, 139.53),
    "小貝川_つくばみらい": (35.97, 36.04, 139.99, 140.08),
    "利根川_河内稲敷": (35.85, 35.91, 140.20, 140.30),
}


def ll2px(lat, lon, z):
    n = 2 ** z * 256
    r = math.radians(lat)
    return ((lon + 180.0) / 360.0 * n,
            (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n)


def px2ll(x, y, z):
    n = 2 ** z * 256
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, x / n * 360.0 - 180.0


def _get(url, path):
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        r = requests.get(url, headers=UA, timeout=20)
        time.sleep(0.05)
        with open(path, "wb") as f:
            f.write(r.content if r.ok else b"")
    return path


def dem_tile(out, tx, ty):
    p = _get(f"https://cyberjapandata.gsi.go.jp/xyz/dem5a_png/{DEM_Z}/{tx}/{ty}.png",
             os.path.join(out, "dem5a", f"{tx}_{ty}.png"))
    if os.path.getsize(p) == 0:
        return np.full((256, 256), np.nan)
    a = np.asarray(Image.open(p).convert("RGB")).astype(np.int64)
    x = a[..., 0] * 65536 + a[..., 1] * 256 + a[..., 2]
    h = np.where(x < 8388608, x * 0.01, (x - 16777216) * 0.01).astype(float)
    h[(a[..., 0] == 128) & (a[..., 1] == 0) & (a[..., 2] == 0)] = np.nan
    return h


def mosaic(out, box):
    la0, la1, lo0, lo1 = box
    x0, _ = ll2px(la0, lo0, DEM_Z)
    x1, _ = ll2px(la1, lo1, DEM_Z)
    _, y1 = ll2px(la0, lo0, DEM_Z)
    _, y0 = ll2px(la1, lo1, DEM_Z)
    tx0, tx1, ty0, ty1 = int(x0 // 256), int(x1 // 256), int(y0 // 256), int(y1 // 256)
    rows = [np.hstack([dem_tile(out, tx, ty) for tx in range(tx0, tx1 + 1)])
            for ty in range(ty0, ty1 + 1)]
    return np.vstack(rows), tx0 * 256, ty0 * 256


def _filt(a, k, fn):
    b = np.pad(a, k // 2, mode="edge")
    b = fn(sliding_window_view(b, k, axis=0), axis=-1)
    return fn(sliding_window_view(b, k, axis=1), axis=-1)


def bilinear(H, gx, gy):
    x0 = np.clip(np.floor(gx).astype(int), 0, H.shape[1] - 2)
    y0 = np.clip(np.floor(gy).astype(int), 0, H.shape[0] - 2)
    fx, fy = gx - x0, gy - y0
    ok = (gx >= 0) & (gy >= 0) & (gx < H.shape[1] - 1) & (gy < H.shape[0] - 1)
    v = (H[y0, x0] * (1 - fx) * (1 - fy) + H[y0, x0 + 1] * fx * (1 - fy)
         + H[y0 + 1, x0] * (1 - fx) * fy + H[y0 + 1, x0 + 1] * fx * fy)
    return np.where(ok, v, np.nan)


def luc_from_bullington(bull, total_km):
    """製品の Bullington（補正項つき）から補正前の Luc を二分法で逆算する。"""
    lo, hi = 0.0, bull
    for _ in range(60):
        mid = (lo + hi) / 2
        if mid + (1 - math.exp(-mid / 6)) * (10 + 0.02 * total_km) < bull:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def evaluate(H, ox, oy, mpp, cx, cy, ux, uy, d1, d2):
    """天端 (cx,cy)[モザイク画素] から法線 (ux,uy) へ -d1..+d2 [m] の経路を評価する。"""
    L = d1 + d2
    n = int(L / 2) + 1
    s = np.linspace(-d1, d2, n) / mpp
    gx, gy = cx + ux * s, cy + uy * s
    prof = bilinear(H, gx, gy)
    if np.isnan(prof).any():
        return None
    flat = []
    r = int(20 / mpp)
    for ex, ey in ((gx[0], gy[0]), (gx[-1], gy[-1])):
        w = H[int(ey) - r:int(ey) + r + 1, int(ex) - r:int(ex) + r + 1]
        if w.size == 0 or np.isnan(w).any():
            return None
        flat.append(float(w.max() - w.min()))
    la_t, lo_t = px2ll(ox + gx[0], oy + gy[0], DEM_Z)
    la_r, lo_r = px2ll(ox + gx[-1], oy + gy[-1], DEM_Z)
    tp = models.calculate_terrain_profile(prof, la_t, lo_t, la_r, lo_r)
    loss = {m: models.calculate_propagation(
        terrain=tp, h_tx=ANT_H, h_rx=ANT_H, freq_mhz=FREQ_MHZ, veg_h=0.0,
        initial_k=4 / 3, diff_method=m, env_type="los", rain_rate=0.0).diff_loss
        for m in ("single", "bullington")}
    d = np.linspace(0, L, n)
    land = float(max(prof[0], prof[-1]))
    return dict(
        tx=(round(la_t, 6), round(lo_t, 6)), rx=(round(la_r, 6), round(lo_r, 6)),
        d1=d1, d2=d2, single=round(loss["single"], 1), bull=round(loss["bullington"], 1),
        luc=round(luc_from_bullington(loss["bullington"], L / 1000.0), 1),
        flat_tx=round(flat[0], 2), flat_rx=round(flat[1], 2),
        bumps=round(float(prof[np.abs(d - d1) > 60].max()) - land, 2),
        crest_rel=round(float(prof[np.abs(d - d1) <= 30].max()) - land, 1),
        end_dh=round(float(prof[-1] - prof[0]), 1))


def scan_box(out, name, box):
    H, ox, oy = mosaic(out, box)
    mpp = 156543.03392 * math.cos(math.radians((box[0] + box[1]) / 2)) / 2 ** DEM_Z
    Hf = np.where(np.isnan(H), np.nanmax(H) + 50, H)  # 欠測を谷にしない
    base = _filt(_filt(Hf, 41, np.min), 41, np.mean)
    rel = np.where(np.isnan(H), 0, H - base)
    # 細さ＝狭い窓の最小値も高ければ台地で、堤防ではない
    narrow = rel - (_filt(_filt(Hf, 11, np.min), 3, np.mean) - base)
    crest = (rel >= 4) & (narrow >= 2.5)
    cell = int(100 / mpp)
    keep = {}
    for y, x in zip(*np.nonzero(crest)):
        k = (y // cell, x // cell)
        if k not in keep or rel[y, x] > rel[keep[k]]:
            keep[k] = (y, x)
    res = []
    r = int(60 / mpp)
    for y, x in keep.values():
        yy, xx = np.nonzero(crest[max(0, y - r):y + r, max(0, x - r):x + r])
        if len(xx) < 8:
            continue
        P = np.c_[xx - xx.mean(), yy - yy.mean()].astype(float)
        w, V = np.linalg.eigh(P.T @ P)
        if w[1] < 4 * w[0]:
            continue  # 線状でない
        ux, uy = -V[1, 1], V[0, 1]  # 軸に直交
        for d1 in (150, 250, 400):
            for d2 in (150, 250, 400):
                e = evaluate(H, ox, oy, mpp, x, y, ux, uy, d1, d2)
                if e is not None:
                    e["box"] = name
                    res.append(e)
    return res


def passes(e):
    return (8 <= e["single"] <= 18 and abs(e["luc"] - e["single"]) <= 1.0
            and e["flat_tx"] <= 1.0 and e["flat_rx"] <= 1.0
            and e["bumps"] <= 1.0 and abs(e["end_dh"]) <= 3.0)


def cmd_scan(out):
    allres = []
    for name, box in BOXES.items():
        r = scan_box(out, name, box)
        print(name, "評価した経路", len(r), flush=True)
        allres += r
    good = sorted((e for e in allres if passes(e)),
                  key=lambda e: abs(e["luc"] - e["single"])
                  + max(e["flat_tx"], e["flat_rx"]) + max(e["bumps"], 0))

    def mid(e):
        return (e["tx"][0] + e["rx"][0]) / 2, (e["tx"][1] + e["rx"][1]) / 2
    picked = []
    for e in good:
        m = mid(e)
        if all(math.hypot((m[0] - mid(p)[0]) * 111000, (m[1] - mid(p)[1]) * 90000) > 800
               for p in picked):
            picked.append(e)
    with open(os.path.join(out, "levee_picked.json"), "w", encoding="utf-8") as f:
        json.dump(picked, f, ensure_ascii=False, indent=1)
    print("ふるいを通った経路", len(good), "→ 場所ごと", len(picked))
    for i, e in enumerate(picked[:30]):
        print(i, e)


def cmd_sheet(out, start, count, z):
    picked = json.load(open(os.path.join(out, "levee_picked.json"), encoding="utf-8"))
    items = picked[start:start + count]
    P, cols = 560, 3
    font = ImageFont.truetype("C:/Windows/Fonts/meiryo.ttc", 22)
    sheet = Image.new("RGB", (cols * P, math.ceil(len(items) / cols) * P), "white")
    for k, e in enumerate(items):
        ax, ay = ll2px(*e["tx"], z)
        bx, by = ll2px(*e["rx"], z)
        x0, y0 = int((ax + bx) / 2 - P / 2), int((ay + by) / 2 - P / 2)
        im = Image.new("RGB", (P, P))
        for ty in range(y0 // 256, (y0 + P) // 256 + 1):
            for tx in range(x0 // 256, (x0 + P) // 256 + 1):
                p = _get(f"https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{tx}/{ty}.jpg",
                         os.path.join(out, "photo", str(z), f"{tx}_{ty}.jpg"))
                try:
                    t = Image.open(p).convert("RGB")
                except Exception:
                    t = Image.new("RGB", (256, 256), (60, 60, 60))
                im.paste(t, (tx * 256 - x0, ty * 256 - y0))
        dr = ImageDraw.Draw(im)
        dr.line([(ax - x0, ay - y0), (bx - x0, by - y0)], fill=(0, 220, 255), width=3)
        for px, py in ((ax, ay), (bx, by)):
            dr.ellipse([px - x0 - 6, py - y0 - 6, px - x0 + 6, py - y0 + 6],
                       outline=(255, 50, 50), width=3)
        dr.rectangle([0, 0, P, 30], fill=(0, 0, 0))
        dr.text((6, 2), f"#{start + k} {e['box']} {e['d1'] + e['d2']}m single {e['single']}dB",
                fill="white", font=font)
        sheet.paste(im, ((k % cols) * P, (k // cols) * P))
    path = os.path.join(out, f"sheet_{start}_{start + len(items) - 1}_z{z}.png")
    sheet.save(path)
    print(path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("scan", "sheet"))
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "."),
                                                  "radiosim_levee_scan"))
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--zoom", type=int, default=16)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.cmd == "scan":
        cmd_scan(a.out)
    else:
        cmd_sheet(a.out, a.start, a.count, a.zoom)
