"""
tests/test_repo_hygiene.py
==========================
「追跡してはいけないファイルが追跡されていないか」を機械的に検証する回帰テスト。
コミット前の門（.git/hooks/pre-commit）と**同じ判定ロジックの単一の出所**でもある。

背景（2026-08-02 の事故）: このリポジトリは OneDrive 配下にあり、OneDrive は同期
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
