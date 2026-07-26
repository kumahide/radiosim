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
"""

from __future__ import annotations

import tkinter as tk

# 画面いっぱいまでは広げない（タスクバー・ウィンドウ枠のぶんを残す）。
# 従来はバッチ 80px / 条件探索 90px / ランチャー「画面の 92%」と窓ごとにばらけて
# いたので、ここで 1 つに揃える（値そのものより、揃っていることに意味がある）。
SCREEN_MARGIN = 90


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
    win.update_idletasks()
    lim_w = max(win.winfo_screenwidth()  - SCREEN_MARGIN, min_w)
    lim_h = max(win.winfo_screenheight() - SCREEN_MARGIN, min_h)

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
