"""
tests/test_tracer_recorder.py
=============================
実測補助アプリ（`apps/tracer`）の**測りながら書く部分**と CLI を検証する（増分4）。

🔑 **ここで守っているのは「現場で取り直しの効かない記録」の約束**
  - UART の読み出しの区切りに跨ったフレームが落ちないこと（落ちると、電波と無関係な
    欠けが打ち切りに混ざる）
  - 生のバイト列から**同じ結果を作り直せる**こと（復号の不具合が後で見つかっても、
    測定を取り直さずに済む）
  - 雛形の未記入・別個体の校正で記録を始めないこと

⚠️ シリアルポートとキーボードはここでは触らない（`Recorder` はそれを知らない）。
フレームの並び・CRC_EXTRA の正しさは `test_tracer_mavlink.py` が仕様から手で
書き下して守っているので、ここではダイアレクトの値を借りて組み立てる。
"""

from __future__ import annotations

import csv
import json
import random
import struct
from datetime import datetime, timedelta, timezone

import pytest

from apps.tracer import cli
from apps.tracer import recorder as REC
from apps.tracer import session as S
from apps.tracer.mavlink import dialect as D
from apps.tracer.mavlink import reader as R

RX_MAC = b"\xaa\xbb\xcc\xdd\xee\x02"
TX_MAC = b"\xaa\xbb\xcc\xdd\xee\x01"
_BASE = datetime(2026, 9, 19, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def dialect() -> D.Dialect:
    return D.load_dialect()


# --- フレーム -----------------------------------------------------------------


def _frame(dialect: D.Dialect, name: str, payload: bytes, link_seq: int = 0) -> bytes:
    definition = dialect.messages_by_name[name]
    body = (
        bytes([len(payload), 0, 0, link_seq & 0xFF, 1, 1])
        + definition.msgid.to_bytes(3, "little")
        + payload
    )
    crc = D.x25_crc(body)
    crc = D.x25_crc(bytes([definition.crc_extra]), crc)
    return bytes([0xFD]) + body + crc.to_bytes(2, "little")


def _sample_frame(
    dialect: D.Dialect, seq: int, rssi_raw: int = -140, tx: bytes = TX_MAC, config_id: int = 1,
    tx_config_id: int = 2, sample_seq: int | None = None,
) -> bytes:
    payload = (
        struct.pack(
            "<QIIhhHH", 1_000_000 + seq * 100_000, seq,
            seq if sample_seq is None else sample_seq, rssi_raw, -190, config_id, tx_config_id,
        )
        + RX_MAC
        + tx
    )
    return _frame(dialect, "TRACER_RX_SAMPLE", payload, seq)


def _config_frame(
    dialect: D.Dialect, *, role: int = 1, config_id: int = 1, channel: int = 6,
    device: bytes = RX_MAC, interval_ms: int = 100,
) -> bytes:
    payload = (
        struct.pack("<IHHhHH", 1000, config_id, 1000, 1300, interval_ms, 10)
        + bytes([role])
        + device
        + b"5aab362".ljust(16, b"\x00")
        + bytes([channel, 2, 2])
    )
    return _frame(dialect, "TRACER_CONFIG", payload)


def _tx_config_frame(dialect: D.Dialect, **kwargs) -> bytes:
    """TX が空中で送り、RX が中継してきた設定（role が tx）。"""
    values = dict(role=0, config_id=2, device=TX_MAC)
    values.update(kwargs)
    return _config_frame(dialect, **values)


def _configs(dialect: D.Dialect) -> bytes:
    """セッションを始めるのに要る 2 つ（RX 自身の設定と、中継された TX の設定）。"""
    return _config_frame(dialect) + _tx_config_frame(dialect)


def _t(seq_time: float) -> str:
    return S.format_utc(_BASE + timedelta(seconds=seq_time * 0.1))


# --- 雛形 --------------------------------------------------------------------


def _filled_template() -> dict:
    template = REC.header_template()
    template.update(target_kind="existing_link", env_class="rural", meas_method="esp32c6-2g4")
    template["provenance"].update(
        geoid_model="GSIGEO2011", censor_min_receive_rate=0.9, censor_window_s=1.0
    )
    for role, mac, height in (("tx", "AA:BB:CC:DD:EE:01", 45.0), ("rx", "aa:bb:cc:dd:ee:02", 20.0)):
        end = template[role]
        end.update(
            measurement_config_id=f"{role}-esp32c6-rod-3m", device_id=mac,
            feeder_loss_db=1.0, antenna_gain_dbi=2.0,
        )
        # 手で書いた校正値（校正ファイルを使わない経路）。
        end["calibration"] = dict(
            measured_on="2026-09-19", offset_db=-96.0, scale_db_per_count=0.5,
            reference_unit_id="ref-01", reference_measured_on="2026-09-10", note="",
        )
        end["position"].update(
            lat=35.0, lon=139.0, elevation_m=120.0, height_agl_m=height, height_source="survey"
        )
    # 中継される TX の設定（13 dBm・チャネル 6）で測った SMA 端の出力。
    template["tx"]["calibration"].update(
        tx_output_dbm=12.4, tx_output_power_cdbm=1300, tx_output_channel=6
    )
    template["rx"]["radio"] = {"sensitivity_dbm": -98.0}
    return template


def test_an_unfilled_template_does_not_start_a_recording():
    """未記入（`null`）の欄を**名前で**挙げて止めること（現場で気づくと測れない）。"""
    with pytest.raises(S.SessionError, match="未記入") as info:
        REC.check_template(REC.header_template())
    assert "rx.radio.sensitivity_dbm" in str(info.value)
    REC.check_template(_filled_template())


@pytest.mark.parametrize("path", [("session_id",), ("provenance", "software_commit")])
def test_the_template_cannot_carry_what_the_recorder_fills(path):
    """記録する側が埋める項目を雛形に書かせないこと（古いコミットが紛れ込む）。"""
    template = _filled_template()
    node = template
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = "x"
    with pytest.raises(S.SessionError, match="記録する側が埋める"):
        REC.check_template(template)


def test_the_rx_radio_in_the_template_is_only_the_sensitivity():
    """RX の無線設定は機器から取る＝雛形に書いた値で上書きさせないこと。"""
    template = _filled_template()
    template["rx"]["radio"]["channel"] = 11
    with pytest.raises(S.SessionError, match="sensitivity_dbm だけ"):
        REC.check_template(template)


def test_the_template_cannot_carry_the_tx_radio():
    """🔑 TX の無線設定を雛形に書かせないこと（§6.6 の提案②）。

    書けると、実機と食い違った送信間隔が黙ってヘッダに入り、窓の期待パケット数が
    別の値になる（送信間隔が実は 200 ms なら、全窓が受信率 50%＝打ち切りに見える）。
    """
    template = _filled_template()
    template["tx"]["radio"] = {"tx_interval_ms": 100}
    with pytest.raises(S.SessionError, match="tx.radio"):
        REC.check_template(template)


def _message(dialect, frame: bytes) -> R.Message:
    return R.read_log(frame, dialect)[0][0]


def test_the_header_takes_both_radios_from_the_devices(dialect):
    header = REC.build_header(
        _filled_template(),
        _message(dialect, _config_frame(dialect, channel=11)),
        _message(dialect, _tx_config_frame(dialect, interval_ms=200)),
        started_utc=_t(0), software_commit="abc123", dialect=dialect,
    )
    assert header.rx.radio.channel == 11                # 機器の値
    assert header.rx.radio.sensitivity_dbm == -98.0     # 雛形の値
    assert header.tx.radio.tx_interval_ms == 200        # 中継された TX の値
    assert header.tx.radio.config_id == 2
    assert header.tx.radio.sensitivity_dbm is None      # TX は受けない
    assert header.tx.device_id == "aa:bb:cc:dd:ee:01"   # 小文字へ揃える
    assert header.provenance.software_commit == "abc123"
    assert header.session_id == "20260919T010000Z"


def test_another_rx_unit_is_refused(dialect):
    """🔑 雛形と違う個体を挿したまま測らないこと＝**別の個体の校正**が掛かった dBm が出る。"""
    rx = _message(dialect, _config_frame(dialect, device=b"\xaa\xbb\xcc\xdd\xee\x77"))
    with pytest.raises(S.SessionError, match="校正値が別の個体"):
        REC.build_header(
            _filled_template(), rx, _message(dialect, _tx_config_frame(dialect)),
            started_utc=_t(0), software_commit="abc", dialect=dialect,
        )


def test_another_tx_unit_is_refused(dialect):
    tx = _message(dialect, _tx_config_frame(dialect, device=b"\xaa\xbb\xcc\xdd\xee\x77"))
    with pytest.raises(S.SessionError, match="TX の個体 ID"):
        REC.build_header(
            _filled_template(), _message(dialect, _config_frame(dialect)), tx,
            started_utc=_t(0), software_commit="abc", dialect=dialect,
        )


# --- 細切れに届くバイト列 -----------------------------------------------------


def _messy_log(dialect: D.Dialect) -> bytes:
    """雑音・化けたフレーム・知らない ID を混ぜたログ。"""
    unknown = bytes([0xFD, 3, 0, 0, 0, 1, 1]) + (999).to_bytes(3, "little") + b"abc" + b"\x00\x00"
    broken = bytearray(_sample_frame(dialect, 5))
    broken[12] ^= 0xFF
    return (
        b"\x00\x13garbage"
        + _configs(dialect)
        + b"".join(_sample_frame(dialect, q) for q in range(1, 5))
        + bytes(broken)
        + unknown
        + b"".join(_sample_frame(dialect, q) for q in range(6, 12))
        + _sample_frame(dialect, 12)[:9]            # 末尾が切れている
    )


def _fields(messages):
    return [(m.name, m.fields) for m in messages]


def test_a_frame_split_across_reads_is_not_lost(dialect):
    """読み出しの区切りに跨ったフレームを持ち越して読むこと。

    ⚠️ 持ち越しを捨てると、跨ったフレーム**だけ**が落ち、落ちる数は読み出しの間隔で
    変わる＝電波と無関係な欠けが打ち切りに混ざる。
    """
    frame = _sample_frame(dialect, 7)
    stream = R.FrameStream(dialect=dialect)
    assert stream.feed(frame[:5]) == []
    got = stream.feed(frame[5:])
    assert [m.fields["seq"] for m in got] == [7]
    stream.close()
    assert stream.stats.truncated_tail == 0


def test_any_split_gives_the_same_messages_and_counts(dialect):
    """どう区切って渡しても、1 回で渡したときと**同じメッセージ・同じエラー数**になること。"""
    log = _messy_log(dialect)
    whole, whole_stats = R.read_log(log, dialect)
    assert whole_stats.crc_errors >= 1 and whole_stats.unknown_msgid == 1
    assert whole_stats.truncated_tail == 9

    rng = random.Random(20260919)
    splits = [[i] for i in range(1, len(log))] + [
        sorted(rng.sample(range(1, len(log)), k)) for k in (3, 10, 40) for _ in range(20)
    ]
    for cuts in splits:
        stream = R.FrameStream(dialect=dialect)
        got = []
        for a, b in zip([0] + cuts, cuts + [len(log)]):
            got += stream.feed(log[a:b])
        stream.close()
        assert _fields(got) == _fields(whole), cuts
        assert stream.stats == whole_stats, cuts


# --- 記録 --------------------------------------------------------------------


def _recorder(tmp_path, dialect, template=None) -> REC.Recorder:
    return REC.Recorder(
        tmp_path, template or _filled_template(), software_commit="abc123", dialect=dialect
    )


def _feed_seq(rec: REC.Recorder, dialect: D.Dialect, seqs, **kwargs) -> None:
    """1 パケット 1 回の読み出しとして、届く時刻どおりに渡す。"""
    for q in seqs:
        rec.feed(_sample_frame(dialect, q, **kwargs), _t(q))


def test_a_recording_starts_at_the_rx_config_and_keeps_the_raw_bytes(tmp_path, dialect):
    """設定が届くまではセッションを作らず、**それまでの生のバイト列も残す**こと。"""
    rec = _recorder(tmp_path, dialect)
    rec.feed(b"boot noise", _t(0))
    _feed_seq(rec, dialect, [1])                        # 設定の前＝サンプルにしない
    assert rec.state == "waiting" and rec.directory is None
    rec.feed(_configs(dialect), _t(2))
    assert rec.state == "placed" and rec.slot == 0
    _feed_seq(rec, dialect, range(3, 13))
    rec.stop(_t(12.5))

    directory = rec.directory
    assert [s.seq for s in S.read_rx_samples(directory)] == list(range(3, 13))
    raw = b"".join(chunk for _, chunk in S.read_raw(directory))
    assert raw.startswith(b"boot noise")                # 設定の前の分も残っている
    assert [e.kind for e in S.read_events(directory)] == ["place", "stop"]


def test_the_sample_sequence_is_written_as_the_firmware_numbered_it(tmp_path, dialect):
    """ファームの通し番号と TX の設定番号を、そのまま samples.csv に残すこと
    （PC までの間で落ちた数は、書き出すときにここから数える）。"""
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(0))
    rec.feed(_sample_frame(dialect, 1, sample_seq=40), _t(1))
    rec.feed(_sample_frame(dialect, 2, sample_seq=42), _t(2))
    rec.stop(_t(3))
    written = list(S.read_rx_samples(rec.directory))
    assert [(s.sample_seq, s.tx_config_id) for s in written] == [(40, 2), (42, 2)]


def test_moving_and_placing_are_recorded_and_tag_the_samples(tmp_path, dialect):
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(-1))
    _feed_seq(rec, dialect, range(0, 10))
    rec.move(_t(9.5))
    _feed_seq(rec, dialect, range(10, 15))
    assert rec.place(_t(14.5)) == 1
    _feed_seq(rec, dialect, range(15, 25))
    rec.stop(_t(24.5))

    slots = [s.spatial_slot for s in S.read_rx_samples(rec.directory)]
    assert slots == [0] * 10 + [S.MOVING_SLOT] * 5 + [1] * 10
    with pytest.raises(S.SessionError):
        rec.move(_t(30))                                # 終えた後は動かせない


def test_placing_requires_moving_first(tmp_path, dialect):
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(-1))
    with pytest.raises(S.SessionError, match="移動中でない"):
        rec.place(_t(1))


def test_a_clock_that_goes_back_is_refused(tmp_path, dialect):
    """読み出しと操作の時刻が戻ったら止めること（読み直すと別の置き場所に振り分けられる）。"""
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(5))
    with pytest.raises(S.SessionError, match="戻って"):
        rec.move(_t(4))


def test_the_recording_waits_for_the_tx_config_too(tmp_path, dialect):
    """RX の設定だけではセッションを作らないこと＝TX の設定が分からないまま測ると、
    窓の分母（送信間隔）が雛形頼みに戻る。他の TX から中継された設定では始めない。"""
    rec = _recorder(tmp_path, dialect)
    rec.feed(_config_frame(dialect), _t(0))              # RX の設定だけ
    assert rec.state == "waiting" and rec.waiting_for == ["tx"]
    rec.feed(_tx_config_frame(dialect, device=b"\x09" * 6), _t(1))    # 近くの別の TX
    assert rec.state == "waiting"
    _feed_seq(rec, dialect, [2])
    rec.feed(_tx_config_frame(dialect), _t(3))
    assert rec.state == "placed" and rec.header is not None
    _feed_seq(rec, dialect, range(4, 8))
    rec.stop(_t(8))
    assert [s.seq for s in S.read_rx_samples(rec.directory)] == [4, 5, 6, 7]


def test_a_tx_config_change_ends_the_session(tmp_path, dialect):
    """中継された TX の設定が変わったら終えること（同じ設定の再送は続ける）。"""
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(-1))
    _feed_seq(rec, dialect, range(0, 3))
    rec.feed(_tx_config_frame(dialect), _t(3))          # 再送
    rec.feed(_tx_config_frame(dialect, device=b"\x09" * 6, interval_ms=50), _t(3.5))  # 別の TX
    assert rec.state == "placed"
    rec.feed(_tx_config_frame(dialect, config_id=3, interval_ms=200), _t(4))
    assert rec.state == "stopped" and rec.config_changed


def test_a_sample_from_a_changed_tx_ends_the_session(tmp_path, dialect):
    """TX の設定番号が変わったサンプルが、中継された設定より**先に**届いても終えること。

    ⚠️ そのサンプルは書かない＝ヘッダの TX の設定（送信間隔）で窓を刻むと、分母が
    別物になる。
    """
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(-1))
    _feed_seq(rec, dialect, range(0, 3))
    rec.feed(_sample_frame(dialect, 0, tx_config_id=3), _t(3))   # TX が再起動した
    assert rec.state == "stopped" and rec.config_changed
    assert [s.seq for s in S.read_rx_samples(rec.directory)] == [0, 1, 2]
    again = REC.replay(rec.directory, dialect)
    assert again.samples == list(S.read_rx_samples(rec.directory))


def test_samples_from_another_pair_are_kept_raw_but_not_written(tmp_path, dialect):
    """近くで別の組が動いていたら、そのサンプルは書かずに数えること（生ログには残る）。"""
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(-1))
    _feed_seq(rec, dialect, range(0, 3))
    rec.feed(_sample_frame(dialect, 500, tx=b"\x01\x02\x03\x04\x05\x06"), _t(3))
    rec.stop(_t(4))
    assert rec.foreign == 1
    assert [s.seq for s in S.read_rx_samples(rec.directory)] == [0, 1, 2]


def test_a_config_change_ends_the_session(tmp_path, dialect):
    """RX の設定が途中で変わったら終えること（ヘッダは書き換えない約束）。"""
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(-1))
    _feed_seq(rec, dialect, range(0, 5))
    rec.feed(_configs(dialect), _t(5))             # 同じ設定の再送は続ける
    assert rec.state == "placed"
    rec.feed(_sample_frame(dialect, 6) + _config_frame(dialect, channel=11), _t(6))
    assert rec.state == "stopped" and rec.config_changed
    # 変わった読み出しの分は書かない（読み直しと食い違わないように）
    assert [s.seq for s in S.read_rx_samples(rec.directory)] == [0, 1, 2, 3, 4]
    with pytest.raises(S.SessionError, match="終えたセッション"):
        rec.feed(b"x", _t(7))


def test_stopping_before_the_config_creates_nothing(tmp_path, dialect):
    rec = _recorder(tmp_path, dialect)
    rec.feed(b"noise", _t(0))
    rec.stop(_t(1))
    assert rec.directory is None and rec.state == "stopped"
    assert list(tmp_path.iterdir()) == []


def _chunked_session(tmp_path, dialect, seed: int) -> REC.Recorder:
    """細切れの読み出し・操作・雑音・化け・別の組・設定の変更を混ぜた測定。"""
    rng = random.Random(seed)
    stream = b"noise" + _configs(dialect)
    plan = []
    for q in range(0, 60):
        if rng.random() < 0.15:
            continue                                    # 電波で届かなかった
        frame = _sample_frame(dialect, q, rssi_raw=-140 + rng.randint(-5, 5))
        if rng.random() < 0.05:
            frame = bytes([frame[0]]) + frame[1:12] + bytes([frame[12] ^ 0x55]) + frame[13:]
        if rng.random() < 0.05:
            frame += _sample_frame(dialect, 900 + q, tx=b"\x09" * 6)
        plan.append((q, frame))
    rec = _recorder(tmp_path, dialect)
    rec.feed(stream[:7], _t(-2))
    rec.feed(stream[7:], _t(-1))
    for q, frame in plan:
        cut = rng.randint(1, len(frame) - 1)
        rec.feed(frame[:cut], _t(q))
        rec.feed(frame[cut:], _t(q + 0.3))
        if q == 20:
            rec.move(_t(q + 0.5))
        if q == 30:
            rec.place(_t(q + 0.5))
    rec.stop(_t(61))
    return rec


@pytest.mark.parametrize("seed", range(5))
def test_replaying_the_raw_bytes_gives_the_same_samples(tmp_path, dialect, seed):
    """🔑 生のバイト列と操作の記録から、**ライブで書いたものと同じ**サンプルを作り直せること。

    これが崩れると、生ログを残しても「作り直した結果が測った結果と違う」ので使えない。
    """
    rec = _chunked_session(tmp_path, dialect, seed)
    again = REC.replay(rec.directory, dialect)
    assert again.samples == list(S.read_rx_samples(rec.directory))
    assert again.stats == rec.stats
    assert again.foreign == rec.foreign


def test_replaying_samples_that_arrived_with_the_config_gives_the_same_samples(tmp_path, dialect):
    """設定と同じ読み出しに入っていたサンプルも、読み直しで同じに扱われること。

    ⚠️ 読み直しは「据えた」の記録を、同じ時刻の読み出しの**前**に当てる。後に当てると、
    ライブでは書いた設定直後のサンプルが、読み直しでは消える。
    """
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect) + _sample_frame(dialect, 0) + _sample_frame(dialect, 1), _t(1))
    _feed_seq(rec, dialect, range(2, 5))
    rec.stop(_t(5))
    written = list(S.read_rx_samples(rec.directory))
    assert [s.seq for s in written] == [0, 1, 2, 3, 4]
    assert REC.replay(rec.directory, dialect).samples == written


def test_replaying_a_config_change_gives_the_same_samples(tmp_path, dialect):
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(-1))
    _feed_seq(rec, dialect, range(0, 5))
    rec.feed(_sample_frame(dialect, 6) + _config_frame(dialect, channel=11), _t(6))
    again = REC.replay(rec.directory, dialect)
    assert again.samples == list(S.read_rx_samples(rec.directory))


def test_the_raw_index_must_point_into_the_raw_bytes(tmp_path, dialect):
    """索引が本体の外を指していたら読めたことにしないこと（書きかけで落ちた形）。"""
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(-1))
    rec.stop(_t(0))
    with (rec.directory / S.RAW_INDEX_FILE).open("a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow([_t(1), 10_000])
    with pytest.raises(S.SessionError, match="本体を指していません"):
        list(S.read_raw(rec.directory))


# --- CLI ---------------------------------------------------------------------


def test_the_template_command_writes_a_template_and_does_not_overwrite(tmp_path, capsys):
    path = tmp_path / "t.json"
    assert cli.main(["template", str(path)]) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == REC.header_template()
    assert cli.main(["template", str(path)]) == 2


def test_export_writes_the_batch_csv(tmp_path, dialect, capsys):
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(-0.5))
    _feed_seq(rec, dialect, range(0, 10))
    rec.stop(_t(29.5))                                  # 後半は受からずに終えた
    out = tmp_path / "out.csv"
    assert cli.main(["export", str(rec.directory), str(out)]) == 0
    with out.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["meas_dbm"] == "" for r in rows] == [False, True, True]
    printed = capsys.readouterr().out
    assert "打ち切り 2 個" in printed
    # 本体の送信電力に入れるのは SMA 端の実測（12.4）で、設定値（13）ではない（B-258）。
    assert "本体の送信電力を 12.40 dBm にして" in printed


def test_the_template_has_no_calibration_to_copy_by_hand():
    """🔑 雛形に校正値の欄を置かないこと（2026-09-19 ユーザー決定＝校正ファイルから
    個体 ID で写す）。欄があると、手で書き写した値と校正ファイルのどちらが効いたか
    後から分からない。"""
    template = REC.header_template()
    assert "calibration" not in template["tx"] and "calibration" not in template["rx"]


def test_a_tx_output_measured_at_another_setting_does_not_start_a_session(tmp_path, dialect):
    """実測出力を測った設定と、中継された TX の設定が違えば記録を始めないこと。"""
    template = _filled_template()
    template["tx"]["calibration"]["tx_output_power_cdbm"] = 1500   # TX は 13 dBm で動いている
    with pytest.raises(S.SessionError, match="実測出力"):
        REC.build_header(
            template,
            _message(dialect, _config_frame(dialect)),
            _message(dialect, _tx_config_frame(dialect)),
            started_utc=_t(0), software_commit="abc123", dialect=dialect,
        )


def test_export_of_an_unfinished_session_needs_recover(tmp_path, dialect, capsys):
    """終了の記録が無い（途中で落ちた）セッションを黙って書き出さないこと。"""
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(-0.5))
    _feed_seq(rec, dialect, range(0, 20))               # stop を呼ばずに落ちた
    out = tmp_path / "out.csv"
    assert cli.main(["export", str(rec.directory), str(out)]) == 2
    assert "--recover" in capsys.readouterr().err
    assert cli.main(["export", str(rec.directory), str(out), "--recover"]) == 0
    with out.open(encoding="utf-8", newline="") as f:
        assert len(list(csv.DictReader(f))) == 2


def test_export_warns_when_samples_disagree_with_the_raw_log(tmp_path, dialect, capsys):
    """samples.csv が生ログと食い違っていたら、書き出しても成功と言わないこと。"""
    rec = _recorder(tmp_path, dialect)
    rec.feed(_configs(dialect), _t(-0.5))
    _feed_seq(rec, dialect, range(0, 10))
    rec.stop(_t(9.5))
    S.append_rx_samples(rec.directory, [])              # 形は崩さずに
    path = rec.directory / S.SAMPLES_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")   # 1 行消えた
    assert cli.main(["export", str(rec.directory), str(tmp_path / "o.csv")]) == 1
    assert "一致しません" in capsys.readouterr().err


def test_the_software_commit_is_taken_from_git():
    commit = cli.software_commit()
    assert commit and all(c in "0123456789abcdef" for c in commit.removesuffix("-dirty"))
