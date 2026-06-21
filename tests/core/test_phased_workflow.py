"""Tests for phased workflow state and evidence plumbing [bu-xp0x0.4].

Covers:
- QaFinding: source_session_trigger_source and structured_evidence fields
- insert_finding: persists source_session_trigger_source and structured_evidence
- QA self-recursion barrier: suppresses QA-originated findings from QA sessions
- record_phase_session: called by healing dispatch when session is launched
- record_phase_session: called by QA dispatch when session is launched
- Phase session status updates (completed/failed/timeout) flow through all paths
- Workflow deadline remains separate from per-session timeouts
"""

from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.healing.dispatch import (
    HealingConfig,
    _run_healing_session,
)
from butlers.core.healing.fingerprint import FingerprintResult
from butlers.core.healing.tracking import create_or_join_attempt
from butlers.core.qa.dispatch import (
    QaDispatchConfig,
    _run_investigation_session,
    dispatch_qa_investigation,
)
from butlers.core.qa.findings import insert_finding
from butlers.core.qa.models import QaFinding
from butlers.core.qa.triage import TriagedFinding

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _make_finding(
    source_butler: str = "finance",
    severity: int = 1,
    source_session_trigger_source: str | None = None,
    structured_evidence: dict | None = None,
) -> QaFinding:
    now = _now()
    return QaFinding(
        fingerprint=uuid.uuid4().hex * 2,
        source_type="session_records",
        source_butler=source_butler,
        severity=severity,
        exception_type="ValueError",
        event_summary="Test event",
        call_site="module:func",
        occurrence_count=3,
        first_seen=now,
        last_seen=now,
        timestamp=now,
        source_session_trigger_source=source_session_trigger_source,
        structured_evidence=structured_evidence,
    )


def _make_triaged(finding: QaFinding | None = None) -> TriagedFinding:
    return TriagedFinding(
        finding=finding or _make_finding(),
        dedup_reason=None,
        finding_id=uuid.uuid4(),
    )


def _make_qa_pool():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool


def _make_spawner(success: bool = True) -> MagicMock:
    spawner = MagicMock()

    @dataclass
    class _Result:
        success: bool
        session_id: uuid.UUID | None
        error: str | None = None

    result = _Result(success=success, session_id=uuid.uuid4())
    spawner.trigger = AsyncMock(return_value=result)
    return spawner


def _make_fp() -> FingerprintResult:
    return FingerprintResult(
        fingerprint="a" * 64,
        severity=1,
        exception_type="builtins.ValueError",
        call_site="src/butlers/jobs.py:run",
        sanitized_message="connection failed",
    )


def _make_healing_config(**kwargs) -> HealingConfig:
    defaults = {
        "enabled": True,
        "severity_threshold": 2,
        "cooldown_minutes": 60,
        "max_concurrent": 2,
        "circuit_breaker_threshold": 5,
        "timeout_minutes": 30,
    }
    defaults.update(kwargs)
    return HealingConfig(**defaults)


# ---------------------------------------------------------------------------
# QaFinding: new fields
# ---------------------------------------------------------------------------


def test_qa_finding_new_fields_default_and_set():
    """source_session_trigger_source and structured_evidence default to None and round-trip."""
    default = _make_finding()
    assert default.source_session_trigger_source is None
    assert default.structured_evidence is None

    evidence = {"session_id": str(uuid.uuid4()), "model": "claude-opus-4", "tool_call_count": 12}
    populated = _make_finding(source_session_trigger_source="healing", structured_evidence=evidence)
    assert populated.source_session_trigger_source == "healing"
    assert populated.structured_evidence == evidence


# ---------------------------------------------------------------------------
# insert_finding: persists new fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_finding_includes_source_session_trigger_source():
    """insert_finding passes source_session_trigger_source to the SQL."""
    pool = _make_qa_pool()
    # Use "qa" which is the real trigger_source emitted by QA investigation sessions.
    finding = _make_finding(source_session_trigger_source="qa")
    patrol_id = uuid.uuid4()

    await insert_finding(pool, patrol_id, finding, dedup_reason=None)

    call_args = pool.fetchval.call_args.args
    # source_session_trigger_source should be in the args
    assert "qa" in call_args


@pytest.mark.asyncio
async def test_insert_finding_includes_structured_evidence_as_json():
    """insert_finding passes structured_evidence as a dict for the asyncpg JSONB codec."""
    pool = _make_qa_pool()
    evidence = {"session_id": "abc-123", "trace_id": "def-456"}
    finding = _make_finding(structured_evidence=evidence)
    patrol_id = uuid.uuid4()

    await insert_finding(pool, patrol_id, finding, dedup_reason=None)

    call_args = pool.fetchval.call_args.args
    # structured_evidence is passed as a Python dict; the asyncpg JSONB codec
    # registered on the pool handles encoding (no json.dumps pre-serialization).
    assert evidence in call_args


@pytest.mark.asyncio
async def test_insert_finding_null_structured_evidence():
    """insert_finding passes None when structured_evidence is not set."""
    pool = _make_qa_pool()
    finding = _make_finding()
    patrol_id = uuid.uuid4()

    await insert_finding(pool, patrol_id, finding, dedup_reason=None)

    call_args = pool.fetchval.call_args.args
    # Last arg before or at end should be None for structured_evidence
    assert call_args[-1] is None


# ---------------------------------------------------------------------------
# QA self-recursion barrier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger_source", ["healing", "qa"])
async def test_qa_self_recursion_barrier_suppresses_qa_originated(trigger_source):
    """A QA-butler finding from a QA/healing-originated session is suppressed.

    QA investigation sessions use trigger_source="qa" and healing sessions
    "healing"; a finding whose source_session_trigger_source is either indicates
    a recursive case that the barrier must block at Gate 0.
    """
    finding = _make_finding(
        source_butler="qa",
        source_session_trigger_source=trigger_source,
    )
    result = await dispatch_qa_investigation(
        pool=_make_qa_pool(),
        triaged_finding=_make_triaged(finding),
        patrol_id=uuid.uuid4(),
        config=QaDispatchConfig(),
        repo_root=Path("/tmp/repo"),
        spawner=MagicMock(),
        gh_token=None,
    )
    assert result.accepted is False
    assert result.reason == "qa_self_recursion"


@pytest.mark.asyncio
async def test_qa_self_recursion_barrier_unknown_trigger_routes_to_meta_review(caplog):
    """QA finding from QA butler with unrecognized trigger_source routes to meta-review."""
    import logging

    finding = _make_finding(
        source_butler="qa",
        source_session_trigger_source=None,  # null trigger source
    )
    with caplog.at_level(logging.WARNING, logger="butlers.core.qa.dispatch"):
        result = await dispatch_qa_investigation(
            pool=_make_qa_pool(),
            triaged_finding=_make_triaged(finding),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )
    assert result.accepted is False
    assert result.reason == "qa_self_recursion_precaution"
    assert "unrecognized trigger_source" in caplog.text


@pytest.mark.asyncio
async def test_qa_self_recursion_barrier_non_qa_butler_not_affected():
    """Non-QA butler findings are not affected by the self-recursion barrier."""
    # The barrier should not apply — dispatch should proceed past Gate 0.
    # We stop at Gate 5 (severity) to avoid needing full DB/spawner mocking.
    # Severity 3 > threshold 2 => rejected at Gate 5 (not Gate 0).
    finding_high_sev = _make_finding(
        source_butler="finance",
        severity=3,
        source_session_trigger_source="healing",
    )
    result = await dispatch_qa_investigation(
        pool=_make_qa_pool(),
        triaged_finding=_make_triaged(finding_high_sev),
        patrol_id=uuid.uuid4(),
        config=QaDispatchConfig(severity_threshold=2),
        repo_root=Path("/tmp/repo"),
        spawner=MagicMock(),
        gh_token=None,
    )
    # Should be rejected at severity gate, NOT qa_self_recursion
    assert result.accepted is False
    assert result.reason == "severity_above_threshold"


# ---------------------------------------------------------------------------
# Healing dispatch: record_phase_session on successful spawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healing_dispatch_records_phase_session_on_success():
    """_run_healing_session calls record_phase_session when session is launched."""
    pool = _make_qa_pool()
    spawner = _make_spawner(success=True)
    session_id = spawner.trigger.return_value.session_id
    attempt_id = uuid.uuid4()
    phase_session_id = uuid.uuid4()

    with (
        patch(
            "butlers.core.healing.dispatch.record_phase_session",
            new_callable=AsyncMock,
            return_value=phase_session_id,
        ) as mock_record_phase,
        patch(
            "butlers.core.healing.dispatch.update_phase_session_status",
            new_callable=AsyncMock,
        ) as mock_update_phase,
        patch(
            "butlers.core.healing.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.healing.dispatch._is_unfixable",
            return_value=False,
        ),
        patch(
            "butlers.core.healing.dispatch._create_pr",
            new_callable=AsyncMock,
            return_value=("https://github.com/org/repo/pull/1", 1, None),
        ),
        patch(
            "butlers.core.healing.dispatch.get_attempt",
            new_callable=AsyncMock,
            return_value={"created_at": None, "session_ids": []},
        ),
        patch(
            "butlers.core.healing.dispatch.remove_healing_worktree",
            new_callable=AsyncMock,
        ),
    ):
        await _run_healing_session(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            branch_name="healing/fix-abc",
            worktree_path=Path("/tmp/wt"),
            fp=_make_fp(),
            butler_name="finance",
            trigger_source="external",
            agent_context=None,
            config=_make_healing_config(),
            spawner=spawner,
            gh_token=None,
        )

    # record_phase_session must have been called with the correct phase label
    mock_record_phase.assert_called_once()
    call_kwargs = mock_record_phase.call_args
    assert call_kwargs.args[2] == "diagnose_and_fix"  # phase
    assert call_kwargs.args[3] == session_id  # session_id

    # update_phase_session_status should have been called with "completed" on success
    mock_update_phase.assert_called_once()
    assert mock_update_phase.call_args.args[2] == "completed"


@pytest.mark.asyncio
async def test_healing_dispatch_marks_phase_session_failed_on_agent_failure():
    """_run_healing_session marks phase session as failed when agent fails."""
    pool = _make_qa_pool()
    spawner = _make_spawner(success=False)
    spawner.trigger.return_value.error = "agent crashed"
    attempt_id = uuid.uuid4()
    phase_session_id = uuid.uuid4()

    with (
        patch(
            "butlers.core.healing.dispatch.record_phase_session",
            new_callable=AsyncMock,
            return_value=phase_session_id,
        ),
        patch(
            "butlers.core.healing.dispatch.update_phase_session_status",
            new_callable=AsyncMock,
        ) as mock_update_phase,
        patch(
            "butlers.core.healing.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.healing.dispatch.remove_healing_worktree",
            new_callable=AsyncMock,
        ),
    ):
        await _run_healing_session(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            branch_name="healing/fix-abc",
            worktree_path=Path("/tmp/wt"),
            fp=_make_fp(),
            butler_name="finance",
            trigger_source="external",
            agent_context=None,
            config=_make_healing_config(),
            spawner=spawner,
            gh_token=None,
        )

    # Phase session should be marked failed
    mock_update_phase.assert_called_once()
    assert mock_update_phase.call_args.args[2] == "failed"


# ---------------------------------------------------------------------------
# QA dispatch: record_phase_session on successful spawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_dispatch_records_phase_session_on_success():
    """_run_investigation_session calls record_phase_session when session is launched."""
    pool = _make_qa_pool()
    spawner = _make_spawner(success=True)
    session_id = spawner.trigger.return_value.session_id
    attempt_id = uuid.uuid4()
    finding = _make_finding()
    phase_session_id = uuid.uuid4()

    with (
        patch(
            "butlers.core.qa.dispatch.record_phase_session",
            new_callable=AsyncMock,
            return_value=phase_session_id,
        ) as mock_record_phase,
        patch(
            "butlers.core.qa.dispatch.update_phase_session_status",
            new_callable=AsyncMock,
        ) as mock_update_phase,
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.qa.dispatch._UNFIXABLE_FILE",
            new="UNFIXABLE",
        ),
        patch(
            "butlers.core.qa.dispatch._create_qa_pr",
            new_callable=AsyncMock,
            return_value=("https://github.com/org/repo/pull/2", 2, None, None),
        ),
        patch(
            "butlers.core.qa.dispatch.remove_healing_worktree",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.qa.dispatch.build_investigation_prompt",
            return_value="investigate this",
        ),
        patch(
            "butlers.core.qa.dispatch.build_sandbox_env",
            return_value={},
        ),
    ):
        await _run_investigation_session(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            finding_id=uuid.uuid4(),
            branch_name="qa/fix-abc",
            worktree_path=Path("/tmp/wt"),
            finding=finding,
            config=QaDispatchConfig(),
            spawner=spawner,
            gh_token=None,
        )

    # record_phase_session must have been called with "investigate" phase
    mock_record_phase.assert_called_once()
    call_args = mock_record_phase.call_args
    assert call_args.args[2] == "investigate"  # phase
    assert call_args.args[3] == session_id  # session_id

    # update_phase_session_status should be called with "completed" on success
    mock_update_phase.assert_called_once()
    assert mock_update_phase.call_args.args[2] == "completed"


@pytest.mark.asyncio
async def test_qa_dispatch_marks_phase_session_failed_on_agent_failure():
    """_run_investigation_session marks phase session as failed when agent fails."""
    pool = _make_qa_pool()
    spawner = _make_spawner(success=False)
    spawner.trigger.return_value.error = "agent crashed"
    attempt_id = uuid.uuid4()
    phase_session_id = uuid.uuid4()

    with (
        patch(
            "butlers.core.qa.dispatch.record_phase_session",
            new_callable=AsyncMock,
            return_value=phase_session_id,
        ),
        patch(
            "butlers.core.qa.dispatch.update_phase_session_status",
            new_callable=AsyncMock,
        ) as mock_update_phase,
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.qa.dispatch.remove_healing_worktree",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.qa.dispatch.build_investigation_prompt",
            return_value="investigate this",
        ),
        patch(
            "butlers.core.qa.dispatch.build_sandbox_env",
            return_value={},
        ),
    ):
        await _run_investigation_session(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            finding_id=uuid.uuid4(),
            branch_name="qa/fix-abc",
            worktree_path=Path("/tmp/wt"),
            finding=_make_finding(),
            config=QaDispatchConfig(),
            spawner=spawner,
            gh_token=None,
        )

    # Phase session should be marked failed
    mock_update_phase.assert_called_once()
    assert mock_update_phase.call_args.args[2] == "failed"


# ---------------------------------------------------------------------------
# Workflow deadline vs per-session timeout are distinct
# ---------------------------------------------------------------------------


def test_workflow_deadline_distinct_from_session_timeout():
    """HealingConfig.timeout_minutes is the per-session limit; workflow_deadline is set at creation.

    This is a structural/contract test: the workflow_deadline_at column is set
    via create_or_join_attempt(workflow_deadline_minutes=N), which defaults to 60,
    while the session watchdog uses config.timeout_minutes (default 30).
    These two values are independent — the workflow can span multiple sessions.
    """
    # The per-session watchdog timeout
    config = _make_healing_config(timeout_minutes=30)
    assert config.timeout_minutes == 30

    # The workflow deadline is a separate parameter to create_or_join_attempt,
    # defaulting to 60 minutes. Verify the default is documented in tracking.
    sig = inspect.signature(create_or_join_attempt)
    param = sig.parameters.get("workflow_deadline_minutes")
    assert param is not None, "workflow_deadline_minutes param must exist"
    assert param.default == 60, "workflow_deadline_minutes must default to 60"

    # Per-session timeout (30) != workflow deadline (60) confirms they're distinct
    assert config.timeout_minutes != param.default


# ---------------------------------------------------------------------------
# Phase session tracking: record_phase_session updates healing_session_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_phase_session_updates_parent_healing_session_id():
    """record_phase_session atomically updates current_phase and healing_session_id on parent."""
    from butlers.core.healing.tracking import record_phase_session

    attempt_id = uuid.uuid4()
    session_id = uuid.uuid4()
    child_id = uuid.uuid4()

    # Mock pool with transaction support
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=child_id)
    mock_conn.execute = AsyncMock()

    mock_transaction = AsyncMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
    mock_transaction.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_transaction)

    mock_pool = MagicMock()
    mock_pool_context = AsyncMock()
    mock_pool_context.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool_context.__aexit__ = AsyncMock(return_value=False)
    mock_pool.acquire = MagicMock(return_value=mock_pool_context)

    result = await record_phase_session(mock_pool, attempt_id, "diagnose", session_id)

    assert result == child_id

    # Child insert carries the phase label; parent update happens exactly once
    # (atomic current_phase + healing_session_id update).
    assert "diagnose" in mock_conn.fetchval.call_args.args
    assert mock_conn.execute.call_count == 1
