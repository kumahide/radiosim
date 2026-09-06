"""
core/diagnostics.py
====================
診断パッケージ（3.2 段9）。`core/env_facts.py` が集めた環境事実を ZIP 1 本へ
まとめる。「何が入るか」を保存前に見せて取捨選択させるのは呼び出し側（画面）の
仕事＝このモジュールは選ばれた項目から ZIP を組み立てるところまでを持つ。

⚠️ **成果物は既定で除外**（判断点②＝「結果ファイルかどうか」ではなく「成果物
かどうか」で割る）＝`results/` の中身は顧客の案件情報そのものなので、呼び出し側が
明示的に選んだものだけを対象にする。環境事実（版・設定・ログ・キャッシュ統計・
環境情報・保存先パス）は成果物ではないので既定で全項目が対象になる。

⚠️ **パス中のユーザー名を伏せる**＝保存先の絶対パス（`C:\\Users\\<名前>\\...`）は
サポートに送る診断情報として要るが、利用者名そのものは要らない（env_facts.py が
座標を伏せているのと同種の穴）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile

from core import config
from core import dem
from core import env_facts

#: 環境事実として提示する項目。`env_facts.collect()` の 5 キーに、保存先パス
#: 一覧（`paths`）を加えたもの。全項目が既定で選択される（成果物ではないため）。
FACT_ITEMS: tuple[str, ...] = (
    "version", "config", "recent_log", "cache_stats", "environment", "paths",
)

#: Windows のユーザープロファイル配下（`\Users\<名前>\` または `/Users/<名前>/`）。
_USERNAME_RE = re.compile(r"([\\/][Uu]sers[\\/])[^\\/]+")


def redact_username(path: str) -> str:
    """パス中の `Users\\<名前>` を `Users\\***` に伏せる。該当箇所が無ければそのまま。"""
    return _USERNAME_RE.sub(lambda m: m.group(1) + "***", path)


def redacted_paths() -> dict[str, str]:
    """診断に要る保存先パス一覧（ユーザー名は伏せる）。"""
    return {
        "config_file":      redact_username(config.CONFIG_FILE),
        "results_dir":      redact_username(config.RESULTS_DIR),
        "log_file":         redact_username(config.LOG_FILE),
        "profile_log_file": redact_username(config.PROFILE_LOG_FILE),
        "cache_dir":        redact_username(dem.CACHE_DIR),
        "user_lang_dir":    redact_username(config.USER_LANG_DIR),
    }


def list_result_runs() -> list[str]:
    """`results/` 直下の名前一覧（成果物候補）。無ければ空リスト。

    **既定では 1 つも選ばれない**＝一覧は「選べる」ことを示すだけで、含めるかは
    呼び出し側（画面）が利用者に確認してから `build_package` へ渡す。
    """
    if not os.path.isdir(config.RESULTS_DIR):
        return []
    return sorted(os.listdir(config.RESULTS_DIR))


def build_package(zip_path: str, selected_facts: "set[str] | None" = None,
                   selected_results: "list[str] | None" = None) -> None:
    """選ばれた項目から診断 ZIP を組み立てる。

    `selected_facts` を省くと全項目（`FACT_ITEMS`）を含める。`selected_results`
    を省く（または空）と成果物は 1 件も入らない＝**既定で除外**を関数の既定値
    でも保つ。

    **原子的に書く**（[[feedback-atomic-writes]]）＝`zip_path` と同じディレクトリに
    一時ファイルを作り切ってから `os.replace`。ZIP 生成の途中で失敗しても、
    そこに壊れた ZIP が残らない。
    """
    facts = env_facts.collect()
    facts["paths"] = redacted_paths()
    if selected_facts is None:
        selected_facts = set(FACT_ITEMS)
    filtered = {k: v for k, v in facts.items() if k in selected_facts}

    directory = os.path.dirname(os.path.abspath(zip_path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".radiosim_diag-", suffix=".zip")
    os.close(fd)
    # B-183＝一時 ZIP・最終保存先が選択済み成果物（results/<run>）の中に
    # 収まっていると、後続の os.walk(src) が生成中の自分自身を発見して
    # 取り込んでしまう。走査対象からこの2つのパスだけを明示的に除外する。
    # ⚠️ **Windows では大文字小文字を無視した比較にする**（Codex round78）＝
    # `os.path.abspath()` はケースを正規化しないため、`zip_path` と
    # `config.RESULTS_DIR` でドライブ文字の大小が食い違うだけで一致判定が
    # 外れ、除外が空振りする。`os.path.normcase` で比較用の値だけ揃える。
    def _norm(p: str) -> str:
        return os.path.normcase(os.path.abspath(p))

    _self_paths = {_norm(tmp), _norm(zip_path)}
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("diagnostics.json",
                        json.dumps(filtered, indent=2, ensure_ascii=False))
            for name in (selected_results or []):
                src = os.path.join(config.RESULTS_DIR, name)
                if os.path.isdir(src):
                    for dirpath, _dirs, filenames in os.walk(src):
                        for fn in filenames:
                            full = os.path.join(dirpath, fn)
                            if _norm(full) in _self_paths:
                                continue
                            arc = os.path.join(
                                "results", name, os.path.relpath(full, src))
                            zf.write(full, arc)
                # 単一ファイルが選択対象そのもの（＝保存先と同じファイルを
                # 上書き保存するケース）でも、同じ除外判定を通す（Codex round78）。
                elif os.path.isfile(src) and _norm(src) not in _self_paths:
                    zf.write(src, os.path.join("results", name))
        os.replace(tmp, zip_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
