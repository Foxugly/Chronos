"""Sensitive-data scrubbing for Sentry events and spans (sentry-sdk 3).

sentry-sdk 3.0 removed its built-in ``EventScrubber`` (``sentry_sdk.scrubber``).
This self-contained replacement applies the same denylist (2.x
``DEFAULT_DENYLIST`` + ``DEFAULT_PII_DENYLIST``, key compared lower-cased with
``-`` read as ``_``) to the same places -- request headers, cookies and data,
``extra``, ``user``, breadcrumb data, stack-frame variables -- recursively
(2.x only looked one level deep), and goes further: ``contexts`` (response
headers, trace data…), every span (name, description, data, attributes), and
free text, where ``name=value`` secrets, URL credentials (``user:pass@``) and
secret-bearing path segments are masked.

``scrub_event`` cannot raise on any event shape (everything is type-checked);
were it to, sentry-sdk 3 drops the event, which is the safe side.
``scrub_span`` never raises: an exception in ``before_send_span`` makes the
SDK send the span *unmodified*, so it falls back to a span with no attributes.
"""

from __future__ import annotations

import re
from typing import Any

FILTERED = "[Filtered]"

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

# ``key=value`` pairs (query strings, headers in free text) whose key mentions
# a secret: the value is replaced, the key kept.
_SECRET_PAIR = re.compile(
    r"(?i)((?:^|[?&;\s,\"'])[\w.\-]*"
    r"(?:passw(?:or)?d|secret|token|api[_-]?key|auth|credential|session|csrf|xsrf|cookie|private[_-]?key)"
    r"[\w.\-]*=)[^&;\s,\"'#]*"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[\w.~+/=-]+")
# scheme://user:pass@host -> scheme://host
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/@\s\"'<>]+@")
_ROUTES = "|".join(re.escape(p) for p in sorted(SECRET_ROUTE_PREFIXES, key=len, reverse=True))
_SECRET_ROUTE = re.compile(rf"(?i)(/(?:{_ROUTES})/)(?!(?:done|key)/)[^/?#\s\"']+")
# A long opaque path segment (signed token, uidb64-token, random key): at least
# 20 URL-safe characters mixing letters and digits. A canonical UUID is a
# resource id, not a secret, and is kept.
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_OPAQUE_SEGMENT = re.compile(r"(?<=/)(?=[\w:.\-]*[A-Za-z])(?=[\w:.\-]*\d)[\w:.\-]{20,}(?=/|$|[?#\s\"'])")


def _normalize(key: str) -> str:
    return key.lower().replace("-", "_")


def is_sensitive_key(key: Any) -> bool:
    """True when ``key`` (or one of its dotted parts) is on the denylist."""
    if not isinstance(key, str):
        return False
    normalized = _normalize(key)
    return normalized in DENYLIST or any(part in DENYLIST for part in normalized.split("."))


def _mask_opaque(match: re.Match) -> str:
    return match.group(0) if _UUID.fullmatch(match.group(0)) else FILTERED


def scrub_text(text: Any) -> Any:
    """Mask secrets in free text or a URL: ``token=…`` pairs, bearer credentials,
    URL userinfo, and secret-bearing path segments."""
    if not isinstance(text, str):
        return text
    text = _URL_USERINFO.sub(r"\1", text)
    text = _SECRET_ROUTE.sub(lambda m: m.group(1) + FILTERED, text)
    text = _OPAQUE_SEGMENT.sub(_mask_opaque, text)
    text = _SECRET_PAIR.sub(r"\1" + FILTERED, text)
    return _BEARER.sub(lambda m: m.group(1) + " " + FILTERED, text)


def scrub_data(data: Any) -> None:
    """In place, at any depth: denylisted keys get ``[Filtered]``, strings are
    passed through ``scrub_text``."""
    if isinstance(data, dict):
        for key, value in data.items():
            if is_sensitive_key(key):
                data[key] = FILTERED
            elif isinstance(value, str):
                data[key] = scrub_text(value)
            else:
                scrub_data(value)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, str):
                data[index] = scrub_text(item)
            else:
                scrub_data(item)


def _frames(event: dict[str, Any]):
    stacks = []
    if isinstance(event.get("stacktrace"), dict):
        stacks.append(event["stacktrace"])
    for section in ("exception", "threads"):
        container = event.get(section)
        values = container.get("values") if isinstance(container, dict) else None
        for value in values if isinstance(values, list) else []:
            if isinstance(value, dict) and isinstance(value.get("stacktrace"), dict):
                stacks.append(value["stacktrace"])
    for stack in stacks:
        frames = stack.get("frames")
        for frame in frames if isinstance(frames, list) else []:
            if isinstance(frame, dict):
                yield frame


def scrub_event(event: dict[str, Any]) -> dict[str, Any]:
    """The 2.x ``EventScrubber`` places, recursively, plus contexts and spans.
    Returns ``event``."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("headers", "cookies", "data", "env"):
            scrub_data(request.get(key))
        for key in ("url", "query_string"):
            if key in request:
                request[key] = scrub_text(request[key])
    scrub_data(event.get("extra"))
    # contexts.response.headers (Set-Cookie…), contexts.trace.data, etc.
    scrub_data(event.get("contexts"))
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("ip_address", None)
        scrub_data(user)
    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else breadcrumbs
    for crumb in values if isinstance(values, list) else []:
        if isinstance(crumb, dict):
            if "message" in crumb:
                crumb["message"] = scrub_text(crumb["message"])
            scrub_data(crumb.get("data"))
    for frame in _frames(event):
        scrub_data(frame.get("vars"))
    spans = event.get("spans")
    for span in spans if isinstance(spans, list) else []:
        if isinstance(span, dict):
            for key in ("name", "description"):
                if key in span:
                    span[key] = scrub_text(span[key])
            for key in ("data", "attributes"):
                if isinstance(span.get(key), dict):
                    span[key] = {k: _scrub_attribute(k, v) for k, v in span[key].items()}
                    scrub_data(span[key])
    return event


def _scrub_attribute(key: Any, value: Any) -> Any:
    if is_sensitive_key(key) or key in PII_SPAN_ATTRIBUTES:
        return FILTERED
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [scrub_text(item) for item in value]
    return value


def scrub_span(span: dict[str, Any]) -> dict[str, Any]:
    """``before_send_span``: same rules on the span name and attributes."""
    try:
        span["name"] = scrub_text(span.get("name"))
        attributes = span.get("attributes")
        if isinstance(attributes, dict):
            span["attributes"] = {k: _scrub_attribute(k, v) for k, v in attributes.items()}
        return span
    except Exception:  # noqa: BLE001 -- the SDK would send the raw span instead
        return {"name": FILTERED, "attributes": {}}
