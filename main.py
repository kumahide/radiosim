"""
main.py
=======
アプリケーションエントリーポイント。
tkinter ループを起動するだけ。依存の組み立て（DI）もここで行う。
"""

# --- 起動プロファイラ（環境変数 RADIOSIM_PROFILE が真のときのみ作動）-----------
# 本番では import time とゼロコストの no-op になるよう設計。バイナリ起動の
# 各フェーズ実時間を radiosim_profile.log に追記する。詳細計測用の一時計装。
import os as _os
import time as _time

_PROF_ON = bool(_os.environ.get("RADIOSIM_PROFILE"))
_PROF_T0 = _time.perf_counter()
_PROF_MARKS: list = []


def _prof(label: str) -> None:
    if _PROF_ON:
        _PROF_MARKS.append((label, _time.perf_counter() - _PROF_T0))


def _prof_bootloader_seconds() -> float:
    """プロセス生成〜本モジュール開始（ブートローダ＋インタプリタ起動）の秒数。

    Windows では GetProcessTimes でプロセス生成 FILETIME を取得し、現在時刻と
    比較して算出する。取得不可なら -1.0 を返す。"""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        # x64 で擬似ハンドル(-1)が 32bit に切り詰められないよう戻り値/引数型を明示
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.GetProcessTimes.argtypes = (
            [ctypes.c_void_p] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        )
        creation = wintypes.FILETIME()
        exit_ = wintypes.FILETIME()
        kernel_ = wintypes.FILETIME()
        user_ = wintypes.FILETIME()
        h = k32.GetCurrentProcess()
        if not k32.GetProcessTimes(
            h, ctypes.byref(creation), ctypes.byref(exit_),
            ctypes.byref(kernel_), ctypes.byref(user_)
        ):
            return -1.0
        # FILETIME(100ns since 1601-01-01) → Unix epoch 秒
        ft = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        created_unix = ft / 1e7 - 11644473600.0
        return _time.time() - created_unix
    except Exception:
        return -1.0


def _prof_flush() -> None:
    if not _PROF_ON:
        return
    boot = _prof_bootloader_seconds()
    lines = ["=== RadioSim startup profile ==="]
    if boot >= 0:
        lines.append(f"  bootloader+interp (proc start -> module start): {boot:7.3f}s")
    prev = 0.0
    for label, t in _PROF_MARKS:
        lines.append(f"  {label:<32} cum={t:7.3f}s  (+{t - prev:6.3f}s)")
        prev = t
    text = "\n".join(lines) + "\n"
    try:
        # 書き込み先の基準は config の解決器へ一本化する（B-014・B-174）。ここで
        # 判定を再実装しない。**遅延 import 必須**＝モジュール先頭で config を
        # 読むと、下の truststore 注入（他の import より先に実行する必要がある）
        # より前に import 連鎖が走ってしまう。この関数は実行時にしか呼ばれない。
        from core.config import PROFILE_LOG_FILE
        with open(PROFILE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    print(text)


# OS の証明書ストアを ssl モジュールに注入する（企業プロキシ環境の SSL エラー対策）
# 他の import より先に実行しなければならない
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import os
import sys
import threading
import tkinter as tk
from typing import Any

import darkdetect
import sv_ttk

from core import config
from core import i18n
from core import runtime_env
from views import errors, theme, title_bar, window_fit
from views.launcher import SimLauncher

_prof("top-level imports done")


class _ThemeManager:
    """system / light / dark の切替と darkdetect リスナーを管理する。"""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._mode = "system"
        self._listener_started = False

    def apply(self, mode: str) -> None:
        """mode: 'system' | 'light' | 'dark'"""
        self._mode = mode
        if mode == "system":
            sv_ttk.set_theme("dark" if darkdetect.isDark() else "light")
            if not self._listener_started:
                self._listener_started = True
                self._start_listener()
        else:
            sv_ttk.set_theme(mode)

    def _start_listener(self) -> None:
        def _cb(theme: str) -> None:
            if self._mode == "system":
                self._root.after(0, sv_ttk.set_theme, theme.lower())
        threading.Thread(target=darkdetect.listener, args=(_cb,), daemon=True).start()


# Per-Monitor DPI Aware **v2**（Windows 10 1703+）。`SetProcessDpiAwarenessContext`
# へ渡す擬似ハンドル。
_PER_MONITOR_AWARE_V2 = -4


def _set_dpi_awareness(windll: Any) -> str:
    """使える一番強い DPI 認識を設定し、その名前を返す（強い順に試す）。

    **v2 を先に試す**理由（I-054）＝v1（`SetProcessDpiAwareness(2)`）が面倒を見るのは
    窓とクライアント領域だけで、**メニューバーは OS が描いたまま拡大されない**。
    実測で確認済み＝帯（ファイル / 設定 / ヘルプ）は Tk の管轄外で、`TkMenuFont` を
    書き換えても `tk.Menu(font=…)` を直に指定しても 1px も変わらない（変わるのは
    ドロップダウンだけ）。⇒ **アプリ側からは字を大きくできない。**

    v2 は非クライアント領域（枠・タイトルバー・**win32 メニュー**）まで OS が自動で
    スケールする。⇒ 「メニューは OS のメニューフォントに合わせる」という
    [views/theme.py](views/theme.py) の方針を**撤回せずに**果たせる（当初案の
    「`TkMenuFont` を DPI で書き換える＝方針の撤回」は、そもそも効かない）。

    Returns:
        `per-monitor-v2` / `per-monitor` / `system` / `none`。
    """
    import ctypes

    try:
        user32 = windll.user32
        user32.SetProcessDpiAwarenessContext.restype  = ctypes.c_bool
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(_PER_MONITOR_AWARE_V2)):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        windll.shcore.SetProcessDpiAwareness(2)        # Windows 8.1+
        return "per-monitor"
    except Exception:
        pass
    try:
        windll.user32.SetProcessDPIAware()             # Vista/7 フォールバック
        return "system"
    except Exception:
        return "none"


def _setup_windows_platform() -> None:
    """DPI 対応とタスクバーグループ化（Windows のみ）。tk.Tk() より前に呼ぶこと。"""
    if sys.platform != "win32":
        return
    import ctypes
    _set_dpi_awareness(ctypes.windll)
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "BearValleyAICraftworks.RadioSimPro"
        )
    except Exception:
        pass


def _set_window_icon(root: tk.Tk) -> None:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(base, "icon.png")
    if not os.path.exists(icon_path):
        return
    try:
        from PIL import Image, ImageTk
        img = Image.open(icon_path)
        photo = ImageTk.PhotoImage(img)
        root.iconphoto(True, photo)  # type: ignore[arg-type]
        root._icon_photo = photo  # type: ignore[attr-defined]  # GC 対策
    except Exception:
        pass


def _warn_if_not_the_declared_interpreter() -> None:
    """宣言と違う Python で起動していたら**警告する**（止めはしない）。

    起動だけ門が無く、素の `python main.py` が**別の依存版で黙って動いていた**
    （B-056）。⛔ **止めない**＝配布 exe は宣言を持たないので `interpreter_mismatch()`
    が `None` を返し、そもそもここへ来ない。それでも例外を投げないのは、開発機で
    exe を試す経路（宣言あり × 走っているのは exe）を利用者と同じ形に保つため。

    ⚠️ **ログと stderr の両方へ出す**＝GUI アプリなのでログだけでは気づけないが、
    ターミナルから起動した人には stderr が見える。ダイアログにはしない（毎回出る
    ものを目の前に置くと、読まずに閉じる習慣がつく＝警告が死ぬ）。
    """
    pair = runtime_env.interpreter_mismatch()
    if pair is None:
        return
    message = runtime_env.mismatch_message(*pair)
    config.logger.warning(message.replace("\n", " / "))
    print(f"[WARNING] {message}", file=sys.stderr)


def main() -> None:
    _prof("main() enter")
    _warn_if_not_the_declared_interpreter()
    _setup_windows_platform()
    root = tk.Tk()
    # 🔴 **表示前に隠す**（I-132 再発）＝`tk.Tk()` は生成と同時に窓を画面へ出す。
    # 出したあとに `apply_title_bar_theme` で DWM へダーク/ライトを申告しても、
    # 非クライアント領域（タイトルバー）はその場では塗り替わらないことがある
    # （実装上の落とし穴③・[views/theme.py](views/theme.py) の docstring）。設定
    # 画面での切替が効くのは、ダイアログを閉じた際のフォーカス復帰が非クライアント
    # 領域の再描画を誘発するため＝**起動直後はその契機が無く、次に何かが窓を
    # アクティブ化するまで白いまま**残っていた。⇒ 申告が終わって
    # `deiconify()` するまで窓を見せない（テストの `root.withdraw()` と同じ形）。
    root.withdraw()
    # 🔴 **`withdraw()` のままでは DWM 申告が黙って失敗する**（B-176＝上の対策の
    # 取り残し）＝Windows は非クライアント領域を持つ「装飾つき HWND（ラッパー）」
    # を、窓が一度も map（表示）されないうちは作らない。`winfo_id()` が返すのは
    # クライアント側の子 HWND のままで、`GetParent()` は 0 を返す＝
    # `DwmSetWindowAttribute` に渡す先が無く、成功も失敗も返らず何も起きない
    # （実機で実測・`hex(GetParent(winfo_id()))` が `withdraw()` のままだと
    # 常に `0`）。⇒ **画面には出さずにラッパーだけ作る**＝`-alpha 0`（完全透明）
    # にしてから `deiconify()` し、`update_idletasks()` で map 処理を即時に
    # 終わらせる（`update()` は外部イベントも処理してしまうので使わない）。
    root.attributes("-alpha", 0.0)
    root.deiconify()
    root.update_idletasks()
    # 以降に作る窓・コールバックすべてを覆うので、**何よりも先に**入れる
    # （ここより前で落ちたものは stderr へ消える＝I-059）。
    errors.install(root)
    _prof("tk.Tk() created")
    _set_window_icon(root)
    manager = _ThemeManager(root)
    cfg = config.load_config()
    # 利用者が足した言語を先に登録する（`set_lang` は登録済みの言語しか受けない）。
    # ⚠️ **読めなくても起動は続ける**＝報告はランチャーが画面で伝える。
    # 同梱の読み取り専用 `LANG_DIR` と、利用者が書ける `USER_LANG_DIR`（I-130・
    # 非ポータブルでは別の場所）の両方を見る。ポータブル配置では同じ場所を
    # 二重に走査するだけ（同じ結果になる）。
    i18n.load_external(config.LANG_DIR, config.USER_LANG_DIR)
    # 設定ファイルが在ればその中身、無ければ初回既定の解決（I-127＝インストーラで
    # 選ばれた言語 → OS の表示言語 → "en"）。以後は利用者の選択が常に優先。
    i18n.set_lang(config.startup_lang(cfg))
    _prof("config/i18n done")
    manager.apply(cfg.get("theme", "system"))
    # タイトルバー・枠は Windows が描くので sv_ttk が届かない（I-132）。切替の
    # たびに <<ThemeChanged>> で開いている全トップレベルへ当て直す（新規に開いた
    # 窓は各々の生成元が `title_bar.follow_title_bar` で予約する＝views/dialogs.py 等）。
    # ⚠️ **ここ（root）だけが「生成直後に当てられる」**＝上の `-alpha 0` +
    # `deiconify()` で**ラッパーを先に作ってある**から。子窓にその契機は無いので、
    # 同じ書き方をすると黙って空振りする（B-179＝B-176 の知見の取り残し）。
    title_bar.apply_title_bars(root)
    root.bind("<<ThemeChanged>>", lambda _e: title_bar.apply_title_bars(root), add="+")
    # 全窓の既定フォントを sv_ttk の本文フォントへ揃える（窓ごとの font= を廃止）。
    # テーマ適用の**後**に呼ぶ（sv.tcl が名前付きフォントを作るのがテーマ読み込み時）。
    theme.apply_fonts(root)
    # 表示環境（DPI・画面サイズ）が変わったら、フォントを貼り直して窓を測り直す。
    # sv_ttk のフォントはピクセル指定＝Tk 任せでは 1px も変わらないので、
    # 「窓だけ大きくなって字は小さいまま」になる（2026-07-26 のユーザー報告）。
    # 画面サイズも寸法の入力（fit_to_content の上限）なので同じ契機で拾う＝B-022。
    # DPI が変わったときだけ縮む方向にも測り直す（I-053＝150% → 100% で窓が戻る）。
    theme.watch_display(
        root, lambda _dpi, dpi_changed: window_fit.refit_all(root, shrink=dpi_changed))
    _prof("sv-ttk theme applied")
    SimLauncher(root, manager.apply)
    _prof("SimLauncher built")
    # 窓は既に map 済み（上の `-alpha 0` トリック）＝残るは見せるだけなので
    # `deiconify()` でなく透明度を戻す（B-176）。
    root.attributes("-alpha", 1.0)
    if _PROF_ON:
        root.update()  # 初回描画を強制してレイアウト/ペイント時間を計測に含める
        _prof("first paint (update)")
        _prof_flush()
    root.mainloop()


if __name__ == "__main__":
    main()
