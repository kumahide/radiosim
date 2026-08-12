// Shared git helpers for the QA Stop hook (used by gate.mjs + llm-review.mjs).

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

export function git(cwd, args) {
  return execFileSync("git", args, {
    cwd,
    encoding: "utf-8",
    stdio: ["ignore", "pipe", "ignore"],
  });
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
