"""
tests/test_hook_shim.py
=======================
門のフックの殻（`tools/qa-hook/hook-shim.mjs`）の検証＝**B-341**。

**何を守るか**: 門のスクリプト自身が読めない・起動で落ちたときは、門は閉じる。
Claude Code のフックは exit 2 だけが止める＝構文エラーや import の失敗（exit 1）は
**非致命**として素通りになる。🔴 実例＝2026-09-27、`pre-commit-gate.mjs` に競合の印が
残ったままマージ 2 件（`258b07a`・`d985109`）がゲートなしで入った。

殻は中身の exit 0・2 をそのまま返し、それ以外を exit 2「門が壊れている」に写す。
殻そのものが壊れたら同じ穴になる＝`node --check` をここで CI にかける。

判定は `.mjs` 側にあり、Python からは再実装しない（本物の殻に本物の子を走らせる）。
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import structural_skip

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_QA = os.path.join(_REPO, "tools", "qa-hook")
_SHIM = os.path.join(_QA, "hook-shim.mjs")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が無い環境")

_BROKEN = "門が壊れています"


def _shim(*argv: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(["node", _SHIM, *argv], input=stdin.encode("utf-8"),
                          capture_output=True, timeout=60)


def _script(tmp_path, name: str, body: str) -> str:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


class TestPassThrough:
    """中身が正常に答えたら、殻は何も足さず何も削らない。"""

    def test_exit0_copies_stdin_to_child_and_stdout_back(self, tmp_path):
        child = _script(tmp_path, "echo.py",
                        "import sys, json\n"
                        "d = json.load(sys.stdin)\n"
                        "print(json.dumps({'decision': 'block', 'reason': d['x']}))\n")
        r = _shim(sys.executable, child, stdin=json.dumps({"x": "日本語の理由"}))
        assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
        assert json.loads(r.stdout.decode("utf-8")) == {"decision": "block", "reason": "日本語の理由"}

    def test_exit2_is_the_gates_own_block(self, tmp_path):
        child = _script(tmp_path, "deny.py",
                        "import sys\nsys.stderr.write('止めた理由\\n')\nsys.exit(2)\n")
        r = _shim(sys.executable, child)
        err = r.stderr.decode("utf-8")
        assert r.returncode == 2
        assert "止めた理由" in err and _BROKEN not in err


class TestBrokenGateCloses:
    """中身が 0・2 以外で終わったら exit 2＝門は閉じる（素通りにしない）。"""

    def test_conflict_marker_in_mjs(self, tmp_path):
        # 実例の形そのもの＝競合の印が残った .mjs（node は SyntaxError で exit 1）
        child = _script(tmp_path, "gate.mjs",
                        "<<<<<<< HEAD\nprocess.exit(0);\n=======\nprocess.exit(0);\n>>>>>>> x\n")
        r = _shim("node", child)
        err = r.stderr.decode("utf-8")
        assert r.returncode == 2
        assert _BROKEN in err and "SyntaxError" in err

    def test_python_syntax_error(self, tmp_path):
        child = _script(tmp_path, "gate.py", "def (:\n")
        r = _shim(sys.executable, child)
        err = r.stderr.decode("utf-8")
        assert r.returncode == 2
        assert _BROKEN in err and "SyntaxError" in err

    def test_python_import_failure(self, tmp_path):
        child = _script(tmp_path, "gate.py", "import no_such_module_b341\n")
        r = _shim(sys.executable, child)
        assert r.returncode == 2
        assert _BROKEN in r.stderr.decode("utf-8")

    def test_missing_program(self):
        r = _shim("no-such-interpreter-b341", "x.py")
        assert r.returncode == 2
        assert _BROKEN in r.stderr.decode("utf-8")

    def test_no_child_given(self):
        r = _shim()
        assert r.returncode == 2

    def test_stop_hook_active_informs_instead_of_looping(self, tmp_path):
        """再入した Stop では止めずに知らせる（gate.mjs と同じループ安全）。"""
        child = _script(tmp_path, "gate.py", "def (:\n")
        r = _shim(sys.executable, child,
                  stdin=json.dumps({"hook_event_name": "Stop", "stop_hook_active": True}))
        assert r.returncode == 0
        assert _BROKEN in json.loads(r.stdout.decode("utf-8"))["systemMessage"]

    def test_pretooluse_blocks_even_when_flag_present(self, tmp_path):
        """ループの心配が無い PreToolUse では、いつも閉じる。"""
        child = _script(tmp_path, "gate.py", "def (:\n")
        r = _shim(sys.executable, child,
                  stdin=json.dumps({"hook_event_name": "PreToolUse", "stop_hook_active": True}))
        assert r.returncode == 2


@pytest.mark.parametrize("name", sorted(n for n in os.listdir(_QA) if n.endswith(".mjs")))
def test_tracked_gates_parse(name):
    """殻と git に乗る門が読めること（殻が壊れたら殻では守れない＝CI で落とす）。"""
    r = subprocess.run(["node", "--check", os.path.join(_QA, name)],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr


def test_blocking_hooks_go_through_the_shim():
    """止める力を持つ口（Stop・PreToolUse）は全部、殻を経由して配線されていること。"""
    path = os.path.join(_REPO, ".claude", "settings.local.json")
    if not os.path.exists(path):
        pytest.skip(structural_skip(".claude/ は git-ignore（CI には存在しない）。"))
    with open(path, encoding="utf-8") as f:
        hooks = json.load(f).get("hooks", {})
    bare = [h.get("command", "") for ev in ("Stop", "PreToolUse")
            for g in hooks.get(ev, []) for h in g.get("hooks", [])
            if "hook-shim.mjs" not in h.get("command", "")]
    assert not bare, f"殻を経由していない門: {bare}"
