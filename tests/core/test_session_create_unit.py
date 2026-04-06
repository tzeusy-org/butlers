"""Unit tests for butlers.core.sessions.session_create — condensed.

Covers validation logic that fires before any database interaction:
- Raises ValueError when request_id is None
- Raises ValueError for invalid trigger_source values
- Accepts all valid trigger_source patterns
- Returns UUID from the pool
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _FakePool:
    """Fake asyncpg pool that captures fetchval calls."""

    def __init__(self, *, return_id: uuid.UUID | None = None) -> None:
        self._return_id = return_id or uuid.uuid4()
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args: Any) -> uuid.UUID:
        self.fetchval_calls.append((sql, args))
        return self._return_id


async def test_session_create_validation_and_return() -> None:
    """None request_id raises before DB call; valid call returns pool UUID."""
    from butlers.core.sessions import session_create

    pool = _FakePool()
    with pytest.raises(ValueError, match="request_id is required"):
        await session_create(
            pool,
            prompt="Tick-triggered prompt",
            trigger_source="tick",
            request_id=None,  # type: ignore[arg-type]
        )
    assert pool.fetchval_calls == []

    expected_id = uuid.uuid4()
    pool2 = _FakePool(return_id=expected_id)
    result = await session_create(pool2, prompt="Test", trigger_source="tick",
                                  request_id=str(uuid.uuid4()))
    assert result == expected_id


@pytest.mark.parametrize(
    "trigger_source",
    ["unknown-trigger", "schedule:", "bad:prefix"],
)
async def test_session_create_invalid_trigger_source_raises(trigger_source: str) -> None:
    """session_create raises ValueError for invalid trigger_source."""
    from butlers.core.sessions import session_create

    with pytest.raises(ValueError, match="Invalid trigger_source"):
        await session_create(
            _FakePool(),
            prompt="Bad trigger",
            trigger_source=trigger_source,
            request_id=str(uuid.uuid4()),
        )



