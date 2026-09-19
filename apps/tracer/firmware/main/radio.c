/*
 * radio.c — 空中のパケット（§6.1-7C・§6.6-③）。
 *
 * 空中の形＝ベンダー固有のアクションフレームの本体に、TRACER_TX_PACKET の
 * MAVLink v2 フレームをそのまま入れる:
 *
 *   802.11 ヘッダ（24）| カテゴリ 127 | OUI（3）| 識別子 "RSTR"（4）| MAVLink フレーム
 *
 * ⇒ 空中の形も XML が単一の出所で、受け側は CRC で化けを弾ける。
 *
 * TX は同じ入れ物で **TRACER_CONFIG も 1 秒ごとに**送り、RX はそれを UART へ中継する
 * （§6.6 の提案②）＝TX の UART は遠くにあるので、TX の実際の設定を PC が知る道は
 * 空中しかない。
 *
 * 空中のレートは 1 Mbps（11b）に固定し、**ドライバが実際に使ったレートを送信完了の
 * 通知で確かめる**（B-257）。レートの指定は esp_wifi_config_80211_tx で行う。
 * ⚠️ esp_wifi_config_80211_tx_rate は使わない＝ESP-IDF v6.1・ESP32-C6 の実機で、
 *    esp_wifi_start の後に呼ぶと ESP_OK を返すのに効かず（11 Mbps を指定しても
 *    1 Mbps で出た＝既定値がたまたま 1 Mbps だっただけ）、前に呼ぶと ESP_FAIL。
 *
 * ⚠️ OUI の値（下の TRACER_AIR_OUI）は仮置き。登録された値ではない。
 */
#include "radio.h"

#include <stdbool.h>
#include <string.h>

#include "esp_event.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "link.h"
#include "tracer_mavlink.h"

#define WLAN_HEADER_LEN 24
#define FCS_LEN 4
#define ACTION_CATEGORY_VENDOR 127
static const uint8_t TRACER_AIR_OUI[3] = {0x02, 0x52, 0x53};   /* 仮置き（未登録） */
static const uint8_t TRACER_AIR_TAG[4] = {'R', 'S', 'T', 'R'};
/* レートの確かめ用。RX は識別子が違うので読まない（セッションを始めさせない）。 */
static const uint8_t TRACER_AIR_VERIFY_TAG[4] = {'R', 'S', 'T', 'P'};
#define AIR_PHY_RATE WIFI_PHY_RATE_1M_L          /* RADIO_AIR_RATE_KBPS と揃える */
#define RATE_VERIFY_WAIT pdMS_TO_TICKS(500)
#define AIR_PREFIX_LEN (1 + 3 + 4)
#define AIR_SYSID 1
#define AIR_COMPID 1
#define AIR_CONFIG_EVERY_US 1000000LL

/* サンプルの通し番号を数える送信機の数。近くに別の組が居ても自分の組の番号が
 * 途切れないように、送信機ごとに数える。 */
#define SAMPLE_COUNTERS 4

typedef enum { RX_EV_SAMPLE, RX_EV_CONFIG } rx_event_kind_t;

typedef struct {
    rx_event_kind_t kind;
    union {
        struct {
            uint64_t rx_time_us;
            uint32_t seq;
            uint32_t sample_seq;
            uint8_t tx_id[6];
            int16_t rssi;
            int16_t noise_floor;
            uint16_t tx_config_id;
        } sample;
        tracer_config_t config;      /* TX が空中で送ってきた設定（中継する） */
    };
} rx_event_t;

typedef struct {
    bool used;
    uint8_t tx_id[6];
    uint32_t next;
} sample_counter_t;

static tracer_settings_t s_settings;
static tracer_config_t s_config;
static uint8_t s_mac[6];
static QueueHandle_t s_rx_queue;
static uint32_t s_tx_failures;
static uint32_t s_rx_overflows;
static sample_counter_t s_counters[SAMPLE_COUNTERS];
static unsigned s_counter_victim;

uint32_t radio_tx_failures(void) { return s_tx_failures; }
uint32_t radio_rx_overflows(void) { return s_rx_overflows; }

/* --- 空中のレート -------------------------------------------------------------- */

/* 送信完了の通知で届いた、最後の実際のレート（kbps・0＝不明）。Wi-Fi のタスクが書き、
 * TX のタスクが読む。 */
static volatile uint16_t s_last_tx_kbps;
static SemaphoreHandle_t s_tx_done;

static uint16_t phy_rate_kbps(wifi_phy_rate_t rate)
{
    switch (rate) {
    case WIFI_PHY_RATE_1M_L: return 1000;
    case WIFI_PHY_RATE_2M_L: case WIFI_PHY_RATE_2M_S: return 2000;
    case WIFI_PHY_RATE_5M_L: case WIFI_PHY_RATE_5M_S: return 5500;
    case WIFI_PHY_RATE_11M_L: case WIFI_PHY_RATE_11M_S: return 11000;
    default: return 0;     /* 11b 以外。1 Mbps でないことだけ分かればよい */
    }
}

static void tx_done(const esp_80211_tx_info_t *info)
{
    s_last_tx_kbps = phy_rate_kbps(info->rate);
    if (s_tx_done != NULL) xSemaphoreGive(s_tx_done);
}

/* --- TX --------------------------------------------------------------------- */

static void tx_task(void *arg)
{
    (void)arg;
    uint8_t frame[WLAN_HEADER_LEN + AIR_PREFIX_LEN + TRACER_MAX_FRAME_LEN];
    static const uint8_t broadcast[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    memset(frame, 0, WLAN_HEADER_LEN);
    frame[0] = 0xD0;                                 /* 管理フレーム・アクション */
    memcpy(frame + 4, broadcast, 6);                 /* addr1 */
    memcpy(frame + 10, s_mac, 6);                    /* addr2＝送信機 */
    memcpy(frame + 16, s_mac, 6);                    /* addr3 */
    uint8_t *body = frame + WLAN_HEADER_LEN;
    body[0] = ACTION_CATEGORY_VENDOR;
    memcpy(body + 1, TRACER_AIR_OUI, 3);
    memcpy(body + 4, TRACER_AIR_TAG, 4);

    uint32_t seq = 0;
    uint8_t air_link_seq = 0;
    int64_t last_config = -AIR_CONFIG_EVERY_US;
    TickType_t last = xTaskGetTickCount();
    const TickType_t period = pdMS_TO_TICKS(s_settings.tx_interval_ms);
    for (;;) {
        xTaskDelayUntil(&last, period);
        int64_t now = esp_timer_get_time();
        uint16_t actual = s_last_tx_kbps;
        if (actual != s_config.rate_kbps) {
            /* 測定の途中でレートが変わった。送信は止めない（止めると受信の途絶＝打ち切りに
             * 見える）。設定の刻印を事実に直して**すぐ**送る＝PC は設定の変化を見て
             * セッションを閉じる（混ざるのは気づく前に送った 1 パケットだけ）。 */
            s_config.rate_kbps = actual;
            last_config = -AIR_CONFIG_EVERY_US;
        }
        if (now - last_config >= AIR_CONFIG_EVERY_US) {
            /* 送れなかったら次の周期でまた試す（last_config を進めない）。番号付きの
             * パケットとは別の数え方なので、s_tx_failures には入れない。 */
            size_t n = tracer_config_pack(body + AIR_PREFIX_LEN, air_link_seq++, AIR_SYSID,
                                          AIR_COMPID, &s_config);
            if (esp_wifi_80211_tx(WIFI_IF_STA, frame, (int)(WLAN_HEADER_LEN + AIR_PREFIX_LEN + n),
                                  true) == ESP_OK) {
                last_config = now;
            }
        }
        tracer_tx_packet_t p = {
            .seq = seq,
            .tx_time_us = (uint64_t)esp_timer_get_time(),
            .config_id = s_settings.config_id,
        };
        memcpy(p.tx_id, s_mac, 6);
        size_t n = tracer_tx_packet_pack(body + AIR_PREFIX_LEN, air_link_seq++, AIR_SYSID,
                                         AIR_COMPID, &p);
        if (esp_wifi_80211_tx(WIFI_IF_STA, frame, (int)(WLAN_HEADER_LEN + AIR_PREFIX_LEN + n),
                              true) != ESP_OK) {
            /* 送れなかった番号は使わない（進めない）＝受け側が「届かなかった」＝
             * 打ち切りの根拠として数えてしまうため。控えも出さない。 */
            s_tx_failures++;
            continue;
        }
        link_send_tx_packet(&p);
        seq++;
    }
}

/* --- RX --------------------------------------------------------------------- */

/* 送信機ごとのサンプルの通し番号。**受信した時点で振る**＝ここから先（キューの溢れ・
 * USB の取りこぼし・UART の化け）で落ちた分は、PC 側で番号の欠けとして見える。
 * Wi-Fi のタスクからだけ呼ぶ（書き手が 1 つなので排他は要らない）。 */
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

/* Wi-Fi のタスクから呼ばれる＝重い処理をせず、キューへ積んで戻る。 */
static void rx_callback(void *buf, wifi_promiscuous_pkt_type_t type)
{
    if (type != WIFI_PKT_MGMT) return;
    const wifi_promiscuous_pkt_t *pkt = (const wifi_promiscuous_pkt_t *)buf;
    int len = (int)pkt->rx_ctrl.sig_len - FCS_LEN;
    const uint8_t *d = pkt->payload;
    if (len < WLAN_HEADER_LEN + AIR_PREFIX_LEN + TRACER_HEADER_LEN + TRACER_CHECKSUM_LEN) return;
    if (d[0] != 0xD0) return;
    const uint8_t *body = d + WLAN_HEADER_LEN;
    if (body[0] != ACTION_CATEGORY_VENDOR || memcmp(body + 1, TRACER_AIR_OUI, 3) != 0
        || memcmp(body + 4, TRACER_AIR_TAG, 4) != 0) return;

    uint32_t msgid;
    const uint8_t *payload;
    uint8_t payload_len;
    size_t avail = (size_t)(len - WLAN_HEADER_LEN - AIR_PREFIX_LEN);
    if (tracer_frame_parse(body + AIR_PREFIX_LEN, avail, &msgid, &payload, &payload_len) == 0) {
        return;
    }
    rx_event_t ev;
    if (msgid == MSGID_TRACER_TX_PACKET) {
        tracer_tx_packet_t p;
        tracer_tx_packet_decode_payload(payload, payload_len, &p);
        ev.kind = RX_EV_SAMPLE;
        ev.sample.rx_time_us = (uint64_t)esp_timer_get_time();
        ev.sample.seq = p.seq;
        ev.sample.rssi = (int16_t)pkt->rx_ctrl.rssi;
        ev.sample.noise_floor = (int16_t)pkt->rx_ctrl.noise_floor;
        ev.sample.tx_config_id = p.config_id;
        memcpy(ev.sample.tx_id, p.tx_id, 6);
        /* キューへ積む**前に**振る＝溢れた分も番号の欠けとして PC に見える。 */
        ev.sample.sample_seq = next_sample_seq(p.tx_id);
    } else if (msgid == MSGID_TRACER_CONFIG) {
        ev.kind = RX_EV_CONFIG;
        tracer_config_decode_payload(payload, payload_len, &ev.config);
        /* 中継するのは TX の設定だけ。RX の設定を中継すると、PC は「自分の RX の設定が
         * 変わった」と読んでセッションを終えてしまう（RX は空中へ送らないが、念のため）。 */
        if (ev.config.role != TRACER_ROLE_TX) return;
    } else {
        return;
    }
    if (xQueueSend(s_rx_queue, &ev, 0) != pdTRUE) {
        /* 溢れた分は PC から見ると「届かなかった」と同じ形になる。数だけは残す。 */
        s_rx_overflows++;
    }
}

static void rx_writer_task(void *arg)
{
    (void)arg;
    rx_event_t ev;
    for (;;) {
        if (xQueueReceive(s_rx_queue, &ev, portMAX_DELAY) != pdTRUE) continue;
        if (ev.kind == RX_EV_CONFIG) {
            link_send_config(&ev.config);
            continue;
        }
        tracer_rx_sample_t m = {
            .seq = ev.sample.seq,
            .rx_time_us = ev.sample.rx_time_us,
            .rssi_raw = ev.sample.rssi,
            .noise_floor_raw = ev.sample.noise_floor,
            .config_id = s_settings.config_id,     /* RX の設定番号（PC 側が引く番号） */
            .tx_config_id = ev.sample.tx_config_id,
            .sample_seq = ev.sample.sample_seq,
        };
        memcpy(m.rx_id, s_mac, 6);
        memcpy(m.tx_id, ev.sample.tx_id, 6);
        link_send_rx_sample(&m);
    }
}

/* --- 起動 ------------------------------------------------------------------- */

void radio_start(const tracer_settings_t *s, const uint8_t mac[6], int8_t *actual_power_qdbm)
{
    s_settings = *s;
    memcpy(s_mac, mac, 6);

    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    /* 11b だけ＝DSSS。レートを 1 Mbps に固定する前提（打ち切りを減らす）。 */
    ESP_ERROR_CHECK(esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_11B));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    ESP_ERROR_CHECK(esp_wifi_set_channel(s->channel, WIFI_SECOND_CHAN_NONE));
    /* start の後で呼ぶ（前だと ESP_FAIL）。効いたかは radio_verify_rate が確かめる。 */
    wifi_tx_rate_config_t rate = {.phymode = WIFI_PHY_MODE_11B, .rate = AIR_PHY_RATE};
    ESP_ERROR_CHECK(esp_wifi_config_80211_tx(WIFI_IF_STA, &rate));
    s_tx_done = xSemaphoreCreateBinary();
    ESP_ERROR_CHECK(esp_wifi_register_80211_tx_cb(tx_done));
    /* 送信電力は固定する（自動出力制御が動くと RSSI からパスロスを引けない）。
     * 実際に効いた値を読み戻して設定メッセージに載せる＝要求値ではなく事実を刻む。 */
    ESP_ERROR_CHECK(esp_wifi_set_max_tx_power(s->tx_power_qdbm));
    ESP_ERROR_CHECK(esp_wifi_get_max_tx_power(actual_power_qdbm));
}

bool radio_verify_rate(uint16_t *actual_kbps)
{
    uint8_t frame[WLAN_HEADER_LEN + AIR_PREFIX_LEN];
    memset(frame, 0, WLAN_HEADER_LEN);
    frame[0] = 0xD0;
    memset(frame + 4, 0xFF, 6);
    memcpy(frame + 10, s_mac, 6);
    memcpy(frame + 16, s_mac, 6);
    uint8_t *body = frame + WLAN_HEADER_LEN;
    body[0] = ACTION_CATEGORY_VENDOR;
    memcpy(body + 1, TRACER_AIR_OUI, 3);
    memcpy(body + 4, TRACER_AIR_VERIFY_TAG, 4);

    s_last_tx_kbps = 0;
    xSemaphoreTake(s_tx_done, 0);                    /* 前の通知を捨てる */
    if (esp_wifi_80211_tx(WIFI_IF_STA, frame, (int)sizeof frame, true) != ESP_OK
        || xSemaphoreTake(s_tx_done, RATE_VERIFY_WAIT) != pdTRUE) {
        *actual_kbps = 0;                            /* 通知が来ない＝確かめられない */
        return false;
    }
    *actual_kbps = s_last_tx_kbps;
    return s_last_tx_kbps == RADIO_AIR_RATE_KBPS;
}

void radio_run(const tracer_config_t *config)
{
    s_config = *config;
    if (s_settings.role == TRACER_ROLE_TX) {
        xTaskCreate(tx_task, "tracer_tx", 4096, NULL, 5, NULL);
    } else {
        s_rx_queue = xQueueCreate(64, sizeof(rx_event_t));
        wifi_promiscuous_filter_t filter = {.filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT};
        ESP_ERROR_CHECK(esp_wifi_set_promiscuous_filter(&filter));
        ESP_ERROR_CHECK(esp_wifi_set_promiscuous_rx_cb(rx_callback));
        xTaskCreate(rx_writer_task, "tracer_rx", 4096, NULL, 5, NULL);
    }
}
