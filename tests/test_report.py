"""
tests/test_report.py
====================
レポート出力層（report_common / report_path / report_summary・ヘッドレス）の
KML / PNG / 連結文書のユニットテスト。

save_path_kml / save_summary_kml は Google Earth に渡す成果物そのものだが、
従来はカバレッジ計測外で退行を止める仕掛けが無かった。ネットワーク・GUI 無しの
純粋なファイル出力なので、well-formed XML と KML の座標順序（lon,lat,alt）という
壊れやすい不変条件をここで守る。PNG/HTML 経路は render_path_map_b64 を
monkeypatch してネットワーク無しでスモークする。

HTML レポートの内容検証（地図埋め込み・座標表記）は test_batch.py 側が担う。
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

from core import models
from report import report_path

_KML_NS = "{http://www.opengis.net/kml/2.2}"


def _css_px(css: str, selector: str, prop: str, index: int = 0) -> float:
    """`selector` の宣言ブロックから `prop` の px 値を読む（`index`＝何個目の数）。

    ⚠️ **CSS を読む側でも「書いてあるはず」を仮定しない**＝プロパティが消えたら
    KeyError で落ちる（既定値で補うと、消えたことが見えないまま予算だけ通る）。
    """
    body = css.split(selector, 1)
    assert len(body) == 2, f"セレクタが無い: {selector}"
    rule = body[1].split("}", 1)[0]
    for decl in rule.replace("\n", " ").split(";"):
        name, _, value = decl.partition(":")
        if name.strip() == prop:
            nums = re.findall(r"(-?[\d.]+)px", value)
            assert len(nums) > index, f"{selector} {prop}: px が足りない（{value}）"
            return float(nums[index])
    raise KeyError(f"{selector} に {prop} が無い")


# Arial のおよその送り幅（em 比）。**上振れ側**に取る＝近似で「収まる」と誤判定
# しないため。全角（CJK・全角記号）は 1em。
_EM_UPPER, _EM_OTHER, _EM_THIN = 0.75, 0.62, 0.30


def _text_px(text: str, font_px: float) -> float:
    """`font_px` で描いたときの `text` のおよその幅（px・上振れ側の見積り）。"""
    total = 0.0
    for ch in text:
        if ord(ch) > 0x2E7F:            # CJK バー（かな・漢字・全角記号）＝1em
            total += 1.0
        elif ch in " .,()":
            total += _EM_THIN
        elif ch.isupper():
            total += _EM_UPPER
        else:
            total += _EM_OTHER
    return total * font_px


def _make_result(status: str = "OK") -> models.LinkBudgetResult:
    return models.LinkBudgetResult(
        eirp=23.0, fspl=100.0, diff_loss=0.0, veg_loss=0.0,
        env_loss=6.0, rain_loss=0.0, gas_loss=0.0,
        total_loss=106.0, p_rx=-83.0,
        actual_margin=2.0, status=status,
        blocked_ratio=0.0, slant_dist_km=1.0,
        diff_method="single", env_type="los",
    )


# ============================================================
# _find_obs_segments / _kml_line_coords（純関数）
# ============================================================
class TestFindObsSegments:

    def test_empty_mask(self):
        assert report_path._find_obs_segments(np.array([], dtype=bool)) == []

    def test_all_false(self):
        assert report_path._find_obs_segments(np.zeros(5, dtype=bool)) == []

    def test_all_true_is_single_segment(self):
        assert report_path._find_obs_segments(np.ones(4, dtype=bool)) == [(0, 3)]

    def test_multiple_segments_inclusive_ends(self):
        mask = np.array([False, True, True, False, True], dtype=bool)
        assert report_path._find_obs_segments(mask) == [(1, 2), (4, 4)]


class TestKmlLineCoords:

    def test_lon_lat_alt_order(self):
        """KML の座標は lon,lat,alt 順（lat,lon に入れ替わる退行を止める）。"""
        out = report_path._kml_line_coords(
            np.array([34.5429]), np.array([132.4118]), np.array([10.0])
        )
        assert out.strip() == "132.411800,34.542900,10.0"

    def test_one_line_per_sample(self):
        out = report_path._kml_line_coords(
            np.array([34.0, 34.1, 34.2]),
            np.array([132.0, 132.1, 132.2]),
            np.array([0.0, 1.0, 2.0]),
        )
        assert len(out.splitlines()) == 3


# ============================================================
# 断片と文書の分離（report_common）＝ CSS スコープの不変条件
# ============================================================
def _selectors(css: str) -> list[str]:
    """CSS からセレクタだけを抜き出す（@media ブロックは中身を見る）。"""
    body = re.sub(r"@media[^{]*\{", "", css)      # メディアクエリの殻を外す
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)   # コメント除去
    sels: list[str] = []
    for chunk in re.findall(r"([^{}]*)\{[^{}]*\}", body):
        sel = chunk.strip().splitlines()[-1].strip() if chunk.strip() else ""
        if sel and not sel.startswith("@"):
            sels.extend(s.strip() for s in sel.split(",") if s.strip())
    return sels


# ============================================================
# 「結果の取扱に関する補足」セクション（3.0a1 / ロードマップ §3.0 の 9）
# ============================================================


def _builds_a_sheet(text: str) -> bool:
    """そのソースが**帳票の 1 面を組み立てているか**（自己同定フッタを置くか）。

    面を列挙せず**実装側の印**で数える＝新しい帳票を足しても、フッタを置いた
    時点でこのゲートの対象に入る（→ [[feedback-user-examples-are-classes]]）。
    """
    return "report_common.page_footer(" in text


def _carries_the_handling_section(text: str) -> bool:
    """そのソースが「結果の取扱に関する補足」を出しているか。

    ⚠️ **B-219 で `handling_notes_html` → `handling_section_html` に改名**
    （感度の変動幅を同じセクションへ統合したため）。
    """
    return ("handling_section_html(" in text) or ("handling_text(" in text)


class TestEveryArtifactFaceCarriesTheHandlingSection:
    """**帳票を組み立てる面は、必ず開示のセクションも出す**（3.0a1 のクラス点検）。

    🔑 開示を書く仕事には「無いことの検査」を対で置く
    （→ [[feedback-promote-recurring-checks]]）＝*ここまでは大丈夫*という主張は、
    反例 1 つで嘘になる。⇒ **セクションを持たない帳票が 1 面でもあれば落とす。**
    """

    @staticmethod
    def _sources():
        root = os.path.join(os.path.dirname(__file__), "..")
        out = []
        for layer in ("core", "report", "webui"):
            d = os.path.join(root, layer)
            for name in sorted(os.listdir(d)):
                if not name.endswith(".py") or name == "report_common.py":
                    continue
                path = os.path.join(d, name)
                out.append((layer + "/" + name,
                            open(path, encoding="utf-8").read()))
        return out

    def test_the_scan_finds_the_faces_at_all(self):
        """ゲートが空振りしていないこと＝走査する層のファイルが見えているか。

        A4 の帳票（4 種）は Pro へ移した（Link 1.0 ステージ11b）＝公開側の面は 0 で
        正しい。ここでの縛りは「公開側に帳票を足すなら開示も一緒に」の見張り。
        4 種が見えていることは Pro 側の同名の試験が確かめる。
        """
        names = [n for n, _t in self._sources()]
        assert "report/report_common.py" not in names
        assert "core/simulation.py" in names and "report/report_path.py" in names, (
            "走査する層が見えていない: " + repr(names)
        )

    def test_no_sheet_is_published_without_it(self):
        offenders = [n for n, t in self._sources()
                     if _builds_a_sheet(t) and not _carries_the_handling_section(t)]
        assert not offenders, (
            "開示のセクションを持たない帳票がある: " + repr(offenders) + "。"
            "`report_common.handling_section_html(models.scope_notes(...))` を"
            "フッタの前に置くこと（3.0a1）＝成果物は一人歩きするので、"
            "前提と適用範囲は帳票そのものが持つ"
        )

    def test_the_plain_text_report_carries_it_too(self):
        """`report.txt` は HTML と別の書き手なので、別に見る。"""
        root = os.path.join(os.path.dirname(__file__), "..")
        text = open(os.path.join(root, "core", "simulation.py"),
                    encoding="utf-8").read()
        assert _carries_the_handling_section(text), (
            "report.txt だけ開示を持たない（HTML の帳票と同じ 1 本を引くこと）"
        )

    @pytest.mark.parametrize("text,expected", [
        ("report_common.page_footer(i18n.t('x'))", (True, False)),
        ("report_common.page_footer(x) + handling_section_html(y)", (True, True)),
        ("disclosure.handling_text(keys)", (False, True)),
        ("# フッタは page_footer が置く", (False, False)),
    ])
    def test_the_detector_catches_what_it_claims(self, text, expected):
        assert (_builds_a_sheet(text), _carries_the_handling_section(text)) == expected


# ============================================================
# 成果物の文言が「○○＝△△」の説明形を使っていないこと（2026-09-17・ユーザー指摘）
# ------------------------------------------------------------
# 画面文言（i18n の ja 値）は `test_i18n_glossary.py`（I-150）が既に締め出しているが、
# `report/*.py` はレポート自身が組み立てる文字列リテラル（見出し・注記・凡例）も
# 持っており、そちらは i18n を経由しないので上の検査の外にある。
#
# 🔑 **docstring は対象外**＝関数・クラス・モジュールの説明文は開発者向けの散文で、
# 成果物には出ない。AST で「そのブロックの最初の式文である文字列定数」だけを
# docstring として除外し、残りの文字列リテラルだけを見る。
import ast as _ast


def _docstring_node_ids(tree: _ast.AST) -> set:
    """モジュール/関数/クラスの docstring として使われている Constant ノードの id 集合。"""
    ids = set()
    candidates = [tree] + [n for n in _ast.walk(tree)
                            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                                               _ast.ClassDef))]
    for node in candidates:
        body = getattr(node, "body", None)
        if body and isinstance(body[0], _ast.Expr) \
                and isinstance(body[0].value, _ast.Constant) \
                and isinstance(body[0].value.value, str):
            ids.add(id(body[0].value))
    return ids


def _non_docstring_literals(path: Path) -> list[str]:
    """そのファイルの、docstring ではない文字列リテラルの一覧。"""
    tree = _ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstring_node_ids(tree)
    return [n.value for n in _ast.walk(tree)
            if isinstance(n, _ast.Constant) and isinstance(n.value, str)
            and id(n) not in skip]


REPORT_PY_FILES = sorted((Path(__file__).resolve().parent.parent / "report").glob("*.py"))


def _strip_css_comments(s: str) -> str:
    """CSS の `/* ... */` を落とす。

    CSS の中の `/* */` は Python の `#` と同じ**開発者向けコメント**で、
    ブラウザは描画しない＝利用者の目に触れる「成果物の文言」ではない。
    ここを見ると、レイアウトの設計判断メモ（多くが「＝」で書かれている）を
    大量の偽陽性として拾ってしまう。
    """
    return re.sub(r"/\*.*?\*/", "", s, flags=re.S)


@pytest.mark.parametrize("path", REPORT_PY_FILES, ids=lambda p: p.name)
def test_report_output_strings_never_use_the_equals_explanation_form(path):
    """`report/*.py` の文字列リテラル（docstring・CSS コメントを除く）が
    「○○＝△△」の説明形を使っていないこと（I-150 の成果物版）。"""
    offenders = [s for s in _non_docstring_literals(path)
                 if "＝" in _strip_css_comments(s)]
    assert not offenders, (
        f"{path.name}: 成果物の文字列に「○○＝△△」の説明形が残っている"
        "（自然な日本語の文へ書き直すこと）: " + ", ".join(offenders)
    )


