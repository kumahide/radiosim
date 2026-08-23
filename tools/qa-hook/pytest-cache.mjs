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

// Trees the suite can only see the *existence* of, never the content (2026-08-23).
// WHY: experiments/ holds one-off probes. They are not product code — nothing in
// the app imports them, they are outside the build, the type check and coverage
// (experiments/README.md) — and since test_smoke's walker skips the directory, no
// test reads a line of them. What the suite still checks is that documented .py
// references resolve (test_docs_consistency), which depends on the file EXISTING,
// not on what it says. So: hash the path, not the bytes. Editing a probe stops
// invalidating the cached pass; adding or deleting one still runs the suite.
//
// The cost of not doing this was concrete: iterating on a probe re-ran the whole
// suite once per turn, which is the same complaint I-056 fixed one level up
// ("the gate rings every time" — feedback-promote-recurring-checks, failure
// mode 2). A gate that answers a question nobody asked is not free; here it was
// the thing making the actual investigation slow.
// 2026-08-23（ユーザー決定）: **experiments/ の全ファイル**へ広げた。当初は .py
// だけで、`experiments/README.md` を除外していた（test_docs_consistency が公開文書
// として課題 ID の混入を検査しており、実際にこの日 1 件捕まえたため）。
// ⚠️ **広げた代償を明示しておく**＝*その turn で experiments/ しか触っていない* 場合、
// README の課題 ID 混入はその場では捕まらない。⇒ 次に他の何かを触った turn、
// 遅くともリリース前の release-check で捕まる（**検出が消えるのではなく遅れる**）。
// **盲目にしてよいのは「壊れても遅れて気づけば済むもの」までで、それ以上は広げない。**
const isContentBlind = (p) => p.startsWith("experiments/");

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

/** Every .py under `dir` (recursively), by path only — never by content. */
function listingLines(cwd, dir) {
  const out = [];
  const walk = (rel) => {
    let names;
    try {
      names = readdirSync(join(cwd, rel), { withFileTypes: true });
    } catch {
      return; // missing tree: nothing to list (an empty listing is a valid state)
    }
    for (const entry of [...names].sort((a, b) => a.name.localeCompare(b.name))) {
      if (entry.name === "__pycache__" || entry.name === ".git") continue;
      const child = `${rel}${entry.name}`;
      if (entry.isDirectory()) walk(`${child}/`);
      else if (child.endsWith(".py")) out.push(`listing\t${child}`);
    }
  };
  walk(dir.endsWith("/") ? dir : `${dir}/`);
  return out;
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
    // Content-blind trees are represented once, by their file listing (below).
    // Skipping the status entry too is deliberate: a dirty marker is itself a
    // change of state, so leaving it in would move the key on the first edit.
    if (isContentBlind(path)) continue;
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
  // Content-blind trees: what still matters is which files exist, so the listing
  // is the whole input. Editing anything under experiments/ leaves it unchanged;
  // adding or deleting a file moves it (documented .py references must resolve).
  parts.push(...listingLines(cwd, "experiments"));

  return createHash("sha256").update(parts.join("\n")).digest("hex");
}

function readCache(cwd) {
  try {
    return JSON.parse(readFileSync(join(cwd, CACHE_PATH), "utf-8")) || {};
  } catch {
    return {};
  }
}

function writeCache(cwd, data) {
  try {
    writeFileSync(join(cwd, CACHE_PATH), JSON.stringify(data) + "\n", "utf-8");
  } catch {
    /* the cache is an optimisation; failing to write it only costs a rerun */
  }
}

/** Note that a pytest run is starting (so a run that never returns is visible).
 *
 * 🔴 WHY (2026-08-23): the hook's pytest step was killed at the 120s hook timeout
 * on every single turn for 15 days. Nothing noticed, because **the thing that
 * dies cannot file the report**: no pass was recorded, so the cache never warmed,
 * so the suite ran again next turn — and the gate verified nothing the whole
 * time while costing two minutes a turn. A start marker turns that silence into
 * evidence: if the next invocation still sees `startedAt`, the previous run did
 * not come back. */
export function markStart(cwd, key) {
  writeCache(cwd, { ...readCache(cwd), startedAt: new Date().toISOString(), startedKey: key });
}

/** Note that a run ENDED (pass or fail) — always clears the start marker. */
export function recordFinish(cwd, key, ok, durationMs) {
  const data = { ...readCache(cwd) };
  delete data.startedAt;
  delete data.startedKey;
  data.finishedAt = new Date().toISOString();
  data.durationMs = durationMs;
  if (ok && key) {
    data.passedKey = key;
    data.at = data.finishedAt;
  }
  writeCache(cwd, data);
}

/** Did the previous pytest run fail to come back (killed / crashed)? */
export function lastRunWasCut(cwd) {
  return Boolean(readCache(cwd).startedAt);
}

/** How long the last completed run took, in ms (0 when unknown). */
export function lastDurationMs(cwd) {
  const ms = readCache(cwd).durationMs;
  return typeof ms === "number" ? ms : 0;
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
export function recordPass(cwd, key, durationMs = 0) {
  if (!key) return;
  recordFinish(cwd, key, true, durationMs);
}
