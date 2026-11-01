"""Unit tests for the public.permissions runtime enforcer.

Covers the opt-in-deny semantics and the fail-open contract of
``butlers.core.permissions.check_permission`` / ``require_permission`` without a
real database (asyncpg pool mocked).

[bu-qu8ma.1]
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from butlers.core.permissions import (
    PermissionDenied,
    check_permission,
    require_permission,
)

# The retained historical permission-test narratives describe the original enforcement.
# Effects now refuse unavailable state; the real-lookup availability test below covers
# bu-30sah4's effect stage, before bu-svqpzo.3 changes remaining shared callers.
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


async def test_effect_permission_policy_is_transient_and_rechecks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Effect-mode availability is distinct from no-row and explicit revocation."""
    from butlers.core.permissions import PermissionUnavailable

    for row in (None, {"granted": True, "reason": "granted"}):
        pool = _pool_returning(row)
        await require_permission(pool, "messenger", "email.send", fail_closed=True)
        assert (await check_permission(pool, "messenger", "email.send", fail_closed=True)).allowed
    with pytest.raises(PermissionDenied):
        await require_permission(
            _pool_returning({"granted": False, "reason": "revoked"}),
            "messenger",
            "email.send",
            fail_closed=True,
        )

    stores = [
        None,
        _pool_returning({}),
        _pool_returning({"granted": "false", "reason": "private-row"}),
        _pool_returning({"granted": True, "reason": object()}),
    ]
    raising = _pool_returning(None)
    raising.fetchrow.side_effect = OSError("private-database-error")
    stores.append(raising)
    for pool in stores:
        status = await check_permission(pool, "messenger", "email.send", fail_closed=True)
        assert status.allowed is False
        assert status.explicit is False
        assert status.unavailable is True
        results = await asyncio.gather(
            *(
                require_permission(pool, "messenger", "email.send", fail_closed=True)
                for _ in range(3)
            ),
            return_exceptions=True,
        )
        assert all(isinstance(result, PermissionUnavailable) for result in results)
        assert all(result.reason == "permission store unavailable" for result in results)
        assert "private-database-error" not in repr(results) + caplog.text
        assert "private-row" not in repr(results) + caplog.text

    # An outage neither retries itself nor poisons a later successful lookup.
    assert raising.fetchrow.await_count == 4
    raising.fetchrow.side_effect = None
    await require_permission(raising, "messenger", "email.send", fail_closed=True)
    raising.fetchrow.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await require_permission(raising, "messenger", "email.send", fail_closed=True)
