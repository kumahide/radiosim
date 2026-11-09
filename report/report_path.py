"""
report_path.py
==============
per-path（1経路）の出力生成（ヘッドレス）＝Google Earth 用の `path.kml`。

新しい画面（`webui/exports.py`）が `path_kml_text` を使う。UI 知識ゼロ。
地形断面 PNG と詳細レポート HTML（A4 縦 1 枚）は Pro へ移した（Link 1.0 ステージ11b）。
"""

from __future__ import annotations

import html as _html

import numpy as np

from core import models
from core import simulation as sim
from core import terrain_grid
from core import units


# ============================================================
# KML 出力（per-path）
# ============================================================

def _kml_line_coords(
    lats: "np.ndarray | list[float]",
    lons: "np.ndarray | list[float]",
    alts: "np.ndarray | list[float]",
) -> str:
    """KML <coordinates> 内容（lon,lat,alt の改行区切り）を返す。"""
    return "\n".join(
        f"          {float(lo):.6f},{float(la):.6f},{float(al):.1f}"
        for la, lo, al in zip(lats, lons, alts)
    )


def _find_obs_segments(mask: np.ndarray) -> list[tuple[int, int]]:
    """連続する True 区間の (start, end) インデックスリスト（両端 inclusive）を返す。"""
    segs: list[tuple[int, int]] = []
    n, i = len(mask), 0
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            segs.append((i, j - 1))
            i = j
        else:
            i += 1
    return segs


def path_kml_text(
    terrain:  models.TerrainProfile,
    result:   models.LinkBudgetResult,
    params:   sim.SimParams,
    h_tx:     float,
    h_rx:     float,
    name:     str,
) -> str:
    """per-path の path.kml の本文（Google Earth で 3D 表示可能）。ファイルへ書くのは呼ぶ側
    （新しい画面の 1 回線の保存＝Link 1.0 ステージ10f・Pro の `save_path_kml`）。

    要素:
      - TX / RX Placemark
      - Terrain Profile（actual elevation）
      - Line of Sight（OK=緑 / NG=橙）
      - 1st Fresnel Zone 上辺・下辺
      - Fresnel Obstruction（遮蔽区間を赤でハイライト）
    """
    # ⚠️ **標本の位置は等間隔ではない**（B-150）＝`linspace` で作り直すと、
    #    標高を読んだ場所と KML の折れ線が別の点になる。
    t    = terrain.frac_axis
    lats = params.lat_tx + (params.lat_rx - params.lat_tx) * t
    lons = params.lon_tx + (params.lon_rx - params.lon_tx) * t
    elev = terrain.raw_elevs.astype(float)

    tx_alt = float(elev[0])  + h_tx
    rx_alt = float(elev[-1]) + h_rx
    los    = terrain.los_line(tx_alt, rx_alt)
    f1     = models.fresnel_zone_radii(terrain.d_km_axis, terrain.horiz_dist_km, params.freq_mhz)

    # 🔴 B-160＝地形の折れ線だけ、画素の弦の両端 2 点を中心 1 点へ畳む（処方(a)）。
    # LoS・Fresnel・遮蔽区間は元の標本列のまま（計算・判定に使う添字を変えない）。
    groups = params.sample_pixel_groups
    terrain_lats = terrain_grid.collapse_pixel_groups(list(lats), groups)
    terrain_lons = terrain_grid.collapse_pixel_groups(list(lons), groups)
    terrain_elev = terrain_grid.collapse_pixel_groups(list(elev), groups)

    # KML の色は aabbggrr。OK＝緑・REVIEW＝紫（`map_graphics.STATUS_RGB` の #6a1b9a）・ほかは橙（4.0 ステージ6）。
    los_color = {"OK": "ff00aa00", "REVIEW": "ff9a1b6a"}.get(result.status, "ff00a5ff")
    path_id   = _html.escape(name)
    desc_esc  = _html.escape(
        f"Freq: {params.freq_mhz} MHz | "
        f"RX: {units.format_db(result.p_rx, unit='dBm')} | "
        f"Margin: {units.format_db(result.actual_margin, signed=True, unit='dB')} | "
        f"Status: {result.status}"
    )

    # 遮蔽区間（地形がフレネル下辺を超える部分）
    obstructed = elev > (los - f1)
    obs_xml = ""
    for s, e in _find_obs_segments(obstructed):
        obs_xml += f"""
    <Placemark>
      <name>Obstruction</name>
      <styleUrl>#obs</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats[s:e+1], lons[s:e+1], elev[s:e+1])}
        </coordinates>
      </LineString>
    </Placemark>"""

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>{path_id}</name>
  <description>{desc_esc}</description>

  <Style id="terrain"><LineStyle><color>ff13458b</color><width>2</width></LineStyle></Style>
  <Style id="los"><LineStyle><color>{los_color}</color><width>2</width></LineStyle></Style>
  <Style id="fresnel"><LineStyle><color>80ffff00</color><width>1</width></LineStyle></Style>
  <Style id="obs"><LineStyle><color>ff0000ff</color><width>4</width></LineStyle></Style>

  <Placemark>
    <name>TX</name>
    <description>{_html.escape(f"h_tx={h_tx:.1f} m | {params.freq_mhz} MHz")}</description>
    <Point>
      <altitudeMode>absolute</altitudeMode>
      <coordinates>{params.lon_tx:.6f},{params.lat_tx:.6f},{tx_alt:.1f}</coordinates>
    </Point>
  </Placemark>
  <Placemark>
    <name>RX</name>
    <description>{_html.escape(f"h_rx={h_rx:.1f} m | {units.format_db(result.p_rx, unit='dBm')} ({result.status})")}</description>
    <Point>
      <altitudeMode>absolute</altitudeMode>
      <coordinates>{params.lon_rx:.6f},{params.lat_rx:.6f},{rx_alt:.1f}</coordinates>
    </Point>
  </Placemark>

  <Folder>
    <name>Terrain Profile</name>
    <Placemark>
      <name>Terrain</name>
      <styleUrl>#terrain</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(terrain_lats, terrain_lons, terrain_elev)}
        </coordinates>
      </LineString>
    </Placemark>
  </Folder>

  <Folder>
    <name>Line of Sight</name>
    <Placemark>
      <name>LoS ({_html.escape(result.status)})</name>
      <styleUrl>#los</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats, lons, los)}
        </coordinates>
      </LineString>
    </Placemark>
  </Folder>

  <Folder>
    <name>1st Fresnel Zone</name>
    <Placemark>
      <name>Upper Boundary</name>
      <styleUrl>#fresnel</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats, lons, los + f1)}
        </coordinates>
      </LineString>
    </Placemark>
    <Placemark>
      <name>Lower Boundary</name>
      <styleUrl>#fresnel</styleUrl>
      <LineString>
        <altitudeMode>absolute</altitudeMode>
        <coordinates>
{_kml_line_coords(lats, lons, los - f1)}
        </coordinates>
      </LineString>
    </Placemark>
  </Folder>

  <Folder>
    <name>Fresnel Obstruction</name>{obs_xml}
  </Folder>

</Document>
</kml>"""
    return kml
