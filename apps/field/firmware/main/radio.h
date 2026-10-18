/*
 * radio.h — E220（920 MHz）での番号付きパケットの送信（TX）と受信（RX）。
 *
 * E220 の設定（e220_configure）が E220_OK のときだけ radio_run を呼ぶ。
 */
#pragma once

#include <stdint.h>

#include "settings.h"
#include "radiosim_field_mavlink.h"

/* E220 を送受信のモードにし、役割に応じて送信か受信を始める。config は TX が空中へ
 * 送る自分の設定。 */
void radio_run(const radiosim_field_settings_t *s, const uint8_t mac[6], const radiosim_field_config_t *config);
