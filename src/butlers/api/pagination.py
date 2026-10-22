"""One opaque keyset cursor codec and one ``Page[T]`` envelope for dashboard lists.

Every cursor-paginated dashboard endpoint builds its ``next_cursor`` with
:func:`encode_cursor` and reads the caller's with :func:`decode_cursor`; every one
returns :class:`Page` (or a subclass that adds sibling fields). See
``docs/api_and_protocols/response-conventions.md`` (Cursor pagination).

The cursor is the keyset position of the last row returned, as a tuple of
``datetime`` / ``UUID`` / ``str`` / ``int`` / ``float`` / ``bool`` / ``None`` values.
The decoder hands back the same types it was given, so callers never re-parse.

Token format: ``base64url(json({"v": 1, "k": [...]}))`` + ``"."`` + a short checksum.
The checksum is not authentication; a keyset position grants no access (every query
stays scoped and binds the values as parameters). It makes a hand-edited or truncated
token fail loudly with a 400 instead of seeking to a wrong position.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel

#: Bump when the token layout changes; older tokens then fail with a 400.
CURSOR_VERSION = 1

_CHECKSUM_BYTES = 4
_TAG_DATETIME = "t"
_TAG_UUID = "u"

CursorValue = datetime | UUID | str | int | float | bool | None


class InvalidCursorError(HTTPException, ValueError):
    """A cursor that is malformed, tampered with, or from another codec version.

    An ``HTTPException`` (400) so an unhandled raise in a route answers correctly, and a
    ``ValueError`` so library callers (for example the ``conversation_recall`` tool) can
    keep catching the plain exception.
    """

    def __init__(self, detail: str = "Invalid cursor") -> None:
        HTTPException.__init__(self, status_code=400, detail=detail)


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


def encode_cursor(values: tuple[CursorValue, ...]) -> str:
    """Encode a keyset position into a URL-safe, versioned, checksummed token."""
    payload = {"v": CURSOR_VERSION, "k": [_pack(v) for v in values]}
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    token = base64.urlsafe_b64encode(body).decode().rstrip("=")
    return f"{token}.{_checksum(body)}"


def decode_cursor(
    cursor: str, shape: tuple[type, ...] | None = None
) -> tuple[CursorValue, ...]:
    """Decode a token from :func:`encode_cursor`; raise :class:`InvalidCursorError` otherwise.

    ``shape`` is the tuple of types the endpoint's keyset has. A well-formed cursor minted by
    another endpoint then fails with the same 400 instead of seeking with the wrong values.
    """
    try:
        token, _, checksum = cursor.partition(".")
        body = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        if not hmac.compare_digest(checksum, _checksum(body)):
            raise ValueError("checksum mismatch")
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
            raise ValueError("unsupported cursor version")
        keys = payload["k"]
        if not isinstance(keys, list):
            raise ValueError("bad key list")
        values = tuple(_unpack(raw) for raw in keys)
        if shape is not None and (
            len(values) != len(shape)
            or not all(isinstance(v, t) for v, t in zip(values, shape, strict=True))
        ):
            raise ValueError("cursor does not match this endpoint")
        return values
    except (ValueError, TypeError, KeyError, binascii.Error) as exc:
        raise InvalidCursorError() from exc


class Page[T](BaseModel):
    """The one dashboard list envelope: ``{"items": [...], "next_cursor": ..., "limit": N}``.

    ``next_cursor`` is ``null`` on the last page; there is no ``has_more``, ``total`` or
    ``offset``. ``sources_degraded`` names fan-out sources that failed this request
    (omitted/``null`` when every source answered), so a partial page never reads as the
    whole truth. Endpoints that return more than the page subclass this and add sibling
    fields; they do not wrap it.
    """

    items: list[T]
    next_cursor: str | None = None
    limit: int
    sources_degraded: list[str] | None = None
