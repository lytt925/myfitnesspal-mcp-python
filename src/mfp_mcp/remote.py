"""
Network (HTTP) deployment of the MyFitnessPal MCP server.

`python -m mfp_mcp.server` keeps its default behaviour (stdio, for local
clients such as Claude Desktop). This module adds:

- `--transport streamable-http`: serve the same tools at ``/mcp`` so any MCP
  client that speaks Streamable HTTP can reach them from anywhere.
- A bearer-token gate in front of it. The tools read *and modify* a personal
  food diary, so the HTTP transport refuses to start without a token.
- `export-cookies`: run on a machine with a browser to produce the
  ``MFP_COOKIES_JSON`` value a headless server needs.

Configuration (flags override environment):

    MFP_MCP_TRANSPORT   stdio | streamable-http      (default stdio)
    MFP_MCP_HOST        bind address                  (default 127.0.0.1)
    MFP_MCP_PORT / PORT bind port                     (default 8000)
    MFP_MCP_AUTH_TOKEN  shared secret, >= 16 chars    (required for HTTP)
    MFP_MCP_STATELESS   1/0, stateless sessions       (default 1)
    MFP_MCP_ALLOWED_HOSTS  comma-separated Host headers to accept (e.g.
                        "mfp.example.com"); unset = accept any, since the
                        bearer token is the access control
    MFP_MCP_ALLOW_NO_AUTH=1  skip the token check (only behind your own auth)
"""

import argparse
import asyncio
import hmac
import json
import logging
import os
import sys
from typing import Dict, List, Optional
from urllib.parse import parse_qs

from mcp.server.transport_security import TransportSecuritySettings

from mfp_mcp.cookie_paste import CookiePasteError, parse_pasted_cookies

logger = logging.getLogger("mfp_mcp")

MIN_TOKEN_LENGTH = 16
HEALTH_PATH = "/healthz"
SESSION_PATH = "/session"
MAX_PASTE_BYTES = 64 * 1024


class BearerAuthMiddleware:
    """Pure-ASGI bearer-token gate (safe for streaming/SSE responses).

    Accepts ``Authorization: Bearer <token>``, or ``?token=<token>`` for
    clients that cannot set headers. ``/healthz`` is left open so platform
    health checks work. Non-HTTP scopes (lifespan) pass straight through.
    """

    def __init__(self, app, token: Optional[str]):
        self.app = app
        self._token = token.encode() if token else None

    def _authorized(self, scope) -> bool:
        if self._token is None:
            return True
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                scheme, _, credential = value.partition(b" ")
                if scheme.lower() == b"bearer" and hmac.compare_digest(
                    credential.strip(), self._token
                ):
                    return True
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        return any(
            hmac.compare_digest(candidate.encode(), self._token)
            for candidate in query.get("token", [])
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # Nothing here speaks websocket; refuse rather than skip the auth gate.
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        if scope["path"] == HEALTH_PATH:
            await _respond(send, 200, b"ok")
            return
        if not self._authorized(scope):
            await _respond(
                send, 401, b"Unauthorized",
                extra_headers=[(b"www-authenticate", b"Bearer")],
            )
            return
        await self.app(scope, receive, send)


async def _respond(
    send, status: int, body: bytes, extra_headers=(),
    content_type: bytes = b"text/plain; charset=utf-8",
):
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", content_type),
            (b"content-length", str(len(body)).encode()),
            *extra_headers,
        ],
    })
    await send({"type": "http.response.body", "body": body})


# Keep the secret-bearing page out of caches and out of Referer headers.
_PRIVATE_HEADERS = [(b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer")]
_PAGE_HEADERS = _PRIVATE_HEADERS + [(
    b"content-security-policy",
    b"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    b"connect-src 'self'; base-uri 'none'; form-action 'none'",
)]

SESSION_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MyFitnessPal session</title>
<style>
 body{font:16px/1.5 system-ui,sans-serif;max-width:40rem;margin:0 auto;padding:1rem}
 textarea{width:100%;height:11rem;font:13px monospace;box-sizing:border-box}
 button{font-size:1rem;padding:.5rem 1.2rem;margin-top:.5rem}
 #out{margin-top:1rem;white-space:pre-wrap}.ok{color:#1a7f37}.err{color:#cf222e}
</style></head><body>
<h1>MyFitnessPal session</h1>
<p>Paste your logged-in MyFitnessPal cookies. They are checked against
MyFitnessPal before being saved, and are never shown again.</p>
<ol>
 <li>Open <b>myfitnesspal.com</b> logged in, press <b>F12</b> &rarr; <b>Network</b>, reload.</li>
 <li>Click any request to <code>www.myfitnesspal.com</code> &rarr; <b>Request Headers</b> &rarr;
     copy the whole <code>Cookie</code> value.</li>
 <li>Paste it below. (Application &rarr; Cookies rows and JSON also work.
     <code>document.cookie</code> will <b>not</b>: the login cookie is HttpOnly.)</li>
</ol>
<textarea id="t" autocomplete="off" spellcheck="false"></textarea><br>
<button id="b">Save session</button>
<div id="out"></div>
<script>
const q = new URLSearchParams(location.search), token = q.get("token") || "";
if (token) history.replaceState(null, "", location.pathname);
document.getElementById("b").onclick = async () => {
  const out = document.getElementById("out");
  out.className = ""; out.textContent = "Checking with MyFitnessPal...";
  try {
    const r = await fetch(location.pathname, {method: "POST",
      headers: token ? {Authorization: "Bearer " + token} : {},
      body: document.getElementById("t").value});
    const j = await r.json();
    out.className = j.ok ? "ok" : "err"; out.textContent = j.message;
    if (j.ok) document.getElementById("t").value = "";
  } catch (e) { out.className = "err"; out.textContent = "Request failed: " + e; }
};
</script></body></html>
"""


async def _read_body(receive, limit: int) -> Optional[bytes]:
    """Read a request body; None if it exceeds `limit`."""
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body"):
            return b"".join(chunks)


async def _json_reply(send, status: int, ok: bool, message: str):
    await _respond(
        send, status, json.dumps({"ok": ok, "message": message}).encode(),
        extra_headers=_PRIVATE_HEADERS, content_type=b"application/json",
    )


async def save_pasted_session(scope, receive, send) -> None:
    """GET: the paste form. POST: verify pasted cookies, then store them."""
    from mfp_mcp import server

    if scope["method"] == "GET":
        await _respond(
            send, 200, SESSION_PAGE.encode(), extra_headers=_PAGE_HEADERS,
            content_type=b"text/html; charset=utf-8",
        )
        return
    if scope["method"] != "POST":
        await _respond(send, 405, b"Method Not Allowed", extra_headers=[(b"allow", b"GET, POST")])
        return

    body = await _read_body(receive, MAX_PASTE_BYTES)
    if body is None:
        await _json_reply(send, 413, False, "Paste is too large.")
        return
    try:
        cookies = parse_pasted_cookies(body.decode("utf-8", errors="replace"))
    except CookiePasteError as e:
        await _json_reply(send, 400, False, str(e))
        return
    if not server._has_real_mfp_session(cookies):
        await _json_reply(
            send, 400, False,
            "No login cookie (…session-token) found. It is HttpOnly, so copy it from the "
            "Network tab's Cookie request header or the Application tab, not document.cookie.",
        )
        return
    try:
        await asyncio.to_thread(verify_session, cookies)
    except Exception as e:
        logger.warning(f"Pasted session rejected by MyFitnessPal: {type(e).__name__}")
        await _json_reply(
            send, 400, False,
            "MyFitnessPal rejected these cookies (expired or logged out?). "
            "Log in again, copy fresh cookies, and retry. Nothing was saved.",
        )
        return
    try:
        server.save_cookies(cookies)
    except Exception as e:
        logger.error(f"Could not save pasted session: {e}")
        await _json_reply(
            send, 500, False,
            "The cookies are valid but could not be saved. Check that the server can "
            "write to its config directory (MFP_CONFIG_DIR).",
        )
        return
    logger.info("Saved pasted MyFitnessPal session")
    await _json_reply(send, 200, True, f"Saved and verified ({len(cookies)} cookies). You're set.")


class _SessionRoutes:
    """Serve /session itself; everything else goes to the MCP app."""

    def __init__(self, app, enabled: bool):
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].rstrip("/") == SESSION_PATH:
            if not self.enabled:
                # Without a token anyone (or any web page the owner visits) could
                # POST cookies for their own account and hijack this server.
                await _respond(
                    send, 404, b"/session is disabled unless MFP_MCP_AUTH_TOKEN is set."
                )
                return
            await save_pasted_session(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def build_http_app(token: Optional[str], stateless: bool = True):
    """Return the ASGI app serving the MCP tools behind the auth gate."""
    from mfp_mcp import server

    server.mcp.settings.stateless_http = stateless
    # FastMCP defaults to a localhost-only Host allowlist, which answers 421 to
    # any real hostname. Replace it: an explicit allowlist if configured,
    # otherwise off (the bearer token, not the Host header, gates access).
    hosts = [h.strip() for h in os.environ.get("MFP_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    server.mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(hosts), allowed_hosts=hosts
    )
    return BearerAuthMiddleware(
        _SessionRoutes(server.mcp.streamable_http_app(), enabled=token is not None), token
    )


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def serve_http(host: str, port: int) -> None:
    import uvicorn

    token = os.environ.get("MFP_MCP_AUTH_TOKEN") or None
    if token is None and not _env_flag("MFP_MCP_ALLOW_NO_AUTH", False):
        sys.exit(
            "Refusing to serve over HTTP without authentication: these tools can "
            "modify your MyFitnessPal diary.\n"
            "Set MFP_MCP_AUTH_TOKEN to a long random secret, e.g.\n"
            "    python -c 'import secrets; print(secrets.token_urlsafe(32))'\n"
            "(or MFP_MCP_ALLOW_NO_AUTH=1 if an authenticating proxy sits in front)."
        )
    if token is not None and len(token) < MIN_TOKEN_LENGTH:
        sys.exit(f"MFP_MCP_AUTH_TOKEN must be at least {MIN_TOKEN_LENGTH} characters.")

    app = build_http_app(token, stateless=_env_flag("MFP_MCP_STATELESS", True))
    logger.info(f"Serving MCP over HTTP on http://{host}:{port}/mcp")
    # access_log off: the `?token=` fallback would otherwise land in the logs.
    uvicorn.run(app, host=host, port=port, access_log=False, log_level="info")


# ---------------------------------------------------------------------------
# export-cookies
# ---------------------------------------------------------------------------


# The keychain dialog needs a person to type a password; the server's 5s
# default (meant for headless calls) expires before they can.
INTERACTIVE_KEYCHAIN_TIMEOUT = 120


def collect_browser_cookies(browser: str = "auto") -> Dict[str, str]:
    """Read MFP cookies from a local browser. Raises RuntimeError on failure."""
    from mfp_mcp import server

    browser = browser.lower().strip()
    cookies: Optional[Dict[str, str]] = None
    if browser == "auto":
        found = server.try_chromium_browsers_for_session_cookies(
            INTERACTIVE_KEYCHAIN_TIMEOUT)
        cookies = found[1] if found else None
        if cookies is None and sys.platform != "darwin":
            browser = "chrome"  # no keychain scan off macOS; try the default profile
    if cookies is None and browser in server._CHROMIUM_BROWSER_ALIASES:
        canonical = server._CHROMIUM_BROWSER_ALIASES[browser]
        if sys.platform == "darwin":
            cookies = server._try_extract_from_chromium_browser(
                f"{canonical} Safe Storage", INTERACTIVE_KEYCHAIN_TIMEOUT)
        elif browser in ("chrome", "chromium"):
            import browser_cookie3
            cookies = {c.name: c.value for c in browser_cookie3.chrome(
                domain_name=".myfitnesspal.com")}
    elif cookies is None and browser == "firefox":
        import browser_cookie3
        cookies = {c.name: c.value for c in browser_cookie3.firefox(
            domain_name=".myfitnesspal.com")}
    elif cookies is None and browser != "auto":
        raise RuntimeError(f"Unsupported browser: {browser!r}")

    if not cookies or not server._has_real_mfp_session(cookies):
        raise RuntimeError(
            "No MyFitnessPal session found. Log into myfitnesspal.com in that "
            "browser first (try --browser chrome|firefox|arc|... to pick one). "
            "On macOS, also check that you clicked Allow on the keychain "
            "prompt within 2 minutes."
        )
    return cookies


def verify_session(cookies: Dict[str, str]) -> None:
    """Make one real MFP request with these cookies. Raises on failure."""
    import cloudscraper
    import myfitnesspal
    import requests
    from datetime import date

    from mfp_mcp import server

    # Same workaround as get_mfp_client: cloudscraper's TLS fingerprint gets 403.
    cloudscraper.create_scraper = lambda **kw: requests.Session()
    client = myfitnesspal.Client(cookiejar=server.dict_to_cookiejar(cookies))
    client.get_date(date.today())


def export_cookies(browser: str) -> None:
    try:
        cookies = collect_browser_cookies(browser)
        verify_session(cookies)
    except Exception as e:
        sys.exit(f"export-cookies failed: {e}")
    print("Session verified against MyFitnessPal.", file=sys.stderr)
    print(
        "Set this as MFP_COOKIES_JSON on your server. It grants full access to "
        "your MyFitnessPal account: treat it like a password.",
        file=sys.stderr,
    )
    print(json.dumps(cookies, separators=(",", ":")))


def import_cookies() -> None:
    """Read pasted DevTools cookies (or export-cookies JSON) from stdin and save them."""
    if sys.stdin.isatty():
        print(
            "Paste the Cookie header (or JSON / DevTools rows), then press Ctrl-D:",
            file=sys.stderr,
        )
    try:
        cookies = parse_pasted_cookies(sys.stdin.read())
        from mfp_mcp import server

        if not server._has_real_mfp_session(cookies):
            raise RuntimeError(
                "No login cookie (...session-token) in the paste. It is HttpOnly: copy "
                "the Cookie request header from the Network tab, not document.cookie."
            )
        verify_session(cookies)
        server.save_cookies(cookies)
    except Exception as e:
        sys.exit(f"import-cookies failed: {e}")
    print(
        f"Session verified against MyFitnessPal and saved to {server.COOKIES_FILE}.",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mfp-mcp", description="MyFitnessPal MCP server")
    p.add_argument(
        "--transport", choices=["stdio", "streamable-http"],
        default=os.environ.get("MFP_MCP_TRANSPORT", "stdio"),
    )
    p.add_argument("--host", default=os.environ.get("MFP_MCP_HOST", "127.0.0.1"))
    # A string default is run through `type=int` by argparse, so a bad PORT in the
    # environment becomes a normal usage error instead of a traceback.
    p.add_argument(
        "--port", type=int,
        default=os.environ.get("MFP_MCP_PORT") or os.environ.get("PORT") or "8000",
    )
    sub = p.add_subparsers(dest="command")
    sub.add_parser(
        "import-cookies",
        help="read cookies pasted from browser DevTools (or export-cookies output) "
             "on stdin, check them, and save them for the server",
    )
    ec = sub.add_parser(
        "export-cookies",
        help="print MFP_COOKIES_JSON from a local browser session, for a headless server",
    )
    ec.add_argument("--browser", default="auto")
    return p


def run_cli(argv: Optional[List[str]] = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "import-cookies":
        import_cookies()
    elif args.command == "export-cookies":
        export_cookies(args.browser)
    elif args.transport == "streamable-http":
        serve_http(args.host, args.port)
    else:
        from mfp_mcp import server
        server.mcp.run()
