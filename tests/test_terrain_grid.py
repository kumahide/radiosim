"""
tests/test_terrain_grid.py
==========================
地形の解像度（I-069）＝**段階から点数を解く**層のゲート。

守っているのは 4 つ：
  1. **段階が段階として効く**（高 < 中 < 低 の順に細かい・実務の距離帯で天井に
     張り付かない）。⇒ 旧上限 2000 の失敗そのものの再発防止。
  2. **点数の解き方が 1 か所しかない**（画面と実行が同じ口を通る）。
  3. **数で入れる口が復活しない**（入力は段階だけ）。
  4. **固定 N の互換の口は段階を名乗らない**（B-137）＝帳票・保存・刻印が
     *使っていない段階*を名乗らないこと。
"""

import ast
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import config              # noqa: E402
from core import i18n                # noqa: E402
from core import models              # noqa: E402
from core import simulation as sim   # noqa: E402
from core import terrain_grid as tg  # noqa: E402
from core import units               # noqa: E402
from views import frozen_common      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============================================================
# 1. 段階が段階として効く
# ============================================================
# 実務の距離帯（ISSUES.md I-069 の実測＝コーパス 26 本は 2〜48km）。
_PRACTICAL_KM = [0.5, 1, 2, 5, 10, 20, 30, 48]


def _north_path(km: float, lat: float = 34.54, lon: float = 132.41):
    """真北へ `km` の経路（点数の比較用＝方位の効きは §5 が見る）。"""
    return (lat, lon, lat + km / 111.32, lon)


def _n(km: float, level: str, lat: float = 34.54) -> int:
    return len(tg.path_sample_fractions(*_north_path(km, lat), level))


@pytest.mark.parametrize("km", _PRACTICAL_KM)
def test_finer_level_never_gives_fewer_points(km):
    """同じ経路なら 高 ≥ 中 ≥ 低 の点数になる（段階の向きが逆転しない）。"""
    high, med, low = (_n(km, k) for k in ("high", "medium", "low"))
    assert high >= med >= low, f"{km}km で段階の順序が壊れている: {high}/{med}/{low}"


@pytest.mark.parametrize("km", _PRACTICAL_KM)
@pytest.mark.parametrize("level", ["high", "medium"])
def test_the_level_actually_delivers_its_grid(km, level):
    """**その段階の画素 1 つにつき 2 点**が置かれている（＝天井に潰されていない）。

    🔴 **これが I-069 の芯**＝旧上限 2000 では、コーパス 26 本のうち 12 本で「高」が
    上限に張り付き、6 本は「高」と「中」が**同じ結果**、48km の 1 本は 3 段階すべてが
    同じ結果だった（2026-08-25 実測）。**利用者が選んだ段階が答えに出ない**なら、
    段階を選ばせる意味そのものが無い。⇒ 実務の距離帯では必ず届くことを固定する。

    🔴 **見る量が変わった**（B-150）＝以前は「実効間隔＝目標間隔」を見ていた。
    段階は等間隔で刻まなくなったので、**画素あたりの点数**で見る（縁が 2 つ）。
    """
    lat, lon, lat2, lon2 = _north_path(km)
    n = _n(km, level)
    px_m = tg.grid_step_m(lat, level)
    per_pixel = n / (km * 1000.0 / px_m)
    assert per_pixel == pytest.approx(2.0, rel=0.1), (
        f"{km}km / {level}: 画素 1 つあたり {per_pixel:.2f} 点"
        f"（点数 {n}・1px {px_m:.2f}m・天井 {tg.SAMPLES_CEILING} に"
        "張り付いていないか）"
    )


@pytest.mark.parametrize("km", _PRACTICAL_KM)
def test_the_coarse_level_stays_evenly_spaced(km):
    """「低」だけは**等間隔のまま**であること＝*粗いが速い*と名乗った段階。

    ⚠️ 対の検査（画素の縁で刻む 2 段階の裏）＝ここが無いと、3 段階を同じ刻み方に
    揃える直しが黙って通る（段階の意味の違いが消える）。
    """
    m = km * 1000.0
    n = _n(km, "low")
    assert tg.effective_spacing_m(m, n) == pytest.approx(
        tg.RESOLUTION_SPACING_M["low"], rel=0.05)
    assert not tg.samples_are_pixel_edges("low")


def test_the_ceiling_only_bites_at_absurd_distances():
    """天井が効き始めるのは**事故の距離**であること（実務の 48km では効かない）。

    天井を残すのは座標の打ち間違い（地球半周など）で点数が爆発しないため。
    ⇒ 「効かないなら外せ」ではなく「**実務では効かない高さに置く**」が答え。
    """
    assert _n(48.0, "high") < tg.SAMPLES_CEILING
    assert len(tg.path_sample_fractions(
        34.54, 132.41, -34.54, 132.41, "high")) == tg.SAMPLES_CEILING
    assert tg.recommended_samples(5_000_000, "high") == tg.SAMPLES_CEILING


@pytest.mark.parametrize("lat", [24.0, 34.54, 46.0])
@pytest.mark.parametrize("bearing", [0.0, 30.0, 45.0, 60.0, 90.0])
def test_a_hundred_km_still_fits_under_the_ceiling(lat, bearing):
    """**100km を「高」で刻んでも天井に当たらない**こと（B-148 の約束・B-150 で再計算）。

    🔴 **天井に当たると等間隔へ落ちる**＝そこから先は画素を飛ばす。つまり天井が低いと
    *直した不変条件が距離の先で黙って破れる*。⚠️ **斜めが最悪**（縦横の境界を両方
    跨ぐので通過画素が最大 √2 倍）＝真北・真東だけ見ると 1.4 倍ぶん見落とす。
    """
    n = len(tg.path_sample_fractions(
        lat, 141.0, *_endpoint(lat, 141.0, 100.0, bearing), "high"))
    assert n < tg.SAMPLES_CEILING, (
        f"緯度 {lat}° / 方位 {bearing}°: 100km で {n} 点＝天井 "
        f"{tg.SAMPLES_CEILING} に当たり、等間隔へ落ちている"
    )


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


def test_the_finest_level_is_finer_than_the_nominal_mesh():
    """いちばん細かい段階は **公称メッシュより細かい**こと（B-148）。

    🔴 **この検査は 2026-08-30 に向きが反転した**＝以前は「『高』の目標間隔＝
    `FINEST_MESH_M`（5m）」を固定しており、**欠陥そのものをゲートで固定していた**。
    公称 5m の層でも Web メルカトルの実 1px は日本で 3.3〜4.4m なので、
    公称で刻むと画素を飛ばす。⇒ 守るべきは**一致**ではなく**公称より細かい**こと。
    """
    assert tg.RESOLUTION_SPACING_M["high"] < tg.FINEST_MESH_M


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
        diff_method="bullington",
    )
    params = sim.SimParams(c)
    shown, spacing = sim.resolve_samples(
        params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx, "high")
    assert shown == params.num
    # ⚠️ 2 つめは**画素の寸法**（B-150）＝「高」「中」は等間隔で刻まない。
    assert spacing == pytest.approx(
        tg.grid_step_m((params.lat_tx + params.lat_rx) / 2.0, "high"))
    assert spacing == pytest.approx(sim.effective_spacing(params))


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


# ============================================================
# 4. 固定 N モード（互換の口）は段階を名乗らない — B-137 回帰ガード
# ============================================================
#
# **壊れた不変条件**＝「帳票・保存・刻印が名乗る段階は、その実行が実際に使った段階」。
# `samples` だけを渡す互換の口（回帰コーパスの生成器・探針）では段階を経由しないのに、
# 以前は `resolution` を既定値で埋めていたため、**使っていない段階を名乗っていた**。
def _fixed_n_config(n: str = "800") -> dict[str, str]:
    return dict(
        start="34.5429, 132.4118", end="34.5629, 132.4318",
        h_tx="30", h_rx="10", freq="2400", p_tx="20",
        gain_tx="3", gain_rx="3", sens="-85", veg_h="0", k_factor="10",
        samples=n, env_type="los", rain_rate="0.0", diff_method="bullington",
    )


def test_fixed_sample_count_does_not_claim_a_level():
    """固定 N で作った実行は、段階を**空**にすること（＝名乗らない）。"""
    p = sim.SimParams(_fixed_n_config())
    assert p.num == 800
    assert p.resolution == "", (
        "使っていない段階を名乗っている（帳票・保存・刻印がこの値を引く）"
    )


def test_a_level_run_still_claims_its_level():
    """**対の検査**＝段階を渡した実行は、いままでどおり名乗ること。

    ⚠️ これが無いと、上の検査は「段階を常に空にする」直しでも緑になる
    （B-128 の刻印が全部消えても気づけない）。
    """
    c = dict(_fixed_n_config(), resolution="high")
    del c["samples"]
    p = sim.SimParams(c)
    assert p.resolution == "high"
    assert p.num == sim.resolve_samples(
        p.lat_tx, p.lon_tx, p.lat_rx, p.lon_rx, "high")[0]


def test_the_stamp_is_silent_when_no_level_was_used():
    """段階を名乗らない実行では、解像度の刻印が**出ない**こと（B-128 と B-137 の境目）。"""
    p = sim.SimParams(_fixed_n_config())
    keys = models.scope_notes(p.freq_mhz, resolution=p.resolution)
    assert not [k for k in keys if k in models.SCOPE_RESOLUTION]

    p_level = sim.SimParams(dict(_fixed_n_config(), resolution="high"))
    keys_level = models.scope_notes(p_level.freq_mhz, resolution=p_level.resolution)
    assert [k for k in keys_level if k in models.SCOPE_RESOLUTION], (
        "段階を使った実行で刻印が消えている（B-128 の退行）"
    )


def test_the_effective_spacing_comes_from_the_samples_actually_used():
    """実効間隔は**実際に刻んだ点数**から出ること（段階から計算し直さない）。"""
    p = sim.SimParams(_fixed_n_config())
    dist_m = models.horizontal_distance_km(
        p.lat_tx, p.lon_tx, p.lat_rx, p.lon_rx) * units.KM_TO_M
    assert sim.effective_spacing(p) == pytest.approx(
        tg.effective_spacing_m(dist_m, p.num))
    # 段階から計算し直した値とは実際に食い違う（＝この検査が空振りでないこと）
    _, from_level = sim.resolve_samples(
        p.lat_tx, p.lon_tx, p.lat_rx, p.lon_rx, tg.RESOLUTION_DEFAULT)
    assert abs(sim.effective_spacing(p) - from_level) > 0.1


def test_a_fixed_n_run_round_trips_only_for_direct_callers(tmp_path):
    """保存した `settings.json` を **`SimParams` へ直に渡せば**同じ点数で再現すること。

    🔴 **製品の「パラメータ読込」では再現しない**（B-140＝B-137 の直しの申告が
    広すぎた）＝画面の読込は `config.select_sim` を通り、`samples` は `SIM_KEYS` に
    無いので捨てられる。**それは欠陥ではなく I-069 の決定**（数で入れる口は無い）＝
    だからこの検査は*直に組む側*（生成器・探針）だけを名乗る。下でそれも見る。
    """
    p = sim.SimParams(_fixed_n_config())
    sim._save_settings(p, 30.0, 10.0, str(tmp_path))
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))

    assert "resolution" not in saved, (
        "名乗れない段階を保存している（読み戻すと点数が別物になる）"
    )
    reloaded = sim.SimParams({
        **_fixed_n_config(), "samples": str(saved["samples"]),
        **({"resolution": saved["resolution"]} if "resolution" in saved else {}),
    })
    assert reloaded.num == p.num

    # 🔑 **製品経路は固定 N を運ばない**ことを、そう書いてある側で確かめる
    #    （代役が本物より寛容だと、その差の分だけ検査は空振りする）。
    assert "samples" not in config.select_sim(saved)


def test_the_band_does_not_invent_a_level_it_will_not_use():
    """帯は、段階を持たない基底に**既定を差し込まない**こと（B-140）。

    🔴 中継経路は帯を表示したうえで**基底をそのまま実行へ渡す**
    （`run_multihop(path, self._base_params)`）ので、表示だけ「中」にすると
    **帯の申告と実行がずれる**。⇒ 名乗れないものは名乗らない（`—`）。
    """
    shown = frozen_common.display_value("resolution", "")
    assert shown == frozen_common.NOT_APPLICABLE
    for level in tg.RESOLUTION_KEYS:
        assert shown != i18n.t(f"res_{level}")
    # 対の検査＝段階があるときは従来どおり表示ラベルを出す。
    assert frozen_common.display_value("resolution", "high") == i18n.t("res_high")


# ============================================================
# 5. **画素を飛ばさない**（B-148）
# ============================================================
# 🔑 **検査は「定数が変わったか」ではなく「1px より細かく刻めているか」**。
# 目標間隔を定数で見るゲートは、**次に緯度や層の構成を変えた日に素通りする**
# （実際、旧実装の `FINEST_MESH_M = 5.0` は「5m メッシュ」としては正しく、
# 誤っていたのは*それを刻みに使ったこと*だった＝定数を見ても分からない）。
#
# 日本の緯度帯（南端 24°＝沖ノ鳥島近辺 〜 北端 46°＝宗谷岬の北）。
_JAPAN_LATS = [24.0, 30.0, 34.54, 40.0, 45.52, 46.0]
# 「低」は対象外＝*粗いが速い*と名乗っている段階（画素を飛ばすのは仕様）。
_NO_SKIP_LEVELS = ["high", "medium"]


# 🔴 **このゲートは 2026-08-30 に作り直した（B-150）。** 前身は経路長を 1px 幅で
# 区切った**1 次元の代理**を見ており、方位も実タイル座標も見ていなかった＝
# *主張していること（画素を飛ばさない）を検査していなかった*
# （[[feedback-promote-recurring-checks]] の壊れ方③「間違ったものを要求している」）。
# ⇒ **実タイル座標で経路をなぞり、通過画素を直接列挙する。**
#
# ⚠️ **斜めを必ず入れる**＝1 次元では成り立つ「1px の半分」が破れるのは、
#    1 歩で縦横の境界を**同時に**跨いだときだけ。真北・真東だけ見ると素通りする。
_BEARINGS = [0.0, 12.5, 45.0, 67.5, 90.0, 123.0, 200.0, 315.0]


def _endpoint(lat: float, lon: float, km: float, bearing_deg: float):
    """出発点から方位・距離で行き先を出す（球面・テスト側の独立な式）。"""
    import math

    r = 6371.0
    d = km / r
    b = math.radians(bearing_deg)
    p1 = math.radians(lat)
    p2 = math.asin(math.sin(p1) * math.cos(d)
                   + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = math.radians(lon) + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(p1),
        math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def _pixel_at(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """**製品がその点で読む画素**（`dem._tile_coords` そのもの）。"""
    from core import dem

    xt, yt, px, py = dem._tile_coords(lat, lon, zoom)
    return (xt * 256 + px, yt * 256 + py)


def _traversed(a, b, zoom: int, per_px: int = 24) -> dict[tuple[int, int], list[float]]:
    """経路が通る画素と、その画素にいる区間 `[t...]`（**密な走査＝独立な参照**）。

    ⚠️ 実装（`_crossing_fractions`）を呼ばない＝**同じ式で採点しない**。
    ⚠️ 走査の刻みより短くかすめる画素は参照側も拾えない＝このゲートは
    *取りこぼしを見逃す*ことはあっても、**無いものを落とすことはない**。
    """
    n = max(int(_dist_km(a, b) * 1000.0 / tg.pixel_size_m(a[0], zoom) * per_px), 2000)
    out: dict[tuple[int, int], list[float]] = {}
    for i in range(n + 1):
        t = i / n
        lat = a[0] + (b[0] - a[0]) * t
        lon = a[1] + (b[1] - a[1]) * t
        out.setdefault(_pixel_at(lat, lon, zoom), []).append(t)
    return out


def _dist_km(a, b) -> float:
    return models.horizontal_distance_km(a[0], a[1], b[0], b[1])


@pytest.mark.parametrize("lat", _JAPAN_LATS)
@pytest.mark.parametrize("level", _NO_SKIP_LEVELS)
@pytest.mark.parametrize("bearing", _BEARINGS)
def test_no_pixel_of_the_dem_is_ever_skipped(bearing, level, lat):
    """標本が **DEM の画素を 1 つも飛ばさない**こと（実タイル座標・全方位）。

    🔴 **B-148 で直したつもりで半分しか直っていなかった不変条件**（B-150）。
    公称 5m で刻んでいた版は目標間隔が 1px の 1.15〜1.51 倍あり画素を飛ばしていた
    （実測でマージンが 29.2 dB 振れ判定が裏返った）。1px の半分にしても、
    **2 次元では縦横の境界を同時に跨いだ画素が抜ける**（403m の経路で 146 画素中 23 個）。
    """
    zoom = tg.RESOLUTION_ZOOM[level]
    a = (lat, 132.41)
    b = _endpoint(*a, 0.6, bearing)
    ref = _traversed(a, b, zoom)
    sampled = {_pixel_at(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, zoom)
               for t in tg.path_sample_fractions(*a, *b, level)}
    missed = sorted(set(ref) - sampled)
    assert not missed, (
        f"緯度 {lat}° / 方位 {bearing}° / {level}: 通過 {len(ref)} 画素のうち "
        f"{len(missed)} 個に標本が無い（例 {missed[:3]}）"
    )


@pytest.mark.parametrize("bearing", [12.5, 45.0, 67.5, 200.0])
def test_each_pixel_is_sampled_at_both_edges_of_its_chord(bearing):
    """各画素の**弦の両端**に標本が置かれていること（B-150 の芯）。

    🔑 **「1 画素に 1 点」では足りない**＝DEM は画素の中で一定の階段状の場で、
    Bullington の接線を決めるのは**棚の縁**。実測（`hiroshima_short_grazing`）＝
    弦の中点だけだと 21.74 dB、**両端なら 27.02 dB**（25 万点の収束値 27.014 と一致）。
    ⇒ ここが「1 点」に戻ると、**値は静かに 5 dB 低く出る**（誰も落ちない）。

    ⚠️ 見るのは点数ではなく**弦をどれだけ覆っているか**＝2 点でも中央に寄せて
    置けば「2 点ある」は満たせる。
    """
    zoom = tg.RESOLUTION_ZOOM["high"]
    a = (34.54, 132.41)
    b = _endpoint(*a, 0.4, bearing)
    ref = _traversed(a, b, zoom)
    fracs = tg.path_sample_fractions(*a, *b, "high")

    inside: dict[tuple[int, int], list[float]] = {}
    for t in fracs:
        p = _pixel_at(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, zoom)
        inside.setdefault(p, []).append(t)

    thin = 0
    for px, ts in ref.items():
        chord = max(ts) - min(ts)
        if chord < 1e-4:            # 角をかすめただけ＝縁も中も区別がつかない
            thin += 1
            continue
        got = inside.get(px, [])
        covered = (max(got) - min(got)) / chord if len(got) >= 2 else 0.0
        assert covered > 0.85, (
            f"方位 {bearing}° の画素 {px}: 弦の {covered:.0%} しか覆っていない"
            f"（標本 {len(got)} 点）＝棚の縁が標本になっていない"
        )
    assert thin < len(ref) / 2, "かすめる画素ばかりで検査になっていない"


@pytest.mark.parametrize("level", _NO_SKIP_LEVELS)
def test_the_two_dimensional_claim_is_not_made_by_a_spacing_rule(level):
    """**間隔で刻む口が「高」「中」に残っていない**こと（B-150）。

    ⚠️ 「1px の半分で刻む」は 1 次元でしか成り立たない主張だった。値を合わせる
    直し方（目標間隔をさらに細かくする）で上のゲートを通せてしまわないよう、
    *引き方*の側も縛る＝あの 2 段階は**画素の縁で刻む**と名乗ること。
    """
    assert tg.samples_are_pixel_edges(level)
    assert tg.grid_step_m(34.54, level) == pytest.approx(
        tg.pixel_size_m(34.54, tg.RESOLUTION_ZOOM[level]))


def test_the_rough_distance_matches_the_models_formula():
    """点数を解くための距離が `models.horizontal_distance_km` と一致すること。

    ⚠️ `terrain_grid` は純粋な層（`models` を import しない）ので式が 2 か所ある＝
    **写しはコメントでなくテストで縛る**（[[feedback-radiosim-rules]]）。
    """
    for a, b in (((34.54, 132.41), (34.55, 132.42)),
                 ((45.0, 141.0), (43.0, 143.5)),
                 ((24.3, 124.0), (24.31, 124.02))):
        assert tg._rough_dist_m(*a, *b) == pytest.approx(
            models.horizontal_distance_km(*a, *b) * 1000.0, rel=1e-9)


def test_the_zoom_table_matches_the_dem_layers():
    """段階が見ている層のズームが、**実際に取りに行く層**と一致すること。

    ⚠️ `RESOLUTION_ZOOM` は `dem.DEM_LAYERS` の写し＝**写しはテストで縛る**
    （`terrain_grid` は純粋な層なので `dem` を import できない＝I-069）。
    """
    from core import dem

    zooms = [z for _id, z in dem.DEM_LAYERS]
    assert tg.RESOLUTION_ZOOM["high"] == min(zooms) + 1 == max(zooms), (
        "5m 層（最優先）のズームと『高』が見ている層がずれている", zooms
    )
    assert tg.RESOLUTION_ZOOM["medium"] == min(zooms), (
        "10m 層（全国カバー）のズームと『中』が見ている層がずれている", zooms
    )
