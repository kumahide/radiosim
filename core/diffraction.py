"""
diffraction.py
==============
**回折損だけ**を持つ層（純粋計算・副作用ゼロ・GUI 依存ゼロ）。

`models.py` から 2026-08-26 に切り出した（B-130 で Bullington へ差し替え、履歴と
関数が増えてモジュールの分割閾値を超えたため＝`tests/test_repo_hygiene.py`）。
⚠️ **閾値を上げるのではなく割る**、が規約（数字を黙って動かさない）。

  _diffraction_loss_fk  : Fresnel-Kirchhoff の J(ν)（両モデル共通の 1 本）
  _bullington_loss      : Bullington 等価ナイフエッジ（ITU-R P.526 §4.5.1）
  _smooth_earth_surface : 平滑地球面（最小二乗直線）
  _spherical_earth_loss : 球面回折の**席**（常に 0 dB・未実装）
  _multi_obstacle_loss  : 上記の合成＝P.452 §4.2.1 の形

⚠️ **`models.py` から再輸出している**（`from core.diffraction import ...`）＝
既存の呼び出し `models._diffraction_loss_fk` などはそのまま動く。
"""

import math

import numpy as np


# NOTE（回折モデルの履歴＝**同じ道に戻らないための記録**。2026-08-26 に全面改訂）:
#
# 🔁 **2026-08-26（3.0a1）に、独自の Deygout 実装から Bullington へ差し替えた**（B-130）。
#   直前まで使っていたのは「最大遮蔽点を主障害物として区間を再帰的に割り、J(ν) を
#   足し上げる」独自実装で、**2 度直して 2 度とも別の壊れ方をした**:
#
#   ① **発散**（B-032・〜3.0a1 の `2cfd5fc` で修正）＝区間を割るほど
#      `d1·d2/(d1+d2)` が縮んで ν が人工的に増え、**同じ山を何度も数える**。
#      山越えで 1000〜2400 dB、実機の 794m の回線で 1956.8 dB。
#      ⇒ 「ν > -0.8 が連続する範囲は 1 枚」として一体化（Kν）＝発散は止まった。
#   ② 🔴 **不連続**（B-130・この改訂）＝その一体化の境界が**1 点の ν で切り替わる**ため、
#      植生高やアンテナ高を**一様に 1m 動かすと回折損が 84.6 dB 跳ぶ**（アンテナを
#      1m 上げたら 84 dB 悪くなる）。実データ 26 本のうち植生高で 7 本・アンテナ高で
#      4 本が逆行した。**「悪い前提にすると答えが良くなる」道具は成立しない。**
#
# 🔑 **①②に共通の根＝「障害物を数える」構成そのもの**。枚数は整数なので、地形が
#   連続に動いても答えは跳ぶ。⇒ **数えない手法（Bullington）に替えた**＝等価ナイフ
#   エッジは 2 本の接線の交点で、地形が動けば連続に動く。
#
# 📏 **連続性は測って確かめた**（掃引の刻みを 1/10 にして最大段差が 1/10 になるか）:
#     旧実装（Kν）      : 1.0m 刻み 84.62 dB / 0.1m 刻み 84.67 dB（比 1.00＝崖）
#     重み付き一体化(Wν): 1.0m 刻み 16.57 dB / 0.1m 刻み 18.78 dB（比 1.13＝崖が残る）
#     **Bullington      : 1.0m 刻み 10.48 dB / 0.1m 刻み  1.99 dB（比 0.19＝連続）**
#   ⚠️ **「1m 刻みの差分」を跳びと呼ばないこと**＝急な坂と区別できない。ここで踏んだ。
#
# ⚠️ **差し替えで悪くなった面もある（承知のうえ）**:
#   - **多重稜線で小さめに出る**＝独立した 2 峰で single の約 1.4 倍（旧実装は約 2.0 倍）。
#     P.526/P.452 もこの過小評価を承知でこの構成を採る。⇒ 刻印で名乗る。
#   - **単一の丘では大きめに出る**＝標準の補正項が乗るため（広い丘 40m で 13.5→22.9 dB）。
#   - ゴールデン 26 本で **17 本の値が動き、判定は 2 本が OK→NG**（どちらも安全側）。
#
# ⛔ **戻さないための記録**＝「主障害物の頂点高度を再帰の端点にする」流儀（古典的な
#   Deygout）は**さらに悪い**（平地 214.96 dB / 広い丘 25m 229.48 dB の実測）。
#   ⇒ **「Deygout に戻せば直る」は誤り。**


# ============================================================
# 回折モデル
# ============================================================

#: 単一障害物モデルの内部キー（最大 ν の 1 点だけを見る）。
DIFF_METHOD_SINGLE: str = "single"

#: 複数障害物モデルの内部キー＝**Bullington 等価ナイフエッジ**（P.526 §4.5.1）。
#: 🔁 **2026-08-26 に独自 Deygout 実装から差し替えた**（B-130）＝一様なかさ上げで
#: 回折損が 84.6 dB 跳ぶ（アンテナを 1m 上げたら 84 dB 悪くなる）欠陥が、
#: 「障害物を数える」構成そのものに由来していたため。**旧キー `deygout` は
#: `DIFF_METHOD_ALIASES` で受け続ける**（利用者の `.rsproj` と入力 CSV が壊れない）。
DIFF_METHOD_MULTI: str = "bullington"

#: 内部キーの旧名 → 新名（**入力だけ**受ける／出力は必ず新名で書く）。
#: ⚠️ **消さない**＝2.x〜3.0a1 の `.rsproj`・設定・入力 CSV に旧名が残っている。
DIFF_METHOD_ALIASES: dict[str, str] = {"deygout": DIFF_METHOD_MULTI}

#: 有効な回折モデル（**画面の並び順**＝複数障害物が既定なので先）。
DIFF_METHOD_KEYS: tuple[str, ...] = (DIFF_METHOD_MULTI, DIFF_METHOD_SINGLE)


def normalize_diff_method(value: str | None) -> str:
    """回折モデルの内部キーを正規化する（旧名を受けて新名を返す・純関数）。

    🔑 **入力の境目 1 か所で呼ぶ**（`SimParams` と `validate_config`）＝散らすと、
    次に旧名が増えた日に「受ける場所」と「受けない場所」ができる。
    """
    key = (value or "").strip()
    return DIFF_METHOD_ALIASES.get(key, key)


# ──────────────────────────────────────────────────────────────
# 回折損ヘルパー
# ──────────────────────────────────────────────────────────────

# 回折損の打ち切り閾値
_NU_THRESHOLD:    float = -0.8   # これ以下は回折損 0 dB として打ち切る（ITU-R P.526 の見通し判定相当）
_MAX_DEPTH:       int   = 20     # 再帰上限（無限ループ防止）
# ⛔ **`_MIN_SEGMENT_M`（区間幅 50m 未満で打ち切る）は撤去した**（B-126・2026-08-26）。
#   ①**値を 1 つも決めていなかった**＝実データ 26 本・合成 5 形状 × 標本数 3 通りの
#     全条件で、この下限を 0m / 50m / 200m のどれにしても結果が完全に一致した。
#   ②**長さで書いた下限は解像度で意味が変わる**＝区間の幅は標本間隔の倍数なので、
#     「50m」は実効 20m では 2 点ぶん・実効 5m では 10 点ぶん。実際に**実効 5m の
#     ときだけ 3 本で発火**していた（発火が幾何ではなく設定で決まっていた）。
#   ⇒ 幾何の下限は **`N < 3`（区間に内点が無い）だけで足りる**。再帰の停止は
#     `N < 3` と `_MAX_DEPTH` が保証する（区間は必ず点数が減るので必ず尽きる）。


def _diffraction_loss_fk(v: float) -> float:
    """
    Fresnel-Kirchhoff 回折損 J(ν)。
    ν <= -0.8 のとき損失なし（完全見通し）。
    """
    if v <= _NU_THRESHOLD:
        return 0.0
    # ⚠️ **負を返させない**＝閾値のすぐ上（-0.80 < ν < -0.79 あたり）で J(ν) は
    # **-0.06 dB まで負に振れる**。損失として負の値は意味を持たず、0.1 dB 刻みの
    # 帳票では `-0.1 dB` と印字されてしまう（B-125 の掃きテストで踏んだ）。
    # 🔑 **打ち切りの側で切らずにここで切る**＝両モデルが同じ 1 本を通るので、
    # 片方だけが負を出す形（＝B-125 そのもの）を作り直さずに済む。
    return max(0.0, float(
        6.9
        + 20 * math.log10(
            math.sqrt((v - 0.1) ** 2 + 1) + v - 0.1
        )
    ))



def _smooth_earth_surface(
    obs_surface: np.ndarray,
    d_m_axis:    np.ndarray,
) -> np.ndarray:
    """経路の「平滑地球」面＝標高に当てた最小二乗直線（P.452 §4.2.1 の平滑地球）。

    ⚠️ **いまは `_spherical_earth_loss` が席（常に 0 dB）なので、この関数は
    その席が埋まったときにだけ使われる**（→ `_multi_obstacle_loss`）。
    単体では常に検査できるように分けてある。
    """
    x = np.asarray(d_m_axis, dtype=float)
    y = np.asarray(obs_surface, dtype=float)
    if len(x) < 2:
        return y.copy()
    slope, intercept = np.polyfit(x, y, 1)
    return slope * x + intercept


def _spherical_earth_loss(
    d_m_axis: np.ndarray,
    tx_abs:   float,
    rx_abs:   float,
    lam:      float,
    smooth:   np.ndarray,
) -> float:
    """平滑地球の球面回折損 [dB]。**⛔ 未実装＝常に 0.0（席）**。

    🔑 **なぜ席だけ置くか**（2026-08-26・ユーザー決定）:
      - **Bullington は地球の丸みそのものによる回折（creeping wave）を過小評価する**。
        P.452 §4.2.1 はそれを `Ld = Lbulla + max(Ldsph - Lbulls, 0)` で補う
        （Delta-Bullington）。**この関数が `Ldsph`。**
      - ⚠️ **実装には入力が 2 つ足りない**＝**地表の電気定数**（比誘電率・導電率＝
        陸 22 / 0.003 S·m⁻¹ に対し海 80 / 5 と桁で違う）と**偏波**。どちらも
        この製品は持っておらず、**黙って固定値を埋めると、いちばん効く海上経路で
        いちばん外す**（→ 帳票の刻印 `spherical_earth` で「見ていない」と名乗る）。
      - 📏 **効く条件は測ってある**＝**平滑地球の地平線を越える経路だけ**。
        ゴールデン 26 本は **0/26 本**（最も近い `niigata_grazing_900` で比 0.65）。
        現実の想定でも「鉄塔 30m × 30km」で比 0.66、**効くのは「低いアンテナ ×
        長距離 × 平坦」**（海上 6m/6m・25km で 1.24／マスト 15m・50km で 1.57）。
      - **埋める引き金**＝①海上・平坦の長距離を対象にすると決めたとき
        ②地表の性質を入力に持ったとき（3.4 / 3.5）。

    Args:
        d_m_axis: 各サンプル点の TX からの水平距離 [m]
        tx_abs:   TX アンテナ絶対高度 [m]
        rx_abs:   RX アンテナ絶対高度 [m]
        lam:      波長 [m]
        smooth:   平滑地球面（`_smooth_earth_surface` の戻り）
    """
    return 0.0


def _bullington_loss(
    obs_surface: np.ndarray,
    d_m_axis:    np.ndarray,
    tx_abs:      float,
    rx_abs:      float,
    lam:         float,
) -> float:
    """ITU-R P.526 §4.5.1 の Bullington 等価ナイフエッジによる回折損 [dB]。

    **両端から見た仰角が最大の点を結ぶ 2 本の接線を引き、その交点を 1 枚の
    ナイフエッジとみなす**（地形を「数えない」のが要点）。

    🔑 **なぜこの手法か**（B-130・2026-08-26 にユーザー決定）＝**交点は地形が
    動けば連続に動く**ので、**「障害物が何枚あるか」が切り替わる瞬間が無い**。
    前身の独自 Deygout 実装は、一様なかさ上げ（植生高・アンテナ高）で
    ν の連続区間が繋がった瞬間に**枚数が 2→1 に変わり、実測で 84.6 dB 跳んだ**
    （アンテナを 1m 上げたら回折損が 84 dB 増える＝道具として成立しない）。
    📏 **連続性は測って確かめてある**＝掃引の刻みを 1/10 にすると最大段差も
    ほぼ 1/10（比 0.19）。旧実装は比 1.00（＝刻みを細かくしても段差が残る＝崖）。

    ⚠️ **多重稜線では過小に出る**＝独立した 2 峰で `single` の約 1.4 倍
    （旧実装は約 2.0 倍）。P.526/P.452 もこの過小評価を承知でこの構成を採る。
    ⇒ **帳票の刻印 `diff_bullington` でそう名乗る。**

    アルゴリズム（P.526 §4.5.1）:
      1. TX から各点を見た仰角の最大 `m_tx` を取る。
      2. `m_tx` が LoS の傾きを超えない＝地形が LoS を切らない場合は、
         **最大 ν の点で J(ν)**（F1 に食い込んでいれば損失が付く）。
      3. 切る場合は RX 側の仰角の最大 `m_rx` も取り、2 本の接線の交点を
         等価ナイフエッジとして J(ν) を出す。
      4. どちらの場合も**標準の補正項**を掛ける:
         `L = Luc + (1 - exp(-Luc/6)) * (10 + 0.02 * D_km)`
         ⚠️ **Luc = 0 のとき補正も 0** なので、見通しの側から連続に立ち上がる。
    """
    d_m_axis = np.asarray(d_m_axis, dtype=float)
    n = len(obs_surface)
    if n < 3:
        return 0.0

    total_m = float(d_m_axis[-1] - d_m_axis[0])
    if total_m <= 0.0:
        return 0.0

    inner = slice(1, -1)
    d1 = np.maximum(d_m_axis[inner] - d_m_axis[0], 1.0)
    d2 = np.maximum(d_m_axis[-1] - d_m_axis[inner], 1.0)

    los_slope = (rx_abs - tx_abs) / total_m
    m_tx = float(np.max((obs_surface[inner] - tx_abs) / d1))

    if m_tx <= los_slope:
        # 地形が LoS を切らない＝最大 ν の点で J(ν)（single と同じ形）
        los = tx_abs + los_slope * (d_m_axis[inner] - d_m_axis[0])
        nu  = (obs_surface[inner] - los) * np.sqrt(
            2.0 / np.maximum(lam * d1 * d2 / (d1 + d2), 1e-9)
        )
        l_uc = _diffraction_loss_fk(float(np.max(np.nan_to_num(nu))))
    else:
        m_rx  = float(np.max((obs_surface[inner] - rx_abs) / d2))
        denom = m_tx + m_rx
        if denom <= 0:
            return 0.0
        # 2 直線 tx_abs + m_tx·x = rx_abs + m_rx·(D - x) の交点＝等価ナイフエッジ
        x_b = min(max((rx_abs - tx_abs + m_rx * total_m) / denom, 1.0),
                  total_m - 1.0)
        h_b = tx_abs + m_tx * x_b
        los_b = tx_abs + los_slope * x_b
        denom_nu = max(lam * x_b * (total_m - x_b) / total_m, 1e-9)
        l_uc = _diffraction_loss_fk((h_b - los_b) * math.sqrt(2.0 / denom_nu))

    if l_uc <= 0.0:
        return 0.0
    return float(
        l_uc
        + (1.0 - math.exp(-l_uc / 6.0)) * (10.0 + 0.02 * (total_m / 1000.0))
    )


def _multi_obstacle_loss(
    obs_surface: np.ndarray,
    d_m_axis:    np.ndarray,
    tx_abs:      float,
    rx_abs:      float,
    lam:         float,
) -> float:
    """複数障害物の回折損 [dB]＝**Bullington ＋（球面回折の席）**。

    P.452 §4.2.1 の Delta-Bullington の形をそのまま置いてある:

        Ld = Lbulla + max(Ldsph - Lbulls, 0)

    ⚠️ **第 2 項はいま常に 0**（`_spherical_earth_loss` が席）＝実質 `Lbulla`
    だけが効く。**式の形を先に置いておく**のは、席が埋まった日に呼び出し側を
    組み替えずに済むようにするため（較正プロファイルの空欄・地面反射の刻印と
    同じ「先に席を置く」型）。
    """
    lbulla = _bullington_loss(obs_surface, d_m_axis, tx_abs, rx_abs, lam)

    smooth = _smooth_earth_surface(obs_surface, d_m_axis)
    ldsph  = _spherical_earth_loss(d_m_axis, tx_abs, rx_abs, lam, smooth)
    if ldsph <= 0.0:
        return lbulla

    lbulls = _bullington_loss(smooth, d_m_axis, tx_abs, rx_abs, lam)
    return float(lbulla + max(ldsph - lbulls, 0.0))
