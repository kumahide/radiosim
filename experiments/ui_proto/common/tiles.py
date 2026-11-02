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
_counts = {"cache": 0, "fetched": 0, "failed": 0}
_last_error = ""


def _count(key: str) -> None:
    with _lock:
        _counts[key] += 1


def get_tile(source_id: str, z: int, x: int, y: int) -> bytes | None:
    """タイルの PNG を返す。キャッシュに無く取れもしなければ None。"""
    global _last_error
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
            raise OSError("タイルが返らなかった")
        with open(path, "rb") as f:
            data = f.read()
    except Exception as exc:
        _count("failed")
        with _lock:
            _last_error = type(exc).__name__
        return None
    _count("fetched")
    return data


def stats() -> dict:
    with _lock:
        return {**_counts, "last_error": _last_error,
                "cache_dir": os.path.basename(dem.CACHE_DIR)}
