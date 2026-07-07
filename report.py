"""
report.py
=========
バッチ結果の出力生成（ヘッドレス）。

batch.py から分離した出力層。実行エンジン（batch.py）が生成した PathResult を
受け取り、per-path の PNG/HTML/KML とサマリ CSV/HTML/KML を書き出す。
UI 知識ゼロ・副作用はファイル I/O のみ（Web 再利用のための継ぎ目を維持）。
"""

from __future__ import annotations

import base64
import csv
import html as _html
import io
import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np

import coords
import i18n
import models
import mpl_fonts
import report_map
import version

if TYPE_CHECKING:
    import simulation as sim
    from batch import PathResult

logger = logging.getLogger("radiosim")


# ============================================================
# レポート v2 ＝ A4 ドロップイン骨格（per-path / summary 共通）
# ------------------------------------------------------------
# 目的：生成 HTML を「そのまま報告書へ綴じ込める portrait A4 の確定1枚」にする。
# 画面でも A4 用紙が見える WYSIWYG（.sheet）＋ 印刷は @page A4。PDF 化は
# ゼロ依存＝ブラウザ Ctrl+P（PDF エンジンは入れない）。ブラウザ挿入の印刷
# ヘッダ/フッタは CSS で抑制できないため、自前のヘッダ/フッタを持ち、利用者は
# 印刷時「ヘッダーとフッターをオフ」にする前提とする。
# ============================================================

def _a4_base_css() -> str:
    """per-path / summary が共通で使う A4 骨格スタイルを返す。

    画面（screen）では中央に A4 用紙（.sheet）を描いて WYSIWYG に、
    印刷（print）では余白を @page に委ね .sheet の装飾を外す。
    """
    return """
/* --- A4 骨格（v2 ドロップイン） --- */
*{box-sizing:border-box}
.sheet{background:#fff}
.page-header{display:flex;justify-content:space-between;align-items:flex-end;
  border-bottom:2px solid #455a64;padding-bottom:6px;margin-bottom:14px}
.page-header .ph-left{min-width:0}
.page-header .proj-name{font-size:12px;color:#455a64;font-weight:bold;margin-bottom:2px}
.page-header .proj-name:empty{display:none}
.page-header .ph-title{font-size:18px;font-weight:bold;color:#222;margin:0}
.page-header .ph-right{text-align:right;font-size:10px;color:#888;
  white-space:nowrap;padding-left:12px}
.page-footer{margin-top:16px;padding-top:6px;border-top:1px solid #ddd;
  color:#aaa;font-size:10px;display:flex;justify-content:space-between}
@media screen{
  body{background:#e9e9e9;margin:0;padding:0}
  .sheet{width:210mm;min-height:297mm;padding:14mm;margin:10px auto;
    box-shadow:0 0 8px rgba(0,0,0,.25)}
}
@media print{
  body{background:#fff;margin:0}
  .sheet{width:auto;min-height:0;padding:0;margin:0;box-shadow:none}
  @page{size:A4 portrait;margin:14mm}
  img{break-inside:avoid}
  thead{display:table-header-group}
}
"""


def _page_header(title_html: str, meta_id_esc: str = "", project_name: str = "") -> str:
    """自己同定ヘッダ（左＝案件名スロット＋タイトル／右＝生成日時・ID・版）。

    project_name はユーザー入力の案件名（自由文字列）。空なら `.proj-name` は
    `:empty` で非表示になり従来の見た目を保つ。meta_id_esc は path_id 等の
    識別子（エスケープ済み）。空なら省く。
    """
    gen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    id_line = f"{meta_id_esc}<br>" if meta_id_esc else ""
    proj_esc = _html.escape(project_name)
    return (
        '<header class="page-header">'
        '<div class="ph-left">'
        f'<div class="proj-name">{proj_esc}</div>'
        f'<p class="ph-title">{title_html}</p>'
        '</div>'
        '<div class="ph-right">'
        f'{id_line}{i18n.t("html_generated")}: {gen}<br>{version.APP_FULL}'
        '</div>'
        '</header>'
    )


def _page_footer() -> str:
    """自己同定フッタ（版＋バッチモード注記）。"""
    return (
        '<footer class="page-footer">'
        f'<span>{version.APP_FULL}</span>'
        f'<span>{i18n.t("html_batch_mode")}</span>'
        '</footer>'
    )


def save_path_visuals(pr: PathResult, coord_format: str = "dd",
                      project_name: str = "") -> None:
    """
    PNG と HTML をメインスレッドから保存する。

    matplotlib の TkAgg バックエンドが初期化されている環境では
    バックグラウンドスレッドから matplotlib を使うと tkinter GC 警告が
    発生するため、この関数は必ずメインスレッド（on_path_complete 内）で呼ぶこと。

    coord_format は HTML レポートの人が読む座標セルのみに効く（既定 DD）。
    project_name はレポートヘッダの案件名（自由文字列・空で従来表示）。
    """
    if pr.result is None or pr.terrain is None or pr.params is None:
        return
    try:
        save_profile_png(
            pr.terrain, pr.result, pr.params,
            pr.params.h_tx, pr.params.h_rx, pr.save_dir, coord_format,
            project_name,
        )
        save_path_kml(
            pr.terrain, pr.result, pr.params,
            pr.params.h_tx, pr.params.h_rx, pr.save_dir,
        )
    except Exception as ex:
        logger.error("Visual save failed for '%s': %s", pr.row.path_id, ex)


def save_profile_png(
    terrain:  models.TerrainProfile,
    result:   models.LinkBudgetResult,
    params:   sim.SimParams,
    h_tx:     float,
    h_rx:     float,
    save_dir: str,
    coord_format: str = "dd",
    project_name: str = "",
) -> None:
    """
    地形断面 PNG をバックグラウンドスレッドから保存する。

    pyplot（TkAgg）を使わず Figure + FigureCanvasAgg を直接使うため
    メインスレッド以外から呼んでも安全。

    日本語ラベルの豆腐化を防ぐため、描画前に日本語フォントを明示適用する
    （個別グラフを開いていなくてもレポート PNG が正しく描画される）。
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    mpl_fonts.apply_japanese_font()

    t     = terrain
    elevs = t.elevs_with_curve
    N     = t.num_samples
    y_min = float(np.min(t.raw_elevs)) - 30

    fig    = Figure(figsize=(15, 6))
    fig.patch.set_facecolor("#EAEAEA")
    canvas = FigureCanvasAgg(fig)

    # 地形断面軸
    ax = fig.add_axes((0.06, 0.11, 0.90, 0.78))
    ax.set_facecolor("#F2F2F2")

    veg_top = elevs + params.veg_h
    ax.fill_between(t.d_km_axis, elevs,   y_min,   color="#8B4513", alpha=0.4)
    ax.fill_between(t.d_km_axis, veg_top, elevs,   color="green",   alpha=0.3)

    tx_abs = float(elevs[0])  + h_tx
    rx_abs = float(elevs[-1]) + h_rx
    los    = np.linspace(tx_abs, rx_abs, N)
    f1     = models.fresnel_zone_radii(t.d_km_axis, t.horiz_dist_km, params.freq_mhz)

    ax.plot(t.d_km_axis, los, color="red", linestyle="--", lw=1.5)
    ax.fill_between(t.d_km_axis, los - f1, los + f1, color="cyan", alpha=0.25)
    ax.vlines(
        [0, t.horiz_dist_km],
        [float(elevs[0]),  float(elevs[-1])],
        [tx_abs, rx_abs],
        color="black", lw=3,
    )

    ax.set_title(f"{params.freq_mhz} MHz", fontsize=13, loc="left")
    ax.set_xlabel(i18n.t("graph_dist_axis"), fontsize=11)
    ax.set_ylabel(i18n.t("graph_alt_axis"),  fontsize=11)
    ax.grid(True, alpha=0.2)

    # 統一凡例: 枠外・右上・横1列
    # loc="lower right" → 凡例の右下隅を bbox_to_anchor に合わせる
    # bbox_to_anchor=(1.0, 1.02) → 軸の右端・上端の少し外側
    ax.legend(
        handles=[
            Patch(facecolor="#8B4513", alpha=0.4, label=i18n.t("legend_terrain")),
            Patch(facecolor="green",   alpha=0.3, label=i18n.t("legend_vegetation")),
            Line2D([0], [0], color="red", linestyle="--", lw=1.5, label=i18n.t("legend_los")),
            Patch(facecolor="cyan",    alpha=0.25, label=i18n.t("legend_fresnel")),
        ],
        loc="lower right",
        bbox_to_anchor=(1.0, 1.02),
        ncol=4,
        fontsize=11,
        framealpha=0.9,
        borderaxespad=0,
    )

    # PNG をディスクに保存しつつ、同じ描画を Base64 にも変換する
    png_path = os.path.join(save_dir, "profile.png")
    fig.savefig(png_path, dpi=150)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    # Figure と Canvas の循環参照をメインスレッドで即時解放する。
    # 解放を遅らせると Python 3.14 のインクリメンタル GC が
    # バックグラウンドスレッドで __del__ を呼ぶことがある。
    fig.clf()
    del canvas, fig

    # 経路オーバーレイ地図（ヘッドレス・ベストエフォート）。タイル取得に失敗
    # したら None を返し、レポートは地図なし＋注記で生成される。
    map_b64 = report_map.render_path_map_b64(
        (params.lat_tx, params.lon_tx), (params.lat_rx, params.lon_rx)
    )

    save_path_html(terrain, result, params, h_tx, h_rx, save_dir, img_b64, map_b64,
                   coord_format, project_name)


def save_path_html(
    terrain:  models.TerrainProfile,
    result:   models.LinkBudgetResult,
    params:   sim.SimParams,
    h_tx:     float,
    h_rx:     float,
    save_dir: str,
    img_b64:  str,
    map_b64:  "str | None" = None,
    coord_format: str = "dd",
    project_name: str = "",
) -> None:
    """per-path の report.html を生成する（グラフ・地図は Base64 埋め込み）。

    map_b64 が None のとき（タイル取得失敗）は地図を省き注記を表示する。
    coord_format は人が読む座標セルのみに効く（"dd"|"dms"）。CSV/KML/settings は
    再読込・規格のため DD 固定。既定 DD でヘッドレス呼び出しは表示設定に非依存。
    project_name はヘッダの案件名（自由文字列・空で従来表示）。
    """
    tx_coords = coords.format_pair(params.lat_tx, params.lon_tx, coord_format)
    rx_coords = coords.format_pair(params.lat_rx, params.lon_rx, coord_format)
    path_id     = os.path.basename(save_dir)
    path_id_esc = _html.escape(path_id)
    status_cls  = "ok" if result.status == "OK" else "ng"
    model_label = i18n.t("html_model_deygout") if result.diff_method == "deygout" else i18n.t("html_model_single")
    env_label   = i18n.t(f"env_{result.env_type}")

    terrain_rows = "\n".join(
        f"<tr><td>{d:.4f}</td><td>{h:.2f}</td></tr>"
        for d, h in zip(terrain.d_km_axis, terrain.raw_elevs)
    )

    # 経路オーバーレイ地図セクション。map_b64 が無い（タイル取得失敗）ときは
    # 地図を省いて注記を表示する（レポート自体は必ず生成される）。
    if map_b64:
        map_block = (
            f'<img class="graph" src="data:image/png;base64,{map_b64}" '
            f'alt="{_html.escape(i18n.t("html_map_title"))}">'
        )
    else:
        map_block = (
            f'<p class="map-note">{_html.escape(i18n.t("html_map_unavailable"))}</p>'
        )

    html = f"""<!DOCTYPE html>
<html lang="{i18n.t('html_lang')}">
<head>
<meta charset="UTF-8">
<title>{i18n.t('html_path_title')} — {path_id_esc}</title>
<style>
{_a4_base_css()}
body{{font-family:Arial,sans-serif;font-size:13px}}
.cards{{display:flex;gap:12px;margin-bottom:16px;break-inside:avoid}}
.card{{background:white;border:1px solid #eee;border-radius:8px;padding:12px 20px;box-shadow:0 1px 3px rgba(0,0,0,.12);text-align:center;min-width:100px}}
.card .lbl{{font-size:10px;color:#999;text-transform:uppercase}}
.card .val{{font-size:22px;font-weight:bold;color:#333}}
.card.ok .val{{color:#2e7d32}}.card.ng .val{{color:#c62828}}
.graph{{width:100%;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.15);margin-bottom:16px}}
.map-note{{color:#999;font-size:12px;font-style:italic;background:white;border-radius:8px;padding:12px 16px;box-shadow:0 1px 3px rgba(0,0,0,.12);margin-bottom:16px}}
.cols{{display:flex;gap:16px;margin-bottom:16px}}
.col{{flex:1;background:white;border-radius:8px;padding:14px 18px;box-shadow:0 1px 3px rgba(0,0,0,.12)}}
.col h3{{margin:0 0 10px;font-size:13px;color:#455a64;border-bottom:1px solid #eee;padding-bottom:6px}}
table.info{{border-collapse:collapse;width:100%}}
table.info td{{padding:4px 6px;border-bottom:1px solid #f0f0f0;font-size:12px}}
table.info td:first-child{{color:#888;width:50%}}
details{{background:white;border-radius:8px;padding:10px 16px;box-shadow:0 1px 3px rgba(0,0,0,.12)}}
summary{{cursor:pointer;font-weight:bold;color:#455a64;font-size:12px}}
table.terrain{{border-collapse:collapse;width:100%;margin-top:8px;font-size:11px}}
table.terrain th{{background:#455a64;color:white;padding:4px 8px;text-align:left}}
table.terrain td{{padding:3px 8px;border-bottom:1px solid #eee}}
</style>
</head>
<body>
<div class="sheet">
{_page_header(f"{i18n.t('html_path_title')} — {path_id_esc}", path_id_esc, project_name)}

<div class="cards">
  <div class="card {status_cls}"><div class="lbl">{i18n.t('html_status')}</div><div class="val">{result.status}</div></div>
  <div class="card"><div class="lbl">{i18n.t('html_rx_level')}</div><div class="val">{result.p_rx:.1f} dBm</div></div>
  <div class="card {status_cls}"><div class="lbl">{i18n.t('html_act_margin')}</div><div class="val">{result.actual_margin:+.1f} dB</div></div>
  <div class="card"><div class="lbl">{i18n.t('html_total_loss')}</div><div class="val">{result.total_loss:.1f} dB</div></div>
</div>

<img class="graph" src="data:image/png;base64,{img_b64}" alt="Terrain Profile">
{map_block}

<div class="cols">
  <div class="col">
    <h3>{i18n.t('html_site_info')}</h3>
    <table class="info">
      <tr><td>{i18n.t('html_tx_coords')}</td><td>{tx_coords}</td></tr>
      <tr><td>{i18n.t('html_rx_coords')}</td><td>{rx_coords}</td></tr>
      <tr><td>{i18n.t('html_tx_height')}</td><td>{h_tx:.1f} m</td></tr>
      <tr><td>{i18n.t('html_rx_height')}</td><td>{h_rx:.1f} m</td></tr>
      <tr><td>{i18n.t('html_slant_dist')}</td><td>{result.slant_dist_km:.3f} km</td></tr>
      <tr><td>{i18n.t('html_horiz_dist')}</td><td>{terrain.horiz_dist_km:.3f} km</td></tr>
    </table>
    <h3 style="margin-top:14px">{i18n.t('html_radio_settings')}</h3>
    <table class="info">
      <tr><td>{i18n.t('html_frequency')}</td><td>{params.freq_mhz} MHz</td></tr>
      <tr><td>{i18n.t('html_tx_power')}</td><td>{params.p_tx} dBm</td></tr>
      <tr><td>{i18n.t('html_tx_gain')}</td><td>{params.gain_tx} dBi</td></tr>
      <tr><td>{i18n.t('html_rx_gain')}</td><td>{params.gain_rx} dBi</td></tr>
      <tr><td>{i18n.t('html_sensitivity')}</td><td>{params.sens} dBm</td></tr>
    </table>
  </div>
  <div class="col">
    <h3>{i18n.t('html_link_budget')}</h3>
    <table class="info">
      <tr><td>{i18n.t('html_eirp')}</td><td>{result.eirp:.2f} dBm</td></tr>
      <tr><td>{i18n.t('html_fspl')}</td><td>{result.fspl:.2f} dB</td></tr>
      <tr><td>{i18n.t('html_diff_loss')}</td><td>{result.diff_loss:.2f} dB</td></tr>
      <tr><td>{i18n.t('html_veg_loss')}</td><td>{result.veg_loss:.2f} dB</td></tr>
      <tr><td>{i18n.t('html_env_loss')}</td><td>{result.env_loss:.2f} dB</td></tr>
      <tr><td>{i18n.t('html_rain_loss')}</td><td>{result.rain_loss:.2f} dB</td></tr>
      <tr><td>{i18n.t('html_gas_loss')}</td><td>{result.gas_loss:.2f} dB</td></tr>
      <tr><td><b>{i18n.t('html_total_loss_row')}</b></td><td><b>{result.total_loss:.2f} dB</b></td></tr>
      <tr><td>{i18n.t('html_rx_ant_gain')}</td><td>+{params.gain_rx:.2f} dBi</td></tr>
      <tr><td><b>{i18n.t('html_rx_level')}</b></td><td><b>{result.p_rx:.2f} dBm</b></td></tr>
      <tr><td>{i18n.t('html_threshold')}</td><td>{params.sens:.2f} dBm</td></tr>
      <tr><td><b>{i18n.t('html_act_margin')}</b></td><td><b>{result.actual_margin:+.2f} dB</b></td></tr>
    </table>
    <h3 style="margin-top:14px">{i18n.t('html_environment')}</h3>
    <table class="info">
      <tr><td>{i18n.t('html_env_type')}</td><td>{env_label}</td></tr>
      <tr><td>{i18n.t('html_diff_model')}</td><td>{model_label}</td></tr>
      <tr><td>{i18n.t('html_k_factor')}</td><td>{result.current_k:.1f}</td></tr>
      <tr><td>{i18n.t('html_f1_obstruct')}</td><td>{result.blocked_ratio:.1f} %</td></tr>
      <tr><td>{i18n.t('html_rain_rate')}</td><td>{params.rain_rate} mm/h</td></tr>
    </table>
  </div>
</div>

<details>
<summary>{i18n.t('html_terrain_data')} ({terrain.num_samples} points)</summary>
<table class="terrain">
<thead><tr><th>{i18n.t('html_dist_col')}</th><th>{i18n.t('html_elev_col')}</th></tr></thead>
<tbody>
{terrain_rows}
</tbody>
</table>
</details>

{_page_footer()}
</div>
</body>
</html>"""

    with open(os.path.join(save_dir, "report.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ============================================================
# サマリ出力
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
            "slant_km", "f1_pct", "note", "error",
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
                    pr.row.path_id, r.status,
                    freq_val, gain_tx_val, gain_rx_val, h_tx_val, h_rx_val,
                    f"{r.p_rx:.2f}",          f"{r.actual_margin:.2f}",
                    f"{r.fspl:.2f}",           f"{r.diff_loss:.2f}",
                    f"{r.veg_loss:.2f}",       f"{r.env_loss:.2f}",
                    f"{r.rain_loss:.2f}",      f"{r.gas_loss:.2f}",
                    f"{r.total_loss:.2f}",
                    f"{r.slant_dist_km:.3f}",  f"{r.blocked_ratio:.1f}",
                    pr.row.note, "",
                ])
            else:
                writer.writerow([
                    pr.row.path_id, "ERROR",
                    freq_val, gain_tx_val, gain_rx_val, h_tx_val, h_rx_val,
                    "", "", "", "", "", "", "", "", "", "", "",
                    pr.row.note, str(pr.error),
                ])


def save_summary_html(results: list[PathResult], batch_dir: str,
                      project_name: str = "", memo: str = "") -> None:
    """バッチの summary.html を生成する。

    project_name はヘッダの案件名、memo はサーベイ全体の自由メモ（どちらも
    ユーザー入力の自由文字列・空で従来表示）。memo は非空時のみ p1 のヘッダ直下に
    小ブロックとして表示する（サーベイ全体の注記＝summary のみ）。
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
                f"<td>{note_esc}</td>"
                f"<td></td></tr>\n"
            )
            continue
        r   = pr.result
        cls = "ok" if r.status == "OK" else "ng"
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
            f"<td>{r.slant_dist_km:.3f}</td>"
            f"<td>{r.blocked_ratio:.1f}</td>"
            f"<td>{note_esc}</td>"
            f"<td><a href='{pid_safe}/report.html'>"
            f"<img src='{pid_safe}/profile.png' style='max-height:60px;border:1px solid #ddd;border-radius:3px;vertical-align:middle;'>"
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

    html = f"""<!DOCTYPE html>
<html lang="{i18n.t('html_lang')}">
<head>
<meta charset="UTF-8">
<title>{i18n.t('html_batch_title')}</title>
<style>
{_a4_base_css()}
body{{font-family:Arial,sans-serif;font-size:13px}}
.cards{{display:flex;gap:12px;margin-bottom:20px;break-inside:avoid}}
.card{{background:white;border:1px solid #eee;border-radius:8px;padding:14px 20px;box-shadow:0 1px 3px rgba(0,0,0,.12);text-align:center;min-width:80px}}
.card .lbl{{font-size:10px;color:#999;text-transform:uppercase}}
.card .val{{font-size:28px;font-weight:bold;color:#333}}
.card.ok .val{{color:#2e7d32}}.card.ng .val{{color:#c62828}}.card.err .val{{color:#e65100}}
table.summary{{border-collapse:collapse;width:100%;background:white;box-shadow:0 1px 3px rgba(0,0,0,.12)}}
table.summary th{{background:#455a64;color:white;padding:7px 10px;text-align:left;font-size:11px;white-space:nowrap}}
table.summary td{{padding:5px 10px;border-bottom:1px solid #eee;font-size:12px;white-space:nowrap}}
table.summary tr{{break-inside:avoid}}
tr.ok{{background:#f1f8e9}}tr.ng{{background:#fff8e1}}tr.err{{background:#fce4ec}}
.s-ok{{color:#2e7d32;font-weight:bold}}.s-ng{{color:#c62828;font-weight:bold}}.s-err{{color:#bf360c;font-weight:bold}}
.report-memo{{background:#f7f9fa;border:1px solid #e0e6e9;border-radius:6px;padding:8px 12px;margin-bottom:16px;font-size:12px;color:#37474f;break-inside:avoid}}
.report-memo .rm-label{{color:#90a4ae;font-weight:bold;margin-right:4px}}
</style>
</head>
<body>
<div class="sheet">
{_page_header(i18n.t('html_batch_title'), project_name=project_name)}
{memo_block}
<div class="cards">
  <div class="card"><div class="lbl">{i18n.t('html_total')}</div><div class="val">{total}</div></div>
  <div class="card ok"><div class="lbl">{i18n.t('html_ok')}</div><div class="val">{ok_count}</div></div>
  <div class="card ng"><div class="lbl">{i18n.t('html_ng')}</div><div class="val">{ng_count}</div></div>
  <div class="card err"><div class="lbl">{i18n.t('html_error')}</div><div class="val">{err_count}</div></div>
</div>
<table class="summary">
<thead>
<tr>
  <th>{i18n.t('html_col_id')}</th><th>{i18n.t('html_col_status')}</th>
  <th>{i18n.t('html_col_freq')}</th>
  <th>{i18n.t('html_col_gain_tx')}</th><th>{i18n.t('html_col_gain_rx')}</th>
  <th>{i18n.t('html_col_h_tx')}</th><th>{i18n.t('html_col_h_rx')}</th>
  <th>{i18n.t('html_col_rx')}</th><th>{i18n.t('html_col_margin')}</th>
  <th>{i18n.t('html_col_fspl')}</th><th>{i18n.t('html_col_diff')}</th>
  <th>{i18n.t('html_col_veg')}</th><th>{i18n.t('html_col_env')}</th>
  <th>{i18n.t('html_col_rain')}</th><th>{i18n.t('html_col_gas')}</th>
  <th>{i18n.t('html_col_total_loss')}</th><th>{i18n.t('html_col_slant')}</th>
  <th>{i18n.t('html_col_f1')}</th><th>{i18n.t('html_col_note')}</th><th>{i18n.t('html_col_graph')}</th>
</tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
{_page_footer()}
</div>
</body>
</html>"""

    with open(os.path.join(batch_dir, "summary.html"), "w", encoding="utf-8") as f:
        f.write(html)


# ============================================================
# KML 出力
# ============================================================

def _kml_line_coords(lats: np.ndarray, lons: np.ndarray, alts: np.ndarray) -> str:
    """KML <coordinates> 内容（lon,lat,alt の改行区切り）を返す。"""
    return "\n".join(
        f"          {float(lo):.6f},{float(la):.6f},{float(al):.1f}"
        for la, lo, al in zip(lats, lons, alts)
    )


def _find_obs_segments(mask: np.ndarray) -> list[tuple[int, int]]:
    """連続する True 区間の (start, end) インデックスリスト（両端 inclusive）を返す。"""
    segs: list[tuple[int, int]] = []
    n, i = len(mask), 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            segs.append((i, j - 1))
            i = j
        else:
            i += 1
    return segs


def save_path_kml(
    terrain:  models.TerrainProfile,
    result:   models.LinkBudgetResult,
    params:   sim.SimParams,
    h_tx:     float,
    h_rx:     float,
    save_dir: str,
) -> None:
    """per-path の path.kml を生成する（Google Earth で 3D 表示可能）。

    要素:
      - TX / RX Placemark
      - Terrain Profile（actual elevation）
      - Line of Sight（OK=緑 / NG=橙）
      - 1st Fresnel Zone 上辺・下辺
      - Fresnel Obstruction（遮蔽区間を赤でハイライト）
    """
    N    = terrain.num_samples
    t    = np.linspace(0, 1, N)
    lats = params.lat_tx + (params.lat_rx - params.lat_tx) * t
    lons = params.lon_tx + (params.lon_rx - params.lon_tx) * t
    elev = terrain.raw_elevs.astype(float)

    tx_alt = float(elev[0])  + h_tx
    rx_alt = float(elev[-1]) + h_rx
    los    = np.linspace(tx_alt, rx_alt, N)
    f1     = models.fresnel_zone_radii(terrain.d_km_axis, terrain.horiz_dist_km, params.freq_mhz)

    los_color = "ff00aa00" if result.status == "OK" else "ff00a5ff"
    path_id   = _html.escape(os.path.basename(save_dir))
    desc_esc  = _html.escape(
        f"Freq: {params.freq_mhz} MHz | RX: {result.p_rx:.1f} dBm | "
        f"Margin: {result.actual_margin:+.1f} dB | Status: {result.status}"
    )

    # 遮蔽区間（地形がフレネル下辺を超える部分）
    obstructed = elev > (los - f1)
    obs_xml = ""
    for s, e in _find_obs_segments(obstructed):
        obs_xml += f"""
    <Placemark>
      <name>Obstruction</name>
      <styleUrl>#obs</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats[s:e+1], lons[s:e+1], elev[s:e+1])}
        </coordinates>
      </LineString>
    </Placemark>"""

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>{path_id}</name>
  <description>{desc_esc}</description>

  <Style id="terrain"><LineStyle><color>ff13458b</color><width>2</width></LineStyle></Style>
  <Style id="los"><LineStyle><color>{los_color}</color><width>2</width></LineStyle></Style>
  <Style id="fresnel"><LineStyle><color>80ffff00</color><width>1</width></LineStyle></Style>
  <Style id="obs"><LineStyle><color>ff0000ff</color><width>4</width></LineStyle></Style>

  <Placemark>
    <name>TX</name>
    <description>{_html.escape(f"h_tx={h_tx:.1f} m | {params.freq_mhz} MHz")}</description>
    <Point>
      <altitudeMode>absolute</altitudeMode>
      <coordinates>{params.lon_tx:.6f},{params.lat_tx:.6f},{tx_alt:.1f}</coordinates>
    </Point>
  </Placemark>
  <Placemark>
    <name>RX</name>
    <description>{_html.escape(f"h_rx={h_rx:.1f} m | {result.p_rx:.1f} dBm ({result.status})")}</description>
    <Point>
      <altitudeMode>absolute</altitudeMode>
      <coordinates>{params.lon_rx:.6f},{params.lat_rx:.6f},{rx_alt:.1f}</coordinates>
    </Point>
  </Placemark>

  <Folder>
    <name>Terrain Profile</name>
    <Placemark>
      <name>Terrain</name>
      <styleUrl>#terrain</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats, lons, elev)}
        </coordinates>
      </LineString>
    </Placemark>
  </Folder>

  <Folder>
    <name>Line of Sight</name>
    <Placemark>
      <name>LoS ({_html.escape(result.status)})</name>
      <styleUrl>#los</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats, lons, los)}
        </coordinates>
      </LineString>
    </Placemark>
  </Folder>

  <Folder>
    <name>1st Fresnel Zone</name>
    <Placemark>
      <name>Upper Boundary</name>
      <styleUrl>#fresnel</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats, lons, los + f1)}
        </coordinates>
      </LineString>
    </Placemark>
    <Placemark>
      <name>Lower Boundary</name>
      <styleUrl>#fresnel</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats, lons, los - f1)}
        </coordinates>
      </LineString>
    </Placemark>
  </Folder>

  <Folder>
    <name>Fresnel Obstruction</name>{obs_xml}
  </Folder>

</Document>
</kml>"""

    with open(os.path.join(save_dir, "path.kml"), "w", encoding="utf-8") as f:
        f.write(kml)


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
