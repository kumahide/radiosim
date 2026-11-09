"""
batch.py
========
複数経路（バッチ）の行の型と検査（UI 知識ゼロ）。

`PathRow`（入力の 1 行）・`PathResult`（1 行の結果）・CSV の 1 行の読み取りと
行の検査・行から `SimParams` を作る口を持つ。新しい画面（`webui/sheets.py`・
`webui/worker.py`）と `.rsproj`（`project.py`）がここを引く。
CSV ファイルの読み書き・実行の糸・帳票は Pro へ移した（Link 1.0 ステージ11b）。
"""

import math
import re
from dataclasses import dataclass

from core import config
from core import i18n
from core import models
from core import simulation as sim


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
#     🔴 **上げるときは表の欄の幅も要る**（旧画面〔Link 1.0 で撤去〕では ID 欄の
#     幅の単一ソースだった＝FHD 150% で余裕 3px しかなかった・実測 2026-08-12）。
#   - `_MAX_PATH_ID_LEN` ＝ **出力ディレクトリ名として許す**長さ。人は打たない＝
#     中継が `route1_h8` のように**生成した** ID がここに収まればよく、表の欄幅
#     とは無関係。`report/multihop.py` の上限（12）＋接尾辞（`_h8`）がこの中に
#     収まることを `tests/test_multihop.py` が固定している。
#
# ⛔ **どちらか一方に戻して「1 つにまとめる」ことはできない**＝16 に揃えると
# B-057 が戻り、11 に揃えると中継の経路 ID が 8 文字まで削られる（無傷のウィンドウを壊す）。
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
    # 実測突合せ（3.4/3.5）用の任意列。省略した行は残差計算の対象から除外する。
    meas_dbm:        float | None = None
    meas_method:     str          = ""
    feeder_loss_db:  float | None = None
    env_class:       str          = ""


@dataclass
class PathResult:
    """1パスの実行結果。"""
    row:     PathRow
    result:  models.LinkBudgetResult | None
    terrain: models.TerrainProfile   | None = None
    params:  sim.SimParams           | None = None
    save_dir: str                           = ""
    error:   Exception               | None = None
    # 生成済みの A4 シート断片（Pro の帳票が詰める）。バッチ完了時に
    # report_all.html へ連結するための保持で、失敗したパスは空のまま。
    sheet_html: str                         = ""
    # 成果物（PNG / report.html / KML）の生成に失敗したときの例外（I-010）。
    # **計算の失敗（`error`）とは別物**＝計算は通ったのに納品物だけが欠けた状態で、
    # 2026-08-03 に実機で現実になった（B-037＝`⚠ 0 ERR` で完走し詳細レポートだけ
    # が無い）。Pro の帳票（`radiosim_pro/report_path.py`）がここへ入れる。
    artifact_error: Exception        | None = None

    @property
    def status(self) -> str:
        """この経路の判定＝`"OK"` / `"NG"` / `"REVIEW"` / `"ERROR"`（REVIEW は v2・4.0 ステージ6）。

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
# 列の契約（列名・順序・必須/任意）は core/batch_csv_schema.py が単一ソース
# （I-159＝RadioSim Field が apps 越しに report/ を import せず契約だけ読めるように）。
# ドキュメント整合テストもそちらを単一ソースに README の CSV セクションを照合する。


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
        meas_dbm       = _opt_float("meas_dbm"),
        meas_method    = raw.get("meas_method", "").strip(),
        feeder_loss_db = _opt_float("feeder_loss_db"),
        env_class      = raw.get("env_class", "").strip(),
    )


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
        # 🔑 **段階を渡す＝点数は行の座標から解かれる**（I-069）。以前はここで
        # `base.num` を渡しており、**500m の行にも 20km の行にも同じ 200 点**が
        # 当たっていた（＝共通設定の 1 つの数が、行によって別の意味になっていた）。
        # ⚠️ **段階を持たない共通設定（固定 N の互換の口）はその点数を引き継ぐ**
        #    （B-137）＝空の段階を渡すと行の側で解けない。この枝に落ちるのは
        #    段階を経由しない呼び出し（回帰コーパスの生成器・探針）だけで、
        #    そこでは「全行が同じ点数」こそが要求されたもの。
        **({"resolution": base.resolution} if base.resolution
           else {"samples": str(base.num)}),
        "env_type"   : base.env_type,
        "rain_rate"  : str(base.rain_rate),
        "diff_method": base.diff_method,
        "dem_source" : base.dem_source,   # 3.4 ステージ1（I-147）
        "calc_profile": base.calc_profile,  # 4.0 ステージ3
    }
    return sim.SimParams(c)
