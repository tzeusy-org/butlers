"""Tests for butlers.core.corrections — error-recovery correction system.

Covers the behavioral contracts for data_correction, memory_deletion,
misroute, action_reversal handlers, and the audit trail.

The append-only audit trail and the state rewrite run against a real migrated
database. Handler branching (preconditions, cross-schema pool selection, the
identifier guard) uses strict ``fake_pool`` seams configured with the rows each
lookup returns, in call order; an unconfigured method raises, so a pool the
handler must not touch is left unconfigured.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.core.sessions import session_create
from butlers.testing.fake_pool import FakePool, fake_pool
from butlers.testing.records import row

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
# Pool seams
# ---------------------------------------------------------------------------


def _correcting_pool(*lookups) -> FakePool:
    """The correcting butler's pool: under the rate limit, audit INSERTs succeed.

    *lookups* are the rows its ``fetchrow`` returns, in call order; with none,
    any lookup on this pool fails the test.
    """
    configured = {"fetchval": 0, "execute": "INSERT 0 1"}
    if lookups:
        configured["fetchrow"] = AsyncMock(side_effect=list(lookups))
    return fake_pool(**configured)


def _target_pool(*lookups) -> FakePool:
    """A target butler's pool answering only row lookups, in call order."""
    return fake_pool(fetchrow=AsyncMock(side_effect=list(lookups)))


def _audit_statuses(pool: FakePool) -> list[str]:
    """``status`` of each correction INSERT, in order (bound parameter $6)."""
    return [
        call.args[6]
        for call in pool.execute.await_args_list
        if "INSERT INTO corrections" in call.args[0]
    ]


async def _seed_session(pool: asyncpg.Pool) -> uuid.UUID:
    return await session_create(
        pool, prompt="seed", trigger_source="tick", request_id=str(uuid.uuid4())
    )


# ---------------------------------------------------------------------------
# Correction record — append-only insertion (real database)
# ---------------------------------------------------------------------------


@corrections_required
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_create_correction_round_trips_through_the_audit_queries(db_pool):
    """create_correction appends rows the two audit queries read back in order."""
    target = await _seed_session(db_pool)
    corrector = await _seed_session(db_pool)
    ids = []
    for status in ("applied", "failed"):
        ids.append(
            await create_correction(
                db_pool,
                correction_type=CorrectionType.DATA_CORRECTION,
                target_session_id=target,
                correcting_session_id=corrector,
                description="Test correction",
                status=status,
                summary=f"Summary {status}",
                original_data_snapshot={"pref": "old"},
                correction_details=None,
            )
        )

    by_target = await corrections_by_session(db_pool, target_session_id=target)
    by_corrector = await corrections_for_session(db_pool, correcting_session_id=corrector)

    assert [r["id"] for r in by_target] == ids
    assert by_corrector == by_target
    assert [r["status"] for r in by_target] == ["applied", "failed"]
    assert by_target[0]["correction_type"] == "data_correction"
    assert by_target[0]["original_data_snapshot"] == {"pref": "old"}
    assert by_target[0]["correction_details"] is None
    assert await corrections_by_session(db_pool, target_session_id=corrector) == []


@corrections_required
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_handle_data_correction_rewrites_state_and_records_the_snapshot(db_pool):
    """An applied data correction rewrites the state row and audits the prior value."""
    target = await _seed_session(db_pool)
    corrector = await _seed_session(db_pool)
    await db_pool.execute("INSERT INTO state (key, value) VALUES ('pref', $1)", "old")

    result = await handle_data_correction(
        db_pool,
        target_session_id=target,
        correcting_session_id=corrector,
        description="Fix it",
        state_key="pref",
        corrected_value={"tone": "new"},
    )

    assert result["status"] == "applied"
    assert result["original_data_snapshot"] == {"pref": "old"}
    assert await db_pool.fetchval("SELECT value FROM state WHERE key = 'pref'") == {"tone": "new"}
    [audit] = await corrections_by_session(db_pool, target_session_id=target)
    assert str(audit["id"]) == result["correction_id"]
    assert audit["status"] == "applied"
    assert audit["original_data_snapshot"] == {"pref": "old"}
    assert audit["correction_details"] == {"state_key": "pref", "new_value": {"tone": "new"}}


# ---------------------------------------------------------------------------
# Handler behavioral contracts
# ---------------------------------------------------------------------------


@corrections_required
async def test_handle_memory_deletion_applies_for_an_active_fact():
    """An active fact on an existing session is retracted and audited as applied."""
    session_id = uuid.uuid4()
    memory_id = uuid.uuid4()
    pool = _correcting_pool(
        row(id=session_id),
        row(id=memory_id, validity="active", supersedes_id=None),
        row(id=memory_id, content="some memory"),
    )

    result = await handle_memory_deletion(
        pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Fix it",
        memory_type="fact",
        memory_id=memory_id,
    )

    assert result["status"] == "applied"
    assert result["original_data_snapshot"] == {"id": memory_id, "content": "some memory"}
    assert _audit_statuses(pool) == ["applied"]


@corrections_required
async def test_handler_failure_cases():
    """handle_data_correction fails when session missing; handle_misroute fails when butler unregistered."""
    # Missing session: no audit row (its FK would reject the INSERT), so no execute.
    pool = fake_pool(fetchval=0, fetchrow=None)
    result = await handle_data_correction(
        pool,
        target_session_id=uuid.uuid4(),
        correcting_session_id=uuid.uuid4(),
        description="Fix missing",
        state_key="key",
        corrected_value="val",
    )
    assert result["status"] == "failed"
    assert result["correction_id"] == ""

    session_id = uuid.uuid4()
    pool2 = _correcting_pool(row(id=session_id, ingestion_event_id=str(uuid.uuid4())))
    result2 = await handle_misroute(
        pool2,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Wrong butler",
        correct_butler="nonexistent_butler",
        registered_butlers=["finance", "general"],
        switchboard_client=AsyncMock(),
    )
    assert result2["status"] == "failed"
    assert "nonexistent_butler" in result2["summary"]
    assert _audit_statuses(pool2) == ["failed"]


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
    pool = _target_pool(row(id=session_id), row(key="mykey"))
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
    pool = _correcting_pool()
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
    assert _audit_statuses(pool) == ["failed"]


@corrections_required
async def test_handle_memory_deletion_cross_schema_uses_target_pool():
    """handle_memory_deletion uses target_pool for session/memory queries when target_butler given."""
    session_id = uuid.uuid4()
    # The session resolves on the target butler, but the fact does not exist.
    target_pool = _target_pool(row(id=session_id), None)
    # The local pool only rate-limits and audits; a lookup here would raise.
    local_pool = _correcting_pool()

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
    assert result["status"] == "failed"
    assert target_pool.fetchrow.await_count == 2
    assert "finance.sessions" in target_pool.fetchrow.await_args_list[0].args[0]
    assert "finance.facts" in target_pool.fetchrow.await_args_list[1].args[0]
    assert _audit_statuses(local_pool) == ["failed"]


@corrections_required
async def test_handle_memory_deletion_target_butler_in_correction_details():
    """handle_memory_deletion includes target_butler in correction_details on success."""
    import datetime
    import unittest.mock as _mock

    session_id = uuid.uuid4()
    mem_id = uuid.uuid4()
    target_pool = _target_pool(
        row(id=session_id),
        # A non-expired episode passes the validity check...
        row(id=mem_id, expires_at=datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)),
        # ...and is snapshotted before retraction.
        row(id=mem_id, content="episode content"),
    )
    local_pool = _correcting_pool()

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
    assert result["original_data_snapshot"] == {"id": mem_id, "content": "episode content"}
    mock_forget.assert_called_once_with(target_pool, "episode", str(mem_id))
    assert _audit_statuses(local_pool) == ["applied"]


# ---------------------------------------------------------------------------
# check_misroute_preconditions — real registered_butlers list (bu-e2vdj)
# ---------------------------------------------------------------------------


@corrections_required
async def test_check_misroute_preconditions_passes_for_registered_butler():
    """A correct_butler present in a real registered_butlers list passes
    preconditions (returns None), not a butler_not_registered failure.

    Regression guard for the tool-layer bug where `correct` hardcoded
    registered_butlers=[] for misroute corrections, so every target butler
    -- even correctly registered ones -- failed check_misroute_preconditions.
    """
    session_id = uuid.uuid4()
    pool = _target_pool(row(id=session_id, ingestion_event_id=str(uuid.uuid4())))

    result = await check_misroute_preconditions(
        pool,
        target_session_id=session_id,
        correct_butler="finance",
        registered_butlers=["finance", "general"],
    )

    assert result is None


@corrections_required
async def test_check_misroute_preconditions_fails_for_unregistered_butler():
    """A correct_butler absent from registered_butlers fails with butler_not_registered."""
    session_id = uuid.uuid4()
    pool = _target_pool(row(id=session_id, ingestion_event_id=str(uuid.uuid4())))

    result = await check_misroute_preconditions(
        pool,
        target_session_id=session_id,
        correct_butler="ghost_butler",
        registered_butlers=["finance", "general"],
    )

    assert result is not None
    assert "ghost_butler" in result


# ---------------------------------------------------------------------------
# Cross-schema: handle_misroute
# ---------------------------------------------------------------------------


@corrections_required
async def test_handle_misroute_cross_schema_unknown_target_butler():
    """handle_misroute fails with butler_not_registered when target_butler not in registered list."""
    session_id = uuid.uuid4()
    pool = _correcting_pool()
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
    assert _audit_statuses(pool) == ["failed"]


def _ingested_session(session_id: uuid.UUID, ingestion_id: uuid.UUID):
    return row(id=session_id, trigger_source="ingestion", ingestion_event_id=ingestion_id)


def _redispatching_switchboard() -> AsyncMock:
    client = AsyncMock()
    client.call_tool = AsyncMock(return_value={"status": "ok", "new_session_id": "new-1"})
    return client


@corrections_required
async def test_handle_misroute_cross_schema_uses_target_pool():
    """handle_misroute uses target_pool for session lookup when target_butler is given."""
    session_id = uuid.uuid4()
    ingestion_id = uuid.uuid4()
    # Precondition check, then the routing snapshot: both on the target butler.
    session = _ingested_session(session_id, ingestion_id)
    target_pool = _target_pool(session, session)
    local_pool = _correcting_pool()

    result = await handle_misroute(
        local_pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Cross-schema misroute via target_pool",
        correct_butler="finance",
        registered_butlers=["finance", "general"],
        switchboard_client=_redispatching_switchboard(),
        target_butler="general",
        target_pool=target_pool,
    )

    assert target_pool.fetchrow.await_count == 2
    assert "general.sessions" in target_pool.fetchrow.await_args.args[0]
    assert result["status"] == "applied"
    assert result["correction_details"]["target_butler"] == "general"
    assert result["correction_details"]["correct_butler"] == "finance"
    assert result["correction_details"]["new_session_id"] == "new-1"
    assert result["original_data_snapshot"]["original_request_id"] == str(ingestion_id)
    assert _audit_statuses(local_pool) == ["applied"]


# ---------------------------------------------------------------------------
# Cross-schema: handle_action_reversal
# ---------------------------------------------------------------------------


@corrections_required
async def test_handle_action_reversal_cross_schema_unknown_butler():
    """handle_action_reversal fails with butler_not_registered when target_butler not in list."""
    pool = _correcting_pool()
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
    assert _audit_statuses(pool) == ["failed"]


@corrections_required
async def test_handle_action_reversal_cross_schema_uses_target_pool():
    """handle_action_reversal uses target_pool for session lookup when target_butler is given."""
    session_id = uuid.uuid4()
    session = row(id=session_id, tool_calls=[{"tool": "remind"}])
    target_pool = _target_pool(session, session)
    local_pool = _correcting_pool()

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

    assert target_pool.fetchrow.await_count == 2
    assert "finance.sessions" in target_pool.fetchrow.await_args.args[0]
    assert result["status"] == "applied"
    assert result["correction_details"]["reversed"] == ["remind"]
    assert result["correction_details"].get("target_butler") == "finance"
    assert _audit_statuses(local_pool) == ["applied"]


# ---------------------------------------------------------------------------
# SQL injection guard: _validate_identifier on target_butler
# ---------------------------------------------------------------------------


def _rate_limited_only() -> FakePool:
    """Only the rate-limit count is configured: the guard must fire before any lookup."""
    return fake_pool(fetchval=0)


@corrections_required
async def test_handle_memory_deletion_rejects_unsafe_target_butler():
    """handle_memory_deletion raises ValueError for an unsafe target_butler identifier."""
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        await handle_memory_deletion(
            _rate_limited_only(),
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
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        await handle_misroute(
            _rate_limited_only(),
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
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        await handle_action_reversal(
            _rate_limited_only(),
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
    session = _ingested_session(session_id, uuid.uuid4())
    target_pool = _target_pool(session, session)
    local_pool = _correcting_pool()

    # registered_butlers=None should not cause a TypeError or "butler_not_registered" failure
    result = await handle_misroute(
        local_pool,
        target_session_id=session_id,
        correcting_session_id=uuid.uuid4(),
        description="Misroute with no registered list",
        correct_butler="finance",
        registered_butlers=None,
        switchboard_client=_redispatching_switchboard(),
        target_butler="general",
        target_pool=target_pool,
    )
    assert result["status"] == "applied"


@corrections_required
async def test_handle_action_reversal_target_butler_no_pool_fallback():
    """handle_action_reversal falls back to pool when target_pool is None."""
    session_id = uuid.uuid4()
    session = row(id=session_id, tool_calls=[])
    pool = _correcting_pool(session, session)

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
    assert pool.fetchrow.await_count == 2
    assert "finance.sessions" in pool.fetchrow.await_args.args[0]
    assert result["status"] == "partially_applied"
    assert result["correction_details"].get("target_butler") == "finance"
    assert _audit_statuses(pool) == ["partially_applied"]
