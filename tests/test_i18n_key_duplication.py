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
**成果物（レポート HTML・グラフ画像）の語は対象外**。`html_*` と `pl_*`、および
`report/` から引かれているキー（`scn_mode` `mh_section` `scn_samples` など）は
出力契約の側で、**画面と成果物の名前を揃えるのは 3.2 の仕事**として据え置いてある。
ここを混ぜて統合すると、**画面の改名がレポート出力を黙って変える**。

⚠️ 接頭辞だけで切ると足りない＝`report/` は接頭辞なしのキーも引いている
（I-073 の作業中に実測で判明。接頭辞で切った最初の集計は 13 組と出て、2 組多かった）。
だから分類は **接頭辞 ＋ 実際の参照元** の両方で決める。

判定は「全言語が一致したとき」だけ
----------------------------------
ja と en の**両方**が同じキーどうしを重複と呼ぶ。片方だけ一致（`Note` が
`メモ` と `備考`、`送信電力（dBm）` と `送信電力 (dBm)` など）は**訳のゆれ**という
別の欠陥で、2026-08-09 時点で 12 組ある。ここで一緒に鳴らすと**例外表が本体より
長いゲート**になるので、そちらは台帳の I-075 として分けた。

ゲートの壊れ方 3 点（[[feedback-promote-recurring-checks]]）
------------------------------------------------------------
- **一度も落ちない**：変異検証済み（キーを 2 本に割ると赤／`report/` の参照を
  見なくすると赤／画面側の集合が空になると `test_the_screen_side_is_not_empty` が赤）。
- **毎回鳴る**：現時点の例外は **0 件**。0 件で始められる範囲を選んだのがこの分類。
- **間違ったものを要求している**：偶然同じ字になる短い語（`OK` など）が将来
  画面どうしでぶつかったら、統合ではなく `ALLOWED_DUPLICATES` に**理由つきで**足す。
"""

import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import i18n

REPO = Path(__file__).resolve().parent.parent

#: 画面どうしで同じ字を持つことを認める組。**理由を必ず書く**（空で始まっている）。
#: 形式: frozenset({"キー", "キー"}): "なぜ別々でよいか"
ALLOWED_DUPLICATES: dict[frozenset[str], str] = {}


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
    """成果物（レポート HTML・グラフ画像）に出る語のキー。"""
    by_prefix = {k for k in i18n._STRINGS["ja"] if k.startswith(("html_", "pl_"))}
    return by_prefix | _keys_referenced_by("report")


def _screen_keys() -> list[str]:
    artifact = _artifact_keys()
    return [k for k in i18n._STRINGS["ja"] if k not in artifact]


def test_the_screen_side_is_not_empty():
    """分類が壊れて画面側が空になると、本体のテストが黙って素通りする。"""
    screen = _screen_keys()
    assert len(screen) >= 200, f"画面キーが少なすぎる（分類が壊れている疑い）: {len(screen)}"
    assert _keys_referenced_by("report"), "report/ からキーを 1 つも拾えていない"


def test_screen_keys_do_not_hold_the_same_wording_twice():
    """画面に出る同じ字（全言語一致）を、2 つのキーで持っていないこと。"""
    langs = sorted(i18n._STRINGS)
    groups: dict[tuple[str | None, ...], list[str]] = defaultdict(list)
    for key in _screen_keys():
        groups[tuple(i18n._STRINGS[lang].get(key) for lang in langs)].append(key)

    offenders = [
        (values, keys)
        for values, keys in groups.items()
        if len(keys) > 1 and frozenset(keys) not in ALLOWED_DUPLICATES
    ]
    assert not offenders, "同じ字を 2 つ以上のキーで持っている（1 本へ寄せる）: " + " / ".join(
        f"{dict(zip(langs, values))} -> {sorted(keys)}" for values, keys in offenders
    )
