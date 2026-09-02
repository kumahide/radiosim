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

------------------------------------------------------------------------------
📌 **規約 2 を自分で通した実例**（I-112・3.0a1 で予告 → 3.1 で改名）
------------------------------------------------------------------------------
`scenario.csv` の 2 列目は、以前はスイープ軸の名前そのもの（`freq_mhz` など）に
差し替わっていた＝軸に `freq_mhz` / `h_tx` / `h_rx` / `veg_h` を選ぶと、後ろに
並ぶ同名の固定列と**見出しが 2 回出る**（`csv.DictReader` やpandas は後勝ちで読む
ので、軸の値が静かに捨てられる）。
- **3.1 で 2 列目を固定名 `axis_value` にした**（値そのものは変えていない＝
  比較では条件名、スイープでは軸の値）。**軸の名前**（比較では空文字列）は
  末尾に追加した `axis` 列へ移した（規約 1＝追加は末尾のみ）。
- これで軸に何を選んでも見出しの重複は起きない。**古い予告の文言**（CHANGELOG
  3.0 節・公開文書）は歴史的記録として残し、いま在る列の説明だけを実態に直す。
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
    """

    filename: str
    row_meaning: str
    columns: tuple[str, ...]
    writer: str


# --- 個別シミュレーション ----------------------------------------------------
TERRAIN_CSV_COLUMNS: tuple[str, ...] = ("Distance_m", "Elevation_m")

# --- 複数経路（バッチ）------------------------------------------------------
# 🆕 `samples`（3.0a1 / I-069）＝**その回線で実際に地形を刻んだ点数**。利用者は
# 解像度の段階（高/中/低）を選び、点数は**経路の長さから 1 行ごとに解かれる**ように
# なったので、**同じ CSV の中で行によって違う**。⇒ これが無いと「どれだけ細かく見た
# 答えなのか」が成果物から復元できない（以前は共通設定の 1 つの数で全行同じだった）。
# 🔴 **間隔は列にしない**（B-150 で理由が変わった）＝以前は「`slant_m` と点数から
# **割れる導出値**だから」としていたが、**「高」「中」の標本はもう等間隔ではない**
# （DEM 画素の縁ごとに置く）ので、そもそも**点数から間隔は割り出せない**。効いている
# 刻み＝*1 画素の寸法*は帳票の「結果の取扱に関する補足」が名乗る。
# ⚠️ **`terrain_profile.csv` は階段状になる**＝1 つの画素につき縁が 2 行入り、
# **距離の丸め（0.1m）では同じ値の行が隣り合って見える**（標高は違う＝それが棚の縁）。
#
# 🆕 `f1_depth_x`（3.0a1 / I-077）は **末尾に足してある**（規約 1 の最初の実例）。
# `f1_pct` の隣に置くほうが読みやすいが、**既存列の位置を動かすと位置で読む相手が
# 静かに壊れる**＝読みやすさより互換を採る。3 本の CSV とも同じ扱いにした
# （`summary.csv` / `hops.csv` / `scenario.csv`。HTML 台帳のほうは位置の契約が
# 無いので `f1_pct` の隣に置いてある＝**人が読む面と機械が読む面で並びが違う**）。
#
# 🆕 `horiz_m`（3.1 / B-139）＝**水平距離**。`slant_m` は送受信のアンテナ高と
# 標高差を含む斜距離なので、`slant_m ÷ (samples − 1)` は実効間隔の近似にしか
# ならず（短距離・急峻な経路ほど誤差が大きい＝実測 +16.6%）、しかも成果物には
# 復元する手がかりが無かった。水平距離をそのまま列にすれば、その割り算をする
# かどうかは読む側の判断に戻せる。規約 1＝末尾に追加。
SUMMARY_CSV_COLUMNS: tuple[str, ...] = (
    "id", "status", "freq_mhz", "gain_tx_dbi", "gain_rx_dbi",
    "h_tx", "h_rx",
    "rx_dbm", "margin_db",
    "fspl_db", "diff_db", "veg_db", "env_db",
    "rain_db", "gas_db", "total_loss_db",
    "slant_m", "f1_pct", "note", "error",
    "f1_depth_x",
    "samples",
    "horiz_m",
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
# 🔁 **2 列目は 3.1 で `axis_value` に固定した**（I-112・3.0a1 で予告済み）。
# 以前はスイープで軸の名前（`freq_mhz` など）に差し替わり、同名の固定列と
# 見出しが 2 回出た。値の意味は変えていない（比較＝条件名／スイープ＝軸の値）。
# 軸の名前そのものは `axis` 列（末尾＝規約 1）に移した＝比較では空文字列。
SCENARIO_CSV_COLUMNS: tuple[str, ...] = (
    "label", "axis_value", "status",
    "rx_dbm", "margin_db", "total_loss_db",
    "fspl_db", "diff_db", "veg_db", "env_db", "rain_db", "gas_db",
    "f1_pct", "slant_m",
    "freq_mhz", "p_tx_dbm", "gain_tx_dbi", "gain_rx_dbi", "sens_dbm",
    "h_tx", "h_rx", "veg_h", "rain_mmh", "env_type", "diff_method",
    "f1_depth_x",
    "axis",
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
    ),
)
