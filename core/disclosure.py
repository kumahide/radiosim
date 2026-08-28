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
（節タグ・CSS）は出力層＝`report/report_common.py` が持つ。
"""

from __future__ import annotations

from core import i18n
from core import models
from core import terrain_grid


def _m(value: float) -> str:
    """間隔の値 [m] を字にする（`5.0` → `5`）。"""
    return f"{float(value):g}"


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
    # 解像度の刻印（B-128）＝**段階の目標間隔も `terrain_grid` の定数から差し込む**
    # （字に 5 / 10 / 20 を直書きすると、段階を組み替えた日に開示だけ古くなる）。
    spacing = terrain_grid.RESOLUTION_SPACING_M
    span = {"coarse": _m(max(spacing.values())), "fine": _m(min(spacing.values()))}
    return {
        "earth_k_fixed":     {"k": f"{float(models.EARTH_K_STANDARD):.2f}"},
        "rain_zeroed":       {"lo": _ghz(models.RAIN_MIN_GHZ)},
        "rain_extrapolated": {"hi": _ghz(models.RAIN_TABLE_MAX_GHZ)},
        "gas_zeroed":        {"lo": _ghz(gas_lo)},
        "gas_extrapolated":  {"lo": _ghz(gas_lo), "hi": _ghz(gas_hi)},
        "veg_extrapolated":  {"lo": _ghz(veg_lo), "hi": _ghz(veg_hi)},
        **{
            f"resolution_{level}": {"m": _m(value), **span}
            for level, value in spacing.items()
        },
    }.get(key, {})


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


def data_source_line() -> str:
    """標高データの出典（B-134）。**帳票の 5 面と地形断面図が引く 1 本の字**。

    🔑 **地図タイルの出典（B-133）とは置き場が違う**＝地図は 3 面にしか出ないので
    画像へ焼けば足りたが、**標高データは全面の土台**（条件探索のように断面図を
    持たない帳票も、値は標高から出ている）。⇒ **全面が必ず通る開示の節**に置き、
    図だけ抜き出して渡される断面図には*加えて*焼く（`report_path`）。

    ⚠️ **「較正の席」と同じ扱いにする**（`calibration_line`）＝刻印の列
    （`html_scope_*`）に混ぜない。あちらは**条件によって出たり出なかったりする**
    適用範囲の話で、出典は**常に出る事実**。混ぜると `models.scope_notes()` が
    出典まで判定することになる。
    """
    return i18n.t("html_elev_source")


def handling_text(note_keys) -> str:
    """「結果の取扱に関する補足」節の**素のテキスト**（`report.txt` 用）。

    見出しは英字の角括弧＝`report.txt` の他の節（`[LINK BUDGET]` 等）と同じ字面に
    合わせる。中身は表示言語に従う（レポート本文と同じ扱い）。
    """
    body = "\n".join(f"- {line}" for line in handling_lines(note_keys))
    return (
        "[NOTES ON HANDLING THIS RESULT]\n"
        f"{i18n.t('html_handling_title')}\n"
        f"{body}\n"
        f"- {calibration_line()}\n"
        f"- {data_source_line()}\n"
    )
