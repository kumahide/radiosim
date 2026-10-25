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
    solve … 観測地の設定（`site.json`）に従い、基準局の成果で基線解析して成果と比べる（標準）。
    solve-multi … 実験機能 Multi-base PPK（⑩）＝`site.json` の `bases` から局を選び、局ごとに独立に解いて統合する。
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
    ⑨ 電子基準点のレドーム（`GSI6` など）は国土地理院の `GSI_PCV.TXT` に載っているが、中身は
       **相対較正**（NGS の旧書式・`AOAD/M_T` を L1 上 110・L2 上 128 mm・PCV 0 と置く）＝ANTEX（絶対）へ
       そのまま入れられない。⇒ 同じファイルの中の「`<型> GSI6` − `<型> NONE`」（レドームの効きだけ）を
       ANTEX の `<型> NONE` に足した項目を作り、使う型だけの ANTEX（`solve.atx`）を書いて渡す。L1 帯
       （1.56〜1.61 GHz）は L1 の差・ほか（L2・L5・E5・E6・B3）は L2 の差を当てる。確かめ＝ANTEX の絶対値が
       「GSI の相対値 − AOAD/M_T の定義 ＋ AOAD/M_T の絶対値」で 0.1〜0.6 mm（RMS）で再現＝符号の取り決めは同じ。
       効きは 3857 で 2〜3 mm（−5 cm の原因ではない）。置き場に無ければ従来どおり ③ の NONE で代える。

🧪 実験機能 Multi-base PPK（⑩・2026-10-25）＝標準は単一基準局の `solve` のまま:
    ⑩ 10〜35 km では基線長より**局ごとの差**（±5 cm・アンテナとレドームの型で並び、日をまたいで繰り返す）が
       効く。房総の電子基準点 10 局×3 日の 588 本で、1 局（高低差 100 m 未満の中で最短）の |差| の 68% 約 5 cm・
       95% 約 10 cm、近い 3 局の平均で 3.9・7.1 cm。⇒ `solve-multi` は局ごとに `<out>/<局番号>/` で
       `rnx2rtkp` を独立に回し、平均（既定）と中央値を並べる。
       事前選択＝解けない局（付属標・観測が無い・窓と重ならない・アンテナが ANTEX に無い）と外す局
       （高低差 `TROPO_DH_WARN_M` 以上・窓の覆いが `MULTI_MIN_COVERAGE` 未満・アンテナの型が空）を除き、
       近い順に既定 3 局。`--bases 3036,3030` で上書き（外す理由は警告だけ）。
       統合＝最後の回が Fix の局だけ（`solve` は Q に関係なく最後の回を採る＝その落とし穴を繰り返さない）。
       局間の高さの最大差が `--spread-warn`（既定 0.10 m）を超える・Fix の局が 2 局未満なら ⚠️ を出し、
       精度を保証しない。結果は `<out>/multi.json` にも書く。
       3857 の 1 本目を富津・市原1・鹿野山で解くと −0.052・+0.039・+0.12 m（最大差 0.17 m で ⚠️）。

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
    `solve-multi` は `base` の代わりに `bases`（`base` と同じ形の配列・`code` は局ごとに別）を読む。

実行（製品の venv で）:
    python experiments/field_ppk.py setup
    python experiments/field_ppk.py rinex <gnss-dir> --out DIR [--marker NAME]
    python experiments/field_ppk.py solve <site.json> [--out DIR] [--no-semidyna] [--tropo est]
    python experiments/field_ppk.py solve-multi <site.json> [--out DIR] [--n 3] [--bases 3036,3030]
                                    [--combine mean|median] [--spread-warn 0.10]
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
import statistics
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta
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
# ⑨ 国土地理院の電子基準点のアンテナ位相特性（相対較正）。落とさない＝人が置き場へ置く
GSI_PCV = "GSI_PCV.TXT"

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
    gsi = home / GSI_PCV
    if gsi.exists():
        print(f"GSI_PCV: 既にある {gsi}（{read_gsi_pcv(gsi)[0]}）")
    else:
        print(f"GSI_PCV: 無い＝電子基準点のレドーム（GSI6 など）は NONE で代える。"
              f"国土地理院の {GSI_PCV} を {home} へ置けば入る（⑨・効きは 2〜3 mm）")


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


def read_gsi_pcv(path):
    """⑨ GSI_PCV.TXT（NGS の旧書式）を (1 行目, {型名 20 字: {"L1"/"L2": (PCO 北東上, PCV 仰角 90..0)}}) で返す。
    1 項目＝型名の行＋数の行 6 本（PCO 3・PCV 10＋9 を L1・L2 の順）。値は mm・**相対較正**。"""
    lines = Path(path).read_text(encoding="ascii", errors="replace").splitlines()
    shape = (3, 10, 9, 3, 10, 9)
    out = {}
    for i, ln in enumerate(lines[:-6]):
        if not ln[:1].strip():
            continue
        try:
            rows = [[float(x) for x in lines[i + 1 + k].split()] for k in range(6)]
        except ValueError:
            continue
        if tuple(len(r) for r in rows) != shape:
            continue
        out.setdefault(ln[:20].rstrip(), {"L1": (rows[0], rows[1] + rows[2]), "L2": (rows[3], rows[4] + rows[5])})
    if not out:
        sys.exit(f"{path} から項目が読めません（GSI_PCV.TXT の書式か確かめる）")
    return (lines[0].strip() if lines else ""), out


def read_atx(path, names):
    """ANTEX のヘッダの行と、names の型の項目（START OF ANTENNA〜END OF ANTENNA の行）を返す。"""
    header, blocks, block, name, in_header = [], {}, None, None, True
    with open(path, encoding="ascii", errors="replace") as f:
        for line in f:
            lab = line[60:].rstrip()
            if in_header:
                header.append(line)
                in_header = not lab.startswith("END OF HEADER")
                continue
            if lab.startswith("START OF ANTENNA"):
                block, name = [line], None
                continue
            if block is None:
                continue
            block.append(line)
            if lab.startswith("TYPE / SERIAL NO"):
                name = line[:20].rstrip()
            if lab.startswith("END OF ANTENNA"):
                if name in names and name not in blocks:
                    blocks[name] = block
                block = None
    return header, blocks


def _gsi_band(code):
    """ANTEX の周波数（`G01` など）を GSI の L1・L2 のどちらで近づけるか（1.56〜1.61 GHz＝L1・ほかは L2）。"""
    return "L1" if code[1:] in ("01", "04") or code == "C02" else "L2"


def _at_elev(values, elev):
    """仰角 90, 85, …, 0 の 19 値を、仰角 elev（度）で線形に引く（0 度より下は 0 度の値）。"""
    x = min(max((90.0 - elev) / 5.0, 0.0), 18.0)
    i = min(int(x), 17)
    return values[i] + (values[i + 1] - values[i]) * (x - i)


def gsi_radome_entry(none_block, gsi, anttype):
    """⑨ ANTEX（絶対）の `<型> NONE` の項目に、GSI_PCV の中の「`<型> <レドーム>` − `<型> NONE`」（相対どうしの差＝
    レドームの効きだけ）を足して、型名を `<型> <レドーム>` にした項目を作る。L5 帯などは L2 の差を当てる。"""
    rel, rel0 = gsi[anttype], gsi[f"{anttype[:16]:<16}NONE"]
    diff = {b: ([a - c for a, c in zip(rel[b][0], rel0[b][0])], [a - c for a, c in zip(rel[b][1], rel0[b][1])])
            for b in ("L1", "L2")}
    zen = None
    out, band = [], None
    for line in none_block:
        lab = line[60:].rstrip()
        if lab.startswith("TYPE / SERIAL NO"):
            line = f"{anttype:<20}" + line[20:]
        elif lab.startswith("ZEN1 / ZEN2 / DZEN"):
            z1, z2, dz = (float(x) for x in line[:60].split())
            zen = [z1 + dz * k for k in range(int(round((z2 - z1) / dz)) + 1)]
        elif lab.startswith("START OF FREQUENCY"):
            band = _gsi_band(line[3:6])
        elif lab.startswith("END OF FREQUENCY"):
            band = None
        elif band and lab.startswith("NORTH / EAST / UP"):
            p = [float(x) + d for x, d in zip(line[:30].split(), diff[band][0])]
            line = f"{p[0]:10.2f}{p[1]:10.2f}{p[2]:10.2f}{'':30}NORTH / EAST / UP\n"
        elif band and (line[:8].strip() == "NOAZI" or re.match(r"\s*\d", line[:8])):
            # PCV の行＝先頭が `NOAZI` か方位（度）、続けて天頂角ごとの値（F8.2）。方位つきの行にも同じ差を足す
            # （行が 60 字を超えるのでラベルの欄では見分けられない）
            head, vals = line[:8], [float(x) for x in line[8:].split()]
            if zen is None or len(vals) != len(zen):
                sys.exit(f"ANTEX の {anttype[:16]} NONE の PCV の行が ZEN1 / ZEN2 / DZEN と合いません")
            line = head + "".join(f"{v + _at_elev(diff[band][1], 90.0 - z):8.2f}" for v, z in zip(vals, zen)) + "\n"
        out.append(line)
    return out, diff


def pick_antenna(anttype, known, who, gsi=None):
    """③ ANTEX に無ければ、GSI_PCV（⑨）に型とレドームが載っていればレドームの差を足した項目を作る（"gsi"）。
    それも無ければレドームを NONE に代える。NONE も無ければ止める。返り値＝(型名, "atx" か "gsi")。"""
    if anttype in known:
        return anttype, "atx"
    alt = f"{anttype[:16]:<16}NONE"
    if alt in known:
        if gsi and anttype in gsi[1] and alt in gsi[1]:
            print(f"  {who}のアンテナ {anttype!r} は ANTEX に無い＝{alt!r} に GSI_PCV のレドームの差を足して使う（⑨）")
            return anttype, "gsi"
        hint = "GSI_PCV に載っていない" if gsi else "GSI_PCV.TXT を置けば入る＝⑨"
        print(f"  ⚠️ {who}のアンテナ {anttype!r} は ANTEX に無い＝{alt!r} で代える（レドームの効きは入らない・{hint}）")
        return alt, "atx"
    sys.exit(f"{who}のアンテナ {anttype!r} も {alt!r} も ANTEX にありません")


def write_solve_atx(atx, gsi, used, dest):
    """⑨ 使う型だけを持つ ANTEX を dest へ書く。"gsi" の型は NONE の項目からレドームの差で作る。"""
    names = {name if how == "atx" else f"{name[:16]:<16}NONE" for name, how in used}
    header, blocks = read_atx(atx, names)
    body = []
    for name, how in used:
        if how == "atx":
            body += blocks[name]
            continue
        block, diff = gsi_radome_entry(blocks[f"{name[:16]:<16}NONE"], gsi[1], name)
        body += block
        for b in ("L1", "L2"):
            dp, dv = diff[b]
            print(f"    {b}: PCO の差 北 {dp[0]:+.1f}・東 {dp[1]:+.1f}・上 {dp[2]:+.1f} mm・"
                  f"PCV の差 {min(dv):+.1f}〜{max(dv):+.1f} mm")
    dest.write_text("".join(header + body), encoding="ascii")
    return dest


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


def is_auxiliary(base):
    """⑤ 成果が付属標（等級種別「電子基準点(付)」）か。"""
    return "(付)" in base.get("grade", "") or "（付）" in base.get("grade", "")


def load_models(args):
    """ANTEX（の型の一覧）・補正パラメータ・GSI_PCV を読む＝(atx, known, table, gsi)。"""
    atx = Path(args.atx or Path(args.home) / ANTEX["name"]).resolve()
    known = antex_types(atx)
    table = None if args.no_semidyna else load_semidyna(Path(args.par or Path(args.home) / SEMIDYNA["name"]))
    gsi = None
    if args.gsi_pcv != "none":
        gsi_path = Path(args.gsi_pcv or Path(args.home) / GSI_PCV)
        if gsi_path.exists():
            gsi = read_gsi_pcv(gsi_path)
            print(f"  GSI_PCV: {gsi_path}（{gsi[0]}・{len(gsi[1])} 項目）")
        elif args.gsi_pcv:
            sys.exit(f"{gsi_path} がありません")
    return atx, known, table, gsi


def ref_point(ref):
    """比べる点の (緯度, 経度, 楕円体高) と元期の扱い。"""
    if "h" in ref:
        ref_h = float(ref["h"])
    else:
        ref_h = float(ref["H"]) + float(ref["geoid"])
    epoch = ref.get("epoch", "元期")
    if epoch not in ("元期", "今期", "不明"):
        sys.exit(f"ref.epoch は 元期・今期・不明 のどれか: {epoch!r}")
    return (parse_angle(ref["lat"]), parse_angle(ref["lon"]), ref_h), epoch


def ref_sides(ref, sol, konki, table):
    """比べる点の元期の扱いに従い、比べる解を [(見出し, 解), ...] で返す。"""
    _, epoch = ref_point(ref)
    sides = [("元期", sol)] if epoch == "元期" else [("今期", konki)] if epoch == "今期" \
        else [("元期と見なすと", sol), ("今期と見なすと", konki)]
    if table is None and epoch != "元期":
        sides = [("補正なしの解と", sol)]
    return sides


def compare_ref(ref, sol, konki, table):
    """比べる点との差を出し、[{"side", "n", "e", "u"}, ...] で返す。"""
    ref_llh, _ = ref_point(ref)
    out = []
    for label, s in ref_sides(ref, sol, konki, table):
        dn, de, du = neu(s, ref_llh)
        msg = (f"「{ref.get('name', '')}」（{label}）との差: 北 {dn:+.3f}・東 {de:+.3f}・上 {du:+.3f} m"
               f"（水平 {math.hypot(dn, de):.3f} m）")
        if "geoid" in ref:
            msg += f"・標高 {s[2] - float(ref['geoid']):.3f}（成果 {float(ref['H']):.3f}）"
        print(msg)
        out.append({"side": label, "n": dn, "e": de, "u": du})
    return out


def cmd_solve(args):
    site_path = Path(args.site).resolve()
    site = json.loads(site_path.read_text(encoding="utf-8"))
    sdir = site_path.parent
    out = Path(args.out).resolve() if args.out else sdir / "ppk"
    out.mkdir(parents=True, exist_ok=True)
    rover, base, ref = site["rover"], site["base"], site.get("ref")

    if is_auxiliary(base):
        sys.exit(f"基準局の成果が付属標（{base['grade']}）＝本点の成果を使う（⑤）")
    atx, known, table, gsi = load_models(args)

    rover_obs = local_file(sdir, rover["obs"], out)
    nav = local_file(sdir, rover["nav"], out)
    base_obs = local_file(sdir, base["obs"], out)
    r = solve_base(args, rover, rover_obs, nav, base, base_obs, out, atx, known, table, gsi)
    if ref:
        compare_ref(ref, r["sol"], r["konki"], table)


def solve_base(args, rover, rover_obs, nav, base, base_obs, out, atx, known, table, gsi):
    """基準局 1 局で基線解析し、最後の回を採る。`solve` と `solve-multi` の両方が使う。
    返り値＝{"rows", "nfix", "last", "konki"（今期）, "sol"（元期へ戻した解）, "base_pos", "dist_km", "dh"}。"""
    used = []
    if rover.get("antenna"):
        rover_ant, how = pick_antenna(rover["antenna"], known, "移動局", gsi)
        used.append((rover_ant, how))
    else:
        rover_ant = ""
        print("  ⚠️ 移動局のアンテナの型が分からない＝位相中心の補正をかけない（高さに数 cm〜1 dm 残る）")
    _, base_type, base_dh, base_de, base_dn = antenna_of(rinex_header(base_obs))
    if base_type:
        base_ant, how = pick_antenna(base_type, known, "基準局", gsi)
        used.append((base_ant, how))
    else:                                   # `rinex --rtcm` の局（1033 が来ない）を基準局に据えたとき
        base_ant = ""
        print("  ⚠️ 基準局のアンテナの型が分からない＝位相中心の補正をかけない（高さに数 cm〜1 dm 残る）")
    if any(how == "gsi" for _, how in used):
        # RTKLIB が読む受信機アンテナの ANTEX は 1 本＝使う型だけを持つ ANTEX を書いて渡す
        atx = write_solve_atx(atx, gsi, list(dict.fromkeys(used)), out / "solve.atx")

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
        f"file-rcvantfile    ={atx}",
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
    return {"rows": rows, "nfix": nfix, "last": last, "konki": konki, "sol": sol,
            "base_pos": base_pos, "dist_km": dist, "dh": dh}


# ---------------------------------------------------------------- Multi-base PPK（実験機能）

# ⑩ 局間の高さの最大差がこれを超えたら警告し、精度を保証しない。房総の 10 局×3 日で、1 局の解の
# |差| の 95% が約 10 cm（2026-10-25）
MULTI_SPREAD_WARN_M = 0.10
MULTI_DEFAULT_N = 3
# 移動局の窓（最初〜最後の回）のうち、基準局の観測が覆う割合がこれ未満なら事前選択で外す
MULTI_MIN_COVERAGE = 0.8


def ecef_to_llh(x, y, z):
    """地心直交座標（m）→ GRS80 の (緯度, 経度, 楕円体高)。"""
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - E2))
    h = 0.0
    for _ in range(6):
        n = radii(math.degrees(lat))[1]
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - E2 * n / (n + h)))
    return math.degrees(lat), math.degrees(lon), h


def obs_epochs(path):
    """観測の回の時刻（datetime）の並び。RINEX 3 以降の `>` の行を読む（RINEX 2 は空になる）。
    フラグ 2〜6（ヘッダの差し込みなど）の行は回に数えない。"""
    out, in_header = [], True
    with open(path, encoding="ascii", errors="replace") as f:
        for line in f:
            if in_header:
                in_header = not line[60:].startswith("END OF HEADER")
                continue
            if line.startswith(">"):
                p = line[1:].split()
                if len(p) >= 7 and int(p[6]) <= 1:
                    y, mo, d, hh, mi = (int(x) for x in p[:5])
                    out.append(datetime(y, mo, d, hh, mi) + timedelta(seconds=float(p[5])))
    return out


def coverage(rover_epochs, base_epochs):
    """基準局の観測が移動局の窓（最初〜最後の回）を覆う割合（0〜1）と、窓の中の基準局の回の数。
    基準局の刻みは回の間隔の中央値で見る。"""
    if not rover_epochs or len(base_epochs) < 2:
        return 0.0, 0
    r0, r1 = rover_epochs[0], rover_epochs[-1]
    inside = sum(r0 <= t <= r1 for t in base_epochs)
    step = statistics.median((b - a).total_seconds() for a, b in zip(base_epochs, base_epochs[1:]))
    if step <= 0:
        return 0.0, inside
    expected = int((r1 - r0).total_seconds() // step) + 1
    return min(inside / expected, 1.0), inside


def antenna_status(anttype, known, gsi=None):
    """`pick_antenna` と同じ判定を、止めず出さずに返す＝"atx"・"gsi"（⑨）・"none"（レドームを NONE で代える）・
    "unknown"（型が空＝位相中心を補正しない）・"missing"（NONE も ANTEX に無い＝解けない）。"""
    if not anttype:
        return "unknown"
    if anttype in known:
        return "atx"
    alt = f"{anttype[:16]:<16}NONE"
    if alt not in known:
        return "missing"
    return "gsi" if gsi and anttype in gsi[1] and alt in gsi[1] else "none"


def base_code(base):
    return str(base.get("code") or base.get("name") or "")


def base_label(c):
    return f"{c['name']}（{c['code']}）" if c.get("name") and c["name"] != c["code"] else c["code"]


def assess_base(sdir, base, rover_llh, rover_epochs, out, known, gsi):
    """候補の局 1 つを解く前に見る＝(見立て, 観測の経路)。見立ての `fatal` は解けない理由（手動でも止める）、
    `soft` は自動の事前選択で外す理由（手動なら警告だけ）。"""
    code = base_code(base)
    c = {"code": code, "name": base.get("name", ""), "base": base, "dist_km": None, "dh": None,
         "coverage": 0.0, "overlap": 0, "antenna": None, "fatal": [], "soft": []}
    if is_auxiliary(base):
        c["fatal"].append(f"成果が付属標（{base['grade']}）＝本点の成果を使う（⑤）")
    llh = (parse_angle(base["lat"]), parse_angle(base["lon"]), float(base["h"]))
    dn, de, _ = neu(llh, rover_llh)
    c["dist_km"] = math.hypot(dn, de) / 1000
    c["dh"] = llh[2] - rover_llh[2]
    if abs(c["dh"]) >= TROPO_DH_WARN_M:
        c["soft"].append(f"高低差 {c['dh']:+.0f} m＝{TROPO_DH_WARN_M:.0f} m 以上（⑧）")
    obs = (sdir / base.get("obs", "")).resolve()
    if not base.get("obs") or not obs.is_file():
        c["fatal"].append(f"観測が無い（{obs}）")
        return c, None
    sub = out / code
    sub.mkdir(parents=True, exist_ok=True)
    obs = local_file(sdir, base["obs"], sub)
    c["coverage"], c["overlap"] = coverage(rover_epochs, obs_epochs(obs))
    if c["overlap"] == 0:
        c["fatal"].append("移動局の窓と重なる回が無い（RINEX 2 なら回を数えられない）")
    elif c["coverage"] < MULTI_MIN_COVERAGE:
        c["soft"].append(f"移動局の窓の {c['coverage']:.0%} しか覆わない（{MULTI_MIN_COVERAGE:.0%} 未満）")
    c["antenna"] = antenna_status(antenna_of(rinex_header(obs))[1], known, gsi)
    if c["antenna"] == "missing":
        c["fatal"].append("アンテナの型が ANTEX に無い")
    elif c["antenna"] == "unknown":
        c["soft"].append("アンテナの型が分からない（位相中心を補正しない）")
    return c, obs


def select_bases(cands, n=MULTI_DEFAULT_N, manual=None):
    """事前選択＝(使う局, 順位を付けた候補)。順位＝解ける（fatal 無し）→ 外す理由（soft）無し → 距離の近い順。
    自動＝上から soft も fatal も無い局を n 局。manual（局番号か名前の並び）＝その局をその順で使う
    （soft は警告だけ・fatal と見つからない局は ValueError）。"""
    ranked = sorted(cands, key=lambda c: (bool(c["fatal"]), bool(c["soft"]),
                                          math.inf if c["dist_km"] is None else c["dist_km"]))
    if not manual:
        return [c for c in ranked if not c["fatal"] and not c["soft"]][:n], ranked
    picked = []
    for key in manual:
        hit = next((c for c in cands if key in (c["code"], c["name"])), None)
        if hit is None:
            raise ValueError(f"--bases の {key!r} が site.json の bases にありません")
        if hit["fatal"]:
            raise ValueError(f"--bases の {base_label(hit)} は解けません: {'・'.join(hit['fatal'])}")
        if hit not in picked:
            picked.append(hit)
    return picked, ranked


def combine_results(results, method="mean", spread_warn=MULTI_SPREAD_WARN_M):
    """局ごとの解を統合する。results の各項目＝{"code", "name", "q"（最後の回の Q）, "sol"（元期）, "konki"（今期）}。
    最後の回が Fix（Q=1）でない局は外す（`solve` は Q に関係なく最後の回を採る＝その落とし穴を繰り返さない）。
    返り値＝{"used", "dropped", "mean", "median", "adopted", "spread", "warnings"}。mean・median は
    {"sol": (緯度, 経度, 楕円体高), "konki": …}（Fix の局が無ければ None）。spread＝局間の高さの最大差（2 局以上）。"""
    if method not in ("mean", "median"):
        raise ValueError(f"統合の方式は mean か median: {method!r}")
    used = [r for r in results if r["q"] == 1]
    dropped = [r for r in results if r["q"] != 1]
    warnings = [f"{base_label(r)} は最後の回が Fix でない（Q={r['q']}）＝統合から外した" for r in dropped]

    def agg(f):
        if not used:
            return None
        return {k: tuple(f([r[k][i] for r in used]) for i in range(3)) for k in ("sol", "konki")}

    spread = None
    if len(used) >= 2:
        hs = [r["sol"][2] for r in used]
        spread = max(hs) - min(hs)
    if len(used) < 2:
        warnings.append(f"Fix の局が {len(used)} 局＝局ごとの差（±5 cm）を平均で薄められない＝精度を保証しない")
    if spread is not None and spread > spread_warn:
        warnings.append(f"局間の高さの最大差 {spread:.3f} m が {spread_warn:.3f} m を超える＝精度を保証しない"
                        "（局ごとの差が大きいか、どれかの局の解が誤っている）")
    mean, median = agg(statistics.fmean), agg(statistics.median)
    return {"used": [r["code"] for r in used], "dropped": [r["code"] for r in dropped],
            "mean": mean, "median": median, "adopted": mean if method == "mean" else median,
            "spread": spread, "warnings": warnings}


def rover_position(rover_obs, ref):
    """事前選択に使う移動局の概略位置＝観測のヘッダの APPROX POSITION XYZ（単独測位・数 m）。
    無ければ比べる点の成果で代える。"""
    xyz = header_xyz(rinex_header(rover_obs))
    if xyz and any(abs(v) > 1 for v in xyz):
        return ecef_to_llh(*xyz)
    if ref:
        return ref_point(ref)[0]
    sys.exit("移動局の概略位置が分からない（ヘッダに APPROX POSITION XYZ も site.json に ref も無い）")


def header_xyz(header):
    line = header.get("APPROX POSITION XYZ", [""])[0].split()
    try:
        return tuple(float(v) for v in line[:3]) if len(line) >= 3 else None
    except ValueError:
        return None


def cmd_solve_multi(args):
    site_path = Path(args.site).resolve()
    site = json.loads(site_path.read_text(encoding="utf-8"))
    sdir = site_path.parent
    out = Path(args.out).resolve() if args.out else sdir / "ppk_multi"
    out.mkdir(parents=True, exist_ok=True)
    rover, ref, bases = site["rover"], site.get("ref"), site.get("bases")
    if not bases:
        sys.exit("site.json に bases（基準局の候補の配列・各項目は base と同じ形）がありません")
    codes = [base_code(b) for b in bases]
    if "" in codes or len(set(codes)) != len(codes):
        sys.exit(f"bases の局番号（code）が空か重なっている: {codes}")
    print("Multi-base PPK（実験機能）＝基準局ごとに独立に解いて統合する。標準は単一基準局の solve")
    atx, known, table, gsi = load_models(args)

    rover_obs = local_file(sdir, rover["obs"], out)
    nav = local_file(sdir, rover["nav"], out)
    rover_epochs = obs_epochs(rover_obs)
    if not rover_epochs:
        sys.exit(f"移動局の観測の回が読めない（RINEX 3 以降か確かめる）: {rover_obs}")
    rover_llh = rover_position(rover_obs, ref)

    cands, obs_paths = [], {}
    for b in bases:
        c, obs = assess_base(sdir, b, rover_llh, rover_epochs, out, known, gsi)
        cands.append(c)
        obs_paths[c["code"]] = obs
    manual = [s.strip() for s in args.bases.split(",") if s.strip()] if args.bases else None
    try:
        picked, ranked = select_bases(cands, args.n, manual)
    except ValueError as e:
        sys.exit(str(e))
    print(f"基準局の候補（事前選択＝{'--bases の指定' if manual else f'外す理由の無い局を近い順に {args.n} 局'}）:")
    for c in ranked:
        mark = "◎" if c in picked else "  "
        why = "・".join(c["fatal"] + c["soft"])
        print(f"  {mark} {base_label(c)}  {c['dist_km']:.1f} km・高低差 {c['dh']:+.0f} m・"
              f"窓の {c['coverage']:.0%}（{c['overlap']} 回）・アンテナ {c['antenna'] or '-'}"
              + (f"  ⚠️ {why}" if why else ""))
    if not picked:
        sys.exit("使える基準局がありません（--bases で選ぶか、bases に候補を足す）")
    if not manual and len(picked) < args.n:
        print(f"  ⚠️ 外す理由の無い候補が {len(picked)} 局しかない（既定 {args.n} 局）")

    results = []
    for c in picked:
        print(f"── {base_label(c)}")
        r = solve_base(args, rover, rover_obs, nav, c["base"], obs_paths[c["code"]], out / c["code"],
                       atx, known, table, gsi)
        results.append({"code": c["code"], "name": c["name"], "q": r["last"]["q"], "sol": r["sol"],
                         "konki": r["konki"], "dist_km": r["dist_km"], "dh_m": r["dh"],
                         "epochs": len(r["rows"]), "nfix": r["nfix"], "ratio": r["last"]["ratio"],
                         "warned": list(c["soft"])})
    comb = combine_results(results, args.combine, args.spread_warn)

    ref_llh = ref_point(ref)[0] if ref else None
    print(f"Multi-base の結果（{'元期' if table is not None else '補正なし'}の楕円体高・最後の回）:")
    for r in results:
        line = (f"  {base_label(r)}  基線 {r['dist_km']:.2f} km・Q={r['q']}・Fix {r['nfix']}/{r['epochs']}・"
                f"ratio {r['ratio']}・楕円体高 {r['sol'][2]:.4f}")
        if ref_llh:
            s = ref_sides(ref, r["sol"], r["konki"], table)[0][1]
            r["du_ref"] = neu(s, ref_llh)[2]
            line += f"・「{ref.get('name', '')}」との差 上 {r['du_ref']:+.3f} m"
        if r["code"] in comb["dropped"]:
            line += "  ← 統合から外した"
        print(line)
    n_used = len(comb["used"])
    for key, label in (("mean", "平均"), ("median", "中央値")):
        if comb[key]:
            mark = "  ← 採る値" if args.combine == key else ""
            print(f"  {label}（{n_used} 局）  楕円体高 {comb[key]['sol'][2]:.4f}{mark}")
    if comb["spread"] is not None:
        print(f"  局間の高さの最大差 {comb['spread']:.3f} m（{n_used} 局・閾値 {args.spread_warn:.3f} m）")
    for w in comb["warnings"]:
        print(f"  ⚠️ {w}")
    ref_diff = None
    if comb["adopted"]:
        s = comb["adopted"]["sol"]
        print(f"統合した解（{'平均' if args.combine == 'mean' else '中央値'}・元期）"
              f"{s[0]:.9f}, {s[1]:.9f}, 楕円体高 {s[2]:.4f}")
        if ref:
            ref_diff = compare_ref(ref, comb["adopted"]["sol"], comb["adopted"]["konki"], table)
    doc = {"experimental": "Multi-base PPK（実験機能）", "site": str(site_path),
           "options": {"combine": args.combine, "spread_warn_m": args.spread_warn, "n": args.n,
                       "bases": manual, "tropo": args.tropo, "semidyna": table is not None},
           "candidates": [{k: c[k] for k in ("code", "name", "dist_km", "dh", "coverage", "overlap",
                                             "antenna", "fatal", "soft")} | {"picked": c in picked}
                          for c in ranked],
           "stations": results,
           "combined": {k: comb[k] for k in ("used", "dropped", "mean", "median", "spread", "warnings")}
           | {"adopted": args.combine, "ref_diff": ref_diff},
           "no_guarantee": bool(comb["warnings"])}
    dest = out / "multi.json"
    dest.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {dest}")


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

    for name in ("solve", "solve-multi"):
        p = sub.add_parser(name)
        p.add_argument("site")
        p.add_argument("--out")
        p.add_argument("--atx")
        p.add_argument("--par")
        p.add_argument("--no-semidyna", action="store_true", help="基準局を元期のまま与え、解も元期と見なす")
        p.add_argument("--gsi-pcv", help="GSI_PCV.TXT の経路（既定は置き場の GSI_PCV.TXT・無ければ NONE で代える）。"
                                         "none＝使わない（⑨）")
        p.add_argument("--tropo", choices=("saas", "est"), default="saas",
                       help="対流圏＝saas（既定）か est（推定・高さの近い基準局が選べないときの補助＝⑧）")
    p.add_argument("--n", type=int, default=MULTI_DEFAULT_N, help=f"自動で選ぶ局の数（既定 {MULTI_DEFAULT_N}）")
    p.add_argument("--bases", help="使う局を局番号（か名前）のカンマ区切りで指定する＝事前選択を上書きする")
    p.add_argument("--combine", choices=("mean", "median"), default="mean",
                   help="採る値＝mean（単純平均・既定）か median（中央値）。両方とも出す")
    p.add_argument("--spread-warn", type=float, default=MULTI_SPREAD_WARN_M,
                   help=f"局間の高さの最大差がこれを超えたら警告する（m・既定 {MULTI_SPREAD_WARN_M}）")

    p = sub.add_parser("semidyna")
    p.add_argument("lat")
    p.add_argument("lon")
    p.add_argument("--par")

    args = ap.parse_args()
    {"setup": cmd_setup, "rinex": cmd_rinex, "solve": cmd_solve, "solve-multi": cmd_solve_multi,
     "semidyna": cmd_semidyna}[args.cmd](args)


if __name__ == "__main__":
    main()
