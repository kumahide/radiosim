"""
report_summary.py
=================
バッチ（複数経路）のサマリ出力生成（ヘッドレス）。

summary.csv（数値）／summary.html（A4 台帳＋全経路俯瞰地図）／summary.kml、
および **report_all.html（サマリ＋全 per-path を 1 文書へ連結＝Ctrl+P 一発で
全ページぶんの PDF）** を書き出す。
UI 知識ゼロ・副作用はファイル I/O のみ（Web 再利用のための継ぎ目を維持）。

per-path のシート断片は report_path が作る（→ report_common の「断片と文書の分離」）。
"""

from __future__ import annotations

import csv
import html as _html
import os
from datetime import datetime
from typing import TYPE_CHECKING

import i18n
import report_common
import report_map
import report_path
import units
import version

if TYPE_CHECKING:
    from batch import PathResult


# ============================================================
# サマリ CSV
# ============================================================
def _save_summary_csv(results: list[PathResult], batch_dir: str) -> None:
    path = os.path.join(batch_dir, "summary.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "status", "freq_mhz", "gain_tx_dbi", "gain_rx_dbi",
            "h_tx", "h_rx",
            "rx_dbm", "margin_db",
            "fspl_db", "diff_db", "veg_db", "env_db",
            "rain_db", "gas_db", "total_loss_db",
            "slant_m", "f1_pct", "note", "error",
        ])
        for pr in results:
            freq_val    = f"{pr.params.freq_mhz:.1f}" if pr.params else ""
            gain_tx_val = f"{pr.params.gain_tx:.1f}"  if pr.params else ""
            gain_rx_val = f"{pr.params.gain_rx:.1f}"  if pr.params else ""
            h_tx_val = f"{pr.row.h_tx:.1f}"
            h_rx_val = f"{pr.row.h_rx:.1f}"
            if pr.result is not None:
                r = pr.result
                writer.writerow([
                    report_common.csv_cell(pr.row.path_id), r.status,
                    freq_val, gain_tx_val, gain_rx_val, h_tx_val, h_rx_val,
                    f"{r.p_rx:.2f}",          f"{r.actual_margin:.2f}",
                    f"{r.fspl:.2f}",           f"{r.diff_loss:.2f}",
                    f"{r.veg_loss:.2f}",       f"{r.env_loss:.2f}",
                    f"{r.rain_loss:.2f}",      f"{r.gas_loss:.2f}",
                    f"{r.total_loss:.2f}",
                    units.csv_distance(r.slant_dist_km),
                    units.csv_blocked_ratio(r.blocked_ratio),
                    report_common.csv_cell(pr.row.note), "",
                ])
            else:
                writer.writerow([
                    report_common.csv_cell(pr.row.path_id), "ERROR",
                    freq_val, gain_tx_val, gain_rx_val, h_tx_val, h_rx_val,
                    "", "", "", "", "", "", "", "", "", "", "",
                    report_common.csv_cell(pr.row.note),
                    report_common.csv_cell(pr.error),
                ])


# ============================================================
# サマリ HTML（A4 台帳）
# ============================================================
# 台帳ヘッダの並び（i18n キー・列順は tbody の <td> と一致させること）。
_SUMMARY_COL_KEYS = (
    "html_col_id", "html_col_status", "html_col_freq",
    "html_col_gain_tx", "html_col_gain_rx", "html_col_h_tx", "html_col_h_rx",
    "html_col_rx", "html_col_margin", "html_col_fspl", "html_col_diff",
    "html_col_veg", "html_col_env", "html_col_rain", "html_col_gas",
    "html_col_total_loss", "html_col_slant", "html_col_f1",
    "html_col_note", "html_col_graph",
)


def _summary_header_cells() -> str:
    """台帳の <th> 群を返す。単位（"… (dBm)"）は 2 行目へ落とす。

    "受信レベル (dBm)" のように "名前 (単位)" 形式のヘッダは、単位を `.u`（改行＋
    小さめ）で 2 行目に置く。これで各ヘッダの必要幅が max(名前, 単位) に縮み
    （名前と単位を横に並べない）、列が横に広がりにくく表が印字域に収まりやすい。
    単位の無いヘッダ（ID・判定・備考・グラフ等）はそのまま 1 行。
    """
    cells = []
    for key in _SUMMARY_COL_KEYS:
        label = i18n.t(key)
        if label.endswith(")") and " (" in label:
            name, unit = label.split(" (", 1)
            cells.append(f'<th>{name}<span class="u">({unit}</span></th>')
        else:
            cells.append(f"<th>{label}</th>")
    return "".join(cells)


def render_summary_map_b64(results: list[PathResult]) -> "str | None":
    """全パスを1枚に俯瞰する地図（summary 用）を生成し base64 で返す。失敗時 None。

    座標は PathRow（実行前に凍結済み）から取るため、計算に失敗した ERROR 行も
    地図には描ける。ステータスは summary 台帳の行色と同じ配色で塗り分ける。
    """
    specs = [
        report_map.PathSpec(
            tx=(pr.row.lat_tx, pr.row.lon_tx),
            rx=(pr.row.lat_rx, pr.row.lon_rx),
            status=(pr.result.status if pr.result is not None else "ERROR"),
            label=pr.row.path_id,
        )
        for pr in results
    ]
    return report_map.render_paths_map_b64(specs)


def summary_sheet_css() -> str:
    """summary シート固有のスタイル（すべて `.sheet.summary` へスコープ）。

    ⚠️ スコープを外さないこと：per-path シートも `.sheet` / `.page-header` /
    `.cards` を別値で持つため、素のセレクタで書くと連結文書（report_all.html）で
    後勝ちの上書きが起き、どちらかのレイアウトが壊れる。
    """
    return """
/* --- summary シート（台帳） --- */
.sheet.summary .cards{display:flex;gap:12px;margin-bottom:20px;break-inside:avoid}
/* カードの高さは per-path レポートと揃える＝高さを決める余白・ラベル/数値の
   フォントを per-path と同値にする（padding 6px・lbl 9px・val 15px）。
   件数表示なので数値は小さめでも十分読める。 */
.sheet.summary .card{background:white;border:1px solid #eee;border-radius:8px;padding:6px 20px;box-shadow:0 1px 3px rgba(0,0,0,.12);text-align:center;min-width:80px}
.sheet.summary .card .lbl{font-size:9px;color:#999;text-transform:uppercase}
.sheet.summary .card .val{font-size:15px;font-weight:bold;color:#333}
.sheet.summary .card.ok .val{color:#2e7d32}.sheet.summary .card.ng .val{color:#c62828}.sheet.summary .card.err .val{color:#e65100}
/* 台帳は 20 列あり A4 印字域（182mm）に収める必要がある。**table-layout:auto**＝
   各列を内容の実幅（nowrap）に合わせて配分する。以前の table-layout:fixed（等幅）は
   狭い列にヘッダ日本語が押し込まれて語中で折れ（"受信レ/ベル"）、長い数値
   （"-1672.4"）がセル幅を超えて罫線からはみ出した。auto＋小さめフォント（ヘッダ 8px／
   データ 9px）なら、極端値（受信レベル -1871.0 等）を含む行でも 1 行に収まり 1 枚に
   納まることを実測（Edge --print-to-pdf）。値が空の列は詰まり、桁の大きい列へ幅が回る。
   なお元のはみ出しバグの主因は「フォントが大きい＋等幅」で、auto 化＋縮小で解消する。
   per-path の縮小フィット（transform）は使えない＝改ページに効かず表が切れるため。 */
.sheet.summary table.summary{border-collapse:collapse;width:100%;table-layout:auto;background:white;box-shadow:0 1px 3px rgba(0,0,0,.12)}
/* ヘッダは中央・下揃え・**折り返し禁止**（列幅は内容に追従するので語中で折れない）。 */
.sheet.summary table.summary th{background:#455a64;color:white;padding:4px 4px;text-align:center;
  vertical-align:bottom;font-size:8px;white-space:nowrap;line-height:1.2;
  border-right:1px solid rgba(255,255,255,.22)}
/* 単位は 2 行目・小さめ・やや淡色（ヘッダ幅を名前だけで決めさせる）。 */
.sheet.summary table.summary th .u{display:block;font-size:7px;font-weight:normal;opacity:.8}
/* 数値セルは右寄せ＋折り返し禁止で列内に整列させ、隣接列とは縦罫線で仕切る
   （桁の大きい値でも "受信レベル｜マージン｜FSPL" が地続きに見えないように）。
   ID・判定は左/中央、備考のみ自由文なので折り返し可。 */
.sheet.summary table.summary td{padding:4px 4px;border-bottom:1px solid #eee;border-right:1px solid #e6e6e6;
  font-size:9px;text-align:right;white-space:nowrap}
.sheet.summary table.summary th:last-child,.sheet.summary table.summary td:last-child{border-right:none}
.sheet.summary table.summary td.c-id{text-align:left}
.sheet.summary table.summary td.c-status{text-align:center}
/* 備考は自由文。overflow-wrap:break-word で**空白で折り返す**（"ridge crossing" が
   "ridg/e cros/sing" と語中で割れないように）。長い連続語だけ必要時に分割する。 */
.sheet.summary table.summary td.c-note{text-align:left;white-space:normal;
  word-break:normal;overflow-wrap:break-word}
.sheet.summary table.summary tr{break-inside:avoid}
/* 備考（自由文）とグラフだけ幅を抑える（auto だと長い備考が幅を奪いすぎるため）。
   数値・ID 列は幅指定せず内容に追従させる。 */
.sheet.summary table.summary col.c-note{width:90px}.sheet.summary table.summary col.c-graph{width:46px}
.sheet.summary table.summary td img{max-width:100%;height:auto}
/* フッタを用紙の最下部へ。.sheet を縦フレックスにして .page-footer を margin-top:auto で
   押し下げる（画面は .sheet が 297mm 高なので下端へ／印刷は下記 min-height で1枚目を
   用紙高に合わせる）。summary 専用＝per-path はフッタが縮小フィット .fit の内側にあり
   別扱い（ページ最下部固定は縮小スケールと競合するため触らない）。 */
.sheet.summary{display:flex;flex-direction:column}
.sheet.summary .page-footer{margin-top:auto}
@media print{.sheet.summary{min-height:calc(297mm - 14mm - 8mm)}}
.sheet.summary tr.ok{background:#f1f8e9}.sheet.summary tr.ng{background:#fff8e1}.sheet.summary tr.err{background:#fce4ec}
.sheet.summary .s-ok{color:#2e7d32;font-weight:bold}.sheet.summary .s-ng{color:#c62828;font-weight:bold}.sheet.summary .s-err{color:#bf360c;font-weight:bold}
.sheet.summary .report-memo{background:#f7f9fa;border:1px solid #e0e6e9;border-radius:6px;padding:8px 12px;margin-bottom:16px;font-size:12px;color:#37474f;break-inside:avoid}
.sheet.summary .report-memo .rm-label{color:#90a4ae;font-weight:bold;margin-right:4px}
.sheet.summary .paths-map{display:block;width:100%;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,.12);margin-bottom:16px;break-inside:avoid}
.sheet.summary .map-note{color:#999;font-size:12px;font-style:italic;background:white;border-radius:8px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,.12);margin-bottom:16px}
/* 連結レポートへの導線（画面のみ・印刷では消える＝.no-print） */
.sheet.summary .all-link{margin:0 0 10px;font-size:11px}
.sheet.summary .all-link a{color:#00695c}
"""


def summary_sheet_html(results: list[PathResult], project_name: str = "",
                       memo: str = "", map_b64: "str | None" = None,
                       anchor_links: bool = False) -> str:
    """summary（台帳）の A4 シート断片（`<section class="sheet summary">`）を返す。

    anchor_links=False（既定・単体の summary.html）＝台帳のグラフ列は
    `p01/report.html` へリンクし、画面のみ表示の「全ページ連結（report_all.html）」
    導線を出す。True（連結文書）＝同じ文書内の per-path シート `#p01` へ飛ばし、
    導線は出さない（自分自身への案内になるため）。

    project_name はヘッダの案件名、memo はサーベイ全体の自由メモ（どちらも
    ユーザー入力の自由文字列・空で従来表示）。memo は非空時のみヘッダ直下に
    小ブロックとして表示する（サーベイ全体の注記＝summary のみ）。
    map_b64 は全パス俯瞰地図（render_summary_map_b64 の戻り）。None のときは
    地図を省き注記を表示する（per-path と同じベストエフォート）。
    """
    ok_count  = sum(1 for pr in results if pr.result is not None and pr.result.status == "OK")
    ng_count  = sum(1 for pr in results if pr.result is not None and pr.result.status != "OK")
    err_count = sum(1 for pr in results if pr.result is None)
    total     = len(results)

    rows_html = ""
    for pr in results:
        freq_disp    = f"{pr.params.freq_mhz:.1f}" if pr.params else "—"
        gain_tx_disp = f"{pr.params.gain_tx:.1f}"  if pr.params else "—"
        gain_rx_disp = f"{pr.params.gain_rx:.1f}"  if pr.params else "—"
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
                f"<td>{freq_disp}</td><td>{gain_tx_disp}</td><td>{gain_rx_disp}</td>"
                f"<td>{h_tx_disp}</td><td>{h_rx_disp}</td>"
                f"<td colspan='11'>{error_esc}</td>"
                f"<td class='c-note'>{note_esc}</td>"
                f"<td></td></tr>\n"
            )
            continue
        r   = pr.result
        cls = "ok" if r.status == "OK" else "ng"
        # 連結文書では文書内アンカー（#p01）へ、単体では p01/report.html へ飛ばす。
        href = f"#{pid_safe}" if anchor_links else f"{pid_safe}/report.html"
        rows_html += (
            f"<tr class='{cls}'>"
            f"<td>{pid_esc}</td>"
            f"<td class='s-{cls}'>{r.status}</td>"
            f"<td>{freq_disp}</td>"
            f"<td>{gain_tx_disp}</td>"
            f"<td>{gain_rx_disp}</td>"
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
            f"<td>{units.format_distance(r.slant_dist_km, unit=False)}</td>"
            f"<td>{units.format_blocked_ratio(r.blocked_ratio, unit=False)}</td>"
            f"<td class='c-note'>{note_esc}</td>"
            f"<td><a href='{href}'>"
            f"<img src='{pid_safe}/profile.png' style='max-height:40px;border:1px solid #ddd;border-radius:3px;vertical-align:middle;'>"
            f"</a></td></tr>\n"
        )

    # 案件メモ（サーベイ全体の自由注記）。非空時のみヘッダ直下（p1）に小ブロック表示。
    if memo:
        memo_block = (
            f'<div class="report-memo">'
            f'<span class="rm-label">{i18n.t("html_report_memo")}</span> '
            f'{_html.escape(memo)}</div>'
        )
    else:
        memo_block = ""

    # 全ページ連結レポートへの導線（単体 summary.html のみ・印刷では消える）。
    if anchor_links:
        all_link = ""
    else:
        all_link = (
            f'<p class="all-link no-print">'
            f'<a href="report_all.html">{_html.escape(i18n.t("html_all_link"))}</a></p>'
        )

    # 全パス俯瞰地図（p1 のみ）。取得失敗時は地図を省いて注記を出す。
    if map_b64:
        map_block = (
            f'<img class="paths-map" src="data:image/png;base64,{map_b64}" '
            f'alt="{_html.escape(i18n.t("html_map_title"))}">'
        )
    else:
        map_block = (
            f'<p class="map-note">{_html.escape(i18n.t("html_map_unavailable"))}</p>'
        )

    return f"""<section class="sheet summary">
{report_common.page_header(i18n.t('html_batch_title'), project_name=project_name)}
{memo_block}
{all_link}
<div class="cards">
  <div class="card"><div class="lbl">{i18n.t('html_total')}</div><div class="val">{total}</div></div>
  <div class="card ok"><div class="lbl">{i18n.t('html_ok')}</div><div class="val">{ok_count}</div></div>
  <div class="card ng"><div class="lbl">{i18n.t('html_ng')}</div><div class="val">{ng_count}</div></div>
  <div class="card err"><div class="lbl">{i18n.t('html_error')}</div><div class="val">{err_count}</div></div>
</div>
{map_block}
<table class="summary">
<colgroup>
  <col class="c-id"><col class="c-status"><col class="c-freq">
  <col><col><col><col><col><col><col><col><col><col><col><col><col><col><col>
  <col class="c-note"><col class="c-graph">
</colgroup>
<thead>
<tr>{_summary_header_cells()}</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
{report_common.page_footer(i18n.t("html_batch_mode"))}
</section>"""


def save_summary_html(results: list[PathResult], batch_dir: str,
                      project_name: str = "", memo: str = "",
                      map_b64: "str | None" = None) -> None:
    """バッチの summary.html（台帳 1 枚）を生成する。引数は summary_sheet_html 参照。"""
    html = report_common.html_document(
        _html.escape(i18n.t("html_batch_title")),
        summary_sheet_css(),
        summary_sheet_html(results, project_name, memo, map_b64),
    )
    with open(os.path.join(batch_dir, "summary.html"), "w", encoding="utf-8") as f:
        f.write(html)


def save_report_all_html(results: list[PathResult], batch_dir: str,
                         project_name: str = "", memo: str = "",
                         map_b64: "str | None" = None) -> None:
    """report_all.html（サマリ＋全 per-path を 1 文書へ連結）を生成する。

    狙い＝**Ctrl+P 一発で全ページぶんの PDF**（従来は台帳から 1 経路ずつ開いて
    印刷する手作業が残っていた）。PDF エンジンは入れない方針は不変で、連結した
    HTML をブラウザに印刷させるだけ。単体共有用の summary.html / p01/report.html
    は従来どおり残す（**追加のみ・最小侵襲**）。

    per-path のシート断片は実行時に保持した `PathResult.sheet_html` を使う
    （断面図・地図は base64 埋め込み済み＝ディスクを読み直さない）。失敗した
    パスは断片を持たないので連結からは落ち、台帳のエラー行だけが残る。
    台帳のサムネイル（`p01/profile.png`）だけは相対参照なので、この文書は
    batch_dir 直下に置くこと。
    """
    doc_title = i18n.t("html_all_title")
    if project_name:
        doc_title = f"{project_name} - {doc_title}"

    sheets = [summary_sheet_html(results, project_name, memo, map_b64,
                                 anchor_links=True)]
    sheets += [pr.sheet_html for pr in results if pr.sheet_html]

    html = report_common.html_document(
        _html.escape(doc_title),
        summary_sheet_css() + report_path.path_sheet_css(),
        "\n".join(sheets) + "\n" + report_common.fit_to_page_script(),
    )
    with open(os.path.join(batch_dir, "report_all.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ============================================================
# サマリ KML
# ============================================================
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
