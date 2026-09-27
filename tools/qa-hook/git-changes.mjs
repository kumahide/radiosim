// Shared git helpers for the QA Stop hook (used by gate.mjs + llm-review.mjs).

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export function git(cwd, args) {
  return execFileSync("git", args, {
    cwd,
    encoding: "utf-8",
    stdio: ["ignore", "pipe", "ignore"],
  });
}

// B-312 (2026-09-27): WHICH REPOSITORY a hook is looking at. Claude Code hands
// every hook the session's working directory (`input.cwd`), and that follows a
// Bash `cd` — out of this repo into the memory folder, say. Both gates used to
// take it as "the repo": from outside, the Stop gate found no git repo and
// exited silently every turn, and the commit gate found no tests/ and let
// `git -C <this repo> commit` through without a single test. The repository a
// hook guards is the one it lives in (tools/qa-hook/ → two levels up); a linked
// worktree of it is the same repository (same common git dir).
export const HOOK_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

/** Two filesystem paths for the same place? (Windows: case and `\` vs `/`.) */
export function samePath(a, b) {
  const norm = (p) => {
    const r = resolve(p).replace(/\\/g, "/").replace(/\/+$/, "");
    return process.platform === "win32" ? r.toLowerCase() : r;
  };
  return norm(a) === norm(b);
}

/** `{top, common}` of the repository git resolves from `cwd` plus the
 *  location options it would be given (`-C <p>`, `--git-dir=<p>`,
 *  `--work-tree=<p>`), or null (not a working tree / not there). */
export function repoAt(cwd, locationOpts = []) {
  try {
    const out = execFileSync("git", [
      ...locationOpts, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir",
    ], { cwd, encoding: "utf-8", stdio: ["ignore", "pipe", "ignore"] });
    const [top, common] = out.trim().split(/\r?\n/);
    return top && common ? { top, common } : null;
  } catch {
    return null;
  }
}

/** The working tree a per-turn hook should look at: `cwd`'s own tree when it is
 *  this repository (a linked worktree included), otherwise this hook's own —
 *  never "no repo here, nothing to check". */
export function hookTreeFor(cwd) {
  const own = repoAt(HOOK_ROOT);
  if (!own) return HOOK_ROOT; // a copy outside any repo: the caller finds no git and stops
  const here = cwd ? repoAt(cwd) : null;
  return here && samePath(here.common, own.common) ? here.top : own.top;
}

/** Changed *.py entries from `git status --porcelain` (excluding .venv). */
export function changedPyEntries(cwd) {
  const out = git(cwd, ["status", "--porcelain"]);
  const entries = [];
  for (const raw of out.split("\n")) {
    if (!raw.trim()) continue;
    const status = raw.slice(0, 2);
    let path = raw.slice(3);
    if (path.includes(" -> ")) path = path.split(" -> ").pop(); // rename
    path = path.replace(/^"|"$/g, "");
    const norm = path.replace(/\\/g, "/");
    if (!norm.endsWith(".py")) continue;
    if (norm.includes(".venv/") || norm.startsWith(".venv")) continue;
    entries.push({ status, path: norm });
  }
  return [...new Map(entries.map((e) => [e.path, e])).values()];
}

/** Every changed path from `git status --porcelain` (any extension, excluding
 *  .venv) — unlike changedPyEntries, not limited to *.py. Used to decide which
 *  EXTRA_GATES prefixes (docs/, lang/, requirements, …) apply this turn. */
export function changedAllPaths(cwd) {
  const out = git(cwd, ["status", "--porcelain"]);
  const paths = [];
  for (const raw of out.split("\n")) {
    if (!raw.trim()) continue;
    let path = raw.slice(3);
    if (path.includes(" -> ")) path = path.split(" -> ").pop(); // rename
    path = path.replace(/^"|"$/g, "");
    const norm = path.replace(/\\/g, "/");
    if (norm.includes(".venv/") || norm.startsWith(".venv")) continue;
    paths.push(norm);
  }
  return [...new Set(paths)];
}

/** Is this a deleted entry? (no content to check) */
export function isDeleted(entry) {
  return entry.status.includes("D");
}

/** Set of new-side line numbers (1-based) changed in this file vs HEAD.
 *  Untracked / no-HEAD -> every line. Used to diff-scope linter findings so
 *  pre-existing violations never gate; only newly introduced ones do. */
export function changedLinesForFile(cwd, entry) {
  const allLines = () => {
    const n = readFileSync(join(cwd, entry.path), "utf-8").split("\n").length;
    const s = new Set();
    for (let i = 1; i <= n; i++) s.add(i);
    return s;
  };
  if (entry.status.includes("?")) return allLines();
  let diff;
  try {
    diff = git(cwd, ["diff", "HEAD", "--", entry.path]);
  } catch {
    return allLines();
  }
  const set = new Set();
  let newLine = 0;
  for (const line of diff.split("\n")) {
    const m = line.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (m) {
      newLine = parseInt(m[1], 10);
      continue;
    }
    if (line.startsWith("+++")) continue;
    if (line.startsWith("+")) {
      set.add(newLine);
      newLine++;
    } else if (line.startsWith("-")) {
      /* old side — no new line */
    } else if (line.startsWith(" ")) {
      newLine++;
    }
  }
  return set;
}

/** Changed *.py {path, diff} for a commit range (e.g. "main..HEAD", "<base>..HEAD").
 *  Used by the pre-push / on-demand LLM review (which review committed history,
 *  not the working tree). Excludes .venv. */
export function filesFromRange(cwd, range) {
  let names;
  try {
    names = git(cwd, ["diff", range, "--name-only", "--", "*.py"]);
  } catch {
    return [];
  }
  const files = [];
  for (const raw of names.split("\n")) {
    const p = raw.trim();
    if (!p) continue;
    const norm = p.replace(/\\/g, "/");
    if (!norm.endsWith(".py")) continue;
    if (norm.includes(".venv/") || norm.startsWith(".venv")) continue;
    let diff;
    try {
      diff = git(cwd, ["diff", range, "--", p]).trimEnd();
    } catch {
      continue;
    }
    if (diff && diff.trim()) files.push({ path: norm, diff });
  }
  return files;
}

/** Unified diff for one file: tracked -> `git diff HEAD`; untracked -> whole file as added. */
export function diffForFile(cwd, entry) {
  if (entry.status.includes("?")) {
    const body = readFileSync(join(cwd, entry.path), "utf-8");
    const added = body.split("\n").map((l) => "+" + l).join("\n");
    return `--- /dev/null\n+++ b/${entry.path}\n${added}`;
  }
  try {
    return git(cwd, ["diff", "HEAD", "--", entry.path]).trimEnd();
  } catch {
    const body = readFileSync(join(cwd, entry.path), "utf-8");
    return body.split("\n").map((l) => "+" + l).join("\n");
  }
}
