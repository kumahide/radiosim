"""CodeGraph の射程（母数）を実測する＝新トリガー案①（2026-08-31）。

問い: **探索の中で「コードの構造への問い」はどれだけあるか。**
CodeGraph が畳めるのはそこだけで、doc/memory の読み取りには一切効かない
（ソースしか索引しない）。母数が小さければ導入しても効かない。

⚠️ ここで出すのは**上限（ceiling）**であって効果ではない。
   「.py を対象にした探索呼び出し」は全部が構造の問いではない（実装を読むために
   開いた回も入る）ので、実際の削減はこれより必ず小さい。

数え方は `analyze_usage.py` の `_fold` / `classify` をそのまま借りる
（B-077＝畳み方を 2 か所に持たない）。
"""
import collections
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "token-usage"))
import analyze_usage as A  # noqa: E402

# --- 対象の分類 -------------------------------------------------------------
# CodeGraph が索引するのは **Python のソース**だけ。
# ⚠️ 初版は `--include=*.py` や `views/` 直下の grep を取りこぼして「不明」へ落とし、
#    py の母数を過小に見せていた（標本 12 件で発見＝ラベルは疑ってから使う）。
_PY = re.compile(r"(?:[\w./\\-]+|\*)\.py\b|--include=\*?\.?py\b")
# ソースの置き場を直に指した探索（`views 共通設定` のような形）も py に数える。
_PY_DIR = re.compile(r"[/\\]?(core|views|report|tests|buildtools)([/\\]|\s|$)")
_DOC = re.compile(r"[\w./\\-]+\.(md|txt|json|tsv|yml|yaml|csv|bat|ps1|mjs|js|spec)\b")
_MEMORY_HINT = re.compile(r"memory|MEMORY|ISSUES|CHANGELOG|README|docs[/\\]|roadmap")

# 構造の問い＝定義・呼び出し元・import を探す形。CodeGraph の本来の射程。
# ⚠️ 粗い当たり判定（正規表現で「構造を聞いている」と読める形だけ）。
_STRUCTURAL = re.compile(
    r"\bdef\s|\bclass\s|\bimport\b|\bfrom\s+\w+\s+import|"
    r"^\s*(def|class)|\(\)|\bcall|\busage\b"
)


def targets_of(name, inp):
    """1 つの探索呼び出しが触った対象の文字列を集める。"""
    out = []
    if name == "Read":
        out.append(str(inp.get("file_path") or ""))
    elif name in ("Grep", "Glob"):
        out.append(str(inp.get("path") or ""))
        out.append(str(inp.get("glob") or ""))
        out.append(str(inp.get("type") or ""))
        out.append(str(inp.get("pattern") or ""))
    elif name in ("Bash", "PowerShell"):
        out.append(str(inp.get("command") or ""))
    return [t for t in out if t]


def classify_target(name, inp):
    """py / doc / 両方 / 不明 のどれを触った探索か。"""
    blob = " ".join(targets_of(name, inp))
    if not blob:
        return "不明"
    py = (bool(_PY.search(blob)) or bool(_PY_DIR.search(blob))
          or (name == "Grep" and inp.get("type") == "py"))
    doc = bool(_DOC.search(blob)) or bool(_MEMORY_HINT.search(blob))
    if py and not doc:
        return "py"
    if doc and not py:
        return "doc"
    if py and doc:
        return "両方"
    return "不明"


def main():
    logs = sorted(A._project_logs().glob("*.jsonl"))
    calls = collections.Counter()          # 対象別の探索呼び出し数
    result_tok = collections.Counter()     # 対象別のツール結果トークン
    structural = 0                         # py 探索のうち構造の問いに見えるもの
    py_calls = 0
    resp_kind = collections.Counter()      # 往復の作業構成
    explore_resp_target = collections.Counter()  # 探索往復を対象別に
    chains = collections.Counter()         # 連続する py 探索往復の長さ
    run = 0
    total_resp = 0
    total_input = 0

    for path in logs:
        responses, tool_result_tok, _f, _u = A._fold(path)
        # ツール結果トークンはツール名単位でしか取れない（対象別に割れない）ので、
        # 対象別の按分は呼び出し数の比で行う＝概算であることを明示する。
        for r in responses:
            total_resp += 1
            total_input += r.total_input
            kind = A.classify(r)
            resp_kind[kind] += 1
            if kind != "探索":
                if run:
                    chains[run] += 1
                    run = 0
                continue
            seen = set()
            for name, inp, _ in r.tools:
                if name not in (A._EXPLORE_TOOLS | {"Bash", "PowerShell"}):
                    continue
                if name in ("Bash", "PowerShell"):
                    cmd = A._command_head(inp.get("command", "")).strip()
                    if not A._READONLY_SHELL.search(cmd):
                        continue
                t = classify_target(name, inp)
                calls[t] += 1
                seen.add(t)
                if t == "py":
                    py_calls += 1
                    if _STRUCTURAL.search(" ".join(targets_of(name, inp))):
                        structural += 1
            tgt = ("py" if seen == {"py"} else
                   "doc" if seen == {"doc"} else
                   "混在" if seen else "不明")
            explore_resp_target[tgt] += 1
            if tgt == "py":
                run += 1
            else:
                if run:
                    chains[run] += 1
                    run = 0
        for nm, tok in tool_result_tok.items():
            if nm in ("Read", "Grep", "Glob"):
                result_tok[nm] += tok
    if run:
        chains[run] += 1

    total_calls = sum(calls.values())
    print(f"記録 {len(logs)} 本 / 往復 {total_resp:,} / 総入力 {total_input:,} tok")
    print()
    print("-- 往復の作業構成 --")
    for k in A.KINDS:
        print(f"  {k:6} {resp_kind[k]:6,}  ({resp_kind[k]/total_resp:5.1%})")
    print()
    print(f"-- 探索の呼び出し {total_calls:,} 件を対象別に --")
    for t, n in calls.most_common():
        print(f"  {t:6} {n:6,}  ({n/total_calls:5.1%})")
    print()
    print("-- 探索往復を対象別に（その往復が触った対象の集合） --")
    tot = sum(explore_resp_target.values())
    for t, n in explore_resp_target.most_common():
        print(f"  {t:6} {n:6,}  ({n/tot:5.1%} of 探索往復 / {n/total_resp:5.1%} of 全往復)")
    print()
    print(f"-- .py を触った探索呼び出し {py_calls:,} のうち、構造の問いに見える形: "
          f"{structural:,}  ({structural/max(py_calls,1):.1%})")
    print()
    print("-- 連続する『py だけの探索往復』の鎖（CodeGraph が畳める形） --")
    collapsible = 0
    for ln in sorted(chains):
        n = chains[ln]
        # 長さ L の鎖は理屈の上では 1 往復へ畳める＝節約は (L-1) 往復
        collapsible += (ln - 1) * n
        print(f"  長さ {ln:2}: {n:4} 本")
    print(f"  ⇒ 全部畳めたと仮定した往復の節約上限: {collapsible:,} 往復 "
          f"（全往復の {collapsible/total_resp:.1%}）")
    print()
    print("-- 探索系ツールが投入した結果トークン --")
    s = sum(result_tok.values())
    for nm, tok in result_tok.most_common():
        print(f"  {nm:6} {tok:12,} tok")
    print(f"  合計 {s:,} tok = 総入力の {s/total_input:.2%}")


if __name__ == "__main__":
    main()
