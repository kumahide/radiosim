"""上の分類の「不明」「混在」に何が入っているかを標本で確かめる。
⚠️ ラベル自体が思い込みのことがある（[[feedback-synthetic-cases-lie]]）。
"""
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "token-usage"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import analyze_usage as A  # noqa: E402
from measure_scope import classify_target, targets_of  # noqa: E402

buckets = {"不明": [], "両方": [], "py": [], "doc": []}
for path in sorted(A._project_logs().glob("*.jsonl")):
    responses, _t, _f, _u = A._fold(path)
    for r in responses:
        if A.classify(r) != "探索":
            continue
        for name, inp, _ in r.tools:
            if name in ("Bash", "PowerShell"):
                cmd = A._command_head(inp.get("command", "")).strip()
                if not A._READONLY_SHELL.search(cmd):
                    continue
            elif name not in A._EXPLORE_TOOLS:
                continue
            t = classify_target(name, inp)
            buckets[t].append((name, " ".join(targets_of(name, inp))[:150]))

random.seed(7)
for k in ("不明", "両方", "py", "doc"):
    v = buckets[k]
    print(f"===== {k}（{len(v)} 件）から 12 件 =====")
    for name, blob in random.sample(v, min(12, len(v))):
        print(f"  [{name}] {blob}")
    print()
