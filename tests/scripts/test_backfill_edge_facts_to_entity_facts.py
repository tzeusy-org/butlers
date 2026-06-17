"""Tests for scripts/backfill_edge_facts_to_entity_facts.py.

Covers:
1. classify_predicate — underscore aliases resolve to canonical registry name.
2. classify_predicate — unknown predicate is treated as narrative.
3. classify_predicate — already-canonical hyphenated predicate resolves directly.
4. dry-run — no DB writes (pool.execute not called, assert_fact not called).
5. apply — mappable edge is re-asserted via the central writer and memory copy
   is retracted exactly once.
6. idempotency — re-running after apply finds zero active rows → no-op.
7. narrative edge — predicate not in registry; fact is left in memory unchanged.
8. loader — _load_assert_fact_fn() registers the module in sys.modules BEFORE
   exec_module so @dataclass KW_ONLY resolution succeeds.
9. owner carve-out (pending_approval) — source row is NOT retracted and the
   parked counter increments (bu-2ezvz).
10. non-pending outcome (active write) — source row IS retracted and parked
    counter stays zero (bu-2ezvz regression guard).

Issue: bu-1fu8c, bu-hzz09, bu-2ezvz
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Load the script under test
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "butlers"
    / "scripts"
    / "backfill_edge_facts_to_entity_facts.py"
)
_MODULE_NAME = "backfill_edge_facts_to_entity_facts"


def _load_script():
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_script()

classify_predicate = _mod.classify_predicate
run = _mod.run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENTITY_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_OBJECT_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")

# Canonical predicates in the "registry" for test purposes.
_REGISTRY = {"works-at", "member-of", "friend-of", "child-of", "parent-of"}


def _make_fact_row(
    predicate: str,
    entity_id: uuid.UUID = _ENTITY_ID,
    object_entity_id: uuid.UUID = _OBJECT_ID,
    confidence: float = 0.9,
) -> MagicMock:
    """Build a minimal mock asyncpg Record for a relationship.facts row."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": uuid.uuid4(),
        "entity_id": entity_id,
        "object_entity_id": object_entity_id,
        "predicate": predicate,
        "confidence": confidence,
        "last_confirmed_at": None,
        "source_butler": "relationship",
    }[key]
    row.get = lambda key, default=None: {
        "id": uuid.uuid4(),
        "entity_id": entity_id,
        "object_entity_id": object_entity_id,
        "predicate": predicate,
        "confidence": confidence,
        "last_confirmed_at": None,
        "source_butler": "relationship",
    }.get(key, default)
    return row


def _make_pool(edge_rows: list) -> AsyncMock:
    """Create a mock pool that returns *edge_rows* from _fetch_edge_facts."""
    pool = AsyncMock()
    registry_row_objects = [_make_predicate_row(p) for p in _REGISTRY]
    # First .fetch() call: registered predicates; subsequent: edge facts.
    pool.fetch = AsyncMock(side_effect=[registry_row_objects, edge_rows])
    pool.execute = AsyncMock(return_value="UPDATE 1")
    pool.close = AsyncMock()
    return pool


def _make_predicate_row(predicate: str) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: predicate if key == "predicate" else None
    return row


def _make_assert_fact_result(outcome: str = "inserted") -> MagicMock:
    result = MagicMock()
    result.outcome.value = outcome
    return result


# ---------------------------------------------------------------------------
# classify_predicate tests
# ---------------------------------------------------------------------------


def test_classify_underscore_alias_mappable() -> None:
    """Underscore alias resolves to canonical and is mappable."""
    mappable, canonical = classify_predicate("works_at", {"works-at"})
    assert mappable is True
    assert canonical == "works-at"


def test_classify_unknown_predicate_is_narrative() -> None:
    """Unknown predicate is not mappable — treated as narrative."""
    mappable, canonical = classify_predicate("planned_dinner_with", _REGISTRY)
    assert mappable is False
    assert canonical == "planned_dinner_with"


def test_classify_already_canonical_hyphenated() -> None:
    """A predicate already in hyphenated canonical form resolves directly."""
    mappable, canonical = classify_predicate("works-at", {"works-at"})
    assert mappable is True
    assert canonical == "works-at"


def test_classify_many_to_one_sibling_of() -> None:
    """sibling_of maps to family-of (many-to-one alias)."""
    mappable, canonical = classify_predicate("sibling_of", {"family-of"})
    assert mappable is True
    assert canonical == "family-of"


def test_classify_predicate_not_in_registry_despite_alias() -> None:
    """Alias resolves canonically but canonical not in registry → narrative."""
    mappable, canonical = classify_predicate("works_at", set())  # empty registry
    assert mappable is False
    assert canonical == "works-at"


# ---------------------------------------------------------------------------
# dry-run: no writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_makes_no_writes() -> None:
    """In dry-run mode, assert_fact and pool.execute are never called."""
    rows = [_make_fact_row("works_at")]
    pool = _make_pool(rows)
    assert_fact = AsyncMock(return_value=_make_assert_fact_result())

    result = await run(pool, dry_run=True, _assert_fact=assert_fact)

    assert_fact.assert_not_called()
    pool.execute.assert_not_called()
    # Counts are still non-zero in dry-run (they represent the plan).
    assert result["total_migrated"] == 1
    assert result["total_retracted"] == 1
    assert result["total_left_narrative"] == 0


# ---------------------------------------------------------------------------
# apply: mappable edge migrated and retracted exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_migrates_and_retracts_mappable_edge() -> None:
    """Applying the backfill asserts the edge and retracts the source."""
    fact_id = uuid.uuid4()
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": fact_id,
        "entity_id": _ENTITY_ID,
        "object_entity_id": _OBJECT_ID,
        "predicate": "works_at",
        "confidence": 0.8,
        "last_confirmed_at": None,
        "source_butler": "relationship",
    }[key]

    pool = _make_pool([row])
    assert_fact = AsyncMock(return_value=_make_assert_fact_result("inserted"))

    result = await run(pool, dry_run=False, _assert_fact=assert_fact)

    # assert_fact called once with the canonical predicate.
    assert_fact.assert_called_once()
    call_args = assert_fact.call_args
    assert call_args.args[2] == "works-at"  # canonical predicate
    assert call_args.kwargs["object_kind"] == "entity"
    assert call_args.kwargs["src"] == "backfill"

    # Retraction UPDATE called once.
    pool.execute.assert_called_once()
    update_sql = pool.execute.call_args.args[0]
    assert "validity = 'retracted'" in update_sql
    assert pool.execute.call_args.args[1] == fact_id

    assert result["total_migrated"] == 1
    assert result["total_retracted"] == 1
    assert result["total_errors"] == 0


# ---------------------------------------------------------------------------
# idempotency: re-run after apply is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotency_after_apply() -> None:
    """After a successful apply, re-running finds no active edge rows → no-op."""
    pool = AsyncMock()
    # First call: registered predicates; second call: no active edge rows (all retracted).
    registry_rows = [_make_predicate_row(p) for p in _REGISTRY]
    pool.fetch = AsyncMock(side_effect=[registry_rows, []])
    pool.execute = AsyncMock()
    pool.close = AsyncMock()

    assert_fact = AsyncMock(return_value=_make_assert_fact_result())

    result = await run(pool, dry_run=False, _assert_fact=assert_fact)

    assert_fact.assert_not_called()
    pool.execute.assert_not_called()
    assert result["total_processed"] == 0
    assert result["total_migrated"] == 0
    assert result["total_retracted"] == 0


# ---------------------------------------------------------------------------
# narrative edge: left in place
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrative_edge_left_in_place() -> None:
    """A predicate absent from the registry is treated as narrative and not migrated."""
    rows = [_make_fact_row("planned_dinner_with")]
    pool = _make_pool(rows)
    assert_fact = AsyncMock(return_value=_make_assert_fact_result())

    result = await run(pool, dry_run=False, _assert_fact=assert_fact)

    assert_fact.assert_not_called()
    pool.execute.assert_not_called()
    assert result["total_migrated"] == 0
    assert result["total_left_narrative"] == 1
    assert result["per_predicate"]["planned_dinner_with"]["mappable"] is False
    assert result["per_predicate"]["planned_dinner_with"]["left_narrative"] == 1


# ---------------------------------------------------------------------------
# mixed batch: some mappable, some narrative
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_batch_migrates_only_mappable() -> None:
    """A mixed batch migrates registry predicates and leaves narrative ones."""
    rows = [
        _make_fact_row("works_at"),
        _make_fact_row("planned_dinner_with"),
        _make_fact_row("member_of"),
    ]
    pool = _make_pool(rows)
    assert_fact = AsyncMock(return_value=_make_assert_fact_result("inserted"))

    result = await run(pool, dry_run=False, _assert_fact=assert_fact)

    assert assert_fact.call_count == 2  # works_at + member_of
    assert result["total_migrated"] == 2
    assert result["total_left_narrative"] == 1
    per = result["per_predicate"]
    assert per["works_at"]["migrated"] == 1
    assert per["member_of"]["migrated"] == 1
    assert per["planned_dinner_with"]["left_narrative"] == 1


# ---------------------------------------------------------------------------
# owner carve-out (pending_approval) — source row must NOT be retracted
# (regression guard for bu-2ezvz)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_pending_approval_does_not_retract_source() -> None:
    """When assert_fact returns pending_approval, the source row MUST be left active.

    RFC 0017 §2.3 owner carve-out: the write is parked for human approval;
    entity_facts is NOT written yet.  Retracting the source before approval
    means the edge is lost if the owner rejects or the pending_action expires.
    """
    fact_id = uuid.uuid4()
    action_id = uuid.uuid4()

    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": fact_id,
        "entity_id": _ENTITY_ID,
        "object_entity_id": _OBJECT_ID,
        "predicate": "works_at",
        "confidence": 0.9,
        "last_confirmed_at": None,
        "source_butler": "relationship",
    }[key]

    pool = _make_pool([row])

    # Simulate the central writer returning pending_approval (owner carve-out).
    pending_result = MagicMock()
    pending_result.outcome = "pending_approval"  # StrEnum equality check in backfill
    pending_result.action_id = action_id
    assert_fact = AsyncMock(return_value=pending_result)

    result = await run(pool, dry_run=False, _assert_fact=assert_fact)

    # assert_fact was called (attempted the write).
    assert_fact.assert_called_once()

    # Source row must NOT have been retracted.
    pool.execute.assert_not_called()

    # Parked counter increments; migrated and retracted stay zero.
    assert result["total_parked"] == 1
    assert result["total_migrated"] == 0
    assert result["total_retracted"] == 0
    assert result["total_errors"] == 0
    assert result["per_predicate"]["works_at"]["parked"] == 1
    assert result["per_predicate"]["works_at"]["migrated"] == 0
    assert result["per_predicate"]["works_at"]["retracted"] == 0


@pytest.mark.asyncio
async def test_apply_active_outcome_retracts_source() -> None:
    """Non-pending_approval outcomes (inserted/superseded/unchanged) DO retract.

    Regression guard: the parked check must not accidentally suppress retractions
    for ordinary successful writes.
    """
    fact_id = uuid.uuid4()

    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": fact_id,
        "entity_id": _ENTITY_ID,
        "object_entity_id": _OBJECT_ID,
        "predicate": "works_at",
        "confidence": 0.8,
        "last_confirmed_at": None,
        "source_butler": "relationship",
    }[key]

    pool = _make_pool([row])

    for active_outcome in ("inserted", "superseded", "unchanged"):
        # Reset call counts between sub-cases.
        pool.execute.reset_mock()

        active_result = MagicMock()
        active_result.outcome = active_outcome
        assert_fact = AsyncMock(return_value=active_result)

        result = await run(pool, dry_run=False, _assert_fact=assert_fact)

        # Source row MUST be retracted for every non-pending outcome.
        pool.execute.assert_called_once()
        assert "validity = 'retracted'" in pool.execute.call_args.args[0]
        assert pool.execute.call_args.args[1] == fact_id

        assert result["total_migrated"] == 1
        assert result["total_retracted"] == 1
        assert result["total_parked"] == 0, f"parked should be 0 for outcome={active_outcome!r}"

        # Re-prime pool.fetch for the next iteration.
        pool.fetch = AsyncMock(side_effect=[[_make_predicate_row(p) for p in _REGISTRY], [row]])


# ---------------------------------------------------------------------------
# Loader: sys.modules registration before exec_module (regression for bu-hzz09)
# ---------------------------------------------------------------------------


def test_load_assert_fact_fn_registers_module_before_exec() -> None:
    """_load_assert_fact_fn() must succeed when the roster file contains @dataclass.

    Regression guard for bu-hzz09: without ``sys.modules[spec.name] = mod``
    BEFORE ``exec_module``, Python's @dataclass decorator cannot resolve KW_ONLY
    via ``sys.modules.get(cls.__module__).__dict__`` and raises
    ``AttributeError: 'NoneType' object has no attribute '__dict__'``.

    All other tests in this file inject ``_assert_fact``, so the loader is never
    exercised there.  This test calls the loader directly.
    """
    _MODULE_SENTINEL = "_roster_assert_fact"
    # Remove any prior cached load so the loader runs from scratch.
    prior_cached = _mod._cached_assert_fact
    prior_module = sys.modules.pop(_MODULE_SENTINEL, None)
    _mod._cached_assert_fact = None
    try:
        fn = _mod._load_assert_fact_fn()
        assert callable(fn), (
            "_load_assert_fact_fn() must return the relationship_assert_fact callable"
        )
        # The module must now be registered.
        assert _MODULE_SENTINEL in sys.modules
    finally:
        # Restore prior state so module-level cache is not polluted across tests.
        _mod._cached_assert_fact = prior_cached
        if prior_module is not None:
            sys.modules[_MODULE_SENTINEL] = prior_module
        else:
            sys.modules.pop(_MODULE_SENTINEL, None)
