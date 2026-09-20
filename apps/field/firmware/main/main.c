/*
 * main.c — RadioSim Fieldのファーム（XIAO ESP32C6・phase 1・増分5）。
 *
 * 送信と受信は同じファームで、役割は設定で切り替える（§6.1-7E）。
 * 流れ: NVS → 設定 → 自己検査 → アンテナを外部側へ → 無線を起こす → 以後、
 *       設定を 1 秒ごとに送りながら、USB からのコマンドを待つ。
 *
 * 設定（RADIOSIM_FIELD_CONFIG）は起動時・変更時に加えて **1 秒ごとに再送する**（§6.6-①）＝
 * PC 側は最初に届いた RX の設定でセッションを作るので、「起動時だけ」だと RX が
 * 先に起動していたら記録が始まらない。同じ設定の再送は PC 側が読み飛ばす。
 */
#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
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

/* XIAO ESP32C6 のアンテナ切替（RF スイッチ）。⚠️ ピンと論理は販売元の資料による
 * （一次資料で未確認）: GPIO3 を Low でスイッチを有効に、GPIO14 を High で外部側。
 * 実機では確かめた＝外部アンテナを外すと受信レベルが約 45 dB 下がる（README）。
 * 既定はオンボード側なので、ここで明示的に外部側を選ぶ（§7 機材）。 */
#define RF_SWITCH_ENABLE_GPIO GPIO_NUM_3
#define RF_SWITCH_SELECT_GPIO GPIO_NUM_14

static void select_external_antenna(void)
{
    gpio_config_t io = {
        .pin_bit_mask = (1ULL << RF_SWITCH_ENABLE_GPIO) | (1ULL << RF_SWITCH_SELECT_GPIO),
        .mode = GPIO_MODE_OUTPUT,
    };
    ESP_ERROR_CHECK(gpio_config(&io));
    ESP_ERROR_CHECK(gpio_set_level(RF_SWITCH_ENABLE_GPIO, 0));
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_ERROR_CHECK(gpio_set_level(RF_SWITCH_SELECT_GPIO, 1));
}

static radiosim_field_config_t make_config(const radiosim_field_settings_t *s, const uint8_t mac[6],
                                   int8_t actual_power_qdbm)
{
    radiosim_field_config_t c = {
        .config_id = s->config_id,
        .role = s->role,
        .channel = s->channel,
        .rate_kbps = RADIO_AIR_RATE_KBPS,     /* TX は起動時に実際のレートで確かめ済み */
        .tx_power_cdbm = (int16_t)(actual_power_qdbm * 25),
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

/* `cont SECONDS`（1〜600）＝連続送信・`cont 0`＝中止。TX だけ。設定の保存はせず
 * （電源を入れ直せば普段の送信に戻る＝現場で連送し続ける機器を作らない）、config_id だけ
 * 先に進める。コマンドだったら true。 */
static bool handle_continuous(const char *line, radiosim_field_settings_t *settings)
{
    unsigned long seconds;
    char extra;
    if (strncmp(line, "cont", 4) != 0) return false;
    if (sscanf(line, "cont %lu %c", &seconds, &extra) != 1 || seconds > 600) {
        link_send_text("ERR cont は 0〜600 秒です（0 で中止）");
        return true;
    }
    if (settings->role != RADIOSIM_FIELD_ROLE_TX) {
        link_send_text("ERR cont は TX だけです（role tx にしてから）");
        return true;
    }
    if (seconds == 0) {
        radio_continuous(0);
        link_send_text("OK cont 中止します");
        return true;
    }
    settings_bump_config_id(settings);
    char reply[96];
    snprintf(reply, sizeof reply, "OK cont %lu 秒 config_id=%u 終わったら再起動します", seconds,
             settings->config_id);
    link_send_text(reply);
    radio_continuous((uint32_t)seconds);
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

    select_external_antenna();
    int8_t actual_power_qdbm = 0;
    radio_start(&settings, mac, &actual_power_qdbm);
    if (settings.role == RADIOSIM_FIELD_ROLE_TX) {
        uint16_t actual_kbps = 0;
        if (!radio_verify_rate(&actual_kbps)) {
            /* 刻印（1 Mbps）と違うレートで測ると、受信感度が違うのに同じ条件として
             * 記録される。測定を始めないほうがまし（B-257）。 */
            ESP_LOGE(TAG, "空中のレートが %u kbps です（期待 %u）", actual_kbps,
                     (unsigned)RADIO_AIR_RATE_KBPS);
            char msg[128];
            snprintf(msg, sizeof msg, "ERR 空中のレートが 1 Mbps になりません（実際 %u kbps・0＝不明）",
                     actual_kbps);
            /* コマンドは受け付ける＝role rx などで抜けられる（NVS を消さずに済む）。 */
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
    }
    radiosim_field_config_t config = make_config(&settings, mac, actual_power_qdbm);
    radio_run(&config);                 /* TX はこの設定を空中へも 1 秒ごとに送る */
    link_send_config(&config);

    int64_t last_config = esp_timer_get_time();
    char line[96];
    char reply[96];
    for (;;) {
        if (link_read_line(line, sizeof line) > 0) {
            if (handle_continuous(line, &settings)) {
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
