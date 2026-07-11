"""Real-Postgres proof for the QA circuit-breaker *truth* fix (bu-533qx.1).

The old ``POST /api/qa/circuit-breaker/reset`` handler broke the
consecutive-failure chain by INSERTing a synthetic clean ``qa_patrols`` row and
a fake ``manual_reset`` ``healing_attempts`` row — fabricated history that
``/summary`` and ``staffer_status`` then read back as "healthy". The fix
replaces that with an honest ``public.breaker_resets`` ledger consulted by both
the dispatch-admission gate (``_is_circuit_breaker_tripped``) and every
dashboard breaker query.

These tests exercise the real SQL against a provisioned Postgres, proving:
  - a reset writes ONE ``breaker_resets`` row and re-admits the next dispatch,
  - NO synthetic patrol / attempt rows are created on reset,
  - the reset boundary (not a forged sentinel) is what clears the chain, and
    real failures accumulated *after* a reset re-trip the breaker,
  - the genuine failure history stays visible (the real error patrol remains
    the most-recent patrol ``/summary`` would surface).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_mcp_manager
from butlers.api.routers.qa import _get_db_manager
from butlers.core.qa.dispatch import _is_circuit_breaker_tripped

pytestmark = pytest.mark.integration

_BASE_URL = "http://testserver"


async def _make_schema(pool: asyncpg.Pool) -> None:
    """Minimal slice of the real schema touched by the breaker queries + reset.

    Mirrors the production column set and the ``ON DELETE SET NULL`` FK from
    core_054 closely enough that the reset endpoint and admission gate run
    their genuine SQL unchanged.
    """
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


async def _add_failed_attempt(pool: asyncpg.Pool, patrol_id, *, closed_at: datetime) -> None:
    await pool.execute(
        """
        INSERT INTO public.healing_attempts (status, closed_at, healing_session_id, qa_patrol_id)
        VALUES ('failed', $1, gen_random_uuid(), $2)
        """,
        closed_at,
        patrol_id,
    )


def _app_for(pool: asyncpg.Pool):
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    return app


async def _post_reset(app) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:
        return await client.post("/api/qa/circuit-breaker/reset")


async def test_reset_records_ledger_row_and_admits_without_fabrication(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)

        # A genuine error patrol + five real failed investigations trip the breaker.
        patrol_id = await pool.fetchval(
            "INSERT INTO public.qa_patrols (status, completed_at) "
            "VALUES ('error', now()) RETURNING id"
        )
        base = datetime.now(tz=UTC) - timedelta(hours=1)
        for i in range(5):
            await _add_failed_attempt(pool, patrol_id, closed_at=base + timedelta(minutes=i))

        assert await _is_circuit_breaker_tripped(pool, 5) is True

        # Reset via the real endpoint.
        resp = await _post_reset(_app_for(pool))
        assert resp.status_code == 200
        assert resp.json()["data"]["reset"] is True

        # Exactly one honest ledger row — who/when/why.
        reset_rows = await pool.fetch("SELECT breaker, reset_by, reason FROM public.breaker_resets")
        assert len(reset_rows) == 1
        assert reset_rows[0]["breaker"] == "qa"
        assert reset_rows[0]["reset_by"] == "dashboard"
        assert reset_rows[0]["reason"]

        # NO fabricated history: no synthetic clean patrol, no manual_reset attempt.
        assert (
            await pool.fetchval("SELECT count(*) FROM public.qa_patrols WHERE status = 'clean'")
            == 0
        )
        assert (
            await pool.fetchval("SELECT count(*) FROM public.qa_patrols") == 1
        )  # only the error one
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM public.healing_attempts WHERE status = 'manual_reset'"
            )
            == 0
        )
        assert await pool.fetchval("SELECT count(*) FROM public.healing_attempts") == 5

        # The breaker now admits the next dispatch...
        assert await _is_circuit_breaker_tripped(pool, 5) is False

        # ...while the genuine failure history stays visible: the most-recent
        # non-running patrol /summary would surface is still the real 'error' one.
        last_status = await pool.fetchval(
            "SELECT status FROM public.qa_patrols WHERE status != 'running' "
            "ORDER BY started_at DESC LIMIT 1"
        )
        assert last_status == "error"


async def test_reset_boundary_retrips_on_new_post_reset_failures(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)
        patrol_id = await pool.fetchval(
            "INSERT INTO public.qa_patrols (status) VALUES ('error') RETURNING id"
        )
        base = datetime.now(tz=UTC) - timedelta(hours=1)
        for i in range(5):
            await _add_failed_attempt(pool, patrol_id, closed_at=base + timedelta(minutes=i))
        assert await _is_circuit_breaker_tripped(pool, 5) is True

        # Record a reset AFTER those failures.
        reset_at = datetime.now(tz=UTC)
        await pool.execute(
            "INSERT INTO public.breaker_resets (breaker, reset_at) VALUES ('qa', $1)", reset_at
        )
        assert await _is_circuit_breaker_tripped(pool, 5) is False

        # Four fresh post-reset failures — still below threshold, still admits.
        for i in range(4):
            await _add_failed_attempt(
                pool, patrol_id, closed_at=reset_at + timedelta(minutes=i + 1)
            )
        assert await _is_circuit_breaker_tripped(pool, 5) is False

        # The fifth post-reset failure re-trips the breaker on the real chain.
        await _add_failed_attempt(pool, patrol_id, closed_at=reset_at + timedelta(minutes=10))
        assert await _is_circuit_breaker_tripped(pool, 5) is True


async def test_reset_is_noop_when_not_tripped(provisioned_postgres_pool) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)
        patrol_id = await pool.fetchval(
            "INSERT INTO public.qa_patrols (status) VALUES ('error') RETURNING id"
        )
        base = datetime.now(tz=UTC) - timedelta(hours=1)
        for i in range(3):  # below threshold
            await _add_failed_attempt(pool, patrol_id, closed_at=base + timedelta(minutes=i))

        resp = await _post_reset(_app_for(pool))
        assert resp.status_code == 200
        assert resp.json()["data"]["reset"] is False
        # No ledger row written when there was nothing to reset.
        assert await pool.fetchval("SELECT count(*) FROM public.breaker_resets") == 0
