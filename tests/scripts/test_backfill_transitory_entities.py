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


@pytest.mark.parametrize(
    "label, scope, expected",
    [
        ("Nutrition Kitchen SG", "finance", "organization"),
        ("Marina Bay Sands", "travel", "place"),
        ("Sarah", "relationship", "person"),
        ("City Medical Centre", "health", "organization"),
        ("NUS Business School", "education", "organization"),
        ("Dr. Smith", "finance", "person"),  # person prefix overrides scope
        ("Acme Corp", "global", "organization"),
        ("ABC Solutions Pte Ltd", "global", "organization"),
        ("Sentosa Beach Resort", "global", "place"),
        ("FooBarBaz", "global", "other"),  # no scope mapping, no tokens
        ("SmartLock 3000", "home", "other"),  # home defaults to other
    ],
)
def test_infer_entity_type(label, scope, expected):
    assert infer_entity_type(label, scope) == expected


# ---------------------------------------------------------------------------
# GENERIC_LABELS tests
# ---------------------------------------------------------------------------


def test_generic_labels_set():
    """Verify all expected generic labels are present."""
    for label in ("Owner", "user", "User", "me", "Me", "self", "Self", "I"):
        assert label in GENERIC_LABELS, f"Expected {label!r} in GENERIC_LABELS"


# ---------------------------------------------------------------------------
# run_diagnostic tests
# ---------------------------------------------------------------------------


class TestRunDiagnostic:
    """Tests for run_diagnostic() querying the facts table."""

    async def test_returns_rows_and_excludes_generic_labels(self):
        """Returns DiagnosticRow entries for non-generic subjects; filters generic ones."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {"subject": "Nutrition Kitchen SG", "scope": "finance", "fact_count": 3},
                {"subject": "Marina Bay Hotel", "scope": "travel", "fact_count": 1},
                {"subject": "Owner", "scope": "global", "fact_count": 10},
                {"subject": "user", "scope": "global", "fact_count": 5},
            ]
        )

        rows = await run_diagnostic(mock_conn, "finance")

        assert len(rows) == 2
        assert rows[0].subject == "Nutrition Kitchen SG"
        assert rows[0].inferred_type == "organization"
        assert rows[1].subject == "Marina Bay Hotel"
        assert rows[1].inferred_type == "place"

    async def test_empty_result_and_sql_filters(self):
        """Empty table returns []; SQL contains entity_id IS NULL and validity = active."""
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])

        rows = await run_diagnostic(mock_conn, "finance")

        assert rows == []
        call_sql = mock_conn.fetch.call_args[0][0]
        assert "entity_id IS NULL" in call_sql
        assert "validity = 'active'" in call_sql


# ---------------------------------------------------------------------------
# resolve_or_create_entity tests
# ---------------------------------------------------------------------------


class _FakeTransactionForResolve:
    """Minimal async context manager standing in for asyncpg conn.transaction()."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _mock_conn_with_transaction(**kwargs):
    """Create an AsyncMock connection whose .transaction() returns an async CM."""
    mock_conn = AsyncMock(**kwargs)
    mock_conn.transaction = lambda: _FakeTransactionForResolve()
    return mock_conn


class TestResolveOrCreateEntity:
    """Tests for the INSERT-then-resolve logic."""

    async def test_creates_entity_with_metadata(self):
        """Returns (entity_id, True) on INSERT success; metadata has unidentified=True."""
        expected_uuid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        mock_conn = _mock_conn_with_transaction()
        mock_conn.fetchval = AsyncMock(return_value=expected_uuid)

        entity_id_str, was_created = await resolve_or_create_entity(
            mock_conn, "Nutrition Kitchen SG", "finance", "organization", "finance"
        )

        assert entity_id_str == str(expected_uuid)
        assert was_created is True

        call_sql = mock_conn.fetchval.call_args[0][0]
        assert "INSERT INTO public.entities" in call_sql

        # Verify metadata has unidentified flag and provenance
        metadata_json = mock_conn.fetchval.call_args[0][5]
        metadata = json.loads(metadata_json)
        assert metadata["unidentified"] is True
        assert metadata["source"] == "backfill"

    async def test_resolves_existing_and_tombstoned_raises(self):
        """On UniqueViolationError: returns (entity_id, False); tombstoned raises RuntimeError."""
        import asyncpg

        expected_uuid = uuid.UUID("bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee")
        mock_conn = _mock_conn_with_transaction()

        # Case 1: resolve to existing entity
        mock_conn.fetchval = AsyncMock(
            side_effect=[asyncpg.UniqueViolationError("duplicate"), expected_uuid]
        )
        entity_id_str, was_created = await resolve_or_create_entity(
            mock_conn, "Acme Corp", "finance", "organization", "finance"
        )
        assert entity_id_str == str(expected_uuid)
        assert was_created is False
        assert mock_conn.fetchval.call_count == 2

        # Case 2: tombstoned — fetchval returns None twice → RuntimeError
        mock_conn2 = _mock_conn_with_transaction()
        mock_conn2.fetchval = AsyncMock(
            side_effect=[asyncpg.UniqueViolationError("dup"), None, None]
        )
        with pytest.raises(RuntimeError, match="could not be resolved"):
            await resolve_or_create_entity(
                mock_conn2, "Dead Entity", "finance", "organization", "finance"
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


class TestBackfillSchema:
    """Dry-run and apply modes."""

    async def test_dry_run_diagnostic_only(self):
        """Dry-run: returns diagnostic without writing; excludes generic labels."""
        fact_rows = [
            {"subject": "Nutrition Kitchen SG", "scope": "finance", "fact_count": 3},
            {"subject": "Owner", "scope": "global", "fact_count": 10},
        ]
        conn = _FakeConn(fact_rows=fact_rows)
        pool = _FakePool(conn)

        result = await backfill_schema(pool, "finance", apply=False)

        assert len(result.diagnostic) == 1
        assert result.diagnostic[0].subject == "Nutrition Kitchen SG"
        assert result.entities_created == 0
        assert result.facts_updated == 0

    async def test_apply_creates_entity_and_captures_errors(self):
        """Apply mode: creates entities; generic labels skipped; errors captured per entity."""

        # With a real entity
        fact_rows = [{"subject": "Merchant A", "scope": "finance", "fact_count": 2}]
        conn = _FakeConn(fact_rows=fact_rows, update_result="UPDATE 2")
        pool = _FakePool(conn)
        result = await backfill_schema(pool, "finance", apply=True)
        assert result.entities_created == 1
        assert result.facts_updated == 2

        # With error mid-entity
        error_rows = [{"subject": "Bad Entity", "scope": "finance", "fact_count": 1}]
        conn2 = _FakeConn(fact_rows=error_rows)
        conn2.fetchval = AsyncMock(side_effect=RuntimeError("DB error"))
        pool2 = _FakePool(conn2)
        result2 = await backfill_schema(pool2, "finance", apply=True)
        assert len(result2.errors) == 1
        assert "Bad Entity" in result2.errors[0]
        assert result2.entities_created == 0


# ---------------------------------------------------------------------------
# discover_memory_schemas tests
# ---------------------------------------------------------------------------


async def test_discover_memory_schemas():
    """Returns sorted schema list; empty when none; SQL excludes system schemas."""
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

    # Empty case + SQL check
    mock_pool.fetch = AsyncMock(return_value=[])
    schemas = await discover_memory_schemas(mock_pool)
    assert schemas == []
    call_sql = mock_pool.fetch.call_args[0][0]
    assert "information_schema" in call_sql
    assert "public" in call_sql


# ---------------------------------------------------------------------------
# BackfillResult dataclass
# ---------------------------------------------------------------------------


def test_backfill_result_default_fields():
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


@pytest.mark.parametrize("name", ["finance", "my_schema", "_private", "schema2"])
def test_validate_schema_name_valid(name):
    assert _validate_schema_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "public; DROP TABLE facts; --",
        "my schema",
        "my-schema",
        "1schema",
        "public.facts",
        "",
    ],
)
def test_validate_schema_name_invalid(name):
    with pytest.raises(ValueError, match="Invalid schema name"):
        _validate_schema_name(name)


async def test_backfill_schema_rejects_invalid_name():
    pool = _FakePool(_FakeConn())
    with pytest.raises(ValueError, match="Invalid schema name"):
        await backfill_schema(pool, "bad; DROP TABLE facts; --", apply=False)
