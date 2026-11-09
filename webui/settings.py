"""
webui/settings.py
=================
新しい画面の **「設定」の枠**＝旧画面のメニュー「設定」の項目のうち、画面から変えるもの（I-257）。

- `payload(cfg)`＝今の値と選べる値（画面はこれだけで枠を組む＝字はサーバーが渡す）。
- `apply(cfg, key, value)`＝検査 → `cfg` を書き換えて `config.save_app`。返り値は
  「読み直しが要るか」（言語を替えたとき＝画面の字は起動時の表から組む）。

扱う鍵（`KEYS`）＝言語・テーマ・プロキシ・起動時の更新の確認・プレリリースも知らせるか。
- 座標の表記（`coord_format`）は載せない＝効くのは帳票の人が読む座標だけで、帳票は
  Pro へ移る（ステージ11b）。欄の読み（`app.js` の `parseLatLon`）は十進度だけ。
- 背景地図（`basemap_layer`）は地図の張り替えと一緒に入れる（I-257 の C）。

⚠️ 起動の経路で網の部品を引かない（`test_the_startup_path_does_not_load_the_network_parts`）＝
`dem`・`simulation`・`update_check` は関数の中で取り込む。
"""

from __future__ import annotations

from typing import Any

from core import config
from core import i18n

#: 画面が変える鍵（並びは枠の並び）。
KEYS = ("lang", "theme", "proxy_url", "update_check_auto", "update_check_prerelease")

#: テーマの値と字の鍵（旧メニューと同じ 3 つ）。
THEMES = (("system", "menu_system"), ("light", "menu_light"), ("dark", "menu_dark"))

_ON_OFF = ("on", "off")


class SettingError(ValueError):
    """知らない鍵・選べる値の外・形の違う値。"""


def languages() -> "list[list[str]]":
    """言語の選択肢 `[[コード, 表示名], …]`＝同梱の 2 つ ＋ 読み込めた外部訳。"""
    return [["en", i18n.t("lang_en")], ["ja", i18n.t("lang_ja")],
            *([code, name] for code, name in i18n.external_languages())]


def payload(cfg: "dict[str, str]") -> "dict[str, Any]":
    """枠の今の値・選択肢・字。`update_check_prerelease` は版に追従する既定を真偽に畳む。"""
    from core import update_check
    return {
        "values": {
            "lang": cfg.get("lang", "en"),
            "theme": cfg.get("theme", "system"),
            "proxy_url": cfg.get("proxy_url", ""),
            "update_check_auto": cfg.get("update_check_auto") == "on",
            "update_check_prerelease": update_check.want_prerelease(cfg),
        },
        "choices": {
            "lang": languages(),
            "theme": [[v, i18n.t(k)] for v, k in THEMES],
        },
        "labels": {
            "title": i18n.t("menu_settings"),
            "lang": i18n.t("menu_language"),
            "theme": i18n.t("menu_theme"),
            "proxy_url": i18n.t("dlg_proxy_url_label"),
            "proxy_hint": i18n.t("dlg_proxy_hint"),
            "update_check_auto": i18n.t("menu_check_updates_auto"),
            "update_check_prerelease": i18n.t("menu_check_updates_pre"),
            "close": i18n.t("dlg_ok"),          # 1 つ替えるごとに保存する＝閉じるだけ
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
    if key == "proxy_url":
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise SettingError("proxy_url: not a one-line string")
        return value.strip()
    # 起動時の確認・プレリリース＝画面は真偽で送る。触った時点で on/off を固定する（I-180）。
    if not isinstance(value, bool):
        raise SettingError(f"{key}: not a boolean")
    return _ON_OFF[0] if value else _ON_OFF[1]


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
