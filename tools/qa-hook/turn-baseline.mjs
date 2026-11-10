#!/usr/bin/env node
// RadioSim QA — 番の初めの指紋（turn baseline）。
//
// 何のため：作業ツリーは複数の会話で共有している。ある会話が書きかけで残した
// `*.py`（赤のまま）があると、Python に 1 字も触れていない別の会話まで、毎番
// Stop ゲート（gate.mjs）に「変更した Python で QA ゲートが赤」で止められ、
// スコープの pytest が走り、往復が 1 回増えていた（2026-11-10・窓口 122 本目の実測）。
//
// 仕組み：
//   - 番の初め（UserPromptSubmit フック＝`node turn-baseline.mjs`）に、その時点で
//     変わっている全パスの中身の指紋を、会話（session_id）ごとに控える。
//   - Stop（gate.mjs）では、控えと指紋が違うパス＝**この番のうちに中身が動いた
//     パス**だけを検査の対象にする（lint の対象・pytest の範囲の両方）。
//     どれも動いていなければ、ゲートは何も走らせずに通す。
//   - gate.mjs が番を通したとき（block しなかったとき）も控えを書き直す＝
//     利用者の発言でなく通知で始まった番も、前の番の終わりを起点にできる。
//
// ⚠️ 安全側への倒し方：控えが無い（配線されていない・session_id が無い・控えの
// 読み書きに失敗した）ときは **今までどおり全部を見る**。控えのせいで検査が
// 減るのは「この番で中身が動いていない」と指紋で言えたときだけ。
// ⚠️ この番の間に**別の会話**が同じファイルを書き換えた場合は、こちらの番の
// 変更として数えてしまう（多く見る側＝止めすぎであって見落としではない）。
// ⚠️ 見なくなったものも最後には捕まる：コミット前ゲート（pre-commit-gate.mjs）は
// 番と無関係に作業ツリーを見る。
//
// UserPromptSubmit では exit 2 が「発言を消して止める」になるので、ここは
// **何が起きても exit 0**（hook-shim を通さない）。失敗しても上の安全側に落ちる。

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync, renameSync, statSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { git } from "./git-changes.mjs";

export const BASELINE_NAME = "radiosim-qa-turn-baseline.json";
const MAX_SESSIONS = 30; // 古い会話の控えは捨てる（ファイルが育ち続けない）

/** 控えの置き場：git が報告する git dir の中（linked worktree でも書ける・B-315 と同じ）。 */
export function baselineFile(cwd) {
  const dir = git(cwd, ["rev-parse", "--absolute-git-dir"]).trim();
  return join(dir, BASELINE_NAME);
}

function walkDir(abs, rel, out) {
  let names;
  try {
    names = readdirSync(abs, { withFileTypes: true });
  } catch {
    return;
  }
  for (const d of names.sort((a, b) => (a.name < b.name ? -1 : 1))) {
    const p = join(abs, d.name);
    if (d.isDirectory()) walkDir(p, `${rel}/${d.name}`, out);
    else {
      try {
        const st = statSync(p);
        out.push(`${rel}/${d.name}\t${st.size}\t${st.mtimeMs}`);
      } catch {
        /* 消えた */
      }
    }
  }
}

/** 1 パスの指紋。ファイル＝中身の sha1（触っただけでは動かない）。
 *  追跡外のフォルダ（`git status` は `dir/` で返す）＝中の名前・大きさ・時刻。
 *  無い＝"-"（削除も「状態」として比べる）。 */
export function fingerprint(cwd, path) {
  const abs = join(cwd, path);
  let st;
  try {
    st = statSync(abs);
  } catch {
    return "-";
  }
  if (st.isDirectory()) {
    const lines = [];
    walkDir(abs, path.replace(/\/+$/, ""), lines);
    return "d:" + createHash("sha1").update(lines.join("\n")).digest("hex");
  }
  try {
    return createHash("sha1").update(readFileSync(abs)).digest("hex");
  } catch {
    return "?";
  }
}

export function fingerprints(cwd, paths) {
  const out = {};
  for (const p of paths) out[p] = fingerprint(cwd, p);
  return out;
}

function readAll(cwd) {
  try {
    const d = JSON.parse(readFileSync(baselineFile(cwd), "utf-8"));
    return d && typeof d === "object" && !Array.isArray(d) ? d : {};
  } catch {
    return {};
  }
}

/** この会話の控えを、いまの `paths`（＝変わっている全パス）で書き直す。 */
export function recordBaseline(cwd, session, paths) {
  if (!session) return false;
  const all = readAll(cwd);
  all[session] = { at: Date.now(), files: fingerprints(cwd, paths) };
  const keep = Object.entries(all)
    .sort((a, b) => (b[1]?.at || 0) - (a[1]?.at || 0))
    .slice(0, MAX_SESSIONS);
  const file = baselineFile(cwd);
  const tmp = `${file}.${process.pid}.tmp`;
  writeFileSync(tmp, JSON.stringify(Object.fromEntries(keep)));
  renameSync(tmp, file); // 同時に走る別の会話の書き込みと混ざらない
  return true;
}

/** この番のうちに中身が動いたパスの集合。控えが無ければ null（＝全部を見る）。
 *  控えに無いパス（番の初めは HEAD のままだった）は「動いた」。 */
export function movedThisTurn(cwd, session, paths) {
  if (!session) return null;
  const base = readAll(cwd)[session];
  if (!base || typeof base.files !== "object" || base.files === null) return null;
  const moved = new Set();
  for (const p of paths) {
    if (!(p in base.files) || base.files[p] !== fingerprint(cwd, p)) moved.add(p);
  }
  return moved;
}

// --- UserPromptSubmit の入口 -------------------------------------------------

async function main() {
  try {
    const { changedAllPaths, hookTreeFor } = await import("./git-changes.mjs");
    const input = JSON.parse(readFileSync(0, "utf-8") || "{}");
    const cwd = hookTreeFor(input.cwd || process.cwd());
    recordBaseline(cwd, input.session_id, changedAllPaths(cwd));
  } catch {
    /* 控えが書けない＝ゲートは今までどおり全部を見る（安全側） */
  }
  process.exit(0);
}

// import しただけでは走らない（gate.mjs と同じ作法）。
const invokedDirectly =
  process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop());
if (invokedDirectly) main();
