"""3 候補を開発機で測り、JSON と表を残す（4.0 ステージ2）。

使い方（製品の外の venv で・リポジトリの外へ書く）:
  D:\\dev\\radiosim\\venv-ui-proto\\Scripts\\python.exe experiments\\ui_proto\\measure.py ^
      --runs 5 --size 1900x950 --out D:\\dev\\radiosim\\ui_proto\\measure

`--exe a=...\\a.exe` を渡すと固めた exe を測る。渡さなければソースのまま測る。
初回＝初めて起こす回（固めた exe は OS のファイルキャッシュが冷えていない・Edge の
プロファイルは消してから）。2 回目以降の中央値を「2 回目」の列に書く。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess  # nosec B404 ─ 自分が作った候補を起こすだけ
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = {
    "a": os.path.join(_HERE, "a_pywebview", "main.py"),
    "b": os.path.join(_HERE, "b_edge_app", "main.py"),
    "c": os.path.join(_HERE, "c_tk", "main.py"),
}
_PROTO_DIR = os.path.join(os.environ["LOCALAPPDATA"], "RadioSimUiProto")
_TIMEOUT_S = 120


def _run_once(cmd: list[str], out: str, size: str, offline: bool) -> dict | None:
    if os.path.exists(out):
        os.remove(out)
    args = cmd + ["--measure", out, "--size", size]
    if offline:
        args.append("--offline")
    started = time.time()
    proc = subprocess.Popen(args)  # nosec B603 ─ 上の定数と引数のパスだけ
    while time.time() - started < _TIMEOUT_S:
        if os.path.exists(out):
            break
        time.sleep(0.05)
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()
    if not os.path.exists(out):
        print(f"  ✖ 測れなかった: {' '.join(cmd)}")
        return None
    time.sleep(0.2)
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def _startup_ms(m: dict) -> float | None:
    if "startup_ms" in m:
        return m["startup_ms"]
    if "first_verdict_ms" in m and "page_origin_ms" in m:
        return m["page_origin_ms"] + m["first_verdict_ms"] - m["process_start_epoch_ms"]
    return None


def _row(key: str, runs: list[dict]) -> dict:
    first, rest = runs[0], runs[1:] or runs
    latency = [r["input_ms"]["p50"] for r in rest if "input_ms" in r]
    latency95 = [r["input_ms"]["p95"] for r in rest if "input_ms" in r]
    fps = [r["pan_fps"] for r in rest if r.get("pan_fps")]
    return {
        "candidate": key,
        "first_start_ms": _startup_ms(first),
        "second_start_ms": statistics.median(_startup_ms(r) for r in rest),
        "input_p50_ms": round(statistics.median(latency), 1) if latency else None,
        "input_p95_ms": round(statistics.median(latency95), 1) if latency95 else None,
        "pan_fps": statistics.median(fps) if fps else None,
        "overflow": first.get("overflow"),
        "client": first.get("client"),
        "tiles": runs[-1].get("tiles"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--size", default="1900x950")
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="abc")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--exe", action="append", default=[], help="a=パス")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    exes = dict(item.split("=", 1) for item in args.exe)
    table = []
    for key in args.only:
        cmd = [exes[key]] if key in exes else [sys.executable, _CANDIDATES[key]]
        shutil.rmtree(os.path.join(_PROTO_DIR, "edge_profile"), ignore_errors=True)
        runs = []
        for i in range(args.runs):
            data = _run_once(cmd, os.path.join(args.out, f"{key}_{i}.json"), args.size, args.offline)
            if data is None:
                break
            runs.append(data)
            print(f"  {key}#{i}: 起動→判定 {_startup_ms(data)} ms")
        if runs:
            table.append(_row(key, runs))
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False, indent=1)
    print(json.dumps(table, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
