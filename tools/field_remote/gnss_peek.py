"""
tools/field_remote/gnss_peek.py
================================
記録中（または記録済み）の GNSS の生ログのフォルダを**読むだけ**で、様子を 1 画面に出す。
野外で Claude が SSH 越しに確かめるための道具（呼び手は `x13_watch.ps1`）。

    python tools/field_remote/gnss_peek.py <gnss-… のフォルダ>

出すもの
  - 最後の受信から何秒たったか（＝記録が止まっていないか）・直近 60 秒の 1 秒あたりのバイト数
  - 記録器と**同じ数え方**の状態の行（RAWX の信号数・解・RAWX の欠け・検査和の不一致）
数え方は `GnssLogger.poll()` に生ログを読み出しの区切りのまま流して得る＝記録器の画面と
食い違わない（数え方を写さない）。

⚠️ 書き込み中のファイルを読む＝索引の末尾の行が書きかけのことがある。読めない行で止め、
   そこまでの分で出す（本体を先に書く順なので、索引は本体を追い越さない）。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apps.field.gnss import INDEX_FILE, RAW_FILE, GnssLogger  # noqa: E402
from apps.field.session import SessionError, parse_utc, read_raw  # noqa: E402

_RECENT_S = 60.0


class _Replay:
    """記録した読み出しを 1 回ずつ渡すポート（書き込みは受けない）。"""

    def __init__(self) -> None:
        self.chunk = b""

    @property
    def in_waiting(self) -> int:
        return len(self.chunk)

    def read(self, size: int) -> bytes:
        chunk, self.chunk = self.chunk, b""
        return chunk

    def write(self, data: bytes) -> int | None:
        raise AssertionError("読むだけの道具は受信機へ書かない")


def peek(directory: Path, now: datetime) -> list[str]:
    port = _Replay()
    logger = GnssLogger(directory.parent, port, software_commit="peek", port_name="-",
                        baud=0, clock=lambda: "")
    reads: list[tuple[datetime, int]] = []
    try:
        for pc_utc, chunk in read_raw(directory, raw_file=RAW_FILE, index_file=INDEX_FILE):
            reads.append((parse_utc(pc_utc), len(chunk)))
            port.chunk = chunk
            logger.poll()
    except (SessionError, ValueError, TypeError, KeyError):
        pass                                    # 書きかけの末尾の行＝そこまでで出す
    if not reads:
        return [f"{directory.name}: まだ 1 回も受けていません"]
    first, last = reads[0][0], reads[-1][0]
    recent = [n for t, n in reads if (last - t).total_seconds() <= _RECENT_S]
    span = min(_RECENT_S, max(1.0, (last - first).total_seconds()))
    logger.bytes_written = sum(n for _, n in reads)
    return [
        f"{directory.name}",
        f"最後の受信から {(now - last).total_seconds():.0f} 秒"
        f"・直近 {span:.0f} 秒は 1 秒あたり {sum(recent) / span:.0f} バイト",
        logger.status_line((last - first).total_seconds()),
        logger.summary(),
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("使い方: python tools/field_remote/gnss_peek.py <gnss-… のフォルダ>")
        return 2
    directory = Path(argv[0])
    if not (directory / RAW_FILE).exists():
        print(f"生ログがありません: {directory / RAW_FILE}")
        return 1
    print("\n".join(peek(directory, datetime.now(timezone.utc))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
