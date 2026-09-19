"""
apps/tracer/aggregate.py
========================
受信サンプルを**窓ごとにまとめ**、本体のバッチ CSV へ書き出す（Tracer phase 1・増分2）。

    samples.csv（生値・1 受信 1 行）＋ events.csv（置き場所の区切り）
      → 窓（受信率と代表値） → バッチ CSV（1 窓 1 行）

**窓はシーケンス番号で刻む（時刻ではない）。** TX は等間隔で番号付きのパケットを
送るので、窓の期待パケット数は `集計窓の長さ ÷ 送信間隔` で決まり、その範囲に
実際に届いた番号を数えれば受信率がそのまま出る。時刻で刻むと、**届かなかった
パケットには時刻が無い**ので分母を作れない（欠けているものは数えられない）。

**打ち切り**（→ [[project-radiosim-tracer]] §5-4・§6.1-2）
  受信率が刻印のしきい値を下回る窓は、値を出さずに打ち切りとして記録する。
  ⛔ **一部だけ届いた窓の平均を実測として出さない**＝弱いパケットから先に落ちるので、
  残った強いパケットの平均は楽観側へ偏る。閉じた測定だけを集めると、較正が
  「効いている」ように見える偽陽性を生む。

**平均のとり方＝dB 領域の算術平均**（2026-09-19 ユーザー決定）。§7 の分解能の要求
（1 dB 量子化で σ=1/√12、N≥10 で標準誤差 0.09 dB）は dB 領域で逆算されており、
実装を同じ領域に揃える。⚠️ **速いフェージングが窓に入ると、dB 平均は真の平均電力
より低く出る**（負に偏る）。受け入れ試験（§6.1-3 の段0）でこの偏りを測る。

⚠️ **この層は打ち切りの「しきい値そのもの」を決めない**＝しきい値は受け入れ試験で
決めてセッションの刻印に入れる値で、コードの定数ではない。
"""

from __future__ import annotations

import bisect
import csv
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from core.batch_csv_schema import CSV_COLUMNS

from apps.tracer.session import (
    Event,
    RxSample,
    SessionError,
    SessionHeader,
    parse_utc,
    to_dbm,
    validate_events,
)

# 2.4 GHz 帯のチャネル → 中心周波数 [MHz]。**表で持つ**（式で書くと 14 番だけ外れる）。
# 設定メッセージのチャネル番号から本体の `freq` 列を作るのに使う。
CHANNEL_MHZ = {ch: 2412 + (ch - 1) * 5 for ch in range(1, 14)} | {14: 2484}


@dataclass(frozen=True)
class Window:
    """1 つの集計窓。**打ち切りでも行として残る**（捨てると統計が偏る）。"""

    index: int                  # セッションの中の通し番号（CSV の id になる）
    config_id: int
    spatial_slot: int           # 空間平均の置き場所。窓はここを跨がない
    seq_start: int              # この窓が覆うシーケンス番号の先頭
    seq_count: int              # 期待パケット数（分母）
    received: int               # 実際に届いた数（分子）
    meas_dbm: float | None      # 打ち切りなら None
    noise_floor_raw_mean: float | None
    first_pc_utc: str           # 届いた最初/最後の PC 時刻（空＝1 つも届かなかった）
    last_pc_utc: str

    @property
    def receive_rate(self) -> float:
        return self.received / self.seq_count

    @property
    def censored(self) -> bool:
        return self.meas_dbm is None


def window_seq_count(header: SessionHeader) -> int:
    """1 窓が覆うシーケンス番号の数＝期待パケット数。

    送信間隔は **TX の設定**から取る（受信側の積分窓ではない）。
    """
    interval_ms = header.tx.radio.tx_interval_ms
    if interval_ms <= 0:
        raise SessionError(f"送信間隔が正の値ではありません: {interval_ms}")
    count = round(header.provenance.censor_window_s * 1000.0 / interval_ms)
    if count < 1:
        raise SessionError(
            f"集計窓（{header.provenance.censor_window_s} 秒）が送信間隔"
            f"（{interval_ms} ms）より短く、1 パケットも入りません"
        )
    return count


def aggregate(
    header: SessionHeader, samples: Iterable[RxSample], events: list[Event]
) -> list[Window]:
    """受信サンプルを窓へまとめる。**サンプルはシーケンス番号の昇順**で渡すこと。

    窓は**置き場所に据えていた間**（操作の記録の `place` から次の `move`/`stop` まで）
    の中だけで刻み、跨がない。置き場所が変われば別の場所を測っているので、跨いで
    平均すると**混ざったものが 1 つの実測値として本体へ入る**。移動中はどの窓にも
    入れない。

    🔑 **区切りはサンプルではなく操作の記録から決める。** サンプルの側から区切ると
    - 受信が途絶えた末尾が窓にならず、**打ち切りが静かに減る**（§5-4 が避けたい偏り）
    - 1 つも受からなかった置き場所は**存在ごと見えない**（いちばん悪い場所が消える）
    操作の時刻を「TX がその時点までに送った番号」へ直せば、受からなかった分も
    分母に入る（→ `_SeqClock`）。
    """
    validate_events(events)
    per_window = window_seq_count(header)
    ordered = list(samples)
    _check_samples(header, ordered)
    if not ordered:
        # 時刻を番号へ直す手がかりが無い。機材の不具合（チャネル違いなど）と電波の
        # 弱さを見分けられないので、「全部打ち切り」とも言えない。
        raise SessionError(
            "受信サンプルが 1 つもないので窓を作れません（機材の設定を確かめてください）"
        )
    clock = _SeqClock(ordered, header.tx.radio.tx_interval_ms / 1000.0)
    by_seq = {s.seq: s for s in ordered}
    key_config = header.rx.radio.config_id

    windows: list[Window] = []
    for slot, placed_at, left_at in _placements(events):
        start = clock.last_sent(placed_at) + 1
        last = clock.last_sent(left_at)
        # 端数の窓は出さない。**分母が足りない**ので受信率が意味を持たない。
        while start + per_window - 1 <= last:
            got = [by_seq[q] for q in range(start, start + per_window) if q in by_seq]
            windows.append(
                _make_window(header, len(windows), (key_config, slot), start, per_window, got)
            )
            start += per_window
    return windows


def _placements(events: list[Event]) -> list[tuple[int, datetime, datetime]]:
    """`(置き場所, 据えた時刻, 離れた時刻)` の並び。離れた＝次の move か stop。"""
    spans = []
    for current, following in zip(events, events[1:]):
        if current.kind == "place":
            spans.append(
                (current.spatial_slot, parse_utc(current.pc_utc), parse_utc(following.pc_utc))
            )
    return spans


class _SeqClock:
    """PC の時刻 →「TX がその時点までに送った最後の番号」。

    TX は等間隔に番号を振るので、届いたサンプル 1 つを基準にすれば、届かなかった
    時間帯の番号も `基準の番号 + 経過 ÷ 送信間隔` で分かる。基準には**その時刻の
    直前に届いたサンプル**（無ければ直後）を使う＝外挿の距離を短くして、TX と PC の
    時計の進み方の差（水晶の ppm 級）を効かせない。

    ⚠️ 受信の遅れ（UART・USB）ぶん番号が 1 つ前後するが、窓は数十パケットで、
    区切りの端数の窓は出さないので、窓の中身には効かない。
    """

    def __init__(self, samples: list[RxSample], interval_s: float):
        self._interval_s = interval_s
        self._times: list[float] = []
        self._seqs: list[int] = []
        for s in samples:
            moment = parse_utc(s.pc_utc).timestamp()
            if self._times and moment < self._times[-1]:
                # PC の時計が戻った（時刻合わせ）。直すと番号の対応が崩れる。
                raise SessionError(
                    f"サンプルの受信時刻が戻っています: seq {s.seq} の {s.pc_utc}"
                )
            self._times.append(moment)
            self._seqs.append(s.seq)

    def last_sent(self, moment: datetime) -> int:
        t = moment.timestamp()
        index = bisect.bisect_right(self._times, t) - 1
        index = max(index, 0)
        offset = (t - self._times[index]) / self._interval_s
        return self._seqs[index] + math.floor(offset)


def _check_samples(header: SessionHeader, samples: list[RxSample]) -> None:
    expected_config = header.rx.radio.config_id
    expected_tx = header.tx.device_id
    previous = None
    for s in samples:
        if s.config_id != expected_config:
            # 知らない設定番号のサンプルは、積分窓も検波方式も引けない＝どの条件で
            # 取ったか分からない値になる。黙って混ぜない。
            raise SessionError(
                f"ヘッダに無い設定番号のサンプルがあります: {s.config_id}"
                f"（ヘッダの RX は {expected_config}）"
            )
        if s.tx_id != expected_tx:
            # 近くで別の送信機が動いている。番号の系列が別物なので、混ぜると
            # 受信率も平均も意味を失う。
            raise SessionError(
                f"ヘッダの TX と違う送信機のサンプルがあります: {s.tx_id}"
                f"（ヘッダの TX は {expected_tx}）"
            )
        if previous is not None and s.seq <= previous:
            raise SessionError(
                f"サンプルがシーケンス番号の昇順ではありません: {previous} の次が {s.seq}"
            )
        previous = s.seq


def _make_window(
    header: SessionHeader,
    index: int,
    key: tuple[int, int],
    seq_start: int,
    seq_count: int,
    got: list[RxSample],
) -> Window:
    config_id, spatial_slot = key
    rate = len(got) / seq_count
    censored = rate < header.provenance.censor_min_receive_rate
    if censored or not got:
        meas_dbm = None
        noise = None
    else:
        calibration = header.rx.calibration
        meas_dbm = sum(to_dbm(s.rssi_raw, calibration) for s in got) / len(got)
        noise = sum(s.noise_floor_raw for s in got) / len(got)
    return Window(
        index=index,
        config_id=config_id,
        spatial_slot=spatial_slot,
        seq_start=seq_start,
        seq_count=seq_count,
        received=len(got),
        meas_dbm=meas_dbm,
        noise_floor_raw_mean=noise,
        first_pc_utc=got[0].pc_utc if got else "",
        last_pc_utc=got[-1].pc_utc if got else "",
    )


def censored_fraction(windows: list[Window]) -> float:
    """打ち切りになった窓の割合（§6.1-2B＝集計は Tracer 側の仕事）。"""
    if not windows:
        return 0.0
    return sum(1 for w in windows if w.censored) / len(windows)


# --- 本体のバッチ CSV への書き出し -------------------------------------------


def censoring_note(header: SessionHeader) -> str:
    """打ち切りの行に入れる `note`。

    ⛔ **感度の値を `meas_dbm` に入れて `meas_method` で区別する形は採らない**＝
    本体は `meas_method` を見ずに、値が入っていればそれを実測として使う（→ §6.1-2B）。
    """
    return f"感度以下（{header.rx.radio.sensitivity_dbm:g} dBm 未満）"


def to_csv_rows(header: SessionHeader, windows: list[Window]) -> list[list[object]]:
    """窓を本体のバッチ CSV の行へ変換する（列の契約は `core/batch_csv_schema`）。

    **利得と給電線損の割り振り**＝本体の予測は
    `p_rx = eirp + gain_rx − total_loss` で、`eirp` に TX 側の利得が入り、給電線損は
    モデルに無い。残差は `predicted − (meas_dbm + feeder_loss_db)` で取る
    （`core/residuals.py`）ので、**`feeder_loss_db` 列は RX 側だけ**の量になる。
    ⇒ TX 側の給電線損は行き場が無いので、`gain_tx` から引いて渡す。
    ⚠️ ここを両側の和にすると、TX 側が**二重に効く**。
    """
    if header.tx.position is None or header.rx.position is None:
        # 位置がサンプルごとに決まる構成（機体で測る段）。1 本の経路に畳めない。
        raise SessionError(
            "端点の位置がセッション定数になっていないので、経路 1 行の CSV に"
            "書き出せません（位置の時系列を持つ構成は phase 2 の扱い）"
        )
    channel = header.rx.radio.channel
    if channel not in CHANNEL_MHZ:
        raise SessionError(f"2.4 GHz 帯のチャネルではありません: {channel}")

    tx, rx = header.tx.position, header.rx.position
    rows: list[list[object]] = []
    for w in windows:
        rows.append([
            f"{header.session_id}-w{w.index:04d}",
            f"{tx.lat}, {tx.lon}",
            f"{rx.lat}, {rx.lon}",
            tx.height_agl_m,
            rx.height_agl_m,
            CHANNEL_MHZ[channel],
            header.tx.antenna_gain_dbi - header.tx.feeder_loss_db,
            header.rx.antenna_gain_dbi,
            censoring_note(header) if w.censored else "",
            "" if w.meas_dbm is None else round(w.meas_dbm, 2),
            header.meas_method,
            header.rx.feeder_loss_db,
            header.env_class,
        ])
    return rows


def write_batch_csv(
    path: str | os.PathLike[str], header: SessionHeader, windows: list[Window]
) -> None:
    """バッチ CSV を書き出す（一時ファイル → `os.replace`）。

    直に開くと、書き込みの途中で落ちたときに**切れた CSV** が残る。読む側は
    それを「短い測定」として黙って受け取ってしまう。
    """
    rows = to_csv_rows(header, windows)
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".batch-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
