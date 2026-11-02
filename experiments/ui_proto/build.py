"""3 候補を PyInstaller で固め、候補ごとに zip を作る（4.0 ステージ2）。

使い方（製品の外の venv で・出力はリポジトリの外）:
  D:\\dev\\radiosim\\venv-ui-proto\\Scripts\\python.exe experiments\\ui_proto\\build.py ^
      --out D:\\dev\\radiosim\\ui_proto

製品の `radiosim.spec` と `build.bat` は使わない＝試作は製品の配布物に混ぜない。
A・B は Tk と matplotlib を除き、B はさらに pywebview と pythonnet を除く
＝zip の大きさが「その道具だけで要る量」になるように。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404 ─ 同じ venv の PyInstaller を起こすだけ
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))

_TK = ["tkinter", "_tkinter", "matplotlib", "sv_ttk", "tkintermapview"]
_WEBVIEW = ["webview", "clr", "clr_loader", "pythonnet"]
_COMMON_EXCLUDES = ["pytest", "IPython", "pandas"]

_TARGETS = {
    "a": {"name": "RadioSimProtoA", "script": "a_pywebview/main.py",
          "excludes": _TK, "web": True, "collect": ["webview"]},
    "b": {"name": "RadioSimProtoB", "script": "b_edge_app/main.py",
          "excludes": _TK + _WEBVIEW, "web": True, "collect": []},
    "c": {"name": "RadioSimProtoC", "script": "c_tk/main.py",
          "excludes": _WEBVIEW, "web": False, "collect": ["sv_ttk", "tkintermapview"]},
}


def _build(key: str, out: str) -> dict:
    t = _TARGETS[key]
    dist, work = os.path.join(out, "dist"), os.path.join(out, "build")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
            "--noconsole", "--name", t["name"], "--distpath", dist, "--workpath", work,
            "--specpath", work, "--paths", _HERE, "--paths", _REPO]
    for mod in t["excludes"] + _COMMON_EXCLUDES:
        args += ["--exclude-module", mod]
    for pkg in t["collect"]:
        args += ["--collect-all", pkg]
    if t["web"]:
        args += ["--add-data", f"{os.path.join(_HERE, 'web')}{os.pathsep}web"]
    args.append(os.path.join(_HERE, t["script"]))
    subprocess.run(args, check=True, cwd=_REPO)  # nosec B603 ─ 上の定数だけ

    folder = os.path.join(dist, t["name"])
    zip_path = os.path.join(out, f"{t['name']}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    files = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, _dirs, names in os.walk(folder):
            for n in names:
                full = os.path.join(root, n)
                zf.write(full, os.path.relpath(full, dist))
                files += 1
    folder_bytes = sum(os.path.getsize(os.path.join(r, n))
                       for r, _d, ns in os.walk(folder) for n in ns)
    return {"candidate": key, "exe": os.path.join(folder, t["name"] + ".exe"),
            "zip": zip_path, "zip_mb": round(os.path.getsize(zip_path) / 2**20, 1),
            "folder_mb": round(folder_bytes / 2**20, 1), "files": files}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="abc")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    rows = []
    for key in args.only:
        shutil.rmtree(os.path.join(args.out, "dist", _TARGETS[key]["name"]), ignore_errors=True)
        rows.append(_build(key, args.out))
    with open(os.path.join(args.out, "build_summary.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
