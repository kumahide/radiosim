"""
report_common.py
================
レポート出力の共有部品（ヘッドレス）。

per-path（report_path.py）と summary（report_summary.py）が共有する A4 骨格の
CSS・自己同定ヘッダ/フッタ・縮小フィットスクリプト・HTML 文書の外枠を持つ。
レポート系 CSV のセル安全化（`csv_cell`）も、書き手が複数あるのでここに置く。
UI 知識ゼロ・副作用なし（純関数のみ＝文字列を返すだけ）。

⚠️ **断片と文書を分ける**のがこの層の設計:
  - 各レポートは「1 シート＝1 断片」（`<section class="sheet …">`）を返す純関数と、
    それを `html_document()` で包んで書き出す関数に分かれる。
  - 断片は複数を 1 文書へ連結できる（report_summary.save_report_all_html）。
  - そのため **シート固有の CSS は必ず `.sheet.path` / `.sheet.summary` へスコープ
    する**（両者が素の `.sheet` / `.page-header` を別値で上書きしていると、連結
    した瞬間に後勝ちで壊れる）。
"""

from __future__ import annotations

import html as _html
from datetime import datetime

from core import disclosure
from core import i18n
from core import version

# ============================================================
# CSV セルの安全化（B-012 / Formula Injection）
# ------------------------------------------------------------
# `=` `+` `-` `@` で始まる値は Excel/LibreOffice が**数式として解釈する**ため、
# 自由文字列（note・エラーメッセージ）をそのまま書くと、レポートを表計算で
# 開いた利用者の環境で `=HYPERLINK(...)` 等が実行され得る。タブ・改行・復帰で
# 始まる値も同じ扱い（先頭の空白を剥がしてから判定するソフトがあるため）。
#
# 対策＝先頭に `'`（アポストロフィ）を前置して「これは文字列」と明示する。
# 表計算で開いた時は `'` は表示されず、テキストエディタでは見える。
#
# ⚠️ **掛けるのはレポート系 CSV（summary.csv / scenario.csv）だけ**。
#    batch.export_csv はアプリ自身が読み戻す**交換フォーマット**なので、ここで
#    `'` を足すと再インポートで note の中身が変わる（往復が壊れる）。書き手を
#    増やすときはどちらの性質かを先に決めること。
#
# 🔑 **数値として読める値はそのまま通す**。マージンや受信レベル・感度は負値＝
#    `-` 始まりが正常で、一律に前置すると `'-93.20` になり表計算で数値として
#    読めなくなる（＝出力の意味が壊れる）。数式ではない以上、危険なのは
#    「数値に見えないのに数式記号で始まる値」だけ。この線引きにより、数値列に
#    掛かってしまっても壊れない＝書き手が判断を誤りにくい。
# ============================================================
_CSV_RISKY_PREFIX = ("=", "+", "-", "@", "\t", "\r", "\n")


def csv_cell(value) -> str:
    """CSV セルの値を数式解釈されない文字列へ整える（数値はそのまま）。"""
    text = "" if value is None else str(value)
    # ⚠️ **先頭の空白を剥がしてから判定する**。表計算ソフトは前置の空白を無視して
    # 数式として評価し得るので、`" =1+1"` を素通しすると回避されてしまう
    # （2026-07-26 Codex レビュー指摘）。前置する `'` は**元の文字列**に付ける
    # ＝空白ごと文字列として見せる（値を書き換えない）。
    head = text.lstrip()
    if not head.startswith(_CSV_RISKY_PREFIX):
        return text
    try:
        float(head)
    except ValueError:
        return "'" + text
    return text


# ============================================================
# レポート v2 ＝ A4 ドロップイン骨格（per-path / summary 共通）
# ------------------------------------------------------------
# 目的：生成 HTML を「そのまま報告書へ綴じ込める portrait A4 の確定1枚」にする。
# 画面でも A4 用紙が見える WYSIWYG（.sheet）＋ 印刷は @page A4。PDF 化は
# ゼロ依存＝ブラウザ Ctrl+P（PDF エンジンは入れない）。ブラウザ挿入の印刷
# ヘッダ/フッタは CSS で抑制できないため、自前のヘッダ/フッタを持ち、利用者は
# 印刷時「ヘッダーとフッターをオフ」にする前提とする。
# ============================================================


def a4_base_css() -> str:
    """per-path / summary が共通で使う A4 骨格スタイルを返す。

    画面（screen）では中央に A4 用紙（.sheet）を描いて WYSIWYG に、
    印刷（print）では余白を @page に委ね .sheet の装飾を外す。

    複数シートを連結した文書（report_all.html）でもそのまま効くよう、
    **シートの改ページ（break-after）もここで規定する**（単票では
    `.sheet` が 1 つしかないので無害）。
    """
    return """
/* --- A4 骨格（v2 ドロップイン） --- */
*{box-sizing:border-box}
body{font-family:Arial,sans-serif;font-size:13px}
.sheet{background:#fff}
.page-header{display:flex;justify-content:space-between;align-items:flex-end;
  border-bottom:2px solid #455a64;padding-bottom:6px;margin-bottom:12px}
.page-header .ph-title{font-size:18px;font-weight:bold;color:#222;margin:0;min-width:0}
.page-header .ph-right{text-align:right;font-size:10px;color:#888;
  white-space:nowrap;padding-left:12px}
.page-footer{margin-top:10px;padding-top:6px;border-top:1px solid #ddd;
  color:#aaa;font-size:10px;display:flex;justify-content:space-between}
/* 「この結果をどう扱うか」節（3.0a1）。**4 種のシートが同じ 1 本を引く**ので
   `.sheet.path` 等へはスコープしない（クラス名が固有＝連結しても衝突しない）。
   小さく畳んで最下部に置く＝per-path は A4 1 枚の縮小フィットの中に入るため、
   本文を押しのけない字送りにしてある。印刷で節が割れないよう break-inside:avoid。 */
.handling{margin-top:7px;padding-top:4px;border-top:1px solid #e0e6e9;
  break-inside:avoid}
.handling h4{margin:0 0 2px;font-size:9px;color:#607d8b;letter-spacing:.04em}
.handling .hd-lead{margin:0 0 2px;font-size:8px;color:#90a4ae}
/* 2 段組みにするのは**台帳の行数を食わないため**。実測（Edge --print-to-pdf・
   A4 1 枚に載る経路の数）＝節なし 30 行／1 段の節 25 行／**2 段の節 26 行**。
   開示を足した代わりに台帳が 1 枚で済まなくなる、という取り引きを薄める。
   ⚠️ それでも 4 行ぶんは食う＝26 行を超えるサーベイは 2 枚目に入る。 */
.handling ul{margin:0;padding-left:13px;font-size:8px;color:#78909c;line-height:1.35;
  column-count:2;column-gap:14px}
.handling li{break-inside:avoid}
.handling .hd-calib{margin:3px 0 0;font-size:8px;color:#b0bec5;font-style:italic}
@media screen{
  /* min-width:max-content ＝ 窓が A4 幅(210mm)より狭くても body が内容幅まで広がり、
     中央寄せシートが左へはみ出して左端が見切れる（水平スクロールで届かない）のを防ぐ。
     広い窓では width:auto がビューポート幅になり背景は従来どおり全面に出る。 */
  body{background:#e9e9e9;margin:0;padding:0;min-width:max-content}
  .sheet{width:210mm;min-height:297mm;padding:14mm;margin:10px auto;
    box-shadow:0 0 8px rgba(0,0,0,.25)}
  .no-print{display:block}
}
@media print{
  body{background:#fff;margin:0}
  .sheet{width:auto;min-height:0;padding:0;margin:0;box-shadow:none}
  @page{size:A4 portrait;margin:14mm 14mm 8mm}
  img{break-inside:avoid}
  thead{display:table-header-group}
  /* 連結文書：シートごとに改ページする（最後のシートは余白ページを作らない）。 */
  .sheet{break-after:page}
  .sheet:last-of-type{break-after:auto}
  .no-print{display:none !important}
}
"""


def page_header(title: str, project_name: str = "", report_id: str = "") -> str:
    """自己同定ヘッダ（左＝「案件名 - タイトル」の1行／右＝生成日時のみ）。

    project_name（案件名・自由文字列）が非空なら「案件名 - タイトル」、空なら
    「タイトル」のみを1行で表示する。report_id はバッチ per-path の識別子（path_id）で、
    非空ならタイトル末尾に「 — path_id」を付す＝バッチはどの経路かを残す。単一レポートは
    save_dir のタイムスタンプを ID にしないよう空で呼ぶ（露出していた不具合の修正）。版は
    フッタで自己同定するのでヘッダ右は生成日時のみ。title は翻訳済み文字列（エスケープ
    不要）、project_name / report_id はユーザー由来なのでエスケープする。
    """
    gen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    proj = _html.escape(project_name)
    title_line = f"{proj} - {title}" if proj else title
    if report_id:
        title_line += f" — {_html.escape(report_id)}"
    return (
        '<header class="page-header">'
        f'<p class="ph-title">{title_line}</p>'
        f'<div class="ph-right">{i18n.t("html_generated")}: {gen}</div>'
        '</header>'
    )


def page_footer(mode_label: str) -> str:
    """自己同定フッタ（版＋レポート種別ラベル）。

    per-path と summary で共有するため種別ラベルは引数で受ける（per-path＝個別／
    summary＝一括）。以前はバッチ固定ラベルを出力し、単一レポートのフッタまで
    「一括シミュレーション」になっていた不具合を修正。
    """
    return (
        '<footer class="page-footer">'
        f'<span>{version.APP_FULL}</span>'
        f'<span>{mode_label}</span>'
        '</footer>'
    )


def fit_to_page_script() -> str:
    """per-path シートを A4 縦1枚に収める「縮小フィット」スクリプトを返す。

    ページ読込後（画像レイアウト確定後）に本文 `.fit` の実高を測り、A4 印字域より高い
    分だけ `transform: scale()` で縮小する。収まる内容なら等倍（無変化）、はみ出す時
    だけ数 % 縮む。境界ギリギリの内容差・環境差に依らず常に1枚を保証する。

    **文書内の `.fit` を全て処理する**（querySelectorAll）＝連結文書（report_all.html）
    は per-path シートを N 枚持つため。単票では要素が 1 つなので挙動は従来どおり。

    要点は「transform は見た目だけでレイアウト高＝改ページに効かない」ことへの対策：
    親 `.fit-outer` の高さを固定し overflow:hidden する。これで親は固定高しか占有せず
    はみ出しも切られ、改ページは1頁で確定する（Chromium は流し込みコンテンツ＋zoom を
    改ページに反映しないため zoom は使わない）。

    右寄せは translateX＋scale（左上基点）で行い、右端ピッタリではなく数 px 内側に置く。
    overflow:hidden は左右もクリップするため、右端ピッタリだと右カラムの枠線・影が切れる。
    数 px のガター（右）を空けてクリップ境界の内側に収める。横の余白の大半は左に出る。

    縮小は幅を触らない一様スケール（transform-origin 左上）にする。幅を 1/scale に広げて
    横いっぱいに戻す小細工は、幅 100% の画像（断面図・地図）まで一緒に拡大して縦にも
    伸び、計測した高さと食い違ってフッタがはみ出す（クリップされる）ため使わない。
    一様スケール中は右側にわずかな余白が出るが、確実に全要素が1枚に収まる方を採る。

    肝は「縮小目標高」と「クリップ箱高」を分けること。画面 scrollHeight は印刷の実寸
    より数 mm 低く出るため、縮小は厳しめ（安全 8mm）にし、クリップ箱はそれより緩い
    （安全 1mm）に取る。こうすると印刷での縮小後コンテンツは箱に収まってクリップされず、
    箱は印字域内なので1頁で確定する。内容が元々収まる時は縮小も箱固定もせず等倍。

    印字域は @page 余白（上 14mm・下 8mm）に合わせて 297−14−8=275mm。下余白を詰めた
    ぶん縮小目標が印字域に近づき、縦の下部余白が減る。transform-origin は top right＝
    右寄せなので、縮小で生じる横余白は左側に出る。
    """
    return '''<script>
(function(){
  function fitOne(el){
    var outer=el.parentNode;
    el.style.transform="none";
    outer.style.height=""; outer.style.overflow="";
    var pxPerMm=96/25.4;
    /* フッタは .fit の外（.sheet 直下・最下部固定）へ出したので、その高さ分(約6mm)を
       印字域から引いて .fit の縮小目標/クリップ箱を決める。 */
    var target=(297-14-8-8-6)*pxPerMm;   /* 縮小目標高（印字域275−安全8mm−フッタ6mm） */
    var box=(297-14-8-1-6)*pxPerMm;      /* クリップ箱高（印字域275−安全1mm−フッタ6mm） */
    var h=el.scrollHeight;
    if(h>target){
      var s=target/h;
      var gutter=6;                    /* 右枠線・影がクリップされないための右ガター(px) */
      /* 右寄せ量＝横余白の大半を左へ。ただし僅少スケール時に負値になると左端が
         クリップされる（見切れ）ため 0 未満にしない。 */
      var tx=Math.max(0, outer.clientWidth*(1-s)-gutter);
      el.style.transformOrigin="top left";
      el.style.transform="translateX("+tx+"px) scale("+s+")";
      outer.style.height=box+"px";
      outer.style.overflow="hidden";
    }
  }
  function fit(){
    /* 連結文書は .fit を複数持つ（per-path シート×N）。全件を個別に測って縮める。 */
    var els=document.querySelectorAll(".fit");
    for(var i=0;i<els.length;i++) fitOne(els[i]);
  }
  if(document.readyState==="complete") fit();
  else window.addEventListener("load", fit);
})();
</script>'''


def html_document(doc_title: str, css: str, body: str) -> str:
    """シート断片を A4 文書として包む（<!DOCTYPE> 〜 </html>）。

    doc_title はブラウザタブ／印刷 PDF の既定ファイル名になる `<title>`。
    **エスケープ済みの文字列を渡すこと**（案件名等ユーザー由来を含むため）。
    css は a4_base_css() に続けて連結するシート固有スタイル。
    """
    return f"""<!DOCTYPE html>
<html lang="{i18n.t('html_lang')}">
<head>
<meta charset="UTF-8">
<title>{doc_title}</title>
<style>
{a4_base_css()}
{css}
</style>
</head>
<body>
{body}
</body>
</html>"""


# ============================================================
# 「この結果をどう扱うか」節（3.0a1 / ロードマップ §3.0 の 9）
# ------------------------------------------------------------
# 🔑 **存在理由＝成果物は一人歩きする**。レポートを受け取った人は、README の
# 開示も画面の但し書きも見ない。⇒ **前提と適用範囲を、帳票そのものに焼き込む。**
#
# 🔑 **物理を 1 行も足していない**＝出るのは `models.scope_notes()` が返した刻印
# だけで、範囲の数字（1 GHz・40 GHz・350 GHz・1〜6 GHz）は**式が使っている定数
# そのもの**を差し込む。⇒ *式を変えたのに開示だけ古い* が起きない。
#
# ⚠️ **5 面が同じ 1 本を引く**（per-path / 台帳 / 中継 / 条件探索 / report.txt）。
# 面ごとに書き写すと、次に足した 1 面だけ節を持たない形になる
# （→ [[feedback-user-examples-are-classes]]）。ゲート＝
# `tests/test_report.py::TestEveryArtifactFaceCarriesTheHandlingSection`。
# ============================================================


def handling_notes_html(note_keys) -> str:
    """「この結果をどう扱うか」節の HTML 断片を返す（**4 種のシート共通**）。

    字は `core.disclosure` が単一ソース（`report.txt` と同じ 1 本）。ここが持つのは
    **体裁だけ**＝節タグとクラス名（CSS は `a4_base_css` の `.handling`）。
    """
    items = "".join(
        f"<li>{_html.escape(line)}</li>"
        for line in disclosure.handling_lines(note_keys)
    )
    return (
        '<section class="handling">'
        f'<h4>{_html.escape(i18n.t("html_handling_title"))}</h4>'
        f'<p class="hd-lead">{_html.escape(i18n.t("html_handling_lead"))}</p>'
        f'<ul>{items}</ul>'
        f'<p class="hd-calib">'
        f'{_html.escape(disclosure.calibration_line())}</p>'
        '</section>'
    )
