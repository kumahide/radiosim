#!/usr/bin/env node
// Release-boundary advisory — fires at an RC/final promotion and, on demand,
// before cutting a release.
//
//   node tools/qa-hook/release-check.mjs [tag]
//
// Jobs, all ADVISORY (always exits 0, never blocks a push or build): report the
// CI colour, report whether the display-dependent suite has run for this commit,
// and print the machine-mechanical release checklist so the manual step that has
// slipped before (binary README not reflecting new features — the 2.3RC1 reship)
// is put in front of the human/Claude at the release decision point, even when
// the /release skill was bypassed.
//
// 🔁 **2026-08-31 に doc⇔挙動の助言レビュー（ローカル LLM・qwen3:8b）を外した。**
// 通算で真陽性は導入時の 1 件（GitHub issue #15 の再発防止）だけで、以後 7 回の
// 報告はすべて偽陽性か既裁定の再掲だった。しかも一度 ENOENT で 1 か月死んでいて、
// **非ゲート・常に exit 0 なので「死んでいる」と「指摘なし」が区別できなかった**
// （[[feedback-promote-recurring-checks]] の壊れ方①）。受け持っていた面＝「挙動の
// 散文 ⇔ 実装」は Codex の①コード面（`tools/codex_review/run.ps1 -Mode code`）が
// リポジトリ全体を見て実際に拾っている（B-161・I-038・Deygout の未開示など）。
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
// このスクリプトは Ollama にもローカル LLM の足場にも依存しない（2026-08-31 以降）。

import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

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

async function main() {
  const root = process.cwd();
  const tag = process.argv.slice(2).find((a) => !a.startsWith("--"));

  process.stderr.write(
    `\n[QA RELEASE] version-boundary advisory${tag ? ` (${tag})` : ""} — ` +
      `CI / display-run stamp / release checklist. Advisory only; never blocks.\n`,
  );

  // (1) CI status — asked, not remembered.
  process.stderr.write("\n" + ciStatusLine(root) + "\n");

  // (2) 表示依存の面が、この commit で回っているか（B-074(b)）。
  process.stderr.write("\n" + (await displayRunLine(root)) + "\n");

  // (3) machine-mechanical release checklist (single-sourced).
  process.stderr.write("\n" + loadChecklist() + "\n");
  process.exit(0);
}

// CI が赤いまま RC と正式リリースを通過した（2026-08-08〜11・3 回の push）。
// チェックリストには「CI 緑」の 1 行が前からあったのに、**人が読み飛ばす形**
// だったので効かなかった。⇒ 覚えている運用をやめ、**その場で聞いて字を出す**
// （[[feedback-promote-recurring-checks]] の昇格）。
// ⚠️ 助言専用のまま＝gh が無い/未認証/オフラインでも黙って通す（このスクリプト
// 自体は常に exit 0）。ここで止めると、ネットワークの都合でリリースが止まる。
function ciStatusLine(root) {
  try {
    const out = execFileSync("gh", ciRunListArgs(ciBranch(root)), {
      encoding: "utf8", timeout: 20000, stdio: ["ignore", "pipe", "ignore"],
    });
    return ciRunLine(JSON.parse(out)[0]);
  } catch (e) {
    return `[CI] 状態を取得できなかった（${e && e.message}）＝手動で確認せよ`;
  }
}

// 🔴 **B-303**＝以前は `--branch` を付けずに `--limit 1` で引いていた＝別のブランチ
// （`feature/field-phase1` など）の実行を、いま見たいブランチの色として出しえた。
// ⇒ 見るのはいまのブランチ（切り離された HEAD＝タグの push などは main）。
export function ciBranch(root) {
  try {
    const b = git(root, ["rev-parse", "--abbrev-ref", "HEAD"]);
    return b && b !== "HEAD" ? b : "main";
  } catch {
    return "main";
  }
}

export function ciRunListArgs(branch) {
  return ["run", "list", "--branch", branch, "--limit", "1",
          "--json", "status,conclusion,headBranch,createdAt"];
}

// 🔒 **🔴🔴 は、赤と確かめられたときだけ出す**（B-303）。
// 🔴 以前は `conclusion === "success"` かどうかだけで分けていた＝実行中（`status` が
// `in_progress`・`conclusion` が空）を失敗の枝へ落とし、「直近の実行が （…）」と
// 結論の欄が空のまま赤の文面で出た。`/release` は push → ビルドの順なので、
// ビルド（約 1 分）は必ず CI（約 8 分）の実行中に終わる＝RC・正式のたびに鳴っていた。
// 毎回鳴る網は、本当に赤いときの 🔴🔴 まで読み飛ばされる（[[feedback-promote-recurring-checks]]）。
export function ciRunLine(run) {
  if (!run) return "[CI] 実行履歴なし（判断は手動で）";
  const when = String(run.createdAt).slice(0, 16).replace("T", " ");
  const where = `${run.headBranch} / ${when}`;
  if (run.status !== "completed" || !run.conclusion) {
    return (
      `[CI] ⏳ 実行中（${run.status || "状態不明"}・${where}）＝まだ色が無い。\n` +
      "        終わってから確かめる（gh run watch）。"
    );
  }
  if (run.conclusion === "success") {
    return `[CI] ✅ 緑（${where}）`;
  }
  return (
    `\n🔴🔴 [CI] 直近の実行が ${run.conclusion}（${where}）\n` +
    `        赤いまま配布すると、緑を前提にした工程が全部意味を失う。\n` +
    `        gh run view --log-failed で中身を見てから進むこと。\n`
  );
}

function git(root, args) {
  return execFileSync("git", args, {
    cwd: root, encoding: "utf8", timeout: 20000, stdio: ["ignore", "pipe", "ignore"],
  }).trim();
}

// 表示依存のテストは **CI で 1 本も走らない**（ランナーに表示が無く、xvfb を足しても
// assert しているのが Windows のフォント実測ピクセルなので同じ検査にならない）。
// ⇒ 「誰かが表示のある機械で回す」以外に結果を得る道が無いのに、**回ったかどうかを
// 誰も確かめていなかった**＝2.7 のスケール追従のゲートは、CI で skip され開発機で
// 赤いまま、RC も正式も通過した（B-074）。
// ⇒ tests/conftest.py が .qa/display_run.json へ**回った事実**を刻むので、ここで
// HEAD と突き合わせる（チェックリストの一行と違い、読み飛ばしても記録が残る）。
// ⚠️ 助言専用のまま＝刻印が無くても止めない（このスクリプトは常に exit 0）。
//
// 🔒 **照らすのは commit ではなく中身**（B-294）＝刻印の `display_files`（検査した
// 作業ツリーの views/ tests/ の「パス → blob」）と、HEAD の同じ範囲の blob を比べる。
// 🔴 以前は刻印の commit と HEAD を比べていた＝コミット前ゲートは作業ツリーのまま
// 回すので刻印は必ず親を指し、自分のコミットの views/ tests/ を見つけて 🔴🔴 で鳴った。
// 逆向きに、未コミットの変更で回してから戻すと commit が一致して嘘の ✅ になった。
// 作り方は tests/conftest.py の `_display_files` と対（範囲は `DISPLAY_SCOPE`）。
export const DISPLAY_SCOPE = ["views", "tests"];

export function headDisplayFiles(root) {
  const out = execFileSync("git", ["ls-tree", "-r", "-z", "HEAD", "--", ...DISPLAY_SCOPE], {
    cwd: root, encoding: "utf8", timeout: 20000, stdio: ["ignore", "pipe", "ignore"],
  });
  const files = {};
  for (const rec of out.split("\0")) {
    const m = /^\d+ blob ([0-9a-f]+)\t(.+)$/s.exec(rec);
    if (m) files[m[2]] = m[1];
  }
  return files;
}

// 中身が違うパス（片方にしか無いものも含む）を返す。
export function differingDisplayFiles(stamped, head) {
  const paths = new Set([...Object.keys(stamped), ...Object.keys(head)]);
  return [...paths].filter((p) => stamped[p] !== head[p]).sort();
}

export function displayRunLine(root) {
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

  const when = String(stamp.when || "").slice(0, 16).replace("T", " ");
  const rerun = "        → 表示のある機械でフルスイートを回し直すこと。";
  if (!stamp.display_files || typeof stamp.display_files !== "object") {
    // B-294 より前の刻印＝commit しか持たず、中身で照らせない。✅ とは言わない。
    return `⚠⚠ [表示依存] 刻印が旧形式（${when}）で、中身を照合できません。\n` + rerun;
  }
  let head;
  try {
    head = headDisplayFiles(root);
  } catch {
    return `⚠⚠ [表示依存] 刻印（${when}）を HEAD と照合できません（git が使えない）。\n` + rerun;
  }

  const differ = differingDisplayFiles(stamp.display_files, head);
  if (differ.length === 0) {
    return (
      `[表示依存] ✅ HEAD の views/ tests/ はこの中身で回っている` +
      `（${when} / 実行 ${stamp.ran} 本）`
    );
  }
  const shown = differ.slice(0, 3).join(" ") + (differ.length > 3 ? " …" : "");
  return (
    `\n🔴🔴 [表示依存] 刻印（${when}）は、HEAD と views/ tests/ の中身が\n` +
    `        ${differ.length} ファイル違う（${shown}）＝**表示依存の面は\n` +
    "        この状態で 1 度も検査されていない**（CI では構造的に走らない）。\n" +
    '        → & "$env:RADIOSIM_PYTHON" -m pytest を最後まで回してから配ること。\n'
  );
}

// 読み込んだだけでは走らせない（tests/test_release_check.py が判定の関数を import する）。
// ⚠️ Windows はドライブ文字の大小が呼び方で変わる（`d:\` と `D:\`）＝大小を無視して比べる。
// 食い違うと main() が黙って走らず、助言が「指摘なし」と区別できなくなる。
const norm = (p) => (process.platform === "win32" ? p.toLowerCase() : p);
if (process.argv[1] && norm(resolve(process.argv[1])) === norm(fileURLToPath(import.meta.url))) {
  main().catch((e) => {
    process.stderr.write(`[QA RELEASE] error: ${e && e.message}\n`);
    process.exit(0);
  });
}
