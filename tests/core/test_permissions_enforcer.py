"""Unit tests for the public.permissions runtime enforcer.

Covers the opt-in-deny semantics and the fail-open contract of
``butlers.core.permissions.check_permission`` / ``require_permission`` without a
real database (asyncpg pool mocked).

[bu-qu8ma.1]
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.core.permissions import (
    PermissionDenied,
    check_permission,
    require_permission,
)

pytestmark = pytest.mark.unit


def _pool_returning(row: dict | None) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)
    return pool


async def test_no_row_is_default_allow() -> None:
    """No explicit row → allowed (default-allow), not explicit."""
    pool = _pool_returning(None)
    status = await check_permission(pool, "chronicler", "spawn")
    assert status.allowed is True
    assert status.explicit is False


async def test_granted_row_allows() -> None:
    pool = _pool_returning({"granted": True, "reason": "default"})
    status = await check_permission(pool, "chronicler", "spawn")
    assert status.allowed is True
    assert status.explicit is True
    assert status.reason == "default"


async def test_revoked_row_denies() -> None:
    pool = _pool_returning({"granted": False, "reason": "revoked by owner"})
    status = await check_permission(pool, "chronicler", "spawn")
    assert status.allowed is False
    assert status.explicit is True
    assert status.reason == "revoked by owner"


async def test_none_pool_fails_open() -> None:
    status = await check_permission(None, "chronicler", "spawn")
    assert status.allowed is True
    assert status.explicit is False


async def test_db_error_fails_open() -> None:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
    status = await check_permission(pool, "chronicler", "spawn")
    assert status.allowed is True
    assert status.explicit is False


async def test_require_permission_raises_on_deny() -> None:
    pool = _pool_returning({"granted": False, "reason": "nope"})
    with pytest.raises(PermissionDenied) as excinfo:
        await require_permission(pool, "messenger", "spawn")
    assert excinfo.value.butler == "messenger"
    assert excinfo.value.permission == "spawn"
    assert "messenger" in str(excinfo.value)


async def test_require_permission_passes_on_grant() -> None:
    pool = _pool_returning({"granted": True, "reason": "ok"})
    await require_permission(pool, "messenger", "spawn")  # no raise
