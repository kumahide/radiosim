/*
 * settings.c — 設定の保存と 1 行コマンド。
 *
 * コマンド（USB へ 1 行ずつ・改行で終わる）:
 *   show              … 設定をすぐ送る（TRACER_CONFIG）
 *   role tx|rx        … 役割
 *   channel N         … 2.4 GHz のチャネル（1〜13）
 *   interval MS       … 送信間隔（20〜10000 ms）
 *   power DBM         … 送信電力（2〜20 dBm・0.25 dB 刻み）
 * 変えたら保存して再起動する（途中で条件を変えたまま送り続けない）。
 */
#include "settings.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nvs.h"
#include "tracer_mavlink.h"

#define NS "tracer"

static const tracer_settings_t DEFAULTS = {
    .role = TRACER_ROLE_RX,
    .channel = 1,
    .tx_interval_ms = 100,      /* §6.1-7D：量子化平均 N≥10 が約 1 秒で集まる */
    .tx_power_qdbm = 60,        /* 15 dBm */
    .config_id = 1,
};

static void save(const tracer_settings_t *s)
{
    nvs_handle_t h;
    ESP_ERROR_CHECK(nvs_open(NS, NVS_READWRITE, &h));
    ESP_ERROR_CHECK(nvs_set_u8(h, "role", s->role));
    ESP_ERROR_CHECK(nvs_set_u8(h, "channel", s->channel));
    ESP_ERROR_CHECK(nvs_set_u16(h, "interval", s->tx_interval_ms));
    ESP_ERROR_CHECK(nvs_set_i8(h, "power", s->tx_power_qdbm));
    ESP_ERROR_CHECK(nvs_set_u16(h, "config_id", s->config_id));
    ESP_ERROR_CHECK(nvs_commit(h));
    nvs_close(h);
}

void settings_load(tracer_settings_t *out)
{
    *out = DEFAULTS;
    nvs_handle_t h;
    if (nvs_open(NS, NVS_READONLY, &h) != ESP_OK) {
        save(out);
        return;
    }
    bool ok = nvs_get_u8(h, "role", &out->role) == ESP_OK
           && nvs_get_u8(h, "channel", &out->channel) == ESP_OK
           && nvs_get_u16(h, "interval", &out->tx_interval_ms) == ESP_OK
           && nvs_get_i8(h, "power", &out->tx_power_qdbm) == ESP_OK
           && nvs_get_u16(h, "config_id", &out->config_id) == ESP_OK;
    nvs_close(h);
    if (!ok) {
        /* 一部だけ読めた設定は使わない（どの値が既定か分からなくなる）。番号は
         * 読めた分より先へ進め、以前の設定と同じ番号を名乗らない。 */
        uint16_t seen = out->config_id;
        *out = DEFAULTS;
        out->config_id = (uint16_t)(seen + 1);
        save(out);
    }
}

static bool parse_long(const char *text, long lo, long hi, long *out)
{
    char *end;
    long v = strtol(text, &end, 10);
    if (end == text || *end != '\0' || v < lo || v > hi) return false;
    *out = v;
    return true;
}

bool settings_apply_command(const char *line, tracer_settings_t *current, char *reply,
                            int reply_len)
{
    char cmd[16] = {0};
    char arg[32] = {0};
    int n = sscanf(line, "%15s %31s", cmd, arg);
    if (n < 1) {
        snprintf(reply, (size_t)reply_len, "ERR 空の行です");
        return false;
    }
    tracer_settings_t next = *current;
    long v;
    if (strcmp(cmd, "show") == 0) {
        snprintf(reply, (size_t)reply_len, "OK");
        return false;
    } else if (strcmp(cmd, "role") == 0 && n == 2) {
        if (strcmp(arg, "tx") == 0) next.role = TRACER_ROLE_TX;
        else if (strcmp(arg, "rx") == 0) next.role = TRACER_ROLE_RX;
        else {
            snprintf(reply, (size_t)reply_len, "ERR role は tx か rx です");
            return false;
        }
    } else if (strcmp(cmd, "channel") == 0 && n == 2) {
        if (!parse_long(arg, 1, 13, &v)) {
            snprintf(reply, (size_t)reply_len, "ERR channel は 1〜13 です");
            return false;
        }
        next.channel = (uint8_t)v;
    } else if (strcmp(cmd, "interval") == 0 && n == 2) {
        if (!parse_long(arg, 20, 10000, &v)) {
            snprintf(reply, (size_t)reply_len, "ERR interval は 20〜10000 ms です");
            return false;
        }
        next.tx_interval_ms = (uint16_t)v;
    } else if (strcmp(cmd, "power") == 0 && n == 2) {
        char *end;
        double dbm = strtod(arg, &end);
        double q = dbm * 4.0;
        if (end == arg || *end != '\0' || dbm < 2.0 || dbm > 20.0 || q != (double)(long)q) {
            snprintf(reply, (size_t)reply_len, "ERR power は 2〜20 dBm・0.25 刻みです");
            return false;
        }
        next.tx_power_qdbm = (int8_t)(long)q;
    } else {
        snprintf(reply, (size_t)reply_len, "ERR 知らないコマンドです: %s", cmd);
        return false;
    }
    if (next.role == current->role && next.channel == current->channel
        && next.tx_interval_ms == current->tx_interval_ms
        && next.tx_power_qdbm == current->tx_power_qdbm) {
        snprintf(reply, (size_t)reply_len, "OK 変わっていません");
        return false;
    }
    next.config_id = (uint16_t)(current->config_id + 1);
    save(&next);
    *current = next;
    snprintf(reply, (size_t)reply_len, "OK config_id=%u 再起動します", next.config_id);
    return true;
}
