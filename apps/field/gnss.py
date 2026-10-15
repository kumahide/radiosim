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
  2. `CFG-VALSET` を **RAM 層だけ**に書く＝電源を切れば元の設定に戻り、受信機に
     恒久の変更を残さない。
  3. ACK を確かめる。NAK・無返答なら止める（フォルダは作らない）。
  4. ヘッダを書き、**ここまでに受けたバイトを生ログの先頭に書く**（捨てない）。

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


class GnssError(SessionError):
    """受信機が応えない・設定を断った・ログの約束が破れたとき。"""


class Port(Protocol):
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


def valset(items: tuple[tuple[str, int, int], ...], layers: int = LAYER_RAM) -> bytes:
    """`CFG-VALSET` のフレーム。値が 1 バイトのキーだけを受け付ける（キーの 28〜30 ビット＝2）。"""
    payload = struct.pack("<BBxx", 0, layers)
    for name, key, value in items:
        if (key >> 28) & 0x7 != 2 or not 0 <= value <= 0xFF:
            raise GnssError(f"1 バイトの値のキーだけを書けます: {name}（{key:#010x}＝{value}）")
        payload += struct.pack("<IB", key, value)
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

    @property
    def gps_seconds(self) -> float:
        return self.week * 604800 + self.rcv_tow


def parse_rawx(payload: bytes) -> RawxEpoch:
    if len(payload) < 16:
        raise GnssError(f"RXM-RAWX が短すぎます: {len(payload)} バイト")
    rcv_tow, week, _leap_s, count = struct.unpack_from("<dHbB", payload, 0)
    if len(payload) != 16 + 32 * count:
        raise GnssError(f"RXM-RAWX の長さが信号数 {count} と合いません: {len(payload)} バイト")
    tracking = [payload[16 + 32 * i + 30] for i in range(count)]
    return RawxEpoch(week, rcv_tow, count,
                     sum(1 for t in tracking if t & 0x01), sum(1 for t in tracking if t & 0x02))


_FIX_TYPES = {0: "解なし", 1: "推測航法のみ", 2: "2D", 3: "3D", 4: "3D＋推測航法", 5: "時刻のみ"}
_CARRIER = {1: "RTK Float", 2: "RTK Fix"}


@dataclass(frozen=True)
class PvtStatus:
    """NAV-PVT のうち、画面に出す分。**ここの解は記録しない**（本番は後処理）。"""

    fix_type: int
    fix_ok: bool                  # gnssFixOK（受信機が解を有効とみなしたか）
    carrier: int                  # 0 なし・1 Float・2 Fix
    satellites: int
    h_acc_m: float
    v_acc_m: float

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
    fix_type, flags, _flags2, satellites = struct.unpack_from("<BBBB", payload, 20)
    h_acc, v_acc = struct.unpack_from("<II", payload, 40)
    return PvtStatus(fix_type, bool(flags & 0x01), (flags >> 6) & 0x03, satellites,
                     h_acc / 1000, v_acc / 1000)


# --- 記録器 --------------------------------------------------------------------


class GnssLogger:
    """受信機に問い合わせて設定し、届いたバイト列をそのまま書く。

    `clock` は PC の UTC（`recorder.now_utc` と同じ時計）、`ticks` は待ちの上限を
    測る単調な時計。どちらも呼び手が渡す（テストで差し替えるため）。
    """

    def __init__(
        self, root: str | Path, port: Port, *, software_commit: str, port_name: str,
        baud: int, clock: Callable[[], str], ticks: Callable[[], float] = time.monotonic,
        outputs: tuple[tuple[str, int, int], ...] = OUTPUTS,
    ):
        if not software_commit:
            raise GnssError("ソフトウェアのコミットが空です（刻印の 1 項目）")
        self._root = Path(root)
        self._port = port
        self._software_commit = software_commit
        self._port_name = port_name
        self._baud = baud
        self._clock = clock
        self._ticks = ticks
        self._outputs = outputs
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
        """問い合わせ → 設定 → ACK を確かめ、フォルダを作ってヘッダを書く。"""
        started = self._clock()
        parse_utc(started)
        self._port.write(frame(MON_VER))
        ver = self._wait(lambda m: m.kind == MON_VER,
                         "受信機が MON-VER に応えません（ポート・ボーレート・電源を確かめてください）")
        self.version = parse_mon_ver(ver.payload)
        self._port.write(valset(self._outputs))
        ack = self._wait(lambda m: m.kind in (ACK_ACK, ACK_NAK)
                         and m.payload[:2] == bytes(CFG_VALSET),
                         "受信機が CFG-VALSET に応えません")
        if ack.kind == ACK_NAK:
            raise GnssError("受信機が設定（CFG-VALSET）を断りました（NAK）。キーがこの版の"
                            f"受信機に無いかもしれません: {[n for n, _k, _v in self._outputs]}")
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
            "baud": self._baud,
            "receiver": {
                "software": self.version.software,
                "hardware": self.version.hardware,
                "firmware": self.version.firmware,
                "extensions": list(self.version.extensions),
            },
            "config": {
                "layers": "RAM",
                "items": [{"name": n, "key": f"{k:#010x}", "value": v}
                          for n, k, v in self._outputs],
            },
        }

    def _wait(self, wanted: Callable[[UbxMessage], bool], what: str) -> UbxMessage:
        since = self._ticks()
        while self._ticks() - since <= REPLY_WAIT_S:
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
