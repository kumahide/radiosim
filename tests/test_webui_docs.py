"""
tests/test_webui_docs.py
========================
新しい画面の「説明書を開く」（`webui/docs.py`・I-257 の D）。

画像・リンク・一式の書き出しの 4 つは旧 `tests/test_docs_consistency.py` の試験を写した
（旧画面の撤去で消えていた・中身は同じ）。
"""

import re
from pathlib import Path

import pytest

from webui import docs
from webui.files import FileError

ROOT = Path(__file__).resolve().parent.parent


def test_the_manual_viewer_inlines_any_bundled_image_not_just_the_logo(tmp_path):
    """描画時の data URI 化が**画像 1 枚の決め打ちでない**こと。

    ⚠️ **同梱物の外を読まないこと**は、**実在するファイル**で確かめる（無いパスだと
    「無いから素通し」で緑になり、防御を外しても落ちない）。
    """
    png = (ROOT / "logo.png").read_bytes()
    base = tmp_path / "bundle"
    (base / "docs" / "images").mkdir(parents=True)
    (base / "logo.png").write_bytes(png)
    (base / "docs" / "images" / "shot.png").write_bytes(png)
    (tmp_path / "outside.png").write_bytes(png)          # **実在する**同梱物の外
    (base / "docs" / "images" / "arch.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")

    body = ('<img src="logo.png">'
            '<img src="docs/images/shot.png">'
            '<img src="docs/images/arch.svg">'
            '<img src="https://example.com/x.png">'
            '<img src="../outside.png">')
    out = docs.inline_local_images(body, str(base))

    assert out.count("data:image/png;base64,") == 2, "同梱画像が 2 枚とも畳まれていない"
    assert "data:image/svg+xml;base64," in out, "同梱の SVG 図が畳まれていない"
    assert 'src="https://example.com/x.png"' in out, "外部 URL を触っている"
    assert 'src="../outside.png"' in out, "同梱物の外のファイルを読んでいる"


def test_the_manual_viewer_resolves_images_from_the_document_not_the_root(tmp_path):
    """解決の基準は**その文書のあるディレクトリ**・境界は根のまま（1 つの試験で両方を見る）。"""
    png = (ROOT / "logo.png").read_bytes()
    base = tmp_path / "bundle"
    (base / "docs" / "images").mkdir(parents=True)
    (base / "logo.png").write_bytes(png)
    (base / "docs" / "images" / "shot.png").write_bytes(png)
    (tmp_path / "outside.png").write_bytes(png)

    body = ('<img src="images/shot.png">'
            '<img src="../logo.png">'
            '<img src="../../outside.png">')
    out = docs.inline_local_images(body, str(base), str(base / "docs"))

    assert out.count("data:image/png;base64,") == 2, "文書のあるディレクトリを基準に解決していない"
    assert 'src="../../outside.png"' in out, "同梱物の外のファイルを読んでいる"


def test_the_manual_viewer_rewrites_links_to_places_that_can_be_opened(tmp_path):
    """`<a href>` の 3 分岐（一緒に書き出した文書 / 同梱ファイル / 開けない＝字だけ残す）。"""
    base = tmp_path / "bundle"
    (base / "docs").mkdir(parents=True)
    (base / "docs" / "manual.md").write_text("x", encoding="utf-8")
    (base / "LICENSE").write_text("MIT", encoding="utf-8")
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")   # **実在する**外
    names = {str(base / "docs" / "manual.md"): "docs_manual.html"}

    body = ('<a href="manual.md#使い方">M</a>'
            '<a href="../LICENSE">L</a>'
            '<a href="../../outside.md">O</a>'
            '<a href="missing.md">X</a>'
            '<a href="https://example.com">W</a>'
            '<a href="#概要">A</a>')
    out = docs.rewrite_local_links(body, str(base), str(base / "docs"), names)

    assert 'href="docs_manual.html#使い方"' in out
    assert 'href="file:///' in out and "LICENSE" in out
    assert "outside.md" not in out and "missing.md" not in out, "開けないリンクが残っている"
    assert "O" in out and "X" in out, "リンクを外すときに字まで消している"
    assert 'href="https://example.com"' in out and 'href="#概要"' in out


def _to_html(text):                         # markdown ライブラリを使わない最小の変換
    return re.sub(r"\[([^\]]*)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)


def test_the_manual_viewer_writes_every_document_it_can_reach(tmp_path):
    """辿れる文書が**一式**書き出され、相互リンク（A ⇄ B）で止まり、両方向で開けること。"""
    base = tmp_path / "bundle"
    (base / "docs").mkdir(parents=True)
    (base / "docs" / "a.md").write_text("[to b](b.md)", encoding="utf-8")
    (base / "docs" / "b.md").write_text("[to a](a.md)", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    entry = docs.render_doc_site(str(base / "docs" / "a.md"), str(base), str(out), _to_html, "ja")
    written = sorted(p.name for p in out.iterdir())
    assert len(written) == 2, f"辿れる文書が一式そろっていない: {written}"
    for name in written:
        href = re.search(r'href="([^"]+)"', (out / name).read_text(encoding="utf-8"))
        assert href and (out / href.group(1)).exists()
    assert Path(entry).name in written


@pytest.mark.parametrize("packaged,key", [(True, "readme_binary_filename"),
                                         (False, "readme_filename")])
def test_the_document_follows_the_distribution(tmp_path, packaged, key):
    from core import i18n
    target = tmp_path / i18n.t(key)
    target.parent.mkdir(parents=True)
    target.write_text("[x](https://example.com)", encoding="utf-8")
    opened = []
    entry = docs.open_docs(opened.append, base_dir=str(tmp_path), packaged=packaged,
                           to_html=_to_html)
    assert opened == [entry] and entry.endswith(".html")
    assert 'href="https://example.com"' in Path(entry).read_text(encoding="utf-8")


def test_without_markdown_the_plain_file_opens(tmp_path, monkeypatch):
    from core import i18n
    target = tmp_path / i18n.t("readme_filename")
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")

    def missing():
        raise ImportError("markdown")
    monkeypatch.setattr(docs, "_markdown_to_html", missing)
    opened = []
    docs.open_docs(opened.append, base_dir=str(tmp_path), packaged=False)
    assert opened == [str(target)]


def test_a_missing_document_says_so(tmp_path):
    from core import i18n
    opened = []
    with pytest.raises(FileError, match=re.escape(i18n.t("dlg_readme_missing"))):
        docs.open_docs(opened.append, base_dir=str(tmp_path), packaged=True)
    assert opened == []


def test_the_real_markdown_renders_the_repository_document(tmp_path, monkeypatch):
    """本物の `markdown` で開発者向け文書が描ける（拡張の指定が今の版で通る）。"""
    pytest.importorskip("markdown")
    import tempfile
    monkeypatch.setattr(tempfile, "mkdtemp", lambda prefix="": str(tmp_path))
    opened = []
    entry = docs.open_docs(opened.append, base_dir=str(ROOT), packaged=False)
    assert entry.endswith(".html") and "<h1" in Path(entry).read_text(encoding="utf-8")
