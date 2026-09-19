"""
tests/test_tracer_monitor.py
============================
実測補助アプリ（`apps/tracer`）の `monitor`（受信を眺めるだけ・I-161）を検証する。

  - 欠けを電波と USB に分けて数えること（`seq` と `sample_seq` の飛びの差）
  - 機器の再起動（番号が戻る）を欠けに数えないこと
  - 設定は変わったときだけ知らせること
  - 受信の無い区切りと CRC 不一致を表示すること

⚠️ シリアルポートは触らない（`run_monitor` は `read`/`in_waiting` だけを使う）。
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.tracer import cli
from apps.tracer.mavlink import dialect as D
from apps.tracer.mavlink import gen_c as G
from apps.tracer.mavlink.reader import read_log

RX = bytes.fromhex("98a3168e92ac")
TX = bytes.fromhex("98a3168df660")


@pytest.fixture(scope="module")
def dialect() -> D.Dialect:
    return D.load_dialect()


def _frame(dialect: D.Dialect, name: str, **values) -> bytes:
    return G.encode_frame(dialect.messages_by_name[name], values)


def _sample(dialect: D.Dialect, seq: int, sample_seq: int, rssi: int = -27) -> bytes:
    return _frame(dialect, "TRACER_RX_SAMPLE", rx_id=tuple(RX), tx_id=tuple(TX), seq=seq,
                  rx_time_us=seq * 100_000, rssi_raw=rssi, noise_floor_raw=-93,
                  config_id=1, tx_config_id=1, sample_seq=sample_seq)


def _config(dialect: D.Dialect, config_id: int = 1, role: int = 1) -> bytes:
    return _frame(dialect, "TRACER_CONFIG", config_id=config_id, role=role,
                  device_id=tuple(RX), firmware_version="c08507ff00", channel=1,
                  rate_kbps=1000, tx_power_cdbm=1500, tx_interval_ms=100, detector=1,
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
    assert "出力 15 dBm" in said[0]


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
                           firmware_version="c08507ff00", channel=1, rate_kbps=1000,
                           tx_power_cdbm=1500, tx_interval_ms=100, detector=1,
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
            self.buffer += _frame(self.dialect, "TRACER_CONFIG", **self.values)
            if self.relay is not None:
                other = dict(self.values, role=0, device_id=tuple(self.relay), config_id=3)
                self.buffer += _frame(self.dialect, "TRACER_CONFIG", **other)
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
            "power": ("tx_power_cdbm", lambda a: round(float(a) * 100)),
        }[cmd]
        value = parse(arg[0])
        if field == "channel" and not 1 <= value <= 13:
            self.buffer += "ERR channel は 1〜13 です\r\n".encode()
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


def _changes(**over) -> list:
    import argparse
    args = argparse.Namespace(role=None, channel=None, interval=None, power=None)
    for k, v in over.items():
        setattr(args, k, v)
    return cli.settings_changes(args)


def test_settings_changes_and_reads_back(dialect):
    unit = _Unit(dialect, relay=TX)
    said: list[str] = []
    final = cli.apply_settings(unit, _changes(role="tx", channel=6, power=10.25),
                               say=said.append, clock=unit.clock)
    # 役割は最後に送る（TX にしてから他を変えると、変えるたびに送信が途切れる）
    assert unit.sent == ["show", "channel 6", "power 10.25", "role tx"]
    assert (final["role"], final["channel"], final["tx_power_cdbm"]) == (0, 6, 1025)
    assert final["config_id"] == 10
    # 中継された他の TX（config_id 3）ではなく、自分の設定を表示している
    assert said[0].startswith("現在: RX 98:a3:16:8e:92:ac")
    assert "config_id 7" in said[0]
    assert said[-1].startswith("変更後: TX 98:a3:16:8e:92:ac  チャネル 6・間隔 100 ms・出力 10.25 dBm")


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
    with pytest.raises(cli.SessionError, match="断りました: ERR channel は 1〜13 です"):
        cli.apply_settings(unit, [("channel 14", "channel", 14)],
                           say=lambda _m: None, clock=unit.clock)


@pytest.mark.parametrize("over", [dict(channel=0), dict(channel=14), dict(interval=19),
                                  dict(interval=10001), dict(power=1.75), dict(power=20.25),
                                  dict(power=10.1)])
def test_settings_refuses_out_of_range_values_before_sending(over):
    with pytest.raises(cli.SessionError):
        _changes(**over)


def test_the_monitor_command_is_on_the_cli():
    with pytest.raises(SystemExit) as e:
        cli.main(["monitor", "--help"])
    assert e.value.code == 0
