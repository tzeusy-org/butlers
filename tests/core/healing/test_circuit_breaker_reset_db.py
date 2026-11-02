"""Real-Postgres proof for the healing circuit-breaker *truth* fix (bu-dz7ac).

The old ``POST /api/healing/circuit-breaker/reset`` handler broke the
consecutive-failure chain by INSERTing a synthetic ``pr_merged``
``healing_attempts`` row with a ``reset-sentinel-<uuid>`` fingerprint and a
synthetic ``healing_session_id`` — fabricated history that
``get_recent_terminal_statuses`` (consumed by both the dispatch-admission gate
``_is_circuit_breaker_tripped`` and the dashboard's ``_compute_breaker_state``)
then read back as a genuine successful investigation. This is the same
fabricated-history class the QA breaker shed in bu-533qx.1 (core_164).

The fix replaces that with an honest ``public.breaker_resets`` ledger row
(``breaker='healing'``) consulted by ``get_recent_terminal_statuses``.

These tests exercise the real SQL against the real core-migrated schema, proving:
  - a reset writes ONE ``breaker_resets`` row and re-admits the next dispatch,
  - NO synthetic ``healing_attempts`` row is created on reset,
  - the reset boundary (not a forged sentinel) is what clears the chain, and
    real failures accumulated *after* a reset re-trip the breaker.

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
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

_BASE_URL = "http://testserver"


async def _is_circuit_breaker_tripped(pool, threshold):
    from butlers.api.routers.healing import _compute_breaker_state

    return (await _compute_breaker_state(pool, threshold))[0]


async def _add_failed_attempt(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """
        INSERT INTO public.healing_attempts
            (fingerprint, butler_name, status, severity, exception_type, call_site,
             healing_session_id)
        VALUES ($1, 'general', 'failed', 2, 'RuntimeError', 'src/foo.py:bar',
                gen_random_uuid())
        """,
        "a" * 64,
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
        return await client.post("/api/healing/circuit-breaker/reset")


@pytest.mark.pg_clock
async def test_reset_records_ledger_row_and_admits_without_fabrication(db_pool) -> None:
    pool = db_pool

    # Five real failed investigations trip the breaker.
    for _ in range(5):
        await _add_failed_attempt(pool)

    assert await _is_circuit_breaker_tripped(pool, 5) is True

    # Reset via the real endpoint.
    resp = await _post_reset(_app_for(pool))
    assert resp.status_code == 200
    assert resp.json()["tripped"] is False

    # Exactly one honest ledger row — who/when/why.
    reset_rows = await pool.fetch("SELECT breaker, reset_by, reason FROM public.breaker_resets")
    assert len(reset_rows) == 1
    assert reset_rows[0]["breaker"] == "healing"
    assert reset_rows[0]["reset_by"] == "dashboard"
    assert reset_rows[0]["reason"]

    # NO fabricated history: no synthetic reset-sentinel attempt row, the
    # five genuine failed attempts remain untouched.
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM public.healing_attempts WHERE fingerprint LIKE 'reset-sentinel-%'"
        )
        == 0
    )
    assert await pool.fetchval("SELECT count(*) FROM public.healing_attempts") == 5
    assert (
        await pool.fetchval("SELECT count(*) FROM public.healing_attempts WHERE status = 'failed'")
        == 5
    )

    # The breaker now admits the next dispatch.
    assert await _is_circuit_breaker_tripped(pool, 5) is False


async def test_reset_boundary_retrips_on_new_post_reset_failures(db_pool) -> None:
    pool = db_pool
    for _ in range(5):
        await _add_failed_attempt(pool)
    assert await _is_circuit_breaker_tripped(pool, 5) is True

    # Record a reset AFTER those failures.
    await pool.execute("INSERT INTO public.breaker_resets (breaker) VALUES ('healing')")
    assert await _is_circuit_breaker_tripped(pool, 5) is False

    # Four fresh post-reset failures — still below threshold, still admits.
    for _ in range(4):
        await _add_failed_attempt(pool)
    assert await _is_circuit_breaker_tripped(pool, 5) is False

    # The fifth post-reset failure re-trips the breaker on the real chain.
    await _add_failed_attempt(pool)
    assert await _is_circuit_breaker_tripped(pool, 5) is True


async def test_reset_unconditionally_writes_ledger_row_even_when_not_tripped(db_pool) -> None:
    """The healing reset endpoint (unlike QA's) has no admission-gated no-op —
    it always records a reset row when called, matching pre-existing behavior
    of the retired synthetic-insert handler (which always inserted)."""
    pool = db_pool
    for _ in range(2):  # below threshold
        await _add_failed_attempt(pool)

    resp = await _post_reset(_app_for(pool))
    assert resp.status_code == 200
    assert await pool.fetchval("SELECT count(*) FROM public.breaker_resets") == 1
