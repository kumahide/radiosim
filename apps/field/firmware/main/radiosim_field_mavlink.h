/*
 * radiosim_field_mavlink.h — 生成物（手で直さない）
 *
 *   作り直す: python -m apps.field.mavlink.gen_c   （リポジトリの直下から）
 *   出所:     apps/field/mavlink/radiosim_field.xml
 *
 * 並び・CRC_EXTRA は PC 側（apps/field/mavlink/dialect.py）と同じ計算の結果を
 * そのまま書き出している。ここを手で直すと XML と食い違い、CI の一致検査で落ちる。
 */
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define RADIOSIM_FIELD_STX_V2 0xFD
#define RADIOSIM_FIELD_HEADER_LEN 10
#define RADIOSIM_FIELD_CHECKSUM_LEN 2
#define RADIOSIM_FIELD_INCOMPAT_SIGNED 0x01

/* --- リトルエンディアンの読み書き（CPU のエンディアンに依らない） --- */
static inline void radiosim_field_put_u8(uint8_t *p, uint8_t v) { p[0] = v; }
static inline void radiosim_field_put_u16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8);
}
static inline void radiosim_field_put_u32(uint8_t *p, uint32_t v)
{
    for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i));
}
static inline void radiosim_field_put_u64(uint8_t *p, uint64_t v)
{
    for (int i = 0; i < 8; i++) p[i] = (uint8_t)(v >> (8 * i));
}
static inline void radiosim_field_put_f32(uint8_t *p, float v)
{
    uint32_t u; memcpy(&u, &v, 4); radiosim_field_put_u32(p, u);
}
static inline void radiosim_field_put_f64(uint8_t *p, double v)
{
    uint64_t u; memcpy(&u, &v, 8); radiosim_field_put_u64(p, u);
}
static inline uint8_t radiosim_field_get_u8(const uint8_t *p) { return p[0]; }
static inline uint16_t radiosim_field_get_u16(const uint8_t *p)
{
    return (uint16_t)(p[0] | (p[1] << 8));
}
static inline uint32_t radiosim_field_get_u32(const uint8_t *p)
{
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) v |= (uint32_t)p[i] << (8 * i);
    return v;
}
static inline uint64_t radiosim_field_get_u64(const uint8_t *p)
{
    uint64_t v = 0;
    for (int i = 0; i < 8; i++) v |= (uint64_t)p[i] << (8 * i);
    return v;
}
static inline float radiosim_field_get_f32(const uint8_t *p)
{
    uint32_t u = radiosim_field_get_u32(p); float v; memcpy(&v, &u, 4); return v;
}
static inline double radiosim_field_get_f64(const uint8_t *p)
{
    uint64_t u = radiosim_field_get_u64(p); double v; memcpy(&v, &u, 8); return v;
}

/* --- X.25 CRC（CRC-16/MCRF4XX） --- */
static inline uint16_t radiosim_field_crc_accumulate(uint8_t b, uint16_t crc)
{
    uint8_t tmp = (uint8_t)(b ^ (uint8_t)(crc & 0xFF));
    tmp ^= (uint8_t)(tmp << 4);
    return (uint16_t)((crc >> 8) ^ ((uint16_t)tmp << 8) ^ ((uint16_t)tmp << 3) ^ (tmp >> 4));
}
static inline uint16_t radiosim_field_crc(const uint8_t *p, size_t n, uint16_t crc)
{
    while (n--) crc = radiosim_field_crc_accumulate(*p++, crc);
    return crc;
}

/* --- enum --- */
typedef enum {
    RADIOSIM_FIELD_ROLE_TX = 0,
    RADIOSIM_FIELD_ROLE_RX = 1,
} RADIOSIM_FIELD_ROLE;

typedef enum {
    RADIOSIM_FIELD_DETECTOR_UNKNOWN = 0,
    RADIOSIM_FIELD_DETECTOR_INSTANT = 1,
    RADIOSIM_FIELD_DETECTOR_MEAN = 2,
} RADIOSIM_FIELD_DETECTOR;

typedef enum {
    RADIOSIM_FIELD_ANTENNA_UNKNOWN = 0,
    RADIOSIM_FIELD_ANTENNA_INTERNAL = 1,
    RADIOSIM_FIELD_ANTENNA_EXTERNAL = 2,
} RADIOSIM_FIELD_ANTENNA;

/* --- メッセージの ID・payload 長・CRC_EXTRA --- */
#define MSGID_RADIOSIM_FIELD_TX_PACKET 42100
#define LEN_RADIOSIM_FIELD_TX_PACKET 20
#define CRC_EXTRA_RADIOSIM_FIELD_TX_PACKET 210
#define MSGID_RADIOSIM_FIELD_RX_SAMPLE 42101
#define LEN_RADIOSIM_FIELD_RX_SAMPLE 36
#define CRC_EXTRA_RADIOSIM_FIELD_RX_SAMPLE 158
#define MSGID_RADIOSIM_FIELD_CONFIG 42102
#define LEN_RADIOSIM_FIELD_CONFIG 40
#define CRC_EXTRA_RADIOSIM_FIELD_CONFIG 17
#define RADIOSIM_FIELD_MAX_PAYLOAD_LEN 40
#define RADIOSIM_FIELD_MAX_FRAME_LEN (RADIOSIM_FIELD_HEADER_LEN + RADIOSIM_FIELD_MAX_PAYLOAD_LEN + RADIOSIM_FIELD_CHECKSUM_LEN)

/* このダイアレクトの ID なら CRC_EXTRA、知らない ID なら -1。 */
static inline int radiosim_field_crc_extra(uint32_t msgid)
{
    switch (msgid) {
    case MSGID_RADIOSIM_FIELD_TX_PACKET: return CRC_EXTRA_RADIOSIM_FIELD_TX_PACKET;
    case MSGID_RADIOSIM_FIELD_RX_SAMPLE: return CRC_EXTRA_RADIOSIM_FIELD_RX_SAMPLE;
    case MSGID_RADIOSIM_FIELD_CONFIG: return CRC_EXTRA_RADIOSIM_FIELD_CONFIG;
    default: return -1;
    }
}

/* payload（frame + RADIOSIM_FIELD_HEADER_LEN に full_len バイト）を詰め終えた frame に、
 * ヘッダと CRC を書く。**payload 末尾のゼロを切り詰める**（1 バイトは残す）。 */
static inline size_t radiosim_field_frame_finish(uint8_t *frame, uint8_t link_seq, uint8_t sysid,
                                         uint8_t compid, uint32_t msgid, uint8_t full_len,
                                         uint8_t crc_extra)
{
    uint8_t len = full_len;
    while (len > 1 && frame[RADIOSIM_FIELD_HEADER_LEN + len - 1] == 0) len--;
    frame[0] = RADIOSIM_FIELD_STX_V2;
    frame[1] = len;
    frame[2] = 0;                     /* incompat: 署名しない */
    frame[3] = 0;                     /* compat */
    frame[4] = link_seq;
    frame[5] = sysid;
    frame[6] = compid;
    frame[7] = (uint8_t)msgid;
    frame[8] = (uint8_t)(msgid >> 8);
    frame[9] = (uint8_t)(msgid >> 16);
    uint16_t crc = radiosim_field_crc(frame + 1, (size_t)RADIOSIM_FIELD_HEADER_LEN - 1 + len, 0xFFFF);
    crc = radiosim_field_crc_accumulate(crc_extra, crc);
    frame[RADIOSIM_FIELD_HEADER_LEN + len] = (uint8_t)crc;
    frame[RADIOSIM_FIELD_HEADER_LEN + len + 1] = (uint8_t)(crc >> 8);
    return (size_t)RADIOSIM_FIELD_HEADER_LEN + len + RADIOSIM_FIELD_CHECKSUM_LEN;
}

/* buf の先頭の 1 フレームを検める。**このダイアレクトの ID で、CRC が合うときだけ**
 * フレームの長さを返し、msgid・payload・payload_len を埋める。それ以外は 0。
 * 署名つきフレームは受けない（Field は署名しない＝来たら別の送り手）。 */
static inline size_t radiosim_field_frame_parse(const uint8_t *buf, size_t n, uint32_t *msgid,
                                        const uint8_t **payload, uint8_t *payload_len)
{
    if (n < (size_t)RADIOSIM_FIELD_HEADER_LEN + RADIOSIM_FIELD_CHECKSUM_LEN || buf[0] != RADIOSIM_FIELD_STX_V2) return 0;
    if (buf[2] & RADIOSIM_FIELD_INCOMPAT_SIGNED) return 0;
    uint8_t len = buf[1];
    size_t total = (size_t)RADIOSIM_FIELD_HEADER_LEN + len + RADIOSIM_FIELD_CHECKSUM_LEN;
    if (n < total) return 0;
    uint32_t id = (uint32_t)buf[7] | ((uint32_t)buf[8] << 8) | ((uint32_t)buf[9] << 16);
    int extra = radiosim_field_crc_extra(id);
    if (extra < 0) return 0;
    uint16_t crc = radiosim_field_crc(buf + 1, (size_t)RADIOSIM_FIELD_HEADER_LEN - 1 + len, 0xFFFF);
    crc = radiosim_field_crc_accumulate((uint8_t)extra, crc);
    if (buf[RADIOSIM_FIELD_HEADER_LEN + len] != (uint8_t)crc
        || buf[RADIOSIM_FIELD_HEADER_LEN + len + 1] != (uint8_t)(crc >> 8)) return 0;
    *msgid = id;
    *payload = buf + RADIOSIM_FIELD_HEADER_LEN;
    *payload_len = len;
    return total;
}

/* --- RADIOSIM_FIELD_TX_PACKET（ワイヤ上の並び: tx_time_us, seq, config_id, tx_id） --- */
typedef struct {
    uint8_t tx_id[6];
    uint32_t seq;
    uint64_t tx_time_us;
    uint16_t config_id;
} radiosim_field_tx_packet_t;

static inline void radiosim_field_tx_packet_encode_payload(uint8_t *p, const radiosim_field_tx_packet_t *m)
{
    radiosim_field_put_u64(p + 0, (uint64_t)m->tx_time_us);
    radiosim_field_put_u32(p + 8, (uint32_t)m->seq);
    radiosim_field_put_u16(p + 12, (uint16_t)m->config_id);
    for (int i = 0; i < 6; i++) radiosim_field_put_u8(p + 14 + i * 1, (uint8_t)m->tx_id[i]);
}

/* 短い payload（v2 の末尾切り詰め）はゼロで埋め戻し、長い分（拡張）は捨てる。 */
static inline void radiosim_field_tx_packet_decode_payload(const uint8_t *src, uint8_t len, radiosim_field_tx_packet_t *m)
{
    uint8_t p[20] = {0};
    memcpy(p, src, len < 20 ? len : 20);
    m->tx_time_us = (uint64_t)radiosim_field_get_u64(p + 0);
    m->seq = (uint32_t)radiosim_field_get_u32(p + 8);
    m->config_id = (uint16_t)radiosim_field_get_u16(p + 12);
    for (int i = 0; i < 6; i++) m->tx_id[i] = (uint8_t)radiosim_field_get_u8(p + 14 + i * 1);
}

/* フレームを frame に組み、長さを返す（frame は RADIOSIM_FIELD_MAX_FRAME_LEN バイト以上）。 */
static inline size_t radiosim_field_tx_packet_pack(uint8_t *frame, uint8_t link_seq, uint8_t sysid,
                                  uint8_t compid, const radiosim_field_tx_packet_t *m)
{
    radiosim_field_tx_packet_encode_payload(frame + RADIOSIM_FIELD_HEADER_LEN, m);
    return radiosim_field_frame_finish(frame, link_seq, sysid, compid, MSGID_RADIOSIM_FIELD_TX_PACKET,
                               LEN_RADIOSIM_FIELD_TX_PACKET, CRC_EXTRA_RADIOSIM_FIELD_TX_PACKET);
}

/* --- RADIOSIM_FIELD_RX_SAMPLE（ワイヤ上の並び: rx_time_us, seq, sample_seq, rssi_raw, noise_floor_raw, config_id, tx_config_id, rx_id, tx_id） --- */
typedef struct {
    uint8_t rx_id[6];
    uint8_t tx_id[6];
    uint32_t seq;
    uint64_t rx_time_us;
    int16_t rssi_raw;
    int16_t noise_floor_raw;
    uint16_t config_id;
    uint16_t tx_config_id;
    uint32_t sample_seq;
} radiosim_field_rx_sample_t;

static inline void radiosim_field_rx_sample_encode_payload(uint8_t *p, const radiosim_field_rx_sample_t *m)
{
    radiosim_field_put_u64(p + 0, (uint64_t)m->rx_time_us);
    radiosim_field_put_u32(p + 8, (uint32_t)m->seq);
    radiosim_field_put_u32(p + 12, (uint32_t)m->sample_seq);
    radiosim_field_put_u16(p + 16, (uint16_t)m->rssi_raw);
    radiosim_field_put_u16(p + 18, (uint16_t)m->noise_floor_raw);
    radiosim_field_put_u16(p + 20, (uint16_t)m->config_id);
    radiosim_field_put_u16(p + 22, (uint16_t)m->tx_config_id);
    for (int i = 0; i < 6; i++) radiosim_field_put_u8(p + 24 + i * 1, (uint8_t)m->rx_id[i]);
    for (int i = 0; i < 6; i++) radiosim_field_put_u8(p + 30 + i * 1, (uint8_t)m->tx_id[i]);
}

/* 短い payload（v2 の末尾切り詰め）はゼロで埋め戻し、長い分（拡張）は捨てる。 */
static inline void radiosim_field_rx_sample_decode_payload(const uint8_t *src, uint8_t len, radiosim_field_rx_sample_t *m)
{
    uint8_t p[36] = {0};
    memcpy(p, src, len < 36 ? len : 36);
    m->rx_time_us = (uint64_t)radiosim_field_get_u64(p + 0);
    m->seq = (uint32_t)radiosim_field_get_u32(p + 8);
    m->sample_seq = (uint32_t)radiosim_field_get_u32(p + 12);
    m->rssi_raw = (int16_t)radiosim_field_get_u16(p + 16);
    m->noise_floor_raw = (int16_t)radiosim_field_get_u16(p + 18);
    m->config_id = (uint16_t)radiosim_field_get_u16(p + 20);
    m->tx_config_id = (uint16_t)radiosim_field_get_u16(p + 22);
    for (int i = 0; i < 6; i++) m->rx_id[i] = (uint8_t)radiosim_field_get_u8(p + 24 + i * 1);
    for (int i = 0; i < 6; i++) m->tx_id[i] = (uint8_t)radiosim_field_get_u8(p + 30 + i * 1);
}

/* フレームを frame に組み、長さを返す（frame は RADIOSIM_FIELD_MAX_FRAME_LEN バイト以上）。 */
static inline size_t radiosim_field_rx_sample_pack(uint8_t *frame, uint8_t link_seq, uint8_t sysid,
                                  uint8_t compid, const radiosim_field_rx_sample_t *m)
{
    radiosim_field_rx_sample_encode_payload(frame + RADIOSIM_FIELD_HEADER_LEN, m);
    return radiosim_field_frame_finish(frame, link_seq, sysid, compid, MSGID_RADIOSIM_FIELD_RX_SAMPLE,
                               LEN_RADIOSIM_FIELD_RX_SAMPLE, CRC_EXTRA_RADIOSIM_FIELD_RX_SAMPLE);
}

/* --- RADIOSIM_FIELD_CONFIG（ワイヤ上の並び: integration_window_us, config_id, rate_kbps, tx_power_cdbm, tx_interval_ms, integration_samples, role, device_id, firmware_version, channel, detector, antenna） --- */
typedef struct {
    uint16_t config_id;
    uint8_t role;
    uint8_t device_id[6];
    char firmware_version[16];
    uint8_t channel;
    uint16_t rate_kbps;
    int16_t tx_power_cdbm;
    uint16_t tx_interval_ms;
    uint8_t detector;
    uint32_t integration_window_us;
    uint16_t integration_samples;
    uint8_t antenna;
} radiosim_field_config_t;

static inline void radiosim_field_config_encode_payload(uint8_t *p, const radiosim_field_config_t *m)
{
    radiosim_field_put_u32(p + 0, (uint32_t)m->integration_window_us);
    radiosim_field_put_u16(p + 4, (uint16_t)m->config_id);
    radiosim_field_put_u16(p + 6, (uint16_t)m->rate_kbps);
    radiosim_field_put_u16(p + 8, (uint16_t)m->tx_power_cdbm);
    radiosim_field_put_u16(p + 10, (uint16_t)m->tx_interval_ms);
    radiosim_field_put_u16(p + 12, (uint16_t)m->integration_samples);
    radiosim_field_put_u8(p + 14, (uint8_t)m->role);
    for (int i = 0; i < 6; i++) radiosim_field_put_u8(p + 15 + i * 1, (uint8_t)m->device_id[i]);
    memcpy(p + 21, m->firmware_version, 16);
    radiosim_field_put_u8(p + 37, (uint8_t)m->channel);
    radiosim_field_put_u8(p + 38, (uint8_t)m->detector);
    radiosim_field_put_u8(p + 39, (uint8_t)m->antenna);
}

/* 短い payload（v2 の末尾切り詰め）はゼロで埋め戻し、長い分（拡張）は捨てる。 */
static inline void radiosim_field_config_decode_payload(const uint8_t *src, uint8_t len, radiosim_field_config_t *m)
{
    uint8_t p[40] = {0};
    memcpy(p, src, len < 40 ? len : 40);
    m->integration_window_us = (uint32_t)radiosim_field_get_u32(p + 0);
    m->config_id = (uint16_t)radiosim_field_get_u16(p + 4);
    m->rate_kbps = (uint16_t)radiosim_field_get_u16(p + 6);
    m->tx_power_cdbm = (int16_t)radiosim_field_get_u16(p + 8);
    m->tx_interval_ms = (uint16_t)radiosim_field_get_u16(p + 10);
    m->integration_samples = (uint16_t)radiosim_field_get_u16(p + 12);
    m->role = (uint8_t)radiosim_field_get_u8(p + 14);
    for (int i = 0; i < 6; i++) m->device_id[i] = (uint8_t)radiosim_field_get_u8(p + 15 + i * 1);
    memcpy(m->firmware_version, p + 21, 16);
    m->channel = (uint8_t)radiosim_field_get_u8(p + 37);
    m->detector = (uint8_t)radiosim_field_get_u8(p + 38);
    m->antenna = (uint8_t)radiosim_field_get_u8(p + 39);
}

/* フレームを frame に組み、長さを返す（frame は RADIOSIM_FIELD_MAX_FRAME_LEN バイト以上）。 */
static inline size_t radiosim_field_config_pack(uint8_t *frame, uint8_t link_seq, uint8_t sysid,
                                  uint8_t compid, const radiosim_field_config_t *m)
{
    radiosim_field_config_encode_payload(frame + RADIOSIM_FIELD_HEADER_LEN, m);
    return radiosim_field_frame_finish(frame, link_seq, sysid, compid, MSGID_RADIOSIM_FIELD_CONFIG,
                               LEN_RADIOSIM_FIELD_CONFIG, CRC_EXTRA_RADIOSIM_FIELD_CONFIG);
}

/* --- 見本（Python 側で組んだフレーム・gen_c.py の golden_values） --- */
static const radiosim_field_tx_packet_t RADIOSIM_FIELD_GOLDEN_MSG_RADIOSIM_FIELD_TX_PACKET = { .tx_time_us = 15906611102759988753ULL, .seq = 2036088610u, .config_id = 20531u, .tx_id = {0u, 0u, 0u, 0u, 0u, 0u} };
static const uint8_t RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_TX_PACKET[26] = { 0xFD, 0x0E, 0x00, 0x00, 0x5A, 0x01, 0x01, 0x74, 0xA4, 0x00, 0x11, 0x2E, 0x4B, 0x68, 0x85, 0xA2, 0xBF, 0xDC, 0x22, 0x3F, 0x5C, 0x79, 0x33, 0x50, 0x81, 0xBB };
static const radiosim_field_rx_sample_t RADIOSIM_FIELD_GOLDEN_MSG_RADIOSIM_FIELD_RX_SAMPLE = { .rx_time_us = 15906611102759988753ULL, .seq = 2036088610u, .sample_seq = 2322419763u, .rssi_raw = -68, .noise_floor_raw = -85, .config_id = 33638u, .tx_config_id = 38007u, .rx_id = {137u, 144u, 151u, 158u, 165u, 172u}, .tx_id = {0u, 0u, 0u, 0u, 0u, 0u} };
static const uint8_t RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_RX_SAMPLE[42] = { 0xFD, 0x1E, 0x00, 0x00, 0x5A, 0x01, 0x01, 0x75, 0xA4, 0x00, 0x11, 0x2E, 0x4B, 0x68, 0x85, 0xA2, 0xBF, 0xDC, 0x22, 0x3F, 0x5C, 0x79, 0x33, 0x50, 0x6D, 0x8A, 0xBC, 0xFF, 0xAB, 0xFF, 0x66, 0x83, 0x77, 0x94, 0x89, 0x90, 0x97, 0x9E, 0xA5, 0xAC, 0x82, 0x1E };
static const radiosim_field_config_t RADIOSIM_FIELD_GOLDEN_MSG_RADIOSIM_FIELD_CONFIG = { .integration_window_us = 1749757457u, .config_id = 16162u, .rate_kbps = 20531u, .tx_power_cdbm = -68, .tx_interval_ms = 29269u, .integration_samples = 33638u, .role = 119u, .device_id = {137u, 144u, 151u, 158u, 165u, 172u}, .firmware_version = "g102firmware_ve", .channel = 170u, .detector = 187u, .antenna = 0u };
static const uint8_t RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_CONFIG[51] = { 0xFD, 0x27, 0x00, 0x00, 0x5A, 0x01, 0x01, 0x76, 0xA4, 0x00, 0x11, 0x2E, 0x4B, 0x68, 0x22, 0x3F, 0x33, 0x50, 0xBC, 0xFF, 0x55, 0x72, 0x66, 0x83, 0x77, 0x89, 0x90, 0x97, 0x9E, 0xA5, 0xAC, 0x67, 0x31, 0x30, 0x32, 0x66, 0x69, 0x72, 0x6D, 0x77, 0x61, 0x72, 0x65, 0x5F, 0x76, 0x65, 0x00, 0xAA, 0xBB, 0x28, 0x35 };

/* 起動時の自己検査。見本の値を自分のエンコーダで組み、Python 側で組んだ見本と
 * 1 バイトも違わないか、さらに見本を解いて組み直すと同じになるかを見る。
 * 0＝合格、それ以外＝落ちたメッセージの番号（1 から）。**落ちたら何も送らない**
 * ＝PC 側と食い違ったフレームは、読めてはいるが値が違う形で測定に混ざる。 */
static inline int radiosim_field_selftest(void)
{
    uint8_t frame[RADIOSIM_FIELD_MAX_FRAME_LEN];
    uint8_t again[RADIOSIM_FIELD_MAX_FRAME_LEN];
    uint32_t msgid;
    const uint8_t *payload;
    uint8_t payload_len;
    size_t n;
    {
        n = radiosim_field_tx_packet_pack(frame, 0x5A, 1, 1, &RADIOSIM_FIELD_GOLDEN_MSG_RADIOSIM_FIELD_TX_PACKET);
        if (n != sizeof RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_TX_PACKET || memcmp(frame, RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_TX_PACKET, n) != 0) return 1;
        if (radiosim_field_frame_parse(RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_TX_PACKET, n, &msgid, &payload, &payload_len) != n || msgid != MSGID_RADIOSIM_FIELD_TX_PACKET) return 1;
        radiosim_field_tx_packet_t decoded;
        radiosim_field_tx_packet_decode_payload(payload, payload_len, &decoded);
        if (radiosim_field_tx_packet_pack(again, 0x5A, 1, 1, &decoded) != n || memcmp(again, frame, n) != 0) return 1;
    }
    {
        n = radiosim_field_rx_sample_pack(frame, 0x5A, 1, 1, &RADIOSIM_FIELD_GOLDEN_MSG_RADIOSIM_FIELD_RX_SAMPLE);
        if (n != sizeof RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_RX_SAMPLE || memcmp(frame, RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_RX_SAMPLE, n) != 0) return 2;
        if (radiosim_field_frame_parse(RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_RX_SAMPLE, n, &msgid, &payload, &payload_len) != n || msgid != MSGID_RADIOSIM_FIELD_RX_SAMPLE) return 2;
        radiosim_field_rx_sample_t decoded;
        radiosim_field_rx_sample_decode_payload(payload, payload_len, &decoded);
        if (radiosim_field_rx_sample_pack(again, 0x5A, 1, 1, &decoded) != n || memcmp(again, frame, n) != 0) return 2;
    }
    {
        n = radiosim_field_config_pack(frame, 0x5A, 1, 1, &RADIOSIM_FIELD_GOLDEN_MSG_RADIOSIM_FIELD_CONFIG);
        if (n != sizeof RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_CONFIG || memcmp(frame, RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_CONFIG, n) != 0) return 3;
        if (radiosim_field_frame_parse(RADIOSIM_FIELD_GOLDEN_FRAME_RADIOSIM_FIELD_CONFIG, n, &msgid, &payload, &payload_len) != n || msgid != MSGID_RADIOSIM_FIELD_CONFIG) return 3;
        radiosim_field_config_t decoded;
        radiosim_field_config_decode_payload(payload, payload_len, &decoded);
        if (radiosim_field_config_pack(again, 0x5A, 1, 1, &decoded) != n || memcmp(again, frame, n) != 0) return 3;
    }
    return 0;
}
