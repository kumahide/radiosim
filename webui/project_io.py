"""
webui/project_io.py
===================
新しい画面の **`.rsproj` の保存と開く**（Link 1.0 ステージ10d）。

ファイルの形は旧画面と同じ `report.project`（`ProjectDoc`・`to_dict`／`from_dict`）＝読み書きの
規則を二重に持たない。ここが担うのは、画面の入力（入力の集合＝文字列・面の入力＝`webui.sheets`
の文字列の形）と `ProjectDoc`（型つき）の間の変換だけ。

🔑 画面とは **JSON の文字列で往復する**（ファイルを書くのは画面の側＝Edge のファイルの API・
2026-11-08 経営者の決定）＝ここはパスを取らない。

保存の流儀は旧画面（`views/launcher_project._collect_project`）と同じ＝**読めない値のある面は
その面だけ保存せず、警告で知らせる**（入力途中でも保存できることを優先・黙って捨てない）。
空の面（行の無い表・点の無い中継）は節ごと書かない。

計算方式（`calc_profile`）＝**ファイルの方式のまま開き、そのまま書く**（2026-11-08 経営者の
決定＝3.x の `.rsproj`（v1）は v1 で計算する）。方式は画面の欄ではない＝`AppState` が入力の
集合と別に持つ。新規（開かずに始めた）は既定（v2）。
"""

from __future__ import annotations

import json
import math
from typing import Any

from core import calc_profile
from core import config
from core import coords
from core import i18n
from core.batch_csv_schema import CSV_COLUMNS
from webui import sheets as sheets_mod


def _num_text(value: "float | None") -> str:
    """数 → 欄の文字列（空は空・`10.0` は `10`）。桁は落とさない（`repr`）。"""
    if value is None:
        return ""
    text = repr(float(value))
    return text[:-2] if text.endswith(".0") else text


def _pair_text(lat: float, lon: float) -> str:
    return f"{_num_text(lat)}, {_num_text(lon)}"


# ============================================================
# 保存＝画面の入力 → `.rsproj` の文字列
# ============================================================
def _batch_section(rows: "list[dict[str, str]]") -> "tuple[list | None, str | None]":
    """複数経路の表 → (`PathRow` の並びか None, 警告か None)。

    1 行でも読めなければ表ごと保存しない（旧画面と同じ）。値域の外は保存する（読める値＝
    開いた後に表で直せる）。NaN・inf は JSON に書けない＝`project.unreadable_row` で弾く。
    """
    if not rows:
        return None, None
    from report import batch
    from report import project
    parsed = []
    for line, raw in enumerate(rows, start=1):
        try:
            parsed.append(batch._parse_csv_row(raw, line))
        except ValueError:
            label = raw.get("id", "").strip() or f"#{line}"
            return None, i18n.t("proj_warn_batch").format(id=label).strip()
    bad = project.unreadable_row(parsed)
    if bad is not None:
        return None, i18n.t("proj_warn_batch").format(id=bad.path_id or "-").strip()
    return parsed, None


def _multihop_section(sheet: "dict[str, Any]") -> "tuple[Any, str | None]":
    """中継の入力 → (`MultiHopPath` か None, 警告か None)。点が無く名前も無ければ書かない。"""
    if not sheet["waypoints"] and not sheet["path_id"].strip() and not sheet["note"].strip():
        return None, None
    try:
        path = sheets_mod.multihop_path(sheet)
    except ValueError as e:
        return None, i18n.t("proj_warn_multihop").format(reason=e).strip()
    values = [v for w in path.waypoints for v in (w.lat, w.lon, w.h)]
    values += [v for rf in path.hop_rf for v in (rf.freq_mhz, rf.gain_tx, rf.gain_rx)
               if v is not None]
    if not all(math.isfinite(v) for v in values):
        return None, i18n.t("proj_warn_multihop").format(reason="NaN").strip()
    return path, None


def dump(inputs: "dict[str, str]", sheets: "dict[str, Any]",
         meta: "dict[str, str] | None" = None,
         profile: str = calc_profile.DEFAULT_PROFILE) -> "tuple[str, list[str]]":
    """画面の入力 → (`.rsproj` の JSON の文字列, 警告の並び)。

    `inputs` は入力の集合（`AppState` の写し）・`sheets` は面の入力（`sheets.check` を通った形）・
    `meta` は開いたファイルの案件名とメモ（新しい画面に欄は無い＝持ち越すだけ）・`profile` は
    今の計算方式（開いたファイルの方式＝v1 を開いて保存しても v1 のまま）。
    座標は DD で書く（データ＝DD 原則＝旧画面の `_coords_to_dd` と同じ）。
    """
    from report import project
    params = dict(inputs)
    for key in ("start", "end"):
        if key in params:
            params[key] = coords.to_dd_str(params[key])
    warnings: list[str] = []
    rows, warn = _batch_section(sheets["batch"])
    if warn:
        warnings.append(warn)
    path, warn = _multihop_section(sheets["multihop"])
    if warn:
        warnings.append(warn)
    sc = sheets["scenario"]
    doc = project.ProjectDoc(
        meta=dict(meta or {}),
        params=params,
        batch_rows=rows,
        scenario=project.ScenarioSpec(mode=sc["mode"],
                                      compare=[dict(c) for c in sc["compare"]],
                                      sweep=dict(sc["sweep"])),
        multihop=path,
        calc_profile=calc_profile.require(profile),
    )
    text = json.dumps(project.to_dict(doc), indent=2, ensure_ascii=False, allow_nan=False)
    return text, warnings


def filename(meta: "dict[str, str] | None") -> str:
    """保存の初期ファイル名（案件名があればそれ）。"""
    from report import project
    return project.default_filename((meta or {}).get("project_name", ""))


# ============================================================
# 開く＝`.rsproj` の文字列 → 画面の入力
# ============================================================
def _batch_sheet(rows: list) -> "list[dict[str, str]]":
    out = []
    for r in rows:
        out.append({
            "id": r.path_id,
            "start": _pair_text(r.lat_tx, r.lon_tx),
            "end": _pair_text(r.lat_rx, r.lon_rx),
            "h_tx": _num_text(r.h_tx), "h_rx": _num_text(r.h_rx),
            "freq": _num_text(r.freq_mhz),
            "gain_tx": _num_text(r.gain_tx), "gain_rx": _num_text(r.gain_rx),
            "note": r.note,
            "meas_dbm": _num_text(r.meas_dbm),
            "meas_method": r.meas_method,
            "feeder_loss_db": _num_text(r.feeder_loss_db),
            "env_class": r.env_class,
        })
    missing = set(CSV_COLUMNS) - set(out[0]) if out else set()
    assert not missing, f"CSV の列の写し漏れ: {sorted(missing)}"   # 列を足したら上へも足す
    return out


def _multihop_sheet(path: Any) -> "dict[str, Any]":
    return {
        "path_id": path.path_id,
        "note": path.note,
        "waypoints": [{"name": w.name, "coord": _pair_text(w.lat, w.lon), "h": _num_text(w.h)}
                      for w in path.waypoints],
        "hop_rf": [{"freq": _num_text(rf.freq_mhz), "gain_tx": _num_text(rf.gain_tx),
                    "gain_rx": _num_text(rf.gain_rx)} for rf in path.hop_rf],
    }


def load(text: str) -> "tuple[dict[str, str], dict[str, Any], dict[str, str], str]":
    """`.rsproj` の文字列 → (入力の集合の全部, 面の入力の全部, meta, 計算方式)。

    読めない理由は `report.project.ProjectError`（画面の字）に畳む。入力の集合はファイルに無い
    鍵を既定へ戻す（開く＝前の入力を残さない）。ファイルに無い節は空の面にする。
    計算方式はファイルのまま（方式の無い 3.x のファイルは `from_dict` が v1 と読む）。
    """
    from report import project
    try:
        data = json.loads(text)
    except ValueError as e:                              # 桁の大きい整数の素の ValueError も
        raise project.ProjectError(i18n.t("proj_err_broken").format(reason=e)) from e
    doc = project.from_dict(data)
    inputs = {k: config.DEFAULT_CONFIG[k] for k in config.SIM_KEYS}
    inputs.update({k: v for k, v in doc.params.items() if k in inputs})
    sheets = sheets_mod.empty()
    if doc.batch_rows is not None:
        sheets["batch"] = _batch_sheet(doc.batch_rows)
    if doc.scenario is not None:
        sheets["scenario"] = {"mode": doc.scenario.mode, "compare": doc.scenario.compare,
                              "sweep": doc.scenario.sweep}
    if doc.multihop is not None:
        sheets["multihop"] = _multihop_sheet(doc.multihop)
    try:
        sheets = {name: sheets_mod.check(name, value) for name, value in sheets.items()}
    except sheets_mod.SheetError as e:                   # 条件の数・知らない上書きの鍵など
        raise project.ProjectError(i18n.t("proj_err_broken").format(reason=e)) from e
    return inputs, sheets, dict(doc.meta), doc.calc_profile
