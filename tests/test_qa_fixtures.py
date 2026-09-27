"""
tests/test_qa_fixtures.py
=========================
動作確認用の設定ファイル（`qa_fixtures/`）が、**製品の読み込み器で実際に読める**
ことを見張る。

**なぜ要るか**＝宣言ファイルの書式は版とともに変わり得る（`dem_sources.toml` は
3.3 ステージ4c → 3.4 ステージ1 で、`tile_sources.toml` は 3.5 ステージ3 で入った）。正本が黙って
古びると、**確認したかった機能ではなく設定の誤りを見ている**状態になり、しかも
「欄が出ない」という同じ見え方をする＝気づけない。⇒ ここで毎回読んでおく。

配る側（`buildtools/deploy_qa_fixtures.py`）が知っているファイル名と、ここで
検証する名前がずれないことも見る（片方にだけ足す取りこぼしを止める）。
"""

import importlib.util
import os
import sys

import pytest

from core import dem_sources, i18n, tile_sources

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load_deployer():
    """スクリプトをモジュールとして読み込む（`buildtools` はパッケージではない）。"""
    path = os.path.join(_ROOT, "buildtools", "deploy_qa_fixtures.py")
    spec = importlib.util.spec_from_file_location("deploy_qa_fixtures", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


deploy_qa_fixtures = _load_deployer()
_FIXTURES = deploy_qa_fixtures.FIXTURES_DIR


def _fixture(name: str) -> str:
    return os.path.join(_FIXTURES, name)


class TestFixturesAreReadableByTheProduct:

    def test_dem_sources_fixture_loads_without_any_rejection(self):
        specs, reports = dem_sources.load_user_sources(_fixture("dem_sources.toml"))
        assert reports == []          # 正本は妥当な宣言だけ（壊すのはコピーの側）
        ids = [s.source_id for s in specs]
        assert ids == ["terrarium_aws", "qa_offline_probe"]

    def test_dem_sources_fixture_makes_the_source_selectors_appear(self):
        """欄が出る条件＝組み込み（国土地理院）以外に 1 つ以上あること。

        ランチャーの「DEM ソース」欄も、地図ウィンドウの「対象 DEM ソース」欄も
        選択肢が 1 つなら出さない（I-153 / I-155）＝この正本の存在理由そのもの。
        """
        specs, _ = dem_sources.load_user_sources(_fixture("dem_sources.toml"))
        assert len(specs) >= 1
        # 欄の幅の確認に使うので、組み込みより長い表示名を必ず 1 つ持たせておく。
        longest = max(len(s.display_name) for s in specs)
        assert longest > len(dem_sources.GSI_DEM.display_name)

    def test_tile_sources_fixture_loads_without_any_rejection(self):
        specs, reports = tile_sources.load_user_sources(_fixture("tile_sources.toml"))
        assert reports == []
        assert [s.source_id for s in specs] == ["osm"]

    def test_lang_fixture_loads_without_any_rejection(self):
        """I-130＝利用者が足す表示言語。全キーが採用される（正本はわざと部分訳）。"""
        import json
        with open(_fixture("lang/qa_fr.json"), encoding="utf-8") as f:
            table = json.load(f)
        accepted, rejected = i18n.validate_external(table)
        assert rejected == []
        assert accepted == {
            "menu_help": "Aide",
            "btn_run": "Exécuter",
            "proj_saved": "Projet enregistré :\n{path}",
        }


class TestDeployerAndFixturesAgree:

    @pytest.mark.parametrize("name", deploy_qa_fixtures.FIXTURE_FILES)
    def test_every_file_the_deployer_copies_exists(self, name):
        assert os.path.isfile(_fixture(name))

    def test_every_product_config_in_the_directory_is_deployed(self):
        """`qa_fixtures/` に置いた製品の設定ファイルが配る対象から漏れないこと。

        README・壊した実験用のコピーは対象外。トップレベルの `.toml` と、
        `lang/` の下の `.json` の両方を見る（`lang/` は `USER_LANG_DIR` 基準の
        サブフォルダなので置き場が違う＝FIXTURE_FILES 側は `lang/<name>.json`
        と書く）。
        """
        present = {
            n for n in os.listdir(_FIXTURES)
            if n != "README.md" and n.endswith(".toml")
        }
        lang_dir = os.path.join(_FIXTURES, "lang")
        present |= {
            f"lang/{n}" for n in os.listdir(lang_dir) if n.endswith(".json")
        }
        assert present == set(deploy_qa_fixtures.FIXTURE_FILES)


class TestDeployerDoesNotDestroyRealSettings:
    """B-267＝配置先に先客（手で書いた本物の宣言）が居たら触らないこと。

    ⚠️ **`--appdata` は実プロファイルを指す**＝ここで消えるのは開発機の本物の
    設定。配布物には入らない道具だが、壊すのは本物のファイル。
    """

    FIRST = deploy_qa_fixtures.FIXTURE_FILES[0]

    def _mine(self, tmp_path) -> str:
        """配置先に「手で書いた別内容のファイル」を置く。"""
        dst = tmp_path / self.FIRST
        dst.write_text("# 手で書いた宣言\n", encoding="utf-8")
        return str(dst)

    def test_refuses_to_overwrite_a_file_it_did_not_deploy(self, tmp_path):
        dst = self._mine(tmp_path)

        rc = deploy_qa_fixtures.main(["--target", str(tmp_path)])

        assert rc == 1
        with open(dst, encoding="utf-8") as f:
            assert f.read() == "# 手で書いた宣言\n"

    def test_force_overwrites_but_keeps_a_backup(self, tmp_path):
        dst = self._mine(tmp_path)

        rc = deploy_qa_fixtures.main(["--target", str(tmp_path), "--force"])

        assert rc == 0
        with open(dst + ".bak", encoding="utf-8") as f:
            assert f.read() == "# 手で書いた宣言\n"

    def test_remove_keeps_a_file_it_did_not_deploy(self, tmp_path):
        dst = self._mine(tmp_path)

        rc = deploy_qa_fixtures.main(["--target", str(tmp_path), "--remove"])

        assert rc == 0
        assert os.path.isfile(dst), "配ったものではないのに消した"

    def test_remove_deletes_what_it_deployed(self, tmp_path):
        assert deploy_qa_fixtures.main(["--target", str(tmp_path)]) == 0
        deployed = str(tmp_path / self.FIRST)
        assert os.path.isfile(deployed)

        assert deploy_qa_fixtures.main(["--target", str(tmp_path), "--remove"]) == 0

        assert not os.path.exists(deployed)

    def test_deploying_twice_is_not_refused(self, tmp_path):
        """同じ内容なら配り直せる（何度実行しても同じ結果になること）。"""
        assert deploy_qa_fixtures.main(["--target", str(tmp_path)]) == 0
        assert deploy_qa_fixtures.main(["--target", str(tmp_path)]) == 0

    def test_a_later_file_being_refused_deploys_nothing(self, tmp_path):
        """B-275＝先発のファイルに先客が無くても、後発のファイルで弾かれたら
        何も配置しない（1 本ずつ検査→コピーを交互にすると先発だけ残っていた）。"""
        second = deploy_qa_fixtures.FIXTURE_FILES[1]
        dst = tmp_path / second
        dst.write_text("# 手で書いた宣言\n", encoding="utf-8")

        rc = deploy_qa_fixtures.main(["--target", str(tmp_path)])

        assert rc == 1
        assert not os.path.isfile(tmp_path / self.FIRST), \
            "後発で弾かれたのに先発だけ配置先に残った"
