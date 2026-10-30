"""get_mfp_client: cookies.json first, then MFP_COOKIES_JSON, nothing else."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mfp_mcp import server  # noqa: E402

FILE = {"__Secure-next-auth.session-token": "from-file"}
ENV = {"__Secure-next-auth.session-token": "from-env"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(server, "COOKIES_FILE", tmp_path / "cookies.json")
    monkeypatch.delenv("MFP_COOKIES_JSON", raising=False)
    refreshed = []
    monkeypatch.setattr(
        server, "maybe_refresh_session",
        lambda cookies, force=False: refreshed.append((dict(cookies), force)) or cookies,
    )
    return refreshed


def _stored(cookies):
    server.COOKIES_FILE.write_text(
        json.dumps({"cookies": cookies, "saved_at": datetime.now().isoformat()})
    )


def _accepting(valid_values):
    def build(cookies):
        if cookies["__Secure-next-auth.session-token"] not in valid_values:
            raise RuntimeError("rejected")
        return ("client", cookies["__Secure-next-auth.session-token"])
    return build


def test_stored_cookies_win_over_env(env, monkeypatch):
    _stored(FILE)
    monkeypatch.setenv("MFP_COOKIES_JSON", json.dumps(ENV))
    monkeypatch.setattr(server, "_client_from_cookies", _accepting({"from-file", "from-env"}))
    assert server.get_mfp_client() == ("client", "from-file")
    assert env == [(FILE, False)]


def test_dead_stored_session_falls_back_to_env_and_persists(env, monkeypatch):
    _stored(FILE)
    monkeypatch.setenv("MFP_COOKIES_JSON", json.dumps(ENV))
    monkeypatch.setattr(server, "_client_from_cookies", _accepting({"from-env"}))
    assert server.get_mfp_client() == ("client", "from-env")
    assert env == [(ENV, True)]


def test_env_only(env, monkeypatch):
    monkeypatch.setenv("MFP_COOKIES_JSON", json.dumps(ENV))
    monkeypatch.setattr(server, "_client_from_cookies", _accepting({"from-env"}))
    assert server.get_mfp_client() == ("client", "from-env")


def test_nothing_configured_explains_how_to_fix(env):
    with pytest.raises(RuntimeError, match="No MyFitnessPal session cookies are configured"):
        server.get_mfp_client()


def test_nothing_works_reports_the_failure(env, monkeypatch):
    _stored(FILE)
    monkeypatch.setattr(server, "_client_from_cookies", _accepting(set()))
    with pytest.raises(RuntimeError, match="no longer works.*rejected"):
        server.get_mfp_client()


def test_legacy_login_paths_are_gone():
    assert not hasattr(server, "authenticate_with_credentials")
    assert not hasattr(server, "get_decrypted_credential")
