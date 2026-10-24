"""電子基準点（GEONET）の観測データを国土地理院の SFTP から取り、観測の時間で切り出して 1 本の RINEX にする。

なぜ残すか（2026-10-18）:
    後処理（`field_ppk.py solve`）の基準局のデータは、これまで人が Web の画面で局と時間を選んで
    落としていた。`tamtam` の回では JST で選んで 1 時間ずれた英字（`c`）を 1 度取り違えた。
    検証のたびに要る＝記録のフォルダから時間の窓を決めて取る道具にする。

手順（サブコマンド）:
    setup … RINGO（国土地理院の RINEX の道具・Web の提供物もこれでつないでいる）を置き場へ落とす。
    ls    … SFTP の 1 か所を一覧する（置き場の形が変わったときの確かめ用）。
    fetch … 局番号と時間の窓（`--from-log <gnss-dir>` か `--start`/`--end`）から、その日の 1 時間ごとの
            ファイル（無ければ 1 日のファイル）を取り、RINGO で切り出して `<局><通日><英字>.<年>o` を書く。

取り寄せの決定（2026-10-18）:
    FTP は 2026-10-30 12:00 JST で終わる＝SFTP（`terras.gsi.go.jp:22`・利用登録のユーザ名とパスワード）。
    2026-03 から RINEX は 4.01 だけ・Compact RINEX を gz にしたもの（`.yyd.gz`）。
    SFTP は Windows の標準の `sftp.exe`（OpenSSH）で開く＝依存を足さない（paramiko だと 6 本増える）。
    パスワードは `SSH_ASKPASS` で渡す＝引数・画面・ログに出さない。
    資格情報＝環境変数 `TERRAS_USER`・`TERRAS_PASSWORD`、無ければ `<置き場>/terras.json`
    （`{"user": "...", "password": "..."}`・リポジトリの外）。
    サーバの鍵は初回に `<置き場>/known_hosts` へ覚える（以後は変わったら止まる）。
    RINGO は公開物の zip を SHA-256 で固定して落とす。Hatanaka の展開・つなぎ・切り出しを 1 本でする。

SFTP の置き場（2026-10-18 に一覧で確かめた）:
    `/data/GRJE_4.01/<年>/<通日>/` に 1 時間ごとの `<局4桁><通日><a〜x>.<年>d.gz`（30 秒・G/R/E/J）と
    航法 `….<年>N.tar.gz`。1 日のファイル（英字 `0`）は無かった（残す＝置き方が替わったときの受け口）。
    ⚠️ Git Bash から `/data` を渡すと `C:/Program Files/Git/data` に書き換わる＝PowerShell から回すか
    `MSYS_NO_PATHCONV=1` を付ける。⚠️ Windows の sftp は引用符の中の `*` をエスケープする＝囲まない。

合格（2026-10-18）:
    中野（`3030`・`b`）と `tamtam`（`3026`・`l`＋`m`＝時間の境をまたぐ）を記録から取り、人が Web で落とした
    `3030290b`・`3026290l` を同じ窓で切ったものと **観測の本体がバイト単位で一致**（75 回・102 回・欠け 0）。
    ヘッダの違いは GLONASS の周波数の一覧だけ（Web は 2 時間ぶんをつないだ分だけ衛星が多い）。

🔑 時刻:
    GEONET の英字は **GPS 時刻** の時（a＝0 時 … x＝23 時）。記録（`gnss.json`）は UTC。
    GPS＝UTC＋18 秒（2017-01-01 から変わっていない・閏秒が入ったら `GPS_UTC` を直す）。
    窓は前後に `--pad`（既定 10 分）を足し、30 秒へ外向きに丸める。

置き場:
    既定は `%LOCALAPPDATA%/radiosim_ppk`（`field_ppk.py` と同じ・`--home` で替えられる）。
    取った元のファイルは `<置き場>/geonet_cache/` に残す＝同じ時間を 2 度取りに行かない。

実行（製品の venv で）:
    python experiments/field_geonet.py setup
    python experiments/field_geonet.py ls /data
    python experiments/field_geonet.py fetch 3045 3036 --from-log D:\\dev\\field-data\\gnss\\gnss-... --out DIR
    python experiments/field_geonet.py fetch 3026 --start "2026-10-17 11:47" --end "2026-10-17 12:17" --out DIR
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:                                    # 既定のコンソールは cp932（局名などを出す）
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    except Exception:
        pass

HOST = "terras.gsi.go.jp"
RINGO = {
    "url": "https://terras.gsi.go.jp/software/ringo/dist/v0.9.6/ringo-v0.9.6-win64.zip",
    "sha256": "2130bc7d301c7737f920cd9586314c337360ca7025d33fbd585857f3c9c0f20c",
    "dir": "ringo-v0.9.6-win64",
}
GPS_UTC = dt.timedelta(seconds=18)
JST = dt.timezone(dt.timedelta(hours=9))
INTERVAL = 30                                # 1 時間・1 日のファイルの間隔（秒）
# 観測データの置き場の候補（`/data` の下）。4.01 の名前は公開の文書に無い＝一覧から拾う
RINEX_DIR = re.compile(r"^GRJE\w*_4\.\d+$")


def default_home():
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "radiosim_ppk"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- RINGO

def cmd_setup(args):
    home = Path(args.home)
    exe = home / RINGO["dir"] / "ringo.exe"
    if exe.exists():
        print(f"RINGO: 既にある {exe}")
        return
    home.mkdir(parents=True, exist_ok=True)
    z = home / f"{RINGO['dir']}.zip"
    print(f"RINGO: 落とす {RINGO['url']}")
    req = urllib.request.Request(RINGO["url"], headers={"User-Agent": "radiosim field_geonet"})
    # B310 を抑止する理由: URL は定数 RINGO["url"]（https の固定値）だけ＝file: などの口は無い
    with urllib.request.urlopen(req, timeout=120) as r, open(z, "wb") as f:  # nosec B310
        shutil.copyfileobj(r, f)
    if sha256(z) != RINGO["sha256"]:
        sys.exit(f"RINGO の zip の SHA-256 が固定値と違います（版が替わった？）: {z}")
    with zipfile.ZipFile(z) as zf:
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(zf.read(f"{RINGO['dir']}/ringo.exe"))
    z.unlink()
    print(f"  → {exe}")


def ringo(home):
    p = Path(home) / RINGO["dir"] / "ringo.exe"
    if not p.exists():
        sys.exit(f"{p} がありません＝先に `setup` を回してください")
    return p


# ---------------------------------------------------------------- SFTP

def credentials(home):
    user, password = os.environ.get("TERRAS_USER"), os.environ.get("TERRAS_PASSWORD")
    path = Path(home) / "terras.json"
    if not (user and password) and path.exists():
        d = json.loads(path.read_text(encoding="utf-8"))
        user, password = d.get("user"), d.get("password")
    if not (user and password):
        sys.exit("SFTP のユーザ名とパスワードがありません＝環境変数 TERRAS_USER・TERRAS_PASSWORD か\n"
                 f"  {path} に {{\"user\": \"...\", \"password\": \"...\"}} を書いてください\n"
                 "  （登録＝https://terras.gsi.go.jp/ftp_user_regist.php・登録後 1 か月ログインしないと消える）")
    return user, password


def sftp_exe():
    native = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / "sftp.exe"
    if native.exists():
        return native
    found = shutil.which("sftp")
    if not found:
        sys.exit("sftp が見つかりません（Windows の「OpenSSH クライアント」を入れてください）")
    return Path(found)


def sftp(home, commands):
    """コマンドを 1 回の接続で流し、(標準出力の行, 標準エラーの行) を返す。
    `-b` は BatchMode＝パスワードを聞かなくなる＝標準入力で流す。"""
    user, password = credentials(home)
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        ask = Path(td) / "askpass.cmd"
        # パスワードは子の環境変数だけに置き、askpass がそれを出す（% を含んでも cmd に展開させない）
        ask.write_text(f'@"{sys.executable}" -I -c "import os,sys;'
                       f"sys.stdout.write(os.environ['TERRAS_PASSWORD']+chr(10))\"\r\n", encoding="ascii")
        env = dict(os.environ, SSH_ASKPASS=str(ask), SSH_ASKPASS_REQUIRE="force", DISPLAY="none",
                   TERRAS_PASSWORD=password)
        cmd = [str(sftp_exe()),
               "-o", "StrictHostKeyChecking=accept-new",
               "-o", f"UserKnownHostsFile={home / 'known_hosts'}",
               "-o", "PubkeyAuthentication=no",
               "-o", "PreferredAuthentications=password,keyboard-interactive",
               "-o", "NumberOfPasswordPrompts=1",
               "-o", "ConnectTimeout=30",
               f"{user}@{HOST}"]
        r = subprocess.run(cmd, input="\n".join(commands + ["bye"]) + "\n", capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env, timeout=1800)
    out = [ln for ln in r.stdout.splitlines() if ln.strip() and not ln.startswith("sftp>")]
    err = [ln for ln in r.stderr.splitlines() if ln.strip()]
    if r.returncode != 0 and not out:
        sys.exit(f"SFTP が失敗しました（{r.returncode}）\n" + "\n".join(err))
    return out, err


def remote_ls(home, path):
    # 引用符で囲むと Windows の sftp は `*` をエスケープして一致しなくなる＝空白のある時だけ囲む
    arg = f'"{path}"' if " " in path else path
    out, err = sftp(home, [f"ls -1 {arg}"])
    names = [Path(ln.strip()).name for ln in out]
    return names, err


def cmd_ls(args):
    names, err = remote_ls(args.home, args.path)
    for n in names:
        print(n)
    for e in err:
        print(e, file=sys.stderr)


def rinex_root(home, override):
    if override:
        return override
    names, err = remote_ls(home, "/data")
    hits = sorted(n for n in names if RINEX_DIR.match(n))
    if not hits:
        sys.exit("`/data` に RINEX 4 の置き場が見つかりません＝`ls /data` で見て `--dir` で渡してください\n"
                 f"  /data の中身: {names}\n" + "\n".join(err))
    return f"/data/{hits[-1]}"


# ---------------------------------------------------------------- 時間の窓

def parse_time(text, scale):
    """「2026-10-17 01:20[:00]」か ISO（末尾 Z 可）。scale＝utc／gps／jst。GPS 時刻の素の datetime を返す。"""
    s = text.strip().replace("/", "-").replace("T", " ").rstrip("Z")
    s = re.sub(r"\.\d+$", "", s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            t = dt.datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    else:
        sys.exit(f"時刻が読めません: {text!r}（例: 2026-10-17 01:20）")
    if scale == "gps":
        return t
    if scale == "jst":
        t = t.replace(tzinfo=JST).astimezone(dt.timezone.utc).replace(tzinfo=None)
    return t + GPS_UTC


def window_of_log(gnss_dir):
    """記録のフォルダ → (始まり, 終わり) の UTC 文字列。終わり＝索引（`gnss_index.csv`）の最後の行。"""
    d = Path(gnss_dir)
    head = json.loads((d / "gnss.json").read_text(encoding="utf-8"))
    last = None
    with open(d / "gnss_index.csv", encoding="utf-8") as f:
        for line in f:
            if line[:1].isdigit():
                last = line
    if last is None:
        sys.exit(f"{d / 'gnss_index.csv'} に行がありません")
    return head["started_utc"], last.split(",")[0]


def floor30(t):
    return t.replace(microsecond=0) - dt.timedelta(seconds=t.second % INTERVAL)


def ceil30(t):
    f = floor30(t)
    return f if f == t else f + dt.timedelta(seconds=INTERVAL)


def hours(t0, t1):
    """GPS 時刻の窓が触る (日, 時) を並べる。"""
    h = t0.replace(minute=0, second=0, microsecond=0)
    out = []
    while h <= t1:
        out.append(h)
        h += dt.timedelta(hours=1)
    return out


def session_letter(hour):
    return chr(ord("a") + hour)


# ---------------------------------------------------------------- 取り寄せ

def pick_files(names, code, day, hrs):
    """その日の局のファイルから、窓の時間を全部覆う 1 時間ごとのファイル、無ければ 1 日のファイルを選ぶ。"""
    doy, yy = day.strftime("%j"), day.strftime("%y")
    pat = re.compile(rf"^{code}{doy}([a-x0])\.{yy}[do](\.gz)?$", re.IGNORECASE)
    by_session = {}
    for n in names:
        m = pat.match(n)
        if m:
            s = m.group(1).lower()
            # 同じ時に .d と .o があれば Compact（小さい）を採る
            if s not in by_session or n.lower().endswith(("d.gz", "d")):
                by_session[s] = n
    want = [session_letter(h.hour) for h in hrs]
    if all(s in by_session for s in want):
        return [by_session[s] for s in want]
    if "0" in by_session:
        return [by_session["0"]]
    return None


def fetch_raw(home, root, code, t0, t1):
    """窓が触る日ごとに SFTP の一覧を見て、要るファイルを置き場の cache へ取り、その経路を返す。"""
    cache = Path(home) / "geonet_cache"
    paths = []
    by_day = {}
    for h in hours(t0, t1):
        by_day.setdefault(h.date(), []).append(h)
    for day, hrs in by_day.items():
        rdir = f"{root}/{day.year}/{day.strftime('%j')}"
        names, err = remote_ls(home, f"{rdir}/{code}{day.strftime('%j')}*")
        files = pick_files(names, code, day, hrs)
        if files is None:
            sys.exit(f"{rdir} に局 {code} の {day}（通日 {day.strftime('%j')}）の要るファイルがありません"
                     f"（要る時＝{''.join(session_letter(h.hour) for h in hrs)}）\n"
                     f"  ある: {names}\n" + "\n".join(err))
        local = cache / root.strip("/").replace("/", "_") / str(day.year) / day.strftime("%j")
        local.mkdir(parents=True, exist_ok=True)
        missing = [f for f in files if not (local / f).exists()]
        if missing:
            print(f"  取る {rdir}/ {' '.join(missing)}")
            out, err = sftp(home, [f'lcd "{local}"'] + [f'get "{rdir}/{f}"' for f in missing])
            still = [f for f in missing if not (local / f).exists()]
            if still:
                sys.exit(f"取れませんでした: {still}\n" + "\n".join(err))
        else:
            print(f"  手元にある {local}\\ {' '.join(files)}")
        paths += [local / f for f in files]
    return paths


def header_lines(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    out = {}
    with opener(path, "rt", encoding="ascii", errors="replace") as f:
        for line in f:
            label = line[60:].strip()
            out.setdefault(label, []).append(line[:60])
            if label == "END OF HEADER":
                break
    return out


def count_epochs(path):
    with open(path, encoding="ascii", errors="replace") as f:
        return sum(1 for line in f if line.startswith(">"))


def cmd_fetch(args):
    exe = ringo(args.home)
    if args.from_log:
        s, e = window_of_log(args.from_log)
        t0, t1 = parse_time(s, "utc"), parse_time(e, "utc")
        print(f"記録 {Path(args.from_log).name}＝UTC {s} 〜 {e}")
    elif args.start and args.end:
        t0, t1 = parse_time(args.start, args.time), parse_time(args.end, args.time)
    else:
        sys.exit("時間の窓がありません＝`--from-log <gnss-dir>` か `--start` と `--end` を渡してください")
    if t1 <= t0:
        sys.exit("終わりが始まりより前です")
    pad = dt.timedelta(minutes=args.pad)
    w0, w1 = floor30(t0 - pad), ceil30(t1 + pad)
    print(f"窓（GPS 時刻・前後 {args.pad} 分）＝{w0:%Y-%m-%d %H:%M:%S} 〜 {w1:%Y-%m-%d %H:%M:%S}"
          f"（通日 {w0:%j}・英字 {''.join(session_letter(h.hour) for h in hours(w0, w1))}）")
    root = rinex_root(args.home, args.dir)
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    for raw in args.stations:
        code = raw[-4:]                       # 6 桁（93045）でも 4 桁（3045）でも下 4 桁＝ファイル名の頭
        if not re.fullmatch(r"\d{4}", code):
            sys.exit(f"局番号が読めません: {raw!r}（例: 3045・93045）")
        print(f"局 {code}:")
        files = fetch_raw(args.home, root, code, w0, w1)
        dest = out_dir / f"{code}{w0:%j}{session_letter(w0.hour)}.{w0:%y}o"
        r = subprocess.run([str(exe), "merge",
                            "--stime", f"{w0:%Y/%m/%d %H:%M:%S}", "--etime", f"{w1:%Y/%m/%d %H:%M:%S}",
                            *[str(p) for p in files], "-o", str(dest)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not dest.exists():
            sys.exit(f"RINGO が失敗しました（{r.returncode}）\n{r.stdout}\n{r.stderr}")
        h = header_lines(dest)
        first = " ".join(h.get("TIME OF FIRST OBS", ["?"])[0].split()[:5])
        last = " ".join(h.get("TIME OF LAST OBS", ["?"])[0].split()[:5])
        ant = h.get("ANT # / TYPE", [""])[0][20:40].rstrip()
        systems = "".join(ln[0] for ln in h.get("SYS / # / OBS TYPES", []) if ln[0] != " ")
        n = count_epochs(dest)
        expect = int((w1 - w0).total_seconds()) // INTERVAL + 1
        print(f"  → {dest}\n    {first} 〜 {last}（GPS）・{n} 回（窓なら {expect} 回）"
              f"・衛星系 {systems}・アンテナ {ant!r}")
        if n < expect:
            print(f"    ⚠️ {expect - n} 回欠けている（局の欠測か、まだ上がっていない時間）")


# ---------------------------------------------------------------- 入口

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", default=str(default_home()), help="RINGO・資格情報・取ったファイルの置き場")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup")

    p = sub.add_parser("ls")
    p.add_argument("path")

    p = sub.add_parser("fetch")
    p.add_argument("stations", nargs="+", help="局番号（GEONET の 4 桁か 6 桁）")
    p.add_argument("--from-log", help="`gnss log` の記録のフォルダ＝その始まりと終わりを窓にする")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--time", choices=("utc", "gps", "jst"), default="utc", help="--start/--end の時刻の系")
    p.add_argument("--pad", type=float, default=10.0, help="窓の前後に足す分")
    p.add_argument("--out", required=True)
    p.add_argument("--dir", help="SFTP の観測データの置き場（既定＝/data の下の RINEX 4 を探す）")

    args = ap.parse_args()
    {"setup": cmd_setup, "ls": cmd_ls, "fetch": cmd_fetch}[args.cmd](args)


if __name__ == "__main__":
    main()
