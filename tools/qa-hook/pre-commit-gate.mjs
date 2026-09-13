#!/usr/bin/env node
// RadioSim QA PreToolUse hook — the FULL pytest suite, once, right before a
// commit leaves the machine (I-154).
//
// WHY THIS EXISTS: gate.mjs (the Stop hook) used to run the whole suite every
// turn; that was slow (7m28s and rising) for no benefit on turns that touched
// one file. 2026-09-13 that Stop-hook run was SCOPED to the tests the turn's
// changes justify (see scopedTests() in gate.mjs) — fast, but it no longer
// proves the whole tree is green. This hook is where that proof still
// happens: it intercepts `git commit` / `git push` and blocks them unless the
// full suite passes for the exact working-tree content (cache-shared with
// gate.mjs via pytest-cache.mjs, keyed under a distinct "full-suite" scope so
// a scoped pass never counts as a full pass or vice versa).
//
// Cheap on every OTHER command: the regex check below runs before anything
// else, so a Read/Edit/ordinary Bash call costs nothing here.
//
// Loop safety / never blocks progress on its own bug: any unexpected error
// here allows the command through (this hook can only ADD a gate in front of
// commit/push, never become the reason nothing can run at all).

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { resolvePython } from "./gate.mjs";
import { pytestCacheKey, isCachedPass, recordPass, recordFinish, markStart } from "./pytest-cache.mjs";

const FULL_SCOPE = "full-suite";
const MAX_OUT = 3000;

// Matches `git commit` / `git push` as a command position (not inside a
// string, not as a substring of a longer word) — segments split on the usual
// shell separators, same idea as .claude/no_shell_detours.py but only for
// these two subcommands, so no PATH-token quoting subtlety matters here: a
// false negative just means the full suite is checked one command later (at
// the next commit/push in the turn), never a false block.
const COMMIT_OR_PUSH = /(?:^|[;&|\n]|\)\s*)\s*git(?:\.exe)?\s+(?:commit|push)\b/;

function readStdin() {
  try {
    return readFileSync(0, "utf-8");
  } catch {
    return "";
  }
}

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
  process.exit(0);
}

function deny(reason) {
  emit({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: reason,
    },
  });
}

function tail(s, n) {
  s = (s || "").trim();
  return s.length > n ? "…\n" + s.slice(-n) : s;
}

function main() {
  let input = {};
  try {
    input = JSON.parse(readStdin() || "{}");
  } catch {
    /* ignore */
  }
  if (!["Bash", "PowerShell"].includes(input.tool_name)) return;
  const command = (input.tool_input || {}).command || "";
  if (!COMMIT_OR_PUSH.test(command)) return;

  const cwd = input.cwd || process.cwd();
  if (!existsSync(join(cwd, "tests"))) return; // not this repo's working tree

  const resolved = resolvePython();
  if (resolved.error) {
    deny(`コミット前提のフル QA ゲートが走れません。\n\n${resolved.error}`);
    return;
  }

  const key = pytestCacheKey(cwd, FULL_SCOPE);
  if (isCachedPass(cwd, key)) return; // already proven green for this exact content

  markStart(cwd, key);
  const started = Date.now();
  let r;
  try {
    const stdout = execFileSync(resolved.python, ["-m", "pytest"], {
      cwd,
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "pipe"],
      maxBuffer: 10 * 1024 * 1024,
    });
    r = { code: 0, stdout, stderr: "" };
  } catch (err) {
    r = {
      code: typeof err.status === "number" ? err.status : 1,
      stdout: err.stdout || "",
      stderr: err.stderr || "",
    };
  }
  const ms = Date.now() - started;

  if (r.code !== 0) {
    recordFinish(cwd, key, false, ms);
    deny(
      "⛔ コミット前提のフルスイートが赤です（I-154＝毎ターンは影響範囲だけ・" +
      "フルはコミット直前に 1 回）。直してから commit/push をやり直してください。\n\n" +
      `\`\`\`\n${tail(r.stdout + r.stderr, MAX_OUT)}\n\`\`\``);
    return;
  }
  recordPass(cwd, key, ms);
}

const invokedDirectly =
  process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop());

if (invokedDirectly) {
  try {
    main();
  } catch (err) {
    process.stderr.write(`pre-commit-gate error: ${err && err.message}\n`);
  }
  process.exit(0);
}
