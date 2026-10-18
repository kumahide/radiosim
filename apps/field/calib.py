"""
apps/field/calib.py
====================
**個体ごとの校正ファイル**（Field phase 1・増分6）。ステージ0 の机上試験（`bench analyze`）が
書き、記録（`record --calib`）が個体 ID で読んで**セッションへ写す**
（2026-09-19 ユーザー決定＝手で書き写す欄を無くす）。

    <校正フォルダ>/<個体 ID の : を - にしたもの>.json

⚠️ **最新の 1 版だけを持ち、履歴は持たない**＝§8 の「校正台帳は製品内に作らない」との
線引き。使った値はセッションに写るので（§6.1-1B）、ファイルを書き換えても過去の
セッションの換算は変わらない。

🔑 **帯域ごとに分けて持つ**（形式 2・I-243）＝1 台の個体が 920 MHz（E220）と 2.4 GHz
（C6 内蔵）の両方を測り、受信の特性も送信の出力も帯域で別物。机上試験は 1 回 1 帯域
なので、書くときは**その帯域だけ**を差し替え、もう片方の帯域は残す。形式 1（帯域の
欄が無い・2.4 GHz を戻す前）は 920 として読む。

🔑 **TX の出力は（帯域・電力・チャネル）ごとの表**＝セッションは「実測した設定＝動いて
いる設定」を要求する（`session._validate_tx_output`）。1 点だけだと、現場でチャネルや
電力を変えた途端に記録が始まらない。各行の出どころ（`source`）を分けて持つ:
  meter              … 測定器で測った（2.4 GHz は `cont`・920 MHz の測り方は I-242）
  bench_power_sweep  … 測定器の 1 点から、机上試験の電力掃引の相対差で導いた
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from apps.field.session import BANDS, Calibration, SessionError

CALIB_FORMAT = 2
TX_OUTPUT_SOURCES = ("meter", "bench_power_sweep")


@dataclass(frozen=True)
class TxOutput:
    band: str                # BANDS のどれか
    power_cdbm: int          # 設定（機器が読み戻した値・0.01 dBm 単位）
    channel: int
    dbm: float               # U.FL→SMA 中継ケーブルの SMA 端の出力（バースト中）
    source: str              # TX_OUTPUT_SOURCES
    measured_on: str         # その値の元になった測定器の測定日
    note: str = ""


@dataclass(frozen=True)
class RxCurve:
    """`dbm = rssi_raw * scale_db_per_count + offset_db`（`session.to_dbm` と同じ式）。
    出どころ（どの机上試験か）も帯域ごと＝帯域ごとに別の日に測る。"""

    offset_db: float
    scale_db_per_count: float
    reference_unit_id: str       # 入力の絶対値の元にした TX（測定器で測った個体）
    reference_measured_on: str   # その TX を測定器で測った日
    linear_range_dbm: tuple[float, float]   # 当てはめに使った入力の範囲
    fit_max_residual_db: float
    measured_on: str             # 机上試験の日（校正日）
    source_bench: str            # 机上試験のフォルダ名
    software_commit: str         # 判定したソフトのコミット
    note: str = ""


@dataclass(frozen=True)
class DeviceCalibration:
    device_id: str
    rx: dict[str, RxCurve]               # 帯域 → 受信の校正（無い帯域は鍵が無い）
    tx_outputs: tuple[TxOutput, ...]


def calib_path(directory: str | os.PathLike[str], device_id: str) -> Path:
    return Path(directory) / (device_id.lower().replace(":", "-") + ".json")


def write_device_calibration(
    directory: str | os.PathLike[str], cal: DeviceCalibration, band: str
) -> Path:
    """`band` の分だけを差し替えて書く（ほかの帯域は今のファイルのまま残す）。
    書き切ってから置き換える（書きかけの校正ファイルを record に読ませない）。"""
    if band not in BANDS:
        raise SessionError(f"知らない帯域です: {band}（{', '.join(BANDS)}）")
    if set(cal.rx) - {band} or any(t.band != band for t in cal.tx_outputs):
        raise SessionError(f"{band} MHz 帯の校正に別の帯域の値が混ざっています")
    for t in cal.tx_outputs:
        if t.source not in TX_OUTPUT_SOURCES:
            raise SessionError(f"TX の出力の出どころが不明です: {t.source}")
    path = calib_path(directory, cal.device_id)
    if path.exists():
        kept = read_device_calibration(directory, cal.device_id)
        cal = DeviceCalibration(
            device_id=cal.device_id,
            rx={**{b: r for b, r in kept.rx.items() if b != band}, **cal.rx},
            tx_outputs=tuple(t for t in kept.tx_outputs if t.band != band) + cal.tx_outputs,
        )
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


def _rx_curve(fields: dict[str, Any]) -> RxCurve:
    return RxCurve(**{**fields, "linear_range_dbm": tuple(fields["linear_range_dbm"])})


def read_device_calibration(directory: str | os.PathLike[str], device_id: str) -> DeviceCalibration:
    path = calib_path(directory, device_id)
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SessionError(
            f"個体 {device_id} の校正ファイルがありません: {path}（bench analyze --calib-dir で作ります）"
        ) from e
    form = payload.pop("format", None)
    if form not in (1, CALIB_FORMAT):
        raise SessionError(f"知らない校正ファイルの形式です: {path}")
    if payload["device_id"].lower() != device_id.lower():
        raise SessionError(f"校正ファイルの個体 ID が名前と違います: {path}")
    if form == 1:
        # 2.4 GHz を戻す前の形＝920 MHz だけ。机上試験の出どころは受信の校正へ移す。
        origin = {k: payload[k] for k in ("measured_on", "source_bench", "software_commit")}
        rx = {} if payload["rx"] is None else {"920": _rx_curve({**payload["rx"], **origin})}
        outputs = [{**t, "band": "920"} for t in payload["tx_outputs"]]
    else:
        rx = {b: _rx_curve(r) for b, r in payload["rx"].items()}
        outputs = payload["tx_outputs"]
    cal = DeviceCalibration(
        device_id=payload["device_id"], rx=rx,
        tx_outputs=tuple(TxOutput(**t) for t in outputs),
    )
    unknown = (set(cal.rx) | {t.band for t in cal.tx_outputs}) - set(BANDS)
    if unknown:
        raise SessionError(f"校正ファイルに知らない帯域があります: {sorted(unknown)}（{path}）")
    return cal


def endpoint_calibration(
    cal: DeviceCalibration, role: str, *, band: str, power_cdbm: int | None = None,
    channel: int | None = None,
) -> Calibration:
    """セッションの端点に写す控え。**動いている帯域**の受信の校正を使い、TX なら
    **動いている設定と同じ行**の出力だけを使う。"""
    rx = cal.rx.get(band)
    if rx is None:
        raise SessionError(
            f"個体 {cal.device_id} の校正ファイルに {band} MHz 帯の RX の校正がありません"
            "（相手の TX の出力を測定器で測ってから、その帯域の bench analyze を"
            "やり直してください）"
        )

    def copy(note: str, out: TxOutput | None = None) -> Calibration:
        return Calibration(
            measured_on=rx.measured_on,
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
        return copy(f"校正ファイル（{band} MHz 帯・机上試験 {rx.source_bench}）")
    if role != "tx":
        raise SessionError(f"知らない役割です: {role}")
    in_band = [t for t in cal.tx_outputs if t.band == band]
    matches = [t for t in in_band if (t.power_cdbm, t.channel) == (power_cdbm, channel)]
    if not matches:
        have = ", ".join(f"{t.power_cdbm / 100:g} dBm・ch {t.channel}" for t in in_band)
        raise SessionError(
            f"個体 {cal.device_id} の校正ファイルに、{band} MHz 帯の設定 "
            f"{(power_cdbm or 0) / 100:g} dBm・チャネル {channel} の出力がありません"
            f"（あるのは: {have or 'なし'}）"
        )
    # meter を優先する（導いた値より直接測った値）。
    out = sorted(matches, key=lambda t: TX_OUTPUT_SOURCES.index(t.source))[0]
    return copy(
        f"校正ファイル（{band} MHz 帯・机上試験 {rx.source_bench}）・送信出力は "
        f"{out.source}（{out.measured_on}）",
        out,
    )
