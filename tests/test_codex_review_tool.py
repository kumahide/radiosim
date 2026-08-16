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

import os
import re
import shutil
import subprocess
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
    assert "if ($rc -ne 0) {" in script and "throw (\"Codex が異常終了" in script, \
        "異常終了で throw していない（巡を消化したことになってしまう）"


def test_a_failed_round_does_not_consume_its_number(script):
    """⛔ 失敗した巡が採番を食わないこと（9 巡目 P2＝8 巡目の直しが作った矛盾）。

    採番は `*_codex_raw.md` を数えるので、失敗 raw をその名前のまま残すと
    ①次回が次の番号へ進み ②同じ `-Round` では「既にあります」で弾かれる
    ＝**成立していないと宣言した巡を、再試行できないまま消化する。**
    """
    assert "_codex_FAILED_" in script, "失敗 raw を採番対象外の名前へ退避していない"
    assert re.search(r"Move-Item[^\n]*rawPath", script), "失敗時に raw を退避していない"
    # ⚠️ 退避はサイズを条件にしない（10 巡目 P2＝空の raw が取りこぼされていた）
    assert "if (-not (Test-Path $script:rawPath)) { return $null }" in script, \
        "存在すれば必ず退避する形になっていない"
    # 退避先が採番の glob に掛からないこと（掛かると退避の意味が消える）
    assert "-Filter 'round*_codex_raw.md'" in script
    assert "_codex_FAILED_" not in "round*_codex_raw.md"


def test_the_diff_is_written_only_when_it_is_not_empty(script):
    """空の差分は、分かる文言で止める（base に HEAD 自身を渡した実例がある）。

    以前はパイプで直に書いており、空だとファイルが作られず `Get-Item` が
    「パスが存在しません」で落ちて、**base の誤りという本当の原因が見えなかった**。
    """
    assert "IsNullOrWhiteSpace($diffText)" in script
    assert "差分が空です" in script


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


def test_the_staging_name_is_unique_per_clone_and_process(script):
    """⛔ staging の固定名を禁じる（9 巡目 P2＝8 巡目の直しが作った退行）。

    `%TEMP%` は全 clone 共通なので、固定名だと別 clone や 2 本目の実行が
    **先発の staging を再帰削除**し、Codex が欠損中／別 clone の文書を読み得る。
    リポジトリ内に置いていた頃は clone ごとに分かれていた＝**外へ出したことで
    失った分離**を、名前で取り戻す。
    """
    assert "$PID" in script, "プロセスごとに分かれていない"
    assert "SHA1" in script and "repoTag" in script, "clone ごとに分かれていない"
    # 掃除は自分の clone の staging だけを対象にすること
    assert '-Filter "$stagePrefix*"' in script, "他 clone の staging まで消し得る"


def test_the_extension_version_is_compared_semantically(script):
    """`0.4.9` を `0.4.10` より新しいと読まないこと（8 巡目 P2）。"""
    assert "Sort-Object Name -Descending" not in script, "文字列順で版を選んでいる"
    assert "[version]" in script


# --- 実際に走らせる検査（10 巡目の指摘＝文字列照合では実行時条件を見られない） ---
#
# 🔑 Codex 10 巡目の締めの一言が正しかった＝「関連テスト13件は成功しましたが、
#    現在の文字列ベースのテストでは上記の実行時条件を検出できていません」。
#    **上の検査群は「そう書いてあるか」しか見ていない。** 失敗経路の後始末は
#    実際に失敗させないと分からないので、stub の codex を噛ませて 1 本通す。

_STUB = r"""
# 失敗する codex の代役: -o のパスへ空ファイルを作り、非ゼロで終わる。
$out = $null
for ($i = 0; $i -lt $args.Count; $i++) { if ($args[$i] -eq '-o') { $out = $args[$i + 1] } }
if ($out) { New-Item -ItemType File -Path $out -Force | Out-Null }
exit 3
"""


@pytest.fixture
def stub_codex(tmp_path):
    """`codex.exe` の代役。隣に code-mode host のダミーも要る（起動前の検査）。"""
    stub = tmp_path / "codex.ps1"
    stub.write_text(_STUB, encoding="utf-8")
    (tmp_path / "codex-code-mode-host.exe").write_bytes(b"")
    return stub


def test_an_empty_raw_from_a_failed_run_does_not_block_the_retry(stub_codex):
    """⛔ **空の raw を残して落ちた回**が、巡番号を食って再実行を拒まないこと。

    9 巡目の直しは「非空なら退避」だったので、この経路（空ファイル＋異常終了）
    だけが素通りし、`…_codex_raw.md` が残って採番も再実行も詰まっていた。
    ⇒ **サイズによらず退避する**ことを、実際に失敗させて確かめる。
    """
    if not shutil.which("pwsh"):
        pytest.skip("pwsh が無い環境")
    if not SCRIPT.exists():
        pytest.skip("run.ps1 が無い環境")

    out_dir = ROOT / ".qa" / "codex_review"
    round_no = 900  # 実運用の採番と衝突しない番号
    raw = out_dir / f"round{round_no}_code_codex_raw.md"
    made: list[Path] = []
    try:
        env = {**os.environ, "CODEX_EXE": str(stub_codex)}
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(SCRIPT),
             "-Mode", "code", "-Base", "HEAD~1", "-Round", str(round_no)],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
        )
        made = sorted(out_dir.glob(f"round{round_no}_*"))

        assert proc.returncode != 0, "異常終了が失敗として扱われていない"
        assert not raw.exists(), (
            "空の raw が採番対象の名前のまま残っている＝同じ -Round で再実行できない"
        )
        assert any("_codex_FAILED_" in p.name for p in made), (
            f"失敗 raw が退避されていない: {[p.name for p in made]}"
        )
    finally:
        for p in made:
            p.unlink(missing_ok=True)
        raw.unlink(missing_ok=True)
