"""
buildtools/build_link.py
========================
新しい画面（RadioSim Link）の配布を **embeddable Python** で作る（4.0 ステージ8d・I-144）。

`build.bat link`（ポータブル版の zip）と `build.bat link-installer`（インストーラ）の本体。
build.bat は ASCII 専用なので、日本語の説明と判断はすべてここに置く（B-020 の規則）。

作るもの（名前の `RadioSimLink` は仮＝ステージ8e の改名で決める）:

  dist\\RadioSimLink\\               python.exe・pythonw.exe・標準ライブラリの zip（embeddable の展開）
    python3XX._pth                  sys.path＝標準ライブラリ・`.`・`vendor`・`app` だけ
    vendor\\                         実行時の依存 8 つ（`RUNTIME_DISTS`）
    app\\core\\  app\\webui\\           製品のソース（.py と、前もって作った .pyc）
    LICENSE・icon.ico・version.txt   version.txt＝`version_tuple()` の 4 数字（インストーラの版比べ＝I-176）
  dist\\RadioSimLink-<版>.zip        zip のときだけ＝フォルダに portable.txt と RadioSimLink.cmd を足して固める
  dist\\RadioSimLink-Setup-<版>.exe  インストーラのとき（installer\\link.iss を ISCC 7 で）

🔑 自前の exe を作らない＝起動は PSF の署名がある `pythonw.exe -m webui`。PyInstaller の exe が
Smart App Control に 2 回止められた（3.9.1RC1・4.0 の試作）のが選んだ理由（台帳 I-144）。

宣言制（B-020 と同じ考え＝探さない・食い違ったら止める）:
  - `RADIOSIM_PYTHON`＝検証に使った唯一の Python。この道具はそれで走っていなければ止める
    （依存はこの解釈系の site-packages から写す＝検証した版と配る版を同じにする）
  - `RADIOSIM_EMBED_ZIP`＝python.org の embeddable の zip。未設定・実体なしで止める。
    展開した python.exe の版が `RADIOSIM_PYTHON` と違えば止める（vendor の .pyd は cp3XX 用）
  - `requirements.txt` に固定のある依存は、写す版が固定と違えば止める
  - 依存 8 つの `Requires-Dist` が 8 つの中で閉じていなければ止める（上流が依存を足したとき）
  - 作った木の python.exe で `core` と `webui` の全モジュールを読み、読めなければ止める
    （開発機には全部が入っているので、ソース実行の試験ではこの漏れが見えない＝B-036 と同じ）
  - インストーラのとき `RADIOSIM_ISCC`＝Inno Setup 7（別の版は `RADIOSIM_ISCC_ANY_VERSION=1`）
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import os
import re
import shutil
import subprocess  # nosec B404 ─ 宣言された解釈系・展開した embeddable・ISCC だけを起こす
import sys
import zipfile
from collections.abc import Callable, Iterable

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from core import runtime_env, version  # noqa: E402  ─ 上の sys.path の後で読む

#: 配布の名前（仮）。フォルダ・zip・インストーラ・起動の .cmd に使う＝8e の改名で 1 か所を替える。
APP_DIR_NAME = "RadioSimLink"

EMBED_ENV = "RADIOSIM_EMBED_ZIP"
ISCC_ENV = "RADIOSIM_ISCC"
ISCC_ANY_ENV = "RADIOSIM_ISCC_ANY_VERSION"
SIGNTOOL_ENV = "RADIOSIM_SIGNTOOL"
ISCC_BANNER = "Inno Setup 7 Command-Line Compiler"

#: 新しい画面が実行時に読む外部の部品（2026-11-07 に起動〜通信の後まで `sys.modules` で数えた）。
#: 増やすときは `tests/test_build_link.py` の閉包の試験が、足りない依存を名前で言う。
RUNTIME_DISTS = ("numpy", "pillow", "requests", "urllib3", "certifi", "charset-normalizer",
                 "idna", "truststore")
APP_PACKAGES = ("core", "webui")

#: 依存の配布物から写さないもの（実行に要らない＝試験・ヘッダ・型の控え）。
_SKIP_PARTS = frozenset({"tests", "__pycache__", "include"})
_SKIP_SUFFIX = (".pyi", ".h", ".c", ".pxd", ".lib", ".a")

#: 2 つの解釈系が同じかを比べる問い（版の 5 つ組とポインタの幅＝vendor の .pyd が載るか）。
INTERPRETER_PROBE = "import struct, sys; print(tuple(sys.version_info), struct.calcsize('P') * 8)"

#: ポータブル版の起こし方（zip のときだけ置く）。`start` で起こすので黒い窓は一瞬で閉じる。
#: 作業フォルダを移さない＝基準は `pythonw.exe` の隣（`config.app_base_dir`）。
LAUNCHER_CMD = '@echo off\r\nstart "" "%~dp0pythonw.exe" -m webui\r\n'


class BuildError(Exception):
    """ビルドを止める理由（人に見せる 1 文）。"""


# ---------------------------------------------------------------------------
# 純粋な部分（tests/test_build_link.py が見る）
# ---------------------------------------------------------------------------

def normalize(name: str) -> str:
    """配布物の名前を比べられる形へ（PEP 503＝大小・`-`・`_`・`.` を揃える）。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(lines: Iterable[str]) -> dict[str, str]:
    """`requirements.txt` の `名前==版` だけを拾う（コメント・空行・`>=` などは無視）。"""
    pins: dict[str, str] = {}
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;]+)\s*(?:;.*)?", line)
        if m:
            pins[normalize(m.group(1))] = m.group(2)
    return pins


def pin_mismatches(dists: Iterable[str], pins: dict[str, str],
                   installed: Callable[[str], str]) -> list[str]:
    """固定のある依存のうち、写す版が固定と違うものを `名前 版（固定 版）` で返す。"""
    out = []
    for name in dists:
        want = pins.get(normalize(name))
        if want is None:
            continue
        have = installed(name)
        if have != want:
            out.append(f"{name} {have}（requirements.txt は {want}）")
    return out


def requirement_name(spec: str) -> str | None:
    """`Requires-Dist` の 1 行から、無条件に要る配布物の名前を取り出す。

    extra（`; extra == "socks"` など＝付けたときだけ要る）は None。環境の条件
    （`python_version`・`sys_platform`）は保守的に「要る」と読む。
    """
    name, _, marker = spec.partition(";")
    if "extra" in marker:
        return None
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", name)
    return normalize(m.group(1)) if m else None


def missing_dependencies(dists: Iterable[str],
                         requires: Callable[[str], Iterable[str]]) -> list[str]:
    """写す依存の `Requires-Dist` のうち、写す集合に無いものを `要る物 ← 求めた物` で返す。"""
    have = {normalize(d) for d in dists}
    out = []
    for name in dists:
        for spec in requires(name):
            dep = requirement_name(spec)
            if dep is not None and dep not in have:
                out.append(f"{dep} ← {name}")
    return sorted(set(out))


def pth_lines(stdlib_zips: Iterable[str]) -> list[str]:
    """embeddable の `._pth` に書く行＝標準ライブラリの zip・`.`・`vendor`・`app` だけ。

    ⚠️ `import site` を書かない＝利用者の site-packages（`%APPDATA%\\Python`）を読まない。
    """
    zips = sorted(stdlib_zips)
    if len(zips) != 1:
        raise BuildError(f"標準ライブラリの zip が 1 つではない: {zips}")
    return [zips[0], ".", "vendor", "app"]


def version_numbers(v: str) -> str:
    """`version_tuple()` の 4 数字を `.` で（インストーラが比べる＝正式版が最大・B-162）。"""
    return ".".join(str(n) for n in version.version_tuple(v))


def skip_file(rel: str) -> bool:
    """依存の配布物のうち、写さないファイルか。"""
    parts = rel.replace("\\", "/").split("/")
    return bool(_SKIP_PARTS.intersection(parts[:-1])) or rel.endswith(_SKIP_SUFFIX)


# ---------------------------------------------------------------------------
# 確かめ（宣言・版・ISCC）
# ---------------------------------------------------------------------------

def _run_probe(python: str) -> str:
    done = subprocess.run([python, "-c", INTERPRETER_PROBE],  # nosec B603 ─ 定数の問い
                          capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise BuildError(f"解釈系に版を聞けない: {python}\n{done.stderr.strip()}")
    return done.stdout.strip()


def check_declared() -> str:
    """宣言された Python で走っているか。宣言の python.exe を返す。"""
    declared = runtime_env.declared_interpreter()
    if not declared or not os.path.isfile(declared):
        raise BuildError(f"{runtime_env.DECLARED_ENV} が未設定か、実体が無い: {declared!r}")
    if not runtime_env.same_interpreter(declared, sys.executable):
        raise BuildError(f"宣言された Python で走っていない＝依存の版が検証と違い得る\n"
                         f"  宣言 : {declared}\n  実行 : {sys.executable}")
    return declared


def check_embed_zip() -> str:
    path = os.environ.get(EMBED_ENV, "").strip().strip('"')
    if not path or not os.path.isfile(path):
        raise BuildError(
            f"{EMBED_ENV} が未設定か、実体が無い: {path!r}\n"
            f"  python.org の Windows embeddable package（64-bit）の zip を指す。版は "
            f"{runtime_env.DECLARED_ENV} と同じにする（例 python-3.15.0-embed-amd64.zip）")
    return path


def check_dependencies() -> None:
    with open(os.path.join(REPO, "requirements.txt"), encoding="utf-8") as f:
        pins = read_pins(f)
    bad = pin_mismatches(RUNTIME_DISTS, pins, md.version)
    if bad:
        raise BuildError("依存の版が requirements.txt の固定と違う（pip install -r requirements.txt）:\n  "
                         + "\n  ".join(bad))
    missing = missing_dependencies(RUNTIME_DISTS, lambda n: md.requires(n) or [])
    if missing:
        raise BuildError("写す依存が閉じていない（RUNTIME_DISTS に足す）:\n  " + "\n  ".join(missing))


def check_iscc() -> str:
    iscc = os.environ.get(ISCC_ENV, "").strip().strip('"')
    if not iscc or not os.path.isfile(iscc):
        raise BuildError(f"{ISCC_ENV} が未設定か、実体が無い: {iscc!r}\n"
                         f"  winget install JRSoftware.InnoSetup.7 で入れ、その ISCC.exe を指す")
    if not os.environ.get(ISCC_ANY_ENV):
        done = subprocess.run([iscc], capture_output=True, text=True,  # nosec B603 ─ 宣言の ISCC
                              check=False, errors="replace")
        if ISCC_BANNER not in (done.stdout + done.stderr):
            raise BuildError(f"{ISCC_ENV} が Inno Setup 7 の ISCC ではない: {iscc}\n"
                             f"  別の版で作るときだけ {ISCC_ANY_ENV}=1 を宣言する（I-125）")
    return iscc


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------

def _copy_dist(name: str, vendor: str) -> None:
    for f in md.distribution(name).files or []:
        rel = str(f)
        if rel.startswith("..") or skip_file(rel):
            continue
        src = str(f.locate())
        if not os.path.isfile(src):
            continue
        dst = os.path.join(vendor, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def _write_icon(root: str) -> None:
    """`icon.png` から `icon.ico` を作って置く（ショートカットとアンインストールの一覧の絵）。"""
    from PIL import Image
    png = os.path.join(REPO, "icon.png")
    with Image.open(png) as img:
        img.convert("RGBA").save(os.path.join(root, "icon.ico"), format="ICO",
                                 sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32),
                                        (16, 16)])


def build_tree(dist_dir: str, embed_zip: str, declared: str) -> str:
    root = os.path.join(dist_dir, APP_DIR_NAME)
    shutil.rmtree(root, ignore_errors=True)
    if os.path.exists(root):
        raise BuildError(f"前のビルドを消せない（開いているファイルがある？）: {root}")
    os.makedirs(root)
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(root)
    py = os.path.join(root, "python.exe")
    if not os.path.isfile(py):
        raise BuildError(f"{EMBED_ENV} の zip に python.exe が無い: {embed_zip}")
    want, have = _run_probe(declared), _run_probe(py)
    if want != have:
        raise BuildError(f"embeddable の版が {runtime_env.DECLARED_ENV} と違う\n"
                         f"  宣言     : {want}\n  embeddable: {have}")

    pth = [n for n in os.listdir(root) if n.endswith("._pth")]
    if len(pth) != 1:
        raise BuildError(f"embeddable の ._pth が 1 つではない: {pth}")
    stdlib = [n for n in os.listdir(root) if n.startswith("python") and n.endswith(".zip")]
    with open(os.path.join(root, pth[0]), "w", encoding="ascii", newline="\r\n") as f:
        f.write("\n".join(pth_lines(stdlib)) + "\n")

    vendor, app = os.path.join(root, "vendor"), os.path.join(root, "app")
    for name in RUNTIME_DISTS:
        _copy_dist(name, vendor)
    for pkg in APP_PACKAGES:
        shutil.copytree(os.path.join(REPO, pkg), os.path.join(app, pkg),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(os.path.join(REPO, "LICENSE"), root)
    _write_icon(root)
    with open(os.path.join(root, "version.txt"), "w", encoding="ascii", newline="\r\n") as f:
        f.write(version_numbers(version.APP_VERSION) + "\n")

    # .pyc を前もって作る＝Program Files では書けないので毎回コンパイルし直すのを避ける。
    # unchecked-hash＝インストーラが時刻を変えても読む（ソースと一緒に入れ替わるだけ）。
    subprocess.run([py, "-m", "compileall", "-q", "-j", "0",  # nosec B603 ─ 展開した embeddable
                    "--invalidation-mode", "unchecked-hash", vendor, app], check=True)
    return root


def smoke_import(root: str) -> None:
    """作った木の python.exe で、`core` と `webui` の全モジュールを読む（起動はしない）。"""
    code = (
        "import importlib, pkgutil, sys\n"
        "bad = []\n"
        f"for pkg in {APP_PACKAGES!r}:\n"
        "    mod = importlib.import_module(pkg)\n"
        "    for info in pkgutil.walk_packages(mod.__path__, pkg + '.'):\n"
        "        if info.name.endswith('__main__'):\n"
        "            continue\n"
        "        try:\n"
        "            importlib.import_module(info.name)\n"
        "        except Exception as e:\n"
        "            bad.append(f'{info.name}: {type(e).__name__}: {e}')\n"
        "print('\\n'.join(bad))\n"
        "sys.exit(1 if bad else 0)\n"
    )
    done = subprocess.run([os.path.join(root, "python.exe"), "-c", code],  # nosec B603
                          capture_output=True, text=True, check=False, cwd=root,
                          errors="replace")
    if done.returncode != 0:
        raise BuildError("作った木で読めないモジュールがある（依存の写し漏れ）:\n"
                         + (done.stdout + done.stderr).strip())


def write_zip(root: str, zip_path: str) -> None:
    """ポータブル版＝portable.txt と起動の .cmd を足して、フォルダごと固める。"""
    with open(os.path.join(root, "portable.txt"), "w", encoding="ascii") as f:
        f.write("\n")
    with open(os.path.join(root, f"{APP_DIR_NAME}.cmd"), "w", encoding="ascii", newline="") as f:
        f.write(LAUNCHER_CMD)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    base = os.path.dirname(root)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for top, _dirs, names in os.walk(root):
            for n in names:
                full = os.path.join(top, n)
                zf.write(full, os.path.relpath(full, base))


def installer_command(iscc: str, dist_dir: str) -> list[str]:
    """ISCC へ渡す引数。木の場所（`LinkDir`）も渡す＝`--dist` を変えても同梱元と出力先がずれない。"""
    return [iscc, f"/DAppVersion={version.APP_VERSION}",
            f"/DLinkDir={os.path.join(dist_dir, APP_DIR_NAME)}",
            f"/O{dist_dir}", os.path.join(REPO, "installer", "link.iss")]


def build_installer(dist_dir: str, iscc: str) -> str:
    subprocess.run(installer_command(iscc, dist_dir), check=True)  # nosec B603 ─ 宣言の ISCC
    setup = os.path.join(dist_dir, f"{APP_DIR_NAME}-Setup-{version.APP_VERSION}.exe")
    if not os.path.isfile(setup):
        raise BuildError(f"ISCC は成功を返したが、インストーラが無い: {setup}")
    signtool = os.environ.get(SIGNTOOL_ENV, "").strip().strip('"')
    if signtool:  # 署名しない決定（I-140）の口だけ＝build.bat と同じ呼び方
        subprocess.run(["cmd", "/c", signtool, setup], check=True)  # nosec B603 B607
    return setup


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RadioSim Link を embeddable Python で配布の形にする")
    ap.add_argument("--mode", choices=("zip", "installer"), default="zip")
    ap.add_argument("--dist", default=os.path.join(REPO, "dist"))
    a = ap.parse_args(argv)
    try:
        declared = check_declared()
        embed_zip = check_embed_zip()
        iscc = check_iscc() if a.mode == "installer" else ""
        check_dependencies()
        print(f"[INFO] {APP_DIR_NAME} {version.APP_VERSION}（{a.mode}）を作る")
        print(f"       embeddable : {embed_zip}")
        os.makedirs(a.dist, exist_ok=True)
        root = build_tree(a.dist, embed_zip, declared)
        smoke_import(root)
        print(f"[OK] フォルダ : {root}")
        if a.mode == "zip":
            zip_path = os.path.join(a.dist, f"{APP_DIR_NAME}-{version.APP_VERSION}.zip")
            write_zip(root, zip_path)
            print(f"[OK] zip     : {zip_path}")
        else:
            print(f"[OK] インストーラ : {build_installer(a.dist, iscc)}")
    except BuildError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] 子の処理が失敗した（終了コード {e.returncode}）: {e.cmd}", file=sys.stderr)
        return 1
    print("[SUCCESS] Link build complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
