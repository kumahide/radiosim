"""`127.0.0.1` だけで聞く小さな HTTP サーバ＝画面の素材・タイル・計算の口を配る。

A（pywebview）と B（Edge のアプリモード）は同じ画面を読み、C（Tk）はタイルだけ使う。
ポートは空きを OS に選ばせる。`Host` を確かめ、計算の口は起動ごとの鍵を要る形に
する＝同じ機械の別のページから呼ばれないように。
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import engine, tiles
from core import dem

_TILE_RE = re.compile(r"^/tile/([a-z]+)/(\d{1,2})/(\d{1,7})/(\d{1,7})\.png$")
_MAX_BODY = 64 * 1024


class App:
    """1 回の起動の状態＝測定の値と、終わりの合図。"""

    def __init__(self, web_root: str, facts: dict, measure: bool) -> None:
        self.web_root = web_root
        self.facts = facts
        self.measure = measure
        self.token = secrets.token_urlsafe(16)
        self.metrics: dict = {}
        self.measured = threading.Event()
        self.closed = threading.Event()
        self.page_serial = 0
        self.port = 0

    def page_left(self) -> None:
        """ページが閉じた＝読み直し（プロキシの適用）なら間もなく次が来る。"""
        serial = self.page_serial

        def _check() -> None:
            if self.page_serial == serial:
                self.closed.set()
        threading.Timer(2.0, _check).start()

    @property
    def url(self) -> str:
        flag = "&measure=1" if self.measure else ""
        return f"http://127.0.0.1:{self.port}/?t={self.token}{flag}"

    def env_payload(self) -> dict:
        return {**self.facts, "tiles": tiles.stats()}


def _make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):  # noqa: A002 ─ 親のシグネチャ
            pass

        def _send(self, code: int, body: bytes, content_type: str,
                  cache: str = "no-store") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass  # 画面が先に取り消した＝捨ててよい

        def _json(self, payload: dict, code: int = 200) -> None:
            self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{app.port}"

        def _body(self) -> dict:
            length = min(int(self.headers.get("Content-Length") or 0), _MAX_BODY)
            raw = self.rfile.read(length)
            return json.loads(raw or b"{}")

        def do_GET(self):  # noqa: N802 ─ 標準の名前
            if not self._host_ok():
                return self._send(403, b"", "text/plain")
            path = self.path.split("?", 1)[0]
            if path == "/":
                app.page_serial += 1
            if (m := _TILE_RE.match(path)):
                source, z, x, y = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
                data = tiles.get_tile(source, z, x, y)
                if data is None:
                    return self._send(404, b"", "text/plain", "no-store")
                return self._send(200, data, "image/png", "max-age=86400")
            if path == "/api/env":
                return self._json(app.env_payload())
            return self._static("index.html" if path == "/" else path.lstrip("/"))

        def _static(self, rel: str) -> None:
            full = os.path.normpath(os.path.join(app.web_root, rel))
            if not full.startswith(os.path.normpath(app.web_root) + os.sep) or not os.path.isfile(full):
                return self._send(404, b"", "text/plain")
            with open(full, "rb") as f:
                body = f.read()
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, body, ctype)

        def do_POST(self):  # noqa: N802 ─ 標準の名前
            path, _, query = self.path.partition("?")
            if path == "/api/bye" and self._host_ok() and query == f"t={app.token}":
                # sendBeacon は見出しを足せない＝鍵は URL で受ける。
                app.page_left()
                return self._send(204, b"", "text/plain")
            if not self._host_ok() or self.headers.get("X-Token") != app.token:
                return self._send(403, b"", "text/plain")
            body = self._body()
            if path == "/api/calc":
                return self._json({"seq": body.get("seq"), **engine.calculate(body.get("inputs", {}))})
            if path == "/api/proxy":
                dem.set_proxy(str(body.get("url", "")))
                return self._json({})
            if path == "/api/metrics":
                app.metrics.update(body)
                if body.get("done"):
                    app.measured.set()
                return self._json({})
            return self._send(404, b"", "text/plain")

    return Handler


def start(app: App) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(app))
    server.daemon_threads = True
    app.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def write_metrics(app: App, path: str, started: float) -> None:
    """測定の値を JSON へ（開発機の実測の表の元）。"""
    payload = {**app.facts, **app.metrics, "tiles": tiles.stats(),
               "wall_s": round(time.time() - started, 3)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
