"""
core/failure.py
===============
**失敗の伝え方の型**（I-100）。画面へ出す失敗メッセージを、ここ 1 か所で組む。

なぜ要るか
----------
失敗の伝え方が経路ごとにばらばらだった。**1 件ずつ潰すと、潰した数だけ書き方が
増える**ので、型を決めて全部そこへ乗せる（設計哲学⑧＝一貫性は人の速度。読む人が
毎回「これは何を言っているのか」を組み立て直さずに済む）。

型は既にあった 2 つの先例から抽出した:

- `err_dem_unreachable` … **起きたこと＋止めた理由＋次の一手**を全部持つ。最良の先例。
- `dlg_unexpected_error_msg` … 起きたこと＋**詳細の在り処**を持つ（次の一手が無い）。
  ⚠️ このキーは I-100 で退けた＝1 枚の型板に戻すと、また次の一手を書き忘れられる。

⇒ **段落 3 つ**で表す:

1. **何が起きた**（＋なぜ止めた／続けた）
2. **次に何をすべきか** ← **必須**。無いのが全経路に共通の欠けだった。
3. **詳細**（例外の原文・ログの場所）＝診断は捨てないが、**先に読むのは 1 と 2**。

⚠️ **次の一手は閉じた語彙から選ぶ**（`fix_*` キー）。1 件ずつ文を書かせると、
また書き方が増える＝この項目が潰したかった欠陥そのものになる。

⚠️ **既に型に乗っている例外は包み直さない**＝`UserFacingError` を継承していれば
`explain()` はその文をそのまま通す。包むと「地形データを取得できませんでした」の
上に「実行に失敗しました」がもう 1 枚かぶり、**同じことを 2 回言う**。
"""

from __future__ import annotations

from core import i18n

#: 「次に何をすべきか」に使ってよいキー（閉じた語彙）。
#: **足すのは、既存のどれでも言えない一手が出てきたときだけ。**
FIX_KEYS = (
    "fix_check_input",     # 入力を直す
    "fix_file_write",      # 保存先を確かめる
    "fix_file_read",       # 開いたファイルを確かめる
    "fix_retry_or_log",    # もう一度試し、続くならログを添えて報告
    "fix_edit_lang_file",  # 言語ファイルの該当キーを直す
)
# ⚠️ **ネットワーク／プロキシの一手は語彙に置いていない**＝その文は
# `err_dem_unreachable` の中に既にあり（型の出所そのもの）、取り出して別キーに
# すると同じ字が 2 か所に増える。取り出すのは 2 件目の使い道が出たとき。


class UserFacingError(Exception):
    """**メッセージが既にこの型に乗っている**例外（そのまま画面へ出してよい）。

    これを継承していない例外は「内部の診断」であって、画面の文ではない。
    `explain()` はその区別だけを見る。
    """


def message(*, what: str, hint: str, why: str = "", detail: str = "") -> str:
    """型に沿った失敗メッセージを組む。

    Parameters
    ----------
    what : 何が起きたか（1 文・必須）
    hint : 次に何をすべきか（`i18n.t(fix_*)` の文・必須）
    why  : なぜ止めた／続けたか（1 文・省略可＝`what` に含まれている場合）
    detail : 詳細（例外の原文・ログの場所など・省略可）
    """
    if not what.strip():
        raise ValueError("failure.message: 何が起きたかが空")
    if not hint.strip():
        # ⚠️ ここが型の芯＝**次の一手が無い失敗メッセージを作れないようにする**。
        raise ValueError("failure.message: 次に何をすべきかが空")
    head = what.strip()
    if why.strip():
        # ⚠️ 文と文のあいだの空白は**訳語ではなく組版の規則**＝キーにしない
        # （空文字の訳キーは `test_config.TestI18n.test_no_empty_values` が
        # 「空の翻訳値」として弾く。実際に弾かれた）。句点で終わる言語は
        # 詰め、ピリオド等で終わる言語は 1 つ空ける。
        head += ("" if head[-1] in "。！？" else " ") + why.strip()
    parts = [head, hint.strip()]
    if detail.strip():
        parts.append(i18n.t("fail_detail_label") + "\n" + detail.strip())
    return "\n\n".join(parts)


def explain(exc: BaseException, *, what: str, hint: str, why: str = "") -> str:
    """例外を画面の文にする。**既に型に乗った例外はそのまま通す。**

    包む場合、例外の原文は**捨てずに段落 3 へ落とす**（`PermissionError:
    [Errno 13] ...` のような字は診断としては価値があるが、最初に読む字ではない
    ＝2026-08-19 ユーザー決定）。
    """
    if isinstance(exc, UserFacingError):
        return str(exc)
    return message(what=what, hint=hint, why=why,
                   detail=f"{type(exc).__name__}: {exc}")


def listing(lines: list[str], limit: int = 10) -> str:
    """検証メッセージの並びを本文にする（入力の型＝1 行 1 件）。

    ⚠️ **切り詰めたことを黙らない**＝以前は `errors[:10]` で静かに捨てており、
    11 件目以降は画面にもログにも出ない状態だった（型の「詳細はどこ」が欠ける）。
    """
    shown = [ln for ln in lines[:limit]]
    rest = len(lines) - len(shown)
    if rest > 0:
        shown.append(i18n.t("verr_more").format(n=rest))
    return "\n".join(shown)
