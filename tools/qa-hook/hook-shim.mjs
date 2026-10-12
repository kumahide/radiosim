#!/usr/bin/env node
// RadioSim hook shim — a gate that cannot run must close, not open (B-341).
//
// Usage (in .claude/settings.local.json):  node hook-shim.mjs <program> <script> [args...]
//
// Claude Code only treats exit 2 as "block". A gate script that dies while
// starting — a syntax error (a merge-conflict marker left in
// pre-commit-gate.mjs let two merges in without the gate, 2026-09-27), a failed
// import, a missing interpreter — exits 1 or so, which Claude Code treats as a
// non-fatal hook error: the commit (or the stop) goes through as if the gate
// had said yes. This shim runs the gate and maps "ended with anything other
// than 0 or 2" to exit 2 with "the gate is broken".
//
// Everything else passes through untouched: stdin goes to the child as is, and
// on exit 0 / 2 the child's stdout and stderr are copied byte for byte (gates
// answer with JSON on stdout + exit 0, or with stderr + exit 2).
//
// Loop safety (same rule as gate.mjs): on a Stop hook that is already
// re-running (stop_hook_active), a broken gate is reported to the user as a
// systemMessage instead of blocking again — Claude was told once, and blocking
// every stop would loop.
//
// ⚠️ Keep this file small. If the shim itself does not parse, it reopens the
// same hole; tests/test_hook_shim.py runs `node --check` on it in CI.

import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";

const TAIL = 1500; // chars of the child's stderr quoted in the report

function readStdin() {
  try {
    return readFileSync(0);
  } catch {
    return Buffer.alloc(0);
  }
}

function stopHookActive(input) {
  try {
    const j = JSON.parse(input.toString("utf-8"));
    return j?.hook_event_name === "Stop" && j?.stop_hook_active === true;
  } catch {
    return false;
  }
}

const [program, ...args] = process.argv.slice(2);
const input = readStdin();
if (!program) {
  process.stderr.write("⛔ [hook-shim] 中身の門が指定されていません（配線の誤り・B-341）。\n");
  process.exit(2);
}

const r = spawnSync(program, args, { input, maxBuffer: 64 * 1024 * 1024 });
if (r.status === 0 || r.status === 2) {
  process.stdout.write(r.stdout);
  process.stderr.write(r.stderr);
  process.exit(r.status);
}

const why = r.error
  ? `起動できない（${r.error.code || r.error.message}）`
  : r.status === null ? `シグナル ${r.signal} で止まった` : `exit ${r.status} で終わった`;
const tail = String(r.stderr || "").slice(-TAIL).trim();
const msg =
  `⛔ 門が壊れています: ${[program, ...args].join(" ")} が${why}` +
  "（読めない・起動で落ちた・中で例外）。門は開けずに閉じます（B-341）。" +
  "門のスクリプトを直してください。" +
  (tail ? `\n--- 門の stderr の末尾 ---\n${tail}\n` : "\n");

if (stopHookActive(input)) {
  process.stdout.write(JSON.stringify({ systemMessage: msg }) + "\n");
  process.exit(0);
}
process.stderr.write(msg);
process.exit(2);
