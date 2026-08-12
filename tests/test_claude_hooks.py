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
import json
import os
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
            "### ★ B-002: 版が 2 つ", "", "- ★ **状態**: 未着手（行き先＝2.8。2.7 では直さない）",
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

    def test_two_versions_in_one_state_line_go_to_ambiguous(self, hook):
        """版が 2 つ見えるものは**人に読ませる**（機械で当てにいかない）。

        実データ＝B-065 の状態欄は「行き先＝2.8」と「2.7 で直さない理由」を
        両方含む。片方を選ぶ実装は、選び方を間違えても緑になる。
        """
        doc = _doc("### ★ B-065: t", "",
                   "- ★ **状態**: 未着手（**行き先＝2.8**。2.7 で直さない理由は下）")
        audit = hook.assignment_audit(doc)
        assert audit["ambiguous"] == ["B-065"] and audit["undeclared"] == []

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
