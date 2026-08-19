"""
batch.py
========
バッチシミュレーション実行エンジン。

UI 知識ゼロ — PathRow リストを受け取って全パスを順次処理する。
CSV パース・エクスポート・バリデーション・実行を担う。
出力生成は report_path.py（per-path）と report_summary.py（サマリ・連結）へ
分離した（A4 骨格の共有部品は report_common.py）。
"""

import csv
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import numpy as np

from core import config
from core import i18n
from core import models
from core import simulation as sim
from report import report_path
from report import report_summary

logger = __import__("logging").getLogger("radiosim")

_PATH_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")

# path_id・備考の最大文字数。長すぎる値は summary 台帳の列幅を押し広げ A4 レイアウトを
# 崩すため、実行前の validate_rows で弾く（手入力・CSV 取込の共通チョークポイント）。
#
# ⚠️ **path_id の上限は 2 つある**（B-057・2026-08-12 に分割）。ひとつの数で
# 両方を兼ねていたのが欠陥の温床だった＝**役割が違う**：
#
#   - `MAX_TYPED_ID_LEN` ＝ **人が表に打ち、表で読む**長さ。複数経路の ID 欄の
#     幅と**同じ数でなければならない**（打てる長さと読める長さが食い違うと、
#     `asaminami24` が `asaminami2` に読めて**別の ID として通用してしまう**）。
#     ⇒ この定数が ID 欄の幅の単一ソースでもある（`views/batch_builder._WIDTHS`）。
#     🔴 **上げるときは欄幅と画面の両方が要る**＝複数経路の窓は FHD 150% で
#     余裕 3px しかなく、周波数・利得・距離は見出しが床で 1px も出ない
#     （実測 2026-08-12）。数字だけ上げると見切れゲートが赤くなる。
#   - `_MAX_PATH_ID_LEN` ＝ **出力ディレクトリ名として許す**長さ。人は打たない＝
#     中継が `route1_h8` のように**生成した** ID がここに収まればよく、表の欄幅
#     とは無関係。`report/multihop.py` の上限（12）＋接尾辞（`_h8`）がこの中に
#     収まることを `tests/test_multihop.py` が固定している。
#
# ⛔ **どちらか一方に戻して「1 つにまとめる」ことはできない**＝16 に揃えると
# B-057 が戻り、11 に揃えると中継の経路 ID が 8 文字まで削られる（無傷の窓を壊す）。
MAX_TYPED_ID_LEN = 11
_MAX_PATH_ID_LEN = 16
_MAX_NOTE_LEN    = 40


# ============================================================
# データ構造
# ============================================================
@dataclass
class PathRow:
    """1パス分の入力データ。None フィールドは base_params の値を継承する。"""
    path_id:  str
    lat_tx:   float
    lon_tx:   float
    lat_rx:   float
    lon_rx:   float
    h_tx:     float
    h_rx:     float
    freq_mhz: float | None = None
    gain_tx:  float | None = None
    gain_rx:  float | None = None
    note:     str          = ""


@dataclass
class PathResult:
    """1パスの実行結果。"""
    row:     PathRow
    result:  models.LinkBudgetResult | None
    terrain: models.TerrainProfile   | None = None
    params:  sim.SimParams           | None = None
    save_dir: str                           = ""
    error:   Exception               | None = None
    # 生成済みの A4 シート断片（report_path が詰める）。バッチ完了時に
    # report_all.html へ連結するための保持で、失敗したパスは空のまま。
    sheet_html: str                         = ""
    # 成果物（PNG / report.html / KML）の生成に失敗したときの例外（I-010）。
    # **計算の失敗（`error`）とは別物**＝計算は通ったのに納品物だけが欠けた状態で、
    # 2026-08-03 に実機で現実になった（B-037＝`⚠ 0 ERR` で完走し詳細レポートだけ
    # が無い）。`report_path.save_path_visuals` がここへ入れる。
    artifact_error: Exception        | None = None

    @property
    def status(self) -> str:
        """この経路の判定＝`"OK"` / `"NG"` / `"ERROR"`。

        🔑 **判定を作る場所はここだけ**（I-010 ③）。画面・台帳・CSV・KML・地図が
        それぞれ `pr.result is not None and pr.result.status == "OK"` を書いていた
        ころは、**成果物の失敗を数える口が 1 つも無い**のに全部が「成功」と読めた。
        条件を足すなら 1 か所で足りるように、判定はここから引く。

        **成果物の失敗も ERROR に含める**＝計算が通っていても納品物が欠けていれば
        「成功」ではない（バッチの出口は確定成果物ファイル）。数値そのものは
        `result` に残るので、台帳は値を出したままエラー行として扱える。
        """
        if self.result is None or self.artifact_error is not None:
            return "ERROR"
        return self.result.status

    @property
    def ok(self) -> bool:
        """**実行が成功したか**（＝判定 OK/NG ではない。NG でも実行は成功）。

        ⚠️ `status` と混同しないこと＝あちらは回線の判定、こちらは「この経路の
        処理が最後まで通ったか」。成果物が欠けた経路はここでも成功ではない。
        """
        return self.result is not None and self.artifact_error is None


# ============================================================
# CSV I/O
# ============================================================
_REQUIRED_COLS = {"id", "start", "end", "h_tx", "h_rx"}

# CSV スキーマの正準（出力ヘッダ順）。required の後に optional。
# ドキュメント整合テストはこの定数を単一ソースに README の CSV 節を照合する。
CSV_COLUMNS = ["id", "start", "end", "h_tx", "h_rx", "freq", "gain_tx", "gain_rx", "note"]
OPTIONAL_COLS = [c for c in CSV_COLUMNS if c not in _REQUIRED_COLS]

def parse_csv(csv_path: str) -> list[PathRow]:
    """
    CSV ファイルを PathRow リストに変換する。

    必須列: id, start, end, h_tx, h_rx
    省略可: freq, gain_tx, gain_rx, note
    """
    rows: list[PathRow] = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(i18n.t("berr_no_header"))
        cols = {_norm_key(c) for c in reader.fieldnames}
        missing = _REQUIRED_COLS - cols
        if missing:
            raise ValueError(i18n.t("berr_missing_cols").format(
                cols=", ".join(sorted(missing))))

        for line_no, raw in enumerate(reader, start=2):
            rows.append(_parse_csv_row(_normalize_row(raw), line_no))

    if not rows:
        raise ValueError(i18n.t("berr_no_rows"))
    return rows


def _norm_key(key: str) -> str:
    """CSV 列名の正準形（前後空白を落として小文字）を返す。"""
    return key.strip().lower()


def _normalize_row(raw: dict) -> dict[str, str]:
    """DictReader の 1 行を正準キーの dict へ寄せる。

    ⚠️ **ヘッダ判定と行アクセスで正規化の有無を非対称にしない**（B-011）。
    以前はヘッダだけ `strip().lower()` して必須列ありと判定し、行は生キーで
    引いていたため、`ID,Start,…` のような大文字混じりの正しい CSV が
    「必須列あり」と判定された直後に「Row 2: 'id' is empty」で落ちていた。

    値の `None` は空文字へ倒す（列数が足りない行を DictReader が None で
    埋めるため。ここで倒しておけば以降は「空欄」として一様に扱える）。
    """
    out: dict[str, str] = {}
    for key, val in raw.items():
        if key is None:      # 余剰列（restkey）は捨てる
            continue
        out[_norm_key(key)] = "" if val is None else val
    return out


def _parse_csv_row(raw: dict, line: int) -> PathRow:
    pid = raw.get("id", "").strip()
    if not pid:
        raise ValueError(i18n.t("berr_id_empty").format(line=line))

    def _coord(key: str) -> tuple[float, float]:
        val = raw.get(key, "").strip()
        parts = val.split(",")
        if len(parts) != 2:
            raise ValueError(i18n.t("berr_coord_format").format(line=line, key=key))
        try:
            return float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            # ⚠️ ここを素通りさせると `could not convert string to float: 'x'`
            # という**英語の内部診断がそのまま画面へ出る**（I-100 で塞いだ穴）。
            raise ValueError(i18n.t("berr_coord_invalid").format(
                line=line, key=key, val=val)) from None

    def _float(key: str) -> float:
        val = raw.get(key, "").strip()
        try:
            return float(val)
        except ValueError:
            raise ValueError(i18n.t("berr_not_number").format(
                line=line, key=key, val=val)) from None

    def _opt_float(key: str) -> float | None:
        val = raw.get(key, "").strip()
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            raise ValueError(i18n.t("berr_not_number").format(
                line=line, key=key, val=val)) from None

    lat_tx, lon_tx = _coord("start")
    lat_rx, lon_rx = _coord("end")
    return PathRow(
        path_id  = pid,
        lat_tx   = lat_tx,
        lon_tx   = lon_tx,
        lat_rx   = lat_rx,
        lon_rx   = lon_rx,
        h_tx     = _float("h_tx"),
        h_rx     = _float("h_rx"),
        freq_mhz = _opt_float("freq"),
        gain_tx  = _opt_float("gain_tx"),
        gain_rx  = _opt_float("gain_rx"),
        note     = raw.get("note", "").strip(),
    )


def export_csv(rows: list[PathRow], csv_path: str) -> None:
    """PathRow リストを CSV ファイルに書き出す。"""
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for r in rows:
            writer.writerow([
                r.path_id,
                f"{r.lat_tx}, {r.lon_tx}",
                f"{r.lat_rx}, {r.lon_rx}",
                r.h_tx,
                r.h_rx,
                r.freq_mhz if r.freq_mhz is not None else "",
                r.gain_tx  if r.gain_tx  is not None else "",
                r.gain_rx  if r.gain_rx  is not None else "",
                r.note,
            ])


# ============================================================
# バリデーション
# ============================================================
def validate_rows(
    rows: list[PathRow], *, max_id_len: int = MAX_TYPED_ID_LEN,
) -> list[str]:
    """PathRow リストを検証してエラーメッセージのリストを返す。空リストなら正常。

    `max_id_len` は既定で「人が打てる長さ」＝表の ID 欄で読める長さ（B-057）。
    ⚠️ **生成された行を検証する呼び出しだけが明示的に渡す**（中継の `_h8` 付き
    ID）＝人が打っていないものに「表で読めるか」を要求しても意味が無く、逆に
    ここを既定で緩めると CSV 取込が読めない ID を通してしまう。
    """
    errors: list[str] = []
    if not rows:
        errors.append(i18n.t("verr_empty"))
        return errors

    # ⚠️ 重複判定は **大小を区別しない**（B-013）。path_id はそのまま出力
    # ディレクトリ名になるが、Windows のファイルシステムは大小を区別しない
    # ため、`p01` と `P01` を別 ID として通すと両者の成果物が同一パスへ書かれ
    # 先勝ち/後勝ちで黙って混ざる。ID そのものは入力どおり保持する（表示・
    # ディレクトリ名は原文のまま）。
    seen: set[str] = set()
    for r in rows:
        key = r.path_id.casefold()
        if key in seen:
            errors.append(i18n.t("verr_duplicate_id").format(pid=r.path_id))
        seen.add(key)

    for r in rows:
        pid = r.path_id
        if not _PATH_ID_RE.fullmatch(pid):
            errors.append(i18n.t("verr_invalid_id").format(pid=repr(pid)))
            continue
        if len(pid) > max_id_len:
            errors.append(i18n.t("verr_id_too_long").format(
                pid=pid, max=max_id_len, n=len(pid)))
        if len(r.note) > _MAX_NOTE_LEN:
            errors.append(i18n.t("verr_note_too_long").format(
                pid=pid, max=_MAX_NOTE_LEN, n=len(r.note)))
        coords = [r.lat_tx, r.lon_tx, r.lat_rx, r.lon_rx, r.h_tx, r.h_rx]
        if any(math.isnan(v) for v in coords):
            errors.append(i18n.t("verr_invalid_coord").format(pid=pid))
            continue
        if not (-85.05 <= r.lat_tx <= 85.05):
            errors.append(i18n.t("verr_tx_lat").format(pid=pid, val=r.lat_tx))
        if not (-180 <= r.lon_tx <= 180):
            errors.append(i18n.t("verr_tx_lon").format(pid=pid, val=r.lon_tx))
        if not (-85.05 <= r.lat_rx <= 85.05):
            errors.append(i18n.t("verr_rx_lat").format(pid=pid, val=r.lat_rx))
        if not (-180 <= r.lon_rx <= 180):
            errors.append(i18n.t("verr_rx_lon").format(pid=pid, val=r.lon_rx))
        if abs(r.lat_tx - r.lat_rx) < 1e-7 and abs(r.lon_tx - r.lon_rx) < 1e-7:
            errors.append(i18n.t("verr_identical").format(pid=pid))
        # 値域はリテラルで持たず config.VALIDATION_RULES から引く（B-018）。
        # ランチャー／条件探索／バッチ共通設定はすべてこの表を出所にしているので、
        # 行だけが第2の出所になっていると、表を直したとき行の範囲だけ取り残される。
        for attr, val, msg_key in (
            ("h_tx",    r.h_tx,     "verr_h_tx"),
            ("h_rx",    r.h_rx,     "verr_h_rx"),
            ("freq",    r.freq_mhz, "verr_freq"),
            ("gain_tx", r.gain_tx,  "verr_gain_tx"),
            ("gain_rx", r.gain_rx,  "verr_gain_rx"),
        ):
            if val is None:
                continue                 # 行の任意列（未指定＝共通設定を踏襲）
            vmin, vmax, _ = config.VALIDATION_RULES[attr]
            if not (vmin <= val <= vmax):
                errors.append(i18n.t(msg_key).format(pid=pid, val=val))

    return errors


# ============================================================
# 実行エンジン
# ============================================================
def run_batch(
    rows:              list[PathRow],
    base_params:       sim.SimParams,
    on_path_start:     Callable[[int, int, str], None],
    on_path_progress:  Callable[[int], None],
    on_path_complete:  Callable[[int, int, "PathResult"], None],
    on_batch_complete: Callable[[str, list["PathResult"]], None],
    on_error:          Callable[[Exception], None],
    coord_format:      str = "dd",
    on_path_stage:     "Callable[[str], None] | None" = None,
    project_name:      str = "",
    memo:              str = "",
) -> None:
    """バッチ実行をバックグラウンドスレッドで開始する。

    coord_format は per-path report.txt の人が読む座標表記のみに効く（既定 DD）。

    成果物生成（PNG/HTML/KML・サマリ地図）もこのスレッド内で行う。report_* と
    report_map は Figure+FigureCanvasAgg と PIL のみで tkinter に触れないため
    ワーカースレッドから安全に呼べる（→ save_profile_png の docstring）。GUI を
    固めないために必ずここで生成すること。project_name / memo はレポートの
    ヘッダに載る自由文字列。

    on_path_stage は 1 パス内の段階通知（"fetch" / "render"）。所要時間の大半は
    "render"（matplotlib 描画）なので、呼び出し側はこれで表示を切り替える。
    """
    threading.Thread(
        target = _run_thread,
        args   = (rows, base_params, on_path_start, on_path_progress,
                  on_path_complete, on_batch_complete, on_error, coord_format,
                  on_path_stage, project_name, memo),
        daemon = True,
    ).start()


def _run_thread(
    rows:              list[PathRow],
    base_params:       sim.SimParams,
    on_path_start:     Callable[[int, int, str], None],
    on_path_progress:  Callable[[int], None],
    on_path_complete:  Callable[[int, int, "PathResult"], None],
    on_batch_complete: Callable[[str, list["PathResult"]], None],
    on_error:          Callable[[Exception], None],
    coord_format:      str = "dd",
    on_path_stage:     "Callable[[str], None] | None" = None,
    project_name:      str = "",
    memo:              str = "",
) -> None:
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_dir = config.new_run_dir("batch", timestamp)

        path_results: list[PathResult] = []
        total = len(rows)
        t_batch = time.perf_counter()
        logger.info("Batch started: %d paths → %s", total, batch_dir)

        for i, row in enumerate(rows):
            on_path_start(i + 1, total, row.path_id)
            pr = _process_one(row, base_params, batch_dir, on_path_progress,
                              coord_format, on_path_stage, project_name)
            path_results.append(pr)
            on_path_complete(i + 1, total, pr)

        # サマリ生成もここ（ワーカースレッド）で行う。render_summary_map_b64 は
        # 淡色地図タイルをネットワーク取得するため、GUI スレッドで呼ぶと数秒
        # 固まる（basemap は DEM と別キャッシュなので DEM が暖まっていても
        # コールドになりうる）。
        if on_path_stage:
            on_path_stage("summary")
        t_sum = time.perf_counter()
        map_b64 = report_summary.render_summary_map_b64(path_results)
        logger.info("Summary map complete in %.2fs", time.perf_counter() - t_sum)
        report_summary.save_summary_html(path_results, batch_dir, project_name,
                                         memo, map_b64)
        # 全ページ連結レポート（Ctrl+P 一発で全パスぶんの PDF）。per-path の
        # シート断片は実行中に PathResult へ溜めてあるので追加コストは連結のみ。
        report_summary.save_report_all_html(path_results, batch_dir,
                                            project_name, memo, map_b64)
        report_summary.save_summary_kml(path_results, batch_dir)
        report_summary._save_summary_csv(path_results, batch_dir)
        logger.info("Batch complete: %d paths in %.2fs (summary %.2fs) → %s",
                    total, time.perf_counter() - t_batch,
                    time.perf_counter() - t_sum, batch_dir)
        on_batch_complete(batch_dir, path_results)

    except Exception as ex:
        logger.exception("Batch error: %s", ex)
        on_error(ex)


def _process_one(
    row:         PathRow,
    base:        sim.SimParams,
    batch_dir:   str,
    on_progress: Callable[[int], None],
    coord_format: str = "dd",
    on_stage:     "Callable[[str], None] | None" = None,
    project_name: str = "",
) -> PathResult:
    try:
        params    = _make_params(row, base)
        if on_stage:
            on_stage("fetch")
        raw_elevs = _fetch_sync(params, on_progress)
        terrain   = models.calculate_terrain_profile(
            raw_elevs = raw_elevs,
            lat_tx    = params.lat_tx,
            lon_tx    = params.lon_tx,
            lat_rx    = params.lat_rx,
            lon_rx    = params.lon_rx,
        )
        result = sim.run_calculation(terrain, params.h_tx, params.h_rx, params)

        path_dir = os.path.join(batch_dir, row.path_id)
        os.makedirs(path_dir, exist_ok=True)
        sim._save_settings(params, params.h_tx, params.h_rx, path_dir)
        sim._save_terrain_csv(terrain, path_dir)
        sim._save_report(result, params, params.h_tx, params.h_rx, path_dir,
                         coord_format)
        pr = PathResult(
            row      = row,
            result   = result,
            terrain  = terrain,
            params   = params,
            save_dir = path_dir,
        )

        # PNG/HTML/KML の生成はこのスレッドで行う（メインスレッド制約は無い＝
        # save_profile_png が pyplot ではなく FigureCanvasAgg を使うため）。
        # 1 パスの所要時間はほぼここが占めるので、GUI スレッドに載せると
        # ウィンドウごと固まりプログレスバーの再描画も止まる。
        # ⚠️ mpl_fonts.apply_japanese_font() が matplotlib.rcParams（グローバル）を
        # 書き換えるため、パスの描画を並列化してはいけない（逐次実行を維持）。
        # ガード: tests/test_batch.py::TestRunBatch::test_path_rendering_is_never_parallel
        if on_stage:
            on_stage("render")
        # phase 境界ログ。B-006 の診断では「バッチで最も時間を食う区間」に
        # ログ行が1つも無く、所要時間が最後まで測れなかった（→ 開発環境 C-b3②）。
        t0 = time.perf_counter()
        # 戻り値は `pr.artifact_error` と同じもの（save_path_visuals が pr へ
        # 記録する）＝**呼び出し側が受け取り忘れても失敗が消えない**形にしてある
        # （I-010・受け取り忘れは「静かに成功」に戻る唯一の道だった）。
        report_path.save_path_visuals(pr, coord_format, project_name)
        logger.info("Path '%s' render complete in %.2fs (%s)",
                    row.path_id, time.perf_counter() - t0,
                    "ok" if pr.artifact_error is None else "artifacts failed")

        return pr

    except sim.DemUnreachableError:
        # DEM に届かないのは**経路の問題ではなく環境の問題**なので、1 行の ERR に
        # せず batch ごと止める（B-025 ②）。ここで飲み込むと、残りの経路も必ず
        # 同じ失敗をしながら 1 経路ぶんの待ち時間を積み上げ、最後に「全行 ERR の
        # レポート」を作って終わる＝ユーザーは何が悪いのか分からないまま待たされる。
        # 上位（_run_thread の except）が on_error → ダイアログへ流す。
        raise

    except Exception as ex:
        # ⚠️ **経路 1 本の失敗はここで畳まれ、バッチは完走する**（それが仕様）＝
        # 最上位の except には届かないので、**traceback はここで残すしかない**。
        # 計算・保存・レポート描画の失敗が全部この経路へ入る。
        # ⚠️ **失敗を「成功に紛れさせない」件（I-010）は別**＝ここが直っても、
        # 完了ダイアログが正常に出ることは変わらない。**残すのと見せるのは別の話。**
        logger.exception("Path '%s' failed: %s", row.path_id, ex)
        return PathResult(row=row, result=None, error=ex)


def _make_params(row: PathRow, base: sim.SimParams) -> sim.SimParams:
    """PathRow + base_params から SimParams を生成する。"""
    c: dict[str, str] = {
        "start"      : f"{row.lat_tx}, {row.lon_tx}",
        "end"        : f"{row.lat_rx}, {row.lon_rx}",
        "h_tx"       : str(row.h_tx),
        "h_rx"       : str(row.h_rx),
        "freq"       : str(row.freq_mhz    if row.freq_mhz    is not None else base.freq_mhz),
        "p_tx"       : str(base.p_tx),
        "gain_tx"    : str(row.gain_tx     if row.gain_tx     is not None else base.gain_tx),
        "gain_rx"    : str(row.gain_rx     if row.gain_rx     is not None else base.gain_rx),
        "sens"       : str(base.sens),
        "veg_h"      : str(base.veg_h),
        "k_factor"   : str(base.k_factor),
        "samples"    : str(base.num),
        "env_type"   : base.env_type,
        "rain_rate"  : str(base.rain_rate),
        "diff_method": base.diff_method,
    }
    return sim.SimParams(c)


def _fetch_sync(
    params:      sim.SimParams,
    on_progress: Callable[[int], None],
) -> np.ndarray:
    """標高取得の非同期コールバックを threading.Event で同期化する。

    単一実行と同じく **キャッシュ付き**（fetch_elevations_cached）を使う。同一
    バッチの再実行や 1 行だけ直しての再実行で DEM 取得がまるごと消える。キーは
    座標＋サンプル数なので、行が違えばキャッシュも別（誤ヒットしない）。
    """
    result: list[np.ndarray] = []
    error:  list[Exception]  = []
    done = threading.Event()

    def _on_complete(e: np.ndarray) -> None:
        result.append(e)
        done.set()

    def _on_error(ex: Exception) -> None:
        error.append(ex)
        done.set()

    sim.fetch_elevations_cached(
        params,
        on_progress = on_progress,
        on_complete = _on_complete,
        on_error    = _on_error,
    )
    done.wait()
    if error:
        raise error[0]
    return result[0]
