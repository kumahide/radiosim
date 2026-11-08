"""
webui/server.py
===============
新しい画面の **HTTP サーバー**＝`127.0.0.1` の空きのポートだけで待つ。

守り（試作 `experiments/ui_proto/common/server.py` から）:
- `Host` が `127.0.0.1:<port>` でなければ 403（DNS の付け替えで外のページから届くのを断つ）
- `/api/*` は起動ごとの鍵が要る＝`X-Token` の頭。`sendBeacon` の `/api/bye` だけは
  頭を付けられないので URL の `?t=<鍵>`
- 静的ファイルは `webui/static/` の外へ出ない（`normpath` して根で始まるか）
- 本文は 1 MiB まで（複数経路の表を丸ごと送る＝1 行 150 バイトほどで数千行）

口:
    GET  /                       … `static/index.html`
    GET  /<静的ファイル>
    GET  /api/state?after=N      … 長いポーリング（変化の数が N を越えたら返る）
    POST /api/inputs             … `{"changes": {鍵: 値}}`＝変えた欄だけ。作業面（ステージ10a）は
                                   `"workspace": 面` と `"sheets": {面: 入力}`（面ごとに丸ごと）も
                                   受ける＝3 つのどれか 1 つは要る・形の誤りは 400
    GET  /tile/<src>/<z>/<x>/<y>.png … `dem.basemap_tile_png`
    POST /api/bye?t=<鍵>          … 窓を閉じた。2 秒のうちに次のページが来なければ終わる
"""

from __future__ import annotations

import csv
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
from core import dem_sources
from core import i18n
from core import models
from core import terrain_grid
from core import units
from webui import sheets as sheets_mod
from webui.edge import Listener
from webui.sheets import SheetError
from webui.state import UnknownKeyError
from webui.worker import Worker

logger = logging.getLogger("radiosim")

STATIC_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_MAX_BODY = 1024 * 1024
_POLL_TIMEOUT_S = 20.0
_LEAVE_GRACE_S = 2.0
_TILE_RE = re.compile(r"^/tile/([a-z0-9_]+)/(\d{1,2})/(\d{1,7})/(\d{1,7})\.png$")


#: 入力の列（Link 1.0 ステージ9a）＝(群の見出しの鍵, 「詳細」に畳むか, 欄の並び)。
#: 欄＝(入力の鍵, 欄の語の鍵, 吹き出しの鍵)。選択式の欄は `_choices` が選択肢を返す。
#: 🔑 `k_factor` は出さない（ステージ6 の決定④）＝入力の集合には残る（`state.AppState`）。
_FORM = (
    ("grp_site_info", False, (
        ("start", "lbl_start", "tip_start"),
        ("end", "lbl_end", "tip_end"),
        ("h_tx", "lbl_h_tx", "tip_h_tx"),
        ("h_rx", "lbl_h_rx", "tip_h_rx"),
    )),
    ("grp_radio_settings", False, (
        ("freq", "lbl_freq", "tip_freq"),
        ("p_tx", "lbl_p_tx", "tip_p_tx"),
        ("gain_tx", "lbl_gain_tx", "tip_gain_tx"),
        ("gain_rx", "lbl_gain_rx", "tip_gain_rx"),
        ("sens", "lbl_sens", "tip_sens"),
    )),
    ("grp_environment", False, (
        ("env_type", "lbl_env_type", None),
        ("veg_h", "lbl_veg_h", "tip_veg_h"),
        ("rain_rate", "lbl_rain", "tip_rain_rate"),
    )),
    ("webui_grp_advanced", True, (
        ("diff_method", "lbl_diff_method", "tip_diff_method"),
        ("resolution", "lbl_resolution", "tip_resolution"),
        ("dem_source", "lbl_dem_source", None),
    )),
)

#: 文字で打つ欄のうち、数を打つもの以外（座標）。
_TEXT_KEYS = frozenset({"start", "end"})

#: 空にしたとき欄に薄字で出す書式の例（I-237＝2026-11-08 経営者の選択「座標の 2 欄だけ」）。
#: 数の欄は単位が語に・範囲が吹き出しにある＝付けない（⑧ 二度言わない）。
_HINTS = {"start": "webui_hint_start", "end": "webui_hint_end"}

#: 損失の内訳（入力の列の下）＝(見出しの鍵, 字の鍵, 単位)。上から足し引きの順。
_BREAKDOWN = (
    ("eirp", "pl_eirp", "dBm"),
    ("fspl", "pl_fspl", "dB"),
    ("diff_loss", "pl_diff_loss", "dB"),
    ("veg_loss", "pl_veg_loss", "dB"),
    ("env_loss", "pl_env_loss", "dB"),
    ("rain_loss", "pl_rain_loss", "dB"),
    ("gas_loss", "pl_gas_loss", "dB"),
    ("total_loss", "pl_total_loss", "dB"),
    ("gain_rx", "pl_rx_ant_g", "dBi"),
    ("p_rx", "pl_rx_level", "dBm"),
    ("sens", "pl_threshold", "dBm"),
    ("margin", "pl_act_margin", "dB"),
)


def _choices(key: str) -> "list[list[str]] | None":
    """選択式の欄の [値, 語] の並び。文字で打つ欄は None。"""
    if key == "env_type":
        return [[k, i18n.t(f"env_{k}")] for k in models.ENV_KEYS]
    if key == "diff_method":
        return [["bullington", i18n.t("diff_opt_bullington")],
                ["single", i18n.t("diff_opt_single")]]
    if key == "resolution":
        return [[k, i18n.t(f"res_{k}")] for k in terrain_grid.RESOLUTION_KEYS]
    if key == "dem_source":
        return [[s.source_id, s.display_name] for s in dem_sources.all_sources()]
    return None


def form() -> "list[dict[str, Any]]":
    """入力の列の表（JS はこれを並べるだけ＝欄の順と語をここ 1 か所で持つ）。

    - 選べる DEM ソースが 1 つなら DEM の欄を出さない（I-153）＝値は入力の集合に残る。
    - 吹き出しの鍵が無い欄は吹き出しを付けない。
    """
    groups = []
    for title, advanced, rows in _FORM:
        fields = []
        for key, label, tip in rows:
            options = _choices(key)
            if key == "dem_source" and options is not None and len(options) < 2:
                continue
            field: dict[str, Any] = {"key": key, "label": i18n.t(label)}
            if tip is not None:
                field["tip"] = i18n.t(tip)
            if key in _HINTS:
                field["hint"] = i18n.t(_HINTS[key])
            if options is not None:
                field["options"] = options
            else:
                field["mode"] = "text" if key in _TEXT_KEYS else "decimal"
            fields.append(field)
        groups.append({"title": i18n.t(title).strip(), "advanced": advanced,
                       "fields": fields})
    return groups


def breakdown() -> "list[dict[str, Any]]":
    """損失の内訳の行の表（値は見出しの同じ名前の欄・桁は `units.DB_DECIMALS`）。"""
    return [{"name": n, "label": i18n.t(k), "unit": u, "digits": units.DB_DECIMALS}
            for n, k, u in _BREAKDOWN]


def labels() -> "dict[str, str]":
    """画面の字の表（JS に日本語を直書きしない）。"""
    return {
        "budget": i18n.t("panel_link_budget"),
        "computing": i18n.t("webui_computing"),
        "fetching_pct": i18n.t("status_fetching_pct"),
        "input": i18n.t("webui_input"),
        "terrain": i18n.t("webui_no_terrain"),
        "error": i18n.t("webui_error"),
        "profile_later": i18n.t("webui_profile_later"),
        # 判定の帯（語の横の数・前提を振った幅）
        "margin": i18n.t("pl_act_margin"),
        "p_rx": i18n.t("pl_rx_level"),
        "margin_range": i18n.t("webui_margin_range"),
        # 断面図（帳票と同じ語＝成果物の語を画面でも使う）
        "legend_terrain": i18n.t("legend_terrain"),
        "legend_vegetation": i18n.t("legend_vegetation"),
        "legend_los": i18n.t("legend_los"),
        "legend_fresnel": i18n.t("legend_fresnel"),
        "dist_axis": i18n.t("graph_dist_axis"),
        "alt_axis": i18n.t("graph_alt_axis"),
        # 作業面（ステージ10c）＝旧画面と同じ語を使う（成果物と取扱説明書の語に合わせる）
        "sheet_progress": i18n.t("webui_sheet_progress"),
        "pick_row": i18n.t("webui_pick_row"),
        "col_verdict": i18n.t("webui_col_verdict"),
        "add_row": i18n.t("btn_add_row"),
        "del_row": i18n.t("menu_del"),
        "import_csv": i18n.t("btn_import_csv"),
        "tab_compare": i18n.t("scn_tab_compare"),
        "tab_sweep": i18n.t("scn_tab_sweep"),
        "add_cond": i18n.t("scn_add_cond"),
        "del_cond": i18n.t("scn_del_cond"),
        "base": i18n.t("scn_base"),
        "cond_n": i18n.t("scn_cond_n"),
        "axis": i18n.t("scn_axis"),
        "from": i18n.t("scn_from"),
        "to": i18n.t("scn_to"),
        "points": i18n.t("scn_points"),
        "add_point": i18n.t("mh_add_point"),
        "del_point": i18n.t("mh_del_point"),
        "route_id": i18n.t("mh_route_id"),
        "note": i18n.t("col_note"),
        "waypoints": i18n.t("mh_waypoints").strip(),
        "hops": i18n.t("mh_hops_group").strip(),
        "hint_inherit": i18n.t("mh_hint_inherit"),
        "section": i18n.t("mh_col_section"),
    }


#: 作業面の切り替え（上の並び）＝(面, 語の鍵)。並びは `sheets.WORKSPACES` と同じ（試験が縛る）。
_WORKSPACE_LABELS = (
    ("single", "webui_ws_single"),
    ("batch", "batch_window_title"),
    ("scenario", "scn_mode"),
    ("multihop", "mh_window_title"),
)

#: 複数経路の表に出す列（旧画面の表と同じ 9 列）。CSV のほかの列（実測など）は表に出さず持つだけ。
_BATCH_SHOWN = ("id", "start", "end", "h_tx", "h_rx", "freq", "gain_tx", "gain_rx", "note")

#: 中継の点の欄と区間の欄＝(面の入力の鍵, 語の鍵)。
_WAYPOINT_COLS = (("name", "mh_col_name"), ("coord", "mh_col_coord"), ("h", "mh_col_height"))
_HOP_COLS = (("freq", "col_freq"), ("gain_tx", "col_gain_tx"), ("gain_rx", "col_gain_rx"))

#: 条件探索の上書きの鍵 → 選択式の欄（入力の列と同じ選択肢）。
_OVERRIDE_CHOICES = {"env_type": "env_type", "diff_method": "diff_method"}
#: 条件探索の上書きの鍵のうち、入力の集合と名前が違うもの（`scenario._apply` の読み替えと同じ）。
_OVERRIDE_INPUTS = {"freq_mhz": "freq"}


def sheet_spec() -> "dict[str, Any]":
    """作業面の切り替えと表の列（JS はこれを並べるだけ＝列の順と語をここ 1 か所で持つ）。"""
    from core import scenario as scn

    def cols(pairs: "tuple[tuple[str, str], ...]") -> "list[dict[str, str]]":
        return [{"key": k, "label": i18n.t(lab)} for k, lab in pairs]

    compare = []
    for key in scn.OVERRIDABLE:
        # `input`＝基準の行に出す入力の集合の鍵（空欄の薄字にも使う）
        col: dict[str, Any] = {"key": key, "label": i18n.t(f"scn_axis_{key}"),
                               "input": _OVERRIDE_INPUTS.get(key, key)}
        if key in _OVERRIDE_CHOICES:
            col["options"] = _choices(_OVERRIDE_CHOICES[key])
        compare.append(col)
    return {
        "workspaces": [{"key": k, "label": i18n.t(lab)} for k, lab in _WORKSPACE_LABELS],
        "batch": cols(tuple((k, f"col_{k}") for k in _BATCH_SHOWN)),
        "compare": compare,
        "sweep_axes": [[k, i18n.t(f"scn_axis_{k}")] for k in scn.SWEEP_AXES],
        "waypoints": cols(_WAYPOINT_COLS),
        "hops": cols(_HOP_COLS),
        # 足すボタンを止める数（越えた本文はサーバーが 400 で断る＝画面は手前で止める）
        "limits": {"compare": scn.MAX_COMPARE_CONDITIONS, "waypoints": sheets_mod.MAX_WAYPOINTS},
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
                 tile: "Callable[..., tuple[str, bytes | None]]" = dem.basemap_tile_png,
                 listener: "Listener | None" = None) -> None:
        self.worker = worker
        lis = listener or Listener()
        self.token = lis.token
        self.basemap = basemap
        self._on_quit = on_quit
        self._tile = tile
        self._page_serial = 0
        self._serial_lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(lis.address, _make_handler(self), bind_and_activate=False)
        self.httpd.socket.close()                        # 先に待ち始めた受け口へ差し替える
        self.httpd.socket = lis.sock
        self.httpd.server_address = lis.address
        self.httpd.server_name, self.httpd.server_port = lis.address
        self.httpd.daemon_threads = True
        self.port = lis.address[1]

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
            snap["form"] = form()
            snap["breakdown"] = breakdown()
            snap["sheet_spec"] = sheet_spec()
            snap["db_digits"] = units.DB_DECIMALS
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
            if parts.path == "/api/profile":
                if not self._token_ok(query):
                    return self._deny()
                try:
                    version = int((query.get("v") or [""])[0])
                    item = int((query.get("item") or [""])[0])
                except ValueError:
                    return self._json(400, {"error": "bad query"})
                shape = app.worker.profile_for(version, item)
                if shape is None:                        # 版が古い・その件に地形が無い
                    return self._json(404, {"error": "no profile"})
                return self._json(200, {"version": version, "item": item, "profile": shape})
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
                if body is None or not ({"changes", "workspace", "sheets"} & set(body)):
                    return self._json(400, {"error": "bad body"})
                changes = body.get("changes", {})
                workspace = body.get("workspace")
                if not isinstance(changes, dict) or not (workspace is None
                                                         or isinstance(workspace, str)):
                    return self._json(400, {"error": "bad body"})
                try:
                    version = app.worker.submit(changes, workspace=workspace,
                                                sheets=body.get("sheets"))
                except UnknownKeyError as e:
                    return self._json(400, {"error": f"unknown: {e}"})
                except SheetError as e:
                    return self._json(400, {"error": f"sheet: {e}"})
                return self._json(200, {"version": version})
            if parts.path == "/api/batch_csv":
                body = self._body()
                text = body.get("text") if body is not None else None
                if not isinstance(text, str):
                    return self._json(400, {"error": "bad body"})
                try:
                    rows = sheets_mod.batch_from_csv(text)
                except csv.Error as e:
                    return self._json(422, {"message": str(e)})
                except ValueError as e:                  # 見出し・必須列・空＝画面の字
                    return self._json(422, {"message": str(e)})
                version = app.worker.submit({}, sheets={"batch": rows})
                return self._json(200, {"version": version, "rows": len(rows)})
            self._send(404, b"not found", "text/plain; charset=utf-8")

    return Handler
