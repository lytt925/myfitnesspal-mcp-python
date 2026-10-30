"""Turn cookies pasted from browser DevTools (F12) into a {name: value} dict.

Accepted shapes, detected automatically:

1. A ``Cookie`` request header: ``a=1; b=2`` (optionally prefixed ``Cookie:``).
   Network tab -> any request to www.myfitnesspal.com -> Request Headers.
2. A JSON object ``{"a": "1"}`` (or ``{"cookies": {...}}``, our own format).
3. A JSON array of ``{"name": ..., "value": ...}`` (cookie-manager extensions).
4. Rows copied from Application -> Cookies: tab-separated, name then value.

Note: ``document.cookie`` / the Console cannot see HttpOnly cookies, and
MyFitnessPal's login cookie is HttpOnly, so use one of the sources above.
"""

import json
from typing import Dict

MAX_COOKIES = 200


class CookiePasteError(ValueError):
    """The pasted text could not be understood as cookies."""


def parse_pasted_cookies(text: str) -> Dict[str, str]:
    text = (text or "").strip()
    if not text:
        raise CookiePasteError("Nothing was pasted.")

    cookies = _parse_json(text) if text[0] in "{[" else None
    if cookies is None:
        cookies = _parse_table(text) if "\t" in text else _parse_header(text)

    cookies = {k: v for k, v in cookies.items() if k and v}
    if not cookies:
        raise CookiePasteError("No cookies found in the pasted text.")
    if len(cookies) > MAX_COOKIES:
        raise CookiePasteError(f"Too many cookies ({len(cookies)}); expected a few dozen.")
    return cookies


def _parse_json(text: str):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CookiePasteError(f"Looks like JSON but does not parse: {e.msg}") from None
    if isinstance(data, dict) and isinstance(data.get("cookies"), (dict, list)):
        data = data["cookies"]
    if isinstance(data, dict):
        return {str(k).strip(): str(v).strip() for k, v in data.items()}
    if isinstance(data, list) and all(isinstance(i, dict) for i in data):
        return {
            str(i["name"]).strip(): str(i["value"]).strip()
            for i in data
            if "name" in i and "value" in i
        }
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
