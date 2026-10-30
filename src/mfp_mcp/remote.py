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
import hmac
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

from mcp.server.transport_security import TransportSecuritySettings

logger = logging.getLogger("mfp_mcp")

MIN_TOKEN_LENGTH = 16
HEALTH_PATH = "/healthz"
# Tools that only make sense next to a local browser.
LOCAL_ONLY_TOOLS = ("refresh_browser_cookies",)


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
        if scope["type"] != "http":
            await self.app(scope, receive, send)
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


async def _respond(send, status: int, body: bytes, extra_headers=()):
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
            *extra_headers,
        ],
    })
    await send({"type": "http.response.body", "body": body})


def build_http_app(token: Optional[str], stateless: bool = True):
    """Return the ASGI app serving the MCP tools behind the auth gate."""
    from mfp_mcp import server

    for name in LOCAL_ONLY_TOOLS:
        try:
            server.mcp.remove_tool(name)
        except Exception:  # already removed (e.g. build_http_app called twice)
            pass
    server.mcp.settings.stateless_http = stateless
    # FastMCP defaults to a localhost-only Host allowlist, which answers 421 to
    # any real hostname. Replace it: an explicit allowlist if configured,
    # otherwise off (the bearer token, not the Host header, gates access).
    hosts = [h.strip() for h in os.environ.get("MFP_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    server.mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(hosts), allowed_hosts=hosts
    )
    return BearerAuthMiddleware(server.mcp.streamable_http_app(), token)


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


def collect_browser_cookies(browser: str = "auto") -> Dict[str, str]:
    """Read MFP cookies from a local browser. Raises RuntimeError on failure."""
    from mfp_mcp import server

    browser = browser.lower().strip()
    cookies: Optional[Dict[str, str]] = None
    if browser == "auto":
        found = server.try_chromium_browsers_for_session_cookies()
        cookies = found[1] if found else None
        if cookies is None and sys.platform != "darwin":
            browser = "chrome"  # no keychain scan off macOS; try the default profile
    if cookies is None and browser in server._CHROMIUM_BROWSER_ALIASES:
        canonical = server._CHROMIUM_BROWSER_ALIASES[browser]
        if sys.platform == "darwin":
            cookies = server._try_extract_from_chromium_browser(f"{canonical} Safe Storage")
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
            "browser first (try --browser chrome|firefox|arc|... to pick one)."
        )
    return cookies


def export_cookies(browser: str) -> None:
    try:
        cookies = collect_browser_cookies(browser)
    except Exception as e:
        sys.exit(f"export-cookies failed: {e}")
    print(
        "Set this as MFP_COOKIES_JSON on your server. It grants full access to "
        "your MyFitnessPal account: treat it like a password.",
        file=sys.stderr,
    )
    print(json.dumps(cookies, separators=(",", ":")))


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
    p.add_argument(
        "--port", type=int,
        default=int(os.environ.get("MFP_MCP_PORT") or os.environ.get("PORT") or 8000),
    )
    sub = p.add_subparsers(dest="command")
    ec = sub.add_parser(
        "export-cookies",
        help="print MFP_COOKIES_JSON from a local browser session, for a headless server",
    )
    ec.add_argument("--browser", default="auto")
    return p


def run_cli(argv: Optional[List[str]] = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "export-cookies":
        export_cookies(args.browser)
    elif args.transport == "streamable-http":
        serve_http(args.host, args.port)
    else:
        from mfp_mcp import server
        server.mcp.run()
