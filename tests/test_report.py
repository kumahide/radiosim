"""
tests/test_report.py
====================
レポート出力層（report_common / report_path / report_summary・ヘッドレス）の
KML / PNG / 連結文書のユニットテスト。

save_path_kml / save_summary_kml は Google Earth に渡す成果物そのものだが、
従来はカバレッジ計測外で退行を止める仕掛けが無かった。ネットワーク・GUI 無しの
純粋なファイル出力なので、well-formed XML と KML の座標順序（lon,lat,alt）という
壊れやすい不変条件をここで守る。PNG/HTML 経路は render_path_map_b64 を
monkeypatch してネットワーク無しでスモークする。

HTML レポートの内容検証（地図埋め込み・座標表記）は test_batch.py 側が担う。
"""

import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

from core import disclosure
from core import i18n
from core import models
from core import simulation as sim
from core import terrain_grid
from report import batch
from report import report_common
from report import report_path
from report import report_summary

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
        assert report_path._find_obs_segments(np.array([], dtype=bool)) == []

    def test_all_false(self):
        assert report_path._find_obs_segments(np.zeros(5, dtype=bool)) == []

    def test_all_true_is_single_segment(self):
        assert report_path._find_obs_segments(np.ones(4, dtype=bool)) == [(0, 3)]

    def test_multiple_segments_inclusive_ends(self):
        mask = np.array([False, True, True, False, True], dtype=bool)
        assert report_path._find_obs_segments(mask) == [(1, 2), (4, 4)]


class TestKmlLineCoords:

    def test_lon_lat_alt_order(self):
        """KML の座標は lon,lat,alt 順（lat,lon に入れ替わる退行を止める）。"""
        out = report_path._kml_line_coords(
            np.array([34.5429]), np.array([132.4118]), np.array([10.0])
        )
        assert out.strip() == "132.411800,34.542900,10.0"

    def test_one_line_per_sample(self):
        out = report_path._kml_line_coords(
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
        report_path.save_path_kml(
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
        report_summary.save_summary_kml(results, str(tmp_path))
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
        monkeypatch.setattr(report_path.report_map, "render_path_map_b64",
                            lambda *a, **k: None)
        params = sim.SimParams(default_params_dict)
        report_path.save_profile_png(
            flat_terrain, _make_result(), params, 30.0, 10.0, str(tmp_path)
        )
        png_path = os.path.join(str(tmp_path), "profile.png")
        assert os.path.exists(png_path)
        with open(png_path, "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n"
        assert os.path.exists(os.path.join(str(tmp_path), "report.html"))

    def test_the_profile_figure_carries_the_elevation_source(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch):
        """**地形断面図そのものに標高データの出典が焼かれる**こと（B-134）。

        🔑 **開示の節に書いてあるだけでは足りない**＝`profile.png` は単独の
        ファイルとして配られ、台帳のサムネイルからも直接開かれるので、
        **図と出典が離れる**（地図で同じ判断をした＝[[B-133]]）。
        ⚠️ 画像の画素からは字が読めないので、**配線**（図に置いたか・どこへ）を見る。
        """
        from matplotlib.figure import Figure
        i18n.set_lang("ja")
        seen: list = []
        real = Figure.text
        monkeypatch.setattr(
            Figure, "text",
            lambda self, x, y, s, **kw: (seen.append((x, y, s)),
                                         real(self, x, y, s, **kw))[1],
        )
        monkeypatch.setattr(report_path.report_map, "render_path_map_b64",
                            lambda *a, **k: None)
        params = sim.SimParams(default_params_dict)
        report_path.save_profile_png(
            flat_terrain, _make_result(), params, 30.0, 10.0, str(tmp_path)
        )
        hit = [(x, y) for x, y, s in seen if s == disclosure.data_source_line()]
        assert hit, f"断面図に標高データの出典が無い: {[s for *_, s in seen]}"
        x, y = hit[0]
        assert x > 0.5 and y < 0.5, "出典は図の右下（軸の外）へ置く"

    def test_the_profile_source_is_readable_once_the_page_shrinks_it(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch):
        """断面図の出典が、A4 に載せた後も読める大きさであること（B-135）。

        🔴 この図は **A4 幅へ 0.31 倍**に縮んで載る＝図の中で 9pt と書いたときは
        **5.7px**まで落ちていた（帳票の最小字 8px を下回る）。
        ⇒ 大きさは `report_common.figure_text_pt` が図の幅から決める。
        """
        from matplotlib.figure import Figure
        i18n.set_lang("ja")
        seen: list = []
        real = Figure.text
        monkeypatch.setattr(
            Figure, "text",
            lambda self, x, y, s, **kw: (seen.append((s, kw.get("fontsize"))),
                                         real(self, x, y, s, **kw))[1],
        )
        monkeypatch.setattr(report_path.report_map, "render_path_map_b64",
                            lambda *a, **k: None)
        params = sim.SimParams(default_params_dict)
        report_path.save_profile_png(
            flat_terrain, _make_result(), params, 30.0, 10.0, str(tmp_path)
        )
        pt = next(fs for s, fs in seen if s == disclosure.data_source_line())
        width_px = report_common.PROFILE_FIGSIZE[0] * report_path._PROFILE_DPI
        on_page = (pt * report_path._PROFILE_DPI / 72.0
                   * report_common.A4_CONTENT_WIDTH_PX / width_px)
        assert on_page == pytest.approx(report_common.MIN_FIGURE_TEXT_PX)

    @pytest.mark.parametrize("lang", ("ja", "en"))
    def test_the_profile_source_does_not_collide_with_the_axis_label(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch, lang):
        """出典が距離軸のラベルへ食い込まないこと（B-135）。

        🔴 出典は**軸ラベルと同じ行**（図の下端）に入る。字を読める大きさへ上げた
        とき、**英語では 148px 食い込んでいた**（文言が長かった）。⇒ 文言を地図の
        出典と同じ書式へ短くして解いたので、**また伸ばした日にここで落ちる**。
        ⚠️ **製品が作った図そのものを測る**＝同じ構成を組み直すと、組み方の違いが
        そのまま嘘になる（[[feedback_synthetic_cases_lie]]）。
        """
        from matplotlib.figure import Figure
        i18n.set_lang(lang)
        gaps: list[float] = []
        real = Figure.savefig

        def _spy(self, *a, **k):
            out = real(self, *a, **k)
            rend = self.canvas.get_renderer()
            src = [t for t in self.texts
                   if t.get_text() == disclosure.data_source_line()]
            if src and self.axes:
                label = self.axes[0].xaxis.label
                gaps.append(src[0].get_window_extent(rend).x0
                            - label.get_window_extent(rend).x1)
            return out

        monkeypatch.setattr(Figure, "savefig", _spy)
        monkeypatch.setattr(report_path.report_map, "render_path_map_b64",
                            lambda *a, **k: None)
        params = sim.SimParams(default_params_dict)
        report_path.save_profile_png(
            flat_terrain, _make_result(), params, 30.0, 10.0, str(tmp_path)
        )
        assert gaps, "出典か軸ラベルを測れなかった（図の組み方が変わった）"
        assert min(gaps) > 0, (
            f"[{lang}] 出典が距離軸のラベルへ {-min(gaps):.0f}px 食い込んでいる"
        )



class TestSavePathVisuals:

    def test_skips_silently_when_result_missing(self, tmp_path):
        """実行失敗パス（result=None）は何も書かずに戻る（例外にしない）。"""
        row = batch.PathRow("p1", 34.5429, 132.4118, 34.5389, 132.4050, 30.0, 10.0)
        pr = batch.PathResult(row=row, result=None, save_dir=str(tmp_path))
        report_path.save_path_visuals(pr)
        assert os.listdir(str(tmp_path)) == []

    def test_writes_png_html_kml_when_complete(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch):
        i18n.set_lang("en")
        monkeypatch.setattr(report_path.report_map, "render_path_map_b64",
                            lambda *a, **k: None)
        row = batch.PathRow("p1", 34.5429, 132.4118, 34.5389, 132.4050, 30.0, 10.0)
        pr = batch.PathResult(
            row=row, result=_make_result(), terrain=flat_terrain,
            params=sim.SimParams(default_params_dict), save_dir=str(tmp_path),
        )
        report_path.save_path_visuals(pr)
        produced = set(os.listdir(str(tmp_path)))
        assert {"profile.png", "report.html", "path.kml"} <= produced


# ============================================================
# 断片と文書の分離（report_common）＝ CSS スコープの不変条件
# ============================================================
def _selectors(css: str) -> list[str]:
    """CSS からセレクタだけを抜き出す（@media ブロックは中身を見る）。"""
    body = re.sub(r"@media[^{]*\{", "", css)      # メディアクエリの殻を外す
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)   # コメント除去
    sels: list[str] = []
    for chunk in re.findall(r"([^{}]*)\{[^{}]*\}", body):
        sel = chunk.strip().splitlines()[-1].strip() if chunk.strip() else ""
        if sel and not sel.startswith("@"):
            sels.extend(s.strip() for s in sel.split(",") if s.strip())
    return sels


class TestSheetCssIsScoped:
    """シート固有 CSS は必ず `.sheet.path` / `.sheet.summary` にスコープする。

    連結文書（report_all.html）は per-path と summary の CSS を同じ <style> に
    載せる。両者は `.sheet` / `.page-header` / `.cards` を**別値で**持つため、
    素のセレクタが1つでも残ると後勝ちで上書きされ、どちらかのレイアウトが黙って
    壊れる（画面では気づけず印刷して初めて分かる種類の壊れ方）。
    """

    def test_path_css_selectors_all_scoped(self):
        unscoped = [s for s in _selectors(report_path.path_sheet_css())
                    if ".sheet.path" not in s]
        assert unscoped == [], f"未スコープの per-path セレクタ: {unscoped}"

    def test_summary_css_selectors_all_scoped(self):
        unscoped = [s for s in _selectors(report_summary.summary_sheet_css())
                    if ".sheet.summary" not in s]
        assert unscoped == [], f"未スコープの summary セレクタ: {unscoped}"

    def test_fit_script_processes_every_sheet(self):
        """縮小フィットは文書内の全 `.fit` を対象にする（連結文書は N 枚ある）。"""
        js = report_common.fit_to_page_script()
        assert "querySelectorAll" in js
        assert re.search(r"querySelector\s*\(", js) is None, \
            "単数形 querySelector が残っている（連結文書で2枚目以降が縮まない）"


# ============================================================
# report_all.html（サマリ＋全 per-path を 1 文書へ連結・I-013）
# ============================================================
class TestSaveReportAllHtml:

    def _results(self, tmp_path, flat_terrain, default_params_dict, monkeypatch,
                 ids=("p01", "p02"), fail_id=None) -> list:
        """per-path シート断片を持つ PathResult 群を作る（ネットワーク無し）。"""
        i18n.set_lang("en")
        monkeypatch.setattr(report_path.report_map, "render_path_map_b64",
                            lambda *a, **k: None)
        params = sim.SimParams(default_params_dict)
        out = []
        for pid in ids:
            row = batch.PathRow(pid, 34.5429, 132.4118, 34.5389, 132.4050, 30.0, 10.0)
            if pid == fail_id:
                out.append(batch.PathResult(row=row, result=None,
                                            error=ValueError("DEM fetch failed")))
                continue
            save_dir = tmp_path / pid
            save_dir.mkdir()
            pr = batch.PathResult(
                row=row, result=_make_result(), terrain=flat_terrain,
                params=params, save_dir=str(save_dir),
            )
            report_path.save_path_visuals(pr)
            out.append(pr)
        return out

    def _render(self, tmp_path, results) -> str:
        report_summary.save_report_all_html(results, str(tmp_path))
        return (tmp_path / "report_all.html").read_text(encoding="utf-8")

    def test_contains_summary_and_every_path_sheet(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch):
        results = self._results(tmp_path, flat_terrain, default_params_dict, monkeypatch)
        html = self._render(tmp_path, results)
        assert html.count('class="sheet summary"') == 1
        assert html.count('class="sheet path"') == 2
        # シートは path_id のアンカーを持つ＝台帳から文書内で飛べる
        assert 'id="p01"' in html and 'id="p02"' in html

    def test_ledger_links_are_document_anchors(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch):
        """連結文書の台帳は別ファイルでなく文書内アンカーへ飛ばす。"""
        results = self._results(tmp_path, flat_terrain, default_params_dict, monkeypatch)
        html = self._render(tmp_path, results)
        assert "href='#p01'" in html
        assert "p01/report.html" not in html
        # 自分自身への導線は出さない（単体 summary.html にだけ出す）
        assert "report_all.html" not in html

    def test_failed_path_has_no_sheet_but_stays_in_ledger(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch):
        results = self._results(tmp_path, flat_terrain, default_params_dict,
                                monkeypatch, fail_id="p02")
        html = self._render(tmp_path, results)
        assert html.count('class="sheet path"') == 1
        assert "DEM fetch failed" in html      # 台帳のエラー行は残る

    def test_sheets_break_to_separate_pages(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch):
        """印刷時にシートごと改ページする（最後のシートは空ページを作らない）。"""
        results = self._results(tmp_path, flat_terrain, default_params_dict, monkeypatch)
        html = self._render(tmp_path, results)
        assert ".sheet{break-after:page}" in html
        assert ".sheet:last-of-type{break-after:auto}" in html

    def test_carries_both_sheet_stylesheets(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch):
        results = self._results(tmp_path, flat_terrain, default_params_dict, monkeypatch)
        html = self._render(tmp_path, results)
        assert ".sheet.path .cards{" in html
        assert ".sheet.summary .cards{" in html

    def test_standalone_summary_links_to_report_all(
            self, tmp_path, flat_terrain, default_params_dict, monkeypatch):
        """単体 summary.html は連結文書への導線を持ち、印刷では消える。"""
        results = self._results(tmp_path, flat_terrain, default_params_dict, monkeypatch)
        report_summary.save_summary_html(results, str(tmp_path))
        html = (tmp_path / "summary.html").read_text(encoding="utf-8")
        assert 'href="report_all.html"' in html
        assert 'class="all-link no-print"' in html
        assert ".no-print{display:none !important}" in html
        # 単体では per-path は別ファイル参照のまま
        assert "href='p01/report.html'" in html


# ============================================================
# 「結果の取扱に関する補足」節（3.0a1 / ロードマップ §3.0 の 9）
# ============================================================


def _builds_a_sheet(text: str) -> bool:
    """そのソースが**帳票の 1 面を組み立てているか**（自己同定フッタを置くか）。

    面を列挙せず**実装側の印**で数える＝新しい帳票を足しても、フッタを置いた
    時点でこのゲートの対象に入る（→ [[feedback-user-examples-are-classes]]）。
    """
    return "report_common.page_footer(" in text


def _carries_the_handling_section(text: str) -> bool:
    """そのソースが「結果の取扱に関する補足」を出しているか。"""
    return ("handling_notes_html(" in text) or ("handling_text(" in text)


class TestEveryArtifactFaceCarriesTheHandlingSection:
    """**帳票を組み立てる面は、必ず開示の節も出す**（3.0a1 のクラス点検）。

    🔑 開示を書く仕事には「無いことの検査」を対で置く
    （→ [[feedback-promote-recurring-checks]]）＝*ここまでは大丈夫*という主張は、
    反例 1 つで嘘になる。⇒ **節を持たない帳票が 1 面でもあれば落とす。**
    """

    @staticmethod
    def _sources():
        root = os.path.join(os.path.dirname(__file__), "..")
        out = []
        for layer in ("core", "report", "views"):
            d = os.path.join(root, layer)
            for name in sorted(os.listdir(d)):
                if not name.endswith(".py") or name == "report_common.py":
                    continue
                path = os.path.join(d, name)
                out.append((layer + "/" + name,
                            open(path, encoding="utf-8").read()))
        return out

    def test_the_scan_finds_the_faces_at_all(self):
        """ゲートが空振りしていないこと（4 種の A4 シートが見えているか）。"""
        faces = [n for n, t in self._sources() if _builds_a_sheet(t)]
        assert len(faces) >= 4, "帳票の面を数え切れていない: " + repr(faces)

    def test_no_sheet_is_published_without_it(self):
        offenders = [n for n, t in self._sources()
                     if _builds_a_sheet(t) and not _carries_the_handling_section(t)]
        assert not offenders, (
            "開示の節を持たない帳票がある: " + repr(offenders) + "。"
            "`report_common.handling_notes_html(models.scope_notes(...))` を"
            "フッタの前に置くこと（3.0a1）＝成果物は一人歩きするので、"
            "前提と適用範囲は帳票そのものが持つ"
        )

    def test_the_plain_text_report_carries_it_too(self):
        """`report.txt` は HTML と別の書き手なので、別に見る。"""
        root = os.path.join(os.path.dirname(__file__), "..")
        text = open(os.path.join(root, "core", "simulation.py"),
                    encoding="utf-8").read()
        assert _carries_the_handling_section(text), (
            "report.txt だけ開示を持たない（HTML の帳票と同じ 1 本を引くこと）"
        )

    @pytest.mark.parametrize("text,expected", [
        ("report_common.page_footer(i18n.t('x'))", (True, False)),
        ("report_common.page_footer(x) + handling_notes_html(y)", (True, True)),
        ("disclosure.handling_text(keys)", (False, True)),
        ("# フッタは page_footer が置く", (False, False)),
    ])
    def test_the_detector_catches_what_it_claims(self, text, expected):
        assert (_builds_a_sheet(text), _carries_the_handling_section(text)) == expected


class TestHandlingSectionContent:
    """節の**中身**＝差し込みが埋まっていること・両言語にあること。"""

    def teardown_method(self):
        # 言語は他のテストと共有の状態なので、触ったら必ず戻す（I-108）。
        i18n.set_lang("en")

    @pytest.mark.parametrize("lang", ["en", "ja"])
    def test_every_scope_note_has_wording_in_both_languages(self, lang):
        i18n.set_lang(lang)
        for key in models.SCOPE_NOTE_ORDER:
            text = i18n.t("html_scope_" + key)
            assert text != "html_scope_" + key, (
                lang + " に " + key + " の字が無い（キー名がそのまま出る）"
            )

    @pytest.mark.parametrize("lang", ["en", "ja"])
    def test_no_placeholder_survives_into_the_report(self, lang):
        """🔑 範囲の数字は式の定数から差し込む＝波括弧が利用者の目に出ないこと。"""
        i18n.set_lang(lang)
        for line in disclosure.handling_lines(models.SCOPE_NOTE_ORDER):
            assert "{" not in line and "}" not in line, (
                "差し込みが埋まっていない: " + line
            )

    def test_the_bounds_come_from_the_formulas_not_from_the_prose(self):
        """字の中の数字が、式の使っている定数と同じであること。"""
        i18n.set_lang("en")
        lines = " ".join(disclosure.handling_lines(models.SCOPE_NOTE_ORDER))
        assert str(int(models.RAIN_TABLE_MAX_GHZ)) in lines
        assert str(int(models.GAS_RANGE_GHZ[1])) in lines
        assert str(int(models.VEG_COEFF_RANGE_GHZ[1])) in lines

    def test_the_earth_k_in_the_prose_is_the_one_the_formula_used(self):
        """🔴 **K の字は、曲率補正が実際に使った値そのもの**であること。

        ⚠️ 定数と突き合わせない＝**式の出力**（既定で作った `TerrainProfile` の
        `earth_k`）と突き合わせる。そうしないと「字にも定数にも 1.33 と書いてあり、
        式だけ別の値を使っている」が緑のまま通る（刻印の怖さはこの向き）。
        """
        i18n.set_lang("en")
        t = models.calculate_terrain_profile(
            np.array([0.0, 0.0, 0.0]), 35.0, 139.0, 35.01, 139.01,
        )
        line = disclosure.handling_lines(("earth_k_fixed",))[0]
        assert f"{t.earth_k:.2f}" in line, (line, t.earth_k)

    def test_the_resolution_step_reaches_every_face(self):
        """🔴 **刻印を足しただけでは届かない**＝呼ぶ側が段階を渡すこと（B-128）。

        `resolution` を渡し忘れた面は、**帳票が「どの解像度で出したか」を黙ったまま**
        値だけ出す＝*刻印を書いたのに出ていない*という、いちばん気づけない壊れ方に
        なる（→ [[feedback-promote-recurring-checks]] の「開示を書く仕事は
        『無いことの検査』を対で置く」）。⚠️ 見ているのは**呼び出しの形**で、
        「節を持つか」は `TestHandlingSectionIsOnEverySheet` の側。
        """
        root = os.path.join(os.path.dirname(__file__), "..")
        offenders = []
        for rel in ("core/simulation.py", "report/report_path.py",
                    "report/report_summary.py", "report/report_multihop.py",
                    "report/report_scenario.py"):
            text = open(os.path.join(root, *rel.split("/")), encoding="utf-8").read()
            calls = text.count("models.scope_notes(")
            passed = text.count("resolution=")
            if calls == 0 or passed < calls:
                offenders.append(f"{rel}（呼び出し {calls} 件・渡している {passed} 件）")
        assert not offenders, (
            "解像度の段階を刻印へ渡していない面がある: " + repr(offenders)
        )

    def test_the_resolution_spacings_come_from_the_resolver(self):
        """字の中の 5 / 10 / 20 m は、点数を解く側の定数そのものであること。

        ⚠️ **「その数が行のどこかに在る」では緩すぎる**＝同じ行が
        「20 m → 5 m で +14.8%」という*別の 2 つの間隔*も名乗るので、
        目標間隔を直書きに変えても素通りした（変異検証で実際に踏んだ）。
        ⇒ **名乗っている場所ごと**見る（この節は英語で確かめる）。
        """
        i18n.set_lang("en")
        for level, spacing in terrain_grid.RESOLUTION_SPACING_M.items():
            line = disclosure.handling_lines((f"resolution_{level}",))[0]
            assert f"({spacing:g} m target spacing)" in line, (level, line)
        # 段階を細かくすると増える、と言うための 2 つの端も定数から来ること。
        span = terrain_grid.RESOLUTION_SPACING_M.values()
        line = disclosure.handling_lines(("resolution_medium",))[0]
        assert f"from {max(span):g} m to {min(span):g} m" in line, line

    def test_the_calibration_seat_is_always_present_and_empty(self):
        """較正の席（3.5 で埋まる）＝**空でも欄を置く**。"""
        i18n.set_lang("en")
        line = disclosure.calibration_line()
        assert i18n.t("html_calib_profile") in line
        assert i18n.t("html_calib_none") in line

    def test_the_text_report_lists_every_line_it_was_given(self):
        i18n.set_lang("en")
        keys = models.scope_notes(430.0, diff_method="bullington",
                                  rain_rate=10.0, veg_h=5.0)
        text = disclosure.handling_text(keys)
        assert "[NOTES ON HANDLING THIS RESULT]" in text
        for line in disclosure.handling_lines(keys):
            assert line in text
        assert disclosure.calibration_line() in text
        assert disclosure.data_source_line() in text

    def test_the_elevation_source_is_always_stated(self):
        """標高データの出典は**条件によらず常に出る**こと（B-134）。

        ⚠️ 適用範囲の刻印（`html_scope_*`）と違い、出典は*事実*なので
        `models.scope_notes()` の判定に混ぜない＝周波数や入力を変えても消えない。
        """
        for lang in ("en", "ja"):
            i18n.set_lang(lang)
            line = disclosure.data_source_line()
            assert line == i18n.t("html_elev_source") and line
            assert line not in disclosure.handling_lines(models.SCOPE_NOTE_ORDER)
        i18n.set_lang("ja")
        assert "地理院" in disclosure.data_source_line()

    def test_the_html_section_escapes_and_lists_every_line(self):
        i18n.set_lang("en")
        keys = models.scope_notes(2400.0, diff_method="single")
        html = report_common.handling_notes_html(keys)
        assert html.count("<li>") == len(keys)
        assert 'class="handling"' in html
        assert i18n.t("html_handling_title") in html
        # 出典（B-134）は**全帳票が通る 1 か所**＝この節に入れることで 5 面へ届く。
        assert disclosure.data_source_line() in html
