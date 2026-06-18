"""Shared utility functions for the butlers package."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any


def generate_uuid7_string() -> str:
    """Generate a UUIDv7 string with stdlib support and deterministic fallback."""
    uuid7_fn = getattr(uuid, "uuid7", None)
    if callable(uuid7_fn):
        return str(uuid7_fn())

    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return str(uuid.UUID(int=value))


def coerce_request_id(raw_request_id: Any) -> str:
    """Coerce a raw request-id value to a canonical UUIDv7 string.

    Accepts ``None``, empty string, a valid UUIDv7 string, or a UUID object.
    Returns the input unchanged when it is already a valid UUIDv7; generates a
    fresh UUIDv7 for any other input (None, empty, wrong version, unparsable).

    This helper is the canonical implementation used by both ``MessagePipeline``
    and ``core_tools._switchboard`` so the logic lives in ``core`` rather than
    being duplicated in the module layer.
    """
    if raw_request_id in (None, ""):
        return generate_uuid7_string()
    text = str(raw_request_id).strip()
    if not text:
        return generate_uuid7_string()
    try:
        parsed = uuid.UUID(text)
    except ValueError:
        return generate_uuid7_string()
    if parsed.version != 7:
        return generate_uuid7_string()
    return str(parsed)
