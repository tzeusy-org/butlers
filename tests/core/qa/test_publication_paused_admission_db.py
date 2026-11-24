"""Real-Postgres proof for the proposal-ready rung (bu-ejow5p.2).

With no publisher a committed investigation is retained as an unpublished proposal.  The
breaker (dispatch admission and dashboard, one shared SQL expression) reads that as an
execution success, and Gate 5.6 holds the fingerprint so the neutral breaker cannot become
unbounded spend.  Genuine execution failures still trip the breaker.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.api.routers.qa import (
    _compute_circuit_breaker_state,
    _fetch_recent_circuit_breaker_rows,
)
from butlers.core.healing.tracking import get_paused_proposal
from butlers.core.qa.dispatch import (
    _DIAGNOSIS_UNSUPPORTED_DETAIL,
    _PUBLICATION_UNAVAILABLE_DETAIL,
    QaDispatchConfig,
    _is_circuit_breaker_tripped,
    dispatch_qa_investigation,
)
from butlers.core.qa.publication.retention import retain_export
from tests.core.qa.publication_fakes import synthetic_export
from tests.core.qa.test_dispatch import _make_finding, _make_triaged

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_FP = "a" * 64


async def _patrol(pool: asyncpg.Pool) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.qa_patrols (status, completed_at) VALUES ('error', now()) RETURNING id"
    )


async def _attempt(
    pool: asyncpg.Pool,
    patrol_id: uuid.UUID,
    *,
    status: str = "failed",
    error_detail: str | None = None,
    fingerprint: str = _FP,
    retained: bool = False,
) -> uuid.UUID:
    attempt_id = await pool.fetchval(
        """
        INSERT INTO public.healing_attempts
            (fingerprint, butler_name, status, severity, exception_type, call_site,
             healing_session_id, qa_patrol_id, error_detail)
        VALUES ($1, 'general', $2, 2, 'RuntimeError', 'src/foo.py:bar',
                gen_random_uuid(), $3, $4)
        RETURNING id
        """,
        fingerprint,
        status,
        patrol_id,
        error_detail,
    )
    if retained:
        await _retain_as_qa(pool, attempt_id)
    return attempt_id


async def _retain_as_qa(pool: asyncpg.Pool, attempt_id: uuid.UUID) -> None:
    """Retain an export as the only role the insert policy admits (``butler_qa_rw``)."""
    async with pool.acquire() as connection:
        await connection.execute("SET ROLE butler_qa_rw")
        try:
            await retain_export(connection, attempt_id, synthetic_export())
        finally:
            await connection.execute("RESET ROLE")


async def _tripped_both_ways(pool: asyncpg.Pool) -> tuple[bool, bool]:
    rows = await _fetch_recent_circuit_breaker_rows(pool, limit=5)
    _, _, dashboard = _compute_circuit_breaker_state(rows, threshold=5)
    return await _is_circuit_breaker_tripped(pool, 5), dashboard


async def test_paused_successes_break_streak_in_admission_and_dashboard(db_pool) -> None:
    patrol = await _patrol(db_pool)
    for _ in range(7):
        await _attempt(db_pool, patrol, error_detail=_PUBLICATION_UNAVAILABLE_DETAIL, retained=True)
    assert await _tripped_both_ways(db_pool) == (False, False)


async def test_genuine_failures_still_trip(db_pool) -> None:
    patrol = await _patrol(db_pool)
    for _ in range(4):
        await _attempt(db_pool, patrol, status="timeout")
    await _attempt(db_pool, patrol, error_detail=_PUBLICATION_UNAVAILABLE_DETAIL, retained=True)
    assert await _tripped_both_ways(db_pool) == (False, False)
    for _ in range(5):
        await _attempt(db_pool, patrol, status="timeout")
    assert await _tripped_both_ways(db_pool) == (True, True)


async def test_unretained_publication_unavailable_still_counts_as_failure(db_pool) -> None:
    patrol = await _patrol(db_pool)
    for _ in range(5):
        await _attempt(db_pool, patrol, error_detail=_PUBLICATION_UNAVAILABLE_DETAIL)
    assert await _tripped_both_ways(db_pool) == (True, True)


async def test_paused_fingerprint_is_not_reclaimed_or_launched(db_pool) -> None:
    patrol = await _patrol(db_pool)
    await _attempt(db_pool, patrol, error_detail=_PUBLICATION_UNAVAILABLE_DETAIL, retained=True)
    before = await db_pool.fetchval("SELECT count(*) FROM public.healing_attempts")
    spawner = MagicMock()
    spawner.trigger = AsyncMock()
    finding = _make_finding(severity=1)
    finding.fingerprint = _FP

    result = await dispatch_qa_investigation(
        pool=db_pool,
        triaged_finding=_make_triaged(finding),
        patrol_id=patrol,
        config=QaDispatchConfig(),
        repo_root=MagicMock(),
        spawner=spawner,
        gh_token=None,
    )

    assert result.reason == "publication_paused"
    assert await db_pool.fetchval("SELECT count(*) FROM public.healing_attempts") == before
    spawner.trigger.assert_not_called()


async def test_hold_expires_with_window_and_is_inert_for_other_fingerprints(db_pool) -> None:
    patrol = await _patrol(db_pool)
    await _attempt(db_pool, patrol, error_detail=_PUBLICATION_UNAVAILABLE_DETAIL, retained=True)
    assert await get_paused_proposal(db_pool, _FP, 7) is not None
    assert await get_paused_proposal(db_pool, "d" * 64, 7) is None
    # A zero-day window has already elapsed.
    assert await get_paused_proposal(db_pool, _FP, 0) is None


async def test_unretained_with_existing_export_row_counts_as_failure(db_pool) -> None:
    """A re-run that met an existing export fails closed; the row alone is not a success."""
    patrol = await _patrol(db_pool)
    for _ in range(5):
        await _attempt(
            db_pool,
            patrol,
            error_detail="publication_unretained: export_changed; nothing was pushed",
            retained=True,
        )
    assert await _tripped_both_ways(db_pool) == (True, True)


async def test_unsupported_diagnoses_are_execution_successes_in_admission_and_dashboard(
    db_pool,
) -> None:
    patrol = await _patrol(db_pool)
    for _ in range(7):
        await _attempt(db_pool, patrol, error_detail=_DIAGNOSIS_UNSUPPORTED_DETAIL)
    assert await _tripped_both_ways(db_pool) == (False, False)


async def test_an_unsupported_diagnosis_holds_its_fingerprint_without_an_export(db_pool) -> None:
    patrol = await _patrol(db_pool)
    attempt = await _attempt(db_pool, patrol, error_detail=_DIAGNOSIS_UNSUPPORTED_DETAIL)

    assert await get_paused_proposal(db_pool, _FP, 7) == attempt
    assert await get_paused_proposal(db_pool, "b" * 64, 7) is None
