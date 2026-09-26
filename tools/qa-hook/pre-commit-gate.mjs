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
//
// FULL ONLY WHERE IT IS NEEDED (2026-09-23): "fits one island or else full"
// sent test-only and .gitignore commits to the 8-minute suite. Now a commit is
// full only if it touches a `full_prefixes` face (product code, shared test
// inputs, deps/pytest config) or a path no rule knows; otherwise it runs the
// union of what each path needs (see islandFor and gate-scope.json).
//
// NO SECOND FULL RUN (2026-09-26・I-184/I-185): the cache key is the content
// tree, so the push right after a green commit hits the cache instead of
// re-running the same suite; and a version-line-only step from the last real
// full pass is proven by the version string's readers (versionChain) — on the
// push as well, or the saving would only move from the commit to the push.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { pythonEnv, resolvePython } from "./gate.mjs";
import { git } from "./git-changes.mjs";
import {
  cacheInputs, keyFor, isCachedPass, recordPass, recordFinish, markStart,
  recordFullPass, lastFullPass, sameExtras,
} from "./pytest-cache.mjs";

const FULL_SCOPE = "full-suite";
const MAX_OUT = 3000;

// LEDGER FIRST (2026-09-26・I-179 の回): the real-data checks of the ledger
// (ISSUES.md) and the roadmap/memory used to fail only at the END of the
// 10-minute suite — twice in one commit ("済 without a real hash", then "✅
// stage line pointing at an open issue"), each answered by bending the ledger
// back. They take ~2 s, so run them first and say the rule that fixes them:
// an issue and its stage line are closed AFTER the commit, with its hash.
// Selected by the name prefix (a class, not a hand-kept list): every real-data
// test in test_claude_hooks.py is `test_real_*`. The ledger is git-ignored, so
// it is not in the cache key below — this runs on every commit/push.
export const LEDGER_PREFLIGHT = ["tests/test_claude_hooks.py", "-k", "test_real_", "-q"];
// A denied command never ran AT ALL — a `git add` in front of the commit
// included (2026-09-26: the retry then committed 1 file of 14).
export const NOTHING_RAN =
  "⚠️ このコマンドは丸ごと走っていません＝同じコマンドの `git add` なども未実行です。" +
  "やり直す前に `git status` で何がステージされているかを確かめてください。";
export const LEDGER_ORDER =
  "⛔ 台帳（ISSUES.md）・版計画・メモリの実データの検査が赤です（全テストの前に先に走らせています）。\n" +
  "🔑 課題を「済」にしてアーカイブへ移すことと、版計画のステージ行を ✅ にすることは、" +
  "**コミットの後**（ハッシュが決まってから）に行います。コミットまでは課題を「対応中」、" +
  "ステージ行を ⬜ のままにしてください。仮の字（`COMMIT` など）をハッシュの代わりに書かない。";
// B-302: git's global options may sit between `git` and the subcommand
// (`git -C <path> commit`, `git -c k=v push`, `git --no-pager commit`). The
// old patterns wanted the subcommand right after `git`, so the `-C` form — the
// one no_shell_detours.py steers towards by forbidding `cd` — skipped the gate
// for both commit AND push. Keep in step with `_GIT_OPTS` in that hook.
const OPT_VALUE = String.raw`(?:"[^"]*"|'[^']*'|[^\s;&|]+)`;
const GIT_OPTS =
  String.raw`(?:\s+(?:-[Cc]\s+${OPT_VALUE}` +
  String.raw`|--(?:git-dir|work-tree|namespace|config-env)\s+${OPT_VALUE}` +
  String.raw`|--?[A-Za-z][\w-]*(?:=${OPT_VALUE})?))*`;
const gitSubcommand = (sub) =>
  new RegExp(String.raw`(?:^|[;&|\n]|\)\s*)\s*git(?:\.exe)?${GIT_OPTS}\s+(?:${sub})(?=$|[\s;&|)])`);
const PUSH = gitSubcommand("push");

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

const TEST_FILE = /^tests\/test_[^/]+\.py$/;

/** Tests whose source names `needle` (a scoped face's prefix, e.g. ".gitignore"). */
function readersOf(cwd, needle) {
  const found = [];
  let names = [];
  try {
    names = readdirSync(join(cwd, "tests"));
  } catch {
    return found;
  }
  for (const n of names) {
    if (!(n.startsWith("test_") && n.endsWith(".py"))) continue;
    try {
      if (readFileSync(join(cwd, "tests", n), "utf-8").includes(needle)) found.push(`tests/${n}`);
    } catch {
      /* unreadable: skip */
    }
  }
  return found;
}

// VERSION LINE (I-185・2026-09-26): `core/version.py` is product code (full),
// but the version string moves ~5 times per release (a1, b1, RC1..n, final)
// and each bump paid 8-10 min. When the ONLY change to that file is the one
// `APP_VERSION = "…"` line (matched literally — anything else in the diff, a
// mode change, a second line, is full: fail-closed), the tests that can see
// the version string stand in for it (versionReaders).
export const VERSION_FILE = "core/version.py";
const VERSION_LINE = /^APP_VERSION = "[0-9A-Za-z.]+"$/;
const PRODUCT_DIRS = ["core", "views", "report", "apps"];

/** Do two texts of core/version.py differ in exactly one line, and is that
 *  line `APP_VERSION = "…"` on both sides? (Line count must match — an added
 *  or removed line is not a version bump. CRLF/LF is not a difference, the
 *  same blind spot as the cache key's.) Anything missing → false. */
export function versionLineOnly(oldText, newText) {
  if (typeof oldText !== "string" || typeof newText !== "string") return false;
  const a = oldText.replace(/\r\n/g, "\n").split("\n");
  const b = newText.replace(/\r\n/g, "\n").split("\n");
  if (a.length !== b.length) return false;
  const changed = a.map((line, i) => [line, b[i]]).filter(([x, y]) => x !== y);
  return changed.length === 1 && VERSION_LINE.test(changed[0][0]) && VERSION_LINE.test(changed[0][1]);
}

function readVersionFile(cwd) {
  try {
    return readFileSync(join(cwd, VERSION_FILE), "utf-8");
  } catch {
    return null;
  }
}

/** Working tree vs HEAD: does core/version.py differ only in its version line? */
function versionLineOnlyVsHead(cwd) {
  let head;
  try {
    head = git(cwd, ["show", `HEAD:${VERSION_FILE}`]);
  } catch {
    return false;
  }
  return versionLineOnly(head, readVersionFile(cwd));
}

function productSources(cwd) {
  const out = [];
  const walk = (rel) => {
    let entries = [];
    try {
      entries = readdirSync(join(cwd, rel), { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (e.name === "__pycache__") continue;
      const child = `${rel}/${e.name}`;
      if (e.isDirectory()) walk(child);
      else if (e.name.endsWith(".py")) out.push(child);
    }
  };
  PRODUCT_DIRS.forEach(walk);
  return out;
}

/** The tests that can see the version string, by a mechanical rule (no hand
 *  list — the watcher is test_pre_commit_gate.py::TestVersionReaders):
 *   1. tests naming core.version, APP_VERSION, a constant of core/version.py
 *      whose value carries it (APP_FULL, USER_AGENT…), or a function of it
 *      that defaults to it (is_final, version_tuple…);
 *   2. tests naming a product module whose BEHAVIOUR follows the version's
 *      stage (a/b/RC/final): it calls one of those functions, or a `def` of
 *      it defaults a parameter to the version (update_check).
 *  Modules that only print the string (report headers, the window title, the
 *  User-Agent) are caught by 1 when a test compares against the name, and by
 *  the watcher's literal scan when a test hard-codes the text.
 *  null when core/version.py cannot be read (→ full). */
export function versionReaders(cwd) {
  let src;
  try {
    src = readFileSync(join(cwd, VERSION_FILE), "utf-8");
  } catch {
    return null;
  }
  const names = new Set(["APP_VERSION"]);
  for (const m of src.matchAll(/^([A-Za-z_]\w*)\s*=([^\n]*)$/gm)) {
    if (m[2].includes("APP_VERSION")) names.add(m[1]);
  }
  const funcs = [];
  for (const m of src.matchAll(/^def\s+(\w+)\s*\(((?:[^()]|\([^()]*\))*)\)/gm)) {
    if (m[2].includes("APP_VERSION")) funcs.push(m[1]);
  }
  funcs.forEach((f) => names.add(f));
  const needles = new Set(["core.version", "core import version", ...names]);
  const calls = new RegExp(String.raw`\bversion\.(?:${funcs.join("|") || "(?!)"})\s*\(`);
  const defaults = /\bdef\s+\w+\s*\(((?:[^()]|\([^()]*\))*)\)/g;
  for (const rel of productSources(cwd)) {
    let body;
    try {
      body = readFileSync(join(cwd, rel), "utf-8");
    } catch {
      continue;
    }
    const followsStage = calls.test(body) ||
      [...body.matchAll(defaults)].some((m) => /=\s*(?:version\.)?APP_VERSION\b/.test(m[1]));
    if (followsStage) needles.add(rel.split("/").pop().replace(/\.py$/, ""));
  }
  const found = new Set();
  for (const needle of needles) readersOf(cwd, needle).forEach((t) => found.add(t));
  return [...found].sort();
}

/** The tests a commit of `paths` needs, as a synthetic island {name, tests},
 *  or null (→ full suite). Full when: nothing changed, any path is on a
 *  `full_prefixes` face, or any path matches no rule at all (fail-closed).
 *  Otherwise the UNION of: every island a path falls in, a changed
 *  `tests/test_*.py` itself (+ `tests_walkers`), and the readers of a
 *  `scoped_faces` path — plus `scanners` whenever anything was scoped.
 *  core/version.py counts as scoped (its readers) only while its sole change
 *  is the version line (`versionOnly`; computed from the working tree when
 *  not given). */
export function islandFor(scope, paths, cwd = process.cwd(), { versionOnly } = {}) {
  if (!paths.length) return null;
  const full = scope.full_prefixes || [];
  const names = new Set();
  const tests = new Set();
  for (const p of paths) {
    if (p === VERSION_FILE) {
      const readers = (versionOnly ?? versionLineOnlyVsHead(cwd)) ? versionReaders(cwd) : null;
      if (!readers) return null;
      names.add("version");
      readers.forEach((t) => tests.add(t));
      continue;
    }
    if (full.some((pre) => p.startsWith(pre))) return null;
    let hit = false;
    for (const island of scope.commit_islands || []) {
      if (island.prefixes.some((pre) => p.startsWith(pre))) {
        names.add(island.name);
        island.tests.forEach((t) => tests.add(t));
        hit = true;
      }
    }
    if (hit) continue;
    if (TEST_FILE.test(p)) {
      names.add("tests");
      tests.add(p);
      (scope.tests_walkers || []).forEach((t) => tests.add(t));
      continue;
    }
    const face = (scope.scoped_faces || []).find((pre) => p.startsWith(pre));
    if (face === undefined) return null;
    names.add(face);
    readersOf(cwd, face.replace(/\/$/, "")).forEach((t) => tests.add(t));
  }
  (scope.scanners || []).forEach((t) => tests.add(t));
  return { name: [...names].join("+"), tests: [...tests] };
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
  // `file.py::test_name` picks single tests out of a slow file (tests_walkers).
  return [...targets].filter((t) => existsSync(join(cwd, t.split("::")[0]))).sort();
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
// these two subcommands. ⚠️ A false negative is NOT harmless: if the push is
// written the same way it is missed too, and nothing else runs the full suite
// before `main` leaves the machine (B-302).
const COMMIT_OR_PUSH = gitSubcommand("commit|push");

/** Whether `command` runs `git commit` or `git push` (exported for tests). */
export function isCommitOrPush(command) {
  return COMMIT_OR_PUSH.test(command);
}

/** Whether `command` runs `git push` (→ always the full suite). */
export function isPush(command) {
  return PUSH.test(command);
}

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

// I-166: stdin が空／JSON として読めないのは、Claude Code が PreToolUse
// フックとして呼んだときには起こらない（常に tool_input の JSON が渡る）＝
// 起きるのは人がこのスクリプトを素で叩いたときだけ。以前はそれも他の
// 早期 return と同じ「無言で exit 0」に落ちており、「素叩き」「変更なし」
// 「合格」の 3 状態が手元からは区別できなかった（I-166 の実測）。ここだけは
// 非ゼロで断る＝フックとしての正常動作は変わらない（この分岐に来ないため）。
function requireStdinInput() {
  const raw = readStdin();
  if (!raw.trim()) {
    process.stderr.write(
      "[pre-commit-gate] stdin が空です＝素で叩いたか配線がずれています。" +
      "何も検査していません（PreToolUse フックの呼び出しでは常に tool_input の " +
      "JSON が渡ります）。\n");
    process.exit(2);
  }
  try {
    return JSON.parse(raw);
  } catch {
    process.stderr.write(
      "[pre-commit-gate] stdin を JSON として読めません＝素で叩いたか配線が" +
      "ずれています。何も検査していません。\n");
    process.exit(2);
  }
}

function main() {
  const input = requireStdinInput();
  if (!["Bash", "PowerShell"].includes(input.tool_name)) return;
  const command = (input.tool_input || {}).command || "";
  if (!isCommitOrPush(command)) return;

  const cwd = input.cwd || process.cwd();
  if (!existsSync(join(cwd, "tests"))) return; // not this repo's working tree

  const resolved = resolvePython();
  if (resolved.error) {
    deny(`コミット前提のフル QA ゲートが走れません。\n\n${resolved.error}`);
    return;
  }

  const pre = runPytest(resolved.python, cwd, LEDGER_PREFLIGHT);
  if (pre.code !== 0) {
    deny(`${LEDGER_ORDER}\n${NOTHING_RAN}\n\n\`\`\`\n${tail(pre.stdout + pre.stderr, MAX_OUT)}\n\`\`\``);
    return;
  }

  // A full pass also covers any island run on the same content — check it first.
  // The key is the content tree (I-184), so a commit does not move it: the push
  // right after a green commit hits here.
  const lastFull = lastFullPass(cwd);
  const inputs = cacheInputs(cwd, { without: VERSION_FILE });
  const versionText = readVersionFile(cwd);
  const fullKey = keyFor(inputs, FULL_SCOPE);
  if (isCachedPass(cwd, fullKey)) return; // already proven green for this exact content

  let key = fullKey;
  let targets = [];
  let label = "フルスイート";
  let realFull = true;
  const scope = loadScope(cwd);
  if (versionChain(inputs, lastFull, versionText)) {
    // I-185: the last real full pass + a version-line-only step + its readers
    // green = the full proof for this tree, for commit AND push (a commit-only
    // shortcut would just move the 8 minutes to the push). CI stays the backstop.
    const island = islandFor(scope, [VERSION_FILE], cwd, { versionOnly: true });
    if (island) {
      targets = islandTargets(cwd, scope, island);
      label = `版の字の読み手のテスト（${targets.length} 本・直前のフル合格から版の字の 1 行だけ）`;
      realFull = false;
    }
  } else if (!isPush(command)) {
    const island = islandFor(scope, commitPaths(cwd), cwd);
    if (island) {
      targets = islandTargets(cwd, scope, island);
      key = keyFor(inputs, `island:${island.name}\n${targets.join("\n")}`);
      label = `島「${island.name}」のテスト（${targets.length} 本）`;
      realFull = false;
      if (isCachedPass(cwd, key)) return;
    }
  }

  markStart(cwd, key);
  const started = Date.now();
  const r = runPytest(resolved.python, cwd, targets);
  const ms = Date.now() - started;

  if (r.code !== 0) {
    recordFinish(cwd, key, false, ms);
    deny(
      `⛔ コミット前提の${label}が赤です（I-154＝毎ターンは影響範囲だけ・` +
      "フルはコミット直前に 1 回／島に収まるコミットは島のテストだけ）。" +
      `直してから commit/push をやり直してください。\n${NOTHING_RAN}\n\n` +
      `\`\`\`\n${tail(r.stdout + r.stderr, MAX_OUT)}\n\`\`\``);
    return;
  }
  recordPass(cwd, key, ms);
  if (realFull) recordFullPass(cwd, inputs, { versionText });
}

/** I-185: does the working tree differ from the last REAL full pass only by
 *  the version line? = the same tree once core/version.py is set aside, the
 *  same untracked sources, and that file's text off by exactly that line. */
export function versionChain(inputs, lastFull, versionText) {
  if (!inputs || !lastFull || !inputs.rest || inputs.rest !== lastFull.rest) return false;
  if (!sameExtras(inputs, lastFull)) return false;
  return versionLineOnly(lastFull.versionText, versionText);
}

function runPytest(python, cwd, args) {
  try {
    const stdout = execFileSync(python, ["-m", "pytest", ...args], {
      cwd,
      env: pythonEnv(),   // 日本語の文字化けを防ぐ（I-172）
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "pipe"],
      maxBuffer: 10 * 1024 * 1024,
    });
    return { code: 0, stdout, stderr: "" };
  } catch (err) {
    return {
      code: typeof err.status === "number" ? err.status : 1,
      stdout: err.stdout || "",
      stderr: err.stderr || "",
    };
  }
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
