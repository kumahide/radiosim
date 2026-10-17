"""
apps/field/gnss.py
===================
GNSS 受信機（u-blox ZED-X20P）の**生データの記録器**＝静止観測を 1 フォルダに残す。

    gnss-<開始時刻>/
      gnss.json       … ヘッダ（受信機の版・書いた設定・コミット）。開始時に 1 回だけ書く
      gnss.ubx        … 受信機から届いたバイト列そのまま。**追記のみ**
      gnss_index.csv  … そのバイト列を PC が受けた時刻（読み出し 1 回 1 行）。**追記のみ**

無線のセッション（`session.py`）とは別のフォルダ＝静止観測は TX/RX の雛形なしに回す
（サイトごとに一度・受け入れ試験の三角点と水準点）。**位置の解はここでは作らない**＝
本番の解は生ログ（RAWX・SFRBX）から後処理で作る（Field の正典 §5 の経路①）。
`gnss.csv`（`session.GNSS_COLUMNS`）はこの生ログの上に後で載せる。

**始め方**（崩れると、何の設定で取ったログか後から言えない）
  1. `MON-VER` を問い合わせて受信機の版を得る（刻印の「GNSS のファーム版」）。
  2. `CFG-VALSET` を **RAM 層だけ**に書く（生データの出力と、出荷時に切れている QZSS）＝
     電源を切れば元の設定に戻り、受信機に恒久の変更を残さない。
  3. ACK を確かめる。NAK・無返答なら止める（フォルダは作らない）。
  1・3・4 の問いは、返事が来るまで送り直す（1 回の返事が落ちることがある＝B-350）。
  4. UART1 の速度を記録の速度（115200）へ上げ、NMEA を止める（同じく RAM 層）。
     新しい速度で `MON-VER` が返ることを確かめる。応えなければ止める。
  5. ヘッダを書き、**ここまでに受けたバイトを生ログの先頭に書く**（捨てない）。
  終えるときは `stop()` で始めの速度・NMEA ありへ書き戻し、2. の設定も出荷時の 0 へ戻す。
  出力を残すと、屋外では始めの速度（38400）が RAWX で埋まり、次の記録の MON-VER が
  返らない（2026-10-17 の三角点・B-349）。

**速度を上げる理由**（2026-10-16 の実機）＝出荷時の 38400 bps（約 3.8 kB/s）は、屋外で信号が
100 を超えると RAWX だけで埋まる（16＋32×信号数 バイト/秒）。出荷時の NMEA は後処理に要らず、
屋内でも帯域の 6 割を食っていた。

⚠️ 画面の表示（RAWX の信号数・NAV-PVT の解）は現場で様子を見るためのもの。読めない
メッセージがあっても記録は止めない＝生ログが正で、表示は数えるだけ。
"""

from __future__ import annotations

import csv
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from apps.field.session import (
    RAW_INDEX_COLUMNS,
    SessionError,
    _write_json_atomic,
    append_raw,
    parse_utc,
)

GNSS_LOG_FORMAT = 1

HEADER_FILE = "gnss.json"
RAW_FILE = "gnss.ubx"
INDEX_FILE = "gnss_index.csv"

# 問い合わせ・設定への返事を待つ上限。MON-VER も ACK も、届くなら 1 秒かからない。
REPLY_WAIT_S = 3.0

# 返事が無いときに同じ問いを送り直す間隔（B-350）。38400 bps・NMEA ありの線で、1 回の問いの
# 返事が 15 回に 2 回落ちた（2026-10-17 の X13・受信機は正常）。MON-VER の問いも RAM 層の
# VALSET も、何度送っても同じ結果。1 秒の倍数にしない＝落ちる原因が毎秒の NMEA の塊との
# 重なり（仮説・未確認）なら、1 秒ごとの送り直しは毎回同じ位相に当たる。
RESEND_S = 0.7

# --- UBX の枠 ------------------------------------------------------------------

SYNC = b"\xb5\x62"

NAV_PVT = (0x01, 0x07)
RXM_SFRBX = (0x02, 0x13)
RXM_RAWX = (0x02, 0x15)
ACK_NAK = (0x05, 0x00)
ACK_ACK = (0x05, 0x01)
CFG_VALSET = (0x06, 0x8A)
MON_VER = (0x0A, 0x04)

# 1 フレームの中身の上限。RAWX が最大（16＋32×255＝8176 バイト）。これを超える長さは
# 偽の同期（中身の途中に B5 62 が現れた）とみなす。
_MAX_PAYLOAD = 8192

# CFG-VALSET の層。RAM だけに書く（BBR・Flash には書かない）。
LAYER_RAM = 0x01

# 書く出力（キー名・キー・値＝航法の解 1 回ごとに 1 回）。RAWX と SFRBX は 2026-10-16 に
# HPG 2.11 で ACK を確かめたキー（F9 と同じ）。NAV-PVT は画面の状態表示に使う。
# ポートが受信機の UART1 か USB かは PC から見分けられないので、両方に出す。
OUTPUTS: tuple[tuple[str, int, int], ...] = (
    ("CFG-MSGOUT-UBX_RXM_RAWX_UART1", 0x209102A5, 1),
    ("CFG-MSGOUT-UBX_RXM_RAWX_USB", 0x209102A7, 1),
    ("CFG-MSGOUT-UBX_RXM_SFRBX_UART1", 0x20910232, 1),
    ("CFG-MSGOUT-UBX_RXM_SFRBX_USB", 0x20910234, 1),
    ("CFG-MSGOUT-UBX_NAV_PVT_UART1", 0x20910007, 1),
    ("CFG-MSGOUT-UBX_NAV_PVT_USB", 0x20910009, 1),
)

# 入れる衛星系（出力と同じ VALSET で書く）。X20P（HPG 2.11）は出荷時に QZSS の系全体が切れて
# いる＝RAM 層も既定の層も `QZSS_ENA`＝0、各信号（L1C/A・L1S・L2C・L5）は 1（2026-10-17 の
# VALGET・B-351）。日本では常に高仰角にいて、電子基準点の RINEX にも在る＝後処理の共通衛星。
# 出荷時の値は 0＝`stop()` で 0 へ戻す。⚠️ `CFG-SIGNAL` を変えると受信機は追尾をやり直す。
SIGNALS: tuple[tuple[str, int, int], ...] = (
    ("CFG-SIGNAL-QZSS_ENA", 0x10310024, 1),
)

# 記録中の UART1 の速度。屋外の見込み（約 6 kB/s）の 2 倍ほどの余裕（約 11.5 kB/s）。
LOG_BAUD = 115200
BAUD_KEY = ("CFG-UART1-BAUDRATE", 0x40520001)
NMEA_KEY = ("CFG-UART1OUTPROT-NMEA", 0x10740002)

# 速度を替える VALSET を送ってから PC 側を替えるまでの間。送り終わる前に PC 側を替えると
# VALSET そのものが化ける。⚠️ この VALSET の ACK は読めない（受信機が返事の途中で速度を
# 替える＝2026-10-16 の実機）＝新しい速度で MON-VER が返ることで確かめる。
SETTLE_S = 0.3


def port_items(baud: int, nmea: int) -> tuple[tuple[str, int, int], ...]:
    """UART1 の速度と NMEA の出力の設定。PC とはボードの USB シリアル変換＝UART1 でつながる。"""
    return ((BAUD_KEY[0], BAUD_KEY[1], baud), (NMEA_KEY[0], NMEA_KEY[1], nmea))


class GnssError(SessionError):
    """受信機が応えない・設定を断った・ログの約束が破れたとき。"""


class Port(Protocol):
    baudrate: int

    @property
    def in_waiting(self) -> int: ...

    def read(self, size: int) -> bytes: ...

    def write(self, data: bytes) -> int | None: ...


def checksum(body: bytes) -> bytes:
    """UBX の検査和（クラスから中身の終わりまでの 8 ビット Fletcher）。"""
    a = b = 0
    for x in body:
        a = (a + x) & 0xFF
        b = (b + a) & 0xFF
    return bytes((a, b))


def frame(kind: tuple[int, int], payload: bytes = b"") -> bytes:
    body = struct.pack("<BBH", kind[0], kind[1], len(payload)) + payload
    return SYNC + body + checksum(body)


# キーの 28〜30 ビット → 値の書式（1＝真偽・2＝1 バイト・3＝2 バイト・4＝4 バイト）。
_VALUE_FORMATS = {1: "B", 2: "B", 3: "H", 4: "I"}


def valset(items: tuple[tuple[str, int, int], ...], layers: int = LAYER_RAM) -> bytes:
    """`CFG-VALSET` のフレーム。値が 4 バイトまでのキーを受け付ける（8 バイトは使わない）。"""
    payload = struct.pack("<BBxx", 0, layers)
    for name, key, value in items:
        size = (key >> 28) & 0x7
        fmt = _VALUE_FORMATS.get(size)
        top = 1 if size == 1 else (1 << 8 * struct.calcsize(fmt)) - 1 if fmt else -1
        if fmt is None or not 0 <= value <= top:
            raise GnssError(f"書けないキーか値です: {name}（{key:#010x}＝{value}）")
        payload += struct.pack("<I" + fmt, key, value)
    return frame(CFG_VALSET, payload)


@dataclass(frozen=True)
class UbxMessage:
    kind: tuple[int, int]
    payload: bytes


@dataclass
class UbxStats:
    frames: int = 0
    checksum_errors: int = 0
    other_bytes: int = 0          # UBX の外のバイト（NMEA など）と、検査和の合わない枠


class UbxStream:
    """バイト列から UBX のフレームを取り出す（読み出しの区切りをまたいでよい）。"""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.stats = UbxStats()

    def feed(self, data: bytes) -> list[UbxMessage]:
        self._buf += data
        out: list[UbxMessage] = []
        while True:
            start = self._buf.find(SYNC)
            if start < 0:
                # 末尾の B5 は、次の読み出しの 62 と合わせて同期の頭になり得る
                keep = 1 if self._buf.endswith(SYNC[:1]) else 0
                self._skip(len(self._buf) - keep)
                return out
            self._skip(start)
            if len(self._buf) < 6:
                return out
            cls, mid, length = struct.unpack_from("<BBH", self._buf, 2)
            if length > _MAX_PAYLOAD:
                self._skip(1)
                continue
            end = 6 + length + 2
            if len(self._buf) < end:
                return out
            if bytes(self._buf[end - 2:end]) != checksum(bytes(self._buf[2:end - 2])):
                # 偽の同期かもしれない＝1 バイトだけ進めて探し直す
                self.stats.checksum_errors += 1
                self._skip(1)
                continue
            out.append(UbxMessage((cls, mid), bytes(self._buf[6:end - 2])))
            self.stats.frames += 1
            del self._buf[:end]

    def _skip(self, n: int) -> None:
        self.stats.other_bytes += n
        del self._buf[:n]


# --- 中身の読み取り（表示とヘッダに要る分だけ） ---------------------------------


def _text(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", "replace").strip()


@dataclass(frozen=True)
class ReceiverVersion:
    software: str
    hardware: str
    extensions: tuple[str, ...]

    @property
    def firmware(self) -> str:
        """`FWVER=` の拡張（例 `EXT HPG 2.11`）。無ければ空。"""
        for ext in self.extensions:
            if ext.startswith("FWVER="):
                return ext[len("FWVER="):]
        return ""


def parse_mon_ver(payload: bytes) -> ReceiverVersion:
    if len(payload) < 40 or (len(payload) - 40) % 30:
        raise GnssError(f"MON-VER の長さが想定と違います: {len(payload)} バイト")
    extensions = tuple(_text(payload[i:i + 30]) for i in range(40, len(payload), 30))
    return ReceiverVersion(_text(payload[:30]), _text(payload[30:40]), extensions)


@dataclass(frozen=True)
class RawxEpoch:
    """RAWX 1 回ぶんの数え（観測値そのものは生ログにある）。"""

    week: int
    rcv_tow: float
    signals: int
    pr_valid: int                 # 擬似距離が有効な信号
    cp_valid: int                 # 搬送波位相が有効な信号
    clock_reset: bool = False     # recStat の clkReset＝受信機が時計を合わせ直した回

    @property
    def time_known(self) -> bool:
        """週が確定しているか。確定前は week=0・rcvTow＝起動からの秒で出る（B-346）。"""
        return self.week != 0

    @property
    def gps_seconds(self) -> float:
        return self.week * 604800 + self.rcv_tow


def parse_rawx(payload: bytes) -> RawxEpoch:
    if len(payload) < 16:
        raise GnssError(f"RXM-RAWX が短すぎます: {len(payload)} バイト")
    rcv_tow, week, _leap_s, count, rec_stat = struct.unpack_from("<dHbBB", payload, 0)
    if len(payload) != 16 + 32 * count:
        raise GnssError(f"RXM-RAWX の長さが信号数 {count} と合いません: {len(payload)} バイト")
    tracking = [payload[16 + 32 * i + 30] for i in range(count)]
    return RawxEpoch(week, rcv_tow, count,
                     sum(1 for t in tracking if t & 0x01), sum(1 for t in tracking if t & 0x02),
                     bool(rec_stat & 0x02))


_FIX_TYPES = {0: "解なし", 1: "推測航法のみ", 2: "2D", 3: "3D", 4: "3D＋推測航法", 5: "時刻のみ"}
_CARRIER = {1: "RTK Float", 2: "RTK Fix"}


@dataclass(frozen=True)
class PvtStatus:
    """NAV-PVT のうち、画面に出す分と、NTRIP の caster へ上げる GGA の材料（`ntrip.gga`）。
    **ここの解は記録しない**（本番は後処理）。"""

    fix_type: int
    fix_ok: bool                  # gnssFixOK（受信機が解を有効とみなしたか）
    carrier: int                  # 0 なし・1 Float・2 Fix
    satellites: int
    h_acc_m: float
    v_acc_m: float
    lat_deg: float = 0.0
    lon_deg: float = 0.0
    height_m: float = 0.0         # 楕円体高
    h_msl_m: float = 0.0          # 受信機の内蔵のジオイドでの標高（⚠️ 国土地理院のものと約 4.9 m 違う）
    utc_hms: tuple[int, int, int] = (0, 0, 0)
    pdop: float = 0.0

    def describe(self) -> str:
        label = _FIX_TYPES.get(self.fix_type, f"種別 {self.fix_type}")
        if self.carrier in _CARRIER:
            label += f"・{_CARRIER[self.carrier]}"
        if self.fix_type and not self.fix_ok:
            label += "（無効）"
        return label


def parse_nav_pvt(payload: bytes) -> PvtStatus:
    if len(payload) < 92:
        raise GnssError(f"NAV-PVT が短すぎます: {len(payload)} バイト")
    hour, minute, second = struct.unpack_from("<BBB", payload, 8)
    fix_type, flags, _flags2, satellites = struct.unpack_from("<BBBB", payload, 20)
    lon, lat, height, h_msl, h_acc, v_acc = struct.unpack_from("<iiiiII", payload, 24)
    (pdop,) = struct.unpack_from("<H", payload, 76)
    return PvtStatus(fix_type, bool(flags & 0x01), (flags >> 6) & 0x03, satellites,
                     h_acc / 1000, v_acc / 1000, lat * 1e-7, lon * 1e-7, height / 1000,
                     h_msl / 1000, (hour, minute, second), pdop / 100)


# --- 記録器 --------------------------------------------------------------------


class GnssLogger:
    """受信機に問い合わせて設定し、届いたバイト列をそのまま書く。

    `clock` は PC の UTC（`recorder.now_utc` と同じ時計）、`ticks` は待ちの上限を
    測る単調な時計。どちらも呼び手が渡す（テストで差し替えるため）。
    """

    def __init__(
        self, root: str | Path, port: Port, *, software_commit: str, port_name: str,
        baud: int, clock: Callable[[], str], ticks: Callable[[], float] = time.monotonic,
        outputs: tuple[tuple[str, int, int], ...] = OUTPUTS, log_baud: int = LOG_BAUD,
        signals: tuple[tuple[str, int, int], ...] = SIGNALS,
    ):
        if not software_commit:
            raise GnssError("ソフトウェアのコミットが空です（刻印の 1 項目）")
        self._root = Path(root)
        self._port = port
        self._software_commit = software_commit
        self._port_name = port_name
        self._baud = baud
        self._log_baud = log_baud
        self.baud_at_start: int | None = None          # 受信機が応えた速度
        self._clock = clock
        self._ticks = ticks
        self._outputs = outputs
        self._signals = signals
        self.stream = UbxStream()
        self.directory: Path | None = None
        self.version: ReceiverVersion | None = None
        self._pending: list[tuple[str, bytes]] = []     # ヘッダを書くまでに受けた分
        self._replies: list[UbxMessage] = []
        self.bytes_written = 0
        self.counts: dict[str, int] = {"rawx": 0, "sfrbx": 0, "pvt": 0}
        self.undecoded = 0
        self.rawx_gaps = 0
        self.last_rawx: RawxEpoch | None = None
        self.last_pvt: PvtStatus | None = None
        self._rawx_period: float | None = None

    # 始め方 -------------------------------------------------------------------

    def start(self) -> Path:
        """問い合わせ → 設定 → ACK を確かめ → 速度を上げ、フォルダを作ってヘッダを書く。"""
        started = self._clock()
        parse_utc(started)
        self.version = parse_mon_ver(self._find_receiver().payload)
        settings = self._outputs + self._signals
        ack = self._ask(valset(settings),
                        lambda m: m.kind in (ACK_ACK, ACK_NAK) and m.payload[:2] == bytes(CFG_VALSET),
                        "受信機が CFG-VALSET に応えません")
        if ack.kind == ACK_NAK:
            raise GnssError("受信機が設定（CFG-VALSET）を断りました（NAK）。キーがこの版の"
                            f"受信機に無いかもしれません: {[n for n, _k, _v in settings]}")
        self._switch(self._log_baud, nmea=0)
        self._ask(frame(MON_VER), lambda m: m.kind == MON_VER,
                  f"{self._log_baud} bps に切り替えた後、受信機が応えません"
                  f"（電源を入れ直すと元の速度に戻ります）")
        name = "gnss-" + parse_utc(started).strftime("%Y%m%dT%H%M%SZ")
        directory = self._root / name
        if directory.exists():
            raise GnssError(f"同じ名前のフォルダが既にあります: {directory}")
        directory.mkdir(parents=True)
        _write_json_atomic(directory / HEADER_FILE, self._header(name, started))
        (directory / RAW_FILE).touch()
        with (directory / INDEX_FILE).open("w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(RAW_INDEX_COLUMNS)
        self.directory = directory
        for pc_utc, chunk in self._pending:
            self._append(chunk, pc_utc)
        self._pending.clear()
        return directory

    def _header(self, name: str, started: str) -> dict[str, Any]:
        assert self.version is not None
        return {
            "format": GNSS_LOG_FORMAT,
            "kind": "gnss_static",
            "log_id": name,
            "started_utc": started,
            "software_commit": self._software_commit,
            "port": self._port_name,
            "baud": self._log_baud,
            "baud_at_start": self.baud_at_start,
            "receiver": {
                "software": self.version.software,
                "hardware": self.version.hardware,
                "firmware": self.version.firmware,
                "extensions": list(self.version.extensions),
            },
            "config": {
                "layers": "RAM",
                "items": [{"name": n, "key": f"{k:#010x}", "value": v}
                          for n, k, v in self._outputs + self._signals
                          + port_items(self._log_baud, 0)],
            },
        }

    def _find_receiver(self) -> UbxMessage:
        """始めの速度で MON-VER を問い、応えなければ記録の速度でも問う（前回の記録が
        書き戻せずに終わると、受信機は電源を切るまで記録の速度のまま）。"""
        bauds = [self._baud] + ([self._log_baud] if self._log_baud != self._baud else [])
        for baud in bauds:
            self._port.baudrate = baud
            try:
                reply = self._ask(frame(MON_VER), lambda m: m.kind == MON_VER, "")
            except GnssError:
                continue
            self.baud_at_start = baud
            return reply
        raise GnssError(f"受信機が MON-VER に応えません（{'・'.join(map(str, bauds))} bps で"
                        "問い合わせました。ポート・電源を確かめてください）")

    def _switch(self, baud: int, nmea: int,
                also: tuple[tuple[str, int, int], ...] = ()) -> None:
        """UART1 の速度と NMEA（と `also`）を RAM 層に書き、送り終わるのを待ってから PC 側も
        替える。待つ間に届いた分も読む（ヘッダの前なら溜め、後なら生ログへ）。"""
        self._port.write(valset(also + port_items(baud, nmea)))
        since = self._ticks()
        while self._ticks() - since < SETTLE_S:
            self.poll()
        self._port.baudrate = baud

    def stop(self) -> None:
        """始めの速度・NMEA ありへ書き戻し、出力と衛星系を出荷時の 0 へ戻す（RAM 層・1 回の
        VALSET）。記録を始める前なら何もしない。"""
        if self.directory is None:
            return
        self._switch(self._baud, nmea=1,
                     also=tuple((n, k, 0) for n, k, _v in self._outputs + self._signals))

    def _ask(self, request: bytes, wanted: Callable[[UbxMessage], bool], what: str) -> UbxMessage:
        """`request` を送り、`wanted` の返事が来るまで `RESEND_S` ごとに送り直す（B-350）。
        前の問いの返事の残り（送り直しで 2 つ返った分）は捨ててから問う。"""
        self._replies.clear()
        self._port.write(request)
        since = sent = self._ticks()
        while (now := self._ticks()) - since <= REPLY_WAIT_S:
            if now - sent >= RESEND_S:
                self._port.write(request)
                sent = now
            self.poll()
            for i, message in enumerate(self._replies):
                if wanted(message):
                    del self._replies[:i + 1]
                    return message
        raise GnssError(what)

    # 読み ---------------------------------------------------------------------

    def poll(self) -> None:
        """ポートから 1 回読み、書いて数える。"""
        chunk = self._port.read(max(1, self._port.in_waiting))
        if not chunk:
            return
        pc_utc = self._clock()
        if self.directory is None:
            self._pending.append((pc_utc, chunk))
        else:
            self._append(chunk, pc_utc)
        for message in self.stream.feed(chunk):
            self._take(message)

    def _append(self, chunk: bytes, pc_utc: str) -> None:
        assert self.directory is not None
        append_raw(self.directory, chunk, pc_utc, raw_file=RAW_FILE, index_file=INDEX_FILE)
        self.bytes_written += len(chunk)

    def _take(self, message: UbxMessage) -> None:
        if self.directory is None and message.kind in (MON_VER, ACK_ACK, ACK_NAK):
            self._replies.append(message)
        try:
            if message.kind == RXM_RAWX:
                self._take_rawx(parse_rawx(message.payload))
            elif message.kind == NAV_PVT:
                self.last_pvt = parse_nav_pvt(message.payload)
                self.counts["pvt"] += 1
            elif message.kind == RXM_SFRBX:
                self.counts["sfrbx"] += 1
        except GnssError:
            self.undecoded += 1                 # 表示の材料が欠けるだけ（生ログには在る）

    def _take_rawx(self, epoch: RawxEpoch) -> None:
        """RAWX の欠けを数える。間隔は見えた最小の差とみなす（測定の間隔は設定しない
        ので知らない）＝最初の差がもう欠けを含むと、その分は数え落とす（目安の数）。"""
        self.counts["rawx"] += 1
        last = self.last_rawx
        self.last_rawx = epoch
        if last is None:
            return
        if epoch.clock_reset or epoch.time_known != last.time_known:
            return                              # 時刻の基準が替わった（確定した瞬間に週 0→今の週）
        delta = epoch.gps_seconds - last.gps_seconds
        if delta <= 0:
            return                              # 時刻が戻った（受信機の再起動）
        if self._rawx_period is None or delta < self._rawx_period:
            self._rawx_period = delta
        self.rawx_gaps += max(0, round(delta / self._rawx_period) - 1)

    # 表示 ---------------------------------------------------------------------

    def status_line(self, elapsed_s: float) -> str:
        minutes, seconds = divmod(int(elapsed_s), 60)
        hours, minutes = divmod(minutes, 60)
        parts = [f"{hours:d}:{minutes:02d}:{seconds:02d}"]
        rawx = self.last_rawx
        parts.append("RAWX まだなし" if rawx is None else
                     f"RAWX 信号 {rawx.signals}（擬似距離 {rawx.pr_valid}・搬送波位相 {rawx.cp_valid}）")
        pvt = self.last_pvt
        parts.append("解 まだなし" if pvt is None else
                     f"解 {pvt.describe()}・衛星 {pvt.satellites}・精度 水平 {pvt.h_acc_m:.2f} m"
                     f"／垂直 {pvt.v_acc_m:.2f} m")
        parts.append(f"RAWX の欠け {self.rawx_gaps}・検査和の不一致 {self.stream.stats.checksum_errors}")
        return "  ".join(parts)

    def summary(self) -> str:
        stats = self.stream.stats
        return (f"記録 {self.bytes_written} バイト・RAWX {self.counts['rawx']} 回"
                f"（欠け {self.rawx_gaps}）・SFRBX {self.counts['sfrbx']}・NAV-PVT {self.counts['pvt']}"
                f"・検査和の不一致 {stats.checksum_errors}・UBX の外 {stats.other_bytes} バイト"
                f"・読めなかった中身 {self.undecoded}")
