"""
tests/test_report_map.py
========================
report_map.py（ヘッドレス経路地図生成）と map_graphics.py（純 PIL 描画）の単体テスト。

純関数（投影・ズーム選択・bbox 余白・距離テキスト）を中心に検証し、
render_path_map は _fetch_tile を monkeypatch してネットワーク無しでスモークする。
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from PIL import Image

from conftest import require_burnable_font
from core import dem
from core import i18n
from report import map_graphics
from report import report_common
from report import report_map


def _attribution_text() -> str:
    """いま焼かれる出典表記（製品と同じキーから引く）。"""
    return i18n.t(report_map._ATTR_KEY)


def _expected_badge(img: Image.Image) -> Image.Image:
    """その画像に貼られるはずの出典帯（**幅から字の大きさが決まる**＝B-135）。"""
    return map_graphics.attribution_badge(
        _attribution_text(), font_px=report_common.figure_text_px(img.width))


def _missing_fill_count(img: Image.Image) -> int:
    """`_MISSING_RGB` の画素数を、**出典帯の領域を除いて**数える。

    帯は白地＋濃グレー文字なので、縁のアンチエイリアスが白→#333 の途中で
    (229,229,229) を**通り得る**＝そのままだと「タイルが欠けた」ことを見る検査が
    帯の 1 画素で落ちる（B-133 の実装中に実際に落ちた）。
    ⚠️ **帯の色や不透明度をずらして避けない**＝検査の都合で刻印の見た目を
    決めることになる。除くのは帯が占める矩形だけで、他は従来どおり全数見る。
    """
    fill = np.all(np.asarray(img) == report_map._MISSING_RGB, axis=2)
    badge = _expected_badge(img)
    m = report_map._ATTR_MARGIN_PX
    fill[img.height - badge.height - m:, img.width - badge.width - m:] = False
    return int(fill.sum())


# ============================================================
# map_graphics（純 PIL 描画）
# ============================================================
class TestMapGraphics:

    def test_node_icon_returns_pil_image(self):
        for hollow in (True, False):
            img = map_graphics.node_icon(hollow)
            assert isinstance(img, Image.Image)
            assert img.size == (26, 26)
            assert img.mode == "RGBA"

    def test_distance_badge_returns_pil_image(self):
        img = map_graphics.distance_badge("1.23 km")
        assert isinstance(img, Image.Image)
        assert img.width > 0 and img.height > 0

    def test_north_arrow_returns_rgba_image(self):
        img = map_graphics.north_arrow(0.0, -1.0)
        assert isinstance(img, Image.Image)
        assert img.mode == "RGBA"
        assert img.width > 0 and img.height > 0

    def test_north_arrow_handles_zero_vector(self):
        # 退化（北ベクトル 0）でも例外なく描く。
        img = map_graphics.north_arrow(0.0, 0.0)
        assert isinstance(img, Image.Image)

    def test_distance_formatting_does_not_live_here(self):
        """距離の書式は units.py の担当（I-014 / 2.5a1）。

        以前は PIL 描画モジュールである map_graphics に distance_text があり、
        書式が report.py / views/graph.py のインライン f-string にも散っていた。
        置き場が無いと次の距離表示追加でまた散るので、この層へ戻らないよう
        構造で固定する。描画（distance_badge）はここ、書式は units。
        """
        assert not hasattr(map_graphics, "distance_text")


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
# _band_px / _coverage_tiles / choose_zoom（純関数）
# ============================================================
class TestBandPx:

    def test_band_contains_tx_rx_within_half_extent(self):
        # TX/RX は中点からバンド長手方向に ±path_len/2＝必ず half_w 以内に収まる。
        band = report_map._band_px((34.54, 132.41), (34.40, 132.20), 14, 0.15)
        for px, py in ((band.ax, band.ay), (band.bx, band.by)):
            along = (px - band.mx) * band.ux + (py - band.my) * band.uy
            perp  = (px - band.mx) * band.px + (py - band.my) * band.py
            assert abs(along) <= band.half_w + 1e-6
            assert abs(perp) <= band.half_h + 1e-6

    def test_unit_vectors_are_orthonormal(self):
        band = report_map._band_px((34.54, 132.41), (34.40, 132.20), 14, 0.15)
        assert band.ux * band.ux + band.uy * band.uy == pytest.approx(1.0)
        assert band.ux * band.px + band.uy * band.py == pytest.approx(0.0)

    def test_degenerate_point_has_minimum_extent(self):
        # TX==RX でも最小半幅が確保され（東向きに固定）破綻しない。
        band = report_map._band_px((34.54, 132.41), (34.54, 132.41), 14, 0.15)
        assert band.half_w >= report_map._MIN_HALF_PX
        assert band.half_h > 0
        assert (band.ux, band.uy) == (1.0, 0.0)

    def test_band_aspect_matches_requested(self):
        # half_w/half_h = aspect（出力比を固定＝レポート断面図と高さを揃える）。
        for aspect in (15 / 6, 2.0, 1.0):
            band = report_map._band_px(
                (34.54, 132.41), (34.40, 132.20), 14, 0.15, aspect
            )
            assert band.half_w / band.half_h == pytest.approx(aspect)


class TestChooseZoom:

    def test_tile_count_within_cap(self):
        tx, rx = (34.54, 132.41), (34.40, 132.20)
        z = report_map.choose_zoom(tx, rx, max_tiles=16)
        x0, x1, y0, y1 = report_map._coverage_tiles(
            report_map._band_px(tx, rx, z, 0.15)
        )
        assert (x1 - x0 + 1) * (y1 - y0 + 1) <= 16

    def test_closer_path_gets_higher_or_equal_zoom(self):
        near = report_map.choose_zoom((34.540, 132.410), (34.539, 132.409))
        far  = report_map.choose_zoom((34.6, 132.5), (34.2, 132.1))
        assert near >= far

    def test_degenerate_returns_valid_zoom(self):
        p = (34.54, 132.41)
        z = report_map.choose_zoom(p, p, max_tiles=16, min_zoom=5, max_zoom=18)
        assert 5 <= z <= 18


# ============================================================
# render_path_map（_fetch_tile を monkeypatch・ネットワーク無し）
# ============================================================
class TestRenderPathMap:

    def _fake_tile(self, *args, **kwargs):
        return np.full((256, 256, 3), 200, dtype=np.uint8)

    def test_returns_image_when_tiles_available(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map((34.54, 132.41), (34.53, 132.40))
        assert isinstance(img, Image.Image)
        assert img.mode == "RGB"
        assert img.width > 0 and img.height > 0

    def test_diagonal_path_is_rotated_to_landscape(self, monkeypatch):
        # 経路を水平化するため、対角の経路でも横長（width > height）になる。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map((35.70, 139.70), (35.62, 139.81))
        assert isinstance(img, Image.Image)
        assert img.width > img.height

    def test_output_aspect_matches_the_profile_figure(self, monkeypatch):
        # 出力の幅/高さ ≈ 15:4.5＝**地形断面図と同じ比**（2026-08-28 に 15:5 から変更）。
        # 🔑 **断面図の figsize から引く**＝数字を書き写すと、片方だけ変えた日に
        # 「揃えてある」という設計意図が黙って壊れる（2 枚は width:100% で縦に並ぶ）。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map((35.70, 139.70), (35.62, 139.81))
        assert isinstance(img, Image.Image)   # 取得失敗（None）を比の検査より先に落とす
        w_in, h_in = report_common.PROFILE_FIGSIZE
        assert img.width / img.height == pytest.approx(w_in / h_in, rel=0.03)

    def test_no_gray_fill_after_rotation(self, monkeypatch):
        # 回転 expand のグレー余白（_MISSING_RGB）がバンド内に残らない
        # （バンドの north-up 外接矩形ぶん取得＋数 px インセットで隅も埋まる）。
        # _fake_tile は全画素 200 なので 229=_MISSING_RGB は必ず埋め色。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map((35.70, 139.70), (35.62, 139.81))
        assert _missing_fill_count(img) == 0

    def test_returns_none_when_all_tiles_fail(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **k: None)
        img = report_map.render_path_map((34.54, 132.41), (34.53, 132.40))
        assert img is None

    def test_returns_none_when_fetch_rate_below_threshold(self, monkeypatch):
        # タイルの約半分だけ取得成功 → 閾値 0.6 未満なので地図なし（注記に委ねる）。
        # 並列ワーカーから呼ばれるため、共有カウンタではなくタイル座標で決定的に分岐。
        def _half(layer, zoom, x, y, *args, **kwargs):
            return self._fake_tile() if (x + y) % 2 == 0 else None

        monkeypatch.setattr(dem, "_fetch_tile", _half)
        img = report_map.render_path_map(
            (34.6, 132.5), (34.4, 132.3), min_fetch_frac=0.6
        )
        assert img is None

    def test_partial_fetch_above_threshold_renders(self, monkeypatch):
        # 全取得成功でも閾値を下げれば当然描画。閾値境界の健全性確認。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map(
            (34.54, 132.41), (34.53, 132.40), min_fetch_frac=0.6
        )
        assert isinstance(img, Image.Image)

    def test_b64_wrapper_none_when_render_fails(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **k: None)
        assert report_map.render_path_map_b64((34.54, 132.41), (34.53, 132.40)) is None

    def test_b64_wrapper_returns_string(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        b64 = report_map.render_path_map_b64((34.54, 132.41), (34.53, 132.40))
        assert isinstance(b64, str) and len(b64) > 0


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


class TestRenderPathsMap:

    def _fake_tile(self, *args, **kwargs):
        return np.full((256, 256, 3), 200, dtype=np.uint8)

    def test_returns_image_with_expected_aspect(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_paths_map(_TWO_PATHS)
        assert isinstance(img, Image.Image)
        assert img.mode == "RGB"
        assert img.width / img.height == pytest.approx(15 / 7, rel=0.03)

    def test_no_gray_fill_in_output(self, monkeypatch):
        # north-up＝タイル格子と平行に切り出すので欠け（_MISSING_RGB）は出ない。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_paths_map(_TWO_PATHS)
        assert _missing_fill_count(img) == 0

    def test_status_colors_are_drawn(self, monkeypatch):
        # OK（緑）と NG（赤）の線が両方とも画素として現れる＝台帳の行色と対応。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_paths_map(_TWO_PATHS)
        arr = np.asarray(img)
        for status in ("OK", "NG"):
            rgb = map_graphics.STATUS_RGB[status]
            assert np.any(np.all(arr == rgb, axis=2)), f"{status} 色の線が無い"

    def test_error_path_is_drawn(self, monkeypatch):
        # 計算に失敗した ERROR 行も座標は既知なので地図には描く。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_paths_map(
            _specs(((34.54, 132.41), (34.50, 132.37), "ERROR", "P1")))
        arr = np.asarray(img)
        assert np.any(np.all(arr == map_graphics.STATUS_RGB["ERROR"], axis=2))

    def test_empty_paths_returns_none(self):
        assert report_map.render_paths_map([]) is None

    def test_returns_none_when_all_tiles_fail(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **k: None)
        assert report_map.render_paths_map(_TWO_PATHS) is None

    def test_returns_none_when_fetch_rate_below_threshold(self, monkeypatch):
        def _half(layer, zoom, x, y, *args, **kwargs):
            return self._fake_tile() if (x + y) % 2 == 0 else None

        monkeypatch.setattr(dem, "_fetch_tile", _half)
        assert report_map.render_paths_map(_TWO_PATHS, min_fetch_frac=0.6) is None

    def test_many_paths_render_without_labels(self, monkeypatch):
        # ラベル上限超過でも例外なく描ける（地図が文字で埋まらないよう落とす）。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        many = _specs(*[
            ((34.50 + i * 0.002, 132.40), (34.50 + i * 0.002, 132.41), "OK", f"P{i}")
            for i in range(report_map._MAX_PATH_LABELS + 2)
        ])
        img = report_map.render_paths_map(many)
        assert isinstance(img, Image.Image)

    def test_b64_wrapper_returns_string(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        b64 = report_map.render_paths_map_b64(_TWO_PATHS)
        assert isinstance(b64, str) and len(b64) > 0

    def test_b64_wrapper_none_when_render_fails(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **k: None)
        assert report_map.render_paths_map_b64(_TWO_PATHS) is None


# ============================================================
# 出典表記（B-133）＝地理院タイルの表示義務
# ============================================================
class TestAttribution:
    """**帳票へ焼く地図にも出典が要る**（B-133）。

    🔴 この欠陥の正体は「UI の地図には出したが、同じ絵を帳票へ焼くもう一方の
    経路が引き継がなかった」こと＝*字が抜けていた*のではなく**配線が無かった**。
    なので検査も字でなく**配線**を見る（[[feedback_promote_recurring_checks]] の
    実証 50）＝どの地図関数から呼んでも帯が貼られること・文言がタイルのレイヤから
    引かれていること・UI と帳票が同じ表を引いていることの 3 点。
    """

    def _fake_tile(self, *args, **kwargs):
        return np.full((256, 256, 3), 200, dtype=np.uint8)

    def test_layer_to_source_text_is_single_sourced(self):
        # UI（views/map_window.py の `_TILE_LAYERS`）と帳票が**同じ表**を引く。
        # ⚠️ ここが 2 つあると「航空写真を見ながら『出典: 淡色地図』」が生まれる。
        from views import map_window
        for key, layer in map_window._TILE_LAYERS.items():
            assert layer.attr_key == map_graphics.ATTR_KEYS[key]

    def test_every_tile_layer_has_a_source_text(self):
        # タイルを足したら出典も足さないと通らない（対で持つことの強制）。
        for key, attr_key in map_graphics.ATTR_KEYS.items():
            assert i18n.t(attr_key) and i18n.t(attr_key) != attr_key, key
        assert dem.BASEMAP_LAYER in map_graphics.ATTR_KEYS

    def test_report_source_text_matches_the_tile_it_actually_draws(self):
        """帳票が焼く文言が、**実際に取ってくるタイル**の出典であること。

        🔑 ここが `report_map._ATTR_KEY` をリテラルで書ける根拠＝キーを直に
        書くのは外部翻訳ゲート（B-101）が `t()` の引数を静的に読むためで、
        **レイヤとの対はこの 1 本が受け持つ**。帳票のタイルを淡色から替えたら
        （`dem.BASEMAP_LAYER`）、キーを直さない限りここで落ちる。
        """
        assert report_map._ATTR_KEY == map_graphics.ATTR_KEYS[dem.BASEMAP_LAYER]

    def test_source_text_is_readable_not_tofu(self):
        # 日本語の出典が**豆腐（□）にならない**フォントで焼けること。
        # 読めない刻印は無いのと同じ＝表示義務を満たさない。
        text  = "出典: 地理院タイル（淡色地図）"
        font  = map_graphics.load_font(22, text)
        empty = map_graphics.load_font(22, "").getbbox(text)
        assert font.getbbox(text) is not None
        # 既定フォントしか無い環境では判定できないので、その場合だけ見送る。
        if getattr(font, "path", None) is None:
            pytest.skip("no TrueType font available")
        assert not str(getattr(font, "path", "")).lower().startswith("arial")
        assert empty is not None    # ASCII 経路も壊れていないこと

    @pytest.mark.parametrize("render", ("path", "paths"))
    def test_rendered_map_carries_the_source_badge(self, monkeypatch, render):
        # **両方の地図関数**が帯を貼ること（片方だけ直すのを止める）。
        seen: list[str] = []
        real = map_graphics.attribution_badge
        monkeypatch.setattr(
            map_graphics, "attribution_badge",
            lambda text, *a, **k: (seen.append(text), real(text, *a, **k))[1],
        )
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = (report_map.render_path_map((34.54, 132.41), (34.53, 132.40))
               if render == "path" else report_map.render_paths_map(_TWO_PATHS))
        assert isinstance(img, Image.Image)
        assert seen == [_attribution_text()], "出典の帯が貼られていない"

    def test_the_badge_is_pasted_at_the_bottom_right(self, monkeypatch):
        # 右下＝地図出典の慣例位置。北矢印（右上）と重ならないことも兼ねる。
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map((34.54, 132.41), (34.53, 132.40))
        assert isinstance(img, Image.Image)
        badge = _expected_badge(img)
        m   = report_map._ATTR_MARGIN_PX
        arr = np.asarray(img)
        region = arr[img.height - badge.height - m:img.height - m,
                     img.width - badge.width - m:img.width - m]
        # 帯は不透明の白地なので、その矩形にはタイルの地色（200）が残らない。
        assert (region == 200).all(axis=2).mean() < 0.5
        # 逆に**左下**は素のタイルのまま＝隅を取り違えていない。
        opposite = arr[img.height - badge.height - m:img.height - m,
                       m:m + badge.width]
        assert (opposite == 200).all(axis=2).mean() > 0.9


# ============================================================
# 図に焼く字の大きさ（B-135）＝A4 に載せた後で読めるか
# ============================================================
class TestBurnedTextStaysReadable:
    """🔴 **図の中の px は、そのままの大きさでは読めない**（B-135）。

    帳票の図は width:100% で A4 の印字幅へ縮めて載る＝**縮小率のぶん字も縮む**。
    実測で 5.7〜7.8px まで落ち、帳票の最小字（開示節の 8px）を下回っていた
    （ユーザー指摘「地図の出典が小さく過ぎて読めません」）。
    ⚠️ **図ごとに解像度が違う**ので、px や pt を直接書くと実寸がバラバラになる。
    """

    def _fake_tile(self, *args, **kwargs):
        return np.full((256, 256, 3), 200, dtype=np.uint8)

    @pytest.mark.parametrize("width", (600, 975, 1044, 1140, 2250))
    def test_the_size_is_derived_so_the_page_size_is_constant(self, width):
        # どんな幅の図でも、A4 に載せた後の実寸は同じ（＝下限ちょうど）。
        px = report_common.figure_text_px(width)
        on_page = px * report_common.A4_CONTENT_WIDTH_PX / width
        assert on_page == pytest.approx(report_common.MIN_FIGURE_TEXT_PX)

    def test_the_floor_is_at_least_the_smallest_type_in_the_report(self):
        """帳票の最小字（開示節の CSS 8px）を下回らないこと。

        ⚠️ **縮小フィット（per-path・最大 0.82 倍）は掛けない**＝比べる相手の
        開示節も同じ `.fit` の中で同率に縮むので、両者の比は変わらない
        （2026-08-28 に 0.82 を掛けて比べたのは誤りだった）。
        """
        assert report_common.MIN_FIGURE_TEXT_PX >= 8.0

    @pytest.mark.parametrize("path", (((34.54, 132.41), (34.53, 132.40)),
                                      ((35.70, 139.70), (35.62, 139.81))))
    def test_the_map_source_is_sized_from_the_image_it_is_pasted_on(
            self, monkeypatch, path):
        """**地図の出典は、その画像の幅から大きさが決まる**こと。

        🔑 経路の長さで画像の幅が変わる＝固定 px だと**経路ごとに実寸が変わる**。
        ⇒ 幅の違う 2 枚で帯の大きさが変わり、A4 に載せた後は揃うことを見る。

        ⚠️ **測っているのは実際に焼かれた字の高さ**なので、**その字が引ける機械**
        でしか成立しない（I-118＝ubuntu の CI では PIL が既定のビットマップ字に
        落ち、要求した px と無関係な高さになる）。
        """
        require_burnable_font(_attribution_text())
        monkeypatch.setattr(dem, "_fetch_tile", self._fake_tile)
        img = report_map.render_path_map(*path)
        assert isinstance(img, Image.Image)
        badge = _expected_badge(img)
        on_page = badge.height * report_common.A4_CONTENT_WIDTH_PX / img.width
        # 帯の高さ＝字＋余白なので、字そのもの（下限）より大きく、その 2 倍未満。
        assert report_common.MIN_FIGURE_TEXT_PX < on_page < \
            report_common.MIN_FIGURE_TEXT_PX * 2
