/*
 * link.h — USB（USB Serial/JTAG）へ MAVLink のフレームを流し、コマンドの行を受ける。
 *
 * **書き手はここ 1 か所だけ**（ロックで直列にする）。複数のタスクが直に書くと、
 * フレームの途中に別のフレームが割り込み、どちらも CRC で弾かれる＝PC 側では
 * 電波で届かなかった分と見分けが付かない欠けになる。
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "tracer_mavlink.h"

void link_init(void);

void link_send_tx_packet(const tracer_tx_packet_t *m);
void link_send_rx_sample(const tracer_rx_sample_t *m);
void link_send_config(const tracer_config_t *m);

/* テキストの 1 行（コマンドへの返答）。UTF-8 は 0xFD を含まないので、PC 側の
 * 読み取りはフレームの外のバイトとして読み飛ばす。 */
void link_send_text(const char *line);

/* 1 行読む（改行まで・改行は含まない）。行が揃わなければ 0 を返す。 */
size_t link_read_line(char *buf, size_t len);

/* USB へ書けずに捨てたフレームの数（PC が読んでいないときなど）。 */
uint32_t link_dropped(void);
