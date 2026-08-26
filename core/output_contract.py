"""
core/output_contract.py
=======================
機械可読な成果物（CSV）の**列仕様＝出力契約**の単一ソース（ヘッドレス・純データ）。

利用者は成果物を表計算や自前の集計スクリプトへ流し込む。**列の名前・並び・意味は
そのまま相手のシートの数式や列参照になる**ので、こちらの都合で動かすと黙って壊れる
（実例＝2.5 で `slant_km` → `slant_m` に変えたとき、告知が CHANGELOG の 1 行しか
なかった）。ここに置いた定義が、書き手・文書・ゲートの共通の出所になる。

🔑 **書き手は必ずここから見出し行を取る**（列を手書きしない）。手書きだと
「行に値を足したのに見出しを足し忘れる」がすり抜ける＝列と値がずれた CSV が出る。

🔑 **`CSV_CONTRACTS` は全成果物 CSV の台帳**＝新しい CSV を足したらここにも足す。
`tests/test_output_contract.py` が **`core/` `report/` の `csv.writer` 呼び出しを
すべて数え**、台帳に無い書き手を落とす（列挙で塞いだ穴は、次に足す 1 本で開く
→ [[feedback-user-examples-are-classes]]）。

⚠️ **入力 CSV（`report.batch.CSV_COLUMNS`）はここに置かない**＝あれはアプリ自身が
読み戻す**交換フォーマット**で、契約の向きが逆（我々が読む側の約束）。実際に扱いも
分かれている＝成果物は `report_common.csv_cell()` で数式化を止めるが、交換物には
掛けない（`'` を足すと再インポートで値が変わる＝往復が壊れる・B-012）。

------------------------------------------------------------------------------
📜 **変更規約**（この版〔3.0〕で定めた・公開文書にも同じ規約を載せてある）
------------------------------------------------------------------------------
1. **列の追加は末尾のみ。** 既存列の位置は動かさない（位置で読む相手が居る）。
2. **列の削除・改名・意味の変更は、1 つ前の版で予告してから。** 予告は CHANGELOG
   と公開文書の両方に書く（片方だけだと、配布版だけを見る利用者に届かない）。
3. **値の書式（単位・桁・頭打ち）も契約のうち。** 単位を変えるときは列名も変える
   （`slant_km` → `slant_m` の型＝名前が変われば古い数式は静かに通らず落ちる）。
4. **ファイル名も契約。** 1 行の意味（`row_meaning`）が変わるときは列を足すのでは
   なく**別ファイルにする**（実例＝中継は `summary.csv` に `group_id` を足さず
   `hops.csv` を切った）。
⚠️ **明文化だけだと「二度と変えられない」に化ける。** 上の 2・3 は*変えてよい道*を
書いたもので、禁止事項ではない。

------------------------------------------------------------------------------
📌 **規約 3 を自分で通した実例**（3.0a1・2026-08-26）
------------------------------------------------------------------------------
**dB 系の値の桁を `0.01` → `0.1` に変えた**（`rx_dbm` `margin_db` `fspl_db`
`diff_db` `veg_db` `env_db` `rain_db` `gas_db` `total_loss_db` と、利得の 2 列）。
- **列名は変えていない**＝規約 3 が改名を求めるのは*単位*を変えるときで、桁だけの
  変更は数値として読む相手には黙って通る（`-93.20` → `-93.2` は同じ数）。
- **理由**＝0.01 dB 刻みは**持っていない精度の主張**だった（DEM は水平 5〜10m・
  標高にも数 m の誤差、植生高は仮定値、環境損失は区分の経験値）。しかも
  **画面は元から 0.1 dB** で、成果物だけが 2 桁を名乗っていた。
- **告知**＝CHANGELOG と公開文書の両方（規約 2 と同じ道＝配布版だけを見る利用者に
  届かない書き方をしない）。桁は `core/units.py` の `DB_DECIMALS` が単一ソース。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CsvContract:
    """1 つの成果物 CSV の列仕様。

    Attributes:
        filename:    出力されるファイル名（**これも契約**）。
        row_meaning: 「1 行 = 何か」。**行の意味が変わるなら別ファイル**（規約 4）。
        columns:     見出し行そのもの（並びも契約）。
        writer:      これを書く関数（`module:function`）。ゲートが実在を確かめる。
        variable:    実行時に字が変わる唯一の列があるならその位置の既定名。
                     いまは条件探索の軸だけ（→ `scenario_csv_columns`）。
    """

    filename: str
    row_meaning: str
    columns: tuple[str, ...]
    writer: str
    variable: str = ""


# --- 個別シミュレーション ----------------------------------------------------
TERRAIN_CSV_COLUMNS: tuple[str, ...] = ("Distance_m", "Elevation_m")

# --- 複数経路（バッチ）------------------------------------------------------
# 🆕 `samples`（3.0a1 / I-069）＝**その回線で実際に地形を刻んだ点数**。利用者は
# 解像度の段階（高/中/低）を選び、点数は**経路の長さから 1 行ごとに解かれる**ように
# なったので、**同じ CSV の中で行によって違う**。⇒ これが無いと「どれだけ細かく見た
# 答えなのか」が成果物から復元できない（以前は共通設定の 1 つの数で全行同じだった）。
# 実効間隔は列にしない＝`slant_m` と点数から割れる**導出値**で、規約の面を増やさない。
#
# 🆕 `f1_depth_x`（3.0a1 / I-077）は **末尾に足してある**（規約 1 の最初の実例）。
# `f1_pct` の隣に置くほうが読みやすいが、**既存列の位置を動かすと位置で読む相手が
# 静かに壊れる**＝読みやすさより互換を採る。3 本の CSV とも同じ扱いにした
# （`summary.csv` / `hops.csv` / `scenario.csv`。HTML 台帳のほうは位置の契約が
# 無いので `f1_pct` の隣に置いてある＝**人が読む面と機械が読む面で並びが違う**）。
SUMMARY_CSV_COLUMNS: tuple[str, ...] = (
    "id", "status", "freq_mhz", "gain_tx_dbi", "gain_rx_dbi",
    "h_tx", "h_rx",
    "rx_dbm", "margin_db",
    "fspl_db", "diff_db", "veg_db", "env_db",
    "rain_db", "gas_db", "total_loss_db",
    "slant_m", "f1_pct", "note", "error",
    "f1_depth_x",
    "samples",
)

# --- 中継経路 ---------------------------------------------------------------
HOPS_CSV_COLUMNS: tuple[str, ...] = (
    "group_id", "hop_index", "hop_id", "from", "to", "status",
    "freq_mhz", "gain_tx_dbi", "gain_rx_dbi", "h_tx", "h_rx",
    "rx_dbm", "margin_db", "slant_m", "f1_pct", "error",
    "f1_depth_x",
    "samples",
)

# --- 条件探索 ---------------------------------------------------------------
#: 2 列目の既定名（比較モード）。スイープでは**軸の名前**に置き換わる唯一の可変列。
SCENARIO_CSV_AXIS_COLUMN = "condition"

SCENARIO_CSV_COLUMNS: tuple[str, ...] = (
    "label", SCENARIO_CSV_AXIS_COLUMN, "status",
    "rx_dbm", "margin_db", "total_loss_db",
    "fspl_db", "diff_db", "veg_db", "env_db", "rain_db", "gas_db",
    "f1_pct", "slant_m",
    "freq_mhz", "p_tx_dbm", "gain_tx_dbi", "gain_rx_dbi", "sens_dbm",
    "h_tx", "h_rx", "veg_h", "rain_mmh", "env_type", "diff_method",
    "f1_depth_x",
)


def scenario_csv_columns(axis: str | None) -> tuple[str, ...]:
    """条件探索の見出し行を返す（スイープでは 2 列目が軸の名前になる）。

    ⚠️ **軸の名前は固定列と衝突しうる**（`freq_mhz` `h_tx` `h_rx` `veg_h` の 4 軸は
    同名の固定列を持つ＝見出しに同じ字が 2 回出る）。列を辞書で読む相手にとっては
    後勝ちになるので、**位置で読むか、2 列目を軸の値として読む**こと。

    📣 **3.1 で直す（3.0 で予告済み・I-112）**＝2 列目に**固定の名前**を与え、
    同じ名前が 2 回出る形をやめる（軸の名前は別の場所で持つ）。**新しい列名は
    3.1 で決める**＝3.0 の予告は*方向*だけを約束している（ユーザー決定 2026-08-26）。
    ⚠️ 予告は **CHANGELOG と公開文書 4 本**に載せてある＝規約 2 を自分で通した最初の
    実例。載っているかは `tests/test_output_contract.py` が重なりの続く間だけ見張る。
    """
    if not axis:
        return SCENARIO_CSV_COLUMNS
    return tuple(
        axis if i == 1 else c for i, c in enumerate(SCENARIO_CSV_COLUMNS)
    )


#: 全成果物 CSV の台帳（**新しい CSV を足したらここにも足す**）。
CSV_CONTRACTS: tuple[CsvContract, ...] = (
    CsvContract(
        filename="terrain_profile.csv",
        row_meaning="1 行 = 地形断面の 1 標本点",
        columns=TERRAIN_CSV_COLUMNS,
        writer="core.simulation:_save_terrain_csv",
    ),
    CsvContract(
        filename="summary.csv",
        row_meaning="1 行 = 1 回線",
        columns=SUMMARY_CSV_COLUMNS,
        writer="report.report_summary:_save_summary_csv",
    ),
    CsvContract(
        filename="hops.csv",
        row_meaning="1 行 = 1 区間（N 行で 1 経路）",
        columns=HOPS_CSV_COLUMNS,
        writer="report.multihop:_write_hops_csv",
    ),
    CsvContract(
        filename="scenario.csv",
        row_meaning="1 行 = 1 条件（スイープでは 1 点）",
        columns=SCENARIO_CSV_COLUMNS,
        writer="report.report_scenario:save_scenario_csv",
        variable=SCENARIO_CSV_AXIS_COLUMN,
    ),
)
