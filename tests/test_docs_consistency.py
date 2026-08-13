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

import ast
import re
from pathlib import Path

import pytest

from core import i18n
from core import version
from report import batch

ROOT = Path(__file__).resolve().parent.parent

# --- 正準リスト（実装＝真実）-------------------------------------------------
VIEW_MODULES = sorted(p.name for p in (ROOT / "views").glob("*.py") if p.name != "__init__.py")
TEST_FILES = sorted(p.name for p in (ROOT / "tests").glob("test_*.py"))
# 層構成図に必ず現れるべきコアモジュール（i18n/version/main は図の抽象度では
# 省く設計なので対象外＝図の意図に合わせた allowlist）。
CORE_ARCH_MODULES = [
    "models.py", "simulation.py", "config.py", "dem.py",
    "batch.py", "scenario.py",
    "report_common.py", "report_path.py", "report_summary.py",
    "report_scenario.py",
    "report_map.py", "map_graphics.py", "coords.py",
    "units.py",
]

DEV_DOCS = ["docs/developer_ja.md", "docs/developer_en.md"]
PIP_DOCS = ["README.md", "docs/developer_ja.md", "docs/developer_en.md"]


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
@pytest.mark.parametrize("doc", DEV_DOCS)
def test_file_tree_lists_all_modules(doc):
    tree = _section(_read(doc), ["ファイル構成", "File Structure"])
    for name in VIEW_MODULES + CORE_ARCH_MODULES + TEST_FILES:
        assert name in tree, f"{doc}: file-structure tree is missing {name}"


# --- 2. アーキテクチャ層構成図: 全 view + コアモジュールを含むか -------------
#       （今回見落とした図そのものをガードする）
@pytest.mark.parametrize("doc", DEV_DOCS)
def test_architecture_diagram_lists_all_modules(doc):
    arch = _section(_read(doc), ["アーキテクチャ", "Architecture"])
    for name in VIEW_MODULES + CORE_ARCH_MODULES:
        assert name in arch, f"{doc}: architecture layer diagram is missing {name}"


# --- 3. テスト表: 全テストファイルを列挙しているか ---------------------------
@pytest.mark.parametrize("doc", DEV_DOCS)
def test_test_table_lists_all_test_files(doc):
    section = _section(_read(doc), ["テスト", "Testing"])
    for name in TEST_FILES:
        assert name in section, f"{doc}: test table is missing {name}"


# --- 4. pip install 行: requirements.txt から入れているか ---------------------
#
# ⚠️ **このゲートは 2026-08-02 に向きが反転した**（2.6RC1・独立レビュー Codex 由来）。
# 旧版は「pip 行が全依存を**名前で列挙**していること」を要求していた＝**誤った運用を
# ゲートで固定していた**。`requirements.txt` は冒頭コメントのとおり「再現可能な CI と
# 配布ビルドのために固定する」方針なのに、README は名前だけで入れさせており、読者は
# **配布バイナリと違う版**を掴む（実際、レビュー環境では 4 依存がピンからずれて
# `test_env_consistency` が落ちた）。
#
# 🔑 **直し方の要点＝列挙そのものを禁じる**。名前を並べる限り「requirements.txt と
# README の二重管理」という*面*が残り、いつか必ずずれる（旧ゲートは名前の**欠落**しか
# 見ないので、版のずれは原理的に検出できなかった）。面を消せばゲートは 1 行で足りる。
@pytest.mark.parametrize("doc", PIP_DOCS)
def test_pip_install_line_installs_from_requirements(doc):
    text = _read(doc)
    pip_lines = [ln for ln in text.splitlines() if "pip install" in ln]
    assert pip_lines, f"{doc}: no pip install line found"
    blob = "\n".join(pip_lines)
    assert "-r requirements.txt" in blob, (
        f"{doc}: pip install line does not install from requirements.txt"
        "（版を固定した単一ソースから入れさせること）"
    )
    for dep in _deps():
        assert dep.lower() not in blob.lower(), (
            f"{doc}: pip install line still names {dep}"
            "（依存名を並べると版が固定されず requirements.txt と二重管理になる）"
        )


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
VERSION_DOCS = [
    "docs/developer_ja.md", "docs/developer_en.md",
    "docs/manual_ja.md", "docs/manual_en.md",
]

_ALPHA_RE = re.compile(r"^\d+\.\d+a\d+$")
_BASE_VER_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)?)")


def _base_version() -> str:
    """APP_VERSION の base（a/b/RC 接尾辞を除いた X.Y[.Z]）。"""
    m = _BASE_VER_RE.match(version.APP_VERSION)
    return m.group(1) if m else version.APP_VERSION


@pytest.mark.parametrize("doc", VERSION_DOCS)
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


@pytest.mark.parametrize("doc", DEV_DOCS)
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
ALL_DOCS = ["docs/developer_ja.md", "docs/developer_en.md",
               "docs/manual_ja.md", "docs/manual_en.md"]


@pytest.mark.parametrize("doc", ALL_DOCS)
def test_batch_csv_columns_listed(doc):
    """バッチ CSV の全列（batch.CSV_COLUMNS が単一ソース）が各 README の
    複数経路の節に載っているか。gain_tx/gain_rx 追加のような
    スキーマ変更をドキュメント全系統へ反映し忘れるのを捕捉する。"""
    section = _section(_read(doc), ["複数経路", "Multiple Paths"])
    for col in batch.CSV_COLUMNS:
        assert col in section, f"{doc}: batch CSV section is missing column '{col}'"


# doc の言語 → i18n の言語キー。バイナリ/開発者の両系統を言語ごとに照合する。
_MODE_DOCS = [
    ("docs/developer_ja.md", "ja"), ("docs/developer_en.md", "en"),
    ("docs/manual_ja.md", "ja"), ("docs/manual_en.md", "en"),
]
_MODE_KEYS = ["map_mode_coords", "map_mode_append", "map_mode_cache"]


_MENU_SECTION_HEADERS = ["メニュー", "Menus"]


def _menu_i18n_keys() -> list[str]:
    """`_build_menu` が実際に使っている i18n キーを**実装から**採る。

    ⚠️ ここに手書きの一覧を置かない。列挙で塞いだ穴は**メニュー項目を 1 つ足した
    瞬間に開く**（→ [[feedback-promote-recurring-checks]] の実証 10）。実装を単一
    ソースにしておけば、項目を足した人が README も直すまでこのゲートが赤いままになる。
    """
    # ⚠️ 2.7 スライス A でメニューは `views/launcher_menu.py`（`SimLauncher` の
    #    Mixin）へ移った。**ここが「見つからない」で落ちて教えた**＝場所を書いた
    #    ゲートは、移動そのものを検出できる形になっている。
    src = (ROOT / "views" / "launcher_menu.py").read_text(encoding="utf-8")
    m = re.search(r"\n    def _build_menu\b.*?(?=\n    def )", src, re.S)
    assert m, "views/launcher_menu.py: _build_menu が見つからない（このゲートが空振りする）"
    body = m.group(0)
    keys = set(re.findall(r'i18n\.t\("([a-z0-9_]+)"\)', body))
    # f-string 経由（例: `i18n.t(f"coord_fmt_{value}")`）＝接頭辞を持つキー族を
    # まるごと対象にする。ここも名前で拾うので、族に値が増えても追従する。
    for prefix in re.findall(r'i18n\.t\(f"([a-z0-9_]+)\{', body):
        keys |= {k for k in i18n._STRINGS["en"] if k.startswith(prefix)}
    return sorted(keys)


@pytest.mark.parametrize("doc,lang", _MODE_DOCS)
def test_menu_items_are_documented(doc, lang):
    """メニューバーの**全項目**が、各 README の「メニュー」節に載っているか。

    背景（2026-08-03・ユーザー指摘）: 節名が「UI 設定」だったころ、載っていたのは
    12 項目中 6 項目だけで、**ファイルメニュー 4 項目が丸ごと欠けていた**——
    2.6 の目玉であるプロジェクト機能の唯一の入口が、メニュー表に 1 行も無い状態。
    しかも節にはメニューでないもの（マップウィンドウ）が同居していた。
    「実装にメニューを足したが README を直さなかった」は放置すれば必ず再発する型
    なので、`test_map_mode_labels_listed` と同じ形の門にして止める。
    """
    section = _section(_read(doc), _MENU_SECTION_HEADERS)
    for key in _menu_i18n_keys():
        label = i18n._STRINGS[lang][key]
        assert label in section, (
            f"{doc}: メニュー項目 {label!r} ({key}) が「メニュー」節に載っていない"
        )


@pytest.mark.parametrize("doc,lang", _MODE_DOCS)
def test_map_mode_labels_listed(doc, lang):
    """マップウィンドウの全モードのボタンラベル（i18n が単一ソース）が各 README に
    載っているか。連続追加モードの追加のようなモード新設を反映し忘れるのを捕捉する
    （README が実際の UI ボタン名を名乗ることも保証する）。"""
    text = _read(doc)
    for key in _MODE_KEYS:
        label = i18n._STRINGS[lang][key]
        assert label in text, f"{doc}: map mode label {label!r} ({key}) is not documented"


# --- 文書が名乗るボタン名が、実装に在るか -------------------------------------
# 🔑 **上の 2 つと向きが逆**。`test_menu_items_are_documented` と
# `test_map_mode_labels_listed` は「実装のラベルが文書に載っているか」を見るので、
# **ボタンを改名して文書を直し忘れた**場合は素通りする（古い名前が文書に残り、
# 新しい名前がどこかに在れば緑）。2026-08-10 の 2.7RC1 実機確認でユーザーが
# 指摘した 2 件——`▶ 実行`（I-029 で `▶` を落とした）と`個別シミュレーション
# ボタン`（I-029 で「実行」へ改名）——は、まさにこの向きの穴だった。
#
# ⇒ **文書が「〜ボタン」と名指しした字は、i18n の実値に在ること。**
_BUTTON_MENTION = {
    # 「…」ボタン ／ **…** ボタン ／ 見出し末尾の「…ボタン」
    "ja": [r"「([^」]{1,30})」\s*ボタン",
           r"\*\*([^*]{1,30})\*\*\s*ボタン",
           r"^#{2,4}\s*\d*\.?\s*(.{1,30}?)ボタン\s*$"],
    # **…** button ／ "…" button ／ 見出しの `… Button`
    "en": [r"\*\*([^*]{1,40})\*\*\s+button",
           r'"([^"]{1,40})"\s+[Bb]utton',
           r"^#{2,4}\s*\d*\.?\s*(?:The\s+)?[\"“]?(.{1,40}?)[\"”]?\s+Button\s*$"],
}

#: 実装に無くても文書に書いてよいボタン名。**理由を必ず書く**（空で始まっている）。
_BUTTON_MENTION_ALLOWED: dict[str, str] = {}


def _norm_label(s: str) -> str:
    """比較用に空白と装飾を落とす（`↻ From Launcher` と `↻From Launcher` を同一視）。"""
    return s.strip().strip("*「」\"“”").replace(" ", "").replace("　", "")


def _button_keys() -> set[str]:
    """**画面が使っている** i18n キーを実装から採る（`views/` に現れるものすべて）。

    ⚠️ `i18n` の全値と照合してはいけない＝**成果物の語まで通ってしまう**。実例＝
    `個別シミュレーション` は `html_single_mode`（レポートがモードを名乗る出力契約
    の字）として今も i18n に在るので、全値と比べると「個別シミュレーションボタン」
    という**存在しないボタン名が緑になる**（2026-08-10 に実際そうなった）。
    ⇒ **`report/` からしか引かれないキーを入れない**ことがこのゲートの肝。

    ⚠️ **`ttk.Button(text=i18n.t("…"))` の形だけを採ってはいけない**（2026-08-10・
    Codex 独立レビュー P2）＝**実在するボタンの多くはキーが変数**で、
    `views/map_window.py` のモード 4 つ（`map_mode_*`）も
    `views/multihop.py` の `mh_add_point` / `mh_from_map` も**ループのタプル**から
    渡る。AST で `Button` だけを追うと、これらを「実装に無いボタン」と誤判定して
    **文書に正しいことを書けなくなる**（＝毎回鳴るゲート）。⇒ 拾うのは
    **`views/` に現れる文字列リテラルのうち i18n のキーであるもの**。ボタン以外の
    画面語も混ざるが、このゲートが問うのは「その字が画面に在るか」なので害はない。
    """
    known = set(i18n._STRINGS["en"])
    keys: set[str] = set()
    for path in (ROOT / "views").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value in known):
                keys.add(node.value)
    return keys


def _implemented_labels(lang: str) -> set[str]:
    strings = i18n._STRINGS[lang]
    return {_norm_label(strings[k]) for k in _button_keys() if k in strings}


def _button_mentions(text: str, lang: str) -> list[str]:
    """文書が「〜ボタン」と名指ししている字を、実装に無いものだけ返す。"""
    known = _implemented_labels(lang)
    missing = []
    for pattern in _BUTTON_MENTION[lang]:
        for m in re.finditer(pattern, text, re.MULTILINE):
            label = _norm_label(m.group(1))
            if label and label not in known and label not in _BUTTON_MENTION_ALLOWED:
                missing.append(m.group(1).strip())
    return missing


@pytest.mark.parametrize("doc,lang", _MODE_DOCS)
def test_documented_button_names_exist_in_the_app(doc, lang):
    """文書のボタン名が、i18n の実値として実在するか（改名の置き去りを捕捉）。

    ゲートの壊れ方 3 点（[[feedback-promote-recurring-checks]]）:
    - **一度も落ちない**: `test_the_button_scanner_catches_a_rename` が、改名前の
      文言（`▶ 実行`）を毎回スキャンして赤くなることを確かめる。
    - **毎回鳴る**: 例外は `_BUTTON_MENTION_ALLOWED`＝**0 件**で始めている。拾うのは
      「〜ボタン」と明示した箇所だけなので、地の文の言い換えでは鳴らない。
    - **間違ったものを要求している**: 要求は「**その字のボタンが在る**」であって
      文言の良し悪しではない。ja / en それぞれの実値と照合するので、片方の言語だけ
      直した状態も捕まる。
    """
    missing = _button_mentions(_read(doc), lang)
    assert not missing, (
        f"{doc}: 実装に無いボタン名を書いている（改名の置き去り）: {missing}"
    )


def test_the_button_scanner_catches_a_rename():
    """改名前の文言をスキャナが必ず捕まえること（変異検証）。"""
    assert _button_mentions("**▶ 実行** ボタンを押すと", "ja") == ["▶ 実行"]
    assert _button_mentions("### 2. 個別シミュレーションボタン", "ja") == ["個別シミュレーション"]
    assert _button_mentions("Click the **Relay Route** button.", "en") == ["Relay Route"]


def test_the_button_scanner_accepts_the_real_labels():
    """実在するラベルでは鳴らないこと（毎回鳴るゲートにしない）。"""
    assert _button_mentions("**実行** ボタンを押すと", "ja") == []
    assert _button_mentions("「↻ ランチャーから更新」ボタン", "ja") == []
    assert _button_mentions("Click the **Relay Path** button.", "en") == []


def test_the_button_scanner_accepts_labels_whose_key_is_a_variable():
    """キーがループ変数で渡るボタンも「実在」と認めること（Codex P2・変異検証）。

    `views/map_window.py` のモード 4 つと `views/multihop.py` の 2 つは、キーが
    タプルから渡るので `Button(text=i18n.t("…"))` の形では拾えない。**文書が
    これらを名指しできなくなる**のが誤検知の実害なので、両言語で確かめる。
    """
    for key in ("map_mode_append", "mh_add_point", "mh_from_map"):
        assert key in _button_keys(), f"{key} が画面のキーとして拾えていない"
        for lang, template in (("ja", "**{}** ボタン"), ("en", "the **{}** button")):
            label = i18n._STRINGS[lang][key]
            assert _button_mentions(template.format(label), lang) == []


# --- 文書が窓を名指しする「枠」に、実装の窓名が入っているか --------------------
# 🔑 **上のボタン名ゲートより広い穴を、前置詞ではなく“位置”で塞ぐ**（I-079）。
# `test_documented_button_names_exist_in_the_app` が見るのは「〜ボタン」と名指し
# した形だけなので、**見出し・目次・機能表で窓を古い名前で呼んでいても緑**になる。
# 2.7 の正式ビルド後に同梱 README を grep して見つかった `Relay Route`（実装は
# `Relay Path`）は、まさにその位置に居た（`## Usage — Relay Route` と目次）。
#
# ⚠️ **素朴な拡張＝「古そうな語を探す」は毎回鳴る**（2026-08-11 に実測した）。
# 窓名の頭で全文を走査すると、正当な散文が大量に落ちる——ja は `中継点`（中継
# *経路*ではない実在の概念）`複数障害` `条件を…` で 18 件、en は `Relay points`
# `Condition explorer` が鳴った。**語の見た目からは、窓を名指ししているのか
# ただ日本語/英語を書いているのかを区別できない。**
#
# ⇒ **枠で位置を決める。** 4 文書とも窓の名前が出るのは次の 2 か所だけで、
#    どちらも「窓名を知らなくても」構文から特定できる:
#      ① 見出し `## 使い方 — <窓名>` / `## Usage — <Window>`（地図は `## 地図`）
#      ② 目次のリンクの字
#    枠の中身だけを検査するので、散文には**原理的に触れない**。
_WINDOW_TITLE_KEYS = sorted(k for k in i18n._STRINGS["en"] if k.endswith("_window_title"))

#: 「使い方 — X」の枠。X は窓名か、ランチャー（`html_single_mode`）。
_USAGE_FRAME = re.compile(r"^(?:使い方|Usage)\s*[—–-]\s*(.+?)\s*$")


def _headings(text: str) -> list[str]:
    """見出しの字（`#` を落としたもの）。"""
    return [re.sub(r"^#+\s*", "", s) for line in text.splitlines()
            if (s := line.strip()).startswith("#")]


def _toc_labels(text: str) -> list[str]:
    """目次のリンクの字（`1. [〜](#anchor)` の `〜`）。"""
    return re.findall(r"^\s*\d+\.\s*\[([^\]]+)\]\(#", text, re.MULTILINE)


def _frame_subjects(text: str) -> list[str]:
    """「使い方 — X」の枠に入っている X を、見出しと目次から全部集める。

    末尾の括弧書きは落とす（`条件探索（比較 / スイープ）` → `条件探索`）＝括弧の
    中は窓名ではなく**その節が何を扱うかの補足**なので、実装と照合する対象でない。
    """
    subjects = []
    for s in _headings(text) + _toc_labels(text):
        if m := _USAGE_FRAME.match(s):
            subjects.append(re.sub(r"\s*[（(].*[）)]\s*$", "", m.group(1)))
    return subjects


@pytest.mark.parametrize("doc,lang", _MODE_DOCS)
def test_window_titles_are_named_in_headings_and_toc(doc, lang):
    """**前向き**＝窓の名前（i18n が単一ソース）が、見出しと目次に実値で在ること。

    窓を改名して文書を直し忘れると、枠は古い名前で埋まったまま**新しい名前が
    どこにも無い**状態になるので、ここが赤くなる。
    """
    text = _read(doc)
    heads, toc = _headings(text), _toc_labels(text)
    for key in _WINDOW_TITLE_KEYS:
        title = i18n._STRINGS[lang][key]
        assert any(title in h for h in heads), (
            f"{doc}: 窓 {key} の名前 {title!r} を名乗る見出しが無い（改名の置き去り）"
        )
        assert any(title in t for t in toc), (
            f"{doc}: 窓 {key} の名前 {title!r} が目次に無い（改名の置き去り）"
        )


@pytest.mark.parametrize("doc,lang", _MODE_DOCS)
def test_usage_sections_name_something_that_exists(doc, lang):
    """**後ろ向き**＝「使い方 — X」の X が、画面に実在する字であること。

    前向きの検査だけでは、**古い名前が別の見出しに残っていても緑**になる（新しい
    名前がどこか 1 か所に在れば通ってしまう）。だから枠の中身そのものも見る。
    `Usage — Relay Route` は、`Relay Route` という i18n 値が無いので赤くなる。

    ゲートの壊れ方 3 点（[[feedback-promote-recurring-checks]]）:
    - **一度も落ちない**: `test_the_usage_frame_catches_a_stale_window_name` が
      改名前の字（`Relay Route` / `中継ルート`）で赤くなることを確かめる。
    - **毎回鳴る**: 例外表は**持たない**。枠の外（散文・小見出し・表のセル）は
      一切見ないので、`中継点` や `Relay points` では鳴りようがない。
    - **間違ったものを要求している**: 要求は「**その字が画面に在る**」だけで、
      名前の良し悪しでも、節の並び順でもない。
    """
    known = set(i18n._STRINGS[lang].values())
    for subject in _frame_subjects(_read(doc)):
        assert subject in known, (
            f"{doc}: 使い方の節が名乗る {subject!r} は画面に無い字（改名の置き去り）"
        )


def test_the_usage_frame_catches_a_stale_window_name():
    """改名前の窓名を枠が必ず捕まえること（変異検証）。"""
    for lang, stale in (("en", "Relay Route"), ("ja", "中継ルート")):
        head = "## Usage — " if lang == "en" else "## 使い方 — "
        assert _frame_subjects(head + stale) == [stale]
        assert stale not in set(i18n._STRINGS[lang].values())


def test_the_usage_frame_ignores_prose_that_merely_starts_alike():
    """枠の外は見ないこと（毎回鳴るゲートにしない・実測で鳴った字で確かめる）。

    どれも 2026-08-11 に実文書へ在った正当な散文で、窓名の頭で全文走査すると
    落ちていたもの。**枠を見る限り 1 件も拾わない。**
    """
    for prose in ("中継点は最大 7 つで、区間ごとに計算します。",
                  "| **複数障害** | 条件を変えた結果を並べる |",
                  "Up to 7 relay points, and a Condition explorer run.",
                  "### 設計思想 — シングルで詰め、複数経路で確定する"):
        assert _frame_subjects(prose) == []


# --- CI ゲートの対象網羅 -----------------------------------------------------
# 🔑 **この照合は 2.7 スライス H（I-058）で不要になった。**
# かつて pyright の対象は CI ワークフローにモジュール名を 43 件**べた書き**して
# おり、追記を忘れるとそのファイルだけ静的検査をすり抜けた（2026-07-23 の
# 2.4RC1 移行時に `views/theme.py` で実際に発生）。だから「実装＝真実」で照合する
# テストが要った。**いまは対象が層のディレクトリ（`core report views`）なので、
# 新しいモジュールは置いた時点で対象**＝照合するリストがそもそも無い。
# ⇒ **残すのは「層が全部載っているか」だけ**（それは
#    tests/test_repo_hygiene.py::test_pyright_finds_no_type_errors_in_app_modules
#    が ci.yml を読んで検査している）。ここには**直下に .py を置き去りにしていない
#    こと**＝層に属さないアプリモジュールが生まれていないことを置く。
CI_WORKFLOW = ".github/workflows/ci.yml"

# 直下に置いてよい `.py`（層に属さないもの）とその理由。
_ROOT_PY_ALLOWED = {
    "main.py": "アプリの入口＝層ではない（起動して views を組み立てるだけ）",
}


def test_no_app_module_sits_outside_a_layer():
    """アプリのモジュールが `core/` `report/` `views/` のどれかに属すること。

    直下へ 1 本置くだけで、その版のうちは誰も困らない。困るのは次の分割のとき
    で、**どの層のものか誰にも分からないまま参照だけ増える**（I-058 が消した
    「フラットな直下は共有コアを表現できない」状態そのもの）。
    """
    stray = sorted(
        p.name for p in ROOT.glob("*.py") if p.name not in _ROOT_PY_ALLOWED
    )
    assert not stray, (
        f"層に属さないモジュールが直下にある: {stray}。"
        "`core/`（土台）・`report/`（出力）・`views/`（画面）のどれかへ置くこと"
        "（依存は views → report → core の一方向）。"
    )


# --- 9. ドキュメント内リンクの実在（2026-07-25 追加） ---------------------------
#       背景: 課題台帳の末尾に `[design_philosophy]: memory/feedback_design_philosophy.md`
#       という参照定義があり、**memory はリポジトリ外にあるので常にリンク切れ**だった
#       （全 I- 項目の「設計方針との整合」欄が参照するラベルなので影響は広い）。人手の
#       doc 点検で見つけるまで誰も踏まなかった＝機械化できる面なのに無ガードだった。
#       同種のドリフト源（ファイル改名でリンクが腐る）はこれから増える見込みなので
#       Tier-0 に落とす。**リンク切れの検出だけを見る**（リンクの妥当性＝指し先が適切か
#       は散文の判断なので doc-review 助言に委ねる）。
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")          # インラインリンク
_REFDEF_RE = re.compile(r"^\[([^\]]+)\]:\s*(\S+)")          # 参照定義
_FENCE_RE = re.compile(r"^\s*(```|~~~)")

# 非追跡ゆえ CI には存在しないが、ローカルでは検査したいドキュメント。
_LOCAL_ONLY_DOCS = ["ISSUES.md", "issue_evidence/README.md"]
_LINK_DOCS = ["README.md", *ALL_DOCS, "CHANGELOG.md", *_LOCAL_ONLY_DOCS]


def _iter_links(text: str):
    """(行番号, ラベル, ターゲット) を返す。コードフェンス内は対象外。"""
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        d = _REFDEF_RE.match(line)
        if d:
            yield lineno, d.group(1), d.group(2)
        for m in _LINK_RE.finditer(line):
            yield lineno, m.group(1), m.group(2)


@pytest.mark.parametrize("doc", _LINK_DOCS)
def test_doc_links_point_to_existing_files(doc):
    """ドキュメントの相対リンク・参照定義の指し先が実在すること。"""
    path = ROOT / doc
    if not path.exists():
        pytest.skip(f"{doc} は git-ignore（CI には存在しない）")
    broken = []
    for lineno, label, target in _iter_links(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "data:", "#")):
            continue
        rel = target.split("#", 1)[0]          # 行アンカー（#L42）は落として実体を見る
        if not rel:
            continue
        if not (path.parent / rel).exists():
            broken.append(f"L{lineno} [{label}] -> {target}")
    assert not broken, f"{doc}: リンク切れ {len(broken)} 件\n  " + "\n  ".join(broken)


# --- 10. カバレッジ対象の網羅（2.5a3 追加・2.7 スライス H で作り直し） --------
#       背景: `[tool.coverage.run] source` はモジュール名のべた書きで、**分割・改名で
#       黙って死角ができる**。実際 2.5a2 で `report.py` を report_common/path/summary へ
#       割ったとき source に "report" が残り、出力層がまるごと計測外のまま
#       カバレッジ 95% が報告されていた（気づいたのは 2.5a3 で新モジュールを足した時）。
#
#       🔑 **I-058 で source が 2 要素（`["core", "report"]`）になり、19 件の名前
#       列挙が消えた**＝「割ったのに追記を忘れる」という壊れ方が構造的に起きない。
#       ⇒ 残す検査は「**ヘッドレス層のディレクトリが両方載っているか**」だけ。
#       ⚠️ この検査を消さない理由＝`source` を空や `["."]` に書き換えると、GUI 層を
#       含めた薄いカバレッジで `fail_under` を満たしてしまう（数字は緑・中身は死角）。


def test_coverage_source_is_the_headless_layers():
    """カバレッジ計測の対象がヘッドレス層のディレクトリ 2 つであること。"""
    text = _read("pyproject.toml")
    block = re.search(r"\[tool\.coverage\.run\].*?source\s*=\s*\[(.*?)\]",
                      text, re.S)
    assert block, "pyproject.toml に [tool.coverage.run] source が無い"
    listed = set(re.findall(r'"([\w_./]+)"', block.group(1)))
    assert listed == {"core", "report"}, (
        f"カバレッジ計測の対象がヘッドレス層と一致しない: {sorted(listed)}。"
        "層はディレクトリで表す（`source = [\"core\", \"report\"]`）。"
    )


# --- 11. 公開文書に非公開の課題 ID を書かない（2.6 追加） ---------------------
#       背景（2026-08-03・ユーザー指摘）: メニュー節を書き直したとき、README に
#       `I-030` `B-021` `B-025②` `I-060` の 4 種 6 箇所を書き込んでしまった。
#       **ISSUES.md は `.gitignore` 対象**（未修正の脆弱性・実機スクショを含むため
#       公開できない）＝**公開リポジトリの読者には解決不能な参照**になる。
#
#       ⚠️ この判断は ISSUES.md の冒頭に既にルールとして書いてあった——
#       「逆向き（GitHub 側に B-021 を書く）はしない…CHANGELOG から課題 ID を
#       落としているのと同じ理由」。**CHANGELOG が守っている規則を README で破った**
#       ＝散文の規則は、書いた本人でも次の作業で踏む（→ [[feedback-promote-recurring-checks]]）。
#
#       中身（なぜそうなっているか）は書いてよい。**落とすのは ID だけ**で、理由は
#       地の文へ展開する。
#
#       対象は「追跡されている = 公開される」ファイル。この性質そのものが判定基準
#       なので、ここでも手書きの一覧を持たず git に問い合わせる。
#       ⚠️ **語境界に `\b` を使わない**（2026-08-13・B-078 のクラス点検）＝Python の
#       `\b` は *Unicode の*単語文字で境界を決めるので、`クラスB-078` や `B-078を直す`
#       のように**日本語へ直に接した ID が素通りする**。対象は日本語の文書なので、
#       この形はむしろ普通に書かれる。⇒ 境界は **ASCII の単語文字だけ**で見る。
_ISSUE_ID_RE = re.compile(r"(?<![A-Za-z0-9_])[BI]-\d{3}(?![A-Za-z0-9_])")


def _tracked_markdown() -> list[str]:
    """追跡下（＝公開される）の Markdown。git が無い環境では skip。"""
    import subprocess
    try:
        out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover
        pytest.skip("git が使えない環境")
    return [p for p in out.splitlines() if p]


def test_public_docs_do_not_cite_issue_ids():
    """公開される Markdown に ISSUES.md の課題 ID（B-000 / I-000 形式）が無いこと。"""
    hits = []
    for rel in _tracked_markdown():
        for lineno, line in enumerate(_read(rel).splitlines(), 1):
            for m in _ISSUE_ID_RE.finditer(line):
                hits.append(f"{rel}:{lineno} {m.group(0)}")
    assert not hits, (
        "公開文書に非公開の課題 ID がある（ISSUES.md は .gitignore 対象なので"
        "読者には辿れない）。ID を消し、理由を地の文で書くこと:\n  "
        + "\n  ".join(hits)
    )


# ============================================================
# 同梱マニュアルが参照する画像は、exe にも入っていること（I-017）
# ============================================================
# **配布物の文書だけ画像が無い**という壊れ方は 2.3RC1 で実際に踏んでいる
# （I-012＝同梱ドキュメントの存在自体を忘れた）。今度は画像で同じ穴が開きうる：
# `docs/manual_*.md` に `![](images/…)` と書いても、`radiosim.spec` の
# `datas` に足し忘れれば **exe の中だけ絵が出ない**。⇒ 参照と同梱を突き合わせる。
#
# 🔑 **参照はその文書からの相対**（Markdown の意味）なので、突き合わせる前に
# **リポジトリ相対へ畳む**（`docs/manual_ja.md` の `../logo.png` = `logo.png`）。
# ⚠️ 畳まずに文字列のまま比べると、`images/shot_map.png` が spec の
# `docs/images/shot_map.png` と一致せず**常に赤**になる（＝毎回鳴るゲート）。
BUNDLED_MANUALS = ["docs/manual_ja.md", "docs/manual_en.md"]

_IMG_MD_RE = re.compile(r'!\[[^\]]*\]\(([^)]+\.(?:png|jpg|jpeg|gif))\)')
_IMG_HTML_RE = re.compile(r'<img[^>]+src="([^"]+\.(?:png|jpg|jpeg|gif))"')


def _image_refs(doc: str) -> set[str]:
    """`doc` の画像参照を**リポジトリ相対のパス**として返す。"""
    import posixpath
    text = (ROOT / doc).read_text(encoding="utf-8")
    here = posixpath.dirname(doc)
    return {posixpath.normpath(posixpath.join(here, m.group(1)))
            for r in (_IMG_MD_RE, _IMG_HTML_RE) for m in r.finditer(text)}


def _bundled_paths() -> set[str]:
    """`radiosim.spec` の `datas` が同梱するソース側のパス。"""
    spec = (ROOT / "radiosim.spec").read_text(encoding="utf-8")
    return {m.group(1).replace("\\", "/")
            for m in re.finditer(r'\(\s*"([^"]+\.(?:png|md)|LICENSE)"\s*,\s*"[^"]*"\s*\)', spec)}


@pytest.mark.parametrize("doc", BUNDLED_MANUALS)
def test_images_in_the_bundled_manual_are_also_bundled(doc):
    refs = _image_refs(doc)
    assert refs, f"{doc}: 画像参照が 1 つも無い（この検査が空振りしている）"
    bundled = _bundled_paths()
    missing = sorted(r for r in refs if r not in bundled)
    assert not missing, (
        f"{doc} が参照する画像が radiosim.spec の datas に無い"
        f"（exe の中だけ絵が出ない）: {missing}"
    )


@pytest.mark.parametrize("doc", BUNDLED_MANUALS)
def test_images_in_the_bundled_manual_exist_on_disk(doc):
    for ref in sorted(_image_refs(doc)):
        assert (ROOT / ref).exists(), f"{doc}: 参照先が無い {ref}"


def test_the_manual_viewer_inlines_any_bundled_image_not_just_the_logo(tmp_path):
    """描画時の data URI 化が**画像 1 枚の決め打ちでない**こと。

    Tier 1 の表示は一時ディレクトリへ HTML を書くので、相対パスの画像は必ず壊れる。
    `logo.png` だけを畳む実装のままスクショを足すと、**新しい画像だけ黙って壊れる**。

    ⚠️ **同梱物の外を読まないこと**は、**実在するファイル**で確かめる。存在しない
    パスで試すと「無いから素通し」で緑になり、防御を外しても落ちない（最初の実装が
    実際にそうなっていた＝理由なく通るテスト）。
    """
    from views.launcher_menu import inline_local_images

    png = (ROOT / "logo.png").read_bytes()
    base = tmp_path / "bundle"
    (base / "docs" / "images").mkdir(parents=True)
    (base / "logo.png").write_bytes(png)
    (base / "docs" / "images" / "shot.png").write_bytes(png)
    (tmp_path / "outside.png").write_bytes(png)          # **実在する**同梱物の外

    body = ('<img src="logo.png">'
            '<img src="docs/images/shot.png">'
            '<img src="https://example.com/x.png">'
            '<img src="../outside.png">')
    out = inline_local_images(body, str(base))

    assert out.count("data:image/png;base64,") == 2, "同梱画像が 2 枚とも畳まれていない"
    assert 'src="https://example.com/x.png"' in out, "外部 URL を触っている"
    assert 'src="../outside.png"' in out, "同梱物の外のファイルを読んでいる"


def test_the_manual_viewer_resolves_images_from_the_document_not_the_root(tmp_path):
    """画像の解決基準が**その文書のあるディレクトリ**であること（`docs/` 移設）。

    マニュアルは `docs/manual_*.md` にあり、参照は Markdown の意味どおり
    **その文書からの相対**（`images/…`・`../logo.png`）。⇒ 根から解決すると
    **アプリでだけ絵が出ない**（GitHub では出るので気づきにくい）。

    ⚠️ **境界は根のまま**＝`../logo.png` は同梱物の中なので畳み、そこからさらに
    外へ出るものは畳まない。**この 2 つを 1 つのテストで見る**＝基準だけ動かして
    境界も一緒に動かす直し方（＝任意のファイルを読める口）を落とすため。
    """
    from views.launcher_menu import inline_local_images

    png = (ROOT / "logo.png").read_bytes()
    base = tmp_path / "bundle"
    (base / "docs" / "images").mkdir(parents=True)
    (base / "logo.png").write_bytes(png)
    (base / "docs" / "images" / "shot.png").write_bytes(png)
    (tmp_path / "outside.png").write_bytes(png)          # **実在する**同梱物の外

    body = ('<img src="images/shot.png">'                # docs/ からの相対
            '<img src="../logo.png">'                    # 根へ戻る＝同梱物の中
            '<img src="../../outside.png">')             # 根の外＝触らない
    out = inline_local_images(body, str(base), str(base / "docs"))

    assert out.count("data:image/png;base64,") == 2, (
        "文書のあるディレクトリを基準に解決していない"
        "（`images/…` と `../logo.png` の両方が畳まれること）"
    )
    assert 'src="../../outside.png"' in out, "同梱物の外のファイルを読んでいる"


# ------------------------------------------------------------
# 文書の中のリンク（B-081）
# ------------------------------------------------------------
# ビューアは一時ディレクトリへ HTML を書き出す＝**相対リンクは必ず死ぬ**。画像は
# data URI へ畳んでいたのに `<a href>` は素通しで、2026-08-13 に利用者から
# 「リンクが開けません」と報告された。⇒ 辿れる文書を一緒に書き出して繋ぐ。
_MD_LINK_RE = re.compile(r'(?<!!)\[[^\]]*\]\(([^)#][^)]*\.md)(?:#[^)]*)?\)')


def _doc_link_refs(doc: str) -> set[str]:
    """`doc` が指すローカル `.md` を**リポジトリ相対のパス**として返す。"""
    import posixpath
    text = (ROOT / doc).read_text(encoding="utf-8")
    here = posixpath.dirname(doc)
    return {posixpath.normpath(posixpath.join(here, m.group(1)))
            for m in _MD_LINK_RE.finditer(text)}


@pytest.mark.parametrize("doc", BUNDLED_MANUALS)
def test_documents_linked_from_the_bundled_manual_are_also_bundled(doc):
    """マニュアルから**辿れる文書も同梱**されていること（画像と同じ規則）。

    ビューアは辿れる `.md` を一緒に書き出してリンクを繋ぐので、同梱されていない
    文書へのリンクは**配布版でだけ黙って消える**（開発機では開けるので気づかない）。
    """
    refs = _doc_link_refs(doc)
    assert refs, f"{doc}: 他の文書へのリンクが 1 つも無い（この検査が空振りしている）"
    missing = sorted(r for r in refs if r not in _bundled_paths())
    assert not missing, (
        f"{doc} が指す文書が radiosim.spec の datas に無い"
        f"（exe でだけリンクが消える）: {missing}"
    )


def test_the_manual_viewer_rewrites_links_to_places_that_can_be_opened(tmp_path):
    """`<a href>` の 3 分岐（一緒に書き出した文書 / 同梱ファイル / 開けない）。

    ⚠️ **開けないものは「リンクを外して字だけ残す」**＝押せるのに何も起きない
    リンクを作らない。外部 URL とページ内アンカーには触らない。
    """
    from views.launcher_menu import rewrite_local_links

    base = tmp_path / "bundle"
    (base / "docs").mkdir(parents=True)
    (base / "docs" / "manual.md").write_text("x", encoding="utf-8")
    (base / "LICENSE").write_text("MIT", encoding="utf-8")
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")   # **実在する**外
    names = {str(base / "docs" / "manual.md"): "docs_manual.html"}

    body = ('<a href="manual.md#使い方">M</a>'
            '<a href="../LICENSE">L</a>'
            '<a href="../../outside.md">O</a>'
            '<a href="missing.md">X</a>'
            '<a href="https://example.com">W</a>'
            '<a href="#概要">A</a>')
    out = rewrite_local_links(body, str(base), str(base / "docs"), names)

    assert 'href="docs_manual.html#使い方"' in out, "同梱文書が一緒に書き出した先を向いていない"
    assert 'href="file:///' in out and "LICENSE" in out, "同梱ファイルが file:// になっていない"
    assert ">O<" not in out.replace("<a", "@a"), "同梱物の外へのリンクが残っている"
    assert "outside.md" not in out and "missing.md" not in out, (
        "開けないリンクが残っている（押せるのに何も起きない）"
    )
    assert "O" in out and "X" in out, "リンクを外すときに字まで消している"
    assert 'href="https://example.com"' in out, "外部 URL を触っている"
    assert 'href="#概要"' in out, "ページ内アンカーを触っている"


def test_the_manual_viewer_writes_every_document_it_can_reach(tmp_path):
    """辿れる文書が**一式**書き出され、相互リンクが両方向で開けること。

    1 本だけ書き出すと、文書間のリンクは一時ディレクトリに相手がいないので必ず
    死ぬ。⚠️ 相互参照（A ⇄ B）で**止まる**ことも一緒に見る（推移閉包を素朴に
    たどると戻ってくる）。
    """
    from views.launcher_menu import render_doc_site

    base = tmp_path / "bundle"
    (base / "docs").mkdir(parents=True)
    (base / "docs" / "a.md").write_text("[to b](b.md)", encoding="utf-8")
    (base / "docs" / "b.md").write_text("[to a](a.md)", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()

    def _to_html(text):                     # markdown ライブラリを使わない最小の変換
        return re.sub(r"\[([^\]]*)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    entry = render_doc_site(str(base / "docs" / "a.md"), str(base), str(out),
                            _to_html, "ja")

    written = sorted(p.name for p in out.iterdir())
    assert len(written) == 2, f"辿れる文書が一式そろっていない: {written}"
    for name in written:
        href = re.search(r'href="([^"]+)"', (out / name).read_text(encoding="utf-8"))
        assert href, f"{name}: リンクが消えている"
        assert (out / href.group(1)).exists(), (
            f"{name}: リンク先 {href.group(1)} が書き出されていない"
        )
    assert Path(entry).name in written


def test_the_license_text_ships_with_the_binary():
    """**配布物に `LICENSE` が入っている**こと。

    MIT は「複製物に許諾表示を含めること」を求める。exe を受け取った利用者は
    リポジトリを見られないので、**同梱されていなければライセンス本文に触れられない**。
    ⚠️ 2026-08-09 まで実際に入っていなかった（README にライセンスを書き足そうとして
    発覚）＝**表記の不足ではなく配布の不備**。
    """
    assert "LICENSE" in _bundled_paths(), (
        "radiosim.spec の datas に LICENSE が無い（配布物にライセンス本文が入らない）"
    )


# ============================================================
# 適用不能域（B-032）の開示が、公開 README 5 本すべてに在ること
# ------------------------------------------------------------
# 2026-08-09 に「5 本の README へ明記した」とコミットメッセージに書いたのに、
# **実際に触れたのは 4 本**で、一番読まれる `README.md` だけ抜けていた
# （Codex の再レビューで発覚）。⚠️ **自己申告では検出できない型**＝数えたつもりの
# 数と触ったファイルの数が食い違っても、誰も突き合わせない。
#
# ここで守るのは**開示が全数に在ること**だけ（文面の良し悪しは doc-review 助言へ）。
# 🔴 **B-032 を 3.2 で直したら、この 2 つのゲートは「消す」のではなく
# 「何を開示すべきか」を書き換える**（頭打ちの説明は修正後も残る＝I-077）。
_PUBLIC_DOCS = ["README.md", *ALL_DOCS]

# ⚠️ **2026-08-09 に目印を入れ替えた**＝初版は `25m` / `25 m` を必須マーカーにしており、
# **誤った安全境界を機械的に固定していた**（Codex の再レビュー）。ゲートの壊れ方の
# 3 つ目「**間違ったものを要求している**」の実例（→ [[feedback-promote-recurring-checks]]）。
# ⇒ **数値の境界は目印にしない。**開示すべき概念は 3 つ＝①過大に出ること
# ②事前に見分けられないこと ③自衛策（`Single` と見比べる）。
_DIVERGENCE_MARKERS = {
    "ja": ["過大", "起伏の大きさでも", "`Single`"],
    "en": ["too high", "Neither", "`Single`"],
}
_CLAMP_MARKERS = {
    "ja": ["100% で頭打ち"],
    "en": ["capped at 100%"],
}

# 🔴 **書いてはいけない**＝「起伏がこれ以下なら信頼できる」という形の安全保証。
# 起伏は予測子ではない（同じ起伏 25m で、広い丘 65 dB / 細い峰 5.7 dB＝**11 倍**）。
# ⚠️ **在ることの検査だけでは、この誤りは防げなかった**＝誤った境界も「開示」の
# 顔をして全 5 本に揃っていた。**無いことの検査**を対で置く。
_FORBIDDEN_SAFE_BOUNDARY = [
    r"起伏\s*[0-9.]+\s*m\s*程度まで",          # 「起伏 25m 程度までの…は信頼できる」
    r"信頼できるのは起伏",
    r"relief or less",                          # 「…25 m of relief or less」
    r"roughly\s*[0-9.]+\s*m of relief",
]

# 🔴 **書いてはいけない②＝裏の取れていない保証**（2026-08-09・Codex 3 件目）。
# 上の安全境界と**同じ型**＝「危険がある」ではなく「ここまでは大丈夫」と言う形。
# 前日に安全境界を撤回した*その修正の中で*、代わりに 2 つを書いてしまった:
#   ①「2 つのモデルは真の値を挟む」＝**実測とも基準実装とも突き合わせていない**。
#     P.526 自体が地形の形ごとにモデルを選ぶ構造で、Single と Deygout を上下限と
#     していない。⇒ 言えるのは「差が大きい＝モデル依存が強い」という**診断**まで。
#   ②「Deygout は ITU-R P.526 準拠」＝使っているのは J(ν) だけで、現行 P.526-16 の
#     孤立円筒モデルも Bullington も標準の補正項も持たない**独自実装**。
# 🔑 **型＝「弱めた」つもりの文がまた別の保証になっていた。**開示を書き直すときは
# 「何を保証していないか」を数えること（→ [[feedback-promote-recurring-checks]]）。
_FORBIDDEN_UNBACKED_CLAIM = [
    (r"真の値を挟む|真値を挟む|上下限として", "Single と Deygout が真値を挟むという保証"),
    (r"bracket the truth", "the two models bracketing the true value"),
    # 見出しで「Deygout ＝ P.526 準拠」と名乗る形（本文の否定文は拾わない）
    (r"^#+.*Deygout.*P\.526", "見出しで Deygout を P.526 準拠と名乗っている"),
    # 🔴 **片側だけの方向保証**（2026-08-09・Codex 4 巡目で残留が判明）。
    # 「挟む」を消しても「Single は過小評価する」が残っていた＝**上下限保証の片側**。
    # ⚠️ 前回のゲートはこれを見逃した＝**禁じたのは合成語（「挟む」）だけで、
    # 主張の*形*（真値に対する向き）を見ていなかった**。
    # 言えるのは「Single は複数障害物の合成損失を表現しない ⇒ Deygout より小さく
    # 出ることがある」までで、**それが真値の下側という意味にはならない**。
    (r"過小評価|過少評価", "真値に対して過小という方向保証（モデル間の大小に言い換える）"),
    (r"underestimat", "a directional claim against the true value (say 'lower than Deygout')"),
]


def _readme_langs(doc: str) -> list[str]:
    """`README.md` は日英併記なので**両方**を要求する。他は名前で決まる。"""
    if doc == "README.md":
        return ["ja", "en"]
    return ["en"] if doc.endswith("_en.md") else ["ja"]


@pytest.mark.parametrize("doc", _PUBLIC_DOCS)
def test_mountain_path_limitation_is_disclosed(doc):
    """**回折損が過大に出ること・事前に見分けられないこと・自衛策**が全 README に在るか。"""
    text = _read(doc)
    for lang in _readme_langs(doc):
        for marker in _DIVERGENCE_MARKERS[lang]:
            assert marker in text, (
                f"{doc}: 回折損の適用限界の開示が足りない（{lang} の目印 '{marker}' が見つからない）"
            )


@pytest.mark.parametrize(
    "doc", _PUBLIC_DOCS + ["docs/screenshots.md", "CHANGELOG.md"]
)
def test_no_relief_based_safety_claim(doc):
    """**「起伏がこれ以下なら信頼できる」と書いていない**こと。

    2026-08-09 に実際に書いてしまい、しかも**ゲートがその境界を必須マーカーとして
    固定していた**。起伏 25m の滑らかな丘で `Single` 0.0 dB に対し Deygout 65 dB
    （F1 遮蔽率は 100% 未満）＝**起伏も遮蔽率も予測子ではない**。
    """
    text = _read(doc)
    for pat in _FORBIDDEN_SAFE_BOUNDARY:
        m = re.search(pat, text)
        assert m is None, (
            f"{doc}: 起伏を根拠にした安全保証が書かれている（'{m.group(0)}'）。"
            "起伏は予測子ではない＝同じ 25m で広い丘 65 dB / 細い峰 5.7 dB。"
        )


@pytest.mark.parametrize("doc", _PUBLIC_DOCS)
def test_blocked_ratio_clamp_is_explained(doc):
    """**F1 遮蔽率が表示側で 100% に頭打ちされる**ことが書いてあるか（I-077）。

    ⚠️ 頭打ちは B-032 の発散を画面から隠す＝「100%」が*完全遮蔽*と*発散*の
    2 つの意味を持つ。**発散の開示だけでは足りない**（利用者は率を見て安心する）。
    """
    text = _read(doc)
    for lang in _readme_langs(doc):
        for marker in _CLAMP_MARKERS[lang]:
            assert marker in text, (
                f"{doc}: F1 遮蔽率の 100% 頭打ちの説明が無い（{lang} の目印 '{marker}' が見つからない）"
            )


@pytest.mark.parametrize(
    "doc", _PUBLIC_DOCS + ["docs/screenshots.md", "CHANGELOG.md"]
)
def test_no_unbacked_accuracy_claim(doc):
    """**裏の取れていない保証を書いていない**こと（→ 上の `_FORBIDDEN_UNBACKED_CLAIM`）。

    禁じるのは **3 つの形**（語そのものではなく*主張の形*）:
      1. **見出しで規格への準拠を名乗る形**（`#### Deygout … P.526`）
      2. **2 モデルが真値を挟むという主張**（`真の値を挟む` / `bracket the truth`）
      3. **真値に対する一方向の保証**（`過小評価` / `underestimat`）
         ＝ 2. の片側だけを言い換えた形。**3. は 2. を撤回した後も残っていた**ので、
         あとから足した（2026-08-09・Codex 5 巡目）。

    ⚠️ 「Deygout」「P.526」という語そのものは禁じない。本文で「P.526 準拠ではない」と
    *否定*するのは通す（それが正しい記述だから）。
    ⚠️ **`過大`（Deygout 側）は禁じていない**＝あちらは真値との比較ではなく**モデルの
    自己矛盾**で立つ（ν が全区間で「見通し」判定なのに再帰を足して 65 dB／794m の回線で
    2000 dB は物理的に不可能）。**根拠の種類が違うものを、語の見た目で一緒に禁じない。**

    ⚠️ **このリストを増やすときは docstring も直す**＝3. を足したとき本文だけ直して
    ここを直し忘れ、**「2 つだけ禁じる」と書いてあるのに 3 つ禁じている**状態を作った
    （同巡の指摘）。**検査範囲の説明は、検査そのものと同じ場所で保守する。**
    """
    text = _read(doc)
    for pat, what in _FORBIDDEN_UNBACKED_CLAIM:
        m = re.search(pat, text, re.MULTILINE)
        assert m is None, (
            f"{doc}: 裏の取れていない保証が書かれている（{what}／'{m.group(0)[:60]}'）。"
            "言えるのは「差が大きければモデル依存が強い」という診断まで。"
        )
