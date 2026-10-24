/*
 * led.c — 台の表示の LED（led.h）。
 *
 * ピンは台の配線（HARDWARE.md §3・pcb/field_carrier.kicad_sch）と同じ＝High で点く
 * （GPIO → 330 Ω → アノード・カソードは GND）。E220（GPIO0・1・2・18・21）・RF スイッチ
 * （GPIO3・14）・ログの口（GPIO16・17）とは重ならない。
 *
 * 消すのは esp_timer の 1 回きり（LED ごとに 1 つ）。点けるときは**止める→点ける→始める**の
 * 順＝タイマーの関数（esp_timer のタスク＝優先度が高い）が点けた直後に割り込んで消しても、
 * 止めた後なので割り込めない。
 */
#include "led.h"

#include <stdint.h>

#include "driver/gpio.h"
#include "esp_timer.h"

#define LED_ON_US 30000

static const gpio_num_t PINS[LED_COUNT] = {
    [LED_920_TX] = GPIO_NUM_22,     /* D4 → R1 → D2 の赤 */
    [LED_920_RX] = GPIO_NUM_23,     /* D5 → R2 → D2 の緑 */
    [LED_2400_TX] = GPIO_NUM_19,    /* D8 → R3 → D3 の赤 */
    [LED_2400_RX] = GPIO_NUM_20,    /* D9 → R4 → D3 の緑 */
};

static esp_timer_handle_t s_off[LED_COUNT];

static void turn_off(void *arg)
{
    gpio_set_level(PINS[(led_t)(intptr_t)arg], 0);
}

void led_init(void)
{
    uint64_t mask = 0;
    for (int i = 0; i < LED_COUNT; i++) mask |= 1ULL << PINS[i];
    gpio_config_t io = {.pin_bit_mask = mask, .mode = GPIO_MODE_OUTPUT};
    for (int i = 0; i < LED_COUNT; i++) gpio_set_level(PINS[i], 0);
    ESP_ERROR_CHECK(gpio_config(&io));
    for (int i = 0; i < LED_COUNT; i++) {
        esp_timer_create_args_t args = {.callback = turn_off, .arg = (void *)(intptr_t)i,
                                        .name = "led"};
        if (esp_timer_create(&args, &s_off[i]) != ESP_OK) s_off[i] = NULL;   /* 点けたままにはしない */
    }
}

void led_flash(led_t which)
{
    esp_timer_handle_t t = s_off[which];
    if (t == NULL) return;
    esp_timer_stop(t);                       /* 動いていなければ ESP_ERR_INVALID_STATE＝構わない */
    gpio_set_level(PINS[which], 1);
    if (esp_timer_start_once(t, LED_ON_US) != ESP_OK) gpio_set_level(PINS[which], 0);
}

void led_hold(led_t which)
{
    if (s_off[which] != NULL) esp_timer_stop(s_off[which]);
    gpio_set_level(PINS[which], 1);
}
