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
import sys
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


def test_no_absolute_home_path_is_baked_in(script):
    """⛔ 手元のユーザー名・ディレクトリ構成を公開物へ焼き込まない。

    このスクリプトは公開リポジトリに入る。初版は memory の場所を
    `C:\\Users\\<name>\\.claude\\...` と直書きしており、**git 履歴に残ると
    取り消せない**形だった（push 直前に気づいた）。
    ⇒ 宣言制（`RADIOSIM_MEMORY_DIR`）＋既定値の導出にする。
    """
    assert not re.search(r"[A-Za-z]:\\+Users\\+", script), \
        "ユーザーのホームパスが直書きされている"
    assert "RADIOSIM_MEMORY_DIR" in script, "memory の場所が宣言制になっていない"


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
# ⚠️ **受け取った入力文を必ず書き出す**（2026-08-16・Codex 12 巡目 P1）。
#    初版の stub は入力文を捨てていたので、「入力文が存在しないパスを指す」
#    という欠陥を検査が素通りした＝*代役が本物より寛容だと、検査は空振りする*。
$out = $null
for ($i = 0; $i -lt $args.Count; $i++) { if ($args[$i] -eq '-o') { $out = $args[$i + 1] } }
if ($env:STUB_PROMPT_OUT) {
    Set-Content -LiteralPath $env:STUB_PROMPT_OUT -Value $args[-1] -Encoding UTF8
}
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


def test_an_empty_raw_from_a_failed_run_does_not_block_the_retry(stub_codex, tmp_path):
    """⛔ **空の raw を残して落ちた回**が、巡番号を食って再実行を拒まないこと。

    9 巡目の直しは「非空なら退避」だったので、この経路（空ファイル＋異常終了）
    だけが素通りし、`…_codex_raw.md` が残って採番も再実行も詰まっていた。
    ⇒ **サイズによらず退避する**ことを、実際に失敗させて確かめる。

    ⚠️ **書き先は `-OutDir` で隔離する**（2026-08-16・Codex 11 巡目 P1）。初版は
    実リポジトリの `.qa/codex_review` へ書き、後始末で `round900_*` を一括削除して
    いた＝**正当な巡の成果物を消し得たし、並列実行では他ワーカーの作成中ファイルも
    消し得た**。*検査が本番の置き場を汚さない*ことは、検査自身の要件。
    """
    # ⚠️ **`pwsh` の有無で分岐してはいけない**（2026-08-16・CI が赤くなった）。
    #    GitHub の Linux ランナーには pwsh が入っているので skip が効かず、
    #    Windows 前提のスクリプト（`$env:USERPROFILE`・`\` 区切り）をそのまま
    #    実行して落ちた。**「道具が在るか」ではなく「その道具が意味を持つ OS か」**
    #    を見る（[[feedback-no-wsl-push]]＝この製品は Windows ネイティブ前提）。
    if sys.platform != "win32":
        pytest.skip("run.ps1 は Windows 前提（pwsh の有無では判定しない）")
    if not shutil.which("pwsh"):
        pytest.skip("pwsh が無い環境")
    if not SCRIPT.exists():
        pytest.skip("run.ps1 が無い環境")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    real_dir = ROOT / ".qa" / "codex_review"
    before = {p.name for p in real_dir.glob("*")} if real_dir.exists() else set()

    round_no = 900
    prompt_out = tmp_path / "prompt.txt"
    env = {**os.environ, "CODEX_EXE": str(stub_codex),
           "STUB_PROMPT_OUT": str(prompt_out)}
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(SCRIPT),
         "-Mode", "code", "-Base", "HEAD~1", "-Round", str(round_no),
         "-OutDir", str(out_dir)],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    made = sorted(out_dir.glob(f"round{round_no}_*"))

    assert proc.returncode != 0, "異常終了が失敗として扱われていない"

    # ⛔ 入力文が指す差分が**実在すること**（12 巡目 P1）。
    #    Codex の作業根は repo なので、相対なら repo から、絶対ならそのまま解決する。
    prompt = prompt_out.read_text(encoding="utf-8").strip()
    m = re.search(r"(\S*round900_diff\S*\.diff)", prompt)
    assert m, f"入力文が差分を名指ししていない: {prompt!r}"
    named = Path(m.group(1))
    resolved = named if named.is_absolute() else (ROOT / named)
    assert resolved.exists(), (
        f"入力文が存在しないパスを指している: {named} "
        "（-OutDir がリポジトリ外だと相対化が壊れる）"
    )
    assert not (out_dir / f"round{round_no}_code_codex_raw.md").exists(), (
        "空の raw が採番対象の名前のまま残っている＝同じ -Round で再実行できない"
    )
    assert any("_codex_FAILED_" in p.name for p in made), (
        f"失敗 raw が退避されていない: {[p.name for p in made]}"
    )
    # ⛔ 本番の置き場に 1 つも足していないこと（消していないことは自明にならない）
    after = {p.name for p in real_dir.glob("*")} if real_dir.exists() else set()
    assert after == before, f"検査が実リポジトリの置き場を触った: {after ^ before}"


def _run_with_stub(stub_codex, tmp_path, out_dir_arg, cwd, round_no):
    """stub の codex で 1 巡走らせ、`(proc, 入力文)` を返す。

    stub は必ず異常終了する（`_STUB`）ので、ここで見られるのは
    **Codex を呼ぶまでに組み立てたもの**＝入力文が名指しする差分のパス。
    """
    prompt_out = tmp_path / f"prompt_{round_no}.txt"
    env = {**os.environ, "CODEX_EXE": str(stub_codex),
           "STUB_PROMPT_OUT": str(prompt_out)}
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(SCRIPT),
         "-Mode", "code", "-Base", "HEAD~1", "-Round", str(round_no),
         "-OutDir", str(out_dir_arg)],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180,
    )
    prompt = prompt_out.read_text(encoding="utf-8").strip() if prompt_out.exists() else ""
    return proc, prompt


def _diff_named_by(prompt: str) -> Path:
    """入力文が名指しする差分のパス（Codex の作業根は repo なので相対は repo 基準）。"""
    m = re.search(r"(\S*round\d+_diff\S*\.diff)", prompt)
    assert m, f"入力文が差分を名指ししていない: {prompt!r}"
    named = Path(m.group(1))
    return named if named.is_absolute() else (ROOT / named)


def test_a_sibling_directory_is_not_taken_for_a_child_of_the_repository(stub_codex, tmp_path):
    r"""⛔ `<repo>-review` のような**兄弟**を「リポジトリ配下」と読まないこと（B-107）。

    文字列の前方一致だけで配下を判定していたので、`D:\dev\radiosim-repo` と
    `D:\dev\radiosim-repo-review` が「配下」になり、`Substring` が
    `-review\…diff` という**存在しないパス**を切って Codex に渡していた
    （＝差分を読めない巡になる）。境界（区切り文字）まで見るのが処方。

    ⚠️ **兄弟でなければ再現しない**ので、置き場はリポジトリの隣に作る
    （`tmp_path` では前方一致が起きない＝*代役が本物より寛容だと検査は空振りする*）。
    後始末は finally で必ず行う。
    """
    if sys.platform != "win32":
        pytest.skip("run.ps1 は Windows 前提（pwsh の有無では判定しない）")
    if not shutil.which("pwsh"):
        pytest.skip("pwsh が無い環境")
    if not SCRIPT.exists():
        pytest.skip("run.ps1 が無い環境")

    sibling = ROOT.parent / f"{ROOT.name}-review-pytest-{os.getpid()}"
    sibling.mkdir()
    try:
        proc, prompt = _run_with_stub(stub_codex, tmp_path, sibling, ROOT, 901)
        assert proc.returncode != 0, "異常終了が失敗として扱われていない"
        named = _diff_named_by(prompt)
        assert named.exists(), (
            f"入力文が存在しないパスを指している: {named} "
            "（兄弟ディレクトリを配下と誤判定して相対化した）"
        )
        assert named.parent.resolve() == sibling.resolve(), (
            f"差分が -OutDir の外に置かれた: {named}"
        )
    finally:
        shutil.rmtree(sibling, ignore_errors=True)


def test_a_relative_outdir_is_anchored_to_the_repository_not_the_caller(stub_codex, tmp_path):
    """相対の `-OutDir` の基準を**リポジトリに固定**すること（B-107・原文の後段）。

    基準が呼び出し元の作業ディレクトリだと、**同じ引数が呼ぶ場所で別の場所を指す**。
    ⇒ ここでは repo の外を作業ディレクトリにして走らせ、置き場が repo 基準で
    解決されることを見る（`.qa/` は git-ignore 済みなので後始末で消して足りる）。
    """
    if sys.platform != "win32":
        pytest.skip("run.ps1 は Windows 前提（pwsh の有無では判定しない）")
    if not shutil.which("pwsh"):
        pytest.skip("pwsh が無い環境")
    if not SCRIPT.exists():
        pytest.skip("run.ps1 が無い環境")

    rel = f".qa/codex_review_pytest_{os.getpid()}"
    expected = ROOT / rel
    stray = tmp_path / rel
    try:
        proc, prompt = _run_with_stub(stub_codex, tmp_path, rel, tmp_path, 902)
        assert proc.returncode != 0, "異常終了が失敗として扱われていない"
        assert not stray.exists(), (
            f"相対 -OutDir が呼び出し元の作業ディレクトリ基準で解決された: {stray}"
        )
        named = _diff_named_by(prompt)
        assert named.exists(), f"入力文が存在しないパスを指している: {named}"
        assert named.parent.resolve() == expected.resolve(), (
            f"相対 -OutDir がリポジトリ基準になっていない: {named}"
        )
    finally:
        shutil.rmtree(expected, ignore_errors=True)
