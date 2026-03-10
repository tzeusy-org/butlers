"""Real-DB integration tests for the memory module migration chain.

These tests apply the full mem_001 → mem_022 migration chain to a fresh
PostgreSQL schema and then exercise store_episode / store_fact / store_rule
against that live schema to verify that tenant_id, retention_class, and
sensitivity are persisted correctly.

Marked with ``pytest.mark.integration`` and skipped when Docker is absent
(matching the project-wide convention used in tests/core/test_db.py and
tests/integration/test_integration.py).
"""

from __future__ import annotations

import importlib.util
import shutil
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Docker gate — skip entire module when Docker is unavailable.
# ---------------------------------------------------------------------------

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    # Run tests in the session event loop so asyncpg pools created in
    # session-scoped fixtures (asyncio_default_fixture_loop_scope=session)
    # remain usable.  Without this, function-scoped test loops cannot
    # acquire connections from the session-loop pool.
    pytest.mark.asyncio(loop_scope="session"),
]

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_MEMORY_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "src" / "butlers" / "modules" / "memory"
)
_STORAGE_PATH = _MEMORY_MODULE_PATH / "storage.py"


# ---------------------------------------------------------------------------
# Load storage functions dynamically (roster/ is not a Python package).
# ---------------------------------------------------------------------------


def _load_storage_module():
    """Load storage.py from src/butlers/modules/memory/ via importlib."""
    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_storage = _load_storage_module()
store_episode = _storage.store_episode
store_fact = _storage.store_fact
store_rule = _storage.store_rule


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a shared pgvector-enabled PostgreSQL container for all tests in this module.

    Uses ``pgvector/pgvector:pg16`` (same base as production docker-compose) to
    ensure the ``vector`` extension is available for the memory migration chain.
    """
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg


_TEST_BUTLER_SCHEMA = "memory_test"


@pytest.fixture
async def memory_pool(postgres_container):
    """Provision a fresh DB, apply the full memory migration chain, return a pool.

    Each test gets its own isolated database; the container is shared
    (module scope) so startup cost is paid once per module.

    Memory migrations are run against a dedicated butler schema (``memory_test``)
    which mirrors production: ``SET search_path TO memory_test, shared, public``
    is applied so unqualified table references in migrations (e.g. ``UPDATE
    entities`` in mem_013) resolve correctly through ``shared.entities``.
    """
    from butlers.db import Database
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db = Database(
        db_name=db_name,
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    await db.provision()

    db_url = f"postgresql://{db.user}:{db.password}@{db.host}:{db.port}/{db.db_name}"
    # Core creates shared schema tables (shared.entities, shared.contacts, etc.).
    # Memory chain requires search_path to include 'shared' so that unqualified
    # references like 'entities' in mem_013 resolve to shared.entities.
    await run_migrations(db_url, chain="core")
    await run_migrations(db_url, chain="memory", schema=_TEST_BUTLER_SCHEMA)

    # Connect with schema-scoped search_path so that unqualified table
    # references in storage.py (e.g. INSERT INTO episodes) resolve to the
    # butler schema, matching production behaviour.
    db_schema = Database(
        db_name=db_name,
        schema=_TEST_BUTLER_SCHEMA,
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    pool = await db_schema.connect()
    try:
        yield pool
    finally:
        await db_schema.close()


@pytest.fixture
def embedding_engine() -> MagicMock:
    """Mock EmbeddingEngine producing a stable 384-d zero vector."""
    engine = MagicMock()
    engine.embed.return_value = [0.0] * 384
    return engine


# ---------------------------------------------------------------------------
# Helper: introspect actual DB column names
# ---------------------------------------------------------------------------


async def _get_columns(pool, table: str, schema: str = _TEST_BUTLER_SCHEMA) -> set[str]:
    """Return the set of column names for the given table in *schema*."""
    rows = await pool.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        """,
        schema,
        table,
    )
    return {row["column_name"] for row in rows}


async def _get_column_type(
    pool, table: str, column: str, schema: str = _TEST_BUTLER_SCHEMA
) -> str | None:
    """Return the data_type for a specific column, or None if it does not exist."""
    row = await pool.fetchrow(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = $1
          AND table_name = $2
          AND column_name = $3
        """,
        schema,
        table,
        column,
    )
    return row["data_type"] if row else None


# ---------------------------------------------------------------------------
# 1. Schema verification — critical columns exist with correct types
# ---------------------------------------------------------------------------


class TestMemoryMigrationSchema:
    """Verify the applied migration chain produces the expected schema."""

    async def test_core_tables_exist(self, memory_pool) -> None:
        """All four core memory tables exist in the butler schema after the full chain."""
        rows = await memory_pool.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = $1
            ORDER BY tablename
            """,
            _TEST_BUTLER_SCHEMA,
        )
        names = {row["tablename"] for row in rows}
        for table in (
            "episodes",
            "facts",
            "rules",
            "memory_links",
            "memory_events",
            "predicate_registry",
            "memory_policies",
            "rule_applications",
        ):
            assert table in names, f"Expected table '{table}' to exist after migration"

    async def test_facts_tenant_and_lineage_columns(self, memory_pool) -> None:
        """facts has tenant_id, request_id, retention_class, sensitivity (mem_014)."""
        cols = await _get_columns(memory_pool, "facts")
        for col in ("tenant_id", "request_id", "retention_class", "sensitivity"):
            assert col in cols, f"facts.{col} missing — check mem_014 migration"

    async def test_facts_temporal_idempotency_columns(self, memory_pool) -> None:
        """facts has idempotency_key, observed_at, invalid_at (mem_016) and valid_at (mem_007)."""
        cols = await _get_columns(memory_pool, "facts")
        for col in ("idempotency_key", "observed_at", "invalid_at", "valid_at"):
            assert col in cols, f"facts.{col} missing — check mem_007/mem_016 migrations"

    async def test_facts_tenant_id_type(self, memory_pool) -> None:
        """facts.tenant_id is a text column."""
        dtype = await _get_column_type(memory_pool, "facts", "tenant_id")
        assert dtype == "text", f"Expected facts.tenant_id to be text, got {dtype!r}"

    async def test_facts_observed_at_type(self, memory_pool) -> None:
        """facts.observed_at is a timestamptz column."""
        dtype = await _get_column_type(memory_pool, "facts", "observed_at")
        assert dtype in ("timestamp with time zone",), (
            f"Expected timestamptz for facts.observed_at, got {dtype!r}"
        )

    async def test_facts_invalid_at_type(self, memory_pool) -> None:
        """facts.invalid_at is a timestamptz column (nullable)."""
        dtype = await _get_column_type(memory_pool, "facts", "invalid_at")
        assert dtype in ("timestamp with time zone",), (
            f"Expected timestamptz for facts.invalid_at, got {dtype!r}"
        )

    async def test_episodes_tenant_and_lineage_columns(self, memory_pool) -> None:
        """episodes has tenant_id, request_id, retention_class, sensitivity (mem_014)."""
        cols = await _get_columns(memory_pool, "episodes")
        for col in ("tenant_id", "request_id", "retention_class", "sensitivity"):
            assert col in cols, f"episodes.{col} missing — check mem_014 migration"

    async def test_episodes_lease_columns(self, memory_pool) -> None:
        """episodes has leased_until, leased_by, dead_letter_reason columns (mem_015)."""
        cols = await _get_columns(memory_pool, "episodes")
        for col in ("leased_until", "leased_by", "dead_letter_reason"):
            assert col in cols, f"episodes.{col} missing — check mem_015 migration"

    async def test_episodes_consolidation_attempt_rename(self, memory_pool) -> None:
        """episodes.consolidation_attempts exists (renamed from retry_count in mem_015)."""
        cols = await _get_columns(memory_pool, "episodes")
        assert "consolidation_attempts" in cols, (
            "episodes.consolidation_attempts missing — check mem_015 column rename"
        )
        # The old column name must not exist after the rename.
        assert "retry_count" not in cols, "episodes.retry_count still present after mem_015 rename"

    async def test_rules_tenant_and_lineage_columns(self, memory_pool) -> None:
        """rules has tenant_id, request_id, retention_class, sensitivity (mem_014)."""
        cols = await _get_columns(memory_pool, "rules")
        for col in ("tenant_id", "request_id", "retention_class", "sensitivity"):
            assert col in cols, f"rules.{col} missing — check mem_014 migration"

    async def test_memory_policies_columns(self, memory_pool) -> None:
        """memory_policies has the corrected columns from mem_020."""
        cols = await _get_columns(memory_pool, "memory_policies")
        for col in (
            "retention_class",
            "ttl_days",
            "decay_rate",
            "min_retrieval_confidence",
            "archive_before_delete",
            "allow_summarization",
        ):
            assert col in cols, f"memory_policies.{col} missing — check mem_020 migration"
        # The old mem_017 name must not exist post-rename.
        assert "default_ttl_days" not in cols, (
            "memory_policies.default_ttl_days still present after mem_020 rename"
        )


# ---------------------------------------------------------------------------
# 2. memory_policies seed verification
# ---------------------------------------------------------------------------


class TestMemoryPoliciesSeed:
    """Verify the memory_policies table is seeded with 8 correct rows."""

    async def test_policies_has_eight_rows(self, memory_pool) -> None:
        """memory_policies contains exactly 8 seeded retention classes."""
        count = await memory_pool.fetchval("SELECT COUNT(*) FROM memory_policies")
        assert count == 8, f"Expected 8 seeded memory_policies rows, got {count}"

    async def test_policies_expected_retention_classes(self, memory_pool) -> None:
        """All 8 retention classes from the spec are present."""
        rows = await memory_pool.fetch("SELECT retention_class FROM memory_policies")
        classes = {row["retention_class"] for row in rows}
        expected = {
            "transient",
            "episodic",
            "operational",
            "personal_profile",
            "health_log",
            "financial_log",
            "rule",
            "anti_pattern",
        }
        assert classes == expected, (
            f"memory_policies retention classes mismatch.\n"
            f"  Expected: {sorted(expected)}\n"
            f"  Got:      {sorted(classes)}"
        )

    async def test_policies_transient_has_ttl(self, memory_pool) -> None:
        """transient retention class has a non-null ttl_days value."""
        row = await memory_pool.fetchrow(
            "SELECT ttl_days FROM memory_policies WHERE retention_class = 'transient'"
        )
        assert row is not None, "transient retention class not found"
        assert row["ttl_days"] is not None, "transient.ttl_days must not be NULL"
        assert row["ttl_days"] > 0, f"transient.ttl_days must be positive, got {row['ttl_days']}"

    async def test_policies_operational_no_ttl(self, memory_pool) -> None:
        """operational retention class has NULL ttl_days (never expires by default)."""
        row = await memory_pool.fetchrow(
            "SELECT ttl_days FROM memory_policies WHERE retention_class = 'operational'"
        )
        assert row is not None, "operational retention class not found"
        assert row["ttl_days"] is None, (
            f"operational.ttl_days should be NULL, got {row['ttl_days']}"
        )

    async def test_policies_all_have_decay_rate_and_confidence(self, memory_pool) -> None:
        """All 8 retention class rows have non-null decay_rate and min_retrieval_confidence."""
        rows = await memory_pool.fetch(
            "SELECT retention_class, decay_rate, min_retrieval_confidence FROM memory_policies"
        )
        assert len(rows) == 8, f"Expected 8 rows, got {len(rows)}"
        for row in rows:
            rc = row["retention_class"]
            assert row["decay_rate"] is not None, (
                f"memory_policies.decay_rate is NULL for retention_class={rc!r}"
            )
            assert row["min_retrieval_confidence"] is not None, (
                f"memory_policies.min_retrieval_confidence is NULL for retention_class={rc!r}"
            )


# ---------------------------------------------------------------------------
# 3. Storage round-trips against live schema
# ---------------------------------------------------------------------------


class TestStorageRoundTrips:
    """Verify store_episode / store_fact / store_rule write correct values."""

    async def test_store_episode_persists_tenant_id(self, memory_pool, embedding_engine) -> None:
        """store_episode writes tenant_id to the episodes table."""
        tenant = "test-tenant-ep"
        ep_id = await store_episode(
            memory_pool,
            "The user discussed their travel plans.",
            "test-butler",
            embedding_engine,
            tenant_id=tenant,
        )
        row = await memory_pool.fetchrow("SELECT tenant_id FROM episodes WHERE id = $1", ep_id)
        assert row is not None, "Episode row not found after store_episode"
        assert row["tenant_id"] == tenant, (
            f"Expected episodes.tenant_id={tenant!r}, got {row['tenant_id']!r}"
        )

    async def test_store_episode_persists_retention_class(
        self, memory_pool, embedding_engine
    ) -> None:
        """store_episode writes the default retention_class to the episodes table."""
        ep_id = await store_episode(
            memory_pool,
            "Brief episode for retention class check.",
            "test-butler",
            embedding_engine,
        )
        row = await memory_pool.fetchrow(
            "SELECT retention_class FROM episodes WHERE id = $1", ep_id
        )
        assert row is not None, "Episode row not found after store_episode"
        # Default retention_class for episodes is 'transient' (mem_014 column DEFAULT).
        assert row["retention_class"] == "transient", (
            f"Expected episodes.retention_class='transient', got {row['retention_class']!r}"
        )

    async def test_store_fact_persists_tenant_id(self, memory_pool, embedding_engine) -> None:
        """store_fact writes tenant_id to the facts table."""
        tenant = "test-tenant-fact"
        fact_id = await store_fact(
            memory_pool,
            "user",
            "preferred_language",
            "Python",
            embedding_engine,
            tenant_id=tenant,
        )
        row = await memory_pool.fetchrow("SELECT tenant_id FROM facts WHERE id = $1", fact_id)
        assert row is not None, "Fact row not found after store_fact"
        assert row["tenant_id"] == tenant, (
            f"Expected facts.tenant_id={tenant!r}, got {row['tenant_id']!r}"
        )

    async def test_store_fact_persists_retention_class(self, memory_pool, embedding_engine) -> None:
        """store_fact writes the caller-supplied retention_class."""
        fact_id = await store_fact(
            memory_pool,
            "user",
            "city",
            "Berlin",
            embedding_engine,
            retention_class="personal_profile",
        )
        row = await memory_pool.fetchrow("SELECT retention_class FROM facts WHERE id = $1", fact_id)
        assert row is not None, "Fact row not found after store_fact"
        assert row["retention_class"] == "personal_profile", (
            f"Expected facts.retention_class='personal_profile', got {row['retention_class']!r}"
        )

    async def test_store_fact_persists_sensitivity(self, memory_pool, embedding_engine) -> None:
        """store_fact writes the caller-supplied sensitivity."""
        fact_id = await store_fact(
            memory_pool,
            "user",
            "health_condition",
            "diabetes",
            embedding_engine,
            sensitivity="pii",
        )
        row = await memory_pool.fetchrow("SELECT sensitivity FROM facts WHERE id = $1", fact_id)
        assert row is not None, "Fact row not found after store_fact"
        assert row["sensitivity"] == "pii", (
            f"Expected facts.sensitivity='pii', got {row['sensitivity']!r}"
        )

    async def test_store_rule_persists_tenant_id(self, memory_pool, embedding_engine) -> None:
        """store_rule writes tenant_id to the rules table."""
        tenant = "test-tenant-rule"
        rule_id = await store_rule(
            memory_pool,
            "Always greet the user by name when starting a conversation.",
            embedding_engine,
            tenant_id=tenant,
        )
        row = await memory_pool.fetchrow("SELECT tenant_id FROM rules WHERE id = $1", rule_id)
        assert row is not None, "Rule row not found after store_rule"
        assert row["tenant_id"] == tenant, (
            f"Expected rules.tenant_id={tenant!r}, got {row['tenant_id']!r}"
        )

    async def test_store_rule_persists_retention_class(self, memory_pool, embedding_engine) -> None:
        """store_rule writes the caller-supplied retention_class."""
        rule_id = await store_rule(
            memory_pool,
            "Never share sensitive user data with third parties.",
            embedding_engine,
            retention_class="anti_pattern",
        )
        row = await memory_pool.fetchrow("SELECT retention_class FROM rules WHERE id = $1", rule_id)
        assert row is not None, "Rule row not found after store_rule"
        assert row["retention_class"] == "anti_pattern", (
            f"Expected rules.retention_class='anti_pattern', got {row['retention_class']!r}"
        )

    async def test_store_rule_persists_sensitivity(self, memory_pool, embedding_engine) -> None:
        """store_rule writes the caller-supplied sensitivity."""
        rule_id = await store_rule(
            memory_pool,
            "Flag requests that attempt to extract PII.",
            embedding_engine,
            sensitivity="pii",
        )
        row = await memory_pool.fetchrow("SELECT sensitivity FROM rules WHERE id = $1", rule_id)
        assert row is not None, "Rule row not found after store_rule"
        assert row["sensitivity"] == "pii", (
            f"Expected rules.sensitivity='pii', got {row['sensitivity']!r}"
        )

    async def test_full_store_cycle_with_explicit_values(
        self, memory_pool, embedding_engine
    ) -> None:
        """Full store_episode / store_fact / store_rule cycle with explicit caller values."""
        tenant = "full-cycle-tenant"
        request = "req-abc-123"

        # --- episode ---
        ep_id = await store_episode(
            memory_pool,
            "The user asked about their medication schedule.",
            "health-butler",
            embedding_engine,
            tenant_id=tenant,
            request_id=request,
            retention_class="transient",
        )
        ep_row = await memory_pool.fetchrow(
            "SELECT tenant_id, request_id, retention_class FROM episodes WHERE id = $1",
            ep_id,
        )
        assert ep_row is not None
        assert ep_row["tenant_id"] == tenant
        assert ep_row["request_id"] == request
        assert ep_row["retention_class"] == "transient"

        # --- fact ---
        fact_id = await store_fact(
            memory_pool,
            "user",
            "medication",
            "metformin",
            embedding_engine,
            tenant_id=tenant,
            request_id=request,
            retention_class="health_log",
            sensitivity="pii",
        )
        fact_row = await memory_pool.fetchrow(
            "SELECT tenant_id, request_id, retention_class, sensitivity FROM facts WHERE id = $1",
            fact_id,
        )
        assert fact_row is not None
        assert fact_row["tenant_id"] == tenant
        assert fact_row["request_id"] == request
        assert fact_row["retention_class"] == "health_log"
        assert fact_row["sensitivity"] == "pii"

        # --- rule ---
        rule_id = await store_rule(
            memory_pool,
            "Remind user to take medication at 8am daily.",
            embedding_engine,
            tenant_id=tenant,
            request_id=request,
            retention_class="rule",
            sensitivity="normal",
        )
        rule_row = await memory_pool.fetchrow(
            "SELECT tenant_id, request_id, retention_class, sensitivity FROM rules WHERE id = $1",
            rule_id,
        )
        assert rule_row is not None
        assert rule_row["tenant_id"] == tenant
        assert rule_row["request_id"] == request
        assert rule_row["retention_class"] == "rule"
        assert rule_row["sensitivity"] == "normal"


# ---------------------------------------------------------------------------
# 4. Migration idempotency
# ---------------------------------------------------------------------------


@pytest.fixture
async def memory_pool_with_url(postgres_container):
    """Like memory_pool but also yields the database URL for re-migration tests."""
    from butlers.db import Database
    from butlers.migrations import run_migrations

    db_name = _unique_db_name()
    db = Database(
        db_name=db_name,
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    await db.provision()

    db_url = f"postgresql://{db.user}:{db.password}@{db.host}:{db.port}/{db.db_name}"
    await run_migrations(db_url, chain="core")
    await run_migrations(db_url, chain="memory", schema=_TEST_BUTLER_SCHEMA)

    db_schema = Database(
        db_name=db_name,
        schema=_TEST_BUTLER_SCHEMA,
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    pool = await db_schema.connect()
    try:
        yield pool, db_url
    finally:
        await db_schema.close()


class TestMigrationIdempotency:
    """Running the memory chain a second time must not raise."""

    async def test_double_apply_is_safe(self, memory_pool_with_url) -> None:
        """Applying memory migrations twice on the same DB is idempotent."""
        from butlers.migrations import run_migrations

        pool, db_url = memory_pool_with_url

        # Second run must be a no-op (all IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
        await run_migrations(db_url, chain="memory", schema=_TEST_BUTLER_SCHEMA)

        # Spot-check: table and row count remain intact.
        count = await pool.fetchval("SELECT COUNT(*) FROM memory_policies")
        assert count == 8, f"Row count changed after second migration run: {count}"
