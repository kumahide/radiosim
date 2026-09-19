"""
apps/tracer/recorder.py
=======================
**測りながら書く**（Tracer phase 1・増分4）。UART から届いたバイト列と作業者の
操作を受け取り、セッションのフォルダへ追記していく。

⚠️ **ここはシリアルポートもキーボードも知らない。** `feed(バイト列, 受けた時刻)` と
`place`/`move`/`stop` を呼ぶのは CLI（`apps/tracer/cli.py`）の仕事で、ここを
純粋に保つのは**測定の現場でしか試せない部分を最小にする**ため。

**流れ**
  1. 雛形（JSON）を読み、未記入が無いか先に確かめる（現場で気づくと測れない）
  2. `TRACER_CONFIG` を 2 つ待つ＝RX 自身の設定と、**TX が空中で送り RX が中継した
     TX の設定**。両方届いたら、雛形＋その 2 つでヘッダを組み、セッションを作り、
     置き場所 0 に据えたことを記録する
  3. 以後、受信サンプルを追記する。操作（移動開始・設置・終了）は操作の記録へ
  4. RX か TX の設定が途中で変わったら、そこで終える（ヘッダは書き換えない約束なので、
     別の条件のサンプルを同じセッションに入れられない）。TX の変更は、中継された
     設定か、サンプルに載った TX の設定番号の**早く届いたほう**で気づく

**生のバイト列は全部残す**（セッションを作る前に届いた分も含む）。受けた時刻の
索引と一緒に残すので、**あとから同じ処理を通して samples.csv を作り直せる**
（`replay`）。ライブで書いたものと作り直したものが一致することはテストで見ている＝
復号の不具合が後で見つかっても、測定を取り直さずに済む。
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from apps.tracer.mavlink.dialect import Dialect, load_dialect
from apps.tracer.mavlink.reader import (
    FrameStream,
    Message,
    ReadStats,
    mac,
    to_radio_settings,
    to_rx_sample,
)
from apps.tracer.session import (
    MOVING_SLOT,
    SCHEMA_VERSION,
    Event,
    RxSample,
    SessionError,
    SessionHeader,
    append_event,
    append_raw,
    append_rx_samples,
    create_session,
    format_utc,
    header_from_dict,
    parse_utc,
    read_events,
    read_header,
    read_raw,
)

# 雛形に書かせない項目＝記録する側が埋める。書けると、測った事実と違う値
# （古いコミット・前回の開始時刻）が雛形から紛れ込む。
_FILLED_BY_RECORDER = (
    ("session_id",),
    ("started_utc",),
    ("schema_version",),
    ("provenance", "software_commit"),
)


# --- 雛形 --------------------------------------------------------------------


def header_template() -> dict[str, Any]:
    """雛形の骨組み。**`null` の欄が記入するところ**（記入し忘れは記録を始めない）。

    RX の無線設定は機器が送ってくるので、書くのは受信感度だけ。**TX の無線設定は
    書かせない**＝TX が空中で送り RX が中継してくる（雛形に書けると、実機と食い違った
    送信間隔・電力が黙ってヘッダに入る）。
    """

    def endpoint(role: str) -> dict[str, Any]:
        return {
            "role": role,
            "measurement_config_id": None,
            "device_id": None,
            "feeder_loss_db": None,
            "antenna_gain_dbi": None,
            "calibration": {
                "measured_on": None,
                "offset_db": None,
                "scale_db_per_count": None,
                "reference_unit_id": None,
                "reference_measured_on": None,
                "note": "",
            },
            "radio": None,
            "position": {
                "lat": None,
                "lon": None,
                "elevation_m": None,
                "height_agl_m": None,
                "height_source": None,
                "fix_state": "none",
                "antenna_offset_m": 0.0,
            },
            "orientation": None,
        }

    tx = endpoint("tx")
    del tx["radio"]
    rx = endpoint("rx")
    rx["radio"] = {"sensitivity_dbm": None}
    return {
        "target_kind": None,
        "env_class": None,
        "meas_method": None,
        "provenance": {
            "geoid_model": None,
            "censor_min_receive_rate": None,
            "censor_window_s": None,
            "operator": "",
            "note": "",
        },
        "tx": tx,
        "rx": rx,
        "site_note": "",
    }


def check_template(template: dict[str, Any]) -> None:
    """記録を始める前の検査。**未記入（`null`）と、書いてはいけない項目**を見る。"""
    for path in _FILLED_BY_RECORDER:
        node: Any = template
        for key in path[:-1]:
            node = node.get(key, {}) if isinstance(node, dict) else {}
        if isinstance(node, dict) and path[-1] in node:
            raise SessionError(
                f"雛形に {'.'.join(path)} があります（記録する側が埋める項目です）"
            )
    if "radio" in template.get("tx", {}):
        raise SessionError(
            "雛形に tx.radio があります（TX の無線設定は、TX が空中で送ってくる設定から"
            "埋めます）"
        )
    rx_radio = template.get("rx", {}).get("radio")
    if not isinstance(rx_radio, dict) or set(rx_radio) != {"sensitivity_dbm"}:
        raise SessionError(
            "雛形の rx.radio には sensitivity_dbm だけを書きます"
            "（残りは RX が送ってくる設定から埋めます）"
        )
    missing = sorted(_unfilled(template))
    if missing:
        raise SessionError("雛形に未記入の項目があります: " + ", ".join(missing))


def _unfilled(node: Any, prefix: str = "") -> list[str]:
    if node is None:
        return [prefix or "(全体)"]
    if isinstance(node, dict):
        found: list[str] = []
        for key, value in node.items():
            if key == "orientation" and value is None:
                continue                      # 向きは任意（出どころの無い値を書かせない）
            found += _unfilled(value, f"{prefix}.{key}" if prefix else key)
        return found
    return []


def build_header(
    template: dict[str, Any],
    rx_config: Message,
    tx_config: Message,
    *,
    started_utc: str,
    software_commit: str,
    dialect: Dialect | None = None,
) -> SessionHeader:
    """雛形と、RX の設定・中継された TX の設定からヘッダを組む。

    🔑 **RX の個体 ID は雛形と機器で一致させる。** 校正値は個体ごとの値なので、
    別の個体を挿したまま測ると、その個体に**別の個体の校正**が掛かった dBm が出る。
    TX も同じ（送信電力の校正が個体ごと）。
    """
    check_template(template)
    if not software_commit:
        raise SessionError("ソフトウェアのコミットが空です（刻印の 1 項目）")
    if rx_config.name != "TRACER_CONFIG" or not _is_rx(rx_config, dialect):
        raise SessionError("RX の設定メッセージではありません")
    if tx_config.name != "TRACER_CONFIG" or _is_rx(tx_config, dialect):
        raise SessionError("TX の設定メッセージではありません")
    tx_device = mac(tx_config.fields["device_id"])
    declared_tx = template["tx"]["device_id"]
    if declared_tx.lower() != tx_device:
        raise SessionError(
            f"雛形の TX の個体 ID（{declared_tx}）と、中継された TX の設定（{tx_device}）が違います"
        )
    device = mac(rx_config.fields["device_id"])
    declared = template["rx"]["device_id"]
    if declared.lower() != device:
        raise SessionError(
            f"雛形の RX の個体 ID（{declared}）と、繋がっている機器（{device}）が違います"
            "＝校正値が別の個体のものになります"
        )
    payload = copy.deepcopy(template)
    started = parse_utc(started_utc)
    payload["session_id"] = started.strftime("%Y%m%dT%H%M%SZ")
    payload["started_utc"] = started_utc
    payload["schema_version"] = SCHEMA_VERSION
    payload["provenance"]["software_commit"] = software_commit
    payload["rx"]["device_id"] = device
    # 機器から来る個体 ID は小文字。雛形の大文字をそのまま残すと、TX のサンプルが
    # 全部「別の組」として窓から外れる。
    payload["tx"]["device_id"] = tx_device
    payload["rx"]["radio"] = asdict(
        to_radio_settings(rx_config, template["rx"]["radio"]["sensitivity_dbm"], dialect)
    )
    payload["tx"]["radio"] = asdict(to_radio_settings(tx_config, None, dialect))
    return header_from_dict(payload)


def _is_rx(config: Message, dialect: Dialect | None) -> bool:
    return (dialect or load_dialect()).label("TRACER_ROLE", config.fields["role"]) == "rx"


# --- 記録の芯（ライブと読み直しで同じものを通す） ----------------------------


@dataclass
class _Processor:
    """バイト列 → 受信サンプル。**ライブも読み直しも、これ 1 つを通る。**

    `on_start` は、RX の設定と（`tx_device` の）TX の設定が揃ったときにヘッダを返す
    関数（ライブ＝組んでセッションを作る／読み直し＝既存のヘッダを返す）。揃う前は
    それぞれ**最後に届いたもの**を持っておく。
    """

    on_start: Callable[[Message, Message, str], SessionHeader]
    dialect: Dialect
    tx_device: str                     # 待つ TX の個体 ID（小文字）。他の TX の設定は見ない
    stream: FrameStream = field(init=False)
    header: SessionHeader | None = None
    rx_config: Message | None = None   # セッションを作る前に届いた最後の設定
    tx_config: Message | None = None
    slot: int | None = None            # None＝まだ据えていない／終えた
    stopped: bool = False
    foreign: int = 0                   # ヘッダと違う送受信機のサンプル（窓に入れない）

    def __post_init__(self) -> None:
        self.stream = FrameStream(dialect=self.dialect)

    def apply(self, event: Event) -> None:
        if event.kind == "place":
            self.slot = event.spatial_slot
        elif event.kind == "move":
            self.slot = MOVING_SLOT
        else:
            self.slot = None
            self.stopped = True

    def process(self, chunk: bytes, pc_utc: str) -> tuple[list[RxSample], bool]:
        """`(書くサンプル, 設定が変わったか)`。

        ⚠️ **設定が変わった読み出しは、その回のサンプルを 1 つも書かない。**
        変わる前の分だけ書くと、読み直し（終了の記録をその読み出しの前に当てる）と
        結果が食い違う。落ちるのは読み出し 1 回ぶん（100 ms 程度）で、どのみち
        設定が切り替わる境目の値。
        """
        samples: list[RxSample] = []
        for message in self.stream.feed(chunk):
            if message.name == "TRACER_CONFIG":
                if self.stopped:
                    continue
                is_rx = _is_rx(message, self.dialect)
                if not is_rx and mac(message.fields["device_id"]) != self.tx_device:
                    continue                  # 近くの別の TX（中継されてきた）
                if self.header is None:
                    if is_rx:
                        self.rx_config = message
                    else:
                        self.tx_config = message
                    if self.rx_config is not None and self.tx_config is not None:
                        self.header = self.on_start(self.rx_config, self.tx_config, pc_utc)
                elif self._differs(message, is_rx):
                    return [], True
                continue
            if message.name != "TRACER_RX_SAMPLE" or self.header is None:
                continue
            if self.slot is None:
                continue
            if (mac(message.fields["tx_id"]) != self.header.tx.device_id
                    or mac(message.fields["rx_id"]) != self.header.rx.device_id):
                # 近くで別の組が動いている。番号の系列が別物なので混ぜない
                # （生のバイト列には残っている）。
                self.foreign += 1
                continue
            if message.fields["tx_config_id"] != self.header.tx.radio.config_id:
                # TX の設定が変わった（中継された設定より先に届くことがある）。
                # ヘッダの TX の設定は、もうこのサンプルの条件ではない。
                return [], True
            samples.append(to_rx_sample(message, pc_utc, self.slot))
        return samples, False

    def _differs(self, message: Message, is_rx: bool) -> bool:
        assert self.header is not None
        end = self.header.rx if is_rx else self.header.tx
        now = to_radio_settings(message, end.radio.sensitivity_dbm, self.dialect)
        return now != end.radio or mac(message.fields["device_id"]) != end.device_id


# --- ライブ ------------------------------------------------------------------


class Recorder:
    """測りながら書く。呼び手（CLI）は時刻を**同じ時計で、進む向きに**渡すこと。"""

    def __init__(
        self,
        root: str | Path,
        template: dict[str, Any],
        *,
        software_commit: str,
        dialect: Dialect | None = None,
    ):
        check_template(template)
        self._root = Path(root)
        self._template = template
        self._software_commit = software_commit
        self._dialect = dialect or load_dialect()
        self._processor = _Processor(
            on_start=self._start,
            dialect=self._dialect,
            tx_device=template["tx"]["device_id"].lower(),
        )
        self._pending_raw: list[tuple[str, bytes]] = []
        self._last_time: datetime | None = None
        self.directory: Path | None = None
        self.samples_written = 0
        self.config_changed = False

    # 状態 --------------------------------------------------------------------

    @property
    def header(self) -> SessionHeader | None:
        return self._processor.header

    @property
    def state(self) -> str:
        """`waiting`（設定待ち）／`placed`／`moving`／`stopped`。"""
        p = self._processor
        if p.stopped:
            return "stopped"
        if p.header is None:
            return "waiting"
        return "moving" if p.slot == MOVING_SLOT else "placed"

    @property
    def slot(self) -> int | None:
        return self._processor.slot

    @property
    def waiting_for(self) -> list[str]:
        """まだ届いていない設定（`rx`／`tx`）。セッションを作る前だけ意味がある。"""
        p = self._processor
        return [name for name, got in (("rx", p.rx_config), ("tx", p.tx_config)) if got is None]

    @property
    def stats(self) -> ReadStats:
        return self._processor.stream.stats

    @property
    def foreign(self) -> int:
        return self._processor.foreign

    # 入力 --------------------------------------------------------------------

    def feed(self, chunk: bytes, pc_utc: str) -> None:
        """UART から読めた分を渡す（空でもよい）。"""
        self._advance(pc_utc)
        if self.state == "stopped":
            raise SessionError("終えたセッションには書けません")
        if not chunk:
            return
        if self.directory is None:
            self._pending_raw.append((pc_utc, chunk))
        else:
            append_raw(self.directory, chunk, pc_utc)
        samples, changed = self._processor.process(chunk, pc_utc)
        if self.directory is not None and self._pending_raw:
            # このセッションはこの読み出しで始まった＝それまでの分もまとめて残す。
            for when, data in self._pending_raw:
                append_raw(self.directory, data, when)
            self._pending_raw = []
        if samples:
            assert self.directory is not None
            append_rx_samples(self.directory, samples)
            self.samples_written += len(samples)
        if changed:
            self.config_changed = True
            self._record(Event(pc_utc, "stop"))
            self._processor.stream.close()

    def move(self, pc_utc: str) -> None:
        """アンテナを動かし始めた。"""
        self._advance(pc_utc)
        if self.state != "placed":
            raise SessionError(f"据えていない状態（{self.state}）からは動かせません")
        self._record(Event(pc_utc, "move"))

    def place(self, pc_utc: str) -> int:
        """次の置き場所に据えた。番号は 1 つずつ進む（使い回さない）。"""
        self._advance(pc_utc)
        if self.state != "moving":
            raise SessionError(f"移動中でない状態（{self.state}）では据え直せません")
        assert self.directory is not None
        used = [e.spatial_slot for e in read_events(self.directory) if e.kind == "place"]
        slot = max(used) + 1
        self._record(Event(pc_utc, "place", slot))
        return slot

    def stop(self, pc_utc: str) -> None:
        """測定を終える。設定待ちのまま終えたら、セッションは作られていない。"""
        self._advance(pc_utc)
        if self.state == "stopped":
            return
        if self.directory is None:
            self._processor.stopped = True
            return
        self._record(Event(pc_utc, "stop"))
        self._processor.stream.close()

    # 内部 --------------------------------------------------------------------

    def _start(self, rx_config: Message, tx_config: Message, pc_utc: str) -> SessionHeader:
        header = build_header(
            self._template,
            rx_config,
            tx_config,
            started_utc=pc_utc,
            software_commit=self._software_commit,
            dialect=self._dialect,
        )
        self.directory = create_session(self._root, header)
        self._record(Event(pc_utc, "place", 0))
        return header

    def _record(self, event: Event) -> None:
        assert self.directory is not None
        append_event(self.directory, event)
        self._processor.apply(event)

    def _advance(self, pc_utc: str) -> None:
        moment = parse_utc(pc_utc)
        if self._last_time is not None and moment < self._last_time:
            # 読み直しは「時刻の順」で操作と読み出しを並べ直す。順が崩れた記録は、
            # 読み直すと別の置き場所に振り分けられる。
            raise SessionError(f"時刻が戻っています: {pc_utc}")
        self._last_time = moment


def now_utc() -> str:
    """いまの UTC（µ 秒まで）。CLI が読み出しと操作の両方に使う**同じ時計**。"""
    return format_utc(datetime.now(timezone.utc))


# --- 読み直し ----------------------------------------------------------------


@dataclass(frozen=True)
class Replay:
    samples: list[RxSample]
    stats: ReadStats
    foreign: int


def replay(directory: str | Path, dialect: Dialect | None = None) -> Replay:
    """生のバイト列と操作の記録から、受信サンプルを**作り直す**。

    操作は「その時刻以前の読み出し」の前に当てる＝ライブでは、操作が記録されて
    から次の読み出しが来る。
    """
    dialect = dialect or load_dialect()
    header = read_header(directory)
    events = list(read_events(directory))

    def on_start(rx_config: Message, tx_config: Message, pc_utc: str) -> SessionHeader:
        for config, end in ((rx_config, header.rx), (tx_config, header.tx)):
            if to_radio_settings(config, end.radio.sensitivity_dbm, dialect) != end.radio:
                raise SessionError(f"生ログの最初の {end.role} の設定がヘッダと一致しません")
        return header

    processor = _Processor(on_start=on_start, dialect=dialect, tx_device=header.tx.device_id)
    samples: list[RxSample] = []
    pending = list(events)
    for pc_utc, chunk in read_raw(directory):
        moment = parse_utc(pc_utc)
        while pending and parse_utc(pending[0].pc_utc) <= moment:
            processor.apply(pending.pop(0))
        if processor.stopped:
            break
        got, _changed = processor.process(chunk, pc_utc)
        samples += got
    processor.stream.close()
    return Replay(samples=samples, stats=processor.stream.stats, foreign=processor.foreign)
