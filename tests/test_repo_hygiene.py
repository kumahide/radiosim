"""
tests/test_repo_hygiene.py
==========================
「追跡してはいけないファイルが追跡されていないか」を機械的に検証する回帰テスト。
コミット前の門（.git/hooks/pre-commit）と**同じ判定ロジックの単一の出所**でもある。

⚠️ **前提が 1 つ変わった（2026-08-11・I-057）＝リポジトリは OneDrive の外へ移した**
（`D:` ドライブ）。⇒ **下の同期競合コピーの検査は、いまの置き場では原理的に発火しない。**
それでも**残す**＝①判定ロジックは pre-commit フックと共有の単一の出所で、削ると門も痩せる
②置き場は将来また動き得る（実際 2026-07-27 に「移設しない」と決めたものが 2026-08-11 に
覆った）③コストがゼロ。**ただし「緑であること」を守りの証拠として数えないこと**——
いまこの検査が緑なのは、守れているからではなく**その事故が起こり得ない場所に居るから**。

背景（2026-08-02 の事故）: **当時**このリポジトリは OneDrive 配下にあり、OneDrive は同期
競合時に「<元の名前>-<マシン名>[-N].<拡張子>」というコピーを作る。**元の名前が
ignore されていてもコピーは別名なので ignore を素通りする**。実際に radiosim.log
（ignore 済み）の競合コピー radiosim-HP-OMEN25L.log（8.1MB）が commit・push され、
公開リポジトリの履歴から消すのに filter-repo での全履歴書き換えと、無効化した SHA
参照 264 箇所の修正を要した。

⚠️ 危険なのは容量ではなく中身: 同じ経路で ISSUES.md（未修正の脆弱性を含む）や
issue_evidence/（実運用のスクリーンショット）のコピーが**公開リポジトリに出る**。
.gitignore がこれらを外しているのは「公開できないから」であって、パターンを 1 つ
書き足すだけの対策では次の新しい名前に必ず負ける。

対策の層:
  A. .gitignore のパターン        … 常時・ただし既知の名前しか止まらない（Tier-2）
  B. .git/hooks/pre-commit        … 履歴に入る前に止める（このマシン限り＝clone されない）
  C. このテスト                   … CI と Stop ゲートで強制（消えない保証）

低ドリフト設計: 判定は「実ファイルの集合」と「.gitignore の解決結果」だけに依存し、
マシン名や利用者の環境に依存しない（CI でも同じ結果になる）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# --- 規則のパラメータ --------------------------------------------------------

# 追跡ファイルの上限。ここを超えるものは「成果物・ログ・素材の取り込み」を疑う。
SIZE_LIMIT = 1_000_000

# 上限を超えてよい既知の追跡ファイル（意図して置いている素材）。
SIZE_ALLOWLIST = {"logo.png"}

# 実行時に生成されるだけで、追跡する理由が無い拡張子。
RUNTIME_SUFFIXES = {".log"}

# 公開してはいけないクラス。.gitignore が緩んでも、ここで独立に止める
# （.gitignore の一行が消えたら守りごと消える、という単一障害点を作らない）。
PRIVATE_PATTERNS = ("ISSUES", "issue_evidence/", "radiosim_conf", "results/", "terrain_cache/")


# --- リポジトリ直下の venv ----------------------------------------------------
#
# 背景（B-038・2026-08-03 発覚 / 2.7a1 で決着）: 検証用の venv がリポジトリ直下
# （`.venv`・OneDrive 配下・12,120 ファイル 185MB）に、ビルド用の venv が
# `RADIOSIM_PYTHON` の指す OneDrive 外に、と **2 つ**あった。ピン更新は後者にしか
# 当たらず、**テストが検証した matplotlib 3.10.9 と exe に入る 3.11.1 がずれたまま
# `2.6RC1` を出荷**した。`test_env_consistency` は正しく鳴っていたが、1 週間読まれ
# なかった。⇒ **venv は 1 つだけ**という状態そのものを検査する。
#
# ⚠️ 名前ではなく `pyvenv.cfg` で見る。`.venv` だけを禁止すると `venv/` `env/` と
# 名前を変えた瞬間に穴が開く（[[feedback-promote-recurring-checks]] 実証10＝
# 「列挙で塞ぐ穴は名前 1 つで開く」）。`pyvenv.cfg` は venv の定義そのもので、
# 名前に依存しない。
VENV_MARKER = "pyvenv.cfg"


def venv_dirs_in_root(root: Path | None = None) -> list[str]:
    """リポジトリ直下にある venv（`pyvenv.cfg` を持つディレクトリ）の名前。

    直下だけを見る（全走査はしない）＝venv は直下に作られるものであり、
    terrain_cache 等の巨大ディレクトリを毎回舐める費用に見合わない。
    """
    root = root or ROOT
    found = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name != ".git" and (child / VENV_MARKER).exists():
            found.append(child.name)
    return found


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def tracked_paths() -> list[str]:
    """追跡下の全ファイル（リポジトリ相対・POSIX 区切り）。"""
    return [p for p in _git("ls-files").splitlines() if p]


def staged_paths() -> list[str]:
    """コミット予定（追加・変更）のファイル。pre-commit フックから使う。"""
    out = _git("diff", "--cached", "--name-only", "--diff-filter=AM")
    return [p for p in out.splitlines() if p]


def ignored(paths: list[str]) -> set[str]:
    """`.gitignore` に一致するものだけを返す（追跡の有無と無関係に判定）。"""
    if not paths:
        return set()
    r = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=ROOT, input="\n".join(paths), capture_output=True, text=True,
    )
    # 0=一致あり / 1=一致なし / それ以外=異常
    if r.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore failed: {r.stderr.strip()}")
    return {p for p in r.stdout.splitlines() if p}


def _conflict_copy_origin(path: str) -> str | None:
    """OneDrive 競合コピーの疑いがあるとき、その「元の名前」を返す。

    `radiosim-HP-OMEN25L.log` → `radiosim.log` のように、ハイフンで区切られた
    末尾要素を落とした候補を作る。**候補が ignore 対象のときだけ**競合コピーと
    みなす（`requirements-dev.txt` の元候補 `requirements.txt` は ignore されて
    いないので誤検知しない＝ハイフンを含む正当なファイル名を巻き込まない）。
    マシン名を知らなくても判定できるので、CI でも同じ結果になる。
    """
    p = Path(path)
    parts = p.stem.split("-")
    if len(parts) < 2:
        return None
    # 候補は短い順（= 落とす要素が多い順）に見て、最初に ignore に当たったものを
    # 「元の名前」とする。ここを集合の辞書順で選ぶと radiosim-HP-OMEN25L.log の元が
    # radiosim-HP.log になり、報告が実態とずれる。
    for i in range(1, len(parts)):
        candidate = "-".join(parts[:i]) + p.suffix
        rel = candidate if p.parent == Path(".") else str(p.parent / candidate)
        if ignored([rel]):
            return candidate
    return None


def violations(paths: list[str], size_of=None) -> list[str]:
    """規則に反するものを「パス: 理由」の一覧で返す（空なら健全）。"""
    if size_of is None:
        def size_of(rel: str) -> int:
            f = ROOT / rel
            return f.stat().st_size if f.exists() else 0

    found: list[str] = []
    ignored_set = ignored(paths)

    for rel in sorted(paths):
        name = Path(rel).name

        if Path(rel).suffix.lower() in RUNTIME_SUFFIXES:
            found.append(f"{rel}: 実行時ログは追跡しない（{Path(rel).suffix}）")
            continue

        if any(rel.startswith(pat) or name.startswith(pat) for pat in PRIVATE_PATTERNS):
            found.append(f"{rel}: 公開できないクラス（このリポジトリは公開）")
            continue

        origin = _conflict_copy_origin(rel)
        if origin is not None:
            found.append(f"{rel}: OneDrive の同期競合コピーの疑い（元は ignore 対象の {origin}）")
            continue

        if rel in ignored_set:
            found.append(f"{rel}: .gitignore に一致するのに追跡されている")
            continue

        if name not in SIZE_ALLOWLIST and size_of(rel) > SIZE_LIMIT:
            found.append(f"{rel}: {size_of(rel):,} バイト（上限 {SIZE_LIMIT:,}）")

    return found


# --- テスト ------------------------------------------------------------------


class TestRepoHygiene:
    def test_追跡ファイルに違反が無い(self):
        """本番の不変条件。ここが落ちたら履歴に入る前に止める。"""
        found = violations(tracked_paths())
        assert not found, "追跡してはいけないファイルがある:\n  " + "\n  ".join(found)

    # --- ゲートの壊れ方 3 種を通す（memory: feedback-promote-recurring-checks）---

    def test_壊れ方1_一度も落ちない_ことがない(self):
        """①事故と同じ入力を与えたら必ず検出すること（検出力の証明）。"""
        found = violations(
            ["radiosim-HP-OMEN25L.log"], size_of=lambda _: 8_118_094
        )
        assert found, "2026-08-02 の事故ファイルを検出できていない"

    @pytest.mark.parametrize(
        "path, size, 理由",
        [
            ("ISSUES-HP-OMEN25L.md", 100, "公開できない課題台帳のコピー"),
            ("issue_evidence/B-021_01.png", 100, "実運用のスクリーンショット"),
            ("radiosim_conf-HP-OMEN25L.json", 100, "利用者の実設定のコピー"),
            ("radiosim-HP-OMEN25L-2.log", 100, "ローテーション付きの競合コピー"),
            ("terrain_cache/z14/1.png", 100, "ランタイムキャッシュ"),
            ("assets/huge_blob.bin", SIZE_LIMIT + 1, "巨大な取り込み"),
        ],
    )
    def test_壊れ方1b_同じクラスの別の名前も検出する(self, path, size, 理由):
        """1 件だけ直して終わりにしない（memory: user-examples-are-classes）。"""
        assert violations([path], size_of=lambda _: size), f"未検出: {理由}"

    def test_壊れ方2_毎回鳴る_ことがない(self):
        """②正当な追跡ファイルでは沈黙すること（空振りしない）。"""
        正当 = ["config.py", "requirements-dev.txt", "logo.png", "tests/test_repo_hygiene.py"]
        found = violations(正当)
        assert not found, "正当なファイルを誤検知している:\n  " + "\n  ".join(found)

    def test_壊れ方3_間違ったものを要求していない(self):
        """③「ハイフンを含む名前」ではなく「元が ignore 対象のコピー」を見ている。

        requirements-dev.txt は元候補 requirements.txt が ignore されていないので
        通り、radiosim-HP-OMEN25L.log は元候補 radiosim.log が ignore なので落ちる。
        両者の差が付かなければ、この規則は名前の形だけを見ていることになる。
        """
        assert _conflict_copy_origin("requirements-dev.txt") is None
        assert _conflict_copy_origin("radiosim-HP-OMEN25L.log") == "radiosim.log"

    # --- リポジトリ直下の venv（B-038 ③）-------------------------------------

    def test_リポジトリ直下に_venv_が無い(self):
        """本番の不変条件。venv が 2 つある状態を作らせない。"""
        found = venv_dirs_in_root()
        assert not found, (
            "リポジトリ直下に venv があります: " + ", ".join(found) + "\n"
            "この環境は誰にも宣言されていないため、依存のピン更新から取り残され、"
            "**検証した版と exe に入る版がずれます**（B-038＝実際に 2.6RC1 で起きた）。\n"
            "venv はリポジトリの外に 1 つだけ置き、RADIOSIM_PYTHON で宣言してください"
            "（手順は README「開発環境のセットアップ」）。"
        )

    def test_venv門_壊れ方1_一度も落ちない_ことがない(self, tmp_path):
        """①venv があれば必ず検出すること（削除後に無検出で緑になっていないか）。"""
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / VENV_MARKER).write_text("home = C:\\Python", encoding="utf-8")
        assert venv_dirs_in_root(tmp_path) == [".venv"]

    def test_venv門_壊れ方2_毎回鳴る_ことがない(self, tmp_path):
        """②venv でないディレクトリでは沈黙すること。"""
        for name in ("views", "tests", "terrain_cache"):
            (tmp_path / name).mkdir()
        (tmp_path / "requirements.txt").write_text("numpy\n", encoding="utf-8")
        assert venv_dirs_in_root(tmp_path) == []

    @pytest.mark.parametrize("name", ["venv", "env", ".env311", "検証環境"])
    def test_venv門_壊れ方3_名前ではなく実体を見ている(self, tmp_path, name):
        """③`.venv` という名前だけを禁じていないこと。

        名前で列挙すると、次に作られる venv が別名だった瞬間に穴が開く。
        判定は `pyvenv.cfg` の有無＝venv の定義そのもの。
        """
        (tmp_path / name).mkdir()
        (tmp_path / name / VENV_MARKER).write_text("home = C:\\Python", encoding="utf-8")
        assert venv_dirs_in_root(tmp_path) == [name]

    def test_size_allowlist_が実在する(self):
        """許可リストが陳腐化していないか（消えたファイルを許し続けない）。"""
        for name in SIZE_ALLOWLIST:
            assert (ROOT / name).exists(), f"許可リストの {name} が存在しない"


# ============================================================
# モジュールの分割閾値（2.7 スライス A で新設）
# ============================================================
# 🔴 **「触る版が来たら割る」では二度と割れない**——2.6 は超過 3 モジュールを
# 全部触ったのに、どれも割らなかった。監視条項が散文だったので、版の作業順に
# 項目として現れず、誰も判断を迫られなかった（[[feedback-promote-recurring-checks]]）。
# ⇒ 2.7 で 3 つとも割ったので、**その状態を機械で保つ**。
#
# 閾値 1045 行は 2.4 から使ってきた値をそのまま採る（新しい根拠を作らない）。
# ⚠️ **超えたら「割る」か「理由つきで許可リストへ」の二択**＝どちらを選んでも
# 判断が記録に残る。数字を黙って上げるのは 3 つ目の選択肢ではない。
_MODULE_LINE_LIMIT = 1045

# 行数ではなく**性質**で外すもの（表・辞書＝分割しても読みやすくならない）。
_LINE_LIMIT_EXEMPT = {
    "core/i18n.py": "UI 文字列の辞書＝データ表。分割しても読む単位は変わらない",
}

# アプリの層（2.7 スライス H・I-058）。直下は入口（main.py）だけ。
_LAYERS = ("core", "report", "views")


def _python_modules():
    """アプリのモジュール（直下の入口と 3 層）。テスト・ツールは対象外。"""
    paths = list(ROOT.glob("*.py"))
    for layer in _LAYERS:
        paths += (ROOT / layer).glob("*.py")
    return sorted(paths)


def _rel(path) -> str:
    """リポジトリからの相対パス（`core/i18n.py` の形）。"""
    return path.relative_to(ROOT).as_posix()


def test_no_module_exceeds_the_split_threshold():
    """アプリのモジュールが分割閾値を超えていないこと。"""
    over = []
    for path in _python_modules():
        if _rel(path) in _LINE_LIMIT_EXEMPT:
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > _MODULE_LINE_LIMIT:
            over.append(f"{_rel(path)}: {lines} 行")
    assert not over, (
        f"分割閾値（{_MODULE_LINE_LIMIT} 行）を超えたモジュールがある: {over}。"
        "割るか、理由を書いて _LINE_LIMIT_EXEMPT へ入れること"
        "（閾値そのものを上げるのは選択肢ではない）。"
    )


def test_line_limit_exemptions_still_exist():
    """除外リストに、実在しないファイルが残っていないこと（掃除漏れ検出）。"""
    missing = [name for name in _LINE_LIMIT_EXEMPT if not (ROOT / name).exists()]
    assert not missing, f"除外リストに実在しないファイルがある: {missing}"


# ============================================================
# 未定義の名前・未使用の import（2.7 スライス F で新設）
# ============================================================
# 🔴 **CI にしか無いゲートは、push するまで一度も鳴らない。**
# 2.7 スライス A（view 分割）は `views/map_cache.py` に `_LEVEL_COLORS` の
# **未定義参照**（定義は `map_window.py` に残っていた＝`NameError`）を残したが、
# 気づいたのは 6 コミットあと。理由は単純で、**この間 push が無く CI が一度も
# 走らなかった**から。`ruff` は最初からこれを F821 として指していた。
# ⇒ **同じ検査をローカルの pytest からも回す**（設定は pyproject の
#    [tool.ruff] をそのまま使うので二重管理にならない）。
#
# ⚠️ **未使用 import も一緒に見る**（F401）。分割の残骸というだけでなく、
# **テストが「たまたま生きている import」経由でモジュールを差し替えている**のを
# 隠す（実例＝`tests/test_batch.py` が `batch_builder.os` を差し替えていたが、
# 実際に `os.startfile` を呼ぶのは `batch_run.py` だった。モジュールオブジェクトは
# 共有なので通っていただけで、掃除した瞬間に落ちた）。
def test_ruff_finds_no_undefined_names_or_unused_imports():
    """`ruff check .` が通ること（CI の Ruff ステップと同じ設定・同じ対象）。

    ⚠️ **`sys.executable -m ruff` で呼ぶ**＝`shutil.which("ruff")` は venv の
    `Scripts/` が PATH に無い実行のしかたで空を返す。そこで skip すると、
    **一度も鳴らないゲート**になる（[[feedback-promote-recurring-checks]] 壊れ方②）。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format", "concise"],
        cwd=ROOT, capture_output=True, text=True, errors="replace",
    )
    if "No module named ruff" in proc.stderr:
        pytest.skip("ruff が入っていない（requirements-dev.txt）")
    assert proc.returncode == 0, (
        "ruff の指摘がある（CI の Ruff ステップが同じ内容で落ちる）:\n"
        + proc.stdout + proc.stderr
    )


# ============================================================
# 型エラー（2.7・B-049 で新設）
# ============================================================
# 🔴 **B-047 と同じ穴が、同じ分割で、別の道具に開いていた。**
# `ruff` はローカルへ降ろした（上のゲート）が、**pyright は CI にしか無いまま**
# だった。2.7 スライス A（view 分割）で作った Mixin 8 本は、宿主（`MapWindow` /
# `SimLauncher` / `BatchBuilderWindow`）の `self.*` を借りたまま切り出されており、
# 型検査器から見ると借りている属性は「無い属性」＝**365 件のエラー**になっていた。
# 気づいたのは 12 コミットあと、push して CI が赤くなった時。
# ⇒ **CI と同じ対象・同じ設定で、ローカルの pytest からも回す。**
#
# ⚠️ **対象リストは `.github/workflows/ci.yml` から読む**＝ここへ写すと 2 本目の
# 手書きリストになり、片方だけ増えて黙ってすり抜ける（[[project-radiosim]] の
# I-058 が消そうとしているのと同じ形の穴）。出所は CI の 1 か所のまま。
def _ci_pyright_targets() -> list[str]:
    """CI の Pyright ステップが検査する対象を ci.yml から取り出す。"""
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("- name:") and "Pyright" in line:
            break
    else:
        raise AssertionError("ci.yml に Pyright のステップが無い")
    # `run: >-` の折り畳みブロック＝`run:` より深くインデントされた行が本文。
    for j in range(i + 1, len(lines)):
        if lines[j].strip().startswith("run:"):
            run_indent = len(lines[j]) - len(lines[j].lstrip())
            break
    else:
        raise AssertionError("Pyright ステップに run: が無い")
    words: list[str] = []
    for line in lines[j + 1:]:
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) <= run_indent:
            break
        words += line.split()
    assert words and words[0] == "pyright", f"想定外の run: 本文 {words[:3]}"
    return words[1:]


def test_pyright_finds_no_type_errors_in_app_modules():
    """`pyright` が通ること（CI の Pyright ステップと同じ対象・同じ設定）。

    ⚠️ **対象が痩せていないことを先に検査する**＝ci.yml の書式が変わって 0 件を
    取り出しても pyright は `0 errors` で終わる＝**一度も鳴らないゲート**になる
    （[[feedback-promote-recurring-checks]] 壊れ方①）。
    """
    targets = _ci_pyright_targets()
    # 3 層とも入っていること。**件数ではなく層で見る**（2.7 スライス H で対象が
    # ファイル 43 件からディレクトリ 3 つ＋α になった＝件数の下限は意味を失った）。
    missing = {"core", "report", "views"} - set(targets)
    assert not missing, (
        f"ci.yml の pyright が層を取りこぼしている: {sorted(missing)}（対象={targets}）"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "pyright", "--outputjson", *targets],
        cwd=ROOT, capture_output=True, text=True, errors="replace",
    )
    if "No module named pyright" in proc.stderr:
        pytest.skip("pyright が入っていない（requirements-dev.txt）")
    errors = [
        f"{d.get('file')}:{(d.get('range') or {}).get('start', {}).get('line', 0) + 1}"
        f" {d.get('message', '').splitlines()[0]}"
        for d in (json.loads(proc.stdout or "{}").get("generalDiagnostics") or [])
        if d.get("severity") == "error"
    ]
    assert not errors, (
        f"pyright の型エラーが {len(errors)} 件ある"
        "（CI の Pyright ステップが同じ内容で落ちる）:\n"
        + "\n".join(errors[:25])
    )


# ============================================================
# アプリ設定の出所は 1 つ（2.7 スライス G2＝I-055 ②）
# ============================================================
# 🔴 背景: 窓が `config.load_config()` を**直に**呼ぶと、①テストの緑が開発機の
# 実設定に左右され（B-034 が長期間生き延びた機構そのもの）②同じ設定を窓ごとに
# 違う時点で読むので、画面と保存物で表記が食い違い得る。
# ⇒ **app 設定はランチャーが読み、開く時点のスナップショットを窓へ渡す**
#   （[[project-radiosim]] の凍結方式を設定へ広げただけ＝新しい仕掛けではない）。
#
# ⚠️ 窓の名前を列挙して禁じない＝次に増える窓が別名だった瞬間に穴が開く
# （[[feedback-promote-recurring-checks]] 実証10）。**読んでよい側を挙げる。**
_CONFIG_READ_ALLOWED = {
    "core/config.py":     "定義そのもの",
    "main.py":            "起動時の読み込み（アプリの入口）",
    "views/launcher.py":  "凍結の出所＝ランチャーが読んで子窓へ渡す",
}

# `config.load_config()` と、`from config import load_config`（別名で持ち込んで
# から呼ぶ経路）の両方を捕まえる。⚠️ 層をディレクトリにしたので
# `from core.config import load_config` の形も来る（2.7 スライス H）。
_CONFIG_READ_RE = re.compile(
    r"\bload_config\s*\(|from\s+(?:core\.)?config\s+import\s+[^\n]*\bload_config\b"
)


def direct_config_reads(name: str, text: str) -> list[str]:
    """モジュール 1 本の中の「app 設定を直に読んでいる」行（許可された側は空）。"""
    if name in _CONFIG_READ_ALLOWED:
        return []
    found = []
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]          # コメント内の言及は対象外
        if _CONFIG_READ_RE.search(code):
            found.append(f"{name}:{i}")
    return found


def _app_modules_with_text():
    for path in _python_modules():
        rel = path.relative_to(ROOT).as_posix()
        yield rel, path.read_text(encoding="utf-8")


class TestConfigHasOneSource:
    def test_窓が_app_設定を直に読まない(self):
        """本番の不変条件。読んでよいのは入口とランチャーだけ。"""
        found = [v for rel, text in _app_modules_with_text()
                 for v in direct_config_reads(rel, text)]
        assert not found, (
            "app 設定を直に読んでいる箇所があります: " + ", ".join(found) + "\n"
            "設定はランチャーが読み、窓を開く時点のスナップショットを引数で渡して"
            "ください（凍結方式・I-055 ②）。直に読むと、テストの結果が開発機の"
            "設定に左右されます。"
        )

    def test_壊れ方1_一度も落ちない_ことがない(self):
        """①直読みを与えたら必ず検出すること。"""
        text = '        self._coord_format = config.load_config().get("coord_format", "dd")\n'
        assert direct_config_reads("views/batch_builder.py", text)

    @pytest.mark.parametrize("code", [
        "cfg = config.load_config()",
        "from config import load_config",     # 別名で持ち込む経路
        "fmt = load_config().get('coord_format')",
        "conf = config.load_config(path)",
    ])
    def test_壊れ方1b_同じクラスの別の書き方も検出する(self, code):
        assert direct_config_reads("views/新しい窓.py", code + "\n")

    def test_壊れ方2_毎回鳴る_ことがない(self):
        """②正しい書き方では沈黙すること。"""
        正当 = (
            "self._coord_format = coord_format\n"
            "c = self._config_provider()\n"
            "params = sim.SimParams(config.DEFAULT_CONFIG)\n"
            "config.save_app(self.config)\n"
            "# 窓は config.load_config() を直に読まない（説明のコメント）\n"
        )
        assert direct_config_reads("views/graph.py", 正当) == []

    def test_壊れ方3_間違ったものを要求していない(self):
        """③禁じているのは「窓が直に読むこと」であって config の利用ではない。

        許可された側（ランチャー・入口）は同じ行でも通ること、逆に窓の名前を
        知らなくても検出できることの両方を示す。
        """
        code = "cfg = config.load_config()\n"
        assert direct_config_reads("views/launcher.py", code) == []
        assert direct_config_reads("main.py", code) == []
        assert direct_config_reads("views/まだ存在しない窓.py", code)

    def test_許可リストが実在する(self):
        missing = [n for n in _CONFIG_READ_ALLOWED if not (ROOT / n).exists()]
        assert not missing, f"許可リストに実在しないファイルがある: {missing}"


# --- pre-commit フックからの呼び出し口 ---------------------------------------

if __name__ == "__main__":
    対象 = staged_paths() if "--staged" in sys.argv else tracked_paths()
    問題 = violations(対象)
    if 問題:
        print("コミットを中止しました（tests/test_repo_hygiene.py）:", file=sys.stderr)
        for v in 問題:
            print(f"  - {v}", file=sys.stderr)
        print(
            "\n意図した追加なら .gitignore と当該規則を見直してから再実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    sys.exit(0)
