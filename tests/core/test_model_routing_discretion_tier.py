"""Tests for Complexity.DISCRETION tier in butlers.core.model_routing.

Covers:
- Complexity.DISCRETION exists and equals "discretion"
- Complexity enum round-trip (string → enum)
- resolve_model works end-to-end for the discretion tier (integration)
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
def test_complexity_discretion_exists() -> None:
    """Complexity.DISCRETION must exist and equal the string 'discretion'."""
    assert Complexity.DISCRETION == "discretion"
    assert Complexity.DISCRETION.value == "discretion"


@pytest.mark.unit
def test_complexity_discretion_from_string() -> None:
    """Complexity('discretion') must resolve to Complexity.DISCRETION."""
    assert Complexity("discretion") is Complexity.DISCRETION


@pytest.mark.unit
def test_complexity_enum_has_five_tiers() -> None:
    """All five tiers (including discretion) must be present."""
    values = {m.value for m in Complexity}
    assert values == {"trivial", "medium", "high", "extra_high", "discretion"}


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
    """Create the shared schema and model routing tables with discretion tier."""
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
                       OR complexity_tier IN
                       ('trivial', 'medium', 'high', 'extra_high', 'discretion'))
        )
    """)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_discretion_tier(postgres_container: Any) -> None:
    """resolve_model finds a discretion-tier entry for Complexity.DISCRETION."""
    async with _make_pool(postgres_container) as pool:
        import json

        await pool.execute(
            """
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            """,
            "discretion-qwen3",
            "opencode",
            "ollama/qwen3.5:9b",
            json.dumps([]),
            "discretion",
            True,
            10,
        )

        result = await resolve_model(pool, "connector", Complexity.DISCRETION)

        assert result is not None
        runtime_type, model_id, extra_args = result
        assert runtime_type == "opencode"
        assert model_id == "ollama/qwen3.5:9b"
        assert extra_args == []


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_resolve_discretion_string_tier(postgres_container: Any) -> None:
    """resolve_model accepts the plain string 'discretion' for complexity_tier."""
    async with _make_pool(postgres_container) as pool:
        import json

        await pool.execute(
            """
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            """,
            "discretion-model",
            "opencode",
            "ollama/qwen3.5:9b",
            json.dumps([]),
            "discretion",
            True,
            10,
        )

        result = await resolve_model(pool, "connector", "discretion")

        assert result is not None
        assert result[1] == "ollama/qwen3.5:9b"


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_discretion_tier_does_not_match_medium(postgres_container: Any) -> None:
    """A discretion-tier entry must NOT be returned when resolving 'medium'."""
    async with _make_pool(postgres_container) as pool:
        import json

        await pool.execute(
            """
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            """,
            "discretion-only",
            "opencode",
            "ollama/qwen3.5:9b",
            json.dumps([]),
            "discretion",
            True,
            10,
        )

        result = await resolve_model(pool, "connector", Complexity.MEDIUM)
        assert result is None


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
async def test_override_remap_to_discretion_tier(postgres_container: Any) -> None:
    """A per-butler override can remap a medium entry to the discretion tier."""
    async with _make_pool(postgres_container) as pool:
        import json

        row = await pool.fetchrow(
            """
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            RETURNING id
            """,
            "small-local",
            "opencode",
            "ollama/phi4:3.8b",
            json.dumps([]),
            "medium",
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
            "connector",
            entry_id,
            True,
            None,
            "discretion",
        )

        # Should now be found under discretion tier for the connector butler
        result = await resolve_model(pool, "connector", Complexity.DISCRETION)
        assert result is not None
        assert result[1] == "ollama/phi4:3.8b"

        # Should NOT be found under medium tier for the connector butler (remapped away)
        medium_result = await resolve_model(pool, "connector", Complexity.MEDIUM)
        assert medium_result is None
