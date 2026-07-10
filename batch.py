"""
batch.py
========
バッチシミュレーション実行エンジン。

UI 知識ゼロ — PathRow リストを受け取って全パスを順次処理する。
CSV パース・エクスポート・バリデーション・実行を担う。
出力生成（PNG/HTML/KML/サマリ）は report.py へ分離した。
"""

import csv
import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import numpy as np

import config
import i18n
import models
import report
import simulation as sim

logger = __import__("logging").getLogger("radiosim")

_PATH_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

# path_id・備考の最大文字数。長すぎる値は summary 台帳の列幅を押し広げ A4 レイアウトを
# 崩すため、実行前の validate_rows で弾く（手入力・CSV 取込の共通チョークポイント）。
_MAX_PATH_ID_LEN = 24
_MAX_NOTE_LEN    = 60


# ============================================================
# データ構造
# ============================================================
@dataclass
class PathRow:
    """1パス分の入力データ。None フィールドは base_params の値を継承する。"""
    path_id:  str
    lat_tx:   float
    lon_tx:   float
    lat_rx:   float
    lon_rx:   float
    h_tx:     float
    h_rx:     float
    freq_mhz: float | None = None
    gain_tx:  float | None = None
    gain_rx:  float | None = None
    note:     str          = ""


@dataclass
class PathResult:
    """1パスの実行結果。"""
    row:     PathRow
    result:  models.LinkBudgetResult | None
    terrain: models.TerrainProfile   | None = None
    params:  sim.SimParams           | None = None
    save_dir: str                           = ""
    error:   Exception               | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None


# ============================================================
# CSV I/O
# ============================================================
_REQUIRED_COLS = {"id", "start", "end", "h_tx", "h_rx"}

# CSV スキーマの正準（出力ヘッダ順）。required の後に optional。
# ドキュメント整合テストはこの定数を単一ソースに README の CSV 節を照合する。
CSV_COLUMNS = ["id", "start", "end", "h_tx", "h_rx", "freq", "gain_tx", "gain_rx", "note"]
OPTIONAL_COLS = [c for c in CSV_COLUMNS if c not in _REQUIRED_COLS]

def parse_csv(csv_path: str) -> list[PathRow]:
    """
    CSV ファイルを PathRow リストに変換する。

    必須列: id, start, end, h_tx, h_rx
    省略可: freq, gain_tx, gain_rx, note
    """
    rows: list[PathRow] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row.")
        cols = {c.strip().lower() for c in reader.fieldnames}
        missing = _REQUIRED_COLS - cols
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")

        for line_no, raw in enumerate(reader, start=2):
            rows.append(_parse_csv_row(raw, line_no))

    if not rows:
        raise ValueError("CSV has no data rows.")
    return rows


def _parse_csv_row(raw: dict, line: int) -> PathRow:
    pid = raw.get("id", "").strip()
    if not pid:
        raise ValueError(f"Row {line}: 'id' is empty.")

    def _coord(key: str) -> tuple[float, float]:
        val = raw.get(key, "").strip()
        parts = val.split(",")
        if len(parts) != 2:
            raise ValueError(f"Row {line}: '{key}' must be in 'lat, lon' format.")
        return float(parts[0].strip()), float(parts[1].strip())

    def _float(key: str) -> float:
        val = raw.get(key, "").strip()
        try:
            return float(val)
        except ValueError:
            raise ValueError(f"Row {line}: '{key}' is not a number: '{val}'")

    def _opt_float(key: str) -> float | None:
        val = raw.get(key, "").strip()
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            raise ValueError(f"Row {line}: '{key}' is not a number: '{val}'")

    lat_tx, lon_tx = _coord("start")
    lat_rx, lon_rx = _coord("end")
    return PathRow(
        path_id  = pid,
        lat_tx   = lat_tx,
        lon_tx   = lon_tx,
        lat_rx   = lat_rx,
        lon_rx   = lon_rx,
        h_tx     = _float("h_tx"),
        h_rx     = _float("h_rx"),
        freq_mhz = _opt_float("freq"),
        gain_tx  = _opt_float("gain_tx"),
        gain_rx  = _opt_float("gain_rx"),
        note     = raw.get("note", "").strip(),
    )


def export_csv(rows: list[PathRow], csv_path: str) -> None:
    """PathRow リストを CSV ファイルに書き出す。"""
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for r in rows:
            writer.writerow([
                r.path_id,
                f"{r.lat_tx}, {r.lon_tx}",
                f"{r.lat_rx}, {r.lon_rx}",
                r.h_tx,
                r.h_rx,
                r.freq_mhz if r.freq_mhz is not None else "",
                r.gain_tx  if r.gain_tx  is not None else "",
                r.gain_rx  if r.gain_rx  is not None else "",
                r.note,
            ])


# ============================================================
# バリデーション
# ============================================================
def validate_rows(rows: list[PathRow]) -> list[str]:
    """PathRow リストを検証してエラーメッセージのリストを返す。空リストなら正常。"""
    errors: list[str] = []
    if not rows:
        errors.append(i18n.t("verr_empty"))
        return errors

    seen: set[str] = set()
    for r in rows:
        if r.path_id in seen:
            errors.append(i18n.t("verr_duplicate_id").format(pid=r.path_id))
        seen.add(r.path_id)

    for r in rows:
        pid = r.path_id
        if not _PATH_ID_RE.fullmatch(pid):
            errors.append(i18n.t("verr_invalid_id").format(pid=repr(pid)))
            continue
        if len(pid) > _MAX_PATH_ID_LEN:
            errors.append(i18n.t("verr_id_too_long").format(
                pid=pid, max=_MAX_PATH_ID_LEN, n=len(pid)))
        if len(r.note) > _MAX_NOTE_LEN:
            errors.append(i18n.t("verr_note_too_long").format(
                pid=pid, max=_MAX_NOTE_LEN, n=len(r.note)))
        coords = [r.lat_tx, r.lon_tx, r.lat_rx, r.lon_rx, r.h_tx, r.h_rx]
        if any(math.isnan(v) for v in coords):
            errors.append(i18n.t("verr_invalid_coord").format(pid=pid))
            continue
        if not (-85.05 <= r.lat_tx <= 85.05):
            errors.append(i18n.t("verr_tx_lat").format(pid=pid, val=r.lat_tx))
        if not (-180 <= r.lon_tx <= 180):
            errors.append(i18n.t("verr_tx_lon").format(pid=pid, val=r.lon_tx))
        if not (-85.05 <= r.lat_rx <= 85.05):
            errors.append(i18n.t("verr_rx_lat").format(pid=pid, val=r.lat_rx))
        if not (-180 <= r.lon_rx <= 180):
            errors.append(i18n.t("verr_rx_lon").format(pid=pid, val=r.lon_rx))
        if abs(r.lat_tx - r.lat_rx) < 1e-7 and abs(r.lon_tx - r.lon_rx) < 1e-7:
            errors.append(i18n.t("verr_identical").format(pid=pid))
        if not (0 <= r.h_tx <= 500):
            errors.append(i18n.t("verr_h_tx").format(pid=pid, val=r.h_tx))
        if not (0 <= r.h_rx <= 500):
            errors.append(i18n.t("verr_h_rx").format(pid=pid, val=r.h_rx))
        if r.freq_mhz is not None and not (1 <= r.freq_mhz <= 100000):
            errors.append(i18n.t("verr_freq").format(pid=pid, val=r.freq_mhz))
        if r.gain_tx is not None and not (0 <= r.gain_tx <= 60):
            errors.append(i18n.t("verr_gain_tx").format(pid=pid, val=r.gain_tx))
        if r.gain_rx is not None and not (0 <= r.gain_rx <= 60):
            errors.append(i18n.t("verr_gain_rx").format(pid=pid, val=r.gain_rx))

    return errors


# ============================================================
# 実行エンジン
# ============================================================
def run_batch(
    rows:              list[PathRow],
    base_params:       sim.SimParams,
    on_path_start:     Callable[[int, int, str], None],
    on_path_progress:  Callable[[int], None],
    on_path_complete:  Callable[[int, int, "PathResult"], None],
    on_batch_complete: Callable[[str, list["PathResult"]], None],
    on_error:          Callable[[Exception], None],
    coord_format:      str = "dd",
) -> None:
    """バッチ実行をバックグラウンドスレッドで開始する。

    coord_format は per-path report.txt の人が読む座標表記のみに効く（既定 DD）。
    """
    threading.Thread(
        target = _run_thread,
        args   = (rows, base_params, on_path_start, on_path_progress,
                  on_path_complete, on_batch_complete, on_error, coord_format),
        daemon = True,
    ).start()


def _run_thread(
    rows:              list[PathRow],
    base_params:       sim.SimParams,
    on_path_start:     Callable[[int, int, str], None],
    on_path_progress:  Callable[[int], None],
    on_path_complete:  Callable[[int, int, "PathResult"], None],
    on_batch_complete: Callable[[str, list["PathResult"]], None],
    on_error:          Callable[[Exception], None],
    coord_format:      str = "dd",
) -> None:
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir = os.path.join(config.RESULTS_DIR, f"batch_{timestamp}")
        os.makedirs(batch_dir, exist_ok=True)

        path_results: list[PathResult] = []
        total = len(rows)

        for i, row in enumerate(rows):
            on_path_start(i + 1, total, row.path_id)
            pr = _process_one(row, base_params, batch_dir, on_path_progress,
                              coord_format)
            path_results.append(pr)
            on_path_complete(i + 1, total, pr)

        report._save_summary_csv(path_results, batch_dir)
        logger.info("Batch complete: %d paths → %s", total, batch_dir)
        on_batch_complete(batch_dir, path_results)

    except Exception as ex:
        logger.error("Batch error: %s", ex)
        on_error(ex)


def _process_one(
    row:         PathRow,
    base:        sim.SimParams,
    batch_dir:   str,
    on_progress: Callable[[int], None],
    coord_format: str = "dd",
) -> PathResult:
    try:
        params    = _make_params(row, base)
        raw_elevs = _fetch_sync(params, on_progress)
        terrain   = models.calculate_terrain_profile(
            raw_elevs = raw_elevs,
            lat_tx    = params.lat_tx,
            lon_tx    = params.lon_tx,
            lat_rx    = params.lat_rx,
            lon_rx    = params.lon_rx,
        )
        result = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)

        path_dir = os.path.join(batch_dir, row.path_id)
        os.makedirs(path_dir, exist_ok=True)
        sim._save_settings(params, params.h_tx, params.h_rx, path_dir)
        sim._save_terrain_csv(terrain, path_dir)
        sim._save_report(result, params, params.h_tx, params.h_rx, path_dir,
                         coord_format)
        # _save_profile_png は matplotlib を使うためメインスレッドで呼ぶ。
        # save_path_visuals() を on_path_complete コールバック内（メインスレッド）で呼ぶこと。

        return PathResult(
            row      = row,
            result   = result,
            terrain  = terrain,
            params   = params,
            save_dir = path_dir,
        )

    except Exception as ex:
        logger.error("Path '%s' failed: %s", row.path_id, ex)
        return PathResult(row=row, result=None, error=ex)


def _make_params(row: PathRow, base: sim.SimParams) -> sim.SimParams:
    """PathRow + base_params から SimParams を生成する。"""
    c: dict[str, str] = {
        "start"      : f"{row.lat_tx}, {row.lon_tx}",
        "end"        : f"{row.lat_rx}, {row.lon_rx}",
        "h_tx"       : str(row.h_tx),
        "h_rx"       : str(row.h_rx),
        "freq"       : str(row.freq_mhz    if row.freq_mhz    is not None else base.freq_mhz),
        "p_tx"       : str(base.p_tx),
        "gain_tx"    : str(row.gain_tx     if row.gain_tx     is not None else base.gain_tx),
        "gain_rx"    : str(row.gain_rx     if row.gain_rx     is not None else base.gain_rx),
        "sens"       : str(base.sens),
        "veg_h"      : str(base.veg_h),
        "k_factor"   : str(base.k_factor),
        "samples"    : str(base.num),
        "env_type"   : base.env_type,
        "rain_rate"  : str(base.rain_rate),
        "diff_method": base.diff_method,
    }
    return sim.SimParams(c)


def _fetch_sync(
    params:      sim.SimParams,
    on_progress: Callable[[int], None],
) -> np.ndarray:
    """fetch_elevations の非同期コールバックを threading.Event で同期化する。"""
    result: list[np.ndarray] = []
    error:  list[Exception]  = []
    done = threading.Event()

    def _on_complete(e: np.ndarray) -> None:
        result.append(e)
        done.set()

    def _on_error(ex: Exception) -> None:
        error.append(ex)
        done.set()

    sim.fetch_elevations(
        params,
        on_progress = on_progress,
        on_complete = _on_complete,
        on_error    = _on_error,
    )
    done.wait()
    if error:
        raise error[0]
    return result[0]
