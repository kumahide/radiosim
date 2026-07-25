"""
units.py
========
人が読む数値表記（距離・F1 遮蔽率）の単一ソース。純関数・副作用ゼロ。
GUI・ネットワーク・ファイル I/O を一切持たない（models.py / coords.py と同じ制約）。

方針（2.5a1 / I-014）:
  - **内部計算は km 据え置き**。ITU-R の式が dB/km 系（models.py の比減衰量・
    酸素吸収）で、models.py は副作用ゼロの純コアなので触らない。
  - **換算は表示層のみ**＝人が読む距離はすべて m に統一する。実用距離が 20km
    程度で標高軸が m である以上、距離だけ km は読みづらい（DEM 10m 精度・
    机上スクリーニングという前提とも m のほうが整合する）。
  - **m/km の切替は設けない**。coord_format の DD/DMS は「表記の好み」だが、
    距離は「単位の一貫性」なので固定が正しい（設計哲学⑤＝増やすより削る）。

なぜ独立モジュールなのか:
  以前は距離書式が map_graphics.distance_text（PIL 描画モジュール）と
  report.py / views/graph.py のインライン f-string に散在していた。置き場が
  無いと次の距離表示追加でまた散るので、**表示単位の整形をここ 1 箇所へ**
  集約する（設計哲学⑦＝単一源泉の精神）。
"""

KM_TO_M = 1000.0


def km_to_m(km):
    """km を m へ換算する（丸めない生値）。

    スカラーだけでなく numpy 配列も通す（グラフの距離軸をまとめて換算するため）。
    numpy に依存させたくないので乗算だけで書き、型は呼び出し側のものが返る。
    """
    return km * KM_TO_M


def format_distance(km: float, *, unit: bool = True) -> str:
    """距離 [km] を人が読む m 表記へ整形する（例: 20.153 → ``20,153 m``）。

    桁区切りを入れるのは**人が読む面だけ**（GUI パネル・HTML レポート・地図
    バッジ）。CSV など機械可読の出力には :func:`csv_distance` を使うこと。

    Args:
        km:   距離 [km]
        unit: 単位 ``m`` を付けるか（表の列見出しに単位がある場合は False）
    """
    m = round(float(km) * KM_TO_M)
    text = f"{m:,}"
    return f"{text} m" if unit else text


def csv_distance(km: float, *, decimals: int = 0) -> str:
    """距離 [km] を CSV 用の m 値へ整形する（桁区切りなし・単位なし）。

    表計算ソフトが数値として読めることが要件なので、``,`` は入れない。
    ``decimals`` は分解能の主張＝地形断面のように 0.1m 刻みを保ちたい面でだけ
    増やす（リンクバジェットの距離は m 整数で足りる）。
    """
    m = float(km) * KM_TO_M
    if decimals <= 0:
        return f"{round(m)}"
    return f"{m:.{decimals}f}"


# ============================================================
# F1 遮蔽率（2.5a3 / I-018）
# ------------------------------------------------------------
# models.calculate_propagation の blocked_ratio は「フレネル第1ゾーン半径に対して
# 障害物がどれだけ食い込んでいるか」で、**上限が無い**（深い山越えでは 7000% に
# なる＝実測）。これは侵入深さであって率ではないので、「率(%)」と名乗って出す面
# では 100% で頭打ちにする＝100% は「完全遮蔽」の意味。
#
# ⚠️ 打ち切るのは表示だけで **models の値は生のまま**（環境損失は既に
# `min(blocked_ratio, 100.0)` で内部的に扱っており、計算は生値の情報を使える）。
# 生の侵入深さを出したくなったら「×F1」という別の量として足す（→ 3.2 出力契約）。
# ============================================================

BLOCKED_RATIO_MAX = 100.0


def format_blocked_ratio(pct: float, *, unit: bool = True) -> str:
    """F1 遮蔽率 [%] を人が読む表記へ整形する（100% で頭打ち）。

    Args:
        pct:  models の blocked_ratio（上限なしの生値）
        unit: 単位 ``%`` を付けるか（列見出しに単位がある表では False）
    """
    v = min(float(pct), BLOCKED_RATIO_MAX)
    return f"{v:.1f} %" if unit else f"{v:.1f}"


def csv_blocked_ratio(pct: float) -> str:
    """F1 遮蔽率 [%] を CSV 用へ整形する（100% で頭打ち・単位なし）。

    表示と CSV で値が食い違うほうが混乱するので、CSV も同じ上限にする
    （生の侵入深さが要るなら別列として足す＝出力契約の話）。
    """
    return format_blocked_ratio(pct, unit=False)
