"""
webui/__main__.py
=================
`python -m webui`＝新しい画面の入口。

順番に意味がある:
1. truststore を入れる（社内のプロキシの証明書を OS の証明書ストアで信じる）
2. `NO_PROXY` に `127.0.0.1` を足す（自分のサーバーへの道がプロキシへ行かない）
3. その後で `core` を読む（読んだ時点の通信の設定を固めるため）

⚠️ import されても起動しない（`main()` の中でだけ core を読む＝`tests/test_smoke.py` が
ヘッドレスで import する）。設定は**読むだけ**＝`config.startup_lang` は設定が無いと
書き戻すので使わず `cfg["lang"]` を使う。
"""

from __future__ import annotations

import os
import sys
import threading


def _prepare_network() -> None:
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:                                    # 無くても動く（OS の既定で信じる）
        pass
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
    _prepare_network()
    from core import config, dem, i18n
    from webui import edge
    from webui.server import WebServer
    from webui.state import AppState
    from webui.worker import Worker

    logger = config.setup_logging()
    cfg = config.load_config()
    i18n.set_lang(cfg.get("lang", "en"))
    dem.set_proxy(cfg.get("proxy_url", ""))

    browser = edge.find_edge()
    if browser is None:
        _fatal(i18n.t("webui_no_edge"))
        return 1

    stop = threading.Event()
    state = AppState(config.select_sim(cfg))
    worker = Worker(state)
    server = WebServer(worker, basemap=cfg.get("basemap_layer", "pale"), on_quit=stop.set)
    threading.Thread(target=worker.run_forever, args=(stop,), daemon=True).start()
    threading.Thread(target=server.httpd.serve_forever, daemon=True).start()
    logger.info("webui: http://127.0.0.1:%d/", server.port)

    profile = os.path.join(config.cache_log_base_dir(), "edge_profile")
    try:
        edge.run_window(browser, server.url, profile, stop)
    except OSError as e:
        _fatal(f"{i18n.t('webui_no_edge')}\n{e}")
        return 1
    finally:
        stop.set()
        server.httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
