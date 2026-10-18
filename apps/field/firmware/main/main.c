/*
 * main.c — RadioSim Fieldのファーム（XIAO ESP32C6＋E220-900T22S(JP)・920 MHz）。
 *
 * 送信と受信は同じファームで、役割は設定で切り替える（§6.1-7E）。
 * 流れ: NVS → 設定 → 自己検査 → E220 の設定を照らし合わせる → 送受信を始める → 以後、
 *       設定を 1 秒ごとに送りながら、USB からのコマンドを待つ。
 *
 * 設定（RADIOSIM_FIELD_CONFIG）は起動時・変更時に加えて **1 秒ごとに再送する**（§6.6-①）＝
 * PC 側は最初に届いた RX の設定でセッションを作るので、「起動時だけ」だと RX が
 * 先に起動していたら記録が始まらない。同じ設定の再送は PC 側が読み飛ばす。
 */
#include <stdio.h>
#include <string.h>

#include "e220.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "link.h"
#include "nvs_flash.h"
#include "radio.h"
#include "settings.h"
#include "radiosim_field_mavlink.h"
#include "radiosim_field_version.h" /* ビルドのたびに生成（version.cmake）。 */

static const char *TAG = "field";

#define CONFIG_RESEND_MS 1000

static radiosim_field_config_t make_config(const radiosim_field_settings_t *s, const uint8_t mac[6])
{
    radiosim_field_config_t c = {
        .config_id = s->config_id,
        .role = s->role,
        .channel = s->channel,
        .air_sf = E220_AIR_SF,                /* 起動時に E220 の 0x02 で確かめ済み */
        .air_bw_khz = E220_AIR_BW_KHZ,
        .tx_power_cdbm = (int16_t)(s->tx_power_dbm * 100),   /* 0x03 で確かめ済み */
        .tx_interval_ms = s->tx_interval_ms,
        .detector = RADIOSIM_FIELD_DETECTOR_INSTANT,
        /* 0＝不明。瞬時値がどれだけの時間で測られたかはチップの内部仕様で、ファームは
         * 知らない（数字を作って刻むと、刻印が事実でなくなる）。 */
        .integration_window_us = 0,
        .integration_samples = 1,
        .antenna = RADIOSIM_FIELD_ANTENNA_EXTERNAL,
    };
    memcpy(c.device_id, mac, 6);
    /* char[16] に NUL 終端は要らない（MAVLink の文字列は固定長・残りは上の初期化で 0）。
     * ⚠️ esp_app_get_description()->version は使わない＝構成時の値で、既存の build
     * フォルダでは古いコミットのまま残る（B-256）。 */
    _Static_assert(sizeof RADIOSIM_FIELD_FIRMWARE_VERSION - 1 <= sizeof c.firmware_version,
                   "ファームの版が刻印の欄に収まらない");
    memcpy(c.firmware_version, RADIOSIM_FIELD_FIRMWARE_VERSION, sizeof RADIOSIM_FIELD_FIRMWARE_VERSION - 1);
    return c;
}

/* `cont` は 920 MHz では受けない＝AUX の Low は空中の時間と同じでなく、デューティを
 * 測れない（測り方は I-242）。コマンドだったら true。 */
static bool handle_continuous(const char *line)
{
    if (strncmp(line, "cont", 4) != 0) return false;
    link_send_text("ERR 920 MHz では未対応です（cont）");
    return true;
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    link_init();

    int failed = radiosim_field_selftest();
    if (failed != 0) {
        /* PC 側と食い違ったフレームは「読めているのに値が違う」形で測定に混ざる。
         * 何も送らないほうがまし（生成物が古い／コンパイラの不具合など）。 */
        ESP_LOGE(TAG, "自己検査に落ちました（メッセージ %d）", failed);
        for (;;) {
            link_send_text("ERR 自己検査に落ちました（radiosim_field_mavlink.h を作り直してください）");
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }

    radiosim_field_settings_t settings;
    settings_load(&settings);
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));

    /* 食い違ったまま測ると、刻印（SF・BW・チャネル・電力）と違う条件のサンプルが同じ条件
     * として記録される。測定を始めないほうがまし（B-257 と同じ形）。 */
    e220_init();
    uint8_t got[E220_REG_COUNT];
    e220_err_t e = e220_configure(settings.channel, settings.tx_power_dbm, got);
    if (e != E220_OK) {
        ESP_LOGE(TAG, "%s", e220_err_text(e));
        char msg[160];
        int k = snprintf(msg, sizeof msg, "ERR %s（読めた値", e220_err_text(e));
        for (int i = 0; i < E220_REG_COUNT && k < (int)sizeof msg - 8; i++) {
            k += snprintf(msg + k, sizeof msg - (size_t)k, " %02X", got[i]);
        }
        snprintf(msg + k, sizeof msg - (size_t)k, "）");
        /* コマンドは受け付ける＝channel などで抜けられる（NVS を消さずに済む）。 */
        char cmd[96];
        char ans[96];
        for (;;) {
            link_send_text(msg);
            int64_t until = esp_timer_get_time() + 1000000LL;
            while (esp_timer_get_time() < until) {
                if (link_read_line(cmd, sizeof cmd) > 0) {
                    bool changed = settings_apply_command(cmd, &settings, ans, sizeof ans);
                    link_send_text(ans);
                    if (changed) {
                        vTaskDelay(pdMS_TO_TICKS(100));
                        esp_restart();
                    }
                }
            }
        }
    }
    radiosim_field_config_t config = make_config(&settings, mac);
    radio_run(&settings, mac, &config);  /* TX はこの設定を空中へも 10 秒ごとに送る */
    link_send_config(&config);

    int64_t last_config = esp_timer_get_time();
    char line[96];
    char reply[96];
    for (;;) {
        if (link_read_line(line, sizeof line) > 0) {
            if (handle_continuous(line)) {
                last_config = esp_timer_get_time();
                continue;
            }
            if (settings_apply_command(line, &settings, reply, sizeof reply)) {
                link_send_text(reply);
                vTaskDelay(pdMS_TO_TICKS(100));        /* 返答を送り切ってから */
                esp_restart();
            }
            link_send_text(reply);
            link_send_config(&config);
            last_config = esp_timer_get_time();
        }
        if (esp_timer_get_time() - last_config >= CONFIG_RESEND_MS * 1000LL) {
            link_send_config(&config);
            last_config = esp_timer_get_time();
        }
    }
}
