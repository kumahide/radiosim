"""
webui/__main__.py
=================
`python -m webui`＝新しい画面の入口。

順番に意味がある:
1. 設定とログ（`core.config`＝通信の部品〔`ssl`・`requests`〕を読まない）
2. 空きのポートで待ち始め、**先に Edge を起こす**＝この後の読み込み（約 0.18 秒）を
   Edge の立ち上がりと重ねる。ページの接続はサーバーが応え始めるまで待ち行列で待つ
   （I-232 の実測・開発機・温まった状態＝Edge を起こすまで 0.19→0.03 秒・ページが
   状態を取りに来るまで 0.34→0.28 秒。いまはページがサーバーの準備を待つ＝残りは読み込み）
3. `NO_PROXY` に `127.0.0.1` を足す（自分のサーバーへの道がプロキシへ行かない）
4. その後で通信を使う `core` を読む（読んだ時点の通信の設定を固めるため）

truststore はここで入れない＝`core.dem` が初めての通信の直前に入れる（I-232＝起動の
経路で `truststore`・`urllib3`・`requests` を読まない）。

⚠️ import されても起動しない（`main()` の中でだけ core を読む＝`tests/test_smoke.py` が
ヘッドレスで import する）。設定は**読むだけ**＝`config.startup_lang` は設定が無いと
書き戻すので使わず `cfg["lang"]` を使う。
"""

from __future__ import annotations

import os
import sys
import threading


def _prepare_network() -> None:
    hosts = [h for h in os.environ.get("NO_PROXY", "").split(",") if h.strip()]
    if "127.0.0.1" not in hosts:
        os.environ["NO_PROXY"] = ",".join(hosts + ["127.0.0.1"])


def _fatal(message: str) -> None:
    sys.stderr.write(message + "\n")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "RadioSim", 0x10)  # type: ignore[attr-defined]
    except Exception:
        pass


def main() -> int:
    from core import config, i18n
    from webui import edge

    logger = config.setup_logging()
    cfg = config.load_config()
    i18n.set_lang(cfg.get("lang", "en"))

    browser = edge.find_edge()
    if browser is None:
        _fatal(i18n.t("webui_no_edge"))
        return 1
    listener = edge.Listener()
    profile = os.path.join(config.local_base_dir(), "edge_profile")
    try:
        window = edge.open_window(browser, listener.url, profile)
    except OSError as e:
        listener.sock.close()
        _fatal(f"{i18n.t('webui_no_edge')}\n{e}")
        return 1

    _prepare_network()
    from core import dem
    from webui.server import WebServer
    from webui.state import AppState
    from webui.worker import Worker

    dem.set_proxy(cfg.get("proxy_url", ""))
    stop = threading.Event()
    state = AppState(config.select_sim(cfg))
    worker = Worker(state)
    server = WebServer(worker, basemap=cfg.get("basemap_layer", "pale"), on_quit=stop.set,
                       listener=listener)
    threading.Thread(target=worker.run_forever, args=(stop,), daemon=True).start()
    threading.Thread(target=server.httpd.serve_forever, daemon=True).start()
    logger.info("webui: http://127.0.0.1:%d/", server.port)
    try:
        edge.wait_window(window, stop)
    finally:
        stop.set()
        server.httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
