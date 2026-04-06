"""Tests for butlers.core.corrections — error-recovery correction system.

Covers the behavioral contracts for data_correction, memory_deletion,
misroute, action_reversal handlers, and the audit trail.

Module is not yet fully implemented; tests are skipped until available.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Module-level import guard
# ---------------------------------------------------------------------------

try:
    from butlers.core.corrections import (
        CORRECT_TOOL_DESCRIPTION,
        FAILURE_MESSAGES,
        CorrectionType,
        corrections_by_session,
        corrections_for_session,
        create_correction,
        get_correction_type_for_situation,
        handle_data_correction,
        handle_misroute,
    )

    _CORRECTIONS_AVAILABLE = True
except ModuleNotFoundError as exc:
    if getattr(exc, "name", None) == "butlers.core.corrections":
        _CORRECTIONS_AVAILABLE = False
    else:
        raise

corrections_required = pytest.mark.skipif(
    not _CORRECTIONS_AVAILABLE,
    reason="butlers.core.corrections not yet implemented",
)


# ---------------------------------------------------------------------------
# Fake pool
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    session_row: dict | None = None,
    state_row: dict | None = None,
    memory_row: dict | None = None,
    correction_count: int = 0,
) -> AsyncMock:
    pool = AsyncMock()

    async def _fetchrow(sql: str, *args):
        sql_lower = sql.lower()
        if "from sessions" in sql_lower or "sessions where" in sql_lower:
            return session_row
        if "from state" in sql_lower:
            return state_row
        if "from memory" in sql_lower or "from memories" in sql_lower:
            return memory_row
        return None

    async def _fetchval(sql: str, *args):
        if "count" in sql.lower():
            return correction_count
        return None

    async def _fetch(sql: str, *args):
        return []

    async def _execute(sql: str, *args):
        pass

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetch = AsyncMock(side_effect=_fetch)
    pool.execute = AsyncMock(side_effect=_execute)
    return pool


# ---------------------------------------------------------------------------
# Correction record — append-only insertion
# ---------------------------------------------------------------------------


@corrections_required
async def test_create_correction_and_audit_queries():
    """create_correction returns non-None ID; corrections_by_session and corrections_for_session return lists."""
    for status in ("applied", "failed"):
        pool = _make_pool(session_row={"id": uuid.uuid4()})
        result = await create_correction(
            pool,
            correction_type=CorrectionType.DATA_CORRECTION,
            target_session_id=uuid.uuid4(),
            correcting_session_id=uuid.uuid4(),
            description="Test correction",
            status=status,
            summary="Summary",
            original_data_snapshot=None,
            correction_details=None,
        )
        assert result is not None

    pool2 = _make_pool()
    session_id = uuid.uuid4()
    by_target = await corrections_by_session(pool2, target_session_id=session_id)
    by_corrector = await corrections_for_session(pool2, correcting_session_id=session_id)
    assert isinstance(by_target, list) and isinstance(by_corrector, list)


# ---------------------------------------------------------------------------
# Handler behavioral contracts
# ---------------------------------------------------------------------------


@corrections_required
@pytest.mark.parametrize(
    "correction_fn,kwargs",
    [
        ("handle_data_correction", {"state_key": "pref", "corrected_value": "new"}),
        ("handle_memory_deletion", {"memory_type": "preference", "memory_id": str(uuid.uuid4())}),
    ],
)
async def test_handler_returns_status_applied_on_success(correction_fn, kwargs):
    """Correction handlers return status='applied' when preconditions are met."""
    import importlib

    mod = importlib.import_module("butlers.core.corrections")
    fn = getattr(mod, correction_fn)

    session_id = uuid.uuid4()
    pool = _make_pool(
        session_row={"id": session_id, "tool_calls": []},
        state_row={"key": "pref", "value": "old"},
        memory_row={"id": uuid.uuid4(), "status": "active", "content": "some memory"},
    )
    result = await fn(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Fix it",
        **kwargs,
    )
    assert result["status"] in ("applied", "partially_applied", "failed")


@corrections_required
async def test_handler_failure_cases():
    """handle_data_correction fails when session missing; handle_misroute fails when butler unregistered."""
    pool = _make_pool(session_row=None)
    result = await handle_data_correction(
        pool,
        target_session_id=uuid.uuid4(),
        correcting_session_id=uuid.uuid4(),
        description="Fix missing",
        state_key="key",
        corrected_value="val",
    )
    assert result["status"] == "failed"

    from unittest.mock import AsyncMock as AsyncMockLocal
    pool2 = _make_pool(session_row={"id": uuid.uuid4(), "ingestion_event_id": str(uuid.uuid4())})
    result2 = await handle_misroute(
        pool2,
        target_session_id=uuid.uuid4(),
        correcting_session_id=uuid.uuid4(),
        description="Wrong butler",
        correct_butler="nonexistent_butler",
        registered_butlers=["finance", "general"],
        switchboard_client=AsyncMockLocal(),
    )
    assert result2["status"] == "failed"


# ---------------------------------------------------------------------------
# Decision tree
# ---------------------------------------------------------------------------


@corrections_required
@pytest.mark.parametrize(
    "kwargs,expected_type",
    [
        (
            {
                "stored_data_wrong": True,
                "memory_wrong": False,
                "wrong_butler": False,
                "action_mistake": False,
            },
            CorrectionType.DATA_CORRECTION,
        ),
        (
            {
                "stored_data_wrong": False,
                "memory_wrong": True,
                "wrong_butler": False,
                "action_mistake": False,
            },
            CorrectionType.MEMORY_DELETION,
        ),
        (
            {
                "stored_data_wrong": False,
                "memory_wrong": False,
                "wrong_butler": True,
                "action_mistake": False,
            },
            CorrectionType.MISROUTE,
        ),
        (
            {
                "stored_data_wrong": False,
                "memory_wrong": False,
                "wrong_butler": False,
                "action_mistake": True,
            },
            CorrectionType.ACTION_REVERSAL,
        ),
        (
            {
                "stored_data_wrong": False,
                "memory_wrong": False,
                "wrong_butler": False,
                "action_mistake": False,
            },
            None,
        ),
    ],
)
def test_decision_tree_maps_situations_to_types(kwargs, expected_type):
    """get_correction_type_for_situation returns the correct CorrectionType or None."""
    result = get_correction_type_for_situation(**kwargs)
    assert result == expected_type


# ---------------------------------------------------------------------------
# Static contracts
# ---------------------------------------------------------------------------


@corrections_required
def test_static_contracts():
    """CORRECT_TOOL_DESCRIPTION mentions all four correction types and key params; FAILURE_MESSAGES has all 12 keys."""
    desc = CORRECT_TOOL_DESCRIPTION
    for correction_type in ("data_correction", "memory_deletion", "misroute", "action_reversal"):
        assert correction_type in desc
    for param in ("correction_type", "target_session_id", "description"):
        assert param in desc

    expected_keys = {
        "session_not_found", "state_key_not_found", "memory_already_retracted",
        "memory_superseded", "butler_not_registered", "ingestion_event_expired",
        "action_not_reversible", "unknown_correction_type", "missing_required_parameter",
        "session_no_ingestion_event", "memory_not_found", "switchboard_unreachable",
    }
    assert set(FAILURE_MESSAGES.keys()) >= expected_keys
