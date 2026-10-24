/*
 * led.h — 台の表示の LED（HARDWARE.md §3「表示の LED」）＝920 と 2.4 の送信（赤）・受信（緑）。
 *
 * 電源の LED は 5V から抵抗で常時点灯＝ここでは触らない。E220 の AUX は送信と受信の両方で
 * 下がり、ESP32-C6 の無線にも送信中を示す端子が無い＝送受を知っているファームが点ける。
 * 表示だけの物＝失敗しても測定は止めない。
 */
#pragma once

typedef enum {
    LED_920_TX,
    LED_920_RX,
    LED_2400_TX,
    LED_2400_RX,
    LED_COUNT,
} led_t;

/* ピンを出力（消灯）にし、消すためのタイマーを用意する。無線より先に呼ぶ。 */
void led_init(void);

/* 約 30 ms 点ける。点いている間に来たら、そこから 30 ms へ延ばす。 */
void led_flash(led_t which);

/* 点けたままにする（連続送信＝終わりは再起動で消える）。 */
void led_hold(led_t which);
