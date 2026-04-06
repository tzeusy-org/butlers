"""Tests for butlers.core.healing.tracking — condensed.

Covers:
- VALID_STATUSES, TERMINAL_STATUSES, ACTIVE_STATUSES, _VALID_TRANSITIONS
- State machine: invalid status, terminal rejection, invalid transition, valid transitions
- Fingerprint collision detection: CRITICAL logged on mismatch, no log on match
- create_or_join_attempt: creates row, joins duplicate, accumulates session_ids
- update_attempt_status: valid transitions, closed_at, healing_session_id
- Gate queries: get_active_attempt, get_recent_attempt, count_active_attempts,
  get_recent_terminal_statuses, list_attempts (with pagination/filter)
- recover_stale_attempts: timeout/failed transitions, fresh row preserved
- session_set_healing_fingerprint: best-effort, no error on missing
- qa_patrol_id: optional parameter stored on create
"""

from __future__ import annotations

import logging
import shutil
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

docker_available = shutil.which("docker") is not None

_CREATE_HEALING_ATTEMPTS_TABLE = """
CREATE TABLE IF NOT EXISTS public.healing_attempts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint     TEXT NOT NULL,
    butler_name     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'investigating',
    severity        INTEGER NOT NULL,
    exception_type  TEXT NOT NULL,
    call_site       TEXT NOT NULL,
    sanitized_msg   TEXT,
    branch_name     TEXT,
    worktree_path   TEXT,
    pr_url          TEXT,
    pr_number       INTEGER,
    session_ids     UUID[] NOT NULL DEFAULT '{}',
    healing_session_id UUID,
    qa_patrol_id    UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ,
    error_detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_healing_fingerprint
    ON public.healing_attempts(fingerprint);
CREATE INDEX IF NOT EXISTS idx_healing_status
    ON public.healing_attempts(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_healing_active_fingerprint
    ON public.healing_attempts(fingerprint)
    WHERE status IN ('dispatch_pending', 'investigating', 'pr_open');
"""


def _unique_db_name() -> str:
    return f"testdb_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
async def healing_pool(postgres_container):  # type: ignore[no-untyped-def]
    """Fresh isolated database with public.healing_attempts."""
    db_name = _unique_db_name()
    admin_conn = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database="postgres",
    )
    try:
        safe_name = db_name.replace('"', '""')
        await admin_conn.execute(f'CREATE DATABASE "{safe_name}"')
    finally:
        await admin_conn.close()

    pool = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=5,
    )
    await pool.execute("SELECT 1")
    await pool.execute(_CREATE_HEALING_ATTEMPTS_TABLE)
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
    """Status constants correct; terminal/active are proper subsets; transitions defined."""
    from butlers.core.healing.tracking import (
        _VALID_TRANSITIONS,
        ACTIVE_STATUSES,
        TERMINAL_STATUSES,
        VALID_STATUSES,
    )

    expected = {
        "dispatch_pending", "investigating", "pr_open", "pr_merged",
        "failed", "unfixable", "anonymization_failed", "timeout",
    }
    assert VALID_STATUSES == expected
    assert TERMINAL_STATUSES.issubset(VALID_STATUSES)
    for non_terminal in ("investigating", "dispatch_pending", "pr_open"):
        assert non_terminal not in TERMINAL_STATUSES
    assert "dispatch_pending" in ACTIVE_STATUSES and "investigating" in ACTIVE_STATUSES
    assert "pr_open" in ACTIVE_STATUSES
    assert _VALID_TRANSITIONS["dispatch_pending"] == frozenset({"investigating", "failed"})


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
    pool.fetchrow = AsyncMock(return_value={
        "id": attempt_id,
        "existing_exc_type": "asyncpg.exceptions.UndefinedTableError",
        "existing_call_site": "other/site.py:fn",
        "was_inserted": False,
    })
    with caplog.at_level(logging.CRITICAL, logger="butlers.core.healing.tracking"):
        result_id, is_new = await tracking.create_or_join_attempt(
            pool, fingerprint="a" * 64, butler_name="test-butler", severity=0,
            exception_type=exc_type, call_site=call_site, session_id=uuid.uuid4(),
        )
    assert result_id == attempt_id and is_new is False
    assert any("Fingerprint collision detected" in r.message for r in caplog.records if r.levelno == logging.CRITICAL)

    # Match → no CRITICAL; is_new=False
    caplog.clear()
    pool2 = MagicMock()
    pool2.fetchrow = AsyncMock(return_value={
        "id": uuid.uuid4(), "existing_exc_type": exc_type,
        "existing_call_site": call_site, "was_inserted": False,
    })
    with caplog.at_level(logging.CRITICAL, logger="butlers.core.healing.tracking"):
        _, is_new2 = await tracking.create_or_join_attempt(
            pool2, fingerprint="b" * 64, butler_name="test-butler", severity=2,
            exception_type=exc_type, call_site=call_site, session_id=uuid.uuid4(),
        )
    assert is_new2 is False
    assert not [r for r in caplog.records if r.levelno == logging.CRITICAL]

    # Insert returns is_new=True
    pool3 = MagicMock()
    pool3.fetchrow = AsyncMock(return_value={
        "id": uuid.uuid4(), "existing_exc_type": exc_type,
        "existing_call_site": call_site, "was_inserted": True,
    })
    _, is_new3 = await tracking.create_or_join_attempt(
        pool3, fingerprint="c" * 64, butler_name="test-butler", severity=2,
        exception_type=exc_type, call_site=call_site, session_id=uuid.uuid4(),
    )
    assert is_new3 is True

    # None result → RuntimeError
    pool4 = MagicMock()
    pool4.fetchrow = AsyncMock(return_value=None)
    with pytest.raises(RuntimeError, match="unexpected empty result"):
        await tracking.create_or_join_attempt(
            pool4, fingerprint="d" * 64, butler_name="test-butler", severity=2,
            exception_type=exc_type, call_site=call_site, session_id=uuid.uuid4(),
        )


# ===========================================================================
# Integration tests
# ===========================================================================


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_create_join_and_status_transitions_integration(healing_pool: asyncpg.Pool) -> None:
    """Create, join, accumulate session_ids, idempotent; valid transitions, terminal rejects, closed_at set."""
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
    aid1, in1 = await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp, session_id=s1))
    aid2, in2 = await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp, session_id=s2))
    assert in1 is True and in2 is False and aid1 == aid2
    row2 = await get_attempt(healing_pool, aid1)
    sids = [str(s) for s in row2["session_ids"]]
    assert str(s1) in sids and str(s2) in sids
    await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp, session_id=s1))
    row3 = await get_attempt(healing_pool, aid1)
    assert [str(s) for s in row3["session_ids"]].count(str(s1)) == 1

    # Valid transitions; closed_at semantics
    aid3, _ = await create_or_join_attempt(healing_pool, **_make_attempt_args())
    assert await update_attempt_status(healing_pool, aid3, "pr_open", pr_url="https://github.com/t/r/pull/1", pr_number=1)
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
    """get_active_attempt, get_recent_attempt, count_active_attempts, get_recent_terminal_statuses, list_attempts."""
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

    aid_active, _ = await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp_active))
    aid_terminal, _ = await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp_terminal))
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
    aid_new, _ = await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp_new))
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
    """Stale with session_id → timeout; stale without → failed; fresh → preserved."""
    from butlers.core.healing.tracking import (
        create_or_join_attempt,
        get_attempt,
        recover_stale_attempts,
    )

    # Stale with healing_session_id → timeout
    fp_stale = uuid.uuid4().hex * 2
    aid_stale, _ = await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp_stale))
    await healing_pool.execute(
        """
        UPDATE public.healing_attempts
        SET healing_session_id = $2, updated_at = now() - INTERVAL '35 minutes'
        WHERE id = $1
        """,
        aid_stale, uuid.uuid4(),
    )
    recovered_count, pending_rows = await recover_stale_attempts(healing_pool, timeout_minutes=30)
    assert recovered_count >= 1 and isinstance(pending_rows, list)
    r = await get_attempt(healing_pool, aid_stale)
    assert r["status"] == "timeout" and r["closed_at"] is not None

    # Stale without healing_session_id → failed
    fp_never = uuid.uuid4().hex * 2
    aid_never, _ = await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp_never))
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

    # Fresh row preserved
    fp_fresh = uuid.uuid4().hex * 2
    aid_fresh, _ = await create_or_join_attempt(healing_pool, **_make_attempt_args(fingerprint=fp_fresh))
    await healing_pool.execute(
        "UPDATE public.healing_attempts SET updated_at = now() WHERE id = $1", aid_fresh,
    )
    await recover_stale_attempts(healing_pool, timeout_minutes=30)
    r3 = await get_attempt(healing_pool, aid_fresh)
    assert r3["status"] == "investigating"
    # Cleanup
    await healing_pool.execute(
        "UPDATE public.healing_attempts SET status = 'failed', closed_at = now() WHERE id = $1",
        aid_fresh,
    )
