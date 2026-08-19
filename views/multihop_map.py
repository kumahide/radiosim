"""
views/multihop_map.py
=====================
中継経路ウィンドウの**地図との受け渡し**（`MultiHopWindow` の Mixin）。

地図を中継点モードで開く口と、地図が写して描き直すための地点列、そして
地図から 1 点を足す／置き直す口（`_WaypointSink` の実装）をここに集める。

⚠️ **これは `MultiHopWindow` の一部**であって独立した部品ではない（切り出しの
流儀は [views/map_picks.py](map_picks.py) と同じ＝メソッド本文は動かしていない）。
切り出したのは I-098（地図で置き直す口を足したら分割閾値を越えた）＝**割る面は
「窓の中の機能」ではなく「窓と地図の境目」**を選んだ。境目に置くものは、地図の
側の約束（写しの並びで位置を指す・書き戻す前に照合する）とセットで読むのが
いちばん短いから。
"""

from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING, Callable

from core import coords
from core import i18n
from views import dialogs

# 宿主は `tk.Toplevel`（`dialogs.alert(self, …)` に渡す）。**型検査のときだけ**
# それを名乗る＝実行時は素の Mixin のまま（MRO も振る舞いも変わらない）。
# 流儀は [views/batch_table.py](batch_table.py) の同じブロックと同じ（B-049）。
if TYPE_CHECKING:
    _HostBase = tk.Toplevel
else:
    _HostBase = object


class _MapSinkMixin(_HostBase):
    # 宿主（`MultiHopWindow`）から借りている面の宣言。**型検査のときだけ**存在する
    # （実行時は 1 文字も定義しない）。理由は map_picks.py の同じブロック（B-049）。
    if TYPE_CHECKING:
        _wp_vars: list[dict[str, tk.StringVar]]
        _coord_format: str
        _map_opener: "Callable[[object], None] | None"
        _map_notify: "Callable[[], None] | None"

        def _add_waypoint(self, name: str = "", lat: "float | None" = None,
                          lon: "float | None" = None, h: "float | None" = None,
                          index: "int | None" = None) -> None: ...

    def _on_from_map(self) -> None:
        """地図から順に拾う（宛先をこの窓へ切り替える）。

        地図は**アプリ唯一のインスタンス**で、モードで宛先を切り替える設計
        （2.3 D2）。ここはその 3 つ目のシンク＝**1 点ずつ順に足す**。

        ⚠️ **親ウィジェットからメソッドを探さない**（`getattr(self.master, …)`）。
        `self.master` は Tk のルートで、ランチャー（`SimLauncher`）はウィジェット
        ではないため**必ず None になり、この機能は一度も動かなかった**。依存は
        バッチの `load_params`・条件探索の `config_provider` と同じく**注入**する。
        """
        if self._map_opener is None:
            dialogs.alert(self, i18n.t("dlg_input_error"), i18n.t("mh_err_no_map"))
            return
        self._map_opener(self)

    def _readable_waypoints(self) -> "list[tuple[int, str, float, float]]":
        """座標として読める地点だけを `(行の位置, 名前, lat, lon)` で返す。

        🔑 **写しを作る口と、写しの位置を解く口を 1 つにする**（I-098）。地図は
        「写しの並びで何番目か」しか持たないので、落とす規則が 2 か所にあると、
        片方だけ変わった瞬間に**地図が指した点と窓が動かす点がずれる**（B-068 /
        B-102 と同じ、黙って別の行を触る型）。
        """
        out: list[tuple[int, str, float, float]] = []
        for i, vars_ in enumerate(self._wp_vars):
            try:
                lat, lon = coords.parse_pair(vars_["coord"].get())
            except ValueError:
                continue
            out.append((i, vars_["name"].get(), lat, lon))
        return out

    def waypoint_markers(self) -> "list[tuple[str, float, float]]":
        """地図が描き直すための現在の地点列（読めない座標の行は落とす）。

        **地図は写すだけ**＝ここが唯一の出所（`_WaypointSink` の実装）。座標を
        入力途中の行は座標として読めないので出さない（実行時の検証とは別物＝
        地図の表示は「今読める点」で足りる）。
        """
        return [(name, lat, lon) for _i, name, lat, lon in self._readable_waypoints()]

    def update_waypoint(self, index: int, lat: float, lon: float,
                        expect: str) -> bool:
        """地図で選んだ地点を置き直す（`_WaypointSink` の実装・I-098）。

        `index` は `waypoint_markers()` の並びでの位置。**選んでから動かすまでの
        あいだに地点が消える・並びが変わることがある**ので、`expect`（選んだ
        時点の地点名）と食い違ったら**動かさずに断る**。⇒ 地図は「選び直して
        ください」と言う＝黙って別の地点を動かさない（B-068 / B-102 と同じ形）。
        """
        rows = self._readable_waypoints()
        if not 0 <= index < len(rows):
            return False
        row, name, _lat, _lon = rows[index]
        if name != expect:
            return False
        self._wp_vars[row]["coord"].set(
            coords.format_pair(lat, lon, self._coord_format))
        return True

    def _notify_map(self) -> None:
        """地点列が変わったことを地図へ知らせる（開いていて中継点モードのときだけ効く）。

        ⚠️ **削除も編集も通知する**＝追加のときだけ描くと、窓で消した地点が地図に
        残る（2026-08-01 実機確認）。複数経路 → 地図の `on_paths_changed` と同じ形。
        """
        if self._map_notify is not None:
            self._map_notify()

    def append_waypoint(self, lat: float, lon: float) -> str:
        """地図からの 1 点追加（`_WaypointSink` の実装）。

        空欄の地点があればそこを埋め、無ければ末尾に足す＝「TX と RX の枠だけ
        ある状態」から地図で順に埋めていける。
        """
        for vars_ in self._wp_vars:
            if not vars_["coord"].get().strip():
                vars_["coord"].set(
                    coords.format_pair(lat, lon, self._coord_format))
                return vars_["name"].get()
        # 空きが無ければ**中継点として**足す（受信点の手前＝_add_waypoint の約束）。
        before = len(self._wp_vars)
        self._add_waypoint(lat=lat, lon=lon)
        if len(self._wp_vars) == before:
            return ""                     # 上限に達していて足せなかった
        return self._wp_vars[len(self._wp_vars) - 2]["name"].get()
