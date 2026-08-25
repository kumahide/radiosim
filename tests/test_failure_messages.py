"""
tests/test_failure_messages.py
==============================
**失敗の伝え方が型に乗っている**ことのゲート（I-100）。

なぜ要るか
----------
失敗の伝え方が経路ごとにばらばらだった。**1 件ずつ潰すと、潰した数だけ書き方が
増える**ので、`core/failure.py` に型を 1 つ置き、全失敗経路をそこへ乗せた。
⇒ 型は注意書きでは保てない（[[feedback-promote-recurring-checks]] の昇格）ので、
**新しい失敗経路を「例外の str() をそのまま画面へ」の形で書けなくする**。

型（`core/failure.py` の docstring が正典）
-------------------------------------------
1. 何が起きた（＋なぜ止めた／続けた）
2. **次に何をすべきか**（必須）
3. 詳細（例外の原文・ログの場所）

見るのは「失敗のダイアログの本文」
----------------------------------
`views/` の `alert(...)` のうち、**題が失敗の題**（`*_error` 系）のものだけ。
本文に許すのは 2 形だけ:

- `failure.*(...)`＝型で組んだ本文
- `i18n.t("...")`（`.format(...)` 付きも可）＝カタログの 1 文（入力の型）

⛔ 弾くのは `str(e)`・`"\\n".join(...)`・f 文字列・`+` 連結＝**その場で文を組む形**。
これが「1 件ずつ書き方が増える」入口そのものだった。

⚠️ **成功のダイアログは対象外**（`dlg_success` など）＝型は失敗の伝え方の話で、
成功に「次に何をすべきか」を要求すると毎回鳴るゲートになる。

ゲートの壊れ方 3 点（[[feedback-promote-recurring-checks]]）
------------------------------------------------------------
- **一度も落ちない**：`test_the_scanner_catches_the_original_form` が I-100 以前の
  実際の書き方（`dialogs.alert(self, i18n.t("dlg_error"), str(e))`）を毎回走査して
  赤になることを確かめる。走査が空を返すようになればここが落ちる。
- **毎回鳴る**：例外表は**空**。空で始められる範囲（失敗の題だけ）を選んだ。
- **間違ったものを要求している**：「日本語で書け」ではなく「**その場で文を組むな**」。
  型に乗った文はカタログにあり、翻訳も外部の言語ファイルで差し替えられる。
"""

import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import failure
from core import i18n

REPO = Path(__file__).resolve().parent.parent
VIEWS = REPO / "views"

#: 失敗の題（＝本文が型に乗っていなければならないダイアログ）。
#: ⚠️ **手で数えない**＝`*_error` で終わる題のキーを i18n の表から拾い、
#: 名前の形で表せないものだけ下の `EXTRA_FAILURE_TITLES` に理由つきで足す。
EXTRA_FAILURE_TITLES = {
    # 外部の言語ファイルの却下＝**続けた**側の失敗（題に error が入らない）。
    "lang_ext_title",
}


def failure_titles() -> set:
    keys = {k for k in i18n._STRINGS["en"] if k.endswith("_error")}
    return keys | EXTRA_FAILURE_TITLES


def _i18n_key(node: ast.expr) -> "str | None":
    """`i18n.t("x")` なら `"x"`（それ以外は `None`）。"""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "t"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "i18n"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)):
        return node.args[0].value
    return None


def _is_on_type_body(node: ast.expr) -> bool:
    """本文が型に乗っている 2 形のどちらかか。"""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        # ① `failure.message(...)` / `failure.explain(...)` / `failure.listing(...)`
        if (isinstance(node.func.value, ast.Name)
                and node.func.value.id == "failure"):
            return True
        # ② `i18n.t("...").format(...)`
        if node.func.attr == "format" and _i18n_key(node.func.value) is not None:
            return True
    # ② `i18n.t("...")`
    return _i18n_key(node) is not None


def _alert_calls(tree: ast.AST):
    """`alert(...)` / `_alert(...)` の呼び出しを (題ノード, 本文ノード, 行) で返す。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else \
            (func.id if isinstance(func, ast.Name) else "")
        if name not in ("alert", "_alert"):
            continue
        if len(node.args) < 2:
            continue
        yield node.args[-2], node.args[-1], node.lineno


def scan(source: str, where: str) -> list:
    """型から外れた失敗ダイアログを返す（`(場所, 本文の式)` の並び）。"""
    titles = failure_titles()
    out = []
    for title, body, lineno in _alert_calls(ast.parse(source)):
        if _i18n_key(title) not in titles:
            continue
        if not _is_on_type_body(body):
            out.append((f"{where}:{lineno}", ast.unparse(body)))
    return out


# ============================================================
# ① 実装の全数（これが本番のゲート）
# ============================================================
def test_every_failure_dialog_is_built_from_the_type():
    offenders = []
    for path in sorted(VIEWS.glob("*.py")):
        offenders += scan(path.read_text(encoding="utf-8"), path.name)
    assert not offenders, (
        "失敗ダイアログの本文がその場で組まれている（I-100 の型を通していない）: "
        f"{offenders}"
    )


# ============================================================
# ② ゲート自身の検査（一度も落ちない／毎回鳴る／間違ったものを要求している）
# ============================================================
def test_the_scanner_catches_the_original_form():
    """I-100 以前の実際の書き方が赤になること（走査が死んだら気づける）。"""
    src = 'dialogs.alert(self, i18n.t("dlg_error"), str(e))'
    assert scan(src, "x") == [("x:1", "str(e)")]


@pytest.mark.parametrize("src", [
    'dialogs.alert(self, i18n.t("dlg_error"), failure.explain(e, what=w, hint=h))',
    'dialogs.alert(self, i18n.t("dlg_input_error"), i18n.t("mh_err_no_map"))',
    'dialogs.alert(self, i18n.t("dlg_input_error"), '
    'i18n.t("mh_err_too_many").format(max=9))',
    # 成功の題は対象外＝毎回鳴らせない
    'dialogs.alert(self, i18n.t("dlg_success"), str(path))',
])
def test_the_scanner_leaves_innocent_forms_alone(src):
    assert scan(src, "x") == []


def test_the_title_set_is_not_empty_and_covers_the_known_failures():
    """題の集合が空になれば①は何も見ていない（緑のまま無力化する形）。"""
    titles = failure_titles()
    for key in ("dlg_error", "dlg_import_error", "dlg_export_error",
                "dlg_save_error", "dlg_batch_error", "dlg_input_error",
                "dlg_common_cfg_error",
                "dlg_unexpected_error"):
        assert key in titles, f"失敗の題が集合から漏れている: {key}"


# ============================================================
# ③ 型そのもの
# ============================================================
class TestTheType:
    def test_the_next_step_is_required(self):
        """**次に何をすべきかが空の失敗メッセージは作れない**＝型の芯。"""
        with pytest.raises(ValueError):
            failure.message(what="壊れました。", hint="")

    def test_what_happened_is_required(self):
        with pytest.raises(ValueError):
            failure.message(what="  ", hint="やり直してください。")

    def test_the_three_paragraphs_come_in_order(self):
        msg = failure.message(what="A。", why="B。", hint="C。", detail="D")
        first, second, third = msg.split("\n\n")
        assert first.startswith("A。") and first.endswith("B。")
        assert second == "C。"
        assert third.endswith("D") and "D" not in second

    def test_the_details_are_dropped_when_there_are_none(self):
        assert failure.message(what="A。", hint="C。") == "A。\n\nC。"

    def test_an_exception_keeps_its_class_name_in_the_details(self):
        msg = failure.explain(PermissionError("[Errno 13] denied"),
                              what="A。", hint="C。")
        assert "PermissionError" in msg and "[Errno 13] denied" in msg
        # 最初に読むのは「何が起きた」＝例外の字は先頭に来ない
        assert msg.startswith("A。")

    def test_a_message_already_on_the_type_is_not_wrapped_again(self):
        """`err_dem_unreachable` の上に「実行できませんでした」を重ねない。"""
        from core import simulation as sim
        i18n.set_lang("ja")
        exc = sim.DemUnreachableError(i18n.t("err_dem_unreachable"))
        assert failure.explain(exc, what="包んだ。", hint="やり直す。") == str(exc)

    def test_a_truncated_listing_says_how_many_were_dropped(self):
        """切り詰めたことを黙らない（以前の `errors[:10]` は黙って捨てていた）。"""
        i18n.set_lang("ja")
        out = failure.listing([f"e{i}" for i in range(13)])
        lines = out.splitlines()
        assert len(lines) == 11 and lines[:10] == [f"e{i}" for i in range(10)]
        assert "3" in lines[-1]

    def test_a_short_listing_is_left_alone(self):
        assert failure.listing(["a", "b"]) == "a\nb"


# ============================================================
# ④ 語彙（次の一手は閉じた集合）
# ============================================================
class TestTheVocabulary:
    def test_every_fix_key_exists_in_both_languages(self):
        for key in failure.FIX_KEYS:
            for lang in ("en", "ja"):
                assert key in i18n._STRINGS[lang], f"{lang} に {key} が無い"

    def test_the_closed_set_is_what_the_code_actually_uses(self):
        """コードが引く `fix_*` が語彙の外に増えていないこと。

        ⚠️ **増やすこと自体は禁じない**＝既存のどれでも言えない一手が出たら
        `FIX_KEYS` に足す。ここが縛るのは「足したのに宣言し忘れる」形。
        """
        used = set()
        for folder in ("views", "core", "report"):
            for path in sorted((REPO / folder).glob("*.py")):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    key = _i18n_key(node) if isinstance(node, ast.Call) else None
                    if key and key.startswith("fix_"):
                        used.add(key)
        assert used <= set(failure.FIX_KEYS), (
            f"閉じた語彙の外の `fix_*` を使っている: {sorted(used - set(failure.FIX_KEYS))}")

    def test_every_fix_key_is_actually_used(self):
        """使われない一手を並べない（語彙は在庫ではなく実際の選択肢）。"""
        used = set()
        for folder in ("views", "core", "report"):
            for path in sorted((REPO / folder).glob("*.py")):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    key = _i18n_key(node) if isinstance(node, ast.Call) else None
                    if key and key.startswith("fix_"):
                        used.add(key)
        assert set(failure.FIX_KEYS) <= used, (
            f"宣言だけで使われていない一手: {sorted(set(failure.FIX_KEYS) - used)}")


# ============================================================
# ⑤ 画面へ出る失敗の字が i18n を通っていること（CSV 取り込み＝I-100 で移した面）
# ============================================================
def test_csv_import_errors_are_not_written_in_english_in_the_code():
    """`report/batch.py` の取り込み検証が i18n を迂回していないこと。

    2026-08-19 まで `"Row 3: 'id' is empty."` のような英語が直接 `raise` され、
    日本語表示のまま**そこだけ英語**でダイアログに出ていた（B-066 と同クラス）。
    """
    src = (REPO / "report" / "batch.py").read_text(encoding="utf-8")
    offenders = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        for arg in node.exc.args:
            if isinstance(arg, (ast.Constant, ast.JoinedStr)):
                offenders.append(ast.unparse(node))
    assert not offenders, f"画面へ出る失敗の字が i18n を通っていない: {offenders}"
