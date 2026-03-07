"""Shared utility functions for the butlers package."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime


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
