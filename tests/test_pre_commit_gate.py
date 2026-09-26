"""
tests/test_pre_commit_gate.py
=============================
コミット前ゲート（`tools/qa-hook/pre-commit-gate.mjs`）の「島」の判定を守る。

**何を守るか**: 2026-09-19 から、コミットの変更がすべて `gate-scope.json` の
`commit_islands` の 1 つ（例: Field）に収まるときだけ、フルスイートの代わりに
その島のテストを回す（Field のコミットが毎回 9 分のフルを払っていた）。
⇒ **このテストが守るのは「速さ」ではなく「絞りすぎないこと」**:

1. 島の外が 1 つでも混じったらフル（`islandFor` が null）。
2. リネームは元のパスも数える（core/ から島へ移すのは島だけの変更ではない）。
3. 変更が空ならフル（絞る根拠が無い）。
4. リポジトリ全体を走査するテストは島のテストに必ず入っている
   （島の中の変更でも落とし得るのはそれらだけ＝漏れたら赤を素通しする）。
"""

import json
import os
import re
import shutil
import subprocess

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_GATE = os.path.join(_REPO, "tools", "qa-hook", "pre-commit-gate.mjs")
_SCOPE = os.path.join(_REPO, "tools", "qa-hook", "gate-scope.json")

pytestmark = [
    pytest.mark.skipif(not os.path.exists(_GATE), reason="QA ゲート本体が無い環境"),
    pytest.mark.skipif(shutil.which("node") is None, reason="node が無い環境"),
]


def _node(script: str, cwd: str) -> str:
    src = _GATE.replace("\\", "/")
    path = os.path.join(cwd, "_probe_gate.mjs")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'import {{ commitPaths, islandFor, islandTargets, isCommitOrPush, isPush }} '
                f'from "file:///{src}";\n')
        f.write(script)
    try:
        out = subprocess.run(["node", path], cwd=cwd, capture_output=True, text=True,
                             check=True, timeout=60)
    finally:
        os.remove(path)
    return out.stdout.strip()


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


_SCOPE_FIXTURE = {
    "always_tests": "tests/test_repo_hygiene.py",
    "commit_islands": [
        {"name": "field", "prefixes": ["apps/field/", "tests/test_field_"],
         "tests": ["tests/test_field_*", "tests/test_layers.py"]},
    ],
}


def _island(paths, tmp_path) -> str:
    return _node(
        f"const i = islandFor({json.dumps(_SCOPE_FIXTURE)}, {json.dumps(paths)});\n"
        "process.stdout.write(i ? i.name : 'FULL');\n", str(tmp_path))


class TestIslandFor:
    def test_all_inside_is_island(self, tmp_path):
        assert _island(["apps/field/cli.py", "tests/test_field_session.py"], tmp_path) == "field"

    def test_one_outside_is_full(self, tmp_path):
        assert _island(["apps/field/cli.py", "core/simulation.py"], tmp_path) == "FULL"

    def test_empty_is_full(self, tmp_path):
        assert _island([], tmp_path) == "FULL"

    def test_prefix_is_not_substring(self, tmp_path):
        assert _island(["apps/fieldx/cli.py"], tmp_path) == "FULL"


class TestCommitPaths:
    def test_rename_counts_both_sides(self, tmp_path):
        repo = tmp_path / "r"
        (repo / "core").mkdir(parents=True)
        (repo / "apps" / "field").mkdir(parents=True)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "t")
        (repo / "core" / "x.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "init")
        _git(repo, "mv", "core/x.py", "apps/field/x.py")
        out = _node("process.stdout.write(JSON.stringify(commitPaths(process.cwd()).sort()));\n",
                    str(repo))
        paths = json.loads(out)
        assert "core/x.py" in paths and "apps/field/x.py" in paths
        assert _island(paths, tmp_path) == "FULL"


class TestIslandTargets:
    def test_glob_expands_and_always_tests_added(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        for n in ["test_field_a.py", "test_field_b.py", "test_layers.py",
                  "test_repo_hygiene.py", "test_other.py", "test_field_c.txt"]:
            (tests / n).write_text("", encoding="utf-8")
        out = _node(
            f"const s = {json.dumps(_SCOPE_FIXTURE)};\n"
            "process.stdout.write(JSON.stringify(islandTargets(process.cwd(), s, s.commit_islands[0])));\n",
            str(tmp_path))
        # islandTargets は最後に sort() するので、並びは常に辞書順
        assert json.loads(out) == [
            "tests/test_field_a.py", "tests/test_field_b.py",
            "tests/test_layers.py", "tests/test_repo_hygiene.py",
        ]


#: リポジトリを歩く字面。⚠️ **pytest の一時フォルダ（`tmp_path`）を歩く形は除く**
#: ＝リポジトリには触れないので島に足す理由が無い（2026-09-24＝B-280 のテストが
#: `os.walk(tmp_path)` で誤検知され、フルスイート 8 分を 1 回無駄にした）。
_REPO_WALK = re.compile(
    r"os\.walk\((?!\s*(?:str\(\s*)?tmp_path\b)"
    r"|(?<!tmp_path)\.rglob\("
    r"|ls-files")


def _repo_wide_scanners() -> set[str]:
    """リポジトリ全体（apps/・全 .py・全追跡ファイル）を歩くテスト。"""
    found = set()
    for name in os.listdir(os.path.join(_REPO, "tests")):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        with open(os.path.join(_REPO, "tests", name), encoding="utf-8") as f:
            if _REPO_WALK.search(f.read()):
                found.add(f"tests/{name}")
    return found


@pytest.mark.parametrize("src, expected", [
    ("for d, _, fs in os.walk(ROOT):", True),
    ("for p in ROOT.rglob('*.py'):", True),
    ("git ls-files", True),
    ("for d, _, fs in os.walk(tmp_path):", False),
    ("for d, _, fs in os.walk(str(tmp_path)):", False),
    ("for p in tmp_path.rglob('*.png'):", False),
])
def test_repo_walk_pattern_ignores_pytest_tmp_dirs(src, expected):
    assert bool(_REPO_WALK.search(src)) is expected


class TestCommandDetection:
    """B-302＝git の全体オプション（`-C <パス>` など）を挟んだ commit/push も拾うこと。

    🔴 旧い判定は `git` の直後の副コマンドしか見ず、`git -C <パス> commit` と
    `git -C <パス> push` を両方素通りさせた＝push の前のフルスイートまで外れた
    （`cd` を止めるフックが絶対パスへ誘導するので、この形が多数派になる）。
    """

    def _probe(self, commands, tmp_path) -> list:
        return json.loads(_node(
            f"process.stdout.write(JSON.stringify({json.dumps(commands)}"
            ".map((c) => [isCommitOrPush(c), isPush(c)])));\n", str(tmp_path)))

    def test_commit_and_push_forms(self, tmp_path):
        cases = {
            # command: (commit か push か, push か)
            "git commit -m x": (True, False),
            "git push": (True, True),
            "git -C D:\\dev\\radiosim-repo commit -F -": (True, False),
            "$m | git -C D:\\dev\\radiosim-repo commit -F -": (True, False),
            'git -C "D:\\a b\\repo" push origin main': (True, True),
            "git -c core.quotepath=false commit -m x": (True, False),
            "git --no-pager -C d:/r push": (True, True),
            "git --git-dir=d:/r/.git --work-tree d:/r commit -m x": (True, False),
            "git.exe -C d:/r commit -m x": (True, False),
            "git -C d:/r add -A; git -C d:/r commit -q -m x": (True, False),
            # 副コマンドが別のもの・文字列の中＝拾わない
            "git -C d:/r log --grep commit": (False, False),
            "git -C d:/r status": (False, False),
            "echo git commit": (False, False),
            "git -C d:/r push-foo": (False, False),
        }
        got = self._probe(list(cases), tmp_path)
        for (cmd, want), actual in zip(cases.items(), got):
            assert tuple(actual) == want, cmd


class TestStdinDiagnostics:
    """I-166＝「素で叩いた（stdin が空／JSON として読めない）」と「フックとして
    呼ばれ、検査対象ではなかった」を手元から区別できること。前者だけ非ゼロで
    断る（後者は毎回のフック呼び出しなので無言のまま＝壊れ方②を避ける）。
    """

    def _run(self, stdin: str, tmp_path) -> subprocess.CompletedProcess:
        # I-172＝日本語の文字化けを防ぐため encoding を明示（既定は Windows の
        # ロケール cp932 で、node が UTF-8 で書くメッセージを読めずに落ちる）。
        return subprocess.run(["node", _GATE], input=stdin, cwd=str(tmp_path),
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_empty_stdin_is_refused_not_silently_allowed(self, tmp_path):
        r = self._run("", tmp_path)
        assert r.returncode != 0
        assert r.stdout == ""
        assert "stdin" in r.stderr

    def test_unparseable_stdin_is_refused_not_silently_allowed(self, tmp_path):
        r = self._run("not json {", tmp_path)
        assert r.returncode != 0
        assert r.stdout == ""
        assert "stdin" in r.stderr

    def test_valid_input_for_an_unrelated_tool_stays_silent(self, tmp_path):
        """フックとして呼ばれた通常の Read/Edit 等＝この分岐には来ない（無言のまま 0）。"""
        r = self._run(json.dumps({"tool_name": "Read", "tool_input": {}}), tmp_path)
        assert r.returncode == 0
        assert r.stdout == ""
        assert r.stderr == ""


def test_real_islands_include_every_repo_wide_scanner():
    """全体を走査するテストが増えたら島のテストにも足す（漏れると島のコミットで赤を素通しする）。"""
    with open(_SCOPE, encoding="utf-8") as f:
        scope = json.load(f)
    scanners = _repo_wide_scanners() - {scope["always_tests"], "tests/test_pre_commit_gate.py"}
    for island in scope.get("commit_islands", []):
        listed = set(island["tests"])
        missing = sorted(s for s in scanners
                         if s not in listed
                         and not any(t.endswith("*") and s.startswith(t[:-1]) for t in island["tests"]))
        assert not missing, f"島「{island['name']}」の tests に足りない全体走査テスト: {missing}"


def _gate_const(name: str) -> object:
    src = _GATE.replace("\\", "/")
    out = subprocess.run(
        ["node", "--input-type=module", "-e",
         f'import * as g from "file:///{src}"; console.log(JSON.stringify(g.{name}));'],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=60)
    return json.loads(out.stdout)


def test_ledger_preflight_selects_every_real_data_test():
    """台帳・版計画・メモリの実データの検査は、全テストの前に数秒で走る（2026-09-26）。

    選び方は名前の頭（`test_real_`）＝実データの検査を足しても並べ直さずに乗る。
    ここでは「選ばれたものが、ファイルにある `test_real_` の全部」であることを見る。
    """
    args = _gate_const("LEDGER_PREFLIGHT")
    assert isinstance(args, list)
    py = os.environ.get("RADIOSIM_PYTHON") or shutil.which("python")
    assert py
    out = subprocess.run([py, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                          *args], cwd=_REPO, capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    ids = [ln for ln in out.stdout.splitlines() if "::" in ln]
    with open(os.path.join(_REPO, args[0]), encoding="utf-8") as f:
        defined = set(re.findall(r"def (test_real_\w+)", f.read()))
    assert ids and all("test_real_" in i for i in ids)
    assert {re.search(r"(test_real_\w+)", i).group(1) for i in ids} == defined  # type: ignore[union-attr]


def test_deny_messages_say_the_order_and_that_nothing_ran():
    order = _gate_const("LEDGER_ORDER")
    nothing = _gate_const("NOTHING_RAN")
    assert isinstance(order, str) and "コミットの後" in order and "ステージ行" in order
    assert isinstance(nothing, str) and "git add" in nothing and "git status" in nothing
