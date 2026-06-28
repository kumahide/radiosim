"""
coords.py
=========
座標表記（DD: 十進度 ⇔ DMS: 度分秒）の双方向変換。純関数・副作用ゼロ。
GUI・ネットワーク・ファイル I/O を一切持たない（models.py と同じ制約）。

ランチャーの数値欄は常に source of truth だが、DD/DMS のどちらの notation でも
表示・入力できる。表記の違いを吸収するのが本モジュールの役割で、計算側
（simulation.SimParams / infrastructure.validate_config）へ渡す前に必ず DD へ
正規化する。

入出力形式:
  DD  : "34.542900, 132.411800"
  DMS : "34°32'34.4\"N, 132°24'42.5\"E"
        パースは記号（° ' "）・N/S/E/W・空白区切りを寛容に受理する。
"""

import re

# DMS トークン抽出: 度〔分〔秒〕〕＋任意の半球記号。記号は ° ' " のほか空白でも可。
_HEMI = {"N": 1.0, "S": -1.0, "E": 1.0, "W": -1.0}
_DMS_RE = re.compile(
    r"""^\s*
    (?P<deg>\d+(?:\.\d+)?)\s*[°\s]\s*
    (?:(?P<min>\d+(?:\.\d+)?)\s*['′\s]\s*)?
    (?:(?P<sec>\d+(?:\.\d+)?)\s*["″\s]?\s*)?
    (?P<hemi>[NSEWnsew])?
    \s*$""",
    re.VERBOSE,
)


def _parse_one(token: str) -> float:
    """1 つの座標成分（DD 数値文字列 or DMS 文字列）を float へ変換する。"""
    token = token.strip()
    # まず素の十進度として試す（"34.5429" や "-132.41"）。
    try:
        return float(token)
    except ValueError:
        pass
    m = _DMS_RE.match(token)
    if not m:
        raise ValueError(f"invalid coordinate component: {token!r}")
    deg = float(m.group("deg"))
    minutes = float(m.group("min") or 0.0)
    seconds = float(m.group("sec") or 0.0)
    value = deg + minutes / 60.0 + seconds / 3600.0
    hemi = m.group("hemi")
    if hemi:
        value *= _HEMI[hemi.upper()]
    return value


def parse_pair(text: str) -> tuple[float, float]:
    """DD / DMS いずれかの "lat, lon" 文字列を (lat, lon) の float タプルに変換する。

    変換できない場合は ValueError を送出する。
    """
    parts = text.split(",")
    if len(parts) != 2:
        raise ValueError(f"expected 'lat, lon', got {text!r}")
    return _parse_one(parts[0]), _parse_one(parts[1])


def format_dd(lat: float, lon: float) -> str:
    """十進度の "lat, lon" 文字列を返す（既存の .6f 精度を踏襲）。"""
    return f"{lat:.6f}, {lon:.6f}"


def _format_dms_one(value: float, positive: str, negative: str) -> str:
    """1 成分を度分秒（秒は小数1桁）＋半球記号へ整形する。"""
    hemi = positive if value >= 0 else negative
    value = abs(value)
    deg = int(value)
    rem_min = (value - deg) * 60.0
    minutes = int(rem_min)
    seconds = (rem_min - minutes) * 60.0
    # 秒の四捨五入が 60.0 に達したら桁上げする（59.95 → 60.0"）。
    if round(seconds, 1) >= 60.0:
        seconds -= 60.0
        minutes += 1
    if minutes >= 60:
        minutes -= 60
        deg += 1
    return f"{deg}°{minutes:02d}'{seconds:04.1f}\"{hemi}"


def format_dms(lat: float, lon: float) -> str:
    """度分秒の "lat, lon" 文字列を返す（例: 34°32'34.4\"N, 132°24'42.5\"E）。"""
    return (
        f"{_format_dms_one(lat, 'N', 'S')}, "
        f"{_format_dms_one(lon, 'E', 'W')}"
    )


def format_pair(lat: float, lon: float, fmt: str) -> str:
    """座標を fmt（"dd" | "dms"）に応じた "lat, lon" 文字列へ整形する。

    DD/DMS のどちらで出すかを呼び出し側の設定値ひとつで切り替える共通入口。
    ランチャーの地図ピック書き戻し・レポート出力で共有する。
    """
    return format_dms(lat, lon) if fmt == "dms" else format_dd(lat, lon)


def reformat(text: str, target: str) -> str:
    """text を target 表記（"dd" | "dms"）へ整形する。

    パースできない場合は原文をそのまま返す（toggle / load 時に欄を壊さない）。
    """
    try:
        lat, lon = parse_pair(text)
    except ValueError:
        return text
    return format_dms(lat, lon) if target == "dms" else format_dd(lat, lon)


def to_dd_str(text: str) -> str:
    """text を DD 表記の "lat, lon" 文字列へ正規化する。

    パースできない場合は原文を返す（不正値の判定は downstream の validate に委ねる）。
    """
    return reformat(text, "dd")
