"""
core/terrain_grid.py
====================
**地形をどれだけ細かく刻めるか／刻むか**の単一ソース（純関数・標準ライブラリのみ）。

なぜ dem.py から独立させたか
----------------------------
ここに在るのは *DEM の格子の事実*（1px が何 m か）と、そこから導かれる
*刻み方の決定*（何点で標本するか）だけで、**タイルの取得とは関心事が違う**。
分けたことで 2 つ効いた：

  - **設定層（`core/config.py`）から引ける**。config は「ネットワーク・PIL・numpy に
    依存しない」層なので `dem` を import できず、そのままだと段階の語彙
    （`high`/`medium`/`low`）を config 側に**書き写す**しかなかった。写しは必ずずれる
    （`VALID_ENV_TYPES` が `models.ENV_KEYS` の写しになっているのと同じ形）。
  - **`dem.py` が行数の分割閾値を越えた**（I-069 の追記で 1103 行）。割る線として、
    「取ってくる」と「どう刻むか」は自然な境目だった。

⚠️ **DEM1A（1m 級）を入れる日に動くのはこのファイルだけ**＝間隔の表はここにしかない。
"""

from __future__ import annotations

import math

# ============================================================
# DEM の格子の事実
# ============================================================
# 取れる中でいちばん細かいメッシュの**公称**間隔 [m]（`dem.DEM_LAYERS` の先頭＝5m）。
# **地形の標本がどれだけ細かいか**を画面へ伝えるために引く（I-098＝地図で点を
# 置き直す面。これより小さく動かしても標高は同じ格子から拾われるので、画面上は
# 動いたのに結果が変わらない）。
# ⚠️ これは *その場所で取れたら* の値＝5m が無い範囲は 10m へ落ちる。落ちた側は
# もっと粗いので、この値で出す注意は**控えめな側**（＝見落としではなく過小申告）。
# 🔴 **公称であって、タイルの実 1px ではない**（B-148）＝下の `pixel_size_m` が実寸。
#    刻みを決めるのに公称を使うと**画素を飛ばす**（この値で刻んだ版が実際にそうなった）。
FINEST_MESH_M: float = 5.0

# --- タイルの実 1px（B-148）------------------------------------------------
# 地理院タイルは Web メルカトル・256px/タイル。**1px の地上寸法は緯度で縮む**
# （`cos φ`）ので、公称 5m の層でも日本では 3.3〜4.4m になる。
EARTH_CIRCUMFERENCE_M: float = 40_075_016.686     # 赤道周長（WGS84）
TILE_PIXELS: int = 256

# 段階が見ている層のズーム。**`dem.DEM_LAYERS` の写しになる**ので、一致することは
# `tests/test_terrain_grid.py` が検査する（写しはコメントでなくテストで縛る）。
# ⚠️ `low` は**入っていない**＝*粗いが速い*と名乗っている段階なので、画素を飛ばすのは
# 仕様（下の `RESOLUTION_SPACING_M` を参照）。
RESOLUTION_ZOOM: dict[str, int] = {"high": 15, "medium": 14}

# 全国最悪値を採る緯度 [deg]。**日本の最北端（宗谷岬 45.52°N）より北**を採る＝
# ここで 1px がいちばん小さくなるので、**この緯度で決めた刻みは全国どこでも
# 画素を跨がない**。⚠️ 南（24°N）では必要より 2.6 倍細かく刻むが、実測でコストは
# **タイル取得が支配的で点数にほぼ依存しない**（48km・N=9677 で 0.8 秒）。
# 🔑 **緯度ごとに解く案は採らなかった**（2026-08-30 ユーザー決定）＝目標間隔が実行
# ごとに動くと、帳票の刻印「目標間隔 {m} m」を段階の定数で書けなくなる。
WORST_CASE_LAT_DEG: float = 46.0


def pixel_size_m(lat_deg: float, zoom: int) -> float:
    """その緯度・ズームでのタイル 1px の地上寸法 [m]（Web メルカトル）。"""
    return (EARTH_CIRCUMFERENCE_M * math.cos(math.radians(lat_deg))
            / (TILE_PIXELS * 2.0 ** zoom))


def lonlat_to_pixel(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """緯度・経度 → そのズームの「グローバルピクセル座標」（小数）。

    Web メルカトル順射影。タイル境界で floor せず小数のまま返す（各タイルは
    256px 四方なので `tile_x * 256 = タイル左端の world_px_x`）。

    🔑 **`dem` からここへ移した**（B-150）＝*どの画素を読むか*は「格子の事実」の
    側で、標本の置き方（`path_sample_fractions`）がこの式を必要とする。
    `dem` は純粋層を import できるので、`dem.lonlat_to_pixel` は**同じ関数**を
    指したまま（写しを作らない）。
    """
    n       = 2.0 ** zoom
    xtile_f = (lon + 180.0) / 360.0 * n
    ytile_f = (
        1.0
        - math.log(
            math.tan(math.radians(lat))
            + 1 / math.cos(math.radians(lat))
        ) / math.pi
    ) / 2.0 * n
    return xtile_f * TILE_PIXELS, ytile_f * TILE_PIXELS


def pixel_y_to_lat(world_y: float, zoom: int) -> float:
    """`lonlat_to_pixel` の **y の逆写像**＝そのグローバル画素 y の緯度 [deg]。

    画素の境目が経路のどこ（t）に当たるかを**解いて**出すために要る
    （走査で近似すると、詰めた桁数ぶんだけ境目がずれる）。
    """
    n = TILE_PIXELS * 2.0 ** zoom
    return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * world_y / n))))


def _no_skip_spacing_m(zoom: int) -> float:
    """その層の画素を **1 つも飛ばさない**目標間隔 [m]（全国最悪値・0.01m 丸め）。

    🔴 **これは「低」だけが使う量になった**（B-150・2026-08-30）。
    🔑 かつての読み＝「1px ちょうどだと 2 度読む位置と 1 度も読まない位置が交互に
    出るので**その半分**なら各画素に必ず 1 点入る」。**これは 1 次元でしか成り立たない**
    ＝2 次元の格子では、1 歩で縦横の境界を同時に跨いだ瞬間に隣の画素が抜ける
    （実測＝403m の経路で通過 146 画素のうち **23 個が欠落**）。
    ⇒ 「高」「中」は間隔で刻むのをやめ、**通過画素を列挙する**
    （→ `path_sample_fractions`）。
    """
    return math.floor(pixel_size_m(WORST_CASE_LAT_DEG, zoom) / 2.0 * 100.0) / 100.0


# ============================================================
# 地形の解像度（I-069）＝**利用者は段階を選び、点数はアプリが解く**
# ------------------------------------------------------------
# 以前は「地形サンプル数」を数で入力させていた。数は**距離によって意味が変わる**
# （既定 200 点は 500m なら 2.5m 間隔・20km なら 100m 間隔＝尾根を 1 点で跨ぐ）。
# 適切な数を選ぶには DEM の 1px を知っている必要があり、スクリーニング用途の
# 利用者に求めるべき知識ではない。⇒ **段階を選べば点数はアプリが解く。**
# ⚠️ **段階＝間隔ではなくなった**（B-150）＝「高」「中」は*どの層の画素を縁ごとに
# 読むか*で、点数は経路（距離・方位・緯度）から決まる。
#
# ⚠️ **段階は「取れたら」の値**＝5m 層が無い範囲は 10m 層へ落ちるので、そこでは
# 「高」でも同じ格子を読み直すだけ（害はないが細かくもならない）。
RESOLUTION_KEYS: list[str] = ["high", "medium", "low"]
RESOLUTION_DEFAULT: str = "medium"      # 全国カバーの 10m メッシュと同じ刻み
#
# 🔴 **等間隔で刻むのは「低」だけになった**（B-150）。「高」「中」は経路が通る画素を
# 列挙して縁に標本を置く（→ `path_sample_fractions`）ので、ここの値を**実行では
# 引かない**。⚠️ **表から消さない**＝①知らない語・座標が読めないときの落とし先
# ②「低」との比較（開示の字）で、どちらも「その段階が名乗る粗さ」を要る。
#
# 📜 経緯（B-148）＝以前は 3 段階ともこの表で刻んでいた。公称（5 / 10m）で刻んだ版は
# **日本の全緯度で 1px の 1.15〜1.51 倍**＝どこでも粗い側で画素を飛ばしており
# （実測でマージンが 29.2 dB 振れ判定が裏返った）、それを「実 1px の半分」へ直した。
# **その半分がまだ 2 次元では足りなかった**というのが B-150（→ `_no_skip_spacing_m`）。
RESOLUTION_SPACING_M: dict[str, float] = {
    "high":   _no_skip_spacing_m(RESOLUTION_ZOOM["high"]),    # 落とし先（5m 層 z15）
    "medium": _no_skip_spacing_m(RESOLUTION_ZOOM["medium"]),  # 落とし先（10m 層 z14）
    "low":    FINEST_MESH_M * 4,        # 20m ＝粗いが速い（従来の 200 点に近い側）
}

# 解決後の点数の下限と天井。
# ⚠️ **天井は性能上の必然ではない**（実測 2026-08-25＝48km の経路で N=240 が 0.3 秒・
# N=9677〔実効 5.0m〕でも 0.8 秒。コストはタイル取得＝距離で決まり N に依存しない）。
# 🔴 **旧上限 2000 は段階そのものを潰していた**＝コーパス 26 本のうち 12 本で「高」が
# 上限に張り付き、6 本は「高」と「中」が**同じ結果**、48km の 1 本は 3 段階すべてが
# 同じ結果だった。⇒ 天井は**事故の歯止め**（座標の打ち間違いで極端な距離が入った時）
# として残すだけにし、実務の距離帯では段階が段階として効くようにする。
SAMPLES_MIN: int = 10
# 🔴 **天井は刻み方と連動する**（B-148 / B-150）＝天井に当たった瞬間に等間隔へ落ちる
# ので、**天井が低いと「直した不変条件が距離の先で黙って破れる」**（B-148 のときは
# 旧天井 20000 のままで 33km を超えた「高」が目標間隔に届かなくなっていた）。
# 🔴 **B-150 で必要な高さが上がった**＝斜めの経路は縦横**両方**の境界を跨ぐので、
# 通過画素は真北・真東の最大 √2 倍。実測の最悪＝**46°N・方位 45°・100km で 85,817 点**
# （全緯度 24〜46°・方位 0〜90° を 5° 刻みで掃いた最大値）。
# ⇒ 「100km を『高』で刻んでも届く」という約束を、斜めでも満たす高さへ。
# ⚠️ **伝搬計算は問題にならない**（実測＝N=60000 で 0.006 秒）。
# 🔴 **効くのは取得の側で、そこには点数に比例する固定コストがある**（B-152・実測
# 2026-08-30＝取得を定数に差し替えても **N=60000 で 4.51 秒**＝1 点 1 スレッド生成）。
# ⚠️ **かつてここには「そのコストは距離＝タイル枚数で決まる」と書いてあったが誤り**
# （天井を上げる根拠にその一文を使っていた）。天井を上げると**待ち時間が点数に比例して
# 伸びる**＝この値は「事故の歯止め」であると同時に**待ち時間の上限**でもある。
# ⇒ 取得の骨格（ワーカープール化）は B-152 として 3.1 で直す。
SAMPLES_CEILING: int = 90000            # 100km を「高」（画素の縁）で刻んでも届く高さ


def recommended_samples(dist_m: float, level: str) -> int:
    """距離と段階から**等間隔で刻むときの**標本数を解く（純関数・NW に触らない）。

    🔴 **これを引くのは「低」と落とし先だけ**（B-150）＝「高」「中」は画素の縁で
    刻むので、点数は距離だけでは決まらない（方位と緯度が効く）。
    ⚠️ **1 次元の解き方**（`距離 ÷ 目標間隔`）＝2 次元で画素を飛ばさないことは
    この式では約束できない。約束するのは `path_sample_fractions` の側。

    ⚠️ **どの DEM 層が答えるかは取得後にしか分からない**ので 5m 層前提で決め打ちする
    （10m 層しか無い地域では同じ格子を重複して読むだけで害はない）。

    Args:
        dist_m: 2 地点の水平距離 [m]。0 以下・非有限なら下限を返す。
        level:  `RESOLUTION_KEYS` のいずれか。**知らない語は既定へ落とす**
                （壊れた設定ファイルで実行そのものを止めない＝入力の検査は
                `config.validate_config` の仕事で、ここは計算の側）。
    """
    spacing = RESOLUTION_SPACING_M.get(level, RESOLUTION_SPACING_M[RESOLUTION_DEFAULT])
    if not math.isfinite(dist_m) or dist_m <= 0:
        return SAMPLES_MIN
    return int(min(SAMPLES_CEILING, max(SAMPLES_MIN, round(dist_m / spacing) + 1)))


def effective_spacing_m(dist_m: float, samples: int) -> float:
    """解決後の点数での**実際の**標本間隔 [m]（等間隔に刻んだ実行だけの値）。

    🔴 天井に張り付くと段階が効かなくなる＝**そこで画面が黙るのが最悪の壊れ方**
    （利用者は「高」を選んだのに中と同じ答えを見ることになる）。目標間隔ではなく
    *実効*を出すのは、張り付きが数字として見えるようにするため。

    ⚠️ **「高」「中」はもう等間隔ではない**（B-150）＝画素の縁に標本を置くので
    間隔は画素ごとに違う。あの 2 段階が画面へ出すのは `grid_step_m`（画素の寸法）で、
    この関数を引くのは**「低」と固定 N（等間隔で刻む口）だけ**。
    """
    if samples <= 1 or not math.isfinite(dist_m) or dist_m <= 0:
        return 0.0
    return dist_m / (samples - 1)


# ============================================================
# 通過画素で刻む（B-150）
# ------------------------------------------------------------
# 🔴 **「1px の半分なら各画素に必ず 1 点入る」は 2 次元では成り立たない。**
# 1 歩のあいだに縦横の境界を**同時に**跨ぐと、経路が実際に通った隣の画素へ標本が
# 置かれない。実測（403m・「高」245 点）＝通過 146 画素のうち **23 個が欠落**。
#
# 🔑 **処方は「もっと細かく刻む」ではない**＝DEM の標高は*画素の中で一定の階段状の
# 場*で、Bullington の接線を決めるのは**棚の縁**（中心ではない）。だから一様な刻みは
# 細かくするほど値が上がり続ける。実測（`hiroshima_short_grazing` の回折損）:
#
#     刻み 1.65m → 19.66 dB ／ 0.21m → 26.76 ／ 0.0016m → **27.014**（収束値）
#     ⚠️ 最高標高はどの刻みでも 331.26m で同じ＝**峰を落としていたのではない。**
#
#   - **弦の中点に 1 点**（105 点）＝ 21.74 dB … 中点では届かない
#   - **弦の両端＝棚の縁**（210 点）＝ **27.02 dB** … 25 万点の収束値と 0.01 dB 一致
#
# ⇒ **刻みという収束パラメータそのものを消す**＝経路が通る画素を列挙し、各画素の
#    弦の両端に標本を置く。📏 再測は `experiments/b150_supercover_probe.py`。
#
# ⚠️ **標本はもう等間隔に並ばない**＝距離軸を `linspace` で作っている側は全部
#    引き直す必要がある（→ `models.TerrainProfile.frac_axis`）。

# 画素の縁から内側へずらす量 [px]。**縁ちょうどを読むと floor がどちらへ落ちるか
# 決まらない**（境界そのものは隣の画素と共有している）ので、必ず内側で読む。
# 0.4mm 相当＝距離としては無視でき、倍精度の丸め（1e-9 px 程度）より 5 桁大きい。
_EDGE_INSET_PX: float = 1e-4


def path_sample_fractions(
    lat_tx: float, lon_tx: float, lat_rx: float, lon_rx: float, level: str,
) -> list[float]:
    """経路上の標本位置を **0.0〜1.0 の並び**で返す（純関数・ネットワーク不使用）。

    「高」「中」＝**経路が通る DEM 画素を列挙し、各画素の弦の両端**（B-150）。
    「低」＝*粗いが速い*と名乗っている段階なので**等間隔のまま**（画素を飛ばすのが仕様）。

    ⚠️ **返すのは経路のパラメータ**（緯度・経度ではない）＝標本の位置を運ぶ器は
    これ 1 つにする。緯度経度は呼ぶ側が `tx + (rx - tx) * t` で作る（製品の
    `np.linspace` と同じ内挿＝経路の定義を 2 か所に持たない）。
    """
    zoom = RESOLUTION_ZOOM.get(level)
    if zoom is None:
        # 「低」と知らない語＝等間隔（知らない語の落とし先は `recommended_samples`）。
        n = recommended_samples(_rough_dist_m(lat_tx, lon_tx, lat_rx, lon_rx), level)
        return _uniform(n)

    coords = (lat_tx, lon_tx, lat_rx, lon_rx)
    if not all(math.isfinite(v) for v in coords):
        return _uniform(SAMPLES_MIN)

    x0, y0 = lonlat_to_pixel(lat_tx, lon_tx, zoom)
    x1, y1 = lonlat_to_pixel(lat_rx, lon_rx, zoom)
    span_px = math.hypot(x1 - x0, y1 - y0)
    if span_px <= 0.0:
        return _uniform(SAMPLES_MIN)

    # 画素が多すぎる（座標の打ち間違いで地球半周など）＝天井で等間隔へ落とす。
    # ⚠️ **落ちたことは点数として画面に出る**（天井の値がそのまま出る）。
    if span_px * 2.0 + 2.0 > SAMPLES_CEILING:
        return _uniform(SAMPLES_CEILING)

    cuts = _crossing_fractions(x0, y0, x1, y1, lat_tx, lat_rx, zoom)

    inset = _EDGE_INSET_PX / span_px
    out: list[float] = []
    edges = [0.0] + cuts + [1.0]
    for lo, hi in zip(edges, edges[1:]):
        if hi - lo <= 2.0 * inset:
            out.append((lo + hi) / 2.0)      # 端をかすめただけの画素＝1 点で足りる
        else:
            out.append(lo + inset)
            out.append(hi - inset)
    # 両端は**きっかり TX / RX**（`elevs[0]` / `elevs[-1]` がアンテナの足元になる）。
    out[0] = 0.0
    out[-1] = 1.0
    if len(out) < SAMPLES_MIN:
        return _uniform(SAMPLES_MIN)
    return out


def _crossing_fractions(
    x0: float, y0: float, x1: float, y1: float,
    lat_tx: float, lat_rx: float, zoom: int,
) -> list[float]:
    """経路が画素の境界を跨ぐ `t` を昇順で返す（**解いて出す・走査しない**）。

    🔑 **縦と横で解き方が違う**＝`x` は経度に比例し経度は `t` に比例するので一次式で
    解ける。`y` は緯度のメルカトル像なので、**境界の y から緯度へ逆写像**してから
    `t` に直す（→ `pixel_y_to_lat`）。走査で近似すると、詰めた桁の分だけ境目がずれ、
    「縁の標本」がときどき隣の画素に落ちる。
    """
    cuts: list[float] = []

    if x1 != x0:
        lo, hi = sorted((x0, x1))
        for k in range(math.floor(lo) + 1, math.floor(hi) + 1):
            cuts.append((k - x0) / (x1 - x0))

    if y1 != y0 and lat_rx != lat_tx:
        lo, hi = sorted((y0, y1))
        for k in range(math.floor(lo) + 1, math.floor(hi) + 1):
            lat_k = pixel_y_to_lat(float(k), zoom)
            cuts.append((lat_k - lat_tx) / (lat_rx - lat_tx))

    return sorted(t for t in cuts if 0.0 < t < 1.0)


def _uniform(n: int) -> list[float]:
    """等間隔の並び（「低」・固定 N・落とし先）。"""
    n = max(2, int(n))
    return [i / (n - 1) for i in range(n)]


def _rough_dist_m(lat_tx: float, lon_tx: float, lat_rx: float, lon_rx: float) -> float:
    """点数を解くためだけの水平距離 [m]（球面・`models.horizontal_distance_km` と同式）。

    ⚠️ **`models` を import しない**＝この層は標準ライブラリだけ（設定層から引ける
    ようにするための境界＝I-069）。式が 2 か所になるので、一致は
    `tests/test_terrain_grid.py` が縛る（写しはコメントでなくテストで表現する）。
    """
    r_earth_m = 6_371_000.0
    dlat = math.radians(lat_rx - lat_tx)
    dlon = math.radians(lon_rx - lon_tx)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat_tx)) * math.cos(math.radians(lat_rx))
         * math.sin(dlon / 2) ** 2)
    return 2 * r_earth_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def grid_step_m(lat_deg: float, level: str) -> float:
    """その段階が**実際に見ている地形の刻み** [m]（画面・帳票へ出す値）。

    「高」「中」＝その緯度での DEM の 1px（標本はその画素の縁に置かれる）。
    「低」＝等間隔の目標間隔（画素より粗い＝そう名乗っている段階）。

    🔑 **「実効間隔」を置き換えた値**（B-150）＝画素の縁で刻む以上、標本間隔は
    画素ごとに違うので「N 点を等間隔で」という読み方そのものが成り立たない。
    *効いているのは画素の寸法*なので、それを出す。
    """
    zoom = RESOLUTION_ZOOM.get(level)
    if zoom is None:
        return RESOLUTION_SPACING_M.get(
            level, RESOLUTION_SPACING_M[RESOLUTION_DEFAULT])
    return pixel_size_m(lat_deg, zoom)


def samples_are_pixel_edges(level: str, samples: "int | None" = None) -> bool:
    """その実行が**画素の縁で刻んだ**か（＝等間隔ではないか）。

    画面・帳票が「実効 約 N m 間隔」と「DEM 画素 N m ごと」のどちらを名乗るかは
    この 1 つの判定から出す（面ごとに条件を書くと、次に段階を足した日にずれる）。

    ⚠️ **天井に当たった実行は「画素の縁」を名乗れない**＝そこでは等間隔へ落ちている
    （`path_sample_fractions`）。点数を渡さないと*段階の建前*しか答えられないので、
    実行済みの面は必ず `samples` を渡す。
    """
    if level not in RESOLUTION_ZOOM:
        return False
    return samples is None or samples < SAMPLES_CEILING
