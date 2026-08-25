"""
tests/test_i18n_key_duplication.py
==================================
**画面に出る同じ字を、2 つの i18n キーで持たない**ゲート。

なぜ要るか
----------
2026-08-08 に `↻ ランチャーから更新` が `btn_refresh_common` と `scn_refresh` の
2 キーで持たれているのが見つかった（B-053 で ↻ の置き場を 3 窓で揃えたときに発覚）。
**置き場は揃ったのに出所は揃っていない**状態で、片方だけ直せば画面に 2 通りの語が出る。

⇒ 全数を数えたら**画面どうしの重複は 11 組・24 キー**あった（I-073）。窓の名前が
ランチャーのボタン・窓の題・ダイアログの題で 3 キーに分かれている類が主で、
これはスライス D の改名（`マップウィンドウ`→`地図` 等）で実際に踏んだ面である。
24 キーを 11 キーへ寄せたうえで、**再び増えないこと**をここで機械に守らせる。

見るのは「画面どうし」だけ
--------------------------
**成果物（レポート HTML・グラフ画像・KML）の語は対象外**。あちらは出力契約で、
**画面の改名がレポート出力を黙って変える**のを避けたい。しかも成果物側は
`html_*` と `pl_*` が同じ語を持つのが**設計どおり**（同じ量を台帳と断面図の両方に
出す）なので、同じ網を掛けると 20 組以上が毎回鳴る＝**壊れ方②**そのものになる。

🔑 **分類はここで数え直さない**（2026-08-26・I-089）＝`core/i18n.py` の
`_ARTIFACT_PREFIXES` / `_ARTIFACT_KEYS` が**外部訳の「開かないキー」の正典**で、
`tests/test_i18n_external.py` が成果物モジュールの `t()` を走査して守っている。
以前はこのファイルが接頭辞と `report/` の参照から**独自に**分類していて、実際に
食い違っていた（`scn_axis_*` は i18n では成果物・ここでは画面）。⇒ 正典を引き、
`report/` の参照は**和集合**として重ねるだけにする（分類を 2 つ持たない）。

判定は「どちらか一方の言語で字が同じ」なら鳴る
----------------------------------------------
🆕 **2026-08-26（I-089）に「全言語一致」から広げた。** 片方だけ一致する組
（`Note` が `メモ` と `備考`、`Rician K-Factor` が 2 通りの ja、…）は**訳のゆれ**
という別の欠陥で、I-075 / I-089 として台帳で片付けた。**12 組を消し切ったので、
ようやくここを有効にできる**（残っていると例外表が本体より長いゲートになる）。

⚠️ **例外は 1 つの系統だけ**＝`col_*`（表の列見出し）と `lbl_b_*`（共通設定の帯）は
**幅が語に優先する**系統で、用語集が「同じ語の短縮は許す」と明文で認めている。
この 2 系統は *ja が同じで en だけ短い* のが設計なので、ja 側の一致は見逃す。
🔑 **ただし en まで一致したら見逃さない**＝それは短縮形ではなく本物の重複。

ゲートの壊れ方 3 点（[[feedback-promote-recurring-checks]]）
------------------------------------------------------------
- **一度も落ちない**：変異検証済み（キーを 2 本に割ると赤／`col_note` の en を
  `Note` に戻すと赤／幅優先の免除に en 一致の組を食わせると赤＝下の 2 本）。
- **毎回鳴る**：現時点の例外表は **0 件**（幅優先の系統は規則で見逃すので、
  手書きの例外は 1 つも要らない）。
- **間違ったものを要求している**：偶然同じ字になる短い語（`OK` など）が将来
  画面どうしでぶつかったら、統合ではなく `ALLOWED_DUPLICATES` に**理由つきで**足す。
"""

import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import i18n

REPO = Path(__file__).resolve().parent.parent

#: 画面どうしで同じ字を持つことを認める組。**理由を必ず書く**（空で始まっている）。
#: 形式: frozenset({"キー", "キー"}): "なぜ別々でよいか"
ALLOWED_DUPLICATES: dict[frozenset[str], str] = {}

#: **幅が語に優先する 2 つの系統**（用語集「表の列見出しだけは、幅が語に優先する」）。
#: `col_*`＝表の列見出し／`lbl_b_*`＝共通設定の帯。どちらも ja は本体と同じ語のまま、
#: en だけ短縮する（`Frequency (MHz)` → `Freq (MHz)`）のが設計。
_WIDTH_FIRST_PREFIXES = ("col_", "lbl_b_")


def _keys_referenced_by(layer: str) -> set[str]:
    """`<layer>/` 配下の .py が文字列リテラルとして書いているキー。"""
    known = set(i18n._STRINGS["ja"])
    found: set[str] = set()
    for path in (REPO / layer).rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        found |= {
            m.group(1)
            for m in re.finditer(r"""["']([a-z][a-z0-9_]{2,})["']""", text)
            if m.group(1) in known
        }
    return found


def _artifact_keys() -> set[str]:
    """成果物（レポート HTML・グラフ画像・KML）に出る語のキー。

    正典は `core/i18n.py` の「開かないキー」。`report/` の参照を和集合で重ねるのは、
    正典が接頭辞で漏らした 1 件が黙って画面側に紛れ込むのを避けるため（**分類を
    2 つ持たない**が、片方が緩い場合は厳しいほうへ倒す）。
    """
    by_i18n = {
        k for k in i18n._STRINGS["ja"]
        if k.startswith(i18n._ARTIFACT_PREFIXES) or k in i18n._ARTIFACT_KEYS
    }
    return by_i18n | _keys_referenced_by("report")


def _screen_keys() -> list[str]:
    artifact = _artifact_keys()
    return [k for k in i18n._STRINGS["ja"] if k not in artifact]


def _is_width_first_group(keys: list[str], lang: str) -> bool:
    """幅優先の系統ゆえに ja の一致を許してよい組か。

    ⚠️ **en まで一致していたら許さない**（短縮形ではなく本物の重複）。
    """
    if lang != "ja":
        return False
    if not any(k.startswith(_WIDTH_FIRST_PREFIXES) for k in keys):
        return False
    en = [i18n._STRINGS["en"].get(k) for k in keys]
    return len(set(en)) == len(en)


def test_the_screen_side_is_not_empty():
    """分類が壊れて画面側が空になると、本体のテストが黙って素通りする。"""
    screen = _screen_keys()
    assert len(screen) >= 200, f"画面キーが少なすぎる（分類が壊れている疑い）: {len(screen)}"
    assert _keys_referenced_by("report"), "report/ からキーを 1 つも拾えていない"
    assert i18n._ARTIFACT_PREFIXES, "i18n の成果物接頭辞が空（正典が壊れている）"


@pytest.mark.parametrize("lang", sorted(i18n._STRINGS))
def test_screen_keys_do_not_hold_the_same_wording_twice(lang: str):
    """画面に出る同じ字を、2 つのキーで持っていないこと（**片方の言語で見る**）。"""
    groups: dict[str | None, list[str]] = defaultdict(list)
    for key in _screen_keys():
        groups[i18n._STRINGS[lang].get(key)].append(key)

    offenders = [
        (value, keys)
        for value, keys in groups.items()
        if len(keys) > 1
        and frozenset(keys) not in ALLOWED_DUPLICATES
        and not _is_width_first_group(keys, lang)
    ]
    assert not offenders, (
        f"[{lang}] 同じ字を 2 つ以上のキーで持っている"
        "（1 本へ寄せる／訳を割る／幅優先の系統なら接頭辞を見直す）: "
        + " / ".join(f"{value!r} -> {sorted(keys)}" for value, keys in offenders)
    )


# ============================================================
# 免除そのものの変異検証（この抜け道が広すぎないか）
# ============================================================
def test_the_width_first_exemption_needs_a_width_first_key():
    """幅優先の系統が 1 つも無い組は、ja が同じでも免除しない。"""
    assert not _is_width_first_group(["err_label_start", "scn_from"], "ja")


def test_the_width_first_exemption_does_not_swallow_real_duplicates():
    """en まで一致した組は、幅優先の系統でも免除しない（本物の重複）。"""
    assert not _is_width_first_group(["col_note", "col_note"], "ja")
    assert not _is_width_first_group(["col_start", "lbl_start"], "en")
