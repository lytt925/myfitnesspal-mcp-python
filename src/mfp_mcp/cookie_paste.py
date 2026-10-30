"""Turn cookies pasted from browser DevTools (F12) into a {name: value} dict.

Accepted shapes, detected automatically:

1. A ``Cookie`` request header: ``a=1; b=2`` (optionally prefixed ``Cookie:``).
   Network tab -> any request to www.myfitnesspal.com -> Request Headers.
   A whole block of request headers, or a "Copy as cURL" command, also works:
   only the cookie part is used.
2. A JSON object ``{"a": "1"}`` (or ``{"cookies": {...}}``, our own format).
3. A JSON array of ``{"name": ..., "value": ...}`` (cookie-manager extensions).
4. Rows copied from Application -> Cookies: tab-separated, name then value.
"""

import json
import re
from typing import Dict

MAX_COOKIES = 200

# A "cookie: ..." line inside a pasted block of request headers.
_COOKIE_HEADER_LINE = re.compile(r"^\s*cookie\s*:\s*(.*)$", re.IGNORECASE | re.MULTILINE)
# cURL: -b 'a=1; b=2' / --cookie "..." / -H 'cookie: a=1; b=2'
_CURL_COOKIE_ARG = re.compile(r"""(?:^|\s)(?:-b|--cookie)\s+(?P<q>['"])(?P<v>.*?)(?P=q)""", re.DOTALL)
_CURL_COOKIE_HEADER = re.compile(
    r"""-H\s+(?P<q>['"])\s*cookie\s*:\s*(?P<v>.*?)(?P=q)""", re.IGNORECASE | re.DOTALL
)


class CookiePasteError(ValueError):
    """The pasted text could not be understood as cookies."""


def parse_pasted_cookies(text: str) -> Dict[str, str]:
    # Pasting from web pages and chat apps drags in BOMs and non-breaking spaces.
    text = (text or "").replace("\ufeff", "").replace("\u00a0", " ").strip()
    if not text:
        raise CookiePasteError("Nothing was pasted.")

    cookies = _parse_json(text) if text[0] in "{[" else None
    if cookies is None:
        if "\t" in text:
            cookies = _parse_table(text)
        else:
            cookies = _parse_header(_cookie_part(text))

    cookies = {k: v for k, v in cookies.items() if k and v}
    if not cookies:
        raise CookiePasteError("No cookies found in the pasted text.")
    if len(cookies) > MAX_COOKIES:
        raise CookiePasteError(f"Too many cookies ({len(cookies)}); expected a few dozen.")
    return cookies


def _cookie_part(text: str) -> str:
    """Narrow a paste to just the cookie string, if it is a header block or cURL."""
    if text.lower().startswith("curl"):
        match = _CURL_COOKIE_ARG.search(text) or _CURL_COOKIE_HEADER.search(text)
        if match:
            return match.group("v")
        raise CookiePasteError("That cURL command contains no cookies (-b / -H 'cookie: ...').")
    header_values = _COOKIE_HEADER_LINE.findall(text)
    if header_values:
        return "; ".join(header_values)
    return text


def _scalar(value):
    """Cookie values are strings; accept numbers/booleans, drop null and containers."""
    if isinstance(value, (dict, list)) or value is None:
        return None
    return str(value).strip()


def _parse_json(text: str):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CookiePasteError(f"Looks like JSON but does not parse: {e.msg}") from None
    if isinstance(data, dict) and isinstance(data.get("cookies"), (dict, list)):
        data = data["cookies"]
    if isinstance(data, dict):
        pairs = ((k, _scalar(v)) for k, v in data.items())
        return {str(k).strip(): v for k, v in pairs if v is not None}
    if isinstance(data, list) and all(isinstance(i, dict) for i in data):
        pairs = ((i.get("name"), _scalar(i.get("value"))) for i in data)
        return {str(n).strip(): v for n, v in pairs if n is not None and v is not None}
    raise CookiePasteError("JSON must be an object or a list of {name, value} objects.")


def _parse_table(text: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) >= 2:
            cookies[cols[0].strip()] = cols[1].strip()
    return cookies


def _parse_header(text: str) -> Dict[str, str]:
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:"):]
    cookies: Dict[str, str] = {}
    for part in text.replace("\n", ";").split(";"):
        name, sep, value = part.partition("=")  # values (JWT/base64) may contain "="
        if sep:
            cookies[name.strip()] = value.strip().strip('"')
    return cookies
