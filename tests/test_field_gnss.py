"""
tests/test_field_gnss.py
=========================
実測補助アプリ（`apps/field`）の GNSS の生データの記録器（`gnss.py`・`gnss log`）を検証する。

  - UBX の枠（検査和・読み出しの区切りをまたぐ・NMEA が混ざる・壊れた枠）
  - 始め方＝MON-VER → CFG-VALSET（RAM 層だけ）→ ACK を確かめてからフォルダを作る
  - NAK・無返答ならフォルダを作らずに止まる
  - 記録中は UART1 を 115200 に上げて NMEA を止め、終えたら戻す（前回の取り残しも見つける）
  - ヘッダを書くまでに受けたバイトも捨てず、生ログは受けたバイト列と一致する
  - 表示の数え（RAWX の欠け・解の状態）と、読めない中身で記録が止まらないこと
  - 受信機の解の時系列＝生ログの NAV-PVT を読み直し、セッションの期間の分だけ `gnss.csv` へ
    足す（`gnss attach`）。解の状態の決め方・時刻の確定しない回・2 度足さないこと
  - 止めたときのまとめ＝解の状態の数と、RTCM を使い始めてから最初の Fix までの秒

⚠️ シリアルポートは触らない（偽の受信機は `read`/`in_waiting`/`write` だけを持つ）。
⚠️ RTK Fix の実データはまだ無い（2026-10-17 の室内は Float まで）＝Fix の道は合成の NAV-PVT だけで
   確かめている。屋外で Fix を取ったら、その生ログで `attach` を 1 回通す。
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import struct
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from apps.field import cli
from apps.field import gnss as G
from apps.field import session as S
from apps.field.session import read_raw

NMEA = b"$GNGGA,000000.00,,,,,0,00,99.99,,,,,,*56\r\n"


def _mon_ver() -> bytes:
    sw = b"EXT CORE 2.11 (c97ee1)".ljust(30, b"\0")
    hw = b"000A0000".ljust(10, b"\0")
    ext = [b"FWVER=HPG 2.11", b"PROTVER=50.11", b"MOD=ZED-X20P"]
    return sw + hw + b"".join(e.ljust(30, b"\0") for e in ext)


def _rawx(tow: float, week: int = 2440, tracking: tuple[int, ...] = (0x03, 0x01, 0x00),
          rec_stat: int = 0x01) -> bytes:
    head = struct.pack("<dHbBBBxx", tow, week, 18, len(tracking), rec_stat, 1)
    meas = b"".join(bytes(30) + bytes((t, 0)) for t in tracking)
    return G.frame(G.RXM_RAWX, head + meas)


def _pvt(fix_type: int = 3, flags: int = 0x01, satellites: int = 12,
         h_acc_mm: int = 1500, v_acc_mm: int = 2300) -> bytes:
    payload = bytearray(92)
    struct.pack_into("<BBBB", payload, 20, fix_type, flags, 0, satellites)
    struct.pack_into("<II", payload, 40, h_acc_mm, v_acc_mm)
    return G.frame(G.NAV_PVT, bytes(payload))


def _timed_pvt(second: int, *, carrier: int = 0, diff: bool = True, age: int = 0,
               fix_type: int = 3, ok: bool = True, valid: int = 0x37, nano: int = 0,
               llh_invalid: bool = False, satellites: int = 31) -> bytes:
    """時刻の確定した NAV-PVT（2026-10-16 05:00:<second> UTC）。valid の 0x37 は実機の値
    （2026-10-17 の 2 本の記録の全回）。位置は三角点「中野」の上（後処理の解に近い値）。"""
    payload = bytearray(92)
    struct.pack_into("<HBBBBBB", payload, 4, 2026, 10, 16, 5, 0, second, valid)
    struct.pack_into("<i", payload, 16, nano)
    flags = (0x01 if ok else 0) | (0x02 if diff else 0) | (carrier << 6)
    struct.pack_into("<BBBB", payload, 20, fix_type, flags, 0, satellites)
    struct.pack_into("<iiiiII", payload, 24, 1399447071, 354312407, 36793, 1902, 14, 20)
    struct.pack_into("<HH", payload, 76, 120, (age << 1) | int(llh_invalid))
    return G.frame(G.NAV_PVT, bytes(payload))


def _valset_items(data: bytes) -> dict[int, int]:
    """CFG-VALSET のフレームからキーと値を読む（偽の受信機と検査で使う）。"""
    payload, items, i = data[6:-2], {}, 4
    while i < len(payload):
        key = struct.unpack_from("<I", payload, i)[0]
        size = {1: 1, 2: 1, 3: 2, 4: 4}[(key >> 28) & 0x7]
        items[key] = int.from_bytes(payload[i + 4:i + 4 + size], "little")
        i += 4 + size
    return items


BAUD = G.BAUD_KEY[1]
NMEA_OUT = G.NMEA_KEY[1]
QZSS_ENA = 0x10310024                   # CFG-SIGNAL-QZSS_ENA（u-blox の鍵＝写しでなく直に書く）
GARBLED = b"\xfe"                       # 速度が合わないときに PC が受ける化けたバイト


class _Receiver:
    """受信機の振る舞いの写し。NMEA を流し続け、MON-VER と CFG-VALSET に応える。
    `answer` は出力の VALSET への返事（"ack"・"nak"・None＝黙る）。速度の VALSET には
    返事が読めない（実機と同じ）＝黙って速度を替える（`follows_speed=False` なら替えない）。
    PC 側の速度（`baudrate`）が受信機の速度（`rate`）と違うと、書かれた枠は届かず、
    読み出しは化けたバイトになる。1 回の読み出しで時計が 0.1 秒進む。
    生データを出したまま 38400 bps 以下にいると線が埋まり、MON-VER の返事を落とす
    （2026-10-17 の屋外＝RAWX 1 回が 38400 bps の 1 秒分とほぼ同じ・B-349）。
    `loses_first_reply` なら、返事のある問い（MON-VER・出力の VALSET）の返事を、同じ問い・
    同じ速度の初回だけ落とす（2026-10-17 の X13＝NMEA ありの線で 15 回に 2 回・B-350）。"""

    def __init__(self, *, answer: str | None = "ack", after: list[bytes] | None = None,
                 rate: int = 38400, follows_speed: bool = True, loses_first_reply: bool = False):
        self.answer = answer
        self.loses_first_reply = loses_first_reply
        self.heard: set[tuple[bytes, int]] = set()
        self.after = list(after or [])      # ACK の後、読み出し 1 回に 1 つずつ流す
        self.rate = rate
        self.follows_speed = follows_speed
        self.nmea = True
        self.baudrate = 38400               # PC 側（pyserial の Serial と同じ名前）
        self.t = 0.0
        self.buffer = bytearray(NMEA)
        self.sent = bytearray()             # PC が受けたバイトの全部（生ログと比べる）
        self.written: list[bytes] = []
        self.configured = False
        self.outputs = False                # RAWX などの生データを出しているか

    def ticks(self) -> float:
        return self.t

    @property
    def in_waiting(self) -> int:
        return len(self.buffer) if self.baudrate == self.rate else 1

    def read(self, size: int) -> bytes:
        self.t += 0.1
        if self.baudrate != self.rate:
            self.sent += GARBLED
            return GARBLED
        if not self.buffer:
            if self.configured and self.after:
                self.buffer += self.after.pop(0)
            elif self.nmea:
                self.buffer += NMEA
        # わざと半端な長さで切る＝枠が読み出しの区切りをまたぐ
        out = bytes(self.buffer[:min(size, 37)])
        del self.buffer[:len(out)]
        self.sent += out
        return out

    def write(self, data: bytes) -> int:
        self.written.append(data)
        if self.baudrate != self.rate:
            return len(data)
        kind = (data[2], data[3])
        asked = (bytes(data), self.rate)
        lose = (self.loses_first_reply and asked not in self.heard
                and not (kind == G.CFG_VALSET and BAUD in _valset_items(data)))
        self.heard.add(asked)
        before = len(self.buffer)
        if kind == G.CFG_VALSET:
            items = _valset_items(data)
            outputs = [items[k] for _n, k, _v in G.OUTPUTS if k in items]
            if outputs:
                self.outputs = any(outputs)
        if kind == G.MON_VER:
            if not (self.outputs and self.rate <= 38400):
                self.buffer += G.frame(G.MON_VER, _mon_ver())
        elif kind == G.CFG_VALSET and BAUD in _valset_items(data):
            items = _valset_items(data)
            if self.follows_speed:
                self.rate = items[BAUD]
            self.nmea = bool(items[NMEA_OUT])
            self.buffer.clear()             # 速度の切り替えで途中の枠は化ける
        elif kind == G.CFG_VALSET and self.answer is not None:
            reply = G.ACK_ACK if self.answer == "ack" else G.ACK_NAK
            self.buffer += G.frame(reply, bytes(G.CFG_VALSET))
            self.configured = self.answer == "ack"
        if lose:
            del self.buffer[before:]            # 受信機は処理したが、返事が PC に届かない
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


def test_valset_writes_the_ram_layer_only_and_refuses_what_does_not_fit():
    data = G.valset((("CFG-MSGOUT-UBX_RXM_RAWX_USB", 0x209102A7, 1),))
    assert data[2:4] == bytes(G.CFG_VALSET)
    assert data[6:10] == bytes((0, G.LAYER_RAM, 0, 0))
    assert data[10:15] == struct.pack("<IB", 0x209102A7, 1)
    # 値の幅はキーが決める（真偽 1・1 バイト・2 バイト・4 バイト）
    data = G.valset(G.port_items(115200, 0) + (("CFG-RATE-MEAS", 0x30210001, 1000),))
    assert data[10:-2] == (struct.pack("<II", BAUD, 115200) + struct.pack("<IB", NMEA_OUT, 0)
                               + struct.pack("<IH", 0x30210001, 1000))
    for name, key, value in (("CFG-RATE-MEAS", 0x30210001, 70000),     # 2 バイトに入らない
                             ("CFG-UART1OUTPROT-NMEA", NMEA_OUT, 2),    # 真偽でない
                             ("CFG-NAVSPG-USRDAT_MAJA", 0x50110063, 1)):  # 8 バイトの値
        with pytest.raises(G.GnssError, match="書けないキーか値"):
            G.valset(((name, key, value),))


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


def test_start_asks_configures_speeds_up_and_then_writes_the_header(tmp_path):
    receiver = _Receiver(after=[_rawx(1.0), _pvt()])
    logger = _logger(tmp_path, receiver)
    directory = logger.start()
    # 問い合わせ → 出力の設定 → 速度と NMEA の設定 → 新しい速度で問い合わせ。設定は RAM 層だけ
    assert [(w[2], w[3]) for w in receiver.written] == [G.MON_VER, G.CFG_VALSET, G.CFG_VALSET,
                                                         G.MON_VER]
    assert receiver.written[1][7] == receiver.written[2][7] == G.LAYER_RAM
    # 出荷時に切れている QZSS を出力と同じ VALSET で入れる（B-351）
    assert _valset_items(receiver.written[1]) == {
        **{k: v for _n, k, v in G.OUTPUTS}, QZSS_ENA: 1}
    assert _valset_items(receiver.written[2]) == {BAUD: 115200, NMEA_OUT: 0}
    assert receiver.baudrate == receiver.rate == 115200 and not receiver.nmea
    header = json.loads((directory / G.HEADER_FILE).read_text(encoding="utf-8"))
    assert directory.name == header["log_id"] == "gnss-20261016T050000Z"
    assert header["software_commit"] == "abc123"
    assert (header["port"], header["baud"], header["baud_at_start"]) == ("COM5", 115200, 38400)
    assert header["receiver"]["firmware"] == "HPG 2.11"
    assert "MOD=ZED-X20P" in header["receiver"]["extensions"]
    assert header["config"]["layers"] == "RAM"
    assert [(i["key"], i["value"]) for i in header["config"]["items"]] == \
        [(f"{k:#010x}", v) for _n, k, v in G.OUTPUTS + G.SIGNALS + G.port_items(115200, 0)]


def test_a_receiver_left_at_the_logging_speed_is_still_found(tmp_path):
    # 前回の記録が書き戻せずに終わった＝受信機は 115200 のまま
    receiver = _Receiver(after=[_rawx(1.0)], rate=115200)
    logger = _logger(tmp_path, receiver)
    directory = logger.start()
    assert logger.baud_at_start == 115200
    header = json.loads((directory / G.HEADER_FILE).read_text(encoding="utf-8"))
    assert (header["baud"], header["baud_at_start"]) == (115200, 115200)
    _drain(logger, receiver)
    assert logger.counts["rawx"] == 1


def test_no_folder_is_made_when_the_receiver_does_not_follow_the_speed(tmp_path):
    receiver = _Receiver(follows_speed=False)
    with pytest.raises(G.GnssError, match="115200 bps に切り替えた後、受信機が応えません"):
        _logger(tmp_path, receiver).start()
    assert list(tmp_path.iterdir()) == []


def test_stop_puts_the_speed_and_nmea_back_and_logs_until_then(tmp_path):
    receiver = _Receiver(after=[_rawx(1.0), _rawx(2.0)])
    logger = _logger(tmp_path, receiver)
    _logger(tmp_path / "unstarted", _Receiver()).stop()        # 始める前なら何もしない
    directory = logger.start()
    _drain(logger, receiver)
    logger.stop()
    assert _valset_items(receiver.written[-1]) == {
        **{k: 0 for _n, k, _v in G.OUTPUTS}, QZSS_ENA: 0, BAUD: 38400, NMEA_OUT: 1}
    assert receiver.written[-1][7] == G.LAYER_RAM
    assert receiver.baudrate == receiver.rate == 38400 and receiver.nmea
    assert not receiver.outputs
    chunks = list(read_raw(directory, raw_file=G.RAW_FILE, index_file=G.INDEX_FILE))
    # 戻す設定を送った後の待ちで受けた分（化けたバイト）まで生ログに残る
    assert b"".join(c for _t, c in chunks) == bytes(receiver.sent)
    assert bytes(receiver.sent).endswith(GARBLED)
    assert logger.counts["rawx"] == 2


def test_a_second_log_starts_after_stop_without_power_cycling(tmp_path):
    # B-349＝止めても生データを出し続けると、38400 bps が埋まって次の問い合わせが落ちた
    receiver = _Receiver(after=[_rawx(1.0)])
    first = _logger(tmp_path / "first", receiver)
    first.start()
    _drain(first, receiver)
    first.stop()
    receiver.after = [_rawx(2.0)]
    second = _logger(tmp_path / "second", receiver)
    second.start()
    assert second.baud_at_start == 38400
    _drain(second, receiver)
    assert second.counts["rawx"] == 1


def test_a_lost_reply_is_asked_again_at_every_step(tmp_path):
    # B-350＝1 回の問いの返事が落ちると、受信機は正常なのに「MON-VER に応えません」で止まった
    receiver = _Receiver(after=[_rawx(1.0)], loses_first_reply=True)
    logger = _logger(tmp_path, receiver)
    directory = logger.start()
    # 始めの MON-VER・出力の VALSET・速度を上げた後の MON-VER を、どれも 2 回目で通す
    assert [(w[2], w[3]) for w in receiver.written] == [
        G.MON_VER, G.MON_VER, G.CFG_VALSET, G.CFG_VALSET, G.CFG_VALSET, G.MON_VER, G.MON_VER]
    assert receiver.written[0] == receiver.written[1] and receiver.written[2] == receiver.written[3]
    assert logger.baud_at_start == 38400                # 記録の速度へ探しに行っていない
    _drain(logger, receiver)
    chunks = list(read_raw(directory, raw_file=G.RAW_FILE, index_file=G.INDEX_FILE))
    assert b"".join(c for _t, c in chunks) == bytes(receiver.sent)
    assert logger.counts["rawx"] == 1


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
    asked: list[tuple[tuple[int, int], int, float]] = []
    receiver.write = lambda data: asked.append(((data[2], data[3]), receiver.baudrate,
                                                receiver.t)) or len(data)   # 何も返さない
    with pytest.raises(G.GnssError, match="MON-VER に応えません（38400・115200 bps"):
        _logger(tmp_path, receiver).start()
    # 始めの速度と記録の速度で、待ちの上限まで送り直して問い、設定は書かない
    assert {kind for kind, _b, _t in asked} == {G.MON_VER}
    bauds = [b for _k, b, _t in asked]
    assert bauds == sorted(bauds)                       # 始めの速度で問い終えてから次へ
    assert min(bauds.count(38400), bauds.count(115200)) >= int(G.REPLY_WAIT_S / G.RESEND_S)
    # 送り直しの間隔は 1 秒の倍数にしない（毎秒の NMEA の塊と同じ位相に当て続けない）
    gaps = {round(b[2] - a[2], 1) for a, b in zip(asked, asked[1:]) if a[1] == b[1]}
    assert gaps and all(g % 1.0 for g in gaps)
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


@pytest.mark.parametrize("rec_stat_at_fix", [0x03, 0x01])
def test_the_moment_the_receiver_time_is_fixed_is_not_a_gap(tmp_path, rec_stat_at_fix):
    """B-346: 電源投入後は week=0・rcvTow＝起動からの秒。確定した回に週 0→2440 へ飛ぶ
    （X13 の実機では recStat の clkReset が立った。立たなくても週 0 から抜けたら数えない）。
    確定の後の欠けは今まで通り数える。"""
    receiver = _Receiver(after=[_rawx(79.0, week=0, rec_stat=0x00), _rawx(80.0, week=0, rec_stat=0x00),
                                _rawx(421888.99, rec_stat=rec_stat_at_fix), _rawx(421889.99),
                                _rawx(421891.99)])
    logger = _logger(tmp_path, receiver)
    logger.start()
    _drain(logger, receiver)
    assert (logger.counts["rawx"], logger.rawx_gaps) == (5, 1)    # 421890.99 だけ


def test_a_clock_reset_is_not_a_gap(tmp_path):
    """時刻の確定後でも、受信機が時計を合わせ直した回の飛びは欠けにしない。"""
    receiver = _Receiver(after=[_rawx(10.0), _rawx(11.0), _rawx(25.0, rec_stat=0x03), _rawx(26.0)])
    logger = _logger(tmp_path, receiver)
    logger.start()
    _drain(logger, receiver)
    assert (logger.counts["rawx"], logger.rawx_gaps) == (4, 0)


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


def test_the_solution_tally_times_the_first_fix_from_when_rtcm_was_used(tmp_path):
    receiver = _Receiver(after=[
        _pvt(),                                             # 時刻の確定前（数えるが、秒は測らない）
        _timed_pvt(10),                                     # SBAS などの補正で単独
        _timed_pvt(11, carrier=1, age=2),                   # RTCM を使い始めた
        _timed_pvt(12, diff=False),                         # 補正が抜けた
        _timed_pvt(16, carrier=2, age=1),
        _timed_pvt(17, carrier=2, age=1),
        _timed_pvt(18, fix_type=2),                         # 2D＝高さが仮定の値
    ])
    logger = _logger(tmp_path, receiver)
    logger.start()
    _drain(logger, receiver)
    assert logger.states == {"fix": 2, "float": 1, "single": 3, "none": 1}
    assert logger.solutions() == ("解 Fix 2・Float 1・単独 3（うち補正あり 1）・なし 1"
                                  "・RTCM を使い始めてから最初の Fix まで 5 秒")


def test_the_solution_tally_says_when_rtcm_never_reached_a_fix(tmp_path):
    receiver = _Receiver(after=[_timed_pvt(10, carrier=1, age=3), _timed_pvt(11)])
    logger = _logger(tmp_path, receiver)
    logger.start()
    _drain(logger, receiver)
    assert logger.solutions().endswith("・RTCM を使い始めてから Fix に届いていません")
    plain = _logger(tmp_path / "plain", _Receiver(after=[_timed_pvt(10)]))
    assert "RTCM" not in plain.solutions()                  # RTCM を使っていなければ秒は出さない


# --- 受信機の解の時系列（gnss attach） ---------------------------------------------


def test_nav_pvt_time_and_correction_marks_are_read():
    pvt = G.parse_nav_pvt(_timed_pvt(13, carrier=2, age=2, nano=-500_000)[6:-2])
    assert pvt.utc == datetime(2026, 10, 16, 5, 0, 12, 999500, tzinfo=timezone.utc)
    assert (pvt.diff_soln, pvt.corr_age_code, pvt.invalid_llh) == (True, 2, False)
    assert (round(pvt.lat_deg, 7), round(pvt.lon_deg, 7), pvt.height_m) == \
        (35.4312407, 139.9447071, 36.793)
    # 日付・時刻・その確定のどれかが欠ける回と、閏秒の回は時刻なし
    for valid in (0x03, 0x05, 0x06):
        assert G.parse_nav_pvt(_timed_pvt(13, valid=valid)[6:-2]).utc is None
    assert G.parse_nav_pvt(_timed_pvt(60)[6:-2]).utc is None
    assert G.parse_nav_pvt(_timed_pvt(13, llh_invalid=True)[6:-2]).invalid_llh


@pytest.mark.parametrize("kwargs, state", [
    (dict(carrier=2), "fix"),
    (dict(carrier=1), "float"),
    (dict(), "single"),
    (dict(diff=False), "single"),
    (dict(carrier=2, ok=False), "none"),
    (dict(carrier=2, llh_invalid=True), "none"),
    (dict(fix_type=2), "none"),
    (dict(fix_type=5), "none"),
    (dict(fix_type=4, carrier=1), "float"),
], ids=["RTK Fix", "RTK Float", "単独・補正あり", "単独・補正なし", "受信機が無効とした",
        "緯度経度が無効", "2D", "時刻だけ", "3D＋推測航法"])
def test_the_solution_state_follows_the_receiver_flags(kwargs, state):
    assert G.solution_state(G.parse_nav_pvt(_timed_pvt(13, **kwargs)[6:-2])) == state
    assert state in S.FIX_STATES


def _session(root: Path, started: str, stop: str | None) -> Path:
    calibration = S.Calibration(
        measured_on="2026-10-01", offset_db=-96.0, scale_db_per_count=0.5,
        reference_unit_id="ref-01", reference_measured_on="2026-09-10")
    radio = S.RadioSettings(
        config_id=1, firmware_version="5aab362", channel=6, air_sf=7, air_bw_khz=500,
        tx_power_cdbm=1300, tx_interval_ms=250, detector="mean", integration_window_us=1000, integration_samples=10,
        antenna="external", sensitivity_dbm=-98.0)

    def endpoint(role: str) -> S.Endpoint:
        return S.Endpoint(
            role=role, measurement_config_id=f"{role}-e220", device_id="aa:bb:cc:dd:ee:01",
            feeder_loss_db=1.5, antenna_gain_dbi=2.0,
            calibration=calibration if role == "rx" else replace(
                calibration, tx_output_dbm=12.4, tx_output_power_cdbm=1300, tx_output_channel=6),
            radio=radio if role == "rx" else replace(radio, config_id=2, sensitivity_dbm=None),
            position=S.Position(lat=35.43, lon=139.94, elevation_m=1.9, height_agl_m=1.0,
                                height_source="survey"))

    directory = S.create_session(root, S.SessionHeader(
        session_id="20261016T0500Z-gnss", started_utc=started, target_kind="existing_link",
        env_class="rural", meas_method="e220-920",
        provenance=S.Provenance(software_commit="c896ced", geoid_model="GSIGEO2024",
                                censor_min_receive_rate=0.9, censor_window_s=10.0),
        tx=endpoint("tx"), rx=endpoint("rx")))
    S.append_event(directory, S.Event(started, "place", 0))
    if stop is not None:
        S.append_event(directory, S.Event(stop, "stop"))
    return directory


def _gnss_log(tmp_path: Path) -> tuple[Path, list[tuple[str, G.PvtStatus]]]:
    receiver = _Receiver(after=[_pvt(), _timed_pvt(10), _timed_pvt(11, carrier=1, age=2),
                                _timed_pvt(12, carrier=2, age=1), _timed_pvt(13, carrier=2, age=1)])
    logger = _logger(tmp_path / "gnss", receiver)
    directory = logger.start()
    _drain(logger, receiver)
    received = list(G.read_pvt(directory))
    assert len(received) == 5                       # 生ログを読み直すと、受けた NAV-PVT が全部ある
    return directory, received


def test_attach_adds_the_solutions_within_the_session_to_its_gnss_csv(tmp_path):
    log, received = _gnss_log(tmp_path)
    # セッションは時刻の確定前の回に始まり、4 回目の解を受けた時に終わった（5 回目は期間の外）
    session = _session(tmp_path / "sessions", received[0][0], received[3][0])

    added = G.attach_to_session(log, session, "rx")

    assert added.states == {"fix": 1, "float": 1, "single": 1, "none": 0}
    assert (added.rows, added.untimed) == (3, 1)
    fixes = list(S.read_position_fixes(session))
    assert [f.fix_state for f in fixes] == ["single", "float", "fix"]
    assert [f.pc_utc for f in fixes] == [t for t, _p in received[1:4]]
    assert fixes[2] == S.PositionFix(
        utc="2026-10-16T05:00:12.000000Z", pc_utc=received[3][0], endpoint="rx",
        lat=35.4312407, lon=139.9447071, ellipsoid_h_m=36.793, fix_state="fix", h_acc_m=0.014,
        v_acc_m=0.02, num_sv=31, diff_soln=1, corr_age_code=1, log_id=log.name)
    assert S.read_header(session).schema_version == S.SCHEMA_VERSION

    with pytest.raises(G.GnssError, match="既に rx の時系列として足してあります"):
        G.attach_to_session(log, session, "rx")
    assert len(list(S.read_position_fixes(session))) == 3      # 何も重ねていない


def test_attach_needs_the_end_of_the_session_and_an_overlap(tmp_path):
    log, received = _gnss_log(tmp_path)
    crashed = _session(tmp_path / "crashed", received[0][0], None)
    with pytest.raises(G.GnssError, match="--recover"):
        G.attach_to_session(log, crashed, "tx")
    assert G.attach_to_session(log, crashed, "tx", until=received[-1][0]).rows == 4

    later = _session(tmp_path / "later", "2026-10-16T06:00:00Z", "2026-10-16T06:10:00Z")
    with pytest.raises(G.GnssError, match="時刻の確定した解がありません"):
        G.attach_to_session(log, later, "rx")
    with pytest.raises(G.GnssError, match="端点が語彙の外"):
        G.attach_to_session(log, later, "RX")
    assert list(S.read_position_fixes(later)) == []


def test_the_gnss_attach_command_reports_what_it_added(tmp_path, capsys):
    log, received = _gnss_log(tmp_path)
    session = _session(tmp_path / "sessions", received[0][0], None)
    assert cli.main(["gnss", "attach", str(log), str(session), "--endpoint", "rx"]) == 2
    assert "--recover" in capsys.readouterr().err

    # 無線のセッションの生ログ（uart.bin）を最後に読めた時刻で終える
    S.append_raw(session, b"\x00", received[2][0])
    assert cli.main(["gnss", "attach", str(log), str(session), "--endpoint", "rx",
                     "--recover"]) == 0
    out = capsys.readouterr().out
    assert "rx の解を 2 行足しました（Fix 0・Float 1・単独 1・なし 0）" in out
    assert "時刻が確定していなかった 1 回" in out


def test_the_gnss_log_command_is_on_the_cli():
    with pytest.raises(SystemExit) as e:
        cli.main(["gnss", "log", "--help"])
    assert e.value.code == 0


class _FakeKernel32:
    def __init__(self, previous: int) -> None:
        self.previous = previous
        self.calls: list[int] = []

    def SetThreadExecutionState(self, flags: int) -> int:  # noqa: N802 — Win32 の名前
        self.calls.append(flags)
        return self.previous


def test_recording_keeps_windows_and_the_screen_awake_and_lets_go_even_on_error():
    k = _FakeKernel32(previous=0x80000000)
    with pytest.raises(RuntimeError):
        with cli._KeepAwake(k) as awake:
            assert awake.held
            assert k.calls == [0x80000003]  # 続けて・システム・画面
            raise RuntimeError("記録の途中で落ちた")
    assert k.calls == [0x80000003, 0x80000000]  # 元へ戻した
    assert not awake.held


def test_a_refused_keep_awake_is_reported_as_not_held():
    k = _FakeKernel32(previous=0)
    with cli._KeepAwake(k) as awake:
        assert not awake.held


# --- 野外で SSH 越しに読む道具（tools/field_remote/gnss_peek.py） ----------------------


def _gnss_peek():
    path = Path(__file__).resolve().parents[1] / "tools" / "field_remote" / "gnss_peek.py"
    spec = importlib.util.spec_from_file_location("gnss_peek", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_remote_peek_counts_a_recorded_log_as_the_logger_did(tmp_path):
    receiver = _Receiver(after=[_rawx(100.0), _pvt(), _rawx(101.0), _rawx(103.0)])
    logger = _logger(tmp_path, receiver)
    logger.start()
    _drain(logger, receiver)
    assert logger.rawx_gaps == 1
    now = datetime(2026, 10, 16, 6, 0, tzinfo=timezone.utc)

    lines = _gnss_peek().peek(logger.directory, now)

    assert lines[0] == logger.directory.name
    assert lines[1].startswith("最後の受信から ")
    # 経過時間（先頭の欄）だけは数え方が違う＝そこから後ろが記録器の画面と同じ
    assert lines[2].split("  ", 1)[1] == logger.status_line(0).split("  ", 1)[1]
    assert lines[3] == logger.summary()
    assert lines[4] == logger.solutions()

    # 書きかけの索引の末尾の行があっても、そこまでの分で出す
    with (logger.directory / G.INDEX_FILE).open("a", encoding="utf-8") as f:
        f.write("2026-10-16T05:0")
    assert _gnss_peek().peek(logger.directory, now)[3] == logger.summary()


# --- 切り離した起動（tools/field_remote/timed_log.py） ----------------------------------


def _timed_log():
    path = Path(__file__).resolve().parents[1] / "tools" / "field_remote" / "timed_log.py"
    spec = importlib.util.spec_from_file_location("timed_log", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SignalledReceiver(_Receiver):
    """記録が始まって `reads` 回読まれたら、止めの合図のファイルを置く（人が SSH から置く代わり）。"""

    def __init__(self, stop_file: Path, reads: int) -> None:
        super().__init__(after=[_rawx(100.0 + i) for i in range(5)])
        self.stop_file = stop_file
        self.reads = reads

    def read(self, size: int) -> bytes:
        if self.outputs:
            self.reads -= 1
            if self.reads == 0:
                self.stop_file.touch()
        return super().read(size)


def test_the_stop_file_ends_a_detached_recording_the_same_way_as_q(tmp_path, monkeypatch, capsys):
    stop_file = tmp_path / "timed_log.stop"
    stop_file.touch()                       # 前の回の合図の残り＝消してから始める
    receiver = _SignalledReceiver(stop_file, reads=30)

    class _Serial:                          # pyserial の代わり＝偽の受信機を開く
        SerialException = OSError

        @staticmethod
        def Serial(port: str, baud: int, timeout: float):  # noqa: N802 — pyserial の名前
            return contextlib.nullcontext(receiver)

    monkeypatch.setattr(cli, "_import_serial", lambda: _Serial)
    monkeypatch.setattr(cli, "_Keyboard", cli._Keyboard)   # timed_log が差し替える＝後で戻す
    keep_awake = cli._KeepAwake
    monkeypatch.setattr(cli, "_KeepAwake", lambda: keep_awake(_FakeKernel32(previous=0)))
    monkeypatch.setattr(G, "SETTLE_S", 0.0)

    assert _timed_log().main(["3600", "--port", "COM6", "--root", str(tmp_path / "gnss")],
                             stop_file=stop_file) == 0

    out = capsys.readouterr().out
    assert "前の回の止めの合図が残っていたので消しました" in out
    assert "止めの合図" in out and "を受けました" in out
    assert not stop_file.exists()           # 合図は読んだら消す＝次の回を止めない
    # q と同じ道で終わった＝受信機は始めの速度・NMEA あり・生データなしへ戻った（B-349 の形でない）
    assert (receiver.rate, receiver.nmea, receiver.outputs) == (38400, True, False)
    assert "RAWX" in out
