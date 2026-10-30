"""Tests for keeping the MFP session alive via NextAuth's /api/auth/session."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mfp_mcp import server  # noqa: E402

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
def cookie_file(tmp_path, monkeypatch):
    path = tmp_path / "cookies.json"
    monkeypatch.setattr(server, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(server, "COOKIES_FILE", path)
    return path


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
