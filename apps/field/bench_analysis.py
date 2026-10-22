"""
apps/field/bench_analysis.py
=============================
**ステージ0 の判定**（Field phase 1・増分6）。`bench.py` が残した生ログとステップの記録だけから
数え直す＝ステージ0b でアッテネータ・ケーブル・TX の出力の実測値（`ref`）が分かったら、
**測り直さずに**判定と校正をやり直せる。

    python -m apps.field.cli bench analyze <机上試験のフォルダ> [--ref ref.json]
                                             [--calib-dir 校正フォルダ]

**1 対で測って分かること**（2026-09-19 に整理）
  A→B の読み ＝ R_B(P_A − L)、B→A の読み ＝ R_A(P_B − L)
  - ref が無い（ステージ0a だけ）＝R の**形**（傾き・線形性・ディザ・受信率の落ち方）と
    個体差は分かるが、**絶対値は P と R の和の形でしか出ず、2 つに分けられない**。
    入力は「設定した送信電力 − 公称の損失」で書く（公称と明記する）。
  - ref に P_A・P_B（パワー計）がある＝R_A・R_B の絶対値が決まる → 校正ファイル。
  - ref に信号発生器で直接入れた点（`rx_direct`）がある＝R を別の経路でも求め、
    **閉合チェック**（±1 dB）をする。

⚠️ **ここに並ぶしきい値は「目安」**＝打ち切りのしきい値・感度・最小件数を**決める**のは
作業者（§6.1-2A・§6.6）。判定は数を並べて、目安との比較を添えるだけ。

🔑 **ref は最上位を帯域で分ける**（I-243）＝`{"920": {…}, "2400": {…}}`。固定損失・
アッテネータ・TX の出力・信号発生器の点はどれも周波数で違う。机上試験は 1 回 1 帯域
（計画の `band`）なので、判定はその帯域の節だけを見る。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apps.field.bench import (
    TAIL_S, BenchError, plan_band, read_bench, read_steps, started_on, unsent_between,
)
from apps.field.calib import DeviceCalibration, RxCurve, TxOutput, write_device_calibration
from apps.field.mavlink.dialect import Dialect, load_dialect
from apps.field.mavlink.reader import FrameStream, ReadStats, mac
from apps.field.session import BANDS, SessionError, parse_utc, read_raw, rssi_saturated

# 目安（決めるのは作業者）。
FULL_RATE = 0.99             # 線形性・ディザを見るステップ＝ほぼ全部届いたステップ
LINEAR_TOL_DB = 0.5          # 当てはめの端点をこれ以上外れたら、線形域の外とみなして外す
LINEAR_OK_DB = 1.0           # 線形域の中の最大の外れの目安（§7 の絶対確度 ±1 dB）
CLOSURE_OK_DB = 1.0          # 閉合チェックの目安
RATE_CROSSINGS = (0.9, 0.5)  # 受信率がこれを切る入力を出す（感度・打ち切りの材料）
MIN_FIT_POINTS = 4
# 受信サンプルをステップに対応づけるときの時刻の余裕（TX の控えと RX のサンプルは別の USB）。
_MATCH_SLACK_S = 1.0

REPORT_JSON = "report.json"
REPORT_MD = "report.md"


# --- 生ログからステップごとの数へ ---------------------------------------------------


@dataclass
class _UnitLog:
    echoes: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)
    samples: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)
    stats: ReadStats = field(default_factory=ReadStats)
    text_bytes: int = 0


def _load_unit(directory: Path, dialect: Dialect) -> _UnitLog:
    log = _UnitLog()
    stream = FrameStream(stats=log.stats, dialect=dialect)
    for pc_utc, chunk in read_raw(directory):
        when = parse_utc(pc_utc)
        for m in stream.feed(chunk):
            if m.name == "RADIOSIM_FIELD_TX_PACKET":
                log.echoes.append((when, m.fields))
            elif m.name == "RADIOSIM_FIELD_RX_SAMPLE":
                log.samples.append((when, m.fields))
        # 文字の行（返答・報告）はフレームの外として数えられている＝線の汚れと分ける。
        log.text_bytes += sum(len(line.encode("utf-8")) + 2 for line in stream.take_lines())
    stream.close()
    return log


@dataclass(frozen=True)
class StepStats:
    step: int
    kind: str
    label: str
    tx_unit: str
    rx_unit: str
    power_cdbm: int
    channel: int
    atten_db: int | None
    sent: int                 # TX の控えの番号の幅
    received: int
    usb_lost: int             # RX の sample_seq の欠け（電波ではなく PC までの間）
    rssi: tuple[int, ...]     # 飽和（生値 −256）を除いた生値
    noise_mean: float | None
    # 生値 −256（E220 の飽和の回り込み・B-361）＝届いた数には入れるが、レベルには使わない。
    saturated: int = 0

    @property
    def rate(self) -> float:
        usable = self.sent - self.usb_lost
        return 0.0 if usable <= 0 else self.received / usable

    @property
    def mean(self) -> float | None:
        # 飽和が 1 つでもあれば平均は名乗らない（残りの値も頭打ちの近く＝当てはめ・個体差・
        # 電力・チャネルの点から外れる。どれも `mean is not None` で絞っている）。
        if self.saturated or not self.rssi:
            return None
        return sum(self.rssi) / len(self.rssi)

    @property
    def std(self) -> float | None:
        if self.saturated or len(self.rssi) < 2:
            return None
        m = sum(self.rssi) / len(self.rssi)
        return math.sqrt(sum((x - m) ** 2 for x in self.rssi) / (len(self.rssi) - 1))

    @property
    def distinct(self) -> int:
        return len(set(self.rssi))


def step_stats(row: dict[str, Any], tx: _UnitLog, rx: _UnitLog) -> StepStats:
    """ステップの数え。**送った番号は TX の控えから**（USB で控えが欠けても幅で数え、
    送らなかった周期を引く）、
    受けたものは RX から（送信機・両方の設定番号・番号の幅・時刻で絞る）。"""
    start, end = parse_utc(row["start_utc"]), parse_utc(row["end_utc"])
    echoes = [f for when, f in tx.echoes
              if start <= when <= end and f["config_id"] == row["tx_config_id"]]
    rssi: list[int] = []
    saturated = 0
    noise: list[int] = []
    sample_seqs: list[int] = []
    sent = 0
    if echoes:
        low, high = min(f["seq"] for f in echoes), max(f["seq"] for f in echoes)
        # 送らなかった周期（E220 が受け付けなかった）は送った数に入れない（I-165）。
        sent = high - low + 1 - unsent_between(echoes)
        # 時刻は**番号の取り違えを防ぐ枠**でしかない（対応づけは番号と設定番号）。
        # ⚠️ 下限をステップの頭ちょうどにしない＝TX と RX は別の USB で届くので、ステップの頭の
        # 番号の受信が控えより先に PC に着き、1 件だけ「届かなかった」ことになる。
        early = start.timestamp() - _MATCH_SLACK_S
        late = end.timestamp() + TAIL_S + _MATCH_SLACK_S
        for when, f in rx.samples:
            if (mac(f["tx_id"]) == row["tx_device"]
                    and f["tx_config_id"] == row["tx_config_id"]
                    and f["config_id"] == row["rx_config_id"]
                    and low <= f["seq"] <= high
                    and early <= when.timestamp() <= late):
                if rssi_saturated(f["rssi_raw"]):
                    saturated += 1
                else:
                    rssi.append(f["rssi_raw"])
                noise.append(f["noise_floor_raw"])
                sample_seqs.append(f["sample_seq"])
    sample_seqs.sort()
    usb_lost = sum(max(0, b - a - 1) for a, b in zip(sample_seqs, sample_seqs[1:]))
    return StepStats(
        step=row["step"], kind=row["kind"], label=row["label"],
        tx_unit=row["tx_unit"], rx_unit=row["rx_unit"],
        power_cdbm=row["power_cdbm"], channel=row["channel"], atten_db=row["atten_db"],
        sent=sent, received=len(rssi) + saturated, usb_lost=usb_lost, rssi=tuple(rssi),
        noise_mean=sum(noise) / len(noise) if noise else None, saturated=saturated,
    )


# --- 入力レベル ----------------------------------------------------------------


@dataclass(frozen=True)
class Levels:
    """RX に入った電力の求め方。ref が無ければ公称（設定した電力・公称の損失）。"""

    fixed_loss_db: float
    fixed_loss_measured: bool
    tens: dict[int, float]
    ones: dict[int, float]
    tx_output: dict[tuple[str, int, int], dict[str, Any]]   # (個体, 電力, ch) → 実測の行

    def atten(self, total: int) -> float:
        tens = min(total // 10 * 10, 110)
        ones = total - tens
        return self.tens.get(tens, float(tens)) + self.ones.get(ones, float(ones))

    def tx_dbm(self, device: str, power_cdbm: int, channel: int) -> tuple[float, bool]:
        """(送信電力, 実測か)。"""
        found = self.tx_output.get((device, power_cdbm, channel))
        if found is not None:
            return float(found["dbm"]), True
        return power_cdbm / 100, False

    def input_dbm(self, device: str, s: StepStats) -> tuple[float, bool]:
        assert s.atten_db is not None
        p, absolute = self.tx_dbm(device, s.power_cdbm, s.channel)
        return p - self.fixed_loss_db - self.atten(s.atten_db), absolute


def check_ref(ref: dict[str, Any]) -> None:
    """最上位が帯域でない ref（2.4 GHz を戻す前の平らな形）を断る＝黙って読むと、
    実測値が全部無いものとして公称で判定してしまう。"""
    if not isinstance(ref, dict) or set(ref) - set(BANDS):
        raise SessionError(
            "ref の最上位は帯域（" + "・".join(f'"{b}"' for b in BANDS) + "）で分けます"
            "＝古い形なら、中身を \"920\" の下へ移してください"
        )
    # 節と表の形も見る＝読み手（判定・`cont`）が型の違いで途中で落ちないように（B-356）。
    # null は「無い」と同じに読む。
    for band, section in ref.items():
        if section is None:
            continue
        if not isinstance(section, dict) or any(
            not isinstance(section.get(k), (list, type(None))) for k in ("tx_output", "rx_direct")
        ):
            raise SessionError(
                f'ref の "{band}" の形が違います（節は {{…}}・tx_output と rx_direct は […]）'
            )


def ref_section(ref: dict[str, Any] | None, band: str) -> dict[str, Any]:
    """その帯域の実測値（無ければ空＝公称で判定する）。"""
    if ref is None:
        return {}
    check_ref(ref)
    return ref.get(band) or {}


def levels_from(plan: dict[str, Any], ref: dict[str, Any] | None) -> Levels:
    ref = ref_section(ref, plan_band(plan))
    fixed = ref.get("fixed_loss_db")
    step = ref.get("step_attenuator") or {}
    outputs: dict[tuple[str, int, int], dict[str, Any]] = {}
    for t in ref.get("tx_output") or []:
        key = (t["device_id"].lower(), int(t["power_cdbm"]), int(t["channel"]))
        outputs[key] = t          # 同じ条件を 2 度測ったら後の行（測り直し）を使う
    return Levels(
        fixed_loss_db=float(plan["fixed_loss_db"] if fixed is None else fixed),
        fixed_loss_measured=fixed is not None,
        tens={int(k): float(v) for k, v in (step.get("tens") or {}).items()},
        ones={int(k): float(v) for k, v in (step.get("ones") or {}).items()},
        tx_output=outputs,
    )


# --- 当てはめ ------------------------------------------------------------------


@dataclass(frozen=True)
class Fit:
    offset_db: float
    scale_db_per_count: float
    points: tuple[tuple[float, float], ...]      # (平均の生値, 入力 dBm)
    dropped: tuple[tuple[float, float], ...]     # 線形域の外として外した点
    max_residual_db: float

    def dbm(self, raw: float) -> float:
        return self.offset_db + self.scale_db_per_count * raw


def _lsq(points: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    if sxx == 0:
        raise SessionError("生値が 1 つの値しか取っていないので傾きを出せません")
    scale = sum((x - mx) * (y - my) for x, y in points) / sxx
    return my - scale * mx, scale


def fit_linear(points: list[tuple[float, float]]) -> Fit | None:
    """入力 dBm ＝ offset ＋ scale × 平均の生値。**両端から**、外れが LINEAR_TOL_DB を
    超える点を 1 つずつ外す（上は飽和・下は感度付近の偏り）。中の点は外さない＝
    中の外れは線形性そのものの結果として残す。"""
    pts = sorted(points, key=lambda p: p[1])
    dropped: list[tuple[float, float]] = []
    while len(pts) >= MIN_FIT_POINTS:
        offset, scale = _lsq(pts)

        def resid(p: tuple[float, float]) -> float:
            return abs(offset + scale * p[0] - p[1])

        ends = [(resid(pts[0]), 0), (resid(pts[-1]), len(pts) - 1)]
        worst, index = max(ends)
        if worst <= LINEAR_TOL_DB or len(pts) == MIN_FIT_POINTS:
            return Fit(offset, scale, tuple(pts), tuple(dropped),
                       max(resid(p) for p in pts))
        dropped.append(pts.pop(index))
    return None


def rate_crossing(points: list[tuple[float, float]], threshold: float) -> float | None:
    """受信率が threshold を**上から切る**入力 dBm（隣のステップどうしの直線補間）。
    入力の高い側から見て、最初に threshold を下回ったステップと、その 1 つ上のステップの間。"""
    pts = sorted(points, key=lambda p: -p[0])       # 入力の高い順
    for (x1, r1), (x2, r2) in zip(pts, pts[1:]):
        if r1 >= threshold > r2:
            return x1 + (threshold - r1) * (x2 - x1) / (r2 - r1)
    return None


# --- 判定 ----------------------------------------------------------------------


def analyze(
    directory: str | os.PathLike[str], ref: dict[str, Any] | None = None,
    dialect: Dialect | None = None,
) -> dict[str, Any]:
    directory = Path(directory)
    dialect = dialect or load_dialect()
    bench = read_bench(directory)
    if bench.get("purpose", "bench") != "bench":
        # 動作確認は減衰量を記録しない＝判定の材料にならない。
        raise BenchError(f"机上試験のフォルダではありません（{bench['purpose']}）: {directory}")
    plan = bench["plan"]
    units = {u: v["device_id"] for u, v in bench["units"].items()}
    logs = {u: _load_unit(directory / u, dialect) for u in units}
    steps = [step_stats(r, logs[r["tx_unit"]], logs[r["rx_unit"]]) for r in read_steps(directory)]
    band = plan_band(plan)
    levels = levels_from(plan, ref)
    report: dict[str, Any] = {
        "bench": directory.name,
        "band": band,
        "firmware_version": bench["firmware_version"],
        "software_commit": bench["software_commit"],
        "units": units,
        "levels": {
            "fixed_loss_db": levels.fixed_loss_db,
            "fixed_loss_measured": levels.fixed_loss_measured,
            "step_attenuator_measured": bool(levels.tens or levels.ones),
        },
        "uart": {
            u: {"frames": log.stats.frames, "crc_errors": log.stats.crc_errors,
                "unknown_msgid": log.stats.unknown_msgid,
                "noise_bytes": max(0, log.stats.skipped_bytes - log.text_bytes)}
            for u, log in logs.items()
        },
    }
    report["leak"] = [
        {"direction": _dir(s), "sent": s.sent, "received": s.received,
         "max_rssi": max(s.rssi) if s.rssi else None, "saturated": s.saturated,
         "pass": s.received == 0}
        for s in steps if s.kind == "leak"
    ]
    report["warmup"] = _warmup(steps)
    report["directions"] = {}
    fits: dict[str, Fit] = {}
    outside: set[int] = set()
    for tx in units:
        rx = _other(tx)
        d = _direction(steps, tx, rx, units[tx], levels)
        report["directions"][f"{tx}->{rx}"] = d["report"]
        outside |= d["dropped_atten"]
        if d["fit"] is not None:
            fits[rx] = d["fit"]
    report["pair_difference"] = _pair_difference(steps, outside)
    report["power"] = _power(steps, fits)
    report["channels"] = _channels(steps)
    report["closure"] = _closure(ref_section(ref, band), units, fits)
    report["usb_lost_total"] = sum(s.usb_lost for s in steps)
    report["_fits"] = fits
    report["_steps"] = steps
    report["_levels"] = levels
    report["_bench"] = bench
    return report


def _dir(s: StepStats) -> str:
    return f"{s.tx_unit}->{s.rx_unit}"


def _other(u: str) -> str:
    return "b" if u == "a" else "a"


def _warmup(steps: list[StepStats]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in steps:
        if s.kind == "warmup" and s.mean is not None:
            out.setdefault(_dir(s), []).append(round(s.mean, 3))
    return {d: {"means": m, "drift": round(m[-1] - m[0], 3)} for d, m in out.items()}


def _direction(
    steps: list[StepStats], tx: str, rx: str, tx_device: str, levels: Levels
) -> dict[str, Any]:
    level_steps = [s for s in steps if s.kind == "level" and s.tx_unit == tx]
    rows = []
    fit_points: list[tuple[float, float]] = []
    fit_atten: list[int] = []
    rate_points: list[tuple[float, float]] = []
    absolute_all = True
    dither_fail = []
    for s in sorted(level_steps, key=lambda s: (s.atten_db, s.step)):
        x, absolute = levels.input_dbm(tx_device, s)
        absolute_all = absolute_all and absolute
        if s.sent - s.usb_lost > 0 and s.usb_lost == 0:
            rate_points.append((x, s.rate))
        rows.append({
            "step": s.step, "label": s.label, "atten_db": s.atten_db, "input_dbm": round(x, 2),
            "sent": s.sent, "received": s.received, "usb_lost": s.usb_lost,
            "saturated": s.saturated, "rate": round(s.rate, 4),
            "rssi_mean": None if s.mean is None else round(s.mean, 3),
            "rssi_std": None if s.std is None else round(s.std, 3),
            "distinct": s.distinct,
            "noise_mean": None if s.noise_mean is None else round(s.noise_mean, 2),
            "histogram": dict(sorted(Counter(s.rssi).items())),
        })
        if s.rate >= FULL_RATE and s.mean is not None:
            fit_points.append((s.mean, x))
            fit_atten.append(s.atten_db)
            if s.distinct < 2:
                dither_fail.append(s.label)
    fit = fit_linear(fit_points) if len(fit_points) >= MIN_FIT_POINTS else None
    report = {
        "input": "absolute" if absolute_all and level_steps else "nominal",
        "steps": rows,
        "fit": None if fit is None else {
            "offset_db": round(fit.offset_db, 3),
            "scale_db_per_count": round(fit.scale_db_per_count, 4),
            "range_dbm": [round(fit.points[0][1], 2), round(fit.points[-1][1], 2)],
            "max_residual_db": round(fit.max_residual_db, 3),
            "dropped_dbm": [round(p[1], 2) for p in fit.dropped],
            "pass": fit.max_residual_db <= LINEAR_OK_DB,
        },
        "dither": {
            "checked": len(fit_points), "failed": dither_fail, "pass": not dither_fail,
        },
        "rate_crossings_dbm": {
            str(t): (None if (c := rate_crossing(rate_points, t)) is None else round(c, 2))
            for t in RATE_CROSSINGS
        },
    }
    # 線形域の外として外した段の減衰量（2 台の差からも外す＝B-352）。
    dropped_atten = set() if fit is None else {
        a for p, a in zip(fit_points, fit_atten) if p in fit.dropped}
    return {"report": report, "fit": fit, "dropped_atten": dropped_atten}


def _pair_difference(steps: list[StepStats], outside: set[int]) -> dict[str, Any]:
    """同じ減衰量での A→B と B→A の平均の差（生値）＝(P_A − P_B) ＋ (R_B − R_A) の和。
    減衰量によって差が動けば、2 台の R の**形**が違う（線形性の個体差）。
    どちらかの向きの当てはめが線形域の外として外した段（`outside`＝飽和など）は入れない
    （B-352）＝飽和した段の差は縮むので、入れると平均が寄り、振れ幅が広がる。"""
    by: dict[int, dict[str, float]] = {}
    for s in steps:
        if s.kind == "level" and s.rate >= FULL_RATE and s.mean is not None \
                and s.atten_db is not None and s.atten_db not in outside:
            by.setdefault(s.atten_db, {})[_dir(s)] = s.mean
    diffs = {a: v["a->b"] - v["b->a"] for a, v in sorted(by.items())
             if "a->b" in v and "b->a" in v}
    if not diffs:
        return {"per_atten": {}, "mean": None, "spread": None}
    values = list(diffs.values())
    return {
        "per_atten": {str(a): round(v, 3) for a, v in diffs.items()},
        "mean": round(sum(values) / len(values), 3),
        "spread": round(max(values) - min(values), 3),
    }


def _power(steps: list[StepStats], fits: dict[str, Fit]) -> dict[str, Any]:
    """TX の設定の刻みが出力どおりに効くか。**RX は同じ・減衰量も同じ**なので、読みの
    差は TX の出力の差だけ（RX の傾きで dB に直す）。"""
    out: dict[str, Any] = {}
    for tx in ("a", "b"):
        pts = [(s.power_cdbm, s.mean) for s in steps
               if s.kind == "power" and s.tx_unit == tx and s.mean is not None
               and s.rate >= FULL_RATE]
        rx_fit = fits.get(_other(tx))
        if len(pts) < 2:
            continue
        scale = rx_fit.scale_db_per_count if rx_fit is not None else 1.0
        base_p, base_m = max(pts)            # 最大の電力の点を基準に差を見る
        rows = []
        for p, m in sorted(pts):
            measured = (m - base_m) * scale
            rows.append({"power_cdbm": p, "rssi_mean": round(m, 3),
                         "delta_db": round(measured, 3),
                         "error_db": round(measured - (p - base_p) / 100, 3)})
        out[f"{tx}->{_other(tx)}"] = {
            "rows": rows, "scale_from_fit": rx_fit is not None,
            "max_error_db": max(abs(r["error_db"]) for r in rows),
        }
    return out


def _channels(steps: list[StepStats]) -> dict[str, Any]:
    out: dict[str, dict[str, float]] = {}
    for s in steps:
        if s.kind == "channel" and s.mean is not None and s.rate >= FULL_RATE:
            out.setdefault(_dir(s), {})[str(s.channel)] = round(s.mean, 3)
    return out


def _closure(
    section: dict[str, Any], units: dict[str, str], fits: dict[str, Fit]
) -> list[dict[str, Any]]:
    """信号発生器で直接入れた点（その帯域の `rx_direct`）と、TX 経由で求めた R を突き合わせる。"""
    rows = []
    by_device = {v.lower(): u for u, v in units.items()}
    for point in section.get("rx_direct") or []:
        unit = by_device.get(point["device_id"].lower())
        fit = fits.get(unit) if unit else None
        if fit is None:
            continue
        predicted = fit.dbm(float(point["rssi_mean"]))
        err = predicted - float(point["input_dbm"])
        rows.append({"unit": unit, "input_dbm": point["input_dbm"],
                     "rssi_mean": point["rssi_mean"], "error_db": round(err, 3),
                     "pass": abs(err) <= CLOSURE_OK_DB})
    return rows


# --- 校正ファイル ----------------------------------------------------------------


def device_calibrations(report: dict[str, Any]) -> list[DeviceCalibration]:
    """**入力が実測（パワー計で測った TX）の向き**からだけ RX の校正を作る。
    公称の入力で作った校正は dBm を名乗れないので作らない（§6 の注意②）。"""
    bench = report["_bench"]
    levels: Levels = report["_levels"]
    fits: dict[str, Fit] = report["_fits"]
    steps: list[StepStats] = report["_steps"]
    units: dict[str, str] = report["units"]
    band: str = report["band"]
    measured_on = started_on(bench)
    out = []
    for unit, device in units.items():
        partner = _other(unit)
        direction = report["directions"].get(f"{partner}->{unit}")
        fit = fits.get(unit)
        rx: dict[str, RxCurve] = {}
        if fit is not None and direction is not None and direction["input"] == "absolute":
            # 入力が absolute＝レベル掃引の全ステップの TX 設定に実測の行がある。
            level_keys = sorted({(units[partner].lower(), s.power_cdbm, s.channel)
                                 for s in steps if s.kind == "level" and s.tx_unit == partner})
            meter = levels.tx_output[level_keys[0]]
            rx[band] = RxCurve(
                offset_db=round(fit.offset_db, 4),
                scale_db_per_count=round(fit.scale_db_per_count, 5),
                reference_unit_id=units[partner],
                reference_measured_on=str(meter["measured_on"]),
                linear_range_dbm=(round(fit.points[0][1], 2), round(fit.points[-1][1], 2)),
                fit_max_residual_db=round(fit.max_residual_db, 3),
                measured_on=measured_on, source_bench=report["bench"],
                software_commit=bench["software_commit"],
                note=("固定損失は実測" if levels.fixed_loss_measured else "固定損失は公称")
                     + "・ステップアッテネータは"
                     + ("実測" if levels.tens or levels.ones else "公称"),
            )
        out.append(DeviceCalibration(
            device_id=device, rx=rx,
            tx_outputs=_tx_outputs(band, device, unit, levels, steps, fits),
        ))
    return out


def _tx_outputs(
    band: str, device: str, unit: str, levels: Levels, steps: list[StepStats],
    fits: dict[str, Fit],
) -> tuple[TxOutput, ...]:
    rows = [
        TxOutput(band=band, power_cdbm=p, channel=ch, dbm=round(float(t["dbm"]), 3),
                 source="meter", measured_on=str(t["measured_on"]),
                 note=str(t.get("instrument", "")))
        for (dev, p, ch), t in sorted(levels.tx_output.items()) if dev == device.lower()
    ]
    rx_fit = fits.get(_other(unit))
    sweep = [s for s in steps if s.kind == "power" and s.tx_unit == unit
             and s.mean is not None and s.rate >= FULL_RATE]
    if rx_fit is None or not sweep:
        return tuple(rows)
    have = {(r.power_cdbm, r.channel) for r in rows}
    for anchor in list(rows):
        base = [s for s in sweep if (s.power_cdbm, s.channel) == (anchor.power_cdbm, anchor.channel)]
        if not base:
            continue
        base_mean = base[-1].mean
        assert base_mean is not None
        for s in sweep:
            if s.channel != anchor.channel or (s.power_cdbm, s.channel) in have:
                continue
            assert s.mean is not None
            delta = (s.mean - base_mean) * rx_fit.scale_db_per_count
            rows.append(TxOutput(
                band=band, power_cdbm=s.power_cdbm, channel=s.channel,
                dbm=round(anchor.dbm + delta, 3), source="bench_power_sweep",
                measured_on=anchor.measured_on,
                note=f"{anchor.power_cdbm / 100:g} dBm の実測から相対",
            ))
            have.add((s.power_cdbm, s.channel))
    return tuple(sorted(rows, key=lambda r: (r.channel, r.power_cdbm)))


def write_calibrations(report: dict[str, Any], calib_dir: str | os.PathLike[str]) -> list[str]:
    """その帯域の、作れた分だけを書き換え、機器ごとに何を書いたかの行を返す（もう片方の
    帯域の校正と、作れなかった部分の前の値は残る）。1 台も作れなければ何も書かずに止める
    （B-353＝空の校正で既存のファイルを消していた）。"""
    band = report["band"]
    cals = device_calibrations(report)
    if not any(c.rx or c.tx_outputs for c in cals):
        raise SessionError(
            f"{band} MHz 帯の校正を 1 台も作れませんでした（パワー計で測った ref が要ります）"
            "＝校正ファイルは書いていません")
    lines = []
    for c in cals:
        if not (c.rx or c.tx_outputs):
            lines.append(f"校正を作れませんでした: {c.device_id}（書いていません＝前の値のまま）")
            continue
        path = write_device_calibration(calib_dir, c, band)
        made = "・".join(name for name, ok in (("RX の曲線", c.rx), ("TX の出力表", c.tx_outputs)) if ok)
        kept = "" if c.rx and c.tx_outputs else "・作れなかった分は前の値のまま"
        lines.append(f"校正ファイル: {path}（{band} MHz 帯の{made}{kept}）")
    return lines


# --- 書き出し --------------------------------------------------------------------


def public(report: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in report.items() if not k.startswith("_")}


def write_report(directory: str | os.PathLike[str], report: dict[str, Any]) -> tuple[Path, Path]:
    directory = Path(directory)
    j = directory / REPORT_JSON
    j.write_text(json.dumps(public(report), indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    m = directory / REPORT_MD
    m.write_text(render_markdown(report), encoding="utf-8")
    return j, m


def _mark(ok: bool | None) -> str:
    return "—" if ok is None else ("合格" if ok else "不合格")


def render_markdown(report: dict[str, Any]) -> str:
    L: list[str] = []
    u = report["units"]
    L.append(f"# ステージ0 の判定（{report['bench']}・{report['band']} MHz 帯）\n")
    # 人が読める版（日付と件名）は git を引く CLI が足す＝無ければコミットのまま。
    firmware = report.get("firmware_label") or f"`{report['firmware_version']}`"
    L.append(f"- A = `{u['a']}`／B = `{u['b']}`／ファーム {firmware}"
             f"／判定のコミット `{report['software_commit']}`")
    lv = report["levels"]
    L.append(f"- 固定損失 {lv['fixed_loss_db']:g} dB（{'実測' if lv['fixed_loss_measured'] else '公称'}）"
             f"／ステップアッテネータ {'実測' if lv['step_attenuator_measured'] else '公称'}")
    L.append("- ⚠️ 目安は判定の材料で、しきい値を決めるのは作業者です。\n")

    L.append("## 読み取りの健全性\n")
    L.append("| 機器 | フレーム | CRC 不一致 | 知らない ID | 雑音バイト |")
    L.append("|---|---|---|---|---|")
    for name, s in report["uart"].items():
        L.append(f"| {name.upper()} | {s['frames']} | {s['crc_errors']} | {s['unknown_msgid']}"
                 f" | {s['noise_bytes']} |")
    L.append(f"\nPC までの間で落ちたサンプル（全ステップ）: {report['usb_lost_total']}\n")

    L.append("## 漏れ（終端器）\n")
    if report["leak"]:
        for r in report["leak"]:
            L.append(f"- {r['direction'].upper()}: {r['received']}/{r['sent']} 受信"
                     f"（{_mark(r['pass'])}・目安は 0）")
    else:
        L.append("- 測っていません")

    L.append("\n## 温め\n")
    if report["warmup"]:
        for d, w in report["warmup"].items():
            L.append(f"- {d.upper()}: 平均 {w['means']}（始めから終わりまで {w['drift']:+.3f}）")
    else:
        L.append("- 測っていません")

    for d, rep in report["directions"].items():
        rx = d.split("->")[1].upper()
        L.append(f"\n## {d.upper()}（RX＝{rx} の特性・入力は{'実測' if rep['input'] == 'absolute' else '公称'}）\n")
        f = rep["fit"]
        if f is None:
            L.append("- 当てはめる点が足りません")
        else:
            L.append(f"- 当てはめ: dBm = {f['offset_db']:+.3f} + {f['scale_db_per_count']:.4f} × 生値"
                     f"（{f['range_dbm'][0]}〜{f['range_dbm'][1]} dBm）")
            L.append(f"- 線形性: 最大の外れ {f['max_residual_db']:.3f} dB（{_mark(f['pass'])}"
                     f"・目安 {LINEAR_OK_DB} dB）・線形域の外として外した入力 {f['dropped_dbm']}")
        dth = rep["dither"]
        L.append(f"- ディザ（2 値以上に散るか）: {dth['checked']} ステップ中 不合格 {len(dth['failed'])}"
                 f"（{_mark(dth['pass'] if dth['checked'] else None)}）{dth['failed'] or ''}")
        cr = rep["rate_crossings_dbm"]
        L.append("- 受信率が切る入力: " + "・".join(
            f"{float(t):.0%} → {'—' if v is None else f'{v} dBm'}" for t, v in cr.items()))
        L.append("\n| ステップ | 減衰 | 入力 dBm | 受信 | 率 | 平均 | σ | 値の数 | 雑音 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for r in rep["steps"]:
            L.append(
                f"| {r['label']} | {r['atten_db']} | {r['input_dbm']} | {r['received']}/{r['sent']}"
                f"{'（USB 欠け ' + str(r['usb_lost']) + '）' if r['usb_lost'] else ''}"
                f"{'（飽和 ' + str(r['saturated']) + '）' if r.get('saturated') else ''}"
                f" | {r['rate']:.1%} | {'' if r['rssi_mean'] is None else r['rssi_mean']}"
                f" | {'' if r['rssi_std'] is None else r['rssi_std']} | {r['distinct']}"
                f" | {'' if r['noise_mean'] is None else r['noise_mean']} |"
            )

    pdiff = report["pair_difference"]
    L.append("\n## 個体差（同じ減衰量での A→B − B→A・生値）\n")
    if pdiff["mean"] is None:
        L.append("- 両方向がそろったステップがありません")
    else:
        L.append(f"- 平均 {pdiff['mean']:+.3f}・減衰量による振れ幅 {pdiff['spread']:.3f}"
                 "（振れ幅が大きいと 2 台の特性の形が違う）")

    L.append("\n## 送信電力の設定と出力\n")
    if not report["power"]:
        L.append("- 測っていません")
    for d, p in report["power"].items():
        L.append(f"- {d.upper()}: 設定の差と出力の差の食い違い 最大 {p['max_error_db']:.3f} dB"
                 f"{'' if p['scale_from_fit'] else '（RX の傾きが無いので 1 生値＝1 dB とした）'}")

    L.append("\n## チャネル\n")
    if not report["channels"]:
        L.append("- 測っていません")
    for d, c in report["channels"].items():
        L.append(f"- {d.upper()}: " + "・".join(f"ch {k} → {v}" for k, v in c.items()))

    L.append("\n## 閉合チェック（信号発生器の点）\n")
    if not report["closure"]:
        L.append("- 点がありません（ref の rx_direct）")
    for r in report["closure"]:
        L.append(f"- {r['unit'].upper()}: 入力 {r['input_dbm']} dBm → 外れ {r['error_db']:+.3f} dB"
                 f"（{_mark(r['pass'])}・目安 ±{CLOSURE_OK_DB} dB）")
    return "\n".join(L) + "\n"


def ref_template() -> dict[str, Any]:
    """ステージ0b の実測値を書く雛形＝帯域ごとに同じ形の節（2.4 GHz の tx_output は
    `cont` が足す・920 MHz の tx_output の測り方は I-242）。"""
    return {
        band: {
            "fixed_loss_db": None,
            "step_attenuator": {"tens": {}, "ones": {}},
            "tx_output": [],
            "rx_direct": [],
        }
        for band in BANDS
    }


def read_ref(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        ref = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SessionError(f"ref がありません: {path}") from e
    except json.JSONDecodeError as e:
        raise SessionError(f"ref が JSON として読めません: {path}（{e}）") from e
    except (UnicodeDecodeError, OSError) as e:
        # UTF-8 でない・フォルダ・権限など＝読み手（`cont` の書き戻し）が測った値を載せて止まれるように（B-358）
        raise SessionError(f"ref を読めません: {path}（{e}）") from e
    check_ref(ref)
    return ref


def add_tx_output(ref: dict[str, Any], band: str, row: dict[str, Any]) -> None:
    """その帯域の `tx_output` の末尾に 1 行足す（既存の行はそのまま・null の節や表は空として扱う）。"""
    section = ref.get(band) or {}
    section["tx_output"] = [*(section.get("tx_output") or []), row]
    ref[band] = section


def write_ref(path: str | os.PathLike[str], ref: dict[str, Any]) -> None:
    """書き切ってから置き換える＝ref は手で測った値の積み重ねで作り直せない
    （直に開くと開いた時点で中身が消え、途中で落ちると切れた ref が残る・B-356）。"""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".ref-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(ref, indent=2, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
