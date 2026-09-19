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
 * ⚠️ 試作で確かめる前提の点（Claude の知識による・一次資料で未確認）:
 *   - esp_wifi_80211_tx でアクションフレームを送れること・レートを 1 Mbps に固定できること
 *   - プロミスキャス受信の rx_ctrl.rssi / rx_ctrl.noise_floor が ESP32-C6 で取れること
 *   - OUI の値（下の TRACER_AIR_OUI）は仮置き。登録された値ではない
 */
#include "radio.h"

#include <string.h>

#include "esp_event.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "link.h"
#include "tracer_mavlink.h"

#define WLAN_HEADER_LEN 24
#define FCS_LEN 4
#define ACTION_CATEGORY_VENDOR 127
static const uint8_t TRACER_AIR_OUI[3] = {0x02, 0x52, 0x53};   /* 仮置き（未登録） */
static const uint8_t TRACER_AIR_TAG[4] = {'R', 'S', 'T', 'R'};
#define AIR_PREFIX_LEN (1 + 3 + 4)
#define AIR_SYSID 1
#define AIR_COMPID 1

typedef struct {
    uint64_t rx_time_us;
    uint32_t seq;
    uint8_t tx_id[6];
    int16_t rssi;
    int16_t noise_floor;
} rx_event_t;

static tracer_settings_t s_settings;
static uint8_t s_mac[6];
static QueueHandle_t s_rx_queue;
static uint32_t s_tx_failures;
static uint32_t s_rx_overflows;

uint32_t radio_tx_failures(void) { return s_tx_failures; }
uint32_t radio_rx_overflows(void) { return s_rx_overflows; }

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
    TickType_t last = xTaskGetTickCount();
    const TickType_t period = pdMS_TO_TICKS(s_settings.tx_interval_ms);
    for (;;) {
        xTaskDelayUntil(&last, period);
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
    if (tracer_frame_parse(body + AIR_PREFIX_LEN, avail, &msgid, &payload, &payload_len) == 0
        || msgid != MSGID_TRACER_TX_PACKET) return;
    tracer_tx_packet_t p;
    tracer_tx_packet_decode_payload(payload, payload_len, &p);

    rx_event_t ev = {
        .rx_time_us = (uint64_t)esp_timer_get_time(),
        .seq = p.seq,
        .rssi = (int16_t)pkt->rx_ctrl.rssi,
        .noise_floor = (int16_t)pkt->rx_ctrl.noise_floor,
    };
    memcpy(ev.tx_id, p.tx_id, 6);
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
        tracer_rx_sample_t m = {
            .seq = ev.seq,
            .rx_time_us = ev.rx_time_us,
            .rssi_raw = ev.rssi,
            .noise_floor_raw = ev.noise_floor,
            .config_id = s_settings.config_id,     /* RX の設定番号（PC 側が引く番号） */
        };
        memcpy(m.rx_id, s_mac, 6);
        memcpy(m.tx_id, ev.tx_id, 6);
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
    ESP_ERROR_CHECK(esp_wifi_config_80211_tx_rate(WIFI_IF_STA, WIFI_PHY_RATE_1M_L));
    /* 送信電力は固定する（自動出力制御が動くと RSSI からパスロスを引けない）。
     * 実際に効いた値を読み戻して設定メッセージに載せる＝要求値ではなく事実を刻む。 */
    ESP_ERROR_CHECK(esp_wifi_set_max_tx_power(s->tx_power_qdbm));
    ESP_ERROR_CHECK(esp_wifi_get_max_tx_power(actual_power_qdbm));

    if (s->role == TRACER_ROLE_TX) {
        xTaskCreate(tx_task, "tracer_tx", 4096, NULL, 5, NULL);
    } else {
        s_rx_queue = xQueueCreate(64, sizeof(rx_event_t));
        wifi_promiscuous_filter_t filter = {.filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT};
        ESP_ERROR_CHECK(esp_wifi_set_promiscuous_filter(&filter));
        ESP_ERROR_CHECK(esp_wifi_set_promiscuous_rx_cb(rx_callback));
        xTaskCreate(rx_writer_task, "tracer_rx", 4096, NULL, 5, NULL);
    }
}
