"""
tests/test_dem.py
=================
dem.py のユニットテスト（DEM/淡色地図タイル取得・標高デコード・キャッシュ）。
HTTP 通信は monkeypatch で差し替え、ネットワーク接続不要。
"""

import json
import os
import unittest.mock as mock

import numpy as np
import pytest
import requests

from core import config
from core import dem
from core import dem_prefetch


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
        assert ("dem_png", 111, 222) in dem._failed_tiles

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
        """通信で失敗したら `network_failed()` が立つこと。

        戻り値は 0.0 のまま（契約は 3.x まで変えない）なので、**これが「取れて
        いない」を知る唯一の手段**。ここが立たないと、呼び出し側は標高 0m の
        平坦地形を正常値として配り続ける（B-025 の実害そのもの）。
        """
        self._mock_session(
            monkeypatch,
            lambda *a, **k: (_ for _ in ()).throw(requests.RequestException("timeout")),
        )
        assert dem.get_elevation(34.5429, 132.4118) == pytest.approx(0.0)
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

        # 1回目：全レイヤが一時失敗 → 0.0、負キャッシュは汚れない。
        assert dem.get_elevation(34.5429, 132.4118) == pytest.approx(0.0)
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
        tiles = dem._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        assert all(len(t) == 6 for t in tiles)

    def test_covers_all_dem_layers(self):
        tiles = dem._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        layer_ids = {t[0] for t in tiles}
        assert layer_ids == {lid for lid, _ in dem.DEM_LAYERS}

    def test_at_least_one_tile_per_layer(self):
        tiles = dem._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        for layer_id, _ in dem.DEM_LAYERS:
            assert any(t[0] == layer_id for t in tiles)

    def test_inverted_coords_same_result(self):
        """lat1/lon1 が NW でなくても同じ結果を返す（入力順に依存しない）。"""
        tiles_nw_se = dem._enumerate_bbox(34.54, 132.40, 34.53, 132.41)
        tiles_se_nw = dem._enumerate_bbox(34.53, 132.41, 34.54, 132.40)
        assert set(t[:4] for t in tiles_nw_se) == set(t[:4] for t in tiles_se_nw)

    def test_larger_area_returns_more_tiles(self):
        small = dem._enumerate_bbox(34.540, 132.410, 34.539, 132.409)
        large = dem._enumerate_bbox(34.600, 132.500, 34.400, 132.300)
        assert len(large) > len(small)

    def test_tile_coords_in_valid_range(self):
        """タイル座標がズームレベルに対して有効な範囲内であること。"""
        tiles = dem._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
        for layer_id, zoom, x, y, subdir, cache_path in tiles:
            assert 0 <= x < 2 ** zoom
            assert 0 <= y < 2 ** zoom

    def test_cache_path_contains_layer_and_coords(self):
        """cache_path が layer_id / x / y.png の構造を持つこと。"""
        tiles = dem._enumerate_bbox(34.54, 132.41, 34.53, 132.40)
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
        tile_arr = np.zeros((256, 256, 3), dtype=np.uint8)
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
        tile_arr = np.zeros((256, 256, 3), dtype=np.uint8)

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
        tile_arr = np.zeros((256, 256, 3), dtype=np.uint8)

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
        tile_arr = np.zeros((256, 256, 3), dtype=np.uint8)

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
        """全画素 (128,0,0) の欠損タイル、または全画素有効(0,0,0)のタイル。"""
        arr = np.zeros((256, 256, 3), dtype=np.uint8)
        if void:
            arr[:, :, 0] = 128
        return arr

    def test_descends_to_5b_when_5a_has_void(self, tmp_path, monkeypatch):
        """5a 取得成功だが欠損あり・5b が補完 → 5b も取得し dem は不要。"""
        import threading
        valid = np.zeros((256, 256, 3), dtype=np.uint8)

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
                return np.zeros((256, 256, 3), dtype=np.uint8)
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
            return np.zeros((256, 256, 3), dtype=np.uint8) if layer_id == "dem5a_png" else None

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
        """_void_mask が (128,0,0) のみを True とすること。"""
        arr = np.zeros((2, 2, 3), dtype=np.uint8)
        arr[0, 0] = (128, 0, 0)   # 無効値
        arr[0, 1] = (0, 0, 1)     # 標高 0.01m（有効）
        arr[1, 0] = (128, 0, 1)   # 有効（b!=0）
        mask = dem_prefetch._void_mask(arr)
        assert mask[0, 0] and not mask[0, 1] and not mask[1, 0] and not mask[1, 1]


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
        return np.zeros((256, 256, 3), dtype=np.uint8)

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
        assert dem.scan_cache_overlay(
            self.LAT, self.LON, self.LAT - 0.01, self.LON + 0.01, 14
        ) == []

    def test_5a_wins_over_dem_at_same_cell(self, tmp_path, monkeypatch):
        """同じ zoom-14 セルに 5a と dem があれば最高精度 5a を返す。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        x15, y15, _, _ = dem._tile_coords(self.LAT, self.LON, 15)
        self._touch(tmp_path, "dem_png", x14, y14)
        self._touch(tmp_path, "dem5a_png", x15, y15)
        cells = dem.scan_cache_overlay(
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
        cells = dem.scan_cache_overlay(
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
        cells = dem.scan_cache_overlay(43.07, 141.34, 43.06, 141.35, 14)
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
        cells = dem.scan_cache_overlay(*self.WIDE, 12)
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
        cells = dem.scan_cache_overlay(*self.WIDE, 12)
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
        cached = dem.count_cached_areas(*wide)
        total = dem_prefetch.count_bbox_tiles(*wide)
        assert cached == 2
        assert total > cached   # 範囲総数は未取得を含むので多い

    def test_count_cached_areas_zero_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        assert dem.count_cached_areas(*self.WIDE) == 0

    def test_broken_tile_excluded(self, tmp_path, monkeypatch):
        """壊れたタイルは件数表示・塗りのどちらにも「取得済み」として現れない（B-143）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)          # 読める
        self._touch_broken(tmp_path, "dem_png", x14 + 1, y14)  # 壊れている
        wide = (self.LAT + 0.1, self.LON - 0.1, self.LAT - 0.1, self.LON + 0.1)
        assert dem.count_cached_areas(*wide) == 1
        cells = dem.scan_cache_overlay(*wide, 14)
        assert not any(c["x"] == x14 + 1 and c["y"] == y14 for c in cells)

    def test_repaired_tile_becomes_visible_after_rewrite(self, tmp_path, monkeypatch):
        """壊れたタイルが上書きで直ったら、次の走査で取得済みとして現れる（メモがstat変化で追従）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch_broken(tmp_path, "dem_png", x14, y14)
        wide = (self.LAT + 0.1, self.LON - 0.1, self.LAT - 0.1, self.LON + 0.1)
        assert dem.count_cached_areas(*wide) == 0   # メモに「壊れている」を記録
        self._touch(tmp_path, "dem_png", x14, y14)   # stat（mtime/size）が変わる
        assert dem.count_cached_areas(*wide) == 1    # メモが古いと踏んだままにならない


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
        assert dem.coverage_outline(*self.WIDE) == []

    def test_single_cell_is_rectangle(self, tmp_path, monkeypatch):
        """単一セル → 4 頂点の矩形ループ1個。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        loops = dem.coverage_outline(*self.WIDE)
        assert len(loops) == 1
        assert len(loops[0]) == 4

    def test_adjacent_cells_merge_to_one_outline(self, tmp_path, monkeypatch):
        """隣接2セルは内部線なしの単一矩形（4頂点）になる。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        x14, y14, _, _ = dem._tile_coords(self.LAT, self.LON, 14)
        self._touch(tmp_path, "dem_png", x14, y14)
        self._touch(tmp_path, "dem_png", x14 + 1, y14)
        loops = dem.coverage_outline(*self.WIDE)
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
        loops = dem.coverage_outline(*self.WIDE)
        assert len(loops) == 1
        assert len(loops[0]) == 6


class TestBasemapTiles:
    """淡色地図（レポート地図）タイルの取得・キャッシュ・削除。"""

    LAT, LON = 34.54, 132.41
    WIDE = (34.6, 132.3, 34.4, 132.5)

    def test_tile_path_includes_zoom(self):
        """キャッシュパスにズームが入る（異なるズームの同一(x,y)が衝突しない）。"""
        subdir, path = dem._basemap_tile_path(14, 100, 200)
        assert os.path.join(dem.BASEMAP_SUBDIR, "14", "100") in subdir
        assert path.endswith(os.path.join("100", "200.png"))
        # ズーム違いはパスが異なる。
        _, path15 = dem._basemap_tile_path(15, 100, 200)
        assert path != path15

    def test_fetch_basemap_tiles_parallel_returns_dict(self, monkeypatch):
        """並列取得が成功タイルだけを {(x,y):配列} で返す。"""
        def fake(layer_id, zoom, x, y, subdir, path):
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
        subdir, path = dem._basemap_tile_path(z, x, y)
        os.makedirs(subdir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"\x89PNG")
        assert os.path.exists(path)
        dem.delete_tile_cache(*self.WIDE)
        assert os.path.exists(path)

    def test_delete_all_tile_cache_removes_basemap(self, tmp_path, monkeypatch):
        """全キャッシュ削除は basemap タイルも消す。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        z = 14
        x, y, _, _ = dem._tile_coords(self.LAT, self.LON, z)
        subdir, path = dem._basemap_tile_path(z, x, y)
        os.makedirs(subdir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"\x89PNG")
        assert os.path.exists(path)
        dem.delete_all_tile_cache()
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
        tiles = dem._enumerate_bbox(*self.BBOX)
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
        layer_id, _, x, y, _, _ = tiles[0]
        dem._tile_cache[(layer_id, x, y)] = np.zeros(1)
        dem._tile_cache[("dem_png", 0, 0)] = np.zeros(1)
        dem._failed_tiles.add((layer_id, x, y))

        res = dem.delete_tile_cache(*self.BBOX)

        assert res == {"deleted": len(tiles), "errors": 0}
        assert all(not os.path.exists(p) for *_, p in tiles)
        assert os.path.exists(outside_file)
        assert (layer_id, x, y) not in dem._tile_cache
        assert ("dem_png", 0, 0) in dem._tile_cache
        assert (layer_id, x, y) not in dem._failed_tiles

    def test_missing_files_count_zero(self, tmp_path, monkeypatch):
        """未取得エリアの範囲削除は deleted=0（存在しないものを数えない）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._fresh_memory_cache(monkeypatch)
        assert dem.delete_tile_cache(*self.BBOX) == {"deleted": 0, "errors": 0}

    def test_get_cache_stats_missing_dir_is_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path / "no_such_dir"))
        assert dem.get_cache_stats() == {"count": 0, "size_bytes": 0}

    def test_get_cache_stats_counts_png_only(self, tmp_path, monkeypatch):
        """枚数・総バイト数は .png のみ集計（ログ等の同居ファイルを数えない）。"""
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        d = tmp_path / "dem_png" / "123"
        d.mkdir(parents=True)
        (d / "1.png").write_bytes(b"abc")
        (d / "2.png").write_bytes(b"abcde")
        (d / "note.txt").write_bytes(b"zz")
        assert dem.get_cache_stats() == {"count": 2, "size_bytes": 8}

    def test_delete_all_removes_png_and_clears_memory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dem, "CACHE_DIR", str(tmp_path))
        self._fresh_memory_cache(monkeypatch)
        dem._tile_cache[("dem_png", 1, 2)] = np.zeros(1)
        dem._failed_tiles.add(("dem_png", 1, 2))
        d = tmp_path / "dem_png" / "1"
        d.mkdir(parents=True)
        (d / "2.png").write_bytes(b"\x89PNG")
        (tmp_path / "keep.txt").write_bytes(b"keep")

        res = dem.delete_all_tile_cache()

        assert res == {"deleted": 1}
        assert not (d / "2.png").exists()
        assert (tmp_path / "keep.txt").exists()   # .png 以外は消さない
        assert dem._tile_cache == {}
        assert dem._failed_tiles == set()
