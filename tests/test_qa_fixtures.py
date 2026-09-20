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

from core import dem_sources, tile_sources

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


class TestDeployerAndFixturesAgree:

    @pytest.mark.parametrize("name", deploy_qa_fixtures.FIXTURE_FILES)
    def test_every_file_the_deployer_copies_exists(self, name):
        assert os.path.isfile(_fixture(name))

    def test_every_product_config_in_the_directory_is_deployed(self):
        """`qa_fixtures/` に置いた製品の設定ファイルが配る対象から漏れないこと。"""
        present = {
            n for n in os.listdir(_FIXTURES)
            if n.endswith(".toml")
        }
        assert present == set(deploy_qa_fixtures.FIXTURE_FILES)
