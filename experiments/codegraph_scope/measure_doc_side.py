"""doc の読み込みが何にいくら使われているかを実測する（2026-08-31）。

問い: **「ドキュメントの読み込みを畳む」道具を選ぶ前に、何を畳むのかを知る。**
CodeGraph の射程を測ったとき（experiments/codegraph_scope/）に doc 側が探索の
37.5% を占めると出たので、その中身を割る。

⚠️ ここでは `_fold` を使わず自前で 1 パスする＝**tool_use_id ごとに「対象」と
「結果トークン」を結び付ける**必要があるため（`_fold` はツール名単位でしか
結果トークンを持たない）。畳み方（message.id 単位）に依存する数字は出さない。
"""
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(r"D:\dev\radiosim-repo\tools\token-usage")))
import analyze_usage as A  # noqa: E402

DOC_RE = re.compile(r"\.(md|txt|tsv|csv|json)$", re.I)


def norm(path: str) -> str:
    """置き場の違いを畳んで、読まれたファイルの名前に寄せる。"""
    p = path.replace("\\", "/")
    if "/memory/" in p or p.endswith("MEMORY.md"):
        return "memory/" + p.rsplit("/", 1)[-1]
    if "/.qa/codex_review/" in p:
        return ".qa/codex_review/*（Codex の返答原文）"
    if "/tasks/" in p and p.endswith(".output"):
        return "（サブエージェントの出力）"
    for key in ("ISSUES.md", "CHANGELOG.md", "README.md"):
        if p.endswith("/" + key) or p == key:
            return key
    if "/docs/" in p:
        return "docs/" + p.rsplit("/", 1)[-1]
    return p.rsplit("/", 1)[-1]


def main():
    calls = collections.Counter()        # ファイル別 Read 回数
    tok = collections.Counter()          # ファイル別 結果トークン
    whole = collections.Counter()        # offset/limit 無しの Read（全文読み）
    partial = collections.Counter()
    grep_targets = collections.Counter()  # Grep がどの doc を叩いたか
    total_read_tok = 0
    total_doc_tok = 0

    for path in sorted(A._project_logs().glob("*.jsonl")):
        pending = {}
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
                    if b.get("type") != "tool_use":
                        continue
                    name = b.get("name", "?")
                    inp = b.get("input") or {}
                    if name == "Read":
                        fp = str(inp.get("file_path") or "")
                        pending[b.get("id")] = ("Read", fp, inp)
                    elif name == "Grep":
                        tgt = str(inp.get("path") or "")
                        if DOC_RE.search(tgt) or "memory" in tgt or "ISSUES" in tgt:
                            grep_targets[norm(tgt)] += 1
            elif entry.get("type") == "user":
                for b in A.blocks(msg):
                    if b.get("type") != "tool_result":
                        continue
                    got = pending.get(b.get("tool_use_id"))
                    if not got:
                        continue
                    _n, fp, inp = got
                    t = A.approx_tokens(A._result_text(b))
                    total_read_tok += t
                    if not DOC_RE.search(fp):
                        continue
                    k = norm(fp)
                    calls[k] += 1
                    tok[k] += t
                    total_doc_tok += t
                    if inp.get("offset") or inp.get("limit"):
                        partial[k] += 1
                    else:
                        whole[k] += 1

    print(f"Read の結果トークン合計 {total_read_tok:,} tok "
          f"／ そのうち doc/md 系 {total_doc_tok:,} tok "
          f"（{total_doc_tok/max(total_read_tok,1):.1%}）")
    print()
    print("-- doc の Read（結果トークンの多い順・上位 18） --")
    print(f"  {'ファイル':38}{'回数':>6}{'全文':>6}{'部分':>6}{'結果 tok':>12}{'平均':>9}")
    for k, t in tok.most_common(18):
        n = calls[k]
        print(f"  {k:38}{n:6,}{whole[k]:6,}{partial[k]:6,}{t:12,}{t//max(n,1):9,}")
    print()
    print("-- Grep が叩いた doc（上位 10） --")
    for k, n in grep_targets.most_common(10):
        print(f"  {k:38}{n:6,}")


if __name__ == "__main__":
    main()
