"""Tests for cross-butler catalog search sensitivity / authorization filtering.

Spec: openspec/specs/memory-discovery-catalog/spec.md — "Sensitivity filtering".

A cross-butler catalog search MUST exclude results above the caller's
authorization and MUST default to only ``sensitivity = 'normal'`` unless the
caller explicitly requests higher sensitivity levels.

Unit tests pin the authorization-resolution helper (fail-closed semantics).
Integration tests (require Docker + Postgres) prove the SQL filter against a
real ``public.memory_catalog`` table: a low-authorization caller does not see
higher-sensitivity rows, while an authorized caller does.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from butlers.modules.memory import search as catalog_search

# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit


class TestResolveAllowedSensitivities:
    """The authorization ceiling resolves to an inclusive, fail-closed set."""

    def test_default_normal_only(self) -> None:
        assert catalog_search.resolve_allowed_sensitivities("normal") == ["normal"]

    def test_unknown_level_fails_closed_to_normal(self) -> None:
        assert catalog_search.resolve_allowed_sensitivities("topsecret") == ["normal"]

    def test_pii_includes_normal(self) -> None:
        assert catalog_search.resolve_allowed_sensitivities("pii") == ["normal", "pii"]

    def test_confidential_includes_all_lower(self) -> None:
        assert catalog_search.resolve_allowed_sensitivities("confidential") == [
            "normal",
            "pii",
            "confidential",
        ]


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCatalogSensitivityFilteringIntegration:
    """Sensitivity filtering against a real public.memory_catalog table."""

    @pytest.fixture
    async def catalog_pool(self, provisioned_postgres_pool):
        """Provision a fresh DB with a minimal public.memory_catalog table."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute(
                """
                CREATE TABLE IF NOT EXISTS public.memory_catalog (
                    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id     TEXT NOT NULL DEFAULT 'shared',
                    source_schema TEXT NOT NULL,
                    source_table  TEXT NOT NULL,
                    source_id     UUID NOT NULL,
                    source_butler TEXT NOT NULL DEFAULT 'memory',
                    memory_type   TEXT NOT NULL,
                    title         TEXT,
                    summary       TEXT,
                    search_text   TEXT,
                    search_vector tsvector,
                    sensitivity   TEXT,
                    -- Mirrors the real public.memory_catalog schema (core_009):
                    -- the catalog search excludes stale rows via invalid_at IS NULL.
                    invalid_at    TIMESTAMPTZ
                )
                """
            )
            yield pool

    async def _insert(self, pool, *, text: str, sensitivity: str | None) -> uuid.UUID:
        source_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO public.memory_catalog (
                source_schema, source_table, source_id, memory_type,
                summary, search_text, search_vector, sensitivity, tenant_id
            )
            VALUES ($1, $2, $3, $4, $5, $5, to_tsvector('english', $5), $6, 'shared')
            """,
            "memory",
            "facts",
            source_id,
            "fact",
            text,
            sensitivity,
        )
        return source_id

    @pytest.mark.asyncio(loop_scope="session")
    async def test_low_auth_caller_excludes_sensitive_results(self, catalog_pool) -> None:
        """A default ('normal') caller never receives pii/confidential rows."""
        pool = catalog_pool
        await self._insert(pool, text="alpha bravo charlie", sensitivity="normal")
        await self._insert(pool, text="alpha bravo charlie", sensitivity="pii")
        await self._insert(pool, text="alpha bravo charlie", sensitivity="confidential")

        engine = MagicMock()
        results = await catalog_search.search_catalog(
            pool,
            "alpha bravo charlie",
            engine,
            mode="keyword",
        )

        sensitivities = {r["sensitivity"] for r in results}
        assert sensitivities == {"normal"}, f"leaked sensitive rows: {sensitivities}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_null_sensitivity_treated_as_normal(self, catalog_pool) -> None:
        """Rows with NULL sensitivity are visible to a default caller."""
        pool = catalog_pool
        await self._insert(pool, text="delta echo foxtrot", sensitivity=None)

        engine = MagicMock()
        results = await catalog_search.search_catalog(
            pool,
            "delta echo foxtrot",
            engine,
            mode="keyword",
        )
        assert len(results) == 1
        assert results[0]["sensitivity"] is None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_authorized_caller_receives_higher_sensitivity(self, catalog_pool) -> None:
        """A caller authorized to 'confidential' receives all lower levels too."""
        pool = catalog_pool
        await self._insert(pool, text="golf hotel india", sensitivity="normal")
        await self._insert(pool, text="golf hotel india", sensitivity="pii")
        await self._insert(pool, text="golf hotel india", sensitivity="confidential")

        engine = MagicMock()
        results = await catalog_search.search_catalog(
            pool,
            "golf hotel india",
            engine,
            mode="keyword",
            max_sensitivity="confidential",
        )

        sensitivities = {r["sensitivity"] for r in results}
        assert sensitivities == {"normal", "pii", "confidential"}, sensitivities

    @pytest.mark.asyncio(loop_scope="session")
    async def test_pii_caller_excludes_confidential(self, catalog_pool) -> None:
        """A caller authorized to 'pii' still does not see 'confidential' rows."""
        pool = catalog_pool
        await self._insert(pool, text="juliet kilo lima", sensitivity="pii")
        await self._insert(pool, text="juliet kilo lima", sensitivity="confidential")

        engine = MagicMock()
        results = await catalog_search.search_catalog(
            pool,
            "juliet kilo lima",
            engine,
            mode="keyword",
            max_sensitivity="pii",
        )

        sensitivities = {r["sensitivity"] for r in results}
        assert sensitivities == {"pii"}, sensitivities
