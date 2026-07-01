"""Tests for butlers.core.qa.sources.session_records.SessionRecordsSource.

Covers:
- DiscoverySource protocol compliance
- Health check: validates view accessibility before querying
- SQL query: correct lookback window passed as parameter
- Finding construction from session rows
- Fingerprint compatibility: existing healing_fingerprint is used if valid
- Fingerprint computation: computes fresh fingerprint when healing_fingerprint absent
- Aggregation: multiple rows with same fingerprint → one finding with occurrence_count
- Error propagation: asyncpg.PostgresError raised from health check and query
- anonymize() applied to event_summary
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.core.qa.sources.protocol import DiscoverySource
from butlers.core.qa.sources.session_records import SessionRecordsSource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_asyncpg_record(
    source_butler: str = "finance",
    session_id: uuid.UUID | None = None,
    error: str | None = "ValueError: something went wrong",
    healing_fingerprint: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    status: str = "error",
    trigger_source: str | None = None,
) -> MagicMock:
    """Build a mock asyncpg Record for v_qa_recent_failures."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: {
        "source_butler": source_butler,
        "session_id": session_id or uuid.uuid4(),
        "error": error,
        "healing_fingerprint": healing_fingerprint,
        "started_at": started_at or (datetime.now(UTC) - timedelta(minutes=5)),
        "completed_at": completed_at or datetime.now(UTC),
        "status": status,
        "trigger_source": trigger_source,
    }[key]
    return record


def _make_source(pool: AsyncMock | None = None) -> SessionRecordsSource:
    if pool is None:
        pool = AsyncMock(spec=asyncpg.Pool)
        pool.execute = AsyncMock(return_value=None)
        pool.fetch = AsyncMock(return_value=[])
    return SessionRecordsSource(pool=pool, repo_root=Path("/tmp"))


# ---------------------------------------------------------------------------
# Protocol compliance and health check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_and_query_behavior():
    """Protocol compliance; health check runs before main query; failure propagates; lookback passed correctly; empty view returns []."""
    import inspect

    mock_pool = MagicMock()
    source0 = SessionRecordsSource(pool=mock_pool)
    assert isinstance(source0, DiscoverySource)
    assert source0.name == "session_records"
    assert inspect.iscoroutinefunction(source0.discover)

    # Health check called first; fetch called after
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    source = SessionRecordsSource(pool=pool)
    await source.discover(lookback_minutes=15)
    assert pool.execute.called
    health_call_sql = pool.execute.call_args[0][0]
    assert "v_qa_recent_failures" in health_call_sql
    assert "LIMIT 0" in health_call_sql

    # Health check failure propagates; fetch not called
    pool2 = AsyncMock(spec=asyncpg.Pool)
    pool2.execute = AsyncMock(side_effect=asyncpg.PostgresError("permission denied"))
    source2 = SessionRecordsSource(pool=pool2)
    with pytest.raises(asyncpg.PostgresError):
        await source2.discover(lookback_minutes=15)
    assert not pool2.fetch.called

    # Lookback window passed as timestamp parameter
    pool3 = AsyncMock(spec=asyncpg.Pool)
    pool3.execute = AsyncMock(return_value=None)
    pool3.fetch = AsyncMock(return_value=[])
    source3 = SessionRecordsSource(pool=pool3)
    await source3.discover(lookback_minutes=30)
    fetch_call = pool3.fetch.call_args
    cutoff_arg = fetch_call[0][1]
    now = datetime.now(UTC)
    expected_cutoff = now - timedelta(minutes=30)
    assert abs((cutoff_arg - expected_cutoff).total_seconds()) < 5

    # Empty view returns []
    assert await _make_source().discover(lookback_minutes=15) == []


@pytest.mark.asyncio
async def test_finding_construction_fingerprint_and_aggregation():
    """Finding correctly constructed from row; healing_fingerprint used if valid; computed when missing/invalid; aggregated by fingerprint; different fps separate."""
    # Basic finding construction
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    now = datetime.now(UTC)
    row = _make_asyncpg_record(
        source_butler="travel",
        error="ConnectionError: failed to reach API",
        status="error",
        completed_at=now,
    )
    pool.fetch = AsyncMock(return_value=[row])
    source = SessionRecordsSource(pool=pool, repo_root=Path("/tmp"))
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    f = findings[0]
    assert f.source_type == "session_records"
    assert f.source_butler == "travel"
    assert len(f.fingerprint) == 64
    assert f.occurrence_count == 1
    assert f.severity >= 0

    # Uses existing healing_fingerprint
    pool2 = AsyncMock(spec=asyncpg.Pool)
    pool2.execute = AsyncMock(return_value=None)
    known_fp = "a" * 64
    pool2.fetch = AsyncMock(
        return_value=[_make_asyncpg_record(healing_fingerprint=known_fp, error="some error")]
    )
    findings2 = await SessionRecordsSource(pool=pool2).discover(lookback_minutes=15)
    assert len(findings2) == 1
    assert findings2[0].fingerprint == known_fp

    # Computes fingerprint when None or too short
    for bad_fp in (None, "short"):
        pool3 = AsyncMock(spec=asyncpg.Pool)
        pool3.execute = AsyncMock(return_value=None)
        pool3.fetch = AsyncMock(
            return_value=[
                _make_asyncpg_record(healing_fingerprint=bad_fp, error="ValueError: test")
            ]
        )
        findings3 = await SessionRecordsSource(pool=pool3).discover(lookback_minutes=15)
        assert len(findings3) == 1
        assert len(findings3[0].fingerprint) == 64

    # Same fingerprint aggregated
    pool4 = AsyncMock(spec=asyncpg.Pool)
    pool4.execute = AsyncMock(return_value=None)
    shared_fp = "b" * 64
    rows4 = [
        _make_asyncpg_record(
            healing_fingerprint=shared_fp, completed_at=now - timedelta(minutes=10)
        ),
        _make_asyncpg_record(
            healing_fingerprint=shared_fp, completed_at=now - timedelta(minutes=5)
        ),
        _make_asyncpg_record(healing_fingerprint=shared_fp, completed_at=now),
    ]
    pool4.fetch = AsyncMock(return_value=rows4)
    findings4 = await SessionRecordsSource(pool=pool4).discover(lookback_minutes=15)
    assert len(findings4) == 1
    assert findings4[0].occurrence_count == 3

    # Different fingerprints → separate findings
    pool5 = AsyncMock(spec=asyncpg.Pool)
    pool5.execute = AsyncMock(return_value=None)
    pool5.fetch = AsyncMock(
        return_value=[
            _make_asyncpg_record(healing_fingerprint="a" * 64, error="error A"),
            _make_asyncpg_record(healing_fingerprint="b" * 64, error="error B"),
        ]
    )
    findings5 = await SessionRecordsSource(pool=pool5).discover(lookback_minutes=15)
    assert len(findings5) == 2

    # Status maps to synthetic exception_type
    for status, expected_type in [
        ("timeout", "SessionTimeoutError"),
        ("crash", "SessionCrashError"),
    ]:
        pool6 = AsyncMock(spec=asyncpg.Pool)
        pool6.execute = AsyncMock(return_value=None)
        pool6.fetch = AsyncMock(
            return_value=[_make_asyncpg_record(status=status, error=None, healing_fingerprint=None)]
        )
        findings6 = await SessionRecordsSource(pool=pool6).discover(lookback_minutes=15)
        assert findings6[0].exception_type == expected_type


@pytest.mark.asyncio
async def test_orphaned_daemon_restart_rows_are_not_actionable_findings():
    """Startup recovery markers are not application failures for QA to dispatch."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(
        return_value=[
            _make_asyncpg_record(
                source_butler="general",
                error="orphaned: daemon restart",
                status="error",
                healing_fingerprint=None,
            )
        ]
    )

    findings = await SessionRecordsSource(pool=pool, repo_root=Path("/tmp")).discover(
        lookback_minutes=15
    )

    assert findings == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        (
            "RuntimeError: token_budget_exceeded: session consumed 310,000 input tokens, "
            "exceeding budget of 300,000 (+10,000 over)"
        ),
        "RuntimeError: tool_call_budget_exceeded: session made 51 tool calls",
        "RuntimeError: degenerate_tool_loop: same tool call repeated",
    ],
)
async def test_guardrail_termination_rows_are_not_actionable_findings(error: str):
    """Spawner guardrail stops are controlled policy outcomes, not QA code-fix findings."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(
        return_value=[
            _make_asyncpg_record(
                source_butler="relationship",
                error=error,
                status="error",
                healing_fingerprint=None,
            )
        ]
    )

    findings = await SessionRecordsSource(pool=pool, repo_root=Path("/tmp")).discover(
        lookback_minutes=15
    )

    assert findings == []


@pytest.mark.asyncio
async def test_switchboard_classification_timeout_rows_are_not_actionable_findings():
    """Switchboard classifier timeout rows already degrade through routing fallback."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(
        return_value=[
            _make_asyncpg_record(
                source_butler="switchboard",
                error=(
                    "TimeoutError: Session timed out after 30s "
                    "(model=gpt-5.4-mini, butler=switchboard)"
                ),
                status="timeout",
                trigger_source="tick",
                healing_fingerprint=None,
            )
        ]
    )

    findings = await SessionRecordsSource(pool=pool, repo_root=Path("/tmp")).discover(
        lookback_minutes=15
    )

    assert findings == []


@pytest.mark.asyncio
async def test_non_classification_switchboard_timeout_rows_still_actionable():
    """Manual switchboard runtime timeouts are not hidden by the classifier filter."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(
        return_value=[
            _make_asyncpg_record(
                source_butler="switchboard",
                error=(
                    "TimeoutError: Session timed out after 1800s "
                    "(model=gpt-5.4-mini, butler=switchboard)"
                ),
                status="timeout",
                trigger_source="trigger",
                healing_fingerprint=None,
            )
        ]
    )

    findings = await SessionRecordsSource(pool=pool, repo_root=Path("/tmp")).discover(
        lookback_minutes=15
    )

    assert len(findings) == 1


@pytest.mark.asyncio
async def test_long_tick_switchboard_timeout_rows_still_actionable():
    """The classifier suppression is capped to short routing timeouts."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(
        return_value=[
            _make_asyncpg_record(
                source_butler="switchboard",
                error=(
                    "TimeoutError: Session timed out after 1800s "
                    "(model=gpt-5.4-mini, butler=switchboard)"
                ),
                status="timeout",
                trigger_source="tick",
                healing_fingerprint=None,
            )
        ]
    )

    findings = await SessionRecordsSource(pool=pool, repo_root=Path("/tmp")).discover(
        lookback_minutes=15
    )

    assert len(findings) == 1


@pytest.mark.asyncio
async def test_postgres_error_and_anonymization():
    """PostgresError from main query propagates; event_summary anonymized to strip PII."""
    # Error propagation
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(side_effect=asyncpg.PostgresError("query failed"))
    with pytest.raises(asyncpg.PostgresError):
        await SessionRecordsSource(pool=pool).discover(lookback_minutes=15)

    # Anonymization
    pool2 = AsyncMock(spec=asyncpg.Pool)
    pool2.execute = AsyncMock(return_value=None)
    row2 = _make_asyncpg_record(
        error="Failed to process message from user@test.example.com", healing_fingerprint=None
    )
    pool2.fetch = AsyncMock(return_value=[row2])
    findings2 = await SessionRecordsSource(pool=pool2, repo_root=Path("/tmp")).discover(
        lookback_minutes=15
    )
    assert len(findings2) == 1
    assert "user@test.example.com" not in findings2[0].event_summary


@pytest.mark.asyncio
async def test_structured_evidence_populated():
    """structured_evidence is populated with source, status, and session_ids."""
    now = datetime.now(UTC)
    sid1 = uuid.uuid4()
    sid2 = uuid.uuid4()
    shared_fp = "c" * 64

    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    rows = [
        _make_asyncpg_record(
            session_id=sid1,
            healing_fingerprint=shared_fp,
            status="error",
            completed_at=now - timedelta(minutes=5),
        ),
        _make_asyncpg_record(
            session_id=sid2,
            healing_fingerprint=shared_fp,
            status="error",
            completed_at=now,
        ),
    ]
    pool.fetch = AsyncMock(return_value=rows)
    source = SessionRecordsSource(pool=pool, repo_root=Path("/tmp"))
    findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    ev = findings[0].structured_evidence
    assert ev is not None
    assert ev["source"] == "session_records"
    assert ev["status"] == "error"
    # Both session IDs collected (within the cap of 5)
    assert str(sid1) in ev["session_ids"]
    assert str(sid2) in ev["session_ids"]


@pytest.mark.asyncio
async def test_structured_evidence_session_id_cap():
    """session_ids in structured_evidence is capped at _MAX_EVIDENCE_SESSION_IDS (5)."""
    from butlers.core.qa.sources.session_records import _MAX_EVIDENCE_SESSION_IDS

    shared_fp = "d" * 64
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    now = datetime.now(UTC)
    rows = [
        _make_asyncpg_record(
            session_id=uuid.uuid4(),
            healing_fingerprint=shared_fp,
            completed_at=now - timedelta(minutes=i),
        )
        for i in range(_MAX_EVIDENCE_SESSION_IDS + 3)  # more than cap
    ]
    pool.fetch = AsyncMock(return_value=rows)
    source = SessionRecordsSource(pool=pool, repo_root=Path("/tmp"))
    findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    ev = findings[0].structured_evidence
    assert ev is not None
    assert len(ev["session_ids"]) == _MAX_EVIDENCE_SESSION_IDS
