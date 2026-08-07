"""
report_path.py
==============
per-path（1経路）の出力生成（ヘッドレス）。

地形断面 PNG・詳細レポート HTML（A4 縦1枚）・Google Earth 用 KML を書き出す。
単一シミュレーション（views/graph.py）とバッチ（batch.py）の両方が使う。
UI 知識ゼロ・副作用はファイル I/O のみ（Web 再利用のための継ぎ目を維持）。

**断片と文書の分離**（→ report_common）：`path_sheet_html()` が A4 1 シート分の
断片を返し、`save_path_html()` がそれを文書に包んで書き出す。断片は連結文書
（report_summary.save_report_all_html）が再利用する。
"""

from __future__ import annotations

import base64
import html as _html
import io
import logging
import os
from typing import TYPE_CHECKING

import numpy as np

from core import coords
from core import i18n
from core import models
from core import units
from report import mpl_fonts
from report import report_common
from report import report_map

if TYPE_CHECKING:
    from core import simulation as sim
    from report.batch import PathResult

logger = logging.getLogger("radiosim")


def save_path_visuals(pr: PathResult, coord_format: str = "dd",
                      project_name: str = "") -> "Exception | None":
    """
    PNG と HTML を保存する（バックグラウンドスレッドから呼んでよい）。

    以前は「メインスレッド必須」としていたが、save_profile_png が pyplot
    （TkAgg）ではなく Figure + FigureCanvasAgg を直接使う実装に変わった時点で
    その制約は消えている（Tk オブジェクトを一切生成しない）。所要時間の大半を
    占めるため、GUI を固めないようワーカースレッドから呼ぶこと（batch._process_one）。

    ⚠️ 描画前に mpl_fonts.apply_japanese_font() が matplotlib.rcParams
    （プロセス共有）を書き換えるので、複数パスの並列描画は不可（逐次実行前提）。
    ガード: tests/test_batch.py::TestRunBatch::test_path_rendering_is_never_parallel

    coord_format は HTML レポートの人が読む座標セルのみに効く（既定 DD）。
    project_name はレポートヘッダの案件名（自由文字列・空で従来表示）。

    生成した A4 シート断片は `pr.sheet_html` へ保持する（バッチ完了時に
    report_all.html へ連結するため＝断面 PNG/地図は base64 埋め込みなので
    ディスクを読み直さずに済む）。失敗したパスは空のまま＝連結からは落ちる
    （台帳にはエラー行として載る）。

    **成否を返す**（I-010・2026-08-03 に B-037 で実機に出た欠陥の恒久対策）＝
    成功なら `None`、失敗ならその例外。同じものを `pr.artifact_error` へも入れる
    ので、**戻り値を捨てても失敗は消えない**（`PathResult` はこの後 集計・台帳・
    画面へそのまま流れる）。以前はここで `logger.error` して黙って戻っており、
    描画が全滅しても `⚠ 0 ERR` で完走して完了ダイアログが正常に出た。

    ⚠️ **戻り値だけにしないのが要点**＝呼び出し側が受け取り忘れれば「静かに成功」
    へ戻る。記録先を結果オブジェクトに置けば、忘れようがない。
    """
    if pr.result is None or pr.terrain is None or pr.params is None:
        return None                      # 計算が無い＝そもそも成果物を作らない
    try:
        pr.sheet_html = save_profile_png(
            pr.terrain, pr.result, pr.params,
            pr.params.h_tx, pr.params.h_rx, pr.save_dir, coord_format,
            project_name, report_id=pr.row.path_id,
        )
        save_path_kml(
            pr.terrain, pr.result, pr.params,
            pr.params.h_tx, pr.params.h_rx, pr.save_dir,
        )
    except Exception as ex:
        # traceback を残す（I-010 ②）＝B-037 の診断はメッセージ 1 行しか無く、
        # 連鎖を追うのに再現実験が要った。
        logger.exception("Visual save failed for '%s': %s", pr.row.path_id, ex)
        pr.artifact_error = ex
        return ex
    return None


def save_profile_png(
    terrain:  models.TerrainProfile,
    result:   models.LinkBudgetResult,
    params:   sim.SimParams,
    h_tx:     float,
    h_rx:     float,
    save_dir: str,
    coord_format: str = "dd",
    project_name: str = "",
    memo: str = "",
    report_id: str = "",
) -> str:
    """
    地形断面 PNG をバックグラウンドスレッドから保存する。

    pyplot（TkAgg）を使わず Figure + FigureCanvasAgg を直接使うため
    メインスレッド以外から呼んでも安全。

    日本語ラベルの豆腐化を防ぐため、描画前に日本語フォントを明示適用する
    （個別グラフを開いていなくてもレポート PNG が正しく描画される）。

    続けて report.html も書き出し、**そこへ埋め込んだ A4 シート断片を返す**
    （連結文書での再利用向け。単一シミュレーションは戻り値を使わない）。
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
    fig.patch.set_facecolor("white")
    canvas = FigureCanvasAgg(fig)

    # 地形断面軸。図は A4 幅（約7inch）へ縮小表示されるため、視認性確保に
    # フォント・目盛りを大きめにする。背景は白（灰色下地は付けない）。
    ax = fig.add_axes((0.065, 0.14, 0.90, 0.74))
    ax.set_facecolor("white")
    ax.tick_params(labelsize=15)

    veg_top = elevs + params.veg_h
    # 距離軸は表示のみ m へ換算する（内部・物理式は km 据え置き＝units 参照）。
    d_m = units.km_to_m(t.d_km_axis)
    ax.fill_between(d_m, elevs,   y_min,   color="#8B4513", alpha=0.4)
    ax.fill_between(d_m, veg_top, elevs,   color="green",   alpha=0.3)

    tx_abs = float(elevs[0])  + h_tx
    rx_abs = float(elevs[-1]) + h_rx
    los    = np.linspace(tx_abs, rx_abs, N)
    f1     = models.fresnel_zone_radii(t.d_km_axis, t.horiz_dist_km, params.freq_mhz)

    ax.plot(d_m, los, color="red", linestyle="--", lw=1.5)
    ax.fill_between(d_m, los - f1, los + f1, color="cyan", alpha=0.25)
    ax.vlines(
        [0, units.km_to_m(t.horiz_dist_km)],
        [float(elevs[0]),  float(elevs[-1])],
        [tx_abs, rx_abs],
        color="black", lw=3,
    )

    ax.set_title(f"{params.freq_mhz} MHz", fontsize=19, loc="left")
    ax.set_xlabel(i18n.t("graph_dist_axis"), fontsize=17)
    ax.set_ylabel(i18n.t("graph_alt_axis"),  fontsize=17)
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
        fontsize=15,
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

    return save_path_html(terrain, result, params, h_tx, h_rx, save_dir,
                          img_b64, map_b64, coord_format, project_name, memo,
                          report_id)


def path_sheet_css() -> str:
    """per-path シート固有のスタイル（すべて `.sheet.path` へスコープ）。

    ⚠️ スコープを外さないこと：summary シートも `.sheet` / `.page-header` /
    `.cards` を別値で持つため、素のセレクタで書くと連結文書（report_all.html）で
    後勝ちの上書きが起き、どちらかのレイアウトが壊れる。
    """
    return """
/* --- per-path シート --- */
/* ヘッダ/フッタの余白を詰めて縮小フィットの余地を増やす（summary は据え置き） */
.sheet.path .page-header{padding-bottom:4px;margin-bottom:7px}
/* フッタは summary 同様に用紙最下部へ固定（.sheet を縦フレックス＋margin-top:auto）。
   .fit の外に出したので縮小フィットの transform/clip の影響を受けない。 */
.sheet.path{display:flex;flex-direction:column}
.sheet.path .page-footer{margin-top:auto;padding-top:4px}
@media print{.sheet.path{min-height:calc(297mm - 14mm - 8mm)}}
.sheet.path .report-memo{background:#f7f9fa;border:1px solid #e0e6e9;border-radius:6px;padding:5px 10px;margin-bottom:6px;font-size:11px;color:#37474f;break-inside:avoid}
.sheet.path .report-memo .rm-label{color:#90a4ae;font-weight:bold;margin-right:4px}
.sheet.path .cards{display:flex;gap:12px;margin-bottom:6px;break-inside:avoid}
.sheet.path .card{background:white;border:1px solid #eee;border-radius:8px;padding:4px 20px;box-shadow:0 1px 3px rgba(0,0,0,.12);text-align:center;min-width:100px}
.sheet.path .card .lbl{font-size:9px;color:#999;text-transform:uppercase}
.sheet.path .card .val{font-size:15px;font-weight:bold;color:#333}
.sheet.path .card.ok .val{color:#2e7d32}.sheet.path .card.ng .val{color:#c62828}
.sheet.path .graph{width:100%;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.15);margin-bottom:6px}
.sheet.path .map-note{color:#999;font-size:12px;font-style:italic;background:white;border-radius:8px;padding:10px 16px;box-shadow:0 1px 3px rgba(0,0,0,.12);margin-bottom:6px}
.sheet.path .cols{display:flex;gap:16px;margin-bottom:2px}
.sheet.path .col{flex:1;background:white;border-radius:8px;padding:8px 16px;box-shadow:0 1px 3px rgba(0,0,0,.12)}
.sheet.path .col h3{margin:0 0 5px;font-size:13px;color:#455a64;border-bottom:1px solid #eee;padding-bottom:3px}
.sheet.path table.info{border-collapse:collapse;width:100%}
.sheet.path table.info td{padding:2px 6px;border-bottom:1px solid #f0f0f0;font-size:12px}
.sheet.path table.info td:first-child{color:#888;width:50%}
.sheet.path table.info td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.sheet.path table.info td.n .u{color:#999;display:inline-block;width:2.6em;text-align:left;margin-left:6px}
"""


def path_sheet_html(
    terrain:  models.TerrainProfile,
    result:   models.LinkBudgetResult,
    params:   sim.SimParams,
    h_tx:     float,
    h_rx:     float,
    img_b64:  str,
    map_b64:  "str | None" = None,
    coord_format: str = "dd",
    project_name: str = "",
    memo: str = "",
    report_id: str = "",
) -> str:
    """per-path の A4 シート 1 枚分の断片（`<section class="sheet path">`）を返す。

    文書には包まない（→ save_path_html / report_summary.save_report_all_html）。
    report_id が非空ならシートに `id` を振る＝連結文書で台帳から `#p01` へ飛べる
    （path_id は validate_rows で `[A-Za-z0-9_-]+` に制限済み＝アンカーに使える）。

    map_b64 が None のとき（タイル取得失敗）は地図を省き注記を表示する。
    coord_format は人が読む座標セルのみに効く（"dd"|"dms"）。CSV/KML/settings は
    再読込・規格のため DD 固定。既定 DD でヘッドレス呼び出しは表示設定に非依存。
    project_name はヘッダの案件名（自由文字列・空で従来表示）。memo は自由メモ
    （非空時のみヘッダ直下に小ブロック表示）。バッチの per-path は行ごとにメモを持つ
    設計ではないので既定は空＝従来どおり非表示、単一レポートの保存時のみ使う。
    """
    tx_coords = coords.format_pair(params.lat_tx, params.lon_tx, coord_format)
    rx_coords = coords.format_pair(params.lat_rx, params.lon_rx, coord_format)
    status_cls  = "ok" if result.status == "OK" else "ng"
    model_label = i18n.t("html_model_deygout") if result.diff_method == "deygout" else i18n.t("html_model_single")
    env_label   = i18n.t(f"env_{result.env_type}")

    # A-0 アンテナ初期指向（AZ/EL）。既存データ（座標・アンテナ高・地形標高・地球
    # 曲率）から幾何計算する純関数の表示（新規入力ゼロ）。両端で別値＝AZ は大圏逆
    # 方位（単純な ±180° でない）、EL は高低差項のみ反転（曲率で双方同じだけ沈む）。
    # 曲率は calculate_terrain_profile と同一系の 4/3（models 既定）を使う。
    tx_abs   = float(terrain.raw_elevs[0])  + h_tx
    rx_abs   = float(terrain.raw_elevs[-1]) + h_rx
    dist_m   = terrain.horiz_dist_km * 1000.0
    az_tx_rx = models.bearing_deg(params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx)
    el_tx_rx = models.elevation_angle_deg(tx_abs, rx_abs, dist_m)
    az_rx_tx = models.bearing_deg(params.lat_rx, params.lon_rx, params.lat_tx, params.lon_tx)
    el_rx_tx = models.elevation_angle_deg(rx_abs, tx_abs, dist_m)

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

    # 自由メモ（非空時のみヘッダ直下に小ブロック表示）。summary と同じ体裁。
    if memo:
        memo_block = (
            f'<div class="report-memo">'
            f'<span class="rm-label">{i18n.t("html_report_memo")}</span> '
            f'{_html.escape(memo)}</div>'
        )
    else:
        memo_block = ""

    sheet_id = f' id="{report_id}"' if report_id else ""

    return f"""<section class="sheet path"{sheet_id}>
<div class="fit-outer"><div class="fit">
{report_common.page_header(i18n.t('html_path_title'), project_name, report_id)}
{memo_block}
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
      <tr><td>{i18n.t('html_slant_dist')}</td><td>{units.format_distance(result.slant_dist_km)}</td></tr>
      <tr><td>{i18n.t('html_horiz_dist')}</td><td>{units.format_distance(terrain.horiz_dist_km)}</td></tr>
      <tr><td>{i18n.t('html_aim_tx')}</td><td>{az_tx_rx:.1f}° / {el_tx_rx:+.1f}°</td></tr>
      <tr><td>{i18n.t('html_aim_rx')}</td><td>{az_rx_tx:.1f}° / {el_rx_tx:+.1f}°</td></tr>
    </table>
    <h3 style="margin-top:9px">{i18n.t('html_radio_settings')}</h3>
    <table class="info">
      <tr><td>{i18n.t('html_frequency')}</td><td>{params.freq_mhz} MHz</td></tr>
      <tr><td>{i18n.t('html_tx_power')}</td><td>{params.p_tx} dBm</td></tr>
      <tr><td>{i18n.t('html_tx_gain')}</td><td>{params.gain_tx} dBi</td></tr>
      <tr><td>{i18n.t('html_rx_gain')}</td><td>{params.gain_rx} dBi</td></tr>
    </table>
  </div>
  <div class="col">
    <h3>{i18n.t('html_link_budget')}</h3>
    <table class="info">
      <tr><td>{i18n.t('html_eirp')}</td><td class="n">{result.eirp:.2f}<span class="u">dBm</span></td></tr>
      <tr><td>{i18n.t('html_fspl')}</td><td class="n">{result.fspl:.2f}<span class="u">dB</span></td></tr>
      <tr><td>{i18n.t('html_diff_loss')}</td><td class="n">{result.diff_loss:.2f}<span class="u">dB</span></td></tr>
      <tr><td>{i18n.t('html_veg_loss')}</td><td class="n">{result.veg_loss:.2f}<span class="u">dB</span></td></tr>
      <tr><td>{i18n.t('html_env_loss')}</td><td class="n">{result.env_loss:.2f}<span class="u">dB</span></td></tr>
      <tr><td>{i18n.t('html_rain_loss')}</td><td class="n">{result.rain_loss:.2f}<span class="u">dB</span></td></tr>
      <tr><td>{i18n.t('html_gas_loss')}</td><td class="n">{result.gas_loss:.2f}<span class="u">dB</span></td></tr>
      <tr><td>{i18n.t('html_rx_ant_gain')}</td><td class="n">+{params.gain_rx:.2f}<span class="u">dBi</span></td></tr>
      <tr><td><b>{i18n.t('html_rx_level')}</b></td><td class="n"><b>{result.p_rx:.2f}<span class="u">dBm</span></b></td></tr>
      <tr><td>{i18n.t('html_threshold')}</td><td class="n">{params.sens:.2f}<span class="u">dBm</span></td></tr>
      <tr><td><b>{i18n.t('html_act_margin')}</b></td><td class="n"><b>{result.actual_margin:+.2f}<span class="u">dB</span></b></td></tr>
    </table>
    <h3 style="margin-top:9px">{i18n.t('html_environment')}</h3>
    <table class="info">
      <tr><td>{i18n.t('html_env_type')}</td><td>{env_label}</td></tr>
      <tr><td>{i18n.t('html_diff_model')}</td><td>{model_label}</td></tr>
      <tr><td>{i18n.t('html_rain_rate')}</td><td>{params.rain_rate} mm/h</td></tr>
    </table>
  </div>
</div>
</div></div>
{report_common.page_footer(i18n.t("html_single_mode"))}
</section>"""


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
    memo: str = "",
    report_id: str = "",
) -> str:
    """per-path の report.html を生成し、埋め込んだシート断片を返す。

    戻り値は連結文書（report_all.html）での再利用のため。引数の意味は
    path_sheet_html を参照。
    """
    sheet = path_sheet_html(terrain, result, params, h_tx, h_rx, img_b64,
                            map_b64, coord_format, project_name, memo, report_id)

    # ブラウザタブ／印刷 PDF の既定ファイル名になる <title>。ヘッダと同じ
    # 「案件名 - タイトル（バッチは — path_id）」にする＝単一レポートで save_dir の
    # タイムスタンプを露出させない。
    _doc_title = i18n.t("html_path_title")
    if project_name:
        _doc_title = f"{project_name} - {_doc_title}"
    if report_id:
        _doc_title = f"{_doc_title} — {report_id}"

    html = report_common.html_document(
        _html.escape(_doc_title),
        path_sheet_css(),
        sheet + "\n" + report_common.fit_to_page_script(),
    )
    with open(os.path.join(save_dir, "report.html"), "w", encoding="utf-8") as f:
        f.write(html)
    return sheet


# ============================================================
# KML 出力（per-path）
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
