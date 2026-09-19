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
from datetime import datetime, timedelta, timezone

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
        radio=_radio(sensitivity_dbm=None if role == "tx" else -98.0),   # TX は受けない
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


_BASE = datetime(2026, 9, 19, 1, 0, 0, tzinfo=timezone.utc)


def _t(seq_time: float) -> str:
    """「番号 seq_time のパケットが届く時刻」（送信間隔 100 ms）。"""
    return S.format_utc(_BASE + timedelta(seconds=seq_time * 0.1))


def _sample(
    seq: int, rssi_raw: int = -140, slot: int = 0, config_id: int = 1,
    tx_id: str = "aa:bb:cc:dd:ee:01", sample_seq: int = 0, tx_config_id: int = 1,
) -> S.RxSample:
    """`sample_seq` の既定は全部 0＝番号が進まないので、**PC までの間の欠けは数えない**
    （`link_gaps` は番号が進んだところだけを見る）。欠けを試すテストは明示して渡す。"""
    return S.RxSample(
        pc_utc=_t(seq),
        radio_time_us=1_000_000 + seq * 100_000,
        seq=seq,
        tx_id=tx_id,
        rssi_raw=rssi_raw,
        noise_floor_raw=-190,
        config_id=config_id,
        tx_config_id=tx_config_id,
        sample_seq=sample_seq,
        spatial_slot=slot,
    )


def _events(*steps: tuple[str, float, int]) -> list[S.Event]:
    """`(種類, 番号で測った時刻, 置き場所)` の並び → 操作の記録。"""
    return [
        S.Event(_t(when), kind, slot if kind == "place" else S.MOVING_SLOT)
        for kind, when, slot in steps
    ]


def _run(seqs, header=None, stop_at: float | None = None, **sample_kwargs) -> list[A.Window]:
    """置き場所 0 に据えたまま測り、最後の受信の直後（または `stop_at`）に終える。"""
    header = header or _header()
    seqs = list(seqs)
    events = _events(
        ("place", seqs[0] - 0.5, 0),
        ("stop", seqs[-1] + 0.5 if stop_at is None else stop_at, 0),
    )
    return A.aggregate(header, [_sample(q, **sample_kwargs) for q in seqs], events)


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
    events = _events(("place", 99.5, 0), ("stop", 101.5, 0))
    got = A.aggregate(header, samples, events)[0].meas_dbm
    assert got == pytest.approx((-146.0 + -186.0) / 2)      # dB 平均 = −166 dBm
    assert got != pytest.approx(-149.0, abs=1.0)            # 電力平均なら約 −149 dBm


# --- 打ち切り ----------------------------------------------------------------


def test_a_partly_received_window_is_censored_instead_of_averaged():
    """受信率がしきい値を下回る窓は値を出さないこと。

    🔑 **弱いパケットから先に落ちる**ので、届いた分の平均は楽観側へ偏る。
    「少しでも届いたのだから平均すればよい」が、較正の偽陽性を作る。
    """
    windows = _run([100, 101, 102, 103, 104], stop_at=109.5)
    assert [w.receive_rate for w in windows] == [pytest.approx(0.5)]
    assert windows[0].censored and windows[0].meas_dbm is None


def test_a_window_that_received_nothing_still_exists():
    """1 つも届かなかった窓も**行として残る**こと（捨てると打ち切りが数から落ちる）。"""
    windows = _run(list(range(100, 110)) + list(range(120, 130)))
    assert [w.received for w in windows] == [10, 0, 10]
    assert [w.censored for w in windows] == [False, True, False]
    assert windows[1].first_pc_utc == ""


def test_the_tail_before_a_move_is_not_lost():
    """動かす直前に途絶えた分が、窓にならずに消えないこと。

    ⚠️ 区間の終わりを「最後に届いたパケット」で切ると、リンクが落ちた末尾が
    そもそも窓にならない＝**打ち切りが静かに減る**（§5-4 が避けたい偏り）。
    区切りは操作の時刻から「TX がそこまでに送った番号」を出して決める。
    """
    samples = (
        [_sample(q, slot=1) for q in range(100, 110)]     # 満ちた窓
        + [_sample(q, slot=1) for q in range(110, 112)]   # 次の窓は 2 個で途絶
        + [_sample(q, slot=2) for q in range(120, 130)]   # 据え直した
    )
    events = _events(
        ("place", 99.5, 1), ("move", 119.5, 0), ("place", 119.7, 2), ("stop", 129.5, 0)
    )
    windows = A.aggregate(_header(), samples, events)
    assert [(w.spatial_slot, w.received) for w in windows] == [(1, 10), (1, 2), (2, 10)]
    assert windows[1].censored


def test_the_tail_of_the_session_is_closed_by_the_stop():
    """最後の置き場所の末尾が、終了の記録まで窓になること（増分2 で残した穴）。

    受信が途絶えた後も TX は送り続けている。終了の時刻が無いと、その分の打ち切りが
    **セッションの最後でだけ**消える。
    """
    windows = _run(range(100, 110), stop_at=129.5)
    assert [(w.seq_start, w.received) for w in windows] == [(100, 10), (110, 0), (120, 0)]


def test_a_slot_that_received_nothing_still_has_windows():
    """1 つも受からなかった置き場所も、打ち切りの窓として残ること。

    🔑 サンプルから区切ると、この置き場所は**存在ごと見えない**＝いちばん悪い場所が
    数から落ちる。操作の記録から区切るので、受信ゼロでも窓ができる。
    """
    samples = [_sample(q, slot=0) for q in range(100, 110)] + [
        _sample(q, slot=2) for q in range(140, 150)
    ]
    events = _events(
        ("place", 99.5, 0), ("move", 109.5, 0),
        ("place", 110.3, 1), ("move", 130.3, 0),         # ここでは 1 つも受からない
        ("place", 139.5, 2), ("stop", 149.5, 0),
    )
    windows = A.aggregate(_header(), samples, events)
    slot1 = [w for w in windows if w.spatial_slot == 1]
    assert [(w.seq_start, w.received) for w in slot1] == [(111, 0), (121, 0)]
    assert all(w.censored for w in slot1)


def test_samples_received_while_moving_are_not_in_any_window():
    """移動中に受けたものは窓に入れないこと（どちらの場所の値でもない）。"""
    samples = [_sample(q) for q in range(100, 130)]
    events = _events(
        ("place", 99.5, 0), ("move", 109.5, 0), ("place", 119.5, 1), ("stop", 129.5, 0)
    )
    windows = A.aggregate(_header(), samples, events)
    assert [(w.spatial_slot, w.seq_start) for w in windows] == [(0, 100), (1, 120)]


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
    events = _events(
        ("place", 99.5, 1), ("move", 104.5, 0), ("place", 104.6, 2), ("stop", 114.5, 0)
    )
    windows = A.aggregate(_header(), samples, events)
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


def test_a_sample_from_another_transmitter_is_refused():
    """ヘッダの TX と違う送信機のサンプルを混ぜないこと（番号の系列が別物）。"""
    with pytest.raises(S.SessionError, match="違う送信機"):
        _run(range(100, 110), tx_id="aa:bb:cc:dd:ee:99")


def test_no_samples_is_an_error_not_an_empty_result():
    """1 つも受からなかったセッションを「窓 0 個」で済ませないこと。

    時刻を番号へ直す手がかりが無く、機材の不具合と電波の弱さも見分けられない。
    黙って空を返すと、いちばん悪い測定が何も無かったことになる。
    """
    with pytest.raises(S.SessionError, match="1 つもない"):
        A.aggregate(_header(), [], _events(("place", 0, 0), ("stop", 10, 0)))


def test_the_session_must_have_been_stopped():
    """終了の記録が無い並びから窓を作らないこと（末尾がどこまでか分からない）。"""
    with pytest.raises(S.SessionError, match="stop で終わって"):
        A.aggregate(_header(), [_sample(100)], _events(("place", 99.5, 0)))


def test_a_clock_that_goes_back_is_refused():
    """受信時刻が戻った列から番号を出さないこと（時刻と番号の対応が崩れる）。"""
    samples = [_sample(100), S.RxSample(**{**_sample(101).__dict__, "pc_utc": _t(90)})]
    with pytest.raises(S.SessionError, match="戻って"):
        A.aggregate(_header(), samples, _events(("place", 89, 0), ("stop", 102, 0)))


def test_a_sample_from_another_tx_config_is_refused():
    """TX の設定が変わった後のサンプルを混ぜないこと（送信間隔が違えば窓の分母が別物）。"""
    with pytest.raises(S.SessionError, match="TX の設定番号"):
        _run(range(100, 110), tx_config_id=7)


# --- 受信機から PC までの間で落ちた分（§6.6 の提案①） --------------------------


def _received(pairs, slot: int = 0) -> list[S.RxSample]:
    """`(seq, sample_seq)` の並び → サンプル。sample_seq はファームが受信時に振った番号。"""
    return [_sample(q, sample_seq=n, slot=slot) for q, n in pairs]


def _windows(pairs, first: int = 100, last: int = 119) -> list[A.Window]:
    events = _events(("place", first - 0.5, 0), ("stop", last + 0.5, 0))
    return A.aggregate(_header(), _received(pairs), events)


def test_samples_lost_after_reception_are_taken_out_of_the_denominator():
    """🔑 電波では届いたが PC までの間で落ちた分を、打ち切りの根拠にしないこと。

    108〜112 はファームが番号（8〜12）を振ったが PC に届かなかった＝番号の欠けが
    ちょうど seq の欠けと同じ数なので、どの窓の分か番号で分かる（窓を跨いでも）。
    落ち方は受信レベルと関係ないので、残りは偏りの無い部分集合。
    """
    kept = [q for q in range(100, 120) if not 108 <= q <= 112]
    windows = _windows([(q, q - 100) for q in kept])
    assert [w.received for w in windows] == [8, 7]
    assert [w.link_lost for w in windows] == [2, 3]
    assert [w.receive_rate for w in windows] == [1.0, 1.0]
    assert not any(w.censored for w in windows)


def test_a_mixed_gap_inside_one_window_is_credited_to_that_window():
    """電波の欠け（103）と PC までの欠け（104＝番号 3）が混ざっていても、両端が同じ窓に
    あれば、その窓の分として分母から引くこと。"""
    pairs = [(100, 0), (101, 1), (102, 2)] + [(q, q - 101) for q in range(105, 110)]
    windows = _windows(pairs, last=109)
    assert windows[0].link_lost == 1
    assert windows[0].receive_rate == pytest.approx(8 / 9)
    assert windows[0].censored                          # 8/9 < 0.9＝電波の欠けは残る


def test_a_mixed_gap_across_windows_is_not_credited_anywhere():
    """⚠️ 混ざっていて窓を跨ぐものは、どの窓の分か分からない＝**どの窓にも振らない**。

    振ると、本当は電波で欠けた分まで分母から消え、受信率が高く見える（打ち切りが
    減る＝§5-4 が避けたい向き）。数は総数の側にだけ残る。
    """
    # 108(番号 8) → [109 は電波で欠け] → [110 は番号 9 で PC までに落ちた] → 111(番号 10)
    pairs = [(q, q - 100) for q in range(100, 109)] + [(q, q - 101) for q in range(111, 120)]
    windows = _windows(pairs)
    assert [w.link_lost for w in windows] == [0, 0]
    assert A.link_lost_total(_received(pairs)) == 1


def test_a_sample_sequence_that_goes_back_is_not_counted_as_lost():
    """受信機が再起動すると通し番号は 0 に戻る＝何件落ちたか分からないので、落ちたことに
    しない（数えると、大きな負の数や、でたらめな正の数が分母から消える）。"""
    pairs = [(q, 500 + q - 100) for q in range(100, 105)] + [(q, q - 105) for q in range(105, 110)]
    windows = _windows(pairs, last=109)
    assert windows[0].link_lost == 0 and windows[0].received == 10
    assert A.link_lost_total(_received(pairs)) == 0


def test_a_window_lost_entirely_after_reception_is_not_censored():
    """窓の全部が PC までの間で落ちた＝電波については何も分からない。「感度以下」では
    ないので打ち切りに数えず、行の note もそれと分かる文面にする。"""
    pairs = [(q, q - 100) for q in range(100, 110)] + [(q, q - 100) for q in range(120, 130)]
    windows = _windows(pairs, last=129)
    assert [w.lost_on_link for w in windows] == [False, True, False]
    assert not any(w.censored for w in windows)
    assert A.censored_fraction(windows) == 0.0
    row = _rows(windows)[1]
    assert row["meas_dbm"] == "" and row["note"] == A.LINK_LOST_NOTE


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
