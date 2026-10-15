"""
tests/test_field_gnss.py
=========================
実測補助アプリ（`apps/field`）の GNSS の生データの記録器（`gnss.py`・`gnss log`）を検証する。

  - UBX の枠（検査和・読み出しの区切りをまたぐ・NMEA が混ざる・壊れた枠）
  - 始め方＝MON-VER → CFG-VALSET（RAM 層だけ）→ ACK を確かめてからフォルダを作る
  - NAK・無返答ならフォルダを作らずに止まる
  - ヘッダを書くまでに受けたバイトも捨てず、生ログは受けたバイト列と一致する
  - 表示の数え（RAWX の欠け・解の状態）と、読めない中身で記録が止まらないこと

⚠️ シリアルポートは触らない（偽の受信機は `read`/`in_waiting`/`write` だけを持つ）。
"""

from __future__ import annotations

import json
import struct

import pytest

from apps.field import cli
from apps.field import gnss as G
from apps.field.session import read_raw

NMEA = b"$GNGGA,000000.00,,,,,0,00,99.99,,,,,,*56\r\n"


def _mon_ver() -> bytes:
    sw = b"EXT CORE 2.11 (c97ee1)".ljust(30, b"\0")
    hw = b"000A0000".ljust(10, b"\0")
    ext = [b"FWVER=HPG 2.11", b"PROTVER=50.11", b"MOD=ZED-X20P"]
    return sw + hw + b"".join(e.ljust(30, b"\0") for e in ext)


def _rawx(tow: float, week: int = 2440, tracking: tuple[int, ...] = (0x03, 0x01, 0x00)) -> bytes:
    head = struct.pack("<dHbBBBxx", tow, week, 18, len(tracking), 0x01, 1)
    meas = b"".join(bytes(30) + bytes((t, 0)) for t in tracking)
    return G.frame(G.RXM_RAWX, head + meas)


def _pvt(fix_type: int = 3, flags: int = 0x01, satellites: int = 12,
         h_acc_mm: int = 1500, v_acc_mm: int = 2300) -> bytes:
    payload = bytearray(92)
    struct.pack_into("<BBBB", payload, 20, fix_type, flags, 0, satellites)
    struct.pack_into("<II", payload, 40, h_acc_mm, v_acc_mm)
    return G.frame(G.NAV_PVT, bytes(payload))


class _Receiver:
    """受信機の振る舞いの写し。NMEA を流し続け、MON-VER と CFG-VALSET に応える。
    `answer` は VALSET への返事（"ack"・"nak"・None＝黙る）。1 回の読み出しで時計が 0.1 秒進む。"""

    def __init__(self, *, answer: str | None = "ack", after: list[bytes] | None = None):
        self.answer = answer
        self.after = list(after or [])      # ACK の後、読み出し 1 回に 1 つずつ流す
        self.t = 0.0
        self.buffer = bytearray(NMEA)
        self.sent = bytearray()             # 受信機が出したバイトの全部（生ログと比べる）
        self.written: list[bytes] = []
        self.configured = False

    def ticks(self) -> float:
        return self.t

    @property
    def in_waiting(self) -> int:
        return len(self.buffer)

    def read(self, size: int) -> bytes:
        self.t += 0.1
        if not self.buffer:
            self.buffer += self.after.pop(0) if self.configured and self.after else NMEA
        # わざと半端な長さで切る＝枠が読み出しの区切りをまたぐ
        out = bytes(self.buffer[:max(1, min(size, 37))])
        del self.buffer[:len(out)]
        self.sent += out
        return out

    def write(self, data: bytes) -> int:
        self.written.append(data)
        kind = (data[2], data[3])
        if kind == G.MON_VER:
            self.buffer += G.frame(G.MON_VER, _mon_ver())
        elif kind == G.CFG_VALSET and self.answer is not None:
            reply = G.ACK_ACK if self.answer == "ack" else G.ACK_NAK
            self.buffer += G.frame(reply, bytes(G.CFG_VALSET))
            self.configured = self.answer == "ack"
        return len(data)


def _clock(receiver: _Receiver):
    def clock() -> str:
        total = round(receiver.t * 10)
        return f"2026-10-16T05:{total // 600:02d}:{total // 10 % 60:02d}.{total % 10}00000Z"
    return clock


def _logger(tmp_path, receiver: _Receiver) -> G.GnssLogger:
    return G.GnssLogger(tmp_path, receiver, software_commit="abc123", port_name="COM5",
                        baud=38400, clock=_clock(receiver), ticks=receiver.ticks)


def _drain(logger: G.GnssLogger, receiver: _Receiver, reads: int = 400) -> None:
    for _ in range(reads):
        logger.poll()


# --- UBX の枠 ---------------------------------------------------------------------


def test_the_poll_frame_matches_the_known_bytes():
    # MON-VER の問い合わせ＝u-blox の文書どおりの 8 バイト
    assert G.frame(G.MON_VER) == bytes.fromhex("b5620a0400000e34")


def test_valset_writes_the_ram_layer_only_and_refuses_wide_keys():
    data = G.valset((("CFG-MSGOUT-UBX_RXM_RAWX_USB", 0x209102A7, 1),))
    assert data[2:4] == bytes(G.CFG_VALSET)
    assert data[6:10] == bytes((0, G.LAYER_RAM, 0, 0))
    assert data[10:15] == struct.pack("<IB", 0x209102A7, 1)
    with pytest.raises(G.GnssError, match="1 バイトの値"):
        G.valset((("CFG-RATE-MEAS", 0x30210001, 1000),))     # 2 バイトの値のキー


def test_the_stream_finds_frames_among_nmea_and_across_reads():
    data = NMEA + _rawx(1.0) + NMEA + _pvt() + NMEA
    stream = G.UbxStream()
    messages = [m for i in range(0, len(data), 5) for m in stream.feed(data[i:i + 5])]
    assert [m.kind for m in messages] == [G.RXM_RAWX, G.NAV_PVT]
    assert stream.stats.frames == 2
    assert stream.stats.other_bytes == 3 * len(NMEA)


def test_a_broken_frame_is_counted_and_the_next_one_is_still_found():
    broken = bytearray(_rawx(1.0))
    broken[20] ^= 0xFF
    stream = G.UbxStream()
    messages = stream.feed(bytes(broken) + _pvt())
    assert [m.kind for m in messages] == [G.NAV_PVT]
    assert stream.stats.checksum_errors == 1


def test_contents_are_read():
    version = G.parse_mon_ver(_mon_ver())
    assert version.software == "EXT CORE 2.11 (c97ee1)"
    assert version.firmware == "HPG 2.11"
    epoch = G.parse_rawx(_rawx(12.0)[6:-2])
    assert (epoch.week, epoch.rcv_tow, epoch.signals, epoch.pr_valid, epoch.cp_valid) \
        == (2440, 12.0, 3, 2, 1)
    pvt = G.parse_nav_pvt(_pvt(flags=0x01 | 0x80)[6:-2])
    assert pvt.describe() == "3D・RTK Fix"
    assert (pvt.satellites, pvt.h_acc_m, pvt.v_acc_m) == (12, 1.5, 2.3)
    assert G.parse_nav_pvt(_pvt(flags=0)[6:-2]).describe() == "3D（無効）"
    with pytest.raises(G.GnssError):
        G.parse_rawx(_rawx(1.0)[6:-3])                       # 信号数と長さが合わない


# --- 始め方 ---------------------------------------------------------------------


def test_start_asks_configures_and_then_writes_the_header(tmp_path):
    receiver = _Receiver(after=[_rawx(1.0), _pvt()])
    logger = _logger(tmp_path, receiver)
    directory = logger.start()
    # 問い合わせてから設定する。設定は RAM 層だけ
    assert [(w[2], w[3]) for w in receiver.written] == [G.MON_VER, G.CFG_VALSET]
    assert receiver.written[1][7] == G.LAYER_RAM
    header = json.loads((directory / G.HEADER_FILE).read_text(encoding="utf-8"))
    assert directory.name == header["log_id"] == "gnss-20261016T050000Z"
    assert header["software_commit"] == "abc123"
    assert (header["port"], header["baud"]) == ("COM5", 38400)
    assert header["receiver"]["firmware"] == "HPG 2.11"
    assert "MOD=ZED-X20P" in header["receiver"]["extensions"]
    assert header["config"]["layers"] == "RAM"
    assert [i["key"] for i in header["config"]["items"]] == [f"{k:#010x}" for _n, k, _v in G.OUTPUTS]


def test_the_raw_log_keeps_every_byte_including_those_before_the_header(tmp_path):
    receiver = _Receiver(after=[_rawx(1.0), _pvt(), _rawx(2.0)])
    logger = _logger(tmp_path, receiver)
    directory = logger.start()
    _drain(logger, receiver)
    chunks = list(read_raw(directory, raw_file=G.RAW_FILE, index_file=G.INDEX_FILE))
    # 問い合わせの前に流れていた NMEA と、MON-VER・ACK の返事が先頭に残っている
    assert chunks[0][1] == NMEA[:37]
    assert b"".join(c for _t, c in chunks) == bytes(receiver.sent)
    assert logger.bytes_written == len(receiver.sent)
    times = [t for t, _c in chunks]
    assert times == sorted(times)                       # 受けた時刻のまま並ぶ
    assert logger.counts == {"rawx": 2, "sfrbx": 0, "pvt": 1}


@pytest.mark.parametrize("answer, message", [("nak", "断りました（NAK）"),
                                             (None, "CFG-VALSET に応えません")])
def test_no_folder_is_made_when_the_receiver_refuses_or_is_silent(tmp_path, answer, message):
    receiver = _Receiver(answer=answer)
    with pytest.raises(G.GnssError, match=message):
        _logger(tmp_path, receiver).start()
    assert list(tmp_path.iterdir()) == []


def test_a_receiver_that_does_not_answer_mon_ver_stops_before_configuring(tmp_path):
    receiver = _Receiver()
    receiver.write = lambda data: receiver.written.append(data) or len(data)   # 何も返さない
    with pytest.raises(G.GnssError, match="MON-VER に応えません"):
        _logger(tmp_path, receiver).start()
    assert len(receiver.written) == 1
    assert list(tmp_path.iterdir()) == []


def test_an_existing_folder_is_not_reused(tmp_path):
    (tmp_path / "gnss-20261016T050000Z").mkdir()
    with pytest.raises(G.GnssError, match="既にあります"):
        _logger(tmp_path, _Receiver()).start()


# --- 数えと表示 -------------------------------------------------------------------


def test_missing_rawx_epochs_are_counted_but_a_restart_is_not(tmp_path):
    receiver = _Receiver(after=[_rawx(t) for t in (10.0, 11.0, 12.0, 15.0, 16.0, 1.0, 2.0)])
    logger = _logger(tmp_path, receiver)
    logger.start()
    _drain(logger, receiver)
    assert logger.counts["rawx"] == 7
    assert logger.rawx_gaps == 2                       # 13 と 14。16→1 は時刻が戻った


def test_rawx_gaps_follow_the_week_rollover(tmp_path):
    receiver = _Receiver(after=[_rawx(604799.0, week=2440), _rawx(0.0, week=2441)])
    logger = _logger(tmp_path, receiver)
    logger.start()
    _drain(logger, receiver)
    assert (logger.counts["rawx"], logger.rawx_gaps) == (2, 0)


def test_an_unreadable_message_is_counted_and_logging_goes_on(tmp_path):
    receiver = _Receiver(after=[G.frame(G.NAV_PVT, bytes(10)), _rawx(1.0)])
    logger = _logger(tmp_path, receiver)
    logger.start()
    _drain(logger, receiver)
    assert logger.undecoded == 1
    assert logger.counts["rawx"] == 1


def test_the_status_line_shows_signals_solution_and_accuracy(tmp_path):
    receiver = _Receiver(after=[_rawx(1.0), _pvt(flags=0x01 | 0x40)])
    logger = _logger(tmp_path, receiver)
    assert "RAWX まだなし" in logger.status_line(0) and "解 まだなし" in logger.status_line(0)
    logger.start()
    _drain(logger, receiver)
    line = logger.status_line(3725)
    assert line.startswith("1:02:05")
    assert "RAWX 信号 3（擬似距離 2・搬送波位相 1）" in line
    assert "解 3D・RTK Float・衛星 12・精度 水平 1.50 m／垂直 2.30 m" in line
    assert "RAWX の欠け 0" in line


def test_the_gnss_log_command_is_on_the_cli():
    with pytest.raises(SystemExit) as e:
        cli.main(["gnss", "log", "--help"])
    assert e.value.code == 0
