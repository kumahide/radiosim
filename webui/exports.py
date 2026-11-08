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


#: KML を出せる面＝旧画面にある分だけ（`summary.kml`）。中継と条件探索は旧画面にも KML が無い
KML_WORKSPACES: "tuple[str, ...]" = ("batch",)


def sheet_kml(workspace: str, run: Any) -> "tuple[str, str] | None":
    """作業面の全件の結果 → `(ファイル名, KML の本文)`。KML の無い面は None。"""
    if workspace not in KML_WORKSPACES:
        return None
    from report import report_summary
    return "summary.kml", report_summary.summary_kml_text(run)


# ============================================================
# 地図の画像（10d）＝帳票と同じ絵（`report_map.render_paths_map`・north-up・判定の色）を焼く。
# Leaflet の絵はタイルの canvas が無く PNG にできない＝サーバーで焼く（2026-11-08 経営者＝地図と断面図は別々）
# ============================================================
def map_lines(headline: "dict[str, Any]", inputs: "dict[str, str]") -> "list[Any] | None":
    """今の版の見出し → 地図に描く線 `(送信, 受信, 判定の語, 名前)` の並び。描けなければ None。

    両端は `(緯度, 経度)`。線は画面の地図と同じ。

    1 回線＝判定が出ていれば入力の両端を判定の語で。作業面＝全件そろっていれば各件を語で
    （条件探索は全件が同じ経路＝1 本を全体の語で）。座標の無い誤りの行は描かない（画面と同じ）。
    """
    kind = headline.get("kind")
    if kind == "verdict":
        from core import simulation as sim
        try:
            p = sim.SimParams(inputs)
        except (ValueError, KeyError, IndexError):
            return None
        return [((p.lat_tx, p.lon_tx), (p.lat_rx, p.lon_rx), headline["word"], "")]
    if kind != "sheet" or headline.get("done") != headline.get("total"):
        return None
    items = [it for it in headline.get("items") or [] if it.get("tx") and it.get("rx")]
    if headline.get("workspace") == "scenario":
        items, word = items[:1], headline.get("overall", "")
        return [(tuple(it["tx"]), tuple(it["rx"]), word, "") for it in items] or None
    return [(tuple(it["tx"]), tuple(it["rx"]), it["word"], it["label"]) for it in items] or None


def map_png(lines: "list[Any]", basemap: str) -> "bytes | None":
    """線の並び → 地図の PNG（背景のタイルが取れなければ None＝帳票の「地図は取得できませんでした」）。"""
    import io

    from report import report_map
    specs = [report_map.PathSpec(tx=tx, rx=rx, status=word, label=label)
             for tx, rx, word, label in lines]
    img = report_map.render_paths_map(specs, basemap_source_id=basemap)
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
