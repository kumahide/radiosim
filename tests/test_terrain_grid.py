"""
tests/test_terrain_grid.py
==========================
地形の解像度（I-069）＝**段階から点数を解く**層のゲート。

守っているのは 3 つ：
  1. **段階が段階として効く**（高 < 中 < 低 の順に細かい・実務の距離帯で天井に
     張り付かない）。⇒ 旧上限 2000 の失敗そのものの再発防止。
  2. **点数の解き方が 1 か所しかない**（画面と実行が同じ口を通る）。
  3. **数で入れる口が復活しない**（入力は段階だけ）。
"""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import config              # noqa: E402
from core import simulation as sim   # noqa: E402
from core import terrain_grid as tg  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============================================================
# 1. 段階が段階として効く
# ============================================================
# 実務の距離帯（ISSUES.md I-069 の実測＝コーパス 26 本は 2〜48km）。
_PRACTICAL_KM = [0.5, 1, 2, 5, 10, 20, 30, 48]


@pytest.mark.parametrize("km", _PRACTICAL_KM)
def test_finer_level_never_gives_fewer_points(km):
    """同じ経路なら 高 ≥ 中 ≥ 低 の点数になる（段階の向きが逆転しない）。"""
    m = km * 1000.0
    high, med, low = (tg.recommended_samples(m, k) for k in ("high", "medium", "low"))
    assert high >= med >= low, f"{km}km で段階の順序が壊れている: {high}/{med}/{low}"


@pytest.mark.parametrize("km", _PRACTICAL_KM)
@pytest.mark.parametrize("level", ["high", "medium", "low"])
def test_the_level_actually_delivers_its_spacing(km, level):
    """**実効間隔が名乗った間隔とほぼ一致する**（＝天井に潰されていない）。

    🔴 **これが I-069 の芯**＝旧上限 2000 では、コーパス 26 本のうち 12 本で「高」が
    上限に張り付き、6 本は「高」と「中」が**同じ結果**、48km の 1 本は 3 段階すべてが
    同じ結果だった（2026-08-25 実測）。**利用者が選んだ段階が答えに出ない**なら、
    段階を選ばせる意味そのものが無い。⇒ 実務の距離帯では必ず届くことを固定する。
    """
    m = km * 1000.0
    n = tg.recommended_samples(m, level)
    spacing = tg.effective_spacing_m(m, n)
    target = tg.RESOLUTION_SPACING_M[level]
    assert spacing == pytest.approx(target, rel=0.05), (
        f"{km}km / {level}: 実効 {spacing:.2f}m が目標 {target}m と違う"
        f"（点数 {n}・天井 {tg.SAMPLES_CEILING} に張り付いていないか）"
    )


def test_the_ceiling_only_bites_at_absurd_distances():
    """天井が効き始めるのは**事故の距離**であること（実務の 48km では効かない）。

    天井を残すのは座標の打ち間違い（地球半周など）で点数が爆発しないため。
    ⇒ 「効かないなら外せ」ではなく「**実務では効かない高さに置く**」が答え。
    """
    assert tg.recommended_samples(48_000, "high") < tg.SAMPLES_CEILING
    assert tg.recommended_samples(5_000_000, "high") == tg.SAMPLES_CEILING


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_broken_distance_falls_back_to_the_floor(bad):
    """距離が読めない／0 でも落ちない（下限を返す）。"""
    assert tg.recommended_samples(bad, "high") == tg.SAMPLES_MIN
    assert tg.effective_spacing_m(bad, 100) == 0.0


def test_unknown_level_falls_back_to_the_default():
    """知らない段階の語は既定へ落とす（**計算の側は実行を止めない**）。

    ⚠️ 入力として弾くのは `config.validate_config` の仕事＝ここで例外にすると、
    壊れた設定ファイル 1 つでアプリが起動直後に落ちる。
    """
    assert (tg.recommended_samples(10_000, "ultra")
            == tg.recommended_samples(10_000, tg.RESOLUTION_DEFAULT))


def test_the_default_level_is_a_real_level():
    assert tg.RESOLUTION_DEFAULT in tg.RESOLUTION_KEYS
    assert set(tg.RESOLUTION_SPACING_M) == set(tg.RESOLUTION_KEYS)


def test_the_finest_level_matches_the_finest_mesh():
    """いちばん細かい段階は **DEM の 1px そのもの**（表が 2 つに割れていない）。"""
    assert tg.RESOLUTION_SPACING_M["high"] == tg.FINEST_MESH_M


# ============================================================
# 2. 解き方の口が 1 つしかない
# ============================================================
def _calls_recommended_samples(path: str) -> bool:
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and node.attr in ("recommended_samples", "effective_spacing_m")):
            return True
    return False


def test_no_window_resolves_the_sample_count_by_itself():
    """**画面は自分で点数を解かない**（`simulation.resolve_samples` を通る）。

    🔑 見せる側が自前で解くと、**画面に出た N と実際に使われた N がずれる余地**が
    できる。しかもズレは誰にも見えない（両方それらしい数字に見える）。
    ⇒ 面を列挙せず `views/` を走査する向きにしてある＝次に足した 1 窓でも効く。
    """
    views = os.path.join(ROOT, "views")
    offenders = [
        name for name in sorted(os.listdir(views))
        if name.endswith(".py")
        and _calls_recommended_samples(os.path.join(views, name))
    ]
    assert not offenders, (
        f"{offenders} が点数を自分で解いている＝`simulation.resolve_samples` を"
        "通すこと（画面と実行で同じ値を見るための唯一の口）"
    )


def test_the_readout_and_the_run_agree(monkeypatch):
    """読み取り欄が使う口と、実行が使う値が**同じ数**であること。"""
    c = dict(
        start="34.5429, 132.4118", end="34.6429, 132.5118",
        h_tx="30", h_rx="10", freq="2400", p_tx="20",
        gain_tx="3", gain_rx="3", sens="-85", veg_h="10", k_factor="10",
        resolution="high", env_type="los", rain_rate="0.0",
        diff_method="deygout",
    )
    params = sim.SimParams(c)
    shown, spacing = sim.resolve_samples(
        params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx, "high")
    assert shown == params.num
    assert spacing == pytest.approx(tg.RESOLUTION_SPACING_M["high"], rel=0.05)


# ============================================================
# 3. 数で入れる口が復活しない
# ============================================================
def test_the_sample_count_is_not_an_input_any_more():
    """設定にも値域表にも `samples` が無いこと（入力は段階だけ）。

    ⚠️ 戻すなら**この検査ごと**戻すこと＝「入口は 1 つ」を崩すと、窓によって
    意味の違う入力が同居する（I-069 の起点そのもの）。
    """
    assert "samples" not in config.DEFAULT_CONFIG
    assert "samples" not in config.VALIDATION_RULES
    assert "resolution" in config.DEFAULT_CONFIG


def test_a_broken_level_is_rejected_by_the_validator():
    assert config.validate_value("resolution", "ultra") is not None
    assert config.validate_value("resolution", "high") is None
