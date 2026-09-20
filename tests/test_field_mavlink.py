"""
tests/test_field_mavlink.py
============================
実測補助（`apps/field`）の **MAVLink の読み取り**を検証する（増分3）。

🔑 **ここで守っているのは「読めたのに値が違う」を起こさないこと。** MAVLink の
仕様を外しても、例外は出ずに数字だけが静かにずれる:

  - ワイヤ上の並びは XML の並びではない（型のサイズの降順）
  - CRC_EXTRA は定義の食い違いを検出する仕掛けそのもの
  - v2 は payload 末尾の 0 を切り捨てて送る（受け側でゼロ埋め戻し）
  - フレームの `link_seq` は Field の `seq` ではない（8 ビットで一周する）

⚠️ **フレームは、この検査の中で仕様から組み立てる**＝製品側の並べ替えの結果を
使ってフレームを作ると、並べ替えが間違っていても検査は通る（同じ間違いで作って
同じ間違いで読むだけ）。期待する並びは**このファイルに手で書き下す**。
"""

from __future__ import annotations

import struct
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from apps.field import aggregate as A
from apps.field import session as S
from apps.field.mavlink import dialect as D
from apps.field.mavlink import reader as R


@pytest.fixture(scope="module")
def dialect() -> D.Dialect:
    return D.load_dialect()


# --- 仕様から書き下した期待値 -------------------------------------------------
#
# ワイヤ上の並び＝**基本型のサイズの降順、同サイズは XML の順**。XML の並びを
# 写したものではないことに注意（RX_SAMPLE は XML では rx_id が先頭）。
_WIRE_ORDER = {
    "RADIOSIM_FIELD_TX_PACKET": ("tx_time_us", "seq", "config_id", "tx_id"),
    "RADIOSIM_FIELD_RX_SAMPLE": (
        "rx_time_us", "seq", "sample_seq", "rssi_raw", "noise_floor_raw", "config_id",
        "tx_config_id", "rx_id", "tx_id",
    ),
    "RADIOSIM_FIELD_CONFIG": (
        "integration_window_us", "config_id", "rate_kbps", "tx_power_cdbm",
        "tx_interval_ms", "integration_samples", "role", "device_id",
        "firmware_version", "channel", "detector", "antenna",
    ),
}

# CRC_EXTRA の材料＝「メッセージ名 」＋（並べ替え後の）「型 」「名前 」、配列なら
# 要素数の 1 バイト。**ここも手で書き下す**（製品側の並べ替えを借りない）。
_CRC_SPEC = {
    "RADIOSIM_FIELD_TX_PACKET": [
        ("uint64_t", "tx_time_us", 0), ("uint32_t", "seq", 0),
        ("uint16_t", "config_id", 0), ("uint8_t", "tx_id", 6),
    ],
    "RADIOSIM_FIELD_RX_SAMPLE": [
        ("uint64_t", "rx_time_us", 0), ("uint32_t", "seq", 0),
        ("uint32_t", "sample_seq", 0),
        ("int16_t", "rssi_raw", 0), ("int16_t", "noise_floor_raw", 0),
        ("uint16_t", "config_id", 0), ("uint16_t", "tx_config_id", 0),
        ("uint8_t", "rx_id", 6), ("uint8_t", "tx_id", 6),
    ],
    "RADIOSIM_FIELD_CONFIG": [
        ("uint32_t", "integration_window_us", 0), ("uint16_t", "config_id", 0),
        ("uint16_t", "rate_kbps", 0), ("int16_t", "tx_power_cdbm", 0),
        ("uint16_t", "tx_interval_ms", 0), ("uint16_t", "integration_samples", 0),
        ("uint8_t", "role", 0), ("uint8_t", "device_id", 6),
        ("char", "firmware_version", 16), ("uint8_t", "channel", 0),
        ("uint8_t", "detector", 0), ("uint8_t", "antenna", 0),
    ],
}


def _expected_crc_extra(name: str) -> int:
    crc = D.x25_crc(f"{name} ".encode("ascii"))
    for type_name, field_name, count in _CRC_SPEC[name]:
        crc = D.x25_crc(f"{type_name} ".encode("ascii"), crc)
        crc = D.x25_crc(f"{field_name} ".encode("ascii"), crc)
        if count:
            crc = D.x25_crc(bytes([count]), crc)
    return (crc & 0xFF) ^ (crc >> 8)


# --- フレームの組み立て（仕様どおり手で） -------------------------------------


def _frame(
    msgid: int,
    payload: bytes,
    crc_extra: int,
    *,
    link_seq: int = 0,
    sysid: int = 1,
    compid: int = 1,
    incompat: int = 0,
    signature: bytes = b"",
) -> bytes:
    body = (
        bytes([len(payload), incompat, 0, link_seq, sysid, compid])
        + msgid.to_bytes(3, "little")
        + payload
    )
    crc = D.x25_crc(body)
    crc = D.x25_crc(bytes([crc_extra]), crc)
    return bytes([0xFD]) + body + crc.to_bytes(2, "little") + signature


def _rx_payload(
    *,
    rx_time_us: int = 1_234_567,
    seq: int = 42,
    rssi_raw: int = -71,
    noise_floor_raw: int = -95,
    config_id: int = 1,
    tx_config_id: int = 2,
    sample_seq: int = 9,
    rx_id: bytes = b"\xaa\xbb\xcc\xdd\xee\x02",
    tx_id: bytes = b"\xaa\xbb\xcc\xdd\xee\x01",
) -> bytes:
    return (
        struct.pack(
            "<QIIhhHH", rx_time_us, seq, sample_seq, rssi_raw, noise_floor_raw,
            config_id, tx_config_id,
        )
        + rx_id
        + tx_id
    )


def _config_payload(
    *,
    integration_window_us: int = 1000,
    config_id: int = 1,
    rate_kbps: int = 1000,
    tx_power_cdbm: int = 1300,
    tx_interval_ms: int = 100,
    integration_samples: int = 10,
    role: int = 1,
    device_id: bytes = b"\xaa\xbb\xcc\xdd\xee\x02",
    firmware_version: bytes = b"5aab362",
    channel: int = 6,
    detector: int = 2,
    antenna: int = 2,
) -> bytes:
    return (
        struct.pack(
            "<IHHhHH",
            integration_window_us, config_id, rate_kbps, tx_power_cdbm,
            tx_interval_ms, integration_samples,
        )
        + bytes([role])
        + device_id
        + firmware_version.ljust(16, b"\x00")
        + bytes([channel, detector, antenna])
    )


def _rx_frame(dialect: D.Dialect, **kwargs) -> bytes:
    link_seq = kwargs.pop("link_seq", 0)
    definition = dialect.messages_by_name["RADIOSIM_FIELD_RX_SAMPLE"]
    return _frame(
        definition.msgid, _rx_payload(**kwargs), definition.crc_extra, link_seq=link_seq
    )


# ============================================================
# ①仕様そのもの（CRC・並び・CRC_EXTRA）
# ============================================================
def test_the_crc_matches_the_published_check_value():
    """X.25（CRC-16/MCRF4XX）の公表されている検査値。

    ⚠️ ここが合わないと以下すべてが「自分の実装どうしで一致しているだけ」になる。
    """
    assert D.x25_crc(b"123456789") == 0x6F91


@pytest.mark.parametrize("name", sorted(_WIRE_ORDER))
def test_the_wire_order_is_by_type_size_not_xml_order(dialect, name):
    """並べ替えを外すと、全フィールドが隣の値を拾ったまま**例外なく**復号される。"""
    actual = tuple(f.name for f in dialect.messages_by_name[name].fields)
    assert actual == _WIRE_ORDER[name]


def test_the_wire_order_really_differs_from_the_xml_order(dialect):
    """上の検査が「XML の順を写しただけ」で通っていないこと。

    ⚠️ [[feedback-promote-recurring-checks]] の壊れ方①＝両者が同じ並びなら、
    並べ替えを丸ごと消しても緑のままになる。
    """
    xml_order = ("rx_id", "tx_id", "seq", "rx_time_us", "rssi_raw",
                 "noise_floor_raw", "config_id", "tx_config_id", "sample_seq")
    assert _WIRE_ORDER["RADIOSIM_FIELD_RX_SAMPLE"] != xml_order


@pytest.mark.parametrize("name", sorted(_CRC_SPEC))
def test_the_crc_extra_is_built_from_the_definition(dialect, name):
    """CRC_EXTRA が仕様どおりに作られていること（定義の食い違いを検出する仕掛け）。"""
    assert dialect.messages_by_name[name].crc_extra == _expected_crc_extra(name)


def test_the_payload_size_matches_the_fields(dialect):
    assert dialect.messages_by_name["RADIOSIM_FIELD_RX_SAMPLE"].payload_size == 36
    assert dialect.messages_by_name["RADIOSIM_FIELD_TX_PACKET"].payload_size == 20
    assert dialect.messages_by_name["RADIOSIM_FIELD_CONFIG"].payload_size == 40


# ============================================================
# ②フレームの読み取り
# ============================================================
def test_a_frame_decodes_to_the_values_that_were_packed(dialect):
    messages, stats = R.read_log(_rx_frame(dialect))
    assert stats.frames == 1 and stats.crc_errors == 0
    f = messages[0].fields
    assert messages[0].name == "RADIOSIM_FIELD_RX_SAMPLE"
    assert f["rx_time_us"] == 1_234_567
    assert f["seq"] == 42
    assert f["rssi_raw"] == -71          # 符号つき＝正の大きな値として読まないこと
    assert f["noise_floor_raw"] == -95
    assert f["config_id"] == 1
    assert f["tx_config_id"] == 2
    assert f["sample_seq"] == 9
    assert f["tx_id"] == (0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x01)


def test_a_truncated_payload_is_zero_filled(dialect):
    """v2 は payload 末尾の 0 を切り捨てて送る。

    ⚠️ 埋め戻さないと、**末尾のフィールドが 0 のサンプルだけ**が選択的に読めなく
    なる（`struct.error` で落ちるか、短い分だけずれる）。
    """
    definition = dialect.messages_by_name["RADIOSIM_FIELD_RX_SAMPLE"]
    payload = _rx_payload(tx_id=b"\x00" * 6).rstrip(b"\x00")
    assert len(payload) < definition.payload_size
    messages, stats = R.read_log(_frame(definition.msgid, payload, definition.crc_extra))
    assert stats.frames == 1
    assert messages[0].fields["rssi_raw"] == -71
    assert messages[0].fields["tx_id"] == (0, 0, 0, 0, 0, 0)


def test_a_longer_payload_is_read_as_far_as_it_is_defined(dialect):
    """ファームが後からフィールドを足しても、古い PC 側が読めること（前方互換）。"""
    definition = dialect.messages_by_name["RADIOSIM_FIELD_RX_SAMPLE"]
    payload = _rx_payload() + b"\x01\x02\x03\x04"
    messages, stats = R.read_log(_frame(definition.msgid, payload, definition.crc_extra))
    assert stats.frames == 1
    assert messages[0].fields["seq"] == 42


def test_a_corrupted_frame_yields_no_message(dialect):
    """化けた 1 バイトは RSSI を数十 dB 動かす。**読めたことにしない。**"""
    data = bytearray(_rx_frame(dialect))
    data[R._HEADER_LEN] ^= 0xFF                      # payload の先頭を化けさせる
    messages, stats = R.read_log(bytes(data))
    assert messages == []
    assert stats.crc_errors >= 1
    assert stats.frames == 0


def test_noise_before_a_frame_is_skipped_and_the_frame_still_reads(dialect):
    messages, stats = R.read_log(b"\x00\x11\x22" + _rx_frame(dialect))
    assert len(messages) == 1
    assert stats.skipped_bytes == 3
    assert stats.crc_errors == 0


def test_an_unknown_message_is_skipped_whole_not_byte_by_byte(dialect):
    """他のダイアレクトのフレームで **CRC 不一致の数を水増ししない**。

    🔑 その数は「UART が汚れていたか」＝打ち切りを信じてよいかの判断に使うので、
    他機の正常な通信で膨らませてはいけない。⇒ 知らない ID は宣言された長さぶん
    読み飛ばす（payload に 0xFD が入っていても中を読み直さない）。
    """
    unknown = _frame(30, b"\xfd\xfd\xfd\xfd\xfd\xfd\xfd\xfd", 0x00)
    messages, stats = R.read_log(unknown + _rx_frame(dialect))
    assert len(messages) == 1                        # 後続の正しいフレームは読める
    assert stats.unknown_msgid == 1
    assert stats.crc_errors == 0


def test_a_tail_cut_in_the_middle_is_not_counted_as_corruption(dialect):
    """測定中に電源が落ちた形。**線が汚れていることとは別の事実。**"""
    data = _rx_frame(dialect)[:-4]
    messages, stats = R.read_log(data)
    assert messages == []
    assert stats.crc_errors == 0
    assert stats.truncated_tail == len(data)


def test_a_signed_frame_skips_its_signature(dialect):
    """署名つきフレームは 13 バイト長い。飛ばさないと次のフレームを見失う。"""
    definition = dialect.messages_by_name["RADIOSIM_FIELD_RX_SAMPLE"]
    signed = _frame(
        definition.msgid, _rx_payload(seq=7), definition.crc_extra,
        incompat=0x01, signature=bytes(13),
    )
    messages, stats = R.read_log(signed + _rx_frame(dialect, seq=8))
    assert [m.fields["seq"] for m in messages] == [7, 8]
    assert stats.skipped_bytes == 0


def test_the_error_fraction_counts_only_frames_it_checked(dialect):
    data = bytearray(_rx_frame(dialect))
    data[R._HEADER_LEN] ^= 0xFF
    _, stats = R.read_log(bytes(data) + _rx_frame(dialect))
    assert stats.frames == 1
    assert 0.0 < stats.error_fraction <= 1.0


# ============================================================
# ③メッセージ → セッションの型
# ============================================================
def test_the_frame_sequence_is_not_the_payload_sequence(dialect):
    """🔴 **リンクの seq は 8 ビットで一周する**＝打ち切りを数えるのに使うと、
    256 パケットごとに巻き戻って受信率が桁違いに狂う。数えるのは payload の seq。
    """
    message = R.read_log(_rx_frame(dialect, seq=300, link_seq=44))[0][0]
    assert message.link_seq == 44
    sample = R.to_rx_sample(message, pc_utc="2026-09-19T01:00:00Z")
    assert sample.seq == 300


def test_the_pc_adds_only_the_time_and_the_spatial_slot(dialect):
    """生値には触らない／置き場所は PC 側が付ける（ファームは知らない）。"""
    message = R.read_log(_rx_frame(dialect))[0][0]
    sample = R.to_rx_sample(message, pc_utc="2026-09-19T01:00:00Z", spatial_slot=3)
    assert sample.rssi_raw == -71                    # 換算しない
    assert sample.noise_floor_raw == -95
    assert sample.spatial_slot == 3
    assert sample.tx_config_id == 2                  # TX の設定番号（RX のものではない）
    assert sample.sample_seq == 9                    # ファームが振った通し番号
    assert sample.tx_id == "aa:bb:cc:dd:ee:01"
    assert sample.pc_utc == "2026-09-19T01:00:00Z"


def test_another_message_is_not_taken_for_a_sample(dialect):
    definition = dialect.messages_by_name["RADIOSIM_FIELD_CONFIG"]
    message = R.read_log(
        _frame(definition.msgid, _config_payload(), definition.crc_extra)
    )[0][0]
    with pytest.raises(S.SessionError):
        R.to_rx_sample(message, pc_utc="2026-09-19T01:00:00Z")


def test_the_config_message_becomes_radio_settings(dialect):
    definition = dialect.messages_by_name["RADIOSIM_FIELD_CONFIG"]
    message = R.read_log(
        _frame(definition.msgid, _config_payload(), definition.crc_extra)
    )[0][0]
    radio = R.to_radio_settings(message, sensitivity_dbm=-98.0)
    assert radio.config_id == 1
    assert radio.firmware_version == "5aab362"       # 詰め物の NUL を落とす
    assert radio.channel == 6
    assert radio.tx_power_cdbm == 1300
    assert radio.integration_samples == 10           # 時間平均（空間平均とは別）
    assert radio.detector == "mean"                  # enum の語彙は XML から
    assert radio.antenna == "external"
    assert radio.sensitivity_dbm == -98.0            # ファームは感度を知らない


def test_the_enum_labels_come_from_the_xml(dialect):
    """語彙は `session.py` の定数と一致すること（手で写した表を持たない）。"""
    for value in (0, 1, 2):
        assert dialect.label("RADIOSIM_FIELD_DETECTOR", value) in S.DETECTORS
        assert dialect.label("RADIOSIM_FIELD_ANTENNA", value) in S.ANTENNAS
    with pytest.raises(D.DialectError):
        dialect.label("RADIOSIM_FIELD_DETECTOR", 9)          # ファームが新しい＝黙って通さない


# ============================================================
# ④XML が単一の出所であること
# ============================================================
def test_a_duplicate_message_id_is_refused(tmp_path):
    """同じ ID を 2 つが名乗ると、片方を他方として復号する（例外は出ない）。"""
    xml = tmp_path / "dup.xml"
    xml.write_text(
        "<mavlink><messages>"
        '<message id="1" name="A"><field type="uint8_t" name="a">x</field></message>'
        '<message id="1" name="B"><field type="uint8_t" name="b">x</field></message>'
        "</messages></mavlink>",
        encoding="utf-8",
    )
    with pytest.raises(D.DialectError):
        D.load_dialect(str(xml))


def test_extension_fields_are_not_reordered_and_not_in_the_crc(tmp_path):
    """拡張フィールドは末尾のまま・CRC_EXTRA にも入らない。

    🔑 入れてしまうと、ファームがフィールドを足した瞬間に**古い PC 側が全フレームを
    捨てる**（前方互換が壊れ、測定が丸ごと落ちる）。
    """
    base = (
        '<message id="7" name="EXT">'
        '<field type="uint8_t" name="a">x</field>'
        '<field type="uint32_t" name="b">x</field>'
    )
    plain = tmp_path / "plain.xml"
    plain.write_text(f"<mavlink><messages>{base}</message></messages></mavlink>",
                     encoding="utf-8")
    extended = tmp_path / "ext.xml"
    extended.write_text(
        "<mavlink><messages>" + base
        + '<extensions/><field type="uint32_t" name="c">x</field>'
        + "</message></messages></mavlink>",
        encoding="utf-8",
    )
    before = D.load_dialect(str(plain)).messages_by_name["EXT"]
    after = D.load_dialect(str(extended)).messages_by_name["EXT"]
    assert tuple(f.name for f in after.fields) == ("b", "a", "c")   # c は末尾のまま
    assert after.crc_extra == before.crc_extra


def test_an_unknown_type_is_refused(tmp_path):
    xml = tmp_path / "bad.xml"
    xml.write_text(
        '<mavlink><messages><message id="1" name="A">'
        '<field type="quad_t" name="a">x</field>'
        "</message></messages></mavlink>",
        encoding="utf-8",
    )
    with pytest.raises(D.DialectError):
        D.load_dialect(str(xml))


# ============================================================
# ⑤集計まで通す（UART で落ちた分が打ち切りとして現れること）
# ============================================================
def _header() -> S.SessionHeader:
    calibration = S.Calibration(
        measured_on="2026-09-19", offset_db=-96.0, scale_db_per_count=0.5,
        reference_unit_id="ref-01", reference_measured_on="2026-09-10",
    )
    radio = S.RadioSettings(
        config_id=1, firmware_version="5aab362", channel=6, rate_kbps=1000,
        tx_power_cdbm=1300, tx_interval_ms=100, detector="mean",
        integration_window_us=1000, integration_samples=10, antenna="external",
        sensitivity_dbm=-98.0,
    )

    def endpoint(role: str, height: float) -> S.Endpoint:
        return S.Endpoint(
            role=role, measurement_config_id=f"{role}-esp32c6-rod",
            device_id="aa:bb:cc:dd:ee:01", feeder_loss_db=1.5, antenna_gain_dbi=2.0,
            calibration=calibration if role == "rx" else replace(
                calibration, tx_output_dbm=12.4, tx_output_power_cdbm=1300, tx_output_channel=6
            ),
            radio=radio if role == "rx" else replace(radio, config_id=2, sensitivity_dbm=None),
            position=S.Position(
                lat=35.0, lon=139.0, elevation_m=120.0, height_agl_m=height,
                height_source="survey",
            ),
        )

    return S.SessionHeader(
        session_id="20260919T0100Z-uart", started_utc="2026-09-19T01:00:00Z",
        target_kind="existing_link", env_class="rural", meas_method="esp32c6-2g4",
        provenance=S.Provenance(
            software_commit="3c093a2", geoid_model="GSIGEO2011",
            censor_min_receive_rate=0.95, censor_window_s=1.0,
        ),
        tx=endpoint("tx", 45.0), rx=endpoint("rx", 20.0),
    )


def _at(seq_time: float) -> str:
    """番号 seq_time のパケットが届く時刻（送信間隔 100 ms）。"""
    return S.format_utc(
        datetime(2026, 9, 19, 1, 0, tzinfo=timezone.utc) + timedelta(seconds=seq_time * 0.1)
    )


def _windows_of(dialect, log: bytes) -> tuple[list[A.Window], R.ReadStats]:
    messages, stats = R.read_log(log)
    samples = R.rx_samples(messages, lambda m: _at(m.fields["seq"]))
    events = [S.Event(_at(0.5), "place", 0), S.Event(_at(20.5), "stop")]
    return A.aggregate(_header(), samples, events), stats


def test_a_frame_lost_on_the_uart_is_not_counted_as_censoring(dialect):
    """🔑 **UART で落ちた分を、電波で届かなかった分と見分ける**（§6.6 の提案①）。

    ファームは受信した時点で通し番号（`sample_seq`）を振る＝UART で化けて落ちた
    サンプルは、その番号の欠けとして見える。落ち方は受信レベルと関係ないので、
    ウィンドウの受信率の分母から引く（打ち切りにしない）。
    """
    frames = [_rx_frame(dialect, seq=n, sample_seq=n, tx_config_id=2) for n in range(1, 21)]
    broken = bytearray(frames[10])                   # seq 11 が UART で化けた
    broken[R._HEADER_LEN] ^= 0xFF
    frames[10] = bytes(broken)

    windows, stats = _windows_of(dialect, b"".join(frames))
    assert stats.crc_errors >= 1                     # 化けは化けとして数えている
    assert [w.received for w in windows] == [10, 9]  # 期待は 10/ウィンドウ（1 秒 ÷ 100 ms）
    assert [w.link_lost for w in windows] == [0, 1]
    assert windows[1].receive_rate == 1.0            # 分母は 9
    assert not any(w.censored for w in windows)


def test_a_packet_lost_in_the_air_is_still_censoring(dialect):
    """対照＝**電波で**届かなかった分は、今までどおり打ち切りの根拠になる。

    ファームは受からなかったパケットに番号を振らない（振りようがない）ので、
    通し番号は途切れずに続く。⚠️ 上のテストだけだと「欠けは全部 UART のせい」にする
    実装でも通る。
    """
    kept = [n for n in range(1, 21) if n != 11]      # seq 11 は空中で消えた
    log = b"".join(
        _rx_frame(dialect, seq=n, sample_seq=i, tx_config_id=2) for i, n in enumerate(kept)
    )
    windows, stats = _windows_of(dialect, log)
    assert stats.crc_errors == 0
    assert [w.received for w in windows] == [10, 9]
    assert [w.link_lost for w in windows] == [0, 0]
    assert windows[1].censored is True               # 受信率 0.9＜0.95＝値を出さない
    assert A.censored_fraction(windows) == 0.5
