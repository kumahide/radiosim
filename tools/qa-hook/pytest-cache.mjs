// Skip-the-rerun cache for the QA Stop hook's pytest step (I-056).
//
// WHY: the gate fires whenever the working tree has a changed *.py, and pytest
// runs the WHOLE suite. Holding an uncommitted .py across several conversation
// turns therefore re-ran the identical suite once per turn — "the gate rings
// every time" (feedback-promote-recurring-checks, failure mode 2). The gate was
// not wrong, it was just answering a question nobody had asked again.
//
// The fix must not cost detection power: the cache key is the *content* of
// everything the suite could read, so any real change misses the cache and the
// suite runs. Specifically the key covers
//   1. HEAD (a commit / checkout / rebase invalidates everything),
//   2. every dirty path in `git status --porcelain` — ANY extension, not just
//      .py, because tests/test_docs_consistency.py reads README/docs — hashed
//      by content, so touching a file without changing it does not invalidate,
//   3. the size+mtime of the locally-tested but git-ignored trees (`.claude/`
//      Python hooks and `tools/qa-hook/*.mjs`), which `git status` cannot see
//      yet tests/test_claude_hooks.py verifies — and which include this file.
//
// Anything unexpected (not a git repo, an oversized file, a corrupt cache)
// answers "run it": the cache may only ever remove a *redundant* run.

import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { git } from "./git-changes.mjs";

// A dirty file bigger than this is not worth hashing every turn -> just run.
const MAX_HASH_BYTES = 8 * 1024 * 1024;

// git-ignored trees whose contents the suite still verifies (test_claude_hooks).
const UNTRACKED_DEPS = [
  { dir: ".claude", suffix: ".py" },
  { dir: "tools/qa-hook", suffix: ".mjs" },
];

export const CACHE_PATH = join(".git", "radiosim-qa-pytest.json");

function statLines(cwd, { dir, suffix }) {
  const abs = join(cwd, dir);
  let names;
  try {
    names = readdirSync(abs);
  } catch {
    return [];
  }
  const lines = [];
  for (const name of names.sort()) {
    if (!name.endsWith(suffix)) continue;
    try {
      const st = statSync(join(abs, name));
      lines.push(`${dir}/${name}\t${st.size}\t${Math.round(st.mtimeMs)}`);
    } catch {
      lines.push(`${dir}/${name}\tunreadable`);
    }
  }
  return lines;
}

/** Cache key for the current working tree, or null if it cannot be computed
 *  (caller must then run pytest). */
export function pytestCacheKey(cwd) {
  let head;
  try {
    head = git(cwd, ["rev-parse", "HEAD"]).trim();
  } catch {
    head = "no-head";
  }

  let status;
  try {
    status = git(cwd, ["status", "--porcelain"]);
  } catch {
    return null; // not a git repo
  }

  const parts = [`head\t${head}`];
  for (const raw of status.split("\n")) {
    if (!raw.trim()) continue;
    const state = raw.slice(0, 2);
    let path = raw.slice(3);
    if (path.includes(" -> ")) path = path.split(" -> ").pop(); // rename
    path = path.replace(/^"|"$/g, "").replace(/\\/g, "/");
    if (path.includes(".venv/") || path.startsWith(".venv")) continue;
    const abs = join(cwd, path);
    if (!existsSync(abs)) {
      parts.push(`${state}\t${path}\tgone`); // deleted: no content to hash
      continue;
    }
    let st;
    try {
      st = statSync(abs);
    } catch {
      return null;
    }
    if (st.isDirectory()) {
      // An untracked directory is reported as one entry; hashing it would mean
      // walking it. Rare enough that "just run" is the honest answer.
      return null;
    }
    if (st.size > MAX_HASH_BYTES) return null;
    try {
      parts.push(`${state}\t${path}\t${createHash("sha256").update(readFileSync(abs)).digest("hex")}`);
    } catch {
      return null;
    }
  }

  for (const dep of UNTRACKED_DEPS) parts.push(...statLines(cwd, dep));

  return createHash("sha256").update(parts.join("\n")).digest("hex");
}

/** Did pytest already pass for exactly this key? */
export function isCachedPass(cwd, key) {
  if (!key) return false;
  try {
    const data = JSON.parse(readFileSync(join(cwd, CACHE_PATH), "utf-8"));
    return data && data.passedKey === key;
  } catch {
    return false;
  }
}

/** Remember that pytest passed for this key (best effort). */
export function recordPass(cwd, key) {
  if (!key) return;
  try {
    writeFileSync(
      join(cwd, CACHE_PATH),
      JSON.stringify({ passedKey: key, at: new Date().toISOString() }) + "\n",
      "utf-8",
    );
  } catch {
    /* the cache is an optimisation; failing to write it only costs a rerun */
  }
}
