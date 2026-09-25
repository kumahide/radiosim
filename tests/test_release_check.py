"""
tests/test_release_check.py
===========================
版の節目の助言（`tools/qa-hook/release-check.mjs`）の検証＝**B-294・B-303**。

**何を守るか**: 助言の 🔴🔴 は、赤・未検査と**確かめられたときだけ**出ること。
毎回鳴る網は、本当に赤いときの 🔴🔴 まで読み飛ばされる
（[[feedback-promote-recurring-checks]] の壊れ方②）。

- **B-294**＝表示依存の刻印は、検査した中身（作業ツリーの `views/`・`tests/`）を名札に
  する。🔴 以前は `git rev-parse HEAD` を名札にしていた＝コミット前ゲートの全数実行は
  必ず親として刻まれ、push 前の助言が自分のコミットを見つけて鳴った。逆向きに、
  未コミットの変更で回してから戻すと嘘の ✅ になった。
- **B-303**＝CI の行は、実行中（結論が空）を赤と同じ 🔴🔴 で出さない。いまのブランチの
  実行を引く（別のブランチの色を出さない）。

刻む側（`tests/conftest.py` の `_display_files`）と読む側（`.mjs` の `headDisplayFiles`）の
**作り方が揃っていること**が要なので、両方を本物のまま一時リポジトリで突き合わせる。
判定は `.mjs` 側にあり、Python からは再実装しない。
"""

import json
import os
import shutil
import subprocess

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MJS = os.path.join(_REPO, "tools", "qa-hook", "release-check.mjs")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が無い環境")

# コミット前ゲートなど git の中から呼ばれたとき、GIT_DIR などが一時リポジトリへ漏れない
# ようにする（漏れると本物のリポジトリの索引を触る）。
_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(repo, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, env=_ENV, check=True,
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    return out.stdout.strip()


def _node(repo, expr: str) -> str:
    """`release-check.mjs` を import して `expr` の値（文字列）を返す。"""
    src = _MJS.replace("\\", "/")
    script = (f'import * as rc from "file:///{src}";\n'
              f"process.stdout.write(String({expr}));\n")
    out = subprocess.run(["node", "--input-type=module", "-e", script], cwd=repo, env=_ENV,
                         check=True, capture_output=True, text=True, encoding="utf-8",
                         timeout=60)
    return out.stdout


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """views/ tests/ core/ を 1 本ずつ持つ、コミット 1 つの一時リポジトリ。

    `core.autocrlf=true`＝開発機と同じ設定（作業ツリーは CRLF・blob は LF）。
    """
    import conftest

    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"),
                 ("core.autocrlf", "true"), ("commit.gpgsign", "false")):
        _git(d, "config", k, v)
    for rel in ("views/a.py", "tests/test_x.py", "core/m.py"):
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(b"x = 1\r\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "init")

    monkeypatch.setattr(conftest, "_REPO_ROOT", d)
    monkeypatch.setattr(conftest, "DISPLAY_RUN_STAMP", d / ".qa" / "display_run.json")
    return d


def _stamp() -> None:
    """表示のある機械でフルスイートが通った、として刻む（刻む側は本物のまま）。"""
    import conftest

    conftest._stamp_display_run(100, {})


def _line(repo) -> str:
    return _node(repo, "rc.displayRunLine(process.cwd())")


def _commit(repo, rel: str, body: bytes = b"x = 2\r\n") -> None:
    (repo / rel).write_bytes(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"touch {rel}")


class TestDisplayStampNamesTheTestedContent:
    """B-294＝刻印の名札は、検査した中身を指す。"""

    def test_a_clean_tree_is_green(self, repo):
        _stamp()
        line = _line(repo)
        assert "✅" in line and "🔴" not in line, line

    def test_the_pre_commit_gate_run_covers_the_commit_it_gates(self, repo):
        """🔴 B-294 の症状そのもの＝**コミット前**（HEAD はまだ親）に回した実行が、
        そのコミットを検査済みと認められること。"""
        (repo / "views" / "a.py").write_bytes(b"x = 2\r\n")
        _git(repo, "add", "-A")
        _stamp()                                # この時点の HEAD は親
        _git(repo, "commit", "-q", "-m", "gated")
        line = _line(repo)
        assert "✅" in line and "🔴" not in line, line

    def test_reverting_after_the_run_is_not_a_green(self, repo):
        """⚠️ 逆向きの嘘＝未コミットの変更で回してから戻すと、HEAD の中身は回っていない。

        🔴 commit を名札にしていたころは、HEAD と一致して ✅ と出た。
        """
        (repo / "views" / "a.py").write_bytes(b"x = 2\r\n")
        _stamp()
        _git(repo, "checkout", "--", "views/a.py")
        line = _line(repo)
        assert "🔴🔴" in line and "✅" not in line, line
        assert "views/a.py" in line

    def test_a_later_display_commit_rings(self, repo):
        _stamp()
        _commit(repo, "tests/test_x.py")
        line = _line(repo)
        assert "🔴🔴" in line and "1 ファイル" in line, line

    def test_a_commit_outside_the_scope_does_not_ring(self, repo):
        """views/ tests/ 以外（CI が見る層）が動いただけでは鳴らさない＝毎コミット鳴る網にしない。"""
        _stamp()
        _commit(repo, "core/m.py")
        line = _line(repo)
        assert "✅" in line and "🔴" not in line, line

    def test_an_untracked_test_counts_until_committed(self, repo):
        """未追跡のテストも pytest は読む＝刻印に入り、コミットされるまで HEAD とは違う。"""
        (repo / "tests" / "test_new.py").write_bytes(b"def test_n(): pass\r\n")
        _stamp()
        assert "🔴🔴" in _line(repo)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "add")
        assert "✅" in _line(repo)

    def test_a_file_deleted_from_the_worktree_is_not_counted_as_tested(self, repo):
        (repo / "views" / "a.py").unlink()
        _stamp()                                # 消えたファイルで落ちないこと
        assert "🔴🔴" in _line(repo), "HEAD にはあるのに回っていないファイルを見落とした"
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "rm")
        assert "✅" in _line(repo)

    def test_the_stamp_hashes_like_git_add(self, repo):
        """作業ツリーが CRLF・blob が LF でも一致すること（改行の変換を git add と揃える）。"""
        import conftest

        head = json.loads(_node(repo, "JSON.stringify(rc.headDisplayFiles(process.cwd()))"))
        assert conftest._display_files(repo) == head
        assert set(head) == {"views/a.py", "tests/test_x.py"}

    def test_a_legacy_stamp_is_not_green(self, repo):
        """B-294 より前の刻印（commit しか無い）は中身で照らせない＝✅ と言わない。"""
        (repo / ".qa").mkdir()
        (repo / ".qa" / "display_run.json").write_text(json.dumps(
            {"commit": _git(repo, "rev-parse", "HEAD"), "when": "2026-09-26T00:00:00",
             "ran": 1}), encoding="utf-8")
        line = _line(repo)
        assert "旧形式" in line and "✅" not in line, line

    def test_no_stamp_rings(self, repo):
        assert "刻印がありません" in _line(repo)


class TestCiLineIsRedOnlyWhenRed:
    """B-303＝CI の 🔴🔴 は、赤と確かめられたときだけ。"""

    @staticmethod
    def _ci(repo, run: dict | None) -> str:
        return _node(repo, f"rc.ciRunLine({json.dumps(run)} ?? undefined)")

    _BASE = {"headBranch": "main", "createdAt": "2026-09-25T20:05:00Z"}

    @pytest.mark.parametrize("status", ["in_progress", "queued", "waiting", "pending"])
    def test_a_running_ci_is_not_red(self, repo, status):
        """🔴 B-303 の症状そのもの＝結論の欄が空のまま「直近の実行が （…）」と赤で出た。"""
        line = self._ci(repo, {**self._BASE, "status": status, "conclusion": ""})
        assert "⏳" in line and "🔴" not in line, line

    def test_a_completed_run_without_conclusion_is_not_red(self, repo):
        line = self._ci(repo, {**self._BASE, "status": "completed", "conclusion": ""})
        assert "🔴" not in line, line

    @pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
    def test_a_red_run_is_red(self, repo, conclusion):
        """壊れ方①（一度も鳴らない）の番＝直したあとも、赤は 🔴🔴 のまま。"""
        line = self._ci(repo, {**self._BASE, "status": "completed", "conclusion": conclusion})
        assert "🔴🔴" in line and conclusion in line, line

    def test_a_green_run_is_green(self, repo):
        line = self._ci(repo, {**self._BASE, "status": "completed", "conclusion": "success"})
        assert "✅" in line and "🔴" not in line, line

    def test_the_query_names_the_branch_and_asks_status(self, repo):
        args = json.loads(_node(repo, 'JSON.stringify(rc.ciRunListArgs("main"))'))
        assert args[args.index("--branch") + 1] == "main"
        assert "status" in args[args.index("--json") + 1].split(",")

    def test_the_branch_is_the_current_one(self, repo):
        _git(repo, "switch", "-q", "-c", "feature/x")
        assert _node(repo, "rc.ciBranch(process.cwd())") == "feature/x"
        _git(repo, "switch", "-q", "--detach")
        assert _node(repo, "rc.ciBranch(process.cwd())") == "main", "切り離された HEAD は main"
