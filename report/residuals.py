"""
residuals.py（report 側）
=========================
`core/residuals.py` の純関数へ、バッチ実行結果（`report.batch.PathResult`）から
生の数値を抽出して渡す層。**report → core の一方向**（core は report を知らない）。
"""
from core import residuals as core_residuals
from report import batch


def samples_from_results(results: "list[batch.PathResult]") -> "list[core_residuals.ResidualSample]":
    """実測値（`meas_dbm`）を持つ経路だけを残差標本に変換する。

    `result.ok` でない行（計算が失敗した経路）や `meas_dbm` が未入力の行は
    統計を汚すので除外する（ロードマップ「省略した行は残差計算の対象から除外」）。
    """
    samples: "list[core_residuals.ResidualSample]" = []
    for pr in results:
        row = pr.row
        if row.meas_dbm is None or not pr.ok or pr.result is None or pr.params is None:
            continue
        samples.append(core_residuals.build_sample(
            path_id        = row.path_id,
            predicted_dbm  = pr.result.p_rx,
            measured_dbm   = row.meas_dbm,
            feeder_loss_db = row.feeder_loss_db,
            env_class      = row.env_class,
            freq_mhz       = pr.params.freq_mhz,
            distance_km    = pr.terrain.horiz_dist_km if pr.terrain is not None else 0.0,
            meas_method    = row.meas_method,
        ))
    return samples
