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
from unittest.mock import AsyncMock, MagicMock

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


class TestServerDerivedCatalogReadPolicy:
    """Held authority maps to the existing sensitivity vocabulary."""

    def test_internal_authority_allows_normal_and_pii(self) -> None:
        policy = catalog_search.resolve_catalog_read_policy("internal")

        assert policy.authority == "internal"
        assert policy.allowed_sensitivities == ("normal", "pii")

    def test_unknown_authority_fails_closed(self) -> None:
        policy = catalog_search.resolve_catalog_read_policy("caller-invented")

        assert policy.authority == "normal"
        assert policy.allowed_sensitivities == ("normal",)


class TestSearchCatalogEmbeddingHandoff:
    async def test_default_semantic_search_computes_one_embedding(self) -> None:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        engine = MagicMock()
        vector = [0.125, -0.25]
        engine.embed.return_value = vector

        results = await catalog_search.search_catalog(
            pool,
            "prompt",
            engine,
            mode="semantic",
            read_policy=catalog_search.resolve_catalog_read_policy("normal"),
        )

        assert results == []
        engine.embed.assert_called_once_with("prompt")
        pool.fetch.assert_awaited_once()
        sql, embedding, tenant_id, sensitivities, limit = pool.fetch.await_args.args
        assert "embedding <=> $1" in sql
        assert "chronicler_catalog_row_visible(memory_catalog.id)" in sql
        assert embedding == str(vector)
        assert tenant_id == "shared"
        assert sensitivities == ["normal"]
        assert limit == 10


class TestCatalogVisibilityRunsLazily:
    """The per-row visibility function must not run over every candidate (bu-dhloi1.190).

    It costs ~0.5 ms a row; evaluated in the scan's WHERE it made each context fetch
    take ~4 s on dev. It may only run on rows already ranked, until the limit is met.
    """

    @staticmethod
    async def _sql(mode: str) -> str:
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        engine = MagicMock()
        engine.embed.return_value = [0.5]

        await catalog_search.search_catalog(
            pool,
            "prompt",
            engine,
            mode=mode,
            read_policy=catalog_search.resolve_catalog_read_policy("normal"),
        )
        return pool.fetch.await_args.args[0]

    @pytest.mark.parametrize("mode", ["semantic", "keyword"])
    async def test_visibility_function_is_applied_after_ranking_not_in_the_scan(
        self, mode: str
    ) -> None:
        sql = await self._sql(mode)
        ranked, visible = sql.split("visible AS MATERIALIZED")

        assert "ranked AS MATERIALIZED" in ranked
        assert "ORDER BY" in ranked
        assert "chronicler_catalog_row_visible" not in ranked
        assert "public.chronicler_catalog_row_visible(memory_catalog.id)" in visible
        assert visible.index("chronicler_catalog_row_visible") < visible.index("LIMIT $")
        assert sql.count("chronicler_catalog_row_visible") == 1

    @pytest.mark.parametrize("mode", ["semantic", "keyword"])
    async def test_cheap_filters_stay_in_the_ranking_scan(self, mode: str) -> None:
        sql = await self._sql(mode)
        ranked = sql.split("visible AS MATERIALIZED")[0]

        for predicate in ("tenant_id = $2", "invalid_at IS NULL", "sensitivity"):
            assert predicate in ranked

    async def test_results_keep_the_ranked_order_and_score_columns(self) -> None:
        semantic = await self._sql("semantic")
        keyword = await self._sql("keyword")

        assert "ORDER BY visible.distance ASC" in semantic
        assert "1 - visible.distance AS similarity" in semantic
        assert "ORDER BY visible.rank DESC" in keyword
        assert "visible.rank AS rank" in keyword


# ---------------------------------------------------------------------------
# Integration tests — require Docker + Postgres
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCatalogSensitivityFilteringIntegration:
    """Sensitivity filtering against the core-migrated public.memory_catalog (core_009)."""

    async def _insert(self, pool, *, text: str, sensitivity: str | None) -> uuid.UUID:
        source_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO public.memory_catalog (
                source_schema, source_table, source_id, memory_type,
                summary, search_vector, sensitivity, tenant_id
            )
            VALUES ($1, $2, $3, $4, $5, to_tsvector('english', $5), $6, 'shared')
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
    async def test_low_auth_caller_excludes_sensitive_results(self, db_pool) -> None:
        """A default ('normal') caller never receives pii/confidential rows."""
        pool = db_pool
        await self._insert(pool, text="alpha bravo charlie", sensitivity="normal")
        await self._insert(pool, text="alpha bravo charlie", sensitivity="pii")
        await self._insert(pool, text="alpha bravo charlie", sensitivity="confidential")

        engine = MagicMock()
        results = await catalog_search.search_catalog(
            pool,
            "alpha bravo charlie",
            engine,
            mode="keyword",
            read_policy=catalog_search.resolve_catalog_read_policy("normal"),
        )

        sensitivities = {r["sensitivity"] for r in results}
        assert sensitivities == {"normal"}, f"leaked sensitive rows: {sensitivities}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_null_sensitivity_treated_as_normal(self, db_pool) -> None:
        """Rows with NULL sensitivity are visible to a default caller."""
        pool = db_pool
        await self._insert(pool, text="delta echo foxtrot", sensitivity=None)

        engine = MagicMock()
        results = await catalog_search.search_catalog(
            pool,
            "delta echo foxtrot",
            engine,
            mode="keyword",
            read_policy=catalog_search.resolve_catalog_read_policy("normal"),
        )
        assert len(results) == 1
        assert results[0]["sensitivity"] is None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_authorized_caller_receives_higher_sensitivity(self, db_pool) -> None:
        """A caller authorized to 'confidential' receives all lower levels too."""
        pool = db_pool
        await self._insert(pool, text="golf hotel india", sensitivity="normal")
        await self._insert(pool, text="golf hotel india", sensitivity="pii")
        await self._insert(pool, text="golf hotel india", sensitivity="confidential")

        engine = MagicMock()
        results = await catalog_search.search_catalog(
            pool,
            "golf hotel india",
            engine,
            mode="keyword",
            read_policy=catalog_search.resolve_catalog_read_policy("confidential"),
        )

        sensitivities = {r["sensitivity"] for r in results}
        assert sensitivities == {"normal", "pii", "confidential"}, sensitivities

    @pytest.mark.asyncio(loop_scope="session")
    async def test_pii_caller_excludes_confidential(self, db_pool) -> None:
        """A caller authorized to 'pii' still does not see 'confidential' rows."""
        pool = db_pool
        await self._insert(pool, text="juliet kilo lima", sensitivity="pii")
        await self._insert(pool, text="juliet kilo lima", sensitivity="confidential")

        engine = MagicMock()
        results = await catalog_search.search_catalog(
            pool,
            "juliet kilo lima",
            engine,
            mode="keyword",
            read_policy=catalog_search.resolve_catalog_read_policy("internal"),
        )

        sensitivities = {r["sensitivity"] for r in results}
        assert sensitivities == {"pii"}, sensitivities

    @pytest.mark.asyncio(loop_scope="session")
    async def test_catalog_search_rejects_caller_authority_argument(self, db_pool) -> None:
        """The shared primitive has no caller-controlled authority fallback."""
        pool = db_pool
        await self._insert(pool, text="mike november oscar", sensitivity="normal")
        await self._insert(pool, text="mike november oscar", sensitivity="pii")
        await self._insert(pool, text="mike november oscar", sensitivity="confidential")

        with pytest.raises(TypeError, match="max_sensitivity"):
            await catalog_search.search_catalog(
                pool,
                "mike november oscar",
                MagicMock(),
                mode="keyword",
                max_sensitivity="confidential",
                read_policy=catalog_search.resolve_catalog_read_policy("internal"),
            )
