"""
apps/tracer/session.py
======================
RadioSim Tracer（仮称）の**測定セッション**＝1 回の測定を 1 フォルダに残す器。

⚠️ **本体の CSV 列をここで再定義しない。** 3.4 のバッチ CSV は「書き出し先」で、
列の契約は `core/batch_csv_schema.py` が単一の出所。セッションの側は、その CSV に
入りきらない項目（打ち切り・生値・刻印・2 種類の平均・既設/候補のフラグ・姿勢）を
持つために独自の形をしている。

**ファイルの形**（途中で落ちても、書けた分が壊れないこと）
    <session_id>/
      session.json   … ヘッダと端点。開始時に 1 回書き、以後は書き換えない
      samples.csv    … 受信サンプル。**追記のみ**
      events.csv     … 作業者の操作（設置・移動・終了）。**追記のみ**
      uart.bin       … UART の生のバイト列。**追記のみ**
      uart_index.csv … 生のバイト列を PC が受けた時刻（読み出し 1 回 1 行）。**追記のみ**
      gnss.csv       … 位置の時系列（任意）。**追記のみ**

ヘッダを JSON、サンプルを追記 CSV に割ったのは、**電源が落ちる測定**を前提にする
ため。1 つの JSON に全部入れると、書きかけで落ちたときにその回の測定が丸ごと読めない。

**後から足せない約束**（ここが崩れると、取り直すしか無くなる）
  1. 位置・姿勢は「セッション定数」と「サンプルごとの時系列」の**両方**を許す
     （固定 2 点だけを前提に作ると、機体で測る段で全部書き直しになる）。
  2. **生値を捨てない。** `rssi_raw` は整数のまま残し、dBm への換算は書き出すときに
     校正値の控えから計算する。⛔ 生 RSSI を dBm に見せかけて保存しない。
  3. **打ち切りを捨てない。** 閉じた測定だけを集めると統計が楽観側へ偏る。欠けた
     シーケンス番号は「感度以下」という情報であって、欠測ではない。
  4. **時間平均と空間平均を同じ欄に入れない。** 時間平均（量子化・雑音）は設定側の
     窓の件数、空間平均（マルチパス）は作業者が動かした**置き場所の番号**。
  5. **刻印はヘッダと端点に 1 回だけ書く。** 後で「ケーブルが 1 本傷んでいた」と
     分かったとき、その構成のサンプルだけを隔離できるようにするため。
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

# セッション形式の版。読み手が知らない版を黙って読まないための番号。
# 2＝TX の無線設定を機器（空中の中継）から取り、サンプルに TX の設定番号と
#   送信機ごとの通し番号を足した（増分5 の続き）。
SCHEMA_VERSION = 2

HEADER_FILE = "session.json"
SAMPLES_FILE = "samples.csv"
EVENTS_FILE = "events.csv"
RAW_FILE = "uart.bin"
RAW_INDEX_FILE = "uart_index.csv"
GNSS_FILE = "gnss.csv"

# --- 語彙（値の集合を閉じておく。綴り違いが黙って別の意味になるのを防ぐ） -------

# 測定対象の種別。phase 1 は「アンテナを設置できる場所」しか測れず、既設回線は
# 「建てた＝だいたい繋がっている」ので成功例に偏る。**偏りを後から補正できるように
# フラグを刻む**（構造的に解消できるのは機体で測る段だけ）。
TARGET_KINDS = ("existing_link", "candidate_site")

# 高さの出どころ。CLAS は Fix 解のときだけ採用する（Float 以下では、もっともらしい
# 値のまま誤差がデシメートル〜メートル級に戻る）。
HEIGHT_SOURCES = ("tape", "survey", "clas_fix")

# GNSS の解の状態。`clas_fix` を名乗るには `fix` であること。
FIX_STATES = ("fix", "float", "single", "none")

ROLES = ("tx", "rx")

# 受信感度として通す範囲 [dBm]。本体の入力検査（`core/i18n.py` の `err_sens`）と
# 同じ範囲にしてある＝ここを通った値がそのまま「感度以下（〜未満）」として
# 本体へ渡る文面になるので、本体が受け付けない値を名乗らせない。
SENSITIVITY_RANGE_DBM = (-130.0, -20.0)

# 検波方式。MAVLink の TRACER_DETECTOR と同じ語彙（XML が正典）。
DETECTORS = ("unknown", "instant", "mean")

# アンテナ切替の状態。同上。
ANTENNAS = ("unknown", "internal", "external")

SAMPLE_COLUMNS = (
    "pc_utc",            # PC が受けた時刻（UTC・ISO 8601）
    "radio_time_us",     # 受信機の起動からの経過時間（µs）＝ファームの時計
    "seq",               # 送信シーケンス番号。欠けが打ち切りの根拠
    "tx_id",             # 送信機の個体 ID
    "rssi_raw",          # 受信レベルの生値（整数・dBm ではない）
    "noise_floor_raw",   # 雑音フロアの生値（整数・dBm ではない）
    "config_id",         # RX の設定番号。積分窓・検波方式はここから引く
    "tx_config_id",      # TX の設定番号（受けたパケットに載っていたもの）
    "sample_seq",        # ファームが振った通し番号（送信機ごと）。欠けは PC までの間の落ち
    "spatial_slot",      # 空間平均の置き場所の番号（PC 側が付ける）
)

# 作業者の操作。空間平均の置き場所の区切りは**サンプルではなくここから**決める＝
# 移動中に 1 つも受からなかった置き場所は、サンプルからは存在ごと見えない
# （見えないまま捨てると、いちばん悪い場所の打ち切りが数から落ちる）。
#   place … 置き場所に据えた（以後の受信はこの番号の置き場所）
#   move  … 動かし始めた（次の place までの受信はどの窓にも入れない）
#   stop  … 測定を終えた（最後の置き場所の末尾はここまで）
EVENT_KINDS = ("place", "move", "stop")

# 移動中に受けたサンプルの置き場所の番号。**捨てずに残し、窓には入れない。**
MOVING_SLOT = -1

EVENT_COLUMNS = (
    "pc_utc",            # 操作した時刻（UTC・ISO 8601）。サンプルの pc_utc と同じ時計
    "kind",
    "spatial_slot",      # place のときの置き場所の番号（それ以外は MOVING_SLOT）
)

RAW_INDEX_COLUMNS = (
    "pc_utc",            # その読み出しを PC が受けた時刻
    "end_offset",        # uart.bin の、その読み出しの終わりのバイト位置
)

GNSS_COLUMNS = (
    "utc",               # ISO 8601
    "lat",
    "lon",
    "ellipsoid_h_m",     # 楕円体高。標高への変換はセッション処理の 1 か所だけで行う
    "fix_state",
)


class SessionError(Exception):
    """セッションの読み書きの約束が破れたとき。"""


# --- 刻印・校正 ---------------------------------------------------------------


@dataclass(frozen=True)
class Calibration:
    """使った校正値の**控え**。セッション単体で完結させる。

    外部の校正台帳を日付とハッシュで参照する形は採らない＝生値を dBm に換算するには
    校正値そのものが要るので、控えが無いと後から換算をやり直せない。台帳（全個体・
    全履歴の管理）は製品の中に作らない。

    換算は `dbm = rssi_raw * scale_db_per_count + offset_db`。⚠️ 係数を 1.0 に
    しただけの「素通し」を校正と呼ばないこと（それは生値のままという意味）。
    """

    measured_on: str                 # 校正日（ISO 8601 の日付）
    offset_db: float
    scale_db_per_count: float
    reference_unit_id: str           # 基準器の ID
    reference_measured_on: str       # 基準器を校正済みの測定器で測った日
    note: str = ""


@dataclass(frozen=True)
class Provenance:
    """セッション全体の刻印。**製品責任のためではなく、データを後から救うため。**"""

    software_commit: str             # PC 側（このリポジトリ）のコミット
    geoid_model: str                 # 楕円体高→標高の変換に使ったモデル名
    censor_min_receive_rate: float   # 打ち切りのしきい値（受け入れ試験で決める）
    censor_window_s: float           # 集計窓の長さ
    operator: str = ""
    note: str = ""


# --- 端点 --------------------------------------------------------------------


@dataclass(frozen=True)
class RadioSettings:
    """無線設定。**設定番号でサンプルと 1 対 1 に結ぶ**（MAVLink の設定メッセージと同じ）。"""

    config_id: int
    firmware_version: str            # ファームの版（git のコミット）＝刻印に入る
    channel: int
    rate_kbps: int
    tx_power_cdbm: int               # 設定した送信電力（0.01 dBm 単位）
    tx_interval_ms: int
    detector: str
    integration_window_us: int
    integration_samples: int         # **時間平均**の件数（空間平均とは別の場所）
    antenna: str
    # 受信感度。打ち切りの「〜未満」の値になる。**TX では None**（受けないので値が無い。
    # 機器は感度を知らないので、RX でも雛形に書いた値）。
    sensitivity_dbm: float | None


@dataclass(frozen=True)
class Position:
    """端点の位置（セッション定数の側）。標高（ジオイド基準）を作業座標にする。"""

    lat: float
    lon: float
    elevation_m: float               # 地面の標高
    height_agl_m: float              # 給電点の地上高
    height_source: str
    fix_state: str = "none"          # GNSS 由来のときの解の状態
    antenna_offset_m: float = 0.0    # GNSS アンテナ→給電点の高さ差（巻尺で測る）


@dataclass(frozen=True)
class Orientation:
    """向き。出どころを値と一緒に持つ（後で精度を問い直せるように）。"""

    azimuth_deg: float
    elevation_deg: float
    source: str = ""


@dataclass(frozen=True)
class Endpoint:
    """TX または RX の端点。

    `position` が `None` なのは「定数を持たない」＝サンプルごとの時系列で位置が
    決まる構成（機体で測る段）。phase 1 は定数を入れるが、**器は両方を許す**。
    """

    role: str
    measurement_config_id: str       # 無線機の個体・アンテナ・給電線の組み合わせ
    device_id: str                   # 個体 ID（MAC アドレス）
    feeder_loss_db: float
    antenna_gain_dbi: float          # アンテナ端子基準の利得（給電線損とは別の量）
    calibration: Calibration
    radio: RadioSettings
    position: Position | None = None
    orientation: Orientation | None = None


# --- ヘッダ ------------------------------------------------------------------


@dataclass(frozen=True)
class SessionHeader:
    """開始時に確定し、以後は書き換えない部分。"""

    session_id: str
    started_utc: str
    target_kind: str
    env_class: str                   # 本体の環境区分（CSV の `env_class` へ渡す）
    meas_method: str                 # 本体の `meas_method` へ渡す
    provenance: Provenance
    tx: Endpoint
    rx: Endpoint
    site_note: str = ""
    schema_version: int = SCHEMA_VERSION


# --- サンプル ----------------------------------------------------------------


@dataclass(frozen=True)
class RxSample:
    """受信 1 回ぶん。**生値のまま**残す（dBm への換算は書き出すときに行う）。"""

    pc_utc: str
    radio_time_us: int
    seq: int
    tx_id: str
    rssi_raw: int
    noise_floor_raw: int
    config_id: int
    tx_config_id: int
    sample_seq: int
    spatial_slot: int = 0


@dataclass(frozen=True)
class Event:
    """作業者の操作 1 回ぶん。"""

    pc_utc: str
    kind: str
    spatial_slot: int = MOVING_SLOT


@dataclass(frozen=True)
class PositionFix:
    """位置の時系列の 1 点（任意）。"""

    utc: str
    lat: float
    lon: float
    ellipsoid_h_m: float
    fix_state: str


# --- 検証 --------------------------------------------------------------------


def _require(value: str, allowed: tuple[str, ...], what: str) -> None:
    if value not in allowed:
        raise SessionError(
            f"{what} が語彙の外です: {value!r}（使えるのは {', '.join(allowed)}）"
        )


def _validate_position(position: Position, where: str) -> None:
    _require(position.height_source, HEIGHT_SOURCES, f"{where} の高さの出どころ")
    _require(position.fix_state, FIX_STATES, f"{where} の解の状態")
    if position.height_source == "clas_fix" and position.fix_state != "fix":
        # Float 以下の解を「CLAS で測った高さ」として通すと、誤差がメートル級に
        # 戻ったまま、もっともらしい値として全サンプルに乗る。
        raise SessionError(
            f"{where}: 高さの出どころが clas_fix なのに解の状態が "
            f"{position.fix_state!r} です（Fix 解のときだけ採用する）"
        )


def _validate_endpoint(endpoint: Endpoint, expected_role: str) -> None:
    _require(endpoint.role, ROLES, "端点の役割")
    if endpoint.role != expected_role:
        raise SessionError(
            f"{expected_role} の欄に role={endpoint.role!r} の端点が入っています"
        )
    _require(endpoint.radio.detector, DETECTORS, f"{expected_role} の検波方式")
    _require(endpoint.radio.antenna, ANTENNAS, f"{expected_role} のアンテナ切替")
    low, high = SENSITIVITY_RANGE_DBM
    sensitivity = endpoint.radio.sensitivity_dbm
    if expected_role == "tx":
        if sensitivity is not None:
            # TX は受けない。値があると、どこかで RX の感度と取り違えて使われ得る。
            raise SessionError(f"TX に受信感度があります: {sensitivity}（TX は受けません）")
    elif sensitivity is None or not low <= sensitivity <= high:
        raise SessionError(
            f"{expected_role} の受信感度が範囲の外です: "
            f"{sensitivity}（{low:g}〜{high:g} dBm）"
        )
    if not endpoint.measurement_config_id:
        # 刻印の 1 項目目。これが無いと、後から「どの構成で取ったか」を辿れない。
        raise SessionError(f"{expected_role} の測定構成 ID が空です")
    if endpoint.position is not None:
        _validate_position(endpoint.position, expected_role)


def validate_header(header: SessionHeader) -> None:
    """書く前・読んだ後の両方で通す検査。**通らないものは保存しない。**"""
    if header.schema_version != SCHEMA_VERSION:
        raise SessionError(
            f"知らないセッション形式の版です: {header.schema_version}"
            f"（この版が読めるのは {SCHEMA_VERSION}）"
        )
    if not header.session_id:
        raise SessionError("session_id が空です")
    _require(header.target_kind, TARGET_KINDS, "測定対象の種別")
    if not 0.0 < header.provenance.censor_min_receive_rate <= 1.0:
        # 0 は「全部欠けた窓だけを打ち切りにする」＝一部だけ届いた窓の偏った平均が
        # 実測として本体に入る。1 超は全部が打ち切りになる。
        raise SessionError(
            "打ち切りのしきい値は 0 より大きく 1 以下の受信率で指定します: "
            f"{header.provenance.censor_min_receive_rate}"
        )
    if header.provenance.censor_window_s <= 0:
        raise SessionError("集計窓の長さは正の秒数です")
    _validate_endpoint(header.tx, "tx")
    _validate_endpoint(header.rx, "rx")


def parse_utc(value: str) -> datetime:
    """ISO 8601 の UTC を読む。**時差の無い時刻は受け付けない**＝PC の現地時刻が
    混ざると、イベントとサンプルの前後が 9 時間ずれたまま黙って対応づけられる。"""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise SessionError(f"時刻が ISO 8601 ではありません: {value!r}") from e
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise SessionError(f"時刻が UTC ではありません: {value!r}")
    return parsed


def format_utc(moment: datetime) -> str:
    """UTC の時刻を µ 秒まで書く（`parse_utc` で読み戻せる形）。"""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def validate_events(events: list[Event], *, finished: bool = True) -> None:
    """操作の並びの約束。**区切りを決める材料なので、崩れた並びから窓を作らない。**

    `place` で始まり、`place → move|stop`・`move → place|stop` と進み、`stop` で
    終わる。置き場所の番号は使い回さない（同じ番号が 2 か所を指すと、後から
    どちらの場所の値か分からない）。`finished=False` は測定中（まだ `stop` が無い）。
    """
    if not events:
        if finished:
            raise SessionError("操作の記録がありません（place から始まります）")
        return
    used: set[int] = set()
    previous: Event | None = None
    for event in events:
        _require(event.kind, EVENT_KINDS, "操作の種類")
        moment = parse_utc(event.pc_utc)
        if previous is None:
            if event.kind != "place":
                raise SessionError(f"操作の記録が place で始まっていません: {event.kind}")
        else:
            if previous.kind == "stop":
                raise SessionError("stop の後に操作があります")
            if event.kind == previous.kind:
                raise SessionError(f"{event.kind} が続いています（{event.pc_utc}）")
            if moment < parse_utc(previous.pc_utc):
                raise SessionError(f"操作の時刻が戻っています: {event.pc_utc}")
        if event.kind == "place":
            if event.spatial_slot < 0:
                raise SessionError(f"置き場所の番号が負です: {event.spatial_slot}")
            if event.spatial_slot in used:
                raise SessionError(f"置き場所の番号を使い回しています: {event.spatial_slot}")
            used.add(event.spatial_slot)
        elif event.spatial_slot != MOVING_SLOT:
            raise SessionError(f"{event.kind} に置き場所の番号が付いています")
        previous = event
    if finished and events[-1].kind != "stop":
        raise SessionError("操作の記録が stop で終わっていません（測定が途中で止まった）")


# --- 書き出し ----------------------------------------------------------------


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """同じディレクトリの一時ファイルへ書き切ってから `os.replace`。

    `open(path, "w")` を直に開くと**開いた時点で中身が消える**ので、途中で落ちると
    ヘッダの無いセッションが残る（サンプルは書けているのに読めない、が最悪）。
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".session-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def create_session(root: str | os.PathLike[str], header: SessionHeader) -> Path:
    """セッションのフォルダを作り、ヘッダと空のサンプル CSV を置く。

    既にあるフォルダには作らない（**追記のみ**の器に別の測定が混ざると、後から
    分けられない）。
    """
    validate_header(header)
    directory = Path(root) / header.session_id
    if directory.exists():
        raise SessionError(f"同じ session_id のフォルダが既にあります: {directory}")
    directory.mkdir(parents=True)
    _write_json_atomic(directory / HEADER_FILE, asdict(header))
    _write_csv_header(directory / SAMPLES_FILE, SAMPLE_COLUMNS)
    _write_csv_header(directory / EVENTS_FILE, EVENT_COLUMNS)
    _write_csv_header(directory / RAW_INDEX_FILE, RAW_INDEX_COLUMNS)
    (directory / RAW_FILE).touch()
    return directory


def _write_csv_header(path: Path, columns: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(columns)


def append_rx_samples(directory: str | os.PathLike[str], samples: list[RxSample]) -> None:
    """受信サンプルを**追記**する。既に書いた行には触らない。"""
    path = Path(directory) / SAMPLES_FILE
    if not path.exists():
        raise SessionError(f"サンプル CSV がありません: {path}")
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for s in samples:
            writer.writerow([getattr(s, c) for c in SAMPLE_COLUMNS])
        f.flush()
        os.fsync(f.fileno())   # 測定中に電源が落ちても、書けた分はディスクに在る


def append_event(directory: str | os.PathLike[str], event: Event) -> None:
    """操作を 1 つ**追記**する。書く前に、それまでの並びに続けて約束を通るか見る。"""
    history = list(read_events(directory))
    validate_events(history + [event], finished=False)
    _append_rows(Path(directory) / EVENTS_FILE, [[getattr(event, c) for c in EVENT_COLUMNS]])


def append_raw(directory: str | os.PathLike[str], chunk: bytes, pc_utc: str) -> None:
    """UART の生のバイト列を**追記**し、受けた時刻を索引に 1 行足す。

    ⚠️ 本体（バイト列）を先に書く。索引が先だと、間で落ちたときに**無いバイトを
    指す索引**が残る（逆なら、索引の無い末尾が残るだけで読み直せる）。
    """
    parse_utc(pc_utc)
    raw = Path(directory) / RAW_FILE
    with raw.open("ab") as f:
        f.write(chunk)
        f.flush()
        os.fsync(f.fileno())
        end = f.tell()
    _append_rows(Path(directory) / RAW_INDEX_FILE, [[pc_utc, end]])


def read_raw(directory: str | os.PathLike[str]) -> Iterator[tuple[str, bytes]]:
    """生のバイト列を、**読み出したときの区切りと時刻のまま**返す。"""
    directory = Path(directory)
    data = (directory / RAW_FILE).read_bytes()
    start = 0
    for row in _read_rows(directory / RAW_INDEX_FILE, RAW_INDEX_COLUMNS, "生ログの索引"):
        end = int(row["end_offset"])
        if not start <= end <= len(data):
            raise SessionError(
                f"生ログの索引が本体を指していません: {end}（本体は {len(data)} バイト）"
            )
        yield row["pc_utc"], data[start:end]
        start = end


def read_events(directory: str | os.PathLike[str]) -> Iterator[Event]:
    for row in _read_rows(Path(directory) / EVENTS_FILE, EVENT_COLUMNS, "操作の記録"):
        yield Event(
            pc_utc=row["pc_utc"], kind=row["kind"], spatial_slot=int(row["spatial_slot"])
        )


def _append_rows(path: Path, rows: list[list[object]]) -> None:
    if not path.exists():
        raise SessionError(f"追記先がありません: {path}")
    with path.open("a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)
        f.flush()
        os.fsync(f.fileno())


def _read_rows(path: Path, columns: tuple[str, ...], what: str) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != columns:
            raise SessionError(
                f"{what}の列が違います: {reader.fieldnames}（期待は {list(columns)}）"
            )
        yield from reader


def append_position_fixes(
    directory: str | os.PathLike[str], fixes: list[PositionFix]
) -> None:
    """位置の時系列を**追記**する（無ければヘッダ行から作る＝任意のファイル）。"""
    path = Path(directory) / GNSS_FILE
    if not path.exists():
        _write_csv_header(path, GNSS_COLUMNS)
    for fix in fixes:
        _require(fix.fix_state, FIX_STATES, "位置の時系列の解の状態")
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for fix in fixes:
            writer.writerow([getattr(fix, c) for c in GNSS_COLUMNS])
        f.flush()
        os.fsync(f.fileno())


# --- 読み戻し ----------------------------------------------------------------


def _build(cls: Any, payload: dict[str, Any], what: str) -> Any:
    """dict から dataclass へ。**知らない鍵・欠けた鍵は黙って捨てない。**"""
    fields = {f.name for f in cls.__dataclass_fields__.values()}
    unknown = sorted(set(payload) - fields)
    if unknown:
        raise SessionError(f"{what} に知らない項目があります: {', '.join(unknown)}")
    try:
        return cls(**payload)
    except TypeError as e:
        raise SessionError(f"{what} の項目が足りません: {e}") from e


def read_header(directory: str | os.PathLike[str]) -> SessionHeader:
    """ヘッダを読む。**語彙と約束を通らないものは読めたことにしない。**"""
    path = Path(directory) / HEADER_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SessionError(f"ヘッダがありません: {path}") from e
    except json.JSONDecodeError as e:
        raise SessionError(f"ヘッダが壊れています: {path}（{e}）") from e
    return header_from_dict(payload)


def header_from_dict(payload: dict[str, Any]) -> SessionHeader:
    """dict からヘッダを組み、約束を通す（読み戻しと雛形からの組み立ての共通の口）。"""
    nested = dict(payload)
    nested["provenance"] = _build(Provenance, payload.get("provenance", {}), "刻印")
    for key in ("tx", "rx"):
        nested[key] = _build_endpoint(payload.get(key, {}), key)
    header = _build(SessionHeader, nested, "ヘッダ")
    validate_header(header)
    return header


def _build_endpoint(payload: dict[str, Any], what: str) -> Endpoint:
    nested = dict(payload)
    nested["calibration"] = _build(Calibration, payload.get("calibration", {}), f"{what} の校正値")
    nested["radio"] = _build(RadioSettings, payload.get("radio", {}), f"{what} の無線設定")
    for key, cls in (("position", Position), ("orientation", Orientation)):
        value = payload.get(key)
        nested[key] = None if value is None else _build(cls, value, f"{what} の{key}")
    return _build(Endpoint, nested, f"{what} の端点")


def read_rx_samples(directory: str | os.PathLike[str]) -> Iterator[RxSample]:
    """サンプルを 1 行ずつ返す（測定 1 回で数十万行になり得るので溜めない）。"""
    path = Path(directory) / SAMPLES_FILE
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != SAMPLE_COLUMNS:
            raise SessionError(
                f"サンプル CSV の列が違います: {reader.fieldnames}"
                f"（期待は {list(SAMPLE_COLUMNS)}）"
            )
        for row in reader:
            yield RxSample(
                pc_utc=row["pc_utc"],
                radio_time_us=int(row["radio_time_us"]),
                seq=int(row["seq"]),
                tx_id=row["tx_id"],
                rssi_raw=int(row["rssi_raw"]),
                noise_floor_raw=int(row["noise_floor_raw"]),
                config_id=int(row["config_id"]),
                tx_config_id=int(row["tx_config_id"]),
                sample_seq=int(row["sample_seq"]),
                spatial_slot=int(row["spatial_slot"]),
            )


def read_position_fixes(directory: str | os.PathLike[str]) -> Iterator[PositionFix]:
    """位置の時系列を 1 行ずつ返す。ファイルが無ければ何も返さない（任意だから）。"""
    path = Path(directory) / GNSS_FILE
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != GNSS_COLUMNS:
            raise SessionError(
                f"位置 CSV の列が違います: {reader.fieldnames}"
                f"（期待は {list(GNSS_COLUMNS)}）"
            )
        for row in reader:
            yield PositionFix(
                utc=row["utc"],
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                ellipsoid_h_m=float(row["ellipsoid_h_m"]),
                fix_state=row["fix_state"],
            )


def to_dbm(rssi_raw: int, calibration: Calibration) -> float:
    """生値を**無線機の端での** dBm へ換算する（書き出すときだけ行う）。

    ⛔ 換算した値でサンプルを上書きしない。校正をやり直せなくなるのが最悪。
    ⛔ **給電線損をここで足さない。** 本体は残差を
    `predicted − (meas_dbm + feeder_loss_db)` で計算する＝給電線損は CSV の独立した
    列として渡すものなので、ここで足すと**二重に効く**（給電線損 3 dB の回線で
    残差が 3 dB ずれ、較正の判定をそのまま誤らせる）。
    """
    return rssi_raw * calibration.scale_db_per_count + calibration.offset_db
