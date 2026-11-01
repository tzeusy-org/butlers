"""Keep credential-bearing query parameters out of dashboard HTTP server spans.

The OTel ASGI instrumentor records the request query in ``http.url`` (old HTTP
semconv) and ``url.query`` (new semconv) and only redacts a few cloud-signature
parameters. Its ``excluded_urls`` list is matched against the path alone, so an
exclusion cannot follow every path a credential may ride on (a trailing slash, a
dot segment, a typo'd route that falls through to owner auth). This hook scrubs
sensitive query values from every traced URL instead, whatever the path.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, unquote_plus

REDACTED = "REDACTED"
# Matched against the decoded parameter name, the same name Starlette resolves.
_SENSITIVE_NAME = re.compile(
    r"token|key|secret|password|passwd|signature|credential|^(?:code|sig|auth)$", re.IGNORECASE
)


def redact_query(query: str) -> str:
    """Return ``query`` with every sensitive parameter's value replaced."""
    parts = query.split("&")
    for index, part in enumerate(parts):
        name, sep, _value = part.partition("=")
        if sep and _SENSITIVE_NAME.search(unquote_plus(name)):
            parts[index] = f"{name}={REDACTED}"
    return "&".join(parts)


def redact_span_query(span: Any, scope: dict[str, Any]) -> None:
    """``server_request_hook``: overwrite the span's query-bearing URL attributes."""
    raw = scope.get("query_string") or b""
    query = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    redacted = redact_query(query)
    if redacted == query or not span.is_recording():
        return
    attributes = getattr(span, "attributes", None) or {}
    if isinstance(attributes.get("url.query"), str):
        span.set_attribute("url.query", redacted)
    url = attributes.get("http.url")
    if isinstance(url, str):
        span.set_attribute("http.url", url.split("?", 1)[0] + "?" + unquote(redacted))
