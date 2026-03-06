"""Shared ID generation utilities."""

import secrets
import uuid
from datetime import UTC, datetime


def generate_uuid7() -> uuid.UUID:
    """Generate a UUIDv7 (time-ordered, RFC 9562) value.

    Uses stdlib uuid.uuid7() on Python 3.14+ with a manual fallback for earlier versions.
    """
    uuid7_fn = getattr(uuid, "uuid7", None)
    if callable(uuid7_fn):
        return uuid7_fn()

    timestamp_ms = int(datetime.now(UTC).timestamp() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def generate_uuid7_str() -> str:
    """Generate a UUIDv7 string."""
    return str(generate_uuid7())
