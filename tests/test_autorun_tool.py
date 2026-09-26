"""無人の駆動スクリプト（`tools/autorun/run.ps1`・I-181）の検査。

ここが守るのは製品の振る舞いではなく、**駆動スクリプトについて私たちが書く主張**
（push しない・許可を聞かない・入力文は正典のファイルから・票が足りなければ走らない）。
git と claude には触らない＝`-DryRun` が出す計画（JSON）だけを見る。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "autorun"
SCRIPT = TOOL / "run.ps1"
PROMPT = TOOL / "prompt_worker.txt"
TEMPLATE = TOOL / "ticket_template.md"

_PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(_PWSH is None, reason="pwsh が無い環境")


def _dry_run(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_PWSH, "-NoProfile", "-File", str(SCRIPT), "-Tickets", str(target), "-DryRun"],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)


def _ticket(dir_: Path, name: str, head: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / name
    p.write_text(f"---\n{head}\n---\n\n## 受け入れ条件\n\n- x\n", encoding="utf-8")
    return p


GOOD = "issues: B-294 B-303\naccept: tests/test_release_check.py\nscope: tools/qa-hook/"


@pytest.fixture
def plan(tmp_path):
    if _PWSH is None:
        pytest.skip("pwsh が無い環境")
    _ticket(tmp_path / "3.9", "01-first.md", GOOD)
    _ticket(tmp_path / "3.9", "02-second.md", GOOD + "\nmax_turns: 30")
    r = _dry_run(tmp_path / "3.9")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_prompt_is_read_from_a_file_with_fixed_slots():
    """入力文は正典のファイルから読み、差し込み口は 4 つだけ＝その場で指示を足さない。"""
    assert PROMPT.name in SCRIPT.read_text(encoding="utf-8")
    slots = set(re.findall(r"\{([A-Z_]+)\}", PROMPT.read_text(encoding="utf-8")))
    assert slots == {"TICKET_PATH", "BRANCH", "RESULT_PATH", "PRIOR"}, slots


def test_push_is_blocked_by_structure_not_only_by_rules():
    """⛔ push を塞ぐのは字面の拒否規則だけにしない＝子プロセスの push 先を差し替える。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "remote.origin.pushurl" in src and "GIT_CONFIG_COUNT" in src


def test_branches_stack_in_ticket_order(plan):
    assert [t["branch"] for t in plan["tickets"]] == [
        "auto/3.9-01-first", "auto/3.9-02-second"]
    assert plan["tickets"][1]["max_turns"] == 30


def test_the_worker_cannot_ask_or_escape(plan):
    """無人＝聞けない・フックを飛ばさない・push/gh/リリースの道具を持たない。"""
    args = [str(a) for a in plan["claude_args"]]
    assert args[args.index("--permission-prompts") + 1] == "none"
    for banned in ("--bare", "--dangerously-skip-permissions",
                   "--allow-dangerously-skip-permissions"):
        assert banned not in args, f"{banned} を渡している（フックか許可の確認を飛ばす）"
    for need in ("--strict-mcp-config", "--disable-slash-commands", "--max-turns"):
        assert need in args
    denied = args[args.index("--disallowedTools") + 1:]
    for rule in ("Bash(git push *)", "PowerShell(git push *)", "Bash(gh *)",
                 "PowerShell(gh *)", "AskUserQuestion"):
        assert rule in denied, rule


def test_the_first_prompt_carries_ticket_branch_and_result(plan):
    p = plan["first_prompt"]
    assert "3.9/01-first.md" in p and "auto/3.9-01-first" in p and "<RESULT_PATH>" in p
    assert "{" + "PRIOR}" not in p


@needs_pwsh
def test_a_single_ticket_file_is_a_plan_of_one(tmp_path):
    """票 1 枚を直接渡しても計画になる（foreach が辞書そのものを返す罠）。"""
    p = _ticket(tmp_path / "3.9", "01-only.md", GOOD)
    r = _dry_run(p)
    assert r.returncode == 0, r.stderr
    assert [t["branch"] for t in json.loads(r.stdout)["tickets"]] == ["auto/3.9-01-only"]


@needs_pwsh
@pytest.mark.parametrize("head, word", [
    ("issues: B-294\nscope: tools/", "accept"),
    ("accept: tests/x.py\nscope: tools/", "issues"),
    ("issues: B-294\naccept: tests/x.py", "scope"),
    ("issues: B294\naccept: tests/x.py\nscope: tools/", "課題 ID"),
    (GOOD + "\nowner: me", "知らない項目"),
])
def test_an_incomplete_ticket_stops_before_anything_runs(tmp_path, head, word):
    """足りない票は回す前に落とす＝無人で走り出してから気づくと一番高い所で止まる。"""
    _ticket(tmp_path / "3.9", "01-bad.md", head)
    r = _dry_run(tmp_path / "3.9")
    assert r.returncode != 0 and word in (r.stderr + r.stdout)


@needs_pwsh
def test_the_template_is_a_valid_ticket(tmp_path):
    """雛形そのものが票として読める＝写して埋めれば回る。"""
    d = tmp_path / "3.9"
    d.mkdir()
    # 公開物なので雛形の ID は B-nnn の形（非公開の台帳を指さない）＝埋めてから読ませる
    (d / "01-template.md").write_text(
        TEMPLATE.read_text(encoding="utf-8").replace("nnn", "001"), encoding="utf-8")
    r = _dry_run(d)
    assert r.returncode == 0, r.stderr
