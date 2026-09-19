"""
tests/test_tracer_aggregate.py
==============================
実測補助アプリ（`apps/tracer`）の**窓の集計と打ち切り**を検証する。

🔑 **ここで守っているのは「統計を偏らせない」約束**＝どれも、外しても値は出るが
出た値が静かに間違う種類のもの。

  - 一部だけ届いた窓の平均を実測として出さないこと（強いパケットだけが残る）
  - 受信が途絶えた区間が**窓にならずに消えない**こと（打ち切りが数から落ちる）
  - 設定・置き場所を跨いで平均しないこと（別の条件が 1 つの値に混ざる）
  - TX 側の給電線損が二重に効かないこと

⚠️ **実行時の制約はコメントではなくテストで表す**（コメントは守らないので）。
"""

from __future__ import annotations

import csv

import pytest

from apps.tracer import aggregate as A
from apps.tracer import session as S
from core.batch_csv_schema import CSV_COLUMNS


# --- 叩き台 ------------------------------------------------------------------


def _calibration() -> S.Calibration:
    return S.Calibration(
        measured_on="2026-09-19",
        offset_db=-96.0,
        scale_db_per_count=0.5,
        reference_unit_id="ref-01",
        reference_measured_on="2026-09-10",
    )


def _radio(**kwargs) -> S.RadioSettings:
    values = dict(
        config_id=1,
        firmware_version="0123456789ab",
        channel=6,
        rate_kbps=1000,
        tx_power_cdbm=1300,
        tx_interval_ms=100,
        detector="mean",
        integration_window_us=1000,
        integration_samples=10,
        antenna="external",
        sensitivity_dbm=-98.0,
    )
    values.update(kwargs)
    return S.RadioSettings(**values)


def _endpoint(role: str, **kwargs) -> S.Endpoint:
    values = dict(
        role=role,
        measurement_config_id=f"{role}-esp32c6-rod-3m",
        device_id="aa:bb:cc:dd:ee:0" + ("1" if role == "tx" else "2"),
        feeder_loss_db=1.5 if role == "rx" else 2.0,
        antenna_gain_dbi=2.0,
        calibration=_calibration(),
        radio=_radio(),
        position=S.Position(
            lat=35.0 if role == "tx" else 35.1,
            lon=139.0 if role == "tx" else 139.1,
            elevation_m=120.5,
            height_agl_m=45.0 if role == "tx" else 20.0,
            height_source="survey",
        ),
    )
    values.update(kwargs)
    return S.Endpoint(**values)


def _header(censor_rate: float = 0.9, window_s: float = 1.0, **kwargs) -> S.SessionHeader:
    values = dict(
        session_id="20260919T0100Z-site-a",
        started_utc="2026-09-19T01:00:00Z",
        target_kind="existing_link",
        env_class="rural",
        meas_method="esp32c6-2g4",
        provenance=S.Provenance(
            software_commit="5aab362",
            geoid_model="GSIGEO2011",
            censor_min_receive_rate=censor_rate,
            censor_window_s=window_s,
        ),
        tx=_endpoint("tx"),
        rx=_endpoint("rx"),
    )
    values.update(kwargs)
    return S.SessionHeader(**values)


def _sample(seq: int, rssi_raw: int = -140, slot: int = 0, config_id: int = 1) -> S.RxSample:
    return S.RxSample(
        pc_utc=f"2026-09-19T01:00:{seq % 60:02d}Z",
        radio_time_us=1_000_000 + seq * 100_000,
        seq=seq,
        tx_id="aa:bb:cc:dd:ee:01",
        rssi_raw=rssi_raw,
        noise_floor_raw=-190,
        config_id=config_id,
        spatial_slot=slot,
    )


def _run(seqs, header=None, **sample_kwargs) -> list[A.Window]:
    header = header or _header()
    return A.aggregate(header, [_sample(q, **sample_kwargs) for q in seqs])


# --- 窓の刻み ----------------------------------------------------------------


def test_window_size_comes_from_the_interval_and_the_window_length():
    """期待パケット数は `集計窓 ÷ 送信間隔`（送信間隔は TX の設定から取る）。"""
    assert A.window_seq_count(_header(window_s=1.0)) == 10
    assert A.window_seq_count(_header(window_s=2.5)) == 25


def test_a_window_shorter_than_one_packet_is_refused():
    """1 パケットも入らない窓は作らないこと（受信率の分母が 0 になる）。"""
    with pytest.raises(S.SessionError, match="1 パケットも入りません"):
        A.window_seq_count(_header(window_s=0.01))


def test_a_full_window_reports_the_mean_in_db():
    """満ちた窓は dB 領域の算術平均を返すこと（2026-09-19 の決定）。"""
    windows = _run(range(100, 110))
    assert len(windows) == 1
    assert windows[0].received == 10
    assert windows[0].meas_dbm == pytest.approx(-140 * 0.5 - 96.0)
    assert not windows[0].censored


def test_the_mean_is_taken_in_the_db_domain_not_in_power():
    """⚠️ dB 平均と電力平均は別物＝取り違えたら落ちること。

    振幅の差が大きい窓では、電力平均のほうが高く出る（強いサンプルに引かれる）。
    どちらを採ったかが黙って入れ替わらないよう、値そのもので固定する。
    """
    strong, weak = -100, -180                      # 生値 → −146 dBm / −186 dBm
    header = _header(window_s=0.2)                 # 2 パケットで 1 窓
    samples = [_sample(100, rssi_raw=strong), _sample(101, rssi_raw=weak)]
    got = A.aggregate(header, samples)[0].meas_dbm
    assert got == pytest.approx((-146.0 + -186.0) / 2)      # dB 平均 = −166 dBm
    assert got != pytest.approx(-149.0, abs=1.0)            # 電力平均なら約 −149 dBm


# --- 打ち切り ----------------------------------------------------------------


def test_a_partly_received_window_is_censored_instead_of_averaged():
    """受信率がしきい値を下回る窓は値を出さないこと。

    🔑 **弱いパケットから先に落ちる**ので、届いた分の平均は楽観側へ偏る。
    「少しでも届いたのだから平均すればよい」が、較正の偽陽性を作る。
    """
    # ⚠️ 末尾に満ちた窓を 1 つ足してある＝**最後の区間の端数は窓にならない**ので、
    # 途切れた窓だけを並べると何も出ない（この穴は増分4 の停止時刻で塞ぐ）。
    windows = _run([100, 101, 102, 103, 104] + list(range(110, 120)))
    assert [w.receive_rate for w in windows] == [pytest.approx(0.5), pytest.approx(1.0)]
    assert windows[0].censored and windows[0].meas_dbm is None
    assert not windows[1].censored


def test_a_window_that_received_nothing_still_exists():
    """1 つも届かなかった窓も**行として残る**こと（捨てると打ち切りが数から落ちる）。"""
    windows = _run(list(range(100, 110)) + list(range(120, 130)))
    assert [w.received for w in windows] == [10, 0, 10]
    assert [w.censored for w in windows] == [False, True, False]
    assert windows[1].first_pc_utc == ""


def test_the_tail_of_a_segment_is_recovered_from_the_next_segment():
    """置き場所を移す直前に途絶えた区間が、窓にならずに消えないこと。

    ⚠️ 区間の終わりを「最後に届いたパケット」で切ると、リンクが落ちた末尾が
    そもそも窓にならない＝**打ち切りが静かに減る**（§5-4 が避けたい偏り）。
    """
    samples = (
        [_sample(q, slot=1) for q in range(100, 110)]     # 満ちた窓
        + [_sample(q, slot=1) for q in range(110, 112)]   # 次の窓は 2 個で途絶
        + [_sample(q, slot=2) for q in range(120, 130)]   # 置き場所を移した
    )
    windows = A.aggregate(_header(), samples)
    assert [(w.spatial_slot, w.received) for w in windows] == [(1, 10), (1, 2), (2, 10)]
    assert windows[1].censored


def test_the_censored_fraction_is_reported():
    """打ち切りの割合を Tracer 側で出せること（本体には渡さない集計・§6.1-2B）。"""
    windows = _run(list(range(100, 110)) + list(range(120, 125)))
    assert A.censored_fraction(windows) == pytest.approx(0.5)
    assert A.censored_fraction([]) == 0.0


# --- 混ぜないこと ------------------------------------------------------------


def test_windows_do_not_cross_a_spatial_slot():
    """空間平均の置き場所を跨いで平均しないこと（別の場所の値が 1 つに混ざる）。"""
    samples = (
        [_sample(q, slot=1) for q in range(100, 105)]
        + [_sample(q, slot=2) for q in range(105, 115)]
    )
    windows = A.aggregate(_header(), samples)
    # 置き場所 1 は 5 個しか無く**端数の窓**なので出ない。2 は 10 個で 1 窓。
    assert [(w.spatial_slot, w.received) for w in windows] == [(2, 10)]


def test_a_partial_window_at_the_end_is_not_emitted():
    """端数の窓は出さないこと（分母が足りず、受信率が意味を持たない）。"""
    windows = _run(range(100, 115))
    assert [w.seq_start for w in windows] == [100]


def test_an_unknown_config_id_is_refused():
    """ヘッダに無い設定番号のサンプルを黙って混ぜないこと。"""
    with pytest.raises(S.SessionError, match="設定番号"):
        _run(range(100, 110), config_id=7)


def test_samples_must_be_in_sequence_order():
    """シーケンス番号の昇順でないサンプル列は受け付けないこと。"""
    with pytest.raises(S.SessionError, match="昇順"):
        _run([100, 102, 101])


def test_no_samples_gives_no_windows():
    assert A.aggregate(_header(), []) == []


# --- 本体のバッチ CSV への受け渡し -------------------------------------------


def _rows(windows, header=None) -> list[dict[str, str]]:
    header = header or _header()
    rows = A.to_csv_rows(header, windows)
    return [dict(zip(CSV_COLUMNS, [str(v) for v in row])) for row in rows]


def test_the_csv_uses_the_shared_column_contract(tmp_path):
    """列と順序は `core/batch_csv_schema` から引くこと（Tracer 側で再定義しない）。"""
    path = tmp_path / "out.csv"
    A.write_batch_csv(path, _header(), _run(range(100, 110)))
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == CSV_COLUMNS
    assert len(rows) == 2


def test_a_censored_row_carries_the_sensitivity_and_no_value():
    """打ち切りの行は `meas_dbm` を空にし、`note` に感度を書くこと。

    ⛔ 感度の値を `meas_dbm` に入れて `meas_method` で区別する形は採らない＝本体は
    `meas_method` を見ずに、値が入っていればそれを実測として使う。
    """
    row = _rows(_run([100, 101, 102] + list(range(110, 120))))[0]
    assert row["meas_dbm"] == ""
    assert row["note"] == "感度以下（-98 dBm 未満）"


def test_a_measured_row_has_no_note():
    row = _rows(_run(range(100, 110)))[0]
    assert row["note"] == ""
    assert float(row["meas_dbm"]) == pytest.approx(-166.0)


def test_the_tx_feeder_loss_is_not_counted_twice():
    """`feeder_loss_db` 列は RX 側だけ。TX 側は `gain_tx` から引くこと。

    🔑 本体の残差は `predicted − (meas_dbm + feeder_loss_db)` で、`predicted` の
    `eirp` に TX 側の利得が入る（給電線損はモデルに無い）。両側の和を列に入れると、
    TX 側が**二重に効く**。
    """
    row = _rows(_run(range(100, 110)))[0]
    assert float(row["feeder_loss_db"]) == pytest.approx(1.5)     # RX 側だけ
    assert float(row["gain_tx"]) == pytest.approx(2.0 - 2.0)      # 利得 − TX 給電線損
    assert float(row["gain_rx"]) == pytest.approx(2.0)            # RX は引かない


def test_the_channel_becomes_a_frequency():
    """チャネル番号を本体の `freq`（MHz）へ直すこと。"""
    assert _rows(_run(range(100, 110)))[0]["freq"] == "2437"      # ch6
    assert A.CHANNEL_MHZ[1] == 2412 and A.CHANNEL_MHZ[14] == 2484

    bad = _header(rx=_endpoint("rx", radio=_radio(channel=36)))
    with pytest.raises(S.SessionError, match="チャネル"):
        A.to_csv_rows(bad, [])


def test_the_row_id_is_unique_per_window():
    """窓ごとに別の id を振ること（同じ id だと本体側で 1 行に潰れる）。"""
    ids = [r["id"] for r in _rows(_run(range(100, 130)))]
    assert ids == [
        "20260919T0100Z-site-a-w0000",
        "20260919T0100Z-site-a-w0001",
        "20260919T0100Z-site-a-w0002",
    ]


def test_a_session_without_constant_positions_is_refused():
    """位置がサンプルごとに決まる構成は、経路 1 行の CSV へ畳まないこと。

    黙って畳むと、機体が動いた測定が「1 本の固定回線」として本体に入る。
    """
    header = _header(rx=_endpoint("rx", position=None))
    with pytest.raises(S.SessionError, match="phase 2"):
        A.to_csv_rows(header, [])


def test_the_batch_csv_is_written_atomically(tmp_path, monkeypatch):
    """書き込みの途中で落ちても、切れた CSV が残らないこと。"""
    path = tmp_path / "out.csv"
    A.write_batch_csv(path, _header(), _run(range(100, 110)))
    before = path.read_text(encoding="utf-8")

    def explode(*args, **kwargs):
        raise RuntimeError("書き込みの途中で落ちた")

    monkeypatch.setattr(A.csv, "writer", explode)
    with pytest.raises(RuntimeError):
        A.write_batch_csv(path, _header(), _run(range(100, 110)))
    monkeypatch.undo()

    assert path.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob(".batch-*"))
