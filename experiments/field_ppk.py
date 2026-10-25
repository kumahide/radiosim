"""Field の受信機の静止観測を、電子基準点と基線解析して基準点の成果と比べる（後処理＝PPK）。

なぜ残すか（2026-10-18）:
    2026-10-17 に三角点「中野」で 1 回通したが、道具（`final.py`・`semidyna.py`・`possum.py`）は
    作業用フォルダにあって消えた。水準点 `3857`（2026-10-24 の予定）と geoRTK の局 `tamtam` の
    座標当てで同じ処理が要る＝落とし穴を道具の中で処理した 1 本にまとめる。

手順（サブコマンド）:
    setup … RTKLIB-EX 2.5.1（`convbin`・`rnx2rtkp`）・セミ・ダイナミック補正のパラメータ・
            NGS の ANTEX を置き場へ落とす（既にあれば何もしない）。
    rinex … `gnss log` の生ログ（`gnss.ubx`）を RINEX 3.04 の観測と航法へ変える。
            `--rtcm` なら同じ記録の `ntrip.rtcm`（NTRIP の局の MSM）を局の観測へ変える
            ＝局の申告座標（`ntrip.json` の 1005）を電子基準点で当て直す材料（航法は生ログ側を使う）。
    solve … 観測地の設定（`site.json`）に従い、基準局の成果で基線解析して成果と比べる。
    semidyna … 1 点の元期→今期の補正量を出す（確かめ用）。

置き場（RTKLIB-EX の取り寄せ方の決定・2026-10-18）:
    既定は `%LOCALAPPDATA%/radiosim_ppk`（`--home` で替えられる）。リポジトリへは入れない
    （exe と 70 MB の ANTEX）。RTKLIB-EX は GitHub の公開物の zip を SHA-256 で固定して落とす。
    補正パラメータは年度ごとに替わる（2026 年度版＝2026-04-01〜2027-03-31）＝版が替わったら
    `SEMIDYNA` を書き換える。ANTEX は NGS が随時更新する＝固定せず、落とした時の SHA-256 を出す。

🔑 中野で踏んだ落とし穴と、この道具での処理:
    ① convbin の `-ha` は `/` で割るとき先頭の空の欄を飛ばす（strtok）＝`-ha "/型名"` は型名を
       ANT # の欄へ書く。⇒ `rinex` は `-ha " /型名"` で渡し、書けたかをヘッダで確かめる。
    ② 設定でアンテナの型名を書くと、RINEX のヘッダの DELTA H は使われず `antN-antdelu` が効く。
       ⇒ `solve` は両側とも型名と高さを設定へ直に書く（基準局はヘッダから読む）。
    ③ 基準局のレドーム（GEONET の `GSI6` など）は ANTEX に無い＝`<型> NONE` で代え、そう出す。
    ④ 基準局の位置をヘッダの概略値で解くと cm〜dm ずれる（3030 で 8 cm）＝`site.json` の
       成果（元期）にセミ・ダイナミック補正を足した今期の値で与える。解は今期＝補正を引いて元期へ戻す。
    ⑤ 成果は **本点** を使う。同じ場所に付属標（等級種別「電子基準点(付)」・3030 では 5.2 m 下）が
       重なって出る＝`site.json` の `grade` に「(付)」があれば止める。
    ⑥ RTKLIB の exe は Git Bash の `/` の経路だと「no obs data」になる＝Windows の経路で渡す。
    ⑦ BeiDou B1C は RTKLIB-EX が読めない・`-f 3` だと Galileo E6 と BeiDou B3I が落ちる＝`-f 6`。

🔑 水準点 3857（2026-10-25）で分かったこと＝基準局は**高さの近い局**を選ぶ:
    ⑧ 基準局が移動局より大きく高いと、低い側の高さが高く出る（湿潤遅延の差と見られる）。
       鹿野山（楕円体高 382 m）で解くと 3857（49 m）は +0.125・+0.101 m、電子基準点どうしの
       鹿野山→富津（高低差 335 m）でも富津が +0.146 m 高く出た＝こちらの機材の誤りではない。
       富津（47 m）で解くと −0.050・−0.056 m・繰り返しの差 6 mm。`saas` と `off` は 3 通りとも
       同じ解＝相対測位では湿潤分の差を消さない。⇒ `solve` は高低差が `TROPO_DH_WARN_M` 以上なら
       警告する。高さの近い局が選べない（遠すぎる）ときだけ `--tropo est`（対流圏を推定）を補助に
       使う＝1 時間未満の静止では推定が揺れる（3857 の富津で 1 本目 +0.062・2 本目 −0.017）。

`site.json`（観測 1 回ぶん・置き場はリポジトリの外＝`D:\\dev\\field-data\\gnss\\` の下）:
    {
      "rover": {"obs": "rover.obs", "nav": "rover.nav",
                "antenna": "JCA228F0001     NONE", "arp_height": 0.943},
      "base":  {"name": "市原1", "code": "3030", "grade": "電子基準点", "obs": "base/3030290b.26o.gz",
                "lat": "35 26 52.0217", "lon": "140 03 04.8059", "h": 79.674, "datum": "測地成果2024"},
      "ref":   {"name": "中野", "lat": "35 25 52.4683", "lon": "139 56 40.9540",
                "H": 2.00, "geoid": 34.8915}
    }
    経路は site.json からの相対でよい。緯度経度は 10 進か「度 分 秒」（記号 ° ′ ″ も可）。
    基準局の `h` は楕円体高（元期）。比べる点は楕円体高 `h` か、標高 `H` とジオイド高 `geoid` の組。
    `rover.arp_height`＝標石の上面からアンテナの ARP（台座の底）まで（m）。
    `rover.antenna` が null＝型が分からない（NTRIP の局）＝位相中心の補正をかけない（高さに数 cm〜1 dm 残る）。
    基準局の型はヘッダから読む＝`rinex --rtcm` の局（型が空）を基準局に据えても同じく補正をかけない
    （現地の見当づけ＝NTRIP の局を基準局にして、電子基準点の 1 時間ごとのファイルを待たずに解く）。
    `ref.epoch`＝比べる座標の元期／今期（既定 "元期"＝成果）。"不明"（局の申告など）なら両方で比べる。

実行（製品の venv で）:
    python experiments/field_ppk.py setup
    python experiments/field_ppk.py rinex <gnss-dir> --out DIR [--marker NAME]
    python experiments/field_ppk.py solve <site.json> [--out DIR] [--no-semidyna] [--tropo est]
    python experiments/field_ppk.py semidyna <緯度> <経度>

中野の再現（2026-10-18・この道具での合格の基準）:
    材料＝`D:\\dev\\field-data\\gnss\\ppk-20261017\\`（`site.json` あり）。
    2026-10-17 の値＝997 回すべて Fix（ratio 12.5）・成果との差 北 +0.060・東 +0.064・上 −0.099 m。
"""
import argparse
import gzip
import hashlib
import json
import math
import re
import shutil
import ssl
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:                                    # 既定のコンソールは cp932（点名などの実データを出す）
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    except Exception:
        pass

RTKLIB = {
    "url": "https://github.com/rtklibexplorer/RTKLIB/releases/download/v2.5.1/RTKLIB_EX_2.5.1.zip",
    "sha256": "0b20c940b1939b0701f0ee027daf5225221120dbff50c3f563f8705fd0b496f5",
    "dir": "RTKLIB_EX_2.5.1",
    "exe": ("convbin.exe", "rnx2rtkp.exe"),
}
SEMIDYNA = {
    "url": "https://www.gsi.go.jp/common/000276921.zip",
    "sha256": "ad1296efbdb4fff78e119a14f79c8d00df93e537e63727d4602b8321f92fbd6b",
    "name": "SemiDyna2026.par",
}
ANTEX = {"url": "https://geodesy.noaa.gov/ANTCAL/LoadFile?file=ngs20.atx", "name": "ngs20.atx"}

A_GRS80 = 6378137.0
F_GRS80 = 1 / 298.257222101
E2 = F_GRS80 * (2 - F_GRS80)

# 中野で回した設定（static.conf・2026-10-17）。アンテナと基準局の位置は solve が足す
CONF = """\
pos1-posmode       =static
pos1-frequency     =l1+l2+l5
pos1-soltype       =combined
pos1-elmask        =15
pos1-snrmask_r     =off
pos1-dynamics      =off
pos1-tidecorr      =off
pos1-ionoopt       =brdc
pos1-tropopt       =saas
pos1-sateph        =brdc
# GPS＋GLONASS＋Galileo＋BeiDou（SBAS は使わない）
pos1-navsys        =45
pos2-armode        =continuous
pos2-gloarmode     =off
pos2-bdsarmode     =on
pos2-arthres       =3
pos2-arminfix      =10
pos2-elmaskar      =15
out-solformat      =llh
out-outhead        =on
out-outopt         =on
out-height         =ellipsoidal
out-solstatic      =all
"""
TROPO_LINE = "pos1-tropopt       =saas"

# ⑧ 基準局と移動局の楕円体高の差がこれ以上なら警告する。鹿野山→富津の 335 m で +0.12〜0.15 m
# ＝比例で見て 100 m で約 4 cm（1 例からの見当）
TROPO_DH_WARN_M = 100.0


def default_home():
    import os
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "radiosim_ppk"


# ---------------------------------------------------------------- 取り寄せ

def download(url, dest, legacy_tls=False):
    """legacy_tls＝www.gsi.go.jp は古い再ネゴシエーションを求め、既定の TLS では
    `UNSAFE_LEGACY_RENEGOTIATION_DISABLED` で落ちる（2026-10-18）。中身は SHA-256 で確かめる。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "radiosim field_ppk"})
    ctx = ssl.create_default_context()
    if legacy_tls:
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    with urllib.request.urlopen(req, timeout=120, context=ctx) as r, open(tmp, "wb") as f:  # nosec B310
        shutil.copyfileobj(r, f)
    tmp.replace(dest)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cmd_setup(args):
    home = Path(args.home)
    rtk = home / RTKLIB["dir"]
    if all((rtk / e).exists() for e in RTKLIB["exe"]):
        print(f"RTKLIB-EX: 既にある {rtk}")
    else:
        z = home / "RTKLIB_EX_2.5.1.zip"
        print(f"RTKLIB-EX: 落とす {RTKLIB['url']}")
        download(RTKLIB["url"], z)
        if sha256(z) != RTKLIB["sha256"]:
            sys.exit(f"RTKLIB-EX の zip の SHA-256 が固定値と違います: {z}")
        with zipfile.ZipFile(z) as zf:
            for e in RTKLIB["exe"]:
                rtk.mkdir(parents=True, exist_ok=True)
                (rtk / e).write_bytes(zf.read(f"{RTKLIB['dir']}/{e}"))
        z.unlink()
        print(f"  → {rtk}")
    par = home / SEMIDYNA["name"]
    if par.exists():
        print(f"補正パラメータ: 既にある {par}")
    else:
        z = home / "semidyna.zip"
        print(f"補正パラメータ: 落とす {SEMIDYNA['url']}")
        download(SEMIDYNA["url"], z, legacy_tls=True)
        if sha256(z) != SEMIDYNA["sha256"]:
            sys.exit(f"補正パラメータの zip の SHA-256 が固定値と違います（版が替わった？）: {z}")
        with zipfile.ZipFile(z) as zf:
            par.write_bytes(zf.read(SEMIDYNA["name"]))
        z.unlink()
        print(f"  → {par}")
    atx = home / ANTEX["name"]
    if atx.exists():
        print(f"ANTEX: 既にある {atx}（SHA-256 {sha256(atx)}）")
    else:
        print(f"ANTEX: 落とす {ANTEX['url']}（約 70 MB）")
        download(ANTEX["url"], atx)
        print(f"  → {atx}（SHA-256 {sha256(atx)}＝NGS が随時更新するので固定しない）")


def tool(home, name):
    p = Path(home) / RTKLIB["dir"] / name
    if not p.exists():
        sys.exit(f"{p} がありません＝先に `setup` を回してください")
    return p


def run(exe, argv):
    # ⑥ 経路はすべて Windows の形（resolve した Path の str）で渡す
    cmd = [str(exe)] + [str(a) for a in argv]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"{exe.name} が失敗しました（{r.returncode}）\n{r.stdout}\n{r.stderr}")
    return r


# ---------------------------------------------------------------- RINEX

def rinex_header(path):
    """ヘッダの行を {ラベル: [中身, ...]} で返す（.gz も読む）。"""
    opener = gzip.open if str(path).endswith(".gz") else open
    out = {}
    with opener(path, "rt", encoding="ascii", errors="replace") as f:
        for line in f:
            label = line[60:].strip()
            out.setdefault(label, []).append(line[:60])
            if label == "END OF HEADER":
                break
    return out


def antenna_of(header):
    """(ANT #, 型名, DELTA H, E, N) を返す。型名は 20 字（型 16 字＋レドーム 4 字）の欄。"""
    ant = header.get("ANT # / TYPE", [""])[0]
    delta = header.get("ANTENNA: DELTA H/E/N", ["0 0 0"])[0].split()
    return ant[:20].strip(), ant[20:40].rstrip(), *(float(x) for x in delta[:3])


def cmd_rinex(args):
    if args.rtcm:
        return rinex_from_rtcm(args)
    src = Path(args.gnss_dir).resolve() / "gnss.ubx"
    if not src.exists():
        sys.exit(f"{src} がありません")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    obs, nav = out / f"{args.name}.obs", out / f"{args.name}.nav"
    run(tool(args.home, "convbin.exe"), [
        "-r", "ubx", "-v", "3.04", "-f", "6", "-od", "-os",     # ⑦
        "-hm", args.marker,
        "-ha", f" /{args.antenna}",                              # ① 先頭の欄を空白で埋める
        "-hd", f"{args.arp_height}/0/0",
        "-o", obs, "-n", nav, src,
    ])
    serial, anttype, dh, _, _ = antenna_of(rinex_header(obs))
    if anttype != args.antenna.rstrip():
        sys.exit(f"ヘッダのアンテナの型名が {anttype!r}（ANT # は {serial!r}）＝`-ha` の渡し方を確かめる")
    print(f"→ {obs}\n→ {nav}\n  ANT TYPE {anttype!r}・DELTA H {dh}")


def rinex_from_rtcm(args):
    """`ntrip.rtcm` → 局の観測。MSM は週の中の時刻しか持たない＝`ntrip.json` の始まりを `-tr` に渡す。
    局のアンテナの型（1033）は geoRTK の局が送らない＝ヘッダは空のまま（solve で null にする）。"""
    gdir = Path(args.gnss_dir).resolve()
    src, meta_path = gdir / "ntrip.rtcm", gdir / "ntrip.json"
    for p in (src, meta_path):
        if not p.exists():
            sys.exit(f"{p} がありません")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    t0 = meta["started_utc"][:19].replace("-", "/").replace("T", " ")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    name = args.name if args.name != "rover" else meta["mountpoint"]
    obs = out / f"{name}.obs"
    run(tool(args.home, "convbin.exe"), [
        "-r", "rtcm3", "-tr", t0, "-v", "3.04", "-f", "6", "-od", "-os",
        "-hm", meta["mountpoint"], "-o", obs, src,
    ])
    h = rinex_header(obs)
    first = h.get("TIME OF FIRST OBS", ["?"])[0].split()[:6]
    last = h.get("TIME OF LAST OBS", ["?"])[0].split()[:6]
    print(f"→ {obs}（{' '.join(first)} 〜 {' '.join(last)} GPS 時刻）")
    for st in meta.get("stations", []):
        print(f"  局の申告（{st['message']}・ITRF {st['itrf_year']}）{st['lat']:.9f}, {st['lon']:.9f}, "
              f"楕円体高 {st['ellipsoid_h_m']:.4f}・アンテナ高 {st['antenna_height_m']}")


# ---------------------------------------------------------------- 角度と座標

def parse_angle(v):
    """10 進の数か「度 分 秒」（° ′ ″ や : も可・秒の小数点が記号の後ろでもよい）を度で返す。"""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    # 「52″.0217」の形（日本の成果表の書き方）を「52.0217」へ寄せる
    s = re.sub(r"[″\"]\s*\.", ".", s)
    nums = re.findall(r"-?\d+(?:\.\d+)?", s)
    if len(nums) == 1:
        return float(nums[0])
    if len(nums) != 3:
        sys.exit(f"角度が読めません: {v!r}")
    d, m, sec = (float(x) for x in nums)
    sign = -1 if d < 0 or s.startswith("-") else 1
    return sign * (abs(d) + m / 60 + sec / 3600)


def radii(lat_deg):
    """GRS80 の子午線曲率半径 M と卯酉線曲率半径 N。"""
    s = math.sin(math.radians(lat_deg))
    w = math.sqrt(1 - E2 * s * s)
    return A_GRS80 * (1 - E2) / w ** 3, A_GRS80 / w


def neu(a, b):
    """a − b を北・東・上（m）で返す。a, b は (緯度, 経度, 楕円体高)。"""
    m, n = radii(b[0])
    dn = math.radians(a[0] - b[0]) * m
    de = math.radians(a[1] - b[1]) * n * math.cos(math.radians(b[0]))
    return dn, de, a[2] - b[2]


# ---------------------------------------------------------------- セミ・ダイナミック補正

def load_semidyna(path):
    """パラメータを {(緯度の秒, 経度の秒): (dB 秒, dL 秒, dH m)} で返す（格子点＝3 次メッシュの南西の角）。"""
    table = {}
    with open(path, "rb") as f:
        lines = f.read().decode("cp932", errors="replace").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("MeshCode")) + 1
    for ln in lines[start:]:
        parts = ln.split()
        if len(parts) != 4:
            continue
        code = parts[0]
        p, u, q, v, r, w = int(code[0:2]), int(code[2:4]), int(code[4]), int(code[5]), int(code[6]), int(code[7])
        lat_s = p * 2400 + q * 300 + r * 30            # 1 次＝40′・2 次＝5′・3 次＝30″
        lon_s = (u + 100) * 3600 + v * 450 + w * 45    # 1 次＝1°・2 次＝7.5′・3 次＝45″
        table[(lat_s, lon_s)] = tuple(float(x) for x in parts[1:])
    return table


def semidyna(table, lat, lon):
    """元期→今期の補正量（dB 秒・dL 秒・dH m）。格子 2.5′×3.75′ の双一次（国土地理院の方法）。"""
    lat_s, lon_s = lat * 3600, lon * 3600
    la0, lo0 = math.floor(lat_s / 150) * 150, math.floor(lon_s / 225) * 225
    corners = [(la0, lo0), (la0, lo0 + 225), (la0 + 150, lo0), (la0 + 150, lo0 + 225)]
    missing = [c for c in corners if c not in table]
    if missing:
        sys.exit(f"補正パラメータに格子点がありません（{lat:.6f}, {lon:.6f} の周り）")
    t, s = (lat_s - la0) / 150, (lon_s - lo0) / 225
    sw, se, nw, ne = (table[c] for c in corners)
    return tuple((1 - t) * (1 - s) * a + (1 - t) * s * b + t * (1 - s) * c + t * s * d
                 for a, b, c, d in zip(sw, se, nw, ne))


def to_konki(table, llh):
    db, dl, dh = semidyna(table, llh[0], llh[1])
    return llh[0] + db / 3600, llh[1] + dl / 3600, llh[2] + dh


def to_genki(table, llh):
    """今期→元期＝補正量を引く。補正量は元期の位置で引くべきなので 2 回まわして寄せる。"""
    g = llh
    for _ in range(2):
        db, dl, dh = semidyna(table, g[0], g[1])
        g = (llh[0] - db / 3600, llh[1] - dl / 3600, llh[2] - dh)
    return g


def cmd_semidyna(args):
    table = load_semidyna(Path(args.par or Path(args.home) / SEMIDYNA["name"]))
    lat, lon = parse_angle(args.lat), parse_angle(args.lon)
    db, dl, dh = semidyna(table, lat, lon)
    m, n = radii(lat)
    print(f"dB {db:+.5f}″ dL {dl:+.5f}″ dH {dh:+.5f} m"
          f"＝北 {math.radians(db / 3600) * m:+.3f}・東 {math.radians(dl / 3600) * n * math.cos(math.radians(lat)):+.3f}"
          f"・上 {dh:+.3f} m（元期→今期）")


# ---------------------------------------------------------------- 基線解析

def antex_types(path):
    types = set()
    with open(path, encoding="ascii", errors="replace") as f:
        for line in f:
            if line[60:].startswith("TYPE / SERIAL NO"):
                types.add(line[:20].rstrip())
    return types


def pick_antenna(anttype, known, who):
    """③ ANTEX に無ければレドームを NONE に代える。それも無ければ止める。"""
    if anttype in known:
        return anttype
    alt = f"{anttype[:16]:<16}NONE"
    if alt in known:
        print(f"  ⚠️ {who}のアンテナ {anttype!r} は ANTEX に無い＝{alt!r} で代える（レドームの効きは入らない）")
        return alt
    sys.exit(f"{who}のアンテナ {anttype!r} も {alt!r} も ANTEX にありません")


def read_pos(path):
    rows = []
    with open(path, encoding="ascii", errors="replace") as f:
        for line in f:
            if line.startswith("%") or not line.strip():
                continue
            p = line.split()
            rows.append({"t": f"{p[0]} {p[1]}", "lat": float(p[2]), "lon": float(p[3]), "h": float(p[4]),
                         "q": int(p[5]), "ns": int(p[6]), "ratio": float(p[14])})
    return rows


def local_file(base_dir, rel, out):
    """site.json からの相対経路を解き、.gz なら out へ展開した経路を返す。"""
    p = (base_dir / rel).resolve()
    if not p.exists():
        sys.exit(f"{p} がありません")
    if p.suffix == ".gz":
        dest = out / p.stem
        with gzip.open(p, "rb") as fi, open(dest, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        return dest
    return p


def cmd_solve(args):
    site_path = Path(args.site).resolve()
    site = json.loads(site_path.read_text(encoding="utf-8"))
    sdir = site_path.parent
    out = Path(args.out).resolve() if args.out else sdir / "ppk"
    out.mkdir(parents=True, exist_ok=True)
    rover, base, ref = site["rover"], site["base"], site.get("ref")

    if "(付)" in base.get("grade", "") or "（付）" in base.get("grade", ""):
        sys.exit(f"基準局の成果が付属標（{base['grade']}）＝本点の成果を使う（⑤）")
    known = antex_types(Path(args.atx or Path(args.home) / ANTEX["name"]))
    table = None if args.no_semidyna else load_semidyna(Path(args.par or Path(args.home) / SEMIDYNA["name"]))

    rover_obs = local_file(sdir, rover["obs"], out)
    nav = local_file(sdir, rover["nav"], out)
    base_obs = local_file(sdir, base["obs"], out)
    if rover.get("antenna"):
        rover_ant = pick_antenna(rover["antenna"], known, "移動局")
    else:
        rover_ant = ""
        print("  ⚠️ 移動局のアンテナの型が分からない＝位相中心の補正をかけない（高さに数 cm〜1 dm 残る）")
    _, base_type, base_dh, base_de, base_dn = antenna_of(rinex_header(base_obs))
    if base_type:
        base_ant = pick_antenna(base_type, known, "基準局")
    else:                                   # `rinex --rtcm` の局（1033 が来ない）を基準局に据えたとき
        base_ant = ""
        print("  ⚠️ 基準局のアンテナの型が分からない＝位相中心の補正をかけない（高さに数 cm〜1 dm 残る）")

    base_genki = (parse_angle(base["lat"]), parse_angle(base["lon"]), float(base["h"]))
    base_pos = base_genki if table is None else to_konki(table, base_genki)
    conf = out / "solve.conf"
    conf.write_text(CONF.replace(TROPO_LINE, f"pos1-tropopt       ={args.tropo}") + "\n".join([
        "# ② 型名を書くとヘッダの DELTA H は使われない＝高さも直に書く",
        f"ant1-anttype       ={rover_ant}",
        f"ant1-antdelu       ={float(rover.get('arp_height') or 0.0)}",
        f"ant2-anttype       ={base_ant}",
        f"ant2-antdele       ={base_de}",
        f"ant2-antdeln       ={base_dn}",
        f"ant2-antdelu       ={base_dh}",
        f"# ④ 基準局＝{base.get('name', '')}（{base.get('code', '')}）の成果"
        f"{'（元期のまま）' if table is None else '＋セミ・ダイナミック補正＝今期'}",
        "ant2-postype       =llh",
        f"ant2-pos1          ={base_pos[0]:.11f}",
        f"ant2-pos2          ={base_pos[1]:.11f}",
        f"ant2-pos3          ={base_pos[2]:.4f}",
        f"file-rcvantfile    ={Path(args.atx or Path(args.home) / ANTEX['name']).resolve()}",
    ]) + "\n", encoding="utf-8")
    pos = out / "solve.pos"
    run(tool(args.home, "rnx2rtkp.exe"), ["-k", conf, "-o", pos, rover_obs, base_obs, nav])

    rows = read_pos(pos)
    if not rows:
        sys.exit(f"解がありません: {pos}")
    nfix = sum(r["q"] == 1 for r in rows)
    last = rows[-1]
    print(f"基線解析: {len(rows)} 回・Fix {nfix}・最後の回 {last['t']} Q={last['q']} ratio {last['ratio']}")
    konki = (last["lat"], last["lon"], last["h"])
    dist = math.hypot(*neu(konki, base_pos)[:2]) / 1000
    print(f"  基線 {dist:.3f} km・解（{'元期' if table is None else '今期'}）"
          f"{konki[0]:.9f}, {konki[1]:.9f}, 楕円体高 {konki[2]:.4f}")
    dh = base_pos[2] - konki[2]
    if args.tropo == "est":
        print("  ⚠️ 対流圏を推定した＝補助の解（1 時間未満の静止では推定が揺れる・3857 で 8 cm）。"
              "高さの近い基準局で解けるなら、そちらを本番の値にする（⑧）")
    elif abs(dh) >= TROPO_DH_WARN_M:
        print(f"  ⚠️ 基準局が移動局より {abs(dh):.0f} m {'高い' if dh > 0 else '低い'}"
              f"＝移動局の高さが{'高く' if dh > 0 else '低く'}出る（鹿野山→富津の 335 m で 0.12〜0.15 m）。"
              "高さの近い基準局で解く。選べなければ --tropo est を補助に（⑧）")
    sol = konki
    if table is not None:
        sol = to_genki(table, konki)
        print(f"  元期へ戻した解 {sol[0]:.9f}, {sol[1]:.9f}, 楕円体高 {sol[2]:.4f}")
    if ref:
        if "h" in ref:
            ref_h = float(ref["h"])
        else:
            ref_h = float(ref["H"]) + float(ref["geoid"])
        ref_llh = (parse_angle(ref["lat"]), parse_angle(ref["lon"]), ref_h)
        epoch = ref.get("epoch", "元期")
        if epoch not in ("元期", "今期", "不明"):
            sys.exit(f"ref.epoch は 元期・今期・不明 のどれか: {epoch!r}")
        sides = [("元期", sol)] if epoch == "元期" else [("今期", konki)] if epoch == "今期" \
            else [("元期と見なすと", sol), ("今期と見なすと", konki)]
        if table is None and epoch != "元期":
            sides = [("補正なしの解と", sol)]
        for label, s in sides:
            dn, de, du = neu(s, ref_llh)
            msg = (f"「{ref.get('name', '')}」（{label}）との差: 北 {dn:+.3f}・東 {de:+.3f}・上 {du:+.3f} m"
                   f"（水平 {math.hypot(dn, de):.3f} m）")
            if "geoid" in ref:
                msg += f"・標高 {s[2] - float(ref['geoid']):.3f}（成果 {float(ref['H']):.3f}）"
            print(msg)


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", default=str(default_home()), help="RTKLIB・補正パラメータ・ANTEX の置き場")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup")

    p = sub.add_parser("rinex")
    p.add_argument("gnss_dir")
    p.add_argument("--out", required=True)
    p.add_argument("--name", default="rover")
    p.add_argument("--marker", default="FIELD")
    p.add_argument("--antenna", default="JCA228F0001     NONE")
    p.add_argument("--arp-height", type=float, default=0.0, help="ヘッダの DELTA H（solve は site.json の値を使う）")
    p.add_argument("--rtcm", action="store_true", help="ntrip.rtcm（NTRIP の局の観測）を RINEX にする")

    p = sub.add_parser("solve")
    p.add_argument("site")
    p.add_argument("--out")
    p.add_argument("--atx")
    p.add_argument("--par")
    p.add_argument("--no-semidyna", action="store_true", help="基準局を元期のまま与え、解も元期と見なす")
    p.add_argument("--tropo", choices=("saas", "est"), default="saas",
                   help="対流圏＝saas（既定）か est（推定・高さの近い基準局が選べないときの補助＝⑧）")

    p = sub.add_parser("semidyna")
    p.add_argument("lat")
    p.add_argument("lon")
    p.add_argument("--par")

    args = ap.parse_args()
    {"setup": cmd_setup, "rinex": cmd_rinex, "solve": cmd_solve, "semidyna": cmd_semidyna}[args.cmd](args)


if __name__ == "__main__":
    main()
