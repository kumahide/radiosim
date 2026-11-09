"""
webui/state.py
==============
新しい画面の**入力の集合と見出し**。

- 入力＝`config.SIM_KEYS` の鍵だけ（知らない鍵は受けない）。値が変わったときだけ**版**を上げる。
- 見出し＝判定の帯に出すもの。種類は 6 つ:
  `verdict`（判定）・`sheet`（作業面の結果＝1 件ごとに載せ直す・ステージ10b）・
  `input`（入力待ち＋1 文）・`terrain`（地形なし＋1 文）・`error`・`computing`。
  `computing` 以外は「どの版の入力で出したか」を持つ。

🔒 **古い版の結果を今の判定に見せない**＝`headline()` は、結果の版が今の版と同じときだけ
それを返し、違えば `computing` を返す（`tests/test_webui.py` が縛る）。

画面は `wait(after)` の長いポーリングで変化を待つ＝変化の数（`revision`）が `after` を
越えたら返る。

作業面（ステージ10a）＝今の面（`sheets.WORKSPACES`）と面ごとの入力（`sheets.SHEETS`）も
同じ錠で持つ。入力の集合・面・面の入力のどれが変わっても版は 1 つ上がる（1 回の `update`
で何を変えても 1 つ）＝結果の版の比べ方は 1 回線と同じ。

計算方式（`calc_profile`・ステージ10d）＝画面の欄ではなく**開いたファイルの属性**（旧画面の
`ProjectDoc.calc_profile` と同じ）。新規と v2 のファイルは既定（v2）・3.x の `.rsproj`（v1）は
v1 のまま計算する（2026-11-08 経営者の決定）。入力の集合とは別に持ち（`update` は知らない鍵を
断る）、計算のスレッドの写し（`snapshot`）にだけ混ぜる＝`SimParams(inputs)` が拾う。
"""

from __future__ import annotations

import copy
import threading
from typing import Any

from core import calc_profile
from core import config
from webui import sheets as sheets_mod

#: 見出しの種類（JS もこの字で分ける）。
KINDS = ("verdict", "sheet", "input", "terrain", "error", "computing")

#: 知らせの種類（`info`＝更新の知らせなど・`warn`＝定義の却下など）。
NOTICE_KINDS = ("info", "warn")


def _pct(p: "tuple[int, int, int]") -> int:
    """取得の進み具合を画面と同じ百分率の整数にする（`app.js` の `Math.floor`）。"""
    return (100 * p[1]) // p[2] if p[2] > 0 else 0


class UnknownKeyError(ValueError):
    """入力の集合に無い鍵、または文字列でない値を受けた。"""


class AppState:
    """入力・版・見出しを 1 つの錠で持つ（計算のスレッドと HTTP のスレッドが触る）。"""

    def __init__(self, initial: "dict[str, str] | None" = None) -> None:
        base = {k: config.DEFAULT_CONFIG[k] for k in sorted(config.SIM_KEYS)}
        for k, v in (initial or {}).items():
            if k in base and isinstance(v, str):
                base[k] = v
        self._inputs: dict[str, str] = base
        self._workspace: str = sheets_mod.WORKSPACES[0]
        self._sheets: dict[str, Any] = sheets_mod.empty()
        self._profile: str = calc_profile.DEFAULT_PROFILE
        self._version = 1
        self._revision = 1
        self._result: "dict[str, Any] | None" = None     # 版つきの見出し
        self._progress: "tuple[int, int, int] | None" = None   # (版, 取得済み, 点数)
        self._notices: "list[dict[str, Any]]" = []
        self._notice_seq = 0
        self._cond = threading.Condition()

    # ---- 入力 -------------------------------------------------------------
    def update(self, changes: dict, *, workspace: "str | None" = None,
               sheets: "dict | None" = None, profile: "str | None" = None) -> int:
        """変えた欄・面・面の入力・計算方式を受け、今の版を返す。

        全部を検査してから 1 回で反映する＝どこか 1 つでも形が違えば何も変えない
        （知らない鍵は `UnknownKeyError`・面の形の誤りは `sheets.SheetError`・知らない方式は
        `ValueError`）。面の入力は面ごとに丸ごと差し替える（行の一部だけは送らない）。
        方式を変えるのは `.rsproj` を開いたときだけ（画面に欄は無い）。
        """
        bad = sorted(str(k) for k, v in changes.items()
                     if k not in self._inputs or not isinstance(v, str))
        if bad:
            raise UnknownKeyError(", ".join(bad))
        if workspace is not None and workspace not in sheets_mod.WORKSPACES:
            raise sheets_mod.SheetError(f"unknown workspace: {workspace!r}")
        if sheets is not None and not isinstance(sheets, dict):
            raise sheets_mod.SheetError("sheets: not an object")
        if profile is not None:
            calc_profile.require(profile)
        checked = {name: sheets_mod.check(name, value) for name, value in (sheets or {}).items()}
        with self._cond:
            changed = {k: v for k, v in changes.items() if self._inputs[k] != v}
            new_sheets = {n: v for n, v in checked.items() if self._sheets[n] != v}
            new_ws = workspace if workspace not in (None, self._workspace) else None
            new_profile = profile if profile not in (None, self._profile) else None
            if changed or new_sheets or new_ws or new_profile:
                self._inputs.update(changed)
                self._sheets.update(new_sheets)
                if new_ws:
                    self._workspace = new_ws
                if new_profile:
                    self._profile = new_profile
                self._version += 1
                self._progress = None
                self._bump()
            return self._version

    def snapshot(self) -> "tuple[int, dict[str, str]]":
        """(今の版, 入力の写し＋`calc_profile`)。計算のスレッドはこの写しで計算する。

        方式は写しにだけ混ぜる＝`SimParams(inputs)` が拾い、地形の鍵（双線形か）にも効く。
        画面へ返す写し（`wait` の `inputs`）には混ぜない（欄ではない）。
        """
        with self._cond:
            return self._version, {**self._inputs, "calc_profile": self._profile}

    def workspace_snapshot(self) -> "tuple[int, str, dict[str, Any]]":
        """(今の版, 今の作業面, 面の入力の写し)。入力の集合は `snapshot` と同じ版で読む。"""
        with self._cond:
            return self._version, self._workspace, copy.deepcopy(self._sheets)

    def document(self) -> "tuple[dict[str, str], dict[str, Any], str]":
        """(入力の写し, 面の入力の写し, 計算方式) を 1 回の錠で＝`.rsproj` の保存が同じ版を書く。"""
        with self._cond:
            return dict(self._inputs), copy.deepcopy(self._sheets), self._profile

    @property
    def version(self) -> int:
        with self._cond:
            return self._version

    # ---- 見出し -----------------------------------------------------------
    def publish(self, version: int, kind: str, **fields: Any) -> None:
        """`version` の入力で出した見出しを載せる（古い版でも載せる＝見せるかは `headline`）。"""
        if kind not in KINDS or kind == "computing":
            raise ValueError(kind)
        with self._cond:
            self._result = {"kind": kind, "version": version, **fields}
            self._bump()

    def set_progress(self, version: int, done: int, total: int) -> None:
        with self._cond:
            if version != self._version:
                return
            old = self._progress
            self._progress = (version, done, total)
            # 画面が出すのは百分率の整数だけ＝1 点ごとに起こすと 1 回の取得で千回を超える（実測 2217 点）
            if old is None or old[0] != version or _pct(old) != _pct(self._progress):
                self._bump()

    def headline(self) -> "dict[str, Any]":
        with self._cond:
            return self._headline_locked()

    def _headline_locked(self) -> "dict[str, Any]":
        r = self._result
        if r is not None and r["version"] == self._version:
            return dict(r)
        head: dict[str, Any] = {"kind": "computing", "version": self._version}
        p = self._progress
        if p is not None and p[0] == self._version:
            head["done"], head["total"] = p[1], p[2]
        return head

    # ---- 知らせ（起動時の言語・地形の定義の却下・更新の確認＝I-256） ----------
    def add_notice(self, kind: str, text: str) -> int:
        """画面の上の帯に出す知らせを 1 件足し、その番号を返す。

        閉じた印は画面だけが持つ（読み直すと全部また出る＝起動 1 回ぶんの知らせ）。
        """
        if kind not in NOTICE_KINDS:
            raise ValueError(kind)
        with self._cond:
            self._notice_seq += 1
            self._notices.append({"id": self._notice_seq, "kind": kind, "text": str(text)})
            self._bump()
            return self._notice_seq

    # ---- 長いポーリング ---------------------------------------------------
    def _bump(self) -> None:
        self._revision += 1
        self._cond.notify_all()

    def wait(self, after: int, timeout: float) -> "dict[str, Any]":
        """変化の数が `after` を越えるか `timeout` 秒たったら、今の姿を返す。"""
        with self._cond:
            self._cond.wait_for(lambda: self._revision > after, timeout=timeout)
            return {
                "revision": self._revision,
                "version": self._version,
                "inputs": dict(self._inputs),
                "workspace": self._workspace,
                "sheets": copy.deepcopy(self._sheets),
                "calc_profile": self._profile,             # 欄ではない＝出すかは 10f で決める
                "headline": self._headline_locked(),
                "notices": [dict(n) for n in self._notices],
            }
