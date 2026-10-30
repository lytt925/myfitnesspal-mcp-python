"""Pasting DevTools cookies: parser formats and the /session endpoint."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mfp_mcp import remote, server  # noqa: E402
from mfp_mcp.cookie_paste import CookiePasteError, parse_pasted_cookies  # noqa: E402

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
def stored(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(server, "COOKIES_FILE", tmp_path / "cookies.json")
    return tmp_path / "cookies.json"


@pytest.fixture
def client():
    server.mcp._session_manager = None
    with TestClient(remote.build_http_app(TOKEN)) as c:
        yield c


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


def test_paste_without_login_cookie_explains_httponly(client, stored, monkeypatch):
    monkeypatch.setattr(remote, "verify_session", lambda c: pytest.fail("must not verify"))
    r = client.post("/session", content="_ga=1; foo=bar", headers=AUTH)
    assert r.status_code == 400 and "HttpOnly" in r.json()["message"]
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


def test_session_is_disabled_without_a_token(stored, monkeypatch):
    """Tokenless mode must not expose a login-CSRF-able write endpoint."""
    monkeypatch.setattr(remote, "verify_session", lambda c: pytest.fail("must not verify"))
    server.mcp._session_manager = None
    with TestClient(remote.build_http_app(None)) as c:
        for method in ("get", "post"):
            r = getattr(c, method)("/session", **({"content": GOOD} if method == "post" else {}))
            assert r.status_code == 404
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
