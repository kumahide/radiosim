"""
report_scenario.py
==================
条件探索（A-1 比較 / A-2 スイープ）の成果物 CSV（ヘッドレス・UI 知識ゼロ）。

`scenario.ScenarioRun` を受け取り、`scenario.csv` の本文を返す（ファイルへ書くのは
呼ぶ側＝新しい画面の保存）。セルの安全化と末尾 2 列は [report_common](report_common.py)。
A4 縦 1 枚の帳票（折れ線＋表・比較の差分表）は Pro へ移した（Link 1.0 ステージ11b）。
"""

from __future__ import annotations

import csv
import io

from core import calc_profile
from core import output_contract
from core import scenario as scn
from core import units
from report import report_common


def scenario_csv_text(run: scn.ScenarioRun) -> str:
    """`scenario.csv` の本文（行末 `\\r\\n`）。ファイルへ書くのは呼ぶ側（新しい画面の保存・Pro の帳票）。"""
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    # 見出しは出力契約が単一ソース（→ core/output_contract.py）。
    # 3.1（I-112）で 2 列目を固定名 `axis_value` にし、軸の名前は末尾の
    # `axis` 列へ移した＝どの軸を選んでも見出しの重複が起きない。
    w.writerow(list(output_contract.SCENARIO_CSV_COLUMNS))
    base = run.base_params
    for p in run.points:
        o = p.overrides
        def val(name, default):
            return o.get(name, default)
        r = p.result
        # ラベルは今のところ i18n 由来か数値だが、CSV セルの安全化は
        # 書き手ごとの判断にしない（→ report_common.csv_cell のセクション）。
        label = report_common.csv_cell(p.label)
        w.writerow([
            label, o.get(run.axis, "") if run.axis else label, r.status,
            # 桁は `units.csv_db` が単一ソース（0.1 dB）＝**書式も出力契約**。
            units.csv_db(r.p_rx), units.csv_db(r.actual_margin),
            units.csv_db(r.total_loss),
            units.csv_db(r.fspl), units.csv_db(r.diff_loss),
            units.csv_db(r.veg_loss), units.csv_db(r.env_loss),
            units.csv_db(r.rain_loss), units.csv_db(r.gas_loss),
            units.csv_blocked_ratio(r.blocked_ratio),
            units.csv_distance(r.slant_dist_km),
            f"{val('freq_mhz', base.freq_mhz):g}",
            f"{val('p_tx', base.p_tx):g}",
            f"{val('gain_tx', base.gain_tx):g}",
            f"{val('gain_rx', base.gain_rx):g}",
            f"{val('sens', base.sens):g}",
            f"{p.h_tx:g}", f"{p.h_rx:g}",
            f"{val('veg_h', base.veg_h):g}",
            f"{val('rain_rate', base.rain_rate):g}",
            val("env_type", base.env_type), val("diff_method", base.diff_method),
            # 頭打ちしない側の量（F1 半径の何倍まで食い込んでいるか）＝I-077。
            units.csv_f1_depth(r.blocked_ratio),
            # 末尾＝出力契約の規約 1（追加は末尾のみ）。軸の名前そのもの
            # （比較では空文字列）＝I-112。
            run.axis,
            # DEM 取得の失敗率（ISSUES.md B-025 ③）＝条件探索は terrain を
            # 1 回だけ取得して固定し条件を振る（`core/scenario.py`）ので
            # **全行が同じ値**になる。
            units.csv_fail_pct(run.terrain.fail_pct),
            base.calc_profile, _point_input_hash(base, p),
        ])
    return buf.getvalue()


def _point_input_hash(base, point) -> str:
    """条件 1 つの入力ハッシュ（ベース＋上書き＋その行の高さ）。"""
    return calc_profile.input_hash(
        base, {**point.overrides, "h_tx": point.h_tx, "h_rx": point.h_rx})
