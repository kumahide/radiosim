/*
 * settings.h — 設定（NVS に保存）と、USB から届く 1 行コマンド。
 *
 * 設定を変えるたびに config_id を 1 つ進める（再起動しても同じ番号を使い回さない）。
 * PC 側はサンプルを config_id で設定に結ぶので、**中身が違うのに番号が同じ**だと
 * 別の条件のサンプルが同じ設定として集計される。
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint8_t role;             /* TRACER_ROLE_TX / TRACER_ROLE_RX */
    uint8_t channel;          /* 1〜13 */
    uint16_t tx_interval_ms;  /* 送信間隔 */
    int8_t tx_power_qdbm;     /* 送信電力の要求値（0.25 dBm 単位＝esp_wifi の単位） */
    uint16_t config_id;
} tracer_settings_t;

/* NVS から読む（無ければ既定値で作って保存する）。 */
void settings_load(tracer_settings_t *out);

/* 1 行のコマンドを解釈する。設定が変わったら保存して true を返す（呼び手が再起動する）。
 * 返答（"OK ..." / "ERR ..."）は reply に書く。 */
bool settings_apply_command(const char *line, tracer_settings_t *current, char *reply,
                            int reply_len);
