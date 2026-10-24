"""
apps/field/ntrip.py
===================
NTRIP の caster から補正（RTCM 3）を受け、GNSS の記録器（`gnss.py`）と**同じ COM**から受信機へ
流す。`gnss log --ntrip host:port/mount` のときだけ動く（無指定なら記録器は今と同じ）。

役割＝**携帯圏内での即時確認**（RTK の解を現場で見る）。本番の解は生ログからの後処理
（Field の正典 §5 の経路①）＝補正が切れても記録は止めない。

流れ（COM の読み書きとファイルの書き込みは記録のループの 1 本のスレッドだけ）
  受信のスレッド（`NtripClient`）… caster につなぎ、届いたバイトをキューへ入れる。GGA を
     1 秒ごとに上げる。切れたら間をあけてつなぎ直す。COM にもファイルにも触らない。
  記録のループ（`NtripRelay.pump`）… キューを空にして COM へ書き、`ntrip.rtcm` に追記する。
     NAV-PVT から作った GGA を受信のスレッドへ渡す（受信機の NMEA は止めたまま）。

GNSS の記録のフォルダに足すもの
  ntrip.json       … caster・接続先の名前・利用者名・局の座標（1005/1006）・つなぎの出来事。
                     変わるたびに丸ごと書き直す（原子的に）。**パスワードはどこにも書かない**
  ntrip.rtcm       … caster から届き、受信機へ流したバイト列そのまま。**追記のみ**
  ntrip_index.csv  … そのバイト列を PC が受けた時刻。**追記のみ**
  基準局の観測値（MSM）が残る＝局の座標を後から電子基準点で当て直せ、別の局を 2 本目の
  基準局として後処理に使える。⚠️ 局の座標は caster が配るまま（ALES は元期・有志の局は
  持ち主の自己申告）。

NTRIP は初期の版（1.0）で問う＝`GET /<mount> HTTP/1.0` に `ICY 200 OK` が返る（geoRTK は応えた）。
受信機の受け付け＝`CFG-UART1INPROT-RTCM3X`・`CFG-USBINPROT-RTCM3X` は出荷時から 1（RAM 層・
既定の層とも・2026-10-17 の X13 の VALGET）＝記録器の始めの設定に足さない。
"""

from __future__ import annotations

import base64
import csv
import math
import queue
import socket
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from apps.field.gnss import PvtStatus
from apps.field.session import (
    RAW_INDEX_COLUMNS,
    SessionError,
    _write_json_atomic,
    append_raw,
)

NTRIP_FORMAT = 1

HEADER_FILE = "ntrip.json"
RAW_FILE = "ntrip.rtcm"
INDEX_FILE = "ntrip_index.csv"

# パスワードは引数にしない（画面の履歴・プロセスの一覧に残る）。
PASSWORD_ENV = "RADIOSIM_NTRIP_PASSWORD"
DEFAULT_PORT = 2101

CONNECT_TIMEOUT_S = 10.0
HANDSHAKE_S = 10.0              # 問いを送ってから応答の頭が揃うまで
TICK_S = 0.5                    # 受信の待ち 1 回（GGA を上げる間合いもこれで刻む）
NO_DATA_S = 15.0                # これだけ届かなければ切れたとみなす
RETRY_S = 5.0                   # つなぎ直すまでの間（失敗が続くと倍々・上限あり）
RETRY_MAX_S = 60.0
GGA_EVERY_S = 1.0               # ALES の推奨（1 秒ごと）


class NtripError(SessionError):
    """caster の指定が読めない・caster が断った・補正が届かないとき。"""


@dataclass(frozen=True)
class Caster:
    host: str
    port: int
    mountpoint: str

    @classmethod
    def parse(cls, text: str) -> Caster:
        """`host:port/mount`（port を省けば 2101）。"""
        address, slash, mount = text.partition("/")
        host, colon, port = address.partition(":")
        if not slash or not mount or not host or "/" in mount:
            raise NtripError(f"NTRIP の指定は host:port/接続先の名前 です: {text!r}")
        try:
            number = int(port) if colon else DEFAULT_PORT
        except ValueError:
            raise NtripError(f"NTRIP のポートが数ではありません: {text!r}") from None
        if not 0 < number < 65536:
            raise NtripError(f"NTRIP のポートが範囲の外です: {text!r}")
        return cls(host, number, mount)

    def __str__(self) -> str:
        return f"{self.host}:{self.port}/{self.mountpoint}"


def request(caster: Caster, user: str | None, password: str | None) -> bytes:
    lines = [f"GET /{caster.mountpoint} HTTP/1.0",
             f"User-Agent: NTRIP RadioSimField/{NTRIP_FORMAT}",   # 1.0 の caster は頭の NTRIP を見る
             "Accept: */*"]
    if user is not None:
        token = base64.b64encode(f"{user}:{password or ''}".encode()).decode("ascii")
        lines.append(f"Authorization: Basic {token}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


# --- GGA（caster が局を選ぶための位置） ----------------------------------------------

# NAV-PVT の解 → GGA の品質（1＝単独・4＝RTK Fix・5＝RTK Float）
_GGA_QUALITY = {2: 4, 1: 5}


def _ddmm(value: float, width: int) -> str:
    degrees = int(abs(value))
    minutes = round((abs(value) - degrees) * 60, 5)
    if minutes >= 60:                       # 59.999996 分が丸めで 60 になった
        degrees, minutes = degrees + 1, minutes - 60
    return f"{degrees:0{width}d}{minutes:08.5f}"


def gga(pvt: PvtStatus) -> bytes | None:
    """NAV-PVT から GGA の 1 文を作る。有効な解がまだなければ None（上げない）。
    ⚠️ 高さの欄は受信機の hMSL＝caster が局を選ぶのに使うだけで、解には効かない。"""
    if pvt.fix_type not in (2, 3, 4) or not pvt.fix_ok:
        return None
    h, m, s = pvt.utc_hms
    body = (f"GPGGA,{h:02d}{m:02d}{s:02d}.00,{_ddmm(pvt.lat_deg, 2)},"
            f"{'N' if pvt.lat_deg >= 0 else 'S'},{_ddmm(pvt.lon_deg, 3)},"
            f"{'E' if pvt.lon_deg >= 0 else 'W'},{_GGA_QUALITY.get(pvt.carrier, 1)},"
            f"{min(pvt.satellites, 99):02d},{pvt.pdop:.1f},{pvt.h_msl_m:.3f},M,"
            f"{pvt.height_m - pvt.h_msl_m:.3f},M,,")
    check = 0
    for c in body.encode("ascii"):
        check ^= c
    return f"${body}*{check:02X}\r\n".encode("ascii")


# --- RTCM 3 の枠 ---------------------------------------------------------------------

_PREAMBLE = 0xD3


def _crc24q_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
        table.append(crc & 0xFFFFFF)
    return table


_CRC24Q = _crc24q_table()


def crc24q(data: bytes) -> int:
    crc = 0
    for x in data:
        crc = ((crc << 8) & 0xFFFFFF) ^ _CRC24Q[(crc >> 16) ^ x]
    return crc


@dataclass
class RtcmStats:
    frames: int = 0
    crc_errors: int = 0
    other_bytes: int = 0


class RtcmStream:
    """バイト列から RTCM 3 の枠を取り出す（読み出しの区切りをまたいでよい）。数えるだけ＝
    受信機へは届いたバイトをそのまま流す。"""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.stats = RtcmStats()
        self.types: Counter[int] = Counter()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self._buf += data
        out: list[tuple[int, bytes]] = []
        while True:
            start = self._buf.find(bytes((_PREAMBLE,)))
            if start < 0:
                self._skip(len(self._buf))
                return out
            self._skip(start)
            if len(self._buf) < 3:
                return out
            if self._buf[1] & 0xFC:             # 長さの上の 6 ビットは 0＝偽の頭
                self._skip(1)
                continue
            length = (self._buf[1] & 0x03) << 8 | self._buf[2]
            end = 3 + length + 3
            if len(self._buf) < end:
                return out
            if crc24q(bytes(self._buf[:end - 3])) != int.from_bytes(self._buf[end - 3:end], "big"):
                self.stats.crc_errors += 1
                self._skip(1)
                continue
            payload = bytes(self._buf[3:end - 3])
            number = payload[0] << 4 | payload[1] >> 4 if length >= 2 else 0
            out.append((number, payload))
            self.stats.frames += 1
            self.types[number] += 1
            del self._buf[:end]

    def _skip(self, n: int) -> None:
        self.stats.other_bytes += n
        del self._buf[:n]


@dataclass(frozen=True)
class Station:
    """1005/1006 の基準局の位置（ARP の ECEF）。"""

    message: int
    station_id: int
    itrf_year: int
    ecef_m: tuple[float, float, float]
    antenna_height_m: float | None          # 1006 だけ

    def as_json(self) -> dict[str, Any]:
        lat, lon, height = ecef_to_geodetic(*self.ecef_m)
        return {"message": self.message, "station_id": self.station_id,
                "itrf_year": self.itrf_year, "ecef_m": list(self.ecef_m),
                "antenna_height_m": self.antenna_height_m,
                "lat": round(lat, 9), "lon": round(lon, 9), "ellipsoid_h_m": round(height, 4)}


def _bits(payload: bytes, start: int, width: int, signed: bool = False) -> int:
    value = int.from_bytes(payload, "big") >> (len(payload) * 8 - start - width) & ((1 << width) - 1)
    if signed and value >> (width - 1):
        value -= 1 << width
    return value


def parse_station(number: int, payload: bytes) -> Station | None:
    """1005（19 バイト）・1006（21 バイト）なら局の位置を返す。それ以外は None。"""
    if (number, len(payload)) not in ((1005, 19), (1006, 21)):
        return None
    xyz = tuple(_bits(payload, at, 38, signed=True) * 1e-4 for at in (34, 74, 114))
    height = _bits(payload, 152, 16) * 1e-4 if number == 1006 else None
    return Station(number, _bits(payload, 12, 12), _bits(payload, 24, 6),
                   (xyz[0], xyz[1], xyz[2]), height)


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """ECEF → 緯度・経度（度）・楕円体高（m）。GRS80（WGS84 との差は 0.1 mm 以下）。"""
    a, f = 6378137.0, 1 / 298.257222101
    e2 = f * (2 - f)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(10):
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        lat = math.atan2(z + e2 * n * math.sin(lat), p)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return math.degrees(lat), math.degrees(math.atan2(y, x)), p / math.cos(lat) - n


# --- 受信のスレッド ------------------------------------------------------------------


class Socket(Protocol):
    def settimeout(self, value: float | None, /) -> None: ...

    def sendall(self, data: bytes, /) -> None: ...

    def recv(self, size: int, /) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Delivery:
    """受信のスレッドから記録のループへの 1 件（届いたバイトか、つなぎの出来事）。"""

    pc_utc: str
    data: bytes = b""
    event: str = ""                         # "connected"・"lost"
    detail: str = ""


class NtripClient:
    """caster とつなぎ、届いたバイトと出来事を `drain()` で渡す。`connect` は
    `socket.create_connection` と同じ形（テストで差し替える）。"""

    def __init__(
        self, caster: Caster, user: str | None, password: str | None, *,
        clock: Callable[[], str], ticks: Callable[[], float] = time.monotonic,
        connect: Callable[..., Socket] = socket.create_connection,
        tick_s: float = TICK_S, no_data_s: float = NO_DATA_S, retry_s: float = RETRY_S,
        retry_max_s: float = RETRY_MAX_S, gga_every_s: float = GGA_EVERY_S,
        handshake_s: float = HANDSHAKE_S,
    ):
        self.caster = caster
        self._request = request(caster, user, password)
        self._clock = clock
        self._ticks = ticks
        self._connect = connect
        self._tick_s = tick_s
        self._no_data_s = no_data_s
        self._retry_s = retry_s
        self._retry_max_s = retry_max_s
        self._gga_every_s = gga_every_s
        self._handshake_s = handshake_s
        self._gga: bytes | None = None
        self._got = False                   # 今のつなぎで補正が届いたか
        self._queue: queue.Queue[Delivery] = queue.Queue()
        self._stop = threading.Event()
        # 記録が終わるときに待ちきれなくても、プロセスの終わりを止めない
        self._thread = threading.Thread(target=self._run, name="ntrip", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, wait_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread.ident is not None:  # 始める前に止めた（記録の始まりで落ちた）なら待たない
            self._thread.join(wait_s)

    def set_gga(self, sentence: bytes | None) -> None:
        self._gga = sentence                # 1 つの代入＝スレッドの間で割れない

    def drain(self) -> list[Delivery]:
        out = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                return out

    def _put(self, **fields: Any) -> None:
        self._queue.put(Delivery(self._clock(), **fields))

    def _run(self) -> None:
        """つなぐ → 切れたら間をあけてつなぎ直す。間は失敗が続くと倍々に延び、補正が
        1 バイトでも届いたつなぎの後は始めの間へ戻す。"""
        delay = self._retry_s
        while True:
            self._got = False
            try:
                self._session()
                return                      # 切れずに戻るのは止められたときだけ
            except (OSError, NtripError) as e:
                reason = str(e) or type(e).__name__
            if self._stop.is_set():
                return
            if self._got:
                delay = self._retry_s
            self._put(event="lost", detail=f"{reason}・{delay:g} 秒後につなぎ直す")
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, self._retry_max_s)

    def _session(self) -> None:
        sock = self._connect((self.caster.host, self.caster.port), timeout=CONNECT_TIMEOUT_S)
        try:
            sock.settimeout(self._tick_s)
            sock.sendall(self._request)
            sent_gga = -math.inf
            if self._gga is not None:       # VRS・単独局は GGA を待ってから流し始める
                sock.sendall(self._gga)
                sent_gga = self._ticks()
            rest = self._handshake(sock)
            self._put(event="connected", detail=str(self.caster))
            last = self._ticks()
            if rest:
                self._put(data=rest)
                self._got = True
            while not self._stop.is_set():
                sentence = self._gga
                if sentence is not None and self._ticks() - sent_gga >= self._gga_every_s:
                    sock.sendall(sentence)
                    sent_gga = self._ticks()
                try:
                    data = sock.recv(4096)
                except TimeoutError:
                    data = None
                if data == b"":
                    raise NtripError("caster が接続を閉じました")
                if data:
                    self._put(data=data)
                    last, self._got = self._ticks(), True
                elif self._ticks() - last > self._no_data_s:
                    waiting = "" if self._gga is not None else "（GGA をまだ上げていない＝受信機の解がまだ）"
                    raise NtripError(f"{self._no_data_s:.0f} 秒 補正が届きません{waiting}")
        finally:
            sock.close()

    def _handshake(self, sock: Socket) -> bytes:
        """応答の頭を読み、断られたら理由つきで止める。頭の後ろに続いたバイトは返す。"""
        buf = b""
        since = self._ticks()
        while True:
            head, sep, rest = buf.partition(b"\r\n")
            if sep:
                if head.startswith(b"ICY 200"):
                    return rest
                if head.startswith(b"HTTP/1.") and b" 200" in head[:13]:
                    if b"\r\n\r\n" in buf:
                        return buf.split(b"\r\n\r\n", 1)[1]
                else:
                    raise NtripError(_refusal(head, self.caster))
            if self._stop.is_set() or self._ticks() - since > self._handshake_s or len(buf) > 8192:
                raise NtripError("caster の応答の頭が揃いません")
            try:
                data = sock.recv(4096)
            except TimeoutError:
                continue
            if not data:
                raise NtripError("caster が応答の前に接続を閉じました")
            buf += data


def _refusal(head: bytes, caster: Caster) -> str:
    line = head.decode("ascii", "replace").strip()
    if line.startswith("SOURCETABLE"):
        return f"接続先の名前 {caster.mountpoint} が caster にありません（局の一覧が返りました）"
    if " 401" in line:
        return f"利用者名かパスワードが違います（{line}）"
    return f"caster が断りました（{line}）"


# --- 記録のループの側 ----------------------------------------------------------------


class Port(Protocol):
    def write(self, data: bytes) -> int | None: ...


class NtripRelay:
    """届いた補正を受信機へ流し、フォルダに残す。呼ぶのは記録のループだけ。"""

    def __init__(self, directory: Path, client: NtripClient, *, user: str | None,
                 clock: Callable[[], str], ticks: Callable[[], float] = time.monotonic):
        self._directory = Path(directory)
        self._client = client
        self._clock = clock
        self._ticks = ticks
        self.stream = RtcmStream()
        self.bytes_written = 0
        self.connections = 0
        self.stations: list[Station] = []
        self._events: list[dict[str, str]] = []
        self._state = "つないでいます"
        self._last_data: float | None = None
        self._last_pvt: PvtStatus | None = None
        self._header: dict[str, Any] = {
            "format": NTRIP_FORMAT,
            "caster": f"{client.caster.host}:{client.caster.port}",
            "mountpoint": client.caster.mountpoint,
            "user": user,
            "protocol": "NTRIP 1.0",
            "started_utc": clock(),
            "forwarded_to": "受信機（gnss.ubx と同じポート）",
        }
        (self._directory / RAW_FILE).touch()
        with (self._directory / INDEX_FILE).open("w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(RAW_INDEX_COLUMNS)
        self._save()

    def _save(self) -> None:
        _write_json_atomic(self._directory / HEADER_FILE, {
            **self._header,
            "stations": [s.as_json() for s in self.stations],
            "events": self._events,
        })

    def pump(self, port: Port, pvt: PvtStatus | None) -> None:
        """GGA を新しい解に替え、届いた分を受信機へ書いて残す。"""
        if pvt is not None and pvt is not self._last_pvt:
            self._last_pvt = pvt
            self._client.set_gga(gga(pvt))
        changed = False
        for item in self._client.drain():
            if item.data:
                port.write(item.data)
                append_raw(self._directory, item.data, item.pc_utc,
                           raw_file=RAW_FILE, index_file=INDEX_FILE)
                self.bytes_written += len(item.data)
                self._last_data = self._ticks()
                for number, payload in self.stream.feed(item.data):
                    station = parse_station(number, payload)
                    if station is not None and station not in self.stations:
                        self.stations.append(station)
                        changed = True
            else:
                self._events.append({"pc_utc": item.pc_utc, "event": item.event,
                                     "detail": item.detail})
                if item.event == "connected":
                    self.connections += 1
                    self._state = "つないだ"
                else:
                    self._state = f"切れた（{item.detail}）"
                changed = True
        if changed:
            self._save()

    def close(self, port: Port) -> None:
        """受信のスレッドを止め、残りを流して書き終える。"""
        self._client.stop()
        self.pump(port, None)
        self._events.append({"pc_utc": self._clock(), "event": "stopped", "detail": ""})
        self._save()

    def status(self) -> str:
        if self._state == "つないだ" and self._last_data is not None:
            return (f"補正 {self.bytes_written / 1000:.1f} kB"
                    f"・{self._ticks() - self._last_data:.0f} 秒前")
        if self._state == "つないだ":
            return "補正 つないだ・まだ届かない"
        return f"補正 {self._state}"

    def summary(self) -> str:
        kinds = "・".join(f"{n}×{c}" for n, c in sorted(self.stream.types.items())) or "なし"
        return (f"補正 {self.bytes_written} バイト・RTCM {self.stream.stats.frames} 個（{kinds}）"
                f"・CRC の不一致 {self.stream.stats.crc_errors}"
                f"・つないだ {self.connections} 回・局 {len(self.stations)}")
