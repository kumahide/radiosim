"""
webui/settings.py
=================
新しい画面の **「設定」の枠**＝旧画面のメニュー「設定」の項目のうち、画面から変えるもの（I-257）。

- `payload(cfg)`＝今の値と選べる値（画面はこれだけで枠を組む＝字はサーバーが渡す）。
- `apply(cfg, key, value)`＝検査 → `cfg` を書き換えて `config.save_app`。返り値は
  「読み直しが要るか」（言語を替えたとき＝画面の字は起動時の表から組む）。

扱う鍵（`KEYS`）＝言語・テーマ・座標の表記・プロキシ・起動時の更新の確認・プレリリースも知らせるか。
- 座標の表記（`coord_format`＝`dd`／`dms`）＝ピンを動かしたときに欄へ書き戻す表記と、替えたときの
  端点の欄の書き換え（`app.js`）。欄はどちらの表記で書いても読む（2026-11-09 経営者の選択）。
- 背景地図（`basemap_layer`）＝淡色・航空写真＋宣言ファイルで足した背景地図（表示名は訳さない）。
  サーバーが地図の PNG の背景も替え、画面は地図のタイルを張り直す（I-257 の C）。

⚠️ 起動の経路で網の部品を引かない（`test_the_startup_path_does_not_load_the_network_parts`）＝
`dem`・`simulation`・`update_check` は関数の中で取り込む。
"""

from __future__ import annotations

from typing import Any

from core import config
from core import i18n
from core import version

#: 画面が変える鍵（並びは枠の並び）。
KEYS = ("lang", "theme", "coord_format", "basemap_layer", "proxy_url", "update_check_auto",
        "update_check_prerelease")

#: テーマの値と字の鍵（旧メニューと同じ 3 つ）。
THEMES = (("system", "menu_system"), ("light", "menu_light"), ("dark", "menu_dark"))

#: 座標の表記の値と字の鍵（旧メニューと同じ 2 つ）。
COORD_FORMATS = (("dd", "coord_fmt_dd"), ("dms", "coord_fmt_dms"))

#: 組み込みの背景地図の値と字の鍵（旧画面の地図の窓と同じ 2 つ）。
BASEMAPS = (("pale", "map_layer_pale"), ("photo", "map_layer_photo"))

_ON_OFF = ("on", "off")


class SettingError(ValueError):
    """知らない鍵・選べる値の外・形の違う値。"""


def languages() -> "list[list[str]]":
    """言語の選択肢 `[[コード, 表示名], …]`＝同梱の 2 つ ＋ 読み込めた外部訳。"""
    return [["en", i18n.t("lang_en")], ["ja", i18n.t("lang_ja")],
            *([code, name] for code, name in i18n.external_languages())]


def basemaps() -> "list[list[str]]":
    """背景地図の選択肢 `[[値, 表示名], …]`＝組み込み 2 つ ＋ 宣言ファイルのソース（名前は訳さない）。"""
    from core import tile_sources
    return [*([v, i18n.t(k)] for v, k in BASEMAPS),
            *([s.source_id, s.display_name] for s in tile_sources.all_sources())]


def payload(cfg: "dict[str, str]") -> "dict[str, Any]":
    """枠の今の値・選択肢・字。`update_check_prerelease` は版に追従する既定を真偽に畳む。"""
    from core import update_check
    return {
        "values": {
            "lang": cfg.get("lang", "en"),
            "theme": cfg.get("theme", "system"),
            "coord_format": cfg.get("coord_format", "dd"),
            "basemap_layer": cfg.get("basemap_layer", "pale"),
            "proxy_url": cfg.get("proxy_url", ""),
            "update_check_auto": cfg.get("update_check_auto") == "on",
            "update_check_prerelease": update_check.want_prerelease(cfg),
        },
        "choices": {
            "lang": languages(),
            "theme": [[v, i18n.t(k)] for v, k in THEMES],
            "coord_format": [[v, i18n.t(k)] for v, k in COORD_FORMATS],
            "basemap_layer": basemaps(),
        },
        "labels": {
            "title": i18n.t("menu_settings"),
            "display": i18n.t("settings_display"),
            "network": i18n.t("settings_network"),
            "lang": i18n.t("menu_language"),
            "theme": i18n.t("menu_theme"),
            "coord_format": i18n.t("lbl_coord_format"),
            "basemap_layer": i18n.t("map_layer_label"),
            "proxy_url": i18n.t("dlg_proxy_url_label"),
            "proxy_hint": i18n.t("dlg_proxy_hint"),
            "update_check_auto": i18n.t("menu_check_updates_auto"),
            "update_check_prerelease": i18n.t("menu_check_updates_pre"),
            "lang_template": i18n.t("btn_lang_template"),
            "file": i18n.t("menu_file"),
            "load_sim": i18n.t("menu_load_settings"),
            "load_app": i18n.t("menu_load_app_settings"),
            "delete_cache": i18n.t("menu_delete_all_cache"),
            "help": i18n.t("menu_help"),
            "open_docs": i18n.t("menu_open_readme"),
            "diagnostics": i18n.t("menu_diagnostics"),
            "check_updates": i18n.t("menu_check_updates"),
            "about": i18n.t("menu_about"),
            "about_text": i18n.t("dlg_about_msg").format(
                app=version.APP_NAME, ver=version.APP_VERSION, copy=version.COPYRIGHT),
            "close":i18n.t("dlg_ok"),          # 1 つ替えるごとに保存する＝閉じるだけ
        },
    }


def _check(key: str, value: Any) -> str:
    """検査して `cfg` に書く文字列へ直す（形の違いは `SettingError`）。"""
    if key not in KEYS:
        raise SettingError(f"unknown setting: {key!r}")
    if key == "lang":
        if value not in i18n.available_languages():
            raise SettingError(f"unknown language: {value!r}")
        return value
    if key == "theme":
        if value not in {v for v, _k in THEMES}:
            raise SettingError(f"unknown theme: {value!r}")
        return value
    if key == "coord_format":
        if value not in {v for v, _k in COORD_FORMATS}:
            raise SettingError(f"unknown coord_format: {value!r}")
        return value
    if key == "basemap_layer":
        if value not in {v for v, _t in basemaps()}:
            raise SettingError(f"unknown basemap_layer: {value!r}")
        return value
    if key == "proxy_url":
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise SettingError("proxy_url: not a one-line string")
        return value.strip()
    # 起動時の確認・プレリリース＝画面は真偽で送る。触った時点で on/off を固定する（I-180）。
    if not isinstance(value, bool):
        raise SettingError(f"{key}: not a boolean")
    return _ON_OFF[0] if value else _ON_OFF[1]


#: アプリ設定読込で取り込む鍵（ファイルは `on`/`off` の字で持つ起動時の確認は取り込まない＝旧と同じ）。
FILE_KEYS = ("lang", "theme", "coord_format", "basemap_layer", "proxy_url")


def apply_many(cfg: "dict[str, str]", values: dict) -> "tuple[bool, bool]":
    """アプリ設定読込＝`FILE_KEYS` のうち選べる値だけを 1 回で保存して効かせる（旧
    `_on_load_app_settings`）。返り値＝(1 つでも取り込んだか, 画面の読み直しが要るか)。

    選べない値は黙って捨てる（他の鍵は取り込む）。1 つも無ければ保存しない。言語の種は
    言語を取り込んだときだけ消費済みにする（B-299）。
    """
    from core import dem
    from core import simulation as sim
    got: dict[str, str] = {}
    for key in FILE_KEYS:
        if key not in values:
            continue
        try:
            got[key] = _check(key, values[key])
        except SettingError:
            continue
    if not got:
        return False, False
    reload = "lang" in got and got["lang"] != cfg.get("lang")
    cfg.update(got)
    if config.save_app(cfg) and "lang" in got:
        config.consume_lang_seed()
    if "lang" in got:
        i18n.set_lang(got["lang"])
    if "proxy_url" in got:
        dem.set_proxy(got["proxy_url"])
        sim.clear_terrain_cache()
    return True, reload


def apply(cfg: "dict[str, str]", key: str, value: Any) -> bool:
    """1 つの設定を検査して保存し、効かせる。返り値＝画面の読み直しが要るか。

    - 言語＝保存が通ったら種を消費済みにする（B-299）。画面の字はサーバーが起動時の表から
      渡す＝その場で `set_lang` して読み直させる。
    - プロキシ＝`dem.set_proxy` と地形の覚えの破棄（旧 `launcher_menu._on_proxy_settings` と同じ）。
    - 保存に失敗しても今の起動では効かせる（旧と同じ＝`save_app` の失敗はログに出る）。
    """
    text = _check(key, value)
    cfg[key] = text
    saved = config.save_app(cfg)
    if key == "lang":
        if saved:
            config.consume_lang_seed()
        i18n.set_lang(text)
        return True
    if key == "proxy_url":
        from core import dem
        from core import simulation as sim
        dem.set_proxy(text)
        sim.clear_terrain_cache()
    return False
