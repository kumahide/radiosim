"""更新の確認（I-178 段階 1・I-179 起動時の確認）と、その回に取り出した次の一手 `fix_network` の検査。"""

import datetime

import pytest
import requests

from core import config, i18n, simulation as sim, update_check
from core.update_check import Release, UpdateCheckError, pick_newer

_PAGE = update_check.RELEASE_PAGE_PREFIX


def _rel(tag, *, pre=False, draft=False, url=None):
    return {"tag_name": tag, "prerelease": pre, "draft": draft,
            "html_url": url or f"{_PAGE}tag/{tag}"}


# Link のタグ（判断点 ⑯）＝「製品名の小文字-版の字」・`v` なし。
_LIST = [_rel("link-1.1"), _rel("link-1.1RC2", pre=True), _rel("link-1.1RC1", pre=True),
         _rel("link-1.0"), _rel("link-1.0RC1", pre=True)]

# 同じリポジトリに並ぶ他の製品のタグ（Pro 3.9.x の実際の形＝正式は `3.9.1`・RC は
# `v3.9RC1`）。接頭辞を外すと Link 1.x より新しく読める。
_OTHERS = [_rel("3.9.1"), _rel("v3.9.1RC1", pre=True), _rel("v9.0"),
           _rel("field-2.0"), _rel("drone-1.5"), _rel("Link-9.0"), _rel("link9.0")]


def _newer(rels, current, include_pre=None) -> Release:
    got = pick_newer(rels, current, include_pre)
    assert got is not None
    return got


class TestPickNewer:
    def test_final_user_is_told_of_a_newer_final(self):
        got = pick_newer(_LIST, "1.0")
        assert got == Release("1.1", "link-1.1", f"{_PAGE}tag/link-1.1")

    def test_final_user_is_not_told_of_a_release_candidate(self):
        assert pick_newer([_rel("link-1.2RC1", pre=True), *_LIST], "1.1") is None

    def test_rc_tag_without_the_prerelease_flag_is_still_a_prerelease(self):
        assert pick_newer([_rel("link-1.2RC1"), *_LIST], "1.1") is None

    def test_rc_user_is_told_of_a_newer_rc(self):
        assert _newer(_LIST, "1.1RC1").version == "1.1"   # 正式が RC より上

    def test_rc_user_gets_the_largest_candidate(self):
        got = _newer([_rel("link-1.2RC2", pre=True), _rel("link-1.2RC1", pre=True),
                      *_LIST], "1.1RC2")
        assert got.version == "1.2RC2" and got.tag == "link-1.2RC2"

    def test_alpha_counts_as_a_prerelease_user(self):
        assert _newer([_rel("link-1.2RC1", pre=True)], "1.2a1").version == "1.2RC1"

    def test_up_to_date_is_none(self):
        assert pick_newer(_LIST, "1.1") is None
        assert pick_newer(_LIST, "1.2a1") is None

    def test_drafts_unreadable_tags_and_foreign_urls_are_dropped(self):
        rels = [_rel("link-9.0", draft=True), _rel("link-nightly"),
                _rel("link-9.1", url="https://example.com/evil"), "junk", {}]
        assert pick_newer(rels, "1.1") is None

    # 判断点 ⑯＝同じリポジトリの他の製品のタグでは、どの版のどの選択でも知らせない。
    @pytest.mark.parametrize("current", ["1.0a1", "1.0", "1.1RC1"])
    @pytest.mark.parametrize("include_pre", [None, True, False])
    def test_other_products_tags_are_never_told(self, current, include_pre):
        assert pick_newer(_OTHERS, current, include_pre) is None

    def test_link_is_found_among_other_products_tags(self):
        assert _newer([*_OTHERS, *_LIST], "1.0").tag == "link-1.1"

    def test_the_query_asks_for_the_most_releases_one_page_holds(self):
        assert update_check.RELEASES_API.endswith("per_page=100")

    # I-180＝利用者が選んだら、いまの版より選択が勝つ。
    def test_final_user_who_opts_in_is_told_of_a_release_candidate(self):
        rels = [_rel("link-1.2RC1", pre=True), *_LIST]
        assert _newer(rels, "1.1", include_pre=True).version == "1.2RC1"

    def test_rc_user_who_opts_out_hears_only_of_finals(self):
        rels = [_rel("link-1.2RC2", pre=True), *_LIST]
        assert pick_newer(rels, "1.2RC1", include_pre=False) is None
        assert _newer([_rel("link-1.2"), *rels], "1.2RC1",
                      include_pre=False).version == "1.2"

    def test_opting_out_also_drops_an_rc_tag_without_the_flag(self):
        assert _newer([_rel("link-1.2RC1"), *_LIST], "1.1RC1",
                      include_pre=False).version == "1.1"


class TestWantPrerelease:
    """プレリリースも知らせるか（I-180）＝触るまではいまの版で決まる。"""

    def test_default_is_untouched_and_a_setting_of_the_app(self):
        assert config.DEFAULT_CONFIG["update_check_prerelease"] == ""
        assert "update_check_prerelease" in config.APP_KEYS
        assert "update_check_prerelease" not in config.SIM_KEYS

    @pytest.mark.parametrize("current,expected", [
        ("3.6", False), ("3.7RC1", True), ("3.7a1", True), ("3.7b1", True)])
    def test_untouched_follows_the_current_version(self, current, expected):
        assert update_check.want_prerelease(
            dict(config.DEFAULT_CONFIG), current) is expected

    @pytest.mark.parametrize("current", ["3.6", "3.7RC1"])
    def test_a_choice_wins_over_the_current_version(self, current):
        assert update_check.want_prerelease(
            {"update_check_prerelease": "on"}, current) is True
        assert update_check.want_prerelease(
            {"update_check_prerelease": "off"}, current) is False

    @pytest.mark.parametrize("value", [None, True, "On", "yes", "1"])
    def test_anything_but_on_or_off_follows_the_version(self, value):
        conf = {"update_check_prerelease": value}
        assert update_check.want_prerelease(conf, "3.6") is False
        assert update_check.want_prerelease(conf, "3.7RC1") is True


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
        got = update_check.check("1.0")
        assert got is not None and got.version == "1.1"
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


class TestAutoDue:
    """起動時の確認（I-179）を今日打つか＝既定オフ・1 日 1 回まで。"""
    _TODAY = datetime.date(2026, 9, 26)

    def test_default_config_is_off(self):
        assert config.DEFAULT_CONFIG["update_check_auto"] == "off"
        assert not update_check.auto_due(dict(config.DEFAULT_CONFIG), self._TODAY)

    def test_keys_are_app_settings(self):
        # 設定ファイルの app 側＝プロジェクトやパラメータ読込に混ざらない。
        for key in ("update_check_auto", "update_check_last"):
            assert key in config.APP_KEYS and key not in config.SIM_KEYS

    def test_on_and_not_yet_today(self):
        assert update_check.auto_due(
            {"update_check_auto": "on", "update_check_last": ""}, self._TODAY)
        assert update_check.auto_due(
            {"update_check_auto": "on", "update_check_last": "2026-09-25"}, self._TODAY)

    def test_once_a_day(self):
        assert not update_check.auto_due(
            {"update_check_auto": "on", "update_check_last": "2026-09-26"}, self._TODAY)

    def test_clock_turned_back_still_checks(self):
        assert update_check.auto_due(
            {"update_check_auto": "on", "update_check_last": "2026-12-31"}, self._TODAY)

    @pytest.mark.parametrize("value", [True, "true", "On", "1", None])
    def test_anything_but_on_is_off(self, value):
        assert not update_check.auto_due(
            {"update_check_auto": value, "update_check_last": ""}, self._TODAY)
