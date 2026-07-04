"""
tests/test_env_consistency.py
=============================
実行環境 ⇔ requirements.txt ピンの整合を検証する環境ゲート。

背景（2026-07-04 環境監査）: .venv の numpy/requests がピンと乖離したまま
テスト・開発が進んでいた（CI はピン版・ローカルは別版という片肺状態）。
ドリフトは静かに起きるため「監査で見つける」のではなく「ずれた瞬間に
pytest が落ちる」Tier-0 ゲートに昇格する。

守備範囲: 本テストは **pytest を実行しているインタープリタの環境**
（ローカル=.venv / CI=ランナー）を検査する。もう一方の系統である
ビルド用 Python（PATH 側）は build.bat が `pip install -r requirements.txt`
でビルドごとにピンを強制するため、2系統とも常時ガードされる。

ピンを意図的に上げる手順は requirements.txt 冒頭コメントのとおり
（bump→フルテスト＋ビルド→コミット。Requires-Python の再確認も忘れずに）。
"""

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _pinned_requirements() -> list[tuple[str, str]]:
    """requirements.txt の (パッケージ名, ピン版) 一覧を返す。"""
    pins: list[tuple[str, str]] = []
    for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)\s*==\s*(\S+)$", line)
        if m:
            pins.append((m.group(1), m.group(2)))
        else:
            # == 以外の行はここでは (name, None 相当) にせず、
            # test_all_requirements_are_pinned が名指しで落とす。
            pins.append((line, ""))
    return pins


_PINS = _pinned_requirements()


def test_all_requirements_are_pinned():
    """requirements.txt の全行が `name==version` 形式であること。

    ピン無し行が混ざると「既知良好版で再現ビルドする」前提が崩れる
    （範囲指定や無指定は環境ごとに解決結果が変わる）。
    """
    unpinned = [name for name, ver in _PINS if not ver]
    assert unpinned == [], (
        f"requirements.txt に == ピンでない行がある: {unpinned}"
    )


@pytest.mark.parametrize(
    "name,pinned", [(n, v) for n, v in _PINS if v],
    ids=[n for n, v in _PINS if v],
)
def test_installed_version_matches_pin(name, pinned):
    """インストール済みの実版がピンと一致すること。

    落ちたら: ①環境が古い → `pip install -r requirements.txt` で揃える
    ②意図的な更新 → requirements.txt のピンを上げてフルテスト＋ビルド後に
    コミット（README の Python 下限＝Requires-Python も再確認）。
    """
    try:
        actual = installed_version(name)
    except PackageNotFoundError:
        pytest.fail(
            f"{name} が実行環境に未インストール"
            "（pip install -r requirements.txt で導入する）"
        )
    assert actual == pinned, (
        f"{name}: 実行環境={actual} / requirements.txt ピン={pinned}。"
        "環境をピンに揃えるか、意図的な更新ならピン側を上げる。"
    )
