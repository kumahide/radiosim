"""
core/verdict.py
===============
判定の口（4.0 ステージ6・判定契約 v2）＝回線 1 本の判定の語（OK／NG／REVIEW／ERROR）を
作るのは**ここだけ**。`simulation.run_calculation` が呼び、`LinkBudgetResult.status` と
`.verdict` へ載せる＝単一・バッチ・条件探索・中継の各区間は `status` を読むだけで同じ語になる。

🔑 **語の意味**（判断点 ①・2026-10-23／着手で決めたこと・2026-11-07）
- **OK**＝評価した前提の範囲（下の振り幅）で、どの組み合わせでも余裕度 ≥ 0。
  「評価した前提の範囲で成立」であって、振っていない前提（解像度〔B-231〕・DEM 標高 ±3 m
  〔B-232〕・地面反射の適用外）まで保証するものではない＝開示で名乗る。
- **NG**＝どの組み合わせでも余裕度 < 0。
- **REVIEW**＝前提の取り方で裏返る（**両向き**＝基準 OK で最悪の組が NG・基準 NG で最良の組が
  OK）＝現地確認。幅は「誤差」でなく**前提依存性**（語の正典は `docs/glossary.md`）。
- **ERROR**＝判定の材料が欠けた（v2 では DEM の通信の失敗の標本が 1 つでもある）。
  集約では別扱い（`aggregate`）。

振り幅＝植生高 ×3（`sensitivity.VEG_PERTURB_FRAC`・植生 0 なら振らない）×環境区分 ±1 段×
回折の方式 2×回折と植生の合成 2（和⇄大きいほう）の**全組み合わせ**（最大 36 通り・I-235）＋
地面反射の包絡線（各組の余裕度 − ヌルの深さ〜＋建設的側）＋無効画素（`dem.ElevStatus.
INVALID_PIXEL`）を前後の有効な標本の直線内挿で埋め直した 1 回（I-247）。

⚠️ **v1 は 3.9 のまま OK／NG**（`calc_profile.reviews_assumptions`）＝旧プロジェクトを当時の
方式で開く約束（判断点 ⑨）。
⚠️ 404（海上・日本域外）は 0 m のまま使い、点数を開示で名乗る（判定は動かさない）。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np

from core import calc_profile, dem, ground_reflection, models
from core.sensitivity import VEG_PERTURB_FRAC

OK: str = "OK"
NG: str = "NG"
REVIEW: str = "REVIEW"
ERROR: str = "ERROR"

#: 判定の語（表示と件数の並び順）。
WORDS: tuple[str, ...] = (OK, REVIEW, NG, ERROR)

#: 集約の重さ（NG＞REVIEW＞OK）。ERROR は別扱い＝`aggregate` が先に見る。
_SEVERITY: dict[str, int] = {OK: 0, REVIEW: 1, NG: 2}

#: 裏返る前提の名前（`Verdict.flipped_by` の値・並びはこの順）。
#: 最初の 4 つは `sensitivity.compute_link_sensitivity` の軸と同じ名前。
AXES: tuple[str, ...] = (
    "veg_h", "env_type", "diff_method", "diff_veg_compose",
    "ground_reflection", "dem_invalid_pixel",
)


@dataclass(frozen=True)
class Verdict:
    """回線 1 本の判定（語・余裕度の幅・裏返る前提・DEM の事情の点数）。"""
    word:              str
    base_margin:       float                # 基準の余裕度 [dB]（`actual_margin` と同じ値）
    worst_margin:      "float | None" = None  # 振り幅の中の最悪 [dB]（v1・ERROR は None）
    best_margin:       "float | None" = None  # 振り幅の中の最良 [dB]（同上）
    flipped_by:        tuple[str, ...] = ()   # 裏返る前提（`AXES` の名前・REVIEW のときだけ）
    combinations:      int = 0               # 計算した組の数（地面反射・埋め直しを含まない）
    dem_no_tile:       int = 0               # 404（全層でタイルが無い）の標本の数
    dem_invalid_pixel: int = 0               # 無効画素の標本の数
    dem_fetch_failed:  int = 0               # 通信の失敗の標本の数


def word_for_margin(margin: float) -> str:
    """余裕度 1 つから語を作る（基準の判定・v1 の判定）。"""
    return OK if margin >= 0 else NG


def aggregate(words) -> str:
    """作業面の集約（各経路・各条件・各区間 → 全体）＝NG＞REVIEW＞OK。

    ⚠️ **ERROR が 1 つでもあれば ERROR**・**空も ERROR**（判定の材料が無いことを NG と
    言い切らない＝中継の `overall_status` と同じ規則）。
    """
    words = list(words)
    if not words or ERROR in words:
        return ERROR
    return max(words, key=lambda w: _SEVERITY[w])


def counts(words) -> dict[str, int]:
    """語ごとの件数（`WORDS` の全部の鍵を持つ＝0 件も 0 と言う）。"""
    out = dict.fromkeys(WORDS, 0)
    for w in words:
        out[w] += 1
    return out


def _status_counts(status: "np.ndarray | None") -> tuple[int, int, int]:
    """(404, 無効画素, 通信の失敗) の標本の数。"""
    if status is None:
        return 0, 0, 0
    s = np.asarray(status)
    return tuple(int((s == int(k)).sum()) for k in (   # type: ignore[return-value]
        dem.ElevStatus.NO_TILE, dem.ElevStatus.INVALID_PIXEL, dem.ElevStatus.FETCH_FAILED))


def fill_invalid_pixels(raw: np.ndarray, status: "np.ndarray | None") -> "np.ndarray | None":
    """無効画素の標本を、前後の有効な標本（`VALUE`）の直線内挿で埋め直す。

    埋める標本が無い・有効な標本が 2 つに満たないときは None（埋め直しの計算をしない）。
    404 の標本（0 m のまま使う）は埋めない。
    """
    if status is None:
        return None
    raw = np.asarray(raw, dtype=float)
    s = np.asarray(status)
    bad = s == int(dem.ElevStatus.INVALID_PIXEL)
    good = (s == int(dem.ElevStatus.VALUE)) & ~np.isnan(raw)
    if not bad.any() or int(good.sum()) < 2:
        return None
    idx = np.arange(len(raw))
    filled = raw.copy()
    filled[bad] = np.interp(idx[bad], idx[good], raw[good])
    return filled


def _margins(terrain, h_tx, h_rx, p, rain_rate, *, veg_h, env_type, diff_method):
    """1 回まわして（和の余裕度, 大きいほうの合成の余裕度, 回折損）を返す。

    🔑 合成の軸は再計算しない＝`sensitivity.compute_link_sensitivity` と同じ後処理の式。
    """
    prop = models.calculate_propagation(
        terrain, h_tx, h_rx, p.freq_mhz, veg_h, p.k_factor, diff_method, env_type,
        rain_rate, profile=p.calc_profile)
    r = models.calculate_link_budget(prop, p.freq_mhz, p.p_tx, p.gain_tx, p.gain_rx, p.sens)
    total_max = (r.fspl + max(prop.diff_loss, prop.veg_loss)
                 + prop.env_loss + prop.rain_loss + prop.gas_loss)
    return r.actual_margin, r.eirp + p.gain_rx - total_max - p.sens, prop.diff_loss


def judge(terrain: models.TerrainProfile, h_tx: float, h_rx: float, params,
          rain_rate: float, base_margin: float) -> Verdict:
    """回線 1 本の判定を作る（`simulation.run_calculation` が呼ぶ）。

    Args:
        params:      `simulation.SimParams`（計算を決める欄・`calc_profile`・端点の緯度経度）。
        rain_rate:   計算に使った降雨強度（グラフのスライダーが params と別に渡す）。
        base_margin: 基準の余裕度（呼び手が計算済みの `actual_margin`）。
    """
    no_tile, invalid, failed = _status_counts(terrain.dem_status)
    base_word = word_for_margin(base_margin)
    if not calc_profile.reviews_assumptions(params.calc_profile):
        return Verdict(base_word, base_margin, dem_no_tile=no_tile,
                       dem_invalid_pixel=invalid, dem_fetch_failed=failed)
    raw = np.asarray(terrain.raw_elevs, dtype=float)
    if failed or bool(np.isnan(raw).any()):
        return Verdict(ERROR, base_margin, dem_no_tile=no_tile,
                       dem_invalid_pixel=invalid, dem_fetch_failed=failed)

    veg = params.veg_h
    vegs = [veg] if veg <= 0.0 else [veg * (1 - VEG_PERTURB_FRAC), veg, veg * (1 + VEG_PERTURB_FRAC)]
    env = params.env_type if params.env_type in models.ENV_COEFFS else models.ENV_DEFAULT
    i = models.ENV_KEYS.index(env)
    envs = [models.ENV_KEYS[j] for j in (i - 1, i, i + 1) if 0 <= j < len(models.ENV_KEYS)]
    base_diff = models.normalize_diff_method(params.diff_method)
    diffs = [models.DIFF_METHOD_SINGLE, models.DIFF_METHOD_MULTI]

    # (変えた前提の集合, 余裕度) を集める。
    cases: list[tuple[frozenset, float]] = []
    base_diff_loss = 0.0
    for v, e, d in itertools.product(vegs, envs, diffs):
        changed = {name for name, moved in (
            ("veg_h", v != veg), ("env_type", e != env), ("diff_method", d != base_diff)) if moved}
        m_sum, m_max, diff_loss = _margins(terrain, h_tx, h_rx, params, rain_rate,
                                           veg_h=v, env_type=e, diff_method=d)
        if not changed:
            base_diff_loss = diff_loss
        cases.append((frozenset(changed), m_sum))
        cases.append((frozenset(changed | {"diff_veg_compose"}), m_max))
    n_combos = len(cases)

    # 地面反射の包絡線（v2 は遮られた経路で出さない＝B-338）。
    envelope = ground_reflection.envelope_for_link(
        terrain, h_tx, h_rx, params.freq_mhz, diff_loss_db=base_diff_loss, profile=params.calc_profile)
    if envelope is not None:
        cases += [(c | {"ground_reflection"}, m + dm) for c, m in list(cases)
                  for dm in (-envelope.null_depth_db, envelope.constructive_gain_db)]

    filled = fill_invalid_pixels(raw, terrain.dem_status)
    if filled is not None:
        t2 = models.calculate_terrain_profile(
            raw_elevs=filled, lat_tx=params.lat_tx, lon_tx=params.lon_tx,
            lat_rx=params.lat_rx, lon_rx=params.lon_rx,
            earth_k=terrain.earth_k, frac_axis=terrain.frac_axis)
        m_fill, _, _ = _margins(t2, h_tx, h_rx, params, rain_rate,
                                veg_h=veg, env_type=env, diff_method=base_diff)
        cases.append((frozenset({"dem_invalid_pixel"}), m_fill))

    margins = [m for _, m in cases] + [base_margin]
    flips = [c for c, m in cases if word_for_margin(m) != base_word]
    flipped_by: tuple[str, ...] = ()
    if flips:
        # 🔑 裏返る前提＝最も少ない数の前提を変えて裏返る組に出てくる前提（全組の和集合に
        # すると、どれか 1 つで裏返る回線ではいつも全部の名前が並ぶ）。
        k = min(len(c) for c in flips)
        names = set().union(*(c for c in flips if len(c) == k))
        flipped_by = tuple(a for a in AXES if a in names)
    return Verdict(
        REVIEW if flips else base_word, base_margin,
        worst_margin=min(margins), best_margin=max(margins), flipped_by=flipped_by,
        combinations=n_combos, dem_no_tile=no_tile, dem_invalid_pixel=invalid,
        dem_fetch_failed=failed)
