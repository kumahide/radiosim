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

from core import i18n
from core import models
from core import output_contract
from core import units
from core import version
from report import report_common
from report import report_map
from report import report_path

if TYPE_CHECKING:
    from report.batch import PathResult


# ============================================================
# サマリ CSV
# ============================================================
def _save_summary_csv(results: list[PathResult], batch_dir: str) -> None:
    path = os.path.join(batch_dir, "summary.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # 見出しは出力契約が単一ソース（→ core/output_contract.py）＝ここに列を
        # 手書きすると「行に値を足したのに見出しを足し忘れる」がすり抜ける。
        writer.writerow(list(output_contract.SUMMARY_CSV_COLUMNS))
        for pr in results:
            freq_val    = f"{pr.params.freq_mhz:.1f}" if pr.params else ""
            gain_tx_val = units.csv_db(pr.params.gain_tx) if pr.params else ""
            gain_rx_val = units.csv_db(pr.params.gain_rx) if pr.params else ""
            h_tx_val = f"{pr.row.h_tx:.1f}"
            h_rx_val = f"{pr.row.h_rx:.1f}"
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
                ])


# ============================================================
# サマリ HTML（A4 台帳）
# ============================================================
# 台帳ヘッダの並び（i18n キー・列順は tbody の <td> と一致させること）。
# 🆕 **22 列→9 列（I-143・2026-09-10 ユーザー決定）**＝一目で go/no-go を見る面
# として機能させるため、内訳（送受利得・回折/植生/環境/降雨/大気・F1・DEM 失敗率）
# と備考を落とし、回線計算の流れの 9 項目だけにそろえた。内訳は `summary.csv` /
# `hops.csv` と個別レポートに残る（この表は消える情報を作らない＝別の面へ移すだけ）。
_SUMMARY_COL_KEYS = (
    "html_col_id", "html_col_status", "html_col_freq", "mh_heights",
    "html_col_fspl", "html_col_total_loss", "html_col_rx", "html_col_margin",
    "html_col_graph",
)


# CSS で名指しする列（幅＝`col.c-id`／字揃え＝`td.c-graph` 等）は**列キーで引く**。
# `<colgroup>` を手書きで並べていた頃は、列を足すたびに末尾の 2 本が 1 列ずつ
# 手前へずれ（B-185＝f1_depth と dem_fail の 2 回ぶん）、幅の指定が当たる列がずれて
# いた（＝台帳の崩れ）。列数を引き算で書く ERROR 行の colspan と同じ理由＝
# **位置は書かない**。
_SUMMARY_COL_CLASSES = {
    "html_col_id":     "c-id",
    "html_col_status": "c-status",
    "html_col_freq":   "c-freq",
    "html_col_graph":  "c-graph",
}


def _summary_colgroup() -> str:
    """台帳の `<colgroup>`（列キーと 1:1）を返す。"""
    cols = "".join(
        f'<col class="{_SUMMARY_COL_CLASSES[key]}">'
        if key in _SUMMARY_COL_CLASSES else "<col>"
        for key in _SUMMARY_COL_KEYS
    )
    return f"<colgroup>{cols}</colgroup>"


def _summary_header_cells() -> str:
    """台帳の <th> 群を返す。単位（"… (dBm)"）は 2 行目へ落とす。

    "受信レベル (dBm)" のように "名前 (単位)" 形式のヘッダは、単位を `.u`（改行＋
    小さめ）で 2 行目に置く。これで各ヘッダの必要幅が max(名前, 単位) に縮み、
    列が横に広がりにくく表が印字域に収まりやすい。単位の無いヘッダはそのまま
    1 行。`mh_heights`（アンテナ高）は中継台帳（`report_multihop`）と同じ扱い＝
    ⑧ 同じ意味は同じ見せ方（i18n 文言に単位が無いのでここで "(m)" を補う）。
    """
    cells = []
    for key in _SUMMARY_COL_KEYS:
        label = i18n.t(key)
        if label.endswith(")") and " (" in label:
            name, unit = label.split(" (", 1)
            cells.append(f'<th>{name}<span class="u">({unit}</span></th>')
        elif key == "mh_heights":
            cells.append(f'<th>{label}<span class="u">(m)</span></th>')
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
            status=pr.status,
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
/* ⚠️ **中継の台帳と同じ作りにする**（B-155）＝縮めずに折り返す。ここは 4 枚とも
   短い語なので今は 1 行に収まるが、**カードは同じ意味関係を同じ見せ方で出す面**
   （⑧）なので、片方だけ「縮んで語中で折れる」作りを残さない。 */
.sheet.summary .cards{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px;
  break-inside:avoid}
/* カードの高さは per-path レポートと揃える＝高さを決める余白・ラベル/数値の
   フォントを per-path と同値にする（padding 6px・lbl 9px・val 15px）。
   件数表示なので数値は小さめでも十分読める。 */
.sheet.summary .card{background:white;border:1px solid #eee;border-radius:8px;padding:6px 20px;box-shadow:0 1px 3px rgba(0,0,0,.12);text-align:center;min-width:80px;flex:0 0 auto}
.sheet.summary .card .lbl{font-size:9px;color:#999;text-transform:uppercase;white-space:nowrap}
.sheet.summary .card .val{font-size:15px;font-weight:bold;color:#333;white-space:nowrap}
.sheet.summary .card.ok .val{color:#2e7d32}.sheet.summary .card.ng .val{color:#c62828}.sheet.summary .card.err .val{color:#e65100}
/* 台帳は 9 列（I-143・2026-09-10）＝**table-layout:auto** で各列を内容の実幅
   （nowrap）に合わせて配分する。以前の table-layout:fixed（等幅）は狭い列に
   ヘッダ日本語が押し込まれて語中で折れ（"受信レ/ベル"）、長い数値（"-1672.4"）が
   セル幅を超えて罫線からはみ出した。auto＋小さめフォント（ヘッダ 8px／データ 9px）
   なら、極端値（受信レベル -1871.0 等）を含む行でも 1 行に収まり 1 枚に納まることを
   実測（Edge --print-to-pdf）。値が空の列は詰まり、桁の大きい列へ幅が回る。
   per-path の縮小フィット（transform）は使えない＝改ページに効かず表が切れるため。 */
.sheet.summary table.summary{border-collapse:collapse;width:100%;table-layout:auto;background:white;box-shadow:0 1px 3px rgba(0,0,0,.12)}
/* ヘッダは中央・下揃え・**折り返し禁止**（列幅は内容に追従するので語中で折れない）。 */
/* ⚠️ **左右の余白は 2px**（B-187）＝列数が多いほど 1 列あたりの削りが効く。
   ここが 4px だった頃は 22 列で最小幅の合計が印字域をはみ出し、`<col>` の
   `width` が丸ごと捨てられて表が崩れた。9 列に減ってからも据え置く。 */
.sheet.summary table.summary th{background:#455a64;color:white;padding:4px 2px;text-align:center;
  vertical-align:bottom;font-size:8px;white-space:nowrap;line-height:1.2;
  border-right:1px solid rgba(255,255,255,.22)}
/* 単位は 2 行目・小さめ・やや淡色（ヘッダ幅を名前だけで決めさせる）。 */
.sheet.summary table.summary th .u{display:block;font-size:7px;font-weight:normal;opacity:.8}
/* 数値セルは右寄せ＋折り返し禁止で列内に整列させ、隣接列とは縦罫線で仕切る
   （桁の大きい値でも "受信レベル｜マージン｜FSPL" が地続きに見えないように）。
   ID・判定は左/中央、ERROR 行の理由のみ自由文なので折り返し可。 */
.sheet.summary table.summary td{padding:4px 2px;border-bottom:1px solid #eee;border-right:1px solid #e6e6e6;
  font-size:9px;text-align:right;white-space:nowrap}
.sheet.summary table.summary th:last-child,.sheet.summary table.summary td:last-child{border-right:none}
/* ID は利用者が CSV に書いた自由文（長さの上限が無い）＝**折り返す**（B-187）。
   nowrap のままだと "hatsukaichi_kita_relay" のような ID 1 つで表が印字域を
   28px はみ出した（実測）。区切りの無い長い識別子が来るので anywhere で割る。
   ⚠️ `min-width` を必ず添える＝anywhere だけだと最小幅が **1 文字**になり、余った
   幅が別の列へ回って ID が縦に 1 文字ずつ割れた（13px まで潰れた・実測）。 */
.sheet.summary table.summary td.c-id{text-align:left;white-space:normal;
  word-break:normal;overflow-wrap:anywhere;min-width:46px}
.sheet.summary table.summary td.c-status{text-align:center}
/* ERROR 行の理由（自由文・colspan）は折り返す（B-145）。nowrap のままだと
   **折り返せない 1 行が table-layout:auto の表全体を押し広げ**、グラフ列が
   A4 の印字域（182mm）の外へ出る。理由には空白の無い長い連続語（Windows の
   パス・例外クラス名）が入るので anywhere で割る。備考（I-143 決定 3）も同じ
   セルへ一緒に載るので同じ割り方でよい。 */
.sheet.summary table.summary td.c-reason{text-align:left;white-space:normal;
  word-break:normal;overflow-wrap:anywhere}
/* 改ページを避ける単位は **1 行**（`tr`）＝備考が別行に分かれなくなった
   （I-143）ので、B-190 の per-tbody 括りはもう要らない。 */
.sheet.summary table.summary tr{break-inside:avoid}
/* グラフ列のサムネイル（I-143＝リンク文字から戻す）＝中継台帳と同じ寸法
   （`report_multihop.route_sheet_css` の `img.thumb` と揃える・⑧）。 */
.sheet.summary table.summary td.c-graph{text-align:center}
.sheet.summary table.summary img.thumb{max-height:40px;max-width:100%;height:auto;
  border:1px solid #ddd;border-radius:3px;vertical-align:middle}
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
/* 成果物が欠けた経路のグラフ列（I-010）＝リンク切れの画像を出さず字で言う。 */
.sheet.summary td.c-missing{color:#e65100;font-size:8px;text-align:center}
.sheet.summary .map-note{color:#999;font-size:12px;font-style:italic;background:white;border-radius:8px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,.12);margin-bottom:16px}
/* DEM 取得の失敗率（I-143 決定 2）＝台帳の下に 1 行だけ（列は持たない）。 */
.sheet.summary .dem-fail-note{color:#777;font-size:9px;margin:4px 0 0}
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
    # 判定の出所は `PathResult.status` 1 か所（I-010 ③）＝ここで条件を書き直すと、
    # 成果物だけ失敗した経路が台帳で「OK」に数え直される。
    ok_count  = sum(1 for pr in results if pr.status == "OK")
    ng_count  = sum(1 for pr in results if pr.status == "NG")
    err_count = sum(1 for pr in results if pr.status == "ERROR")
    total     = len(results)

    rows_html = ""
    # DEM 取得に失敗した標本を含む経路（I-143 決定 2）＝列でなく判定セルの ⚠ と
    # 台帳下の 1 行で出す。ここへ (表示名, fail_pct) を集めて末尾で注記に変換する。
    dem_fail_entries: list[tuple[str, float]] = []
    for pr in results:
        freq_disp = f"{pr.params.freq_mhz:.1f}" if pr.params else "—"
        h_tx_disp = f"{pr.row.h_tx:.1f}"
        h_rx_disp = f"{pr.row.h_rx:.1f}"
        pid_safe  = pr.row.path_id          # validated: [A-Za-z0-9_-]+ — safe for href
        pid_esc   = _html.escape(pr.row.path_id)
        note_esc  = _html.escape(pr.row.note)
        dem_mark = ""
        if pr.terrain is not None and pr.terrain.fail_pct > 0:
            dem_mark = " ⚠"
            dem_fail_entries.append((pr.row.path_id, pr.terrain.fail_pct))
        if pr.result is None:
            error_esc = _html.escape(str(pr.error))
            # I-143 決定 3＝計算に失敗した経路は個別レポートが作られないので、
            # 備考をここに一緒に載せないと HTML のどこにも出なくなる。
            if pr.row.note:
                error_esc = f"{error_esc}　｜　{i18n.t('html_col_note')}: {note_esc}"
            rows_html += (
                f"<tr class='err'>"
                f"<td class='c-id'>{pid_esc}</td>"
                f"<td class='c-status s-err'>ERROR</td>"
                f"<td>{freq_disp}</td>"
                f"<td>{h_tx_disp} / {h_rx_disp}</td>"
                # 幅は**列数から引く**（先頭 4 列＋グラフの 5 列以外）。直に数を書くと、
                # 列を足した日に理由欄だけが 1 列ずれる（I-077 で実際に踏んだ）。
                f"<td class='c-reason' colspan='{len(_SUMMARY_COL_KEYS) - 5}'>{error_esc}</td>"
                f"<td class='c-graph'></td></tr>\n"
            )
            continue
        r   = pr.result
        # 判定は `pr.status`＝**成果物が欠けた経路はここで ERROR になる**（I-010）。
        # 数値は計算できているのでセルには残す（値まで消すと、何が起きたのか
        # 分からなくなる）。欠けているのはグラフ列のサムネイルなので、そこへ
        # 「成果物なし」を出す＝**リンク切れの画像で気づかせない**。
        cls = {"OK": "ok", "NG": "ng"}.get(pr.status, "err")
        # 連結文書では文書内アンカー（#p01）へ、単体では p01/report.html へ飛ばす。
        href = f"#{pid_safe}" if anchor_links else f"{pid_safe}/report.html"
        if pr.artifact_error is None:
            # サムネイルを戻す（I-143・2026-09-10 ユーザー決定）＝22 列→9 列で
            # 幅の予算ができた（中継台帳は 19 列でもサムネイルが出ている＝B-187）。
            graph_cell = (
                f"<td class='c-graph'><a href='{href}'>"
                f"<img src='{pid_safe}/profile.png' class='thumb'></a></td>"
            )
        else:
            # I-143 決定 3＝成果物の保存に失敗した経路も個別レポートが無いので、
            # 備考をここへ一緒に出す。
            missing_txt = _html.escape(i18n.t("html_artifact_missing"))
            if pr.row.note:
                missing_txt = f"{missing_txt}：{note_esc}"
            graph_cell = f"<td class='c-missing'>{missing_txt}</td>"
        rows_html += (
            f"<tr class='{cls}'>"
            # ⚠️ **`c-id` / `c-status` はここで付ける**（B-187）＝CSS には最初から
            # `td.c-id` / `td.c-status` があったのに `<td>` 側にクラスが無く、
            # ID の左寄せも判定の中央寄せも**一度も効いていなかった**（`<col>` の
            # クラスは列に当たっても `<td>` には降りてこない）。
            f"<td class='c-id'>{pid_esc}</td>"
            f"<td class='c-status s-{cls}'>{pr.status}{dem_mark}</td>"
            f"<td>{freq_disp}</td>"
            f"<td>{h_tx_disp} / {h_rx_disp}</td>"
            f"<td>{units.format_db(r.fspl)}</td>"
            f"<td>{units.format_db(r.total_loss)}</td>"
            f"<td>{units.format_db(r.p_rx)}</td>"
            f"<td>{units.format_db(r.actual_margin, signed=True)}</td>"
            f"{graph_cell}</tr>\n"
        )

    # 「結果の取扱に関する補足」（3.0a1）。⚠️ **台帳は 1 枚で N 本を載せる**ので
    # 刻印は**和集合**＝どれか 1 本にでも当てはまる注記を出す（消すと*その行には
    # 書いていない*ことになる）。計算できなかった行は条件が確定していないので数えない。
    handling = report_common.handling_notes_html(models.scope_notes_union(
        models.scope_notes(
            pr.params.freq_mhz,
            diff_method=pr.result.diff_method,
            rain_rate=pr.params.rain_rate,
            veg_h=pr.params.veg_h,
            resolution=pr.params.resolution,
        )
        for pr in results if pr.result is not None and pr.params is not None
    ))

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
{_summary_colgroup()}
<thead>
<tr>{_summary_header_cells()}</tr>
</thead>
<tbody>
{rows_html}</tbody>
</table>
{report_common.dem_fail_notice_html(dem_fail_entries)}
{handling}
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
                f"Freq: {freq_s} | "
                f"RX: {units.format_db(pr.result.p_rx, unit='dBm')} | "
                f"Margin: "
                f"{units.format_db(pr.result.actual_margin, signed=True, unit='dB')}"
            )
            # 線は実測どおり引ける（地形がある）が、**振り分けは判定に従う**＝
            # 成果物が欠けた経路は Error フォルダへ入る（I-010・台帳と食い違わせない）。
            style = {"OK": "ok", "NG": "ng"}.get(pr.status, "err")
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
