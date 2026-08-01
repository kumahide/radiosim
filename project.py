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


def _str_map(value) -> dict[str, str]:
    """dict[str, str] へ正規化（数値が入っていても文字列にする）。"""
    if not isinstance(value, dict):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in value.items()}


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
        meta        = _str_map(data.get("meta")),
        # ⚠️ 読む側でも sim キーだけ＝ファイルに app キーが混ざっていても
        # theme/lang/proxy_url は取り込まない（`select_sim` が唯一の関門）。
        params      = config.select_sim(_str_map(data.get("params"))),
        app_version = str(data.get("app_version", "")),
        saved_at    = str(data.get("saved_at", "")),
    )

    b = data.get("batch")
    if isinstance(b, dict) and isinstance(b.get("rows"), list):
        doc.batch_rows = [_row_from_dict(r) for r in b["rows"]]

    s = data.get("scenario")
    if isinstance(s, dict):
        mode = str(s.get("mode", "compare"))
        doc.scenario = ScenarioSpec(
            mode    = mode if mode in ("compare", "sweep") else "compare",
            compare = [_str_map(c) for c in s.get("compare", [])
                       if isinstance(c, dict)],
            sweep   = _str_map(s.get("sweep")),
        )

    m = data.get("multihop")
    if isinstance(m, dict):
        wps = m.get("waypoints")
        if not isinstance(wps, list):
            raise ProjectError(i18n.t("proj_err_broken").format(reason="waypoints"))
        doc.multihop = mh.MultiHopPath(
            path_id   = str(m.get("path_id", "")),
            waypoints = [mh.Waypoint(name=str(w.get("name", "")),
                                     lat=_num(w.get("lat"), "lat"),
                                     lon=_num(w.get("lon"), "lon"),
                                     h=_num(w.get("h"), "h"))
                         for w in wps if isinstance(w, dict)],
            hop_rf    = [mh.HopRF(freq_mhz=_opt_num(rf.get("freq_mhz"), "freq_mhz"),
                                  gain_tx=_opt_num(rf.get("gain_tx"), "gain_tx"),
                                  gain_rx=_opt_num(rf.get("gain_rx"), "gain_rx"))
                         for rf in m.get("hop_rf", []) if isinstance(rf, dict)],
            note      = str(m.get("note", "")),
        )
    return doc


def _row_from_dict(r: dict) -> batch.PathRow:
    if not isinstance(r, dict):
        raise ProjectError(i18n.t("proj_err_broken").format(reason="batch.rows"))
    return batch.PathRow(
        path_id  = str(r.get("path_id", "")),
        lat_tx   = _num(r.get("lat_tx"), "lat_tx"),
        lon_tx   = _num(r.get("lon_tx"), "lon_tx"),
        lat_rx   = _num(r.get("lat_rx"), "lat_rx"),
        lon_rx   = _num(r.get("lon_rx"), "lon_rx"),
        h_tx     = _num(r.get("h_tx"),   "h_tx"),
        h_rx     = _num(r.get("h_rx"),   "h_rx"),
        freq_mhz = _opt_num(r.get("freq_mhz"), "freq_mhz"),
        gain_tx  = _opt_num(r.get("gain_tx"),  "gain_tx"),
        gain_rx  = _opt_num(r.get("gain_rx"),  "gain_rx"),
        note     = str(r.get("note", "")),
    )


# ============================================================
# ファイル I/O
# ============================================================
def save(doc: ProjectDoc, path: str) -> None:
    """`.rsproj` として保存する。

    ⚠️ **失敗を握り潰さない**（I-010 と同クラス）＝書けなかったことを呼び出し側が
    知らないまま「保存しました」と出すのが最悪の振る舞い。例外はそのまま上げる。

    座標は `params` の中で **DD 固定**（データ＝DD 原則）。呼び出し側（ランチャーの
    `_current_config`）が既に DD へ整えている＝ここで表示形式を持ち込まない。
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_dict(doc), f, indent=2, ensure_ascii=False)


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
