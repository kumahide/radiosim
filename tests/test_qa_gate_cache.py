"""
tests/test_qa_gate_cache.py
===========================
Stop フックの決定論ゲート（`tools/qa-hook/pytest-cache.mjs`）の検証＝**I-056**。

**何を守るか**: ゲートは「作業ツリーに変更された `.py` があれば全スイートを回す」で
起動するので、未コミットの `.py` を抱えたまま会話を続けると**中身が 1 バイトも
変わっていなくても毎ターン同じスイートが走った**（[[feedback-promote-recurring-checks]]
のゲートの壊れ方②＝毎回鳴る）。対策は「前回通った内容と同一なら pytest を飛ばす」
キャッシュ。⇒ **このテストが守るのは「速さ」ではなく「飛ばしすぎないこと」**。

**壊れ方 3 種を全部通す**（新ゲートの必須検証）:

1. **一度も落ちない**（＝キャッシュが効きすぎて pytest が二度と走らない）
   → `TestKeyChangesWhenInputChanges` が、`.py`・非 `.py`（README 等）・
     git-ignore だがテスト対象のフック本体、のどれを変えても鍵が変わることを要求。
2. **毎回鳴る**（＝直したはずの症状が残る）
   → `TestKeyIsStableWhenNothingChanges` が、触っただけ（mtime 更新）や再計算では
     鍵が変わらないことを要求。
3. **間違ったものを要求している**
   → `TestCacheHit` が、鍵が違えばキャッシュはヒットしないこと（＝鍵の一致だけを
     根拠にしていること）を要求。

`tools/` は git-ignore なので **CI では skip**（対象が存在しない）。
"""

import json
import os
import shutil
import subprocess
import time

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CACHE_MJS = os.path.join(_REPO, "tools", "qa-hook", "pytest-cache.mjs")

pytestmark = [
    pytest.mark.skipif(
        not os.path.exists(_CACHE_MJS),
        reason="tools/ は git-ignore（CI には存在しない）。ローカルのみ検証する。",
    ),
    pytest.mark.skipif(shutil.which("node") is None, reason="node が無い環境"),
]


def _run_node(script: str, cwd: str) -> str:
    """`pytest-cache.mjs` を読み込む使い捨てスクリプトを node で実行して stdout を返す。"""
    path = os.path.join(cwd, "_probe.mjs").replace("\\", "/")
    src = os.path.join(_CACHE_MJS).replace("\\", "/")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'import {{ pytestCacheKey, isCachedPass, recordPass, CACHE_PATH }} from "file:///{src}";\n')
        f.write(script)
    try:
        out = subprocess.run(
            ["node", path],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        os.remove(path)
    return out.stdout.strip()


def _key(repo: str) -> str:
    return _run_node('process.stdout.write(String(pytestCacheKey(process.cwd())));\n', repo)


def _git(repo: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    """コミットが 1 つある、素の git リポジトリ（作業ツリーは綺麗）。"""
    d = tmp_path / "repo"
    d.mkdir()
    _git(str(d), "init", "-q")
    _git(str(d), "config", "user.email", "t@example.com")
    _git(str(d), "config", "user.name", "t")
    (d / "app.py").write_text("x = 1\n", encoding="utf-8")
    (d / "README.md").write_text("# doc\n", encoding="utf-8")
    _git(str(d), "add", "-A")
    _git(str(d), "commit", "-q", "-m", "init")
    return str(d)


# ============================================================
# 壊れ方②：同じ内容なら鍵は動かない（＝毎ターン走らない）
# ============================================================
class TestKeyIsStableWhenNothingChanges:
    def test_same_tree_gives_the_same_key(self, repo):
        assert _key(repo) == _key(repo)

    def test_touching_a_file_without_changing_it_does_not_move_the_key(self, repo):
        before = _key(repo)
        target = os.path.join(repo, "app.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("x = 1\n")  # 同じ内容で書き直す＝mtime だけ動く
        os.utime(target, (time.time() + 10, time.time() + 10))
        assert _key(repo) == before


# ============================================================
# 壊れ方①：入力が変わったのに走らない、を許さない
# ============================================================
class TestKeyChangesWhenInputChanges:
    def test_changed_py_moves_the_key(self, repo):
        before = _key(repo)
        (open(os.path.join(repo, "app.py"), "w", encoding="utf-8")).write("x = 2\n")
        assert _key(repo) != before

    def test_changed_non_py_moves_the_key(self, repo):
        """README 等もスイートの入力（test_docs_consistency が読む）。"""
        before = _key(repo)
        (open(os.path.join(repo, "README.md"), "w", encoding="utf-8")).write("# other\n")
        assert _key(repo) != before

    def test_new_untracked_file_moves_the_key(self, repo):
        before = _key(repo)
        (open(os.path.join(repo, "new.py"), "w", encoding="utf-8")).write("y = 1\n")
        assert _key(repo) != before

    def test_deleting_a_file_moves_the_key(self, repo):
        before = _key(repo)
        os.remove(os.path.join(repo, "app.py"))
        assert _key(repo) != before

    def test_new_commit_moves_the_key(self, repo):
        """コミットで作業ツリーは綺麗になるが、HEAD が動いた以上は走り直す。"""
        before = _key(repo)
        (open(os.path.join(repo, "app.py"), "w", encoding="utf-8")).write("x = 3\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "second")
        assert _key(repo) != before

    def test_ignored_but_tested_hook_moves_the_key(self, repo):
        """`.claude/*.py` は git-ignore だが test_claude_hooks の対象＝入力である。"""
        hooks = os.path.join(repo, ".claude")
        os.makedirs(hooks)
        (open(os.path.join(hooks, "h.py"), "w", encoding="utf-8")).write("a = 1\n")
        (open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8")).write(".claude/\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "ignore hooks")
        before = _key(repo)
        (open(os.path.join(hooks, "h.py"), "w", encoding="utf-8")).write("a = 22222\n")
        assert _key(repo) != before

    def test_ignored_but_tested_gate_source_moves_the_key(self, repo):
        """ゲート自身（`tools/qa-hook/*.mjs`）を書き換えたらキャッシュは無効。"""
        d = os.path.join(repo, "tools", "qa-hook")
        os.makedirs(d)
        (open(os.path.join(d, "x.mjs"), "w", encoding="utf-8")).write("// a\n")
        (open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8")).write("tools/\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "ignore tools")
        before = _key(repo)
        (open(os.path.join(d, "x.mjs"), "w", encoding="utf-8")).write("// bbbbbbbb\n")
        assert _key(repo) != before


# ============================================================
# 壊れ方③：ヒット判定が鍵の一致だけを根拠にしているか
# ============================================================
class TestCacheHit:
    def test_no_record_is_a_miss(self, repo):
        assert _run_node(
            'process.stdout.write(String(isCachedPass(process.cwd(), pytestCacheKey(process.cwd()))));\n',
            repo,
        ) == "false"

    def test_recorded_key_hits(self, repo):
        assert _run_node(
            "const k = pytestCacheKey(process.cwd());\n"
            "recordPass(process.cwd(), k);\n"
            "process.stdout.write(String(isCachedPass(process.cwd(), k)));\n",
            repo,
        ) == "true"

    def test_a_different_key_does_not_hit(self, repo):
        assert _run_node(
            "recordPass(process.cwd(), pytestCacheKey(process.cwd()));\n"
            "process.stdout.write(String(isCachedPass(process.cwd(), 'some-other-key')));\n",
            repo,
        ) == "false"

    def test_record_then_edit_misses(self, repo):
        """記録 → `.py` を書き換え → ミス（＝実運用の一巡）。"""
        out = _run_node(
            "import { writeFileSync } from 'node:fs';\n"
            "recordPass(process.cwd(), pytestCacheKey(process.cwd()));\n"
            "writeFileSync('app.py', 'x = 999\\n');\n"
            "process.stdout.write(String(isCachedPass(process.cwd(), pytestCacheKey(process.cwd()))));\n",
            repo,
        )
        assert out == "false"

    def test_cache_lives_inside_dot_git(self, repo):
        """作業ツリーを汚さない（`.git/` 配下＝追跡されず同期もされない）。"""
        _run_node("recordPass(process.cwd(), pytestCacheKey(process.cwd()));\n", repo)
        assert os.path.exists(os.path.join(repo, ".git", "radiosim-qa-pytest.json"))
        assert subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True
        ).stdout.strip() == ""

    def test_corrupt_cache_is_a_miss_not_a_crash(self, repo):
        path = os.path.join(repo, ".git", "radiosim-qa-pytest.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        assert _run_node(
            'process.stdout.write(String(isCachedPass(process.cwd(), pytestCacheKey(process.cwd()))));\n',
            repo,
        ) == "false"


def test_cache_path_is_the_documented_one(repo):
    assert json.loads(
        _run_node("process.stdout.write(JSON.stringify(CACHE_PATH));\n", repo)
    ).replace("\\", "/") == ".git/radiosim-qa-pytest.json"
