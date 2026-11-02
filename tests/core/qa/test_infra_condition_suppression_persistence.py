"""Persistence failures cannot masquerade as durable QA suppression."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from butlers.core.qa import dispatch
from butlers.core.qa.models import QaFinding
from butlers.core.qa.triage import TriagedFinding

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("failed_writers", [{"finding"}, {"event"}, {"finding", "event"}])
async def test_failed_suppression_is_honest_and_same_finding_retry_is_idempotent(
    monkeypatch, failed_writers
):
    now = datetime.now(UTC)
    finding = QaFinding(
        fingerprint="a" * 64,
        source_type="infra_state",
        source_butler="qa-test",
        severity=1,
        exception_type="SyntheticUnavailable",
        event_summary="Synthetic infrastructure unavailable",
        call_site="synthetic:1",
        occurrence_count=1,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )
    triaged = TriagedFinding(finding=finding, dedup_reason=None, finding_id=uuid4())
    condition = dict(
        state="open", escalation_level="L0", first_detected_at=now, summary="Synthetic"
    )
    faults = set(failed_writers)
    events = {}
    visible_reason = None

    async def update_reason(_sql, reason, finding_id):
        nonlocal visible_reason
        assert finding_id == triaged.finding_id
        if "finding" in faults:
            raise OSError("synthetic finding persistence unavailable")
        visible_reason = reason
        return "UPDATE 1"

    async def insert_event(_sql, *args):
        fp, butler, decision, reason, attempt = args[:5]
        event_id = UUID(str(args[5])) if len(args) == 6 else uuid4()
        inserted = event_id not in events
        events.setdefault(
            event_id,
            dict(
                fingerprint=fp,
                butler_name=butler,
                decision=decision,
                reason=reason,
                attempt_id=attempt,
            ),
        )
        # Model a committed event whose acknowledgement was lost, not merely a
        # failed insert: retry must discover that same durable decision.
        if "event" in faults:
            raise OSError("synthetic event acknowledgement lost")
        return event_id if inserted else None

    async def find_event(_sql, event_id):
        return events.get(UUID(str(event_id)))

    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=update_reason)
    pool.fetchval = AsyncMock(side_effect=insert_event)
    pool.fetchrow = AsyncMock(side_effect=find_event)
    claim, workspace, resolve_model = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(dispatch, "get_active_condition", AsyncMock(return_value=condition))
    monkeypatch.setattr(dispatch, "create_or_join_attempt", claim)
    monkeypatch.setattr(dispatch, "create_healing_worktree", workspace)
    monkeypatch.setattr(dispatch, "resolve_model", resolve_model)
    spawner, metrics = MagicMock(), MagicMock()

    async def invoke():
        return await dispatch.dispatch_qa_investigation(
            pool,
            triaged,
            uuid4(),
            dispatch.QaDispatchConfig(),
            Path("/unused"),
            spawner,
            metrics=metrics,
        )

    failed = await invoke()
    assert (failed.accepted, failed.reason, failed.attempt_id) == (False, "internal_error", None)
    metrics.record_recovery_dispatch_decision.assert_not_called()
    faults.clear()
    original_reason = None
    for _ in range(2):
        retried = await invoke()
        assert (retried.accepted, retried.reason, retried.attempt_id) == (
            False,
            "infra_condition_open",
            None,
        )
        event = next(iter(events.values()))
        if original_reason is None:
            original_reason = event["reason"]
            condition["summary"] = "Synthetic condition evidence updated"
        assert event["reason"] == original_reason
    assert visible_reason == "infra_condition_open"
    assert len(events) == 1
    event = next(iter(events.values()))
    assert event["decision"] == "infra_condition_open"
    assert event["attempt_id"] is None
    assert "Infrastructure condition already active" in event["reason"]
    event["fingerprint"] = "b" * 64
    misbound = await invoke()
    assert (misbound.reason, misbound.accepted, misbound.attempt_id) == (
        "internal_error",
        False,
        None,
    )
    assert len(events) == 1
    assert metrics.record_recovery_dispatch_decision.call_count == 2
    claim.assert_not_awaited()
    workspace.assert_not_awaited()
    resolve_model.assert_not_awaited()
    assert not spawner.mock_calls
