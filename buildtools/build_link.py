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

外の包み（4.0 ステージ8f・オープンコアの殻）:
  `--package 名前=フォルダ`（繰り返せる）で、このリポジトリの外にある包みを `app\\` へ足し、
  `--entry 名前` で起動の口（`pythonw.exe -m 名前`）をその包みへ替える。上に載る非公開の層が
  この道具を借りて自分の配布を作るための口で、この道具はその包みの名前を知らない
  （`tests/test_layers.py` の ⑤＝公開のコードは上に載る層を引かない）。
  足した包みも作った木の python.exe で全モジュールを読む（漏れたら止める）。
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import os
import re
import shutil
import subprocess  # nosec B404 ─ 宣言された解釈系・展開した embeddable・ISCC だけを起こす
import sys
import xml.etree.ElementTree as ET  # nosec B405 ─ 読むのはリポジトリの brand.svg だけ
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
#: 起動の口の既定（`pythonw.exe -m webui`）。`--entry` で外の包みへ替えられる。
DEFAULT_ENTRY = "webui"

#: ロゴとアイコンの元（I-238）。画面はそのまま描き、`icon.ico` は `<g id="icon">` から焼く。
BRAND_SVG = os.path.join(REPO, "webui", "static", "brand.svg")
ICON_ID = "icon"
#: 小さい寸法（`SMALL_ICON_MAX` 以下）は弧を減らした絵から描く＝細い弧が 1px に潰れない（ステージ9f）。
SMALL_ICON_ID = "icon-small"
SMALL_ICON_MAX = 16
ICON_SIZES = (256, 128, 64, 48, 32, 16)
_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
#: Tk の SVG 読み（nanosvg）が描かずに黙って落とす要素＝アイコンの中に置かせない。
_ICON_UNDRAWN = frozenset({"text", "tspan", "style", "image", "foreignObject"})
ET.register_namespace("", _SVG_NS)

#: 依存の配布物から写さないもの（実行に要らない＝試験・ヘッダ・型の控え）。
_SKIP_PARTS = frozenset({"tests", "__pycache__", "include"})
_SKIP_SUFFIX = (".pyi", ".h", ".c", ".pxd", ".lib", ".a")

#: 2 つの解釈系が同じかを比べる問い（版の 5 つ組とポインタの幅＝vendor の .pyd が載るか）。
INTERPRETER_PROBE = "import struct, sys; print(tuple(sys.version_info), struct.calcsize('P') * 8)"

#: ポータブル版の起こし方（zip のときだけ置く）。`start` で起こすので黒い窓は一瞬で閉じる。
#: 作業フォルダを移さない＝基準は `pythonw.exe` の隣（`config.app_base_dir`）。
def launcher_cmd(entry: str = DEFAULT_ENTRY) -> str:
    return f'@echo off\r\nstart "" "%~dp0pythonw.exe" -m {entry}\r\n'


LAUNCHER_CMD = launcher_cmd()


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


def package_sources(extra: Iterable[str] = (), base: str = REPO) -> list[tuple[str, str]]:
    """`app\\` へ入れる包み＝`(名前, 元のフォルダ)`。公開側の 2 つ（`APP_PACKAGES`）が先。

    `extra` は `名前=フォルダ`（`--package`）。フォルダは `base` からの相対でもよい。
    名前が識別子でない・公開側や互いと重なる・`__init__.py` が無い、なら止める。
    """
    out = [(p, os.path.join(REPO, p)) for p in APP_PACKAGES]
    for spec in extra:
        name, sep, folder = spec.partition("=")
        name, folder = name.strip(), folder.strip().strip('"')
        if not sep or not name or not folder:
            raise BuildError(f"--package は 名前=フォルダ の形で渡す: {spec!r}")
        if not name.isidentifier():
            raise BuildError(f"--package の名前が Python の包みの名前でない: {name!r}")
        if any(name == n for n, _ in out):
            raise BuildError(f"--package の名前が既にある包みと重なる: {name!r}")
        path = os.path.abspath(os.path.join(base, folder))
        if not os.path.isfile(os.path.join(path, "__init__.py")):
            raise BuildError(f"--package {name} のフォルダに __init__.py が無い: {path}")
        out.append((name, path))
    return out


def check_entry(entry: str, packages: Iterable[tuple[str, str]]) -> None:
    """起動の口が、入れる包みのどれかで、`__main__.py` を持つこと。"""
    found = dict(packages).get(entry)
    if found is None:
        raise BuildError(f"--entry {entry!r} が入れる包みに無い（--package で足す）")
    if not os.path.isfile(os.path.join(found, "__main__.py")):
        raise BuildError(f"--entry {entry} に __main__.py が無い＝`-m {entry}` で起こせない: {found}")


def icon_svg(brand_text: str, icon_id: str = ICON_ID) -> str:
    """`brand.svg` の `<g id="icon">`（または `icon_id`）を、Tk の SVG 読みが描ける単独の SVG にする（I-238）。

    nanosvg は `<use>` を描かないので、参照先（`mark`）の中身を `<g transform>` に展開する。
    `<text>`・`<style>`・`<image>` は描かれずに黙って消える＝見つけたら止める。
    """
    root = ET.fromstring(brand_text)  # nosec B314 ─ リポジトリの brand.svg（外から来ない）
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}
    icon = by_id.get(icon_id)
    if icon is None:
        raise BuildError(f"brand.svg に <g id=\"{icon_id}\"> が無い")
    box = (icon.get("data-viewbox") or "").split()
    if len(box) != 4:
        raise BuildError(f"<g id=\"{icon_id}\"> に data-viewbox（4 数字）が無い")

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def expand(el: ET.Element, depth: int) -> list[ET.Element]:
        name = local(el.tag)
        if name in _ICON_UNDRAWN:
            raise BuildError(f"アイコンの中に <{name}> がある＝Tk の SVG 読みは描かない（図形で書く）")
        if name == "use":
            href = el.get("href") or el.get(f"{{{_XLINK_NS}}}href") or ""
            target = by_id.get(href[1:]) if href.startswith("#") else None
            if target is None or depth > 8:
                raise BuildError(f"アイコンの <use href=\"{href}\"> の参照先が無いか、深すぎる")
            g = ET.Element(f"{{{_SVG_NS}}}g")
            if el.get("transform"):
                g.set("transform", el.get("transform"))
            for child in target:
                g.extend(expand(child, depth + 1))
            return [g]
        out = ET.Element(el.tag, {k: v for k, v in el.attrib.items()
                                  if k != "id" and not k.startswith("data-")})
        for child in el:
            out.extend(expand(child, depth))
        return [out]

    svg = ET.Element(f"{{{_SVG_NS}}}svg", {"viewBox": " ".join(box),
                                           "width": box[2], "height": box[3]})
    for child in icon:
        svg.extend(expand(child, 0))
    return ET.tostring(svg, encoding="unicode")


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


def render_icon_pngs(svg: str, out_dir: str, sizes: Iterable[int] = ICON_SIZES,
                     small_svg: str | None = None) -> list[str]:
    """単独の SVG を Tk 9 の SVG 読み（nanosvg）で寸法ごとの PNG に描き、パスを大きい順で返す。

    `small_svg` を渡すと、`SMALL_ICON_MAX` 以下の寸法はそちらから描く（弧を減らした絵）。

    縮めるのでなく寸法ごとに描き直す＝16 や 32 でも輪郭が滲まない（I-238）。Tk は宣言の
    Python に入っている＝依存を足さない。build.bat の Tk 9 の関所は `link` より後ろなのでここで見る。
    """
    import tkinter as tk
    if tk.TkVersion < 9:
        raise BuildError(f"アイコンを描くには Tk 9 が要る（SVG を読む）: Tk {tk.TkVersion}")
    paths = []
    root = tk.Tk()
    root.withdraw()
    try:
        for n in sorted(sizes, reverse=True):
            src = small_svg if small_svg is not None and n <= SMALL_ICON_MAX else svg
            img = tk.PhotoImage(master=root, data=src, format=f"svg -scaletoheight {n}")
            if (img.width(), img.height()) != (n, n):
                raise BuildError(f"アイコンが正方形に描けない: {n} → {img.width()}x{img.height()}"
                                 f"（{ICON_ID} の data-viewbox を正方形にする）")
            path = os.path.join(out_dir, f"icon_{n}.png")
            img.write(path, format="png")
            paths.append(path)
    finally:
        root.destroy()
    return paths


def write_icon(dst: str, brand: str = BRAND_SVG) -> None:
    """`brand.svg` のアイコンを `icon.ico`（6 寸法・透過あり）にして `dst` へ書く。"""
    import tempfile

    from PIL import Image
    with open(brand, encoding="utf-8") as f:
        text = f.read()
    svg, small = icon_svg(text), icon_svg(text, SMALL_ICON_ID)
    with tempfile.TemporaryDirectory() as tmp:
        pngs = render_icon_pngs(svg, tmp, small_svg=small)
        imgs = []
        for p in pngs:
            with Image.open(p) as img:
                imgs.append(img.convert("RGBA"))
        # 寸法ごとに描いた絵をそのまま使う（append_images）＝Pillow に縮めさせない
        imgs[0].save(dst, format="ICO", sizes=[im.size for im in imgs], append_images=imgs[1:])


def _write_icon(root: str) -> None:
    """ショートカットとアンインストールの一覧の絵（`icon.ico`）を木に置く。"""
    write_icon(os.path.join(root, "icon.ico"))


def build_tree(dist_dir: str, embed_zip: str, declared: str,
               packages: Iterable[tuple[str, str]] | None = None) -> str:
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
    for pkg, src in (package_sources() if packages is None else packages):
        shutil.copytree(src, os.path.join(app, pkg),
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


def smoke_import(root: str, names: Iterable[str] = APP_PACKAGES) -> None:
    """作った木の python.exe で、入れた包み（既定は `core` と `webui`）の全モジュールを読む（起動はしない）。"""
    code = (
        "import importlib, pkgutil, sys\n"
        "bad = []\n"
        f"for pkg in {tuple(names)!r}:\n"
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


def write_zip(root: str, zip_path: str, entry: str = DEFAULT_ENTRY) -> None:
    """ポータブル版＝portable.txt と起動の .cmd を足して、フォルダごと固める。"""
    with open(os.path.join(root, "portable.txt"), "w", encoding="ascii") as f:
        f.write("\n")
    with open(os.path.join(root, f"{APP_DIR_NAME}.cmd"), "w", encoding="ascii", newline="") as f:
        f.write(launcher_cmd(entry))
    if os.path.exists(zip_path):
        os.remove(zip_path)
    base = os.path.dirname(root)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for top, _dirs, names in os.walk(root):
            for n in names:
                full = os.path.join(top, n)
                zf.write(full, os.path.relpath(full, base))


def installer_command(iscc: str, dist_dir: str, entry: str = DEFAULT_ENTRY) -> list[str]:
    """ISCC へ渡す引数。木の場所（`LinkDir`）も渡す＝`--dist` を変えても同梱元と出力先がずれない。

    起動の口（`EntryModule`）も渡す＝ショートカットと最後の「起動する」が `-m <entry>` になる。
    """
    return [iscc, f"/DAppVersion={version.APP_VERSION}",
            f"/DLinkDir={os.path.join(dist_dir, APP_DIR_NAME)}",
            f"/DEntryModule={entry}",
            f"/O{dist_dir}", os.path.join(REPO, "installer", "link.iss")]


def build_installer(dist_dir: str, iscc: str, entry: str = DEFAULT_ENTRY) -> str:
    subprocess.run(installer_command(iscc, dist_dir, entry), check=True)  # nosec B603 ─ 宣言の ISCC
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
    ap.add_argument("--package", action="append", default=[], metavar="名前=フォルダ",
                    help="このリポジトリの外の包みを app へ足す（繰り返せる・相対は今のフォルダから）")
    ap.add_argument("--entry", default=DEFAULT_ENTRY, metavar="名前",
                    help=f"起動の口＝pythonw.exe -m 名前（既定 {DEFAULT_ENTRY}）")
    a = ap.parse_args(argv)
    try:
        packages = package_sources(a.package, base=os.getcwd())
        check_entry(a.entry, packages)
        declared = check_declared()
        embed_zip = check_embed_zip()
        iscc = check_iscc() if a.mode == "installer" else ""
        check_dependencies()
        print(f"[INFO] {APP_DIR_NAME} {version.APP_VERSION}（{a.mode}・-m {a.entry}）を作る")
        print(f"       embeddable : {embed_zip}")
        for name, src in packages[len(APP_PACKAGES):]:
            print(f"       外の包み   : {name} ← {src}")
        os.makedirs(a.dist, exist_ok=True)
        root = build_tree(a.dist, embed_zip, declared, packages)
        smoke_import(root, [n for n, _ in packages])
        print(f"[OK] フォルダ : {root}")
        if a.mode == "zip":
            zip_path = os.path.join(a.dist, f"{APP_DIR_NAME}-{version.APP_VERSION}.zip")
            write_zip(root, zip_path, a.entry)
            print(f"[OK] zip     : {zip_path}")
        else:
            print(f"[OK] インストーラ : {build_installer(a.dist, iscc, a.entry)}")
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
