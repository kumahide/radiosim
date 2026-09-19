/*
 * radio.h — Wi-Fi の生フレーム送信（TX）とプロミスキャス受信（RX）。
 */
#pragma once

#include <stdint.h>

#include "settings.h"

/* 無線を起こし、役割に応じて送信か受信を始める。実際に効いた送信電力
 * （0.25 dBm 単位に量子化された値）を *actual_power_qdbm に返す。 */
void radio_start(const tracer_settings_t *s, const uint8_t mac[6], int8_t *actual_power_qdbm);

/* 送れなかった回数（TX）／受信キューが溢れて捨てた回数（RX）。 */
uint32_t radio_tx_failures(void);
uint32_t radio_rx_overflows(void);
