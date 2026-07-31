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

import tkinter as tk
from tkinter import ttk
from typing import Any

# 画面いっぱいまでは広げない（タスクバー・ウィンドウ枠のぶんを残す）。
# 従来はバッチ 80px / 条件探索 90px / ランチャー「画面の 92%」と窓ごとにばらけて
# いたので、ここで 1 つに揃える（値そのものより、揃っていることに意味がある）。
SCREEN_MARGIN = 90


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
    escape: "_ScrollEscape | None" = getattr(win, "_fit_scroll", None)
    if escape is not None:
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


def refit_all(root: "tk.Tk | tk.Toplevel") -> None:
    """開いている全ウィンドウを、同じ条件のまま測り直す。

    フォントが変わると（DPI 変更・テーマ変更）**必要な幅も高さも変わる**ので、
    貼り直しただけでは字が大きくなった分だけ見切れる。窓ごとの事情（下限・
    スクロールバー分の加算）は `fit_to_content` が `_fit_kwargs` に残しているので、
    ここは「もう一度同じ呼び出しをする」だけでよい。

    ⚠️ `grow_only` はそのまま尊重する＝DPI が下がったときに窓を縮めはしない
    （ユーザーが広げた窓を勝手に狭めないという既存の約束を、DPI 経路でも破らない）。
    """
    for win in (root, *_toplevels(root)):
        # 窓自身が再測メソッドを持つならそちらを優先する。`_fit_kwargs` に残る
        # 加算値（バッチのスクロールバー幅）は**呼んだ時点の実測**なので、DPI が
        # 変わるとスクロールバー自体が太って数 px 足りなくなる。窓の側で測り直せる
        # なら、その方が正しい。
        refit = getattr(win, "_fit_refit", None)
        kwargs: "dict[str, Any]" = getattr(win, "_fit_kwargs", {})
        if refit is None and not kwargs:
            continue
        try:
            if refit is not None:
                refit()
            else:
                fit_to_content(win, **kwargs)
        except tk.TclError:
            pass          # 破棄途中の窓は飛ばす


def _toplevels(root: tk.Misc) -> "list[tk.Toplevel]":
    """`root` 配下の Toplevel を集める（入れ子も辿る）。"""
    found: list[tk.Toplevel] = []
    stack = list(root.winfo_children())
    while stack:
        w = stack.pop()
        if isinstance(w, tk.Toplevel):
            found.append(w)
        stack.extend(w.winfo_children())
    return found
