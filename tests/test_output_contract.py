"""
tests/test_output_contract.py
=============================
成果物 CSV の**列仕様＝出力契約**（`core/output_contract.py`）が、実装から浮かない
ことを守る。

守る型は 3 つ
-------------
1. **台帳が全部を数えている**＝`core/` `report/` の `csv.writer` 呼び出しのうち、
   台帳（`CSV_CONTRACTS`）にも許可リストにも無い書き手が居ないこと。
   ⚠️ **列挙で塞いだ穴は次に足す 1 本で開く**（→ [[feedback-user-examples-are-classes]]）
   ので、「台帳に書いた 4 本を見る」ではなく**実装側を数えてから引く**向きにする。
2. **見出しは契約から取っている**＝書き手が列を手書きしていないこと。
3. **値の数が列の数と合っている**＝行に値を足して見出しを足し忘れる形を止める。
   これは**手書きの見出しを禁じただけでは防げない**（見出しは契約から来るので
   静かに列がずれる）＝2 と 3 は別の欠陥を見ている。

⚠️ **空振りしないこと自体を検査する**（ゲートの壊れ方②「一度も落ちない」）＝
書き手が 1 つも見つからない／見出し行が読めない／値の並びが 1 つも拾えない場合は
**落とす**。書き方を変えたなら、このゲートの読み方も一緒に直す合図。
"""

import ast
from pathlib import Path

import pytest

from core import output_contract as oc

ROOT = Path(__file__).resolve().parent.parent

#: `def` の 2 形（同期・非同期）をまとめて指す別名。
_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef

#: 台帳に載せない `csv.writer` と、その理由（**増やすなら理由を書く**）。
_ALLOWED_UNREGISTERED = {
    "report.batch:export_csv":
        "入力 CSV＝アプリ自身が読み戻す交換フォーマットで、契約の向きが逆"
        "（単一ソースは `batch.CSV_COLUMNS`）。成果物ではないので出力契約の"
        "台帳には載せない（→ core/output_contract.py の冒頭）",
}


def _is_csv_writer(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("writer", "DictWriter")
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "csv"
    )


def _writer_functions() -> dict[str, _FuncDef]:
    """`csv.writer` を呼んでいる関数を `module:function` で集める（実装＝真実）。"""
    found: dict[str, _FuncDef] = {}
    for layer in ("core", "report"):
        for path in sorted((ROOT / layer).glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if any(_is_csv_writer(c) for c in ast.walk(node)):
                    found[f"{layer}.{path.stem}:{node.name}"] = node
    return found


WRITERS = _writer_functions()


def _writerow_calls(node) -> list[ast.Call]:
    return [
        n for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "writerow"
        and n.args
    ]


def _contract(writer: str) -> oc.CsvContract:
    return next(c for c in oc.CSV_CONTRACTS if c.writer == writer)


# --- 1. 台帳が全部を数えているか --------------------------------------------
def test_the_scanner_finds_the_writers_at_all():
    """このゲートが空振りしていないこと（書き方を変えたら読み方も直す合図）。"""
    assert len(WRITERS) >= len(oc.CSV_CONTRACTS), (
        "core/ report/ から csv.writer の呼び出しを台帳の数だけ見つけられていない"
        f"（見つけたのは {sorted(WRITERS)}）。書き方を変えたなら、この走査も直すこと"
    )


def test_every_csv_writer_is_registered():
    """台帳にも許可リストにも無い CSV の書き手が居ないこと。

    🔑 **新しい CSV を足した人が、契約を書くまでここが赤いまま**になる形にする。
    台帳の 4 本を見に行く向きだと、5 本目が生えても緑のまま気づけない。
    """
    unknown = sorted(
        set(WRITERS) - {c.writer for c in oc.CSV_CONTRACTS} - set(_ALLOWED_UNREGISTERED)
    )
    assert not unknown, (
        f"出力契約の台帳に無い CSV の書き手がある: {unknown}。"
        "`core/output_contract.py` の `CSV_CONTRACTS` に列仕様を足すこと"
        "（成果物でないなら `_ALLOWED_UNREGISTERED` に理由つきで）"
    )


def test_the_allow_list_still_points_at_something_real():
    """許可リストが実在の書き手を指していること（腐った免除を残さない）。"""
    stale = sorted(set(_ALLOWED_UNREGISTERED) - set(WRITERS))
    assert not stale, f"許可リストが指す書き手がもう居ない: {stale}"


@pytest.mark.parametrize("contract", oc.CSV_CONTRACTS, ids=lambda c: c.filename)
def test_registered_writer_exists_and_writes_that_file(contract):
    assert contract.writer in WRITERS, (
        f"{contract.filename}: 台帳が指す書き手 {contract.writer} が見つからない"
        "（改名・移動したなら台帳も直すこと）"
    )
    src = ast.unparse(WRITERS[contract.writer])
    assert f"'{contract.filename}'" in src or f'"{contract.filename}"' in src, (
        f"{contract.writer} が書いているファイル名が台帳の {contract.filename} と違う"
    )


def test_contract_filenames_are_unique():
    names = [c.filename for c in oc.CSV_CONTRACTS]
    assert len(names) == len(set(names)), f"同じファイル名の契約が 2 つある: {names}"


# --- 2. 見出しは契約から取っているか ----------------------------------------
@pytest.mark.parametrize("contract", oc.CSV_CONTRACTS, ids=lambda c: c.filename)
def test_header_row_comes_from_the_contract(contract):
    """見出し行が `output_contract` 由来で、列の手書きが復活していないこと。"""
    calls = _writerow_calls(WRITERS[contract.writer])
    assert calls, f"{contract.writer}: writerow が 1 つも見つからない（このゲートが空振りする）"
    header = ast.unparse(calls[0].args[0])
    assert not isinstance(calls[0].args[0], (ast.List, ast.Tuple)), (
        f"{contract.writer}: 見出し行に列を手書きしている（{header}）。"
        "`core/output_contract.py` から取ること"
    )
    assert "output_contract" in header, (
        f"{contract.writer}: 見出し行が出力契約を参照していない（{header}）"
    )


# --- 3. 値の数が列の数と合っているか ----------------------------------------
@pytest.mark.parametrize("contract", oc.CSV_CONTRACTS, ids=lambda c: c.filename)
def test_data_rows_have_one_value_per_column(contract):
    """行に書く値の数が、契約の列数と一致すること。

    🔴 **見出しを契約から取るようにしただけでは、この欠陥は防げない**＝値を 1 つ
    足しても見出しは正しいまま出るので、**列が 1 つずれた CSV が黙って出る**
    （読む側は名前で読んでいるので、ずれた先の列の値を正しい名前で受け取る）。
    """
    rows = [
        (c, c.args[0]) for c in _writerow_calls(WRITERS[contract.writer])[1:]
        if isinstance(c.args[0], (ast.List, ast.Tuple))
    ]
    assert rows, (
        f"{contract.writer}: 値の並びを 1 つも拾えない（このゲートが空振りする）。"
        "行の組み立て方を変えたなら、この走査も直すこと"
    )
    want = len(contract.columns)
    for call, values in rows:
        got = len(values.elts)
        assert got == want, (
            f"{contract.writer} ({call.lineno} 行目): 値 {got} 個に対し列は {want} 個"
            f"＝{contract.filename} の列がずれる"
        )


# --- 条件探索の軸列（I-112・3.1 で改名済み）---------------------------------
def test_scenario_axis_value_column_is_fixed():
    """2 列目は `axis_value` に固定され、軸を何に選んでも動かないこと。

    以前は 2 列目がスイープ軸の名前そのもの（`freq_mhz` など）に差し替わり、
    後ろの同名固定列と見出しが重複した（I-112）。3.1 でこの可変性を無くした。
    """
    assert oc.SCENARIO_CSV_COLUMNS[1] == "axis_value"


def test_scenario_axis_name_column_is_appended_at_the_tail():
    """軸の名前そのものは末尾の `axis` 列が持つこと（規約 1＝追加は末尾のみ）。"""
    assert oc.SCENARIO_CSV_COLUMNS[-1] == "axis"


def test_scenario_columns_no_longer_collide():
    """`axis_value` が固定名になった以上、軸を選んでも見出しの重複が起きないこと。

    ⚠️ 固定列そのものに `freq_mhz` 等が**残っている**のは意図どおり（その条件で
    使った値の列）＝ここで見るのは「同じ名前が 2 回出るか」であって、軸名と
    同じ字の列が存在すること自体ではない。
    """
    names = list(oc.SCENARIO_CSV_COLUMNS)
    assert len(names) == len(set(names)), (
        f"scenario.csv の列見出しに重複がある: {names}"
    )


# --- 水平距離（B-139・3.1 で列追加）------------------------------------------
def test_summary_has_horizontal_distance_column_at_the_tail():
    """`slant_m`（斜距離）だけでは実効間隔が復元できない問題への対処。

    `slant_m` は送受信のアンテナ高と標高差を含む斜距離なので、
    `slant_m ÷ (samples − 1)` は実効間隔の近似にしかならない（短距離・急峻な
    経路ほど誤差が大きい）。水平距離を列として持てば、読む側がその近似計算を
    するかどうかを選べる。規約 1＝末尾に追加。
    """
    assert oc.SUMMARY_CSV_COLUMNS[-1] == "horiz_m"
