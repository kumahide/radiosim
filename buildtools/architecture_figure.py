"""
buildtools/architecture_figure.py
=================================
層構成図（`docs/images/architecture_ja.svg` / `architecture_en.svg`）を**1 つの表から
生成する**（B-209・3.3 段8）。

**なぜ生成物にしたか**: 図は座標を手で書いていて、行を 1 本足すたびに連動する箇所
（行の座標・カードの高さと幅・帯の高さ・背景・全体の height・下に置いた字・列の
割り付け）のどれかが漏れ、**3 度崩れた**（B-147 / B-153 / B-209）。壊れ方を見つける
たびにゲートを足す方式は、数え漏れの分だけ次に壊れる。⇒ **座標は人が触る場所から
消す**＝この表だけを書き、座標はすべて計算で決める。ja / en は同じ表から出るので、
幾何の一致も作り方で保証される（幅と高さは 2 言語の大きい方で割り付ける）。

**字の幅は書体に依らない上限で見積もる**: CI（ubuntu）には Windows の書体が無く、
閲覧者の書体も `font-family` の候補のどれになるか分からない。⇒ 候補の書体で**一度だけ
実測した上限**を定数（`_ASCII_EM`）として持つ。1 つの書体で測って詰めると、別の書体で
また崩れる。⚠️ 上限は**実測した書体（`_MEASURED_FONTS`）の中での上限**＝候補の外の
書体（macOS の Hiragino Sans / Helvetica など）は測っていない。

使い方（リポジトリの根で）:
    & "$env:RADIOSIM_PYTHON" buildtools/architecture_figure.py            # 2 枚を書き出す
    & "$env:RADIOSIM_PYTHON" buildtools/architecture_figure.py --check    # 差があれば 1 で終わる
    & "$env:RADIOSIM_PYTHON" buildtools/architecture_figure.py --measure  # 上限表を実測し直して表示

ゲート: `tests/test_docs_consistency.py` の §2b が「リポジトリの SVG がこの出力と一致
すること」と「上限表が実測の書体を下回らないこと（Windows のみ）」を検査する。
"""

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = {"ja": ROOT / "docs/images/architecture_ja.svg",
           "en": ROOT / "docs/images/architecture_en.svg"}
LANGS = ("ja", "en")

# =============================================================================
# 表（ここだけを書く）
# =============================================================================
#: (ja, en) の組。
Text = tuple[str, str]
#: (モジュール名, 役割 ja, 役割 en)。
Row = tuple[str, str, str]


@dataclass
class Card:
    """カード＝題と、1〜2 列の行。列の数がそのまま格子の列をいくつ跨ぐかになる。"""
    title: Text
    columns: list[list[Row]]

    @property
    def span(self) -> int:
        return len(self.columns)


@dataclass
class Notes:
    """カードの横に置く注記（枠なし）。`span` 列ぶんの幅を使う。"""
    lines: list[Text]
    span: int = 2


@dataclass
class Band:
    """帯＝1 つの層。`rows` の各行は、跨ぐ列の合計が `GRID_COLUMNS` になるように並べる。"""
    name: Text
    rule: Text
    fill: str
    stroke: str
    ink: str
    rows: list[list[Card | Notes]] = field(default_factory=list)


GRID_COLUMNS = 3

TITLE: Text = ("RadioSim Pro — レイヤー構成", "RadioSim Pro — Layer Structure")
ARIA_TITLE: Text = ("RadioSim Pro レイヤー構成", "RadioSim Pro layer structure")
DESC: Text = (
    "層は views / report / core の 3 つ。依存は views → report → core の一方向で、"
    "各層に属するモジュールを役割つきで並べた図。",
    "Three layers — views, report and core — with a one-way dependency from views "
    "to report to core, listing the modules of each layer with their role.",
)
LEAD: Text = (
    "層はディレクトリで表す。依存は views → report → core の一方向で、2 つの層から"
    "使うものは「下」へ置く（shared/ のような箱は作らない）。",
    "Layers are directories. The dependency runs one way: views → report → core. "
    "Anything used by two layers moves down, never into a shared/ box.",
)
FOOTER: list[Text] = [
    ("main.py は層に属さない（起動して views を組み立てるだけ）。層をまたぐ逆流と "
     "import 時の循環は tests/test_layers.py が機械で止める。",
     "main.py belongs to no layer (it starts the app and assembles views). Upward "
     "dependencies and import-time cycles are stopped by tests/test_layers.py."),
    # ⚠️ 生成スクリプトの名前は書かない＝図は exe に同梱されるが、スクリプトは同梱
    # されない（同梱の読者に届かないファイルを名指ししない）。手順は同梱の開発者
    # ガイドの節に置き、そちらを指す。
    ("この図は 1 つの表から生成している（座標を手で書かない）。作り直し方と検査は"
     "開発者ガイドの「レイヤー構成」にある。",
     "This figure is generated from a single table (no hand-written coordinates); "
     "see “Layer Structure” in the developer guide for how to regenerate and check it."),
]

#: 生成物の冒頭に置く注記（SVG のコメント＝描画されない）。
GENERATED_NOTE: Text = (
    "生成物（B-209）: 手で直さない。表を直して作り直す（開発者ガイド「レイヤー構成」）。",
    "Generated (B-209): do not edit by hand. Edit the table and regenerate "
    "(developer guide, “Layer Structure”).",
)

BANDS: list[Band] = [
    Band(
        name=("views/ — 画面（tkinter）", "views/ — the screen (tkinter)"),
        rule=("副作用あり。計算と I/O は下の層へ委譲する。",
              "Has side effects. Delegates calculation and I/O downward."),
        fill="#eef2fb", stroke="#c3d0ec", ink="#24406e",
        rows=[
            [
                Card(("ランチャー（本体＋ Mixin）", "Launcher (core + mixins)"), [[
                    ("launcher.py", "本体＝入力・実行・進捗", "form, run, progress"),
                    ("launcher_menu.py", "メニューバーと操作", "menu bar and its actions"),
                    ("launcher_project.py", ".rsproj の保存・読込", ".rsproj save / load"),
                    ("launcher_windows.py", "子窓の開閉と通知", "child windows, notifications"),
                ]]),
                Card(("地図（本体＋ Mixin）", "Map (core + mixins)"), [[
                    ("map_window.py", "本体＝モード切替", "mode switching, widget"),
                    ("map_picks.py", "地点の指定と経路の描画", "picking sites, drawing paths"),
                    ("map_cache.py", "DEM キャッシュの操作", "DEM cache operations"),
                    ("map_adapter.py", "tkintermapview の唯一の依存点",
                     "sole dependency on tkintermapview"),
                    ("map_style.py", "描画定数の単一ソース", "single source of constants"),
                ]]),
                Card(("複数経路（本体＋ Mixin）", "Multiple Paths (core + mixins)"), [[
                    ("batch_builder.py", "本体＝共通設定・案件", "common settings, case info"),
                    ("batch_table.py", "入力表（行の操作）", "input table (row editing)"),
                    ("batch_io.py", "CSV 入出力と雛形", "CSV import/export, template"),
                    ("batch_run.py", "実行と進捗", "execution and progress"),
                ]]),
            ],
            [
                Card(("そのほかの窓", "Other windows"), [[
                    ("graph.py", "グラフ窓", "graph window"),
                    ("scenario.py", "条件探索（比較 / スイープ）", "compare / sweep"),
                    ("multihop.py", "中継経路（地点が入力面）", "relay path (waypoints in)"),
                    ("multihop_map.py", "中継経路と地図の受け渡し", "relay path <-> map handoff"),
                ]]),
                Card(("全窓で共有する部品", "Shared by every window"), [
                    [
                        ("dialogs.py", "親中央のモーダル", "modals centered on the parent"),
                        ("errors.py", "未捕捉例外の受け皿", "sink for unhandled exceptions"),
                        ("progress.py", "進捗の伝送（キュー）", "progress transport (queue)"),
                        ("frozen_common.py", "凍結帯の項目の単一ソース", "Frozen band’s item list"),
                    ],
                    [
                        ("theme.py", "テーマ色・UI 書体", "theme colors, UI fonts"),
                        ("window_fit.py", "窓の寸法（見切れ防止）", "window sizing (no clipping)"),
                        ("tooltip.py", "入力ヒント", "input hints"),
                        ("window_scroll.py", "入らない時の逃げ道", "scroll escape (too big)"),
                        ("title_bar.py", "タイトルバー（OS 描画）", "title bar (drawn by the OS)"),
                    ],
                ]),
            ],
        ],
    ),
    Band(
        name=("report/ — 出力を作る層", "report/ — the layer that produces output"),
        rule=("ヘッドレス＝画面が無くても動く（テスト・CI もここを直接まわす）。",
              "Headless: it runs with no display (tests and CI drive it directly)."),
        fill="#eefaf3", stroke="#bfe3d1", ink="#1c5c3f",
        rows=[
            [
                Card(("実行エンジン", "Execution engines"), [[
                    ("batch.py", "複数経路（CSV・一括実行）", "multiple paths (CSV + run)"),
                    ("multihop.py", "中継経路（区間を導出・min）", "relay path (sections, min)"),
                    ("project.py", ".rsproj の読み書き", ".rsproj read / write"),
                ]]),
                Card(("成果物の生成", "Artifact generation"), [
                    [
                        ("report_common.py", "A4 骨格・ヘッダ / フッタ", "A4 skeleton, header / footer"),
                        ("report_path.py", "経路ごと（PNG/HTML/KML）", "per path (PNG/HTML/KML)"),
                        ("report_summary.py", "サマリ・全ページ連結", "summary, all-pages document"),
                    ],
                    [
                        ("report_scenario.py", "条件探索（折れ線＋表）", "line chart + table"),
                        ("report_multihop.py", "中継（合成シート・hops.csv）", "relay sheet, hops.csv"),
                        ("report_map.py", "経路地図（タイル取得＋合成）", "path map (tiles, stitched)"),
                    ],
                ]),
            ],
            [
                Card(("共有する描画部品", "Shared drawing parts"), [[
                    ("map_graphics.py", "地図の重ね描き（純 PIL）", "map overlays (pure PIL)"),
                    ("mpl_fonts.py", "matplotlib 日本語書体", "matplotlib Japanese fonts"),
                ]]),
                Notes([
                    ("画面（views/）とレポートが同じ絵を出すために、描画も「下」へ置いている。",
                     "The screen (views/) and the reports must draw the same picture, "
                     "so the drawing lives down here too"),
                    ("＝ 2 つの層から使うものは共有の箱ではなく、下の層の住人にする。",
                     "— what two layers share becomes a resident of the lower layer, "
                     "not a shared/ box."),
                ]),
            ],
        ],
    ),
    Band(
        name=("core/ — 土台（計算・データ・設定）",
              "core/ — the foundation (calculation, data, configuration)"),
        rule=("tkinter も matplotlib も引かない。", "Imports neither tkinter nor matplotlib."),
        fill="#fff6e9", stroke="#f0dcbe", ink="#7a4f16",
        rows=[
            [
                Card(("オーケストレーション", "Orchestration"), [[
                    ("simulation.py", "DEM 取得管理・地形・計算", "DEM fetch, terrain, calc"),
                    ("scenario.py", "条件探索の共有ランナー", "condition explorer runner"),
                ]]),
                Card(("純粋（副作用ゼロ）", "Pure (no side effects)"), [[
                    ("models.py", "伝搬計算", "propagation calculation"),
                    ("coords.py", "座標表記（DD ⇔ DMS）", "coordinates (DD ⇔ DMS)"),
                    ("units.py", "距離の表示整形（km → m）", "distance display (km → m)"),
                    ("output_contract.py", "成果物 CSV の列仕様", "artifact CSV column spec"),
                    ("terrain_grid.py", "解像度の段階 → 点数", "resolution → sample count"),
                    ("diffraction.py", "回折損（Bullington・J(ν)）", "diffraction loss (Bullington)"),
                    ("disclosure.py", "帳票の開示の字（前提・適用範囲）", "report disclosure wording"),
                ]]),
                Card(("設定・環境・文字列", "Config, environment, strings"), [[
                    ("config.py", "設定 I/O・検証・ログ", "config I/O, validation, log"),
                    ("runtime_env.py", "宣言された実行系の判定", "the declared interpreter"),
                    ("env_facts.py", "環境事実の収集層（診断・刻印）",
                     "env facts (diagnostics, stamps)"),
                    ("i18n.py", "文字列表＋ lang/*.json", "string table + lang/*.json"),
                    ("version.py", "バージョン情報", "version information"),
                    ("failure.py", "失敗メッセージの型", "failure message shape"),
                    ("diagnostics.py", "診断パッケージ（成果物は既定除外）",
                     "diagnostic package (artifacts opt-in)"),
                ]]),
            ],
            [
                Card(("DEM 層", "DEM layer"), [[
                    ("dem.py", "DEM・タイルの取得とキャッシュ I/O",
                     "DEM / tile single fetch, cache I/O"),
                    ("dem_sources.py", "DEM ソースの宣言（URL・復号）",
                     "source declarations (URL, decoding)"),
                    ("dem_cache.py", "キャッシュ棚卸し・カバレッジ・削除",
                     "cache inventory, coverage, deletion"),
                    ("dem_prefetch.py", "面での事前取得", "area prefetch"),
                ]]),
                Notes([
                    ("HTTP・プロキシ・ディスクキャッシュは dem.py の中だけ。",
                     "HTTP, proxies and the disk cache exist inside dem.py only"),
                    ("＝ ほかの層は「取れた標高」しか知らない。",
                     "— every other layer only ever sees elevations that were already fetched."),
                ]),
            ],
        ],
    ),
]

# =============================================================================
# 字の幅の上限（書体に依らない）
# =============================================================================
#: 各図の `font-family`。上限はこの候補（のうち Windows にある書体）で測る。
_FONT_FAMILY = {
    "ja": "'BIZ UDPGothic','Meiryo','Yu Gothic','Hiragino Sans',sans-serif",
    "en": "Arial,Helvetica,'Segoe UI',sans-serif",
}
_MONO_FAMILY = "Consolas,'Courier New',monospace"

#: 上限を実測した書体（`--measure` がこれを開く）。(ファイル名, ttc の index)。
#: 🔑 **言語ごとに分ける**＝英語の図は Arial / Segoe UI で描かれるのに、Meiryo の
#: 広い英数字（数字 0.76 em）まで上限に入れると幅が 2 割ほど過大になり、図が無駄に
#: 広がって縮小表示で字が読めなくなる。上限は「その図の候補の書体のどれで描かれても」
#: の意味で取れば足りる。
#: ⚠️ BIZ-UDGothic*.ttc の index 1 が図の指定する BIZ UDPGothic（プロポーショナル）。
_MEASURED_FONTS: dict[str, list[tuple[str, int]]] = {
    "ja-regular": [("BIZ-UDGothicR.ttc", 1), ("meiryo.ttc", 0), ("YuGothR.ttc", 0)],
    "ja-bold": [("BIZ-UDGothicB.ttc", 1), ("meiryob.ttc", 0), ("YuGothB.ttc", 0)],
    "en-regular": [("arial.ttf", 0), ("segoeui.ttf", 0)],
    "en-bold": [("arialbd.ttf", 0), ("segoeuib.ttf", 0)],
    "mono": [("consola.ttf", 0), ("cour.ttf", 0)],
}

#: ASCII（0x20〜0x7E）の 1 字ごとの幅の上限（em）＝上の書体の最大値を 1/1000 で切り上げ。
#: ⚠️ **手で書き換えない**＝`--measure` の出力を貼る。Windows のテストが実測と突き合わせる。
_ASCII_EM: dict[str, tuple[float, ...]] = {
    "ja-regular": (
        0.340, 0.379, 0.470, 0.920, 0.800, 1.032, 0.890, 0.290, 0.490, 0.490, 0.621, 0.840,
        0.349, 0.480, 0.349, 0.500, 0.760, 0.630, 0.760, 0.760, 0.760, 0.760, 0.760, 0.760,
        0.760, 0.760, 0.430, 0.430, 0.840, 0.840, 0.840, 0.775, 1.020, 0.760, 0.770, 0.800,
        0.840, 0.700, 0.660, 0.840, 0.820, 0.420, 0.540, 0.750, 0.600, 0.980, 0.820, 0.860,
        0.740, 0.890, 0.760, 0.710, 0.700, 0.840, 0.740, 1.001, 0.800, 0.720, 0.750, 0.500,
        0.800, 0.500, 0.804, 0.621, 0.621, 0.650, 0.710, 0.640, 0.710, 0.690, 0.500, 0.670,
        0.700, 0.320, 0.380, 0.630, 0.330, 0.959, 0.700, 0.680, 0.710, 0.710, 0.450, 0.590,
        0.470, 0.700, 0.580, 0.880, 0.600, 0.610, 0.580, 0.600, 0.440, 0.600, 0.804,
    ),
    "ja-bold": (
        0.334, 0.385, 0.537, 0.940, 0.800, 1.141, 0.910, 0.320, 0.505, 0.510, 0.677, 0.840,
        0.347, 0.480, 0.347, 0.594, 0.760, 0.677, 0.760, 0.760, 0.760, 0.760, 0.760, 0.760,
        0.760, 0.760, 0.404, 0.404, 0.840, 0.840, 0.840, 0.800, 1.003, 0.780, 0.770, 0.810,
        0.850, 0.700, 0.670, 0.860, 0.840, 0.509, 0.560, 0.760, 0.624, 1.000, 0.840, 0.880,
        0.760, 0.900, 0.760, 0.730, 0.700, 0.860, 0.760, 1.051, 0.820, 0.740, 0.750, 0.550,
        0.800, 0.550, 0.833, 0.677, 0.677, 0.660, 0.730, 0.640, 0.730, 0.690, 0.510, 0.670,
        0.720, 0.320, 0.400, 0.660, 0.360, 1.026, 0.720, 0.700, 0.730, 0.730, 0.490, 0.590,
        0.490, 0.720, 0.609, 0.900, 0.641, 0.640, 0.580, 0.652, 0.500, 0.652, 0.833,
    ),
    "en-regular": (
        0.278, 0.284, 0.392, 0.591, 0.556, 0.889, 0.800, 0.230, 0.333, 0.333, 0.417, 0.684,
        0.278, 0.400, 0.278, 0.390, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556, 0.556,
        0.556, 0.556, 0.278, 0.278, 0.684, 0.684, 0.684, 0.556, 1.015, 0.667, 0.667, 0.722,
        0.722, 0.667, 0.611, 0.778, 0.722, 0.278, 0.500, 0.667, 0.556, 0.898, 0.748, 0.778,
        0.667, 0.778, 0.722, 0.667, 0.611, 0.722, 0.667, 0.944, 0.667, 0.667, 0.611, 0.302,
        0.379, 0.302, 0.684, 0.556, 0.333, 0.556, 0.588, 0.500, 0.589, 0.556, 0.313, 0.589,
        0.566, 0.242, 0.242, 0.500, 0.242, 0.861, 0.566, 0.586, 0.588, 0.589, 0.348, 0.500,
        0.339, 0.566, 0.500, 0.723, 0.500, 0.500, 0.500, 0.334, 0.260, 0.334, 0.684,
    ),
    "en-bold": (
        0.278, 0.333, 0.493, 0.592, 0.575, 0.889, 0.850, 0.293, 0.369, 0.369, 0.455, 0.707,
        0.278, 0.404, 0.278, 0.443, 0.575, 0.575, 0.575, 0.575, 0.575, 0.575, 0.575, 0.575,
        0.575, 0.575, 0.333, 0.333, 0.707, 0.707, 0.707, 0.611, 0.975, 0.722, 0.722, 0.722,
        0.737, 0.667, 0.611, 0.778, 0.766, 0.317, 0.556, 0.722, 0.611, 0.957, 0.790, 0.778,
        0.667, 0.778, 0.722, 0.667, 0.611, 0.723, 0.667, 1.005, 0.667, 0.667, 0.611, 0.369,
        0.436, 0.369, 0.707, 0.556, 0.333, 0.556, 0.620, 0.556, 0.619, 0.556, 0.383, 0.619,
        0.611, 0.284, 0.284, 0.559, 0.284, 0.916, 0.611, 0.611, 0.620, 0.619, 0.398, 0.556,
        0.389, 0.611, 0.556, 0.797, 0.556, 0.556, 0.500, 0.389, 0.326, 0.389, 0.707,
    ),
    "mono": (
        0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600,
        0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600,
        0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600,
        0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600,
        0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600,
        0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600,
        0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600,
        0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600, 0.600,
    ),
}

#: ASCII 以外（全角・記号）の上限＝1 em。候補の書体では、図に使っている字はどれも
#: 1 em 以下（Windows のテストが図の全字を実測して確かめる）。
_WIDE_EM = 1.0

#: 字の種類ごとの太さと大きさ。**図の `<style>` もここから書き出す**（写しを作らない）。
STYLE: dict[str, tuple[str, float]] = {
    "title": ("bold", 21.0),
    "lname": ("bold", 16.0),
    "lrule": ("regular", 12.5),
    "ctitle": ("bold", 12.5),
    "mod": ("mono", 13.0),
    "role": ("regular", 11.5),
    "note": ("regular", 12.0),
}


def text_width(text: str, cls: str, lang: str) -> float:
    """`lang` の図で、`cls` の字で `text` を描いたときの幅の**上限**（px）。"""
    weight, px = STYLE[cls]
    face = "mono" if weight == "mono" else f"{lang}-{weight}"
    table = _ASCII_EM[face]
    em = 0.0
    for ch in text:
        code = ord(ch)
        if 0x20 <= code <= 0x7E:
            em += table[code - 0x20]
        elif face == "mono":
            # 等幅の候補（Consolas / Courier New）には全角の字形が無く、別の書体へ
            # 落ちる＝幅が読めない。モジュール名は ASCII だけのはず。
            raise ValueError(f"等幅の字に ASCII 以外が混じっている: {text!r}")
        else:
            em += _WIDE_EM
    return em * px


# =============================================================================
# 割り付け
# =============================================================================
MARGIN = 20        # 図の端 → 帯
BAND_PAD = 14      # 帯の端 → カード
GAP = 14           # カードとカードの間
CARD_PAD = 12      # カードの端 → 字
MOD_GAP = 12       # モジュール名の列の後ろの空き
BAND_RULE_GAP = 24  # 帯の名前と右寄せの説明の間
LINE = 20          # 行の送り
MIN_COLUMN = 341   # 格子の列の最小幅（短い図を無理に縮めない）
ARROW = 32         # 帯と帯の間（矢印を置く）


def _ceil(v: float) -> int:
    return int(math.ceil(v - 1e-9))


def _card_height(card: Card) -> int:
    # 題の基線 +17、1 行目の基線 +38、以後 LINE ごと、最後の基線の下に 18。
    return 38 + LINE * (max(len(c) for c in card.columns) - 1) + 18


def _notes_height(notes: Notes) -> int:
    return 26 + LINE * (len(notes.lines) - 1) + 8


@dataclass
class Layout:
    mod_col: int
    columns: list[int]
    width: int
    height: int


def layout(lang: str) -> Layout:
    """`lang` の図の、格子の列の幅・モジュール名の列幅・図の寸法を決める。"""
    li = LANGS.index(lang)

    def widest(texts, cls: str) -> int:
        return _ceil(max(text_width(t, cls, lang) for t in texts))

    all_rows = [r for b in BANDS for row in b.rows for it in row
                if isinstance(it, Card) for col in it.columns for r in col]
    mod_col = widest([r[0] for r in all_rows], "mod") + MOD_GAP

    cols = [MIN_COLUMN] * GRID_COLUMNS
    spans: list[tuple[int, int, int]] = []   # (始まりの列, 跨ぐ数, 要る幅)
    for band in BANDS:
        for row in band.rows:
            assert sum(it.span for it in row) == GRID_COLUMNS, f"列の数が合わない: {row}"
            i = 0
            for it in row:
                if isinstance(it, Card):
                    for k, col in enumerate(it.columns):
                        need = CARD_PAD + mod_col + widest([r[1 + li] for r in col], "role") \
                            + CARD_PAD
                        cols[i + k] = max(cols[i + k], need)
                    spans.append((i, it.span,
                                  CARD_PAD + widest([it.title[li]], "ctitle") + CARD_PAD))
                else:
                    spans.append((i, it.span, CARD_PAD
                                  + widest([line[li] for line in it.lines], "note") + CARD_PAD))
                i += it.span

    def fit(start: int, span: int, need: int) -> None:
        have = sum(cols[start:start + span]) + GAP * (span - 1)
        if need > have:
            cols[start + span - 1] += need - have   # 足りない分は最後の列が持つ

    for start, span, need in spans:
        fit(start, span, need)
    # 帯の中で使える幅＝全列＋列間。帯の名前と右寄せの説明がここに並ぶ。
    for band in BANDS:
        fit(0, GRID_COLUMNS, widest([band.name[li]], "lname") + BAND_RULE_GAP
            + widest([band.rule[li]], "lrule"))
    # 図の端から端まで置く字（題・前書き・脚注）は、帯の余白のぶんだけ広く使える。
    figure_texts = [(TITLE, "title"), (LEAD, "note")] + [(f, "note") for f in FOOTER]
    for texts, cls in figure_texts:
        fit(0, GRID_COLUMNS, widest([texts[li]], cls) - BAND_PAD * 2)

    width = MARGIN * 2 + BAND_PAD * 2 + GAP * (GRID_COLUMNS - 1) + sum(cols)
    height = (FIRST_BAND_TOP + sum(_band_height(b) for b in BANDS) + ARROW * (len(BANDS) - 1)
              + 28 + LINE * (len(FOOTER) - 1) + 14)
    return Layout(mod_col, cols, width, height)


FIRST_BAND_TOP = 76


def _row_height(row: list[Card | Notes]) -> int:
    return max(_card_height(it) if isinstance(it, Card) else _notes_height(it) for it in row)


def _band_height(band: Band) -> int:
    # 名前の基線 +24、1 段目 +48、段の間 GAP、最後の段の下に 14。
    return 48 + sum(_row_height(r) for r in band.rows) + GAP * (len(band.rows) - 1) + 14


# =============================================================================
# 書き出し
# =============================================================================
def _t(x: int, y: int, cls: str, s: str, extra: str = "") -> str:
    return f'<text x="{x}" y="{y}" class="{cls}"{extra}>{escape(s)}</text>'


def render(lang: str) -> str:
    """`lang` の図を SVG の文字列で返す（改行は LF・末尾に改行）。"""
    li = LANGS.index(lang)
    lay = layout(lang)
    w, h = lay.width, lay.height
    band_w = w - MARGIN * 2
    col_x = []
    x = MARGIN + BAND_PAD
    for c in lay.columns:
        col_x.append(x)
        x += c + GAP

    def span_right(start: int, span: int) -> int:
        return col_x[start + span - 1] + lay.columns[start + span - 1]

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}"',
        f'     role="img" aria-labelledby="ttl desc" font-family="{_FONT_FAMILY[lang]}">',
        "  <!-- " + GENERATED_NOTE[li] + " -->",
        f'  <title id="ttl">{escape(ARIA_TITLE[li])}</title>',
        f'  <desc id="desc">{escape(DESC[li])}</desc>',
        "",
        "  <defs>",
        '    <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">',
        '      <path d="M0,0 L10,5 L0,10 z" fill="#8892a4"/>',
        "    </marker>",
        "    <style>",
        "      .band  { stroke-width:1.5 }",
        "      .card  { fill:#ffffff; stroke-width:1 }",
        f"      .lname {{ font-size:{STYLE['lname'][1]:g}px; font-weight:700 }}",
        f"      .lrule {{ font-size:{STYLE['lrule'][1]:g}px; fill:#5b6270 }}",
        f"      .ctitle{{ font-size:{STYLE['ctitle'][1]:g}px; font-weight:700 }}",
        f"      .mod   {{ font-size:{STYLE['mod'][1]:g}px; "
        f"font-family:{_MONO_FAMILY}; fill:#1f2430 }}",
        f"      .role  {{ font-size:{STYLE['role'][1]:g}px; fill:#5b6270 }}",
        f"      .note  {{ font-size:{STYLE['note'][1]:g}px; fill:#5b6270 }}",
        "      .arrow { stroke:#8892a4; stroke-width:2; marker-end:url(#ar) }",
        "    </style>",
        "  </defs>",
        "",
        f'  <rect width="{w}" height="{h}" fill="#ffffff"/>',
        "",
        f'  <text x="{MARGIN}" y="34" font-size="{STYLE["title"][1]:g}" font-weight="700" '
        f'fill="#1f2430">{escape(TITLE[li])}</text>',
        f"  {_t(MARGIN, 57, 'note', LEAD[li])}",
    ]

    top = FIRST_BAND_TOP
    for bi, band in enumerate(BANDS):
        band_h = _band_height(band)
        ink = f' fill="{band.ink}"'
        out += [
            "",
            f"  <!-- {band.name[1].split('/')[0]} -->",
            f'  <rect class="band" x="{MARGIN}" y="{top}" width="{band_w}" height="{band_h}" '
            f'rx="10" fill="{band.fill}" stroke="{band.stroke}"/>',
            "  " + _t(MARGIN + BAND_PAD, top + 24, "lname", band.name[li], ink),
            "  " + _t(MARGIN + band_w - BAND_PAD, top + 24, "lrule", band.rule[li],
                      ' text-anchor="end"'),
        ]
        y = top + 48
        for row in band.rows:
            out.append("  <g>")
            i = 0
            for it in row:
                x0 = col_x[i]
                if isinstance(it, Card):
                    cw = span_right(i, it.span) - x0
                    out.append(
                        f'    <rect class="card" x="{x0}" y="{y}" width="{cw}" '
                        f'height="{_card_height(it)}" rx="6" stroke="{band.stroke}"/>')
                    out.append("    " + _t(x0 + CARD_PAD, y + 17, "ctitle", it.title[li], ink))
                    for k, col in enumerate(it.columns):
                        tx = col_x[i + k] + CARD_PAD
                        for n, r in enumerate(col):
                            ty = y + 38 + LINE * n
                            out.append("    " + _t(tx, ty, "mod", r[0]) + " "
                                       + _t(tx + lay.mod_col, ty, "role", r[1 + li]))
                else:
                    for n, line in enumerate(it.lines):
                        out.append("    " + _t(x0 + CARD_PAD, y + 26 + LINE * n, "note", line[li]))
                i += it.span
            out.append("  </g>")
            y += _row_height(row) + GAP
        bottom = top + band_h
        if bi < len(BANDS) - 1:
            out += ["", f'  <line class="arrow" x1="{w // 2}" y1="{bottom + 4}" '
                        f'x2="{w // 2}" y2="{bottom + ARROW - 4}"/>']
        top = bottom + ARROW

    y = top - ARROW + 28
    out.append("")
    for n, line in enumerate(FOOTER):
        out.append("  " + _t(MARGIN, y + LINE * n, "note", line[li]))
    out.append("</svg>")
    return "\n".join(out) + "\n"


# =============================================================================
# 実測（Windows・PIL）
# =============================================================================
def measure_ascii_em(font_dir: Path = Path("C:/Windows/Fonts")) -> dict[str, tuple[float, ...]]:
    """`_MEASURED_FONTS` を開き、ASCII の 1 字ごとの最大幅（em）を返す（切り上げなし）。"""
    from PIL import ImageFont

    out = {}
    for face, files in _MEASURED_FONTS.items():
        fonts = [ImageFont.truetype(str(font_dir / n), size=1000, index=i) for n, i in files]
        out[face] = tuple(max(f.getlength(chr(c)) for f in fonts) / 1000
                          for c in range(0x20, 0x7F))
    return out


def _format_table(table: dict[str, tuple[float, ...]]) -> str:
    lines = []
    for face, vals in table.items():
        cells = [f"{math.ceil(v * 1000 - 1e-9) / 1000:.3f}" for v in vals]
        lines.append(f'    "{face}": (')
        for k in range(0, len(cells), 12):
            lines.append("        " + ", ".join(cells[k:k + 12]) + ",")
        lines.append("    ),")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="層構成図の SVG を表から生成する（B-209）")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="書き出さず、差があれば 1 で終わる")
    g.add_argument("--measure", action="store_true", help="字幅の上限表を実測し直して表示する")
    args = p.parse_args(argv)

    if args.measure:
        print(_format_table(measure_ascii_em()))
        return 0
    stale = []
    for lang, path in OUTPUTS.items():
        svg = render(lang)
        current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
        if current == svg:
            continue
        stale.append(path)
        if not args.check:
            path.write_text(svg, encoding="utf-8", newline="\n")
    for path in stale:
        print(("古い: " if args.check else "書き出した: ") + str(path.relative_to(ROOT)))
    return 1 if (args.check and stale) else 0


if __name__ == "__main__":
    sys.exit(main())
