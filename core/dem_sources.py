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

🔴 **3.3 時点で `SOURCES` に在るのは国土地理院のみ**＝Terrarium / Mapbox の
デコード関数は将来ソースを足すときのための実装で、`dem.py` は現状これらを
呼ばない（アクティブなソースは `GSI_DEM` だけ）。
"""

from __future__ import annotations

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
