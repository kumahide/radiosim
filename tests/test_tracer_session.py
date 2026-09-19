"""
tests/test_tracer_session.py
============================
実測補助アプリ（`apps/tracer`）の測定セッションの器を検証する。

🔑 **ここで守っているのは「後から足せない約束」**＝データモデルの穴は、測り直し
以外で埋められない。具体的には:
  - 位置・姿勢は「セッション定数」と「サンプルごとの時系列」の両方を許すこと
  - 生値を捨てないこと（換算は書き出すときだけ）
  - 打ち切りのしきい値が刻印に入っていること
  - 時間平均（窓の件数）と空間平均（置き場所の番号）が別の場所に在ること
  - CLAS の高さは Fix 解のときだけ通すこと

⚠️ **実行時の制約はコメントではなくテストで表す**（コメントは守らないので）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.tracer import session as S


# --- 叩き台（各テストが必要なところだけ差し替える） --------------------------


def _calibration() -> S.Calibration:
    return S.Calibration(
        measured_on="2026-09-19",
        offset_db=-96.0,
        scale_db_per_count=0.5,
        reference_unit_id="ref-01",
        reference_measured_on="2026-09-10",
    )


def _radio(config_id: int = 1, sensitivity_dbm: float | None = -98.0) -> S.RadioSettings:
    return S.RadioSettings(
        config_id=config_id,
        firmware_version="0123456789ab",
        channel=6,
        rate_kbps=1000,
        tx_power_cdbm=1300,
        tx_interval_ms=100,
        detector="mean",
        integration_window_us=1000,
        integration_samples=10,
        antenna="external",
        sensitivity_dbm=sensitivity_dbm,
    )


def _position(height_source: str = "survey", fix_state: str = "none") -> S.Position:
    return S.Position(
        lat=35.0,
        lon=139.0,
        elevation_m=120.5,
        height_agl_m=45.0,
        height_source=height_source,
        fix_state=fix_state,
        antenna_offset_m=1.2,
    )


def _endpoint(role: str, **kwargs) -> S.Endpoint:
    values = dict(
        role=role,
        measurement_config_id=f"{role}-esp32c6-rod-3m",
        device_id="aa:bb:cc:dd:ee:0" + ("1" if role == "tx" else "2"),
        feeder_loss_db=1.5,
        antenna_gain_dbi=2.0,
        calibration=_calibration(),
        radio=_radio(sensitivity_dbm=None if role == "tx" else -98.0),   # TX は受けない
        position=_position(),
        orientation=S.Orientation(azimuth_deg=210.0, elevation_deg=-1.0, source="コンパス"),
    )
    values.update(kwargs)
    return S.Endpoint(**values)


def _header(**kwargs) -> S.SessionHeader:
    values = dict(
        session_id="20260919T0100Z-site-a",
        started_utc="2026-09-19T01:00:00Z",
        target_kind="existing_link",
        env_class="rural",
        meas_method="esp32c6-2g4",
        provenance=S.Provenance(
            software_commit="0002066",
            geoid_model="GSIGEO2011",
            censor_min_receive_rate=0.9,
            censor_window_s=10.0,
        ),
        tx=_endpoint("tx"),
        rx=_endpoint("rx"),
    )
    values.update(kwargs)
    return S.SessionHeader(**values)


def _samples(count: int = 3) -> list[S.RxSample]:
    return [
        S.RxSample(
            pc_utc=f"2026-09-19T01:00:0{i}Z",
            radio_time_us=1_000_000 + i * 100_000,
            seq=100 + i,
            tx_id="aa:bb:cc:dd:ee:01",
            rssi_raw=-140 + i,
            noise_floor_raw=-190,
            config_id=1,
            tx_config_id=4,
            sample_seq=7 + i,
            spatial_slot=2,
        )
        for i in range(count)
    ]


# --- 往復 --------------------------------------------------------------------


def test_header_round_trip(tmp_path):
    """書いたヘッダがそのまま読み戻ること（刻印と端点を含めて）。"""
    directory = S.create_session(tmp_path, _header())
    loaded = S.read_header(directory)
    assert loaded == _header()


def test_samples_are_append_only(tmp_path):
    """2 回に分けて追記しても、先に書いた行が残っていること。"""
    directory = S.create_session(tmp_path, _header())
    S.append_rx_samples(directory, _samples(2))
    S.append_rx_samples(directory, _samples(3)[2:])
    assert [s.seq for s in S.read_rx_samples(directory)] == [100, 101, 102]


def test_position_fixes_are_optional(tmp_path):
    """位置の時系列は無くても読めること（phase 1 は定数で足りる構成もある）。"""
    directory = S.create_session(tmp_path, _header())
    assert list(S.read_position_fixes(directory)) == []

    fixes = [S.PositionFix("2026-09-19T01:00:00Z", 35.0, 139.0, 155.3, "fix")]
    S.append_position_fixes(directory, fixes)
    assert list(S.read_position_fixes(directory)) == fixes


def test_header_is_written_atomically(tmp_path):
    """ヘッダの書き込みは一時ファイル経由＝途中の断片が `session.json` に現れない。

    書き込みの途中で落ちた状況を、`json.dump` を失敗させて作る。
    """
    directory = S.create_session(tmp_path, _header())
    before = (directory / S.HEADER_FILE).read_text(encoding="utf-8")

    def explode(*args, **kwargs):
        raise RuntimeError("書き込みの途中で落ちた")

    real_dump = json.dump
    json.dump = explode
    try:
        with pytest.raises(RuntimeError):
            S._write_json_atomic(directory / S.HEADER_FILE, {"session_id": "x"})
    finally:
        json.dump = real_dump

    assert (directory / S.HEADER_FILE).read_text(encoding="utf-8") == before
    # 一時ファイルも残らない（次の測定が「知らないファイル」を見つけないこと）
    assert not list(directory.glob(".session-*"))


# --- 後から足せない約束 ------------------------------------------------------


def test_position_may_be_a_timeseries_instead_of_a_constant(tmp_path):
    """端点の位置は `None`（＝サンプルごとの時系列で決まる構成）も許すこと。

    🔑 固定 2 点だけを前提にすると、機体で測る段でデータモデルごと書き直しになる。
    """
    header = _header(tx=_endpoint("tx", position=None))
    directory = S.create_session(tmp_path, header)
    assert S.read_header(directory).tx.position is None


def test_time_average_and_spatial_average_live_in_different_places():
    """時間平均は設定側の件数、空間平均はサンプル側の置き場所の番号。

    ⚠️ 1 つの「平均サンプル数」に混ぜると、後から分けられない（量子化・雑音の平均は
    静止でよいが、マルチパスの平均は動かさないと得られない＝役割が違う）。
    """
    assert "integration_samples" in S.RadioSettings.__dataclass_fields__
    assert "spatial_slot" in S.RxSample.__dataclass_fields__
    assert "spatial_slot" not in S.RadioSettings.__dataclass_fields__
    assert "integration_samples" not in S.RxSample.__dataclass_fields__


def test_raw_values_survive_the_round_trip(tmp_path):
    """サンプルは生値のまま残ること（換算値で上書きしないこと）。"""
    directory = S.create_session(tmp_path, _header())
    S.append_rx_samples(directory, _samples(1))
    sample = next(iter(S.read_rx_samples(directory)))
    assert sample.rssi_raw == -140 and isinstance(sample.rssi_raw, int)


def test_to_dbm_does_not_apply_feeder_loss():
    """換算に給電線損を混ぜないこと。

    🔑 本体は残差を `predicted − (meas_dbm + feeder_loss_db)` で計算する＝給電線損は
    CSV の独立した列として渡すもの。ここで足すと**二重に効く**。
    """
    cal = _calibration()
    assert S.to_dbm(-140, cal) == pytest.approx(-140 * 0.5 - 96.0)


def test_censoring_threshold_is_part_of_the_provenance():
    """打ち切りのしきい値が刻印に在ること（後から「どの基準で切ったか」を辿れる）。"""
    assert "censor_min_receive_rate" in S.Provenance.__dataclass_fields__
    assert "censor_window_s" in S.Provenance.__dataclass_fields__


def test_target_kind_is_recorded():
    """既設回線か候補地点かを刻めること（phase 1 の成功例バイアスを後から補正する材料）。"""
    assert S.TARGET_KINDS == ("existing_link", "candidate_site")
    assert "target_kind" in S.SessionHeader.__dataclass_fields__


# --- 語彙と検証 --------------------------------------------------------------


def test_clas_height_requires_a_fix_solution(tmp_path):
    """高さの出どころが CLAS なら解の状態は Fix でなければ通らないこと。

    Float 以下では、誤差がデシメートル〜メートル級に戻ったまま、もっともらしい値として
    全サンプルに乗る（この層のずれは較正の判定そのものを誤らせる）。
    """
    bad = _endpoint("rx", position=_position(height_source="clas_fix", fix_state="float"))
    with pytest.raises(S.SessionError, match="clas_fix"):
        S.create_session(tmp_path, _header(rx=bad))

    good = _endpoint("rx", position=_position(height_source="clas_fix", fix_state="fix"))
    assert S.create_session(tmp_path, _header(rx=good)).is_dir()


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"target_kind": "既設"}, "測定対象の種別"),
        ({"session_id": ""}, "session_id"),
    ],
)
def test_header_vocabulary_is_closed(tmp_path, kwargs, expected):
    """語彙の外の値・空の ID は保存しないこと（綴り違いが別の意味になるのを防ぐ）。"""
    with pytest.raises(S.SessionError, match=expected):
        S.create_session(tmp_path, _header(**kwargs))


@pytest.mark.parametrize("rate", [0.0, 1.5])
def test_censoring_threshold_must_be_a_receive_rate(tmp_path, rate):
    """しきい値は 0 より大きく 1 以下の受信率。

    0 は「全部欠けた窓だけを打ち切りにする」＝一部だけ届いた窓の**強いパケットだけが
    残った平均**が実測として本体に入る（楽観側へ偏る）。
    """
    provenance = S.Provenance(
        software_commit="0002066",
        geoid_model="GSIGEO2011",
        censor_min_receive_rate=rate,
        censor_window_s=10.0,
    )
    with pytest.raises(S.SessionError, match="打ち切りのしきい値"):
        S.create_session(tmp_path, _header(provenance=provenance))


@pytest.mark.parametrize("sens", [-131.0, -19.0, 0.0])
def test_sensitivity_must_be_a_plausible_level(tmp_path, sens):
    """受信感度は本体が受け付ける範囲（−130〜−20 dBm）でなければ保存しないこと。

    🔑 この値はそのまま打ち切りの「〜未満」として本体へ渡る文面になる＝既定の 0 や
    符号の取り違えを通すと、**もっともらしい文面のまま嘘の下限**が帳票に載る。
    """
    bad = _endpoint("rx", radio=S.RadioSettings(**{
        **{f: getattr(_radio(), f) for f in S.RadioSettings.__dataclass_fields__},
        "sensitivity_dbm": sens,
    }))
    with pytest.raises(S.SessionError, match="受信感度"):
        S.create_session(tmp_path, _header(rx=bad))


def test_antenna_gain_and_feeder_loss_are_separate_fields():
    """アンテナ利得と給電線損を同じ欄にまとめないこと。

    ⚠️ 本体の予測は利得をモデルに持ち、給電線損は持たない（残差の式が別に引く）＝
    1 つに畳むと、どちらの向きで効かせるかを後から決められない。
    """
    assert "antenna_gain_dbi" in S.Endpoint.__dataclass_fields__
    assert "feeder_loss_db" in S.Endpoint.__dataclass_fields__


def test_measurement_config_id_is_required(tmp_path):
    """測定構成 ID が空のセッションは作れないこと。

    これが無いと「ケーブルが 1 本傷んでいた」と後で分かったときに、そのサンプルだけを
    隔離できず全部捨てるしかない。
    """
    with pytest.raises(S.SessionError, match="測定構成 ID"):
        S.create_session(tmp_path, _header(tx=_endpoint("tx", measurement_config_id="")))


def test_the_tx_has_no_sensitivity_and_the_rx_must_have_one(tmp_path):
    """TX は受けないので受信感度を持たせない（値があると、どこかで RX の感度と取り違えて
    「感度以下（〜未満）」の文面に乗り得る）。RX は必須（打ち切りの文面になる）。"""
    with pytest.raises(S.SessionError, match="TX に受信感度"):
        S.create_session(tmp_path, _header(tx=_endpoint("tx", radio=_radio())))
    with pytest.raises(S.SessionError, match="受信感度が範囲の外"):
        S.create_session(tmp_path, _header(rx=_endpoint("rx", radio=_radio(sensitivity_dbm=None))))


def test_roles_are_not_swapped(tmp_path):
    """TX の欄に RX の端点を入れたら落ちること（取り違えは値の意味ごと入れ替わる）。"""
    with pytest.raises(S.SessionError, match="tx"):
        S.create_session(tmp_path, _header(tx=_endpoint("rx")))


def test_existing_session_is_never_reused(tmp_path):
    """同じ session_id のフォルダには作らないこと（追記のみの器に別の測定が混ざる）。"""
    S.create_session(tmp_path, _header())
    with pytest.raises(S.SessionError, match="既に"):
        S.create_session(tmp_path, _header())


def test_unknown_schema_version_is_refused(tmp_path):
    """知らない形式の版を黙って読まないこと。"""
    directory = S.create_session(tmp_path, _header())
    path = directory / S.HEADER_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = S.SCHEMA_VERSION + 1
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(S.SessionError, match="セッション形式の版"):
        S.read_header(directory)


def test_unknown_fields_are_not_silently_dropped(tmp_path):
    """知らない項目を黙って捨てないこと（新しい版で足した項目を読み落とさない）。"""
    directory = S.create_session(tmp_path, _header())
    path = directory / S.HEADER_FILE
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tx"]["polarization"] = "vertical"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(S.SessionError, match="polarization"):
        S.read_header(directory)


def test_the_helper_is_not_bundled_with_the_main_app():
    """本体の配布物に `apps/` が混ざらないこと（対で置く「無いことの検査」）。

    ⚠️ いま入っていないのは、`main.py` がこのアプリを引いていないからで、守っている
    からではない。⇒ 同梱一覧に名前が出たら落ちる形で固定する（別アプリを配るなら
    その版で**意図して**この検査を書き換える）。
    """
    spec = Path(__file__).resolve().parent.parent / "radiosim.spec"
    assert "apps/" not in spec.read_text(encoding="utf-8"), (
        "radiosim.spec が apps/ を同梱しようとしている。"
        "実測補助は本体とは別のアプリで、本体の配布物には入らない。"
    )


def test_sample_columns_must_match(tmp_path):
    """サンプル CSV の列が違えば読まないこと。"""
    directory = S.create_session(tmp_path, _header())
    (directory / S.SAMPLES_FILE).write_text("seq,rssi_raw\n1,-140\n", encoding="utf-8")
    with pytest.raises(S.SessionError, match="列が違います"):
        list(S.read_rx_samples(directory))


# --- 操作の記録（置き場所の区切り） --------------------------------------------


def _ev(kind: str, second: int, slot: int = S.MOVING_SLOT) -> S.Event:
    return S.Event(f"2026-09-19T01:00:{second:02d}.000000Z", kind, slot)


def test_a_well_formed_sequence_of_operations_passes():
    S.validate_events([
        _ev("place", 0, 0), _ev("move", 10), _ev("place", 20, 1), _ev("stop", 30),
    ])


@pytest.mark.parametrize("events, message", [
    ([_ev("move", 0), _ev("stop", 1)], "place で始まって"),
    ([_ev("place", 0, 0), _ev("place", 1, 1), _ev("stop", 2)], "続いて"),
    ([_ev("place", 0, 0), _ev("move", 1), _ev("place", 2, 0), _ev("stop", 3)], "使い回し"),
    ([_ev("place", 0, 0), _ev("stop", 1), _ev("move", 2)], "stop の後"),
    ([_ev("place", 5, 0), _ev("stop", 4)], "戻って"),
    ([_ev("place", 0, 0), _ev("move", 1, 3), _ev("stop", 2)], "番号が付いて"),
    ([_ev("place", 0, 0)], "stop で終わって"),
    ([], "記録がありません"),
])
def test_a_broken_sequence_of_operations_is_refused(events, message):
    """崩れた並びから窓を作らないこと（区切りを決める材料なので）。

    とくに**置き場所の番号の使い回し**＝同じ番号が 2 か所を指すと、後からどちらの
    場所の値か分からない。
    """
    with pytest.raises(S.SessionError, match=message):
        S.validate_events(events)


def test_an_unfinished_sequence_is_allowed_while_measuring():
    S.validate_events([_ev("place", 0, 0), _ev("move", 1)], finished=False)
    S.validate_events([], finished=False)


def test_times_must_be_utc():
    """時差の無い時刻（PC の現地時刻）を受け付けないこと＝9 時間ずれたまま対応づく。"""
    with pytest.raises(S.SessionError, match="UTC"):
        S.parse_utc("2026-09-19T10:00:00")
    with pytest.raises(S.SessionError, match="UTC"):
        S.parse_utc("2026-09-19T10:00:00+09:00")
    assert S.parse_utc(S.format_utc(S.parse_utc("2026-09-19T01:00:00Z"))).hour == 1


def test_an_event_that_breaks_the_order_is_not_appended(tmp_path):
    """追記の前に、それまでの並びに続けて約束を通るか見ること（書いてから気づかない）。"""
    directory = S.create_session(tmp_path, _header())
    S.append_event(directory, _ev("place", 0, 0))
    with pytest.raises(S.SessionError):
        S.append_event(directory, _ev("place", 1, 1))
    assert [e.kind for e in S.read_events(directory)] == ["place"]
