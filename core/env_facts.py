"""
core/env_facts.py
==================
「環境事実の収集層」（3.2 段5）。バージョン・設定・直近ログ・DEM キャッシュ統計・
実行環境（OS／frozen／DPI／プロキシ設定の有無）を 1 つの dict にまとめて返す。

なぜ要るか
----------
出所刻印（段7）・診断パッケージ（段9）・旧配置の残骸通知は、いずれも「いまこの
環境で何が起きているか」という同じ土台を要る。**器を 3 つ作ると、同じ質問に
3 つの答えが出かねない**（欠損時の扱い・座標の伏せ方がそれぞれ独自になる）ので、
収集はここ 1 か所へ集め、使う側はこの dict を読むだけにする。

⚠️ **ここではまだ何も書き出さない**（ZIP を作る・帳票へ焼くのは呼び出し側の
仕事）。このモジュールは読み取り専用＝副作用はログの末尾を読むことだけ。

⚠️ **座標はここで伏せる**（3.2 段4 で見つかった穴）。`radiosim.log` には
シミュレーション開始・地形キャッシュ命中・DEM 全滅警告の計 3 か所で TX/RX 座標が
生のまま書かれている（`core/simulation.py`）。判断点②の「成果物は既定で除外」は
帳票・ZIP の話であって、収集した環境事実そのものに座標が混じる穴は塞がない。
⇒ ログを読む側（`recent_log_lines`）で座標を伏せ字にする＝下流のどの消費者
（刻印・診断 ZIP・残骸通知）も座標入りのログを二度と読めない。
**同じ理由で `config.load_config()` の `start`/`end`（前回実行の TX/RX 座標）も
ここで伏せる**＝ログだけ塞いで設定を素通しすると、同じ情報が別の入口から漏れる。
"""

from __future__ import annotations

import os
import re
import sys

from core import config
from core import dem
from core import version

#: ログの座標を伏せる正規表現。`simulation.py` の 3 箇所が使う
#: "start=(%s,%s) end=(%s,%s)" の書式（%s・%.6f のどちらも拾う）。
_COORD_LOG_PATTERN = re.compile(r"start=\([^)]*\)\s*end=\([^)]*\)")
_COORD_LOG_MASK = "start=(***) end=(***)"

#: `core/dem.py` の DEM 警告・エラーログが使う別書式（"lat=35.123456 lon=139.123456"）。
#: **B-180**＝上の `_COORD_LOG_PATTERN` は `simulation.py` の書式しか拾わず、
#: この書式は素通りしていた（座標伏字化の列挙漏れ）。
_LATLON_LOG_PATTERN = re.compile(r"lat=-?\d+(?:\.\d+)?\s+lon=-?\d+(?:\.\d+)?")
_LATLON_LOG_MASK = "lat=*** lon=***"

#: プロキシ URL の認証情報（`user:password@host`）を伏せる。**B-180**＝
#: `dem.set_proxy()` がホスト情報ごと URL 全体をログへ記録するため、
#: 資格情報入りの URL がそのまま直近ログへ残っていた。
_PROXY_CRED_LOG_PATTERN = re.compile(r"(://)[^/@\s]+@")
_PROXY_CRED_LOG_MASK = r"\1***@"

#: 収集する末尾行数。多すぎると「直近の挙動を見る」という収集層の趣旨から外れ、
#: ログ全体を持ち出す器になってしまう。
_LOG_TAIL_LINES = 200

#: `config.load_config()` の中で座標そのものにあたるキー（前回実行の TX/RX）。
_SENSITIVE_CONFIG_KEYS = ("start", "end")


def _redact_log_line(line: str) -> str:
    """1 行の座標・プロキシ認証情報を伏せ字にして返す（該当しない行はそのまま）。"""
    line = _COORD_LOG_PATTERN.sub(_COORD_LOG_MASK, line)
    line = _LATLON_LOG_PATTERN.sub(_LATLON_LOG_MASK, line)
    line = _PROXY_CRED_LOG_PATTERN.sub(_PROXY_CRED_LOG_MASK, line)
    return line


def recent_log_lines(*, path: "str | None" = None, limit: int = _LOG_TAIL_LINES) -> list[str]:
    """直近のログを座標を伏せた状態で返す。読めなければ空リスト。"""
    target = path if path is not None else config.LOG_FILE
    if not os.path.isfile(target):
        return []
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    tail = lines[-limit:] if limit > 0 else lines
    return [_redact_log_line(ln.rstrip("\n")) for ln in tail]


def _redact_proxy_url(url: str) -> str:
    """`scheme://user:pass@host` の資格情報部分だけ伏せる（ホストは診断に要る）。

    資格情報を持たない URL（大半のケース）はそのまま返す。
    """
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*://)([^@/]+@)(.*)$", url)
    if not m:
        return url
    return f"{m.group(1)}***@{m.group(3)}"


def sanitized_config() -> dict[str, str]:
    """設定を座標・プロキシ資格情報を伏せた状態で返す。

    `start`/`end` は前回実行の TX/RX 座標＝顧客の案件情報そのもの
    （判断点②が刻印・診断 ZIP について定めたのと同じ理由で、環境事実からも除く）。
    """
    cfg = dict(config.load_config())
    for key in _SENSITIVE_CONFIG_KEYS:
        if cfg.get(key):
            cfg[key] = "***"
    if cfg.get("proxy_url"):
        cfg["proxy_url"] = _redact_proxy_url(cfg["proxy_url"])
    return cfg


def _system_dpi() -> "int | None":
    """システム全体の DPI（取れない環境・非 Windows では None）。

    ウィンドウごとの実 DPI（`views/theme.py::window_dpi`）とは違い、**窓を
    持たずに**答えられる値だけを見る＝この層は tkinter を引かない
    （`tests/test_layers.py` が core の純度を検査する）。
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        return dpi if dpi > 0 else None
    except Exception:
        return None


def environment_info() -> dict:
    """OS／frozen／DPI／プロキシ設定の有無。座標や個人情報は含まない。"""
    return {
        "platform": sys.platform,
        "python_version": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
        "portable": config.is_portable(),
        "dpi": _system_dpi(),
        "proxy_configured": bool(config.load_config().get("proxy_url", "").strip()),
    }


def collect() -> dict:
    """環境事実を 1 つにまとめて返す。

    刻印（段7）・診断パッケージ（段9）・旧配置の残骸通知が共有する入口。
    キーは版・設定（座標伏せ済み）・直近ログ（座標伏せ済み）・DEM キャッシュ統計・
    実行環境の 5 つ。
    """
    return {
        "version": version.APP_VERSION,
        "config": sanitized_config(),
        "recent_log": recent_log_lines(),
        "cache_stats": dem.get_cache_stats(),
        "environment": environment_info(),
    }
