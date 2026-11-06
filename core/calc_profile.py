"""
core/calc_profile.py
====================
計算プロファイル ID と、成果物の出所のハッシュ（ヘッドレス・純関数）。

🔑 **存在理由＝「何で計算した成果物か」を成果物だけから言えるようにする**（4.0 の中身 3）。
`docs` の約束は 2 つ＝①数字が動く直しは新しいプロファイルの側にだけ入れ、旧プロジェクトは
当時の計算の方式で開く ②成果物に「どの方式で・どの入力で計算したか」を刻む。

- **計算プロファイル**＝計算の方式の版。`v1` は 3.9.x の方式。製品の版とは別の番号で、
  改番しても変えない。
- **入力設定のハッシュ**＝計算を決める入力の同一性（CSV の `input_hash`）。
  🔑 **プロファイルを含めない**＝列が別にあるうえ、含めると「同じ入力を v1 と v2 で
  計算した 2 行」を突き合わせられなくなる。案件名・メモ・言語・座標の表示形式も
  含めない（計算を変えない）。
- **地形のハッシュ**＝標本の位置と標高列の同一性（帳票の刻印だけ・CSV には出さない）。
  🔑 **設定の同一性と地形の同一性は別のもの**＝同じ設定でも標高タイルを取り直せば
  数字は変わる。⇒ 2 つを分けて名乗る（判断点 ③）。

⚠️ **ハッシュの値には接頭辞が付く**（`i1-`・`t1-`・`m1-`）＝①16 進の字だけだと
`123e45678901` が表計算で数として読まれ、`012345678901` は先頭の 0 が落ちる
②数字は「何を畳んだかの版」＝入力の項目を増減した版は数字を上げる（古い値と黙って
不一致にしない・一致したふりもしない）。
"""

from __future__ import annotations

import hashlib
import json

import numpy as np

from core import dem_sources

#: 計算の方式の版＝3.9.x の方式。
V1: str = "v1"

#: 計算の方式の版＝4.0 の方式。🔑 数字が動く直しは**こちらの側にだけ**入れる（ステージ4）。
V2: str = "v2"

#: この版が知っているプロファイル（**閉じた集合**・`.rsproj` の読みの許可値）。
PROFILES: tuple[str, ...] = (V1, V2)

#: 新しい計算の既定。
DEFAULT_PROFILE: str = V1

#: プロファイルを持たない `.rsproj` の方式（判断点 ⑨）。
#: ⚠️ **`DEFAULT_PROFILE` と分けて持つ**＝既定を v2 へ切り替えた後も、3.x のファイルは
#: v1 で開く。既定に合わせて動かしてはいけない。
LEGACY_PROFILE: str = V1

_HASH_CHARS: int = 12

#: 入力設定のハッシュが畳む `SimParams` の項目（`input_hash` の残りは下で個別に扱う）。
_INPUT_ATTRS: tuple[str, ...] = (
    "lat_tx", "lon_tx", "lat_rx", "lon_rx", "h_tx", "h_rx",
    "freq_mhz", "p_tx", "gain_tx", "gain_rx", "sens",
    "veg_h", "k_factor", "rain_rate", "env_type", "diff_method",
)

#: `SimParams` の属性のうち、`_INPUT_ATTRS` の外にあるもの（理由つき）。
#: 🔒 `tests/test_calc_profile.py` が `SimParams` の全属性をここと突き合わせる＝
#: 入力を足したのにハッシュへ足し忘れる、を赤にする。
NOT_HASHED: dict[str, str] = {
    "calc_profile":        "別の列で名乗る（モジュールの docstring）",
    "resolution":          "`input_hash` が段階の語として畳む",
    "num":                 "段階から解ける。固定 N のときだけ `input_hash` が畳む",
    "sample_fracs":        "段階か固定 N から決まる",
    "sample_pixel_groups": "表示専用（物理に使わない）",
    "dem_source":          "`input_hash` が解決後のソースと定義の指紋として畳む",
}


def require(profile: str) -> str:
    """知っているプロファイルならそのまま返す（それ以外は `ValueError`）。

    ⚠️ **知らない語を既定へ倒さない**＝黙って別の方式で計算することになる。
    """
    if profile not in PROFILES:
        raise ValueError(f"unknown calculation profile: {profile!r}")
    return profile


def reads_bilinear(profile: str) -> bool:
    """その方式が DEM を**双線形**で読むか（v2・B-339）。偽＝点を含む画素の値（v1）。

    🔑 **読み方を決めるのはここ 1 か所**＝標本の置き方（`terrain_grid.path_sample_fractions`）
    と標高の読み（`dem.get_elevation`）と地形のキャッシュの鍵が同じ答えを引く。
    片方だけが v2 になると、v1 の縁の位置を双線形で読む（またはその逆）ことになる。
    """
    return require(profile) != V1


def two_ray_needs_clear_path(profile: str) -> bool:
    """その方式が、直接波の遮られた経路で地面反射の包絡線を出さないか（v2・B-338）。

    偽（v1）＝3.4〜3.9.x の方式＝遮蔽を見ずに、腕の長さ（`MIN_ARM_M`）だけで出す。
    """
    return require(profile) != V1


def _digest(prefix: str, payload: bytes) -> str:
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:_HASH_CHARS]}"


def _canonical(value):
    """同じ値を同じ字にする（`2400` と `2400.0` を分けない）。"""
    return value if isinstance(value, str) else float(value)


def input_hash(params, overrides: "dict | None" = None) -> str:
    """計算を決める入力のハッシュ（`input_hash` 列の値）。

    Args:
        params:    `core.simulation.SimParams`。
        overrides: 条件探索の上書き（`ScenarioPoint.overrides` と同じキー）。
            ⚠️ 条件探索は `SimParams` を行ごとに作らないので、**行の入力＝ベース＋上書き**。
    """
    merged = overrides or {}
    fields: dict[str, object] = {
        attr: _canonical(merged.get(attr, getattr(params, attr)))
        for attr in _INPUT_ATTRS
    }
    fields["resolution"] = params.resolution
    # ⚠️ **段階があれば点数は入れない**＝点数は段階から解いた結果で、入力ではない。
    fields["samples"] = None if params.resolution else params.num
    # 🔑 **解決後のソースで畳む**＝未知の `source_id` は国土地理院へ倒れるので、字面で畳むと
    # 同じ標高を読んだ 2 回が別物に見える。定義の指紋は `source_id` を変えずに URL だけ
    # 書き換えた宣言を見分ける（`dem_sources.definition_fingerprint` の B-236 と同じ根拠）。
    source = dem_sources.resolve(params.dem_source)
    fields["dem_source"] = source.source_id
    fields["dem_definition"] = dem_sources.definition_fingerprint(source)
    payload = json.dumps(fields, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return _digest("i1", payload.encode("utf-8"))


def _column_bytes(values, decimals: int) -> bytes:
    """列を、丸めて機械の表現に依らないバイト列にする（`nan` は別に印を持つ）。"""
    column = np.round(np.asarray(values, dtype=float), decimals) + 0.0  # -0.0 を 0.0 へ
    missing = np.isnan(column)
    return missing.tobytes() + np.where(missing, 0.0, column).astype("<f8").tobytes()


def terrain_hash(lats, lons, elevs) -> str:
    """標本の位置と標高列のハッシュ（帳票の刻印だけに出る）。

    丸めの桁＝緯度経度は 1e-7 度（約 1 cm）・標高は 0.01 m（`terrain_profile.csv` と同じ桁）。
    """
    payload = b"".join((_column_bytes(lats, 7), _column_bytes(lons, 7),
                        _column_bytes(elevs, 2)))
    return _digest("t1", payload)


def combined_hash(hashes) -> str:
    """複数のハッシュを、**並びごと**畳んだ 1 つの値にする（帳票 1 枚に N 本載る面）。

    入力設定は CSV の `input_hash` 列を上から順に渡せば同じ値が再現できる。
    """
    return _digest("m1", "\n".join(hashes).encode("ascii"))
