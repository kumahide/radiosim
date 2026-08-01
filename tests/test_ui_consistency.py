"""
tests/test_ui_consistency.py
============================
**窓をまたいで同じであるべきもの**を縛る横断ゲート。

なぜ要るか
----------
2.6 の UI/UX パスで「実行ボタンは 3 フローとも同じ表記・同じ位置（進捗バーの
右）」と決めて記録したのに、**実際に揃っていたのはランチャーだけ**だった
（条件探索はバーの左、バッチは下段の編集ボタン列の右端）。決めごとを散文でしか
持っていないと、次の窓を作るときに**型が静かにずれる**——しかも窓ごとに見れば
どれも「おかしくない」ので、実際に並べて触るまで気づけない。

⇒ 「揃っている」ことを**全窓を実際に起こして機械的に確認**する。窓が増えたら
`_WINDOWS` に足す（足し忘れは新しい窓が検査されないという形で効くので、
window_fit の登録漏れ検出と同じ理由でここも一覧を 1 つに保つ）。

ここで守る型
------------
1. **実行ボタンは進捗バーと同じ帯にあり、帯の右端**（I-029）。
2. **判定色（OK / NG）の出所は theme**（画面のハードコードを増やさない）。
3. **画面で bold を使わない**（強調は配置と余白で作る＝2026-08-01 ユーザー決定）。
   レポート HTML の太字は**別の設計言語**（印刷物）なのでここでは見ない。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import i18n
import simulation as sim
from conftest import make_themed_root


def _launcher(root):
    from views.launcher import SimLauncher
    return SimLauncher(root, lambda _t: None)


def _params():
    return sim.SimParams(config.DEFAULT_CONFIG)


# 実行フローを持つ窓＝(名前, 生成関数)。生成関数は (root, launcher) を受けて窓を返す。
_WINDOWS = [
    ("launcher", lambda root, app: app.root),
    ("batch",    lambda root, app: app.ensure_batch_window()),
    ("scenario", lambda root, app: (app._on_open_scenario(), app._scenario_win)[1]),
    ("multihop", lambda root, app: (app._on_open_multihop(), app._multihop_win)[1]),
]


@pytest.fixture
def app_windows():
    """ランチャーと 3 つの窓を開いた状態（テストごとに作り直す）。"""
    pytest.importorskip("tkinter")
    root = make_themed_root()
    root.withdraw()
    i18n.set_lang("ja")
    app = _launcher(root)
    try:
        yield app, {name: make(root, app) for name, make in _WINDOWS}
    finally:
        root.destroy()


# ============================================================
# 1. 実行ボタンの位置
# ============================================================
@pytest.mark.parametrize("name", [n for n, _ in _WINDOWS])
def test_run_button_sits_at_the_right_end_of_the_progress_bar(app_windows, name):
    """実行ボタンは**進捗バーと同じ帯の右端**にあること。

    「同じ帯」まで縛るのが要点＝バッチは実行ボタンを別フレーム（編集ボタン列）に
    置いており、window_fit のような寸法ゲートでは検出できなかった。
    """
    app, wins = app_windows
    win = wins[name]
    owner = app if name == "launcher" else win
    # 属性名も 4 窓で同じ（`_run_btn` / `_prog_bar`）＝ランチャーだけ `run_btn` と
    # 公開名で持っており、横断で検査しようとして初めて食い違いが見えた。
    run_btn  = owner._run_btn
    prog_bar = owner._prog_bar

    assert run_btn.winfo_parent() == prog_bar.winfo_parent(), (
        f"{name}: 実行ボタンが進捗バーと別の帯にある"
    )
    assert run_btn.pack_info()["side"] == "right", f"{name}: 実行ボタンが右端でない"
    assert prog_bar.pack_info()["side"] == "left", f"{name}: 進捗バーが左で伸びていない"
    assert prog_bar.pack_info()["expand"] in ("1", 1, True), (
        f"{name}: 進捗バーが伸びない（ボタン位置が中身の量で動く）"
    )
    # 実測でも右にあること（pack の順序を間違えると side=right でも内側に来る）。
    # ⚠️ `update_idletasks` では足りない＝スクロール受け皿（Canvas の中に窓を置く
    # 形）の中身は **Configure イベントが回るまで幅が 1 のまま**で、実測が全部 0 に
    # なる。イベントまで回す `update()` を使う。
    win.update()
    assert run_btn.winfo_x() > prog_bar.winfo_x(), f"{name}: 実行ボタンがバーより左"


@pytest.mark.parametrize("name", [n for n, _ in _WINDOWS])
def test_run_button_is_the_only_accent_button(app_windows, name):
    """Accent（塗り）は**「走らせる」ボタンだけ**（強調の軸＝意味の軸）。"""
    app, wins = app_windows
    win = wins[name]
    owner = app if name == "launcher" else win
    accents = _accent_buttons(win if name != "launcher" else app.root)
    assert accents, f"{name}: Accent ボタンが 1 つも無い"
    assert accents == [str(owner._run_btn)], (
        f"{name}: 走らせる以外にも Accent が付いている: {accents}"
    )


def _accent_buttons(widget) -> list:
    """配下の Accent.TButton を文字列パスで集める。

    ⚠️ 別の Toplevel（他の窓・ダイアログ）へは降りない＝Tk では Toplevel も
    ルートの子なので、素直に再帰すると全窓を数えてしまう。
    """
    import tkinter as tk
    found = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Toplevel):
            continue
        try:
            if str(child.cget("style")) == "Accent.TButton":
                found.append(str(child))
        except Exception:
            pass
        found.extend(_accent_buttons(child))
    return found


# ============================================================
# 2. 判定色の出所
# ============================================================
def test_verdict_colors_come_from_theme_in_every_window(app_windows):
    """OK / NG の色は `theme.verdict_colors` を出所にし、**全窓で同じ**であること。

    ⚠️ 条件探索だけが色分けされ、中継経路は同色だった（レポート側は両方とも
    色分けしている＝画面だけが落ちていた）。色を窓ごとに足すと出所が増えるので、
    出所を 1 つにしたうえで「使っていること」をここで縛る。
    """
    from views import theme
    _, wins = app_windows
    expected = theme.verdict_colors(wins["scenario"])
    for name in ("scenario", "multihop"):
        tree = wins[name]._tree
        for key in ("ok", "ng"):
            got = str(tree.tag_configure(key, "foreground"))
            assert got == expected[key], (
                f"{name}: 判定色 {key} が theme と違う（{got!r} != {expected[key]!r}）"
            )


# ============================================================
# 3. 画面では bold を使わない
# ============================================================
def test_no_bold_font_on_screen(app_windows):
    """どの窓のウィジェットにも太字を使わないこと（2026-08-01 ユーザー決定）。

    理由＝**かえって読みにくい**。強調は配置・余白・区切り線で作る。
    ⚠️ レポート HTML（印刷物）の `font-weight:bold` は別の設計言語なので対象外。
    """
    _, wins = app_windows
    offenders: list[str] = []
    for name, win in wins.items():
        _collect_bold(win, name, offenders)
    assert not offenders, "画面で bold が使われている: " + ", ".join(offenders)


def _collect_bold(widget, name: str, out: list) -> None:
    for child in widget.winfo_children():
        try:
            font = child.cget("font")
        except Exception:
            font = ""
        if font:
            spec = str(widget.tk.call("font", "actual", font))
            if "bold" in spec:
                out.append(f"{name}:{child.winfo_class()}")
        _collect_bold(child, name, out)
