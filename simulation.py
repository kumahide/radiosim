"""
simulation.py
=============
ViewModel 相当のオーケストレーター。

責務:
  - 標高取得（別スレッド）のスケジューリング
  - models.py の各計算関数を順番に呼び出す
  - 結果を LinkBudgetResult として View に返す
  - 保存パッケージ（PNG / CSV / JSON / TXT）の生成

View はこのモジュールを呼ぶだけでよく、計算や I/O の詳細を知らない。
"""

import csv
import json
import logging
import os
import threading
from dataclasses import asdict
from datetime import datetime
from typing import Callable

import numpy as np

import infrastructure as infra
import models

logger = logging.getLogger("radiosim")


# ============================================================
# シミュレーションパラメータ
# ============================================================
class SimParams:
    """
    View から渡される実行パラメータ。
    文字列の設定値を型変換して保持する。
    """
    def __init__(self, c: dict[str, str]) -> None:
        s_parts = c["start"].split(",")
        e_parts = c["end"].split(",")
        self.lat_tx:      float = float(s_parts[0].strip())
        self.lon_tx:      float = float(s_parts[1].strip())
        self.lat_rx:      float = float(e_parts[0].strip())
        self.lon_rx:      float = float(e_parts[1].strip())
        self.h_tx:        float = float(c["h_tx"])
        self.h_rx:        float = float(c["h_rx"])
        self.freq_mhz:    float = float(c["freq"])
        self.p_tx:        float = float(c["p_tx"])
        self.gain_tx:     float = float(c["gain_tx"])
        self.gain_rx:     float = float(c["gain_rx"])
        self.sens:        float = float(c["sens"])
        self.veg_h:       float = float(c["veg_h"])
        self.k_factor:    float = float(c["k_factor"])
        self.num:         int   = max(10, int(c["samples"]))
        self.diff_method: str   = c.get("diff_method", "deygout")
        self.env_type:    str   = c.get("env_type", models.ENV_DEFAULT)
        self.rain_rate:   float = float(c.get("rain_rate", "0.0"))


# ============================================================
# 標高取得スレッド
# ============================================================
def fetch_elevations(
    params: SimParams,
    on_progress: Callable[[int], None],
    on_complete: Callable[[np.ndarray], None],
    on_error: Callable[[Exception], None],
) -> None:
    """
    GSI DEM から標高を取得する。別スレッドで実行すること。

    Args:
        params:      シミュレーションパラメータ
        on_progress: 1サンプル取得するたびに呼ばれる (index: int)
        on_complete: 全取得完了時に呼ばれる (raw_elevs: np.ndarray)
        on_error:    例外発生時に呼ばれる
    """
    def _run() -> None:
        try:
            logger.info(
                "Simulation started: start=(%s,%s) end=(%s,%s) freq=%.1f MHz samples=%d",
                params.lat_tx, params.lon_tx,
                params.lat_rx, params.lon_rx,
                params.freq_mhz, params.num,
            )
            lats = np.linspace(params.lat_tx, params.lat_rx, params.num)
            lons = np.linspace(params.lon_tx, params.lon_rx, params.num)

            raw_elevs: list[float] = []
            for i, (la, lo) in enumerate(zip(lats, lons)):
                raw_elevs.append(infra.get_elevation(la, lo))
                on_progress(i + 1)

            logger.info("Terrain fetch complete: %d samples", params.num)
            on_complete(np.array(raw_elevs))

        except Exception as ex:
            on_error(ex)

    threading.Thread(target=_run, daemon=True).start()


# ============================================================
# 計算（スライダー変更時など随時呼び出し）
# ============================================================
def run_calculation(
    terrain: models.TerrainProfile,
    h_tx: float,
    h_rx: float,
    params: SimParams,
    rain_rate: float | None = None,
) -> models.LinkBudgetResult:
    """
    TerrainProfile と現在のアンテナ高から LinkBudgetResult を返す。
    GUI スレッドから直接呼んでよい（純粋計算のみ）。

    Args:
        rain_rate: None のとき params.rain_rate を使用。
                   グラフのスライダーから直接渡す場合は float を指定。
    """
    _rain = params.rain_rate if rain_rate is None else rain_rate
    prop   = models.calculate_propagation(
        terrain     = terrain,
        h_tx        = h_tx,
        h_rx        = h_rx,
        freq_mhz    = params.freq_mhz,
        veg_h       = params.veg_h,
        k_factor    = params.k_factor,
        diff_method = params.diff_method,
        env_type    = params.env_type,
        rain_rate   = _rain,
    )
    result = models.calculate_link_budget(
        prop     = prop,
        freq_mhz = params.freq_mhz,
        p_tx     = params.p_tx,
        gain_tx  = params.gain_tx,
        gain_rx  = params.gain_rx,
        sens     = params.sens,
    )
    return result


# ============================================================
# 保存パッケージ
# ============================================================
def save_package(
    fig,                          # matplotlib.figure.Figure
    terrain: models.TerrainProfile,
    result: models.LinkBudgetResult,
    params: SimParams,
    h_tx: float,
    h_rx: float,
) -> str:
    """
    結果一式をタイムスタンプ付きディレクトリに保存する。
    保存先ディレクトリのパスを返す。
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_dir  = os.path.join(infra.RESULTS_DIR, timestamp)
    os.makedirs(save_dir, exist_ok=True)

    _save_graph(fig, save_dir)
    _save_settings(params, h_tx, h_rx, save_dir)
    _save_terrain_csv(terrain, save_dir)
    _save_report(result, params, h_tx, h_rx, save_dir)

    logger.info("Package saved: %s", save_dir)
    return save_dir


def _save_graph(fig, save_dir: str) -> None:
    path = os.path.join(save_dir, "profile.png")
    fig.savefig(path, dpi=150)


def _save_settings(
    params: SimParams,
    h_tx: float,
    h_rx: float,
    save_dir: str,
) -> None:
    settings = {
        "start"       : f"{params.lat_tx}, {params.lon_tx}",
        "end"         : f"{params.lat_rx}, {params.lon_rx}",
        "h_tx"        : h_tx,
        "h_rx"        : h_rx,
        "freq"        : params.freq_mhz,
        "p_tx"        : params.p_tx,
        "gain_tx"     : params.gain_tx,
        "gain_rx"     : params.gain_rx,
        "sens"        : params.sens,
        "veg_h"       : params.veg_h,
        "k_factor"    : params.k_factor,
        "samples"     : params.num,
        "diff_method" : params.diff_method,
        "env_type"    : params.env_type,
        "rain_rate"   : params.rain_rate,
    }
    path = os.path.join(save_dir, "settings.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)


def _save_terrain_csv(terrain: models.TerrainProfile, save_dir: str) -> None:
    path = os.path.join(save_dir, "terrain_profile.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Distance_km", "Elevation_m"])
        for d, h in zip(terrain.d_km_axis, terrain.raw_elevs):
            writer.writerow([round(float(d), 4), round(float(h), 2)])


def _save_report(
    result: models.LinkBudgetResult,
    params: SimParams,
    h_tx: float,
    h_rx: float,
    save_dir: str,
) -> None:
    text = (
        "=== RADIO LINK REPORT ===\n\n"
        f"Date: {datetime.now()}\n\n"
        "[SITE INFO]\n"
        f"TX Site       : {params.lat_tx}, {params.lon_tx}\n"
        f"RX Site       : {params.lat_rx}, {params.lon_rx}\n"
        f"TX Height     : {h_tx:.1f} m\n"
        f"RX Height     : {h_rx:.1f} m\n\n"
        "[RADIO SETTINGS]\n"
        f"Frequency     : {params.freq_mhz} MHz\n"
        f"TX Power      : {params.p_tx} dBm\n"
        f"TX Ant. Gain  : {params.gain_tx} dBi\n"
        f"RX Ant. Gain  : {params.gain_rx} dBi\n"
        f"Sensitivity   : {params.sens} dBm\n\n"
        "[LINK BUDGET]\n"
        f"Diff Model    : {result.diff_method}\n"
        f"Env Type      : {result.env_type}\n"
        f"EIRP          : {result.eirp:.2f} dBm\n"
        f"FSPL          : {result.fspl:.2f} dB\n"
        f"Diffraction   : {result.diff_loss:.2f} dB\n"
        f"Vegetation    : {result.veg_loss:.2f} dB\n"
        f"Env Loss      : {result.env_loss:.2f} dB\n"
        f"Rain Loss     : {result.rain_loss:.2f} dB\n"
        f"Gas Loss      : {result.gas_loss:.2f} dB\n"
        f"Total Loss    : {result.total_loss:.2f} dB\n"
        f"RX Level      : {result.p_rx:.2f} dBm\n"
        f"Sensitivity   : {params.sens:.2f} dBm\n"
        f"Act Margin    : {result.actual_margin:.2f} dB\n"
        f"Status        : {result.status}\n\n"
        "[ENVIRONMENT]\n"
        f"K-Factor      : {result.current_k:.2f}\n"
        f"F1 Obstruct   : {result.blocked_ratio:.1f} %\n"
        f"Slant Dist    : {result.slant_dist_km:.3f} km\n"
    )
    path = os.path.join(save_dir, "report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
