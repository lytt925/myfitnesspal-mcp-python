"""The bearer gate is the only access control: try to get around it."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

import pytest

from mfp_mcp import remote

TOKEN = "Zk3-_Qw9" * 4
INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "t", "version": "0"}},
}
MCP = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def client(make_client):
    return make_client(TOKEN)


def post_init(client, url="/mcp", headers=None):
    return client.post(url, json=INIT, headers={**MCP, **(headers or {})})


@pytest.mark.parametrize("path", [
    "/", "/mcp", "/mcp/", "/MCP", "/session", "/session/", "//session", "/SESSION",
    "/session%2F", "/session%2f..%2fmcp", "/mcp/../session", "/healthz/", "/healthz%2F",
    "/.env", "/docs", "/openapi.json", "/sse", "/messages/", "/admin", "/%00", "/session?x=1",
])
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_nothing_but_healthz_is_reachable_without_credentials(client, method, path):
    r = client.request(method, path, content=b"a=b")
    assert r.status_code == 401, f"{method} {path} -> {r.status_code}"
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.text == "Unauthorized"  # no hint about what exists


def test_head_is_gated_too(client):
    assert client.head("/mcp").status_code == 401
    assert client.head("/session").status_code == 401


def test_healthz_reveals_nothing(client):
    r = client.get("/healthz")
    assert (r.status_code, r.text) == (200, "ok")


@pytest.mark.parametrize("header", [
    f"Bearer {TOKEN}",
    f"bearer {TOKEN}",
    f"BEARER {TOKEN}",
    f"Bearer   {TOKEN}",
    f"Bearer {TOKEN}  ",
])
def test_reasonable_spellings_of_the_header_are_accepted(client, header):
    assert post_init(client, headers={"Authorization": header}).status_code == 200


@pytest.mark.parametrize("header", [
    "",
    "Bearer",
    "Bearer ",
    f"Bearer{TOKEN}",
    f"Token {TOKEN}",
    f"Basic {TOKEN}",
    f"Bearer {TOKEN}x",
    f"Bearer x{TOKEN}",
    f"Bearer {TOKEN[:-1]}",
    f"Bearer {TOKEN.upper()}",
    f"Bearer {TOKEN} extra",
    f"Bearer \"{TOKEN}\"",
])
def test_near_misses_are_rejected(client, header):
    assert post_init(client, headers={"Authorization": header}).status_code == 401


def test_second_authorization_header_cannot_smuggle_past_a_bad_first(client):
    """Either header may carry the right token; neither being right is a 401."""
    both_bad = client.post("/mcp", json=INIT, headers=[
        *MCP.items(), ("Authorization", "Bearer nope"), ("Authorization", "Bearer also-nope")])
    assert both_bad.status_code == 401


def test_non_ascii_credentials_are_a_401_not_a_crash(client):
    r = client.post("/mcp", content=json.dumps(INIT).encode(), headers=[
        ("Accept", MCP["Accept"]), ("Content-Type", "application/json"),
        ("Authorization", "Bearer ".encode() + "密碼\xff".encode("latin-1", "ignore"))])
    assert r.status_code == 401
    assert client.get("/mcp?token=%ff%fe%e5%af%86").status_code == 401
    assert client.get("/mcp?token=" + quote("密碼")).status_code == 401


@pytest.mark.parametrize("query", [
    "", "token", "token=", "token=wrong", "tokens=" + TOKEN, "TOKEN=" + TOKEN,
    "token=" + TOKEN + "x", "token=" + TOKEN[:-1], "x=1&token=wrong",
    "token=" + TOKEN.replace("-", "%2d%00"),
])
def test_bad_query_tokens_are_rejected(client, query):
    assert client.get(f"/mcp?{query}").status_code == 401


@pytest.mark.parametrize("query", [
    "token=" + TOKEN,
    "x=1&token=" + TOKEN,
    "token=wrong&token=" + TOKEN,          # repeated parameter: any exact match
    "token=" + TOKEN + "&x=1",
    "token=" + quote(TOKEN, safe=""),
    "token=" + TOKEN.replace("-", "%2D"),  # percent-encoding is decoded
])
def test_good_query_tokens_are_accepted(client, query):
    assert post_init(client, url=f"/mcp?{query}").status_code == 200


def test_plus_in_a_query_token_means_space_so_clients_must_encode_it(make_client):
    c = make_client("abc+def+ghi+jkl+mno+pqr")
    assert c.get("/mcp?token=abc+def+ghi+jkl+mno+pqr").status_code == 401
    assert post_init(c, url="/mcp?token=" + quote("abc+def+ghi+jkl+mno+pqr", safe="")).status_code == 200
    assert post_init(c, headers={"Authorization": "Bearer abc+def+ghi+jkl+mno+pqr"}).status_code == 200


def test_oversized_credentials_do_not_slow_or_crash_the_gate(client):
    assert post_init(client, headers={"Authorization": "Bearer " + "A" * 60_000}).status_code == 401
    assert client.get("/mcp?token=" + "A" * 60_000).status_code == 401


def test_tokenless_mode_is_open_by_explicit_choice(make_client):
    c = make_client(token=None)
    assert post_init(c).status_code == 200
    assert c.get("/healthz").status_code == 200


def test_wrong_token_never_reaches_the_inner_app(monkeypatch):
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])

    mw = remote.BearerAuthMiddleware(inner, TOKEN)
    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "path": "/mcp", "headers": [(b"authorization", b"Bearer nope")],
             "query_string": b"", "method": "GET"}
    asyncio.run(mw(scope, None, send))
    assert reached == [] and sent[0]["status"] == 401


# --------------------------------------------------------------------------
# /session request-body handling
# --------------------------------------------------------------------------

AUTH = {"Authorization": f"Bearer {TOKEN}"}
SESSION_COOKIE = "__Secure-next-auth.session-token=abc"


@pytest.mark.parametrize("body,status", [
    (b"", 400),
    (b"   \n  ", 400),
    (b"\xff\xfe\x00garbage", 400),
    (b"{", 400),
    (b"a=b", 400),                       # parses, but has no login cookie
    (b"x" * (remote.MAX_PASTE_BYTES + 1), 413),
])
def test_bad_session_bodies_get_a_clean_json_error(client, cookies_file, body, status):
    r = client.post("/session", content=body, headers=AUTH)
    assert r.status_code == status
    payload = r.json()
    assert payload["ok"] is False and payload["message"]
    assert not cookies_file.exists()


def test_body_exactly_at_the_limit_is_accepted_for_parsing(client, monkeypatch, cookies_file):
    monkeypatch.setattr(remote, "verify_session", lambda c: None)
    pad = "x" * (remote.MAX_PASTE_BYTES - len(SESSION_COOKIE) - len("; p="))
    body = f"{SESSION_COOKIE}; p={pad}".encode()
    assert len(body) == remote.MAX_PASTE_BYTES
    assert client.post("/session", content=body, headers=AUTH).status_code == 200


def run_read_body(chunks, limit):
    messages = iter(chunks)

    async def receive():
        return next(messages)

    return asyncio.run(remote._read_body(receive, limit))


def test_read_body_reassembles_chunks():
    chunks = [{"type": "http.request", "body": b"ab", "more_body": True},
              {"type": "http.request", "body": b"cd", "more_body": True},
              {"type": "http.request", "body": b"ef", "more_body": False}]
    assert run_read_body(chunks, 100) == b"abcdef"


def test_read_body_stops_as_soon_as_the_limit_is_passed():
    consumed = []

    def stream():
        for i in range(1000):
            consumed.append(i)
            yield {"type": "http.request", "body": b"x" * 10, "more_body": True}

    assert run_read_body(stream(), 25) is None
    assert len(consumed) == 3  # 10, 20, 30 > 25 -> stop; never reads the rest


def test_read_body_handles_a_client_that_disconnects():
    assert run_read_body([{"type": "http.request", "body": b"ab", "more_body": True},
                          {"type": "http.disconnect"}], 100) is None


def test_read_body_with_no_body_key():
    assert run_read_body([{"type": "http.request"}], 100) == b""
