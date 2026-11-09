"""
tests/test_build_link.py
========================
新しい画面の配布を作る道具（`buildtools/build_link.py`・4.0 ステージ8d・I-144）の純粋な部分。

ビルドそのもの（embeddable の展開・依存の写し・ISCC）は重く外部の zip が要るので、ここでは
**止める判断**だけを見る＝固定の読み方・版の食い違い・依存の閉包・`._pth` の中身・版の 4 数字・
build.bat の入口の位置。ほかに、ロゴとアイコンの元（`brand.svg`）から `icon.ico` を焼く部分（I-238）。
"""

from __future__ import annotations

import importlib.metadata as md
import importlib.util
import mimetypes
import os
import xml.etree.ElementTree as ET  # nosec B405 ─ リポジトリの SVG と試験の文字列だけ

import pytest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_SCRIPT = os.path.join(_ROOT, "buildtools", "build_link.py")


def _load():
    spec = importlib.util.spec_from_file_location("build_link", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bl = _load()


class TestPins:
    def test_reads_only_exact_pins(self):
        lines = ["# comment", "", "numpy==2.5.4", "Pillow == 13.0.0  # 画像",
                 "sv-ttk==2.6.1", "ruff>=0.1", "requests==2.34.2 ; python_version>'3'"]
        assert bl.read_pins(lines) == {"numpy": "2.5.4", "pillow": "13.0.0",
                                       "sv-ttk": "2.6.1", "requests": "2.34.2"}

    def test_normalize_matches_pep503(self):
        assert bl.normalize("Charset_Normalizer") == bl.normalize("charset-normalizer")
        assert bl.normalize("zope.interface") == "zope-interface"

    def test_mismatch_names_the_dist_and_both_versions(self):
        pins = {"numpy": "2.5.4", "pillow": "13.0.0"}
        have = {"numpy": "2.5.4", "pillow": "12.0.0", "idna": "3.0"}
        out = bl.pin_mismatches(["numpy", "pillow", "idna"], pins, have.__getitem__)
        assert len(out) == 1
        assert "pillow" in out[0] and "12.0.0" in out[0] and "13.0.0" in out[0]

    def test_unpinned_dists_are_not_checked(self):
        assert bl.pin_mismatches(["idna"], {}, lambda n: "anything") == []

    def test_real_requirements_pin_the_four_expected(self):
        with open(os.path.join(_ROOT, "requirements.txt"), encoding="utf-8") as f:
            pins = bl.read_pins(f)
        pinned = {d for d in bl.RUNTIME_DISTS if bl.normalize(d) in pins}
        assert pinned == {"numpy", "pillow", "requests", "truststore", "markdown"}


class TestDependencyClosure:
    def test_extras_are_not_required(self):
        assert bl.requirement_name('PySocks!=1.5.7,>=1.5.6; extra == "socks"') is None

    def test_environment_markers_are_kept(self):
        assert bl.requirement_name("idna<4,>=2.5; python_version >= '3'") == "idna"
        assert bl.requirement_name("charset_normalizer<4,>=2") == "charset-normalizer"

    def test_missing_dependency_is_named_with_its_requirer(self):
        reqs = {"a": ["b>=1", "c; extra == 'x'"], "b": ["d"]}
        out = bl.missing_dependencies(["a", "b"], lambda n: reqs.get(n, []))
        assert out == ["d ← b"]

    def test_runtime_dists_are_closed_in_this_environment(self):
        """宣言環境の依存が互いの中で閉じている（上流が依存を足したらここで落ちる）。"""
        assert bl.missing_dependencies(bl.RUNTIME_DISTS, lambda n: md.requires(n) or []) == []


class TestImportClosure:
    """配布の木に入る包みが読む名前は、すべて木の中にある（B-380）。

    🔑 不変条件＝`APP_PACKAGES` のどのモジュールが**どこで**（関数の中でも）読む名前も、標準
    ライブラリ（embeddable に無い tkinter などを除く）・`APP_PACKAGES`・`RUNTIME_DISTS` の
    どれか。作った木の `smoke_import` はモジュールの頭しか読まない＝関数の中の import の漏れ
    （ステージ10 で `webui` が `report` を読み始めたのに木へ入れていなかった）はここで捕まえる。
    """

    #: embeddable の標準ライブラリの zip に入っていないもの（Tk と、その上の道具）。
    _NOT_IN_EMBEDDABLE = frozenset({"tkinter", "_tkinter", "turtle", "turtledemo", "idlelib"})

    @staticmethod
    def _roots(pkg: str, base: str = _ROOT) -> dict[str, str]:
        import ast
        out: dict[str, str] = {}
        for top, _dirs, names in os.walk(os.path.join(base, pkg)):
            for n in sorted(names):
                if not n.endswith(".py"):
                    continue
                path = os.path.join(top, n)
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        mods = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                        mods = [node.module]
                    else:
                        continue
                    for m in mods:
                        out.setdefault(m.split(".")[0],
                                       f"{os.path.relpath(path, base)}:{node.lineno}")
        return out

    def test_every_imported_name_ships_in_the_tree(self):
        import sys
        by_dist: dict[str, set[str]] = {}
        for top, dists in md.packages_distributions().items():
            for d in dists:
                by_dist.setdefault(bl.normalize(d), set()).add(top)
        shipped = set(bl.APP_PACKAGES)
        for d in bl.RUNTIME_DISTS:
            shipped |= by_dist.get(bl.normalize(d), set())
        stdlib = set(sys.stdlib_module_names) - self._NOT_IN_EMBEDDABLE
        bad = []
        for pkg in bl.APP_PACKAGES:
            for root, where in self._roots(pkg).items():
                if root not in shipped and root not in stdlib:
                    bad.append(f"{root}（{where}）")
        assert bad == [], "配布の木に入らない名前を読んでいる: " + ", ".join(sorted(bad))

    def test_the_gate_sees_imports_inside_functions(self, tmp_path):
        """壊れ方の確かめ＝関数の中の import も数える（頭だけ読むと B-380 を見逃す）。"""
        d = tmp_path / "pkg"
        d.mkdir()
        (d / "m.py").write_text("def f():\n    from report import batch\n    import tkinter\n"
                                "    from . import sibling\n", encoding="utf-8")
        assert set(self._roots("pkg", str(tmp_path))) == {"report", "tkinter"}

    def test_report_is_in_the_tree(self):
        """B-380 の当の漏れ＝`webui` が読む `report` が木に入る。"""
        assert "report" in bl.APP_PACKAGES
        assert "webui.exports" in {f"webui.{n[:-3]}" for n in os.listdir(os.path.join(_ROOT, "webui"))}


class TestDocFiles:
    """同梱文書の閉包（ステージ11f）＝説明書が指す同梱物の中の文書と絵は、すべて木に入る。"""

    def test_real_manuals_bring_their_links(self):
        files = bl.doc_files()
        assert {"docs/manual_ja.md", "docs/manual_en.md"} <= set(files)
        # マニュアルが辿る先（開発者向け文書・用語集）も入る＝配布版でだけリンクが消えない
        assert {"docs/developer_ja.md", "docs/glossary.md"} <= set(files)
        assert all(f.endswith(bl.DOC_SUFFIXES) for f in files)
        assert not any(f.endswith(".py") or f == "requirements.txt" for f in files)

    def test_closure_follows_md_and_images_but_not_outside(self, tmp_path):
        """壊れ方の確かめ＝列挙でなく辿る（新しい絵を 1 枚足しても漏れない）・外と源は入れない。"""
        (tmp_path / "docs" / "images").mkdir(parents=True)
        (tmp_path / "docs" / "images" / "new.png").write_bytes(b"x")
        (tmp_path / "logo.png").write_bytes(b"x")
        (tmp_path / "src.py").write_text("", encoding="utf-8")
        (tmp_path / "docs" / "b.md").write_text('<img src="images/new.png">', encoding="utf-8")
        (tmp_path / "docs" / "a.md").write_text(
            "![l](../logo.png) [b](b.md#x) [s](../src.py) [o](../../out.md) "
            "[w](https://example.com/a.md) [m](missing.md)", encoding="utf-8")
        got = bl.doc_files(str(tmp_path), ["docs/a.md"])
        assert got == ["docs/a.md", "docs/b.md", "docs/images/new.png", "logo.png"]

    def test_missing_entry_stops_the_build(self, tmp_path):
        with pytest.raises(bl.BuildError):
            bl.doc_files(str(tmp_path), ["docs/manual_ja.md"])

    def test_entries_are_what_the_settings_panel_opens(self):
        """入口＝「ドキュメントを開く」が配布物で開く文書（`readme_binary_filename`）の日英。"""
        from core import i18n
        opened = set()
        for lang in ("ja", "en"):
            prev = i18n.current_lang()
            try:
                i18n.set_lang(lang)
                opened.add(i18n.t("readme_binary_filename"))
            finally:
                i18n.set_lang(prev)
        assert opened == set(bl.DOC_ENTRIES)


class TestRuntimeDistOption:
    """`--runtime-dist`＝上に載る層が、公開側に無い依存（Pro の帳票の matplotlib）を足す口。"""

    def test_extra_dists_come_after_the_public_ones_without_duplicates(self):
        got = bl.runtime_dists(["matplotlib", "Pillow", "cycler", "matplotlib"])
        assert got == [*bl.RUNTIME_DISTS, "matplotlib", "cycler"]

    @pytest.mark.parametrize("bad", ["", "a b", "-x", "x;y"])
    def test_bad_names_stop(self, bad):
        with pytest.raises(bl.BuildError):
            bl.runtime_dists([bad])

    def test_absent_dist_stops_before_copying(self):
        with pytest.raises(bl.BuildError, match="入っていない"):
            bl.check_dependencies([*bl.RUNTIME_DISTS, "no-such-dist-radiosim"])

    def test_open_closure_names_the_option(self):
        with pytest.raises(bl.BuildError, match="--runtime-dist"):
            bl.check_dependencies([*bl.RUNTIME_DISTS, "matplotlib"])  # cycler などが無い

    def test_main_keeps_the_product_name_with_outer_packages(self, tmp_path, monkeypatch):
        """外の包みを表示する繰り返しが配布の名前を上書きしない（`dist\\radiosim_pro` ができた）。"""
        d = tmp_path / "outer"
        d.mkdir()
        (d / "__init__.py").write_text("", encoding="utf-8")
        (d / "__main__.py").write_text("", encoding="utf-8")
        seen = {}
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(bl, "check_declared", lambda: "py")
        monkeypatch.setattr(bl, "check_embed_zip", lambda: "embed.zip")
        monkeypatch.setattr(bl, "check_dependencies", lambda dists: None)

        def fake_tree(dist_dir, embed_zip, declared, packages, *, name, licenses, dists):
            seen.update(name=name, dists=list(dists))
            raise bl.BuildError("ここで止める")
        monkeypatch.setattr(bl, "build_tree", fake_tree)
        rc = bl.main(["--dist", str(tmp_path / "dist"), "--package", "outer=outer",
                      "--entry", "outer", "--product", "RadioSim Link Pro",
                      "--runtime-dist", "cycler"])
        assert rc == 1
        assert seen == {"name": "RadioSimLinkPro", "dists": [*bl.RUNTIME_DISTS, "cycler"]}


class TestPth:
    def test_lines_are_stdlib_then_app_paths_without_site(self):
        lines = bl.pth_lines(["python315.zip"])
        assert lines == ["python315.zip", ".", "vendor", "app"]
        assert not any("site" in ln for ln in lines)

    @pytest.mark.parametrize("zips", [[], ["python315.zip", "python314.zip"]])
    def test_not_exactly_one_stdlib_zip_stops(self, zips):
        with pytest.raises(bl.BuildError):
            bl.pth_lines(zips)


class TestVersionAndFiles:
    def test_version_numbers_put_final_above_prerelease(self):
        assert bl.version_numbers("2.1a1") == "2.1.0.0"
        assert bl.version_numbers("2.1").endswith(".9999")

    @pytest.mark.parametrize("rel,skip", [
        ("numpy/_core/tests/test_x.py", True),
        ("numpy/__init__.pyi", True),
        ("numpy/_core/include/numpy/x.h", True),
        ("numpy/__pycache__/x.cpython-315.pyc", True),
        ("numpy/_core/_multiarray_umath.cp315-win_amd64.pyd", False),
        ("numpy-2.5.4.dist-info/LICENSE.txt", False),
    ])
    def test_skip_file(self, rel, skip):
        assert bl.skip_file(rel) is skip

    def test_launcher_starts_pythonw_with_the_module_next_to_itself(self):
        assert '"%~dp0pythonw.exe" -m webui' in bl.LAUNCHER_CMD
        assert bl.LAUNCHER_CMD.startswith("@echo off\r\n")
        assert bl.LAUNCHER_CMD.isascii()


class TestOuterPackages:
    """`--package 名前=フォルダ` と `--entry`＝上に載る非公開の層がこの道具を借りる口（ステージ8f）。"""

    def _pkg(self, tmp_path, name="outer", main=True):
        d = tmp_path / name
        d.mkdir()
        (d / "__init__.py").write_text("", encoding="utf-8")
        if main:
            (d / "__main__.py").write_text("", encoding="utf-8")
        return d

    def test_default_is_the_two_public_packages(self):
        got = bl.package_sources()
        assert [n for n, _ in got] == list(bl.APP_PACKAGES)
        for name, src in got:  # core は __init__.py の無い名前空間の包み＝フォルダだけを見る
            assert os.path.isdir(src) and os.path.basename(src) == name, name

    def test_outer_package_comes_after_the_public_ones(self, tmp_path):
        self._pkg(tmp_path)
        got = bl.package_sources(["outer=outer"], base=str(tmp_path))
        assert [n for n, _ in got] == [*bl.APP_PACKAGES, "outer"]
        assert got[-1][1] == os.path.abspath(tmp_path / "outer")

    @pytest.mark.parametrize("spec,why", [
        ("outer", "名前=フォルダ"),
        ("=outer", "名前=フォルダ"),
        ("out-er=outer", "包みの名前でない"),
        ("webui=outer", "重なる"),
        ("other=missing", "__init__.py が無い"),
    ])
    def test_bad_package_spec_stops(self, tmp_path, spec, why):
        self._pkg(tmp_path)
        with pytest.raises(bl.BuildError, match=why):
            bl.package_sources([spec], base=str(tmp_path))

    def test_the_same_outer_name_twice_stops(self, tmp_path):
        self._pkg(tmp_path)
        with pytest.raises(bl.BuildError, match="重なる"):
            bl.package_sources(["outer=outer", "outer=outer"], base=str(tmp_path))

    def test_entry_must_be_a_package_with_main(self, tmp_path):
        self._pkg(tmp_path)
        self._pkg(tmp_path, "nomain", main=False)
        pk = bl.package_sources(["outer=outer", "nomain=nomain"], base=str(tmp_path))
        bl.check_entry(bl.DEFAULT_ENTRY, pk)
        bl.check_entry("outer", pk)
        with pytest.raises(bl.BuildError, match="入れる包みに無い"):
            bl.check_entry("ghost", pk)
        with pytest.raises(bl.BuildError, match="__main__.py が無い"):
            bl.check_entry("nomain", pk)

    def test_launcher_follows_the_entry(self):
        assert bl.launcher_cmd("outer").endswith('"%~dp0pythonw.exe" -m outer\r\n')
        assert bl.LAUNCHER_CMD == bl.launcher_cmd(bl.DEFAULT_ENTRY)


class TestInstallerCommand:
    def test_tree_and_output_follow_the_same_dist(self, tmp_path):
        """同梱元（`LinkDir`）と出力先（`/O`）が同じ `--dist` から出る＝`--dist` を変えてもずれない。"""
        cmd = bl.installer_command("ISCC.exe", str(tmp_path))
        assert f"/DLinkDir={tmp_path / bl.APP_DIR_NAME}" in cmd
        assert f"/O{tmp_path}" in cmd
        assert f"/DEntryModule={bl.DEFAULT_ENTRY}" in cmd
        assert "/DEntryModule=outer" in bl.installer_command("ISCC.exe", str(tmp_path), "outer")
        assert os.path.normcase(os.path.normpath(cmd[-1])) == os.path.normcase(
            os.path.normpath(os.path.join(_ROOT, "installer", "link.iss")))

    def test_iss_reads_the_tree_it_is_given(self):
        """`link.iss` は `LinkDir` から同梱し、版の 4 数字もその木の `version.txt` から読む。"""
        with open(os.path.join(_ROOT, "installer", "link.iss"), encoding="utf-8-sig") as f:
            iss = f.read()
        assert 'Source: "{#LinkDir}\\*"' in iss
        assert '#define VerFile AddBackslash(LinkDir) + "version.txt"' in iss
        assert "OutputBaseFilename={#OutputName}-Setup-{#AppVersion}" in iss
        assert f'#define OutputName "{bl.APP_DIR_NAME}"' in iss
        assert f'#define AppName "{bl.DEFAULT_PRODUCT}"' in iss
        assert '#define LicenseName "LICENSE"' in iss
        assert "LicenseFile={#LinkDir}\\{#LicenseName}" in iss

    def test_product_name_reaches_the_installer(self, tmp_path):
        """`--product` は木・インストーラの名前と AppName を替え、AppId とインストール先は替えない（11c）。"""
        cmd = bl.installer_command("ISCC.exe", str(tmp_path), "outer",
                                   product="RadioSim Link Pro", license_name="LICENSE-PRO.txt")
        assert f"/DLinkDir={tmp_path / 'RadioSimLinkPro'}" in cmd
        assert {"/DAppName=RadioSim Link Pro", "/DOutputName=RadioSimLinkPro",
                "/DLicenseName=LICENSE-PRO.txt"} <= set(cmd)
        with open(os.path.join(_ROOT, "installer", "link.iss"), encoding="utf-8-sig") as f:
            iss = f.read()
        assert "DefaultDirName={autopf}\\RadioSim Link\n" in iss.replace("\r\n", "\n")
        assert "AppId={{E45B60BB-825A-42EE-92DB-796FAA051C21}" in iss
        assert "UsePreviousGroup=no" in iss

    def test_lite_name_is_the_default(self):
        assert bl.dir_name(bl.DEFAULT_PRODUCT) == bl.APP_DIR_NAME
        assert bl.LICENSE_NAME == "LICENSE"   # build_tree は字で写す（test_docs_consistency が見る）

    @pytest.mark.parametrize("bad", ["", " RadioSim", "RadioSim  Link", "Radio/Sim", "ラジオ"])
    def test_bad_product_name_stops(self, bad):
        with pytest.raises(bl.BuildError, match="製品名"):
            bl.dir_name(bad)

    def test_license_must_exist_and_not_shadow_mit(self, tmp_path):
        (tmp_path / "LICENSE-PRO.txt").write_text("x", encoding="utf-8")
        (tmp_path / "LICENSE").write_text("x", encoding="utf-8")
        assert bl.license_source("LICENSE-PRO.txt", str(tmp_path)).endswith("LICENSE-PRO.txt")
        with pytest.raises(bl.BuildError, match="無い"):
            bl.license_source("ghost.txt", str(tmp_path))
        with pytest.raises(bl.BuildError, match="重なる"):
            bl.license_source("LICENSE", str(tmp_path))

    def test_iss_starts_the_entry_it_is_given(self):
        """ショートカット 2 つと最後の「起動する」は `EntryModule` を起こす（既定は webui）。"""
        with open(os.path.join(_ROOT, "installer", "link.iss"), encoding="utf-8-sig") as f:
            iss = f.read()
        assert f'#define EntryModule "{bl.DEFAULT_ENTRY}"' in iss
        assert iss.count('Parameters: "-m {#EntryModule}"') == 3
        assert 'Parameters: "-m webui"' not in iss


class TestBrandAndIcon:
    """ロゴとアイコンの元は `webui/static/brand.svg` の 1 つ（I-238）。"""

    def _brand(self) -> str:
        with open(bl.BRAND_SVG, encoding="utf-8") as f:
            return f.read()

    def test_brand_has_the_logo_the_mark_and_the_icon(self):
        root = ET.fromstring(self._brand())
        ids = {el.get("id") for el in root.iter()}
        assert {"logo", "mark", bl.ICON_ID} <= ids

    def test_icon_svg_is_standalone_and_drawable_by_tk(self):
        """抜き出した SVG に、nanosvg が描かない要素（`use`・`text`・`style`）が残らない。"""
        svg = bl.icon_svg(self._brand())
        root = ET.fromstring(svg)
        tags = {el.tag.rsplit("}", 1)[-1] for el in root.iter()}
        assert not tags & {"use", "text", "style", "defs"}
        assert {"rect", "circle", "path"} <= tags
        assert root.get("viewBox") == "0 0 256 256"
        assert (root.get("width"), root.get("height")) == ("256", "256")

    def test_small_icon_is_standalone_and_has_fewer_arcs(self):
        """16px は弧を減らした絵から描く（ステージ9f）＝単独で描け、弧が大きい絵より少ない。"""
        def arcs(svg: str) -> int:
            return sum(1 for el in ET.fromstring(svg).iter() if el.tag.endswith("}path"))
        small = bl.icon_svg(self._brand(), bl.SMALL_ICON_ID)
        tags = {el.tag.rsplit("}", 1)[-1] for el in ET.fromstring(small).iter()}
        assert not tags & {"use", "text", "style", "defs"}
        assert ET.fromstring(small).get("viewBox") == "0 0 256 256"
        assert 0 < arcs(small) < arcs(bl.icon_svg(self._brand()))

    def test_logo_puts_link_after_sim_at_the_same_size(self):
        """製品名は `RadioSim Link`＝「Link」は「Sim」と同じ字の大きさで同じ行（9f の答え）。"""
        root = ET.fromstring(self._brand())
        texts = {el.text: el for el in root.iter() if el.tag.endswith("}text")}
        sim_m, link = texts["m"], texts["Link"]
        assert link.get("class").split()[0] == sim_m.get("class").split()[0] == "word"
        assert link.get("y") == sim_m.get("y")
        assert float(link.get("x")) > float(sim_m.get("x")) + float(sim_m.get("textLength"))
        left, _, width, _ = map(float, root.get("viewBox").split())
        assert float(link.get("x")) + float(link.get("textLength")) <= left + width

    @pytest.mark.parametrize("inner,word", [
        ('<text>x</text>', "text"),
        ('<use href="#nowhere"/>', "nowhere"),
    ])
    def test_icon_with_undrawable_or_dangling_parts_stops(self, inner, word):
        brand = (f'<svg xmlns="{bl._SVG_NS}"><g id="icon" data-viewbox="0 0 16 16">'
                 f'{inner}</g></svg>')
        with pytest.raises(bl.BuildError, match=word):
            bl.icon_svg(brand)

    def test_missing_icon_or_viewbox_stops(self):
        with pytest.raises(bl.BuildError):
            bl.icon_svg(f'<svg xmlns="{bl._SVG_NS}"><g id="logo"/></svg>')
        with pytest.raises(bl.BuildError, match="data-viewbox"):
            bl.icon_svg(f'<svg xmlns="{bl._SVG_NS}"><g id="icon"/></svg>')

    def test_ico_has_six_sizes_with_transparent_corners(self, tmp_path):
        """寸法ごとに Tk 9 で描いた 6 枚を持ち、角丸の外は透ける（地の四角で塗り潰さない）。"""
        import tkinter as tk

        from PIL import Image
        if tk.TkVersion < 9:
            pytest.skip("Tk 9 が無い（宣言の Python で回す）")
        dst = tmp_path / "icon.ico"
        try:
            bl.write_icon(str(dst))
        except tk.TclError as e:  # 画面の無い環境
            pytest.skip(f"Tk を起こせない: {e}")
        with Image.open(dst) as ico:
            assert sorted(ico.info["sizes"]) == sorted((n, n) for n in bl.ICON_SIZES)
            ico.size = (256, 256)
            big = ico.copy().convert("RGBA")
        assert big.getpixel((0, 0))[3] == 0
        assert big.getpixel((128, 128))[3] == 255

    def test_static_server_types_svg_as_an_image(self):
        """画面の配信は `nosniff` を付ける＝型が違うと `<img>` が描かない。"""
        assert mimetypes.guess_type(bl.BRAND_SVG)[0] == "image/svg+xml"


class TestBuildBatDoor:
    def _bat(self) -> str:
        with open(os.path.join(_ROOT, "build.bat"), encoding="ascii") as f:
            return f.read()

    def test_the_only_build_is_the_python_tool(self):
        """組み立ては Python の道具だけ＝旧画面の PyInstaller の口は残っていない（ステージ11a）。"""
        bat = self._bat()
        door = bat.index(r'buildtools\build_link.py" --mode %LINK_MODE%')
        assert bat.index('set "PY=%RADIOSIM_PYTHON%"') < door
        assert "PyInstaller radiosim.spec" not in bat
        assert "tkinter.TkVersion" not in bat

    def test_both_subcommands_are_wired(self):
        bat = self._bat()
        assert 'if "%~1"=="" set "LINK_MODE=zip"' in bat
        assert 'if /i "%~1"=="link" set "LINK_MODE=zip"' in bat
        assert 'if /i "%~1"=="installer" set "LINK_MODE=installer"' in bat
        assert 'if /i "%~1"=="link-installer" set "LINK_MODE=installer"' in bat

    def test_an_unknown_subcommand_stops(self):
        """綴りを誤ったら zip へ倒さずに止める＝違う種類の配布物を黙って作らない。"""
        bat = self._bat()
        assert bat.index("if not defined LINK_MODE (") < bat.index(r'buildtools\build_link.py" --mode')
