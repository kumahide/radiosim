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

import batch
import i18n
import version

ROOT = Path(__file__).resolve().parent.parent

# --- 正準リスト（実装＝真実）-------------------------------------------------
VIEW_MODULES = sorted(p.name for p in (ROOT / "views").glob("*.py") if p.name != "__init__.py")
TEST_FILES = sorted(p.name for p in (ROOT / "tests").glob("test_*.py"))
# 層構成図に必ず現れるべきコアモジュール（i18n/version/main は図の抽象度では
# 省く設計なので対象外＝図の意図に合わせた allowlist）。
CORE_ARCH_MODULES = [
    "models.py", "simulation.py", "config.py", "dem.py",
    "batch.py", "report.py", "report_map.py", "map_graphics.py", "coords.py",
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


# --- 5. バージョン文字列: version.py を単一ソースに各ドキュメントが追従するか --
#       リリース時に version.APP_VERSION を上げたら README の H1 と CHANGELOG の
#       見出しも更新することを強制する（最も影響の大きいリリース時ドリフト）。
#
#       プレリリース段階の扱い（2026-06-28・feedback_branch_strategy と整合）:
#       - alpha（`X.YaN`）＝開発着手直後。version.py だけ上げ、README/CHANGELOG は
#         まだ追従しない軽量段階 → このグループの照合は **skip**。
#       - beta/RC/正式（`X.YbN`/`X.YRCn`/`X.Y`）＝ドキュメント整備対象 → **base 版**
#         （`X.Y`）で照合する。プレリリース接尾辞まで README H1 に書かせない
#         （README は配布版の見え方＝base のみ）。
VERSION_READMES = [
    "README_ja.md", "README_en.md",
    "README_binary_ja.md", "README_binary_en.md",
]

_ALPHA_RE = re.compile(r"^\d+\.\d+a\d+$")
_BASE_VER_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)?)")


def _base_version() -> str:
    """APP_VERSION の base（a/b/RC 接尾辞を除いた X.Y[.Z]）。"""
    m = _BASE_VER_RE.match(version.APP_VERSION)
    return m.group(1) if m else version.APP_VERSION


@pytest.mark.parametrize("doc", VERSION_READMES)
def test_readme_h1_matches_app_version(doc):
    if _ALPHA_RE.match(version.APP_VERSION):
        pytest.skip(f"alpha 段階（{version.APP_VERSION}）は README 追従免除")
    expected = f"# RadioSim Pro {_base_version()}"
    first_line = _read(doc).splitlines()[0].strip()
    assert first_line == expected, (
        f"{doc}: H1 is {first_line!r}, expected {expected!r} "
        f"(version.APP_VERSION={version.APP_VERSION})"
    )


def test_changelog_has_current_version_section():
    if _ALPHA_RE.match(version.APP_VERSION):
        pytest.skip(f"alpha 段階（{version.APP_VERSION}）は CHANGELOG 追従免除")
    needle = f"## [{_base_version()}]"
    assert needle in _read("CHANGELOG.md"), (
        f"CHANGELOG.md has no '{needle}' section for the current "
        f"version.APP_VERSION={version.APP_VERSION}"
    )


# --- 6. dev README が参照する .py ファイルが実在するか -----------------------
#       散文中の `xxx.py` / `views/xxx.py` 参照が、改名・削除されたモジュールを
#       指していないかを検証する（バックティック内の .py トークンのみ対象）。
def _repo_py_files() -> tuple[set[str], set[str]]:
    """リポジトリ内の .py ファイルの (相対パス集合, ベース名集合) を返す。"""
    skip = {".venv", "build", "dist", "__pycache__", ".git", "tools", ".qa"}
    paths: set[str] = set()
    names: set[str] = set()
    for p in ROOT.rglob("*.py"):
        if skip & set(p.relative_to(ROOT).parts):
            continue
        paths.add(p.relative_to(ROOT).as_posix())
        names.add(p.name)
    return paths, names


@pytest.mark.parametrize("doc", DEV_READMES)
def test_dev_readme_py_references_exist(doc):
    paths, names = _repo_py_files()
    refs = set(re.findall(r"`([\w/]+\.py)`", _read(doc)))
    for ref in refs:
        # パス付き参照は相対パスで、ベース名のみの参照は名前集合で照合する
        # （テスト表は `test_models.py` のように tests/ 接頭辞なしで列挙される）。
        ok = (ref in paths) if "/" in ref else (ref in names)
        assert ok, f"{doc}: references non-existent Python file `{ref}`"


# --- 7. 機能スキーマの列挙: 新機能が全 README（バイナリ含む）に載っているか -----
#       背景（2026-07-02・[[feedback-promote-recurring-checks]]）: 2.3RC1 で開発者
#       README にだけ 2.3 機能（連続追加モード・per-row 利得）を反映し、**エンド
#       ユーザー向けのバイナリ README を前版水準のまま配布**した。当時これを止め
#       うる仕掛けは全て Tier-2（人手の手順・未配線ツール）でスルーされた。そこで
#       **列挙可能な部分（CSV 列・マップのモード名）を実装を単一ソースに全 README
#       で照合するブロッキングゲート＝Tier-0** へ昇格させる。振る舞いの散文（概念の
#       説明が十分か）は引き続き doc-review 助言に委ねる（機械化できる列挙のみ守る）。
ALL_READMES = ["README_ja.md", "README_en.md",
               "README_binary_ja.md", "README_binary_en.md"]


@pytest.mark.parametrize("doc", ALL_READMES)
def test_batch_csv_columns_listed(doc):
    """バッチ CSV の全列（batch.CSV_COLUMNS が単一ソース）が各 README の
    一括シミュレーション節に載っているか。gain_tx/gain_rx 追加のような
    スキーマ変更をドキュメント全系統へ反映し忘れるのを捕捉する。"""
    section = _section(_read(doc), ["一括シミュレーション", "Batch Mode"])
    for col in batch.CSV_COLUMNS:
        assert col in section, f"{doc}: batch CSV section is missing column '{col}'"


# doc の言語 → i18n の言語キー。バイナリ/開発者の両系統を言語ごとに照合する。
_MODE_READMES = [
    ("README_ja.md", "ja"), ("README_en.md", "en"),
    ("README_binary_ja.md", "ja"), ("README_binary_en.md", "en"),
]
_MODE_KEYS = ["map_mode_coords", "map_mode_append", "map_mode_cache"]


@pytest.mark.parametrize("doc,lang", _MODE_READMES)
def test_map_mode_labels_listed(doc, lang):
    """マップウィンドウの全モードのボタンラベル（i18n が単一ソース）が各 README に
    載っているか。連続追加モードの追加のようなモード新設を反映し忘れるのを捕捉する
    （README が実際の UI ボタン名を名乗ることも保証する）。"""
    text = _read(doc)
    for key in _MODE_KEYS:
        label = i18n._STRINGS[lang][key]
        assert label in text, f"{doc}: map mode label {label!r} ({key}) is not documented"


# --- CI ゲートの対象網羅 -----------------------------------------------------
# pyright の対象は CI ワークフローにモジュール名を**べた書き**している。新しい
# アプリモジュールを足したときリストへの追記を忘れると、そのファイルだけ静的検査を
# すり抜ける（2026-07-23 の 2.4RC1 移行時に `views/theme.py` で実際に発生）。
# ドキュメントと同じく「実装＝真実」で照合し、追記漏れを落とす。
CI_WORKFLOW = ".github/workflows/ci.yml"

# 型検査から意図的に外すもの（テスト・ビルド定義・ツール類）。
_PYRIGHT_EXEMPT = {"radiosim.spec"}


def test_ci_pyright_covers_all_app_modules():
    """CI の pyright 対象に、ルート直下と views/ の全モジュールが載っていること。"""
    workflow = _read(CI_WORKFLOW)
    listed = set(re.findall(r"[\w/]+\.py", workflow))
    app_modules = {p.name for p in ROOT.glob("*.py")} | {
        f"views/{name}" for name in VIEW_MODULES
    }
    missing = sorted(m for m in app_modules - listed if m not in _PYRIGHT_EXEMPT)
    assert not missing, (
        f"{CI_WORKFLOW} の pyright 対象に未登録のモジュール: {missing}。"
        "CI がこのファイルを型検査していない（追記すること）。"
    )
