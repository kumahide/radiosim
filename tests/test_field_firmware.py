"""
tests/test_field_firmware.py
=============================
実測補助アプリ（`apps/field`）の**ファームと PC 側の境目**を検証する（増分5）。

🔑 **CI には ESP-IDF も C コンパイラも無い。** それでも C 側と PC 側を同じ物差しで
結ぶために、生成した C ヘッダ（`apps/field/firmware/main/radiosim_field_mavlink.h`）に
**見本のフレーム**を埋め込んである。
  - ここ（CI）は、ヘッダに**書かれているバイト列**を PC 側の読み取りで復号し、
    見本の値に戻ることを確かめる
  - ファームは起動時に、自分のエンコーダで同じバイト列を作れるかを確かめ
    （`radiosim_field_selftest`）、作れなければ何も送らない
⇒ 2 つが揃うと「C が組んだフレームを PC が読める」が、C を CI で動かさずに言える。

⚠️ PC 側の読み取りの正しさ（並び・CRC_EXTRA）は `test_field_mavlink.py` が仕様から
手で書き下したフレームで守っている。ここはその読み取りを**物差しとして借りる**側。
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 — git と cmake を固定の引数で呼ぶだけ
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from apps.field import recorder as REC
from apps.field import session as S
from apps.field.mavlink import dialect as D
from apps.field.mavlink import gen_c as G
from apps.field.mavlink import reader as R

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "apps" / "field" / "firmware"
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
        "radiosim_field_mavlink.h が XML と食い違っています: python -m apps.field.mavlink.gen_c"
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
        rf"RADIOSIM_FIELD_GOLDEN_FRAME_{name}\[\d+\] = \{{ ([0-9A-Fx, ]+) \}};", header_text
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
    selftest = body.index("radiosim_field_selftest()")
    for later in ("e220_init(", "prepare_920(", "prepare_2400(", "link_send_config("):
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
        f for f in dialect.messages_by_name["RADIOSIM_FIELD_CONFIG"].fields
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
    assert re.search(r"add_custom_target\(radiosim_field_version ALL\b", main_cmake)
    assert "version.cmake" in main_cmake
    assert "add_dependencies(${COMPONENT_LIB} radiosim_field_version)" in main_cmake
    code = re.sub(r"/\*.*?\*/", "", _main_c(), flags=re.DOTALL)
    assert "esp_app_get_description" not in code
    assert "RADIOSIM_FIELD_FIRMWARE_VERSION" in code


def _code(name: str) -> str:
    """コメントを除いたファームのソース（コメントの中の API 名で通らないように）。"""
    text = (FIRMWARE / "main" / name).read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _function(code: str, signature: str) -> str:
    """signature から最初の行頭の `}` までの本体。"""
    return code.split(signature, 1)[1].split("\n}\n", 1)[0]


def test_the_firmware_verifies_the_e220_before_it_measures():
    """B-257 と同じ形。E220 の設定（SF・BW・チャネル・電力）を書いて読み返し、照らし
    合わせてから測定を始める（食い違えば止まる）。刻印の SF・BW は確かめた値の定数から
    取る（数字を別に書くと食い違える）。"""
    main = _code("main.c")
    prepare = _function(main, "static int16_t prepare_920(")
    assert prepare.index("e220_configure(") < prepare.index("refuse_to_measure(")
    body = main.split("void app_main(void)", 1)[1]
    configure = body.index("prepare_920(")
    assert body.index("radiosim_field_selftest()") < configure < body.index("radio_e220_run(")
    assert configure < body.index("link_send_config(")
    assert re.search(r"\.air_sf = is2400 \? 0 : E220_AIR_SF,", main)
    assert re.search(r"\.air_bw_khz = is2400 \? 0 : E220_AIR_BW_KHZ,", main)


def test_the_2400_band_fixes_the_antenna_and_verifies_the_rate_before_it_measures():
    """I-243（B-257 を戻した）。2.4 GHz はアンテナを外部側へ固定してから Wi-Fi を起こし、
    TX は空中のレートを確かめてから測定を始める（食い違えば止まる）。"""
    main = _code("main.c")
    prepare = _function(main, "static int16_t prepare_2400(")
    order = [prepare.index(s) for s in ("select_external_antenna()", "radio_wifi_start(",
                                         "radio_verify_rate(", "refuse_to_measure(")]
    assert order == sorted(order)
    assert re.search(r"#define RF_SWITCH_SELECT_GPIO GPIO_NUM_14\b", main)
    body = main.split("void app_main(void)", 1)[1]
    assert body.index("prepare_2400(") < body.index("radio_wifi_run(")
    assert re.search(r"\.rate_kbps = is2400 \? RADIO_AIR_RATE_KBPS : 0,", main)


def test_only_one_band_is_started():
    """正典 §4＝2 帯域は同時に動かさない。app_main は band で片方だけ起こす（E220 は
    mode 3 まで＝e220_init だけで、e220_configure は 920 の枝の中）。"""
    body = _code("main.c").split("void app_main(void)", 1)[1].split("int64_t last_config", 1)[0]
    branch = body.split("if (settings.band == RADIOSIM_FIELD_BAND_2400) {", 1)[1]
    wifi, e220 = branch.split("} else {", 1)
    assert "radio_wifi_run(" in wifi and "radio_e220_run(" not in wifi and "prepare_920(" not in wifi
    assert "radio_e220_run(" in e220 and "radio_wifi_run(" not in e220 and "prepare_2400(" not in e220


def test_cont_is_refused_outside_the_2400_band():
    """920 MHz は AUX の Low が空中の時間と同じでない＝デューティを測れない（I-242）。"""
    cont = _function(_code("main.c"), "static bool handle_continuous(")
    assert cont.index("RADIOSIM_FIELD_BAND_2400") < cont.index("radio_continuous(")


def test_the_2400_tx_counts_failed_sends_instead_of_skipping_the_seq():
    """I-165 を 2.4 GHz にも＝送信関数が失敗した周期も seq を進め、sent_mask／unsent_total に
    「送らなかった」と出す（旧版は seq を進めず控えも出さなかった）。"""
    tx = _function(_code("radio_wifi.c"), "static void tx_task(void *arg)")
    assert "continue;" not in tx.split("bool sent = esp_wifi_80211_tx(", 1)[1]
    assert "unsent++" in tx and "sent_bits = (sent_bits << 1)" in tx and "seq++" in tx
    assert ".sent_mask = (uint32_t)sent_bits" in tx


def test_the_tx_reads_the_result_of_the_previous_send_before_it_sends():
    """送らなかった周期を TX が知る口＝送る前に前の送信の結果（0xA1 の bit0〜2）を読み、
    送れなかった seq を `sent_mask` から落とす。前の送信がまだなら、その周期は送らずに
    数える（控えは送らなかった周期も出す）。"""
    radio = _code("radio_e220.c")
    assert re.search(r"#define A1_NOT_SENT 0x07\b", radio)
    ready = radio.split("static bool ready_to_send(void)", 1)[1].split("\n}\n", 1)[0]
    assert "A1_NOT_SENT" in ready and "mark_unsent(" in ready
    tx = radio.split("static void tx_task(void *arg)", 1)[1].split("\n}\n", 1)[0]
    assert tx.index("ready_to_send()") < tx.index("e220_send(")
    assert "s_unsent++" in tx and "link_send_tx_packet(&p)" in tx


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
    return re.search(r'RADIOSIM_FIELD_FIRMWARE_VERSION "([^"]*)"', out.read_text(encoding="utf-8")).group(1)


def test_the_version_script_follows_the_commit_on_every_run(tmp_path):
    """同じ出力先に対して走らせ直すたびに、**その時点の** git の状態を書く（B-256）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "radiosim_field_version.h"
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
    out = tmp_path / "radiosim_field_version.h"
    result = subprocess.run(  # nosec B603 — 手元の cmake でスクリプトを走らせるだけ
        [_cmake(), f"-DSRC_DIR={tmp_path}", f"-DOUT={out}",
         "-P", str(FIRMWARE / "version.cmake")],
        capture_output=True,
    )
    assert result.returncode != 0 and not out.exists()


def _check_sdkconfig(tmp_path: Path, defaults: str, sdkconfig: str) -> subprocess.CompletedProcess:
    (tmp_path / "sdkconfig.defaults").write_bytes(defaults.encode("utf-8"))
    (tmp_path / "sdkconfig").write_bytes(sdkconfig.encode("utf-8"))
    return subprocess.run(  # nosec B603 — 手元の cmake でスクリプトを走らせるだけ
        [_cmake(), f"-DDEFAULTS={tmp_path / 'sdkconfig.defaults'}",
         f"-DSDKCONFIG={tmp_path / 'sdkconfig'}",
         "-P", str(FIRMWARE / "sdkconfig_check.cmake")],
        capture_output=True, encoding="utf-8", errors="replace",
    )


_DEFAULTS = (
    "# 注釈は照合しない\n"
    'CONFIG_IDF_TARGET="esp32c6"\n'
    "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y\n"
    "# CONFIG_FOO is not set\n"
)


def test_the_sdkconfig_check_passes_when_every_default_is_in_effect(tmp_path):
    """既定の各行が sdkconfig にそのまま在れば通る（改行が CRLF でも・既定に無い項目があっても）。"""
    sdkconfig = (
        'CONFIG_IDF_TARGET="esp32c6"\n# CONFIG_ESPTOOLPY_FLASHSIZE_2MB is not set\n'
        "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y\n# CONFIG_FOO is not set\nCONFIG_BAR=3\n"
    )
    result = _check_sdkconfig(tmp_path, _DEFAULTS.replace("\n", "\r\n"), sdkconfig)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("sdkconfig", "shown"), [
    # B-260 の実例＝既定を 4MB に直しても、残っていた sdkconfig は 2MB のまま。
    ('CONFIG_IDF_TARGET="esp32c6"\nCONFIG_ESPTOOLPY_FLASHSIZE_2MB=y\n'
     "# CONFIG_ESPTOOLPY_FLASHSIZE_4MB is not set\n# CONFIG_FOO is not set\n",
     "# CONFIG_ESPTOOLPY_FLASHSIZE_4MB is not set"),
    # 「is not set」の既定が有効になっている。
    ('CONFIG_IDF_TARGET="esp32c6"\nCONFIG_ESPTOOLPY_FLASHSIZE_4MB=y\nCONFIG_FOO=y\n',
     "CONFIG_FOO=y"),
    # 依存が満たされず項目ごと消えている＝既定が効いていない。
    ('CONFIG_IDF_TARGET="esp32c6"\n# CONFIG_FOO is not set\n',
     "sdkconfig に項目が無い"),
])
def test_the_sdkconfig_check_stops_the_build_when_a_default_is_not_in_effect(
    tmp_path, sdkconfig, shown
):
    """既定と食い違う sdkconfig で焼かせない（B-260）＝版はコミットしか表さない。"""
    result = _check_sdkconfig(tmp_path, _DEFAULTS, sdkconfig)
    assert result.returncode != 0
    assert shown in result.stderr
    assert "消してからビルドし直してください" in result.stderr


def test_the_sdkconfig_check_runs_on_every_build_before_the_stamp():
    """照合は版の刻印と同じ、毎回走るターゲットの中で、刻印より先に走る（B-260）。"""
    main_cmake = (FIRMWARE / "main" / "CMakeLists.txt").read_text(encoding="utf-8")
    target = main_cmake.split("add_custom_target(radiosim_field_version ALL", 1)[1]
    assert "sdkconfig_check.cmake" in target
    assert target.index("sdkconfig_check.cmake") < target.index("version.cmake")
    assert re.search(r"-DSDKCONFIG=\$\{RADIOSIM_FIELD_SDKCONFIG\}\s", target)
    assert "idf_build_get_property(RADIOSIM_FIELD_SDKCONFIG SDKCONFIG)" in main_cmake


def test_the_esp_idf_version_is_pinned_in_one_place():
    """ビルドが受け付ける ESP-IDF の版と、README に書いた版が同じ。

    CMake が止めるので版は 1 つに決まる＝ファームのコミットから版を辿れる。README
    だけが古いと、利用者が別の版を入れてビルドが止まる。"""
    cmake = (FIRMWARE / "CMakeLists.txt").read_text(encoding="utf-8")
    pinned = re.search(r'set\(RADIOSIM_FIELD_IDF_VERSION "(\d+\.\d+)"\)', cmake)
    assert pinned, "CMakeLists.txt が ESP-IDF の版を固定していない"
    assert "FATAL_ERROR" in cmake.split("RADIOSIM_FIELD_IDF_VERSION", 2)[2]
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
    """ファームの `make_config` と同じ形（瞬時値・ウィンドウ長 0＝不明・外部アンテナ）。"""
    enums = dialect.enums
    values = dict(
        config_id=3, role=enums["RADIOSIM_FIELD_ROLE"]["RADIOSIM_FIELD_ROLE_RX"], device_id=RX_MAC,
        firmware_version="0123456789-dirty", band=920, channel=6, air_sf=7, air_bw_khz=500, rate_kbps=0,
        tx_power_cdbm=1300, tx_interval_ms=100,
        detector=enums["RADIOSIM_FIELD_DETECTOR"]["RADIOSIM_FIELD_DETECTOR_INSTANT"],
        integration_window_us=0, integration_samples=1,
        antenna=enums["RADIOSIM_FIELD_ANTENNA"]["RADIOSIM_FIELD_ANTENNA_EXTERNAL"],
    )
    values.update(overrides)
    return G.encode_frame(dialect.messages_by_name["RADIOSIM_FIELD_CONFIG"], values, link_seq=0)


def _tx_config(dialect: D.Dialect, **overrides) -> bytes:
    """TX が空中で送り、RX が**そのまま**中継した設定（`radio_e220.c`・`radio_wifi.c` の中継）。"""
    enums = dialect.enums
    values = dict(
        config_id=2, role=enums["RADIOSIM_FIELD_ROLE"]["RADIOSIM_FIELD_ROLE_TX"], device_id=TX_MAC,
        firmware_version="0123456789", band=920, channel=6, air_sf=7, air_bw_khz=500, rate_kbps=0,
        tx_power_cdbm=1300, tx_interval_ms=100,
        detector=enums["RADIOSIM_FIELD_DETECTOR"]["RADIOSIM_FIELD_DETECTOR_INSTANT"],
        integration_window_us=0, integration_samples=1,
        antenna=enums["RADIOSIM_FIELD_ANTENNA"]["RADIOSIM_FIELD_ANTENNA_EXTERNAL"],
    )
    values.update(overrides)
    return G.encode_frame(dialect.messages_by_name["RADIOSIM_FIELD_CONFIG"], values, link_seq=0)


def _rx_sample(dialect: D.Dialect, seq: int) -> bytes:
    values = dict(
        rx_id=RX_MAC, tx_id=TX_MAC, seq=seq, rx_time_us=5_000_000 + seq * 100_000,
        rssi_raw=-60 - seq % 7, noise_floor_raw=-95, config_id=3,
        tx_config_id=2, sample_seq=seq, sent_mask=0xFFFFFFFF, unsent_total=0,
    )
    return G.encode_frame(dialect.messages_by_name["RADIOSIM_FIELD_RX_SAMPLE"], values, link_seq=seq)


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
    # 中継される TX の設定（13 dBm・チャネル 6）で測った SMA 端の出力。
    template["tx"]["calibration"].update(
        tx_output_dbm=12.2, tx_output_power_cdbm=1300, tx_output_channel=6
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
    """B-257 の名残。設定番号が同じでも、中継された TX の設定の空中の条件（`air_sf`）が
    変われば閉じる。PC がそれを再送として読み飛ばすと、違う条件のサンプルが同じ条件と
    して記録に混ざる。"""
    rec = REC.Recorder(tmp_path, _template(), software_commit="abc123")
    rec.feed(_rx_config(dialect) + _tx_config(dialect) + _rx_sample(dialect, 0), _t(0))
    rec.feed(_tx_config(dialect) + _rx_sample(dialect, 1), _t(1))
    assert not rec.config_changed
    rec.feed(_tx_config(dialect, air_sf=9) + _rx_sample(dialect, 2), _t(2))
    assert rec.config_changed and rec.state == "stopped"
