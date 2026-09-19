"""
apps/tracer/mavlink/reader.py
=============================
UART のログ（MAVLink v2 のバイト列）を読み、**受信サンプルへ変える**
（Tracer phase 1・増分3）。

    UART のバイト列 → フレーム（CRC で検証）→ メッセージ → RxSample / RadioSettings

**CRC を通らなかったフレームからはサンプルを作らない。** 化けた 1 バイトは
RSSI を数十 dB 動かすので、「読めたことにする」と**もっともらしい値**が測定に混ざる。

🚨 **UART で落ちたフレームは、電波で届かなかったパケットと見分けが付かない。**
どちらも `TRACER_RX_SAMPLE` が 1 つ来ないという形でしか現れず、集計では同じ
「欠け」＝打ち切りの根拠になる（`apps/tracer/aggregate.py`）。⇒ **読み取りの
エラー数を数えて外へ出す**（`ReadStats`）。ここで数えないと、配線の不良が
そのまま「電波が弱かった」という測定結果になる。
⚠️ **この層はしきい値を決めない**＝どれだけ汚れていたら測定を捨てるかは受け入れ
試験（§6.1-3 の段0）で決めることで、コードの定数ではない。

**エラー数は上限であって、少なく出ることはない。** CRC が合わなかったところからは
1 バイト進めて読み直す（同期を取り直す）ので、壊れた 1 フレームが複数回数えられる
ことはある。逆向き（汚れているのに少なく見える）だけは起こさない＝「UART は
きれいだった」と誤って言わせないため。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from apps.tracer.mavlink.dialect import (
    Dialect,
    decode_payload,
    load_dialect,
    x25_crc,
)
from apps.tracer.session import RadioSettings, RxSample, SessionError

STX_V2 = 0xFD
_HEADER_LEN = 10          # STX・payload 長・互換フラグ 2・seq・system・component・msgid 3
_CHECKSUM_LEN = 2
_SIGNATURE_LEN = 13       # 署名つきフレーム（incompat の最下位ビット）
_INCOMPAT_SIGNED = 0x01


@dataclass(frozen=True)
class Message:
    """復号できた 1 メッセージ。

    ⚠️ **`link_seq` は Tracer のシーケンス番号ではない。** これは MAVLink の
    リンク上の通し番号で、**8 ビットで一周する**うえ、送信機が送ったパケットとは
    無関係（UART に流した順でしかない）。打ち切りを数えるのに使ってはいけない＝
    使うと 256 ごとに巻き戻り、受信率が桁違いに狂う。数えるのは payload の `seq`。
    """

    name: str
    msgid: int
    fields: dict[str, Any]
    link_seq: int
    sysid: int
    compid: int


@dataclass
class ReadStats:
    """読み取りの健全性。**測定の解釈に必要な値**であって、ログ出力ではない。"""

    frames: int = 0            # CRC を通り、定義も分かったフレーム
    crc_errors: int = 0        # CRC 不一致（UART の化け）
    unknown_msgid: int = 0     # 定義に無い ID（同じ線に他のダイアレクトが流れている）
    skipped_bytes: int = 0     # フレームの外だったバイト（同期外れ・雑音）
    truncated_tail: int = 0    # 末尾で切れていたバイト（測定中に電源が落ちた形）

    @property
    def error_fraction(self) -> float:
        """CRC 不一致の割合（分母は CRC を見たフレームの数）。

        ⚠️ **判定はしない**＝しきい値は受け入れ試験で決める。ここは数を出すだけ。
        """
        seen = self.frames + self.crc_errors
        return 0.0 if seen == 0 else self.crc_errors / seen


def iter_messages(
    data: bytes, stats: ReadStats | None = None, dialect: Dialect | None = None
) -> Iterator[Message]:
    """バイト列から、**CRC を通ったメッセージだけ**を順に返す。

    知らない ID のフレームは、CRC を検証できない（CRC_EXTRA を持たないため）ので
    宣言された長さのぶんだけ読み飛ばす。⚠️ ここで 1 バイトずつ進めると、他機の
    正常なフレームの中身を雑音として読み直し、**CRC 不一致の数が水増しされる**
    （その数は測定の解釈に使うので、汚さない）。
    """
    stats = stats if stats is not None else ReadStats()
    messages, consumed = _parse(data, stats, dialect or load_dialect())
    # 末尾で切れている。**残りを雑音として数えない**＝測定が途中で止まったことと、
    # 線が汚れていることは別の事実。
    stats.truncated_tail += len(data) - consumed
    yield from messages


class FrameStream:
    """**細切れに届く**バイト列を読む（UART は 1 回の読み出しでフレームの途中まで
    しか来ないことが普通にある）。

    フレームの途中で切れた分は次の `feed` まで持ち越す。⚠️ 持ち越しを捨てると、
    **読み出しの区切りに跨ったフレームだけ**が落ち、落ちた数は読み出しの間隔で
    変わる＝電波と無関係な「欠け」が打ち切りの根拠に混ざる。
    どう区切って渡しても、1 回で渡したときと**同じメッセージ・同じエラー数**になる。
    """

    def __init__(self, stats: ReadStats | None = None, dialect: Dialect | None = None):
        self.stats = stats if stats is not None else ReadStats()
        self._dialect = dialect or load_dialect()
        self._pending = b""
        self._outside = bytearray()
        self._lines: list[str] = []

    def feed(self, data: bytes) -> list[Message]:
        buffer = self._pending + data
        messages, consumed = _parse(buffer, self.stats, self._dialect, self._outside)
        self._pending = buffer[consumed:]
        *done, rest = self._outside.split(b"\n")
        for line in done:
            text = line.rstrip(b"\r").decode("utf-8", errors="replace").strip()
            if text:
                self._lines.append(text)
        self._outside = bytearray(rest)
        return messages

    def take_lines(self) -> list[str]:
        """フレームの外に流れた**文字の行**（コマンドへの返答 `OK`/`ERR`・連続送信の報告
        `CONT`）を、届いた順に取り出す（取り出した分は消える）。

        ファームは文字の行を 1 行ずつ丸ごと書き、行の中に STX（0xFD）は現れない
        （UTF-8 は 0xF8 以上のバイトを使わない）。⚠️ 化けたフレームの読み飛ばしも
        ここに混ざり得るので、呼び手は行の頭（`OK`・`ERR`・`CONT`）で選ぶこと。
        """
        lines, self._lines = self._lines, []
        return lines

    def close(self) -> None:
        """終わり。持ち越しが残っていれば、それは末尾で切れたフレーム。"""
        self.stats.truncated_tail += len(self._pending)
        self._pending = b""


def _parse(
    data: bytes, stats: ReadStats, dialect: Dialect, outside: bytearray | None = None
) -> tuple[list[Message], int]:
    """読めるところまで読み、`(メッセージ, 読み終えたバイト数)` を返す。

    末尾の不完全なフレームは**読み終えたことにしない**（数えもしない）＝続きが
    来るかどうかは呼び手にしか分からない。`outside` を渡すと、フレームの外だった
    バイト（STX 以外で読み飛ばしたもの）をそこへ足す＝文字の行を拾うため。
    """
    messages: list[Message] = []
    index = 0
    size = len(data)
    while index < size:
        if data[index] != STX_V2:
            stats.skipped_bytes += 1
            if outside is not None:
                outside.append(data[index])
            index += 1
            continue
        if index + _HEADER_LEN > size:
            break
        payload_len = data[index + 1]
        incompat = data[index + 2]
        total = _HEADER_LEN + payload_len + _CHECKSUM_LEN
        if incompat & _INCOMPAT_SIGNED:
            total += _SIGNATURE_LEN
        if index + total > size:
            break
        msgid = int.from_bytes(data[index + 7:index + 10], "little")
        definition = dialect.message(msgid)
        if definition is None:
            stats.unknown_msgid += 1
            index += total
            continue
        payload = data[index + _HEADER_LEN:index + _HEADER_LEN + payload_len]
        expected = int.from_bytes(
            data[index + _HEADER_LEN + payload_len:][:_CHECKSUM_LEN], "little"
        )
        crc = x25_crc(data[index + 1:index + _HEADER_LEN + payload_len])
        crc = x25_crc(bytes([definition.crc_extra]), crc)
        if crc != expected:
            # 同期を取り直す＝STX の次から。ここを `index += total` にすると、
            # 化けた長さを信じて**正しいフレームを丸ごと飛ばす**。
            stats.crc_errors += 1
            stats.skipped_bytes += 1
            index += 1
            continue
        stats.frames += 1
        messages.append(Message(
            name=definition.name,
            msgid=msgid,
            fields=decode_payload(definition, payload),
            link_seq=data[index + 4],
            sysid=data[index + 5],
            compid=data[index + 6],
        ))
        index += total
    return messages, index


def read_log(
    data: bytes, dialect: Dialect | None = None
) -> tuple[list[Message], ReadStats]:
    """ログ全体を読む（小さなログ向け。長い測定は `iter_messages` で流す）。"""
    stats = ReadStats()
    return list(iter_messages(data, stats, dialect)), stats


# --- メッセージ → セッションの型 ----------------------------------------------


def mac(value: tuple[int, ...] | list[int]) -> str:
    """個体 ID（MAC アドレス）を `aa:bb:cc:dd:ee:01` の形にする。"""
    return ":".join(f"{b:02x}" for b in value)


def to_rx_sample(message: Message, pc_utc: str, spatial_slot: int = 0) -> RxSample:
    """`TRACER_RX_SAMPLE` を受信サンプルへ。

    **PC 側が足すのは 2 つだけ**＝受けた時刻（`pc_utc`）と空間平均の置き場所
    （作業者の操作に由来するのでファームは知らない）。⛔ 生値には触らない。
    """
    if message.name != "TRACER_RX_SAMPLE":
        raise SessionError(
            f"受信サンプルではないメッセージです: {message.name}"
        )
    f = message.fields
    return RxSample(
        pc_utc=pc_utc,
        radio_time_us=f["rx_time_us"],
        # ⚠️ ここは payload の seq。フレームの `link_seq` ではない（8 ビットで一周する）。
        seq=f["seq"],
        tx_id=mac(f["tx_id"]),
        rssi_raw=f["rssi_raw"],
        noise_floor_raw=f["noise_floor_raw"],
        config_id=f["config_id"],
        tx_config_id=f["tx_config_id"],
        sample_seq=f["sample_seq"],
        spatial_slot=spatial_slot,
    )


def to_radio_settings(
    message: Message, sensitivity_dbm: float | None, dialect: Dialect | None = None
) -> RadioSettings:
    """`TRACER_CONFIG` を無線設定へ（セッションのヘッダを組むときに使う）。

    **受信感度は引数**＝ファームは自分の感度を知らない（データシートと受け入れ
    試験で決める値）。`meas_dbm` を出さない窓の「感度以下（〜未満）」の文面に
    そのまま乗るので、ここで既定値をでっち上げない。TX の設定なら `None`（受けない）。
    """
    if message.name != "TRACER_CONFIG":
        raise SessionError(f"設定メッセージではありません: {message.name}")
    dialect = dialect or load_dialect()
    f = message.fields
    return RadioSettings(
        config_id=f["config_id"],
        firmware_version=f["firmware_version"],
        channel=f["channel"],
        rate_kbps=f["rate_kbps"],
        tx_power_cdbm=f["tx_power_cdbm"],
        tx_interval_ms=f["tx_interval_ms"],
        detector=dialect.label("TRACER_DETECTOR", f["detector"]),
        integration_window_us=f["integration_window_us"],
        integration_samples=f["integration_samples"],
        antenna=dialect.label("TRACER_ANTENNA", f["antenna"]),
        sensitivity_dbm=sensitivity_dbm,
    )


def rx_samples(
    messages: list[Message], pc_utc_of: Any, spatial_slot: int = 0
) -> list[RxSample]:
    """受信サンプルだけを取り出して変換する（他のメッセージは読み飛ばす）。

    `pc_utc_of` は「メッセージ → PC が受けた時刻（ISO 8601）」の関数。
    ⚠️ **ログを後から読むときの時刻は、測りながら付けた時刻と同じではない。**
    ここを `datetime.now()` で埋める既定は置かない（読み直した日時が測定時刻として
    残る＝後から見分けが付かない）。
    """
    return [
        to_rx_sample(m, pc_utc_of(m), spatial_slot)
        for m in messages
        if m.name == "TRACER_RX_SAMPLE"
    ]
