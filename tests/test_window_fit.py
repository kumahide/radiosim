"""
tests/test_window_fit.py
========================
**全ウィンドウ横断**の見切れゲート。

なぜ窓ごとの手書きテストでは駄目だったか
----------------------------------------
見切れはこのプロジェクトで最も繰り返している不具合クラス（B-002 / B-007 /
I-000 / I-023 / I-024）で、そのつど「その窓だけ」を実測追従に直し、「その窓
だけ」のテストを足してきた。結果、

  - **次の窓**（地図ウィンドウは 2.5b2 までゲートが 1 本も無く、`geometry("900x680")`
    のリテラルのままだった）
  - **次の増え方**（起動時しか測らない＝バッチは CSV インポートで列が広がっても
    窓は広がらないままだった）

で必ず再発した。つまり欠けていたのは個々の修正ではなく、**新しい窓・新しい
増え方が自動的に検査対象になる仕組み**。ここでは 3 つを固定する：

  1. **登録された全窓**が、開いた時点で中身を収めていること。
  2. **中身が増える操作のあと**も収めていること（窓ごとに「増やし方」を書く）。
  3. **登録漏れが起きないこと**＝`views/` で Toplevel を作る場所を静的に洗い出し、
     登録も除外もされていない窓があればここが落ちる（新しい窓を足した人が
     テストを書き忘れても気づける＝[[feedback-promote-recurring-checks]] の昇格）。

⚠️ 測定は必ず `make_themed_root()` で行う（素の Tk 既定フォントは実機より小さく、
実物より狭い前提でゲートが緑になる。2.5b2 に実際そうなっていた）。
"""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import i18n
import simulation as sim
from conftest import make_themed_root
from views import window_fit

_VIEWS_DIR = os.path.join(os.path.dirname(__file__), "..", "views")

_PARAMS = {
    "start": "34.5429, 132.4118", "end": "34.5389, 132.4050",
    "h_tx": "30.0", "h_rx": "10.0", "freq": "2400.0", "p_tx": "20.0",
    "gain_tx": "3.0", "gain_rx": "3.0", "sens": "-85.0", "veg_h": "10.0",
    "k_factor": "10.0", "samples": "50", "diff_method": "deygout",
    "env_type": "los", "rain_rate": "0.0",
}


# ============================================================
# 窓のレジストリ（新しい窓はここへ足す）
# ============================================================
def _open_launcher(root):
    from views.launcher import SimLauncher
    app = SimLauncher(root, lambda _t: None)
    return root, app


def _open_batch(root):
    from views.batch_builder import BatchBuilderWindow
    win = BatchBuilderWindow(root, sim.SimParams(_PARAMS))
    return win, win


def _open_scenario(root):
    from views.scenario import ScenarioWindow
    win = ScenarioWindow(root, sim.SimParams(_PARAMS))
    return win, win


def _open_map(root, monkeypatch):
    """地図ウィンドウ（`TkinterMapView` はフェイクに差し替える）。

    差し替えるのは**タイル取得のためにネットワークへ出る部品だけ**で、見切れの
    起点であるモードバー・各モードのパネル・ステータス行は本物を組み立てる。
    """
    import tkinter as tk
    from tkinter import ttk
    from types import SimpleNamespace

    class _FakeMap(ttk.Frame):
        def __init__(self, master, **_kw):
            super().__init__(master)
            self.canvas = tk.Canvas(self, width=200, height=150)
            self.canvas.pack(fill="both", expand=True)
            self.running = False

        def set_tile_server(self, *a, **k): pass
        def set_position(self, *a, **k): pass
        def set_zoom(self, *a, **k): pass
        def set_address(self, *a, **k): pass
        def add_left_click_map_command(self, *a, **k): pass
        def set_polygon(self, *a, **k): return SimpleNamespace(delete=lambda: None)
        def set_marker(self, *a, **k): return SimpleNamespace(delete=lambda: None)
        def set_path(self, *a, **k): return SimpleNamespace(delete=lambda: None)
        def convert_canvas_coords_to_decimal_coords(self, *a, **k): return (35.0, 139.0)
        def get_position(self): return (35.0, 139.0)
        def get_zoom(self): return 8

    import views.map_window as mw
    monkeypatch.setattr(mw, "TkinterMapView", _FakeMap)
    win = mw.MapWindow(root, {"proxy_url": ""})
    return win._win, win


# name -> (窓を開く関数, 中身を増やす操作 or None)
#
# 「中身を増やす操作」は **その窓で実際に中身が増える経路**を書く（2 の検査）。
# None は「開いたあとに中身が増える経路を持たない窓」の明示。
_WINDOWS = {
    "launcher": (_open_launcher, None),
    "batch":    (_open_batch,    lambda win, owner: _grow_batch(owner)),
    "scenario": (_open_scenario, lambda win, owner: _grow_scenario(owner)),
    "map":      (_open_map,      None),
}


def _grow_batch(win) -> None:
    """CSV インポート（**長い備考**で列が広がる）。

    ⚠️ ここで `_fit_width_to_content()` を呼んではいけない。**製品の経路**
    （`replace_rows` ＝ インポート本体）を叩き、その中で窓が追従することを見る。
    テスト側が fit を呼ぶと、製品が呼び忘れていても緑になる。

    2.5b2 まで、起動後に列が広がっても窓は広がらなかった（`_fit_initial_width`
    が `__init__` からしか呼ばれていなかった）。
    """
    win.replace_rows([[
        "path-with-a-very-long-identifier",
        "34.542900, 132.411800", "34.538900, 132.405000",
        "30", "10", "2400", "3", "3",
        "非常に長い備考テキスト：列幅同期がここまで列を広げる",
    ]])


def _grow_scenario(win) -> None:
    """比較条件を上限（5 列）まで足す＝実機で条件 5 が見切れた経路（I-024）。"""
    import scenario as scn
    while len(win._cmp_cols) < scn.MAX_COMPARE_CONDITIONS:
        win._add_condition_column()


# ============================================================
# 1・2: 全窓が中身を収めていること
# ============================================================
def _assert_fits(win, label: str) -> None:
    need_w, need_h = window_fit.required_size(win)
    size = getattr(win, "_fit_size", None)
    assert size is not None, (
        f"[{label}] 窓が window_fit.fit_to_content() を通っていない"
        "（寸法をリテラルで持つと中身が増えた日に黙って切れる）。"
    )
    lim_w = win.winfo_screenwidth()  - window_fit.SCREEN_MARGIN
    lim_h = win.winfo_screenheight() - window_fit.SCREEN_MARGIN
    assert size[0] >= min(need_w, lim_w), (
        f"[{label}] 中身が窓幅に入らない（必要 {need_w}px / 窓 {size[0]}px）。"
        "右端のウィジェットが見切れる。"
    )
    assert size[1] >= min(need_h, lim_h), (
        f"[{label}] 中身が窓高に入らない（必要 {need_h}px / 窓 {size[1]}px）。"
        "下端のウィジェットが見切れる。"
    )


@pytest.mark.parametrize("lang", ["ja", "en"])
@pytest.mark.parametrize("name", sorted(_WINDOWS))
def test_window_fits_its_content(name, lang, monkeypatch):
    """開いた時点で全窓が中身を収めていること（言語を変えても）。

    日本語見出しは英語より 1 割ほど広い＝どちらか片方でしか測らないと、もう
    片方で見切れる（B-002 がまさにそれ）。
    """
    prev = i18n._lang
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang(lang)
        opener, _ = _WINDOWS[name]
        win, _owner = (opener(root, monkeypatch) if name == "map" else opener(root))
        _assert_fits(win, f"{name}/{lang}")
    finally:
        i18n.set_lang(prev)
        root.destroy()


@pytest.mark.parametrize(
    "name", sorted(n for n, (_, grow) in _WINDOWS.items() if grow is not None))
def test_window_still_fits_after_content_grows(name, monkeypatch):
    """**あとから中身が増えても**収まっていること。

    起動時だけ測る実装は「開いた瞬間は正しい」ので、初期表示しか見ないゲートでは
    永久に捕まらない。I-024（条件 5 の見切れ）は実際にその形で出た。
    """
    prev = i18n._lang
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        opener, grow = _WINDOWS[name]
        win, owner = opener(root)
        grow(win, owner)
        root.update_idletasks()
        _assert_fits(win, f"{name}（中身を増やしたあと）")
    finally:
        i18n.set_lang(prev)
        root.destroy()


def test_growing_content_does_not_shrink_the_window(monkeypatch):
    """広がった窓を、あとの操作で勝手に狭めないこと。

    ユーザーが手で広げた窓／中身に合わせて広げた窓を縮めるのは、見切れの逆側の
    嫌がらせになる（条件を 1 つ消したら窓が縮んで他の入力が見えなくなる、等）。
    """
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        win, owner = _open_scenario(root)
        _grow_scenario(owner)
        root.update_idletasks()
        wide = win._fit_size[0]
        owner._remove_condition_column()
        root.update_idletasks()
        assert win._fit_size[0] == wide
    finally:
        root.destroy()


# ============================================================
# DPI が変わったら測り直すこと
# ============================================================
def test_windows_are_refitted_when_dpi_grows(monkeypatch):
    """DPI が上がってフォントが大きくなったら、窓も広がること。

    2026-07-26 のユーザー報告は「窓は DPI に追従して変わるのに字が変わらない」
    だった。字を追従させると今度は**必要な幅も高さも増える**ので、貼り直しと
    測り直しは対で要る（片方だけ直すと、字は大きくなったが窓は元のままで
    見切れる＝これまでと同じクラスの不具合になる）。
    """
    from views import theme

    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        theme.apply_fonts(root, dpi=96)
        win, owner = _open_scenario(root)
        before = win._fit_size

        theme.apply_fonts(root, dpi=144)     # 150% のモニタへ移した相当
        window_fit.refit_all(root)
        root.update_idletasks()

        assert win._fit_size[0] > before[0], (
            f"DPI が上がってフォントが大きくなったのに窓幅が変わらない"
            f"（{before[0]}px のまま）。右端が見切れる。"
        )
        _assert_fits(win, "scenario（DPI 144）")
    finally:
        theme.apply_fonts(root, dpi=96)      # 名前付きフォントは他テストと共有
        root.destroy()


def test_refit_all_keeps_each_window_s_own_conditions(monkeypatch):
    """測り直しで窓ごとの下限・加算が失われないこと。

    `refit_all` は窓ごとの事情（下限幅・スクロールバー分の加算）を知らないので、
    `fit_to_content` が残した `_fit_kwargs` をそのまま使う。ここが失われると
    バッチだけスクロールバー分だけ狭くなる、といった形で静かに壊れる。
    """
    root = make_themed_root()
    try:
        root.withdraw()
        i18n.set_lang("ja")
        win, _ = _open_batch(root)
        kwargs = dict(win._fit_kwargs)
        window_fit.refit_all(root)
        assert win._fit_kwargs == kwargs
        assert kwargs["extra_w"] > 0, "バッチの加算（スクロールバー＋外周）が消えている"
        # バッチは「列幅同期からやり直す」再測を自前で持つ＝DPI が変わって
        # スクロールバー自体が太っても正しく測れる（保存済みの加算値は古くなる）。
        assert getattr(win, "_fit_refit", None) is not None
    finally:
        root.destroy()


# ============================================================
# 3: 登録漏れが起きないこと（新しい窓を自動で対象にする）
# ============================================================
# Toplevel を作るが「窓の見切れ」の対象外にするもの。**理由を必ず書く**
# （空の除外リストは運用で必ず膨らむので、理由ごと残して見直せるようにする）。
_EXEMPT = {
    ("dialogs.py", "_make"):
        "モーダルダイアログ＝サイズを指定せず自然サイズで開く（geometry は位置のみ）。"
        "中身はメッセージ 1 つとボタンで、増える経路が無い。",
    ("launcher.py", "_show"):
        "ツールチップ（overrideredirect）。自然サイズ・位置のみ指定。",
    ("launcher.py", "_on_proxy_settings"):
        "プロキシ設定ダイアログ＝サイズを指定せず自然サイズで開く（位置のみ）。",
    ("launcher.py", "_show_readme_text"):
        "README ビューア＝スクロール前提の閲覧窓（resizable・中身は本文テキスト）。",
}


def _toplevel_sites() -> "list[tuple[str, str]]":
    """`views/` で `tk.Toplevel` を作っている場所を (ファイル, 関数/クラス名) で返す。

    - `tk.Toplevel(...)` の呼び出し
    - `class X(tk.Toplevel)` の宣言
    の両方を拾う（バッチ・条件探索は後者、地図・ダイアログは前者）。
    """
    sites: list[tuple[str, str]] = []
    for fname in sorted(os.listdir(_VIEWS_DIR)):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(_VIEWS_DIR, fname)
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=fname)
        parents: dict[ast.AST, str] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    parents[child] = node.name
                elif node in parents:
                    parents[child] = parents[node]
        for node in ast.walk(tree):
            hit = False
            if isinstance(node, ast.ClassDef):
                hit = any(
                    isinstance(b, ast.Attribute) and b.attr == "Toplevel"
                    for b in node.bases
                )
                if hit:
                    sites.append((fname, node.name))
                    continue
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute) and fn.attr == "Toplevel":
                    sites.append((fname, parents.get(node, "<module>")))
    return sorted(set(sites))


# レジストリの窓 → 実装上の Toplevel 生成箇所の対応（登録済みであることの証明）。
_REGISTERED_SITES = {
    ("batch_builder.py", "BatchBuilderWindow"): "batch",
    ("scenario.py",      "ScenarioWindow"):     "scenario",
    ("map_window.py",    "__init__"):           "map",
}


def test_every_window_is_registered_or_exempt():
    """`views/` の Toplevel 生成箇所が、登録か除外のどちらかであること。

    **これがこのファイルの本体**。個別の窓のテストは書き忘れれば存在しないが、
    ここは views を静的に走査するので、新しい窓を足した時点で必ず落ちる。
    落ちたら「レジストリ `_WINDOWS` に足す」か「理由つきで `_EXEMPT` に足す」の
    どちらかを選ぶことになる＝判断が記録に残る。
    """
    unknown = [s for s in _toplevel_sites()
               if s not in _REGISTERED_SITES and s not in _EXEMPT]
    assert not unknown, (
        f"見切れゲートの対象に入っていない窓がある: {unknown}。"
        "tests/test_window_fit.py の _WINDOWS へ登録するか、理由を書いて "
        "_EXEMPT へ入れること。"
    )


def test_registry_has_no_stale_entries():
    """レジストリ・除外リストに、実在しない窓が残っていないこと（掃除漏れ検出）。"""
    sites = set(_toplevel_sites())
    stale = [s for s in list(_REGISTERED_SITES) + list(_EXEMPT) if s not in sites]
    assert not stale, f"実在しない窓が登録されたまま: {stale}"


def test_launcher_is_covered_even_though_it_is_the_root():
    """ランチャーは `tk.Tk`（Toplevel ではない）なので静的走査に出てこない。

    走査に出ないものは**明示的に**押さえておかないと、レジストリから消えても
    誰も気づかない（「静的ガードがあるから大丈夫」の死角）。
    """
    assert "launcher" in _WINDOWS
