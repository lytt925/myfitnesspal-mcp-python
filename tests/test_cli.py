"""Command-line surface: argument parsing, server startup, cookie export/import."""
from __future__ import annotations

import io
import sys
import types

import pytest

from mfp_mcp import remote, server

SESSION = "__Secure-next-auth.session-token"
SECRET = "s3cr3t-value-that-must-not-leak"
COOKIES = {SESSION: SECRET, "_ga": "GA1.2"}


# --------------------------------------------------------------------------
# argument parsing / dispatch
# --------------------------------------------------------------------------

def parse(argv=()):
    return remote._parser().parse_args(list(argv))


def test_defaults_are_local_stdio():
    a = parse()
    assert (a.transport, a.host, a.port, a.command) == ("stdio", "127.0.0.1", 8000, None)


@pytest.mark.parametrize("env,expected", [
    ({"PORT": "9000"}, 9000),
    ({"MFP_MCP_PORT": "9100"}, 9100),
    ({"PORT": "9000", "MFP_MCP_PORT": "9100"}, 9100),  # specific beats the PaaS convention
])
def test_port_from_environment(monkeypatch, env, expected):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert parse().port == expected


def test_flags_override_environment(monkeypatch):
    monkeypatch.setenv("MFP_MCP_PORT", "9100")
    monkeypatch.setenv("MFP_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MFP_MCP_TRANSPORT", "streamable-http")
    a = parse(["--port", "7", "--host", "h", "--transport", "stdio"])
    assert (a.port, a.host, a.transport) == (7, "h", "stdio")


def test_environment_selects_the_transport(monkeypatch):
    monkeypatch.setenv("MFP_MCP_TRANSPORT", "streamable-http")
    assert parse().transport == "streamable-http"


@pytest.mark.parametrize("var", ["PORT", "MFP_MCP_PORT"])
@pytest.mark.parametrize("bad", ["abc", "80.5", "-"])
def test_a_garbage_port_is_a_usage_error_not_a_traceback(monkeypatch, capsys, var, bad):
    monkeypatch.setenv(var, bad)
    with pytest.raises(SystemExit) as exc:
        parse()
    assert exc.value.code == 2
    assert "invalid int value" in capsys.readouterr().err


def test_unknown_transport_is_rejected(capsys):
    with pytest.raises(SystemExit):
        parse(["--transport", "carrier-pigeon"])


def test_run_cli_dispatches(monkeypatch):
    calls = []
    monkeypatch.setattr(remote, "serve_http", lambda h, p: calls.append(("http", h, p)))
    monkeypatch.setattr(remote, "export_cookies", lambda b: calls.append(("export", b)))
    monkeypatch.setattr(remote, "import_cookies", lambda f=None: calls.append(("import", f)))
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: calls.append(("stdio",)))

    remote.run_cli([])
    remote.run_cli(["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "1234"])
    remote.run_cli(["export-cookies", "--browser", "arc"])
    remote.run_cli(["export-cookies"])
    remote.run_cli(["import-cookies"])
    remote.run_cli(["import-cookies", "c.txt"])
    assert calls == [("stdio",), ("http", "0.0.0.0", 1234), ("export", "arc"),
                     ("export", "auto"), ("import", None), ("import", "c.txt")]


def test_main_is_wired_to_the_cli(monkeypatch):
    seen = []
    monkeypatch.setattr(remote, "run_cli", lambda argv: seen.append(argv))
    server.main(["--transport", "stdio"])
    assert seen == [["--transport", "stdio"]]


# --------------------------------------------------------------------------
# serve_http
# --------------------------------------------------------------------------

@pytest.fixture
def uvicorn_calls(monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    return calls


def test_serve_http_never_logs_access(uvicorn_calls, monkeypatch):
    """`?token=` would otherwise be written to the access log."""
    monkeypatch.setenv("MFP_MCP_AUTH_TOKEN", "x" * 32)
    remote.serve_http("0.0.0.0", 8123)
    (app, kw), = uvicorn_calls
    assert kw["access_log"] is False and (kw["host"], kw["port"]) == ("0.0.0.0", 8123)
    assert isinstance(app, remote.BearerAuthMiddleware)


@pytest.mark.parametrize("flag,expected", [(None, True), ("1", True), ("0", False), ("false", False), ("off", False)])
def test_stateless_flag(uvicorn_calls, monkeypatch, flag, expected):
    monkeypatch.setenv("MFP_MCP_AUTH_TOKEN", "x" * 32)
    if flag is not None:
        monkeypatch.setenv("MFP_MCP_STATELESS", flag)
    remote.serve_http("127.0.0.1", 1)
    assert server.mcp.settings.stateless_http is expected


def test_serving_without_a_token_needs_an_explicit_opt_out(uvicorn_calls, monkeypatch):
    with pytest.raises(SystemExit):
        remote.serve_http("127.0.0.1", 1)
    assert uvicorn_calls == []
    monkeypatch.setenv("MFP_MCP_ALLOW_NO_AUTH", "1")
    remote.serve_http("127.0.0.1", 1)
    assert len(uvicorn_calls) == 1


@pytest.mark.parametrize("token", ["", "short", "x" * 15])
def test_empty_or_short_tokens_never_start_a_server(uvicorn_calls, monkeypatch, token):
    monkeypatch.setenv("MFP_MCP_AUTH_TOKEN", token)
    with pytest.raises(SystemExit):
        remote.serve_http("127.0.0.1", 1)
    assert uvicorn_calls == []


def test_a_token_of_exactly_the_minimum_length_is_fine(uvicorn_calls, monkeypatch):
    monkeypatch.setenv("MFP_MCP_AUTH_TOKEN", "x" * remote.MIN_TOKEN_LENGTH)
    remote.serve_http("127.0.0.1", 1)
    assert len(uvicorn_calls) == 1


# --------------------------------------------------------------------------
# export-cookies / import-cookies
# --------------------------------------------------------------------------

def run_export(monkeypatch, capsys, *, collect=None, verify=None, **kw):
    monkeypatch.setattr(remote, "collect_browser_cookies", collect or (lambda b: dict(COOKIES)))
    monkeypatch.setattr(remote, "verify_session", verify or (lambda c: None))
    try:
        remote.export_cookies("auto", **kw)
        code = None
    except SystemExit as e:
        code = str(e.code)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_export_prints_only_the_json_on_stdout(monkeypatch, capsys):
    code, out, err = run_export(monkeypatch, capsys)
    assert code is None
    assert out.count("\n") == 1 and __import__("json").loads(out) == COOKIES
    assert SECRET not in err and "verified" in err


def test_export_does_not_print_cookies_when_verification_fails(monkeypatch, capsys):
    def reject(_):
        raise RuntimeError("403 Forbidden")

    code, out, err = run_export(monkeypatch, capsys, verify=reject)
    assert out == "" and SECRET not in err and "export-cookies failed" in code and "403" in code


def test_export_reports_collection_failures(monkeypatch, capsys):
    def nothing(_):
        raise RuntimeError("No MyFitnessPal session found")

    code, out, err = run_export(monkeypatch, capsys, collect=nothing)
    assert out == "" and "No MyFitnessPal session found" in code


class _Terminal(io.StringIO):
    def isatty(self):
        return True


def run_import(monkeypatch, capsys, pasted, verify=None, path=None, tty=False):
    monkeypatch.setattr(sys, "stdin", (_Terminal if tty else io.StringIO)(pasted))
    monkeypatch.setattr(remote, "verify_session", verify or (lambda c: None))
    try:
        remote.import_cookies(path)
        code = None
    except SystemExit as e:
        code = str(e.code)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_import_saves_a_paste_and_prints_nothing_secret(monkeypatch, capsys):
    code, out, err = run_import(monkeypatch, capsys, f"Cookie: {SESSION}={SECRET}; _ga=GA1.2")
    assert code is None
    assert server.load_cookies() == COOKIES
    assert SECRET not in out and SECRET not in err and "saved to" in err


def test_import_reads_a_file(monkeypatch, capsys, tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text(f"{SESSION}={SECRET}\n_ga=GA1.2\n")
    code, out, err = run_import(monkeypatch, capsys, "", path=str(f))
    assert code is None and server.load_cookies() == COOKIES
    assert SECRET not in out + err


def test_import_file_with_a_bom_and_a_long_session_cookie(monkeypatch, capsys, tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text(f"\ufeff{SESSION}={'e' * 1700}; _ga=GA1.2", encoding="utf-8")
    code, _, _ = run_import(monkeypatch, capsys, "", path=str(f))
    assert code is None and len(server.load_cookies()[SESSION]) == 1700


def test_import_missing_file_is_a_clear_error(monkeypatch, capsys, tmp_path):
    code, out, _ = run_import(monkeypatch, capsys, "", path=str(tmp_path / "nope.txt"))
    assert out == "" and "import-cookies failed" in code and "nope.txt" in code
    assert server.load_cookies() is None


def test_import_on_a_terminal_points_to_a_file_instead_of_hanging(monkeypatch, capsys):
    code, out, _ = run_import(monkeypatch, capsys, "ignored", tty=True)
    assert out == "" and "import-cookies failed" in code and "file" in code
    assert server.load_cookies() is None


def test_import_accepts_export_cookies_output(monkeypatch, capsys):
    exported = __import__("json").dumps(COOKIES)
    code, _, _ = run_import(monkeypatch, capsys, exported)
    assert code is None and server.load_cookies() == COOKIES


@pytest.mark.parametrize("pasted,fragment", [
    ("", "Nothing was pasted"),
    ("{broken", "JSON"),
    ("_ga=1; other=2", "session-token"),
])
def test_import_rejects_bad_pastes_without_output(monkeypatch, capsys, pasted, fragment):
    code, out, err = run_import(monkeypatch, capsys, pasted)
    assert out == "" and "import-cookies failed" in code and fragment in code
    assert server.load_cookies() is None


def test_import_does_not_print_when_myfitnesspal_rejects(monkeypatch, capsys):
    def reject(_):
        raise RuntimeError("expired")

    code, out, err = run_import(monkeypatch, capsys, f"{SESSION}={SECRET}", verify=reject)
    assert out == "" and SECRET not in err + str(code)
    assert server.load_cookies() is None  # a rejected session is never stored


# --------------------------------------------------------------------------
# collect_browser_cookies (the keychain-timeout regression lives here)
# --------------------------------------------------------------------------

def fake_browser_cookie3(monkeypatch, **jars):
    module = types.ModuleType("browser_cookie3")
    for name, cookies in jars.items():
        module.__dict__[name] = lambda domain_name=None, _c=cookies: [
            types.SimpleNamespace(name=k, value=v) for k, v in _c.items()]
    monkeypatch.setitem(sys.modules, "browser_cookie3", module)


def test_auto_on_macos_gives_the_user_time_to_answer_the_keychain_prompt(monkeypatch):
    seen = {}
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(server, "try_chromium_browsers_for_session_cookies",
                        lambda timeout=5: seen.setdefault("t", timeout) and ("Chrome", dict(COOKIES)))
    assert remote.collect_browser_cookies("auto") == COOKIES
    assert seen["t"] == remote.INTERACTIVE_KEYCHAIN_TIMEOUT >= 60  # the old 5s default lost the race


def test_explicit_chromium_browser_on_macos_gets_the_same_timeout(monkeypatch):
    seen = {}
    monkeypatch.setattr(sys, "platform", "darwin")

    def extract(service, timeout=5):
        seen.update(service=service, timeout=timeout)
        return dict(COOKIES)

    monkeypatch.setattr(server, "_try_extract_from_chromium_browser", extract)
    assert remote.collect_browser_cookies("Chrome") == COOKIES
    assert seen == {"service": "Chrome Safe Storage", "timeout": remote.INTERACTIVE_KEYCHAIN_TIMEOUT}


def test_server_side_keychain_lookups_keep_a_short_timeout():
    import inspect

    for fn in (server._safe_storage_keychain_password, server._try_extract_from_chromium_browser,
               server.try_chromium_browsers_for_session_cookies):
        timeout = next(p for n, p in inspect.signature(fn).parameters.items() if "timeout" in n)
        assert timeout.default == 5  # headless tool calls must not hang on a dialog


def test_auto_off_macos_falls_back_to_the_chrome_profile(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    fake_browser_cookie3(monkeypatch, chrome=COOKIES)
    assert remote.collect_browser_cookies("auto") == COOKIES


def test_firefox(monkeypatch):
    fake_browser_cookie3(monkeypatch, firefox=COOKIES)
    assert remote.collect_browser_cookies("Firefox ") == COOKIES


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_a_browser_without_a_login_is_a_clear_error(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(server, "try_chromium_browsers_for_session_cookies", lambda t=5: None)
    monkeypatch.setattr(server, "_try_extract_from_chromium_browser", lambda s, t=5: {"_ga": "1"})
    fake_browser_cookie3(monkeypatch, chrome={"_ga": "1"}, firefox={})
    for browser in ("auto", "chrome", "firefox"):
        with pytest.raises(RuntimeError, match="No MyFitnessPal session found"):
            remote.collect_browser_cookies(browser)


def test_unknown_browser(monkeypatch):
    with pytest.raises(RuntimeError, match="Unsupported browser"):
        remote.collect_browser_cookies("netscape")


# --------------------------------------------------------------------------
# verify_session
# --------------------------------------------------------------------------

def test_verify_session_makes_one_authenticated_request(monkeypatch):
    import myfitnesspal

    seen = {}

    class FakeClient:
        def __init__(self, cookiejar):
            seen["jar"] = {c.name: c.value for c in cookiejar}

        def get_date(self, day):
            seen["day"] = day

    monkeypatch.setattr(myfitnesspal, "Client", FakeClient)
    remote.verify_session(COOKIES)
    assert seen["jar"] == COOKIES and seen["day"].year >= 2026


def test_verify_session_propagates_rejection(monkeypatch):
    import myfitnesspal

    class Rejecting:
        def __init__(self, cookiejar):
            pass

        def get_date(self, day):
            raise RuntimeError("403")

    monkeypatch.setattr(myfitnesspal, "Client", Rejecting)
    with pytest.raises(RuntimeError, match="403"):
        remote.verify_session(COOKIES)
