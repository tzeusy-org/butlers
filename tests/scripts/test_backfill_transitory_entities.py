"""Tests for scripts/backfill_transitory_entities.py.

Covers:
1. infer_entity_type — scope-based and token-based heuristics
2. run_diagnostic — queries, generic-label exclusion
3. resolve_or_create_entity — create path and duplicate-resolve path
4. backfill_schema — dry-run and apply modes, fact update counts
5. discover_memory_schemas — finds schemas with facts table
6. Generic label exclusion (Owner, user, me, etc.)

Issue: bu-cbs.4
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Load the script under test (it lives in scripts/, not a package)
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "backfill_transitory_entities.py"
)

_MODULE_NAME = "backfill_transitory_entities"


def _load_script():
    """Load the standalone script into sys.modules so @dataclass resolves correctly."""
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Must register BEFORE exec_module so @dataclass field() can find the module dict.
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_script()

infer_entity_type = _mod.infer_entity_type
run_diagnostic = _mod.run_diagnostic
resolve_or_create_entity = _mod.resolve_or_create_entity
backfill_schema = _mod.backfill_schema
discover_memory_schemas = _mod.discover_memory_schemas
_validate_schema_name = _mod._validate_schema_name
DiagnosticRow = _mod.DiagnosticRow
BackfillResult = _mod.BackfillResult
GENERIC_LABELS = _mod.GENERIC_LABELS


# ---------------------------------------------------------------------------
# infer_entity_type tests
# ---------------------------------------------------------------------------


class TestInferEntityType:
    """Unit tests for the entity_type heuristic function."""

    def test_finance_scope_defaults_to_organization(self):
        assert infer_entity_type("Nutrition Kitchen SG", "finance") == "organization"

    def test_travel_scope_defaults_to_place(self):
        assert infer_entity_type("Marina Bay Sands", "travel") == "place"

    def test_relationship_scope_defaults_to_person(self):
        assert infer_entity_type("Sarah", "relationship") == "person"

    def test_health_scope_defaults_to_organization(self):
        assert infer_entity_type("City Medical Centre", "health") == "organization"

    def test_education_scope_defaults_to_organization(self):
        assert infer_entity_type("NUS Business School", "education") == "organization"

    def test_person_prefix_overrides_finance_scope(self):
        """Dr./Mr. prefix → person even in finance scope."""
        assert infer_entity_type("Dr. Smith", "finance") == "person"

    def test_org_suffix_in_unknown_scope(self):
        """Ltd/Inc/Corp suffix → organization when no scope mapping."""
        assert infer_entity_type("Acme Corp", "global") == "organization"

    def test_pte_ltd_suffix(self):
        assert infer_entity_type("ABC Solutions Pte Ltd", "global") == "organization"

    def test_place_tokens_in_unknown_scope(self):
        assert infer_entity_type("Sentosa Beach Resort", "global") == "place"

    def test_unknown_scope_no_tokens_falls_back_to_other(self):
        assert infer_entity_type("FooBarBaz", "global") == "other"

    def test_home_scope_defaults_to_other(self):
        assert infer_entity_type("SmartLock 3000", "home") == "other"


# ---------------------------------------------------------------------------
# GENERIC_LABELS tests
# ---------------------------------------------------------------------------


class TestGenericLabels:
    """Verify the set of labels excluded from backfill."""

    def test_owner_is_generic(self):
        assert "Owner" in GENERIC_LABELS

    def test_user_variants_are_generic(self):
        assert "user" in GENERIC_LABELS
        assert "User" in GENERIC_LABELS

    def test_me_variants_are_generic(self):
        assert "me" in GENERIC_LABELS
        assert "Me" in GENERIC_LABELS

    def test_self_variants_are_generic(self):
        assert "self" in GENERIC_LABELS
        assert "Self" in GENERIC_LABELS

    def test_I_is_generic(self):
        assert "I" in GENERIC_LABELS


# ---------------------------------------------------------------------------
# run_diagnostic tests
# ---------------------------------------------------------------------------


class TestRunDiagnostic:
    """Tests for run_diagnostic() querying the facts table."""

    async def test_returns_diagnostic_rows(self):
        """run_diagnostic returns DiagnosticRow entries for non-generic subjects."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {"subject": "Nutrition Kitchen SG", "scope": "finance", "fact_count": 3},
                {"subject": "Marina Bay Hotel", "scope": "travel", "fact_count": 1},
            ]
        )

        rows = await run_diagnostic(mock_conn, "finance")

        assert len(rows) == 2
        assert rows[0].subject == "Nutrition Kitchen SG"
        assert rows[0].scope == "finance"
        assert rows[0].fact_count == 3
        assert rows[0].inferred_type == "organization"
        assert rows[1].subject == "Marina Bay Hotel"
        assert rows[1].scope == "travel"
        assert rows[1].inferred_type == "place"

    async def test_excludes_generic_labels(self):
        """run_diagnostic filters out generic labels like 'Owner', 'user', 'me'."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {"subject": "Owner", "scope": "global", "fact_count": 10},
                {"subject": "user", "scope": "global", "fact_count": 5},
                {"subject": "Me", "scope": "health", "fact_count": 2},
                {"subject": "Real Entity", "scope": "finance", "fact_count": 1},
            ]
        )

        rows = await run_diagnostic(mock_conn, "finance")

        assert len(rows) == 1
        assert rows[0].subject == "Real Entity"

    async def test_empty_table_returns_empty_list(self):
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        rows = await run_diagnostic(mock_conn, "finance")

        assert rows == []

    async def test_query_filters_entity_id_null_and_active(self):
        """The diagnostic query targets entity_id IS NULL and validity = active."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        await run_diagnostic(mock_conn, "finance")

        call_sql = mock_conn.fetch.call_args[0][0]
        assert "entity_id IS NULL" in call_sql
        assert "validity = 'active'" in call_sql


# ---------------------------------------------------------------------------
# resolve_or_create_entity tests
# ---------------------------------------------------------------------------


class TestResolveOrCreateEntity:
    """Tests for the INSERT-then-resolve logic."""

    async def test_creates_entity_on_success(self):
        """Returns (entity_id, True) when INSERT succeeds."""
        expected_uuid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=expected_uuid)

        entity_id_str, was_created = await resolve_or_create_entity(
            mock_conn, "Nutrition Kitchen SG", "finance", "organization", "finance"
        )

        assert entity_id_str == str(expected_uuid)
        assert was_created is True
        # Verify INSERT statement was called
        call_sql = mock_conn.fetchval.call_args[0][0]
        assert "INSERT INTO shared.entities" in call_sql

    async def test_resolves_existing_entity_on_unique_violation(self):
        """Returns (entity_id, False) when INSERT raises UniqueViolationError."""
        import asyncpg

        expected_uuid = uuid.UUID("bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee")
        mock_conn = AsyncMock()

        # First call (INSERT) raises UniqueViolationError; second call (SELECT) returns UUID
        mock_conn.fetchval = AsyncMock(
            side_effect=[asyncpg.UniqueViolationError("duplicate"), expected_uuid]
        )

        entity_id_str, was_created = await resolve_or_create_entity(
            mock_conn, "Acme Corp", "finance", "organization", "finance"
        )

        assert entity_id_str == str(expected_uuid)
        assert was_created is False
        # Should have been called twice: INSERT then SELECT
        assert mock_conn.fetchval.call_count == 2

    async def test_metadata_contains_unidentified_flag(self):
        """Created entity metadata must include unidentified=True and provenance."""
        expected_uuid = uuid.UUID("cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee")
        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=expected_uuid)

        await resolve_or_create_entity(
            mock_conn, "Nutrition Kitchen SG", "finance", "organization", "finance"
        )

        call_args = mock_conn.fetchval.call_args[0]
        # Metadata is the 5th positional arg (index 5 after SQL, tenant_id, name, type, aliases)
        # Position: sql, tenant_id, canonical_name, entity_type, aliases, metadata, roles
        metadata_json = call_args[5]
        metadata = json.loads(metadata_json)

        assert metadata["unidentified"] is True
        assert metadata["source"] == "backfill"
        assert metadata["source_butler"] == "finance"
        assert metadata["source_scope"] == "finance"

    async def test_tombstoned_entity_raises_runtime_error(self):
        """If entity exists but is tombstoned and no fallback found, raises RuntimeError."""
        import asyncpg

        mock_conn = AsyncMock()
        # INSERT fails, first SELECT returns None (tombstoned), second SELECT also returns None
        mock_conn.fetchval = AsyncMock(
            side_effect=[asyncpg.UniqueViolationError("dup"), None, None]
        )

        with pytest.raises(RuntimeError, match="could not be resolved"):
            await resolve_or_create_entity(
                mock_conn, "Dead Entity", "finance", "organization", "finance"
            )


# ---------------------------------------------------------------------------
# backfill_schema tests
# ---------------------------------------------------------------------------


_SAMPLE_UUID = str(uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))


class _FakeTransaction:
    """Minimal async context manager that acts like a DB transaction."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeConn:
    """Minimal asyncpg connection mock for backfill_schema integration tests."""

    def __init__(self, *, fact_rows=None, entity_uuid=None, update_result="UPDATE 3"):
        self._fact_rows = fact_rows or []
        self._entity_uuid = entity_uuid or uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        self._update_result = update_result
        self.executed_sql: list[str] = []

    async def execute(self, sql, *args):
        self.executed_sql.append(sql)
        return self._update_result

    async def fetch(self, sql, *args):
        return self._fact_rows

    async def fetchval(self, sql, *args):
        return self._entity_uuid

    def transaction(self):
        return _FakeTransaction()


class _FakePool:
    """Minimal asyncpg pool mock returning a _FakeConn."""

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        class _CM:
            def __init__(self, conn):
                self._conn = conn

            async def __aenter__(self):
                return self._conn

            async def __aexit__(self, *args):
                return False

        return _CM(self._conn)


class TestBackfillSchemaDryRun:
    """Dry-run mode: no writes, returns diagnostic only."""

    async def test_dry_run_returns_diagnostic_without_writing(self):
        fact_rows = [
            {"subject": "Nutrition Kitchen SG", "scope": "finance", "fact_count": 3},
        ]
        conn = _FakeConn(fact_rows=fact_rows)
        pool = _FakePool(conn)

        result = await backfill_schema(pool, "finance", apply=False)

        assert len(result.diagnostic) == 1
        assert result.diagnostic[0].subject == "Nutrition Kitchen SG"
        # No entities created, no facts updated in dry-run
        assert result.entities_created == 0
        assert result.facts_updated == 0

    async def test_dry_run_excludes_generic_labels(self):
        fact_rows = [
            {"subject": "Owner", "scope": "global", "fact_count": 10},
            {"subject": "Real Entity", "scope": "finance", "fact_count": 2},
        ]
        conn = _FakeConn(fact_rows=fact_rows)
        pool = _FakePool(conn)

        result = await backfill_schema(pool, "finance", apply=False)

        # Only non-generic rows in diagnostic
        assert len(result.diagnostic) == 1
        assert result.diagnostic[0].subject == "Real Entity"

    async def test_dry_run_no_facts_table_returns_empty_result(self):
        """UndefinedTableError → empty BackfillResult, no crash."""
        import asyncpg

        conn = _FakeConn()
        conn.fetch = AsyncMock(side_effect=asyncpg.UndefinedTableError("no table"))
        pool = _FakePool(conn)

        result = await backfill_schema(pool, "no_memory_schema", apply=False)

        assert result.entities_created == 0
        assert result.facts_updated == 0
        assert result.diagnostic == []


class TestBackfillSchemaApply:
    """Apply mode: creates entities and updates facts."""

    async def test_apply_creates_entity_and_updates_facts(self):
        fact_rows = [
            {"subject": "Nutrition Kitchen SG", "scope": "finance", "fact_count": 3},
        ]
        conn = _FakeConn(fact_rows=fact_rows, update_result="UPDATE 3")
        pool = _FakePool(conn)

        result = await backfill_schema(pool, "finance", apply=True)

        assert result.entities_created == 1
        assert result.entities_resolved == 0
        assert result.facts_updated == 3
        assert result.errors == []

    async def test_apply_multiple_pairs(self):
        fact_rows = [
            {"subject": "Merchant A", "scope": "finance", "fact_count": 2},
            {"subject": "Merchant B", "scope": "finance", "fact_count": 5},
        ]
        entity_uuids = iter(
            [
                uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-000000000001"),
                uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-000000000002"),
            ]
        )
        conn = _FakeConn(fact_rows=fact_rows, update_result="UPDATE 2")
        conn.fetchval = AsyncMock(side_effect=lambda *args: next(entity_uuids))
        pool = _FakePool(conn)

        result = await backfill_schema(pool, "finance", apply=True)

        assert result.entities_created == 2
        assert result.facts_updated == 4  # 2 rows × "UPDATE 2" each

    async def test_apply_generic_labels_skipped(self):
        fact_rows = [
            {"subject": "Owner", "scope": "global", "fact_count": 10},
        ]
        conn = _FakeConn(fact_rows=fact_rows)
        pool = _FakePool(conn)

        result = await backfill_schema(pool, "finance", apply=True)

        assert result.entities_created == 0
        assert result.facts_updated == 0

    async def test_apply_captures_errors_per_entity(self):
        """Entity creation errors are captured in result.errors, not raised."""
        fact_rows = [
            {"subject": "Bad Entity", "scope": "finance", "fact_count": 1},
        ]
        conn = _FakeConn(fact_rows=fact_rows)
        # Make fetchval raise a generic exception (not UniqueViolation)
        conn.fetchval = AsyncMock(side_effect=RuntimeError("DB error"))
        pool = _FakePool(conn)

        result = await backfill_schema(pool, "finance", apply=True)

        assert len(result.errors) == 1
        assert "Bad Entity" in result.errors[0]
        assert result.entities_created == 0
        assert result.facts_updated == 0


# ---------------------------------------------------------------------------
# discover_memory_schemas tests
# ---------------------------------------------------------------------------


class TestDiscoverMemorySchemas:
    """Tests for discover_memory_schemas() schema discovery."""

    async def test_returns_sorted_schema_list(self):
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(
            return_value=[
                {"table_schema": "finance"},
                {"table_schema": "health"},
                {"table_schema": "relationship"},
            ]
        )

        schemas = await discover_memory_schemas(mock_pool)

        assert schemas == ["finance", "health", "relationship"]

    async def test_empty_result_when_no_memory_schemas(self):
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        schemas = await discover_memory_schemas(mock_pool)

        assert schemas == []

    async def test_query_excludes_system_schemas(self):
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        await discover_memory_schemas(mock_pool)

        call_sql = mock_pool.fetch.call_args[0][0]
        assert "information_schema" in call_sql
        assert "public" in call_sql
        assert "shared" in call_sql


# ---------------------------------------------------------------------------
# Integration-style: BackfillResult dataclass
# ---------------------------------------------------------------------------


class TestBackfillResultDataclass:
    """Verify BackfillResult behaves as expected."""

    def test_default_fields(self):
        r = BackfillResult(schema="finance")
        assert r.schema == "finance"
        assert r.diagnostic == []
        assert r.entities_created == 0
        assert r.entities_resolved == 0
        assert r.facts_updated == 0
        assert r.errors == []


# ---------------------------------------------------------------------------
# _validate_schema_name tests
# ---------------------------------------------------------------------------


class TestValidateSchemaName:
    """Unit tests for schema name validation (SQL injection guard)."""

    def test_valid_simple_name(self):
        assert _validate_schema_name("finance") == "finance"

    def test_valid_name_with_underscore(self):
        assert _validate_schema_name("my_schema") == "my_schema"

    def test_valid_name_starting_with_underscore(self):
        assert _validate_schema_name("_private") == "_private"

    def test_valid_name_with_numbers(self):
        assert _validate_schema_name("schema2") == "schema2"

    def test_rejects_semicolon_injection(self):
        with pytest.raises(ValueError, match="Invalid schema name"):
            _validate_schema_name("public; DROP TABLE facts; --")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError, match="Invalid schema name"):
            _validate_schema_name("my schema")

    def test_rejects_hyphen(self):
        with pytest.raises(ValueError, match="Invalid schema name"):
            _validate_schema_name("my-schema")

    def test_rejects_leading_digit(self):
        with pytest.raises(ValueError, match="Invalid schema name"):
            _validate_schema_name("1schema")

    def test_rejects_dot(self):
        with pytest.raises(ValueError, match="Invalid schema name"):
            _validate_schema_name("public.facts")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="Invalid schema name"):
            _validate_schema_name("")


class TestBackfillSchemaValidation:
    """backfill_schema must reject unsafe schema names before touching the DB."""

    async def test_rejects_invalid_schema_name(self):
        pool = _FakePool(_FakeConn())
        with pytest.raises(ValueError, match="Invalid schema name"):
            await backfill_schema(pool, "bad; DROP TABLE facts; --", apply=False)
