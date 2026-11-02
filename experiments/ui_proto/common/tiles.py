"""背景地図のタイルを Python 側のディスクキャッシュから配る（オフラインの地図の案）。

画面（Leaflet・tkintermapview）はインターネットへ出ない＝`127.0.0.1` の `/tile/...`
だけを見る。外へ出るのはここだけなので、プロキシと社内の証明書は製品と同じ
`core.dem` の経路で受けられる。キャッシュも製品と同じ場所を使う。
"""
from __future__ import annotations

import os
import threading

from core import dem

_lock = threading.Lock()
_counts = {"cache": 0, "fetched": 0, "failed": 0, "absent": 0}
_last_error = ""
_last_failed = ""


def _count(key: str) -> None:
    with _lock:
        _counts[key] += 1


def get_tile(source_id: str, z: int, x: int, y: int) -> bytes | None:
    """タイルの PNG を返す。キャッシュに無く取れもしなければ None。

    世界の外（x・y が 2^z 以上）と配信元の 404 は「無い」に数え、失敗に入れない
    ＝オフラインや社内の遮断で取れなかったものだけを失敗として画面に出す。
    """
    global _last_error, _last_failed
    if x >= 2 ** z or y >= 2 ** z:
        _count("absent")
        return None
    src = dem._resolve_basemap_source(source_id)
    subdir, path = dem._basemap_tile_path(src, z, x, y)
    try:
        with open(path, "rb") as f:
            data = f.read()
        _count("cache")
        return data
    except OSError:
        pass
    try:
        arr = dem._fetch_tile(src.source_id, z, x, y, subdir, path, src)
        if arr is None:
            with dem._cache_lock:
                absent = (src.source_id, src.source_id, x, y) in dem._failed_tiles
            if absent:
                _count("absent")
                return None
            raise OSError("タイルが返らなかった")
        with open(path, "rb") as f:
            data = f.read()
    except Exception as exc:
        _count("failed")
        with _lock:
            _last_error = f"{type(exc).__name__}: {exc}"[:80]
            _last_failed = f"{z}/{x}/{y}"
        return None
    _count("fetched")
    return data


def stats() -> dict:
    with _lock:
        return {**_counts, "last_error": _last_error, "last_failed": _last_failed,
                "cache_dir": os.path.basename(dem.CACHE_DIR)}
