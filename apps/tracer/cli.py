"""
apps/tracer/cli.py
==================
RadioSim Tracer（仮称）の**コマンドライン**（phase 1・増分4）。リポジトリの直下から:

    python -m apps.tracer.cli template 雛形.json      … ヘッダの雛形を書き出す
    python -m apps.tracer.cli record --template 雛形.json --port COM5
                                                      … RX を繋いで測る
    python -m apps.tracer.cli export セッション 出力.csv
                                                      … 本体のバッチ CSV へ書き出す

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
import subprocess  # nosec B404 — 固定の git 呼び出しだけ（刻印のコミットを取る）
import sys
import time
from pathlib import Path
from typing import Any

from apps.tracer.aggregate import aggregate, censored_fraction, write_batch_csv
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

    p = sub.add_parser("export", help="セッションを本体のバッチ CSV へ書き出す")
    p.add_argument("session", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--recover", action="store_true",
                   help="終了の記録が無いセッション（途中で落ちた）を、最後に読めた時刻で"
                        "終えたものとして扱う")

    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            return _template(args.path)
        if args.command == "record":
            return _record(args)
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
    print("null の欄を埋めてください。rx.radio は受信感度だけ（残りは RX から届きます）。")
    return 0


def _load_template(path: Path) -> dict[str, Any]:
    try:
        template = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SessionError(f"雛形がありません: {path}") from e
    except json.JSONDecodeError as e:
        raise SessionError(f"雛形が JSON として読めません: {path}（{e}）") from e
    check_template(template)
    return template


# --- record ------------------------------------------------------------------


def _record(args: argparse.Namespace) -> int:
    template = _load_template(args.template)     # 現場で気づくと測れないので、繋ぐ前に
    recorder = Recorder(args.root, template, software_commit=software_commit())
    serial = _import_serial()
    keys = _Keyboard()
    try:
        opened = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as e:
        raise SessionError(f"ポートを開けません: {args.port}（{e}）") from e
    print("RX の設定を待っています…（q で中止）")
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
                    print("設定が届きません。RX をリセットしてください（起動時に送ります）。")
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
        print("RX の設定が途中で変わったので終えました。続けるなら新しいセッションで測ってください。")
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
    print(f"窓 {len(windows)} 個・打ち切り {censored} 個（{censored_fraction(windows):.0%}）")
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


if __name__ == "__main__":
    sys.exit(main())
