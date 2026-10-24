/*
 * radio_e220.c — 空中のパケット（§6.1-7C・§6.6-③）＝E220-900T22S(JP)・920 MHz（下回りは e220.c・
 * 2.4 GHz は radio_wifi.c）。
 *
 * 空中の形＝E220 の透過送信の中身に、RADIOSIM_FIELD_TX_PACKET の MAVLink v2 フレームを
 * そのまま入れる（入れ物の頭は付けない）⇒ 空中の形も XML が単一の出所で、受け側は CRC で
 * 化けと他の機器の電波を弾ける。
 *
 * TX は同じ形で **RADIOSIM_FIELD_CONFIG を 10 秒ごと・TX_PACKET の周期を 1 つ借りて**送り、RX はそれを
 * UART へ中継する（§6.6 の提案②）＝TX の UART は遠くにあるので、TX の実際の設定を PC が
 * 知る道は空中しかない。1 秒ごとだと 32 B の TX_PACKET と合わせてデューティが 10% を超える。
 *
 * **seq は周期の番号**（I-165）＝送れなかった周期も進む（PC は seq を時間の軸に使う）。
 * 前の送信が電波に乗ったかは**次に送る直前に**読む:
 *   - AUX が Low＝前の送信がまだ終わっていない（キャリアセンスで待たされている）→ この回は送らない
 *   - High＝0xA1 を読む（検査和の誤り・送信あふれ・キャリアセンスの時間切れのどれかなら
 *     前は送れていない）→ フラグを消す → 送る
 *   - 応答が読めない回は「送った」とみなす（悲観側＝受からなかった方へ数える）
 * 結果は sent_mask（直前 32 周期）と unsent_total（起動からの累計）で PC へ届く。
 *
 * RX は空中の枠ごとに 0xA0 から 5 バイト（0xA0〜0xA4）を読み、0xA3（環境ノイズ）を
 * noise_floor_raw にする（読めない回は最後に読めた値）。受信の時刻は AUX の立ち下がり。
 *
 * 連続送信（cont）は 920 MHz には無い（2.4 GHz だけ＝radio_wifi.c）＝AUX の Low は空中の
 * 時間と同じでないので、デューティを測れない（測り方は I-242）。
 */
#include "radio.h"

#include <stdbool.h>
#include <string.h>

#include "e220.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "led.h"
#include "link.h"
#include "radiosim_field_mavlink.h"

#define AIR_SYSID 1
#define AIR_COMPID 1
#define AIR_CONFIG_EVERY_US 10000000LL
#define STATUS_WAIT_MS 100          /* mode 0 の命令の応答を待つ長さ */
#define CMD_AUX_WAIT_US 50000       /* 命令の後 AUX が 13〜14 ms Low になる（送信ではない） */
#define RX_FRAME_WAIT_MS 1000

/* 0xA1 のうち「その送信は電波に乗っていない」ビット＝bit2 キャリアセンスの時間切れ・
 * bit1 送信あふれ・bit0 検査和の誤り。 */
#define A1_NOT_SENT 0x07
/* 0xA0 の bit0＝受信あふれ（モジュールの中で受けた枠を捨てた）。 */
#define A0_RX_OVERFLOW 0x01

/* サンプルの通し番号を数える送信機の数。近くに別の組が居ても自分の組の番号が
 * 途切れないように、送信機ごとに数える。 */
#define SAMPLE_COUNTERS 4

typedef struct {
    bool used;
    uint8_t tx_id[6];
    uint32_t next;
} sample_counter_t;

typedef enum { PEND_NONE, PEND_PACKET, PEND_OTHER } pending_t;

static radiosim_field_settings_t s_settings;
static radiosim_field_config_t s_config;
static uint8_t s_mac[6];
static sample_counter_t s_counters[SAMPLE_COUNTERS];
static unsigned s_counter_victim;

/* --- mode 0 の命令の応答 -------------------------------------------------------- */

/* cmd（C1/F0）・addr の応答を待つ。届いた他機のデータ枠は捨てる（TX だけが使う）。 */
static bool wait_reply(uint8_t cmd, uint8_t addr, e220_frame_t *f)
{
    int64_t end = esp_timer_get_time() + STATUS_WAIT_MS * 1000LL;
    for (;;) {
        int left_ms = (int)((end - esp_timer_get_time()) / 1000);
        if (left_ms <= 0) return false;
        if (e220_read_frame(f, left_ms) == E220_FRAME_REPLY && f->len >= 5 && f->data[2] == cmd
            && f->data[3] == addr) {
            return true;
        }
    }
}

static void wait_aux_after_command(void)
{
    int64_t end = esp_timer_get_time() + CMD_AUX_WAIT_US;
    while (!e220_aux_high() && esp_timer_get_time() < end) vTaskDelay(1);
}

/* --- TX --------------------------------------------------------------------- */

static uint32_t s_seq;              /* 今の周期 */
static uint64_t s_sent_bits;        /* 第 k ビット＝周期 s_seq−1−k を送った（結果待ちは 1） */
static uint32_t s_unsent;           /* 起動から送らなかった周期の累計 */
static pending_t s_pending = PEND_OTHER;   /* 起動直後は前の起動のフラグが残りうる＝消す */
static uint32_t s_pending_seq;

static void mark_unsent(uint32_t cycle)
{
    uint32_t k = s_seq - 1 - cycle;
    if (k < 64) s_sent_bits &= ~(1ULL << k);
    s_unsent++;
}

/* 送る直前の段取り。前の送信がまだなら false（この回は送らない）。前の送信の結果を
 * 読んで記録し、フラグを消す。設定の空中送信の失敗は数えない（10 秒後に送り直す）。 */
static bool ready_to_send(void)
{
    if (!e220_aux_high()) return false;
    if (s_pending != PEND_NONE) {
        e220_frame_t f;
        e220_request_status(0xA1, 1);
        if (wait_reply(0xC1, 0xA1, &f) && f.len == 6 && (f.data[5] & A1_NOT_SENT) != 0) {
            if (s_pending == PEND_PACKET) mark_unsent(s_pending_seq);
        }
        e220_clear_flags(0xA1);
        wait_reply(0xF0, 0xA1, &f);
        s_pending = PEND_NONE;
        wait_aux_after_command();
    }
    return true;
}

static void tx_task(void *arg)
{
    (void)arg;
    uint8_t frame[RADIOSIM_FIELD_MAX_FRAME_LEN];
    uint8_t air_link_seq = 0;
    int64_t last_config = -AIR_CONFIG_EVERY_US;
    TickType_t last = xTaskGetTickCount();
    const TickType_t period = pdMS_TO_TICKS(s_settings.b920.tx_interval_ms);
    for (;;) {
        xTaskDelayUntil(&last, period);
        bool ready = ready_to_send();
        int64_t now = esp_timer_get_time();
        /* 設定は 10 秒ごとに周期を 1 つ借りる（その周期の TX_PACKET は送らない＝sent_mask に出る）。
         * 1 周期に 2 枠は入らない＝問い合わせ 2 つで約 60 ms・32 B の UART 入力で約 34 ms・
         * 送信の後 AUX が約 130 ms Low（実機・2026-10-18）で、250 ms の周期の約 220 ms を使う。
         * 設定（52 B）は長いので次の周期も AUX が Low のまま＝実機では 10 秒ごとに 2 周期送らない。 */
        bool config_turn = ready && now - last_config >= AIR_CONFIG_EVERY_US;
        bool sent = ready && !config_turn;
        radiosim_field_tx_packet_t p = {
            .seq = s_seq,
            .sent_mask = (uint32_t)s_sent_bits,
            .unsent_total = s_unsent + (sent ? 0 : 1),
            .config_id = s_settings.config_id,
        };
        memcpy(p.tx_id, s_mac, 6);
        if (sent) {
            size_t n = radiosim_field_tx_packet_pack(frame, air_link_seq++, AIR_SYSID, AIR_COMPID, &p);
            e220_send(frame, n);
            led_flash(LED_920_TX);
            s_pending = PEND_PACKET;
            s_pending_seq = s_seq;
        } else {
            s_unsent++;
            if (config_turn) {
                size_t n = radiosim_field_config_pack(frame, air_link_seq++, AIR_SYSID, AIR_COMPID, &s_config);
                e220_send(frame, n);
                led_flash(LED_920_TX);
                s_pending = PEND_OTHER;
                last_config = now;
            }
        }
        link_send_tx_packet(&p);                     /* USB の控えは送らなかった周期も出す */
        s_sent_bits = (s_sent_bits << 1) | (sent ? 1u : 0u);
        s_seq++;
    }
}

/* --- RX --------------------------------------------------------------------- */

/* 送信機ごとのサンプルの通し番号。**受信した時点で振る**＝ここから先（USB の取りこぼし・
 * UART の化け）で落ちた分は、PC 側で番号の欠けとして見える。 */
static uint32_t next_sample_seq(const uint8_t tx_id[6])
{
    sample_counter_t *slot = NULL;
    for (int i = 0; i < SAMPLE_COUNTERS; i++) {
        if (s_counters[i].used && memcmp(s_counters[i].tx_id, tx_id, 6) == 0) {
            return s_counters[i].next++;
        }
        if (slot == NULL && !s_counters[i].used) slot = &s_counters[i];
    }
    if (slot == NULL) {
        /* 表が一杯＝順番に 1 つ追い出す。追い出された送信機は次に 0 から数え直す
         * （PC 側は番号が戻ったところを数えないので、落ちたことにはならない）。 */
        slot = &s_counters[s_counter_victim];
        s_counter_victim = (s_counter_victim + 1) % SAMPLE_COUNTERS;
    }
    slot->used = true;
    memcpy(slot->tx_id, tx_id, 6);
    slot->next = 0;
    return slot->next++;
}

static int16_t s_noise = 0;                 /* 最後に読めた環境ノイズ（RSSI バイト − 256） */
static bool s_have_sample;                  /* 雑音の読み出しを待っているサンプル */
static radiosim_field_rx_sample_t s_sample;

static void flush_sample(void)
{
    if (!s_have_sample) return;
    s_sample.noise_floor_raw = s_noise;
    link_send_rx_sample(&s_sample);
    s_have_sample = false;
}

static void on_data(const e220_frame_t *f)
{
    uint32_t msgid;
    const uint8_t *payload;
    uint8_t payload_len;
    if (radiosim_field_frame_parse(f->data, f->len, &msgid, &payload, &payload_len) == 0) return;
    led_flash(LED_920_RX);                       /* CRC の通った枠だけ（他の機器の電波では点けない） */
    if (msgid == MSGID_RADIOSIM_FIELD_CONFIG) {
        radiosim_field_config_t c;
        radiosim_field_config_decode_payload(payload, payload_len, &c);
        /* 中継するのは TX の設定だけ。RX の設定を中継すると、PC は「自分の RX の設定が
         * 変わった」と読んでセッションを終えてしまう（RX は空中へ送らないが、念のため）。 */
        if (c.role == RADIOSIM_FIELD_ROLE_TX) link_send_config(&c);
        return;
    }
    if (msgid != MSGID_RADIOSIM_FIELD_TX_PACKET) return;
    flush_sample();                              /* 前の枠の雑音が読めないまま次が来た */
    radiosim_field_tx_packet_t p;
    radiosim_field_tx_packet_decode_payload(payload, payload_len, &p);
    memset(&s_sample, 0, sizeof s_sample);
    s_sample.seq = p.seq;
    s_sample.rx_time_us = (uint64_t)(f->aux_fall_us != 0 ? f->aux_fall_us : esp_timer_get_time());
    s_sample.rssi_raw = f->rssi_raw;
    s_sample.config_id = s_settings.config_id;  /* RX の設定番号（PC 側が引く番号） */
    s_sample.tx_config_id = p.config_id;
    s_sample.sent_mask = p.sent_mask;
    s_sample.unsent_total = p.unsent_total;
    memcpy(s_sample.rx_id, s_mac, 6);
    memcpy(s_sample.tx_id, p.tx_id, 6);
    s_sample.sample_seq = next_sample_seq(p.tx_id);
    s_have_sample = true;
    e220_request_status(0xA0, 5);
}

static void on_reply(const e220_frame_t *f)
{
    /* FF FF C1 A0 05 d(A0) d(A1) d(A2) d(A3) d(A4) */
    if (f->len != 10 || f->data[2] != 0xC1 || f->data[3] != 0xA0 || f->data[4] != 5) return;
    s_noise = (int16_t)((int)f->data[8] - 256);
    flush_sample();
    if ((f->data[5] & A0_RX_OVERFLOW) != 0) {
        /* モジュールの中で捨てた枠は seq の欠け（電波で届かなかった分）と同じに見える。
         * 数える道は今は無い＝フラグだけ消して次の枠を見逃さない。 */
        e220_clear_flags(0xA0);
    }
}

static void rx_task(void *arg)
{
    (void)arg;
    e220_frame_t f;
    for (;;) {
        switch (e220_read_frame(&f, s_have_sample ? STATUS_WAIT_MS : RX_FRAME_WAIT_MS)) {
        case E220_FRAME_DATA: on_data(&f); break;
        case E220_FRAME_REPLY: on_reply(&f); break;
        default: flush_sample(); break;          /* 雑音の応答が来なかった＝最後の値で出す */
        }
    }
}

/* --- 起動 ------------------------------------------------------------------- */

void radio_e220_run(const radiosim_field_settings_t *s, const uint8_t mac[6],
                    const radiosim_field_config_t *config)
{
    s_settings = *s;
    memcpy(s_mac, mac, 6);
    s_config = *config;
    e220_enter_normal();
    if (s_settings.role == RADIOSIM_FIELD_ROLE_TX) {
        xTaskCreate(tx_task, "radiosim_field_tx", 4096, NULL, 5, NULL);
    } else {
        xTaskCreate(rx_task, "radiosim_field_rx", 4096, NULL, 5, NULL);
    }
}
