"""Shared test isolation.

Every test runs with: no MFP_*/PORT variables from the developer's shell, the
cookie store redirected to a temp dir (never the real ~/.mfp_mcp), no real
network access, and a pristine FastMCP singleton. Tests that need something
specific set it themselves.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mfp_mcp import remote, server  # noqa: E402

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testserver"}


@pytest.fixture(autouse=True)
def _scrubbed_environment(monkeypatch):
    """Tests must not depend on (or be altered by) the developer's environment."""
    for name in list(os.environ):
        if name.startswith("MFP_") or name == "PORT":
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def _isolated_cookie_store(tmp_path_factory, monkeypatch):
    """Point the cookie store at a throwaway dir and reset refresh throttling."""
    config_dir = tmp_path_factory.mktemp("mfp_config") / "store"  # created lazily
    monkeypatch.setattr(server, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(server, "COOKIES_FILE", config_dir / "cookies.json")
    monkeypatch.setattr(server, "_last_refresh_attempt", None)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Fail loudly on any attempt to reach a non-local host.

    Raises pytest's `Failed` (a BaseException) so it can't be swallowed by the
    broad `except Exception` handlers around the network calls under test.
    """
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def guarded_connect(self, address):
        if (
            self.family in (socket.AF_INET, socket.AF_INET6)
            and isinstance(address, tuple)
            and address[0] not in LOCAL_HOSTS
        ):
            pytest.fail(f"test tried to connect to the real network: {address[0]}")
        return real_connect(self, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if isinstance(host, str) and host not in LOCAL_HOSTS:
            pytest.fail(f"test tried to resolve a real hostname: {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture(autouse=True)
def _pristine_mcp(monkeypatch):
    """The FastMCP object is a module singleton; undo whatever a test does to it.

    A StreamableHTTPSessionManager can run() only once per instance, and
    build_http_app() rewrites `mcp.settings`, so both are restored per test.
    """
    mcp = server.mcp
    monkeypatch.setattr(mcp, "_session_manager", None)
    monkeypatch.setattr(mcp.settings, "stateless_http", mcp.settings.stateless_http)
    monkeypatch.setattr(mcp.settings, "transport_security", mcp.settings.transport_security)


@pytest.fixture
def cookies_file() -> Path:
    """Path of the (isolated) cookies.json for this test."""
    return server.COOKIES_FILE


@pytest.fixture
def make_client():
    """Factory for in-process HTTP clients of the real app, lifespan included.

        client = make_client()                       # token-protected
        client = make_client(token=None)             # tokenless mode
        client = make_client(base_url="http://x.example")
    """
    from starlette.testclient import TestClient

    opened = []

    def make(token="t" * 32, base_url="http://testserver"):
        server.mcp._session_manager = None  # fresh manager for each app
        client = TestClient(remote.build_http_app(token), base_url=base_url)
        client.__enter__()
        opened.append(client)
        return client

    yield make
    for client in reversed(opened):
        client.__exit__(None, None, None)
