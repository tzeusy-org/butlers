"""Unit tests for predicate lifecycle (status, deprecation, supersedence) in store_fact().

Covers tasks 10.1–10.5 from openspec/changes/predicate-registry-enforcement/tasks.md:

  10.2 Write-time warning for deprecated predicates (write succeeds, warning in response)
  10.3 Auto-registered predicates get status='proposed'
  10.5 Status filtering / response shape

Scenarios tested:

  Deprecated predicate warns (10.2):
    - deprecated predicate with superseded_by → response includes warning with replacement
    - deprecated predicate without superseded_by → response includes warning, no replacement
    - write still succeeds for deprecated predicates (fact is stored)
    - non-deprecated predicate (active) → no warning in response
    - novel predicate (not in registry) → no warning in response

  Auto-registration status='proposed' (10.3):
    - novel predicate is auto-registered with status='proposed'
    - registered predicate is NOT re-inserted (no auto-registration)

  Warning does not block write:
    - deprecated predicate write returns dict with 'id', 'warning', 'supersedes_id'
    - 'id' is a valid UUID
"""

from __future__ import annotations

import importlib.util
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.modules.memory._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module from disk (not a Python package).
# ---------------------------------------------------------------------------
_STORAGE_PATH = MEMORY_MODULE_PATH / "storage.py"


def _load_storage_module():
    spec = importlib.util.spec_from_file_location("storage", _STORAGE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_storage_module()
store_fact = _mod.store_fact

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


@pytest.fixture()
def embedding_engine():
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    return engine


def _make_pool(conn):
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


def _make_conn(
    *,
    fetchval_side_effect=None,
    fetchval_return=None,
    fetchrow_side_effect=None,
    fetchrow_return=None,
):
    conn = AsyncMock()
    if fetchval_side_effect is not None:
        conn.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        conn.fetchval = AsyncMock(return_value=fetchval_return)
    if fetchrow_side_effect is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    return conn


def _get_auto_registration_execute_call(conn):
    """Return the predicate_registry INSERT execute call, or None."""
    for c in conn.execute.call_args_list:
        sql = c.args[0] if c.args else ""
        if "predicate_registry" in sql and "INSERT" in sql:
            return c
    return None


# ---------------------------------------------------------------------------
# Tests: deprecated predicate produces warning
# ---------------------------------------------------------------------------


class TestDeprecatedPredicateWarning:
    """Deprecated predicate writes succeed but include a warning in the response."""

    async def test_deprecated_with_superseded_by_includes_warning_with_replacement(
        self, embedding_engine
    ):
        """Deprecated predicate with superseded_by produces warning naming the replacement.

        WHEN store_fact() is called with a predicate that has status='deprecated'
        and superseded_by='new_predicate',
        THEN the response MUST include a 'warning' key whose value mentions both
        the deprecated predicate name and the replacement predicate name.
        """
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "status": "deprecated",
            "superseded_by": "medication",
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        # fetchrow: [registry=deprecated_row, supersession=None]
        conn = _make_conn(fetchrow_side_effect=[registry_row, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "dosage",
            "10mg",
            embedding_engine,
        )

        assert "warning" in result, "Expected 'warning' key in response for deprecated predicate"
        warning = result["warning"]
        assert "dosage" in warning, "Warning must mention the deprecated predicate name"
        assert "medication" in warning, "Warning must mention the replacement predicate"

    async def test_deprecated_without_superseded_by_includes_generic_warning(
        self, embedding_engine
    ):
        """Deprecated predicate without superseded_by produces a generic warning.

        WHEN store_fact() is called with a predicate that has status='deprecated'
        and superseded_by=None,
        THEN the response MUST include a 'warning' key without a specific replacement.
        """
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "status": "deprecated",
            "superseded_by": None,
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        conn = _make_conn(fetchrow_side_effect=[registry_row, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "doctor_name",
            "Dr Smith",
            embedding_engine,
        )

        assert "warning" in result
        warning = result["warning"]
        assert "doctor_name" in warning
        # No specific replacement is mentioned — check for generic language
        assert "deprecated" in warning

    async def test_deprecated_predicate_write_succeeds(self, embedding_engine):
        """Deprecated predicate writes succeed — the warning is non-blocking.

        WHEN store_fact() is called with a deprecated predicate,
        THEN the fact MUST still be stored (no exception raised)
        AND the response MUST contain a valid 'id' UUID.
        """
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "status": "deprecated",
            "superseded_by": "condition",
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        conn = _make_conn(fetchrow_side_effect=[registry_row, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "condition_status",
            "managed",
            embedding_engine,
        )

        assert isinstance(result, dict)
        assert isinstance(result["id"], uuid.UUID)
        assert "warning" in result

    async def test_deprecated_predicate_response_has_expected_keys(self, embedding_engine):
        """Deprecated predicate response includes id, supersedes_id, and warning.

        WHEN store_fact() is called with a deprecated predicate,
        THEN the response dict MUST include 'id', 'supersedes_id', and 'warning'.
        """
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "status": "deprecated",
            "superseded_by": "symptom",
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        conn = _make_conn(fetchrow_side_effect=[registry_row, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "symptom_pattern",
            "morning headaches",
            embedding_engine,
        )

        assert "id" in result
        assert "supersedes_id" in result
        assert "warning" in result


# ---------------------------------------------------------------------------
# Tests: active predicate produces no warning
# ---------------------------------------------------------------------------


class TestActivePredicateNoWarning:
    """Active predicates do not produce a warning."""

    async def test_active_predicate_does_not_include_warning(self, embedding_engine):
        """Active predicate writes produce no 'warning' key in the response.

        WHEN store_fact() is called with a predicate that has status='active',
        THEN the response MUST NOT include a 'warning' key.
        """
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "status": "active",
            "superseded_by": None,
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        conn = _make_conn(fetchrow_side_effect=[registry_row, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "birthday",
            "1990-01-01",
            embedding_engine,
        )

        assert "warning" not in result

    async def test_novel_predicate_does_not_include_warning(self, embedding_engine):
        """Novel (unregistered) predicate writes produce no 'warning' key.

        WHEN store_fact() is called with a predicate NOT in the registry,
        THEN the response MUST NOT include a 'warning' key.
        """
        # fetchrow returns None (novel predicate), then None (supersession check)
        conn = _make_conn(fetchrow_side_effect=[None, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "brand_new_predicate",
            "some value",
            embedding_engine,
        )

        assert "warning" not in result

    async def test_proposed_predicate_does_not_include_warning(self, embedding_engine):
        """Proposed predicate (auto-registered, not yet curated) produces no warning.

        WHEN store_fact() is called with a predicate that has status='proposed',
        THEN the response MUST NOT include a 'warning' key.
        """
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "status": "proposed",
            "superseded_by": None,
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        conn = _make_conn(fetchrow_side_effect=[registry_row, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "recently_auto_registered",
            "some value",
            embedding_engine,
        )

        assert "warning" not in result


# ---------------------------------------------------------------------------
# Tests: auto-registered predicates get status='proposed'
# ---------------------------------------------------------------------------


class TestAutoRegistrationProposedStatus:
    """Novel predicates are auto-registered with status='proposed'."""

    async def test_auto_registered_predicate_has_proposed_status(self, embedding_engine):
        """Novel predicate auto-registration INSERT uses status='proposed'.

        WHEN store_fact() succeeds with a predicate NOT in the registry,
        THEN the auto-registration INSERT MUST specify status='proposed'.
        """
        conn = _make_conn(fetchrow_side_effect=[None, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "novel_predicate_xyz",
            "some value",
            embedding_engine,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is not None, "Expected auto-registration INSERT to be called"
        sql = reg_call.args[0]
        assert "'proposed'" in sql, (
            "Auto-registration INSERT must use status='proposed', got: " + sql
        )

    async def test_auto_registration_does_not_use_active_status(self, embedding_engine):
        """Novel predicate auto-registration must NOT default to status='active'.

        WHEN a novel predicate is auto-registered,
        THEN the INSERT MUST explicitly set status='proposed' — not 'active'.
        Auto-registered predicates need curator review before promotion to 'active'.
        """
        conn = _make_conn(fetchrow_side_effect=[None, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "another_novel_predicate",
            "value",
            embedding_engine,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is not None
        sql = reg_call.args[0]
        # The SQL should explicitly include 'proposed', not just rely on a default
        assert "'proposed'" in sql

    async def test_registered_predicate_skips_auto_registration(self, embedding_engine):
        """Registered predicates (any status) are not re-inserted.

        WHEN store_fact() is called with a predicate already in the registry,
        THEN NO auto-registration INSERT is issued.
        """
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "status": "active",
            "superseded_by": None,
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        conn = _make_conn(fetchrow_side_effect=[registry_row, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "birthday",
            "1990-01-01",
            embedding_engine,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is None, (
            "Auto-registration INSERT must NOT be called for already-registered predicates"
        )

    async def test_deprecated_predicate_skips_auto_registration(self, embedding_engine):
        """Deprecated predicates (in registry) are not re-inserted.

        WHEN store_fact() is called with a deprecated predicate (still in registry),
        THEN NO auto-registration INSERT is issued.
        """
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "status": "deprecated",
            "superseded_by": "condition",
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        conn = _make_conn(fetchrow_side_effect=[registry_row, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "condition_status",
            "active",
            embedding_engine,
        )

        reg_call = _get_auto_registration_execute_call(conn)
        assert reg_call is None, (
            "Auto-registration INSERT must NOT be called for deprecated predicates"
        )


# ---------------------------------------------------------------------------
# Tests: backward compatibility (old mocked registry without status/superseded_by)
# ---------------------------------------------------------------------------


class TestBackwardCompatibilityWithOldMocks:
    """Registry rows with complete column set (including lifecycle + type columns) work correctly."""

    async def test_registry_row_with_full_columns_produces_no_warning(self, embedding_engine):
        """Registry row with all expected columns and no type mismatch produces no warning.

        WHEN store_fact() is called with a registry row that includes all columns
        (status, superseded_by, expected_subject_type, expected_object_type)
        and there is no type mismatch,
        THEN no warning is included in the response.
        """
        # Full column set: all present, no type mismatch possible (no entity_id)
        registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "status": "active",
            "superseded_by": None,
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        conn = _make_conn(fetchrow_side_effect=[registry_row, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "birthday",
            "1990-01-01",
            embedding_engine,
        )

        assert isinstance(result, dict)
        assert "warning" not in result
