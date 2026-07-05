"""Real-Postgres verification for the bu-38ae1 classifier split.

The unit tests in ``test_secrets_v2_inventory.py`` hand-construct asyncpg
exception instances (``UndefinedColumnError(...)``, ``UndefinedTableError(...)``)
to exercise ``_is_missing_secrets_schema_error`` / the ``DegradedSources``
tracker wiring in ``_fetch_system_secrets``. Per repo memory
(mocked-pool-vs-integration-gap), a classifier keyed off a specific asyncpg
exception TYPE is exactly the kind of thing that can silently drift from what
the real driver actually raises against a real backend — mocks only prove the
code *would* behave correctly *if* the exception looked like that.

This test runs the real ``_fetch_system_secrets`` query against a migrated
Postgres (via testcontainers/Docker):

- dropping a column from a real, migrated ``butler_secrets`` table reproduces
  a genuine ``UndefinedColumnError`` (schema drift) and must be tracked as a
  degraded source;
- a schema that was never migrated (no ``butler_secrets`` table at all)
  reproduces a genuine ``UndefinedTableError`` (legitimate absence) and must
  NOT be tracked.

Mirrors the real-pool harness in ``test_relationship_entities_concentration_db.py``.
"""

from __future__ import annotations

import logging
import shutil

import asyncpg
import pytest

from butlers.api.degraded import DegradedSources
from butlers.api.routers.secrets_v2 import _fetch_system_secrets
from butlers.db import register_jsonb_codec, schema_search_path
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

_logger = logging.getLogger("test_secrets_v2_schema_drift_db")


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the core chain into a butler-like schema named 'finance'.

    The 'core' chain is what creates the real, migrated ``butler_secrets``
    table (core_001, plus test-state columns from core_106/core_117).
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
        schemas={"core": "finance"},
    )


@pytest.fixture
async def finance_pool(migrated_db_url: str):
    """Pool scoped to the migrated 'finance' schema — has a real butler_secrets table."""
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=2,
        init=register_jsonb_codec,
        server_settings={"search_path": schema_search_path("finance")},
    )
    yield p
    await p.close()


@pytest.fixture
async def empty_schema_pool(migrated_db_url: str):
    """Pool scoped to a schema that was never migrated — no butler_secrets table
    exists there (nor in 'public', since the core chain was mapped to 'finance').
    """
    admin_conn = await asyncpg.connect(migrated_db_url)
    try:
        await admin_conn.execute("CREATE SCHEMA IF NOT EXISTS neverbutler")
    finally:
        await admin_conn.close()

    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=2,
        init=register_jsonb_codec,
        server_settings={"search_path": schema_search_path("neverbutler")},
    )
    yield p
    await p.close()


async def test_dropped_column_raises_real_undefined_column_error_and_is_tracked(
    finance_pool: asyncpg.Pool,
):
    """Schema drift (a test-state column dropped from a real, existing table)
    must raise a genuine ``UndefinedColumnError`` and be tracked as degraded —
    reproducing the bu-urcwx production symptom, now with API-level signal
    (bu-38ae1)."""
    await finance_pool.execute("ALTER TABLE butler_secrets DROP COLUMN last_verified")

    tracker = DegradedSources(_logger)
    rows = await _fetch_system_secrets(finance_pool, "finance", tracker=tracker)

    assert rows == []
    assert tracker.failed is True
    assert tracker.names == ["finance"]


async def test_missing_table_raises_real_undefined_table_error_and_is_not_tracked(
    empty_schema_pool: asyncpg.Pool,
):
    """A butler schema with no butler_secrets table at all (never migrated) is
    a legitimate absence, not a degraded source."""
    tracker = DegradedSources(_logger)
    rows = await _fetch_system_secrets(empty_schema_pool, "neverbutler", tracker=tracker)

    assert rows == []
    assert tracker.failed is False
    assert tracker.names == []
