"""Sensitive-key scrubbing for Sentry events and spans (sentry-sdk 3).

sentry-sdk 3.0 removed its built-in ``EventScrubber`` (``sentry_sdk.scrubber``).
This self-contained replacement applies the same denylist (2.x
``DEFAULT_DENYLIST`` + ``DEFAULT_PII_DENYLIST``, key compared lower-cased with
``-`` read as ``_``) to the same places -- request headers, cookies and data,
``extra``, ``user``, breadcrumb data, stack-frame variables, span data --
recursively (2.x only looked one level deep), and to the name and attributes
of streamed spans for ``before_send_span``.

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

# ``key=value`` pairs (query strings, headers in free text) whose key mentions
# a secret: the value is replaced, the key kept.
_SECRET_PAIR = re.compile(
    r"(?i)((?:^|[?&;\s,\"'])[\w.\-]*"
    r"(?:passw(?:or)?d|secret|token|api[_-]?key|auth|credential|session|csrf|xsrf|cookie|private[_-]?key)"
    r"[\w.\-]*=)[^&;\s,\"'#]*"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[\w.~+/=-]+")


def _normalize(key: str) -> str:
    return key.lower().replace("-", "_")


def is_sensitive_key(key: Any) -> bool:
    """True when ``key`` (or one of its dotted parts) is on the denylist."""
    if not isinstance(key, str):
        return False
    normalized = _normalize(key)
    return normalized in DENYLIST or any(part in DENYLIST for part in normalized.split("."))


def scrub_text(text: Any) -> Any:
    """Mask secret values in free text: ``token=…`` pairs and bearer credentials."""
    if not isinstance(text, str):
        return text
    return _BEARER.sub(lambda m: m.group(1) + " " + FILTERED, _SECRET_PAIR.sub(r"\1" + FILTERED, text))


def scrub_data(data: Any) -> None:
    """Replace in place, at any depth, the value of every denylisted key."""
    if isinstance(data, dict):
        for key, value in data.items():
            if is_sensitive_key(key):
                data[key] = FILTERED
            else:
                scrub_data(value)
    elif isinstance(data, list):
        for item in data:
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
    """What the 2.x ``EventScrubber`` did, recursively. Returns ``event``."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("headers", "cookies", "data", "env"):
            scrub_data(request.get(key))
        if isinstance(request.get("query_string"), str):
            request["query_string"] = scrub_text(request["query_string"])
    scrub_data(event.get("extra"))
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("ip_address", None)
        scrub_data(user)
    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else breadcrumbs
    for crumb in values if isinstance(values, list) else []:
        if isinstance(crumb, dict):
            scrub_data(crumb.get("data"))
    for frame in _frames(event):
        scrub_data(frame.get("vars"))
    spans = event.get("spans")
    for span in spans if isinstance(spans, list) else []:
        if isinstance(span, dict):
            scrub_data(span.get("data"))
    return event


def _scrub_attribute(key: str, value: Any) -> Any:
    if is_sensitive_key(key) or key in PII_SPAN_ATTRIBUTES:
        return FILTERED
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [scrub_text(item) for item in value]
    return value


def scrub_span(span: dict[str, Any]) -> dict[str, Any]:
    """``before_send_span``: same denylist on the span name and attributes."""
    try:
        span["name"] = scrub_text(span.get("name"))
        attributes = span.get("attributes")
        if isinstance(attributes, dict):
            span["attributes"] = {k: _scrub_attribute(k, v) for k, v in attributes.items()}
        return span
    except Exception:  # noqa: BLE001 -- the SDK would send the raw span instead
        return {"name": FILTERED, "attributes": {}}
