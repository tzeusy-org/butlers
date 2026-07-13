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

These tests exercise the real SQL against a provisioned Postgres, proving:
  - a reset writes ONE ``breaker_resets`` row and re-admits the next dispatch,
  - NO synthetic ``healing_attempts`` row is created on reset,
  - the reset boundary (not a forged sentinel) is what clears the chain, and
    real failures accumulated *after* a reset re-trip the breaker.
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
from butlers.api.routers.healing import _get_db_manager
from butlers.core.healing.dispatch import _is_circuit_breaker_tripped

pytestmark = pytest.mark.integration

_BASE_URL = "http://testserver"


async def _make_schema(pool: asyncpg.Pool) -> None:
    """Minimal slice of the real schema touched by the breaker queries + reset."""
    await pool.execute("""
        CREATE TABLE public.healing_attempts (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            fingerprint        TEXT NOT NULL,
            butler_name        TEXT NOT NULL DEFAULT 'general',
            status             TEXT NOT NULL,
            severity           INTEGER NOT NULL DEFAULT 2,
            exception_type     TEXT NOT NULL DEFAULT 'RuntimeError',
            call_site          TEXT NOT NULL DEFAULT 'src/foo.py:bar',
            session_ids        UUID[] NOT NULL DEFAULT '{}',
            closed_at          TIMESTAMPTZ,
            healing_session_id UUID
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


async def _add_failed_attempt(pool: asyncpg.Pool, *, closed_at: datetime) -> None:
    await pool.execute(
        """
        INSERT INTO public.healing_attempts (fingerprint, status, closed_at, healing_session_id)
        VALUES ($1, 'failed', $2, gen_random_uuid())
        """,
        "a" * 64,
        closed_at,
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
        return await client.post("/api/healing/circuit-breaker/reset")


@pytest.mark.pg_clock
async def test_reset_records_ledger_row_and_admits_without_fabrication(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)

        # Five real failed investigations trip the breaker.
        base = datetime.now(tz=UTC) - timedelta(hours=1)
        for i in range(5):
            await _add_failed_attempt(pool, closed_at=base + timedelta(minutes=i))

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
            await pool.fetchval(
                "SELECT count(*) FROM public.healing_attempts WHERE status = 'failed'"
            )
            == 5
        )

        # The breaker now admits the next dispatch.
        assert await _is_circuit_breaker_tripped(pool, 5) is False


async def test_reset_boundary_retrips_on_new_post_reset_failures(
    provisioned_postgres_pool,
) -> None:
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)
        base = datetime.now(tz=UTC) - timedelta(hours=1)
        for i in range(5):
            await _add_failed_attempt(pool, closed_at=base + timedelta(minutes=i))
        assert await _is_circuit_breaker_tripped(pool, 5) is True

        # Record a reset AFTER those failures.
        reset_at = datetime.now(tz=UTC)
        await pool.execute(
            "INSERT INTO public.breaker_resets (breaker, reset_at) VALUES ('healing', $1)",
            reset_at,
        )
        assert await _is_circuit_breaker_tripped(pool, 5) is False

        # Four fresh post-reset failures — still below threshold, still admits.
        for i in range(4):
            await _add_failed_attempt(pool, closed_at=reset_at + timedelta(minutes=i + 1))
        assert await _is_circuit_breaker_tripped(pool, 5) is False

        # The fifth post-reset failure re-trips the breaker on the real chain.
        await _add_failed_attempt(pool, closed_at=reset_at + timedelta(minutes=10))
        assert await _is_circuit_breaker_tripped(pool, 5) is True


async def test_reset_unconditionally_writes_ledger_row_even_when_not_tripped(
    provisioned_postgres_pool,
) -> None:
    """The healing reset endpoint (unlike QA's) has no admission-gated no-op —
    it always records a reset row when called, matching pre-existing behavior
    of the retired synthetic-insert handler (which always inserted)."""
    async with provisioned_postgres_pool(schema="public") as pool:
        await _make_schema(pool)
        base = datetime.now(tz=UTC) - timedelta(hours=1)
        for i in range(2):  # below threshold
            await _add_failed_attempt(pool, closed_at=base + timedelta(minutes=i))

        resp = await _post_reset(_app_for(pool))
        assert resp.status_code == 200
        assert await pool.fetchval("SELECT count(*) FROM public.breaker_resets") == 1
