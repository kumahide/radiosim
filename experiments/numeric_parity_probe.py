"""
experiments/numeric_parity_probe.py
===================================
**実行環境（Python・numpy・依存の版）を替えても計算結果がビット単位で同じか**を
確かめる探針。版の頭で依存を替える回（3.8 の numpy 2.4.4→2.5.3・3.9 の
CPython 3.15＋Tk 9＋numpy 2.5）に、**新旧 2 つの venv で同じスクリプトを回して
出力を突き合わせる**。

なぜ残すか
----------
3.8 では同じ比較を使い捨ての道具で回し、3.9 の完了条件「3.8 の比較と同じ方法で
ビット単位で一致」を満たすために作り直す羽目になった＝**条件を入れ替えて再判定
できる形で残す**（→ experiments/README.md）。

何を比べるか（3.8 の比較と同じ範囲）
------------------------------------
- ゴールデンコーパスの全回線（3.9 は 32 本・4.0 で 31 本＝B-175）を**標高配列から**再計算（ネットワークに触らない）
- 各回線でスイープ 9 軸×41 点（値域いっぱい・周波数は 30 MHz〜100 GHz の対数）
- 各回線で比較 40 条件（env 4×回折 2×k 5）
- 生の値＝`TerrainProfile` と `LinkBudgetResult` の全項目（配列の要素まで）を
  `float.hex` で書く＝NaN の位置・符号つきゼロまで区別する
- 成果物＝**製品の書き手そのもの**で書いた `summary.csv`・`terrain_profile.csv`・
  `scenario.csv`（スイープ・比較）・`report.txt`（`Date:` の 1 行は実行時刻なので
  比べるときに外す）

使い方
------
    <旧 venv の python> experiments/numeric_parity_probe.py dump <出力先A>
    <新 venv の python> experiments/numeric_parity_probe.py dump <出力先B>
    python experiments/numeric_parity_probe.py compare <出力先A> <出力先B>

出力先はリポジトリの外（スクラッチ）を使う。compare は差が 1 つでもあれば
終了コード 1。
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys

import numpy as np

_REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _REPO)

from core import models               # noqa: E402
from core import scenario as scn      # noqa: E402
from core import simulation as sim    # noqa: E402
from report import report_scenario    # noqa: E402
from report import report_summary     # noqa: E402
from report.batch import PathResult, PathRow   # noqa: E402

_CORPUS = os.path.join(_REPO, "tests", "data", "golden_links.json")

_POINTS = scn.MAX_SWEEP_POINTS
# スイープの値域＝製品の検証表（config.VALIDATION_RULES）いっぱい。周波数だけは
# 1 MHz からの線形だと低域が 1 点しか入らないので、30 MHz〜100 GHz の対数で振る。
_SWEEP_RANGES: dict[str, tuple[float, float]] = {
    "p_tx": (-30.0, 60.0), "gain_tx": (0.0, 60.0), "gain_rx": (0.0, 60.0),
    "sens": (-130.0, -20.0), "h_tx": (0.0, 500.0), "h_rx": (0.0, 500.0),
    "veg_h": (0.0, 100.0), "rain_rate": (0.0, 200.0),
}
_COMPARE_K = (0.0, 1.0, 1.33, 10.0, 30.0)


def _params(inp: dict) -> sim.SimParams:
    # tests/test_golden_links.py の `_params` と同じ組み方（段階を持つ行は段階で）。
    return sim.SimParams({
        "start": f"{inp['lat_tx']}, {inp['lon_tx']}",
        "end":   f"{inp['lat_rx']}, {inp['lon_rx']}",
        "h_tx": str(inp["h_tx"]), "h_rx": str(inp["h_rx"]),
        "freq": str(inp["freq_mhz"]), "p_tx": str(inp["p_tx"]),
        "gain_tx": str(inp["gain_tx"]), "gain_rx": str(inp["gain_rx"]),
        "sens": str(inp["sens"]), "veg_h": str(inp["veg_h"]),
        "k_factor": str(inp["k_factor"]),
        **({"resolution": inp["resolution"]} if inp.get("resolution")
           else {"samples": str(inp["samples"])}),
        "env_type": inp["env_type"], "diff_method": inp["diff_method"],
        "rain_rate": str(inp["rain_rate"]),
    })


def _enc(v):
    """値を版に依らない JSON へ落とす（浮動小数点は `float.hex`）。"""
    if isinstance(v, np.ndarray):
        return {"dtype": str(v.dtype), "shape": list(v.shape),
                "data": [_enc(x) for x in v.ravel().tolist()]}
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (float, np.floating)):
        return {"f": float(v).hex()}
    if isinstance(v, (int, np.integer)):
        return int(v)
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _enc(x) for k, x in v.items()}
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: _enc(getattr(v, f.name)) for f in dataclasses.fields(v)}
    return {"repr": repr(v)}


def _sweep_values(axis: str) -> list[float]:
    if axis == "freq_mhz":
        return [float(x) for x in np.geomspace(30.0, 100000.0, _POINTS)]
    lo, hi = _SWEEP_RANGES[axis]
    return scn.linspace_values(lo, hi, _POINTS)


def _compare_conditions() -> list[scn.Condition]:
    conds = []
    for env in sorted(models.ENV_COEFFS):
        for diff in ("single", "bullington"):
            for k in _COMPARE_K:
                conds.append(scn.Condition(
                    label=f"{env}/{diff}/k{k:g}",
                    overrides={"env_type": env, "diff_method": diff, "k_factor": k}))
    return conds


def dump(out: str) -> None:
    import tkinter
    os.makedirs(out, exist_ok=True)
    with open(_CORPUS, encoding="utf-8") as f:
        links = json.load(f)["links"]

    raw: dict[str, dict] = {}
    path_results: list[PathResult] = []
    compare = _compare_conditions()
    for link in links:
        lid = link["id"]
        params = _params(link["input"])
        terrain = models.calculate_terrain_profile(
            raw_elevs=np.array(link["raw_elevs"], dtype=float),
            lat_tx=params.lat_tx, lon_tx=params.lon_tx,
            lat_rx=params.lat_rx, lon_rx=params.lon_rx,
            frac_axis=params.sample_fracs,
        )
        result = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
        entry: dict = {"terrain": _enc(terrain), "result": _enc(result)}

        link_dir = os.path.join(out, "links", lid)
        os.makedirs(link_dir, exist_ok=True)
        sim._save_terrain_csv(terrain, link_dir, params.dem_source)
        sim._save_report(result, params, params.h_tx, params.h_rx, link_dir,
                         "dd", terrain)

        for axis in scn.SWEEP_AXES:
            values = _sweep_values(axis)
            points = scn.evaluate(terrain, params, scn.sweep_conditions(axis, values))
            entry[f"sweep:{axis}"] = [_enc(p.result) for p in points]
            run = scn.ScenarioRun(kind="sweep", base_params=params, terrain=terrain,
                                  points=points, axis=axis, axis_values=values)
            d = os.path.join(link_dir, f"sweep_{axis}")
            os.makedirs(d, exist_ok=True)
            report_scenario.save_scenario_csv(run, d)

        points = scn.evaluate(terrain, params, compare)
        entry["compare"] = [_enc(p.result) for p in points]
        run = scn.ScenarioRun(kind="compare", base_params=params, terrain=terrain,
                              points=points)
        d = os.path.join(link_dir, "compare")
        os.makedirs(d, exist_ok=True)
        report_scenario.save_scenario_csv(run, d)

        raw[lid] = entry
        path_results.append(PathResult(
            row=PathRow(path_id=lid, lat_tx=params.lat_tx, lon_tx=params.lon_tx,
                        lat_rx=params.lat_rx, lon_rx=params.lon_rx,
                        h_tx=params.h_tx, h_rx=params.h_rx),
            result=result, terrain=terrain, params=params))

    report_summary._save_summary_csv(path_results, out)
    with open(os.path.join(out, "raw.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f, sort_keys=True)
    meta = {"python": sys.version, "numpy": np.__version__,
            "tk": str(tkinter.TkVersion), "links": len(links)}
    with open(os.path.join(out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    print(json.dumps(meta, ensure_ascii=False))


def _walk(a, b, path: str, stats: dict) -> None:
    if isinstance(a, dict) and "f" in a and len(a) == 1:
        stats["values"] += 1
        if not (isinstance(b, dict) and b.get("f") is not None):
            stats["diffs"].append(f"{path}: 型が違う")
            return
        if a["f"] != b["f"]:
            fa, fb = float.fromhex(a["f"]), float.fromhex(b["f"])
            d = abs(fa - fb)
            if not np.isnan(d):
                stats["max_abs"] = max(stats["max_abs"], d)
            stats["diffs"].append(f"{path}: {fa!r} != {fb!r}")
        return
    if isinstance(a, dict):
        if not isinstance(b, dict) or set(a) != set(b):
            stats["diffs"].append(f"{path}: 鍵が違う")
            return
        for k in sorted(a):
            _walk(a[k], b[k], f"{path}.{k}", stats)
        return
    if isinstance(a, list):
        if not isinstance(b, list) or len(a) != len(b):
            stats["diffs"].append(f"{path}: 長さが違う")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            _walk(x, y, f"{path}[{i}]", stats)
        return
    stats["values"] += 1
    if a != b:
        stats["diffs"].append(f"{path}: {a!r} != {b!r}")


def _files(root: str) -> dict[str, str]:
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            if n in ("raw.json", "meta.json"):
                continue
            full = os.path.join(d, n)
            out[os.path.relpath(full, root).replace(os.sep, "/")] = full
    return out


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    if path.endswith("report.txt"):   # 実行時刻の 1 行だけ外す
        data = b"\n".join(line for line in data.split(b"\n")
                          if not line.startswith(b"Date: "))
    return data


def compare(dir_a: str, dir_b: str) -> int:
    for d in (dir_a, dir_b):
        with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
            print(d, json.load(f))
    with open(os.path.join(dir_a, "raw.json"), encoding="utf-8") as f:
        ra = json.load(f)
    with open(os.path.join(dir_b, "raw.json"), encoding="utf-8") as f:
        rb = json.load(f)
    stats: dict = {"values": 0, "diffs": [], "max_abs": 0.0}
    _walk(ra, rb, "raw", stats)
    print(f"生の値: {stats['values']} 個・差 {len(stats['diffs'])} 個・"
          f"差の最大値 {stats['max_abs']!r}")
    for line in stats["diffs"][:20]:
        print("  ", line)

    fa, fb = _files(dir_a), _files(dir_b)
    missing = sorted(set(fa) ^ set(fb))
    differ = sorted(k for k in set(fa) & set(fb) if _read(fa[k]) != _read(fb[k]))
    kinds: dict[str, int] = {}
    for k in fa:
        name = k.rsplit("/", 1)[-1]
        kinds[name] = kinds.get(name, 0) + 1
    print(f"成果物: {len(fa)} 本 {kinds}・片方にだけ在る {len(missing)} 本・"
          f"中身が違う {len(differ)} 本")
    for k in (missing + differ)[:20]:
        print("  ", k)
    return 1 if (stats["diffs"] or missing or differ) else 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "dump":
        dump(sys.argv[2])
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "compare":
        return compare(sys.argv[2], sys.argv[3])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
