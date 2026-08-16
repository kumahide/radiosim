"""
views/scenario.py
=================
条件探索ウィンドウ（2.5 / A-1 比較・A-2 スイープ）。

**4 つ目の実行フロー**の View。ヘッドレスの実行は [scenario.py](scenario.py)、
出力は [report_scenario.py](report_scenario.py) が担い、ここは入力欄・進捗・
結果の一覧表示だけを持つ（純コアのヘッドレス性を保つ継ぎ目）。

置き場の決定（2026-07-25・ユーザー選択）＝**独立した窓 1 つ**にタブで比較/スイープ
を同居させる。グラフ窓（既に 1000 行超）へ足すと肥り、バッチ窓へ足すと「N 本の
独立回線」と「1 本を掘る」というデータモデルが衝突する（設計哲学⑦）。

**レイアウトの原則（2026-07-25 の実機フィードバックで確定）**:
  - 入力（タブ）は**内容の高さに収める**＝タブを expand させると空白だけが伸びる。
  - 結果は **Treeview（固定高＋スクロール）**＝点数が増えても窓を縦に伸ばさない
    （41 点でも FHD に収まる）。以前は Label に全点を流し込み、点数次第で
    窓外へ溢れて保存ボタンまで見切れた。
  - 実行・レポートのボタンは**常に見える帯**（タブの外・下端固定）に置く。
  - 項目名は**単位つき**（出所は report_scenario の単位表＝画面とレポートで表記が
    ずれない）、入力欄の幅は**文字数でなく grid の列**に決めさせる、結果表の余白は
    [views/theme.table_style](views/theme.py) から取る（2026-07-26 実機 FB）。

進捗は [views/progress.ProgressPump](views/progress.py) で受け、相の切り替え
（取得 → 計算 → レポート生成）はランナー側の宣言に従うだけ＝**重い相が管轄外に
置かれない**（B-006／I-008 の構造対策）。

⚠️ 素の tk ウィジェットは sv_ttk のテーマに追従しないので、**新規はすべて ttk**
で作る（[[feedback_radiosim_rules]]）。
"""

from __future__ import annotations

import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Callable

from core import config
from core import coords
from core import i18n
from core import scenario as scn
from core import simulation as sim
from core import units
from report import project
from report import report_scenario
from views import dialogs, theme, window_fit
from views.progress import ProgressPump

# 比較タブで編集できる項目（順に並ぶ）。値は文字列で持ち、実行時に変換する
# （バッチのランチャー凍結と同じ流儀＝入力途中の値でパースを走らせない）。
_COMPARE_FIELDS: tuple[tuple[str, str], ...] = (
    ("freq_mhz", "num"), ("p_tx", "num"), ("gain_tx", "num"), ("gain_rx", "num"),
    ("sens", "num"), ("h_tx", "num"), ("h_rx", "num"), ("veg_h", "num"),
    ("rain_rate", "num"), ("env_type", "env"), ("diff_method", "diff"),
)

# 選択肢は**内部キーで持ち、表示は i18n ラベル**（ランチャーと同じ流儀）。
# 以前はキー（los / deygout …）をそのまま見せており、ja でも英語のままだった。
_ENV_KEYS  = ("los", "rural", "suburban", "urban")
_DIFF_KEYS = ("deygout", "single")


def _axis_label(key: str) -> str:
    """項目名の表示（**単位つき**）。

    単位の出所は report_scenario.AXIS_UNITS ＝ レポート（HTML/PNG/CSV）と同じ
    一覧を使う（2026-07-26 の実機フィードバック：画面だけ単位が無く、dBm と dBi と
    m が同じ見た目の数字で並んでいて何を入れる欄か分からない）。
    **画面とレポートで単位表記がずれない**ことが、出所を1つにする理由。
    """
    return report_scenario.axis_label(key)


def _number(text: str, label: str) -> float:
    """入力欄の文字列を数値へ。読めない値は**その欄の名前つき**で弾く。

    素の `float()` に任せると Python 生の英語（`could not convert string to
    float: 'abc'`）がダイアログに出る＝言語設定にも [[feedback_japanese_everywhere]]
    にも従わない（B-016）。`nan` / `inf` は `float()` を通ってしまうので、
    値域と一緒に config.validate_value 側で弾く。
    """
    try:
        return float(text)
    except ValueError:
        raise ValueError(i18n.t("scn_err_value").format(
            label=label, reason=i18n.t("err_numeric"), value=repr(text))) from None


def _env_labels() -> dict[str, str]:
    return {k: i18n.t(f"env_{k}") for k in _ENV_KEYS}


def _diff_labels() -> dict[str, str]:
    return {k: i18n.t(f"diff_opt_{k}") for k in _DIFF_KEYS}

# 結果一覧の見える行数（超えた分はスクロール）。窓の高さを点数から切り離す。
_RESULT_ROWS = 8


class ScenarioWindow(tk.Toplevel):
    """条件探索ウィンドウ（ランチャーが唯一のインスタンスを持つ）。"""

    # 幅の**下限**（実寸は中身から決まる＝`_fit_to_content`）。900 だったころは
    # 凍結帯が 1050px を要求していたので下限が効いておらず、比較条件が 1 列でも
    # 窓は 1070px で右側が丸ごと空いていた（2026-08-01 実機フィードバック）。
    # 帯を詰めた今は**中身に追従する**ので、下限は最小サイズ寄りに戻す。
    # ⚠️ その後 B-046（座標欄 21→27 文字）で帯が 870 → 1085px に太り、**再び帯が
    # 窓幅を決めた**（＝同じ苦情が 2026-08-08 に再来＝B-052）。処方も 1 回目と同じ
    # ＝**帯を細くする**（↻ と 🔒 の説明を案件情報の行へ移した）。⇒ 実測 ja/dd で
    # 1105px → 859px（条件 1 列・スイープ）/ 947px（条件 5 列）。
    _BASE_W = 820

    def __init__(
        self,
        parent: tk.Tk,
        base_params: sim.SimParams,
        config_provider: "Callable[[], dict] | None" = None,
        meta_provider:   "Callable[[], dict] | None" = None,
        on_close:        "Callable[[], None] | None" = None,
        initial_spec:    "project.ScenarioSpec | None" = None,
        coord_format:    str = "dd",
    ) -> None:
        super().__init__(parent)
        self.title(i18n.t("scn_window_title"))
        self.minsize(780, 520)

        self._base_params     = base_params
        self._config_provider = config_provider
        self._meta_provider   = meta_provider
        # 座標の表記＝**開いた時点で凍結**（G2 と同じ形・I-070）。この帯は長らく
        # 十進度で決め打ちしており、設定を DMS にしてもここだけ従わなかった。
        self._coord_format    = coord_format
        # 案件情報（案件名・自由メモ）も**ランチャーのスナップショット**として持つ。
        # ここで凍結し、↻ で明示的に取り込み直す（実行時に読み直さない＝下記）。
        self._meta: dict[str, str] = self._snapshot_meta()
        self._on_close_cb     = on_close
        self._running = False
        self._last_run: "scn.ScenarioRun | None" = None
        self._last_dir = ""

        self._pump = ProgressPump(self, self._dispatch_event)

        self._build()
        if initial_spec is not None:
            self._apply_spec(initial_spec)
        self._fit_to_content()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------------
    # プロジェクト（`.rsproj`）との受け渡し
    # ----------------------------------------------------------
    def project_spec(self) -> "project.ScenarioSpec":
        """画面の条件セットを**文字列のまま**取り出す（保存用）。

        ⚠️ ここで数値へ変換しない＝この窓は「値は文字列で持ち、実行時に変換する」
        流儀（`_COMPARE_FIELDS`）で、保存の瞬間にパースを走らせると**入力途中の
        値を保存できなくなる**。値域の検証は実行時に 1 か所（`scn.Condition`）で行う。

        env/diff は**画面ラベルでなく内部キー**で持つ（ja で保存したファイルを en で
        開いても壊れないため＝`_to_value` と対の `_display` で戻す）。
        """
        compare = [
            {key: self._key_of(key, cvars[key].get()) for key, _kind in _COMPARE_FIELDS}
            for cvars in self._cmp_cols
        ]
        sweep = {
            "axis":   self._sweep_axis.get(),
            "from":   self._from_var.get(),
            "to":     self._to_var.get(),
            "points": self._points_var.get(),
        }
        return project.ScenarioSpec(mode=self._mode.get(), compare=compare,
                                    sweep=sweep)

    def apply_project_spec(self, spec: "project.ScenarioSpec") -> None:
        """プロジェクトの条件セットをこの窓へ取り込む（I-061 の帯から呼ばれる）。"""
        self._apply_spec(spec)

    def _apply_spec(self, spec: "project.ScenarioSpec") -> None:
        """プロジェクトの条件セットを画面へ流し込む（`project_spec` と対）。

        ベース列には触らない＝**ベースは常にランチャーの現在値**（プロジェクトの
        params もランチャーへ流し込まれてからこの窓が開くので、両者は一致する）。
        """
        if spec.mode in ("compare", "sweep"):
            self._mode.set(spec.mode)
            self._on_mode_changed()

        # 比較条件の列数をファイルに合わせる（最低 1 列は常に残す）。
        while len(self._cmp_cols) > max(len(spec.compare), 1):
            self._remove_condition_column()
        while len(self._cmp_cols) < len(spec.compare):
            before = len(self._cmp_cols)
            self._add_condition_column()
            if len(self._cmp_cols) == before:
                break                      # 上限に達した（保存元の版が違う等）
        for cvars, saved in zip(self._cmp_cols, spec.compare):
            for key, _kind in _COMPARE_FIELDS:
                if key in saved:
                    cvars[key].set(self._display(key, saved[key]))

        axis = spec.sweep.get("axis", "")
        if axis in self._sweep_rows:
            self._sweep_axis.set(axis)
        for name, var in (("from", self._from_var), ("to", self._to_var),
                          ("points", self._points_var)):
            if spec.sweep.get(name):
                var.set(spec.sweep[name])
        self._on_sweep_axis_changed()

    @staticmethod
    def _key_of(key: str, text: str) -> str:
        """画面表示 → 保存する文字列（env/diff だけ i18n ラベルを内部キーへ戻す）。

        `_to_value` を使わない理由＝あちらは数値欄を `float` にして**読めない値で
        例外を投げる**（実行時の検証）。保存は入力途中でも通す必要がある。
        """
        if key == "env_type":
            return {v: k for k, v in _env_labels().items()}.get(text, text)
        if key == "diff_method":
            return {v: k for k, v in _diff_labels().items()}.get(text, text)
        return text

    def _build_frozen_header(self, parent: tk.Misc) -> None:
        """ランチャーから凍結した前提の帯（I-031）。

        **バッチ窓と同じ見せ方に揃える**＝どちらも「ランチャーから凍結した
        スナップショットを読み取り専用で見せる帯」という*同じ性質のもの*なのに、
        バッチは枠付きの構造化された表示、こちらは枠なしの 1 行テキストで、
        見た目から同じ性質だと読み取れなかった。凍結方式はアプリ全体の原則
        （⑦：入力の権限を 1 か所に置く）なので、**原則が守られているのに見た目が
        原則を裏切っている**状態を消す。揃えた点は 3 つ＝①ラベル付きグループ枠
        ②`readonly` の Entry ＋ 🔒 ③「ランチャーのスナップショット」の明示。

        ⚠️ **実行はここに出ている値で行う**（黙って読み直さない）。以前は実行時に
        `config_provider` を読み直しており、ランチャーで座標を変えると「画面の経路と
        計算した経路が違う」状態になり得た。更新は ↻ ボタンで明示的に行う。
        """
        # 案件情報＝バッチの `_build_case_info` と同じ並び（ラベル→readonly→🔒）。
        case = ttk.LabelFrame(parent, text=i18n.t("batch_case_info"), padding=(8, 2))
        case.pack(fill="x", pady=(0, 4))
        self._project_var = tk.StringVar()
        self._memo_var    = tk.StringVar()
        for label_key, var, expand in (
            ("batch_project_name", self._project_var, False),
            ("batch_memo",         self._memo_var,    True),
        ):
            f = ttk.Frame(case)
            f.pack(side="left", padx=6, fill="x", expand=expand)
            ttk.Label(f, text=i18n.t(label_key)).pack(side="left")
            # width は下限（メモ側は expand で余りを取る）＝バッチと同じ流儀。
            ttk.Entry(f, textvariable=var, state="readonly", width=20).pack(
                side="left", padx=(2, 0), fill="x", expand=expand)
            ttk.Label(f, text="🔒").pack(side="left", padx=(2, 0))

        # 🔒 の意味と ↻ は**凍結した領域の 1 行目（案件情報の行）の右側**に置く。
        # **なぜ座標の行から移したか**＝以前は座標と同じ行にあり、`↻ ランチャーから
        # 更新`（140px）と `🔒 ランチャーの値`（98px）が、DMS でも切れない座標欄
        # 2 つ（B-046＝各 315px）と同じ行を分け合っていた。その結果**この帯だけで
        # 1085px を要求し、窓幅を決めていた**（条件 5 列のグリッドは 900px）＝
        # 2026-08-01 に一度直した「窓が広すぎる」が B-046 の後に再来した正体
        # （B-052）。移すと帯は 835px になり、**窓幅は比較条件の列数で決まる**。
        # ⚠️ **独立した行にはしない**＝行を 1 つ増やすとこの窓は FHD 100% の 990px
        # を超える（実測 1033px＝100% の実機でスクロールが要る窓になる）。
        # ⚠️ この 2 つは**帯 1 つではなく凍結した領域全体に効く**（`↻` は案件情報・
        # 経路・ベース列をまとめて取り込み直す）ので、1 行目に置くほうが実態に近い
        # ＝バッチも `↻` は共通設定と案件情報の両方を取り込む（`_refresh_common…`）。
        # ⚠️ `width=` は付けない＝文字数で幅を固定すると、中身より広い帯を要求する。
        ttk.Button(case, text=i18n.t("btn_refresh_common"),
                   command=self._refresh_from_launcher).pack(side="right", padx=(6, 0))
        ttk.Label(case, text=i18n.t("hint_common_readonly"),
                  foreground=theme.muted_foreground(case)).pack(side="right", padx=6)

        # この窓が振れない前提（座標と samples）。**振れる前提は比較タブの
        # ベース列に出る**ので、ここには出さない（二重に見せない）。
        # 枠名は「経路」＝「固定した」は凍結方式の言い方で、**同じことを右端の
        # 🔒 ヒントが既に言っている**（同じ意味を 2 通りで言わない・I-048）。
        fixed = ttk.LabelFrame(parent, text=i18n.t("scn_fixed_group"), padding=(8, 2))
        fixed.pack(fill="x", pady=(0, 6))
        row = ttk.Frame(fixed)
        row.pack(fill="x")
        self._tx_var      = tk.StringVar()
        self._rx_var      = tk.StringVar()
        self._samples_var = tk.StringVar()
        # **送信座標 / 受信座標の 2 欄に割る**（I-048）＝バッチの列と同じ語・同じ
        # 並びになり、「→」という第 3 の表記が消える。
        # ⚠️ `→` そのものを追放したいのではない＝中継の区間名 `A → B` は *2 点の
        # 関係*を表す記号として情報を持つ。ここは *2 つの入力値* なので欄で表す。
        # 座標欄の幅は `coords` が単一ソース（B-046）＝DMS でも末尾の `E` が
        # 切れない下限。⚠️ **表記によって幅を変えない**（切り替えるたびに列が
        # ずれる＝⑧に反する）。
        # ⚠️ 座標が読めない凍結帯は帯の意味を失う（何を固定したのか分からない）。
        # 実測＝スライス D の帯の作り直しで必要幅は 870 → 1009px（高さ 986px は
        # 不変＝実機の高さ予算 990px に対する余裕 4px を減らしていない）。
        for label_key, var, expand, width in (
            ("scn_tx_coord", self._tx_var,      True,  coords.DISPLAY_WIDTH_CHARS),
            ("scn_rx_coord", self._rx_var,      True,  coords.DISPLAY_WIDTH_CHARS),
            ("scn_samples",  self._samples_var, False, 6),
        ):
            f = ttk.Frame(row)
            f.pack(side="left", padx=6, fill="x", expand=expand)
            ttk.Label(f, text=i18n.t(label_key)).pack(side="left")
            ttk.Entry(f, textvariable=var, state="readonly", width=width).pack(
                side="left", padx=(2, 0), fill="x", expand=expand)
            ttk.Label(f, text="🔒").pack(side="left", padx=(2, 0))

        self._update_path_label()
        self._update_meta_label()

    def _snapshot_meta(self) -> dict[str, str]:
        """ランチャーの案件情報を取り込む（**この窓が持つのは常にこの写し**）。"""
        meta = self._meta_provider() if self._meta_provider else {}
        return {
            "project_name": str(meta.get("project_name", "")),
            "memo":         str(meta.get("memo", "")),
        }

    def _update_meta_label(self) -> None:
        """案件情報の表示を、いま持っているスナップショットに合わせる。

        **なぜ表示するか**（2026-07-26・ユーザー指摘）：案件名とメモはレポートの
        自己同定ヘッダに刻印されるのに、この窓には出ていなかった。しかも実行の
        瞬間にランチャーを読み直していたので、**画面に無い値が成果物に載る**
        （窓を開いたあとにランチャー側で案件名を変えると、気づく手段が無い）。
        経路と同じく「凍結して見せる」に揃える。
        """
        self._project_var.set(self._meta["project_name"])
        self._memo_var.set(self._meta["memo"])

    def _update_path_label(self) -> None:
        p = self._base_params
        self._tx_var.set(coords.format_pair(p.lat_tx, p.lon_tx, self._coord_format))
        self._rx_var.set(coords.format_pair(p.lat_rx, p.lon_rx, self._coord_format))
        self._samples_var.set(str(p.num))

    def _refresh_from_launcher(self) -> None:
        """ランチャーの現在値（**座標を含む**）を取り込み直す。

        取り込むのは経路表示とベース列、そして比較条件の初期値。条件列で編集済みの
        値まで巻き戻すと編集内容が消えるので、**ベースと同じ値だった欄だけ**を
        追従させる（触った欄はユーザーの意図として残す）。
        """
        # 案件情報は座標と独立に取り込む（`config_provider` の有無に引きずらない）。
        self._meta = self._snapshot_meta()
        self._update_meta_label()
        if self._config_provider is None:
            return
        try:
            new_base = sim.SimParams(self._config_provider())
        except Exception as ex:
            dialogs.alert(self, i18n.t("dlg_input_error"), str(ex))
            return
        old_base = self._base_params
        self._base_params = new_base
        self._update_path_label()
        for key, _kind in _COMPARE_FIELDS:
            new_val = getattr(new_base, key)
            self._base_vars[key].set(self._display(key, new_val))
            for cvars in self._cmp_cols:
                if cvars[key].get() == self._display(key, getattr(old_base, key)):
                    cvars[key].set(self._display(key, new_val))
        # `ベース ±` はベース値が動けば範囲も動く（取り込み直した値で引き直す）。
        self._sync_range_from_delta()

    @staticmethod
    def _display(key: str, value) -> str:
        """内部値 → 画面表示（環境・回折モデルは i18n ラベル）。"""
        if key == "env_type":
            return _env_labels().get(str(value), str(value))
        if key == "diff_method":
            return _diff_labels().get(str(value), str(value))
        return str(value)

    @staticmethod
    def _to_value(key: str, text: str) -> "float | str":
        """画面表示 → 内部値（i18n ラベルをキーへ戻す）。"""
        if key == "env_type":
            return {v: k for k, v in _env_labels().items()}.get(text, text)
        if key == "diff_method":
            return {v: k for k, v in _diff_labels().items()}.get(text, text)
        return _number(text, _axis_label(key))

    def _fit_to_content(self) -> None:
        """**中身に合わせて開き、足りなくなったら広げる**（縮めない）。

        高さ：定数で決めると結果一覧が潰れる（比較モード）かスイープで余白が出る。
        幅　：`_BASE_W` は**下限**でしかない。比較条件は最大 5 列まで増やせるので、
        幅を固定すると **「条件を追加」で生やした右端の列が窓外へ出て見えなくなる**
        （I-024・2026-07-26 の実機フィードバックで実際に条件 5 が見切れた）。列を
        増減するたびに測り直す。

        測り方そのものは [views/window_fit](views/window_fit.py) に集約してある
        （見切れは窓ごとに直しては再発してきたクラスなので、実装を 1 つにする）。
        """
        window_fit.fit_to_content(self, min_w=self._BASE_W)

    # ----------------------------------------------------------
    # 組み立て
    # ----------------------------------------------------------
    def _build(self) -> None:
        # 中身はスクロールの受け皿の中へ（B-023＝B-021② と同一パス）。この窓は
        # 100% では入るが 125% で 1095px、150% で 1223px を要求し、実機（FHD）の
        # 使える高さ 990px を超える。外周 padding は受け皿の内側へ移す（外に残すと
        # スクロール領域の外で下端を隠す）。
        outer = window_fit.scrollable_body(self, padding=10)

        self._build_frozen_header(outer)

        # モード切替＝**表示中のフレームだけを pack する**。ttk.Notebook は
        # 「一番背の高いタブ」に合わせて全タブの高さが決まるため、行数の多い比較タブに
        # 引きずられてスイープ側に死んだ余白ができる（実機フィードバック）。
        # フレームを差し替えれば、空いた高さは下の結果一覧が使う。
        self._mode = tk.StringVar(value="compare")
        switch = ttk.Frame(outer)
        switch.pack(fill="x")
        for value, key in (("compare", "scn_tab_compare"), ("sweep", "scn_tab_sweep")):
            # sv_ttk の "Toggle.TButton" ＝押されている側が塗られるトグル外観。
            # 素の Radiobutton だと「ただの文字」に見えて切り替えと気づけない。
            ttk.Radiobutton(switch, text=i18n.t(key), value=value,
                            variable=self._mode, style="Toggle.TButton",
                            command=self._on_mode_changed).pack(
                                side="left", padx=(0, 6))

        self._panels = ttk.Frame(outer)
        self._panels.pack(fill="x", pady=(4, 0))
        self._compare_panel = self._build_compare_tab()
        self._sweep_panel   = self._build_sweep_tab()
        self._on_mode_changed()

        # 実行 & 進捗 & レポート（常に見える帯）
        # ⚠️ 帯が 2 段になったぶん、外側の余白を (10, 6) から詰める＝この窓は FHD
        # 100% の 990px に対して余りが 10px しか無く、詰めないと**入らなくなる**
        # （B-021 系＝見切れは寸法を足した回に出る）。
        prog_frame = ttk.Frame(outer)
        prog_frame.pack(fill="x", pady=(4, 2))

        # 上段: ステータス 1 行（**バーの上**＝4 窓共通の位置・I-047）。以前はこの
        # 窓だけバーの*横*にあり、フェーズ名が伸びるたびに実行ボタンが左へ動いた。
        row1 = ttk.Frame(prog_frame)
        row1.pack(fill="x")
        self._prog_label = ttk.Label(row1, text="", anchor="w")
        self._prog_label.pack(side="left", fill="x", expand=True)

        # 下段: 実行帯の型（I-029）＝**バーが左で伸び、実行は帯の右端**。以前はこの窓
        # だけボタンがバーの左にあり、ランチャー・中継経路と鏡像になっていた。
        bar = ttk.Frame(prog_frame)
        bar.pack(fill="x", pady=(2, 0))
        self._run_btn = ttk.Button(bar, text=i18n.t("btn_run"), command=self._on_run,
                                   style="Accent.TButton")
        self._run_btn.pack(side="right", padx=(10, 0))
        self._prog_bar = ttk.Progressbar(bar, mode="determinate", maximum=100)
        self._prog_bar.pack(side="left", fill="x", expand=True)

        # 結果一覧（残りの高さを使う＝点数が増えてもスクロールで収まる）
        self._result_box = ttk.LabelFrame(outer, text=i18n.t("scn_result"), padding=8)
        self._result_box.pack(fill="both", expand=True)
        self._summary_label = ttk.Label(self._result_box, text="")
        self._summary_label.pack(anchor="w", pady=(0, 6))

        # 余白つきの表スタイル（既定は行が詰まり、文字が枠・列境界に張り付く）。
        cols = ("label", "rx", "margin", "status")
        self._tree = ttk.Treeview(self._result_box, columns=cols, show="headings",
                                  height=_RESULT_ROWS,
                                  style=theme.table_style(self))
        _unit = report_scenario.with_unit
        headings = (i18n.t("scn_col_label"), _unit(i18n.t("html_rx_level"), "dBm"),
                    _unit(i18n.t("html_act_margin"), "dB"), i18n.t("html_status"))
        widths = (220, 130, 130, 80)
        for col, head, w in zip(cols, headings, widths):
            self._tree.heading(col, text=head)
            self._tree.column(col, width=w,
                              anchor="w" if col == "label" else "e", stretch=True)
        vsb = ttk.Scrollbar(self._result_box, orient="vertical",
                            command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        # 判定色は theme が出所（窓ごとに書かない）。**表を作った時点で当てる**＝
        # 結果を入れる関数の中だけで当てると、テーマ切替や他窓との突合せで
        # 「まだ色が無い」状態ができる。
        theme.apply_verdict_tags(self._tree)
        self.bind("<<ThemeChanged>>",
                  lambda _e: theme.apply_verdict_tags(self._tree), add="+")

    def _build_compare_tab(self) -> ttk.Frame:
        """ベース列（読み取り専用）＋比較条件 N 列（2026-07-25 要望で N 可変）。

        ベースはランチャーの現在値そのもの＝**基準を編集させない**（編集できると
        「何と比べたのか」が曖昧になる）。比較条件はベース値で初期化し、変えたい
        欄だけ触る使い方を想定する。
        """
        frame = ttk.Frame(self._panels, padding=(10, 4))
        self._cmp_grid = ttk.Frame(frame)
        self._cmp_grid.pack(anchor="w")

        # 列を足す/減らすボタン（上限は scenario.MAX_COMPARE_CONDITIONS）。
        btns = ttk.Frame(frame)
        btns.pack(anchor="w", pady=(8, 0))
        self._add_btn = ttk.Button(btns, text=i18n.t("scn_add_cond"),
                                  command=self._add_condition_column, width=14)
        self._add_btn.pack(side="left")
        self._del_btn = ttk.Button(btns, text=i18n.t("scn_del_cond"),
                                  command=self._remove_condition_column, width=14)
        self._del_btn.pack(side="left", padx=(6, 0))

        self._cmp_cols: list[dict[str, tk.StringVar]] = []
        self._build_compare_grid()
        self._add_condition_column()      # 既定は比較条件 1 列
        return frame

    def _build_compare_grid(self) -> None:
        """項目名の列とベース列（読み取り専用・↻ で更新される）を作る。"""
        ttk.Label(self._cmp_grid, text="").grid(row=0, column=0)
        ttk.Label(self._cmp_grid, text=i18n.t("scn_base"), anchor="center").grid(
            row=0, column=1, padx=6, sticky="ew")
        self._base_vars: dict[str, tk.StringVar] = {}
        for row, (key, _kind) in enumerate(_COMPARE_FIELDS, start=1):
            ttk.Label(self._cmp_grid, text=_axis_label(key)).grid(
                row=row, column=0, sticky="w", pady=1)
            var = tk.StringVar(value=self._display(key, getattr(self._base_params, key)))
            self._base_vars[key] = var
            ttk.Label(self._cmp_grid, textvariable=var,
                      anchor="e", width=11).grid(row=row, column=1, padx=6, pady=1,
                                                 sticky="ew")

    def _add_condition_column(self) -> None:
        if len(self._cmp_cols) >= scn.MAX_COMPARE_CONDITIONS:
            return
        col = len(self._cmp_cols) + 2         # 0=項目名 / 1=ベース
        ttk.Label(self._cmp_grid, text=i18n.t("scn_cond_n").format(n=col - 1),
                  anchor="center").grid(row=0, column=col, padx=6, sticky="ew")
        cvars: dict[str, tk.StringVar] = {}
        for row, (key, kind) in enumerate(_COMPARE_FIELDS, start=1):
            var = tk.StringVar(
                value=self._display(key, getattr(self._base_params, key)))
            cvars[key] = var
            if kind == "num":
                w = ttk.Entry(self._cmp_grid, textvariable=var, width=11)
            else:
                labels = (_env_labels() if kind == "env" else _diff_labels())
                w = ttk.Combobox(self._cmp_grid, textvariable=var,
                                 values=list(labels.values()),
                                 state="readonly", width=9)
            # sticky="ew" ＝ **列の幅に合わせて広げる**。Entry(width=11) と
            # Combobox(width=9＋矢印ボタン) は同じ width 指定でも実幅が揃わないので、
            # 幅は grid の列（＝その列で一番広いウィジェット）に決めさせる
            # （2026-07-26 の実機フィードバック：欄の右端がガタついて見える）。
            w.grid(row=row, column=col, padx=6, pady=1, sticky="ew")
        self._cmp_cols.append(cvars)
        self._sync_cond_buttons()
        self._fit_to_content()

    def _remove_condition_column(self) -> None:
        if len(self._cmp_cols) <= 1:          # 比較対象ゼロにはしない
            return
        col = len(self._cmp_cols) + 1
        for w in self._cmp_grid.grid_slaves(column=col):
            w.destroy()
        self._cmp_cols.pop()
        self._sync_cond_buttons()
        self._fit_to_content()

    def _sync_cond_buttons(self) -> None:
        n = len(self._cmp_cols)
        self._add_btn.config(
            state="disabled" if n >= scn.MAX_COMPARE_CONDITIONS else "normal")
        self._del_btn.config(state="disabled" if n <= 1 else "normal")

    def _build_sweep_tab(self) -> ttk.Frame:
        """比較タブと同じ器（項目名＋ベース値）に、**振る軸のラジオ**を足す（I-032）。

        従来は軸 Combobox と開始/終了/点数だけの独立ブロックで、**周波数や
        アンテナ高が今いくつなのかが画面に一切無かった**＝「振らない側の条件」を
        確かめるために比較タブへ往復させられた。比較タブが既にベース列でこの情報を
        出しているので、**新しい表現を増やさず同じ器に載せる**（⑧の判断テスト
        「この表現は他のどこかに既にあるか？」の答えが Yes ならそれに合わせる）。

        ベース値の StringVar は**比較タブと共有する**＝↻ で取り込み直したときに
        片方だけ古い値が残る、という形の破れ方をしなくなる（Tk の textvariable は
        1 つの変数を複数のウィジェットが参照できる）。
        """
        frame = ttk.Frame(self._panels, padding=(10, 4))
        grid = ttk.Frame(frame)
        grid.pack(anchor="w", fill="x")
        self._sweep_grid = grid

        ttk.Label(grid, text=i18n.t("scn_base"), anchor="center").grid(
            row=0, column=1, padx=6, sticky="ew")

        # 振る軸（ラジオ）。既定は従来と同じ h_tx。
        self._sweep_axis = tk.StringVar(value="h_tx")
        self._sweep_rows: dict[str, int] = {}
        for row, (key, _kind) in enumerate(_COMPARE_FIELDS, start=1):
            ttk.Label(grid, text=_axis_label(key)).grid(
                row=row, column=0, sticky="w", pady=1)
            ttk.Label(grid, textvariable=self._base_vars[key],
                      anchor="e", width=11).grid(row=row, column=1, padx=6, pady=1,
                                                 sticky="ew")
            if key in scn.SWEEP_AXES:
                # **振れない項目にはラジオを置かない**＝env/diff は離散の選択肢で
                # 比較（A-1）の担当、k_factor は損失にも判定にも効かない
                # （振っても結果が動かない軸を並べると「効くはず」と誤読させる）。
                ttk.Radiobutton(grid, variable=self._sweep_axis, value=key,
                                command=self._on_sweep_axis_changed).grid(
                                    row=row, column=2, padx=(0, 6))
                self._sweep_rows[key] = row

        # 範囲の入れ方＝**開始/終了** か **ベース ±**（2026-08-01 ユーザー要望）。
        # 実際に振りたいのは「今の値の前後」なので、その言い方をそのまま入れられる
        # ようにする。⚠️ **器は 2 つにしない**＝どちらの入れ方でも内部は常に
        # 開始/終了へ正規化し、実行も保存（`.rsproj`）もそこだけを見る。同じ範囲の
        # 表現が 2 つ残ると、食い違ったときにどちらが本当かを決められなくなる。
        self._from_var   = tk.StringVar(value="10")
        self._to_var     = tk.StringVar(value="60")
        self._points_var = tk.StringVar(value="11")
        self._delta_var  = tk.StringVar(value="10")
        self._range_mode = tk.StringVar(value="range")

        switch = ttk.Frame(frame)
        switch.pack(anchor="w", pady=(6, 0))
        ttk.Label(switch, text=i18n.t("scn_range_how")).pack(side="left", padx=(0, 6))
        for value, key in (("range", "scn_range_from_to"),
                           ("delta", "scn_range_delta")):
            ttk.Radiobutton(switch, text=i18n.t(key), value=value,
                            variable=self._range_mode, style="Toggle.TButton",
                            command=self._on_range_mode_changed).pack(
                                side="left", padx=(0, 6))

        # 欄は**選ばれた軸の行にだけ現れる**。1 組だけ作って選択に合わせて置き直す
        # （行ごとに欄を作ると、入力途中の値が行を変えた瞬間に消えたり、どの行の値が
        # 実行に使われるのか曖昧になる）。
        self._range_cells: dict[str, tuple[ttk.Label, ttk.Entry]] = {}
        for name, label_key, var in (
            ("from",   "scn_from",   self._from_var),
            ("to",     "scn_to",     self._to_var),
            ("delta",  "scn_delta",  self._delta_var),
            ("points", "scn_points", self._points_var),
        ):
            lab = ttk.Label(grid, text=i18n.t(label_key))
            ent = ttk.Entry(grid, textvariable=var, width=10)
            self._range_cells[name] = (lab, ent)
        # ± を変えたら開始/終了を作り直す（正規化はここ 1 か所）。
        # ⚠️ **返り値を捨てない**（B-050）。`trace_add` が登録する Tcl コマンドは
        #    ラムダ →（self を捕まえて）窓 → この変数、と **C レベルを経由して
        #    循環する**ので、Python の GC が切れない＝**窓を閉じても永久に解放
        #    されず、開閉のたびに 65 個ずつ積み上がる**（10 回で +650 個）。
        #    `_on_close` で外すために id を持っておく。
        # ⚠️ **処方は 2 つある**（2026-08-12・B-059 で 2 つ目が要った）＝ここは
        #    「id を控えて閉じ際に外す」形だが、**変数が動的に捨てられる窓では
        #    足りない**（捨てる箇所を数え上げれば必ず漏れる）。中継経路は
        #    `views/multihop.py` の `_watch_input` のように**弱参照で循環を
        #    作らない**形を採っている。窓の作りで選ぶこと。
        self._delta_trace = self._delta_var.trace_add(
            "write", lambda *_: self._sync_range_from_delta())

        ttk.Label(frame, text=i18n.t("scn_err_points").format(
            max=scn.MAX_SWEEP_POINTS),
            foreground=theme.muted_foreground(frame)).pack(anchor="w", pady=(6, 0))
        self._on_sweep_axis_changed()
        return frame

    # 入れ方ごとに見せる欄（**点数は共通**）。
    _RANGE_FIELDS = {"range": ("from", "to", "points"),
                     "delta": ("delta", "points")}

    def _on_sweep_axis_changed(self) -> None:
        """範囲の欄を、選ばれている軸の行へ移す。"""
        row = self._sweep_rows[self._sweep_axis.get()]
        for lab, ent in self._range_cells.values():
            lab.grid_remove()
            ent.grid_remove()
        for i, name in enumerate(self._RANGE_FIELDS[self._range_mode.get()]):
            lab, ent = self._range_cells[name]
            lab.grid(row=row, column=3 + i * 2, sticky="e", padx=(6, 2), pady=1)
            ent.grid(row=row, column=4 + i * 2, sticky="w", pady=1)
        if self._range_mode.get() == "delta":
            self._sync_range_from_delta()      # 軸が変われば基準値も変わる
        self._fit_to_content()

    def _on_range_mode_changed(self) -> None:
        """入れ方を切り替える。**振り幅は引き継ぐ**（打ち直させない）。

        ⚠️ 引き継げるのは幅だけ＝`ベース ±` は定義上ベース値を中心に置くので、
        開始/終了が中心からずれていた場合は中心が動く（これは仕様であって
        バグではない）。
        """
        if self._range_mode.get() == "delta":
            # 今の開始/終了の幅の半分を ± とする（振り幅を引き継ぐ）。
            try:
                half = abs(float(self._to_var.get()) - float(self._from_var.get())) / 2
                self._delta_var.set(f"{half:g}")
            except ValueError:
                pass                            # 入力途中なら既定値のまま
        self._on_sweep_axis_changed()

    def _sync_range_from_delta(self) -> None:
        """`ベース ±Δ` を開始/終了へ正規化する（**内部の表現は 1 つ**）。

        基準はその軸のベース値＝ランチャーの現在値（↻ で取り込み直す値）。
        読めない値のときは何もしない＝実行時に名前つきのエラーで弾かれる。
        """
        if self._range_mode.get() != "delta":
            return
        try:
            base  = float(self._base_vars[self._sweep_axis.get()].get())
            delta = abs(float(self._delta_var.get()))
        except (ValueError, KeyError):
            return
        self._from_var.set(f"{base - delta:g}")
        self._to_var.set(f"{base + delta:g}")

    # ----------------------------------------------------------
    # 実行
    # ----------------------------------------------------------
    def _on_mode_changed(self) -> None:
        """選ばれたモードのパネルだけを表示する（余白を作らない）。"""
        for panel in (self._compare_panel, self._sweep_panel):
            panel.pack_forget()
        panel = (self._compare_panel if self._mode.get() == "compare"
                 else self._sweep_panel)
        panel.pack(fill="x")

    def _conditions(self) -> tuple[list[scn.Condition], str, list[float]]:
        """モードに応じた条件列を作る（入力エラーは ValueError で投げる）。"""
        if self._mode.get() == "compare":
            return self._compare_conditions(), "", []
        return self._sweep_conditions()

    def _compare_conditions(self) -> list[scn.Condition]:
        """ベース（上書きなし）＋比較条件 N 個。基準は常に先頭。"""
        conds = [scn.Condition(label=i18n.t("scn_base"), overrides={})]
        for i, cvars in enumerate(self._cmp_cols, start=1):
            overrides: dict[str, float | str] = {}
            for key, _kind in _COMPARE_FIELDS:
                overrides[key] = self._to_value(key, cvars[key].get().strip())
            conds.append(scn.Condition(
                label=i18n.t("scn_cond_n").format(n=i), overrides=overrides))
        return conds

    def _sweep_conditions(self) -> tuple[list[scn.Condition], str, list[float]]:
        axis = self._sweep_axis.get()
        start = _number(self._from_var.get(), i18n.t("scn_from"))
        stop  = _number(self._to_var.get(),   i18n.t("scn_to"))
        if start == stop:
            raise ValueError(i18n.t("scn_err_range"))
        points = int(_number(self._points_var.get(), i18n.t("scn_points")))
        values = scn.linspace_values(start, stop, points)
        # 軸の値域（周波数 0 や負の高さ）は Condition が弾く＝**DEM 取得の前**に
        # 落ちる。ここで別途チェックを書くと範囲の出所が二重になる（B-016）。
        return scn.sweep_conditions(axis, values), axis, values

    def _on_run(self) -> None:
        if self._running:
            return
        try:
            conditions, axis, values = self._conditions()
        except ValueError as ex:
            dialogs.alert(self, i18n.t("dlg_input_error"), str(ex))
            return

        base = self._base_params
        kind = "sweep" if axis else "compare"
        # ⚠️ **実行はここに出ている値で行う**（経路と同じ扱い）。以前は実行の瞬間に
        # meta_provider を読み直しており、窓を開いたあとにランチャーで案件名を
        # 変えると「画面に無い案件名がレポートに刻印される」状態だった。
        project = self._meta["project_name"]
        memo    = self._meta["memo"]

        save_dir = config.new_run_dir(
            "scenario", datetime.now().strftime("%Y%m%d_%H%M%S"))
        self._last_dir = save_dir

        self._running = True
        self._run_btn.config(state="disabled")
        self._prog_bar.config(value=0)
        self._prog_label.config(text=i18n.t("scn_phase_fetch"))
        self._tree.delete(*self._tree.get_children())
        self._pump.start()
        push = self._pump.push

        # 成果物生成はランナーの RENDER 相＝ワーカースレッドで走る
        # （GUI を固めず、その時間も進捗率に乗る）。
        def _artifacts(run: scn.ScenarioRun) -> None:
            report_scenario.save_scenario_package(run, save_dir, project, memo)

        scn.run_scenario(
            base, conditions,
            on_complete = lambda run, p=push: p(("complete", (run,))),
            on_error    = lambda ex,  p=push: p(("error", (ex,))),
            kind        = kind,
            axis        = axis,
            axis_values = values,
            on_phase    = lambda name, p=push: p(("phase", (name,))),
            on_progress = lambda pct,  p=push: p(("progress", (pct,))),
            artifacts   = _artifacts,
        )

    # ----------------------------------------------------------
    # コールバック（メインスレッド）
    # ----------------------------------------------------------
    def _dispatch_event(self, item: tuple) -> None:
        event, args = item
        if event == "progress":
            self._prog_bar.config(value=args[0])
        elif event == "phase":
            self._prog_label.config(text=i18n.t(f"scn_phase_{args[0]}"))
        elif event == "complete":
            self._on_complete(*args)
        elif event == "error":
            self._on_error(*args)

    def _on_complete(self, run: scn.ScenarioRun) -> None:
        self._running = False
        self._pump.stop()
        self._run_btn.config(state="normal")
        # 完了時はバーを 0 に戻す（単一・バッチと挙動を揃える）。結果はダイアログと
        # 下の一覧が伝えるので、バーに完了状態を残さない。
        self._prog_bar.config(value=0)
        self._prog_label.config(text="")
        self._last_run = run
        self._fill_results(run)

        # 単一・バッチと同じ流儀＝保存先を告げ、レポートかフォルダかを選ばせる
        # （実機フィードバック：ここだけダイアログが出ないのは挙動が揃っていない）。
        # 「保存先を開く」はランチャーの恒久ボタンを外した代わりの受け皿（I-030）。
        choice = dialogs.choose(
            self, i18n.t("dlg_saved_title"),
            i18n.t("scn_saved_msg").format(dir=self._last_dir),
            [("report", i18n.t("dlg_open_report")),
             ("folder", i18n.t("dlg_open_folder"))],
        )
        if choice == "report":
            self._open_report()
        elif choice == "folder":
            os.startfile(self._last_dir)

    def _fill_results(self, run: scn.ScenarioRun) -> None:
        """結果一覧を埋める（点数が多くてもスクロールで収まる）。"""
        self._result_box.config(
            text=i18n.t("scn_sweep_title") if run.kind == "sweep"
            else i18n.t("scn_compare_title"))
        self._summary_label.config(
            text=f'{i18n.t("html_horiz_dist")}: '
                 f'{units.format_distance(run.terrain.horiz_dist_km)}')

        self._tree.delete(*self._tree.get_children())
        for p in run.points:
            self._tree.insert("", "end", values=(
                p.label, f"{p.result.p_rx:.2f}", f"{p.result.actual_margin:+.2f}",
                p.result.status,
            ), tags=("ok" if p.ok else "ng",))

    def _on_error(self, ex: Exception) -> None:
        self._running = False
        self._pump.stop()
        self._run_btn.config(state="normal")
        self._prog_bar.config(value=0)
        self._prog_label.config(text="")
        dialogs.alert(self, i18n.t("dlg_error"), str(ex))

    def _open_report(self) -> None:
        if not self._last_dir:
            dialogs.alert(self, i18n.t("dlg_input_error"), i18n.t("scn_err_no_result"))
            return
        os.startfile(os.path.join(self._last_dir, "scenario.html"))

    # ----------------------------------------------------------
    def close_window(self) -> None:
        """他所（ランチャーのプロジェクト読込）から閉じるための公開口
        （3 つの窓で名前を揃える＝内部ハンドラ名で分岐させない）。"""
        self._on_close()

    def _on_close(self) -> None:
        """閉じるときはポンプを止めてから破棄する。

        実行中に閉じると破棄済みウィジェットへ `after` し続ける経路が生まれる
        （2.4b3 で単一/バッチともに塞いだのと同じクラス）。
        """
        self._pump.stop()
        # Tcl 側に残る参照を切る（B-050）。これを外さないと窓が解放されない。
        trace_id = getattr(self, "_delta_trace", None)
        if trace_id is not None:
            try:
                self._delta_var.trace_remove("write", trace_id)
            except Exception:
                pass
            self._delta_trace = None
        if self._on_close_cb:
            self._on_close_cb()
        self.destroy()
