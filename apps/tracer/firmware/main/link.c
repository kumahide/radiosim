/*
 * link.c — USB（USB Serial/JTAG）の書き手と、コマンドの読み手。
 */
#include "link.h"

#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

/* MAVLink の system / component。PC 側はどちらも測定に使わない。 */
#define SYSID 1
#define COMPID 1
/* USB が詰まっているときに待つ上限。待ち続けると受信の処理が止まり、空中の
 * パケットを取りこぼす（そちらのほうが見分けの付かない欠けになる）。 */
#define WRITE_WAIT pdMS_TO_TICKS(20)

static SemaphoreHandle_t s_lock;
static uint8_t s_link_seq;
static uint32_t s_dropped;
static char s_line[96];
static size_t s_line_len;

void link_init(void)
{
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    cfg.tx_buffer_size = 4096;
    cfg.rx_buffer_size = 256;
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
    s_lock = xSemaphoreCreateMutex();
}

static void write_all(const uint8_t *data, size_t n)
{
    int written = usb_serial_jtag_write_bytes(data, n, WRITE_WAIT);
    if (written != (int)n) {
        /* 途中まで書けたフレームは PC 側で CRC 不一致か末尾の切れとして数えられる。
         * 1 バイトも書けなかった分はここでしか分からない。 */
        s_dropped++;
    }
}

#define SEND(pack_fn, m)                                                   \
    do {                                                                   \
        uint8_t frame[TRACER_MAX_FRAME_LEN];                               \
        xSemaphoreTake(s_lock, portMAX_DELAY);                             \
        size_t n = pack_fn(frame, s_link_seq++, SYSID, COMPID, (m));       \
        write_all(frame, n);                                               \
        xSemaphoreGive(s_lock);                                            \
    } while (0)

void link_send_tx_packet(const tracer_tx_packet_t *m) { SEND(tracer_tx_packet_pack, m); }
void link_send_rx_sample(const tracer_rx_sample_t *m) { SEND(tracer_rx_sample_pack, m); }
void link_send_config(const tracer_config_t *m) { SEND(tracer_config_pack, m); }

void link_send_text(const char *line)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    write_all((const uint8_t *)line, strlen(line));
    write_all((const uint8_t *)"\r\n", 2);
    xSemaphoreGive(s_lock);
}

size_t link_read_line(char *buf, size_t len)
{
    uint8_t c;
    while (usb_serial_jtag_read_bytes(&c, 1, pdMS_TO_TICKS(50)) == 1) {
        if (c == '\r' || c == '\n') {
            if (s_line_len == 0) continue;
            size_t n = s_line_len < len - 1 ? s_line_len : len - 1;
            memcpy(buf, s_line, n);
            buf[n] = '\0';
            s_line_len = 0;
            return n;
        }
        if (s_line_len < sizeof s_line) s_line[s_line_len++] = (char)c;
    }
    return 0;
}

uint32_t link_dropped(void) { return s_dropped; }
