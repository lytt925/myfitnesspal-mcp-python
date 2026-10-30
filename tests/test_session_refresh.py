"""Tests for keeping the MFP session alive via NextAuth's /api/auth/session."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import pytest

from mfp_mcp import server

OLD = {"__Secure-next-auth.session-token": "old", "other": "keep"}


class _Headers:
    def __init__(self, set_cookies):
        self._set_cookies = set_cookies

    def getlist(self, name):
        assert name == "Set-Cookie"
        return self._set_cookies


class _Resp:
    def __init__(self, body, set_cookies=()):
        self._body = body
        self.raw = type("Raw", (), {"headers": _Headers(list(set_cookies))})()

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _http_date(delta):
    return format_datetime(datetime.now(timezone.utc) + delta, usegmt=True)


@pytest.fixture
def cookie_file(cookies_file):
    cookies_file.parent.mkdir(parents=True, exist_ok=True)
    return cookies_file


def _write(path, saved_at):
    path.write_text(json.dumps({"cookies": OLD, "saved_at": saved_at.isoformat()}))


def _patch_get(monkeypatch, resp):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return resp

    monkeypatch.setattr("requests.get", fake_get)
    return calls


def test_fetch_returns_reissued_cookie(monkeypatch):
    cookie = f"__Secure-next-auth.session-token=new; Path=/; Expires={_http_date(timedelta(days=30))}; HttpOnly"
    _patch_get(monkeypatch, _Resp({"user": {}}, [cookie]))
    assert server._fetch_refreshed_session(OLD) == {"__Secure-next-auth.session-token": "new"}


def test_fetch_none_when_no_active_session(monkeypatch):
    _patch_get(monkeypatch, _Resp({}, ["__Secure-next-auth.session-token=new; Path=/"]))
    assert server._fetch_refreshed_session(OLD) is None


def test_fetch_ignores_cleared_and_expired_cookies(monkeypatch):
    past = _http_date(timedelta(days=-1))
    headers = [
        f"__Secure-next-auth.session-token=; Path=/; Expires={past}",
        f"stale=x; Path=/; Expires={past}",
    ]
    _patch_get(monkeypatch, _Resp({"user": {}}, headers))
    assert server._fetch_refreshed_session(OLD) == {}


def test_refresh_persists_merged_cookies(cookie_file, monkeypatch):
    _write(cookie_file, datetime.now() - timedelta(days=2))
    _patch_get(monkeypatch, _Resp({"user": {}}, ["__Secure-next-auth.session-token=new; Path=/"]))
    merged = server.maybe_refresh_session(OLD)
    assert merged == {"__Secure-next-auth.session-token": "new", "other": "keep"}
    saved = json.loads(cookie_file.read_text())
    assert saved["cookies"] == merged
    assert datetime.now() - datetime.fromisoformat(saved["saved_at"]) < timedelta(minutes=1)


def test_refresh_is_throttled_by_file_age(cookie_file, monkeypatch):
    _write(cookie_file, datetime.now() - timedelta(hours=1))
    calls = _patch_get(monkeypatch, _Resp({"user": {}}, []))
    assert server.maybe_refresh_session(OLD) == OLD
    assert calls == []


def test_force_ignores_throttle(cookie_file, monkeypatch):
    _write(cookie_file, datetime.now())
    calls = _patch_get(monkeypatch, _Resp({"user": {}}, ["a=b; Path=/"]))
    server.maybe_refresh_session(OLD, force=True)
    assert len(calls) == 1


def test_refresh_without_cookie_file_runs(cookie_file, monkeypatch):
    calls = _patch_get(monkeypatch, _Resp({"user": {}}, ["a=b; Path=/"]))
    merged = server.maybe_refresh_session(OLD)
    assert len(calls) == 1 and merged["a"] == "b" and cookie_file.exists()


def test_dead_session_never_overwrites_file(cookie_file, monkeypatch):
    _write(cookie_file, datetime.now() - timedelta(days=2))
    before = cookie_file.read_text()
    _patch_get(monkeypatch, _Resp({}, ["__Secure-next-auth.session-token=junk; Path=/"]))
    assert server.maybe_refresh_session(OLD) == OLD
    assert cookie_file.read_text() == before


def test_network_failure_is_swallowed(cookie_file, monkeypatch):
    _write(cookie_file, datetime.now() - timedelta(days=2))

    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr("requests.get", boom)
    assert server.maybe_refresh_session(OLD) == OLD


def test_empty_result_does_not_retry_on_every_call(cookie_file, monkeypatch):
    """A live session with no usable Set-Cookie saves nothing; don't re-ask each call."""
    _write(cookie_file, datetime.now() - timedelta(days=2))
    calls = _patch_get(monkeypatch, _Resp({"user": {}}, []))
    for _ in range(3):
        server.maybe_refresh_session(OLD)
        server.maybe_refresh_session(OLD, force=True)
    assert len(calls) == 1


def test_failures_are_throttled_too(cookie_file, monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise ConnectionError("down")

    monkeypatch.setattr("requests.get", boom)
    for _ in range(3):
        server.maybe_refresh_session(OLD, force=True)
    assert len(calls) == 1


def test_attempt_allowed_again_after_the_interval(cookie_file, monkeypatch):
    calls = _patch_get(monkeypatch, _Resp({"user": {}}, []))
    server.maybe_refresh_session(OLD, force=True)
    monkeypatch.setattr(server, "_last_refresh_attempt",
                        server._last_refresh_attempt - 13 * 3600)
    server.maybe_refresh_session(OLD, force=True)
    assert len(calls) == 2


def test_save_cookies_is_private_atomic_and_leaves_no_temp_files(cookie_file):
    server.save_cookies(OLD)
    assert (cookie_file.stat().st_mode & 0o777) == 0o600
    assert json.loads(cookie_file.read_text())["cookies"] == OLD
    assert [p.name for p in cookie_file.parent.iterdir()] == ["cookies.json"]


def test_failed_save_keeps_old_file_and_cleans_up(cookie_file, monkeypatch):
    server.save_cookies(OLD)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(server.os, "replace", boom)
    with pytest.raises(OSError):
        server.save_cookies({"x": "y"})
    assert json.loads(cookie_file.read_text())["cookies"] == OLD
    assert [p.name for p in cookie_file.parent.iterdir()] == ["cookies.json"]


def test_existing_custom_config_dir_is_not_chmodded(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    monkeypatch.setattr(server, "CONFIG_DIR", shared)
    monkeypatch.setenv("MFP_CONFIG_DIR", str(shared))
    server.ensure_config_dir()
    assert (shared.stat().st_mode & 0o777) == 0o755


# --------------------------------------------------------------------------
# Robustness: odd Set-Cookie headers, error responses, concurrency
# --------------------------------------------------------------------------
import threading  # noqa: E402

# Shape of a NextAuth session token: a JWE with an empty key segment ("dir" alg).
JWE = ("eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..2QUbnKDy1qlYpdDf."
       "JCNygnrd5QT7WLFmt76-tIWgd6cclPOHKi9h6OghWRgvCT1Niev9FgBKHNL8Kq_yMg.wG9U_AP1-zxGeSA1RYnX7g==")


def fetch_with(monkeypatch, *set_cookies, body=None):
    _patch_get(monkeypatch, _Resp({"user": {}} if body is None else body, set_cookies))
    return server._fetch_refreshed_session(OLD)


def test_realistic_session_token_survives_verbatim(monkeypatch):
    header = f"__Secure-next-auth.session-token={JWE}; Path=/; Expires={_http_date(timedelta(days=30))}; HttpOnly; Secure; SameSite=Lax"
    assert fetch_with(monkeypatch, header) == {"__Secure-next-auth.session-token": JWE}


@pytest.mark.parametrize("value", ["a=b==", "x.y-z_w", "%7B%22a%22%3A1%7D", "A" * 4000])
def test_values_with_padding_percent_escapes_and_length_survive(monkeypatch, value):
    assert fetch_with(monkeypatch, f"c={value}; Path=/") == {"c": value}


def test_the_last_of_two_set_cookies_for_one_name_wins(monkeypatch):
    got = fetch_with(monkeypatch, "c=first; Path=/", "c=second; Path=/")
    assert got == {"c": "second"}


def test_cookies_not_mentioned_by_the_response_are_left_alone(cookie_file, monkeypatch):
    _write(cookie_file, datetime.now() - timedelta(days=2))
    _patch_get(monkeypatch, _Resp({"user": {}}, ["brand-new=1; Path=/"]))
    merged = server.maybe_refresh_session(OLD)
    assert merged == {**OLD, "brand-new": "1"}


@pytest.mark.parametrize("attr", ["Max-Age=0", "Max-Age=-1", "expires=Thu, 01 Jan 1970 00:00:00 GMT"])
def test_cookies_being_deleted_are_never_stored(monkeypatch, attr):
    assert fetch_with(monkeypatch, f"gone=still-has-a-value; Path=/; {attr}") == {}


def test_positive_max_age_is_kept(monkeypatch):
    assert fetch_with(monkeypatch, "kept=1; Path=/; Max-Age=2592000") == {"kept": "1"}


@pytest.mark.parametrize("expires", ["Expires=not a date", "Expires=", "Expires=Sun, 99 Foo 2026 25:61:61 GMT"])
def test_an_unparseable_expiry_does_not_discard_the_cookie(monkeypatch, expires):
    assert fetch_with(monkeypatch, f"c=1; Path=/; {expires}") == {"c": "1"}


def test_a_timezone_less_expiry_date_is_handled(monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%a, %d %b %Y %H:%M:%S -0000")
    assert fetch_with(monkeypatch, f"c=1; Path=/; Expires={future}") == {"c": "1"}


def test_an_empty_header_list_is_not_a_failure(monkeypatch):
    assert fetch_with(monkeypatch) == {}


class _Failing(_Resp):
    def __init__(self, status=200, body_error=None):
        super().__init__({})
        self.status, self.body_error = status, body_error

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        if self.body_error:
            raise self.body_error
        return {}


@pytest.mark.parametrize("resp", [
    _Failing(403), _Failing(500), _Failing(429),
    _Failing(200, body_error=ValueError("<html>Just a moment...</html>")),  # Cloudflare challenge page
])
def test_error_responses_never_touch_the_stored_session(cookie_file, monkeypatch, resp):
    _write(cookie_file, datetime.now() - timedelta(days=2))
    before = cookie_file.read_text()
    _patch_get(monkeypatch, resp)
    assert server.maybe_refresh_session(OLD) == OLD
    assert cookie_file.read_text() == before


def test_request_goes_to_the_session_endpoint_with_a_timeout(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen.update(url=url, **kw)
        return _Resp({"user": {}}, [])

    monkeypatch.setattr("requests.get", fake_get)
    server._fetch_refreshed_session(OLD)
    assert seen["url"] == "https://www.myfitnesspal.com/api/auth/session"
    assert seen["cookies"] == OLD and 0 < seen["timeout"] <= 30  # never hangs a tool call


def test_the_log_never_contains_cookie_values(cookie_file, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="mfp_mcp")
    _write(cookie_file, datetime.now() - timedelta(days=2))
    header = f"__Secure-next-auth.session-token={JWE}; Path=/; Expires={_http_date(timedelta(days=30))}"
    _patch_get(monkeypatch, _Resp({"user": {}}, [header]))
    server.maybe_refresh_session(OLD)
    assert "re-issued" in caplog.text
    assert JWE not in caplog.text


def test_readers_never_see_a_partial_file_while_writers_race(cookie_file):
    """Atomic replace: a concurrent load_cookies() is always complete JSON or the previous one."""
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    server.save_cookies({"gen": "0", "pad": "x" * 50_000})
    stop = threading.Event()
    seen_bad = []

    def reader():
        while not stop.is_set():
            got = server.load_cookies()
            if got is None or "gen" not in got or len(got["pad"]) != 50_000:
                seen_bad.append(got and list(got))

    def writer(n):
        for i in range(60):
            server.save_cookies({"gen": f"{n}-{i}", "pad": "x" * 50_000})

    readers = [threading.Thread(target=reader) for _ in range(3)]
    writers = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in readers + writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()
    assert seen_bad == []
    assert [p.name for p in cookie_file.parent.iterdir()] == ["cookies.json"]  # no temp litter


def test_stale_stored_session_is_refreshed_on_the_way_through_get_mfp_client(cookie_file, monkeypatch):
    """End to end: stored cookies work -> they are re-issued -> the file holds the new token."""
    _write(cookie_file, datetime.now() - timedelta(days=2))
    monkeypatch.setattr(server, "_client_from_cookies", lambda cookies: ("client", dict(cookies)))
    _patch_get(monkeypatch, _Resp({"user": {}}, ["__Secure-next-auth.session-token=reissued; Path=/"]))
    assert server.get_mfp_client()[0] == "client"
    saved = json.loads(cookie_file.read_text())["cookies"]
    assert saved["__Secure-next-auth.session-token"] == "reissued" and saved["other"] == "keep"


@pytest.mark.parametrize("header,expected", [
    ("a=1; Path=/; HttpOnly", ("a", "1", {"path": "/", "httponly": ""})),
    ("  a = 1 ;Path=/", ("a", "1", {"path": "/"})),
    ('a="quoted"; Secure', ("a", "quoted", {"secure": ""})),
    ("a=b=c==; SameSite=Lax", ("a", "b=c==", {"samesite": "Lax"})),
    ("a=; Max-Age=0", ("a", "", {"max-age": "0"})),
    ("a=1; Expires=Sun, 29 Nov 2026 14:24:22 GMT", ("a", "1", {"expires": "Sun, 29 Nov 2026 14:24:22 GMT"})),
    ("a=1; EXPIRES=whenever; MAX-AGE=5", ("a", "1", {"expires": "whenever", "max-age": "5"})),
])
def test_set_cookie_parsing(header, expected):
    assert server._parse_set_cookie(header) == expected


@pytest.mark.parametrize("header", ["", "no-equals-sign", "=value", "  =  ; Path=/", ";"])
def test_malformed_set_cookie_headers_are_ignored(header):
    assert server._parse_set_cookie(header) is None
