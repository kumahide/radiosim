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


# --- 条件探索の可変列 --------------------------------------------------------
def test_sweep_axis_replaces_only_the_second_column():
    base = oc.SCENARIO_CSV_COLUMNS
    got = oc.scenario_csv_columns("p_tx")
    assert got[1] == "p_tx", "スイープの軸が 2 列目に出ていない"
    assert got[:1] + got[2:] == base[:1] + base[2:], "軸以外の列まで動いている"
    assert oc.scenario_csv_columns(None) == base, "比較モードの既定名が変わっている"
    assert oc.scenario_csv_columns("") == base


def test_the_axis_column_can_collide_with_a_fixed_column():
    """軸名が固定列と同名になりうることを、契約として明文化しておく。

    ⚠️ **これは「直った」ではなく「そういう契約だ」を固定する検査**＝辞書で読む
    相手は後勝ちになる。直すときは列の改名＝1 版前の予告が要る（変更規約 2）。
    """
    from core import scenario as scn

    collide = sorted(set(scn.SWEEP_AXES) & set(oc.SCENARIO_CSV_COLUMNS))
    assert collide == ["freq_mhz", "h_rx", "h_tx", "veg_h"], (
        f"軸と固定列の重なりが変わった: {collide}。"
        "列を足す／軸を足すときは、この重なりが増えていないか見ること"
    )
    for axis in collide:
        cols = oc.scenario_csv_columns(axis)
        assert cols.count(axis) == 2, "重なりの形が変わった（契約の説明を見直すこと）"
