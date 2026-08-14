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
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Any

# 画面いっぱいまでは広げない（タスクバー・ウィンドウ枠のぶんを残す）。
# 従来はバッチ 80px / 条件探索 90px / ランチャー「画面の 92%」と窓ごとにばらけて
# いたので、ここで 1 つに揃える（値そのものより、揃っていることに意味がある）。
SCREEN_MARGIN = 90


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


def place_within_screen(
    win: "tk.Tk | tk.Toplevel", *, size: "tuple[int, int] | None" = None
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

    余白の当て方
    ------------
    **下端だけ `SCREEN_MARGIN` を引く**（Windows のタスクバーは既定で下）。
    装飾の上 31px もこの帯で吸収する。左右・上は 0＝画面に触れていてもよく、
    ここで余白を要求すると**右端に寄せて置く**という正常な置き方を毎回崩す。

    ⚠️ **入っている窓は動かさない**＝測り直し（DPI 変更・画面変更）のたびに
    位置を書き戻すと、ユーザーが置いた場所が失われる。触るのは外へ出たときだけ。
    """
    win.update_idletasks()
    w, h = size if size is not None else (win.winfo_width(), win.winfo_height())
    screen_w, screen_h = screen_size(win)
    x, y = window_position(win)
    # 画面より大きい窓では上限が負になり得る（下限 `min_h` が画面を超える場合）。
    # そのときは左上を原点に寄せる＝下端は諦めるが、掴む場所は必ず残す。
    new_x = max(0, min(x, screen_w - w))
    new_y = max(0, min(y, screen_h - SCREEN_MARGIN - h))
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


def scrollable_body(
    win: "tk.Tk | tk.Toplevel", *, padding: "int | tuple[int, int]" = 0
) -> ttk.Frame:
    """`win` の中身を入れる受け皿を作り、その内側のフレームを返す。

    窓は以後この戻り値へ組み立てる（`win` へ直接 pack しない）。**中身が窓に入る
    あいだはスクロールバーを出さない**ので、見た目・タブ順・レイアウトは受け皿が
    無いときと変わらない。入らなくなった時だけバーが現れる。

    出し入れの判断は `fit_to_content` が行う（寸法を決めるのはあちらの仕事で、
    ここは器だけ持つ）。

    Args:
        padding: 内側フレームの padding。**受け皿を挟む前に窓が持っていた
            外周 padding をここへ移す**（`ttk.Frame(win, padding=10)` を
            そのまま受け皿の中へ入れると、スクロール領域の外側に padding が
            残って下端が隠れる）。
    """
    holder = ttk.Frame(win)
    holder.pack(fill="both", expand=True)
    holder.rowconfigure(0, weight=1)
    holder.columnconfigure(0, weight=1)

    # tk.Canvas は ttk 管理外＝テーマに追従しないので、生成時点のテーマ背景色を
    # 明示的に合わせる（出所は views/theme.py。`ttk.Style().lookup` は sun-valley
    # では常に空を返し、黙って無指定になる＝B-008）。
    from views import theme                       # 遅延 import（循環回避）
    canvas = tk.Canvas(holder, borderwidth=0, highlightthickness=0,
                       bg=theme.palette(holder)["bg"])
    canvas.grid(row=0, column=0, sticky="nsew")
    vsb = ttk.Scrollbar(holder, orient="vertical",   command=canvas.yview)
    hsb = ttk.Scrollbar(holder, orient="horizontal", command=canvas.xview)
    canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    body = ttk.Frame(canvas, padding=padding)
    item = canvas.create_window((0, 0), window=body, anchor="nw")

    escape = _ScrollEscape(win, canvas, body, item, vsb, hsb)
    win._fit_scroll = escape                       # type: ignore[attr-defined]
    return body


def _has_own_scroll(widget: "tk.Misc | None", *, stop: tk.Misc) -> bool:
    """`widget` か、その祖先（`stop` まで）が**自分でスクロールできる**か。

    ホイールのイベントはトップレベルまで上がってくるが、途中に結果一覧
    （Treeview）のような自前のスクロールを持つウィジェットがあれば、そちらが
    既に動かしている。受け皿まで一緒に動かすと**一度のホイールで二重に流れる**
    ので、その場合は手を出さない。`yview()` が `(0.0, 1.0)` の間は「中身が全部
    見えている＝そのウィジェットは動けない」ので、受け皿の仕事になる。
    """
    node = widget
    while node is not None and node is not stop:
        yview: "Any" = getattr(node, "yview", None)
        if callable(yview):
            try:
                view: "Any" = yview()
                first, last = (float(v) for v in view)
            except (tk.TclError, TypeError, ValueError):
                first, last = 0.0, 1.0
            if (first, last) != (0.0, 1.0):
                return True
        node = getattr(node, "master", None)
    return False


class _ScrollEscape:
    """「入らないときだけスクロールする」受け皿の中身（`scrollable_body` 用）。

    肝は **キャンバスの*要求*サイズを中身の要求サイズに合わせ続ける**こと。
    こうすると窓の `winfo_reqheight()` は受け皿が無いときと同じ値を返すので、
    `fit_to_content` も横断ゲートも「中身がどれだけ要るか」を今までどおり測れる
    （＝スクロールできることを理由に必要量を小さく偽らない）。窓が実際にその
    高さを取れなければキャンバスだけが縮み、差分がスクロール量になる。
    """

    def __init__(self, win, canvas, body, item, vsb, hsb) -> None:
        self.win, self.canvas, self.body = win, canvas, body
        self.item, self.vsb, self.hsb = item, vsb, hsb
        self.active = (False, False)               # (縦, 横) バーを出しているか
        body.bind("<Configure>",   self._on_body_configure)
        canvas.bind("<Configure>", self._on_canvas_configure)
        # ⚠️ **`bind_all` は使わない**。Tk のバインドタグは
        # 「ウィジェット → クラス → トップレベル → all」なので、**トップレベルに
        # 束ねればこの窓の中だけに効く**。`bind_all` だと(1)他の窓のホイールまで
        # 拾い(2)後から `bind_all` した窓に**上書きされ**(3)その窓が閉じるときの
        # `unbind_all` で**こちらの分まで消える**（バッチ表が実際に bind_all して
        # いる）。窓に閉じたバインドなら、この 3 つがまとめて起きない。
        win.bind("<MouseWheel>", self._on_mousewheel)
        # **ユーザーが窓を縮めたときも**バーを出す。`fit_to_content` は開いたときと
        # 中身が増えたときにしか走らないので、これが無いと「手で小さくしたら下端が
        # 消えて、スクロールもできない」になる（リサイズできる窓＝グラフ窓で露見）。
        win.bind("<Configure>", self._on_win_configure, add="+")

    # -- 中身とキャンバスの同期 --------------------------------
    def remeasure(self) -> None:
        """キャンバスの**要求**サイズを中身の要求サイズへ合わせ直す。

        ⚠️ `<Configure>` 任せにはできない。あれは*実際の*サイズが変わったときしか
        飛ばないが、フォントが大きくなった（DPI 変更）ときに変わるのは中身の
        *要求*サイズだけで、窓が固定サイズなら実際のサイズは動かない
        ＝**要求が伝わらず、窓は元の幅のまま字だけ大きくなって見切れる**
        （実装中に踏んだ。`test_windows_are_refitted_when_dpi_grows` が捕まえた）。
        だから `fit_to_content` が測る前に、ここを明示的に呼ぶ。
        """
        self.win.update_idletasks()
        need_w, need_h = self.body.winfo_reqwidth(), self.body.winfo_reqheight()
        self.canvas.configure(width=need_w, height=need_h)
        self._sync_scrollregion()
        self.win.update_idletasks()

    def _sync_scrollregion(self) -> None:
        """スクロールできる範囲＝**中身の要求サイズ**（キャンバスより大きい側）。

        ⚠️ `bbox("all")` から取らない＝あれは*描かれている*大きさで、窓が未実現の
        あいだや `<Configure>` が飛ぶ前は中身より小さい値を返す。そのままだと
        「バーは出ているのに下端まで送れない」状態になる（実装中に踏んだ）。
        """
        w = max(self.body.winfo_reqwidth(),  self.canvas.winfo_width())
        h = max(self.body.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0, 0, w, h))

    def _on_body_configure(self, _event=None) -> None:
        self._sync_scrollregion()

    def _on_canvas_configure(self, event) -> None:
        # 窓のほうが中身より大きいときは中身を引き伸ばす（`fill="both"` や
        # `side="bottom"` で組んである既存のレイアウトを、受け皿の中でも
        # そのまま成立させるため）。
        self.canvas.itemconfig(
            self.item,
            width=max(event.width,  self.body.winfo_reqwidth()),
            height=max(event.height, self.body.winfo_reqheight()),
        )

    def _on_win_configure(self, event) -> None:
        """窓の実サイズと必要量を突き合わせてバーを出し入れする。

        ⚠️ 比べるのは `_fit_size`（**決めた寸法**）ではなく**今の実サイズ**＝
        ユーザーが手で変えた結果はそちらにしか現れない。`sync` は状態が変わった
        ときだけ触るので、この handler が Configure を呼び戻して無限に往復する
        ことはない。
        """
        if event.widget is not self.win:
            return                       # 子ウィジェットの Configure は無視
        need_w, need_h = getattr(self.win, "_fit_need", (0, 0))
        self.sync(overflow_v=need_h > event.height, overflow_h=need_w > event.width)

    def _on_mousewheel(self, event) -> None:
        if not self.active[0]:
            return                                 # 溢れていない＝スクロールしない
        if _has_own_scroll(event.widget, stop=self.body):
            return                                 # 中の一覧・表が自分で処理する
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # -- 出し入れ ----------------------------------------------
    def sync(self, *, overflow_v: bool, overflow_h: bool) -> tuple[int, int]:
        """バーを出し入れし、**そのために余分に要る (幅, 高さ)** を返す。

        縦バーを出せばその幅だけ中身の使える幅が減る（＝窓をそのぶん広げないと
        横にも溢れる）。`fit_to_content` が返り値を見て一度だけ測り直す。
        """
        if (overflow_v, overflow_h) != self.active:
            (self.vsb.grid(row=0, column=1, sticky="ns") if overflow_v
             else self.vsb.grid_remove())
            (self.hsb.grid(row=1, column=0, sticky="ew") if overflow_h
             else self.hsb.grid_remove())
            self.active = (overflow_v, overflow_h)
            self.win.update_idletasks()
        return (self.vsb.winfo_reqwidth() if overflow_v else 0,
                self.hsb.winfo_reqheight() if overflow_h else 0)


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
    screen_w, screen_h = screen_size(win)
    lim_w = max(screen_w - SCREEN_MARGIN, min_w)
    lim_h = max(screen_h - SCREEN_MARGIN, min_h)

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
    win.geometry(f"{w}x{h}")
    # 大きさが決まったら**置き場所も画面の中へ**（B-083）。⚠️ ここで `_fit_size`
    # を渡す＝`winfo_width()` は未表示のあいだ 1 を返すので、実測から測ると
    # 「どこに置いても入る」ことになり、このクランプは黙って無効になる。
    place_within_screen(win, size=(w, h))
    _thaw_table_columns(frozen)
    return w, h


def required_size(win: "tk.Tk | tk.Toplevel") -> tuple[int, int]:
    """`win` が中身を収めるのに必要な `(幅, 高さ)`。

    窓が申告した必要量（`_fit_need`）と、ここでの実測（`winfo_req*`）の**大きい方**
    を返す。ゲート側が「何と比べるか」を窓ごとに書き直さずに済ませるための口で、
    申告を信じきらないのは**申告そのものが間違っている窓**（測り忘れ・測り方の
    誤り）を通さないため。
    """
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
