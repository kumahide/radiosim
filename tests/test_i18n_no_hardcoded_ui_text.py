"""
tests/test_i18n_no_hardcoded_ui_text.py
=======================================
**画面に出る自然言語が、i18n を迂回して書かれていない**ことのゲート。

なぜ要るか
----------
2026-08-10 の 2.7RC1 実機確認で、バッチ完了時の帯だけが**日本語表示のまま
`Done: batch_20260810_164338`** と英語で出ていた（B-066）。原文は「ここだけ英語」。
`views/batch_run.py` が完了表示だけ `i18n.t()` を通さず f-string で組んでいた。

1 行の直し（`batch_done` キーの新設）で済む欠陥だが、**同じ迂回はどの窓でも
いつでも書ける**。注意書きを増やしても強制されないので、機械に守らせる
（[[feedback-promote-recurring-checks]] の昇格）。

見るのは「画面へ字を出す 4 つの口」
----------------------------------
①`text=`（ウィジェット生成と `.config(text=…)` は `ast.Call` のキーワードとして
同じ形に見える）②`label=`（`tk.Menu.add_command` 系）③`.title(…)`（窓の題）
④`dialogs.*(…)` の引数（ダイアログの題と本文）。

⚠️ **①だけを見ていた版があった**（2026-08-10・Codex 独立レビュー P2 で指摘）＝
テスト名と README は「画面に出る自然言語」を保証すると読めるのに、実際は `text=`
しか見ておらず、`label="Open"` や `title("Settings")` を直書きしても緑だった。
**保証の文と検査の範囲がずれているゲートは、無いより悪い**（→
[[feedback-promote-recurring-checks]]＝「ここまでは大丈夫」は反例 1 つで嘘になる）。

f-string は**式の部分を捨ててリテラルの部分だけ**を見る＝`f"▶  {pid}"` の `pid` や
`f"{margin:+.2f}"` の数値書式は自然言語ではないので対象外になる。

⚠️ 対象は `views/` だけ。`report/` は成果物の側で、画面と成果物の語を揃えるのは
別の仕事（[[test_i18n_key_duplication]] と同じ切り方）。

ゲートの壊れ方 3 点（[[feedback-promote-recurring-checks]]）
------------------------------------------------------------
- **一度も落ちない**：`test_the_scanner_catches_the_original_defect` が B-066 の
  実際の書き方（`text=f"Done: {dir}"`）を毎回スキャンして、赤くなることを確かめる。
  スキャナが空を返すようになったらこの試験が落ちる。
- **毎回鳴る**：例外は下の `ALLOWED_LITERALS` の 1 語（`ERR`）だけ。判定の 3 値は
  `docs/glossary.md` で**両言語共通の定訳**と決めてあるので、訳し分けない。
- **間違ったものを要求している**：「英字が出たら赤」ではなく「**自然言語が出たら
  赤**」。3 文字未満の英字（`OK` `NG` `dB` 等）と数値書式は最初から対象外で、
  記号（`▶` `✓` `⚠`）も通る。
"""

import ast
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REPO = Path(__file__).resolve().parent.parent

#: 自然言語とみなす字＝英単語（3 文字以上）または仮名・漢字。
#: 3 文字未満の英字を外すのは `OK` `NG` `dB` `m` を鳴らさないため
#: （単位と判定は両言語で同じ字を使う＝docs/glossary.md）。
NATURAL_LANGUAGE = re.compile(r"[A-Za-z]{3,}|[぀-ヿ一-鿿]")

#: i18n を通さないことを認めるリテラル。**理由を必ず書く**。
ALLOWED_LITERALS: dict[str, str] = {
    "ERR": "判定の 3 値（OK / NG / ERR）は両言語共通の定訳＝docs/glossary.md",
    "README": "メニュー「READMEを開く」で開く窓の題＝訳す対象ではない"
              "（開く実体は `docs/manual_*.md` へ移ったが、画面の語は据え置き）",
    "MHz": "単位は両言語共通（docs/glossary.md の対象外）＝グラフ窓の題 `2400.0 MHz`",
}


def _literal_part(node: ast.expr) -> str | None:
    """`text=` の値から**リテラルの字だけ**を取り出す（`{…}` の式は捨てる）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def _screen_text_args(call: ast.Call) -> list[ast.expr]:
    """この呼び出しのうち、**画面に字を出す**引数を返す（上の 4 つの口）。"""
    args: list[ast.expr] = [kw.value for kw in call.keywords
                            if kw.arg in ("text", "label")]
    func = call.func
    if isinstance(func, ast.Attribute):
        # `win.title("…")`＝窓の題。⚠️ 引数無しの `str.title()` と区別するため
        # 「リテラルを 1 つ渡している」形だけを見る。
        if func.attr == "title" and len(call.args) == 1 and not call.keywords:
            args.append(call.args[0])
        # `dialogs.alert(parent, title, message)` 系＝題も本文も画面に出る。
        if isinstance(func.value, ast.Name) and func.value.id == "dialogs":
            args.extend(call.args)
    return args


def _offenders(source: str, name: str = "<test>") -> list[str]:
    """画面へ字を出す口に自然言語のリテラルを渡している箇所を返す。"""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        for value in _screen_text_args(node):
            text = _literal_part(value)
            if text is None:
                continue
            words = NATURAL_LANGUAGE.findall(text)
            if words and not all(w in ALLOWED_LITERALS for w in words):
                found.append(f"{name}:{value.lineno}: {text!r}")
    return found


VIEWS = sorted((REPO / "views").glob("*.py"))


def test_the_view_layer_is_not_empty():
    """走査対象そのものが消えていないこと（空を緑と読み違えない）。"""
    assert len(VIEWS) >= 5


@pytest.mark.parametrize("path", VIEWS, ids=lambda p: p.name)
def test_no_natural_language_reaches_the_screen_without_i18n(path: Path):
    offenders = _offenders(path.read_text(encoding="utf-8"), path.name)
    assert not offenders, (
        "画面に出る字が i18n を迂回している（B-066 と同じ形）。"
        "`core/i18n.py` にキーを足して `i18n.t()` 経由にすること:\n  "
        + "\n  ".join(offenders)
    )


def test_the_scanner_catches_the_original_defect():
    """B-066 の実際の書き方を、このスキャナが必ず捕まえること（変異検証）。"""
    offenders = _offenders(
        'self._prog_label.config(text=f"Done: {os.path.basename(batch_dir)}")')
    assert len(offenders) == 1


def test_the_scanner_catches_the_other_three_doors():
    """`text=` 以外の 3 つの口も塞がっていること（Codex P2・変異検証）。"""
    assert _offenders('menu.add_command(label="Open", command=f)')
    assert _offenders('win.title("Settings")')
    assert _offenders('dialogs.alert(self, "Error", "Could not save the file")')


def test_the_scanner_lets_the_shared_verdict_words_through():
    """判定・単位・記号・数値書式は鳴らないこと（毎回鳴るゲートにしない）。"""
    assert not _offenders('lbl.config(text=f"⚠ {n} ERR")')
    assert not _offenders('lbl.config(text=f"{cur} / {tot}  ({pct}%)")')
    assert not _offenders('lbl.config(text=f"▶  {pid}")')
