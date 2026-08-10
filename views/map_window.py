
"""
views/map_window.py
===================
マップウィンドウ。地図を軸にした補助レイヤ機能のホスト。

上部のモードセレクタでモードを切り替える。

- キャッシュ管理モード: 地図上で bbox を指定し DEM タイルの
    - カバレッジ確認（色付きオーバーレイ表示）
    - 不足タイルのダウンロード
    - 範囲削除 / 全削除
  を行う。
- 座標入力モード（Phase B）: 地図を素クリックして TX→RX を交互にピックし、
  ランチャーの座標欄（start/end）へ書き戻す。数値欄が常に source of truth。
- 連続追加モード（Phase D2）: TX→RX ペア成立ごとにバッチへ 1 行を append し自動
  リセットする。append 先のバッチウィンドウはモード移行時に開く/前面化する。

**MapWindow はアプリ（ランチャー）が持つ唯一のインスタンス**。「バッチからも地図を
開く」という第2インスタンスは作らない（ランチャーが本筋・バッチは従属する受け皿）。
ピック結果の宛先は 2 種類の「座標シンク」で表現する：
- 単一書き戻し（ランチャー）= `_SingleSink`（`apply_map_pick`/`current_path_coords`）。
  構築時に固定で受け取る。
- 連続 append（バッチ）= `_AppendSink`（`append_path`/`existing_paths`）。連続追加
  モードに入ったとき `append_provider()` で受け皿を開いて受け取り、抜けると手放す。
"""

import os
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable, NamedTuple, Protocol, cast

from PIL import ImageTk

from core import dem
from core import i18n
from report import map_graphics
from tkintermapview import TkinterMapView
from views import theme, window_fit
from views.map_cache import _CacheMixin
from views.map_picks import _PickMixin
from views.progress import ProgressPump

logger = __import__("logging").getLogger("radiosim")

# ⚠️ `_LEVEL_COLORS`（zoom-14 オーバーレイの色）は
# [views/map_style.py](map_style.py) へ移した＝**使うのは `map_cache.py` だけ**
# なのにここに残っており、あちらから見えていなかった（2.7 スライス A の分割で
# 生まれた `NameError`。`ruff` の F821 が指していた）。


# 背景タイル（**2 択だけ**・I-028）。どちらも地理院タイル＝外部 API は GSI 一本の
# まま（設計哲学④）。**標準地図・白地図・陰影起伏…と足せる作りにしない**＝足せる
# 作りにすると次に必ず「全部出せるように」が来る（YAGNI の釘）。
#
# 航空写真を足す理由＝`veg_h` と `env_type` は今のところ利用者の当てずっぽうで、
# ここが数 dB〜十数 dB を動かす。写真で市街地／杉林／水面を目で決められると
# **新しい出力を増やさずに既存入力の質が上がる**。
# ⚠️ **見て確認するためだけ**＝写真から入力へ値を流す導線は作らない。
class _TileLayer(NamedTuple):
    url:       str
    max_zoom:  int
    label_key: str          # 選択欄に出す名前（i18n キー）
    attr_key:  str          # 出典表記（i18n キー）＝**タイルと対で持つ**


_TILE_LAYERS: dict[str, _TileLayer] = {
    "pale": _TileLayer(
        "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
        18, "map_layer_pale", "tm_attr_pale"),
    "photo": _TileLayer(
        "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg",
        18, "map_layer_photo", "tm_attr_photo"),
}
_DEFAULT_LAYER = "pale"

# 出典表記の配色。**背景を持たせるのは切り替え対策**＝淡色地図（明るい）と
# 航空写真（暗い・多色）では地の色が真逆で、地に直接描くとどちらかで必ず
# 読めなくなる（B-009＝ダークでツールチップが判読不能、と同型）。ウィジェットに
# 背景色を持たせれば**レイヤに依存しない 1 経路**で済む。
# ⚠️ ここは地図の上＝**アプリのテーマではなくタイルの上での可読性**で決める
# （sv_ttk のダークに合わせると航空写真の暗部で沈む）。
_ATTR_FG = "#333333"
_ATTR_BG = "#FFFFFF"

# 開いたとき設定中 TX/RX を収めるよう自動ズームする際のパラメータ。

# 座標シンク（ピック結果の受け手）の 2 形態。実装はランチャー／バッチビルダー。
# 共有メソッドは無く capability で分岐するため、別々の Protocol として定義する。
class _SingleSink(Protocol):
    """単一書き戻しシンク（ランチャー）: ピックごとに start/end 欄へ反映する。"""
    def apply_map_pick(self, role: str, lat: float, lon: float) -> None: ...
    def current_path_coords(self) -> dict: ...


class _AppendSink(Protocol):
    """連続 append シンク（バッチ・Phase D2）: ペア成立ごとに 1 行を追加する。"""
    def append_path(self, tx: tuple, rx: tuple) -> str: ...
    def existing_paths(self) -> list[tuple]: ...


class _WaypointSink(Protocol):
    """中継点シンク（中継経路・A-3）: クリックごとに**1 点ずつ順に**足す。

    ペア（TX/RX）ではなく**点**を足すのが append シンクとの違い＝中継経路は
    「順に並んだ地点の列」で、区間はその導出物だから（⑦）。

    `waypoint_markers` は地図が**写しを描き直す**ための現在の地点列
    （`existing_paths` と同じ役割＝地図は source of truth を持たない）。
    """
    def append_waypoint(self, lat: float, lon: float) -> str: ...
    def waypoint_markers(self) -> "list[tuple[str, float, float]]": ...


# tkintermapview の after ループを止めてからウィジェットを破棄するまでの猶予 [ms]。
# キュー済みの直近 after（10ms 周期）が widget 生存中に消化される時間。
_MAP_DRAIN_MS = 60


def close_map_safely(scheduler, map_widget, destroy) -> None:
    """TkinterMapView を抱えるウィンドウを安全に閉じる**唯一の手順**。

    tkintermapview は `update_canvas_tile_images` を 10ms ごとに自己再スケジュール
    する。`destroy()` は `running=False` にするが**既にキュー済みの直近 after は
    取り消さない**ため、破棄直後に発火して
    `invalid command name ...update_canvas_tile_images` を出す。そこで

      1. `running=False` で再スケジュールを断ち、
      2. `_MAP_DRAIN_MS` だけ待って（キュー済み after が widget 生存中に無害消化
         されるのを待って）から `destroy` を実行する。

    マップを破棄し得る経路（マップウィンドウの×・親ランチャー終了・将来の新規経路）
    は**必ずこの関数を通すこと**。各所に手順をコピーすると今回のように猶予だけ抜け
    落ちて再発する（[[feedback-radiosim-rules]]）。破棄経路が増えていないことは
    tests/test_map_window.py::test_map_widget_is_destroyed_only_through_close_map_safely
    が静的に検査する（新経路を足すときは _ALLOWED_MAP_DESTROY も更新すること）。

    引数:
      scheduler  : `.after(ms, cb)` を持つ生存中の tk ウィジェット（破棄予定の親など）。
      map_widget : TkinterMapView 実体（`running` 属性を持つ）。
      destroy    : 猶予後に呼ぶ実破棄クロージャ。
    """
    try:
        map_widget.running = False
    except Exception:
        pass
    try:
        scheduler.after(_MAP_DRAIN_MS, destroy)
    except Exception:
        destroy()


class MapWindow(_PickMixin, _CacheMixin):
    # ウィンドウ既定サイズ（**下限**。実寸は window_fit が中身から決める）。
    _BASE_W = 900
    _BASE_H = 680

    def __init__(
        self,
        parent: tk.Misc,
        config: dict,
        single_sink: object = None,
        append_provider: "Callable[[], object] | None" = None,
        waypoint_provider: "Callable[[], object] | None" = None,
    ) -> None:
        self._config = config
        # 座標入力モード（single）の書き戻し先。構築時固定（ランチャー）。
        self._single_sink: "_SingleSink | None" = (
            cast("_SingleSink", single_sink) if single_sink is not None else None
        )
        # 連続追加モードの append 先を開いて返すプロバイダ（ランチャー → バッチ）。
        # 連続追加モードに入ったときに呼び、受け皿（_AppendSink）を取得する。
        self._append_provider = append_provider
        # 中継点モードの宛先を開いて返すプロバイダ（ランチャー → 中継経路）。
        # **append と同じ形にする**＝モードセレクタから直接この モードを選んでも
        # 受け皿が用意される。以前は宛先が None のままで、クリックしても
        # 「何も起きない・メッセージも出ない」死んだモードになっていた。
        self._waypoint_provider = waypoint_provider
        # 現在保持している append 先（連続追加モードのときのみ非 None）。
        self._append_sink: "_AppendSink | None" = None
        self._sync_proxy()

        self._win = tk.Toplevel(parent)
        self._win.title(i18n.t("map_window_title"))
        # タイル取得の進捗は単一/バッチと同じ部品で受ける（実行のあいだだけ回す）。
        self._pump = ProgressPump(self._win, self._render_progress, latest_only=True)
        # 既定サイズは**下限**（実寸は _build_ui の最後に中身から決める）。
        self._win.minsize(720, 520)

        # 現在のモード。"cache"=キャッシュ管理 / "coords"=座標入力（Phase B）。
        # 既定は coords＝座標入力（マップ連携の主機能。タイル管理は補助レイヤ）。
        self._mode = tk.StringVar(value="coords")

        # 座標入力モードの状態。次にどちらを置くか（交互）と、TX/RX のマーカー・線。
        self._pick_next = "tx"
        # 中継点モードの宛先（中継経路ウィンドウ）。モードを抜けたら手放す。
        self._waypoint_sink: "_WaypointSink | None" = None
        self._tx_coord: tuple | None = None
        self._rx_coord: tuple | None = None
        self._tx_marker = None
        self._rx_marker = None
        self._path_line = None
        self._dist_label = None         # path 中点の水平距離ラベル（マーカー）
        # 追記モードで既に確定したパスの地図オブジェクト（マーカー・線・距離バッジ）。
        # 座標値は持たない（バッチ表が source of truth）。毎回引き直す。
        self._committed: list = []
        # 確定パスの PhotoImage（距離バッジ・RX 矢じり）保持リスト（GC 防止）。
        self._committed_images: list = []
        # 端点のノードアイコン（半透明ハロー＋シアンノード。TX=塗り / RX=白抜き）。
        # PhotoImage は GC されると消えるためインスタンスに保持する。
        self._tx_icon = self._make_node_icon(hollow=False)
        self._rx_icon = self._make_node_icon(hollow=True)
        # 中継点＝リング（送信点・受信点と**形で**区別する）。
        self._relay_icon = ImageTk.PhotoImage(map_graphics.relay_icon())
        # 中継経路レイヤ（折れ線＋地点マーカー）。窓の地点列を写すだけで持たない。
        self._wp_objects: list = []
        # 距離ラベルのバッジ画像（テキスト＋半透明ピル背景）。距離ごとに作り直す。
        # PhotoImage は GC されると消えるためインスタンスに保持する。
        self._dist_badge = None

        self._bbox_polygon = None
        self._tile_polygons: list = []

        # Ctrl＋ドラッグ矩形選択の状態
        self._sel_start = None    # (lat, lon) ドラッグ開始点
        self._sel_rect  = None    # ドラッグ中のライブ矩形ポリゴン

        self._lat1_var = tk.StringVar()
        self._lon1_var = tk.StringVar()
        self._lat2_var = tk.StringVar()
        self._lon2_var = tk.StringVar()

        self._busy = False              # DL 実行中フラグ（多重操作防止）
        self._overlay_after_id = None   # 自動カバレッジ表示のデバウンス用
        self._status_clear_id = None    # 結果文の自動クリア用 after ID

        self._closing = False
        self._init_after_id = None      # 初回レイヤ描画の after ID（早期クローズ対策）
        self._build_ui()
        self._refresh_stats()
        # 既存の TX/RX 座標（数値欄）を取り込み、地図中心を合わせる。
        self._load_single_coords()
        # 地図レイアウト確定後に現在モードのレイヤを描画する
        # （cache=カバレッジ / coords=経路。既定は coords）。
        self._init_after_id = self._win.after(600, self._apply_mode_visibility)
        # 閉じるときは after ループを止めてから破棄する（下記 _on_close 参照）。
        self._win.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------------
    # クローズ処理（tkintermapview の after ループを安全に停止）
    # ----------------------------------------------------------
    def _on_close(self) -> None:
        """ウィンドウを閉じる。マップの after ループを止めてから破棄する手順は
        `close_map_safely` に集約（[[feedback-radiosim-rules]]）。"""
        if self._closing:
            return
        self._closing = True
        # 進捗ポーリングを止める（破棄済みウィジェットへの after を避ける）。
        self._pump.stop()
        # 自前の after ジョブを取り消す。
        for aid in (self._overlay_after_id, self._status_clear_id,
                    self._init_after_id):
            if aid is not None:
                try:
                    self._win.after_cancel(aid)
                except Exception:
                    pass
        self._overlay_after_id = self._status_clear_id = None
        self._init_after_id = None
        close_map_safely(self._win, self._map, self._destroy)

    def _destroy(self) -> None:
        try:
            self._map.destroy()
        except Exception:
            pass
        try:
            self._win.destroy()
        except Exception:
            pass

    # ----------------------------------------------------------
    # プロキシ env 変数同期
    # ----------------------------------------------------------
    def _sync_proxy(self) -> None:
        proxy = self._config.get("proxy_url", "").strip()
        if proxy:
            os.environ["HTTP_PROXY"]  = proxy
            os.environ["HTTPS_PROXY"] = proxy
        else:
            os.environ.pop("HTTP_PROXY",  None)
            os.environ.pop("HTTPS_PROXY", None)

    # ----------------------------------------------------------
    # モード切替（Phase A はキャッシュ管理のみ。Phase B で分岐を追加）
    # ----------------------------------------------------------
    def _select_mode(self, value: str) -> None:
        """モードを選択し、セグメントボタンのスタイルを更新する。"""
        self._mode.set(value)
        for v, btn in self._mode_buttons.items():
            btn.configure(style="Accent.TButton" if v == value else "TButton")
        self._on_mode_change()

    def start_waypoint_mode(self, sink: object) -> None:
        """宛先を指定して中継点モードへ入る（中継経路窓の「地図から選択」）。

        **公開口にしてある**＝呼び出し側が `_waypoint_sink` や `_select_mode` を
        直接触らない（内部名に依存した配線は、5b の `getattr(self.master, …)` と
        同じ形で黙って壊れる）。宛先を先に据えるので、モード遷移側の
        `waypoint_provider`（窓を開いて受け皿を作る経路）は走らない。
        """
        self._waypoint_sink = cast("_WaypointSink", sink)
        self._select_mode("waypoints")
        self._win.lift()

    def start_append_mode(self) -> None:
        """連続追加モードへ入る（複数経路の窓の「地図から取る」・I-043）。

        `start_waypoint_mode` と同じ理由で**公開口にしてある**＝呼び出し側が
        `_select_mode` のような内部名に依存しない。宛先（受け皿）は
        `_on_mode_change` が `append_provider` から取るので、ここでは渡さない
        ——**受け皿を開くのはランチャーの仕事**という既存の分担をそのまま使う。
        """
        self._select_mode("append")
        self._win.lift()

    def _on_mode_change(self) -> None:
        # UI 構築中（ステータスバー未生成）に呼ばれる初期スタイル反映時は何もしない。
        if not hasattr(self, "_status_label"):
            return
        if self._mode.get() == "append":
            # 連続追加に入る＝受け皿（バッチ）を開いて append 先を確保する。
            sink = self._append_provider() if self._append_provider else None
            if sink is None:
                # 受け皿が用意できないときは座標入力へ戻す（_select_mode が再入し
                # 描画・ヒント更新まで行うのでここでは return）。
                self._select_mode("coords")
                return
            self._append_sink = cast("_AppendSink", sink)
            self._reset_active_pick()           # アクティブピックは持たず TX 待ちから
            self._fit_to_existing_paths()       # 既存パス群へズームを合わせる
            self._win.lift()                    # 受け皿を開いた後、地図を前面へ戻す
        elif self._mode.get() == "waypoints":
            # 中継点モード＝連続追加と同じ約束（受け皿が無ければ開いて確保する）。
            # 既に宛先を持っている場合（中継経路窓の「地図から選択」から来た場合）は
            # それを尊重する＝窓を開き直さない。
            self._append_sink = None
            if self._waypoint_sink is None:
                sink = self._waypoint_provider() if self._waypoint_provider else None
                if sink is None:
                    self._select_mode("coords")
                    return
                self._waypoint_sink = cast("_WaypointSink", sink)
            self._win.lift()
        else:
            self._append_sink   = None          # 連続追加を抜けたら append 先を手放す
            self._waypoint_sink = None          # 中継点モードを抜けたら同様
            if self._mode.get() == "coords":
                # 連続追加で消したアクティブピックを、ランチャーの現在値で復元する。
                self._load_single_coords()
        self._apply_mode_visibility()
        # モードに応じてアイドルヒントを切り替える（_set_idle がモードを見る）。
        self._set_idle()

    def _apply_mode_visibility(self) -> None:
        """各モードは自分の関心レイヤだけを表示する（モードが見た目を決める）。

        - cache      : キャッシュカバレッジを描画し、経路レイヤは隠す。
        - coords/append : 経路レイヤ（マーカー/線/距離・確定パス）を描画し、塗りは隠す。
        - waypoints  : **中継経路のレイヤだけ**（TX/RX のピックや確定パスは出さない）。

        座標値（_tx_coord/_rx_coord）は保持するのでモードを往復しても失われない。
        ⚠️ **中継点レイヤはモードを抜けたら必ず消す**＝どのモードでも出しっぱなしに
        なっていた（2026-08-01 実機確認）。モードが見た目を決めるという原則から
        中継点だけが外れていた。
        """
        if self._mode.get() == "cache":
            self._clear_coord_visuals()
            self._clear_waypoint_visuals()
            self._refresh_overlay()
        elif self._mode.get() == "waypoints":
            self._clear_tile_overlays()
            self._clear_coord_visuals()
            self._refresh_waypoints()
        else:
            self._clear_tile_overlays()
            self._clear_waypoint_visuals()
            self._show_coord_visuals()
            self._refresh_committed_paths()

    # ----------------------------------------------------------
    # 中継経路レイヤ（中継点モード）
    # ----------------------------------------------------------
    # **地図は source of truth を持たない**＝中継経路ウィンドウの地点列を毎回
    # 写して描き直すだけ（確定パス表示＝`_refresh_committed_paths` と同じ流儀）。
    # 以前はクリックのたびに `set_marker` を足すだけだったので、**窓側で地点を
    # 削除しても地図のピンが残り**、消し方も持っていなかった（2026-08-01 実機確認）。


    # ----------------------------------------------------------
    # UI 構築
    # ----------------------------------------------------------
    def _build_ui(self) -> None:
        # ---- 上部モードセレクタ（セグメントボタン列）---------------------
        # 地図を軸にした補助機能のモードを切り替える。選択中モードは Accent
        # （青塗り）で押下状態を表し、ボタンとして明確に認識できるようにする。
        # Phase A はキャッシュ管理の 1 つのみ。Phase B でリストに「座標入力」を足す。
        modebar = ttk.Frame(self._win)
        modebar.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(modebar, text=i18n.t("map_mode_label")).pack(side="left", padx=(2, 6))
        self._mode_buttons: dict[str, ttk.Button] = {}
        # 座標入力＝主機能なので左に並べる。連続追加（バッチへ）はその隣。
        for value, key in [
            ("coords", "map_mode_coords"),
            ("append", "map_mode_append"),
            ("waypoints", "map_mode_waypoints"),
            ("cache",  "map_mode_cache"),
        ]:
            b = ttk.Button(
                modebar, text=i18n.t(key),
                command=lambda v=value: self._select_mode(v),
            )
            b.pack(side="left", padx=2)
            self._mode_buttons[value] = b
        self._select_mode(self._mode.get())   # 初期選択のスタイルを反映

        # 背景の 2 択（I-028）。**モードではないので見た目を変える**＝モードは
        # セグメントボタン、こちらは Combobox。同じ形にすると「4 つ目のモード」に
        # 見えて、選ぶ軸が 2 本あることが読み取れなくなる。
        ttk.Label(modebar, text=i18n.t("map_layer_label")).pack(side="left", padx=(16, 4))
        self._layer_labels = {
            i18n.t(spec.label_key): key
            for key, spec in _TILE_LAYERS.items()
        }
        self._layer_box = ttk.Combobox(
            modebar, values=list(self._layer_labels), state="readonly", width=10)
        self._layer_box.set(i18n.t(_TILE_LAYERS[_DEFAULT_LAYER].label_key))
        self._layer_box.bind("<<ComboboxSelected>>", self._on_layer_changed)
        self._layer_box.pack(side="left")

        self._map = TkinterMapView(self._win, corner_radius=0)
        self._layer = _DEFAULT_LAYER
        self._map.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        self._map.set_position(35.68, 139.77)
        self._map.set_zoom(8)
        # 座標入力モードでの素クリック → ピック。ライブラリがドラッグ（パン）と
        # クリックを内部で区別するため、キャッシュ管理の修飾キー操作とは競合しない。
        self._map.add_left_click_map_command(self._on_map_click)

        cv = self._map.canvas
        # パン/ズーム終了時にカバレッジを自動再描画する。
        # tkintermapview 自身が canvas にバインド済みのため add="+" で相乗りする。
        for seq in ("<ButtonRelease-1>", "<MouseWheel>", "<Button-4>", "<Button-5>"):
            cv.bind(seq, self._schedule_overlay_refresh, add="+")
        # ジェスチャ（素のドラッグはパン）。tkinter は具体的なバインドを優先するため
        # 各修飾キー付きドラッグとパンは競合しない。
        #   Ctrl＋ドラッグ        = ダウンロード（通常）
        #   Ctrl+Alt＋ドラッグ    = ダウンロード（強制再取得）
        #   Shift+Ctrl＋ドラッグ  = 範囲削除
        for seq in ("<Control-Button-1>", "<Control-Alt-Button-1>", "<Shift-Control-Button-1>"):
            cv.bind(seq, self._sel_press, add="+")
        for seq in ("<Control-B1-Motion>", "<Control-Alt-B1-Motion>", "<Shift-Control-B1-Motion>"):
            cv.bind(seq, self._sel_drag, add="+")
        cv.bind("<Control-ButtonRelease-1>",
                lambda e: self._sel_release(e, "download"), add="+")
        cv.bind("<Control-Alt-ButtonRelease-1>",
                lambda e: self._sel_release(e, "download_force"), add="+")
        cv.bind("<Shift-Control-ButtonRelease-1>",
                lambda e: self._sel_release(e, "delete"), add="+")

        # 出典表記（GSI 帰属）は地図右下にオーバーレイ表示する（地図出典の慣例位置）。
        #
        # ⚠️ **canvas のテキストではなく「置いたウィジェット」にする**（2.6a5・B-027）。
        # 従来は `create_text` で canvas に直接描いており、`<Configure>` のたびに
        # `tag_raise` していたが、**タイル画像は後から canvas に足される**ので
        # 描画のたびに埋もれた＝実画面では出典が 1 度も見えていなかった
        # （実機スクショで発覚。地理院タイルの出典表記は表示義務がある）。
        # 子ウィジェットは canvas の中身より常に上に描かれるので、`place` すれば
        # z 順の争いが構造的に消える（背景色もウィジェットが持てる＝下敷き不要）。
        self._attribution = tk.Label(
            self._map, text=i18n.t(_TILE_LAYERS[_DEFAULT_LAYER].attr_key),
            fg=_ATTR_FG, bg=_ATTR_BG, font=theme.ui_font(self._win, "small"),
            padx=4, pady=1,
        )
        self._attribution.place(relx=1.0, rely=1.0, anchor="se", x=-4, y=-4)
        self._apply_layer(_DEFAULT_LAYER)

        # ---- 下部ステータスバー（1 本に集約）----------------------------
        # 出没でレイアウトが動かないよう、各要素の高さを予約して配置する。
        bottom = ttk.Frame(self._win)
        bottom.pack(fill="x", padx=6, pady=(2, 4))

        statusbar = ttk.Frame(bottom)
        statusbar.pack(fill="x")

        # 右: キャッシュ統計（常時表示のアンカー）
        self._stats_var = tk.StringVar(value="")
        ttk.Label(statusbar, textvariable=self._stats_var, anchor="e").pack(side="right")

        # 左: 動的メッセージ（アイドル時=操作ヒント / 操作中・直後=状態・結果）。
        # 複数行になり得るため justify=left。アイドル時はグレー表示。
        self._status_var = tk.StringVar(value="")
        self._status_label = ttk.Label(
            statusbar, textvariable=self._status_var, anchor="w", justify="left",
            wraplength=600,
        )
        self._status_label.pack(side="left", fill="x", expand=True)
        # 幅に追従して折り返し幅を更新（統計表示分を右に確保する）。
        statusbar.bind(
            "<Configure>",
            lambda e: self._status_label.config(wraplength=max(200, e.width - 200)),
        )

        # プログレスバー: 細線。アイドル時も高さを予約して畳み、DL 中のみ表示する。
        style = ttk.Style()
        style.configure("Thin.Horizontal.TProgressbar", thickness=6)
        self._progress_holder = ttk.Frame(bottom, height=8)
        self._progress_holder.pack(fill="x", pady=(2, 0))
        self._progress_holder.pack_propagate(False)   # 中身の有無に関わらず高さ固定
        self._progress_var = tk.IntVar(value=0)
        self._progress = ttk.Progressbar(
            self._progress_holder, variable=self._progress_var, maximum=100,
            style="Thin.Horizontal.TProgressbar",
        )

        self._set_idle()   # 起動時はヒントを表示

        # 窓を中身に合わせる（既定 900x680 は下限）。地図そのものは伸縮するが、
        # モードバー・ステータス行・各モードのパネルは言語とフォントで幅が変わる
        # ＝ここを固定寸法のままにすると他の窓とまったく同じ形で見切れる
        # （B-002 / I-000 / I-023 / I-024 と同じクラス。2.5b2 の横断ゲート追加で
        # 「この窓だけ実測追従になっていない」ことが分かった）。
        window_fit.fit_to_content(self._win, min_w=self._BASE_W, min_h=self._BASE_H)


    # ----------------------------------------------------------
    # 背景タイル（淡色地図 / 航空写真）
    # ----------------------------------------------------------
    def _on_layer_changed(self, _event=None) -> None:
        self._apply_layer(self._layer_labels[self._layer_box.get()])

    def _apply_layer(self, key: str) -> None:
        """背景タイルを切り替え、**出典表記も一緒に変える**（I-028）。

        ⚠️ 表記の追従がこの関数の本体＝タイルだけ替えると「航空写真を見ながら
        『出典: 淡色地図』」という*事実と食い違う刻印*になる。1 か所で両方を
        変えることで、片方だけ直す余地を残さない。

        写真には地名が写らないので、目的地まで地図を送るのは淡色のほうが速い。
        **常時 2 択で行き来できること**が要件で、「写真＋注記の重ね合わせ」は
        2 レイヤ同時描画になり `tkintermapview` の作りから見て割に合わない＝やらない。
        """
        spec = _TILE_LAYERS[key]
        self._layer = key
        self._map.set_tile_server(spec.url, max_zoom=spec.max_zoom)
        self._attribution.config(text=i18n.t(spec.attr_key))


        # ⚠️ ここには「カバレッジ描画で上に来るため出典を持ち上げ直す」という
        # 1 行があった。**持ち上げ直しが要る時点で z 順の争いに負けている**
        # （実際タイル画像のほうは拾えておらず、出典は一度も見えていなかった＝
        # B-027）。出典を `place` したウィジェットにしたので争い自体が消えた。

    # ----------------------------------------------------------
    # ダウンロード（Ctrl＋ドラッグ → 確認 → 実行）
    # ----------------------------------------------------------
    # 1 エリア = 最大 4 サブタイル（5m系）と見なした安全側の容量見積り。
    _TILES_PER_AREA = 4
    _DEFAULT_TILE_BYTES = 25 * 1024   # 実キャッシュが無いときのフォールバック


    # ----------------------------------------------------------
    # ステータス表示ヘルパー
    # ----------------------------------------------------------
    _STATUS_CLEAR_MS = 8000   # 結果文を自動的に消すまでの時間

    def _show_progress(self) -> None:
        """細線プログレスバーを高さ予約済みのホルダー内に表示する。"""
        if not self._progress.winfo_ismapped():
            self._progress.pack(fill="x")

    def _hide_progress(self) -> None:
        # ホルダーは pack_propagate(False) で高さを保つため、外してもリフローしない。
        if self._progress.winfo_ismapped():
            self._progress.pack_forget()

    def _set_idle(self) -> None:
        """アイドル状態: モードに応じた操作ヒントをグレーで表示する。"""
        self._status_clear_id = None
        # MapWindow は tk ウィジェットではない（ウィンドウを内包する素のクラス）ので、
        # テーマ問い合わせにはラベル自身を渡す。
        self._status_label.config(foreground=theme.muted_foreground(self._status_label))
        mode = self._mode.get()
        if mode == "cache":
            self._status_var.set(i18n.t("tm_hint"))
        elif mode == "append":
            key = "map_append_hint_tx" if self._pick_next == "tx" else "map_append_hint_rx"
            self._status_var.set(i18n.t(key))
        elif mode == "waypoints":
            # ⚠️ ここが無いと**中継点モードなのに「TX を指定します」**と出る
            # （2026-08-01 の実機スクリーンショットで判明）。モードごとの分岐を
            # 足すときは、ヒント・表示レイヤ・宛先の 3 か所を揃えて足すこと。
            self._status_var.set(i18n.t("map_status_waypoint"))
        else:
            key = "map_coords_hint_tx" if self._pick_next == "tx" else "map_coords_hint_rx"
            self._status_var.set(i18n.t(key))

    def _set_status(self, text: str, auto_clear: bool = False) -> None:
        """状態・結果文を通常色で設定。auto_clear=True なら一定時間後にヒントへ戻す。"""
        if self._status_clear_id is not None:
            self._win.after_cancel(self._status_clear_id)
            self._status_clear_id = None
        self._status_label.config(foreground="")   # テーマ既定色に戻す
        self._status_var.set(text)
        if auto_clear and text:
            self._status_clear_id = self._win.after(self._STATUS_CLEAR_MS, self._set_idle)


    def _on_download_done(self, dl_result: dict) -> None:
        self._pump.stop()
        self._set_busy(False)
        self._hide_progress()
        self._set_status(i18n.t("tm_dl_done").format(
            dl5a=dl_result["downloaded_5a"],
            dl5b=dl_result["downloaded_5b"],
            dl_dem=dl_result["downloaded_dem"],
            skipped=dl_result["skipped"],
            failed=dl_result["failed"],
        ), auto_clear=True)
        # ⚠️ ここから先は進捗表示を消した後にメインスレッドで走る区間。
        # _refresh_stats はキャッシュ全体を走査するのでファイル数に比例して伸びる
        # （B-006／I-008 と同型の「進捗を消してから重い処理」）。所要をログに残し、
        # 無視できない大きさになったら段階ラベルを出す判断ができるようにする。
        t0 = time.perf_counter()
        self._refresh_stats()
        self._refresh_overlay()   # DL 結果を自動カバレッジ表示に反映
        self._clear_selection()   # DL 完了後は選択枠を消す
        logger.info("Post-download refresh complete in %.2fs",
                    time.perf_counter() - t0)

    def _clear_selection(self) -> None:
        """選択枠と座標をクリアする（ステータス文には触れない）。"""
        if self._bbox_polygon is not None:
            self._bbox_polygon.delete()
            self._bbox_polygon = None
        for var in (self._lat1_var, self._lon1_var, self._lat2_var, self._lon2_var):
            var.set("")

    # ----------------------------------------------------------
    # 範囲削除（Shift+Ctrl＋ドラッグ → 確認 → 実行）
    # ----------------------------------------------------------
    def _do_delete(self, bbox: tuple) -> None:
        result = dem.delete_tile_cache(*bbox)
        self._set_status(i18n.t("tm_delete_done").format(deleted=result["deleted"]), auto_clear=True)
        self._refresh_stats()
        self._refresh_overlay()   # 削除結果を自動表示に反映
        self._clear_selection()   # 削除後は選択枠を消す

    # ----------------------------------------------------------
    # 全削除（実行はランチャーの設定メニュー側。ここは外部削除後の再描画のみ）
    # ----------------------------------------------------------
    def on_external_delete_all(self, deleted: int) -> None:
        """ランチャーから全キャッシュ削除された後、開いている管理画面を更新する。"""
        self._clear_tile_overlays()
        self._set_status(i18n.t("tm_delete_all_done").format(deleted=deleted), auto_clear=True)
        self._refresh_stats()
        self._refresh_overlay()   # 全削除後の状態を自動表示に反映

    def on_external_cache_change(self) -> None:
        """ランチャーのシミュレーション等で外部からキャッシュが増減した後、
        開いている管理画面の統計とカバレッジ表示を更新する。"""
        self._refresh_stats()
        self._refresh_overlay()   # cache モードのみ反映（coords は早期 return）

    # ----------------------------------------------------------
    # キャッシュ統計
    # ----------------------------------------------------------
    def _refresh_stats(self) -> None:
        stats = dem.get_cache_stats()
        mb = stats["size_bytes"] / (1024 * 1024)
        self._stats_var.set(i18n.t("tm_stats").format(count=stats["count"], mb=f"{mb:.1f}"))

    # ----------------------------------------------------------
    # ビジー状態制御（DL 実行中は新たなジェスチャ操作を受け付けない）
    # ----------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
