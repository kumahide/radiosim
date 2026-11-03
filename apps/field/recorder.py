"""
apps/field/recorder.py
=======================
**測りながら書く**（Field phase 1・増分4）。UART から届いたバイト列と作業者の
操作を受け取り、セッションのフォルダへ追記していく。

⚠️ **ここはシリアルポートもキーボードも知らない。** `feed(バイト列, 受けた時刻)` と
`place`/`move`/`stop` を呼ぶのは CLI（`apps/field/cli.py`）の仕事で、ここを
純粋に保つのは**測定の現場でしか試せない部分を最小にする**ため。

**流れ**
  1. 雛形（JSON）を読み、未記入が無いか先に確かめる（現場で気づくと測れない）
  2. `RADIOSIM_FIELD_CONFIG` を 2 つ待つ＝RX 自身の設定と、**TX が空中で送り RX が中継した
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

from apps.field.calib import DeviceCalibration, endpoint_calibration
from apps.field.mavlink.dialect import Dialect, load_dialect
from apps.field.mavlink.reader import (
    FrameStream,
    Message,
    ReadStats,
    mac,
    sample_band,
    to_radio_settings,
    to_rx_sample,
)
from apps.field.session import (
    BANDS,
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

    **校正値の欄も置かない**＝個体ごとの校正ファイル（`calib.py`・`record --calib`）から
    個体 ID で読んで写す（2026-09-19 ユーザー決定＝手で書き写す欄を無くす）。
    ⚠️ 手で `calibration` を書いた雛形も読める（その端点は校正ファイルを使わない）。
    """

    def endpoint(role: str) -> dict[str, Any]:
        return {
            "role": role,
            "measurement_config_id": None,
            "device_id": None,
            "feeder_loss_db": None,
            "antenna_gain_dbi": None,
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


def check_template(
    template: dict[str, Any], calibrations: dict[str, DeviceCalibration] | None = None
) -> None:
    """記録を始める前の検査。**未記入（`null`）と、書いてはいけない項目**を見る。

    校正値は端点ごとに**ちょうど 1 つの出どころ**＝雛形の `calibration` か、個体 ID で
    引いた校正ファイル（`calibrations`・キーは小文字の個体 ID）。両方あるとどちらが
    写ったか後から分からず、どちらも無ければ dBm に直せない。
    """
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
    for role in ("tx", "rx"):
        end = template.get(role, {})
        device = str(end.get("device_id", "")).lower()
        from_file = calibrations is not None and device in calibrations
        if "calibration" in end and from_file:
            raise SessionError(
                f"{role} の校正値が雛形と校正ファイルの両方にあります（どちらか 1 つにします）"
            )
        if "calibration" not in end and not from_file:
            raise SessionError(
                f"{role}（{device}）の校正値がありません＝校正ファイルのフォルダ（--calib）を"
                "指定してください"
            )


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
    calibrations: dict[str, DeviceCalibration] | None = None,
) -> SessionHeader:
    """雛形と、RX の設定・中継された TX の設定からヘッダを組む。

    校正ファイルから写す端点は、**動いている帯域の受信の校正と、TX の送信出力を動いて
    いる設定（帯域・電力・チャネル）の行から選ぶ**＝設定は TX が空中で送ってくるまで
    分からないので、ここで選ぶ。

    🔑 **RX の個体 ID は雛形と機器で一致させる。** 校正値は個体ごとの値なので、
    別の個体を挿したまま測ると、その個体に**別の個体の校正**が掛かった dBm が出る。
    TX も同じ（送信電力の校正が個体ごと）。
    """
    check_template(template, calibrations)
    if not software_commit:
        raise SessionError("ソフトウェアのコミットが空です（刻印の 1 項目）")
    if rx_config.name != "RADIOSIM_FIELD_CONFIG" or not _is_rx(rx_config, dialect):
        raise SessionError("RX の設定メッセージではありません")
    if tx_config.name != "RADIOSIM_FIELD_CONFIG" or _is_rx(tx_config, dialect):
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
    if tx_config.fields["block_ms"]:
        # 2 帯域を交互に送る TX（I-252）＝1 回の記録で帯域ごとにセッションが 2 つでき、
        # 同じ読み出しで始まると同じ名前になる。名前で帯域が分かるようにもしておく。
        band = (dialect or load_dialect()).label("RADIOSIM_FIELD_BAND", tx_config.fields["band"])
        payload["session_id"] += f"-{band}"
    payload["started_utc"] = started_utc
    payload["schema_version"] = SCHEMA_VERSION
    payload["provenance"]["software_commit"] = software_commit
    payload["rx"]["device_id"] = device
    # 機器から来る個体 ID は小文字。雛形の大文字をそのまま残すと、TX のサンプルが
    # 全部「別の組」としてウィンドウから外れる。
    payload["tx"]["device_id"] = tx_device
    payload["rx"]["radio"] = asdict(
        to_radio_settings(rx_config, template["rx"]["radio"]["sensitivity_dbm"], dialect)
    )
    payload["tx"]["radio"] = asdict(to_radio_settings(tx_config, None, dialect))
    for role, end_device in (("tx", tx_device), ("rx", device)):
        if "calibration" in template[role]:
            continue
        assert calibrations is not None               # check_template が見ている
        payload[role]["calibration"] = asdict(endpoint_calibration(
            calibrations[end_device], role,
            band=(dialect or load_dialect()).label("RADIOSIM_FIELD_BAND", tx_config.fields["band"]),
            power_cdbm=tx_config.fields["tx_power_cdbm"],
            channel=tx_config.fields["channel"],
        ))
    return header_from_dict(payload)


def _is_rx(config: Message, dialect: Dialect | None) -> bool:
    return (dialect or load_dialect()).label("RADIOSIM_FIELD_ROLE", config.fields["role"]) == "rx"


# --- 記録のコア（ライブと読み直しで同じものを通す） ----------------------------


@dataclass
class _Processor:
    """バイト列 → 受信サンプル（**1 つの帯域**）。**ライブも読み直しも、これ 1 つを通る。**

    `on_start` は、その帯域の RX の設定と（`tx_device` の）TX の設定が揃ったときにヘッダを
    返す関数（ライブ＝組んでセッションを作る／読み直し＝既存のヘッダを返す）。`None` を
    返したら、この帯域は始めない（ライブで組めなかった＝`Recorder.band_errors`）。揃う前は
    それぞれ**最後に届いたもの**を持っておく。`can_start(時刻)` が偽の間は、揃っても始めない
    （ライブ＝移動中は次の `place` まで待つ／読み直し＝ヘッダの開始時刻まで待つ）。

    🔑 **帯域で絞る**（I-252）＝2 帯域を同時に聞く RX は、両方の帯域の設定とサンプルを同じ
    ポートへ流す。帯域の欄が 0 のサンプル（古いファーム＝1 帯域だけを起こす版）は受ける。
    """

    on_start: Callable[[Message, Message, str], SessionHeader | None]
    dialect: Dialect
    tx_device: str                     # 待つ TX の個体 ID（小文字）。他の TX の設定は見ない
    band: str                          # BANDS のどれか
    can_start: Callable[[str], bool] = lambda _pc_utc: True
    stream: FrameStream = field(init=False)
    header: SessionHeader | None = None
    rx_config: Message | None = None   # セッションを作る前に届いた最後の設定
    tx_config: Message | None = None
    slot: int | None = None            # None＝まだ据えていない／終えた
    stopped: bool = False
    foreign: int = 0                   # ヘッダと違う送受信機のサンプル（ウィンドウに入れない）

    def __post_init__(self) -> None:
        self.stream = FrameStream(dialect=self.dialect)

    @property
    def ready(self) -> bool:
        """設定が揃い、まだ始めていない。"""
        return (self.header is None and not self.stopped
                and self.rx_config is not None and self.tx_config is not None)

    def try_start(self, pc_utc: str) -> None:
        if not self.ready or not self.can_start(pc_utc):
            return
        assert self.rx_config is not None and self.tx_config is not None
        self.header = self.on_start(self.rx_config, self.tx_config, pc_utc)
        if self.header is None:
            self.stopped = True

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
        self.try_start(pc_utc)
        for message in self.stream.feed(chunk):
            if message.name == "RADIOSIM_FIELD_CONFIG":
                if self.stopped:
                    continue
                if self.dialect.label("RADIOSIM_FIELD_BAND", message.fields["band"]) != self.band:
                    continue                  # もう片方の帯域の設定（その帯域の記録が見る）
                is_rx = _is_rx(message, self.dialect)
                if not is_rx and mac(message.fields["device_id"]) != self.tx_device:
                    continue                  # 近くの別の TX（中継されてきた）
                if self.header is None:
                    if is_rx:
                        self.rx_config = message
                    else:
                        self.tx_config = message
                    self.try_start(pc_utc)
                elif self._differs(message, is_rx):
                    return [], True
                continue
            if message.name != "RADIOSIM_FIELD_RX_SAMPLE" or self.header is None:
                continue
            if self.slot is None:
                continue
            if sample_band(message, self.dialect) not in (None, self.band):
                continue                      # もう片方の無線で受けた（別の組ではない）
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


@dataclass
class _Band:
    """1 つの帯域の記録（I-252＝2 帯域を交互に送る TX なら、1 回の記録で帯域ごとに 1 つ）。"""

    processor: _Processor
    directory: Path | None = None
    samples_written: int = 0


class Recorder:
    """測りながら書く。呼び手（CLI）は時刻を**同じ時計で、進む向きに**渡すこと。

    🔑 **帯域ごとにセッションを 1 つ**（I-252）。各帯域は、その帯域の RX と TX の設定が
    揃った所で始める。置き場所の番号は Recorder で 1 本＝両方のセッションで同じ番号が
    同じ場所（920 と 2.4 を後で並べるため）。後から始まる帯域は、据えていればいまの番号で
    始め、移動中なら次の `place` まで待つ。生のバイト列は各セッションへ全部（始まる前の
    分も）残し、操作は始まっている全部へ書く。どちらかの設定が変わったら両方を終える。
    """

    def __init__(
        self,
        root: str | Path,
        template: dict[str, Any],
        *,
        software_commit: str,
        dialect: Dialect | None = None,
        calibrations: dict[str, DeviceCalibration] | None = None,
    ):
        check_template(template, calibrations)
        self._root = Path(root)
        self._template = template
        self._calibrations = calibrations
        self._software_commit = software_commit
        self._dialect = dialect or load_dialect()
        tx_device = template["tx"]["device_id"].lower()
        self._bands: dict[str, _Band] = {
            band: _Band(_Processor(
                on_start=lambda rx, tx, t, band=band: self._start(band, rx, tx, t),
                dialect=self._dialect, tx_device=tx_device, band=band,
                can_start=lambda _t: not self._stopped and self._slot != MOVING_SLOT,
            ))
            for band in BANDS
        }
        self._pending_raw: list[tuple[str, bytes]] = []
        self._last_time: datetime | None = None
        self._slot: int | None = None       # None＝どの帯域もまだ始めていない
        self._next_slot = 0
        self._stopped = False
        self.config_changed = False
        self.band_errors: dict[str, str] = {}   # 始められなかった帯域 → 理由

    # 状態 --------------------------------------------------------------------

    def _started(self) -> list[_Band]:
        return [b for b in self._bands.values() if b.directory is not None]

    @property
    def directories(self) -> dict[str, Path]:
        """始まった帯域 → セッションのフォルダ。"""
        return {band: b.directory for band, b in self._bands.items() if b.directory is not None}

    @property
    def samples_by_band(self) -> dict[str, int]:
        return {band: b.samples_written for band, b in self._bands.items()
                if b.directory is not None}

    @property
    def directory(self) -> Path | None:
        """最初に始まったセッション（1 帯域の記録ならそれだけ）。"""
        started = self._started()
        return started[0].directory if started else None

    @property
    def header(self) -> SessionHeader | None:
        started = self._started()
        return started[0].processor.header if started else None

    @property
    def samples_written(self) -> int:
        return sum(b.samples_written for b in self._bands.values())

    @property
    def state(self) -> str:
        """`waiting`（設定待ち）／`placed`／`moving`／`stopped`。"""
        if self._stopped:
            return "stopped"
        if self._slot is None:
            return "waiting"
        return "moving" if self._slot == MOVING_SLOT else "placed"

    @property
    def slot(self) -> int | None:
        return self._slot

    @property
    def waiting_for(self) -> list[tuple[str | None, str]]:
        """まだ届いていない設定＝`(帯域, 役割)`（役割は `rx`／`tx`）。

        待つ帯域＝TX の設定が 2 帯域を交互に送る形（`block_ms > 0`）なら両方・1 帯域の形なら
        その帯域だけ。TX の設定がまだ無ければ、RX の設定が来た帯域（それも無ければ帯域は
        `None`＝分からない）。始まった帯域・始められなかった帯域は挙げない。
        """
        procs = {band: b.processor for band, b in self._bands.items()}
        tx_seen = {band for band, p in procs.items() if p.tx_config is not None}
        if any(p.tx_config is not None and p.tx_config.fields["block_ms"]
               for p in procs.values()):
            expected = set(BANDS)
        elif tx_seen:
            expected = tx_seen
        else:
            expected = {band for band, p in procs.items() if p.rx_config is not None}
        if not expected:
            return [(None, "rx"), (None, "tx")]
        out: list[tuple[str | None, str]] = []
        for band in BANDS:
            p = procs[band]
            if band not in expected or p.header is not None or p.stopped:
                continue
            out += [(band, role) for role, got in (("rx", p.rx_config), ("tx", p.tx_config))
                    if got is None]
        return out

    @property
    def stats(self) -> ReadStats:
        # どの帯域の読み手も同じバイト列を全部読む＝数えた値は同じ。
        return self._bands[BANDS[0]].processor.stream.stats

    @property
    def foreign(self) -> int:
        return sum(b.processor.foreign for b in self._bands.values())

    # 入力 --------------------------------------------------------------------

    def feed(self, chunk: bytes, pc_utc: str) -> None:
        """UART から読めた分を渡す（空でもよい）。"""
        self._advance(pc_utc)
        if self.state == "stopped":
            raise SessionError("終えたセッションには書けません")
        if not chunk:
            return
        started = self._started()
        if not started:
            self._pending_raw.append((pc_utc, chunk))
        for b in started:
            assert b.directory is not None
            append_raw(b.directory, chunk, pc_utc)
        results = {band: b.processor.process(chunk, pc_utc) for band, b in self._bands.items()}
        if any(changed for _, changed in results.values()):
            # どちらかの設定が変わった＝両方を終える。この読み出しのサンプルはどちらにも
            # 書かない（読み直しは終了の記録をこの読み出しの前に当てる）。
            self.config_changed = True
            self._stop_all(pc_utc)
            return
        for band, (samples, _) in results.items():
            if samples:
                b = self._bands[band]
                assert b.directory is not None
                append_rx_samples(b.directory, samples)
                b.samples_written += len(samples)

    def move(self, pc_utc: str) -> None:
        """アンテナを動かし始めた。"""
        self._advance(pc_utc)
        if self.state != "placed":
            raise SessionError(f"据えていない状態（{self.state}）からは動かせません")
        self._slot = MOVING_SLOT
        self._record(Event(pc_utc, "move"))

    def place(self, pc_utc: str) -> int:
        """次の置き場所に据えた。番号は 1 つずつ進む（使い回さない・帯域で共通）。"""
        self._advance(pc_utc)
        if self.state != "moving":
            raise SessionError(f"移動中でない状態（{self.state}）では据え直せません")
        slot = self._next_slot
        self._next_slot += 1
        self._slot = slot
        self._record(Event(pc_utc, "place", slot))
        for b in self._bands.values():
            b.processor.try_start(pc_utc)     # 移動中に設定が揃った帯域はここから
        return slot

    def stop(self, pc_utc: str) -> None:
        """測定を終える。設定待ちのまま終えたら、セッションは作られていない。"""
        self._advance(pc_utc)
        if self.state == "stopped":
            return
        self._stop_all(pc_utc)

    # 内部 --------------------------------------------------------------------

    def _start(
        self, band: str, rx_config: Message, tx_config: Message, pc_utc: str
    ) -> SessionHeader | None:
        try:
            header = build_header(
                self._template,
                rx_config,
                tx_config,
                started_utc=pc_utc,
                software_commit=self._software_commit,
                dialect=self._dialect,
                calibrations=self._calibrations,
            )
            directory = create_session(self._root, header)
        except SessionError as e:
            others = [b for k, b in self._bands.items()
                      if k != band and not b.processor.stopped]
            if not self._started() and not (tx_config.fields["block_ms"] and others):
                raise                     # 1 帯域の記録＝今までどおり止める
            # もう片方の帯域は測れている（測れ得る）＝その記録を落とさずに、この帯域だけ諦める。
            self.band_errors[band] = str(e)
            return None
        started = self._started()
        source = self._pending_raw if not started else list(read_raw(started[0].directory))
        for when, data in source:
            append_raw(directory, data, when)
        self._pending_raw = []
        b = self._bands[band]
        b.directory = directory
        if self._slot is None:
            self._slot = self._next_slot
            self._next_slot += 1
        event = Event(pc_utc, "place", self._slot)
        append_event(directory, event)
        b.processor.apply(event)
        return header

    def _record(self, event: Event) -> None:
        for b in self._started():
            assert b.directory is not None
            append_event(b.directory, event)
            b.processor.apply(event)

    def _stop_all(self, pc_utc: str) -> None:
        self._record(Event(pc_utc, "stop"))
        for b in self._bands.values():
            b.processor.stopped = True
            b.processor.stream.close()
        self._stopped = True

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

    started = parse_utc(header.started_utc)
    processor = _Processor(
        on_start=on_start, dialect=dialect, tx_device=header.tx.device_id,
        band=header.rx.radio.band,
        # ライブは移動中に揃った帯域を次の place で始める＝その時刻より前には始めない。
        can_start=lambda pc_utc: parse_utc(pc_utc) >= started,
    )
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
