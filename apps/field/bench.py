"""
apps/field/bench.py
====================
**ステージ0 の机上試験を回す**（Field phase 1・増分6）。2 台（A・B）を USB で PC に
つないだまま、役割をコマンド（`role tx` / `role rx`）で入れ替えながら、両方向
（A→B・B→A）を同じアッテネータのステップで測る。

    python -m apps.field.cli bench run --port-a COM3 --port-b COM4 --root 測定データ

**人がするのはアッテネータのつまみと Enter だけ**（2026-09-19 ユーザー決定＝手動の
ステップアッテネータ）。それ以外（役割・チャネル・送信電力の切替、ステップごとの記録、
細かい掃引の範囲・電力掃引の減衰量の選択）はここが決める。

**残すのは生のバイト列とステップの記録だけ**（判定は `bench_analysis.py`）＝ステージ0b で
アッテネータやケーブルの実測値が分かったら、**測り直さずに**判定をやり直せる。

    bench-<開始 UTC>/
      bench.json      … 開始時に 1 回書く（2 台の個体 ID・ファームの版・計画・刻印）
      steps.csv       … 測ったステップ（追記のみ）。どの時刻にどちらが送り、減衰量はいくつか
      a/uart.bin …    … A の USB の生のバイト列と、受けた時刻の索引（追記のみ）
      b/uart.bin …    … B の同じもの

⚠️ **ここはシリアルポートもキーボードも知らない**（`recorder.py` と同じ方針）＝
ポート（`read`/`write`/`in_waiting`）・時計・問いかけは呼び手が渡す。
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from apps.field.mavlink.dialect import Dialect, load_dialect
from apps.field.mavlink.reader import FrameStream, Message, mac
from apps.field.session import (
    RAW_FILE,
    RAW_INDEX_COLUMNS,
    RAW_INDEX_FILE,
    SessionError,
    append_raw,
    parse_utc,
)

BENCH_FORMAT = 1
BENCH_FILE = "bench.json"
STEPS_FILE = "steps.csv"
UNITS = ("a", "b")

# probe＝動作確認（`BenchRunner.probe`）のステップ。机上試験のフォルダには現れない。
STEP_KINDS = ("leak", "warmup", "level", "power", "channel", "probe")
PURPOSES = ("bench", "probe")          # フォルダの名前の頭と bench.json の purpose
PROBE_FILE = "probe.json"
STEP_COLUMNS = (
    "step",           # 通し番号
    "kind",           # STEP_KINDS
    "label",          # 人が読む補足（掃引の名前など）
    "tx_unit",        # a / b
    "rx_unit",
    "tx_device",
    "rx_device",
    "tx_config_id",   # このステップで TX に効いていた設定番号
    "rx_config_id",
    "power_cdbm",     # TX の設定（機器が読み戻した値）
    "channel",
    "atten_db",       # ステップアッテネータの合計（公称）。終端器のステップは空
    "start_utc",
    "end_utc",
)

# ステップの頭で待つ時間＝役割の入れ替え（再起動）の直後に落ち着くまで。
SETTLE_S = 2.0
# ステップの終わりの後に読み続ける時間＝終わり際に送った番号の受信が USB を通ってくるまで。
TAIL_S = 0.5
# 設定の変更（再起動）を待つ上限。
CONFIG_WAIT_S = 15.0
# 起動直後に 2 台の自分の個体 ID を見分けるまで読む時間（設定は 1 秒ごとに届く）。
IDENTIFY_S = 2.5


class BenchError(SessionError):
    """机上試験を続けられないとき（機器が応えない・設定が効かない）。"""


class BenchAbort(BenchError):
    """作業者が止めた。"""


class Port(Protocol):
    @property
    def in_waiting(self) -> int: ...

    def read(self, size: int) -> bytes: ...

    def write(self, data: bytes) -> int | None: ...


# --- 計画 --------------------------------------------------------------------


def default_plan() -> dict[str, Any]:
    """既定の計画（`bench plan` で書き出して直せる）。

    所要時間の目安は 1 時間強（人が触るのはつまみ 30 回ほど）。
    """
    return {
        "channel": 1,
        "power_dbm": 15,
        # 固定アッテネータ（30 dB ×2）とケーブルの**公称**。ステージ0b の実測値は判定の側
        # （`bench analyze --ref`）で差し替える＝ここを直して測り直す必要はない。
        "fixed_loss_db": 60.0,
        "leak": {"duration_s": 30, "power_dbm": 20},
        "warmup": {
            "atten_db": 40, "chunk_s": 60, "max_chunks": 30,
            "stable_chunks": 5, "stable_db": 0.2,
        },
        "coarse": {"atten_db": list(range(0, 120, 10)), "duration_s": 20},
        # 粗い掃引で受信率が落ち始めたステップ k の前後を 1 dB 刻みで。
        "fine": {"below_db": 9, "above_db": 6, "duration_s": 30},
        # 受信率が落ち始めるステップ k から margin_db 以上下げた減衰量（10 dB 単位）で。
        # ⚠️ power_dbm（15）を必ず含める＝校正ファイルの他の電力の出力は、パワー計で
        # 測った 15 dBm の点からの相対差で導く（含まないと導けない）。
        "power": {"dbm": [3, 5, 7, 9, 11, 13, 15, 17, 19, 20], "duration_s": 20,
                  "margin_db": 25},
        "channels": {"list": [1, 7, 13], "duration_s": 20},
    }


STAGES = ("leak", "warmup", "coarse", "fine", "power", "channels")


# --- 1 台ぶんの読み手 ----------------------------------------------------------


@dataclass
class _Unit:
    name: str
    port: Port
    directory: Path
    dialect: Dialect
    stream: FrameStream = field(init=False)
    device_id: str | None = None
    config: Message | None = None           # 自分の最新の設定
    lines: list[str] = field(default_factory=list)
    configs_seen: list[Message] = field(default_factory=list)   # 見分ける前の分
    tx_echoes: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    rx_samples: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.stream = FrameStream(dialect=self.dialect)

    @property
    def role(self) -> str | None:
        if self.config is None:
            return None
        return self.dialect.label("RADIOSIM_FIELD_ROLE", self.config.fields["role"])


def create_raw_log(directory: Path) -> None:
    """`append_raw` の書き先（生のバイト列と索引）を空で作る。"""
    directory.mkdir(parents=True)
    (directory / RAW_FILE).touch()
    with (directory / RAW_INDEX_FILE).open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(RAW_INDEX_COLUMNS)


# --- 進め手 ------------------------------------------------------------------


@dataclass(frozen=True)
class StepCount:
    """ステップの**その場の**数え（判定は生ログから `bench_analysis` がやり直す）。"""

    sent: int
    received: int
    rssi_mean: float | None

    @property
    def rate(self) -> float:
        return 0.0 if self.sent == 0 else self.received / self.sent


# 動作確認で「受かっている」とみなす受信率。機器と配線が動いているかを見るだけの
# 目安で、ステージ0 の打ち切りのしきい値とは別物（そちらは作業者がステージ0 の結果から決める）。
PROBE_MIN_RATE = 0.9


@dataclass(frozen=True)
class ProbeLink:
    """動作確認の 1 方向ぶん。"""

    tx_unit: str
    rx_unit: str
    sent: int
    received: int
    usb_lost: int                   # USB で落ちたサンプル（sample_seq の飛び）
    rssi_min: int | None            # 受信レベルの生値
    rssi_max: int | None
    noise_floor: int | None         # 雑音フロアの生値の中央値

    @property
    def rate(self) -> float:
        """**電波の**受信率＝USB で落ちた分は電波では届いているので足す（USB の不調を
        電波の不調と取り違えて、アッテネータや配線を疑わせないため）。"""
        return 0.0 if self.sent == 0 else (self.received + self.usb_lost) / self.sent

    @property
    def air_ok(self) -> bool:
        return self.sent > 0 and self.rate >= PROBE_MIN_RATE

    @property
    def ok(self) -> bool:
        return self.air_ok and self.usb_lost == 0


def usb_lost(samples: list[dict[str, Any]]) -> int:
    """USB で落ちたサンプルの数＝`sample_seq` の飛びの合計。

    電波で届かなかったパケットには番号が振られないので、ここには数えられない。
    番号が戻ったところ（RX の再起動・送信機の表の追い出し）は数えない。
    """
    # 届いた順のまま＝並べ替えると、戻る前と後の番号が混ざって飛びに見える。
    seqs = [f["sample_seq"] for f in samples]
    return sum(b - a - 1 for a, b in zip(seqs, seqs[1:]) if b > a + 1)


def judge_firmware(
    firmware_version: str, software_commit: str,
    same_source: Callable[[str], bool | None],
) -> tuple[bool, str]:
    """ファームの版がこのリポジトリのコミットと合っているか。

    `same_source(版)` は、その版と今のコミットでファームのソースが同じか
    （同じ＝True・違う＝False・その版がリポジトリに無い＝None）。コミットが違っても
    ファームのソースが同じなら、焼き直さなくてよい。
    """
    if firmware_version.endswith("-dirty"):
        return False, "ファームが未コミットの変更を含んだままビルドされています。焼き直してください"
    if software_commit.endswith("-dirty"):
        return False, "この PC の作業ツリーに変更があります（記録に -dirty が付きます）"
    if software_commit.startswith(firmware_version):
        return True, "リポジトリのコミットと一致しています"
    same = same_source(firmware_version)
    if same is None:
        return False, (f"ファームの版 {firmware_version} がこのリポジトリにありません"
                       "（ファームのコミットが push されていないかもしれません）")
    if same:
        return True, (f"コミットは違いますが、ファームのソースは {firmware_version} と"
                      "同じです")
    return False, (f"ファームのソースが {firmware_version} から変わっています。"
                   "今のコミットで焼き直してください")


def write_probe(directory: Path, payload: dict[str, Any]) -> Path:
    path = directory / PROBE_FILE
    _write_json(path, payload)
    return path


class BenchRunner:
    """2 台を操り、ステップを記録する。

    `clock()` は UTC の ISO 8601（`recorder.now_utc` と同じ時計）、`prompt(文)` は
    作業者に頼んで Enter を待つ（`q` で止める）、`say(文)` は表示するだけ。
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        ports: Mapping[str, Port],
        plan: dict[str, Any],
        *,
        clock: Callable[[], str],
        prompt: Callable[[str], str],
        say: Callable[[str], None],
        software_commit: str,
        dialect: Dialect | None = None,
        purpose: str = "bench",
    ):
        if set(ports) != set(UNITS):
            raise BenchError("ポートは a と b の 2 つです")
        if purpose not in PURPOSES:
            raise BenchError(f"知らない用途です: {purpose}")
        if not software_commit:
            raise BenchError("ソフトウェアのコミットが空です（刻印の 1 項目）")
        self._clock = clock
        self._prompt = prompt
        self._say = say
        self.plan = plan
        self._dialect = dialect or load_dialect()
        started = clock()
        name = f"{purpose}-" + parse_utc(started).strftime("%Y%m%dT%H%M%SZ")
        self.directory = Path(root) / name
        self._purpose = purpose
        if self.directory.exists():
            raise BenchError(f"同じ名前のフォルダが既にあります: {self.directory}")
        self.directory.mkdir(parents=True)
        self._started = started
        self._software_commit = software_commit
        self.units = {}
        for u in UNITS:
            create_raw_log(self.directory / u)
            self.units[u] = _Unit(u, ports[u], self.directory / u, self._dialect)
        with (self.directory / STEPS_FILE).open("w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(STEP_COLUMNS)
        self._step = 0
        self.results: dict[str, list[tuple[dict[str, Any], StepCount]]] = {}

    # 読み ---------------------------------------------------------------------

    def poll(self) -> None:
        """両方のポートから読めた分を 1 回ずつ読む。"""
        for unit in self.units.values():
            waiting = unit.port.in_waiting
            chunk = unit.port.read(max(1, waiting))
            if not chunk:
                continue
            now = self._clock()
            append_raw(unit.directory, chunk, now)
            for message in unit.stream.feed(chunk):
                self._take(unit, message, now)
            unit.lines += unit.stream.take_lines()

    def _take(self, unit: _Unit, message: Message, now: str) -> None:
        if message.name == "RADIOSIM_FIELD_CONFIG":
            if unit.device_id is None:
                unit.configs_seen.append(message)
            elif mac(message.fields["device_id"]) == unit.device_id:
                unit.config = message
        elif message.name == "RADIOSIM_FIELD_TX_PACKET":
            unit.tx_echoes.append((now, message.fields))
        elif message.name == "RADIOSIM_FIELD_RX_SAMPLE":
            unit.rx_samples.append((now, message.fields))

    def _elapsed(self, since: str) -> float:
        return (parse_utc(self._clock()) - parse_utc(since)).total_seconds()

    def _read_for(self, seconds: float) -> None:
        since = self._clock()
        while self._elapsed(since) < seconds:
            self.poll()

    def _wait(self, done: Callable[[], bool], seconds: float, what: str) -> None:
        since = self._clock()
        while not done():
            if self._elapsed(since) > seconds:
                raise BenchError(what)
            self.poll()
            self._check_errors()

    def _check_errors(self) -> None:
        for unit in self.units.values():
            for line in unit.lines:
                if line.startswith("ERR 空中のレート") or line.startswith("ERR 自己検査"):
                    raise BenchError(f"{unit.name.upper()} が止まっています: {line}")

    # 見分け ---------------------------------------------------------------------

    def connect(self) -> None:
        """2 台の自分の個体 ID を見分け、`bench.json` を書く。

        RX は空中で受けた TX の設定も中継するので、RX のポートには**他の機器の
        設定**も流れる。中継されるのは役割が tx の設定だけ＝役割が rx の設定が
        見えたら、それが自分。rx が 1 つも無ければ自分は TX（TX は中継しない）。
        """
        for unit in self.units.values():
            unit.port.write(b"show\n")
        self._read_for(IDENTIFY_S)
        for unit in self.units.values():
            seen = unit.configs_seen
            if not seen:
                raise BenchError(
                    f"{unit.name.upper()} の設定が届きません（ポート・電源・ファームを"
                    "確かめてください）"
                )
            rx = [m for m in seen if self._label_role(m) == "rx"]
            own = rx if rx else seen
            ids = {mac(m.fields["device_id"]) for m in own}
            if len(ids) != 1:
                raise BenchError(f"{unit.name.upper()} の個体 ID を 1 つに決められません: {ids}")
            unit.device_id = ids.pop()
            unit.config = own[-1]
            unit.configs_seen = []
        a, b = self.units["a"], self.units["b"]
        if a.device_id == b.device_id:
            raise BenchError("A と B が同じ個体です（同じ機器を 2 つのポートで見ている）")
        versions = {u.name: u.config.fields["firmware_version"] for u in self.units.values()
                    if u.config is not None}
        if len(set(versions.values())) != 1:
            raise BenchError(f"A と B のファームの版が違います: {versions}")
        payload = {
            "format": BENCH_FORMAT,
            "purpose": self._purpose,
            "started_utc": self._started,
            "software_commit": self._software_commit,
            "firmware_version": versions["a"],
            "units": {u.name: {"device_id": u.device_id} for u in self.units.values()},
            "plan": self.plan,
        }
        _write_json(self.directory / BENCH_FILE, payload)
        self._say(f"A={a.device_id}  B={b.device_id}  ファーム {versions['a']}")

    def _label_role(self, message: Message) -> str:
        return self._dialect.label("RADIOSIM_FIELD_ROLE", message.fields["role"])

    # 設定 -----------------------------------------------------------------------

    def _command(self, unit: _Unit, line: str) -> str:
        unit.lines = []
        unit.port.write((line + "\n").encode("ascii"))
        reply: list[str] = []

        def answered() -> bool:
            reply[:] = [x for x in unit.lines if x.startswith(("OK", "ERR"))]
            return bool(reply)

        self._wait(answered, CONFIG_WAIT_S, f"{unit.name.upper()} が「{line}」に応えません")
        if reply[0].startswith("ERR"):
            raise BenchError(f"{unit.name.upper()} が「{line}」を断りました: {reply[0]}")
        return reply[0]

    def _set(self, unit: _Unit, line: str, check: Callable[[dict[str, Any]], bool]) -> None:
        """1 つ変えて、変わった自分の設定が届くまで待つ（変えると再起動する）。"""
        reply = self._command(unit, line)
        found = re.search(r"config_id=(\d+)", reply)
        if found is None:
            return                                    # 「変わっていません」
        target = int(found.group(1))

        def arrived() -> bool:
            c = unit.config
            return c is not None and c.fields["config_id"] == target and check(c.fields)

        self._wait(arrived, CONFIG_WAIT_S,
                   f"{unit.name.upper()} の新しい設定（config_id={target}）が届きません")

    def configure(self, unit_name: str, *, role: str, channel: int, power_dbm: float) -> None:
        unit = self.units[unit_name]
        assert unit.config is not None
        f = unit.config.fields
        cdbm = round(power_dbm * 100)
        if f["channel"] != channel:
            self._set(unit, f"channel {channel}", lambda g: g["channel"] == channel)
        if abs(unit.config.fields["tx_power_cdbm"] - cdbm) >= 25:
            # 機器が読み戻す値は 0.25 dB 刻み。刻みより小さい差は「同じ」とみなす。
            self._set(unit, f"power {power_dbm:g}",
                      lambda g: abs(g["tx_power_cdbm"] - cdbm) < 25)
        if unit.role != role:
            code = 0 if role == "tx" else 1
            self._set(unit, f"role {role}", lambda g: g["role"] == code)

    def arrange(self, tx: str, *, power_dbm: float, channel: int) -> None:
        """tx の機器を TX、もう 1 台を RX にする（両方とも同じチャネル・電力）。"""
        rx = _other(tx)
        # RX を先に＝TX を先に切り替えると、2 台とも TX の瞬間ができる（害は無いが
        # 両方の送信が重なる）。
        self.configure(rx, role="rx", channel=channel, power_dbm=power_dbm)
        self.configure(tx, role="tx", channel=channel, power_dbm=power_dbm)

    # ステップ -------------------------------------------------------------------------

    def measure(
        self, kind: str, tx: str, *, duration_s: float, atten_db: int | None,
        power_dbm: float, channel: int, label: str = "",
    ) -> StepCount:
        if kind not in STEP_KINDS:
            raise BenchError(f"知らないステップの種類です: {kind}")
        self.arrange(tx, power_dbm=power_dbm, channel=channel)
        self._read_for(SETTLE_S)
        t_unit, r_unit = self.units[tx], self.units[_other(tx)]
        assert t_unit.config is not None and r_unit.config is not None
        start = self._clock()
        self._read_for(duration_s)
        end = self._clock()
        self._read_for(TAIL_S)
        self._step += 1
        row = {
            "step": self._step, "kind": kind, "label": label,
            "tx_unit": tx, "rx_unit": _other(tx),
            "tx_device": t_unit.device_id, "rx_device": r_unit.device_id,
            "tx_config_id": t_unit.config.fields["config_id"],
            "rx_config_id": r_unit.config.fields["config_id"],
            "power_cdbm": t_unit.config.fields["tx_power_cdbm"],
            "channel": t_unit.config.fields["channel"],
            "atten_db": "" if atten_db is None else atten_db,
            "start_utc": start, "end_utc": end,
        }
        with (self.directory / STEPS_FILE).open("a", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow([row[c] for c in STEP_COLUMNS])
            f.flush()
            os.fsync(f.fileno())
        count = self._count(row)
        self.results.setdefault(kind, []).append((row, count))
        level = "" if count.rssi_mean is None else f"  RSSI 平均 {count.rssi_mean:.2f}"
        self._say(
            f"  {tx.upper()}→{_other(tx).upper()} {label}: "
            f"{count.received}/{count.sent}（{count.rate:.0%}）{level}"
        )
        return count

    def _step_samples(self, row: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
        """ステップの間に TX が送った数と、そのうち RX が受けたサンプル。"""
        t_unit, r_unit = self.units[row["tx_unit"]], self.units[row["rx_unit"]]
        start, end = parse_utc(row["start_utc"]), parse_utc(row["end_utc"])
        seqs = [
            f["seq"] for when, f in t_unit.tx_echoes
            if start <= parse_utc(when) <= end and f["config_id"] == row["tx_config_id"]
        ]
        if not seqs:
            return 0, []
        low, high = min(seqs), max(seqs)
        got = [
            f for _when, f in r_unit.rx_samples
            if mac(f["tx_id"]) == row["tx_device"]
            and f["tx_config_id"] == row["tx_config_id"]
            and low <= f["seq"] <= high
        ]
        return high - low + 1, got

    def _count(self, row: dict[str, Any]) -> StepCount:
        sent, got = self._step_samples(row)
        levels = [f["rssi_raw"] for f in got]
        mean = sum(levels) / len(levels) if levels else None
        return StepCount(sent, len(levels), mean)

    # 動作確認 ---------------------------------------------------------------------

    def probe(self, *, duration_s: float, power_dbm: float, channel: int) -> list[ProbeLink]:
        """**動作確認**＝2 台を見分け、両方向を短く測る。人には何も頼まない＝
        対話の無いところ（別の PC の Claude のシェルなど）からも実行できる。

        アッテネータは今の位置のまま（減衰量は記録しない）。判定は機器と配線が
        動いているかだけで、ステージ0 のしきい値には使わない。
        """
        self.connect()
        links = []
        for tx in UNITS:
            self.measure("probe", tx, duration_s=duration_s, atten_db=None,
                         power_dbm=power_dbm, channel=channel, label="動作確認")
            row, _count = self.results["probe"][-1]
            links.append(self._probe_link(row))
        return links

    def _probe_link(self, row: dict[str, Any]) -> ProbeLink:
        sent, got = self._step_samples(row)
        levels = [f["rssi_raw"] for f in got]
        noise = sorted(f["noise_floor_raw"] for f in got)
        return ProbeLink(
            tx_unit=row["tx_unit"], rx_unit=row["rx_unit"],
            sent=sent, received=len(got), usb_lost=usb_lost(got),
            rssi_min=min(levels) if levels else None,
            rssi_max=max(levels) if levels else None,
            noise_floor=noise[len(noise) // 2] if noise else None,
        )

    def _ask(self, message: str) -> None:
        answer = self._prompt(message)
        if answer.strip().lower() == "q":
            raise BenchAbort("作業者が止めました")

    def ask_atten(self, total_db: int) -> None:
        if not 0 <= total_db <= 121:
            raise BenchError(f"ステップアッテネータで作れない減衰量です: {total_db} dB")
        tens = min(total_db // 10 * 10, 110)
        ones = total_db - tens
        self._ask(
            f"ステップアッテネータを合計 {total_db} dB に"
            f"（10 dB 刻み {tens}・1 dB 刻み {ones}）して Enter（q で中止）"
        )

    # ステージの組み ---------------------------------------------------------------------

    def run(self, stages: tuple[str, ...] = STAGES) -> None:
        unknown = [s for s in stages if s not in STAGES]
        if unknown:
            raise BenchError(f"知らないステージです: {unknown}（{', '.join(STAGES)}）")
        self.connect()
        for stage in STAGES:
            if stage in stages:
                self._say(f"--- {stage} ---")
                getattr(self, f"_stage_{stage}")()

    def _both(self, kind: str, *, first: str, **kwargs: Any) -> None:
        """両方向を測る。**first から**＝直前のステップと同じ向きから始めれば入れ替えが 1 回で済む。"""
        for tx in (first, _other(first)):
            self.measure(kind, tx, **kwargs)

    def _last_tx(self) -> str:
        """いま TX の機器（両方向を測った直後は、2 つ目の向きの TX）。"""
        for name, unit in self.units.items():
            if unit.role == "tx":
                return name
        return "a"

    def _stage_leak(self) -> None:
        p = self.plan["leak"]
        self._ask("A と B の SMA 端から配線を外し、それぞれに 50 Ω 終端器を付けて Enter"
                  "（q で中止）")
        self._both("leak", first=self._last_tx(), duration_s=p["duration_s"], atten_db=None,
                   power_dbm=p["power_dbm"], channel=self.plan["channel"], label="終端")
        self._ask("終端器を外し、配線を元に戻して Enter（q で中止）")

    def _stage_warmup(self) -> None:
        p = self.plan["warmup"]
        self.ask_atten(p["atten_db"])
        means: dict[str, list[float]] = {"a": [], "b": []}
        for chunk in range(p["max_chunks"]):
            tx = self._last_tx() if chunk == 0 else _other(self._last_tx())
            count = self.measure(
                "warmup", tx, duration_s=p["chunk_s"], atten_db=p["atten_db"],
                power_dbm=self.plan["power_dbm"], channel=self.plan["channel"],
                label=f"{chunk + 1} 回目",
            )
            if count.rssi_mean is not None:
                means[tx].append(count.rssi_mean)
            if all(_stable(means[u], p["stable_chunks"], p["stable_db"]) for u in UNITS):
                self._say(f"  落ち着きました（{chunk + 1} 回目）")
                return
        self._say("  ⚠️ 上限まで温めても落ち着きませんでした（判定で見てください）")

    def _stage_coarse(self) -> None:
        p = self.plan["coarse"]
        first = self._last_tx()
        for atten in p["atten_db"]:
            self.ask_atten(atten)
            self._both("level", first=first, duration_s=p["duration_s"], atten_db=atten,
                       power_dbm=self.plan["power_dbm"], channel=self.plan["channel"],
                       label=f"粗 {atten} dB")
            first = self._last_tx()

    def knee(self) -> int:
        """粗い掃引で、**どちらかの向きの受信率が 99% を下回った最小の減衰量**。"""
        levels = self.results.get("level", [])
        dropped = [
            row["atten_db"] for row, count in levels
            if row["label"].startswith("粗") and count.rate < 0.99
        ]
        if not dropped:
            raise BenchError(
                "粗い掃引で受信率が落ちませんでした（減衰量が足りない＝固定アッテネータを"
                "足すか、漏れを疑ってください）"
            )
        return min(dropped)

    def _stage_fine(self) -> None:
        p = self.plan["fine"]
        k = self.knee()
        first = self._last_tx()
        for atten in range(max(0, k - p["below_db"]), min(121, k + p["above_db"]) + 1):
            self.ask_atten(atten)
            self._both("level", first=first, duration_s=p["duration_s"], atten_db=atten,
                       power_dbm=self.plan["power_dbm"], channel=self.plan["channel"],
                       label=f"細 {atten} dB")
            first = self._last_tx()

    def power_atten(self) -> int:
        """電力掃引の減衰量＝落ち始めるステップから margin_db 以上下げた、10 dB 単位の値。"""
        margin = self.plan["power"]["margin_db"]
        return max(0, (self.knee() - margin) // 10 * 10)

    def _stage_power(self) -> None:
        p = self.plan["power"]
        atten = self.power_atten()
        self.ask_atten(atten)
        first = self._last_tx()
        for dbm in p["dbm"]:
            self._both("power", first=first, duration_s=p["duration_s"], atten_db=atten,
                       power_dbm=dbm, channel=self.plan["channel"], label=f"{dbm:g} dBm")
            first = self._last_tx()

    def _stage_channels(self) -> None:
        p = self.plan["channels"]
        # 電力掃引と同じ減衰量（電力掃引をしていなければ選び直して頼む）。
        atten = self.power_atten()
        if "power" not in self.results:
            self.ask_atten(atten)
        first = self._last_tx()
        for ch in p["list"]:
            self._both("channel", first=first, duration_s=p["duration_s"], atten_db=atten,
                       power_dbm=self.plan["power_dbm"], channel=ch, label=f"ch {ch}")
            first = self._last_tx()


def _other(unit: str) -> str:
    return "b" if unit == "a" else "a"


def _stable(means: list[float], chunks: int, width_db: float) -> bool:
    """直近 chunks 回の平均が width_db の幅に収まったか。"""
    if len(means) < chunks:
        return False
    recent = means[-chunks:]
    return max(recent) - min(recent) <= width_db


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# --- 読み戻し ------------------------------------------------------------------


def read_bench(directory: str | os.PathLike[str]) -> dict[str, Any]:
    path = Path(directory) / BENCH_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise BenchError(f"{BENCH_FILE} がありません（2 台を見分ける前に止まった）: {path}") from e
    if payload.get("format") != BENCH_FORMAT:
        raise BenchError(f"知らない机上試験の形式です: {payload.get('format')}")
    return payload


def read_steps(directory: str | os.PathLike[str]) -> list[dict[str, Any]]:
    path = Path(directory) / STEPS_FILE
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != STEP_COLUMNS:
            raise BenchError(f"ステップの記録の列が違います: {path}")
        rows = []
        for r in reader:
            row: dict[str, Any] = dict(r)
            for key in ("step", "tx_config_id", "rx_config_id", "power_cdbm", "channel"):
                row[key] = int(row[key])
            row["atten_db"] = None if row["atten_db"] == "" else int(row["atten_db"])
            if row["kind"] not in STEP_KINDS:
                raise BenchError(f"知らないステップの種類です: {row['kind']}")
            rows.append(row)
    return rows


def started_on(bench: dict[str, Any]) -> str:
    """机上試験の日付（校正日）。"""
    return parse_utc(bench["started_utc"]).date().isoformat()
