"""
tests/test_model_preconditions.py
=================================
式の前提（適用範囲）を、ゴールデンコーパスの**全回線**に当てる（I-239 の 1・Tier 0）。

壊れた不変条件
--------------
「**式は、前提が成り立つ入力にだけ値を出す。成り立たなければ値を出さず、開示に戻す**」。

式ごとの単体テストは、書いた人が思いついた形（両端から遠い・端へ寄る…）しか通さない。
B-338 は、固定回線では普通にある「地形で遮られた経路」が単体テストに一度も現れず、
包絡線が前提の外で値を出し続けた。⇒ **実在の地形で組んだ回線を全部流し、
前提の述語を機械で確かめる**（回線を足せば、述語も自動でその回線を見る）。

流儀
----
- 回線の組み方は `tests/test_golden_links.py` の `_params`・`_terrain` をそのまま使う
  ＝本番（単一・バッチ）と同じ呼び方。ネットワークには触らない。
- 既知の反例は回線ごとに `xfail(strict=True, reason=<課題 ID>)` で置く
  ＝直した日に xpass で赤になり、印を外させる（既知の失敗を暗黙に持たない）。
  それ以外の回線は今日から守る＝新しい反例は普通の赤になる。
- 手本＝`tests/test_models.py` の `TestScopeNotes`（式を掃いて、刻印と突き合わせる）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import ground_reflection
from core import simulation as sim
from tests.test_golden_links import LINKS, _params, _terrain


def _build(link: dict):
    params  = _params(link["input"])
    terrain = _terrain(link, params)
    result  = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
    return params, terrain, result


def _cases(known: dict[str, str]) -> list:
    """全回線を並べ、既知の反例だけに課題 ID つきの strict な xfail を付ける。"""
    ids = {link["id"] for link in LINKS}
    stale = set(known) - ids
    assert not stale, f"既知の反例の印が、コーパスに無い回線を指している: {sorted(stale)}"
    return [
        pytest.param(
            link, id=link["id"],
            marks=([pytest.mark.xfail(strict=True, reason=known[link["id"]])]
                   if link["id"] in known else []),
        )
        for link in LINKS
    ]


# ============================================================
# 地面反射の包絡線（core/ground_reflection.py）
# ============================================================
#: 前提「直接波と反射波がどちらも地形に遮られない」が成り立たないのに、
#: 包絡線が出ている回線（2026-10-12 に数えた）。B-338 を直すと xpass になる。
_ENVELOPE_ON_BLOCKED_PATH = {
    "nagoya_grazing_900":         "B-338",
    "short_veg_low_antenna_high": "B-338",
}


class TestTwoRayEnvelopePreconditions:

    @pytest.mark.parametrize("link", _cases(_ENVELOPE_ON_BLOCKED_PATH))
    def test_no_envelope_on_a_blocked_path(self, link):
        """🔴 **回折損 > 0 の回線では `compute_two_ray_envelope` が `None`** であること。

        2 波の干渉は直接波と反射波が両方届く経路の話＝回折損が乗る（直接波が
        地形に遮られる）経路で幅を出すと、前提の成り立たない数字を製品の
        感度表に載せ、1 本の回線を見る面では「地面反射は含まない」の開示まで外す。
        呼び方は本番（`sensitivity.compute_link_sensitivity`）と同じ引数。
        """
        params, terrain, result = _build(link)
        if result.diff_loss <= 0.0:
            return
        envelope = ground_reflection.compute_two_ray_envelope(
            terrain, params.h_tx, params.h_rx, params.freq_mhz)
        assert envelope is None, (
            f"遮られた経路（回折損 {result.diff_loss:.1f} dB）に包絡線が出た: "
            f"反射点 {envelope.frac:.2f}・下側 {envelope.null_depth_db:.1f} dB"
        )

    def test_the_corpus_actually_exercises_the_precondition(self):
        """🔴 **述語が空振りしていない**こと＝コーパスに両方の形が揃っている。

        「遮られていて、腕の長さ（`MIN_ARM_M`）では弾かれない回線」が 1 本も
        無ければ、上の述語は何も確かめていない。逆に「見通しで包絡線が出る回線」
        が無ければ、`None` を返すだけの実装でも緑になる。
        """
        blocked_long_arms = clear_with_envelope = 0
        for link in LINKS:
            params, terrain, result = _build(link)
            d1, _ = ground_reflection.specular_point(
                float(terrain.horiz_dist_km) * 1000.0, params.h_tx, params.h_rx)
            d2 = float(terrain.horiz_dist_km) * 1000.0 - d1
            long_arms = min(d1, d2) >= ground_reflection.MIN_ARM_M
            if result.diff_loss > 0.0 and long_arms:
                blocked_long_arms += 1
            envelope = ground_reflection.compute_two_ray_envelope(
                terrain, params.h_tx, params.h_rx, params.freq_mhz)
            if result.diff_loss <= 0.0 and envelope is not None:
                clear_with_envelope += 1
        assert blocked_long_arms >= 1, "遮られていて腕の長い回線がコーパスに無い"
        assert clear_with_envelope >= 1, "見通しで包絡線が出る回線がコーパスに無い"
