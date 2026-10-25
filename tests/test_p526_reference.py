"""
tests/test_p526_reference.py
============================
**規格例照合コーパス**＝製品の回折の式を、ITU-R P.526 の値と突き合わせる
（4.0 ステージ1・判断点 ⑰・作業記録 `.claude/org/work_log.md` の O-008）。

🔑 **自己回帰でない検証**＝ゴールデン（`test_golden_links.py`）は「昨日の製品と同じ答えか」
しか言えない。ここは**製品の外で決まった値**（数値積分の厳密値・規格の手順で手で出した値）と
比べるので、ゴールデンを作った日に既に間違っていた式も捕まえる。

範囲（2026-10-24 に本人が承認）＝**製品が実際に使う回折の式だけ**:
  ① J(ν) の近似式（P.526 §4.1）を Fresnel 積分の数値積分の厳密値と比べる（11 点）
  ② 単一ナイフエッジ（`single`）の ν の幾何を手計算の例 3 本と比べる
  ③ Bullington（P.526 §4.5.1）を手計算できる合成断面 4 本と比べる
外したもの＝球面回折（`_spherical_earth_loss` は席＝常に 0）・地面反射・植生・降雨（P.838）・
大気（P.676）・環境損失（勧告の式ではない）。

出典の表（1 件ずつ）:

  | 項 | 比べる相手 | 出典 | 許容差と根拠 |
  |---|---|---|---|
  | ① | J(ν) の厳密値 | P.526 §4.1 の定義（Fresnel 積分 C・S による式）を numpy の Simpson で積分。積分器は C(1)・S(1) の既知値（Abramowitz & Stegun 表 7.7）で確かめる | **0.15 dB**＝近似式そのものの誤差が最大 0.123 dB（ν＝4.58）。典型の書き損じ（6.9→6.0・0.1→0.2・0.1→0）はどれも 0.84 dB 以上ずれる＝捕まえられる（下の変異の試験） |
  | ② | ν と J(ν) | P.526 §4.1 の ν の式と J(ν) の近似式を手で当てた値（導出は表の各行） | **0.01 dB**＝表の丸め（ν 5 桁・dB 4 桁）より上・書き損じ（地球の丸みの落とし・d1/d2 の取り違え）は 0.1 dB 以上 |
  | ③ | 補正後の Bullington 損 | P.526 §4.5.1 の手順（Stim・Str・Srim・交点・νb・補正項）を手で当てた値 | **0.01 dB**＝同上 |

🌐 **ITU の検証例との照合は探針で回す**（本人の決定 2026-10-24＝断面データ〔ライセンスの表示が
無い〕はリポジトリに置かない）＝`experiments/p526_delta_bullington_itu_probe.py`。
2026-10-24 の照合＝検証例 20 ケースの中間値 `Lba` と製品の差は**最大 0.0031 dB**（全部が光速の丸め）。

⚠️ **地球の等価半径**＝②③は製品の既定（6371 km × 4/3）で手計算する。ITU の検証例は 8500 km。
"""

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import models

_C = 299792458.0                     # 製品と同じ光速（`calculate_propagation`）
_AE = 6371000.0 * models.EARTH_K_STANDARD   # 製品の既定の等価地球半径 [m]


# ================================================================
# ① J(ν) と数値積分の厳密値
# ================================================================
def _fresnel_cs(nu: float, n: int = 400001) -> tuple[float, float]:
    """Fresnel 積分 C(ν)・S(ν)＝∫0^ν cos/sin(πt²/2) dt を合成 Simpson で（numpy だけ）。

    ⚠️ ν＝10 で被積分関数の周期は約 0.1＝刻み 2.5e-5 なら 1 周期に 4000 点。
    """
    t = np.linspace(0.0, nu, n)
    h = nu / (n - 1)
    w = np.ones(n)
    w[1:-1:2] = 4.0
    w[2:-1:2] = 2.0
    phase = np.pi * t * t / 2.0
    return (float(h / 3.0 * np.sum(w * np.cos(phase))),
            float(h / 3.0 * np.sum(w * np.sin(phase))))


def _j_exact(nu: float) -> float:
    """P.526 §4.1 の J(ν) の定義＝−20 log10( √((1−C−S)² + (C−S)²) / 2 )。"""
    c, s = _fresnel_cs(nu)
    return -20.0 * math.log10(math.hypot(1.0 - c - s, c - s) / 2.0)


def _j_formula(nu: float, a: float = 6.9, b: float = 0.1) -> float:
    """P.526 §4.1 の近似式（ν > −0.78）。`a`・`b` は**変異の試験だけ**が動かす。"""
    return a + 20.0 * math.log10(math.sqrt((nu - b) ** 2 + 1.0) + nu - b)


#: 比べる ν（−0.78 の縁の内側から 10 まで）。4.58 と −0.56 は近似式の誤差の山
#: （0.02 刻みで掃いた最大＝作業記録 O-008）。
_NU_POINTS = (-0.7, -0.56, -0.3, 0.0, 0.5, 1.0, 2.0, 3.0, 4.58, 7.0, 10.0)
_J_TOL_DB = 0.15


class TestJnuAgainstExactIntegral:
    def test_the_integrator_reproduces_known_fresnel_values(self):
        """物差しの検査＝C(1)・S(1) が既知値（A&S 表 7.7）と 7 桁一致し、J(0) が 20 log10 2。"""
        c, s = _fresnel_cs(1.0)
        assert c == pytest.approx(0.7798934004, abs=1e-8)
        assert s == pytest.approx(0.4382591474, abs=1e-8)
        assert _j_exact(0.0) == pytest.approx(20 * math.log10(2.0), abs=1e-9)

    @pytest.mark.parametrize("nu", _NU_POINTS)
    def test_product_formula_is_within_tolerance_of_exact(self, nu):
        got = models._diffraction_loss_fk(nu)
        want = _j_exact(nu)
        assert abs(got - want) <= _J_TOL_DB, (
            f"ν={nu}: 製品 {got:.4f} dB／厳密値 {want:.4f} dB（差 {got - want:+.4f}）"
        )

    def test_the_formula_in_this_file_is_the_product_formula(self):
        """変異の試験が製品と同じ式を壊していることの前提（ずれたら変異が的外れ）。"""
        for nu in _NU_POINTS:
            assert _j_formula(nu) == pytest.approx(models._diffraction_loss_fk(nu), abs=1e-12)

    @pytest.mark.parametrize("a, b, label", [
        (6.0, 0.1, "定数 6.9→6.0"),
        (6.9, 0.2, "ずらし 0.1→0.2"),
        (6.9, 0.0, "ずらし 0.1→0"),
    ])
    def test_tolerance_still_catches_typical_miswriting(self, a, b, label):
        """許容差 0.15 dB が**緩すぎない**こと＝典型の書き損じは 11 点のどこかで外へ出る。"""
        worst = max(abs(_j_formula(nu, a, b) - _j_exact(nu)) for nu in _NU_POINTS)
        assert worst > 4 * _J_TOL_DB, f"{label}: 最大のずれ {worst:.3f} dB＝許容差で見逃す"


# ================================================================
# 断面の組み立て（②③共通）＝赤道上を東へ・等間隔 201 点・尖りは 1 標本
# ================================================================
_N = 201


def _run(dist_m: float, peaks: list[tuple[float, float]], h_tx: float,
         h_rx: float, freq_mhz: float, method: str) -> float:
    """製品の入口（`calculate_terrain_profile`→`calculate_propagation`・植生 0）の回折損。"""
    raw = np.zeros(_N)
    for frac, h in peaks:
        raw[round(frac * (_N - 1))] = h
    dlon = math.degrees(dist_m / 6371000.0)      # 赤道上の haversine は R·Δλ
    terr = models.calculate_terrain_profile(raw, 0.0, 0.0, 0.0, dlon)
    assert terr.horiz_dist_km * 1000.0 == pytest.approx(dist_m, abs=1e-6)
    return models.calculate_propagation(
        terr, h_tx, h_rx, freq_mhz, 0.0, 10.0, diff_method=method,
    ).diff_loss


def _bulge(d1: float, dist: float) -> float:
    """地球の丸みで障害物が持ち上がる量 d1·d2 / (2 ae) [m]。"""
    return d1 * (dist - d1) / (2.0 * _AE)


# ================================================================
# ② 単一ナイフエッジの手計算（`single`）
# ================================================================
#: (名前, D [m], d1 [m], 尖りの標高 [m], h_tx, h_rx, f [MHz], ν, J [dB])
#: ν = (尖り + d1·d2/2ae − LoS) · √(2D / (λ·d1·d2))・J は §4.1 の近似式
_SINGLE_CASES = [
    # 2 km・中央 25 m・900 MHz＝15.0589 m 上に出る（丸み 0.0589 m を含む）
    ("center_900", 2000.0, 1000.0, 25.0, 10.0, 10.0, 900.0, 1.65019, 17.5166),
    # 5 km・1 km 地点・2400 MHz＝LoS 22 m の 3.5 m 下（F1 に食い込む＝ν<0 でも損失）
    ("grazing_2400", 5000.0, 1000.0, 18.2, 20.0, 30.0, 2400.0, -0.50428, 1.9274),
    # 10 km・7.5 km 地点・150 MHz＝傾いた LoS（30→15 m）と丸み 1.1036 m
    ("asym_150", 10000.0, 7500.0, 61.0, 30.0, 15.0, 150.0, 1.00156, 13.9358),
]


class TestSingleKnifeEdgeByHand:
    @pytest.mark.parametrize("name, dist, d1, peak, htx, hrx, f, nu, j", _SINGLE_CASES)
    def test_hand_nu_matches_the_table(self, name, dist, d1, peak, htx, hrx, f, nu, j):
        """表の ν が導出どおりか（表を書き換えて物差しを動かしていないことの見張り）。"""
        lam = _C / (f * 1e6)
        d2 = dist - d1
        los = htx + (hrx - htx) * d1 / dist
        h = peak + _bulge(d1, dist) - los
        assert h * math.sqrt(2.0 * dist / (lam * d1 * d2)) == pytest.approx(nu, abs=1e-5)
        assert _j_formula(nu) == pytest.approx(j, abs=1e-3)

    @pytest.mark.parametrize("name, dist, d1, peak, htx, hrx, f, nu, j", _SINGLE_CASES)
    def test_product_matches_hand_value(self, name, dist, d1, peak, htx, hrx, f, nu, j):
        got = _run(dist, [(d1 / dist, peak)], htx, hrx, f, models.DIFF_METHOD_SINGLE)
        assert got == pytest.approx(j, abs=0.01), f"{name}: 製品 {got:.4f}／手計算 {j}"


# ================================================================
# ③ Bullington の手計算（P.526 §4.5.1・合成断面）
# ================================================================
def _bullington_by_hand(dist, peaks, hts, hrs, f):
    """§4.5.1 の手順を**尖りの標本だけ**で当てる（平地の標本は接線を決めない）。

    戻り＝(νb, Luc, Lb)。距離は m（規格の km の式を m に直した）。
    """
    lam = _C / (f * 1e6)
    pts = [(fr * dist, h + _bulge(fr * dist, dist)) for fr, h in peaks]
    stim = max((h - hts) / x for x, h in pts)            # TX から見た最大の傾き
    s_tr = (hrs - hts) / dist                            # TX→RX の直線の傾き
    if stim < s_tr:                                      # 見通し＝最大 ν の点
        nu = max((h - (hts * (dist - x) + hrs * x) / dist)
                 * math.sqrt(2.0 * dist / (lam * x * (dist - x))) for x, h in pts)
    else:                                                # 遮蔽＝2 本の接線の交点
        srim = max((h - hrs) / (dist - x) for x, h in pts)
        dbp = (hrs - hts + srim * dist) / (stim + srim)
        nu = ((hts + stim * dbp - (hts * (dist - dbp) + hrs * dbp) / dist)
              * math.sqrt(2.0 * dist / (lam * dbp * (dist - dbp))))
    luc = max(_j_formula(nu), 0.0) if nu > -0.78 else 0.0
    return nu, luc, luc + (1.0 - math.exp(-luc / 6.0)) * (10.0 + 0.02 * dist / 1000.0)


#: (名前, D [m], 尖り [(位置の割合, 標高 m)], h_ts, h_rs, f [MHz], νb, Lb [dB])
_BULLINGTON_CASES = [
    # 平地・30 m/30 m・10 km・900 MHz＝中央の丸み 2.94 m は F1（28.9 m）の外＝0
    ("clear_flat", 10000.0, [], 30.0, 30.0, 900.0, None, 0.0),
    # 4 km 地点の 20 m の丘が LoS（30 m）の下＝見通しの枝・F1 に食い込む分だけ損失
    ("los_partial", 10000.0, [(0.4, 20.0)], 30.0, 30.0, 900.0, -0.42952, 5.9594),
    # 1 峰＝Stim・Srim とも同じ峰が決める→交点は峰の位置（dbp = 4000 m）
    ("one_peak", 10000.0, [(0.4, 60.0)], 20.0, 20.0, 900.0, 2.07135, 29.1203),
    # 2 峰＝TX 側は 3 km の 60 m（Stim 0.017079）・RX 側は 8 km の 45 m（Srim 0.012971）
    #      → 交点 dbp = 4649.3 m（どちらの峰の上でもない）
    ("two_peaks", 10000.0, [(0.3, 60.0), (0.8, 45.0)], 10.0, 20.0, 900.0, 3.67251, 34.1631),
]


class TestBullingtonByHand:
    @pytest.mark.parametrize("name, dist, peaks, hts, hrs, f, nu, lb",
                             [c for c in _BULLINGTON_CASES if c[1] and c[2]])
    def test_hand_procedure_matches_the_table(self, name, dist, peaks, hts, hrs, f, nu, lb):
        got_nu, _, got_lb = _bullington_by_hand(dist, peaks, hts, hrs, f)
        assert got_nu == pytest.approx(nu, abs=1e-5)
        assert got_lb == pytest.approx(lb, abs=1e-3)

    @pytest.mark.parametrize("name, dist, peaks, hts, hrs, f, nu, lb", _BULLINGTON_CASES)
    def test_product_matches_hand_value(self, name, dist, peaks, hts, hrs, f, nu, lb):
        got = _run(dist, peaks, hts, hrs, f, models.DIFF_METHOD_MULTI)
        assert got == pytest.approx(lb, abs=0.01), f"{name}: 製品 {got:.4f}／手計算 {lb}"

    def test_two_peak_breakpoint_lies_between_the_peaks(self):
        """2 峰の例が本当に「交点」を試していること（峰の上に落ちたら 1 峰と同じ検査）。"""
        hts, hrs, dist = 10.0, 20.0, 10000.0
        x1, h1 = 3000.0, 60.0 + _bulge(3000.0, dist)
        x2, h2 = 8000.0, 45.0 + _bulge(8000.0, dist)
        stim, srim = (h1 - hts) / x1, (h2 - hrs) / (dist - x2)
        dbp = (hrs - hts + srim * dist) / (stim + srim)
        assert x1 + 500.0 < dbp < x2 - 500.0
