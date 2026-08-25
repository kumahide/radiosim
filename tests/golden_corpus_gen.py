"""
tests/golden_corpus_gen.py
==========================
回帰コーパス（`tests/data/golden_links.json`）の生成器。**手動実行のみ**
（`python tests/golden_corpus_gen.py`）。テスト実行では読み込まれない。

なぜ生成器を分けるか
--------------------
コーパスは「代表回線の入力一式＋実 DEM 由来の標高配列＋その時点の
`LinkBudgetResult` 全項目」を凍結したもので、**テストはネットワークに触らず
標高配列から再計算して突き合わせる**。地形取得（外部 API）と検証を分離しないと、
回帰テストが GSI サーバーの応答に依存してしまう。

物理モデルを**意図して**変えたときは、この生成器を再実行して JSON を作り直し、
**差分をレビューしてからコミットする**（差分＝全回線の数値がどう動いたかの
一覧そのもの＝影響範囲の把握手段）。意図しない差分が出たらそれが回帰。

⚠️ 実行すると GSI の DEM タイルを取得する（キャッシュ済みなら再取得しない）。
代表回線は 26 本・サンプル数は 120〜240 に抑えてある（[[feedback_design_philosophy]]
④ 外部 API 配慮＝無差別な広域取得をしない）。
"""

from __future__ import annotations

import json
import os
import sys
import threading

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from core import models          # noqa: E402
from core import simulation as sim   # noqa: E402

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "golden_links.json")

# 標高は 2 桁で丸めて保存し、**期待値も丸めた配列から計算する**
# （保存値と検証時の入力を完全に一致させる＝丸め起因の差を作らない）。
_ELEV_DECIMALS = 2


# ============================================================
# 代表回線（26 本）
# ------------------------------------------------------------
# 選定方針＝「実務で通る道を薄く広く」：
#   - 地形の性格：平地/河川沿い・尾根越え・山岳・市街地・島嶼/海越え
#   - 距離：2 km 〜 40 km（FSPL と回折の効き方が変わる帯域を跨ぐ）
#   - 周波数：400 MHz 〜 26 GHz（1 GHz 未満は降雨/大気減衰ゼロ・上は支配的）
#   - env_type 4 種・diff_method 2 種・降雨 0〜100 mm/h をコーパス全体で網羅
#   - 判定は OK / NG の両方が出るようにアンテナ高と感度を振る
# 座標はすべて日本国内の陸上（GSI DEM のカバー内）。
# ============================================================
LINKS: list[dict] = [
    # --- 市街地・短距離（都市減衰と回折の基礎） ---
    dict(id="tokyo_urban_2400", start=(35.6896, 139.7006), end=(35.6586, 139.7454),
         h_tx=45, h_rx=20, freq=2400, p_tx=20, gain_tx=12, gain_rx=12,
         sens=-85, veg_h=0, k=1.33, samples=160, env="urban", diff="deygout", rain=0),
    dict(id="osaka_urban_5600", start=(34.7025, 135.4959), end=(34.6789, 135.5200),
         h_tx=60, h_rx=15, freq=5600, p_tx=23, gain_tx=17, gain_rx=17,
         sens=-80, veg_h=0, k=1.33, samples=120, env="urban", diff="single", rain=30),
    dict(id="nagoya_suburb_900", start=(35.1709, 136.8815), end=(35.2286, 136.9067),
         h_tx=30, h_rx=10, freq=900, p_tx=20, gain_tx=9, gain_rx=9,
         sens=-95, veg_h=5, k=1.33, samples=160, env="suburban", diff="deygout", rain=0),
    dict(id="fukuoka_suburb_2400", start=(33.5902, 130.4207), end=(33.5150, 130.5245),
         h_tx=80, h_rx=60, freq=2400, p_tx=20, gain_tx=14, gain_rx=14,
         sens=-85, veg_h=10, k=1.33, samples=200, env="suburban", diff="deygout", rain=50),

    # --- 山越え・尾根（回折が主役／deygout と single の差が出る） ---
    dict(id="hiroshima_kure_ridge", start=(34.3853, 132.4553), end=(34.2493, 132.5657),
         h_tx=120, h_rx=80, freq=2400, p_tx=20, gain_tx=15, gain_rx=15,
         sens=-85, veg_h=15, k=1.33, samples=240, env="rural", diff="deygout", rain=0),
    dict(id="hiroshima_kure_single", start=(34.3853, 132.4553), end=(34.2493, 132.5657),
         h_tx=120, h_rx=80, freq=2400, p_tx=20, gain_tx=15, gain_rx=15,
         sens=-85, veg_h=15, k=1.33, samples=240, env="rural", diff="single", rain=0),
    dict(id="kobe_rokko", start=(34.6901, 135.1955), end=(34.7784, 135.2617),
         h_tx=60, h_rx=130, freq=5600, p_tx=23, gain_tx=20, gain_rx=20,
         sens=-80, veg_h=12, k=1.33, samples=200, env="rural", diff="deygout", rain=0),
    dict(id="kyoto_hiei", start=(35.0116, 135.7681), end=(35.0714, 135.8341),
         h_tx=60, h_rx=160, freq=400, p_tx=30, gain_tx=6, gain_rx=6,
         sens=-100, veg_h=20, k=1.33, samples=200, env="rural", diff="deygout", rain=0),
    dict(id="nara_ikoma", start=(34.6851, 135.8048), end=(34.6784, 135.6786),
         h_tx=30, h_rx=50, freq=2400, p_tx=20, gain_tx=15, gain_rx=15,
         sens=-85, veg_h=15, k=1.33, samples=200, env="rural", diff="single", rain=0),
    dict(id="sendai_zao", start=(38.2601, 140.8825), end=(38.1339, 140.6543),
         h_tx=160, h_rx=120, freq=900, p_tx=25, gain_tx=12, gain_rx=12,
         sens=-95, veg_h=18, k=1.33, samples=240, env="rural", diff="deygout", rain=0),

    # --- 山岳・長距離（曲率と k ファクタが効く） ---
    dict(id="fuji_kawaguchi", start=(35.5045, 138.7688), end=(35.4870, 138.6870),
         h_tx=110, h_rx=110, freq=2400, p_tx=20, gain_tx=18, gain_rx=18,
         sens=-85, veg_h=20, k=1.33, samples=200, env="rural", diff="deygout", rain=0),
    dict(id="fuji_ricek_low", start=(35.5045, 138.7688), end=(35.4870, 138.6870),
         h_tx=110, h_rx=110, freq=2400, p_tx=20, gain_tx=18, gain_rx=18,
         sens=-85, veg_h=20, k=1.00, samples=200, env="rural", diff="deygout", rain=0),
    dict(id="nagano_utsukushi", start=(36.6513, 138.1810), end=(36.2200, 138.1100),
         h_tx=260, h_rx=260, freq=400, p_tx=30, gain_tx=10, gain_rx=10,
         sens=-100, veg_h=25, k=1.33, samples=240, env="rural", diff="deygout", rain=0),
    dict(id="morioka_iwate", start=(39.7020, 141.1545), end=(39.8530, 141.0010),
         h_tx=40, h_rx=30, freq=900, p_tx=25, gain_tx=12, gain_rx=12,
         sens=-95, veg_h=20, k=1.33, samples=220, env="rural", diff="deygout", rain=0),
    dict(id="aomori_hakkoda", start=(40.8246, 140.7406), end=(40.6597, 140.8768),
         h_tx=45, h_rx=45, freq=2400, p_tx=23, gain_tx=18, gain_rx=18,
         sens=-85, veg_h=22, k=1.33, samples=220, env="rural", diff="single", rain=0),

    # --- 海越え・島嶼（低標高・見通し良好＝LoS 側の基準） ---
    dict(id="kagoshima_sakurajima", start=(31.5966, 130.5571), end=(31.5800, 130.6570),
         h_tx=70, h_rx=60, freq=5600, p_tx=23, gain_tx=20, gain_rx=20,
         sens=-80, veg_h=8, k=1.33, samples=160, env="los", diff="deygout", rain=0),
    dict(id="takamatsu_yashima", start=(34.3428, 134.0466), end=(34.3560, 134.1080),
         h_tx=45, h_rx=90, freq=15000, p_tx=18, gain_tx=30, gain_rx=30,
         sens=-75, veg_h=6, k=1.33, samples=140, env="los", diff="deygout", rain=0),
    dict(id="takamatsu_rain100", start=(34.3428, 134.0466), end=(34.3560, 134.1080),
         h_tx=45, h_rx=90, freq=15000, p_tx=18, gain_tx=30, gain_rx=30,
         sens=-75, veg_h=6, k=1.33, samples=140, env="los", diff="deygout", rain=100),
    dict(id="naha_urasoe", start=(26.2124, 127.6809), end=(26.2458, 127.7217),
         h_tx=55, h_rx=45, freq=26000, p_tx=15, gain_tx=35, gain_rx=35,
         sens=-70, veg_h=5, k=1.33, samples=120, env="los", diff="deygout", rain=50),
    dict(id="niigata_yahiko", start=(37.9161, 139.0364), end=(37.7070, 138.8290),
         h_tx=35, h_rx=55, freq=900, p_tx=25, gain_tx=12, gain_rx=12,
         sens=-95, veg_h=15, k=1.33, samples=240, env="rural", diff="deygout", rain=0),

    # --- かすめ（grazing）＝回折損が数 dB の帯（係数が最も見える所） ---
    # 深く塞がれた回線は diff_loss が飽和し current_k も 0 に張り付くため、
    # **0 < diff_loss < 数 dB／0 < current_k < initial_k の内挿帯**を必ず含める
    # （この帯が無いと回折・K 推定の係数を変えてもコーパスが気づけない）。
    dict(id="nagoya_grazing_900", start=(35.1709, 136.8815), end=(35.2286, 136.9067),
         h_tx=40, h_rx=10, freq=900, p_tx=20, gain_tx=9, gain_rx=9,
         sens=-95, veg_h=0, k=1.33, samples=160, env="suburban", diff="deygout", rain=0),
    dict(id="niigata_grazing_900", start=(37.9161, 139.0364), end=(37.7070, 138.8290),
         h_tx=10, h_rx=50, freq=900, p_tx=25, gain_tx=12, gain_rx=12,
         sens=-95, veg_h=0, k=1.33, samples=240, env="rural", diff="deygout", rain=0),

    # --- 植生・低アンテナ（veg_loss と NG 判定の基準） ---
    dict(id="veg_low_antenna", start=(35.3620, 138.7300), end=(35.3350, 138.6800),
         h_tx=3, h_rx=3, freq=2400, p_tx=20, gain_tx=6, gain_rx=6,
         sens=-85, veg_h=25, k=1.33, samples=180, env="rural", diff="deygout", rain=0),
    dict(id="veg_none", start=(35.3620, 138.7300), end=(35.3350, 138.6800),
         h_tx=3, h_rx=3, freq=2400, p_tx=20, gain_tx=6, gain_rx=6,
         sens=-85, veg_h=0, k=1.33, samples=180, env="rural", diff="deygout", rain=0),
    dict(id="sapporo_teine", start=(43.0621, 141.3544), end=(43.0870, 141.2100),
         h_tx=90, h_rx=160, freq=5600, p_tx=23, gain_tx=20, gain_rx=20,
         sens=-80, veg_h=18, k=1.33, samples=200, env="suburban", diff="deygout", rain=30),
    dict(id="shizuoka_nihondaira", start=(34.9756, 138.3828), end=(34.9683, 138.4650),
         h_tx=45, h_rx=75, freq=400, p_tx=30, gain_tx=6, gain_rx=6,
         sens=-100, veg_h=12, k=1.33, samples=160, env="suburban", diff="single", rain=0),
]


def _params(link: dict) -> sim.SimParams:
    return sim.SimParams({
        "start": f"{link['start'][0]}, {link['start'][1]}",
        "end":   f"{link['end'][0]}, {link['end'][1]}",
        "h_tx": str(link["h_tx"]), "h_rx": str(link["h_rx"]),
        "freq": str(link["freq"]), "p_tx": str(link["p_tx"]),
        "gain_tx": str(link["gain_tx"]), "gain_rx": str(link["gain_rx"]),
        "sens": str(link["sens"]), "veg_h": str(link["veg_h"]),
        "k_factor": str(link["k"]), "samples": str(link["samples"]),
        "env_type": link["env"], "diff_method": link["diff"],
        "rain_rate": str(link["rain"]),
    })


def _fetch(params: sim.SimParams) -> np.ndarray:
    """標高取得（非同期 API を同期化）。"""
    out: list[np.ndarray] = []
    err: list[Exception] = []
    done = threading.Event()
    sim.fetch_elevations(
        params,
        on_progress=lambda n: None,
        on_complete=lambda e: (out.append(e), done.set()),
        on_error=lambda ex: (err.append(ex), done.set()),
    )
    done.wait()
    if err:
        raise err[0]
    return out[0]


def build() -> dict:
    links_out = []
    for i, link in enumerate(LINKS, 1):
        params = _params(link)
        raw = np.round(_fetch(params), _ELEV_DECIMALS)
        terrain = models.calculate_terrain_profile(
            raw_elevs=raw,
            lat_tx=params.lat_tx, lon_tx=params.lon_tx,
            lat_rx=params.lat_rx, lon_rx=params.lon_rx,
        )   # ⚠️ earth_k は既定 4/3＝単一/バッチと同じ呼び方（params.k_factor は
            #    ライス K の表示値で曲率とは無関係＝production と同じ経路を通す）
        result = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)
        links_out.append({
            "id": link["id"],
            "input": {
                "lat_tx": params.lat_tx, "lon_tx": params.lon_tx,
                "lat_rx": params.lat_rx, "lon_rx": params.lon_rx,
                "h_tx": params.h_tx, "h_rx": params.h_rx,
                "freq_mhz": params.freq_mhz, "p_tx": params.p_tx,
                "gain_tx": params.gain_tx, "gain_rx": params.gain_rx,
                "sens": params.sens, "veg_h": params.veg_h,
                "k_factor": params.k_factor, "samples": params.num,
                "env_type": params.env_type, "diff_method": params.diff_method,
                "rain_rate": params.rain_rate,
            },
            "raw_elevs": [float(v) for v in raw],
            "expected": {
                "horiz_dist_km": terrain.horiz_dist_km,
                "eirp": result.eirp, "fspl": result.fspl,
                "diff_loss": result.diff_loss, "veg_loss": result.veg_loss,
                "env_loss": result.env_loss, "rain_loss": result.rain_loss,
                "gas_loss": result.gas_loss, "total_loss": result.total_loss,
                "p_rx": result.p_rx, "actual_margin": result.actual_margin,
                "status": result.status, "current_k": result.current_k,
                "blocked_ratio": result.blocked_ratio,
                "slant_dist_km": result.slant_dist_km,
                "diff_method": result.diff_method, "env_type": result.env_type,
            },
        })
        print(f"[{i:2d}/{len(LINKS)}] {link['id']:<26} "
              f"{terrain.horiz_dist_km:6.2f} km  {result.status:<3} "
              f"p_rx={result.p_rx:9.2f} dBm  margin={result.actual_margin:+8.2f} dB")
    return {
        "_comment": (
            "回帰コーパス（物理モデルのゴールデン固定）。生成器＝"
            "tests/golden_corpus_gen.py。手で編集しない＝モデルを意図して変えた"
            "ときだけ生成器を再実行し、差分をレビューしてコミットする。"
        ),
        "elev_decimals": _ELEV_DECIMALS,
        "links": links_out,
    }


def main() -> None:
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    data = build()
    # 原子的に置き換える＝途中で例外が出ても既存のコーパスを切らない
    # （`open(path, "w")` を直に開くと、その時点でファイルが 0 バイトになる）
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT_PATH)
    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(f"\nwrote {OUT_PATH}  ({size_kb:.0f} KB, {len(data['links'])} links)")


if __name__ == "__main__":
    main()
