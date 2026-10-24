"""
apps/field/mavlink/dialect.py
==============================
`radiosim_field.xml` を読んで、**フレームの解釈そのもの**（フィールドの並び・
サイズ・CRC_EXTRA）を組み立てる（Field phase 1・増分3）。

🔑 **XML が単一の出所**＝ファームと PC 側の両方がここから生成する、と XML 自身が
宣言している。⇒ **この層は表を持たない。** メッセージ名・フィールド名・型・enum の
語彙を Python 側にも書き写すと、XML を直したときに片方だけが古くなり、**取り込んだ
値が黙って別の意味になる**（気づけるのは測定が終わった後）。

**MAVLink の落とし穴 3 つ**（どれも外すと「読めてはいるが値が違う」形で壊れる）

1. **ワイヤ上の並びは XML の並びではない。** 送る前に**型のサイズの降順**へ並べ替える
   （同じサイズの中では XML の順を保つ）。XML の順で解くと、全フィールドが隣の
   フィールドの値を拾ったまま**例外を出さずに**復号できてしまう。
2. **CRC_EXTRA** は、メッセージ名と（並べ替えた後の）フィールドの型・名前から作る
   1 バイト。これが合わないとフレームは弾かれる＝**定義の食い違いを検出する仕掛け**
   そのものなので、ここを手で書いた定数にすると仕掛けが死ぬ。
3. **v2 は payload 末尾の 0 を切り捨てて送る。** 受け側で**ゼロで埋め戻す**必要が
   ある。埋め戻しを忘れると、末尾が 0 のサンプル（RSSI 生値 0 など）だけが
   選択的に読めなくなる。

**拡張フィールド**（`<extensions/>` より後）は並べ替えず末尾に置き、CRC_EXTRA にも
入れない。⇒ **ファームが後からフィールドを足しても、古い PC 側がフレームを弾かない**
（前方互換）。いま XML に拡張は無いが、足された日に静かに壊れないよう先に実装する。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

DIALECT_XML = Path(__file__).resolve().parent / "radiosim_field.xml"


class DialectError(Exception):
    """XML の定義が読めない／知らない型が出てきたとき。"""


# MAVLink の型 → （1 要素のバイト数, struct の書式）。**リトルエンディアン固定**。
_TYPES: dict[str, tuple[int, str]] = {
    "char": (1, "s"),
    "uint8_t": (1, "B"),
    "int8_t": (1, "b"),
    "uint16_t": (2, "H"),
    "int16_t": (2, "h"),
    "uint32_t": (4, "I"),
    "int32_t": (4, "i"),
    "uint64_t": (8, "Q"),
    "int64_t": (8, "q"),
    "float": (4, "f"),
    "double": (8, "d"),
}


@dataclass(frozen=True)
class FieldDef:
    """1 フィールドの定義。`count` が 0 なら配列ではない。"""

    name: str
    type_name: str               # 配列の添字を外した基本型（"uint8_t" など）
    count: int                   # 配列の要素数（0＝スカラ）
    extension: bool              # `<extensions/>` より後か

    @property
    def element_size(self) -> int:
        return _TYPES[self.type_name][0]

    @property
    def size(self) -> int:
        return self.element_size * max(self.count, 1)

    @property
    def fmt(self) -> str:
        code = _TYPES[self.type_name][1]
        if self.type_name == "char":
            # char[16] は 1 個の固定長バイト列として取る（1 文字ずつではない）。
            return f"{max(self.count, 1)}s"
        return code if self.count == 0 else f"{self.count}{code}"


@dataclass(frozen=True)
class MessageDef:
    """1 メッセージの定義。`fields` は**ワイヤ上の並び**。"""

    msgid: int
    name: str
    fields: tuple[FieldDef, ...]
    crc_extra: int

    @property
    def payload_size(self) -> int:
        return sum(f.size for f in self.fields)


@dataclass(frozen=True)
class Dialect:
    """XML 1 本ぶん。メッセージと enum の語彙を持つ。"""

    messages_by_id: dict[int, MessageDef]
    messages_by_name: dict[str, MessageDef]
    enums: dict[str, dict[str, int]]

    def message(self, msgid: int) -> MessageDef | None:
        """知らない ID には `None` を返す（同じ UART に他のダイアレクトが流れる）。"""
        return self.messages_by_id.get(msgid)

    def label(self, enum_name: str, value: int) -> str:
        """enum の値 → 語彙（`RADIOSIM_FIELD_DETECTOR_MEAN` → `"mean"`）。

        ⚠️ **接頭辞を外して小文字にしただけの機械的な対応**＝`session.py` の語彙
        （`DETECTORS` など）と同じ字になるように XML 側の名前を付けてある。手で表を
        持つと、XML に entry を足した日に片方だけが古くなる。
        """
        entries = self.enums.get(enum_name)
        if entries is None:
            raise DialectError(f"知らない enum です: {enum_name}")
        for name, entry_value in entries.items():
            if entry_value == value:
                return name[len(enum_name) + 1:].lower()
        raise DialectError(
            f"{enum_name} に値 {value} の entry がありません"
            f"（XML に足したのに PC 側が古い、の形）"
        )


# --- XML → 定義 ---------------------------------------------------------------


def _parse_field(element: ElementTree.Element, extension: bool) -> FieldDef:
    raw = (element.get("type") or "").strip()
    name = (element.get("name") or "").strip()
    if not raw or not name:
        raise DialectError(f"型か名前の無いフィールドがあります: {element.attrib}")
    count = 0
    if raw.endswith("]"):
        base, _, length = raw[:-1].partition("[")
        raw = base
        try:
            count = int(length)
        except ValueError as e:
            raise DialectError(f"配列の長さが数ではありません: {name}") from e
        if count < 1:
            raise DialectError(f"配列の長さが 1 未満です: {name}")
    if raw not in _TYPES:
        raise DialectError(f"知らない型です: {raw}（フィールド {name}）")
    return FieldDef(name=name, type_name=raw, count=count, extension=extension)


def _wire_order(fields: list[FieldDef]) -> tuple[FieldDef, ...]:
    """ワイヤ上の並び＝**基本フィールドを型のサイズの降順**（同サイズは XML の順）。

    拡張フィールドは並べ替えずに末尾へ付ける（前方互換のため）。
    """
    base = [f for f in fields if not f.extension]
    extensions = [f for f in fields if f.extension]
    ordered = sorted(base, key=lambda f: -f.element_size)   # sorted は安定
    return tuple(ordered + extensions)


def _crc_extra(name: str, fields: tuple[FieldDef, ...]) -> int:
    """メッセージ名と（並べ替え後の）基本フィールドから作る 1 バイト。

    **拡張フィールドは入れない**＝入れてしまうと、ファームがフィールドを足した
    瞬間に古い PC 側が全フレームを CRC 不一致で捨てる（前方互換が壊れる）。
    """
    crc = x25_crc(f"{name} ".encode("ascii"))
    for field in fields:
        if field.extension:
            continue
        crc = x25_crc(f"{field.type_name} ".encode("ascii"), crc)
        crc = x25_crc(f"{field.name} ".encode("ascii"), crc)
        if field.count:
            crc = x25_crc(bytes([field.count]), crc)
    return (crc & 0xFF) ^ (crc >> 8)


def x25_crc(data: bytes, crc: int = 0xFFFF) -> int:
    """MAVLink の X.25 CRC（CRC-16/MCRF4XX）。続きから積めるよう `crc` を受ける。"""
    for byte in data:
        tmp = (byte ^ (crc & 0xFF)) & 0xFF
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


@lru_cache(maxsize=None)
def load_dialect(path: str | None = None) -> Dialect:
    """XML を読んで定義を組み立てる（同じ XML は 1 回だけ読む）。"""
    xml_path = Path(path) if path else DIALECT_XML
    try:
        # B314 の抑止: 読むのはリポジトリ内の自前の定義（外から受け取ったものではない）
        root = ElementTree.parse(xml_path).getroot()  # nosec B314
    except FileNotFoundError as e:
        raise DialectError(f"ダイアレクトの XML がありません: {xml_path}") from e
    except ElementTree.ParseError as e:
        raise DialectError(f"ダイアレクトの XML が壊れています: {xml_path}（{e}）") from e

    enums: dict[str, dict[str, int]] = {}
    for enum in root.findall("./enums/enum"):
        enum_name = (enum.get("name") or "").strip()
        entries: dict[str, int] = {}
        for entry in enum.findall("entry"):
            entry_name = (entry.get("name") or "").strip()
            value = entry.get("value")
            if not entry_name or value is None:
                raise DialectError(f"{enum_name} に名前か値の無い entry があります")
            entries[entry_name] = int(value)
        enums[enum_name] = entries

    by_id: dict[int, MessageDef] = {}
    by_name: dict[str, MessageDef] = {}
    for message in root.findall("./messages/message"):
        name = (message.get("name") or "").strip()
        msgid = int(message.get("id") or -1)
        fields: list[FieldDef] = []
        extension = False
        for child in message:
            if child.tag == "extensions":
                extension = True
            elif child.tag == "field":
                fields.append(_parse_field(child, extension))
        if not fields:
            raise DialectError(f"{name} にフィールドがありません")
        ordered = _wire_order(fields)
        definition = MessageDef(
            msgid=msgid, name=name, fields=ordered, crc_extra=_crc_extra(name, ordered)
        )
        if msgid in by_id:
            # 同じ ID を 2 つのメッセージが名乗ると、受けた側は片方を他方として
            # 復号する＝例外は出ないまま値だけが入れ替わる。
            raise DialectError(f"メッセージ ID が重複しています: {msgid}")
        by_id[msgid] = definition
        by_name[name] = definition
    if not by_id:
        raise DialectError(f"メッセージが 1 つもありません: {xml_path}")
    return Dialect(messages_by_id=by_id, messages_by_name=by_name, enums=enums)


# --- payload の復号 -----------------------------------------------------------


def decode_payload(definition: MessageDef, payload: bytes) -> dict[str, Any]:
    """payload を辞書へ。**短ければゼロで埋め、長ければ切る。**

    - 短い＝v2 の末尾ゼロ切り捨て。埋め戻さないと、値が 0 のフィールドを持つ
      サンプルだけが選択的に読めなくなる。
    - 長い＝ファームが後から足した拡張フィールド。**知らない分は捨てて読み進める**
      （新しいファームのログを古い PC 側が読める＝前方互換）。
    """
    size = definition.payload_size
    if len(payload) < size:
        payload = payload + bytes(size - len(payload))
    values: dict[str, Any] = {}
    offset = 0
    for field in definition.fields:
        chunk = struct.unpack_from("<" + field.fmt, payload, offset)
        offset += field.size
        if field.type_name == "char":
            raw = chunk[0]
            # 末尾の詰め物（NUL）を落とす。ASCII 以外はファームの版が化けている
            # 証拠なので、黙って捨てずに置換文字で残す。
            values[field.name] = raw.split(b"\x00", 1)[0].decode("ascii", "replace")
        elif field.count:
            values[field.name] = tuple(chunk)
        else:
            values[field.name] = chunk[0]
    return values
