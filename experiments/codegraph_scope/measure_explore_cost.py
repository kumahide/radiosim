"""Explore サブエージェントの費用と損益分岐を実測する（子の記録は subagents/ に在る）。

🔴 **子の消費は親の jsonl に載らない**＝`<session>/subagents/agent-*.jsonl` が別に在る。
   ⇒ `tools/token-usage/analyze_usage.py` は**子を 1 トークンも数えていない**
     （glob が親の `*.jsonl` だけ）。採用するなら主指標に穴が開く。
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools" / "token-usage"))
import analyze_usage as A  # noqa: E402


def total_input(u):
    return ((u.get("input_tokens") or 0)
            + (u.get("cache_creation_input_tokens") or 0)
            + (u.get("cache_read_input_tokens") or 0))


def fold_usage(path):
    """message.id 単位に畳んで (往復ごとの総入力, 出力, ツール名) を返す。"""
    by_mid, order, tools = {}, [], []
    out = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = entry.get("message") or {}
        if entry.get("type") != "assistant":
            continue
        mid = msg.get("id") or entry.get("requestId") or f"_{len(order)}"
        if mid not in by_mid:
            by_mid[mid] = {}
            order.append(mid)
        u = msg.get("usage") or {}
        if u:
            by_mid[mid] = u
        for b in A.blocks(msg):
            if b.get("type") == "tool_use":
                tools.append(b.get("name", "?"))
    for mid in order:
        out += (by_mid[mid].get("output_tokens") or 0)
    return [total_input(by_mid[m]) for m in order], out, tools


def main():
    base = A._project_logs()
    parent = max(base.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    kids = sorted((parent.parent / parent.stem / "subagents").glob("agent-*.jsonl"),
                  key=lambda p: p.stat().st_mtime)
    if not kids:
        print("子の記録が見つからない")
        return
    kid = kids[-1]
    print(f"親: {parent.name}\n子: {kid.name}")

    ins, out, tools = fold_usage(kid)
    pins, _pout, _pt = fold_usage(parent)
    print()
    print(f"① 子の消費: 往復 {len(ins)} / 総入力 {sum(ins):,} tok / 出力 {out:,} tok")
    print(f"   下駄（1 往復目の入力）= {ins[0]:,} tok"
          f"／最終 {ins[-1]:,} tok／伸び {(ins[-1]-ins[0])//max(len(ins)-1,1):,} tok/往復")
    print(f"   子が使ったツール {len(tools)} 回: "
          + ", ".join(f"{n}×{tools.count(n)}" for n in sorted(set(tools))))
    print()
    par_turn = pins[-1]
    print(f"③ 親の 1 往復の値段（直近の文脈）= {par_turn:,} tok")
    cost = par_turn + sum(ins)
    print(f"⇒ Explore 1 回の総費用 = 親 1 往復 {par_turn:,} + 子 {sum(ins):,}"
          f" = **{cost:,} tok**")
    print(f"⇒ 損益分岐: 同じ探索をインラインでやると "
          f"**{cost/par_turn:.1f} 往復**を超える場合に黒字")
    print()
    print("   ⚠️ インラインは 1 往復ごとに親の文脈を丸ごと再送するので、"
          "N 往復＝ N × {:,} tok（＋読んだ内容が以後ずっと乗る）".format(par_turn))


if __name__ == "__main__":
    main()
