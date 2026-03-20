"""Unit tests for predicate alias resolution in store_fact().

Covers the alias resolution feature added in migration mem_025:

  - When predicate matches an alias, it is resolved to the canonical name
    before all other logic (enforcement, supersession, auto-registration).
  - The response includes a 'resolved_from' key naming the original alias.
  - When the predicate does not match any alias, no resolution occurs and
    'resolved_from' is absent from the response.
  - Alias resolution failure (pre-migration environment) is silently skipped
    and the predicate is used as-is.
  - Alias resolution uses the canonical name for registry enforcement (e.g.
    is_edge, is_temporal, deprecation warnings).
  - Alias resolution uses the canonical name for auto-registration (the
    canonical predicate is what gets inserted, not the alias).
"""

from __future__ import annotations

import importlib.util
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.modules.memory._test_helpers import MEMORY_MODULE_PATH

# ---------------------------------------------------------------------------
# Load storage module from disk (roster/ is not a Python package).
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
    fetchrow_side_effect=None,
    fetchrow_return=None,
):
    """Build an AsyncMock connection.

    fetchrow_side_effect is used for sequential calls (alias lookup, then
    registry lookup, then supersession check, ...).
    """
    conn = AsyncMock()
    if fetchrow_side_effect is not None:
        conn.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.fetchval = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    return conn


def _active_registry_row(
    *,
    is_edge: bool = False,
    is_temporal: bool = False,
    expected_subject_type=None,
    expected_object_type=None,
):
    return {
        "is_edge": is_edge,
        "is_temporal": is_temporal,
        "status": "active",
        "superseded_by": None,
        "expected_subject_type": expected_subject_type,
        "expected_object_type": expected_object_type,
    }


# ---------------------------------------------------------------------------
# Tests: alias resolved to canonical name
# ---------------------------------------------------------------------------


class TestAliasResolution:
    """When predicate matches an alias, it resolves to the canonical name."""

    async def test_alias_resolution_returns_resolved_from_key(self, embedding_engine):
        """resolved_from is set to the original alias name when resolution occurs.

        WHEN store_fact() is called with a predicate that matches an alias
        AND the alias lookup returns the canonical predicate name,
        THEN the response MUST include 'resolved_from' set to the original alias.
        """
        # fetchrow call order inside store_fact:
        #   1. alias lookup → row with canonical name
        #   2. registry enforcement lookup → active row for canonical
        #   3. supersession check → None (no existing fact)
        alias_row = {"name": "medication"}
        registry_row = _active_registry_row()
        conn = _make_conn(fetchrow_side_effect=[alias_row, registry_row, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "med",  # alias for 'medication'
            "10mg",
            embedding_engine,
        )

        assert "resolved_from" in result, "Expected 'resolved_from' key when alias is resolved"
        assert result["resolved_from"] == "med"

    async def test_alias_resolution_uses_canonical_for_storage(self, embedding_engine):
        """After alias resolution the canonical predicate name is used in the INSERT.

        WHEN store_fact() resolves 'med' → 'medication' via alias lookup,
        THEN the INSERT into facts MUST use 'medication', not 'med'.
        """
        alias_row = {"name": "medication"}
        registry_row = _active_registry_row()
        conn = _make_conn(fetchrow_side_effect=[alias_row, registry_row, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "med",
            "10mg",
            embedding_engine,
        )

        # Find the INSERT INTO facts execute call and check predicate value.
        insert_call = None
        for c in conn.execute.call_args_list:
            sql = c.args[0] if c.args else ""
            if "INSERT INTO facts" in sql:
                insert_call = c
                break

        assert insert_call is not None, "Expected INSERT INTO facts to be called"
        # The predicate value is the third positional argument after subject
        # and content — check that 'medication' appears in the call args, not 'med'.
        call_args = insert_call.args
        assert "medication" in call_args, f"Expected 'medication' in INSERT args, got: {call_args}"
        assert "med" not in call_args, f"Expected alias 'med' NOT in INSERT args, got: {call_args}"

    async def test_alias_resolution_no_resolved_from_when_no_alias_match(self, embedding_engine):
        """No resolved_from key when predicate is canonical (no alias match).

        WHEN store_fact() is called with a predicate that is NOT an alias
        (alias lookup returns None),
        THEN the response MUST NOT include 'resolved_from'.
        """
        # alias lookup → None (no match), registry lookup → active row, supersession → None
        registry_row = _active_registry_row()
        conn = _make_conn(fetchrow_side_effect=[None, registry_row, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "medication",  # canonical predicate, not an alias
            "10mg",
            embedding_engine,
        )

        assert "resolved_from" not in result, (
            "Expected no 'resolved_from' key when predicate is canonical"
        )

    async def test_alias_resolution_no_resolved_from_for_novel_predicate(self, embedding_engine):
        """No resolved_from key for novel predicates (alias lookup returns None).

        WHEN store_fact() is called with a brand-new predicate not in the registry,
        THEN the alias lookup returns None AND registry lookup returns None,
        AND the response MUST NOT include 'resolved_from'.
        """
        # alias lookup → None, registry lookup → None (novel), supersession → None
        conn = _make_conn(fetchrow_side_effect=[None, None, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "brand_new_predicate",
            "some value",
            embedding_engine,
        )

        assert "resolved_from" not in result

    async def test_alias_resolution_skipped_when_query_raises(self, embedding_engine):
        """Alias resolution failure is silently skipped (pre-migration environment).

        WHEN the alias lookup query raises an exception (e.g. aliases column absent),
        THEN store_fact() MUST still succeed using the original predicate name
        AND 'resolved_from' MUST NOT be present in the response.
        """

        # Use a custom fetchrow that raises on the first call.
        conn = AsyncMock()

        _call_count = 0

        async def fetchrow_impl(*args, **kwargs):
            nonlocal _call_count
            _call_count += 1
            if _call_count == 1:
                raise Exception("column aliases does not exist")
            if _call_count == 2:
                return _active_registry_row()
            return None  # supersession check

        conn.fetchrow = AsyncMock(side_effect=fetchrow_impl)
        conn.fetchval = AsyncMock(return_value=None)
        conn.execute = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "medication",
            "10mg",
            embedding_engine,
        )

        assert isinstance(result, dict)
        assert "id" in result
        assert "resolved_from" not in result


# ---------------------------------------------------------------------------
# Tests: alias resolution + downstream enforcement
# ---------------------------------------------------------------------------


class TestAliasResolutionWithEnforcement:
    """Alias resolution interacts correctly with registry enforcement."""

    async def test_alias_resolves_to_deprecated_canonical_produces_warning(self, embedding_engine):
        """Alias resolves to deprecated canonical → deprecation warning in response.

        WHEN store_fact() is called with an alias for a deprecated canonical predicate,
        THEN the alias resolves to the canonical name AND the deprecation warning
        for the canonical predicate is included in the response.
        """
        alias_row = {"name": "dosage"}
        deprecated_registry_row = {
            "is_edge": False,
            "is_temporal": False,
            "status": "deprecated",
            "superseded_by": "medication",
            "expected_subject_type": None,
            "expected_object_type": None,
        }
        # alias lookup → dosage, registry → deprecated, supersession → None
        conn = _make_conn(fetchrow_side_effect=[alias_row, deprecated_registry_row, None])
        pool = _make_pool(conn)

        result = await store_fact(
            pool,
            "user",
            "dose",  # alias for deprecated 'dosage'
            "10mg",
            embedding_engine,
        )

        assert "resolved_from" in result
        assert result["resolved_from"] == "dose"
        assert "warning" in result
        assert "dosage" in result["warning"]
        assert "medication" in result["warning"]

    async def test_alias_resolves_canonical_used_for_usage_tracking(self, embedding_engine):
        """Alias resolution: usage_count UPDATE targets the canonical name.

        WHEN store_fact() resolves 'med' → 'medication' via alias lookup,
        THEN the usage_count UPDATE in predicate_registry MUST use 'medication'.
        """
        alias_row = {"name": "medication"}
        registry_row = _active_registry_row()
        conn = _make_conn(fetchrow_side_effect=[alias_row, registry_row, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "med",
            "10mg",
            embedding_engine,
        )

        # Find the usage tracking UPDATE call.
        usage_call = None
        for c in conn.execute.call_args_list:
            sql = c.args[0] if c.args else ""
            if "usage_count" in sql and "UPDATE predicate_registry" in sql:
                usage_call = c
                break

        assert usage_call is not None, "Expected usage_count UPDATE call"
        # The canonical name 'medication' must be the parameter, not 'med'.
        call_args = usage_call.args
        assert "medication" in call_args, (
            f"Expected 'medication' as usage tracking param, got: {call_args}"
        )

    async def test_no_auto_registration_when_alias_resolved(self, embedding_engine):
        """No auto-registration INSERT when alias resolves to an existing canonical.

        WHEN store_fact() resolves an alias to a canonical predicate already in
        the registry,
        THEN the predicate is NOT novel and NO auto-registration INSERT is issued.
        """
        alias_row = {"name": "medication"}
        registry_row = _active_registry_row()
        conn = _make_conn(fetchrow_side_effect=[alias_row, registry_row, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "med",
            "10mg",
            embedding_engine,
        )

        # No INSERT INTO predicate_registry should be present.
        for c in conn.execute.call_args_list:
            sql = c.args[0] if c.args else ""
            assert not ("INSERT INTO predicate_registry" in sql and "proposed" in sql), (
                "Auto-registration INSERT must NOT be called when alias resolves"
                " to existing predicate"
            )


# ---------------------------------------------------------------------------
# Tests: migration 025 — aliases column present in INSERT
# ---------------------------------------------------------------------------


class TestMigrationAliasesColumn:
    """Validate that auto-registration INSERT includes the aliases column correctly."""

    async def test_auto_registration_insert_does_not_include_alias_as_name(self, embedding_engine):
        """Novel predicate (no alias match) is auto-registered by its own name, not an alias.

        WHEN store_fact() is called with a predicate not in the registry AND
        the alias lookup returns None,
        THEN the auto-registration INSERT uses the original predicate name
        (which IS the canonical name in this case — it's a genuinely new predicate).
        """
        # alias lookup → None (not an alias), registry lookup → None (novel)
        conn = _make_conn(fetchrow_side_effect=[None, None, None])
        pool = _make_pool(conn)

        await store_fact(
            pool,
            "user",
            "my_new_predicate",
            "some value",
            embedding_engine,
        )

        # Find auto-registration INSERT call.
        insert_call = None
        for c in conn.execute.call_args_list:
            sql = c.args[0] if c.args else ""
            if "INSERT INTO predicate_registry" in sql and "proposed" in sql:
                insert_call = c
                break

        assert insert_call is not None, "Expected auto-registration INSERT for novel predicate"
        assert "my_new_predicate" in insert_call.args, (
            "Auto-registration INSERT must use the original predicate name"
        )
