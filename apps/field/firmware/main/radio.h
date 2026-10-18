/*
 * radio.h — 番号付きパケットの送信（TX）と受信（RX）。帯域ごとに 2 本:
 *   - radio_e220.c＝E220-900T22S(JP)・920 MHz（下回りは e220.c）
 *   - radio_wifi.c＝ESP32-C6 内蔵の Wi-Fi・2.4 GHz（11b・1 Mbps の生フレーム）
 * main が設定の band で**片方だけ**起こす（2 帯域は同時に動かさない＝正典 §4）。
 * 帯域の切り替えは設定の変更＝config_id が進んで再起動する（ブロック単位の時分割）。
 *
 * 空中の中身はどちらも同じ＝RADIOSIM_FIELD_TX_PACKET／RADIOSIM_FIELD_CONFIG の MAVLink v2
 * フレーム（XML が単一の出所）。seq は周期の番号・送れなかった周期は sent_mask／unsent_total。
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "settings.h"
#include "radiosim_field_mavlink.h"

/* --- 920 MHz（radio_e220.c） --------------------------------------------------- */

/* E220 を送受信のモードにし、役割に応じて送信か受信を始める。e220_configure が
 * E220_OK のときだけ呼ぶ。config は TX が空中へ送る自分の設定。 */
void radio_e220_run(const radiosim_field_settings_t *s, const uint8_t mac[6],
                    const radiosim_field_config_t *config);

/* --- 2.4 GHz（radio_wifi.c） --------------------------------------------------- */

/* 空中のレート（11b・1 Mbps・ロングプリアンブル）。設定メッセージの rate_kbps の既定値。 */
#define RADIO_AIR_RATE_KBPS 1000

/* Wi-Fi を起こす（まだ送受信は始めない）。実際に効いた送信電力
 * （0.25 dBm 単位に量子化された値）を *actual_power_qdbm に返す。 */
void radio_wifi_start(const radiosim_field_settings_t *s, const uint8_t mac[6], int8_t *actual_power_qdbm);

/* TX だけ: 確かめ用のフレームを 1 つ送り、ドライバが実際に使ったレートを読む。
 * RADIO_AIR_RATE_KBPS で出ていれば true。false なら測定を始めてはいけない。
 * *actual_kbps には実際のレート（読めなければ 0）を返す。radio_wifi_run より前に呼ぶ。 */
bool radio_verify_rate(uint16_t *actual_kbps);

/* 役割に応じて送信か受信を始める。config は TX が空中へ送る自分の設定
 * （radio_wifi_start で読み戻した送信電力を載せたもの）。 */
void radio_wifi_run(const radiosim_field_config_t *config);

/* TX だけ: 連続送信（ステージ0b で送信電力を平均電力計で測るため）を seconds 秒行い、
 * 終わったら再起動する。0 なら実行中の連続送信を止める（止めたときも再起動する）。
 * 番号付きのパケットと設定の空中送信はその間止まる＝呼び手は先に config_id を進めて
 * おくこと（再起動後の設定で、PC がこの間を跨いだ記録を閉じられるように）。 */
void radio_continuous(uint32_t seconds);
