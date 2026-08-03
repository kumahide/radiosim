"""
check_bundle_imports.py
=======================
PyInstaller の依存解析レポート（`warn-<name>.txt`）を検査し、
**同梱物が無条件に import するのに exe へ入っていないモジュール**があればビルドを止める。

なぜ要るか（B-036 / B-037・2026-08-03）
--------------------------------------
2.6RC1 の exe は、図を作った瞬間に `ModuleNotFoundError: No module named 'timeit'`
で落ちていた。`matplotlib.dviread` が `fontTools` を経由して `timeit` を
トップレベル import するようになったのに、spec の `excludes` が `timeit` を
削っていたため。**この事実は RC1 のビルド時点で warn レポートに 1 行で出ていた**
（`excluded module named timeit - imported by fontTools.misc.loggingTools (top-level)`）。
誰も読まなかっただけで、情報は最初からあった。

**ソース実行の QA は原理的にこれを検出できない**（開発機には stdlib が全部ある）。
検出できる唯一の場所がここなので、助言でなく**ゲート**にする。

何を落とすか
------------
- `excluded module named X - imported by …(top-level)`
  ＝**我々が意図して除いた**ものを、同梱物が**無条件に**import する＝必ず落ちる。
- `missing module named X - imported by …(top-level)` は**落とさない**。
  上流が広く撒く任意依存（`collections.abc` の別名解決など）で毎回大量に出るため、
  ここで鳴らすと「毎回鳴るゲート」になり、読まれなくなる
  （[[feedback-promote-recurring-checks]] のゲートの壊れ方②）。

`(conditional)` / `(delayed)` も落とさない。`if __name__ == "__main__"` や
try/except の中にしか現れず、実行時に到達しないため。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 「excluded module named <name> - imported by <importers>」を拾う。
_LINE = re.compile(r"^excluded module named (\S+) - imported by (.+)$")

# 既知の安全な組み合わせ ＝ (除外したモジュール, それを import している側)。
# **理由を書けないものは足さない。** ここが膨らみ始めたら、それは
# 「間違ったものを要求しているゲート」の兆候（ゲートの壊れ方③）なので、
# 除外リストの方針そのものを見直す合図。
_ALLOW: dict[tuple[str, str], str] = {
    ("_frozen_importlib", "zipimport"):
        "PyInstaller のブートストラップ内部。実体は常に存在し、warn には構造上必ず出る。",
    ("cryptography", "urllib3.contrib.pyopenssl"):
        "pyopenssl 経由の TLS は本アプリでは使わない（`inject_into_urllib3` を呼ばない）。"
        "`urllib3.contrib.pyopenssl` 自体が到達しないので、その中の top-level は実行されない。",
}


def find_fatal_exclusions(warn_text: str) -> list[tuple[str, list[str]]]:
    """(除外されたモジュール名, トップレベルで import している側) の一覧を返す。"""
    fatal: list[tuple[str, list[str]]] = []
    for line in warn_text.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        name, importers = m.group(1), m.group(2)
        top = []
        for part in importers.split(","):
            part = part.strip()
            if not part.endswith("(top-level)"):
                continue
            importer = part[: -len("(top-level)")].strip()
            if (name, importer) in _ALLOW:
                continue
            top.append(part)
        if top:
            fatal.append((name, top))
    return fatal


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_bundle_imports.py <warn-*.txt>", file=sys.stderr)
        return 2

    warn_path = Path(argv[1])
    if not warn_path.exists():
        # レポートが無いのは「検査していない」ことなので、黙って通さない。
        print(f"[ERROR] PyInstaller warn report not found: {warn_path}", file=sys.stderr)
        return 1

    fatal = find_fatal_exclusions(warn_path.read_text(encoding="utf-8", errors="replace"))
    if not fatal:
        print("[OK] No excluded module is imported unconditionally by the bundle.")
        return 0

    print("", file=sys.stderr)
    print("[ERROR] The build would ship an exe that crashes on import.", file=sys.stderr)
    print("        These modules are in the spec's excludes, but bundled code", file=sys.stderr)
    print("        imports them at top level (i.e. always):", file=sys.stderr)
    for name, importers in fatal:
        print(f"          - {name}", file=sys.stderr)
        for imp in importers:
            print(f"              imported by {imp}", file=sys.stderr)
    print("", file=sys.stderr)
    print("        Fix: remove the name from `excludes` in radiosim.spec.", file=sys.stderr)
    print(f"        Report: {warn_path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
