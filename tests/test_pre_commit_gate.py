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
_CACHE = os.path.join(_REPO, "tools", "qa-hook", "pytest-cache.mjs")

pytestmark = [
    pytest.mark.skipif(not os.path.exists(_GATE), reason="QA ゲート本体が無い環境"),
    pytest.mark.skipif(shutil.which("node") is None, reason="node が無い環境"),
]


def _node(script: str, cwd: str) -> str:
    src = _GATE.replace("\\", "/")
    path = os.path.join(cwd, "_probe_gate.mjs")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'import {{ commitPaths, islandFor, islandTargets, isCommitOrPush, isPush, '
                f'versionLineOnly, versionReaders, versionChain }} from "file:///{src}";\n')
        cache = _CACHE.replace("\\", "/")
        f.write(f'import {{ cacheInputs, recordFullPass, lastFullPass }} from "file:///{cache}";\n')
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


# ============================================================
# I-185：版の字の 1 行だけの変更は、版の字の読み手のテストで足りる
# ============================================================
# 🔑 **守るのは「絞りすぎないこと」**＝版の字の 1 行だけ、を字面で判定し、少しでも
# 外れたらフルへ落とす（fail-closed）。読み手は手書きの一覧にせず規則で決め、
# 規則の穴（テストに版の字を直書きする形）は下の見張りが止める。
_VERSION_SRC = (
    'import re\n\n'
    'APP_NAME    = "RadioSim Pro"\n'
    'APP_VERSION = "3.7"\n'
    'APP_FULL    = f"{APP_NAME} {APP_VERSION}"\n'
    'COPYRIGHT   = "c"\n\n\n'
    'def is_final(v: str = APP_VERSION) -> bool:\n'
    '    return True\n'
)


def _bump(src: str, new: str = '"3.8a1"') -> str:
    return src.replace('APP_VERSION = "3.7"', f"APP_VERSION = {new}")


class TestVersionLineOnly:
    @pytest.mark.parametrize("new, expected", [
        (_bump(_VERSION_SRC), True),
        (_bump(_VERSION_SRC).replace("\n", "\r\n"), True),          # 改行の形だけは差にしない
        (_bump(_VERSION_SRC).replace('"c"', '"d"'), False),         # 2 行
        (_bump(_VERSION_SRC) + "X = 1\n", False),                   # 行を足した
        (_VERSION_SRC.replace('APP_NAME    = "RadioSim Pro"', 'APP_NAME    = "X"'), False),
        (_VERSION_SRC, False),                                      # 同じ
        (_bump(_VERSION_SRC, '"3.8a1"  # x'), False),               # 字面の外
        (_bump(_VERSION_SRC, 'f"{APP_NAME}"'), False),
        (_bump(_VERSION_SRC, '"3.8 a1"'), False),
        (None, False),                                              # ファイルが無い
    ])
    def test_only_the_literal_version_line(self, new, expected, tmp_path):
        out = _node(f"process.stdout.write(String(versionLineOnly("
                    f"{json.dumps(_VERSION_SRC)}, {json.dumps(new)})));\n", str(tmp_path))
        assert out == str(expected).lower()


def _version_fixture(root) -> None:
    """版の字の読み手の規則を試す小さなリポジトリの中身。"""
    (root / "core").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "core" / "version.py").write_text(_VERSION_SRC, encoding="utf-8")
    (root / "core" / "update_check.py").write_text(
        "from core import version\n\n"
        "def check(current: str = version.APP_VERSION):\n    return current\n", encoding="utf-8")
    (root / "core" / "stagey.py").write_text(
        "from core import version\n\ndef f():\n    return version.is_final()\n", encoding="utf-8")
    (root / "core" / "printer.py").write_text(
        "from core import version\n\nTITLE = version.APP_FULL\n"
        "def show():\n    return print_it(ver=version.APP_VERSION)\n", encoding="utf-8")
    for name, body in {
        "test_a.py": "assert APP_FULL",                 # 版の字を運ぶ定数
        "test_b.py": "from core import update_check",   # 引数の既定が版の字
        "test_c.py": "from core import printer",        # 字を出すだけ（名前も呼ばない）
        "test_d.py": "COPYRIGHT",                       # 版の字を運ばない定数
        "test_e.py": "from core import stagey",         # 段階（a/b/RC/正式）で振る舞いが変わる
        "test_f.py": "version.is_final('3.7')",
    }.items():
        (root / "tests" / name).write_text(body + "\n", encoding="utf-8")


class TestVersionReaders:
    def test_the_rule(self, tmp_path):
        _version_fixture(tmp_path)
        got = json.loads(_node("process.stdout.write(JSON.stringify(versionReaders(process.cwd())));\n",
                               str(tmp_path)))
        assert got == ["tests/test_a.py", "tests/test_b.py", "tests/test_e.py", "tests/test_f.py"]

    def test_no_version_file_means_full(self, tmp_path):
        (tmp_path / "tests").mkdir()
        out = _node("process.stdout.write(String(versionReaders(process.cwd())));\n", str(tmp_path))
        assert out == "null"

    @pytest.mark.parametrize("only, expected", [(True, "version"), (False, "FULL")])
    def test_island_for_the_version_file(self, only, expected, tmp_path):
        _version_fixture(tmp_path)
        scope = {**_SCOPE_FIXTURE, "full_prefixes": ["core/"]}
        out = _node(
            f"const i = islandFor({json.dumps(scope)}, ['core/version.py'], process.cwd(),"
            f" {{ versionOnly: {json.dumps(only)} }});\n"
            "process.stdout.write(i ? i.name + '|' + i.tests.join(',') : 'FULL');\n", str(tmp_path))
        assert out.split("|")[0] == expected
        if only:
            assert "tests/test_a.py" in out and "tests/test_c.py" not in out

    def test_another_core_file_alongside_is_full(self, tmp_path):
        _version_fixture(tmp_path)
        scope = {**_SCOPE_FIXTURE, "full_prefixes": ["core/"]}
        out = _node(
            f"const i = islandFor({json.dumps(scope)}, ['core/version.py', 'core/printer.py'],"
            " process.cwd(), { versionOnly: true });\n"
            "process.stdout.write(i ? i.name : 'FULL');\n", str(tmp_path))
        assert out == "FULL"


class TestVersionChain:
    """直前の**本物の**フル合格から、版の字の 1 行だけ動いた木か（push でも使う）。"""

    def _repo(self, tmp_path):
        repo = tmp_path / "r"
        repo.mkdir()
        _version_fixture(repo)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "t")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "init")
        return repo

    def _node_in(self, repo, tmp_path, script: str) -> str:
        """探針をリポジトリの**外**に置く（中に置くと未追跡のファイルとして木に入る）。"""
        path = tmp_path / "_probe_chain.mjs"
        gate = _GATE.replace("\\", "/")
        cache = _CACHE.replace("\\", "/")
        path.write_text(
            f'import {{ versionChain }} from "file:///{gate}";\n'
            f'import {{ cacheInputs, recordFullPass, lastFullPass }} from "file:///{cache}";\n'
            "import { readFileSync } from 'node:fs';\n"
            f"const R = {json.dumps(str(repo))};\n"
            "const V = () => readFileSync(R + '/core/version.py', 'utf-8');\n" + script,
            encoding="utf-8")
        return subprocess.run(["node", str(path)], capture_output=True, text=True,
                              check=True, timeout=60).stdout.strip()

    def _record_full(self, repo, tmp_path):
        self._node_in(repo, tmp_path,
                      "recordFullPass(R, cacheInputs(R, { without: 'core/version.py' }),"
                      " { versionText: V() });\n")

    def _chain(self, repo, tmp_path) -> str:
        return self._node_in(repo, tmp_path,
                             "process.stdout.write(String(versionChain("
                             "cacheInputs(R, { without: 'core/version.py' }), lastFullPass(R), V())));\n")

    def test_a_bump_after_a_full_pass_chains(self, tmp_path):
        repo = self._repo(tmp_path)
        self._record_full(repo, tmp_path)
        (repo / "core" / "version.py").write_text(_bump(_VERSION_SRC), encoding="utf-8")
        assert self._chain(repo, tmp_path) == "true"
        _git(repo, "commit", "-qam", "bump")          # コミットの後（＝push の前）も同じ
        assert self._chain(repo, tmp_path) == "true"

    def test_no_full_pass_no_chain(self, tmp_path):
        repo = self._repo(tmp_path)
        (repo / "core" / "version.py").write_text(_bump(_VERSION_SRC), encoding="utf-8")
        assert self._chain(repo, tmp_path) == "false"

    def test_another_file_changed_too_breaks_the_chain(self, tmp_path):
        repo = self._repo(tmp_path)
        self._record_full(repo, tmp_path)
        (repo / "core" / "version.py").write_text(_bump(_VERSION_SRC), encoding="utf-8")
        (repo / "tests" / "test_c.py").write_text("changed\n", encoding="utf-8")
        assert self._chain(repo, tmp_path) == "false"

    def test_a_new_untracked_file_breaks_the_chain(self, tmp_path):
        repo = self._repo(tmp_path)
        self._record_full(repo, tmp_path)
        (repo / "core" / "version.py").write_text(_bump(_VERSION_SRC), encoding="utf-8")
        (repo / "new.md").write_text("x\n", encoding="utf-8")
        assert self._chain(repo, tmp_path) == "false"

    def test_a_second_line_in_the_version_file_breaks_the_chain(self, tmp_path):
        repo = self._repo(tmp_path)
        self._record_full(repo, tmp_path)
        (repo / "core" / "version.py").write_text(
            _bump(_VERSION_SRC).replace('"c"', '"d"'), encoding="utf-8")
        assert self._chain(repo, tmp_path) == "false"

    def test_an_ignored_but_tested_hook_changed_breaks_the_chain(self, tmp_path):
        """木に見えない入力（`.claude/*.py`）が動いたら、連鎖は認めない。"""
        repo = self._repo(tmp_path)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "h.py").write_text("a = 1\n", encoding="utf-8")
        (repo / ".gitignore").write_text(".claude/\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "hooks")
        self._record_full(repo, tmp_path)
        (repo / "core" / "version.py").write_text(_bump(_VERSION_SRC), encoding="utf-8")
        (repo / ".claude" / "h.py").write_text("a = 222222\n", encoding="utf-8")
        assert self._chain(repo, tmp_path) == "false"


def _version_literal(v: str) -> re.Pattern:
    """版の字を直書きする形（引用符の中・製品名の後・`v` の後・User-Agent の `/` の後）。"""
    return re.compile(r"(?:[\"'`]|Pro |RadioSim[ /]|\bv)" + re.escape(v) + r"(?![\w.])")


@pytest.mark.parametrize("text, hit", [
    ('assert s == "3.7"', True),
    ("RadioSim Pro 3.7 の帳票", True),
    ("Mozilla/5.0 RadioSim/3.7", True),
    ("v3.7", True),
    ("x = 3.7  # dB", False),
    ('"3.75"', False),
    ('"3.7.1"', False),
])
def test_version_literal_pattern(text, hit):
    assert bool(_version_literal("3.7").search(text)) is hit


def test_tests_that_hard_code_the_version_are_readers():
    """見張り（I-185）＝版の字を直書きしたテスト・データは、読み手の規則に入っていること。

    読み手の規則は「名前を呼ぶテスト」しか拾わない＝版の字を直書きした比較（ゴールデン
    など）は規則の外で、版の字の 1 行だけのコミットで赤を素通しし得る。ここで止める。
    """
    readers = set(json.loads(_node(
        "process.stdout.write(JSON.stringify(versionReaders(process.cwd())));\n", _REPO)))
    assert "tests/test_version.py" in readers  # 規則が空回りしていないこと
    with open(os.path.join(_REPO, "core", "version.py"), encoding="utf-8") as f:
        current = re.search(r'^APP_VERSION = "([^"]+)"$', f.read(), re.M).group(1)  # type: ignore[union-attr]
    pattern = _version_literal(current)
    hits = []
    for d, dirs, files in os.walk(os.path.join(_REPO, "tests")):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        for name in files:
            path = os.path.join(d, name)
            rel = os.path.relpath(path, _REPO).replace("\\", "/")
            if rel in readers:
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                continue  # 画像などの二進
            if pattern.search(text):
                hits.append(rel)
    assert not hits, (
        f"版の字（{current}）を直書きしているのに、読み手の規則に入っていない: {hits}\n"
        "＝版の字だけのコミットでは走らない。`APP_VERSION` 等の名前で比べる形に直すこと。")
