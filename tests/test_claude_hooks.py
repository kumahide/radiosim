"""
tests/test_claude_hooks.py
==========================
`.claude/` のローカルフック（Claude Code 用の道具）の検証。

**なぜ必要か**: `.claude/` は Python 800 行超あるのに**テストが 1 件も無く**、しかも
全ファイルが `.gitignore` 対象で CI のチェックアウトに存在しない。さらに各フックは
「例外は握りつぶして常に exit 0」（セッション開始/終了を妨げないため）という設計なので、
**壊れても誰も気づかない**のが既定の失敗モードだった。実際 2026-07-25 の 1 セッションで
`session_context.py` から**項目が黙って注入から落ちる欠陥が 2 件**見つかっている：

1. 状態トークンに註釈が付く書き方（`未着手（2.5 前段スライス）`）で正規表現が括弧ごと
   飲み込み、`OPEN_STATES` と一致せず落ちていた（I-009 / I-011 が長期間不可視）。
2. 「ID 000 は記入例テンプレ」として `-000` を除外していたが、記入例の見出しは
   `B-0XX` / `I-0XX`（数字でない）で元々一致せず、**実在の I-000 だけを消していた**。

どちらも「出力の件数を数える」以外に発見手段が無い形＝散文の注意書きでは守れない。
ここを Tier-0（テスト）へ落とすのがこのファイルの役割。

**CI では skip される**（対象が git-ignore で存在しないため）。実効性はローカルの
pytest ＝ Stop フックの決定論ゲート（`tools/qa-hook/gate.mjs`）が毎回全スイートを
走らせることで担保する。テスト本体は追跡され、対象だけが非追跡という構成。
"""

import importlib.util
import io
import json
import os
import pathlib
import sys

import pytest

from conftest import structural_skip

_HOOK_DIR = os.path.join(os.path.dirname(__file__), "..", ".claude")
_HOOK_PATH = os.path.abspath(os.path.join(_HOOK_DIR, "session_context.py"))

pytestmark = pytest.mark.skipif(
    not os.path.exists(_HOOK_PATH),
    reason=structural_skip(".claude/ は git-ignore（CI には存在しない）。ローカルのみ検証する。"),
)


def _load_hook():
    """`.claude/session_context.py` を単体モジュールとして読み込む。

    `.claude` はパッケージでなく import 可能なパス上にも無いので spec から直接ロードする。
    """
    spec = importlib.util.spec_from_file_location("_session_context", _HOOK_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_session_context"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hook():
    return _load_hook()


def _doc(*blocks: str) -> list[str]:
    """課題台帳の断片から行列を作る（parse_issues は純関数なのでファイル不要）。"""
    return "\n".join(blocks).splitlines()


_ARCHIVE_HEAD = "## ✅ 確認済み・対応済み（アーカイブ）"


def _item(iid: str, state: str, title: str = "タイトル", resp: str = "") -> str:
    body = f"### ★ {iid}: {title}\n\n- ★ **状態**: {state}\n"
    if resp:
        body += f"- **対応**: {resp}\n"
    return body


# ============================================================
# 未対応項目の抽出（欠陥 1・2 の回帰ガード）
# ============================================================
class TestOpenItems:
    @pytest.mark.parametrize("state", ["未着手", "対応中", "保留"])
    def test_open_states_are_listed(self, hook, state):
        items, _, _ = hook.parse_issues(_doc(_item("B-001", state)))
        assert len(items) == 1 and items[0].startswith(f"B-001({state})")

    @pytest.mark.parametrize("state", ["済", "却下"])
    def test_closed_states_are_not_listed(self, hook, state):
        items, _, _ = hook.parse_issues(_doc(_item("B-001", state, resp="`abc1234`")))
        assert items == []

    @pytest.mark.parametrize("written", [
        "未着手",
        "未着手（2.5 前段スライス＝A-1/A-2 の着手門を閉じる前に実施）",  # 欠陥1
        "`未着手`",
        "保留（設計判断待ち）",                                          # 欠陥1
        "未着手 （全角スペース前置き）",
    ])
    def test_state_annotations_do_not_hide_the_item(self, hook, written):
        """状態トークンの後ろに註釈・バッククォートが付いても拾うこと。

        これを取りこぼすと項目が**警告もなく**注入から消える（I-009/I-011 の実害）。
        """
        items, _, _ = hook.parse_issues(_doc(_item("I-009", written)))
        assert len(items) == 1, f"{written!r} を状態として認識できていない"

    def test_full_width_colon_is_accepted(self, hook):
        items, _, _ = hook.parse_issues(_doc("### ★ B-001: t\n\n- ★ **状態**：未着手\n"))
        assert len(items) == 1

    @pytest.mark.parametrize("iid", ["I-000", "B-000"])
    def test_id_000_is_a_real_item_not_a_template(self, hook, iid):
        """ID 000 を除外してはいけない（欠陥2）。記入例は `B-0XX` で数字ではない。"""
        items, _, _ = hook.parse_issues(_doc(_item(iid, "保留")))
        assert len(items) == 1 and items[0].startswith(iid)

    def test_template_placeholder_is_ignored(self, hook):
        """記入例テンプレ（`B-0XX`/`I-0XX`）は項目として数えないこと。"""
        items, _, _ = hook.parse_issues(_doc(
            "### ★ B-0XX: （症状を一言で）\n\n- ★ **状態**: 未着手 / 対応中 / 済\n"
        ))
        assert items == []

    def test_long_titles_are_truncated_but_id_and_state_survive(self, hook):
        items, _, _ = hook.parse_issues(_doc(_item("B-001", "未着手", "あ" * 80)))
        assert items[0].startswith("B-001(未着手) ") and items[0].endswith("…")

    def test_multiple_items_keep_document_order(self, hook):
        items, _, _ = hook.parse_issues(_doc(
            _item("B-013", "未着手"), _item("B-012", "対応中"), _item("I-011", "保留"),
        ))
        assert [i.split("(")[0] for i in items] == ["B-013", "B-012", "I-011"]

    def test_missing_state_line_does_not_leak_into_the_next_item(self, hook):
        """状態欄が無い項目の直後の項目を取り違えないこと。"""
        items, _, _ = hook.parse_issues(_doc(
            "### ★ B-001: 状態欄なし\n\n- **対象**: UI\n",
            _item("B-002", "未着手"),
        ))
        assert [i.split("(")[0] for i in items] == ["B-002"]


# ============================================================
# 済のアーカイブ節への移動（規則がコメントだけで守られなかった件のガード）
# ============================================================
class TestArchivePlacement:
    def test_done_outside_archive_is_flagged(self, hook):
        _, stale, _ = hook.parse_issues(_doc(
            "## 🐞 バグ", _item("B-004", "済", resp="2.4a1 / `abc1234`"), _ARCHIVE_HEAD,
        ))
        assert stale == ["B-004"]

    def test_done_inside_archive_is_not_flagged(self, hook):
        _, stale, _ = hook.parse_issues(_doc(
            "## 🐞 バグ", _item("B-013", "未着手"),
            _ARCHIVE_HEAD, _item("B-004", "済", resp="2.4a1 / `abc1234`"),
        ))
        assert stale == []

    def test_open_items_inside_archive_are_still_listed(self, hook):
        """アーカイブ節に未対応が紛れていても見落とさない（置き場より状態を優先）。"""
        items, stale, _ = hook.parse_issues(_doc(
            _ARCHIVE_HEAD, _item("B-004", "未着手"),
        ))
        assert len(items) == 1 and stale == []

    # --- 置き場の規則の**逆向き**（2026-08-12・I-016 で実際に踏んだ）------------
    #
    # 🔴 **規則は双方向なのに、検査は片方しか無かった。** 上の 3 本が見るのは
    # 「済なのに本文節」だけで、「未対応なのにアーカイブ節」は誰も見ていない。
    # ⇒ 実際に **I-016 がアーカイブ節の中で「未着手」のまま**置かれていた（2.8 へ
    # 復活させたとき、状態欄だけ直して物理移動が漏れた）。⚠️ **見つけたのは偶然**＝
    # 版割りの突き合わせ（I-085）が台帳側を 1 件少なく数えたので気づいた。

    def test_open_item_inside_archive_is_flagged_as_misplaced(self, hook):
        """未対応がアーカイブ節にあれば鳴ること（I-016 の形）。"""
        assert hook.misplaced_open_items(_doc(
            "## 🐞 バグ", _item("B-013", "未着手"),
            _ARCHIVE_HEAD, _item("I-016", "未着手"),
        )) == ["I-016"]

    def test_open_item_in_the_body_is_not_flagged(self, hook):
        """本文節の未対応は正しい置き場＝鳴らないこと。"""
        assert hook.misplaced_open_items(_doc(
            "## 💡 改善案", _item("I-016", "未着手"), _ARCHIVE_HEAD,
        )) == []

    def test_done_inside_archive_is_not_misplaced(self, hook):
        """済がアーカイブ節にあるのは正しい＝鳴らないこと（毎回鳴る網にしない）。"""
        assert hook.misplaced_open_items(_doc(
            _ARCHIVE_HEAD, _item("B-004", "済", resp="2.4a1 / `abc1234`"),
        )) == []

    def test_misplaced_reads_the_same_state_words_as_the_forward_check(self, hook):
        """状態語の読み方が順方向と揃っていること（強調・註釈つきでも拾う）。

        ⚠️ ここが揃っていないと、**同じ 1 つの規則が方向によって別の基準になる**。
        実データの状態欄は `**未着手**（✅ 2.8 確定＝…）` の形が普通。
        """
        assert hook.misplaced_open_items(_doc(
            _ARCHIVE_HEAD,
            _item("I-016", "**未着手**（✅ 2.8 確定＝2026-08-12 ユーザー決定）"),
        )) == ["I-016"]

    # --- 状態語の読み取り（2026-08-05・このゲートが「一度も落ちなかった」件）-----
    #
    # 実データの状態欄は強調・註釈・同義語が付く。旧実装はそれを状態語として
    # 認識できず、該当項目が**未対応にも済にも数えられずに消えていた**。実害＝
    # B-031/B-034/B-035/I-039/I-059 が本文節に滞留し、下の実データテストは緑のまま。
    # ⇒ ゲートの壊れ方 3 点（[[feedback-promote-recurring-checks]]）を全部通す。

    @pytest.mark.parametrize("written", [
        "**済（2.7a1・2026-08-04）**＝旧 `.venv` を削除し、再発ガードも入れた",  # B-038
        "**対応済み（2.6RC1）**＝独立レビュー（Codex）由来",                      # B-035
        "**対応済み（2.6RC1・`coords.py`）**＝RC1 のビルド前 QA で発見",          # B-034
        "**済**（2.6b・2026-08-02・実機 `2.6RC2` で画面確認 OK）",               # B-031
        "**済（2.5 の後・ドキュメントのみ）**。⚠️ 配布済み `2.5` の同梱 README",  # I-039
        "済",
    ])
    def test_壊れ方1_強調や同義語で済を見落とさない(self, hook, written):
        """①実データと同じ書き方の「済」が本文節にあれば必ず鳴ること。"""
        _, stale, _ = hook.parse_issues(_doc(
            "## 🐞 バグ", _item("B-004", written, resp="`abc1234`"), _ARCHIVE_HEAD,
        ))
        assert stale == ["B-004"], f"{written!r} を済として認識できていない"

    @pytest.mark.parametrize("written", [
        "未着手",
        "未着手（**調査完了・処方確定 2026-08-02／実施は 3.2**）",
        "未着手 ／ **✅ 対象版 = 2.7 確定（2026-08-04 ユーザー）**",
        "**対応中**。**②＝対応済み（実機確認待ち・2.6a2）／④＝済（2.6a1）**",
    ])
    def test_壊れ方2_未対応を済と誤らない(self, hook, written):
        """②註釈の中に「済」が現れても、宣言された状態（先頭）を採ること。

        ここを取り違えると**未対応の項目が注入から消える**＝I-009/I-011 と同じ実害。
        """
        items, stale, _ = hook.parse_issues(_doc("## 🐞 バグ", _item("B-004", written)))
        assert stale == [], f"{written!r} を済と誤判定している"
        assert len(items) == 1, f"{written!r} が未対応として注入に載っていない"

    def test_壊れ方3_置き場ではなく状態を見ている(self, hook):
        """③「本文節にある」ことではなく「済である」ことを条件にしていること。

        本文節の未対応で鳴り、アーカイブ節の済で鳴らない——この差が付かなければ、
        規則は場所だけを見ていることになる。
        """
        _, stale_open, _ = hook.parse_issues(_doc("## 🐞 バグ", _item("B-004", "未着手")))
        _, stale_arc, _ = hook.parse_issues(_doc(
            _ARCHIVE_HEAD, _item("B-004", "**済（2.6RC1）**", resp="`abc1234`"),
        ))
        assert stale_open == [] and stale_arc == []


# ============================================================
# 済の裏取り（対応版＋コミット参照。18件中4件しか守られていなかった規則）
# ============================================================
class TestDoneEvidence:
    @pytest.mark.parametrize("resp", [
        "2.4b3 / `3a5b75a`。配色をテーマから取る。",
        "2.4b2 / `40e7254`（PR#44 `88283c4`）。",
        "2.4a1。PR#45 で対応。",
    ])
    def test_commit_reference_satisfies_the_rule(self, hook, resp):
        _, _, weak = hook.parse_issues(_doc(_ARCHIVE_HEAD, _item("B-001", "済", resp=resp)))
        assert weak == []

    def test_missing_commit_reference_is_flagged(self, hook):
        _, _, weak = hook.parse_issues(_doc(
            _ARCHIVE_HEAD, _item("B-001", "済", resp="2.4a1。メニューへテーマ色を適用した。"),
        ))
        assert weak == ["B-001"]

    def test_explicit_opt_out_is_respected(self, hook):
        """参照が原理的に無い項目は明記でオプトアウトできる（B-000 の実例）。

        「書き忘れ」と「参照が無いと判断した」を区別するための逃げ道。
        """
        _, _, weak = hook.parse_issues(_doc(
            _ARCHIVE_HEAD,
            _item("B-000", "済", resp="実行時バリデーションで実害は解消。**コミット参照なし**。"),
        ))
        assert weak == []

    @pytest.mark.parametrize("resp", [
        "2.3b2 で最小距離ガードを追加予定（append 前に距離チェック）。",  # B-000 の旧記述
        "2.4RC2（コード修正済み・**未コミット**／ビルドは配布パスで）。",   # B-010 の旧記述
        "2.3b2 で対応予定。",
    ])
    def test_unfulfilled_wording_is_flagged(self, hook, resp):
        """「予定」「未コミット」が残る済は裏取りが弱い（誰も実施を確認しない）。"""
        _, _, weak = hook.parse_issues(_doc(_ARCHIVE_HEAD, _item("B-000", "済", resp=resp)))
        assert weak == ["B-000"]

    def test_quoting_a_corrected_old_wording_is_not_a_false_positive(self, hook):
        """過去の誤りを「」で引用して訂正した文で誤検知しないこと。

        誤検知が出ると毎セッション警告が出て読まれなくなる＝ガードの価値が消えるため、
        実データ（B-000/B-010 の訂正文）と同じ形を固定する。
        """
        _, _, weak = hook.parse_issues(_doc(
            _ARCHIVE_HEAD,
            _item("B-010", "済", resp=(
                "2.4RC2 / `a15801f`。旧記述「未コミット／ビルドは RC2 の配布パスで」は"
                "実態へ更新。旧記述「2.3b2 で追加予定」は予定のまま実施されず。"
            )),
        ))
        assert weak == []

    def test_open_items_are_never_flagged_for_evidence(self, hook):
        """未対応に裏取りを求めない（対応前なのでコミットが無いのは当然）。"""
        _, _, weak = hook.parse_issues(_doc(_item("B-013", "未着手")))
        assert weak == []


# ============================================================
# 実データ（現在の ISSUES.md）が清浄であること
# ============================================================
class TestDuplicateIds:
    """**1 項目 1 ID** が守られていること（2026-08-12 新設）。

    実際に衝突させた＝新しいバグを起票するとき**未対応節の最大値だけを見て
    `B-072` を採った**が、`B-072` はアーカイブ節に既にあった。台帳は ID 降順で
    未対応節が上なので、**上から読むと済んだ番号が見えない**＝衝突は「うっかり」
    ではなく並び方が構造的に誘発する。

    ゲートの壊れ方 3 点（[[feedback-promote-recurring-checks]]）:
    - **一度も落ちない**: 下の `test_a_collision_across_sections_is_flagged` が、
      実際に起きた形（本文節とアーカイブ節に同じ ID）で赤くなることを確かめる。
    - **毎回鳴る**: 本文の相互参照とテンプレでは鳴らないことを 2 件で固定する。
      例外表は持たない（実データで 0 件＝下の実データ検査）。
    - **間違ったものを要求している**: 要求は「同じ ID の**項目**が 2 つ無い」こと
      だけ。番号が連番であることも、順に並んでいることも要求しない。
    """

    def test_a_collision_across_sections_is_flagged(self, hook):
        """実際に起きた形＝本文節の新項目とアーカイブ節の済が同じ ID。"""
        doc = _doc("## 🐞 バグ", _item("B-072", "未着手"),
                   _ARCHIVE_HEAD, _item("B-072", "済", resp="`abc1234`"))
        assert hook.duplicate_ids(doc) == ["B-072"]

    def test_distinct_ids_are_not_flagged(self, hook):
        doc = _doc("## 🐞 バグ", _item("B-074", "未着手"),
                   _ARCHIVE_HEAD, _item("B-072", "済", resp="`abc1234`"))
        assert hook.duplicate_ids(doc) == []

    def test_a_reference_in_the_body_is_not_a_second_item(self, hook):
        """本文が他の項目を**指す**のは名乗りではない（毎回鳴るゲートにしない）。

        実データにこの形がある＝B-065 の状態欄が `B-074 (a) と束ねて直す` と書く。
        参照まで数えると、正しく相互参照するほど赤くなる。
        """
        doc = _doc("## 🐞 バグ", _item("B-065", "未着手"),
                   "- 🔑 **B-065 と B-074 は同じ面**（B-065 の状態欄から参照）",
                   _item("B-074", "未着手"))
        assert hook.duplicate_ids(doc) == []

    def test_the_template_is_not_counted(self, hook):
        """記入例テンプレ（HTML コメント内）は項目ではない。"""
        doc = _doc("## 🐞 バグ",
                   "<!--", "### ★ B-001: （症状を一言で）", "-->",
                   _item("B-001", "未着手"))
        assert hook.duplicate_ids(doc) == []

    def test_next_free_id_looks_at_every_section(self, hook):
        """空き番号は**全節**の最大値＋1（未対応節だけ見ると衝突する）。

        🔑 **これが衝突の原因そのものを消す部分**＝検出は事後にしか鳴らない。
        アーカイブ側のほうが大きい番号を持つ状況を作って固定する。
        """
        doc = _doc("## 🐞 バグ", _item("B-060", "未着手"), _item("I-010", "未着手"),
                   _ARCHIVE_HEAD, _item("B-072", "済", resp="`abc1234`"))
        assert hook.next_free_ids(doc) == {"B": "B-073", "I": "I-011"}


class TestAssignmentAudit:
    """未対応項目に**行き先が書いてあるか**（2026-08-12 新設）。

    きっかけ＝ユーザーの「現状の issue はすべて版に割り振り済みですか？」。答えは
    **No** で、**素の漏れ 2 件**（I-081・I-016）と**決定済みなのに在庫へ積み忘れ
    1 件**（I-078）が出た。⇒ 聞かれたときだけ数えるのでは遅い。

    🔴 **最初に書いた監査スクリプトは静かに間違えた**＝`対象版` 欄を版割りとして
    読み、**B-025 を 2.5**（実際は 3.2／`2.5RC2` は*発生元*）、**B-032 と I-077 を
    「行き先なし」**（実際はどちらも 3.0）と出した。**静かに間違える監査は、監査が
    無いより悪い**ので、その形を下の 2 つのテストで名指しで固定する。
    """

    def test_the_origin_version_field_is_not_the_destination(self, hook):
        """⛔ `発生版` 欄を行き先として読まないこと（実データの形で固定）。

        B-025 の実データ＝`発生版` は `2.5RC2（以前から同構造）`＝**発生元**で、
        直すのは状態欄が言う 3.2。欄を読むと 2.5 に振り分けてしまう。
        """
        doc = _doc(
            "### ★ B-025: DEM 取得が全滅しても黙って完走する",
            "",
            "- ★ **状態**: **対応中**。①③は ✅ 3.2 確定（呼び出し側と出力契約に触る）",
            "- **発生版**: 2.5RC2（以前から同構造）",
        )
        audit = hook.assignment_audit(doc)
        assert audit["assigned"] == {"3.2": ["B-025"]}, audit
        assert audit["undeclared"] == []

    @pytest.mark.parametrize("state", [
        "未着手 ／ **✅ 版割り確定＝3.0・処方は「直す」（2026-08-05 ユーザー）**",   # B-032
        "未着手（**3.0＝出力契約の回**。units.py が既にその線を書いている）",        # I-077
        "未着手（**✅ 3.0 確定＝2026-08-11 ユーザー決定**。理由＝出力契約に触れる）",  # B-071
        "未着手 ／ **✅ 対象版 = 3.0 確定（2026-08-05 ユーザー）**",                 # I-069
        "未着手（**調査完了・処方確定 2026-08-02／実施は 3.0**）",                   # B-033
    ])
    def test_the_many_prose_forms_of_a_destination_are_all_read(self, hook, state):
        """行き先の書き方は実データで 5 通り以上ある。**全部「書いてある」側**。

        ⚠️ ここが取りこぼされると、**版割り済みの項目が「漏れ」として毎回鳴る**＝
        正しく運用しているほど警告が増える（毎回鳴るゲート）。
        """
        doc = _doc("### ★ B-001: t", "", f"- ★ **状態**: {state}")
        audit = hook.assignment_audit(doc)
        assert audit["undeclared"] == [], f"{state!r} を未記入と誤判定"
        assert audit["assigned"] == {"3.0": ["B-001"]}, audit

    def test_a_destination_without_a_number_is_pending_not_missing(self, hook):
        """🔑 **番号が無いことと、行き先が無いことは別**（2026-08-24）。

        [[project-roadmap]] の `+0.1` の受け皿は**番号を後から決める**器なので、
        「受け皿へ入れると決めたが番号は未定」は*正規の運用*。これを取りこぼし
        （`undeclared`）として鳴らすと、**規約どおり運用するほど鳴る**ゲートになる。
        """
        doc = _doc("### ★ B-120: t", "",
                   "- ★ **状態**: 未着手（**✅ 次のマイナー〔`+0.1` の受け皿〕へ"
                   "＝2026-08-24 ユーザー決定。版番号未定**＝切るときに決める）")
        audit = hook.assignment_audit(doc)
        assert audit["undeclared"] == [], audit
        assert audit["pending"] == ["B-120"], audit

    def test_a_bare_open_item_is_flagged(self, hook):
        """行き先も判断待ちも書いていない項目は鳴ること（I-081・I-016 の形）。"""
        doc = _doc("### ★ I-081: 公開文書に旧番号の版が残っている", "",
                   "- ★ **状態**: 未着手")
        assert hook.assignment_audit(doc)["undeclared"] == ["I-081"]

    @pytest.mark.parametrize("state", [
        "未着手（**版割り未決**＝`+0.1` か 3.0 か）",          # I-075
        "保留（**設計判断待ち＝既存の決定を蒸し返すか**）",     # I-080
    ])
    def test_declared_indecision_is_not_a_miss(self, hook, state):
        """**決めていないと書いてある**なら漏れではない（版より前の段階）。

        ⚠️ ここを鳴らすと「まだ決めない」という正当な状態を持てなくなり、
        版を埋めるためだけの嘘の割り振りを誘発する。
        """
        doc = _doc("### ★ I-075: t", "", f"- ★ **状態**: {state}")
        audit = hook.assignment_audit(doc)
        assert audit["undeclared"] == [] and audit["pending"] == ["I-075"]

    def test_the_four_buckets_partition_every_open_item(self, hook):
        """4 分類の合計が未対応の総数になること（＝どれかに必ず入り、重複しない）。

        🔴 **この検査が無いと、報告が矛盾して見える**（2026-08-12 ユーザー指摘）＝
        `undeclared` は「行き先が**無い**」ではなく「行き先の**宣言**が無い」なので、
        **`pending` の項目も行き先は無い**。両者を並べて「行き先なし 0 件／判断待ち
        3 件」と報告すると読み手には矛盾に見える。⇒ **分類は分割（partition）で
        あって、名前はその分割のどこを指すかを言っていなければならない。**
        """
        doc = _doc(
            "### ★ B-001: 版が 1 つ", "", "- ★ **状態**: 未着手（✅ 3.0 確定）",
            "### ★ B-002: 候補が 2 つ", "", "- ★ **状態**: 未着手（2.8＝器の都合。3.0＝出力契約の都合）",
            "### ★ B-003: 決めていないと明記", "", "- ★ **状態**: 保留（**設計判断待ち**）",
            "### ★ B-004: 何も書いていない", "", "- ★ **状態**: 未着手",
            "### ★ B-005: 済は対象外", "", "- ★ **状態**: 済",
        )
        a = hook.assignment_audit(doc)
        assert sorted(v for ids in a["assigned"].values() for v in ids) == ["B-001"]
        assert a["ambiguous"] == ["B-002"]
        assert a["pending"] == ["B-003"]
        assert a["undeclared"] == ["B-004"]
        total = (sum(len(v) for v in a["assigned"].values())
                 + len(a["ambiguous"]) + len(a["pending"]) + len(a["undeclared"]))
        assert total == 4, f"4 分類の合計が未対応の総数と合わない: {total}"

    def test_two_candidates_in_one_state_line_go_to_ambiguous(self, hook):
        """**候補が 2 つ残る**ものは人に読ませる（機械で当てにいかない）。

        片方を選ぶ実装は、選び方を間違えても緑になる。⇒ どちらも肯定形で
        名指ししている欄は、機械が決めない。
        """
        doc = _doc("### ★ B-065: t", "",
                   "- ★ **状態**: 未着手（**2.8 の器に載る**。3.0 の契約にも関わる）")
        audit = hook.assignment_audit(doc)
        assert audit["ambiguous"] == ["B-065"] and audit["undeclared"] == []

    def test_a_version_that_is_ruled_out_is_not_a_candidate(self, hook):
        """🔴 **否定された版は「2 つ目の候補」ではない**（2026-08-24・I-111）。

        旧実装は「版が 2 つ見える」で曖昧に倒していたが、実データの 2 つ目は
        **ほぼ常に「その版では直さない理由」**だった（B-065・B-119・I-109）。
        ⇒ *選ぶ*のではなく、**候補でないものを候補から外す**。これは
        「片方を選ぶ実装は間違えても緑になる」という旧方針と矛盾しない。

        ⚠️ **この欄はブロッキングのゲートも読む**（`check_memory` の在庫照合）＝
        ここを曖昧に倒すと、行き先でない版の在庫に積めと要求してリリースを止める
        （2026-08-24 に実際に止まり、**台帳の日本語を削って**緑にした）。
        """
        doc = _doc("### ★ B-065: t", "",
                   "- ★ **状態**: 未着手（**行き先＝2.8**。2.7 では直さない）")
        audit = hook.assignment_audit(doc)
        assert audit["assigned"] == {"2.8": ["B-065"]}, audit
        assert audit["ambiguous"] == [] and audit["undeclared"] == []

    def test_a_pending_state_word_needs_no_mark(self, hook):
        """`保留` は台帳の凡例が「設計判断待ち」と定義している＝語だけで判断待ち。

        実データ＝B-119（`保留（… 2.9 では直さない …Tk 9 の評価待ち）`）を、
        旧実装は **2.9 行き**と読んでいた。2.9 は出荷済みなので、台帳は
        *済んだ版に未対応項目がぶら下がって見える*状態だった。
        """
        doc = _doc("### ★ B-119: t", "",
                   "- ★ **状態**: 保留（既知の制限・2.9 では直さない＝Tk 9 の評価待ち）")
        audit = hook.assignment_audit(doc)
        assert audit["pending"] == ["B-119"], audit
        assert audit["assigned"] == {}, audit

    def test_closed_items_are_not_audited(self, hook):
        """済・却下に行き先は要らない。"""
        doc = _doc(_ARCHIVE_HEAD, _item("B-001", "済", resp="`abc1234`"))
        a = hook.assignment_audit(doc)
        assert a["undeclared"] == [] and a["assigned"] == {}

    def test_the_template_is_not_audited(self, hook):
        doc = _doc("<!--", "### ★ B-0XX: （症状を一言で）",
                   "- ★ **状態**: 未着手 / 対応中 / 済", "-->")
        assert hook.assignment_audit(doc)["undeclared"] == []


def test_real_ledger_has_every_open_item_assigned(hook):
    """実データ＝行き先の無い未対応が 0 件であること（2026-08-12 に 2 件あり解消）。"""
    ledger = os.path.abspath(os.path.join(_HOOK_DIR, "..", "ISSUES.md"))
    if not os.path.exists(ledger):
        pytest.skip(structural_skip("ISSUES.md も git-ignore（CI には存在しない）"))
    with open(ledger, encoding="utf-8") as f:
        audit = hook.assignment_audit(f.read().splitlines())
    total = (sum(len(v) for v in audit["assigned"].values())
             + len(audit["pending"]) + len(audit["ambiguous"]) + len(audit["undeclared"]))
    assert total, "未対応が 0 件＝パーサが壊れている可能性が高い"
    assert audit["undeclared"] == [], (
        f"行き先が未記入の未対応がある: {audit['undeclared']}"
    )


def test_real_ledger_has_no_duplicate_ids(hook):
    """実際の台帳に ID の衝突が無いこと（2026-08-12 に 1 件あり、振り直した）。"""
    ledger = os.path.abspath(os.path.join(_HOOK_DIR, "..", "ISSUES.md"))
    if not os.path.exists(ledger):
        pytest.skip("ISSUES.md も git-ignore（CI には存在しない）")
    with open(ledger, encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert hook.issue_id_headings(lines), "ID が 1 つも採れていない＝パーサが壊れている"
    assert hook.duplicate_ids(lines) == [], (
        f"同じ ID の項目が 2 つ以上ある: {hook.duplicate_ids(lines)}"
    )


def test_real_ledger_has_no_outstanding_warnings(hook):
    """実際の台帳に「未移動」「裏取りが弱い」が溜まっていないこと。

    2026-07-25 に済 18 件を移動しコミット参照を backfill した状態を固定する。
    ここが落ちたら**掃除をサボった**という意味なので、警告どおり直す。
    """
    ledger = os.path.abspath(os.path.join(_HOOK_DIR, "..", "ISSUES.md"))
    if not os.path.exists(ledger):
        pytest.skip("ISSUES.md も git-ignore（CI には存在しない）")
    with open(ledger, encoding="utf-8") as f:
        items, stale, weak = hook.parse_issues(f.read().splitlines())
    assert items, "未対応項目が 0 件＝パーサが壊れている可能性が高い"
    assert stale == [], f"済だがアーカイブ節へ未移動: {stale}"
    assert weak == [], f"済だが裏取りが弱い: {weak}"
    assert hook.unquoted_user_items(
        open(ledger, encoding="utf-8").read().splitlines()
    ) == [], "人由来なのに原文の引用が無い項目がある"


# ============================================================
# 原文の引用（2026-08-01 新設・言い換えのずれを検出可能にする）
# ============================================================
def _sourced(iid: str, source: str, quote: str = "", state: str = "未着手") -> str:
    """出所欄（と任意の原文欄）を持つ項目の断片。"""
    body = f"### ★ {iid}: タイトル\n\n- ★ **状態**: {state}\n"
    if quote:
        body += f"- ★ **原文（出所の言葉のまま・言い換えない）**: 「{quote}」\n"
    body += f"- **提案日・出所**: {source}\n"
    return body


class TestQuoteTheSource:
    """人の報告を Claude が言い換えて書くとき、**原文が残っていないとずれが検出できない**。

    2026-08-01 の 1 セッションで 3 件ずれた（I-052＝問いのすり替え／B-031＝「語が誤り」を
    「数字が誤り」に読み替え／B-029・B-030＝「バグ」を Claude の定義で適用）。**どれも
    ユーザーが読み返して初めて出た**＝台帳の中に原文が無い限り機械にも人にも見えない。
    ここが見張るのは**欄の欠落**だけ（中身の一致は機械で判定できないが、欄さえあれば
    人が突き合わせられる）。
    """

    def test_user_sourced_item_without_quote_is_flagged(self, hook):
        assert hook.unquoted_user_items(
            _doc(_sourced("I-100", "2026-08-01（2.6b1 試用のユーザー要望）"))
        ) == ["I-100"]

    def test_quote_satisfies_the_rule(self, hook):
        assert hook.unquoted_user_items(
            _doc(_sourced("I-100", "2026-08-01（2.6b1 試用のユーザー要望）",
                          quote="・保存ボタン大きすぎ"))
        ) == []

    def test_explicit_opt_out_is_respected(self, hook):
        """Claude/Codex 由来で引く原文が無いときの逃げ道（「書き忘れ」と区別する）。"""
        doc = _doc(_sourced("I-100", "2026-08-01（実機試用で気づいた・ユーザー報告ではない）")
                   .replace("- **提案日", "- ★ **原文**: **原文なし**（Claude 起票）\n- **提案日"))
        assert hook.unquoted_user_items(doc) == []

    @pytest.mark.parametrize("source", [
        "2026-07-30（独立レビュー Codex 由来）",
        "B-009 のクラス掃き出し",
        "2026-08-01（Codex レビュー由来・版区切り整合 WF の検算）",
    ])
    def test_non_human_sources_are_not_required_to_quote(self, hook, source):
        assert hook.unquoted_user_items(_doc(_sourced("I-100", source))) == []

    @pytest.mark.parametrize("source", [
        "2026-07-27（2.5RC1 試用のユーザー要望・スクリーンショット添付）",
        "2026-07-25（2.4RC2 試用のユーザー要望）",
    ])
    def test_items_older_than_the_rule_are_exempt(self, hook, source):
        """規則の開始日より前は対象外＝原文はもう手元に無い（遡ると全件が警告に載る）。"""
        assert hook.unquoted_user_items(_doc(_sourced("I-100", source))) == []

    def test_template_inside_html_comment_is_ignored(self, hook):
        """記入例テンプレは見出しの体裁が同じなので、コメント内は丸ごと飛ばす。"""
        doc = _doc(
            "<!-- テンプレ",
            _sourced("I-0XX", "2026-08-01（ユーザー要望）"),
            _sourced("I-101", "2026-08-01（2.6b1 試用のユーザー要望）"),
            "-->",
            _sourced("I-102", "2026-08-01（2.6b1 試用のユーザー要望）", quote="原文"),
        )
        assert hook.unquoted_user_items(doc) == []


# ============================================================
# check_memory.py check 9 ＝ 現在地表の衛生（2026-07-26 / 実証5-b）
# ============================================================
_CHECK_MEMORY_PATH = os.path.abspath(os.path.join(_HOOK_DIR, "check_memory.py"))


def _load_check_memory():
    spec = importlib.util.spec_from_file_location("_check_memory", _CHECK_MEMORY_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_check_memory"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def memcheck():
    if not os.path.exists(_CHECK_MEMORY_PATH):
        pytest.skip("check_memory.py は git-ignore（CI には存在しない）")
    return _load_check_memory()


class TestRoadmapDashboardHygiene:
    """ロードマップ現在地表の「片方のセルだけ古い」を機械で拾う。

    2026-07-26 の実例＝「次の一手」セルを公開済みに更新したのに、同じ行の
    「状態」セルが「ビルド未実施」のまま残った。原因は表の様式違反（1 セルが
    2,290 字まで肥大し、誰も全体を読まなくなっていた）。散文を置くなという
    ルールは置ける限り破られるので、**分量そのものを測る**。
    """

    def test_contradictory_state_words_in_one_row(self, memcheck):
        """今回の事故そのもの＝未実施と公開済みが同居する行を拾う。"""
        row = (1, "| 2.5 | 🚧 2.5RC1（ビルド未実施） | ✅ 公開済み＝gh release 実施 |")
        found = memcheck.check_dashboard_rows([row])
        assert any("食い違い" in f for f in found)

    def test_long_cell_is_flagged(self, memcheck):
        """散文の蓄積そのもの（＝食い違いを生む条件）を検出する。"""
        row = (1, "| 2.5 | 🚧 進行中 | " + "あ" * 400 + " |")
        found = memcheck.check_dashboard_rows([row])
        assert any("散文が溜まっている" in f for f in found)

    def test_waiting_is_not_a_contradiction(self, memcheck):
        """「公開済み＋試用待ち」は正常＝待ち/未定で誤検知しないこと。"""
        row = (1, "| 2.5 | 🚧 2.5RC1 公開済み／実機試用待ち | 指摘があれば RC2 |")
        assert memcheck.check_dashboard_rows([row]) == []

    def test_real_roadmap_dashboard_is_clean(self, memcheck):
        """実データで誤検知ゼロ（ノイズを出すゲートは読まれなくなる）。"""
        rows = memcheck._dashboard_rows()
        assert rows, "現在地表を 1 行も拾えていない＝パーサが壊れている可能性"
        assert memcheck.check_dashboard_rows(rows) == []


class TestRoadmapHeadingMatchesRow:
    """現在地表の状態と、その版の H2 見出しが食い違っていないか。

    check 9 が拾うのは「1 行の中の」食い違い。こちらはその 1 段外側＝表は
    リリース済みと言っているのに、その版の節の見出しが 🚧 のまま、という形。

    2026-07-30 の実例＝2.5 の正式リリースで、現在地表と節内の「現在地」行は
    更新したのに `## 🚧 2.5（RC 進行中…）` を直し忘れ、ユーザーの指摘で発覚した。
    「同じ事実が 2 か所にあって片方だけ直る」の 4 例目なので、心がけではなく
    ここで測る。
    """

    def test_shipped_row_with_in_progress_heading_is_flagged(self, memcheck):
        """今回の事故そのもの。"""
        rows = [(1, "| 2.5 | ✅ リリース済み（tag `2.5`） | — |")]
        headings = [(9, "## 🚧 2.5（RC 進行中・確定 2026-07-11）— テーマ「条件探索」")]
        found = memcheck.check_heading_matches_row(rows, headings)
        assert any("見出しに" in f for f in found)

    def test_matching_pair_is_not_flagged(self, memcheck):
        rows = [(1, "| 2.5 | ✅ リリース済み（tag `2.5`） | — |")]
        headings = [(9, "## ✅ 2.5（リリース済み・2026-07-30 tag `2.5`）")]
        assert memcheck.check_heading_matches_row(rows, headings) == []

    def test_unreleased_version_keeps_its_in_progress_heading(self, memcheck):
        """未リリースの版が 🔜 の見出しを持つのは正常＝誤検知を出さない。"""
        rows = [(1, "| 2.6 | 🔜 確定・未着手 | 着手門＝中継方式の決定 |")]
        headings = [(9, "## 🔜 2.6（確定 2026-07-11）— テーマ「中継点」")]
        assert memcheck.check_heading_matches_row(rows, headings) == []

    def test_version_token_does_not_match_a_longer_number(self, memcheck):
        """2.5 の行が『12.5』や『2.50』の見出しに当たらないこと。"""
        rows = [(1, "| 2.5 | ✅ リリース済み | — |")]
        headings = [(9, "## 🚧 12.5（RC 進行中）"), (10, "## 🚧 2.50（進行中）")]
        assert memcheck.check_heading_matches_row(rows, headings) == []

    def test_real_roadmap_is_clean(self, memcheck):
        """実データで誤検知ゼロ。"""
        rows, headings = memcheck._dashboard_rows(), memcheck._roadmap_headings()
        assert rows and headings, "パーサが何も拾えていない"
        assert memcheck.check_heading_matches_row(rows, headings) == []

    # -- 状態は「欄の先頭の記号」で読む（I-093）-----------------------------
    #
    # 🔴 実際に踏んだ誤検知＝2.8 の欄に「（消化した項目も同じ節に ✅ で残す）」と
    # 注記を書いたら、**その版がリリース済みと読まれて鳴った**。表の欄は状態の
    # ダッシュボードだが、括弧書きの注記が入ることは普通にある。
    # ⛔ 「欄に ✅ を書くな」で直さない＝ゲートに合わせて書き方を狭める形（壊れ方③）。

    def test_a_check_mark_inside_a_note_is_not_a_release(self, memcheck):
        """今回の誤検知そのもの＝**文中**の `✅` を状態と読まないこと。"""
        rows = [(1, "| 2.8 | 🔜 **着手（`2.8a1`）**（消化済みも同節に ✅ で残す） | … |")]
        headings = [(9, "## 🔜 2.8（着手中）— テーマ「利用者の手に開く版」")]
        assert memcheck.check_heading_matches_row(rows, headings) == []

    def test_a_leading_check_mark_still_means_released(self, memcheck):
        """⚠️ 本来の欠陥は**引き続き**捕まること（誤検知を消して網ごと殺さない）。"""
        rows = [(1, "| 2.5 | ✅ **リリース済み**（注記に 🔜 の字を含む） | — |")]
        headings = [(9, "## 🔜 2.5（RC 進行中）")]
        assert memcheck.check_heading_matches_row(rows, headings) != []

    def test_a_row_without_a_mark_falls_back_to_the_word(self, memcheck):
        """記号を付けない書き方でも読めること（様式が変われば黙って全行無視、を避ける）。"""
        assert memcheck.row_says_released("リリース済み（tag `2.2`）")
        assert not memcheck.row_says_released("着手（`2.8a1`）")

    def test_the_mark_reader_looks_only_at_the_head(self, memcheck):
        """判定そのものの単体検査（分岐ごとに 1 本＝B-078 の教訓）。"""
        assert memcheck.row_says_released("✅ リリース済み")
        assert memcheck.row_says_released("**✅ リリース済み**")
        assert not memcheck.row_says_released("🔜 着手（✅ 済みの項目も残す）")
        assert not memcheck.row_says_released("⬜ 骨子確定（✅ 着手順も確定）")


class TestRoadmapSectionPlacement:
    """版を切り替えたときに、版の節を並べ替えたか（check 17）。

    ロードマップ自身の様式が決めている 2 つの配置＝①リリースしたら §アーカイブ
    へ落とす ②現役の版の節は表のすぐ下に置く。**2026-08-24 の版の切り替わりで
    2 つとも破れた**（2.9 がアーカイブの外に残り、次の版の 3.0 は §3.x の中の
    H3 のままだった＝どちらもユーザー指摘）。版あたり 1 回しか来ない工程なので
    想起では守れない＝位置だけを機械で見る。
    """

    _ARCHIVE = 100
    _RELEASED_ROW = [(9, "| 2.9 | ✅ リリース済 | — |")]
    _NEXT_ROW = [(9, "| 3.0 | 🔜 **次の版** | 結果の信頼性と出力契約 |")]

    def test_released_section_left_above_the_archive_is_flagged(self, memcheck):
        """事故その 1＝出し終えた版の節が現役の位置に残っている。"""
        headings = [(1, "## 現在地"), (44, "## ✅ 2.9（リリース済み）"),
                    (self._ARCHIVE, "## 🗄 アーカイブ")]
        found = memcheck.check_section_placement(
            self._RELEASED_ROW, headings, self._ARCHIVE)
        assert any("アーカイブ" in f for f in found)

    def test_next_version_without_its_own_section_is_flagged(self, memcheck):
        """事故その 2＝次の版が §3.x の中の H3 のままで、表の直下に居ない。"""
        headings = [(1, "## 現在地"), (50, "## ⬜ 3.x — 哲学「製品成熟」"),
                    (self._ARCHIVE, "## 🗄 アーカイブ")]
        found = memcheck.check_section_placement(
            self._NEXT_ROW, headings, self._ARCHIVE)
        assert any("繰り上げ" in f for f in found)

    def test_next_version_buried_in_the_archive_is_flagged(self, memcheck):
        headings = [(1, "## 現在地"), (self._ARCHIVE, "## 🗄 アーカイブ"),
                    (120, "## 🔜 3.0 — 結果の信頼性と出力契約")]
        found = memcheck.check_section_placement(
            self._NEXT_ROW, headings, self._ARCHIVE)
        assert any("戻す" in f for f in found)

    def test_correct_placement_is_silent(self, memcheck):
        headings = [(1, "## 現在地"), (44, "## 🔜 3.0 — 結果の信頼性と出力契約"),
                    (self._ARCHIVE, "## 🗄 アーカイブ"),
                    (110, "## ✅ 2.9（リリース済み）")]
        rows = self._RELEASED_ROW + self._NEXT_ROW
        assert memcheck.check_section_placement(rows, headings, self._ARCHIVE) == []

    def test_a_version_mentioned_in_a_heading_is_not_that_version_section(
            self, memcheck):
        """⛔ 見出しに版の字が含まれるかで見ない＝§次のマイナーの見出しは
        「§2.9 を切って器を再設置」を含み、2.9 の節に化ける。"""
        headings = [(1, "## 現在地"),
                    (44, "## 🧺 次のマイナー（2026-08-14 に §2.9 を切って器を再設置）"),
                    (self._ARCHIVE, "## 🗄 アーカイブ")]
        assert memcheck.check_section_placement(
            self._RELEASED_ROW, headings, self._ARCHIVE) == []

    def test_a_released_version_without_any_section_is_silent(self, memcheck):
        """古い版は §アーカイブ の中で 1 行に畳まれ H2 節を持たないことがある。"""
        headings = [(1, "## 現在地"), (self._ARCHIVE, "## 🗄 アーカイブ")]
        assert memcheck.check_section_placement(
            self._RELEASED_ROW, headings, self._ARCHIVE) == []

    def test_骨子確定_rows_are_not_treated_as_the_current_version(self, memcheck):
        """⬜ の行（3.1〜3.5）は H3 のままで正しい＝毎回鳴らせない。"""
        rows = [(9, "| 3.1 | ⬜ 骨子確定 | 配布の信頼性 |")]
        headings = [(1, "## 現在地"), (50, "## ⬜ 3.x"),
                    (self._ARCHIVE, "## 🗄 アーカイブ")]
        assert memcheck.check_section_placement(rows, headings, self._ARCHIVE) == []

    def test_the_next_mark_reader_looks_only_at_the_head(self, memcheck):
        """判定そのものの単体検査（row_says_released と同じ読み方＝I-093）。"""
        assert memcheck.row_says_next("🔜 **次の版**")
        assert not memcheck.row_says_next("✅ リリース済（🔜 の字を含む注記）")
        assert not memcheck.row_says_next("⬜ 骨子確定")

    def test_real_roadmap_is_clean(self, memcheck):
        """実データで誤検知ゼロ＝パーサが何も拾えていない形にもならないこと。"""
        headings = memcheck._roadmap_headings()
        rows = memcheck._dashboard_rows()
        assert rows and headings, "パーサが何も拾えていない"
        assert memcheck._archive_lineno(headings), "§アーカイブ を見つけられていない"
        assert memcheck.check_section_placement(
            rows, headings, memcheck._archive_lineno(headings)) == []


class TestMemoryIndexLineLength:
    """MEMORY.md の索引行が「要約」に留まっているかを機械で測る。

    check 9 と同じ失敗の 1 段上。MEMORY.md は毎セッション読み込まれるので
    知識ベースで最も読まれる面だが、ロードマップの索引行が約 1,000 字の
    ミニ版履歴に育ち、2026-07-26 に本体だけ更新されて索引が stale 化した
    （同日 3 件目の「同じ事実が 2 か所にあり片方だけ直る」）。
    """

    def test_long_index_line_is_flagged(self, memcheck):
        line = "- [x.md（長い）](x.md) — " + "あ" * 500
        found = memcheck.check_index_line_length([line])
        assert any("索引に詳細が溜まっている" in f for f in found)

    def test_normal_index_line_is_not_flagged(self, memcheck):
        line = "- [x.md（ふつう）](x.md) — " + "あ" * 100
        assert memcheck.check_index_line_length([line]) == []


# ============================================================
# session_budget.py ＝ トークン予算の計測（2026-07-27）
# ============================================================
_BUDGET_PATH = os.path.abspath(os.path.join(_HOOK_DIR, "session_budget.py"))


@pytest.fixture(scope="module")
def budget():
    if not os.path.exists(_BUDGET_PATH):
        pytest.skip("session_budget.py は git-ignore（CI には存在しない）")
    spec = importlib.util.spec_from_file_location("_session_budget", _BUDGET_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_session_budget"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestRoundtripCounting:
    """1 回の API 応答を 1 回として数えられているかを固定する。

    2026-07-27、最初の計測はこれを取り違えて結論を誤らせかけた。jsonl は 1 回の
    応答を content ブロックごとに複数行へ分割して記録し、**分割された各行が同じ
    usage を持ち回る**。行単位で数えると入力トークンが最大 3 倍に膨らみ（236M と
    出た値の実体は 175M）、並列ツール呼び出しも「並列度 1.00」に潰れて見える。
    施策の効果測定がこの数字に乗っているので、静かに壊れると判断ごと狂う。
    """

    def _write(self, tmp_path, entries):
        p = tmp_path / "t.jsonl"
        p.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in entries),
            encoding="utf-8",
        )
        return p

    def _assistant(self, mid, cache_read):
        return {
            "type": "assistant",
            "message": {
                "id": mid,
                "content": [{"type": "text", "text": "x"}],
                "usage": {"cache_read_input_tokens": cache_read},
            },
        }

    def test_split_message_counts_once(self, budget, tmp_path):
        """同一 message.id が 3 行に分割されても 1 往復・1 回ぶんのトークン。"""
        path = self._write(tmp_path, [self._assistant("msg_A", 1000)] * 3)
        trips, total = budget.count_roundtrips(path)
        assert trips == 1
        assert total == 1000

    def test_distinct_messages_accumulate(self, budget, tmp_path):
        path = self._write(
            tmp_path,
            [self._assistant("msg_A", 1000), self._assistant("msg_B", 2500)],
        )
        assert budget.count_roundtrips(path) == (2, 3500)

    def test_non_assistant_and_broken_lines_are_ignored(self, budget, tmp_path):
        """記録には user 行や壊れた行が混ざる＝落ちずに読み飛ばすこと。"""
        p = tmp_path / "t.jsonl"
        p.write_text(
            json.dumps({"type": "user", "message": {"content": "hi"}})
            + "\n{ broken json\n\n"
            + json.dumps(self._assistant("msg_A", 700)),
            encoding="utf-8",
        )
        assert budget.count_roundtrips(p) == (1, 700)

    def test_non_index_lines_are_ignored(self, memcheck):
        """見出しや説明文は対象外（索引エントリだけを見る）。"""
        assert memcheck.check_index_line_length(["## " + "あ" * 500]) == []

    def test_real_index_is_clean(self, memcheck):
        """実データで誤検知ゼロ（ノイズを出すゲートは読まれなくなる）。"""
        assert memcheck.check_memory_index_lines() == []


class TestGroundworkDeadline:
    """**将来の版のための布石**が、その版に入っても未了なら知らせること。

    2026-08-01：ロードマップとドローン応用メモの両方に「2.6 で waypoint の
    トポロジーを鎖で決め打ちせず点列＋接続規則で持つ（今ならほぼゼロ・後だと
    作り直し）」と書いてあったのに、2.6 の A-3 実装は鎖の決め打ちで通り、
    ユーザーの「スター型は配慮されている?」で初めて発覚した。決定は実在し
    正しかったが、**その版の作業リストに現れなかったので打たれなかった**。

    通常の仕組みが拾えなかった理由＝①布石はドローンの話題の下にあり、2.6 の
    作業順（テーマ＝A-3 から作った）に入らなかった ②版区切り整合WF は
    **リリース時**に走る＝着手時に要る布石には一巡遅い。⇒ 対象版を機械可読に
    書き、**時計のほうを機械に見張らせる**。
    """

    def _memo(self, tmp_path, text):
        p = tmp_path / "project_x.md"
        p.write_text(text, encoding="utf-8")
        return p

    def _run(self, memcheck, tmp_path, monkeypatch, ver):
        monkeypatch.setattr(memcheck, "MEM_DIR", tmp_path)
        return memcheck.check_groundwork(ver)

    def test_due_groundwork_is_flagged(self, memcheck, tmp_path, monkeypatch):
        self._memo(tmp_path, "- ⏳布石(2.6): waypoint を点列＋接続規則で持つ\n")
        found = self._run(memcheck, tmp_path, monkeypatch, "2.6a3")
        assert any("布石(2.6)" in f and "waypoint" in f for f in found)

    def test_future_groundwork_is_silent(self, memcheck, tmp_path, monkeypatch):
        """まだ先の版の布石では鳴らない（着手した版だけを見る＝ノイズを出さない）。"""
        self._memo(tmp_path, "- ⏳布石(3.2): 出力契約に版を持たせる\n")
        assert self._run(memcheck, tmp_path, monkeypatch, "2.6a3") == []

    def test_done_groundwork_is_silent(self, memcheck, tmp_path, monkeypatch):
        self._memo(tmp_path, "- ✅布石(2.6): waypoint を点列＋接続規則で持つ\n")
        assert self._run(memcheck, tmp_path, monkeypatch, "2.6a3") == []

    def test_it_keeps_nagging_after_the_target_version(self, memcheck, tmp_path,
                                                       monkeypatch):
        """**版を跨いでも鳴り続ける**＝打ち忘れたまま次の版へ進ませない。"""
        self._memo(tmp_path, "- ⏳布石(2.6): 打ち忘れたもの\n")
        assert self._run(memcheck, tmp_path, monkeypatch, "2.7a1")

    def test_half_width_parens_are_accepted(self, memcheck, tmp_path, monkeypatch):
        """記法の揺れ（全角/半角の括弧）で黙らないこと。"""
        self._memo(tmp_path, "- ⏳布石(2.6): x\n- ⏳布石（2.6）: y\n")
        assert len(self._run(memcheck, tmp_path, monkeypatch, "2.6a3")) == 2

    def test_notation_quoted_in_prose_is_ignored(self, memcheck, tmp_path,
                                                 monkeypatch):
        """記法を**説明している行**では鳴らないこと（宣言 vs 言及）。

        ⚠️ 実際に踏んだ誤検知（2026-08-01・実装直後）＝この check の説明文
        「`⏳布石(2.6)` … を拾い」が検出された。**check 11 でも同じ罠を踏んで
        いる**（本文中の日付は引用であることが多い）。同じクラスなので対策も
        同じ＝インラインコードを落とし、行頭付近の宣言だけを数える。
        """
        self._memo(
            tmp_path,
            "# 説明\n"
            "- メモリ全体から `⏳布石(2.6)` 形式のマーカーを拾い、版に達したら警告する\n"
            "- 長い前置きがずっと続く文章の途中に ⏳布石(2.6): が出てくる場合も言及\n",
        )
        assert self._run(memcheck, tmp_path, monkeypatch, "2.6a3") == []

    def test_real_memory_has_no_overdue_groundwork(self, memcheck):
        assert memcheck.check_groundwork(memcheck._current_version() or "0.0") == []


class TestVersionSyncIgnoresDeltaNotation:
    """版番号の**増分**表記（`+0.1` / `+1.0`）を版番号と読み違えないこと。

    2026-08-01 の誤検知＝ロードマップに「`+0.1` の受け皿」という見出しを足した
    ところ、`0.1` を版と読み current `2.6b1` より古い＝stale と鳴った。`+0.1`/
    `+1.0` は [[feedback-branch-strategy]]「見つけたものをどの版で直すか」の
    用語で**今後も見出しに現れる**ので、その場しのぎの言い換えでは再発する。

    check 11（本文の日付）・check 12（布石の記法）と**同じクラス**＝
    パターンが一致しただけの「言及」を「宣言」と取り違える誤検知。
    """

    def _memo(self, tmp_path, text):
        (tmp_path / "project_x.md").write_text(text, encoding="utf-8")

    def _run(self, memcheck, tmp_path, monkeypatch, ver):
        monkeypatch.setattr(memcheck, "MEM_DIR", tmp_path)
        return memcheck.check_version_sync(ver)

    def test_delta_notation_is_not_a_version(self, memcheck, tmp_path, monkeypatch):
        self._memo(tmp_path, "## 次のマイナー（番号未定）— `+0.1` の受け皿\n")
        assert self._run(memcheck, tmp_path, monkeypatch, "2.6b1") == []

    def test_major_delta_notation_is_not_a_version(self, memcheck, tmp_path,
                                                   monkeypatch):
        self._memo(tmp_path, "## 約束が変わるなら +1.0 へ割り振る\n")
        assert self._run(memcheck, tmp_path, monkeypatch, "2.6b1") == []

    def test_a_genuinely_stale_heading_is_still_flagged(self, memcheck, tmp_path,
                                                        monkeypatch):
        """除外を入れたせいで**本来の検出まで黙らない**こと（対の確認）。"""
        self._memo(tmp_path, "## 2.4 の作業中\n")
        assert self._run(memcheck, tmp_path, monkeypatch, "2.6b1")


class TestVersionSyncFlagsClaimsNotMentions:
    """古い版の**言及**で鳴らず、古い版を**現役だと主張**したときだけ鳴ること。

    2026-08-15 に判定を反転した。旧設計は「歴史を語る語」を列挙して黙らせる形で、
    その列挙を **2 度伸ばして 2 度漏れた**（`+0.1` の増分表記／「〜から更新」
    「初回使用は」）。後者は毎セッション 3 件鳴り続けており、
    [[feedback-promote-recurring-checks]] の**壊れ方②「毎回鳴る」**そのものだった。

    🔑 この検査は**古い版にしか反応しない**。そして見出し・description に出る
    古い版はほぼ全部が記録（更新履歴・取得時点）なので、除外側を数え上げる
    設計が逆だった。⇒ 「いま現役だ」と主張する語があるときだけ鳴らす。

    ⚠️ 取り逃し（現役と言わずに陳腐化した行）は設計上の受容。鳴りっぱなしで
    advisory ごと読み飛ばされるより総量が小さい、という判断。
    """

    def _memo(self, tmp_path, text):
        (tmp_path / "project_x.md").write_text(text, encoding="utf-8")

    def _run(self, memcheck, tmp_path, monkeypatch, ver):
        monkeypatch.setattr(memcheck, "MEM_DIR", tmp_path)
        return memcheck.check_version_sync(ver)

    # --- ① 黙るべきもの＝実データで実際に鳴っていた 3 つの形 -------------------
    def test_a_superseded_condition_quoted_as_history_is_silent(
            self, memcheck, tmp_path, monkeypatch):
        """「『2.6 の後』から更新」＝更新履歴（project_radiosim_for_drone L87）。"""
        self._memo(
            tmp_path,
            "## 4. 着手タイミング＝**3.x で要件が整ったら**"
            "（2026-08-01 ユーザー決定・**「2.6 の後」から更新**）\n")
        assert self._run(memcheck, tmp_path, monkeypatch, "2.8b3") == []

    def test_a_description_recording_an_update_is_silent(
            self, memcheck, tmp_path, monkeypatch):
        """description の更新履歴（project_radiosim_for_drone L3）。"""
        self._memo(
            tmp_path,
            "description: 着手は 2026-08-01 に「2.6 の後」→"
            "「3.x で要件が整ったら」へ更新。未着手。\n")
        assert self._run(memcheck, tmp_path, monkeypatch, "2.8b3") == []

    def test_a_first_observed_at_stamp_is_silent(
            self, memcheck, tmp_path, monkeypatch):
        """「初回使用は `2.6RC2`」＝取得時点の記録（project_real_world_env_vdi L38）。"""
        self._memo(
            tmp_path,
            "## 💻 別 FHD 実機の素性"
            "（2026-08-03 ユーザー確認・初回使用は `2.6RC2` の実機確認）\n")
        assert self._run(memcheck, tmp_path, monkeypatch, "2.8b3") == []

    # --- ② 鳴るべきもの（変異検証）＝反転で本来の検出を失っていないこと -------
    def test_calling_an_older_version_current_is_flagged(
            self, memcheck, tmp_path, monkeypatch):
        """⛔ これがこの検査の存在理由＝見出しが「現役は 2.6」のまま置き去り。"""
        self._memo(tmp_path, "## 現役は 2.6（テーマ「中継点」）\n")
        found = self._run(memcheck, tmp_path, monkeypatch, "2.8b3")
        assert found and "2.6" in found[0]

    def test_each_currency_word_fires(self, memcheck, tmp_path, monkeypatch):
        """語を 1 つ増やしたつもりで**効いていない**のを防ぐ（空振りの自衛）。"""
        for word in ("現役", "現在", "現行", "最新", "今は", "進行中", "作業中", "着手中"):
            self._memo(tmp_path, f"## {word} 2.6 の話\n")
            assert self._run(memcheck, tmp_path, monkeypatch, "2.8b3"), \
                f"currency word not wired: {word}"

    # --- ③ 間違ったものを要求していないこと -----------------------------------
    def test_a_currency_claim_on_the_current_version_is_silent(
            self, memcheck, tmp_path, monkeypatch):
        """現役の版を現役だと言うのは正常（向きの除外が生きていること）。"""
        self._memo(tmp_path, "## 🚧 現役は 2.8（進行中）\n")
        assert self._run(memcheck, tmp_path, monkeypatch, "2.8b3") == []

    def test_real_memory_is_clean(self, memcheck):
        """実データで誤検知ゼロ（①の 3 件が実際に黙っていること）。"""
        assert memcheck.check_version_sync(
            memcheck._current_version() or "0.0") == []


class TestUpdatedStampFreshness:
    """本文の「最終更新 YYYY-MM-DD」がファイル更新日より古くないこと。

    2026-07-26、現在地表を書き換えた（2.5RC1 公開）のに見出しの
    「最終更新 2026-07-25」を直し忘れた＝同日 5 件目の「同じ事実が 2 か所に
    あり片方だけ直る」。frontmatter の `modified:` は通常のファイル書き込みでは
    更新されず当てにならないので、**mtime だけが嘘をつかない**。
    """

    def _memo(self, tmp_path, text, mtime_date):
        import datetime
        p = tmp_path / "project_x.md"
        p.write_text(text, encoding="utf-8")
        ts = datetime.datetime.combine(mtime_date, datetime.time(12, 0)).timestamp()
        os.utime(p, (ts, ts))
        return p

    def _run(self, memcheck, tmp_path, monkeypatch):
        monkeypatch.setattr(memcheck, "MEM_DIR", tmp_path)
        return memcheck.check_updated_stamps()

    def test_stale_stamp_is_flagged(self, memcheck, tmp_path, monkeypatch):
        import datetime
        self._memo(tmp_path, "## 現在地（最終更新 2026-07-25）\n", datetime.date(2026, 7, 26))
        found = self._run(memcheck, tmp_path, monkeypatch)
        assert any("直し忘れ" in f for f in found)

    def test_same_day_edit_is_not_flagged(self, memcheck, tmp_path, monkeypatch):
        import datetime
        self._memo(tmp_path, "## 現在地（最終更新 2026-07-26）\n", datetime.date(2026, 7, 26))
        assert self._run(memcheck, tmp_path, monkeypatch) == []

    def test_latest_stamp_wins(self, memcheck, tmp_path, monkeypatch):
        """節ごとの古い日付だけでは鳴らない（最新の記載と比べる）。"""
        import datetime
        self._memo(
            tmp_path,
            "## A（最終更新 2026-06-01）\n## B（最終更新 2026-07-26）\n",
            datetime.date(2026, 7, 26),
        )
        assert self._run(memcheck, tmp_path, monkeypatch) == []

    def test_stamp_quoted_in_prose_is_ignored(self, memcheck, tmp_path, monkeypatch):
        """本文で過去の事故を引用しているだけの日付では鳴らないこと。

        ⚠️ 実際に踏んだ誤検知（2026-07-26）＝この check 自体の説明文に
        「見出しが『最終更新 2026-07-25』のままだった」と書いた行が拾われた。
        **ファイルが自分の更新日を宣言するのは見出し**で、本文中の日付は
        たいてい何かの引用（宣言 vs 言及）。
        """
        import datetime
        self._memo(
            tmp_path,
            "# メモ\n- 契機＝見出しが「最終更新 2026-07-25」のままだった\n",
            datetime.date(2026, 7, 26),
        )
        assert self._run(memcheck, tmp_path, monkeypatch) == []

    def test_memo_without_stamp_is_ignored(self, memcheck, tmp_path, monkeypatch):
        import datetime
        self._memo(tmp_path, "# 日付を持たないメモ\n", datetime.date(2026, 7, 26))
        assert self._run(memcheck, tmp_path, monkeypatch) == []

    def test_real_memory_is_clean(self, memcheck):
        assert memcheck.check_updated_stamps() == []


# ============================================================
# 存在しないモジュール参照（check 6）— I-072
# ============================================================
class TestStaleModuleRefs:
    """**除外が「場所」に依存していた**ことで死んだ検査を、宣言へ寄せ直した回。

    元の実装は「リポジトリを歩いて見つかった `.py` は実在」とし、docstring は
    除外理由に `map_widget.py`（tkintermapview）を名指ししていた。**venv が
    リポジトリの外へ出た**（`RADIOSIM_PYTHON`＝2.6a1／旧 `.venv` の削除＝2.7a1）
    ことでライブラリが視野から消え、**毎セッション同じ 2 行を報告し続けた**。

    ⚠️ ここで守るのは 2 つで、**片方だけでは意味がない**:
      ① ライブラリの名前で鳴らない（＝直したこと）
      ② **自分のモジュールの drift ではまだ鳴る**（＝検出力を捨てていないこと）
    ①だけを検証すると「全部黙らせる」変異が緑で通る（壊れ方①）。
    """

    def _memo(self, tmp_path, body: str):
        (tmp_path / "project_x.md").write_text(body, encoding="utf-8")

    def _run(self, memcheck, tmp_path, monkeypatch):
        monkeypatch.setattr(memcheck, "MEM_DIR", tmp_path)
        return memcheck.check_stale_module_refs()

    def test_library_module_outside_the_repo_is_not_flagged(
        self, memcheck, tmp_path, monkeypatch
    ):
        """宣言された venv に在るライブラリのモジュールでは鳴らない（I-072 の本題）。"""
        if not os.environ.get("RADIOSIM_PYTHON"):
            pytest.skip("RADIOSIM_PYTHON 未宣言＝この環境ではライブラリを解決しない")
        self._memo(tmp_path, "- `map_widget.py` の after 再スケジュールに注意\n")
        assert self._run(memcheck, tmp_path, monkeypatch) == []

    def test_a_module_that_exists_nowhere_is_still_flagged(
        self, memcheck, tmp_path, monkeypatch
    ):
        """**検出力を捨てていない**＝リポジトリにも venv にも無い名前は鳴る。"""
        self._memo(tmp_path, "- `infrastructure.py` が設定と DEM を抱えている\n")
        found = self._run(memcheck, tmp_path, monkeypatch)
        assert any("infrastructure.py" in f for f in found)

    def test_repo_module_is_not_flagged(self, memcheck, tmp_path, monkeypatch):
        """リポジトリに在る名前は従来どおり鳴らない（venv を見に行く前に解決する）。"""
        self._memo(tmp_path, "- `map_window.py` は単一インスタンス\n")
        assert self._run(memcheck, tmp_path, monkeypatch) == []

    def test_undeclared_interpreter_falls_back_to_repo_only(
        self, memcheck, tmp_path, monkeypatch
    ):
        """`RADIOSIM_PYTHON` 未設定なら従来の挙動（CI・新規 clone を壊さない）。

        ⚠️ **推測しない**＝ここで PATH の python を拾うと、別環境のライブラリ名で
        「実在する」ことになり、このプロジェクトに無い名前まで免除されてしまう。
        """
        monkeypatch.delenv("RADIOSIM_PYTHON", raising=False)
        assert memcheck._declared_venv_py_basenames() == set()
        self._memo(tmp_path, "- `map_widget.py` の話\n")
        found = self._run(memcheck, tmp_path, monkeypatch)
        assert any("map_widget.py" in f for f in found)

    def test_real_memory_is_clean(self, memcheck):
        """実メモリで鳴らないこと（②毎回鳴るの回帰ガード＝この項目の出発点）。"""
        if not os.environ.get("RADIOSIM_PYTHON"):
            pytest.skip("RADIOSIM_PYTHON 未宣言＝この環境ではライブラリを解決しない")
        assert memcheck.check_stale_module_refs() == []


# ============================================================
# check_memory.py check 13 ＝ 台帳の版割り ⇔ ロードマップの在庫（I-085）
# ============================================================
# 「割り振りを決める」と「在庫へ積む」は**別の手**なので、後者だけ落ちても
# どちらのファイルを読んでも矛盾が見えない。実際に 1 日で 2 件落ちた
# （I-078＝8/10 確定→積み忘れ／I-084＝8/12 確定→同じ日の棚卸しで再発）。


class TestVersionInventorySync:
    """台帳で版を確定した未対応が、ロードマップの在庫にも現れること。"""

    _LEDGER = [
        "## 💡 改善案",
        "### ★ I-100: 何かの改善",
        "- ★ **状態**: 未着手（**✅ 2.8 確定＝2026-08-12 ユーザー決定**）",
        "### ★ I-101: 別の改善",
        "- ★ **状態**: 未着手（**✅ 3.0 確定**）",
        "## ✅ 確認済み・対応済み（アーカイブ）",
        "### ★ I-102: 済んだ改善",
        "- ★ **状態**: **済**（`2.8a1`・`abc1234`）",
    ]
    _ROADMAP = ["## 🔜 2.8 — 受け皿", "1. **I-100 何かの改善**", "## 🔜 3.0 — 出力契約"]

    def test_an_assigned_item_missing_from_the_inventory_is_flagged(self, memcheck):
        """在庫に積み忘れた項目が鳴ること（この項目の出発点そのもの）。"""
        roadmap = ["## 🔜 2.8 — 受け皿", "1. 何も積んでいない", "## 🔜 3.0"]
        found = memcheck.check_ledger_matches_inventory(self._LEDGER, roadmap, "2.8")
        assert found and "I-100" in found[0]

    def test_an_item_present_in_the_inventory_is_silent(self, memcheck):
        """積んであれば鳴らないこと（②毎回鳴るを避ける）。"""
        assert memcheck.check_ledger_matches_inventory(
            self._LEDGER, self._ROADMAP, "2.8") == []

    def test_other_versions_are_not_demanded(self, memcheck):
        """別の版に割り振った項目を、この版の在庫に要求しないこと。"""
        assert "I-101" not in "".join(
            memcheck.check_ledger_matches_inventory(self._LEDGER, self._ROADMAP, "2.8"))

    def test_done_items_are_not_demanded(self, memcheck):
        """済んだ項目は在庫に無くてよい（積む義務があるのは未対応だけ）。"""
        assert "I-102" not in "".join(
            memcheck.check_ledger_matches_inventory(self._LEDGER, self._ROADMAP, "2.8"))

    def test_the_reverse_direction_is_not_checked(self, memcheck):
        """⛔ **逆向き（在庫にあり台帳に無い）は鳴らさないこと。**

        在庫には ID を持たない項目が正当に居る（`B-061 の残留リスク`・
        `表示言語の利用者拡張`）。両方向にすると毎回鳴る網になる。
        """
        roadmap = self._ROADMAP + ["2. **B-061 の残留リスク**", "3. **I-999 台帳に無い**"]
        assert memcheck.check_ledger_matches_inventory(self._LEDGER, roadmap, "2.8") == []

    def test_a_version_named_only_to_rule_it_out_is_not_demanded(self, memcheck):
        """🔴 **リリースを止めた実データ**をそのまま固定する（2026-08-24・I-111）。

        I-109 の行き先は 3.1 で、状態欄には「**⚠️ `2.9` には入れない**」と
        *入れない理由*が書いてあった。旧実装はこれを「2.9 と確定した」と読み、
        **2.9 の在庫の欠け**として正式リリース工程の Tier-0 を止めた。
        ⇒ そのとき緑にした手は**台帳の日本語を削ること**だった（応急を 2 回）。

        ⚠️ この検査は**在庫節が存在する**状態で書く＝節が無いと
        `inventory_ids_for_version` が `None` を返して**丸ごと黙る**ので、
        通っても何も言っていないことになる（*一度も落ちないゲート*）。
        """
        ledger = [
            "## 💡 改善案",
            "### ★ I-109: 追加言語のキー一覧が配布版の手元に無い",
            "- ★ **状態**: 未着手（**✅ 3.1 確定＝ユーザー決定**＝配布の芯と一致する。"
            "⚠️ **`2.9` には入れない**＝新しい約束が 1 つ増えるので `+0.1` の器ではない）",
        ]
        roadmap = ["## 🔜 2.9 — 直せるようにする版", "1. **I-098 地図で直せる**",
                   "## 🔜 3.1 — 配布"]
        assert memcheck.check_ledger_matches_inventory(ledger, roadmap, "2.9") == [], \
            "行き先でない版の在庫に積めと要求している（I-111 の再発）"
        # ⚠️ 対で見る＝**本当の行き先では鳴る**こと（上だけだと「常に黙る」で緑になる）
        found = memcheck.check_ledger_matches_inventory(ledger, roadmap, "3.1")
        assert found and "I-109" in found[0], "行き先の在庫の欠けを見逃している"

    def test_version_token_matching_has_boundaries(self, memcheck):
        """`2.8` が `12.8` や `2.85` に当たらないこと（版の取り違え）。"""
        assert memcheck._names_version("2.8a1 で直す", "2.8")
        assert not memcheck._names_version("12.8 の話", "2.8")
        assert not memcheck._names_version("2.85 の話", "2.8")

    def test_missing_inventory_section_is_silent(self, memcheck):
        """その版の節がまだ無いなら黙ること（版を切る前に鳴らさない）。"""
        assert memcheck.check_ledger_matches_inventory(self._LEDGER, ["## 🔜 3.0"], "2.8") == []

    def test_real_data_is_clean(self, memcheck):
        """実データで鳴らないこと（②毎回鳴るの回帰ガード）。"""
        assert memcheck.check_version_inventory() == []

    def test_ids_next_to_japanese_are_seen(self, memcheck):
        """🔴 **在庫の ID が日本語に直に接していても拾うこと**（B-078 と同型）。

        `\\b` で境界を取っていたころは `クラスI-100` が見えず、**積んであるのに
        「積み忘れ」と鳴った**。実データのロードマップで 19 件が不可視だった。
        """
        roadmap = ["## 🔜 2.8 — 受け皿", "1. クラスI-100を直す", "## 🔜 3.0"]
        assert memcheck.check_ledger_matches_inventory(
            self._LEDGER, roadmap, "2.8") == []


# ============================================================
# check_memory.py check 16 ＝ 状態欄と対応欄の食い違い（I-016）
# ============================================================


class TestStateContradictsResponse:
    """「状態＝未着手」なのに対応欄が実施を語る項目を鳴らす。

    出発点は I-016＝作業は `2.7a2` で終わっていたのに状態欄が「未着手」のまま残り、
    **棚卸しが済んだ仕事に版を割り振った**（人も機械も古い方を信じた）。
    ⚠️ check 13 では捕まらない＝あちらは状態欄を*正*として在庫と突き合わせるので、
    状態欄そのものが古いときは静かになる。
    """

    def _item(self, state: str, resp: str) -> list[str]:
        return ["## 💡 改善案", "### ★ I-100: 何かの改善",
                f"- ★ **状態**: {state}", f"- **対応**: {resp}"]

    def test_the_original_defect_is_caught(self, memcheck):
        """I-016 の実際の形（未着手なのに版とコミットが書いてある）。"""
        found = memcheck.check_state_contradicts_response(
            self._item("未着手（**✅ 2.8 確定**）", "**2.7a2 / `d2a256c`（2026-08-08）**"))
        assert found and "I-100" in found[0]

    def test_a_plain_open_item_is_silent(self, memcheck):
        """まだ手を付けていない項目では鳴らないこと（②毎回鳴る）。"""
        assert memcheck.check_state_contradicts_response(
            self._item("未着手（**✅ 2.8 確定**）", "未")) == []

    def test_a_destination_version_is_not_evidence(self, memcheck):
        """⛔ 「対応: 未（3.0）」は**行き先**であって実施記録ではない。

        絞らないと実データで B-071・I-077 が鳴る（＝毎回鳴る網になる）。
        """
        assert memcheck.check_state_contradicts_response(
            self._item("未着手", "未（3.0）")) == []

    def test_in_progress_items_may_carry_a_record(self, memcheck):
        """「対応中」は部分実施の記録を持ってよい（B-025 が実例）。"""
        assert memcheck.check_state_contradicts_response(
            self._item("対応中（一部のみ）", "`2.7a2` で①だけ実施")) == []

    def test_archived_items_are_not_checked(self, memcheck):
        """アーカイブ節は「済＋実施記録」が普通の形なので対象外。"""
        lines = ["## ✅ 確認済み・対応済み（アーカイブ）", "### ★ I-102: 済んだ改善",
                 "- ★ **状態**: 未着手", "- **対応**: `2.7a2` / `abc1234`"]
        assert memcheck.check_state_contradicts_response(lines) == []

    def test_real_data_is_clean(self, memcheck):
        """実データで鳴らないこと（I-016 を直したので 0 件）。"""
        assert memcheck.check_ledger_state_consistency() == []


# ============================================================
# check_memory.py check 14/15 ＝ 索引の揮発物（I-086）と正典移動（I-087）
# ============================================================


class TestIndexVolatileValues:
    """索引に「いまの件数」を書かせない（I-086）。

    索引は毎セッション読み込まれるので、古い数字を信じたまま作業を始める入口に
    なる。実際に 2 度 stale 化した（ロードマップ行の在庫数／CodeGraph 行の必須条件）。
    """

    def test_a_live_inventory_count_is_flagged(self, memcheck):
        """実際に stale 化した形（在庫数）が鳴ること。"""
        found = memcheck.check_index_volatile_values(
            ["- [project_roadmap.md](project_roadmap.md) — 在庫 15 件で進行中"])
        assert found and "在庫 15 件" in found[0]

    @pytest.mark.parametrize("line", [
        "- [a.md](a.md) — 版段階は完了で b1・大改修ごとに +1",          # 規則の説明
        "- [b.md](b.md) — 2026-08-01 に 1 セッションで 3 件の実例",      # 凍った history
        "- [c.md](c.md) — 依存 3 件の実測が要る",                        # 要件の数
        "- [d.md](d.md) — 2026-08-12 に決着（2.8 で実施）",             # 日付・版番号
    ])
    def test_frozen_facts_are_not_flagged(self, memcheck, line):
        """⛔ **動かない数は鳴らさないこと。**

        ここを素朴に「件数を禁止」にすると索引 29 行中 8 行が当たり、その中身は
        規則の説明・凍った history・要件の数＝**毎回鳴る網**になる（壊れ方②）。
        """
        assert memcheck.check_index_volatile_values([line]) == []

    def test_non_index_lines_are_ignored(self, memcheck):
        """見出しや注記は対象外（索引の 1 行だけを見る）。"""
        assert memcheck.check_index_volatile_values(["## 未対応 20 件の話"]) == []

    def test_real_index_is_clean(self, memcheck):
        """実データで鳴らないこと（②毎回鳴るの回帰ガード）。"""
        assert memcheck.check_memory_index_volatile() == []


class TestCanonMovedButKept:
    """「正典は Q」と宣言しながら自分が実体を持つのを拾う（I-087）。

    2026-08-12 の実例＝`project_radiosim` が「ここで版スコープを再記述しない」と
    宣言する同じ 1 文で「2.2 リリース済み」「2.3 リリース済み」と再記述していた。
    🔑 危なかったのは古くなることではなく、**列挙が 2.3 で止まっていたこと**。
    """

    _DECL = "⚠️ 版ごとの進捗・現在地は [[project-roadmap]] の表を見る"

    def test_enumeration_alongside_the_pointer_is_flagged(self, memcheck):
        found = memcheck.check_pointer_without_removal(
            "project_radiosim.md", [self._DECL, "- 2.2 リリース済み・2.3 リリース済み"])
        assert found and "project_radiosim.md:2" in found[0]

    def test_a_file_without_the_pointer_is_out_of_scope(self, memcheck):
        """宣言していないファイルは対象外（版を語ってよい場所がある）。"""
        assert memcheck.check_pointer_without_removal(
            "other.md", ["- 2.2 リリース済み"]) == []

    def test_the_incident_record_itself_is_not_flagged(self, memcheck):
        """⛔ **記録・引用ブロック（`>`）を消させないこと。**

        この事故の*記録*そのものが「2.2 リリース済み」を引用している。ここで鳴ると
        ゲートが**記録を壊す**＝壊れ方③（間違ったものを要求する）。実データで 2 件該当。
        """
        assert memcheck.check_pointer_without_removal(
            "project_radiosim.md",
            [self._DECL, "> 昔は「2.2 リリース済み」と書いていた＝それが欠陥だった"],
        ) == []

    def test_real_memory_is_clean(self, memcheck):
        """実データで鳴らないこと（棚卸しの結果＝残存 0 件の回帰ガード）。"""
        assert memcheck.check_canon_moved_but_kept() == []


# ============================================================
# 並列度の畳み方（B-077・2026-08-13 ／ 計測の所在は I-091・2026-08-14 で 1 本化）
# ============================================================
# 🔴 **計測器が構造的に必ず 1.00 を返していた。**
# jsonl は 1 応答を複数行へ分割し、**1 エントリに入る tool_use は最大 1 個**。
# 旧実装は message.id の**最初の行だけ**を採っていたので、
#   - 先頭行はたいてい text だけ ⇒ その応答は丸ごと数から落ち、
#   - 拾えた応答も必ず 1 個ぶんしか見えない
# ⇒ 閾値 1.15 に永遠に届かず、何をしても鳴り続ける網だった（壊れ方②）。
# ⚠️ **この罠は analyze_usage.py も、上の TestRoundtripCounting の docstring も
# 既に知っていた**＝知識は在ったのに再実装で失われた。
#
# 🔁 **2026-08-14（I-091）＝計測の所在が 1 つになったので、ここも付け替えた。**
# `session_budget.py` にあった並列度の網は撤去した（指標が「独立性」を測っておらず、
# 逐次でしかあり得ない局面でも鳴る＝壊れ方③。理由は当該ファイルの註が正典）。
# ⇒ **畳み方を持つ実装は `tools/token-usage/analyze_usage.py` だけになった**ので、
# B-077 の教訓はそちらに掛ける。**「同じ計測が 2 か所」という B-077 の遠因も、
# これで構造的に消えた**（片方だけ正しい、が起こり得ない）。


def _load_analyzer():
    path = os.path.abspath(os.path.join(
        _HOOK_DIR, "..", "tools", "token-usage", "analyze_usage.py"))
    spec = importlib.util.spec_from_file_location("_analyze_usage", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_analyze_usage"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def analyzer():
    return _load_analyzer()


class TestParallelismFolding:
    """並列度が「合算」で数えられていること（B-077）。

    ⚠️ **規範ではなく計測の検査**＝ここが見るのは「数え方が正しいか」だけで、
    「いくつであるべきか」は問わない（閾値は I-091 で撤去した）。
    """

    def _entry(self, mid, tools=(), text=False):
        content = [{"type": "text", "text": "x"}] if text else []
        content += [{"type": "tool_use", "name": n} for n in tools]
        # ⚠️ `usage` が要る＝分析器は usage を持つ応答だけを母数に入れる
        # （＝1 往復として数えられたものだけを並列度の分母にする）。
        return {"type": "assistant",
                "message": {"id": mid, "content": content,
                            "usage": {"cache_read_input_tokens": 1}}}

    def _write(self, tmp_path, entries):
        p = tmp_path / "t.jsonl"
        p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries),
                     encoding="utf-8")
        return p

    def _transcript(self, tmp_path, tools_per_response):
        """応答ごとのツール列から、実データと同じ「分割された」jsonl を作る。"""
        entries = []
        for i, tools in enumerate(tools_per_response):
            entries.append(self._entry(f"m{i}", text=True))        # 先頭行＝text だけ
            for t in tools:
                entries.append(self._entry(f"m{i}", tools=(t,)))   # 1 行 1 tool_use
        return self._write(tmp_path, entries)

    def _parallelism(self, analyzer, path):
        """`analyze()` の結果から平均並列度を出す（`report()` と同じ数え方）。"""
        par = analyzer.analyze(pathlib.Path(path))["parallelism"]
        with_tool = sum(v for k, v in par.items() if k)
        n_tools = sum(k * v for k, v in par.items())
        return (n_tools / with_tool if with_tool else 0.0), with_tool

    def test_a_split_response_with_two_tools_counts_as_two(self, analyzer, tmp_path):
        """🔴 **これが旧実装で 1.00 に潰れていた形。**"""
        path = self._transcript(tmp_path, [["Bash", "Read"]] * 15)
        par, n = self._parallelism(analyzer, path)
        assert n == 15 and par == 2.0

    def test_a_response_whose_first_entry_is_text_is_not_dropped(self, analyzer, tmp_path):
        """先頭行が text だけの応答を分母から落とさないこと。

        実データではこれが多数派で、旧実装は 1 セッションあたり 43〜234 応答を
        丸ごと見落としていた。
        """
        path = self._transcript(tmp_path, [["Bash"]] * 15)
        par, n = self._parallelism(analyzer, path)
        assert n == 15 and par == 1.0

    def test_text_only_responses_stay_out_of_the_denominator(self, analyzer, tmp_path):
        """会話だけの応答は数えない（「喋った量」の指標にしない）。"""
        path = self._transcript(tmp_path, [["Bash", "Read"]] * 15 + [[]] * 10)
        par, n = self._parallelism(analyzer, path)
        assert n == 15 and par == 2.0

    def test_the_value_is_not_structurally_pinned_to_one(self, analyzer, tmp_path):
        """⚠️ **まとめ方が変われば数字が動くこと**（旧実装は動かなかった）。

        これが無いと「構造的に必ず 1.00」へ戻ったことに気づけない＝B-077 の本体。
        """
        for sub in ("a", "b"):
            (tmp_path / sub).mkdir(parents=True, exist_ok=True)
        low, _ = self._parallelism(
            analyzer, self._transcript(tmp_path / "a", [["Bash"]] * 15))
        high, _ = self._parallelism(
            analyzer, self._transcript(tmp_path / "b", [["Bash", "Read", "Grep"]] * 15))
        assert low == 1.0 and high == 3.0


# ============================================================
# セッションを区切る助言（トークン効率化の再立案・施策1／2026-08-14）
# ============================================================
# 🔑 **区切りは、効果に実測の裏づけがある唯一の施策**（5 セッション実測）＝
# 立ち上げの下駄 33k・伸び 約 1,260 tok/応答・**後半を新セッションでやり直すと
# 総入力が 56〜62% 減る**。⇒ 100 往復の時点で既に「切ったほうが 5 倍安い」。
#
# 🔴 **ここには 2026-08-14 までテストが 1 本も無かった。** その間、低い側の閾値
# （200/320）は `systemMessage`＝**人間の画面にしか出ない形**で残っており、
# **Claude に最初に届くのは 440**だった——**同じファイルの註が「systemMessage は
# 届かない」と実証しているのに**である。⇒ *知っていることが配線に反映されていない*
# 取りこぼしは、テストが無い面で起きる。


class TestSessionSplitAdvice:
    def _transcript(self, tmp_path, n, ctx=200_000):
        """`n` 往復ぶんの記録（各応答が `ctx` の文脈を持つ）。"""
        p = tmp_path / "t.jsonl"
        p.write_text("\n".join(
            json.dumps({"type": "assistant", "message": {
                "id": f"m{i}", "content": [{"type": "text", "text": "x"}],
                "usage": {"cache_read_input_tokens": ctx}}}, ensure_ascii=False)
            for i in range(n)), encoding="utf-8")
        return p

    def _run(self, budget, monkeypatch, capsys, tmp_path, n, ctx=200_000):
        path = self._transcript(tmp_path, n, ctx)
        monkeypatch.setattr(budget, "_STATE", tmp_path / "state.json")
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
            {"transcript_path": str(path), "session_id": "s1"})))
        try:
            budget.main()          # 早期 return は sys.exit、最後まで来ると普通に返る
        except SystemExit:
            pass
        out = capsys.readouterr().out.strip()
        return json.loads(out) if out else {}

    def test_the_first_threshold_reaches_the_model(
            self, budget, monkeypatch, capsys, tmp_path):
        """**最初の閾値で Claude に届くこと**（`systemMessage` へ戻さない）。

        ⚠️ これが緩むと「発火しているのに誰の行動も変えない」に逆戻りする＝
        2026-08-01 に実証済みの壊れ方。
        """
        first = min(budget._THRESHOLDS)
        assert first <= 250, f"最初の閾値が遅すぎる（{first}）"
        got = self._run(budget, monkeypatch, capsys, tmp_path, first)
        assert got.get("decision") == "block", (
            f"{first} 往復で Claude に届いていない（{got}）"
            "＝人間の画面にだけ出る形に戻っている。"
        )

    def test_every_threshold_reaches_the_model(self, budget):
        """助言の形は 1 つだけ＝**人間向けと Claude 向けに割らない**。"""
        assert budget._THRESHOLDS == budget._BLOCKING_THRESHOLDS
        src = open(_BUDGET_PATH, encoding="utf-8").read()
        # ⚠️ 素の語で見ない＝**撤去の理由を書いた註にも同じ語が出る**（実装中に踏んだ）。
        # 見るのは JSON のキーとして書かれた形だけ。
        assert '"systemMessage"' not in src.split("def main")[1], (
            "main に systemMessage の枝が戻っている（Claude に届かない）"
        )

    def test_the_advice_states_the_marginal_cost(
            self, budget, monkeypatch, capsys, tmp_path):
        """**限界費用を実数で言うこと**＝往復数だけでは高いかどうか分からない。

        ⚠️ ここで出すのは**事実**（いまの 1 往復が何トークンか）であって、
        「まとめよ」のような*行動の要求*ではない（I-091 で撤去した網との違い）。
        """
        got = self._run(budget, monkeypatch, capsys, tmp_path,
                        min(budget._THRESHOLDS), ctx=200_000)
        reason = got.get("reason", "")
        assert "200,000" in reason, f"いまの 1 往復の費用が出ていない: {reason}"
        assert f"{budget._FRESH_CONTEXT:,}" in reason, "比較対象（立ち上げ）が無い"
        assert "6.1 倍" in reason, f"何倍高いかが出ていない: {reason}"

    def test_the_same_threshold_is_not_repeated(
            self, budget, monkeypatch, capsys, tmp_path):
        """同じ閾値で二度言わないこと（毎回鳴る網にしない＝壊れ方②）。"""
        first = min(budget._THRESHOLDS)
        assert self._run(budget, monkeypatch, capsys, tmp_path, first)
        path = self._transcript(tmp_path, first)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
            {"transcript_path": str(path), "session_id": "s1"})))
        with pytest.raises(SystemExit):
            budget.main()
        assert capsys.readouterr().out.strip() == "", "同じ閾値で二度言っている"

    def test_latest_context_is_the_last_response(self, budget, tmp_path):
        """限界費用は**最後の応答**の文脈（平均でも合計でもない）。"""
        p = tmp_path / "t.jsonl"
        p.write_text("\n".join(
            json.dumps({"type": "assistant", "message": {
                "id": f"m{i}", "content": [],
                "usage": {"cache_read_input_tokens": c}}})
            for i, c in enumerate((10_000, 50_000, 123_456))), encoding="utf-8")
        assert budget.latest_context(p) == 123_456


# ============================================================
# 区切りの助言を「費用」でも鳴らす（I-105・2026-08-18）
# ============================================================
# 🔴 **このフックは自分で書いた判断材料で発火していなかった。**
# `latest_context()` の註は「**区切るべきかの唯一の判断材料は 1 往復がいくらか**」と
# 言うのに、**発火条件は往復数だけ**だった＝文脈が速く伸びる回では、高くなっている
# のに一度も鳴らない。実例（発見した回そのもの）＝**83 往復で 1 往復 170k＝5.1 倍**、
# 伸び 1,635 tok/応答（目安 1,260 より 30% 速い）。最初の閾値 200 往復に届くころには
# 約 360k＝**11 倍**で、**一番高い時間帯を丸ごと素通り**していた。
#
# 🔑 **I-103 と同じクラス**＝*決め手でない量を条件にしていた*。⇒ 費用の梯子を対で置く。


class TestMarginalCostTrigger:
    """費用の梯子（`_COST_MULTIPLES`）＝往復数が少なくても高ければ鳴る。"""

    def _run(self, budget, monkeypatch, capsys, tmp_path, n, ctx):
        return TestSessionSplitAdvice()._run(
            budget, monkeypatch, capsys, tmp_path, n, ctx)

    def test_an_expensive_short_session_fires(
            self, budget, monkeypatch, capsys, tmp_path):
        """🔴 **これが鳴らなかった形**＝往復数は最初の閾値の半分以下、費用は 5 倍。"""
        n = min(budget._THRESHOLDS) // 2
        got = self._run(budget, monkeypatch, capsys, tmp_path, n, 170_000)
        assert got.get("decision") == "block", (
            f"{n} 往復・1 往復 170k（立ち上げの 5.2 倍）で鳴っていない＝"
            "費用の梯子が外れている"
        )

    def test_a_cheap_long_session_does_not_fire_on_cost(
            self, budget, monkeypatch, capsys, tmp_path):
        """⚠️ **安ければ鳴らない**こと（毎回鳴る網にしない＝壊れ方②）。

        往復数の梯子にも届いていない回では、費用の梯子も黙っていること。
        """
        n = min(budget._THRESHOLDS) - 1
        got = self._run(budget, monkeypatch, capsys, tmp_path, n, 50_000)
        assert got == {}, f"安いセッションで鳴っている: {got}"

    def test_the_reason_names_which_ladder_fired(
            self, budget, monkeypatch, capsys, tmp_path):
        """**何で鳴ったかを言う**＝「まだ 83 往復なのに」と読み違えさせない。"""
        got = self._run(budget, monkeypatch, capsys, tmp_path,
                        min(budget._THRESHOLDS) // 2, 170_000)
        assert "発火の理由" in got.get("reason", "")
        assert "倍" in got["reason"].split("発火の理由")[1].splitlines()[0]

    def test_the_same_cost_level_is_not_repeated(
            self, budget, monkeypatch, capsys, tmp_path):
        """同じ倍率で二度言わないこと。"""
        n = min(budget._THRESHOLDS) // 2
        assert self._run(budget, monkeypatch, capsys, tmp_path, n, 170_000)
        assert self._run(budget, monkeypatch, capsys, tmp_path, n, 170_000) == {}

    def test_crossing_both_ladders_at_once_speaks_once(
            self, budget, monkeypatch, capsys, tmp_path):
        """⚠️ **片方だけ状態を上げない**＝同じ内容を次の Stop で二度言う形になる。"""
        n = min(budget._THRESHOLDS)
        assert self._run(budget, monkeypatch, capsys, tmp_path, n, 200_000)
        assert self._run(budget, monkeypatch, capsys, tmp_path, n, 200_000) == {}

    def test_a_higher_cost_level_speaks_again(
            self, budget, monkeypatch, capsys, tmp_path):
        """**上の倍率へ上がったら言い直す**（黙って登り続けない）。"""
        n = min(budget._THRESHOLDS) // 2
        assert self._run(budget, monkeypatch, capsys, tmp_path, n, 140_000)
        got = self._run(budget, monkeypatch, capsys, tmp_path, n, 280_000)
        assert got.get("decision") == "block", "倍率が上がったのに黙っている"

    def test_the_first_cost_level_is_near_a_hundred_roundtrips(self, budget):
        """最初の倍率が**実測の判断地点**に合っていること。

        ⚠️ 規範ではなく**実測との対応**の検査＝伸び 1,260〜1,635 tok/応答で
        4 倍（132k）に達するのは 80〜100 往復＝[[feedback-token-budget]] の
        「100 往復の時点で既に切ったほうが 5 倍安い」。ここが 10 倍などへ上がると
        **一番高い時間帯を素通りする**元の欠陥に戻る。
        """
        first = min(budget._COST_MULTIPLES) * budget._FRESH_CONTEXT
        assert 80 <= first / 1_635 <= 120, f"最初の倍率が実測と合っていない: {first:,}"

    def test_the_old_integer_state_is_still_read(self, budget, tmp_path):
        """移行の瞬間に**全部言い直さない**こと（旧状態は整数だった）。"""
        assert budget._reached_levels({"s": 440}, "s") == {"trips": 440, "cost": 0}
        assert budget._reached_levels({}, "s") == {"trips": 0, "cost": 0}
        assert budget._reached_levels({"s": {"trips": 200, "cost": 6}}, "s") == {
            "trips": 200, "cost": 6}

def test_the_stop_hook_no_longer_judges_parallelism(budget):
    """**撤去したものが戻っていないこと**（I-091・2026-08-14）。

    ⚠️ これは「機能が無いこと」の検査＝規範としての並列度を復活させるなら、
    まず I-091 の結論（指標が独立性を測っていない／block は配送しか強制しない）を
    覆すこと。**黙って戻すと、間違ったものを要求する網が復活する。**
    """
    assert not hasattr(budget, "recent_parallelism"), (
        "並列度の判定が session_budget.py に戻っている（計測は analyze_usage.py が持つ）"
    )
    src = open(os.path.join(_HOOK_DIR, "session_budget.py"), encoding="utf-8").read()
    assert "_PARALLEL_FLOOR" not in src, "並列度の規範的な閾値が戻っている"


# ============================================================
# シェルの遠回りを止めるフック（I-084 の②③ → I-092 で③を強制へ）
# ============================================================
_DETOUR_PATH = os.path.abspath(os.path.join(_HOOK_DIR, "no_shell_detours.py"))


@pytest.fixture(scope="module")
def detours():
    """`.claude/no_shell_detours.py` を単体モジュールとして読み込む。"""
    if not os.path.exists(_DETOUR_PATH):
        pytest.skip(structural_skip(".claude/ は git-ignore（CI には存在しない）。"))
    spec = importlib.util.spec_from_file_location("_no_shell_detours", _DETOUR_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_no_shell_detours"] = mod
    spec.loader.exec_module(mod)
    return mod


def _verdict(detours, command: str) -> str:
    """判定だけを返す（`None` は "pass"）。"""
    result = detours.check(command)
    return "pass" if result is None else result[0]


class TestShellDetourVerdicts:
    """**強制する範囲が実測どおりであること**（I-092）。

    ⚠️ ②（`cd`）は先頭語だけで決まったが、③は**同じ語でも形で変わる**＝
    実測で 5 回に 1 回は専用ツールで置換できない使い方だった。⇒ ここが緩むと
    「間違ったものを要求するゲート」（feedback-promote-recurring-checks の
    壊れ方③）になり、フックごと信用を失う。
    """

    # ---- A：素の探索＝止める --------------------------------------
    @pytest.mark.parametrize("command", [
        'grep -n "foo" core/models.py',
        'grep -rn "foo" --include=*.py .',
        'cat core/version.py',
        'head -60 ISSUES.md',
        'sed -n "10,20p" core/dem.py',
        'find core -name "*.py"',
        'grep -n "foo" ISSUES.md | head -50',        # 件数を絞るだけ＝head_limit で足りる
        r'grep -n "A\|B" core/i18n.py | head -40',  # ⚠️ `\|` はパイプではない
        'RADIOSIM_HEADLESS=1 grep -n "foo" f.py',    # 環境変数の前置きは読み飛ばす
        'ls docs/ && grep -n "foo" docs/manual_ja.md',
    ])
    def test_bare_searches_are_denied(self, detours, command):
        assert _verdict(detours, command) == "deny", command

    # ---- B/C/D/E：正当な形＝通す（助言のみ） -----------------------
    @pytest.mark.parametrize("command", [
        # B 集計＝専用ツールでは作れない値
        'grep -oE "B-[0-9]+" ISSUES.md | sort -n | uniq -c',
        'grep -n "対象版" ISSUES.md | cut -c1-120',
        # ⚠️ 絞ってから整形する形＝**後段は全段を見ないと取りこぼす**
        'grep -nE "④|⑤" memory/feedback_token_budget.md | head -25 | cut -c1-160',
        'find core report views -name "*.py" | wc -l',
        # C 自前の出力＝分けると往復が 1 増える
        '"$PY" -m pytest -q > "$T/full.txt" 2>&1; tail -20 "$T/full.txt"',
        'node tools/x.mjs > "$T/o.txt"; grep -E "^FAILED" "$T/o.txt"',
        # D 二段フィルタ＝文脈を取ってから絞る
        'grep -n "foo" -A 30 tests/test_x.py | grep -n "def "',
        'grep -rn "validate_rows" --include=*.py . | grep -v "^./tests"',
        # E 書き込み＝そもそも探索ではない
        'cat >> tests/test_x.py << EOF\nbody\nEOF',
        'cat core/a.py core/b.py > "$T/joined.py"',
    ])
    def test_legitimate_shapes_are_only_advised(self, detours, command):
        assert _verdict(detours, command) == "warn", command

    # ---- 触らない面 ------------------------------------------------
    @pytest.mark.parametrize("command", [
        'gh run view 123 | grep -n "failed"',        # パイプ後段＝正当な後処理
        '"$RADIOSIM_PYTHON" -m pytest -q',
        'git status --short',
    ])
    def test_untouched_shapes_pass(self, detours, command):
        assert _verdict(detours, command) == "pass", command

    def test_cd_is_still_denied_first(self, detours):
        """②は据え置き＝③の絞り込みで `cd` の判定を薄めていないこと。"""
        assert _verdict(detours, 'cd /tmp && ls') == "deny"
        assert _verdict(detours, '(cd /tmp && ls)') == "pass"
        # ③の絞り込み（集計は通す）を `cd` に適用していないこと
        assert _verdict(detours, 'cd /tmp && ls | wc -l') == "deny"

    def test_the_deny_message_names_what_still_passes(self, detours):
        """**止めない形を、止めるときに言うこと**。

        ⛔ 「探索は専用ツールで」とだけ言うと、集計やテスト出力の読み取りまで
        禁じられたと読める＝**正当な使い方を萎縮させる**。ゲートは「何が通るか」
        を同じ画面で言えないと、回避の口実か盲従のどちらかを育てる。
        """
        _, reason = detours.check('grep -n "foo" ISSUES.md')
        for expected in ("sort", "wc -l", "tail", "後段"):
            assert expected in reason, expected


class TestPipeTailParsing:
    """`|` の読み取り（実データ 331 件のうち 121 件を誤分類した箇所）。"""

    @pytest.mark.parametrize("segment,tail", [
        (r'grep -n "A\|B" f.py', ""),                   # 正規表現の選択
        ('grep -n "a|b" f.py', ""),                      # 引用符の中
        ('grep -n "x" f.py | head -5', " head -5"),
        ("grep -n 'x' f.py | sort", " sort"),
    ])
    def test_only_real_pipes_are_pipes(self, detours, segment, tail):
        assert detours._pipe_tail(segment) == tail

    def test_discarding_stderr_is_not_writing_a_file(self, detours):
        """`2>/dev/null` を「出力を作った」と読まないこと。

        ⚠️ これを取り違えると `ls x 2>/dev/null && head -60 ISSUES.md` が
        「自前の出力を読んでいる」ことになり、**素の探索を見逃す**（最初の
        分類器はこれで C を 46 → 61 件に水増しした）。
        """
        assert _verdict(detours, 'ls ISSUES.md 2>/dev/null && head -60 ISSUES.md') == "deny"


# ============================================================
# 区間集計＝主指標「課題 1 件の完了コスト」（I-103・2026-08-18）
# ============================================================
# 🔑 **測る対象が間違っていると、効かない施策を追い続ける**（I-091 で実際にそうなった
# ＝「平均並列度」は行動の代理指標でしかなく、**安くなったかを直接は答えない**）。
# ⇒ 主指標を ①課題 1 件あたりの往復数 ②同・総入力 ③作業構成別の中央値
# ④定型検証の往復数 に置き換え、並列度は補助診断へ降格した。
#
# ⚠️ **ここが守るのは「数え方」であって「いくつであるべきか」ではない**
# （TestParallelismFolding と同じ姿勢＝規範的な閾値は置かない）。
# 特に危ないのは**区間の境界**で、境界を取り違えるとコストの帰属が丸ごとずれる。
# 実データで実際に踏んだ 3 つを、ここで固定する:
#   1. `git -C <path> commit` が多数派（素朴な文字列一致では取れない）
#   2. ヒアドキュメントの**本文の中の文字列**をコミットと誤認する（実データに在る）
#   3. 課題 ID を本文全体から拾うと、*参照しただけ*の ID まで閉じたことになる
#      （実データで 1 コミットが 8 件を閉じた）


class TestCommitBoundaryDetection:
    """区間の境界＝コミットの検出（コストの帰属はここで決まる）。"""

    @pytest.mark.parametrize("cmd", [
        "git commit -m 'x'",
        "git add -A && git commit -F -",
        "git -C d:/dev/radiosim-repo commit -q -F -",          # 実データの多数派
        "git -C D:\\dev\\repo add -A; git -C D:\\dev\\repo commit -q -m",
        "& $env:X -m pytest -q; git -C d:/r add -A; git -C d:/r commit -q -m",
    ])
    def test_real_commits_are_boundaries(self, analyzer, cmd):
        assert analyzer.is_commit_command(cmd), cmd

    @pytest.mark.parametrize("cmd", [
        "git log --oneline -5",
        "git status --short",
        "git diff --check HEAD",
        # 🔴 本文の中の文字列＝コマンドではない（実データに在る形）
        "python - <<'HEOF'\nfor line in f:\n    if 'git commit' in line:\n        pass\nHEOF",
    ])
    def test_mentions_are_not_boundaries(self, analyzer, cmd):
        assert not analyzer.is_commit_command(cmd), cmd

    def test_the_summary_line_is_the_body_not_the_heredoc_marker(self, analyzer):
        """要約は**本文の 1 行目**＝ヒアドキュメントの閉じ引用符を食い残さないこと。

        ⚠️ 食い残すと要約が全部 `'` になり、**課題 ID を要約から拾えなくなる**
        （実装中に実データで踏んだ）。
        """
        cmd = "git add -A && git commit -F - <<'MSG'\nfix: 直す（B-001）\n\n本文\nMSG"
        assert analyzer._commit_subject(cmd) == "fix: 直す（B-001）"

    def test_the_powershell_here_string_summary_is_read_too(self, analyzer):
        cmd = "git add -A && git commit -m @'\nfix: 直す（B-001）\n\n本文\n'@"
        assert analyzer._commit_subject(cmd) == "fix: 直す（B-001）"

    @pytest.mark.parametrize("tail", [
        " && git log --oneline -1",
        "&& git -C d:/r log -1",
        "  ;  git status --short",
    ])
    def test_a_command_after_the_heredoc_marker_is_not_the_summary(self, analyzer, tail):
        """🔴 B-109: 開き記号のうしろに続くコマンドを要約と取り違えないこと。

        本文は**開き行の改行の後**から始まる（shell の規則）。取り違えると
        要約に課題 ID が無くなり、その区間が `--issues` の**母数から黙って外れる**
        （2026-08-19 に I-098 の区間 234 往復がまるごと落ちた実例）。
        """
        cmd = f"git commit -q -F - <<'MSG'{tail}\nfix: 直す（B-001）\n\n本文\nMSG"
        assert analyzer._commit_subject(cmd) == "fix: 直す（B-001）"


class TestIssueIntervals:
    """区間 → 課題への束ね方。"""

    def _resp(self, mid, ts, *, tools=(), ctx=1000):
        """1 応答（分割されていない形）。`tools` は (名前, 入力) の列。"""
        content = [{"type": "text", "text": "x"}]
        content += [{"type": "tool_use", "name": n, "input": i, "id": f"{mid}-{k}"}
                    for k, (n, i) in enumerate(tools)]
        return {"type": "assistant", "timestamp": ts,
                "message": {"id": mid, "content": content,
                            "usage": {"cache_read_input_tokens": ctx}}}

    def _commit(self, mid, ts, subject):
        cmd = "git add -A && git commit -F - <<'MSG'\n" + subject + "\nMSG"
        return self._resp(mid, ts, tools=[("Bash", {"command": cmd})])

    def _write(self, tmp_path, entries, name="t.jsonl"):
        p = tmp_path / name
        p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries),
                     encoding="utf-8")
        return [p]

    def test_an_interval_runs_from_the_previous_commit_to_this_one(
            self, analyzer, tmp_path):
        paths = self._write(tmp_path, [
            self._resp("a", "2026-08-18T01:00"),
            self._resp("b", "2026-08-18T01:01"),
            self._commit("c", "2026-08-18T01:02", "fix: 直す（B-001）"),
            self._resp("d", "2026-08-18T01:03"),
            self._commit("e", "2026-08-18T01:04", "fix: 直す（B-002）"),
        ])
        done, tail = analyzer.intervals(paths)
        assert [iv.issues for iv in done] == [("B-001",), ("B-002",)]
        assert [iv.roundtrips for iv in done] == [3, 2]   # コミットの応答を含む
        assert tail == []

    def test_work_after_the_last_commit_is_not_a_finished_interval(
            self, analyzer, tmp_path):
        """⚠️ 末尾の未コミット分を区間に数えない（*閉じていない*ものは母数の外）。"""
        paths = self._write(tmp_path, [
            self._commit("a", "2026-08-18T01:00", "fix: 直す（B-001）"),
            self._resp("b", "2026-08-18T01:01"),
        ])
        done, tail = analyzer.intervals(paths)
        assert len(done) == 1 and len(tail) == 1

    def test_a_failed_commit_does_not_close_an_interval(self, analyzer, tmp_path):
        """失敗したコミットで区切らない＝**やり直した分のコストが消える**のを防ぐ。"""
        entries = [
            self._commit("a", "2026-08-18T01:00", "fix: 直す（B-001）"),
            self._resp("b", "2026-08-18T01:01"),
            self._commit("c", "2026-08-18T01:02", "fix: 直す（B-001）"),
        ]
        # a の呼び出しだけ失敗した、という結果を足す
        entries.insert(1, {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a-0", "is_error": True,
             "content": "pre-commit hook failed"}]}})
        done, _tail = analyzer.intervals(self._write(tmp_path, entries))
        assert len(done) == 1, "失敗したコミットで区切っている"
        assert done[0].roundtrips == 3

    def test_issue_ids_come_from_the_summary_line_only(self, analyzer, tmp_path):
        """🔴 本文の**参照 ID** を閉じた課題として数えないこと。"""
        body = ("fix: 直す（B-001）\n\n"
                "B-017 と形が似ているが別物。I-042 も参照した。")
        paths = self._write(tmp_path, [
            self._commit("a", "2026-08-18T01:00", body)])
        done, _ = analyzer.intervals(paths)
        assert done[0].issues == ("B-001",)

    def test_a_commit_closing_two_issues_splits_the_cost(self, analyzer, tmp_path):
        """⚠️ **総和が実測を超えないこと**＝両方に全額を付けない。"""
        paths = self._write(tmp_path, [
            self._resp("a", "2026-08-18T01:00"),
            self._commit("b", "2026-08-18T01:01", "fix: 束ねて直す（B-001・B-002）"),
        ])
        done, _ = analyzer.intervals(paths)
        agg = analyzer.per_issue(done)
        assert set(agg) == {"B-001", "B-002"}
        assert agg["B-001"]["往復"] == agg["B-002"]["往復"] == 1.0
        assert sum(a["往復"] for a in agg.values()) == done[0].roundtrips

    def test_the_same_issue_across_commits_is_one_item(self, analyzer, tmp_path):
        """課題 1 件＝**コミット 1 本ではない**（B-106 は 6 コミットで 1 件だった）。"""
        paths = self._write(tmp_path, [
            self._commit("a", "2026-08-18T01:00", "fix: 直す（B-001）"),
            self._resp("b", "2026-08-18T01:01"),
            self._commit("c", "2026-08-18T01:02", "fix: 直す（B-001 の 2 巡目）"),
        ])
        agg = analyzer.per_issue(analyzer.intervals(paths)[0])
        assert list(agg) == ["B-001"]
        assert agg["B-001"]["区間"] == 2 and agg["B-001"]["往復"] == 3.0

    def test_records_are_ordered_by_time_across_sessions(self, analyzer, tmp_path):
        """課題は**セッションをまたぐ**＝記録の並びでなく時刻で束ねること。"""
        for sub in ("a", "b"):
            (tmp_path / sub).mkdir(exist_ok=True)
        newer = self._write(tmp_path / "b", [
            self._resp("c", "2026-08-18T02:00"),
            self._commit("d", "2026-08-18T02:01", "fix: 直す（B-002）")], "s2.jsonl")
        older = self._write(tmp_path / "a", [
            self._resp("a", "2026-08-18T01:00"),
            self._commit("b", "2026-08-18T01:01", "fix: 直す（B-001）")], "s1.jsonl")
        done, _ = analyzer.intervals(newer + older)   # わざと新しい記録を先に渡す
        assert [iv.issues for iv in done] == [("B-001",), ("B-002",)]


class TestWorkKindAndVerification:
    """主指標③（作業構成）と④（定型検証）の数え方。"""

    def _r(self, analyzer, *tools):
        return analyzer.Response(
            mid="m", tools=[(n, i, f"t{k}") for k, (n, i) in enumerate(tools)])

    def test_editing_counts_as_implementation(self, analyzer):
        assert analyzer.classify(self._r(analyzer, ("Edit", {}))) == "実装"

    def test_running_the_suite_counts_as_review(self, analyzer):
        for cmd in ("& $env:RADIOSIM_PYTHON -m pytest -q",
                    "python buildtools/dev_check.py",
                    "git diff --check HEAD"):
            assert analyzer.classify(
                self._r(analyzer, ("Bash", {"command": cmd}))) == "レビュー", cmd

    def test_reading_and_searching_count_as_exploration(self, analyzer):
        assert analyzer.classify(self._r(analyzer, ("Read", {}), ("Grep", {}))) == "探索"

    def test_a_response_with_no_tools_is_neither(self, analyzer):
        """会話だけの往復を実装や探索に混ぜない（混ぜると③が読めなくなる）。"""
        assert analyzer.classify(self._r(analyzer)) == "その他"

    def test_implementation_wins_over_review_in_the_same_response(self, analyzer):
        """実装とテストを 1 往復でやったら「実装」＝④は別勘定なので奪われない。"""
        r = self._r(analyzer, ("Edit", {}), ("Bash", {"command": "pytest -q"}))
        assert analyzer.classify(r) == "実装"
        assert any(analyzer.is_verification(c) for c in r.commands())

    @pytest.mark.parametrize("cmd,verify", [
        ("& $env:RADIOSIM_PYTHON -m pytest tests/test_batch.py", True),
        ("python buildtools/dev_check.py --tests tests/test_x.py", True),
        ("ruff check .", True),
        ("git diff --check HEAD", True),
        ("git log --oneline", False),
        ("python main.py", False),
    ])
    def test_routine_verification_is_recognised(self, analyzer, cmd, verify):
        assert analyzer.is_verification(cmd) is verify, cmd


class TestPrimaryMetricIsCompletionCost:
    """⚠️ **主指標が並列度へ戻っていないこと**（I-103 の芯）。

    🔑 [[feedback-promote-recurring-checks]] の「開示を書く仕事は『無いことの検査』を
    対で置く」＝*主指標を入れ替えた* と書いた以上、**書いた保証をテストで持つ**。
    """

    def _report(self, analyzer, capsys, tmp_path):
        iv = TestIssueIntervals()
        paths = iv._write(tmp_path, [
            iv._resp("a", "2026-08-18T01:00", tools=[("Read", {"file_path": "x"})]),
            iv._commit("b", "2026-08-18T01:01", "fix: 直す（B-001）"),
            iv._resp("c", "2026-08-18T01:02",
                     tools=[("Bash", {"command": "pytest -q"})]),
            iv._commit("d", "2026-08-18T01:03", "fix: 直す（I-002）"),
        ])
        analyzer.report_issues(paths)
        return capsys.readouterr().out

    def test_all_four_primary_metrics_are_reported(self, analyzer, capsys, tmp_path):
        out = self._report(analyzer, capsys, tmp_path)
        for label in ("API往復数", "総入力トークン", "定型検証の往復数",
                      "実装", "探索", "レビュー"):
            assert label in out, label

    def test_parallelism_is_labelled_as_a_secondary_diagnostic(
            self, analyzer, capsys, tmp_path):
        """並列度は出してよいが、**主指標ではないと明示**されていること。"""
        out = self._report(analyzer, capsys, tmp_path)
        lines = out.splitlines()
        i = next(n for n, ln in enumerate(lines) if "平均並列度" in ln)
        assert any("補助診断" in ln for ln in lines[max(i - 1, 0):i + 1]), (
            "並列度が主指標として出ている（I-091 の降格が戻っている）")

    def test_the_numbers_move_with_the_transcript(self, analyzer, tmp_path):
        """⚠️ **構造的に固定されていないこと**（B-077 の型＝動かない指標を作らない）。"""
        iv = TestIssueIntervals()
        for sub in ("a", "b"):
            (tmp_path / sub).mkdir(exist_ok=True)
        cheap = iv._write(tmp_path / "a", [
            iv._commit("b", "2026-08-18T01:01", "fix: 直す（B-001）")])
        pricey = iv._write(tmp_path / "b", [
            iv._resp("x", "2026-08-18T01:00"),
            iv._resp("y", "2026-08-18T01:00"),
            iv._commit("b", "2026-08-18T01:01", "fix: 直す（B-001）")])
        got = [analyzer.per_issue(analyzer.intervals(p)[0])["B-001"]["往復"]
               for p in (cheap, pricey)]
        assert got == [1.0, 3.0]


# ============================================================
# ledger.py ＝ 台帳／ロードマップの「節だけを 1 回で取り出す」CLI
# ============================================================
# **なぜ在るか**（2026-08-31・ユーザー決定）＝実測で台帳へのアクセスは
# `Read` 236 + `Grep` 162 ＝ 約 400 呼び出しあり、形はほぼ「Grep で行番号を
# 探す → Read で節を取る」の 2 段だった（`experiments/codegraph_scope/`）。
# ⚠️ **見込みは 1〜2%** と分かった上で採っている（桁は変わらない）。
#
# ここで守るのは**切り出しの境界**＝節が隣の項目へ溢れる／途中で切れると、
# 「読んだつもりで別の課題を読む」形になり、**素の Read より危ない**。


class TestIssueSection:
    """`issue_section` は 1 つの項目の節をちょうど切り出す（純関数）。"""

    LEDGER = _doc(
        "## 未対応",
        "",
        "### ★ B-001: 最初の項目",
        "- ★ **状態**: **未着手**",
        "本文 A",
        "",
        "#### 内訳（小見出しは節の一部）",
        "本文 B",
        "",
        "### ★ B-002: 次の項目",
        "- ★ **状態**: **対応中**",
        "本文 C",
        "",
        "## ✅ 済んだもの（アーカイブ）",
        "",
        "### ★ B-003: 済んだ項目",
        "本文 D",
    )

    def test_it_returns_just_that_item(self, hook):
        got = hook.issue_section(self.LEDGER, "B-001")
        assert got[0].startswith("### ★ B-001:")
        assert "本文 A" in got and "本文 B" in got
        assert "本文 C" not in got, "次の項目へ溢れている"

    def test_a_deeper_heading_stays_inside(self, hook):
        got = hook.issue_section(self.LEDGER, "B-001")
        assert any(ln.startswith("#### ") for ln in got), (
            "小見出しで節が切れている＝本文の途中で打ち切られる")

    def test_a_shallower_heading_closes_the_section(self, hook):
        """アーカイブ見出し（H2）で閉じる＝節が台帳の末尾まで伸びない。"""
        got = hook.issue_section(self.LEDGER, "B-002")
        assert "本文 C" in got
        assert not any("アーカイブ" in ln for ln in got)
        assert "本文 D" not in got

    def test_it_finds_items_in_the_archive_too(self, hook):
        assert "本文 D" in hook.issue_section(self.LEDGER, "B-003")

    def test_an_unknown_id_is_empty_not_wrong(self, hook):
        """⚠️ **無い ID に「それらしい節」を返さない**＝取り違えが一番害が大きい。"""
        assert hook.issue_section(self.LEDGER, "B-999") == []

    def test_a_duplicated_id_returns_both(self, hook):
        """同じ ID が 2 か所にある事故（`duplicate_ids` の由来）を握り潰さない。"""
        dup = _doc(
            "### ★ B-072: 未対応節のほう",
            "本文 X",
            "",
            "## ✅ アーカイブ",
            "### ★ B-072: アーカイブのほう",
            "本文 Y",
        )
        got = hook.issue_section(dup, "B-072")
        assert "本文 X" in got and "本文 Y" in got, (
            "片方だけ返すと、衝突しているのに 1 件に見える")

    def test_the_real_ledger_yields_a_known_item(self, hook):
        """実データでも動く（合成だけで通す検査にしない）。"""
        lines = hook._issue_lines()
        if not lines:
            pytest.skip("ISSUES.md が無い")
        ids = hook.issue_id_headings(lines)
        assert ids, "台帳に項目が 1 件も無い"
        body = hook.issue_section(lines, ids[0])
        assert body and ids[0] in body[0]


class TestRoadmapSection:
    """`roadmap_section` は版の H2 節をちょうど切り出す（純関数）。"""

    ROADMAP = _doc(
        "# ロードマップ",
        "",
        "## 現在地",
        "表の行",
        "",
        "## 🔜 3.1 — 配布の信頼性（**次の版**）",
        "3.1 の本文",
        "",
        "### 3.1 の中の小見出し",
        "小見出しの本文",
        "",
        "## 3.2 — サポート性",
        "3.2 の本文",
    )

    def test_it_returns_just_that_version(self, memcheck):
        got = memcheck.roadmap_section(self.ROADMAP, "3.1")
        assert got[0].startswith("## 🔜 3.1")
        assert "3.1 の本文" in got and "小見出しの本文" in got
        assert "3.2 の本文" not in got, "次の版へ溢れている"

    def test_an_absent_version_is_empty(self, memcheck):
        assert memcheck.roadmap_section(self.ROADMAP, "9.9") == []

    def test_it_does_not_match_a_version_merely_mentioned(self, memcheck):
        """⛔ **見出しに版の字が含まれるかで見てはいけない**（check 17 で踏んだ穴）。

        「§次のマイナー」の見出しが本文で 2.9 を名指ししていても、2.9 の節ではない。
        """
        rm = _doc(
            "## 🆕 次のマイナー（番号未定）＝**2026-08-14 に §2.9 を切って器を再設置**",
            "器の本文",
            "",
            "## 2.9 — 本物の 2.9",
            "2.9 の本文",
        )
        got = memcheck.roadmap_section(rm, "2.9")
        assert "2.9 の本文" in got
        assert "器の本文" not in got, "版を名指ししただけの見出しに化けている"

    def test_the_real_roadmap_yields_the_current_version(self, memcheck):
        path = memcheck.MEM_DIR / "project_roadmap.md"
        if not path.exists():
            pytest.skip("ロードマップが無い")
        lines = path.read_text(encoding="utf-8").splitlines()
        versions = {v for v in (memcheck._heading_version(ln)
                                for ln in lines if ln.startswith("## ")) if v}
        assert versions, "版を宣言する H2 見出しが 1 つも無い"
        one = sorted(versions)[0]
        assert memcheck.roadmap_section(lines, one), f"§{one} の節が取れない"
