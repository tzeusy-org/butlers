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

    def __init__(
        self,
        *,
        return_id: uuid.UUID | None = None,
        fetchval_results: list[Any] | None = None,
    ) -> None:
        self._return_id = return_id or uuid.uuid4()
        self._fetchval_results = list(fetchval_results or [])
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args: Any) -> uuid.UUID:
        self.fetchval_calls.append((sql, args))
        if self._fetchval_results:
            result = self._fetchval_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
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
    result = await session_create(
        pool2, prompt="Test", trigger_source="tick", request_id=str(uuid.uuid4())
    )
    assert result == expected_id


async def test_session_create_drops_stale_ingestion_event_link(monkeypatch: pytest.MonkeyPatch):
    """A stale ingestion_event_id must not prevent recording the session itself."""
    import butlers.core.sessions as sessions

    class _IngestionEventFkViolation(Exception):
        constraint_name = "sessions_ingestion_event_id_fkey"

    monkeypatch.setattr(
        sessions.asyncpg,
        "ForeignKeyViolationError",
        _IngestionEventFkViolation,
    )

    request_id = str(uuid.uuid4())
    ingestion_event_id = str(uuid.uuid4())
    expected_id = uuid.uuid4()
    pool = _FakePool(
        fetchval_results=[
            _IngestionEventFkViolation("missing ingestion event"),
            expected_id,
        ]
    )

    result = await sessions.session_create(
        pool,
        prompt="Routed prompt",
        trigger_source="route",
        request_id=request_id,
        ingestion_event_id=ingestion_event_id,
    )

    assert result == expected_id
    assert len(pool.fetchval_calls) == 2
    assert pool.fetchval_calls[0][1][4] == request_id
    assert pool.fetchval_calls[0][1][5] == ingestion_event_id
    assert pool.fetchval_calls[1][1][4] == request_id
    assert pool.fetchval_calls[1][1][5] is None


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
