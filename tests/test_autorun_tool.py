"""リレー（`tools/autorun/relay.ps1`・I-186）の検査。

ここが守るのは製品の振る舞いではなく、**リレーについて私たちが書く主張**
（入力文は正典のファイルから・フックも許可の確認も飛ばさない・引き継ぎ書が足りなければ走らない）。
git と claude には触らない＝`-DryRun` が出す計画（JSON）だけを見る。
作業票方式（`run.ps1`・I-181/I-182）はリレーに置き換えて廃止した。
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

_PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(_PWSH is None, reason="pwsh が無い環境")


# ============================================================
# リレー（relay.ps1・I-186）＝セッションを自動でつなぐ
# ============================================================
RELAY = TOOL / "relay.ps1"
RELAY_PROMPT = TOOL / "prompt_relay.txt"
HANDOFF_TEMPLATE = TOOL / "handoff_template.md"


def _relay_dry_run(handoff: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_PWSH, "-NoProfile", "-File", str(RELAY), "-DryRun", "-Handoff", str(handoff), *extra],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)


def _handoff(tmp_path: Path, head: str) -> Path:
    p = tmp_path / "handoff.md"
    p.write_text(f"---\n{head}\n---\n\n## 次の一手\n\n- x\n", encoding="utf-8")
    return p


@pytest.fixture
def relay_plan(tmp_path):
    if _PWSH is None:
        pytest.skip("pwsh が無い環境")
    r = _relay_dry_run(_handoff(tmp_path, "status: continue\ngoal: 3.9 のステージ2まで"),
                       "-Trips", "30")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_find_claude_is_shared_not_copied():
    """`claude` の探し方は 1 か所＝版の比べ方を直したとき片方だけ古く残さない。"""
    src = RELAY.read_text(encoding="utf-8")
    assert "common.ps1" in src and "function Find-Claude" not in src


def test_the_relay_prompt_is_read_from_a_file_with_fixed_slots():
    assert RELAY_PROMPT.name in RELAY.read_text(encoding="utf-8")
    slots = set(re.findall(r"\{([A-Z_]+)\}", RELAY_PROMPT.read_text(encoding="utf-8")))
    assert slots == {"SESSION_NO", "PREV_HANDOFF", "HANDOFF_PATH"}, slots


def test_the_relay_session_is_interactive_and_watchable(relay_plan):
    """裏の対話型・Remote Control つき・フックも許可の確認も飛ばさない。"""
    args = [str(a) for a in relay_plan["claude_args"]]
    assert args[0] == "<PROMPT>", "入力文が先頭に無い＝可変長のオプションに食われる"
    for need in ("--bg", "--remote-control", "--settings"):
        assert need in args, need
    assert "--session-id" not in args, "`--bg` は --session-id を無視する＝id は起動の出力から取る"
    for banned in ("-p", "--print", "--bare", "--dangerously-skip-permissions",
                   "--allow-dangerously-skip-permissions"):
        assert banned not in args, banned


def test_the_relay_env_reaches_the_budget_hook_both_ways(relay_plan):
    """環境変数は起動側と `--settings` の両方で渡す（どちらが届くかは -Probe で確かめる）。"""
    for env in (relay_plan["env"], relay_plan["settings"]["env"]):
        assert env["RADIOSIM_RELAY"] == "1"
        assert env["RADIOSIM_RELAY_TRIPS"] == "30"
        assert env["RADIOSIM_RELAY_HANDOFF"].endswith("handoff.md")
        assert env["CLAUDE_BG_ISOLATION"] == "none", "worktree に閉じ込められると台帳と引き継ぎ書を書けない"
    assert relay_plan["settings"]["autoContinueAtUsageLimit"] is True
    assert relay_plan["settings"]["worktree"]["bgIsolation"] == "none"


@needs_pwsh
@pytest.mark.parametrize("head, word", [
    ("status: continue", "goal"),
    ("status: blocked\ngoal: x", "status"),
    ("status: continue\ngoal: x\nowner: me", "知らない項目"),
])
def test_a_bad_handoff_stops_before_anything_runs(tmp_path, head, word):
    r = _relay_dry_run(_handoff(tmp_path, head))
    assert r.returncode != 0 and word in (r.stderr + r.stdout)


@needs_pwsh
def test_the_handoff_template_is_a_valid_handoff(tmp_path):
    p = tmp_path / "handoff.md"
    p.write_text(HANDOFF_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    r = _relay_dry_run(p)
    assert r.returncode == 0, r.stderr
