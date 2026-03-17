"""Tests for check_token_quota and record_token_usage in butlers.core.model_routing.

Covers:
- QuotaStatus dataclass structure
- check_token_quota fast path: no limits row → allowed=True, zero usage, no DB query
- check_token_quota: usage within both limits → allowed=True
- check_token_quota: 24h limit exceeded → allowed=False
- check_token_quota: 30d limit exceeded → allowed=False
- check_token_quota: one window unlimited, other exceeded → allowed=False
- check_token_quota: fail-open on DB error → allowed=True, warning logged
- record_token_usage: inserts a row into the ledger
- record_token_usage: best-effort on error (does not raise)

[bu-21g0]
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
def test_quota_status_dataclass_fields() -> None:
    """QuotaStatus dataclass exposes the required fields."""
    qs = QuotaStatus(
        allowed=True,
        usage_24h=100,
        limit_24h=500,
        usage_30d=200,
        limit_30d=None,
    )
    assert qs.allowed is True
    assert qs.usage_24h == 100
    assert qs.limit_24h == 500
    assert qs.usage_30d == 200
    assert qs.limit_30d is None


@pytest.mark.unit
def test_quota_status_allowed_false() -> None:
    """QuotaStatus with allowed=False is constructed correctly."""
    qs = QuotaStatus(
        allowed=False,
        usage_24h=1000,
        limit_24h=500,
        usage_30d=0,
        limit_30d=None,
    )
    assert qs.allowed is False
    assert qs.usage_24h == 1000
    assert qs.limit_24h == 500


@pytest.mark.unit
async def test_check_token_quota_fail_open_on_db_error() -> None:
    """check_token_quota returns allowed=True and logs a warning on DB error."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("connection refused"))
    entry_id = uuid.uuid4()

    with patch("butlers.core.model_routing.logger") as mock_logger:
        result = await check_token_quota(pool, entry_id)

    assert result.allowed is True
    assert result.usage_24h == 0
    assert result.limit_24h is None
    assert result.usage_30d == 0
    assert result.limit_30d is None
    mock_logger.warning.assert_called_once()


@pytest.mark.unit
async def test_record_token_usage_best_effort_on_error() -> None:
    """record_token_usage does not raise when the INSERT fails."""
    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=RuntimeError("missing partition"))
    entry_id = uuid.uuid4()

    with patch("butlers.core.model_routing.logger") as mock_logger:
        # Must not raise
        await record_token_usage(
            pool,
            catalog_entry_id=entry_id,
            butler_name="test-butler",
            session_id=None,
            input_tokens=100,
            output_tokens=50,
        )

    mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@asynccontextmanager
async def _make_pool(postgres_container: Any) -> AsyncIterator[asyncpg.Pool]:
    """Create a fresh DB with model routing + quota tables and yield a pool."""
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
    """Create shared schema with model catalog, limits, and ledger tables."""
    await pool.execute("CREATE SCHEMA IF NOT EXISTS shared")

    await pool.execute("""
        CREATE TABLE IF NOT EXISTS shared.model_catalog (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias           TEXT NOT NULL,
            runtime_type    TEXT NOT NULL,
            model_id        TEXT NOT NULL,
            extra_args      JSONB NOT NULL DEFAULT '[]'::jsonb,
            complexity_tier TEXT NOT NULL DEFAULT 'medium',
            enabled         BOOLEAN NOT NULL DEFAULT true,
            priority        INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_model_catalog_alias UNIQUE (alias),
            CONSTRAINT chk_model_catalog_complexity_tier
                CHECK (complexity_tier IN ('trivial', 'medium', 'high', 'extra_high', 'discretion'))
        )
    """)

    await pool.execute("""
        CREATE TABLE IF NOT EXISTS shared.token_limits (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            catalog_entry_id UUID NOT NULL UNIQUE
                REFERENCES shared.model_catalog(id) ON DELETE CASCADE,
            limit_24h        BIGINT,
            limit_30d        BIGINT,
            reset_24h_at     TIMESTAMPTZ,
            reset_30d_at     TIMESTAMPTZ,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Non-partitioned ledger for test simplicity (no pg_partman in CI)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS shared.token_usage_ledger (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            catalog_entry_id UUID NOT NULL
                REFERENCES shared.model_catalog(id) ON DELETE CASCADE,
            butler_name      TEXT NOT NULL,
            session_id       UUID,
            input_tokens     INTEGER NOT NULL DEFAULT 0,
            output_tokens    INTEGER NOT NULL DEFAULT 0,
            recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    await pool.execute("""
        CREATE INDEX IF NOT EXISTS idx_ledger_entry_time
            ON shared.token_usage_ledger (catalog_entry_id, recorded_at)
    """)


async def _insert_catalog_entry(pool: asyncpg.Pool, *, alias: str) -> uuid.UUID:
    """Insert a minimal catalog entry and return its UUID."""
    row = await pool.fetchrow(
        """
        INSERT INTO shared.model_catalog
            (alias, runtime_type, model_id, complexity_tier, enabled, priority)
        VALUES ($1, 'claude', 'test-model', 'medium', true, 0)
        RETURNING id
        """,
        alias,
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
    """Insert a token_limits row for the given catalog entry."""
    await pool.execute(
        """
        INSERT INTO shared.token_limits
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
    """Insert a ledger row for testing quota checks."""
    if recorded_at is None:
        recorded_at = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO shared.token_usage_ledger
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
# check_token_quota integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_check_quota_no_limits_row_fast_path(postgres_container: Any) -> None:
    """Entry with no token_limits row → fast path, allowed=True, zero usage."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="no-limits-entry")

        result = await check_token_quota(pool, entry_id)

        assert result.allowed is True
        assert result.usage_24h == 0
        assert result.limit_24h is None
        assert result.usage_30d == 0
        assert result.limit_30d is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_check_quota_within_both_limits(postgres_container: Any) -> None:
    """Usage below both limits → allowed=True."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="within-limits")
        await _insert_limits(pool, catalog_entry_id=entry_id, limit_24h=1000, limit_30d=10000)
        await _insert_ledger_row(
            pool, catalog_entry_id=entry_id, input_tokens=100, output_tokens=50
        )

        result = await check_token_quota(pool, entry_id)

        assert result.allowed is True
        assert result.usage_24h == 150  # 100 + 50
        assert result.limit_24h == 1000
        assert result.usage_30d == 150
        assert result.limit_30d == 10000


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_check_quota_24h_limit_exceeded(postgres_container: Any) -> None:
    """Usage >= limit_24h → allowed=False."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="24h-exceeded")
        await _insert_limits(pool, catalog_entry_id=entry_id, limit_24h=100, limit_30d=10000)
        # Insert usage that exceeds 24h limit
        await _insert_ledger_row(pool, catalog_entry_id=entry_id, input_tokens=60, output_tokens=50)

        result = await check_token_quota(pool, entry_id)

        assert result.allowed is False
        assert result.usage_24h == 110  # 60 + 50
        assert result.limit_24h == 100
        assert result.usage_24h >= result.limit_24h


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_check_quota_30d_limit_exceeded(postgres_container: Any) -> None:
    """Usage >= limit_30d → allowed=False."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="30d-exceeded")
        await _insert_limits(pool, catalog_entry_id=entry_id, limit_24h=None, limit_30d=100)
        await _insert_ledger_row(pool, catalog_entry_id=entry_id, input_tokens=60, output_tokens=50)

        result = await check_token_quota(pool, entry_id)

        assert result.allowed is False
        assert result.usage_30d == 110
        assert result.limit_30d == 100
        assert result.usage_30d >= result.limit_30d


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_check_quota_24h_unlimited_30d_exceeded(postgres_container: Any) -> None:
    """limit_24h is NULL (unlimited) but 30d exceeded → allowed=False."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="30d-only-limit")
        # 24h unlimited, 30d limited
        await _insert_limits(pool, catalog_entry_id=entry_id, limit_24h=None, limit_30d=50)
        await _insert_ledger_row(pool, catalog_entry_id=entry_id, input_tokens=30, output_tokens=30)

        result = await check_token_quota(pool, entry_id)

        assert result.allowed is False
        assert result.limit_24h is None
        assert result.usage_30d == 60
        assert result.limit_30d == 50


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_check_quota_exactly_at_limit_is_blocked(postgres_container: Any) -> None:
    """Usage exactly equal to limit → allowed=False (>= blocks)."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="exact-limit")
        await _insert_limits(pool, catalog_entry_id=entry_id, limit_24h=100, limit_30d=None)
        await _insert_ledger_row(pool, catalog_entry_id=entry_id, input_tokens=60, output_tokens=40)

        result = await check_token_quota(pool, entry_id)

        assert result.allowed is False
        assert result.usage_24h == 100
        assert result.limit_24h == 100


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_check_quota_reset_24h_at_respected(postgres_container: Any) -> None:
    """reset_24h_at excludes old usage from 24h window."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="reset-24h")
        # Limit of 100 for 24h
        reset_time = datetime.now(UTC) - timedelta(hours=1)
        await _insert_limits(
            pool,
            catalog_entry_id=entry_id,
            limit_24h=100,
            limit_30d=None,
            reset_24h_at=reset_time,
        )
        # Old row (before reset_24h_at) — should NOT count toward 24h window
        old_time = datetime.now(UTC) - timedelta(hours=12)
        await _insert_ledger_row(
            pool,
            catalog_entry_id=entry_id,
            input_tokens=80,
            output_tokens=0,
            recorded_at=old_time,
        )
        # Recent row (after reset_24h_at) — counts
        await _insert_ledger_row(
            pool,
            catalog_entry_id=entry_id,
            input_tokens=20,
            output_tokens=0,
        )

        result = await check_token_quota(pool, entry_id)

        # Only 20 tokens counted in 24h window (old 80 excluded by reset)
        assert result.usage_24h == 20
        assert result.allowed is True  # 20 < 100


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_check_quota_old_usage_outside_30d_excluded(postgres_container: Any) -> None:
    """Usage older than 30 days is excluded from the 30d window."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="old-usage-30d")
        await _insert_limits(pool, catalog_entry_id=entry_id, limit_24h=None, limit_30d=500)
        # Very old row — outside both windows
        ancient = datetime.now(UTC) - timedelta(days=31)
        await _insert_ledger_row(
            pool,
            catalog_entry_id=entry_id,
            input_tokens=400,
            output_tokens=0,
            recorded_at=ancient,
        )
        # Recent row
        await _insert_ledger_row(pool, catalog_entry_id=entry_id, input_tokens=50, output_tokens=0)

        result = await check_token_quota(pool, entry_id)

        assert result.usage_30d == 50
        assert result.allowed is True


# ---------------------------------------------------------------------------
# record_token_usage integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_record_token_usage_inserts_row(postgres_container: Any) -> None:
    """record_token_usage inserts a row with the correct fields."""
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
            FROM shared.token_usage_ledger
            WHERE catalog_entry_id = $1
            """,
            entry_id,
        )
        assert row is not None
        assert row["catalog_entry_id"] == entry_id
        assert row["butler_name"] == "test-butler"
        assert row["session_id"] == session_id
        assert row["input_tokens"] == 123
        assert row["output_tokens"] == 456


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_record_token_usage_null_session_id(postgres_container: Any) -> None:
    """record_token_usage accepts NULL session_id (discretion dispatcher calls)."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="record-null-session")

        await record_token_usage(
            pool,
            catalog_entry_id=entry_id,
            butler_name="__discretion__",
            session_id=None,
            input_tokens=10,
            output_tokens=20,
        )

        row = await pool.fetchrow(
            "SELECT session_id, butler_name FROM shared.token_usage_ledger"
            " WHERE catalog_entry_id = $1",
            entry_id,
        )
        assert row is not None
        assert row["session_id"] is None
        assert row["butler_name"] == "__discretion__"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_record_then_quota_check_reflects_new_usage(postgres_container: Any) -> None:
    """After record_token_usage, check_token_quota reflects the recorded tokens."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="record-then-check")
        await _insert_limits(pool, catalog_entry_id=entry_id, limit_24h=1000, limit_30d=5000)

        await record_token_usage(
            pool,
            catalog_entry_id=entry_id,
            butler_name="test-butler",
            session_id=None,
            input_tokens=300,
            output_tokens=200,
        )

        result = await check_token_quota(pool, entry_id)

        assert result.usage_24h == 500  # 300 + 200
        assert result.usage_30d == 500
        assert result.allowed is True  # 500 < 1000
