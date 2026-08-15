"""独立レビュー駆動スクリプト（`tools/codex_review/run.ps1`）の検査。

🔑 **この面が要る理由**＝2026-08-16 の Codex 8 巡目 P1 で、
**スクリプトのコメントが「構造で塞いだ」と書いていた保証が実在しなかった**。
`-C` は作業ディレクトリを決めるだけで読み取り範囲を狭めず、`-s read-only` は
「書けない」であって「ここしか読めない」ではない。canary で実測したところ、
staging の 3 階層上のファイルを Codex はそのまま読み出した。

⇒ [[feedback-promote-recurring-checks]] の「開示を書く仕事は『無いことの検査』を
対で置く」。**書いた保証が本当かを、文章ではなくテストで持つ。**
ここが守るのは製品の振る舞いではなく、**私たちが自分の道具について書く主張**。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "codex_review" / "run.ps1"
PROMPTS = {
    "code": ROOT / "tools" / "codex_review" / "prompt_code.txt",
    "docs": ROOT / "tools" / "codex_review" / "prompt_docs.txt",
}


@pytest.fixture(scope="module")
def script() -> str:
    if not SCRIPT.exists():
        pytest.skip("run.ps1 が無い環境")
    return SCRIPT.read_text(encoding="utf-8")


# --- 独立性の芯（ここが壊れるとこの工程の値が消える） -------------------------

def test_the_prompt_is_read_from_a_file_not_composed_inline(script):
    """入力文はファイルから読む＝その場で観点を混ぜる余地を作らない。"""
    for mode, path in PROMPTS.items():
        assert path.exists(), f"入力文の正典が無い: {path}"
        assert path.name in script, f"{mode} の入力文をスクリプトが読んでいない"


def test_the_code_prompt_only_carries_the_diff_path_and_base(script):
    """⛔ 入力文の差し込みは**差分のパスと比較元だけ**。

    ここに観点（「DPI を見て」等）を混ぜられる差し込み口を増やさない。
    """
    placeholders = set(re.findall(r"\{([A-Z_]+)\}", PROMPTS["code"].read_text(encoding="utf-8")))
    assert placeholders <= {"DIFF_PATH", "BASE"}, f"差し込み口が増えている: {placeholders}"


def test_the_docs_prompt_names_only_the_scope(script):
    """②で指定してよいのは範囲だけ＝「正典間の食い違い・状態の陳腐化」。"""
    text = PROMPTS["docs"].read_text(encoding="utf-8")
    assert "正典間の食い違い" in text and "陳腐化" in text
    # どの文書のどこが怪しいかを言っていないこと（具体的な指差しの語）
    for banned in ("を重点的", "に注意して", "が怪しい", "を中心に"):
        assert banned not in text, f"観点の注入: {banned}"


def test_codex_is_never_allowed_to_write(script):
    """発見は Codex・処方はこちら＝`-s read-only` 固定で、危険な迂回を持たない。"""
    assert "'-s', 'read-only'" in script
    assert "--dangerously-bypass-approvals-and-sandbox" not in script.split(".NOTES")[-1].split("#>")[-1], \
        "サンドボックス回避が実行引数に入っている"


def test_the_raw_answer_lands_before_we_read_it(script):
    """返答は原文のままファイルへ＝私の要約と突き合わせられる状態にする。"""
    assert "-o" in script and "codex_raw.md" in script


# --- 異常系（8 巡目 P2） -------------------------------------------------------

def test_a_nonzero_exit_fails_the_round(script):
    """途中まで書いて落ちた回を「レビュー完了」にしない。"""
    assert re.search(r"if \(\$rc -ne 0\) \{\s*\n\s*throw", script), \
        "異常終了で throw していない（巡を消化したことになってしまう）"


# --- ⛔ 書いてはいけない主張（8 巡目 P1 の再発防止） ---------------------------

def test_the_script_does_not_claim_a_read_boundary_it_does_not_have(script):
    """🔴 **canary で偽と確認済みの主張**を、コメントにも出力にも復活させない。

    実測（2026-08-16）: `-C <staging> -s read-only` で起動した Codex に
    staging の 3 階層上のファイルを読ませたところ、**中身をそのまま返した**。
    ⇒ 「Codex からは見えません」「そもそも見えなくする」は**嘘**になる。
    言ってよいのは「渡していない（運用規則）」まで。
    """
    for lie in ("Codex からは見えません", "そもそも見えなくする", "構造で塞ぐ"):
        assert lie not in script, (
            f"実在しない保証を書いている: {lie!r}。"
            "read-only は任意のパスを読めるので、staging は秘匿の保証ではない。"
        )


def test_the_disclosure_of_the_real_limit_is_present(script):
    """⚠️ 「無いことの検査」は、**在ることの検査**と対で置く。

    上のテストは嘘を禁じるだけなので、**黙って消す**とどちらも緑になる。
    実際の制約が書かれていることまで見る。
    """
    assert "保証ではありません" in script or "保証ではない" in script, \
        "読み取り範囲が狭まらない事実の開示が消えている"


def test_the_staging_lives_outside_the_repository(script):
    """staging をリポジトリの中に置かない（親を 1 つ上がると ISSUES.md に届く）。"""
    assert "GetTempPath" in script, "staging がリポジトリ外に置かれていない"
    assert "Join-Path $outDir 'doc_review'" not in script


def test_the_extension_version_is_compared_semantically(script):
    """`0.4.9` を `0.4.10` より新しいと読まないこと（8 巡目 P2）。"""
    assert "Sort-Object Name -Descending" not in script, "文字列順で版を選んでいる"
    assert "[version]" in script
