"""Real-Postgres regression: purpose-tagged spend attribution (bu-qvnce.12).

Exercises the actual production write paths — not just mocked-pool unit
tests — against a real, fully-migrated Postgres instance (testcontainers):

- core_156 additively adds ``public.token_usage_ledger.purpose`` (nullable,
  no default) without disturbing pre-existing rows or the cache-token
  columns core_155 added just before it.
- core_157 seeds the two new ``runtime_type='api'`` catalog rows
  (``api-haiku-cheap`` / ``api-haiku-specialty``) at higher priority than
  every pre-existing 'cheap'/'specialty' entry, so ``resolve_model`` picks
  them by default — proving the "flip" (slice 2) actually took effect on a
  migrated database, not just in ``model_catalog_defaults.toml`` (which has
  no effect post-bootstrap; see core_157's docstring).
- ``DiscretionDispatcher.call(identity=...)`` writes the per-connector
  identity as ``butler_name`` and ``purpose="discretion"`` through a real
  asyncpg INSERT (the mocked-pool unit tests in
  ``tests/connectors/test_discretion_dispatcher.py`` cannot catch a
  parameter-count/column-order mismatch between ``_LEDGER_INSERT_SQL`` and
  the real table — this test can).
"""

from __future__ import annotations

import shutil
import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.connectors.discretion_dispatcher import DiscretionDispatcher
from butlers.core.model_routing import Complexity, record_token_usage, resolve_model
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    # Historical partitions so an out-of-band insert never hits "no partition found".
    await p.execute("""
        DO $$
        DECLARE month_start TIMESTAMPTZ := date_trunc('month', now());
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS public.%I PARTITION OF public.token_usage_ledger '
                'FOR VALUES FROM (%L) TO (%L)',
                format('token_usage_ledger_%s', to_char(month_start, 'YYYYMM')),
                month_start,
                month_start + INTERVAL '1 month'
            );
        END $$;
    """)
    yield p
    await p.close()


async def test_purpose_column_exists_and_is_nullable(pool: asyncpg.Pool) -> None:
    row = await pool.fetchrow(
        """
        SELECT is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'token_usage_ledger'
          AND column_name = 'purpose'
        """
    )
    assert row is not None, "core_156 did not add token_usage_ledger.purpose"
    assert row["is_nullable"] == "YES"
    assert row["column_default"] is None


async def test_seeded_api_catalog_rows_win_cheap_and_specialty_tiers(pool: asyncpg.Pool) -> None:
    """core_157's seed rows outrank every pre-existing entry for their tier."""
    cheap = await resolve_model(pool, "switchboard", Complexity.CHEAP)
    assert cheap is not None
    runtime_type, model_id, _extra_args, _id, _timeout = cheap
    assert runtime_type == "api"
    assert model_id == "claude-haiku-4-5-20251001"

    specialty = await resolve_model(pool, "__discretion__", Complexity.SPECIALTY)
    assert specialty is not None
    assert specialty[0] == "api"


async def test_discretion_dispatcher_writes_identity_and_purpose_via_real_pool(
    pool: asyncpg.Pool,
) -> None:
    """DiscretionDispatcher.call(identity=...) round-trips through a real INSERT."""
    dispatcher = DiscretionDispatcher(pool=pool)
    fake_adapter = AsyncMock()
    fake_adapter.invoke = AsyncMock(
        return_value=("FORWARD: looks real", [], {"input_tokens": 7, "output_tokens": 3})
    )

    with (
        patch.object(dispatcher, "_get_or_create_adapter", return_value=fake_adapter),
        patch.object(dispatcher, "_resolve_provider_config", AsyncMock(return_value=None)),
    ):
        result = await dispatcher.call("hi", identity="tg:555")

    assert result == "FORWARD: looks real"

    row = await pool.fetchrow(
        """
        SELECT butler_name, session_id, purpose, input_tokens, output_tokens
        FROM public.token_usage_ledger
        WHERE butler_name = 'tg:555'
        ORDER BY recorded_at DESC LIMIT 1
        """
    )
    assert row is not None
    assert row["session_id"] is None
    assert row["purpose"] == "discretion"
    assert row["input_tokens"] == 7 and row["output_tokens"] == 3


async def test_record_token_usage_purpose_defaults_null_when_omitted(pool: asyncpg.Pool) -> None:
    """Backward compatibility: callers that don't pass purpose= get an honest NULL,
    never a fabricated category."""
    row = await pool.fetchrow("SELECT id FROM public.model_catalog WHERE alias = 'api-haiku-cheap'")
    assert row is not None
    entry_id = row["id"]

    await record_token_usage(
        pool,
        catalog_entry_id=entry_id,
        butler_name="atlas",
        session_id=uuid.uuid4(),
        input_tokens=1,
        output_tokens=1,
    )
    written = await pool.fetchrow(
        "SELECT purpose FROM public.token_usage_ledger WHERE catalog_entry_id = $1 "
        "ORDER BY recorded_at DESC LIMIT 1",
        entry_id,
    )
    assert written is not None and written["purpose"] is None
