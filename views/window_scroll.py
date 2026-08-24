"""
views/window_scroll.py
======================
**入らないときの逃げ道**＝窓の中身を「入る間は今までどおり・入らなくなった時だけ
スクロールする」受け皿へ入れる仕掛け（2.6a1・B-021②／B-023）。

なぜ [views/window_fit](window_fit.py) から分けたか（2026-08-24・I-106/I-107 の回）
-----------------------------------------------------------------------------
関心事が 2 つある：**窓を中身に合わせること**（測って決める）と、**合わせても
入らなかったときに全部へ手が届くこと**（逃げ道）。前者だけが `window_fit` の仕事で、
ここは後者だけを持つ。⚠️ **分けたのは行数のためではない**＝`window_fit` の芯
（測り方を 1 か所に確定させる）は 1 行も動かしておらず、**出し入れの判断は今も
`fit_to_content` が握っている**（ここは器だけ持つ）。

⚠️ **受け皿へ入れても `_fit_need`（必要量）は減らない**。減らすと「スクロール
できるから入っている」ことになり、**免除条項を別の場所に作り直すだけ**になる
（B-021 で 6 回目を通した仕掛けそのもの）。ゲートが「入らない」と言い続け、
そのうえで実害が消えている、という状態を保つ。

使い方（窓の側）::

    body = window_fit.scrollable_body(self)   # 以後 self ではなく body へ pack する
    ...
    window_fit.fit_to_content(self, ...)      # 逃げ道の出し入れも面倒を見る
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any


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

        **変化が無ければそこで返る**（2026-08-24・I-106）＝呼び口は
        `fit_to_content` と `required_size` の 2 つで、どちらも「測る前に必ず呼ぶ」
        設計なので、**同じ測り直しが何度も回る**（実測＝`tests/test_window_fit.py`
        で 298 回・25.0 秒＝ファイル 59 秒の 4 割）。

        ⛔ **早すぎる脱出は B-100 そのもの**（中身が伸びたのに古い値を返すと横断
        ゲートの目が塞がる）。⇒ 脱出の判断は**必ず測ってから**行う:

          1. 先頭の `update_idletasks()` は**省かない**（中身の*要求*サイズは
             アイドルで確定するので、省くと古い値で「変化なし」と判定し得る）。
          2. 比べるのは **前回の値の記憶ではなく、いまキャンバスに入っている値**
             ＝「これから入れるものが、既に入っている」ときだけ返る。記憶を持つと
             *記憶が古い*という B-100 と同じ壊れ方の口を自分で作ることになる。

        ⚠️ **キャンバスの実寸を「前回との差」で見てはいけない**（実装中に踏んだ）＝
        `configure` の直後はまだ窓が広がっていないので実寸は 1 手遅れて追いつく。
        差で見ると**中身が止まっていても毎回 1 回ぶん働く**（実測＝脱出が 8 回中
        2 回しか起きない）。出来上がりで見れば、追いついた時点で静かになる。
        """
        self.win.update_idletasks()
        need_w, need_h = self.body.winfo_reqwidth(), self.body.winfo_reqheight()
        want = (0, 0, max(need_w, self.canvas.winfo_width()),
                max(need_h, self.canvas.winfo_height()))
        if (self._canvas_request() == (need_w, need_h)
                and self._scrollregion() == want):
            return                                 # 既にそうなっている（I-106）
        self.canvas.configure(width=need_w, height=need_h)
        self._sync_scrollregion()
        self.win.update_idletasks()

    def _canvas_request(self) -> "tuple[int, int]":
        """キャンバスに**いま設定されている**要求サイズ（`-width` / `-height`）。"""
        try:
            return (int(self.canvas.cget("width")), int(self.canvas.cget("height")))
        except (tk.TclError, ValueError):
            return (-1, -1)                        # 読めない＝そろっていない扱い

    def _scrollregion(self) -> "tuple[int, int, int, int] | None":
        """いまの `scrollregion`（未設定・読めなければ None）。"""
        try:
            got = [int(float(v)) for v in str(self.canvas.cget("scrollregion")).split()]
        except (tk.TclError, ValueError):
            return None
        return tuple(got) if len(got) == 4 else None       # type: ignore[return-value]

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
