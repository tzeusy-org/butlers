"""Real-Postgres proof that publication_unavailable outcomes do not trip the QA breaker.

Since #4595 every committing investigation ends ``failed`` with the
``publication_unavailable`` detail because automated publication is disabled by design.
Those outcomes say nothing about the investigation or the system, so neither the
dispatch-admission gate nor the dashboard breaker may count them toward a failure storm,
while genuine failures keep tripping it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.api.routers.qa import (
    _CIRCUIT_BREAKER_THRESHOLD,
    _compute_circuit_breaker_state,
    _fetch_recent_circuit_breaker_rows,
)
from butlers.core.qa.dispatch import (
    _PUBLICATION_UNAVAILABLE_DETAIL,
    _is_circuit_breaker_tripped,
)

pytestmark = pytest.mark.integration

_THRESHOLD = _CIRCUIT_BREAKER_THRESHOLD


async def _make_schema(pool: asyncpg.Pool) -> None:
    await pool.execute("""
        CREATE TABLE public.qa_patrols (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            status       TEXT NOT NULL DEFAULT 'running'
        )
    """)
    await pool.execute("""
        CREATE TABLE public.healing_attempts (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            status             TEXT NOT NULL,
            closed_at          TIMESTAMPTZ,
            healing_session_id UUID,
            error_detail       TEXT,
            qa_patrol_id       UUID REFERENCES public.qa_patrols(id) ON DELETE SET NULL
        )
    """)
    await pool.execute("""
        CREATE TABLE public.breaker_resets (
            id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            breaker  TEXT NOT NULL,
            reset_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            reset_by TEXT NOT NULL DEFAULT 'dashboard',
            reason   TEXT
        )
    """)


async def _add_failed(
    pool: asyncpg.Pool, patrol_id, *, minute: int, error_detail: str | None
) -> None:
    await pool.execute(
        """
        INSERT INTO public.healing_attempts
            (status, closed_at, healing_session_id, error_detail, qa_patrol_id)
        VALUES ('failed', $1, gen_random_uuid(), $2, $3)
        """,
        datetime.now(tz=UTC) - timedelta(hours=1) + timedelta(minutes=minute),
        error_detail,
        patrol_id,
    )


async def _patrol(pool: asyncpg.Pool):
    return await pool.fetchval(
        "INSERT INTO public.qa_patrols (status) VALUES ('error') RETURNING id"
    )


async def _dashboard_tripped(pool: asyncpg.Pool) -> bool:
    rows = await _fetch_recent_circuit_breaker_rows(pool, limit=_THRESHOLD)
    return _compute_circuit_breaker_state(rows, threshold=_THRESHOLD)[2]


async def test_publication_unavailable_outcomes_do_not_trip_the_breaker(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)
        patrol_id = await _patrol(pool)
        for minute in range(_THRESHOLD + 2):
            await _add_failed(
                pool, patrol_id, minute=minute, error_detail=_PUBLICATION_UNAVAILABLE_DETAIL
            )

        assert await _is_circuit_breaker_tripped(pool, _THRESHOLD) is False
        assert await _dashboard_tripped(pool) is False


async def test_genuine_failures_still_trip_the_breaker_around_publication_outcomes(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)
        patrol_id = await _patrol(pool)
        # Four genuine failures interleaved with publication outcomes: still below threshold.
        for minute in range(4):
            await _add_failed(pool, patrol_id, minute=minute * 2, error_detail="boom")
            await _add_failed(
                pool,
                patrol_id,
                minute=minute * 2 + 1,
                error_detail=_PUBLICATION_UNAVAILABLE_DETAIL,
            )
        assert await _is_circuit_breaker_tripped(pool, _THRESHOLD) is False
        assert await _dashboard_tripped(pool) is False

        # The fifth genuine failure trips it, publication outcomes notwithstanding.
        await _add_failed(pool, patrol_id, minute=20, error_detail="boom")
        assert await _is_circuit_breaker_tripped(pool, _THRESHOLD) is True
        assert await _dashboard_tripped(pool) is True
