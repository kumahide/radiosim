"""
buildtools/deploy_qa_fixtures.py
================================
動作確認用の設定ファイル（`qa_fixtures/`）を、確認したい配置先へ配る。

**なぜ要るか**＝宣言ファイル（`dem_sources.toml` / `tile_sources.toml`）の置き場は
「設定フォルダ」で、ポータブル配置ではそれが **exe の隣**（`dist\\RadioSimPro\\`）に
なる。ここはビルドのたびに作り直されるので、手で置いた確認用の設定は毎回消える。
⇒ **正本をリポジトリの `qa_fixtures/` に置き、ビルド後にここから配る。**

⛔ **build.bat からは呼ばない。** build.bat は zip とインストーラを作る＝確認用の
設定が配布物に混入する。**配るのはビルドが終わった後の、確認する人の手元だけ。**

使い方（`RADIOSIM_PYTHON` で実行する。cwd はリポジトリ直下）::

    & "$env:RADIOSIM_PYTHON" buildtools\\deploy_qa_fixtures.py            # dist\\RadioSimPro\\ へ
    & "$env:RADIOSIM_PYTHON" buildtools\\deploy_qa_fixtures.py --repo     # ソース実行用（リポジトリ直下）
    & "$env:RADIOSIM_PYTHON" buildtools\\deploy_qa_fixtures.py --appdata  # インストーラ版用（%APPDATA%\\RadioSim）
    & "$env:RADIOSIM_PYTHON" buildtools\\deploy_qa_fixtures.py --target <dir>
    & "$env:RADIOSIM_PYTHON" buildtools\\deploy_qa_fixtures.py --remove   # 配ったものを片付ける

配置先は**アプリが実際に読む場所を製品のコードに聞く**（`core.config` の
`USER_DEM_SOURCES_FILE` と同じ解決）＝ここで置き場を書き写すと、製品側が動いた日に
黙ってずれる。⚠️ `--appdata` は `SHGetKnownFolderPath` が返す実フォルダ
（環境変数の書き換えでは動かない＝[[feedback_known_folder_ignores_env_spoof]]）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(_REPO, "qa_fixtures")

#: 配る対象（`qa_fixtures/` の中で**製品が読む名前のものだけ**）。README や
#: 壊した実験用のコピーを一緒に配らないよう、名前で白紙から並べる。
FIXTURE_FILES = ("dem_sources.toml", "tile_sources.toml")


def _same_content(a: str, b: str) -> bool:
    """2 つのファイルの中身が同じか（B-267＝配ったものかどうかの判定）。

    ⚠️ **更新日時では判定しない**＝コピーは日時を持ち込まないので、配った直後の
    ファイルでも「別物」に見える。中身そのものを読む。
    """
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _portable_dist_dir() -> str:
    return os.path.join(_REPO, "dist", "RadioSimPro")


def _appdata_config_dir() -> str:
    """インストーラ版（非ポータブル）が設定を読む場所を**製品のコードに聞く**。"""
    sys.path.insert(0, _REPO)
    from core import config          # noqa: PLC0415（配置先の解決に必要なだけ）
    return os.path.dirname(config.USER_DEM_SOURCES_FILE.replace("/", os.sep))


def resolve_target(args: argparse.Namespace) -> str:
    if args.target:
        return os.path.abspath(args.target)
    if args.repo:
        return _REPO
    if args.appdata:
        return _appdata_config_dir()
    return _portable_dist_dir()


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--repo", action="store_true",
                   help="リポジトリ直下（ソースから main.py を実行して確認する場合）")
    g.add_argument("--appdata", action="store_true",
                   help="インストーラ版の設定フォルダ（%%APPDATA%%\\RadioSim）")
    g.add_argument("--target", help="配置先を直接指定する")
    ap.add_argument("--remove", action="store_true",
                    help="配った設定ファイルを消す（元の正本は消さない）")
    ap.add_argument("--force", action="store_true",
                    help="配置先の別内容のファイルを上書き（.bak へ退避）・削除する")
    args = ap.parse_args(argv)

    target = resolve_target(args)
    if not os.path.isdir(target):
        print(f"[ERROR] 配置先がありません: {target}\n"
              f"        ビルド前か、--target の指定が違います。", file=sys.stderr)
        return 1

    for name in FIXTURE_FILES:
        src = os.path.join(FIXTURES_DIR, name)
        dst = os.path.join(target, name)
        if args.remove:
            if not os.path.isfile(dst):
                continue
            # B-267＝**自分が配ったものだけ消す**。配置先には手で書いた本物の
            # 宣言が居ることがある（`--appdata` は実プロファイル）。
            if not args.force and not _same_content(src, dst):
                print(f"[SKIP] 配った内容と違うので消しません: {dst}\n"
                      f"       手で書いたものなら残すのが正しい（消すなら --force）。",
                      file=sys.stderr)
                continue
            os.remove(dst)
            print(f"[OK] removed: {dst}")
            continue
        if not os.path.isfile(src):
            print(f"[ERROR] 正本がありません: {src}", file=sys.stderr)
            return 1
        # B-267＝**先客が居たら上書きしない**（戻せないため）。同じ内容なら黙って
        # 配り直す＝配置は何度実行しても同じ結果になる。
        if os.path.isfile(dst) and not _same_content(src, dst):
            if not args.force:
                print(f"[ERROR] 配置先に別の内容のファイルがあります: {dst}\n"
                      f"        退避してから配り直すか、上書きしてよいなら --force。",
                      file=sys.stderr)
                return 1
            backup = dst + ".bak"
            shutil.copyfile(dst, backup)
            print(f"[NOTE] 退避しました: {backup}")
        shutil.copyfile(src, dst)
        print(f"[OK] {name} -> {dst}")

    if not args.remove:
        # ⚠️ 配布物と取り違えないための一言（zip を作り直すと混入する）。
        print("[NOTE] 確認用の設定です。この配置先を固め直して配布しないこと。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
