"""
tools/field_remote/timed_log.py
===============================
`gnss log` を、キー入力だけ「N 秒たったら q」に差し替えて回す（残りの引数はそのまま渡す）。
SSH から切り離して記録を起動するのに使う。回し方は同じフォルダの README.md。

  python tools\\field_remote\\timed_log.py 秒数 --port COM6 --root C:\\dev\\field-data\\gnss [gnss log の引数…]

止め方は q と同じ道＝秒数が来るか、止めの合図のファイル（`%TEMP%\\timed_log.stop`）が
置かれると、受信機を始めの速度と NMEA ありへ戻してから終わる。合図のファイルは読んだら消す・
起動のときに残っていたら消してから始める（前の回の合図で即座に止まらない）。
⚠️ プロセスを殺さない＝受信機は生データを出したまま残る（B-349 の形）。途中で止めるのは合図で。
⚠️ 切り離した起動には SSH の中で入れた環境変数が届かない（NTRIP のパスワードは README）。
⚠️ 名前を変えるときは x13_watch.ps1 の「記録の処理」の照合（`timed_log\\w*\\.py`）も直す。
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from apps.field import cli  # noqa: E402


STOP_FILE = Path(tempfile.gettempdir()) / "timed_log.stop"   # X13 では %TEMP%


class TimedKeys:
    """`gnss log` のキー入力の口の差し替え。秒数が来るか合図のファイルがあれば `q` を返す
    （秒数は記録の処理がキー入力の口を作った時点から数える）。"""

    def __init__(self, seconds: float, stop_file: Path) -> None:
        self._until = time.monotonic() + seconds
        self._stop_file = stop_file

    def poll(self) -> str | None:
        if self._stop_file.exists():
            self._stop_file.unlink(missing_ok=True)
            print(f"\n止めの合図（{self._stop_file}）を受けました")
            return "q"
        return "q" if time.monotonic() >= self._until else None


def main(argv: list[str], stop_file: Path = STOP_FILE) -> int:
    if not argv:
        print("使い方: timed_log.py 秒数 --port COM? --root フォルダ [gnss log の引数…]",
              file=sys.stderr)
        return 2
    seconds = float(argv[0])
    # 出力はファイルへ向けて起動する＝既定の cp932 だと「⚠️」の行で書き出しが落ちる
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", line_buffering=True)
    if stop_file.exists():
        stop_file.unlink()
        print(f"前の回の止めの合図が残っていたので消しました（{stop_file}）")
    print(f"{seconds:g} 秒で止めます（途中で止めるときは {stop_file} を置く）")

    cli._Keyboard = lambda: TimedKeys(seconds, stop_file)
    return cli.main(["gnss", "log", *argv[1:]])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
