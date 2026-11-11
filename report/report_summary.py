"""
report_summary.py
=================
バッチ（複数経路）の成果物（ヘッドレス・UI 知識ゼロ・副作用なし）。

`summary.csv`（数値）と `summary.kml`（Google Earth 用）の本文を返す（ファイルへ
書くのは呼ぶ側＝新しい画面の保存）。
`summary.html`（A4 台帳＋全経路俯瞰地図）と `report_all.html`（連結）は Pro へ
移した（Link 1.0 ステージ11b）。
"""

from __future__ import annotations

import csv
import html as _html
import io
import math
from datetime import datetime
from typing import TYPE_CHECKING

from core import output_contract
from core import units
from core import version
from report import report_common
from report.map_graphics import kml_color

if TYPE_CHECKING:
    from report.batch import PathResult, PathRow


def _csv_height(value: float) -> str:
    """空中線高の欄。有限でなければ空＝新しい画面の誤りの行（`webui/worker.py`）は高さを持たない。"""
    return f"{value:.1f}" if math.isfinite(value) else ""


def summary_csv_text(results: list[PathResult]) -> str:
    """`summary.csv` の本文（行末 `\\r\\n`）。ファイルへ書くのは呼ぶ側（新しい画面の保存・Pro の帳票）。"""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    # 見出しは出力契約が単一ソース（→ core/output_contract.py）＝ここに列を
    # 手書きすると「行に値を足したのに見出しを足し忘れる」がすり抜ける。
    writer.writerow(list(output_contract.SUMMARY_CSV_COLUMNS))
    for pr in results:
        freq_val    = f"{pr.params.freq_mhz:.1f}" if pr.params else ""
        gain_tx_val = units.csv_db(pr.params.gain_tx) if pr.params else ""
        gain_rx_val = units.csv_db(pr.params.gain_rx) if pr.params else ""
        h_tx_val = _csv_height(pr.row.h_tx)
        h_rx_val = _csv_height(pr.row.h_rx)
        if pr.result is not None:
            r = pr.result
            # status 列は `pr.status`＝成果物だけ失敗した経路も ERROR で出る
            # （I-010）。**数値は残す**（計算は通っているので消す理由が無い）
            # ＝何が欠けたかは末尾の error 列が持つ。
            writer.writerow([
                report_common.csv_cell(pr.row.path_id), pr.status,
                freq_val, gain_tx_val, gain_rx_val, h_tx_val, h_rx_val,
                # 桁は `units.csv_db` が単一ソース（0.1 dB）＝**書式も出力契約**
                # なので、変えた版の CHANGELOG と公開文書に書いてある。
                units.csv_db(r.p_rx),          units.csv_db(r.actual_margin),
                units.csv_db(r.fspl),          units.csv_db(r.diff_loss),
                units.csv_db(r.veg_loss),      units.csv_db(r.env_loss),
                units.csv_db(r.rain_loss),     units.csv_db(r.gas_loss),
                units.csv_db(r.total_loss),
                units.csv_distance(r.slant_dist_km),
                units.csv_blocked_ratio(r.blocked_ratio),
                report_common.csv_cell(pr.row.note),
                report_common.csv_cell(pr.artifact_error)
                if pr.artifact_error is not None else "",
                # 末尾＝出力契約の規約 1（追加は末尾のみ）。`f1_pct` の隣では
                # ないので、並べて読むときは列名で引くこと（I-077）。
                units.csv_f1_depth(r.blocked_ratio),
                # 何点で刻んだ答えか（I-069）＝**行ごとに違う**。
                str(pr.params.num) if pr.params else "",
                # 水平距離（B-139）＝`slant_m` は斜距離なので、実効間隔を
                # 割り出すには水平距離が要る。標本は等間隔とは限らない
                # （B-150）ので、割り算はあくまで読む側の近似計算。
                units.csv_distance(pr.terrain.horiz_dist_km)
                if pr.terrain is not None else "",
                # DEM 取得の失敗率（ISSUES.md B-025 ③）＝単一ソースは
                # `models.TerrainProfile.fail_pct`。
                units.csv_fail_pct(pr.terrain.fail_pct)
                if pr.terrain is not None else "",
                report_common.csv_calc_profile(pr.params),
                report_common.csv_input_hash(pr.params),
            ])
        else:
            writer.writerow([
                report_common.csv_cell(pr.row.path_id), "ERROR",
                freq_val, gain_tx_val, gain_rx_val, h_tx_val, h_rx_val,
                "", "", "", "", "", "", "", "", "", "", "",
                report_common.csv_cell(pr.row.note),
                report_common.csv_cell(pr.error),
                "",
                str(pr.params.num) if pr.params else "",
                units.csv_distance(pr.terrain.horiz_dist_km)
                if pr.terrain is not None else "",
                units.csv_fail_pct(pr.terrain.fail_pct)
                if pr.terrain is not None else "",
                report_common.csv_calc_profile(pr.params),
                report_common.csv_input_hash(pr.params),
            ])
    return buf.getvalue()


def _has_coords(row: PathRow) -> bool:
    """送受の座標がそろっているか（新しい画面の誤りの行は NaN＝`webui.worker._error_row`）。"""
    return all(math.isfinite(v) for v in (row.lat_tx, row.lon_tx, row.lat_rx, row.lon_rx))


def summary_kml_text(results: list[PathResult]) -> str:
    """全パスを OK / REVIEW / NG / Error のフォルダに分けた KML の本文（2 つの画面が共用）。

    線の色は 4 語とも画面と地図の画像と同じ（`map_graphics.kml_color`＝B-378）。座標の無い誤りの行は線を引かず、
    名前と理由だけの Placemark を Error に置く（件数を台帳と画面の表にそろえる）。
    """
    ok_xml = rev_xml = ng_xml = err_xml = ""

    for pr in results:
        pid_esc = _html.escape(pr.row.path_id)
        if not _has_coords(pr.row):
            err_xml += (
                f"    <Placemark><name>{pid_esc}</name>"
                f"<description>{_html.escape(str(pr.error))}</description>"
                f"<styleUrl>#err</styleUrl></Placemark>\n"
            )
            continue
        if pr.result is not None and pr.terrain is not None and pr.params is not None:
            tx_alt   = float(pr.terrain.raw_elevs[0])  + pr.params.h_tx
            rx_alt   = float(pr.terrain.raw_elevs[-1]) + pr.params.h_rx
            coords   = (
                f"{pr.row.lon_tx:.6f},{pr.row.lat_tx:.6f},{tx_alt:.1f} "
                f"{pr.row.lon_rx:.6f},{pr.row.lat_rx:.6f},{rx_alt:.1f}"
            )
            freq_s   = f"{pr.params.freq_mhz:.1f} MHz"
            desc_esc = _html.escape(
                f"Freq: {freq_s} | "
                f"RX: {units.format_db(pr.result.p_rx, unit='dBm')} | "
                f"Margin: "
                f"{units.format_db(pr.result.actual_margin, signed=True, unit='dB')}"
            )
            # 線は実測どおり引ける（地形がある）が、**振り分けは判定に従う**＝
            # 成果物が欠けた経路は Error フォルダへ入る（I-010・台帳と食い違わせない）。
            style = {"OK": "ok", "REVIEW": "rev", "NG": "ng"}.get(pr.status, "err")
            pm = (
                f"    <Placemark><name>{pid_esc}</name>"
                f"<description>{desc_esc}</description>"
                f"<styleUrl>#{style}</styleUrl>"
                f"<LineString><altitudeMode>absolute</altitudeMode>"
                f"<coordinates>{coords}</coordinates>"
                f"</LineString></Placemark>\n"
            )
            if style == "ok":
                ok_xml += pm
            elif style == "rev":
                rev_xml += pm
            elif style == "ng":
                ng_xml += pm
            else:
                err_xml += pm
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

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>Batch Summary</name>
  <description>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {version.APP_FULL}</description>

  <Style id="ok"><LineStyle><color>{kml_color("OK")}</color><width>3</width></LineStyle></Style>
  <Style id="rev"><LineStyle><color>{kml_color("REVIEW")}</color><width>3</width></LineStyle></Style>
  <Style id="ng"><LineStyle><color>{kml_color("NG")}</color><width>3</width></LineStyle></Style>
  <Style id="err"><LineStyle><color>{kml_color("ERROR")}</color><width>3</width></LineStyle></Style>

  <Folder><name>OK</name><open>1</open>
{ok_xml}  </Folder>
  <Folder><name>REVIEW</name><open>1</open>
{rev_xml}  </Folder>
  <Folder><name>NG</name><open>1</open>
{ng_xml}  </Folder>
  <Folder><name>Error</name><open>0</open>
{err_xml}  </Folder>

</Document>
</kml>"""
