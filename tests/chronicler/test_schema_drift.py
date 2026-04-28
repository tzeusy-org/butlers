"""CI guardrail: _apply_chronicler_schema DDL must stay in sync with Alembic migrations.

This test catches drift between the inline DDL helper used by the
``roster/chronicler/tests/test_storage_integration.py`` fixture and the
canonical Alembic migration chain for the chronicler butler.

Background
----------
Two near-misses in one PR cycle showed that the manual-duplication pattern is
fragile:
- ``watermark_id`` was missing from the inline DDL after migration 005 landed.
- ``carryover`` was missing after migration 006 landed.

A short-term docstring warning was added in PR #1222.  This test is the
durable long-term fix: CI catches drift automatically instead of relying on
reviewer attention.

Mechanism
---------
1. Apply the inline DDL (``_apply_chronicler_schema``) to a fresh DB (DB-A).
2. Apply the full Alembic chronicler migration chain to a second fresh DB (DB-B).
3. Query ``information_schema.columns`` for every BASE TABLE in the ``public``
   schema of each DB.
4. For every table that is common to both DBs (the overlap), compare column
   sets.
5. Fail with a clear diff message if any column is present in one DB but not
   the other.

Note: the inline DDL deliberately omits ``tier2_cache`` (migration 004) and
other tables it does not need for storage integration testing.  That is
intentional — this test only checks the *shared* tables for column drift.
Adding a table to the migration chain without also adding it to
``_apply_chronicler_schema`` is NOT considered drift by this test.

Requires Docker.  Marked ``integration`` and skip-guarded in ``roster/conftest.py``.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from collections import defaultdict
from typing import Any

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.testing.migration import create_migration_db

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHRONICLER_MIGRATION_CHAIN = "chronicler"

# Tables the inline DDL creates.  Any table in this set that is also present
# in the migration DB will be column-compared.
_INLINE_DDL_TABLES = frozenset(
    {
        "source_adapter_state",
        "projection_checkpoints",
        "point_events",
        "episodes",
        "episode_event_links",
        "overrides",
        "idempotency_keys",
    }
)

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


# ---------------------------------------------------------------------------
# Inline DDL — copied verbatim from
# roster/chronicler/tests/test_storage_integration.py _apply_chronicler_schema
# ---------------------------------------------------------------------------


async def _apply_inline_ddl(conn: asyncpg.Connection) -> None:
    """Apply the chronicler inline DDL (mirrors _apply_chronicler_schema)."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS source_adapter_state (
            source_name TEXT PRIMARY KEY,
            chronicler_compatibility TEXT NOT NULL
                CHECK (chronicler_compatibility IN (
                    'supported', 'deferred', 'not_time_bearing', 'planned'
                )),
            read_surface TEXT,
            boundary_semantics TEXT,
            optional_schema BOOLEAN NOT NULL DEFAULT false,
            active BOOLEAN NOT NULL DEFAULT false,
            inactive_reason TEXT,
            schema_version INTEGER NOT NULL DEFAULT 1,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS projection_checkpoints (
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name)
                ON DELETE CASCADE,
            subsource TEXT NOT NULL DEFAULT '',
            watermark TIMESTAMPTZ,
            watermark_id BIGINT,
            last_run_at TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            last_error TEXT,
            rows_projected BIGINT NOT NULL DEFAULT 0,
            run_count BIGINT NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            carryover JSONB,
            PRIMARY KEY (source_name, subsource)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS point_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name),
            source_ref TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            precision TEXT NOT NULL DEFAULT 'exact'
                CHECK (precision IN ('exact', 'minute', 'hour', 'day', 'unknown')),
            title TEXT,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            privacy TEXT NOT NULL DEFAULT 'normal'
                CHECK (privacy IN ('normal', 'sensitive', 'restricted')),
            retention_days INTEGER,
            tombstone_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_name, source_ref)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name),
            source_ref TEXT NOT NULL,
            episode_type TEXT NOT NULL,
            start_at TIMESTAMPTZ NOT NULL,
            end_at TIMESTAMPTZ,
            precision TEXT NOT NULL DEFAULT 'exact'
                CHECK (precision IN ('exact', 'minute', 'hour', 'day', 'unknown')),
            title TEXT,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            privacy TEXT NOT NULL DEFAULT 'normal'
                CHECK (privacy IN ('normal', 'sensitive', 'restricted')),
            retention_days INTEGER,
            tombstone_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (source_name, source_ref),
            CHECK (end_at IS NULL OR end_at >= start_at)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS episode_event_links (
            episode_id UUID NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
            event_id UUID NOT NULL REFERENCES point_events(id) ON DELETE CASCADE,
            relation TEXT NOT NULL DEFAULT 'supports'
                CHECK (relation IN ('supports', 'boundary_start', 'boundary_end', 'evidence')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (episode_id, event_id, relation)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS overrides (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            target_kind TEXT NOT NULL CHECK (target_kind IN ('episode', 'point_event')),
            target_id UUID NOT NULL,
            corrected_start_at TIMESTAMPTZ,
            corrected_end_at TIMESTAMPTZ,
            corrected_title TEXT,
            corrected_privacy TEXT
                CHECK (corrected_privacy IS NULL OR
                       corrected_privacy IN ('normal', 'sensitive', 'restricted')),
            corrected_tombstone_at TIMESTAMPTZ,
            note TEXT,
            submitted_by TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (
                corrected_start_at IS NOT NULL OR
                corrected_end_at IS NOT NULL OR
                corrected_title IS NOT NULL OR
                corrected_privacy IS NOT NULL OR
                corrected_tombstone_at IS NOT NULL OR
                note IS NOT NULL
            )
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            source_name TEXT NOT NULL REFERENCES source_adapter_state(source_name)
                ON DELETE CASCADE,
            key TEXT NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            hit_count INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (source_name, key)
        )
    """)


# ---------------------------------------------------------------------------
# Schema introspection helpers
# ---------------------------------------------------------------------------


def _get_table_columns(db_url: str) -> dict[str, frozenset[str]]:
    """Return {table_name: frozenset(column_names)} for all BASE TABLEs in public schema."""
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT table_name, column_name "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "  AND table_name IN ("
                    "    SELECT table_name FROM information_schema.tables"
                    "    WHERE table_schema = 'public'"
                    "      AND table_type = 'BASE TABLE'"
                    "  ) "
                    "ORDER BY table_name, column_name"
                )
            ).fetchall()
    finally:
        engine.dispose()

    table_cols: dict[str, list[str]] = defaultdict(list)
    for table_name, column_name in rows:
        table_cols[table_name].append(column_name)
    return {t: frozenset(cols) for t, cols in table_cols.items()}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_inline_ddl_matches_alembic_migration_chain(postgres_container: Any) -> None:
    """Column sets for shared tables must match between inline DDL and migration chain.

    Creates two isolated databases:
    - DB-A: inline DDL from ``_apply_chronicler_schema``
    - DB-B: full Alembic chronicler migration chain

    Compares column sets for every table present in both DBs (the intersection).
    Tables only in DB-B (e.g. ``tier2_cache``) are intentionally excluded
    because the inline DDL deliberately omits tables it does not use.

    FAILS if any column appears in one DB but not the other, with a diff that
    identifies the exact table and missing column so the fix is unambiguous.
    """
    from alembic import command as alembic_command
    from butlers.migrations import _build_alembic_config

    # ── Provision DB-A: inline DDL ────────────────────────────────────────
    db_a_name = f"test_drift_inline_{uuid.uuid4().hex[:10]}"
    db_a_url = create_migration_db(postgres_container, db_a_name)

    parsed = _parse_db_url(db_a_url)
    asyncio.run(
        _run_inline_ddl(
            host=parsed["host"],
            port=parsed["port"],
            user=parsed["user"],
            password=parsed["password"],
            database=parsed["database"],
        )
    )

    # ── Provision DB-B: Alembic migration chain ───────────────────────────
    db_b_name = f"test_drift_migrated_{uuid.uuid4().hex[:10]}"
    db_b_url = create_migration_db(postgres_container, db_b_name)

    config = _build_alembic_config(db_b_url, chains=[_CHRONICLER_MIGRATION_CHAIN])
    alembic_command.upgrade(config, f"{_CHRONICLER_MIGRATION_CHAIN}@head")

    # ── Introspect column sets ────────────────────────────────────────────
    inline_cols = _get_table_columns(db_a_url)
    migrated_cols = _get_table_columns(db_b_url)

    # Compare only tables that the inline DDL is responsible for.
    shared_tables = sorted(_INLINE_DDL_TABLES & migrated_cols.keys() & inline_cols.keys())

    drift_lines: list[str] = []
    for table in shared_tables:
        in_inline = inline_cols.get(table, frozenset())
        in_migrated = migrated_cols.get(table, frozenset())

        only_in_inline = sorted(in_inline - in_migrated)
        only_in_migrated = sorted(in_migrated - in_inline)

        if only_in_inline:
            drift_lines.append(
                f"  {table}: column(s) in inline DDL but NOT in migration chain: "
                + ", ".join(only_in_inline)
            )
        if only_in_migrated:
            drift_lines.append(
                f"  {table}: column(s) in migration chain but NOT in inline DDL: "
                + ", ".join(only_in_migrated)
            )

    # Tables declared by inline DDL but not created by migrations (unexpected gap).
    missing_from_migrations = sorted(_INLINE_DDL_TABLES - migrated_cols.keys())
    if missing_from_migrations:
        drift_lines.append(
            "  Tables in _INLINE_DDL_TABLES but absent from migration chain: "
            + ", ".join(missing_from_migrations)
        )

    if drift_lines:
        raise AssertionError(
            "Inline DDL in _apply_chronicler_schema is out of sync with the "
            "Alembic migration chain.\n"
            "Update roster/chronicler/tests/test_storage_integration.py "
            "_apply_chronicler_schema to match the migration chain.\n"
            "Drift detected:\n" + "\n".join(drift_lines)
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_db_url(db_url: str) -> dict[str, Any]:
    """Extract connection parameters from a postgresql://... URL."""
    from urllib.parse import urlparse

    parsed = urlparse(db_url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "postgres",
        "database": parsed.path.lstrip("/"),
    }


async def _run_inline_ddl(*, host: str, port: int, user: str, password: str, database: str) -> None:
    """Connect to the target DB and apply the inline chronicler DDL."""
    conn = await asyncpg.connect(
        host=host, port=port, user=user, password=password, database=database
    )
    try:
        await _apply_inline_ddl(conn)
    finally:
        await conn.close()
