"""更新の確認（I-178 段階 1・I-179 起動時の確認）と、その回に取り出した次の一手 `fix_network` の検査。"""

import datetime
from typing import Any, cast

import pytest
import requests

from core import config, i18n, simulation as sim, update_check, version
from core.update_check import Release, UpdateCheckError, pick_newer
from views import launcher_menu

_PAGE = update_check.RELEASE_PAGE_PREFIX


def _rel(tag, *, pre=False, draft=False, url=None):
    return {"tag_name": tag, "prerelease": pre, "draft": draft,
            "html_url": url or f"{_PAGE}tag/{tag}"}


# 実際の応答の形（2026-09-26 に確かめた）＝正式は `3.6`・RC は `v3.6RC2`。
_LIST = [_rel("3.6"), _rel("v3.6RC2", pre=True), _rel("v3.6RC1", pre=True),
         _rel("3.5"), _rel("v3.5RC1", pre=True)]


def _newer(rels, current, include_pre=None) -> Release:
    got = pick_newer(rels, current, include_pre)
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

    # I-180＝利用者が選んだら、いまの版より選択が勝つ。
    def test_final_user_who_opts_in_is_told_of_a_release_candidate(self):
        rels = [_rel("v3.7RC1", pre=True), *_LIST]
        assert _newer(rels, "3.6", include_pre=True).version == "3.7RC1"

    def test_rc_user_who_opts_out_hears_only_of_finals(self):
        rels = [_rel("v3.7RC2", pre=True), *_LIST]
        assert pick_newer(rels, "3.7RC1", include_pre=False) is None
        assert _newer([_rel("3.7"), *rels], "3.7RC1", include_pre=False).version == "3.7"

    def test_opting_out_also_drops_an_rc_tag_without_the_flag(self):
        assert _newer([_rel("v3.7RC1"), *_LIST], "3.6RC1",
                      include_pre=False).version == "3.6"


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


class _Root:
    def __init__(self):
        self.cursor = ""

    def configure(self, cursor=""):
        self.cursor = cursor


class _Host(launcher_menu._MenuMixin):
    """`SimLauncher` のうち更新の確認が使う面だけ。"""
    saved: list
    asked: list        # `check` に渡った `include_pre`（1 回の問い合わせに 1 つ）

    def __init__(self, conf=None):
        self.root = cast(Any, _Root())
        self.config = dict(config.DEFAULT_CONFIG, **(conf or {}))
        self.alerts, self.confirms = [], []

    def _alert(self, title, message):
        self.alerts.append(message)

    def _confirm(self, title, message):
        self.confirms.append(message)
        return False


class _Thread:
    """`start()` で走らせず、`run_all()` で答えを返させる（問い合わせ中を作る）。"""
    started: list = []

    def __init__(self, target, **_kw):
        self.target = target

    def start(self):
        _Thread.started.append(self.target)

    @classmethod
    def run_all(cls):
        while cls.started:
            cls.started.pop(0)()


@pytest.fixture
def host(monkeypatch):
    _Thread.started = []
    monkeypatch.setattr(launcher_menu.threading, "Thread", _Thread)
    monkeypatch.setattr(launcher_menu.progress, "post_to_ui",
                        lambda _root, fn: fn())
    saved = []
    monkeypatch.setattr(launcher_menu.config, "save_app",
                        lambda values: saved.append(dict(values)) or True)
    i18n.set_lang("ja")

    def _make(answer, **conf):
        def _check(current=version.APP_VERSION, include_pre=None):
            h.asked.append(include_pre)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        monkeypatch.setattr(update_check, "check", _check)
        h = _Host(conf)
        h.saved = saved
        h.asked = []
        return h
    return _make


_NEW = Release("9.0", "9.0", f"{_PAGE}tag/9.0")


class TestStartupCheck:
    def test_off_by_default_never_asks(self, host):
        h = host(_NEW)
        h._auto_check_updates()
        assert _Thread.started == [] and h.saved == []

    def test_records_the_day_before_asking_and_only_once(self, host):
        h = host(None, update_check_auto="on")
        h._auto_check_updates()
        assert h.saved[-1]["update_check_last"] == datetime.date.today().isoformat()
        assert len(_Thread.started) == 1
        _Thread.run_all()
        h._auto_check_updates()                   # 同じ日の 2 度目の起動
        assert _Thread.started == [] and len(h.saved) == 1

    @pytest.mark.parametrize("answer", [
        None, UpdateCheckError("network", "boom"), UpdateCheckError("rate_limited"),
        KeyError("x")])
    def test_latest_and_failures_stay_silent(self, host, answer):
        h = host(answer, update_check_auto="on")
        h._auto_check_updates()
        _Thread.run_all()
        assert h.alerts == [] and h.confirms == [] and h.root.cursor == ""

    def test_newer_version_asks_and_says_how_to_stop(self, host):
        h = host(_NEW, update_check_auto="on")
        h._auto_check_updates()
        assert h.root.cursor == ""                # 起動時はカーソルを変えない
        _Thread.run_all()
        (msg,) = h.confirms
        assert i18n.t("update_auto_note") in msg
        assert msg.endswith(i18n.t("dlg_update_available").rsplit("\n\n", 1)[1])

    def test_manual_press_while_startup_check_runs_shows_the_answer(self, host):
        h = host(None, update_check_auto="on")
        h._auto_check_updates()
        h._on_check_updates()                     # 答えが来る前に利用者が押す
        assert len(_Thread.started) == 1 and h.root.cursor == "watch"
        _Thread.run_all()
        assert len(h.alerts) == 1 and h.root.cursor == ""

    def test_manual_check_has_no_startup_note(self, host):
        h = host(_NEW)
        h._on_check_updates()
        _Thread.run_all()
        (msg,) = h.confirms
        assert i18n.t("update_auto_note") not in msg

    def test_toggle_saves_the_choice(self, host):
        h = host(None)
        h._update_auto_var = type("V", (), {"get": lambda self: "on"})()
        h._on_update_auto_toggle()
        assert h.saved[-1]["update_check_auto"] == "on"


class TestPrereleaseChoice:
    """プレリリースも知らせるか（I-180）が、手動と起動時の両方の問い合わせへ届くこと。"""

    @pytest.mark.parametrize("choice,expected", [
        ("on", True), ("off", False),
        ("", not version.is_final(version.APP_VERSION))])
    def test_manual_check_passes_the_choice(self, host, choice, expected):
        h = host(None, update_check_prerelease=choice)
        h._on_check_updates()
        _Thread.run_all()
        assert h.asked == [expected]

    @pytest.mark.parametrize("choice,expected", [("on", True), ("off", False)])
    def test_startup_check_passes_the_choice(self, host, choice, expected):
        h = host(None, update_check_auto="on", update_check_prerelease=choice)
        h._auto_check_updates()
        _Thread.run_all()
        assert h.asked == [expected]

    @pytest.mark.parametrize("value", ["on", "off"])
    def test_toggle_fixes_the_choice(self, host, value):
        h = host(None)
        h._update_pre_var = type("V", (), {"get": lambda self: value})()
        h._on_update_pre_toggle()
        assert h.saved[-1]["update_check_prerelease"] == value
