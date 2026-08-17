"""
tests/test_dev_check.py
=======================
**検証ランナー自身のゲート**（`buildtools/dev_check.py`・I-102 / 2.9）。

**なぜこのテストが要るか**: dev-check は「実装後に何を検査するか」を決める道具
なので、**ここが黙って痩せると、以後の全ての検証が痩せたまま緑になる**。しかも
`EXTRA_GATES` は**手書きの対応表**＝この repo が I-058 で消したばかりの形なので、
手書きを置く以上は**腐り検出を対で置く**（[[feedback-promote-recurring-checks]]
「開示を書く仕事は『無いことの検査』を対で置く」と同じ型）。

守るのは 3 点:
  1. **対応表が実在するテストを指している**（ファイル名を変えたら赤くなる）
  2. **範囲を絞っても静的検査が落ちない**＝`ALWAYS_TESTS` が必ず足される
     （ruff / pyright は `test_repo_hygiene.py` に同居しているので、ここが
     外れると *静的検査を 1 つも回さないまま緑* になる）
  3. **部分実行に `--cov` を付けない**＝付けると fail_under を必ず割って
     「毎回鳴るゲート」になる（ゲートの壊れ方②）
"""

import importlib.util
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_SCRIPT = os.path.join(_ROOT, "buildtools", "dev_check.py")


def _load():
    """スクリプトをモジュールとして読み込む（`buildtools` はパッケージではない）。"""
    spec = importlib.util.spec_from_file_location("dev_check", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # ⚠️ **exec の前に sys.modules へ載せる**＝`@dataclass` は
    # `from __future__ import annotations` の文字列注釈を解決するのに
    # `sys.modules[cls.__module__]` を引く。載せずに exec すると
    # `AttributeError: 'NoneType' object has no attribute '__dict__'` で
    # 収集ごと落ちる（`check_bundle_imports.py` は dataclass を持たないので
    # 同じ読み込み方でも表に出ていなかった）。
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


dev_check = _load()


# ============================================================
# 1. 対応表の腐り検出
# ============================================================
def test_every_extra_gate_points_at_a_test_that_exists():
    """`EXTRA_GATES` の右側が実在すること。

    ⚠️ 手書きの対応表なので、テストを改名・分割した瞬間に**黙って何も足さなく
    なる**。それは「一度も鳴らないゲート」＝ここで赤くする。
    """
    missing = [
        t
        for _prefixes, tests in dev_check.EXTRA_GATES
        for t in tests
        if not os.path.exists(os.path.join(_ROOT, t))
    ]
    assert not missing, (
        "EXTRA_GATES が実在しないテストを指している（改名・分割の取り残し）: "
        + ", ".join(missing)
    )


def test_the_always_included_test_exists():
    """`ALWAYS_TESTS` が実在すること（ここが空振りすると 2. の保証が消える）。"""
    assert os.path.exists(os.path.join(_ROOT, dev_check.ALWAYS_TESTS)), (
        f"{dev_check.ALWAYS_TESTS} が無い＝範囲を絞ったとき ruff / pyright の"
        "ゲートが 1 つも回らなくなる"
    )


def test_extra_gate_prefixes_are_repo_relative_slash_paths():
    """前方一致の左側が `/` 区切りのリポジトリ相対であること。

    `git diff --name-only` は `/` 区切りで返す（Windows でも）。`\\` で書くと
    **どの変更にも一致せず、追加ゲートが永久に足されない**。
    """
    bad = [
        p
        for prefixes, _tests in dev_check.EXTRA_GATES
        for p in prefixes
        if "\\" in p or p.startswith("/")
    ]
    assert not bad, f"前方一致は `/` 区切りのリポジトリ相対で書く: {bad}"


# ============================================================
# 2. 範囲を絞っても静的検査は落ちない
# ============================================================
def test_narrowing_the_scope_still_runs_the_static_analysis_gate():
    argv, note = dev_check.pytest_argv(["tests/test_models.py"])
    assert dev_check.ALWAYS_TESTS in argv, (
        "範囲を絞ったとき test_repo_hygiene.py が足されていない＝"
        "ruff / pyright を 1 つも回さないまま緑になる"
    )
    assert "tests/test_models.py" in argv
    assert note


def test_the_scope_has_no_duplicates_even_if_asked_for_twice():
    """明示指定が `ALWAYS_TESTS` と重なっても二重に回さないこと。"""
    argv, _ = dev_check.pytest_argv([dev_check.ALWAYS_TESTS])
    assert argv.count(dev_check.ALWAYS_TESTS) == 1


# ============================================================
# 3. カバレッジ門は全件のときだけ
# ============================================================
def test_coverage_gate_runs_only_on_the_full_suite():
    full_argv, _ = dev_check.pytest_argv(None)
    assert "--cov" in full_argv, "全件では CI と同じカバレッジ門を掛ける"

    partial_argv, _ = dev_check.pytest_argv(["tests/test_models.py"])
    assert "--cov" not in partial_argv, (
        "部分実行に --cov を付けると fail_under を必ず割る＝毎回鳴るゲートになる"
    )


# ============================================================
# 4. 変更ファイルは「足す」だけに使う
# ============================================================
def test_changed_files_only_ever_add_gates():
    """どんな変更集合を渡しても、返るのは足すテストだけ（引くことはない）。"""
    assert dev_check.extra_gates_for([]) == []
    assert dev_check.extra_gates_for(["core/simulation.py"]) == []

    docs = dev_check.extra_gates_for(["docs/glossary.md"])
    assert "tests/test_docs_consistency.py" in docs

    both = dev_check.extra_gates_for(["docs/glossary.md", "lang/en.json"])
    assert set(docs) <= set(both), "変更が増えて足されるゲートが減ってはいけない"


# ============================================================
# 5. 出力は要約であること
# ============================================================
def test_the_excerpt_stays_small_and_names_what_failed():
    """⚠️ 結果は文脈に永住して以後の全往復の単価を上げる＝既定は要約。"""
    out = "\n".join(
        ["． " * 10]
        + [f"FAILED tests/test_x.py::test_{i} - AssertionError" for i in range(50)]
        + ["50 failed, 900 passed in 235.00s"]
    )
    res = dev_check.Result("pytest", 1, out, 1.0)
    lines = dev_check.excerpt(res).splitlines()

    assert len(lines) <= dev_check.EXCERPT_LINES, "抜粋が上限を超えている"
    assert any("FAILED" in ln for ln in lines), "何が落ちたかが分からない抜粋"
    assert any("50 failed" in ln for ln in lines), "締めの 1 行が落ちている"


def test_the_excerpt_falls_back_to_the_tail_for_other_tools():
    """`FAILED` 行を持たない道具（bandit 等）は末尾＝結論の出る場所を取る。"""
    out = "\n".join([f"line {i}" for i in range(100)] + ["Issue: [B602] subprocess"])
    res = dev_check.Result("bandit", 1, out, 1.0)
    lines = dev_check.excerpt(res).splitlines()

    assert len(lines) <= dev_check.EXCERPT_LINES
    assert "Issue: [B602] subprocess" in lines[-1]
