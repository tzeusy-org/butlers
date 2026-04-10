"""Tests for check_token_quota and record_token_usage in butlers.core.model_routing.

Covers:
- QuotaStatus dataclass structure
- fail-open on DB error; record_token_usage best-effort
- quota: no limits row → fast path allowed=True
- quota: within limits → allowed=True
- quota: 24h/30d/exact limit exceeded → allowed=False
- quota: reset_at respects excluded-old-usage window
- quota: old usage outside 30d window excluded
- record_token_usage: inserts row; NULL session_id accepted; reflected in quota check
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.core.model_routing import QuotaStatus, check_token_quota, record_token_usage

docker_available = shutil.which("docker") is not None

# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_quota_unit_behaviors() -> None:
    """QuotaStatus fields; fail-open on DB error; record_token_usage best-effort."""
    # QuotaStatus fields
    qs = QuotaStatus(allowed=True, usage_24h=100, limit_24h=500, usage_30d=200, limit_30d=None)
    assert qs.allowed is True and qs.usage_24h == 100 and qs.limit_24h == 500
    assert qs.usage_30d == 200 and qs.limit_30d is None
    qs2 = QuotaStatus(allowed=False, usage_24h=1000, limit_24h=500, usage_30d=0, limit_30d=None)
    assert qs2.allowed is False and qs2.usage_24h == 1000

    # check_token_quota: fail-open on DB error
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("connection refused"))
    entry_id = uuid.uuid4()
    with patch("butlers.core.model_routing.logger") as mock_logger:
        result = await check_token_quota(pool, entry_id)
    assert result.allowed is True
    assert result.usage_24h == 0 and result.limit_24h is None
    assert result.usage_30d == 0 and result.limit_30d is None
    mock_logger.warning.assert_called_once()

    # record_token_usage: best-effort (does not raise on INSERT error)
    pool2 = MagicMock()
    pool2.execute = AsyncMock(side_effect=RuntimeError("missing partition"))
    with patch("butlers.core.model_routing.logger") as mock_logger2:
        await record_token_usage(
            pool2,
            catalog_entry_id=uuid.uuid4(),
            butler_name="test-butler",
            session_id=None,
            input_tokens=100,
            output_tokens=50,
        )
    mock_logger2.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@asynccontextmanager
async def _make_pool(postgres_container: Any) -> AsyncIterator[asyncpg.Pool]:
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
        max_size=3,
    )
    try:
        await _create_schema(pool)
        yield pool
    finally:
        await pool.close()


async def _create_schema(pool: asyncpg.Pool) -> None:
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.model_catalog (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias           TEXT NOT NULL,
            runtime_type    TEXT NOT NULL DEFAULT 'claude',
            model_id        TEXT NOT NULL DEFAULT 'test-model',
            extra_args      JSONB NOT NULL DEFAULT '[]'::jsonb,
            complexity_tier TEXT NOT NULL DEFAULT 'medium',
            enabled         BOOLEAN NOT NULL DEFAULT true,
            priority        INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_model_catalog_alias UNIQUE (alias)
        )
    """)

    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.token_limits (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            catalog_entry_id UUID NOT NULL UNIQUE
                REFERENCES public.model_catalog(id) ON DELETE CASCADE,
            limit_24h        BIGINT,
            limit_30d        BIGINT,
            reset_24h_at     TIMESTAMPTZ,
            reset_30d_at     TIMESTAMPTZ,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.token_usage_ledger (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            catalog_entry_id UUID NOT NULL
                REFERENCES public.model_catalog(id) ON DELETE CASCADE,
            butler_name      TEXT NOT NULL,
            session_id       UUID,
            input_tokens     INTEGER NOT NULL DEFAULT 0,
            output_tokens    INTEGER NOT NULL DEFAULT 0,
            recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    await pool.execute("""
        CREATE INDEX IF NOT EXISTS idx_ledger_entry_time
            ON public.token_usage_ledger (catalog_entry_id, recorded_at)
    """)


async def _insert_catalog_entry(
    pool: asyncpg.Pool, *, alias: str, enabled: bool = True
) -> uuid.UUID:
    row = await pool.fetchrow(
        """
        INSERT INTO public.model_catalog
            (alias, runtime_type, model_id, complexity_tier, enabled, priority)
        VALUES ($1, 'claude', 'test-model', 'medium', $2, 0)
        RETURNING id
        """,
        alias,
        enabled,
    )
    return row["id"]


async def _insert_limits(
    pool: asyncpg.Pool,
    *,
    catalog_entry_id: uuid.UUID,
    limit_24h: int | None = None,
    limit_30d: int | None = None,
    reset_24h_at: datetime | None = None,
    reset_30d_at: datetime | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO public.token_limits
            (catalog_entry_id, limit_24h, limit_30d, reset_24h_at, reset_30d_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        catalog_entry_id,
        limit_24h,
        limit_30d,
        reset_24h_at,
        reset_30d_at,
    )


async def _insert_ledger_row(
    pool: asyncpg.Pool,
    *,
    catalog_entry_id: uuid.UUID,
    butler_name: str = "test-butler",
    session_id: uuid.UUID | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    recorded_at: datetime | None = None,
) -> None:
    if recorded_at is None:
        recorded_at = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO public.token_usage_ledger
            (catalog_entry_id, butler_name, session_id, input_tokens, output_tokens, recorded_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        catalog_entry_id,
        butler_name,
        session_id,
        input_tokens,
        output_tokens,
        recorded_at,
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_quota_no_limits_row_fast_path(postgres_container: Any) -> None:
    """No token_limits row → fast path, allowed=True, zero usage."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="no-limits-entry")
        result = await check_token_quota(pool, entry_id)
        assert result.allowed is True
        assert result.usage_24h == 0 and result.limit_24h is None
        assert result.usage_30d == 0 and result.limit_30d is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_quota_within_limits_and_exceeded(postgres_container: Any) -> None:
    """Within both limits → allowed=True. Exceeding 24h or 30d → allowed=False."""
    async with _make_pool(postgres_container) as pool:
        # Within limits
        eid = await _insert_catalog_entry(pool, alias="within-limits")
        await _insert_limits(pool, catalog_entry_id=eid, limit_24h=1000, limit_30d=10000)
        await _insert_ledger_row(pool, catalog_entry_id=eid, input_tokens=100, output_tokens=50)
        r = await check_token_quota(pool, eid)
        assert r.allowed is True and r.usage_24h == 150 and r.limit_24h == 1000

        # 24h exceeded
        eid2 = await _insert_catalog_entry(pool, alias="24h-exceeded")
        await _insert_limits(pool, catalog_entry_id=eid2, limit_24h=100, limit_30d=10000)
        await _insert_ledger_row(pool, catalog_entry_id=eid2, input_tokens=60, output_tokens=50)
        r2 = await check_token_quota(pool, eid2)
        assert r2.allowed is False and r2.usage_24h == 110

        # 30d exceeded (24h unlimited)
        eid3 = await _insert_catalog_entry(pool, alias="30d-exceeded")
        await _insert_limits(pool, catalog_entry_id=eid3, limit_24h=None, limit_30d=100)
        await _insert_ledger_row(pool, catalog_entry_id=eid3, input_tokens=60, output_tokens=50)
        r3 = await check_token_quota(pool, eid3)
        assert r3.allowed is False and r3.usage_30d == 110 and r3.limit_24h is None

        # Exactly at limit is blocked
        eid4 = await _insert_catalog_entry(pool, alias="exact-limit")
        await _insert_limits(pool, catalog_entry_id=eid4, limit_24h=100)
        await _insert_ledger_row(pool, catalog_entry_id=eid4, input_tokens=60, output_tokens=40)
        r4 = await check_token_quota(pool, eid4)
        assert r4.allowed is False and r4.usage_24h == 100


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_quota_reset_and_window_exclusion(postgres_container: Any) -> None:
    """reset_at excludes old usage; usage older than 30d excluded from 30d window."""
    async with _make_pool(postgres_container) as pool:
        # reset_24h_at: old row excluded, recent row counted
        eid = await _insert_catalog_entry(pool, alias="reset-24h")
        reset_time = datetime.now(UTC) - timedelta(hours=1)
        await _insert_limits(pool, catalog_entry_id=eid, limit_24h=100, reset_24h_at=reset_time)
        await _insert_ledger_row(
            pool,
            catalog_entry_id=eid,
            input_tokens=80,
            recorded_at=datetime.now(UTC) - timedelta(hours=12),  # before reset
        )
        await _insert_ledger_row(pool, catalog_entry_id=eid, input_tokens=20)  # after reset
        r = await check_token_quota(pool, eid)
        assert r.usage_24h == 20 and r.allowed is True

        # Old usage outside 30d excluded
        eid2 = await _insert_catalog_entry(pool, alias="old-30d")
        await _insert_limits(pool, catalog_entry_id=eid2, limit_30d=500)
        await _insert_ledger_row(
            pool,
            catalog_entry_id=eid2,
            input_tokens=400,
            recorded_at=datetime.now(UTC) - timedelta(days=31),
        )
        await _insert_ledger_row(pool, catalog_entry_id=eid2, input_tokens=50)
        r2 = await check_token_quota(pool, eid2)
        assert r2.usage_30d == 50 and r2.allowed is True


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_record_token_usage_and_reflected_in_quota(postgres_container: Any) -> None:
    """record_token_usage inserts row with correct fields; NULL session_id accepted;
    subsequent quota check reflects recorded tokens."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="record-test")
        session_id = uuid.uuid4()

        await record_token_usage(
            pool,
            catalog_entry_id=entry_id,
            butler_name="test-butler",
            session_id=session_id,
            input_tokens=123,
            output_tokens=456,
        )

        row = await pool.fetchrow(
            """
            SELECT catalog_entry_id, butler_name, session_id, input_tokens, output_tokens
            FROM public.token_usage_ledger WHERE catalog_entry_id = $1
            """,
            entry_id,
        )
        assert row is not None
        assert row["butler_name"] == "test-butler"
        assert row["session_id"] == session_id
        assert row["input_tokens"] == 123 and row["output_tokens"] == 456

        # NULL session_id accepted
        entry2_id = await _insert_catalog_entry(pool, alias="record-null-session")
        await record_token_usage(
            pool,
            catalog_entry_id=entry2_id,
            butler_name="__discretion__",
            session_id=None,
            input_tokens=10,
            output_tokens=20,
        )
        row2 = await pool.fetchrow(
            "SELECT session_id, butler_name FROM public.token_usage_ledger"
            " WHERE catalog_entry_id = $1",
            entry2_id,
        )
        assert row2 is not None and row2["session_id"] is None

        # Quota check reflects recorded usage
        await _insert_limits(pool, catalog_entry_id=entry_id, limit_24h=1000, limit_30d=5000)
        result = await check_token_quota(pool, entry_id)
        assert result.usage_24h == 579  # 123 + 456
        assert result.allowed is True
