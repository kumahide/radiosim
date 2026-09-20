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
//
// COMMIT ISLANDS (2026-09-19): a `git commit` whose changes all fall inside one
// "island" of gate-scope.json (e.g. the Field app, which nothing in the main
// app imports) runs that island's tests + the repo-wide scanners instead of
// the whole suite — Field commits were paying ~9 min each for tests their
// changes cannot reach. `git push` always stays FULL: it is still the proof
// that the whole tree is green before anything leaves the machine.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { resolvePython } from "./gate.mjs";
import { git } from "./git-changes.mjs";
import { pytestCacheKey, isCachedPass, recordPass, recordFinish, markStart } from "./pytest-cache.mjs";

const FULL_SCOPE = "full-suite";
const MAX_OUT = 3000;
const PUSH = /(?:^|[;&|\n]|\)\s*)\s*git(?:\.exe)?\s+push\b/;

/** Every path a commit made now could carry: working tree vs HEAD, untracked
 *  included, and BOTH sides of a rename (moving a file out of core/ into an
 *  island must not look like an island-only change). */
export function commitPaths(cwd) {
  const out = git(cwd, ["status", "--porcelain"]);
  const paths = new Set();
  for (const raw of out.split("\n")) {
    if (!raw.trim()) continue;
    for (const p of raw.slice(3).split(" -> ")) {
      const norm = p.replace(/^"|"$/g, "").replace(/\\/g, "/");
      if (norm.includes(".venv/") || norm.startsWith(".venv")) continue;
      paths.add(norm);
    }
  }
  return [...paths];
}

/** The island every path fits in, or null (→ full suite). An empty change set
 *  is null too: there is nothing to scope by, so the honest answer is "full". */
export function islandFor(scope, paths) {
  if (!paths.length) return null;
  for (const island of scope.commit_islands || []) {
    if (paths.every((p) => island.prefixes.some((pre) => p.startsWith(pre)))) return island;
  }
  return null;
}

/** An island's pytest targets, `tests/foo_*` expanded against tests/ (pytest
 *  itself does not glob, and execFileSync runs no shell). */
export function islandTargets(cwd, scope, island) {
  const targets = new Set([scope.always_tests]);
  let names = [];
  try {
    names = readdirSync(join(cwd, "tests"));
  } catch {
    /* no tests dir: only literal targets */
  }
  for (const t of island.tests) {
    if (!t.endsWith("*")) {
      targets.add(t);
      continue;
    }
    const stem = t.slice("tests/".length, -1);
    for (const n of names) if (n.startsWith(stem) && n.endsWith(".py")) targets.add(`tests/${n}`);
  }
  return [...targets].filter((t) => existsSync(join(cwd, t))).sort();
}

function loadScope(cwd) {
  try {
    return JSON.parse(readFileSync(join(cwd, "tools", "qa-hook", "gate-scope.json"), "utf-8"));
  } catch {
    return { always_tests: "tests/test_repo_hygiene.py" }; // no islands → always full
  }
}

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

  // A full pass also covers any island run on the same content — check it first.
  const fullKey = pytestCacheKey(cwd, FULL_SCOPE);
  if (isCachedPass(cwd, fullKey)) return; // already proven green for this exact content

  let key = fullKey;
  let targets = [];
  let label = "フルスイート";
  if (!PUSH.test(command)) {
    const scope = loadScope(cwd);
    const island = islandFor(scope, commitPaths(cwd));
    if (island) {
      targets = islandTargets(cwd, scope, island);
      key = pytestCacheKey(cwd, `island:${island.name}\n${targets.join("\n")}`);
      label = `島「${island.name}」のテスト（${targets.length} 本）`;
      if (isCachedPass(cwd, key)) return;
    }
  }

  markStart(cwd, key);
  const started = Date.now();
  let r;
  try {
    const stdout = execFileSync(resolved.python, ["-m", "pytest", ...targets], {
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
      `⛔ コミット前提の${label}が赤です（I-154＝毎ターンは影響範囲だけ・` +
      "フルはコミット直前に 1 回／島に収まるコミットは島のテストだけ）。" +
      "直してから commit/push をやり直してください。\n\n" +
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
