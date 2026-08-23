"""B-119 は**リサイズできる窓**にも起きるのか（実機採取）。

なぜ要るか
----------
B-119（表示スケールを変えたあとドラッグすると窓の寸法が 6px ずつ変わり続ける）は
**ランチャー**でしか実測していない。ランチャーは `resizable(False, False)` なので、
「リサイズできる窓（バッチ・条件探索・中継経路・グラフ・地図）にも同じ欠陥が
あるか」は**未確認のまま**残っている（ISSUES.md の B-119 の対応欄）。

⚠️ **推理で埋めない**（[[feedback-diff-before-gui-repro]]＝B-119 では推理で 5 回
外して 3 回出荷した）。分かっているのは次の 2 点だけで、どちらも「リサイズできる窓は
安全」も「同じく壊れる」も**言えない**：

  * 前提は 5 窓とも揃っている＝`fit_to_content` が `geometry("WxH")` で寸法を
    明示する（`views/window_fit.py:789`）。B-119 の駆動条件（寸法の要求が在ること）は
    ランチャー固有ではない。
  * `rebuild`（b119_frame_slip_probe）で、**スケール変更後に建てたまっさらな窓**も
    同じ 6px を刻んだ ⇒ 欠陥は窓の中身によらない。**ただしその窓も
    `resizable(False, False)` だった。**
  * 逆に「リサイズ可否は無関係」とも言えない＝`minmax` の採取では **min/max を
    掛けた向きだけ暴走が止まった**（片方向）。⇒ **Tk の再適用は寸法の制約を
    見ている経路がある**。リサイズ可否は独立変数として実測する価値がある。

実行::

    & "$env:RADIOSIM_PYTHON" experiments/b119_resizable_windows_probe.py

手順（**モニタは 1 枚でよい**・B-119 の RC6 版と同じ）:
  1. ズーム 100% で起動する（このスクリプトが本物のランチャーを建てる）
  2. ランチャーから見たい窓を開く（バッチ・条件探索・地図。グラフと中継経路は
     計算結果が要るので、先に 1 回実行してから開く）
  3. **起動したまま** Windows の表示スケールを 150% に変える
  4. 窓を 1 つずつタイトルバーでドラッグし、`DRIFT` 行が出るか見る
  5. 対照として `Ctrl+Shift+B`（下）で建てた **BARE-fixed / BARE-resizable** も
     同じようにドラッグする

出るもの（1 行 1 事象）:
    OPEN   新しいトップレベルを見つけた（題・resizable・寸法）
    GRAB   マウスのボタンが落ちた＝**どの窓を掴んだか**（ポインタが入っている矩形／
           **タイトルバーか窓の中身か**も出る＝中身を押しても窓は動かない）
    DROP   離した（掴んでいた時間・寸法の変化・**窓が動いた距離**）。
           ⛔ **動いていなければ「サンプルとして無効」**＝静かなのは当たり前で、
           これを証拠に数えると「リサイズできる窓は安全」という誤った結論が出る
    残り   **まだ動かしていない窓**（`DROP` のたびに出る＝取りこぼし防止）
    DRIFT  その窓の実サイズが変わった（Δ・**掴んでいる窓**・`asked`＝最後に要求した寸法）
    WMGEO  `wm geometry` に**書き込んだ者**（呼び出し元つき）
    REFIT  我々が測り直した（`refit_all`）

`DRIFT` に付く印:
    ≈OS の再スケール      …… スケール変更で OS が窓を ×1.5／×0.667 しただけ
                             （`minsize` で止まった縮小も同じ側）＝**B-119 ではない**
    ★6px 刻み（B-119 の形） …… 幅が 6 の倍数だけ動いた＝本命

読み方（B-119 と同じ）:
  * ある窓で `★6px 刻み` が**同じ向きに並ぶ**のに、その間 `WMGEO` が出ない
    ⇒ その窓も B-119 と同じ（Tk/WM が動かしている）
  * ドラッグしても出ない、または 1 回で止まる ⇒ その窓は該当しない
  * 🔑 **`掴んでいる窓=` と暴れた窓が違う**なら、それ自体が新しい事実
    （1 巡目はこの列が無く、そこが確定できなかった）
  * `BARE-fixed` だけが刻み、`BARE-resizable` が刻まないなら
    ⇒ **リサイズ可否が効いている**（＝5 窓は無傷）。両方刻むなら 5 窓も同じ。

⚠️ **「出なかった」も結果**＝棄却の記録は仮説より価値がある（B-119 の教訓）。
どちらに転んでも ISSUES.md の B-119 へ**実測として**書き戻すこと。
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
import traceback
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# 🔴 **判定表より先に出力の文字コードを決める**（2026-08-23・5 巡目で踏んだ）。
# Windows の既定コンソールは cp932 で、この探針の行（⚠️ ★ ≈ …）を出せずに
# `UnicodeEncodeError` で**採取の途中で死ぬ**＝実機で 1 回スケールを変えてもらった
# その巡が丸ごと無駄になる。⚠️ **印を ASCII に落とす手当ては採らない**＝印は
# ログを読む人の判定そのもの（`★6px 刻み` かどうか）で、削ると読み方が変わる。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
    except Exception:       # 差し替えられた stdout（テスト・パイプ）では黙って諦める
        pass

import main as app_main                      # noqa: E402
from views import theme, window_fit          # noqa: E402

T0 = time.perf_counter()

#: 見つけた窓ごとの前回サイズ（キーは Tk のパス名＝窓より長生きさせないため）。
_SEEN: "dict[str, tuple[int, int]]" = {}

#: 🔴 **自動ドラッグ中はポインタ監視を黙らせる**（2026-08-23・5 巡目で踏んだ）。
#: 監視は「物理ボタンが上がっているのに `_GRABBED` が埋まっている」を*人が手を
#: 離した*と読むので、**合成ドラッグの掴みを 100ms 以内に横取りして消す**。
#: その巡では 8 窓すべてが `23〜101ms 掴んでいた／移動=0px` として台帳に載り、
#: **実際には 3.5 秒暴走していたランチャーまで「動かず」**と記録された。
#: ⇒ 判定表の「移動」列が丸ごと人工物になる＝**静かさの偽証**（この探針が 2 度
#: 潰してきたのと同じ向きの欠陥・[[feedback-user-examples-are-classes]] の同系統④）。
_AUTO: "dict[str, bool]" = {"on": False}

#: いま掴んでいる窓（`GRAB` で入り `DROP` で出る）。`DRIFT` 行に載せる。
_GRABBED: "dict[str, Any]" = {"key": None, "label": "-", "since": 0.0,
                             "size": None, "pos": None}

#: **もう掴んだ窓**（1 秒以上）。`DROP` のたびに「残り」を出すために持つ。
#: 🔴 **1 巡目・2 巡目とも取りこぼした**（2026-08-23）＝2 巡目は 5 窓のうち
#: 中継経路とグラフ、そして**対照の BARE-resizable** を掴み忘れたまま終わった。
#: ⇒ **人の記憶に頼らせない**（[[feedback-promote-recurring-checks]]＝思い出す規則にしない）。
_DRAGGED: "set[str]" = set()

#: 掴んだとみなす最短時間（これ未満は「窓を選んだだけ」）。
_DRAG_MIN_MS = 1000

#: 窓ごとの **★6px 刻み** の回数（累計）。
_DRIFTS: "dict[str, int]" = {}

#: 窓ごとの **★6px 刻み** のうち、**その窓を動かしている最中**に起きた分。
#: 🔑 **判定表が見るのはこちら**（上の `verdict` の註＝累計にはスケール変更直後の
#: 言い直し 1 回が必ず混ざる）。
_DRAG_DRIFTS: "dict[str, int]" = {}

#: 窓ごとの**位置**と、**動いた総量**（前後の差ではなく道のり）。
#: 🔴 **前後の差では駄目**（2026-08-23・自己検査が捕まえた）＝観測のために窓を
#: 往復させるので、**終わってみれば元の位置**になり「動いていない」と誤判定する。
#: 移動ループに入っていたかを見たいのだから、見るべきは**道のり**。
_POS: "dict[str, tuple[int, int]]" = {}
_MOVED: "dict[str, int]" = {}

#: 🔴 **ドラッグとして数える最短の移動量**（2026-08-23・4 巡目で踏んだ）。
#: 4 巡目は「ランチャーを 8 秒掴んでも暴走しない」という**前 3 巡と反対の結果**が
#: 出たが、掴んだ座標が `at=(533,169)`＝**窓の中身**だった。中身を押しても窓は
#: 動かない ⇒ 寸法の再適用が起きない ⇒ 暴走しないのは当たり前で、**サンプルとして
#: 無効**。⇒ **「掴んだ」ではなく「動かした」を数える**（`GRAB` の時間だけを見て
#: いると、無効なサンプルが静かな証拠として台帳に載る）。
_DRAG_MIN_PX = 20

#: OS の再スケールとみなす倍率（96 ⇄ 144dpi）と、その許容誤差。
_SCALE_RATIOS = (144 / 96, 96 / 144)
_SCALE_TOL = 0.02


def log(kind: str, text: str) -> None:
    print(f"{(time.perf_counter() - T0) * 1000:8.0f}ms  {kind:<6} {text}", flush=True)


def label(win) -> str:
    """ログに出す窓の名前（題が無ければクラス名）。"""
    try:
        title = win.title()
    except tk.TclError:
        title = ""
    return f"{title or win.__class__.__name__}[{win}]"


def install_geometry_probe() -> None:
    """`wm geometry` に書き込んだ者を呼び出し元つきで記録する（本家の probe と同型）。

    ⚠️ **これが無いと結論が出ない**＝`DRIFT` が並んでも、我々が要求していたのなら
    B-119 ではなく我々の欠陥。**「誰も要求していないのに動く」ことが B-119 の印。**
    """
    orig = tk.Wm.wm_geometry

    def probed(self, newGeometry=None):       # noqa: N803
        if newGeometry is not None:
            where = " <- ".join(
                f"{Path(f.filename).name}:{f.lineno} {f.name}"
                for f in reversed(traceback.extract_stack()[-4:-1]))
            log("WMGEO", f"{label(self)} {newGeometry!r}   {where}")
        return orig(self, newGeometry)

    tk.Wm.wm_geometry = probed                # type: ignore[assignment]
    tk.Wm.geometry = probed                   # type: ignore[assignment]


def classify(win, was: "tuple[int, int]", now: "tuple[int, int]") -> str:
    """その寸法変化が**何に見えるか**を 1 語で添える。

    🔴 **1 巡目の採取で誤読しかけた**（2026-08-23）＝スケールを変えた瞬間に出る
    `(900,680) -> (1352,1023)` のような大きな変化は **OS が窓を ×1.5 したもの**で、
    B-119 とは無関係。**印を付けておかないと、ログを読む人が毎回この判定を
    やり直すことになる。**

    ⚠️ **軸ごとに見て「片方が合えば OS」とする**＝実データでは倍率どおりに動くのは
    片方だけのことが多い（もう片方は**画面の上限**や **`minsize`** で止まる）。
    実測 `(833,986) -> (1252,1393)` は幅が ×1.503 なのに高さは ×1.41（上限で頭打ち）、
    `(907,680) -> (720,520)` は**両軸とも `minsize` ちょうど**で止まっていた。
    ⇒ 「倍率に一致」に加えて「**その軸の `minsize` で止まっている**」も OS 側とみなす。
    """
    dw, dh = now[0] - was[0], now[1] - was[1]
    try:
        floor = tuple(int(v) for v in win.minsize())
    except (tk.TclError, TypeError, ValueError):
        floor = (0, 0)
    if dw and dh and (dw > 0) == (dh > 0):        # 両軸が同じ向きに大きく動いた
        for axis, (w, n) in enumerate(zip(was, now)):
            if abs(n - w) < 20:
                continue
            if any(abs(n - w * r) <= max(2.0, w * r * _SCALE_TOL)
                   for r in _SCALE_RATIOS) or (n == floor[axis] and n < w):
                return "≈OS の再スケール"
    if dw and abs(dw) % 6 == 0 and abs(dw) <= 12 and abs(dh) in (0, 27):
        return "★6px 刻み（B-119 の形）"
    return ""


def window_under_pointer(root: tk.Tk, px: int, py: int):
    """`(px, py)` に**実際に見えている**トップレベル（無ければ None）。

    🔴 **矩形の当たり判定では駄目**（2026-08-23・ユーザー報告で判明）＝最初の実装は
    `(root, *toplevels)` の順に見て**最初に矩形へ入った窓**を採っていた。窓は重なる
    ので、**ランチャーの上に載っている子窓を掴んでも「ランチャー」と記録される**
    ＝ログの「掴んでいる窓」が嘘をつく。4 巡目の「ランチャーを 8 秒掴んでも静か」は
    これで説明が付く（掴んでいたのは別の窓）。⛔ **この探針の結論は、掴んだ窓の
    同定が正しいことに全面的に乗っている**ので、ここが狂うと全部が狂う。

    ⇒ **OS に聞く**（`WindowFromPoint`）。Z 順もタイトルバー（Tk のウィジェットでは
    ない装飾枠）も OS 側が面倒を見る。`GetAncestor(GA_ROOT)` で装飾枠の窓まで
    遡り、各 Toplevel の `winfo_id()` を同じく遡った値と突き合わせる。

    ⚠️ **聞けない環境では stackorder で代用**＝`wm stackorder` は**下から上**の順に
    返すので、後ろから見て最初に当たったものが最前面。矩形の判定に変わりはないが、
    少なくとも**重なりの順序**は正しくなる。
    """
    wins = [root, *window_fit.toplevels(root)]
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        _GA_ROOT = 2
        target = user32.GetAncestor(
            user32.WindowFromPoint(wintypes.POINT(px, py)), _GA_ROOT)
        if target:
            for win in wins:
                try:
                    if user32.GetAncestor(win.winfo_id(), _GA_ROOT) == target:
                        return win
                except tk.TclError:
                    continue
            return None                   # 別アプリの窓・デスクトップ
    except (ImportError, AttributeError, OSError, ValueError):
        pass                              # Windows 以外＝下の代用へ

    try:                                  # 下から上の順に返る＝後ろが最前面
        order = [root.nametowidget(name)
                 for name in root.tk.call("wm", "stackorder", str(root))]
    except tk.TclError:
        order = wins
    for win in reversed(order):
        try:
            x0, y0, x1, y1 = window_fit.window_rect(win)
        except tk.TclError:
            continue
        if x0 <= px <= x1 and y0 <= py <= y1:
            return win
    return None


def remaining(root: tk.Tk) -> str:
    """**まだ「1 秒以上・20px 以上」動かしていない窓**の一覧（取りこぼし防止）。"""
    rest = []
    for win in (root, *window_fit.toplevels(root)):
        try:
            if not win.winfo_viewable():
                continue          # 隠れている窓（ツールチップの残骸など）は数えない
        except tk.TclError:
            continue
        if str(win) not in _DRAGGED:
            rest.append(label(win).split("[")[0])
    return "／".join(rest)


def poll_pointer(root: tk.Tk) -> None:
    """**どの窓を掴んでいるか**を 100ms ごとに見る（`GRAB` / `DROP`）。

    🔑 **1 巡目の採取で足りなかったのはこれ**＝「暴れた窓」は記録できていたのに
    「掴んだ窓」が記録されておらず、*中継経路を掴んでランチャーが暴走した* のか
    *ランチャーを掴んだ* のかが後から区別できなかった。⇒ **同じログの中で
    突き合わせられる**ようにする。

    ⚠️ **`winfo_containing` では取れない**＝タイトルバーは Tk のウィジェットでは
    ないので、掴んでいる最中は常に `None` が返る（＝いちばん知りたい瞬間に無言）。
    ⇒ **ポインタ座標と窓の矩形**で判定する。
    ⚠️ ボタンの上下は `theme._pointer_is_down`（左右どちらも見る＝主ボタンを
    入れ替えている利用者でも拾う）。
    """
    if _AUTO["on"]:
        # 合成ドラッグの最中＝物理ボタンは上がったままなので、ここが動くと
        # 掴みを横取りして消してしまう（上の `_AUTO` の註）。
        root.after(100, lambda: poll_pointer(root))
        return

    try:
        down = theme._pointer_is_down()
        px, py = root.winfo_pointerxy()
    except tk.TclError:                       # 破棄途中の窓
        root.after(100, lambda: poll_pointer(root))
        return

    if down and _GRABBED["key"] is None:
        win = window_under_pointer(root, px, py)
        x0 = y0 = 0
        size = (0, 0)
        try:
            if win is not None:
                x0, y0, _x1, _y1 = window_fit.window_rect(win)
                size = (win.winfo_width(), win.winfo_height())
        except tk.TclError:
            win = None
        if win is None:
            # 窓の外（デスクトップ・別アプリ）で押した＝掴んでいる窓は無い。
            _GRABBED.update(key="-", label="(窓の外)", since=time.perf_counter(),
                            size=None, pos=None)
        else:
            _GRABBED.update(key=str(win), label=label(win),
                            since=time.perf_counter(), size=size,
                            pos=_MOVED.get(str(win), 0))   # 道のりの基準点
            # **タイトルバーを掴んだか**を出す＝装飾の高さより上にポインタが
            # あれば窓は動く。中身を押しただけなら動かない（無効なサンプル）。
            bar = ("タイトルバー"
                   if py - y0 < window_fit.decoration_size(win)[1]
                   else "⚠️ 窓の中身（動かない可能性）")
            log("GRAB", f"{label(win)}  at=({px},{py})  size={size}  "
                        f"pos=({x0},{y0})  {bar}")
    elif not down and _GRABBED["key"] is not None:
        held = (time.perf_counter() - _GRABBED["since"]) * 1000
        key = str(_GRABBED["key"])
        now = _SEEN.get(key)
        moved = _MOVED.get(key, 0) - (_GRABBED["pos"] or 0)   # 道のり（上の註）
        # 🔴 **「掴んだ」ではなく「動かした」で数える**（上の `_DRAG_MIN_PX` の註）。
        valid = held >= _DRAG_MIN_MS and moved >= _DRAG_MIN_PX and key != "-"
        verdict = "" if valid else "  ⛔ **サンプルとして無効**（窓が動いていない）"
        log("DROP", f"{_GRABBED['label']}  {held:.0f}ms 掴んでいた  "
                    f"{_GRABBED['size']} -> {now}  移動={moved}px{verdict}")
        if valid:
            _DRAGGED.add(key)
            log("残り", remaining(root) or "**なし＝全窓を動かし終えた**")
        _GRABBED.update(key=None, label="-", size=None, pos=None)

    root.after(100, lambda: poll_pointer(root))


def watch(win) -> None:
    """`win` の実サイズの変化を記録する（トップレベル 1 つぶん）。

    ⚠️ **`event.widget is win` を見る**＝子ウィジェットの `<Configure>` も同じ
    バインドタグを通って上がってくるので、絞らないと中身のレイアウトまで拾う。
    """
    key = str(win)
    if key in _SEEN:
        return
    try:
        win.update_idletasks()
        _SEEN[key] = (win.winfo_width(), win.winfo_height())
        resizable = win.resizable()
    except tk.TclError:
        return
    log("OPEN", f"{label(win)}  resizable={resizable}  size={_SEEN[key]}  "
                f"fit={getattr(win, '_fit_size', None)}")

    def on_configure(event: "tk.Event") -> None:
        if event.widget is not win:
            return
        try:
            now = (win.winfo_width(), win.winfo_height())
            pos = window_fit.window_position(win)
        except tk.TclError:
            return
        was_pos = _POS.get(key)
        if was_pos is not None and pos != was_pos:
            _MOVED[key] = (_MOVED.get(key, 0)
                           + abs(pos[0] - was_pos[0]) + abs(pos[1] - was_pos[1]))
        _POS[key] = pos
        was = _SEEN.get(key)
        if was is not None and now != was:
            # **掴んでいる窓を毎行に載せる**＝暴れた窓と掴んだ窓の対応が、
            # 後から突き合わせ無しで読める（1 巡目に足りなかった 1 列）。
            grabbed = _GRABBED["label"] if _GRABBED["key"] is not None else "-"
            if classify(win, was, now).startswith("★"):
                _DRIFTS[key] = _DRIFTS.get(key, 0) + 1
                # **その窓を動かしている最中**の刻みだけ別に数える（判定表はこちら）。
                # ⚠️ 別の窓を掴んでいる間に暴れた分は入れない＝「掴んだ窓と暴れた窓が
                # 違う」こと自体が事実なので、まとめると消える。
                if _GRABBED["key"] == key:
                    _DRAG_DRIFTS[key] = _DRAG_DRIFTS.get(key, 0) + 1
            log("DRIFT", f"{label(win)}  {was} -> {now}  "
                         f"(Δ{now[0] - was[0]:+d},{now[1] - was[1]:+d}) "
                         f"{classify(win, was, now)}  掴んでいる窓={grabbed}  "
                         f"asked={getattr(win, '_fit_asked', None)} "
                         f"fit={getattr(win, '_fit_size', None)}")
        _SEEN[key] = now

    win.bind("<Configure>", on_configure, add="+")


def scan(root: tk.Tk) -> None:
    """開いている窓を拾い直す（新しく開いた窓に配線するため・500ms ごと）。

    ⚠️ **窓が開く契機を 1 つずつ捕まえない**＝製品の 5 窓は開き方がばらばら
    （ランチャーのボタン・結果からの派生・地図からの往復）で、そこへ手を入れると
    「配線を思い出す規則」になる（[[feedback-promote-recurring-checks]]）。
    走査なら**この probe が知らない窓も自動で入る**。
    """
    for win in (root, *window_fit.toplevels(root)):
        watch(win)
    # 閉じた窓の分は落とす（辞書が窓を掴んだままにしない＝B-050 の形）。
    alive = {str(root), *(str(w) for w in window_fit.toplevels(root))}
    for gone in [k for k in _SEEN if k not in alive]:
        _SEEN.pop(gone, None)
    root.after(500, lambda: scan(root))


def make_bare_pair(root: tk.Tk) -> None:
    """対照の 2 窓を**いま**建てる＝中身が同じで、リサイズ可否だけが違う。

    🔑 **これが本題の切り分け**＝製品の 5 窓は中身も寸法も違うので、そこだけ見ても
    「リサイズできるから無傷なのか、たまたま条件が違うのか」が分からない。
    **同じ中身・同じ寸法で可否だけ変えた 2 枚**を、スケール変更**後**に建てて
    並べてドラッグすれば、変数はリサイズ可否 1 つになる。
    """
    for resizable in (False, True):
        win = tk.Toplevel(root)
        win.title(f"BARE-{'resizable' if resizable else 'fixed'}（ドラッグして）")
        win.resizable(resizable, resizable)
        tk.Label(win, text=("リサイズ可" if resizable else "リサイズ不可")
                 + "\nタイトルバーでドラッグ").pack(padx=40, pady=60)
        win.geometry("500x400+%d+%d" % (140 if resizable else 660, 140))
        watch(win)
    log("INIT", "対照の 2 窓を建てた（BARE-fixed / BARE-resizable）")


def frame_hwnd(win) -> int:
    """`win` の**装飾枠**のウィンドウハンドル（取れなければ 0）。"""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        return int(user32.GetAncestor(win.winfo_id(), 2) or 0)   # GA_ROOT
    except (ImportError, AttributeError, OSError, tk.TclError, TypeError):
        return 0


def move_window_synthetically(hwnd: int, seconds: float = 3.0) -> None:
    """窓を**移動モーダルループに入れて動かす**（呼ぶのは別スレッド）。

    🔴 **なぜ自動化するか**（2026-08-23・ユーザー指摘）＝探針を直すたびに人が
    「5 窓を開く → スケールを変える → 1 つずつ掴む」をやり直していた。**検証が
    進まない原因は探針の欠陥ではなく、直すたびに人手の全巡が要る作りのほう**。
    ⇒ 人に残す操作は**スケールの変更 1 回**だけにする。

    🔴 **合成マウスでは子窓が動かない**（2026-08-23・実測）＝`SetCursorPos` ＋
    `mouse_event` は **`tk.Tk` のルート窓は動かせるのに `Toplevel` は 1px も
    動かない**（当たり判定は `HTCAPTION`・スタイルも同一なのに移動ループに入らない）。
    ⇒ **`WM_SYSCOMMAND`／`SC_MOVE` ＋矢印キー**にする。これは**タイトルバーの
    ドラッグと同じ移動モーダルループ**（`WM_ENTERSIZEMOVE` … `WM_EXITSIZEMOVE`）で、
    B-119 で問題になっている「移動中の寸法の再適用」も同じ経路を通る。
    ⚠️ **等価性は仮定せず対照で確かめる**＝ランチャーと `BARE-fixed` は本物の
    ドラッグで暴走することが分かっているので、**この方式でそれが再現しなければ
    その巡は無効**（判定表にそう出す）。

    ⚠️ **Tk のスレッドから呼ばない**＝移動ループの間 Tk の `after` は動けない。
    Tk の API はここから 1 つも触らない（ハンドルは呼ぶ前に測って渡す）。
    ⚠️ 走っている間は**キーボードに触らない**（矢印と Enter を送っている）。
    """
    import ctypes
    user32 = ctypes.windll.user32
    _WM_SYSCOMMAND, _SC_MOVE = 0x0112, 0xF010
    _VK_LEFT, _VK_RIGHT, _VK_RETURN, _KEYUP = 0x25, 0x27, 0x0D, 0x0002
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.15)
    user32.PostMessageW(hwnd, _WM_SYSCOMMAND, _SC_MOVE, 0)
    time.sleep(0.25)
    try:
        steps = max(int(seconds / 0.04), 1)
        for i in range(steps):
            # 往復させる＝画面外へ出さず、かつ**動き続ける**（止まると WM の
            # 再適用も止まり、観測の窓が閉じる）。
            key = _VK_RIGHT if (i // 12) % 2 == 0 else _VK_LEFT
            user32.keybd_event(key, 0, 0, 0)
            user32.keybd_event(key, 0, _KEYUP, 0)
            time.sleep(0.04)
    finally:
        user32.keybd_event(_VK_RETURN, 0, 0, 0)      # 移動を確定して抜ける
        user32.keybd_event(_VK_RETURN, 0, _KEYUP, 0)


def auto_drag_all(root: tk.Tk, targets: "list[str]", done: "Callable[[], None]",
                  seconds: float = 3.0) -> None:
    """`targets`（Tk のパス名）を**順に 1 つずつ自動でドラッグ**する。

    ⚠️ 1 つ終わるたびにメインスレッドへ戻る＝次の窓の座標は**その時点で**測る
    （前の窓のドラッグで配置が変わり得る）。
    """
    import threading
    queue = list(targets)
    _AUTO["on"] = True          # ポインタ監視を黙らせる（横取り防止・上の註）
    drifts0 = {"n": 0}          # その窓のドラッグ**中に増えた**刻みだけ数える

    def step() -> None:
        while queue:
            key = queue.pop(0)
            win = next((w for w in (root, *window_fit.toplevels(root))
                        if str(w) == key), None)
            if win is None:
                continue
            hwnd = frame_hwnd(win)
            if not hwnd:
                continue
            try:
                win.lift()
                _GRABBED.update(key=key, label=label(win),
                                since=time.perf_counter(),
                                size=(win.winfo_width(), win.winfo_height()),
                                pos=_MOVED.get(key, 0))   # 道のりの基準点
                drifts0["n"] = _DRIFTS.get(key, 0)         # 刻みの基準点
            except tk.TclError:
                continue
            log("AUTO", f"{label(win)} を {seconds:.0f} 秒動かします")
            thread = threading.Thread(
                target=move_window_synthetically, args=(hwnd, seconds),
                daemon=True)
            thread.start()
            root.after(200, lambda t=thread: wait(t))
            return
        _AUTO["on"] = False           # 人のドラッグを見る監視へ戻す
        done()

    def wait(thread) -> None:
        if thread.is_alive():
            root.after(200, lambda: wait(thread))
            return
        # ⚠️ **締めるのは余韻の後**＝手を離しても暴走は続く（B-119 の芯はそこ）。
        # 先に締めると、いちばん効く 700ms が誰の分でもなくなる。
        root.after(700, lambda: (finish(), step()))

    def finish() -> None:
        """1 窓ぶんの結果を締める（合成の移動には `DROP` が来ないので自前で）。"""
        key = _GRABBED["key"]
        if key is None:
            return
        # **道のり**で見る（往復させるので前後の差では 0 になり得る＝上の註）。
        moved = _MOVED.get(str(key), 0) - (_GRABBED["pos"] or 0)
        # 刻みも**この巡で増えた分**（累計だと前の窓の暴走が全窓に載る）。
        drifted = _DRIFTS.get(str(key), 0) - drifts0["n"]
        valid = moved >= _DRAG_MIN_PX
        log("DROP", f"{_GRABBED['label']}  移動={moved}px  "
                    f"★6px 刻み={drifted} 回（この巡）"
                    + ("" if valid else "  ⛔ **サンプルとして無効**（窓が動いていない）"))
        if valid:
            _DRAGGED.add(str(key))
        _GRABBED.update(key=None, label="-", size=None, pos=None)

    step()


def verdict(root: tk.Tk) -> None:
    """**判定表**を出して終わる＝ログを読み直さなくても結論が分かる形にする。"""
    print("\n=== 判定 ===", flush=True)
    # 🔴 **数えるのは「ドラッグ中の刻み」**（2026-08-23・6 巡目）＝累計だと
    # **スケール変更直後の言い直し 1 回**（`correct_landing`＝6 窓すべてに出る正常な
    # 1 発）が混ざり、リサイズできる窓まで `🔴 1 回` と出る。**そこが本題の分かれ目
    # なので、混ぜたままの表は結論を逆に読ませる。**
    print(f"{'窓':<34}{'resizable':<11}{'ドラッグ中の刻み':<12}{'（うち変更直後）':<12}"
          f"{'移動':<8}", flush=True)
    for win in (root, *window_fit.toplevels(root)):
        key = str(win)
        hits = _DRAG_DRIFTS.get(key, 0)
        before = _DRIFTS.get(key, 0) - hits
        if key not in _DRAGGED and hits == 0:
            continue
        try:
            resizable = win.resizable()
        except tk.TclError:
            continue
        moved = "動いた" if key in _DRAGGED else "⛔ 動かず"
        mark = f"🔴 {hits} 回" if hits else "✅ 0 回"
        print(f"{label(win).split('[')[0]:<34}{str(resizable):<11}{mark:<12}"
              f"{before:<12}{moved:<8}", flush=True)
    print("\n⚠️ `⛔ 動かず` の行は**サンプルとして無効**（静かで当たり前）", flush=True)
    # 🔑 **対照が暴走していない巡は、巡ごと無効**（2026-08-23）。ランチャーと
    # BARE-fixed は本物のドラッグで暴走することが分かっている＝この 2 つが静かなら、
    # 疑うべきは「リサイズできる窓は安全」ではなく**測り方**（合成の移動が
    # タイトルバーのドラッグと同じ経路を通っていない・スケール変更が効いていない）。
    controls = [key for key, hits in _DRAG_DRIFTS.items() if hits]
    fixed = [str(w) for w in (root, *window_fit.toplevels(root))
             if _resizable_off(w)]
    if any(key in controls for key in fixed):
        print("✅ 対照（リサイズ不可の窓）が暴走している＝この巡は有効", flush=True)
    else:
        print("⛔ **この巡は無効**＝対照（ランチャー・BARE-fixed）まで静かなので、"
              "測り方かスケール変更のほうを疑う", flush=True)


def _resizable_off(win) -> bool:
    try:
        return win.resizable() == (0, 0)
    except tk.TclError:
        return False


def open_product_windows(app) -> None:
    """製品の 5 窓を**探針が開く**（人にボタンを押させない）。

    グラフだけは計算結果が要るので、ランチャーの実行をそのまま起こす
    （`_on_run` は非同期＝窓は後から出る。走査が拾うので待たない）。
    """
    for name, call in (("複数経路", app._on_batch),
                       ("条件探索", app._on_open_scenario),
                       ("中継経路", app._on_open_multihop),
                       ("地図", app._on_open_map),
                       ("グラフ（実行）", app._on_run)):
        try:
            call()
            log("INIT", f"{name} を開いた")
        except Exception as exc:                  # 探針なので 1 つ失敗しても続ける
            log("INIT", f"{name} を開けない: {exc!r}")


def build() -> "tuple[tk.Tk, Any]":
    """製品と同じ手順でランチャーを建てる（probe 用に作り直さない）。"""
    import ctypes
    log("INIT", f"dpi awareness = {app_main._set_dpi_awareness(ctypes.windll)}")
    import sv_ttk
    from core import config, i18n
    from views.launcher import SimLauncher
    root = tk.Tk()
    cfg = config.load_config()
    i18n.set_lang(cfg.get("lang", "en"))
    sv_ttk.set_theme("light")
    theme.apply_fonts(root)
    app = SimLauncher(root, lambda *_a: None)
    root.title("B-119 probe（リサイズできる窓）")
    return root, app


def selftest() -> int:
    """**探針そのもの**を数秒で検査する（実機の手順を 1 つも要求しない）。

    🔴 **これが無かったことが、検証が進まない原因だった**（2026-08-23・ユーザー
    指摘）＝探針を直すたびに「5 窓を開く → スケール変更 → 1 つずつ掴む」の全巡を
    人にやらせ、そこで初めて探針の欠陥（重なりの誤同定・移動を見ていない）が
    露見していた。**計測の道具は、対象を測る前に単体で検査できる。**

    見るのは 4 つ＝①重なった窓の同定 ②タイトルバーの帰属 ③合成ドラッグが
    **本当に窓を動かす**か ④印の分類（実データで検算）。
    """
    ok = True

    def check(name: str, got, want) -> None:
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f"  [{'OK' if good else 'NG'}] {name}: {got!r}"
              + ("" if good else f" ≠ {want!r}"), flush=True)

    root = tk.Tk()
    root.geometry("400x300+100+100")
    root.title("ROOT")
    top = tk.Toplevel(root)
    top.geometry("200x200+150+180")
    top.title("TOP")
    root.update()
    top.lift()
    root.update()

    print("① 重なりの同定（旧実装はここで ROOT と誤答した）", flush=True)
    got = window_under_pointer(root, 250, 260)
    check("重なった領域", got.title() if got else None, "TOP")
    got = window_under_pointer(root, 250, 190)
    check("TOP のタイトルバー", got.title() if got else None, "TOP")
    got = window_under_pointer(root, 120, 130)
    check("ROOT だけの領域", got.title() if got else None, "ROOT")
    check("どの窓の外", window_under_pointer(root, 4000, 4000), None)

    print("② 合成の移動が窓を動かすか（動かなければ全サンプルが無効になる）",
          flush=True)
    print("   ⚠️ 合成マウスは `Toplevel` を動かせなかった（実測）ので "
          "`SC_MOVE` を使う。**子窓で**検査するのはそれが理由。", flush=True)
    import threading
    for name, win in (("Toplevel", top), ("Tk ルート", root)):
        watch(win)                     # 位置の追跡を配線する（道のりを数える側）
        before = _MOVED.get(str(win), 0)
        # ⚠️ **1 回失敗したら 1 度だけやり直す**＝合成入力は「他のアプリが前面に
        # いる瞬間に押すと活性化だけで終わる」ことがあり（実測で 1 回発生）、
        # ここが揺れると**探針の検査そのものが信用できなくなる**。
        for attempt in (1, 2):
            thread = threading.Thread(
                target=move_window_synthetically, args=(frame_hwnd(win), 1.0),
                daemon=True)
            thread.start()
            while thread.is_alive():   # Tk を回しながら待つ（窓が固まらない）
                root.update()
                time.sleep(0.02)
            root.update()
            if _MOVED.get(str(win), 0) - before >= _DRAG_MIN_PX:
                break
            if attempt == 1:
                print("      （1 回目は動かなかった＝やり直す）", flush=True)
        moved = _MOVED.get(str(win), 0) - before
        check(f"{name} が動く", moved >= _DRAG_MIN_PX, True)
        print(f"      道のり {moved}px（前後の差ではない＝往復するので）", flush=True)

    print("③ 印の分類（1〜4 巡目の実ログから）", flush=True)
    cases = [
        ((720, 520), (900, 680), (1352, 1023), "≈OS の再スケール"),
        ((720, 520), (907, 680), (720, 520), "≈OS の再スケール"),   # minsize で停止
        ((780, 520), (833, 986), (1252, 1393), "≈OS の再スケール"),  # 片軸は上限で頭打ち
        ((0, 0), (602, 1197), (596, 1197), "★6px 刻み（B-119 の形）"),
        ((0, 0), (458, 916), (446, 889), "★6px 刻み（B-119 の形）"),
    ]
    for floor, was, now, want in cases:
        probe_win = tk.Toplevel(root)
        probe_win.minsize(*floor)
        check(f"{was}->{now}", classify(probe_win, was, now), want)
        probe_win.destroy()

    print("④ ポインタ監視が合成の掴みを横取りしないか（5 巡目はこれで全滅した）",
          flush=True)
    #: 合成ドラッグ中は物理ボタンが上がったまま＝監視から見れば「手を離した」。
    #: 守りが効いていなければ 1 回目の呼び出しで `_GRABBED` が消える。
    _GRABBED.update(key=".fake", label="FAKE", since=time.perf_counter(),
                    size=(500, 400), pos=0)
    _AUTO["on"] = True
    poll_pointer(root)
    check("自動ドラッグ中は掴みが残る", _GRABBED["key"], ".fake")
    # ⚠️ **裏も見る**＝守りを外せば実際に消えること（消えないなら、この検査は
    # 何も見ていない＝「一度も落ちないゲート」になる）。
    _AUTO["on"] = False
    poll_pointer(root)
    check("人のドラッグでは離したら締める", _GRABBED["key"], None)

    print("⑤ 判定表が数える刻みが、掴んでいる窓のものか（6 巡目で誤読しかけた）",
          flush=True)
    #: 累計（`_DRIFTS`）にはスケール変更直後の言い直しが必ず 1 回混ざる。表が
    #: そちらを出すと、**リサイズできる窓まで `🔴 1 回`** と読める＝結論が逆になる。
    _DRIFTS.clear()
    _DRAG_DRIFTS.clear()
    mark = tk.Toplevel(root)
    mark.geometry("500x400+120+120")
    watch(mark)
    key = str(mark)
    _GRABBED.update(key=None, label="-", size=None, pos=None)
    mark.geometry("494x400")            # 誰も掴んでいない間の 6px（＝言い直し相当）
    root.update()
    _GRABBED.update(key=key, label="MARK", since=time.perf_counter(),
                    size=(494, 400), pos=0)
    mark.geometry("488x400")            # 掴んでいる間の 6px（＝暴走相当）
    root.update()
    check("累計は 2 回", _DRIFTS.get(key, 0), 2)
    check("ドラッグ中は 1 回だけ", _DRAG_DRIFTS.get(key, 0), 1)
    _GRABBED.update(key=None, label="-", size=None, pos=None)
    mark.destroy()

    root.destroy()
    print(f"\n=== selftest: {'PASS' if ok else 'FAIL'} ===", flush=True)
    return 0 if ok else 1


def main() -> None:
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())

    manual = "--manual" in sys.argv
    root, app = build()
    install_geometry_probe()
    state = {"started": False}

    def on_change(dpi: int, changed: bool) -> None:
        log("REFIT", f"dpi={dpi} changed={changed}")
        window_fit.refit_all(root, shrink=changed)
        if not changed or state["started"] or manual:
            return
        # 🔑 **スケールが変わった＝ここから測る**。人の操作はここまでで終わり。
        state["started"] = True
        _DRAGGED.clear()
        _DRIFTS.clear()
        _DRAG_DRIFTS.clear()
        make_bare_pair(root)
        root.after(2500, begin)

    def begin() -> None:
        targets = [str(w) for w in (root, *window_fit.toplevels(root))
                   if w.winfo_viewable()]
        log("AUTO", f"{len(targets)} 窓を順に自動ドラッグします"
                    "（マウスに触らないでください）")
        auto_drag_all(root, targets, lambda: verdict(root))

    theme.watch_display(root, on_change)
    root.bind_all("<Control-Shift-B>", lambda _e: make_bare_pair(root))
    scan(root)
    poll_pointer(root)
    if not manual:
        root.after(1000, lambda: open_product_windows(app))
    print("\n--- 人の操作は **表示スケールを 1 回変えるだけ** ---", flush=True)
    print("    窓は探針が開き、ドラッグも探針が行い、最後に判定表を出します。", flush=True)
    print("    （手で試したいときは --manual、探針自身の検査は --selftest）\n",
          flush=True)
    root.mainloop()


if __name__ == "__main__":
    main()
