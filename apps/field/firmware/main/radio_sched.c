/*
 * radio_sched.c — TX の送信の予定（I-252）。送信のタスクはここの 1 本だけ＝帯域ごとの「1 周期」
 * （radio_e220_tx_cycle・radio_wifi_tx_cycle）を予定の時刻に呼ぶ。
 *
 * 1 帯域＝今までと同じ＝その帯域の送信間隔ごとに 1 周期（tick で刻む）。
 *
 * both＝920 のブロック → 2.4 のブロック → … を再起動なしで繰り返す（各 block_ms）。
 *   - 各ブロックの頭から送信間隔ごとに n＝floor(block_ms ÷ 間隔) 周期。seq はその帯域の
 *     ブロックの中でだけ進む＝RX から見て帯域ごとに途切れない番号（相手のブロックの間を
 *     欠けに数えない）。
 *   - 予定は **µs の絶対時刻**（起点＋c·P＋k·間隔・P＝2·block_ms）＝tick の丸めが積もらない。
 *     PC は番号 s の帯域内の時刻を (s ÷ n の商)·P ＋ (s mod n)·間隔 と読む（CONFIG.block_ms）
 *     ので、ここが式からずれていくと、置き場所の区切りに入る番号が狂う。
 *   - **送信は同時にしない**（正典 §4）＝2.4 の各周期の直前に E220 の AUX を見て、Low（920 の
 *     送信がまだ終わっていない・キャリアセンスで遅れた）なら送らない周期に数える（予定の時刻は
 *     ずらさない）。2.4 の送信は 1 周期の中で終わる（1 Mbps・数百 µs）ので、920 の頭で待つ必要はない。
 */
#include "radio.h"

#include "e220.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TICK_US ((int64_t)portTICK_PERIOD_MS * 1000LL)

static radiosim_field_settings_t s_settings;

/* at（esp_timer の µs）まで待つ。過ぎていればすぐ戻る（遅れは次の周期で取り戻す＝予定はずらさない）。 */
static void wait_until_us(int64_t at)
{
    int64_t left = at - esp_timer_get_time();
    if (left > 0) vTaskDelay((TickType_t)((left + TICK_US - 1) / TICK_US));
}

static void single_band_task(void *arg)
{
    (void)arg;
    const bool is2400 = s_settings.band == RADIOSIM_FIELD_BAND_2400;
    const TickType_t period = pdMS_TO_TICKS(settings_band(&s_settings, s_settings.band)->tx_interval_ms);
    TickType_t last = xTaskGetTickCount();
    for (;;) {
        xTaskDelayUntil(&last, period);
        if (is2400) {
            radio_wifi_tx_cycle(false);
        } else {
            radio_e220_tx_cycle();
        }
    }
}

static void both_bands_task(void *arg)
{
    (void)arg;
    const int64_t block_us = (int64_t)s_settings.block_ms * 1000LL;
    const int64_t i920_us = (int64_t)s_settings.b920.tx_interval_ms * 1000LL;
    const int64_t i2400_us = (int64_t)s_settings.b2400.tx_interval_ms * 1000LL;
    const uint32_t n920 = s_settings.block_ms / s_settings.b920.tx_interval_ms;     /* ≥ 1（settings が確かめた） */
    const uint32_t n2400 = s_settings.block_ms / s_settings.b2400.tx_interval_ms;
    int64_t start = esp_timer_get_time() + i920_us;     /* 1 帯域と同じく、最初の周期は 1 間隔あとから */
    for (;;) {
        for (uint32_t k = 0; k < n920; k++) {
            wait_until_us(start + (int64_t)k * i920_us);
            radio_e220_tx_cycle();
        }
        start += block_us;
        for (uint32_t k = 0; k < n2400; k++) {
            wait_until_us(start + (int64_t)k * i2400_us);
            radio_wifi_tx_cycle(!e220_aux_high());
        }
        start += block_us;
    }
}

void radio_tx_run(const radiosim_field_settings_t *s)
{
    s_settings = *s;
    xTaskCreate(s->band == SETTINGS_BAND_BOTH ? both_bands_task : single_band_task, "radiosim_field_tx",
                4096, NULL, 5, NULL);
}
