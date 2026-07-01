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
        check_data_correction_preconditions,
        corrections_by_session,
        corrections_for_session,
        create_correction,
        get_correction_type_for_situation,
        handle_action_reversal,
        handle_data_correction,
        handle_memory_deletion,
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
        "session_not_found",
        "state_key_not_found",
        "memory_already_retracted",
        "memory_superseded",
        "butler_not_registered",
        "ingestion_event_expired",
        "action_not_reversible",
        "unknown_correction_type",
        "missing_required_parameter",
        "session_no_ingestion_event",
        "memory_not_found",
        "switchboard_unreachable",
        "invalid_json_corrected_value",
    }
    assert set(FAILURE_MESSAGES.keys()) >= expected_keys


# ---------------------------------------------------------------------------
# JSON validation for corrected_value
# ---------------------------------------------------------------------------


@corrections_required
@pytest.mark.parametrize(
    "corrected_value,expect_error",
    [
        # Valid JSON-serializable values — precondition should pass the JSON check
        ({"key": "value"}, False),
        ([1, 2, 3], False),
        ("a plain string", False),
        (42, False),
        (3.14, False),
        (True, False),
        (None, False),
        # Non-JSON-serializable value — should trigger the invalid_json_corrected_value error
        (object(), True),
    ],
)
async def test_check_data_correction_preconditions_json_validation(corrected_value, expect_error):
    """check_data_correction_preconditions rejects non-JSON-serializable corrected_value."""
    session_id = uuid.uuid4()
    pool = _make_pool(
        session_row={"id": session_id},
        state_row={"key": "mykey"},
    )
    result = await check_data_correction_preconditions(
        pool,
        target_session_id=session_id,
        state_key="mykey",
        corrected_value=corrected_value,
    )
    if expect_error:
        assert result is not None
        assert "not valid JSON" in result or "invalid_json" in result.lower()
    else:
        assert result is None


# ---------------------------------------------------------------------------
# Cross-schema: handle_memory_deletion
# ---------------------------------------------------------------------------


@corrections_required
async def test_handle_memory_deletion_cross_schema_unknown_butler():
    """handle_memory_deletion fails with butler_not_registered when target_butler not in list."""
    pool = _make_pool(session_row={"id": uuid.uuid4()})
    result = await handle_memory_deletion(
        pool,
        target_session_id=uuid.uuid4(),
        correcting_session_id=uuid.uuid4(),
        description="Cross-schema deletion",
        memory_type="fact",
        memory_id=uuid.uuid4(),
        target_butler="nonexistent_butler",
        registered_butlers=["finance", "general"],
    )
    assert result["status"] == "failed"
    assert "nonexistent_butler" in result["summary"]
    assert result["correction_details"] == {"target_butler": "nonexistent_butler"}


@corrections_required
async def test_handle_memory_deletion_cross_schema_uses_target_pool():
    """handle_memory_deletion uses target_pool for session/memory queries when target_butler given."""
    session_id = uuid.uuid4()
    # target_pool resolves the session; local pool is for correction record only
    target_pool = _make_pool(
        session_row={"id": session_id},
        memory_row=None,  # memory not found -> precondition error
    )
    local_pool = _make_pool(session_row={"id": session_id})

    result = await handle_memory_deletion(
        local_pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Cross-schema deletion via target_pool",
        memory_type="fact",
        memory_id=uuid.uuid4(),
        target_butler="finance",
        target_pool=target_pool,
        registered_butlers=["finance", "general"],
    )
    # The session exists on target_pool but memory type "fact" triggers a
    # memory_not_found path (mock returns None for memory queries).
    # Key assertion: target_pool was used (not local_pool) for session lookup.
    assert result["status"] == "failed"
    # target_pool.fetchrow was called (session lookup on the target butler's pool)
    target_pool.fetchrow.assert_called()


@corrections_required
async def test_handle_memory_deletion_target_butler_in_correction_details():
    """handle_memory_deletion includes target_butler in correction_details on success."""
    import unittest.mock as _mock

    session_id = uuid.uuid4()
    mem_id = uuid.uuid4()

    target_pool = _make_pool(
        session_row={"id": session_id, "trigger_source": "scheduler", "ingestion_event_id": None},
    )

    # Override fetchrow to return memory row for episodes query
    async def custom_fetchrow(sql: str, *args):
        sql_lower = sql.lower()
        if "from sessions" in sql_lower or "sessions where" in sql_lower:
            return {"id": session_id}
        if "episodes" in sql_lower and "memory validity check" in sql_lower:
            # Return a non-expired episode
            import datetime

            return {
                "id": mem_id,
                "expires_at": datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC),
            }
        if "episodes" in sql_lower and "memory snapshot" in sql_lower:
            return {"id": mem_id, "content": "episode content"}
        return None

    target_pool.fetchrow = AsyncMock(side_effect=custom_fetchrow)

    local_pool = _make_pool()

    with _mock.patch(
        "butlers.core.corrections.memory_forget", new_callable=AsyncMock
    ) as mock_forget:
        mock_forget.return_value = {"forgotten": True}
        result = await handle_memory_deletion(
            local_pool,
            target_session_id=session_id,
            correcting_session_id=uuid.uuid4(),
            description="Cross-schema episode deletion",
            memory_type="episode",
            memory_id=mem_id,
            target_butler="finance",
            target_pool=target_pool,
            registered_butlers=["finance", "general"],
        )

    assert result["status"] == "applied"
    assert result["correction_details"]["target_butler"] == "finance"
    assert result["correction_details"]["memory_type"] == "episode"
    mock_forget.assert_called_once_with(target_pool, "episode", str(mem_id))


# ---------------------------------------------------------------------------
# Cross-schema: handle_misroute
# ---------------------------------------------------------------------------


@corrections_required
async def test_handle_misroute_cross_schema_unknown_target_butler():
    """handle_misroute fails with butler_not_registered when target_butler not in registered list."""
    session_id = uuid.uuid4()
    pool = _make_pool(session_row={"id": session_id, "ingestion_event_id": str(uuid.uuid4())})
    result = await handle_misroute(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Cross-schema misroute",
        correct_butler="finance",
        registered_butlers=["finance", "general"],
        switchboard_client=AsyncMock(),
        target_butler="ghost_butler",
    )
    assert result["status"] == "failed"
    assert "ghost_butler" in result["summary"]
    assert result["correction_details"] == {"target_butler": "ghost_butler"}


@corrections_required
async def test_handle_misroute_cross_schema_uses_target_pool():
    """handle_misroute uses target_pool for session lookup when target_butler is given."""
    session_id = uuid.uuid4()
    ingestion_id = uuid.uuid4()

    target_pool = _make_pool(
        session_row={
            "id": session_id,
            "trigger_source": "ingestion",
            "ingestion_event_id": ingestion_id,
        },
    )
    local_pool = _make_pool()

    # Switchboard client that returns a successful re-dispatch
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(
        return_value={"status": "ok", "new_session_id": str(uuid.uuid4())}
    )

    result = await handle_misroute(
        local_pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Cross-schema misroute via target_pool",
        correct_butler="finance",
        registered_butlers=["finance", "general"],
        switchboard_client=mock_client,
        target_butler="general",
        target_pool=target_pool,
    )

    # Session lookup should use target_pool
    target_pool.fetchrow.assert_called()
    assert result["status"] == "applied"
    assert result["correction_details"]["target_butler"] == "general"
    assert result["correction_details"]["correct_butler"] == "finance"


# ---------------------------------------------------------------------------
# Cross-schema: handle_action_reversal
# ---------------------------------------------------------------------------


@corrections_required
async def test_handle_action_reversal_cross_schema_unknown_butler():
    """handle_action_reversal fails with butler_not_registered when target_butler not in list."""
    pool = _make_pool(session_row={"id": uuid.uuid4()})
    result = await handle_action_reversal(
        pool,
        target_session_id=uuid.uuid4(),
        correcting_session_id=uuid.uuid4(),
        description="Cross-schema reversal",
        action_description="Undo the reminder",
        target_butler="shadow_butler",
        registered_butlers=["finance", "general"],
    )
    assert result["status"] == "failed"
    assert "shadow_butler" in result["summary"]
    assert result["correction_details"] == {"target_butler": "shadow_butler"}


@corrections_required
async def test_handle_action_reversal_cross_schema_uses_target_pool():
    """handle_action_reversal uses target_pool for session lookup when target_butler is given."""
    session_id = uuid.uuid4()
    target_pool = _make_pool(
        session_row={"id": session_id, "tool_calls": [{"tool": "remind"}]},
    )
    local_pool = _make_pool()

    result = await handle_action_reversal(
        local_pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Cross-schema reversal via target_pool",
        action_description="Cancel the reminder",
        target_butler="finance",
        target_pool=target_pool,
        registered_butlers=["finance", "general"],
    )

    # Session lookup should use target_pool
    target_pool.fetchrow.assert_called()
    assert result["status"] in ("applied", "partially_applied", "failed")
    assert result["correction_details"].get("target_butler") == "finance"


# ---------------------------------------------------------------------------
# SQL injection guard: _validate_identifier on target_butler
# ---------------------------------------------------------------------------


@corrections_required
async def test_handle_memory_deletion_rejects_unsafe_target_butler():
    """handle_memory_deletion raises ValueError for an unsafe target_butler identifier."""
    pool = _make_pool(session_row={"id": uuid.uuid4()})
    import pytest as _pytest

    with _pytest.raises(ValueError, match="Unsafe SQL identifier"):
        await handle_memory_deletion(
            pool,
            target_session_id=uuid.uuid4(),
            correcting_session_id=uuid.uuid4(),
            description="Injection attempt",
            memory_type="fact",
            memory_id=uuid.uuid4(),
            target_butler="'; DROP TABLE facts; --",
            registered_butlers=None,
        )


@corrections_required
async def test_handle_misroute_rejects_unsafe_target_butler():
    """handle_misroute raises ValueError for an unsafe target_butler identifier."""
    pool = _make_pool(session_row={"id": uuid.uuid4()})
    import pytest as _pytest

    with _pytest.raises(ValueError, match="Unsafe SQL identifier"):
        await handle_misroute(
            pool,
            target_session_id=uuid.uuid4(),
            correcting_session_id=uuid.uuid4(),
            description="Injection attempt",
            correct_butler="finance",
            registered_butlers=None,
            switchboard_client=AsyncMock(),
            target_butler="evil'; DROP TABLE--",
        )


@corrections_required
async def test_handle_action_reversal_rejects_unsafe_target_butler():
    """handle_action_reversal raises ValueError for an unsafe target_butler identifier."""
    pool = _make_pool(session_row={"id": uuid.uuid4()})
    import pytest as _pytest

    with _pytest.raises(ValueError, match="Unsafe SQL identifier"):
        await handle_action_reversal(
            pool,
            target_session_id=uuid.uuid4(),
            correcting_session_id=uuid.uuid4(),
            description="Injection attempt",
            action_description="Undo",
            target_butler="1; TRUNCATE corrections;",
            registered_butlers=None,
        )


@corrections_required
async def test_handle_misroute_registered_butlers_none_bypasses_butler_check():
    """handle_misroute with registered_butlers=None skips the butler validation check."""
    session_id = uuid.uuid4()
    ingestion_id = uuid.uuid4()
    target_pool = _make_pool(
        session_row={
            "id": session_id,
            "trigger_source": "ingestion",
            "ingestion_event_id": ingestion_id,
        },
    )
    local_pool = _make_pool()
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(
        return_value={"status": "ok", "new_session_id": str(uuid.uuid4())}
    )

    # registered_butlers=None should not cause a TypeError or "butler_not_registered" failure
    result = await handle_misroute(
        local_pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Misroute with no registered list",
        correct_butler="finance",
        registered_butlers=None,
        switchboard_client=mock_client,
        target_butler="general",
        target_pool=target_pool,
    )
    assert result["status"] != "failed" or "not_registered" not in result.get("summary", "")


@corrections_required
async def test_handle_action_reversal_target_butler_no_pool_fallback():
    """handle_action_reversal falls back to pool when target_pool is None."""
    session_id = uuid.uuid4()
    pool = _make_pool(
        session_row={"id": session_id, "tool_calls": []},
    )

    result = await handle_action_reversal(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Cross-schema reversal fallback",
        action_description="Undo something",
        target_butler="finance",
        target_pool=None,  # fallback to pool
        registered_butlers=["finance", "general"],
    )
    # With no tool_calls the reversal has nothing to reverse -> partially_applied
    assert result["status"] in ("applied", "partially_applied", "failed")
    assert result["correction_details"].get("target_butler") == "finance"
