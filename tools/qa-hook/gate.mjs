#!/usr/bin/env node
// RadioSim QA Stop hook — deterministic gate only.
//
// Fires when Claude finishes a turn. If any *.py changed in the working tree,
// run the DETERMINISTIC GATE (authoritative): pyright + ruff + bandit on the
// changed lines, and pytest (whole suite). Failure -> block with the errors.
// pytest is skipped when it already passed for this exact working-tree content
// (see pytest-cache.mjs — I-056: same suite, once per turn, for no new input).
//
// The local-LLM (Qwen) second opinion used to run here as stage 2, then moved
// to pre-push / on-demand. RETIRED 2026-07-26: it returned summaries rather
// than findings and scored 0 on the labelled benchmark, while Codex scored
// 11/11 (experiments/review_benchmark/). Independent review is Codex's job
// now; this gate stays purely deterministic.
//
// Loop safety: once stop_hook_active is set we never block (only inform).
// Never crashes the turn: any unexpected error -> exit 0.
// Config (env): RADIOSIM_PYTHON = the project venv's python.exe. REQUIRED.
// There is deliberately no fallback (see resolvePython): falling back to the
// PATH python is how B-020 happened — the gate would go green against a shared
// interpreter that is not the one the app ships with, and say nothing.

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { readFileSync } from "node:fs";
import { changedPyEntries, isDeleted, changedLinesForFile } from "./git-changes.mjs";
import {
  pytestCacheKey, isCachedPass, recordPass, recordFinish,
  markStart, lastRunWasCut, lastDurationMs,
} from "./pytest-cache.mjs";

const MAX_OUT = 2500; // cap pytest output fed back to Claude
const MAX_ITEMS = 25; // cap linter findings fed back to Claude
// Warn while there is still headroom under the hook timeout.
// 🔴 The gate's pytest step was killed at a 120s timeout on every turn for 15
// days (2026-08-23) — silently, because a killed process files no report.
// ⚠️ The threshold is derived from the CONFIGURED timeout, never hard-coded: a
// constant here would stop matching the moment someone edits settings, which is
// how the outage started (the suite grew past a ceiling nobody was watching).
const WARN_FRACTION = 0.7;
const FALLBACK_TIMEOUT_MS = 420 * 1000;

/** The Stop-hook timeout configured for this gate, in ms. */
export function gateTimeoutMs(cwd) {
  for (const name of ["settings.local.json", "settings.json"]) {
    let cfg;
    try {
      cfg = JSON.parse(readFileSync(join(cwd, ".claude", name), "utf-8"));
    } catch {
      continue;
    }
    for (const group of cfg?.hooks?.Stop || []) {
      for (const h of group.hooks || []) {
        if (String(h.command || "").includes("gate.mjs") && Number(h.timeout) > 0) {
          return Number(h.timeout) * 1000;
        }
      }
    }
  }
  return FALLBACK_TIMEOUT_MS;
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

// The project venv lives outside the repo (outside OneDrive) since 2.6a1, so it
// cannot be discovered by looking next to the sources. It must be declared.
// Returns { python } or { error } — never a guess.
function resolvePython() {
  const p = process.env.RADIOSIM_PYTHON;
  if (!p) {
    return {
      error:
        "RADIOSIM_PYTHON is not set, so the QA gate cannot tell which interpreter " +
        "to verify against. Set it to the project venv's python.exe, e.g.\n" +
        "  setx RADIOSIM_PYTHON D:\\dev\\radiosim\\venv\\Scripts\\python.exe",
    };
  }
  if (!existsSync(p)) {
    return { error: `RADIOSIM_PYTHON points at a file that does not exist:\n  ${p}` };
  }
  return { python: p };
}

// Run a command, capturing stdout/stderr and exit code even on failure.
function runCmd(cwd, file, args) {
  try {
    const stdout = execFileSync(file, args, {
      cwd,
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "pipe"],
      maxBuffer: 10 * 1024 * 1024,
    });
    return { code: 0, stdout, stderr: "" };
  } catch (err) {
    if (err.code === "ENOENT") return { code: -1, stdout: "", stderr: "", missing: true };
    return {
      code: typeof err.status === "number" ? err.status : 1,
      stdout: err.stdout || "",
      stderr: err.stderr || "",
    };
  }
}

function tail(s, n) {
  s = (s || "").trim();
  return s.length > n ? "…\n" + s.slice(-n) : s;
}

const norm = (p) => String(p).replace(/\\/g, "/");

// Match a tool-reported path (often absolute) to one of our relative keys.
function matchKey(reported, keys) {
  const r = norm(reported);
  return keys.find((k) => r === k || r.endsWith("/" + k) || r.endsWith(k));
}

function parseJson(s) {
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

// --- diff-scoped linters: only findings on changed lines gate -----------------

// pyright (type errors), diff-scoped to changed lines.
// experiments/ is excluded on purpose: those one-off probes are declared outside
// the type check (experiments/README.md) because they poke at Tk/Win32 internals
// a typed API cannot describe. Gating on them blocks turns over throwaway code —
// ruff (syntax/pyflakes) still covers them, which is what actually protects the
// next person who runs a probe.
function pyrightItems(cwd, py, files, keys, changedLines) {
  const typed = files.filter((f) => !norm(f).startsWith("experiments/"));
  if (!typed.length) return [];
  const r = runCmd(cwd, py, ["-m", "pyright", "--outputjson", ...typed]);
  if (r.missing) return [];
  const data = parseJson(r.stdout);
  if (!data) return [];
  const items = [];
  for (const d of data.generalDiagnostics || []) {
    if (d.severity !== "error") continue;
    const key = matchKey(d.file || "", keys);
    if (!key) continue;
    const line = d.range && d.range.start ? d.range.start.line + 1 : 0;
    if (!changedLines[key] || !changedLines[key].has(line)) continue;
    items.push(`${key}:${line} [pyright${d.rule ? " " + d.rule : ""}] ${d.message.split("\n")[0]}`);
  }
  return items;
}

// ruff (syntax + pyflakes only: E9,F), diff-scoped.
function ruffItems(cwd, py, files, keys, changedLines) {
  const r = runCmd(cwd, py, ["-m", "ruff", "check", "--select", "E9,F", "--output-format", "json", ...files]);
  if (r.missing) return [];
  const data = parseJson(r.stdout);
  if (!Array.isArray(data)) return [];
  const items = [];
  for (const d of data) {
    const key = matchKey(d.filename || "", keys);
    const line = d.location && d.location.row;
    if (!key || !line) continue;
    if (!changedLines[key] || !changedLines[key].has(line)) continue;
    items.push(`${key}:${line} [ruff ${d.code}] ${d.message}`);
  }
  return items;
}

// bandit (security, MEDIUM+ severity, app files only), diff-scoped.
function banditItems(cwd, py, files, keys, changedLines) {
  const appFiles = files.filter((f) => !norm(f).startsWith("tests/"));
  if (!appFiles.length) return [];
  const r = runCmd(cwd, py, ["-m", "bandit", "-f", "json", "-q", ...appFiles]);
  if (r.missing) return [];
  const data = parseJson(r.stdout);
  if (!data) return [];
  const items = [];
  for (const d of data.results || []) {
    if (!["MEDIUM", "HIGH"].includes((d.issue_severity || "").toUpperCase())) continue;
    const key = matchKey(d.filename || "", keys);
    const line = d.line_number;
    if (!key || !line) continue;
    if (!changedLines[key] || !changedLines[key].has(line)) continue;
    items.push(`${key}:${line} [bandit ${d.test_id} ${d.issue_severity}] ${d.issue_text}`);
  }
  return items;
}

function lintSection(title, items) {
  if (!items.length) return null;
  const shown = items.slice(0, MAX_ITEMS);
  const more = items.length > MAX_ITEMS ? `\n…(+${items.length - MAX_ITEMS} more)` : "";
  return `### ${title}\n${shown.map((i) => "- " + i).join("\n")}${more}`;
}

// Returns { ok, report } for the deterministic gate.
function runDeterministic(cwd, py, entries) {
  const failures = [];
  const notes = [];
  const live = entries.filter((e) => !isDeleted(e));
  const files = live.map((e) => e.path);

  if (files.length) {
    // Map each changed file to its changed line numbers (diff-scope).
    const changedLines = {};
    for (const e of live) {
      try {
        changedLines[e.path] = changedLinesForFile(cwd, e);
      } catch {
        changedLines[e.path] = new Set();
      }
    }

    const lint = [
      lintSection("pyright (types)", pyrightItems(cwd, py, files, files, changedLines)),
      lintSection("ruff (E9,F)", ruffItems(cwd, py, files, files, changedLines)),
      lintSection("bandit (MEDIUM+)", banditItems(cwd, py, files, files, changedLines)),
    ].filter(Boolean);
    if (lint.length) failures.push(lint.join("\n\n"));
  }

  // pytest (whole suite; pyproject sets testpaths=tests) — pass/fail wholesale.
  // Skipped when the suite already passed for this exact working-tree content
  // (I-056: holding an uncommitted .py re-ran the identical suite every turn).
  if (existsSync(join(cwd, "tests"))) {
    const key = pytestCacheKey(cwd);
    if (!isCachedPass(cwd, key)) {
      // A run that never came back is only visible on the NEXT invocation: the
      // process that gets killed cannot report anything (see markStart).
      if (lastRunWasCut(cwd)) {
        notes.push(
          "⛔ 前回の pytest が**帰ってきていません**（フックの timeout で殺された可能性）。" +
          "その間ゲートは何も検査しておらず、合格も記録できないので**毎ターン最初から走り直します**。" +
          `フックの timeout（.claude/settings.local.json の gate.mjs）と、実測 ${
            Math.round(lastDurationMs(cwd) / 1000)}秒 の突き合わせが要ります。`);
      }
      markStart(cwd, key);
      // `-x`: a failing turn should come back fast. A green run costs the same
      // either way, and the cache only ever records a green run, so stopping at
      // the first failure changes how long a red turn takes, not what passes.
      const started = Date.now();
      const r = runCmd(cwd, py, ["-m", "pytest", "-x"]);
      const ms = Date.now() - started;
      if (!r.missing && r.code !== 0) {
        recordFinish(cwd, key, false, ms);
        failures.push(`### pytest\n\`\`\`\n${tail(r.stdout + r.stderr, MAX_OUT)}\n\`\`\``);
      } else if (!r.missing) {
        recordPass(cwd, key, ms);
      } else {
        recordFinish(cwd, key, false, ms);   // pytest 自体が無い＝走っていない
      }
      // The precursor of the 15-day outage: the suite creeping up on the ceiling.
      // Warn while there is still room, not after the gate has gone silent.
      const limit = gateTimeoutMs(cwd);
      if (ms > limit * WARN_FRACTION) {
        notes.push(
          `⚠️ スイートが ${Math.round(ms / 1000)} 秒かかりました（フックの timeout は ${
            Math.round(limit / 1000)} 秒＝残り ${Math.round((limit - ms) / 1000)} 秒）。` +
          "**超えた瞬間からゲートは黙って死にます**（2026-08-23 に 15 日間そうなっていた）。" +
          "timeout を上げるか、スイートを速くしてください。");
      }
    }
  }

  return { ok: failures.length === 0, report: failures.join("\n\n"), notes };
}

async function main() {
  let input = {};
  try {
    input = JSON.parse(readStdin() || "{}");
  } catch {
    /* ignore */
  }
  const cwd = input.cwd || process.cwd();
  const stopActive = Boolean(input.stop_hook_active);

  let entries;
  try {
    entries = changedPyEntries(cwd);
  } catch {
    process.exit(0); // not a git repo
  }
  if (entries.length === 0) process.exit(0);

  // Deterministic gate (authoritative). LLM advisory runs at pre-push / on-demand.
  // An unusable interpreter is a gate FAILURE, not a reason to skip: "no gate
  // ran" must never be reported as "gate is green" (B-020's failure mode).
  const resolved = resolvePython();
  if (resolved.error) {
    const reason = `Deterministic QA gate could not run.\n\n${resolved.error}`;
    if (stopActive) emit({ systemMessage: `[QA gate] cannot run\n\n${resolved.error}` });
    emit({ decision: "block", reason });
  }
  const det = runDeterministic(cwd, resolved.python, entries);
  const notes = (det.notes || []).join("\n");
  if (!det.ok) {
    const reason =
      "Deterministic QA gate FAILED on the changed Python. Fix these before " +
      `finishing:\n\n${det.report}${notes ? `\n\n${notes}` : ""}`;
    if (stopActive) emit({ systemMessage: `[QA gate] failures remain\n\n${det.report}` });
    emit({ decision: "block", reason });
  }
  // ⚠️ **合格しても黙らない**＝ゲート自身の不調（前回の run が帰ってこない／
  // スイートが上限に近づいている）は、合格の裏でこそ起きる。2026-08-23 の 15 日間の
  // 空白は「誰も何も言わなかった」ことで続いた。
  if (notes) emit({ systemMessage: `[QA gate] ${notes}` });

  process.exit(0);
}

// ⚠️ **import しただけでゲートを走らせない**（2026-08-23 に踏んだ＝ヘルパーを
// 1 つ確かめるつもりで import したら全スイートが走り出した）。フックは
// `node gate.mjs` と直接起動するので、そのときだけ実行する。テストや診断は
// `gateTimeoutMs` のような関数を副作用なしで呼べる。
const invokedDirectly =
  process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop());

if (invokedDirectly) {
  main().catch((err) => {
    process.stderr.write(`qa-gate error: ${err && err.message}\n`);
    process.exit(0);
  });
}
