"""探針（`experiments/`）が製品と一緒に動いているかを**静的に**検査する（B-261 の昇格）。

🔴 **なぜ要るか**＝2026-09-21 に `b129_vegetation_double_count_probe.py` が
`models._vegetation_loss()` へ `(horiz_dist_km, n)` の 2 本を渡していたが、製品側は
`d_m_axis` の 1 本に変わっており **TypeError で落ちていた**。この探針は [[B-132]]
（対応中）の**再現手順そのもの**なので、**対応中の課題の再現路が断線していた**。
しかも手前で cp932 のクラッシュが出ていたため、**何度実行してもそこまで到達せず**
気づけなかった（→ [[feedback_promote_recurring_checks]] の実証61＝**ゲートの
壊れ方の 4 つ目＝先に別の理由で落ちていて、検査したい所まで到達していない**）。

⛔ **実行して確かめる形は採れない**＝探針は引数・データ・ネットワーク・実機・
GUI の前提がまちまちで、CI で一律に走らせる形が無い（B-261 の対応欄が
「まだゲート化していない・規約で受ける」で止まった理由）。⇒ **動かさずに読む。**

検査は 2 つ:

  A. **呼び出しが製品のシグネチャに通るか**＝`core` / `report` の関数を呼ぶ箇所を
     AST で拾い、`inspect.signature().bind()` に掛ける。**名前が消えた／引数の数や
     キーワードが変わった**を、探針を実行せずに捕まえる。
  B. **cp932 で落ちないか**＝非 ASCII を `print` する探針は `sys.stdout` の
     `reconfigure` を持つこと（B-261 の症状そのもの）。

⚠️ **この検査が見ていないもの**（＝「間違ったものを要求している」を避けるために明記）:
  - 引数の**値**（型・単位・意味）は見ない。⇒ 2026-09-21 の欠陥は捕まるが、
    「同じ数の引数を意味を取り違えて渡した」は捕まらない。
  - `*args` / `**kwargs` で展開している呼び出しは **bind できないので飛ばす**。
  - 製品モジュール以外（`numpy` 等）の呼び出しは対象外。
"""
from __future__ import annotations

import ast
import inspect
import importlib
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_EXPERIMENTS = os.path.join(_ROOT, "experiments")

#: 製品側＝ここに属するモジュールの呼び出しだけを照合する。
_PRODUCT_ROOTS = ("core", "report", "views", "buildtools")


def _rel(path: str) -> str:
    """リポジトリからの相対パス。⚠️ **別ドライブなら relpath は例外**を投げる
    （`tmp_path` は C:・リポジトリは D:）＝そこは名前だけに落とす。"""
    try:
        return os.path.relpath(path, _ROOT).replace("\\", "/")
    except ValueError:
        return os.path.basename(path)


def _probe_files() -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(_EXPERIMENTS):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return out


def _alias_map(tree: ast.AST) -> dict[str, str]:
    """`from core import models as m` → `{"m": "core.models"}` を作る。

    ⚠️ **`import core.models` は飛ばす**＝呼び出し側が `core.models.f()` と
    書くので `func.value` が `Name` にならず、この検査の土俵に乗らない
    （実データで 0 件なので、当てにいかずに飛ばす）。
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            base = node.module.split(".")[0]
            if base not in _PRODUCT_ROOTS or node.level:
                continue
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _resolve(dotted: str):
    """`core.models` → モジュール／`core.models.f` → 関数。届かなければ None。"""
    try:
        return importlib.import_module(dotted)
    except ImportError:
        pass
    mod_name, _, attr = dotted.rpartition(".")
    if not mod_name:
        return None
    try:
        mod = importlib.import_module(mod_name)
    except ImportError:
        return None
    return getattr(mod, attr, None)


def _binding_problems(path: str) -> list[str]:
    """1 ファイルぶんの「製品の呼び出しが通らない」を列挙する。"""
    return _scan(path)[0]


def _scan(path: str) -> tuple[list[str], int]:
    """`(問題の一覧, 実際に照合できた呼び出しの数)`。

    🔑 **数も返す理由**＝問題 0 件は「健全」と「1 件も見ていない」の両方で起きる。
    import の書き方が変わってエイリアスが取れなくなれば、このゲートは**黙って
    通る**（→ [[feedback_promote_recurring_checks]] の壊れ方①）。
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    aliases = _alias_map(tree)
    if not aliases:
        return [], 0
    rel = _rel(path)
    problems: list[str] = []
    checked = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)):
            continue
        dotted = aliases.get(fn.value.id)
        if dotted is None:
            continue
        target = _resolve(f"{dotted}.{fn.attr}")
        if target is None:
            problems.append(
                f"{rel}:{node.lineno} {fn.value.id}.{fn.attr} が製品側に無い"
                f"（{dotted} を探した）"
            )
            continue
        if not callable(target):
            continue          # 定数への参照＝呼び出しではない
        # ⚠️ 展開された呼び出しは bind できない＝**飛ばす**（当てにいかない）。
        if any(isinstance(a, ast.Starred) for a in node.args) or \
           any(k.arg is None for k in node.keywords):
            continue
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError):
            continue          # 組み込み等＝シグネチャを持たない
        args = [None] * len(node.args)
        kwargs = {k.arg: None for k in node.keywords}
        checked += 1
        try:
            sig.bind(*args, **kwargs)
        except TypeError as exc:
            problems.append(
                f"{rel}:{node.lineno} {fn.value.id}.{fn.attr}{sig} へ "
                f"位置 {len(args)} 個・キーワード {sorted(kwargs)} を渡している"
                f"＝{exc}"
            )
    return problems, checked


def test_probes_call_the_product_with_a_signature_that_binds():
    """探針の製品呼び出しが、いまのシグネチャに通ること（B-261 の断線の再発防止）。"""
    problems: list[str] = []
    checked = 0
    for path in _probe_files():
        found, n = _scan(path)
        problems.extend(found)
        checked += n
    assert checked > 50, (
        f"照合できた製品呼び出しが {checked} 件しかない＝ゲートが黙って通っている"
        "（import の書き方が変わってエイリアスを拾えていない可能性）"
    )
    assert problems == [], (
        "探針が製品のシグネチャに通らない呼び出しを持っている"
        "＝実行すると TypeError で落ちる（対応中の課題の再現路が断線する）:\n  "
        + "\n  ".join(problems)
    )


# ============================================================
# B: cp932 で落ちないか（B-261 の症状そのもの）
# ============================================================
def _unencodable(text: str) -> bool:
    """既定のコンソール（cp932）で**出せない**文字を含むか。

    🔴 **「非 ASCII」で判定してはいけない**（2026-09-22 に実際に踏んだ）＝
    最初この関数は `not s.isascii()` で書いたところ **14 本を挙げた**が、
    **症状を測ったら 1 本も落ちなかった**（→ [[feedback_measure_the_symptom]]）。
    `ν`・`α`・全角の括弧や漢字は **cp932 に在る**ので通る。落ちるのは
    `⚠️` `✅` `🔑` `⇒` のような **cp932 の外の記号**だけ。
    ⇒ **判定は実際のコーデックに聞く。** 字の見た目で決めない。
    """
    try:
        text.encode("cp932")
    except UnicodeEncodeError:
        return True
    return False


def _prints_unencodable(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if _unencodable(sub.value):
                    return True
    return False


def _has_stdout_reconfigure(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "reconfigure":
            return True
    return False


def test_probes_that_print_unencodable_text_survive_redirection():
    """cp932 で出せない字を出す探針は `reconfigure` を持つこと。

    🔑 **症状は「リダイレクトすると落ちる」**（コンソールへ直接出すと再現しない）
    ＝`& "$env:RADIOSIM_PYTHON" experiments/x.py > out.txt` で
    `UnicodeEncodeError`。台帳の再現手順を引用して回すとき、ここで死ぬと
    **検査したい所まで到達しない**。
    """
    offenders = []
    for path in _probe_files():
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        if _prints_unencodable(tree) and not _has_stdout_reconfigure(tree):
            offenders.append(_rel(path))
    assert offenders == [], (
        "cp932 で出せない字を print するのに stdout を作り直していない探針がある"
        "＝出力をファイルへ落とすと cp932 で落ちる（B-261）:\n  "
        + "\n  ".join(offenders)
    )


# ============================================================
# ゲート自身の検査（→ [[feedback_promote_recurring_checks]] の「壊れ方 3 種」）
# ============================================================
class TestTheGateItself:
    """①一度も落ちない ②毎回鳴る ③間違ったものを要求している、を全部通す。"""

    def _write(self, tmp_path, body: str) -> str:
        p = tmp_path / "probe_case.py"
        p.write_text(body, encoding="utf-8")
        return str(p)

    def test_it_catches_the_real_defect(self, tmp_path, monkeypatch):
        """①＝2026-09-21 の欠陥そのものを合成して、**落ちること**を確かめる。

        `_vegetation_loss` へ末尾 2 本（旧 `(horiz_dist_km, n)`）を渡す形。
        """
        path = self._write(tmp_path, (
            "from core import models\n"
            "models._vegetation_loss(a, b, c, d, e, f, g)\n"
        ))
        problems = _binding_problems(path)
        assert problems, "旧シグネチャの呼び出しを見逃した＝ゲートが落ちない"
        assert "_vegetation_loss" in problems[0]

    def test_it_catches_a_name_that_no_longer_exists(self, tmp_path):
        """①＝関数ごと消えた／改名された場合も捕まえる。"""
        path = self._write(tmp_path, (
            "from core import models\n"
            "models.this_function_was_renamed(1)\n"
        ))
        problems = _binding_problems(path)
        assert problems and "製品側に無い" in problems[0]

    def test_it_is_quiet_on_a_correct_call(self, tmp_path):
        """②＝正しい呼び出しでは鳴らないこと（毎回鳴るゲートは無視される）。"""
        path = self._write(tmp_path, (
            "from core import models\n"
            "models.scope_notes(2400.0, veg_h=3.0)\n"
        ))
        assert _binding_problems(path) == []

    def test_it_does_not_demand_what_it_cannot_read(self, tmp_path):
        """③＝展開された呼び出しには**口を出さない**こと。

        `f(*args)` の実引数は静的には数えられない。ここで鳴らすと、
        **直しようのない指摘**を毎回出すゲートになる。
        """
        path = self._write(tmp_path, (
            "from core import models\n"
            "models.scope_notes(*everything)\n"
            "models.scope_notes(2400.0, **options)\n"
        ))
        assert _binding_problems(path) == []

    def test_it_ignores_modules_that_are_not_the_product(self, tmp_path):
        """③＝製品でないモジュール（numpy 等）は対象外。"""
        path = self._write(tmp_path, (
            "import numpy as np\n"
            "np.array(1, 2, 3, 4, 5)\n"
        ))
        assert _binding_problems(path) == []

    def test_the_encoding_check_catches_and_stays_quiet(self, tmp_path):
        """①②＝cp932 側のゲートも両方向を通す。"""
        bad = ast.parse("print('⚠️ 警告')")
        assert _prints_unencodable(bad) and not _has_stdout_reconfigure(bad)
        good = ast.parse(
            "import sys\n"
            "sys.stdout.reconfigure(encoding='utf-8')\n"
            "print('⚠️ 警告')\n"
        )
        assert _has_stdout_reconfigure(good)
        plain = ast.parse("print('ascii only')")
        assert not _prints_unencodable(plain)

    def test_the_encoding_check_does_not_fire_on_plain_japanese(self):
        """③＝**cp932 に在る字では鳴らない**こと。

        🔴 ここが最初 `isascii()` で書かれていて、**14 本の偽陽性**を出した。
        症状（PowerShell のリダイレクト）を測ったら 1 本も落ちなかった
        （→ [[feedback_measure_the_symptom]]）。日本語も `ν` も cp932 に在る。
        """
        jp = ast.parse("print('標本数依存（2km 平地）  ν の幅  α 係数')")
        assert not _prints_unencodable(jp)
        assert _unencodable("⚠️") and _unencodable("✅") and _unencodable("🔑")
        assert not _unencodable("ν") and not _unencodable("標本数依存（）")
        # ⚠️ **`⇒` は cp932 に在る**（2026-09-22 に外した＝見た目で決めた思い込み）。
        assert not _unencodable("⇒")

    def test_the_probe_corpus_is_not_empty(self, tmp_path):
        """⛔ **探針が 1 本も見つからないとき、このゲートは黙って通る。**"""
        files = _probe_files()
        assert len(files) > 10, f"探針の収集が壊れている: {len(files)} 本"
