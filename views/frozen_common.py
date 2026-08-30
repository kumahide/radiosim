"""
views/frozen_common.py
======================
凍結帯「共通設定」に**何を出すか**の唯一の定義（I-101）。

なぜ窓ごとに書かせないか
------------------------
この帯は「**ランチャーから凍結した前提**」で、🔒 と ↻ は帯 1 つでなく**凍結領域
全体**に効く。定義が「実行に効く凍結入力の一覧」である以上、**実行に効く項目が
出ないのは定義違反**になる。にもかかわらず項目の集合は窓ごとに手で並べられており、
中継経路は新設（`211a44b`・2026-08-01）から **6 項目のまま 2 週間気づかれなかった**：

  - **周波数・送信利得・受信利得** … 区間表の列で上書きでき「空欄は共通設定を
    引き継ぎます」と書いてあるのに、**継承元が画面に無かった**。複数経路は
    まったく同じ構造（行で上書き・空欄は共通値）なのに帯へ出しており、
    **同じ意味関係を 2 通りに見せていた**（⑧）。
  - **K ファクター・回折モデル** … 中継経路に上書き口が無く `base_params` のまま
    実行に効く。**画面のどこにも出ない純粋な欠落**で、しかも回折モデルは per-path
    レポートには必ず出る＝**画面と成果物が食い違っていた**。

🔑 **当時この帯に対して行われたのは幅の修正だけで、「6 欄」自体が所与として扱われて
いた**＝型（見せ方）は I-031 から踏襲したのに、項目の集合は一度も照合されなかった。
横断ゲート（`tests/test_ui_consistency.py`）が 3 窓に強制していたのも **↻/🔒 が
どの枠に属するか**だけ＝**縛ったのは器で、中身は縛っていない**。

⇒ ここが**一覧の単一ソース**。窓は「どう並べるか」（何行に折るか・欄の幅）だけを
決め、**何を出すかは決めない**。⚠️ それでも `frozen_common_keys()` の突き合わせ
ゲートは残す＝ここを参照せずに手で並べる窓が現れた日に落ちる（一覧を共有しても、
*使わない*自由までは消せない）。

⚠️ **並べ方まで揃えない**（⑧は「同じ意味関係を同じ見せ方に」であって「同じ寸法に」
ではない）＝複数経路は横に広い窓なので 1 行に 5 欄、中継経路は区間表が窓幅を決める
ので各グループ 2 行に折る（B-053＝帯が窓幅を決めた過去がある）。
"""

from __future__ import annotations

import tkinter as tk
import unicodedata
from tkinter import ttk

from core import i18n, terrain_grid

# 帯に出す項目＝`(i18n の見出しキー, SimParams の属性名)`。**順序も含めて正典**。
RADIO_FIELDS = (
    ("lbl_b_freq",     "freq_mhz"),
    ("lbl_p_tx",       "p_tx"),
    ("lbl_b_gain_tx",  "gain_tx"),
    ("lbl_b_gain_rx",  "gain_rx"),
    ("lbl_b_sens",     "sens"),
)
ENV_FIELDS = (
    ("lbl_b_veg_h",    "veg_h"),
    ("lbl_b_k_factor", "k_factor"),
    ("lbl_b_resolution", "resolution"),
    ("lbl_b_rain",     "rain_rate"),
)
# 選択肢を持つ 2 つ。**複数経路では Combobox・中継経路では読み取り専用の欄**と
# 見せ方が違うので、上の 2 つとは別に持つ（*出すかどうか*は同じく必須）。
SELECT_FIELDS = (
    ("lbl_env_type",    "env_type"),
    ("lbl_b_diff_model", "diff_method"),
)

# 帯に出るべき属性の全体（窓間の突き合わせの基準）。
ALL_KEYS = frozenset(attr for _k, attr in RADIO_FIELDS + ENV_FIELDS + SELECT_FIELDS)

# その実行に当てはまらない項目の印（B-140）。**空欄にしない**＝欄が空だと
# 「読み込みに失敗した」と区別がつかない。語ではなく記号にするのは訳を増やさないため。
NOT_APPLICABLE = "—"


def display_value(attr: str, value: object) -> str:
    """帯に出す文字列。**選択式の 2 つはキーでなく表示ラベル**を出す。

    ⚠️ ランチャー（source of truth）が `Deygout` / `見通し` と表示しているのに、
    凍結した帯だけ `deygout` / `los` と内部キーで出ると、**同じ値が窓によって
    別の言葉に見える**（⑧）。表示ラベルの出所は i18n の 1 か所。
    """
    # 🔴 **ここで既定を補ってはいけない**（B-140＝B-137 の直しが作った欠陥）＝
    #    この関数は**帯を入力として読み直す窓**（複数経路）と**基底をそのまま実行へ
    #    渡す窓**（中継経路＝`run_multihop(path, self._base_params)`）の両方が使う。
    #    段階を持たない基底に既定を差し込むと、**中継経路は帯に「中」と出しながら
    #    固定 N で走る**（帯の申告と実行がずれる）。⇒ **名乗れないものは名乗らない**。
    #    既定が要るのは*帯を入力として使う側*なので、補うのはその窓の仕事。
    if attr == "resolution" and not value:
        return NOT_APPLICABLE
    prefix = {"env_type": "env_", "diff_method": "diff_opt_",
              "resolution": "res_"}.get(attr)
    return i18n.t(f"{prefix}{value}") if prefix else str(value)


def resolution_key(label: str) -> str:
    """表示ラベル（`高（約 5 m）`）→ 内部キー（`high`）。**逆写しの唯一の口**。

    ⚠️ 帯は**表示ラベル**を持つ（上の `display_value`）ので、実行のために内部キーへ
    戻す必要がある。`env_type` は窓ごとに `_env_label_to_key` を持っていたが、
    段階は 3 窓（複数経路・中継経路・条件探索）に出るので**ここに 1 つだけ**置く。

    🔑 **読めなかったら既定へ黙って化かさない**＝原文をそのまま返し、値域の検査
    （`config.validate_value`）に落とさせる。既定へ寄せると、**利用者が選んでいない
    段階で実行が通り、しかもそれが画面のどこにも出ない**（この窓の共通設定は
    「検証されないまま計算へ届く」欠陥の再発点＝B-018 のクラス）。
    """
    for key in terrain_grid.RESOLUTION_KEYS:
        if i18n.t(f"res_{key}") == label:
            return key
    return label


def _display_width(text: str) -> int:
    """Tk の `width`（"0" の幅が 1）でいくつ要る字か。**全角は 2 つぶん**。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1
               for ch in text)


def resolution_field_width() -> int:
    """段階の欄に要る幅（文字数）＝**いちばん長いラベルから引く**（B-149）。

    🔴 **数で書くと、ラベルを変えた日に切れる**＝実際に `高（約 5 m）` →
    `高 5m メッシュ`（B-148）で複数経路の 11 も中継経路の 6 も足りなくなり、
    **閉じ括弧が消えた状態で配布物のスクリーンショットまで進んだ**。
    ⚠️ **言語で変わる**ので定数にはできない（英語は `High 5 m mesh`）＝
    窓を建てるたびに引く。⚠️ **全角は 2 つぶん**で数える（`len()` だと日本語で
    半分の幅になる＝これが 6 という数字の出どころでもある）。
    """
    return max(_display_width(i18n.t(f"res_{key}"))
               for key in terrain_grid.RESOLUTION_KEYS) + 1   # 余白 1


def sampling_field_width() -> int:
    """点数と刻みの欄が**要求する**幅（文字数）＝短いほうの字から引く（B-150）。

    🔴 **ここだけは「いちばん長い字」に合わせられない**（`resolution_field_width`
    との違い）＝条件探索の帯は**窓幅を決めてはいけない**（B-052）ので、幅の予算が
    「条件 5 列のグリッド」で頭打ちになっている。実測＝いちばん長い字
    （`90000 点 / 画素 20.1 m`＝23 桁）を要求すると帯が 976px になり、グリッドの
    911px を超えて**帯が窓幅を決めてしまう**。
    ⇒ **要求するのは短いほうの字（等間隔の形）だけ**にし、残りは `expand` で
    受ける（窓は帯より広いので、実際の描画幅はこれより広い）。
    ⚠️ **数のべた書きに戻さないこと**＝字を変えた日にどれだけ足りないかが分からなくなる。
    ⚠️ **最悪の値で測る**＝点数は天井（5 桁）、刻みは 2 桁（「低」の 20.1m）。
    ⚠️ **全角は 2 つぶん**（→ `_display_width`）。
    """
    from core import simulation as sim   # 遅延＝帯を建てる時だけで足りる

    plain = sim.SAMPLING_READOUT_KEYS[True][0]
    return _display_width(
        i18n.t(plain).format(n=terrain_grid.SAMPLES_CEILING, spacing="20.1"))


def readonly_field(parent: tk.Misc, label_key: str, var: tk.StringVar,
                   width: int) -> None:
    """読み取り専用の 1 欄（見出し＋欄＋🔒）を `parent` の左から積む。

    素の `tk.Entry` ＋背景色のべた書きは sv_ttk のテーマに追従せず、ダークで
    薄グレー背景×白文字になって読めない（B-001）。`ttk.Entry` の `readonly` なら
    ライト／ダークどちらもテーマ配色になる。🔒 はテーマ配色に依存しないグリフ
    なので、双方で「編集不可（ランチャーからの凍結値）」の視覚キューになる（I-004）。
    """
    f = ttk.Frame(parent)
    f.pack(side="left", padx=6)
    ttk.Label(f, text=i18n.t(label_key)).pack(side="left")
    ttk.Entry(f, textvariable=var, state="readonly", width=width).pack(
        side="left", padx=(2, 0))
    ttk.Label(f, text="🔒").pack(side="left", padx=(2, 0))


def readonly_band(parent: tk.Misc, *, widths: "dict[str, int]",
                  fold: int) -> "dict[str, tk.StringVar]":
    """RF 系／環境系のサブグループに**全項目を読み取り専用で**並べ、変数を返す。

    見出しキー（`grp_radio_settings` / `grp_environment`）は複数経路が I-003 で
    導入したものをそのまま使う＝**レイアウトの移植だけで語彙は増やさない**（⑤）。

    Args:
        widths: 属性名 → 欄の文字数。**窓ごとの事情**（既定は 6）。
        fold: 1 行に並べる欄の数。⚠️ **窓が決める**＝中継経路は区間表が窓幅を
            決めるべき窓なので小さく折る（B-053 で帯を 859 → 620px 台へ下げた成果を
            食わないため。窓新設時には 6 欄を 1 行に並べて 125%/150% で 2088px を
            要求し、横断ゲートが止めている）。
    """
    vars_: "dict[str, tk.StringVar]" = {}
    for title, fields in (("grp_radio_settings", RADIO_FIELDS),
                          ("grp_environment", ENV_FIELDS + SELECT_FIELDS)):
        group = ttk.LabelFrame(parent, text=i18n.t(title), padding=(6, 2))
        group.pack(fill="x", pady=(0 if title == "grp_radio_settings" else 4, 0))
        rows: "list[ttk.Frame]" = []
        for n, (label_key, attr) in enumerate(fields):
            if n % fold == 0:
                rows.append(ttk.Frame(group))
                rows[-1].pack(fill="x")
            vars_[attr] = tk.StringVar()
            readonly_field(rows[-1], label_key, vars_[attr], widths.get(attr, 6))
    return vars_
