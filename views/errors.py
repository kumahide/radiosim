"""
views/errors.py
===============
Tk のコールバック内で起きた**未捕捉例外**を、ログとユーザーへ必ず届ける。

なぜ要るか（I-059 / B-036・2026-08-03）
--------------------------------------
`root.after(...)` やウィジェットのコマンドから呼ばれる関数で例外が出ると、Tk は
既定で `report_callback_exception` → **stderr へ traceback を書くだけ**で握り潰す。
**windowed の exe には stderr が無い**ので、実機ではどこにも何も残らない。

2.6RC1 の実機では、これが「グラフを準備中…」のまま**固まったように見える**症状に
なった＝`show_graph` が `ModuleNotFoundError` で落ち、ラベルを「準備完了」へ戻す行に
到達しないまま、例外の存在自体が消えた。**現象と原因の距離が最大化される**形。

設計
----
- **例外は 5 フロー全部で同じ穴**（単一 / バッチ / 地図 / 条件探索 / 中継経路はどれも
  `after` かポンプ経由でハンドラを回す）。**塞ぐのは root の 1 か所**＝呼び出し側を
  1 つずつ直さない（[[feedback-promote-recurring-checks]] 実証10）。
- ログには **traceback 付き**で残す（`exc_info`）。1 行のメッセージだけでは、今回の
  `No module named 'timeit'` のように**誰が投げたかが分からない**。
- ダイアログは**ログの場所を必ず書く**＝利用者が持ち帰れるものを示す。
- **再入防止**＝ダイアログ表示中に次の例外が来ても積み上げない（描画のたびに例外が
  出る種類の故障では、モーダルが無限に生えて操作不能になる）。
"""

from __future__ import annotations

import tkinter as tk
from typing import Any

import config
import i18n
from views import dialogs


def install(root: tk.Tk) -> None:
    """`root` 配下の Tk コールバック例外をログ＋ダイアログへ流すようにする。"""
    state = {"showing": False}

    def _report(exc_type: type[BaseException], exc_value: BaseException,
                tb: Any) -> None:
        # まずログ。ダイアログ側で何が起きてもここは通っている状態にする。
        config.logger.error(
            "Unhandled exception in Tk callback",
            exc_info=(exc_type, exc_value, tb),
        )
        if state["showing"]:
            return
        state["showing"] = True
        try:
            dialogs.alert(
                root,
                i18n.t("dlg_unexpected_error"),
                i18n.t("dlg_unexpected_error_msg").format(
                    error=f"{exc_type.__name__}: {exc_value}",
                    log=config.LOG_FILE,
                ),
            )
        except Exception:
            # ダイアログすら出せない状況（終了処理中など）でも、ログは残っている。
            config.logger.exception("Failed to show the error dialog")
        finally:
            state["showing"] = False

    root.report_callback_exception = _report  # type: ignore[method-assign]
