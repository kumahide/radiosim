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
//   1. the TREE the working tree would commit as (`git add -A` + `write-tree`
//      on a throwaway copy of the index — see workingTree()): every tracked and
//      untracked-but-not-ignored file, ANY extension (tests/test_docs_consistency
//      reads README/docs), by content — touching a file without changing it,
//      staging it, or committing it leaves the key where it is,
//   2. the size+mtime of the locally-tested but git-ignored trees (`.claude/`
//      Python hooks and `tools/qa-hook/*.mjs`), which the tree cannot see
//      yet tests/test_claude_hooks.py verifies — and which include this file.
//
// I-184 (2026-09-26): the key used to be HEAD + the dirty files. A commit moves
// HEAD and cleans the tree without changing one byte of content, so the push
// right after a commit re-ran the identical full suite (8-10 min for nothing).
// Dropping HEAD is only safe because nothing in the suite reads git STATE: the
// two readers of `git ls-files` (test_repo_hygiene / test_docs_consistency)
// were widened in the same change to "tracked + untracked, not ignored" — the
// same set the tree holds — so a new file is checked whether it is committed
// yet or not.
// ⚠️ Blind spot, unchanged from before: `core.autocrlf=true` makes `git add`
// normalise line endings, so a CRLF<->LF-only edit does not move the key. The
// old key went through `git status`, which is blind to the same edit.
//
// Anything unexpected (not a git repo, an oversized file, a corrupt cache)
// answers "run it": the cache may only ever remove a *redundant* run.

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { isAbsolute, join } from "node:path";
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

function gitWith(cwd, args, env) {
  return execFileSync("git", args, {
    cwd,
    env: { ...process.env, ...env },
    encoding: "utf-8",
    stdio: ["ignore", "pipe", "ignore"],
    maxBuffer: 64 * 1024 * 1024,
  });
}

/** The tree the working tree would commit as, or null (→ run the suite).
 *
 * Built on a COPY of the index with a throwaway object store that reads the
 * real one as an alternate: the real `.git/index` and `.git/objects` are never
 * written (no stray blobs, no staging side effects — pinned by a test). The
 * copied index keeps git's stat cache, so only changed files are re-hashed.
 * `experiments/` is dropped from the tree (content-blind — its listing goes
 * into the key instead, see cacheInputs()).
 *
 * `without` (a path) also returns `rest`: the same tree minus that one path —
 * so "everything else is identical" can be checked later by comparing two ids,
 * with no need for the old tree's objects (they lived in a store that is gone;
 * and with experiments/ dropped, the tree is never one git has on file). */
export function workingTree(cwd, { without } = {}) {
  let top, gitDir, objects;
  try {
    top = git(cwd, ["rev-parse", "--show-toplevel"]).trim();
    gitDir = git(top, ["rev-parse", "--absolute-git-dir"]).trim();
    objects = git(top, ["rev-parse", "--git-path", "objects"]).trim();
  } catch {
    return null; // not a git repo
  }
  if (!isAbsolute(objects)) objects = join(top, objects);

  // A huge untracked/modified file is not worth hashing every turn -> just run.
  let dirty;
  try {
    dirty = git(top, ["ls-files", "-z", "--modified", "--others", "--exclude-standard"]);
  } catch {
    return null;
  }
  for (const p of dirty.split("\0")) {
    if (!p || isContentBlind(p)) continue;
    let st;
    try {
      st = statSync(join(top, p));
    } catch {
      continue; // deleted: nothing to hash
    }
    if (st.isFile() && st.size > MAX_HASH_BYTES) return null;
  }

  let tmp;
  try {
    tmp = mkdtempSync(join(tmpdir(), "radiosim-qa-tree-"));
    const index = join(tmp, "index");
    if (existsSync(join(gitDir, "index"))) copyFileSync(join(gitDir, "index"), index);
    const store = join(tmp, "objects");
    mkdirSync(store);
    const env = {
      GIT_INDEX_FILE: index,
      GIT_OBJECT_DIRECTORY: store,
      GIT_ALTERNATE_OBJECT_DIRECTORIES: objects,
    };
    const run = (args) => gitWith(top, args, env);
    run(["add", "-A", "--", ".", ":(exclude)experiments"]);
    run(["rm", "-r", "--cached", "--ignore-unmatch", "-q", "--", "experiments"]);
    const tree = run(["write-tree"]).trim();
    let rest = null;
    if (without) {
      run(["rm", "--cached", "--ignore-unmatch", "-q", "--", without]);
      rest = run(["write-tree"]).trim();
    }
    return { tree, rest };
  } catch {
    return null;
  } finally {
    if (tmp) {
      try {
        rmSync(tmp, { recursive: true, force: true });
      } catch {
        /* a leftover temp dir costs disk, not correctness */
      }
    }
  }
}

/** Everything the key is made of: `{tree, extras, rest}`, or null (→ run).
 *  `extras` is what the tree cannot see (git-ignored but tested sources, the
 *  experiments/ listing); `rest` only with `opts.without` (see workingTree). */
export function cacheInputs(cwd, opts = {}) {
  const wt = workingTree(cwd, opts);
  if (!wt) return null;
  const extras = [];
  for (const dep of UNTRACKED_DEPS) extras.push(...statLines(cwd, dep));
  // Content-blind trees: what still matters is which files exist, so the listing
  // is the whole input. Editing anything under experiments/ leaves it unchanged;
  // adding or deleting a file moves it (documented .py references must resolve).
  extras.push(...listingLines(cwd, "experiments"));
  return { tree: wt.tree, extras: extras.join("\n"), rest: wt.rest };
}

/** The key for `inputs` (from cacheInputs) under `scopeSignature`. */
export function keyFor(inputs, scopeSignature = "") {
  if (!inputs) return null;
  return createHash("sha256")
    .update(`tree\t${inputs.tree}\n${inputs.extras}\nscope\t${scopeSignature}`)
    .digest("hex");
}

/** Cache key for the current working tree, or null if it cannot be computed
 *  (caller must then run pytest).
 *
 * `scopeSignature` distinguishes a scoped run (I-154: per-turn gate now runs
 * only the tests affected by the changed files) from the full suite — a
 * partial-run green is not the same fact as a full-run green, so they must
 * never share a cache slot. Pass the sorted, joined list of pytest targets;
 * the full-suite caller passes a fixed literal instead. Omitting it keeps the
 * pre-I-154 key shape (a constant scope line for every caller). */
export function pytestCacheKey(cwd, scopeSignature = "") {
  return keyFor(cacheInputs(cwd), scopeSignature);
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

// How many passes are remembered (I-184). One slot was not enough: a scoped
// Stop-hook pass between a commit and its push overwrote the commit's full
// pass, and the push paid the full suite again. Each key is a content hash +
// scope, so an older entry can only hit on exactly the content it passed on.
const MAX_PASSED_KEYS = 8;

/** Note that a run ENDED (pass or fail) — always clears the start marker. */
export function recordFinish(cwd, key, ok, durationMs) {
  const data = { ...readCache(cwd) };
  delete data.startedAt;
  delete data.startedKey;
  data.finishedAt = new Date().toISOString();
  data.durationMs = durationMs;
  if (ok && key) {
    const older = Array.isArray(data.passedKeys) ? data.passedKeys : [];
    data.passedKeys = [key, ...older.filter((k) => k !== key)].slice(0, MAX_PASSED_KEYS);
    data.passedKey = key; // the latest, for a human reading the file
    data.at = data.finishedAt;
  }
  writeCache(cwd, data);
}

/** Remember a REAL full-suite pass (not one proven by a chain) — the starting
 *  point I-185's version-line chain is measured from. `note` rides along
 *  (the caller's own facts, e.g. the version file's text). */
export function recordFullPass(cwd, inputs, note = {}) {
  if (!inputs) return;
  writeCache(cwd, {
    ...readCache(cwd),
    lastFull: {
      ...note,
      tree: inputs.tree,
      rest: inputs.rest,
      extras: createHash("sha256").update(inputs.extras).digest("hex"),
      at: new Date().toISOString(),
    },
  });
}

/** The last real full pass `{tree, rest, extras, …note}` (extras hashed), or null. */
export function lastFullPass(cwd) {
  const f = readCache(cwd).lastFull;
  return f && typeof f.tree === "string" && typeof f.extras === "string" ? f : null;
}

/** Do `inputs` see the same untracked sources as the recorded full pass? */
export function sameExtras(inputs, full) {
  return Boolean(inputs && full) &&
    createHash("sha256").update(inputs.extras).digest("hex") === full.extras;
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
    if (!data) return false;
    if (Array.isArray(data.passedKeys)) return data.passedKeys.includes(key);
    return data.passedKey === key; // a cache file written before I-184
  } catch {
    return false;
  }
}

/** Remember that pytest passed for this key (best effort). */
export function recordPass(cwd, key, durationMs = 0) {
  if (!key) return;
  recordFinish(cwd, key, true, durationMs);
}
