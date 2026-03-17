"""Tests for Complexity.SELF_HEALING tier in butlers.core.model_routing.

Covers:
- Complexity.SELF_HEALING exists and equals "self_healing"
- Complexity enum round-trip (string → enum)
- All expected tiers present in the enum
- resolve_model works end-to-end for the self_healing tier (integration)
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest

from butlers.core.model_routing import Complexity, resolve_model

docker_available = shutil.which("docker") is not None

# ---------------------------------------------------------------------------
# Unit tests (no DB required)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_complexity_self_healing_exists() -> None:
    """Complexity.SELF_HEALING must exist and equal the string 'self_healing'."""
    assert Complexity.SELF_HEALING == "self_healing"
    assert Complexity.SELF_HEALING.value == "self_healing"


@pytest.mark.unit
def test_complexity_self_healing_from_string() -> None:
    """Complexity('self_healing') must resolve to Complexity.SELF_HEALING."""
    assert Complexity("self_healing") is Complexity.SELF_HEALING


@pytest.mark.unit
def test_complexity_enum_has_all_tiers() -> None:
    """All six tiers (including self_healing) must be present in the enum."""
    values = {m.value for m in Complexity}
    assert values == {"trivial", "medium", "high", "extra_high", "discretion", "self_healing"}


@pytest.mark.unit
def test_complexity_self_healing_not_equal_to_discretion() -> None:
    """SELF_HEALING and DISCRETION are distinct tiers."""
    assert Complexity.SELF_HEALING != Complexity.DISCRETION
    assert Complexity.SELF_HEALING.value != Complexity.DISCRETION.value


# ---------------------------------------------------------------------------
# Integration helpers
# ---------------------------------------------------------------------------


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@asynccontextmanager
async def _make_pool(postgres_container: Any) -> AsyncIterator[asyncpg.Pool]:
    """Create a fresh DB with the model routing tables and yield a pool."""
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
    """Create the shared schema and model routing tables with self_healing tier."""
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
                CHECK (complexity_tier IN (
                    'trivial', 'medium', 'high', 'extra_high', 'discretion', 'self_healing'
                ))
        )
    """)

    await pool.execute("""
        CREATE TABLE IF NOT EXISTS shared.butler_model_overrides (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name      TEXT NOT NULL,
            catalog_entry_id UUID NOT NULL
                REFERENCES shared.model_catalog(id) ON DELETE CASCADE,
            enabled          BOOLEAN NOT NULL DEFAULT true,
            priority         INTEGER,
            complexity_tier  TEXT,
            CONSTRAINT uq_butler_model_overrides_butler_entry
                UNIQUE (butler_name, catalog_entry_id),
            CONSTRAINT chk_butler_model_overrides_complexity_tier
                CHECK (complexity_tier IS NULL
                       OR complexity_tier IN (
                           'trivial', 'medium', 'high', 'extra_high',
                           'discretion', 'self_healing'
                       ))
        )
    """)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_self_healing_tier(postgres_container: Any) -> None:
    """resolve_model finds a self_healing-tier entry for Complexity.SELF_HEALING."""
    async with _make_pool(postgres_container) as pool:
        import json

        await pool.execute(
            """
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            """,
            "healing-sonnet",
            "claude",
            "claude-sonnet-4-6",
            json.dumps([]),
            "self_healing",
            True,
            10,
        )

        result = await resolve_model(pool, "email", Complexity.SELF_HEALING)

        assert result is not None
        runtime_type, model_id, extra_args, catalog_entry_id = result
        assert runtime_type == "claude"
        assert model_id == "claude-sonnet-4-6"
        assert extra_args == []
        assert catalog_entry_id is not None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_self_healing_string_tier(postgres_container: Any) -> None:
    """resolve_model accepts the plain string 'self_healing' for complexity_tier."""
    async with _make_pool(postgres_container) as pool:
        import json

        await pool.execute(
            """
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            """,
            "healing-model",
            "claude",
            "claude-sonnet-4-6",
            json.dumps([]),
            "self_healing",
            True,
            10,
        )

        result = await resolve_model(pool, "email", "self_healing")

        assert result is not None
        assert result[1] == "claude-sonnet-4-6"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_self_healing_tier_does_not_match_medium(postgres_container: Any) -> None:
    """A self_healing-tier entry must NOT be returned when resolving 'medium'."""
    async with _make_pool(postgres_container) as pool:
        import json

        await pool.execute(
            """
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            """,
            "healing-only",
            "claude",
            "claude-sonnet-4-6",
            json.dumps([]),
            "self_healing",
            True,
            10,
        )

        result = await resolve_model(pool, "email", Complexity.MEDIUM)
        assert result is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_no_self_healing_model_returns_none(postgres_container: Any) -> None:
    """resolve_model returns None when no self_healing-tier entry is configured."""
    async with _make_pool(postgres_container) as pool:
        result = await resolve_model(pool, "email", Complexity.SELF_HEALING)
        assert result is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_override_remap_to_self_healing_tier(postgres_container: Any) -> None:
    """A per-butler override can remap an entry to the self_healing tier."""
    async with _make_pool(postgres_container) as pool:
        import json

        row = await pool.fetchrow(
            """
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            RETURNING id
            """,
            "finance-opus",
            "claude",
            "claude-opus-4-6",
            json.dumps([]),
            "high",
            True,
            5,
        )
        entry_id = row["id"]

        await pool.execute(
            """
            INSERT INTO shared.butler_model_overrides
                (butler_name, catalog_entry_id, enabled, priority, complexity_tier)
            VALUES ($1, $2, $3, $4, $5)
            """,
            "finance",
            entry_id,
            True,
            None,
            "self_healing",
        )

        # Should now be found under self_healing tier for the finance butler
        result = await resolve_model(pool, "finance", Complexity.SELF_HEALING)
        assert result is not None
        assert result[1] == "claude-opus-4-6"

        # Other butlers still see nothing for self_healing (no entry in catalog)
        result_other = await resolve_model(pool, "general", Complexity.SELF_HEALING)
        assert result_other is None
