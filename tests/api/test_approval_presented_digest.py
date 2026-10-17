"""A connector card tap is bound to the digest of the call the card showed."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from butlers.api.routers import approvals as approvals_router
from butlers.modules.approvals.previews import args_digest

pytestmark = pytest.mark.unit

_ARGS = {"to": "dana@x.com", "subject": "Hi", "body": "Hello"}


class _Conn:
    def __init__(self, *, has_column: Any = True, presented: Any = ()) -> None:
        self._has_column = has_column
        self._presented = presented

    async def fetchval(self, *_args: Any) -> Any:
        return self._has_column

    async def fetch(self, *_args: Any) -> Any:
        return self._presented


async def _check(conn: _Conn, stored: Any = _ARGS) -> None:
    await approvals_router._require_presented_call_unchanged(conn, uuid.uuid4(), stored)


async def test_matching_digest_passes() -> None:
    await _check(_Conn(presented=[{"args_digest": args_digest(_ARGS)}]))


async def test_any_mismatching_digest_refuses() -> None:
    stale = {"args_digest": args_digest({**_ARGS, "to": "other@x.com"})}
    with pytest.raises(HTTPException) as caught:
        await _check(_Conn(presented=[{"args_digest": args_digest(_ARGS)}, stale]))
    assert caught.value.status_code == 409


async def test_non_list_presented_rows_fail_closed() -> None:
    with pytest.raises(HTTPException) as caught:
        await _check(_Conn(presented=object()))
    assert caught.value.status_code == 409


async def test_schema_without_digest_column_is_unaffected() -> None:
    await _check(_Conn(has_column=False, presented=object()))
