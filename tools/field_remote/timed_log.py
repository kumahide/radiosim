"""
tools/field_remote/timed_log.py
===============================
`gnss log` を、キー入力だけ「N 秒たったら q」に差し替えて回す（残りの引数はそのまま渡す）。
SSH から切り離して記録を起動するのに使う。回し方は同じフォルダの README.md。

  python tools\\field_remote\\timed_log.py 秒数 --port COM6 --root C:\\dev\\field-data\\gnss [gnss log の引数…]

止め方は q と同じ道＝秒数が来ると受信機を始めの速度と NMEA ありへ戻してから終わる。
⚠️ 途中で止める口は無い＝プロセスを殺すと受信機は生データを出したまま残る（B-349 の形）。
⚠️ 切り離した起動には SSH の中で入れた環境変数が届かない（NTRIP のパスワードは README）。
⚠️ 名前を変えるときは x13_watch.ps1 の「記録の処理」の照合（`timed_log\\w*\\.py`）も直す。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from apps.field import cli  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        print("使い方: timed_log.py 秒数 --port COM? --root フォルダ [gnss log の引数…]",
              file=sys.stderr)
        return 2
    seconds = float(argv[0])
    # 出力はファイルへ向けて起動する＝既定の cp932 だと「⚠️」の行で書き出しが落ちる
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", line_buffering=True)

    class _TimedKeys:  # 秒数は記録の処理がキー入力の口を作った時点から数える
        def __init__(self) -> None:
            self._until = time.monotonic() + seconds

        def poll(self) -> str | None:
            return "q" if time.monotonic() >= self._until else None

    cli._Keyboard = _TimedKeys
    return cli.main(["gnss", "log", *argv[1:]])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
