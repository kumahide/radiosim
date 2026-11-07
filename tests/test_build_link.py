"""
tests/test_build_link.py
========================
新しい画面の配布を作る道具（`buildtools/build_link.py`・4.0 ステージ8d・I-144）の純粋な部分。

ビルドそのもの（embeddable の展開・依存の写し・ISCC）は重く外部の zip が要るので、ここでは
**止める判断**だけを見る＝固定の読み方・版の食い違い・依存の閉包・`._pth` の中身・版の 4 数字・
build.bat の入口の位置。
"""

from __future__ import annotations

import importlib.metadata as md
import importlib.util
import os

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
        assert pinned == {"numpy", "pillow", "requests", "truststore"}


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
        """宣言環境の依存 8 つが互いの中で閉じている（上流が依存を足したらここで落ちる）。"""
        assert bl.missing_dependencies(bl.RUNTIME_DISTS, lambda n: md.requires(n) or []) == []


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


class TestInstallerCommand:
    def test_tree_and_output_follow_the_same_dist(self, tmp_path):
        """同梱元（`LinkDir`）と出力先（`/O`）が同じ `--dist` から出る＝`--dist` を変えてもずれない。"""
        cmd = bl.installer_command("ISCC.exe", str(tmp_path))
        assert f"/DLinkDir={tmp_path / bl.APP_DIR_NAME}" in cmd
        assert f"/O{tmp_path}" in cmd
        assert os.path.normcase(os.path.normpath(cmd[-1])) == os.path.normcase(
            os.path.normpath(os.path.join(_ROOT, "installer", "link.iss")))

    def test_iss_reads_the_tree_it_is_given(self):
        """`link.iss` は `LinkDir` から同梱し、版の 4 数字もその木の `version.txt` から読む。"""
        with open(os.path.join(_ROOT, "installer", "link.iss"), encoding="utf-8-sig") as f:
            iss = f.read()
        assert 'Source: "{#LinkDir}\\*"' in iss
        assert '#define VerFile AddBackslash(LinkDir) + "version.txt"' in iss
        assert f"OutputBaseFilename={bl.APP_DIR_NAME}-Setup-{{#AppVersion}}" in iss


class TestBuildBatDoor:
    def _bat(self) -> str:
        with open(os.path.join(_ROOT, "build.bat"), encoding="ascii") as f:
            return f.read()

    def test_link_branches_off_before_the_old_ui_gates(self):
        """旧画面だけの関所（Tk 9・pip install・PyInstaller）より前に Python の道具へ渡す。"""
        bat = self._bat()
        door = bat.index(r'buildtools\build_link.py" --mode %LINK_MODE%')
        assert door < bat.index("tkinter.TkVersion >= 9")
        assert door < bat.index("-m PyInstaller radiosim.spec")
        assert bat.index('set "PY=%RADIOSIM_PYTHON%"') < door

    def test_both_subcommands_are_wired(self):
        bat = self._bat()
        assert 'if /i "%~1"=="link" set "LINK_MODE=zip"' in bat
        assert 'if /i "%~1"=="link-installer" set "LINK_MODE=installer"' in bat
