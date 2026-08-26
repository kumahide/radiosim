"""
tests/test_golden_links.py
==========================
回帰コーパス＝物理モデル（models.py / simulation.run_calculation）の**数値そのもの**を
代表回線 26 本で凍結する。あわせて「terrain 固定で run_calculation を N 回まわす」
という A-1/A-2（2.5 の条件探索）の前提＝純関数としての不変条件を固定する。

なぜ要るか
----------
既存の `tests/test_models.py` は**関係性**（単調性・範囲・式の整合）を守るが、
**値そのもの**は守っていない。係数を書き換える／リファクタで項の順序が変わる、
といった変更は関係性テストを全通過したまま全回線の dB を動かせる。2.5 の A-1/A-2 は
`run_calculation` の呼び出し面を N 倍に増やし、3.5（ローカル較正）は全回線の数値を
意図的に動かす操作なので、**基準（コーパス）が無いと影響範囲を誰も把握できない**。

コーパスの中身と流儀
--------------------
- `tests/data/golden_links.json` ＝ 入力一式＋**実 DEM 由来の標高配列**＋その時点の
  `LinkBudgetResult` 全項目。生成器は `tests/golden_corpus_gen.py`（手動実行）。
- **このテストはネットワークに触らない**（標高配列から再計算するだけ）。
- **モデルを意図して変えたときは生成器を再実行し、JSON の差分をレビューして
  コミットする**＝差分が「どの回線がどれだけ動いたか」の一覧になる。
  意図しない差分が出たらそれが回帰。**JSON を手で編集して通すのは禁じ手**
  （テストを黙らせるだけで、影響範囲の記録が失われる）。
- 許容差は **1e-6 dB**（＝物理的に無意味な桁。丸め順序の揺れは通し、係数や式の
  変更は落とす）。標高は保存時に 2 桁へ丸め、期待値も丸めた配列から作ってあるので
  入力側の不一致は起きない。
"""

import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import models
from core import simulation as sim

_DATA = os.path.join(os.path.dirname(__file__), "data", "golden_links.json")

with open(_DATA, encoding="utf-8") as _f:
    CORPUS = json.load(_f)

LINKS = CORPUS["links"]
IDS = [link["id"] for link in LINKS]

# 許容差 [dB / km]。物理的に無意味な桁だけを許す（→ モジュール docstring）。
TOL = 1e-6

# 突合する数値項目（LinkBudgetResult 全項目＋地形の水平距離）。
_NUMERIC_FIELDS = (
    "horiz_dist_km", "eirp", "fspl", "diff_loss", "veg_loss", "env_loss",
    "rain_loss", "gas_loss", "total_loss", "p_rx", "actual_margin",
    "current_k", "blocked_ratio", "slant_dist_km",
)
_TEXT_FIELDS = ("status", "diff_method", "env_type")


def _params(inp: dict) -> sim.SimParams:
    return sim.SimParams({
        "start": f"{inp['lat_tx']}, {inp['lon_tx']}",
        "end":   f"{inp['lat_rx']}, {inp['lon_rx']}",
        "h_tx": str(inp["h_tx"]), "h_rx": str(inp["h_rx"]),
        "freq": str(inp["freq_mhz"]), "p_tx": str(inp["p_tx"]),
        "gain_tx": str(inp["gain_tx"]), "gain_rx": str(inp["gain_rx"]),
        "sens": str(inp["sens"]), "veg_h": str(inp["veg_h"]),
        "k_factor": str(inp["k_factor"]), "samples": str(inp["samples"]),
        "env_type": inp["env_type"], "diff_method": inp["diff_method"],
        "rain_rate": str(inp["rain_rate"]),
    })


def _terrain(link: dict, params: sim.SimParams) -> models.TerrainProfile:
    """コーパスの標高配列から TerrainProfile を作る。

    ⚠️ earth_k は既定（4/3）＝単一・バッチと同じ呼び方。`params.k_factor` は
    ライス K の表示値で曲率とは無関係（models.calculate_terrain_profile 参照）。
    """
    return models.calculate_terrain_profile(
        raw_elevs=np.array(link["raw_elevs"], dtype=float),
        lat_tx=params.lat_tx, lon_tx=params.lon_tx,
        lat_rx=params.lat_rx, lon_rx=params.lon_rx,
    )


def _run(link: dict):
    params  = _params(link["input"])
    terrain = _terrain(link, params)
    return terrain, sim.run_calculation(terrain, params.h_tx, params.h_rx, params)


# ============================================================
# ゴールデン値（代表回線 26 本 × 全項目）
# ============================================================
@pytest.mark.parametrize("link", LINKS, ids=IDS)
def test_link_budget_matches_golden(link):
    """凍結した LinkBudgetResult 全項目と一致すること。

    落ちたときは**まず「モデルを変えたか」を確認する**。意図した変更なら
    `python tests/golden_corpus_gen.py` で作り直し、差分をレビューして
    コミットする（差分が影響範囲そのもの）。意図していないなら回帰。
    """
    terrain, result = _run(link)
    exp = link["expected"]
    got = {"horiz_dist_km": terrain.horiz_dist_km,
           **{f: getattr(result, f) for f in _NUMERIC_FIELDS[1:]}}
    diffs = []
    for f in _NUMERIC_FIELDS:
        if abs(got[f] - exp[f]) > TOL:
            diffs.append(f"{f}: golden={exp[f]!r} got={got[f]!r}")
    for f in _TEXT_FIELDS:
        if getattr(result, f) != exp[f]:
            diffs.append(f"{f}: golden={exp[f]!r} got={getattr(result, f)!r}")
    assert not diffs, (
        f"{link['id']}: ゴールデン値との差 {len(diffs)} 件\n  " + "\n  ".join(diffs)
    )


# ============================================================
# コーパス自体の網羅性（痩せさせない）
# ============================================================
class TestCorpusCoverage:
    """コーパスが薄くなる方向の編集を止める（守る面が黙って減るのを防ぐ）。"""

    def test_link_count(self):
        assert len(LINKS) >= 26, "代表回線が 26 本を下回っている"

    def test_ids_unique(self):
        assert len(set(IDS)) == len(IDS)

    def test_covers_all_env_types(self):
        envs = {link["input"]["env_type"] for link in LINKS}
        assert envs == set(models.ENV_COEFFS), f"未カバーの env_type: {set(models.ENV_COEFFS) - envs}"

    def test_covers_both_diff_methods(self):
        assert {link["input"]["diff_method"] for link in LINKS} == {"single", "bullington"}

    def test_covers_both_verdicts(self):
        assert {link["expected"]["status"] for link in LINKS} == {"OK", "NG"}

    def test_covers_rain_on_and_off(self):
        rains = {link["input"]["rain_rate"] for link in LINKS}
        assert 0.0 in rains and any(r > 0 for r in rains)

    def test_covers_below_and_above_1ghz(self):
        """1 GHz 境界の両側（降雨・大気減衰がゼロになる分岐）を含むこと。"""
        freqs = [link["input"]["freq_mhz"] for link in LINKS]
        assert any(f < 1000 for f in freqs) and any(f >= 1000 for f in freqs)

    def test_covers_short_and_long_paths(self):
        d = [link["expected"]["horiz_dist_km"] for link in LINKS]
        assert min(d) < 6.0 and max(d) > 25.0

    def test_covers_the_grazing_band(self):
        """回折損が数 dB／current_k が内挿帯にある回線を必ず持つこと。

        深く塞がれた回線は diff_loss が飽和し current_k も 0 に張り付くため、
        **その領域だけのコーパスは回折・K 推定の係数変更を検出できない**
        （実際、初版のコーパスは current_k が 1.33 か 0.0 しか取らず、
        `initial_k - diff_loss/3` の係数を変えても全通過した）。
        """
        exp = [link["expected"] for link in LINKS]
        assert any(0.0 < e["diff_loss"] < 6.0 for e in exp),             "回折損が数 dB の「かすめ」回線が無い"
        ks = [(e["current_k"], i["k_factor"])
              for e, i in zip(exp, (link["input"] for link in LINKS))]
        assert any(0.0 < k < k0 for k, k0 in ks),             "current_k が内挿帯（0 < k < initial_k）にある回線が無い"

    def test_elevations_are_real_terrain(self):
        """標高配列が実 DEM 由来＝定数列や合成波形でないこと。"""
        for link in LINKS:
            elevs = np.array(link["raw_elevs"], dtype=float)
            assert len(elevs) == link["input"]["samples"]
            assert float(np.std(elevs)) > 0.0, f"{link['id']}: 平坦な合成地形に見える"


# ============================================================
# 純関数としての不変条件（A-1/A-2 が terrain 固定で N 回まわす前提）
# ============================================================
class TestRunCalculationIsPure:
    """`scenario.py`（A-1/A-2）が暗黙に依存する前提を明示的に固定する。

    2.5 の条件探索は「DEM 取得 1 回 → terrain 固定 → params だけ変えて
    run_calculation を N 回」で成り立つ。この前提が崩れると、比較結果が
    計算順序や前の実行の残留状態に汚染される（しかも値は"それらしく"出るので
    気づけない）。ゲートとして残す。
    """

    # staticmethod なのは pytest 10 対策：class スコープの fixture を
    # インスタンスメソッドで書く形は 9.1 で deprecated（10 で削除）。
    # この fixture は self を一切使わないので形を変えるだけで済む。
    @pytest.fixture(scope="class")
    @staticmethod
    def link():
        return LINKS[0]

    def test_repeated_runs_are_bit_identical(self, link):
        _, first = _run(link)
        for _ in range(5):
            _, again = _run(link)
            for f in _NUMERIC_FIELDS[1:]:
                assert getattr(again, f) == getattr(first, f), f

    def test_reusing_one_terrain_matches_fresh_terrain(self, link):
        """同じ terrain を使い回した結果＝毎回作り直した結果（A-1/A-2 の土台）。"""
        params  = _params(link["input"])
        terrain = _terrain(link, params)
        reused = [sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
                  for _ in range(3)]
        fresh  = [_run(link)[1] for _ in range(3)]
        for r, f in zip(reused, fresh):
            for name in _NUMERIC_FIELDS[1:]:
                assert getattr(r, name) == getattr(f, name), name

    def test_does_not_mutate_terrain_or_params(self, link):
        params  = _params(link["input"])
        terrain = _terrain(link, params)
        raw_before   = terrain.raw_elevs.copy()
        curve_before = terrain.elevs_with_curve.copy()
        axis_before  = terrain.d_km_axis.copy()
        params_before = dict(vars(params))

        sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
        sim.run_calculation(terrain, params.h_tx + 20, params.h_rx, params,
                            rain_rate=120.0)

        assert np.array_equal(terrain.raw_elevs, raw_before)
        assert np.array_equal(terrain.elevs_with_curve, curve_before)
        assert np.array_equal(terrain.d_km_axis, axis_before)
        assert dict(vars(params)) == params_before, \
            "run_calculation が SimParams を書き換えた（rain_rate 引数は params を汚さない）"

    def test_result_is_independent_of_evaluation_order(self, link):
        """A→B と B→A で同じ値（前の実行の残留状態に汚染されない）。"""
        params  = _params(link["input"])
        terrain = _terrain(link, params)

        def a():
            return sim.run_calculation(terrain, 5.0, 5.0, params, rain_rate=0.0)

        def b():
            return sim.run_calculation(terrain, 90.0, 60.0, params, rain_rate=150.0)

        a1, b1 = a(), b()
        b2, a2 = b(), a()
        for name in _NUMERIC_FIELDS[1:]:
            assert getattr(a1, name) == getattr(a2, name), f"A の値が順序で変わった: {name}"
            assert getattr(b1, name) == getattr(b2, name), f"B の値が順序で変わった: {name}"

    def test_rain_argument_overrides_params_without_side_effect(self, link):
        """rain_rate 引数（グラフのスライダ経路）が params を汚さないこと。"""
        params  = _params(link["input"])
        terrain = _terrain(link, params)
        base    = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
        sim.run_calculation(terrain, params.h_tx, params.h_rx, params, rain_rate=200.0)
        after   = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
        assert after.rain_loss == base.rain_loss
        assert after.p_rx == base.p_rx
