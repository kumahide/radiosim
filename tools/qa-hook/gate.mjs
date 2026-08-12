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
import { pytestCacheKey, isCachedPass, recordPass } from "./pytest-cache.mjs";

const MAX_OUT = 2500; // cap pytest output fed back to Claude
const MAX_ITEMS = 25; // cap linter findings fed back to Claude

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
function pyrightItems(cwd, py, files, keys, changedLines) {
  const r = runCmd(cwd, py, ["-m", "pyright", "--outputjson", ...files]);
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
      const r = runCmd(cwd, py, ["-m", "pytest"]);
      if (!r.missing && r.code !== 0) {
        failures.push(`### pytest\n\`\`\`\n${tail(r.stdout + r.stderr, MAX_OUT)}\n\`\`\``);
      } else if (!r.missing) {
        recordPass(cwd, key);
      }
    }
  }

  return { ok: failures.length === 0, report: failures.join("\n\n") };
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
  if (!det.ok) {
    const reason =
      "Deterministic QA gate FAILED on the changed Python. Fix these before " +
      `finishing:\n\n${det.report}`;
    if (stopActive) emit({ systemMessage: `[QA gate] failures remain\n\n${det.report}` });
    emit({ decision: "block", reason });
  }

  process.exit(0);
}

main().catch((err) => {
  process.stderr.write(`qa-gate error: ${err && err.message}\n`);
  process.exit(0);
});
