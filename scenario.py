"""
scenario.py
===========
同一経路の条件探索（2.5 / A-1 差分比較・A-2 パラメータスイープ）の共有ランナー。

UI 知識ゼロ・ヘッドレス。単一（launcher/graph）・バッチ（batch_builder）・地図
（map_window）に続く **4 つ目の実行フロー**で、この 3 つとは狙いが違う：

  - 単一 ＝ 1 条件を対話的に詰める（what-if スライダ）
  - バッチ ＝ N 本の**独立した回線**の成果物を作る
  - **条件探索（ここ）＝ 1 本の確定した経路を、条件を変えて掘る**

**🔑 成立の根拠＝計算パイプラインが 2 相であること**：`fetch_elevations`
（高コスト・ネットワーク・**座標とサンプル数にしか依存しない**）と
`run_calculation`（低コスト・純関数）。したがって **DEM 取得 1 回 + N 回の純計算**
で条件探索が成り立つ。この前提（terrain 使い回しの安全性・評価順非依存・入力を
壊さないこと）は `tests/test_golden_links.py::TestRunCalculationIsPure` が固定
している＝崩れても値は"それらしく"出るので、構造で守る。

**進捗の「相（phase）」の宣言**（2.4b3 の宿題・B-006/I-008 の構造対策）：
既存 3 フローは「進捗をコールバックが取れる相に合わせ、重い相が管轄外になる」
同じ欠陥を別々に持っていた（バッチ＝B-006／単一＝I-008）。トランスポートは
`views/progress.ProgressPump` へ畳んだが、**配分の意味論は各フローが手書きのまま**。
4 つ目を作るこの版で、`Phases`（名前＋重み）としてランナー側へ入れる。
既存 3 フローを移すかは、ここで形が固まってから判断する（先に全面移行しない）。
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

import models
import simulation as sim

logger = logging.getLogger("radiosim")


# ============================================================
# 条件（1 条件＝ベース SimParams への上書き）
# ============================================================
# 上書きできる項目＝**terrain を作り直さずに効く**もの（＝座標とサンプル数以外）。
# 座標/samples を変えると DEM 取得が要り「同一経路を掘る」という前提から外れる
# ため、ここには含めない（経路自体の比較はバッチの仕事＝2026-07-25 決定）。
OVERRIDABLE: tuple[str, ...] = (
    "freq_mhz", "p_tx", "gain_tx", "gain_rx", "sens",
    "h_tx", "h_rx", "veg_h", "k_factor", "rain_rate",
    "env_type", "diff_method",
)

# スイープの軸に選べる項目＝上書き可能なもののうち**連続量**。
# env_type / diff_method は離散の選択肢なので軸にせず、比較（A-1）で扱う。
SWEEP_AXES: tuple[str, ...] = (
    "freq_mhz", "p_tx", "gain_tx", "gain_rx", "sens",
    "h_tx", "h_rx", "veg_h", "k_factor", "rain_rate",
)

# 1 回のスイープで許す点数の上限。純計算なので速いが、レポートの表が A4 に
# 収まらなくなる・軸の刻みを無闇に細かくしても判断は変わらない（DEM 10m 精度が
# 上限＝設計哲学①）ため、器の側で上限を持つ。
MAX_SWEEP_POINTS = 41


@dataclass(frozen=True)
class Condition:
    """1 条件＝ラベル＋ベース params への上書き。

    `overrides` のキーは OVERRIDABLE のみ（それ以外は ValueError）。
    値は数値または文字列（env_type / diff_method）。
    """
    label: str
    overrides: dict[str, float | str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        bad = sorted(set(self.overrides) - set(OVERRIDABLE))
        if bad:
            raise ValueError(f"上書きできない項目です: {bad}")


@dataclass
class ScenarioPoint:
    """条件 1 つ分の結果（＝比較の 1 列 / スイープの 1 点）。"""
    label:     str
    overrides: dict[str, float | str]
    h_tx:      float
    h_rx:      float
    result:    models.LinkBudgetResult

    @property
    def ok(self) -> bool:
        return self.result.status == "OK"


@dataclass
class ScenarioRun:
    """条件探索 1 回分の結果一式（レポート層・View が読む）。

    kind ＝ "compare"（A-1）/ "sweep"（A-2）。sweep のときだけ axis と
    axis_values が入る（レポートの折れ線の横軸）。
    """
    kind:        str
    base_params: sim.SimParams
    terrain:     models.TerrainProfile
    points:      list[ScenarioPoint]
    axis:        str = ""
    axis_values: list[float] = field(default_factory=list)

    @property
    def labels(self) -> list[str]:
        return [p.label for p in self.points]

    def margins(self) -> list[float]:
        return [p.result.actual_margin for p in self.points]

    def first_ok_index(self) -> int:
        """判定が初めて OK になる点の位置（無ければ -1）。

        スイープの主眼は「どこで足りるようになるか」なので、しきい値を
        レポートと View が同じ規則で指す（各所で別々に判定させない）。
        """
        for i, p in enumerate(self.points):
            if p.ok:
                return i
        return -1


# ============================================================
# 進捗の「相」の宣言
# ============================================================
@dataclass(frozen=True)
class Phase:
    """実行の 1 相。`weight` は所要時間の相対的な重み（合計は任意）。"""
    key:    str
    weight: float


# 条件探索の相：DEM 取得（ネットワーク・支配的）→ 純計算（N 回・軽い）。
# 成果物の生成はレポート層の仕事で、呼び出し側が "render" 相を足せる。
FETCH = Phase("fetch", 8.0)
CALC  = Phase("calc", 1.0)
RENDER = Phase("render", 3.0)


class Phases:
    """宣言された相を順に進め、**全体の進捗率**へ換算して通知する。

    各フローが「相の名前と重み」を宣言し、あとは `start(phase)` と
    `advance(done, total)` を呼ぶだけにする＝**重い相が進捗の管轄外に置かれる**
    （B-006／I-008 の欠陥）を、配分をランナー側に持つことで構造的に防ぐ。

    `on_phase` は相の切り替わり（UI のラベル差し替え）、`on_progress` は
    0〜100 の全体進捗。どちらもワーカースレッドから呼ばれるので、View は
    ProgressPump 経由でメインスレッドへ渡すこと。
    """

    def __init__(
        self,
        phases: list[Phase],
        on_phase: "Callable[[str], None] | None" = None,
        on_progress: "Callable[[int], None] | None" = None,
    ) -> None:
        if not phases:
            raise ValueError("相が 1 つも宣言されていない")
        self._phases = list(phases)
        self._total_weight = sum(p.weight for p in self._phases) or 1.0
        self._on_phase = on_phase
        self._on_progress = on_progress
        self._index = -1
        self._done_weight = 0.0

    @property
    def current(self) -> str:
        return self._phases[self._index].key if self._index >= 0 else ""

    def start(self, phase: Phase) -> None:
        """次の相へ進む（宣言した順に呼ぶこと）。"""
        idx = self._phases.index(phase)
        if idx <= self._index:
            raise ValueError(f"相の順序が宣言と違う: {phase.key}")
        # 飛ばされた相も完了扱いにする（宣言と実行のズレで進捗が巻き戻らない）。
        self._done_weight = sum(p.weight for p in self._phases[:idx])
        self._index = idx
        if self._on_phase:
            self._on_phase(phase.key)
        self._emit(0.0)

    def advance(self, done: int, total: int) -> None:
        """現在の相の中の進み具合（done/total）を通知する。"""
        if self._index < 0:
            raise ValueError("start() より前に advance() が呼ばれた")
        self._emit(0.0 if total <= 0 else min(1.0, max(0.0, done / total)))

    def finish(self) -> None:
        """全相の完了（100%）を通知する。"""
        self._done_weight = self._total_weight
        self._index = len(self._phases) - 1
        self._emit(0.0)

    def _emit(self, frac_in_phase: float) -> None:
        if not self._on_progress:
            return
        w = self._phases[self._index].weight if self._index >= 0 else 0.0
        overall = (self._done_weight + w * frac_in_phase) / self._total_weight
        self._on_progress(int(round(min(1.0, overall) * 100)))


# ============================================================
# 純計算（terrain 固定・N 条件）
# ============================================================
def _apply(base: sim.SimParams, overrides: dict[str, float | str]) -> sim.SimParams:
    """ベース params の複製に上書きを当てる（**ベースは書き換えない**）。

    SimParams はスカラー属性しか持たないので浅い複製で足りる。呼び出し側の
    params を汚さないことは A-1/A-2 の生命線（同じ params を N 回使うため）。
    """
    p = copy.copy(base)
    for key, value in overrides.items():
        if key not in OVERRIDABLE:
            raise ValueError(f"上書きできない項目です: {key}")
        setattr(p, key, value)
    return p


def evaluate(
    terrain: models.TerrainProfile,
    base:    sim.SimParams,
    conditions: list[Condition],
    on_point: "Callable[[int, int], None] | None" = None,
) -> list[ScenarioPoint]:
    """固定した terrain の上で条件を順に評価する（純計算・ネットワーク無し）。

    ここが 2.5 の心臓部＝「DEM 取得 1 回 + run_calculation を N 回」。
    terrain も base も**書き換えない**（→ モジュール docstring の不変条件）。
    """
    points: list[ScenarioPoint] = []
    total = len(conditions)
    for i, cond in enumerate(conditions, 1):
        p = _apply(base, cond.overrides)
        result = sim.run_calculation(terrain, p.h_tx, p.h_rx, p)
        points.append(ScenarioPoint(
            label=cond.label, overrides=dict(cond.overrides),
            h_tx=p.h_tx, h_rx=p.h_rx, result=result,
        ))
        if on_point:
            on_point(i, total)
    return points


def sweep_conditions(axis: str, values: list[float]) -> list[Condition]:
    """1 軸 N 点のスイープを Condition 列へ変換する（A-2）。

    ラベルは軸の値そのもの（レポートの横軸・表の見出しに使う）。書式は
    レポート層が単位付きで整えるので、ここでは数値を素直に文字列化する。
    """
    if axis not in SWEEP_AXES:
        raise ValueError(f"スイープできない軸です: {axis}")
    if len(values) < 2:
        raise ValueError("スイープには 2 点以上が要ります")
    if len(values) > MAX_SWEEP_POINTS:
        raise ValueError(f"点数が多すぎます（上限 {MAX_SWEEP_POINTS}）")
    return [Condition(label=f"{v:g}", overrides={axis: float(v)}) for v in values]


def linspace_values(start: float, stop: float, points: int) -> list[float]:
    """スイープ軸の等間隔な値を返す（View の入力欄からの変換用）。"""
    if points < 2:
        raise ValueError("スイープには 2 点以上が要ります")
    if points > MAX_SWEEP_POINTS:
        raise ValueError(f"点数が多すぎます（上限 {MAX_SWEEP_POINTS}）")
    return [float(v) for v in np.linspace(float(start), float(stop), int(points))]


# ============================================================
# 実行（DEM 取得 1 回 → 純計算 N 回）
# ============================================================
def run_scenario(
    base_params: sim.SimParams,
    conditions:  list[Condition],
    on_complete: Callable[[ScenarioRun], None],
    on_error:    Callable[[Exception], None],
    *,
    kind:        str = "compare",
    axis:        str = "",
    axis_values: "list[float] | None" = None,
    on_phase:    "Callable[[str], None] | None" = None,
    on_progress: "Callable[[int], None] | None" = None,
    artifacts:   "Callable[[ScenarioRun], None] | None" = None,
) -> None:
    """条件探索をバックグラウンドスレッドで実行する。

    `artifacts` を渡すと**このワーカースレッドの中で** RENDER 相として呼ぶ
    （レポート生成＝matplotlib Agg と文字列組み立てのみで tkinter に触れない）。
    こうする理由が「相の宣言」の本体＝**重い相をランナーの管轄外に置かない**。
    View 側で完了後に生成すると、①GUI スレッドが固まる ②その時間が進捗率に
    現れない（B-006／I-008 で 2 度起きた欠陥）。相の宣言はここに一本化する。
    """
    phases = Phases([FETCH, CALC, *( [RENDER] if artifacts else [] )],
                    on_phase, on_progress)

    def _work() -> None:
        try:
            t0 = time.perf_counter()
            phases.start(FETCH)
            raw = _fetch_sync(base_params, phases)
            terrain = models.calculate_terrain_profile(
                raw_elevs=raw,
                lat_tx=base_params.lat_tx, lon_tx=base_params.lon_tx,
                lat_rx=base_params.lat_rx, lon_rx=base_params.lon_rx,
            )
            t_fetch = time.perf_counter() - t0

            phases.start(CALC)
            t1 = time.perf_counter()
            points = evaluate(terrain, base_params, conditions,
                              on_point=phases.advance)
            logger.info(
                "Scenario complete: kind=%s points=%d (fetch %.2fs / calc %.3fs)",
                kind, len(points), t_fetch, time.perf_counter() - t1,
            )
            run = ScenarioRun(
                kind=kind, base_params=base_params, terrain=terrain,
                points=points, axis=axis, axis_values=list(axis_values or []),
            )
            if artifacts:
                phases.start(RENDER)
                t2 = time.perf_counter()
                artifacts(run)
                logger.info("Scenario artifacts complete in %.2fs",
                            time.perf_counter() - t2)
            phases.finish()
            on_complete(run)
        except Exception as ex:      # 失敗は呼び出し側へ 1 本化して渡す
            logger.error("Scenario error: %s", ex)
            on_error(ex)

    threading.Thread(target=_work, daemon=True).start()


def _fetch_sync(params: sim.SimParams, phases: Phases) -> np.ndarray:
    """標高取得（非同期 API を同期化）。**キャッシュ付きを使う**。

    バッチと同じく `fetch_elevations_cached` を通す＝同じ経路で条件を変えて
    何度も回すのが条件探索の使い方そのものなので、2 回目以降は取得が消える
    （設計哲学④＝外部 API に優しい）。
    """
    out: list[np.ndarray] = []
    err: list[Exception] = []
    done = threading.Event()
    total = params.num

    def _on_complete(elevs: np.ndarray) -> None:
        out.append(elevs)
        done.set()

    def _on_error(ex: Exception) -> None:
        err.append(ex)
        done.set()

    sim.fetch_elevations_cached(
        params,
        on_progress=lambda n: phases.advance(n, total),
        on_complete=_on_complete,
        on_error=_on_error,
    )
    done.wait()
    if err:
        raise err[0]
    return out[0]
