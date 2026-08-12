"""
tests/test_i18n_glossary.py
===========================
**画面語彙のゲート**＝`docs/glossary.md` に載せた語だけを画面に出す。

なぜ要るか
----------
2026-08-01 に「同じものを 2 語で呼んでいる」欠陥が 1 セッションで 4 件出た。
うち 1 件（中継の `ホップ数`）は**数字は正しいのに語だけが別の量として伝わる**
形のバグで、2.6b で `区間` へ直した。そのとき分かったのは
**1 か所だけ直すと語彙が 3 通りに増える**ということで、語の統一を散文で持つ限り
必ずずれる（実行ボタンの位置・フローの定義でも同じ壊れ方を見ている）。

⇒ 用語集を**表**として持ち、i18n の全文言と突き合わせる。

ここが見るのは**画面語彙だけ**
------------------------------
`hops.csv` の列名（出力契約）と、コード内部の識別子・i18n のキー名・`{hops}` の
ような差し込み名（実装語彙）は対象外。差し込み名は読み手に見えないので、判定の
前に落とす（この配慮が無いと、実装語彙の改名をゲートが強制してしまう）。

守る型（用語集の 4 列がそのまま 3 本のテストになる）
--------------------------------------------------
1. **使わない言い換えが画面に出ていない**（`使わない言い換え` 列）。
2. **用語も en も、実際に画面で使われている**（`用語` / `en` 列）＝用語集が製品から
   浮いて、誰も読まない紙になるのを防ぐ。
3. **表自身が自己矛盾していない**＝ある行の禁止語が、別の行の採用語になっていない。

ゲートの壊れ方 3 点（新設ゲートはこれを全部通す）
------------------------------------------------
- **一度も落ちない**：2026-08-05 に変異 3 種で確認済み＝①`mh_hops` を `"ホップ数"`
  に戻す→ 1 が落ちる ②`pl_rx_level` を `"受信強度"` に変える→ 1 が落ちる
  ③用語集の `区間` 行を `ホップ` に書き換える→ 2 と 3 が落ちる。
  ⚠️ ②で 2 が落ちないのは正しい＝`受信レベル` は他のキーにも出ているので、
  **1 か所の書き換えは「禁止語が出た」側で捕まる**（2 が見るのは語の全滅）。
- **毎回鳴る**：禁止語 22 語が現時点の i18n に 1 つも出ていないことを実測してから
  表に載せた。英字は語境界つきで見る（`hop` が `hopper` に当たらない）。
- **間違ったものを要求している**：2 の「実際に使われている」は**部分一致**で見る
  ——`地点` は ` 地点 `、`Waypoint` は ` Waypoints ` として出るので、完全一致を
  求めると*正しい画面のほうを*直させることになる。
"""

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import i18n

GLOSSARY = Path(__file__).resolve().parent.parent / "docs" / "glossary.md"


class Term:
    """用語集の 1 行。"""

    def __init__(self, ja: str, definition: str, en: str, avoid: list[str]):
        self.ja = ja
        self.definition = definition
        self.en = en
        self.avoid = avoid

    def __repr__(self) -> str:      # 失敗時に読める形で出す
        return f"<Term {self.ja} / {self.en}>"


def _parse_glossary() -> list[Term]:
    """`## 用語` 節の表を読む。

    見出しで節を切るのは、**表の読み方**の表など他の表を拾わないため。
    """
    text = GLOSSARY.read_text(encoding="utf-8")
    section = text.split("\n## 用語\n", 1)
    assert len(section) == 2, "docs/glossary.md に「## 用語」節が無い"
    body = section[1].split("\n## ", 1)[0]

    terms: list[Term] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != 4:
            raise AssertionError(f"用語集の行が 4 列ではない: {line}")
        if cells[0] in ("用語", "") or set(cells[0]) <= {"-", ":"}:
            continue                                   # 見出し行・区切り行
        avoid = [w.strip() for w in cells[3].split("/") if w.strip() and w.strip() != "—"]
        terms.append(Term(cells[0], cells[1], cells[2], avoid))
    return terms


def _shown_strings() -> list[tuple[str, str, str]]:
    """(lang, key, 読み手に見える字) の一覧。差し込み名は落とす。"""
    out = []
    for lang, table in i18n._STRINGS.items():
        for key, value in table.items():
            if isinstance(value, str):
                out.append((lang, key, re.sub(r"\{[^}]*\}", "", value)))
    return out


def _occurs_as_word(word: str, text: str) -> bool:
    """**禁止語の判定**＝英字は語境界で、日本語は部分一致で見る。

    語境界なしだと `hop` が `hopper` に当たる（＝毎回鳴るゲート）。

    ⚠️ **`\\b` は使えない**（2026-08-13・I-082 の変異検証で発覚）＝Python の `\\b` は
    *Unicode の*単語文字で境界を決めるので、`READMEを開く` の `README` と `を` の
    あいだに境界が立たず、**日本語に直に接した英字の禁止語が丸ごと素通りする**。
    ⇒ 境界とみなすのは **ASCII の単語文字だけ**にする（`hop` は `hopper` に当たらず、
    `README` は `READMEを開く` に当たる）。
    """
    if word.isascii():
        pat = rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])"
        return re.search(pat, text, re.IGNORECASE) is not None
    return word in text


def _contains(word: str, text: str) -> bool:
    """**採用語の在否**＝部分一致で見る。

    ⚠️ ここを語境界にすると `Waypoint` が ` Waypoints ` に当たらず、**正しい画面の
    ほうを**直させることになる（実際に最初の実装がそうなって落ちた）。在否の判定に
    厳しさは要らない——用語集が製品から浮いていないかだけを見ている。
    """
    return word.lower() in text.lower()


TERMS = _parse_glossary()


def test_glossary_is_not_empty():
    """表が読めていること（パーサが黙って 0 行を返すと 1〜3 が全部通ってしまう）。"""
    assert len(TERMS) >= 10, f"用語集の行数が少なすぎる: {len(TERMS)}"
    for t in TERMS:
        assert t.ja and t.en and t.definition, f"空欄のある行: {t!r}"


# ============================================================
# 1. 使わない言い換えが画面に出ていないこと
# ============================================================
@pytest.mark.parametrize("term", TERMS, ids=lambda t: t.ja)
def test_avoided_wording_never_reaches_the_screen(term: Term):
    offenders = [
        f"{lang}:{key}（{word}）"
        for word in term.avoid
        for lang, key, shown in _shown_strings()
        if _occurs_as_word(word, shown)
    ]
    assert not offenders, (
        f"「{term.ja}」の使わない言い換えが画面語彙に出ている"
        f"（{term.ja} / {term.en} へ）: " + ", ".join(offenders)
    )


# ============================================================
# 2. 用語集が製品から浮いていないこと
# ============================================================
@pytest.mark.parametrize("term", TERMS, ids=lambda t: t.ja)
def test_each_term_is_actually_used_in_both_languages(term: Term):
    used = {
        lang: any(_contains(word, shown)
                  for l, _key, shown in _shown_strings() if l == lang)
        for lang, word in (("ja", term.ja), ("en", term.en))
    }
    missing = [lang for lang, ok in used.items() if not ok]
    assert not missing, (
        f"用語集の「{term.ja} / {term.en}」が {missing} の画面文言に 1 つも無い"
        "（語を変えたなら用語集も同じコミットで直す／使わなくなったなら表から落とす）"
    )


# ============================================================
# 3. 表そのものが自己矛盾していないこと
# ============================================================
def test_no_term_is_banned_by_another_row():
    adopted = {t.ja for t in TERMS} | {t.en for t in TERMS}
    conflicts = [
        f"{t.ja}:{word}"
        for t in TERMS
        for word in t.avoid
        if word in adopted
    ]
    assert not conflicts, (
        "ある行の「使わない言い換え」が、別の行の採用語になっている: "
        + ", ".join(conflicts)
    )


# ============================================================
# 4. 判定そのものの変異検証（このゲートが本当に鳴るか）
# ============================================================
@pytest.mark.parametrize("word,text,expected", [
    # 🔴 実際に素通りしていた形（I-082 の変異検証で発覚）＝英字の禁止語が
    #    日本語に直に接すると `\b` では境界が立たない。
    ("README", "READMEを開く",   True),
    ("README", "Open README",    True),
    ("hop",    "hop count",      True),
    # 語境界は保つ＝これらで鳴ってはいけない（毎回鳴るゲートにしない）。
    ("hop",    "hopper",         False),
    ("hop",    "multihop",       False),
    ("README", "READMEs",        False),
    # 日本語の禁止語は部分一致のまま。
    ("ホップ", "ホップ数",        True),
])
def test_the_word_matcher_catches_what_it_claims(word: str, text: str, expected: bool):
    assert _occurs_as_word(word, text) is expected
