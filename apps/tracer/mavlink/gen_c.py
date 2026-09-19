"""
apps/tracer/mavlink/gen_c.py
============================
`radiosim_tracer.xml` から、**ファーム用の C ヘッダ**（`apps/tracer/firmware/main/
tracer_mavlink.h`）を作る（Tracer phase 1・増分5）。

    python -m apps.tracer.mavlink.gen_c           … 作り直す
    python -m apps.tracer.mavlink.gen_c --check   … 作り直すと一致するかだけ見る

🔑 **並び・CRC_EXTRA は `dialect.py` の結果をそのまま使う**＝PC 側と別の計算を
書くと、2 つの実装が同じ XML から別のフレームを作り得る（増分3 の落とし穴 1・2 が
C 側で復活する）。⚠️ 生成物はコミットする（ファームのビルドに Python の手順を
挟まない）。**手で直さない**＝直した日に XML と食い違い、CI の一致検査で落ちる。

**見本のフレーム**（`TRACER_GOLDEN_*`）を一緒に書き出す＝既知の値を、ここ（Python）で
組んだバイト列。
- CI は見本を **PC 側の読み取り（`reader.py`）で復号**して、値が戻ることを確かめる。
- ファームは起動時に**自分のエンコーダで同じバイト列を作れるか**を確かめ、作れなければ
  何も送らない（`tracer_selftest`）。
⇒ **CI に C コンパイラが無くても、C の実装と PC 側の読み取りが同じ見本で結ばれる。**
見本の値は、末尾がゼロ（v2 の切り詰めが起きる）・符号付きが負・全フィールドが別の値、
になるように選ぶ（並びの取り違えが「たまたま同じ値」で隠れないように）。
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path
from typing import Any

from apps.tracer.mavlink.dialect import Dialect, FieldDef, MessageDef, load_dialect, x25_crc

HEADER_PATH = (
    Path(__file__).resolve().parents[1] / "firmware" / "main" / "tracer_mavlink.h"
)

STX_V2 = 0xFD
HEADER_LEN = 10
CHECKSUM_LEN = 2
# 見本のフレームヘッダ（リンク番号・system・component）。PC 側はどれも測定に使わない。
GOLDEN_LINK_SEQ = 0x5A
GOLDEN_SYSID = 1
GOLDEN_COMPID = 1

_C_TYPES = {
    "char": "char",
    "uint8_t": "uint8_t",
    "int8_t": "int8_t",
    "uint16_t": "uint16_t",
    "int16_t": "int16_t",
    "uint32_t": "uint32_t",
    "int32_t": "int32_t",
    "uint64_t": "uint64_t",
    "int64_t": "int64_t",
    "float": "float",
    "double": "double",
}
# 型 → C の読み書きの補助関数の接尾辞（符号付きは符号なしの箱を通す）。
_ACCESSOR = {
    "uint8_t": "u8", "int8_t": "u8",
    "uint16_t": "u16", "int16_t": "u16",
    "uint32_t": "u32", "int32_t": "u32",
    "uint64_t": "u64", "int64_t": "u64",
    "float": "f32", "double": "f64",
}
_UNSIGNED_BOX = {
    "u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t", "u64": "uint64_t",
}


# --- 見本の値とフレーム（Python 側で組む） -----------------------------------


def golden_values(definition: MessageDef) -> dict[str, Any]:
    """見本の値。**最後のフィールドはゼロ**（v2 の末尾切り詰めを必ず通す）。"""
    values: dict[str, Any] = {}
    last = definition.fields[-1].name
    for index, field in enumerate(definition.fields):
        seed = 0x11 * (index + 1)
        if field.name == last:
            values[field.name] = _zero(field)
        elif field.type_name == "char":
            text = f"g{definition.msgid % 1000:03d}{field.name}"[: max(field.count, 1) - 1]
            values[field.name] = text
        elif field.count:
            values[field.name] = tuple(
                _scalar(field, seed + 7 * j + 1) for j in range(field.count)
            )
        else:
            values[field.name] = _scalar(field, seed)
    return values


def _zero(field: FieldDef) -> Any:
    if field.type_name == "char":
        return ""
    if field.count:
        return tuple(0.0 if field.type_name in ("float", "double") else 0
                     for _ in range(field.count))
    return 0.0 if field.type_name in ("float", "double") else 0


def _scalar(field: FieldDef, seed: int) -> Any:
    size = field.element_size
    if field.type_name in ("float", "double"):
        return float(seed) + 0.5                   # 2 進で正確に表せる値
    if field.type_name.startswith("int"):
        return -(seed % (1 << (8 * size - 1)))      # 負（符号の取り違えを隠さない）
    # 全バイトが別の値になるよう、seed を型の幅まで繰り返す。
    value = 0
    for k in range(size):
        value |= ((seed + k * 0x1D) & 0xFF) << (8 * k)
    return value


def encode_payload(definition: MessageDef, values: dict[str, Any]) -> bytes:
    """**ワイヤ上の並び**（`definition.fields`）で詰める。切り詰めはしない。"""
    out = b""
    for field in definition.fields:
        value = values[field.name]
        if field.type_name == "char":
            out += struct.pack("<" + field.fmt, value.encode("ascii"))
        elif field.count:
            out += struct.pack("<" + field.fmt, *value)
        else:
            out += struct.pack("<" + field.fmt, value)
    return out


def encode_frame(
    definition: MessageDef,
    values: dict[str, Any],
    *,
    link_seq: int = GOLDEN_LINK_SEQ,
    sysid: int = GOLDEN_SYSID,
    compid: int = GOLDEN_COMPID,
) -> bytes:
    """v2 のフレーム。**payload 末尾のゼロを切り詰める**（1 バイトは残す）。"""
    payload = encode_payload(definition, values)
    trimmed = payload.rstrip(b"\x00") or payload[:1]
    msgid = definition.msgid
    header = bytes([
        STX_V2, len(trimmed), 0, 0, link_seq & 0xFF, sysid, compid,
        msgid & 0xFF, (msgid >> 8) & 0xFF, (msgid >> 16) & 0xFF,
    ])
    crc = x25_crc(header[1:] + trimmed)
    crc = x25_crc(bytes([definition.crc_extra]), crc)
    return header + trimmed + struct.pack("<H", crc)


# --- C ヘッダ -----------------------------------------------------------------


def _c_name(message: MessageDef) -> str:
    return message.name.lower()                  # TRACER_TX_PACKET → tracer_tx_packet


def _offsets(message: MessageDef) -> list[tuple[FieldDef, int]]:
    result, offset = [], 0
    for field in message.fields:
        result.append((field, offset))
        offset += field.size
    return result


def _struct(message: MessageDef, xml_order: list[str]) -> list[str]:
    # 構造体は XML の順（読む人の順）。ワイヤの並びは encode/decode が持つ。
    by_name = {f.name: f for f in message.fields}
    lines = ["typedef struct {"]
    for name in xml_order:
        field = by_name[name]
        suffix = f"[{field.count}]" if field.count else ""
        lines.append(f"    {_C_TYPES[field.type_name]} {field.name}{suffix};")
    lines.append(f"}} {_c_name(message)}_t;")
    return lines


def _encode_fn(message: MessageDef) -> list[str]:
    name = _c_name(message)
    lines = [
        f"static inline void {name}_encode_payload(uint8_t *p, const {name}_t *m)",
        "{",
    ]
    for field, offset in _offsets(message):
        if field.type_name == "char":
            lines.append(f"    memcpy(p + {offset}, m->{field.name}, {field.count});")
            continue
        acc = _ACCESSOR[field.type_name]
        box = _UNSIGNED_BOX.get(acc)
        cast = f"({box})" if box else ""
        if field.count:
            lines.append(
                f"    for (int i = 0; i < {field.count}; i++) "
                f"tracer_put_{acc}(p + {offset} + i * {field.element_size}, "
                f"{cast}m->{field.name}[i]);"
            )
        else:
            lines.append(f"    tracer_put_{acc}(p + {offset}, {cast}m->{field.name});")
    lines.append("}")
    return lines


def _decode_fn(message: MessageDef) -> list[str]:
    name = _c_name(message)
    size = message.payload_size
    lines = [
        "/* 短い payload（v2 の末尾切り詰め）はゼロで埋め戻し、長い分（拡張）は捨てる。 */",
        f"static inline void {name}_decode_payload(const uint8_t *src, uint8_t len, {name}_t *m)",
        "{",
        f"    uint8_t p[{size}] = {{0}};",
        f"    memcpy(p, src, len < {size} ? len : {size});",
    ]
    for field, offset in _offsets(message):
        ctype = _C_TYPES[field.type_name]
        if field.type_name == "char":
            lines.append(f"    memcpy(m->{field.name}, p + {offset}, {field.count});")
            continue
        acc = _ACCESSOR[field.type_name]
        if field.count:
            lines.append(
                f"    for (int i = 0; i < {field.count}; i++) "
                f"m->{field.name}[i] = ({ctype})tracer_get_{acc}(p + {offset} + i * {field.element_size});"
            )
        else:
            lines.append(f"    m->{field.name} = ({ctype})tracer_get_{acc}(p + {offset});")
    lines.append("}")
    return lines


def _pack_fn(message: MessageDef) -> list[str]:
    name = _c_name(message)
    upper = message.name
    return [
        "/* フレームを frame に組み、長さを返す（frame は TRACER_MAX_FRAME_LEN バイト以上）。 */",
        f"static inline size_t {name}_pack(uint8_t *frame, uint8_t link_seq, uint8_t sysid,",
        f"                                  uint8_t compid, const {name}_t *m)",
        "{",
        f"    {name}_encode_payload(frame + TRACER_HEADER_LEN, m);",
        f"    return tracer_frame_finish(frame, link_seq, sysid, compid, MSGID_{upper},",
        f"                               LEN_{upper}, CRC_EXTRA_{upper});",
        "}",
    ]


def _c_bytes(data: bytes) -> str:
    return ", ".join(f"0x{b:02X}" for b in data)


def _c_value(field: FieldDef, value: Any) -> str:
    if field.type_name == "char":
        return '"' + value + '"'
    if field.count:
        return "{" + ", ".join(_c_scalar(field, v) for v in value) + "}"
    return _c_scalar(field, value)


def _c_scalar(field: FieldDef, value: Any) -> str:
    if field.type_name == "float":
        return f"{value!r}f"
    if field.type_name == "double":
        return repr(value)
    if field.type_name in ("uint64_t", "int64_t"):
        return f"{value}LL" if value < 0 else f"{value}ULL"
    if field.type_name.startswith("uint"):
        return f"{value}u"
    return str(value)


def _golden(message: MessageDef) -> list[str]:
    name = _c_name(message)
    upper = message.name
    values = golden_values(message)
    frame = encode_frame(message, values)
    inits = ", ".join(f".{f.name} = {_c_value(f, values[f.name])}" for f in message.fields)
    return [
        f"static const {name}_t TRACER_GOLDEN_MSG_{upper} = {{ {inits} }};",
        f"static const uint8_t TRACER_GOLDEN_FRAME_{upper}[{len(frame)}] = {{ {_c_bytes(frame)} }};",
    ]


def _selftest(dialect: Dialect) -> list[str]:
    lines = [
        "/* 起動時の自己検査。見本の値を自分のエンコーダで組み、Python 側で組んだ見本と",
        " * 1 バイトも違わないか、さらに見本を解いて組み直すと同じになるかを見る。",
        " * 0＝合格、それ以外＝落ちたメッセージの番号（1 から）。**落ちたら何も送らない**",
        " * ＝PC 側と食い違ったフレームは、読めてはいるが値が違う形で測定に混ざる。 */",
        "static inline int tracer_selftest(void)",
        "{",
        "    uint8_t frame[TRACER_MAX_FRAME_LEN];",
        "    uint8_t again[TRACER_MAX_FRAME_LEN];",
        "    uint32_t msgid;",
        "    const uint8_t *payload;",
        "    uint8_t payload_len;",
        "    size_t n;",
    ]
    for number, message in enumerate(_ordered(dialect), start=1):
        name = _c_name(message)
        upper = message.name
        lines += [
            "    {",
            f"        n = {name}_pack(frame, 0x{GOLDEN_LINK_SEQ:02X}, {GOLDEN_SYSID}, "
            f"{GOLDEN_COMPID}, &TRACER_GOLDEN_MSG_{upper});",
            f"        if (n != sizeof TRACER_GOLDEN_FRAME_{upper} || "
            f"memcmp(frame, TRACER_GOLDEN_FRAME_{upper}, n) != 0) return {number};",
            f"        if (tracer_frame_parse(TRACER_GOLDEN_FRAME_{upper}, n, &msgid, &payload, "
            f"&payload_len) != n || msgid != MSGID_{upper}) return {number};",
            f"        {name}_t decoded;",
            f"        {name}_decode_payload(payload, payload_len, &decoded);",
            f"        if ({name}_pack(again, 0x{GOLDEN_LINK_SEQ:02X}, {GOLDEN_SYSID}, "
            f"{GOLDEN_COMPID}, &decoded) != n || memcmp(again, frame, n) != 0) return {number};",
            "    }",
        ]
    lines += ["    return 0;", "}"]
    return lines


def _ordered(dialect: Dialect) -> list[MessageDef]:
    return sorted(dialect.messages_by_id.values(), key=lambda m: m.msgid)


def _xml_orders(xml_path: Path | None = None) -> dict[str, list[str]]:
    """構造体をXML の順で書くために、メッセージごとのフィールド名の並びを取る。"""
    from xml.etree import ElementTree

    from apps.tracer.mavlink.dialect import DIALECT_XML

    # B314 の抑止: 読むのはリポジトリ内の自前の定義（外から受け取ったものではない）
    root = ElementTree.parse(xml_path or DIALECT_XML).getroot()  # nosec B314
    return {
        (m.get("name") or "").strip(): [
            (f.get("name") or "").strip() for f in m.findall("field")
        ]
        for m in root.findall("./messages/message")
    }


_PRELUDE = """\
/*
 * tracer_mavlink.h — 生成物（手で直さない）
 *
 *   作り直す: python -m apps.tracer.mavlink.gen_c   （リポジトリの直下から）
 *   出所:     apps/tracer/mavlink/radiosim_tracer.xml
 *
 * 並び・CRC_EXTRA は PC 側（apps/tracer/mavlink/dialect.py）と同じ計算の結果を
 * そのまま書き出している。ここを手で直すと XML と食い違い、CI の一致検査で落ちる。
 */
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define TRACER_STX_V2 0xFD
#define TRACER_HEADER_LEN 10
#define TRACER_CHECKSUM_LEN 2
#define TRACER_INCOMPAT_SIGNED 0x01

/* --- リトルエンディアンの読み書き（CPU のエンディアンに依らない） --- */
static inline void tracer_put_u8(uint8_t *p, uint8_t v) { p[0] = v; }
static inline void tracer_put_u16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8);
}
static inline void tracer_put_u32(uint8_t *p, uint32_t v)
{
    for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i));
}
static inline void tracer_put_u64(uint8_t *p, uint64_t v)
{
    for (int i = 0; i < 8; i++) p[i] = (uint8_t)(v >> (8 * i));
}
static inline void tracer_put_f32(uint8_t *p, float v)
{
    uint32_t u; memcpy(&u, &v, 4); tracer_put_u32(p, u);
}
static inline void tracer_put_f64(uint8_t *p, double v)
{
    uint64_t u; memcpy(&u, &v, 8); tracer_put_u64(p, u);
}
static inline uint8_t tracer_get_u8(const uint8_t *p) { return p[0]; }
static inline uint16_t tracer_get_u16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}
static inline uint32_t tracer_get_u32(const uint8_t *p)
{
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) v |= (uint32_t)p[i] << (8 * i);
    return v;
}
static inline uint64_t tracer_get_u64(const uint8_t *p)
{
    uint64_t v = 0;
    for (int i = 0; i < 8; i++) v |= (uint64_t)p[i] << (8 * i);
    return v;
}
static inline float tracer_get_f32(const uint8_t *p)
{
    uint32_t u = tracer_get_u32(p); float v; memcpy(&v, &u, 4); return v;
}
static inline double tracer_get_f64(const uint8_t *p)
{
    uint64_t u = tracer_get_u64(p); double v; memcpy(&v, &u, 8); return v;
}

/* --- X.25 CRC（CRC-16/MCRF4XX） --- */
static inline uint16_t tracer_crc_accumulate(uint8_t b, uint16_t crc)
{
    uint8_t tmp = (uint8_t)(b ^ (uint8_t)(crc & 0xFF));
    tmp ^= (uint8_t)(tmp << 4);
    return (uint16_t)((crc >> 8) ^ ((uint16_t)tmp << 8) ^ ((uint16_t)tmp << 3) ^ (tmp >> 4));
}
static inline uint16_t tracer_crc(const uint8_t *p, size_t n, uint16_t crc)
{
    while (n--) crc = tracer_crc_accumulate(*p++, crc);
    return crc;
}
"""

_FRAME_FUNCS = """\
/* payload（frame + TRACER_HEADER_LEN に full_len バイト）を詰め終えた frame に、
 * ヘッダと CRC を書く。**payload 末尾のゼロを切り詰める**（1 バイトは残す）。 */
static inline size_t tracer_frame_finish(uint8_t *frame, uint8_t link_seq, uint8_t sysid,
                                         uint8_t compid, uint32_t msgid, uint8_t full_len,
                                         uint8_t crc_extra)
{
    uint8_t len = full_len;
    while (len > 1 && frame[TRACER_HEADER_LEN + len - 1] == 0) len--;
    frame[0] = TRACER_STX_V2;
    frame[1] = len;
    frame[2] = 0;                     /* incompat: 署名しない */
    frame[3] = 0;                     /* compat */
    frame[4] = link_seq;
    frame[5] = sysid;
    frame[6] = compid;
    frame[7] = (uint8_t)msgid;
    frame[8] = (uint8_t)(msgid >> 8);
    frame[9] = (uint8_t)(msgid >> 16);
    uint16_t crc = tracer_crc(frame + 1, (size_t)TRACER_HEADER_LEN - 1 + len, 0xFFFF);
    crc = tracer_crc_accumulate(crc_extra, crc);
    frame[TRACER_HEADER_LEN + len] = (uint8_t)crc;
    frame[TRACER_HEADER_LEN + len + 1] = (uint8_t)(crc >> 8);
    return (size_t)TRACER_HEADER_LEN + len + TRACER_CHECKSUM_LEN;
}

/* buf の先頭の 1 フレームを検める。**このダイアレクトの ID で、CRC が合うときだけ**
 * フレームの長さを返し、msgid・payload・payload_len を埋める。それ以外は 0。
 * 署名つきフレームは受けない（Tracer は署名しない＝来たら別の送り手）。 */
static inline size_t tracer_frame_parse(const uint8_t *buf, size_t n, uint32_t *msgid,
                                        const uint8_t **payload, uint8_t *payload_len)
{
    if (n < (size_t)TRACER_HEADER_LEN + TRACER_CHECKSUM_LEN || buf[0] != TRACER_STX_V2) return 0;
    if (buf[2] & TRACER_INCOMPAT_SIGNED) return 0;
    uint8_t len = buf[1];
    size_t total = (size_t)TRACER_HEADER_LEN + len + TRACER_CHECKSUM_LEN;
    if (n < total) return 0;
    uint32_t id = (uint32_t)buf[7] | ((uint32_t)buf[8] << 8) | ((uint32_t)buf[9] << 16);
    int extra = tracer_crc_extra(id);
    if (extra < 0) return 0;
    uint16_t crc = tracer_crc(buf + 1, (size_t)TRACER_HEADER_LEN - 1 + len, 0xFFFF);
    crc = tracer_crc_accumulate((uint8_t)extra, crc);
    if (buf[TRACER_HEADER_LEN + len] != (uint8_t)crc
        || buf[TRACER_HEADER_LEN + len + 1] != (uint8_t)(crc >> 8)) return 0;
    *msgid = id;
    *payload = buf + TRACER_HEADER_LEN;
    *payload_len = len;
    return total;
}
"""


def render(dialect: Dialect | None = None) -> str:
    """C ヘッダの全文。同じ XML からは 1 バイトも違わない文字列を返す。"""
    dialect = dialect or load_dialect()
    messages = _ordered(dialect)
    xml_orders = _xml_orders()
    out: list[str] = [_PRELUDE]

    out.append("/* --- enum --- */")
    for enum_name, entries in dialect.enums.items():
        out.append("typedef enum {")
        for entry_name, value in entries.items():
            out.append(f"    {entry_name} = {value},")
        out.append(f"}} {enum_name};")
        out.append("")

    out.append("/* --- メッセージの ID・payload 長・CRC_EXTRA --- */")
    for message in messages:
        out.append(f"#define MSGID_{message.name} {message.msgid}")
        out.append(f"#define LEN_{message.name} {message.payload_size}")
        out.append(f"#define CRC_EXTRA_{message.name} {message.crc_extra}")
    longest = max(m.payload_size for m in messages)
    out.append(f"#define TRACER_MAX_PAYLOAD_LEN {longest}")
    out.append(
        "#define TRACER_MAX_FRAME_LEN "
        "(TRACER_HEADER_LEN + TRACER_MAX_PAYLOAD_LEN + TRACER_CHECKSUM_LEN)"
    )
    out.append("")
    out.append("/* このダイアレクトの ID なら CRC_EXTRA、知らない ID なら -1。 */")
    out.append("static inline int tracer_crc_extra(uint32_t msgid)")
    out.append("{")
    out.append("    switch (msgid) {")
    for message in messages:
        out.append(f"    case MSGID_{message.name}: return CRC_EXTRA_{message.name};")
    out.append("    default: return -1;")
    out.append("    }")
    out.append("}")
    out.append("")
    out.append(_FRAME_FUNCS)

    for message in messages:
        out.append(f"/* --- {message.name}（ワイヤ上の並び: "
                   + ", ".join(f.name for f in message.fields) + "） --- */")
        out += _struct(message, xml_orders[message.name])
        out.append("")
        out += _encode_fn(message)
        out.append("")
        out += _decode_fn(message)
        out.append("")
        out += _pack_fn(message)
        out.append("")

    out.append("/* --- 見本（Python 側で組んだフレーム・gen_c.py の golden_values） --- */")
    for message in messages:
        out += _golden(message)
    out.append("")
    out += _selftest(dialect)
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m apps.tracer.mavlink.gen_c")
    parser.add_argument("--check", action="store_true",
                        help="書かずに、作り直すと一致するかだけ見る")
    args = parser.parse_args(argv)
    text = render()
    current = HEADER_PATH.read_text(encoding="utf-8") if HEADER_PATH.exists() else None
    if args.check:
        if current != text:
            print(f"古くなっています: {HEADER_PATH}（python -m apps.tracer.mavlink.gen_c）",
                  file=sys.stderr)
            return 1
        return 0
    HEADER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = HEADER_PATH.with_suffix(".h.tmp")
    # 改行を LF に固定（Windows で CRLF になると、生成物が環境で変わる）。
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(HEADER_PATH)
    print(f"書き出しました: {HEADER_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
