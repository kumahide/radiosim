"""
webui/sheets.py
===============
新しい画面の**作業面の入力**（Link 1.0 ステージ10a）。

作業面は 4 つ＝`single`（1 回線）・`batch`（複数経路）・`scenario`（条件探索）・
`multihop`（中継）。無線機と環境の欄は全作業面で 1 つ（ステージ5 の決定 ③）＝
`state.AppState` の入力の集合。ここが持つのは、面ごとに増える入力（表・条件・点列）だけ。

🔑 **画面の文字列のまま持つ**（`report.project.ScenarioSpec` と同じ流儀）＝打ちかけの値でも
持てる・保存できる。型へは実行と保存のときに直す（`batch_rows`・`conditions`・
`multihop_path`）。値域の検査は型の側（`scenario.Condition`・`batch.validate_rows`・
`multihop.validate_path`）が 1 か所で担う＝ここで二重に書かない。

面ごとの形（`.rsproj` の `ProjectDoc` の 3 つの節に対応する）:

- `batch` ＝行の並び。行は `{CSV の列名: 文字列}`（鍵は `CSV_COLUMNS`・欠けた鍵は空）
- `scenario` ＝`{"mode", "compare": [{上書きの鍵: 文字列}], "sweep": {axis/from/to/points}}`
  （`compare` の鍵は `scenario.OVERRIDABLE`・空欄は基準を踏襲）
- `multihop` ＝`{"path_id", "note", "waypoints": [{name, coord, h}], "hop_rf": [{freq, gain_tx, gain_rx}]}`

⚠️ `report.*` は matplotlib を引く（取り込み 0.19 秒・I-232）＝型へ直す関数の中で取り込む。
"""

from __future__ import annotations

import copy
import math
from typing import Any

from core import coords
from core import i18n
from core import scenario as scn
from core.batch_csv_schema import CSV_COLUMNS

#: 作業面（JS もこの字で分ける）。先頭が起動したときの面。
WORKSPACES = ("single", "batch", "scenario", "multihop")

#: 入力を持つ作業面（`single` は入力の集合だけで足りる）。
SHEETS = ("batch", "scenario", "multihop")

#: 条件探索の種類（`report.project.SCENARIO_MODES` と同じ＝試験が縛る）。
MODES = ("compare", "sweep")
_SWEEP_KEYS = ("axis", "from", "to", "points")
_WAYPOINT_KEYS = ("name", "coord", "h")
_HOP_KEYS = ("freq", "gain_tx", "gain_rx")
#: 中継の区間の欄 → `HopRF` の属性。
_HOP_ATTRS = {"freq": "freq_mhz", "gain_tx": "gain_tx", "gain_rx": "gain_rx"}
#: 文字で選ぶ上書き（内部の鍵で持つ）。他は数。
_CHOICE_OVERRIDES = frozenset({"env_type", "diff_method"})

#: 中継の点の上限（`report.multihop.MAX_HOPS` ＋ 1＝試験が縛る）。形の検査で器の大きさだけ見る。
MAX_WAYPOINTS = 9


class SheetError(ValueError):
    """作業面の入力の形が違う（知らない面・知らない鍵・文字列でない値・数の上限）。"""


def empty() -> "dict[str, Any]":
    """起動したときの作業面の入力（どの面も空）。"""
    return {
        "batch": [],
        "scenario": {"mode": "compare", "compare": [],
                     "sweep": {"axis": scn.SWEEP_AXES[0], "from": "", "to": "", "points": ""}},
        "multihop": {"path_id": "", "note": "", "waypoints": [], "hop_rf": []},
    }


# ============================================================
# 形の検査（HTTP から来た値＝何が来ても SheetError 1 種に畳む）
# ============================================================
def _str_map(obj: Any, keys: "tuple[str, ...] | frozenset[str]", what: str) -> "dict[str, str]":
    """`{鍵: 文字列}` を検査し、`keys` の全部をそろえた写しを返す（欠けた鍵は空）。"""
    if not isinstance(obj, dict):
        raise SheetError(f"{what}: not an object")
    bad = sorted(str(k) for k, v in obj.items() if k not in keys or not isinstance(v, str))
    if bad:
        raise SheetError(f"{what}: {', '.join(bad)}")
    return {k: obj.get(k, "") for k in keys}


def _list(obj: Any, limit: "int | None", what: str) -> list:
    if not isinstance(obj, list):
        raise SheetError(f"{what}: not a list")
    if limit is not None and len(obj) > limit:
        raise SheetError(f"{what}: more than {limit}")
    return obj


def _check_batch(obj: Any) -> "list[dict[str, str]]":
    return [_str_map(r, tuple(CSV_COLUMNS), f"batch[{i}]")
            for i, r in enumerate(_list(obj, None, "batch"))]


def _check_scenario(obj: Any) -> "dict[str, Any]":
    if not isinstance(obj, dict) or set(obj) - {"mode", "compare", "sweep"}:
        raise SheetError("scenario: bad keys")
    mode = obj.get("mode", "compare")
    if mode not in MODES:
        raise SheetError(f"scenario.mode: {mode!r}")
    compare = [_str_map(c, scn.OVERRIDABLE, f"scenario.compare[{i}]")
               for i, c in enumerate(_list(obj.get("compare", []), scn.MAX_COMPARE_CONDITIONS,
                                           "scenario.compare"))]
    sweep = _str_map(obj.get("sweep", {}), _SWEEP_KEYS, "scenario.sweep")
    return {"mode": mode, "compare": compare, "sweep": sweep}


def _check_multihop(obj: Any) -> "dict[str, Any]":
    if not isinstance(obj, dict) or set(obj) - {"path_id", "note", "waypoints", "hop_rf"}:
        raise SheetError("multihop: bad keys")
    head = _str_map({k: obj.get(k, "") for k in ("path_id", "note")}, ("path_id", "note"), "multihop")
    waypoints = [_str_map(w, _WAYPOINT_KEYS, f"multihop.waypoints[{i}]")
                 for i, w in enumerate(_list(obj.get("waypoints", []), MAX_WAYPOINTS,
                                             "multihop.waypoints"))]
    hop_rf = [_str_map(h, _HOP_KEYS, f"multihop.hop_rf[{i}]")
              for i, h in enumerate(_list(obj.get("hop_rf", []), MAX_WAYPOINTS - 1,
                                          "multihop.hop_rf"))]
    return {**head, "waypoints": waypoints, "hop_rf": hop_rf}


_CHECKS = {"batch": _check_batch, "scenario": _check_scenario, "multihop": _check_multihop}


def check(name: Any, value: Any) -> Any:
    """1 つの面の入力を検査し、欠けた鍵をそろえた写しを返す（元の値は触らない）。"""
    if name not in _CHECKS:
        raise SheetError(f"unknown sheet: {name!r}")
    return _CHECKS[name](copy.deepcopy(value))


# ============================================================
# 型への変換（実行と保存のとき）＝誤りは画面の字の ValueError
# ============================================================
def _number(text: str, label: str) -> float:
    """欄の文字列 → 数。読めない値は欄の名前つきで弾く（`views/scenario._number` と同じ字）。"""
    try:
        return float(text)
    except ValueError:
        raise ValueError(i18n.t("scn_err_value").format(
            label=label, reason=i18n.t("err_numeric"), value=repr(text))) from None


def batch_rows(rows: "list[dict[str, str]]") -> list:
    """複数経路の表 → `PathRow` の並び（行の番号は表の 1 始まり）。値域は `batch.validate_rows`。"""
    from report import batch
    return [batch._parse_csv_row(r, line) for line, r in enumerate(rows, start=1)]


def conditions(spec: "dict[str, Any]") -> "tuple[list[scn.Condition], str, list[float]]":
    """条件探索の入力 → (条件の並び, 掃引の軸, 掃引の値)。比較では軸は空・値は空。

    比較＝基準（上書きなし）が先頭＋条件 N（空欄は基準を踏襲）＝旧画面と同じ語。
    値域は `scenario.Condition` が弾く（地形を取る前）。
    """
    if spec["mode"] == "sweep":
        sw = spec["sweep"]
        start = _number(sw["from"], i18n.t("scn_from"))
        stop = _number(sw["to"], i18n.t("scn_to"))
        if start == stop:
            raise ValueError(i18n.t("scn_err_range"))
        points = _number(sw["points"], i18n.t("scn_points"))
        if not math.isfinite(points) or points != int(points):
            raise ValueError(i18n.t("scn_err_points").format(max=scn.MAX_SWEEP_POINTS))
        values = scn.linspace_values(start, stop, int(points))
        return scn.sweep_conditions(sw["axis"], values), sw["axis"], values
    conds = [scn.Condition(label=i18n.t("scn_base"), overrides={})]
    for i, col in enumerate(spec["compare"], start=1):
        overrides: dict[str, float | str] = {}
        for key, text in col.items():
            text = text.strip()
            if not text:
                continue                                 # 空欄＝基準を踏襲
            overrides[key] = (text if key in _CHOICE_OVERRIDES
                              else _number(text, i18n.t(f"scn_axis_{key}")))
        conds.append(scn.Condition(label=i18n.t("scn_cond_n").format(n=i), overrides=overrides))
    return conds, "", []


def multihop_path(sheet: "dict[str, Any]") -> Any:
    """中継の入力 → `MultiHopPath`（`views/multihop._collect_path` と同じ組み立て）。

    座標は `coords.parse_pair`（DD と DMS の両方）・名前が空なら `P{i}`・区間の空欄は共通設定を踏襲。
    中継の形（点の数・区間の数・高さの範囲）は `multihop.validate_path`。
    """
    from report import multihop as mh
    waypoints = []
    for i, w in enumerate(sheet["waypoints"], start=1):
        try:
            lat, lon = coords.parse_pair(w["coord"])
            height = float(w["h"])
        except ValueError:
            raise ValueError(i18n.t("mh_err_coord").format(no=i, name=w["name"])) from None
        waypoints.append(mh.Waypoint(name=w["name"].strip() or f"P{i}",
                                     lat=lat, lon=lon, h=height))
    hop_rf = []
    for hop in sheet["hop_rf"]:
        values: dict[str, float | None] = {}
        for key, attr in _HOP_ATTRS.items():
            text = hop[key].strip()
            values[attr] = (_number(text, i18n.t(f"scn_axis_{attr}")) if text else None)
        hop_rf.append(mh.HopRF(**values))
    return mh.MultiHopPath(path_id=sheet["path_id"].strip(), waypoints=waypoints,
                           hop_rf=hop_rf, note=sheet["note"].strip())
