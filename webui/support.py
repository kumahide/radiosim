"""
webui/support.py
================
新しい画面の「設定」の枠の **「ヘルプ」の口**＝旧画面のメニュー「ヘルプ」の、更新の確認と
診断パッケージ（I-257 の D）。説明書を開くのは `webui/docs.py`、「この版について」の字は
`webui/settings.py` の `labels`。

- 更新の確認（`check_updates`）＝押したときだけ・同期で打つ（サーバーは要求ごとにスレッド）。
  最新・新しい版・失敗のどれも画面の 1 文で返す（旧は窓で出していた）。
- 診断パッケージ（`diagnostics_form`・`diagnostics_zip`）＝保存前に何が入るかを見せて選ばせる
  （環境事実は既定でチェック済み・`results/` の中身は**既定でチェックなし**＝顧客の案件情報）。
  zip は一時フォルダで組み、バイト列を画面へ返す（保存の画面は画面が出す）。
  🔒 画面から来た名前は `FACT_ITEMS` と `list_result_runs()` の中だけ受ける＝結果フォルダの外の
  名前（`..\\…`）を zip へ入れさせない。

⚠️ 起動の経路で網の部品を引かない（`test_the_startup_path_does_not_load_the_network_parts`）＝
`update_check`・`diagnostics`（`core.dem` を引く）は関数の中で取り込む。
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any, Callable

from core import failure
from core import i18n
from webui.files import FileError


def update_failure_message(exc: BaseException) -> str:
    """更新の確認が答えを得られなかったときの本文（型＝`core/failure.py`）。"""
    kind = getattr(exc, "kind", "")
    if kind == "rate_limited":
        return failure.message(what=i18n.t("fail_update_check"),
                               why=i18n.t("fail_why_rate_limited"),
                               hint=i18n.t("fix_retry_later"))
    detail = getattr(exc, "detail", "") or f"{type(exc).__name__}: {exc}"
    hint = i18n.t("fix_network") if kind == "network" else i18n.t("fix_retry_or_log")
    return failure.message(what=i18n.t("fail_update_check"), hint=hint,
                           detail=detail)


def check_updates(cfg: "dict[str, str]", *,
                  check: "Callable[..., Any] | None" = None) -> "tuple[int, dict[str, Any]]":
    """GitHub Releases に新しい版を尋ねる（I-178 段階 1＝押したときだけ）。"""
    from core import config
    from core import update_check
    from core import version
    from webui import notices
    ask = check or update_check.check
    try:
        rel = ask(include_pre=update_check.want_prerelease(cfg))
    except update_check.UpdateCheckError as e:
        return 422, {"message": update_failure_message(e)}
    except Exception as e:                                  # 想定外＝ログに残して画面へ
        config.logger.exception("Update check failed")
        return 422, {"message": update_failure_message(e)}
    if rel is None:
        return 200, {"message": i18n.t("dlg_update_latest").format(ver=version.APP_VERSION)}
    return 200, {"message": notices.update_available(rel, auto=False)}


def diagnostics_form() -> "dict[str, Any]":
    """診断パッケージの小区画の中身＝字・環境事実の項目・結果の一覧・保存の名前。"""
    from core import diagnostics
    return {
        "title":         i18n.t("dlg_diagnostics_title"),
        "intro":         i18n.t("dlg_diagnostics_intro"),
        "facts_group":   i18n.t("dlg_diagnostics_facts_group"),
        "facts":         [{"id": item, "label": i18n.t(f"diag_item_{item}")}
                          for item in diagnostics.FACT_ITEMS],
        "results_group": i18n.t("dlg_diagnostics_results_group"),
        "results_hint":  i18n.t("dlg_diagnostics_results_hint"),
        "results":       diagnostics.list_result_runs(),
        "no_results":    i18n.t("dlg_diagnostics_no_results"),
        "save":          i18n.t("btn_save_diagnostics"),
        "cancel":        i18n.t("btn_cancel"),
        "saved":         i18n.t("dlg_diagnostics_saved"),     # `{path}` は画面が保存した名前で埋める
        "name":         f"radiosim_diagnostics_{time.strftime('%Y%m%d_%H%M%S')}.zip",
    }


def diagnostics_zip(facts: Any, results: Any) -> "tuple[int, dict[str, Any]]":
    """選んだ項目で診断 zip を組み、base64 で返す `{"zip"}`。

    `facts` は `FACT_ITEMS` の、`results` は `list_result_runs()` の部分集合だけ受ける（外は 400）。
    """
    import shutil
    import tempfile
    from core import diagnostics
    if not (isinstance(facts, list) and all(isinstance(f, str) for f in facts)
            and isinstance(results, list) and all(isinstance(r, str) for r in results)):
        return 400, {"error": "bad body"}
    if not set(facts) <= set(diagnostics.FACT_ITEMS):
        return 400, {"error": "unknown fact"}
    if not set(results) <= set(diagnostics.list_result_runs()):
        return 400, {"error": "unknown result"}
    folder = tempfile.mkdtemp(prefix="radiosim_diag_")
    try:
        path = os.path.join(folder, "diagnostics.zip")
        diagnostics.build_package(path, set(facts), list(results))
        with open(path, "rb") as f:
            data = f.read()
    except Exception as e:
        raise FileError(failure.explain(e, what=i18n.t("fail_save_diagnostics"),
                                        hint=i18n.t("fix_file_write"))) from e
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return 200, {"zip": base64.b64encode(data).decode("ascii")}
