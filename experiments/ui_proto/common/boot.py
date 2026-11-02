"""3 候補の起動の共通部＝製品の `main.py` と同じ順で証明書を入れてからコアを読む。"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
from dataclasses import dataclass

from common import probe

_APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "RadioSimUiProto")


@dataclass
class Boot:
    app: object
    size: tuple[int, int] | None
    measure_out: str | None
    offline: bool


def web_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "web")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def fatal(message: str) -> None:
    """窓が出ないときの最後の口＝`--noconsole` では print が届かない。

    同じ文を `%LOCALAPPDATA%\\RadioSimUiProto\\last_error.txt` にも残す＝測定の自動化と、
    メッセージ箱を閉じてしまった後の確認のため。
    """
    try:
        os.makedirs(_APP_DIR, exist_ok=True)
        with open(app_dir("last_error.txt"), "w", encoding="utf-8") as f:
            f.write(message)
    except OSError:
        pass
    ctypes.windll.user32.MessageBoxW(None, message, "RadioSim 画面の試作", 0x10)
    raise SystemExit(1)


def app_dir(*parts: str) -> str:
    return os.path.join(_APP_DIR, *parts)


def start(candidate: str) -> Boot:
    parser = argparse.ArgumentParser()
    parser.add_argument("--measure", help="測り終えたら値をこの JSON へ書いて閉じる")
    parser.add_argument("--size", help="窓の大きさ（例 1900x950）＝省略で最大化")
    parser.add_argument("--offline", action="store_true",
                        help="届かないプロキシを挟み、ネットワークを切った状態を作る")
    args = parser.parse_args()

    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:  # noqa: BLE001 ─ 製品の main.py と同じく、無くても起動は止めない
        pass
    os.environ["NO_PROXY"] = ",".join(filter(None, [os.environ.get("NO_PROXY"), "127.0.0.1"]))

    from common import server
    from core import config, dem, i18n

    cfg = config.load_config()
    # 製品の main.py と同じ言語の決め方＝「地形なし」などコアの文を同じ言語で出す。
    i18n.set_lang(config.startup_lang(cfg))
    app = server.App(web_root(), probe.facts(candidate, probe.launch_count(candidate)),
                     measure=bool(args.measure))
    app.facts["process_start_epoch_ms"] = round(probe.process_start_epoch() * 1000)
    if args.offline:
        dem.set_proxy("http://127.0.0.1:9")
    else:
        dem.set_proxy(cfg.get("proxy_url", ""))
    server.start(app)
    size = tuple(int(v) for v in args.size.split("x")) if args.size else None
    return Boot(app, size, args.measure, args.offline)


def finish(boot: Boot) -> None:
    if boot.measure_out:
        from common import server
        server.write_metrics(boot.app, boot.measure_out, probe.process_start_epoch())
