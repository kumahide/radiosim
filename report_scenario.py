"""
report_scenario.py
==================
条件探索（A-1 比較 / A-2 スイープ）の出力生成（ヘッドレス）。

`scenario.ScenarioRun` を受け取り、**A4 縦 1 枚**のシート断片と文書（HTML）、
および CSV を書き出す。A4 骨格・ヘッダ/フッタ・縮小フィットは
[report_common](report_common.py) と共有する＝2.5a2 の「断片と文書の分離」が
そのままここへ効く（新テンプレは断片を1つ足すだけ）。

出力形式の決定（2026-07-25・ユーザー選択）:
  - スイープは **折れ線＋表の両方**。折れ線は「どこで判定が反転するか」を一目で、
    表は報告書へ数値を載せるため（設計哲学②＝別ソフトへ貼る手順を残さない）。
  - 比較は条件を横に並べた**差分表**（差が出た行だけ強調・Δ 列つき）。
"""

from __future__ import annotations

import base64
import csv
import html as _html
import io
import os

import i18n
import mpl_fonts
import report_common
import scenario as scn
import units

# 軸ごとの単位（表・折れ線の軸ラベルに使う）。距離以外は素の単位で、
# 距離書式（units）と違い変換は無い＝ここは「ラベル」の一覧。
AXIS_UNITS: dict[str, str] = {
    "freq_mhz": "MHz", "p_tx": "dBm", "gain_tx": "dBi", "gain_rx": "dBi",
    "sens": "dBm", "h_tx": "m", "h_rx": "m", "veg_h": "m",
    "k_factor": "", "rain_rate": "mm/h",
}

# 比較シートに並べる行（i18n キー, 取り出し方）。数値は dB 系で統一。
_COMPARE_ROWS = (
    ("html_status",      lambda p: p.result.status,               None),
    ("html_rx_level",    lambda p: p.result.p_rx,                 "dBm"),
    ("html_act_margin",  lambda p: p.result.actual_margin,        "dB"),
    ("html_total_loss",  lambda p: p.result.total_loss,           "dB"),
    ("html_fspl",        lambda p: p.result.fspl,                 "dB"),
    ("html_diff_loss",   lambda p: p.result.diff_loss,            "dB"),
    ("html_veg_loss",    lambda p: p.result.veg_loss,             "dB"),
    ("html_env_loss",    lambda p: p.result.env_loss,             "dB"),
    ("html_rain_loss",   lambda p: p.result.rain_loss,            "dB"),
    ("html_gas_loss",    lambda p: p.result.gas_loss,             "dB"),
    ("html_eirp",        lambda p: p.result.eirp,                 "dBm"),
)


def axis_label(axis: str) -> str:
    """スイープ軸の見出し（例: ``送信アンテナ高 (m)``）を返す。"""
    name = i18n.t(f"scn_axis_{axis}")
    unit = AXIS_UNITS.get(axis, "")
    return f"{name} ({unit})" if unit else name


# ============================================================
# 折れ線（A-2）＝ヘッドレス PNG → base64
# ============================================================
def render_sweep_png_b64(run: scn.ScenarioRun) -> str:
    """スイープの折れ線（横軸＝振った量／縦軸＝マージン）を base64 PNG で返す。

    しきい値（マージン 0 dB）に水平線を引き、**初めて OK になる点**へ注記を出す
    ＝スイープの主眼は「どこで足りるようになるか」なので、読み手に数えさせない。
    判定の反転位置は `ScenarioRun.first_ok_index()` が単一の規則（View と同じ）。

    pyplot を使わず Figure + FigureCanvasAgg で描くのでワーカースレッド可
    （report_path.save_profile_png と同じ理由）。
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    mpl_fonts.apply_japanese_font()

    xs = run.axis_values or list(range(len(run.points)))
    ys = run.margins()

    fig = Figure(figsize=(11, 4.2))
    fig.patch.set_facecolor("white")
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_axes((0.085, 0.17, 0.895, 0.76))
    ax.set_facecolor("white")

    ax.axhline(0.0, color="#c62828", lw=1.2, linestyle="--")
    ax.plot(xs, ys, color="#1565c0", lw=2.0, marker="o", markersize=5)
    # 判定で塗り分ける（OK=緑 / NG=橙）＝表の行色と同じ意味づけ。
    ok_x  = [x for x, p in zip(xs, run.points) if p.ok]
    ok_y  = [p.result.actual_margin for p in run.points if p.ok]
    ng_x  = [x for x, p in zip(xs, run.points) if not p.ok]
    ng_y  = [p.result.actual_margin for p in run.points if not p.ok]
    ax.scatter(ok_x, ok_y, color="#2e7d32", zorder=3, s=42)
    ax.scatter(ng_x, ng_y, color="#ef6c00", zorder=3, s=42)

    # 深く塞がれた点はマージンが -1000 dB 級になり、線形軸だと**しきい値付近が
    # 潰れて読めない**（スイープの主眼はそこ）。振れ幅が大きいときだけ symlog に
    # 切り替える＝0 付近は線形のまま、遠い値は圧縮して両方見せる。
    if max(ys) - min(ys) > 200:
        ax.set_yscale("symlog", linthresh=10)
    ax.margins(y=0.15)          # 注記が上端で切れないよう余白を作る

    idx = run.first_ok_index()
    if idx >= 0:
        ax.axvline(xs[idx], color="#2e7d32", lw=1.0, linestyle=":")
        ax.annotate(
            i18n.t("scn_first_ok").format(value=f"{xs[idx]:g}"),
            xy=(xs[idx], ys[idx]), xytext=(6, 12), textcoords="offset points",
            fontsize=12, color="#2e7d32", annotation_clip=False,
        )

    ax.set_xlabel(axis_label(run.axis), fontsize=13)
    ax.set_ylabel(i18n.t("scn_margin_axis"), fontsize=13)
    ax.tick_params(labelsize=11)
    ax.grid(True, alpha=0.25)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    img = base64.b64encode(buf.getvalue()).decode()
    fig.clf()
    del canvas, fig
    return img


# ============================================================
# シート断片
# ============================================================
def scenario_sheet_css() -> str:
    """条件探索シート固有のスタイル（すべて `.sheet.scenario` へスコープ）。

    ⚠️ per-path / summary と同じ理由でスコープ必須（連結文書で後勝ちの上書きが
    起きる＝report_common の docstring）。ガード＝tests/test_report.py。
    """
    return """
/* --- 条件探索シート（比較 / スイープ） --- */
.sheet.scenario{display:flex;flex-direction:column}
.sheet.scenario .page-header{padding-bottom:4px;margin-bottom:7px}
.sheet.scenario .page-footer{margin-top:auto;padding-top:4px}
@media print{.sheet.scenario{min-height:calc(297mm - 14mm - 8mm)}}
.sheet.scenario .meta{background:white;border-radius:8px;padding:6px 14px;margin-bottom:8px;box-shadow:0 1px 3px rgba(0,0,0,.12);font-size:11px;color:#455a64}
.sheet.scenario .meta b{color:#222}
.sheet.scenario .chart{width:100%;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.15);margin-bottom:8px}
.sheet.scenario table.scn{border-collapse:collapse;width:100%;background:white;box-shadow:0 1px 3px rgba(0,0,0,.12)}
.sheet.scenario table.scn th{background:#455a64;color:white;padding:4px 6px;font-size:9px;
  white-space:nowrap;text-align:center;border-right:1px solid rgba(255,255,255,.22)}
.sheet.scenario table.scn td{padding:3px 6px;border-bottom:1px solid #eee;border-right:1px solid #e6e6e6;
  font-size:10px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.sheet.scenario table.scn td.name{text-align:left;color:#666}
.sheet.scenario table.scn th:last-child,.sheet.scenario table.scn td:last-child{border-right:none}
.sheet.scenario table.scn tr{break-inside:avoid}
.sheet.scenario tr.ok{background:#f1f8e9}.sheet.scenario tr.ng{background:#fff8e1}
.sheet.scenario .s-ok{color:#2e7d32;font-weight:bold}.sheet.scenario .s-ng{color:#c62828;font-weight:bold}
/* 比較シート：差の出た行だけ地の色を変えて目を誘導する（Δ 列と対） */
.sheet.scenario tr.diff td{background:#fffde7}
.sheet.scenario td.delta{color:#00695c;font-weight:bold}
/* 条件そのものの行（何を変えたか）は下段にまとめ、地の色で数値行と見分ける */
.sheet.scenario tr.cond td{background:#fafbfc;color:#546e7a}
.sheet.scenario tr.cond:first-of-type td{border-top:2px solid #cfd8dc}
.sheet.scenario .report-memo{background:#f7f9fa;border:1px solid #e0e6e9;border-radius:6px;padding:5px 10px;margin-bottom:6px;font-size:11px;color:#37474f}
.sheet.scenario .report-memo .rm-label{color:#90a4ae;font-weight:bold;margin-right:4px}
"""


def _meta_block(run: scn.ScenarioRun) -> str:
    """経路（固定された前提）を 1 行で示す＝「何を固定して何を振ったか」。"""
    p = run.base_params
    return (
        f'<div class="meta">'
        f'<b>{i18n.t("scn_fixed_path")}</b>: '
        f'{p.lat_tx:.5f}, {p.lon_tx:.5f} → {p.lat_rx:.5f}, {p.lon_rx:.5f}'
        f'　/　{i18n.t("html_horiz_dist")}: {units.format_distance(run.terrain.horiz_dist_km)}'
        f'　/　{i18n.t("scn_samples")}: {p.num}'
        f'</div>'
    )


def _compare_table(run: scn.ScenarioRun) -> str:
    """比較表（行＝項目・列＝条件）。2 条件のときは Δ 列を足す。"""
    pts = run.points
    show_delta = len(pts) == 2
    head = "".join(f"<th>{_html.escape(p.label)}</th>" for p in pts)
    if show_delta:
        head += f"<th>{i18n.t('scn_delta')}</th>"

    rows = ""
    for key, getter, unit in _COMPARE_ROWS:
        vals = [getter(p) for p in pts]
        if isinstance(vals[0], str):                    # 判定行
            cells = "".join(
                f"<td class='s-{'ok' if v == 'OK' else 'ng'}'>{v}</td>" for v in vals
            )
            delta = "<td></td>" if show_delta else ""
            differs = len(set(vals)) > 1
        else:
            cells = "".join(f"<td>{v:+.2f}</td>" if unit == "dB" and key.endswith("margin")
                            else f"<td>{v:.2f}</td>" for v in vals)
            differs = max(vals) - min(vals) > 0.005
            delta = ""
            if show_delta:
                d = vals[1] - vals[0]
                delta = f"<td class='delta'>{d:+.2f}</td>" if differs else "<td>—</td>"
        label = i18n.t(key) + (f" ({unit})" if unit else "")
        cls = " class='diff'" if differs else ""
        rows += f"<tr{cls}><td class='name'>{label}</td>{cells}{delta}</tr>\n"

    # 条件そのもの（何を変えたか）を下段に出す＝表だけ見て再現できるように。
    changed = sorted({k for p in pts for k in p.overrides})
    for key in changed:
        vals = [p.overrides.get(key, "—") for p in pts]
        cells = "".join(
            f"<td>{v if isinstance(v, str) else f'{v:g}'}</td>" for v in vals
        )
        delta = "<td></td>" if show_delta else ""
        unit = AXIS_UNITS.get(key, "")
        label = i18n.t(f"scn_axis_{key}") + (f" ({unit})" if unit else "")
        rows += f"<tr class='cond'><td class='name'>{label}</td>{cells}{delta}</tr>\n"

    return (
        f'<table class="scn"><thead><tr><th></th>{head}</tr></thead>'
        f'<tbody>\n{rows}</tbody></table>'
    )


def _sweep_table(run: scn.ScenarioRun) -> str:
    """スイープ表（1 行＝1 点）。判定で行色を塗り、初 OK 点を強調する。"""
    idx = run.first_ok_index()
    head = "".join(f"<th>{h}</th>" for h in (
        axis_label(run.axis), i18n.t("html_rx_level") + " (dBm)",
        i18n.t("html_act_margin") + " (dB)", i18n.t("html_total_loss") + " (dB)",
        i18n.t("html_col_f1"), i18n.t("html_status"),
    ))
    rows = ""
    for i, p in enumerate(run.points):
        r = p.result
        cls = "ok" if p.ok else "ng"
        mark = " ◀" if i == idx else ""
        rows += (
            f"<tr class='{cls}'>"
            f"<td>{p.label}</td>"
            f"<td>{r.p_rx:.2f}</td>"
            f"<td>{r.actual_margin:+.2f}</td>"
            f"<td>{r.total_loss:.2f}</td>"
            f"<td>{units.format_blocked_ratio(r.blocked_ratio, unit=False)}</td>"
            f"<td class='s-{cls}'>{r.status}{mark}</td></tr>\n"
        )
    return (
        f'<table class="scn"><thead><tr>{head}</tr></thead>'
        f'<tbody>\n{rows}</tbody></table>'
    )


def scenario_sheet_html(run: scn.ScenarioRun, project_name: str = "",
                        memo: str = "", chart_b64: str = "") -> str:
    """条件探索の A4 シート断片（`<section class="sheet scenario">`）を返す。

    chart_b64 は A-2 の折れ線（`render_sweep_png_b64`）。空なら図を省く
    （比較シートは図を持たない＝表が主役）。
    """
    title = i18n.t("scn_sweep_title") if run.kind == "sweep" else i18n.t("scn_compare_title")
    memo_block = ""
    if memo:
        memo_block = (
            f'<div class="report-memo">'
            f'<span class="rm-label">{i18n.t("html_report_memo")}</span> '
            f'{_html.escape(memo)}</div>'
        )
    chart_block = ""
    if chart_b64:
        chart_block = (
            f'<img class="chart" src="data:image/png;base64,{chart_b64}" '
            f'alt="{_html.escape(title)}">'
        )
    table = _sweep_table(run) if run.kind == "sweep" else _compare_table(run)

    return f"""<section class="sheet scenario">
<div class="fit-outer"><div class="fit">
{report_common.page_header(title, project_name)}
{memo_block}
{_meta_block(run)}
{chart_block}
{table}
</div></div>
{report_common.page_footer(i18n.t("scn_mode"))}
</section>"""


# ============================================================
# 保存
# ============================================================
def save_scenario_package(run: scn.ScenarioRun, save_dir: str,
                          project_name: str = "", memo: str = "") -> None:
    """`scenario.html` と `scenario.csv` を保存する。

    HTML は A4 縦 1 枚（Ctrl+P でそのまま PDF）。CSV は表計算で扱うための
    生値（**表示のクランプ・桁区切りは入れない**＝units の CSV 系を使う）。
    """
    chart = render_sweep_png_b64(run) if run.kind == "sweep" else ""
    doc_title = i18n.t("scn_sweep_title") if run.kind == "sweep" else i18n.t("scn_compare_title")
    if project_name:
        doc_title = f"{project_name} - {doc_title}"

    html = report_common.html_document(
        _html.escape(doc_title),
        scenario_sheet_css(),
        scenario_sheet_html(run, project_name, memo, chart) + "\n"
        + report_common.fit_to_page_script(),
    )
    with open(os.path.join(save_dir, "scenario.html"), "w", encoding="utf-8") as f:
        f.write(html)
    save_scenario_csv(run, save_dir)


def save_scenario_csv(run: scn.ScenarioRun, save_dir: str) -> None:
    """全点の数値を CSV で保存する（1 行＝1 条件／1 点）。"""
    path = os.path.join(save_dir, "scenario.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "label", run.axis or "condition", "status",
            "rx_dbm", "margin_db", "total_loss_db",
            "fspl_db", "diff_db", "veg_db", "env_db", "rain_db", "gas_db",
            "f1_pct", "slant_m",
            "freq_mhz", "p_tx_dbm", "gain_tx_dbi", "gain_rx_dbi", "sens_dbm",
            "h_tx", "h_rx", "veg_h", "rain_mmh", "env_type", "diff_method",
        ])
        base = run.base_params
        for p in run.points:
            o = p.overrides
            def val(name, default):
                return o.get(name, default)
            r = p.result
            w.writerow([
                p.label, o.get(run.axis, "") if run.axis else p.label, r.status,
                f"{r.p_rx:.2f}", f"{r.actual_margin:.2f}", f"{r.total_loss:.2f}",
                f"{r.fspl:.2f}", f"{r.diff_loss:.2f}", f"{r.veg_loss:.2f}",
                f"{r.env_loss:.2f}", f"{r.rain_loss:.2f}", f"{r.gas_loss:.2f}",
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
            ])
