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

import json
import math
import random
import re
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
FIXED_LOSS = 60.0
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
        self.channel = 1
        self.power_qdbm = 60
        self.config_id = 1
        self.seq = 0
        self.sample_seq: dict[bytes, int] = {}
        self.buffer = bytearray()
        self.pending = b""
        self.paused_until = 0.0
        self.next_config = 0.0
        self.next_packet = 0.0
        self.next_air_config = 0.0
        self.cont_until: float | None = None

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
            rate_kbps=1000, tx_power_cdbm=self.power_qdbm * 25, tx_interval_ms=100,
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
        if parts[0] == "cont":
            seconds = int(parts[1])
            self.config_id += 1
            self.text(f"OK cont {seconds} 秒 config_id={self.config_id} 終わったら再起動します")
            self.cont_until = self.world.t + seconds
            return
        key, value = parts
        before = (self.role, self.channel, self.power_qdbm)
        if key == "role":
            self.role = value
        elif key == "channel":
            self.channel = int(value)
        elif key == "power":
            self.power_qdbm = round(float(value) * 4)
        if (self.role, self.channel, self.power_qdbm) == before:
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
        return self.power_qdbm / 4 + self.tx_offset_db

    def tick(self, other: "Device") -> None:
        t = self.world.t
        if t < self.paused_until:
            return
        if self.cont_until is not None:
            if t < self.cont_until:
                if t >= self.next_config:
                    self.text(f"CONT duty=0.97000 intervals={int(t * 80)} airtime_us=12224 "
                              "span_us=1000000 other_rate=0 failures=0 lost_notices=0 "
                              "remaining_s=1")
                    self.next_config = t + 1.0
                return
            self.text("CONT END")
            self.cont_until = None
            self.restart()
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
                tx_id=tuple(self.mac), seq=self.seq, tx_time_us=int(t * 1e6),
                config_id=self.config_id))
            other.receive(self)
            self.seq += 1
            self.next_packet += 0.1

    def hears(self, tx: "Device") -> bool:
        if self.role != "rx" or self.channel != tx.channel or self.world.t < self.paused_until:
            return False
        return self.world.input_dbm(tx) > self.sensitivity_dbm

    def receive(self, tx: "Device") -> None:
        if self.role != "rx" or self.channel != tx.channel or self.world.t < self.paused_until:
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
            config_id=self.config_id, tx_config_id=tx.config_id, sample_seq=n))


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
    plan["leak"]["duration_s"] = 2
    plan["warmup"].update(chunk_s=2, stable_chunks=2, max_chunks=6, stable_db=0.5)
    plan["coarse"]["duration_s"] = 4
    plan["fine"].update(duration_s=3, below_db=3, above_db=3)
    plan["power"].update(duration_s=4, dbm=[9, 12, 15, 18])
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
        runner.configure("a", role="rx", channel=1, power_dbm=15)


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
    assert all(c.rx is None and c.tx_outputs == () for c in cals)
    with pytest.raises(S.SessionError, match="RX の校正がありません"):
        C.endpoint_calibration(cals[0], "rx")


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
    assert report["pair_difference"]["mean"] == pytest.approx(expected, abs=0.2)


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


def _ref(world: World) -> dict:
    ref = BA.ref_template()
    ref["fixed_loss_db"] = FIXED_LOSS
    for dev in (world.a, world.b):
        ref["tx_output"].append({
            "device_id": R.mac(tuple(dev.mac)), "power_cdbm": 1500, "channel": 1,
            "dbm": 15 + dev.tx_offset_db, "measured_on": "2026-09-25", "instrument": "PM",
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
        assert cal.rx is not None
        # dBm = 生値 − rx_offset
        assert cal.rx.offset_db == pytest.approx(-dev.rx_offset, abs=0.3)
        assert cal.rx.reference_unit_id != cal.device_id        # 相手の TX が基準
        assert cal.rx.reference_measured_on == "2026-09-25"


def test_the_tx_output_table_is_derived_from_the_power_sweep(full_run):
    """パワー計の 1 点から、電力掃引の相対差で他の電力の出力を導くこと（出どころを分けて）。"""
    world, runner = full_run
    report = BA.analyze(runner.directory, _ref(world))
    cals = {c.device_id: c for c in BA.device_calibrations(report)}
    for dev in (world.a, world.b):
        rows = {(t.power_cdbm, t.channel): t for t in cals[R.mac(tuple(dev.mac))].tx_outputs}
        assert rows[(1500, 1)].source == "meter"
        for p in (900, 1200, 1800):
            assert rows[(p, 1)].source == "bench_power_sweep"
            assert rows[(p, 1)].dbm == pytest.approx(p / 100 + dev.tx_offset_db, abs=0.3)
        # チャネル 7・13 はパワー計で測っていない＝導かない（TX と RX のどちらの差か分けられない）。
        assert not any(ch != 1 for _p, ch in rows)


def test_the_closure_check_compares_a_signal_generator_point(full_run):
    world, runner = full_run
    ref = _ref(world)
    ref["rx_direct"] = [
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
        tx.echoes.append((t0 + timedelta(seconds=0.1 * seq), {"seq": seq, "config_id": 3}))
    for seq in range(10):
        if seq in (4, 5):
            continue                                        # USB で落ちた（番号は振られていた）
        rx.samples.append((t0 + timedelta(seconds=0.1 * seq), {
            "tx_id": (1, 2, 3, 4, 5, 6), "tx_config_id": 3, "config_id": 7, "seq": seq,
            "rssi_raw": -60, "noise_floor_raw": -95, "sample_seq": seq}))
    row = {"step": 1, "kind": "level", "label": "x", "tx_unit": "a", "rx_unit": "b",
           "tx_device": "01:02:03:04:05:06", "rx_device": "x", "tx_config_id": 3,
           "rx_config_id": 7, "power_cdbm": 1500, "channel": 1, "atten_db": 10,
           "start_utc": S.format_utc(_BASE), "end_utc": S.format_utc(_BASE + timedelta(seconds=1))}
    s = BA.step_stats(row, tx, rx)
    assert (s.sent, s.received, s.usb_lost) == (10, 8, 2)
    assert s.rate == 1.0


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
    assert cal.device_id == device and cal.rx is not None and cal.tx_outputs
    assert C.calib_path(calib_dir, device).name == "aa-bb-cc-00-00-0a.json"


def test_record_copies_the_calibration_by_unit_id(full_run, tmp_path):
    """🔑 校正ファイルを個体 ID で読み、RX は受信の校正を・TX は**動いている設定の行**の
    出力を写すこと。役割を入れ替えた組でも同じファイルが使えること。"""
    world, calib_dir = _calibrated(full_run, tmp_path)
    for tx, rx in ((world.a, world.b), (world.b, world.a)):
        cals = {R.mac(tuple(d.mac)): C.read_device_calibration(calib_dir, R.mac(tuple(d.mac)))
                for d in (tx, rx)}
        tx.role, rx.role = "tx", "rx"
        tx.channel = rx.channel = 1                        # 全ステップの後はチャネル掃引の最後のまま
        tx.power_qdbm = 48                                 # 12 dBm（電力掃引から導いた行）
        header = REC.build_header(
            _template(world, tx, rx), _config_message(rx), _config_message(tx),
            started_utc=S.format_utc(_BASE), software_commit="abc", dialect=world.dialect,
            calibrations=cals,
        )
        assert header.tx.calibration.tx_output_dbm == pytest.approx(12 + tx.tx_offset_db, abs=0.3)
        assert "bench_power_sweep" in header.tx.calibration.note
        assert header.rx.calibration.offset_db == pytest.approx(-rx.rx_offset, abs=0.3)
        assert header.rx.calibration.tx_output_dbm is None


def test_record_refuses_a_setting_that_the_file_does_not_cover(full_run, tmp_path):
    world, calib_dir = _calibrated(full_run, tmp_path)
    cals = {R.mac(tuple(d.mac)): C.read_device_calibration(calib_dir, R.mac(tuple(d.mac)))
            for d in (world.a, world.b)}
    world.a.role, world.b.role = "tx", "rx"
    with pytest.raises(S.SessionError, match="チャネル 7 の出力がありません"):
        REC.build_header(
            _template(world, world.a, world.b), _config_message(world.b, channel=7),
            _config_message(world.a, channel=7, tx_power_cdbm=1500),
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
    ref = tmp_path / "ref.json"
    assert cli.main(["bench", "ref", str(ref)]) == 0


def test_the_default_power_sweep_includes_the_metered_power():
    """他の電力の出力は、パワー計で測った電力（計画の power_dbm）からの相対で導く＝
    掃引に含まれていないと 1 行も導けない。"""
    plan = B.default_plan()
    assert plan["power_dbm"] in plan["power"]["dbm"]


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
    kwargs = dict(seconds=3, power_dbm=15, channel=1, software_commit=_HEAD,
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


def test_a_probe_folder_is_not_analyzed_as_a_bench(tmp_path, dialect):
    """動作確認は減衰量を記録しない＝机上試験の判定に混ぜないこと。"""
    world = World(dialect)
    runner = _probe_runner(tmp_path, world)
    _run_probe(runner, [])
    with pytest.raises(B.BenchError, match="机上試験のフォルダではありません"):
        BA.analyze(runner.directory)


# --- 連続送信 --------------------------------------------------------------------


def test_the_continuous_report_is_read_as_the_firmware_writes_it():
    """ファームの書式（`radio.c` の snprintf）と PC 側の読み方が食い違わないこと。"""
    radio = (Path(__file__).resolve().parents[1] / "apps" / "field" / "firmware" / "main"
             / "radio.c").read_text(encoding="utf-8")
    fmt = re.search(r'"(CONT duty=%\.5f intervals=%lu[^"]*)"\s*"([^"]*)"', radio)
    assert fmt, "連続送信の報告の書式が見つからない"
    line = (fmt.group(1) + fmt.group(2)).replace("%.5f", "0.96123").replace("%lld", "5") \
        .replace("%lu", "7").replace("%u", "12224")
    parsed = cli.parse_cont_line(line)
    assert parsed == {"duty": 0.96123, "intervals": 7, "other_rate": 7}


def test_the_burst_power_corrects_the_average_reading_by_the_duty():
    assert cli.burst_dbm(10.0, kind="avg", duty=0.5, pad_db=0.0) == pytest.approx(10 + 3.0103, abs=1e-3)
    assert cli.burst_dbm(10.0, kind="burst", duty=0.5, pad_db=20.0) == pytest.approx(30.0)
    with pytest.raises(S.SessionError):
        cli.burst_dbm(10.0, kind="avg", duty=0.0, pad_db=0.0)


def test_the_continuous_mode_uses_the_measurement_transmit_path():
    """🔑 連続送信は**測定と同じ送信経路**（esp_wifi_80211_tx・同じ電力の設定）で送り、
    PHY の試験モードを使わないこと（電力の指定の経路が違うと、測った値が測定の出力でない）。
    RX がサンプルにしない識別子で送り、始める前に設定番号を進めること。"""
    main_dir = Path(__file__).resolve().parents[1] / "apps" / "field" / "firmware" / "main"
    radio = re.sub(r"/\*.*?\*/", "", (main_dir / "radio.c").read_text(encoding="utf-8"),
                   flags=re.DOTALL)
    body = radio.split("static void run_continuous(uint32_t seconds)", 1)[1].split("\n}\n", 1)[0]
    assert "esp_wifi_80211_tx(" in body
    assert "esp_phy" not in radio and "set_max_tx_power" not in body
    assert "RADIOSIM_FIELD_AIR_CONT_TAG" in body and "RADIOSIM_FIELD_AIR_TAG" not in body
    assert re.search(r"#define CONT_AIRTIME_US \(192u \+ \(CONT_FRAME_LEN \+ FCS_LEN\) \* 8u\)", radio)
    main = re.sub(r"/\*.*?\*/", "", (main_dir / "main.c").read_text(encoding="utf-8"),
                  flags=re.DOTALL)
    handler = main.split("static bool handle_continuous(", 1)[1].split("\n}\n", 1)[0]
    assert handler.index("settings_bump_config_id(") < handler.index("radio_continuous((uint32_t)")
    assert "RADIOSIM_FIELD_ROLE_TX" in handler


def test_the_airtime_matches_11b_long_preamble():
    """1500 バイト＋FCS 4 バイトを 1 Mbps・ロングプリアンブル（192 µs）で送る時間。"""
    assert 192 + (1500 + 4) * 8 == 12224
    assert -10 * math.log10(12224 / 12600) == pytest.approx(0.13, abs=0.01)


# --- 文字の行 --------------------------------------------------------------------


def test_text_lines_are_picked_out_between_frames_in_any_split(dialect):
    """返答・報告の行はフレームの間に流れる＝どう区切って読んでも同じ行・同じフレーム。"""
    dev = World(dialect).a
    data = (b"OK config_id=3 \xe5\x86\x8d\xe8\xb5\xb7\xe5\x8b\x95\r\n"
            + dev.frame("RADIOSIM_FIELD_CONFIG", dev.config_values())
            + b"CONT duty=0.97000 intervals=5 airtime_us=12224 span_us=1 other_rate=0\r\n"
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
        assert lines[0].startswith("OK config_id=3") and lines[1].startswith("CONT duty=")
        assert stream.stats.crc_errors == 0
