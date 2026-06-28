"""
tests/test_docs_consistency.py
==============================
ドキュメントが実装からドリフトしていないかを機械的に検証する回帰テスト。

背景: 2.2 リリース前の手作業ドキュメント監査で、アーキテクチャ層構成図に
2.2 の新モジュール（map_window / report_map / map_graphics 等）が未反映なのを
見落とした。原因は「モジュール名がドキュメントのどこかに在るか」という**存在
ベース grep** で、図のような**独立した構造表現がそれ単体で陳腐化する**ケースを
取りこぼしたこと。

対策: コード（実ファイル・requirements）から正準リストを生成し、ドキュメントの
**各構造セクションを個別に**照合する。これにより「ファイルツリーには在るが層
構成図には無い」といったセクション固有のドリフトを検出する。

低ドリフト設計: ここで参照するのはモジュール/テストファイル/依存の集合のみ。
これらが変わるのはドキュメントも更新すべきときだけなので、無関係な変更で落ちない。
正確な件数は人手の節目チェックに委ね、ここでは「列挙の網羅」を守る。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# --- 正準リスト（実装＝真実）-------------------------------------------------
VIEW_MODULES = sorted(p.name for p in (ROOT / "views").glob("*.py") if p.name != "__init__.py")
TEST_FILES = sorted(p.name for p in (ROOT / "tests").glob("test_*.py"))
# 層構成図に必ず現れるべきコアモジュール（i18n/version/main は図の抽象度では
# 省く設計なので対象外＝図の意図に合わせた allowlist）。
CORE_ARCH_MODULES = [
    "models.py", "simulation.py", "infrastructure.py",
    "batch.py", "report_map.py", "map_graphics.py", "coords.py",
]

DEV_READMES = ["README_ja.md", "README_en.md"]
PIP_READMES = ["README.md", "README_ja.md", "README_en.md"]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _section(text: str, start_headers: list[str]) -> str:
    """`## ` 見出しで区切られた、最初に一致した見出し直後から次の `## ` 直前まで。"""
    lines = text.splitlines()
    starts = tuple(start_headers)
    out: list[str] = []
    capturing = False
    for ln in lines:
        if ln.startswith("## "):
            if capturing:
                break
            if any(h in ln for h in starts):
                capturing = True
                continue
        if capturing:
            out.append(ln)
    assert capturing, f"section {start_headers} not found"
    return "\n".join(out)


def _deps() -> list[str]:
    names = []
    for raw in _read("requirements.txt").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        names.append(re.split(r"[<>=!~]", s, maxsplit=1)[0].strip())
    return names


# --- 1. ファイル構成ツリー: 全 view/コア/テストを列挙しているか -------------
@pytest.mark.parametrize("doc", DEV_READMES)
def test_file_tree_lists_all_modules(doc):
    tree = _section(_read(doc), ["ファイル構成", "File Structure"])
    for name in VIEW_MODULES + CORE_ARCH_MODULES + TEST_FILES:
        assert name in tree, f"{doc}: file-structure tree is missing {name}"


# --- 2. アーキテクチャ層構成図: 全 view + コアモジュールを含むか -------------
#       （今回見落とした図そのものをガードする）
@pytest.mark.parametrize("doc", DEV_READMES)
def test_architecture_diagram_lists_all_modules(doc):
    arch = _section(_read(doc), ["アーキテクチャ", "Architecture"])
    for name in VIEW_MODULES + CORE_ARCH_MODULES:
        assert name in arch, f"{doc}: architecture layer diagram is missing {name}"


# --- 3. テスト表: 全テストファイルを列挙しているか ---------------------------
@pytest.mark.parametrize("doc", DEV_READMES)
def test_test_table_lists_all_test_files(doc):
    section = _section(_read(doc), ["テスト", "Testing"])
    for name in TEST_FILES:
        assert name in section, f"{doc}: test table is missing {name}"


# --- 4. pip install 行: 全依存を含むか --------------------------------------
@pytest.mark.parametrize("doc", PIP_READMES)
def test_pip_install_line_lists_all_dependencies(doc):
    text = _read(doc)
    pip_lines = [ln for ln in text.splitlines() if "pip install" in ln]
    assert pip_lines, f"{doc}: no pip install line found"
    blob = "\n".join(pip_lines).lower()
    for dep in _deps():
        assert dep.lower() in blob, f"{doc}: pip install line is missing dependency {dep}"
