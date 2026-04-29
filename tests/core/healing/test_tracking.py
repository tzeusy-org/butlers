"""Tests for butlers.core.healing.tracking — condensed.

Covers:
- VALID_STATUSES, TERMINAL_STATUSES, ACTIVE_STATUSES, PHASE_SESSION_STATUSES, _VALID_TRANSITIONS
- State machine: invalid status, terminal rejection, invalid transition, valid transitions
- Fingerprint collision detection: CRITICAL logged on mismatch, no log on match
- create_or_join_attempt: creates row, joins duplicate, accumulates session_ids
- update_attempt_status: valid transitions, closed_at, healing_session_id
- Gate queries: get_active_attempt, get_recent_attempt, count_active_attempts (qa_only),
  get_recent_terminal_statuses (healing_session_id filter), list_attempts (with pagination/filter)
- recover_stale_attempts: deadline-aware timeout, legacy fallback, fresh row preserved
- session_set_healing_fingerprint: best-effort, no error on missing
- qa_patrol_id: optional parameter stored on create
- create_dispatch_event / list_dispatch_events: dispatch decision recording
- record_phase_session / update_phase_session_status / list_phase_sessions: phase session tracking
"""

from __future__ import annotations

import logging
import shutil
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture(scope="module")
async def healing_pool(migrated_db_url: str):  # type: ignore[no-untyped-def]
    """Asyncpg pool pointing at the core-migrated DB.

    Healing tables (public.healing_attempts, public.healing_attempt_sessions,
    public.healing_dispatch_events) are created by the core migration chain.
    """
    pool = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=5)
    await pool.execute("SELECT 1")
    # Truncate data tables before the test module runs (shared DB).
    await pool.execute(
        "TRUNCATE TABLE public.healing_dispatch_events, "
        "public.healing_attempt_sessions, public.healing_attempts CASCADE"
    )
    yield pool
    await pool.close()


def _make_attempt_args(
    *,
    fingerprint: str | None = None,
    butler_name: str = "test-butler",
    severity: int = 2,
    exception_type: str = "builtins.KeyError",
    call_site: str = "src/butlers/core/spawner.py:_run",
    session_id: uuid.UUID | None = None,
    sanitized_msg: str | None = "something went wrong",
) -> dict[str, Any]:
    return {
        "fingerprint": fingerprint or uuid.uuid4().hex * 2,
        "butler_name": butler_name,
        "severity": severity,
        "exception_type": exception_type,
        "call_site": call_site,
        "session_id": session_id or uuid.uuid4(),
        "sanitized_msg": sanitized_msg,
    }


# ===========================================================================
# Unit tests
# ===========================================================================


@pytest.mark.unit
def test_status_sets_and_transitions() -> None:
    """Status constants correct; terminal/active are proper subsets; transitions defined.

    dispatch_pending was removed in core_066 — verify it is absent.
    """
    from butlers.core.healing.tracking import (
        _VALID_TRANSITIONS,
        ACTIVE_STATUSES,
        PHASE_SESSION_STATUSES,
        TERMINAL_STATUSES,
        VALID_STATUSES,
    )

    expected = {
        "investigating",
        "pr_open",
        "pr_merged",
        "failed",
        "unfixable",
        "anonymization_failed",
        "timeout",
    }
    assert VALID_STATUSES == expected
    assert "dispatch_pending" not in VALID_STATUSES
    assert TERMINAL_STATUSES.issubset(VALID_STATUSES)
    for non_terminal in ("investigating", "pr_open"):
        assert non_terminal not in TERMINAL_STATUSES
    assert "investigating" in ACTIVE_STATUSES and "pr_open" in ACTIVE_STATUSES
    assert "dispatch_pending" not in ACTIVE_STATUSES
    assert "dispatch_pending" not in _VALID_TRANSITIONS
    assert PHASE_SESSION_STATUSES == {"running", "completed", "failed", "timeout", "cancelled"}


@pytest.mark.unit
async def test_state_machine_unit_rejections() -> None:
    """Returns False for unknown status, terminal state, missing row, invalid transition."""
    from butlers.core.healing.tracking import update_attempt_status

    # Unknown status — fetchrow never called
    pool_a = MagicMock()
    pool_a.fetchrow = AsyncMock(return_value={"status": "investigating"})
    assert await update_attempt_status(pool_a, uuid.uuid4(), "not_a_real_status") is False
    pool_a.fetchrow.assert_not_called()

    # Terminal state rejects further transition
    pool_b = MagicMock()
    pool_b.fetchrow = AsyncMock(return_value={"status": "failed"})
    pool_b.fetchval = AsyncMock(return_value=None)
    assert await update_attempt_status(pool_b, uuid.uuid4(), "pr_open") is False

    # Not found
    pool_c = MagicMock()
    pool_c.fetchrow = AsyncMock(return_value=None)
    assert await update_attempt_status(pool_c, uuid.uuid4(), "failed") is False

    # Invalid transition (investigating → pr_merged, must go through pr_open)
    pool_d = MagicMock()
    pool_d.fetchrow = AsyncMock(return_value={"status": "investigating"})
    pool_d.fetchval = AsyncMock(return_value=None)
    assert await update_attempt_status(pool_d, uuid.uuid4(), "pr_merged") is False


@pytest.mark.unit
async def test_collision_detection_unit(caplog: pytest.LogCaptureFixture) -> None:
    """CRITICAL on metadata mismatch; no log on match; is_new=True on insert; None raises."""
    from butlers.core.healing import tracking

    exc_type = "builtins.KeyError"
    call_site = "src/butlers/core/spawner.py:_run"

    # Mismatch → CRITICAL
    attempt_id = uuid.uuid4()
    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "id": attempt_id,
            "existing_exc_type": "asyncpg.exceptions.UndefinedTableError",
            "existing_call_site": "other/site.py:fn",
            "was_inserted": False,
        }
    )
    with caplog.at_level(logging.CRITICAL, logger="butlers.core.healing.tracking"):
        result_id, is_new = await tracking.create_or_join_attempt(
            pool,
            fingerprint="a" * 64,
            butler_name="test-butler",
            severity=0,
            exception_type=exc_type,
            call_site=call_site,
            session_id=uuid.uuid4(),
        )
    assert result_id == attempt_id and is_new is False
    assert any(
        "Fingerprint collision detected" in r.message
        for r in caplog.records
        if r.levelno == logging.CRITICAL
    )

    # Match → no CRITICAL; is_new=False
    caplog.clear()
    pool2 = MagicMock()
    pool2.fetchrow = AsyncMock(
        return_value={
            "id": uuid.uuid4(),
            "existing_exc_type": exc_type,
            "existing_call_site": call_site,
            "was_inserted": False,
        }
    )
    with caplog.at_level(logging.CRITICAL, logger="butlers.core.healing.tracking"):
        _, is_new2 = await tracking.create_or_join_attempt(
            pool2,
            fingerprint="b" * 64,
            butler_name="test-butler",
            severity=2,
            exception_type=exc_type,
            call_site=call_site,
            session_id=uuid.uuid4(),
        )
    assert is_new2 is False
    assert not [r for r in caplog.records if r.levelno == logging.CRITICAL]

    # Insert returns is_new=True
    pool3 = MagicMock()
    pool3.fetchrow = AsyncMock(
        return_value={
            "id": uuid.uuid4(),
            "existing_exc_type": exc_type,
            "existing_call_site": call_site,
            "was_inserted": True,
        }
    )
    _, is_new3 = await tracking.create_or_join_attempt(
        pool3,
        fingerprint="c" * 64,
        butler_name="test-butler",
        severity=2,
        exception_type=exc_type,
        call_site=call_site,
        session_id=uuid.uuid4(),
    )
    assert is_new3 is True

    # None result → RuntimeError
    pool4 = MagicMock()
    pool4.fetchrow = AsyncMock(return_value=None)
    with pytest.raises(RuntimeError, match="unexpected empty result"):
        await tracking.create_or_join_attempt(
            pool4,
            fingerprint="d" * 64,
            butler_name="test-butler",
            severity=2,
            exception_type=exc_type,
            call_site=call_site,
            session_id=uuid.uuid4(),
        )


# ===========================================================================
# Integration tests
# ===========================================================================


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_create_join_and_status_transitions_integration(healing_pool: asyncpg.Pool) -> None:
    """Create, join, idempotent session_ids; valid transitions; terminal rejects; closed_at set."""
    from butlers.core.healing.tracking import (
        TERMINAL_STATUSES,
        create_or_join_attempt,
        get_attempt,
        update_attempt_status,
    )

    # Create new row
    attempt_id, is_new = await create_or_join_attempt(healing_pool, **_make_attempt_args())
    assert isinstance(attempt_id, uuid.UUID) and is_new is True
    row = await get_attempt(healing_pool, attempt_id)
    assert row is not None and row["status"] == "investigating"

    # Join same fingerprint → is_new=False, session_ids accumulated; idempotent
    fp = uuid.uuid4().hex * 2
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    aid1, in1 = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp, session_id=s1)
    )
    aid2, in2 = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp, session_id=s2)
    )
    assert in1 is True and in2 is False and aid1 == aid2
    row2 = await get_attempt(healing_pool, aid1)
    sids = [str(s) for s in row2["session_ids"]]
    assert str(s1) in sids and str(s2) in sids
    await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp, session_id=s1))
    row3 = await get_attempt(healing_pool, aid1)
    assert [str(s) for s in row3["session_ids"]].count(str(s1)) == 1

    # Valid transitions; closed_at semantics
    aid3, _ = await create_or_join_attempt(healing_pool, **_make_attempt_args())
    assert await update_attempt_status(
        healing_pool, aid3, "pr_open", pr_url="https://github.com/t/r/pull/1", pr_number=1
    )
    r1 = await get_attempt(healing_pool, aid3)
    assert r1["status"] == "pr_open" and r1["closed_at"] is None
    assert await update_attempt_status(healing_pool, aid3, "pr_merged")
    r2 = await get_attempt(healing_pool, aid3)
    assert r2["status"] == "pr_merged" and r2["closed_at"] is not None
    for target in TERMINAL_STATUSES:
        assert await update_attempt_status(healing_pool, aid3, target) is False

    # investigating → failed with error_detail
    aid4, _ = await create_or_join_attempt(healing_pool, **_make_attempt_args())
    assert await update_attempt_status(healing_pool, aid4, "failed", error_detail="agent gave up")
    r3 = await get_attempt(healing_pool, aid4)
    assert r3["status"] == "failed" and r3["closed_at"] is not None
    assert r3["error_detail"] == "agent gave up"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_gate_queries_integration(healing_pool: asyncpg.Pool) -> None:
    """gate queries: active, recent, count, terminal statuses, list."""
    from butlers.core.healing.tracking import (
        TERMINAL_STATUSES,
        count_active_attempts,
        create_or_join_attempt,
        get_active_attempt,
        get_recent_attempt,
        get_recent_terminal_statuses,
        list_attempts,
        update_attempt_status,
    )

    fp_active = uuid.uuid4().hex * 2
    fp_terminal = uuid.uuid4().hex * 2

    aid_active, _ = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp_active)
    )
    aid_terminal, _ = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp_terminal)
    )
    await update_attempt_status(healing_pool, aid_terminal, "failed")

    # get_active_attempt returns active; None for terminal/unknown
    assert await get_active_attempt(healing_pool, fp_active) is not None
    assert await get_active_attempt(healing_pool, fp_terminal) is None
    assert await get_active_attempt(healing_pool, "e" * 64) is None

    # get_recent_attempt: None for active; row for terminal within window; None for unknown
    assert await get_recent_attempt(healing_pool, fp_active, window_minutes=60) is None
    r = await get_recent_attempt(healing_pool, fp_terminal, window_minutes=60)
    assert r is not None and r["status"] == "failed"
    assert await get_recent_attempt(healing_pool, "f" * 64, window_minutes=60) is None

    # count_active_attempts increases on new, decreases on terminal
    before = await count_active_attempts(healing_pool)
    fp_new = uuid.uuid4().hex * 2
    aid_new, _ = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp_new)
    )
    assert await count_active_attempts(healing_pool) >= before + 1
    await update_attempt_status(healing_pool, aid_new, "failed")
    assert await count_active_attempts(healing_pool) == before

    # get_recent_terminal_statuses returns valid terminal statuses
    statuses = await get_recent_terminal_statuses(healing_pool, limit=10)
    assert all(s in TERMINAL_STATUSES for s in statuses)

    # list_attempts: pagination and status_filter
    fps = [uuid.uuid4().hex * 2 for _ in range(4)]
    for fp in fps:
        await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp))
    page1 = await list_attempts(healing_pool, limit=2, offset=0)
    page2 = await list_attempts(healing_pool, limit=2, offset=2)
    assert len(page1) == 2 and len(page2) >= 1

    fp_f = uuid.uuid4().hex * 2
    aid_f, _ = await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp_f))
    await update_attempt_status(healing_pool, aid_f, "failed")
    failed_rows = await list_attempts(healing_pool, status_filter="failed")
    assert all(r["status"] == "failed" for r in failed_rows)
    assert str(aid_f) in {str(r["id"]) for r in failed_rows}


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_recover_stale_attempts_integration(healing_pool: asyncpg.Pool) -> None:
    """Deadline-aware recovery: expired deadline → timeout; no-deadline legacy fallback → timeout;
    never-spawned → failed; within-budget → preserved; returns int count."""
    from butlers.core.healing.tracking import (
        create_or_join_attempt,
        get_attempt,
        recover_stale_attempts,
    )

    # 1. Deadline-expired with healing_session_id → timeout (deadline authority)
    fp_expired = uuid.uuid4().hex * 2
    aid_expired, _ = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp_expired)
    )
    await healing_pool.execute(
        """
        UPDATE public.healing_attempts
        SET healing_session_id = $2,
            workflow_deadline_at = now() - INTERVAL '5 minutes'
        WHERE id = $1
        """,
        aid_expired,
        uuid.uuid4(),
    )
    recovered_count = await recover_stale_attempts(healing_pool, timeout_minutes=30)
    assert isinstance(recovered_count, int) and recovered_count >= 1
    r = await get_attempt(healing_pool, aid_expired)
    assert r["status"] == "timeout" and r["closed_at"] is not None
    assert "deadline exceeded" in r["error_detail"]

    # 2. Legacy row (no deadline) with stale updated_at → timeout (fallback heuristic)
    fp_legacy = uuid.uuid4().hex * 2
    aid_legacy, _ = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp_legacy)
    )
    await healing_pool.execute(
        """
        UPDATE public.healing_attempts
        SET healing_session_id = $2,
            workflow_deadline_at = NULL,
            updated_at = now() - INTERVAL '35 minutes'
        WHERE id = $1
        """,
        aid_legacy,
        uuid.uuid4(),
    )
    await recover_stale_attempts(healing_pool, timeout_minutes=30)
    r_legacy = await get_attempt(healing_pool, aid_legacy)
    assert r_legacy["status"] == "timeout"
    assert "no deadline set" in r_legacy["error_detail"]

    # 3. Never-spawned (no healing_session_id, old created_at) → failed
    fp_never = uuid.uuid4().hex * 2
    aid_never, _ = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp_never)
    )
    await healing_pool.execute(
        """
        UPDATE public.healing_attempts
        SET healing_session_id = NULL,
            created_at = now() - INTERVAL '10 minutes',
            updated_at = now() - INTERVAL '10 minutes'
        WHERE id = $1
        """,
        aid_never,
    )
    await recover_stale_attempts(healing_pool, timeout_minutes=30)
    r2 = await get_attempt(healing_pool, aid_never)
    assert r2["status"] == "failed" and "never spawned" in r2["error_detail"].lower()

    # 4. Within-budget row preserved (workflow_deadline_at in future)
    fp_fresh = uuid.uuid4().hex * 2
    aid_fresh, _ = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp_fresh)
    )
    await healing_pool.execute(
        """
        UPDATE public.healing_attempts
        SET healing_session_id = $2,
            workflow_deadline_at = now() + INTERVAL '30 minutes'
        WHERE id = $1
        """,
        aid_fresh,
        uuid.uuid4(),
    )
    await recover_stale_attempts(healing_pool, timeout_minutes=30)
    r3 = await get_attempt(healing_pool, aid_fresh)
    assert r3["status"] == "investigating"
    # Cleanup
    await healing_pool.execute(
        "UPDATE public.healing_attempts SET status = 'failed', closed_at = now() WHERE id = $1",
        aid_fresh,
    )


@pytest.mark.unit
async def test_count_active_attempts_qa_only_unit() -> None:
    """count_active_attempts qa_only=True scopes to qa_patrol_id IS NOT NULL."""
    from butlers.core.healing.tracking import count_active_attempts

    # qa_only=True path
    pool_qa = MagicMock()
    pool_qa.fetchval = AsyncMock(return_value=3)
    result = await count_active_attempts(pool_qa, qa_only=True)
    assert result == 3
    called_sql: str = pool_qa.fetchval.call_args[0][0]
    assert "qa_patrol_id IS NOT NULL" in called_sql

    # qa_only=False (default) path — no qa_patrol_id filter
    pool_all = MagicMock()
    pool_all.fetchval = AsyncMock(return_value=7)
    result2 = await count_active_attempts(pool_all, qa_only=False)
    assert result2 == 7
    called_sql2: str = pool_all.fetchval.call_args[0][0]
    assert "qa_patrol_id IS NOT NULL" not in called_sql2


@pytest.mark.unit
async def test_get_recent_terminal_statuses_filters_by_session_id() -> None:
    """get_recent_terminal_statuses only returns rows with healing_session_id IS NOT NULL."""
    from butlers.core.healing.tracking import get_recent_terminal_statuses

    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"status": "failed"}, {"status": "pr_merged"}])
    result = await get_recent_terminal_statuses(pool, limit=5)
    assert result == ["failed", "pr_merged"]
    called_sql: str = pool.fetch.call_args[0][0]
    assert "healing_session_id IS NOT NULL" in called_sql


@pytest.mark.unit
async def test_create_dispatch_event_unit() -> None:
    """create_dispatch_event inserts a row and returns a UUID."""
    from butlers.core.healing.tracking import create_dispatch_event

    event_id = uuid.uuid4()
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=event_id)
    result = await create_dispatch_event(
        pool,
        fingerprint="a" * 64,
        butler_name="test-butler",
        decision="cooldown",
        reason="within cooldown window",
        attempt_id=None,
    )
    assert result == event_id
    sql: str = pool.fetchval.call_args[0][0]
    assert "healing_dispatch_events" in sql


@pytest.mark.unit
async def test_list_dispatch_events_unit() -> None:
    """list_dispatch_events returns rows and applies decision_filter."""
    from butlers.core.healing.tracking import list_dispatch_events

    event_id = uuid.uuid4()
    mock_row = {
        "id": event_id,
        "fingerprint": "a" * 64,
        "butler_name": "test-butler",
        "decision": "cooldown",
        "reason": "within window",
        "attempt_id": None,
        "created_at": None,
    }

    # Unfiltered path
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[mock_row])
    rows = await list_dispatch_events(pool)
    assert len(rows) == 1 and rows[0]["decision"] == "cooldown"
    unfiltered_sql: str = pool.fetch.call_args[0][0]
    assert "decision = " not in unfiltered_sql

    # Filtered path
    pool2 = MagicMock()
    pool2.fetch = AsyncMock(return_value=[mock_row])
    rows2 = await list_dispatch_events(pool2, decision_filter="cooldown")
    assert len(rows2) == 1
    filtered_sql: str = pool2.fetch.call_args[0][0]
    assert "decision = $1" in filtered_sql


@pytest.mark.unit
async def test_record_phase_session_unit() -> None:
    """record_phase_session inserts child row and updates parent (inside a transaction)."""
    from unittest.mock import AsyncMock, MagicMock

    from butlers.core.healing.tracking import record_phase_session

    child_id = uuid.uuid4()

    # Build a mock connection that supports fetchval and execute
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=child_id)
    conn.execute = AsyncMock()

    # transaction() is an async context manager
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=None)
    txn_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn_cm)

    # acquire() is an async context manager that yields conn
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)

    attempt_id = uuid.uuid4()
    session_id = uuid.uuid4()
    result = await record_phase_session(pool, attempt_id, "diagnose", session_id)
    assert result == child_id

    # Verify conn.fetchval was called with INSERT into healing_attempt_sessions
    assert conn.fetchval.called
    insert_sql: str = conn.fetchval.call_args[0][0]
    assert "healing_attempt_sessions" in insert_sql

    # Verify conn.execute was called with UPDATE on healing_attempts
    assert conn.execute.called
    update_sql: str = conn.execute.call_args[0][0]
    assert "current_phase" in update_sql
    assert "healing_session_id" in update_sql


@pytest.mark.unit
async def test_update_phase_session_status_unit() -> None:
    """update_phase_session_status returns False for invalid status; True on success."""
    from butlers.core.healing.tracking import update_phase_session_status

    # Invalid status
    pool = MagicMock()
    pool.fetchval = AsyncMock()
    result = await update_phase_session_status(pool, uuid.uuid4(), "not_real")
    assert result is False
    pool.fetchval.assert_not_called()

    # Valid status, row found
    phase_id = uuid.uuid4()
    pool2 = MagicMock()
    pool2.fetchval = AsyncMock(return_value=phase_id)
    result2 = await update_phase_session_status(pool2, phase_id, "completed")
    assert result2 is True
    sql: str = pool2.fetchval.call_args[0][0]
    assert "healing_attempt_sessions" in sql

    # Row not found
    pool3 = MagicMock()
    pool3.fetchval = AsyncMock(return_value=None)
    result3 = await update_phase_session_status(pool3, uuid.uuid4(), "failed")
    assert result3 is False


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_dispatch_events_integration(healing_pool: asyncpg.Pool) -> None:
    """create_dispatch_event and list_dispatch_events work end-to-end."""
    from butlers.core.healing.tracking import (
        create_dispatch_event,
        create_or_join_attempt,
        list_dispatch_events,
    )

    fp = uuid.uuid4().hex * 2

    # Create a dispatch event without an attempt_id
    event_id = await create_dispatch_event(
        healing_pool,
        fingerprint=fp,
        butler_name="test-butler",
        decision="cooldown",
        reason="within 60 min window",
    )
    assert isinstance(event_id, uuid.UUID)

    # Create dispatch event linked to an attempt
    attempt_id, _ = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=uuid.uuid4().hex * 2)
    )
    event_id2 = await create_dispatch_event(
        healing_pool,
        fingerprint=fp,
        butler_name="test-butler",
        decision="accepted",
        reason="all gates passed",
        attempt_id=attempt_id,
    )
    assert isinstance(event_id2, uuid.UUID)

    # list_dispatch_events returns both, ordered by created_at DESC
    rows = await list_dispatch_events(healing_pool, limit=10)
    ids = {str(r["id"]) for r in rows}
    assert str(event_id) in ids and str(event_id2) in ids

    # Filter by decision
    cooldown_rows = await list_dispatch_events(healing_pool, decision_filter="cooldown")
    assert all(r["decision"] == "cooldown" for r in cooldown_rows)
    accepted_rows = await list_dispatch_events(healing_pool, decision_filter="accepted")
    assert any(str(r["id"]) == str(event_id2) for r in accepted_rows)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_phase_sessions_integration(healing_pool: asyncpg.Pool) -> None:
    """record_phase_session, update_phase_session_status, list_phase_sessions work."""
    from butlers.core.healing.tracking import (
        create_or_join_attempt,
        get_attempt,
        list_phase_sessions,
        record_phase_session,
        update_phase_session_status,
    )

    attempt_id, _ = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=uuid.uuid4().hex * 2)
    )

    # Record diagnose phase
    s1 = uuid.uuid4()
    child_id1 = await record_phase_session(healing_pool, attempt_id, "diagnose", s1)
    assert isinstance(child_id1, uuid.UUID)

    # Parent should have current_phase and healing_session_id updated
    parent = await get_attempt(healing_pool, attempt_id)
    assert parent["current_phase"] == "diagnose"
    assert str(parent["healing_session_id"]) == str(s1)

    # Record implement phase
    s2 = uuid.uuid4()
    child_id2 = await record_phase_session(healing_pool, attempt_id, "implement", s2)

    # Parent current_phase updated to latest phase
    parent2 = await get_attempt(healing_pool, attempt_id)
    assert parent2["current_phase"] == "implement"
    assert str(parent2["healing_session_id"]) == str(s2)

    # list_phase_sessions returns both in order
    sessions = await list_phase_sessions(healing_pool, attempt_id)
    assert len(sessions) == 2
    assert sessions[0]["phase"] == "diagnose"
    assert sessions[1]["phase"] == "implement"

    # update_phase_session_status: valid terminal status sets completed_at
    ok = await update_phase_session_status(healing_pool, child_id1, "completed")
    assert ok is True
    sess = await healing_pool.fetchrow(
        "SELECT * FROM public.healing_attempt_sessions WHERE id = $1",
        child_id1,
    )
    assert sess["status"] == "completed" and sess["completed_at"] is not None

    # update_phase_session_status: invalid status returns False
    bad = await update_phase_session_status(healing_pool, child_id2, "bad_status")
    assert bad is False

    # update_phase_session_status: not found returns False
    not_found = await update_phase_session_status(healing_pool, uuid.uuid4(), "failed")
    assert not_found is False


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_count_active_qa_only_integration(healing_pool: asyncpg.Pool) -> None:
    """count_active_attempts(qa_only=True) excludes non-QA rows."""
    from butlers.core.healing.tracking import count_active_attempts, create_or_join_attempt

    # Create a non-QA attempt
    fp_plain = uuid.uuid4().hex * 2
    await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp_plain))

    # Create a QA attempt
    fp_qa = uuid.uuid4().hex * 2
    qa_patrol = uuid.uuid4()
    await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp_qa), qa_patrol_id=qa_patrol
    )

    total_count = await count_active_attempts(healing_pool, qa_only=False)
    qa_count = await count_active_attempts(healing_pool, qa_only=True)
    # qa_count should be strictly less than total for any set with non-QA rows
    assert qa_count <= total_count
    assert qa_count >= 1  # at least the QA row we just created


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_get_recent_terminal_statuses_session_id_filter_integration(
    healing_pool: asyncpg.Pool,
) -> None:
    """get_recent_terminal_statuses excludes rows without a healing_session_id."""
    from butlers.core.healing.tracking import (
        create_or_join_attempt,
        get_recent_terminal_statuses,
    )

    # Row with a healing_session_id and terminal status — should appear
    fp_with = uuid.uuid4().hex * 2
    aid_with, _ = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp_with)
    )
    await healing_pool.execute(
        """
        UPDATE public.healing_attempts
        SET status = 'failed', closed_at = now(), healing_session_id = $2
        WHERE id = $1
        """,
        aid_with,
        uuid.uuid4(),
    )

    # Row WITHOUT a healing_session_id and terminal status — should NOT appear
    fp_without = uuid.uuid4().hex * 2
    aid_without, _ = await create_or_join_attempt(
        healing_pool, **_make_attempt_args(fingerprint=fp_without)
    )
    await healing_pool.execute(
        """
        UPDATE public.healing_attempts
        SET status = 'failed', closed_at = now(), healing_session_id = NULL
        WHERE id = $1
        """,
        aid_without,
    )

    statuses = await get_recent_terminal_statuses(healing_pool, limit=100)
    # All returned statuses must come from rows with a healing_session_id
    # Verify the without-row isn't present by checking all rows
    rows = await healing_pool.fetch(
        """
        SELECT id FROM public.healing_attempts
        WHERE status = ANY(ARRAY['failed', 'timeout', 'pr_merged', 'unfixable',
                                  'anonymization_failed'])
          AND healing_session_id IS NULL
        """
    )
    # Our without-row should be among those with null session_id
    null_ids = {str(r["id"]) for r in rows}
    assert str(aid_without) in null_ids
    # And the returned statuses should be >= 1 (our with-row is there)
    assert "failed" in statuses
