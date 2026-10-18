/*
 * e220.h — E220-900T22S(JP)（ver.2）の下回り＝UART1 9600・M0/M1・AUX。
 *
 * 920 MHz への作り直し（作業順 2）で radio.c の Wi-Fi を置き換える。ここはモジュールとの
 * やり取りだけを持ち、パケットの中身（MAVLink）は知らない。
 *
 * 設定は**起動のたびに 0x00〜0x0A を照らし合わせ、違う所だけ書いて読み直す**
 * （この版に一時書き込み C2 は無い＝書き込みは C0＝不揮発だけ）。0x09 を Strict にするので、
 * 以後は mode 3 の読み書きも mode 0 の受信の出力も「長さ・中身・検査和」で区切られる。
 *
 * ⚠️ 0xE0〜0xEF には読みも書きもしない（モジュールのファームのベクタ表と読める＝
 *    書くと壊すおそれ・2026-10-18 の実機）。
 * ⚠️ mode 0 で知らない命令は電波に乗る疑いがある＝mode 0 で送る命令は
 *    状態の読み出し（C1 0xA0〜）とフラグ消去（F0）だけ。
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define E220_REG_COUNT 11          /* 0x00〜0x0A */
#define E220_VERSION 0x20          /* 0x08＝22S(JP) v2.0。違えば設定を書かない */

/* 空中の SF/BW はファームの定数（作業順 3 で実測するまで設定にしない）。 */
#define E220_AIR_SF 7
#define E220_AIR_BW_KHZ 500
#define E220_CHANNEL_MAX 12        /* BW500 は CH0〜30＝920.8＋0.2·CH MHz。使うのは 0〜12 */
#define E220_POWER_MAX_DBM 13      /* 0〜13 dBm の整数 */

/* mode 0 の受信の出力の最長（中身 200 B＋長さ・RSSI・検査和）。 */
#define E220_FRAME_MAX 204

typedef enum {
    E220_OK = 0,
    E220_ERR_READ,       /* 設定が読めない（配線・電源・UART の速さ） */
    E220_ERR_VERSION,    /* 0x08 が E220_VERSION でない＝別のモジュール・別の版 */
    E220_ERR_WRITE,      /* 書き込みの応答が無い・違う */
    E220_ERR_VERIFY,     /* 書いて読み直した値が要求と違う */
} e220_err_t;

typedef enum {
    E220_FRAME_NONE,     /* 時間切れ（何も揃わなかった） */
    E220_FRAME_DATA,     /* 空中で受けた中身 */
    E220_FRAME_REPLY,    /* mode 0 の命令への応答（先頭が FF FF） */
} e220_frame_kind_t;

typedef struct {
    e220_frame_kind_t kind;
    uint8_t len;                   /* data の長さ */
    uint8_t data[E220_FRAME_MAX];  /* DATA＝中身／REPLY＝FF FF から検査和の前まで */
    int16_t rssi_raw;              /* DATA だけ＝RSSI バイト − 256（dBm とみなさない） */
    int64_t aux_fall_us;           /* DATA だけ＝最初のバイトより前の最後の AUX の立ち下がり（0＝不明） */
} e220_frame_t;

/* ピン・UART・AUX の割り込みを用意し、mode 3（設定）にする。 */
void e220_init(void);

/* 0x00〜0x0A の要求値を組む（0x08 は E220_VERSION・0xE0 以降は含まない）。 */
void e220_build_regs(uint8_t channel, int8_t power_dbm, uint8_t out[E220_REG_COUNT]);

/* mode 3 で設定を照らし合わせ、違う所だけ書いて読み直す。got には最後に読めた値
 * （読めなければ 0）。E220_OK 以外なら測定を始めてはいけない。 */
e220_err_t e220_configure(uint8_t channel, int8_t power_dbm, uint8_t got[E220_REG_COUNT]);
const char *e220_err_text(e220_err_t err);

/* mode 0（送受信）にする。e220_configure が E220_OK のときだけ呼ぶ
 * （Strict でないまま mode 0 で命令を送ると、透過送信として電波に乗る）。 */
void e220_enter_normal(void);

bool e220_aux_high(void);

/* mode 0：長さで区切った 1 つの枠を読む（検査和の合わない枠は 1 バイトずらして捨てる）。 */
e220_frame_kind_t e220_read_frame(e220_frame_t *f, int timeout_ms);

/* mode 0 の命令。応答は e220_read_frame の E220_FRAME_REPLY で届く。 */
void e220_request_status(uint8_t addr, uint8_t len);   /* FF FF C1 addr len cs */
void e220_clear_flags(uint8_t addr);                   /* FF FF F0 addr cs */

/* mode 0：透過送信。書き終えるまで待つ（AUX は書き込みから約 2 ms で Low へ落ちる）。 */
void e220_send(const uint8_t *data, size_t n);
