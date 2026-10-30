"""cookies.json: round trips, expiry, and every flavor of bad file."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from mfp_mcp import server

COOKIES = {"__Secure-next-auth.session-token": "ey.J=abc==", "k": "v"}
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


def write_raw(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))


def write_cookies(path, cookies=COOKIES, age=timedelta(0), **extra):
    write_raw(path, {"cookies": cookies, "saved_at": (datetime.now() - age).isoformat(), **extra})


def test_round_trip(cookies_file):
    server.save_cookies(COOKIES)
    assert server.load_cookies() == COOKIES


def test_round_trip_keeps_unicode_and_odd_characters(cookies_file):
    odd = {"a": "雙引號\"與'單引號'", "b": "semi;colon", "c": "x" * 5000, "d": "a=b=c"}
    server.save_cookies(odd)
    assert server.load_cookies() == odd


def test_saving_again_replaces_rather_than_merges(cookies_file):
    server.save_cookies({"old": "1"})
    server.save_cookies({"new": "2"})
    assert server.load_cookies() == {"new": "2"}


def test_missing_file(cookies_file):
    assert server.load_cookies() is None
    assert server._cookies_file_age() is None


@pytest.mark.parametrize("age,usable", [
    (timedelta(days=0), True),
    (timedelta(days=29, hours=23), True),
    (timedelta(days=30, minutes=1), False),
    (timedelta(days=400), False),
])
def test_thirty_day_expiry(cookies_file, age, usable):
    write_cookies(cookies_file, age=age)
    assert (server.load_cookies() == COOKIES) is usable


@pytest.mark.parametrize("payload", [
    "",
    "not json at all",
    "{",
    "[]",
    "null",
    '"a string"',
    {"cookies": COOKIES},                                   # no saved_at
    {"cookies": COOKIES, "saved_at": "yesterday-ish"},      # unparsable timestamp
    {"cookies": COOKIES, "saved_at": None},
    {"cookies": COOKIES, "saved_at": datetime.now(timezone.utc).isoformat()},  # tz-aware
])
def test_unusable_files_yield_none_instead_of_raising(cookies_file, payload):
    write_raw(cookies_file, payload)
    assert server.load_cookies() is None


def test_future_timestamp_is_not_treated_as_expired(cookies_file):
    write_cookies(cookies_file, age=timedelta(days=-3))
    assert server.load_cookies() == COOKIES


def test_age_of_fresh_and_stale_files(cookies_file):
    write_cookies(cookies_file, age=timedelta(hours=5))
    assert timedelta(hours=4, minutes=59) < server._cookies_file_age() < timedelta(hours=5, minutes=1)


@pytest.mark.parametrize("payload", ["", "garbage", {"cookies": COOKIES}, {"saved_at": 12345}])
def test_age_of_unusable_files_is_none(cookies_file, payload):
    write_raw(cookies_file, payload)
    assert server._cookies_file_age() is None


@POSIX
def test_saved_file_and_new_directory_are_owner_only(cookies_file):
    server.save_cookies(COOKIES)
    assert cookies_file.stat().st_mode & 0o777 == 0o600
    assert cookies_file.parent.stat().st_mode & 0o777 == 0o700


@POSIX
def test_saving_over_a_symlink_replaces_the_link_not_its_target(cookies_file, tmp_path):
    target = tmp_path / "precious.txt"
    target.write_text("do not touch")
    cookies_file.parent.mkdir(parents=True)
    cookies_file.symlink_to(target)
    server.save_cookies(COOKIES)
    assert target.read_text() == "do not touch"
    assert not cookies_file.is_symlink() and server.load_cookies() == COOKIES


@POSIX
def test_default_directory_is_tightened_but_a_custom_existing_one_is_not(tmp_path, monkeypatch):
    default_like = tmp_path / "default"
    default_like.mkdir()
    default_like.chmod(0o755)
    monkeypatch.setattr(server, "CONFIG_DIR", default_like)
    server.ensure_config_dir()  # MFP_CONFIG_DIR unset -> ours to lock down
    assert default_like.stat().st_mode & 0o777 == 0o700


@POSIX
def test_unwritable_custom_directory_surfaces_the_error(cookies_file, monkeypatch):
    # An operator-provided directory (MFP_CONFIG_DIR) is never chmod'ed back to
    # writable; the failure must reach the caller, which reports it.
    monkeypatch.setenv("MFP_CONFIG_DIR", str(cookies_file.parent))
    cookies_file.parent.mkdir(parents=True)
    cookies_file.parent.chmod(0o500)
    try:
        if cookies_file.parent.stat().st_uid == 0:
            pytest.skip("root ignores directory permissions")
        with pytest.raises(OSError):
            server.save_cookies(COOKIES)
    finally:
        cookies_file.parent.chmod(0o700)


def test_env_cookies_are_not_age_limited(monkeypatch):
    monkeypatch.setenv("MFP_COOKIES_JSON", json.dumps({"saved_at": "2000-01-01", "a": "1"}))
    assert server.load_env_cookies() == {"saved_at": "2000-01-01", "a": "1"}
