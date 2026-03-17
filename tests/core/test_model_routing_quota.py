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


async def _insert_catalog_entry(
    pool: asyncpg.Pool, *, alias: str, enabled: bool = True
) -> uuid.UUID:
    """Insert a minimal catalog entry and return its UUID."""
    row = await pool.fetchrow(
        """
        INSERT INTO shared.model_catalog
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
async def test_check_quota_reset_30d_at_respected(postgres_container: Any) -> None:
    """reset_30d_at excludes old usage from 30d window."""
    async with _make_pool(postgres_container) as pool:
        entry_id = await _insert_catalog_entry(pool, alias="reset-30d")
        # Limit of 100 for 30d
        reset_time = datetime.now(UTC) - timedelta(days=10)
        await _insert_limits(
            pool,
            catalog_entry_id=entry_id,
            limit_24h=None,
            limit_30d=100,
            reset_30d_at=reset_time,
        )
        # Old row (before reset_30d_at) — should NOT count toward 30d window
        old_time = datetime.now(UTC) - timedelta(days=20)
        await _insert_ledger_row(
            pool,
            catalog_entry_id=entry_id,
            input_tokens=80,
            output_tokens=0,
            recorded_at=old_time,
        )
        # Recent row (after reset_30d_at) — counts
        await _insert_ledger_row(
            pool,
            catalog_entry_id=entry_id,
            input_tokens=20,
            output_tokens=0,
        )

        result = await check_token_quota(pool, entry_id)

        # Only 20 tokens counted in 30d window (old 80 excluded by reset)
        assert result.usage_30d == 20
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


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_check_quota_disabled_entry_with_override_still_enforces_limits(
    postgres_container: Any,
) -> None:
    """Disabled entry with limits re-enabled via override still enforces those limits.

    Scenario from spec: a catalog entry is globally disabled but a butler override
    re-enables it. The global token_limits row still applies to that butler's usage
    of the entry, and quota enforcement uses the same limits regardless of whether
    the entry was enabled globally or via override.

    This test verifies that check_token_quota does NOT check the enabled state —
    it only uses catalog_entry_id to look up limits and ledger usage, so limits
    are enforced regardless of the global enabled flag.
    """
    async with _make_pool(postgres_container) as pool:
        # Create a globally disabled catalog entry
        entry_id = await _insert_catalog_entry(pool, alias="disabled-with-limits", enabled=False)

        # Configure token limits for this entry (even though it's globally disabled)
        limit_24h = 500
        await _insert_limits(
            pool,
            catalog_entry_id=entry_id,
            limit_24h=limit_24h,
            limit_30d=None,
        )

        # Add some usage to the ledger
        await _insert_ledger_row(
            pool,
            catalog_entry_id=entry_id,
            input_tokens=300,
            output_tokens=100,  # Total: 400 tokens
        )

        # Even though the entry is globally disabled, check_token_quota should still
        # enforce the limits using this entry's ID
        result = await check_token_quota(pool, entry_id)

        assert result.allowed is True  # 400 < 500
        assert result.usage_24h == 400
        assert result.limit_24h == limit_24h

        # Add more usage to exceed the 24h limit
        await _insert_ledger_row(
            pool,
            catalog_entry_id=entry_id,
            input_tokens=60,
            output_tokens=50,  # Total: 110 tokens, cumulative: 510 > 500
        )

        # Now the quota should be exceeded
        result = await check_token_quota(pool, entry_id)

        assert result.allowed is False  # 510 >= 500
        assert result.usage_24h == 510
        assert result.limit_24h == limit_24h


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


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_delete_and_recreate_resets_usage_history(postgres_container: Any) -> None:
    """When catalog entry is deleted and recreated with same alias, usage is reset.

    This tests the spec scenario 'Delete and recreate resets usage history':
    - Old entry accumulates token usage and is subject to limits
    - Entry is deleted (CASCADE deletes ledger rows and limits row)
    - New entry with same alias is created (new UUID)
    - New entry has zero usage and no limits history
    - New entry is not blocked by prior usage

    This validates that the CASCADE constraint on token_usage_ledger and
    token_limits tables properly enforces clean state separation between
    the old and new catalog entries.
    """
    async with _make_pool(postgres_container) as pool:
        # Create first entry with a limit
        entry1_id = await _insert_catalog_entry(pool, alias="resettable-entry")
        await _insert_limits(
            pool,
            catalog_entry_id=entry1_id,
            limit_24h=500,
            limit_30d=5000,
        )

        # Accumulate heavy usage for first entry
        await _insert_ledger_row(
            pool,
            catalog_entry_id=entry1_id,
            butler_name="test-butler",
            input_tokens=400,
            output_tokens=150,  # Total: 550 tokens, exceeds 24h limit of 500
        )

        # Verify first entry is quota-blocked
        result1 = await check_token_quota(pool, entry1_id)
        assert result1.allowed is False
        assert result1.usage_24h == 550
        assert result1.limit_24h == 500

        # Delete the first entry (CASCADE removes ledger rows and limits)
        await pool.execute(
            "DELETE FROM shared.model_catalog WHERE id = $1",
            entry1_id,
        )

        # Verify ledger rows are gone (CASCADE worked)
        ledger_count = await pool.fetchval(
            "SELECT COUNT(*) FROM shared.token_usage_ledger WHERE catalog_entry_id = $1",
            entry1_id,
        )
        assert ledger_count == 0

        # Verify limits row is gone (CASCADE worked)
        limits_count = await pool.fetchval(
            "SELECT COUNT(*) FROM shared.token_limits WHERE catalog_entry_id = $1",
            entry1_id,
        )
        assert limits_count == 0

        # Create a new entry with the same alias (will have a new UUID)
        entry2_id = await _insert_catalog_entry(pool, alias="resettable-entry")

        # Verify new entry has different UUID
        assert entry2_id != entry1_id

        # Verify new entry has zero usage history (no old ledger rows)
        result2 = await check_token_quota(pool, entry2_id)
        assert result2.allowed is True
        assert result2.usage_24h == 0
        assert result2.limit_24h is None  # No limits row for new entry
        assert result2.usage_30d == 0
        assert result2.limit_30d is None

        # Set limits on new entry at same level as original
        await _insert_limits(
            pool,
            catalog_entry_id=entry2_id,
            limit_24h=500,
            limit_30d=5000,
        )

        # Verify new entry is not blocked despite original entry's heavy usage
        result2_with_limits = await check_token_quota(pool, entry2_id)
        assert result2_with_limits.allowed is True
        assert result2_with_limits.usage_24h == 0
        assert result2_with_limits.limit_24h == 500

        # Record some usage on the new entry (well within limits)
        await record_token_usage(
            pool,
            catalog_entry_id=entry2_id,
            butler_name="test-butler",
            session_id=None,
            input_tokens=100,
            output_tokens=50,
        )

        # Verify new entry accurately reflects its own usage (not old entry's)
        result2_after_usage = await check_token_quota(pool, entry2_id)
        assert result2_after_usage.allowed is True
        assert result2_after_usage.usage_24h == 150  # Only this entry's usage
        assert result2_after_usage.limit_24h == 500
