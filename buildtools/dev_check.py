"""dev-check — 実装後の定型検証を 1 コマンドへ畳む（I-102 / 2.9）。

**なぜ束ねるか（＝理由が入れ替わった経緯）**
    開発環境ロードマップ §E には長らく「タスクランナー（余力があれば）」として
    在り、そこには *束ねる必然性はない* と書いてあった。当時の理由が **DX（薄い
    入口の便宜）** だったからで、だから着手されなかった。2026-08-14 に理由が
    **往復数の削減** へ入れ替わって 2.9 の項目になった＝検証 1 サイクルが
    「ruff → pyright → pytest → git diff --check」の **4 往復から 1 往復** になる。

**⚠️ 出力は要約であること**
    往復数と同じくらい効くのが結果の大きさで、**ツール結果は文脈に永住して以後の
    全往復の単価を上げる**。生の出力を連結して返すと、往復は減っても総入力は
    増えうる。⇒ 既定で返すのは **検査ごとに 1 行＋落ちた検査の抜粋だけ**。
    全文が要るときだけ `--full-output`。

**ruff と pyright はここでは回さない**
    どちらも既に `tests/test_repo_hygiene.py` の中から `pytest` が回している
    （B-047 / B-049 ＝「同じ検査をローカルの pytest からも回す」）。ここで別途
    呼ぶと **同じ検査を 2 回走らせて時間だけ倍**になり、しかも対象の指定が
    2 か所に割れる（片方だけ痩せても気づけない）。⇒ 静的検査は pytest に任せる。
    `bandit` だけは pytest に入っていない（CI にしか無い）ので、ここで回す。

🔑 **変更ファイルは「足す」ためだけに見る。「減らす」判断には使わない。**
    変更ファイルから関連テストを *推定して減らす* と、この repo が何度も踏んで
    いる「一度も鳴らないゲート」を作る側に回る（実際 ruff / pyright は
    `test_repo_hygiene.py` に同居しているので、選択を誤ると静的検査ごと消える）。
    範囲を狭めるのは **人／モデルが `--tests` で明示したときだけ**＝黙って外れる
    経路が存在しない。変更ファイルを見るのは追加ゲートを *足す* ためだけ。

使い方::

    # コミット前（本番）＝全件。CI と同じくカバレッジ門も掛かる
    & $env:RADIOSIM_PYTHON buildtools/dev_check.py

    # 実装の反復中＝範囲を明示する（test_repo_hygiene.py は常に足される）
    & $env:RADIOSIM_PYTHON buildtools/dev_check.py --tests tests/test_multihop.py
"""

from __future__ import annotations

import argparse
import re
import subprocess  # nosec B404 — 開発機で決定論的な検査器を呼ぶだけ（入力は自前）
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 範囲を絞ったときに **必ず足す** テスト。ruff / pyright のゲートがここに同居して
# いるので、これを外すと「静的検査を 1 つも回さないまま緑」になる。
ALWAYS_TESTS = "tests/test_repo_hygiene.py"

# 変更ファイルに応じて **足す** 追加ゲート（減らすことはしない）。
# 左＝変更パスの前方一致（リポジトリ相対・`/` 区切り）、右＝足すテスト。
# ⚠️ 右側が実在することは tests/test_dev_check.py が検査する（対応表が腐って
#    黙って外れるのを防ぐ＝手書きリストを置く以上、腐り検出を対で置く）。
EXTRA_GATES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("docs/", "README", "CHANGELOG.md"), ("tests/test_docs_consistency.py",)),
    (("lang/", "core/i18n.py"), (
        "tests/test_i18n_external.py",
        "tests/test_i18n_glossary.py",
        "tests/test_i18n_key_duplication.py",
        "tests/test_i18n_no_hardcoded_ui_text.py",
    )),
    (("requirements", "pyproject.toml"), ("tests/test_env_consistency.py",)),
    (("radiosim.spec",), ("tests/test_bundle_imports.py",)),
    (("buildtools/",), ("tests/test_bundle_imports.py", "tests/test_dev_check.py")),
    ((".claude/",), ("tests/test_claude_hooks.py",)),
]

# 落ちた検査から抜き出す行数の上限（要約の設計制約）。
EXCERPT_LINES = 12


@dataclass
class Result:
    name: str
    code: int
    output: str
    seconds: float

    @property
    def ok(self) -> bool:
        return self.code == 0


def _run(name: str, argv: list[str]) -> Result:
    started = time.monotonic()
    proc = subprocess.run(  # nosec B603 — argv は下の組み立てのみ（shell 不使用）
        argv, cwd=ROOT, capture_output=True, text=True, errors="replace",
    )
    return Result(name, proc.returncode, proc.stdout + proc.stderr,
                  time.monotonic() - started)


def changed_files() -> list[str]:
    """作業ツリーで触れているファイル（追跡分の差分＋未追跡）。"""
    out: list[str] = []
    for argv in (["git", "diff", "--name-only", "HEAD"],
                 ["git", "ls-files", "--others", "--exclude-standard"]):
        proc = subprocess.run(  # nosec B603,B607 — 固定の git 呼び出し
            argv, cwd=ROOT, capture_output=True, text=True, errors="replace",
        )
        if proc.returncode == 0:
            out += [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return sorted(set(out))


def extra_gates_for(paths: list[str]) -> list[str]:
    """変更ファイルに応じて **足す** テストを返す（減らさない）。"""
    found: list[str] = []
    for prefixes, tests in EXTRA_GATES:
        if any(p.startswith(prefixes) for p in paths):
            found += [t for t in tests if t not in found]
    return found


def pytest_argv(scope: list[str] | None) -> tuple[list[str], str]:
    """pytest の引数と、その範囲を人に説明する 1 行を返す。"""
    if not scope:
        # 全件のときだけカバレッジ門を掛ける＝CI と同じ門。
        # ⚠️ 部分実行に `--cov` を付けると fail_under=85 は必ず割れる（回して
        #    いない層が 0% で数えられる）＝「毎回鳴るゲート」になる。
        return ([sys.executable, "-m", "pytest", "--cov"], "全件（+ カバレッジ門）")

    targets = list(dict.fromkeys([*scope, ALWAYS_TESTS, *extra_gates_for(changed_files())]))
    return ([sys.executable, "-m", "pytest", *targets],
            f"{len(targets)} 対象（明示 {len(scope)} ＋ 常時・追加ゲート）")


def excerpt(res: Result, limit: int = EXCERPT_LINES) -> str:
    """落ちた検査から「何が落ちたか」だけを抜く。

    pytest は落ちた項目が `FAILED` / `ERROR` 行に出るので、それと締めの 1 行を
    拾う。ほかの道具は末尾（＝結論が出る場所）を取る。
    """
    lines = [ln for ln in res.output.splitlines() if ln.strip()]
    picked = [ln for ln in lines if re.match(r"^(FAILED|ERROR)\b", ln)]
    if picked:
        picked = picked[:limit - 1] + lines[-1:]
    else:
        picked = lines[-limit:]
    return "\n".join("    " + ln for ln in picked)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="実装後の定型検証を 1 コマンドで回し、結果を要約で返す。")
    ap.add_argument("--tests", nargs="+", metavar="EXPR", default=None,
                    help="pytest の範囲を明示する（省略時は全件）。"
                         f"{ALWAYS_TESTS} と追加ゲートは常に足される。")
    ap.add_argument("--full-output", action="store_true",
                    help="要約ではなく、落ちた検査の出力を全部出す。")
    args = ap.parse_args()

    pt_argv, scope_note = pytest_argv(args.tests)
    jobs: list[tuple[str, list[str]]] = [
        ("pytest", pt_argv),
        ("bandit", [sys.executable, "-m", "bandit", "-r", ".",
                    "-c", "pyproject.toml", "-ll", "-q"]),
        ("git diff --check", ["git", "diff", "--check", "HEAD"]),
    ]

    started = time.monotonic()
    # 独立な検査なので並行に回す＝逐次に積み上げた壁時計を、いちばん遅い 1 本まで
    # 縮める（束ねる意味の半分はここ）。
    #
    # ⚠️ **`ThreadPoolExecutor` は使わない**＝この repo の規約（ワーカーが
    # daemon=False で、tkinter が終了時に落ちる）。禁止の理由は GUI なので
    # CLI のここには当たらないが、**ゲートに例外を作るより実装を合わせるほうが
    # 安い**（`tests/test_smoke.py::test_thread_pool_executor_is_not_used`）。
    #
    # 並行に回しても **出力は宣言順**（`jobs` の順）＝結果を添字で受ける。走る
    # たびに順番が変わると、要約の diff が読めなくなる。
    slots: list[Result | None] = [None] * len(jobs)

    def worker(i: int, name: str, argv: list[str]) -> None:
        slots[i] = _run(name, argv)

    threads = [threading.Thread(target=worker, args=(i, *job), daemon=True)
               for i, job in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.monotonic() - started
    results = [r for r in slots if r is not None]

    print(f"dev-check — pytest の範囲: {scope_note}")
    for res in results:
        mark = "OK  " if res.ok else "NG  "
        print(f"  {mark}{res.name:<18} {res.seconds:6.1f}s")
    print(f"  合計 {total:.1f}s（並行実行）")

    failed = [r for r in results if not r.ok]
    for res in failed:
        print(f"\n--- {res.name}（exit={res.code}）---")
        print(res.output.rstrip() if args.full_output else excerpt(res))
    if failed and not args.full_output:
        print("\n（要約です。全文は --full-output、または当該コマンドを個別に実行）")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
