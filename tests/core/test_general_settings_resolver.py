"""Unit tests for resolve_general_timezone fail-open behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.core.general_settings import resolve_general_timezone

pytestmark = pytest.mark.unit


async def test_resolve_general_timezone_none_pool_defaults_utc() -> None:
    """A missing pool yields the UTC default rather than raising."""
    assert await resolve_general_timezone(None) == "UTC"


async def test_resolve_general_timezone_fails_open_on_error() -> None:
    """A pool whose lookup raises degrades to UTC."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(side_effect=RuntimeError("boom"))
    assert await resolve_general_timezone(pool) == "UTC"
