"""
tests/test_assumptions.py
=========================
式の前提の宣言（`core/assumptions.py`）の**閉じた世界のゲート**（I-239 の 3）。

壊れた不変条件
--------------
「**式は、前提が成り立つ入力にだけ値を出す。成り立たなければ値を出さず、開示に戻す**」。
その前提を式の隣に書かせるため、`core/` の全モジュールに次のどちらか一方だけを求める。

- `ASSUMPTIONS`（式を持つ）＝前提ごとに扱い（入口で確かめる／刻印で名乗る／未対応）を言う
- `assumptions.FORMULA_FREE` に理由つきで載る（式を持たない）

新しいモジュールを足すと、どちらかを決めるまで赤＝決定点で前提を書かせる。

書けば緑の飾りにしない
----------------------
宣言が名指す口（関数・定数・刻印のキー）・テスト（ファイル・クラス・関数）・課題 ID が
**実在する**ことまで見る。検査の本体 `audit` は純関数で、変異（宣言を消す・名前を 1 字
変える・モジュールを足す）を下の `TestTheGateCatchesMutations` が毎回流す
＝ゲートが鳴らなくなる壊れ方も同じテストで落ちる。
"""

import ast
import importlib
import os
import re
import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import assumptions as A  # noqa: E402
from core import models  # noqa: E402
from conftest import structural_skip  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ISSUE_RE = re.compile(r"^[BI]-\d{3}$")
_ORIGIN_RE = re.compile(r"experiments/[\w./-]+\.py")
_LEDGER_HEADING_RE = re.compile(r"^#{2,4} .*?\b([BI]-\d{3}):", re.MULTILINE)


def core_module_names(root: Path = ROOT) -> list[str]:
    return sorted(p.stem for p in (root / "core").glob("*.py") if p.stem != "__init__")


def test_exists(spec: str, root: Path = ROOT) -> bool:
    """`tests/x.py::Class::func`（または `tests/x.py::func`）が実在するか（`ast` で読む）。"""
    path, *names = spec.split("::")
    file = root / path
    if not names or len(names) > 2 or not path.startswith("tests/") or not file.is_file():
        return False
    body = ast.parse(file.read_text(encoding="utf-8")).body
    for name in names:
        hit = next((n for n in body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
                    and n.name == name), None)
        if hit is None:
            return False
        body = hit.body
    return isinstance(hit, ast.FunctionDef)


test_exists.__test__ = False   # pytest に拾わせない（名前が test_ で始まる道具）


def audit(modules: dict, formula_free: dict, scope_keys, root: Path = ROOT) -> list[str]:
    """宣言の問題を文で並べる（純関数）。`modules` はモジュール名 → モジュール（か同じ形の物）。"""
    problems: list[str] = []
    for stale in sorted(set(formula_free) - set(modules)):
        problems.append(f"FORMULA_FREE に無いモジュールの名がある: {stale}")
    for mod_name, mod in sorted(modules.items()):
        declared = getattr(mod, "ASSUMPTIONS", None)
        if declared is not None and mod_name in formula_free:
            problems.append(f"{mod_name}: ASSUMPTIONS と FORMULA_FREE の両方にある")
            continue
        if declared is None:
            if mod_name not in formula_free:
                problems.append(f"{mod_name}: ASSUMPTIONS も FORMULA_FREE の行も無い（式を持つか決める）")
            elif not str(formula_free[mod_name]).strip():
                problems.append(f"{mod_name}: FORMULA_FREE の理由が空")
            continue
        if not declared:
            problems.append(f"{mod_name}: ASSUMPTIONS が空（式を持たないなら FORMULA_FREE へ）")
        seen: set[str] = set()
        for a in declared:
            problems += [f"{mod_name}.{a.name}: {p}" for p in _audit_one(mod, a, scope_keys, root)]
            if a.name in seen:
                problems.append(f"{mod_name}.{a.name}: 名前が重複している")
            seen.add(a.name)
    return problems


def _audit_one(mod, a: "A.Assumption", scope_keys, root: Path) -> list[str]:
    out: list[str] = []
    if not _NAME_RE.match(a.name):
        out.append("名前が英小文字と _ の形でない")
    if not a.description.strip():
        out.append("説明が空")
    if a.handling not in A.HANDLINGS:
        return out + [f"扱いが 3 値のどれでもない: {a.handling!r}"]
    if not isinstance(a.enforced_by, tuple):
        return out + ["enforced_by がタプルでない"]
    if a.handling == A.OPEN:
        if a.enforced_by:
            out.append("未対応（open）なのに enforced_by がある＝扱っているなら guard か disclosed")
        if not a.issue:
            out.append("未対応（open）なのに課題 ID が無い")
    else:
        if not a.enforced_by:
            out.append(f"{a.handling} なのに enforced_by が無い")
        if not a.test:
            out.append(f"{a.handling} なのに前提を外した入力を流すテストが無い")
    for ref in a.enforced_by:
        if ref.startswith(A.SCOPE_PREFIX):
            if a.handling != A.DISCLOSED:
                out.append(f"刻印のキーで確かめられるのは disclosed だけ: {ref}")
            elif ref[len(A.SCOPE_PREFIX):] not in scope_keys:
                out.append(f"刻印のキーが SCOPE_NOTE_ORDER に無い: {ref}")
        elif not hasattr(mod, ref):
            out.append(f"enforced_by の名がモジュールに無い: {ref}")
    if a.test and not test_exists(a.test, root):
        out.append(f"テストが実在しない: {a.test}")
    if a.issue and not _ISSUE_RE.match(a.issue):
        out.append(f"課題 ID の形でない: {a.issue}")
    if a.origin:
        paths = _ORIGIN_RE.findall(a.origin)
        if not paths:
            out.append("origin に experiments/ のファイルが無い")
        out += [f"origin のファイルが無い: {p}" for p in paths if not (root / p).is_file()]
    return out


def _real_modules() -> dict:
    return {name: importlib.import_module(f"core.{name}") for name in core_module_names()}


def _declared(modules: dict) -> list[tuple[str, "A.Assumption"]]:
    return [(n, a) for n, m in sorted(modules.items()) for a in getattr(m, "ASSUMPTIONS", ())]


# ============================================================
# 実データ
# ============================================================
class TestRealDeclarations:

    def test_every_core_module_declares_one_way_and_every_reference_exists(self):
        """🔴 `core/` の全モジュールが一方だけを持ち、宣言の名指す先が全部実在すること。"""
        problems = audit(_real_modules(), A.FORMULA_FREE, models.SCOPE_NOTE_ORDER)
        assert problems == [], "\n".join(problems)

    def test_the_formula_side_is_not_hollow(self):
        """式の側が空洞でない＝宣言した前提が 3 値とも実際に使われている。

        全モジュールを `FORMULA_FREE` に入れれば上の 1 本は緑になる＝それを止める。
        数は 2026-10-25 に置いた時点の下限（減らすなら理由を書いて下げる）。
        """
        declared = _declared(_real_modules())
        modules_with_formulas = {n for n, _ in declared}
        assert len(modules_with_formulas) >= 7, sorted(modules_with_formulas)
        assert {a.handling for _, a in declared} == set(A.HANDLINGS)

    def test_open_issues_are_in_the_ledger(self):
        """未対応の前提が指す課題 ID が台帳（本体かアーカイブ）に実在すること。"""
        files = [ROOT / "ISSUES.md", ROOT / "ISSUES_archive.md"]
        if not files[0].exists():
            pytest.skip(structural_skip("ISSUES.md は git-ignore（CI には存在しない）"))
        known = set()
        for f in files:
            if f.exists():
                known |= set(_LEDGER_HEADING_RE.findall(f.read_text(encoding="utf-8")))
        assert len(known) > 100, "台帳の見出しがほとんど読めない＝読み手が壊れている"
        missing = sorted({a.issue for _, a in _declared(_real_modules())
                          if a.issue and a.issue not in known})
        assert missing == [], f"台帳に無い課題 ID: {missing}"


# ============================================================
# 変異＝ゲートが鳴ること（宣言を消す・1 字変える・モジュールを足す）
# ============================================================
_SCOPE = ("rain_zeroed",)
_FREE = {"io_only": "I/O だけ"}
_GOOD_TEST = "tests/test_ground_reflection.py::test_reflection_near_end_returns_none"
_GOOD_CLASS_TEST = ("tests/test_model_preconditions.py::TestTwoRayEnvelopePreconditions"
                    "::test_no_envelope_on_a_blocked_path")


def _formula_module(*assumptions: "A.Assumption") -> types.SimpleNamespace:
    return types.SimpleNamespace(ASSUMPTIONS=tuple(assumptions), LIMIT=1.0, check=lambda: None)


def _good() -> list["A.Assumption"]:
    return [
        A.Assumption("guarded", "入口で確かめる", A.GUARD, ("LIMIT",), _GOOD_TEST),
        A.Assumption("stamped", "刻印で名乗る", A.DISCLOSED, ("scope:rain_zeroed", "check"), _GOOD_CLASS_TEST),
        A.Assumption("not_yet", "まだ扱っていない", A.OPEN, issue="B-338",
                     origin="`experiments/phase0_two_ray.py`＝ドローン"),
    ]


def _run(*assumptions, extra: dict | None = None, free: dict | None = None) -> list[str]:
    modules = {"formula": _formula_module(*assumptions), "io_only": types.SimpleNamespace()}
    modules.update(extra or {})
    return audit(modules, _FREE if free is None else free, _SCOPE)


class TestTheGateCatchesMutations:

    def test_the_unmutated_fixture_is_green(self):
        assert _run(*_good()) == []

    def test_removing_the_declaration_is_red(self):
        assert any("ASSUMPTIONS も FORMULA_FREE" in p
                   for p in audit({"formula": types.SimpleNamespace()}, {}, _SCOPE))

    def test_an_empty_declaration_is_red(self):
        assert any("ASSUMPTIONS が空" in p for p in _run())

    def test_adding_an_undecided_module_is_red(self):
        assert any("new_formula" in p for p in _run(*_good(), extra={"new_formula": types.SimpleNamespace()}))

    def test_declaring_both_ways_is_red(self):
        assert any("両方" in p for p in _run(*_good(), free={**_FREE, "formula": "嘘"}))

    def test_a_stale_formula_free_name_is_red(self):
        assert any("gone" in p for p in _run(*_good(), free={**_FREE, "gone": "消えた"}))

    def test_an_empty_reason_is_red(self):
        assert any("理由が空" in p for p in _run(*_good(), free={"io_only": " "}))

    @pytest.mark.parametrize("index, field, value, expected", [
        (0, "enforced_by", ("LIMTI",), "モジュールに無い"),
        (1, "enforced_by", ("scope:rain_zeroes",), "SCOPE_NOTE_ORDER に無い"),
        (0, "enforced_by", ("scope:rain_zeroed",), "disclosed だけ"),
        (0, "enforced_by", (), "enforced_by が無い"),
        (0, "enforced_by", "LIMIT", "タプルでない"),
        (0, "test", "", "テストが無い"),
        (0, "test", _GOOD_TEST + "x", "実在しない"),
        (1, "test", _GOOD_CLASS_TEST.replace("TestTwoRay", "TestTwoRey"), "実在しない"),
        (0, "test", "tests/test_no_such_file.py::test_x", "実在しない"),
        (2, "issue", "", "課題 ID が無い"),
        (2, "issue", "B-33", "課題 ID の形でない"),
        (2, "enforced_by", ("LIMIT",), "open"),
        (2, "origin", "`experiments/no_such_probe.py`＝場面", "origin のファイルが無い"),
        (2, "origin", "ドローン", "experiments/ のファイルが無い"),
        (0, "handling", "guarded", "3 値"),
        (0, "name", "Guarded", "英小文字"),
        (0, "description", " ", "説明が空"),
    ])
    def test_a_one_field_mutation_is_red(self, index, field, value, expected):
        mutated = _good()
        mutated[index] = replace(mutated[index], **{field: value})
        problems = _run(*mutated)
        assert any(expected in p for p in problems), problems

    def test_a_duplicate_name_is_red(self):
        good = _good()
        assert any("重複" in p for p in _run(*good, replace(good[2], issue="I-247")))
