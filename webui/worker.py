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
from core import units
from core.verdict import REVIEW
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
