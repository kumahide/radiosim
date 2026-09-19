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
import shutil
import subprocess  # nosec B404 — git と cmake を固定の引数で呼ぶだけ
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
    cmake = (FIRMWARE / "version.cmake").read_text(encoding="utf-8")
    found = re.search(r"--short=(\d+)", cmake)
    assert found, "version.cmake が git のコミットを取っていない"
    digits = int(found.group(1))
    assert digits + len("-dirty") <= field.count


def test_the_firmware_version_is_taken_at_build_time_not_configure_time():
    """B-256。構成時に取ると、既存の build フォルダへ `idf.py build` したときに
    **中身は新しいコミット・名乗りは古いコミット**のファームが焼ける。

    - 構成時の CMakeLists.txt は git を呼ばない
    - 版は毎回走るターゲット（出力の無い add_custom_target … ALL）が作り、main が
      それに依存する
    - ファームは構成時の値（アプリ記述子の版）を刻印に使わない
    """
    top = (FIRMWARE / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "git" not in re.sub(r"#.*", "", top), "構成時に git を呼んでいる"
    main_cmake = (FIRMWARE / "main" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert re.search(r"add_custom_target\(tracer_version ALL\b", main_cmake)
    assert "version.cmake" in main_cmake
    assert "add_dependencies(${COMPONENT_LIB} tracer_version)" in main_cmake
    code = re.sub(r"/\*.*?\*/", "", _main_c(), flags=re.DOTALL)
    assert "esp_app_get_description" not in code
    assert "TRACER_FIRMWARE_VERSION" in code


def _code(name: str) -> str:
    """コメントを除いたファームのソース（コメントの中の API 名で通らないように）。"""
    text = (FIRMWARE / "main" / name).read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def test_the_firmware_fixes_the_air_rate_with_the_api_that_takes_effect():
    """B-257。`esp_wifi_config_80211_tx_rate` は start の後だと ESP_OK を返して効かず
    （11 Mbps を指定しても 1 Mbps で出た）、前だと ESP_FAIL（ESP-IDF v6.1・実機）。
    効くのは `esp_wifi_config_80211_tx`。刻印の値（kbps）と PHY のレートも揃える。"""
    radio = _code("radio.c")
    assert "esp_wifi_config_80211_tx_rate" not in radio
    assert re.search(r"esp_wifi_config_80211_tx\(WIFI_IF_STA,", radio)
    assert re.search(r"#define AIR_PHY_RATE WIFI_PHY_RATE_1M_L\b", radio)
    assert re.search(r"#define RADIO_AIR_RATE_KBPS 1000\b", _code("radio.h"))
    assert "esp_wifi_register_80211_tx_cb(" in radio


def test_the_tx_verifies_the_air_rate_before_it_measures():
    """B-257。TX は実際に出たレートを確かめてから測定を始める（違えば止まる）。
    設定の刻印は確かめた値の定数から取る（数字を別に書くと食い違える）。"""
    body = _code("main.c").split("void app_main(void)", 1)[1]
    verify = body.index("radio_verify_rate(")
    assert body.index("radio_start(") < verify < body.index("radio_run(")
    assert verify < body.index("link_send_config(")
    assert re.search(r"\.rate_kbps = RADIO_AIR_RATE_KBPS,", _code("main.c"))


def test_the_tx_restamps_the_rate_when_it_changes_mid_measurement():
    """B-257。測定中にレートが変わっても**送信は止めない**（止めると受信の途絶＝
    打ち切りに見える）。刻印を事実に直し、次の周期ですぐ設定を送る。"""
    tx = _code("radio.c").split("static void tx_task(void *arg)", 1)[1].split("\n}\n", 1)[0]
    # 関数の先頭にも同じ代入（初期値）があるので、直した直後の行として探す
    assert re.search(
        r"s_config\.rate_kbps = actual;\s*last_config = -AIR_CONFIG_EVERY_US;", tx
    )


def _cmake() -> str:
    found = shutil.which("cmake")
    if found is None:
        pytest.skip("cmake がありません（CI の ubuntu にはある）")
    return found


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # nosec B603,B607 — 固定の git 呼び出し（一時リポジトリ）
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=repo, check=True, capture_output=True,
    )


def _stamp(repo: Path, out: Path) -> str:
    subprocess.run(  # nosec B603 — 手元の cmake でスクリプトを走らせるだけ
        [_cmake(), f"-DSRC_DIR={repo}", f"-DOUT={out}",
         "-P", str(FIRMWARE / "version.cmake")],
        check=True, capture_output=True,
    )
    return re.search(r'TRACER_FIRMWARE_VERSION "([^"]*)"', out.read_text(encoding="utf-8")).group(1)


def test_the_version_script_follows_the_commit_on_every_run(tmp_path):
    """同じ出力先に対して走らせ直すたびに、**その時点の** git の状態を書く（B-256）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "tracer_version.h"
    _git(repo, "init", "-q")
    (repo / "a.c").write_text("1\n")
    _git(repo, "add", "a.c")
    _git(repo, "commit", "-q", "-m", "1")
    first = _stamp(repo, out)
    assert re.fullmatch(r"[0-9a-f]{10}", first)

    (repo / "a.c").write_text("2\n")               # 追跡中のファイルを汚す
    assert _stamp(repo, out) == f"{first}-dirty"

    _git(repo, "commit", "-q", "-am", "2")          # コミットを進める
    second = _stamp(repo, out)
    assert re.fullmatch(r"[0-9a-f]{10}", second) and second != first

    before = out.stat().st_mtime_ns
    assert _stamp(repo, out) == second
    assert out.stat().st_mtime_ns == before, "版が同じなのにヘッダを書き直した（毎回再コンパイルになる）"


def test_the_version_script_refuses_to_stamp_without_git(tmp_path):
    """版の分からないファームは刻印できない＝ビルドを止める。"""
    out = tmp_path / "tracer_version.h"
    result = subprocess.run(  # nosec B603 — 手元の cmake でスクリプトを走らせるだけ
        [_cmake(), f"-DSRC_DIR={tmp_path}", f"-DOUT={out}",
         "-P", str(FIRMWARE / "version.cmake")],
        capture_output=True,
    )
    assert result.returncode != 0 and not out.exists()


def test_the_esp_idf_version_is_pinned_in_one_place():
    """ビルドが受け付ける ESP-IDF の版と、README に書いた版が同じ。

    CMake が止めるので版は 1 つに決まる＝ファームのコミットから版を辿れる。README
    だけが古いと、利用者が別の版を入れてビルドが止まる。"""
    cmake = (FIRMWARE / "CMakeLists.txt").read_text(encoding="utf-8")
    pinned = re.search(r'set\(TRACER_IDF_VERSION "(\d+\.\d+)"\)', cmake)
    assert pinned, "CMakeLists.txt が ESP-IDF の版を固定していない"
    assert "FATAL_ERROR" in cmake.split("TRACER_IDF_VERSION", 2)[2]
    readme = (FIRMWARE / "README.md").read_text(encoding="utf-8")
    assert f"ESP-IDF **v{pinned.group(1)}**" in readme


def test_the_usb_port_carries_only_the_data():
    """ログは USB に出さない＝ログの行がフレームの途中に割り込むと CRC で弾かれ、
    電波で届かなかった分と区別の付かない欠けになる。"""
    defaults = (FIRMWARE / "sdkconfig.defaults").read_text(encoding="utf-8")
    assert "CONFIG_ESP_CONSOLE_SECONDARY_NONE=y" in defaults
    assert "CONFIG_ESP_CONSOLE_UART_DEFAULT=y" in defaults
    # 起動の早い段階のログはコンソールの設定を通らず USB にも出る。フラッシュの
    # 容量が実機（4 MB）と食い違うと、その警告が起動のたびに USB へ流れる。
    assert "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y" in defaults


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


def _tx_config(dialect: D.Dialect, **overrides) -> bytes:
    """TX が空中で送り、RX が**そのまま**中継した設定（`radio.c` の中継）。"""
    enums = dialect.enums
    values = dict(
        config_id=2, role=enums["TRACER_ROLE"]["TRACER_ROLE_TX"], device_id=TX_MAC,
        firmware_version="0123456789", channel=6, rate_kbps=1000,
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
        tx_config_id=2, sample_seq=seq,
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
        end["calibration"] = dict(
            measured_on="2026-09-19", offset_db=-96.0, scale_db_per_count=1.0,
            reference_unit_id="ref-01", reference_measured_on="2026-09-10", note="",
        )
        end["position"].update(
            lat=35.0, lon=139.0, elevation_m=120.0, height_agl_m=10.0, height_source="survey"
        )
    # 中継される TX の設定（15 dBm・チャネル 6）で測った SMA 端の出力。
    template["tx"]["calibration"].update(
        tx_output_dbm=14.2, tx_output_power_cdbm=1500, tx_output_channel=6
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
        if seq % 10 == 3:
            # TX の設定は RX の中継で届く（RX 自身の設定とは別の周期）
            chunk = _tx_config(dialect) + chunk
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
    rec.feed(_rx_config(dialect) + _tx_config(dialect) + _rx_sample(dialect, 0), _t(0))
    rec.feed(_rx_config(dialect) + _rx_sample(dialect, 1), _t(1))
    rec.feed(_rx_config(dialect, config_id=4, channel=11), _t(2))
    assert rec.config_changed and rec.state == "stopped"


def test_a_changed_air_rate_in_the_relayed_tx_config_ends_the_session(tmp_path, dialect):
    """B-257。TX は測定中にレートが変わると、設定番号はそのままで `rate_kbps` だけを
    事実に直して送る。PC がそれを再送として読み飛ばすと、違うレートのサンプルが
    同じ条件として記録に混ざる。"""
    rec = REC.Recorder(tmp_path, _template(), software_commit="abc123")
    rec.feed(_rx_config(dialect) + _tx_config(dialect) + _rx_sample(dialect, 0), _t(0))
    rec.feed(_tx_config(dialect) + _rx_sample(dialect, 1), _t(1))
    assert not rec.config_changed
    rec.feed(_tx_config(dialect, rate_kbps=11000) + _rx_sample(dialect, 2), _t(2))
    assert rec.config_changed and rec.state == "stopped"
