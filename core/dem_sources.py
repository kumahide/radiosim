"""
dem_sources.py
==============
DEM/タイルソースの**宣言ファイル**（3.3 段4c）。国土地理院以外の DEM ソースを
足せるようにする器＝ソースごとの URL テンプレート・デコード方式・無効値
ピクセル・出典表記・利用条件 URL を、コードでなく**宣言（データ）**で持つ。

⚠️ **開く範囲はリモートの XYZ PNG タイルだけ**（ローカル GeoTIFF は別の器・
3.3 では扱わない）。

⚠️ **デコード式を文字列やコードで持たせない（`eval` 禁止）**＝配布物に
`eval` を持ち込むことになる。デコード方式は `DecodeMethod` の列挙から選ぶ
だけにする。業界標準は Terrarium（AWS Open Data・世界カバー）と Mapbox
Terrain-RGB の 2 種しかないので、列挙で足りる。

⚠️ **1 回の計算でソースを混ぜない**＝この規則自体は `core/dem.py` の
`DEM_LAYERS` 直後のコメントが正典（3.3 段4b）。ここは「何を足せるか」の宣言で、
「1 回の計算でどれを使うか」の規則はそちらが持つ。

🔑 **3.4 段1＝利用者が宣言ファイルで足す入口**（I-147）。`load_user_sources()` が
`core/config.py:USER_DEM_SOURCES_FILE`（TOML・利用者が手で書く・アプリは読む
だけで書き戻さない）を読み、検証を通った宣言だけを `DemSourceSpec` へ変換する。
**組み込みで登録するのは国土地理院だけ**のまま（このモジュールは import 時に
ネットワーク／ファイル I/O を一切行わない＝読み込みは呼び出し側が明示的に
`load_user_sources()` を呼んだときだけ）。
"""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class DecodeMethod(Enum):
    """PNG ピクセル(R,G,B) → 標高 [m] のデコード方式。列挙のみ（`eval` 禁止）。"""
    GSI_DEM = "gsi_dem"
    TERRARIUM = "terrarium"
    MAPBOX_TERRAIN_RGB = "mapbox_terrain_rgb"


@dataclass(frozen=True)
class DemSourceSpec:
    """DEM ソース 1 つぶんの宣言。

    Attributes:
        source_id:     `terrain_profile.csv` の `elev_source` 列に書く値
                        （`core/output_contract.py` が単一ソース）。
        display_name:  出典表記に使う名前。
        layers:        (layer_id, zoom) の優先順位列（高精度→低精度）。
                        `core/dem.py` の `DEM_LAYERS` と同形＝1 ソース内の
                        フォールバック降下に使う。
        url_template:  `{layer}` `{z}` `{x}` `{y}` プレースホルダを持つ URL。
        decode:        `DecodeMethod` の列挙値。
        invalid_rgb:   無効値ピクセル (R,G,B)。無ければ None。
        attribution:   出典表記の文言。
        terms_url:     利用条件の参照 URL。
    """
    source_id: str
    display_name: str
    layers: tuple[tuple[str, int], ...]
    url_template: str
    decode: DecodeMethod
    invalid_rgb: "tuple[int, int, int] | None"
    attribution: str
    terms_url: str


# --- 国土地理院 DEM（3.3 時点で唯一のアクティブソース） --------------------
GSI_DEM = DemSourceSpec(
    source_id="gsi_dem",
    display_name="国土地理院 DEM",
    layers=(
        ("dem5a_png", 15),   # 5m  最優先（航空レーザ測量）
        ("dem5b_png", 15),   # 5m  次点（写真測量、dem5a_png より広域）
        ("dem_png",   14),   # 10m 全国カバー（最終フォールバック）
    ),
    url_template="https://cyberjapandata.gsi.go.jp/xyz/{layer}/{z}/{x}/{y}.png",
    decode=DecodeMethod.GSI_DEM,
    invalid_rgb=(128, 0, 0),
    attribution="国土地理院",
    terms_url="https://maps.gsi.go.jp/development/ichiran.html",
)

#: 現在アクティブな DEM ソースの台帳（3.3 時点は国土地理院のみ）。
SOURCES: tuple[DemSourceSpec, ...] = (GSI_DEM,)


def _decode_gsi_dem(r: int, g: int, b: int) -> float:
    """国土地理院方式。無効値ピクセル (128,0,0) は 0.0（海・データ欠損）を返す。"""
    if r == 128 and g == 0 and b == 0:
        return 0.0
    x = r * 65536 + g * 256 + b
    if x < 8388608:
        return float(x * 0.01)
    return float((x - 16777216) * 0.01)


def _decode_terrarium(r: int, g: int, b: int) -> float:
    """Terrarium 方式（AWS Open Data Terrain Tiles）。elevation = (R*256+G+B/256) - 32768 [m]。"""
    return (r * 256 + g + b / 256.0) - 32768.0


def _decode_mapbox_terrain_rgb(r: int, g: int, b: int) -> float:
    """Mapbox Terrain-RGB 方式。elevation = -10000 + (R*256*256+G*256+B) * 0.1 [m]。"""
    return -10000.0 + (r * 256 * 256 + g * 256 + b) * 0.1


_DECODERS: dict[DecodeMethod, Callable[[int, int, int], float]] = {
    DecodeMethod.GSI_DEM: _decode_gsi_dem,
    DecodeMethod.TERRARIUM: _decode_terrarium,
    DecodeMethod.MAPBOX_TERRAIN_RGB: _decode_mapbox_terrain_rgb,
}


def decode(method: DecodeMethod, r: int, g: int, b: int) -> float:
    """RGB ピクセルを標高 [m] へデコードする。列挙からディスパッチするだけ（`eval` 禁止）。"""
    return _DECODERS[method](r, g, b)


def definition_fingerprint(src: DemSourceSpec) -> str:
    """ソース定義（URL・デコード方式・無効値・レイヤ構成）から短いハッシュを作る。

    B-236＝`source_id` を変えずに `dem_sources.toml` の中身（URL・デコード方式等）
    だけ書き換えると、旧ディスクキャッシュのタイルを新しいデコーダで読み直して
    しまい、誤った標高が静かに返る。`core/dem.py:source_layer_dir` がこの値を
    利用者ソースのキャッシュ置き場に含めることで、定義が変われば別ディレクトリ
    になり自動的に無効化される（旧ディレクトリは残るが二度と読まれない）。
    """
    payload = repr((src.url_template, src.decode.value, src.invalid_rgb, src.layers))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ==============================================================================
# 利用者の宣言ファイル（3.4 段1・I-147）
# ==============================================================================

#: `SOURCES`（組み込み）と衝突させない予約語＝`core/output_contract.py` の
#: `elev_source` 列がこの2値を既に使っている（取得成功／取得失敗）。
_RESERVED_SOURCE_IDS = frozenset({"gsi_dem", "unavailable"})

#: `source_id` の許容書式＝英数字と `_-` のみ。**ディスクキャッシュのフォルダ名
#: にそのまま使う**（`core/dem.py` のキャッシュパス分離）ので、`..`・`/`・`\\`
#: を含む値を通すとパストラバーサルになり得る。この正規表現は許可リスト方式
#: （危険文字を拒否するのではなく、安全な文字だけを許す）。
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

#: `url_template` が満たすべき最低条件。`{layer}` は複数レイヤ宣言のときだけ必須
#: （`_validate_source` 側で `layers` の長さを見て判定する）。
_URL_REQUIRED_PLACEHOLDERS = ("{z}", "{x}", "{y}")

#: 直近の `load_user_sources()` の却下報告＝`(source_id または見出し, 却下理由キー)`。
#: `i18n.external_reports()` と同型（画面で知らせるのは呼び出し側の仕事）。
_load_reports: list[tuple[str, str]] = []

#: 直近に読み込めた利用者宣言（`all_sources()` が `SOURCES` に足す）。
_user_sources: list[DemSourceSpec] = []


def _validate_source(
    raw: dict, seen_ids: set[str], seen_names: set[str],
) -> "tuple[DemSourceSpec | None, str, str]":
    """1 つの `[[source]]` テーブルを検証する。

    Returns: (成功なら DemSourceSpec、失敗なら None, 見出し（source_id かエラー
    文脈用の代用語）, 却下理由キー（成功時は空文字列）)
    """
    label = str(raw.get("source_id") or "(source_id 欠落)")

    source_id = raw.get("source_id")
    if not isinstance(source_id, str) or not _SOURCE_ID_RE.match(source_id):
        return None, label, "dem_src_bad_id"
    if source_id in _RESERVED_SOURCE_IDS:
        return None, source_id, "dem_src_id_reserved"
    if source_id in seen_ids:
        return None, source_id, "dem_src_id_duplicate"

    display_name = raw.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        return None, source_id, "dem_src_missing_field"
    # B-237＝表示名は画面の Combobox で人が見分ける唯一の手がかり。組み込み
    # （国土地理院）や他の宣言と同じ表示名を許すと、`display_name → source_id`
    # の逆引きが後勝ちで別ソースを指すようになり、画面上は既定に見えても
    # 実際には別ソースが実行される（黙って誤る）。
    if display_name in seen_names:
        return None, source_id, "dem_src_display_name_duplicate"

    decode_raw = raw.get("decode")
    try:
        decode_method = DecodeMethod(decode_raw)
    except ValueError:
        return None, source_id, "dem_src_bad_decode"

    layers_raw = raw.get("layers")
    if not isinstance(layers_raw, list) or not layers_raw:
        return None, source_id, "dem_src_bad_layers"
    layers: list[tuple[str, int]] = []
    for item in layers_raw:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or not isinstance(item[0], str) or not item[0]
                or not isinstance(item[1], int) or isinstance(item[1], bool)):
            return None, source_id, "dem_src_bad_layers"
        layers.append((item[0], item[1]))

    url_template = raw.get("url_template")
    if not isinstance(url_template, str) or not url_template.startswith("https://"):
        return None, source_id, "dem_src_bad_url"
    if not all(ph in url_template for ph in _URL_REQUIRED_PLACEHOLDERS):
        return None, source_id, "dem_src_bad_url"
    if len(layers) > 1 and "{layer}" not in url_template:
        return None, source_id, "dem_src_bad_url"

    invalid_rgb_raw = raw.get("invalid_rgb")
    invalid_rgb: "tuple[int, int, int] | None" = None
    if invalid_rgb_raw is not None:
        if (not isinstance(invalid_rgb_raw, (list, tuple)) or len(invalid_rgb_raw) != 3
                or not all(isinstance(v, int) and not isinstance(v, bool)
                           for v in invalid_rgb_raw)):
            return None, source_id, "dem_src_bad_invalid_rgb"
        invalid_rgb = (invalid_rgb_raw[0], invalid_rgb_raw[1], invalid_rgb_raw[2])

    attribution = raw.get("attribution")
    terms_url = raw.get("terms_url")
    if not isinstance(attribution, str) or not attribution.strip():
        return None, source_id, "dem_src_missing_field"
    if not isinstance(terms_url, str) or not terms_url.strip():
        return None, source_id, "dem_src_missing_field"

    spec = DemSourceSpec(
        source_id=source_id,
        display_name=display_name,
        layers=tuple(layers),
        url_template=url_template,
        decode=decode_method,
        invalid_rgb=invalid_rgb,
        attribution=attribution,
        terms_url=terms_url,
    )
    return spec, source_id, ""


def load_user_sources(path: str) -> "tuple[list[DemSourceSpec], list[tuple[str, str]]]":
    """利用者の宣言ファイル（TOML）を読み検証する。**読むだけ**＝書き戻さない。

    `core/i18n.py:load_external` と同じ設計＝①ファイルが無ければ空 ②例外を
    投げて起動を止めない（1 つの `[[source]]` が壊れていても他は読む）。

    Returns: (検証を通った DemSourceSpec のリスト, [(source_id, 却下理由キー)])
    """
    if not os.path.isfile(path):
        return [], []

    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return [], [("(dem_sources.toml)", "dem_src_file_unreadable")]

    sources_raw = doc.get("source")
    if not isinstance(sources_raw, list):
        return [], []

    specs: list[DemSourceSpec] = []
    reports: list[tuple[str, str]] = []
    seen_ids: set[str] = set()
    # 組み込み（国土地理院）の表示名も先に予約する＝宣言ファイルが同じ表示名を
    # 名乗って画面上「国土地理院 DEM」に見える別ソースを実行させないため（B-237）。
    seen_names: set[str] = {GSI_DEM.display_name}
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

    起動時に 1 回呼ぶ（`main.py`）。以後 `all_sources()`/`resolve()` はこの結果を
    参照する。**読み込みに失敗しても例外を投げない**（起動を止めない）。
    """
    global _user_sources, _load_reports
    _user_sources, _load_reports = load_user_sources(path)


def load_reports() -> list[tuple[str, str]]:
    """直近の `load_from()` の却下報告（画面で知らせるのは呼び出し側の仕事）。"""
    return list(_load_reports)


def all_sources() -> tuple[DemSourceSpec, ...]:
    """組み込み（`SOURCES`）＋利用者が宣言ファイルで足したソースの単一台帳。"""
    return SOURCES + tuple(_user_sources)


def resolve(source_id: str) -> DemSourceSpec:
    """`source_id` から `DemSourceSpec` を引く。見つからなければ `GSI_DEM` へ
    フォールバックする（プロジェクトファイルが参照するソースを利用者が宣言
    ファイルから削除済みのケース。呼び出し側で警告する）。"""
    for spec in all_sources():
        if spec.source_id == source_id:
            return spec
    return GSI_DEM
