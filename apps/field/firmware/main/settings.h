/*
 * settings.h — 設定（NVS に保存）と、USB から届く 1 行コマンド。
 *
 * 設定を変えるたびに config_id を 1 つ進める（再起動しても同じ番号を使い回さない）。
 * PC 側はサンプルを config_id で設定に結ぶので、**中身が違うのに番号が同じ**だと
 * 別の条件のサンプルが同じ設定として集計される。
 *
 * 帯域（band）は 1 つに固定し、起動時にその帯域だけを起こす（2 帯域は同時に動かさない＝
 * 正典 §4）。役割・帯域・設定番号は共通、チャネル・電力・間隔は帯域ごとに別々に持つ
 * （値域も意味も違う＝下の表）。
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

/* 帯域ごとの値。電力の単位が帯域で違う＝920 は dBm の整数・2.4 は 0.25 dBm 単位
 * （ドライバの esp_wifi_set_max_tx_power の単位）。 */
typedef struct {
    uint8_t channel;          /* 920＝0〜E220_CHANNEL_MAX（920.8＋0.2·CH MHz）／2.4＝1〜13 */
    uint16_t tx_interval_ms;  /* 920＝250〜10000／2.4＝20〜10000 */
    int8_t tx_power;          /* 920＝0〜E220_POWER_MAX_DBM の整数 dBm／2.4＝8〜80（2〜20 dBm の 4 倍） */
} radiosim_field_band_settings_t;

typedef struct {
    uint8_t role;             /* RADIOSIM_FIELD_ROLE_TX / RADIOSIM_FIELD_ROLE_RX */
    uint16_t band;            /* RADIOSIM_FIELD_BAND_920 / RADIOSIM_FIELD_BAND_2400 */
    radiosim_field_band_settings_t b920;
    radiosim_field_band_settings_t b2400;
    uint16_t config_id;
} radiosim_field_settings_t;

/* 今の帯域の値。 */
const radiosim_field_band_settings_t *settings_band(const radiosim_field_settings_t *s);

/* NVS から読む（無ければ既定値で作って保存する）。 */
void settings_load(radiosim_field_settings_t *out);

/* 中身を変えずに config_id だけ進めて保存する（連続送信のように、普段の送信と
 * 違う条件の間を PC が別の設定として閉じられるようにするため）。 */
void settings_bump_config_id(radiosim_field_settings_t *current);

/* 1 行のコマンドを解釈する。設定が変わったら保存して true を返す（呼び手が再起動する）。
 * 返答（"OK ..." / "ERR ..."）は reply に書く。 */
bool settings_apply_command(const char *line, radiosim_field_settings_t *current, char *reply,
                            int reply_len);
