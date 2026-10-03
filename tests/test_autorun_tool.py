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
import sys
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


@needs_pwsh
def test_the_dry_run_does_not_need_userprofile(tmp_path, monkeypatch):
    """CI（Linux）には USERPROFILE が無い＝`$env:USERPROFILE` に頼るとどの検査も最初に落ちる。

    2026-09-27 の main の CI が赤になった形（Windows の手元では通るので、ここで塞ぐ）。
    """
    monkeypatch.delenv("USERPROFILE", raising=False)
    r = _relay_dry_run(_handoff(tmp_path, "status: continue\ngoal: 3.9 のステージ2まで"))
    assert r.returncode == 0, r.stderr


def test_find_claude_is_shared_not_copied():
    """`claude` の探し方は 1 か所＝版の比べ方を直したとき片方だけ古く残さない。"""
    src = RELAY.read_text(encoding="utf-8")
    assert "common.ps1" in src and "function Find-Claude" not in src


def test_the_relay_prompt_is_read_from_a_file_with_fixed_slots():
    assert RELAY_PROMPT.name in RELAY.read_text(encoding="utf-8")
    slots = set(re.findall(r"\{([A-Z_]+)\}", RELAY_PROMPT.read_text(encoding="utf-8")))
    assert slots == {"SESSION_NO", "PREV_HANDOFF", "HANDOFF_PATH"}, slots


def test_the_second_session_can_take_over_the_record_name():
    """n 本目の終わりに写しを置く名前へ、n+1 本目の前に引き継ぎ書を移す＝上書きで移す。

    2026-09-27 の 2 回目の試しで、2 本目の前の移動が「既にある」で止まった（1 本で
    `done` になった 1 回目では通らない道）。止まった理由は窓だけでなく記録にも残す。
    """
    src = RELAY.read_text(encoding="utf-8")
    move = re.search(r"Move-Item -LiteralPath \$handoffPath -Destination \$prev(?P<rest>[^\r\n]*)", src)
    assert move and "-Force" in move["rest"], "写しと同じ名前へ移すので -Force が要る"
    assert re.search(r"\}\s*catch\s*\{\s*(#[^\n]*\n\s*)*Write-Log", src), "止まった理由を relay.log に書く"


def test_push_waits_for_the_goal_and_done_closes_the_window():
    """途中の引き継ぎはコミットだけ・目標まで済んだら push して窓を閉じる
    （2026-09-27 ユーザー指示）。窓を閉じるのは `done` で push まで済んだときだけ。"""
    prompt = RELAY_PROMPT.read_text(encoding="utf-8")
    handoff_step = next(ln for ln in prompt.splitlines() if ln.startswith("4. "))
    done_step = next(ln for ln in prompt.splitlines() if ln.startswith("5. "))
    assert "コミットだけ" in handoff_step and "push しない" in handoff_step, handoff_step
    assert "push" in done_step and "status: done" in done_step, done_step

    src = RELAY.read_text(encoding="utf-8")
    start = re.search(r"\$fwd = @\((?P<args>[^)]*)\)", src)
    assert start and "-NoExit" not in start["args"], "-NoExit だと done でも窓が残る"
    closes = re.search(r"if \(\$finished -and \$unpushed -eq 0\) \{(?P<body>[^}]*)\}", src)
    assert closes and "$keepOpen = $false" in closes["body"], "push まで済んだ done だけが窓を閉じる"
    assert "if ($keepOpen) { Wait-BeforeClose }" in src, "ほかの終わり方は理由を読めるよう待つ"


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


def test_the_watch_loop_speaks_while_the_state_stays(relay_plan):
    """同じ状態が続く間も一定の間隔で 1 行書き足す（I-188）。

    2026-09-27 のステージ6のリレーで、push のゲートのフル（677 秒）の間 `relay.log` が
    21 分間 1 行も増えず、見ている人は「止まった」と「走っている」を区別できなかった。
    """
    assert relay_plan["beat_seconds"] == 300
    src = RELAY.read_text(encoding="utf-8")
    start = re.search(r"\$fwd = @\((?P<args>[^)]*)\)", src)
    assert start and "'-BeatSeconds', $BeatSeconds" in start["args"], "-Start で別の窓へ間隔が渡らない"
    beat = re.search(r"elseif \(\(\$now - \$lastBeat\)\.TotalSeconds -ge \$BeatSeconds\) \{(?P<body>.*?)\n            \}",
                     src, re.S)
    assert beat, "状態が変わらない間の書き足しの枝が無い"
    assert "Write-Log (Format-Beat" in beat["body"] and "$lastBeat = $now" in beat["body"]
    change = re.search(r"if \(\$st -ne \$lastSt\) \{(?P<body>.*?)\}\s*elseif", src, re.S)
    assert change and "$stSince = $now" in change["body"], "状態が変わったら経過時間を数え直す"


def _common(tmp_path: Path, script: str) -> str:
    """common.ps1 を読み込んで 1 行の式を試し、出力を返す。"""
    r = subprocess.run(
        [_PWSH, "-NoProfile", "-Command",
         f". '{TOOL / 'common.ps1'}'; $ErrorActionPreference = 'Stop'; {script}"],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@needs_pwsh
def test_the_beat_without_a_transcript_says_only_the_elapsed_time(tmp_path):
    """記録が無い・読めないときは落ちずに経過時間だけ書く（I-188）。"""
    out = _common(tmp_path,
                  "$m = Get-LastMove (Join-Path $PWD 'none.jsonl'); "
                  "Write-Output ($null -eq $m); "
                  "Write-Output ($null -eq (Find-Transcript (Join-Path $PWD 'nodir') 'abc')); "
                  "Format-Beat 'busy/working' ([TimeSpan]::FromSeconds(401)) $m")
    assert out.splitlines() == ["True", "True", "  … busy/working のまま 6 分"], out


@needs_pwsh
def test_the_beat_reads_the_last_tool_call_from_the_transcript(tmp_path):
    """末尾の切り口の壊れた行・道具の結果の行を飛ばし、最後の呼び出しの説明を書く。"""
    proj = tmp_path / "projects" / "D--dev-x"
    proj.mkdir(parents=True)
    rows = [
        {"type": "assistant", "timestamp": "2026-09-27T02:40:00.000Z",
         "message": {"content": [{"type": "tool_use", "name": "Bash",
                                  "input": {"command": "git commit", "description": "古い手"}}]}},
        {"type": "assistant", "timestamp": "2026-09-27T02:48:56.000Z",
         "message": {"content": [{"type": "text", "text": "送ります"},
                                 {"type": "tool_use", "name": "Bash",
                                  "input": {"command": "git push", "description": "ステージ6のコミットを main へ送る"}}]}},
        # 道具の結果の行（tool_use_id・中身に tool_use の字）は、呼び出しとは数えない
        {"type": "user", "timestamp": "2026-09-27T02:50:00.000Z",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "x",
                                  "content": "grep '\"tool_use\"' の出力"}]}},
    ]
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    (proj / "sid-1.jsonl").write_text('{"broken": "line cut by the tail rea\n' + body, encoding="utf-8")
    out = _common(tmp_path,
                  "$p = Find-Transcript (Join-Path $PWD 'projects') 'sid-1'; "
                  "$m = Get-LastMove $p; "
                  "$exp = [DateTimeOffset]::Parse('2026-09-27T02:48:56Z').LocalDateTime.ToString('HH:mm:ss'); "
                  "Write-Output $exp; "
                  "Format-Beat 'busy/working' ([TimeSpan]::FromMinutes(11)) $m")
    exp, line = out.splitlines()
    assert line == f"  … busy/working のまま 11 分・最後の手＝ステージ6のコミットを main へ送る（{exp} から）", out


@needs_pwsh
@pytest.mark.skipif(sys.platform != "win32", reason="PATH の区切りと .exe は Windows の形")
def test_find_claude_skips_a_script_on_path(tmp_path):
    """PATH の `claude.cmd` は直に起動できない＝先に在っても採らず、後ろの `.exe` を採る（B-313）。"""
    scripts, exes = tmp_path / "npm", tmp_path / "bin"
    scripts.mkdir()
    exes.mkdir()
    (scripts / "claude.cmd").write_text("@echo off\n", encoding="utf-8")
    (exes / "claude.exe").write_bytes(b"")
    out = _common(tmp_path,
                  "$env:CLAUDE_EXE = $null; "
                  f"$env:PATH = '{scripts};{exes}'; "
                  "Write-Output (Find-Claude); "
                  f"$env:PATH = '{scripts}'; "
                  "Write-Output $(try { Find-Claude } catch { 'THROW' })")
    first, only_script = out.splitlines()
    assert first == str(exes / "claude.exe"), out
    assert not only_script.lower().endswith((".cmd", ".ps1")), out   # 拡張の実体か、止まるか


@needs_pwsh
def test_start_quotes_the_arguments_for_the_new_window(tmp_path):
    """空白・引用符・末尾の \\ を含む値が、子のプロセスで元の 1 引数に戻る（B-314）。"""
    src = RELAY.read_text(encoding="utf-8")
    assert "Start-Process pwsh -ArgumentList (Join-ProcessArgs $fwd)" in src, "配列のまま渡すと空白で割れる"
    echo = tmp_path / "argv.py"
    echo.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    vals = [r"C:\a b\relay.ps1", "plain", "", 'x"y', "trail\\", "C:\\dir with space\\", '\\"q']
    lit = ", ".join("'" + v.replace("'", "''") + "'" for v in vals)
    out = _common(tmp_path,
                  f"$psi = [Diagnostics.ProcessStartInfo]::new('{sys.executable}', "
                  f"('\"{echo}\" ' + (Join-ProcessArgs @({lit})))); "
                  "$psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; "
                  "$p = [Diagnostics.Process]::Start($psi); $p.StandardOutput.ReadToEnd()")
    assert json.loads(out) == vals, out


@needs_pwsh
def test_the_handoff_template_is_a_valid_handoff(tmp_path):
    p = tmp_path / "handoff.md"
    p.write_text(HANDOFF_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    r = _relay_dry_run(p)
    assert r.returncode == 0, r.stderr
