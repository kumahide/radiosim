"""更新の確認（I-178 段階 1）と、その回に取り出した次の一手 `fix_network` の検査。"""

import pytest
import requests

from core import i18n, simulation as sim, update_check, version
from core.update_check import Release, UpdateCheckError, pick_newer
from views import launcher_menu

_PAGE = update_check.RELEASE_PAGE_PREFIX


def _rel(tag, *, pre=False, draft=False, url=None):
    return {"tag_name": tag, "prerelease": pre, "draft": draft,
            "html_url": url or f"{_PAGE}tag/{tag}"}


# 実際の応答の形（2026-09-26 に確かめた）＝正式は `3.6`・RC は `v3.6RC2`。
_LIST = [_rel("3.6"), _rel("v3.6RC2", pre=True), _rel("v3.6RC1", pre=True),
         _rel("3.5"), _rel("v3.5RC1", pre=True)]


def _newer(rels, current) -> Release:
    got = pick_newer(rels, current)
    assert got is not None
    return got


class TestPickNewer:
    def test_final_user_is_told_of_a_newer_final(self):
        got = pick_newer(_LIST, "3.5")
        assert got == Release("3.6", "3.6", f"{_PAGE}tag/3.6")

    def test_final_user_is_not_told_of_a_release_candidate(self):
        assert pick_newer([_rel("v3.7RC1", pre=True), *_LIST], "3.6") is None

    def test_rc_tag_without_the_prerelease_flag_is_still_a_prerelease(self):
        assert pick_newer([_rel("v3.7RC1"), *_LIST], "3.6") is None

    def test_rc_user_is_told_of_a_newer_rc(self):
        assert _newer(_LIST, "3.6RC1").version == "3.6"   # 正式が RC より上

    def test_rc_user_gets_the_largest_candidate(self):
        got = _newer([_rel("v3.7RC2", pre=True), _rel("v3.7RC1", pre=True),
                      *_LIST], "3.6RC2")
        assert got.version == "3.7RC2" and got.tag == "v3.7RC2"

    def test_alpha_counts_as_a_prerelease_user(self):
        assert _newer([_rel("v3.7RC1", pre=True)], "3.7a1").version == "3.7RC1"

    def test_up_to_date_is_none(self):
        assert pick_newer(_LIST, "3.6") is None
        assert pick_newer(_LIST, "3.7a1") is None

    def test_drafts_unreadable_tags_and_foreign_urls_are_dropped(self):
        rels = [_rel("9.0", draft=True), _rel("nightly"),
                _rel("9.1", url="https://example.com/evil"), "junk", {}]
        assert pick_newer(rels, "3.6") is None


class _Res:
    def __init__(self, status=200, body=None, headers=None, bad_json=False):
        self.status_code, self._body = status, body
        self.headers, self._bad = headers or {}, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._body


class _Session:
    def __init__(self, res=None, exc=None):
        self.res, self.exc, self.calls = res, exc, []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        if self.exc:
            raise self.exc
        return self.res


@pytest.fixture
def session(monkeypatch):
    from core import dem

    def _use(**kw):
        s = _Session(**kw)
        monkeypatch.setattr(dem, "http_session", lambda: s)
        return s
    return _use


class TestFetch:
    def test_goes_through_the_app_session_with_a_timeout(self, session):
        s = session(res=_Res(body=_LIST))
        got = update_check.check("3.5")
        assert got is not None and got.version == "3.6"
        url, kw = s.calls[0]
        assert url == update_check.RELEASES_API and kw["timeout"] > 0

    @pytest.mark.parametrize("res", [
        _Res(429), _Res(403, headers={"X-RateLimit-Remaining": "0"})])
    def test_rate_limit(self, session, res):
        session(res=res)
        with pytest.raises(UpdateCheckError) as e:
            update_check.fetch_releases()
        assert e.value.kind == "rate_limited"

    @pytest.mark.parametrize("kw", [
        {"exc": requests.ConnectionError("proxy")}, {"exc": requests.Timeout("t")},
        {"res": _Res(503)}, {"res": _Res(403)}])
    def test_network(self, session, kw):
        session(**kw)
        with pytest.raises(UpdateCheckError) as e:
            update_check.fetch_releases()
        assert e.value.kind == "network"

    @pytest.mark.parametrize("res", [_Res(bad_json=True), _Res(body={"a": 1})])
    def test_bad_response(self, session, res):
        session(res=res)
        with pytest.raises(UpdateCheckError) as e:
            update_check.fetch_releases()
        assert e.value.kind == "bad_response"


# 割る前の 1 キー（`err_dem_unreachable`）の字そのもの＝組んだ字がこれと 1 字も
# 違わないこと（外部訳の規則で新しいキーへ移しただけ＝画面の字は変えない）。
_OLD_DEM_UNREACHABLE = {
    "en": ("Could not download terrain data (DEM). The run was stopped so that "
           "a flat 0 m terrain is not reported as a real result.\n\n"
           "Check your network connection, and set a proxy if your site requires "
           "one (Settings > Proxy Settings)."),
    "ja": ("地形データ（DEM）を取得できませんでした。標高 0m の平坦な地形を"
           "結果として出さないよう、実行を中止しました。\n\n"
           "ネットワーク接続を確認してください。社内ネットワークなどで"
           "プロキシが必要な場合は、設定 > プロキシ設定 で指定してください。"),
}


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_dem_unreachable_text_is_unchanged_by_the_split(lang):
    i18n.set_lang(lang)
    assert sim.dem_unreachable_message() == _OLD_DEM_UNREACHABLE[lang]


class TestMessages:
    _REL = Release("3.7", "3.7", f"{_PAGE}tag/3.7")

    @pytest.mark.parametrize("frozen,portable,key", [
        (True, False, "update_how_installer"),
        (True, True, "update_how_portable"),
        (False, False, "update_how_source")])
    def test_how_to_install_follows_the_distribution(self, frozen, portable, key):
        i18n.set_lang("ja")
        msg = launcher_menu.update_available_message(
            self._REL, frozen=frozen, portable=portable)
        assert i18n.t(key).format(tag="3.7") in msg
        assert version.APP_VERSION in msg and "3.7" in msg

    def test_rate_limit_says_wait(self):
        i18n.set_lang("ja")
        msg = launcher_menu.update_failure_message(UpdateCheckError("rate_limited"))
        assert i18n.t("fix_retry_later") in msg

    def test_network_says_check_the_proxy_and_keeps_the_detail(self):
        i18n.set_lang("ja")
        msg = launcher_menu.update_failure_message(
            UpdateCheckError("network", "ConnectionError: boom"))
        assert i18n.t("fix_network") in msg and "ConnectionError: boom" in msg

    def test_unexpected_says_retry_or_log(self):
        i18n.set_lang("ja")
        msg = launcher_menu.update_failure_message(KeyError("x"))
        assert i18n.t("fix_retry_or_log") in msg and "KeyError" in msg
