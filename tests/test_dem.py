"""
tests/test_dem.py
=================
dem.py のユニットテスト（DEM/淡色地図タイル取得・標高デコード・キャッシュ）。
HTTP 通信は monkeypatch で差し替え、ネットワーク接続不要。
"""

import io
import json
import math
import os
import unittest.mock as mock

import numpy as np
import pytest
import requests
from PIL import Image

from core import config
from core import dem
from core import dem_cache
from core import dem_prefetch
from core import dem_sources


# ============================================================
# _decode_elevation
# ============================================================
class TestDecodeElevation:

    def test_invalid_pixel_128_0_0_returns_zero(self):
        """(128, 0, 0) は無効値 → 0.0 m。"""
        rgb = np.array([128, 0, 0], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx(0.0)

    def test_zero_rgb_returns_zero(self):
        rgb = np.array([0, 0, 0], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx(0.0)

    def test_positive_elevation(self):
        """x = 10000 → 100.00 m。"""
        x = 10000
        rgb = np.array([x >> 16, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx(100.0, abs=0.01)

    def test_negative_elevation(self):
        """x = 16776216 → -10.00 m（海面下）。"""
        x = 16776216
        rgb = np.array([(x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx(-10.0, abs=0.01)

    def test_boundary_x_8388607_positive(self):
        """x = 8388607 (< 8388608) → 正の標高。ただし (128,0,0) は無効値なので避ける。"""
        # x = 8388607 → r=(127, g=255, b=255) で無効値ピクセルには該当しない
        x = 8388607
        r = (x >> 16) & 0xFF   # 127
        g = (x >> 8)  & 0xFF   # 255
        b = x & 0xFF            # 255
        assert r != 128, "このテスト用ピクセルが無効値(128,0,0)と誤判定される"
        rgb = np.array([r, g, b], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx(x * 0.01, abs=0.01)

    def test_boundary_x_8388608_negative(self):
        """x = 8388608 は RGB=(128,0,0) となり無効値扱いで 0.0 を返す（仕様）。
        代わりに x=8388609 で負の標高デコードを検証する。"""
        # x=8388608 → r=128,g=0,b=0 = 無効値ピクセル → 0.0 が正しい挙動
        x_invalid = 8388608
        r = (x_invalid >> 16) & 0xFF  # 128
        g = (x_invalid >> 8)  & 0xFF  # 0
        b = x_invalid & 0xFF           # 0
        rgb_invalid = np.array([r, g, b], dtype=np.uint8)
        assert dem._decode_elevation(rgb_invalid) == pytest.approx(0.0), (
            "x=8388608 は (128,0,0) = 無効値ピクセルなので 0.0 を返す"
        )

        # x=8388609 で負の標高デコードを確認
        x = 8388609
        rgb = np.array([(x >> 16) & 0xFF, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        assert dem._decode_elevation(rgb) == pytest.approx((x - 16777216) * 0.01, abs=0.01)

    def test_external_source_invalid_rgb_returns_zero(self):
        """外部ソースの `invalid_rgb` 宣言が実際にデコードで参照されること（B-229）。

        修正前は `invalid_rgb` が検証だけ通り、デコード時に一切参照されて
        いなかった＝無効値ピクセルが Terrarium の式でそのまま `-32768.0m` に
        デコードされていた。
        """
        src = dem_sources.DemSourceSpec(
            source_id="fake_terrarium", display_name="Fake Terrarium",
            layers=(("fake", 10),),
            url_template="https://example.invalid/{z}/{x}/{y}.png",
            decode=dem_sources.DecodeMethod.TERRARIUM,
            invalid_rgb=(0, 0, 0),
            attribution="Fake", terms_url="https://example.invalid",
        )
        rgb = np.array([0, 0, 0], dtype=np.uint8)
        assert dem._decode_elevation(rgb, src) == pytest.approx(0.0)
        # 無効値でないピクセルは通常どおりデコードされる（回帰していないことの確認）。
        rgb_valid = np.array([128, 0, 0], dtype=np.uint8)
        assert dem._decode_elevation(rgb_valid, src) == pytest.approx(
            dem_sources.decode(dem_sources.DecodeMethod.TERRARIUM, 128, 0, 0)
        )


# ============================================================
# get_elevation / _fetch_tile（monkeypatch）
# ============================================================
class TestGetElevation:

    @pytest.fixture(autouse=True)
    def clear_tile_cache(self):
        """テスト間でメモリキャッシュをリセットする。"""
        dem._tile_cache.clear()
        dem._failed_tiles.clear()
        yield
        dem._tile_cache.clear()
        dem._failed_tiles.clear()

    def test_returns_float(self, monkeypatch):
        tile = np.full((256, 256, 3), [0, 39, 16], dtype=np.uint8)
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **kw: tile)
        assert isinstance(dem.get_elevation(34.5429, 132.4118), float)

    def test_uses_decoded_pixel_value(self, monkeypatch):
        """_fetch_tile が返したピクセルを正しくデコードすること。"""
        x     = 10000  # 100.00 m
        pixel = np.array([x >> 16, (x >> 8) & 0xFF, x & 0xFF], dtype=np.uint8)
        tile  = np.full((256, 256, 3), pixel, dtype=np.uint8)
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **kw: tile)
        assert dem.get_elevation(34.5429, 132.4118) == pytest.approx(100.0, abs=0.1)

    def test_returns_zero_when_fetch_returns_none(self, monkeypatch):
        """_fetch_tile が None を返したとき 0.0 になること。"""
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **kw: None)
        assert dem.get_elevation(34.5429, 132.4118) == pytest.approx(0.0)

    def test_tile_cached_after_first_call(self, monkeypatch):
        """同じタイルへの2回目の呼び出しで _fetch_tile が呼ばれないこと。"""
        tile = np.full((256, 256, 3), [0, 39, 16], dtype=np.uint8)
        call_count = {"n": 0}

        def fake_fetch(*a, **kw):
            call_count["n"] += 1
            return tile

        monkeypatch.setattr(dem, "_fetch_tile", fake_fetch)
        dem.get_elevation(34.5429, 132.4118)
        dem.get_elevation(34.5429, 132.4118)
        assert call_count["n"] == 1

    def test_network_fetch_runs_without_holding_the_cache_lock(self, monkeypatch):
        """_fetch_tile 実行中に _cache_lock を保持しないこと（dem.py の制約）。

        保持したままネットワーク取得を行うと、並列ワーカー（prefetch_tiles /
        fetch_elevations）が全員このロックで待たされ並列化が無効になる。
        _cache_lock は非再帰なので「同一スレッドで再取得できる＝未保持」。
        """
        tile = np.full((256, 256, 3), [0, 39, 16], dtype=np.uint8)
        was_held: list[bool] = []

        def fake_fetch(*a, **kw):
            acquired = dem._cache_lock.acquire(blocking=False)
            was_held.append(not acquired)
            if acquired:
                dem._cache_lock.release()
            return tile

        monkeypatch.setattr(dem, "_fetch_tile", fake_fetch)
        dem.get_elevation(34.5429, 132.4118)
        assert was_held, "_fetch_tile が呼ばれていない（キャッシュミスになっていない）"
        assert not any(was_held), "ロック保持中にネットワーク取得が行われた"


# ============================================================
# tile_acquired_date（3.3 ステージ4e＝出所刻印「取得日」）
# ============================================================
class TestTileAcquiredDate:

    _LAT, _LON = 10.0, 20.0   # 他のテストと衝突しない座標（実在チェックは無い）

    @pytest.fixture(autouse=True)
    def clear_tile_cache(self):
        dem._tile_cache.clear()
        dem._failed_tiles.clear()
        yield
        dem._tile_cache.clear()
        dem._failed_tiles.clear()

    def _write_layer0_tile(self, tmp_path, pixel, mtime=None):
        """DEM_LAYERS[0]（最優先レイヤ）のタイルファイルをディスクに置く。

        Returns: 書いたファイルパス。
        """
        from PIL import Image

        layer_id, zoom = dem.DEM_LAYERS[0]
        xtile, ytile, _, _ = dem._tile_coords(self._LAT, self._LON, zoom)
        subdir = os.path.join(str(tmp_path), layer_id, str(xtile))
        os.makedirs(subdir, exist_ok=True)
        path = os.path.join(subdir, f"{ytile}.png")
        Image.new("RGB", (256, 256), pixel).save(path)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def _layer0_key(self):
        layer_id, zoom = dem.DEM_LAYERS[0]
        xtile, ytile, _, _ = dem._tile_coords(self._LAT, self._LON, zoom)
        return ("gsi_dem", layer_id, xtile, ytile)

    def test_returns_none_when_tile_has_no_disk_file(self, tmp_path, monkeypatch):
        """ディスクに実体が無ければ None（メモリにだけある異常系も含む）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        dem._tile_cache[self._layer0_key()] = np.full(
            (256, 256, 3), [0, 39, 16], dtype=np.uint8)
        assert dem.tile_acquired_date(self._layer0_key()) is None

    def test_never_calls_fetch_tile(self, tmp_path, monkeypatch):
        """未取得のタイルでも取りに行かない（`_fetch_tile` を一切呼ばない）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        called = []
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **kw: called.append(1))
        dem.tile_acquired_date(self._layer0_key())
        assert not called

    def test_reflects_disk_tile_mtime_not_now(self, tmp_path, monkeypatch):
        """答えは**タイルファイルの mtime**（＝実行日ではない）。

        `os.utime` で過去日付を焼き込んだファイルから、その日付がそのまま
        返ることを確かめる＝「実行日」と混同していないことの検査。
        """
        import datetime as dt

        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        old = dt.datetime(2020, 1, 2, 3, 4, 5)
        self._write_layer0_tile(tmp_path, (0, 39, 16), mtime=old.timestamp())

        result = dem.tile_acquired_date(self._layer0_key())
        assert result == "2020-01-02"
        assert result != dt.date.today().isoformat()


# ============================================================
# last_source_tile（B-213＝取得日の根拠は「標高を返したタイル」）
# ============================================================
class TestLastSourceTile:

    _LAT, _LON = 10.0, 20.0

    @pytest.fixture(autouse=True)
    def clear_tile_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        dem._tile_cache.clear()
        dem._failed_tiles.clear()
        yield
        dem._tile_cache.clear()
        dem._failed_tiles.clear()

    def _key(self, layer_index):
        layer_id, zoom = dem.DEM_LAYERS[layer_index]
        xtile, ytile, _, _ = dem._tile_coords(self._LAT, self._LON, zoom)
        return ("gsi_dem", layer_id, xtile, ytile)

    def test_names_the_fallback_layer_when_the_first_fails(self, monkeypatch):
        """先頭レイヤが一時失敗して最後のレイヤで値が出たら、答えは最後のレイヤ。"""
        def fake(layer_id, *_a):
            if layer_id == dem.DEM_LAYERS[-1][0]:
                return np.full((256, 256, 3), [0, 39, 16], dtype=np.uint8)
            dem._network_trouble.flag = True
            return None

        monkeypatch.setattr(dem, "_fetch_tile", fake)
        assert dem.get_elevation(self._LAT, self._LON) != 0.0
        assert dem.last_source_tile() == self._key(-1)

    def test_names_the_memory_hit(self, monkeypatch):
        """メモリのタイルに命中した点も、そのタイルを答える。"""
        dem._tile_cache[self._key(0)] = np.full((256, 256, 3), [0, 39, 16],
                                                dtype=np.uint8)
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a: None)
        dem.get_elevation(self._LAT, self._LON)
        assert dem.last_source_tile() == self._key(0)

    def test_is_none_when_no_value_came_back(self, monkeypatch):
        """値を返さなかった点（全レイヤ失敗）は None＝前の点のタイルを持ち越さない。"""
        dem._tile_cache[self._key(0)] = np.full((256, 256, 3), [0, 39, 16],
                                                dtype=np.uint8)
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a: None)
        dem.get_elevation(self._LAT, self._LON)
        assert dem.last_source_tile() is not None
        dem._tile_cache.clear()
        assert dem.get_elevation(self._LAT, self._LON) == 0.0   # 全レイヤ 404 相当
        assert dem.last_source_tile() is None


# ============================================================
# 単一ソース強制（3.4 ステージ1・I-147）
# `core/dem.py` の `DEM_LAYERS` 直後の規則＝1 回の `get_elevation` 呼び出しは
# 単一ソースの `layers` だけを降下し、プロバイダをまたいだ降下フォールバックを
# しない。
# ============================================================
class TestSingleSourcePerCalculation:

    _LAT, _LON = 10.0, 20.0

    _OTHER_SOURCE = dem_sources.DemSourceSpec(
        source_id="other_source",
        display_name="Other Source",
        layers=(("layer_a", 10), ("layer_b", 9)),
        url_template="https://example.com/{layer}/{z}/{x}/{y}.png",
        decode=dem_sources.DecodeMethod.TERRARIUM,
        invalid_rgb=None,
        attribution="Other",
        terms_url="https://example.com",
    )

    @pytest.fixture(autouse=True)
    def clear_tile_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        dem._tile_cache.clear()
        dem._failed_tiles.clear()
        yield
        dem._tile_cache.clear()
        dem._failed_tiles.clear()

    def test_only_the_given_sources_layers_are_tried(self, monkeypatch):
        """`source=` を渡すと、その `layers` だけを降下し GSI の層は一切見ない。"""
        seen_layers: list[str] = []

        def fake(layer_id, *_a, **_kw):
            seen_layers.append(layer_id)
            return None   # 全滅させて全レイヤを踏ませる

        monkeypatch.setattr(dem, "_fetch_tile", fake)
        dem.get_elevation(self._LAT, self._LON, self._OTHER_SOURCE)

        assert seen_layers == ["layer_a", "layer_b"]
        gsi_layer_ids = {lid for lid, _z in dem.DEM_LAYERS}
        assert not gsi_layer_ids & set(seen_layers), (
            "他ソースの計算経路で国土地理院のレイヤへ降下フォールバックしている"
        )

    def test_default_source_is_gsi(self, monkeypatch):
        """`source` 省略時は国土地理院＝既存の挙動を変えない。"""
        seen_layers: list[str] = []

        def fake(layer_id, *_a, **_kw):
            seen_layers.append(layer_id)
            return None

        monkeypatch.setattr(dem, "_fetch_tile", fake)
        dem.get_elevation(self._LAT, self._LON)

        assert seen_layers == [lid for lid, _z in dem.DEM_LAYERS]

    def test_tile_cache_is_namespaced_by_source(self, monkeypatch):
        """別ソースのタイルキャッシュキーは `source_id` を含み、GSI と衝突しない。"""
        valid = (0, 39, 16)

        def fake(layer_id, zoom, xtile, ytile, cache_subdir, cache_path, source=None):
            return np.full((256, 256, 3), valid, dtype=np.uint8)

        monkeypatch.setattr(dem, "_fetch_tile", fake)
        dem.get_elevation(self._LAT, self._LON, self._OTHER_SOURCE)

        assert any(key[0] == "other_source" for key in dem._tile_cache)
        assert not any(key[0] == "gsi_dem" for key in dem._tile_cache)

    def test_disk_cache_path_is_namespaced_by_source(self):
        """別ソースのディスクキャッシュは `CACHE_DIR/external/<source_id>/<定義ハッシュ>/...`
        へ分離される（B-274＝専用の名前空間の下なので組み込みの置き場と衝突しない）。"""
        fp = dem_sources.definition_fingerprint(self._OTHER_SOURCE)
        path = dem._cache_subdir_for(self._OTHER_SOURCE, "layer_a", 123)
        assert os.path.normpath(path) == os.path.normpath(
            os.path.join(dem.CACHE_DIR, dem.DEM_EXTERNAL_SUBDIR,
                         "other_source", fp, "layer_a", "123"))

    def test_gsi_disk_cache_path_is_unchanged(self):
        """国土地理院はソース分離の対象外＝既存キャッシュを移さない（完了条件①）。"""
        path = dem._cache_subdir_for(dem_sources.GSI_DEM, "dem5a_png", 123)
        assert os.path.normpath(path) == os.path.normpath(
            os.path.join(dem.CACHE_DIR, "dem5a_png", "123"))

    def test_disk_cache_path_changes_when_source_definition_changes(self):
        """`source_id` が同じでも定義（URL 等）が変わればキャッシュ置き場も変わる（B-236）。

        変わらないと、宣言ファイルを書き換えたのに旧タイルを新しい解釈で
        読み直し、誤った標高が静かに返る。
        """
        import dataclasses
        redefined = dataclasses.replace(
            self._OTHER_SOURCE,
            url_template="https://example.com/changed/{layer}/{z}/{x}/{y}.png",
        )
        path_before = dem._cache_subdir_for(self._OTHER_SOURCE, "layer_a", 123)
        path_after = dem._cache_subdir_for(redefined, "layer_a", 123)
        assert path_before != path_after


class TestFetchTile:

    def _mock_session(self, monkeypatch, *, side_effect=None, return_value=None):
        """_get_session() をモックセッションに差し替えるヘルパー。"""
        fake_session = mock.Mock()
        if side_effect is not None:
            fake_session.get.side_effect = side_effect
        else:
            fake_session.get.return_value = return_value
        monkeypatch.setattr(dem, "_get_session", lambda: fake_session)
        return fake_session

    def test_returns_none_on_network_error_no_cache(self, tmp_path, monkeypatch):
        """ネットワークエラー＆キャッシュなし → None。"""
        self._mock_session(monkeypatch, side_effect=requests.RequestException("timeout"))
        result = dem._fetch_tile(
            "dem_png", 14, 99999, 99999, str(tmp_path), str(tmp_path / "x.png")
        )
        assert result is None

    def test_uses_disk_cache_on_network_error(self, tmp_path, monkeypatch):
        """ネットワークエラー時にディスクキャッシュがあればそれを返す。"""
        from PIL import Image

        cache_path = tmp_path / "tile.png"
        Image.new("RGB", (256, 256), (0, 39, 16)).save(str(cache_path))

        self._mock_session(monkeypatch, side_effect=requests.RequestException("err"))
        arr = dem._fetch_tile("dem_png", 14, 0, 0, str(tmp_path), str(cache_path))
        assert arr is not None
        assert arr.shape == (256, 256, 3)

    def test_saves_tile_to_disk_on_200(self, tmp_path, monkeypatch):
        """HTTP 200 レスポンス時にタイルをディスクに保存すること。"""
        from PIL import Image
        import io

        img = Image.new("RGB", (256, 256), (0, 39, 16))
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        fake_response = mock.Mock()
        fake_response.status_code = 200
        fake_response.content     = buf.getvalue()
        self._mock_session(monkeypatch, return_value=fake_response)

        cache_path = str(tmp_path / "tile.png")
        dem._fetch_tile("dem5a_png", 15, 0, 0, str(tmp_path), cache_path)
        assert os.path.exists(cache_path)

    def test_returns_array_on_200(self, tmp_path, monkeypatch):
        """HTTP 200 レスポンス時に numpy 配列を返すこと。"""
        from PIL import Image
        import io

        img = Image.new("RGB", (256, 256), (10, 20, 30))
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        fake_response = mock.Mock()
        fake_response.status_code = 200
        fake_response.content     = buf.getvalue()
        self._mock_session(monkeypatch, return_value=fake_response)

        arr = dem._fetch_tile("dem5a_png", 15, 0, 0, str(tmp_path), str(tmp_path / "t.png"))
        assert isinstance(arr, np.ndarray)
        assert arr.shape == (256, 256, 3)

    def test_304_uses_existing_cache(self, tmp_path, monkeypatch):
        """HTTP 304 時（If-Modified-Since）はキャッシュファイルを使うこと。"""
        from PIL import Image

        cache_path = tmp_path / "tile.png"
        Image.new("RGB", (256, 256), (0, 39, 16)).save(str(cache_path))

        fake_response = mock.Mock()
        fake_response.status_code = 304
        self._mock_session(monkeypatch, return_value=fake_response)

        arr = dem._fetch_tile("dem_png", 14, 0, 0, str(tmp_path), str(cache_path))
        assert arr is not None
        assert arr.shape == (256, 256, 3)


# ============================================================
# タイルキャッシュの原子的な書き込み — B-123 回帰ガード
# ============================================================
class TestAtomicTileWrite:
    """**壊れた不変条件**＝「他スレッドから見えるキャッシュファイルは、常に完全」。

    非原子的な `open(cache_path, "wb")` だと、並列取得中に**書き込み途中の PNG**を
    別スレッドが開いて復号に失敗し、その点が黙って標高 0.0（＝海抜 0m と区別が
    つかない）になる（2026-08-24 に実測＝26 回線で 4 点）。

    ⚠️ **速さに頼ったテストは「一度も落ちないゲート」になる**（素の書き込みは速く、
    運任せでは途中を捕まえられない）。⇒ 書き込みを**分割して遅くしたフェイク**を
    噛ませ、競合を必ず起こす形にしてから測る。
    """

    def _png_bytes(self, seed=0, size=256):
        from PIL import Image
        import io
        buf = io.BytesIO()
        # 一様色は PNG が極端に縮むので、分割書き込みが効く程度の大きさを作る。
        img = Image.fromarray(
            np.random.default_rng(seed).integers(0, 256, (size, size, 3), dtype=np.uint8)
        )
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _slow_open(self, monkeypatch, target_path, chunk=512, delay=0.001):
        """`target_path` への "wb" だけ、分割＋スリープで書くように差し替える。"""
        import builtins
        import time
        real_open = builtins.open

        class _SlowFile:
            def __init__(self, fh):
                self._fh = fh

            def write(self, data):
                for i in range(0, len(data), chunk):
                    self._fh.write(data[i:i + chunk])
                    self._fh.flush()
                    os.fsync(self._fh.fileno())
                    time.sleep(delay)
                return len(data)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return self._fh.__exit__(*exc)

        def fake_open(path, mode="r", *a, **kw):
            fh = real_open(path, mode, *a, **kw)
            if "b" in mode and "w" in mode and str(path).startswith(str(target_path)):
                return _SlowFile(fh)
            return fh

        monkeypatch.setattr(builtins, "open", fake_open)

    def test_partial_file_is_never_visible_at_cache_path(self, tmp_path, monkeypatch):
        """書き込みの最中、`cache_path` には**途中のファイルが一切見えない**こと。

        ⚠️ 監視側は `getsize` だけを見る（開かない）＝ Windows で読み取り中の
        `os.replace` が PermissionError になるのを避け、製品の並列度を再現する。
        """
        import threading
        import time

        data = self._png_bytes()
        cache_path = str(tmp_path / "tile.png")
        self._slow_open(monkeypatch, cache_path)

        stop = threading.Event()
        partial_sizes = []

        def watcher():
            while not stop.is_set():
                try:
                    size = os.path.getsize(cache_path)
                except OSError:
                    continue          # 無い＝正常（「無いか、完全か」の片側）
                if size != len(data):
                    partial_sizes.append(size)
                time.sleep(0)

        t = threading.Thread(target=watcher, daemon=True)
        t.start()
        try:
            dem._write_tile_atomic(cache_path, data)
        finally:
            stop.set()
            t.join(timeout=5)

        assert not partial_sizes, (
            f"書き込み途中のファイルが cache_path に見えた（サイズ {partial_sizes[:5]}）"
        )
        assert os.path.getsize(cache_path) == len(data)

    def test_concurrent_writers_leave_a_decodable_tile(self, tmp_path, monkeypatch):
        """同じタイルを 4 スレッドが同時に書いても、残るのは**どれか 1 つの完全な
        内容**であること（混ざらない）。

        ⚠️ 4 者に**別々の内容**を書かせる＝全員が同じバイト列だと、混ざっても結果が
        同じになり、一時ファイル名の衝突を見逃す。
        """
        import threading
        from PIL import Image

        payloads = [self._png_bytes(seed=i) for i in range(4)]
        cache_path = str(tmp_path / "tile.png")
        self._slow_open(monkeypatch, cache_path)

        threads = [
            threading.Thread(target=dem._write_tile_atomic, args=(cache_path, d))
            for d in payloads
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        with open(cache_path, "rb") as f:
            written = f.read()
        assert written in payloads, "複数スレッドの書き込みが混ざった内容が残っている"

        arr = np.array(Image.open(cache_path).convert("RGB"))
        assert arr.shape == (256, 256, 3)
        # 一時ファイルを置き去りにしない。
        assert [p.name for p in tmp_path.iterdir()] == ["tile.png"]

    def test_transient_read_failure_is_retried(self, tmp_path, monkeypatch):
        """一瞬読めなかっただけのキャッシュは、粘って読み直すこと。

        `os.replace` が走っている最中、Windows は置換先を開かせない
        （実測＝`[Errno 13] Permission denied`）。ここで諦めると**内容は健全なのに
        その点が 0.0 になる**。
        """
        from PIL import Image
        cache_path = tmp_path / "tile.png"
        Image.new("RGB", (256, 256), (0, 39, 16)).save(str(cache_path))

        calls = []
        real_pil_open = Image.open

        def flaky_open(path, *a, **kw):
            calls.append(str(path))
            if len(calls) == 1:
                raise PermissionError(13, "Permission denied")
            return real_pil_open(path, *a, **kw)

        monkeypatch.setattr(dem.Image, "open", flaky_open)
        arr = dem._read_cached_tile(str(cache_path))

        assert arr is not None and arr.shape == (256, 256, 3)
        assert len(calls) == 2, "1 回で諦めている（＝置換中の一瞬で 0.0 に化ける）"

    def test_unreadable_cache_falls_back_to_network(self, tmp_path, monkeypatch):
        """粘っても読めないキャッシュは**キャッシュミス扱い**にして取り直すこと。

        ⚠️ ここで例外を上げると `get_elevation` の except に握られて 0.0 になる＝
        書き込み側で塞いだ穴を読み側に開け直す。
        """
        cache_path = tmp_path / "tile.png"
        cache_path.write_bytes(b"not a png at all")

        fake_response = mock.Mock()
        fake_response.status_code = 200
        fake_response.content     = self._png_bytes(seed=7)
        fake_session = mock.Mock()
        fake_session.get.return_value = fake_response
        monkeypatch.setattr(dem, "_get_session", lambda: fake_session)
        monkeypatch.setattr(dem, "_TILE_READ_RETRY_S", 0.0)

        arr = dem._fetch_tile(
            "dem5a_png", 15, 0, 0, str(tmp_path), str(cache_path)
        )
        assert isinstance(arr, np.ndarray)
        assert arr.shape == (256, 256, 3)
        assert fake_session.get.called, "ネットワークへ落ちていない（0.0 になる経路）"

    def test_unreadable_cache_is_replaced_by_the_refetch(self, tmp_path, monkeypatch):
        """読めないと確定したキャッシュは、取り直したデータで**置き換わる**こと（B-136）。

        ⚠️ これが無いと、上の `..._falls_back_to_network` は**緑のまま欠陥を見逃す**
        ＝その回の値は正しいので「取り直せている」と読めてしまう。壊れたファイルが
        残り続けると、**オフラインや通信失敗の回に粗い層か標高 0 へ落ちる**（B-123 で
        塞いだ穴の残り半分）。
        """
        cache_path = tmp_path / "tile.png"
        cache_path.write_bytes(b"not a png at all")

        payload = self._png_bytes(seed=11)
        fake_response = mock.Mock()
        fake_response.status_code = 200
        fake_response.content     = payload
        fake_session = mock.Mock()
        fake_session.get.return_value = fake_response
        monkeypatch.setattr(dem, "_get_session", lambda: fake_session)
        monkeypatch.setattr(dem, "_TILE_READ_RETRY_S", 0.0)

        dem._fetch_tile("dem5a_png", 15, 0, 0, str(tmp_path), str(cache_path))

        assert cache_path.read_bytes() == payload, (
            "壊れたキャッシュが残っている（次の起動でまた取りに行き、"
            "オフラインなら標高 0 へ落ちる）"
        )
        assert dem._read_cached_tile(str(cache_path)) is not None
        # 一時ファイルを置き去りにしない。
        assert [p.name for p in tmp_path.iterdir()] == ["tile.png"]

    def test_repair_does_not_rewrite_a_readable_tile(self, tmp_path, monkeypatch):
        """**読める**キャッシュは、置換の口を開けたあとも書き直さないこと（B-136 の対）。

        ⚠️ 「無いことの検査」を対で置く＝直しが行き過ぎて *常に上書き* になると、
        B-123 で避けた競合（Windows は読まれている最中の置換を拒む）が戻る。
        ここが緑でなければ、上の置換テストは*ただ上書きしているだけ*と区別できない。
        """
        from PIL import Image
        cache_path = tmp_path / "tile.png"
        Image.new("RGB", (256, 256), (1, 2, 3)).save(str(cache_path))
        before = cache_path.read_bytes()

        fake_session = mock.Mock()
        monkeypatch.setattr(dem, "_get_session", lambda: fake_session)

        arr = dem._fetch_tile(
            "dem5a_png", 15, 0, 0, str(tmp_path), str(cache_path)
        )

        assert isinstance(arr, np.ndarray)
        assert not fake_session.get.called, "読めるキャッシュがあるのに取得へ行った"
        assert cache_path.read_bytes() == before

    def test_existing_tile_is_not_rewritten(self, tmp_path, monkeypatch):
        """既に完全なタイルが在るなら**書きに行かない**こと。

        同じ URL のタイルは同じ内容なので、上書きしても得るものが無く、
        Windows の「読まれている最中は置換できない」競合だけを増やす。
        """
        import builtins
        cache_path = tmp_path / "tile.png"
        cache_path.write_bytes(b"already-here")

        real_open = builtins.open
        opened = []

        def spy_open(path, mode="r", *a, **kw):
            if "w" in mode:
                opened.append(str(path))
            return real_open(path, mode, *a, **kw)

        monkeypatch.setattr(builtins, "open", spy_open)
        dem._write_tile_atomic(str(cache_path), self._png_bytes())

        assert opened == [], f"既存タイルがあるのに書きに行った: {opened}"
        assert cache_path.read_bytes() == b"already-here"

    def test_write_failure_is_swallowed_and_leaves_no_temp(self, tmp_path, monkeypatch):
        """キャッシュに書けなくても**例外を上げない**こと（＋一時ファイルを残さない）。

        ここで例外を上げると `get_elevation` の except に落ち、**通信は成功して
        いるのに 0.0** になる＝直そうとしている欠陥そのものを別経路で作る。
        """
        import builtins
        real_open = builtins.open

        def failing_open(path, mode="r", *a, **kw):
            if "b" in mode and "w" in mode and str(path).startswith(str(tmp_path)):
                raise OSError("disk full")
            return real_open(path, mode, *a, **kw)

        monkeypatch.setattr(builtins, "open", failing_open)

        cache_path = str(tmp_path / "tile.png")
        dem._write_tile_atomic(cache_path, b"x" * 100)   # 例外が出なければ合格

        assert not os.path.exists(cache_path)
        assert list(tmp_path.iterdir()) == []

    def test_fetch_tile_returns_array_even_if_cache_write_fails(
        self, tmp_path, monkeypatch
    ):
        """書き込みに失敗しても `_fetch_tile` は取得済みの配列を返すこと。"""
        fake_response = mock.Mock()
        fake_response.status_code = 200
        fake_response.content     = self._png_bytes()
        fake_session = mock.Mock()
        fake_session.get.return_value = fake_response
        monkeypatch.setattr(dem, "_get_session", lambda: fake_session)
        monkeypatch.setattr(
            dem, "_write_tile_atomic",
            # ⚠️ **代役が本物より狭いと、その差の分だけ検査が空振りする**
            #    （`replace_broken` を受け取らない代役は呼び出し形の変化を隠す）。
            mock.Mock(side_effect=lambda p, d, **kw: None),
        )

        arr = dem._fetch_tile(
            "dem5a_png", 15, 0, 0, str(tmp_path), str(tmp_path / "t.png")
        )
        assert isinstance(arr, np.ndarray)
        assert arr.shape == (256, 256, 3)


# ============================================================
# 失敗タイルの負キャッシュ（_failed_tiles）— B-010 回帰ガード
# ============================================================
class TestFailedTileNegativeCache:
    """一時失敗（タイムアウト・接続エラー・5xx）で取得に失敗したタイルを
    _failed_tiles に入れてはならない。入れると回復後もそのタイルを無視し続け、
    標高が 0.0 や粗レイヤ値に化けたまま黙って誤る（B-010）。恒久欠落（404）
    だけは負キャッシュに入れて再リクエストを抑止する。
    """

    @pytest.fixture(autouse=True)
    def isolate_state(self, tmp_path, monkeypatch):
        """メモリキャッシュを空にし、ディスクキャッシュを一時ディレクトリへ隔離。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        dem._tile_cache.clear()
        dem._failed_tiles.clear()
        yield
        dem._tile_cache.clear()
        dem._failed_tiles.clear()

    def _mock_session(self, monkeypatch, get_impl):
        fake_session = mock.Mock()
        fake_session.get.side_effect = get_impl
        monkeypatch.setattr(dem, "_get_session", lambda: fake_session)
        return fake_session

    @staticmethod
    def _png_200(elev_pixel=(0, 39, 16)):
        """指定ピクセル（既定 = 100.0 m）を返す HTTP 200 レスポンス。"""
        import io
        from PIL import Image

        img = Image.new("RGB", (256, 256), tuple(elev_pixel))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        resp = mock.Mock()
        resp.status_code = 200
        resp.content     = buf.getvalue()
        return resp

    @staticmethod
    def _status(code):
        resp = mock.Mock()
        resp.status_code = code
        return resp

    # ── _fetch_tile 単体：404 だけが負キャッシュに入る ────────────────
    def test_404_populates_failed_tiles(self, tmp_path, monkeypatch):
        self._mock_session(monkeypatch, lambda *a, **k: self._status(404))
        result = dem._fetch_tile(
            "dem_png", 14, 111, 222, str(tmp_path), str(tmp_path / "a.png")
        )
        assert result is None
        assert ("gsi_dem", "dem_png", 111, 222) in dem._failed_tiles

    def test_transient_5xx_does_not_populate_failed_tiles(self, tmp_path, monkeypatch):
        self._mock_session(monkeypatch, lambda *a, **k: self._status(503))
        result = dem._fetch_tile(
            "dem_png", 14, 111, 222, str(tmp_path), str(tmp_path / "a.png")
        )
        assert result is None
        assert ("dem_png", 111, 222) not in dem._failed_tiles

    def test_transient_exception_does_not_populate_failed_tiles(self, tmp_path, monkeypatch):
        self._mock_session(
            monkeypatch,
            lambda *a, **k: (_ for _ in ()).throw(requests.RequestException("timeout")),
        )
        result = dem._fetch_tile(
            "dem_png", 14, 111, 222, str(tmp_path), str(tmp_path / "a.png")
        )
        assert result is None
        assert ("dem_png", 111, 222) not in dem._failed_tiles

    # ── 「取れなかった」を別口で知らせる（B-025 ②）────────────────────
    def test_network_failure_is_reported_alongside_the_zero(self, monkeypatch):
        """通信で失敗したら `nan` を返し、`network_failed()` も立つこと（3.2）。

        🔴 **3.2 で戻り値そのものが変わった**（ISSUES.md B-025 ①）＝以前は
        `network_failed()` だけが「取れていない」を知る唯一の手段だったが、
        今は戻り値（`nan`）自体がそれを語る。両方が同時に真であること
        （不変条件）を確かめる。
        """
        self._mock_session(
            monkeypatch,
            lambda *a, **k: (_ for _ in ()).throw(requests.RequestException("timeout")),
        )
        assert math.isnan(dem.get_elevation(34.5429, 132.4118))
        assert dem.network_failed(), "通信の失敗が呼び出し側に伝わらない"

    def test_sea_tiles_404_are_not_a_network_failure(self, monkeypatch):
        """**404 では立たない**こと（海上・日本域外＝通信は成功している）。

        ここを混ぜると、海の上を通る経路が「ネットワーク異常」として打ち切られる
        ＝B-010（一時失敗を負キャッシュに入れて標高を誤った）の鏡像。
        """
        self._mock_session(monkeypatch, lambda *a, **k: self._status(404))
        assert dem.get_elevation(34.5429, 132.4118) == pytest.approx(0.0)
        assert not dem.network_failed(), "404（データが無い）を通信失敗と混同している"

    def test_a_value_from_a_later_layer_clears_the_failure(self, monkeypatch):
        """先のレイヤが通信失敗でも、後のレイヤで値が取れたら失敗ではないこと。"""
        state = {"n": 0}

        def get_impl(*a, **k):
            state["n"] += 1
            if state["n"] == 1:
                raise requests.RequestException("timeout")   # 1 層目だけ失敗
            return self._png_200()

        self._mock_session(monkeypatch, get_impl)
        assert dem.get_elevation(34.5429, 132.4118) == pytest.approx(100.0, abs=0.1)
        assert not dem.network_failed(), "値が取れているのに失敗として扱っている"

    # ── get_elevation 統合：一時失敗 → 回復で取得し直せる（B-010 本丸）──
    def test_transient_failure_then_recovery_refetches(self, monkeypatch):
        """一時失敗の後で通信が回復したら、同一プロセスでも標高を取得し直す。"""
        state = {"recovered": False}

        def get_impl(*a, **k):
            if not state["recovered"]:
                raise requests.RequestException("timeout")
            return self._png_200()

        self._mock_session(monkeypatch, get_impl)

        # 1回目：全レイヤが一時失敗 → nan（3.2）、負キャッシュは汚れない。
        assert math.isnan(dem.get_elevation(34.5429, 132.4118))
        assert not dem._failed_tiles, "一時失敗を負キャッシュに入れてはならない"

        # 2回目：回復後は正しい標高（100.0 m）を取得できる。
        state["recovered"] = True
        assert dem.get_elevation(34.5429, 132.4118) == pytest.approx(100.0, abs=0.1)

    def test_404_is_remembered_and_skips_refetch(self, monkeypatch):
        """恒久欠落（404）は負キャッシュに入り、2回目はネットワークを叩かない。"""
        fake = self._mock_session(monkeypatch, lambda *a, **k: self._status(404))

        assert dem.get_elevation(34.5429, 132.4118) == pytest.approx(0.0)
        calls_after_first = fake.get.call_count
        assert calls_after_first >= 1

        # 2回目：全レイヤが負キャッシュ済み → _fetch_tile を呼ばず get 追加なし。
        assert dem.get_elevation(34.5429, 132.4118) == pytest.approx(0.0)
        assert fake.get.call_count == calls_after_first

    def test_unexpected_exception_returns_nan_and_sets_network_failed(self, monkeypatch):
        """デコード周りの想定外の例外も `nan` になり、`network_failed()` も立つこと。

        🆕 3.2（ISSUES.md B-025 ①）＝以前はここも `0.0`（＝海抜0mと区別が付か
        ない）だった。**不変条件**（`nan` ⟺ `network_failed()`）を、通信失敗
        以外の想定外の例外でも保つことを確かめる。
        """
        self._mock_session(
            monkeypatch,
            lambda *a, **k: (_ for _ in ()).throw(ValueError("corrupt tile")),
        )
        assert math.isnan(dem.get_elevation(34.5429, 132.4118))
        assert dem.network_failed(), "想定外の例外で不変条件が壊れている"


# ============================================================
# プロキシ / セッション管理
# ============================================================
class TestProxy:

    def test_proxy_url_in_default_config(self):
        """proxy_url が DEFAULT_CONFIG に含まれていること。"""
        assert "proxy_url" in config.DEFAULT_CONFIG
        assert config.DEFAULT_CONFIG["proxy_url"] == ""

    def test_load_config_fills_proxy_url(self, tmp_path):
        """proxy_url が未定義の古い config.json でもデフォルト補完されること。"""
        cfg_path = str(tmp_path / "conf.json")
        with open(cfg_path, "w") as f:
            json.dump({"freq": "2400.0"}, f)
        loaded = config.load_config(cfg_path)
        assert "proxy_url" in loaded
        assert loaded["proxy_url"] == ""

    def test_set_proxy_resets_session(self):
        """set_proxy() を呼ぶと既存セッションが破棄されること。"""
        dem.set_proxy("")
        s1 = dem._get_session()
        dem.set_proxy("http://proxy.example.com:8080")
        assert dem._http_session is None  # リセット確認
        s2 = dem._get_session()
        assert s1 is not s2

    def test_get_session_singleton(self):
        """_get_session() は同一セッションを返すこと（再生成しない）。"""
        dem.set_proxy("")
        s1 = dem._get_session()
        s2 = dem._get_session()
        assert s1 is s2

    def teardown_method(self):
        """各テスト後にセッションをリセットしてテスト間干渉を防ぐ。"""
        dem.set_proxy("")


# ============================================================
# _enumerate_bbox / count_bbox_tiles
# ============================================================
class TestEnumerateBbox:

    def test_returns_6_tuple_per_tile(self):
        tiles = dem_cache._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        assert all(len(t) == 6 for t in tiles)

    def test_covers_all_dem_layers(self):
        tiles = dem_cache._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        layer_ids = {t[0] for t in tiles}
        assert layer_ids == {lid for lid, _ in dem.DEM_LAYERS}

    def test_at_least_one_tile_per_layer(self):
        tiles = dem_cache._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        for layer_id, _ in dem.DEM_LAYERS:
            assert any(t[0] == layer_id for t in tiles)

    def test_inverted_coords_same_result(self):
        """lat1/lon1 が NW でなくても同じ結果を返す（入力順に依存しない）。"""
        tiles_nw_se = dem_cache._enumerate_bbox(34.54, 132.40, 34.53, 132.41)
        tiles_se_nw = dem_cache._enumerate_bbox(34.53, 132.41, 34.54, 132.40)
        assert set(t[:4] for t in tiles_nw_se) == set(t[:4] for t in tiles_se_nw)

    def test_larger_area_returns_more_tiles(self):
        small = dem_cache._enumerate_bbox(34.540, 132.410, 34.539, 132.409)
        large = dem_cache._enumerate_bbox(34.600, 132.500, 34.400, 132.300)
        assert len(large) > len(small)

    def test_tile_coords_in_valid_range(self):
        """タイル座標がズームレベルに対して有効な範囲内であること。"""
        tiles = dem_cache._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        for layer_id, zoom, x, y, subdir, cache_path in tiles:
            assert 0 <= x < 2 ** zoom
            assert 0 <= y < 2 ** zoom

    def test_cache_path_contains_layer_and_coords(self):
        """cache_path が layer_id / x / y.png の構造を持つこと。"""
        tiles = dem_cache._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        for layer_id, zoom, x, y, subdir, cache_path in tiles:
            assert layer_id in cache_path
            assert str(x) in cache_path
            assert cache_path.endswith(f"{y}.png")


class TestCountBboxTiles:

    def test_returns_zoom14_position_count(self):
        """count_bbox_tiles は zoom-14 位置数（エリア数）を返す。"""
        lat1, lon1, lat2, lon2 = 34.54, 132.41, 34.53, 132.40
        count = dem_prefetch.count_bbox_tiles(lat1, lon1, lat2, lon2)
        positions = list(dem_prefetch._iter_dem_positions(lat1, lon1, lat2, lon2))
        assert count == len(positions)

    def test_returns_positive_integer(self):
        count = dem_prefetch.count_bbox_tiles(34.54, 132.41, 34.53, 132.40)
        assert isinstance(count, int)
        assert count > 0

    def test_inverted_coords_same_result(self):
        """入力座標の順序に依存しないこと。"""
        assert dem_prefetch.count_bbox_tiles(34.54, 132.41, 34.53, 132.40) == \
               dem_prefetch.count_bbox_tiles(34.53, 132.40, 34.54, 132.41)


# ============================================================
# _iter_dem_positions
# ============================================================
class TestIterDemPositions:

    def test_yields_tuples_with_correct_structure(self):
        """各 yield 値が (x14, y14, subdir, path, zoom15_tiles) の構造を持つ。"""
        positions = list(dem_prefetch._iter_dem_positions(34.54, 132.41, 34.53, 132.40))
        assert len(positions) > 0
        for x14, y14, subdir, path, zoom15_tiles in positions:
            assert isinstance(x14, int)
            assert isinstance(y14, int)
            assert path.endswith(f"{y14}.png")
            assert str(x14) in path
            assert len(zoom15_tiles) >= 1

    def test_zoom15_tiles_are_sub_tiles_of_zoom14(self):
        """zoom-15 サブタイルが対応する zoom-14 の子タイル範囲内に収まること。"""
        positions = list(dem_prefetch._iter_dem_positions(34.54, 132.41, 34.53, 132.40))
        for x14, y14, _, _, zoom15_tiles in positions:
            for x15, y15, *_ in zoom15_tiles:
                assert x14 * 2 <= x15 <= x14 * 2 + 1
                assert y14 * 2 <= y15 <= y14 * 2 + 1

    def test_inverted_coords_same_result(self):
        pos_ab = list(dem_prefetch._iter_dem_positions(34.54, 132.41, 34.53, 132.40))
        pos_ba = list(dem_prefetch._iter_dem_positions(34.53, 132.40, 34.54, 132.41))
        assert [(x, y) for x, y, *_ in pos_ab] == [(x, y) for x, y, *_ in pos_ba]


# ============================================================
# _process_position
# ============================================================
def _land_tile():
    """全画素が標高 0.01 m の、欠損の無いタイル。

    ⚠️ `np.zeros` は使わない（B-304）＝(0, 0, 0) はちょうど 0 m で、計算は下の
    レイヤへ降りる＝事前取得の判定でも「欠損」に数える画素。
    """
    arr = np.zeros((256, 256, 3), dtype=np.uint8)
    arr[:, :, 2] = 1
    return arr



class TestProcessPosition:

    def _make_counts(self):
        return {"downloaded_5a": 0, "downloaded_5b": 0, "downloaded_dem": 0,
                "skipped": 0, "failed": 0}

    def test_skips_when_dem_cached_and_no_force(self, tmp_path, monkeypatch):
        """dem_png キャッシュあり・force=False → skipped。"""
        import threading
        from PIL import Image
        dem_path = tmp_path / "dem.png"
        Image.new("RGB", (256, 256)).save(str(dem_path))
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **kw: None)
        counts = self._make_counts()
        lock = threading.Lock()
        dem_prefetch._process_position(0, 0, str(tmp_path), str(dem_path), [], False, counts, lock)
        assert counts["skipped"] == 1
        assert counts["downloaded_5a"] == counts["downloaded_5b"] == counts["downloaded_dem"] == 0

    def test_downloads_5a_when_available(self, tmp_path, monkeypatch):
        """5a DL 成功 → downloaded_5a 増加・5b/dem は試みない。"""
        import threading
        tile_arr = _land_tile()
        fetch_calls = []

        def mock_fetch(layer_id, *a, **kw):
            fetch_calls.append(layer_id)
            return tile_arr if layer_id == "dem5a_png" else None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        subdir5a = str(tmp_path / "5a" / "0"); subdir5b = str(tmp_path / "5b" / "0")
        zoom15 = [(0, 0, subdir5a, str(tmp_path / "5a.png"),
                         subdir5b, str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem_prefetch._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert counts["downloaded_5a"] == 1
        assert counts["downloaded_5b"] == 0
        assert "dem5b_png" not in fetch_calls

    def test_falls_back_to_5b_when_5a_fails(self, tmp_path, monkeypatch):
        """5a 失敗 → 5b 試みる → downloaded_5b 増加。"""
        import threading
        tile_arr = _land_tile()

        def mock_fetch(layer_id, *a, **kw):
            return tile_arr if layer_id == "dem5b_png" else None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem_prefetch._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert counts["downloaded_5b"] == 1
        assert counts["downloaded_dem"] == 0

    def test_falls_back_to_dem_when_both_5m_fail(self, tmp_path, monkeypatch):
        """5a・5b 両方失敗 → dem_png DL。"""
        import threading
        tile_arr = _land_tile()

        def mock_fetch(layer_id, *a, **kw):
            return tile_arr if layer_id == "dem_png" else None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem_prefetch._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert counts["downloaded_dem"] == 1
        assert counts["failed"] == 0

    def test_force_ignores_existing_cache(self, tmp_path, monkeypatch):
        """force=True: dem_png キャッシュがあっても再取得する。"""
        import threading
        from PIL import Image
        dem_path = tmp_path / "dem.png"
        Image.new("RGB", (256, 256)).save(str(dem_path))
        tile_arr = _land_tile()

        def mock_fetch(layer_id, *a, **kw):
            return tile_arr if layer_id == "dem5a_png" else None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem_prefetch._process_position(0, 0, str(tmp_path), str(dem_path),
                                 zoom15, True, counts, lock)
        assert counts["skipped"] == 0
        assert counts["downloaded_5a"] == 1

    @staticmethod
    def _void_tile(void=True):
        """全画素 (128,0,0) の欠損タイル、または全画素有効（0.01 m）のタイル。"""
        arr = _land_tile()
        if void:
            arr[:] = (128, 0, 0)
        return arr

    def test_descends_to_5b_when_5a_has_void(self, tmp_path, monkeypatch):
        """5a 取得成功だが欠損あり・5b が補完 → 5b も取得し dem は不要。"""
        import threading
        valid = _land_tile()

        def mock_fetch(layer_id, *a, **kw):
            if layer_id == "dem5a_png":
                return self._void_tile(void=True)    # 5a は全欠損
            if layer_id == "dem5b_png":
                return valid                          # 5b が補完
            return None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem_prefetch._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert counts["downloaded_5a"] == 1
        assert counts["downloaded_5b"] == 1
        assert counts["downloaded_dem"] == 0

    def test_descends_to_dem_when_5a_and_5b_void(self, tmp_path, monkeypatch):
        """5a・5b とも同一画素が欠損 → dem_png まで降りる（終端確定）。"""
        import threading

        def mock_fetch(layer_id, *a, **kw):
            if layer_id in ("dem5a_png", "dem5b_png"):
                return self._void_tile(void=True)    # 両方とも全欠損
            if layer_id == "dem_png":
                return _land_tile()
            return None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem_prefetch._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert counts["downloaded_5a"] == 1
        assert counts["downloaded_5b"] == 1
        assert counts["downloaded_dem"] == 1

    def test_no_descent_when_5a_void_free(self, tmp_path, monkeypatch):
        """5a が欠損なし → 5b/dem は一切試みない（DL 最小）。"""
        import threading
        fetch_calls = []

        def mock_fetch(layer_id, *a, **kw):
            fetch_calls.append(layer_id)
            return _land_tile() if layer_id == "dem5a_png" else None

        monkeypatch.setattr(dem, "_fetch_tile", mock_fetch)
        zoom15 = [(0, 0, str(tmp_path), str(tmp_path / "5a.png"),
                         str(tmp_path), str(tmp_path / "5b.png"))]
        counts = self._make_counts()
        lock = threading.Lock()
        dem_prefetch._process_position(0, 0, str(tmp_path), str(tmp_path / "dem.png"),
                                 zoom15, False, counts, lock)
        assert fetch_calls == ["dem5a_png"]
        assert counts["downloaded_dem"] == 0

    def test_void_mask_matches_decode_semantics(self):
        """_void_mask が、計算が下のレイヤへ降りる画素（復号して 0.0）と一致すること。

        🔴 B-304＝以前は (128,0,0) だけを見ており、ちょうど 0 m の (0,0,0) を
        「欠損なし」と読んでいた（計算はその画素で 5b へ降りる）。
        """
        pixels = [(128, 0, 0),   # 無効値
                  (0, 0, 0),     # ちょうど 0 m
                  (0, 0, 1),     # 0.01 m
                  (128, 0, 1),   # 負の標高（b!=0）
                  (255, 255, 255), (0, 39, 16), (127, 255, 255), (129, 0, 0)]
        arr = np.array([pixels], dtype=np.uint8)
        mask = dem_prefetch._void_mask(arr)[0]
        for px, m in zip(pixels, mask):
            assert bool(m) == (dem._decode_elevation(np.array(px)) == 0.0), px


# ============================================================
# prefetch_tiles（並列ワーカーで _process_position を束ねる公開 API）
# ============================================================

class TestPrefetchTiles:
    """prefetch_tiles の並列オーケストレーション層（ワーカープール・進捗・例外集計）
    を _fetch_tile の sync-fake で検証する。実行時に確実に走る公開 API だが従来
    ノーカバレッジだった（batch.run_batch を 100% にした sync-fake 方式と同型）。"""

    # 1 点 bbox に収束させ zoom-14 位置 1・zoom-15 サブタイル 1 枚とし、件数を決定的に。
    LAT, LON = 35.0, 139.0

    def _tile(self):
        """欠損(128,0,0)を含まない有効タイル。"""
        return _land_tile()

    def _run(self, tmp_path, monkeypatch, fetch, **kw):
        # CACHE_DIR を空の一時ディレクトリにしてスキップ条件（既存キャッシュ）を外す。
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "_fetch_tile", fetch)
        return dem_prefetch.prefetch_tiles(self.LAT, self.LON, self.LAT, self.LON, **kw)

    def _seed_dem14(self, tmp_path):
        """1 点 bbox に対応する dem_png(zoom-14) キャッシュファイルを実パスへ置く。"""
        from PIL import Image
        positions = list(dem_prefetch._iter_dem_positions(self.LAT, self.LON, self.LAT, self.LON))
        _, _, dem14_subdir, dem14_path, _ = positions[0]
        os.makedirs(dem14_subdir, exist_ok=True)
        Image.new("RGB", (256, 256)).save(dem14_path)

    def test_all_resolved_by_5a(self, tmp_path, monkeypatch):
        """5a が欠損なしで取れれば 5b/dem は取得しない。"""
        calls = []

        def fetch(layer_id, *a, **kw):
            calls.append(layer_id)
            return self._tile() if layer_id == "dem5a_png" else None

        res = self._run(tmp_path, monkeypatch, fetch)
        assert res["area_total"] == 1
        assert res["downloaded_5a"] == 1
        assert res["downloaded_5b"] == 0
        assert res["downloaded_dem"] == 0
        assert res["skipped"] == 0
        assert res["failed"] == 0
        assert "dem_png" not in calls   # 5a で完結し dem まで降りない

    def test_falls_through_to_dem(self, tmp_path, monkeypatch):
        """5a・5b 不在 → dem_png まで降りて downloaded_dem に計上。"""
        def fetch(layer_id, *a, **kw):
            return self._tile() if layer_id == "dem_png" else None

        res = self._run(tmp_path, monkeypatch, fetch)
        assert res["downloaded_5a"] == 0
        assert res["downloaded_5b"] == 0
        assert res["downloaded_dem"] == 1
        assert res["failed"] == 0

    def test_skips_cached_dem_without_force(self, tmp_path, monkeypatch):
        """dem_png キャッシュ済み・force=False → 位置全体を skipped（_fetch_tile 未呼び）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._seed_dem14(tmp_path)
        calls = []
        monkeypatch.setattr(dem, "_fetch_tile",
                            lambda layer_id, *a, **kw: calls.append(layer_id))
        res = dem_prefetch.prefetch_tiles(self.LAT, self.LON, self.LAT, self.LON, force=False)
        assert res["skipped"] == 1
        assert res["downloaded_5a"] == res["downloaded_dem"] == 0
        assert calls == []

    def _seed_broken_dem14(self, tmp_path):
        """dem_png(zoom-14) の位置に**壊れた**キャッシュを置く（書き込み途中の形）。"""
        from PIL import Image
        import io as _io
        positions = list(dem_prefetch._iter_dem_positions(
            self.LAT, self.LON, self.LAT, self.LON))
        _, _, dem14_subdir, dem14_path, _ = positions[0]
        os.makedirs(dem14_subdir, exist_ok=True)
        buf = _io.BytesIO()
        Image.new("RGB", (256, 256)).save(buf, format="PNG")
        data = buf.getvalue()
        with open(dem14_path, "wb") as f:      # 末尾が欠けた PNG＝書き込み途中
            f.write(data[:len(data) // 2])
        return dem14_path

    def test_broken_cache_is_not_treated_as_resolved(self, tmp_path, monkeypatch):
        """壊れたタイルを「取得済み」と読まないこと（B-141）。

        🔴 **B-136 と同じ不変条件の、事前取得側の口**＝計算経路は自己修復するように
        直したが、こちらは**存在するだけでスキップ**していた。事前取得の目的は
        *オフラインで使えること*なので、ここで見逃すと**面を取り切ったつもりで
        現地で粗い層か標高 0 に落ちる**。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._seed_broken_dem14(tmp_path)
        calls = []
        monkeypatch.setattr(
            dem, "_fetch_tile",
            lambda layer_id, *a, **kw: (calls.append(layer_id), self._tile())[1])

        res = dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, force=False)

        assert res["skipped"] == 0, "壊れたキャッシュを解決済みとして飛ばしている"
        assert calls, "取り直しに行っていない"

    def test_broken_5m_cache_is_not_treated_as_resolved(self, tmp_path, monkeypatch):
        """**同じ関数の中に口は 2 つある**＝5a/5b 側も可読性で見ること（B-141）。

        ⚠️ 上の dem_png 側だけを検査すると、**5a/5b の早期スキップを存在判定へ
        戻す変異が素通りする**（実測＝変異 M8）。
        """
        from PIL import Image
        import io as _io
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        positions = list(dem_prefetch._iter_dem_positions(
            self.LAT, self.LON, self.LAT, self.LON))
        _, _, _, _, zoom15 = positions[0]
        _, _, subdir5a, path5a, _, _ = zoom15[0]
        os.makedirs(subdir5a, exist_ok=True)
        buf = _io.BytesIO()
        Image.new("RGB", (256, 256)).save(buf, format="PNG")
        data = buf.getvalue()
        with open(path5a, "wb") as f:
            f.write(data[:len(data) // 2])      # 壊れた 5a（dem_png は不在）

        calls = []
        monkeypatch.setattr(
            dem, "_fetch_tile",
            lambda layer_id, *a, **kw: (calls.append(layer_id), self._tile())[1])

        dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, force=False)

        assert "dem5a_png" in calls, "壊れた 5m タイルを解決済みとして飛ばしている"

    def test_broken_dem_is_refetched_even_when_the_5m_tiles_are_readable(
        self, tmp_path, monkeypatch
    ):
        """壊れた 10m タイルは、**5m が読めても**取り直すこと（B-142）。

        🔴 **B-141 のゲートが狭かった**＝「5m キャッシュが無い」条件しか見ておらず、
        *5m に欠損があって 10m まで降りた位置*（ごく普通のキャッシュ状態）で
        **壊れた 10m が取り直されないまま残る**のを検出できなかった。
        早期 return を通り抜けた理由は **不在** と **壊れている** の 2 通りある。
        """
        from PIL import Image
        import io as _io
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        positions = list(dem_prefetch._iter_dem_positions(
            self.LAT, self.LON, self.LAT, self.LON))
        _, _, dem14_subdir, dem14_path, zoom15 = positions[0]

        buf = _io.BytesIO()
        Image.new("RGB", (256, 256)).save(buf, format="PNG")
        good = buf.getvalue()
        os.makedirs(dem14_subdir, exist_ok=True)
        with open(dem14_path, "wb") as f:
            f.write(good[:len(good) // 2])          # 壊れた 10m
        for _x, _y, subdir5a, path5a, _s5b, _p5b in zoom15:
            os.makedirs(subdir5a, exist_ok=True)
            with open(path5a, "wb") as f:
                f.write(good)                        # 読める 5m（＝continue する側）

        calls = []
        monkeypatch.setattr(
            dem, "_fetch_tile",
            lambda layer_id, *a, **kw: (calls.append(layer_id), self._tile())[1])

        dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, force=False)

        assert "dem_png" in calls, (
            "壊れた 10m タイルを取り直していない（5m が読めるので降下を飛ばした）"
        )

    def test_force_also_repairs_a_broken_10m_tile(self, tmp_path, monkeypatch):
        """`force` でも壊れた 10m は修復対象にすること（B-144）。

        🔴 **B-142 の直しが `force` を素通りさせた**＝条件を `not force` で切ったため、
        *強制再取得*なのに壊れたファイルが残った（名前に反する）。
        ⇒ 見るのは「在るのに読めない」だけで、`force` は独立した軸。
        """
        from PIL import Image
        import io as _io
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        positions = list(dem_prefetch._iter_dem_positions(
            self.LAT, self.LON, self.LAT, self.LON))
        _, _, dem14_subdir, dem14_path, _zoom15 = positions[0]
        buf = _io.BytesIO()
        Image.new("RGB", (256, 256)).save(buf, format="PNG")
        good = buf.getvalue()
        os.makedirs(dem14_subdir, exist_ok=True)
        with open(dem14_path, "wb") as f:
            f.write(good[:len(good) // 2])           # 壊れた 10m

        calls = []
        monkeypatch.setattr(
            dem, "_fetch_tile",
            lambda layer_id, *a, **kw: (calls.append(layer_id),
                                        self._tile() if layer_id == "dem5a_png"
                                        else None)[1])

        dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, force=True)

        assert "dem_png" in calls, (
            "force なのに壊れた 10m を取り直していない（5m が解決したので降りなかった）"
        )

    def test_force_does_not_redownload_the_10m_tile_when_5m_resolves_it(
        self, tmp_path, monkeypatch
    ):
        """**対の検査**＝`force` では「在る＝壊れている」が成り立たないこと（B-142）。

        ⚠️ これが無いと、上の検査は「在れば常に 10m を取り直す」という
        *行き過ぎた直し*でも緑のままになる（実測＝変異 M12 が素通りした）。
        `force` は「読める在庫を無視して取り直す」なので、**在ることが壊れている
        ことを含意しない**＝5m で解決した位置まで 10m へ降りるのは無駄な取得。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._seed_dem14(tmp_path)                   # 読める 10m が在る
        calls = []
        monkeypatch.setattr(
            dem, "_fetch_tile",
            lambda layer_id, *a, **kw: (calls.append(layer_id),
                                        self._tile() if layer_id == "dem5a_png"
                                        else None)[1])

        dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, force=True)

        assert "dem5a_png" in calls
        assert "dem_png" not in calls, (
            "5m で解決した位置なのに 10m まで降りている（無駄な取得）"
        )

    def test_force_ignores_cache(self, tmp_path, monkeypatch):
        """force=True なら dem_png キャッシュ済みでもスキップせず再取得する。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._seed_dem14(tmp_path)
        monkeypatch.setattr(dem, "_fetch_tile",
                            lambda layer_id, *a, **kw: self._tile() if layer_id == "dem5a_png" else None)
        res = dem_prefetch.prefetch_tiles(self.LAT, self.LON, self.LAT, self.LON, force=True)
        assert res["skipped"] == 0
        assert res["downloaded_5a"] == 1

    def test_progress_callback_reports_completion(self, tmp_path, monkeypatch):
        """progress_cb が (done, total) で呼ばれ、最終 done == total == 件数。"""
        seen = []
        self._run(
            tmp_path, monkeypatch,
            lambda layer_id, *a, **kw: self._tile() if layer_id == "dem5a_png" else None,
            progress_cb=lambda done, total: seen.append((done, total)),
        )
        assert seen                      # 少なくとも 1 回は呼ばれる
        assert seen[-1] == (1, 1)        # 全件完了で done == total
        assert all(total == 1 for _, total in seen)

    def test_worker_counts_process_exception_as_failed(self, tmp_path, monkeypatch):
        """_process_position が例外を投げてもワーカーが握り、failed に計上して継続する。"""
        def boom(*a, **kw):
            raise RuntimeError("fetch blew up")

        res = self._run(tmp_path, monkeypatch, boom)
        assert res["failed"] == 1
        assert res["area_total"] == 1

    def test_empty_positions_returns_zeros(self, monkeypatch):
        """対象 zoom-14 位置が無い場合はワーカー開始前にゼロ集計を返す。"""
        monkeypatch.setattr(dem_prefetch, "_iter_dem_positions", lambda *a, **kw: iter([]))
        res = dem_prefetch.prefetch_tiles(35.0, 139.0, 35.0, 139.0)
        assert res == {"area_total": 0, "downloaded_5a": 0, "downloaded_5b": 0,
                       "downloaded_dem": 0, "skipped": 0, "failed": 0}


class TestForceRefetchDropsUnreadLowerLayers:
    """強制再取得で降下が要らなくなった下位レイヤを消すこと（B-285）。

    以前は 5a の欠損が無くなった位置で、古い 5b・dem_png がディスクに残った
    ＝「キャッシュ済みタイルを取り直す」の言葉どおりにならなかった。⚠️ **dem_png は
    4 枚の zoom-15 を全部見た位置でだけ消す**（端の位置の検査が対）。
    """

    LAT, LON = 35.0, 139.0

    def _full_position_bbox(self):
        """zoom-14 の 1 枚をちょうど覆う範囲＝子の zoom-15 が 4 枚そろう。"""
        x, y, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        n, w = dem_cache.tile_to_latlng(x, y, 14)
        s, e = dem_cache.tile_to_latlng(x + 1, y + 1, 14)
        eps = 1e-6
        return n - eps, w + eps, s + eps, e - eps

    def _tile(self, void=False):
        arr = _land_tile()
        if void:
            arr[0, 0] = (128, 0, 0)
        return arr

    def _seed(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Image.new("RGB", (256, 256)).save(path)

    def _seed_all(self, bbox):
        """以前 5a に欠損があった位置の形＝5a・5b・dem_png がそろって在る。"""
        (_, _, _, dem14_path, zoom15), = dem_prefetch._iter_dem_positions(*bbox)
        for _x, _y, _s5a, path5a, _s5b, path5b in zoom15:
            self._seed(path5a)
            self._seed(path5b)
        self._seed(dem14_path)
        return dem14_path, zoom15

    def test_lower_layers_are_removed_when_5a_has_no_voids(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        bbox = self._full_position_bbox()
        dem14_path, zoom15 = self._seed_all(bbox)
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        gsi = dem_sources.GSI_DEM.source_id
        stale_key = (gsi, "dem_png", x14, y14)
        monkeypatch.setitem(dem._tile_cache, stale_key, self._tile())
        monkeypatch.setattr(
            dem, "_fetch_tile",
            lambda layer_id, *a, **kw: self._tile() if layer_id == "dem5a_png" else None)

        dem_prefetch.prefetch_tiles(*bbox, force=True)

        assert not any(os.path.exists(t[5]) for t in zoom15), "読まれない 5b が残っている"
        assert not os.path.exists(dem14_path), "読まれない dem_png が残っている"
        assert all(os.path.exists(t[3]) for t in zoom15)
        assert stale_key not in dem._tile_cache

    def test_5b_that_fills_the_5a_voids_is_kept(self, tmp_path, monkeypatch):
        """5a に欠損が残り 5b が埋める位置＝5b は読まれるので消さない（dem_png は消す）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        bbox = self._full_position_bbox()
        dem14_path, zoom15 = self._seed_all(bbox)
        monkeypatch.setattr(
            dem, "_fetch_tile",
            lambda layer_id, *a, **kw: {"dem5a_png": self._tile(void=True),
                                        "dem5b_png": self._tile()}.get(layer_id))

        dem_prefetch.prefetch_tiles(*bbox, force=True)

        assert all(os.path.exists(t[5]) for t in zoom15)
        assert not os.path.exists(dem14_path)

    def test_5b_is_kept_when_5a_has_a_zero_metre_pixel(self, tmp_path, monkeypatch):
        """5a にちょうど 0 m の画素がある位置＝計算は 5b へ降りるので消さない（B-304）。

        🔴 以前は (0,0,0) を欠損と見ず、この位置を「5a で完結」として 5b と dem_png を
        消していた＝オフラインでその画素の標高が変わる。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        bbox = self._full_position_bbox()
        dem14_path, zoom15 = self._seed_all(bbox)
        zero = self._tile()
        zero[5, 5] = (0, 0, 0)
        monkeypatch.setattr(
            dem, "_fetch_tile",
            lambda layer_id, *a, **kw: {"dem5a_png": zero,
                                        "dem5b_png": self._tile()}.get(layer_id))

        dem_prefetch.prefetch_tiles(*bbox, force=True)

        assert all(os.path.exists(t[5]) for t in zoom15), "計算が読む 5b を消した"

    def test_dem_png_is_kept_when_one_subtile_still_needs_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        bbox = self._full_position_bbox()
        dem14_path, zoom15 = self._seed_all(bbox)
        void_x15 = zoom15[0][0]

        def fetch(layer_id, zoom, x, *a, **kw):
            if layer_id == "dem5a_png":
                return self._tile(void=(x == void_x15))
            return None           # 5b 不在・dem_png は通信失敗＝ディスクは古いまま

        monkeypatch.setattr(dem, "_fetch_tile", fetch)

        dem_prefetch.prefetch_tiles(*bbox, force=True)

        assert os.path.exists(dem14_path), "まだ要る dem_png を消した"

    def test_dem_png_at_the_edge_of_the_range_is_kept(self, tmp_path, monkeypatch):
        """範囲の端＝zoom-15 を 1 枚しか見ていない位置では dem_png を消さない。

        🔴 範囲外の 1 枚が 10m を要るかもしれない＝消すと、その 1 枚を覆う事前取得を
        もう一度回すまで**オフラインで 10m が欠ける**。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        full = self._full_position_bbox()
        self._seed_all(full)
        point = (self.LAT, self.LON, self.LAT, self.LON)
        (_, _, _, dem14_path, zoom15), = dem_prefetch._iter_dem_positions(*point)
        assert len(zoom15) == 1
        monkeypatch.setattr(
            dem, "_fetch_tile",
            lambda layer_id, *a, **kw: self._tile() if layer_id == "dem5a_png" else None)

        dem_prefetch.prefetch_tiles(*point, force=True)

        assert not os.path.exists(zoom15[0][5]), "この 1 枚の 5b は読まれない"
        assert os.path.exists(dem14_path), "範囲外の 3 枚が要るかもしれない dem_png を消した"


class TestNormalPrefetchRepairsOldCache:
    """通常の事前取得でも、キャッシュ済みの 5a/5b を読み直して足りない層を取ること（B-306）。

    🔴 以前は `dem_png` が無い位置で読める 5a/5b があれば中身を見ずに飛ばしていた
    ＝B-304 より前（3.7 より前）の事前取得が 0 m の画素を欠損と見ずに 5a だけで
    止めた位置が、強制再取得をしないかぎり直らなかった（オフラインでその画素が欠ける）。
    """

    LAT, LON = 35.0, 139.0
    ZERO_PX = (5, 5)

    def _full_position_bbox(self):
        """zoom-14 の 1 枚をちょうど覆う範囲＝子の zoom-15 が 4 枚そろう。"""
        x, y, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        n, w = dem_cache.tile_to_latlng(x, y, 14)
        s, e = dem_cache.tile_to_latlng(x + 1, y + 1, 14)
        eps = 1e-6
        return n - eps, w + eps, s + eps, e - eps

    def _tile(self, zero=False):
        """欠損の無いタイル（zero＝1 画素だけちょうど 0 m の (0,0,0)）。"""
        arr = _land_tile()
        if zero:
            arr[self.ZERO_PX] = (0, 0, 0)
        return arr

    def _seed(self, path, arr):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Image.fromarray(arr).save(path)

    def _run(self, tmp_path, monkeypatch, seed, fetch_result):
        """`seed(zoom15)` でキャッシュを置き、通常の事前取得を 1 回回す。

        Returns: (結果の件数, 呼ばれた (layer_id, x, y) の列, dem14_path, zoom15)
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        bbox = self._full_position_bbox()
        (_, _, _, dem14_path, zoom15), = dem_prefetch._iter_dem_positions(*bbox)
        assert len(zoom15) == 4
        seed(zoom15)
        calls = []

        def fetch(layer_id, zoom, x, y, *a, **kw):
            calls.append((layer_id, x, y))
            return fetch_result.get(layer_id)

        monkeypatch.setattr(dem, "_fetch_tile", fetch)
        res = dem_prefetch.prefetch_tiles(*bbox, force=False)
        return res, calls, dem14_path, zoom15

    def _seed_old_5a(self, zoom15):
        """3.7 より前の事前取得が残す形＝5a だけ・先頭の 1 枚に 0 m の画素。"""
        for i, (_x, _y, _s5a, path5a, _s5b, _p5b) in enumerate(zoom15):
            self._seed(path5a, self._tile(zero=(i == 0)))

    def test_old_5a_with_a_zero_metre_pixel_fetches_5b(self, tmp_path, monkeypatch):
        """① 旧キャッシュの 5a に 0 m の画素＝その 1 枚だけ 5b を取りに行く。"""
        res, calls, dem14_path, zoom15 = self._run(
            tmp_path, monkeypatch, self._seed_old_5a,
            {"dem5b_png": self._tile()})

        x15, y15 = zoom15[0][0], zoom15[0][1]
        assert calls == [("dem5b_png", x15, y15)], (
            "0 m の画素を持つキャッシュ済み 5a を読み直さずに飛ばした")
        assert res["downloaded_5b"] == 1
        assert res["downloaded_5a"] == res["downloaded_dem"] == 0
        assert all(os.path.exists(t[3]) for t in zoom15), "読み直した 5a を消した"

    def test_old_5a_descends_to_dem_png_when_5b_lacks_the_same_pixel(
        self, tmp_path, monkeypatch
    ):
        """① 5b もその画素で欠けていれば dem_png まで取りに行く。"""
        res, calls, _dem14, zoom15 = self._run(
            tmp_path, monkeypatch, self._seed_old_5a,
            {"dem5b_png": self._tile(zero=True), "dem_png": self._tile()})

        layers = [c[0] for c in calls]
        assert layers.count("dem5b_png") == 1
        assert "dem_png" in layers, "5a∩5b に欠損が残るのに 10m を取りに行かない"
        assert "dem5a_png" not in layers
        assert res["downloaded_dem"] == 1

    def test_healthy_cache_fetches_nothing(self, tmp_path, monkeypatch):
        """② 正常なキャッシュ（5a に欠損が無い）では、どの層も取りに行かない。"""
        def seed(zoom15):
            for t in zoom15:
                self._seed(t[3], self._tile())

        res, calls, _dem14, _z = self._run(
            tmp_path, monkeypatch, seed,
            {"dem5a_png": self._tile(), "dem5b_png": self._tile(), "dem_png": self._tile()})

        assert calls == [], "欠損の無いキャッシュなのに取りに行った"
        assert res["downloaded_5a"] == res["downloaded_5b"] == res["downloaded_dem"] == 0
        assert res["failed"] == 0

    def test_5b_only_position_does_not_refetch_5a(self, tmp_path, monkeypatch):
        """③ 5a が無く 5b だけが在る位置（5a はサーバに無い形）＝5a を取り直さない。"""
        def seed(zoom15):
            for t in zoom15:
                self._seed(t[5], self._tile())

        res, calls, _dem14, zoom15 = self._run(
            tmp_path, monkeypatch, seed,
            {"dem5a_png": self._tile(), "dem_png": self._tile()})

        assert calls == [], "5b で埋まっている位置なのに 5a を取り直した"
        assert not any(os.path.exists(t[3]) for t in zoom15)
        assert all(os.path.exists(t[5]) for t in zoom15)

    def test_5b_only_position_with_a_void_goes_to_dem_png_not_5a(
        self, tmp_path, monkeypatch
    ):
        """③ の対＝5b に欠損が残れば 10m へ降りる（5a は取りに行かない）。"""
        def seed(zoom15):
            for i, t in enumerate(zoom15):
                self._seed(t[5], self._tile(zero=(i == 0)))

        res, calls, _dem14, _z = self._run(
            tmp_path, monkeypatch, seed, {"dem_png": self._tile()})

        assert [c[0] for c in calls] == ["dem_png"]
        assert res["downloaded_dem"] == 1

    def test_reread_without_voids_is_not_counted_as_downloaded(self, tmp_path, monkeypatch):
        """④ 読み直して欠損なしと分かった層は `downloaded_*` に数えない・消さない。"""
        def seed(zoom15):
            for i, t in enumerate(zoom15):
                self._seed(t[3], self._tile(zero=(i == 0)))
                if i == 0:
                    self._seed(t[5], self._tile())      # 5a の 0 m を 5b が埋める

        res, calls, _dem14, zoom15 = self._run(
            tmp_path, monkeypatch, seed,
            {"dem5a_png": self._tile(), "dem5b_png": self._tile(), "dem_png": self._tile()})

        assert calls == []
        assert res["downloaded_5a"] == res["downloaded_5b"] == res["downloaded_dem"] == 0
        assert res["failed"] == 0
        assert os.path.exists(zoom15[0][5]), "読み直した 5b を消した"


# ============================================================
# prefetch_tiles(source=) — 国土地理院以外のソース（3.6 ステージ1・B-253）
#
# 従来は source を渡しても無視され、常に国土地理院（_prefetch_gsi）を黙って
# 取りに行っていた（利用者は選んだソースのキャッシュが増えたと誤解する）。
# ============================================================
class TestPrefetchTilesGenericSource:
    """`source=` に国土地理院以外を渡すと `_prefetch_generic` へ分岐し、
    実際にそのソースのタイルを（`_fetch_tile(..., source=src)` 経由で）取りに行くこと。"""

    LAT, LON = 35.0, 139.0

    EXTERNAL = dem_sources.DemSourceSpec(
        source_id="ext_src",
        display_name="External",
        layers=(("terrarium", 12),),
        url_template="https://example.com/{z}/{x}/{y}.png",
        decode=dem_sources.DecodeMethod.TERRARIUM,
        invalid_rgb=None,
        attribution="Example",
        terms_url="https://example.com/terms",
    )

    def test_downloads_the_selected_source_not_gsi(self, tmp_path, monkeypatch):
        """B-253＝外部ソースを選ぶと国土地理院ではなく選んだソースのレイヤを取る。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        seen_sources = []

        def fetch(layer_id, zoom, x, y, subdir, cache_path, source=None, force=False):
            seen_sources.append(source)
            return np.zeros((256, 256, 3), dtype=np.uint8)

        monkeypatch.setattr(dem, "_fetch_tile", fetch)
        res = dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, source=self.EXTERNAL)

        assert seen_sources, "外部ソースを選んでも _fetch_tile が一度も呼ばれていない"
        assert all(s is self.EXTERNAL for s in seen_sources), \
            "国土地理院決め打ちのまま取得している（B-253 の再発）"
        assert res == {"area_total": 1, "downloaded": 1, "skipped": 0, "failed": 0}

    def test_skips_cached_tile_without_force(self, tmp_path, monkeypatch):
        """既にキャッシュ済み・force=False なら _fetch_tile を呼ばずスキップする。"""
        from PIL import Image
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        tasks = dem_cache._enumerate_bbox(self.LAT, self.LON, self.LAT, self.LON, self.EXTERNAL)
        _layer_id, _zoom, _x, _y, subdir, cache_path = tasks[0]
        os.makedirs(subdir, exist_ok=True)
        Image.new("RGB", (256, 256)).save(cache_path)

        calls = []
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **kw: calls.append(a) or None)
        res = dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, source=self.EXTERNAL, force=False)

        assert calls == []
        assert res == {"area_total": 1, "downloaded": 0, "skipped": 1, "failed": 0}

    def test_force_refetches_cached_tile(self, tmp_path, monkeypatch):
        """force=True なら既存キャッシュがあっても取り直す。"""
        from PIL import Image
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        tasks = dem_cache._enumerate_bbox(self.LAT, self.LON, self.LAT, self.LON, self.EXTERNAL)
        _layer_id, _zoom, _x, _y, subdir, cache_path = tasks[0]
        os.makedirs(subdir, exist_ok=True)
        Image.new("RGB", (256, 256)).save(cache_path)

        monkeypatch.setattr(dem, "_fetch_tile",
                            lambda *a, **kw: np.zeros((256, 256, 3), dtype=np.uint8))
        res = dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, source=self.EXTERNAL, force=True)
        assert res == {"area_total": 1, "downloaded": 1, "skipped": 0, "failed": 0}

    @pytest.mark.parametrize("which", ("gsi", "external"))
    def test_force_really_goes_to_the_server(self, tmp_path, monkeypatch, which):
        """強制再取得は、読めるキャッシュがあっても**通信して上書きする**（B-280）。

        上の検査は `_fetch_tile` を差し替えているので、`prefetch_tiles` →
        `_fetch_tile` の継ぎ目（`force` が下の層まで届くか）を見ていなかった。
        ここは `_fetch_tile` を製品のまま通し、通信だけを偽物にする。
        """
        from PIL import Image
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        source = dem_sources.GSI_DEM if which == "gsi" else self.EXTERNAL
        served = {"value": 10}
        urls: list[str] = []

        class _Res:
            status_code = 200

            def __init__(self):
                buf = io.BytesIO()
                Image.new("RGB", (256, 256), (0, 0, served["value"])).save(buf, format="PNG")
                self.content = buf.getvalue()

        class _Session:
            def get(self, url, timeout=None):
                urls.append(url)
                return _Res()
        monkeypatch.setattr(dem, "_get_session", lambda: _Session())

        dem_prefetch.prefetch_tiles(self.LAT, self.LON, self.LAT, self.LON, source=source)
        first = len(urls)
        assert first > 0
        served["value"] = 20
        res = dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, source=source, force=True)

        assert len(urls) - first == first, "強制再取得なのに通信していない"
        assert sum(v for k, v in res.items() if k.startswith("downloaded")) == first
        written = list(tmp_path.glob("**/*.png"))
        assert written and all(
            np.asarray(Image.open(p))[0, 0, 2] == 20 for p in written), \
            "取り直した内容でキャッシュが上書きされていない"

    @staticmethod
    def _serve(monkeypatch, served: dict, fail: dict | None = None) -> None:
        """`_get_session` を偽物にする＝`served["value"]` を青に持つタイルを返す。
        `fail["on"]` が真の間は接続エラーを投げる。"""
        import requests
        from PIL import Image

        class _Res:
            status_code = 200

            def __init__(self):
                buf = io.BytesIO()
                Image.new("RGB", (256, 256), (0, 0, served["value"])).save(buf, format="PNG")
                self.content = buf.getvalue()

        class _Session:
            def get(self, url, timeout=None):
                if fail is not None and fail["on"]:
                    raise requests.ConnectionError("offline")
                return _Res()
        monkeypatch.setattr(dem, "_get_session", lambda: _Session())

    @pytest.mark.parametrize("which", ("gsi", "external"))
    def test_force_refetch_reaches_the_next_calculation(self, tmp_path, monkeypatch, which):
        """強制再取得のあと、同じ起動のまま計算し直すと**新しい標高**を使う（B-283）。

        B-280 でディスクは上書きされるようになったが、`get_elevation` は
        メモリ上の `_tile_cache` を先に見るので、読み込み済みの古いタイルを返し続けた。
        範囲削除（`dem_cache.delete_tile_cache`）はメモリ側も落としていたのに、
        取得の側だけ抜けていた。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "_tile_cache", {})
        monkeypatch.setattr(dem, "_failed_tiles", set())
        source = dem_sources.GSI_DEM if which == "gsi" else self.EXTERNAL
        served = {"value": 10}
        self._serve(monkeypatch, served)

        before = dem.get_elevation(self.LAT, self.LON, source)
        served["value"] = 20
        dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, source=source, force=True)
        after = dem.get_elevation(self.LAT, self.LON, source)

        assert after != before, "強制再取得したのに、読み込み済みの古い標高を使い続けている"

    def test_successful_fetch_clears_the_404_mark(self, tmp_path, monkeypatch):
        """過去に 404 だったタイルが取れたら、負キャッシュから外す（B-283）。

        外さないと `get_elevation` はそのタイルを「恒久的に無い」と読み飛ばし続け、
        取り直したタイルが計算に使われない。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "_tile_cache", {})
        monkeypatch.setattr(dem, "_failed_tiles", set())
        tasks = dem_cache._enumerate_bbox(self.LAT, self.LON, self.LAT, self.LON, self.EXTERNAL)
        layer_id, _zoom, x, y, _subdir, _path = tasks[0]
        dem._failed_tiles.add((self.EXTERNAL.source_id, layer_id, x, y))
        self._serve(monkeypatch, {"value": 10})

        dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, source=self.EXTERNAL, force=True)

        assert (self.EXTERNAL.source_id, layer_id, x, y) not in dem._failed_tiles
        assert dem.get_elevation(self.LAT, self.LON, self.EXTERNAL) != 0.0

    @pytest.mark.parametrize("which", ("gsi", "external"))
    def test_force_refetch_offline_is_not_counted_as_downloaded(
            self, tmp_path, monkeypatch, which):
        """強制再取得で通信に失敗したタイルは「取得した」と数えない（B-284）。

        `_fetch_tile` は通信例外のとき古いキャッシュを返していた＝`force` では
        その戻り値が「取り直せた」の意味になり、更新できなかったタイルまで
        成功件数に入った。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        source = dem_sources.GSI_DEM if which == "gsi" else self.EXTERNAL
        fail = {"on": False}
        self._serve(monkeypatch, {"value": 10}, fail)
        dem_prefetch.prefetch_tiles(self.LAT, self.LON, self.LAT, self.LON, source=source)
        cached = sorted(tmp_path.glob("**/*.png"))
        assert cached

        fail["on"] = True
        res = dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, source=source, force=True)

        assert sum(v for k, v in res.items() if k.startswith("downloaded")) == 0, \
            "通信できなかったのに「取り直した」と数えている"
        assert res["failed"] > 0
        assert sorted(tmp_path.glob("**/*.png")) == cached, "取れなかったのに古いキャッシュが消えた"

    @pytest.mark.parametrize("which", ("gsi", "external"))
    def test_stale_read_racing_a_force_refetch_does_not_win(
            self, tmp_path, monkeypatch, which):
        """計算が古いディスクを読んだ直後・メモリへ載せる前に強制再取得が終わっても、
        古い配列がメモリへ戻って居座らない（B-286）。

        地図の DL は別スレッドで動くので、この順序は実際に起こり得る。ここでは
        計算側の `_read_cached_tile` の戻り際に強制再取得を割り込ませて、その順序を
        決定的に作る。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "_tile_cache", {})
        monkeypatch.setattr(dem, "_failed_tiles", set())
        source = dem_sources.GSI_DEM if which == "gsi" else self.EXTERNAL
        served = {"value": 10}
        self._serve(monkeypatch, served)
        dem_prefetch.prefetch_tiles(self.LAT, self.LON, self.LAT, self.LON, source=source)
        before = dem.get_elevation(self.LAT, self.LON, source)
        monkeypatch.setattr(dem, "_tile_cache", {})     # 次の起動＝メモリは空・ディスクは古い

        real_read = dem._read_cached_tile
        state = {"armed": True}

        def read_then_race(path):
            arr = real_read(path)
            if state["armed"] and arr is not None:
                state["armed"] = False
                served["value"] = 20
                dem_prefetch.prefetch_tiles(
                    self.LAT, self.LON, self.LAT, self.LON, source=source, force=True)
            return arr
        monkeypatch.setattr(dem, "_read_cached_tile", read_then_race)

        dem.get_elevation(self.LAT, self.LON, source)   # 古い配列を読んだ側
        monkeypatch.setattr(dem, "_read_cached_tile", real_read)
        after = dem.get_elevation(self.LAT, self.LON, source)

        assert not state["armed"]
        assert after != before, "強制再取得より後の計算が、競合で戻った古い標高を使っている"

    @pytest.mark.parametrize("how", ("range", "all"))
    def test_stale_read_racing_a_cache_delete_does_not_win(self, tmp_path, monkeypatch, how):
        """B-286 のクラス点検＝範囲削除・全削除も同じ無効化の口。削除と並んで
        古いディスクを読んだ計算が、消したタイルをメモリへ戻さない。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "_tile_cache", {})
        monkeypatch.setattr(dem, "_failed_tiles", set())
        self._serve(monkeypatch, {"value": 10})
        dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, source=self.EXTERNAL)

        real_read = dem._read_cached_tile
        state = {"armed": True}

        def read_then_delete(path):
            arr = real_read(path)
            if state["armed"] and arr is not None:
                state["armed"] = False
                if how == "range":
                    dem_cache.delete_tile_cache(
                        self.LAT, self.LON, self.LAT, self.LON, source=self.EXTERNAL)
                else:
                    dem_cache.delete_all_tile_cache()
            return arr
        monkeypatch.setattr(dem, "_read_cached_tile", read_then_delete)

        dem.get_elevation(self.LAT, self.LON, self.EXTERNAL)

        assert not state["armed"]
        assert dem._tile_cache == {}, "削除したタイルが、競合でメモリへ戻っている"

    @pytest.mark.parametrize("which", ("gsi", "external"))
    def test_force_refetch_whose_write_failed_is_not_counted(
            self, tmp_path, monkeypatch, which):
        """強制再取得でディスクの置き換えに失敗したタイルは「取得した」と数えず、
        メモリにも古い写しの無効化だけが起きたことにしない（B-287）。

        Windows では読まれている最中のファイルへの `os.replace` が拒まれる
        （`_write_tile_atomic` の註）。書けなかったのに成功と数えると、ディスクには
        古いタイルが残り、次の起動の計算は古い標高を読み直す。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "_tile_cache", {})
        monkeypatch.setattr(dem, "_failed_tiles", set())
        source = dem_sources.GSI_DEM if which == "gsi" else self.EXTERNAL
        self._serve(monkeypatch, {"value": 10})
        dem_prefetch.prefetch_tiles(self.LAT, self.LON, self.LAT, self.LON, source=source)

        def deny(src, dst):
            raise PermissionError(5, "Access is denied")
        monkeypatch.setattr(dem.os, "replace", deny)
        res = dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, source=source, force=True)

        assert sum(v for k, v in res.items() if k.startswith("downloaded")) == 0, \
            "ディスクを置き換えられなかったのに「取り直した」と数えている"
        assert res["failed"] > 0

    def test_failed_fetch_is_counted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **kw: None)
        res = dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, source=self.EXTERNAL)
        assert res == {"area_total": 1, "downloaded": 0, "skipped": 0, "failed": 1}

    def test_gsi_source_still_uses_the_layered_descent_path(self, tmp_path, monkeypatch):
        """`source=GSI_DEM` を明示しても、`source` 省略時と同じ国土地理院の
        降下ロジック（内訳つきの戻り値）のままであること。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem, "_fetch_tile",
                            lambda layer_id, *a, **kw:
                                np.zeros((256, 256, 3), dtype=np.uint8) if layer_id == "dem5a_png" else None)
        res = dem_prefetch.prefetch_tiles(
            self.LAT, self.LON, self.LAT, self.LON, source=dem_sources.GSI_DEM)
        assert "downloaded_5a" in res
        assert res["downloaded_5a"] == 1


class TestCountBboxTilesSource:
    """`count_bbox_tiles(source=)`＝確認ダイアログの件数表示が単位を揃えること。"""

    EXTERNAL_COARSE = dem_sources.DemSourceSpec(
        source_id="ext_coarse",
        display_name="External Coarse",
        layers=(("terrarium", 8),),   # zoom-8 = 国土地理院の zoom-14 よりずっと粗い
        url_template="https://example.com/{z}/{x}/{y}.png",
        decode=dem_sources.DecodeMethod.TERRARIUM,
        invalid_rgb=None,
        attribution="Example",
        terms_url="https://example.com/terms",
    )

    def test_default_matches_gsi_zoom14(self):
        """`source` 省略時は従来どおり zoom-14 の位置数（後方互換・1 桁も動かない）。"""
        lat1, lon1, lat2, lon2 = 35.68, 139.69, 35.60, 139.80
        assert dem_prefetch.count_bbox_tiles(lat1, lon1, lat2, lon2) == \
               dem_prefetch.count_bbox_tiles(lat1, lon1, lat2, lon2, source=dem_sources.GSI_DEM)

    def test_coarser_source_uses_its_own_declared_zoom(self):
        """粗いズームを宣言した外部ソースは、その分位置数が少なくなる
        （zoom-14 決め打ちのままだと B-253 と同じ「対象に従わない」不整合になる）。"""
        lat1, lon1, lat2, lon2 = 35.68, 139.69, 35.60, 139.80
        n_gsi = dem_prefetch.count_bbox_tiles(lat1, lon1, lat2, lon2)
        n_coarse = dem_prefetch.count_bbox_tiles(
            lat1, lon1, lat2, lon2, source=self.EXTERNAL_COARSE)
        assert n_coarse < n_gsi

    def test_matches_count_cached_areas_unit(self, tmp_path, monkeypatch):
        """DL 確認ダイアログは `count_bbox_tiles - count_cached_areas` を新規分として
        引き算する（map_cache.py:_sel_release）＝両辺の単位が同じでないと数が合わない。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        lat1, lon1, lat2, lon2 = 35.68, 139.69, 35.60, 139.80
        total = dem_prefetch.count_bbox_tiles(lat1, lon1, lat2, lon2, source=self.EXTERNAL_COARSE)
        cached = dem_cache.count_cached_areas(lat1, lon1, lat2, lon2, source=self.EXTERNAL_COARSE)
        assert cached == 0   # 空キャッシュ
        assert total - cached == total


# ============================================================
# scan_cache_overlay（実キャッシュ走査・自動カバレッジ表示用）
# ============================================================

class TestScanCacheOverlay:

    # 走査対象の代表座標（広島県付近）
    LAT, LON = 34.54, 132.41

    def _touch(self, root, layer_id, x, y):
        from PIL import Image
        d = os.path.join(root, layer_id, str(x))
        os.makedirs(d, exist_ok=True)
        Image.new("RGB", (2, 2)).save(os.path.join(d, f"{y}.png"))

    def _touch_broken(self, root, layer_id, x, y):
        """壊れた（読めない）タイルを置く（B-143）。"""
        d = os.path.join(root, layer_id, str(x))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{y}.png"), "wb") as f:
            f.write(b"\x89PNG")

    def test_empty_cache_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        assert dem_cache.scan_cache_overlay(
            self.LAT, self.LON, self.LAT - 0.01, self.LON + 0.01, 14
        ) == []

    def test_5a_wins_over_dem_at_same_cell(self, tmp_path, monkeypatch):
        """同じ zoom-14 セルに 5a と dem があれば最高精度 5a を返す。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        x15, y15, _, _ = dem._tile_coords(self.LAT, self.LON, 15)
        self._touch(tmp_path, "dem_png", x14, y14)
        self._touch(tmp_path, "dem5a_png", x15, y15)
        cells = dem_cache.scan_cache_overlay(
            self.LAT + 0.01, self.LON - 0.01,
            self.LAT - 0.01, self.LON + 0.01, 14,
        )
        match = [c for c in cells if c["x"] == x14 and c["y"] == y14]
        assert len(match) == 1
        assert match[0]["level"] == "5a"
        assert match[0]["zoom"] == 14

    def test_dem_only_area(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        cells = dem_cache.scan_cache_overlay(
            self.LAT + 0.01, self.LON - 0.01,
            self.LAT - 0.01, self.LON + 0.01, 14,
        )
        assert all(c["level"] == "dem" for c in cells)
        assert any(c["x"] == x14 and c["y"] == y14 for c in cells)

    def test_tiles_outside_view_excluded(self, tmp_path, monkeypatch):
        """表示範囲外のキャッシュは返さない。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        # はるか遠方の小範囲を指定（対象タイルを含まない）
        cells = dem_cache.scan_cache_overlay(43.07, 141.34, 43.06, 141.35, 14)
        assert cells == []

    # 日本全域を覆う bbox（filtering の端数で対象タイルを落とさないため広めに取る）
    WIDE = (46.0, 128.0, 30.0, 146.0)

    def _aligned_block_origin(self, span):
        """span×span に整列した zoom-14 ブロックの原点 (x0, y0) を返す。"""
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        return (x14 // span) * span, (y14 // span) * span

    def test_full_aligned_block_merges_to_single_coarse_cell(self, tmp_path, monkeypatch):
        """完全に埋まった整列 4×4 ブロックは zoom-12 の単一セルへ統合される。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x0, y0 = self._aligned_block_origin(4)   # 4 = 2^(14-12)
        for dx in range(4):
            for dy in range(4):
                self._touch(tmp_path, "dem_png", x0 + dx, y0 + dy)
        cells = dem_cache.scan_cache_overlay(*self.WIDE, 12)
        assert len(cells) == 1
        assert cells[0]["zoom"] == 12
        assert cells[0]["level"] == "dem"

    def test_partial_block_keeps_edges_fine(self, tmp_path, monkeypatch):
        """欠けのあるブロックは粗く統合されず、エッジは zoom-14 のまま残る。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x0, y0 = self._aligned_block_origin(4)
        for dx in range(4):
            for dy in range(4):
                if dx == 0 and dy == 0:
                    continue   # 1 隅を欠けさせる → 全体統合は不可
                self._touch(tmp_path, "dem_png", x0 + dx, y0 + dy)
        cells = dem_cache.scan_cache_overlay(*self.WIDE, 12)
        # 単一の粗いセルにはならない（過大表示を防ぐ）
        assert len(cells) > 1
        # 細粒度（zoom-14）のセルが残る
        assert any(c["zoom"] == 14 for c in cells)
        # 欠けた隅 (x0, y0) は covered として返らない
        assert not any(c["zoom"] == 14 and c["x"] == x0 and c["y"] == y0 for c in cells)

    def test_count_cached_areas_counts_only_cached(self, tmp_path, monkeypatch):
        """count_cached_areas は実在キャッシュのみ数える（未取得は含めない）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        # 2 エリアだけキャッシュ
        self._touch(tmp_path, "dem_png", x14, y14)
        self._touch(tmp_path, "dem_png", x14 + 1, y14)
        wide = (self.LAT + 0.1, self.LON - 0.1, self.LAT - 0.1, self.LON + 0.1)
        cached = dem_cache.count_cached_areas(*wide)
        total = dem_prefetch.count_bbox_tiles(*wide)
        assert cached == 2
        assert total > cached   # 範囲総数は未取得を含むので多い

    def test_count_cached_areas_zero_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        assert dem_cache.count_cached_areas(*self.WIDE) == 0

    def test_broken_tile_excluded(self, tmp_path, monkeypatch):
        """壊れたタイルは件数表示・塗りのどちらにも「取得済み」として現れない（B-143）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)          # 読める
        self._touch_broken(tmp_path, "dem_png", x14 + 1, y14)  # 壊れている
        wide = (self.LAT + 0.1, self.LON - 0.1, self.LAT - 0.1, self.LON + 0.1)
        assert dem_cache.count_cached_areas(*wide) == 1
        cells = dem_cache.scan_cache_overlay(*wide, 14)
        assert not any(c["x"] == x14 + 1 and c["y"] == y14 for c in cells)

    def test_repaired_tile_becomes_visible_after_rewrite(self, tmp_path, monkeypatch):
        """壊れたタイルが上書きで直ったら、次の走査で取得済みとして現れる（メモがstat変化で追従）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch_broken(tmp_path, "dem_png", x14, y14)
        wide = (self.LAT + 0.1, self.LON - 0.1, self.LAT - 0.1, self.LON + 0.1)
        assert dem_cache.count_cached_areas(*wide) == 0   # メモに「壊れている」を記録
        self._touch(tmp_path, "dem_png", x14, y14)   # stat（mtime/size）が変わる
        assert dem_cache.count_cached_areas(*wide) == 1    # メモが古いと踏んだままにならない


class TestCoverageOutline:

    LAT, LON = 34.54, 132.41
    WIDE = (46.0, 128.0, 30.0, 146.0)

    def _touch(self, root, layer_id, x, y):
        from PIL import Image
        d = os.path.join(root, layer_id, str(x))
        os.makedirs(d, exist_ok=True)
        Image.new("RGB", (2, 2)).save(os.path.join(d, f"{y}.png"))

    def test_empty_cache_no_loops(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        assert dem_cache.coverage_outline(*self.WIDE) == []

    def test_single_cell_is_rectangle(self, tmp_path, monkeypatch):
        """単一セル → 4 頂点の矩形ループ1個。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        loops = dem_cache.coverage_outline(*self.WIDE)
        assert len(loops) == 1
        assert len(loops[0]) == 4

    def test_adjacent_cells_merge_to_one_outline(self, tmp_path, monkeypatch):
        """隣接2セルは内部線なしの単一矩形（4頂点）になる。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        self._touch(tmp_path, "dem_png", x14 + 1, y14)
        loops = dem_cache.coverage_outline(*self.WIDE)
        assert len(loops) == 1
        assert len(loops[0]) == 4   # 内部の共有辺は相殺され角は4つ

    def test_l_shape_has_six_corners(self, tmp_path, monkeypatch):
        """L字（2×2 から1セル欠け）は6頂点のループ。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        for dx in (0, 1):
            for dy in (0, 1):
                if dx == 1 and dy == 1:
                    continue
                self._touch(tmp_path, "dem_png", x14 + dx, y14 + dy)
        loops = dem_cache.coverage_outline(*self.WIDE)
        assert len(loops) == 1
        assert len(loops[0]) == 6


class TestBasemapTiles:
    """淡色地図（レポート地図）タイルの取得・キャッシュ・削除。"""

    LAT, LON = 34.54, 132.41
    WIDE = (34.6, 132.3, 34.4, 132.5)

    def test_tile_path_includes_zoom(self):
        """キャッシュパスにズームが入る（異なるズームの同一(x,y)が衝突しない）。"""
        subdir, path = dem._basemap_tile_path(dem._resolve_basemap_source("pale"), 14, 100, 200)
        assert os.path.join(dem.BASEMAP_SUBDIR, "14", "100") in subdir
        assert path.endswith(os.path.join("100", "200.png"))
        # ズーム違いはパスが異なる。
        _, path15 = dem._basemap_tile_path(dem._resolve_basemap_source("pale"), 15, 100, 200)
        assert path != path15

    def test_tile_path_isolates_non_pale_sources(self):
        """`"pale"` 以外は `basemap/<source_id>/` へ分離される（B-248）。"""
        _, pale_path = dem._basemap_tile_path(dem._resolve_basemap_source("pale"), 14, 100, 200)
        _, photo_path = dem._basemap_tile_path(dem._resolve_basemap_source("photo"), 14, 100, 200)
        assert pale_path != photo_path
        assert os.path.join(dem.BASEMAP_EXTRA_SUBDIR, "photo") in photo_path

    def test_rewritten_url_does_not_reuse_old_provider_tiles(
            self, tmp_path, monkeypatch):
        """同じ `source_id` で URL を書き換えたら、旧プロバイダのタイルを読まない（B-281）。

        製品の取得経路（`fetch_basemap_tiles`→`_fetch_tile`）を差し替えずに通し、
        通信だけを偽物にする＝置き場の決め方そのものを見る。
        """
        from core import tile_sources
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))

        def declare(url: str) -> None:
            monkeypatch.setattr(tile_sources, "_user_sources", [
                tile_sources.TileSourceSpec(
                    source_id="osm", display_name="OSM", url=url, max_zoom=18,
                    attribution="(c)", terms_url="https://example.invalid")])

        def png(value: int) -> bytes:
            buf = io.BytesIO()
            Image.new("RGB", (256, 256), (value,) * 3).save(buf, format="PNG")
            return buf.getvalue()

        urls: list[str] = []

        class _Res:
            status_code = 200

            def __init__(self, url: str):
                self.content = png(10 if "old.invalid" in url else 90)

        class _Session:
            def get(self, url, timeout=None):
                urls.append(url)
                return _Res(url)
        monkeypatch.setattr(dem, "_get_session", lambda: _Session())

        declare("https://old.invalid/{z}/{x}/{y}.png")
        old = dem.fetch_basemap_tiles([(1, 2)], 14, "osm")
        declare("https://new.invalid/{z}/{x}/{y}.png")
        new = dem.fetch_basemap_tiles([(1, 2)], 14, "osm")

        assert int(old[(1, 2)][0, 0, 0]) == 10
        assert int(new[(1, 2)][0, 0, 0]) == 90, "旧プロバイダのキャッシュを読んだ"
        assert [u.split("/")[2] for u in urls] == ["old.invalid", "new.invalid"]

    def test_fetch_basemap_tiles_parallel_returns_dict(self, monkeypatch):
        """並列取得が成功タイルだけを {(x,y):配列} で返す。"""
        def fake(layer_id, zoom, x, y, subdir, path, source=None):
            return np.full((256, 256, 3), 100, dtype=np.uint8)
        monkeypatch.setattr(dem, "_fetch_tile", fake)
        tiles = [(1, 2), (3, 4), (5, 6)]
        out = dem.fetch_basemap_tiles(tiles, 14)
        assert set(out.keys()) == set(tiles)

    def test_fetch_basemap_tiles_empty_input(self):
        assert dem.fetch_basemap_tiles([], 14) == {}

    def test_fetch_basemap_tiles_skips_failures(self, monkeypatch):
        """取得失敗（None）のタイルは結果に含めない。"""
        monkeypatch.setattr(dem, "_fetch_tile", lambda *a, **k: None)
        assert dem.fetch_basemap_tiles([(1, 2)], 14) == {}

    def test_delete_tile_cache_keeps_basemap(self, tmp_path, monkeypatch):
        """エリア範囲削除は basemap タイルを消さない（DEM カバレッジ専用の操作）。

        basemap はマップウィンドウで可視化されないため、範囲指定で黙って消すのを
        避ける。basemap は「全キャッシュ削除」でのみ消える（下記テスト参照）。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        z = 14
        x, y, _, _ = dem._tile_coords(self.LAT, self.LON, z)
        subdir, path = dem._basemap_tile_path(dem._resolve_basemap_source("pale"), z, x, y)
        os.makedirs(subdir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"\x89PNG")
        assert os.path.exists(path)
        dem_cache.delete_tile_cache(*self.WIDE)
        assert os.path.exists(path)

    def test_delete_all_tile_cache_removes_basemap(self, tmp_path, monkeypatch):
        """全キャッシュ削除は basemap タイルも消す。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        z = 14
        x, y, _, _ = dem._tile_coords(self.LAT, self.LON, z)
        subdir, path = dem._basemap_tile_path(dem._resolve_basemap_source("pale"), z, x, y)
        os.makedirs(subdir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"\x89PNG")
        assert os.path.exists(path)
        dem_cache.delete_all_tile_cache()
        assert not os.path.exists(path)


# ============================================================
# キャッシュ削除・統計（delete_tile_cache / get_cache_stats / delete_all_tile_cache）
# ============================================================
class TestCacheDeletion:
    """ユーザーデータ（DEM キャッシュ）を消す操作の不変条件。

    basemap の扱いは TestBasemapTiles 側で担保済み。ここでは DEM タイルに
    ついて「bbox 内だけ消える・メモリキャッシュも連動して消える・件数が
    実削除数を報告する」を守る（削除系は誤ると再取得コストがユーザーに跳ねる）。
    """

    BBOX = (34.540, 132.410, 34.539, 132.409)

    def _seed_bbox_tiles(self) -> list[tuple]:
        """bbox 内の全 DEM タイルを実ファイルとして作成し、タイルリストを返す。"""
        tiles = dem_cache._enumerate_bbox(*self.BBOX)
        for _, _, _, _, subdir, cache_path in tiles:
            os.makedirs(subdir, exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(b"\x89PNG")
        return tiles

    def _fresh_memory_cache(self, monkeypatch):
        monkeypatch.setattr(dem, "_tile_cache", {})
        monkeypatch.setattr(dem, "_failed_tiles", set())

    def test_deletes_only_bbox_files_and_memory_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._fresh_memory_cache(monkeypatch)
        tiles = self._seed_bbox_tiles()

        # bbox 外のタイルは残ること（範囲削除が全消しに化けない）。
        outside_dir = os.path.join(str(tmp_path), "dem_png", "0")
        os.makedirs(outside_dir, exist_ok=True)
        outside_file = os.path.join(outside_dir, "0.png")
        with open(outside_file, "wb") as f:
            f.write(b"\x89PNG")

        # メモリキャッシュ: bbox 内キーは消え、bbox 外キーは残ること。
        # 3.4 ステージ1（I-147）＝キーは (source_id, layer_id, x, y) の4要素。
        layer_id, _, x, y, _, _ = tiles[0]
        dem._tile_cache[("gsi_dem", layer_id, x, y)] = np.zeros(1)
        dem._tile_cache[("gsi_dem", "dem_png", 0, 0)] = np.zeros(1)
        dem._failed_tiles.add(("gsi_dem", layer_id, x, y))

        res = dem_cache.delete_tile_cache(*self.BBOX)

        assert res == {"deleted": len(tiles), "errors": 0}
        assert all(not os.path.exists(p) for *_, p in tiles)
        assert os.path.exists(outside_file)
        assert ("gsi_dem", layer_id, x, y) not in dem._tile_cache
        assert ("gsi_dem", "dem_png", 0, 0) in dem._tile_cache
        assert ("gsi_dem", layer_id, x, y) not in dem._failed_tiles

    def test_missing_files_count_zero(self, tmp_path, monkeypatch):
        """未取得エリアの範囲削除は deleted=0（存在しないものを数えない）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._fresh_memory_cache(monkeypatch)
        assert dem_cache.delete_tile_cache(*self.BBOX) == {"deleted": 0, "errors": 0}

    def test_get_cache_stats_missing_dir_is_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path / "no_such_dir"))
        assert dem_cache.get_cache_stats() == {"count": 0, "size_bytes": 0}

    def test_get_cache_stats_counts_png_only(self, tmp_path, monkeypatch):
        """枚数・総バイト数は .png のみ集計（ログ等の同居ファイルを数えない）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        d = tmp_path / "dem_png" / "123"
        d.mkdir(parents=True)
        (d / "1.png").write_bytes(b"abc")
        (d / "2.png").write_bytes(b"abcde")
        (d / "note.txt").write_bytes(b"zz")
        assert dem_cache.get_cache_stats() == {"count": 2, "size_bytes": 8}

    def test_delete_all_removes_png_and_clears_memory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._fresh_memory_cache(monkeypatch)
        dem._tile_cache[("dem_png", 1, 2)] = np.zeros(1)
        dem._failed_tiles.add(("dem_png", 1, 2))
        d = tmp_path / "dem_png" / "1"
        d.mkdir(parents=True)
        (d / "2.png").write_bytes(b"\x89PNG")
        (tmp_path / "keep.txt").write_bytes(b"keep")

        res = dem_cache.delete_all_tile_cache()

        assert res == {"deleted": 1}
        assert not (d / "2.png").exists()
        assert (tmp_path / "keep.txt").exists()   # .png 以外は消さない
        assert dem._tile_cache == {}
        assert dem._failed_tiles == set()


# ============================================================
# I-155（3.5 ステージ3）＝キャッシュ管理のソース対応（get_cache_stats(source=)・
# get_basemap_cache_stats・delete_all_tile_cache(sources=, include_basemap=)）
# ============================================================
class TestCacheStatsAndDeletionBySource:
    """複数 DEM ソースがあるときの、ソース単位の集計・削除の不変条件。

    既定（引数省略）は従来どおり全体を対象にする＝**後方互換**を
    `TestCacheDeletion` 側の無指定呼び出しが既に守っている。ここでは
    引数を渡したときにソースの外を触らないことだけを見る。
    """

    EXTERNAL = dem_sources.DemSourceSpec(
        source_id="ext_src",
        display_name="External",
        layers=(("terrarium", 12),),
        url_template="https://example.com/{z}/{x}/{y}.png",
        decode=dem_sources.DecodeMethod.TERRARIUM,
        invalid_rgb=None,
        attribution="Example",
        terms_url="https://example.com/terms",
    )

    def _seed(self, root, layer_dir: str, n: int, nbytes: int = 4) -> None:
        d = os.path.join(root, *layer_dir.split("/"))
        os.makedirs(d, exist_ok=True)
        for i in range(n):
            with open(os.path.join(d, f"{i}.png"), "wb") as f:
                f.write(b"x" * nbytes)

    def test_get_cache_stats_scopes_to_source(self, tmp_path, monkeypatch):
        """`source=` を渡すと、そのソースの層だけを数える（他ソースは含めない）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        # 国土地理院（`CACHE_DIR/<layer_id>/...`）に 2 枚。
        self._seed(str(tmp_path), "dem5a_png/1", 2)
        # 外部ソース（`CACHE_DIR/external/<source_id>/<fingerprint>/<layer_id>/...`）に 3 枚。
        fp = dem_sources.definition_fingerprint(self.EXTERNAL)
        self._seed(str(tmp_path), f"external/ext_src/{fp}/terrarium/1", 3)

        assert dem_cache.get_cache_stats(dem_sources.GSI_DEM) == \
            {"count": 2, "size_bytes": 8}
        assert dem_cache.get_cache_stats(self.EXTERNAL) == \
            {"count": 3, "size_bytes": 12}
        # 省略時は従来どおり全体（両ソース合算）。
        assert dem_cache.get_cache_stats() == {"count": 5, "size_bytes": 20}

    def test_get_basemap_cache_stats_is_independent_of_dem(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._seed(str(tmp_path), "dem5a_png/1", 2)
        self._seed(str(tmp_path), f"{dem.BASEMAP_SUBDIR}/14/1", 1)

        assert dem_cache.get_basemap_cache_stats() == {"count": 1, "size_bytes": 4}
        assert dem_cache.get_cache_stats(dem_sources.GSI_DEM) == \
            {"count": 2, "size_bytes": 8}

    def test_delete_all_tile_cache_with_no_args_wipes_everything(self, tmp_path, monkeypatch):
        """引数省略＝後方互換（従来どおり CACHE_DIR 全体を無差別に消す）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._seed(str(tmp_path), "dem5a_png/1", 2)
        self._seed(str(tmp_path), f"{dem.BASEMAP_SUBDIR}/14/1", 1)

        res = dem_cache.delete_all_tile_cache()

        assert res == {"deleted": 3}
        assert dem_cache.get_cache_stats() == {"count": 0, "size_bytes": 0}

    def test_delete_all_tile_cache_by_source_leaves_others_untouched(self, tmp_path, monkeypatch):
        """`sources=[GSI]` かつ `include_basemap=False` は他ソース・basemap を残す。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._fresh_memory_cache = lambda mp: (
            mp.setattr(dem, "_tile_cache", {}), mp.setattr(dem, "_failed_tiles", set()))
        self._fresh_memory_cache(monkeypatch)
        self._seed(str(tmp_path), "dem5a_png/1", 2)
        fp = dem_sources.definition_fingerprint(self.EXTERNAL)
        self._seed(str(tmp_path), f"external/ext_src/{fp}/terrarium/1", 3)
        self._seed(str(tmp_path), f"{dem.BASEMAP_SUBDIR}/14/1", 1)

        res = dem_cache.delete_all_tile_cache(
            sources=[dem_sources.GSI_DEM], include_basemap=False)

        assert res == {"deleted": 2}
        assert dem_cache.get_cache_stats(dem_sources.GSI_DEM) == \
            {"count": 0, "size_bytes": 0}
        assert dem_cache.get_cache_stats(self.EXTERNAL) == {"count": 3, "size_bytes": 12}
        assert dem_cache.get_basemap_cache_stats() == {"count": 1, "size_bytes": 4}

    def test_delete_all_tile_cache_basemap_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._seed(str(tmp_path), "dem5a_png/1", 2)
        self._seed(str(tmp_path), f"{dem.BASEMAP_SUBDIR}/14/1", 1)

        res = dem_cache.delete_all_tile_cache(sources=[], include_basemap=True)

        assert res == {"deleted": 1}
        assert dem_cache.get_basemap_cache_stats() == {"count": 0, "size_bytes": 0}
        assert dem_cache.get_cache_stats(dem_sources.GSI_DEM) == \
            {"count": 2, "size_bytes": 8}

    def test_delete_by_source_also_wipes_tiles_of_an_older_definition(
            self, tmp_path, monkeypatch):
        """B-268＝宣言を書き換える前のタイル（古いハッシュ）も消す。

        外部ソースの置き場は `<source_id>/<定義のハッシュ>/<layer>/` で、
        ハッシュは宣言を書き換えるたびに変わる。**読む側は今のハッシュだけが
        正しい**（B-236 の自動無効化）が、**消す側が今のハッシュしか見ないと、
        画面から選べないタイルが永久に残る**（容量が減らない）。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        fp = dem_sources.definition_fingerprint(self.EXTERNAL)
        self._seed(str(tmp_path), f"external/ext_src/{fp}/terrarium/1", 3)
        # 宣言を書き換える前のハッシュのタイル（今のコードは二度と読まない）。
        self._seed(str(tmp_path), "external/ext_src/0123456789ab/terrarium/1", 2)

        res = dem_cache.delete_all_tile_cache(
            sources=[self.EXTERNAL], include_basemap=False)

        assert res == {"deleted": 5}
        assert dem_cache.get_cache_stats() == {"count": 0, "size_bytes": 0}

    def test_delete_by_source_does_not_touch_other_sources_sharing_the_root(
            self, tmp_path, monkeypatch):
        """B-268 の直しが「消しすぎ」ていないこと（別ソースの根は巻き込まない）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        fp = dem_sources.definition_fingerprint(self.EXTERNAL)
        self._seed(str(tmp_path), f"external/ext_src/{fp}/terrarium/1", 3)
        self._seed(str(tmp_path), "external/ext_src_other/abcdef012345/terrarium/1", 4)
        self._seed(str(tmp_path), "dem5a_png/1", 2)

        dem_cache.delete_all_tile_cache(
            sources=[self.EXTERNAL], include_basemap=False)

        assert dem_cache.get_cache_stats() == {"count": 6, "size_bytes": 24}

    def test_delete_by_source_named_like_a_builtin_does_not_touch_gsi(
            self, tmp_path, monkeypatch):
        """B-274＝宣言の `source_id` に組み込みの内部名（`dem5a_png` 等）を
        付けても、専用の名前空間（`external/`）の下にある限り組み込みの置き場
        （`CACHE_DIR/dem5a_png/`）とは重ならず、削除が巻き込まないこと。"""
        import dataclasses
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        colliding = dataclasses.replace(self.EXTERNAL, source_id="dem5a_png")
        fp = dem_sources.definition_fingerprint(colliding)
        self._seed(str(tmp_path), f"external/dem5a_png/{fp}/terrarium/1", 3)
        # 組み込みの国土地理院キャッシュ（本物の置き場）。
        self._seed(str(tmp_path), "dem5a_png/1", 2)

        res = dem_cache.delete_all_tile_cache(
            sources=[colliding], include_basemap=False)

        assert res == {"deleted": 3}
        assert dem_cache.get_cache_stats(dem_sources.GSI_DEM) == \
            {"count": 2, "size_bytes": 8}

    def test_delete_by_source_also_wipes_the_pre_3_6_location(
            self, tmp_path, monkeypatch):
        """B-278＝3.5 以前の置き場（`CACHE_DIR/<source_id>/`）も消す。

        B-274 で置き場を `external/` の下へ移したので旧置き場は二度と読まれず、
        ソース単位の削除からも外れると画面から容量を取り戻せない。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        fp = dem_sources.definition_fingerprint(self.EXTERNAL)
        self._seed(str(tmp_path), f"external/ext_src/{fp}/terrarium/1", 3)
        self._seed(str(tmp_path), f"ext_src/{fp}/terrarium/1", 2)   # 3.5 以前の置き場
        self._seed(str(tmp_path), "dem5a_png/1", 4)

        res = dem_cache.delete_all_tile_cache(
            sources=[self.EXTERNAL], include_basemap=False)

        assert res == {"deleted": 5}
        assert not os.path.exists(os.path.join(str(tmp_path), "ext_src"))
        assert dem_cache.get_cache_stats(dem_sources.GSI_DEM) == \
            {"count": 4, "size_bytes": 16}

    def test_pre_3_6_location_named_like_a_builtin_is_not_swept(
            self, tmp_path, monkeypatch):
        """B-278 の直しが B-274 を再発させないこと＝旧置き場の名前が組み込みの
        置き場（`dem5a_png`・`basemap` 等）と同じなら、旧置き場は消さない。"""
        import dataclasses
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._seed(str(tmp_path), "dem5a_png/1", 2)
        self._seed(str(tmp_path), f"{dem.BASEMAP_EXTRA_SUBDIR}/photo/14/1", 1)
        for name in ("dem5a_png", dem.BASEMAP_EXTRA_SUBDIR, dem.DEM_EXTERNAL_SUBDIR):
            colliding = dataclasses.replace(self.EXTERNAL, source_id=name)
            dem_cache.delete_all_tile_cache(sources=[colliding], include_basemap=False)

        assert dem_cache.get_cache_stats(dem_sources.GSI_DEM) == \
            {"count": 2, "size_bytes": 8}
        assert dem_cache.get_basemap_cache_stats() == {"count": 1, "size_bytes": 4}

    def test_delete_by_source_counts_what_actually_disappeared(
            self, tmp_path, monkeypatch):
        """B-269＝消えなかったぶんを「削除した」と数えない。

        `shutil.rmtree(ignore_errors=True)` はロックや権限で残っても黙るので、
        **消す前の在庫を足すと、1 枚も消えなくても「N 件削除」と出る**。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._seed(str(tmp_path), "dem5a_png/1", 2)
        monkeypatch.setattr(dem_cache.shutil, "rmtree",
                            lambda *a, **k: None)   # 消えなかった状況を作る

        res = dem_cache.delete_all_tile_cache(
            sources=[dem_sources.GSI_DEM], include_basemap=False)

        assert res == {"deleted": 0}
        assert dem_cache.get_cache_stats(dem_sources.GSI_DEM) == \
            {"count": 2, "size_bytes": 8}


# ============================================================
# I-169（3.6 ステージ1）＝キャッシュ内訳の単一の出所（get_cache_breakdown）
# ============================================================
class TestGetCacheBreakdown:
    """地図の統計表示・全削除ダイアログが読む単一の集計関数の不変条件。"""

    EXTERNAL = TestCacheStatsAndDeletionBySource.EXTERNAL

    def _seed(self, root, layer_dir: str, n: int, nbytes: int = 4) -> None:
        d = os.path.join(root, *layer_dir.split("/"))
        os.makedirs(d, exist_ok=True)
        for i in range(n):
            with open(os.path.join(d, f"{i}.png"), "wb") as f:
                f.write(b"x" * nbytes)

    def test_breakdown_lists_each_source_and_basemap_and_sums_to_total(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem_sources, "_user_sources", [self.EXTERNAL])
        self._seed(str(tmp_path), "dem5a_png/1", 2)                      # GSI
        fp = dem_sources.definition_fingerprint(self.EXTERNAL)
        self._seed(str(tmp_path), f"external/ext_src/{fp}/terrarium/1", 3)  # 外部ソース
        self._seed(str(tmp_path), f"{dem.BASEMAP_SUBDIR}/14/1", 1)       # 背景地図

        result = dem_cache.get_cache_breakdown()

        by_id = {s["source_id"]: s for s in result["sources"]}
        assert by_id["gsi_dem"]["count"] == 2
        assert by_id["gsi_dem"]["size_bytes"] == 8
        assert by_id["ext_src"]["count"] == 3
        assert by_id["ext_src"]["size_bytes"] == 12
        assert by_id["ext_src"]["display_name"] == "External"
        assert result["basemap"] == {"count": 1, "size_bytes": 4}
        # どの内訳にも属さない残りが無いときは、合計＝内訳の和＝CACHE_DIR 全体。
        assert result["total"] == {"count": 6, "size_bytes": 24}
        assert result["total"] == dem_cache.get_cache_stats()

    def test_counts_the_same_range_that_deletion_sweeps(self, tmp_path, monkeypatch):
        """ソース別の容量は削除で消える範囲、総量は CACHE_DIR 全体（B-282）。

        宣言を書き換える前のハッシュの下・3.5 以前の旧置き場・宣言を消した
        ソースの残りは、読む側の置き場には無いが、ディスクは使っている。
        """
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem_sources, "_user_sources", [self.EXTERNAL])
        fp = dem_sources.definition_fingerprint(self.EXTERNAL)
        self._seed(str(tmp_path), f"external/ext_src/{fp}/terrarium/1", 1)          # 今の定義
        self._seed(str(tmp_path), "external/ext_src/0123456789ab/terrarium/1", 2)  # 書き換え前
        self._seed(str(tmp_path), "ext_src/terrarium/1", 3)                        # 3.5 以前
        self._seed(str(tmp_path), "external/gone_src/abcdef012345/t/1", 4)         # 宣言を消した

        result = dem_cache.get_cache_breakdown()
        by_id = {s["source_id"]: s for s in result["sources"]}
        assert by_id["ext_src"]["count"] == 6
        assert result["total"]["count"] == 10

        deleted = dem_cache.delete_all_tile_cache(
            sources=[self.EXTERNAL], include_basemap=False)["deleted"]
        assert deleted == by_id["ext_src"]["count"], "表示と削除で数える範囲が違う"

    def test_breakdown_with_only_gsi_has_one_source_entry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        monkeypatch.setattr(dem_sources, "_user_sources", [])
        self._seed(str(tmp_path), "dem5a_png/1", 2)

        result = dem_cache.get_cache_breakdown()

        assert [s["source_id"] for s in result["sources"]] == ["gsi_dem"]
        assert result["basemap"] == {"count": 0, "size_bytes": 0}
        assert result["total"] == {"count": 2, "size_bytes": 8}


# ============================================================
# カバレッジ走査を「粗いレイヤを持つソース」で通す（B-264）
# ============================================================

class TestCoverageScanWithCoarseSourceZoom:
    """宣言したレイヤの zoom が 14 未満のソースでもカバレッジ走査が通ること。

    B-264＝集約単位が zoom-14 決め打ちで `shift = tile_zoom - 14` が負になり、
    `x >> shift` が `ValueError: negative shift count` を投げていた。地図側では
    ワーカースレッドなので**画面に何も出ず、キャッシュが無いように見えた**。

    ⚠️ **ここは実物の走査を通す**＝既存の `tests/test_map_window.py` の
    ソース選択テストは `scan_cache_overlay` を丸ごと差し替えており、「ソースが
    渡ること」しか見ていない（それが B-264 を通した理由）。
    """

    # マニュアルの記載例と同じ zoom（`docs/manual_ja.md` の Terrarium）。
    COARSE = dem_sources.DemSourceSpec(
        source_id="coarse_src",
        display_name="Coarse Source",
        layers=(("terrarium", 12),),
        url_template="https://example.invalid/{z}/{x}/{y}.png",
        decode=dem_sources.DecodeMethod.TERRARIUM,
        invalid_rgb=None,
        attribution="Example",
        terms_url="https://example.invalid/terms",
    )

    LAT, LON = 34.54, 132.41
    WIDE = (46.0, 128.0, 30.0, 146.0)

    def _touch(self, src, layer_id, x, y):
        from PIL import Image
        d = os.path.join(dem.source_layer_dir(src, layer_id), str(x))
        os.makedirs(d, exist_ok=True)
        Image.new("RGB", (2, 2)).save(os.path.join(d, f"{y}.png"))

    def test_scan_returns_the_cached_cell_at_the_declared_zoom(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x12, y12, _, _ = dem._tile_coords(self.LAT, self.LON, 12)
        self._touch(self.COARSE, "terrarium", x12, y12)

        cells = dem_cache.scan_cache_overlay(*self.WIDE, 10, source=self.COARSE)

        assert cells == [
            {"x": x12, "y": y12, "zoom": 12, "level": "terrarium"}]

    def test_overlay_zoom_is_clamped_to_the_base_cell(self, tmp_path, monkeypatch):
        """基準セルより細かい粒度は要求されても返さない（14 を頼んでも 12 のまま）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x12, y12, _, _ = dem._tile_coords(self.LAT, self.LON, 12)
        self._touch(self.COARSE, "terrarium", x12, y12)

        cells = dem_cache.scan_cache_overlay(*self.WIDE, 14, source=self.COARSE)

        assert [c["zoom"] for c in cells] == [12]

    def test_outline_encloses_the_cached_tile(self, tmp_path, monkeypatch):
        """外周線は基準セル（zoom-12）の角で閉じる＝タイルを実際に囲む。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x12, y12, _, _ = dem._tile_coords(self.LAT, self.LON, 12)
        self._touch(self.COARSE, "terrarium", x12, y12)

        loops = dem_cache.coverage_outline(*self.WIDE, source=self.COARSE)

        assert len(loops) == 1
        lats = [lat for lat, _lon in loops[0]]
        lons = [lon for _lat, lon in loops[0]]
        # タイルの NW 角と SE 角（= 隣のタイルの NW 角）で囲まれている。
        nw_lat, nw_lon = dem_cache.tile_to_latlng(x12, y12, 12)
        se_lat, se_lon = dem_cache.tile_to_latlng(x12 + 1, y12 + 1, 12)
        assert min(lats) == pytest.approx(se_lat)
        assert max(lats) == pytest.approx(nw_lat)
        assert min(lons) == pytest.approx(nw_lon)
        assert max(lons) == pytest.approx(se_lon)
        # 代表点（そのタイルを取った座標）が確かに内側にある。
        assert se_lat < self.LAT < nw_lat
        assert nw_lon < self.LON < se_lon

    def test_count_cached_areas_does_not_raise(self, tmp_path, monkeypatch):
        """範囲削除の件数表示（メインスレッドで呼ばれる）も落ちない。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x12, y12, _, _ = dem._tile_coords(self.LAT, self.LON, 12)
        self._touch(self.COARSE, "terrarium", x12, y12)

        assert dem_cache.count_cached_areas(*self.WIDE, source=self.COARSE) == 1

    def test_gsi_base_cell_is_unchanged(self):
        """国土地理院の基準セルは従来どおり zoom-14（この直しで 1 ビットも動かさない）。"""
        assert dem_cache._base_zoom(dem_sources.GSI_DEM) == 14
        assert dem_cache._base_zoom(None) == 14
        assert dem_cache._base_zoom(self.COARSE) == 12
