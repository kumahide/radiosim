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
@pytest.mark.parametrize("doc", PIP_READMES)
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
    複数経路の節に載っているか。gain_tx/gain_rx 追加のような
    スキーマ変更をドキュメント全系統へ反映し忘れるのを捕捉する。"""
    section = _section(_read(doc), ["複数経路", "Multiple Paths"])
    for col in batch.CSV_COLUMNS:
        assert col in section, f"{doc}: batch CSV section is missing column '{col}'"


# doc の言語 → i18n の言語キー。バイナリ/開発者の両系統を言語ごとに照合する。
_MODE_READMES = [
    ("README_ja.md", "ja"), ("README_en.md", "en"),
    ("README_binary_ja.md", "ja"), ("README_binary_en.md", "en"),
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


@pytest.mark.parametrize("doc,lang", _MODE_READMES)
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
_LINK_DOCS = ["README.md", *ALL_READMES, "CHANGELOG.md", *_LOCAL_ONLY_DOCS]


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
_ISSUE_ID_RE = re.compile(r"\b[BI]-\d{3}\b")


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
