#!/usr/bin/env node
// Release-boundary advisory — fires at an RC/final promotion and, on demand,
// before cutting a release.
//
//   node tools/qa-hook/release-check.mjs [tag]
//
// Two jobs, both ADVISORY (always exits 0, never blocks a push or build):
//   (1) run the doc<->behavior review (runDocReview, the existing pair table)
//       so behavioral-prose drift surfaces at the version boundary — the exact
//       cadence doc-review is meant for (not per-diff);
//   (2) print the machine-mechanical release checklist so the manual step that
//       has slipped before (binary README not reflecting new features — the
//       2.3RC1 reship) is put in front of the human/Claude at the release
//       decision point, even when the /release skill was bypassed.
//
// Wired from three decision points (see .claude/commands/release.md): the
// pre-push hook (version-tag push), /release step 4, and build.bat's tail
// (the only one the gh-release-create flow reliably passes through).
//
// The deterministic gate (tests/test_docs_consistency.py) stays the authority
// for name-set/version/.py-reference drift; this fills the prose gap and the
// "did I update the outward-facing docs?" gap that no test can fully cover.
//
// The checklist text is single-sourced in release-checklist.txt (this dir) so
// it has one authoritative location the /release procedure points at, instead
// of a second hardcoded copy that silently drifts.
//
// Config (env, optional): same as doc-review.mjs (OLLAMA_URL,
// RADIOSIM_QA_DOC_MODEL, RADIOSIM_QA_DOC_NUM_CTX).

import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// ⚠️ **doc⇔挙動レビューは任意部品**（動的 import）。本体（`tools/qa-mcp/`）は
// ローカル LLM の足場なので**追跡しない**＝clone には存在しない。静的 import だと
// その環境で**モジュール読み込みごと落ち**、チェックリストも刻印照合も出なくなる
// （2026-08-12・I-090 で tools/ の一部を追跡へ移した際に判明）。
// 🔑 **無いときは静かに飛ばす**＝「Ollama が落ちている」（＝在るはずのものが動いて
// いない）とは別の状態なので、同じ声で鳴らさない。鳴らすと clone した人には毎回
// 鳴る警告になる（[[feedback-promote-recurring-checks]] の「毎回鳴る」壊れ方）。
const DOC_REVIEW_LIB = join(dirname(fileURLToPath(import.meta.url)),
                            "..", "qa-mcp", "lib", "doc-review.mjs");

async function loadDocReview() {
  if (!existsSync(DOC_REVIEW_LIB)) return null;
  try {
    return (await import("../qa-mcp/lib/doc-review.mjs")).runDocReview;
  } catch {
    return null;
  }
}

const OLLAMA_URL = process.env.OLLAMA_URL || "http://localhost:11434";
const MODEL = process.env.RADIOSIM_QA_DOC_MODEL || "qwen3:8b";
const NUM_CTX = parseInt(process.env.RADIOSIM_QA_DOC_NUM_CTX || "8192", 10);

const HERE = dirname(fileURLToPath(import.meta.url));
const CHECKLIST_FILE = join(HERE, "release-checklist.txt");

// Single-sourced checklist. Falls back to a minimal inline note if the file is
// missing so a stripped checkout still surfaces the most-skipped step.
function loadChecklist() {
  try {
    return readFileSync(CHECKLIST_FILE, "utf-8")
      .split(/\r?\n/)
      .filter((ln) => ln.trim() && !ln.startsWith("#"))
      .join("\n");
  } catch {
    return (
      "リリース前チェックリスト: ⚠ README ×4 本文に新機能を反映" +
      "（バイナリREADME最優先）／version.py・CHANGELOG・spec 版・Tier-0 ゲート緑。\n" +
      `(${CHECKLIST_FILE} が読めませんでした)`
    );
  }
}

// Quick reachability probe so a skipped doc-review is LOUD, not silent: if
// Ollama is down every pair errors and the checklist alone could look like a
// clean pass. A 2s AbortController timeout keeps a release from hanging on it.
async function ollamaReachable() {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 2000);
  try {
    const res = await fetch(`${OLLAMA_URL}/api/tags`, { signal: ctrl.signal });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(t);
  }
}

async function main() {
  const root = process.cwd();
  const tag = process.argv.slice(2).find((a) => !a.startsWith("--"));

  process.stderr.write(
    `\n[QA RELEASE] version-boundary advisory${tag ? ` (${tag})` : ""} — ` +
      `doc<->behavior review + release checklist. Advisory only; never blocks.\n`,
  );

  // (1) doc<->behavior review over the existing pair table.
  const runDocReview = await loadDocReview();
  if (!runDocReview) {
    // 足場ごと無い環境（clone）＝この検査はそもそも配線されていない。
    process.stderr.write(
      "\n[QA DOC] 未配線 — doc⇔挙動レビューはローカル QA 環境の部品です" +
        "（tools/qa-mcp/ は非追跡）。以下のチェックリストと突き合わせは通常どおり動きます。\n",
    );
  } else if (!(await ollamaReachable())) {
    process.stderr.write(
      `\n⚠⚠ [QA DOC] SKIPPED — Ollama に接続できません (${OLLAMA_URL})。` +
        `doc⇔挙動レビューは走っていません。\n` +
        `   → 版節目レビューの一部が未実施です。Ollama を起動して再実行` +
        `（node tools/qa-hook/release-check.mjs）するか、doc⇔挙動を手動確認せよ。\n`,
    );
  } else {
    process.stderr.write(`\n[QA DOC ${MODEL}] checking doc<->behavior pairs...\n`);
    let issues = 0;
    let errors = 0;
    let ran = 0;
    try {
      const results = await runDocReview({
        root,
        url: OLLAMA_URL,
        model: MODEL,
        numCtx: NUM_CTX,
      });
      for (const r of results) {
        ran++;
        if (r.error) {
          errors++;
          process.stderr.write(`\n### ${r.title}\n[error] ${r.error}\n`);
        } else if (r.ok) {
          process.stderr.write(`\n### ${r.title}\nNO ISSUES FOUND.\n`);
        } else {
          issues++;
          process.stderr.write(`\n### ${r.title}\n${r.text}\n`);
        }
      }
      // Reachable up front but every pair still errored ⇒ treat as "did not run"
      // and warn loudly rather than let the checklist imply a clean pass.
      if (ran > 0 && errors === ran) {
        process.stderr.write(
          `\n⚠⚠ [QA DOC] 全 ${ran} ペアがエラー — doc⇔挙動レビューは実質未実施。` +
            `手動で確認せよ。\n`,
        );
      } else {
        process.stderr.write(
          `\n[QA DOC] done — ${issues} pair(s) with candidate mismatches, ` +
            `${errors} error(s). Advisory only; confirm before acting.\n`,
        );
      }
    } catch (e) {
      process.stderr.write(
        `\n⚠⚠ [QA DOC] SKIPPED — レビュー実行時エラー (${e && e.message})。` +
          `doc⇔挙動は手動確認せよ。\n`,
      );
    }
  }

  // (2) CI status — asked, not remembered.
  process.stderr.write("\n" + await ciStatusLine() + "\n");

  // (3) 表示依存の面が、この commit で回っているか（B-074(b)）。
  process.stderr.write("\n" + (await displayRunLine(root)) + "\n");

  // (4) machine-mechanical release checklist (single-sourced).
  process.stderr.write("\n" + loadChecklist() + "\n");
  process.exit(0);
}

// CI が赤いまま RC と正式リリースを通過した（2026-08-08〜11・3 回の push）。
// チェックリストには「CI 緑」の 1 行が前からあったのに、**人が読み飛ばす形**
// だったので効かなかった。⇒ 覚えている運用をやめ、**その場で聞いて字を出す**
// （[[feedback-promote-recurring-checks]] の昇格）。
// ⚠️ 助言専用のまま＝gh が無い/未認証/オフラインでも黙って通す（このスクリプト
// 自体は常に exit 0）。ここで止めると、ネットワークの都合でリリースが止まる。
async function ciStatusLine() {
  try {
    const { execFileSync } = await import("node:child_process");
    const out = execFileSync(
      "gh",
      ["run", "list", "--limit", "1", "--json", "conclusion,headBranch,createdAt"],
      { encoding: "utf8", timeout: 20000, stdio: ["ignore", "pipe", "ignore"] },
    );
    const [run] = JSON.parse(out);
    if (!run) return "[CI] 実行履歴なし（判断は手動で）";
    const when = String(run.createdAt).slice(0, 16).replace("T", " ");
    if (run.conclusion === "success") {
      return `[CI] ✅ 緑（${run.headBranch} / ${when}）`;
    }
    return (
      `\n🔴🔴 [CI] 直近の実行が ${run.conclusion}（${run.headBranch} / ${when}）\n` +
      `        赤いまま配布すると、緑を前提にした工程が全部意味を失う。\n` +
      `        gh run view --log-failed で中身を見てから進むこと。\n`
    );
  } catch (e) {
    return `[CI] 状態を取得できなかった（${e && e.message}）＝手動で確認せよ`;
  }
}

// 表示依存のテストは **CI で 1 本も走らない**（ランナーに表示が無く、xvfb を足しても
// assert しているのが Windows のフォント実測ピクセルなので同じ検査にならない）。
// ⇒ 「誰かが表示のある機械で回す」以外に結果を得る道が無いのに、**回ったかどうかを
// 誰も確かめていなかった**＝2.7 のスケール追従のゲートは、CI で skip され開発機で
// 赤いまま、RC も正式も通過した（B-074）。
// ⇒ tests/conftest.py が .qa/display_run.json へ**回った事実**を刻むので、ここで
// HEAD と突き合わせる（チェックリストの一行と違い、読み飛ばしても記録が残る）。
// ⚠️ 助言専用のまま＝刻印が無くても止めない（このスクリプトは常に exit 0）。
async function displayRunLine(root) {
  const { execFileSync } = await import("node:child_process");
  const git = (args) =>
    execFileSync("git", args, {
      cwd: root, encoding: "utf8", timeout: 20000, stdio: ["ignore", "pipe", "ignore"],
    }).trim();

  let stamp;
  try {
    stamp = JSON.parse(readFileSync(join(root, ".qa", "display_run.json"), "utf-8"));
  } catch {
    return (
      "\n🔴🔴 [表示依存] 刻印がありません＝この作業ツリーで**フルスイートが 1 度も\n" +
      "        通っていない**（表示のある機械で回すと自動で刻まれる）。\n" +
      '        → & "$env:RADIOSIM_PYTHON" -m pytest を最後まで回すこと。\n'
    );
  }

  let head = "";
  try { head = git(["rev-parse", "HEAD"]); } catch { /* git が無くても止めない */ }
  const when = String(stamp.when || "").slice(0, 16).replace("T", " ");
  if (head && stamp.commit === head) {
    return `[表示依存] ✅ この commit で回っている（${when} / 実行 ${stamp.ran} 本）`;
  }

  // 🔑 **commit が違うだけでは鳴らさない**＝毎コミット鳴る網は読まれなくなる
  // （[[feedback-promote-recurring-checks]] の「毎回鳴る」壊れ方）。刻印以降に
  // **表示に効く層が動いたか**を見て、動いていなければ字を弱める。
  let touched = [];
  try {
    touched = git(["diff", "--name-only", `${stamp.commit}..HEAD`, "--", "views", "tests"])
      .split(/\r?\n/).filter(Boolean);
  } catch {
    return (
      `⚠⚠ [表示依存] 刻印は ${String(stamp.commit).slice(0, 7)}（${when}）で、HEAD と照合できません。\n` +
      "        → 表示のある機械でフルスイートを回し直すこと。"
    );
  }
  if (touched.length === 0) {
    return (
      `[表示依存] ⚪ 刻印は ${String(stamp.commit).slice(0, 7)}（${when}）＝HEAD とは違うが、\n` +
      "           以降 views/ と tests/ は動いていない（回し直しの必要は薄い）。"
    );
  }
  return (
    `\n🔴🔴 [表示依存] 刻印は ${String(stamp.commit).slice(0, 7)}（${when}）で、以降\n` +
    `        views/ tests/ が ${touched.length} ファイル動いている＝**表示依存の面は\n` +
    "        この状態で 1 度も検査されていない**（CI では構造的に走らない）。\n" +
    '        → & "$env:RADIOSIM_PYTHON" -m pytest を最後まで回してから配ること。\n'
  );
}

main().catch((e) => {
  process.stderr.write(`[QA RELEASE] error: ${e && e.message}\n`);
  process.exit(0);
});
