"""
apps/tracer/cli.py
==================
RadioSim Tracer（仮称）の**コマンドライン**（phase 1・増分4）。リポジトリの直下から:

    python -m apps.tracer.cli template 雛形.json      … ヘッダの雛形を書き出す
    python -m apps.tracer.cli record --template 雛形.json --port COM5
                                                      … RX を繋いで測る
    python -m apps.tracer.cli export セッション 出力.csv
                                                      … 本体のバッチ CSV へ書き出す
    python -m apps.tracer.cli bench run --port-a COM3 --port-b COM4 --root 測定データ
                                                      … 段0 の机上試験（2 台・両方向）
    python -m apps.tracer.cli bench analyze 机上試験 --ref ref.json --calib-dir 校正
                                                      … 判定と、個体ごとの校正ファイル
    python -m apps.tracer.cli cont --port COM3 --seconds 60 --ref ref.json
                                                      … 連続送信で TX の出力を測る（段0b）

**測っている間のキー**（`record`）
    m … アンテナを動かし始める（次に据えるまでの受信は窓に入れない）
    p … 次の置き場所に据えた（置き場所の番号が 1 つ進む）
    q … 終える（Ctrl+C でも同じ。終了の時刻が記録される）

⚠️ **GUI は作らない**（段2 で運用してから判断する＝§6.1-5C）。ここは入口を並べるだけで、
記録の中身は `recorder.py`、窓の集計は `aggregate.py` が持つ。
⚠️ シリアルポートには pyserial を使う（`requirements-tracer.txt`）。**本体の依存には
入れない**＝本体の配布物に要らないものを混ぜない。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess  # nosec B404 — 固定の git 呼び出しだけ（刻印のコミットを取る）
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apps.tracer.aggregate import (
    aggregate,
    body_tx_power_notice,
    censored_fraction,
    link_lost_total,
    write_batch_csv,
)
from apps.tracer.bench import STAGES, BenchError, BenchRunner, default_plan
from apps.tracer.bench_analysis import (
    analyze,
    read_ref,
    ref_template,
    render_markdown,
    write_calibrations,
    write_report,
)
from apps.tracer.calib import DeviceCalibration, read_device_calibration
from apps.tracer.mavlink.dialect import load_dialect
from apps.tracer.mavlink.reader import FrameStream, mac
from apps.tracer.recorder import Recorder, check_template, header_template, now_utc, replay
from apps.tracer.session import (
    Event,
    SessionError,
    read_events,
    read_header,
    read_raw,
    read_rx_samples,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WAIT_HINT_S = 5.0          # 設定が届かないときに案内を出すまでの秒数
_STATUS_EVERY_S = 1.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m apps.tracer.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("template", help="ヘッダの雛形（JSON）を書き出す")
    p.add_argument("path", type=Path)

    p = sub.add_parser("record", help="RX を繋いで測る")
    p.add_argument("--template", type=Path, required=True)
    p.add_argument("--port", required=True, help="RX の COM ポート（例: COM5）")
    p.add_argument("--baud", type=int, default=115200)
    # 既定を置かない＝作業ディレクトリ（公開リポジトリの直下になりがち）に
    # 測定データが溜まるのを防ぐ。
    p.add_argument("--root", type=Path, required=True,
                   help="セッションのフォルダを作る場所")
    p.add_argument("--calib", type=Path,
                   help="個体ごとの校正ファイルのフォルダ（bench analyze --calib-dir で作る）")

    p = sub.add_parser("export", help="セッションを本体のバッチ CSV へ書き出す")
    p.add_argument("session", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--recover", action="store_true",
                   help="終了の記録が無いセッション（途中で落ちた）を、最後に読めた時刻で"
                        "終えたものとして扱う")

    p = sub.add_parser("bench", help="段0 の机上試験")
    bench = p.add_subparsers(dest="bench_command", required=True)
    q = bench.add_parser("plan", help="計画の雛形（JSON）を書き出す")
    q.add_argument("path", type=Path)
    q = bench.add_parser("ref", help="段0b の実測値を書く雛形（JSON）を書き出す")
    q.add_argument("path", type=Path)
    q = bench.add_parser("run", help="2 台をつないで測る")
    q.add_argument("--port-a", required=True)
    q.add_argument("--port-b", required=True)
    q.add_argument("--baud", type=int, default=115200)
    q.add_argument("--root", type=Path, required=True, help="机上試験のフォルダを作る場所")
    q.add_argument("--plan", type=Path, help="計画（省略すると既定）")
    q.add_argument("--only", help=f"測る段をカンマで（{','.join(STAGES)}）")
    q = bench.add_parser("analyze", help="判定する（測り直さずに何度でも）")
    q.add_argument("directory", type=Path)
    q.add_argument("--ref", type=Path, help="段0b の実測値")
    q.add_argument("--calib-dir", type=Path, help="個体ごとの校正ファイルを書くフォルダ")

    p = sub.add_parser("cont", help="連続送信で TX の出力を測る（段0b）")
    p.add_argument("--port", required=True)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--seconds", type=int, default=60)
    p.add_argument("--ref", type=Path, required=True, help="結果を足す ref（無ければ作る）")
    p.add_argument("--reading", choices=("avg", "burst"), default="avg",
                   help="測定器の読みの種類（avg＝平均電力・burst＝バースト中の電力）")
    p.add_argument("--pad-db", type=float, default=0.0,
                   help="SMA 端から測定器までの間に入れた減衰（dB）")

    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            return _template(args.path)
        if args.command == "record":
            return _record(args)
        if args.command == "bench":
            return _bench(args)
        if args.command == "cont":
            return _cont(args)
        return _export(args)
    except SessionError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2


# --- template ----------------------------------------------------------------


def _template(path: Path) -> int:
    if path.exists():
        print(f"エラー: 既にあります（上書きしません）: {path}", file=sys.stderr)
        return 2
    path.write_text(
        json.dumps(header_template(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"雛形を書き出しました: {path}")
    print("null の欄を埋めてください。rx.radio は受信感度だけ（残りは RX から届きます）。"
          "TX の無線設定は書きません（TX が空中で送り、RX が中継します）。")
    print("校正値は書きません。record --calib で、個体ごとの校正ファイル（bench analyze "
          "--calib-dir で作ったもの）から個体 ID で読んで写します。")
    return 0


def _load_template(
    path: Path, calib_dir: Path | None = None
) -> tuple[dict[str, Any], dict[str, DeviceCalibration] | None]:
    try:
        template = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SessionError(f"雛形がありません: {path}") from e
    except json.JSONDecodeError as e:
        raise SessionError(f"雛形が JSON として読めません: {path}（{e}）") from e
    calibrations = None
    if calib_dir is not None:
        # 雛形で校正値を書いていない端点だけ、個体 ID で校正ファイルを引く。
        calibrations = {}
        for role in ("tx", "rx"):
            end = template.get(role) or {}
            device = end.get("device_id")
            if "calibration" not in end and isinstance(device, str):
                calibrations[device.lower()] = read_device_calibration(calib_dir, device)
    check_template(template, calibrations)
    return template, calibrations


# --- record ------------------------------------------------------------------


def _record(args: argparse.Namespace) -> int:
    # 現場で気づくと測れないので、繋ぐ前に
    template, calibrations = _load_template(args.template, args.calib)
    recorder = Recorder(args.root, template, software_commit=software_commit(),
                        calibrations=calibrations)
    serial = _import_serial()
    keys = _Keyboard()
    try:
        opened = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as e:
        raise SessionError(f"ポートを開けません: {args.port}（{e}）") from e
    print("RX と TX の設定を待っています…（q で中止）")
    started_wait = time.monotonic()
    hinted = False
    last_status = 0.0
    with opened as port:
        try:
            while True:
                chunk = port.read(max(1, port.in_waiting))
                recorder.feed(chunk, now_utc())
                if recorder.state == "stopped":
                    break                                 # 設定が変わった
                if recorder.state == "waiting" and not hinted \
                        and time.monotonic() - started_wait > _WAIT_HINT_S:
                    # RX も TX も設定を 1 秒ごとに送る（増分5）＝届かないのは配線か
                    # ポート・ファームの側（TX なら電波の側）。
                    if "rx" in recorder.waiting_for:
                        print("RX の設定が届きません。ポートと RX の電源、RX の役割"
                              "（role rx）を確かめてください。")
                    else:
                        print("TX の設定が届きません（RX は動いています）。TX の電源・"
                              "チャネル・個体 ID（雛形の tx.device_id）を確かめてください。")
                    hinted = True
                key = keys.poll()
                if key == "q":
                    break
                if key == "m" and recorder.state == "placed":
                    recorder.move(now_utc())
                    print("\n移動中（据えたら p）")
                elif key == "p" and recorder.state == "moving":
                    slot = recorder.place(now_utc())
                    print(f"\n置き場所 {slot} に据えました")
                if time.monotonic() - last_status >= _STATUS_EVERY_S:
                    _print_status(recorder)
                    last_status = time.monotonic()
        except KeyboardInterrupt:
            pass
        finally:
            recorder.stop(now_utc())
    print()
    if recorder.directory is None:
        print("設定が届かないまま終えました（セッションは作っていません）。")
        return 1
    if recorder.config_changed:
        print("RX か TX の設定が途中で変わったので終えました。続けるなら新しいセッションで"
              "測ってください。")
    print(f"セッション: {recorder.directory}（受信 {recorder.samples_written} 件）")
    _print_read_stats(recorder.stats, recorder.foreign)
    return 0


def _print_status(recorder: Recorder) -> None:
    state = {"waiting": "設定待ち", "placed": "据え置き", "moving": "移動中"}.get(
        recorder.state, recorder.state
    )
    slot = "" if recorder.slot is None or recorder.slot < 0 else f" 置き場所 {recorder.slot}"
    print(
        f"\r{state}{slot}  受信 {recorder.samples_written} 件"
        f"  CRC 不一致 {recorder.stats.crc_errors}  別の組 {recorder.foreign}   ",
        end="",
        flush=True,
    )


def software_commit() -> str:
    """刻印に入れるコミット。**作業ツリーが汚れていたら `-dirty` を付ける**＝
    コミットの名前だけでは、実際に動いたコードを後から再現できない。"""
    try:
        head = subprocess.run(  # nosec B603,B607 — 固定の git 呼び出し
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(  # nosec B603,B607 — 固定の git 呼び出し
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=_REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        raise SessionError(
            f"コミットを取れません（刻印に要ります）: {e}"
        ) from e
    return head + ("-dirty" if dirty else "")


def _import_serial() -> Any:
    try:
        import serial  # type: ignore[import-not-found]
    except ImportError as e:
        raise SessionError(
            "pyserial が入っていません: python -m pip install -r requirements-tracer.txt"
        ) from e
    return serial


class _Keyboard:
    """押されたキーを待たずに 1 つ返す（Windows のコンソール）。"""

    def __init__(self) -> None:
        try:
            import msvcrt
        except ImportError as e:
            raise SessionError("キー操作は Windows のコンソールでだけ使えます") from e
        self._msvcrt = msvcrt

    def poll(self) -> str | None:
        if not self._msvcrt.kbhit():
            return None
        return self._msvcrt.getwch().lower()


# --- export ------------------------------------------------------------------


def _export(args: argparse.Namespace) -> int:
    directory: Path = args.session
    header = read_header(directory)
    events = list(read_events(directory))
    samples = list(read_rx_samples(directory))
    if not events or events[-1].kind != "stop":
        if not args.recover:
            raise SessionError(
                "終了の記録がありません（測定が途中で落ちた）。最後に読めた時刻で"
                "終えたものとして扱うなら --recover を付けてください"
            )
        last = _last_read_time(directory)
        print(f"注意: 終了の記録が無いので、最後に読めた時刻 {last} で終えたものとして扱います")
        events.append(Event(last, "stop"))
    windows = aggregate(header, samples, events)
    write_batch_csv(args.out, header, windows)

    censored = sum(1 for w in windows if w.censored)
    print(f"書き出しました: {args.out}")
    print(body_tx_power_notice(header))
    print(f"窓 {len(windows)} 個・打ち切り {censored} 個（{censored_fraction(windows):.0%}）")
    # 電波で届いたのに PC までの間で落ちた分（打ち切りには数えていない）。窓に振れた分が
    # 総数より少なければ、残りは電波の欠けと混ざっていてどの窓の分か分からなかった。
    lost_on_link = sum(1 for w in windows if w.lost_on_link)
    print(
        f"受信機から PC までの間で落ちたサンプル {link_lost_total(samples)} 件"
        f"（窓に振った {sum(w.link_lost for w in windows)} 件・全部落ちた窓 {lost_on_link} 個）"
    )
    for slot in sorted({w.spatial_slot for w in windows}):
        members = [w for w in windows if w.spatial_slot == slot]
        cut = sum(1 for w in members if w.censored)
        print(f"  置き場所 {slot}: 窓 {len(members)} 個・打ち切り {cut} 個")

    again = replay(directory)
    _print_read_stats(again.stats, again.foreign)
    if again.samples != samples:
        # samples.csv が生ログと食い違う＝どちらかが壊れている。窓はもう書いたが、
        # そのまま使ってよい状態ではない。
        print(
            "警告: samples.csv と、生ログからの読み直しが一致しません"
            f"（{len(samples)} 件 / {len(again.samples)} 件）",
            file=sys.stderr,
        )
        return 1
    return 0


def _last_read_time(directory: Path) -> str:
    last = None
    for pc_utc, _chunk in read_raw(directory):
        last = pc_utc
    if last is None:
        raise SessionError("生ログが空なので、終えた時刻を決められません")
    return last


def _print_read_stats(stats: Any, foreign: int) -> None:
    """読み取りの健全性。**UART で落ちた分は、電波で届かなかった分と見分けが付かない**
    ので、打ち切りを読む前に必ず見る数（しきい値は受け入れ試験で決める）。"""
    print(
        f"UART: フレーム {stats.frames}・CRC 不一致 {stats.crc_errors}"
        f"（{stats.error_fraction:.2%}）・読み飛ばし {stats.skipped_bytes} バイト"
        f"・末尾の切れ {stats.truncated_tail} バイト・別の組のサンプル {foreign}"
    )


# --- bench -------------------------------------------------------------------


def _bench(args: argparse.Namespace) -> int:
    command = args.bench_command
    if command in ("plan", "ref"):
        path: Path = args.path
        if path.exists():
            print(f"エラー: 既にあります（上書きしません）: {path}", file=sys.stderr)
            return 2
        payload = default_plan() if command == "plan" else ref_template()
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        print(f"書き出しました: {path}")
        return 0
    if command == "analyze":
        ref = read_ref(args.ref) if args.ref else None
        report = analyze(args.directory, ref)
        j, m = write_report(args.directory, report)
        print(render_markdown(report))
        print(f"書き出しました: {m}・{j}")
        if args.calib_dir:
            for path in write_calibrations(report, args.calib_dir):
                print(f"校正ファイル: {path}")
        return 0
    plan = default_plan()
    if args.plan:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
    stages = tuple(s.strip() for s in args.only.split(",")) if args.only else STAGES
    serial = _import_serial()
    ports = {
        "a": _ReopeningPort(serial, args.port_a, args.baud),
        "b": _ReopeningPort(serial, args.port_b, args.baud),
    }
    runner = BenchRunner(
        args.root, ports, plan, clock=now_utc, prompt=_prompt, say=print,
        software_commit=software_commit(),
    )
    print(f"机上試験: {runner.directory}")
    try:
        runner.run(stages)
    except KeyboardInterrupt:
        print("\n中断しました（ここまでの記録は残っています）")
        return 1
    except BenchError as e:
        print(f"\n止まりました: {e}（ここまでの記録は残っています）", file=sys.stderr)
        return 2
    finally:
        for port in ports.values():
            port.close()
    print(f"終わりました。判定: python -m apps.tracer.cli bench analyze {runner.directory}")
    return 0


def _prompt(message: str) -> str:
    return input("\n" + message + " ")


class _ReopeningPort:
    """**機器の再起動を跨いで使えるポート。** 設定を変えると機器が再起動する
    （`role`・`power`・`channel`）。USB が一度切れる環境では読み書きが例外になるので、
    閉じて開き直す（開き直せるまでは空の読みを返す）。

    ⚠️ ポートを閉じると機器はリセットされる（README）が、切れたポートを閉じても
    失うものは無い。"""

    REOPEN_EVERY_S = 0.5

    def __init__(self, serial: Any, name: str, baud: int):
        self._serial = serial
        self._name = name
        self._baud = baud
        self._port: Any = None
        self._next_try = 0.0
        self._open(first=True)

    def _open(self, first: bool = False) -> None:
        try:
            self._port = self._serial.Serial(self._name, self._baud, timeout=0.1)
        except self._serial.SerialException as e:
            if first:
                raise SessionError(f"ポートを開けません: {self._name}（{e}）") from e
            self._port = None
            self._next_try = time.monotonic() + self.REOPEN_EVERY_S

    def _lost(self) -> None:
        try:
            self._port.close()
        except Exception:  # nosec B110 — 切れたポートの後始末（失敗しても失うものが無い）
            pass
        self._port = None
        self._next_try = time.monotonic() + self.REOPEN_EVERY_S

    def _ready(self) -> bool:
        if self._port is None and time.monotonic() >= self._next_try:
            self._open()
        if self._port is None:
            time.sleep(0.05)
        return self._port is not None

    @property
    def in_waiting(self) -> int:
        if not self._ready():
            return 0
        try:
            return int(self._port.in_waiting)
        except (self._serial.SerialException, OSError):
            self._lost()
            return 0

    def read(self, size: int) -> bytes:
        if not self._ready():
            return b""
        try:
            return bytes(self._port.read(size))
        except (self._serial.SerialException, OSError):
            self._lost()
            return b""

    def write(self, data: bytes) -> int:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self._ready():
                try:
                    return int(self._port.write(data))
                except (self._serial.SerialException, OSError):
                    self._lost()
        raise SessionError(f"ポートに書けません: {self._name}")

    def close(self) -> None:
        if self._port is not None:
            self._port.close()
            self._port = None


# --- cont --------------------------------------------------------------------

_CONT_LINE = re.compile(r"^CONT duty=(?P<duty>[0-9.]+) intervals=(?P<intervals>\d+) "
                        r"airtime_us=\d+ span_us=\d+ other_rate=(?P<other>\d+)")


def parse_cont_line(line: str) -> dict[str, float] | None:
    """ファームの連続送信の報告（`radio.c` の run_continuous）を読む。"""
    found = _CONT_LINE.match(line)
    if found is None:
        return None
    return {"duty": float(found["duty"]), "intervals": int(found["intervals"]),
            "other_rate": int(found["other"])}


def burst_dbm(reading_dbm: float, *, kind: str, duty: float, pad_db: float) -> float:
    """SMA 端のバースト中の電力。平均電力計の読みはデューティの分だけ低い。"""
    if not 0.0 < duty <= 1.0:
        raise SessionError(f"デューティが範囲の外です: {duty}")
    value = reading_dbm + pad_db
    if kind == "avg":
        value -= 10 * math.log10(duty)
    return value


def _cont(args: argparse.Namespace) -> int:
    if not 1 <= args.seconds <= 600:
        raise SessionError("--seconds は 1〜600 です")
    serial = _import_serial()
    port = _ReopeningPort(serial, args.port, args.baud)
    dialect = load_dialect()
    stream = FrameStream(dialect=dialect)
    configs: list[Any] = []
    lines: list[str] = []

    def pump(seconds: float) -> None:
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            chunk = port.read(max(1, port.in_waiting))
            configs.extend(m for m in stream.feed(chunk) if m.name == "TRACER_CONFIG")
            lines.extend(stream.take_lines())

    def role(m: Any) -> str:
        return dialect.label("TRACER_ROLE", m.fields["role"])

    try:
        port.write(b"show\n")
        pump(2.5)
        # RX は近くの TX の設定を中継する＝役割が rx の設定があれば、それが自分で、
        # tx の設定は他の機器のもの（取り違えると他の個体の出力として記録される）。
        own_rx = [m for m in configs if role(m) == "rx"]
        if own_rx:
            own = mac(own_rx[-1].fields["device_id"])
            print("TX に切り替えます（role tx）")
            configs.clear()
            port.write(b"role tx\n")
            pump(6.0)
            mine = [m for m in configs if role(m) == "tx" and mac(m.fields["device_id"]) == own]
        else:
            mine = [m for m in configs if role(m) == "tx"]
        if not mine or len({mac(m.fields["device_id"]) for m in mine}) != 1:
            raise SessionError("TX の設定が届きません（ポート・電源・ファームを確かめてください）")
        config = mine[-1]
        device = mac(config.fields["device_id"])
        power = config.fields["tx_power_cdbm"]
        channel = config.fields["channel"]
        print(f"個体 {device}・設定 {power / 100:g} dBm・チャネル {channel}")
        lines.clear()
        port.write(f"cont {args.seconds}\n".encode("ascii"))
        last: dict[str, float] | None = None
        until = time.monotonic() + args.seconds + 10
        ended = False
        while time.monotonic() < until and not ended:
            pump(0.2)
            for line in lines:
                if line.startswith("ERR"):
                    raise SessionError(f"機器が断りました: {line}")
                parsed = parse_cont_line(line)
                if parsed is not None:
                    last = parsed
                    print(f"\r連続送信中  デューティ {parsed['duty']:.4f}"
                          f"（補正 {-10 * math.log10(parsed['duty']):+.3f} dB）"
                          f"  他のレート {parsed['other_rate']}   ", end="", flush=True)
                ended = ended or line.startswith("CONT END")
            lines.clear()
    finally:
        port.close()
    print()
    if last is None:
        raise SessionError("連続送信の報告が届きませんでした")
    if last["other_rate"]:
        raise SessionError("1 Mbps 以外で出たフレームがありました（デューティを使えません）")
    reading = float(_prompt(f"測定器の読み（dBm・{args.reading}）:"))
    instrument = _prompt("測定器の名前（空でも可）:").strip()
    value = burst_dbm(reading, kind=args.reading, duty=last["duty"], pad_db=args.pad_db)
    ref = read_ref(args.ref) if args.ref.exists() else ref_template()
    ref.setdefault("tx_output", []).append({
        "device_id": device, "power_cdbm": power, "channel": channel,
        "dbm": round(value, 3), "measured_on": datetime.now(timezone.utc).date().isoformat(),
        "instrument": instrument, "reading_kind": args.reading, "reading_dbm": reading,
        "pad_db": args.pad_db, "duty": last["duty"],
    })
    args.ref.write_text(json.dumps(ref, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"SMA 端の出力 {value:.3f} dBm を {args.ref} に足しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
