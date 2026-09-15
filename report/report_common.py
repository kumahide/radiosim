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
import re
from datetime import datetime

from core import disclosure
from core import i18n
from core import models
from core import residuals as core_residuals
from core import sensitivity as core_sensitivity
from core import units
from core import version

# 帳票へ width:100% で並べる横長図の縦横比（matplotlib の figsize と同じ〔幅, 高さ〕）。
# 🔑 **断面図（report_path）と経路地図（report_map）が同じ 1 本を引く**＝2 枚は上下に
# 並ぶので、比が違うと高さが揃わない。⚠️ **これは「絵の好み」ではなく A4 1 枚の縦の
# 予算**＝15:6 だった頃は図 2 枚で本文の約半分を食い、既定の内容でも縮小フィットが
# 0.82 倍で常時発火していた（2026-08-28 に実測して 15:4.5 へ）。
PROFILE_FIGSIZE: tuple[float, float] = (15.0, 4.5)

# ============================================================
# 値と単位を同じ行に留める（B-242）
# ------------------------------------------------------------
# `core.units.format_*` は値と単位を**半角空白**で繋ぐ（`7,440 m` / `0.0 %`）。
# HTML ではそこが折り返し可能点になり、帯や箇条が 1px でも印字幅を超えると
# **単位だけが次の行へ落ちる**（3.4RC1 の条件探索レポートで実際に起きた）。
# ⇒ 帳票の自由に折り返す字だけ、その空白を改行しない空白（U+00A0）へ替える。
# ⚠️ **`units` 側は変えない**＝同じ関数を画面（Tk）と report.txt も引くので、
# 帳票の組版の都合をそちらへ持ち込まない。表のセル（`td`）は既定で
# `white-space:nowrap` なので通す必要はない。
# ============================================================
NBSP = " "
_UNIT_GAP = re.compile(
    r"(?<=\d) (?=(?:%|°|" + re.escape(units.F1_DEPTH_UNIT)
    + r"|dBm|dBi|dB|GHz|MHz|kHz|Hz|km|mm/h|m)(?![A-Za-z]))"
)


def keep_unit_with_value(text: str) -> str:
    """数値の直後の半角空白のうち、単位の手前にあるものを U+00A0 に替える。

    折り返しそのものは止めない（`white-space` は触らない）＝**値と単位が別の行に
    分かれることだけ**を防ぐ。エスケープ前の素の文字列に掛ける（U+00A0 は
    `html.escape` を素通りする）。
    """
    return _UNIT_GAP.sub(NBSP, text)

# ============================================================
# 図に焼く字の大きさ（B-135）
# ------------------------------------------------------------
# 🔴 **図の中の px は、そのままの大きさでは読めない。** 帳票の図は width:100% で
# A4 の印字幅へ縮めて載るので、**縮小率のぶんだけ字も縮む**。しかも図ごとに解像度が
# 違う（地図 975〜1140px ／ 断面図 2250px）ので、**px や pt を直接書くと実寸が図ごと
# にバラバラ**になる＝実測で 5.7〜7.8px まで落ち、帳票の最小字（開示節の 8px）を
# どれも下回っていた。⇒ **図の幅を基準に、載せた後の大きさから逆算する。**
#
# ⚠️ **縮小フィット（per-path・最大 0.82 倍）は勘定に入れない**＝比べる相手である
# 開示節（8px）も**同じ `.fit` の中にあって同率で縮む**ので、両者の比は変わらない。
# ⇒ 比較は縮小前の値どうしで行う（2026-08-28 に 0.82 を掛けて比べたのは誤り）。
# ============================================================
#: A4（210mm）から左右の余白（14mm×2）を引いた印字幅を CSS px（96dpi）に直した値。
A4_CONTENT_WIDTH_PX = 182 / 25.4 * 96
#: 帳票に載った後に残ってほしい字の大きさ [CSS px]。
#: ⚠️ **下限は開示節の 8px**＝帳票でいちばん小さい字。出典はそれ以上でなければ
#: 「書いてあるが読めない」に戻る。9px は**そのすぐ上**＝控えめだが読める大きさで、
#: 2026-08-28 にユーザーが 10px から下げる判断をした（「もう少し小さく」）。
MIN_FIGURE_TEXT_PX = 9.0


def figure_text_px(figure_width_px: float) -> float:
    """図の中に焼く字の大きさ [px] を、**その図の幅から**決める。

    🔑 **図を作る側が「A4 でどう縮むか」を知らずに済む**のが要点＝渡すのは自分の
    幅だけで、載せた後の大きさはここが受け持つ。⇒ 図の解像度を変えた日に、
    字の大きさを直し忘れて読めなくなることが起きない。
    """
    return MIN_FIGURE_TEXT_PX * figure_width_px / A4_CONTENT_WIDTH_PX


def figure_text_pt(figure_width_px: float, dpi: float) -> float:
    """同じものを matplotlib の pt で返す（`fontsize=` に渡す）。"""
    return figure_text_px(figure_width_px) * 72.0 / dpi

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
  color:#aaa;font-size:10px;display:flex;justify-content:space-between;
  break-before:avoid}
/* ⚠️ `break-before:avoid`（B-212）＝直前の `.handling`（break-after:avoid と対）と
   フッタを1単位として糊付けする。糊付けしないと、本文が用紙にほぼぴったり収まる
   件数のとき、フッタだけが次ページへ落ち「フッタだけの白紙ページ」になる
   （Chromium はフッタを分割不可の1ブロックとして扱い、残りわずかな余白に
   収まらなければ丸ごと次ページへ送るため）。糊付け後は収まらない側（直前の
   `.handling` ごと）が次ページへ回り、白紙ページは出ない。 */
/* 「結果の取扱に関する補足」節（3.0a1）。**4 種のシートが同じ 1 本を引く**ので
   `.sheet.path` 等へはスコープしない（クラス名が固有＝連結しても衝突しない）。
   小さく畳んで最下部に置く＝per-path は A4 1 枚の縮小フィットの中に入るため、
   本文を押しのけない字送りにしてある。印刷で節が割れないよう break-inside:avoid。 */
.handling{margin-top:7px;padding-top:4px;border-top:1px solid #e0e6e9;
  break-inside:avoid;break-after:avoid}
.handling h4{margin:0 0 2px;font-size:9px;color:#607d8b;letter-spacing:.04em}
.handling .hd-lead{margin:0 0 2px;font-size:8px;color:#90a4ae}
/* 2 段組みにするのは**台帳の行数を食わないため**。開示を足した代わりに台帳が
   1 枚で済まなくなる、という取り引きを薄める。
   ⚠️ **ここに「N 行入る」と書かない**（2026-09-08 に書き直し）＝実測すると
   条件で大きく動く。3.2RC4 の値（サムネイル無し・素の台帳）は地図あり 日 25 /
   英 30 行・地図なし 日 32 / 英 38 行だった。**3.3 で台帳にサムネイルを戻して
   行が高くなり**、3.3RC1 で同じく Edge --print-to-pdf・製品の save_summary_html
   を N 行で呼んで測り直した値は:
       俯瞰地図あり（＝batch.py の実運用経路）… 8〜9 行（日英とも・注記の量で 1 行動く）
       俯瞰地図なし（地図の取得に失敗した回）… 14 行（日英とも）
   刻印の数は条件（回折モデル・降雨・植生・解像度）で変わる。⇒ 固定値を註や
   文書に書くと必ず古くなる（3.3 では文書だけ先に「10〜15 行」へ書き換えて外した）。
   公開文書には範囲と「条件で前後する」ことだけを書いてある。 */
.handling ul{margin:0;padding-left:13px;font-size:8px;color:#78909c;line-height:1.35;
  column-count:2;column-gap:14px}
.handling li{break-inside:avoid}
.handling .hd-calib{margin:3px 0 0;font-size:8px;color:#b0bec5;font-style:italic}
/* 出典は事実の刻印なので、較正の席（斜体・淡色＝空席の印）とは分けて素の字で置く。 */
.handling .hd-source{margin:2px 0 0;font-size:8px;color:#78909c}
/* 感度の変動幅（3.4 段6 / B-219 で `.handling` の内側の子節へ統合）＝
   見出し「机上のスクリーニング推定」を二重に出さないため、独立した
   `<section>` ではなく `.handling` の末尾（出典の後）に置く `<div>` にした。
   骨格は `.handling` に揃えるが、見出しは 1 段軽い `<h5>`（子節の印）。 */
.sensitivity{margin-top:5px;padding-top:3px;border-top:1px dashed #e0e6e9}
.sensitivity h5{margin:0 0 2px;font-size:8px;color:#607d8b;letter-spacing:.03em;
  font-weight:bold}
.sensitivity .sn-lead{margin:0 0 2px;font-size:8px;color:#90a4ae}
.sensitivity table{width:100%;border-collapse:collapse;margin-top:1px}
.sensitivity th,.sensitivity td{font-size:8px;padding:1px 5px;text-align:right;
  border-bottom:1px solid #eef2f3;white-space:nowrap}
.sensitivity th:first-child,.sensitivity td:first-child{text-align:left;
  white-space:normal}
.sensitivity th{color:#78909c;font-weight:normal}
.sensitivity .sn-note,.sensitivity .sn-unchanged,.sensitivity .sn-ground-note{
  margin:3px 0 0;font-size:8px;color:#78909c}
/* 実測残差の層別表（3.4 段6）＝標本が 1 件も無いバッチには出さない
   （呼び出し側が空なら渡さない＝`residuals_table_html` は空文字を返す）。
   骨格は `.sensitivity` と揃える（同じ「補足」の仲間）。 */
.residuals{margin-top:7px;padding-top:4px;border-top:1px solid #e0e6e9;
  break-inside:avoid}
.residuals h4{margin:0 0 2px;font-size:9px;color:#607d8b;letter-spacing:.04em}
.residuals .rs-lead{margin:0 0 2px;font-size:8px;color:#90a4ae}
.residuals table{width:100%;border-collapse:collapse;margin-top:1px}
.residuals th,.residuals td{font-size:8px;padding:1px 5px;text-align:right;
  border-bottom:1px solid #eef2f3;white-space:nowrap}
.residuals th:first-child,.residuals td:first-child{text-align:left;
  white-space:normal}
.residuals th{color:#78909c;font-weight:normal}
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


def dem_fail_notice_html(entries: "list[tuple[str, float]]") -> str:
    """DEM 取得に失敗した標本を含む経路（区間）の注記を返す（空なら空文字）。

    I-143 決定 2＝DEM 失敗率は台帳の列でなく**判定セルの ⚠ ＋台帳下の 1 行**で
    出す（列を落としても信号を消さないため＝3.2 で入れた失敗率の開示・B-025 ③）。
    entries は `(表示名, fail_pct)` の失敗率が 0 より大きいものだけを渡すこと。
    """
    if not entries:
        return ""
    items = "、".join(
        f"{_html.escape(name)}（{units.format_fail_pct(pct)}）"
        for name, pct in entries
    )
    return (
        f'<p class="dem-fail-note">{_html.escape(i18n.t("html_dem_fail_notice"))}'
        f'{items}</p>'
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
    より数 mm 低く出るため、縮小は厳しめ（安全 15mm）にし、クリップ箱はそれより緩い
    （安全 4mm）に取る。こうすると印刷での縮小後コンテンツは箱に収まってクリップされず、
    箱は印字域内なので1頁で確定する。内容が元々収まる時は縮小も箱固定もせず等倍。

    ⚠️ **安全マージンは 2026-09-13 に 8mm/1mm → 15mm/4mm へ拡大した（B-223）**＝
    断面図PNG・地図PNGを実際に埋め込む本番経路（`report_path.save_path_visuals`）で
    測ると、旧の 8mm では画面と印刷のズレを吸収しきれず、`.fit-outer`
    （固定高＋overflow:hidden＝分割不可の1ブロック）がヘッダ直後の残り余白に
    収まらず**丸ごと次ページへ送られ、1枚目がヘッダだけの白紙になっていた**
    （空 `img_b64=""` の合成条件だけで検証していたときは画像の高さぶんが
    無く縮小が発生しなかったため未検出＝[[feedback_synthetic_cases_lie]]）。
    Edge `--print-to-pdf` で実像入りの複数リンクを掃引し、8mm 刻みでは 12mm から
    1頁に収まったが、環境差を吸収する余地を見て 15mm/4mm を採用した。

    印字域は @page 余白（上 14mm・下 8mm）に合わせて 297−14−8=275mm。下余白を詰めた
    ぶん縮小目標が印字域に近づき、縦の下部余白が減る。transform-origin は top right＝
    右寄せなので、縮小で生じる横余白は左側に出る。

    🔑 **ヘッダとフッタは縮小対象の外**（どちらも `.sheet` 直下＝`.fit` の外）。
    自己同定の字が内容量しだいで縮むと、シートごとに題字の大きさが変わって読みにくい。
    ⇒ 2 つの**実高を測って**印字域から引き、残りを `.fit` の持ち分にする（決め打ちの
    mm を書かない＝余白やフォントを変えた日に目標だけ古くならない）。
    """
    return '''<script>
(function(){
  function px(v){ var n=parseFloat(v); return isFinite(n)?n:0; }
  function chromeH(sheet){
    /* .fit の外に置いたヘッダ/フッタの合計高＝縮小されない持ち分。
       ⛔ **フッタの margin-top は数えない**＝`margin-top:auto` は用紙の余りを
       そのまま吸う値で、getComputedStyle は "auto" ではなく**使われた px** を返す。
       これを足すと「中身が短いほど chrome が大きい」＝目標高が中身の高さに
       追従してしまい、収まっている紙でも縮小が始まる（2026-08-28 に実測で発覚）。
       ⇒ 数えるのは**枠そのものの高さと、余りではない側の余白だけ**。 */
    var h=0, kids=sheet?sheet.children:[];
    for(var i=0;i<kids.length;i++){
      var el=kids[i], c=""+(el.className||"");
      var isHead=c.indexOf("page-header")>=0, isFoot=c.indexOf("page-footer")>=0;
      if(!isHead&&!isFoot) continue;
      var cs=getComputedStyle(el);
      h+=el.getBoundingClientRect().height+px(cs.marginBottom);
      if(isHead) h+=px(cs.marginTop);   /* ヘッダの上余白は実寸（auto ではない） */
    }
    return h;
  }
  function fitOne(el){
    var outer=el.parentNode;
    el.style.transform="none";
    outer.style.height=""; outer.style.overflow="";
    var pxPerMm=96/25.4;
    var chrome=chromeH(outer.parentNode);
    var target=(297-14-8-15)*pxPerMm-chrome; /* 縮小目標高（印字域275−安全15mm−ヘッダ/フッタ） */
    var box=(297-14-8-4)*pxPerMm-chrome;     /* クリップ箱高（印字域275−安全4mm−ヘッダ/フッタ） */
    var h=el.scrollHeight;
    if(h>target){
      var s=target/h;
      var gutter=6;                    /* 左右の枠線・影がクリップされないためのガター(px) */
      /* 横余白を左右へ振り分ける。右には gutter 分を確保しつつ、左にも同じ
         gutter 分を保証する（旧実装は左を「余った分」任せで 0 に丸めており、
         僅少スケール時に box-shadow の左への張り出しが切れていた＝B-146）。
         余白が両ガター分に満たない極端な場合は均等に割って対称にする
         （そのときは s がほぼ1＝影の張り出しも元々小さいので対称配分で足りる）。 */
      var slack=outer.clientWidth*(1-s);
      var tx=slack>2*gutter ? slack-gutter : slack/2;
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
# 台帳の見せ方（B-207 / B-208）
# ------------------------------------------------------------
# 🔴 **バッチの台帳と中継の台帳が CSS を別々に書いていた**＝同じ薄黄が、バッチでは
# 「NG」、中継では「最も苦しい区間」（中身は OK）を意味していた（B-207）。縦罫線と
# 判定の中央寄せも片方にしか無かった（B-208）。どちらも*片方だけ直した*跡で、
# 突き合わせる仕組みが無かったことが原因。⇒ **表の骨格と判定の色はここから配る**。
# シート固有の CSS に残すのは、そのシートにしか無い列（ID・区間名・サムネイル）だけ。
# ============================================================
#: 判定ごとの行の地の色（字の色は `map_graphics.STATUS_HEX`）。**判定以外の意味で
#: 行の地を塗らない**＝行を目立たせたいときは地の色でなく罫線などの別の手段を使う。
VERDICT_ROW_BG = {"OK": "#f1f8e9", "NG": "#fff8e1", "ERROR": "#fce4ec"}
#: 判定の語 → CSS クラス（`batch.PathResult.status` の 3 語）。
VERDICT_CLASS = {"OK": "ok", "NG": "ng", "ERROR": "err"}


def verdict_class(status: str) -> str:
    """判定の語（`OK` / `NG` / `ERROR`）→ CSS クラス（`ok` / `ng` / `err`）。

    知らない語は `err`（判定できないものを OK / NG の色で塗らない＝B-071）。
    """
    return VERDICT_CLASS.get(status, "err")


def verdict_css(sheet: str) -> str:
    """判定の色（行の地・判定の字・件数カードの数字）を `.sheet.<sheet>` へ配る。

    🔑 **3 つの帳票（バッチ・中継・条件探索）が同じ 1 本を引く**＝同じ色は同じ
    意味（[[feedback_design_philosophy]] ⑧）。字の色は地図の線と同じ
    `map_graphics.STATUS_HEX`（ERROR が 2 色あった＝B-207）。
    """
    from report import map_graphics      # PIL を読むのは使うときだけ
    s = f".sheet.{sheet}"
    rules = []
    for status, cls in VERDICT_CLASS.items():
        color = map_graphics.STATUS_HEX[status]
        rules.append(f"{s} tr.{cls}{{background:{VERDICT_ROW_BG[status]}}}")
        rules.append(f"{s} .s-{cls}{{color:{color};font-weight:bold}}")
        rules.append(f"{s} .card.{cls} .val{{color:{color}}}")
    return "\n" + "\n".join(rules) + "\n"


def ledger_table_css(sheet: str, table: str) -> str:
    """台帳（1 行 1 経路・1 区間の表）の骨格を `.sheet.<sheet> table.<table>` へ配る。

    列の幅の決まり方（B-208）:
      - **既定の列は中身の幅ぴったり**（`th` の `width:1px`＝`table-layout:auto` では
        「指定幅と最小幅の大きいほう」になり、`nowrap` の数値列は中身の幅に止まる）。
      - **余りを受け取るのは `th.c-flex` の列だけ**（ID・区間名・グラフ＝幅を融通
        できる列）。以前は余り（約 240px）が**全列へ中身の幅に比例して**配られ、
        見出しが長く値が短いアンテナ高の列だけ値の左に 70〜80px の空きができた。
      - 左右の余白は 6px（条件探索の表と同じ）。2px は 22 列時代（B-187）の値で、
        9 列になってからは値が隣の罫線に貼りつくだけだった。
    ⚠️ **印字域（182mm）に収まるかは Edge で実測して決めた**（この表は B-145・
    B-155・B-187 で 3 度はみ出している）。余白を広げるときは測り直すこと。
    """
    t = f".sheet.{sheet} table.{table}"
    return f"""
{t}{{border-collapse:collapse;width:100%;table-layout:auto;background:white;
  box-shadow:0 1px 3px rgba(0,0,0,.12)}}
{t} th{{background:#455a64;color:white;padding:4px 6px;text-align:center;
  vertical-align:bottom;font-size:8px;white-space:nowrap;line-height:1.2;
  border-right:1px solid rgba(255,255,255,.22);width:1px}}
{t} th.c-flex{{width:auto}}
{t} th .u{{display:block;font-size:7px;font-weight:normal;opacity:.8}}
{t} td{{padding:4px 6px;border-bottom:1px solid #eee;border-right:1px solid #e6e6e6;
  font-size:9px;text-align:right;white-space:nowrap}}
{t} th:last-child,{t} td:last-child{{border-right:none}}
{t} td.c-status{{text-align:center}}
{t} td.c-reason{{text-align:left;white-space:normal;word-break:normal;overflow-wrap:anywhere}}
{t} td.c-missing{{white-space:normal;word-break:normal;overflow-wrap:anywhere}}
{t} tr{{break-inside:avoid}}
"""


def ledger_header_cells(labels, flex_keys=frozenset()) -> str:
    """台帳の `<th>` 群を返す（`labels` は `(i18n キー, 訳した文言)` の列）。

    ⚠️ **訳は呼び出し側で引く**＝`tests/test_i18n_external.py` は `t()` の引数を
    *モジュール定数を回すループ*までしか辿れないので、ここで `i18n.t(key)` を呼ぶと
    列のキーが外部訳の検査から消える（列の定数は各帳票のモジュールにある）。

    "受信レベル (dBm)" のように "名前 (単位)" 形式のヘッダは、単位を `.u`（改行＋
    小さめ）で 2 行目に置く。これで各ヘッダの必要幅が max(名前, 単位) に縮み、
    列が横に広がりにくい。**アンテナ高も同じ形**（`mh_heights`＝"アンテナ高 (送 / 受, m)"）。
    🔁 B-208 までは文言が "アンテナ高（送 / 受）" で、全角の括弧がこの規則に拾われず
    1 行目に残り、**この見出しだけが値の約 2 倍の幅で列を決めていた**（単位 "(m)" は
    ここで補っており、英語は汎用の規則に拾われて "(m)" が落ちていた）。
    `flex_keys` の列は余りの幅を受け取る（`ledger_table_css` の `th.c-flex`）。
    """
    cells = []
    for key, label in labels:
        attr = ' class="c-flex"' if key in flex_keys else ""
        if label.endswith(")") and " (" in label:
            name, unit = label.split(" (", 1)
            cells.append(f'<th{attr}>{name}<span class="u">({unit}</span></th>')
        else:
            cells.append(f"<th{attr}>{label}</th>")
    return "".join(cells)


# ============================================================
# 「結果の取扱に関する補足」節（3.0a1 / ロードマップ §3.0 の 9）
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


# ============================================================
# 感度の変動幅（3.4 段6 / B-219 で「結果の取扱に関する補足」へ統合）
# ------------------------------------------------------------
# 🔑 **「無い」と書いた地面反射の行を「幅」に置き換える**のがこの節の存在理由
# （ロードマップ 3.4 段4 の注記）。**単独で出さない**＝地面反射の幅だけを図に
# 描くと「それ以外は正確」という含意が生まれるので、他の摂動軸（DEM 標高・
# 植生高・環境区分・回折モデル・回折+植生の合成・解像度）と**同じ表の 1 行**
# として並べる。値は `core.sensitivity.compute_link_sensitivity` が既存の
# 計算パイプラインを摂動条件で数回まわしただけ（モデルは 1 行も変えない）。
#
# 🔴 **B-219＝見出しと感度表を「結果の取扱に関する補足」の 1 節へ統合した**
# （2026-09-13）。理由＝同じ前提（回折モデル・回折+植生の合成・解像度・地面反射）
# を disclosure の箇条と感度表の行が二重に語り、per-path で本文が約 0.87 倍に
# 縮んで最小字（8px）を割った（実測）。あわせて表は基準列を外し「変動幅」1列へ、
# 変化しない軸（低め=高め=基準）はまとめて 1 行の注記にする。
#
# ⚠️ **disclosure 側の扱いは面によって違う**（据え置き）:
#   - **per-path**（1 本の回線だけを見る面）＝該当する摂動軸が求まったら、
#     `core.models.scope_notes()` の対応する刻印（`ground_reflection` /
#     `diff_bullington` / `diff_veg_serial`）を disclosure から外し、感度表の
#     1 行に**置き換える**（→ report_path.py）。求まらないもの（地面反射の
#     envelope が `None` 等）は disclosure の言葉をそのまま残す。
#   - **scenario / multihop**（N 本の条件・区間の和集合で disclosure を出す面）
#     ＝この表は**そのうちの 1 本（ベース条件／ワースト区間）だけ**の参考値
#     なので、disclosure は**外さない**（他の N-1 本には envelope が無い＝
#     置き換えると「全部に効いた」という誤った含意になる）。`sens_note=` 引数で
#     「この表はどの 1 本の値か」を明示する。
# ============================================================

#: 表示桁（`units.format_db` の 0.1dB 刻み）で同じ値に丸まる差は「変化なし」＝
#: 表の行を増やさずまとめる（B-219）。
_SENS_UNCHANGED_EPS_DB = 0.05


def _axis_is_unchanged(low: float, high: float) -> bool:
    return abs(float(high) - float(low)) < _SENS_UNCHANGED_EPS_DB


def _sensitivity_range_row(label: str, low: float, high: float) -> str:
    return (
        f"<tr><td>{_html.escape(keep_unit_with_value(label))}</td>"
        f"<td>{units.format_db(low, signed=True)} 〜 "
        f"{units.format_db(high, signed=True)}</td></tr>"
    )

#: 表に出す順（先頭が i18n キー＝`_compare_table` の `_COMPARE_ROWS` と同じ形。
#: `tests/test_i18n_external.py` はこの「先頭がキー」の並びをループ変数越しに
#: 読み解く＝キーを直書きせず定数へ括り出しても締め出しの網から漏れない）。
#: 型は `tuple[tuple[str, str], ...]`（先頭が i18n キー・次が `SensitivityResult.axes`
#: の辞書キー）。⚠️ **型注釈付き代入（`x: T = ...`）にしないこと**＝
#: `tests/test_i18n_external.py` の走査は素の `ast.Assign` しかモジュール定数として
#: 拾えない（`ast.AnnAssign` は対象外）＝注釈を付けた瞬間にこの定数が「読み解けない
#: 呼び方」として落ちる（実装時に実際に踏んだ）。
_SENSITIVITY_AXES = (
    ("html_sens_axis_dem_elev",         "dem_elev"),
    ("html_sens_axis_veg_h",            "veg_h"),
    ("html_sens_axis_env_type",         "env_type"),
    ("html_sens_axis_diff_method",      "diff_method"),
    ("html_sens_axis_diff_veg_compose", "diff_veg_compose"),
)


def sensitivity_axis_label(i18n_key: str) -> str:
    """感度表の軸ラベルを返す（DEM・植生高だけ摂動量を差し込む）。

    摂動量は `core.sensitivity` の定数が単一ソース＝値を変えた日に表の文言
    だけ古くなることがない（`core.disclosure._scope_args` と同じ考え方）。
    公開関数＝`report_multihop.py` の argmin 注記も同じラベルを引く（軸名の
    字が表と注記で食い違わないように）。
    """
    text = i18n.t(i18n_key)
    if i18n_key == "html_sens_axis_dem_elev":
        return text.format(m=f"{core_sensitivity.DEM_PERTURB_M:g}")
    if i18n_key == "html_sens_axis_veg_h":
        return text.format(pct=f"{core_sensitivity.VEG_PERTURB_FRAC * 100:g}")
    return text


def _sensitivity_row(label: str, baseline: float, low: float, high: float) -> str:
    return (
        f"<tr><td>{_html.escape(label)}</td>"
        f"<td>{units.format_db(baseline, signed=True)}</td>"
        f"<td>{units.format_db(low, signed=True)}</td>"
        f"<td>{units.format_db(high, signed=True)}</td></tr>"
    )


def _sensitivity_inner_html(
    sens: "core_sensitivity.SensitivityResult | None", sens_note: str,
) -> str:
    """感度の変動幅（見出し・導入文・表・注記）の内側 HTML（`<div>` 1 個・空なら空文字）。

    `handling_section_html` からだけ呼ぶ（節タグ・見出しの重複回避は呼び出し側の責務）。
    """
    if sens is None:
        return ""
    rows: "list[str]" = []
    unchanged: "list[str]" = []
    for i18n_key, axis_key in _SENSITIVITY_AXES:
        axis = sens.axes.get(axis_key)
        if axis is None:
            continue
        label = sensitivity_axis_label(i18n_key)
        if _axis_is_unchanged(axis.low, axis.high):
            unchanged.append(label)
        else:
            rows.append(_sensitivity_range_row(label, axis.low, axis.high))
    if sens.resolution is not None:
        label = i18n.t("html_sens_axis_resolution")
        if _axis_is_unchanged(sens.resolution.low, sens.resolution.high):
            unchanged.append(label)
        else:
            rows.append(_sensitivity_range_row(
                label, sens.resolution.low, sens.resolution.high,
            ))
    ground_note_html = ""
    if sens.ground_reflection is not None:
        env = sens.ground_reflection
        rows.append(_sensitivity_range_row(
            i18n.t("html_sens_axis_ground_reflection"),
            sens.baseline_margin - env.null_depth_db,
            sens.baseline_margin + env.constructive_gain_db,
        ))
        ground_note_html = (
            f'<p class="sn-ground-note">'
            f'{_html.escape(i18n.t("html_sens_ground_reflection_note"))}</p>'
        )
    if not rows and not unchanged:
        return ""

    lead = keep_unit_with_value(i18n.t("html_sensitivity_lead").format(
        baseline=units.format_db(sens.baseline_margin, signed=True, unit="dB"),
    ))
    table_html = ""
    if rows:
        table_html = (
            '<table><thead><tr>'
            f'<th>{_html.escape(i18n.t("html_sens_col_axis"))}</th>'
            f'<th>{_html.escape(i18n.t("html_sens_col_range"))}</th>'
            '</tr></thead><tbody>'
            + "".join(rows) +
            '</tbody></table>'
        )
    unchanged_html = (
        f'<p class="sn-unchanged">'
        f'{_html.escape(keep_unit_with_value(i18n.t("html_sens_unchanged").format(list="、".join(unchanged))))}'
        f'</p>' if unchanged else ""
    )
    note_html = (
        f'<p class="sn-note">{_html.escape(keep_unit_with_value(sens_note))}</p>'
        if sens_note else ""
    )
    return (
        '<div class="sensitivity">'
        f'<h5>{_html.escape(i18n.t("html_sensitivity_title"))}</h5>'
        f'<p class="sn-lead">{_html.escape(lead)}</p>'
        + table_html + unchanged_html + ground_note_html + note_html +
        '</div>'
    )


def handling_section_html(
    note_keys,
    sens: "core_sensitivity.SensitivityResult | None" = None,
    *,
    sens_note: str = "",
    dem_source_ids=None,
) -> str:
    """「結果の取扱に関する補足」節の HTML 断片を返す（**4 種のシート共通**）。

    B-219（2026-09-13）で、感度の変動幅（`sens`）をこの節の中へ統合した
    （見出し「机上のスクリーニング推定」が二重に出ないように・本文が縮んで
    最小字を割っていたのを解消）。

    Args:
        note_keys: `core.models.scope_notes()` 等が返す disclosure の刻印キー列。
        sens: `core.sensitivity.compute_link_sensitivity` の戻り値。`None` なら
            この面はまだ感度を計算していない（表は出ない）。
        sens_note: 感度表の下に添える 1 行。**scenario / multihop は必ず渡す**＝
            この表が N 本のうちどの 1 本の値かを明示する（モジュール docstring）。
        dem_source_ids: この面が実際に使った DEM ソースの `source_id`（単一文字列
            または反復可能）。**渡さないと国土地理院と表示される**（B-216 で
            固定文言だった名残の既定値）＝呼び出し側は必ず渡す。
    """
    items = "".join(
        f"<li>{_html.escape(keep_unit_with_value(line))}</li>"
        for line in disclosure.handling_lines(note_keys)
    )
    return (
        '<section class="handling">'
        f'<h4>{_html.escape(i18n.t("html_handling_title"))}</h4>'
        f'<ul>{items}</ul>'
        f'<p class="hd-calib">'
        f'{_html.escape(keep_unit_with_value(disclosure.calibration_line()))}</p>'
        f'<p class="hd-source">'
        f'{_html.escape(disclosure.data_source_line(dem_source_ids))}</p>'
        + _sensitivity_inner_html(sens, sens_note) +
        '</section>'
    )


# ============================================================
# 実測残差の層別表（3.4 段6 / ロードマップ §3.4）
# ------------------------------------------------------------
# バッチに実測値（`meas_dbm`）が 1 行でも入っていれば、環境区分×帯域×距離帯
# ごとの中央値・ばらつき（IQR）・件数を表で示す。計算は
# `core.residuals.compute_layered_stats`（モデルは変えない・測るだけ）。
# 標本が 0 件（実測値を誰も入力していないバッチ）なら**何も表示しない**
# （空表を出さない＝呼び出し側が空リストのときは空文字を返す）。
# ============================================================

#: `env_class` が名乗る値のうち、i18n の `env_*` 訳を持つもの（ランチャーの
#: 選択肢＝`models.ENV_KEYS` と、空欄の正規化値＝`UNSPECIFIED_LABEL`）。
#: それ以外（利用者が CSV に書いた自由記述）は訳さずそのまま表示する。
_ENV_CLASS_LABEL_KEYS = frozenset(models.ENV_KEYS) | {core_residuals.UNSPECIFIED_LABEL}


def _env_class_label(value: str) -> str:
    """`env_class` の表示名（ランチャーと同じ訳・未知の自由記述はそのまま）。"""
    if value in _ENV_CLASS_LABEL_KEYS:
        return i18n.t(f"env_{value}")
    return value


def residuals_table_html(stats: "list[core_residuals.LayerStats]") -> str:
    """残差の層別表の HTML 断片を返す（`stats` が空なら空文字）。"""
    if not stats:
        return ""
    rows = "".join(
        f"<tr><td>{_html.escape(_env_class_label(s.env_class))}</td>"
        f"<td>{_html.escape(s.band)}</td>"
        f"<td>{_html.escape(s.distance_band)}</td>"
        f"<td>{s.n}</td>"
        f"<td>{units.format_db(s.median_db, signed=True)}</td>"
        f"<td>{units.format_db(s.iqr_db)}</td></tr>"
        for s in stats
    )
    return (
        '<section class="residuals">'
        f'<h4>{_html.escape(i18n.t("html_residuals_title"))}</h4>'
        f'<p class="rs-lead">{_html.escape(i18n.t("html_residuals_lead"))}</p>'
        '<table><thead><tr>'
        f'<th>{_html.escape(i18n.t("html_residuals_col_env"))}</th>'
        f'<th>{_html.escape(i18n.t("html_residuals_col_band"))}</th>'
        f'<th>{_html.escape(i18n.t("html_residuals_col_distance"))}</th>'
        f'<th>{_html.escape(i18n.t("html_residuals_col_n"))}</th>'
        f'<th>{_html.escape(i18n.t("html_residuals_col_median"))}</th>'
        f'<th>{_html.escape(i18n.t("html_residuals_col_iqr"))}</th>'
        '</tr></thead><tbody>'
        + rows +
        '</tbody></table>'
        '</section>'
    )
