"""
tests/test_report_map.py
========================
report_map.py（ヘッドレス経路地図生成）と map_graphics.py（純 PIL 描画）の単体テスト。

純関数（投影・範囲の箱・ズーム選択）を検証する。1 経路を回した地図
（`render_path_map`）の試験は帳票と一緒に Pro へ移した（Link 1.0 ステージ11b）。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from PIL import Image

from core import dem
from report import map_graphics
from report import report_common
from report import report_map


def _attribution_text(source_id: str = "pale") -> str:
    """いま焼かれる出典表記（製品と同じ解決器から引く＝B-248）。"""
    return report_map.attribution_for(source_id)


def _expected_badge(img: Image.Image) -> Image.Image:
    """その画像に貼られるはずの出典バー（**幅から字の大きさが決まる**＝B-135）。"""
    return map_graphics.attribution_badge(
        _attribution_text(), font_px=report_common.figure_text_px(img.width))


def _missing_fill_count(img: Image.Image) -> int:
    """`_MISSING_RGB` の画素数を、**出典バーの領域を除いて**数える。

    バーは白地＋濃グレー文字なので、縁のアンチエイリアスが白→#333 の途中で
    (229,229,229) を**通り得る**＝そのままだと「タイルが欠けた」ことを見る検査が
    バーの 1 画素で落ちる（B-133 の実装中に実際に落ちた）。
    ⚠️ **バーの色や不透明度をずらして避けない**＝検査の都合で刻印の見た目を
    決めることになる。除くのはバーが占める矩形だけで、他は従来どおり全数見る。
    """
    fill = np.all(np.asarray(img) == report_map._MISSING_RGB, axis=2)
    badge = _expected_badge(img)
    m = report_map._ATTR_MARGIN_PX
    fill[img.height - badge.height - m:, img.width - badge.width - m:] = False
    return int(fill.sum())


# ============================================================
# 投影（lonlat_to_pixel）
# ============================================================
class TestLonLatToPixel:

    def test_known_value_origin(self):
        # zoom 0: lon=0 → x=128, lat=0 → y=128（世界1タイル 256px の中心）。
        x, y = dem.lonlat_to_pixel(0.0, 0.0, 0)
        assert x == 128.0
        assert abs(y - 128.0) < 1e-6

    def test_x_increases_eastward(self):
        x_w, _ = dem.lonlat_to_pixel(35.0, 139.0, 12)
        x_e, _ = dem.lonlat_to_pixel(35.0, 140.0, 12)
        assert x_e > x_w

    def test_y_increases_southward(self):
        # 北が上＝緯度が高いほど y は小さい。
        _, y_n = dem.lonlat_to_pixel(36.0, 139.0, 12)
        _, y_s = dem.lonlat_to_pixel(35.0, 139.0, 12)
        assert y_s > y_n


# ============================================================
# 全パス1枚地図（_bbox_px / choose_zoom_paths / render_paths_map）
# ============================================================
def _specs(*triples) -> list[report_map.PathSpec]:
    return [report_map.PathSpec(tx=tx, rx=rx, status=st, label=lb)
            for tx, rx, st, lb in triples]


_TWO_PATHS = _specs(
    ((34.54, 132.41), (34.50, 132.37), "OK", "P1"),
    ((34.46, 132.30), (34.40, 132.20), "NG", "P2"),
)


class TestBboxPx:

    def test_all_endpoints_inside_box(self):
        box = report_map._bbox_px(_TWO_PATHS, 13, 0.15)
        for spec in _TWO_PATHS:
            for lat, lon in (spec.tx, spec.rx):
                px, py = dem.lonlat_to_pixel(lat, lon, 13)
                assert abs(px - box.cx) <= box.half_w + 1e-6
                assert abs(py - box.cy) <= box.half_h + 1e-6

    def test_box_aspect_matches_requested(self):
        for aspect in (15 / 7, 2.0, 1.0):
            box = report_map._bbox_px(_TWO_PATHS, 13, 0.15, aspect)
            assert box.half_w / box.half_h == pytest.approx(aspect)

    def test_degenerate_single_point_has_minimum_extent(self):
        # 全端点が同一（退化）でも最小半幅が確保され破綻しない。
        p = (34.54, 132.41)
        box = report_map._bbox_px(_specs((p, p, "OK", "P1")), 14, 0.15)
        assert box.half_w >= report_map._MIN_HALF_PX
        assert box.half_h > 0

    def test_aspect_fit_only_expands(self):
        # 縦長に散らばる端点でも、アスペクト合わせは広い側に合わせて拡張する
        # （縮めない）＝どの端点も枠内に残る。回帰ガード。
        tall = _specs(((34.30, 132.40), (34.70, 132.40), "OK", "P1"))
        box  = report_map._bbox_px(tall, 12, 0.15)
        px, py = dem.lonlat_to_pixel(34.70, 132.40, 12)
        assert abs(py - box.cy) <= box.half_h + 1e-6


class TestChooseZoomPaths:

    def test_tile_count_within_cap(self):
        z = report_map.choose_zoom_paths(_TWO_PATHS, max_tiles=16)
        x0, x1, y0, y1 = report_map._bbox_tiles(
            report_map._bbox_px(_TWO_PATHS, z, 0.15)
        )
        assert (x1 - x0 + 1) * (y1 - y0 + 1) <= 16

    def test_tighter_cluster_gets_higher_or_equal_zoom(self):
        near = report_map.choose_zoom_paths(
            _specs(((34.540, 132.410), (34.539, 132.409), "OK", "P1")))
        far = report_map.choose_zoom_paths(
            _specs(((34.9, 132.9), (34.1, 132.1), "OK", "P1")))
        assert near >= far


