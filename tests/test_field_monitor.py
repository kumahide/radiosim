"""
tests/test_field_monitor.py
============================
実測補助アプリ（`apps/field`）の `monitor`（受信を眺めるだけ・I-161）を検証する。

  - 欠けを電波と USB に分けて数えること（`seq` と `sample_seq` の飛びの差）
  - TX が送らなかった周期（`unsent_total` の差）を電波の欠けに数えないこと（B-355）
  - 機器の再起動（番号が戻る）を欠けに数えないこと
  - 設定は変わったときだけ知らせること
  - 受信の無い区切りと CRC 不一致を表示すること

⚠️ シリアルポートは触らない（`run_monitor` は `read`/`in_waiting` だけを使う）。
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.field import cli
from apps.field.mavlink import dialect as D
from apps.field.mavlink import gen_c as G
from apps.field.mavlink.reader import read_log

RX = bytes.fromhex("98a3168e92ac")
TX = bytes.fromhex("98a3168df660")


@pytest.fixture(scope="module")
def dialect() -> D.Dialect:
    return D.load_dialect()


def _frame(dialect: D.Dialect, name: str, **values) -> bytes:
    return G.encode_frame(dialect.messages_by_name[name], values)


def _sample(dialect: D.Dialect, seq: int, sample_seq: int, rssi: int = -27) -> bytes:
    return _frame(dialect, "RADIOSIM_FIELD_RX_SAMPLE", rx_id=tuple(RX), tx_id=tuple(TX), seq=seq,
                  rx_time_us=seq * 100_000, rssi_raw=rssi, noise_floor_raw=-93,
                  config_id=1, tx_config_id=1, sample_seq=sample_seq,
                  sent_mask=0xFFFFFFFF, unsent_total=0)


def _config(dialect: D.Dialect, config_id: int = 1, role: int = 1) -> bytes:
    return _frame(dialect, "RADIOSIM_FIELD_CONFIG", config_id=config_id, role=role,
                  device_id=tuple(RX), firmware_version="c08507ff00", channel=1,
                  band=920, rate_kbps=0, air_sf=7, air_bw_khz=500, tx_power_cdbm=1300, tx_interval_ms=250, detector=1,
                  integration_window_us=0, integration_samples=1, antenna=2)


class _Port:
    """区切りごとのバイト列を 1 回の読み出しで 1 つずつ返す。"""

    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)

    @property
    def in_waiting(self) -> int:
        return len(self.chunks[0]) if self.chunks else 0

    def read(self, size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


def _run(chunks: list[bytes]) -> tuple[list[str], cli.ReadStats]:
    """1 回の読み出しを 1 秒として回し、表示された行を返す。"""
    port = _Port(chunks)
    reads = len(chunks)
    t = [0.0]

    def clock() -> float:
        t[0] += 1.0
        return t[0]

    said: list[str] = []
    stats = cli.run_monitor(port, say=said.append, clock=clock, stamp=lambda: "T",
                            stop=lambda: t[0] >= reads + 1)
    return said, stats


def test_losses_are_split_into_air_and_usb(dialect):
    tally = cli.MonitorTally(dialect)
    # seq 0→3 で sample_seq 0→1（2 つとも電波で欠け）、3→6 で 1→3（1 つは USB・1 つは電波）
    messages, _ = read_log(_sample(dialect, 0, 0) + _sample(dialect, 3, 1)
                           + _sample(dialect, 6, 3, rssi=-29))
    for m in messages:
        tally.add(m)
    [line] = tally.lines()
    assert "受信 3" in line
    assert "欠け 電波 3・USB 1" in line
    assert "平均 -27.7（-29〜-27）" in line
    assert "雑音フロア -93" in line
    assert tally.lines() == []                 # 表示したら数え直す


def test_cycles_the_tx_did_not_send_are_not_air_losses(dialect):
    """B-355。TX が送らなかった周期（`unsent_total` の差）は電波の欠けに数えない。
    seq 0→3 の間の 2 周期を送らず、3→5 の間の 1 周期は送ったが届かなかった。"""
    tally = cli.MonitorTally(dialect)
    frames = b"".join(
        _frame(dialect, "RADIOSIM_FIELD_RX_SAMPLE", rx_id=tuple(RX), tx_id=tuple(TX), seq=seq,
               rx_time_us=seq * 100_000, rssi_raw=-27, noise_floor_raw=-93, config_id=1,
               tx_config_id=1, sample_seq=sample_seq, sent_mask=0xFFFFFFFF, unsent_total=total)
        for seq, sample_seq, total in ((0, 0, 0), (3, 1, 2), (5, 2, 2)))
    messages, _ = read_log(frames)
    for m in messages:
        tally.add(m)
    [line] = tally.lines()
    assert "欠け 電波 1・USB 0・送らず 2" in line


def test_a_restart_is_not_counted_as_a_loss(dialect):
    said, _ = _run([_sample(dialect, 40, 40) + _sample(dialect, 0, 0) + _sample(dialect, 1, 1)])
    assert any("欠け 電波 0・USB 0" in s for s in said)


def test_a_config_is_announced_once_and_again_when_it_changes(dialect):
    said, _ = _run([_config(dialect), _config(dialect), _config(dialect, config_id=2)])
    notes = [s for s in said if s.startswith("設定")]
    assert len(notes) == 2
    assert notes[0].startswith("設定 RX 98:a3:16:8e:92:ac")
    assert "出力" not in notes[0]              # RX は送らないので出力を名乗らない
    said, _ = _run([_config(dialect, role=0)])
    assert "出力 13 dBm" in said[0]


def test_a_quiet_second_says_so_and_crc_errors_are_shown(dialect):
    broken = bytearray(_sample(dialect, 0, 0))
    broken[12] ^= 0xFF
    said, stats = _run([bytes(broken), b""])
    assert stats.crc_errors == 1
    assert "T  受信なし  CRC 不一致（累計）1" in said


# --- settings -------------------------------------------------------------------


class _Unit:
    """ファームの設定の振る舞いの写し（`settings.c`・変えると再起動・1 秒ごとの設定）。
    `relay` を渡すと、RX が中継する他の TX の設定も流す。"""

    def __init__(self, dialect: D.Dialect, *, role: int = 1, relay: bytes | None = None,
                 ignore: str | None = None):
        self.dialect = dialect
        self.t = 0.0
        self.values: dict[str, Any] = dict(config_id=7, role=role, device_id=tuple(RX),
                           firmware_version="c08507ff00", channel=1, band=920, rate_kbps=0, air_sf=7,
                           air_bw_khz=500, tx_power_cdbm=1300, tx_interval_ms=250, detector=1,
                           integration_window_us=0, integration_samples=1, antenna=2)
        self.relay = relay
        self.ignore = ignore            # この欄だけ、変えたと言いながら変えない（故障の模擬）
        self.buffer = bytearray()
        self.paused_until = 0.0
        self.next_config = 0.0
        self.sent: list[str] = []

    def clock(self) -> float:
        return self.t

    @property
    def in_waiting(self) -> int:
        return len(self.buffer)

    def read(self, size: int) -> bytes:
        self.t += 0.05
        if self.t >= self.paused_until and self.t >= self.next_config:
            self.buffer += _frame(self.dialect, "RADIOSIM_FIELD_CONFIG", **self.values)
            if self.relay is not None:
                other = dict(self.values, role=0, device_id=tuple(self.relay), config_id=3)
                self.buffer += _frame(self.dialect, "RADIOSIM_FIELD_CONFIG", **other)
            self.next_config = self.t + 1.0
        out = bytes(self.buffer[:size])
        del self.buffer[:size]
        return out

    def write(self, data: bytes) -> int:
        line = data.decode("ascii").strip()
        self.sent.append(line)
        cmd, *arg = line.split()
        if cmd == "show":
            self.buffer += b"OK\r\n"
            self.next_config = self.t
            return len(data)
        field, parse = {
            "role": ("role", lambda a: 0 if a == "tx" else 1),
            "channel": ("channel", int),
            "interval": ("tx_interval_ms", int),
            "power": ("tx_power_cdbm", lambda a: int(a) * 100),
        }[cmd]
        value = parse(arg[0])
        if field == "channel" and not 0 <= value <= 6:
            self.buffer += "ERR channel は 0〜6 です\r\n".encode()
            return len(data)
        if self.values[field] == value:
            self.buffer += "OK 変わっていません\r\n".encode()
            return len(data)
        self.values["config_id"] += 1
        if field != self.ignore:
            self.values[field] = value
        self.buffer += f"OK config_id={self.values['config_id']} 再起動します\r\n".encode()
        self.paused_until = self.t + 0.5
        self.next_config = self.paused_until
        return len(data)


def _changes(on: str = "920", **over) -> list:
    import argparse
    args = argparse.Namespace(band=None, role=None, channel=None, interval=None, power=None)
    for k, v in over.items():
        setattr(args, k, v)
    return cli.settings_changes(args, on)


def test_settings_changes_and_reads_back(dialect):
    unit = _Unit(dialect, relay=TX)
    said: list[str] = []
    final = cli.apply_settings(unit, _changes(role="tx", channel=6, power=10),
                               say=said.append, clock=unit.clock)
    # 役割は最後に送る（TX にしてから他を変えると、変えるたびに送信が途切れる）
    assert unit.sent == ["show", "channel 6", "power 10", "role tx"]
    assert (final["role"], final["channel"], final["tx_power_cdbm"]) == (0, 6, 1000)
    assert final["config_id"] == 10
    # 中継された他の TX（config_id 3）ではなく、自分の設定を表示している
    assert said[0].startswith("現在: RX 98:a3:16:8e:92:ac")
    assert "config_id 7" in said[0]
    assert said[-1].startswith(
        "変更後: TX 98:a3:16:8e:92:ac  920 MHz 帯・チャネル 6・間隔 250 ms・出力 10 dBm")


def test_settings_checks_the_range_against_the_band_read_from_the_device(dialect):
    """帯域を指定しないときは、機器の今の帯域（ここでは 920）で範囲を確かめること。"""
    unit = _Unit(dialect)
    with pytest.raises(cli.SessionError, match="0〜6 です（920 MHz 帯）"):
        cli.apply_settings(unit, lambda band: _changes(band, channel=11),
                           say=lambda _m: None, clock=unit.clock)
    assert unit.sent == ["show"]                  # 範囲の外は送らない


def test_settings_without_changes_only_shows(dialect):
    unit = _Unit(dialect)
    said: list[str] = []
    cli.apply_settings(unit, [], say=said.append, clock=unit.clock)
    assert unit.sent == ["show"]
    assert len(said) == 1 and said[0].startswith("現在: RX")


def test_settings_skips_a_value_that_is_already_set(dialect):
    unit = _Unit(dialect)
    said: list[str] = []
    cli.apply_settings(unit, _changes(channel=1), say=said.append, clock=unit.clock)
    assert unit.sent == ["show"]
    assert "既にこの値です" in said[1]


def test_settings_catches_a_change_that_did_not_take(dialect):
    unit = _Unit(dialect, ignore="channel")
    with pytest.raises(cli.SessionError, match="のままです"):
        cli.apply_settings(unit, _changes(channel=6), say=lambda _m: None, clock=unit.clock)


def test_settings_reports_a_refusal(dialect, monkeypatch):
    # PC 側の範囲の確かめを抜けたと仮定して、機器の断りがそのまま出ることを見る
    unit = _Unit(dialect)
    with pytest.raises(cli.SessionError, match="断りました: ERR channel は 0〜6 です"):
        cli.apply_settings(unit, [("channel 13", "channel", 13)],
                           say=lambda _m: None, clock=unit.clock)


@pytest.mark.parametrize("over", [dict(channel=-1), dict(channel=13), dict(interval=249),
                                  dict(interval=10001), dict(power=-1), dict(power=14)])
def test_settings_refuses_out_of_range_values_before_sending(over):
    with pytest.raises(cli.SessionError):
        _changes(**over)


@pytest.mark.parametrize("over", [dict(channel=0), dict(channel=14), dict(interval=19),
                                  dict(power=1.75), dict(power=20.25), dict(power=10.1)])
def test_settings_refuses_out_of_range_values_on_2400(over):
    with pytest.raises(cli.SessionError, match="2400 MHz 帯"):
        _changes("2400", **over)


def test_settings_on_2400_accepts_its_own_range_and_switches_the_band_first():
    """2.4 GHz の範囲（チャネル 1〜13・間隔 20 ms〜・電力 0.25 刻み）を通し、帯域を最初に送ること
    （チャネルなどは帯域ごとに別に持つ＝先に切り替えないと別の帯域の値を変える）。"""
    changes = _changes("2400", band="2400", role="tx", channel=13, interval=20, power=17.25)
    assert [c[0] for c in changes] == [
        "band 2400", "channel 13", "interval 20", "power 17.25", "role tx"]
    assert changes[0][1:] == ("band", 2400) and changes[3][2] == 1725


def test_cont_line_and_burst_power():
    """連続送信の報告を読み、平均電力計の読みをデューティでバースト中の電力へ直すこと。"""
    line = ("CONT duty=0.50000 intervals=1200 airtime_us=1200 span_us=2880000 other_rate=0 "
            "failures=0 lost_notices=0 remaining_s=30")
    assert cli.parse_cont_line(line) == {"duty": 0.5, "intervals": 1200, "other_rate": 0}
    assert cli.parse_cont_line("CONT END") is None
    assert cli.burst_dbm(10.0, kind="avg", duty=0.5, pad_db=1.0) == pytest.approx(14.0103, abs=1e-4)
    assert cli.burst_dbm(10.0, kind="burst", duty=0.5, pad_db=1.0) == 11.0
    with pytest.raises(cli.SessionError, match="デューティ"):
        cli.burst_dbm(10.0, kind="avg", duty=0.0, pad_db=0.0)


def test_the_monitor_command_is_on_the_cli():
    with pytest.raises(SystemExit) as e:
        cli.main(["monitor", "--help"])
    assert e.value.code == 0
