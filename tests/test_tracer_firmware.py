"""
tests/test_tracer_firmware.py
=============================
実測補助アプリ（`apps/tracer`）の**ファームと PC 側の境目**を検証する（増分5）。

🔑 **CI には ESP-IDF も C コンパイラも無い。** それでも C 側と PC 側を同じ物差しで
結ぶために、生成した C ヘッダ（`apps/tracer/firmware/main/tracer_mavlink.h`）に
**見本のフレーム**を埋め込んである。
  - ここ（CI）は、ヘッダに**書かれているバイト列**を PC 側の読み取りで復号し、
    見本の値に戻ることを確かめる
  - ファームは起動時に、自分のエンコーダで同じバイト列を作れるかを確かめ
    （`tracer_selftest`）、作れなければ何も送らない
⇒ 2 つが揃うと「C が組んだフレームを PC が読める」が、C を CI で動かさずに言える。

⚠️ PC 側の読み取りの正しさ（並び・CRC_EXTRA）は `test_tracer_mavlink.py` が仕様から
手で書き下したフレームで守っている。ここはその読み取りを**物差しとして借りる**側。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from apps.tracer import recorder as REC
from apps.tracer import session as S
from apps.tracer.mavlink import dialect as D
from apps.tracer.mavlink import gen_c as G
from apps.tracer.mavlink import reader as R

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "apps" / "tracer" / "firmware"
RX_MAC = (0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x02)
TX_MAC = (0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0x01)
_BASE = datetime(2026, 9, 19, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def dialect() -> D.Dialect:
    return D.load_dialect()


@pytest.fixture(scope="module")
def header_text() -> str:
    return G.HEADER_PATH.read_text(encoding="utf-8")


# --- 生成物 ------------------------------------------------------------------


def test_the_generated_header_is_up_to_date(header_text):
    """XML を直したのにヘッダを作り直していない＝ファームが古い定義でフレームを組む
    （PC 側は CRC_EXTRA の食い違いで全フレームを捨て、全部が「届かなかった」になる）。"""
    assert header_text == G.render(), (
        "tracer_mavlink.h が XML と食い違っています: python -m apps.tracer.mavlink.gen_c"
    )


def _defines(header_text: str) -> dict[str, int]:
    return {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"^#define (\w+) (\d+)$", header_text, re.MULTILINE)
    }


def test_the_header_carries_the_same_ids_lengths_and_crc_extra(dialect, header_text):
    """ヘッダの定数が PC 側の定義と同じ（取り違えた変数を書き出していない）。"""
    defines = _defines(header_text)
    for message in dialect.messages_by_id.values():
        assert defines[f"MSGID_{message.name}"] == message.msgid
        assert defines[f"LEN_{message.name}"] == message.payload_size
        assert defines[f"CRC_EXTRA_{message.name}"] == message.crc_extra


def _golden_frame(header_text: str, name: str) -> bytes:
    match = re.search(
        rf"TRACER_GOLDEN_FRAME_{name}\[\d+\] = \{{ ([0-9A-Fx, ]+) \}};", header_text
    )
    assert match, f"{name} の見本がヘッダにありません"
    return bytes(int(b, 16) for b in match.group(1).split(", "))


def test_the_golden_frames_in_the_header_decode_to_their_values(dialect, header_text):
    """**ヘッダに書かれたバイト列**を PC 側で読むと、見本の値に戻る。

    ファームはこのバイト列を自分で作れることを起動時に確かめる＝ここが通れば、
    C が組んだフレームを PC 側が同じ値として読む。"""
    for message in dialect.messages_by_id.values():
        stats = R.ReadStats()
        got = list(R.iter_messages(_golden_frame(header_text, message.name), stats, dialect))
        assert stats.crc_errors == 0 and stats.skipped_bytes == 0 and stats.truncated_tail == 0
        assert [m.name for m in got] == [message.name]
        assert got[0].fields == G.golden_values(message)


def test_the_golden_values_would_expose_a_wrong_field_order(dialect, header_text):
    """見本が「並びを取り違えても一致してしまう値」になっていない。

    - 末尾がゼロ＝v2 の切り詰めが必ず起きる（埋め戻しを外した C を見逃さない）
    - ゼロ以外の値は互いに違う＝隣のフィールドと入れ替わっても一致しない
    - 符号付きは負＝符号の取り違えを見逃さない
    """
    for message in dialect.messages_by_id.values():
        frame = _golden_frame(header_text, message.name)
        assert frame[1] < message.payload_size, f"{message.name}: 切り詰めが起きていない"
        values = G.golden_values(message)
        scalars = []
        for field in message.fields:
            value = values[field.name]
            items = value if isinstance(value, tuple) else (value,)
            if field.type_name.startswith("int"):
                assert all(v < 0 for v in items if v != 0), f"{field.name} が負でない"
            scalars += [v for v in items if v not in (0, "")]
        assert len(scalars) == len(set(scalars)), f"{message.name}: 見本に同じ値がある"


# --- ファームの約束（実行時の制約をテストで表す） --------------------------------


def _main_c() -> str:
    return (FIRMWARE / "main" / "main.c").read_text(encoding="utf-8")


def test_the_firmware_checks_itself_before_it_sends_anything():
    """自己検査より前に無線や設定の送信を始めない（食い違ったフレームを 1 つも出さない）。"""
    body = _main_c().split("void app_main(void)", 1)[1]
    selftest = body.index("tracer_selftest()")
    for later in ("radio_start(", "link_send_config("):
        assert selftest < body.index(later), f"{later} が自己検査より前にある"


def test_the_firmware_resends_its_config_every_second():
    """§6.6-①。起動時だけだと、RX が先に起動していたら記録が始まらない。"""
    text = _main_c()
    assert re.search(r"#define CONFIG_RESEND_MS 1000\b", text)
    loop = text.split("for (;;) {")[-1]
    assert "CONFIG_RESEND_MS" in loop and "link_send_config(&config)" in loop


def test_the_firmware_version_fits_the_config_field(dialect):
    """版（10 桁＋-dirty）が char[16] に収まる＝刻印が途中で切れない。"""
    field = next(
        f for f in dialect.messages_by_name["TRACER_CONFIG"].fields
        if f.name == "firmware_version"
    )
    cmake = (FIRMWARE / "CMakeLists.txt").read_text(encoding="utf-8")
    found = re.search(r"--short=(\d+)", cmake)
    assert found, "CMakeLists.txt が git のコミットを取っていない"
    digits = int(found.group(1))
    assert digits + len("-dirty") <= field.count


def test_the_usb_port_carries_only_the_data():
    """ログは USB に出さない＝ログの行がフレームの途中に割り込むと CRC で弾かれ、
    電波で届かなかった分と区別の付かない欠けになる。"""
    defaults = (FIRMWARE / "sdkconfig.defaults").read_text(encoding="utf-8")
    assert "CONFIG_ESP_CONSOLE_SECONDARY_NONE=y" in defaults
    assert "CONFIG_ESP_CONSOLE_UART_DEFAULT=y" in defaults


# --- ファームの流し方のまま、PC 側が記録できること ------------------------------


def _t(tenths: float) -> str:
    return S.format_utc(_BASE + timedelta(seconds=tenths * 0.1))


def _rx_config(dialect: D.Dialect, **overrides) -> bytes:
    """ファームの `make_config` と同じ形（瞬時値・窓長 0＝不明・外部アンテナ）。"""
    enums = dialect.enums
    values = dict(
        config_id=3, role=enums["TRACER_ROLE"]["TRACER_ROLE_RX"], device_id=RX_MAC,
        firmware_version="0123456789-dirty", channel=6, rate_kbps=1000,
        tx_power_cdbm=1500, tx_interval_ms=100,
        detector=enums["TRACER_DETECTOR"]["TRACER_DETECTOR_INSTANT"],
        integration_window_us=0, integration_samples=1,
        antenna=enums["TRACER_ANTENNA"]["TRACER_ANTENNA_EXTERNAL"],
    )
    values.update(overrides)
    return G.encode_frame(dialect.messages_by_name["TRACER_CONFIG"], values, link_seq=0)


def _rx_sample(dialect: D.Dialect, seq: int) -> bytes:
    values = dict(
        rx_id=RX_MAC, tx_id=TX_MAC, seq=seq, rx_time_us=5_000_000 + seq * 100_000,
        rssi_raw=-60 - seq % 7, noise_floor_raw=-95, config_id=3,
    )
    return G.encode_frame(dialect.messages_by_name["TRACER_RX_SAMPLE"], values, link_seq=seq)


def _template() -> dict:
    template = REC.header_template()
    template.update(target_kind="existing_link", env_class="rural", meas_method="esp32c6-2g4")
    template["provenance"].update(
        geoid_model="GSIGEO2011", censor_min_receive_rate=0.9, censor_window_s=1.0
    )
    for role, mac in (("tx", "AA:BB:CC:DD:EE:01"), ("rx", "aa:bb:cc:dd:ee:02")):
        end = template[role]
        end.update(
            measurement_config_id=f"{role}-esp32c6-rod", device_id=mac,
            feeder_loss_db=1.0, antenna_gain_dbi=2.0,
        )
        end["calibration"].update(
            measured_on="2026-09-19", offset_db=-96.0, scale_db_per_count=1.0,
            reference_unit_id="ref-01", reference_measured_on="2026-09-10",
        )
        end["position"].update(
            lat=35.0, lon=139.0, elevation_m=120.0, height_agl_m=10.0, height_source="survey"
        )
    template["tx"]["radio"] = dict(
        config_id=2, firmware_version="0123456789", channel=6, rate_kbps=1000,
        tx_power_cdbm=1500, tx_interval_ms=100, detector="instant",
        integration_window_us=0, integration_samples=1, antenna="external",
        sensitivity_dbm=-98.0,
    )
    template["rx"]["radio"] = {"sensitivity_dbm": -98.0}
    return template


def test_a_recording_joins_a_running_rx_and_survives_the_resends(tmp_path, dialect):
    """**RX が先に動いていた**（最初に届くのはサンプル）ところへ繋いでも記録が始まり、
    1 秒ごとの設定の再送がサンプルと同じ読み出しに混ざっても止まらない。
    読み直しもライブと同じサンプルを作る。"""
    rec = REC.Recorder(tmp_path, _template(), software_commit="abc123")
    rec.feed(_rx_sample(dialect, 0) + _rx_sample(dialect, 1), _t(1))   # 設定より前
    assert rec.state == "waiting"
    for seq in range(2, 40):
        chunk = _rx_sample(dialect, seq)
        if seq % 10 == 5:
            chunk = _rx_config(dialect) + chunk if seq % 20 == 5 else chunk + _rx_config(dialect)
        rec.feed(chunk, _t(seq))
    rec.stop(_t(40))

    assert not rec.config_changed
    assert rec.header is not None and rec.header.rx.radio.integration_window_us == 0
    assert rec.stats.crc_errors == 0
    assert rec.directory is not None
    live = list(S.read_rx_samples(rec.directory))
    # 最初の設定は seq 5 と同じ読み出しの先頭で届く＝セッションはそこから（それより前は
    # 生ログにだけ残る）。
    assert [s.seq for s in live] == list(range(5, 40))
    assert REC.replay(rec.directory).samples == live


def test_a_changed_config_from_the_firmware_still_ends_the_session(tmp_path, dialect):
    """再送を読み飛ばす仕組みが、本当に変わった設定（コマンドで再起動した）まで
    読み飛ばしていない。"""
    rec = REC.Recorder(tmp_path, _template(), software_commit="abc123")
    rec.feed(_rx_config(dialect) + _rx_sample(dialect, 0), _t(0))
    rec.feed(_rx_config(dialect) + _rx_sample(dialect, 1), _t(1))
    rec.feed(_rx_config(dialect, config_id=4, channel=11), _t(2))
    assert rec.config_changed and rec.state == "stopped"
