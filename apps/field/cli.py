"""
apps/field/cli.py
==================
RadioSim Fieldの**コマンドライン**（phase 1・増分4）。リポジトリの直下から:

    python -m apps.field.cli template 雛形.json      … ヘッダの雛形を書き出す
    python -m apps.field.cli record --template 雛形.json --port COM5
                                                      … RX を繋いで測る
    python -m apps.field.cli export セッション 出力.csv
                                                      … 本体のバッチ CSV へ書き出す
    python -m apps.field.cli probe --port-a COM3 --port-b COM4 --root 測定データ
                                                      … 2 台の動作確認（人の操作なし）
    python -m apps.field.cli bench run --port-a COM3 --port-b COM4 --root 測定データ
                                                      … 段0 の机上試験（2 台・両方向）
    python -m apps.field.cli bench analyze 机上試験 --ref ref.json --calib-dir 校正
                                                      … 判定と、個体ごとの校正ファイル
    python -m apps.field.cli cont --port COM3 --seconds 60 --ref ref.json
                                                      … 連続送信で TX の出力を測る（段0b）
    python -m apps.field.cli monitor --port COM4     … 受信を眺めるだけ（何も保存しない）
    python -m apps.field.cli settings --port COM4 --role tx --power 10
                                                      … 機器の設定を表示する・変える

**測っている間のキー**（`record`）
    m … アンテナを動かし始める（次に据えるまでの受信は窓に入れない）
    p … 次の置き場所に据えた（置き場所の番号が 1 つ進む）
    q … 終える（Ctrl+C でも同じ。終了の時刻が記録される）

⚠️ **GUI は作らない**（段2 で運用してから判断する＝§6.1-5C）。ここは入口を並べるだけで、
記録の中身は `recorder.py`、窓の集計は `aggregate.py` が持つ。
⚠️ シリアルポートには pyserial を使う（`requirements-field.txt`）。**本体の依存には
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
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apps.field.aggregate import (
    aggregate,
    body_tx_power_notice,
    censored_fraction,
    link_lost_total,
    write_batch_csv,
)
from apps.field.bench import (
    PROBE_MIN_RATE,
    STAGES,
    BenchError,
    BenchRunner,
    ProbeLink,
    default_plan,
    judge_firmware,
    write_probe,
)
from apps.field.bench_analysis import (
    analyze,
    read_ref,
    ref_template,
    render_markdown,
    write_calibrations,
    write_report,
)
from apps.field.calib import DeviceCalibration, read_device_calibration
from apps.field.mavlink.dialect import load_dialect
from apps.field.mavlink.reader import FrameStream, ReadStats, mac
from apps.field.recorder import Recorder, check_template, header_template, now_utc, replay
from apps.field.session import (
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
    parser = argparse.ArgumentParser(prog="python -m apps.field.cli")
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

    p = sub.add_parser("probe", help="2 台の動作確認（両方向を短く測る・人の操作なし）")
    p.add_argument("--port-a", required=True)
    p.add_argument("--port-b", required=True)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--root", type=Path, required=True, help="動作確認のフォルダを作る場所")
    p.add_argument("--seconds", type=float, default=10.0, help="1 方向を測る秒数")
    p.add_argument("--power", type=float, default=default_plan()["power_dbm"],
                   help="送信電力（dBm）")
    p.add_argument("--channel", type=int, default=default_plan()["channel"])

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

    p = sub.add_parser("settings",
                       help="機器の設定を表示する・変える（変えたら読み戻して確かめる）")
    p.add_argument("--port", required=True, help="機器の COM ポート（例: COM4）")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--role", choices=("tx", "rx"))
    p.add_argument("--channel", type=int, help="1〜13")
    p.add_argument("--interval", type=int, help="送信の間隔（20〜10000 ms）")
    p.add_argument("--power", type=float, help="送信電力（2〜20 dBm・0.25 刻み）")

    p = sub.add_parser("monitor", help="受信を眺めるだけ（何も保存しない・q で終了）")
    p.add_argument("--port", required=True, help="見る機器の COM ポート（例: COM4）")
    p.add_argument("--baud", type=int, default=115200)

    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            return _template(args.path)
        if args.command == "record":
            return _record(args)
        if args.command == "probe":
            return _probe(args)
        if args.command == "bench":
            return _bench(args)
        if args.command == "cont":
            return _cont(args)
        if args.command == "monitor":
            return _monitor(args)
        if args.command == "settings":
            return _settings(args)
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
            "pyserial が入っていません: python -m pip install -r requirements-field.txt"
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
    print(f"終わりました。判定: python -m apps.field.cli bench analyze {runner.directory}")
    return 0


# --- probe -------------------------------------------------------------------


def _probe(args: argparse.Namespace) -> int:
    serial = _import_serial()
    commit = software_commit()
    ports = {
        "a": _ReopeningPort(serial, args.port_a, args.baud),
        "b": _ReopeningPort(serial, args.port_b, args.baud),
    }
    try:
        runner = BenchRunner(
            args.root, ports, {"probe": {"seconds": args.seconds, "power_dbm": args.power,
                                         "channel": args.channel}},
            clock=now_utc, prompt=_no_prompt, say=print, software_commit=commit,
            purpose="probe",
        )
        print(f"動作確認: {runner.directory}")
        return run_probe(runner, seconds=args.seconds, power_dbm=args.power,
                         channel=args.channel, software_commit=commit,
                         same_source=_same_firmware_source, say=print)
    except BenchError as e:
        print(f"\n止まりました: {e}", file=sys.stderr)
        return 2
    finally:
        for port in ports.values():
            port.close()


def run_probe(
    runner: BenchRunner, *, seconds: float, power_dbm: float, channel: int,
    software_commit: str, same_source: Any, say: Any,
) -> int:
    """動作確認を回して判定を表示し、`probe.json` に残す。全部合格なら 0、でなければ 1。"""
    links = runner.probe(duration_s=seconds, power_dbm=power_dbm, channel=channel)
    firmware = runner.units["a"].config.fields["firmware_version"]   # type: ignore[union-attr]
    fw_ok, fw_note = judge_firmware(firmware, software_commit, same_source)
    crc = {u: unit.stream.stats.crc_errors for u, unit in runner.units.items()}
    crc_ok = not any(crc.values())
    say("\n判定")
    say(f"  {_mark(fw_ok)} ファームの版 {firmware}: {fw_note}")
    for link in links:
        say(f"  {_mark(link.ok)} {_probe_line(link)}")
    say(f"  {_mark(crc_ok)} USB の読み取り: "
        + "・".join(f"{u.upper()} の CRC 不一致 {n}" for u, n in crc.items()))
    passed = fw_ok and crc_ok and all(link.ok for link in links)
    if passed:
        say("すべて合格です。")
    else:
        say("合格しなかった項目があります。")
        if not all(link.air_ok for link in links):
            say(f"  受信率の目安は {PROBE_MIN_RATE:.0%} 以上です（動作確認のための目安で、"
                "段0 のしきい値ではありません）。アッテネータを 0 dB にするか、"
                "配線と U.FL の接続を確かめてください。")
        if any(link.usb_lost for link in links) or not crc_ok:
            say("  USB の読み取りで落ちたデータがあります。USB ケーブルを替えるか、"
                "ハブを通さずに PC へ直接つないでください。")
    path = write_probe(runner.directory, {
        "passed": passed,
        "software_commit": software_commit,
        "firmware": {"version": firmware, "ok": fw_ok, "note": fw_note},
        "units": {u: unit.device_id for u, unit in runner.units.items()},
        "settings": {"seconds": seconds, "power_dbm": power_dbm, "channel": channel},
        "min_rate": PROBE_MIN_RATE,
        "links": [dict(asdict(link), rate=link.rate, air_ok=link.air_ok, ok=link.ok)
                  for link in links],
        "crc_errors": crc,
    })
    say(f"記録: {path}")
    return 0 if passed else 1


def _probe_line(link: ProbeLink) -> str:
    level = ("受信なし" if link.rssi_min is None
             else f"受信レベル（生値）{link.rssi_min}〜{link.rssi_max}・雑音フロア {link.noise_floor}")
    return (f"{link.tx_unit.upper()}→{link.rx_unit.upper()}: 電波で受信 "
            f"{link.received + link.usb_lost}/{link.sent}（{link.rate:.0%}）・"
            f"USB の欠け {link.usb_lost}・{level}")


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def _no_prompt(message: str) -> str:
    raise BenchError(f"動作確認では人に頼みません: {message}")


def _same_firmware_source(version: str) -> bool | None:
    """その版と今のコミットで、ファームのソースが同じか（その版が無ければ None）。"""
    if not re.fullmatch(r"[0-9a-f]{7,40}", version):
        return None                                   # git に渡すのはコミットの形だけ
    result = subprocess.run(  # nosec B603,B607 — 固定の git 呼び出し（版は 16 進だけ）
        ["git", "diff", "--quiet", version, "HEAD", "--", "apps/field/firmware"],
        cwd=_REPO_ROOT, capture_output=True,
    )
    return {0: True, 1: False}.get(result.returncode)


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
            configs.extend(m for m in stream.feed(chunk) if m.name == "RADIOSIM_FIELD_CONFIG")
            lines.extend(stream.take_lines())

    def role(m: Any) -> str:
        return dialect.label("RADIOSIM_FIELD_ROLE", m.fields["role"])

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


# --- settings ----------------------------------------------------------------

_SETTINGS_WAIT_S = 15.0       # 返答・再起動後の設定を待つ上限
_SETTINGS_IDENTIFY_S = 2.5    # 自分の設定を見分けるまで読む時間（設定は 1 秒ごとに届く）
# ファームが止まっているときに 1 秒ごとに流す行＝コマンドへの返答ではない。
_STUCK = ("ERR 空中のレート", "ERR 自己検査")


def settings_changes(args: argparse.Namespace) -> list[tuple[str, str, Any]]:
    """引数を `(コマンド, 欄, 期待する値)` の並びへ。**送る前に**範囲を確かめる
    （ファームの `settings.c` と同じ範囲）。役割は最後＝TX にしてから他を変えると、
    変えるたびに測定の送信が途切れる。"""
    out: list[tuple[str, str, Any]] = []
    if args.channel is not None:
        if not 1 <= args.channel <= 13:
            raise SessionError("--channel は 1〜13 です")
        out.append((f"channel {args.channel}", "channel", args.channel))
    if args.interval is not None:
        if not 20 <= args.interval <= 10000:
            raise SessionError("--interval は 20〜10000 ms です")
        out.append((f"interval {args.interval}", "tx_interval_ms", args.interval))
    if args.power is not None:
        if not 2.0 <= args.power <= 20.0 or args.power * 4 != int(args.power * 4):
            raise SessionError("--power は 2〜20 dBm・0.25 刻みです")
        out.append((f"power {args.power:g}", "tx_power_cdbm", round(args.power * 100)))
    if args.role is not None:
        out.append((f"role {args.role}", "role", 0 if args.role == "tx" else 1))
    return out


def describe_settings(fields: dict[str, Any], dialect: Any) -> str:
    return (f"{dialect.label('RADIOSIM_FIELD_ROLE', fields['role']).upper()} "
            f"{mac(fields['device_id'])}  チャネル {fields['channel']}・"
            f"間隔 {fields['tx_interval_ms']} ms・出力 {fields['tx_power_cdbm'] / 100:g} dBm・"
            f"ファーム {fields['firmware_version']}・config_id {fields['config_id']}")


def apply_settings(
    port: Any, changes: list[tuple[str, str, Any]], *, say: Any,
    clock: Any = time.monotonic,
) -> dict[str, Any]:
    """機器の今の設定を読み、`changes` を 1 つずつ送り、**再起動後に届いた設定で
    変わったことを確かめる**。最後の設定（欄の辞書）を返す。

    RX のポートには中継された TX の設定も流れる＝役割 rx の設定があればそれが
    自分、無ければ自分は TX（`bench.connect` と同じ規則）。以後は個体 ID で選ぶ。
    """
    dialect = load_dialect()
    stream = FrameStream(dialect=dialect)
    configs: list[Any] = []
    lines: list[str] = []

    def pump() -> None:
        configs.extend(m for m in stream.feed(port.read(max(1, port.in_waiting)))
                       if m.name == "RADIOSIM_FIELD_CONFIG")
        for line in stream.take_lines():
            if line.startswith(_STUCK):
                raise SessionError(f"機器が止まっています: {line}")
            lines.append(line)

    def wait(done: Any, what: str, at_least: float = 0.0) -> None:
        since = clock()
        while not (done() and clock() - since >= at_least):
            if clock() - since > _SETTINGS_WAIT_S:
                raise SessionError(what)
            pump()

    port.write(b"show\n")
    wait(lambda: bool(configs),
         "設定が届きません（ポート・電源・ファームを確かめてください）",
         at_least=_SETTINGS_IDENTIFY_S)
    rx = [m for m in configs if dialect.label("RADIOSIM_FIELD_ROLE", m.fields["role"]) == "rx"]
    ids = {mac(m.fields["device_id"]) for m in (rx or configs)}
    if len(ids) != 1:
        raise SessionError(f"このポートの機器を 1 台に決められません: {sorted(ids)}")
    own = ids.pop()

    def latest() -> dict[str, Any]:
        return [m for m in configs if mac(m.fields["device_id"]) == own][-1].fields

    current = latest()
    say(f"現在: {describe_settings(current, dialect)}")
    for line, field, value in changes:
        if current[field] == value:
            say(f"  {line}: 既にこの値です")
            continue
        lines.clear()
        port.write((line + "\n").encode("ascii"))
        replies: list[str] = []

        def answered() -> bool:
            replies[:] = [x for x in lines if x.startswith(("OK", "ERR"))]
            return bool(replies)

        wait(answered, f"「{line}」に返答がありません")
        if replies[0].startswith("ERR"):
            raise SessionError(f"機器が「{line}」を断りました: {replies[0]}")
        found = re.search(r"config_id=(\d+)", replies[0])
        if found is None:
            raise SessionError(f"「{line}」の返答が想定と違います: {replies[0]}")
        target = int(found.group(1))

        def arrived() -> bool:
            mine = [m for m in configs if mac(m.fields["device_id"]) == own]
            return bool(mine) and mine[-1].fields["config_id"] == target

        wait(arrived, f"再起動後の設定（config_id={target}）が届きません")
        after = latest()
        if after[field] != value:
            raise SessionError(f"「{line}」を送りましたが、機器の値は {after[field]} のままです")
        say(f"  {line}: 変わりました（config_id {target}）")
        current = after
    if changes:
        say(f"変更後: {describe_settings(current, dialect)}")
    return current


def _settings(args: argparse.Namespace) -> int:
    changes = settings_changes(args)            # 範囲の外なら、ポートを開く前に止める
    serial = _import_serial()
    port = _ReopeningPort(serial, args.port, args.baud)
    try:
        apply_settings(port, changes, say=print)
    finally:
        port.close()
    return 0


# --- monitor -----------------------------------------------------------------


class MonitorTally:
    """受信を**送信機ごと**に区切りの間だけ数える（`monitor` の表示用・何も書かない）。

    欠けは 2 つに分ける＝`seq` の飛びは電波と USB の両方で起き、`sample_seq` の飛びは
    受信機から PC までの間だけで起きる（`radiosim_field.xml`）。差が電波の欠け。
    番号が戻ったところ（機器の再起動）は欠けに数えない。
    """

    def __init__(self, dialect: Any) -> None:
        self._dialect = dialect
        self._last: dict[str, tuple[int, int]] = {}      # tx → (seq, sample_seq)
        self._configs: set[tuple[str, int]] = set()
        self._reset()

    def _reset(self) -> None:
        self._rssi: dict[str, list[int]] = {}
        self._noise: dict[str, list[int]] = {}
        self._air_lost: dict[str, int] = {}
        self._usb_lost: dict[str, int] = {}
        self._sent: dict[str, int] = {}

    @property
    def configs_seen(self) -> bool:
        return bool(self._configs)

    def add(self, message: Any) -> str | None:
        """1 メッセージを数える。新しい設定なら、その知らせの行を返す。"""
        f = message.fields
        if message.name == "RADIOSIM_FIELD_CONFIG":
            device = mac(f["device_id"])
            if (device, f["config_id"]) in self._configs:
                return None
            self._configs.add((device, f["config_id"]))
            role = self._dialect.label("RADIOSIM_FIELD_ROLE", f["role"])
            power = f"出力 {f['tx_power_cdbm'] / 100:g} dBm・" if role == "tx" else ""
            return (f"設定 {role.upper()} {device}  チャネル {f['channel']}・"
                    f"{power}ファーム {f['firmware_version']}")
        if message.name == "RADIOSIM_FIELD_TX_PACKET":
            tx = mac(f["tx_id"])
            self._sent[tx] = self._sent.get(tx, 0) + 1
            return None
        if message.name != "RADIOSIM_FIELD_RX_SAMPLE":
            return None
        tx = mac(f["tx_id"])
        self._rssi.setdefault(tx, []).append(f["rssi_raw"])
        self._noise.setdefault(tx, []).append(f["noise_floor_raw"])
        seq, sample_seq = f["seq"], f["sample_seq"]
        last = self._last.get(tx)
        if last is not None and seq > last[0] and sample_seq > last[1]:
            gap = seq - last[0] - 1
            usb = sample_seq - last[1] - 1
            self._usb_lost[tx] = self._usb_lost.get(tx, 0) + usb
            self._air_lost[tx] = self._air_lost.get(tx, 0) + max(0, gap - usb)
        self._last[tx] = (seq, sample_seq)
        return None

    def lines(self) -> list[str]:
        """区切りの間の集計を 1 送信機 1 行で返し、数え直す。"""
        out = []
        for tx, levels in sorted(self._rssi.items()):
            noise = sorted(self._noise[tx])
            out.append(
                f"{tx}  受信 {len(levels)}  欠け 電波 {self._air_lost.get(tx, 0)}・"
                f"USB {self._usb_lost.get(tx, 0)}  受信レベル（生値）平均 "
                f"{sum(levels) / len(levels):.1f}（{min(levels)}〜{max(levels)}）  "
                f"雑音フロア {noise[len(noise) // 2]}"
            )
        for tx, sent in sorted(self._sent.items()):
            out.append(f"{tx}  送信 {sent}")
        self._reset()
        return out


def run_monitor(
    port: Any, *, say: Any, stop: Any, clock: Any = time.monotonic,
    stamp: Any = None, every_s: float = _STATUS_EVERY_S,
) -> ReadStats:
    """ポートを読み、`every_s` ごとに受信の集計を表示する。`stop()` が真で終える。

    ⛔ 受信レベルは**生値のまま**出す＝校正を当てないので、表示で dBm を名乗らない。
    """
    stamp = stamp or (lambda: datetime.now().strftime("%H:%M:%S"))
    dialect = load_dialect()
    stream = FrameStream(dialect=dialect)
    tally = MonitorTally(dialect)
    started = last = clock()
    hinted = False
    while not stop():
        for message in stream.feed(port.read(max(1, port.in_waiting))):
            note = tally.add(message)
            if note is not None:
                say(note)
        stream.take_lines()                     # 文字の行（起動表示など）は見せない
        now = clock()
        if not hinted and not tally.configs_seen and now - started > _WAIT_HINT_S:
            say("設定が届きません。ポート・機器の電源・ファームを確かめてください。")
            hinted = True
        if now - last >= every_s:
            rows = tally.lines() or ["受信なし"]
            crc = f"  CRC 不一致（累計）{stream.stats.crc_errors}"
            for row in rows:
                say(f"{stamp()}  {row}{crc}")
            last = now
    return stream.stats


def _monitor(args: argparse.Namespace) -> int:
    serial = _import_serial()
    port = _ReopeningPort(serial, args.port, args.baud)
    keys = _Keyboard()
    print(f"{args.port} を見ています（保存しません・q で終了）")
    try:
        run_monitor(port, say=print, stop=lambda: keys.poll() == "q")
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
    print("終えました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
