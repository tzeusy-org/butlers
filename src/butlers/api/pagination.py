"""Canonical dashboard cursor API and ``Page[T]`` list envelope.

The one transport-neutral codec lives in ``butlers.pagination``. The API facade
preserves its public functions and translates only cursor errors to HTTP 400.
"""

from __future__ import annotations

import types

from fastapi import HTTPException
from pydantic import BaseModel

from butlers.pagination import CURSOR_VERSION as CURSOR_VERSION
from butlers.pagination import CursorValue as CursorValue
from butlers.pagination import InvalidCursorError as _CursorError
from butlers.pagination import _checksum as _checksum
from butlers.pagination import decode_cursor as _decode_cursor
from butlers.pagination import encode_cursor as encode_cursor


class InvalidCursorError(HTTPException, _CursorError):
    """A cursor error that answers HTTP 400 and remains a library ``ValueError``."""

    def __init__(self, detail: str = "Invalid cursor") -> None:
        HTTPException.__init__(self, status_code=400, detail=detail)


def decode_cursor(
    cursor: str, shape: tuple[type | types.UnionType, ...] | None = None, *, scope: str
) -> tuple[CursorValue, ...]:
    """Decode the shared opaque token, translating cursor failures to HTTP 400."""
    try:
        return _decode_cursor(cursor, shape, scope=scope)
    except _CursorError as exc:
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
