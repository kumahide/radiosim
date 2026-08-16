"""
tests/test_i18n_external.py
===========================
**利用者が足す言語**（`lang/<コード>.json`）のゲート。

なぜ要るか
----------
`t()` は前からキー単位で英語へフォールバックするので、外部の表は「上書きできた分
だけ」効く——**仕組みは軽いが、軽いぶん壊れ方が静か**になる。いちばん危ないのは
**差し込みの取り違え**で、実装の文言 429 キーのうち **57 キーが `{…}` を持つ**。
訳す人が括弧を落とすと `str.format` が `KeyError` を投げ、**その画面を開いた瞬間に
アプリが止まる**（B-025 と同型＝黙って壊れる形を作らない）。

⇒ 読み込みの時点で英語と突き合わせ、**合わないキーは採用しない**。

ゲートの壊れ方 3 点（[[feedback-promote-recurring-checks]]）
-----------------------------------------------------------
- **一度も落ちない**：`test_a_broken_placeholder_would_crash_the_screen` が
  「弾かなければ実際に `KeyError` になる」ことを毎回実演する（検査を外した世界を
  そのまま作って見せる形）。判定を素通しにすると採用側の 4 本が落ちる。
- **毎回鳴る**：正しい訳・未訳・空のディレクトリでは 1 件も鳴らないことを見る。
- **間違ったものを要求している**：**全キーの翻訳を求めない**（未訳は英語で正しい）。
  求めるのは「採用した訳が壊れていないこと」だけ。
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import i18n


@pytest.fixture
def lang_dir(tmp_path, monkeypatch):
    """外部言語を置くディレクトリ。**登録は毎回まっさらに戻す。**

    ⚠️ `_STRINGS` はモジュール全体の状態なので、後始末をしないと**走らせる順で
    結果が変わる**（B-054 で実際に踏んだ形）。
    """
    before = dict(i18n._STRINGS)
    yield tmp_path
    i18n._STRINGS.clear()
    i18n._STRINGS.update(before)
    i18n.set_lang("en")
    i18n.load_external(str(tmp_path / "does-not-exist"))   # 報告も空へ戻す


def _write(dir_path, code: str, table: dict) -> None:
    (dir_path / f"{code}.json").write_text(
        json.dumps(table, ensure_ascii=False), encoding="utf-8")


# ============================================================
# 1. 採用する側
# ============================================================

def test_a_plain_translation_is_used(lang_dir):
    """訳せたキーはその言語で出ること。"""
    _write(lang_dir, "fr", {"_name": "Français", "btn_run": "Exécuter"})
    i18n.load_external(str(lang_dir))
    i18n.set_lang("fr")
    assert i18n.t("btn_run") == "Exécuter"


def test_untranslated_keys_fall_back_to_english(lang_dir):
    """**全部を訳す義務は無い**＝未訳は英語で出る（版でキーが増えても壊れない）。"""
    _write(lang_dir, "fr", {"btn_run": "Exécuter"})
    i18n.load_external(str(lang_dir))
    i18n.set_lang("fr")
    assert i18n.t("btn_cancel") == i18n._STRINGS["en"]["btn_cancel"]


def test_the_language_appears_in_the_menu_list(lang_dir):
    """メニューに出す名前は**その言語自身の字**（読めない言語の画面で自分を探せる）。"""
    _write(lang_dir, "fr", {"_name": "Français"})
    i18n.load_external(str(lang_dir))
    assert ("fr", "Français") in i18n.external_languages()


def test_a_file_without_a_name_falls_back_to_its_code(lang_dir):
    """`_name` が無くてもメニューには出る（コードで表示）。"""
    _write(lang_dir, "de", {"btn_run": "Ausführen"})
    i18n.load_external(str(lang_dir))
    assert ("de", "de") in i18n.external_languages()


# ============================================================
# 2. 採用しない側（＝この項目の芯）
# ============================================================

def test_a_translation_with_different_placeholders_is_rejected(lang_dir):
    """🔴 **差し込みを落とした訳は採用しない**（採用すると画面が落ちる）。"""
    key = "status_prefetch_pct"                     # en は `{pct}` を持つ
    assert "{pct}" in i18n._STRINGS["en"][key]
    _write(lang_dir, "fr", {key: "Préchargement…"})  # 差し込みが無い
    i18n.load_external(str(lang_dir))
    i18n.set_lang("fr")
    assert i18n.t(key) == i18n._STRINGS["en"][key], "壊れた訳を採用してしまった"


def test_a_broken_placeholder_would_crash_the_screen():
    """**弾かなければ何が起きるか**を毎回実演する（この検査の存在理由）。

    ⚠️ ここは `validate_external` を通さずに `format` を試す＝*検査を外した世界*。
    これが `KeyError` を投げなくなったら、そもそも守る必要が無くなったということ。
    """
    broken = "Préchargement… {pourcent}%"      # 訳者が差し込み名を訳してしまった形
    with pytest.raises(KeyError):
        broken.format(pct=50)


def test_artifact_keys_are_not_open(lang_dir):
    """⛔ **成果物（レポート・KML）の語は開かない**＝出力契約が利用者ごとに変わる。

    これは 2.8 に収まる条件そのもの（開いた瞬間 `+1.0` の話になる）。
    """
    _write(lang_dir, "fr", {"html_col_note": "Remarque", "pl_diff_model": "Modèle"})
    i18n.load_external(str(lang_dir))
    i18n.set_lang("fr")
    assert i18n.t("html_col_note") == i18n._STRINGS["en"]["html_col_note"]
    assert i18n.t("pl_diff_model") == i18n._STRINGS["en"]["pl_diff_model"]


#: 成果物（HTML レポート・断面図 PNG・グラフ）を組み立てるモジュール。
#: ⚠️ **`report/` を丸ごと走査しない**＝同じ層に居る `batch.py` / `multihop.py` /
#: `project.py` は**画面に出す検証メッセージ**（`verr_*` 等）も持っており、それらは
#: 開いてよい語だから（実測＝走査対象を層で切ると 20 キーを誤って締め出す）。
_ARTIFACT_MODULES = (
    "report_path.py", "report_multihop.py", "report_scenario.py",
    "report_summary.py", "report_common.py", "report_map.py", "map_graphics.py",
)
_T_CALL = re.compile(r"""\bt\(\s*["']([A-Za-z0-9_]+)["']""")


def test_every_key_that_reaches_an_artifact_is_closed_to_external_translation():
    """🔴 **締め出しの一覧が実装からずれていないこと**（B-101）。

    **手書きの一覧は必ずずれる**（B-090＝地図モードの一覧が 3 モード時代のまま
    残っていた）。⇒ *名前の形*を信じず、**成果物を組み立てるモジュールが実際に
    引いているキー**を走査して、その全部が `validate_external` に却下されることを
    要求する。⇒ レポートに語を 1 つ足した日に、締め出しを忘れればここが落ちる。

    ⚠️ **却下の理由まで見る**（`artifact`）＝「たまたま英語に無いキーだから
    却下された」のような別の理由で緑になるのを防ぐ。
    """
    repo = os.path.join(os.path.dirname(__file__), "..", "report")
    keys: dict = {}
    for name in _ARTIFACT_MODULES:
        path = os.path.join(repo, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for key in _T_CALL.findall(fh.read()):
                keys.setdefault(key, set()).add(name)
    assert keys, "成果物モジュールから 1 つもキーを拾えていない（走査が壊れている）"

    table = {k: "X" for k in keys}
    _accepted, rejected = i18n.validate_external(table)
    reasons = dict(rejected)
    leaked = sorted(k for k in keys if reasons.get(k) != "artifact")
    assert not leaked, (
        "成果物へ出るのに外部訳で上書きできるキーがある＝出力契約が利用者ごとに"
        f"変わる: {[(k, sorted(keys[k])) for k in leaked]}"
    )


def test_a_translation_with_unbalanced_braces_is_rejected(lang_dir):
    """🔴 **書式文字列として壊れた訳は採用しない**（B-103）。

    差し込み名の集合だけを比べると**対になっていない括弧は差に現れない**ので
    素通りし、表示の瞬間に `ValueError` で止まっていた。
    """
    key = "lang_ext_rejected"                       # en は `{n}` を 1 つ持つ
    assert "{n}" in i18n._STRINGS["en"][key]
    _write(lang_dir, "fr", {key: "Sauté {n} traductions {"})
    i18n.load_external(str(lang_dir))
    i18n.set_lang("fr")
    assert i18n.t(key) == i18n._STRINGS["en"][key], "壊れた書式の訳を採用してしまった"


def test_unbalanced_braces_would_crash_the_screen():
    """**弾かなければ何が起きるか**を実演する（上の検査の存在理由）。

    ⚠️ `KeyError` ではなく `ValueError`＝**既存の実演では捕まらない型**だった。
    """
    broken = "Sauté {n} traductions {"
    with pytest.raises(ValueError):
        broken.format(n=3)


def test_unknown_keys_are_rejected(lang_dir):
    """英語に無いキーは採用しない（綴り違いを黙って抱え込まない）。"""
    accepted, rejected = i18n.validate_external({"btn_runn": "Exécuter"})
    assert accepted == {} and rejected == [("btn_runn", "unknown")]


def test_non_text_values_are_rejected(lang_dir):
    """文字列でない値は採用しない（`t()` の戻り値の型を守る）。"""
    accepted, rejected = i18n.validate_external({"btn_run": 42})
    assert accepted == {} and rejected == [("btn_run", "not_text")]


def test_builtin_languages_cannot_be_overridden(lang_dir):
    """⛔ 同梱の `ja` / `en` は外部から差し替えられないこと。

    差し替えられると、**用語集ゲートが守っている画面語彙が黙って外れる**
    （`docs/glossary.md` は `i18n.py` の字を見ており、外部ファイルは見ない）。
    """
    before = i18n._STRINGS["ja"]["btn_run"]
    _write(lang_dir, "ja", {"btn_run": "走れ"})
    i18n.load_external(str(lang_dir))
    assert i18n._STRINGS["ja"]["btn_run"] == before


# ============================================================
# 3. 壊れた入力でも起動を止めない
# ============================================================

def test_a_broken_file_does_not_stop_the_others(lang_dir):
    """1 本が壊れていても、他の言語は読めること（置き損ねで起動不能にしない）。"""
    (lang_dir / "bad.json").write_text("{ this is not json", encoding="utf-8")
    _write(lang_dir, "fr", {"btn_run": "Exécuter"})
    i18n.load_external(str(lang_dir))
    assert ("fr", "fr") in i18n.external_languages()
    assert any(code == "bad" and rej for code, _n, _ok, rej
               in i18n.external_reports())


def test_a_missing_directory_is_silent(lang_dir):
    """置き場が無いのが普通の状態＝何も鳴らないこと（毎回鳴るゲートにしない）。"""
    assert i18n.load_external(str(lang_dir / "nope")) == []


def test_an_empty_directory_is_silent(lang_dir):
    """空でも同じ（`lang/` を作っただけの人に何も言わない）。"""
    assert i18n.load_external(str(lang_dir)) == []


# ============================================================
# 4. 報告（＝画面で言うための材料）
# ============================================================

def test_the_language_menu_actually_offers_the_added_language(lang_dir):
    """**配線のゲート**＝読み込めても、メニューに出なければ利用者は選べない。

    ⚠️ ここまでの検査は全部 `i18n` の中で閉じている＝**「実装したが画面に出ない」を
    1 つも捕まえられない**。窓を作って実際のメニュー項目を読む 1 本を置く。
    """
    pytest.importorskip("tkinter")
    from tests.conftest import make_themed_root                # noqa: PLC0415
    from views.launcher import SimLauncher                     # noqa: PLC0415

    _write(lang_dir, "fr", {"_name": "Français"})
    i18n.load_external(str(lang_dir))
    root = make_themed_root()
    root.withdraw()
    try:
        app = SimLauncher(root, lambda _t: None)
        menu = root.nametowidget(root.cget("menu"))
        labels = _menu_labels(menu)
        assert "Français" in labels, (
            f"言語メニューに外部言語が出ていない（読み込めても選べない）: {labels}"
        )
        assert app is not None
    finally:
        root.destroy()


def test_an_added_language_can_be_restored_from_a_settings_file(lang_dir, tmp_path,
                                                                monkeypatch):
    """**設定ファイルからも外部言語を復元できること**（B-086）。

    ⚠️ 上の 1 本（メニューに出る）が緑でも、これは落ちうる＝**選べるかどうかを
    判定する場所が 2 つ**あり、片方だけが外部言語を知っていた。取り込み側は
    `("en", "ja")` を直書きしており、`lang: "fr"` の設定を読み戻すと**言語だけ
    黙って捨てられる**（他の項目が 1 つでもあれば成功表示まで出るので、利用者に
    は「取り込めた」ように見える）。

    🔑 **判定の口を `i18n.available_languages()` へ寄せた**＝`set_lang()` と同じ
    表を見るので、片方だけ古くなることが原理的に起きない。
    """
    pytest.importorskip("tkinter")
    from core import config                                    # noqa: PLC0415
    from tests.conftest import make_themed_root                # noqa: PLC0415
    from views import launcher_menu                            # noqa: PLC0415
    from views.launcher import SimLauncher                     # noqa: PLC0415

    _write(lang_dir, "fr", {"_name": "Français", "btn_run": "Exécuter"})
    i18n.load_external(str(lang_dir))

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"lang": "fr", "theme": "dark"}), encoding="utf-8")

    root = make_themed_root()
    root.withdraw()
    try:
        app = SimLauncher(root, lambda _t: None)
        monkeypatch.setattr(launcher_menu.filedialog, "askopenfilename",
                            lambda **_kw: str(settings))
        monkeypatch.setattr(config, "save_app", lambda _c: None)
        monkeypatch.setattr(type(app), "_alert",
                            lambda _s, _t, _m: None, raising=False)
        app._on_load_app_settings()
        assert app.config.get("lang") == "fr", (
            "設定ファイルの外部言語が取り込まれていない"
            f"（lang={app.config.get('lang')!r}）＝メニューでは選べるのに、"
            "設定として保存したものは読み戻せない（B-086）。"
        )
    finally:
        root.destroy()


def _menu_labels(menu) -> list:
    """メニュー階層をたどって全項目のラベルを集める（カスケードの中まで）。"""
    out: list = []
    for i in range(menu.index("end") + 1 if menu.index("end") is not None else 0):
        if menu.type(i) in ("separator", "tearoff"):
            continue
        out.append(str(menu.entrycget(i, "label")))
        if menu.type(i) == "cascade":
            child = menu.nametowidget(menu.entrycget(i, "menu"))
            out.extend(_menu_labels(child))
    return out


def test_rejections_are_reported_for_the_screen(lang_dir):
    """採用しなかったことが**報告に残る**こと（黙って落とさない＝B-025 と同型）。

    画面へ出すのはランチャーの仕事だが、**材料が無ければ言えない**のでここで縛る。
    """
    _write(lang_dir, "fr", {"_name": "Français",
                            "status_prefetch_pct": "壊れた訳",
                            "btn_run": "Exécuter"})
    i18n.load_external(str(lang_dir))
    code, name, accepted, rejected = i18n.external_reports()[0]
    assert (code, name, accepted) == ("fr", "Français", 1)
    assert [k for k, _r in rejected] == ["status_prefetch_pct"]
