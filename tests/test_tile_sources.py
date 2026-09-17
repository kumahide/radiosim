"""
tests/test_tile_sources.py
===========================
tile_sources.py（背景地図タイルソースの宣言ファイル・3.5 段3・I-152）の
ユニットテスト。`tests/test_dem_sources.py` と同じ形。
"""

import textwrap

from core import tile_sources


_VALID_TOML = textwrap.dedent("""\
    [[source]]
    source_id = "osm"
    display_name = "OpenStreetMap"
    url = "https://tile.example.invalid/{z}/{x}/{y}.png"
    max_zoom = 19
    attribution = "(c) OpenStreetMap contributors"
    terms_url = "https://example.invalid/copyright"
    """)


class TestValidation:

    def test_valid_declaration_is_accepted(self, tmp_path):
        path = tmp_path / "tile_sources.toml"
        path.write_text(_VALID_TOML, encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert reports == []
        assert len(specs) == 1
        spec = specs[0]
        assert spec.source_id == "osm"
        assert spec.display_name == "OpenStreetMap"
        assert spec.max_zoom == 19

    def test_missing_file_yields_empty(self, tmp_path):
        specs, reports = tile_sources.load_user_sources(str(tmp_path / "nope.toml"))
        assert specs == []
        assert reports == []

    def test_unreadable_toml_is_reported_not_raised(self, tmp_path):
        path = tmp_path / "tile_sources.toml"
        path.write_text("this is not [ valid toml", encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert specs == []
        assert len(reports) == 1
        assert reports[0][1] == "dem_src_file_unreadable"

    def test_one_broken_source_does_not_block_the_others(self, tmp_path):
        broken_and_valid = _VALID_TOML + textwrap.dedent("""\

            [[source]]
            source_id = "broken"
            display_name = "Broken"
            url = "not-a-url"
            max_zoom = 10
            attribution = "x"
            terms_url = "https://example.invalid"
            """)
        path = tmp_path / "tile_sources.toml"
        path.write_text(broken_and_valid, encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert [s.source_id for s in specs] == ["osm"]
        assert [r for r in reports if r[0] == "broken"]

    def test_reserved_source_id_is_rejected(self, tmp_path):
        toml = _VALID_TOML.replace('source_id = "osm"', 'source_id = "pale"')
        path = tmp_path / "tile_sources.toml"
        path.write_text(toml, encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert specs == []
        assert reports[0][1] == "tile_src_id_reserved"

    def test_bad_source_id_is_rejected(self, tmp_path):
        toml = _VALID_TOML.replace('source_id = "osm"', 'source_id = "has space"')
        path = tmp_path / "tile_sources.toml"
        path.write_text(toml, encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert specs == []
        assert reports[0][1] == "dem_src_bad_id"

    def test_http_url_is_rejected(self, tmp_path):
        toml = _VALID_TOML.replace(
            "https://tile.example.invalid", "http://tile.example.invalid")
        path = tmp_path / "tile_sources.toml"
        path.write_text(toml, encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert specs == []
        assert reports[0][1] == "tile_src_bad_url"

    def test_url_missing_placeholder_is_rejected(self, tmp_path):
        toml = _VALID_TOML.replace(
            "https://tile.example.invalid/{z}/{x}/{y}.png",
            "https://tile.example.invalid/{z}/{x}.png")
        path = tmp_path / "tile_sources.toml"
        path.write_text(toml, encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert specs == []
        assert reports[0][1] == "tile_src_bad_url"

    def test_non_integer_max_zoom_is_rejected(self, tmp_path):
        toml = _VALID_TOML.replace("max_zoom = 19", 'max_zoom = "19"')
        path = tmp_path / "tile_sources.toml"
        path.write_text(toml, encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert specs == []
        assert reports[0][1] == "tile_src_bad_max_zoom"

    def test_out_of_range_max_zoom_is_rejected(self, tmp_path):
        toml = _VALID_TOML.replace("max_zoom = 19", "max_zoom = 0")
        path = tmp_path / "tile_sources.toml"
        path.write_text(toml, encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert specs == []
        assert reports[0][1] == "tile_src_bad_max_zoom"

    def test_missing_attribution_is_rejected(self, tmp_path):
        toml = _VALID_TOML.replace(
            'attribution = "(c) OpenStreetMap contributors"\n', "")
        path = tmp_path / "tile_sources.toml"
        path.write_text(toml, encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert specs == []
        assert reports[0][1] == "dem_src_missing_field"

    def test_duplicate_source_id_is_rejected(self, tmp_path):
        doubled = _VALID_TOML + _VALID_TOML
        path = tmp_path / "tile_sources.toml"
        path.write_text(doubled, encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert len(specs) == 1
        assert any(r[1] == "dem_src_id_duplicate" for r in reports)

    def test_duplicate_display_name_is_rejected(self, tmp_path):
        second = _VALID_TOML.replace("osm", "osm2")
        path = tmp_path / "tile_sources.toml"
        path.write_text(_VALID_TOML + second, encoding="utf-8")
        specs, reports = tile_sources.load_user_sources(str(path))
        assert len(specs) == 1
        assert any(r[1] == "tile_src_display_name_duplicate" for r in reports)


class TestLoadFromAndAllSources:

    def test_load_from_missing_file_leaves_all_sources_empty(self, tmp_path):
        tile_sources.load_from(str(tmp_path / "nope.toml"))
        assert tile_sources.all_sources() == ()
        assert tile_sources.load_reports() == []

    def test_load_from_valid_file_populates_all_sources(self, tmp_path):
        path = tmp_path / "tile_sources.toml"
        path.write_text(_VALID_TOML, encoding="utf-8")
        tile_sources.load_from(str(path))
        ids = [s.source_id for s in tile_sources.all_sources()]
        assert ids == ["osm"]
