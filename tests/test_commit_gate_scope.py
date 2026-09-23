"""
tests/test_commit_gate_scope.py
===============================
コミット前ゲートの**島の中身**を守る＝`gate-scope.json` の `commit_islands` に
2026-09-23 に足した `docs` と `qa-gate` が、**絞りすぎていないか／絞れているか**。

⚠️ **仕組みそのもの**（`islandFor` / `commitPaths` / `islandTargets` の振る舞いと、
全体走査テストの取りこぼし）は `tests/test_pre_commit_gate.py` の担当。ここは
**新しい 2 つの島が何を名乗っているか**だけを見る（二重に持たない）。

**何を直したか**: 毎ターンのゲートは `gate-scope.json` で範囲を絞るのに、
`pre-commit-gate.mjs` は島に収まらない限り無条件にフルスイートを回していた。島は
Field 用に 1 つあるだけで、**ドキュメントだけのコミットには島が無かった**。実害＝
`3.5` のリリースで、版文字列 1 行＋マニュアル 3 箇所の変更に対して 8 分のフルスイートを
3 回（約 24 分）回した。赤の理由はリモートデスクトップ由来のフレークと台帳の記帳漏れで、
どちらも 3251 本を要する種類ではなかった。

⛔ **版文字列（`core/version.py`）は島に入れていない**＝製品コードで、どのテストが
影響を受けるかを言い尽くせない。⇒ リリースのコミットは今までどおりフル。**速くなるのは
「マニュアルだけを直す回」「ゲートの道具だけを直す回」**で、リリース本体ではない。

**ゲートの壊れ方 3 種**（[[feedback-promote-recurring-checks]]・新ゲートの必須検証）:

1. **一度も落ちない**（＝島に当たらずいつもフルで、何も速くならない）
   → `TestTheNewIslandsActuallyCatch`
2. **毎回鳴る**（＝何でも島に収まってフルの保証が消える）
   → `TestTheNewIslandsDoNotOverreach`
3. **間違ったものを要求している**（＝島の tests が実態より薄い）
   → `TestDocsIslandCoversEveryTestThatReadsDocs`（「無いことの検査」の対）

`node` が無い環境ではまとめて skip（判定は .mjs 側にあり、Python からは再実装しない）。
"""

import ast
import json
import os
import re
import shutil
import subprocess

import pytest

from conftest import structural_skip

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_GATE = os.path.join(_REPO, "tools", "qa-hook", "pre-commit-gate.mjs")
_SCOPE = os.path.join(_REPO, "tools", "qa-hook", "gate-scope.json")

pytestmark = [
    pytest.mark.skipif(
        not os.path.exists(_GATE),
        reason=structural_skip("QA ゲート本体が無い環境（ローカルのみ検証する）。"),
    ),
    pytest.mark.skipif(shutil.which("node") is None, reason="node が無い環境"),
]


def _scope() -> dict:
    with open(_SCOPE, encoding="utf-8") as f:
        return json.load(f)


def _island(name: str) -> dict:
    for island in _scope()["commit_islands"]:
        if island["name"] == name:
            return island
    raise AssertionError(f"島「{name}」が gate-scope.json に無い")


def _island_for(paths: list[str]):
    """実物の対応表に対して `islandFor()` を評価し、当たった島の名前か `None`。"""
    src = _GATE.replace("\\", "/")
    probe = os.path.join(_REPO, "_island_probe.mjs")
    with open(probe, "w", encoding="utf-8") as f:
        f.write(f'import {{ islandFor }} from "file:///{src}";\n')
        f.write("import { readFileSync } from \"node:fs\";\n")
        f.write(f"const scope = JSON.parse(readFileSync({json.dumps(_SCOPE)}, 'utf-8'));\n")
        f.write(f"const hit = islandFor(scope, {json.dumps(paths)});\n")
        f.write("process.stdout.write(JSON.stringify(hit ? hit.name : null));\n")
    try:
        r = subprocess.run(["node", probe], cwd=_REPO, capture_output=True,
                           text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)
    finally:
        os.remove(probe)


class TestTheNewIslandsActuallyCatch:
    """壊れ方①＝島に当たらず、いつもフルへ倒れて何も速くならない。"""

    def test_a_manual_only_commit_lands_in_the_docs_island(self):
        """🔴 **実際に払ったコスト**（2026-09-23・`3.5`）＝マニュアル 3 箇所の変更に
        フルスイート（3251 本）を回した。"""
        assert _island_for(["docs/manual_ja.md", "docs/manual_en.md"]) == "docs"

    def test_changelog_and_readme_land_in_the_docs_island(self):
        assert _island_for(["CHANGELOG.md", "README.md"]) == "docs"

    def test_a_gate_tooling_commit_lands_in_the_qa_gate_island(self):
        """B-277 のコミット（テスト 1 本＋チェックリスト 1 行）と同じ形。"""
        assert _island_for(["tools/qa-hook/release-checklist.txt",
                            "tests/test_claude_hooks.py"]) == "qa-gate"

    def test_every_island_prefix_that_names_a_test_file_runs_that_file(self):
        """⛔ 島の prefixes に挙げたテストは、その島の tests に入っていること。

        入れ忘れると「そのテストを直したコミットが、**直したテストを走らせずに**
        通る」＝いちばん気づけない素通しになる。
        """
        for island in _scope()["commit_islands"]:
            named = [p for p in island["prefixes"]
                     if p.startswith("tests/") and p.endswith(".py")]
            listed = set(island["tests"])
            missing = [p for p in named if p not in listed]
            assert not missing, (
                f"島「{island['name']}」の prefixes にあるが tests に無い: {missing}")


class TestTheNewIslandsDoNotOverreach:
    """壊れ方②＝何でも島に収まって、フルスイートの保証が消える。"""

    @pytest.mark.parametrize("paths", [
        pytest.param(["core/models.py"], id="製品コード"),
        pytest.param(["core/version.py"], id="版文字列＝リリースのコミット"),
        pytest.param(["views/launcher.py"], id="画面"),
        pytest.param(["tests/conftest.py"], id="conftest＝全テストの入力"),
        pytest.param(["lang/ja.json"], id="文言＝長さがウィンドウ寸法に効く"),
        pytest.param(["tests/test_models.py"], id="島の道具ではないテスト"),
        pytest.param(["radiosim.spec"], id="同梱の宣言"),
    ])
    def test_unlisted_faces_fall_back_to_full(self, paths):
        assert _island_for(paths) is None, f"{paths} が島に収まっている＝フルの保証が消える"

    def test_one_outside_path_spoils_the_whole_commit(self):
        """⛔ **1 つでも島の外なら フルへ**＝説明できる面と混ざっても緩めない。
        （`3.5` のリリースは版文字列とマニュアルが混ざるので、ここでフルへ倒れる。）
        """
        assert _island_for(["docs/manual_ja.md", "core/version.py"]) is None

    def test_no_island_covers_product_code(self):
        """島の宣言そのものの見張り＝製品コードの面を島にさせない。"""
        for island in _scope()["commit_islands"]:
            for prefix in island["prefixes"]:
                for forbidden in ("core/", "views/", "report/", "lang/",
                                  "tests/conftest"):
                    assert not (prefix.startswith(forbidden)
                                or forbidden.startswith(prefix)), (
                        f"島「{island['name']}」が {prefix} を名乗っている＝"
                        "『全体が緑』を証明すべき面を、島のテスト数本で済ませている")


# --- 島の tests が実態より薄くなっていないか（「無いことの検査」の対） -----------

_DOCS_PATH = re.compile(r"^docs/[\w./-]+$")


def _tests_that_read_docs() -> set[str]:
    """`tests/test_*.py` のうち、**docs/ のファイルを実際に名指ししている**もの。

    判定は AST の文字列定数だけを見る（コメントは AST に残らず、docstring は
    明示的に除く）＝散文で `docs/glossary.md` と書いただけの説明は数えない。
    拾うのは 2 形＝①`"docs/manual_ja.md"` のようなリポジトリ相対のパス
    ②`... / "docs" / "glossary.md"` のような `/` 連結の部品。
    """
    found = set()
    tests_dir = os.path.join(_REPO, "tests")
    for name in sorted(os.listdir(tests_dir)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        path = os.path.join(tests_dir, name)
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
                if ast.get_docstring(node, clean=False) is not None:
                    docstrings.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in docstrings and _DOCS_PATH.match(node.value):
                    found.add(f"tests/{name}")
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                for side in (node.left, node.right):
                    if isinstance(side, ast.Constant) and side.value == "docs":
                        found.add(f"tests/{name}")
    return found


class TestDocsIslandCoversEveryTestThatReadsDocs:
    """壊れ方③＝間違ったものを要求している（島の tests が実態より薄い）。

    🔑 **開示を書く仕事には「無いことの検査」を対で置く**
    （[[feedback-promote-recurring-checks]]）。ここでいう開示は「docs/ を触ったら
    走らせれば十分なのはこの N 本」という宣言で、対になる検査は
    「**それ以外に docs/ を読んでいるテストが無いこと**」。島の tests を薄いまま
    放置すると、ドキュメントだけのコミットで配布物の穴が素通りする
    （同梱一覧・用語集・帳票の文言はどれも公開文書を読んで判定している）。
    """

    def test_every_test_that_names_a_docs_path_is_in_the_docs_island(self):
        listed = set(_island("docs")["tests"]) | {_scope()["always_tests"]}
        missing = sorted(_tests_that_read_docs() - listed)
        assert not missing, (
            "docs/ のファイルを名指ししているのに、docs 島の tests に無い: "
            f"{missing}\n"
            "⇒ gate-scope.json の島「docs」へ足すこと（薄いままにすると、"
            "ドキュメントだけのコミットでこの検査が素通りする）")

    def test_the_same_holds_for_the_extra_gates_row(self):
        """毎ターンのゲート側（`extra_gates` の docs/ 行）も同じ広さを持つこと。

        ⚠️ **島と対応表は別の口**＝片方だけ広げると、毎ターンは通るのに
        コミットで落ちる（またはその逆）という食い違いになる。
        """
        rows = [tests for prefixes, tests in _scope()["extra_gates"]
                if "docs/" in prefixes]
        assert rows, "extra_gates に docs/ の行が無い"
        listed = set(rows[0]) | {_scope()["always_tests"]}
        # glossary.md 専用の行は docs/ の行の下にぶら下がるので、そちらも足す
        for prefixes, tests in _scope()["extra_gates"]:
            if any(p.startswith("docs/") for p in prefixes):
                listed |= set(tests)
        missing = sorted(_tests_that_read_docs() - listed)
        assert not missing, f"extra_gates の docs/ 側に無い: {missing}"

    def test_the_detector_itself_finds_the_known_readers(self):
        """検出器が空振りしていないこと（①の見かけを防ぐ）。"""
        found = _tests_that_read_docs()
        for known in ("tests/test_docs_consistency.py",
                      "tests/test_i18n_glossary.py",
                      "tests/test_report.py"):
            assert known in found, f"{known} を拾えていない＝検出器が壊れている"

    def test_prose_only_mentions_are_not_counted(self):
        """散文で docs/ に触れただけのテストは数えない（毎回鳴るのを防ぐ）。"""
        found = _tests_that_read_docs()
        assert "tests/test_dem.py" not in found, "コメントを拾っている"
        assert "tests/test_i18n_external.py" not in found, "docstring を拾っている"
