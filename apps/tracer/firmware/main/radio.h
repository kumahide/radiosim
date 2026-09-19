/*
 * radio.h — Wi-Fi の生フレーム送信（TX）とプロミスキャス受信（RX）。
 */
#pragma once

#include <stdint.h>

#include "settings.h"
#include "tracer_mavlink.h"

/* 無線を起こす（まだ送受信は始めない）。実際に効いた送信電力
 * （0.25 dBm 単位に量子化された値）を *actual_power_qdbm に返す。 */
void radio_start(const tracer_settings_t *s, const uint8_t mac[6], int8_t *actual_power_qdbm);

/* 役割に応じて送信か受信を始める。config は TX が空中へ送る自分の設定
 * （radio_start で読み戻した送信電力を載せたもの）。 */
void radio_run(const tracer_config_t *config);

/* 送れなかった回数（TX）／受信キューが溢れて捨てた回数（RX）。 */
uint32_t radio_tx_failures(void);
uint32_t radio_rx_overflows(void);
