"""
tests/test_bundle_imports.py
============================
**同梱漏れゲート自身のゲート**（`buildtools/check_bundle_imports.py`）。

**なぜこのテストが要るか**（2026-08-03・独立レビュー Codex 指摘 P1）: あの
スクリプトは B-036（2.6RC1 が `No module named 'timeit'` で図を 1 枚も描けなかった）
の**再発を止める唯一の判定器**なのに、作った直後は手で 1 回動かしただけだった。
**PyInstaller の warn レポートの書式が変われば、パーサは何も拾わずに「問題なし」を
返して静かに通す**（ゲートの壊れ方②の裏返し＝*一度も落ちなくなる*）。判定器は
判定器で固定しないと、守っているつもりだけが残る。

**`_REAL_RC1_LINE` は実物**＝2.6RC1 のビルドが吐いた `warn-radiosim.txt` からの
逐語コピー。ここを実物にしておくことが、このテストの価値のほぼ全部
（自分で作った書式に対して自分のパーサを当てても、書式ずれは検出できない）。
"""

import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "buildtools",
                       "check_bundle_imports.py")


def _load():
    """スクリプトをモジュールとして読み込む（`buildtools` はパッケージではない）。"""
    spec = importlib.util.spec_from_file_location("check_bundle_imports", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = _load()

# ⚠️ 実物（2.6RC1 のビルドが出力した warn-radiosim.txt からの逐語コピー）。
# これが RC1 を落とせなかったら、ゲートは存在しないのと同じ。
_REAL_RC1_LINE = (
    "excluded module named timeit - imported by fontTools.misc.loggingTools (top-level)"
)
# 同じレポートに実在した、**鳴らしてはいけない**行。
_REAL_CONDITIONAL_LINE = (
    "excluded module named doctest - imported by fontTools.misc.loggingTools "
    "(conditional), fontTools.misc.textTools (conditional)"
)
_REAL_MISSING_LINE = (
    "missing module named 'collections.abc' - imported by tracemalloc (top-level), "
    "typing (top-level), logging (top-level)"
)
_REAL_ALLOWED_LINE = (
    "excluded module named cryptography - imported by urllib3.contrib.pyopenssl (top-level)"
)


class TestFindFatalExclusions:
    def test_catches_the_real_2_6rc1_regression(self):
        """RC1 を実際に落とせること（このゲートの存在理由そのもの）。"""
        fatal = check.find_fatal_exclusions(_REAL_RC1_LINE)
        assert [name for name, _ in fatal] == ["timeit"]
        assert fatal[0][1] == ["fontTools.misc.loggingTools (top-level)"]

    def test_conditional_importers_do_not_fire(self):
        """`(conditional)` は実行時に到達しない＝鳴らせば「毎回鳴る門」になる。"""
        assert check.find_fatal_exclusions(_REAL_CONDITIONAL_LINE) == []

    def test_missing_module_lines_do_not_fire(self):
        """`missing module` は上流の任意依存で大量に出るので対象外。"""
        assert check.find_fatal_exclusions(_REAL_MISSING_LINE) == []

    def test_allowlisted_pairs_do_not_fire(self):
        assert check.find_fatal_exclusions(_REAL_ALLOWED_LINE) == []

    def test_allowlist_is_keyed_by_importer_not_module_name(self):
        """許可は (モジュール, import 元) の組。**別の誰か**が同じものを引いたら鳴る。"""
        line = ("excluded module named cryptography - imported by "
                "our_own_module (top-level)")
        assert [n for n, _ in check.find_fatal_exclusions(line)] == ["cryptography"]

    def test_reports_only_the_top_level_importers(self):
        """同じ行に top-level と conditional が混在しても、報告は top-level だけ。"""
        line = ("excluded module named timeit - imported by a.b (conditional), "
                "c.d (top-level), e.f (delayed)")
        fatal = check.find_fatal_exclusions(line)
        assert fatal[0][1] == ["c.d (top-level)"]

    def test_every_allowlist_entry_carries_a_reason(self):
        """理由の書けない許可を足させない（許可リストは膨らませない）。"""
        for key, reason in check._ALLOW.items():
            assert reason.strip(), f"理由の無い許可がある: {key}"

    def test_a_clean_report_is_clean(self):
        assert check.find_fatal_exclusions(
            "missing module named foo - imported by bar (top-level)\n"
            "some unrelated line\n"
        ) == []


class TestMain:
    def test_exit_1_when_the_report_names_a_fatal_exclusion(self, tmp_path, capsys):
        p = tmp_path / "warn-radiosim.txt"
        p.write_text(_REAL_RC1_LINE, encoding="utf-8")
        assert check.main(["check_bundle_imports.py", str(p)]) == 1
        assert "timeit" in capsys.readouterr().err

    def test_exit_0_on_a_clean_report(self, tmp_path):
        p = tmp_path / "warn-radiosim.txt"
        p.write_text(_REAL_CONDITIONAL_LINE + "\n" + _REAL_MISSING_LINE, encoding="utf-8")
        assert check.main(["check_bundle_imports.py", str(p)]) == 0

    def test_missing_report_is_a_failure_not_a_pass(self, tmp_path):
        """レポートが無いのは「検査していない」＝黙って通さない。

        ここを 0 で返すと、レポートの出力場所が変わった日にゲートが**静かに
        消える**（[[feedback-promote-recurring-checks]] の壊れ方①）。
        """
        assert check.main(["check_bundle_imports.py",
                           str(tmp_path / "nope.txt")]) == 1

    def test_bad_usage_is_reported(self):
        assert check.main(["check_bundle_imports.py"]) == 2


@pytest.mark.parametrize("line", [_REAL_RC1_LINE, _REAL_CONDITIONAL_LINE,
                                  _REAL_MISSING_LINE, _REAL_ALLOWED_LINE])
def test_real_lines_are_parsed_or_ignored_but_never_crash(line):
    """実物の書式を食わせてもパーサが落ちないこと（ビルドを巻き添えにしない）。"""
    check.find_fatal_exclusions(line)
