"""Tests for the HTTP deployment: auth gate, tool surface, cookie env loading.

No live MFP session or network needed; the app is driven in-process.
"""
from __future__ import annotations

import json
import pytest

from mfp_mcp import remote, server

TOKEN = "t" * 32
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


@pytest.fixture
def client(make_client):
    return make_client(TOKEN)


def rpc_result(response):
    """The JSON-RPC message in a response, whether sent as SSE or plain JSON."""
    if response.headers["content-type"].startswith("text/event-stream"):
        data = [line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return response.json()


def test_healthz_is_open(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.text == "ok"


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer nope"},
    {"Authorization": "Basic " + TOKEN},
    {"Authorization": TOKEN},
])
def test_rejects_missing_or_wrong_credentials(client, headers):
    r = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, **headers})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_rejects_wrong_query_token(client):
    r = client.post("/mcp?token=wrong", json=INIT, headers=MCP_HEADERS)
    assert r.status_code == 401


def test_bearer_header_reaches_mcp(client):
    r = client.post(
        "/mcp", json=INIT, headers={**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}
    )
    assert r.status_code == 200
    assert "myfitnesspal_mcp" in r.text


def test_query_token_reaches_mcp(client):
    r = client.post(f"/mcp?token={TOKEN}", json=INIT, headers=MCP_HEADERS)
    assert r.status_code == 200


def test_lists_tools(client):
    auth = {**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}
    r = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=auth
    )
    assert r.status_code == 200
    names = {t["name"] for t in rpc_result(r)["result"]["tools"]}
    assert "mfp_get_diary" in names


def test_serve_http_refuses_without_token(monkeypatch):
    monkeypatch.delenv("MFP_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MFP_MCP_ALLOW_NO_AUTH", raising=False)
    with pytest.raises(SystemExit) as exc:
        remote.serve_http("127.0.0.1", 0)
    assert "MFP_MCP_AUTH_TOKEN" in str(exc.value)


def test_serve_http_refuses_short_token(monkeypatch):
    monkeypatch.setenv("MFP_MCP_AUTH_TOKEN", "short")
    with pytest.raises(SystemExit) as exc:
        remote.serve_http("127.0.0.1", 0)
    assert "at least" in str(exc.value)


@pytest.mark.parametrize("raw,expected", [
    ('{"a": "1"}', {"a": "1"}),
    ('{"cookies": {"a": "1"}, "saved_at": "x"}', {"a": "1"}),
    ("not json", None),
    ("[]", None),
    ("{}", None),
    ("", None),
])
def test_load_env_cookies(monkeypatch, raw, expected):
    monkeypatch.setenv("MFP_COOKIES_JSON", raw)
    assert server.load_env_cookies() == expected


def test_load_env_cookies_unset(monkeypatch):
    monkeypatch.delenv("MFP_COOKIES_JSON", raising=False)
    assert server.load_env_cookies() is None


@pytest.mark.parametrize("host,status", [
    ("mfp.example.com", 200),
    ("evil.example.net", 421),
])
def test_allowed_hosts_enforced_when_configured(monkeypatch, make_client, host, status):
    monkeypatch.setenv("MFP_MCP_ALLOWED_HOSTS", "mfp.example.com")
    c = make_client(TOKEN, base_url=f"http://{host}")
    auth = {**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}
    assert c.post("/mcp", json=INIT, headers=auth).status_code == status
