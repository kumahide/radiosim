/*
 * radio.h — 番号付きパケットの送信（TX）と受信（RX）。帯域ごとに 2 本:
 *   - radio_e220.c＝E220-900T22S(JP)・920 MHz（下回りは e220.c）
 *   - radio_wifi.c＝ESP32-C6 内蔵の Wi-Fi・2.4 GHz（11b・1 Mbps の生フレーム）
 * main が設定の band で片方か両方（both＝I-252）を起こす。TX の送信は radio_sched.c の
 * **1 本のタスク**が予定どおりに各帯域の「1 周期」を呼ぶ＝both では 920 のブロック → 2.4 の
 * ブロックを繰り返す（**送信は同時にしない**＝正典 §4）。RX は起こした帯域を全部同時に聞く。
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

/* E220 を送受信のモードにする。RX は受信を始め、TX は radio_e220_tx_cycle を待つ。
 * e220_configure が E220_OK のときだけ呼ぶ。config は TX が空中へ送る自分の 920 の設定。 */
void radio_e220_run(const radiosim_field_settings_t *s, const uint8_t mac[6],
                    const radiosim_field_config_t *config);

/* TX だけ: 920 の 1 周期（前の送信の結果を読む → TX_PACKET か設定を送る → USB へ控え →
 * seq を進める）。radio_sched.c のタスクから呼ぶ。 */
void radio_e220_tx_cycle(void);

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

/* RX は受信を始め、TX は radio_wifi_tx_cycle を待つ。config は TX が空中へ送る自分の 2.4 の
 * 設定（radio_wifi_start で読み戻した送信電力を載せたもの）。 */
void radio_wifi_run(const radiosim_field_config_t *config);

/* TX だけ: 2.4 の 1 周期。hold が true なら空中へ何も送らず「送らなかった周期」に数える
 * （both で 920 の送信がまだ終わっていないとき＝2 つの送信を重ねない）。連続送信の要求が
 * あればここで始める（戻らない）。radio_sched.c のタスクから呼ぶ。 */
void radio_wifi_tx_cycle(bool hold);

/* TX だけ: 連続送信（ステージ0b で送信電力を平均電力計で測るため）を seconds 秒行い、
 * 終わったら再起動する。0 なら実行中の連続送信を止める（止めたときも再起動する）。
 * 番号付きのパケットと設定の空中送信はその間止まる＝呼び手は先に config_id を進めて
 * おくこと（再起動後の設定で、PC がこの間を跨いだ記録を閉じられるように）。 */
void radio_continuous(uint32_t seconds);

/* --- TX の予定（radio_sched.c） --------------------------------------------------- */

/* TX の送信のタスクを 1 本起こす（radio_e220_run／radio_wifi_run の後）。1 帯域なら
 * その帯域の送信間隔ごとに 1 周期。both なら 920 のブロック → 2.4 のブロックの繰り返し
 * （各 block_ms・ブロックの頭から送信間隔ごとに floor(block_ms ÷ 間隔) 周期）を **µs の絶対
 * 時刻で**刻む＝PC は番号 s の時刻を (s ÷ n の商)·2·block_ms ＋ (s mod n)·間隔 と読む
 * （XML の CONFIG.block_ms）。2.4 の各周期の直前に E220 の AUX を見て、Low なら送らない。 */
void radio_tx_run(const radiosim_field_settings_t *s);
