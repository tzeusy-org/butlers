"""The shared transport-neutral opaque keyset cursor codec.

The canonical dashboard API facade is ``butlers.api.pagination``. This module
provides its one transport-neutral codec so core queries share the token format
without depending on the API layer.

The cursor is the keyset position of the last row returned, as a tuple of
``datetime`` / ``UUID`` / ``str`` / ``int`` / ``float`` / ``bool`` / ``None`` values.
The decoder hands back the same types it was given, so callers never re-parse.

Token format: ``base64url(json({"v": 2, "s": receiver_scope, "k": [...]}))``
followed by ``"."`` and a short checksum.
The checksum is not authentication; a keyset position grants no access (every query
stays scoped and binds the values as parameters). It makes a hand-edited or truncated
token fail loudly instead of seeking to a wrong position.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import types
from datetime import datetime
from typing import Any, get_args
from uuid import UUID

#: Bump when the token layout changes; older tokens then fail with a 400.
CURSOR_VERSION = 2

_CHECKSUM_BYTES = 4
_TAG_DATETIME = "t"
_TAG_UUID = "u"

CursorValue = datetime | UUID | str | int | float | bool | None


class InvalidCursorError(ValueError):
    """A malformed, tampered, wrong-version, or wrong-shape opaque cursor."""


def _checksum(body: bytes) -> str:
    return hashlib.blake2s(body, digest_size=_CHECKSUM_BYTES).hexdigest()


def _pack(value: CursorValue) -> Any:
    if isinstance(value, datetime):
        return [_TAG_DATETIME, value.isoformat()]
    if isinstance(value, UUID):
        return [_TAG_UUID, str(value)]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("cursor values must be finite")
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported cursor value type: {type(value).__name__}")


def _unpack(raw: Any) -> CursorValue:
    if isinstance(raw, list):
        if len(raw) != 2 or not isinstance(raw[1], str):
            raise ValueError("bad tagged value")
        if raw[0] == _TAG_DATETIME:
            return datetime.fromisoformat(raw[1])
        if raw[0] == _TAG_UUID:
            return UUID(raw[1])
        raise ValueError("unknown value tag")
    if raw is None or isinstance(raw, str | int | float | bool):
        return raw
    raise ValueError("bad value")


def encode_cursor(values: tuple[CursorValue, ...], *, scope: str) -> str:
    """Encode a keyset position into a URL-safe, versioned, checksummed token."""
    payload = {"v": CURSOR_VERSION, "s": scope, "k": [_pack(v) for v in values]}
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    token = base64.urlsafe_b64encode(body).decode().rstrip("=")
    return f"{token}.{_checksum(body)}"


def decode_cursor(
    cursor: str, shape: tuple[type | types.UnionType, ...] | None = None, *, scope: str
) -> tuple[CursorValue, ...]:
    """Decode a token from :func:`encode_cursor`; raise :class:`InvalidCursorError` otherwise.

    ``scope`` is fixed by the server receiver, never taken from the caller or token.
    It distinguishes endpoints even when their keyset types match. ``shape`` checks
    exact value types, including intentional union members, without bool/int coercion.
    This scope is not authorization: every query retains its normal access controls.
    """
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[0-9a-f]{8}", cursor):
            raise ValueError("noncanonical cursor spelling")
        token, checksum = cursor.split(".")
        body = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(body).decode().rstrip("=") != token:
            raise ValueError("noncanonical base64")
        if not hmac.compare_digest(checksum, _checksum(body)):
            raise ValueError("checksum mismatch")
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
            raise ValueError("unsupported cursor version")
        if payload.get("s") != scope:
            raise ValueError("cursor belongs to another receiver")
        keys = payload["k"]
        if not isinstance(keys, list):
            raise ValueError("bad key list")
        values = tuple(_unpack(raw) for raw in keys)
        if shape is not None and (
            len(values) != len(shape)
            or not all(
                type(value) in (get_args(expected) or (expected,))
                for value, expected in zip(values, shape, strict=True)
            )
        ):
            raise ValueError("cursor does not match this endpoint")
        return values
    except (ValueError, TypeError, KeyError, binascii.Error) as exc:
        raise InvalidCursorError() from exc
