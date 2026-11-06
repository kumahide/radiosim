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

from core import assumptions, calc_profile, ground_reflection, sensitivity
from core import simulation as sim
from tests.test_golden_links import LINKS, _params, _terrain


def _build(link: dict, profile: str = calc_profile.V1):
    """コーパスの回線を組む。

    🔑 **地形はコーパスの標本の位置で凍結し、方式だけ `profile` で通す**＝コーパスの標高は
    v1 の置き方で取ってある（v2 の位置で組み直すと標高の並びと位置が食い違う）。
    前提の述語が見るのは「方式が遮蔽をどう扱うか」なので、地形は同じでよい。
    """
    params  = _params(link["input"])
    terrain = _terrain(link, params)
    params.calc_profile = calc_profile.require(profile)
    result  = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
    return params, terrain, result


def _envelope(params, terrain, result):
    """本番の口（`sensitivity.compute_link_sensitivity`）が出す包絡線＝帳票 4 面と同じ呼び方。"""
    return sensitivity.compute_link_sensitivity(
        terrain, params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx,
        params.h_tx, params.h_rx, params.freq_mhz, params.veg_h, params.k_factor,
        result.diff_method, result.env_type, params.rain_rate,
        params.p_tx, params.gain_tx, params.gain_rx, params.sens,
        profile=params.calc_profile,
    ).ground_reflection


def _cases(known: dict[str, dict[str, str]]) -> list:
    """全回線×全方式を並べ、既知の反例だけに課題 ID つきの strict な xfail を付ける。

    `known` は方式 → {回線 ID: 課題 ID}。
    """
    ids = {link["id"] for link in LINKS}
    for profile, per_link in known.items():
        calc_profile.require(profile)
        stale = set(per_link) - ids
        assert not stale, f"既知の反例の印が、コーパスに無い回線を指している（{profile}）: {sorted(stale)}"
    return [
        pytest.param(
            link, profile, id=f"{profile}-{link['id']}",
            marks=([pytest.mark.xfail(strict=True, reason=known[profile][link["id"]])]
                   if link["id"] in known.get(profile, {}) else []),
        )
        for profile in calc_profile.PROFILES
        for link in LINKS
    ]


# ============================================================
# 地面反射の包絡線（core/ground_reflection.py）
# ============================================================
#: 前提「直接波と反射波がどちらも地形に遮られない」が成り立たないのに、
#: 包絡線が出ている回線（2026-10-12 に数えた）。
#: 🔑 **v1 の印は外さない**＝旧プロジェクトを 3.9.x の数字で開くための旧方式の再現
#: （B-338 は v2 の側だけで直した・2026-11-07）。v2 は印なし＝新しい反例は普通の赤。
_ENVELOPE_ON_BLOCKED_PATH = {
    calc_profile.V1: {
        "nagoya_grazing_900":         "B-338（v1＝3.9.x の方式の再現）",
        "short_veg_low_antenna_high": "B-338（v1＝3.9.x の方式の再現）",
    },
    # v2＝印なし（2026-11-07 に印を付けて走らせ、2 本とも xpass の赤を見てから外した）。
}


class TestTwoRayEnvelopePreconditions:

    @pytest.mark.parametrize(("link", "profile"), _cases(_ENVELOPE_ON_BLOCKED_PATH))
    def test_no_envelope_on_a_blocked_path(self, link, profile):
        """🔴 **回折損 > 0 の回線では、本番の口が包絡線を出さない**こと。

        2 波の干渉は直接波と反射波が両方届く経路の話＝回折損が乗る（直接波が
        地形に遮られる）経路で幅を出すと、前提の成り立たない数字を製品の
        感度表に載せ、1 本の回線を見る面では「地面反射は含まない」の開示まで外す。
        呼ぶのは本番の口（`sensitivity.compute_link_sensitivity`）＝帳票 4 面と同じ。
        """
        params, terrain, result = _build(link, profile)
        if result.diff_loss <= 0.0:
            return
        envelope = _envelope(params, terrain, result)
        assert envelope is None, (
            f"遮られた経路（回折損 {result.diff_loss:.1f} dB）に包絡線が出た（{profile}）: "
            f"反射点 {envelope.frac:.2f}・下側 {envelope.null_depth_db:.1f} dB"
        )

    @pytest.mark.parametrize(("link", "profile"), _cases({}))
    def test_v2_keeps_the_envelope_on_a_clear_path(self, link, profile):
        """🔴 **見通しの回線（回折損 0）では、v1 と v2 が同じ包絡線を出す**こと。

        B-338 の直しは遮られた経路だけを外す＝見通しの経路の幅まで消すと、
        `None` を返すだけの実装でも上の試験が緑になる。
        """
        params, terrain, result = _build(link, profile)
        if result.diff_loss > 0.0:
            return
        got  = _envelope(params, terrain, result)
        bare = ground_reflection.compute_two_ray_envelope(
            terrain, params.h_tx, params.h_rx, params.freq_mhz)
        assert got == bare

    def test_the_declaration_follows_the_default_profile(self):
        """🔴 **宣言 `both_rays_unobstructed` の扱いが、製品の既定の方式と合っている**こと。

        既定が v1 の間は、製品は遮られた経路にも包絡線を出す＝`OPEN` と名乗る。
        既定を v2 へ切り替えたら、入口（`envelope_for_link`）で確かめる＝`GUARD` へ移す。
        切り替えのコミットで宣言を直し忘れると、ここが赤になる（B-338）。
        """
        decl = {a.name: a for a in ground_reflection.ASSUMPTIONS}["both_rays_unobstructed"]
        if calc_profile.two_ray_needs_clear_path(calc_profile.DEFAULT_PROFILE):
            assert decl.handling == assumptions.GUARD
            assert decl.enforced_by == ("envelope_for_link",)
        else:
            assert decl.handling == assumptions.OPEN
            assert decl.issue == "B-338"

    def test_v2_drops_the_envelope_only_on_the_known_blocked_links(self):
        """🔴 **v1→v2 で包絡線が消える回線＝v1 の既知の反例と同じ集合**であること。

        B-338 の v1→v2 の差（台帳の B-338）を数で固定する＝式や閾値が動いて、
        消える回線が増減したら赤にして台帳の数え直しを求める。
        """
        dropped = set()
        for link in LINKS:
            env = {p: _envelope(*_build(link, p)) for p in calc_profile.PROFILES}
            if env[calc_profile.V1] is not None and env[calc_profile.V2] is None:
                dropped.add(link["id"])
        assert dropped == set(_ENVELOPE_ON_BLOCKED_PATH[calc_profile.V1])

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
