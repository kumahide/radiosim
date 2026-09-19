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


def test_the_monitor_command_is_on_the_cli():
    with pytest.raises(SystemExit) as e:
        cli.main(["monitor", "--help"])
    assert e.value.code == 0
