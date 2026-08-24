"""
views/window_fit.py
===================
「ウィンドウを中身に合わせる」ための**唯一の実装**。

なぜ専用モジュールが要るか（2.5b2・負債の清算）
------------------------------------------------
見切れは本プロジェクトで**最も繰り返している不具合クラス**で、原因は毎回同じ
＝**窓の寸法をリテラルで持ち、中身が増えた日に黙って切れる**：

  - B-002（2.4）…… バッチの日本語見出しが見切れる
  - B-007（2.4）…… ランチャーの「マップウィンドウ」ボタンが窓外へ（高さ固定 900）
  - I-000（2.5a1）… バッチに水平距離列を足したら右端が見切れる（幅固定 1080）
  - I-023（2.5b2）… フォント統一でランチャーの必要幅が 464px になり詰まる（幅固定 450）
  - I-024（2.5b2）… 条件探索で条件 5 列目が窓外へ（幅固定 900・**あとから増える**）

そのつど「その窓だけ」を実測追従に直してきたので、**次の窓・次の増え方**で必ず
再発した。ここに寄せる目的は行数の削減ではなく、

  1. 測り方（要求サイズ・画面の上限・縮めない）を 1 か所に確定させること
  2. **決めた寸法を `win._fit_size` に残す**こと
     ＝窓を横断するゲート（tests/test_window_fit.py）が
     「どの窓も中身が収まっている」を**一律に**検査できるようにすること

の 2 点にある。②が肝で、これが無いと窓ごとに手書きのテストを足す運用になり、
新しい窓を足した人が書き忘れた時点で穴が空く（＝これまでの状態）。

使い方
------
窓を組み立てたあと、そして**中身が増減し得る操作のあと**に呼ぶ::

    fit_to_content(self, min_w=self._BASE_W, min_h=self._BASE_H)

`winfo_width()` を当てにしないこと：未表示のあいだ `1` を返すので、実現後の
サイズと比較するテストは壊れた実装でも緑になる（ランチャーで実際に踏んだ）。

画面に入らないときの逃げ道（2.6a1・B-021②／B-023）
--------------------------------------------------
上の「測って合わせる」だけでは、**中身が画面より大きい窓**を救えない。実機
（FHD・使える高さ 990px）ではランチャーが 100% で 1023px、125% で 1148px、
条件探索が 125% で 1095px を要求する＝どう測っても入らない。従来はここで黙って
クランプしていたので、**溢れた分は下端のウィジェットから削られていた**
（B-021＝最下段のボタン列が数 px の帯に潰れ、主要機能へ到達できなくなった）。

そこで窓の中身を `scrollable_body()` の受け皿へ入れる。入る間は今までと完全に
同じ見た目で（スクロールバーは出ない）、**入らなくなった時だけ**スクロールバーが
現れて全部に手が届く。**画面高がいくつであっても壊れない唯一の答え**なので、
窓ごとの手当て（ロゴを縮める・余白を削る）はここまでの繋ぎでしかない::

    body = window_fit.scrollable_body(self)   # 以後 self ではなく body へ pack する
    ...
    fit_to_content(self, ...)                 # 逃げ道の出し入れも面倒を見る

⚠️ **受け皿へ入れても `_fit_need`（必要量）は減らない**。減らすと「スクロール
できるから入っている」ことになり、**免除条項を別の場所に作り直すだけ**になる
（B-021 で 6 回目を通した仕掛けそのもの）。ゲートが「入らない」と言い続け、
そのうえで実害が消えている、という状態を保つ。

🔑 **受け皿そのものの実装は [views/window_scroll](window_scroll.py) にある**
（2026-08-24 に分けた）＝関心事が 2 つあるため（*測って合わせる* と *合わせても
入らなかったときに手が届く*）。**窓から見た口は今までどおり `window_fit` の
`scrollable_body()`** で、出し入れの判断も引き続き `fit_to_content` が握る。
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Any

# 逃げ道の実装（`scrollable_body` はここから公開し直す＝窓の側の呼び口は変えない）。
# ⚠️ `_ScrollEscape` も**同じクラス実体**を指す＝`window_fit._ScrollEscape.sync` を
# 差し替える探針（experiments/b119_frame_slip_probe.py）がそのまま効く。
from views.window_scroll import _ScrollEscape, scrollable_body  # noqa: F401

# 画面いっぱいまでは広げない（タスクバー・ウィンドウ枠のぶんを残す）。
# 従来はバッチ 80px / 条件探索 90px / ランチャー「画面の 92%」と窓ごとにばらけて
# いたので、ここで 1 つに揃える（値そのものより、揃っていることに意味がある）。
#
# 🔴 **これは「OS に聞けなかったとき」の見積り**になった（2026-08-18・B-084）。
# 90px は 100% 表示で「タスクバー（約 48px）＋装飾（31px）」を賄う値として選ばれて
# いるが、**どちらも DPI で拡大する**のに定数のままなので、150% では 90 − 51 = 39px
# しか残らず 72px のタスクバーを賄えない＝窓の下端 33px が常にタスクバーの裏に入る。
# ⇒ 通常は `usable_area()` が**作業領域（`rcWork`）＋装飾の実測**から決める。
# ここが効くのは Windows 以外・API が無い・呼び出しに失敗した場合だけ。
SCREEN_MARGIN = 90

# 装飾枠（タイトルバー・枠）が 96dpi で要求する外側の寸法（幅の左右合計・高さ）。
# **実測が取れないときの保険**＝実測できる窓では `decoration_size()` が
# `winfo_rootx/rooty` との差から本物を読む（100% で左右 8px・上 31px）。
_DECORATION_96 = (16, 39)

# `GetSystemMetricsForDpi` のインデックス（winuser.h）。
_SM_CYCAPTION, _SM_CYSIZEFRAME, _SM_CXPADDEDBORDER = 4, 33, 92



_GEOMETRY = re.compile(r"^\d+x\d+\+(-?\d+)\+(-?\d+)$")


def window_position(win: "tk.Tk | tk.Toplevel") -> tuple[int, int]:
    """窓の左上（**装飾枠**）の位置。

    ⛔ **`winfo_x/y` を直接読まない**（2026-08-14 実測・B-083）＝**まだ画面に
    出ていない窓では 0 を返す**。`fit_to_content` は `mainloop()` より前に走る
    ので、そこで 0 を信じると「原点にある＝はみ出さない」と判定し、**クランプが
    黙って無効になる**（同じ理由でゲートも 6 窓中 5 窓が空振りした＝実装中に踏んだ）。
    `geometry()` は WM が決めた位置を返し、しかも**書き戻すときの座標系と同じ**。

    ⚠️ `-x-y` 形式（右端・下端からの相対）は解釈が別物なので受けない＝その場合
    だけ `winfo_x/y` へ落ちる（こちらが書くのは常に `+x+y`）。
    """
    m = _GEOMETRY.match(win.geometry())
    if m is None:
        return win.winfo_x(), win.winfo_y()
    return int(m.group(1)), int(m.group(2))


def window_rect(win: "tk.Tk | tk.Toplevel") -> "tuple[int, int, int, int]":
    """窓が占める矩形 `(左, 上, 右, 下)`（`geometry()` と同じ**装飾枠**の座標系）。

    ⚠️ **未表示のあいだ `winfo_width/height` は 1 を返す**ので、そのときだけ決めた
    寸法（`_fit_size`）で代用する。`window_position` を使う理由はあちらの註
    （`winfo_x/y` は未表示の窓で 0）。

    ⛔ **表示済みの窓で「大きい方」を採ってはいけない**（2026-08-15・B-089）。
    `_fit_size` は**最後に自動調整した寸法**で、**利用者が手で縮めても更新されない**
    ＝大きい方を採ると矩形が右・下へ水増しされる。左・上のサブモニタの境界付近に
    置いた窓では、その水増し分がプライマリと重なって `host_monitor` がプライマリを
    選び、**手で縮めただけの窓が主画面へ引き戻される**（B-088 で監視に契機を足した
    ので、ドラッグしなくても発火し得る）。**実寸が取れるならそれが正**。
    """
    x, y = window_position(win)
    fit_w, fit_h = getattr(win, "_fit_size", (0, 0))
    w, h = win.winfo_width(), win.winfo_height()
    if w <= 1:
        w = max(fit_w, 1)                            # 未表示＝実寸が無い
    if h <= 1:
        h = max(fit_h, 1)
    return (x, y, x + w, y + h)


def place_within_screen(
    win: "tk.Tk | tk.Toplevel", *,
    size: "tuple[int, int] | None" = None,
    area: "tuple[int, int, int, int] | None" = None,
) -> tuple[int, int]:
    """窓が画面から出ていたら、入る位置へ寄せる。決めた `(x, y)` を返す。

    **なぜ大きさとは別に要るか**（B-083）
    ------------------------------------
    `fit_to_content` は `geometry(f"{w}x{h}")` と**大きさだけ**を渡すので、
    置き場所は WM 任せ＝Windows の**カスケード配置**で `+78+78` → `+156+156` →
    `+234+234` と 78px ずつ下がっていく（実測）。ランチャーは高さ 973px あるので、
    2 番目のスロットに置かれた時点で下端が FHD の外へ出る。

    ⚠️ **逃げ道（`scrollable_body`）はこの壊れ方に効かない**＝バーは
    `need_h > h`（**窓の大きさ**に入らない）ときだけ出る。ここは大きさとしては
    足りているので受け皿は「入っている」と判断し、バーが出ないまま下端が画面外へ
    出る。**大きさの問題と位置の問題は別物。**

    座標系（2026-08-14 実測）
    ------------------------
    `geometry()` の `+x+y` と `winfo_x/y` は**装飾枠の左上**、`winfo_rootx/rooty`
    は**クライアント領域の左上**（差＝上 31px / 左 8px）。ここは前者で話す
    ＝`geometry()` へ返す値と同じ座標系にしておかないと、書き戻すたびに装飾の
    厚みぶん窓が上へ歩いていく。

    余白の当て方（2026-08-18・B-084 で作業領域へ移した）
    ----------------------------------------------------
    置いてよい矩形は `usable_area()` が返す＝**OS の作業領域から装飾のぶんを
    引いた**もの。タスクバーがどの辺にあっても、DPI がいくつでも、そこに答えが
    入っている。⚠️ **左右・上に自前の余白を足さない**＝ここで余白を要求すると
    **右端に寄せて置く**という正常な置き方を毎回崩す。

    ⚠️ **入っている窓は動かさない**＝測り直し（DPI 変更・画面変更）のたびに
    位置を書き戻すと、ユーザーが置いた場所が失われる。触るのは外へ出たときだけ。

    ⛔ **画面は 1 枚ではない**（2026-08-15・B-085）
    ----------------------------------------------
    `screen_size()` が返すのは **プライマリモニタ 1 枚**（Windows の Tk は
    `winfo_screenwidth/height` に `SM_CXSCREEN/SM_CYSCREEN` を返す）。そこへ
    無条件に `max(0, …)` を当てると、**別モニタに置かれた正当な窓を主画面へ
    引き戻す**：左・上のモニタは負座標なので必ず 0 へ丸められ、右のモニタは
    `min(x, screen_w - w)` で引き戻される。

    ⇒ **窓が載っているモニタの矩形の中へ**収める（`host_monitor`）。

    ⚠️ **「主画面の外なら別モニタ」で済ませてはいけない**＝一度そう書いて Codex に
    差し戻された（2026-08-15・2 巡目）。**サブモニタを外したあと `+2200+100` に
    残った窓**は*どこにも無い場所*に居るので、別モニタ扱いで放置すると**掴めない
    まま**になり、B-083 の救済をそのまま取り消す。**実在する矩形と重なるか**で
    判定し、**どれとも重ならない窓だけを主画面へ引き戻す。**

    Args:
        area: 収める先のモニタ矩形。省略すると窓の位置から求める。**`fit_to_content`
            は自分が寸法を決めるのに使った矩形をそのまま渡す**（B-087）＝大きさと
            位置が別々のモニタを基準にすると、収めたつもりの窓が溢れる。
    """
    win.update_idletasks()
    w, h = size if size is not None else (win.winfo_width(), win.winfo_height())
    x, y = window_position(win)
    monitor = area or host_monitor(win, (x, y, x + w, y + h))
    left, top, right, bottom = usable_area(win, monitor)
    # 窓がモニタより大きいと上限が下限を下回り得る（下限 `min_h` が画面を超える
    # 場合）。そのときは左上をモニタの原点へ寄せる＝下端は諦めるが、掴む場所は
    # 必ず残す。
    new_x = max(left, min(x, right - w))
    new_y = max(top, min(y, bottom - h))
    if (new_x, new_y) != (x, y):
        win.geometry(f"+{new_x}+{new_y}")
    win._fit_pos = (new_x, new_y)       # type: ignore[attr-defined]
    return new_x, new_y


def screen_size(win: "tk.Misc") -> tuple[int, int]:
    """窓が載っている画面の `(幅, 高さ)`。

    `winfo_screen*` を直接呼ばず、ここを一度通す。理由は 2 つあり、どちらも
    「開発機の画面で測ると欠陥が見えない」ことに由来する：

      1. **テストが出荷先の画面を与えられる**＝開発機は WQHD（使える高さ 1350px）
         なので、FHD で溢れる窓もローカルでは永久に緑になる（B-021 が 6 回目まで
         生き延びた理由そのもの）。
      2. 画面サイズは実行中に変わり得る（VDI・モニタ切替）。**その追従は B-022**
         で、測り直しの口をここに 1 つだけ用意しておく。
    """
    return win.winfo_screenwidth(), win.winfo_screenheight()


def _enumerate_monitors() -> "list[tuple[tuple[int, int, int, int], tuple[int, int, int, int] | None]]":
    """OS に**実在するモニタ**を聞く＝`(モニタ矩形, 作業領域)` の列（仮想座標系）。

    取れなければ空を返す（Windows 以外・API が無い・呼び出しに失敗した場合）。
    **判断は呼び出し側**＝ここは「聞けたかどうか」だけを返す。

    ⚠️ **作業領域だけ聞けなかったモニタは `None`**（2026-08-22・B-114）＝モニタ矩形で
    代用すると「タスクバーが無い」と**区別が付かなくなり**、`usable_area` が
    `SCREEN_MARGIN` の保険を通らない（＝そのモニタでだけ B-084 が黙って戻る）。

    **2 つを一緒に返す理由**（2026-08-18・B-084）＝この 2 つは**同じ列挙の 2 つの
    フィールド**（`MONITORINFO` の `rcMonitor` / `rcWork`）で、別々に聞くと
    「どのモニタの作業領域か」を突き合わせる仕事が呼ぶ側に生える。⚠️
    `SystemParametersInfo(SPI_GETWORKAREA)` は**プライマリの作業領域しか返さない**
    ので採らない（複数モニタで黙って間違える）。

    - `rcMonitor`＝**モニタの原点を知る**ため（B-085＝別モニタに置かれた窓を
      主画面へ引き戻さない）。
    - `rcWork`＝**タスクバーを除いた実際に使える矩形**（B-084）。辺の位置も OS が
      面倒を見るので、タスクバーが上・左・右にある構成もこれ 1 つで解ける。
    """
    try:
        import ctypes                                  # 遅延 import（Windows 専用）
        from ctypes import wintypes
        user32 = ctypes.windll.user32
    except (ImportError, AttributeError, OSError):
        return []

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                    ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]

    found: "list[tuple[tuple[int, int, int, int], tuple[int, int, int, int] | None]]" = []

    def _collect(hmon, _hdc, rect, _data) -> int:
        r = rect.contents
        monitor = (r.left, r.top, r.right, r.bottom)
        work: "tuple[int, int, int, int] | None" = None
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        try:
            # ⚠️ 失敗しても**列挙は続ける**＝ここで例外を上げると「作業領域が
            # 取れない 1 枚」のせいで**全モニタの列挙が消える**（＝B-085 で直した
            # 「別モニタの窓を引き戻さない」まで巻き添えになる）。
            # 🔴 ただし**モニタ矩形で代用しない**（B-114）＝代用すると呼ぶ側からは
            # 「タスクバーが無い」と同じ形になり、保険の経路が死ぬ。不明は `None`。
            if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                w = info.rcWork
                work = (w.left, w.top, w.right, w.bottom)
        except OSError:
            pass
        found.append((monitor, work))
        return 1

    proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.POINTER(_RECT), wintypes.LPARAM)
    try:
        user32.EnumDisplayMonitors(None, None, proc(_collect), 0)
    except OSError:
        return []
    return found


def _enumerate_monitor_rects() -> "list[tuple[int, int, int, int]]":
    """実在するモニタの矩形だけ（`_enumerate_monitors` の `rcMonitor` 側）。"""
    return [monitor for monitor, _work in _enumerate_monitors()]


def work_areas() -> "dict[tuple[int, int, int, int], tuple[int, int, int, int]]":
    """**モニタ矩形 → 作業領域**の対応。OS に聞けなければ空（＝何も知らない）。

    `screen_size()` / `monitors()` と同じ「**1 か所を通す**」型（B-084）。
    テストはここを差し替えて**偽のタスクバー**を注入する＝そうしないと、
    「タスクバーを避けられているか」は**開発機のタスクバーの高さでしか検査できない**
    ことになり、出荷先（FHD・150% でタスクバー 72px）の条件を再現できない。

    ⚠️ **空を返すことに意味がある**＝呼ぶ側（`usable_area`）は従来どおり
    `SCREEN_MARGIN` で見積もる。「聞けなかった」と「タスクバーが無い」を
    同じ形（＝モニタ矩形と同じ作業領域）で返すと、区別が付かなくなる。
    ⚠️ **1 枚だけ聞けなかった場合も同じ**＝そのモニタは**この対応から落とす**
    （B-114）。列挙そのもの（`monitors()`）には残るので、窓の置き場所は従来どおり
    決まり、上限だけが `SCREEN_MARGIN` の見積りへ落ちる。
    """
    return {monitor: work for monitor, work in _enumerate_monitors()
            if work is not None}


def monitors(win: "tk.Misc") -> "list[tuple[int, int, int, int]]":
    """窓を置ける矩形の一覧。**聞けなければプライマリ 1 枚**として答える。

    `screen_size()` と同じ「**1 か所を通す**」型（2026-08-15・B-085 の作り直し）。
    テストはここを差し替えて**偽のモニタ構成**を注入する＝そうしないと、複数モニタの
    論理は**モニタを 2 枚つないだ機械でしか検査できない**ことになり、実質だれも
    検査しない（開発機は実測で `SM_CMONITORS = 1`）。
    """
    screen_w, screen_h = screen_size(win)
    rects = _enumerate_monitor_rects()
    if not rects:
        return [(0, 0, screen_w, screen_h)]
    # ⚠️ **プライマリだけは Tk の値を正とする**（原点が `(0, 0)` の矩形＝Windows の
    # 定義）。理由は**テストの差し替え口を 1 つに保つため**＝既存のゲートは 20 本
    # 以上が `screen_size` を差し替えて「出荷先の FHD」を注入している。ここで
    # Win32 の実測だけを返すと、**差し替えたはずの画面が黙って無視され、開発機の
    # 画面で測る緑**になる（[[feedback-promote-recurring-checks]] の壊れ方①）。
    # 実機では両者は一致する＝`test_the_primary_monitor_agrees_with_what_tk_reports`。
    return [(0, 0, screen_w, screen_h) if (r[0], r[1]) == (0, 0) else r
            for r in rects]


def host_monitor(
    win: "tk.Misc", rect: "tuple[int, int, int, int]"
) -> "tuple[int, int, int, int]":
    """窓 `rect`（左, 上, 右, 下）が**いちばん載っているモニタ**の矩形。

    **どのモニタとも重なっていなければプライマリ**を返す＝それが「引き戻すべき窓」
    （2026-08-15・B-085 の 2 巡目）。⚠️ **「主画面の外だから別モニタ」とは言えない**
    ＝サブモニタを外したあと `+2200+100` に残った窓は、*どこにも無い場所*に居る。
    そこを別モニタ扱いで放置すると**窓は掴めないまま**になり、B-083 の救済を取り消す。
    """
    x0, y0, x1, y1 = rect
    best: "tuple[int, int, int, int] | None" = None
    best_area = 0
    for left, top, right, bottom in monitors(win):
        w = min(x1, right) - max(x0, left)
        h = min(y1, bottom) - max(y0, top)
        area = w * h if w > 0 and h > 0 else 0
        if area > best_area:
            best, best_area = (left, top, right, bottom), area
    if best is not None:
        return best
    screen_w, screen_h = screen_size(win)
    return (0, 0, screen_w, screen_h)


def decoration_size(win: "tk.Tk | tk.Toplevel") -> tuple[int, int]:
    """装飾枠（タイトルバー・枠）が**クライアント領域の外に**要求する `(幅, 高さ)`。

    **なぜ要るか**（B-084）＝`geometry(f"{w}x{h}")` が決めるのは**クライアント
    領域**の寸法だが、画面を占めるのは**装飾を含めた枠**。作業領域（`rcWork`）は
    枠の話なので、クライアントの上限を出すにはこの差を引かないといけない。
    100% では 31px（上）だが 150% では 51px あり、**DPI で拡大する**
    ＝定数で持つと高 DPI で足りない（それが B-084 の本体）。

    ⛔ **窓から実測しない**（2026-08-18 に一度そう書いて捨てた）＝`winfo_rootx/rooty`
    と `geometry()` の `+x+y` の差は、**同じ窓でも読む時点で変わる**（実装中に実測
    ＝同じランチャーが 58px と 137px を返した。メニューバーの有無・WM がまだ確定
    させていない配置が混ざる）。上限の計算に使う値が測るたびに変わると、**窓の
    大きさが呼び出し順で変わる**＝再現しない見切れを自分で作ることになる。
    ⇒ **OS に定義を聞く**（`GetSystemMetricsForDpi`＝DPI ごとの値を返す）。

    下端・右端の枠は左端と同じ厚み（Windows）なので、高さ＝上の装飾＋下の枠、
    幅＝枠 × 2 とする。100% で `(16, 39)`、150% で `(22, 56)`（実測）。

    🔴 **DPI は「この窓」に聞く**（2026-08-24・B-120）＝下の `_decoration_dpi` の註。
    """
    return _decoration_for(_decoration_dpi(win))


def _decoration_for(dpi: int) -> tuple[int, int]:
    """`dpi` での装飾の寸法。**窓を持たずに聞ける**＝移動元の DPI の分も出せる
    （2026-08-23・独立レビュー 41 巡目＝`correct_landing` の物差しに要る）。"""
    got = _system_decoration(dpi)
    if got is not None:
        return got
    # 聞けない環境（Windows 以外・API が古い）は 96dpi の実測値を比例させる。
    return (int(round(_DECORATION_96[0] * dpi / 96)),
            int(round(_DECORATION_96[1] * dpi / 96)))


def _system_decoration(dpi: int) -> "tuple[int, int] | None":
    """OS が言う装飾の寸法 `(幅, 高さ)`（聞けなければ None）。

    `GetSystemMetricsForDpi` は Windows 10 1607+ で**DPI ごと**の値を返す
    （素の `GetSystemMetrics` はプロセスの既定 DPI 固定＝高 DPI で 100% の値を
    返し、B-084 の欠陥をそのまま再現する）。
    """
    try:
        import ctypes                                  # 遅延 import（Windows 専用）
        for_dpi = ctypes.windll.user32.GetSystemMetricsForDpi
    except (ImportError, AttributeError, OSError):
        return None
    try:
        caption = int(for_dpi(_SM_CYCAPTION, dpi))
        frame = int(for_dpi(_SM_CYSIZEFRAME, dpi))
        padded = int(for_dpi(_SM_CXPADDEDBORDER, dpi))
    except OSError:
        return None
    if caption <= 0:
        return None
    border = frame + padded
    return (border * 2, caption + border + border)


def _applied_dpi(win: "tk.Misc") -> int:
    """いまアプリの字が従っている DPI（取れなければ 96）。

    ⚠️ **モニタの実 DPI ではなく「当たっている DPI」を見る**＝Tk の名前付き
    フォントはインタプリタに 1 組しかないので、字の大きさはアプリ全体で 1 つ
    （[views/theme](theme.py) の `_applied_dpi` の註）。**字の話はこれで正しい。**
    """
    try:
        from views import theme                    # 遅延 import（循環回避）
        return theme.applied_dpi(win)
    except Exception:
        return 96


def _monitor_dpi(win: "tk.Misc") -> "int | None":
    """`win` が載っているモニタが言う DPI（聞けなければ None）。"""
    try:
        from views import theme                    # 遅延 import（循環回避）
        got = int(theme.window_dpi(win))
    except Exception:
        return None
    return got if got > 0 else None


def _decoration_dpi(win: "tk.Misc") -> int:
    """**装飾の見積りに使う DPI**（2026-08-24・B-120）。

    🔑 **字と装飾は別物**＝字（Tk の名前付きフォント）はインタプリタに 1 組しか
    ないので全体値（`applied_dpi`）が正しく、**装飾は Windows が窓ごとに描く**ので
    窓ごとの値が正しい。以前ここは両方を全体値で束ねており、**倍率の違う複数
    モニタ**でだけ B-084（下端がタスクバーの裏）が戻っていた（150% 側の窓の装飾を
    39px と見積もるが実際は 56px ＝クライアント領域を 17px 大きく取る）。

    ⚠️ **窓の実 DPI へ単純に差し替えると、今度はテストが条件を再現できない**＝
    ゲートは `apply_fonts(dpi=144)` で 150% を作るが、モニタは 96 のままなので
    **装飾だけ 100% で計算される**（[views/theme](theme.py) の `applied_dpi` の註が
    書いている「再現したはずの条件が半分しか再現しない」を逆向きに踏む）。

    ⇒ **全体値が基準にした窓（＝ルート）と同じ DPI に居る窓では全体値をそのまま
    使い、違う DPI に居る窓だけ自分の値を使う。** 実機ではルートと同じモニタに居る
    窓は両者が一致するので何も変わらず、**別倍率のモニタへ出した窓だけが正される**。
    """
    applied = _applied_dpi(win)
    own = _monitor_dpi(win)
    if own is None:
        return applied
    try:
        root_dpi = _monitor_dpi(win._root())        # type: ignore[attr-defined]
    except Exception:
        root_dpi = None
    if root_dpi is None or own == root_dpi:
        return applied              # 基準と同じモニタ＝全体値が正しい（＋再現可能）
    return own                      # 別倍率のモニタ＝この窓の枠はこの DPI で描かれる


def usable_area(
    win: "tk.Tk | tk.Toplevel", area: "tuple[int, int, int, int]"
) -> "tuple[int, int, int, int]":
    """モニタ `area` のうち、窓の**中身（クライアント領域）**を置いてよい矩形。

    **装飾枠のぶんは引いてある**＝返り値の幅・高さがそのまま `geometry()` へ
    渡してよい上限になり、`(左, 上)` がそのまま置いてよい左上になる。

    🔴 **余白は定数ではなく OS の作業領域から決める**（2026-08-18・B-084）。
    `SCREEN_MARGIN = 90` は 100% での「タスクバー 48 ＋ 装飾 31」を賄う値だが、
    **どちらも DPI で拡大する**のに定数のままだったので、150% では窓の下端 33px が
    常にタスクバーの裏に入っていた。作業領域なら**タスクバーの実寸**が入り、
    **タスクバーが上・左・右にある構成**も同じ 1 つの式で解ける。

    ⚠️ **聞けなかったときは従来どおり `SCREEN_MARGIN`**（Windows 以外・API 無し）。
    その経路では装飾を**別に引かない**＝90px が装飾のぶんを既に含んでいるので、
    引くと二重になる。
    """
    left, top, right, bottom = area
    work = work_areas().get((left, top, right, bottom))
    if work is None:
        return (left, top, right, bottom - SCREEN_MARGIN)
    dec_w, dec_h = decoration_size(win)
    return (work[0], work[1], work[2] - dec_w, work[3] - dec_h)


def _tables(win: "tk.Misc") -> "list[ttk.Treeview]":
    """`win` の中の Treeview を集める。"""
    found: "list[ttk.Treeview]" = []
    stack = list(win.winfo_children())
    while stack:
        w = stack.pop()
        if isinstance(w, ttk.Treeview):
            found.append(w)
        stack.extend(w.winfo_children())
    return found


def _freeze_table_columns(win: "tk.Misc", *, freeze: bool) -> "list[ttk.Treeview]":
    """`stretch=True` の Treeview が書き戻した列幅の面倒を見る。

    ttk の Treeview は窓が中身より広いと列を引き伸ばし、**引き伸ばした幅を
    `-width` として持ち帰る**。その結果、次に測ったときの要求幅は「広げられた
    あとの幅」になり、**表の必要幅が一方通行で増え続ける**（窓を縮められるように
    しても、中身が元の幅を要求しないので戻らない＝I-053 の最後の一片）。

    そこで**作ったときの列幅を覚えておき**、縮む方向に測り直すときだけ戻す。
    ⚠️ 覚えるのは初回の `fit_to_content` 時点＝窓はまだ中身ぴったりで、引き伸ばし
    は起きていない（あとから列を足す窓が出たら、その窓の分はここで覚え直される）。

    ⚠️ **幅を戻すだけでは足りない**＝戻した直後に走る再レイアウトで、まだ広い
    ままの窓に合わせて ttk がもう一度引き伸ばす（測る前に元通りになる）。測って
    いるあいだは `stretch` も止め、寸法を決めたあとに `_thaw_table_columns` で
    元に戻す（新しい窓幅に合わせた引き伸ばしはそこで改めて走る）。

    Returns:
        止めた Treeview（`_thaw_table_columns` へ渡す）。
    """
    frozen: "list[ttk.Treeview]" = []
    for tree in _tables(win):
        base: "dict[str, tuple[int, bool]] | None" = getattr(tree, "_fit_col_widths", None)
        columns = [str(c) for c in tree["columns"]]
        if base is None or set(base) != set(columns):
            tree._fit_col_widths = {          # type: ignore[attr-defined]
                col: (int(tree.column(col, "width")), bool(tree.column(col, "stretch")))
                for col in columns}
            continue
        if not freeze:
            continue
        for col, (width, _stretch) in base.items():
            tree.column(col, width=width, stretch=False)
        # ⚠️ **列幅を戻すだけでは要求幅が変わらない**（実測＝列は 220px に戻るのに
        # `winfo_reqwidth()` は引き伸ばし後の 1460px を返し続ける。ttk は列幅の
        # 変更で幾何を要求し直さない＝広げる側でしか再計算が走らない）。
        # `displaycolumns` を**同じ値で**入れ直すと再計算の契機になる。
        tree.configure(displaycolumns=tree["displaycolumns"])
        frozen.append(tree)
    return frozen


def _thaw_table_columns(trees: "list[ttk.Treeview]") -> None:
    """`_freeze_table_columns` で止めた引き伸ばしを元に戻す。"""
    for tree in trees:
        for col, (_width, stretch) in getattr(tree, "_fit_col_widths", {}).items():
            tree.column(col, stretch=stretch)


def ready(win: "tk.Tk | tk.Toplevel") -> None:
    """**この窓は組み立て終わった**＝以後の測り直しは畳んでよい（I-107）。

    窓の `__init__` の最後で呼ぶ。⚠️ **呼び忘れても壊れない**（`fit_soon` が
    従来どおり同期で測るだけ＝速くならないが、間違った寸法にはならない）。
    ⇒ 新しい窓を足した人が忘れても、**安全側**に倒れる。
    """
    win._fit_ready = True                       # type: ignore[attr-defined]


def fit_soon(win: "tk.Tk | tk.Toplevel", run: "Any") -> bool:
    """**連続する測り直しを `after_idle` で 1 回に畳む**（2026-08-24・I-107）。

    `run` は「この窓の測り直し 1 回ぶん」を行う引数なしの呼び出し可能物
    （窓ごとに下限・加算値が違うので、**何を測るかは窓が持つ**）。畳んだかどうかを
    返す（真＝あとで走る）。

    **なぜ要るか**＝地点を 1 つ足すたびに `fit_to_content` が走り、**1 回 0.22 秒**
    利用者の手が止まる（実測・中継経路）。同型は 3 つ＝中継経路の地点追加・条件探索の
    条件列追加・バッチ表の列幅追従で、**窓を開くだけでも 3 回連続で走っている**。

    ⚠️ **畳むのは回数であって、追従そのものではない**（B-021 の再発を自分で作らない）。
    アイドルは*次の描画より前*に来るので、利用者から見た「中身が増えたら窓が広がる」は
    1 フレームも遅れない。**測る側から見て遅れないこと**は 2 つで担保する:

      1. **組み立てが終わるまでは同期で測る**（`ready()` を通るまで）＝`__init__`
         から戻った時点で寸法は決まっていないといけない（`_fit_size` / `_fit_need`
         を読む口が窓の外にもある＝置き場所の計算・横断ゲート）。畳んでよいのは
         *出ている窓を利用者が触っている*あいだだけで、そこが 0.22 秒待たされて
         いる当人でもある。
      2. `required_size()` と `refit_all()` は**先に溜まりを流す／捨てる**。
         横断ゲートはここを通るので、**畳んだせいでゲートが古い値を見ることはない**。
      3. **一度きりの条件（`_fit_shrink`）が立っているあいだは畳まない**＝あれは
         `refit_all` がその 1 回のために立てて `finally` で下ろす印なので、
         アイドルまで持ち越すと**縮む向きの測り直しが黙って効かなくなる**
         （実装中に踏んだ＝`shrink` のゲート 3 本が赤）。
    """
    if not getattr(win, "_fit_ready", False):
        run()                       # 組み立て中＝同期（上の 1）
        return False
    if getattr(win, "_fit_shrink", False):
        run()                       # 一度きりの条件は持ち越せない（上の 3）
        return False
    win._fit_soon_run = run                     # type: ignore[attr-defined]
    if getattr(win, "_fit_soon_id", None) is not None:
        return True                 # 既に予約済み＝最新の呼び出しで上書きするだけ
    try:
        win._fit_soon_id = win.after_idle(      # type: ignore[attr-defined]
            lambda: _run_soon_fit(win))
    except tk.TclError:
        win._fit_soon_id = None                 # type: ignore[attr-defined]
        run()                       # 破棄途中など＝畳めないならその場で測る
        return False
    return True


def _run_soon_fit(win: "tk.Tk | tk.Toplevel") -> None:
    """溜まっていた測り直しを 1 回だけ走らせる（`fit_soon` の実体）。"""
    win._fit_soon_id = None                     # type: ignore[attr-defined]
    run = getattr(win, "_fit_soon_run", None)
    win._fit_soon_run = None                    # type: ignore[attr-defined]
    if run is None:
        return
    try:
        run()
    except tk.TclError:
        pass                        # 破棄途中の窓（閉じ際に溜まりが走る経路）


def _drop_soon_fit(win: "tk.Misc") -> None:
    """溜まっている測り直しを**捨てる**（いま同じ仕事をするので走らせない）。"""
    after_id = getattr(win, "_fit_soon_id", None)
    win._fit_soon_run = None                    # type: ignore[attr-defined]
    if after_id is None:
        return
    win._fit_soon_id = None                     # type: ignore[attr-defined]
    try:
        win.after_cancel(after_id)
    except (tk.TclError, ValueError):
        pass


def flush_fit(win: "tk.Misc") -> bool:
    """溜まっている測り直しがあれば**いま**走らせる。走らせたかを返す。

    🔑 **これがあるから畳んでよい**＝「測る前に必ず呼ぶ」口（`required_size` /
    `refit_all`）がここを通るので、**畳んだ結果を誰かが古い値として読むことがない**。
    """
    if getattr(win, "_fit_soon_id", None) is None:
        return False
    try:
        win.after_cancel(win._fit_soon_id)      # type: ignore[attr-defined]
    except (tk.TclError, ValueError):
        pass
    _run_soon_fit(win)                          # type: ignore[arg-type]
    return True


def fit_to_content(
    win: "tk.Tk | tk.Toplevel",
    *,
    min_w: int = 0,
    min_h: int = 0,
    extra_w: int = 0,
    extra_h: int = 0,
    grow_only: bool = True,
) -> tuple[int, int]:
    """`win` を中身が収まる大きさにし、決めた寸法 `(w, h)` を返す。

    Args:
        win: トップレベル（`tk.Tk` / `tk.Toplevel`）。
        min_w: 下限の幅（既定サイズ）。**上限ではない**。
        min_h: 下限の高さ。
        extra_w: 要求幅に足す分。`winfo_reqwidth()` に現れない中身がある窓向け
            （バッチ表は行がキャンバスの中にあり、縦スクロールバーと外周 padding が
            要求幅に載らない）。**この抜け道があるからこそ**、横断ゲートは窓の申告
            （`_fit_need`）と自前の実測の**大きい方**で検査する。
        extra_h: 同上（高さ）。
        grow_only: 真なら**縮めない**（ユーザーが広げた窓を勝手に狭めない）。

    Returns:
        実際に `geometry()` へ渡した `(幅, 高さ)`。同じ値を `win._fit_size` に、
        必要量を `win._fit_need` に残す（横断ゲートが読む唯一の口）。
    """
    # 畳んで待たせていた測り直しは、いまここで済む（I-107）。⚠️ **窓ごとに溜めるのは
    # 1 本だけ**＝どの窓も測り直しの口は 1 つ（`_fit_to_content` / `_fit_refit`）で、
    # 同じ窓に別条件の測り直しが並ぶことはない。
    _drop_soon_fit(win)
    # 呼び出しの内容を残す＝DPI 変更などで**同じ条件のまま測り直す**ため
    # （refit_all が使う。窓ごとに min_w / extra_w が違うので、これが無いと
    # 貼り直し側が窓ごとの事情を知らないと再測できない）。
    win._fit_kwargs = {                 # type: ignore[attr-defined]
        "min_w": min_w, "min_h": min_h,
        "extra_w": extra_w, "extra_h": extra_h, "grow_only": grow_only,
    }
    # **DPI が変わった経路だけ**は縮む方向にも測り直す（I-053）。印は属性で受ける
    # ＝バッチのように自前の再測（`_fit_refit`）を持つ窓は自分の `grow_only` で
    # ここを呼び直すので、引数では伝わらない。⚠️ `_fit_kwargs` は**窓本来の条件**
    # のまま残す（この上書きは 1 回きりで、窓の約束を書き換えない）。
    shrink = bool(getattr(win, "_fit_shrink", False))
    if shrink:
        grow_only = False
    frozen = _freeze_table_columns(win, freeze=shrink)
    escape: "_ScrollEscape | None" = getattr(win, "_fit_scroll", None)
    if escape is not None:
        # ⚠️ **前回出したバーを畳んでから測る**（B-074(a)）。下の測定は窓の
        # `winfo_reqwidth()` を読むが、受け皿のバーは窓の中身なので**出たままだと
        # その幅ぶん要求に載る**。すると「入らなくなって出したバー」が次の測定を
        # 太らせ、**入るようになっても幅が戻らない**（実測＝150% で縦バーが出た
        # あと 100% へ戻すと、窓幅にバー 1 本分の 12px が残る。高さが戻るのは
        # 横バーが出ていないからで、直っていたわけではない）。
        # 下の `escape.sync(...)` が必要なぶんを測り直して出し直すので、ここで
        # 畳んでも「入らない窓のバーが消える」ことにはならない。
        escape.sync(overflow_v=False, overflow_h=False)
        # 受け皿越しでも「中身がどれだけ要るか」を窓が正しく申告できる状態にする。
        escape.remeasure()
    win.update_idletasks()
    # 🔴 **上限も「窓が載っているモニタ」から取る**（2026-08-15・B-087）。
    # 位置だけをモニタ基準にして大きさをプライマリ基準のままにすると、**解像度の
    # 違うモニタで必ず溢れる**＝主画面 2560×1440 / サブ 1920×1080 の構成では、
    # 必要高 1023px のランチャーが主画面基準では 1023px のまま作られ、サブ画面の
    # 使える高さ 990px に対して 33px はみ出す。**位置の調整では直らない**（窓自体が
    # モニタより高いので、上端を原点に寄せても下端が出る）。⇒ 大きさと位置は
    # **同じ 1 枚**を基準にする（下の `place_within_screen` へ同じ矩形を渡す）。
    area = host_monitor(win, window_rect(win))
    # 🔴 **上限は「作業領域から装飾を引いた寸法」**（2026-08-18・B-084）。定数の
    # 余白は DPI で拡大するタスクバー・装飾を賄えず、150% で下端 33px が裏へ入る。
    fit_l, fit_t, fit_r, fit_b = usable_area(win, area)
    lim_w = max(fit_r - fit_l, min_w)
    lim_h = max(fit_b - fit_t, min_h)

    need_w = win.winfo_reqwidth()  + extra_w
    need_h = win.winfo_reqheight() + extra_h
    win._fit_need = (need_w, need_h)    # type: ignore[attr-defined]

    prev_w, prev_h = getattr(win, "_fit_size", (0, 0))
    floor_w, floor_h = min_w, min_h
    if grow_only:
        # 未表示のあいだ winfo_width() は 1 を返すので、下限・前回値で受ける。
        floor_w = max(floor_w, prev_w, win.winfo_width())
        floor_h = max(floor_h, prev_h, win.winfo_height())

    w = min(max(need_w, floor_w), lim_w)
    h = min(max(need_h, floor_h), lim_h)

    # 入らなかったぶんは逃げ道（スクロール）へ回す。⚠️ `_fit_need` は**縮めない**
    # ＝「スクロールできるから入っている」という免除条項を作らないため
    # （ゲートは引き続き「入らない」と言い、実害だけが消える）。
    if escape is not None:
        pad_w, pad_h = escape.sync(overflow_v=need_h > h, overflow_h=need_w > w)
        if pad_w or pad_h:
            # 縦バーが出た分だけ窓を広げる（広げられなければ横バーも出る）。
            w = min(max(need_w + pad_w, floor_w), lim_w)
            h = min(max(need_h + pad_h, floor_h), lim_h)
            escape.sync(overflow_v=need_h > h, overflow_h=need_w > w)

    win._fit_size = (w, h)              # type: ignore[attr-defined]
    # **何を要求したか**を残す（`correct_landing` が着地を確かめる唯一の口）。
    win._fit_asked = (w, h)             # type: ignore[attr-defined]
    win.geometry(f"{w}x{h}")
    # 大きさが決まったら**置き場所も画面の中へ**（B-083）。⚠️ ここで `_fit_size`
    # を渡す＝`winfo_width()` は未表示のあいだ 1 を返すので、実測から測ると
    # 「どこに置いても入る」ことになり、このクランプは黙って無効になる。
    place_within_screen(win, size=(w, h), area=area)
    _thaw_table_columns(frozen)
    return w, h


def correct_landing(
    win: "tk.Tk | tk.Toplevel", *, from_dpi: "int | None" = None
) -> bool:
    """**要求した寸法に着地したか**を確かめ、届いていなければ 1 度だけ言い直す。

    返り値＝言い直したか。

    🔴 **なぜ要るか**（2026-08-23・B-119）。別 DPI のモニタへ窓を移すと、Tk 8.6 は
    **窓枠の厚みを移る前の DPI のまま**持っている。そこへ `fit_to_content` が
    `geometry("602x1197")` と要求すると、返ってくる窓は `596x1197`＝**幅が 6px
    呑まれる**（6px は装飾幅が 16→22 に増えた差そのもの）。呑まれた 6px は
    `need_w(602) > 実幅(596)` を**永久に成立**させ、Tk が要求を出し直すたびに
    また 6px 減る ⇒ **手を離しても止まらずに縮み続ける**（実機ログで確認）。
    ⇒ **足りない分を足して 1 度だけ言い直す**と、そこで燃料が尽きる。

    ⚠️ **覚えない**（2026-08-23・独立レビュー 40 巡目）。最初は「呑まれた分」を
    窓に持たせて次回の要求に足す形にしたが、**持つと汚染される**＝①利用者や OS が
    変えた寸法まで「枠の取り分」として記録し、以後の測り直しがそれを黙って
    取り消す ②誤りを弾くための上限が要り、その上限が**高い表示倍率で破れる**
    （実測＝250% では装飾の差が 49px で、48px の上限を超える）。**その場で 1 度
    直して忘れる**なら、間違えても次に持ち越さない。

    ⚠️ **測るのは「落ち着いた後」**＝要求した直後の `winfo_width()` はまだ古い値を
    返す（実測で 178ms 遅れて届いた）。呼ぶのは [views/theme](theme.py) の
    `watch_display` が DPI 変更の測り直しの後に仕掛ける 1 回きりの確認。

    ⚠️ **装飾の寸法より大きなずれは触らない**＝それは枠の話ではない（利用者が
    窓を掴んで変えた・WM が要求を拒んだ）。**上限を定数で持たない**のが要点で、
    装飾そのものを物差しにすれば表示倍率がいくつでも尺度が合う。
    """
    asked = getattr(win, "_fit_asked", None)
    want = getattr(win, "_fit_size", None)
    if not asked or not want:
        return False                    # `fit_to_content` を通っていない窓は対象外
    try:
        got_w, got_h = win.winfo_width(), win.winfo_height()
    except tk.TclError:
        return False                    # 破棄途中の窓
    if got_w <= 1 or got_h <= 1:
        return False                    # 未表示＝実寸がまだ無い
    short_w, short_h = want[0] - got_w, want[1] - got_h
    if short_w == 0 and short_h == 0:
        return False                    # 要求どおり着地している
    # 🔴 **物差しは移動元と移動先の大きい方**（2026-08-23・独立レビュー 41 巡目）。
    # Tk が握っているのは**移動元**の厚みなので、**倍率が下がる向き**では移動先の
    # 装飾より大きなずれが正当に起き得る（実測＝250%→100% では 49px 起き得るのに、
    # 移動先の装飾は 39px しかなく、**戻る向きだけ補正が拒否されていた**）。
    room_w, room_h = decoration_size(win)
    if from_dpi is not None:
        was_w, was_h = _decoration_for(from_dpi)
        room_w, room_h = max(room_w, was_w), max(room_h, was_h)
    if abs(short_w) > room_w or abs(short_h) > room_h:
        return False                    # 枠の話ではない（上の註）
    # 言い直す分は**要求**に足す（`_fit_size` は「決めた寸法」なので動かさない
    # ＝確かめ直し〔B-118〕が見る基準がずれる）。
    say = (asked[0] + short_w, asked[1] + short_h)
    win._fit_asked = say                # type: ignore[attr-defined]
    try:
        win.geometry(f"{say[0]}x{say[1]}")
    except tk.TclError:
        return False
    return True


def required_size(win: "tk.Tk | tk.Toplevel") -> tuple[int, int]:
    """`win` が中身を収めるのに必要な `(幅, 高さ)`。

    窓が申告した必要量（`_fit_need`）と、ここでの実測（`winfo_req*`）の**大きい方**
    を返す。ゲート側が「何と比べるか」を窓ごとに書き直さずに済ませるための口で、
    申告を信じきらないのは**申告そのものが間違っている窓**（測り忘れ・測り方の
    誤り）を通さないため。

    🔴 **受け皿を先に測り直す**（2026-08-18・B-100）。逃げ道（`scrollable_body`）を
    持つ窓では `winfo_reqwidth()` が**キャンバスの要求幅で頭打ちになる**＝それを
    更新するのは `_ScrollEscape.remeasure()` だけで、呼ぶのは `fit_to_content` だけ。
    ⇒ **測り直しを呼ばない経路で中身が伸びると、ここは伸びる前の値を返し続ける**
    （実測＝中継経路の区間表が 647 → 993px に伸びても、この関数は 801px のまま）。
    **申告も実測も同じ嘘をつくので、大きい方を採っても救われない。**
    ⚠️ これは「窓が測り忘れている」ことを**ゲートから見えなくする**欠陥だった
    ＝[[feedback-promote-recurring-checks]] の壊れ方③（間違ったものを要求して
    いる）。B-100 は 1 つの窓の話に見えて、**横断ゲートの目そのものが塞がっていた。**
    """
    # 🔴 **溜まっている測り直しを先に流す**（2026-08-24・I-107）＝畳んだ結果を
    # 「まだ測っていない窓」として読ませないため。ここは横断ゲートの入口でもある。
    flush_fit(win)
    escape: "_ScrollEscape | None" = getattr(win, "_fit_scroll", None)
    if escape is not None:
        escape.remeasure()
    win.update_idletasks()
    need_w, need_h = getattr(win, "_fit_need", (0, 0))
    return max(win.winfo_reqwidth(), need_w), max(win.winfo_reqheight(), need_h)


def refit_all(root: "tk.Tk | tk.Toplevel", *, shrink: bool = False) -> None:
    """開いている全ウィンドウを、同じ条件のまま測り直す。

    フォントが変わると（DPI 変更・テーマ変更）**必要な幅も高さも変わる**ので、
    貼り直しただけでは字が大きくなった分だけ見切れる。窓ごとの事情（下限・
    スクロールバー分の加算）は `fit_to_content` が `_fit_kwargs` に残しているので、
    ここは「もう一度同じ呼び出しをする」だけでよい。

    Args:
        shrink: 真なら `grow_only` を**この 1 回だけ**外し、縮む方向にも測り直す。
            **DPI が変わった経路にだけ渡す**（I-053）＝表示スケールを 150% →
            100% へ戻したのに窓が 150% の大きさのまま残るのを直す。
            受け入れる副作用＝**手で広げた窓も既定サイズへ戻る**（スケールを
            変えた瞬間は「窓の大きさが変わる」ことが期待そのもの）。
            ⚠️ **画面サイズだけが変わった経路には渡さない**＝あちらは上限が動く
            話で、ユーザーが広げた窓を狭める理由にならない（B-022 の復帰は
            `lim_h` 側で既に効く）。
    """
    for win in (root, *toplevels(root)):
        # 窓自身が再測メソッドを持つならそちらを優先する。`_fit_kwargs` に残る
        # 加算値（バッチのスクロールバー幅）は**呼んだ時点の実測**なので、DPI が
        # 変わるとスクロールバー自体が太って数 px 足りなくなる。窓の側で測り直せる
        # なら、その方が正しい。
        # 畳んで待っている測り直しは、これから同じ窓を測るので捨てる（I-107）。
        # ⚠️ **流す（`flush_fit`）ではなく捨てる**＝流すと*古い DPI のまま* 1 回
        # 測ってから測り直すことになり、`grow_only` の floor に古い値が焼き付く。
        _drop_soon_fit(win)
        refit = getattr(win, "_fit_refit", None)
        kwargs: "dict[str, Any]" = getattr(win, "_fit_kwargs", {})
        if refit is None and not kwargs:
            continue
        win._fit_shrink = shrink        # type: ignore[attr-defined]
        try:
            if refit is not None:
                refit()
            else:
                fit_to_content(win, **kwargs)
        except tk.TclError:
            pass          # 破棄途中の窓は飛ばす
        finally:
            win._fit_shrink = False     # type: ignore[attr-defined]


def toplevels(root: tk.Misc) -> "list[tk.Toplevel]":
    """`root` 配下の Toplevel を集める（入れ子も辿る）。

    ⚠️ **公開しているのは [views/theme](theme.py) が同じ窓の集合を見るため**
    （B-065＝表示環境の監視も「開いている窓ぜんぶ」が単位）。窓の集め方が 2 つに
    割れると、片方に映って片方に映らない窓が出る。
    """
    found: list[tk.Toplevel] = []
    stack = list(root.winfo_children())
    while stack:
        w = stack.pop()
        if isinstance(w, tk.Toplevel):
            found.append(w)
        stack.extend(w.winfo_children())
    return found
