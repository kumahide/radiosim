"""
tests/test_dem_sources.py
=========================
dem_sources.py（DEM ソースの宣言ファイル・3.3 ステージ4c／3.4 ステージ1）のユニットテスト。
"""

import dataclasses
import textwrap

import pytest

from core import dem_sources


# ============================================================
# GSI_DEM / SOURCES
# ============================================================
class TestSources:

    def test_sources_contains_only_gsi_dem(self):
        """3.3 時点でアクティブなソースは国土地理院のみ。"""
        assert dem_sources.SOURCES == (dem_sources.GSI_DEM,)

    def test_gsi_dem_source_id_matches_output_contract_value(self):
        """`elev_source` 列の既存値 `"gsi_dem"` と一致する（core/output_contract.py）。"""
        assert dem_sources.GSI_DEM.source_id == "gsi_dem"

    def test_gsi_dem_layers_match_priority_order(self):
        assert dem_sources.GSI_DEM.layers == (
            ("dem5a_png", 15),
            ("dem5b_png", 15),
            ("dem_png", 14),
        )

    def test_url_template_renders_the_known_gsi_url(self):
        url = dem_sources.GSI_DEM.url_template.format(
            layer="dem5a_png", z=15, x=1, y=2
        )
        assert url == "https://cyberjapandata.gsi.go.jp/xyz/dem5a_png/15/1/2.png"

    def test_invalid_rgb_is_the_gsi_sea_marker(self):
        assert dem_sources.GSI_DEM.invalid_rgb == (128, 0, 0)


# ============================================================
# decode() ディスパッチ
# ============================================================
class TestDecodeDispatch:

    def test_decode_dispatches_to_gsi_dem(self):
        # (128, 1, 0) → x = 128*65536 + 1*256 = 8388864 ≥ 8388608 → (x-16777216)*0.01
        elev = dem_sources.decode(dem_sources.DecodeMethod.GSI_DEM, 128, 1, 0)
        assert elev == pytest.approx((8388864 - 16777216) * 0.01, abs=0.01)

    def test_decode_dispatches_to_terrarium(self):
        elev = dem_sources.decode(dem_sources.DecodeMethod.TERRARIUM, 128, 1, 0)
        assert elev == pytest.approx(1.0, abs=1e-9)

    def test_decode_dispatches_to_mapbox_terrain_rgb(self):
        elev = dem_sources.decode(dem_sources.DecodeMethod.MAPBOX_TERRAIN_RGB, 0, 0, 0)
        assert elev == pytest.approx(-10000.0, abs=1e-9)

    def test_every_decode_method_has_a_decoder(self):
        """列挙の全メンバーに実装が対応する（増やし忘れを検出）。"""
        for method in dem_sources.DecodeMethod:
            assert method in dem_sources._DECODERS


# ============================================================
# 個別デコード方式
# ============================================================
class TestDecodeGsiDem:

    def test_invalid_pixel_returns_zero(self):
        assert dem_sources._decode_gsi_dem(128, 0, 0) == pytest.approx(0.0)

    def test_positive_range_below_threshold(self):
        # x = 100*256 = 25600 < 8388608
        assert dem_sources._decode_gsi_dem(0, 100, 0) == pytest.approx(256.0, abs=0.01)

    def test_negative_range_above_threshold(self):
        # x = 255*65536 + 255*256 + 255 = 16777215 ≥ 8388608
        elev = dem_sources._decode_gsi_dem(255, 255, 255)
        assert elev == pytest.approx((16777215 - 16777216) * 0.01, abs=0.01)


class TestDecodeTerrarium:

    def test_sea_level_marker(self):
        # (R*256 + G + B/256) - 32768 = 0 のとき R=128, G=0, B=0
        assert dem_sources._decode_terrarium(128, 0, 0) == pytest.approx(0.0, abs=1e-9)

    def test_fractional_blue_channel(self):
        # B は 1/256 m 単位の端数を運ぶ
        elev = dem_sources._decode_terrarium(128, 0, 128)
        assert elev == pytest.approx(0.5, abs=1e-9)


class TestDecodeMapboxTerrainRgb:

    def test_void_minimum(self):
        assert dem_sources._decode_mapbox_terrain_rgb(0, 0, 0) == pytest.approx(-10000.0)

    def test_one_decimeter_step(self):
        # B を 1 増やすと 0.1m 増える
        base = dem_sources._decode_mapbox_terrain_rgb(0, 0, 0)
        stepped = dem_sources._decode_mapbox_terrain_rgb(0, 0, 1)
        assert stepped - base == pytest.approx(0.1, abs=1e-9)


# ============================================================
# 利用者の宣言ファイル（3.4 ステージ1・I-147）
# ============================================================
_VALID_TERRARIUM_TOML = textwrap.dedent("""\
    [[source]]
    source_id = "terrarium_aws"
    display_name = "Terrarium (AWS Open Data)"
    layers = [["terrarium", 12]]
    url_template = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
    decode = "terrarium"
    attribution = "AWS Open Data Terrain Tiles"
    terms_url = "https://github.com/tilezen/joerd/blob/master/docs/attribution.md"
    """)


@pytest.fixture(autouse=True)
def _reset_user_sources():
    """`load_from` が触るモジュール状態を各テストの前後で空に戻す。"""
    dem_sources._user_sources = []
    dem_sources._load_reports = []
    yield
    dem_sources._user_sources = []
    dem_sources._load_reports = []


class TestLoadUserSources:

    def test_missing_file_returns_empty(self, tmp_path):
        specs, reports = dem_sources.load_user_sources(str(tmp_path / "nope.toml"))
        assert specs == []
        assert reports == []

    def test_definition_fingerprint_changes_with_url_template(self):
        """定義（URL 等）が変われば `definition_fingerprint` も変わる（B-236）。

        `source_id` を変えずに宣言ファイルの中身だけ書き換えたとき、この値が
        変わらないとディスクキャッシュが旧タイルを新しい解釈で読み直してしまう。
        """
        base = dem_sources.DemSourceSpec(
            source_id="x", display_name="X", layers=(("a", 10),),
            url_template="https://example.invalid/{z}/{x}/{y}.png",
            decode=dem_sources.DecodeMethod.TERRARIUM, invalid_rgb=None,
            attribution="A", terms_url="https://example.invalid",
        )
        changed_url = dataclasses.replace(
            base, url_template="https://example.invalid/v2/{z}/{x}/{y}.png",
        )
        changed_decode = dataclasses.replace(
            base, decode=dem_sources.DecodeMethod.MAPBOX_TERRAIN_RGB,
        )
        fp_base = dem_sources.definition_fingerprint(base)
        assert fp_base == dem_sources.definition_fingerprint(base), "同じ定義なら同じ値"
        assert fp_base != dem_sources.definition_fingerprint(changed_url)
        assert fp_base != dem_sources.definition_fingerprint(changed_decode)

    def test_valid_declaration_is_accepted(self, tmp_path):
        path = tmp_path / "dem_sources.toml"
        path.write_text(_VALID_TERRARIUM_TOML, encoding="utf-8")
        specs, reports = dem_sources.load_user_sources(str(path))
        assert reports == []
        assert len(specs) == 1
        spec = specs[0]
        assert spec.source_id == "terrarium_aws"
        assert spec.decode is dem_sources.DecodeMethod.TERRARIUM
        assert spec.layers == (("terrarium", 12),)

    def test_unreadable_toml_is_reported_not_raised(self, tmp_path):
        path = tmp_path / "dem_sources.toml"
        path.write_text("this is not [ valid toml", encoding="utf-8")
        specs, reports = dem_sources.load_user_sources(str(path))
        assert specs == []
        assert len(reports) == 1
        assert reports[0][1] == "dem_src_file_unreadable"

    def test_one_broken_source_does_not_block_the_others(self, tmp_path):
        """1 つが壊れていても他は読む（`i18n.load_external` と同じ設計）。"""
        broken_and_valid = _VALID_TERRARIUM_TOML + textwrap.dedent("""\

            [[source]]
            source_id = "broken"
            display_name = "Broken"
            layers = [["x", 10]]
            url_template = "not-a-url"
            decode = "terrarium"
            attribution = "x"
            terms_url = "https://example.com"
            """)
        path = tmp_path / "dem_sources.toml"
        path.write_text(broken_and_valid, encoding="utf-8")
        specs, reports = dem_sources.load_user_sources(str(path))
        assert [s.source_id for s in specs] == ["terrarium_aws"]
        assert [r for r in reports if r[0] == "broken"]

    @pytest.mark.parametrize("mutate,reason", [
        (lambda d: d.__setitem__("source_id", "gsi_dem"), "dem_src_id_reserved"),
        (lambda d: d.__setitem__("source_id", "unavailable"), "dem_src_id_reserved"),
        (lambda d: d.__setitem__("source_id", "../etc"), "dem_src_bad_id"),
        (lambda d: d.__setitem__("source_id", "has space"), "dem_src_bad_id"),
        (lambda d: d.__setitem__("decode", "eval"), "dem_src_bad_decode"),
        (lambda d: d.__setitem__("decode", "os.system('x')"), "dem_src_bad_decode"),
        (lambda d: d.__setitem__("url_template", "http://example.com/{z}/{x}/{y}.png"),
         "dem_src_bad_url"),
        (lambda d: d.__setitem__("url_template", "https://example.com/{z}/{x}.png"),
         "dem_src_bad_url"),
        (lambda d: d.__setitem__("layers", []), "dem_src_bad_layers"),
        (lambda d: d.__setitem__("layers", [["a", "not-an-int"]]), "dem_src_bad_layers"),
        (lambda d: d.pop("attribution"), "dem_src_missing_field"),
        (lambda d: d.pop("terms_url"), "dem_src_missing_field"),
    ])
    def test_invalid_declaration_is_rejected(self, mutate, reason):
        raw = {
            "source_id": "terrarium_aws",
            "display_name": "Terrarium",
            "layers": [["terrarium", 12]],
            "url_template": "https://s3.amazonaws.com/x/{z}/{x}/{y}.png",
            "decode": "terrarium",
            "attribution": "AWS Open Data",
            "terms_url": "https://example.com",
        }
        mutate(raw)
        spec, _label, got_reason = dem_sources._validate_source(raw, set(), set())
        assert spec is None
        assert got_reason == reason

    def test_duplicate_source_id_is_rejected(self, tmp_path):
        doubled = _VALID_TERRARIUM_TOML + _VALID_TERRARIUM_TOML
        path = tmp_path / "dem_sources.toml"
        path.write_text(doubled, encoding="utf-8")
        specs, reports = dem_sources.load_user_sources(str(path))
        assert len(specs) == 1
        assert any(r[1] == "dem_src_id_duplicate" for r in reports)

    def test_display_name_colliding_with_gsi_is_rejected(self, tmp_path):
        """表示名が組み込み（国土地理院）と同じ宣言は拒否される（B-237）。

        拒否しないと `display_name → source_id` の逆引きが後勝ちになり、
        画面上「国土地理院 DEM」に見えるのに実際には別ソースが実行される。
        """
        toml = _VALID_TERRARIUM_TOML.replace(
            'display_name = "Terrarium (AWS Open Data)"',
            'display_name = "国土地理院 DEM"',
        )
        path = tmp_path / "dem_sources.toml"
        path.write_text(toml, encoding="utf-8")
        specs, reports = dem_sources.load_user_sources(str(path))
        assert specs == []
        assert any(r[1] == "dem_src_display_name_duplicate" for r in reports)

    def test_display_name_colliding_between_two_declarations_is_rejected(self, tmp_path):
        """2 件目以降の宣言どうしでも表示名の重複は拒否される（B-237）。"""
        second = _VALID_TERRARIUM_TOML.replace("terrarium_aws", "terrarium_aws2")
        path = tmp_path / "dem_sources.toml"
        path.write_text(_VALID_TERRARIUM_TOML + second, encoding="utf-8")
        specs, reports = dem_sources.load_user_sources(str(path))
        assert len(specs) == 1
        assert any(r[1] == "dem_src_display_name_duplicate" for r in reports)


class TestLoadFromAndResolve:

    def test_load_from_missing_file_leaves_only_gsi(self, tmp_path):
        dem_sources.load_from(str(tmp_path / "nope.toml"))
        assert dem_sources.all_sources() == (dem_sources.GSI_DEM,)
        assert dem_sources.load_reports() == []

    def test_load_from_valid_file_extends_all_sources(self, tmp_path):
        path = tmp_path / "dem_sources.toml"
        path.write_text(_VALID_TERRARIUM_TOML, encoding="utf-8")
        dem_sources.load_from(str(path))
        ids = [s.source_id for s in dem_sources.all_sources()]
        assert ids == ["gsi_dem", "terrarium_aws"]

    def test_resolve_finds_user_source(self, tmp_path):
        path = tmp_path / "dem_sources.toml"
        path.write_text(_VALID_TERRARIUM_TOML, encoding="utf-8")
        dem_sources.load_from(str(path))
        assert dem_sources.resolve("terrarium_aws").source_id == "terrarium_aws"

    def test_resolve_falls_back_to_gsi_for_unknown_id(self):
        assert dem_sources.resolve("no-such-source") is dem_sources.GSI_DEM

    def test_resolve_falls_back_to_gsi_for_known_default(self):
        assert dem_sources.resolve("gsi_dem") is dem_sources.GSI_DEM
