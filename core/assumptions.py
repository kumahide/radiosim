"""
core/assumptions.py
===================
**式の前提（適用範囲）を、式の隣にデータで宣言するための器**（I-239 の 2・3）。
純データのみ・副作用ゼロ・標準ライブラリだけに依存する。

壊れた不変条件
--------------
「**式は、前提が成り立つ入力にだけ値を出す。成り立たなければ値を出さず、開示に戻す**」。
B-338 は、ドローンの場面で机上検証した式を固定回線へ移すとき、前提（直接波が届く）を
docstring の一語に預けたまま、入口でも刻印でも扱わなかった。⇒ 前提を**名前のある行**に
して式のモジュールに置き、「どう扱っているか」を 3 値のどれかで言わせる。

使い方
------
式を持つモジュールは `ASSUMPTIONS: tuple[Assumption, ...]` を置く。式を持たない
モジュールは下の `FORMULA_FREE` に理由つきで載せる。`core/` のどのモジュールも
**どちらか一方だけ**＝新しいモジュールを足すと、決めるまで `tests/test_assumptions.py`
が赤になる（閉じた世界のゲート）。宣言が名指す関数・定数・刻印のキー・テスト・課題 ID の
実在も同じテストが見る＝書けば緑の飾りにしない。
"""
from dataclasses import dataclass

#: 入口で確かめて、外れた入力には値を出さない（`enforced_by`＝そのモジュールの関数か定数の名前）。
GUARD: str = "guard"
#: 値は出すが、刻印か画面で前提を名乗る（`enforced_by`＝`"scope:<刻印のキー>"` か、
#: 名乗りを決めるそのモジュールの関数の名前）。
DISCLOSED: str = "disclosed"
#: どちらも無い＝未対応の課題として持つ（`issue` 必須・`enforced_by` は空）。
OPEN: str = "open"

HANDLINGS: tuple[str, ...] = (GUARD, DISCLOSED, OPEN)

#: `enforced_by` のうち、刻印（`models.SCOPE_NOTE_ORDER`）のキーを指す接頭辞。
SCOPE_PREFIX: str = "scope:"


@dataclass(frozen=True)
class Assumption:
    """式の前提 1 つ。"""
    name:        str                     # モジュール内で一意（英小文字と `_`）
    description: str                     # 日本語の説明 1 文
    handling:    str                     # `HANDLINGS` のどれか
    enforced_by: tuple[str, ...] = ()    # 確かめる／名乗る口（`GUARD`・`DISCLOSED` は 1 つ以上）
    test:        str = ""                # 前提を外した入力を流すテスト（`tests/x.py::Class::func`）
    issue:       str = ""                # 課題 ID（`OPEN` は必須）
    origin:      str = ""                # `experiments/` から移した式の元の場面（1 行）


#: 式を持たないモジュール（モジュール名 → 理由）。式を持つなら `ASSUMPTIONS` を置き、ここから外す。
FORMULA_FREE: dict[str, str] = {
    "assumptions":      "この器そのもの（純データ）",
    "batch_csv_schema": "入力 CSV の列の契約（純データ）",
    "config":           "設定の読み書きと入力の検証・ロガー",
    "coords":           "座標の表記の変換だけ（DD⇔DMS）",
    "dem_cache":        "タイルのキャッシュの棚卸し・削除（I/O）",
    "dem_prefetch":     "面での事前取得（I/O）＝標高の復号は `dem` が持つ",
    "dem_sources":      "DEM のソースの宣言（URL・層・復号の型）＝復号の式は `dem` が持つ",
    "diagnostics":      "診断の ZIP をまとめる（I/O）",
    "disclosure":       "刻印の文を組む（前提は各式の `ASSUMPTIONS` と `models.scope_notes`）",
    "env_facts":        "実行環境の事実を集める（I/O）",
    "failure":          "失敗の伝え方の型",
    "i18n":             "画面の文字列の翻訳表",
    "output_contract":  "成果物 CSV の列の契約（純データ）",
    "runtime_env":      "実行環境が宣言どおりかを答える",
    "scenario":         "既存の式を条件を変えて呼ぶだけ（前提は呼ばれる式の側）",
    "simulation":       "既存の式を組み合わせて呼ぶだけ（前提は呼ばれる式の側）",
    "tile_sources":     "背景地図のタイルの宣言（純データ）",
    "units":            "数値の表記の変換だけ（距離・遮蔽率）",
    "update_check":     "新しい版の問い合わせ（I/O）",
    "version":          "版と名前の単一ソース",
}
