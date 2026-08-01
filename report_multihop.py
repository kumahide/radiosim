"""
report_multihop.py
==================
中継経路（A-3）の出力＝**合成シート**（A4 1 枚）と、ホップ別シートの連結。

なぜ新しいシートが要るか
------------------------
バッチの台帳は「**1 行 = 1 回線**」で、N 本の独立した回線を並べる器。中継経路は
「**1 行 = 1 ホップ・N 行で 1 回線**」＝*内訳と全体*という別の意味を持つので、
同じ器に載せると「どれが 1 本の回線なのか」が読み取れない。

**⚠️ 全体判定は min（最も余裕の少ないホップ）だが、min だけを出さない。**
ホップ別の内訳を必ず併記する（設計哲学②＝判断に必要な材料を出す）。「どこが
一番苦しいか」が分からないと、中継点をどこに足すか・どの区間の空中線を上げるか
という**次の一手が決められない**＝スクリーニングとして役に立たない。

per-hop のシート（`report_path`）はバッチと同じものをそのまま使う＝ホップ 1 本は
バッチの 1 経路と同じ形なので、器を作り直さない。
"""

from __future__ import annotations

import html as _html
import os

import i18n
import report_common
import report_summary
import units
from multihop import MultiHopRun


def route_sheet_css() -> str:
    """合成シート固有のスタイル（すべて `.sheet.multihop` へスコープ）。

    ⚠️ スコープを外さないこと＝per-path / summary のシートも `.sheet` や
    `.cards` を別値で持つので、素のセレクタで書くと連結文書で後勝ちの上書きが
    起き、どちらかのレイアウトが壊れる（`report_summary` と同じ約束）。
    """
    return """
/* --- multihop シート（中継経路の内訳＋全体判定） --- */
.sheet.multihop .cards{display:flex;gap:12px;margin-bottom:16px;break-inside:avoid}
.sheet.multihop .card{background:white;border:1px solid #eee;border-radius:8px;
  padding:6px 20px;box-shadow:0 1px 3px rgba(0,0,0,.12);text-align:center;min-width:90px}
.sheet.multihop .card .lbl{font-size:9px;color:#999;text-transform:uppercase}
.sheet.multihop .card .val{font-size:15px;font-weight:bold;color:#333}
.sheet.multihop .card.ok .val{color:#2e7d32}
.sheet.multihop .card.ng .val{color:#c62828}
.sheet.multihop table.hops{border-collapse:collapse;width:100%;table-layout:auto;
  background:white;box-shadow:0 1px 3px rgba(0,0,0,.12)}
.sheet.multihop table.hops th{background:#455a64;color:white;padding:4px;
  text-align:center;vertical-align:bottom;font-size:8px;white-space:nowrap;
  border-right:1px solid rgba(255,255,255,.22)}
.sheet.multihop table.hops th .u{display:block;font-size:7px;font-weight:normal;opacity:.8}
.sheet.multihop table.hops td{padding:3px 4px;font-size:9px;border-bottom:1px solid #eee;
  text-align:right;white-space:nowrap}
.sheet.multihop table.hops td.c-name{text-align:left}
.sheet.multihop tr.ok td.c-status{color:#2e7d32;font-weight:bold}
.sheet.multihop tr.ng td.c-status{color:#c62828;font-weight:bold}
.sheet.multihop tr.err td{color:#e65100}
/* 全体判定を決めているホップ＝**一番苦しい区間**を目で拾えるようにする
   （次の一手はここに打つので、表の中で最初に見つかるべき行）。 */
.sheet.multihop tr.worst td{background:#fff8e1}
.sheet.multihop .route-line{font-size:10px;color:#555;margin:0 0 10px}
.sheet.multihop .map img{width:100%;border:1px solid #ddd;border-radius:4px}
.sheet.multihop .map{margin-bottom:10px}
.sheet.multihop .note{font-size:9px;color:#777;margin-top:8px}
/* 台帳の備考・グラフ列とヘッダの単位行＝**バッチ台帳と同じ見せ方**（⑧）。 */
.sheet.multihop table.hops td.c-note{text-align:left;white-space:normal;
  word-break:break-word;font-size:8px;color:#555}
.sheet.multihop table.hops img.thumb{max-height:40px;border:1px solid #ddd;
  border-radius:3px;vertical-align:middle}
.sheet.multihop .all-link{margin:0 0 10px;font-size:11px}
.sheet.multihop .all-link a{color:#00695c}
"""


# ホップ台帳の列＝**バッチ台帳（`report_summary._SUMMARY_COL_KEYS`）と同じ並び**に
# 揃える（2026-08-01 ユーザー決定「バッチの仕様と合わせたい」）。違うのは先頭 2 列
# （バッチ＝ID / 判定、こちらは # / 区間 → 判定）と、per-hop では意味を持たない
# 送受利得を落とし、代わりに「送/受アンテナ高」を 1 列にまとめるところだけ。
# ⚠️ 列を足すときは**両方の台帳を見て決める**（片方だけ増やすと、同じ「1 本の
# 回線の内訳」を見る 2 つの面がまた食い違う）。
_HOP_COL_KEYS = (
    "mh_col_no", "mh_section", "html_col_status", "html_col_freq",
    "mh_heights", "html_col_rx", "html_col_margin", "html_col_fspl",
    "html_col_diff", "html_col_veg", "html_col_env", "html_col_rain",
    "html_col_gas", "html_col_total_loss", "html_col_slant", "html_col_f1",
    "html_col_note", "html_col_graph",
)


def _hop_header_cells() -> str:
    """ホップ台帳の `<th>` 群（単位は 2 行目へ落とす＝バッチ台帳と同じ規則）。"""
    cells = []
    for key in _HOP_COL_KEYS:
        label = i18n.t(key)
        if label.endswith(")") and " (" in label:
            name, unit = label.split(" (", 1)
            cells.append(f'<th>{name}<span class="u">({unit}</span></th>')
        elif key == "mh_heights":
            cells.append(f'<th>{label}<span class="u">(m)</span></th>')
        else:
            cells.append(f"<th>{label}</th>")
    return "".join(cells)


def route_sheet_html(run: MultiHopRun, project_name: str = "", memo: str = "",
                     map_b64: "str | None" = None,
                     anchor_links: bool = False) -> str:
    """合成シートの A4 断片（`<section class="sheet multihop">`）を返す。

    `anchor_links=True` なら各ホップのリンクを同一文書内のシート（`#route1_h1`）
    へ、False なら `route1_h1/report.html` へ飛ばす（`report_summary` と同じ流儀）。
    """
    worst = run.worst
    overall = run.overall_margin
    status_cls = "ok" if run.ok else "ng"
    overall_txt = f"{overall:+.1f} dB" if overall is not None else "—"
    ok_count  = sum(1 for pr in run.hops
                    if pr.result is not None and pr.result.status == "OK")
    ng_count  = sum(1 for pr in run.hops
                    if pr.result is not None and pr.result.status != "OK")
    err_count = sum(1 for pr in run.hops if pr.result is None)

    names = " → ".join(_html.escape(w.name) for w in run.path.waypoints)
    rows_html = ""
    for i, pr in enumerate(run.hops):
        wp_from = _html.escape(run.path.waypoints[i].name)
        wp_to   = _html.escape(run.path.waypoints[i + 1].name)
        pid     = pr.row.path_id                 # validated: [A-Za-z0-9_-]+
        href    = f"#{pid}" if anchor_links else f"{pid}/report.html"
        classes = []
        r = pr.result
        classes.append("err" if r is None else ("ok" if r.status == "OK" else "ng"))
        if pr is worst:
            classes.append("worst")
        cls = " ".join(classes)
        if r is None:
            rows_html += (
                f"<tr class='{cls}'><td>{i + 1}</td>"
                f"<td class='c-name'>{wp_from} → {wp_to}</td>"
                f"<td class='c-status'>ERROR</td>"
                f"<td colspan='{len(_HOP_COL_KEYS) - 3}'>"
                f"{_html.escape(str(pr.error))}</td></tr>\n"
            )
            continue
        freq_disp = f"{pr.params.freq_mhz:.1f}" if pr.params else "—"
        note_esc  = _html.escape(pr.row.note)
        pid_safe  = pr.row.path_id           # validated: [A-Za-z0-9_-]+
        # 単位は列見出しが持つ（`unit=False`）＝バッチ台帳と同じ約束。数値だけを
        # 並べるほうが桁で読める（画面パネルの桁揃えと同じ理由）。
        rows_html += (
            f"<tr class='{cls}'><td>{i + 1}</td>"
            f"<td class='c-name'><a href='{href}'>{wp_from} → {wp_to}</a></td>"
            f"<td class='c-status'>{r.status}</td>"
            f"<td>{freq_disp}</td>"
            f"<td>{pr.row.h_tx:.1f} / {pr.row.h_rx:.1f}</td>"
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
            f"<img src='{pid_safe}/profile.png' class='thumb'></a></td></tr>\n"
        )

    worst_label = "—"
    if worst is not None:
        idx = run.hops.index(worst)
        worst_label = (f"#{idx + 1} "
                       f"{_html.escape(run.path.waypoints[idx].name)} → "
                       f"{_html.escape(run.path.waypoints[idx + 1].name)}")

    map_html = (f"<div class='map'><img src='data:image/png;base64,{map_b64}'></div>"
                if map_b64 else
                f"<p class='note'>{i18n.t('html_map_unavailable')}</p>")
    memo_html = (f"<p class='route-line'>{_html.escape(memo)}</p>" if memo else "")
    # 全ページ連結（`report_all.html`）への導線＝**バッチ台帳と同じ**（単体の
    # route.html にだけ出し、連結文書では自分自身への案内になるので出さない。
    # 画面でだけ見える＝印刷では消える）。中継は report_all.html を作っていたのに
    # そこへ辿り着く導線がどこにも無く、事実上「無い機能」になっていた。
    all_link = "" if anchor_links else (
        f'<p class="all-link no-print">'
        f'<a href="report_all.html">{_html.escape(i18n.t("html_all_link"))}</a></p>'
    )

    return (
        '<section class="sheet multihop">'
        + report_common.page_header(i18n.t("mh_report_title"), project_name,
                                    run.path.path_id)
        + f'<p class="route-line">{names}</p>'
        + memo_html
        + all_link
        # カードは**全体判定の 4 枚＋内訳の 3 枚**。前半は中継固有（全体は min で
        # 決まる／どの区間が決めているか）、後半は**バッチ台帳と同じ OK/NG/ERR の
        # 内訳**＝同じ性質の情報を同じ見た目で出す（⑧）。
        + '<div class="cards">'
        + f'<div class="card {status_cls}"><div class="lbl">'
          f'{i18n.t("mh_overall")}</div><div class="val">'
          f'{"OK" if run.ok else "NG"}</div></div>'
        + f'<div class="card {status_cls}"><div class="lbl">'
          f'{i18n.t("mh_overall_margin")}</div><div class="val">{overall_txt}</div></div>'
        + f'<div class="card"><div class="lbl">{i18n.t("mh_hops")}</div>'
          f'<div class="val">{len(run.hops)}</div></div>'
        + f'<div class="card"><div class="lbl">{i18n.t("mh_worst_hop")}</div>'
          f'<div class="val">{worst_label}</div></div>'
        + f'<div class="card ok"><div class="lbl">{i18n.t("html_ok")}</div>'
          f'<div class="val">{ok_count}</div></div>'
        + f'<div class="card ng"><div class="lbl">{i18n.t("html_ng")}</div>'
          f'<div class="val">{ng_count}</div></div>'
        + f'<div class="card err"><div class="lbl">{i18n.t("html_error")}</div>'
          f'<div class="val">{err_count}</div></div>'
        + '</div>'
        + map_html
        + '<table class="hops"><thead><tr>'
        + _hop_header_cells()
        + '</tr></thead><tbody>' + rows_html + '</tbody></table>'
        + f'<p class="note">{i18n.t("mh_regenerative_note")}</p>'
        + report_common.page_footer(i18n.t("mh_mode_label"))
        + '</section>'
    )


def save_route_html(run: MultiHopRun, project_name: str = "", memo: str = "",
                    map_b64: "str | None" = None) -> None:
    """`route.html`（合成シート 1 枚）を保存する。"""
    doc = report_common.html_document(
        _html.escape(i18n.t("mh_report_title")),
        route_sheet_css(),
        route_sheet_html(run, project_name, memo, map_b64),
    )
    with open(os.path.join(run.save_dir, "route.html"), "w", encoding="utf-8") as f:
        f.write(doc)


def save_report_all_html(run: MultiHopRun, project_name: str = "", memo: str = "",
                         map_b64: "str | None" = None) -> None:
    """`report_all.html`（合成シート＋全ホップのシートを 1 文書へ連結）。

    バッチと同じく **Ctrl+P 一発で全ホップぶんの PDF** にするための器。断片は
    実行時に `PathResult.sheet_html` へ溜まっているものを使う＝ディスクを
    読み直さない（a2 の分割でそう作ってある）。
    """
    import report_path

    sheets = [route_sheet_html(run, project_name, memo, map_b64, anchor_links=True)]
    sheets += [pr.sheet_html for pr in run.hops if pr.sheet_html]
    doc = report_common.html_document(
        _html.escape(i18n.t("mh_report_title")),
        route_sheet_css() + report_summary.summary_sheet_css()
        + report_path.path_sheet_css(),
        "\n".join(sheets) + report_common.fit_to_page_script(),
    )
    with open(os.path.join(run.save_dir, "report_all.html"), "w",
              encoding="utf-8") as f:
        f.write(doc)
