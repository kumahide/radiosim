"""
dem_cache.py
============
DEM タイルキャッシュの**在庫調査・カバレッジ表示・削除**。1 点の取得は
`core/dem.py`、面での事前取得は `core/dem_prefetch.py`。

  - キャッシュ済みタイルの走査（zoom-14 セル単位の集約）
  - カバレッジの適応的粒度セル化（`scan_cache_overlay`）・外周線（`coverage_outline`）
  - 範囲削除（`delete_tile_cache`）・全削除（`delete_all_tile_cache`）・統計（`get_cache_stats`）

⚠️ **`core/dem.py` から切り出した**（3.3 ステージ4a）＝関心事で割る：*いま要る 1 点の
取得*（dem.py）と*すでに在るキャッシュの棚卸し*（この層）は寿命が違う。前者は
計算の最中に呼ばれ、後者は利用者がキャッシュ管理パネルを開いたときにだけ動く。
`dem_prefetch.py`（B-141）と対になる分割で、切り口の基準は同じ。

⚠️ **`dem` の名前は属性参照で引く**（`from core.dem import CACHE_DIR` にしない）
＝テストが `dem.CACHE_DIR` や `dem._tile_cache` を差し替えるので、束縛を写すと
差し替えが効かなくなる（`dem_prefetch.py` と同じ注意）。
"""

import math
import os
import shutil

from core import dem
from core import dem_sources
from core.config import logger


# 国土地理院のレベルラベル＝**既存の "5a"/"5b"/"dem" を1文字も変えない**
# （`views/map_style.py` の `_LEVEL_COLORS` と地図色分けが直接依存している）。
_GSI_LEVEL_LABELS: dict[str, str] = {
    "dem5a_png": "5a", "dem5b_png": "5b", "dem_png": "dem",
}


def _overlay_layers(
    source: "dem_sources.DemSourceSpec | None" = None,
) -> list[tuple[str, int, str, int]]:
    """精度レベルの優先順位（大きいほど高精度）: (layer_id, tile_zoom, level, priority)。

    3.4 ステージ1（I-147）＝`source` 引数で層構成を一般化。省略時は国土地理院で、
    レベルラベル・優先度とも従来どおり（"5a"=3 / "5b"=2 / "dem"=1）。それ以外の
    ソースは `layer_id` そのものをレベルラベルにする（3層固定を崩さず、
    ソースごとに層数が違ってもよいようにする）。
    """
    src = source if source is not None else dem_sources.GSI_DEM
    n = len(src.layers)
    is_gsi = src.source_id == dem_sources.GSI_DEM.source_id
    return [
        (layer_id, zoom, _GSI_LEVEL_LABELS[layer_id] if is_gsi else layer_id, n - i)
        for i, (layer_id, zoom) in enumerate(src.layers)
    ]


def _priority_to_level(source: "dem_sources.DemSourceSpec | None" = None) -> dict[int, str]:
    return {priority: level for _layer_id, _zoom, level, priority in _overlay_layers(source)}


#: カバレッジ集約の基準ズームの上限＝国土地理院 `dem_png` の上限。ここより細かい
#: セルにしても、全国カバーの層が持っていない精度を描くことになる。
_MAX_BASE_ZOOM = 14


def _base_zoom(source: "dem_sources.DemSourceSpec | None" = None) -> int:
    """カバレッジ集約の基準ズーム（1 セルの大きさ）を返す。

    B-264（`3.5`）＝以前は **14 決め打ち**だったので、宣言したレイヤの zoom が
    14 未満の利用者ソース（マニュアルの記載例の Terrarium が `12`）では
    `shift = tile_zoom - 14` が負になり、`x >> shift` が
    `ValueError: negative shift count` で落ちていた。カバレッジ走査は
    ワーカースレッドなので画面には何も出ず、**キャッシュが在るのに無いように見えた**。

    🔑 **基準は「一番粗いレイヤ」に合わせる**＝1 枚のタイルが基準セルの整数個に
    収まる必要がある（シフトで潰す集約なので `tile_zoom >= base_zoom` が前提）。
    国土地理院は `min(14, 14) = 14` で**従来と何も変わらない**。
    """
    src = source if source is not None else dem_sources.GSI_DEM
    if not src.layers:
        return _MAX_BASE_ZOOM
    return min(_MAX_BASE_ZOOM, min(zoom for _layer_id, zoom in src.layers))


def tile_to_latlng(x: int, y: int, zoom: int) -> tuple[float, float]:
    """タイル座標 (x, y, zoom) の NW コーナーの緯度経度を返す。"""
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return math.degrees(lat_rad), lon


def _scan_cached_positions(
    lat_n: float, lat_s: float,
    lon_w: float, lon_e: float,
    source: "dem_sources.DemSourceSpec | None" = None,
) -> dict[tuple[int, int], int]:
    """表示範囲内の**読める**キャッシュ済みタイルを基準ズームのセル単位で集約する。

    実在するキャッシュファイルだけを走査するため計算量はキャッシュ量に比例し、
    地理的範囲には比例しない。各レイヤーの x ディレクトリ一覧を起点に走査し、
    表示範囲外を間引く。

    壊れたタイル（B-143）は含めない。`os.scandir` の `DirEntry.stat()` は
    Windows では列挙時に得た情報を使うため追加の syscall にならず、その
    (mtime, size) を `dem._is_tile_readable_memoized` の鍵にすることで、初回以外は
    Image.open による復号なしに existence-only 相当の速さで判定できる。

    Returns: {(x_base, y_base): 最高 priority}（セルのズームは `_base_zoom(source)`）
    """
    # ⚠️ **集約単位はソースごとの基準セル**＝`_base_zoom()`（国土地理院は zoom-14＝
    # `dem_png` と同じ基準で従来どおり）。`scan_cache_overlay` の統合ループ・
    # `coverage_outline` の格子も同じ基準を起点にする。以前はここが 14 決め打ちで、
    # **zoom 14 未満のレイヤを持つ利用者ソースで負シフトになり例外**だった（B-264）。
    src = source if source is not None else dem_sources.GSI_DEM
    base_zoom = _base_zoom(src)
    base: dict[tuple[int, int], int] = {}
    for layer_id, tile_zoom, _level, priority in _overlay_layers(src):
        layer_dir = dem.source_layer_dir(src, layer_id)
        if not os.path.isdir(layer_dir):
            continue
        x_min, y_min, _, _ = dem._tile_coords(lat_n, lon_w, tile_zoom)
        x_max, y_max, _, _ = dem._tile_coords(lat_s, lon_e, tile_zoom)
        # `_base_zoom` の取り方から `tile_zoom >= base_zoom` が保証される
        # （GSI: zoom-15(5a/5b)→1, zoom-14(dem)→0）。
        shift = tile_zoom - base_zoom
        try:
            x_entries = os.scandir(layer_dir)
        except OSError:
            continue
        with x_entries:
            for x_entry in x_entries:
                try:
                    x = int(x_entry.name)
                except ValueError:
                    continue
                if x < x_min or x > x_max:
                    continue
                try:
                    y_entries = os.scandir(x_entry.path)
                except OSError:
                    continue
                with y_entries:
                    for y_entry in y_entries:
                        if not y_entry.name.endswith(".png"):
                            continue
                        try:
                            y = int(y_entry.name[:-4])
                        except ValueError:
                            continue
                        if y < y_min or y > y_max:
                            continue
                        try:
                            st = y_entry.stat()
                        except OSError:
                            continue
                        if not dem._is_tile_readable_memoized(y_entry.path, st):
                            continue
                        key = (x >> shift, y >> shift)
                        if base.get(key, 0) < priority:
                            base[key] = priority
    return base


def count_cached_areas(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    source: "dem_sources.DemSourceSpec | None" = None,
) -> int:
    """bbox 内で実際にキャッシュ済みの基準セル数を返す（削除対象の件数表示用）。

    count_bbox_tiles が範囲内の全エリア（未取得含む）を数えるのに対し、本関数は
    実在キャッシュのみを数える。セルの大きさは `_base_zoom(source)`＝国土地理院は
    従来どおり zoom-14 エリア、宣言 zoom がそれより粗いソースはその zoom（B-264）。
    """
    lat_n = max(lat1, lat2)
    lat_s = min(lat1, lat2)
    lon_w = min(lon1, lon2)
    lon_e = max(lon1, lon2)
    return len(_scan_cached_positions(lat_n, lat_s, lon_w, lon_e, source))


def scan_cache_overlay(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    overlay_zoom: int,
    source: "dem_sources.DemSourceSpec | None" = None,
) -> list[dict]:
    """表示範囲内のキャッシュを「適応的粒度」のセルに集約して返す（自動表示用）。

    クアッドツリー方式: 基準セル（`_base_zoom`）を最小単位とし、2×2 の子がすべて存在し
    かつ同一精度レベルのときだけ親セルに統合する。これを overlay_zoom まで
    繰り返す。完全に埋まった領域の内部は大きなセル（ポリゴン少）になり、
    部分的にしか埋まっていない領域（＝カバレッジのエッジ）は細かいセルの
    まま残るため、粗い表示でもカバレッジ範囲を過大に見せない。

    キャッシュ済みセルのみを返す（"none" は返さない）。

    Returns:
        [{"x": int, "y": int, "zoom": int, "level": str}, ...]
        zoom はセルごとに異なる（overlay_zoom 〜 基準ズーム）。
        level は国土地理院なら "5a" | "5b" | "dem"、それ以外のソース（3.4 ステージ1）
        では宣言した `layer_id` そのもの。
    """
    lat_n = max(lat1, lat2)
    lat_s = min(lat1, lat2)
    lon_w = min(lon1, lon2)
    lon_e = max(lon1, lon2)

    src = source if source is not None else dem_sources.GSI_DEM
    # 基準セルより細かい粒度では描けない（国土地理院は dem_png の上限＝zoom-14）
    # ので overlay_zoom はそこで丸める。
    base_zoom = _base_zoom(src)
    overlay_zoom = max(2, min(base_zoom, overlay_zoom))
    current = _scan_cached_positions(lat_n, lat_s, lon_w, lon_e, src)   # 基準セル
    result: list[dict] = []
    priority_to_level = _priority_to_level(src)

    # 基準ズーム → overlay_zoom へ向けてボトムアップに統合する。
    # 親に統合できない（=部分的な）セルはその時点の zoom で確定出力する。
    zoom = base_zoom
    while zoom > overlay_zoom and current:
        groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for (x, y) in current:
            groups.setdefault((x >> 1, y >> 1), []).append((x, y))

        promoted: dict[tuple[int, int], int] = {}
        for parent, children in groups.items():
            levels = {current[c] for c in children}
            if len(children) == 4 and len(levels) == 1:
                # 4 子すべて存在・同一レベル → 親へ統合してさらに上を狙う
                promoted[parent] = next(iter(levels))
            else:
                # 部分的 or レベル混在 → このセル群は現 zoom で確定（エッジ）
                for c in children:
                    result.append(
                        {"x": c[0], "y": c[1], "zoom": zoom,
                         "level": priority_to_level[current[c]]}
                    )
        current = promoted
        zoom -= 1

    # 最後まで統合された（=完全に埋まった）セルを overlay_zoom で出力
    for (x, y), prio in current.items():
        result.append({"x": x, "y": y, "zoom": zoom, "level": priority_to_level[prio]})

    return result


def _simplify_grid_loop(pts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """格子座標の閉ループから一直線上の中間点を除く（角だけ残す）。"""
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    if n < 3:
        return pts
    out: list[tuple[int, int]] = []
    for i in range(n):
        prev = pts[(i - 1) % n]
        cur  = pts[i]
        nxt  = pts[(i + 1) % n]
        # 外積 0 = 3 点が一直線 → cur は角ではないので捨てる
        if (cur[0] - prev[0]) * (nxt[1] - cur[1]) - (cur[1] - prev[1]) * (nxt[0] - cur[0]) == 0:
            continue
        out.append(cur)
    return out


def coverage_outline(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    source: "dem_sources.DemSourceSpec | None" = None,
) -> list[list[tuple[float, float]]]:
    """キャッシュ済み領域の和集合の外周（と穴の境界）を緯度経度ループで返す。

    基準セル（`_base_zoom`）の境界辺を「有向辺の相殺」で求める。隣接する 2 セルが
    共有する辺は逆向きの有向辺として打ち消し合い、残った辺が領域の外周
    （および内側の穴の境界）になる。これにより内部のグリッド線は出ず、
    外周線だけが得られる。

    Returns:
        [[(lat, lon), ...], ...]  各ループは閉路（始点と終点は重複しない）。
    """
    lat_n = max(lat1, lat2)
    lat_s = min(lat1, lat2)
    lon_w = min(lon1, lon2)
    lon_e = max(lon1, lon2)
    base = _scan_cached_positions(lat_n, lat_s, lon_w, lon_e, source)

    # 各セルの 4 辺を一定の回転方向で有向辺として登録し、逆向きがあれば相殺する。
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()

    def _toggle(a: tuple[int, int], b: tuple[int, int]) -> None:
        if (b, a) in edges:
            edges.discard((b, a))
        else:
            edges.add((a, b))

    for (x, y) in base:
        _toggle((x, y),         (x + 1, y))
        _toggle((x + 1, y),     (x + 1, y + 1))
        _toggle((x + 1, y + 1), (x, y + 1))
        _toggle((x, y + 1),     (x, y))

    # 残った有向辺を始点でインデックス化し、連結してループを作る。
    successors: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for a, b in edges:
        successors.setdefault(a, []).append(b)

    remaining = set(edges)
    loops: list[list[tuple[int, int]]] = []
    for start in list(edges):
        if start not in remaining:
            continue
        cur = start
        pts: list[tuple[int, int]] = [cur[0]]
        while cur in remaining:
            remaining.discard(cur)
            pts.append(cur[1])
            nxt = None
            for cand in successors.get(cur[1], ()):
                if (cur[1], cand) in remaining:
                    nxt = (cur[1], cand)
                    break
            if nxt is None:
                break
            cur = nxt
        simplified = _simplify_grid_loop(pts)
        if len(simplified) >= 3:
            loops.append(simplified)

    # 格子点 (col, row) はその基準セルの NW 角に対応する（ズームは走査と同じ基準）。
    base_zoom = _base_zoom(source)
    return [[tile_to_latlng(c, r, base_zoom) for (c, r) in loop] for loop in loops]


def _enumerate_bbox(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    source: "dem_sources.DemSourceSpec | None" = None,
) -> list[tuple]:
    """bbox 内の全タイル座標を (layer_id, zoom, x, y, subdir, cache_path) のリストで返す。

    Web Mercator では x が東向き増加、y が南向き増加。
    NW コーナー（最大緯度・最小経度）が最小の (x, y) になる。
    """
    src = source if source is not None else dem_sources.GSI_DEM
    lat_n = max(lat1, lat2)
    lat_s = min(lat1, lat2)
    lon_w = min(lon1, lon2)
    lon_e = max(lon1, lon2)
    tasks: list[tuple] = []
    for layer_id, zoom in src.layers:
        x0, y0, _, _ = dem._tile_coords(lat_n, lon_w, zoom)  # NW: 最小 (x, y)
        x1, y1, _, _ = dem._tile_coords(lat_s, lon_e, zoom)  # SE: 最大 (x, y)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                subdir     = dem._cache_subdir_for(src, layer_id, x)
                cache_path = os.path.join(subdir, f"{y}.png")
                tasks.append((layer_id, zoom, x, y, subdir, cache_path))
    return tasks


def _clear_terrain_cache() -> None:
    """地形プロファイルのメモリキャッシュも捨てる（B-249・2026-09-19）。

    **なぜ下位層から上位層を呼ぶか**（層の向き＝2026-09-19 ユーザー選択）＝削除の
    入口は 3 か所（地図ウィンドウの範囲削除・ランチャーの全削除 2 経路）あり、画面側で
    2 つ並べて呼ぶ形にすると**入口が増えたときに呼び忘れる**。ここで呼べば構造的に
    起きない。`simulation` は `dem_cache` を import しないので循環にはならないが、
    **import は関数内に置く**（この層はキャッシュ管理パネルからしか動かないので、
    起動時に計算層を引きずり込まない）。

    ⚠️ **範囲削除でも全部捨てる**＝地形キャッシュの鍵は経路の端点と標本数で、
    タイルの bbox と突き合わせられない。取り直すだけで**計算の数字は変わらない**
    （同じソースの同じ地形）ので、残して食い違うより捨てるほうが安全側。
    """
    from core import simulation
    simulation.clear_terrain_cache()


def delete_tile_cache(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    source: "dem_sources.DemSourceSpec | None" = None,
) -> dict:
    """bbox 内のキャッシュファイルを削除し、メモリキャッシュも消去する。

    Returns:
        {"deleted": int, "errors": int}
    """
    src = source if source is not None else dem_sources.GSI_DEM
    tiles = _enumerate_bbox(lat1, lon1, lat2, lon2, src)
    deleted = 0
    errors  = 0
    keys_to_clear: set[tuple] = set()
    paths_to_clear: set[str] = set()
    for layer_id, _, x, y, _, cache_path in tiles:
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
                deleted += 1
                keys_to_clear.add((src.source_id, layer_id, x, y))
                paths_to_clear.add(cache_path)
            except OSError as e:
                logger.warning("delete_tile_cache: %s", e)
                errors += 1
    with dem._cache_lock:
        for key in keys_to_clear:
            dem._tile_cache.pop(key, None)
            dem._failed_tiles.discard(key)
        for path in paths_to_clear:
            dem._tile_validity_memo.pop(path, None)

    # 淡色地図（basemap）タイルは範囲削除の対象にしない。範囲削除はマップ
    # ウィンドウで可視化される DEM カバレッジに対する操作であり、basemap は
    # そこに表示されない（背景地図は tkintermapview 自前タイル・カバレッジ塗りは
    # DEM のみ）。見えないものを範囲指定で黙って消すのを避け、件数表示
    # （count_cached_areas=DEM のみ）とも整合させる。basemap は「全キャッシュ
    # 削除」（delete_all_tile_cache）でのみ消える。
    _clear_terrain_cache()
    logger.info("delete_tile_cache: deleted=%d errors=%d", deleted, errors)
    return {"deleted": deleted, "errors": errors}


def _walk_stats(root: str) -> dict:
    """`root` 配下の PNG を数え上げる（存在しなければ空の集計を返す）。"""
    count = 0
    size  = 0
    if os.path.exists(root):
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                if fname.endswith(".png"):
                    count += 1
                    try:
                        size += os.path.getsize(os.path.join(dirpath, fname))
                    except OSError:
                        pass
    return {"count": count, "size_bytes": size}


def get_cache_stats(source: "dem_sources.DemSourceSpec | None" = None) -> dict:
    """キャッシュの枚数と総バイト数を返す。

    I-155（3.5 ステージ3）＝`source` を指定すると**そのソースの DEM タイルだけ**を
    集計する（背景地図・他ソースは含めない）。省略時は従来どおり
    `CACHE_DIR` 全体（全ソース＋背景地図）を合算する＝**後方互換**。

    Returns:
        {"count": int, "size_bytes": int}
    """
    if source is None:
        return _walk_stats(dem.CACHE_DIR)
    total = {"count": 0, "size_bytes": 0}
    for layer_id, _zoom in source.layers:
        layer_stats = _walk_stats(dem.source_layer_dir(source, layer_id))
        total["count"] += layer_stats["count"]
        total["size_bytes"] += layer_stats["size_bytes"]
    return total


def get_basemap_cache_stats() -> dict:
    """背景地図（帳票サムネイル用の背景地図）キャッシュの枚数と総バイト数。

    I-155（3.5 ステージ3）＝`delete_all_tile_cache` のソース単位選択で「背景地図」を
    独立した対象として扱うための対。地図ウィンドウプレビューの背景タイルは
    `tkintermapview` が持ちこの層には含まれない（`fetch_basemap_tiles` の
    ディスクキャッシュのみが対象）。

    B-248＝地図ウィンドウの選択に追従して `"pale"` 以外（`photo`／宣言した外部
    ソース）も帳票の背景地図として取得され得るようになったので、
    `BASEMAP_SUBDIR`（`"pale"` 専用）と `BASEMAP_EXTRA_SUBDIR`（それ以外）の
    両方を合算する。
    """
    pale  = _walk_stats(os.path.join(dem.CACHE_DIR, dem.BASEMAP_SUBDIR))
    other = _walk_stats(os.path.join(dem.CACHE_DIR, dem.BASEMAP_EXTRA_SUBDIR))
    return {"count": pale["count"] + other["count"],
            "size_bytes": pale["size_bytes"] + other["size_bytes"]}


def get_cache_breakdown() -> dict:
    """キャッシュ内訳（ソース別＋背景地図＋合計）を単一の出所として返す。

    I-169（3.6 ステージ1）＝地図ウィンドウの統計表示・全削除ダイアログが、
    それぞれ別々に `get_cache_stats()` を呼んで合計だけ見せていたのを、
    この関数 1 つに集約する。合計は各内訳の**足し算**で作る（`CACHE_DIR`
    全体を別途もう一度走査しない）＝出所が 2 つに割れて数字が食い違う経路を
    構造的に無くす。

    Returns:
        {
            "sources": [{"source_id", "display_name", "count", "size_bytes"}, ...],
            "basemap": {"count", "size_bytes"},
            "total": {"count", "size_bytes"},
        }
    """
    sources: list[dict] = []
    total_count = 0
    total_size = 0
    for src in dem_sources.all_sources():
        stats = get_cache_stats(src)
        sources.append({
            "source_id": src.source_id,
            "display_name": src.display_name,
            "count": stats["count"],
            "size_bytes": stats["size_bytes"],
        })
        total_count += stats["count"]
        total_size += stats["size_bytes"]
    basemap = get_basemap_cache_stats()
    total_count += basemap["count"]
    total_size += basemap["size_bytes"]
    return {
        "sources": sources,
        "basemap": basemap,
        "total": {"count": total_count, "size_bytes": total_size},
    }


def delete_all_tile_cache(
    sources: "list[dem_sources.DemSourceSpec] | None" = None,
    include_basemap: bool = True,
) -> dict:
    """キャッシュファイルを削除し、メモリキャッシュも消去する。

    I-155（3.5 ステージ3）＝`sources`/`include_basemap` でソース単位の削除に対応。
    **省略時（両方とも既定値）は従来どおり `CACHE_DIR` 配下を無差別に全消し**
    する＝既存呼び出し側（テスト含む）との後方互換。

    Returns:
        {"deleted": int}
    """
    deleted = 0
    if sources is None and include_basemap:
        if os.path.exists(dem.CACHE_DIR):
            for dirpath, _, filenames in os.walk(dem.CACHE_DIR):
                for fname in filenames:
                    if fname.endswith(".png"):
                        try:
                            os.remove(os.path.join(dirpath, fname))
                            deleted += 1
                        except OSError as e:
                            logger.warning("delete_all_tile_cache: %s", e)
    else:
        targets: list[str] = []
        for src in (sources or []):
            # B-268＝消すときは宣言を書き換える前のタイルも掃く（読む側とは
            # 対象が違う＝`source_layer_dir` を列挙しない）。
            targets.extend(dem.source_delete_roots(src))
        if include_basemap:
            targets.append(os.path.join(dem.CACHE_DIR, dem.BASEMAP_SUBDIR))
            targets.append(os.path.join(dem.CACHE_DIR, dem.BASEMAP_EXTRA_SUBDIR))
        for root in targets:
            # B-269＝消す前の在庫ではなく、**消えた枚数**を数える。
            # `shutil.rmtree(ignore_errors=True)` は残っても黙るので、
            # 掃いたあとにもう一度数えて差を取る（`rmtree` の戻り値は無い）。
            before = _walk_stats(root)["count"]
            try:
                shutil.rmtree(root, ignore_errors=True)
            except OSError as e:   # pragma: no cover - ignore_errors なので届かない
                logger.warning("delete_all_tile_cache: %s", e)
            after = _walk_stats(root)["count"]
            deleted += before - after
            if after:
                logger.warning(
                    "delete_all_tile_cache: %d 枚が消えずに残った: %s", after, root)
    with dem._cache_lock:
        dem._tile_cache.clear()
        dem._failed_tiles.clear()
        dem._tile_validity_memo.clear()
    _clear_terrain_cache()
    logger.info("delete_all_tile_cache: deleted=%d", deleted)
    return {"deleted": deleted}
