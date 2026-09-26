"""
core/update_check.py
====================
**新しい版があるかを GitHub Releases に尋ねる**（I-178 段階 1＝ヘルプ > 更新の確認）。

利用者が押したときだけ 1 回問い合わせる。**起動時の確認**（I-179 段階 2）は
既定オフで、オンにした人だけ 1 日 1 回まで（`auto_due()`）。ダウンロードと入れ替えはしない
（署名しない決定と当たる＝未署名のファイルをネットから取って実行すると、
Smart App Control に止められたとき理由を説明できない）。リリースのページを
開くところまでがこのアプリの仕事で、その先はブラウザと利用者に任せる。

- 通信は `dem.http_session()`＝設定 > プロキシ設定 に従う。
- 版の大小は `version.version_tuple()`（B-162 以降、a/b → RC → 正式の順で正しい）。
- **いまの版が正式なら正式だけ**を、RC（a/b も）なら RC も数える。
- 候補の選び方は `pick_newer()`＝純関数（通信と分けて試せる形）。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from core import version

#: 問い合わせ先（公開リポジトリ・認証なし＝IP あたり 1 時間 60 回）。新しい順に返る。
RELEASES_API = "https://api.github.com/repos/kumahide/radiosim/releases?per_page=30"
#: 開いてよいページの頭（応答の `html_url` がこれで始まらなければ捨てる＝
#: 問い合わせの結果をそのままブラウザへ渡さない）。
RELEASE_PAGE_PREFIX = "https://github.com/kumahide/radiosim/releases/"

#: 待つ上限（秒）＝押してから答えが出るまで。接続と読み取りの両方に効く。
TIMEOUT_S = 10


@dataclass(frozen=True)
class Release:
    """知らせる版 1 つ。"""
    version: str       # タグから先頭の `v` を外した字（`3.6` / `3.6RC2`）
    tag: str           # タグの字そのまま（`3.6` / `v3.6RC2`＝ソース実行の案内に使う）
    url: str           # リリースのページ


class UpdateCheckError(Exception):
    """問い合わせが答えを返さなかった。`kind` で次の一手を選ぶ。

    - `network`      … 届かない（接続・プロキシ・タイムアウト・5xx）
    - `rate_limited` … 回数の上限（403＋残り 0／429）＝時間をおけば通る
    - `bad_response` … 届いたが読めない（形が違う）
    """

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


def pick_newer(releases: list, current: str = version.APP_VERSION) -> Release | None:
    """応答の一覧から、`current` より新しい版のうち最大のものを返す（無ければ `None`）。

    - `draft` と、読めないタグ（`version_tuple` が `(0,0,0,0)`）は捨てる。
    - `current` が正式なら、プレリリース（`prerelease` が真、またはタグが RC/a/b）は数えない。
    - `html_url` が `RELEASE_PAGE_PREFIX` で始まらないものは捨てる。
    """
    now = version.version_tuple(current)
    want_pre = not version.is_final(current)
    best: tuple[tuple[int, int, int, int], Release] | None = None
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        tag = rel.get("tag_name")
        url = rel.get("html_url")
        if not isinstance(tag, str) or not isinstance(url, str) \
                or not url.startswith(RELEASE_PAGE_PREFIX):
            continue
        name = tag[1:] if tag[:1] in ("v", "V") else tag
        vt = version.version_tuple(name)
        if vt == (0, 0, 0, 0):
            continue
        if not want_pre and (rel.get("prerelease") or not version.is_final(name)):
            continue
        if vt > now and (best is None or vt > best[0]):
            best = (vt, Release(version=name, tag=tag, url=url))
    return best[1] if best else None


def auto_due(conf: dict, today: datetime.date) -> bool:
    """起動時の確認を今日打つか（I-179）。設定の 2 キーだけを見る純関数。

    - `update_check_auto` が `"on"` のときだけ（既定 `"off"`＝会社 PC やオフラインの
      現場で、起動のたびに外へ通信しない）。`"on"` 以外の字はすべてオフとして読む。
    - `update_check_last`（最後に**試みた**日・ISO）が今日でなければ打つ。成否を問わず
      試みた日を記録する＝失敗しても同じ日に 2 度目は打たない（認証なしの API は
      IP あたり 1 時間 60 回＝会社の NAT の内側では手動の分と同じ枠を分け合う）。
      時計が戻って記録が「未来」でも、今日と違えば打つ（止まったままにしない）。
    """
    if conf.get("update_check_auto") != "on":
        return False
    return conf.get("update_check_last") != today.isoformat()


def fetch_releases() -> list:
    """GitHub Releases の一覧（新しい順の dict の列）を取る。失敗は `UpdateCheckError`。"""
    import requests

    from core import dem

    try:
        res = dem.http_session().get(
            RELEASES_API, timeout=TIMEOUT_S,
            headers={"Accept": "application/vnd.github+json"})
    except requests.RequestException as e:
        raise UpdateCheckError("network", f"{type(e).__name__}: {e}") from e
    if res.status_code == 429 or (
            res.status_code == 403
            and res.headers.get("X-RateLimit-Remaining") == "0"):
        raise UpdateCheckError("rate_limited", f"HTTP {res.status_code}")
    if res.status_code != 200:
        raise UpdateCheckError("network", f"HTTP {res.status_code}")
    try:
        data = res.json()
    except ValueError as e:
        raise UpdateCheckError("bad_response", f"{type(e).__name__}: {e}") from e
    if not isinstance(data, list):
        raise UpdateCheckError("bad_response", f"not a list: {type(data).__name__}")
    return data


def check(current: str = version.APP_VERSION) -> Release | None:
    """問い合わせて、知らせる版を返す（無ければ `None`・失敗は `UpdateCheckError`）。"""
    return pick_newer(fetch_releases(), current)
