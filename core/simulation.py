"""
simulation.py
=============
ViewModel 相当のオーケストレーター。

責務:
  - 標高取得（別スレッド）のスケジューリング
  - models.py の各計算関数を順番に呼び出す
  - 結果を LinkBudgetResult として View に返す
  - 保存パッケージ（PNG / CSV / JSON / TXT）の生成

View はこのモジュールを呼ぶだけでよく、計算や I/O の詳細を知らない。
"""

import csv
import json
import logging
import os
import threading
from datetime import datetime
from typing import Callable

import numpy as np

from core import config
from core import coords
from core import dem
from core import disclosure
from core import failure
from core import i18n
from core import models
from core import output_contract
from core import terrain_grid
from core import units

logger = logging.getLogger("radiosim")


# ============================================================
# シミュレーションパラメータ
# ============================================================
def resolve_samples(
    lat_tx: float, lon_tx: float, lat_rx: float, lon_rx: float, level: str,
) -> tuple[int, float]:
    """2 地点と解像度の段階から `(点数, 実効間隔[m])` を返す（I-069）。

    🔑 **画面の読み取り欄と実行が、同じ値を同じ経路で得るための唯一の口**。
    ⚠️ 見せる側が自前で `recommended_samples` を呼ぶと、**画面に出た N と実際に
    使われた N がずれる余地**ができる（しかも「ずれた」ことは誰にも見えない）。
    """
    dist_m = models.horizontal_distance_km(
        lat_tx, lon_tx, lat_rx, lon_rx) * units.KM_TO_M
    n = terrain_grid.recommended_samples(dist_m, level)
    return n, terrain_grid.effective_spacing_m(dist_m, n)


def effective_spacing(params: "SimParams") -> float:
    """**その実行が実際に刻んだ**標本間隔 [m]（B-137）。

    🔑 `resolve_samples` との違いは**どちらを正典にするか**＝あちらは*入力の段階*
    から点数を解く口（画面の読み取り欄と実行が同じ答えを得るため）、こちらは
    **確定した点数から間隔を出す**口。⚠️ **見せる側が段階から計算し直すと、
    段階を経由しない実行（固定 N＝回帰コーパスの生成器・探針）で
    実際と食い違う**（実測＝800 点で刻んだのに「10.02m 間隔」と表示していた）。
    ⇒ *やったこと*を見せる面は、必ずこちらを通す。
    """
    dist_m = models.horizontal_distance_km(
        params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx) * units.KM_TO_M
    return terrain_grid.effective_spacing_m(dist_m, params.num)


class SimParams:
    """
    View から渡される実行パラメータ。
    文字列の設定値を型変換して保持する。

    🔑 **地形の標本数（`num`）はここで解く**（I-069）＝画面からは「解像度の段階」
    しか来ない。**距離は座標から地形取得の前に分かる**ので、`SimParams` が
    組み上がった時点で点数は確定できる。

    ⚠️ **この 1 か所で解くことに意味がある**＝バッチと中継は共通設定の段階を
    行／区間が引き継ぎ、`SimParams` は**行ごと・区間ごとに作られる**。だから
    「500m の行にも 20km の行にも同じ 200 点が当たる」という以前の形が、
    呼び出し側を 1 行も変えずに解消する（→ `report.batch._make_params`）。
    """
    def __init__(self, c: dict[str, str]) -> None:
        s_parts = c["start"].split(",")
        e_parts = c["end"].split(",")
        self.lat_tx:      float = float(s_parts[0].strip())
        self.lon_tx:      float = float(s_parts[1].strip())
        self.lat_rx:      float = float(e_parts[0].strip())
        self.lon_rx:      float = float(e_parts[1].strip())
        self.h_tx:        float = float(c["h_tx"])
        self.h_rx:        float = float(c["h_rx"])
        self.freq_mhz:    float = float(c["freq"])
        self.p_tx:        float = float(c["p_tx"])
        self.gain_tx:     float = float(c["gain_tx"])
        self.gain_rx:     float = float(c["gain_rx"])
        self.sens:        float = float(c["sens"])
        self.veg_h:       float = float(c["veg_h"])
        self.k_factor:    float = float(c["k_factor"])
        # 解像度の段階 → 点数。**段階が来ていればそれが正典**（画面はこちらだけ）。
        # ⚠️ `samples` は**再現のための固定入力**の口として残す（回帰コーパスの
        # 生成器・保存済みの解決後 N を読み戻す経路）。画面からは決して来ない
        # ＝「同じことを言う入口が 2 つ」にはならない（入口は段階ただ 1 つ）。
        # 🔴 **固定 N モードでは段階を名乗らない**（B-137・2026-08-29 の独立レビュー）
        #    ＝以前はここで `resolution` を既定値（`medium`）で埋めていたため、
        #    **800 点で刻んだ実行が「中」を名乗り**、①`settings.json` に書いた段階を
        #    読み戻すと点数が別物になり（800 → 288）②帳票の実効間隔が段階から
        #    計算し直されて実際と食い違い（3.60m を 10.02m と表示）③**B-128 の刻印が
        #    使っていない段階を名乗った**。空にすると `models.scope_notes` の
        #    「知らない語・空なら刻印を出さない」と噛み合う＝*言えないなら名乗らない*。
        level = str(c.get("resolution", "") or "")
        if level:
            self.resolution: str = level
            self.num: int = resolve_samples(
                self.lat_tx, self.lon_tx, self.lat_rx, self.lon_rx,
                self.resolution,
            )[0]
        else:
            self.resolution = ""
            self.num = max(terrain_grid.SAMPLES_MIN, int(c["samples"]))
        # ⚠️ **旧名 `deygout` はここで受ける**（入力の境目 1 か所・B-130）＝
        #    5 フローすべてがこの `SimParams` を通るので、ここだけで全部が揃う。
        self.diff_method: str   = models.normalize_diff_method(
            c.get("diff_method", models.DIFF_METHOD_MULTI))
        self.env_type:    str   = c.get("env_type", models.ENV_DEFAULT)
        self.rain_rate:   float = float(c.get("rain_rate", "0.0"))


# ============================================================
# 標高取得スレッド
# ============================================================
# DEM 並列取得の最大ワーカー数。
# GSI サーバーへの過負荷とタイルキャッシュのロック競合を避けるため上限を設ける。
_MAX_FETCH_WORKERS: int = 8

# 打ち切りの敷居（B-025 ②）。**1 点も取れないまま通信の失敗がこの数に達したら
# 中止する**。並列度と同じ数＝「最初の 1 巡が全滅した」ことを意味する。
#
# なぜ「1 点も取れないまま」を条件に足すか：数点の失敗は日常的に起こり得る
# （タイル 1 枚のタイムアウト、混雑時の 429）。それで経路全体を落とすと、
# 従来動いていた実行が突然エラーになる。**打ち切りたいのは「そもそも外へ出られて
# いない」場合だけ**（Proxy 未設定・NW 断）で、その形は必ず「成功 0 件」になる。
#
# ⚠️ 敷居を上げると待ち時間がそのまま伸びる＝1 点あたり最大 15 秒
# （timeout 5 秒 × レイヤ 3 段）。8 点は並列に走るので、実測の体感は「1 巡 ＝
# 最大 15 秒で打ち切り」。従来は全点ぶん繰り返していた（200 点なら数十分）。
_DEM_FAILURE_LIMIT: int = _MAX_FETCH_WORKERS


class DemUnreachableError(failure.UserFacingError, RuntimeError):
    """DEM をまったく取得できずに打ち切ったことを表す（B-025 ②）。

    **平坦な地形を正常値の顔で返さない**ための唯一の出口。3 フロー（単一・
    バッチ・条件探索）とも `on_error` がダイアログへ出すので、これを投げれば
    どのフローでもユーザーに届く。

    ⚠️ **`UserFacingError` を継承しているのは「文が既に型に乗っている」印**
    （I-100）＝`err_dem_unreachable` は何が起きた／なぜ止めた／次の一手を全部
    持つ。これが無いと、受け側が「実行を完了できませんでした」でもう 1 枚
    包み、**同じことを 2 回言う**。
    """


def fetch_elevations(
    params: SimParams,
    on_progress: Callable[[int], None],
    on_complete: Callable[[np.ndarray], None],
    on_error: Callable[[Exception], None],
) -> None:
    """
    GSI DEM から標高を並列取得する。別スレッドで実行すること。

    daemon=True Thread + Semaphore で最大 _MAX_FETCH_WORKERS 並列にリクエストを投げ、
    完了した件数を on_progress に通知する。結果は座標インデックス順に整列して返す。

    Args:
        params:      シミュレーションパラメータ
        on_progress: 1サンプル完了するたびに呼ばれる (完了済み件数: int)
        on_complete: 全取得完了時に呼ばれる (raw_elevs: np.ndarray)
        on_error:    例外発生時に呼ばれる
    """
    def _run() -> None:
        try:
            logger.info(
                "Simulation started: start=(%s,%s) end=(%s,%s) freq=%.1f MHz samples=%d",
                params.lat_tx, params.lon_tx,
                params.lat_rx, params.lon_rx,
                params.freq_mhz, params.num,
            )
            lats = np.linspace(params.lat_tx, params.lat_rx, params.num)
            lons = np.linspace(params.lon_tx, params.lon_rx, params.num)

            raw_elevs: list[float] = [0.0] * params.num
            completed  = 0
            failures   = 0
            successes  = 0
            lock       = threading.Lock()
            sem        = threading.Semaphore(_MAX_FETCH_WORKERS)

            worker_error: list[Exception] = []

            def _fetch_one(idx: int, la: float, lo: float) -> None:
                nonlocal completed, failures, successes
                failed = True          # 何が起きても finally で参照できる初期値
                try:
                    raw_elevs[idx] = dem.get_elevation(la, lo)
                    # 「取れなかった」は戻り値に出ない（0.0 は海抜 0m と同じ顔）。
                    # 直後に聞くのが唯一の判別手段＝B-025 ②。
                    failed = dem.network_failed()
                except Exception as e:
                    failed = True
                    with lock:
                        worker_error.append(e)
                finally:
                    with lock:
                        completed += 1
                        if failed:
                            failures  += 1
                        else:
                            successes += 1
                        on_progress(completed)
                    # ⚠️ 解放は**カウンタを更新しきってから**。先に解放すると、
                    # 下の「全ワーカーの終了待ち」が数え終える前に抜ける。
                    sem.release()

            def _should_abort() -> bool:
                with lock:
                    return successes == 0 and failures >= _DEM_FAILURE_LIMIT

            for i, (la, lo) in enumerate(zip(lats, lons)):
                if _should_abort():
                    break                      # これ以上投げない（待ち時間を捨てる）
                sem.acquire()
                threading.Thread(
                    target=_fetch_one, args=(i, la, lo), daemon=True
                ).start()

            # 全ワーカーの終了を待つ＝許可証を全部回収できたら誰も走っていない。
            # （完了件数のイベントで待つと、打ち切って投げ終えなかったぶん永久に
            #   揃わない。「投げた数」を数え直すより許可証を数える方が確実。）
            for _ in range(_MAX_FETCH_WORKERS):
                sem.acquire()

            if worker_error:
                raise worker_error[0]

            if _should_abort():
                logger.error(
                    "Terrain fetch aborted: %d consecutive DEM failures with no "
                    "success (check proxy settings). start=(%.6f,%.6f) end=(%.6f,%.6f)",
                    failures, params.lat_tx, params.lon_tx,
                    params.lat_rx, params.lon_rx,
                )
                raise DemUnreachableError(i18n.t("err_dem_unreachable"))

            logger.info("Terrain fetch complete: %d samples", params.num)
            on_complete(np.array(raw_elevs))

        except Exception as ex:
            on_error(ex)

    threading.Thread(target=_run, daemon=True).start()


# ============================================================
# 地形キャッシュ
# ============================================================
# 地形取得に影響するパラメータをキーとして raw_elevs を保持する。
# lat_tx / lon_tx / lat_rx / lon_rx / num が一致すれば再取得しない。
# k_factor は raw_elevs に影響しない（曲率補正は calculate_terrain_profile で適用）
# ため、キャッシュキーには含めない。
_TerrainCacheKey = tuple[float, float, float, float, int]
_terrain_cache: dict[_TerrainCacheKey, np.ndarray] = {}
_terrain_cache_lock = threading.Lock()


def _terrain_cache_key(params: SimParams) -> _TerrainCacheKey:
    return (params.lat_tx, params.lon_tx, params.lat_rx, params.lon_rx, params.num)


def _is_total_dem_failure(raw_elevs: np.ndarray) -> bool:
    """全点が厳密に 0.0＝DEM が 1 点も取れなかった形か。

    `dem.get_elevation` は全レイヤ失敗時に「取れなかった」ではなく `0.0` を返す
    ため、Proxy 未設定などで取得が全滅すると**標高 0m の平坦地形**が正常値の顔で
    出てくる（ISSUES.md B-025）。戻り値契約そのものの是正は呼び出し側 ~30 箇所と
    出力契約に触るので 3.x 送りだが、**その値が地形キャッシュへ焼き付いて
    「Proxy を直してもアプリを再起動するまで直らない」状態になる**のはここで防げる。

    海上だけを通る経路も all 0 になり得る。その場合に払う代償は「毎回取り直す」
    だけで、誤った平坦地形を配り続けるより安い。
    """
    return raw_elevs.size > 0 and bool(np.all(raw_elevs == 0.0))


def fetch_elevations_cached(
    params: SimParams,
    on_progress: Callable[[int], None],
    on_complete: Callable[[np.ndarray], None],
    on_error: Callable[[Exception], None],
) -> None:
    """
    キャッシュ付き標高取得。

    TX/RX 座標とサンプル数が前回と同じであれば DEM を再取得せず、
    キャッシュした raw_elevs を即座に on_complete へ渡す。
    変更があった場合は fetch_elevations を呼び出してキャッシュを更新する。
    """
    key = _terrain_cache_key(params)

    with _terrain_cache_lock:
        cached = _terrain_cache.get(key)

    if cached is not None:
        logger.info(
            "Terrain cache hit: start=(%.6f,%.6f) end=(%.6f,%.6f) samples=%d",
            params.lat_tx, params.lon_tx,
            params.lat_rx, params.lon_rx,
            params.num,
        )
        # プログレスバーを満杯にしてから完了通知（UI の一貫性のため）
        on_progress(params.num)
        on_complete(cached.copy())
        return

    # キャッシュミス → 実取得してキャッシュに保存
    def _on_complete_and_cache(raw_elevs: np.ndarray) -> None:
        if _is_total_dem_failure(raw_elevs):
            # 取得が全滅した結果は保持しない（B-025）。結果自体は今までどおり
            # 返す＝ここで握り潰すと「何も起きない」になるため。呼び出し側への
            # 失敗の伝播と画面での提示は別途（B-025 の ②③）。
            logger.warning(
                "Terrain NOT cached: every sample is 0.0 — DEM fetch likely failed "
                "for the whole path (check proxy settings). "
                "start=(%.6f,%.6f) end=(%.6f,%.6f) samples=%d",
                params.lat_tx, params.lon_tx,
                params.lat_rx, params.lon_rx,
                params.num,
            )
        else:
            with _terrain_cache_lock:
                _terrain_cache[key] = raw_elevs.copy()
        on_complete(raw_elevs)

    fetch_elevations(params, on_progress, _on_complete_and_cache, on_error)


def clear_terrain_cache() -> None:
    """地形キャッシュを全消去する（テスト・デバッグ用）。"""
    with _terrain_cache_lock:
        _terrain_cache.clear()


# ============================================================
# 計算（スライダー変更時など随時呼び出し）
# ============================================================
def run_calculation(
    terrain: models.TerrainProfile,
    h_tx: float,
    h_rx: float,
    params: SimParams,
    rain_rate: float | None = None,
) -> models.LinkBudgetResult:
    """
    TerrainProfile と現在のアンテナ高から LinkBudgetResult を返す。
    GUI スレッドから直接呼んでよい（純粋計算のみ）。

    Args:
        rain_rate: None のとき params.rain_rate を使用。
                   グラフのスライダーから直接渡す場合は float を指定。
    """
    _rain = params.rain_rate if rain_rate is None else rain_rate
    prop   = models.calculate_propagation(
        terrain     = terrain,
        h_tx        = h_tx,
        h_rx        = h_rx,
        freq_mhz    = params.freq_mhz,
        veg_h       = params.veg_h,
        initial_k   = params.k_factor,
        diff_method = params.diff_method,
        env_type    = params.env_type,
        rain_rate   = _rain,
    )
    result = models.calculate_link_budget(
        prop     = prop,
        freq_mhz = params.freq_mhz,
        p_tx     = params.p_tx,
        gain_tx  = params.gain_tx,
        gain_rx  = params.gain_rx,
        sens     = params.sens,
    )
    return result


# ============================================================
# 保存パッケージ
# ============================================================
def save_package(
    terrain: models.TerrainProfile,
    result: models.LinkBudgetResult,
    params: SimParams,
    h_tx: float,
    h_rx: float,
    coord_format: str = "dd",
) -> str:
    """
    結果一式をタイムスタンプ付きディレクトリに保存する。
    保存先ディレクトリのパスを返す。

    coord_format は **人が読む report.txt の座標表記のみ**に効く（"dd"|"dms"）。
    settings.json は再読込のため常に DD（[[feedback-radiosim-rules]] のデータ=DD原則）。
    既定 DD なのでヘッドレス呼び出しは表示設定に非依存。
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    save_dir  = os.path.join(config.RESULTS_DIR, timestamp)
    os.makedirs(save_dir, exist_ok=True)

    _save_settings(params, h_tx, h_rx, save_dir)
    _save_terrain_csv(terrain, save_dir)
    _save_report(result, params, h_tx, h_rx, save_dir, coord_format)

    logger.info("Package saved: %s", save_dir)
    return save_dir


def _save_settings(
    params: SimParams,
    h_tx: float,
    h_rx: float,
    save_dir: str,
) -> None:
    settings = {
        "start"       : f"{params.lat_tx}, {params.lon_tx}",
        "end"         : f"{params.lat_rx}, {params.lon_rx}",
        "h_tx"        : h_tx,
        "h_rx"        : h_rx,
        "freq"        : params.freq_mhz,
        "p_tx"        : params.p_tx,
        "gain_tx"     : params.gain_tx,
        "gain_rx"     : params.gain_rx,
        "sens"        : params.sens,
        "veg_h"       : params.veg_h,
        "k_factor"    : params.k_factor,
        # 🔑 **入力（段階）と結果（点数）の両方を残す**（I-069）＝読み戻すのは
        # `resolution` の側（`config.select_sim` が拾うのはこちらだけ）。`samples`
        # は**その実行が実際に何点で刻んだか**の記録で、入力としては使われない。
        # ⚠️ **段階が無い実行（固定 N）では書かない**（B-137）＝空でも書くと、
        # 読み戻したときに「段階がある」側の枝へ落ちて**点数が別物になる**。
        # ⚠️ **これで「製品から再現できる」わけではない**（B-140＝この直しの申告が
        # 広すぎた）＝画面の「パラメータ読込」は `config.select_sim` を通り、
        # `samples` は `SIM_KEYS` に無いので捨てられる。固定 N を読み戻せるのは
        # `SimParams` を直に組む側（生成器・探針）だけ＝**入口は段階ただ 1 つ**
        # という I-069 の決定そのもの。
        **({"resolution": params.resolution} if params.resolution else {}),
        "samples"     : params.num,
        "diff_method" : params.diff_method,
        "env_type"    : params.env_type,
        "rain_rate"   : params.rain_rate,
    }
    path = os.path.join(save_dir, "settings.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)


def _save_terrain_csv(terrain: models.TerrainProfile, save_dir: str) -> None:
    path = os.path.join(save_dir, "terrain_profile.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # 見出しは出力契約が単一ソース（→ core/output_contract.py）。
        writer.writerow(list(output_contract.TERRAIN_CSV_COLUMNS))
        for d, h in zip(terrain.d_km_axis, terrain.raw_elevs):
            writer.writerow([round(units.km_to_m(float(d)), 1), round(float(h), 2)])


def _save_report(
    result: models.LinkBudgetResult,
    params: SimParams,
    h_tx: float,
    h_rx: float,
    save_dir: str,
    coord_format: str = "dd",
) -> None:
    tx_site = coords.format_pair(params.lat_tx, params.lon_tx, coord_format)
    rx_site = coords.format_pair(params.lat_rx, params.lon_rx, coord_format)
    # ⚠️ **段階からではなく、実際に刻んだ点数から出す**（B-137）。
    spacing = effective_spacing(params)
    text = (
        "=== RADIO LINK REPORT ===\n\n"
        f"Date: {datetime.now()}\n\n"
        "[SITE INFO]\n"
        f"TX Site       : {tx_site}\n"
        f"RX Site       : {rx_site}\n"
        f"TX Height     : {h_tx:.1f} m\n"
        f"RX Height     : {h_rx:.1f} m\n\n"
        "[RADIO SETTINGS]\n"
        f"Frequency     : {params.freq_mhz} MHz\n"
        f"TX Power      : {params.p_tx} dBm\n"
        f"TX Ant. Gain  : {params.gain_tx} dBi\n"
        f"RX Ant. Gain  : {params.gain_rx} dBi\n"
        f"Sensitivity   : {params.sens} dBm\n\n"
        "[LINK BUDGET]\n"
        f"Diff Model    : {result.diff_method}\n"
        f"Env Type      : {result.env_type}\n"
        # 桁は `units.format_db` が単一ソース（0.1 dB＝持っている精度）。
        f"EIRP          : {units.format_db(result.eirp, unit='dBm')}\n"
        f"FSPL          : {units.format_db(result.fspl, unit='dB')}\n"
        f"Diffraction   : {units.format_db(result.diff_loss, unit='dB')}\n"
        f"Vegetation    : {units.format_db(result.veg_loss, unit='dB')}\n"
        f"Env Loss      : {units.format_db(result.env_loss, unit='dB')}\n"
        f"Rain Loss     : {units.format_db(result.rain_loss, unit='dB')}\n"
        f"Gas Loss      : {units.format_db(result.gas_loss, unit='dB')}\n"
        f"Total Loss    : {units.format_db(result.total_loss, unit='dB')}\n"
        f"RX Level      : {units.format_db(result.p_rx, unit='dBm')}\n"
        f"Sensitivity   : {units.format_db(params.sens, unit='dBm')}\n"
        f"Act Margin    : {units.format_db(result.actual_margin, unit='dB')}\n"
        f"Status        : {result.status}\n\n"
        "[ENVIRONMENT]\n"
        f"Initial K     : {params.k_factor:.2f}\n"
        f"Rice K (est.) : {result.current_k:.2f}\n"
        f"F1 Obstruct   : {units.format_blocked_ratio(result.blocked_ratio)}\n"
        f"F1 Depth      : {units.format_f1_depth(result.blocked_ratio)}\n"
        f"Slant Dist    : {units.format_distance(result.slant_dist_km)}\n"
        # どれだけ細かく地形を見た答えなのか（I-069）。**段階だけでは足りない**
        # ＝天井に張り付くと「高」でも実効間隔は粗くなる（数字のほうが正典）。
        # ⚠️ 段階を経由しない実行（固定 N）は**段階を名乗らない**（B-137）。
        f"Terrain Res   : {params.resolution or '(fixed sample count)'}\n"
        f"Samples       : {params.num} "
        f"({units.format_spacing(spacing)} m spacing)\n\n"
        # 「結果の取扱に関する補足」（3.0a1）＝HTML の帳票と**同じ 1 本**を引く
        # （report.txt だけ開示を持たない、が起きないように）。
        + disclosure.handling_text(models.scope_notes(
            params.freq_mhz,
            diff_method=result.diff_method,
            rain_rate=params.rain_rate,
            veg_h=params.veg_h,
            resolution=params.resolution,
        ))
    )
    path = os.path.join(save_dir, "report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
