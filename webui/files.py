"""
webui/files.py
==============
新しい画面の「設定」の枠の **「ファイル」の 4 つと訳のひな形**＝旧画面のメニュー「ファイル」と
「設定」の、ファイルや OS へ出る項目（I-257 の C）。

- パラメータ読込（`sim_changes`）＝JSON の sim の鍵だけを入力の変更にする（app の鍵は捨てる）。
- アプリ設定読込（`settings.apply_many`）＝app の鍵のうち「設定」の枠が持つものだけ。
- 結果フォルダを開く（`open_results`）＝無ければ作ってから開く（B-273）。Link は結果フォルダへ
  書かない＝3.x の結果と `settings.json` を探す口。
- 全キャッシュ削除（`cache_breakdown`・`cache_delete`）＝DEM のソースが 2 つ以上のときだけ
  ソースごとに選ぶ（I-155・I-153 の「選択肢が 1 つなら出さない」）。
- 訳のひな形（`write_lang_template`）＝ブラウザの保存の画面は初期フォルダを選べない（`startIn` は
  既知のフォルダだけ）＝`USER_LANG_DIR` へ直に書き、上書きはしない（`_template (2).json`…）。

⚠️ 起動の経路で網の部品を引かない（`test_the_startup_path_does_not_load_the_network_parts`）＝
`dem_cache` は関数の中で取り込む。
"""

from __future__ import annotations

import os
from typing import Any, Callable

from core import config
from core import dem_sources
from core import failure
from core import i18n


class FileError(ValueError):
    """画面へそのまま出す 1 文を持つ失敗（`str(e)` が字）。"""


def _choice_values(key: str) -> "frozenset[str] | None":
    """選択式の入力の選べる値（文字で打つ欄は None）。`server._choices` と同じ集合。"""
    if key == "env_type":
        return config.VALID_ENV_TYPES
    if key == "diff_method":
        return config.VALID_DIFF_METHODS
    if key == "resolution":
        return config.VALID_RESOLUTIONS
    if key == "dem_source":
        return frozenset(s.source_id for s in dem_sources.all_sources())
    return None


def load_object(text: str) -> dict:
    """読み込む設定ファイルの本文 → dict（JSON でない・オブジェクトでないは `FileError`）。"""
    import json
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise ValueError("not a JSON object")
    except ValueError as e:
        raise FileError(failure.explain(e, what=i18n.t("fail_load_settings"),
                                        hint=i18n.t("fix_file_read"))) from e
    return obj


def sim_changes(text: str) -> "dict[str, str]":
    """パラメータ読込＝JSON の本文 → 入力の変更（`worker.submit` へ渡す）。

    - sim の鍵だけ（`config.select_sim`＝app の鍵が混ざったファイルでもテーマ等を取り込まない）。
    - 値は文字列・整数・小数だけを `str` にする（旧 `_apply_sim_config` と同じ＝真偽や入れ子は捨てる）。
    - 選択式の知らない値は既定の値へ（旧画面は欄の既定の語へ落としていた）。回折の旧名は新名へ。
    """
    changes: dict[str, str] = {}
    for key, value in config.select_sim(load_object(text)).items():
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            continue
        text_value = str(value)
        if key == "diff_method":
            text_value = config.DIFF_METHOD_ALIASES.get(text_value, text_value)
        allowed = _choice_values(key)
        if allowed is not None and text_value not in allowed:
            text_value = config.DEFAULT_CONFIG[key]
        changes[key] = text_value
    return changes


def open_results(opener: "Callable[[str], None]") -> None:
    """結果フォルダを開く（無ければ作る）。作れなければ `FileError`（旧と同じ字）。"""
    try:
        os.makedirs(config.RESULTS_DIR, exist_ok=True)
    except OSError as e:
        raise FileError(failure.explain(e, what=i18n.t("fail_open_results"),
                                        hint=i18n.t("fix_file_write"))) from e
    opener(config.RESULTS_DIR)


def _size_text(size_bytes: int) -> str:
    return i18n.t("tm_cache_size_suffix").format(mb=f"{size_bytes / (1024 * 1024):.1f}")


def cache_breakdown() -> "dict[str, Any]":
    """全キャッシュ削除の確認の中身＝字・ソースごとの容量（2 つ以上のときだけ選ばせる）。"""
    from core import dem_cache
    sources = list(dem_sources.all_sources())
    out: dict[str, Any] = {"title": i18n.t("tm_delete_all_title"),
                           "confirm": i18n.t("tm_delete_all_confirm"),
                           "ok": i18n.t("dlg_ok"), "cancel": i18n.t("btn_cancel"),
                           "sources": [], "basemap": None}
    if len(sources) <= 1:
        return out
    breakdown = dem_cache.get_cache_breakdown()
    size = {s["source_id"]: s["size_bytes"] for s in breakdown["sources"]}
    out["sources"] = [{"id": s.source_id,
                       "label": s.display_name + _size_text(size.get(s.source_id, 0))}
                      for s in sources]
    out["basemap"] = i18n.t("tm_delete_all_basemap") + _size_text(breakdown["basemap"]["size_bytes"])
    return out


def cache_delete(source_ids: "list[str] | None", basemap: bool) -> str:
    """全キャッシュ削除＝消して完了の 1 文を返す。

    ソースが 1 つ（選ばせていない）なら引数なしで全部（旧と同じ）。選ばせたときは選んだソースと
    背景地図だけ（知らない ID は無視）。
    """
    from core import dem_cache
    sources = list(dem_sources.all_sources())
    if source_ids is None or len(sources) <= 1:
        result = dem_cache.delete_all_tile_cache()
    else:
        chosen = [s for s in sources if s.source_id in set(source_ids)]
        result = dem_cache.delete_all_tile_cache(sources=chosen, include_basemap=bool(basemap))
    return i18n.t("tm_delete_all_done").format(deleted=result["deleted"])


def _free_template_path(folder: str) -> str:
    """`_template.json`・`_template (2).json`…のうち、まだ無い最初の名前（上書きしない）。"""
    path = os.path.join(folder, "_template.json")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"_template ({n}).json")
        n += 1
    return path


def write_lang_template(opener: "Callable[[str], None]") -> str:
    """訳のひな形を `USER_LANG_DIR` へ書き、そのフォルダを開いて完了の 1 文を返す（I-109・I-130）。"""
    try:
        os.makedirs(config.USER_LANG_DIR, exist_ok=True)
        path = _free_template_path(config.USER_LANG_DIR)
        i18n.write_template(path)
    except OSError as e:
        raise FileError(failure.explain(
            e, what=i18n.t("fail_save_lang_template"), why=i18n.t("fail_why_stopped"),
            hint=i18n.t("fix_file_write"))) from e
    opener(config.USER_LANG_DIR)
    return i18n.t("dlg_lang_template_saved").format(path=path)
