"""
project.py
==========
プロジェクトファイル（`.rsproj`）の読み書き＝**入力一式を 1 つに束ねる**層
（ヘッドレス・UI 知識ゼロ・tkinter を import しない）。

**プロジェクト＝入力の集合**（結果は `results/` 側の仕事）
------------------------------------------------------
束ねるのは 5 つ＝案件情報／ランチャーの sim パラメータ／バッチ行／条件探索の
条件セット／中継経路の waypoint 列。

  ⛔ **app 設定（theme / lang / proxy_url）は入れない。**
     他人のプロジェクトを開いた瞬間に言語やプロキシが変わるのは事故で、
     `config.select_sim` / `select_app` が守っている分離をここで壊すことになる。
     読む側でも `select_sim` を通す＝**ファイルに混ざっていても取り込まない**。
  ⛔ **結果・窓の位置/サイズ/開閉状態も入れない**（保存するのは入力一式であって
     画面の見た目ではない＝「テンプレエディタ化しない」ガード）。

なぜバッチ行を CSV 列名でなく `PathRow` の属性名で持つのか（⑦）
--------------------------------------------------------------
CSV（`batch.CSV_COLUMNS`）は**人が Excel で編集する外部契約**で、座標を
`"34.5, 132.4"` の 1 セルに詰める。JSON でその形を真似ると**座標文字列の
パースが 2 か所に増える**＝範囲・パースの出所が二重になる（B-016/B-018 と
同型）。`.rsproj` は**アプリ内部の型の写し**にして、CSV 往復は従来どおり
`batch.parse_csv` / `export_csv` が別に担う。

節が無い（`None`）ことの意味
----------------------------
**「その窓の情報を持たない」**であって「空の窓」ではない。⇒ 読込側は触らず、
保存側は**前回値を持ち越す**。ここを「閉じている窓＝空」と解釈すると、
バッチ窓を閉じただけで**行が消えたファイルを上書き保存**する（データ喪失）。

条件探索と中継経路の重み（⚠️ 軽く扱わない）
--------------------------------------------
この 2 つは**窓の中身以外に器が無い**＝`.rsproj` が事実上**唯一の永続化手段**。
したがってスキーマの後方互換の約束が重い（`schema_version` の規則を下記の
とおり厳密に守る）。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime

import batch
import config
import i18n
import multihop as mh
import version

# ============================================================
# スキーマ
# ------------------------------------------------------------
# **ドキュメント全体で整数 1 つ**（節ごとの版は持たない）。節別の版は 3.2
# 「出力契約」で規格が決まってから＝先食いしない（⑤）。
#
# 読込の規則（`config.load_config` と同じ流儀）:
#   - 自分より**新しい**版 → 拒否する（新しいファイルを古いアプリで黙って壊さない）
#   - 未知キー → 無視
#   - 欠損キー → 既定値 / None
# ============================================================
SCHEMA_VERSION = 1
FILE_EXT = ".rsproj"

# 条件探索の相（**閉じた集合**）。`ScenarioSpec.mode` の許可値の単一ソース。
SCENARIO_MODES = ("compare", "sweep")


class ProjectError(Exception):
    """プロジェクトファイルを読めない（形式・版・破損）。メッセージは i18n 済み。"""


# ============================================================
# データ構造
# ============================================================
@dataclass
class ScenarioSpec:
    """条件探索の条件セット（**画面の文字列のまま**持つ）。

    値を保存時に数値へ変換しない理由＝条件探索の窓が「値は文字列で持ち、実行時に
    変換する」流儀（`views/scenario.py` の `_COMPARE_FIELDS`）だから。保存の瞬間に
    パースを走らせると、**入力途中の値を保存できない**（保存はいつでも通るべき）。
    値域の検証は実行時に `scenario.Condition` が 1 か所で担う。
    """
    mode:    str                  = "compare"      # "compare" | "sweep"
    compare: list[dict[str, str]] = field(default_factory=list)
    sweep:   dict[str, str]       = field(default_factory=dict)  # axis/from/to/points


@dataclass
class ProjectDoc:
    """プロジェクト 1 件＝入力一式。**節ごとに `None` を許す**（上記の意味）。"""
    meta:       dict[str, str]              = field(default_factory=dict)
    params:     dict[str, str]              = field(default_factory=dict)
    batch_rows: "list[batch.PathRow] | None" = None
    scenario:   "ScenarioSpec | None"        = None
    multihop:   "mh.MultiHopPath | None"     = None
    # 刻印（**読込の判定には使わない**＝どの版が書いたかの記録だけ）。
    app_version: str = ""
    saved_at:    str = ""


# ============================================================
# 値の変換（読む側は「壊れたファイル」を例外 1 種に畳む）
# ============================================================
def _num(value, what: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ProjectError(i18n.t("proj_err_broken").format(
            reason=f"{what}={value!r}")) from None


def _opt_num(value, what: str) -> "float | None":
    """空欄・None は None（＝共通設定を踏襲）のまま通す（`PathRow` と同じ約束）。"""
    if value is None or value == "":
        return None
    return _num(value, what)


# ---------------------------------------------------------------------------
# 既知キーの取り出し ＝ **「キーが無い」と「キーはあるが型が違う」を分ける**
# ---------------------------------------------------------------------------
# 🔴 **`data.get(key)` では分けられない**（2026-08-03・独立レビュー Codex 3 巡目）。
# `null` が入っていても欠損と同じ `None` が返るので、**壊れたファイルが「その節は
# 無い」として読めてしまう**。読めてしまうのが危険なのは、**そのまま上書き保存
# すると壊れていた節の中身が消える**から——直したはずのデータ損失と同じクラス。
#
# 🔑 **判断の決め手＝`to_dict` は `null` も裸の配列も書かない**（節が None なら
# キーごと出さない・内側は必ず list / dict）。⇒ **これらの位置に `null` がある
# ファイルは、我々が書いたものではない**＝壊れているか別のツールが作ったもの。
# 「欠損は既定値」の緩さは**キーが無いときにだけ**与えればよい。
#
# ⚠️ 2 巡目まで「`null` は値が無いの自然な表現」として通していた。**説明としては
# 正しいが、書き手を我々に限れば起こり得ない入力**だった＝*一般的な JSON の作法*
# ではなく*この製品が実際に書く形*で線を引く。
_MISSING = object()


def _str_map(value) -> dict[str, str]:
    """dict[str, str] へ正規化（数値が入っていても文字列にする）。"""
    if not isinstance(value, dict):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in value.items()}


def _typed(container: dict, key: str, kind: type, what: str = ""):
    """既知キーを型つきで取り出す。**無ければ None・型が違えば `ProjectError`。**"""
    value = container.get(key, _MISSING)
    if value is _MISSING:
        return None                                   # キーが無い＝既定値へ
    if not isinstance(value, kind):
        raise ProjectError(i18n.t("proj_err_broken").format(reason=what or key))
    return value


def _seq(container: dict, key: str, what: str = "") -> list:
    """既知キーのリストを取り出す（無ければ空・リスト以外は `ProjectError`）。"""
    return _typed(container, key, list, what) or []


def _map(container: dict, key: str, what: str = "") -> dict:
    """既知キーの dict を取り出す（無ければ空・dict 以外は `ProjectError`）。"""
    return _typed(container, key, dict, what) or {}


def _section(data: dict, key: str) -> "dict | None":
    """既知の節（`batch` / `scenario` / `multihop`）を取り出す。

    **キーが無い＝None（節が無い）／dict 以外（`null` を含む）＝`ProjectError`。**
    """
    return _typed(data, key, dict)


def _text(container: dict, key: str, default: str = "", what: str = "") -> str:
    """既知の**文字列項目**を取り出す（`mode` / `path_id` / `note` / `topology` …）。

    **キーが無い＝既定値／文字列と数値＝受ける／それ以外（`null`・配列・dict）
    ＝`ProjectError`。**

    🔴 なぜ `str(...)` ではいけないか（2026-08-04・独立レビュー Codex 4 巡目）＝
    `str()` は**何でも通す**ので、`"mode": []` が `"compare"` に、`"path_id": null`
    が**文字列 `"None"`** に黙って化ける。**壊れた値を変換して受け入れ、そのまま
    再保存できる**＝構造項目で塞いだのと同じ穴がスカラー側に残っていた。

    ⚠️ **数値は受ける**＝JSON の数値は曖昧さなく文字列にでき、情報も失われない
    （手書きの `"path_id": 1` を弾く理由がない）。`bool` は `int` の派生だが
    `True` → `"True"` は明らかに壊れているので弾く。

    ⚠️ **`null` を一律に破損とはできない**（この関数の対象外の話）＝`to_dict` は
    **hop_rf の RF 値には `null` を書く**（空欄＝共通設定の踏襲）。そちらは
    `_opt_num` が正しく `None` のまま通す。**書く側が実際に書く形**で線を引く。
    """
    value = container.get(key, _MISSING)
    if value is _MISSING:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    raise ProjectError(i18n.t("proj_err_broken").format(reason=what or key))


def _name(container: dict, key: str, default: str, what: str) -> str:
    """**開いた集合**の識別子を取り出す（未知の値は通すが、必ず文字列）。

    `topology` 用。**未知の文字列を通すのは設計判断**＝後から星型を足した版の
    ファイルを古いアプリが黙って壊さないため（意味づけは `multihop.links` の
    1 か所に置く）。⚠️ **だからといって数値まで通す理由はない**＝`_text` の
    数値救済を効かせると `1` が `"1"` として通り、**後段の集約で例外**になる。
    """
    value = container.get(key, _MISSING)
    if value is _MISSING:
        return default
    if not isinstance(value, str):
        raise ProjectError(i18n.t("proj_err_broken").format(reason=what))
    return value


def _enum(container: dict, key: str, allowed: tuple, default: str, what: str) -> str:
    """**閉じた集合**の値を取り出す（許可値以外は `ProjectError`）。

    `scenario.mode` 用。⚠️ **「知らない値なら既定値」にしない**＝`"mode": 1` も
    `"mode": "xyz"` も**黙って `"compare"` に化ける**（2026-08-04・独立レビュー
    Codex 5 巡目。数値の例で指摘されたが、**文字列でも同じ**）。
    書く側は必ず許可値のどれかを書くので、それ以外は壊れている。

    ⚠️ **前方互換は `schema_version` の担当**＝将来 mode が増えた版のファイルは
    「新しい版は拒否」で先に止まる。ここで緩める必要はない（`topology` は
    *同じ版の中で*未知の値を持ち得るので扱いが違う＝上の `_name`）。
    """
    value = container.get(key, _MISSING)
    if value is _MISSING:
        return default
    if not isinstance(value, str) or value not in allowed:
        raise ProjectError(i18n.t("proj_err_broken").format(reason=what))
    return value


def _read_map(raw: dict, what: str) -> dict[str, str]:
    """**読む側**の `dict[str, str]` 変換（値が壊れていれば `ProjectError`）。

    ⚠️ `_str_map` は**書く側**の正規化なので、`None` を `""` にするなど寛容で
    よい。同じ関数を読みに使うと、**壊れた値を黙って文字列に化かす**＝
    `compare[].h_tx: null` → `""` で**条件指定が消え**、`params.start: []` →
    `"[]"` が通っていた（2026-08-04・Codex 5 巡目）。

    受けるのは**文字列と数値**（手書きの `"freq_mhz": 2400` を弾く理由がない）。
    `null` / 配列 / dict / 真偽値は壊れている。
    """
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            out[str(key)] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out[str(key)] = str(value)
        else:
            raise ProjectError(i18n.t("proj_err_broken").format(
                reason=f"{what}.{key}"))
    return out


def _dicts(items: list, what: str) -> list:
    """リストの要素が全て dict であることを確かめて返す。

    ⚠️ **黙って捨てない**＝以前は `if isinstance(c, dict)` で非 dict を除外して
    いたので、`"compare": [5]` が**空の compare として読め**、そのまま保存すると
    **条件が消えた**（同上・実測）。落とすくらいなら読めないと言う方が安全。
    """
    for item in items:
        if not isinstance(item, dict):
            raise ProjectError(i18n.t("proj_err_broken").format(reason=what))
    return items


# ============================================================
# シリアライズ
# ============================================================
def to_dict(doc: ProjectDoc) -> dict:
    """`ProjectDoc` → JSON にできる dict。**節が None ならキーごと出さない。**"""
    data: dict = {
        "schema_version": SCHEMA_VERSION,
        "app_version":    doc.app_version or version.APP_VERSION,
        "saved_at":       doc.saved_at or datetime.now().isoformat(timespec="seconds"),
        "meta":           _str_map(doc.meta),
        # ⚠️ 書く側でも sim キーだけに絞る＝app キーが混ざったファイルを作らない。
        "params":         config.select_sim(_str_map(doc.params)),
    }
    if doc.batch_rows is not None:
        data["batch"] = {"rows": [_row_to_dict(r) for r in doc.batch_rows]}
    if doc.scenario is not None:
        data["scenario"] = {
            "mode":    doc.scenario.mode,
            "compare": [_str_map(c) for c in doc.scenario.compare],
            "sweep":   _str_map(doc.scenario.sweep),
        }
    if doc.multihop is not None:
        p = doc.multihop
        data["multihop"] = {
            "path_id":   p.path_id,
            "note":      p.note,
            # 点列をどう結ぶか（既定＝鎖）。**書いておくのは、後から星を使う版が
            # 出たときに古いファイルの意味が変わらないため**（読む側は未知の値を
            # 鎖として扱う＝`multihop.links` の約束）。
            "topology":  p.topology,
            # 高さは**地点にしかない**（`multihop.Waypoint` の核心）。区間側に
            # 高さを足すと二重入力が復活するので、この形を崩さないこと。
            "waypoints": [{"name": w.name, "lat": w.lat, "lon": w.lon, "h": w.h}
                          for w in p.waypoints],
            "hop_rf":    [{"freq_mhz": rf.freq_mhz, "gain_tx": rf.gain_tx,
                           "gain_rx": rf.gain_rx} for rf in p.hop_rf],
        }
    return data


def _row_to_dict(row: batch.PathRow) -> dict:
    return {
        "path_id": row.path_id,
        "lat_tx":  row.lat_tx, "lon_tx": row.lon_tx,
        "lat_rx":  row.lat_rx, "lon_rx": row.lon_rx,
        "h_tx":    row.h_tx,   "h_rx":   row.h_rx,
        "freq_mhz": row.freq_mhz, "gain_tx": row.gain_tx, "gain_rx": row.gain_rx,
        "note":    row.note,
    }


def from_dict(data: dict) -> ProjectDoc:
    """JSON の dict → `ProjectDoc`（**復元はここでしか行わない**）。

    ⚠️ `MultiHopPath` の組み立て点を増やさないための約束＝views 側は「受け取って
    画面に流す」だけにする（5b で `_collect_path` を唯一の組み立て点にしたのと対）。
    """
    if not isinstance(data, dict):
        raise ProjectError(i18n.t("proj_err_not_project"))

    ver = data.get("schema_version")
    if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
        # 版が無い＝そもそも我々のファイルではない（settings.json を誤って開いた等）。
        raise ProjectError(i18n.t("proj_err_not_project"))
    if ver > SCHEMA_VERSION:
        raise ProjectError(i18n.t("proj_err_newer").format(
            ver=ver, cur=SCHEMA_VERSION))

    doc = ProjectDoc(
        meta        = _read_map(_map(data, "meta"), "meta"),
        # ⚠️ 読む側でも sim キーだけ＝ファイルに app キーが混ざっていても
        # theme/lang/proxy_url は取り込まない（`select_sim` が唯一の関門）。
        params      = config.select_sim(_read_map(_map(data, "params"), "params")),
        app_version = _text(data, "app_version"),
        saved_at    = _text(data, "saved_at"),
    )

    b = _section(data, "batch")
    if b is not None:
        rows = _dicts(_seq(b, "rows", "batch.rows"), "batch.rows")
        doc.batch_rows = [_row_from_dict(r) for r in rows]

    s = _section(data, "scenario")
    if s is not None:
        doc.scenario = ScenarioSpec(
            mode    = _enum(s, "mode", SCENARIO_MODES, "compare", "scenario.mode"),
            compare = [_read_map(c, "compare") for c in
                       _dicts(_seq(s, "compare"), "compare")],
            sweep   = _read_map(_map(s, "sweep"), "sweep"),
        )

    m = _section(data, "multihop")
    if m is not None:
        wps = _dicts(_seq(m, "waypoints"), "waypoints")
        doc.multihop = mh.MultiHopPath(
            path_id   = _text(m, "path_id", what="multihop.path_id"),
            waypoints = [mh.Waypoint(name=_text(w, "name", what="waypoints.name"),
                                     lat=_num(w.get("lat"), "lat"),
                                     lon=_num(w.get("lon"), "lon"),
                                     h=_num(w.get("h"), "h"))
                         for w in wps],
            hop_rf    = [mh.HopRF(freq_mhz=_opt_num(rf.get("freq_mhz"), "freq_mhz"),
                                  gain_tx=_opt_num(rf.get("gain_tx"), "gain_tx"),
                                  gain_rx=_opt_num(rf.get("gain_rx"), "gain_rx"))
                         for rf in _dicts(_seq(m, "hop_rf"), "hop_rf")],
            note      = _text(m, "note", what="multihop.note"),
            # 欠損＝鎖（`.rsproj` の「欠損は既定値」）。未知の値もそのまま持たせ、
            # 意味づけは `multihop.links` に任せる（判定を 2 か所に置かない）。
            topology  = _name(m, "topology", mh.TOPOLOGY_CHAIN, "multihop.topology"),
        )
    return doc


def _row_from_dict(r: dict) -> batch.PathRow:
    if not isinstance(r, dict):
        raise ProjectError(i18n.t("proj_err_broken").format(reason="batch.rows"))
    return batch.PathRow(
        path_id  = _text(r, "path_id", what="batch.rows.path_id"),
        lat_tx   = _num(r.get("lat_tx"), "lat_tx"),
        lon_tx   = _num(r.get("lon_tx"), "lon_tx"),
        lat_rx   = _num(r.get("lat_rx"), "lat_rx"),
        lon_rx   = _num(r.get("lon_rx"), "lon_rx"),
        h_tx     = _num(r.get("h_tx"),   "h_tx"),
        h_rx     = _num(r.get("h_rx"),   "h_rx"),
        freq_mhz = _opt_num(r.get("freq_mhz"), "freq_mhz"),
        gain_tx  = _opt_num(r.get("gain_tx"),  "gain_tx"),
        gain_rx  = _opt_num(r.get("gain_rx"),  "gain_rx"),
        note     = _text(r, "note", what="batch.rows.note"),
    )


# ============================================================
# ファイル I/O
# ============================================================
def unreadable_row(rows: "list[batch.PathRow] | None") -> "batch.PathRow | None":
    """数値として読めない値（NaN/inf）を含む行を 1 つ返す（無ければ None）。

    バッチの表は**入力途中でも読める**ように、パースできない欄を NaN のまま持つ
    （`_read_table_rows`）。それをそのまま書くと `NaN` リテラルが混ざって
    **JSON として不正なファイル**になる（他のツールで開けない・`allow_nan=False`
    が下で弾く）。⇒ 呼び出し側が保存前に気づけるようにここで見つける。
    """
    if not rows:
        return None
    for row in rows:
        for value in (row.lat_tx, row.lon_tx, row.lat_rx, row.lon_rx,
                      row.h_tx, row.h_rx, row.freq_mhz, row.gain_tx, row.gain_rx):
            if value is not None and not math.isfinite(value):
                return row
    return None


def save(doc: ProjectDoc, path: str) -> None:
    """`.rsproj` として保存する。

    ⚠️ **失敗を握り潰さない**（I-010 と同クラス）＝書けなかったことを呼び出し側が
    知らないまま「保存しました」と出すのが最悪の振る舞い。例外はそのまま上げる。

    ⚠️ `allow_nan=False`＝**書けたファイルは必ず正しい JSON**（Python の json は
    既定で `NaN` / `Infinity` を書くが、これは JSON の規格外で他のツールが読めない）。
    読めない値は保存前に `unreadable_row` で気づかせる方が、静かに壊れたファイルを
    作るより良い。

    座標は `params` の中で **DD 固定**（データ＝DD 原則）。呼び出し側（ランチャーの
    `_current_config`）が既に DD へ整えている＝ここで表示形式を持ち込まない。

    🔴 **原子的に置き換える**（2026-08-03・独立レビュー Codex 由来）。⚠️ **対象を
    直接 `"w"` で開いてはいけない**＝open した瞬間に**既存ファイルが 0 バイトに
    切り詰められ**、その後 `json.dump` が途中で失敗すると**前のプロジェクトが
    壊れた JSON として残る**。実測＝56,384 バイトのファイルが 42,473 バイトの
    読めない残骸になった（`allow_nan=False` が中ほどの NaN で送出したケース）。

    失敗の引き金は NaN だけではない（ディスク不足・I/O エラーでも同じ）。
    **条件探索と中継経路にとって `.rsproj` は唯一の永続化手段**なので、
    「保存に失敗したが前のファイルは無事」を成立させる必要がある。

    ⇒ **同じディレクトリの一時ファイルへ書き切ってから `os.replace`**。
    同一ボリュームなので replace は原子的で、失敗しても対象は元のまま。
    """
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".rsproj-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(to_dict(doc), f, indent=2, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())   # 電源断でも「空のファイルに置き換わる」を避ける
        os.replace(tmp, path)      # 同一ディレクトリ＝原子的。既存は最後まで無傷
    except BaseException:
        # 失敗したら一時ファイルを片付けて、例外はそのまま上げる（握り潰さない）。
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(path: str) -> ProjectDoc:
    """`.rsproj` を読む。読めない理由は `ProjectError`（i18n 済み）に畳む。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except ProjectError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProjectError(i18n.t("proj_err_broken").format(reason=e)) from e
    return from_dict(data)


def default_filename(project_name: str = "") -> str:
    """保存ダイアログの初期ファイル名（案件名があればそれを使う）。

    ファイル名に使えない文字は落とす（案件名は自由文字列＝`/` や `:` が来る）。
    """
    name = "".join(c for c in project_name.strip()
                   if c not in '\\/:*?"<>|').strip()
    return (name or "project") + FILE_EXT
