"""
webui/docs.py
=============
新しい画面の「設定」の枠の **説明書を開く**＝旧画面のメニュー「ヘルプ → ドキュメントを開く」
（I-257 の D）。描画の関数は旧 `views/launcher_menu.py` から写した（中身は変えていない）。

- 開く文書＝配布物はマニュアル（`readme_binary_filename`）・ソース実行は開発者向け文書
  （`readme_filename`）。根は `config.app_base_dir()`（配布物は `pythonw.exe` の隣）。
- 1 段目＝`markdown` で HTML にして一時フォルダへ一式書き出し、入口の HTML を `opener` で開く
  （既定のブラウザ）。2 段目＝`markdown` が無い・描けないときは `.md` を `opener` で開く
  （OS の既定のアプリ）。旧画面の 3 段目（窓の中の素の字）は器が無いので持たない。
- 文書が無ければ `FileError`（`dlg_readme_missing`）。

配布物の木には、マニュアル日英から辿れる `.md` と絵を `buildtools/build_link.py` の `doc_files`
が入れる（ステージ11f）。`markdown` は vendor に入る（B-380）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

from core import config
from core import i18n
from core import runtime_env
from webui.files import FileError

#: ドキュメントビューアが埋め込める画像（同梱物に置いてよい形式だけ）。
#: ⚠️ **図は SVG で持つ**（層構成図＝docs/images/architecture_*.svg）ので `.svg` が要る。
#: 無いと一時ディレクトリからの相対参照のまま残り、**図だけ黙って出ない**（B-081 と同型）。
_INLINE_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg",
                       ".jpeg": "image/jpeg", ".gif": "image/gif",
                       ".svg": "image/svg+xml"}


def inline_local_images(html_body: str, base_dir: str, doc_dir: str | None = None) -> str:
    """`<img src="...">` のローカル画像を data URI へ畳み込む。

    **なぜ必要か**: Tier 1 の表示は markdown を HTML に変換して**一時ディレクトリ**へ
    書き出しブラウザで開く。相対パスはその一時ディレクトリから解決されるので、
    素のままでは画像が必ず壊れる（`<base href>` を足すと今度はアンカーリンクの
    解決先が変わる）。⇒ **描画時に埋め込む**のがこのウィンドウの作法。

    ⚠️ **`logo.png` 決め打ちだったのを一般化した**（2026-08-09・I-017 でスクショを
    足したときに、決め打ちのままだと新しい画像だけ黙って壊れると分かった）
    ＝**列挙で塞ぐ穴は、次の 1 枚で開く**。

    🔑 **解決の基準は「その文書があるディレクトリ」・境界は「同梱物の根」**（`doc_dir`
    を省くと従来どおり両方 `base_dir`）。文書が `docs/` に置かれるようになったので、
    **Markdown の相対パスの意味（＝書いた文書からの相対）と揃える**必要がある
    ＝そうしないと GitHub で読むときとアプリで読むときで同じ 1 行が別の場所を指す。
    ⚠️ **境界だけは根に据え置く**＝`docs/manual_ja.md` から `../logo.png` を指すのは
    同梱物の中なので通し、そこからさらに外へ出るものは落とす。

    ⚠️ **同梱物の外は読まない**＝`..` で根の外へ出る参照と、スキームつき（`http:` /
    `data:`）はそのまま残す。マニュアルは配布物なので、描画のたびに任意の
    ローカルファイルを読める口にはしない。
    """
    import base64 as _b64
    import re as _re

    root = os.path.abspath(base_dir)
    here = os.path.abspath(doc_dir) if doc_dir else root

    def _replace(m: "_re.Match[str]") -> str:
        src = m.group(1)
        if "://" in src or src.startswith("data:") or os.path.isabs(src):
            return m.group(0)
        path = os.path.abspath(os.path.join(here, src))
        if os.path.commonpath([root, path]) != root:
            return m.group(0)                      # 同梱物の外＝触らない
        mime = _INLINE_IMAGE_TYPES.get(os.path.splitext(path)[1].lower())
        if mime is None or not os.path.exists(path):
            return m.group(0)
        with open(path, "rb") as f:
            data = _b64.b64encode(f.read()).decode("ascii")
        return f'src="data:{mime};base64,{data}"'

    return _re.sub(r'src="([^"]+)"', _replace, html_body)


#: `<a href="…">…</a>` を丸ごと拾う（Markdown 変換後のアンカーは入れ子にならない）。
_ANCHOR_RE = re.compile(r'<a\b([^>]*?)href="([^"]+)"([^>]*)>(.*?)</a>', re.DOTALL)


def _split_fragment(href: str) -> tuple[str, str]:
    path, sep, frag = href.partition("#")
    return path, (sep + frag)


def local_link_target(href: str, doc_dir: str) -> str | None:
    """**ローカル相対参照**の絶対パスを返す（外部 URL・ページ内アンカー・絶対パスは `None`）。

    解決の基準は `inline_local_images` と同じ＝**その文書のあるディレクトリ**
    （Markdown の相対パスの意味＝GitHub で読むときと同じ行き先になる）。
    """
    path, _frag = _split_fragment(href)
    if not path or "://" in href or href.startswith(("data:", "mailto:")) \
            or os.path.isabs(path):
        return None
    return os.path.abspath(os.path.join(doc_dir, path))


def is_bundled(path: str, base_dir: str) -> bool:
    """`path` が**同梱物の根の中**にあるか（境界は `inline_local_images` と同じ）。"""
    root = os.path.abspath(base_dir)
    try:
        return os.path.commonpath([root, os.path.abspath(path)]) == root
    except ValueError:                         # ドライブ違い（Windows）
        return False


def linked_local_docs(html_body: str, base_dir: str, doc_dir: str) -> list[str]:
    """本文が指している**同梱物の中の `.md`** の絶対パス（重複なし・実在するものだけ）。

    ビューアは「開いた 1 本」ではなく**そこから辿れる文書の一式**を書き出す。
    ⇒ ここが推移閉包の 1 段ぶん（呼び出し側が空になるまで回す）。
    """
    found: list[str] = []
    for m in _ANCHOR_RE.finditer(html_body):
        target = local_link_target(m.group(2), doc_dir)
        if (target is not None and is_bundled(target, base_dir)
                and target.lower().endswith(".md")
                and os.path.isfile(target) and target not in found):
            found.append(target)
    return found


def rewrite_local_links(html_body: str, base_dir: str, doc_dir: str,
                        doc_urls: dict) -> str:
    """`<a href>` のローカル参照を**開ける行き先**へ書き換える。

    **なぜ必要か**: Tier 1 の表示は一時ディレクトリへ書き出すので、相対リンクは
    そこから解決される＝**同梱文書へのリンクも `../LICENSE` も必ず開けない**
    （画像は `inline_local_images` で畳んでいたが、`<a href>` は素通しだった＝
    2026-08-13 のユーザー報告）。

    行き先の決め方は 3 つだけ:
      1. **一緒に書き出した `.md`** → その一時 HTML（`doc_urls`＝絶対パス→URL）
      2. **同梱物の中に実在するそれ以外のファイル** → `file://` の絶対 URI（OS へ）
      3. **それ以外**（同梱物の外・実在しない） → **リンクを外して字だけ残す**

    ⚠️ **3 が「開けないリンクを見せない」ための本体**＝配布物には開発者向け文書や
    ソースが入らない構成もあり得るので、**押せるのに何も起きない**を作らない
    （`×` と `＋` の置き方と同じ規則）。外部 URL とページ内アンカーは触らない。
    ⚠️ **画像（`inline_local_images`）は同梱物の外を「素通し」にするが、リンクは
    外す**＝素通しでも絵は出ないだけだが、リンクは**押せてしまう**ので扱いが違う。
    """
    def _replace(m: "re.Match[str]") -> str:
        before, href, after, text = m.groups()
        target = local_link_target(href, doc_dir)
        if target is None:
            return m.group(0)                  # 外部 URL / ページ内アンカー / 絶対パス
        _path, frag = _split_fragment(href)
        url = doc_urls.get(target)
        if url is not None:
            return f'<a{before}href="{url}{frag}"{after}>{text}</a>'
        if is_bundled(target, base_dir) and os.path.isfile(target):
            return f'<a{before}href="{Path(target).as_uri()}"{after}>{text}</a>'
        return text                            # 開けない＝リンクを外す

    return _ANCHOR_RE.sub(_replace, html_body)


#: ドキュメントビューアの体裁（1 か所＝どの文書も同じ見た目で開く）。
_DOC_STYLE = (
    "body{font-family:Arial,sans-serif;margin:40px;max-width:900px;line-height:1.6}"
    "h1,h2,h3{color:#333}"
    "code{font-family:'BIZ UDGothic','MS Gothic',Consolas,'Courier New',monospace;"
    "background:#f4f4f4;padding:2px 6px;border-radius:3px}"
    "pre{font-family:'BIZ UDGothic','MS Gothic',Consolas,'Courier New',monospace;"
    "background:#f4f4f4;padding:12px;border-radius:4px;overflow-x:auto;line-height:1.5}"
    "img{width:100%;height:auto}"
    "table{border-collapse:collapse;width:100%}"
    "th,td{border:1px solid #ddd;padding:8px;text-align:left}"
    "th{background:#455a64;color:white}"
)


def _doc_page(body: str, lang: str) -> str:
    return (f'<!DOCTYPE html><html lang="{lang}">'
            f'<head><meta charset="UTF-8"><style>{_DOC_STYLE}</style></head>'
            f'<body>{body}</body></html>')


def _out_name(src: str, base_dir: str) -> str:
    """同梱物の中での位置から、一時ディレクトリでのファイル名を決める（衝突しない）。"""
    rel = os.path.relpath(src, os.path.abspath(base_dir))
    return re.sub(r"[^0-9A-Za-z_.-]", "_", rel)[:-3] + ".html"


def render_doc_site(entry_path: str, base_dir: str, out_dir: str,
                    to_html, lang: str = "ja") -> str:
    """開く文書と**そこから辿れる同梱文書の一式**を書き出し、入口の HTML を返す。

    **1 本だけ書き出すと、文書間のリンクは必ず死ぬ**（一時ディレクトリには相手が
    いない）。⇒ 辿れる `.md` を推移的に集めて同じ体裁で書き出し、リンクは互いの
    出力先へ向け直す＝**アプリで開いても GitHub で読むのと同じように行き来できる**。

    `to_html` は Markdown → HTML の変換（呼び出し側が注入する＝この関数は
    `markdown` ライブラリに依存しない＝テストから素の文字列で回せる）。
    """
    base = os.path.abspath(base_dir)
    bodies: dict = {}
    pending = [os.path.abspath(entry_path)]
    while pending:
        src = pending.pop(0)
        if src in bodies:
            continue
        with open(src, encoding="utf-8") as f:
            body = to_html(f.read())
        # 画像は data URI へ畳む（<base href> を足さない＝アンカーを壊さない）。
        bodies[src] = inline_local_images(body, base, os.path.dirname(src))
        pending.extend(linked_local_docs(bodies[src], base, os.path.dirname(src)))

    # **名前を全部決めてから**書き換える（相互リンクが処理順に依存しないように）。
    names = {src: _out_name(src, base) for src in bodies}
    for src, body in bodies.items():
        body = rewrite_local_links(body, base, os.path.dirname(src), names)
        with open(os.path.join(out_dir, names[src]), "w", encoding="utf-8") as f:
            f.write(_doc_page(body, lang))
    return os.path.join(out_dir, names[os.path.abspath(entry_path)])


def _slugify(value: str, sep: str) -> str:
    """GitHub 互換のアンカー ID 生成。文字・数字は保持し句読点は除去（旧と同じ）。"""
    import unicodedata as _ud
    result = []
    for ch in value.lower():
        cat = _ud.category(ch)
        if cat.startswith("L") or cat.startswith("N") or cat == "Pc":
            result.append(ch)
        elif ch in (" ", "\t"):
            result.append(sep)
    return "".join(result)


def doc_path(base_dir: "str | None" = None, packaged: "bool | None" = None) -> str:
    """開く文書の絶対パス（あるかどうかは見ない）。"""
    base = base_dir or config.app_base_dir()
    if packaged is None:
        packaged = runtime_env.is_packaged()
    key = "readme_binary_filename" if packaged else "readme_filename"
    return os.path.normpath(os.path.join(base, i18n.t(key)))


def _markdown_to_html() -> "Callable[[str], str]":
    """`markdown` の変換（無ければ ImportError＝2 段目へ）。"""
    import markdown as _md
    from markdown.extensions.toc import TocExtension

    def to_html(text: str) -> str:
        return _md.markdown(text, extensions=["tables", "fenced_code",
                                              TocExtension(slugify=_slugify)])
    return to_html


def open_docs(opener: "Callable[[str], None]", *, base_dir: "str | None" = None,
              packaged: "bool | None" = None,
              to_html: "Callable[[str], str] | None" = None) -> str:
    """説明書を開く。開いたもの（入口の HTML か `.md`）のパスを返す。"""
    base = base_dir or config.app_base_dir()
    path = doc_path(base, packaged)
    if not os.path.isfile(path):
        raise FileError(i18n.t("dlg_readme_missing"))
    try:
        convert = to_html or _markdown_to_html()
        import atexit
        import shutil
        import tempfile
        out_dir = tempfile.mkdtemp(prefix="radiosim_doc_")
        atexit.register(lambda d=out_dir: shutil.rmtree(d, ignore_errors=True))
        entry = render_doc_site(path, base, out_dir, convert, i18n.t("html_lang"))
    except ImportError:
        entry = path
    except Exception as ex:                                  # 描けない＝素の .md へ落とす
        config.logger.warning("README markdown render failed: %s", ex)
        entry = path
    opener(entry)
    return entry
