"""
tile_sources.py
================
背景地図タイルの**宣言ファイル**（3.5 段3・I-152）。`core/dem_sources.py`
（DEM ソースの宣言）と同じ型で、利用者が国土地理院以外の背景地図タイルを
設定フォルダの宣言ファイルに書いて足せるようにする器。

⚠️ **開く範囲はリモートの XYZ タイルだけ**（地図描画用途なので PNG/JPG
問わず可＝DEM ソースの標高デコードとは別の対象）。

⚠️ **1 回の計算で 1 つに固定する DEM ソースとは別軸**＝背景地図は表示用途
であり計算結果に影響しない。地図ウィンドウ側で選んだものがそのまま見える
だけで、プロジェクト保存や条件探索の「条件」には入らない。

🔑 **組み込み（国土地理院・淡色地図／航空写真）はこのモジュールに持たない**＝
`views/map_window.py` の `_TILE_LAYERS` が引き続き持つ（label/attribution が
i18n キーで言語追従するため。DEM ソースの `GSI_DEM.display_name` のように
固定文字列にすると英語 UI で日本語のまま出てしまう＝既存の翻訳を壊さない
ための意図した非対称）。`all_sources()` が返すのは**利用者が宣言した分だけ**
で、組み込みとの合成は呼び出し側（`map_window.py`）が行う。
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass


@dataclass(frozen=True)
class TileSourceSpec:
    """背景地図タイルソース 1 つぶんの宣言。

    Attributes:
        source_id:     選択欄の内部キー。組み込みの `pale` / `photo` と衝突
                        させない（予約語）。
        display_name:  選択欄に出す名前（利用者が宣言ファイルに書いた文字列
                        そのまま＝翻訳しない。DEM ソースの `display_name` と
                        同じ扱い）。
        url:           `{z}` `{x}` `{y}` プレースホルダを持つ URL。
        max_zoom:      このタイルサーバが提供する最大ズームレベル。
        attribution:   出典表記の文言（そのまま焼く・翻訳しない）。
        terms_url:     利用条件の参照 URL。
    """
    source_id: str
    display_name: str
    url: str
    max_zoom: int
    attribution: str
    terms_url: str


#: 組み込み（`views/map_window.py` の `_TILE_LAYERS`）と衝突させない予約語。
_RESERVED_SOURCE_IDS = frozenset({"pale", "photo"})

#: `source_id` の許容書式＝英数字と `_-` のみ（`dem_sources.py` と同じ理由＝
#: 将来ディスクキャッシュのキーに使う可能性への保険として許可リスト方式）。
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_URL_REQUIRED_PLACEHOLDERS = ("{z}", "{x}", "{y}")

#: 直近の `load_user_sources()` の却下報告＝`(source_id または見出し, 却下理由キー)`。
_load_reports: list[tuple[str, str]] = []

#: 直近に読み込めた利用者宣言。
_user_sources: list[TileSourceSpec] = []


def _validate_source(
    raw: dict, seen_ids: set[str], seen_names: set[str],
) -> "tuple[TileSourceSpec | None, str, str]":
    """1 つの `[[source]]` テーブルを検証する。

    Returns: (成功なら TileSourceSpec、失敗なら None, 見出し, 却下理由キー)
    """
    # 🔑 **理由キーの一部は `dem_src_*` を再利用する**（`tile_src_*` を新設しない）＝
    # 「source_id が無いか…」「必須項目が無いか空です」等は DEM ソースの宣言でも
    # 同じ文言になり、2 キーで同じ字を持つと `tests/test_i18n_key_duplication.py`
    # が「画面の同じ字を 2 キーで持っている」として落ちる（1 本へ寄せる方針）。
    # 文言が DEM 固有でない（「DEM」の語を含まない）ことを条件に共有している。
    label = str(raw.get("source_id") or "(source_id 欠落)")

    source_id = raw.get("source_id")
    if not isinstance(source_id, str) or not _SOURCE_ID_RE.match(source_id):
        return None, label, "dem_src_bad_id"
    if source_id in _RESERVED_SOURCE_IDS:
        return None, source_id, "tile_src_id_reserved"
    if source_id in seen_ids:
        return None, source_id, "dem_src_id_duplicate"

    display_name = raw.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        return None, source_id, "dem_src_missing_field"
    if display_name in seen_names:
        return None, source_id, "tile_src_display_name_duplicate"

    url = raw.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        return None, source_id, "tile_src_bad_url"
    if not all(ph in url for ph in _URL_REQUIRED_PLACEHOLDERS):
        return None, source_id, "tile_src_bad_url"

    max_zoom = raw.get("max_zoom")
    if (not isinstance(max_zoom, int) or isinstance(max_zoom, bool)
            or not (0 < max_zoom <= 22)):
        return None, source_id, "tile_src_bad_max_zoom"

    attribution = raw.get("attribution")
    terms_url = raw.get("terms_url")
    if not isinstance(attribution, str) or not attribution.strip():
        return None, source_id, "dem_src_missing_field"
    if not isinstance(terms_url, str) or not terms_url.strip():
        return None, source_id, "dem_src_missing_field"

    spec = TileSourceSpec(
        source_id=source_id,
        display_name=display_name,
        url=url,
        max_zoom=max_zoom,
        attribution=attribution,
        terms_url=terms_url,
    )
    return spec, source_id, ""


def load_user_sources(path: str) -> "tuple[list[TileSourceSpec], list[tuple[str, str]]]":
    """利用者の宣言ファイル（TOML）を読み検証する。**読むだけ**＝書き戻さない。

    `core/dem_sources.py:load_user_sources` と同じ設計＝①ファイルが無ければ
    空 ②例外を投げて起動を止めない（1 つの `[[source]]` が壊れていても他は
    読む）。

    Returns: (検証を通った TileSourceSpec のリスト, [(source_id, 却下理由キー)])
    """
    if not os.path.isfile(path):
        return [], []

    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return [], [("(tile_sources.toml)", "dem_src_file_unreadable")]

    sources_raw = doc.get("source")
    if not isinstance(sources_raw, list):
        return [], []

    specs: list[TileSourceSpec] = []
    reports: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for raw in sources_raw:
        if not isinstance(raw, dict):
            reports.append(("(source)", "dem_src_bad_table"))
            continue
        spec, label, reason = _validate_source(raw, seen_ids, seen_names)
        if spec is None:
            reports.append((label, reason))
            continue
        seen_ids.add(spec.source_id)
        seen_names.add(spec.display_name)
        specs.append(spec)
    return specs, reports


def load_from(path: str) -> None:
    """`load_user_sources(path)` を呼び、結果をモジュール状態へ反映する。

    起動時に 1 回呼ぶ（`main.py`）。以後 `all_sources()` はこの結果を参照する。
    **読み込みに失敗しても例外を投げない**（起動を止めない）。
    """
    global _user_sources, _load_reports
    _user_sources, _load_reports = load_user_sources(path)


def load_reports() -> list[tuple[str, str]]:
    """直近の `load_from()` の却下報告（画面で知らせるのは呼び出し側の仕事）。"""
    return list(_load_reports)


def all_sources() -> tuple[TileSourceSpec, ...]:
    """利用者が宣言ファイルで足した背景地図タイルソースの一覧（組み込みは含まない）。

    組み込み（淡色地図・航空写真）は `views/map_window.py:_TILE_LAYERS` が
    別に持つ＝呼び出し側でこの戻り値と合成する。
    """
    return tuple(_user_sources)
