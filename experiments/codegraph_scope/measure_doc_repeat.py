"""同じ場所を同じセッションで読み直していないか（＝畳めるのは道具でなく規律かの判定）。"""
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, r"D:\dev\radiosim-repo\tools\token-usage")
import analyze_usage as A  # noqa: E402

DOC_RE = re.compile(r"\.(md|txt|tsv|csv|json)$", re.I)

rep_calls = rep_tok = tot_calls = tot_tok = 0
worst = collections.Counter()
for path in sorted(A._project_logs().glob("*.jsonl")):
    pending, seen = {}, {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = entry.get("message") or {}
        if entry.get("type") == "assistant":
            for b in A.blocks(msg):
                if b.get("type") == "tool_use" and b.get("name") == "Read":
                    inp = b.get("input") or {}
                    fp = str(inp.get("file_path") or "")
                    if DOC_RE.search(fp):
                        key = (fp.replace("\\", "/").lower(),
                               inp.get("offset"), inp.get("limit"))
                        pending[b.get("id")] = key
        elif entry.get("type") == "user":
            for b in A.blocks(msg):
                if b.get("type") != "tool_result":
                    continue
                key = pending.get(b.get("tool_use_id"))
                if not key:
                    continue
                t = A.approx_tokens(A._result_text(b))
                tot_calls += 1
                tot_tok += t
                if key in seen:
                    rep_calls += 1
                    rep_tok += t
                    worst[key[0].rsplit("/", 1)[-1]] += 1
                seen[key] = True

print(f"doc の Read {tot_calls:,} 回 / {tot_tok:,} tok")
print(f"うち **同一セッションで同じ範囲を読み直した** 回 {rep_calls:,} 回 "
      f"({rep_calls/max(tot_calls,1):.1%}) / {rep_tok:,} tok "
      f"({rep_tok/max(tot_tok,1):.1%})")
print("-- 読み直しの多いファイル --")
for k, n in worst.most_common(8):
    print(f"  {k:44}{n:5}")
