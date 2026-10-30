"""Tests for the HTTP deployment: auth gate, tool surface, cookie env loading.

No live MFP session or network needed; the app is driven in-process.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mfp_mcp import remote, server  # noqa: E402

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


@pytest.fixture(scope="module")
def client():
    app = remote.build_http_app(TOKEN)
    with TestClient(app) as c:
        yield c


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


def test_lists_tools_without_local_only_ones(client):
    auth = {**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}
    r = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=auth
    )
    assert r.status_code == 200
    payload = next(
        json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")
    ) if r.text.lstrip().startswith("event:") else r.json()
    names = {t["name"] for t in payload["result"]["tools"]}
    assert "mfp_get_diary" in names
    assert "refresh_browser_cookies" not in names


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
def test_allowed_hosts_enforced_when_configured(monkeypatch, host, status):
    monkeypatch.setenv("MFP_MCP_ALLOWED_HOSTS", "mfp.example.com")
    server.mcp._session_manager = None  # a manager can only run() once
    app = remote.build_http_app(TOKEN)
    auth = {**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}
    with TestClient(app, base_url=f"http://{host}") as c:
        assert c.post("/mcp", json=INIT, headers=auth).status_code == status
