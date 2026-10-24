"""Field の作業順 8「水準点での受け入れ」に使う水準点を、机上でふるって候補を出す。

なぜ机上で（2026-10-17）:
    人が基準点成果等閲覧サービスと地図の写真で 1 点ずつ見ると時間がかかり、良い所が
    見つからなかった。⇒ 2026-10-11 の回線探し（`field_levee_site_scan.py`）と同じく
    「機械でふるう → 上位だけ人が写真で確かめる」形にする。

手順:
    fetch … 閲覧サービスの API で、中心の半径内の水準点（詳細つき）と、周りの電子基準点を集める。
    sky   … 各点の上空の遮りを見積もる（建物・地形・林の地平を重ねる）。
    rank  … 4 段でふるって並べる＝上空 → 据えやすさ → 成果の状態 → 電子基準点までの距離
            （順はユーザー指定・2026-10-17）。

閲覧サービスの API（地図のスクリプト `contents/screen/point/js/map_e.js` から読んだ・公開の仕様ではない）:
    一覧＝`GET /kijunten/app/api/base`。引数は lat_min/lat_max/lon_min/lon_max・
    intersect_lat/intersect_lon・center_lat/center_lon・rank_ids・result_status_ids・limit・offset。
    詳細＝`GET /kijunten/app/api/base/<id>`（地目 `land_type_value`・成果・改算の別・点の記の有無）。
    等級の ID は `contents/screen/point/data/basic_tree_data_e.json`。

🔑 上空の見積もりの前提（粗い＝写真で確かめる前の順位づけにだけ使う）:
    - 地面は平ら・アンテナは標石の 1 m 上・仰角 15° より上の遮りを数える（GNSS の仰角マスクの慣例）。
      指標＝方位 5° ごとの地平の仰角から、15° より上を塞ぐ割合（0＝全く塞がない・1＝全部）。
    - 建物＝地理院のベクトルタイル（experimental_bvmap・z16）の輪郭。高さは種類から＝
      普通 7 m・堅ろう 12 m・高層 30 m・普通無壁舎 4 m・堅ろう無壁舎 6 m。150 m 以内。
    - 地形＝地理院の標高タイル（dem_png・z14＝10 m）。1.5 km 以内を 10 m 刻み。
    - 林＝OpenFreeMap（OSM 由来・z14）の landcover の wood/forest を 12 m・scrub を 3 m。
    ⚠️ 1 本ずつの木（街路樹）はどのデータにも無い＝見ていない。水準点の水平位置は概略値
       （「水平位置決定作業より算出」）＝数 m ずれうる。⇒ 上位は必ず写真で確かめる。
    ⚠️ 初めは OSM の Overpass で建物と木を引いたが、当日は本家もミラーも 500/504 で使えなかった。

据えやすさ＝地目だけで決める（点の記の図はログインが要るので見ていない）。「公衆用道路」は
    歩道の上か車道の端かが分からない。宅地・田畑・山林・墓地は私有の敷地の中らしいので落とす。

実行（製品の venv で。ネットワークでタイルを取る＝出力先にキャッシュする）:
    python experiments/field_benchmark_scan.py fetch [--out DIR] [--center 名前=緯度,経度 ...] [--radius 20]
    python experiments/field_benchmark_scan.py sky   [--out DIR]
    python experiments/field_benchmark_scan.py rank  [--out DIR] [--max-loss 0.15] [--top 25]

    中心の既定＝home（自宅 35.579452,139.649174）と nakano（三角点「中野」）。
    出力先の既定は `%TEMP%/radiosim_bm_scan`（リポジトリへは書かない）。

結果（2026-10-17・半径 20 km）: 自宅 160 点 → 34 点・現地 72 点 → 16 点。写真で確かめる候補と
    所見はメモリの `project_roadmap.md`（Field の作業順 8）。
"""
import argparse
import gzip
import io
import json
import math
import os
import sys
import time
from pathlib import Path

import requests
from PIL import Image

for _stream in (sys.stdout, sys.stderr):
    try:                                    # 既定のコンソールは cp932（点名などの実データを出す）
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    except Exception:
        pass

API = "https://service.gsi.go.jp/kijunten/app/api"
BVMAP = "https://cyberjapandata.gsi.go.jp/xyz/experimental_bvmap/{z}/{x}/{y}.pbf"
DEM = "https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png"
OFM_TILEJSON = "https://tiles.openfreemap.org/planet"
UA = {"User-Agent": "Mozilla/5.0 (radiosim field_benchmark_scan)"}

CENTERS = {"home": (35.579452, 139.649174), "nakano": (35.431240723241636, 139.94470707549294)}
LEVEL_RANKS = ["L00", "L05", "L01", "L06", "L08", "L07", "L04", "L09", "L02", "L0S", "L03"]
ALL_STATUS = "0,1,11,12,13,16,1S,2,3,32,4,5,6,60,7,8,9,S"
UNUSABLE = {"1", "6", "60", "2", "13"}  # 亡失・廃点・手続中（廃点）・移転・廃点予定＝上空を見るまでもない

ANT = 1.0
MASK = 15.0
R_BLD = 150.0
R_TERRAIN = 1500
H_BLD = {3101: 7, 3102: 12, 3103: 30, 3111: 4, 3112: 6}
H_WOOD = {"wood": 12, "forest": 12, "scrub": 3}

EASE = {
    "公園": (0, "敷地の中（公園）"), "堤": (0, "堤"),
    "学校用地": (1, "敷地の中（学校＝許可が要る）"), "境内地": (1, "敷地の中（社寺＝許可が要る）"),
    "公衆用道路": (2, "道路（歩道の上か要確認）"), "雑種地": (2, "雑種地"), "その他": (2, "その他"),
    "畑": (3, "畑（私有）"), "田": (3, "田（私有）"), "山林": (3, "山林"), "墓地": (3, "墓地"),
    "宅地": (3, "宅地（私有の敷地の中かも）"), None: (3, "地目なし"),
}


# ---------------------------------------------------------------- 通信とキャッシュ

def http_get(url, params=None, cache=None):
    """GET。cache を渡すとそこに保存し、次からは読む（404 は空のバイト列として残す）。"""
    if cache is not None and cache.exists():
        return cache.read_bytes()
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=60)
            if r.status_code == 404:
                body = b""
                break
            r.raise_for_status()
            body = r.content
            break
        except requests.RequestException as e:
            if attempt == 3:
                raise
            print(f"  再試行 {e}")
            time.sleep(2 + attempt * 3)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(body)
        time.sleep(0.15)
    return body


def dist_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------- fetch

def list_points(center, radius_km, ranks):
    lat, lon = center
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * math.cos(math.radians(lat)))
    out, offset = [], 0
    while True:
        q = {
            "limit": 500, "offset": offset, "rank_ids": ",".join(ranks),
            "lat_min": lat - dlat, "lat_max": lat + dlat, "lon_min": lon - dlon, "lon_max": lon + dlon,
            "intersect_lat": lat, "intersect_lon": lon, "center_lat": lat, "center_lon": lon,
            "result_status_ids": ALL_STATUS,
        }
        res = json.loads(http_get(f"{API}/base", q))["results"]
        out += res
        if len(res) < 500:
            break
        offset += 500
        time.sleep(0.5)
    return [p for p in out if dist_km(center, (p["lat"], p["lon"])) <= radius_km]


def cmd_fetch(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    centers = dict(CENTERS)
    for s in a.center or []:
        name, ll = s.split("=", 1)
        centers[name] = tuple(float(v) for v in ll.split(","))
    el0 = {}
    for name, c in centers.items():
        pts = list_points(c, a.radius, LEVEL_RANKS)
        print(f"{name}: 半径 {a.radius} km 以内の水準点 {len(pts)}")
        for i, p in enumerate(pts):
            body = http_get(f"{API}/base/{p['id']}", cache=out / "detail" / f"{p['id']}.json")
            p["detail"] = json.loads(body)["results"]
            if i % 25 == 0:
                print(f"  詳細 {i}/{len(pts)}", flush=True)
        (out / f"{name}_points.json").write_text(json.dumps(pts, ensure_ascii=False), encoding="utf-8")
        for p in list_points(c, a.el0_radius, ["EL0"]):
            el0[p["id"]] = p
    (out / "el0.json").write_text(json.dumps(list(el0.values()), ensure_ascii=False), encoding="utf-8")
    (out / "centers.json").write_text(json.dumps(centers), encoding="utf-8")
    print(f"電子基準点 {len(el0)}")


# ---------------------------------------------------------------- ベクトルタイルの読み取り（MVT・依存を足さない最小限）

def _varint(b, i):
    v = s = 0
    while True:
        c = b[i]
        i += 1
        v |= (c & 0x7F) << s
        if c < 0x80:
            return v, i
        s += 7


def _fields(b):
    """protobuf の (番号, 型, 値) を順に返す。型 2 の値はバイト列。"""
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        else:
            raise ValueError(f"protobuf の型 {wt} は読まない")
        yield num, wt, v


def _packed(b):
    out, i = [], 0
    while i < len(b):
        v, i = _varint(b, i)
        out.append(v)
    return out


def _value(b):
    for num, wt, v in _fields(b):
        if num == 1:
            return v.decode("utf-8")
        if wt == 0:
            return (v >> 1) ^ -(v & 1) if num == 6 else v
    return None


def mvt_layer(data, name):
    """タイルの層 1 つを読み、(属性, 線の列) を返す。線は 0〜1 のタイル座標（y は下向き）。

    面の輪は閉じた線として返す（ClosePath で始点を足す）。
    """
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    for num, _, lay in _fields(data):
        if num != 3:
            continue
        lname, keys, vals, feats, ext = None, [], [], [], 4096
        for n2, _, v in _fields(lay):
            if n2 == 1:
                lname = v.decode("utf-8")
            elif n2 == 2:
                feats.append(v)
            elif n2 == 3:
                keys.append(v.decode("utf-8"))
            elif n2 == 4:
                vals.append(_value(v))
            elif n2 == 5:
                ext = v
        if lname != name:
            continue
        out = []
        for f in feats:
            tags, geom = [], []
            for n3, _, v in _fields(f):
                if n3 == 2:
                    tags = _packed(v)
                elif n3 == 4:
                    geom = _packed(v)
            props = {keys[tags[k]]: vals[tags[k + 1]] for k in range(0, len(tags) - 1, 2)}
            lines, cur, x, y, i = [], [], 0, 0, 0
            while i < len(geom):
                cmd, cnt = geom[i] & 7, geom[i] >> 3
                i += 1
                if cmd == 7:
                    if cur:
                        cur.append(cur[0])
                    continue
                for _ in range(cnt):
                    dx, dy = geom[i], geom[i + 1]
                    i += 2
                    x += (dx >> 1) ^ -(dx & 1)
                    y += (dy >> 1) ^ -(dy & 1)
                    if cmd == 1:
                        if len(cur) > 1:
                            lines.append(cur)
                        cur = []
                    cur.append((x / ext, y / ext))
            if len(cur) > 1:
                lines.append(cur)
            out.append((props, lines))
        return out
    return []


# ---------------------------------------------------------------- sky

def txy(lat, lon, z):
    n = 2 ** z
    return (lon + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n


def tll(z, x, y):
    n = 2 ** z
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n)))), x / n * 360 - 180


def enu(lat0, lon0, lat, lon):
    return (lon - lon0) * 111320.0 * math.cos(math.radians(lat0)), (lat - lat0) * 110574.0


def covers_origin(poly):
    c = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[i - 1]
        if (y1 > 0) != (y2 > 0) and 0 < (x2 - x1) * (0 - y1) / (y2 - y1) + x1:
            c = not c
    return c


def tiles_around(lat, lon, r_m, z):
    dlat, dlon = r_m / 110574.0, r_m / (111320.0 * math.cos(math.radians(lat)))
    x0, y0 = txy(lat + dlat, lon - dlon, z)
    x1, y1 = txy(lat - dlat, lon + dlon, z)
    return [(tx, ty) for tx in range(int(x0), int(x1) + 1) for ty in range(int(y0), int(y1) + 1)]


class Sky:
    def __init__(self, out):
        self.cache = Path(out) / "tiles"
        self._dem = {}
        self._vt = {}
        tj = json.loads(http_get(OFM_TILEJSON))
        self.ofm = tj["tiles"][0]  # 日付つきの経路は配信ごとに変わる＝毎回 TileJSON から取る

    def vt(self, kind, url, z, tx, ty, layer):
        k = (kind, tx, ty)
        if k not in self._vt:
            b = http_get(url.format(z=z, x=tx, y=ty), cache=self.cache / f"{kind}_{z}_{tx}_{ty}.pbf")
            self._vt[k] = [(p, [[tll(z, tx + fx, ty + fy) for fx, fy in ln] for ln in lines])
                           for p, lines in (mvt_layer(b, layer) if b else [])]
        return self._vt[k]

    def dem_at(self, lat, lon):
        x, y = txy(lat, lon, 14)
        k = (int(x), int(y))
        if k not in self._dem:
            b = http_get(DEM.format(z=14, x=k[0], y=k[1]), cache=self.cache / f"dem_14_{k[0]}_{k[1]}.png")
            self._dem[k] = Image.open(io.BytesIO(b)).convert("RGB").load() if b else None
        px = self._dem[k]
        if px is None:
            return None
        r, g, b = px[min(255, int((x % 1) * 256)), min(255, int((y % 1) * 256))]
        v = r * 65536 + g * 256 + b
        if v == 2 ** 23:
            return None
        return v * 0.01 if v < 2 ** 23 else (v - 2 ** 24) * 0.01

    @staticmethod
    def _outline_horizon(lat0, lon0, items, r_m, step):
        """(高さ, 経緯度の線) の列から地平を作る。原点を囲む輪があれば inside を立てる。"""
        hor, near, inside = [0.0] * 72, None, False
        for h, ll in items:
            poly = [enu(lat0, lon0, la, lo) for la, lo in ll]
            if len(poly) > 3 and poly[0] == poly[-1] and covers_origin(poly):
                inside = True
                continue
            for i in range(len(poly) - 1):
                (x1, y1), (x2, y2) = poly[i], poly[i + 1]
                n = max(1, int(math.hypot(x2 - x1, y2 - y1) / step))
                for k in range(n + 1):
                    x, y = x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n
                    d = math.hypot(x, y)
                    if d > r_m:
                        continue
                    near = d if near is None else min(near, d)
                    b = int((math.degrees(math.atan2(x, y)) % 360) // 5)
                    hor[b] = max(hor[b], math.degrees(math.atan2(h - ANT, max(d, 0.5))))
        return hor, near, inside

    def buildings(self, lat0, lon0):
        items = [(H_BLD.get(p.get("ftCode"), 7), ln)
                 for tx, ty in tiles_around(lat0, lon0, R_BLD, 16)
                 for p, lines in self.vt("bvmap", BVMAP, 16, tx, ty, "building") for ln in lines]
        return self._outline_horizon(lat0, lon0, items, R_BLD, 1.5)

    def wood(self, lat0, lon0):
        items = []
        for tx, ty in tiles_around(lat0, lon0, R_BLD, 14):
            for p, lines in self.vt("ofm", self.ofm, 14, tx, ty, "landcover"):
                cls = p.get("class") or p.get("subclass")
                if cls in H_WOOD:
                    items += [(H_WOOD[cls], ln) for ln in lines]
        return self._outline_horizon(lat0, lon0, items, R_BLD, 2.0)

    def terrain(self, lat0, lon0):
        h0 = self.dem_at(lat0, lon0)
        hor = [0.0] * 72
        if h0 is None:
            return h0, hor
        for b in range(72):
            az = math.radians(b * 5 + 2.5)
            for d in range(20, R_TERRAIN + 1, 10):
                h = self.dem_at(lat0 + d * math.cos(az) / 110574.0,
                                lon0 + d * math.sin(az) / (111320.0 * math.cos(math.radians(lat0))))
                if h is not None:
                    hor[b] = max(hor[b], math.degrees(math.atan2(h - h0 - ANT, d)))
        return h0, hor


def sky_loss(hor):
    """15° より上を塞ぐ割合（立体角の比）と、15° を超える方位の数。"""
    s = math.sin(math.radians(MASK))
    bl = [h for h in hor if h > MASK]
    return round(sum((math.sin(math.radians(min(h, 90))) - s) / (1 - s) for h in bl) / 72, 3), len(bl)


def cmd_sky(a):
    out = Path(a.out)
    sky = Sky(out)
    for name in json.loads((out / "centers.json").read_text()):
        pts = [p for p in json.loads((out / f"{name}_points.json").read_text(encoding="utf-8"))
               if p["result_status_id"] not in UNUSABLE]
        res = {}
        for i, p in enumerate(pts):
            lat, lon = p["lat"], p["lon"]
            bld, near, in_b = sky.buildings(lat, lon)
            wd, _, in_w = sky.wood(lat, lon)
            h0, ter = sky.terrain(lat, lon)
            hor = [max(v) for v in zip(bld, ter, wd)]
            loss, nb = sky_loss(hor)
            res[p["id"]] = {
                "sky_loss": loss, "blocked_bins": nb, "max_el": round(max(hor), 1),
                "loss_bld": sky_loss(bld)[0], "loss_terrain": sky_loss(ter)[0], "loss_wood": sky_loss(wd)[0],
                "near_building_m": None if near is None else round(near, 1),
                "in_building": in_b, "in_wood": in_w, "dem_m": h0, "horizon": [round(h, 1) for h in hor],
            }
            if i % 20 == 0:
                print(f"  {name} {i}/{len(pts)}", flush=True)
        (out / f"{name}_sky.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        print(f"{name}: 上空を見積もった点 {len(res)}")


# ---------------------------------------------------------------- rank

def cmd_rank(a):
    out = Path(a.out)
    centers = json.loads((out / "centers.json").read_text())
    el0 = json.loads((out / "el0.json").read_text(encoding="utf-8"))
    for name, c in centers.items():
        pts = json.loads((out / f"{name}_points.json").read_text(encoding="utf-8"))
        sky = json.loads((out / f"{name}_sky.json").read_text(encoding="utf-8"))
        rows, drop = [], {"上空": 0, "据えやすさ": 0, "成果の状態": 0}
        for p in pts:
            d = p["detail"]
            r = d["results"][-1] if d["results"] else {}
            land = (d.get("base_attributes") or [{}])[-1].get("land_type_value")
            s = sky.get(p["id"])
            near = min(el0, key=lambda e: dist_km((p["lat"], p["lon"]), (e["lat"], e["lon"])))
            # 1 段目＝上空
            if s is None or s["in_building"] or s["in_wood"] or s["sky_loss"] > a.max_loss:
                drop["上空"] += 1
                continue
            # 2 段目＝据えやすさ
            ease, ease_txt = EASE.get(land, (3, land))
            if ease >= 3:
                drop["据えやすさ"] += 1
                continue
            # 3 段目＝成果の状態（成果と現況がともに正常・標高がある）
            if p["result_status_id"] != "0" or p["attr_status_value"] != "正常" or r.get("altitude") is None:
                drop["成果の状態"] += 1
                continue
            rows.append({
                "id": p["id"], "name": p["name"], "rank": p["rank_value"], "lat": p["lat"], "lon": p["lon"],
                "dist_center_km": round(dist_km(c, (p["lat"], p["lon"])), 1),
                "alt": r.get("altitude"), "recon": r.get("reconstruction_status_value"),
                "pos_src": r.get("survey_level_value"), "land": land, "ease": ease, "ease_txt": ease_txt,
                "tennoki": bool(d.get("points") and d["points"][-1].get("is_tennoki")),
                "el0": f"{near['name']}（{near.get('geonet_no') or near['id']}）",
                "el0_km": round(dist_km((p["lat"], p["lon"]), (near["lat"], near["lon"])), 1), "sky": s,
            })
        # 4 段目を含めた並べ方＝上空（0.02 刻み）→ 据えやすさ → 電子基準点までの距離
        rows.sort(key=lambda r: (round(r["sky"]["sky_loss"] / 0.02), r["ease"], r["el0_km"]))
        print(f"== {name}: {len(pts)} 点 → 落とした {drop} → 残り {len(rows)}")
        for i, r in enumerate(rows[:a.top], 1):
            s = r["sky"]
            print(f"{i:2d} {r['name']} {r['rank']} {r['lat']:.6f},{r['lon']:.6f} 中心から {r['dist_center_km']} km"
                  f" | 遮り {s['sky_loss']:.3f}＝建 {s['loss_bld']:.3f}・地 {s['loss_terrain']:.3f}・林 {s['loss_wood']:.3f}"
                  f"（建物まで {s['near_building_m']} m） | {r['ease_txt']} | 標高 {r['alt']}（{r['recon']}）"
                  f" | {r['el0']} {r['el0_km']} km | {r['id']}")
        (out / f"{name}_rank.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    default_out = os.path.join(os.environ.get("TEMP", "."), "radiosim_bm_scan")
    for n in ("fetch", "sky", "rank"):
        p = sub.add_parser(n)
        p.add_argument("--out", default=default_out)
        if n == "fetch":
            p.add_argument("--center", action="append", help="名前=緯度,経度（既定の 2 か所に足す）")
            p.add_argument("--radius", type=float, default=20.0)
            p.add_argument("--el0-radius", type=float, default=60.0)
        if n == "rank":
            p.add_argument("--max-loss", type=float, default=0.15)
            p.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    {"fetch": cmd_fetch, "sky": cmd_sky, "rank": cmd_rank}[a.cmd](a)


if __name__ == "__main__":
    main()
