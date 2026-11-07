"""
webui/server.py
===============
新しい画面の **HTTP サーバー**＝`127.0.0.1` の空きのポートだけで待つ。

守り（試作 `experiments/ui_proto/common/server.py` から）:
- `Host` が `127.0.0.1:<port>` でなければ 403（DNS の付け替えで外のページから届くのを断つ）
- `/api/*` は起動ごとの鍵が要る＝`X-Token` の頭。`sendBeacon` の `/api/bye` だけは
  頭を付けられないので URL の `?t=<鍵>`
- 静的ファイルは `webui/static/` の外へ出ない（`normpath` して根で始まるか）
- 本文は 64 KiB まで

口:
    GET  /                       … `static/index.html`
    GET  /<静的ファイル>
    GET  /api/state?after=N      … 長いポーリング（変化の数が N を越えたら返る）
    POST /api/inputs             … `{"changes": {鍵: 値}}`＝変えた欄だけ
    GET  /tile/<src>/<z>/<x>/<y>.png … `dem.basemap_tile_png`
    POST /api/bye?t=<鍵>          … 窓を閉じた。2 秒のうちに次のページが来なければ終わる
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from core import dem
from core import i18n
from webui.state import UnknownKeyError
from webui.worker import Worker

logger = logging.getLogger("radiosim")

STATIC_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_MAX_BODY = 64 * 1024
_POLL_TIMEOUT_S = 20.0
_LEAVE_GRACE_S = 2.0
_TILE_RE = re.compile(r"^/tile/([a-z0-9_]+)/(\d{1,2})/(\d{1,7})/(\d{1,7})\.png$")


def labels() -> "dict[str, str]":
    """画面の字の表（JS に日本語を直書きしない）。"""
    return {
        "start": i18n.t("lbl_start"),
        "end": i18n.t("lbl_end"),
        "h_tx": i18n.t("lbl_h_tx"),
        "h_rx": i18n.t("lbl_h_rx"),
        "freq": i18n.t("lbl_freq"),
        "computing": i18n.t("webui_computing"),
        "fetching_pct": i18n.t("status_fetching_pct"),
        "input": i18n.t("webui_input"),
        "terrain": i18n.t("webui_no_terrain"),
        "error": i18n.t("webui_error"),
        "profile_later": i18n.t("webui_profile_later"),
    }


def resolve_static(path: str, root: str = STATIC_ROOT) -> "str | None":
    """URL の道 → `root` の下のファイル。外へ出る道・無いファイルは None。"""
    rel = path.lstrip("/") or "index.html"
    full = os.path.normpath(os.path.join(root, rel))
    if not full.startswith(os.path.normpath(root) + os.sep):
        return None
    return full if os.path.isfile(full) else None


class WebServer:
    """サーバー本体。`serve_forever` は呼び出し側のスレッドで回す。"""

    def __init__(self, worker: Worker, *, basemap: str = "pale",
                 on_quit: "Callable[[], None] | None" = None,
                 tile: "Callable[..., tuple[str, bytes | None]]" = dem.basemap_tile_png) -> None:
        self.worker = worker
        self.token = secrets.token_urlsafe(24)
        self.basemap = basemap
        self._on_quit = on_quit
        self._tile = tile
        self._page_serial = 0
        self._serial_lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]

    @property
    def url(self) -> str:
        """Edge に渡す URL（鍵は断片＝サーバーへは送られない・JS が読む）。"""
        return f"http://127.0.0.1:{self.port}/#{self.token}"

    def host_ok(self, host: "str | None") -> bool:
        return host == f"127.0.0.1:{self.port}"

    def page_seen(self) -> None:
        with self._serial_lock:
            self._page_serial += 1

    def page_left(self) -> None:
        """窓が閉じた（または読み直した）。2 秒のうちに次のページが来なければ終わる。"""
        with self._serial_lock:
            seen = self._page_serial

        def check() -> None:
            with self._serial_lock:
                if self._page_serial != seen:
                    return                               # 読み直しだった
            self.quit()

        timer = threading.Timer(_LEAVE_GRACE_S, check)
        timer.daemon = True
        timer.start()

    def quit(self) -> None:
        if self._on_quit is not None:
            self._on_quit()
        threading.Thread(target=self.httpd.shutdown, daemon=True).start()

    def state_payload(self, after: int, timeout: float = _POLL_TIMEOUT_S) -> "dict[str, Any]":
        snap = self.worker.state.wait(after, timeout)
        if after == 0:                                   # 最初の 1 回だけ字の表を付ける
            snap["labels"] = labels()
            snap["basemap"] = self.basemap
        return snap


def _make_handler(app: WebServer) -> type:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "RadioSim"

        def log_message(self, format: str, *args: Any) -> None:   # noqa: A002
            logger.debug("webui: " + format, *args)

        # ---- 応答 ----------------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _deny(self) -> None:
            self.close_connection = True                 # 読まずに断った本文を次の要求と取り違えない
            self._send(403, b"forbidden", "text/plain; charset=utf-8")

        def _token_ok(self, query: dict) -> bool:
            given = self.headers.get("X-Token") or (query.get("t") or [""])[0]
            return secrets.compare_digest(given, app.token)

        def _body(self) -> "dict | None":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if n < 0 or n > _MAX_BODY:
                return None
            try:
                obj = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return None
            return obj if isinstance(obj, dict) else None

        # ---- GET -----------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            if not app.host_ok(self.headers.get("Host")):
                return self._deny()
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if parts.path == "/api/state":
                if not self._token_ok(query):
                    return self._deny()
                try:
                    after = int((query.get("after") or ["0"])[0])
                except ValueError:
                    after = 0
                if after == 0:
                    app.page_seen()
                return self._json(200, app.state_payload(after))
            m = _TILE_RE.match(parts.path)
            if m:
                return self._serve_tile(m)
            if parts.path.startswith("/api/"):
                return self._deny()
            full = resolve_static(parts.path)
            if full is None:
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype)

        def _serve_tile(self, m: "re.Match[str]") -> None:
            src, z, x, y = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
            try:
                word, png = app._tile(src, z, x, y)
            except Exception:
                logger.exception("webui: 地図のタイルで予期しない例外")
                word, png = "failed", None
            if png:
                return self._send(200, png, "image/png")
            code = 404 if word == "absent" else 502
            self._send(code, word.encode("ascii", "replace"), "text/plain; charset=utf-8")

        # ---- POST ----------------------------------------------------------
        def do_POST(self) -> None:  # noqa: N802
            if not app.host_ok(self.headers.get("Host")):
                return self._deny()
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if not self._token_ok(query):
                return self._deny()
            if parts.path == "/api/bye":
                app.page_left()
                return self._send(204, b"", "text/plain; charset=utf-8")
            if parts.path == "/api/inputs":
                body = self._body()
                changes = body.get("changes") if body is not None else None
                if not isinstance(changes, dict):
                    return self._json(400, {"error": "bad body"})
                try:
                    version = app.worker.submit(changes)
                except UnknownKeyError as e:
                    return self._json(400, {"error": f"unknown: {e}"})
                return self._json(200, {"version": version})
            self._send(404, b"not found", "text/plain; charset=utf-8")

    return Handler
