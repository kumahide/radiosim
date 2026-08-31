"""
version.py
==========
アプリケーション名・バージョン・著作権の一元管理。

バージョンを上げる際はここだけ編集すればよい。
"""

import re

APP_NAME    = "RadioSim Pro"
APP_VERSION = "3.0"
APP_FULL    = f"{APP_NAME} {APP_VERSION}"
COPYRIGHT   = "© 2026 BearValley AI Craftworks. All rights reserved."
USER_AGENT  = f"Mozilla/5.0 RadioSim/{APP_VERSION}"

# 正式版に与える 4 つ目の数。**プレリリースの通し番号より必ず大きい**こと以外に
# 意味は無い（RC は 1 桁台なので、桁を分けておけば衝突しない）。
_FINAL_RANK = 9999


def version_tuple(v: str = APP_VERSION) -> tuple[int, int, int, int]:
    """版の字を Windows の 4 数字バージョンへ直す（`radiosim.spec` の EXE 情報が使う）。

    `'2.0'` → `(2, 0, 0, 9999)` ／ `'2.1.3'` → `(2, 1, 3, 9999)` ／
    `'2.0RC2'` → `(2, 0, 0, 2)` ／ `'3.0a1'` `'3.0b1'` → `(3, 0, 0, 0)`。

    🔴 **4 つ目は「段階の順番」で、正式版が最大**（B-162）。以前は正式版を 0 に
    しており、**`3.0RC3`＝(3,0,0,3) のほうが `3.0`＝(3,0,0,0) より新しい**と
    読める状態だった＝**文字列の版は正しいのに数字の版だけが逆走**していた
    （Windows の資産管理・配布ツールが見るのはこの 4 数字のほう）。
    ⚠️ **a/b は 0 のまま**＝配布しない段階なので RC より下で順序として正しい。
    """
    m = re.match(r"(\d+)\.(\d+)(?:\.(\d+))?(?:(RC)(\d+))?", v)
    if not m:
        return (0, 0, 0, 0)
    if m.group(4):                       # RC＝通し番号をそのまま
        rank = int(m.group(5))
    elif re.search(r"\d[ab]\d+$", v):    # alpha / beta＝配布しないので最下位
        rank = 0
    else:
        rank = _FINAL_RANK               # 正式
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), rank)
