"""
webui/exports.py
================
作業面の成果物の**本文**を作る（ステージ10d）＝ファイルを書くのは画面（Edge のファイルの API）。

🔑 中身は旧画面の書き手そのもの（`report/` の `*_csv_text`）＝出力契約（`core/output_contract.py`）の
列・書式・ファイル名が 2 つの画面で同じになる。結果フォルダは作らない（成果物は選んだ場所に 1 つずつ＝
2026-11-08 経営者）。

`report/` は matplotlib を引く＝関数の中で読む（起動を重くしない・I-232）。
"""

from __future__ import annotations

from typing import Any


def sheet_csv(workspace: str, run: Any) -> "tuple[str, str]":
    """作業面の全件の結果 → `(ファイル名, CSV の本文)`。中身の形は `Worker.sheet_run` の docstring。"""
    if workspace == "batch":
        from report import report_summary
        return "summary.csv", report_summary.summary_csv_text(run)
    if workspace == "multihop":
        from report import multihop
        return "hops.csv", multihop.hops_csv_text(run)
    if workspace == "scenario":
        from report import report_scenario
        return "scenario.csv", report_scenario.scenario_csv_text(run)
    raise ValueError(f"no CSV for workspace: {workspace}")
