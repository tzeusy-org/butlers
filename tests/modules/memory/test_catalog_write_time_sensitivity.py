"""Tests for write-time sensitivity exclusion on public.memory_catalog writes.

Spec: openspec/specs/memory-discovery-catalog/spec.md — "Catalog write-behind
on memory store" / "Backfill drains pre-existing facts/rules into the
catalog".

Owner ruling (bu-6gsmh): EXCLUDE, not merely filter-at-read-time.
Confidential/pii facts and rules must never be written to
``public.memory_catalog`` at all — defense-in-depth on top of the existing
read-time authorization ceiling in ``search.py`` (see
``tests/modules/memory/test_catalog_sensitivity.py`` for that ceiling's own
coverage, which is unchanged by this bead).

Unit tests here pin the write-time exclusion vocabulary and the
``_upsert_catalog`` short-circuit (mocked pool, no DB). Integration coverage
against a real ``public.memory_catalog`` table — for the live
``store_fact``/``store_rule`` write-behind path AND the two backfill
functions — lives in
``tests/modules/memory/test_memory_migration_integration.py`` (search for
"bu-6gsmh").
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from butlers.modules.memory import search as catalog_search
from butlers.modules.memory import storage

pytestmark = pytest.mark.unit


class TestCatalogWriteExcludedSensitivitiesVocabulary:
    """The write-time exclusion set must mirror the read-time default ceiling exactly."""

    def test_excludes_exactly_pii_and_confidential(self) -> None:
        assert storage.CATALOG_WRITE_EXCLUDED_SENSITIVITIES == frozenset({"pii", "confidential"})

    def test_never_excludes_normal(self) -> None:
        assert "normal" not in storage.CATALOG_WRITE_EXCLUDED_SENSITIVITIES

    def test_parity_with_search_module_read_time_ceiling(self) -> None:
        """storage.py duplicates search.py's sensitivity vocabulary rather than
        importing it (see the comment in storage.py for why — a static import
        would make search.py's pgvector operators reachable from every
        transitive importer, including relationship's deterministic-Finder
        endpoint guardrail). Pin parity here so the two cannot silently drift.
        """
        assert storage._CATALOG_SENSITIVITY_LEVELS == catalog_search.CATALOG_SENSITIVITY_LEVELS
        assert storage._DEFAULT_CATALOG_SENSITIVITY == catalog_search.DEFAULT_CATALOG_SENSITIVITY

        # The write-time exclusion set must be exactly "every level the
        # read-time ceiling would hide from a default ('normal') caller" —
        # i.e. resolve_allowed_sensitivities('normal') is the complement.
        default_allowed = set(catalog_search.resolve_allowed_sensitivities("normal"))
        all_levels = set(catalog_search.CATALOG_SENSITIVITY_LEVELS)
        assert storage.CATALOG_WRITE_EXCLUDED_SENSITIVITIES == all_levels - default_allowed


class TestIsCatalogWriteExcluded:
    @pytest.mark.parametrize("sensitivity", ["pii", "confidential"])
    def test_excluded_levels(self, sensitivity: str) -> None:
        assert storage._is_catalog_write_excluded(sensitivity) is True

    @pytest.mark.parametrize("sensitivity", ["normal", None])
    def test_not_excluded(self, sensitivity: str | None) -> None:
        assert storage._is_catalog_write_excluded(sensitivity) is False

    def test_unknown_value_is_not_excluded(self) -> None:
        """Fail-open on write is intentional here: an unrecognized sensitivity
        string is not proven to be above the ceiling, and search.py's
        read-time filter (fail-closed to 'normal'-only) is the layer
        responsible for keeping unrecognized values out of default results."""
        assert storage._is_catalog_write_excluded("some_future_level") is False


class TestUpsertCatalogSkipsExcludedSensitivity:
    """_upsert_catalog must short-circuit before touching the DB for excluded rows."""

    async def _call(self, *, sensitivity: str | None) -> AsyncMock:
        pool = AsyncMock()
        await storage._upsert_catalog(
            pool,
            source_schema="health",
            source_table="facts",
            source_id=uuid.uuid4(),
            source_butler="health",
            tenant_id="shared",
            entity_id=None,
            summary="summary",
            embedding=[0.0] * 384,
            search_text="search text",
            memory_type="fact",
            sensitivity=sensitivity,
        )
        return pool

    @pytest.mark.parametrize("sensitivity", ["pii", "confidential"])
    async def test_excluded_sensitivity_never_executes_sql(self, sensitivity: str) -> None:
        pool = await self._call(sensitivity=sensitivity)
        pool.execute.assert_not_awaited()

    @pytest.mark.parametrize("sensitivity", ["normal", None])
    async def test_non_excluded_sensitivity_still_writes(self, sensitivity: str | None) -> None:
        pool = await self._call(sensitivity=sensitivity)
        pool.execute.assert_awaited_once()
        sql = pool.execute.await_args.args[0]
        assert "INSERT INTO public.memory_catalog" in sql
