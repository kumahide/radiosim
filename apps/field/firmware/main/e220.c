/*
 * e220.c — E220-900T22S(JP) の下回り（→ e220.h）。
 *
 * 実機で確かめた形（2026-10-18・2 台とも同じ・作業順 2 の「0 の結果」）:
 *   - 検査和＝XOR。入力は FF FF を除く（例 FF FF C1 A3 01 63）。出力は長さを含み FF FF を除く
 *     （例 07 FF FF C1 A3 01 88 EC＝長さは自分より後ろ全部＝検査和を含む）
 *   - Strict のとき mode 3 の応答も長さ・検査和付き（C1 09 01 C9 → 05 C1 09 01 5C 90）。
 *     ただし 0x09 を書いた直後の応答の形は前後の値で変わる＝書いた後は読み直しで判定する
 *   - 読み出しの長さに上限＝0x00 からは 9 バイトまで＝0x00〜0x08 と 0x09〜0x0A に分けて読む
 *   - mode 0 の状態の読み出し FF FF C1 A0 05 64 → 0B FF FF C1 A0 05 d0..d4 cs・
 *     フラグ消去 FF FF F0 A1 51 → 06 FF FF F0 A1 00 57。どちらも AUX は 13〜14 ms Low（送信ではない）
 *
 * ⚠️ 実機で未確認（作業順 2 の 3＝2 台を机上で通すときに確かめる）:
 *   - Strict の mode 0 の**受信の出力**＝長さ・中身・RSSI・検査和（下の parse の形）
 *   - Strict の mode 0 の**送信の入力**に検査和を付けること（0xA1 の bit0＝検査和の誤りが
 *     「送れなかった」の 1 つなので付ける側と読んだ。データシートの控えで確かめる）
 */
#include "e220.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define U UART_NUM_1
#define PIN_TX GPIO_NUM_0      /* D0  → EV2 3 RXD */
#define PIN_RX GPIO_NUM_1      /* D1  ← EV2 4 TXD */
#define PIN_M0 GPIO_NUM_2      /* D2  → EV2 1 */
#define PIN_M1 GPIO_NUM_21     /* D3  → EV2 2 */
#define PIN_AUX GPIO_NUM_18    /* D10 ← EV2 5 */

#define UART_RX_BUF 1024
#define REPLY_WAIT_MS 400
#define MODE_SETTLE_MS 300
#define AUX_WAIT_MS 2000
#define BYTE_GAP_US 60000      /* 応答のバイトが 60 ms 途切れたら終わり（mode 3 の往復） */

/* 0x02＝UART 9600（bit7:5＝011）｜SF7/BW500（bit4:0＝10010）。 */
#define REG02 0x72
/* 0x03＝環境ノイズの読み出しを有効（bit5）｜出力（bit3:0）。bit7:6（ペイロード長）は 00 のまま。 */
#define REG03_NOISE 0x20
/* 0x05＝透過送信・受信に RSSI バイトを付ける。 */
#define REG05 0x83
/* 0x09＝Strict（bit6）・入力に検査和（bit4）・出力に検査和（bit3）・出力の先頭に長さ（bit2）。 */
#define REG09 0x5C
#define REG09_STRICT 0x40
/* 0x0A＝CS（キャリアセンス）の待ち 100 ms。 */
#define REG0A 0x60

/* AUX の立ち下がりの控え（割り込みが書き、読み手が引く）。 */
#define FALLS 8
static volatile int64_t s_falls[FALLS];
static volatile unsigned s_fall_next;

/* mode 0 の読み取りの溜め（バイトごとに届いた時刻を持つ）。 */
static uint8_t s_buf[2 * E220_FRAME_MAX];
static int64_t s_buf_t[2 * E220_FRAME_MAX];
static size_t s_buf_len;

static void IRAM_ATTR aux_isr(void *arg)
{
    (void)arg;
    s_falls[s_fall_next % FALLS] = esp_timer_get_time();
    s_fall_next++;
}

static uint8_t xor_cs(const uint8_t *b, size_t n)
{
    uint8_t c = 0;
    for (size_t i = 0; i < n; i++) c ^= b[i];
    return c;
}

bool e220_aux_high(void) { return gpio_get_level(PIN_AUX) != 0; }

static bool wait_aux_high(int timeout_ms)
{
    int64_t end = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    while (!e220_aux_high()) {
        if (esp_timer_get_time() > end) return false;
        vTaskDelay(1);
    }
    return true;
}

static void set_mode(int m0, int m1)
{
    gpio_set_level(PIN_M0, m0);
    gpio_set_level(PIN_M1, m1);
    vTaskDelay(pdMS_TO_TICKS(MODE_SETTLE_MS));
    wait_aux_high(AUX_WAIT_MS);
    vTaskDelay(pdMS_TO_TICKS(10));
    uart_flush_input(U);
    s_buf_len = 0;
}

void e220_init(void)
{
    /* mode 3 から始める（M が Low に落ちる瞬間＝送受信の状態を作らない）。 */
    gpio_config_t m = {.pin_bit_mask = (1ULL << PIN_M0) | (1ULL << PIN_M1), .mode = GPIO_MODE_OUTPUT};
    gpio_set_level(PIN_M0, 1);
    gpio_set_level(PIN_M1, 1);
    ESP_ERROR_CHECK(gpio_config(&m));
    gpio_set_level(PIN_M0, 1);
    gpio_set_level(PIN_M1, 1);

    gpio_config_t a = {.pin_bit_mask = 1ULL << PIN_AUX, .mode = GPIO_MODE_INPUT,
                       .intr_type = GPIO_INTR_NEGEDGE};
    ESP_ERROR_CHECK(gpio_config(&a));
    ESP_ERROR_CHECK(gpio_install_isr_service(0));
    ESP_ERROR_CHECK(gpio_isr_handler_add(PIN_AUX, aux_isr, NULL));

    uart_config_t c = {.baud_rate = 9600, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
                       .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
                       .source_clk = UART_SCLK_DEFAULT};
    ESP_ERROR_CHECK(uart_driver_install(U, UART_RX_BUF, 0, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(U, &c));
    ESP_ERROR_CHECK(uart_set_pin(U, PIN_TX, PIN_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    gpio_set_pull_mode(PIN_RX, GPIO_PULLUP_ONLY);
    set_mode(1, 1);
}

/* --- mode 3（設定） ------------------------------------------------------------ */

static uint8_t power_bits(int8_t dbm)
{
    /* 22S の表＝0 dBm は 0011・13 dBm は 0001・1〜12 dBm は 0100〜1111。 */
    if (dbm <= 0) return 0x03;
    if (dbm >= E220_POWER_MAX_DBM) return 0x01;
    return (uint8_t)(dbm + 3);
}

void e220_build_regs(uint8_t channel, int8_t power_dbm, uint8_t out[E220_REG_COUNT])
{
    const uint8_t regs[E220_REG_COUNT] = {
        0x00, 0x00,                          /* アドレスは固定値（透過送信では使わない） */
        REG02,
        (uint8_t)(REG03_NOISE | power_bits(power_dbm)),
        channel,
        REG05,
        0x00, 0x00,                          /* 暗号鍵（使わない） */
        E220_VERSION,
        REG09,
        REG0A,
    };
    memcpy(out, regs, E220_REG_COUNT);
}

/* 送って、応答を最初のバイトから BYTE_GAP_US 途切れるか REPLY_WAIT_MS で打ち切る。 */
static int xfer(const uint8_t *req, size_t n, uint8_t *out, int cap)
{
    uart_flush_input(U);
    uart_write_bytes(U, req, n);
    int got = 0;
    int64_t end = esp_timer_get_time() + REPLY_WAIT_MS * 1000LL;
    int64_t last = 0;
    while (esp_timer_get_time() < end) {
        uint8_t tmp[32];
        int k = uart_read_bytes(U, tmp, sizeof tmp, 1);
        if (k > 0) {
            for (int i = 0; i < k && got < cap; i++) out[got++] = tmp[i];
            last = esp_timer_get_time();
        } else if (got > 0 && esp_timer_get_time() - last > BYTE_GAP_US) {
            break;
        }
    }
    return got;
}

static bool plain_read(uint8_t addr, uint8_t len, uint8_t *data)
{
    uint8_t req[3] = {0xC1, addr, len}, b[32];
    int got = xfer(req, 3, b, sizeof b);
    if (got != 3 + len || b[0] != 0xC1 || b[1] != addr || b[2] != len) return false;
    memcpy(data, b + 3, len);
    return true;
}

/* 応答＝L C1 addr len data cs（L は自分より後ろの数・cs は L から data までの XOR）。 */
static bool strict_read(uint8_t addr, uint8_t len, uint8_t *data)
{
    uint8_t req[4] = {0xC1, addr, len, 0}, b[32];
    req[3] = xor_cs(req, 3);
    int got = xfer(req, 4, b, sizeof b);
    if (got != 5 + len || b[0] != 4 + len || b[1] != 0xC1 || b[2] != addr || b[3] != len
        || xor_cs(b, (size_t)got - 1) != b[got - 1]) {
        return false;
    }
    memcpy(data, b + 4, len);
    return true;
}

static bool read_regs(bool strict, uint8_t regs[E220_REG_COUNT])
{
    bool (*rd)(uint8_t, uint8_t, uint8_t *) = strict ? strict_read : plain_read;
    return rd(0x00, 9, regs) && rd(0x09, 2, regs + 9);
}

/* 1 バイト書く。応答は平文 4 バイトか、長さ・検査和付きの 6 バイトのどちらでも受ける
 * （0x09 を書いた直後は前後の値で形が変わる）。効いたかは呼び手が読み直しで決める。 */
static bool write_reg(bool strict, uint8_t addr, uint8_t val)
{
    uint8_t req[5] = {0xC0, addr, 0x01, val, 0}, b[16];
    size_t n = 4;
    if (strict) {
        req[4] = xor_cs(req, 4);
        n = 5;
    }
    int got = xfer(req, n, b, sizeof b);
    if (got == 4) return b[0] == 0xC1 && b[1] == addr && b[2] == 0x01 && b[3] == val;
    if (got == 6) {
        return b[0] == 0x05 && b[1] == 0xC1 && b[2] == addr && b[3] == 0x01 && b[4] == val
            && xor_cs(b, 5) == b[5];
    }
    return false;
}

e220_err_t e220_configure(uint8_t channel, int8_t power_dbm, uint8_t got[E220_REG_COUNT])
{
    uint8_t want[E220_REG_COUNT];
    e220_build_regs(channel, power_dbm, want);
    memset(got, 0, E220_REG_COUNT);
    set_mode(1, 1);

    /* 前の起動で Strict にしてあれば検査和付きでしか読めない。平文から試す。 */
    bool strict = false;
    if (!read_regs(false, got)) {
        strict = true;
        if (!read_regs(true, got)) {
            memset(got, 0, E220_REG_COUNT);
            return E220_ERR_READ;
        }
    }
    if (got[8] != E220_VERSION) return E220_ERR_VERSION;
    if (memcmp(got, want, E220_REG_COUNT) == 0) return E220_OK;

    /* 0x09（読み書きの形を変える）は最後。0x08（版）は読むだけ。 */
    for (uint8_t addr = 0; addr < E220_REG_COUNT; addr++) {
        if (addr == 0x08 || addr == 0x09 || got[addr] == want[addr]) continue;
        if (!write_reg(strict, addr, want[addr])) return E220_ERR_WRITE;
    }
    if (got[9] != want[9]) {
        write_reg(strict, 0x09, want[9]);      /* 応答の形は当てにしない */
        strict = (want[9] & REG09_STRICT) != 0;
    }
    if (!read_regs(strict, got)) {
        memset(got, 0, E220_REG_COUNT);
        return E220_ERR_VERIFY;
    }
    return memcmp(got, want, E220_REG_COUNT) == 0 ? E220_OK : E220_ERR_VERIFY;
}

const char *e220_err_text(e220_err_t err)
{
    switch (err) {
    case E220_OK: return "OK";
    case E220_ERR_READ: return "E220 の設定が読めません（配線・電源）";
    case E220_ERR_VERSION: return "E220 の版が違います（0x08）";
    case E220_ERR_WRITE: return "E220 の設定を書けません";
    case E220_ERR_VERIFY: return "E220 の設定を読み直すと要求と違います";
    default: return "E220 の不明な誤り";
    }
}

void e220_enter_normal(void) { set_mode(0, 0); }

/* --- mode 0（送受信） ---------------------------------------------------------- */

static void drop_front(size_t n)
{
    memmove(s_buf, s_buf + n, s_buf_len - n);
    memmove(s_buf_t, s_buf_t + n, (s_buf_len - n) * sizeof s_buf_t[0]);
    s_buf_len -= n;
}

/* t 以前の最後の AUX の立ち下がり（無ければ 0）。 */
static int64_t fall_before(int64_t t)
{
    int64_t best = 0;
    for (int i = 0; i < FALLS; i++) {
        int64_t f = s_falls[i];
        if (f != 0 && f <= t && f > best) best = f;
    }
    return best;
}

/* 溜めの先頭から枠を 1 つ切り出せたら true。合わない先頭は 1 バイト捨てる。 */
static bool parse_front(e220_frame_t *f)
{
    while (s_buf_len >= 1) {
        size_t l = s_buf[0];
        if (l < 3 || l + 1 > sizeof s_buf) {
            drop_front(1);
            continue;
        }
        if (s_buf_len < l + 1) return false;                 /* まだ揃っていない */
        if (xor_cs(s_buf, l) != s_buf[l]) {
            drop_front(1);
            continue;
        }
        if (s_buf[1] == 0xFF && s_buf[2] == 0xFF) {
            f->kind = E220_FRAME_REPLY;
            f->len = (uint8_t)(l - 1);                       /* FF FF … 検査和の前まで */
            memcpy(f->data, s_buf + 1, f->len);
            f->rssi_raw = 0;
            f->aux_fall_us = 0;
        } else {
            f->kind = E220_FRAME_DATA;
            f->len = (uint8_t)(l - 2);                       /* 中身＝長さ・RSSI・検査和を除く */
            memcpy(f->data, s_buf + 1, f->len);
            f->rssi_raw = (int16_t)((int)s_buf[l - 1] - 256);
            f->aux_fall_us = fall_before(s_buf_t[0]);
        }
        drop_front(l + 1);
        return true;
    }
    return false;
}

e220_frame_kind_t e220_read_frame(e220_frame_t *f, int timeout_ms)
{
    int64_t end = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    for (;;) {
        if (parse_front(f)) return f->kind;
        int64_t left = end - esp_timer_get_time();
        if (left <= 0) {
            /* 揃わないまま止まった欠片は捨てる（次の枠の頭と混ざらないように）。 */
            if (s_buf_len > 0 && esp_timer_get_time() - s_buf_t[s_buf_len - 1] > BYTE_GAP_US) {
                s_buf_len = 0;
            }
            return E220_FRAME_NONE;
        }
        uint8_t tmp[64];
        TickType_t wait = pdMS_TO_TICKS(left / 1000) > 0 ? pdMS_TO_TICKS(left / 1000) : 1;
        int k = uart_read_bytes(U, tmp, sizeof tmp, wait < pdMS_TO_TICKS(20) ? wait : pdMS_TO_TICKS(20));
        if (k <= 0) continue;
        int64_t now = esp_timer_get_time();
        for (int i = 0; i < k; i++) {
            if (s_buf_len == sizeof s_buf) drop_front(1);
            s_buf[s_buf_len] = tmp[i];
            s_buf_t[s_buf_len] = now;
            s_buf_len++;
        }
    }
}

void e220_request_status(uint8_t addr, uint8_t len)
{
    uint8_t req[6] = {0xFF, 0xFF, 0xC1, addr, len, 0};
    req[5] = xor_cs(req + 2, 3);
    uart_write_bytes(U, req, sizeof req);
}

void e220_clear_flags(uint8_t addr)
{
    uint8_t req[5] = {0xFF, 0xFF, 0xF0, addr, 0};
    req[4] = xor_cs(req + 2, 2);
    uart_write_bytes(U, req, sizeof req);
}

void e220_send(const uint8_t *data, size_t n)
{
    uint8_t cs = xor_cs(data, n);
    uart_write_bytes(U, data, n);
    uart_write_bytes(U, &cs, 1);
    uart_wait_tx_done(U, pdMS_TO_TICKS(500));
}
