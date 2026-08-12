"""tools/gui_walkthrough_slice_d.py — 2.7 スライス D の**筋書き付きスクショ**。

`tools/gui_shots.py` は窓を開いて撮るだけ（静止画）。こちらは**操作してから撮る**＝
静止画では確かめられない 2 つを見るためのもの:

  1. **I-060 R3**：座標を確定すると現在の表記へ整形される（ランチャー／複数経路）
  2. **I-052**：中継の集約は判定で語が変わる（OK＝全体マージン／NG＝最大不足）

⛔ **実機確認ではない**（→ `tools/gui_shots.py` の冒頭・見切れの門は
`tests/test_window_fit.py`）。ここで見るのは**語と振る舞い**まで。

⚠️ **利用者の設定ファイルへ書かない**＝座標表記はメモリ上の写しだけ差し替える
（`_on_coord_format_change` は `config.save_app` を呼ぶので**通さない**）。
⚠️ **DEM は引かない**＝地形はフェイク（テストと同じ形）。ネットワークに出ずに
実行まで通す。成果物は一時ディレクトリへ捨てる。

    python tools/gui_walkthrough_slice_d.py [出力ディレクトリ]
"""

import datetime as _dt
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

import numpy as np

from core import config
from core import i18n
from core import simulation as sim
from report import multihop as mh
from report import report_summary
from gui_shots import capture

STEPS: list[tuple[str, str]] = []      # (ファイル名, 何を見るか)


def _shot(win, outdir, name, what):
    win.update()
    time.sleep(0.4)
    win.update()
    path = os.path.join(outdir, f"{name}.png")
    size = capture(win, path)
    STEPS.append((f"{name}.png", what))
    print(f"{name:28s} {size}  {what}")


#: フェイク地形に尾根を立てるか（NG の筋書きだけ立てる）。
_RIDGE = True


def _fake_fetch(params, on_progress, on_complete, on_error):
    """DEM 取得のフェイク。⚠️ 全点 0.0 にしない＝「1 点も取れなかった」形として
    扱われるため（B-025 ④）。

    ⚠️ **尾根の有無で OK / NG が決まるのは距離ではない**＝最初は「短い経路なら OK」
    のつもりで書いたが、**近いほど尾根の見込み角が大きく回折損失が跳ね上がる**ので
    短い経路のほうが −144 dB になった（実測）。⇒ 判定を作りたいなら地形で作る。
    """
    raw = np.full(params.num, 20.0)
    if _RIDGE:
        raw[params.num // 2 - 2:params.num // 2 + 2] = 60.0
    on_progress(params.num)
    on_complete(raw)


def _run_multihop(path, params, results_dir):
    """`run_multihop` を同期的に回す（DEM はフェイク・成果物は捨てる）。"""
    sim.fetch_elevations = _fake_fetch          # プロセスごと使い捨てなので直に差す
    sim._terrain_cache = {}
    config.RESULTS_DIR = results_dir
    report_summary.render_summary_map_b64 = lambda r: None

    out, err = [], []
    done = threading.Event()
    mh.run_multihop(
        path, params,
        on_hop_start    = lambda i, n, pid: None,
        on_hop_progress = lambda v: None,
        on_hop_complete = lambda i, n, pr: None,
        on_complete     = lambda run: (out.append(run), done.set()),
        on_error        = lambda ex: (err.append(ex), done.set()),
    )
    assert done.wait(timeout=120), "run_multihop が完了しない"
    if err:
        raise err[0]
    return out[0]


def _waypoints(case: str):
    """OK＝見通しの短い経路／NG＝尾根に阻まれる長い経路。"""
    if case == "ok":
        pts = [(34.5400, 132.4100), (34.5450, 132.4150), (34.5500, 132.4200)]
    else:
        pts = [(34.5400, 132.4100), (34.7000, 132.6000), (34.9000, 132.9000)]
    names = ["TX", "R1", "RX"]
    return [mh.Waypoint(name=n, lat=la, lon=lo, h=30.0)
            for n, (la, lo) in zip(names, pts)]


def main(outdir: str) -> int:
    os.makedirs(outdir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="radiosim_walkthrough_")
    from conftest import make_themed_root
    from views.launcher import SimLauncher
    from views import dialogs

    i18n.set_lang("ja")
    root = make_themed_root()
    app  = SimLauncher(root, lambda _t: None)
    try:
        # --- 1. 座標の表記を DMS へ（**設定ファイルには書かない**） -------------
        app._coord_fmt_var.set("dms")
        app.config["coord_format"] = "dms"

        entry = app.entries["start"]
        entry.delete(0, "end")
        entry.insert(0, "34.8, 132.6")          # DD 表記で貼り付ける
        _shot(root, outdir, "05_launcher_dd_pasted",
              "DMS 表記の設定で DD を貼った直後（まだ確定していない）")

        root.update()
        entry.focus_force()
        root.update()
        entry.event_generate("<Return>")
        _shot(root, outdir, "06_launcher_reformatted",
              f"確定（Enter）後＝{entry.get()!r} へ整形された〔I-060 R3〕")

        entry.delete(0, "end")
        entry.insert(0, "きた 34 ひがし 132")     # 読めない入力
        root.update()
        entry.focus_force()
        root.update()
        entry.event_generate("<Return>")
        _shot(root, outdir, "07_launcher_unreadable_kept",
              f"読めない入力は原文が残る＝{entry.get()!r}〔I-060 R3 の裏面〕")

        entry.delete(0, "end")
        entry.insert(0, "34.5400, 132.4100")
        root.update()
        entry.focus_force()
        root.update()
        entry.event_generate("<Return>")

        # --- 2. 複数経路の表でも同じ整形が起きる（クラス点検） -----------------
        batch = app.ensure_batch_window()
        cell  = batch._row_entries[0][1]        # 先頭行の送信座標セル
        cell.delete(0, "end")
        cell.insert(0, "34.8, 132.6")
        _shot(batch, outdir, "08_paths_dd_pasted",
              "複数経路の表に DD を貼った直後")
        batch.update()
        cell.focus_force()
        batch.update()
        cell.event_generate("<Return>")
        _shot(batch, outdir, "09_paths_reformatted",
              f"確定後＝{cell.get()!r}〔I-060 のクラス点検＝窓をまたいで同じ返事〕")

        # --- 3. 中継の集約は判定で語が変わる ----------------------------------
        app._on_open_multihop()
        win = app._multihop_win
        dialogs.choose = lambda *a, **k: None    # 完了ダイアログは出さない
        params = sim.SimParams(app.config)

        for case, name, what in (
            ("ok", "10_multihop_summary_ok",
             "全体判定 OK＝「全体マージン（最小余裕）」で符号つき〔I-052〕"),
            ("ng", "11_multihop_summary_ng",
             "全体判定 NG＝「最大不足」で正の不足量〔I-052〕"),
        ):
            globals()["_RIDGE"] = (case == "ng")
            wps  = _waypoints(case)
            path = mh.MultiHopPath(path_id="route1", waypoints=wps,
                                   hop_rf=[mh.HopRF(), mh.HopRF()])
            run = _run_multihop(path, params, tmp)
            # **画面の入力も同じ経路に揃える**＝結果だけ差し込むと、地点表が空の
            # まま「#2 R1 → RX」が出て、見る人には辻褄が合わない絵になる。
            while len(win._wp_vars) < len(wps):
                win._add_waypoint()
            for vars_, wp in zip(win._wp_vars, wps):
                vars_["name"].set(wp.name)
                vars_["coord"].set(f"{wp.lat:.5f}, {wp.lon:.5f}")
                vars_["height"].set(f"{wp.h:.1f}")
            win.update()
            # 区間表にも結果を返す（スライス B の約束＝結果はその結果を生んだ行へ）。
            # ここを抜くと「完了しているのに区間表が空」という、B で直したはずの
            # 絵が証跡に残ってしまう。
            for i, pr in enumerate(run.hops, start=1):
                win._show_hop_result(i, pr)
            win._on_complete(run)
            key, val = mh.overall_display(run, digits=2)
            _shot(win, outdir, name, f"{what}／実測 {i18n.t(key)}: {val} dB")

        # --- 索引を残す（あとで何のスクショか分からなくならないように） --------
        with open(os.path.join(outdir, "README.md"), "w", encoding="utf-8") as f:
            f.write("# 2.7 スライス D の筋書き付きスクショ"
                    f"（{_dt.date.today():%Y-%m-%d}）\n\n")
            f.write("⛔ **実機確認ではない**＝開発機（WQHD）の見た目。"
                    "実機（AVD・使える高さ 990px）の見切れは "
                    "`tests/test_window_fit.py` が門。\n\n")
            f.write("| ファイル | 何を見るか |\n|---|---|\n")
            for fn, what in STEPS:
                f.write(f"| `{fn}` | {what} |\n")
    finally:
        root.destroy()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    default = os.path.join(
        os.path.dirname(__file__), "..", "issue_evidence",
        f"gui_shots_{_dt.date.today():%Y-%m-%d}")
    sys.exit(main(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else default)))
