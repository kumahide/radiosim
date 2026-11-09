"""
webui/notices.py
================
新しい画面の**起動時の知らせ**の文（I-256 の①②⑤）＝旧画面 `views/launcher.py` の
`_warn_about_rejected_*` と `views/launcher_menu.py` の `_auto_check_updates` を移したもの。
出し先は地図の上の閉じられる帯（`AppState.add_notice`）＝作業を止めない・起動のたびに出す
（2026-11-09 経営者の選択）。

- 文を組む関数は**純関数**（報告の並びを受けて文字列か `None` を返す）＝試験が直に呼ぶ。
  題は 1 行目に置く（帯は `white-space: pre-wrap`）。
- `post_startup(state)`＝読み込みの報告を見て、あれば帯へ載せる。読み込み（`i18n.load_external`・
  `dem_sources.load_from`・`tile_sources.load_from`）は `webui/__main__.py` が先に済ませる。
- `start_update_check(state, cfg)`＝起動時の更新の確認（既定オフ・1 日 1 回まで）を背景の
  スレッドで。失敗と「最新」は黙ってログだけ（頼んでいない通信の失敗で毎回知らせない）。

⚠️ 起動の経路で網の部品を引かない（`test_the_startup_path_does_not_load_the_network_parts`）＝
`update_check` は関数の中で取り込む。
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import Any, Callable

from core import failure
from core import i18n

logger = logging.getLogger("radiosim")

#: 却下の理由 → 帯に出す説明の i18n キー。⚠️ **`i18n.validate_external` が返す理由と
#: 1 対 1**（理由を足したらここも足す＝`tests/test_webui.py` が突き合わせる）。
REJECT_REASON_KEYS = {
    "artifact":    "lang_ext_why_artifact",
    "unknown":     "lang_ext_why_unknown",
    "placeholder": "lang_ext_why_placeholder",
    "not_text":    "lang_ext_why_not_text",
    "builtin":     "lang_ext_why_builtin",
    "unreadable":  "lang_ext_why_unreadable",
}


def rejected_translations(reports: list) -> "str | None":
    """外部の訳のうち採用しなかったぶんを 1 通に（`i18n.external_reports()` を受ける）。

    ⛔ ファイルごとに 1 通は出さない（置いた本数だけ並ぶと読まずに閉じる習慣がつく）。
    🔴 **実際に起きた理由だけを、起きた分だけ言う**（2026-08-16 の実機試用の報告＝
    列挙した説明は、列挙から漏れた理由が出た日に嘘になる）。補足も出た理由にだけ添える。
    """
    lines: list = []
    saw_artifact = False
    for _code, name, _ok, rejected in reports:
        if not rejected:
            continue
        by_reason: dict = {}
        for key, reason in rejected:
            by_reason.setdefault(reason, []).append(key)
        lines.append(i18n.t("lang_ext_rejected_file").format(lang=name, n=len(rejected)))
        for reason, keys in by_reason.items():
            saw_artifact = saw_artifact or reason == "artifact"
            shown = [k for k in keys[:3] if k]
            why_key = REJECT_REASON_KEYS.get(reason)
            lines.append(i18n.t("lang_ext_rejected_line").format(
                why=i18n.t(why_key) if why_key else reason,
                n=len(keys),
                keys=(", ".join(shown) + (" …" if len(keys) > len(shown) else ""))
                     if shown else "-"))
    if not lines:
        return None
    detail = "\n".join(lines)
    if saw_artifact:
        detail += "\n\n" + i18n.t("lang_ext_artifact_note")
    return i18n.t("lang_ext_title") + "\n" + failure.message(
        what=i18n.t("lang_ext_rejected"),
        hint=i18n.t("fix_edit_lang_file"),
        detail=detail,
    )


def _rejected_sources(reports: list, title: str, what: str, hint: str) -> "str | None":
    if not reports:
        return None
    # 行の書式は DEM と背景地図で同じ 1 本（`core/tile_sources.py:_validate_source` の注記）
    lines = [i18n.t("dem_src_rejected_line").format(id=source_id, why=i18n.t(reason))
             for source_id, reason in reports]
    return title + "\n" + failure.message(what=what, hint=hint, detail="\n".join(lines))


def rejected_dem_sources(reports: list) -> "str | None":
    """DEM ソースの宣言のうち使えなかったぶん（`dem_sources.load_reports()` を受ける・I-147）。"""
    return _rejected_sources(reports, i18n.t("dem_src_title"), i18n.t("dem_src_rejected"),
                             i18n.t("fix_edit_dem_sources_file"))


def rejected_tile_sources(reports: list) -> "str | None":
    """背景地図ソースの宣言のうち使えなかったぶん（`tile_sources.load_reports()`・I-152）。"""
    return _rejected_sources(reports, i18n.t("tile_src_title"), i18n.t("tile_src_rejected"),
                             i18n.t("fix_edit_tile_sources_file"))


def post_startup(state: Any) -> "list[int]":
    """読み込みの報告を見て、却下があれば帯へ `warn` で載せる。載せた知らせの番号を返す。"""
    from core import dem_sources
    from core import tile_sources
    texts = (rejected_translations(i18n.external_reports()),
             rejected_dem_sources(dem_sources.load_reports()),
             rejected_tile_sources(tile_sources.load_reports()))
    return [state.add_notice("warn", text) for text in texts if text]


def update_available(rel: Any, *, frozen: "bool | None" = None,
                     portable: "bool | None" = None) -> str:
    """起動時の確認で新しい版が見つかったときの文（I-178・I-179）。

    入れ方の 1 文は配布形ごと（インストーラ版は置き換わる・ポータブル版は並べられる・
    ソース実行はタグ）。帯にリンクの器は無い＝リリースのページの URL を文に入れる。
    """
    from core import config
    from core import runtime_env
    from core import version
    if frozen is None:
        frozen = runtime_env.is_packaged()
    if portable is None:
        portable = config.is_portable()
    if not frozen:
        how = i18n.t("update_how_source").format(tag=rel.tag)
    elif portable:
        how = i18n.t("update_how_portable")
    else:
        how = i18n.t("update_how_installer")
    return i18n.t("dlg_update_title") + "\n" + i18n.t("webui_update_available").format(
        cur=version.APP_VERSION, new=rel.version, how=how, url=rel.url,
        note=i18n.t("update_auto_note"))


def start_update_check(state: Any, cfg: "dict[str, str]", *,
                       today: "datetime.date | None" = None,
                       check: "Callable[..., Any] | None" = None) -> "threading.Thread | None":
    """起動時の更新の確認（既定オフ・1 日 1 回まで）。打ったら背景のスレッドを返す。

    試みた日は**打つ前に**保存する（`update_check.auto_due`＝失敗しても同じ日に 2 度目は
    打たない）。新しい版があれば `info` の知らせ・失敗と「最新」はログだけ。
    """
    from core import config
    from core import update_check
    day = today or datetime.date.today()
    if not update_check.auto_due(cfg, day):
        return None
    cfg["update_check_last"] = day.isoformat()
    config.save_app(cfg)
    include_pre = update_check.want_prerelease(cfg)
    ask = check or update_check.check

    def work() -> None:
        try:
            rel = ask(include_pre=include_pre)
        except update_check.UpdateCheckError as e:
            logger.info("Startup update check skipped: %s", e)
            return
        except Exception:                                # 想定外＝ログに残して黙る
            logger.exception("Startup update check failed")
            return
        if rel is not None:
            state.add_notice("info", update_available(rel))

    thread = threading.Thread(target=work, name="update-check", daemon=True)
    thread.start()
    return thread
