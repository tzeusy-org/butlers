"""Tests for the empty-value skip-and-warn guard in run_contact_info_reconciler().

Verifies that rows with empty or whitespace-only ci_value are skipped before
encode_handle_object() is called, so no degenerate triple like 'telegram:' can
be stored.

Belt-and-suspenders guard added in bead bu-36c3w.
"""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import datetime
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Module loading (same pattern as test_interaction_sync.py)
# ---------------------------------------------------------------------------

_MODULE_KEY = "butlers.jobs._roster.relationship_jobs"


def _get_rjobs() -> ModuleType:
    mod = sys.modules.get(_MODULE_KEY)
    if mod is None:
        from butlers.jobs._roster_loader import load_roster_jobs

        mod = load_roster_jobs("relationship")
    return mod


# ---------------------------------------------------------------------------
# Row mock helpers
# ---------------------------------------------------------------------------

_ENTITY_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_CONTACT_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _ci_row(
    *,
    ci_type: str = "telegram_user_id",
    ci_value: str = "",
    entity_id: uuid.UUID | None = _ENTITY_ID,
    contact_id: uuid.UUID | None = _CONTACT_ID,
    ci_id: uuid.UUID | None = None,
    secured: bool = False,
    is_primary: bool = False,
    ci_created_at: datetime | None = None,
) -> MagicMock:
    data: dict[str, Any] = {
        "ci_id": ci_id or uuid.uuid4(),
        "contact_id": contact_id,
        "ci_type": ci_type,
        "ci_value": ci_value,
        "entity_id": entity_id,
        "secured": secured,
        "is_primary": is_primary,
        "ci_created_at": ci_created_at,
    }
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    return row


# ---------------------------------------------------------------------------
# Pool builder
# ---------------------------------------------------------------------------

# Predicates present in the mock registry (matching _CI_TYPE_TO_PREDICATE values)
_REGISTERED_PREDICATES = frozenset({"has-email", "has-phone", "has-website", "has-handle"})


def _make_pool(*, ci_rows: list[MagicMock]) -> MagicMock:
    """Build a pool mock for run_contact_info_reconciler().

    pool.fetch() is called twice inside the function:
      1. _registered_contact_info_predicates → returns registry rows
      2. main sweep query → returns ci_rows

    state_get / state_set are called via butlers.core.state, not via pool
    (the job uses state_get/state_set helpers which we patch separately).
    """
    pool = MagicMock()

    # Registry rows — each must support row["predicate"]
    registry_rows: list[MagicMock] = []
    for pred in _REGISTERED_PREDICATES:
        r = MagicMock()
        r.__getitem__ = lambda self, k, _p=pred: _p if k == "predicate" else None
        registry_rows.append(r)

    fetch_call_count = [0]

    async def _fetch(sql: str, *args: Any) -> list[Any]:
        n = fetch_call_count[0]
        fetch_call_count[0] += 1
        if n == 0:
            return registry_rows
        return ci_rows

    pool.fetch = AsyncMock(side_effect=_fetch)
    # fetchval is used by the reconciler's retirement guard to check whether
    # public.contact_info still exists (bu-e2ja9). These tests exercise the sweep,
    # so the table must read as present.
    pool.fetchval = AsyncMock(return_value="public.contact_info")
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# Mock assert_fact module
# ---------------------------------------------------------------------------


class _OutcomeValue:
    """Stub that behaves like a string (equality, membership) and has .value."""

    def __init__(self, name: str) -> None:
        self.value = name

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _OutcomeValue):
            return self.value == other.value
        return self.value == other

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f"_OutcomeValue({self.value!r})"


class _AssertOutcome:
    inserted = _OutcomeValue("inserted")
    superseded = _OutcomeValue("superseded")
    unchanged = _OutcomeValue("unchanged")
    pending_approval = _OutcomeValue("pending_approval")


class _AssertResult:
    def __init__(self, outcome: str):
        self.outcome = _OutcomeValue(outcome)


# ---------------------------------------------------------------------------
# Core runner: execute run_contact_info_reconciler with stubs injected
# ---------------------------------------------------------------------------


async def _run_reconciler(
    ci_rows: list[MagicMock],
    *,
    assert_outcome: str = "inserted",
) -> dict[str, Any]:
    """Run run_contact_info_reconciler with all external deps stubbed."""
    rjobs = _get_rjobs()
    pool = _make_pool(ci_rows=ci_rows)

    # Stub the assert_fact module imported inside the function.
    assert_mod = MagicMock()
    assert_mod.AssertOutcome = _AssertOutcome
    assert_mod.relationship_assert_fact = AsyncMock(
        return_value=_AssertResult(outcome=assert_outcome)
    )

    # Stub encode_handle_object: use the real implementation so encoding
    # behaviour stays correct, but load it via butlers.tools.relationship
    # (the registered module path) rather than the roster path.
    real_encode = None
    helpers_mod = sys.modules.get("butlers.tools.relationship")
    if helpers_mod is not None:
        real_encode = getattr(helpers_mod, "encode_handle_object", None)
        if real_encode is None:
            # Tools package: look for the submodule
            helpers_sub = sys.modules.get("butlers.tools.relationship._ef_channel_helpers")
            if helpers_sub is not None:
                real_encode = helpers_sub.encode_handle_object
    if real_encode is None:
        # Fallback: inline the real logic so tests don't depend on import order
        def real_encode(ci_type: str, value: str) -> str:  # type: ignore[misc]
            telegram_types = {"telegram", "telegram_user_id", "telegram_username"}
            prefix = "telegram:"
            if ci_type in telegram_types:
                if not value.startswith(prefix):
                    return prefix + value
            return value

    helpers_stub = MagicMock()
    helpers_stub.encode_handle_object = real_encode

    # Patch the two lazy-import paths that run_contact_info_reconciler uses.
    assert_key = "butlers.tools.relationship.relationship_assert_fact"
    helpers_key = "butlers.tools.relationship._ef_channel_helpers"
    saved_assert = sys.modules.get(assert_key)
    saved_helpers = sys.modules.get(helpers_key)
    sys.modules[assert_key] = assert_mod
    sys.modules[helpers_key] = helpers_stub

    # Patch state_get/state_set so checkpoint I/O is a no-op.
    import butlers.core.state as _state_mod

    orig_state_get = _state_mod.state_get
    orig_state_set = _state_mod.state_set
    _state_mod.state_get = AsyncMock(return_value=None)  # type: ignore[assignment]
    _state_mod.state_set = AsyncMock()  # type: ignore[assignment]

    try:
        stats = await rjobs.run_contact_info_reconciler(pool)
    finally:
        # Restore sys.modules
        if saved_assert is None:
            sys.modules.pop(assert_key, None)
        else:
            sys.modules[assert_key] = saved_assert
        if saved_helpers is None:
            sys.modules.pop(helpers_key, None)
        else:
            sys.modules[helpers_key] = saved_helpers
        # Restore state helpers
        _state_mod.state_get = orig_state_get  # type: ignore[assignment]
        _state_mod.state_set = orig_state_set  # type: ignore[assignment]

    return stats


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReconcilerEmptyValueGuard:
    """Empty/whitespace ci_value rows are skipped before encode_handle_object."""

    @pytest.mark.asyncio
    async def test_empty_string_telegram_skipped(self) -> None:
        """Empty ci_value for telegram_user_id must be skipped and counted."""
        row = _ci_row(ci_type="telegram_user_id", ci_value="")
        stats = await _run_reconciler([row])

        assert stats["rows_skipped_empty_value"] == 1
        assert stats["rows_reconciled"] == 0

    @pytest.mark.asyncio
    async def test_whitespace_only_telegram_skipped(self) -> None:
        """Whitespace-only ci_value is treated as empty and skipped."""
        row = _ci_row(ci_type="telegram_user_id", ci_value="   ")
        stats = await _run_reconciler([row])

        assert stats["rows_skipped_empty_value"] == 1
        assert stats["rows_reconciled"] == 0

    @pytest.mark.asyncio
    async def test_empty_string_non_telegram_skipped(self) -> None:
        """Empty ci_value for non-telegram types (e.g. email) is also skipped."""
        row = _ci_row(ci_type="email", ci_value="")
        stats = await _run_reconciler([row])

        assert stats["rows_skipped_empty_value"] == 1
        assert stats["rows_reconciled"] == 0

    @pytest.mark.asyncio
    async def test_nonempty_value_passes_guard(self) -> None:
        """A valid non-empty ci_value must not be counted as empty."""
        row = _ci_row(ci_type="telegram_user_id", ci_value="86807245")
        stats = await _run_reconciler([row], assert_outcome="inserted")

        assert stats["rows_skipped_empty_value"] == 0

    @pytest.mark.asyncio
    async def test_empty_value_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """Skipping an empty value emits a WARNING log."""
        row = _ci_row(ci_type="telegram_user_id", ci_value="")

        with caplog.at_level(logging.WARNING):
            await _run_reconciler([row])

        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("empty" in m.lower() or "degenerate" in m.lower() for m in warning_msgs)

    @pytest.mark.asyncio
    async def test_empty_value_stat_key_present(self) -> None:
        """rows_skipped_empty_value must be present in the returned stats dict."""
        row = _ci_row(ci_type="telegram_user_id", ci_value="86807245")
        stats = await _run_reconciler([row], assert_outcome="inserted")

        assert "rows_skipped_empty_value" in stats

    @pytest.mark.asyncio
    async def test_multiple_empty_rows_all_counted(self) -> None:
        """Each empty-value row increments the counter independently."""
        rows = [
            _ci_row(ci_type="telegram_user_id", ci_value=""),
            _ci_row(ci_type="telegram_username", ci_value="  "),
            _ci_row(ci_type="email", ci_value=""),
        ]
        stats = await _run_reconciler(rows)

        assert stats["rows_skipped_empty_value"] == 3
        assert stats["rows_reconciled"] == 0
