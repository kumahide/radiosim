"""新しい画面だけの配布を 2 つの形で作り、大きさを数える（4.0 ステージ8d・I-144）。

- `pyi`＝PyInstaller の onedir（`RadioSimLink.exe`・tkinter と matplotlib を外す）
- `embed`＝python.org の embeddable Python＋実行時の依存だけ＋ソース（自前の exe なし＝
  `pythonw.exe -m webui` を起こす）。.pyc は前もって作る（Program Files では書けない＝毎回
  コンパイルし直すのを避ける・`unchecked-hash`＝インストーラが時刻を変えても読む）

使い方（開発機の venv で・出力はリポジトリの外）:
  & $env:RADIOSIM_PYTHON experiments\\stage8d_packaging\\build.py --out D:\\dev\\radiosim\\stage8d ^
      --embed-zip D:\\dev\\radiosim\\embeddable_poc\\python-3.15.0-embed-amd64.zip

製品の `radiosim.spec`・`build.bat`・`.iss` は使わない（経営者が形を選んでから替える）。
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import os
import shutil
import subprocess  # nosec B404 ─ 同じ venv の PyInstaller と、作った embeddable の compileall だけ
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))

# 新しい画面が実行時に読む外部の部品（2026-11-07 に起動〜通信の後まで sys.modules で数えた）
_DISTS = ["numpy", "pillow", "requests", "urllib3", "certifi", "charset-normalizer", "idna",
          "truststore"]
_APP_PACKAGES = ["core", "webui"]
_PYI_EXCLUDES = ["tkinter", "_tkinter", "matplotlib", "sv_ttk", "tkintermapview", "darkdetect",
                 "markdown", "views", "report", "pytest", "IPython", "pandas"]
# 実行に要らない物（PyInstaller も読まない＝比べる量をそろえる）
_SKIP_PARTS = {"tests", "__pycache__", "include"}
_SKIP_SUFFIX = (".pyi", ".h", ".c", ".pxd", ".lib", ".a")


def _skip(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    return bool(_SKIP_PARTS.intersection(parts[:-1])) or rel.endswith(_SKIP_SUFFIX)


def _zip_dir(folder: str, zip_path: str) -> None:
    if os.path.exists(zip_path):
        os.remove(zip_path)
    base = os.path.dirname(folder)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _dirs, names in os.walk(folder):
            for n in names:
                full = os.path.join(root, n)
                zf.write(full, os.path.relpath(full, base))


def build_pyi(out: str) -> str:
    dist, work = os.path.join(out, "pyi", "dist"), os.path.join(out, "pyi", "build")
    shutil.rmtree(os.path.join(dist, "RadioSimLink"), ignore_errors=True)
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--noconsole", "--name", "RadioSimLink", "--distpath", dist, "--workpath", work,
            "--specpath", work, "--paths", _REPO, "--paths", _HERE,
            "--hidden-import", "probe",
            "--add-data", f"{os.path.join(_REPO, 'webui', 'static')}{os.pathsep}webui/static",
            "--add-data", f"{os.path.join(_REPO, 'LICENSE')}{os.pathsep}."]
    for mod in _PYI_EXCLUDES:
        args += ["--exclude-module", mod]
    args.append(os.path.join(_HERE, "entry.py"))
    subprocess.run(args, check=True, cwd=_REPO)  # nosec B603 ─ 上の定数だけ
    return os.path.join(dist, "RadioSimLink")


def _copy_dist(name: str, vendor: str) -> None:
    d = md.distribution(name)
    for f in d.files or []:
        rel = str(f)
        if rel.startswith("..") or _skip(rel):
            continue
        src = str(f.locate())
        if not os.path.isfile(src):
            continue
        dst = os.path.join(vendor, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def build_embed(out: str, embed_zip: str) -> str:
    root = os.path.join(out, "embed", "RadioSimLink")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(root)
    vendor, app = os.path.join(root, "vendor"), os.path.join(root, "app")
    for name in _DISTS:
        _copy_dist(name, vendor)
    for pkg in _APP_PACKAGES:
        shutil.copytree(os.path.join(_REPO, pkg), os.path.join(app, pkg),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(os.path.join(_REPO, "LICENSE"), root)
    shutil.copy2(os.path.join(_HERE, "probe.py"), app)
    pth = [p for p in os.listdir(root) if p.endswith("._pth")]
    if len(pth) != 1:
        raise SystemExit(f"._pth が 1 つではない: {pth}")
    stdlib_zip = [p for p in os.listdir(root) if p.startswith("python") and p.endswith(".zip")]
    with open(os.path.join(root, pth[0]), "w", encoding="ascii", newline="\r\n") as f:
        f.write("\n".join(stdlib_zip + [".", "vendor", "app"]) + "\n")
    py = os.path.join(root, "python.exe")
    subprocess.run([py, "-m", "compileall", "-q", "-j", "0", "--invalidation-mode",  # nosec B603
                    "unchecked-hash", vendor, app], check=True)
    return root


def summary(kind: str, folder: str, out: str) -> dict:
    zip_path = os.path.join(out, f"RadioSimLink-{kind}.zip")
    _zip_dir(folder, zip_path)
    files = [os.path.join(r, n) for r, _d, ns in os.walk(folder) for n in ns]
    by_top: dict[str, int] = {}
    for p in files:
        top = os.path.relpath(p, folder).split(os.sep)[0]
        by_top[top] = by_top.get(top, 0) + os.path.getsize(p)
    big = sorted(by_top.items(), key=lambda kv: -kv[1])[:8]
    return {"kind": kind, "folder": folder, "files": len(files),
            "folder_mb": round(sum(os.path.getsize(p) for p in files) / 2**20, 1),
            "zip_mb": round(os.path.getsize(zip_path) / 2**20, 1),
            "largest": {k: round(v / 2**20, 1) for k, v in big}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--embed-zip", required=True)
    ap.add_argument("--only", default="pyi,embed")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rows = []
    if "pyi" in a.only:
        rows.append(summary("pyi", build_pyi(a.out), a.out))
    if "embed" in a.only:
        rows.append(summary("embed", build_embed(a.out, a.embed_zip), a.out))
    with open(os.path.join(a.out, "build_summary.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
