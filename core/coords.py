"""
coords.py
=========
座標表記（DD: 十進度 ⇔ DMS: 度分秒）の双方向変換。純関数・副作用ゼロ。
GUI・ネットワーク・ファイル I/O を一切持たない（models.py と同じ制約）。

ランチャーの数値欄は常に source of truth だが、DD/DMS のどちらの notation でも
表示・入力できる。表記の違いを吸収するのが本モジュールの役割で、計算側
（simulation.SimParams / config.validate_config）へ渡す前に必ず DD へ
正規化する。

入出力形式:
  DD  : "34.542900, 132.411800"
  DMS : "34°32'34.4\"N, 132°24'42.5\"E"
        パースは記号（° ' "）・N/S/E/W・空白区切りを寛容に受理する。
"""

import re

#: 座標を表示する入力欄の**幅**（B-046 / B-057）。単位は Tk の `width`＝**平均文字幅**。
#:
#: DD の `34.54000, 132.41000` は 19 文字だが、DMS の
#: `34°48'00.0"N, 132°36'00.0"E` は **27 文字**。21 文字で作られていた欄では
#: **末尾の `E` / `W` が画面から消えていた**（値は正しく、欄がスクロールするだけ
#: なので、寸法ゲートにも値のゲートにも映らなかった）。
#:
#: 🔴 **27 ではなく 26。文字数ではなく「実際に描いた幅」で決める**（2026-08-12・B-057）。
#: B-046 はここを **DMS の文字数 27** から決めたが、**`width` の単位は `0` の字の幅**で、
#: DMS を作る `°` `'` `"` `.` は `0` よりずっと細い。必要量は文字数ではなく
#: `measure(DMS) / measure("0")` で出る＝**24.17 単位**（出荷し得る書体・サイズを
#: 総なめした最悪値）。⇒ 27 は過大で、**26 は必要量 +1 単位の余裕**。
#: ⚠️ **25 まで詰めれば計算上は足りるが、そこは取らない**＝B-046 は見切れ 6 回目の
#: 対策で、ここを丸めて詰めると未検証の書体（環境依存の日本語フォント）で再発する。
#: ⚠️ **最悪は南緯・西経**（`S` / `W`）＝**`W` がこの文字列で一番太い字**。北緯・東経
#: だけで測ると 24 でも足りると出て、実際に一度それで詰めて切った。
#: 🔑 **「文字数 ≠ 描画幅」を取り違えると両方向に転ぶ**＝ここでは*広すぎ*に、
#: 複数経路の ID 欄では*狭すぎ*に出た（同じ B-057 の 2 つの顔）。⇒ **検査は
#: 文字数を数えず、書体から必要量を計算して比べる**
#: （tests/test_ui_consistency.py の `_required_width_units`）。
#:
#: ⛔ **表記によって幅を変えない**＝切り替えるたびに列がずれると、⑧（一貫性は
#: 人の速度）に反する。**広いほう（DMS）に合わせて固定する。**
#: ⚠️ **窓ごとに数字を書かない**＝同じ 21 文字が 3 か所に散っていたのが B-046 の
#: 正体（列挙で塞ぐ穴は名前 1 つで開く）。座標の書式を持つここが唯一の出所。
DISPLAY_WIDTH_CHARS = 26

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
    # ⚠️ 桁上げしたら秒は **0.0 に置く**（引き算で作らない）。`seconds -= 60.0` だと
    # 浮動小数の残差が **負の微小値**になり、`{:04.1f}` が `-0.0` を出す＝
    # `132°36'-0.0"E` という**読み戻せない文字列**になる（`parse_pair` が ValueError →
    # 呼び出し側で NaN 化）。34.8 や 132.6 のような「丸い」座標——手入力で最も多い形——の
    # 4 割で起きていた（B-034）。
    if round(seconds, 1) >= 60.0:
        seconds = 0.0
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
