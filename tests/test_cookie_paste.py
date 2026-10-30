"""Pasting DevTools cookies: parser formats and the /session endpoint."""
from __future__ import annotations

import json

import pytest

from mfp_mcp import remote, server
from mfp_mcp.cookie_paste import CookiePasteError, parse_pasted_cookies

TOKEN = "t" * 32
SESSION = "__Secure-next-auth.session-token"
EXPECTED = {SESSION: "ey.J=abc==", "_ga": "GA1.2"}


@pytest.mark.parametrize("text", [
    f"{SESSION}=ey.J=abc==; _ga=GA1.2",
    f"Cookie: {SESSION}=ey.J=abc==; _ga=GA1.2",
    f"{SESSION}=ey.J=abc==;\n_ga=GA1.2\n",
    json.dumps(EXPECTED),
    json.dumps({"cookies": EXPECTED, "saved_at": "x"}),
    json.dumps([{"name": SESSION, "value": "ey.J=abc==", "domain": ".x"},
                {"name": "_ga", "value": "GA1.2"}]),
    f"{SESSION}\tey.J=abc==\twww.myfitnesspal.com\t/\n_ga\tGA1.2\t.myfitnesspal.com\t/",
])
def test_parses_every_supported_shape(text):
    assert parse_pasted_cookies(text) == EXPECTED


@pytest.mark.parametrize("text", ["", "   ", "{not json", "[1, 2]", "no equals sign here", "=novalue; x="])
def test_rejects_unusable_input(text):
    with pytest.raises(CookiePasteError):
        parse_pasted_cookies(text)


@pytest.fixture
def stored(cookies_file):
    return cookies_file


@pytest.fixture
def client(make_client):
    return make_client(TOKEN)


AUTH = {"Authorization": f"Bearer {TOKEN}"}
GOOD = f"{SESSION}=abc; _ga=1"


def test_session_requires_auth(client, stored):
    assert client.get("/session").status_code == 401
    assert client.post("/session", content=GOOD).status_code == 401
    assert not stored.exists()


def test_session_page_served_without_caching(client):
    r = client.get(f"/session?token={TOKEN}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert TOKEN not in r.text  # the page never reflects the secret back


def test_valid_paste_is_verified_then_saved(client, stored, monkeypatch):
    seen = {}
    monkeypatch.setattr(remote, "verify_session", lambda c: seen.update(c))
    r = client.post("/session", content=GOOD, headers=AUTH)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert seen == {SESSION: "abc", "_ga": "1"}
    assert json.loads(stored.read_text())["cookies"] == seen
    assert "abc" not in r.text  # values are never echoed


def test_rejected_by_mfp_is_not_saved(client, stored, monkeypatch):
    def boom(_):
        raise RuntimeError("403")

    monkeypatch.setattr(remote, "verify_session", boom)
    r = client.post("/session", content=GOOD, headers=AUTH)
    assert r.status_code == 400 and "Nothing was saved" in r.json()["message"]
    assert not stored.exists()


def test_paste_without_login_cookie_is_rejected(client, stored, monkeypatch):
    monkeypatch.setattr(remote, "verify_session", lambda c: pytest.fail("must not verify"))
    r = client.post("/session", content="_ga=1; foo=bar", headers=AUTH)
    assert r.status_code == 400 and "session-token" in r.json()["message"]
    assert not stored.exists()


def test_garbage_and_oversized_pastes(client, stored):
    assert client.post("/session", content="{oops", headers=AUTH).status_code == 400
    big = "a=" + "x" * (remote.MAX_PASTE_BYTES + 1)
    assert client.post("/session", content=big, headers=AUTH).status_code == 413
    assert not stored.exists()


def test_other_methods_not_allowed(client):
    assert client.put("/session", content="x", headers=AUTH).status_code == 405


def test_mcp_endpoint_still_reachable(client):
    r = client.post("/mcp", headers={**AUTH, "Accept": "application/json, text/event-stream",
                                     "Content-Type": "application/json"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}})
    assert r.status_code == 200


def test_session_page_has_a_locked_down_csp(client):
    csp = client.get(f"/session?token={TOKEN}").headers["content-security-policy"]
    assert "default-src 'none'" in csp and "form-action 'none'" in csp


def test_trailing_slash_is_the_same_endpoint(client):
    assert client.get("/session/").status_code == 401
    assert client.get(f"/session/?token={TOKEN}").status_code == 200


def test_save_failure_is_reported_as_json_not_a_bare_500(client, stored, monkeypatch):
    monkeypatch.setattr(remote, "verify_session", lambda c: None)

    def boom(_):
        raise PermissionError("read-only")

    monkeypatch.setattr(server, "save_cookies", boom)
    r = client.post("/session", content=GOOD, headers=AUTH)
    assert r.status_code == 500 and r.json()["ok"] is False
    assert "could not be saved" in r.json()["message"]


def test_session_is_disabled_without_a_token(stored, monkeypatch, make_client):
    """Tokenless mode must not expose a login-CSRF-able write endpoint."""
    monkeypatch.setattr(remote, "verify_session", lambda c: pytest.fail("must not verify"))
    c = make_client(token=None)
    assert c.get("/session").status_code == 404
    assert c.post("/session", content=GOOD).status_code == 404
    assert not stored.exists()
    assert c.get("/healthz").status_code == 200


def test_websocket_scopes_are_refused_not_passed_through():
    import asyncio

    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    sent = []

    async def send(message):
        sent.append(message)

    mw = remote.BearerAuthMiddleware(inner, TOKEN)
    asyncio.run(mw({"type": "websocket", "path": "/mcp", "headers": []}, None, send))
    assert reached == [] and sent == [{"type": "websocket.close", "code": 1008}]
    asyncio.run(mw({"type": "lifespan"}, None, send))
    assert reached == ["lifespan"]


# --------------------------------------------------------------------------
# Robustness: generated round trips and messy real-world pastes
# --------------------------------------------------------------------------
import random  # noqa: E402
import string  # noqa: E402

_NAME_CHARS = string.ascii_letters + string.digits + "-_."
_VALUE_CHARS = string.ascii_letters + string.digits + "-_.=+/%~"  # JWT/base64/url-ish


def _random_cookies(rng, n):
    names = set()
    while len(names) < n:
        names.add("".join(rng.choices(_NAME_CHARS, k=rng.randint(1, 24))))
    return {
        name: "".join(rng.choices(_VALUE_CHARS, k=rng.randint(1, 900)))
        for name in sorted(names)
    }


def _render(cookies, fmt):
    if fmt == "header":
        return "; ".join(f"{k}={v}" for k, v in cookies.items())
    if fmt == "header-prefixed":
        return "Cookie: " + "; ".join(f"{k}={v}" for k, v in cookies.items())
    if fmt == "header-lines":
        return "\n".join(f"{k}={v};" for k, v in cookies.items())
    if fmt == "json-object":
        return json.dumps(cookies)
    if fmt == "json-wrapped":
        return json.dumps({"cookies": cookies, "saved_at": "2026-01-01"})
    if fmt == "json-array":
        return json.dumps([{"name": k, "value": v, "domain": ".x", "httpOnly": True}
                           for k, v in cookies.items()])
    if fmt == "devtools-table":
        return "\n".join(f"{k}\t{v}\twww.myfitnesspal.com\t/\t2026-11-29T14:24:22.000Z\t512\t✓\t✓"
                         for k, v in cookies.items())
    raise AssertionError(fmt)


FORMATS = ["header", "header-prefixed", "header-lines", "json-object",
           "json-wrapped", "json-array", "devtools-table"]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("seed", range(25))
def test_generated_cookies_round_trip_in_every_format(fmt, seed):
    rng = random.Random(f"{fmt}-{seed}")
    cookies = _random_cookies(rng, rng.randint(1, 40))
    assert parse_pasted_cookies(_render(cookies, fmt)) == cookies


@pytest.mark.parametrize("fmt", ["header", "devtools-table", "header-lines"])
def test_windows_line_endings_and_surrounding_whitespace(fmt):
    cookies = {"a": "1", "b": "x=y"}
    pasted = "\r\n\r\n   " + _render(cookies, fmt).replace("\n", "\r\n") + "  \r\n\r\n"
    assert parse_pasted_cookies(pasted) == cookies


def test_pasting_a_whole_request_headers_block_extracts_only_the_cookie_line():
    block = (
        "GET /api/auth/session HTTP/2\n"
        "accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8\n"
        "accept-language: en-US,en;q=0.9\n"
        "cookie: __Secure-next-auth.session-token=abc; _ga=GA1.2\n"
        "user-agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)\n"
    )
    assert parse_pasted_cookies(block) == {"__Secure-next-auth.session-token": "abc", "_ga": "GA1.2"}


@pytest.mark.parametrize("curl", [
    "curl 'https://www.myfitnesspal.com/api/auth/session' -H 'accept: */*' "
    "-b '__Secure-next-auth.session-token=abc; _ga=GA1.2' -H 'user-agent: x'",
    "curl 'https://www.myfitnesspal.com/' -H 'cookie: __Secure-next-auth.session-token=abc; _ga=GA1.2'",
    'curl "https://www.myfitnesspal.com/" --cookie "__Secure-next-auth.session-token=abc; _ga=GA1.2"',
])
def test_copy_as_curl_is_understood(curl):
    assert parse_pasted_cookies(curl) == {"__Secure-next-auth.session-token": "abc", "_ga": "GA1.2"}


def test_json_values_that_are_not_strings():
    pasted = json.dumps({"a": "1", "n": 5, "t": True, "nothing": None, "nested": {"x": 1}, "l": [1]})
    assert parse_pasted_cookies(pasted) == {"a": "1", "n": "5", "t": "True"}


def test_json_array_entries_missing_fields_are_skipped():
    pasted = json.dumps([{"name": "a", "value": "1"}, {"name": "no-value"}, {"value": "no-name"}, {}])
    assert parse_pasted_cookies(pasted) == {"a": "1"}


def test_quoted_values_lose_their_quotes_and_later_duplicates_win():
    assert parse_pasted_cookies('a="1"; b=2; a=3') == {"a": "3", "b": "2"}


def test_byte_order_mark_and_non_breaking_spaces_are_tolerated():
    assert parse_pasted_cookies("﻿a=1; b=2") == {"a": "1", "b": "2"}


def test_too_many_cookies_is_rejected_not_truncated():
    pasted = "; ".join(f"c{i}=v" for i in range(201))
    with pytest.raises(CookiePasteError, match="Too many"):
        parse_pasted_cookies(pasted)


@pytest.mark.parametrize("junk", [
    "\x00\x01\x02", "💥" * 50, "=" * 100, ";;;;;;", "a" * 100_000, "{" * 1000, "\t\t\t",
])
def test_junk_never_raises_anything_but_cookiepasteerror(junk):
    try:
        result = parse_pasted_cookies(junk)
    except CookiePasteError:
        return
    assert isinstance(result, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in result.items())


def test_random_garbage_fuzz_never_crashes():
    rng = random.Random(1234)
    alphabet = string.printable + "雙💥 ﻿"
    for _ in range(500):
        text = "".join(rng.choices(alphabet, k=rng.randint(0, 300)))
        try:
            parse_pasted_cookies(text)
        except CookiePasteError:
            pass
