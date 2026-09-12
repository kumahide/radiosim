"""
residuals.py
============
実測突合せ（ロードマップ 3.4 段5）の残差計算。純粋計算のみ・副作用ゼロ。

🔑 **モデルは1行も変えない**（3.4 の決定＝測るだけ）。ここが返すのは「予測 − 実測」の
残差[dB]と、その環境区分×帯域×距離帯ごとの中央値・ばらつき・件数だけで、採否の
判断（3.5 のローカル較正）はここでは行わない。

`core/sensitivity.py` / `core/ground_reflection.py` と同じ位置づけの独立モジュール。
**report 側（`report/batch.py` の `PathRow`/`PathResult`）には依存しない**＝
3.0 の項目 9 で踏んだ壁（core → report の逆流）を避けるため、この層は生の数値
（predicted_dbm・measured_dbm など）だけを受け取る。`PathResult` からの抽出は
report 側（`report/residuals.py`）が担う。

帯域の境目は `models.VEG_COEFF_RANGE_GHZ`（植生減衰の係数の境目）を単一ソースに
流用する＝3.4 が新しい境目を作ると、同じ周波数が節によって違う帯域に分類される。
距離帯の境目は実測データの蓄積が無い段階での運用値（調整可能）。
"""
from dataclasses import dataclass

import numpy as np

from core import models

#: 距離帯の境目 [km]（昇順）。固定回線の実務レンジを想定した運用値＝
#: 実測データが溜まったら見直す（3.5 の材料）。
DISTANCE_BAND_EDGES_KM: tuple[float, float] = (5.0, 20.0)

#: 測定方法の文字列のうち「スポット測定」と見なす値（`report.batch.PathRow.meas_method`
#: の自由記述を正規化して比較する。大文字小文字・前後空白を無視）。
SPOT_METHOD_LABELS: frozenset[str] = frozenset({"spot", "スポット"})

#: 環境区分が空欄（自己判定なし）の行に割り当てる層のラベル。
UNSPECIFIED_LABEL: str = "unspecified"


@dataclass
class ResidualSample:
    """実測1点ぶんの残差。"""
    path_id:        str
    residual_db:    float   # predicted(p_rx) − (measured + feeder_loss)
    env_class:      str     # 空欄なら UNSPECIFIED_LABEL に正規化済み
    band:           str     # band_label() の戻り値
    distance_band:  str     # distance_band_label() の戻り値
    is_spot:        bool    # meas_method がスポット測定か


@dataclass
class LayerStats:
    """env_class × band × distance_band の1層ぶんの統計。"""
    env_class:      str
    band:           str
    distance_band:  str
    n:              int
    median_db:      float
    iqr_db:         float   # ばらつき＝75%ile − 25%ile（n<2 のときは 0.0）


def band_label(freq_mhz: float) -> str:
    """周波数 [MHz] を帯域ラベルへ分類する（境目は `models.VEG_COEFF_RANGE_GHZ`）。"""
    lo_ghz, hi_ghz = models.VEG_COEFF_RANGE_GHZ
    freq_ghz = float(freq_mhz) / 1000.0
    if freq_ghz < lo_ghz:
        return f"<{lo_ghz:g}GHz"
    if freq_ghz <= hi_ghz:
        return f"{lo_ghz:g}-{hi_ghz:g}GHz"
    return f">{hi_ghz:g}GHz"


def distance_band_label(distance_km: float) -> str:
    """水平距離 [km] を距離帯ラベルへ分類する（境目は `DISTANCE_BAND_EDGES_KM`）。"""
    lo, hi = DISTANCE_BAND_EDGES_KM
    d = float(distance_km)
    if d < lo:
        return f"<{lo:g}km"
    if d <= hi:
        return f"{lo:g}-{hi:g}km"
    return f">{hi:g}km"


def compute_residual_db(
    predicted_dbm: float, measured_dbm: float, feeder_loss_db: float | None,
) -> float:
    """残差[dB] = 予測受信電力 − 実測受信電力（給電線損失を補正後）。

    `measured_dbm` は受信機（測定器）側で読んだ値＝アンテナ端との間に測定用の
    給電線の損失が乗っている。予測 `predicted_dbm`（`LinkBudgetResult.p_rx`）は
    アンテナ端の値なので、測定値へ損失を**加算**してアンテナ端へ引き戻してから
    比べる（測定器側の損失が増えるほど読み取り値は小さくなるため）。
    """
    loss = 0.0 if feeder_loss_db is None else float(feeder_loss_db)
    return float(predicted_dbm) - (float(measured_dbm) + loss)


def build_sample(
    *,
    path_id: str,
    predicted_dbm: float,
    measured_dbm: float,
    feeder_loss_db: float | None,
    env_class: str,
    freq_mhz: float,
    distance_km: float,
    meas_method: str,
) -> ResidualSample:
    """生の数値から1点ぶんの `ResidualSample` を作る。"""
    normalized_env = env_class.strip() if env_class else ""
    is_spot = meas_method.strip().lower() in SPOT_METHOD_LABELS
    return ResidualSample(
        path_id       = path_id,
        residual_db   = compute_residual_db(predicted_dbm, measured_dbm, feeder_loss_db),
        env_class     = normalized_env or UNSPECIFIED_LABEL,
        band          = band_label(freq_mhz),
        distance_band = distance_band_label(distance_km),
        is_spot        = is_spot,
    )


def compute_layered_stats(
    samples: "list[ResidualSample]", *, exclude_spot: bool = False,
) -> "list[LayerStats]":
    """層（env_class × band × distance_band）ごとの中央値・ばらつき・件数。

    `exclude_spot=True` ならスポット測定の標本を統計から除く（ロードマップの
    「スポット測定は統計から除外可」）。戻り値は件数の多い順（同数なら層キーの
    昇順）。件数 0 の層は作らない。
    """
    by_layer: "dict[tuple[str, str, str], list[float]]" = {}
    for s in samples:
        if exclude_spot and s.is_spot:
            continue
        key = (s.env_class, s.band, s.distance_band)
        by_layer.setdefault(key, []).append(s.residual_db)

    stats: "list[LayerStats]" = []
    for (env_class, band, distance_band), values in by_layer.items():
        arr = np.asarray(values, dtype=float)
        n = len(arr)
        median = float(np.median(arr))
        iqr = float(np.percentile(arr, 75) - np.percentile(arr, 25)) if n >= 2 else 0.0
        stats.append(LayerStats(
            env_class     = env_class,
            band          = band,
            distance_band = distance_band,
            n             = n,
            median_db     = median,
            iqr_db        = iqr,
        ))

    stats.sort(key=lambda s: (-s.n, s.env_class, s.band, s.distance_band))
    return stats
