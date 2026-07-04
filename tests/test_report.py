"""
tests/test_report.py
====================
report.py（バッチ出力層・ヘッドレス）の KML / PNG 生成のユニットテスト。

save_path_kml / save_summary_kml は Google Earth に渡す成果物そのものだが、
従来はカバレッジ計測外で退行を止める仕掛けが無かった。ネットワーク・GUI 無しの
純粋なファイル出力なので、well-formed XML と KML の座標順序（lon,lat,alt）という
壊れやすい不変条件をここで守る。PNG/HTML 経路は render_path_map_b64 を
monkeypatch してネットワーク無しでスモークする。

HTML レポートの内容検証（地図埋め込み・座標表記）は test_batch.py 側が担う。
"""

import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

import batch
import i18n
import models
import report
import simulation as sim

_KML_NS = "{http://www.opengis.net/kml/2.2}"


def _make_result(status: str = "OK") -> models.LinkBudgetResult:
    return models.LinkBudgetResult(
        eirp=23.0, fspl=100.0, diff_loss=0.0, veg_loss=0.0,
        env_loss=6.0, rain_loss=0.0, gas_loss=0.0,
        total_loss=106.0, p_rx=-83.0,
        actual_margin=2.0, status=status,
        current_k=10.0, blocked_ratio=0.0, slant_dist_km=1.0,
        diff_method="single", env_type="los",
    )


# ============================================================
# _find_obs_segments / _kml_line_coords（純関数）
# ============================================================
class TestFindObsSegments:

    def test_empty_mask(self):
        assert report._find_obs_segments(np.array([], dtype=bool)) == []

    def test_all_false(self):
        assert report._find_obs_segments(np.zeros(5, dtype=bool)) == []

    def test_all_true_is_single_segment(self):
        assert report._find_obs_segments(np.ones(4, dtype=bool)) == [(0, 3)]

    def test_multiple_segments_inclusive_ends(self):
        mask = np.array([False, True, True, False, True], dtype=bool)
        assert report._find_obs_segments(mask) == [(1, 2), (4, 4)]


class TestKmlLineCoords:

    def test_lon_lat_alt_order(self):
        """KML の座標は lon,lat,alt 順（lat,lon に入れ替わる退行を止める）。"""
        out = report._kml_line_coords(
            np.array([34.5429]), np.array([132.4118]), np.array([10.0])
        )
        assert out.strip() == "132.411800,34.542900,10.0"

    def test_one_line_per_sample(self):
        out = report._kml_line_coords(
            np.array([34.0, 34.1, 34.2]),
            np.array([132.0, 132.1, 132.2]),
            np.array([0.0, 1.0, 2.0]),
        )
        assert len(out.splitlines()) == 3


# ============================================================
# save_path_kml（per-path の path.kml）
# ============================================================
class TestSavePathKml:

    def _render(self, tmp_path, terrain, params_dict, status="OK") -> str:
        params = sim.SimParams(params_dict)
        report.save_path_kml(
            terrain, _make_result(status), params, 30.0, 10.0, str(tmp_path)
        )
        with open(os.path.join(str(tmp_path), "path.kml"), encoding="utf-8") as f:
            return f.read()

    def _peak_terrain(self):
        """中央に鋭いピークを持つ地形（LoS−F1 を確実に遮蔽する）。"""
        raw = np.zeros(100)
        raw[45:55] = 200.0
        return models.calculate_terrain_profile(
            raw, 34.5429, 132.4118, 34.5389, 132.4050
        )

    def test_wellformed_kml_document(self, tmp_path, flat_terrain, default_params_dict):
        text = self._render(tmp_path, flat_terrain, default_params_dict)
        root = ET.fromstring(text)  # パース失敗＝壊れた XML で fail
        assert root.tag == _KML_NS + "kml"

    def test_tx_rx_points_use_lon_first(self, tmp_path, flat_terrain, default_params_dict):
        text = self._render(tmp_path, flat_terrain, default_params_dict)
        # conftest 座標: TX=(34.5429,132.4118) / RX=(34.5389,132.4050)
        assert "132.411800,34.542900," in text   # TX Point
        assert "132.405000,34.538900," in text   # RX Point

    def test_contains_all_layer_folders(self, tmp_path, flat_terrain, default_params_dict):
        text = self._render(tmp_path, flat_terrain, default_params_dict)
        for name in ("Terrain Profile", "Line of Sight",
                     "1st Fresnel Zone", "Fresnel Obstruction"):
            assert name in text

    def test_los_color_green_when_ok(self, tmp_path, flat_terrain, default_params_dict):
        text = self._render(tmp_path, flat_terrain, default_params_dict, status="OK")
        assert "ff00aa00" in text

    def test_los_color_orange_when_ng(self, tmp_path, flat_terrain, default_params_dict):
        text = self._render(tmp_path, flat_terrain, default_params_dict, status="NG")
        assert "ff00a5ff" in text

    def test_flat_terrain_has_no_obstruction_placemark(
            self, tmp_path, flat_terrain, default_params_dict):
        text = self._render(tmp_path, flat_terrain, default_params_dict)
        assert "<name>Obstruction</name>" not in text

    def test_peak_terrain_marks_obstruction(self, tmp_path, default_params_dict):
        text = self._render(tmp_path, self._peak_terrain(), default_params_dict)
        assert "<name>Obstruction</name>" in text
        ET.fromstring(text)  # 遮蔽区間挿入後も well-formed


# ============================================================
# save_summary_kml（OK / NG / Error のフォルダ分け）
# ============================================================
class TestSaveSummaryKml:

    def _path_result(self, path_id, flat_terrain, default_params_dict,
                     status="OK", error=None) -> batch.PathResult:
        row = batch.PathRow(path_id, 34.5429, 132.4118, 34.5389, 132.4050, 30.0, 10.0)
        if error is not None:
            return batch.PathResult(row=row, result=None, error=error)
        return batch.PathResult(
            row=row, result=_make_result(status),
            terrain=flat_terrain, params=sim.SimParams(default_params_dict),
        )

    def _render(self, tmp_path, results) -> str:
        report.save_summary_kml(results, str(tmp_path))
        with open(os.path.join(str(tmp_path), "summary.kml"), encoding="utf-8") as f:
            return f.read()

    def _folder_names(self, text, folder) -> list[str]:
        """指定フォルダ内の Placemark 名を返す。"""
        root = ET.fromstring(text)
        for f in root.iter(_KML_NS + "Folder"):
            n = f.find(_KML_NS + "name")
            if n is not None and n.text == folder:
                return [
                    pm.find(_KML_NS + "name").text
                    for pm in f.iter(_KML_NS + "Placemark")
                ]
        raise AssertionError(f"folder {folder!r} not found")

    def test_paths_sorted_into_status_folders(
            self, tmp_path, flat_terrain, default_params_dict):
        text = self._render(tmp_path, [
            self._path_result("ok1", flat_terrain, default_params_dict, status="OK"),
            self._path_result("ng1", flat_terrain, default_params_dict, status="NG"),
            self._path_result("er1", flat_terrain, default_params_dict,
                              error=ValueError("DEM fetch failed")),
        ])
        assert self._folder_names(text, "OK") == ["ok1"]
        assert self._folder_names(text, "NG") == ["ng1"]
        assert self._folder_names(text, "Error") == ["er1"]

    def test_error_path_clamps_to_ground(
            self, tmp_path, flat_terrain, default_params_dict):
        """エラーパスは地形データが無いので高度 0＋clampToGround で描く。"""
        text = self._render(tmp_path, [
            self._path_result("er1", flat_terrain, default_params_dict,
                              error=ValueError("boom")),
        ])
        assert "clampToGround" in text
        assert "132.411800,34.542900,0 " in text
        assert "boom" in text  # エラー内容が description に残る

    def test_path_id_is_xml_escaped(self, tmp_path, flat_terrain, default_params_dict):
        text = self._render(tmp_path, [
            self._path_result("A&B<C>", flat_terrain, default_params_dict),
        ])
        assert "A&amp;B&lt;C&gt;" in text
        ET.fromstring(text)  # エスケープ漏れがあればパースで fail

    def test_empty_results_still_wellformed(self, tmp_path):
        text = self._render(tmp_path, [])
        root = ET.fromstring(text)
        assert root.tag == _KML_NS + "kml"


# ============================================================
# save_profile_png / save_path_visuals（Agg・ネットワーク無しスモーク）
# ============================================================
class TestSaveProfilePng:

    def test_writes_png_and_html_without_network(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch):
        i18n.set_lang("en")
        # 経路地図の取得（ネットワーク）は「取得失敗＝地図なし」に固定。
        monkeypatch.setattr(report.report_map, "render_path_map_b64",
                            lambda *a, **k: None)
        params = sim.SimParams(default_params_dict)
        report.save_profile_png(
            flat_terrain, _make_result(), params, 30.0, 10.0, str(tmp_path)
        )
        png_path = os.path.join(str(tmp_path), "profile.png")
        assert os.path.exists(png_path)
        with open(png_path, "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n"
        assert os.path.exists(os.path.join(str(tmp_path), "report.html"))


class TestSavePathVisuals:

    def test_skips_silently_when_result_missing(self, tmp_path):
        """実行失敗パス（result=None）は何も書かずに戻る（例外にしない）。"""
        row = batch.PathRow("p1", 34.5429, 132.4118, 34.5389, 132.4050, 30.0, 10.0)
        pr = batch.PathResult(row=row, result=None, save_dir=str(tmp_path))
        report.save_path_visuals(pr)
        assert os.listdir(str(tmp_path)) == []

    def test_writes_png_html_kml_when_complete(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch):
        i18n.set_lang("en")
        monkeypatch.setattr(report.report_map, "render_path_map_b64",
                            lambda *a, **k: None)
        row = batch.PathRow("p1", 34.5429, 132.4118, 34.5389, 132.4050, 30.0, 10.0)
        pr = batch.PathResult(
            row=row, result=_make_result(), terrain=flat_terrain,
            params=sim.SimParams(default_params_dict), save_dir=str(tmp_path),
        )
        report.save_path_visuals(pr)
        produced = set(os.listdir(str(tmp_path)))
        assert {"profile.png", "report.html", "path.kml"} <= produced
