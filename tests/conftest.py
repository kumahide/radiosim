"""
tests/conftest.py
=================
共有フィクスチャと、外部ネットワークアクセスの遮断ゲート。
"""

import socket
import time

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import models
import simulation as sim


# ============================================================
# 外部ネットワーク遮断ゲート
# ============================================================
# ユニットテストから GSI（cyberjapandata.gsi.go.jp）へ実リクエストが飛ぶのを
# 止める。注意書きではなくゲートにしてあるのは、この混入が「失敗せずに」
# 起きるため（→ [[feedback-promote-recurring-checks]]）。
#
# 実例（B-006・2026-07-20）: バッチの成果物生成をワーカースレッドへ移した際、
# run_batch がサマリ地図の淡色地図タイル取得を含むようになり、ユニットテストが
# 実ネットワークを叩き始めた。report_map はベストエフォート設計で取得失敗を
# None に落として続行するため、オンラインでもオフラインでもテストは緑のまま
# 通る。所要時間の変化に気づかない限り検出できない。
#
# 遮断は socket 層で行う。requests / urllib / tkintermapview のどの経路から
# 来ても最終的にここへ落ちるため、呼び出し側を個別に塞ぐ必要がない。
# localhost は通す（将来のローカルサーバ系テストを巻き込まないため）。
# 正当な理由で外部通信するテストは @pytest.mark.network を付けて明示する。

_real_socket_connect     = socket.socket.connect
_real_create_connection  = socket.create_connection


def _is_local(address) -> bool:
    """接続先が localhost / UNIX ソケットなら True。"""
    if not isinstance(address, tuple) or not address:
        return True   # AF_UNIX 等はアドレスがタプルでない＝外部通信ではない
    host = address[0]
    return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "")


class NetworkAccessBlocked(RuntimeError):
    """テスト中に外部ネットワークアクセスが試みられた。"""


def _blocked(address):
    raise NetworkAccessBlocked(
        f"テストから外部ネットワークへの接続が試みられました: {address}\n"
        "ユニットテストは外部 API（GSI 等）を叩いてはいけません。"
        "取得層を monkeypatch するか、意図的な通信なら "
        "@pytest.mark.network を付けてください。"
    )


@pytest.fixture(autouse=True)
def _block_network(request):
    """全テストで外部ネットワークを遮断する（@pytest.mark.network で解除）。"""
    if request.node.get_closest_marker("network"):
        yield
        return

    def _guarded_connect(self, address):
        if not _is_local(address):
            _blocked(address)
        return _real_socket_connect(self, address)

    def _guarded_create_connection(address, *args, **kwargs):
        if not _is_local(address):
            _blocked(address)
        return _real_create_connection(address, *args, **kwargs)

    socket.socket.connect = _guarded_connect
    socket.create_connection = _guarded_create_connection
    try:
        yield
    finally:
        socket.socket.connect = _real_socket_connect
        socket.create_connection = _real_create_connection


# ============================================================
# 実行環境の一致ゲート（宣言した venv でしかテストを回させない）
# ============================================================
# 背景（2026-08-03・独立レビュー Codex 由来）: `RADIOSIM_PYTHON` は「**検証にも
# ビルドにも使う唯一の環境**」の宣言で、`build.bat` は未設定なら止まる。ところが
# **pytest 側には何の強制も無く**、README のテスト手順も裸の `python -m pytest` の
# ままだった。⇒ **別の環境で検証して、別の環境で exe を焼ける**。
#
# 🔴 実際に起きた（2026-08-03・このゲートを入れた当日）: Claude が
# `2.6` のリリース準備で全スイートをシステムの Python 3.14 で回し、依存版のずれで
# 落ちた 4 件を「既知の課題」と誤読して「全件緑（既知分を除外）」と報告した。
# 宣言環境では 10 件とも一致して**除外ゼロで緑**だった。**赤の意味を取り違えた**
# のではなく、**そもそも違う環境の赤を見ていた**。
#
# 判定は「宣言があるときだけ」＝`RADIOSIM_PYTHON` 未設定の環境（CI・他マシンの
# clone）では何もしない。宣言と食い違うときだけ、理由と直し方を出して止める。
def _same_interpreter(a: str, b: str) -> bool:
    """パスの表記ゆれ（大小・区切り・相対）を吸収して同一性を見る。"""
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def pytest_configure(config):
    declared = os.environ.get("RADIOSIM_PYTHON", "").strip().strip('"')
    if not declared:
        return                                  # 宣言が無い環境＝CI 等。何もしない
    if not os.path.exists(declared):
        # 🔴 **ここで return してはいけない**（2026-08-04・独立レビュー Codex）。
        # venv を移動・削除した／設定を打ち間違えた状態で、**ゲートが黙って
        # 無効になる**＝このゲートが防ごうとしている「別環境での偽の成功」が
        # そのまま再発する（実測＝`Z:\missing\python.exe` を宣言しても、
        # 別の Python で収集が成功した）。**宣言が壊れているなら止める。**
        raise pytest.UsageError(
            "RADIOSIM_PYTHON が指す Python が見つかりません。\n"
            f"  RADIOSIM_PYTHON : {declared}\n"
            "venv を作り直したか、パスを打ち間違えています。"
            "正しい python.exe を指すよう設定し直してください"
            "（未設定にすればこの検査は行われません）。"
        )
    if _same_interpreter(declared, sys.executable):
        return
    raise pytest.UsageError(
        "宣言された環境と違う Python でテストを回しています。\n"
        f"  RADIOSIM_PYTHON : {declared}\n"
        f"  いま実行中       : {sys.executable}\n"
        "この環境の依存版は requirements.txt のピンと一致しない可能性があり、"
        "**検証した版と配布する exe の版がずれます**（build.bat は上の環境で焼きます）。\n"
        "次のように回してください:\n"
        '  & "$env:RADIOSIM_PYTHON" -m pytest\n'
        "意図して別環境で回すなら、そのシェルで RADIOSIM_PYTHON を空にしてください。"
    )


# ============================================================
# モーダルダイアログ・OS 委譲の遮断ゲート
# ============================================================
# GUI テストからモーダルダイアログが出ると、`wait_window()` が**人がボタンを
# 押すまで返らない**＝テストが止まる。CI では気づけず（表示できないので別の形で
# 失敗する）、ローカルでは実行者が手で押す羽目になる。実際 2026-07-26 に条件探索の
# 完了ダイアログ（「保存しました。開きますか？」）で止まり、ユーザーが手動で
# 応答した。しかも「はい」を押すと `os.startfile` でブラウザが開く＝テストが
# 環境の外へ出る。
#
# ネットワーク遮断と**同じ理由・同じ形**で塞ぐ（→ [[feedback-promote-recurring-checks]]）：
# テストごとに monkeypatch を書くのは「思い出す規則」で、新しい GUI テストを
# 書いた人が忘れた瞬間に再発する。既定で塞ぎ、必要なら明示的に外す。
#
# 塞ぐのは「人の操作を待つ」「OS へ制御を渡す」の 2 種類:
#   - views.dialogs の alert / confirm / choose（自前モーダル＝wait_window）
#   - os.startfile（ブラウザ・エクスプローラ起動）
#   - tkinter.filedialog の各種（ファイル選択ダイアログ）
# 応答は**最も安全な既定**＝confirm は False（「いいえ」）、choose は None
# （キャンセル）、ファイル選択は "" （キャンセル）を返す。
#
# 呼ばれた内容を検証したいテストは `dialog_calls` フィクスチャを受け取る。


class _DialogCalls(list):
    """記録された呼び出し（`("confirm", title, message)` 等）。"""

    def kinds(self) -> list:
        return [c[0] for c in self]


@pytest.fixture(autouse=True)
def dialog_calls(request, monkeypatch):
    """モーダルダイアログと OS 委譲を塞ぎ、呼び出しを記録する。

    テストがダイアログを**出そうとしたこと自体**を検証できるよう、戻り値を
    そのままフィクスチャとして提供する。実物を出したいテストは
    `@pytest.mark.real_dialogs` を付ける（人が操作する前提の手動確認用）。
    """
    calls = _DialogCalls()
    if request.node.get_closest_marker("real_dialogs"):
        yield calls
        return

    from tkinter import filedialog

    from views import dialogs

    def _alert(parent, title, message):
        calls.append(("alert", title, message))

    def _confirm(parent, title, message):
        calls.append(("confirm", title, message))
        return False                     # 既定は「いいえ」＝副作用の少ない側

    def _choose(parent, title, message, options, cancel_label=None):
        calls.append(("choose", title, message))
        return None                      # 既定はキャンセル

    monkeypatch.setattr(dialogs, "alert", _alert)
    monkeypatch.setattr(dialogs, "confirm", _confirm)
    monkeypatch.setattr(dialogs, "choose", _choose)
    monkeypatch.setattr(os, "startfile",
                        lambda path, *a, **k: calls.append(("startfile", str(path))),
                        raising=False)
    for name in ("askopenfilename", "asksaveasfilename", "askdirectory"):
        monkeypatch.setattr(filedialog, name,
                            lambda *a, _n=name, **k: (calls.append((_n,)), "")[1])
    # matplotlib の `plt.show()` も入れ子 mainloop でブロックする（グラフ窓）。
    # ⚠️ ここで pyplot を import しない（重い＝matplotlib を使わないテストにまで
    # 起動コストを乗せる）。既に読み込まれている時だけ塞ぐ＝グラフ窓へ到達し得る
    # テストは、そのモジュールを import した時点で pyplot も読み込んでいる。
    plt = sys.modules.get("matplotlib.pyplot")
    if plt is not None:
        monkeypatch.setattr(plt, "show",
                            lambda *a, **k: calls.append(("plt.show",)))
    yield calls


# ============================================================
# プロセス横断状態のリセット
# ============================================================
@pytest.fixture(autouse=True)
def _clear_terrain_cache():
    """テスト間で地形キャッシュを空にする。

    `simulation._terrain_cache` はプロセス全体で共有され、キーは座標＋サンプル数
    のみ（周波数等は地形に影響しないので含まない）。テストの多くが同一座標を
    使うため、消さないと前のテストの結果が次のテストへ漏れる。実際、バッチを
    キャッシュ付き取得へ切り替えた際、取得失敗を検証するテストが「前のテストの
    キャッシュにヒットして取得自体が走らない」ため緑になってしまった。
    """
    sim.clear_terrain_cache()
    yield
    sim.clear_terrain_cache()


@pytest.fixture
def default_params_dict():
    """SimParams / validate_config に渡す標準パラメータ辞書。"""
    return {
        "start"      : "34.5429, 132.4118",
        "end"        : "34.5389, 132.4050",
        "h_tx"       : "30.0",
        "h_rx"       : "10.0",
        "freq"       : "2400.0",
        "p_tx"       : "20.0",
        "gain_tx"    : "3.0",
        "gain_rx"    : "3.0",
        "sens"       : "-85.0",
        "veg_h"      : "10.0",
        "k_factor"   : "10.0",
        "samples"    : "50",
        "diff_method": "deygout",
        "env_type"   : "los",
        "rain_rate"  : "0.0",
    }


@pytest.fixture
def flat_terrain():
    """平坦地形（標高 0m 均一、100 サンプル）。"""
    raw = np.zeros(100)
    return models.calculate_terrain_profile(raw, 34.5429, 132.4118, 34.5389, 132.4050)


# ============================================================
# Tk ルート生成（間欠的な初期化失敗のリトライ）
# ============================================================
# この環境では Tk の初期化が間欠的に失敗する。**毎回違う .tcl が「読めない」と
# 言われ、errno が "No error" や実在ファイルへの "no such file" になる**のが特徴で、
# ファイル自体は存在しディスクにも余裕がある（実測）。リアルタイムスキャン等に
# よる一過性の read 失敗と考えられ、アプリ側では根治できない。
#
# 放置すると表示依存テストが **黙って skip** される＝「緑に見えて実は GUI 配線を
# 1つも検証していない」状態になり、本プロジェクトが最も警戒する失効ゲートそのもの
# （[[feedback-promote-recurring-checks]]）。一過性なら再試行で通るので、skip へ
# 倒す前に数回やり直す。それでも駄目なら従来どおり skip。

_TK_INIT_ATTEMPTS = 5


def make_tk_root(pytest_module=None):
    """tkinter のルートを生成する。間欠的な初期化失敗は数回リトライする。

    全て失敗したときだけ skip する（ヘッドレス CI もここに落ちる）。
    """
    import tkinter as tk

    last = None
    for _ in range(_TK_INIT_ATTEMPTS):
        try:
            return tk.Tk()
        except tk.TclError as e:
            last = e
            time.sleep(0.05)
    pytest.skip(f"no display available ({_TK_INIT_ATTEMPTS} 回試行): {last}")


def make_themed_root(theme_name: str = "dark"):
    """テーマとアプリのフォント設定まで済ませた Tk ルートを返す。

    **窓の寸法を検証するテストは必ずこちらを使う**。素の Tk 既定フォントは実機
    （sv_ttk の本文フォント）より小さく、そのまま測ると**実物より狭い前提**で
    ゲートが緑になる。実際 2.5b2 で、条件探索の必要幅 947px を「テーマ無しなら
    900px 未満」と測ってしまい、右端の条件列が見切れる実装を通した。
    """
    from views import theme as views_theme

    root = make_tk_root()
    set_theme(theme_name)
    views_theme.apply_fonts(root)
    return root


def set_theme(name: str) -> None:
    """sv_ttk のテーマを適用する。`sv.tcl` の間欠的な読み込み失敗を再試行する。

    上の `make_tk_root` と同じ一過性の read 失敗（"couldn't read file ...: No error"）が
    テーマ tcl の source でも起きる。ここで諦めると配色テストが落ちるので、
    Tk 初期化と同じくリトライで吸収する。
    """
    import tkinter as tk

    import sv_ttk

    last = None
    for _ in range(_TK_INIT_ATTEMPTS):
        try:
            sv_ttk.set_theme(name)
            return
        except tk.TclError as e:
            last = e
            time.sleep(0.05)
    pytest.skip(f"sv_ttk テーマを読み込めない（{_TK_INIT_ATTEMPTS} 回試行）: {last}")
