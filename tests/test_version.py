"""版の字 → Windows の 4 数字バージョン（`core.version.version_tuple`）。

🔴 **このゲートが無かったから B-162 が出た**＝変換は `radiosim.spec` の中に在り、
spec は PyInstaller が exec するファイルなのでテストから import できない。
⇒ **どのゲートも見ていない場所に、配布物のメタデータを決める規則が置いてあった**。
規則を `core/version.py` へ移し、ここで段階の順序そのものを検査する。

⚠️ **見るのは「値」ではなく「順序」**＝定数を写した検査は、次に段階を増やした日に
素通りする（[[feedback-promote-recurring-checks]] の壊れ方③）。
"""
from core.version import APP_VERSION, version_tuple


def test_final_outranks_every_rc_of_the_same_version():
    """🔴 正式版は、同じ版のどの RC よりも数値で新しいこと（B-162 の本体）。

    以前は正式が `(3, 0, 0, 0)`・RC3 が `(3, 0, 0, 3)` で**逆走**していた。
    """
    final = version_tuple("3.0")
    for n in range(1, 10):
        assert version_tuple(f"3.0RC{n}") < final, (
            f"3.0RC{n} が正式の 3.0 以上に見える"
            f"（RC={version_tuple(f'3.0RC{n}')} / 正式={final}）")


def test_stages_are_ordered_within_one_version():
    """a → b → RC → 正式 の順に単調増加すること（配布しない段階も逆走させない）。"""
    seq = ["3.0a1", "3.0b1", "3.0RC1", "3.0RC2", "3.0"]
    vals = [version_tuple(v) for v in seq]
    assert vals == sorted(vals), f"段階の順序が壊れている: {list(zip(seq, vals))}"


def test_next_version_outranks_the_previous_final():
    """次の版の最初の RC は、前の版の正式より新しいこと（版をまたぐ順序）。"""
    assert version_tuple("3.0") < version_tuple("3.1RC1")
    assert version_tuple("2.9") < version_tuple("3.0")
    assert version_tuple("3.0") < version_tuple("3.0.1")


def test_patch_field_is_kept():
    """`X.Y.Z` の Z を落とさないこと（3 つ目の欄）。"""
    assert version_tuple("2.1.3")[:3] == (2, 1, 3)


def test_unparsable_falls_back_to_zero():
    """読めない字は `(0, 0, 0, 0)`＝ビルドを止めずに一番古い版として出す。"""
    assert version_tuple("バージョン不明") == (0, 0, 0, 0)


def test_current_version_is_representable():
    """いま名乗っている版が、4 数字へ落ちること（0 埋めの事故を検出する）。"""
    t = version_tuple(APP_VERSION)
    assert t != (0, 0, 0, 0), f"{APP_VERSION} が変換できていない"
    assert all(0 <= n <= 65535 for n in t), f"Windows の 16bit 欄に入らない: {t}"
