"""
tests/test_commit_gate_scope.py
===============================
コミット前ゲートの**部分テストの中身**を守る＝`gate-scope.json` の `commit_islands` に
2026-09-23 に足した `docs` と `qa-gate` が、**絞りすぎていないか／絞れているか**。

⚠️ **仕組みそのもの**（`islandFor` / `commitPaths` / `islandTargets` の振る舞いと、
全体走査テストの取りこぼし）は `tests/test_pre_commit_gate.py` の担当。ここは
**新しい 2 つの部分テストが何を名乗っているか**だけを見る（二重に持たない）。

**何を直したか**: 毎ターンのゲートは `gate-scope.json` で範囲を絞るのに、
`pre-commit-gate.mjs` は部分テストに収まらない限り無条件にフルスイートを回していた。部分テストは
Field 用に 1 つあるだけで、**ドキュメントだけのコミットには部分テストが無かった**。実害＝
`3.5` のリリースで、版文字列 1 行＋マニュアル 3 箇所の変更に対して 8 分のフルスイートを
3 回（約 24 分）回した。赤の理由はリモートデスクトップ由来のフレークと台帳の記帳漏れで、
どちらも 3251 本を要する種類ではなかった。

⛔ **版文字列（`core/version.py`）は部分テストに入れていない**＝製品コードで、どのテストが
影響を受けるかを言い尽くせない。⇒ リリースのコミットは今までどおりフル。**速くなるのは
「マニュアルだけを直す回」「ゲートの道具だけを直す回」**で、リリース本体ではない。
⚠️ 例外は `APP_VERSION` の 1 行だけの差（I-185＝版の字の読み手だけ）。その判定は
`tests/test_pre_commit_gate.py` の担当＝ここの探り針は `versionOnly: false` で固定する。

**ゲートの壊れ方 3 種**（[[feedback-promote-recurring-checks]]・新ゲートの必須検証）:

1. **一度も落ちない**（＝部分テストに当たらずいつもフルで、何も速くならない）
   → `TestTheNewIslandsActuallyCatch`
2. **毎回鳴る**（＝何でも部分テストに収まってフルの保証が消える）
   → `TestTheNewIslandsDoNotOverreach`
3. **間違ったものを要求している**（＝部分テストの tests が実態より薄い）
   → `TestDocsIslandCoversEveryTestThatReadsDocs`（「無いことの検査」の対）

**2026-09-23 追記**: 「部分テスト 1 つに収まらなければフル」をやめ、フルは `full_prefixes`
（製品コード・テストの共通部品・依存と pytest の設定）と未知のパスだけにした。
それ以外は部分テスト・変えたテスト・`scoped_faces` の読み手の和集合（`TestTheNewIslandsActuallyCatch`
の後半 3 本と `test_tests_walkers_cover_every_test_that_walks_tests_dir`）。

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
    raise AssertionError(f"部分テスト「{name}」が gate-scope.json に無い")


def _island_for(paths: list[str]):
    """実物の対応表に対して `islandFor()` を評価し、当たった部分テストの名前か `None`。"""
    hit = _plan(paths)
    return hit["name"] if hit else None


def _plan(paths: list[str]):
    """実物の対応表に対して `islandFor()` を評価し、`{name, tests}` か `None`（フル）。"""
    src = _GATE.replace("\\", "/")
    probe = os.path.join(_REPO, "_island_probe.mjs")
    with open(probe, "w", encoding="utf-8") as f:
        f.write(f'import {{ islandFor }} from "file:///{src}";\n')
        f.write("import { readFileSync } from \"node:fs\";\n")
        f.write(f"const scope = JSON.parse(readFileSync({json.dumps(_SCOPE)}, 'utf-8'));\n")
        # versionOnly を固定する＝省くと作業ツリーの差から決まり、版の字だけを上げた
        # 作業ツリー（＝`X.Ya1` の宣言のコミット）でだけ結論が変わる（2026-09-26・`3.8a1`）
        f.write(f"const hit = islandFor(scope, {json.dumps(paths)}, process.cwd(),"
                " { versionOnly: false });\n")
        f.write("process.stdout.write(JSON.stringify(hit));\n")
    try:
        r = subprocess.run(["node", probe], cwd=_REPO, capture_output=True,
                           text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)
    finally:
        os.remove(probe)


class TestTheNewIslandsActuallyCatch:
    """壊れ方①＝部分テストに当たらず、いつもフルへ倒れて何も速くならない。"""

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

    def test_a_field_only_commit_lands_in_the_field_island(self):
        """Field のコミットに 9 分のフルを払わせない（2026-09-19 に部分テストを作った理由）。"""
        assert _island_for(["apps/field/cli.py", "tests/test_field_session.py"]) == "field"

    def test_a_test_only_commit_runs_just_that_test(self):
        """🔴 **実際に払ったコスト**（2026-09-23）＝`.gitignore` 1 行とテスト 2 本の
        コミットでフルスイートが走った。⇒ 変えたテストそのもの＋走査系だけ。"""
        hit = _plan(["tests/test_models.py"])
        assert hit["name"] == "tests"
        assert "tests/test_models.py" in hit["tests"]
        assert "tests/test_report.py" not in hit["tests"]

    def test_gitignore_runs_its_readers(self):
        hit = _plan([".gitignore", "tests/test_docs_consistency.py",
                     "tests/test_claude_hooks.py"])
        assert hit is not None, "2026-09-23 のコミットがまだフルへ倒れる"
        assert "tests/test_qa_gate_cache.py" in hit["tests"], ".gitignore の読み手を拾っていない"

    def test_islands_mix_by_union(self):
        """部分テストをまたぐコミット（マニュアル＋ゲートの道具）も、両方の tests の和で済む。"""
        hit = _plan(["docs/manual_ja.md", "tools/qa-hook/gate.mjs"])
        assert set(hit["name"].split("+")) == {"docs", "qa-gate"}
        assert {"tests/test_report.py", "tests/test_qa_gate_cache.py"} <= set(hit["tests"])

    def test_every_island_prefix_that_names_a_test_file_runs_that_file(self):
        """⛔ 部分テストの prefixes に挙げたテストは、その部分テストの tests に入っていること。

        入れ忘れると「そのテストを直したコミットが、**直したテストを走らせずに**
        通る」＝いちばん気づけない素通しになる。
        """
        for island in _scope()["commit_islands"]:
            named = [p for p in island["prefixes"]
                     if p.startswith("tests/") and p.endswith(".py")]
            listed = set(island["tests"])
            missing = [p for p in named if p not in listed]
            assert not missing, (
                f"部分テスト「{island['name']}」の prefixes にあるが tests に無い: {missing}")


class TestTheNewIslandsDoNotOverreach:
    """壊れ方②＝何でも部分テストに収まって、フルスイートの保証が消える。"""

    @pytest.mark.parametrize("paths", [
        pytest.param(["core/models.py"], id="製品コード"),
        pytest.param(["core/version.py"], id="版文字列＝リリースのコミット"),
        pytest.param(["views/launcher.py"], id="画面"),
        pytest.param(["tests/conftest.py"], id="conftest＝全テストの入力"),
        pytest.param(["lang/ja.json"], id="文言＝長さがウィンドウ寸法に効く"),
        pytest.param(["tests/table_fit.py"], id="テストの共有ヘルパ"),
        pytest.param(["pyproject.toml"], id="pytest の設定"),
        pytest.param(["requirements.txt"], id="依存"),
        pytest.param(["icon.png"], id="同梱資産"),
        pytest.param(["somewhere_new/x.py"], id="どの規則にも無い面"),
        pytest.param(["apps/other/x.py"], id="Field 以外のアプリ"),
        pytest.param(["requirements-field.txt"], id="Field の依存も依存"),
    ])
    def test_unlisted_faces_fall_back_to_full(self, paths):
        assert _island_for(paths) is None, f"{paths} が部分テストに収まっている＝フルの保証が消える"

    def test_one_outside_path_spoils_the_whole_commit(self):
        """⛔ **1 つでも部分テストの範囲外なら フルへ**＝説明できる面と混ざっても緩めない。
        （`3.5` のリリースは版文字列とマニュアルが混ざるので、ここでフルへ倒れる。）
        """
        assert _island_for(["docs/manual_ja.md", "core/version.py"]) is None

    def test_no_island_covers_product_code(self):
        """部分テストと範囲で済む面の宣言そのものの見張り＝製品コードの面を絞らせない。

        例外は 1 つ＝部分テスト `field` の `apps/field/`（2026-09-27）。本体が import しない
        別アプリで、`apps/` の下のほかのアプリは部分テストに当たらずフルへ倒れる
        （`test_unlisted_faces_fall_back_to_full` の「Field 以外のアプリ」）。
        """
        allowed = {("field", "apps/field/")}
        prefixes = [(i["name"], p) for i in _scope()["commit_islands"] for p in i["prefixes"]]
        prefixes += [("scoped_faces", p) for p in _scope()["scoped_faces"]]
        for name, prefix in prefixes:
            if (name, prefix) in allowed:
                continue
            for forbidden in ("core/", "views/", "report/", "lang/", "apps/",
                              "main.py", "tests/conftest", "requirements",
                              "pyproject.toml"):
                assert not (prefix.startswith(forbidden)
                            or forbidden.startswith(prefix)), (
                    f"「{name}」が {prefix} を名乗っている＝"
                    "『全体が緑』を証明すべき面を、数本のテストで済ませている")


# --- O-030＝製品のコードだけのコミットは、フルから旧画面と道具の試験を引いたもの ------------

def _commit_plan(paths: list[str], push: bool = False):
    """実物の対応表と実物の tests/ に対して `commitPlan()` を評価する（`None`＝フル）。"""
    src = _GATE.replace("\\", "/")
    probe = os.path.join(_REPO, "_plan_probe.mjs")
    with open(probe, "w", encoding="utf-8") as f:
        f.write(f'import {{ commitPlan }} from "file:///{src}";\n')
        f.write("import { readFileSync } from \"node:fs\";\n")
        f.write(f"const scope = JSON.parse(readFileSync({json.dumps(_SCOPE)}, 'utf-8'));\n")
        f.write(f"const p = commitPlan(scope, {json.dumps(paths)}, process.cwd(),"
                f" {{ push: {json.dumps(push)}, versionOnly: false }});\n")
        f.write("process.stdout.write(JSON.stringify(p));\n")
    try:
        r = subprocess.run(["node", probe], cwd=_REPO, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)
    finally:
        os.remove(probe)


def _skipped() -> list[str]:
    fm = _scope()["full_minus"]
    return fm["skip_old_ui"] + fm["skip_tools"]


def _real_test_files() -> set[str]:
    return {f"tests/{n}" for n in os.listdir(os.path.join(_REPO, "tests"))
            if n.startswith("test_") and n.endswith(".py")}


def _imported_roots(path: str) -> set[str]:
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
    return roots


def _reads_the_real_product(fn_src: str) -> bool:
    """引いたファイルの試験 1 本が、一時フォルダでなく本物の製品の木を読むか（字面で見る）。"""
    return "_REPO" in fn_src and bool(re.search(
        r"""["'](?:core|report|views)["'/]|versionReaders|productSources""", fn_src))


class TestFullMinus:
    """壊れ方 4 種で `full_minus` を見る（[[feedback-promote-recurring-checks]]）。"""

    def test_a_core_only_commit_takes_full_minus(self):
        """①一度も落ちない（＝いつもフルで速くならない）の対。"""
        plan = _commit_plan(["core/models.py"])
        assert plan and plan["name"] == "full-minus"
        assert not set(plan["tests"]) & set(_skipped())

    def test_a_core_and_test_commit_runs_the_walkers(self):
        """試験を変えたコミットは tests_walkers を全部走らせる（B-368 と同じ）。"""
        plan = _commit_plan(["core/models.py", "tests/test_models.py"])
        assert plan and plan["name"] == "full-minus"
        missing = [t for t in _scope()["tests_walkers"] if t not in plan["tests"]]
        assert not missing, f"tests_walkers が抜ける: {missing}"

    def test_nothing_but_the_two_lists_is_dropped(self):
        """③間違ったものを要求する（＝一覧の外まで落ちる）の対＝引いたのは 2 つの一覧だけ。"""
        plan = _commit_plan(["report/pdf.py"])
        files = {t for t in plan["tests"] if "::" not in t}
        assert files | set(_skipped()) == _real_test_files()
        for kept in _scope()["full_minus"]["keep"]:
            if kept.split("::")[0] in _skipped():
                assert kept in plan["tests"], f"{kept} が戻っていない"

    @pytest.mark.parametrize("paths", [
        pytest.param(["core/models.py", "views/launcher.py"], id="画面"),
        pytest.param(["core/models.py", "lang/ja.json"], id="文言"),
        pytest.param(["core/models.py", "main.py"], id="起動"),
        pytest.param(["core/models.py", "icon.png"], id="画面の資産"),
        pytest.param(["core/models.py", "tests/conftest.py"], id="conftest"),
        pytest.param(["core/models.py", "tests/data/x.json"], id="試験の入力"),
        pytest.param(["core/models.py", "tests/table_fit.py"], id="試験の共有ヘルパ"),
        pytest.param(["core/models.py", "pyproject.toml"], id="pytest の設定"),
        pytest.param(["core/models.py", "requirements.txt"], id="依存"),
        pytest.param(["core/models.py", "docs/manual_ja.md"], id="文書"),
        pytest.param(["core/models.py", "tools/qa-hook/gate-scope.json"], id="ゲートの道具"),
        pytest.param(["core/models.py", "apps/field/cli.py"], id="Field"),
    ])
    def test_any_other_face_falls_back_to_full(self, paths):
        """②毎回鳴る（＝何でも絞れてフルの保証が消える）の対。"""
        assert _commit_plan(paths) is None, f"{paths} がフルへ倒れない"

    def test_a_changed_skipped_test_runs(self):
        plan = _commit_plan(["core/dem.py", "tests/test_window_fit.py"])
        assert "tests/test_window_fit.py" in plan["tests"]

    def test_push_is_always_full(self):
        """④鳴っても止まらない（＝push で絞られて全体が緑の証明が消える）の対。"""
        assert _commit_plan(["core/models.py"], push=True) is None

    def test_scanners_and_layer_checks_are_never_dropped(self):
        scope = _scope()
        must = set(scope["scanners"]) | {scope["always_tests"],
                                         "tests/test_smoke.py", "tests/test_layers.py"}
        assert not must & set(_skipped())

    def test_every_listed_file_and_test_exists(self):
        """一覧が腐って空振りしない（撤去の回に名前だけ残る、など）。"""
        fm = _scope()["full_minus"]
        for rel in _skipped():
            assert os.path.exists(os.path.join(_REPO, rel)), f"{rel} が無い"
        for node in fm["keep"]:
            rel, name = node.split("::")
            with open(os.path.join(_REPO, rel), encoding="utf-8") as f:
                assert re.search(rf"^\s*def {name}\(", f.read(), re.M), f"{node} が無い"

    def test_old_ui_list_is_only_old_ui_tests(self):
        """skip_old_ui は旧画面（Tk）の試験だけ＝画面の部品を読まない計算の試験を混ぜない。"""
        for rel in _scope()["full_minus"]["skip_old_ui"]:
            roots = _imported_roots(os.path.join(_REPO, rel))
            assert roots & {"views", "tkinter"}, f"{rel} は旧画面を読まない＝外す理由が無い"

    def test_tools_list_does_not_import_the_product(self):
        for rel in _scope()["full_minus"]["skip_tools"]:
            roots = _imported_roots(os.path.join(_REPO, rel))
            hit = roots & {"core", "report", "views", "apps", "main"}
            assert not hit, f"{rel} が製品を import している: {hit}"

    def test_tools_tests_that_read_the_real_product_are_kept(self):
        """import しなくても、本物の製品の木を読む 1 本は keep で戻すこと。"""
        keep = set(_scope()["full_minus"]["keep"])
        found = set()
        for rel in _scope()["full_minus"]["skip_tools"]:
            path = os.path.join(_REPO, rel)
            with open(path, encoding="utf-8") as f:
                src = f.read()
            for node in ast.walk(ast.parse(src, filename=path)):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    if _reads_the_real_product(ast.get_source_segment(src, node) or ""):
                        found.add(f"{rel}::{node.name}")
        assert "tests/test_pre_commit_gate.py::test_tests_that_hard_code_the_version_are_readers" \
            in found, "検出器が空振りしている"
        assert found <= keep, f"本物の製品を読むのに keep に無い: {sorted(found - keep)}"


# --- 部分テストの tests が実態より薄くなっていないか（「無いことの検査」の対） -----------

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


def test_tests_walkers_cover_every_test_that_walks_tests_dir():
    """変えたテストだけを走らせるとき、`tests/` を歩いて全テストを検査するものが
    漏れていないこと（漏れると、テストを 1 本足したコミットでその検査が素通りする）。"""
    scope = _scope()
    listed = {t.split("::")[0] for t in scope["tests_walkers"]}
    listed |= set(scope["scanners"]) | {scope["always_tests"]}
    walkers = set()
    tests_dir = os.path.join(_REPO, "tests")
    for name in os.listdir(tests_dir):
        if name.startswith("test_") and name.endswith(".py"):
            with open(os.path.join(tests_dir, name), encoding="utf-8") as f:
                if re.search(r"listdir\((tests_dir|os\.path\.join\(_REPO, \"tests\"\))", f.read()):
                    walkers.add(f"tests/{name}")
    assert walkers, "検出器が空振りしている"
    missing = sorted(walkers - listed)
    assert not missing, f"tests/ を歩くのに tests_walkers に無い: {missing}"


def test_a_test_file_in_an_island_still_runs_tests_walkers():
    """🔴 B-368（2026-10-24・Codex 174 巡目）＝部分テストの prefixes に当たる試験ファイルは、
    `islandFor()` が部分テストの枝で次へ進み、`tests_walkers` を足していなかった
    （`qa-gate` の 7 本で `test_window_fit.py` の 2 本が抜けた）。部分テストに当たっても
    当たらなくても、試験ファイルを変えたコミットは走査系を全部走らせること。"""
    scope = _scope()
    samples = [(i["name"], p if p.endswith(".py") else f"{p}probe.py")
               for i in scope["commit_islands"] for p in i["prefixes"]
               if p.startswith("tests/test_")]
    assert samples, "tests/ の prefixes を持つ部分テストが無い＝見張りが空振りしている"
    for name, path in samples:
        hit = _plan([path])
        assert hit is not None, f"{path} がフルへ倒れた＝見張りの前提が崩れた"
        missing = [t for t in scope["tests_walkers"] if t not in hit["tests"]]
        assert not missing, f"部分テスト「{name}」の {path} で tests_walkers が抜ける: {missing}"


class TestDocsIslandCoversEveryTestThatReadsDocs:
    """壊れ方③＝間違ったものを要求している（部分テストの tests が実態より薄い）。

    🔑 **開示を書く仕事には「無いことの検査」を対で置く**
    （[[feedback-promote-recurring-checks]]）。ここでいう開示は「docs/ を触ったら
    走らせれば十分なのはこの N 本」という宣言で、対になる検査は
    「**それ以外に docs/ を読んでいるテストが無いこと**」。部分テストの tests を薄いまま
    放置すると、ドキュメントだけのコミットで配布物の穴が素通りする
    （同梱一覧・用語集・帳票の文言はどれも公開文書を読んで判定している）。
    """

    def test_every_test_that_names_a_docs_path_is_in_the_docs_island(self):
        listed = set(_island("docs")["tests"]) | {_scope()["always_tests"]}
        missing = sorted(_tests_that_read_docs() - listed)
        assert not missing, (
            "docs/ のファイルを名指ししているのに、部分テスト「docs」の tests に無い: "
            f"{missing}\n"
            "⇒ gate-scope.json の部分テスト「docs」へ足すこと（薄いままにすると、"
            "ドキュメントだけのコミットでこの検査が素通りする）")

    def test_the_same_holds_for_the_extra_gates_row(self):
        """毎ターンのゲート側（`extra_gates` の docs/ 行）も同じ広さを持つこと。

        ⚠️ **部分テストと対応表は別の口**＝片方だけ広げると、毎ターンは通るのに
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
