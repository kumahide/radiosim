/*
 * settings.c — 設定の保存と 1 行コマンド。
 *
 * コマンド（USB へ 1 行ずつ・改行で終わる）:
 *   show              … 設定をすぐ送る（RADIOSIM_FIELD_CONFIG）
 *   role tx|rx        … 役割
 *   band 920|2400     … 測る帯域（起動時にこの帯域だけを起こす）
 *   channel N         … 今の帯域のチャネル（920＝0〜6＝920.8＋0.2·N MHz／2.4＝1〜13）
 *   interval MS       … 今の帯域の送信間隔（920＝250〜10000 ms／2.4＝20〜10000 ms）
 *   power DBM         … 今の帯域の送信電力（920＝0〜13 dBm の整数／2.4＝2〜20 dBm・0.25 dB 刻み）
 * 変えたら保存して再起動する（途中で条件を変えたまま送り続けない）。
 *
 * NVS＝共通（役割・帯域・設定番号）と帯域ごと（チャネル・間隔・電力）の 3 つの名前空間。
 * 名前空間ごとに「全部読めたら使う・欠けたら既定値」で、どれかが欠けたら番号を進める。
 */
#include "settings.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nvs.h"
#include "e220.h"
#include "radiosim_field_mavlink.h"

#define NS_COMMON "fieldcmn"
/* 帯域を分ける前の版（920 だけ）は役割と番号もここに持っていた＝共通が無いときに引き継ぐ。 */
#define NS_920 "field920"
/* 2.4 GHz だけだった頃の値（"field"）は読まない＝帯域を分ける前の番号が混ざる。 */
#define NS_2400 "field2400"

static const radiosim_field_settings_t DEFAULTS = {
    .role = RADIOSIM_FIELD_ROLE_RX,
    .band = RADIOSIM_FIELD_BAND_920,
    .b920 = {
        .channel = 0,
        .tx_interval_ms = 250,  /* SF7/BW500 の 32 B（約 23 ms）でデューティ約 9% */
        .tx_power = E220_POWER_MAX_DBM,
    },
    .b2400 = {
        .channel = 1,
        .tx_interval_ms = 100,  /* §6.1-7D：量子化平均 N≥10 が約 1 秒で集まる */
        .tx_power = 60,         /* 15 dBm */
    },
    .config_id = 1,
};

const radiosim_field_band_settings_t *settings_band(const radiosim_field_settings_t *s)
{
    return s->band == RADIOSIM_FIELD_BAND_2400 ? &s->b2400 : &s->b920;
}

static void save_band(const char *ns, const radiosim_field_band_settings_t *b)
{
    nvs_handle_t h;
    ESP_ERROR_CHECK(nvs_open(ns, NVS_READWRITE, &h));
    ESP_ERROR_CHECK(nvs_set_u8(h, "channel", b->channel));
    ESP_ERROR_CHECK(nvs_set_u16(h, "interval", b->tx_interval_ms));
    ESP_ERROR_CHECK(nvs_set_i8(h, "power", b->tx_power));
    ESP_ERROR_CHECK(nvs_commit(h));
    nvs_close(h);
}

static void save(const radiosim_field_settings_t *s)
{
    nvs_handle_t h;
    ESP_ERROR_CHECK(nvs_open(NS_COMMON, NVS_READWRITE, &h));
    ESP_ERROR_CHECK(nvs_set_u8(h, "role", s->role));
    ESP_ERROR_CHECK(nvs_set_u16(h, "band", s->band));
    ESP_ERROR_CHECK(nvs_set_u16(h, "config_id", s->config_id));
    ESP_ERROR_CHECK(nvs_commit(h));
    nvs_close(h);
    save_band(NS_920, &s->b920);
    save_band(NS_2400, &s->b2400);
}

/* 帯域の値を全部読めたら true。欠けたら既定値のまま（一部だけ読めた値は使わない）。 */
static bool load_band(const char *ns, const radiosim_field_band_settings_t *defaults,
                      radiosim_field_band_settings_t *out)
{
    radiosim_field_band_settings_t b = *defaults;
    nvs_handle_t h;
    if (nvs_open(ns, NVS_READONLY, &h) != ESP_OK) return false;
    bool ok = nvs_get_u8(h, "channel", &b.channel) == ESP_OK
           && nvs_get_u16(h, "interval", &b.tx_interval_ms) == ESP_OK
           && nvs_get_i8(h, "power", &b.tx_power) == ESP_OK;
    nvs_close(h);
    if (ok) *out = b;
    return ok;
}

void settings_load(radiosim_field_settings_t *out)
{
    *out = DEFAULTS;
    bool ok = false;
    nvs_handle_t h;
    if (nvs_open(NS_COMMON, NVS_READONLY, &h) == ESP_OK) {
        ok = nvs_get_u8(h, "role", &out->role) == ESP_OK
          && nvs_get_u16(h, "band", &out->band) == ESP_OK
          && nvs_get_u16(h, "config_id", &out->config_id) == ESP_OK;
        nvs_close(h);
    } else if (nvs_open(NS_920, NVS_READONLY, &h) == ESP_OK) {
        nvs_get_u8(h, "role", &out->role);
        nvs_get_u16(h, "config_id", &out->config_id);
        nvs_close(h);
    }
    if (out->band != RADIOSIM_FIELD_BAND_920 && out->band != RADIOSIM_FIELD_BAND_2400) {
        out->band = DEFAULTS.band;
        ok = false;
    }
    if (out->role != RADIOSIM_FIELD_ROLE_TX && out->role != RADIOSIM_FIELD_ROLE_RX) {
        out->role = DEFAULTS.role;
        ok = false;
    }
    ok = load_band(NS_920, &DEFAULTS.b920, &out->b920) && ok;
    ok = load_band(NS_2400, &DEFAULTS.b2400, &out->b2400) && ok;
    if (!ok) {
        /* 欠けた所は既定値になった＝以前の設定と同じ番号を名乗らない（読めた番号より先へ）。 */
        out->config_id = (uint16_t)(out->config_id + 1);
        save(out);
    }
}

void settings_bump_config_id(radiosim_field_settings_t *current)
{
    current->config_id = (uint16_t)(current->config_id + 1);
    save(current);
}

static bool parse_long(const char *text, long lo, long hi, long *out)
{
    char *end;
    long v = strtol(text, &end, 10);
    if (end == text || *end != '\0' || v < lo || v > hi) return false;
    *out = v;
    return true;
}

static bool same_band(const radiosim_field_band_settings_t *a, const radiosim_field_band_settings_t *b)
{
    return a->channel == b->channel && a->tx_interval_ms == b->tx_interval_ms && a->tx_power == b->tx_power;
}

bool settings_apply_command(const char *line, radiosim_field_settings_t *current, char *reply,
                            int reply_len)
{
    char cmd[16] = {0};
    char arg[32] = {0};
    int n = sscanf(line, "%15s %31s", cmd, arg);
    if (n < 1) {
        snprintf(reply, (size_t)reply_len, "ERR 空の行です");
        return false;
    }
    radiosim_field_settings_t next = *current;
    const bool is2400 = current->band == RADIOSIM_FIELD_BAND_2400;
    radiosim_field_band_settings_t *b = is2400 ? &next.b2400 : &next.b920;
    long v;
    if (strcmp(cmd, "show") == 0) {
        snprintf(reply, (size_t)reply_len, "OK");
        return false;
    } else if (strcmp(cmd, "role") == 0 && n == 2) {
        if (strcmp(arg, "tx") == 0) next.role = RADIOSIM_FIELD_ROLE_TX;
        else if (strcmp(arg, "rx") == 0) next.role = RADIOSIM_FIELD_ROLE_RX;
        else {
            snprintf(reply, (size_t)reply_len, "ERR role は tx か rx です");
            return false;
        }
    } else if (strcmp(cmd, "band") == 0 && n == 2) {
        if (strcmp(arg, "920") == 0) next.band = RADIOSIM_FIELD_BAND_920;
        else if (strcmp(arg, "2400") == 0) next.band = RADIOSIM_FIELD_BAND_2400;
        else {
            snprintf(reply, (size_t)reply_len, "ERR band は 920 か 2400 です");
            return false;
        }
    } else if (strcmp(cmd, "channel") == 0 && n == 2) {
        if (is2400 ? !parse_long(arg, 1, 13, &v) : !parse_long(arg, 0, E220_CHANNEL_MAX, &v)) {
            snprintf(reply, (size_t)reply_len,
                     is2400 ? "ERR channel は 1〜13 です（2.4 GHz）" : "ERR channel は 0〜6 です（920 MHz）");
            return false;
        }
        b->channel = (uint8_t)v;
    } else if (strcmp(cmd, "interval") == 0 && n == 2) {
        if (!parse_long(arg, is2400 ? 20 : 250, 10000, &v)) {
            snprintf(reply, (size_t)reply_len,
                     is2400 ? "ERR interval は 20〜10000 ms です（2.4 GHz）"
                            : "ERR interval は 250〜10000 ms です（920 MHz）");
            return false;
        }
        b->tx_interval_ms = (uint16_t)v;
    } else if (strcmp(cmd, "power") == 0 && n == 2) {
        if (is2400) {
            char *end;
            double dbm = strtod(arg, &end);
            double q = dbm * 4.0;
            if (end == arg || *end != '\0' || dbm < 2.0 || dbm > 20.0 || q != (double)(long)q) {
                snprintf(reply, (size_t)reply_len, "ERR power は 2〜20 dBm・0.25 刻みです（2.4 GHz）");
                return false;
            }
            b->tx_power = (int8_t)(long)q;
        } else {
            if (!parse_long(arg, 0, E220_POWER_MAX_DBM, &v)) {
                snprintf(reply, (size_t)reply_len, "ERR power は 0〜13 dBm の整数です（920 MHz）");
                return false;
            }
            b->tx_power = (int8_t)v;
        }
    } else {
        snprintf(reply, (size_t)reply_len, "ERR 知らないコマンドです: %s", cmd);
        return false;
    }
    if (next.role == current->role && next.band == current->band && same_band(&next.b920, &current->b920)
        && same_band(&next.b2400, &current->b2400)) {
        snprintf(reply, (size_t)reply_len, "OK 変わっていません");
        return false;
    }
    next.config_id = (uint16_t)(current->config_id + 1);
    save(&next);
    *current = next;
    snprintf(reply, (size_t)reply_len, "OK config_id=%u 再起動します", next.config_id);
    return true;
}
