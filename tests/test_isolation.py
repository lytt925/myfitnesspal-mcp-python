"""The safety nets in conftest.py actually work."""
from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from mfp_mcp import server


def test_cookie_store_is_never_the_real_home_dir():
    real = Path.home() / ".mfp_mcp"
    assert real not in (server.CONFIG_DIR, *server.COOKIES_FILE.parents)
    assert not server.CONFIG_DIR.exists()  # created lazily, only if a test saves


def test_mfp_variables_from_the_shell_are_invisible(monkeypatch):
    # conftest ran before this body: whatever the developer exported is gone.
    assert [k for k in os.environ if k.startswith("MFP_") or k == "PORT"] == []


def test_real_hostnames_cannot_be_resolved():
    with pytest.raises(BaseException) as exc:
        socket.getaddrinfo("www.myfitnesspal.com", 443)
    assert "real hostname" in str(exc.value)


def test_real_connections_cannot_be_opened():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(BaseException) as exc:
            s.connect(("93.184.216.34", 443))
        assert "real network" in str(exc.value)
    finally:
        s.close()


def test_a_blanket_except_cannot_hide_a_network_attempt():
    """The code under test wraps network calls in `except Exception`; the guard must outlive that."""
    import requests

    outcome = "no error escaped"
    try:
        try:
            requests.get("https://www.myfitnesspal.com/api/auth/session", timeout=1)
        except Exception:  # what maybe_refresh_session does
            outcome = "swallowed"
    except BaseException:
        outcome = "propagated"
    assert outcome == "propagated"


def test_local_connections_still_work():
    server_sock = socket.socket()
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    client = socket.socket()
    try:
        client.connect(server_sock.getsockname())
    finally:
        client.close()
        server_sock.close()
