"""
tests/test_field_bench.py
==========================
実測補助アプリ（`apps/field`）の**ステージ0 の机上試験**（増分6）を検証する。

🔑 **2 台の機器と空中の経路を模擬して、真の値を判定が取り戻せるか**を見る。
模擬の側だけが知っている値＝各個体の送信出力のずれ・受信の生値のずれ・感度・飽和。
  - 役割はコマンドで入れ替わり、人に頼むのはアッテネータと終端器だけであること
  - 公称（ref なし）では絶対値を名乗らず、校正ファイルも作らないこと
  - ref（パワー計の実測）を渡すと、**測り直さずに** RX の絶対値と TX の出力の表が出ること
  - 漏れ・ディザの欠如・飽和を見逃さないこと
  - 校正ファイルが record でセッションへ写ること（個体 ID で・動いている設定の行を）

⚠️ シリアルポートは触らない（`BenchRunner` は `read`/`write`/`in_waiting` だけを使う）。
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from apps.field import bench as B
from apps.field import bench_analysis as BA
from apps.field import calib as C
from apps.field import cli
from apps.field import recorder as REC
from apps.field import session as S
from apps.field.mavlink import dialect as D
from apps.field.mavlink import gen_c as G
from apps.field.mavlink import reader as R

_BASE = datetime(2026, 9, 20, 1, 0, 0, tzinfo=timezone.utc)
FIXED_LOSS = 58.0          # 13 dBm − 58 dB＝旧ファームの 15 dBm − 60 dB と同じ水準
TICK = 0.01                 # 1 回の読み出しで進む模擬の時間 [s]


@pytest.fixture(scope="module")
def dialect() -> D.Dialect:
    return D.load_dialect()


# --- 模擬 ----------------------------------------------------------------------


class Device:
    """ファームの振る舞いの写し（設定の再送・中継・コマンドで再起動・番号の振り方）。"""

    def __init__(self, world: "World", mac: bytes, *, tx_offset_db: float, rx_offset: float,
                 sensitivity_dbm: float, role: str = "rx"):
        self.world = world
        self.mac = mac
        self.tx_offset_db = tx_offset_db      # 真の出力 − 設定
        self.rx_offset = rx_offset            # 生値 − 入力 dBm
        self.sensitivity_dbm = sensitivity_dbm
        self.role = role
        self.band = "920"
        self.channel = 0
        self.power_dbm = 13
        # チャネルと電力は帯域ごとに別に持つ（ファームの NVS と同じ）。
        self.stored = {"2400": (1, 15)}
        self.config_id = 1
        self.seq = 0
        self.sample_seq: dict[bytes, int] = {}
        self.buffer = bytearray()
        self.pending = b""
        self.paused_until = 0.0
        self.next_config = 0.0
        self.next_packet = 0.0
        self.next_air_config = 0.0

    # ポート --------------------------------------------------------------------

    @property
    def in_waiting(self) -> int:
        return len(self.buffer)

    def read(self, size: int) -> bytes:
        self.world.advance(TICK)
        out = bytes(self.buffer[:size])
        del self.buffer[:size]
        return out

    def write(self, data: bytes) -> int:
        self.pending += data
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            self.command(line.decode("ascii").strip())
        return len(data)

    # ファーム ------------------------------------------------------------------

    def text(self, line: str) -> None:
        self.buffer += line.encode("utf-8") + b"\r\n"

    def config_values(self) -> dict:
        return dict(
            config_id=self.config_id, role=0 if self.role == "tx" else 1,
            device_id=tuple(self.mac), firmware_version="afd69ae7db", channel=self.channel,
            band=int(self.band), rate_kbps=1000 if self.band == "2400" else 0,
            air_sf=0 if self.band == "2400" else 7, air_bw_khz=0 if self.band == "2400" else 500,
            tx_power_cdbm=round(self.power_dbm * 100), tx_interval_ms=100,
            detector=1, integration_window_us=0, integration_samples=1, antenna=2,
        )

    def frame(self, name: str, values: dict) -> bytes:
        return G.encode_frame(self.world.dialect.messages_by_name[name], values)

    def command(self, line: str) -> None:
        self.world.commands.append((self.mac, line))
        parts = line.split()
        if parts == ["show"]:
            self.text("OK")
            self.buffer += self.frame("RADIOSIM_FIELD_CONFIG", self.config_values())
            return
        key, value = parts
        before = (self.role, self.band, self.channel, self.power_dbm)
        if key == "role":
            self.role = value
        elif key == "band" and value != self.band:
            self.stored[self.band] = (self.channel, self.power_dbm)
            self.band = value
            self.channel, self.power_dbm = self.stored.get(value, (0, 13))
        elif key == "channel":
            self.channel = int(value)
        elif key == "power":
            self.power_dbm = float(value)
        if (self.role, self.band, self.channel, self.power_dbm) == before:
            self.text("OK 変わっていません")
            return
        self.config_id += 1
        self.text(f"OK config_id={self.config_id} 再起動します")
        self.restart()

    def restart(self) -> None:
        self.seq = 0
        self.sample_seq = {}
        self.paused_until = self.world.t + 0.3
        self.next_config = self.paused_until
        self.next_packet = self.paused_until + 0.1
        self.next_air_config = self.paused_until

    def output_dbm(self) -> float:
        return self.power_dbm + self.tx_offset_db

    def tick(self, other: "Device") -> None:
        t = self.world.t
        if t < self.paused_until:
            return
        if t >= self.next_config:
            self.buffer += self.frame("RADIOSIM_FIELD_CONFIG", self.config_values())
            self.next_config = t + 1.0
        if self.role != "tx":
            return
        if t >= self.next_air_config:
            if other.hears(self):
                other.buffer += self.frame("RADIOSIM_FIELD_CONFIG", self.config_values())
            self.next_air_config = t + 1.0
        if t >= self.next_packet:
            self.buffer += self.frame("RADIOSIM_FIELD_TX_PACKET", dict(
                tx_id=tuple(self.mac), seq=self.seq, sent_mask=0xFFFFFFFF, unsent_total=0,
                config_id=self.config_id))
            other.receive(self)
            self.seq += 1
            self.next_packet += 0.1

    def tuned_to(self, tx: "Device") -> bool:
        return (self.role == "rx" and (self.band, self.channel) == (tx.band, tx.channel)
                and self.world.t >= self.paused_until)

    def hears(self, tx: "Device") -> bool:
        return self.tuned_to(tx) and self.world.input_dbm(tx) > self.sensitivity_dbm

    def receive(self, tx: "Device") -> None:
        if not self.tuned_to(tx):
            return
        level = self.world.input_dbm(tx)
        rate = min(1.0, max(0.0, (level - (self.sensitivity_dbm - 2)) / 4))
        if self.world.rng.random() >= rate:
            return
        heard = min(level, self.world.saturation_dbm)
        raw = round(heard + self.rx_offset + self.world.rng.gauss(0, self.world.noise))
        n = self.sample_seq.get(tx.mac, 0)
        self.sample_seq[tx.mac] = n + 1
        self.buffer += self.frame("RADIOSIM_FIELD_RX_SAMPLE", dict(
            rx_id=tuple(self.mac), tx_id=tuple(tx.mac), seq=tx.seq,
            rx_time_us=int(self.world.t * 1e6), rssi_raw=raw, noise_floor_raw=-95,
            config_id=self.config_id, tx_config_id=tx.config_id, sample_seq=n,
            sent_mask=0xFFFFFFFF, unsent_total=0))


class World:
    def __init__(self, dialect: D.Dialect, *, seed: int = 1, noise: float = 0.6,
                 saturation_dbm: float = -50.0, leak_loss_db: float | None = None):
        self.dialect = dialect
        self.rng = random.Random(seed)
        self.noise = noise
        self.saturation_dbm = saturation_dbm
        self.leak_loss_db = leak_loss_db
        self.t = 0.0
        self.atten = 0
        self.terminated = False
        self.prompts: list[str] = []
        self.commands: list[tuple[bytes, str]] = []
        self.a = Device(self, b"\xaa\xbb\xcc\x00\x00\x0a", tx_offset_db=-0.8, rx_offset=3.0,
                        sensitivity_dbm=-95.0, role="tx")
        self.b = Device(self, b"\xaa\xbb\xcc\x00\x00\x0b", tx_offset_db=+0.6, rx_offset=1.5,
                        sensitivity_dbm=-93.0, role="rx")

    def input_dbm(self, tx: Device) -> float:
        if self.terminated:
            return -200.0 if self.leak_loss_db is None else tx.output_dbm() - self.leak_loss_db
        return tx.output_dbm() - FIXED_LOSS - self.atten

    def advance(self, dt: float) -> None:
        self.t += dt
        self.a.tick(self.b)
        self.b.tick(self.a)

    def clock(self) -> str:
        return S.format_utc(_BASE + timedelta(seconds=self.t))

    def prompt(self, message: str) -> str:
        self.prompts.append(message)
        found = re.search(r"合計 (\d+) dB", message)
        if found:
            self.atten = int(found.group(1))
        elif "終端器を付けて" in message:
            self.terminated = True
        elif "元に戻して" in message:
            self.terminated = False
        return ""


def _plan(**over) -> dict:
    plan = B.default_plan()
    plan["fixed_loss_db"] = FIXED_LOSS
    plan["leak"]["duration_s"] = 2
    plan["warmup"].update(chunk_s=2, stable_chunks=2, max_chunks=6, stable_db=0.5)
    plan["coarse"]["duration_s"] = 4
    plan["fine"].update(duration_s=3, below_db=3, above_db=3)
    plan["power"].update(duration_s=4, dbm=[1, 5, 9, 13])
    plan["channels"]["duration_s"] = 3
    plan.update(over)
    return plan


def _runner(tmp_path: Path, world: World, plan: dict | None = None) -> B.BenchRunner:
    return B.BenchRunner(
        tmp_path, {"a": world.a, "b": world.b}, plan or _plan(),
        clock=world.clock, prompt=world.prompt, say=lambda _m: None,
        software_commit="abc123", dialect=world.dialect,
    )


@pytest.fixture(scope="module")
def full_run(tmp_path_factory, dialect):
    world = World(dialect)
    runner = _runner(tmp_path_factory.mktemp("bench"), world)
    runner.run()
    return world, runner


# --- 進め方 ----------------------------------------------------------------------


def test_the_units_are_told_apart_even_when_the_rx_relays_the_tx(tmp_path, dialect):
    """RX のポートには相手の TX の設定も中継されて流れる＝取り違えないこと。"""
    world = World(dialect)
    runner = _runner(tmp_path, world)
    runner.connect()
    assert runner.units["a"].device_id == "aa:bb:cc:00:00:0a"
    assert runner.units["b"].device_id == "aa:bb:cc:00:00:0b"
    bench = B.read_bench(runner.directory)
    assert bench["firmware_version"] == "afd69ae7db"


def test_a_step_measures_both_directions_with_one_knob_turn(full_run):
    """🔑 アッテネータ 1 ステップにつき頼むのは 1 回で、両方向（A→B・B→A）が測られること。"""
    world, runner = full_run
    rows = B.read_steps(runner.directory)
    level = [r for r in rows if r["kind"] == "level"]
    by_atten: dict[int, set[str]] = {}
    for r in level:
        by_atten.setdefault(r["atten_db"], set()).add(r["tx_unit"])
    assert all(v == {"a", "b"} for v in by_atten.values())
    knob = [p for p in world.prompts if "合計" in p]
    # 粗 12 ステップ＋細 7 ステップ＋温め 1＋電力 1（チャネルは電力と同じ減衰量なので頼まない）。
    assert len(knob) == 12 + 7 + 1 + 1
    # 役割の入れ替えは人に頼まない（コマンドで行う）。
    assert not any("入れ替" in p for p in world.prompts)
    assert any(line == "role tx" for _mac, line in world.commands)


def test_consecutive_steps_start_from_the_same_direction_to_save_a_swap(full_run):
    """直前のステップの終わりと同じ向きから始める＝ステップごとの入れ替えは 1 回で済むこと。"""
    _world, runner = full_run
    level = [r for r in B.read_steps(runner.directory) if r["kind"] == "level"]
    for prev, nxt in zip(level[1::2], level[2::2]):
        assert prev["tx_unit"] == nxt["tx_unit"]


def test_the_fine_sweep_brackets_the_knee(full_run):
    """細かい掃引は、粗い掃引で受信率が落ち始めたステップの前後であること。"""
    _world, runner = full_run
    k = runner.knee()
    fine = sorted({r["atten_db"] for r in B.read_steps(runner.directory)
                   if r["label"].startswith("細")})
    assert fine == list(range(k - 3, k + 4))


def test_the_power_sweep_stays_clear_of_the_knee(full_run):
    """電力掃引の減衰量は、最小の電力でも感度から十分離れていること。"""
    _world, runner = full_run
    atten = runner.power_atten()
    assert atten % 10 == 0 and atten <= runner.knee() - 25


def test_a_refused_command_stops_the_bench(tmp_path, dialect):
    world = World(dialect)
    runner = _runner(tmp_path, world)
    runner.connect()
    world.a.command = lambda line: world.a.text("ERR 知らないコマンドです")   # type: ignore[method-assign]
    with pytest.raises(B.BenchError, match="断りました"):
        runner.configure("a", role="rx", channel=0, power_dbm=13)


def test_the_operator_can_stop_at_a_prompt(tmp_path, dialect):
    world = World(dialect)
    runner = _runner(tmp_path, world)
    runner._prompt = lambda _m: "q"       # type: ignore[attr-defined]
    with pytest.raises(B.BenchAbort):
        runner.run(("coarse",))


# --- 判定 ------------------------------------------------------------------------


def test_without_ref_the_levels_are_nominal_and_no_calibration_is_written(full_run, tmp_path):
    """ステージ0a だけでは P と R を分けられない＝**dBm を名乗る校正ファイルを作らない**こと。"""
    _world, runner = full_run
    report = BA.analyze(runner.directory)
    for d in report["directions"].values():
        assert d["input"] == "nominal"
    cals = BA.device_calibrations(report)
    assert all(c.rx == {} and c.tx_outputs == () for c in cals)
    with pytest.raises(S.SessionError, match="920 MHz 帯の RX の校正がありません"):
        C.endpoint_calibration(cals[0], "rx", band="920")


def test_the_nominal_fit_recovers_the_shape_and_the_pair_difference(full_run):
    """公称でも傾き（≈1 dB/生値）と、2 台の読みの差の和が真の値に合うこと。"""
    world, runner = full_run
    report = BA.analyze(runner.directory)
    for d in report["directions"].values():
        assert d["fit"]["scale_db_per_count"] == pytest.approx(1.0, abs=0.03)
        assert d["fit"]["pass"]
        assert d["dither"]["pass"]
    # A→B − B→A ＝ (P_A − P_B) ＋ (R_B − R_A)
    expected = (world.a.tx_offset_db - world.b.tx_offset_db) + (world.b.rx_offset - world.a.rx_offset)
    # 0 dB の段は飽和して差が縮む＝当てはめが外した段は差にも入れない（B-352）。
    pdiff = report["pair_difference"]
    assert "0" not in pdiff["per_atten"]
    assert pdiff["mean"] == pytest.approx(expected, abs=0.1)
    assert pdiff["spread"] < 1.0


def test_saturated_steps_are_left_out_of_the_fit(full_run):
    """飽和したステップ（入力 −50 dBm 以上）を線形域から外すこと。"""
    _world, runner = full_run
    report = BA.analyze(runner.directory)
    for d in report["directions"].values():
        assert d["fit"]["dropped_dbm"], "飽和したステップが当てはめに残っている"
        assert all(x > -50 for x in d["fit"]["dropped_dbm"])


def test_the_receive_rate_crossing_lands_near_the_true_sensitivity(full_run):
    world, runner = full_run
    report = BA.analyze(runner.directory)
    # B が受ける向き（A→B）の 50% は、B の感度（−93）付近（公称の入力は A のずれ −0.8 を含まない）。
    half = report["directions"]["a->b"]["rate_crossings_dbm"]["0.5"]
    assert half == pytest.approx(world.b.sensitivity_dbm - world.a.tx_offset_db, abs=1.5)


def _ref(world: World, band: str = "920", power_dbm: int = 13, channel: int = 0) -> dict:
    ref = BA.ref_template()
    ref[band]["fixed_loss_db"] = FIXED_LOSS
    for dev in (world.a, world.b):
        ref[band]["tx_output"].append({
            "device_id": R.mac(tuple(dev.mac)), "power_cdbm": power_dbm * 100,
            "channel": channel, "dbm": power_dbm + dev.tx_offset_db,
            "measured_on": "2026-09-25", "instrument": "PM",
        })
    return ref


def test_with_the_meter_readings_the_rx_offsets_come_out_absolute(full_run, tmp_path):
    """🔑 ref（パワー計の P_A・P_B）を渡すと、**測り直さずに** R_A・R_B の絶対値が出ること。"""
    world, runner = full_run
    report = BA.analyze(runner.directory, _ref(world))
    for d in report["directions"].values():
        assert d["input"] == "absolute"
    cals = {c.device_id: c for c in BA.device_calibrations(report)}
    for dev in (world.a, world.b):
        cal = cals[R.mac(tuple(dev.mac))]
        assert set(cal.rx) == {"920"}
        rx = cal.rx["920"]
        # dBm = 生値 − rx_offset
        assert rx.offset_db == pytest.approx(-dev.rx_offset, abs=0.3)
        assert rx.reference_unit_id != cal.device_id        # 相手の TX が基準
        assert rx.reference_measured_on == "2026-09-25"
        assert rx.source_bench == runner.directory.name


def test_the_tx_output_table_is_derived_from_the_power_sweep(full_run):
    """パワー計の 1 点から、電力掃引の相対差で他の電力の出力を導くこと（出どころを分けて）。"""
    world, runner = full_run
    report = BA.analyze(runner.directory, _ref(world))
    cals = {c.device_id: c for c in BA.device_calibrations(report)}
    for dev in (world.a, world.b):
        rows = {(t.power_cdbm, t.channel): t for t in cals[R.mac(tuple(dev.mac))].tx_outputs}
        assert rows[(1300, 0)].source == "meter"
        for p in (100, 500, 900):
            assert rows[(p, 0)].source == "bench_power_sweep"
            assert rows[(p, 0)].dbm == pytest.approx(p / 100 + dev.tx_offset_db, abs=0.3)
        # チャネル 3・6 はパワー計で測っていない＝導かない（TX と RX のどちらの差か分けられない）。
        assert not any(ch != 0 for _p, ch in rows)


def test_the_closure_check_compares_a_signal_generator_point(full_run):
    world, runner = full_run
    ref = _ref(world)
    ref["920"]["rx_direct"] = [
        {"device_id": R.mac(tuple(world.b.mac)), "input_dbm": -70.0,
         "rssi_mean": -70.0 + world.b.rx_offset},
        {"device_id": R.mac(tuple(world.a.mac)), "input_dbm": -70.0,
         "rssi_mean": -70.0 + world.a.rx_offset + 3.0},          # 3 dB 食い違う
    ]
    report = BA.analyze(runner.directory, ref)
    by = {r["unit"]: r for r in report["closure"]}
    assert by["b"]["pass"] and not by["a"]["pass"]


def test_a_leak_through_the_terminators_is_reported(tmp_path, dialect):
    world = World(dialect, leak_loss_db=100.0)          # 終端しても 100 dB でしか落ちない
    runner = _runner(tmp_path, world)
    runner.run(("leak",))
    report = BA.analyze(runner.directory)
    assert report["leak"] and not any(r["pass"] for r in report["leak"])


def test_a_clean_bench_passes_the_leak_check(full_run):
    _world, runner = full_run
    report = BA.analyze(runner.directory)
    assert report["leak"] and all(r["pass"] for r in report["leak"])


def test_readings_without_dither_fail_the_dither_check(tmp_path, dialect):
    """雑音が無く生値が 1 値に張り付くと、平均しても分解能は上がらない（§7）＝落とすこと。"""
    world = World(dialect, noise=0.0)
    plan = _plan()
    plan["coarse"]["atten_db"] = [10, 20, 30, 40]
    runner = _runner(tmp_path, world, plan)
    runner.run(("coarse",))
    report = BA.analyze(runner.directory)
    assert not report["directions"]["a->b"]["dither"]["pass"]


def test_the_report_is_written_and_readable(full_run):
    _world, runner = full_run
    report = BA.analyze(runner.directory)
    j, m = BA.write_report(runner.directory, report)
    json.loads(j.read_text(encoding="utf-8"))
    text = m.read_text(encoding="utf-8")
    assert "漏れ" in text and "個体差" in text and "A->B" in text


def test_a_step_with_usb_losses_is_kept_out_of_the_rate_curve():
    """sample_seq の欠け（PC までの間で落ちた）は電波の欠けと混ぜないこと。"""
    rx = BA._UnitLog()
    tx = BA._UnitLog()
    t0 = S.parse_utc(S.format_utc(_BASE))
    for seq in range(10):
        tx.echoes.append((t0 + timedelta(seconds=0.1 * seq), {
            "seq": seq, "config_id": 3, "sent_mask": 0xFFFFFFFF, "unsent_total": 0}))
    for seq in range(10):
        if seq in (4, 5):
            continue                                        # USB で落ちた（番号は振られていた）
        rx.samples.append((t0 + timedelta(seconds=0.1 * seq), {
            "tx_id": (1, 2, 3, 4, 5, 6), "tx_config_id": 3, "config_id": 7, "seq": seq,
            "rssi_raw": -60, "noise_floor_raw": -95, "sample_seq": seq}))
    row = {"step": 1, "kind": "level", "label": "x", "tx_unit": "a", "rx_unit": "b",
           "tx_device": "01:02:03:04:05:06", "rx_device": "x", "tx_config_id": 3,
           "rx_config_id": 7, "power_cdbm": 1300, "channel": 0, "atten_db": 10,
           "start_utc": S.format_utc(_BASE), "end_utc": S.format_utc(_BASE + timedelta(seconds=1))}
    s = BA.step_stats(row, tx, rx)
    assert (s.sent, s.received, s.usb_lost) == (10, 8, 2)
    assert s.rate == 1.0


def _step_with_raw(raws: list[int]) -> BA.StepStats:
    rx = BA._UnitLog()
    tx = BA._UnitLog()
    t0 = S.parse_utc(S.format_utc(_BASE))
    for seq, raw in enumerate(raws):
        when = t0 + timedelta(seconds=0.1 * seq)
        tx.echoes.append((when, {
            "seq": seq, "config_id": 3, "sent_mask": 0xFFFFFFFF, "unsent_total": 0}))
        rx.samples.append((when, {
            "tx_id": (1, 2, 3, 4, 5, 6), "tx_config_id": 3, "config_id": 7, "seq": seq,
            "rssi_raw": raw, "noise_floor_raw": -95, "sample_seq": seq}))
    row = {"step": 1, "kind": "level", "label": "x", "tx_unit": "a", "rx_unit": "b",
           "tx_device": "01:02:03:04:05:06", "rx_device": "x", "tx_config_id": 3,
           "rx_config_id": 7, "power_cdbm": 1300, "channel": 0, "atten_db": 0,
           "start_utc": S.format_utc(_BASE), "end_utc": S.format_utc(_BASE + timedelta(seconds=1))}
    return BA.step_stats(row, tx, rx)


def test_a_saturated_sample_is_counted_as_received_but_gives_no_level():
    """B-361。E220 は強すぎる受信で生値が −256 に回り込む＝**届いた**（受信率は落とさない）が、
    そのステップの平均・σ は名乗らない（当てはめ・個体差・電力・チャネルの点から外れる）。"""
    s = _step_with_raw([-20, -21, S.RSSI_RAW_SATURATED, -20])
    assert (s.received, s.saturated) == (4, 1)
    assert s.rate == 1.0
    assert s.rssi == (-20, -21, -20)
    assert s.mean is None and s.std is None


def test_the_rate_crossing_interpolates_between_steps():
    points = [(-80.0, 1.0), (-90.0, 1.0), (-92.0, 0.8), (-94.0, 0.2), (-96.0, 0.0)]
    assert BA.rate_crossing(points, 0.5) == pytest.approx(-93.0)
    assert BA.rate_crossing(points, 0.9) == pytest.approx(-91.0)
    assert BA.rate_crossing([(-80.0, 1.0)], 0.5) is None
    # ちょうど threshold のステップは「そこで切った」とする（最初のステップでも見落とさない）。
    assert BA.rate_crossing([(-92.0, 0.5), (-94.0, 0.0)], 0.5) == pytest.approx(-92.0)


# --- 校正ファイル → record -------------------------------------------------------


def _calibrated(full_run, tmp_path) -> tuple[World, Path]:
    world, runner = full_run
    report = BA.analyze(runner.directory, _ref(world))
    calib_dir = tmp_path / "calib"
    BA.write_calibrations(report, calib_dir)
    return world, calib_dir


def _template(world: World, tx: Device, rx: Device) -> dict:
    template = REC.header_template()
    template.update(target_kind="existing_link", env_class="rural", meas_method="esp32c6-2g4")
    template["provenance"].update(
        geoid_model="GSIGEO2011", censor_min_receive_rate=0.9, censor_window_s=1.0)
    for role, dev in (("tx", tx), ("rx", rx)):
        template[role].update(measurement_config_id=f"{role}-rod", device_id=R.mac(tuple(dev.mac)),
                              feeder_loss_db=0.0, antenna_gain_dbi=2.0)
        template[role]["position"].update(lat=35.0, lon=139.0, elevation_m=10.0,
                                          height_agl_m=5.0, height_source="survey")
    template["rx"]["radio"] = {"sensitivity_dbm": -95.0}
    return template


def _config_message(dev: Device, **over) -> R.Message:
    values = dev.config_values()
    values.update(over)
    frame = dev.frame("RADIOSIM_FIELD_CONFIG", values)
    messages, _stats = R.read_log(frame, dev.world.dialect)
    return messages[0]


def test_the_calibration_file_round_trips(full_run, tmp_path):
    world, calib_dir = _calibrated(full_run, tmp_path)
    device = R.mac(tuple(world.a.mac))
    cal = C.read_device_calibration(calib_dir, device)
    assert cal.device_id == device and "920" in cal.rx and cal.tx_outputs
    assert C.calib_path(calib_dir, device).name == "aa-bb-cc-00-00-0a.json"


def test_an_analysis_that_makes_no_calibration_does_not_erase_the_files(full_run, tmp_path):
    """B-353。ref なし（公称）の判定で `--calib-dir` を渡しても、既存の校正を空で上書き
    しない＝何も書かずに止まる。"""
    _world, calib_dir = _calibrated(full_run, tmp_path)
    _world, runner = full_run
    before = {p.name: p.read_bytes() for p in calib_dir.glob("*.json")}
    with pytest.raises(S.SessionError, match="1 台も作れませんでした"):
        BA.write_calibrations(BA.analyze(runner.directory), calib_dir)
    assert {p.name: p.read_bytes() for p in calib_dir.glob("*.json")} == before


def test_a_part_that_was_not_made_keeps_the_previous_values(full_run, tmp_path):
    """B-353。片方の向きだけ実測なら、相手の機器は RX の曲線が作れない＝その帯域の前の
    曲線を残し、作れた TX の出力表だけを差し替える。空の校正は書かない。"""
    world, calib_dir = _calibrated(full_run, tmp_path)
    device = R.mac(tuple(world.a.mac))
    old = C.read_device_calibration(calib_dir, device)
    tx_only = dataclasses.replace(old, rx={}, tx_outputs=old.tx_outputs[:1])
    C.write_device_calibration(calib_dir, tx_only, "920")
    new = C.read_device_calibration(calib_dir, device)
    assert new.rx == old.rx and new.tx_outputs == old.tx_outputs[:1]
    with pytest.raises(S.SessionError, match="空です"):
        C.write_device_calibration(calib_dir, dataclasses.replace(old, rx={}, tx_outputs=()), "920")
    assert C.read_device_calibration(calib_dir, device) == new


def test_record_copies_the_calibration_by_unit_id(full_run, tmp_path):
    """🔑 校正ファイルを個体 ID で読み、RX は受信の校正を・TX は**動いている設定の行**の
    出力を写すこと。役割を入れ替えた組でも同じファイルが使えること。"""
    world, calib_dir = _calibrated(full_run, tmp_path)
    for tx, rx in ((world.a, world.b), (world.b, world.a)):
        cals = {R.mac(tuple(d.mac)): C.read_device_calibration(calib_dir, R.mac(tuple(d.mac)))
                for d in (tx, rx)}
        tx.role, rx.role = "tx", "rx"
        tx.channel = rx.channel = 0                        # 計画のチャネル（掃引の後は 6 のまま）
        tx.power_dbm = 9                                   # 9 dBm（電力掃引から導いた行）
        header = REC.build_header(
            _template(world, tx, rx), _config_message(rx), _config_message(tx),
            started_utc=S.format_utc(_BASE), software_commit="abc", dialect=world.dialect,
            calibrations=cals,
        )
        assert header.tx.calibration.tx_output_dbm == pytest.approx(9 + tx.tx_offset_db, abs=0.3)
        assert "bench_power_sweep" in header.tx.calibration.note
        assert header.rx.calibration.offset_db == pytest.approx(-rx.rx_offset, abs=0.3)
        assert header.rx.calibration.tx_output_dbm is None


def test_record_refuses_a_setting_that_the_file_does_not_cover(full_run, tmp_path):
    world, calib_dir = _calibrated(full_run, tmp_path)
    cals = {R.mac(tuple(d.mac)): C.read_device_calibration(calib_dir, R.mac(tuple(d.mac)))
            for d in (world.a, world.b)}
    world.a.role, world.b.role = "tx", "rx"
    with pytest.raises(S.SessionError, match="チャネル 6 の出力がありません"):
        REC.build_header(
            _template(world, world.a, world.b), _config_message(world.b, channel=6),
            _config_message(world.a, channel=6, tx_power_cdbm=1300),
            started_utc=S.format_utc(_BASE), software_commit="abc", dialect=world.dialect,
            calibrations=cals,
        )


def test_the_calibration_has_exactly_one_source(full_run, tmp_path):
    world, calib_dir = _calibrated(full_run, tmp_path)
    template = _template(world, world.a, world.b)
    with pytest.raises(S.SessionError, match="--calib"):
        REC.check_template(template)
    cals = {R.mac(tuple(d.mac)): C.read_device_calibration(calib_dir, R.mac(tuple(d.mac)))
            for d in (world.a, world.b)}
    template["rx"]["calibration"] = {"measured_on": "2026-09-19", "offset_db": 0.0,
                                     "scale_db_per_count": 1.0, "reference_unit_id": "x",
                                     "reference_measured_on": "2026-09-19", "note": ""}
    with pytest.raises(S.SessionError, match="両方"):
        REC.check_template(template, cals)


def test_record_reads_the_calibration_folder_from_the_cli(full_run, tmp_path):
    world, calib_dir = _calibrated(full_run, tmp_path)
    path = tmp_path / "t.json"
    path.write_text(json.dumps(_template(world, world.a, world.b)), encoding="utf-8")
    template, cals = cli._load_template(path, calib_dir)
    assert cals is not None and set(cals) == {R.mac(tuple(world.a.mac)), R.mac(tuple(world.b.mac))}
    with pytest.raises(S.SessionError, match="校正ファイルがありません"):
        cli._load_template(path, tmp_path / "empty")


def test_the_bench_analyze_command_writes_the_files(full_run, tmp_path, capsys):
    world, runner = full_run
    ref = tmp_path / "ref.json"
    ref.write_text(json.dumps(_ref(world)), encoding="utf-8")
    calib_dir = tmp_path / "cal"
    assert cli.main(["bench", "analyze", str(runner.directory), "--ref", str(ref),
                     "--calib-dir", str(calib_dir)]) == 0
    assert len(list(calib_dir.glob("*.json"))) == 2
    assert (runner.directory / BA.REPORT_MD).exists()


def test_the_plan_and_ref_templates_are_written_once(tmp_path):
    plan = tmp_path / "plan.json"
    assert cli.main(["bench", "plan", str(plan)]) == 0
    assert json.loads(plan.read_text(encoding="utf-8")) == B.default_plan()
    assert cli.main(["bench", "plan", str(plan)]) == 2
    plan24 = tmp_path / "plan24.json"
    assert cli.main(["bench", "plan", str(plan24), "--band", "2400"]) == 0
    assert json.loads(plan24.read_text(encoding="utf-8")) == B.default_plan("2400")
    ref = tmp_path / "ref.json"
    assert cli.main(["bench", "ref", str(ref)]) == 0
    assert set(BA.read_ref(ref)) == set(S.BANDS)


def test_bench_run_refuses_a_plan_of_another_band(tmp_path, capsys):
    """計画の帯域と --band が食い違えば、ポートを開く前に止まること。"""
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(B.default_plan("2400")), encoding="utf-8")
    assert cli.main(["bench", "run", "--port-a", "X", "--port-b", "Y", "--root", str(tmp_path),
                     "--plan", str(plan), "--band", "920"]) == 2
    assert "--band" in capsys.readouterr().err


def test_an_old_flat_ref_is_refused(tmp_path):
    """帯域で分けていない ref（2.4 GHz を戻す前の形）を黙って公称で読まないこと。"""
    path = tmp_path / "ref.json"
    path.write_text(json.dumps({"fixed_loss_db": 58.0, "tx_output": []}), encoding="utf-8")
    with pytest.raises(S.SessionError, match="帯域"):
        BA.read_ref(path)


@pytest.mark.parametrize("text, message", [
    ("{", "JSON"),
    ('{"2400": []}', "形"),
    ('{"2400": {"tx_output": {}}}', "形"),
    ('{"920": {"rx_direct": 5}}', "形"),
])
def test_cont_refuses_an_unusable_ref_before_measuring(tmp_path, monkeypatch, capsys, text, message):
    """使えない ref は、ポートを開く（＝連続送信で測り、読みを打ち込む）前に止めること（B-356）。"""
    path = tmp_path / "ref.json"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(cli, "_import_serial", lambda: pytest.fail("測る前に止まるはず"))
    assert cli.main(["cont", "--port", "COM9", "--ref", str(path)]) == 2
    assert message in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == text


def test_cont_refuses_a_ref_in_a_missing_folder_before_measuring(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_import_serial", lambda: pytest.fail("測る前に止まるはず"))
    assert cli.main(["cont", "--port", "COM9", "--ref", str(tmp_path / "無い" / "ref.json")]) == 2
    assert "フォルダ" in capsys.readouterr().err


class _FakeKernel32:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def SetThreadExecutionState(self, flags: int) -> int:  # noqa: N802 — Win32 の名前
        self.calls.append(flags)
        return 0x80000000


@pytest.mark.parametrize("argv", [
    ["bench", "run", "--port-a", "COM3", "--port-b", "COM4"],
    ["probe", "--port-a", "COM3", "--port-b", "COM4"],
    ["cont", "--port", "COM4"],
])
def test_measuring_keeps_windows_awake_from_opening_the_ports_and_lets_go_on_error(
        tmp_path, monkeypatch, capsys, argv):
    """bench・probe・cont も gnss log と同じく、機器とやり取りするあいだスリープさせない
    （X13 は電池だと休止に入り、測定が途中で止まる）。落ちても元へ戻すこと。"""
    k = _FakeKernel32()
    keep_awake = cli._KeepAwake
    monkeypatch.setattr(cli, "_KeepAwake", lambda: keep_awake(k))

    def open_ports():
        assert k.calls == [0x80000003]      # ポートを開く時点で既に起こしてある
        raise S.SessionError("ポートを開けません")

    monkeypatch.setattr(cli, "_import_serial", open_ports)
    extra = (["--ref", str(tmp_path / "ref.json")] if argv[0] == "cont"
             else ["--root", str(tmp_path)])
    assert cli.main(argv + extra) == 2
    assert k.calls == [0x80000003, 0x80000000]   # 元へ戻した
    assert "スリープせず画面も消しません" in capsys.readouterr().out


def test_adding_a_tx_output_keeps_the_rows_already_there(tmp_path):
    """`cont` の 1 行は末尾に足すだけ＝既存の行・ほかの帯域は残る。null の節は空として読む。"""
    ref = BA.ref_template()
    ref["2400"]["tx_output"].append({"device_id": "aa", "dbm": 12.0})
    ref["920"] = None
    path = tmp_path / "ref.json"
    BA.write_ref(path, ref)
    ref = BA.read_ref(path)
    BA.add_tx_output(ref, "2400", {"device_id": "bb", "dbm": 13.0})
    BA.add_tx_output(ref, "920", {"device_id": "cc", "dbm": 11.0})
    BA.write_ref(path, ref)
    got = BA.read_ref(path)
    assert [t["device_id"] for t in got["2400"]["tx_output"]] == ["aa", "bb"]
    assert got["2400"]["fixed_loss_db"] is None
    assert got["920"] == {"tx_output": [{"device_id": "cc", "dbm": 11.0}]}


def test_cont_rereads_the_ref_just_before_adding_its_row(tmp_path):
    """測っている間に手で直した値・別の行は、`cont` の 1 行を足しても残ること（B-357）。"""
    path = tmp_path / "ref.json"
    BA.write_ref(path, BA.ref_template())
    edited = BA.read_ref(path)                      # 測っている間に手で直した
    edited["920"]["fixed_loss_db"] = 77.0
    BA.add_tx_output(edited, "2400", {"device_id": "aa", "dbm": 12.0})
    BA.write_ref(path, edited)
    cli._add_to_ref(path, {"device_id": "bb", "dbm": 13.0})
    got = BA.read_ref(path)
    assert got["920"]["fixed_loss_db"] == 77.0
    assert [t["device_id"] for t in got["2400"]["tx_output"]] == ["aa", "bb"]


def test_a_ref_broken_while_measuring_keeps_the_measured_value(tmp_path):
    """測っている間に ref が壊れたら、書かずに止まり、測った値をエラーに載せること（B-357）。"""
    path = tmp_path / "ref.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(S.SessionError, match='"dbm": 13.0'):
        cli._add_to_ref(path, {"device_id": "bb", "dbm": 13.0})
    assert path.read_text(encoding="utf-8") == "{"


def _utf16_ref(path):
    path.write_text(json.dumps(BA.ref_template()), encoding="utf-16")


def _folder_ref(path):
    path.mkdir()


@pytest.mark.parametrize("make", [_utf16_ref, _folder_ref], ids=["utf-16", "folder"])
def test_an_unreadable_ref_is_a_session_error(tmp_path, make):
    """文字コード・I/O で読めない ref も SessionError にする＝測る前の検査は素のトレースバックで
    落ちず、書く直前の読み直しでは測った値をエラーに載せられる（B-358）。"""
    path = tmp_path / "ref.json"
    make(path)
    with pytest.raises(S.SessionError, match="ref を読めません"):
        BA.read_ref(path)
    with pytest.raises(S.SessionError, match='"dbm": 13.0'):
        cli._add_to_ref(path, {"device_id": "bb", "dbm": 13.0})


def test_a_failed_ref_write_keeps_the_measured_value(tmp_path, monkeypatch):
    """書き込みに落ちても、測った値をエラーに載せること（B-358）。"""
    path = tmp_path / "ref.json"
    BA.write_ref(path, BA.ref_template())

    def denied(*_a):
        raise PermissionError("書けない")

    monkeypatch.setattr(cli, "write_ref", denied)
    with pytest.raises(S.SessionError, match='"dbm": 13.0'):
        cli._add_to_ref(path, {"device_id": "bb", "dbm": 13.0})


def test_a_failed_ref_write_leaves_the_old_file(tmp_path, monkeypatch):
    """書き切ってから置き換える＝途中で落ちても前の ref が残り、一時ファイルも残らない（B-356）。"""
    path = tmp_path / "ref.json"
    BA.write_ref(path, BA.ref_template())
    old = path.read_text(encoding="utf-8")

    def boom(*_a):
        raise OSError("置き換えの前に落ちた")

    monkeypatch.setattr(BA.os, "replace", boom)
    ref = BA.read_ref(path)
    BA.add_tx_output(ref, "2400", {"device_id": "aa", "dbm": 12.0})
    with pytest.raises(OSError):
        BA.write_ref(path, ref)
    assert path.read_text(encoding="utf-8") == old
    assert [p.name for p in tmp_path.iterdir()] == ["ref.json"]


@pytest.mark.parametrize("band", S.BANDS)
def test_the_default_power_sweep_includes_the_metered_power(band):
    """他の電力の出力は、パワー計で測った電力（計画の power_dbm）からの相対で導く＝
    掃引に含まれていないと 1 行も導けない。"""
    plan = B.default_plan(band)
    assert plan["power_dbm"] in plan["power"]["dbm"]


# --- 帯域（I-243） ----------------------------------------------------------------


def test_a_2400_bench_writes_its_band_and_keeps_the_920_calibration(full_run, tmp_path, dialect):
    """🔑 2.4 GHz の机上試験は、機器を帯域へ切り替えてから測り、その帯域の ref だけで判定し、
    校正ファイルの **2.4 GHz の分だけ**を書く（920 MHz の分は残る）。record は動いている
    帯域の分を引く。"""
    world, calib_dir = _calibrated(full_run, tmp_path)
    world24 = World(dialect)
    plan = _plan(band="2400", channel=1, power_dbm=15)
    plan["power"]["dbm"] = [5, 9, 13, 15]
    runner = _runner(tmp_path / "b24", world24, plan)
    runner.run(("coarse", "fine", "power"))
    assert ("band 2400" in [line for _m, line in world24.commands])
    assert world24.a.band == world24.b.band == "2400"
    ref = _ref(world24, "2400", power_dbm=15, channel=1)
    ref["920"]["fixed_loss_db"] = 99.0          # 別の帯域の値は判定に効かない
    report = BA.analyze(runner.directory, ref)
    assert report["band"] == "2400" and report["levels"]["fixed_loss_db"] == FIXED_LOSS
    assert all(d["input"] == "absolute" for d in report["directions"].values())
    assert "2400 MHz 帯" in BA.render_markdown(report)
    BA.write_calibrations(report, calib_dir)
    for dev in (world24.a, world24.b):
        cal = C.read_device_calibration(calib_dir, R.mac(tuple(dev.mac)))
        assert set(cal.rx) == {"920", "2400"}
        assert {t.band for t in cal.tx_outputs} == {"920", "2400"}
        assert cal.rx["2400"].source_bench == runner.directory.name
    tx, rx = world24.a, world24.b
    tx.role, rx.role = "tx", "rx"
    cals = {R.mac(tuple(d.mac)): C.read_device_calibration(calib_dir, R.mac(tuple(d.mac)))
            for d in (tx, rx)}
    tx.channel = rx.channel = 1
    tx.power_dbm = 15
    header = REC.build_header(
        _template(world24, tx, rx), _config_message(rx), _config_message(tx),
        started_utc=S.format_utc(_BASE), software_commit="abc", dialect=world24.dialect,
        calibrations=cals,
    )
    assert header.tx.calibration.tx_output_dbm == pytest.approx(15 + tx.tx_offset_db, abs=0.01)
    assert "2400 MHz 帯" in header.rx.calibration.note
    assert header.rx.calibration.offset_db == pytest.approx(-rx.rx_offset, abs=0.3)


def test_a_format_1_calibration_is_read_as_920(tmp_path):
    """帯域の欄が無い形式 1 の校正ファイルは 920 MHz として読むこと。"""
    device = "aa:bb:cc:00:00:0a"
    C.calib_path(tmp_path, device).write_text(json.dumps({
        "format": 1, "device_id": device, "measured_on": "2026-09-25",
        "source_bench": "bench-1", "software_commit": "abc",
        "rx": {"offset_db": -3.0, "scale_db_per_count": 1.0, "reference_unit_id": "x",
               "reference_measured_on": "2026-09-25", "linear_range_dbm": [-90, -50],
               "fit_max_residual_db": 0.2, "note": ""},
        "tx_outputs": [{"power_cdbm": 1300, "channel": 0, "dbm": 12.2, "source": "meter",
                        "measured_on": "2026-09-25", "note": ""}],
    }), encoding="utf-8")
    cal = C.read_device_calibration(tmp_path, device)
    assert set(cal.rx) == {"920"} and cal.rx["920"].source_bench == "bench-1"
    assert [t.band for t in cal.tx_outputs] == ["920"]
    out = C.endpoint_calibration(cal, "tx", band="920", power_cdbm=1300, channel=0)
    assert out.tx_output_dbm == 12.2
    with pytest.raises(S.SessionError, match="2400 MHz 帯の RX の校正がありません"):
        C.endpoint_calibration(cal, "rx", band="2400")


# --- 動作確認（probe） ------------------------------------------------------------

_HEAD = "afd69ae7db12"          # 模擬のファームの版（afd69ae7db）で始まるコミット


def _probe_runner(tmp_path: Path, world: World) -> B.BenchRunner:
    # 問いかけは CLI と同じもの（呼ばれたら止まる）＝人に頼まないことをここで縛る。
    return B.BenchRunner(
        tmp_path, {"a": world.a, "b": world.b}, {"probe": {}},
        clock=world.clock, prompt=cli._no_prompt, say=lambda _m: None,
        software_commit=_HEAD, dialect=world.dialect, purpose="probe",
    )


def _run_probe(runner: B.BenchRunner, said: list[str], **over) -> int:
    kwargs = dict(seconds=3, power_dbm=13, channel=0, software_commit=_HEAD,
                  same_source=lambda _v: pytest.fail("コミットが一致するなら git を見ない"),
                  say=said.append)
    kwargs.update(over)
    return cli.run_probe(runner, **kwargs)


def _probe_record(runner: B.BenchRunner) -> dict:
    return json.loads((runner.directory / B.PROBE_FILE).read_text(encoding="utf-8"))


def test_the_probe_passes_a_working_pair_without_asking_anyone(tmp_path, dialect):
    """🔑 動作確認は人に何も頼まずに両方向を測る＝対話の無いシェルからも実行できること。"""
    world = World(dialect)
    runner = _probe_runner(tmp_path, world)
    said: list[str] = []
    assert _run_probe(runner, said) == 0
    assert runner.directory.name.startswith("probe-")
    record = _probe_record(runner)
    assert record["passed"] is True
    assert [(x["tx_unit"], x["rx_unit"]) for x in record["links"]] == [("a", "b"), ("b", "a")]
    assert all(x["received"] > 0 and x["usb_lost"] == 0 for x in record["links"])
    assert record["units"] == {"a": "aa:bb:cc:00:00:0a", "b": "aa:bb:cc:00:00:0b"}
    assert B.read_bench(runner.directory)["purpose"] == "probe"
    assert "すべて合格です。" in said


def test_the_probe_fails_when_the_signal_does_not_get_through(tmp_path, dialect):
    world = World(dialect)
    world.atten = 50                     # 感度より下＝どちらの向きもほぼ受からない
    runner = _probe_runner(tmp_path, world)
    said: list[str] = []
    assert _run_probe(runner, said) == 1
    assert not any(x["ok"] for x in _probe_record(runner)["links"])
    assert any("アッテネータを 0 dB に" in line for line in said)


def test_the_probe_catches_samples_lost_on_the_usb(tmp_path, dialect):
    """USB で落ちたサンプル（番号の飛び）は、電波の欠けと分けて数えて不合格にすること。"""
    world = World(dialect)
    frame, dropped = world.b.frame, [0]

    def lossy(name: str, values: dict) -> bytes:
        data = frame(name, values)
        if name == "RADIOSIM_FIELD_RX_SAMPLE":
            dropped[0] += 1
            if dropped[0] % 5 == 0:
                return b""               # 番号は振られたが USB に届かない
        return data

    world.b.frame = lossy                # type: ignore[method-assign]
    runner = _probe_runner(tmp_path, world)
    said: list[str] = []
    assert _run_probe(runner, said) == 1
    a_to_b, b_to_a = _probe_record(runner)["links"]
    assert a_to_b["usb_lost"] > 0 and not a_to_b["ok"]
    # 電波では届いている＝アッテネータや配線を疑わせない。
    assert a_to_b["air_ok"] and a_to_b["rate"] > 0.95
    assert any("USB ケーブル" in line for line in said)
    assert not any("アッテネータ" in line for line in said)
    assert b_to_a["usb_lost"] == 0 and b_to_a["ok"]


def _probe_again(tmp_path: Path, world: World, atten: int, **over) -> tuple[int, dict, list]:
    world.atten = atten
    runner = _probe_runner(tmp_path, world)
    said: list[str] = []
    code = _run_probe(runner, said, **over)
    return code, _probe_record(runner), said


def test_the_probe_fails_when_the_level_drops_from_the_last_pass(tmp_path, dialect):
    """🔑 受信レベルが同じ 2 台・同じ設定の前回の合格より 6 dB を超えて下がったら不合格にする
    （I-245＝浮いた接続は両方向とも同じだけ下げるので、2 方向を比べても見つからない）。"""
    world = World(dialect)                # atten 0 は模擬の受信が飽和する＝10 dB から始める
    code, first, said = _probe_again(tmp_path, world, 10)
    assert code == 0 and first["level_check"]["baseline"] is None
    assert any("前回の記録がありません" in line for line in said)
    code, record, said = _probe_again(tmp_path, world, 12)         # 置き直しの揺れ＝通す
    assert code == 0
    assert record["level_check"]["baseline"].startswith("probe-")
    assert all(-4 <= x["change_db"] <= 0 and x["ok"] for x in record["level_check"]["links"])
    # 比べる相手はいちばん新しい合格（12 dB の回）＝そこから 10 dB 下げる。
    code, record, said = _probe_again(tmp_path, world, 22)         # 浮いた接続＝落とす
    assert code == 1 and record["passed"] is False
    assert not any(x["ok"] for x in record["level_check"]["links"])
    assert all(x["ok"] for x in record["links"])                    # 受信率は落ちていない
    assert any("--new-baseline" in line for line in said)
    assert not any("アッテネータを 0 dB に" in line for line in said)


def test_a_new_baseline_skips_the_comparison_and_becomes_the_next_one(tmp_path, dialect):
    world = World(dialect)
    _probe_again(tmp_path, world, 10)
    code, record, said = _probe_again(tmp_path, world, 20, new_baseline=True)
    assert code == 0
    assert record["level_check"] == dict(baseline=None, new_baseline=True, saturated=False,
                                         max_drop_db=B.PROBE_MAX_DROP_DB, links=[])
    assert any("--new-baseline" in line for line in said)
    code, record, _ = _probe_again(tmp_path, world, 20)
    assert code == 0                      # 比べる相手は 10 dB の回ではなく今の基準
    assert all(abs(x["change_db"]) < 2 for x in record["level_check"]["links"])


def test_the_level_is_compared_by_unit_even_when_the_ports_are_swapped(tmp_path, dialect):
    """A と B のポートを入れ替えても同じ組＝個体 ID で向きを揃えて比べること。"""
    world = World(dialect)
    world.b.rx_offset -= 8                # 向きで 10 dB ほど違う＝取り違えると片方が落ちる
    _probe_again(tmp_path, world, 10)
    world.atten = 10
    runner = B.BenchRunner(
        tmp_path, {"a": world.b, "b": world.a}, {"probe": {}},
        clock=world.clock, prompt=cli._no_prompt, say=lambda _m: None,
        software_commit=_HEAD, dialect=world.dialect, purpose="probe",
    )
    assert _run_probe(runner, []) == 0
    record = _probe_record(runner)
    assert record["level_check"]["baseline"].startswith("probe-")
    assert all(abs(x["change_db"]) < 2 for x in record["level_check"]["links"])


@pytest.mark.parametrize("over", [dict(power_dbm=10), dict(channel=1)])
def test_a_pass_with_other_settings_is_not_a_baseline(tmp_path, dialect, over):
    world = World(dialect)
    _probe_again(tmp_path, world, 10)
    code, record, _ = _probe_again(tmp_path, world, 20, **over)
    assert code == 0 and record["level_check"]["baseline"] is None


def test_a_failed_probe_is_not_a_baseline(tmp_path, dialect):
    world = World(dialect)
    assert _probe_again(tmp_path, world, 50)[0] == 1
    code, record, _ = _probe_again(tmp_path, world, 10)
    assert code == 0 and record["level_check"]["baseline"] is None


def _saturating(world: World, every: int = 4):
    """B の受けるサンプルの `every` 件に 1 件を生値 −256（E220 の飽和の回り込み）にする
    `frame` の差し替え。"""
    frame, n = world.b.frame, [0]

    def wrapped(name: str, values: dict) -> bytes:
        if name == "RADIOSIM_FIELD_RX_SAMPLE":
            n[0] += 1
            if n[0] % every == 0:
                values = dict(values, rssi_raw=S.RSSI_RAW_SATURATED)
        return frame(name, values)

    return wrapped


def test_a_saturated_probe_neither_compares_nor_becomes_a_baseline(tmp_path, dialect):
    """B-361。生値 −256 は「受信が強すぎて測れない」＝最小・中央値に入れず、前回とも比べず、
    次からの基準にもしない。合否は変えない（⚠️ の行で知らせる）。"""
    world = World(dialect)
    first = _probe_runner(tmp_path, world)
    world.atten = 10
    assert _run_probe(first, []) == 0
    clean = world.b.frame
    world.b.frame = _saturating(world)   # type: ignore[method-assign]
    runner = _probe_runner(tmp_path, world)
    said: list[str] = []
    assert _run_probe(runner, said) == 0
    record = _probe_record(runner)
    a_to_b = record["links"][0]
    assert a_to_b["saturated"] > 0 and a_to_b["ok"]
    assert a_to_b["rssi_min"] != S.RSSI_RAW_SATURATED
    assert a_to_b["rssi_median"] > -100
    assert record["level_check"]["saturated"] is True and record["level_check"]["links"] == []
    assert any("受信が強すぎます" in line for line in said)
    assert any("A→B" in line and "飽和" in line for line in said)
    # その場の数え（measure）も平均を名乗らない。
    _row, count = runner.results["probe"][0]
    assert count.saturated > 0 and count.rssi_mean is None
    world.b.frame = clean                # type: ignore[method-assign]
    again = _probe_runner(tmp_path, world)
    assert _run_probe(again, []) == 0
    assert _probe_record(again)["level_check"]["baseline"] == first.directory.name


def test_an_old_probe_record_that_passed_on_the_wrapped_value_is_not_a_baseline(tmp_path):
    """2026-10-19 の回は −256 を最小に載せたまま合格していた＝飽和の欄が無い古い記録でも外す。"""
    units = {"a": "aa:bb:cc:00:00:0a", "b": "aa:bb:cc:00:00:0b"}
    settings = {"band": "920", "power_dbm": 13, "channel": 0}
    folder = tmp_path / "probe-20261019T000000Z"
    folder.mkdir()
    (folder / B.PROBE_FILE).write_text(json.dumps({
        "passed": True, "units": units, "settings": settings,
        "links": [{"tx_unit": "a", "rx_unit": "b", "rssi_min": S.RSSI_RAW_SATURATED,
                   "rssi_max": -20, "rssi_median": -21}],
    }), encoding="utf-8")
    assert B.find_probe_baseline(tmp_path, units=units, settings=settings) is None


def test_usb_losses_are_counted_in_arrival_order():
    """番号が戻ったところ（RX の再起動）は欠けに数えない＝並べ替えると混ざる。"""
    seqs = [5, 6, 8, 0, 1, 2]            # 並べ替えると 2→5 の飛びが現れて 3 になる
    assert B.usb_lost([{"sample_seq": n} for n in seqs]) == 1


def test_the_firmware_version_is_judged_against_the_repository():
    never = lambda _v: pytest.fail("git を見るまでもない")   # noqa: E731
    assert B.judge_firmware("afd69ae7db", "afd69ae7db12", never)[0] is True
    assert B.judge_firmware("afd69ae7db-dirty", "afd69ae7db12", never)[0] is False
    assert B.judge_firmware("afd69ae7db", "0123456789ab-dirty", never)[0] is False
    ok, note = B.judge_firmware("afd69ae7db", "0123456789ab", lambda _v: True)
    assert ok and "同じ" in note                 # コミットだけ違う＝焼き直さなくてよい
    ok, note = B.judge_firmware("afd69ae7db", "0123456789ab", lambda _v: False)
    assert not ok and "焼き直して" in note
    ok, note = B.judge_firmware("afd69ae7db", "0123456789ab", lambda _v: None)
    assert not ok and "push" in note


def test_only_a_commit_shaped_version_is_handed_to_git():
    """ファームの版は機器から届く文字列＝git の引数にするのは 16 進の形だけ。"""
    assert cli._same_firmware_source("--output=x") is None
    assert cli._same_firmware_source("afd69ae7db-dirty") is None


def test_a_firmware_readme_change_does_not_ask_for_reflashing(tmp_path, monkeypatch):
    """ファームのフォルダの文書だけが変わっても、ソースは同じとみなすこと（B-360）。"""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              cwd=tmp_path, check=True, capture_output=True,
                              text=True).stdout.strip()

    firmware = tmp_path / "apps" / "field" / "firmware"
    (firmware / "main").mkdir(parents=True)
    (firmware / "main" / "main.c").write_text("int x;\n", encoding="utf-8")
    (firmware / "README.md").write_text("a\n", encoding="utf-8")
    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "1")
    flashed = git("rev-parse", "--short=10", "HEAD")
    (firmware / "README.md").write_text("b\n", encoding="utf-8")
    git("commit", "-qam", "2")
    monkeypatch.setattr(cli, "_REPO_ROOT", tmp_path)
    assert cli._same_firmware_source(flashed) is True
    (firmware / "main" / "main.c").write_text("int y;\n", encoding="utf-8")
    git("commit", "-qam", "3")
    assert cli._same_firmware_source(flashed) is False


def test_the_firmware_version_is_shown_by_the_last_firmware_change(tmp_path, monkeypatch):
    """人に見せる版は、ファームのソースを最後に変えたコミットの日付と件名＝ビルドした時の
    HEAD（台座や文書のコミット）の件名を名乗らないこと。"""
    def git(*args: str, date: str = "2026-10-01T12:00:00+09:00") -> str:
        env = dict(os.environ, GIT_COMMITTER_DATE=date, GIT_AUTHOR_DATE=date)
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                               "-c", "commit.gpgsign=false", *args],
                              cwd=tmp_path, check=True, capture_output=True,
                              text=True, encoding="utf-8", env=env).stdout.strip()

    firmware = tmp_path / "apps" / "field" / "firmware"
    (firmware / "main").mkdir(parents=True)
    (firmware / "main" / "main.c").write_text("int x;\n", encoding="utf-8")
    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "feat(field): 表示の LED をファームで点け消しする（送受の 1 回ごとに 30 ms）",
        date="2026-10-20T12:00:00+09:00")
    (firmware / "README.md").write_text("a\n", encoding="utf-8")
    (tmp_path / "mount.py").write_text("x = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "fix(field): 台座の板を直す", date="2026-10-22T12:00:00+09:00")
    flashed = git("rev-parse", "--short=10", "HEAD")
    monkeypatch.setattr(cli, "_REPO_ROOT", tmp_path)
    assert cli.describe_firmware(flashed) == (
        f"2026-10-20 版「表示の LED をファームで点け消しする（送受の 1 回ご…」（{flashed}）")
    assert cli.describe_firmware(flashed + "-dirty").endswith(f"（{flashed}-dirty）")
    # 引けない版はそのまま＝push されていない・機器から届いた妙な文字列。
    assert cli.describe_firmware("0123456789") == "0123456789"
    assert cli.describe_firmware("--output=x") == "--output=x"


def test_the_bench_report_shows_the_readable_firmware_version_when_given(full_run):
    """報告は CLI が足した人の読める版を出し、無ければコミットのまま出すこと。"""
    _world, runner = full_run
    report = BA.analyze(runner.directory)
    assert "ファーム `afd69ae7db`" in BA.render_markdown(report)
    report["firmware_label"] = "2026-10-20 版「表示の LED」（afd69ae7db）"
    assert "ファーム 2026-10-20 版「表示の LED」（afd69ae7db）" in BA.render_markdown(report)


def test_a_probe_folder_is_not_analyzed_as_a_bench(tmp_path, dialect):
    """動作確認は減衰量を記録しない＝机上試験の判定に混ぜないこと。"""
    world = World(dialect)
    runner = _probe_runner(tmp_path, world)
    _run_probe(runner, [])
    with pytest.raises(B.BenchError, match="机上試験のフォルダではありません"):
        BA.analyze(runner.directory)



# --- 文字の行 --------------------------------------------------------------------


def test_text_lines_are_picked_out_between_frames_in_any_split(dialect):
    """返答・報告の行はフレームの間に流れる＝どう区切って読んでも同じ行・同じフレーム。"""
    dev = World(dialect).a
    data = (b"OK config_id=3 \xe5\x86\x8d\xe8\xb5\xb7\xe5\x8b\x95\r\n"
            + dev.frame("RADIOSIM_FIELD_CONFIG", dev.config_values())
            + "ERR E220 の設定に失敗しました\r\n".encode("utf-8")
            + dev.frame("RADIOSIM_FIELD_CONFIG", dev.config_values()))
    rng = random.Random(3)
    for _ in range(20):
        stream = R.FrameStream(dialect=dialect)
        cuts = sorted(rng.sample(range(1, len(data)), 6))
        messages, lines = [], []
        for a, b in zip([0] + cuts, cuts + [len(data)]):
            messages += stream.feed(data[a:b])
            lines += stream.take_lines()
        assert len(messages) == 2
        assert lines[0].startswith("OK config_id=3") and lines[1].startswith("ERR E220")
        assert stream.stats.crc_errors == 0
