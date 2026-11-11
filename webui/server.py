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
    GET  /                       … `static/index.html`（`<title>` は `version.APP_NAME`＝`titled_page`）
    GET  /<静的ファイル>
    GET  /api/state?after=N      … 長いポーリング（変化の数が N を越えたら返る）
    POST /api/inputs             … `{"changes": {鍵: 値}}`＝変えた欄だけ。作業面（ステージ10a）は
                                   `"workspace": 面` と `"sheets": {面: 入力}`（面ごとに丸ごと）も
                                   受ける＝3 つのどれか 1 つは要る・形の誤りは 400
    POST /api/project/save       … 今の入力と面を `.rsproj` の文字列で返す `{"text", "name", "warnings"}`
                                   （ファイルを書くのは画面＝ステージ10d）
    POST /api/settings           … `{"key", "value"}`＝「設定」の枠の 1 項目を保存して効かせる
                                   `{"settings", "reload"}`（知らない鍵・選べる値の外は 400・I-257）
    POST /api/settings/load_sim  … `{"text"}`＝パラメータ読込（sim の鍵だけ）`{"message", "version"}`
    POST /api/settings/load_app  … `{"text"}`＝アプリ設定読込 `{"message", "settings", "reload"}`
                                   （読めない JSON は 422＋画面の 1 文・I-257 の C）
    POST /api/lang_template      … 訳のひな形を `USER_LANG_DIR` へ書いてフォルダを開く `{"message"}`
    POST /api/cache/breakdown    … 全キャッシュ削除の確認の中身（ソースが 2 つ以上なら選ばせる）
    POST /api/cache/delete       … `{"sources": [ID…] | null, "basemap": 真偽}` `{"message"}`
    POST /api/help/docs          … 説明書を開く（`webui/docs.py`・無ければ 422＋画面の 1 文）
    POST /api/help/check_updates … 更新の確認 `{"message"}`（答えを得られなければ 422）
    POST /api/help/diagnostics   … 診断パッケージの小区画の中身（字・項目・結果の一覧・名前）
    POST /api/help/diagnostics/zip … `{"facts": [..], "results": [..]}` → `{"zip"（base64）}`
                                   （一覧の外の名前は 400＝結果フォルダの外を読ませない）
    POST /api/project/open       … `{"text"}`＝`.rsproj` の本文で入力と面を差し替える（読めなければ 422）
    POST /api/sheet/csv          … 今の版の作業面の結果を出力契約の CSV で返す `{"text", "name"}`
                                   （全件そろっていなければ 409＋画面の 1 文 `message`）
    POST /api/sheet/kml          … 同じく KML（`summary.kml`＝複数経路だけ・ほかの面は 409）
    POST /api/image/map          … 今の版の地図の PNG `{"png"（base64）, "name"}`（帳票と同じ絵＝
                                   判定が出る前は 409・背景が取れなければ 503＋画面の 1 文）
    GET  /tile/<src>/<z>/<x>/<y>.png … `dem.basemap_tile_png`
    POST /api/bye?t=<鍵>          … 窓を閉じた。2 秒のうちに次のページが来なければ終わる
"""

from __future__ import annotations

import base64
import csv
import html
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

from core import config
from core import dem
from core import dem_sources
from core import i18n
from core import models
from core import terrain_grid
from core import units
from core import version
from webui import docs as docs_mod
from webui import exports
from webui import files as files_mod
from webui import project_io
from webui import settings as settings_mod
from webui import sheets as sheets_mod
from webui import support
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

#: 距離の項目（リンクバジェットの下の別の表）＝(見出しの鍵, 字の鍵, 単位)。
#: リンクバジェットの足し引きの項ではないので、同じ表に混ぜない（2026-11-10 経営者の指示）。
_DISTANCES = (
    ("slant", "pl_slant_dist", "m"),
    ("horiz", "pl_horiz_dist", "m"),
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


def distances() -> "list[dict[str, Any]]":
    """距離の項目の行の表（値は見出しの同じ名前の欄）。

    見出しには整形済みの字（`units.format_distance`＝桁区切りあり）が載る＝`text` の行は
    JS が桁をそろえずそのまま出す。
    """
    return [{"name": n, "label": i18n.t(k), "unit": u, "text": True}
            for n, k, u in _DISTANCES]


def labels() -> "dict[str, str]":
    """画面の字の表（JS に日本語を直書きしない）。"""
    return {
        "budget": i18n.t("panel_link_budget"),
        "distance": i18n.t("webui_distance"),
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
        "row_range": i18n.t("webui_row_range"),          # 作業面の選んだ行（B-384）
        "calc_profile": i18n.t("webui_calc_profile"),    # 計算方式を常に名乗る（B-383）
        "calc_profile_v1": i18n.t("webui_calc_profile_v1"),
        # 断面図（帳票と同じ語＝成果物の語を画面でも使う）
        "legend_terrain": i18n.t("legend_terrain"),
        "legend_vegetation": i18n.t("legend_vegetation"),
        "legend_los": i18n.t("legend_los"),
        "legend_fresnel": i18n.t("legend_fresnel"),
        "dist_axis": i18n.t("graph_dist_axis"),
        "alt_axis": i18n.t("graph_alt_axis"),
        # 地図から地点を置くメニュー（4 つの面とも）＝TX／RX は旧画面の右クリックのメニューと同じ語
        "place_tx": i18n.t("map_menu_place_tx"),
        "place_rx": i18n.t("map_menu_place_rx"),
        "map_new_row": i18n.t("webui_map_new_row"),
        "map_point_limit": i18n.t("webui_map_point_limit"),
        # 作業面（ステージ10c）＝旧画面と同じ語を使う（成果物と取扱説明書の語に合わせる）
        "sheet_progress": i18n.t("webui_sheet_progress"),
        "pick_row": i18n.t("webui_pick_row"),
        "col_verdict": i18n.t("webui_col_verdict"),
        "add_row": i18n.t("btn_add_row"),
        "del_row": i18n.t("menu_del"),
        "import_csv": i18n.t("btn_import_csv"),
        # `.rsproj` の開く・保存（ステージ10d）＝旧画面のメニューと同じ語
        "open_project": i18n.t("menu_open_project"),
        "save_project": i18n.t("menu_save_project"),
        "proj_filetype": i18n.t("proj_filetype"),
        "proj_saved": i18n.t("proj_saved"),
        # 作業面の成果物の保存（ステージ10d）
        "save_csv": i18n.t("webui_save_csv"),
        "save_kml": i18n.t("webui_save_kml"),
        "save_map_png": i18n.t("webui_save_map_png"),
        "save_profile_png": i18n.t("webui_save_profile_png"),
        "no_profile": i18n.t("webui_no_profile"),
        "file_saved": i18n.t("webui_file_saved"),
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


_TITLE_RE = re.compile(rb"<title>[^<]*</title>")


def titled_page(page: bytes) -> bytes:
    """`index.html` の `<title>`（＝Edge の窓の題）を `version.APP_NAME` にする（ステージ11c）。

    Pro は起動の最初に `version.set_app_name` で名前を差し替える＝静的なファイルは Lite の名前のまま。
    """
    title = "<title>" + html.escape(version.APP_NAME) + "</title>"
    return _TITLE_RE.sub(title.encode("utf-8"), page, count=1)


class WebServer:
    """サーバー本体。`serve_forever` は呼び出し側のスレッドで回す。"""

    def __init__(self, worker: Worker, *, basemap: str = "pale",
                 on_quit: "Callable[[], None] | None" = None,
                 tile: "Callable[..., tuple[str, bytes | None]]" = dem.basemap_tile_png,
                 map_png: "Callable[[list, str], bytes | None]" = exports.map_png,
                 listener: "Listener | None" = None,
                 app_cfg: "dict[str, str] | None" = None,
                 opener: "Callable[[str], None] | None" = None) -> None:
        self.worker = worker
        #: フォルダや文書を開く（訳のひな形・説明書）＝試験は偽物を渡す
        self.opener: "Callable[[str], None]" = opener or getattr(os, "startfile", lambda p: None)
        #: アプリの設定（`config.load_config()` の写し）＝「設定」の枠が書き換えて保存する
        self.app_cfg: dict[str, str] = app_cfg if app_cfg is not None else {}
        self._settings_lock = threading.Lock()
        lis = listener or Listener()
        self.token = lis.token
        self.basemap = basemap
        self._on_quit = on_quit
        self._tile = tile
        self._map_png = map_png
        self._page_serial = 0
        self._serial_lock = threading.Lock()
        #: 開いた `.rsproj` の案件名とメモ（画面に欄は無い＝保存で持ち越すだけ）
        self.project_meta: dict[str, str] = {}
        self.httpd = ThreadingHTTPServer(lis.address, _make_handler(self), bind_and_activate=False)
        self.httpd.socket.close()                        # 先に待ち始めた受け口へ差し替える
        self.httpd.socket = lis.sock
        self.httpd.server_address = lis.address
        self.httpd.server_name, self.httpd.server_port = lis.address
        self.httpd.daemon_threads = True
        self.port = lis.address[1]

    def map_image(self) -> "tuple[int, dict[str, Any]]":
        """今の版の地図の PNG（`/api/image/map` の `(状態, 本文)`）。"""
        version, inputs = self.worker.state.snapshot()
        head = self.worker.state.headline()
        lines = exports.map_lines(head, inputs) if head.get("version") == version else None
        if lines is None:
            return 409, {"message": i18n.t("webui_no_result")}
        png = self._map_png(lines, self.basemap)
        if png is None:
            return 503, {"message": i18n.t("html_map_unavailable")}
        return 200, {"png": base64.b64encode(png).decode("ascii"), "name": "map.png"}

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
            snap["distances"] = distances()
            snap["sheet_spec"] = sheet_spec()
            snap["db_digits"] = units.DB_DECIMALS
            snap["basemap"] = self.basemap_payload()
            snap["settings"] = self.settings_payload()
        return snap

    def basemap_payload(self) -> "dict[str, Any]":
        """画面の地図のタイルの元＝`{id, attribution, max_zoom}`（起動時と背景地図を替えたとき）。"""
        return {"id": self.basemap, "attribution": exports.map_attribution(self.basemap),
                "max_zoom": dem.basemap_max_zoom(self.basemap)}

    def settings_payload(self) -> "dict[str, Any]":
        with self._settings_lock:
            return settings_mod.payload(self.app_cfg)

    def apply_setting(self, key: Any, value: Any) -> "tuple[int, dict[str, Any]]":
        """`/api/settings` の `(状態, 本文)`。知らない鍵・選べる値の外は 400。"""
        if not isinstance(key, str):
            return 400, {"error": "bad body"}
        with self._settings_lock:
            try:
                reload = settings_mod.apply(self.app_cfg, key, value)
            except settings_mod.SettingError as e:
                return 400, {"error": str(e)}
            body = {"settings": settings_mod.payload(self.app_cfg), "reload": reload}
            if key == "basemap_layer":                   # 地図の PNG（`map_image`）もこの値を読む
                self.basemap = self.app_cfg[key]
                body["basemap"] = self.basemap_payload()
            return 200, body


    def file_action(self, path: str, body: "dict[str, Any]") -> "tuple[int, dict[str, Any]]":
        """「設定」の枠の「ファイル」の口（I-257 の C）。失敗は 422＋画面の 1 文 `message`。"""
        text = body.get("text")
        try:
            if path == "/api/settings/load_sim":
                if not isinstance(text, str):
                    return 400, {"error": "bad body"}
                version = self.worker.submit(files_mod.sim_changes(text))
                return 200, {"message": i18n.t("dlg_settings_ok"), "version": version}
            if path == "/api/settings/load_app":
                if not isinstance(text, str):
                    return 400, {"error": "bad body"}
                values = config.select_app(files_mod.load_object(text))
                with self._settings_lock:
                    applied, reload = settings_mod.apply_many(self.app_cfg, values)
                    if applied:
                        self.basemap = self.app_cfg.get("basemap_layer", self.basemap)
                    payload = settings_mod.payload(self.app_cfg)
                word = "dlg_app_settings_ok" if applied else "dlg_app_settings_none"
                return 200, {"message": i18n.t(word), "settings": payload, "reload": reload,
                             "basemap": self.basemap_payload()}
            if path == "/api/lang_template":
                return 200, {"message": files_mod.write_lang_template(self.opener)}
            if path == "/api/cache/breakdown":
                return 200, files_mod.cache_breakdown()
            if path == "/api/cache/delete":
                ids = body.get("sources")
                if not (ids is None or (isinstance(ids, list) and all(isinstance(i, str) for i in ids))):
                    return 400, {"error": "bad body"}
                return 200, {"message": files_mod.cache_delete(ids, bool(body.get("basemap", True)))}
            if path == "/api/help/docs":
                docs_mod.open_docs(self.opener)
                return 200, {"message": ""}
            if path == "/api/help/check_updates":
                with self._settings_lock:
                    cfg = dict(self.app_cfg)
                return support.check_updates(cfg)
            if path == "/api/help/diagnostics":
                return 200, support.diagnostics_form()
            if path == "/api/help/diagnostics/zip":
                return support.diagnostics_zip(body.get("facts"), body.get("results"))
        except files_mod.FileError as e:
            return 422, {"message": str(e)}
        return 404, {"error": "not found"}


#: `file_action` が受ける口。
_FILE_PATHS = frozenset({"/api/settings/load_sim", "/api/settings/load_app",
                         "/api/lang_template", "/api/cache/breakdown", "/api/cache/delete",
                         "/api/help/docs", "/api/help/check_updates", "/api/help/diagnostics",
                         "/api/help/diagnostics/zip"})


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
                body = f.read()
            if os.path.basename(full) == "index.html":
                body = titled_page(body)
            self._send(200, body, ctype)

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
            if parts.path == "/api/project/save":
                self._body()                             # 画面は `{}` を送る＝読み捨てる
                inputs, sheets, profile = app.worker.state.document()
                text, warnings = project_io.dump(inputs, sheets, app.project_meta, profile)
                return self._json(200, {"text": text, "name": project_io.filename(app.project_meta),
                                        "warnings": warnings})
            if parts.path == "/api/sheet/csv":
                self._body()                             # 読み捨てる（残すと次の要求として読まれる）
                got = app.worker.sheet_run()
                if got is None:                          # 計算中・入力待ち・古い版
                    return self._json(409, {"message": i18n.t("webui_no_result")})
                name, text = exports.sheet_csv(*got)
                return self._json(200, {"text": text, "name": name})
            if parts.path == "/api/sheet/kml":
                self._body()                             # 読み捨てる（CSV と同じ）
                got = app.worker.sheet_run()
                if got is None:
                    return self._json(409, {"message": i18n.t("webui_no_result")})
                made = exports.sheet_kml(*got)
                if made is None:                         # 中継・条件探索＝旧画面にも無い
                    return self._json(409, {"message": i18n.t("webui_no_kml")})
                return self._json(200, {"text": made[1], "name": made[0]})
            if parts.path == "/api/image/map":
                self._body()                             # 読み捨てる（CSV と同じ）
                return self._json(*app.map_image())
            if parts.path == "/api/settings":
                body = self._body()
                if body is None or "key" not in body or "value" not in body:
                    return self._json(400, {"error": "bad body"})
                return self._json(*app.apply_setting(body["key"], body["value"]))
            if parts.path in _FILE_PATHS:
                body = self._body()
                if body is None:
                    return self._json(400, {"error": "bad body"})
                return self._json(*app.file_action(parts.path, body))
            if parts.path == "/api/project/open":
                body = self._body()
                text = body.get("text") if body is not None else None
                if not isinstance(text, str):
                    return self._json(400, {"error": "bad body"})
                from report.project import ProjectError
                try:
                    inputs, sheets, meta, profile = project_io.load(text)
                except ProjectError as e:
                    return self._json(422, {"message": str(e)})
                app.project_meta = meta
                version = app.worker.submit(inputs, sheets=sheets, profile=profile)
                return self._json(200, {"version": version})
            self._send(404, b"not found", "text/plain; charset=utf-8")

    return Handler
