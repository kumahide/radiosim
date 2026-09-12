"""
dem.py
======
国土地理院 DEM PNG タイルの**1 点の標高取得**・ディスクキャッシュ I/O・
淡色地図タイル取得を担う。

  - HTTP セッション / プロキシ管理
  - GSI DEM PNG タイル取得（優先順位降下）と標高デコード
  - 淡色地図タイル取得（レポート添付の経路地図用）
  - タイル座標変換（順変換）・タイルキャッシュの読み書き（原子的）

⚠️ **「1px が何 m か／何点で刻むか」はここに無い**＝純粋な層
（[core/terrain_grid.py](terrain_grid.py)）へ独立させた（I-069）。
⚠️ **面での事前取得はここに無い**＝[core/dem_prefetch.py](dem_prefetch.py)
（B-141）。**キャッシュの棚卸し・カバレッジ表示・削除もここに無い**＝
[core/dem_cache.py](dem_cache.py)（3.3 段4a）。切り口はどちらも*関心事*＝
「いま要る 1 点」（ここ）と「すでに在るものの管理／これから要る面」は寿命が
違う。

ネットワーク（requests）・PIL・numpy への依存はここに閉じ込める。ロギングだけは
アプリ共通の logger（config.py）を借りる（config → dem の一方向依存・逆流なし）。
infrastructure.py を config.py ＋ dem.py（本体）へ分割した際に切り出した DEM 層。
"""

import io
import math
import os
import queue
import threading
import time
import urllib.request
from datetime import date

import numpy as np
import requests
from PIL import Image

from core import dem_sources
from core import terrain_grid
from core import version
from core.config import cache_log_base_dir, logger

# ============================================================
# DEM タイルキャッシュのルートディレクトリ
#   基準は config.cache_log_base_dir()（ポータブル＝exe／スクリプトの位置、
#   非ポータブル＝%LOCALAPPDATA%\RadioSim）で cwd に依存しない（B-014）。
#   テストは dem.CACHE_DIR を monkeypatch して一時ディレクトリへ差し替える。
# ============================================================
CACHE_DIR = os.path.join(cache_log_base_dir(), "terrain_cache")

# ============================================================
# DEM タイルクライアント
# ============================================================

# 利用する DEM レイヤーの優先順位リスト（高精度順）
# (layer_id, zoom)
#   layer_id は国土地理院タイルの正式 ID（末尾 _png が必須）
#   参照: https://maps.gsi.go.jp/development/ichiran.html
#
#   dem5a_png : 5m メッシュ（航空レーザ測量）  zoom=15  最優先
#   dem5b_png : 5m メッシュ（写真測量）        zoom=15  dem5a_png より広域
#   dem_png   : 10m メッシュ（基盤地図情報）   zoom=14  全国カバー
#
# ※ dem1a_png（1m）はカバレッジが限定的で取得失敗が頻発するため除外
#
# 🔑 **単一ソースの正典は [core/dem_sources.py](dem_sources.py) の
# `GSI_DEM`**（3.3 段4c＝DEM ソースの宣言ファイル）＝ここはその写し。
DEM_LAYERS: list[tuple[str, int]] = list(dem_sources.GSI_DEM.layers)
# ⚠️ **この層構成から導かれる「1px が何 m か」と「何点で刻むか」は
# [core/terrain_grid.py](terrain_grid.py)** ＝純粋な層として独立させてある
# （設定層がネットワーク依存なしに引けるようにするため・I-069）。
#
# ==============================================================================
# 📜 **標高ソースを増やす前に固定する規則**（3.3 段4b・国土地理院以外の
# ソースを [core/dem_sources.py](dem_sources.py)（段4c）へ足す前の設計拘束）
# ==============================================================================
# 🔑 **「1 回の計算で標高ソースを混ぜない」**＝1 本の経路の標高取得（`get_elevation`
# の 1 回の呼び出し列＝`DEM_LAYERS` の降下）の中で、**基準面（datum・ジオイド）の
# 違う複数ソースを使い分けてはならない**。
#
# なぜか＝現行の 5a→5b→10m の降下は「点ごとに」フォールバックするので、基準面が
# 揃っている（同じ国土地理院）うちは実害が無いが、**基準面の違う外部ソースが
# 混ざると 1 本の経路の中で標高の基準がすり替わる**。この製品は地形の**相対形状**
# で判定する（回折損の計算は経路上の高低差を見る）ので、経路全体で一様なオフセット
# なら実害は小さいが、**点ごとに基準がすり替わる混在は直接効く**（山の裏に回り込んだ
# ように見えたり、その逆が起きたりする＝地形のシルエットそのものが歪む）。
#
# ⚠️ **この規則が禁じるのは「標高ソースのすり替え」であって「レイヤの重ね合わせ」
# ではない**（2026-09-06 の検討で書き分けた）。禁じているのは**1 本の経路の中で
# 標高の基準がすり替わること**であって、**同じ基準の地表面の上に別の量を積むこと**
# ではない。例＝入力の忠実度のために建物・樹冠・clutter を DEM へ焼き込まず**別
# レイヤで保持する**のは「重ねる」であって「混ぜる」ではないので、この規則の対象外
# （このどちらも 3.3 時点では未実装＝標高ソースは国土地理院のみ、レイヤ合成もまだ
# 無い。ここは 4c 以降の実装が従うべき拘束を先に書いただけ）。
#
# 実装への指示＝将来 `DEM_LAYERS` 相当の構成を複数プロバイダへ拡張するときは、
# **1 回の `get_elevation` 呼び出し列（＝1 経路の標高取得）の中では単一ソースの
# レイヤ群のみを降下する**（プロバイダをまたいだ降下フォールバックをしない）。

_MAX_PREFETCH_WORKERS: int = 8

# ディスクキャッシュのタイルを読むときの粘り（B-123）。
# `os.replace` が走っている一瞬だけ Windows は置換先を開かせないので、
# そこで諦めるとその点が 0.0 に化ける。置換は数ミリ秒で終わる。
_TILE_READ_ATTEMPTS: int = 3
_TILE_READ_RETRY_S: float = 0.01

# 淡色地図（レポート添付の経路オーバーレイ地図 = report_map.py が使用）。
# DEM レイヤーと違いズームが可変なので、キャッシュパスにズームを含めて
# 異なるズームの同一 (x, y) が衝突しないようにする（DEM は層ごとズーム固定）。
BASEMAP_LAYER:  str = "pale"
BASEMAP_SUBDIR: str = "basemap_pale"

# ============================================================
# HTTP セッション管理
# ============================================================
_proxy_url: str = ""
_http_session: "requests.Session | None" = None
_session_lock = threading.Lock()


def set_proxy(url: str) -> None:
    """プロキシURLを設定してセッションをリセットする。空文字はOSのプロキシ設定を使う。"""
    global _proxy_url, _http_session
    _proxy_url = url.strip()
    with _session_lock:
        if _http_session is not None:
            _http_session.close()
        _http_session = None
    with _cache_lock:
        _failed_tiles.clear()
    logger.info("Proxy configured: %r", _proxy_url or "(system)")


def _get_session() -> "requests.Session":
    global _http_session
    with _session_lock:
        if _http_session is None:
            s = requests.Session()
            s.headers.update({"User-Agent": version.USER_AGENT})
            if _proxy_url:
                s.proxies = {"http": _proxy_url, "https": _proxy_url}
            else:
                s.proxies = urllib.request.getproxies() or {}
            _http_session = s
        return _http_session


# キャッシュキーは (layer_id, xtile, ytile) の 3 要素
# _cache_lock は _tile_cache と _failed_tiles の両方を保護する。
# ロック保持中にネットワーク取得を行ってはいけない（並列化が無効になる）。
# ガード: tests/test_dem.py::TestGetElevation
#         ::test_network_fetch_runs_without_holding_the_cache_lock
_tile_cache: dict[tuple, np.ndarray] = {}
_cache_lock = threading.Lock()

# 恒久的に存在しないタイル（HTTP 404）のセット。再リクエスト防止のための
# 負キャッシュ。_cache_lock で保護する。
#   ★ここに入れてよいのは「取得しても永久に無い」タイルだけ（日本域外・海上で
#     GSI が 404 を返すもの）。タイムアウト・接続エラー・5xx/429 のような
#     "一時失敗" を入れてはならない（回復後もそのタイルを無視し続け、標高が
#     0.0 や粗レイヤ値に化けたまま黙って誤るため = B-010）。登録は _fetch_tile が
#     HTTP ステータスを見て 404 のときだけ行う（唯一ステータスを知る場所）。
# ガード: tests/test_dem.py::TestFailedTileNegativeCache
_failed_tiles: set[tuple] = set()

# タイルの「読める/壊れている」検証結果のメモ（B-143）。
# キー=キャッシュパス、値=((mtime_ns, size), 読めたか)。_cache_lock で保護する。
#
# 存在だけ見る走査（dem_cache._scan_cached_positions）は速いが、壊れたタイルを取得済み
# として扱う（B-143）。かといって走査のたびに毎回 Image.open で検証すると
# 対話 UI（パン/ズームのたび）に対して重すぎる（2026-08-29 実測: 536 枚で
# +169ms・1 万枚で約 3.2 秒）。⇒ 検証結果を (mtime_ns, size) 付きで憶えておき、
# stat が前回と一致する限り再検証しない。ファイルは書くときだけ変わる
# （タイルは URL=内容で不変・上書きは壊れタイルの置換のみ）ので、通常運用は
# 「一度読めば以後はほぼ existence-only 相当」に落ち着く。
# ガード: tests/test_dem.py::TestScanCachedPositions::test_broken_tile_excluded
_tile_validity_memo: dict[str, tuple[tuple[int, int], bool]] = {}


def _is_tile_readable_memoized(cache_path: str, st: "os.stat_result") -> bool:
    """`cache_path` が読めるかを (mtime_ns, size) 付きメモで判定する（B-143）。

    stat が前回検証時と変わっていなければメモの結果をそのまま返す＝
    Image.open による復号（重い）を省く。変わっていた（新規/上書き）ときだけ
    実際に読んで検証し、メモを更新する。
    """
    key = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        cached = _tile_validity_memo.get(cache_path)
    if cached is not None and cached[0] == key:
        return cached[1]
    valid = _read_cached_tile(cache_path) is not None
    with _cache_lock:
        _tile_validity_memo[cache_path] = (key, valid)
    return valid


# ------------------------------------------------------------
# 「取れなかった」を知らせる口（B-025 ②③・当初は戻り値を変えずに知らせていたが
# 3.2 で `get_elevation` の戻り値そのものが `nan` を返せるようになった＝③参照）
# ------------------------------------------------------------
# `get_elevation` は通信の失敗が絡む「取れなかった」を `nan` で返す（3.2）。
# 呼び出し側が「取れなかった」ことに気づけないと、Proxy 未設定の環境で**平坦な
# 地形を正しい顔で配り続ける**。戻り値（`nan` かどうか）だけでも判別できるが、
# **直前の呼び出しが通信の失敗で終わったか**をスレッドローカルにも置いておく
# （B-025 ②の打ち切り判定・③の `nan` 分岐が両方ここを読む＝二重に持たない）。
#
# なぜスレッドローカルか：標高取得は 1 点 1 スレッドで並列に走る（simulation の
# ワーカー）ので、モジュール変数だと隣の点の結果を読む。呼ぶ側は自分のスレッドで
# `get_elevation` → `network_failed()` と続けて読むだけでよい。
#
# ⚠️ **記録するのは通信の失敗だけ**（タイムアウト・接続エラー・5xx/429）。
# **404 は含めない**＝あれは「そこに標高データが永久に無い」＝海上・日本域外で
# 正常に起きることで、通信は成功している。ここを混ぜると**海の上を通る経路が
# 「ネットワーク異常」として打ち切られる**（B-010 で負キャッシュに一時失敗を
# 混ぜたのと鏡像の誤り）。
#
# 🔑 **不変条件**（3.2）＝`get_elevation()` が `nan` を返す ⟺ 直後の
# `network_failed()` が真。どちらか一方だけを直す変更をしないこと。
_network_trouble = threading.local()


def network_failed() -> bool:
    """**このスレッドの直前の `get_elevation`** が通信の失敗で終わったか。

    3.2 以降はこれと同じ答えを `math.isnan(get_elevation(...))` でも得られる
    （不変条件＝上のコメント）。既存の打ち切りロジック（B-025 ②）は
    こちらを読み続ける。
    """
    return bool(getattr(_network_trouble, "flag", False))


# 🔑 **直前の `get_elevation` が値を返したタイル**（B-213）＝出所刻印「取得日」の
# 根拠。`network_failed()` と同じくスレッドローカルで、呼ぶ側は自分のスレッドで
# `get_elevation` → `last_source_tile()` と続けて読む。
# ⚠️ **保存時に座標からタイルを引き直さない**＝一時失敗は負キャッシュしない
# （B-010）ので、先の標本が 10m で計算されても、後の標本で 5m が取れていると
# 引き直しは 5m を答える（3.3 段4e の初版で実際に起きた）。
_source_tile = threading.local()


def last_source_tile() -> "tuple | None":
    """**このスレッドの直前の `get_elevation`** が標高を返したタイルのキー
    `(layer_id, xtile, ytile)`。値を返さなかった（`nan`/`0.0`）なら None。"""
    return getattr(_source_tile, "key", None)


# 🔑 **式の本体は `terrain_grid`**（B-150 で移した）＝*どの画素を読むか*は「格子の
# 事実」の側で、標本の置き方（`path_sample_fractions`）が同じ式を要る。ここは
# **別名**＝写しではないので、片方だけ動くことがない。タイル選択（`_tile_coords`）と
# レポート地図（`report_map.py`）の経路端点ピクセル投影は従来どおりこの名前を引く。
lonlat_to_pixel = terrain_grid.lonlat_to_pixel


def _tile_coords(lat: float, lon: float, zoom: int) -> tuple[int, int, int, int]:
    """緯度・経度・ズームレベルからタイル座標とタイル内ピクセル座標を返す。"""
    n       = 2.0 ** zoom
    wx, wy  = lonlat_to_pixel(lat, lon, zoom)
    xtile_f = wx / 256.0
    ytile_f = wy / 256.0
    xtile = min(int(xtile_f), int(n) - 1)
    ytile = min(int(ytile_f), int(n) - 1)
    px    = min(255, max(0, int((xtile_f - xtile) * 256)))
    py    = min(255, max(0, int((ytile_f - ytile) * 256)))
    return xtile, ytile, px, py


def get_elevation(lat: float, lon: float) -> float:
    """
    国土地理院 DEM PNG から標高 [m] を取得する。

    DEM_LAYERS の順（5m → 5m → 10m）に試み、
    タイル取得成功かつデコード値が有効（!= 0.0）なら返す。

    🔑 **不変条件＝`nan` を返すのは `network_failed()` が真のときだけ**
    （3.2 / ISSUES.md B-025 ①）。全レイヤ消尽・例外のどちらでも、**通信の失敗
    （タイムアウト・5xx・429・例外）が絡んでいた場合だけ** `nan`（＝「取れな
    かった」）を返す。**404（国土地理院に元々データが無い＝海上・日本域外）で
    全滅した場合や、通信は成功して単に無効ピクセルだった場合は従来どおり
    `0.0`** を返す＝海抜 0m の正当な値・正常な海上経路を `nan` に巻き込まない
    （B-010 の鏡像の誤りをまた作らない）。

    ⚠️ **`0.0` はいまも「海抜 0m」と「404 で取れなかった」を区別しない**（そこは
    3.2 の対象外＝出力契約の `Elevation_m` は 3.3 まで意味を変えない、
    `core/output_contract.py` の予告を参照）。区別できるようになったのは
    「通信の失敗」の側だけ。呼ぶ側は `get_elevation()` → `network_failed()` と
    続けて読めば、返ってきた値が `nan` かどうかと**同じ答え**が得られる
    （B-025 ②で先に立てた `network_failed()` をそのまま契約の判定に使い回す）。
    """
    _network_trouble.flag = False
    _source_tile.key = None
    try:
        for layer_id, zoom in DEM_LAYERS:
            xtile, ytile, px, py = _tile_coords(lat, lon, zoom)
            tile_key     = (layer_id, xtile, ytile)
            cache_subdir = os.path.join(CACHE_DIR, layer_id, str(xtile))
            cache_path   = os.path.join(cache_subdir, f"{ytile}.png")

            # ── キャッシュ確認（ロック保持は辞書参照のみ）────────────
            with _cache_lock:
                if tile_key in _failed_tiles:
                    continue
                cached = _tile_cache.get(tile_key)

            if cached is not None:
                elev = _decode_elevation(cached[py, px])
                if elev != 0.0:
                    _network_trouble.flag = False
                    _source_tile.key = tile_key
                    return elev
                logger.debug(
                    "DEM layer '%s' returned invalid pixel at (%.6f,%.6f), trying next",
                    layer_id, lat, lon,
                )
                continue

            # ── キャッシュミス：ロックを解放してネットワーク取得 ─────
            arr = _fetch_tile(layer_id, zoom, xtile, ytile, cache_subdir, cache_path)

            # ── 取得結果を書き込み ────────────────────────────────────
            #   arr is None のときの負キャッシュ登録は _fetch_tile 側で行う
            #   （404 = 恒久欠落のときだけ。一時失敗は登録しない = B-010）。
            with _cache_lock:
                if arr is None:
                    logger.debug(
                        "DEM layer '%s' unavailable at tile(%d,%d), trying next",
                        layer_id, xtile, ytile,
                    )
                    continue
                _tile_cache.setdefault(tile_key, arr)  # 競合時は先着優先

            elev = _decode_elevation(arr[py, px])
            if elev != 0.0:
                # 別レイヤで通信に失敗していても、値が取れたなら失敗ではない。
                _network_trouble.flag = False
                _source_tile.key = tile_key
                return elev
            logger.debug(
                "DEM layer '%s' returned invalid pixel at (%.6f,%.6f), trying next",
                layer_id, lat, lon,
            )

        if _network_trouble.flag:
            logger.warning(
                "All DEM layers exhausted for lat=%.6f lon=%.6f after network "
                "trouble, returning nan", lat, lon,
            )
            return math.nan
        logger.warning(
            "All DEM layers exhausted for lat=%.6f lon=%.6f (no network trouble, "
            "likely no coverage), returning 0.0", lat, lon,
        )
        return 0.0

    except Exception as e:
        # ⚠️ **不変条件を保つ**＝ここで返す値が `nan` である以上
        # `network_failed()` も真にする（B-025 ①）。デコード周りの想定外の
        # 例外は「通信は成功したが値が海抜0m」より「取れなかった」に近い
        # ＝安全側（見せない）に倒す。
        _network_trouble.flag = True
        logger.error(
            "Elevation decode error: lat=%.6f lon=%.6f error=%s", lat, lon, e
        )
        return math.nan


def tile_acquired_date(tile_key: tuple) -> "str | None":
    """タイル `(layer_id, xtile, ytile)`（＝`last_source_tile()` の答え）の
    **取得日**（ISO 8601 の日付・ローカルタイムゾーン）。3.3 段4e＝出所刻印
    「取得日」の値。

    🔑 **「実行日」ではなく「タイルを取った日」**＝DEM はディスクキャッシュ経由
    なので、キャッシュヒットでは過去の日付になり得る。それこそが「このレポートの
    地形データが実際にはいつのものか」という監査上の答え（実行日は report.txt の
    `Date:` 行が別に持っている）。根拠はディスクのファイルの mtime（メモリの配列は
    いつ書かれたかを持たない）。

    ⚠️ **引数は座標ではなくタイル**（B-213）＝座標からタイルを引き直すと、
    標高を返したのとは別のタイルを答え得る（`last_source_tile` の註）。
    ⚠️ **ネットワークへは出ない**。ディスクに実体が無ければ None。
    """
    layer_id, xtile, ytile = tile_key
    cache_path = os.path.join(CACHE_DIR, layer_id, str(xtile), f"{ytile}.png")
    try:
        mtime = os.path.getmtime(cache_path)
    except OSError:
        return None
    return date.fromtimestamp(mtime).isoformat()


def _read_cached_tile(cache_path: str) -> "np.ndarray | None":
    """ディスクキャッシュのタイルを読む。**読めなければ None＝キャッシュミス扱い**。

    キャッシュは常に捨てて取り直せるものなので、**読み取りの失敗を致命にしない**。
    ここで例外を上へ投げると `get_elevation` の except に握られて **0.0**（＝海抜
    0m と区別されない）になる＝B-123 で塞いだ穴を読み側に開け直すことになる。

    ⚠️ **書き込みを原子的にしても、Windows には読めない一瞬が残る**（2026-08-24 に
    冷えたキャッシュ 61 タイルの実測で `[Errno 13] Permission denied` が 1 点＝
    その点が 0.0 になった）: `os.replace` が走っている最中、置換先を開こうとした
    スレッドは共有違反で弾かれる。**内容は壊れていない**ので、数ミリ秒おいて
    読み直せばまず取れる。⇒ 短く粘ってから諦める（諦めても呼び出し側は
    ネットワーク取得へ落ちるだけで、値は正しく埋まる）。
    """
    for attempt in range(_TILE_READ_ATTEMPTS):
        try:
            return np.array(Image.open(cache_path).convert("RGB"))
        except (OSError, ValueError) as e:
            # PIL の UnidentifiedImageError は OSError 派生。
            if attempt == _TILE_READ_ATTEMPTS - 1:
                logger.debug(
                    "cached tile unreadable (treated as cache miss): path=%s error=%s",
                    cache_path, e,
                )
                return None
            time.sleep(_TILE_READ_RETRY_S)
    return None


def _write_tile_atomic(cache_path: str, img_data: bytes, *,
                       replace_broken: bool = False) -> None:
    """タイル画像を**原子的に**ディスクキャッシュへ書く（B-123）。

    同一ディレクトリの一時ファイルへ書いてから `os.replace` する。⇒ 他のスレッド
    から見える `cache_path` は**常に「無いか、完全か」のどちらか**になる。

    なぜ必要か（2026-08-24 に実測）: 隣り合う標高サンプルは同じタイルを共有する
    ので、並列取得では**同じタイルを別スレッドが同時に要求する**。素の
    `open(path, "wb")` だと、書き込み途中（0 バイト〜途中まで）のファイルを
    `_fetch_tile` 冒頭の `Image.open` が掴み、復号に失敗する。失敗した点は
    `get_elevation` の except に握られて **0.0**（＝海抜 0m と区別されない）になり、
    落ちも止まりもせず**結果が黙って楽観側へ振れる**（26 回線で 4 点）。

    ⛔ ロックでは直さない（タイル取得は並列であることに意味がある）。⚠️ 一時ファイル
    名はプロセス／スレッドで衝突してはならない（`.tmp` 固定だと同じ競合を別の場所に
    作るだけ）。⚠️ 書けなかったこと自体は**致命ではない**（次回また取りに行くだけ）
    ので、失敗は握って記録に留める＝ここで例外を上げると `get_elevation` の except に
    落ちて**通信は成功しているのに 0.0 になる**（直す当の欠陥を別経路で作る）。

    ⚠️ **Windows は「読まれている最中のファイル」への置換を拒む**（WinError 5・
    2026-08-24 に冷えたキャッシュ 61 タイルの実測）。別スレッドが先に書き終えた同じ
    タイルを `_fetch_tile` 冒頭が開いている最中に起きる＝**内容は同じ**なので失う
    情報は無い。⇒ ①既に在るなら**書きに行かない** ②それでも競合したら **debug** へ
    （正常運用で warning を鳴らさない）。本当に書けない側〔ディスクフル・権限〕は
    `cache_path` が不在のまま残るので warning で区別できる。⚠️ **この①は「読めない」
    相手には成り立たない**（B-136＝壊れたキャッシュが永久に居座る）ので、**読み側が**
    読めないと確定した時だけ置換を許す（`replace_broken`）。
    """
    if os.path.exists(cache_path) and not replace_broken:
        # 同じ URL のタイル＝同じ内容。上書きしても得るものが無く、競合だけ増える。
        return

    tmp_path = f"{cache_path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(img_data)
        os.replace(tmp_path, cache_path)
    except OSError as e:
        # ⚠️ **握り潰してよいのは中身が読める時だけ**（B-136・置換の回は必ず在る）。
        if os.path.exists(cache_path) and _read_cached_tile(cache_path) is not None:
            logger.debug(
                "tile cache write skipped (already written by another thread): "
                "path=%s error=%s", cache_path, e,
            )
        else:
            logger.warning("tile cache write failed: path=%s error=%s", cache_path, e)
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _fetch_tile(
    layer_id: str,
    zoom: int,
    xtile: int,
    ytile: int,
    cache_subdir: str,
    cache_path: str,
) -> "np.ndarray | None":
    """タイル画像を取得して numpy 配列で返す。失敗時は None。

    失敗には2種類あり、負キャッシュ（_failed_tiles）の扱いが異なる:
      - 恒久欠落（HTTP 404）: このタイルは取得しても永久に無い（日本域外・
        海上）。_failed_tiles に登録し、以後の再リクエストを抑止する。
      - 一時失敗（タイムアウト・接続エラー・5xx/429 等）: 回復し得るので
        _failed_tiles には登録しない。登録すると回復後もそのタイルを無視し
        続け、標高が誤る（= B-010）。
    """
    # 淡色地図（BASEMAP_LAYER="pale"）も DEM も同じ国土地理院タイルサーバーなので
    # URL テンプレートは GSI_DEM のものを共用する（宣言ファイル・3.3 段4c）。
    url = dem_sources.GSI_DEM.url_template.format(
        layer=layer_id, z=zoom, x=xtile, y=ytile
    )
    # 読めなければ None＝ここでは return せず、そのまま取得へ落ちる（B-123）。
    # **読めなかったことは書き側へ持ち越す**＝壊れた相手だけ置換してよい（B-136）。
    cached = _read_cached_tile(cache_path) if os.path.exists(cache_path) else None
    if cached is not None:
        return cached
    cache_is_broken = os.path.exists(cache_path)

    try:
        logger.debug(
            "Fetching tile: layer=%s zoom=%d x=%d y=%d",
            layer_id, zoom, xtile, ytile,
        )
        res = _get_session().get(url, timeout=5)

        if res.status_code == 200:
            img_data = res.content
            arr = np.array(Image.open(io.BytesIO(img_data)).convert("RGB"))
            os.makedirs(cache_subdir, exist_ok=True)
            _write_tile_atomic(cache_path, img_data, replace_broken=cache_is_broken)
            return arr

        if res.status_code == 404:
            # 恒久欠落 = 負キャッシュに登録して再リクエストを抑止。
            with _cache_lock:
                _failed_tiles.add((layer_id, xtile, ytile))
            logger.debug(
                "tile absent (404) layer=%s tile=(%d,%d)",
                layer_id, xtile, ytile,
            )
            return None

        # 404 以外の非 200（5xx/429 等）は一時失敗として扱い、負キャッシュには
        # 登録しない（次回リトライで取得し直せるようにする）。
        logger.warning(
            "tile: unexpected status %d layer=%s tile=(%d,%d)",
            res.status_code, layer_id, xtile, ytile,
        )
        _network_trouble.flag = True
        return None

    except requests.RequestException as e:
        # 一時失敗。負キャッシュには登録しない。
        logger.warning(
            "tile download failed: layer=%s tile=(%d,%d) error=%s",
            layer_id, xtile, ytile, e,
        )
        if os.path.exists(cache_path):
            cached = _read_cached_tile(cache_path)
            if cached is not None:
                return cached
        _network_trouble.flag = True
        return None


def _decode_elevation(rgb: np.ndarray) -> float:
    """RGB ピクセル値から標高 [m] をデコードする（現在の唯一のアクティブソース＝
    `dem_sources.GSI_DEM`）。デコード式そのものは [core/dem_sources.py](dem_sources.py)
    の宣言（3.3 段4c）が単一ソース。"""
    r, g, b = int(rgb[0]), int(rgb[1]), int(rgb[2])
    return dem_sources.decode(dem_sources.DecodeMethod.GSI_DEM, r, g, b)


# ============================================================
# 淡色地図（basemap）タイル取得 — レポート添付の経路地図用
# ============================================================

def _basemap_tile_path(zoom: int, x: int, y: int) -> tuple[str, str]:
    """淡色地図タイルのキャッシュ (subdir, path) を返す（ズーム別ディレクトリ）。"""
    subdir = os.path.join(CACHE_DIR, BASEMAP_SUBDIR, str(zoom), str(x))
    return subdir, os.path.join(subdir, f"{y}.png")


def fetch_basemap_tiles(
    tiles: list[tuple[int, int]], zoom: int,
) -> dict[tuple[int, int], np.ndarray]:
    """淡色地図タイル群 (x, y) を **並列** 取得し {(x, y): RGB配列} を返す。

    レポート保存（メインスレッド）から呼ばれるため、逐次取得で GUI を固めない
    よう prefetch_tiles と同じワーカープール方式で並列化する。取得・キャッシュ
    の所在（layer/subdir/path）はこの層が所有する（呼び出し側は座標だけ渡す）。
    取得できなかったタイルは結果に含めない（呼び出し側が欠損として扱う）。
    """
    results: dict[tuple[int, int], np.ndarray] = {}
    if not tiles:
        return results
    lock   = threading.Lock()
    work_q: queue.Queue = queue.Queue()
    for t in tiles:
        work_q.put(t)

    def _worker() -> None:
        while True:
            try:
                x, y = work_q.get_nowait()
            except queue.Empty:
                return
            try:
                subdir, path = _basemap_tile_path(zoom, x, y)
                arr = _fetch_tile(BASEMAP_LAYER, zoom, x, y, subdir, path)
                if arr is not None:
                    with lock:
                        results[(x, y)] = arr
            except Exception as e:
                logger.warning("basemap tile worker error: %s", e)
            finally:
                work_q.task_done()

    num_workers = min(_MAX_PREFETCH_WORKERS, len(tiles))
    threads = [threading.Thread(target=_worker, daemon=True) for _ in range(num_workers)]
    for th in threads:
        th.start()
    work_q.join()
    return results

