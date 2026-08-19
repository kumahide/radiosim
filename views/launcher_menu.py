"""
views/launcher_menu.py
======================
ランチャーの**メニューバーと、そこからしか呼ばれない操作**（`SimLauncher` の Mixin）。

テーマ・言語・プロキシ・設定の読み込み・キャッシュ削除・バージョン情報・ドキュメント表示。

⚠️ **これは `SimLauncher` の一部**であって独立した部品ではない（`self.root` /
`self.config` / `self.entries` を共有する）。分けたのは*読む単位*を小さくするため
で、依存を切ったわけではない。切り出しは 2.7 スライス A（メソッド本文は 1 文字も
変えていない＝「移動だけ」）。
"""

import json
import os
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import TYPE_CHECKING, Callable

from core import config
from core import dem
from core import failure
from core import i18n
from core import simulation as sim
from core import version
from views import dialogs
from views import theme

if TYPE_CHECKING:
    from views.map_window import MapWindow


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
    解決先が変わる）。⇒ **描画時に埋め込む**のがこの窓の作法。

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


class _MenuMixin:
    # 宿主（`SimLauncher`）から借りている面の宣言。**型検査のときだけ**存在する
    # （実行時は 1 文字も定義しない）。理由は
    # [views/map_picks.py](map_picks.py) の同じブロックに書いた（B-049）。
    if TYPE_CHECKING:
        root: tk.Tk
        config: dict
        _on_theme: Callable[[str], None]
        _map_win: "MapWindow"

        def _alert(self, title: str, message: str) -> None: ...
        def _confirm(self, title: str, message: str) -> bool: ...
        def _apply_sim_config(self, conf: dict) -> None: ...
        def _on_coord_format_change(self) -> None: ...
        def _on_open_project(self) -> None: ...
        def _on_save_project(self) -> None: ...
        def _on_open_results(self) -> None: ...

    def _build_menu(self) -> None:
        # tk.Menu は sv_ttk（ttk 専用）のテーマに追従しないため、生成した全メニューを
        # 保持し、テーマ変更ごとに配色を明示適用する（_apply_menu_theme・B-004）。
        self._themed_menus: list[tk.Menu] = []
        menubar = tk.Menu(self.root)
        self._themed_menus.append(menubar)

        # 「ファイル」＝**ファイル／OS へ出る**操作の置き場（I-030）。ボタン群から
        # 移してきた 2 個で、どちらも「アプリの外の世界へ出る」もの。⚠️ 実行直後に
        # 結果が要る動線は**完了ダイアログの「保存先を開く」**が担う（メニューを
        # 2 階層たどらせない）＝恒久ボタンを消した代わりの受け皿はそちら。
        file_menu = tk.Menu(menubar, tearoff=False)
        self._themed_menus.append(file_menu)
        # プロジェクト（`.rsproj`）＝**入力一式を 1 つに束ねる**唯一の口。
        # ⚠️ ボタンは足さない（ボタン列は FHD 100% の高さ予算をぎりぎりで
        # 使い切っており、4 つ目で 2 列×2 行へ組み直した直後＝B-021）。
        file_menu.add_command(
            label   = i18n.t("menu_open_project"),
            command = self._on_open_project,
        )
        file_menu.add_command(
            label   = i18n.t("menu_save_project"),
            command = self._on_save_project,
        )
        file_menu.add_separator()
        file_menu.add_command(
            label   = i18n.t("menu_load_settings"),
            command = self._on_load_settings,
        )
        file_menu.add_command(
            label   = i18n.t("menu_open_results"),
            command = self._on_open_results,
        )
        menubar.add_cascade(label=i18n.t("menu_file"), menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=False)
        self._themed_menus.append(settings_menu)

        theme_menu = tk.Menu(settings_menu, tearoff=False)
        self._themed_menus.append(theme_menu)
        self._theme_var = tk.StringVar(value=self.config.get("theme", "system"))
        for label, value in [
            (i18n.t("menu_system"), "system"),
            (i18n.t("menu_light"),  "light"),
            (i18n.t("menu_dark"),   "dark"),
        ]:
            theme_menu.add_radiobutton(
                label    = label,
                variable = self._theme_var,
                value    = value,
                command  = lambda v=value: self._on_theme_select(v),
            )
        settings_menu.add_cascade(label=i18n.t("menu_theme"), menu=theme_menu)

        lang_menu = tk.Menu(settings_menu, tearoff=False)
        self._themed_menus.append(lang_menu)
        self._lang_var = tk.StringVar(value=self.config.get("lang", "en"))
        # 同梱の 2 つ＋利用者が `lang/` へ置いた分。⚠️ **外部の表示名は i18n を
        # 通さない**＝その言語自身の字（`Français`）をファイルが名乗るのが正しく、
        # 訳しようがない（`ALLOWED_LITERALS` の話ではなく、値がデータ）。
        for label, value in [
            (i18n.t("lang_en"), "en"),
            (i18n.t("lang_ja"), "ja"),
            *[(name, code) for code, name in i18n.external_languages()],
        ]:
            lang_menu.add_radiobutton(
                label    = label,
                variable = self._lang_var,
                value    = value,
                command  = lambda v=value: self._on_lang_select(v),
            )
        settings_menu.add_cascade(label=i18n.t("menu_language"), menu=lang_menu)

        coord_fmt_menu = tk.Menu(settings_menu, tearoff=False)
        self._themed_menus.append(coord_fmt_menu)
        self._coord_fmt_var = tk.StringVar(value=self.config.get("coord_format", "dd"))
        for value in ("dd", "dms"):
            coord_fmt_menu.add_radiobutton(
                label    = i18n.t(f"coord_fmt_{value}"),
                variable = self._coord_fmt_var,
                value    = value,
                command  = self._on_coord_format_change,
            )
        settings_menu.add_cascade(label=i18n.t("lbl_coord_format"), menu=coord_fmt_menu)

        settings_menu.add_separator()
        settings_menu.add_command(
            label   = i18n.t("menu_proxy_settings"),
            command = self._on_proxy_settings,
        )
        settings_menu.add_command(
            label   = i18n.t("menu_load_app_settings"),
            command = self._on_load_app_settings,
        )
        settings_menu.add_separator()
        settings_menu.add_command(
            label   = i18n.t("menu_delete_all_cache"),
            command = self._on_delete_all_cache,
        )
        menubar.add_cascade(label=i18n.t("menu_settings"), menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        self._themed_menus.append(help_menu)
        help_menu.add_command(
            label   = i18n.t("menu_open_readme"),
            command = self._on_open_readme,
        )
        help_menu.add_separator()
        help_menu.add_command(
            label   = i18n.t("menu_about"),
            command = self._on_about,
        )
        menubar.add_cascade(label=i18n.t("menu_help"), menu=help_menu)

        self.root.configure(menu=menubar)

        # 現在テーマを即反映し、以降のテーマ変更は <<ThemeChanged>> で拾う。これで
        # メニューからの明示切替も、system 連動（darkdetect の OS 追従）も一括対応。
        self._apply_menu_theme()
        self.root.bind("<<ThemeChanged>>", self._apply_menu_theme, add="+")

    def _apply_menu_theme(self, _event: object = None) -> None:
        """tk.Menu へ現在のテーマ色を明示適用する（B-004・B-008）。

        sv_ttk は ttk ウィジェットのみ再スタイルし、ネイティブ tk.Menu は追従しない
        ため、選択インジケータ（✓）もカスケードの ▶ も既定色のままだと背景と同化
        する。色の出所と配色の決め方は [views/theme.py](views/theme.py)。
        """
        theme.apply_menu_theme(self._themed_menus, self.root)

    def _on_theme_select(self, mode: str) -> None:
        self.config["theme"] = mode
        self._on_theme(mode)
        config.save_app(self.config)

    def _on_lang_select(self, lang: str) -> None:
        self.config["lang"] = lang
        config.save_app(self.config)
        self._alert(i18n.t("menu_language"), i18n.t("lang_changed_msg"))

    def _on_proxy_settings(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.transient(self.root)
        dlg.title(i18n.t("dlg_proxy_title"))
        dlg.resizable(False, False)
        dlg.grab_set()

        ttk.Label(dlg, text=i18n.t("dlg_proxy_url_label")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(16, 4),
        )

        url_var = tk.StringVar(value=self.config.get("proxy_url", ""))
        entry = ttk.Entry(dlg, textvariable=url_var, width=46)
        entry.grid(row=1, column=0, columnspan=3, padx=16, pady=(0, 4))

        ttk.Label(dlg, text=i18n.t("dlg_proxy_hint"),
                  foreground=theme.muted_foreground(dlg)).grid(
            row=2, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 12),
        )

        def _on_clear() -> None:
            url_var.set("")
            entry.focus_set()

        def _on_ok() -> None:
            url = url_var.get().strip()
            self.config["proxy_url"] = url
            config.save_app(self.config)
            dem.set_proxy(url)
            sim.clear_terrain_cache()
            dlg.destroy()

        ttk.Button(dlg, text=i18n.t("btn_clear"),  command=_on_clear).grid(
            row=3, column=0, padx=(16, 4), pady=(0, 16), sticky="e",
        )
        ttk.Button(dlg, text=i18n.t("btn_cancel"), command=dlg.destroy).grid(
            row=3, column=1, padx=4, pady=(0, 16),
        )
        ttk.Button(dlg, text=i18n.t("dlg_ok"), style="Accent.TButton", command=_on_ok).grid(
            row=3, column=2, padx=(4, 16), pady=(0, 16), sticky="w",
        )
        dlg.columnconfigure(0, weight=1)
        dlg.columnconfigure(1, weight=0)
        dlg.columnconfigure(2, weight=1)

        # ⚠️ 中央合わせは `dialogs.center_on` に寄せた（B-083）＝ここには同じ 4 行が
        # 複製されており、**画面の中へ引き戻す処理を足すときに片方だけ直る**形
        # だった（親が下端に寄っていれば子も外へ出る）。
        dialogs.center_on(self.root, dlg)

    def _on_delete_all_cache(self) -> None:
        """全 DEM/地図タイルキャッシュを削除する（設定メニューから実行）。"""
        if not self._confirm(
            i18n.t("tm_delete_all_title"), i18n.t("tm_delete_all_confirm")
        ):
            return
        result = dem.delete_all_tile_cache()
        # マップウィンドウが開いていれば表示を更新する。
        if hasattr(self, "_map_win") and self._map_win._win.winfo_exists():
            self._map_win.on_external_delete_all(result["deleted"])
        else:
            self._alert(
                i18n.t("tm_delete_all_title"),
                i18n.t("tm_delete_all_done").format(deleted=result["deleted"]),
            )

    def _on_load_settings(self) -> None:
        file_path = filedialog.askopenfilename(
            initialdir = config.RESULTS_DIR,
            title      = i18n.t("dlg_select_settings"),
            filetypes  = [("JSON files", "*.json")],
            parent     = self.root,
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                self._apply_sim_config(json.load(f))
            self._alert(i18n.t("dlg_success"), i18n.t("dlg_settings_ok"))
        except Exception as e:
            self._alert(i18n.t("dlg_error"), failure.explain(
                e, what=i18n.t("fail_load_settings"),
                hint=i18n.t("fix_file_read")))

    def _on_load_app_settings(self) -> None:
        """ファイルから app 設定（theme/lang/proxy_url）のみ取り込む。

        sim パラメータは無視する（select_app）。settings.json を読んでも
        シミュレーション条件は変わらない。_on_load_settings と対称。
        """
        file_path = filedialog.askopenfilename(
            title     = i18n.t("dlg_select_app_settings"),
            filetypes = [("JSON files", "*.json")],
            parent    = self.root,
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                app = config.select_app(json.load(f))
            # 実際に1つでも app 設定を適用したか。未適用なら成功表示しない。
            applied = False
            # 不正値は無視して安全に適用する（テーマ/言語は既知値のみ）。
            if app.get("theme") in ("system", "light", "dark"):
                self.config["theme"] = app["theme"]
                self._theme_var.set(app["theme"])
                self._on_theme(app["theme"])
                applied = True
            if "proxy_url" in app:
                self.config["proxy_url"] = str(app["proxy_url"])
                dem.set_proxy(self.config["proxy_url"])
                sim.clear_terrain_cache()
                applied = True
            # ⚠️ 言語の白リストをここに書かない（B-086）＝外部の訳を足しても
            # ここだけ `("en", "ja")` のまま残り、`lang: "fr"` の設定を読んでも
            # **言語だけ黙って捨てられる**（他の項目があれば成功表示まで出る）。
            # 判定は `set_lang` と同じ表を見る `i18n.available_languages()` へ。
            langs = i18n.available_languages()
            lang_changed = app.get("lang") in langs and \
                app["lang"] != self.config.get("lang")
            if app.get("lang") in langs:
                self.config["lang"] = app["lang"]
                self._lang_var.set(app["lang"])
                applied = True

            if not applied:
                # ファイルに有効な app 設定がない＝何も取り込んでいない。誤解を招く
                # 成功表示を避け、その旨を伝える（save_app も呼ばない）。
                self._alert(i18n.t("dlg_app_settings_none_title"),
                            i18n.t("dlg_app_settings_none"))
                return
            config.save_app(self.config)
            if lang_changed:
                self._alert(i18n.t("menu_language"), i18n.t("lang_changed_msg"))
            else:
                self._alert(i18n.t("dlg_success"), i18n.t("dlg_app_settings_ok"))
        except Exception as e:
            self._alert(i18n.t("dlg_error"), failure.explain(
                e, what=i18n.t("fail_load_settings"),
                hint=i18n.t("fix_file_read")))

    def _on_about(self) -> None:
        self._alert(
            i18n.t("menu_about"),
            i18n.t("dlg_about_msg").format(
                app  = version.APP_NAME,
                ver  = version.APP_VERSION,
                copy = version.COPYRIGHT,
            ),
        )

    def _on_open_readme(self) -> None:
        import sys
        base = getattr(
            sys, "_MEIPASS",
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        key  = "readme_binary_filename" if getattr(sys, "frozen", False) else "readme_filename"
        path = os.path.join(base, i18n.t(key))
        if not os.path.exists(path):
            self._alert(i18n.t("menu_help"), i18n.t("dlg_readme_missing"))
            return

        # Tier 1: markdown ライブラリで HTML 変換 → ブラウザ表示
        try:
            import atexit
            import shutil
            import tempfile
            import unicodedata as _ud
            import webbrowser
            import markdown as _md
            from markdown.extensions.toc import TocExtension

            def _slugify(value: str, sep: str) -> str:
                """GitHub 互換のアンカー ID 生成。文字・数字は保持し句読点は除去。"""
                result = []
                for ch in value.lower():
                    cat = _ud.category(ch)
                    if cat.startswith("L") or cat.startswith("N") or cat == "Pc":
                        result.append(ch)
                    elif ch in (" ", "\t"):
                        result.append(sep)
                return "".join(result)

            def _to_html(text: str) -> str:
                return _md.markdown(
                    text,
                    extensions=["tables", "fenced_code", TocExtension(slugify=_slugify)],
                )

            # 開く文書 1 本ではなく、そこから辿れる同梱文書の一式を書き出す
            # （＝文書間のリンクが一時ディレクトリでも生きる・2026-08-13）。
            out_dir = tempfile.mkdtemp(prefix="radiosim_doc_")
            atexit.register(lambda d=out_dir: shutil.rmtree(d, ignore_errors=True))
            entry = render_doc_site(path, base, out_dir, _to_html,
                                    i18n.t("html_lang"))
            webbrowser.open(Path(entry).as_uri())
            return
        except ImportError:
            pass
        except Exception as ex:
            config.logger.warning("README markdown render failed: %s", ex)

        # Tier 2: OS デフォルトアプリで .md を直接開く
        try:
            os.startfile(path)
            return
        except Exception:
            pass

        # Tier 3: アプリ内テキストビューア
        self._show_readme_text(path)

    def _show_readme_text(self, path: str) -> None:
        from tkinter.scrolledtext import ScrolledText
        win = tk.Toplevel(self.root)
        win.transient(self.root)
        win.title(i18n.t("dlg_doc_title"))
        win.geometry("800x600")
        win.minsize(500, 400)
        st = ScrolledText(win, font="TkFixedFont", wrap="word")
        st.pack(fill="both", expand=True, padx=10, pady=10)
        with open(path, encoding="utf-8") as f:
            st.insert("1.0", f.read())
        st.config(state="disabled")
