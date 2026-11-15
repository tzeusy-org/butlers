"""Unit tests for the public.permissions runtime enforcer.

Covers the opt-in-deny semantics and the fail-closed contract of
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

# Every caller refuses unavailable state (bu-svqpzo.3); a readable missing row still allows.
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


async def test_none_pool_is_refused_as_unavailable() -> None:
    status = await check_permission(None, "chronicler", "spawn")
    assert status.allowed is False
    assert status.unavailable is True
    assert status.explicit is False
    assert status.reason == "permission store unavailable"


async def test_db_error_is_refused_as_unavailable() -> None:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
    status = await check_permission(pool, "chronicler", "spawn")
    assert status.allowed is False
    assert status.unavailable is True


async def test_unavailable_is_logged_at_error_without_the_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("private-database-detail"))
    with caplog.at_level("WARNING", logger="butlers.core.permissions"):
        await check_permission(pool, "chronicler", "spawn")
    assert [r.levelname for r in caplog.records] == ["ERROR"]
    assert "private-database-detail" not in caplog.text


async def test_require_permission_raises_unavailable_for_every_unreadable_store() -> None:
    from butlers.core.permissions import PermissionUnavailable

    raising = AsyncMock()
    raising.fetchrow = AsyncMock(side_effect=OSError("down"))
    for pool in (None, raising):
        with pytest.raises(PermissionUnavailable):
            await require_permission(pool, "chronicler", "spawn")


def test_denial_message_separates_unavailable_from_denied() -> None:
    from butlers.core.permissions import PermissionStatus, denial_message

    denied = denial_message(
        PermissionStatus(allowed=False, explicit=True, reason="by owner"), "health", "spawn"
    )
    assert denied == "Permission denied: butler 'health' is not granted 'spawn' (reason: by owner)"
    unavailable = denial_message(
        PermissionStatus(allowed=False, reason="permission store unavailable", unavailable=True),
        "health",
        "spawn",
    )
    assert "unavailable" in unavailable and "denied" not in unavailable.lower()


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
        await require_permission(pool, "messenger", "email.send")
        assert (await check_permission(pool, "messenger", "email.send")).allowed
    with pytest.raises(PermissionDenied):
        await require_permission(
            _pool_returning({"granted": False, "reason": "revoked"}),
            "messenger",
            "email.send",
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
        status = await check_permission(pool, "messenger", "email.send")
        assert status.allowed is False
        assert status.explicit is False
        assert status.unavailable is True
        results = await asyncio.gather(
            *(require_permission(pool, "messenger", "email.send") for _ in range(3)),
            return_exceptions=True,
        )
        assert all(isinstance(result, PermissionUnavailable) for result in results)
        assert all(result.reason == "permission store unavailable" for result in results)
        assert "private-database-error" not in repr(results) + caplog.text
        assert "private-row" not in repr(results) + caplog.text

    # An outage neither retries itself nor poisons a later successful lookup.
    assert raising.fetchrow.await_count == 4
    raising.fetchrow.side_effect = None
    await require_permission(raising, "messenger", "email.send")
    raising.fetchrow.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await require_permission(raising, "messenger", "email.send")
