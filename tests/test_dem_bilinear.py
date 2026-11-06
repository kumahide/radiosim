"""
tests/test_dem_bilinear.py
==========================
計算プロファイル v2 の地形の読み（B-339）＝**双線形の読み**と**その標本の置き方**。

🔑 物差し＝**画素の値が画素の位置の 1 次式（平面）なら、双線形はどこで読んでも元の平面を
厳密に返す**＝期待値を手で書ける（近似の許容差に逃げない）。
"""

import math
import threading

import numpy as np
import pytest

from core import calc_profile
from core import dem
from core import dem_sources
from core import simulation as sim
from core import terrain_grid

_Z15 = 15
_Z14 = 14
# 合成の平面の原点（z15 の画素）＝東京付近。値が正の範囲に収まるように原点を近くに置く。
_X0 = 256 * 29100 + 10
_Y0 = 256 * 12900 + 10


def _rgb(elev_m: float) -> list[int]:
    x = int(round(elev_m * 100))
    return [(x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF]


def _plane(gx: int, gy: int) -> float:
    """画素 (gx, gy)（z15）の値＝平面。0.25 の倍数＝符号化で丸めが起きない。"""
    return 100.0 + 0.5 * (gx - _X0) + 0.25 * (gy - _Y0)


def _plane_at(wx: float, wy: float) -> float:
    """平面を画素の**中心**の値と読んだときの、任意の点の値（双線形の正解）。"""
    return 100.0 + 0.5 * (wx - 0.5 - _X0) + 0.25 * (wy - 0.5 - _Y0)


def _latlon(wx: float, wy: float, zoom: int = _Z15) -> tuple[float, float]:
    lon = wx / (256 * 2 ** zoom) * 360.0 - 180.0
    return terrain_grid.pixel_y_to_lat(wy, zoom), lon


class _FakeTiles:
    """層ごとに `value(layer, gx, gy)` から画素を作る偽の `_fetch_tile`（取得の記録つき）。"""

    def __init__(self, value):
        self.value = value
        self.calls: list[tuple] = []
        self._lock = threading.Lock()

    def __call__(self, layer_id, zoom, xtile, ytile, *_a, **_k):
        with self._lock:
            self.calls.append((layer_id, xtile, ytile))
        if self.value(layer_id, xtile * 256, ytile * 256) is None:
            return None                                  # この層にタイルが無い（404 相当）
        tile = np.zeros((256, 256, 3), dtype=np.uint8)   # (0,0,0)＝0 m＝欠損と同じ読み
        for py in range(256):
            for px in range(256):
                v = self.value(layer_id, xtile * 256 + px, ytile * 256 + py)
                if v:
                    tile[py, px] = _rgb(v)
        return tile


@pytest.fixture(autouse=True)
def _clean_tile_cache():
    dem._tile_cache.clear()
    dem._failed_tiles.clear()
    yield
    dem._tile_cache.clear()
    dem._failed_tiles.clear()


def _only_5a(fn):
    """dem5a_png だけに値がある（他の層は 404 相当で None）。"""
    first = dem_sources.GSI_DEM.layers[0][0]
    return lambda layer, gx, gy: fn(gx, gy) if layer == first else None


# ============================================================
# 読み（`dem.get_elevation(bilinear=True)`）
# ============================================================
class TestBilinearRead:

    def test_a_plane_is_reproduced_anywhere_inside_a_tile(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", _FakeTiles(_only_5a(_plane)))
        for wx, wy in ((_X0 + 3.5, _Y0 + 7.5), (_X0 + 3.2, _Y0 + 7.9), (_X0 + 40.71, _Y0 + 2.03)):
            lat, lon = _latlon(wx, wy)
            assert dem.get_elevation(lat, lon, bilinear=True) == pytest.approx(
                _plane_at(wx, wy), abs=1e-6)

    def test_the_pixel_center_reads_the_pixel_value(self, monkeypatch):
        monkeypatch.setattr(dem, "_fetch_tile", _FakeTiles(_only_5a(_plane)))
        lat, lon = _latlon(_X0 + 5.5, _Y0 + 5.5)
        assert dem.get_elevation(lat, lon, bilinear=True) == pytest.approx(
            _plane(_X0 + 5, _Y0 + 5), abs=1e-6)

    def test_v1_still_reads_the_containing_pixel(self, monkeypatch):
        """🔒 既定（v1）の読みは変わらない＝点を含む画素の値そのもの。"""
        monkeypatch.setattr(dem, "_fetch_tile", _FakeTiles(_only_5a(_plane)))
        lat, lon = _latlon(_X0 + 3.2, _Y0 + 7.9)
        assert dem.get_elevation(lat, lon) == pytest.approx(_plane(_X0 + 3, _Y0 + 7), abs=1e-6)

    def test_the_neighbour_tile_of_the_same_layer_is_read(self, monkeypatch):
        """タイルの縁の点は隣のタイルの画素も使う（同じ層）＝平面がタイルを跨いでも厳密。"""
        fake = _FakeTiles(_only_5a(_plane))
        monkeypatch.setattr(dem, "_fetch_tile", fake)
        edge_x = (_X0 // 256 + 1) * 256           # タイルの右の縁（z15 の画素）
        wx, wy = edge_x - 0.2, _Y0 + 7.9           # 左のタイルの最後の画素の右半分
        lat, lon = _latlon(wx, wy)
        assert dem.get_elevation(lat, lon, bilinear=True) == pytest.approx(
            _plane_at(wx, wy), abs=1e-6)
        first = dem_sources.GSI_DEM.layers[0][0]
        assert (first, edge_x // 256, _Y0 // 256) in fake.calls
        # 最後に値を返したタイルは、点を含む画素のタイル（出所刻印「取得日」の根拠＝B-213）
        assert dem.last_source_tile() == (dem_sources.GSI_DEM.source_id, first,
                                          edge_x // 256 - 1, _Y0 // 256)

    def test_an_invalid_neighbour_is_left_out_not_read_as_zero(self, monkeypatch):
        """欠損の近傍は重みから外す＝0 m へ引っ張らない（残りの重みで割る）。"""
        hole = (_X0 + 4, _Y0 + 8)
        monkeypatch.setattr(dem, "_fetch_tile", _FakeTiles(_only_5a(
            lambda gx, gy: 0.0 if (gx, gy) == hole else 500.0)))
        lat, lon = _latlon(_X0 + 3.9, _Y0 + 7.9)   # 4 近傍＝(3,7)(4,7)(3,8)(4,8)
        assert dem.get_elevation(lat, lon, bilinear=True) == pytest.approx(500.0, abs=1e-6)

    def test_a_missing_neighbour_tile_is_not_filled_from_another_layer(self, monkeypatch):
        """🔒 層をまたいで混ぜない＝隣のタイルが 5a に無くても 5b・10m の値を入れない。"""
        first, second = dem_sources.GSI_DEM.layers[0][0], dem_sources.GSI_DEM.layers[1][0]
        edge_x = (_X0 // 256 + 1) * 256

        def value(layer, gx, gy):
            if layer == first:
                return 300.0 if gx < edge_x else None   # 右のタイルは 5a に無い
            return 900.0                                 # 下の層は別の値

        monkeypatch.setattr(dem, "_fetch_tile", _FakeTiles(value))
        lat, lon = _latlon(edge_x - 0.2, _Y0 + 7.9)
        assert dem.get_elevation(lat, lon, bilinear=True) == pytest.approx(300.0, abs=1e-6)
        assert second not in {c[0] for c in dem._tile_cache}  # 下の層は引いてもいない

    def test_the_layer_is_chosen_by_the_containing_pixel_as_in_v1(self, monkeypatch):
        """どの層が答えるかは v1 と同じ＝点を含む画素が 5a で欠損なら 5b の中で読む。"""
        first, second = dem_sources.GSI_DEM.layers[0][0], dem_sources.GSI_DEM.layers[1][0]
        home = (_X0 + 3, _Y0 + 7)

        def value(layer, gx, gy):
            if layer == first:
                return 0.0 if (gx, gy) == home else 300.0
            if layer == second:
                return 700.0
            return None

        monkeypatch.setattr(dem, "_fetch_tile", _FakeTiles(value))
        lat, lon = _latlon(_X0 + 3.6, _Y0 + 7.6)
        assert dem.get_elevation(lat, lon) == pytest.approx(700.0)
        assert dem.get_elevation(lat, lon, bilinear=True) == pytest.approx(700.0, abs=1e-6)

    def test_the_failure_contract_is_unchanged(self, monkeypatch):
        """`nan` ⟺ 通信の失敗、`0.0` ⟺ 値が無い（404・欠損）＝v1 と同じ規約。"""
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **k: None)
        lat, lon = _latlon(_X0 + 3.6, _Y0 + 7.6)
        assert dem.get_elevation(lat, lon, bilinear=True) == 0.0
        assert not dem.network_failed()


# ============================================================
# 標本の置き方（`terrain_grid.path_sample_fractions(bilinear=True)`）
# ============================================================
_PATH = (35.503845, 139.173700, 35.504998, 139.177627)   # B-339 の再現手順の回線（378 m）


def _crossings(zoom: int, *, half: bool) -> list[float]:
    """経路が z の画素の縁（`half` なら中心の格子線）を跨ぐ t を総当たりで解く（独立の解き方）。"""
    lat0, lon0, lat1, lon1 = _PATH
    x0, y0 = terrain_grid.lonlat_to_pixel(lat0, lon0, zoom)
    x1, y1 = terrain_grid.lonlat_to_pixel(lat1, lon1, zoom)
    off = 0.5 if half else 0.0
    out = []
    for k in range(math.floor(min(x0, x1) - off) + 1, math.floor(max(x0, x1) - off) + 1):
        out.append((k + off - x0) / (x1 - x0))
    for k in range(math.floor(min(y0, y1) - off) + 1, math.floor(max(y0, y1) - off) + 1):
        lat_k = terrain_grid.pixel_y_to_lat(k + off, zoom)
        out.append((lat_k - lat0) / (lat1 - lat0))
    return [t for t in out if 0.0 < t < 1.0]


class TestLatticePlacement:

    @pytest.mark.parametrize("level,zoom", [("high", _Z15), ("medium", _Z14)])
    def test_every_kink_and_every_edge_is_a_sample(self, level, zoom):
        """折れ目（中心の格子線）と画素の縁の両方に標本がある＝2 次式の区間を点で挟む。"""
        fr = terrain_grid.path_sample_fractions(*_PATH, level, bilinear=True)
        for t in _crossings(zoom, half=True) + _crossings(zoom, half=False):
            assert min(abs(f - t) for f in fr) < 1e-9

    def test_the_10m_layer_kinks_are_samples_of_the_high_level(self):
        """5m が無く 10m へ落ちた場所でも、10m 層の折れ目を「高」の並びが取りこぼさない。"""
        fr = terrain_grid.path_sample_fractions(*_PATH, "high", bilinear=True)
        for t in _crossings(_Z14, half=True):
            assert min(abs(f - t) for f in fr) < 1e-9

    def test_the_ends_are_the_antennas_and_the_order_is_strict(self):
        fr = terrain_grid.path_sample_fractions(*_PATH, "high", bilinear=True)
        assert fr[0] == 0.0 and fr[-1] == 1.0
        assert all(a < b for a, b in zip(fr, fr[1:]))

    def test_the_count_stays_close_to_v1(self):
        """点数は v1（1 画素に 2 点）とほぼ同じ＝取得の待ち時間を増やさない。"""
        v1 = terrain_grid.path_sample_fractions(*_PATH, "high")
        v2 = terrain_grid.path_sample_fractions(*_PATH, "high", bilinear=True)
        assert abs(len(v2) - len(v1)) <= 0.05 * len(v1)

    def test_no_pixel_groups_are_folded(self):
        """双線形には畳む階段が無い＝断面図は全点をそのまま結ぶ。"""
        assert terrain_grid.sample_pixel_groups(*_PATH, "high", bilinear=True) is None
        assert terrain_grid.sample_pixel_groups(*_PATH, "high") is not None

    def test_low_stays_uniform(self):
        assert (terrain_grid.path_sample_fractions(*_PATH, "low", bilinear=True)
                == terrain_grid.path_sample_fractions(*_PATH, "low"))


# ============================================================
# 取得の配線（`simulation`）
# ============================================================
def _params(profile: str) -> sim.SimParams:
    lat0, lon0, lat1, lon1 = _PATH
    return sim.SimParams({
        "start": f"{lat0}, {lon0}", "end": f"{lat1}, {lon1}",
        "h_tx": "3", "h_rx": "3", "freq": "922", "p_tx": "13", "gain_tx": "0",
        "gain_rx": "0", "sens": "-120", "veg_h": "0", "k_factor": "1.333",
        "resolution": "high", "calc_profile": profile,
    })


class TestWiring:

    def test_each_profile_places_and_reads_its_own_way(self, monkeypatch):
        seen: list[bool] = []
        monkeypatch.setattr(dem, "get_elevation",
                            lambda la, lo, *_a, bilinear=False: seen.append(bilinear) or 10.0)
        for profile in calc_profile.PROFILES:
            params = _params(profile)
            bilinear = calc_profile.reads_bilinear(profile)
            assert params.sample_fracs == terrain_grid.path_sample_fractions(
                *_PATH, "high", bilinear=bilinear)
            assert (params.sample_pixel_groups is None) == bilinear
            assert params.num == sim.resolve_samples(*_PATH, "high", profile=profile)[0]
            seen.clear()
            done = threading.Event()
            sim.fetch_elevations(params, lambda n: None, lambda e: done.set(),
                                 lambda ex: done.set())
            assert done.wait(5)
            assert set(seen) == {bilinear}

    def test_v1_and_v2_terrains_are_cached_apart(self):
        """🔒 同じ座標・同じ段階でも、読み方が違えば地形のキャッシュを分ける。"""
        a, b = _params("v1"), _params("v2")
        b.num = a.num                                  # 点数が偶然そろっても分かれること
        assert sim._terrain_cache_key(a) != sim._terrain_cache_key(b)

    def test_the_resolution_axis_keeps_the_reading(self, monkeypatch):
        """感度の解像度軸（別段階で取り直す口）も基準と同じ読み方で刻む。"""
        got = {}

        def fake_cached(alt, on_progress, on_complete, on_error, on_acquired=None):
            got["fracs"] = alt.sample_fracs
            on_complete(np.zeros(alt.num))

        monkeypatch.setattr(sim, "fetch_elevations_cached", fake_cached)
        sim.fetch_elevations_for_resolution(_params("v2"), "medium")
        assert got["fracs"] == terrain_grid.path_sample_fractions(
            *_PATH, "medium", bilinear=True)
