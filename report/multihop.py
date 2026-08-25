"""
multihop.py
===========
中継点（ホップ）を挟む回線＝**A-3** の実行エンジン（ヘッドレス・UI 知識ゼロ）。

**5 つ目の実行フロー**（既存 4＝単一 / バッチ / 地図 / 条件探索）。入力モデルが
バッチと別（waypoint 列が source of truth）なので独立させたが、**1 ホップの実行は
バッチの `_process_one` そのまま**＝実行層に新規はほとんど無い。

中継方式＝**再生中継（受けて送り直す）に限定**（2026-07-31 ユーザー決定）
---------------------------------------------------------------------
各ホップは**自分の送信電力から始まる独立したリンクバジェット**で、
**ホップ間で損失は足さない**。したがって:

  - 物理は新規ゼロ（`models.py` に手を入れない）。
  - 全体判定は **min**（最も余裕の少ないホップ）。
  - ⚠️ **受動反射（パッシブリフレクタ／バックツーバック）は対象外**。あれは
    損失が連結するので新規の物理が要る＝別の版で判断する。「反射板でも使えますか」
    と聞かれたら**対象外と即答してよい**。

なぜ waypoint 列と行の二層なのか（⑦・**書き落とすと一周戻る**）
--------------------------------------------------------------
`PathRow` は行ごとに独立した `h_tx`/`h_rx`/座標を持つので、行を直接編集させると
**中継点 R1 の高さが「hop1 の h_rx」と「hop2 の h_tx」に二重入力できる**（同じ
1 本のアンテナに違う値を書ける）。⇒ **編集させるのは waypoint（地点）と HopRF
（区間）だけで、`PathRow` は導出物**にする。高さは地点に 1 つしか無い。

  Waypoint … 地点に属するもの（座標・地上高）
  HopRF    … 区間に属するもの（周波数・送受アンテナ利得）
             ＝中継点では送信と受信で**別のアンテナ**なので、利得はホップ側

⚠️ **中継点は「確定して置く」もので「動かして探る」ものにしない**（④）。
ドラッグで動かすたびに新区間の DEM 取得が走る＝「無差別な広域 DL を促す UI」に
なる。探索が要るならそれは条件探索（A-2）の担当。
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from core import config
from core import i18n
from core import simulation as sim
from core import units
from report import batch

logger = logging.getLogger("radiosim")

# 中継経路の ID に許す長さ。**ホップ ID（`<id>_h1`）が出力ディレクトリ名になる**
# ので、バッチの上限（16）から接尾辞ぶんを引いた値にする。
_MAX_PATH_ID_LEN = 12

# ホップ数の上限。**器の側で上限を持つ**（A4 の合成シートに収まる範囲・
# DEM 取得が青天井にならない）。実務のスクリーニングで 2〜4 ホップを想定。
MAX_HOPS = 8


@dataclass(frozen=True)
class Waypoint:
    """経路上の 1 地点（TX / 中継点 / RX を区別しない）。

    **高さはここにしか無い**＝中継点の地上高は 1 つの値で、前後のホップが
    それぞれ `h_rx` / `h_tx` として参照する（二重入力を型で殺す）。
    """
    name: str
    lat:  float
    lon:  float
    h:    float


@dataclass(frozen=True)
class HopRF:
    """1 区間の無線諸元（None は共通設定を踏襲＝`PathRow` と同じ約束）。

    利得が**区間**に属するのは、中継点では送信と受信で別のアンテナだから。
    """
    freq_mhz: "float | None" = None
    gain_tx:  "float | None" = None
    gain_rx:  "float | None" = None


# ============================================================
# トポロジー＝**点列と接続規則を分ける**（2026-08-01）
# ------------------------------------------------------------
# 中継経路は「鎖」（P1→P2→P3）だが、**同じ点列を別の規則で結べば別の使い方に
# なる**：応用プロダクト（RadioSim for Drone）は「星」（GCS→各点）で、点列・
# 高さの持ち方・1 行 = 1 区間の出力まで構造が同一＝**違うのは接続規則だけ**。
#
# ⇒ 鎖を式に埋め込まず（`waypoints[i], waypoints[i+1]` を各所に書かず）、
#    **接続の導出を `links()` 1 か所に閉じる**。ここを分けておかないと、
#    後から星を足すときに「どこが隣接ペアを前提にしているか」を全部探し直す
#    ことになる（[[project-radiosim-for-drone]] の「今ならほぼゼロ・後だと作り直し」）。
#
# ⚠️ **これはデータ構造の布石であって、機能ではない**。星は UI から作れないし
#    作らない（⑤＝作れないものの表現だけ用意しない）。
# ⚠️ **集約規則（`MultiHopRun.ok` / `overall_margin` の min）は鎖の意味論**。
#    星は「独立した N 本」なので min で潰すと各点の値という肝心の情報が消える。
#    星を実際に使うときは集約も併せて決めること（2.6 では決めない）。
TOPOLOGY_CHAIN = "chain"     # P1 → P2 → P3（中継経路＝2.6 の唯一の使い方）
TOPOLOGY_STAR  = "star"      # P1 → P2, P1 → P3, …（先頭がハブ）

#: **宣言されている語彙**＝この名前が何を意味するかを我々が決めてある値。
TOPOLOGIES = (TOPOLOGY_CHAIN, TOPOLOGY_STAR)

#: **この版が実際に扱える値**＝読む・持つ・実行するの 3 層が共通で見る単一ソース。
#
# 🔑 **`TOPOLOGIES` とは別物**（2026-08-05・I-066）。2.6 ではこの 2 つが**同じ 1 つの
# 事実の別の面**として 3 か所に暗黙に散っており、`project.py`（読む）・
# `views/multihop.py`（持つ）・`multihop.py`（実行する）は**どれも単体では筋が
# 通るのに、繋ぐと「星の地点を鎖として計算し、保存で書き換える」**という壊れ方を
# した（独立レビューで 3 巡かけて処方を 3 回変えた）。
#
# ⇒ **扱える範囲は 1 か所で宣言し、各層はそれを参照するだけにする。**
# 星を実装する版では**ここに 1 行足すだけ**で 3 層が同時に開く（＝そのとき
# 3 層を個別に直す作業が発生しない＝本件の再発を封じる形）。
# ⚠️ 開けてよいのは**集約規則が決まってから**（`MultiHopRun` の min は鎖の意味論）。
SUPPORTED_TOPOLOGIES = (TOPOLOGY_CHAIN,)


@dataclass
class MultiHopPath:
    """中継経路 1 本＝waypoint 列と、その間の区間ごとの諸元。"""
    path_id:   str
    waypoints: list[Waypoint]
    hop_rf:    list[HopRF] = field(default_factory=list)
    note:      str         = ""
    # 点列をどう結ぶか。**既定は鎖**＝既存のファイル・呼び出しは何も変わらない。
    topology:  str         = TOPOLOGY_CHAIN

    @property
    def hop_count(self) -> int:
        return len(links(self))


def require_runnable(path: MultiHopPath, what: str = "run") -> None:
    """**実行できるトポロジーか**を問い、できないなら `NotImplementedError`。

    🔴 規則の単一ソース（2026-08-04・独立レビュー Codex 6 巡目）。それまでは
    `MultiHopRun._require_chain`（集約のとき）にしか無かったので、
    **全区間の DEM を引き終えてから落ちて**いた。⇒ **止めるなら、金と時間を
    使う前に止める**（実行の入口でも同じ規則を通す）。

    ⚠️ `links()` が未知のトポロジーを鎖として扱うのとは**矛盾しない**＝
    `links` は「点列をどう結ぶか」の既定を答えるだけで、**実行してよいか**は
    こちらが決める。読み込み側（`project.from_dict`）は宣言済みの値しか通さない
    ので、未知の値がここへ来るのは API を直接叩いた場合だけ。

    ⚠️ **判定は `SUPPORTED_TOPOLOGIES` を見る**（`TOPOLOGY_CHAIN` と直に比べない）
    ＝扱える範囲の宣言は 1 か所（I-066）。
    """
    if path.topology not in SUPPORTED_TOPOLOGIES:
        raise NotImplementedError(
            f"{what}: トポロジー '{path.topology}' の集約規則は未決定です。"
            "min は鎖（直列）の意味論であって、星（独立した N 本）では"
            "経路上の分布という肝心の情報が消えます。出力の形と併せて"
            "集約規則を設計してから使ってください。"
        )


def links(path: MultiHopPath) -> list[tuple[int, int]]:
    """点列 → 区間（waypoint の添字ペア）＝**接続規則はここだけが知っている**。

    未知のトポロジーは鎖として扱う（新しい版が書いたファイルを古い版で開いた
    ときに落とさない＝`.rsproj` の「未知は既定へ」と同じ流儀）。
    """
    n = len(path.waypoints)
    if path.topology == TOPOLOGY_STAR:
        return [(0, i) for i in range(1, n)]
    return [(i, i + 1) for i in range(n - 1)]


@dataclass
class MultiHopRun:
    """実行結果（ホップ別＋全体）。

    ⚠️ **集約（下の 3 つ）は鎖の意味論**であって、トポロジーが変われば意味を失う。
    そのため鎖以外では**黙って数字を返さず止める**（下記 `_require_chain`）。
    """
    path:     MultiHopPath
    hops:     list[batch.PathResult]
    save_dir: str = ""

    def _require_chain(self, what: str) -> None:
        """鎖以外のトポロジーで集約を求められたら、その場で止める。

        **なぜ例外にするか**＝`topology` という入り口を作った以上、星を設定した
        誰かに対して `overall_margin` は**何事もなかったように min を返す**。
        中身は意味を持たないのに、である（鎖＝直列なので min が回線の余裕そのもの
        だが、星＝独立した N 本では「経路上で最悪の 1 点」という統計値の 1 つに
        すぎず、主役であるはずの分布——どこで切れるか・どれだけ切れるか——が
        消える）。**静かに誤るより、その場で決定を強制するほうが安い。**

        この制約はコメントとメモリにしか無いと守られない（[[project-radiosim-for-drone]]
        の布石がまさにそれで打たれなかった）ので、実行時の門にしてある。
        ⛔ ここを外すときは、集約規則そのものを設計してから外すこと
        （判定の単位／主たる出力／`worst` の読み替え＝出力の形とセットで決まる）。
        """
        require_runnable(self.path, what)      # 規則は require_runnable が単一ソース

    @property
    def ok(self) -> bool:
        """**全ホップが成立して初めて回線が成立する**（鎖は最も弱い輪で切れる）。"""
        self._require_chain("ok")
        # 判定は `batch.PathResult.status` が単一ソース（I-010 ③）＝計算に失敗した
        # 区間だけでなく、**成果物が欠けた区間**もここで OK から外れる。
        return bool(self.hops) and all(h.status == "OK" for h in self.hops)

    @property
    def worst(self) -> "batch.PathResult | None":
        """最も余裕の少ないホップ（＝全体判定を決めている区間）。

        **失敗したホップがあればそれが最悪**（数値が無い＝比較できない）。

        ⚠️ 失敗の判定は `status`＝**成果物が欠けた区間もここに入る**（I-010 ③）。
        `result is None` だけを見ていたころは、`ok` が False を返している同じ実行で
        `worst` が**正常な区間を指す**という食い違いが起きた（成果物が失敗した区間
        の RF 値を、健全な値として比較に混ぜていた）。
        """
        self._require_chain("worst")
        failed = [h for h in self.hops if h.status == "ERROR"]
        if failed:
            return failed[0]
        if not self.hops:
            return None
        return min(self.hops, key=lambda h: h.result.actual_margin)  # type: ignore[union-attr]

    @property
    def overall_margin(self) -> "float | None":
        """全体のマージン＝**ホップ別マージンの最小値**（足し算ではない）。

        再生中継なので各ホップは独立したバジェット。「どこが一番苦しいか」が
        回線全体の余裕そのものになる。

        ⚠️ **1 区間でも `ERROR` なら `None`**（`ok` / `worst` と同じ `status` で
        判定する）。成果物が欠けただけの区間は数値そのものは残っているが、その値を
        全体の余裕として出すと **`ok` が False なのに正の余裕が出る**——`overall_display`
        はそれを「最大不足 −20.0 dB」という読めない字にしていた（負の不足量）。
        **判定できないことを「—」と言うほうが、間違った量を言うより安い。**
        区間ごとの数値は表に残るので、情報は失われない。
        """
        self._require_chain("overall_margin")
        if any(h.status == "ERROR" for h in self.hops):
            return None                  # 失敗したホップがある＝全体は語れない
        margins = [h.result.actual_margin for h in self.hops if h.result is not None]
        if not margins or len(margins) != len(self.hops):
            return None
        return min(margins)


def overall_status(run: MultiHopRun) -> str:
    """全体判定の**語**（`"OK"` / `"NG"` / `"ERROR"`）＝**ここだけが作る**（B-071）。

    🔑 **語彙は区間と同じ**（`batch.PathResult.status`）＝同じ成果物の中で、区間表・
    件数カード・全体判定が**同じ 3 つの語**で話す。`run.ok` の**二値**から語を作ると
    `ERROR` が `NG` に潰れ、「計算できたが回線不成立」と「判定できなかった」が
    読み手に区別できなくなる（区間表と件数は `ERROR` と言っているのに、全体だけが
    `NG` と言う——同じ紙の上で食い違う）。

    ⚠️ **`ok` を置き換えるものではない**＝`ok` は「鎖が成立したか」という真偽で、
    集約規則（min）の意味論そのもの。こちらは**それを人に見せる語**で、`ok` が
    答えられない第 3 の状態（判定不能）を持つ。⇒ 判定に使うのは `ok`、
    画面と成果物に書くのは `overall_status`。

    ⚠️ **区間が 1 つも無い実行も `ERROR`**＝判定の材料が無いことを「NG」と
    言い切らない（`overall_margin` も同じ条件で `None` を返している）。

    ⛔ **`run.ok` から語を作る式を、画面やレポートへ書き戻さないこと。**
    `overall_display`（語と数値）と対で、**状態語もここが単一ソース**。
    """
    require_runnable(run.path, "overall_status")   # 規則の出所は 1 か所
    # 判定の出所は `batch.PathResult.status`（I-010 ③）＝成果物が欠けた区間も含む。
    if not run.hops or any(h.status == "ERROR" for h in run.hops):
        return "ERROR"
    return "OK" if run.ok else "NG"


#: 集約カードの語（I-052）。**判定で切り替える**ので、キーを 2 つ持つ。
OVERALL_MARGIN_KEY    = "mh_overall_margin"      # OK＝設計余裕の KPI
OVERALL_SHORTFALL_KEY = "mh_overall_shortfall"   # NG＝あと何 dB 足りないか


def overall_display(run: MultiHopRun, digits: int = 1) -> tuple[str, str]:
    """集約カードの **(語の i18n キー, 表示する数値)** を返す（I-052）。

    **同じ数字が答えている問いが、判定で変わる**——OK なら「あと何 dB 積めるか」
    （連続量として意味を持つ）だが、NG なら要るのは「あと何 dB 足りないか」で、
    `−12.4` を「余裕」と書いた数字は読み違えの元になる。⇒ **値の出所
    （`overall_margin`）は変えず、語と符号だけを切り替える**。

    ⚠️ ここを画面とレポートで別々に書くと、必ず片方だけ直る日が来る
    （I-010 で「判定の出所が各所に散っていた」のと同じ壊れ方）。**単一ソース。**
    ⚠️ 判定できない（`overall_margin is None`）ときは NG 側の語を使わない
    ——不足量が分からないのに「最大不足 —」と書くのは、無い情報を語ってしまう。
    """
    margin = run.overall_margin
    if margin is None:
        return OVERALL_MARGIN_KEY, "—"
    if run.ok:
        return OVERALL_MARGIN_KEY, f"{margin:+.{digits}f}"
    # 「不足 −12.4」は二重否定で読めない＝符号を反転して不足量そのものを出す。
    return OVERALL_SHORTFALL_KEY, f"{-margin:.{digits}f}"


def hop_endpoints(path: MultiHopPath, index: int) -> "tuple[Waypoint, Waypoint] | None":
    """区間 `index` の両端の地点（範囲外なら None）。

    **表示側はここを通す**＝「区間 i の端点は `wp[i]` と `wp[i+1]`」という式を
    レポート・CSV・窓へ書き写すと、接続規則を 1 か所に閉じた意味が無くなる
    （実際 5a の時点で同じ式が 4 か所へ散っていた）。
    """
    pairs = links(path)
    if not 0 <= index < len(pairs):
        return None
    a, b = pairs[index]
    return path.waypoints[a], path.waypoints[b]


def hop_label(path: MultiHopPath, index: int, fallback: str = "") -> str:
    """区間 `index` の見出し（`A → B`）。範囲外なら `fallback`。"""
    ends = hop_endpoints(path, index)
    return f"{ends[0].name} → {ends[1].name}" if ends else fallback


def hop_id(path_id: str, index: int) -> str:
    """ホップの ID（**出力ディレクトリ名になる**ので衝突しない形にする）。"""
    return f"{path_id}_h{index + 1}"


def hop_rows(path: MultiHopPath) -> list[batch.PathRow]:
    """waypoint 列 → `PathRow` の列（**導出**＝ここでしか作らない）。

    どの地点とどの地点を結ぶかは `links()` が決める（鎖なら `wp[i] → wp[i+1]`）。
    高さは**地点から**、無線諸元は**区間から**取る。戻り値をユーザーに編集させない
    こと（編集させると二重入力が復活する）。
    """
    rows: list[batch.PathRow] = []
    for i, (a, b) in enumerate(links(path)):
        tx, rx = path.waypoints[a], path.waypoints[b]
        rf = path.hop_rf[i] if i < len(path.hop_rf) else HopRF()
        rows.append(batch.PathRow(
            path_id  = hop_id(path.path_id, i),
            lat_tx   = tx.lat, lon_tx = tx.lon,
            lat_rx   = rx.lat, lon_rx = rx.lon,
            h_tx     = tx.h,   h_rx   = rx.h,
            freq_mhz = rf.freq_mhz,
            gain_tx  = rf.gain_tx,
            gain_rx  = rf.gain_rx,
            note     = f"{tx.name} → {rx.name}",
        ))
    return rows


def validate_path(path: MultiHopPath) -> list[str]:
    """実行前の検証（**DEM 取得の前に**全部返す＝1 つ直すたびに待たせない）。

    ⚠️ 座標・値域の検証は `batch.validate_rows` に委ねる＝**範囲の出所を 2 つに
    しない**（B-018 と同じ理由。ランチャー／バッチ／条件探索はすべて
    `config.VALIDATION_RULES` が出所）。ここで見るのは**中継経路に固有の形**だけ。
    """
    errors: list[str] = []
    if len(path.waypoints) < 2:
        errors.append(i18n.t("mh_err_too_few"))
        return errors
    if path.hop_count > MAX_HOPS:
        errors.append(i18n.t("mh_err_too_many").format(max=MAX_HOPS))
    if len(path.hop_rf) != path.hop_count:
        errors.append(i18n.t("mh_err_rf_count").format(
            hops=path.hop_count, rf=len(path.hop_rf)))
    if not batch._PATH_ID_RE.fullmatch(path.path_id):
        errors.append(i18n.t("verr_invalid_id").format(pid=repr(path.path_id)))
    elif len(path.path_id) > _MAX_PATH_ID_LEN:
        errors.append(i18n.t("mh_err_id_too_long").format(
            pid=path.path_id, max=_MAX_PATH_ID_LEN, n=len(path.path_id)))
    for wp in path.waypoints:
        if math.isnan(wp.h):
            errors.append(i18n.t("mh_err_bad_height").format(name=wp.name))
            continue
        vmin, vmax, _ = config.VALIDATION_RULES["h_tx"]
        if not (vmin <= wp.h <= vmax):
            errors.append(i18n.t("mh_err_height_range").format(
                name=wp.name, val=wp.h))
    # 隣接する 2 点が同じ位置＝ホップとして成立しない（バッチ側の
    # verr_identical と同じ判定を、導出した行に対してかける）。
    # ⚠️ ID 長は**出力ディレクトリ名の上限**で見る（B-057）＝ここへ渡すのは
    # `route1_h8` のように**生成した** ID で、人が複数経路の表に打つものではない。
    # 既定（`MAX_TYPED_ID_LEN` ＝表で読める長さ）を使うと、中継の経路 ID が
    # 8 文字まで縮む。経路 ID そのものの上限は上の `_MAX_PATH_ID_LEN` で見ている。
    errors.extend(
        batch.validate_rows(hop_rows(path), max_id_len=batch._MAX_PATH_ID_LEN)
        if errors == [] else [])
    return errors


def run_multihop(
    path:             MultiHopPath,
    base_params:      sim.SimParams,
    on_hop_start:     Callable[[int, int, str], None],
    on_hop_progress:  Callable[[int, int], None],
    on_hop_complete:  Callable[[int, int, batch.PathResult], None],
    on_complete:      Callable[[MultiHopRun], None],
    on_error:         Callable[[Exception], None],
    coord_format:     str = "dd",
    on_hop_stage:     "Callable[[str], None] | None" = None,
    project_name:     str = "",
    memo:             str = "",
) -> None:
    """中継経路の実行をバックグラウンドスレッドで開始する。

    コールバックの形は `batch.run_batch` に揃えてある＝呼び出し側（View）が
    進捗の受け取り方を作り直さずに済む（`ProgressPump` の使い方も同じ）。

    ⚠️ **1 ホップ = 1 区間の DEM 取得**なので、取得量はホップ数に比例する（④）。
    追い風＝隣接ホップは端点を共有し、タイルが重なるのでキャッシュが効きやすい。
    """
    threading.Thread(
        target = _run_thread,
        args   = (path, base_params, on_hop_start, on_hop_progress,
                  on_hop_complete, on_complete, on_error, coord_format,
                  on_hop_stage, project_name, memo),
        daemon = True,
    ).start()


def _run_thread(
    path, base_params, on_hop_start, on_hop_progress, on_hop_complete,
    on_complete, on_error, coord_format, on_hop_stage, project_name, memo,
) -> None:
    try:
        # ⛔ **実行できるかを最初に問う**（2026-08-04・独立レビュー Codex 7 巡目）。
        # ここに置く理由が 2 つある:
        #   ① **DEM を引く前**＝引いてから落ちると時間も外部サーバーへの負荷も
        #      無駄になる。出力ディレクトリも作らない。
        #   ② **ワーカースレッドの中**＝`run_multihop` は「バックグラウンドで
        #      開始する」API なので、ここだけ**呼び出し元スレッドで `on_error` を
        #      呼ぶ**とコールバックの順序とスレッド契約が経路ごとに変わる
        #      （再入の危険）。失敗の渡り方は他の失敗と同じ 1 本にする。
        require_runnable(path, "run_multihop")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir   = config.new_run_dir("multihop", timestamp)

        rows  = hop_rows(path)
        total = len(rows)
        t0    = time.perf_counter()
        logger.info("Multihop started: %s (%d hops) → %s",
                    path.path_id, total, run_dir)

        results: list[batch.PathResult] = []
        for i, row in enumerate(rows):
            on_hop_start(i + 1, total, row.path_id)
            # **ホップ 1 本＝バッチの 1 行**（実行層はここで完全に流用する）。
            pr = batch._process_one(row, base_params, run_dir, on_hop_progress,
                                    coord_format, on_hop_stage, project_name)
            results.append(pr)
            on_hop_complete(i + 1, total, pr)

        run = MultiHopRun(path=path, hops=results, save_dir=run_dir)
        _write_hops_csv(run, run_dir)
        # 合成シート（全体判定＋内訳）と連結レポート。**min だけを出さない**＝
        # 「どこが一番苦しいか」が分からないと次の一手（中継点を足す・空中線を
        # 上げる）が決められない（②）。地図はバッチと同じ俯瞰図を流用する
        # ＝端点が一致する N 本なので、そのまま折れ線に見える。
        from report import report_multihop
        from report import report_summary
        map_b64 = report_summary.render_summary_map_b64(results)
        report_multihop.save_route_html(run, project_name, memo, map_b64)
        report_multihop.save_report_all_html(run, project_name, memo, map_b64)
        logger.info("Multihop complete: %s in %.2fs (overall %s)",
                    path.path_id, time.perf_counter() - t0,
                    overall_status(run))
        on_complete(run)

    except Exception as ex:
        logger.exception("Multihop error: %s", ex)
        on_error(ex)


def _write_hops_csv(run: MultiHopRun, run_dir: str) -> None:
    """ホップ別の台帳（`hops.csv`）。

    **バッチの `summary.csv` とは別ファイルにする**（2026-08-01 ユーザー決定）＝
    あちらは「1 行 = 1 回線」の出力契約で既に使われており、`group_id` 列を足すと
    互換破壊の告知が要る。中継は「1 行 = 1 ホップ・N 行で 1 回線」という**別の
    形**なので、器を分けたほうが両方の意味が濁らない。
    """
    import csv

    from core import output_contract
    from report import report_common

    path_csv = os.path.join(run_dir, "hops.csv")
    with open(path_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        # 見出しは出力契約が単一ソース（→ core/output_contract.py）。
        w.writerow(list(output_contract.HOPS_CSV_COLUMNS))
        for i, pr in enumerate(run.hops):
            ends = hop_endpoints(run.path, i)
            wp_from = ends[0].name if ends else ""
            wp_to   = ends[1].name if ends else ""
            r, p = pr.result, pr.params
            w.writerow([
                report_common.csv_cell(run.path.path_id), i + 1,
                report_common.csv_cell(pr.row.path_id),
                report_common.csv_cell(wp_from), report_common.csv_cell(wp_to),
                # 判定は `pr.status` が単一ソース（I-010 ③）＝`r.status` を直に
                # 読むと**成果物の失敗がこの列だけ素通りする**（画面と HTML は
                # ERROR、CSV だけ OK）。台帳の 3 つの口で字が食い違わないよう、
                # ここも同じ 1 か所から引く。
                pr.status,
                f"{p.freq_mhz:.1f}" if p else "",
                f"{p.gain_tx:.1f}"  if p else "",
                f"{p.gain_rx:.1f}"  if p else "",
                f"{pr.row.h_tx:.1f}", f"{pr.row.h_rx:.1f}",
                f"{r.p_rx:.2f}" if r else "",
                f"{r.actual_margin:.2f}" if r else "",
                # ⚠️ **整形は `units` が単一ソース**（B-060）。手書きすると、
                # ここのように「% を % で割り増す」誤りが静かに入る＝
                # `blocked_ratio` は models の時点で既に **%**（`* 100` 済み）で、
                # さらに 100 倍していた（画面 33.8% に対し CSV は 3381.3）。
                # `csv_blocked_ratio` は 100% で頭打ちにする側の約束も持っている。
                units.csv_distance(r.slant_dist_km) if r else "",
                units.csv_blocked_ratio(r.blocked_ratio) if r else "",
                # 理由も 2 種類ある（計算の失敗 `error` ／ 成果物の失敗
                # `artifact_error`）＝`error` だけを見ると、ERROR と書いた行の
                # 理由欄が空になる。バッチの `summary.csv` と同じ埋め方にする。
                report_common.csv_cell(str(pr.error or pr.artifact_error or "")),
                # 末尾＝出力契約の規約 1（追加は末尾のみ）。頭打ちしない側の量
                # （F1 半径の何倍まで食い込んでいるか）＝I-077。
                units.csv_f1_depth(r.blocked_ratio) if r else "",
                # 何点で刻んだ答えか（I-069）＝**区間ごとに違う**（短い区間は
                # 点が少なく、長い区間は多い＝同じ段階でもそうなるのが正しい）。
                str(p.num) if p else "",
            ])
