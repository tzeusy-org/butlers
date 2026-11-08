"""Unit tests for butlers.core.sessions.session_create — condensed.

Covers validation logic that fires before any database interaction:
- Raises ValueError when request_id is None
- Raises ValueError for invalid trigger_source values
- Accepts all valid trigger_source patterns
- Returns UUID from the pool
"""

from __future__ import annotations

import hashlib
import uuid
from unittest.mock import AsyncMock

import pytest

from butlers.testing.fake_pool import fake_pool

pytestmark = pytest.mark.unit


def _insert_args(pool, call: int = 0) -> tuple:
    """Bound parameters ($1..$12) of the nth sessions INSERT."""
    return pool.fetchval.await_args_list[call].args[1:]


async def test_session_create_validation_and_return() -> None:
    """None request_id raises before DB call; valid call returns pool UUID."""
    from butlers.core.sessions import session_create

    pool = fake_pool()
    with pytest.raises(ValueError, match="request_id is required"):
        await session_create(
            pool,
            prompt="Tick-triggered prompt",
            trigger_source="tick",
            request_id=None,  # type: ignore[arg-type]
        )
    pool.fetchval.assert_not_awaited()

    expected_id = uuid.uuid4()
    pool2 = fake_pool(fetchval=expected_id, execute="SELECT 1")
    effective_prompt = "Synthetic system prompt"
    provenance = [
        {
            "source": "roster:synthetic/CLAUDE.md",
            "status": "present",
            "bytes": len(effective_prompt.encode("utf-8")),
            "sha": hashlib.sha256(effective_prompt.encode("utf-8")).hexdigest(),
        }
    ]
    result = await session_create(
        pool2,
        prompt="Test",
        trigger_source="tick",
        request_id=str(uuid.uuid4()),
        effective_system_prompt=effective_prompt,
        prompt_digest=hashlib.sha256(effective_prompt.encode("utf-8")).hexdigest(),
        prompt_provenance=provenance,
    )
    assert result == expected_id
    insert_args = _insert_args(pool2)
    assert insert_args[8] == effective_prompt
    assert insert_args[9] == hashlib.sha256(effective_prompt.encode("utf-8")).hexdigest()
    assert insert_args[10] == provenance
    assert insert_args[11] == "standard"


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
    pool = fake_pool(
        fetchval=AsyncMock(
            side_effect=[_IngestionEventFkViolation("missing ingestion event"), expected_id]
        ),
        execute="SELECT 1",
    )

    result = await sessions.session_create(
        pool,
        prompt="Routed prompt",
        trigger_source="route",
        request_id=request_id,
        ingestion_event_id=ingestion_event_id,
    )

    assert result == expected_id
    assert pool.fetchval.await_count == 2
    assert _insert_args(pool, 0)[4] == request_id
    assert _insert_args(pool, 0)[5] == ingestion_event_id
    assert _insert_args(pool, 1)[4] == request_id
    assert _insert_args(pool, 1)[5] is None


@pytest.mark.parametrize(
    "trigger_source",
    ["unknown-trigger", "schedule:", "bad:prefix"],
)
async def test_session_create_invalid_trigger_source_raises(trigger_source: str) -> None:
    """session_create raises ValueError for invalid trigger_source."""
    from butlers.core.sessions import session_create

    with pytest.raises(ValueError, match="Invalid trigger_source"):
        await session_create(
            fake_pool(),
            prompt="Bad trigger",
            trigger_source=trigger_source,
            request_id=str(uuid.uuid4()),
        )


async def test_session_complete_persists_live_stage_timings_and_ends_the_clock() -> None:
    """The row is completed with the session's stage timings, content-free (bu-dhloi1.180)."""
    from butlers.core import session_timing
    from butlers.core.sessions import session_complete

    session_timing._reset_for_tests()
    sid = uuid.uuid4()
    session_timing.begin(str(sid), origin=0.0, now=lambda: 2.0)
    session_timing.mark(str(sid), "invoke_start_ms")
    pool = fake_pool(fetchrow={"id": sid, "model": "m"}, execute="INSERT 0 1")

    await session_complete(
        pool, sid, output="SECRET-OUTPUT", tool_calls=[], duration_ms=5, success=True
    )

    params = pool.fetchrow.await_args.args[1:]
    stage_timings = params[-1]
    assert stage_timings == {"invoke_start_ms": 2000, "total_ms": 2000}
    assert "SECRET" not in str(stage_timings)
    assert session_timing.snapshot(str(sid)) is None


async def test_session_complete_without_a_clock_writes_null_not_a_guess() -> None:
    from butlers.core import session_timing
    from butlers.core.sessions import session_complete

    session_timing._reset_for_tests()
    sid = uuid.uuid4()
    pool = fake_pool(fetchrow={"id": sid, "model": "m"}, execute="INSERT 0 1")

    await session_complete(pool, sid, output=None, tool_calls=[], duration_ms=5, success=False)

    assert pool.fetchrow.await_args.args[-1] is None
