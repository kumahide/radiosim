"""
apps/tracer/calib.py
====================
**個体ごとの校正ファイル**（Tracer phase 1・増分6）。段0 の机上試験（`bench analyze`）が
書き、記録（`record --calib`）が個体 ID で読んで**セッションへ写す**
（2026-09-19 ユーザー決定＝手で書き写す欄を無くす）。

    <校正フォルダ>/<個体 ID の : を - にしたもの>.json

⚠️ **最新の 1 版だけを持ち、履歴は持たない**＝§8 の「校正台帳は製品内に作らない」との
線引き。使った値はセッションに写るので（§6.1-1B）、ファイルを書き換えても過去の
セッションの換算は変わらない。

🔑 **TX の出力は（電力・チャネル）ごとの表**＝セッションは「実測した設定＝動いている
設定」を要求する（`session._validate_tx_output`）。1 点だけだと、現場でチャネルや
電力を変えた途端に記録が始まらない。各行の出どころ（`source`）を分けて持つ:
  meter              … パワー計で測った（`cli cont`）
  bench_power_sweep  … パワー計の 1 点から、机上試験の電力掃引の相対差で導いた
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from apps.tracer.session import Calibration, SessionError

CALIB_FORMAT = 1
TX_OUTPUT_SOURCES = ("meter", "bench_power_sweep")


@dataclass(frozen=True)
class TxOutput:
    power_cdbm: int          # 設定（機器が読み戻した値・0.01 dBm 単位）
    channel: int
    dbm: float               # U.FL→SMA 中継ケーブルの SMA 端の出力（バースト中）
    source: str              # TX_OUTPUT_SOURCES
    measured_on: str         # その値の元になったパワー計の測定日
    note: str = ""


@dataclass(frozen=True)
class RxCurve:
    """`dbm = rssi_raw * scale_db_per_count + offset_db`（`session.to_dbm` と同じ式）。"""

    offset_db: float
    scale_db_per_count: float
    reference_unit_id: str       # 入力の絶対値の元にした TX（パワー計で測った個体）
    reference_measured_on: str   # その TX をパワー計で測った日
    linear_range_dbm: tuple[float, float]   # 当てはめに使った入力の範囲
    fit_max_residual_db: float
    note: str = ""


@dataclass(frozen=True)
class DeviceCalibration:
    device_id: str
    measured_on: str             # 机上試験の日（校正日）
    source_bench: str            # 机上試験のフォルダ名
    software_commit: str         # 判定したソフトのコミット
    rx: RxCurve | None
    tx_outputs: tuple[TxOutput, ...]


def calib_path(directory: str | os.PathLike[str], device_id: str) -> Path:
    return Path(directory) / (device_id.lower().replace(":", "-") + ".json")


def write_device_calibration(directory: str | os.PathLike[str], cal: DeviceCalibration) -> Path:
    """書き切ってから置き換える（書きかけの校正ファイルを record に読ませない）。"""
    for t in cal.tx_outputs:
        if t.source not in TX_OUTPUT_SOURCES:
            raise SessionError(f"TX の出力の出どころが不明です: {t.source}")
    path = calib_path(directory, cal.device_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"format": CALIB_FORMAT, **asdict(cal)}
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".calib-", suffix=".tmp")
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
    return path


def read_device_calibration(directory: str | os.PathLike[str], device_id: str) -> DeviceCalibration:
    path = calib_path(directory, device_id)
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SessionError(
            f"個体 {device_id} の校正ファイルがありません: {path}（bench analyze --calib-dir で作ります）"
        ) from e
    if payload.pop("format", None) != CALIB_FORMAT:
        raise SessionError(f"知らない校正ファイルの形式です: {path}")
    if payload["device_id"].lower() != device_id.lower():
        raise SessionError(f"校正ファイルの個体 ID が名前と違います: {path}")
    rx = payload.pop("rx")
    outputs = payload.pop("tx_outputs")
    return DeviceCalibration(
        **payload,
        rx=None if rx is None else RxCurve(**{**rx, "linear_range_dbm": tuple(rx["linear_range_dbm"])}),
        tx_outputs=tuple(TxOutput(**t) for t in outputs),
    )


def endpoint_calibration(
    cal: DeviceCalibration, role: str, *, power_cdbm: int | None = None,
    channel: int | None = None,
) -> Calibration:
    """セッションの端点に写す控え。TX なら、**動いている設定と同じ行**の出力だけを使う。"""
    if cal.rx is None:
        raise SessionError(
            f"個体 {cal.device_id} の校正ファイルに RX の校正がありません"
            "（相手の TX の出力をパワー計で測ってから bench analyze をやり直してください）"
        )
    rx = cal.rx

    def copy(note: str, out: TxOutput | None = None) -> Calibration:
        return Calibration(
            measured_on=cal.measured_on,
            offset_db=rx.offset_db,
            scale_db_per_count=rx.scale_db_per_count,
            reference_unit_id=rx.reference_unit_id,
            reference_measured_on=rx.reference_measured_on,
            note=note,
            tx_output_dbm=None if out is None else out.dbm,
            tx_output_power_cdbm=None if out is None else out.power_cdbm,
            tx_output_channel=None if out is None else out.channel,
        )

    if role == "rx":
        return copy(f"校正ファイル（机上試験 {cal.source_bench}）")
    if role != "tx":
        raise SessionError(f"知らない役割です: {role}")
    matches = [t for t in cal.tx_outputs if (t.power_cdbm, t.channel) == (power_cdbm, channel)]
    if not matches:
        have = ", ".join(f"{t.power_cdbm / 100:g} dBm・ch {t.channel}" for t in cal.tx_outputs)
        raise SessionError(
            f"個体 {cal.device_id} の校正ファイルに、設定 {(power_cdbm or 0) / 100:g} dBm・"
            f"チャネル {channel} の出力がありません（あるのは: {have or 'なし'}）"
        )
    # meter を優先する（導いた値より直接測った値）。
    out = sorted(matches, key=lambda t: TX_OUTPUT_SOURCES.index(t.source))[0]
    return copy(
        f"校正ファイル（机上試験 {cal.source_bench}）・送信出力は {out.source}（{out.measured_on}）",
        out,
    )
