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
