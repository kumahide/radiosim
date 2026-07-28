"""
experiments/review_benchmark/run_benchmark.py
=============================================
独立レビューのベンチマークを **1 コマンド**で回す。

    python experiments/review_benchmark/run_benchmark.py ornith:9b
    python experiments/review_benchmark/run_benchmark.py ornith:9b qwen2.5-coder:7b --case C6C7

**これは製品コードではない**（→ ../README.md）。正解ラベルは findings.json、
採点規則と Codex の基準線は README.md、過去の測定は results.md。

## なぜスクリプトにするか
2026-07-26 の初回測定は手作業の PowerShell で、**環境設定を誤って 7 倍遅い数字**を
出しかけた（下記）。同じ轍を踏まないよう、**測り方そのものを実行可能な形で固定する**。

## ⚠️ 埋め込んである環境知見（手で回すと必ず踏む）
- **`num_ctx` の既定はモデル上限**（例: ornith は 131072）。8GB の GPU では KV キャッシュが
  載らず**モデルの半分が CPU へ溢れて 5〜10 倍遅くなる**。ここでは入力実測から必要量を
  見積もって明示指定する。`ollama ps` の PROCESSOR 列が 100% GPU になっていること。
- **thinking を持つモデルは生成トークンの 8 割を思考に使い、結論は変わらなかった**
  （実測: 286 秒 → 52 秒で同じ見落とし）。既定で thinking を切る（`--think` で有効化）。
- 渡し方は Codex と同一＝**観点を指定しない**。ここを変えると測っているのは
  レビュアーではなく渡し方になる。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent
OLLAMA = "http://localhost:11434"

# 観点を指定しない共通プロンプト（Codex への渡し方と揃える）。
PROMPT_HEAD = (
    "以下は Windows 向け Python デスクトップアプリ（無線回線のシミュレータ）の"
    "コミット差分{extra}です。\n"
    "コードレビューをしてください。問題があれば指摘してください。出力は日本語で。\n\n"
)

# ケース＝入力の作り方と、仮採点に使う手掛かり。
# 手掛かりは「その欠陥に言及したか」を機械的に見るためのもので、最終判定は人が読む。
CASES = {
    "C6C7": {
        "findings": ["C6", "C7"],
        "diff": "1ca5089..2730717",
        # C7 の欠陥（_sweep_row）は差分に含まれない＝Codex はワークスペース全体を
        # 読めていた。同条件にするため、差分が触れたファイルの全文を添える。
        "files": ["report_common.py", "report_scenario.py"],
        "at": "2730717",
        "markers": {
            "C6": r"lstrip|先頭\s*の?\s*空白|空白\s*で?\s*始ま",
            "C7": r"_sweep_row|escape\(|エスケープ",
        },
    },
    "C1C2C3": {
        "findings": ["C1", "C2", "C3"],
        "diff": None,
        "files": ["batch.py", "report_summary.py"],
        "at": "1ca5089",
        "markers": {
            "C1": r"_parse_csv_row|生キー|raw\.get|正規化.*非対称|非対称.*正規化",
            "C2": r"formula|数式|インジェクション|HYPERLINK",
            "C3": r"casefold|大文字小文字|大小|exist_ok|同秒|衝突",
        },
    },
    "C5": {
        "findings": ["C5"],
        "diff": "c4c6c8b..0143e6a",
        "files": [],
        "at": "0143e6a",
        "markers": {"C5": r"validate_rows|到達|経路を通|空振り|検証されていない"},
    },
}


def sh(args: list[str]) -> str:
    return subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    ).stdout


def build_input(case: dict) -> str:
    parts = []
    extra = "と、その差分が触れたファイルの全文" if case["files"] else ""
    if case["diff"] is None:
        extra = "対象ファイルの全文"
        parts.append(PROMPT_HEAD.format(extra="").replace("コミット差分", "対象ファイルの全文"))
    else:
        parts.append(PROMPT_HEAD.format(extra=extra))
        parts.append("===== 差分 =====\n" + sh(["git", "diff", case["diff"]]))
    for f in case["files"]:
        body = sh(["git", "show", f"{case['at']}:{f}"])
        parts.append(f"\n\n===== {f}（{case['at']} 時点の全文） =====\n{body}")
    return "".join(parts)


def pick_num_ctx(text: str) -> int:
    """入力の実測から num_ctx を選ぶ（GPU から溢れさせないため上限も設ける）。

    トークン数は実測 3.5 字/トークン前後（コード＋日本語混在）。生成ぶんの余裕を
    足し、8GB 級の GPU に載る 24576 で頭打ちにする。足りない場合は警告する。
    """
    need = int(len(text) / 3.5) + 3000
    for size in (8192, 16384, 24576):
        if need <= size:
            return size
    print(f"  ⚠️ 入力が大きい（推定 {need} tok）＝24576 に切り詰めて実行する。"
          f"GPU に載らなければ CPU へ溢れて数倍遅くなる（ollama ps で確認）", file=sys.stderr)
    return 24576


def ask(model: str, prompt: str, num_ctx: int, think: bool) -> dict:
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False, "think": think,
        "options": {"num_ctx": num_ctx},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate", data=body, headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    # B310 を抑止する理由: URL は定数 OLLAMA（localhost:11434）だけで組み立てており、
    # 外部入力もスキームの選択も入らない（file:/ 等に化ける経路が無い）。
    # ⚠️ 除外は experiments/ ディレクトリごとではなくこの 1 行に限る＝ここを丸ごと
    #    スキャン対象から外すと、以後この配下の実在の指摘も黙って消える。
    with urllib.request.urlopen(req, timeout=3600) as r:  # nosec B310
        data = json.loads(r.read().decode("utf-8"))
    data["_wall"] = time.time() - t0
    return data


def score(text: str, markers: dict[str, str]) -> dict[str, bool]:
    return {fid: bool(re.search(pat, text, re.I)) for fid, pat in markers.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+", help="ollama のモデル名（複数可）")
    ap.add_argument("--case", default="C6C7", choices=sorted(CASES), help="測る事例")
    ap.add_argument("--think", action="store_true", help="思考を有効化（既定は無効＝速い）")
    ap.add_argument("--out", default=str(HERE / "runs"), help="出力先ディレクトリ")
    args = ap.parse_args()

    case = CASES[args.case]
    prompt = build_input(case)
    num_ctx = pick_num_ctx(prompt)
    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"事例 {args.case}（期待={'/'.join(case['findings'])}）"
          f" 入力 {len(prompt):,} 字 / num_ctx={num_ctx} / thinking={'on' if args.think else 'off'}")
    print(f"{'モデル':<22}{'秒':>6}{'入力tok':>9}{'生成tok':>8}  検出")
    for model in args.models:
        try:
            r = ask(model, prompt, num_ctx, args.think)
        except urllib.error.URLError as e:
            print(f"{model:<22} 失敗: {e}（ollama は起動しているか）")
            continue
        text = r.get("response", "")
        hit = score(text, case["markers"])
        tag = re.sub(r"[^\w.-]", "_", model)
        (outdir / f"{args.case}_{tag}.txt").write_text(text, encoding="utf-8")
        marks = " ".join(f"{k}={'○' if v else '×'}" for k, v in hit.items())
        print(f"{model:<22}{r['_wall']:>6.0f}{r.get('prompt_eval_count', 0):>9,}"
              f"{r.get('eval_count', 0):>8,}  {marks}")

    print(f"\n出力は {outdir} に保存した。**○ は言及があっただけ**なので、"
          f"最終判定は本文を読んで行う（findings.json の正解と突き合わせる）。")
    print("Codex の基準線＝この事例で全件検出・偽陽性 0。結果は results.md へ追記すること。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
