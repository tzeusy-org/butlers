"""Real-Postgres proof for the QA circuit-breaker *truth* fix (bu-533qx.1).

The old ``POST /api/qa/circuit-breaker/reset`` handler broke the
consecutive-failure chain by INSERTing a synthetic clean ``qa_patrols`` row and
a fake ``manual_reset`` ``healing_attempts`` row — fabricated history that
``/summary`` and ``staffer_status`` then read back as "healthy". The fix
replaces that with an honest ``public.breaker_resets`` ledger consulted by both
the dispatch-admission gate (``_is_circuit_breaker_tripped``) and every
dashboard breaker query.

These tests exercise the real SQL against the real core-migrated schema, proving:
  - a reset writes ONE ``breaker_resets`` row and re-admits the next dispatch,
  - NO synthetic patrol / attempt rows are created on reset,
  - the reset boundary (not a forged sentinel) is what clears the chain, and
    real failures accumulated *after* a reset re-trip the breaker,
  - the genuine failure history stays visible (the real error patrol remains
    the most-recent patrol ``/summary`` would surface).

core_305 stamps ``closed_at`` from database time, and resets default ``reset_at`` to
database time, so attempts and resets order by the sequence in which they are written.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db, get_mcp_manager
from butlers.core.qa.dispatch import _is_circuit_breaker_tripped
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_BASE_URL = "http://testserver"


async def _add_failed_attempt(pool: asyncpg.Pool, patrol_id) -> None:
    await pool.execute(
        """
        INSERT INTO public.healing_attempts
            (fingerprint, butler_name, status, severity, exception_type, call_site,
             healing_session_id, qa_patrol_id)
        VALUES ($1, 'general', 'failed', 2, 'RuntimeError', 'src/foo.py:bar',
                gen_random_uuid(), $2)
        """,
        "b" * 64,
        patrol_id,
    )


def _app_for(pool: asyncpg.Pool):
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    return app


async def _post_reset(app) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:
        return await client.post("/api/qa/circuit-breaker/reset")


@pytest.mark.pg_clock
async def test_reset_records_ledger_row_and_admits_without_fabrication(db_pool) -> None:
    pool = db_pool

    # A genuine error patrol + five real failed investigations trip the breaker.
    patrol_id = await pool.fetchval(
        "INSERT INTO public.qa_patrols (status, completed_at) VALUES ('error', now()) RETURNING id"
    )
    for _ in range(5):
        await _add_failed_attempt(pool, patrol_id)

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
    assert await pool.fetchval("SELECT count(*) FROM public.qa_patrols WHERE status = 'clean'") == 0
    assert await pool.fetchval("SELECT count(*) FROM public.qa_patrols") == 1  # only the error one
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


async def test_reset_boundary_retrips_on_new_post_reset_failures(db_pool) -> None:
    pool = db_pool
    patrol_id = await pool.fetchval(
        "INSERT INTO public.qa_patrols (status) VALUES ('error') RETURNING id"
    )
    for _ in range(5):
        await _add_failed_attempt(pool, patrol_id)
    assert await _is_circuit_breaker_tripped(pool, 5) is True

    # Record a reset AFTER those failures.
    await pool.execute("INSERT INTO public.breaker_resets (breaker) VALUES ('qa')")
    assert await _is_circuit_breaker_tripped(pool, 5) is False

    # Four fresh post-reset failures — still below threshold, still admits.
    for _ in range(4):
        await _add_failed_attempt(pool, patrol_id)
    assert await _is_circuit_breaker_tripped(pool, 5) is False

    # The fifth post-reset failure re-trips the breaker on the real chain.
    await _add_failed_attempt(pool, patrol_id)
    assert await _is_circuit_breaker_tripped(pool, 5) is True


async def test_reset_is_noop_when_not_tripped(db_pool) -> None:
    pool = db_pool
    patrol_id = await pool.fetchval(
        "INSERT INTO public.qa_patrols (status) VALUES ('error') RETURNING id"
    )
    for _ in range(3):  # below threshold
        await _add_failed_attempt(pool, patrol_id)

    resp = await _post_reset(_app_for(pool))
    assert resp.status_code == 200
    assert resp.json()["data"]["reset"] is False
    # No ledger row written when there was nothing to reset.
    assert await pool.fetchval("SELECT count(*) FROM public.breaker_resets") == 0
