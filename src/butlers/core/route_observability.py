"""Content-blind observability helpers for conceptual route processing."""

from __future__ import annotations

import hashlib
import re
from typing import Any

_SAFE_RUNTIME_FAILURE_CLASS_RE = re.compile(
    r"^(?P<class>[A-Za-z_][A-Za-z0-9_]{0,63}(?:Error|Exception)):",
)


def opaque_route_ref(value: Any) -> str:
    """Return a stable non-reversible correlation for a route identifier."""
    return hashlib.sha256(str(value or "unknown").encode("utf-8")).hexdigest()[:16]


def safe_runtime_failure_class(error: Any) -> str:
    """Return a bounded diagnostic class without retaining runtime error text."""
    if isinstance(error, BaseException):
        class_name = type(error).__name__
        return class_name if len(class_name) <= 64 else "runtime_unsuccessful"
    if isinstance(error, str):
        match = _SAFE_RUNTIME_FAILURE_CLASS_RE.match(error)
        if match is not None:
            return match.group("class")
    return "runtime_unsuccessful"


def has_conceptual_route_context(route_envelope: Any) -> bool:
    """Return whether a raw or validated-shaped envelope carries conceptual context."""
    if not isinstance(route_envelope, dict):
        return False
    input_payload = route_envelope.get("input")
    if not isinstance(input_payload, dict):
        return False
    context = input_payload.get("context")
    return isinstance(context, dict) and isinstance(context.get("conceptual_message"), dict)
