"""
batch.py
========
バッチシミュレーション実行エンジン。

UI 知識ゼロ — PathRow リストを受け取って全パスを順次処理する。
CSV パース・エクスポート・バリデーション・実行・サマリ保存を担う。
"""

import base64
import csv
import html as _html
import io
import math
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import numpy as np

import coords
import i18n
import infrastructure as infra
import models
import mpl_fonts
import report_map
import simulation as sim
import version

logger = __import__("logging").getLogger("radiosim")

_PATH_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


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

def parse_csv(csv_path: str) -> list[PathRow]:
    """
    CSV ファイルを PathRow リストに変換する。

    必須列: id, start, end, h_tx, h_rx
    省略可: freq, note
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
        note     = raw.get("note", "").strip(),
    )


def export_csv(rows: list[PathRow], csv_path: str) -> None:
    """PathRow リストを CSV ファイルに書き出す。"""
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "start", "end", "h_tx", "h_rx", "freq", "note"])
        for r in rows:
            writer.writerow([
                r.path_id,
                f"{r.lat_tx}, {r.lon_tx}",
                f"{r.lat_rx}, {r.lon_rx}",
                r.h_tx,
                r.h_rx,
                r.freq_mhz if r.freq_mhz is not None else "",
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
        batch_dir = os.path.join(infra.RESULTS_DIR, f"batch_{timestamp}")
        os.makedirs(batch_dir, exist_ok=True)

        path_results: list[PathResult] = []
        total = len(rows)

        for i, row in enumerate(rows):
            on_path_start(i + 1, total, row.path_id)
            pr = _process_one(row, base_params, batch_dir, on_path_progress,
                              coord_format)
            path_results.append(pr)
            on_path_complete(i + 1, total, pr)

        _save_summary_csv(path_results, batch_dir)
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
        "gain_tx"    : str(base.gain_tx),
        "gain_rx"    : str(base.gain_rx),
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


def save_path_visuals(pr: PathResult, coord_format: str = "dd") -> None:
    """
    PNG と HTML をメインスレッドから保存する。

    matplotlib の TkAgg バックエンドが初期化されている環境では
    バックグラウンドスレッドから matplotlib を使うと tkinter GC 警告が
    発生するため、この関数は必ずメインスレッド（on_path_complete 内）で呼ぶこと。

    coord_format は HTML レポートの人が読む座標セルのみに効く（既定 DD）。
    """
    if pr.result is None or pr.terrain is None or pr.params is None:
        return
    try:
        save_profile_png(
            pr.terrain, pr.result, pr.params,
            pr.params.h_tx, pr.params.h_rx, pr.save_dir, coord_format,
        )
        save_path_kml(
            pr.terrain, pr.result, pr.params,
            pr.params.h_tx, pr.params.h_rx, pr.save_dir,
        )
    except Exception as ex:
        logger.error("Visual save failed for '%s': %s", pr.row.path_id, ex)


def save_profile_png(
    terrain:  models.TerrainProfile,
    result:   models.LinkBudgetResult,
    params:   sim.SimParams,
    h_tx:     float,
    h_rx:     float,
    save_dir: str,
    coord_format: str = "dd",
) -> None:
    """
    地形断面 PNG をバックグラウンドスレッドから保存する。

    pyplot（TkAgg）を使わず Figure + FigureCanvasAgg を直接使うため
    メインスレッド以外から呼んでも安全。

    日本語ラベルの豆腐化を防ぐため、描画前に日本語フォントを明示適用する
    （個別グラフを開いていなくてもレポート PNG が正しく描画される）。
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    mpl_fonts.apply_japanese_font()

    t     = terrain
    elevs = t.elevs_with_curve
    N     = t.num_samples
    y_min = float(np.min(t.raw_elevs)) - 30

    fig    = Figure(figsize=(15, 6))
    fig.patch.set_facecolor("#EAEAEA")
    canvas = FigureCanvasAgg(fig)

    # 地形断面軸
    ax = fig.add_axes((0.06, 0.11, 0.90, 0.78))
    ax.set_facecolor("#F2F2F2")

    veg_top = elevs + params.veg_h
    ax.fill_between(t.d_km_axis, elevs,   y_min,   color="#8B4513", alpha=0.4)
    ax.fill_between(t.d_km_axis, veg_top, elevs,   color="green",   alpha=0.3)

    tx_abs = float(elevs[0])  + h_tx
    rx_abs = float(elevs[-1]) + h_rx
    los    = np.linspace(tx_abs, rx_abs, N)
    f1     = models.fresnel_zone_radii(t.d_km_axis, t.horiz_dist_km, params.freq_mhz)

    ax.plot(t.d_km_axis, los, color="red", linestyle="--", lw=1.5)
    ax.fill_between(t.d_km_axis, los - f1, los + f1, color="cyan", alpha=0.25)
    ax.vlines(
        [0, t.horiz_dist_km],
        [float(elevs[0]),  float(elevs[-1])],
        [tx_abs, rx_abs],
        color="black", lw=3,
    )

    ax.set_title(f"{params.freq_mhz} MHz", fontsize=13, loc="left")
    ax.set_xlabel(i18n.t("graph_dist_axis"), fontsize=11)
    ax.set_ylabel(i18n.t("graph_alt_axis"),  fontsize=11)
    ax.grid(True, alpha=0.2)

    # 統一凡例: 枠外・右上・横1列
    # loc="lower right" → 凡例の右下隅を bbox_to_anchor に合わせる
    # bbox_to_anchor=(1.0, 1.02) → 軸の右端・上端の少し外側
    ax.legend(
        handles=[
            Patch(facecolor="#8B4513", alpha=0.4, label=i18n.t("legend_terrain")),
            Patch(facecolor="green",   alpha=0.3, label=i18n.t("legend_vegetation")),
            Line2D([0], [0], color="red", linestyle="--", lw=1.5, label=i18n.t("legend_los")),
            Patch(facecolor="cyan",    alpha=0.25, label=i18n.t("legend_fresnel")),
        ],
        loc="lower right",
        bbox_to_anchor=(1.0, 1.02),
        ncol=4,
        fontsize=11,
        framealpha=0.9,
        borderaxespad=0,
    )

    # PNG をディスクに保存しつつ、同じ描画を Base64 にも変換する
    png_path = os.path.join(save_dir, "profile.png")
    fig.savefig(png_path, dpi=150)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    # Figure と Canvas の循環参照をメインスレッドで即時解放する。
    # 解放を遅らせると Python 3.14 のインクリメンタル GC が
    # バックグラウンドスレッドで __del__ を呼ぶことがある。
    fig.clf()
    del canvas, fig

    # 経路オーバーレイ地図（ヘッドレス・ベストエフォート）。タイル取得に失敗
    # したら None を返し、レポートは地図なし＋注記で生成される。
    map_b64 = report_map.render_path_map_b64(
        (params.lat_tx, params.lon_tx), (params.lat_rx, params.lon_rx)
    )

    save_path_html(terrain, result, params, h_tx, h_rx, save_dir, img_b64, map_b64,
                   coord_format)


def save_path_html(
    terrain:  models.TerrainProfile,
    result:   models.LinkBudgetResult,
    params:   sim.SimParams,
    h_tx:     float,
    h_rx:     float,
    save_dir: str,
    img_b64:  str,
    map_b64:  "str | None" = None,
    coord_format: str = "dd",
) -> None:
    """per-path の report.html を生成する（グラフ・地図は Base64 埋め込み）。

    map_b64 が None のとき（タイル取得失敗）は地図を省き注記を表示する。
    coord_format は人が読む座標セルのみに効く（"dd"|"dms"）。CSV/KML/settings は
    再読込・規格のため DD 固定。既定 DD でヘッドレス呼び出しは表示設定に非依存。
    """
    tx_coords = coords.format_pair(params.lat_tx, params.lon_tx, coord_format)
    rx_coords = coords.format_pair(params.lat_rx, params.lon_rx, coord_format)
    path_id     = os.path.basename(save_dir)
    path_id_esc = _html.escape(path_id)
    status_cls  = "ok" if result.status == "OK" else "ng"
    model_label = i18n.t("html_model_deygout") if result.diff_method == "deygout" else i18n.t("html_model_single")
    env_label   = i18n.t(f"env_{result.env_type}")

    terrain_rows = "\n".join(
        f"<tr><td>{d:.4f}</td><td>{h:.2f}</td></tr>"
        for d, h in zip(terrain.d_km_axis, terrain.raw_elevs)
    )

    # 経路オーバーレイ地図セクション。map_b64 が無い（タイル取得失敗）ときは
    # 地図を省いて注記を表示する（レポート自体は必ず生成される）。
    if map_b64:
        map_block = (
            f'<img class="graph" src="data:image/png;base64,{map_b64}" '
            f'alt="{_html.escape(i18n.t("html_map_title"))}">'
        )
    else:
        map_block = (
            f'<p class="map-note">{_html.escape(i18n.t("html_map_unavailable"))}</p>'
        )

    html = f"""<!DOCTYPE html>
<html lang="{i18n.t('html_lang')}">
<head>
<meta charset="UTF-8">
<title>{i18n.t('html_path_title')} — {path_id_esc}</title>
<style>
body{{font-family:Arial,sans-serif;font-size:13px;margin:20px;background:#f5f5f5}}
h1{{color:#333;margin-bottom:4px}}
p.sub{{color:#888;font-size:11px;margin:0 0 14px}}
.cards{{display:flex;gap:12px;margin-bottom:16px}}
.card{{background:white;border-radius:8px;padding:12px 20px;box-shadow:0 1px 3px rgba(0,0,0,.12);text-align:center;min-width:100px}}
.card .lbl{{font-size:10px;color:#999;text-transform:uppercase}}
.card .val{{font-size:22px;font-weight:bold;color:#333}}
.card.ok .val{{color:#2e7d32}}.card.ng .val{{color:#c62828}}
.graph{{width:100%;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.15);margin-bottom:16px}}
.map-note{{color:#999;font-size:12px;font-style:italic;background:white;border-radius:8px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,.12);margin-bottom:16px}}
.cols{{display:flex;gap:16px;margin-bottom:16px}}
.col{{flex:1;background:white;border-radius:8px;padding:14px 18px;box-shadow:0 1px 3px rgba(0,0,0,.12)}}
.col h3{{margin:0 0 10px;font-size:13px;color:#455a64;border-bottom:1px solid #eee;padding-bottom:6px}}
table.info{{border-collapse:collapse;width:100%}}
table.info td{{padding:4px 6px;border-bottom:1px solid #f0f0f0;font-size:12px}}
table.info td:first-child{{color:#888;width:50%}}
details{{background:white;border-radius:8px;padding:10px 16px;box-shadow:0 1px 3px rgba(0,0,0,.12)}}
summary{{cursor:pointer;font-weight:bold;color:#455a64;font-size:12px}}
table.terrain{{border-collapse:collapse;width:100%;margin-top:8px;font-size:11px}}
table.terrain th{{background:#455a64;color:white;padding:4px 8px;text-align:left}}
table.terrain td{{padding:3px 8px;border-bottom:1px solid #eee}}
footer{{margin-top:14px;color:#bbb;font-size:10px}}
</style>
</head>
<body>
<h1>{i18n.t('html_path_title')} — {path_id_esc}</h1>
<p class="sub">{i18n.t('html_generated')}: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} &nbsp;|&nbsp; {version.APP_FULL}</p>

<div class="cards">
  <div class="card {status_cls}"><div class="lbl">{i18n.t('html_status')}</div><div class="val">{result.status}</div></div>
  <div class="card"><div class="lbl">{i18n.t('html_rx_level')}</div><div class="val">{result.p_rx:.1f} dBm</div></div>
  <div class="card {status_cls}"><div class="lbl">{i18n.t('html_act_margin')}</div><div class="val">{result.actual_margin:+.1f} dB</div></div>
  <div class="card"><div class="lbl">{i18n.t('html_total_loss')}</div><div class="val">{result.total_loss:.1f} dB</div></div>
</div>

<img class="graph" src="data:image/png;base64,{img_b64}" alt="Terrain Profile">
{map_block}

<div class="cols">
  <div class="col">
    <h3>{i18n.t('html_site_info')}</h3>
    <table class="info">
      <tr><td>{i18n.t('html_tx_coords')}</td><td>{tx_coords}</td></tr>
      <tr><td>{i18n.t('html_rx_coords')}</td><td>{rx_coords}</td></tr>
      <tr><td>{i18n.t('html_tx_height')}</td><td>{h_tx:.1f} m</td></tr>
      <tr><td>{i18n.t('html_rx_height')}</td><td>{h_rx:.1f} m</td></tr>
      <tr><td>{i18n.t('html_slant_dist')}</td><td>{result.slant_dist_km:.3f} km</td></tr>
      <tr><td>{i18n.t('html_horiz_dist')}</td><td>{terrain.horiz_dist_km:.3f} km</td></tr>
    </table>
    <h3 style="margin-top:14px">{i18n.t('html_radio_settings')}</h3>
    <table class="info">
      <tr><td>{i18n.t('html_frequency')}</td><td>{params.freq_mhz} MHz</td></tr>
      <tr><td>{i18n.t('html_tx_power')}</td><td>{params.p_tx} dBm</td></tr>
      <tr><td>{i18n.t('html_tx_gain')}</td><td>{params.gain_tx} dBi</td></tr>
      <tr><td>{i18n.t('html_rx_gain')}</td><td>{params.gain_rx} dBi</td></tr>
      <tr><td>{i18n.t('html_sensitivity')}</td><td>{params.sens} dBm</td></tr>
    </table>
  </div>
  <div class="col">
    <h3>{i18n.t('html_link_budget')}</h3>
    <table class="info">
      <tr><td>{i18n.t('html_eirp')}</td><td>{result.eirp:.2f} dBm</td></tr>
      <tr><td>{i18n.t('html_fspl')}</td><td>{result.fspl:.2f} dB</td></tr>
      <tr><td>{i18n.t('html_diff_loss')}</td><td>{result.diff_loss:.2f} dB</td></tr>
      <tr><td>{i18n.t('html_veg_loss')}</td><td>{result.veg_loss:.2f} dB</td></tr>
      <tr><td>{i18n.t('html_env_loss')}</td><td>{result.env_loss:.2f} dB</td></tr>
      <tr><td>{i18n.t('html_rain_loss')}</td><td>{result.rain_loss:.2f} dB</td></tr>
      <tr><td>{i18n.t('html_gas_loss')}</td><td>{result.gas_loss:.2f} dB</td></tr>
      <tr><td><b>{i18n.t('html_total_loss_row')}</b></td><td><b>{result.total_loss:.2f} dB</b></td></tr>
      <tr><td>{i18n.t('html_rx_ant_gain')}</td><td>+{params.gain_rx:.2f} dBi</td></tr>
      <tr><td><b>{i18n.t('html_rx_level')}</b></td><td><b>{result.p_rx:.2f} dBm</b></td></tr>
      <tr><td>{i18n.t('html_threshold')}</td><td>{params.sens:.2f} dBm</td></tr>
      <tr><td><b>{i18n.t('html_act_margin')}</b></td><td><b>{result.actual_margin:+.2f} dB</b></td></tr>
    </table>
    <h3 style="margin-top:14px">{i18n.t('html_environment')}</h3>
    <table class="info">
      <tr><td>{i18n.t('html_env_type')}</td><td>{env_label}</td></tr>
      <tr><td>{i18n.t('html_diff_model')}</td><td>{model_label}</td></tr>
      <tr><td>{i18n.t('html_k_factor')}</td><td>{result.current_k:.1f}</td></tr>
      <tr><td>{i18n.t('html_f1_obstruct')}</td><td>{result.blocked_ratio:.1f} %</td></tr>
      <tr><td>{i18n.t('html_rain_rate')}</td><td>{params.rain_rate} mm/h</td></tr>
    </table>
  </div>
</div>

<details>
<summary>{i18n.t('html_terrain_data')} ({terrain.num_samples} points)</summary>
<table class="terrain">
<thead><tr><th>{i18n.t('html_dist_col')}</th><th>{i18n.t('html_elev_col')}</th></tr></thead>
<tbody>
{terrain_rows}
</tbody>
</table>
</details>

<footer>{version.APP_FULL} — {i18n.t('html_batch_mode')}</footer>
</body>
</html>"""

    with open(os.path.join(save_dir, "report.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ============================================================
# サマリ出力
# ============================================================
def _save_summary_csv(results: list[PathResult], batch_dir: str) -> None:
    path = os.path.join(batch_dir, "summary.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "status", "freq_mhz", "h_tx", "h_rx",
            "rx_dbm", "margin_db",
            "fspl_db", "diff_db", "veg_db", "env_db",
            "rain_db", "gas_db", "total_loss_db",
            "slant_km", "f1_pct", "note", "error",
        ])
        for pr in results:
            freq_val = f"{pr.params.freq_mhz:.1f}" if pr.params else ""
            h_tx_val = f"{pr.row.h_tx:.1f}"
            h_rx_val = f"{pr.row.h_rx:.1f}"
            if pr.result is not None:
                r = pr.result
                writer.writerow([
                    pr.row.path_id, r.status,
                    freq_val, h_tx_val, h_rx_val,
                    f"{r.p_rx:.2f}",          f"{r.actual_margin:.2f}",
                    f"{r.fspl:.2f}",           f"{r.diff_loss:.2f}",
                    f"{r.veg_loss:.2f}",       f"{r.env_loss:.2f}",
                    f"{r.rain_loss:.2f}",      f"{r.gas_loss:.2f}",
                    f"{r.total_loss:.2f}",
                    f"{r.slant_dist_km:.3f}",  f"{r.blocked_ratio:.1f}",
                    pr.row.note, "",
                ])
            else:
                writer.writerow([
                    pr.row.path_id, "ERROR",
                    freq_val, h_tx_val, h_rx_val,
                    "", "", "", "", "", "", "", "", "", "", "",
                    pr.row.note, str(pr.error),
                ])


def save_summary_html(results: list[PathResult], batch_dir: str) -> None:
    ok_count  = sum(1 for pr in results if pr.result is not None and pr.result.status == "OK")
    ng_count  = sum(1 for pr in results if pr.result is not None and pr.result.status != "OK")
    err_count = sum(1 for pr in results if pr.result is None)
    total     = len(results)

    rows_html = ""
    for pr in results:
        freq_disp = f"{pr.params.freq_mhz:.1f}" if pr.params else "—"
        h_tx_disp = f"{pr.row.h_tx:.1f}"
        h_rx_disp = f"{pr.row.h_rx:.1f}"
        pid_safe  = pr.row.path_id          # validated: [A-Za-z0-9_-]+ — safe for href
        pid_esc   = _html.escape(pr.row.path_id)
        note_esc  = _html.escape(pr.row.note)
        if pr.result is None:
            error_esc = _html.escape(str(pr.error))
            rows_html += (
                f"<tr class='err'>"
                f"<td>{pid_esc}</td>"
                f"<td class='s-err'>ERROR</td>"
                f"<td>{freq_disp}</td><td>{h_tx_disp}</td><td>{h_rx_disp}</td>"
                f"<td colspan='11'>{error_esc}</td>"
                f"<td>{note_esc}</td>"
                f"<td></td></tr>\n"
            )
            continue
        r   = pr.result
        cls = "ok" if r.status == "OK" else "ng"
        rows_html += (
            f"<tr class='{cls}'>"
            f"<td>{pid_esc}</td>"
            f"<td class='s-{cls}'>{r.status}</td>"
            f"<td>{freq_disp}</td>"
            f"<td>{h_tx_disp}</td>"
            f"<td>{h_rx_disp}</td>"
            f"<td>{r.p_rx:.1f}</td>"
            f"<td>{r.actual_margin:+.1f}</td>"
            f"<td>{r.fspl:.1f}</td>"
            f"<td>{r.diff_loss:.1f}</td>"
            f"<td>{r.veg_loss:.1f}</td>"
            f"<td>{r.env_loss:.1f}</td>"
            f"<td>{r.rain_loss:.1f}</td>"
            f"<td>{r.gas_loss:.1f}</td>"
            f"<td>{r.total_loss:.1f}</td>"
            f"<td>{r.slant_dist_km:.3f}</td>"
            f"<td>{r.blocked_ratio:.1f}</td>"
            f"<td>{note_esc}</td>"
            f"<td><a href='{pid_safe}/report.html'>"
            f"<img src='{pid_safe}/profile.png' style='max-height:60px;border:1px solid #ddd;border-radius:3px;vertical-align:middle;'>"
            f"</a></td></tr>\n"
        )

    html = f"""<!DOCTYPE html>
<html lang="{i18n.t('html_lang')}">
<head>
<meta charset="UTF-8">
<title>{i18n.t('html_batch_title')}</title>
<style>
body{{font-family:Arial,sans-serif;font-size:13px;margin:20px;background:#f5f5f5}}
h1{{color:#333;margin-bottom:4px}}
p.sub{{color:#888;font-size:11px;margin:0 0 16px}}
.cards{{display:flex;gap:12px;margin-bottom:20px}}
.card{{background:white;border-radius:8px;padding:14px 20px;box-shadow:0 1px 3px rgba(0,0,0,.12);text-align:center;min-width:80px}}
.card .lbl{{font-size:10px;color:#999;text-transform:uppercase}}
.card .val{{font-size:28px;font-weight:bold;color:#333}}
.card.ok .val{{color:#2e7d32}}.card.ng .val{{color:#c62828}}.card.err .val{{color:#e65100}}
table{{border-collapse:collapse;width:100%;background:white;box-shadow:0 1px 3px rgba(0,0,0,.12)}}
th{{background:#455a64;color:white;padding:7px 10px;text-align:left;font-size:11px;white-space:nowrap}}
td{{padding:5px 10px;border-bottom:1px solid #eee;font-size:12px;white-space:nowrap}}
tr.ok{{background:#f1f8e9}}tr.ng{{background:#fff8e1}}tr.err{{background:#fce4ec}}
.s-ok{{color:#2e7d32;font-weight:bold}}.s-ng{{color:#c62828;font-weight:bold}}.s-err{{color:#bf360c;font-weight:bold}}
footer{{margin-top:14px;color:#bbb;font-size:10px}}
</style>
</head>
<body>
<h1>{i18n.t('html_batch_title')}</h1>
<p class="sub">{i18n.t('html_generated')}: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} &nbsp;|&nbsp; {version.APP_FULL}</p>
<div class="cards">
  <div class="card"><div class="lbl">{i18n.t('html_total')}</div><div class="val">{total}</div></div>
  <div class="card ok"><div class="lbl">{i18n.t('html_ok')}</div><div class="val">{ok_count}</div></div>
  <div class="card ng"><div class="lbl">{i18n.t('html_ng')}</div><div class="val">{ng_count}</div></div>
  <div class="card err"><div class="lbl">{i18n.t('html_error')}</div><div class="val">{err_count}</div></div>
</div>
<table>
<thead>
<tr>
  <th>{i18n.t('html_col_id')}</th><th>{i18n.t('html_col_status')}</th>
  <th>{i18n.t('html_col_freq')}</th><th>{i18n.t('html_col_h_tx')}</th><th>{i18n.t('html_col_h_rx')}</th>
  <th>{i18n.t('html_col_rx')}</th><th>{i18n.t('html_col_margin')}</th>
  <th>{i18n.t('html_col_fspl')}</th><th>{i18n.t('html_col_diff')}</th>
  <th>{i18n.t('html_col_veg')}</th><th>{i18n.t('html_col_env')}</th>
  <th>{i18n.t('html_col_rain')}</th><th>{i18n.t('html_col_gas')}</th>
  <th>{i18n.t('html_col_total_loss')}</th><th>{i18n.t('html_col_slant')}</th>
  <th>{i18n.t('html_col_f1')}</th><th>{i18n.t('html_col_note')}</th><th>{i18n.t('html_col_graph')}</th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
<footer>{version.APP_FULL} — {i18n.t('html_batch_mode')}</footer>
</body>
</html>"""

    with open(os.path.join(batch_dir, "summary.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ============================================================
# KML 出力
# ============================================================

def _kml_line_coords(lats: np.ndarray, lons: np.ndarray, alts: np.ndarray) -> str:
    """KML <coordinates> 内容（lon,lat,alt の改行区切り）を返す。"""
    return "\n".join(
        f"          {float(lo):.6f},{float(la):.6f},{float(al):.1f}"
        for la, lo, al in zip(lats, lons, alts)
    )


def _find_obs_segments(mask: np.ndarray) -> list[tuple[int, int]]:
    """連続する True 区間の (start, end) インデックスリスト（両端 inclusive）を返す。"""
    segs: list[tuple[int, int]] = []
    n, i = len(mask), 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            segs.append((i, j - 1))
            i = j
        else:
            i += 1
    return segs


def save_path_kml(
    terrain:  models.TerrainProfile,
    result:   models.LinkBudgetResult,
    params:   sim.SimParams,
    h_tx:     float,
    h_rx:     float,
    save_dir: str,
) -> None:
    """per-path の path.kml を生成する（Google Earth で 3D 表示可能）。

    要素:
      - TX / RX Placemark
      - Terrain Profile（actual elevation）
      - Line of Sight（OK=緑 / NG=橙）
      - 1st Fresnel Zone 上辺・下辺
      - Fresnel Obstruction（遮蔽区間を赤でハイライト）
    """
    N    = terrain.num_samples
    t    = np.linspace(0, 1, N)
    lats = params.lat_tx + (params.lat_rx - params.lat_tx) * t
    lons = params.lon_tx + (params.lon_rx - params.lon_tx) * t
    elev = terrain.raw_elevs.astype(float)

    tx_alt = float(elev[0])  + h_tx
    rx_alt = float(elev[-1]) + h_rx
    los    = np.linspace(tx_alt, rx_alt, N)
    f1     = models.fresnel_zone_radii(terrain.d_km_axis, terrain.horiz_dist_km, params.freq_mhz)

    los_color = "ff00aa00" if result.status == "OK" else "ff00a5ff"
    path_id   = _html.escape(os.path.basename(save_dir))
    desc_esc  = _html.escape(
        f"Freq: {params.freq_mhz} MHz | RX: {result.p_rx:.1f} dBm | "
        f"Margin: {result.actual_margin:+.1f} dB | Status: {result.status}"
    )

    # 遮蔽区間（地形がフレネル下辺を超える部分）
    obstructed = elev > (los - f1)
    obs_xml = ""
    for s, e in _find_obs_segments(obstructed):
        obs_xml += f"""
    <Placemark>
      <name>Obstruction</name>
      <styleUrl>#obs</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats[s:e+1], lons[s:e+1], elev[s:e+1])}
        </coordinates>
      </LineString>
    </Placemark>"""

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>{path_id}</name>
  <description>{desc_esc}</description>

  <Style id="terrain"><LineStyle><color>ff13458b</color><width>2</width></LineStyle></Style>
  <Style id="los"><LineStyle><color>{los_color}</color><width>2</width></LineStyle></Style>
  <Style id="fresnel"><LineStyle><color>80ffff00</color><width>1</width></LineStyle></Style>
  <Style id="obs"><LineStyle><color>ff0000ff</color><width>4</width></LineStyle></Style>

  <Placemark>
    <name>TX</name>
    <description>{_html.escape(f"h_tx={h_tx:.1f} m | {params.freq_mhz} MHz")}</description>
    <Point>
      <altitudeMode>absolute</altitudeMode>
      <coordinates>{params.lon_tx:.6f},{params.lat_tx:.6f},{tx_alt:.1f}</coordinates>
    </Point>
  </Placemark>
  <Placemark>
    <name>RX</name>
    <description>{_html.escape(f"h_rx={h_rx:.1f} m | {result.p_rx:.1f} dBm ({result.status})")}</description>
    <Point>
      <altitudeMode>absolute</altitudeMode>
      <coordinates>{params.lon_rx:.6f},{params.lat_rx:.6f},{rx_alt:.1f}</coordinates>
    </Point>
  </Placemark>

  <Folder>
    <name>Terrain Profile</name>
    <Placemark>
      <name>Terrain</name>
      <styleUrl>#terrain</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats, lons, elev)}
        </coordinates>
      </LineString>
    </Placemark>
  </Folder>

  <Folder>
    <name>Line of Sight</name>
    <Placemark>
      <name>LoS ({_html.escape(result.status)})</name>
      <styleUrl>#los</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats, lons, los)}
        </coordinates>
      </LineString>
    </Placemark>
  </Folder>

  <Folder>
    <name>1st Fresnel Zone</name>
    <Placemark>
      <name>Upper Boundary</name>
      <styleUrl>#fresnel</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats, lons, los + f1)}
        </coordinates>
      </LineString>
    </Placemark>
    <Placemark>
      <name>Lower Boundary</name>
      <styleUrl>#fresnel</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats, lons, los - f1)}
        </coordinates>
      </LineString>
    </Placemark>
  </Folder>

  <Folder>
    <name>Fresnel Obstruction</name>{obs_xml}
  </Folder>

</Document>
</kml>"""

    with open(os.path.join(save_dir, "path.kml"), "w", encoding="utf-8") as f:
        f.write(kml)


def save_summary_kml(results: list[PathResult], batch_dir: str) -> None:
    """全パスを OK / NG / Error フォルダ分けした summary.kml を生成する。"""
    ok_xml = ng_xml = err_xml = ""

    for pr in results:
        pid_esc = _html.escape(pr.row.path_id)
        if pr.result is not None and pr.terrain is not None and pr.params is not None:
            tx_alt   = float(pr.terrain.raw_elevs[0])  + pr.params.h_tx
            rx_alt   = float(pr.terrain.raw_elevs[-1]) + pr.params.h_rx
            coords   = (
                f"{pr.row.lon_tx:.6f},{pr.row.lat_tx:.6f},{tx_alt:.1f} "
                f"{pr.row.lon_rx:.6f},{pr.row.lat_rx:.6f},{rx_alt:.1f}"
            )
            freq_s   = f"{pr.params.freq_mhz:.1f} MHz"
            desc_esc = _html.escape(
                f"Freq: {freq_s} | RX: {pr.result.p_rx:.1f} dBm | "
                f"Margin: {pr.result.actual_margin:+.1f} dB"
            )
            style = "ok" if pr.result.status == "OK" else "ng"
            pm = (
                f"    <Placemark><name>{pid_esc}</name>"
                f"<description>{desc_esc}</description>"
                f"<styleUrl>#{style}</styleUrl>"
                f"<LineString><altitudeMode>absolute</altitudeMode>"
                f"<coordinates>{coords}</coordinates>"
                f"</LineString></Placemark>\n"
            )
            if pr.result.status == "OK":
                ok_xml += pm
            else:
                ng_xml += pm
        else:
            # エラーパス: 地形データなし → 地表面クランプにフォールバック
            coords   = (
                f"{pr.row.lon_tx:.6f},{pr.row.lat_tx:.6f},0 "
                f"{pr.row.lon_rx:.6f},{pr.row.lat_rx:.6f},0"
            )
            desc_esc = _html.escape(str(pr.error))
            err_xml += (
                f"    <Placemark><name>{pid_esc}</name>"
                f"<description>{desc_esc}</description>"
                f"<styleUrl>#err</styleUrl>"
                f"<LineString><tessellate>1</tessellate>"
                f"<altitudeMode>clampToGround</altitudeMode>"
                f"<coordinates>{coords}</coordinates>"
                f"</LineString></Placemark>\n"
            )

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>Batch Summary</name>
  <description>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {version.APP_FULL}</description>

  <Style id="ok"><LineStyle><color>ff00aa00</color><width>3</width></LineStyle></Style>
  <Style id="ng"><LineStyle><color>ff00a5ff</color><width>3</width></LineStyle></Style>
  <Style id="err"><LineStyle><color>ff0000ff</color><width>3</width></LineStyle></Style>

  <Folder><name>OK</name><open>1</open>
{ok_xml}  </Folder>
  <Folder><name>NG</name><open>1</open>
{ng_xml}  </Folder>
  <Folder><name>Error</name><open>0</open>
{err_xml}  </Folder>

</Document>
</kml>"""

    with open(os.path.join(batch_dir, "summary.kml"), "w", encoding="utf-8") as f:
        f.write(kml)
