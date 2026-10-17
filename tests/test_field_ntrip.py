"""
tests/test_field_ntrip.py
==========================
実測補助アプリ（`apps/field`）の NTRIP の補正の流し込み（`ntrip.py`・`gnss log --ntrip`）を検証する。

  - caster の指定・問いの文（認証は利用者名があるときだけ）・NAV-PVT から作る GGA
  - RTCM 3 の枠（公表された 1005 の見本・CRC の不一致・偽の頭・読み出しの区切りをまたぐ）と局の位置
  - 受信のスレッド＝応答の頭の読み分け（ICY・局の一覧・401）・GGA を上げる・切れたらつなぎ直す
    （間は倍々・補正が届いた後は始めの間へ）・届かなければ切る・止めたら黙って終わる
  - 記録のループの側＝届いた順に受信機へ書き、同じバイト列をフォルダに残す・パスワードを書かない
  - CLI の指定の読み取りと、SSH 越しの覗き見の道具の補正の行

⚠️ 網にもシリアルポートにも触らない（偽のソケット・偽のポート）。
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import math
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from apps.field import cli
from apps.field import gnss as G
from apps.field import ntrip as N
from apps.field.session import RAW_INDEX_COLUMNS, SessionError, append_raw, read_raw

# RTCM 10403 の本文にある 1005 の見本（局 2003）。CRC と解釈の両方の錨＝自前の CRC で作った枠だけで
# 確かめると、CRC の誤りが作る側と読む側で打ち消し合う。
SAMPLE_1005 = bytes.fromhex("d300133ed7d30202980edeef34b4bd62ac0941986f33360b98")
SAMPLE_ECEF = (1114104.5999, -4850729.7108, 3975521.4643)

UTC = "2026-10-17T03:00:00.000000Z"


def _rtcm(payload: bytes) -> bytes:
    head = bytes((0xD3, len(payload) >> 8, len(payload) & 0xFF)) + payload
    return head + N.crc24q(head).to_bytes(3, "big")


def _msm(number: int, size: int = 30) -> bytes:
    """型の番号だけが意味を持つ中身（MSM の代わり）。"""
    return _rtcm(bytes((number >> 4, (number & 0x0F) << 4)) + bytes(size - 2))


def _pack(fields: list[tuple[int, int]]) -> bytes:
    value, width = 0, 0
    for bits, field in fields:
        value = value << bits | (field & ((1 << bits) - 1))
        width += bits
    return value.to_bytes(width // 8, "big")


def _station_1006(station_id: int, ecef: tuple[float, float, float], height: float) -> bytes:
    x, y, z = (round(v * 1e4) for v in ecef)
    return _pack([(12, 1006), (12, station_id), (6, 0), (4, 0b1110), (38, x), (2, 0), (38, y),
                  (2, 0), (38, z), (16, round(height * 1e4))])


def _geodetic_to_ecef(lat: float, lon: float, h: float) -> tuple[float, float, float]:
    a, f = 6378137.0, 1 / 298.257222101
    e2 = f * (2 - f)
    phi, lam = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    return ((n + h) * math.cos(phi) * math.cos(lam), (n + h) * math.cos(phi) * math.sin(lam),
            (n * (1 - e2) + h) * math.sin(phi))


def _nav_pvt(*, lat: float = 35.431240723, lon: float = 139.944707075, height_mm: int = 36970,
             h_msl_mm: int = -2790, fix_type: int = 3, flags: int = 0x01 | 0x80,
             satellites: int = 31, pdop: int = 123, hms: tuple[int, int, int] = (1, 26, 30)) -> bytes:
    payload = bytearray(92)
    struct.pack_into("<BBB", payload, 8, *hms)
    struct.pack_into("<BBBB", payload, 20, fix_type, flags, 0, satellites)
    struct.pack_into("<iiiiII", payload, 24, round(lon * 1e7), round(lat * 1e7), height_mm,
                     h_msl_mm, 14, 20)
    struct.pack_into("<H", payload, 76, pdop)
    return bytes(payload)


def _checksum_ok(sentence: bytes) -> bool:
    body, star, check = sentence.decode("ascii").strip().lstrip("$").partition("*")
    x = 0
    for c in body.encode("ascii"):
        x ^= c
    return star == "*" and check == f"{x:02X}"


# --- 指定と問い ---------------------------------------------------------------------


def test_the_caster_is_given_as_host_port_and_mountpoint():
    assert N.Caster.parse("geortk.jp:2101/tamtam") == N.Caster("geortk.jp", 2101, "tamtam")
    assert N.Caster.parse("ntrip.ales-corp.co.jp/RRS") == N.Caster("ntrip.ales-corp.co.jp", 2101, "RRS")
    assert str(N.Caster.parse("geortk.jp:2101/tamtam")) == "geortk.jp:2101/tamtam"
    for bad in ("geortk.jp:2101", "geortk.jp:2101/", ":2101/tamtam", "geortk.jp:x/tamtam",
                "geortk.jp:0/tamtam", "geortk.jp:2101/a/b"):
        with pytest.raises(N.NtripError):
            N.Caster.parse(bad)


def test_the_request_is_ntrip_1_and_sends_credentials_only_with_a_user():
    caster = N.Caster("geortk.jp", 2101, "tamtam")
    lines = N.request(caster, None, None).decode("ascii").split("\r\n")
    assert lines[0] == "GET /tamtam HTTP/1.0"
    assert any(line.startswith("User-Agent: NTRIP ") for line in lines)
    assert not any(line.startswith("Authorization") for line in lines)
    assert lines[-2:] == ["", ""]
    token = base64.b64encode(b"alice:s3cret").decode("ascii")
    assert f"Authorization: Basic {token}" in N.request(caster, "alice", "s3cret").decode("ascii")


def test_gga_is_made_from_a_nav_pvt_frame():
    pvt = G.parse_nav_pvt(_nav_pvt())
    sentence = N.gga(pvt)
    assert sentence is not None and _checksum_ok(sentence) and sentence.endswith(b"\r\n")
    # NAV-PVT は 1e-7 度＝35.4312407° ＝ 35° 25.87444′・139.9447071° ＝ 139° 56.68243′
    # RTK Fix＝品質 4・高さ＝受信機の hMSL・ジオイド高＝楕円体高 − hMSL
    assert sentence.decode("ascii").split("*")[0] == \
        "$GPGGA,012630.00,3525.87444,N,13956.68243,E,4,31,1.2,-2.790,M,39.760,M,,"


def test_gga_quality_hemispheres_and_a_minute_that_rounds_up():
    def fields(**kw):
        sentence = N.gga(G.parse_nav_pvt(_nav_pvt(**kw)))
        assert sentence is not None and _checksum_ok(sentence)
        return sentence.decode("ascii").split(",")
    assert fields(flags=0x01 | 0x40)[6] == "5"                 # RTK Float
    assert fields(flags=0x01)[6] == "1"                        # 単独
    assert fields(lat=-0.5, lon=-75.25)[2:6] == ["0030.00000", "S", "07515.00000", "W"]
    assert fields(lat=35.99999999)[2] == "3600.00000"          # 59.999999′ が 60′ にならない
    assert fields(satellites=120)[7] == "99"                   # 欄は 2 桁
    # 解が無い・無効なら上げない
    assert N.gga(G.parse_nav_pvt(_nav_pvt(fix_type=0))) is None
    assert N.gga(G.parse_nav_pvt(_nav_pvt(flags=0))) is None


# --- RTCM 3 の枠 --------------------------------------------------------------------


def test_the_published_1005_sample_passes_the_crc_and_gives_the_station():
    stream = N.RtcmStream()
    data = b"\r\n" + SAMPLE_1005 + b"\xd3" + _msm(1077)
    messages = [m for i in range(0, len(data), 4) for m in stream.feed(data[i:i + 4])]
    assert [n for n, _p in messages] == [1005, 1077]
    assert stream.stats.crc_errors == 0
    assert stream.types == {1005: 1, 1077: 1}
    station = N.parse_station(*messages[0])
    assert station is not None
    assert (station.message, station.station_id, station.antenna_height_m) == (1005, 2003, None)
    assert station.ecef_m == pytest.approx(SAMPLE_ECEF, abs=1e-6)
    assert N.parse_station(*messages[1]) is None


def test_a_broken_frame_is_counted_and_the_next_one_is_still_found():
    broken = bytearray(SAMPLE_1005)
    broken[10] ^= 0x01
    stream = N.RtcmStream()
    # 壊れた枠の中身に偽の頭（D3 02 02＝長さ 514）がある＝その長さが揃うまで数えは待つが、
    # 揃えば CRC で偽と分かり、間の枠を 1 つも落とさない（受信機へは届いたまま流している）
    assert stream.feed(bytes(broken) + _msm(1087)) == []
    assert [n for n, _p in stream.feed(_msm(1087) * 20)] == [1087] * 21
    assert stream.stats.crc_errors == 2
    # 長さの上の 6 ビットが 0 でない D3 は頭ではない
    stream = N.RtcmStream()
    assert [n for n, _p in stream.feed(b"\xd3\xff" + _msm(1097))] == [1097]
    assert stream.stats.crc_errors == 0


def test_a_1006_gives_the_antenna_height_and_the_geodetic_position():
    lat, lon, h = 35.447783, 140.051335, 79.674             # 市原1 のあたり
    ecef = _geodetic_to_ecef(lat, lon, h)
    payload = _station_1006(3030, ecef, 0.0815)
    station = N.parse_station(1006, payload)
    assert station is not None
    assert (station.station_id, station.antenna_height_m) == (3030, pytest.approx(0.0815))
    assert station.ecef_m == pytest.approx(ecef, abs=1e-4)
    out = station.as_json()
    assert (out["lat"], out["lon"]) == (pytest.approx(lat, abs=1e-8), pytest.approx(lon, abs=1e-8))
    assert out["ellipsoid_h_m"] == pytest.approx(h, abs=2e-4)
    assert N.parse_station(1006, payload[:-1]) is None       # 長さが合わない


# --- 受信のスレッド -------------------------------------------------------------------


class _Socket:
    """台本どおりに `recv` が返す偽のソケット。台本が尽きたら「届かない」（待ちの時間切れ）。
    台本の項目＝バイト列（b"" は相手が閉じた）か、投げる例外。"""

    def __init__(self, *script: bytes | BaseException, waits_for_gga: bool = False):
        self.script = list(script)
        self.sent: list[bytes] = []
        self.closed = False
        self.waits_for_gga = waits_for_gga  # VRS・単独局＝GGA を受けるまで何も返さない

    def settimeout(self, value: float | None) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        self.sent.append(bytes(data))

    def recv(self, size: int) -> bytes:
        gga_heard = any(s.startswith(b"$GPGGA") for s in self.sent)
        if not self.script or (self.waits_for_gga and not gga_heard):
            time.sleep(0.002)
            raise TimeoutError
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        self.closed = True


class _Connect:
    """`socket.create_connection` の代わり。用意したソケットを順に返し、尽きたら断る。"""

    def __init__(self, *sockets: _Socket | BaseException):
        self.sockets = list(sockets)
        self.calls: list[tuple[str, int]] = []

    def __call__(self, address: tuple[str, int], timeout: float) -> _Socket:
        self.calls.append(address)
        item = self.sockets.pop(0) if self.sockets else ConnectionRefusedError("断られた")
        if isinstance(item, BaseException):
            raise item
        return item


def _client(connect: _Connect, **kw) -> N.NtripClient:
    options: dict[str, Any] = dict(tick_s=0.0, no_data_s=0.05, retry_s=0.01, retry_max_s=0.04,
                                   gga_every_s=0.0, handshake_s=0.5)
    options.update(kw)
    return N.NtripClient(N.Caster("geortk.jp", 2101, "tamtam"), None, None,
                         clock=lambda: UTC, connect=connect, **options)


def _collect(client: N.NtripClient, done, timeout: float = 5.0) -> list[N.Delivery]:
    got: list[N.Delivery] = []
    deadline = time.monotonic() + timeout
    while not done(got):
        assert time.monotonic() < deadline, f"届いたもの: {got}"
        got += client.drain()
        time.sleep(0.002)
    return got


def _lost(items: list[N.Delivery]) -> list[str]:
    return [d.detail for d in items if d.event == "lost"]


def test_corrections_after_icy_200_are_handed_over_in_order_and_gga_goes_up():
    sock = _Socket(b"ICY 200 OK\r\n" + SAMPLE_1005[:7], SAMPLE_1005[7:], _msm(1077))
    connect = _Connect(sock)
    client = _client(connect, no_data_s=60)
    client.start()
    items = _collect(client, lambda got: sum(len(d.data) for d in got)
                     == len(SAMPLE_1005) + len(_msm(1077)))
    assert items[0].event == "connected" and items[0].detail == "geortk.jp:2101/tamtam"
    assert b"".join(d.data for d in items) == SAMPLE_1005 + _msm(1077)  # 応答の頭は渡さない
    assert connect.calls == [("geortk.jp", 2101)]
    assert sock.sent[0].startswith(b"GET /tamtam HTTP/1.0\r\n")
    assert not any(s.startswith(b"$GPGGA") for s in sock.sent)        # 解がまだ＝上げない
    sentence = N.gga(G.parse_nav_pvt(_nav_pvt()))
    client.set_gga(sentence)
    deadline = time.monotonic() + 5
    while sentence not in sock.sent:
        assert time.monotonic() < deadline
        time.sleep(0.002)
    client.stop()
    assert sock.closed and not client._thread.is_alive()
    assert _lost(client.drain()) == []                                  # 止めたのは切れたではない


def test_a_known_position_goes_up_right_after_the_request():
    # VRS・単独局は GGA を受けてから流し始める＝応答を待たずに上げる
    sock = _Socket(b"ICY 200 OK\r\n", _msm(1077), waits_for_gga=True)
    client = _client(_Connect(sock), no_data_s=60, handshake_s=0.2)
    sentence = N.gga(G.parse_nav_pvt(_nav_pvt()))
    client.set_gga(sentence)
    client.start()
    items = _collect(client, lambda got: any(d.data for d in got) or bool(_lost(got)), timeout=3)
    client.stop()
    assert _lost(items) == []
    assert sock.sent[:2] == [N.request(client.caster, None, None), sentence]


def test_an_http_200_reply_is_also_accepted():
    sock = _Socket(b"HTTP/1.1 200 OK\r\nServer: x\r\n", b"\r\n" + _msm(1077))
    client = _client(_Connect(sock), no_data_s=60)
    client.start()
    items = _collect(client, lambda got: any(d.data for d in got))
    client.stop()
    assert b"".join(d.data for d in items) == _msm(1077)


@pytest.mark.parametrize("reply, reason", [
    (b"SOURCETABLE 200 OK\r\nSTR;tamtam2;...\r\n", "接続先の名前 tamtam が caster にありません"),
    (b"HTTP/1.0 401 Unauthorized\r\n\r\n", "利用者名かパスワードが違います"),
    (b"ICY 403 Forbidden\r\n", "caster が断りました（ICY 403 Forbidden）"),
])
def test_a_refusal_is_reported_with_its_reason_and_the_client_tries_again(reply, reason):
    connect = _Connect(_Socket(reply), _Socket(b"ICY 200 OK\r\n", _msm(1077)))
    client = _client(connect, no_data_s=60)
    client.start()
    items = _collect(client, lambda got: any(d.data for d in got))
    client.stop()
    assert [d.event for d in items if d.event] == ["lost", "connected"]
    assert _lost(items)[0].startswith(reason)
    assert len(connect.calls) == 2


def test_the_wait_before_reconnecting_doubles_and_resets_after_corrections_arrived():
    refused = ConnectionRefusedError("断られた")
    connect = _Connect(refused, refused, _Socket(b"ICY 200 OK\r\n", _msm(1077), b""))
    client = _client(connect)
    client.start()
    items = _collect(client, lambda got: len(_lost(got)) >= 6)
    client.stop()
    waits = [detail.rsplit("・", 1)[1] for detail in _lost(items)[:6]]
    # 0.01 → 0.02 →（届いた後で切れた）0.01 → 0.02 → 0.04 → 上限 0.04
    assert waits == [f"{w} 秒後につなぎ直す" for w in ("0.01", "0.02", "0.01", "0.02", "0.04", "0.04")]
    assert "caster が接続を閉じました" in _lost(items)[2]
    assert "断られた" in _lost(items)[0]


def test_silence_is_treated_as_a_lost_connection():
    sock = _Socket(b"ICY 200 OK\r\n")
    client = _client(_Connect(sock))
    client.start()
    items = _collect(client, lambda got: bool(_lost(got)))
    client.stop()
    assert "補正が届きません（GGA をまだ上げていない＝受信機の解がまだ）" in _lost(items)[0]
    assert sock.closed


def test_a_reply_that_never_completes_is_given_up():
    client = _client(_Connect(_Socket(b"ICY 2")), handshake_s=0.05)
    client.start()
    items = _collect(client, lambda got: bool(_lost(got)))
    client.stop()
    assert _lost(items)[0].startswith("caster の応答の頭が揃いません")


# --- 記録のループの側 ------------------------------------------------------------------


class _FakeClient:
    def __init__(self, *deliveries: N.Delivery):
        self.caster = N.Caster("geortk.jp", 2101, "tamtam")
        self.pending = list(deliveries)
        self.gga: list[bytes | None] = []
        self.stopped = False

    def drain(self) -> list[N.Delivery]:
        out, self.pending = self.pending, []
        return out

    def set_gga(self, sentence: bytes | None) -> None:
        self.gga.append(sentence)

    def stop(self) -> None:
        self.stopped = True


class _Port:
    def __init__(self) -> None:
        self.written = bytearray()

    def write(self, data: bytes) -> int:
        self.written += data
        return len(data)


def _relay(tmp_path: Path, client, ticks=lambda: 100.0) -> N.NtripRelay:
    return N.NtripRelay(tmp_path, client, user="alice", clock=lambda: UTC, ticks=ticks)


def test_corrections_go_to_the_receiver_and_the_same_bytes_are_kept(tmp_path):
    lat, lon = 35.4478, 140.0513
    base = _rtcm(_station_1006(3030, _geodetic_to_ecef(lat, lon, 79.6), 0.0))
    chunks = [b"\r\n" + base[:5], base[5:] + _msm(1077), _msm(1087) + base]
    client = _FakeClient(N.Delivery(UTC, event="connected", detail="geortk.jp:2101/tamtam"),
                         *(N.Delivery(f"2026-10-17T03:00:0{i}.000000Z", data=c)
                           for i, c in enumerate(chunks)))
    now = [100.0]
    relay = _relay(tmp_path, client, ticks=lambda: now[0])
    assert relay.status() == "補正 つないでいます"
    port = _Port()
    relay.pump(port, None)
    now[0] = 103.0
    assert bytes(port.written) == b"".join(chunks)
    kept = list(read_raw(tmp_path, raw_file=N.RAW_FILE, index_file=N.INDEX_FILE))
    assert [c for _t, c in kept] == chunks
    assert [t for t, _c in kept] == [f"2026-10-17T03:00:0{i}.000000Z" for i in range(3)]
    header = json.loads((tmp_path / N.HEADER_FILE).read_text(encoding="utf-8"))
    assert (header["caster"], header["mountpoint"], header["user"]) == \
        ("geortk.jp:2101", "tamtam", "alice")
    assert [s["station_id"] for s in header["stations"]] == [3030]   # 同じ局は 1 回だけ
    assert header["stations"][0]["lat"] == pytest.approx(lat, abs=1e-8)
    assert [e["event"] for e in header["events"]] == ["connected"]
    assert relay.status() == f"補正 {len(port.written) / 1000:.1f} kB・3 秒前"
    assert relay.summary() == (f"補正 {len(port.written)} バイト・RTCM 4 個（1006×2・1077×1・1087×1）"
                               "・CRC の不一致 0・つないだ 1 回・局 1")


def test_a_lost_connection_shows_on_the_status_and_in_the_header(tmp_path):
    client = _FakeClient(N.Delivery(UTC, event="lost", detail="断られた・5 秒後につなぎ直す"))
    relay = _relay(tmp_path, client)
    relay.pump(_Port(), None)
    assert relay.status() == "補正 切れた（断られた・5 秒後につなぎ直す）"
    client.pending = [N.Delivery(UTC, event="connected", detail="x")]
    relay.pump(_Port(), None)
    assert relay.status() == "補正 つないだ・まだ届かない"
    header = json.loads((tmp_path / N.HEADER_FILE).read_text(encoding="utf-8"))
    assert [e["event"] for e in header["events"]] == ["lost", "connected"]


def test_gga_follows_each_new_solution_only(tmp_path):
    client = _FakeClient()
    relay = _relay(tmp_path, client)
    pvt = G.parse_nav_pvt(_nav_pvt())
    relay.pump(_Port(), None)
    relay.pump(_Port(), pvt)
    relay.pump(_Port(), pvt)                                 # 同じ解＝作り直さない
    relay.pump(_Port(), G.parse_nav_pvt(_nav_pvt(fix_type=0)))
    assert client.gga == [N.gga(pvt), None]                  # 解を失ったら上げるのをやめる


def test_close_stops_the_client_and_still_forwards_what_was_left(tmp_path):
    client = _FakeClient()
    relay = _relay(tmp_path, client)
    client.pending = [N.Delivery(UTC, data=_msm(1077))]
    port = _Port()
    relay.close(port)
    assert client.stopped and bytes(port.written) == _msm(1077)
    header = json.loads((tmp_path / N.HEADER_FILE).read_text(encoding="utf-8"))
    assert header["events"][-1]["event"] == "stopped"


def test_the_password_is_written_nowhere(tmp_path):
    client = N.NtripClient(N.Caster("geortk.jp", 2101, "tamtam"), "alice", "s3cret",
                           clock=lambda: UTC, connect=_Connect())
    relay = N.NtripRelay(tmp_path, client, user="alice", clock=lambda: UTC)
    relay.close(_Port())
    token = base64.b64encode(b"alice:s3cret")
    for path in tmp_path.iterdir():
        data = path.read_bytes()
        assert b"s3cret" not in data and token not in data, path.name


# --- CLI と覗き見 ---------------------------------------------------------------------


def _args(ntrip=None, user=None):
    return argparse.Namespace(ntrip=ntrip, ntrip_user=user)


def test_the_cli_reads_the_ntrip_settings_before_opening_the_port():
    assert cli._ntrip_settings(_args(), {}) is None
    assert cli._ntrip_settings(_args("geortk.jp:2101/tamtam"), {}) == \
        (N.Caster("geortk.jp", 2101, "tamtam"), None, None)
    assert cli._ntrip_settings(_args("c:2101/m", "alice"), {N.PASSWORD_ENV: "s3cret"}) == \
        (N.Caster("c", 2101, "m"), "alice", "s3cret")
    with pytest.raises(SessionError, match=N.PASSWORD_ENV):
        cli._ntrip_settings(_args("c:2101/m", "alice"), {})
    with pytest.raises(SessionError, match="--ntrip と一緒に"):
        cli._ntrip_settings(_args(None, "alice"), {})
    with pytest.raises(SessionError, match="host:port/接続先の名前"):
        cli._ntrip_settings(_args("c:2101"), {})


def test_the_ntrip_options_are_on_the_cli(capsys):
    with pytest.raises(SystemExit):
        cli.main(["gnss", "log", "--help"])
    out = capsys.readouterr().out
    assert "--ntrip HOST:PORT/MOUNT" in out and "--ntrip-user" in out


def _gnss_peek():
    path = Path(__file__).resolve().parents[1] / "tools" / "field_remote" / "gnss_peek.py"
    spec = importlib.util.spec_from_file_location("gnss_peek", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_remote_peek_shows_the_corrections(tmp_path):
    (tmp_path / G.RAW_FILE).touch()
    (tmp_path / G.INDEX_FILE).write_text(",".join(RAW_INDEX_COLUMNS) + "\n", encoding="utf-8")
    append_raw(tmp_path, b"$GNGGA\r\n", UTC, raw_file=G.RAW_FILE, index_file=G.INDEX_FILE)
    now = datetime(2026, 10, 17, 3, 0, 30, tzinfo=timezone.utc)
    client = _FakeClient()
    relay = _relay(tmp_path, client)
    assert _gnss_peek().peek(tmp_path, now)[-1] == \
        "補正 まだ届いていません・0 バイト・RTCM 0 個・CRC の不一致 0"
    client.pending = [
        N.Delivery("2026-10-17T03:00:10.000000Z", data=SAMPLE_1005 + _msm(1077)),
        N.Delivery("2026-10-17T03:00:12.000000Z", event="lost", detail="断られた・5 秒後につなぎ直す")]
    relay.pump(_Port(), None)
    assert _gnss_peek().peek(tmp_path, now)[-1] == (
        f"補正 最後に届いてから 20 秒・{len(SAMPLE_1005) + len(_msm(1077))} バイト・RTCM 2 個"
        "・CRC の不一致 0・最後に切れた 2026-10-17T03:00:12.000000Z（断られた・5 秒後につなぎ直す）")
