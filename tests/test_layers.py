"""
tests/test_layers.py
====================
**層をディレクトリで表した以上、その約束を機械が守る**（2.7 スライス H・I-058）。

層は 3 つで、依存は一方向:

    views/  →  report/  →  core/

- `core/`   … 土台（計算・データ・設定）。画面も描画ライブラリも知らない。
- `report/` … 出力を作る層（実行エンジンと成果物の生成）。ヘッドレス。
- `views/`  … 画面（tkinter）。
- `main.py` … アプリの入口＝層ではない（起動して views を組み立てるだけ）。

🔑 **これが「層をディレクトリにした」ことの中身**＝ディレクトリを作っただけでは
規則にならない。以前の層の境界は手書きのリスト 3 本（カバレッジの `source`・
`_HEADLESS_SAFE`・`_HEADLESS_CORE`）としてしか存在せず、**実際に一度腐った**
（2.5a2 で `report.py` を 3 分割したときリストが追従せず、出力層がまるごと
カバレッジ計測の外に出た）。ここで見るのは**リストではなくコードの構造**なので、
モジュールを足しても割っても追従の作業が発生しない。

⚠️ **層の中の循環は禁じていない**（`report/` の中に相互参照はある）。禁じるのは
①**層をまたぐ逆流**と②**import 時に成立する循環**の 2 つだけ。前者は設計の崩れ、
後者は起動の失敗＝どちらも「気をつける」で防げない種類のもの。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 下から順。数字が大きい層は小さい層に依存してよい（逆は不可）。
_RANK = {"core": 0, "report": 1, "views": 2}
_LAYERS = tuple(_RANK)


def _modules() -> dict[str, Path]:
    """`core.models` → そのパス。入口（`main`）も含む（最上位として扱う）。"""
    mods = {
        f"{layer}.{p.stem}": p
        for layer in _LAYERS
        for p in sorted((ROOT / layer).glob("*.py"))
        if p.stem != "__init__"
    }
    mods["main"] = ROOT / "main.py"
    return mods


def _layer_of(module: str) -> str:
    return module.split(".")[0]


def _imported_layers(node: ast.AST) -> set[str]:
    """1 つの文が引いている層つきモジュール名（`core.models` の形）。"""
    found: set[str] = set()
    if isinstance(node, ast.ImportFrom) and node.module:
        parts = node.module.split(".")
        if parts[0] in _LAYERS:
            if len(parts) > 1:                      # from core.models import X
                found.add(".".join(parts[:2]))
            else:                                   # from core import models
                found |= {f"{parts[0]}.{a.name}" for a in node.names}
    elif isinstance(node, ast.Import):
        for a in node.names:                        # import core.models
            parts = a.name.split(".")
            if parts[0] in _LAYERS and len(parts) > 1:
                found.add(".".join(parts[:2]))
    return found


def _all_imports(path: Path) -> set[str]:
    """ファイル中のすべての層 import（関数の中の遅延 import・型注釈用も含む）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        found |= _imported_layers(node)
    return found


def _import_time_imports(path: Path) -> set[str]:
    """**import した瞬間に実行される** 層 import だけ。

    ⚠️ 関数の中の遅延 import は除く（循環を切るために意図的に中へ入れてある）。
    ⚠️ `if TYPE_CHECKING:` の中も除く（実行時は 1 行も走らない）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()

    def walk_body(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.If):
                if _is_type_checking(node.test):
                    continue                        # 型検査のときだけの世界
                walk_body(node.body)
                walk_body(node.orelse)
            elif isinstance(node, (ast.Try, ast.With)):
                walk_body(node.body)
            else:
                found.update(_imported_layers(node))

    walk_body(tree.body)
    return found


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


# ============================================================
# ①層をまたぐ逆流
# ============================================================
@pytest.mark.parametrize("module", sorted(_modules()))
def test_dependencies_flow_one_way(module):
    """`views/` → `report/` → `core/` の向きだけ。逆流は無いこと。

    ⚠️ **遅延 import も型注釈用の import も対象**＝「実行時には引いていない」は
    層の言い訳にならない。下の層が上の層の**名前を知っている**時点で、その 2 つは
    もう分けて取り出せない（[[project-radiosim-for-drone]] の「コアは 1 つ」が
    成り立たなくなる）。
    """
    path = _modules()[module]
    layer = _layer_of(module)
    if layer not in _RANK:                          # main.py は最上位＝何でも引ける
        return
    upward = sorted(
        dep for dep in _all_imports(path)
        if _RANK[_layer_of(dep)] > _RANK[layer]
    )
    assert not upward, (
        f"{module} が上の層を引いている: {upward}。"
        "依存は views → report → core の一方向で、2 つの層から使われるものは"
        "「下」へ置く（`shared/` のような箱は作らない）。"
    )


# ============================================================
# ②import 時に成立する循環
# ============================================================
def test_no_import_time_cycles():
    """import した瞬間に閉じる循環が無いこと（＝起動時の ImportError を防ぐ）。

    ⚠️ **層の中の相互参照そのものは禁じていない**＝`report/` には
    `multihop` ↔ `report_multihop` のような対がある。片方が遅延 import なので
    import 時には閉じない。ここが検出するのは**閉じてしまった**場合だけ。
    """
    graph = {name: _import_time_imports(p) for name, p in _modules().items()}
    color: dict[str, int] = {}
    cycles: list[str] = []

    def visit(node: str, stack: list[str]) -> None:
        color[node] = 1
        for dep in sorted(graph.get(node, ())):
            if dep not in graph:
                continue
            if color.get(dep) == 1:
                cycles.append(" → ".join(stack[stack.index(dep):] + [dep]))
            elif color.get(dep, 0) == 0:
                visit(dep, stack + [dep])
        color[node] = 2

    for node in graph:
        if color.get(node, 0) == 0:
            visit(node, [node])

    assert not cycles, (
        f"import 時に閉じる循環がある: {cycles}。"
        "どちらかを関数の中の遅延 import にするか、共有部分を下の層へ出すこと。"
    )


# ============================================================
# ③core は画面と作図を知らない
# ============================================================
# 🔑 **これが `core/` の存在理由そのもの**＝ヘッドレス層は Web/PWA での再利用を
# 見込んだ資産で、[[project-radiosim-for-drone]] は「同一リポの 2 アプリでコアは
# 1 つ」と決めている。tkinter や matplotlib が 1 本でも混ざれば、その瞬間に
# 「持ち出せるコア」ではなくなる。
#
# ⚠️ **PIL は禁じない**＝`core/dem.py` が DEM タイルを**復号する**のに使っており、
# 描画ではない。「GUI ツールキット」と「作図ライブラリ」だけを名指しする。
_CORE_FORBIDDEN = ("tkinter", "matplotlib", "sv_ttk", "tkintermapview")


def _external_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize(
    "path", sorted((ROOT / "core").glob("*.py")), ids=lambda p: p.name)
def test_core_knows_nothing_about_screens_or_plots(path):
    """`core/` 配下は GUI ツールキットも作図ライブラリも引かないこと。"""
    bad = sorted(_external_roots(path) & set(_CORE_FORBIDDEN))
    assert not bad, (
        f"core/{path.name} が {bad} を引いている。"
        "画面と作図は `report/`（成果物）か `views/`（画面）の仕事で、"
        "`core/` はそのどちらも知らないまま持ち出せる状態に保つ。"
    )


def test_the_layers_are_not_empty():
    """3 層とも実体があること（空なら上の検査は何も見ていない）。

    ⚠️ [[feedback-promote-recurring-checks]] の壊れ方①＝ディレクトリ名を変えた
    瞬間に glob が 0 件を返し、**全部緑のまま何も検査しなくなる**。
    """
    for layer in _LAYERS:
        mods = [p for p in (ROOT / layer).glob("*.py") if p.stem != "__init__"]
        assert mods, f"{layer}/ にモジュールが 1 本も無い（層の検査が空振りする）"
