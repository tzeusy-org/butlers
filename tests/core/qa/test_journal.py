"""Tests for QA investigation journal helpers and flagged event emission."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.dispatch import QaDispatchConfig, dispatch_qa_investigation
from butlers.core.qa.journal import record_event, record_patrol_tick_events
from butlers.core.qa.models import QaFinding
from butlers.core.qa.triage import TriagedFinding

pytestmark = pytest.mark.unit


def _make_finding() -> QaFinding:
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=uuid.uuid4().hex * 2,
        source_type="log_scanner",
        source_butler="finance",
        severity=1,
        exception_type="ValueError",
        event_summary="Test event",
        call_site="module:1",
        occurrence_count=3,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


def _make_triaged(*, dedup_reason: str | None = None) -> TriagedFinding:
    return TriagedFinding(
        finding=_make_finding(),
        dedup_reason=dedup_reason,
        finding_id=uuid.uuid4(),
    )


def _make_pool() -> MagicMock:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool


@pytest.mark.asyncio
async def test_record_event_helper_inserts_uuid7_event() -> None:
    session = MagicMock()
    session.fetchval = AsyncMock()
    attempt_id = uuid.uuid4()
    finding_id = uuid.uuid4()
    ts = datetime(2026, 5, 15, 4, 30, tzinfo=UTC)

    event_id = await record_event(
        session,
        attempt_id=attempt_id,
        finding_id=finding_id,
        step="flagged",
        text="Failure spotted",
        detail="ValueError at module:1",
        data={"fingerprint": "a" * 64},
        ts=ts,
    )

    assert event_id.version == 7
    session.fetchval.assert_awaited_once()
    sql, *params = session.fetchval.await_args.args
    assert "INSERT INTO public.qa_investigation_events" in sql
    assert params == [
        event_id,
        str(attempt_id),
        str(finding_id),
        ts,
        "flagged",
        "Failure spotted",
        "ValueError at module:1",
        '{"fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
    ]


@pytest.mark.asyncio
async def test_record_event_helper_rejects_unknown_step() -> None:
    session = MagicMock()
    session.fetchval = AsyncMock()

    with pytest.raises(ValueError, match="Unknown QA journal step"):
        await record_event(
            session,
            attempt_id=uuid.uuid4(),
            step="unknown",
            text="Nope",
        )

    session.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_tick_emitted_when_case_unchanged() -> None:
    session = MagicMock()
    attempt_id = uuid.uuid4()
    patrol_id = uuid.uuid4()
    patrol_started_at = datetime(2026, 5, 15, 5, 0, tzinfo=UTC)
    tick_ts = datetime(2026, 5, 15, 5, 12, tzinfo=UTC)
    session.fetch = AsyncMock(
        return_value=[
            {
                "id": attempt_id,
                "status": "investigating",
                "created_at": datetime(2026, 5, 15, 5, 0, tzinfo=UTC),
                "detected_at": datetime(2026, 5, 15, 4, 42, tzinfo=UTC),
                "current_phase": "reproduce",
                "pr_number": None,
                "review_state": None,
                "last_follow_up_status": None,
            }
        ]
    )
    session.execute = AsyncMock()

    event_ids = await record_patrol_tick_events(
        session,
        patrol_id=patrol_id,
        patrol_started_at=patrol_started_at,
        ts=tick_ts,
    )

    assert len(event_ids) == 1
    assert event_ids[0].version == 7
    session.fetch.assert_awaited_once()
    fetch_sql, statuses, since = session.fetch.await_args.args
    assert "NOT EXISTS" in fetch_sql
    assert "e.ts >= $2" in fetch_sql
    assert "h.closed_at IS NULL" in fetch_sql
    assert "COALESCE(MIN(f.first_seen), h.created_at) AS detected_at" in fetch_sql
    assert "LEFT JOIN public.qa_findings f ON f.healing_attempt_id = h.id" in fetch_sql
    assert "GROUP BY h.id" in fetch_sql
    assert statuses == ["investigating", "pr_open", "dispatch_pending"]
    assert since == patrol_started_at

    session.execute.assert_awaited_once()
    insert_sql, ids, attempt_ids, ts_values, steps, texts, details, data_values = (
        session.execute.await_args.args
    )
    assert "INSERT INTO public.qa_investigation_events" in insert_sql
    assert "unnest" in insert_sql
    assert ids == event_ids
    assert attempt_ids == [attempt_id]
    assert ts_values == [tick_ts]
    assert steps == ["tick"]
    assert texts == [f"patrol cycle {str(patrol_id).split('-', 1)[0]} - case still investigating"]
    assert details == ["investigation ongoing for 30m; current phase reproduce"]
    assert [json.loads(value) for value in data_values] == [
        {"patrol_id": str(patrol_id), "status": "investigating", "case_age": "30m"}
    ]


@pytest.mark.asyncio
async def test_tick_age_handles_naive_timestamps() -> None:
    session = MagicMock()
    attempt_id = uuid.uuid4()
    patrol_id = uuid.uuid4()
    session.fetch = AsyncMock(
        return_value=[
            {
                "id": attempt_id,
                "status": "dispatch_pending",
                "created_at": datetime(2026, 5, 15, 5, 0),
                "detected_at": datetime(2026, 5, 15, 5, 0),
                "current_phase": None,
                "pr_number": None,
                "review_state": None,
                "last_follow_up_status": None,
            }
        ]
    )
    session.execute = AsyncMock()

    await record_patrol_tick_events(
        session,
        patrol_id=patrol_id,
        patrol_started_at=datetime(2026, 5, 15, 5, 0),
        ts=datetime(2026, 5, 15, 5, 5),
    )

    *_, details, data_values = session.execute.await_args.args
    assert details == ["awaiting dispatch for 5m"]
    assert [json.loads(value) for value in data_values] == [
        {"patrol_id": str(patrol_id), "status": "dispatch_pending", "case_age": "5m"}
    ]


@pytest.mark.asyncio
async def test_tick_not_emitted_when_other_events_fired() -> None:
    session = MagicMock()
    session.fetch = AsyncMock(return_value=[])
    session.execute = AsyncMock()

    event_ids = await record_patrol_tick_events(
        session,
        patrol_id=uuid.uuid4(),
        patrol_started_at=datetime(2026, 5, 15, 5, 0, tzinfo=UTC),
        ts=datetime(2026, 5, 15, 5, 5, tzinfo=UTC),
    )

    assert event_ids == []
    fetch_sql = session.fetch.await_args.args[0]
    assert "FROM public.qa_investigation_events e" in fetch_sql
    assert "e.attempt_id = h.id" in fetch_sql
    assert "e.ts >= $2" in fetch_sql
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_tick_not_emitted_for_terminal_cases() -> None:
    session = MagicMock()
    session.fetch = AsyncMock(return_value=[])
    session.execute = AsyncMock()

    event_ids = await record_patrol_tick_events(
        session,
        patrol_id=uuid.uuid4(),
        patrol_started_at=datetime(2026, 5, 15, 5, 0, tzinfo=UTC),
        ts=datetime(2026, 5, 15, 5, 5, tzinfo=UTC),
    )

    assert event_ids == []
    fetch_sql = session.fetch.await_args.args[0]
    assert "h.closed_at IS NULL" in fetch_sql
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_flagged_event_emitted_for_novel_finding_after_attempt_claim() -> None:
    attempt_id = uuid.uuid4()
    patrol_started_at = datetime(2026, 5, 15, 5, 0, tzinfo=UTC)
    triaged = _make_triaged()

    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch.get_recent_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            return_value=1,
        ),
        patch(
            "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("butlers.core.qa.dispatch.resolve_model", new_callable=AsyncMock, return_value=None),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._fetch_patrol_started_at",
            new_callable=AsyncMock,
            return_value=patrol_started_at,
        ),
        patch("butlers.core.qa.dispatch.record_event", new_callable=AsyncMock) as record,
    ):
        result = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=triaged,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False and result.reason == "no_model"
    record.assert_awaited_once()
    assert record.await_args.kwargs["attempt_id"] == attempt_id
    assert record.await_args.kwargs["finding_id"] == triaged.finding_id
    assert record.await_args.kwargs["step"] == "flagged"
    assert record.await_args.kwargs["text"] == triaged.finding.event_summary
    assert record.await_args.kwargs["ts"] == patrol_started_at
    assert record.await_args.kwargs["data"]["fingerprint"] == triaged.finding.fingerprint


@pytest.mark.asyncio
async def test_flagged_event_not_emitted_for_deduplicated_finding() -> None:
    attempt_id = uuid.uuid4()

    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, False),
        ),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.update_finding_dedup_reason", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.create_dispatch_event", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.record_event", new_callable=AsyncMock) as record,
    ):
        result = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=_make_triaged(dedup_reason="active_investigation"),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False and result.reason == "already_investigating"
    record.assert_not_called()
