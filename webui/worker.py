"""
webui/worker.py
===============
新しい画面の**計算のスレッド 1 本**。

1 回の手順（`step`）:
    版の写しを取る → `config.validate_config` → `SimParams` →
    `engine.terrain_key` が手元の地形と違えば `engine.fetch_terrain(cancel=)` →
    `engine.calculate` → その版の見出しとして載せる。

🔑 **古い入力の取得を止める**（哲学④）＝`submit` の側で、取得中の地形の鍵と新しい入力の鍵が
違えば `cancel` を立てる。地形に効かない欄だけが変わったときは取得を続け、取り終えてから
最新の入力で計算する（取り終えた時点で版が進んでいれば、その版で計算し直す）。

- `Cancelled` は失敗ではない＝次の版へ戻るだけ。
- `DemUnreachableError` は `terrain` の見出し（地形なし＋1 文）。
- 計算の関数は差し替えられる（試験はネットワークを使わない）。

作業面（ステージ10b）＝今の面が `single` でなければ `_step_sheet`:

- 複数経路・中継＝`engine.run_paths` で 1 件ずつ取得して計算し、1 件ごとに `sheet` の見出しを
  載せ直す（最初の 1 件までは `computing` の取得の進み具合）。中継は区間を 1 件と数える
- 条件探索＝1 回線の地形（鍵が同じなら使い回す）の上で `scenario.evaluate`＝純計算で速い
  ＝全件そろえて 1 回で載せる
- 全件そろったときだけ全体の語（`verdict.aggregate`＝NG＞REVIEW＞OK・ERROR か空なら ERROR）と
  語ごとの件数（`verdict.counts`）を載せる
- 入力の誤り（面の字・値域・空の表）は `input` の見出し＋1 文＝地形を取る前に弾く
- 🔑 **作業面の実行中に版が進めば無条件に取り消す**（1 回線の「地形の鍵が違うときだけ」とは
  別）＝表の 1 字を直しても、走っている全件は古い入力の結果
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from core import config
from core import disclosure
from core import engine
from core import i18n
from core import simulation as sim
from core import scenario as scn
from core import units
from core import verdict as vd
from core.verdict import REVIEW
from webui import sheets as sheets_mod
from webui.profile import profile_fields
from webui.state import AppState

logger = logging.getLogger("radiosim")


#: 見出しに載せる数＝(見出しの名前, 結果の属性)。損失の内訳（`server.breakdown`）の値もここ。
_RESULT_NUMBERS = (
    ("margin", "actual_margin"), ("p_rx", "p_rx"), ("eirp", "eirp"), ("fspl", "fspl"),
    ("diff_loss", "diff_loss"), ("veg_loss", "veg_loss"), ("env_loss", "env_loss"),
    ("rain_loss", "rain_loss"), ("gas_loss", "gas_loss"), ("total_loss", "total_loss"),
)
#: 内訳の行のうち入力から取る数＝(見出しの名前, `SimParams` の属性)。
_PARAM_NUMBERS = (("gain_rx", "gain_rx"), ("sens", "sens"))

#: 複数経路と中継の共通設定のうち検査する欄（旧画面 `views/batch_run._read_base_params` と同じ）。
#: ⚠️ 端点と空中線高は検査しない＝各行が持つ（1 回線の面の飾り）。`validate_config` を通すと
#: 「送受信が同一地点」で正当な実行が偽陽性で止まる（B-018）。
_SHEET_BASE_KEYS = ("freq", "p_tx", "gain_tx", "gain_rx", "sens", "veg_h", "k_factor",
                    "resolution", "rain_rate", "env_type", "diff_method")
#: 共通設定から外す欄＝行が上書きする。形を通すため既定値で埋める（`_make_params` が差し替える）。
_ROW_OWNED_KEYS = ("start", "end", "h_tx", "h_rx")


def _item_fields(label: str, result: Any = None, params: Any = None,
                 error: "Exception | None" = None) -> "dict[str, Any]":
    """作業面の表の 1 行（名前・語・余裕度・受信電力・両端）。失敗は ERROR＋1 文。"""
    item: dict[str, Any] = {"label": label}
    if params is not None:
        item["tx"] = [float(params.lat_tx), float(params.lon_tx)]
        item["rx"] = [float(params.lat_rx), float(params.lon_rx)]
    if error is not None or result is None:
        item["word"] = vd.ERROR
        item["message"] = str(error) if error is not None else ""
        return item
    fields = _verdict_fields(result)
    item["word"] = fields["word"]
    item["margin"], item["p_rx"] = fields["margin"], fields["p_rx"]
    return item


class _InputProblem(Exception):
    """作業面の入力の誤り＝`input` の見出しの 1 文。"""


def _verdict_fields(result: Any, params: Any = None) -> "dict[str, Any]":
    """判定の帯と損失の内訳に出す分。"""
    verdict = getattr(result, "verdict", None)
    word = verdict.word if verdict is not None else result.status
    fields: dict[str, Any] = {"word": word}
    pairs = [(n, result, a) for n, a in _RESULT_NUMBERS]
    if params is not None:
        pairs += [(n, params, a) for n, a in _PARAM_NUMBERS]
    for name, obj, attr in pairs:
        fields[name] = round(float(getattr(obj, attr)), units.DB_DECIMALS)   # dB の桁は units
    # 前提を振った余裕度の幅（ERROR・v1 は無い）と、REVIEW を裏返す前提（帳票と同じ字）
    if verdict is not None and verdict.worst_margin is not None and verdict.best_margin is not None:
        fields["worst"] = round(float(verdict.worst_margin), units.DB_DECIMALS)
        fields["best"] = round(float(verdict.best_margin), units.DB_DECIMALS)
    if verdict is not None and verdict.word == REVIEW and verdict.flipped_by:
        fields["flipped"] = i18n.t("html_verdict_flipped").format(
            list=disclosure.flipped_list_text(verdict.flipped_by))
    return fields


class Worker:
    def __init__(
        self,
        state: AppState,
        *,
        fetch: "Callable[..., Any]" = engine.fetch_terrain,
        calc: "Callable[..., Any]" = engine.calculate,
        key: "Callable[[Any], Any]" = engine.terrain_key,
        validate: "Callable[[dict], list[str]]" = config.validate_config,
        profile: "Callable[[Any, Any], dict | None]" = profile_fields,
    ) -> None:
        self.state = state
        self._fetch, self._calc, self._key, self._validate = fetch, calc, key, validate
        self._profile = profile
        self._lock = threading.Lock()
        self._cancel: "threading.Event | None" = None    # 取得中の地形の取り消し
        self._fetch_key: Any = None                      # 取得中の地形の鍵
        self._terrain: Any = None
        self._terrain_key: Any = None
        self._sheet_cancel: "threading.Event | None" = None   # 作業面の実行の取り消し
        self._wake = threading.Event()
        self._wake.set()                                 # 起動したら 1 回は計算する

    # ---- HTTP のスレッドから ---------------------------------------------
    def submit(self, changes: dict, *, workspace: "str | None" = None,
               sheets: "dict | None" = None) -> int:
        """入力を変え、地形に効く欄が変わったら取得中の地形を取り消す。今の版を返す。

        作業面と面の入力（ステージ10a）は `state.update` へそのまま渡す（形の検査も向こう）。
        """
        version = self.state.update(changes, workspace=workspace, sheets=sheets)
        _, inputs = self.state.snapshot()
        new_key = self._key_or_none(inputs)
        with self._lock:
            if self._cancel is not None and self._fetch_key != new_key:
                self._cancel.set()
            if self._sheet_cancel is not None:           # 作業面は版が進めば無条件
                self._sheet_cancel.set()
        self._wake.set()
        return version

    def _key_or_none(self, inputs: dict) -> Any:
        if self._validate(inputs):
            return None
        try:
            return self._key(sim.SimParams(inputs))
        except (ValueError, KeyError, IndexError):
            return None

    # ---- 計算のスレッド --------------------------------------------------
    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            if not self._wake.wait(0.5):
                continue
            self._wake.clear()
            try:
                self.step()
            except Exception:                            # 1 回の失敗でスレッドを殺さない
                logger.exception("webui: 計算の手順で予期しない例外")

    def step(self) -> None:
        version, inputs = self.state.snapshot()
        ws_version, workspace, sheets = self.state.workspace_snapshot()
        if ws_version != version:                        # 2 つの写しの間に入力が進んだ
            self._wake.set()
            return
        if workspace != "single":
            self._step_sheet(version, inputs, workspace, sheets)
            return
        problems = self._validate(inputs)
        if problems:
            self.state.publish(version, "input", message=problems[0])
            return
        params = sim.SimParams(inputs)
        key = self._key(params)
        if key != self._terrain_key:
            if not self._fetch_terrain(version, params, key):
                return
            if self.state.version != version:            # 取得の間に入力が進んだ
                self._wake.set()
                return
        try:
            result = self._calc(self._terrain, params)
            fields = _verdict_fields(result, params)
            shape = self._profile(self._terrain, params)   # 断面図（帳票と同じ線）
        except Exception as e:
            logger.exception("webui: 計算に失敗")
            self.state.publish(version, "error", message=str(e))
            return
        if shape is not None:
            fields["profile"] = shape
        self.state.publish(version, "verdict", **fields)

    # ---- 作業面 ------------------------------------------------------------
    def _step_sheet(self, version: int, inputs: dict, workspace: str, sheets: dict) -> None:
        try:
            if workspace == "scenario":
                self._run_scenario(version, inputs, sheets["scenario"])
            else:
                self._run_paths(version, inputs, workspace, sheets[workspace])
        except sim.Cancelled:
            self._wake.set()                             # 新しい版でやり直す
        except sim.DemUnreachableError as e:
            self.state.publish(version, "terrain",
                               message=str(e) or sim.dem_unreachable_message())
        except _InputProblem as e:
            self.state.publish(version, "input", message=str(e))
        except Exception as e:
            logger.exception("webui: 作業面の実行に失敗")
            self.state.publish(version, "error", message=str(e))

    def _publish_sheet(self, version: int, workspace: str, items: list, total: int,
                       **extra: Any) -> None:
        fields: dict[str, Any] = {"workspace": workspace, "items": items,
                                  "done": len(items), "total": total, **extra}
        if len(items) == total:                          # 全件そろったときだけ全体を言う
            words = [it["word"] for it in items]
            fields["overall"] = vd.aggregate(words)
            fields["counts"] = vd.counts(words)
        self.state.publish(version, "sheet", **fields)

    def _sheet_base(self, inputs: dict) -> Any:
        """複数経路と中継の共通設定（端点と空中線高は行が持つ）。"""
        for key in _SHEET_BASE_KEYS:
            reason = config.validate_value(key, inputs[key])
            if reason is not None:
                raise _InputProblem(f"[{key}] {reason}")
        base = dict(inputs)
        base.update({k: config.DEFAULT_CONFIG[k] for k in _ROW_OWNED_KEYS})
        return sim.SimParams(base)

    def _run_paths(self, version: int, inputs: dict, workspace: str, sheet: Any) -> None:
        from report import batch                         # matplotlib を引く＝ここで読む（I-232）
        try:
            if workspace == "batch":
                rows = sheets_mod.batch_rows(sheet)
                problems = batch.validate_rows(rows)
                labels = [r.path_id for r in rows]
            else:
                from report import multihop as mh
                path = sheets_mod.multihop_path(sheet)
                problems = mh.validate_path(path)
                rows = mh.hop_rows(path) if not problems else []
                labels = [r.note for r in rows]          # 「P1 → 中継」
        except ValueError as e:
            raise _InputProblem(str(e)) from None
        if problems:
            raise _InputProblem(problems[0])
        base = self._sheet_base(inputs)
        cancel = self._start_sheet(version)
        try:
            items: list = []
            outcomes = engine.run_paths(
                rows, lambda row: batch._make_params(row, base), cancel=cancel,
                on_progress=lambda done, num: self.state.set_progress(version, int(done), int(num)),
                label=lambda row: row.path_id, fetch=self._fetch, calc=self._calc)
            for (_row, out), label in zip(outcomes, labels):
                items.append(_item_fields(label, out.result, out.params, out.error))
                self._publish_sheet(version, workspace, list(items), len(rows))
        finally:
            self._end_sheet()

    def _run_scenario(self, version: int, inputs: dict, spec: Any) -> None:
        problems = self._validate(inputs)
        if problems:
            raise _InputProblem(problems[0])
        params = sim.SimParams(inputs)
        try:
            conds, axis, values = sheets_mod.conditions(spec)
        except ValueError as e:
            raise _InputProblem(str(e)) from None
        problems = scn.validate_base(params)
        if problems:
            raise _InputProblem(problems[0])
        key = self._key(params)
        if key != self._terrain_key:
            if not self._fetch_terrain(version, params, key):
                return
            if self.state.version != version:            # 取得の間に入力が進んだ
                self._wake.set()
                return
        cancel = self._start_sheet(version)
        try:
            points = scn.evaluate(self._terrain, params, conds, cancel=cancel, calc=self._calc)
        finally:
            self._end_sheet()
        items = [{**_item_fields(p.label, p.result), "overrides": dict(p.overrides)}
                 for p in points]
        self._publish_sheet(version, "scenario", items, len(conds),
                            mode=spec["mode"], axis=axis, values=list(values))

    def _start_sheet(self, version: int) -> threading.Event:
        """作業面の取り消しを置く。置く前に版が進んでいたら、置いた時点で立てる。"""
        cancel = threading.Event()
        with self._lock:
            self._sheet_cancel = cancel
        if self.state.version != version:
            cancel.set()
        return cancel

    def _end_sheet(self) -> None:
        with self._lock:
            self._sheet_cancel = None

    def _fetch_terrain(self, version: int, params: Any, key: Any) -> bool:
        cancel = threading.Event()
        with self._lock:
            self._cancel, self._fetch_key = cancel, key
        total = int(getattr(params, "num", 0))
        try:
            terrain = self._fetch(
                params,
                lambda done: self.state.set_progress(version, int(done), total),
                cancel=cancel)
        except sim.Cancelled:
            self._wake.set()                             # 新しい版で取り直す
            return False
        except sim.DemUnreachableError as e:
            self.state.publish(version, "terrain",
                               message=str(e) or sim.dem_unreachable_message())
            return False
        except Exception as e:
            logger.exception("webui: 地形の取得に失敗")
            self.state.publish(version, "error", message=str(e))
            return False
        finally:
            with self._lock:
                self._cancel, self._fetch_key = None, None
        self._terrain, self._terrain_key = terrain, key
        return True
