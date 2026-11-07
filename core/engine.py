"""
core/engine.py
==============
**計算コアへの呼び口**（4.0 ステージ6・ステージ5 の UI/UX パスで決めた 3 つ）。
新しい画面も、旧画面の作業面（バッチ・中継・条件探索）も、ここを呼ぶ。

    (1) `fetch_terrain` ＝取り消せる地形の取得（標高・DEM の取得日・標本ごとの事情を
        地形へ載せて返す）
    (2) `calculate`     ＝地形の上の計算（純計算・ネットワーク無し＝入力したらすぐ計算する口）
    (3) `run_paths`     ＝作業面の実行＝1 件ごとに取り消しを見て、結果を順に返す

🔑 **成果物（PNG・HTML・KML）は作らない**＝`report/` の仕事。tkinter も matplotlib も引かない。

取り消し（`cancel`＝`threading.Event`）は省略できる。立てると、取得は次の標本から、
作業面は次の件から止まり、`simulation.Cancelled` が呼び出し側へ上がる（失敗ではない）。
取り消した地形はキャッシュに載らない（`simulation.fetch_elevations_cached`）。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, TypeVar

from core import models
from core import simulation as sim

logger = logging.getLogger("radiosim")

T = TypeVar("T")

Cancelled = sim.Cancelled
raise_if_cancelled = sim.raise_if_cancelled


def fetch_terrain(
    params: sim.SimParams,
    on_progress: "Callable[[int], None] | None" = None,
    *,
    cancel: "threading.Event | None" = None,
) -> models.TerrainProfile:
    """`params` の経路の地形を取る（キャッシュ付き・同期・取り消せる）。

    取得日（B-213）と標本ごとの事情（I-247）は標高と一緒に受け取り、地形へ載せる＝
    保存のときに引き直さない。`on_progress` は取得済みの標本数を受ける。
    """
    acquired: list = [None]
    status: list = [None]
    raw = sim.fetch_elevations_sync(
        params, on_progress, cancel=cancel,
        on_acquired=lambda v: acquired.__setitem__(0, v),
        on_status=lambda v: status.__setitem__(0, v))
    terrain = models.calculate_terrain_profile(
        raw_elevs=raw,
        lat_tx=params.lat_tx, lon_tx=params.lon_tx,
        lat_rx=params.lat_rx, lon_rx=params.lon_rx,
        # ⚠️ **標高を読んだ位置をそのまま渡す**（B-150・落としたのが B-157）＝等間隔ではない
        frac_axis=params.sample_fracs,
    )
    terrain.dem_acquired = acquired[0]
    terrain.dem_status = status[0]
    return terrain


def terrain_key(params: sim.SimParams) -> tuple:
    """`params` の地形を見分ける鍵＝同じ鍵なら地形を取り直さなくてよい（新しい画面の判定）。

    `fetch_elevations_cached` のキャッシュと同じ鍵（端点・標本・段階・解決後の DEM ソース・
    読み方）＝鍵を 2 つ持たない。
    """
    return sim._terrain_cache_key(params)


def calculate(terrain: models.TerrainProfile,
              params: sim.SimParams) -> models.LinkBudgetResult:
    """地形の上で `params` の空中線高のまま計算する（判定の語も載る＝`run_calculation`）。"""
    return sim.run_calculation(terrain, params.h_tx, params.h_rx, params)


@dataclass
class PathOutcome:
    """作業面の 1 件の結果（取得と計算まで＝成果物は呼び出し側が作る）。

    `error` があれば `result` は None（入力が解けなければ `params` も None）。
    """
    params:  "sim.SimParams | None" = None
    terrain: "models.TerrainProfile | None" = None
    result:  "models.LinkBudgetResult | None" = None
    error:   "Exception | None" = None


def run_paths(
    items: Iterable[T],
    make_params: Callable[[T], sim.SimParams],
    *,
    cancel: "threading.Event | None" = None,
    on_start: "Callable[[int, int, str], None] | None" = None,
    on_stage: "Callable[[str], None] | None" = None,
    on_progress: "Callable[[int, int], None] | None" = None,
    label: "Callable[[T], str] | None" = None,
) -> Iterator[tuple[T, PathOutcome]]:
    """`items` を 1 件ずつ取得して計算し、`(item, PathOutcome)` を順に返す（生成器）。

    - 1 件ごとに `cancel` を見る＝立っていれば次の件へ進まず `Cancelled` を投げる
    - `on_start(何件目, 全件, ラベル)`・`on_stage("fetch")`・`on_progress(取得済み, その件の点数)`
      （点数は件ごとに解かれる＝I-069）
    - ⛔ `DemUnreachableError` と `Cancelled` は外へ投げて全体を止める（B-025 ②＝届かないのは
      経路でなく環境の問題）。ほかの例外はその件の `PathOutcome(error=)` に畳む
      ＝traceback はここで残す（全体は完走するので、最上位の except に届かない）
    """
    seq = list(items)
    total = len(seq)
    for i, item in enumerate(seq):
        raise_if_cancelled(cancel)
        name = label(item) if label else str(i + 1)
        if on_start:
            on_start(i + 1, total, name)
        yield item, _run_one(item, make_params, name, cancel, on_stage, on_progress)


def _run_one(item, make_params, name, cancel, on_stage, on_progress) -> PathOutcome:
    params = terrain = None
    try:
        params = make_params(item)
        if on_stage:
            on_stage("fetch")
        progress = None
        if on_progress:
            num = params.num
            progress = lambda done: on_progress(done, num)  # noqa: E731
        terrain = fetch_terrain(params, progress, cancel=cancel)
        return PathOutcome(params=params, terrain=terrain,
                           result=calculate(terrain, params))
    except (sim.DemUnreachableError, Cancelled):
        raise
    except Exception as ex:
        logger.exception("Path '%s' failed: %s", name, ex)
        return PathOutcome(params=params, terrain=terrain, error=ex)
