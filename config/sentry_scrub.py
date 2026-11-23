"""Sensitive-data scrubbing for Sentry events and spans (sentry-sdk 3).

sentry-sdk 3.0 removed its built-in ``EventScrubber`` (``sentry_sdk.scrubber``).
This self-contained replacement applies the same denylist (2.x
``DEFAULT_DENYLIST`` + ``DEFAULT_PII_DENYLIST``, key compared lower-cased with
``-`` read as ``_``) to the same places -- request headers, cookies and data,
``extra``, ``user``, breadcrumb data, stack-frame variables -- recursively
(2.x only looked one level deep), and goes further: ``contexts`` (response
headers, trace data…), exception messages, log messages, every span (name,
description, data, attributes), and free text.

In free text every URL (absolute or a ``/path``) is rebuilt with ``urlsplit``:
no userinfo (``user:pass@``), the segment after a secret-bearing route
(password reset, invitation, magic link…) and long opaque segments masked,
query values masked when their key is sensitive or their value opaque, no
fragment. ``name=value`` secrets and bearer credentials are masked too.

It fails closed and never gates on type: non-string "text" (bytes, lists,
tuples, sets) goes through the same scrubber, unknown value types and anything
nested deeper than ``MAX_DEPTH`` become ``[Filtered]``. Query strings are
decoded, checked chunk by chunk (opaque names and value-less chunks included)
and re-encoded.
``scrub_event`` cannot raise on any event shape; were it to, sentry-sdk 3 drops
the event, which is the safe side. ``scrub_span`` never raises: an exception
in ``before_send_span`` makes the SDK send the span *unmodified*, so it falls
back to a span with no attributes.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

FILTERED = "[Filtered]"
MAX_DEPTH = 20

#: sentry-sdk 2.x ``DEFAULT_DENYLIST`` + ``DEFAULT_PII_DENYLIST``.
DENYLIST = frozenset(
    term.lower().replace("-", "_")
    for term in (
        "password", "passwd", "secret", "api_key", "apikey", "auth", "credentials",
        "mysql_pwd", "privatekey", "private_key", "token", "session", "csrftoken",
        "sessionid", "x_csrftoken", "set_cookie", "cookie", "authorization",
        "proxy-authorization", "x_api_key", "aiohttp_session", "connect.sid",
        "csrf_token", "csrf", "_csrf", "_csrf_token", "PHPSESSID", "_session",
        "symfony", "user_session", "_xsrf", "XSRF-TOKEN",
        # PII (send_default_pii=False)
        "x_forwarded_for", "x_real_ip", "ip_address", "remote_addr",
    )
)

#: Span attributes that identify a person; never sent.
PII_SPAN_ATTRIBUTES = frozenset({
    "user.email", "user.name", "user.username", "user.ip_address",
    "client.address", "network.peer.address",
})

#: Path segments after which the next segment is a secret (one-time links:
#: password reset, e-mail confirmation, activation, invitation, magic link,
#: share link). Django's own and allauth's routes included.
SECRET_ROUTE_PREFIXES = (
    "reset", "password/reset/key", "password-reset", "reset-password",
    "confirm-email", "confirm", "activate", "activation", "verify", "verification",
    "invitation", "invite", "magic", "magic-link", "login/link", "unsubscribe",
    "share", "partage", "token", "key",
)

_SECRET_TERMS = (
    r"passw(?:or)?d|secret|token|api[_-]?key|auth|credential|session|csrf|xsrf"
    r"|cookie|private[_-]?key"
)
# Query-string keys: the same, plus one-time codes and URL signatures.
_SECRET_NAME = re.compile(rf"(?i)(?:{_SECRET_TERMS}|signature|^sig$|code|otp|nonce)")
# ``key=value`` pairs outside URLs (headers or settings quoted in a message).
_SECRET_PAIR = re.compile(
    rf"(?i)((?:^|[?&;\s,\"'])[\w.\-]*(?:{_SECRET_TERMS})[\w.\-]*=)[^&;\s,\"'#]*"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[\w.~+/=-]+")
_ABSOLUTE_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s\"'<>]+")
# A path in free text: "/" not preceded by a word character, ":" "/" or ".".
_RELATIVE_URL = re.compile(r"(?<![\w:/.\]])/(?!/)[^\s\"'<>]*")
_HOST_PORT = re.compile(r"(?:\[[0-9a-fA-F:.]+\]|[\w.\-]+)(?::\d{1,5})?")
_ROUTES = "|".join(re.escape(p) for p in sorted(SECRET_ROUTE_PREFIXES, key=len, reverse=True))
_SECRET_ROUTE = re.compile(rf"(?i)(/(?:{_ROUTES})/)(?!(?:done|key)/)[^/]+")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_OPAQUE_CHARS = re.compile(r"[\w.~:+=%\-]{16,}")
# A decoded query name or value may hold any character (base64 "/" "+").
_OPAQUE_QUERY = re.compile(r"\S{16,}")


def _normalize(key: str) -> str:
    return key.lower().replace("-", "_")


def is_sensitive_key(key: Any) -> bool:
    """True when ``key`` (or one of its dotted parts) is on the denylist."""
    if not isinstance(key, str):
        return False
    normalized = _normalize(key)
    return normalized in DENYLIST or any(part in DENYLIST for part in normalized.split("."))


def is_opaque(value: str, charset: re.Pattern = _OPAQUE_CHARS) -> bool:
    """A token-looking value: 16+ URL-safe characters with a digit or mixed
    case (a signed token, a random key, uidb64-token). A UUID is a resource id
    and is not opaque."""
    if not charset.fullmatch(value) or _UUID.fullmatch(value):
        return False
    return (any(c.isdigit() for c in value)
            or (any(c.islower() for c in value) and any(c.isupper() for c in value)))


def _scrub_path(path: str) -> str:
    path = _SECRET_ROUTE.sub(lambda m: m.group(1) + FILTERED, path)
    return "/".join(FILTERED if is_opaque(seg) else seg for seg in path.split("/"))


def _scrub_query(query: str) -> str:
    """Decoded with ``parse_qsl`` (so ``tok%65n`` is read as ``token``), every
    chunk -- value-less ones included -- checked by name and value, then
    re-encoded: percent-encoding cannot smuggle a secret through."""
    pairs = []
    for name, value in parse_qsl(query, keep_blank_values=True):
        if is_opaque(name, _OPAQUE_QUERY):
            name, value = FILTERED, FILTERED if value else ""
        elif (is_sensitive_key(name) or _SECRET_NAME.search(name)
              or is_opaque(value, _OPAQUE_QUERY)):
            value = FILTERED
        pairs.append((name, value))
    return urlencode(pairs, quote_via=quote, safe="[]")


def scrub_url(url: str) -> str:
    """Rebuild a URL (absolute or a path) without userinfo, fragment or secrets."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return FILTERED
    netloc = parts.netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    elif netloc and not _HOST_PORT.fullmatch(netloc):
        netloc = FILTERED
    return urlunsplit((parts.scheme, netloc, _scrub_path(parts.path),
                       _scrub_query(parts.query) if parts.query else "", ""))


def scrub_text(text: Any) -> Any:
    """Mask secrets in free text: URLs (see ``scrub_url``), ``token=…`` pairs,
    bearer/basic credentials."""
    if not isinstance(text, str):
        return scrub_value(text)
    text = _ABSOLUTE_URL.sub(lambda m: scrub_url(m.group(0)), text)
    text = _RELATIVE_URL.sub(lambda m: scrub_url(m.group(0)), text)
    text = _SECRET_PAIR.sub(r"\1" + FILTERED, text)
    return _BEARER.sub(lambda m: m.group(1) + " " + FILTERED, text)


def scrub_value(value: Any, _depth: int = 0) -> Any:
    """Scrubbed copy of ``value``: denylisted keys get ``[Filtered]``, strings
    go through ``scrub_text``. Fails closed (unknown types, too deep)."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return scrub_text(value)
    if _depth >= MAX_DEPTH:
        return FILTERED
    if isinstance(value, dict):
        return {
            key: FILTERED if is_sensitive_key(key) else scrub_value(item, _depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_scrub_item(item, _depth + 1) for item in value]
    # bytes, objects…: never sent as they are.
    return FILTERED


def _scrub_item(item: Any, depth: int) -> Any:
    # A ``(name, value)`` pair (query-string or header lists): judge the name.
    if (isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[0], str)
            and is_sensitive_key(item[0])):
        return [item[0], FILTERED]
    return scrub_value(item, depth)


def _scrub_in(container: Any, key: str) -> None:
    if isinstance(container, dict) and key in container:
        container[key] = scrub_value(container[key])


def _exception_like(event: dict[str, Any]):
    for section in ("exception", "threads"):
        container = event.get(section)
        values = container.get("values") if isinstance(container, dict) else container
        for value in values if isinstance(values, list) else []:
            if isinstance(value, dict):
                yield value


def _frames(event: dict[str, Any]):
    stacks = [event.get("stacktrace")]
    stacks += [value.get("stacktrace") for value in _exception_like(event)]
    for stack in stacks:
        frames = stack.get("frames") if isinstance(stack, dict) else None
        for frame in frames if isinstance(frames, list) else []:
            if isinstance(frame, dict):
                yield frame


def scrub_event(event: dict[str, Any]) -> dict[str, Any]:
    """The 2.x ``EventScrubber`` places, recursively, plus contexts, exception
    and log messages, and spans. Returns ``event``."""
    request = event.get("request")
    for key in ("headers", "cookies", "data", "env", "url", "query_string"):
        _scrub_in(request, key)
    for key in ("extra", "contexts", "message"):
        _scrub_in(event, key)
    _scrub_in(event, "tags")
    logentry = event.get("logentry")
    for key in ("message", "formatted", "params"):
        _scrub_in(logentry, key)
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("ip_address", None)
        event["user"] = scrub_value(user)
    for value in _exception_like(event):
        _scrub_in(value, "value")
    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else breadcrumbs
    for crumb in values if isinstance(values, list) else []:
        for key in ("message", "data"):
            _scrub_in(crumb, key)
    for frame in _frames(event):
        _scrub_in(frame, "vars")
    spans = event.get("spans")
    for span in spans if isinstance(spans, list) else []:
        if isinstance(span, dict):
            for key in ("name", "description"):
                _scrub_in(span, key)
            for key in ("data", "attributes"):
                if isinstance(span.get(key), dict):
                    span[key] = {k: _scrub_attribute(k, v) for k, v in span[key].items()}
    return event


def _scrub_attribute(key: Any, value: Any) -> Any:
    if is_sensitive_key(key) or key in PII_SPAN_ATTRIBUTES:
        return FILTERED
    return scrub_value(value)


def scrub_span(span: dict[str, Any]) -> dict[str, Any]:
    """``before_send_span``: same rules on the span name and attributes."""
    try:
        name = span.get("name")
        span["name"] = scrub_text(name) if isinstance(name, str) else FILTERED
        attributes = span.get("attributes")
        if isinstance(attributes, dict):
            span["attributes"] = {k: _scrub_attribute(k, v) for k, v in attributes.items()}
        return span
    except Exception:  # noqa: BLE001 -- the SDK would send the raw span instead
        return {"name": FILTERED, "attributes": {}}
