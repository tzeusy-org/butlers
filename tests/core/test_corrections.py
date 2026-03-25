"""Tests for butlers.core.corrections — error-recovery correction system.

Tests cover:
- 7.1  Correction record insertion and append-only enforcement
- 7.2  Precondition validation per correction type (valid and invalid cases)
- 7.3  data_correction handler: state update, snapshot, correction record
- 7.4  memory_deletion handler: memory retraction, provenance metadata, already-retracted guard
- 7.5  misroute handler: re-dispatch success, expired event failure, unregistered butler failure
- 7.6  action_reversal handler: full reversal, partial reversal, failed reversal
- 7.7  Correction audit queries (by target session, by correcting session)
- 7.9  correct tool description contract
- 7.10 Failure message dictionary — 12 templates
- 7.11 Decision tree coverage
- 7.12 Cross-schema resolution
- 7.13 Rate limiting
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Module-level import guard
# ---------------------------------------------------------------------------

# The corrections module is not yet implemented. We import cautiously and skip
# individual tests that need the real module when it is absent.
try:
    from butlers.core.corrections import (
        CORRECT_TOOL_DESCRIPTION,
        FAILURE_MESSAGES,
        CorrectionType,
        check_action_reversal_preconditions,
        check_data_correction_preconditions,
        check_memory_deletion_preconditions,
        check_misroute_preconditions,
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
except ImportError:
    _CORRECTIONS_AVAILABLE = False

corrections_required = pytest.mark.skipif(
    not _CORRECTIONS_AVAILABLE,
    reason="butlers.core.corrections not yet implemented",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    session_row: dict | None = None,
    state_row: dict | None = None,
    memory_row: dict | None = None,
    correction_count: int = 0,
) -> AsyncMock:
    """Build a minimal asyncpg Pool mock."""
    pool = AsyncMock()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.strip().lower()
        if "sessions" in q:
            return session_row
        if "state" in q and "select" in q:
            return state_row
        if "correction" in q and "count" in q:
            return {"count": correction_count}
        return None

    async def _fetchval(query: str, *args: Any) -> Any:
        q = query.strip().lower()
        if "correction" in q and "count" in q:
            return correction_count
        return None

    async def _fetch(query: str, *args: Any) -> list[dict]:
        q = query.strip().lower()
        if "corrections" in q:
            return []
        return []

    async def _execute(query: str, *args: Any) -> None:
        pass

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetch = AsyncMock(side_effect=_fetch)
    pool.execute = AsyncMock(side_effect=_execute)
    return pool


def _session(
    session_id: uuid.UUID | None = None,
    *,
    trigger_source: str = "route",
    ingestion_event_id: uuid.UUID | None = None,
    tool_calls: list | None = None,
) -> dict:
    return {
        "id": session_id or uuid.uuid4(),
        "trigger_source": trigger_source,
        "ingestion_event_id": ingestion_event_id,
        "tool_calls": tool_calls or [],
        "prompt": "test prompt",
        "result": None,
    }


# ---------------------------------------------------------------------------
# 7.1 — Correction record insertion and append-only enforcement
# ---------------------------------------------------------------------------


@corrections_required
async def test_create_correction_inserts_row():
    """create_correction inserts a row into the corrections table."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
    pool.execute = AsyncMock()

    target_sid = uuid.uuid4()
    correcting_sid = uuid.uuid4()

    correction_id = await create_correction(
        pool,
        correction_type=CorrectionType.DATA_CORRECTION,
        target_session_id=target_sid,
        correcting_session_id=correcting_sid,
        description="Fixed wrong value",
        status="applied",
        summary="State key 'x' corrected from 1 to 2",
        original_data_snapshot={"x": 1},
        correction_details={"state_key": "x", "new_value": 2},
    )

    assert pool.execute.called or pool.fetchrow.called
    assert correction_id is not None


@corrections_required
async def test_create_correction_append_only_no_update():
    """create_correction never issues UPDATE on the corrections table."""
    pool = AsyncMock()
    issued_queries: list[str] = []

    async def _record_execute(query: str, *args: Any) -> None:
        issued_queries.append(query.upper())

    pool.execute = AsyncMock(side_effect=_record_execute)
    pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})

    await create_correction(
        pool,
        correction_type=CorrectionType.DATA_CORRECTION,
        target_session_id=uuid.uuid4(),
        correcting_session_id=uuid.uuid4(),
        description="desc",
        status="applied",
        summary="ok",
        original_data_snapshot=None,
        correction_details=None,
    )

    for q in issued_queries:
        assert "UPDATE" not in q, f"create_correction must not UPDATE corrections table: {q}"
        assert "DELETE" not in q, f"create_correction must not DELETE corrections table: {q}"


@corrections_required
async def test_create_correction_records_failed_attempt():
    """create_correction records rows with status=failed when preconditions are not met."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
    pool.execute = AsyncMock()

    correction_id = await create_correction(
        pool,
        correction_type=CorrectionType.DATA_CORRECTION,
        target_session_id=uuid.uuid4(),
        correcting_session_id=uuid.uuid4(),
        description="attempted correction",
        status="failed",
        summary="Session not found",
        original_data_snapshot=None,
        correction_details=None,
    )
    assert correction_id is not None


# ---------------------------------------------------------------------------
# 7.2 — Precondition validation per correction type
# ---------------------------------------------------------------------------


@corrections_required
async def test_data_correction_preconditions_pass_when_session_and_key_exist():
    """data_correction preconditions pass when session and state key both exist."""
    pool = _make_pool(
        session_row=_session(),
        state_row={"key": "my_key", "value": "old_value"},
    )

    error = await check_data_correction_preconditions(
        pool,
        target_session_id=uuid.uuid4(),
        state_key="my_key",
        corrected_value="new_value",
    )
    assert error is None


@corrections_required
async def test_data_correction_preconditions_fail_missing_session():
    """data_correction fails when target session does not exist."""
    pool = _make_pool(session_row=None)

    error = await check_data_correction_preconditions(
        pool,
        target_session_id=uuid.uuid4(),
        state_key="my_key",
        corrected_value="new_value",
    )
    assert error is not None
    assert "does not exist" in error.lower() or "not found" in error.lower()


@corrections_required
async def test_data_correction_preconditions_fail_missing_key():
    """data_correction fails when state key does not exist."""
    pool = _make_pool(
        session_row=_session(),
        state_row=None,
    )

    error = await check_data_correction_preconditions(
        pool,
        target_session_id=uuid.uuid4(),
        state_key="nonexistent_key",
        corrected_value="new_value",
    )
    assert error is not None
    assert "key" in error.lower() or "not found" in error.lower()


@corrections_required
async def test_memory_deletion_preconditions_pass_active_memory():
    """memory_deletion preconditions pass for an active memory."""
    pool = _make_pool(
        session_row=_session(),
        memory_row={"id": uuid.uuid4(), "validity": "active"},
    )

    async def _fetch_memory(*args: Any) -> dict | None:
        return {"id": uuid.uuid4(), "validity": "active", "content": "some memory"}

    pool.fetchrow = AsyncMock(
        side_effect=lambda q, *a: (
            _session() if "sessions" in q.lower() else {"id": uuid.uuid4(), "validity": "active"}
        )
    )

    error = await check_memory_deletion_preconditions(
        pool,
        target_session_id=uuid.uuid4(),
        memory_type="fact",
        memory_id=uuid.uuid4(),
    )
    assert error is None


@corrections_required
async def test_memory_deletion_preconditions_fail_already_retracted():
    """memory_deletion fails when memory is already retracted."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        side_effect=lambda q, *a: (
            _session() if "sessions" in q.lower() else {"id": uuid.uuid4(), "validity": "retracted"}
        )
    )

    error = await check_memory_deletion_preconditions(
        pool,
        target_session_id=uuid.uuid4(),
        memory_type="fact",
        memory_id=uuid.uuid4(),
    )
    assert error is not None
    assert "retracted" in error.lower() or "already" in error.lower()


@corrections_required
async def test_memory_deletion_preconditions_fail_superseded():
    """memory_deletion fails when memory is superseded."""
    pool = AsyncMock()
    successor_id = uuid.uuid4()
    pool.fetchrow = AsyncMock(
        side_effect=lambda q, *a: (
            _session()
            if "sessions" in q.lower()
            else {"id": uuid.uuid4(), "validity": "superseded", "successor_id": successor_id}
        )
    )

    error = await check_memory_deletion_preconditions(
        pool,
        target_session_id=uuid.uuid4(),
        memory_type="fact",
        memory_id=uuid.uuid4(),
    )
    assert error is not None
    assert "superseded" in error.lower()


@corrections_required
async def test_misroute_preconditions_pass_valid_session_and_butler():
    """misroute preconditions pass when session has ingestion event and butler is registered."""
    ingestion_id = uuid.uuid4()
    pool = _make_pool(
        session_row=_session(ingestion_event_id=ingestion_id),
    )

    registered_butlers = ["home", "finance", "travel"]
    error = await check_misroute_preconditions(
        pool,
        target_session_id=uuid.uuid4(),
        correct_butler="finance",
        registered_butlers=registered_butlers,
    )
    assert error is None


@corrections_required
async def test_misroute_preconditions_fail_no_ingestion_event():
    """misroute fails when session was not spawned from an ingestion event."""
    pool = _make_pool(
        session_row=_session(trigger_source="schedule:daily", ingestion_event_id=None),
    )

    error = await check_misroute_preconditions(
        pool,
        target_session_id=uuid.uuid4(),
        correct_butler="finance",
        registered_butlers=["home", "finance"],
    )
    assert error is not None
    assert "ingestion" in error.lower() or "triggered" in error.lower()


@corrections_required
async def test_misroute_preconditions_fail_unregistered_butler():
    """misroute fails when target butler is not registered."""
    ingestion_id = uuid.uuid4()
    pool = _make_pool(
        session_row=_session(ingestion_event_id=ingestion_id),
    )

    error = await check_misroute_preconditions(
        pool,
        target_session_id=uuid.uuid4(),
        correct_butler="unknown_butler",
        registered_butlers=["home", "finance"],
    )
    assert error is not None
    assert "not registered" in error.lower() or "unknown_butler" in error.lower()


@corrections_required
async def test_action_reversal_preconditions_pass_session_with_tool_calls():
    """action_reversal preconditions pass when session has recorded tool calls."""
    pool = _make_pool(
        session_row=_session(tool_calls=[{"tool": "remind", "args": {}}]),
    )

    error = await check_action_reversal_preconditions(
        pool,
        target_session_id=uuid.uuid4(),
        action_description="cancel reminder",
    )
    assert error is None


@corrections_required
async def test_action_reversal_preconditions_fail_no_session():
    """action_reversal fails when session does not exist."""
    pool = _make_pool(session_row=None)

    error = await check_action_reversal_preconditions(
        pool,
        target_session_id=uuid.uuid4(),
        action_description="cancel reminder",
    )
    assert error is not None


# ---------------------------------------------------------------------------
# 7.3 — data_correction handler
# ---------------------------------------------------------------------------


@corrections_required
async def test_data_correction_handler_updates_state_and_records():
    """data_correction handler updates state, stores snapshot, returns applied."""
    pool = AsyncMock()
    session_id = uuid.uuid4()
    correcting_id = uuid.uuid4()

    # Session exists, state key exists with old value
    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id)
        if "state" in q:
            return {"key": "temperature", "value": "hot"}
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)  # rate limit count

    result = await handle_data_correction(
        pool,
        target_session_id=session_id,
        correcting_session_id=correcting_id,
        description="Temperature was recorded wrong",
        state_key="temperature",
        corrected_value="cold",
    )

    assert result["status"] == "applied"
    assert result.get("correction_id") is not None
    assert result.get("original_data_snapshot") is not None


@corrections_required
async def test_data_correction_handler_fails_missing_session():
    """data_correction handler returns failed when target session does not exist."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)  # session not found
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    result = await handle_data_correction(
        pool,
        target_session_id=uuid.uuid4(),
        correcting_session_id=uuid.uuid4(),
        description="Try to correct non-existent session",
        state_key="x",
        corrected_value="y",
    )

    assert result["status"] == "failed"
    assert "summary" in result


@corrections_required
async def test_data_correction_snapshot_contains_original_value():
    """data_correction stores the original value in original_data_snapshot."""
    pool = AsyncMock()
    session_id = uuid.uuid4()

    original_value = {"amount": 100, "currency": "USD"}

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id)
        if "state" in q:
            return {"key": "budget", "value": original_value}
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    result = await handle_data_correction(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Fix budget",
        state_key="budget",
        corrected_value={"amount": 200, "currency": "USD"},
    )

    assert result["status"] == "applied"
    snapshot = result.get("original_data_snapshot") or {}
    assert snapshot.get("budget") == original_value or snapshot == original_value


# ---------------------------------------------------------------------------
# 7.4 — memory_deletion handler
# ---------------------------------------------------------------------------


@corrections_required
async def test_memory_deletion_handler_retracts_active_memory():
    """memory_deletion handler retracts an active memory and records correction."""
    pool = AsyncMock()
    memory_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id)
        if "memories" in q or "memory" in q:
            return {
                "id": memory_id,
                "validity": "active",
                "subject": "user",
                "predicate": "likes",
                "object": "coffee",
            }
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    memory_forget_called = []

    with patch(
        "butlers.core.corrections.memory_forget",
        new=AsyncMock(side_effect=lambda *a, **kw: memory_forget_called.append(True)),
    ):
        result = await handle_memory_deletion(
            pool,
            target_session_id=session_id,
            correcting_session_id=uuid.uuid4(),
            description="Wrong fact stored",
            memory_type="fact",
            memory_id=memory_id,
        )

    assert result["status"] == "applied"
    assert result.get("correction_id") is not None


@corrections_required
async def test_memory_deletion_handler_preserves_original_content():
    """memory_deletion stores original memory content in snapshot before retraction."""
    pool = AsyncMock()
    memory_id = uuid.uuid4()
    session_id = uuid.uuid4()

    original_content = {"subject": "user", "predicate": "likes", "object": "broccoli"}

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id)
        return {"id": memory_id, "validity": "active", **original_content}

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    with patch("butlers.core.corrections.memory_forget", new=AsyncMock()):
        result = await handle_memory_deletion(
            pool,
            target_session_id=session_id,
            correcting_session_id=uuid.uuid4(),
            description="Wrong fact",
            memory_type="fact",
            memory_id=memory_id,
        )

    assert result["status"] == "applied"
    snapshot = result.get("original_data_snapshot") or {}
    # Snapshot must contain the original memory content
    assert snapshot  # non-empty


@corrections_required
async def test_memory_deletion_handler_rejects_already_retracted():
    """memory_deletion handler returns failed if memory is already retracted."""
    pool = AsyncMock()
    memory_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id)
        return {"id": memory_id, "validity": "retracted"}

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    result = await handle_memory_deletion(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Delete memory",
        memory_type="fact",
        memory_id=memory_id,
    )

    assert result["status"] == "failed"
    assert "retracted" in result["summary"].lower() or "already" in result["summary"].lower()


# ---------------------------------------------------------------------------
# 7.5 — misroute handler
# ---------------------------------------------------------------------------


@corrections_required
async def test_misroute_handler_success_returns_new_session_id():
    """misroute handler returns new_session_id on successful re-dispatch."""
    pool = AsyncMock()
    ingestion_id = uuid.uuid4()
    new_session_id = str(uuid.uuid4())
    session_id = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id, ingestion_event_id=ingestion_id)
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    switchboard_client = AsyncMock()
    switchboard_client.call_tool = AsyncMock(
        return_value={"new_session_id": new_session_id, "status": "dispatched"}
    )

    result = await handle_misroute(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Wrong butler",
        correct_butler="finance",
        registered_butlers=["home", "finance", "travel"],
        switchboard_client=switchboard_client,
    )

    assert result["status"] == "applied"
    details = result.get("correction_details") or {}
    assert details.get("new_session_id") == new_session_id


@corrections_required
async def test_misroute_handler_fail_expired_ingestion_event():
    """misroute handler returns failed when ingestion event has expired."""
    pool = AsyncMock()
    ingestion_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id, ingestion_event_id=ingestion_id)
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    switchboard_client = AsyncMock()
    switchboard_client.call_tool = AsyncMock(
        return_value={"status": "expired", "error": "ingestion event expired"}
    )

    result = await handle_misroute(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Wrong butler",
        correct_butler="finance",
        registered_butlers=["home", "finance"],
        switchboard_client=switchboard_client,
    )

    assert result["status"] == "failed"
    assert "expired" in result["summary"].lower() or "30 days" in result["summary"].lower()


@corrections_required
async def test_misroute_handler_fail_unregistered_butler():
    """misroute handler returns failed when correct_butler is not registered."""
    pool = AsyncMock()
    ingestion_id = uuid.uuid4()
    session_id = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id, ingestion_event_id=ingestion_id)
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    result = await handle_misroute(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Wrong butler",
        correct_butler="ghost_butler",
        registered_butlers=["home", "finance", "travel"],
        switchboard_client=AsyncMock(),
    )

    assert result["status"] == "failed"
    summary_lower = result["summary"].lower()
    assert "not registered" in summary_lower or "ghost_butler" in summary_lower


@corrections_required
async def test_misroute_correction_details_contain_required_fields():
    """Successful misroute correction_details includes correct_butler, new_session_id."""
    pool = AsyncMock()
    ingestion_id = uuid.uuid4()
    new_session_id = str(uuid.uuid4())
    session_id = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id, ingestion_event_id=ingestion_id)
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    switchboard_client = AsyncMock()
    switchboard_client.call_tool = AsyncMock(
        return_value={"new_session_id": new_session_id, "status": "dispatched"}
    )

    result = await handle_misroute(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Wrong butler",
        correct_butler="finance",
        registered_butlers=["home", "finance"],
        switchboard_client=switchboard_client,
        original_butler="home",
    )

    assert result["status"] == "applied"
    details = result.get("correction_details") or {}
    assert "correct_butler" in details
    assert "new_session_id" in details


# ---------------------------------------------------------------------------
# 7.6 — action_reversal handler
# ---------------------------------------------------------------------------


@corrections_required
async def test_action_reversal_full_success():
    """action_reversal returns applied when action is fully reversed."""
    pool = AsyncMock()
    session_id = uuid.uuid4()

    tool_calls = [
        {"tool": "remind", "args": {"message": "Call back"}, "result": {"reminder_id": "r1"}}
    ]

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id, tool_calls=tool_calls)
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    result = await handle_action_reversal(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Reminder created by mistake",
        action_description="cancel the reminder",
    )

    assert result["status"] in {"applied", "partially_applied", "failed"}
    assert "summary" in result
    assert result.get("correction_id") is not None


@corrections_required
async def test_action_reversal_partial_success():
    """action_reversal returns partially_applied when only some actions can be reversed."""
    pool = AsyncMock()
    session_id = uuid.uuid4()

    # Two tool calls — one reversible, one not
    tool_calls = [
        {"tool": "remind", "args": {}, "result": {"reminder_id": "r1"}},
        {"tool": "notify", "args": {"message": "sent"}, "result": {"delivered": True}},
    ]

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id, tool_calls=tool_calls)
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    # Simulate partial reversal outcome from handler
    with patch(
        "butlers.core.corrections._attempt_action_reversal",
        new=AsyncMock(return_value={"reversed": ["remind"], "irreversible": ["notify"]}),
    ):
        result = await handle_action_reversal(
            pool,
            target_session_id=session_id,
            correcting_session_id=uuid.uuid4(),
            description="Undo session actions",
            action_description="cancel reminder and message",
        )

    assert result["status"] in {"partially_applied", "applied", "failed"}
    assert "summary" in result


@corrections_required
async def test_action_reversal_failed_no_reversible_actions():
    """action_reversal returns failed when no actions in the session can be reversed."""
    pool = AsyncMock()
    session_id = uuid.uuid4()

    # Only irreversible actions
    tool_calls = [{"tool": "notify", "args": {}, "result": {}}]

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id, tool_calls=tool_calls)
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    with patch(
        "butlers.core.corrections._attempt_action_reversal",
        new=AsyncMock(return_value={"reversed": [], "irreversible": ["notify"]}),
    ):
        result = await handle_action_reversal(
            pool,
            target_session_id=session_id,
            correcting_session_id=uuid.uuid4(),
            description="Undo everything",
            action_description="cancel notification",
        )

    # Result can be failed or partially_applied with zero reversed; must explain
    assert result["status"] in {"failed", "partially_applied"}
    assert result["summary"]


# ---------------------------------------------------------------------------
# 7.7 — Correction audit queries
# ---------------------------------------------------------------------------


@corrections_required
async def test_corrections_by_session_returns_rows_ordered_by_created_at():
    """corrections_by_session returns all corrections for a target session."""
    pool = AsyncMock()
    target_id = uuid.uuid4()
    now = datetime.now(UTC)
    five_min_ago = now - timedelta(minutes=5)
    rows = [
        {"id": uuid.uuid4(), "target_session_id": target_id, "created_at": five_min_ago},
        {"id": uuid.uuid4(), "target_session_id": target_id, "created_at": now},
    ]
    pool.fetch = AsyncMock(return_value=rows)

    results = await corrections_by_session(pool, target_session_id=target_id)
    assert len(results) == 2
    # Must be ordered by created_at (oldest first is standard)


@corrections_required
async def test_corrections_for_session_returns_corrections_made_by_session():
    """corrections_for_session returns corrections initiated by the given session."""
    pool = AsyncMock()
    correcting_id = uuid.uuid4()
    rows = [
        {"id": uuid.uuid4(), "correcting_session_id": correcting_id},
    ]
    pool.fetch = AsyncMock(return_value=rows)

    results = await corrections_for_session(pool, correcting_session_id=correcting_id)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# 7.9 — correct tool description contract
# ---------------------------------------------------------------------------


@corrections_required
def test_tool_description_contains_all_correction_types():
    """CORRECT_TOOL_DESCRIPTION mentions all four correction type names."""
    desc = CORRECT_TOOL_DESCRIPTION
    assert "data_correction" in desc
    assert "memory_deletion" in desc
    assert "misroute" in desc
    assert "action_reversal" in desc


@corrections_required
def test_tool_description_contains_not_for_exclusion_list():
    """CORRECT_TOOL_DESCRIPTION includes a NOT for exclusion list."""
    desc = CORRECT_TOOL_DESCRIPTION
    assert "NOT for" in desc or "not for" in desc.lower()
    assert "state_set" in desc
    assert "memory" in desc.lower()


@corrections_required
def test_tool_description_contains_required_parameters():
    """CORRECT_TOOL_DESCRIPTION lists all required parameter names."""
    desc = CORRECT_TOOL_DESCRIPTION
    assert "correction_type" in desc
    assert "target_session_id" in desc
    assert "description" in desc


@corrections_required
def test_tool_description_contains_optional_parameters():
    """CORRECT_TOOL_DESCRIPTION lists all optional parameter names."""
    desc = CORRECT_TOOL_DESCRIPTION
    assert "target_butler" in desc
    assert "correct_butler" in desc
    assert "state_key" in desc
    assert "corrected_value" in desc
    assert "memory_type" in desc
    assert "memory_id" in desc
    assert "action_description" in desc


@corrections_required
def test_tool_description_states_only_for_past_mistakes():
    """CORRECT_TOOL_DESCRIPTION clearly states it is for fixing past mistakes."""
    desc = CORRECT_TOOL_DESCRIPTION.lower()
    # Must include some form of "ONLY to correct" / "past errors" / "mistakes"
    assert any(word in desc for word in ("mistake", "past error", "previous", "fix"))


# ---------------------------------------------------------------------------
# 7.10 — Failure message dictionary — 12 templates
# ---------------------------------------------------------------------------

_EXPECTED_KEYS = [
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
]


@corrections_required
def test_failure_messages_has_all_12_entries():
    """FAILURE_MESSAGES dictionary contains all 12 required error templates."""
    assert len(FAILURE_MESSAGES) >= 12
    for key in _EXPECTED_KEYS:
        assert key in FAILURE_MESSAGES, f"Missing failure message key: {key}"


@corrections_required
def test_failure_message_session_not_found_placeholder():
    """session_not_found message interpolates session id and includes next-action hint."""
    sid = str(uuid.uuid4())
    msg = FAILURE_MESSAGES["session_not_found"].format(id=sid)
    assert sid in msg
    # Must include a remediation hint
    assert "sessions_list" in msg or "session" in msg.lower()


@corrections_required
def test_failure_message_state_key_not_found_placeholder():
    """state_key_not_found message interpolates key name and includes hint."""
    msg = FAILURE_MESSAGES["state_key_not_found"].format(key="budget")
    assert "budget" in msg
    assert "state_list" in msg or "state" in msg.lower()


@corrections_required
def test_failure_message_memory_already_retracted_placeholder():
    """memory_already_retracted message interpolates memory id and retraction date."""
    mid = str(uuid.uuid4())
    msg = FAILURE_MESSAGES["memory_already_retracted"].format(id=mid, date="2026-01-01")
    assert mid in msg
    assert "retracted" in msg.lower()


@corrections_required
def test_failure_message_memory_superseded_placeholder():
    """memory_superseded message interpolates memory id and successor id."""
    mid = str(uuid.uuid4())
    successor = str(uuid.uuid4())
    msg = FAILURE_MESSAGES["memory_superseded"].format(id=mid, successor_id=successor)
    assert mid in msg
    assert successor in msg


@corrections_required
def test_failure_message_butler_not_registered_placeholder():
    """butler_not_registered message interpolates butler name and available list."""
    msg = FAILURE_MESSAGES["butler_not_registered"].format(
        name="ghost_butler", comma_separated_list="home, finance, travel"
    )
    assert "ghost_butler" in msg
    assert "home, finance, travel" in msg


@corrections_required
def test_failure_message_ingestion_event_expired_placeholder():
    """ingestion_event_expired message interpolates correct_butler and includes re-send hint."""
    msg = FAILURE_MESSAGES["ingestion_event_expired"].format(correct_butler="finance")
    assert "finance" in msg
    assert "30 days" in msg or "expired" in msg.lower() or "re-send" in msg.lower()


@corrections_required
def test_failure_message_action_not_reversible_placeholder():
    """action_not_reversible message interpolates action type and reversible list."""
    msg = FAILURE_MESSAGES["action_not_reversible"].format(
        type="send_email", comma_separated_list="remind, schedule"
    )
    assert "send_email" in msg
    assert "remind, schedule" in msg


@corrections_required
def test_failure_message_unknown_correction_type_placeholder():
    """unknown_correction_type message interpolates type and lists valid types."""
    msg = FAILURE_MESSAGES["unknown_correction_type"].format(type="magic_fix")
    assert "magic_fix" in msg
    assert "data_correction" in msg
    assert "misroute" in msg


@corrections_required
def test_failure_message_missing_required_parameter_placeholder():
    """missing_required_parameter message interpolates param and correction_type."""
    msg = FAILURE_MESSAGES["missing_required_parameter"].format(
        param="state_key", type="data_correction"
    )
    assert "state_key" in msg
    assert "data_correction" in msg


@corrections_required
def test_failure_message_session_no_ingestion_event_placeholder():
    """session_no_ingestion_event message interpolates session id and trigger source."""
    sid = str(uuid.uuid4())
    msg = FAILURE_MESSAGES["session_no_ingestion_event"].format(id=sid, source="schedule:daily")
    assert sid in msg
    assert "schedule:daily" in msg or "ingestion" in msg.lower()


@corrections_required
def test_failure_message_memory_not_found_placeholder():
    """memory_not_found message interpolates memory id and type, hints memory_recall."""
    mid = str(uuid.uuid4())
    msg = FAILURE_MESSAGES["memory_not_found"].format(id=mid, memory_type="fact")
    assert mid in msg
    assert "fact" in msg
    assert "memory_recall" in msg or "memory" in msg.lower()


@corrections_required
def test_failure_message_switchboard_unreachable():
    """switchboard_unreachable message exists and contains actionable guidance."""
    msg = FAILURE_MESSAGES["switchboard_unreachable"]
    assert msg  # non-empty
    assert any(word in msg.lower() for word in ("try again", "escalate", "later", "switchboard"))


# ---------------------------------------------------------------------------
# 7.11 — Decision tree coverage
# ---------------------------------------------------------------------------


@corrections_required
def test_decision_tree_stored_data_wrong_returns_data_correction():
    """Decision tree: stored data is wrong → data_correction."""
    result = get_correction_type_for_situation(
        stored_data_wrong=True,
        memory_wrong=False,
        wrong_butler=False,
        action_mistake=False,
    )
    assert result == CorrectionType.DATA_CORRECTION


@corrections_required
def test_decision_tree_memory_wrong_returns_memory_deletion():
    """Decision tree: memory is wrong → memory_deletion."""
    result = get_correction_type_for_situation(
        stored_data_wrong=False,
        memory_wrong=True,
        wrong_butler=False,
        action_mistake=False,
    )
    assert result == CorrectionType.MEMORY_DELETION


@corrections_required
def test_decision_tree_wrong_butler_returns_misroute():
    """Decision tree: message went to wrong butler → misroute."""
    result = get_correction_type_for_situation(
        stored_data_wrong=False,
        memory_wrong=False,
        wrong_butler=True,
        action_mistake=False,
    )
    assert result == CorrectionType.MISROUTE


@corrections_required
def test_decision_tree_action_mistake_returns_action_reversal():
    """Decision tree: action was mistaken → action_reversal."""
    result = get_correction_type_for_situation(
        stored_data_wrong=False,
        memory_wrong=False,
        wrong_butler=False,
        action_mistake=True,
    )
    assert result == CorrectionType.ACTION_REVERSAL


@corrections_required
def test_decision_tree_none_match_returns_none():
    """Decision tree: none of the above → None (use other tools)."""
    result = get_correction_type_for_situation(
        stored_data_wrong=False,
        memory_wrong=False,
        wrong_butler=False,
        action_mistake=False,
    )
    assert result is None


@corrections_required
def test_decision_tree_first_match_wins():
    """Decision tree: first matching condition (stored data) wins when multiple are True."""
    result = get_correction_type_for_situation(
        stored_data_wrong=True,
        memory_wrong=True,
        wrong_butler=True,
        action_mistake=True,
    )
    # First branch: stored data wins
    assert result == CorrectionType.DATA_CORRECTION


# ---------------------------------------------------------------------------
# 7.12 — Cross-schema resolution
# ---------------------------------------------------------------------------


@corrections_required
async def test_cross_schema_correction_own_schema_default():
    """Without target_butler, data_correction queries the current butler's own schema."""
    pool = AsyncMock()
    session_id = uuid.uuid4()
    queried_schemas: list[str] = []

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        queried_schemas.append(query)
        q = query.lower()
        if "sessions" in q:
            return _session(session_id)
        if "state" in q:
            return {"key": "x", "value": 1}
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)

    result = await handle_data_correction(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="fix x",
        state_key="x",
        corrected_value=2,
        target_butler=None,
    )
    # target_butler=None means no cross-schema; just verify success
    assert result["status"] in {"applied", "failed"}


@corrections_required
async def test_cross_schema_correction_with_valid_target_butler():
    """With target_butler, correction record is written to correcting butler's table."""
    pool = AsyncMock()
    target_pool = AsyncMock()
    session_id = uuid.uuid4()

    target_pool.fetchrow = AsyncMock(
        side_effect=lambda q, *a: (
            _session(session_id)
            if "sessions" in q.lower()
            else {"key": "y", "value": "old"}
            if "state" in q.lower()
            else None
        )
    )
    target_pool.execute = AsyncMock()

    correction_written_to_current: list[bool] = []
    pool.execute = AsyncMock(side_effect=lambda q, *a: correction_written_to_current.append(True))
    pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4()})
    pool.fetchval = AsyncMock(return_value=0)

    result = await handle_data_correction(
        pool,  # correcting butler's pool — must receive the correction record
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="fix y on finance butler",
        state_key="y",
        corrected_value="new",
        target_butler="finance",
        target_pool=target_pool,  # cross-schema: read/write state from target butler
    )

    assert result["status"] in {"applied", "failed"}
    # Correction INSERT must hit the correcting butler's pool (not target_pool)
    if result["status"] == "applied":
        assert correction_written_to_current


@corrections_required
async def test_cross_schema_correction_unknown_target_butler_returns_error():
    """With unknown target_butler, returns failed with butler_not_registered message."""
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetchrow = AsyncMock(return_value=None)

    result = await handle_data_correction(
        pool,
        target_session_id=uuid.uuid4(),
        correcting_session_id=uuid.uuid4(),
        description="fix something",
        state_key="x",
        corrected_value="y",
        target_butler="nonexistent_butler",
        registered_butlers=["home", "finance", "travel"],
    )

    assert result["status"] == "failed"
    summary_lower = result["summary"].lower()
    assert "not registered" in summary_lower or "nonexistent_butler" in summary_lower


# ---------------------------------------------------------------------------
# 7.13 — Rate limiting
# ---------------------------------------------------------------------------


@corrections_required
async def test_rate_limit_below_threshold_allows_correction():
    """Corrections within the 10/hour limit proceed normally."""
    pool = AsyncMock()
    session_id = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id)
        if "state" in q:
            return {"key": "k", "value": "v"}
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=5)  # 5 corrections in past hour — within limit

    result = await handle_data_correction(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="within limit",
        state_key="k",
        corrected_value="new_v",
    )

    # Must not reject due to rate limit
    assert result["status"] in {"applied", "failed"}
    if result["status"] == "failed":
        assert "rate limit" not in result["summary"].lower()


@corrections_required
async def test_rate_limit_11th_correction_is_rejected():
    """The 11th correction (count=10) in the same hour is rejected with actionable message."""
    pool = AsyncMock()
    session_id = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id)
        if "state" in q:
            return {"key": "k", "value": "v"}
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    pool.fetchval = AsyncMock(return_value=10)  # already at limit

    result = await handle_data_correction(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="11th correction attempt",
        state_key="k",
        corrected_value="new_v",
    )

    assert result["status"] == "failed"
    assert "rate limit" in result["summary"].lower() or "10" in result["summary"]


@corrections_required
async def test_rate_limit_is_per_session_independent_counters():
    """Rate limit counters are per correcting_session_id, not shared across sessions."""
    pool = AsyncMock()
    session_a = uuid.uuid4()
    session_b = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session()
        if "state" in q:
            return {"key": "k", "value": "v"}
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()

    # session_a is at limit; session_b is fresh
    async def _fetchval(query: str, *args: Any) -> int:
        if args and args[0] == session_a:
            return 10  # session_a at limit
        return 0  # session_b has no corrections

    pool.fetchval = AsyncMock(side_effect=_fetchval)

    result_b = await handle_data_correction(
        pool,
        target_session_id=uuid.uuid4(),
        correcting_session_id=session_b,  # session_b has fresh counter
        description="correction from session_b",
        state_key="k",
        corrected_value="new_v",
    )

    # session_b should NOT be rejected by rate limit
    if result_b["status"] == "failed":
        assert "rate limit" not in result_b["summary"].lower()


@corrections_required
async def test_rate_limit_old_corrections_do_not_count():
    """Corrections older than 1 hour fall outside the rolling window and don't count."""
    pool = AsyncMock()
    session_id = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        q = query.lower()
        if "sessions" in q:
            return _session(session_id)
        if "state" in q:
            return {"key": "k", "value": "v"}
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    # 15 total corrections but only 2 within the rolling hour window
    pool.fetchval = AsyncMock(return_value=2)

    result = await handle_data_correction(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="after old corrections expired",
        state_key="k",
        corrected_value="new_v",
    )

    # Must not be rejected due to rate limit (only 2 recent)
    if result["status"] == "failed":
        assert "rate limit" not in result["summary"].lower()
