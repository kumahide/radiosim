"""
core/runtime_env.py
===================
**いま走っている実行系が、宣言された環境かどうか**を答えるだけのモジュール。

なぜ要るか
----------
この開発環境は「検証にもビルドにも使う唯一の Python」を `RADIOSIM_PYTHON` で
**宣言する**方式（2.6a1・B-020）。ところが門は 2 か所にしか無く、**起動には無かった**：

  - `build.bat`      → 未設定なら即中止
  - `pytest`         → `tests/conftest.py` が宣言と食い違えば収集前に停止
  - `python main.py` → **何も見ていない**

この機では素の `python` にも依存が入っており、**版だけが違う**（実測 2026-08-08＝
どちらも Python 3.14.4 だが matplotlib は宣言環境 3.11.1 / 素の python 3.10.9）。
同じ版の Python なのでエラーも警告も出ず起動する ⇒ **「動いたから同じ」が成り立たない**。
これは B-020（検証した matplotlib と exe に入った matplotlib が食い違ったまま
`2.6RC1` を出荷した）とまったく同じ形。

⛔ **起動は止めない**（警告だけ）
--------------------------------
配布した exe には環境変数が無い＝`declared_interpreter()` は空になるので、この
モジュールは**利用者の起動には一切関与しない**。それでも「宣言と違えば止める」
実装にしないのは、**開発機で exe を試すとき**（`RADIOSIM_PYTHON` は設定されて
いて、走っているのは exe）に**利用者と同じ経路を塞いでしまう**から。
⇒ `frozen` は最初に除外し、判定は「宣言がある × 素の Python × 食い違い」だけ。

⚠️ **規則をここ 1 か所に置く**＝正規化（大小・区切り・シンボリックリンク）を
`conftest` と `main.py` が別々に書くと、片方だけ直したときに**同じ質問に 2 つの
答え**が出る（B-046 の「同じ 21 文字が 3 か所」と同型）。
"""

from __future__ import annotations

import os
import sys

#: 宣言に使う環境変数（`build.bat` / `conftest.py` と同じ 1 つ）。
DECLARED_ENV = "RADIOSIM_PYTHON"


def declared_interpreter() -> str:
    """宣言された python.exe のパス。**宣言が無ければ空文字**。

    引用符と前後の空白を落とす＝`setx RADIOSIM_PYTHON "D:\\...\\python.exe"` の
    ように引用符ごと入る設定を実際に踏むため。
    """
    return os.environ.get(DECLARED_ENV, "").strip().strip('"')


def same_interpreter(a: str, b: str) -> bool:
    """2 つのパスが同じ実行系を指すか（表記ゆれを吸収する）。

    大小（Windows）・区切り・相対・シンボリックリンクを解いてから比べる。
    """
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def interpreter_mismatch() -> tuple[str, str] | None:
    """宣言と食い違っているなら `(宣言, 実行中)`、問題なければ `None`。

    `None` を返す条件は 3 つ＝①凍結（配布 exe）②宣言が無い（CI・他マシンの
    clone）③宣言と一致。⚠️ **宣言先が存在しないケースは食い違いとして返す**
    （`conftest` 側が「宣言が壊れているなら止める」と決めたのと同じ判断＝黙って
    無効になる門を作らない）。
    """
    if getattr(sys, "frozen", False):
        return None
    declared = declared_interpreter()
    if not declared:
        return None
    running = sys.executable
    if os.path.exists(declared) and same_interpreter(declared, running):
        return None
    return declared, running


def mismatch_message(declared: str, running: str) -> str:
    """食い違いを人に見せる 1 つの文面（ログにも stderr にも同じ字を出す）。"""
    return (
        f"宣言された Python で起動していません（{DECLARED_ENV}）。\n"
        f"  宣言 : {declared}\n"
        f"  実行 : {running}\n"
        "依存ライブラリの版が宣言環境と違う可能性があります"
        "（同じ Python でも版だけ違う状態は警告なしに起こります）。"
        f'正式な起動は  & "$env:{DECLARED_ENV}" main.py  です。'
    )
