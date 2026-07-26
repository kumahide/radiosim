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
import os
import sys

import pytest

_HOOK_DIR = os.path.join(os.path.dirname(__file__), "..", ".claude")
_HOOK_PATH = os.path.abspath(os.path.join(_HOOK_DIR, "session_context.py"))

pytestmark = pytest.mark.skipif(
    not os.path.exists(_HOOK_PATH),
    reason=".claude/ は git-ignore（CI には存在しない）。ローカルのみ検証する。",
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


# ============================================================
# 済の裏取り（対応版＋コミット参照。18件中4件しか守られていなかった規則）
# ============================================================
class TestDoneEvidence:
    @pytest.mark.parametrize("resp", [
        "2.4b3 / `cd1d360`。配色をテーマから取る。",
        "2.4b2 / `d669d66`（PR#44 `dba885e`）。",
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
                "2.4RC2 / `49fc90c`。旧記述「未コミット／ビルドは RC2 の配布パスで」は"
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

    def test_non_index_lines_are_ignored(self, memcheck):
        """見出しや説明文は対象外（索引エントリだけを見る）。"""
        assert memcheck.check_index_line_length(["## " + "あ" * 500]) == []

    def test_real_index_is_clean(self, memcheck):
        """実データで誤検知ゼロ（ノイズを出すゲートは読まれなくなる）。"""
        assert memcheck.check_memory_index_lines() == []
