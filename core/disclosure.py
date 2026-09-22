"""
core/disclosure.py
==================
「結果の取扱に関する補足」＝**帳票に焼き込む前提と適用範囲の字**（ヘッドレス・純関数）。

🔑 **存在理由＝成果物は一人歩きする**。レポートを受け取った人は README の開示も
画面の但し書きも見ない。⇒ 前提（DEM は地表面モデル／植生高は一律値／環境損失は
経験値）と、**いま使った式がどこまでを名乗れるか**を、帳票そのものへ書く。

🔑 **物理を 1 行も足さない**＝出るのは `models.scope_notes()` が返した刻印だけで、
範囲の数字（1 GHz・40 GHz・350 GHz・1〜6 GHz）は**式が使っている定数そのもの**を
差し込む。⇒ *式を変えたのに開示だけ古い* が起きない。

⚠️ **なぜ `core/` に置くか**＝`core/simulation.py` の `report.txt` と `report/` の
HTML 帳票の**両方**がこの字を引くため。層の向きは `views → report → core` の一方向
なので、共有する字は下の層へ置くほかない（→ `tests/test_layers.py`）。HTML の体裁
（セクションタグ・CSS）は出力層＝`report/report_common.py` が持つ。
"""

from __future__ import annotations

from fractions import Fraction

from core import dem_sources
from core import i18n
from core import models
from core import terrain_grid


def _m(value: float) -> str:
    """間隔の値 [m] を字にする（`5.0` → `5`）。"""
    return f"{float(value):g}"


def _fraction(value: float) -> str:
    """比の値を分数の字にする（`4/3` の float 表現 → `"4/3"`）。

    ⚠️ **`.2f` の小数（`1.33`）は名乗らない**（B-220）＝公開文書はすべて `4/3` と
    書いており、帳票だけ小数だと同じ前提の表記が割れる。`limit_denominator` で
    浮動小数の丸め誤差を吸収する（分母 10 で十分＝この値は変わらない定数）。
    """
    frac = Fraction(float(value)).limit_denominator(10)
    return f"{frac.numerator}/{frac.denominator}"


def _ghz(value: float) -> str:
    """範囲の値 [GHz] を字にする（`1.0` → `1`・`350.0` → `350`）。"""
    return f"{float(value):g}"


def _scope_args(key: str) -> dict:
    """刻印ごとの差し込み値（**出所は `core/models.py` の定数だけ**）。

    ⚠️ 差し込みを持つ字にここを足し忘れると `str.format` が `{lo}` を素通しする
    ＝利用者の目に波括弧が出る。`tests/test_report.py` がその形を落とす。
    """
    gas_lo, gas_hi = models.GAS_RANGE_GHZ
    veg_lo, veg_hi = models.VEG_COEFF_RANGE_GHZ
    # 解像度の刻印（B-128）＝**段階の数字も `terrain_grid` から差し込む**
    # （字に 5 / 10 / 20 を直書きすると、段階を組み替えた日に開示だけ古くなる）。
    # 🔴 **差し込む量が変わった**（B-150）＝「高」「中」は等間隔で刻まなくなったので、
    # 名乗れるのは目標間隔ではなく **DEM 画素の寸法**。緯度で縮むため*日本の幅*で出す
    # （南端 24°＝いちばん大きい／北端 46°＝いちばん小さい）。
    spacing = terrain_grid.RESOLUTION_SPACING_M
    coarse = {"m": _m(spacing["low"])}
    return {
        "earth_k_fixed":     {"k": _fraction(models.EARTH_K_STANDARD)},
        "rain_zeroed":       {"lo": _ghz(models.RAIN_MIN_GHZ)},
        "rain_extrapolated": {"hi": _ghz(models.RAIN_TABLE_MAX_GHZ)},
        "gas_zeroed":        {"lo": _ghz(gas_lo)},
        "gas_extrapolated":  {"lo": _ghz(gas_lo), "hi": _ghz(gas_hi)},
        "veg_extrapolated":  {"lo": _ghz(veg_lo), "hi": _ghz(veg_hi)},
        **{
            f"resolution_{level}": _resolution_args(level, coarse)
            for level in spacing
        },
    }.get(key, {})


#: 日本の南端・北端（`terrain_grid.WORST_CASE_LAT_DEG` と対になる南側）。
#: 1px の地上寸法は `cos φ` で縮むので、**この 2 つが幅の両端**。
_JAPAN_LAT_SPAN: tuple[float, float] = (24.0, terrain_grid.WORST_CASE_LAT_DEG)


def _resolution_args(level: str, coarse: dict) -> dict:
    """解像度の刻印の差し込み値（段階ごとに**名乗れる量が違う**）。

    「高」「中」＝画素の縁で刻む ⇒ 出せるのは *1px の寸法*（日本の幅）。
    「低」＝等間隔で刻む ⇒ 従来どおり目標間隔。
    """
    if not terrain_grid.samples_are_pixel_edges(level):
        return dict(coarse)
    zoom = terrain_grid.RESOLUTION_ZOOM[level]
    lo, hi = _JAPAN_LAT_SPAN
    return {
        "px_hi": f"{terrain_grid.pixel_size_m(lo, zoom):.1f}",
        "px_lo": f"{terrain_grid.pixel_size_m(hi, zoom):.1f}",
        **coarse,
    }


def handling_lines(note_keys) -> list[str]:
    """刻印のキー列を、その言語の 1 行ずつへ翻訳する（書式なしの素の字）。

    HTML の帳票も `report.txt` もここを通る＝**字は 1 か所**。
    """
    lines = []
    for key in note_keys:
        text = i18n.t(f"html_scope_{key}")
        args = _scope_args(key)
        lines.append(text.format(**args) if args else text)
    return lines


def calibration_line() -> str:
    """較正プロファイルの欄（3.5 で埋まる**席**）。いまは常に「未適用」。

    ⚠️ **空でも欄を置く**のが要点＝欄が無いと、較正した結果と較正していない結果が
    同じ顔で出る。3.5 で値が入ったとき、初めて違いが読める形にしておく。
    """
    return f'{i18n.t("html_calib_profile")}: {i18n.t("html_calib_none")}'


def data_source_line(dem_source_ids=None) -> str:
    """標高データの出典（B-134／B-216 で実際に使ったソースへ追従）。

    **帳票の 5 面と地形断面図が引く 1 本の字**。

    🔑 **地図タイルの出典（B-133）とは置き場が違う**＝地図は 3 面にしか出ないので
    画像へ焼けば足りたが、**標高データは全面の土台**（条件探索のように断面図を
    持たない帳票も、値は標高から出ている）。⇒ **全面が必ず通る開示のセクション**に置き、
    図だけ抜き出して渡される断面図には*加えて*焼く（`report_path`）。

    ⚠️ **「較正の席」と同じ扱いにする**（`calibration_line`）＝刻印の列
    （`html_scope_*`）に混ぜない。あちらは**条件によって出たり出なかったりする**
    適用範囲の話で、出典は**常に出る事実**。混ぜると `models.scope_notes()` が
    出典まで判定することになる。

    Args:
        dem_source_ids: 実際に使った `dem_sources` の `source_id`。単一の
            文字列、複数本（台帳・多ホップ）を渡す場合は反復可能なもの、
            省略時は国土地理院（3.3 以前と同じ既定）。**複数の異なるソースが
            混じっていれば「複数」と表示する**（1 本を代表に選んで嘘をつかない）。

    ⚠️ **`report.txt` の `DEM Source:` 行（`_format_dem_source_line`）とは
    出所（`dem_sources.resolve()`）は同じだが、**組み込みソース（国土地理院）
    の文字列は表示言語で訳す**（B-227）＝`display_name` は Japanese 固定文字列
    のため、英語帳票では地形断面図を描く matplotlib のフォント（英語モードでは
    日本語フォントを適用しない＝`report/mpl_fonts.py`）に日本語グリフが無く
    豆腐化していた。`report.txt` は素のテキストなのでこの問題が起きず、
    宣言そのままの `display_name` を刻む（利用者が宣言したソースは訳しようが
    ないので、そちらは HTML 側もそのまま出す）。
    **`attribution` は含めない**＝B-135 の「機関名を重ねなくても出所は特定できる」
    という既存の方針を維持し、断面図の距離軸ラベルと同じ行に収める字数を抑える。
    **利用者が宣言した外部ソースだけ**、宣言内容のハッシュ（`[xxxxxxxxxxxx]`）を
    末尾に添える（I-147 残り(a)）＝組み込み（国土地理院）は宣言ファイルを持たず
    書き換えが起き得ないので付けない。
    """
    if dem_source_ids is None:
        ids: set[str] = set()
    elif isinstance(dem_source_ids, str):
        ids = {dem_source_ids}
    else:
        ids = set(dem_source_ids)
    if not ids:
        ids = {dem_sources.GSI_DEM.source_id}
    if len(ids) == 1:
        spec = dem_sources.resolve(next(iter(ids)))
        if spec.source_id == dem_sources.GSI_DEM.source_id:
            value = i18n.t("html_elev_source_gsi_dem")
        else:
            # I-147 残り(a)＝宣言内容のハッシュ（B-236 と同じ fingerprint）を
            # 添える。`source_id` を変えずに URL・デコード方式だけ書き換えても
            # 別物と分かるように（ディスクキャッシュの自動無効化と同じ根拠）。
            fp = dem_sources.definition_fingerprint(spec)
            value = f"{spec.display_name} [{fp}]"
    else:
        value = i18n.t("html_elev_source_mixed")
    return f'{i18n.t("html_elev_source_prefix")}: {value}'


def handling_text(note_keys, dem_source_ids=None) -> str:
    """「結果の取扱に関する補足」セクションの**素のテキスト**（`report.txt` 用）。

    見出しは英字の角括弧＝`report.txt` の他のセクション（`[LINK BUDGET]` 等）と同じ字面に
    合わせる。中身は表示言語に従う（レポート本文と同じ扱い）。

    Args:
        dem_source_ids: `data_source_line()` へそのまま渡す（B-216）。渡さないと
            国土地理院と表示される。
    """
    body = "\n".join(f"- {line}" for line in handling_lines(note_keys))
    return (
        "[NOTES ON HANDLING THIS RESULT]\n"
        f"{i18n.t('html_handling_title')}\n"
        f"{body}\n"
        f"- {calibration_line()}\n"
        f"- {data_source_line(dem_source_ids)}\n"
    )
