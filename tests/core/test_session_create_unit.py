"""Unit tests for butlers.core.sessions.session_create — no database required.

Tests in this file cover validation logic in session_create that fires before
any database interaction:

- Raises ValueError when request_id is None
- Raises ValueError for an invalid trigger_source
- Correctly passes request_id and ingestion_event_id to the DB INSERT

A fake asyncpg pool is used so no Docker or live database is needed.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake asyncpg pool — captures fetchval calls
# ---------------------------------------------------------------------------


class _FakePool:
    """Fake asyncpg pool that captures fetchval (INSERT RETURNING id) calls."""

    def __init__(self, *, return_id: uuid.UUID | None = None) -> None:
        self._return_id = return_id or uuid.uuid4()
        # List of (sql, args) tuples captured from fetchval
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args: Any) -> uuid.UUID:
        self.fetchval_calls.append((sql, args))
        return self._return_id


# ---------------------------------------------------------------------------
# session_create — request_id=None raises ValueError
# ---------------------------------------------------------------------------


async def test_session_create_request_id_none_raises_value_error():
    """session_create must raise ValueError when request_id=None, before any DB call."""
    from butlers.core.sessions import session_create

    pool = _FakePool()
    with pytest.raises(ValueError, match="request_id is required"):
        await session_create(
            pool,
            prompt="Tick-triggered prompt",
            trigger_source="tick",
            request_id=None,  # type: ignore[arg-type]
        )

    # The ValueError must fire before the INSERT, so no DB calls should occur.
    assert pool.fetchval_calls == [], "No DB call should happen when request_id is None"


async def test_session_create_request_id_none_raises_before_db_call():
    """Regression: even with a valid trigger_source, None request_id must raise early."""
    from butlers.core.sessions import session_create

    pool = _FakePool()
    with pytest.raises(ValueError):
        await session_create(
            pool,
            prompt="Scheduled",
            trigger_source="schedule:daily-report",
            request_id=None,  # type: ignore[arg-type]
        )
    assert pool.fetchval_calls == []


# ---------------------------------------------------------------------------
# session_create — invalid trigger_source raises ValueError
# ---------------------------------------------------------------------------


async def test_session_create_invalid_trigger_source_raises():
    """session_create raises ValueError for an unrecognised trigger_source."""
    from butlers.core.sessions import session_create

    pool = _FakePool()
    with pytest.raises(ValueError, match="Invalid trigger_source"):
        await session_create(
            pool,
            prompt="Bad trigger",
            trigger_source="unknown-trigger",
            request_id=str(uuid.uuid4()),
        )


async def test_session_create_empty_schedule_name_raises():
    """schedule: prefix with no task name is invalid."""
    from butlers.core.sessions import session_create

    pool = _FakePool()
    with pytest.raises(ValueError, match="Invalid trigger_source"):
        await session_create(
            pool,
            prompt="Bad schedule",
            trigger_source="schedule:",
            request_id=str(uuid.uuid4()),
        )


# ---------------------------------------------------------------------------
# session_create — valid calls pass request_id to the DB INSERT
# ---------------------------------------------------------------------------


async def test_session_create_passes_request_id_to_insert():
    """session_create forwards the request_id string to the DB INSERT."""
    from butlers.core.sessions import session_create

    pool = _FakePool()
    request_id = str(uuid.uuid4())

    await session_create(
        pool,
        prompt="Test prompt",
        trigger_source="tick",
        request_id=request_id,
    )

    assert pool.fetchval_calls, "fetchval should have been called"
    _, args = pool.fetchval_calls[0]
    # The INSERT args are: prompt, trigger_source, trace_id, model, request_id, ingestion_event_id
    # Index 4 is request_id (0-indexed from args tuple)
    assert args[4] == request_id, f"request_id {request_id!r} not found at index 4 in INSERT args: {args}"


async def test_session_create_passes_none_ingestion_event_id_for_internal():
    """Internally-triggered sessions pass ingestion_event_id=None to the INSERT."""
    from butlers.core.sessions import session_create

    pool = _FakePool()

    await session_create(
        pool,
        prompt="Tick prompt",
        trigger_source="tick",
        request_id=str(uuid.uuid4()),
        ingestion_event_id=None,
    )

    assert pool.fetchval_calls, "fetchval should have been called"
    _, args = pool.fetchval_calls[0]
    # ingestion_event_id is the last positional arg ($6 in the INSERT)
    assert args[-1] is None, f"ingestion_event_id should be None but got {args[-1]!r}"


async def test_session_create_passes_ingestion_event_id_for_connector():
    """Connector-sourced sessions pass the ingestion_event_id UUID string to the INSERT."""
    from butlers.core.sessions import session_create

    pool = _FakePool()
    ingestion_event_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    await session_create(
        pool,
        prompt="Route prompt",
        trigger_source="route",
        request_id=request_id,
        ingestion_event_id=ingestion_event_id,
    )

    assert pool.fetchval_calls, "fetchval should have been called"
    _, args = pool.fetchval_calls[0]
    # ingestion_event_id is the last positional arg ($6 in the INSERT)
    assert args[-1] == ingestion_event_id, (
        f"ingestion_event_id {ingestion_event_id!r} not found as last INSERT arg: {args}"
    )


async def test_session_create_returns_uuid():
    """session_create returns the UUID provided by the pool."""
    from butlers.core.sessions import session_create

    expected_id = uuid.uuid4()
    pool = _FakePool(return_id=expected_id)

    result = await session_create(
        pool,
        prompt="Test",
        trigger_source="tick",
        request_id=str(uuid.uuid4()),
    )

    assert result == expected_id


# ---------------------------------------------------------------------------
# session_create — valid trigger_source values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "trigger_source",
    [
        "tick",
        "external",
        "trigger",
        "route",
        "schedule:morning-check",
        "schedule:nightly-cleanup",
    ],
)
async def test_session_create_accepts_valid_trigger_sources(trigger_source: str):
    """session_create does not raise for any valid trigger_source."""
    from butlers.core.sessions import session_create

    pool = _FakePool()
    # Should not raise
    await session_create(
        pool,
        prompt="Test",
        trigger_source=trigger_source,
        request_id=str(uuid.uuid4()),
    )
    assert pool.fetchval_calls, f"fetchval not called for trigger_source={trigger_source!r}"
