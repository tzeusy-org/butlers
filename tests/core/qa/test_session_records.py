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
# Protocol compliance
# ---------------------------------------------------------------------------


def test_session_records_implements_protocol():
    """SessionRecordsSource implements the DiscoverySource protocol."""
    mock_pool = MagicMock()
    source = SessionRecordsSource(pool=mock_pool)
    assert isinstance(source, DiscoverySource)
    assert source.name == "session_records"


def test_session_records_discover_is_async():
    """discover() is an async method."""
    import inspect

    mock_pool = MagicMock()
    source = SessionRecordsSource(pool=mock_pool)
    assert inspect.iscoroutinefunction(source.discover)


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
    }[key]
    return record


def _make_source(pool: AsyncMock | None = None) -> SessionRecordsSource:
    if pool is None:
        pool = AsyncMock(spec=asyncpg.Pool)
        pool.execute = AsyncMock(return_value=None)
        pool.fetch = AsyncMock(return_value=[])
    return SessionRecordsSource(pool=pool, repo_root=Path("/tmp"))


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_called_before_query():
    """Health check (LIMIT 0 query) is called before the main query."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])

    source = SessionRecordsSource(pool=pool)
    await source.discover(lookback_minutes=15)

    # execute is called for health check
    assert pool.execute.called
    health_call_sql = pool.execute.call_args[0][0]
    assert "v_qa_recent_failures" in health_call_sql
    assert "LIMIT 0" in health_call_sql


@pytest.mark.asyncio
async def test_health_check_failure_propagates():
    """PostgresError from health check propagates to caller."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(side_effect=asyncpg.PostgresError("permission denied"))

    source = SessionRecordsSource(pool=pool)
    with pytest.raises(asyncpg.PostgresError):
        await source.discover(lookback_minutes=15)

    # fetch should NOT be called after health check failure
    assert not pool.fetch.called


# ---------------------------------------------------------------------------
# SQL query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_uses_correct_lookback():
    """Lookback window is passed as a timestamp parameter to the query."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])

    source = SessionRecordsSource(pool=pool)
    await source.discover(lookback_minutes=30)

    fetch_call = pool.fetch.call_args
    # Second argument is the cutoff timestamp
    cutoff_arg = fetch_call[0][1]
    now = datetime.now(UTC)
    expected_cutoff = now - timedelta(minutes=30)
    # Allow 5-second tolerance
    assert abs((cutoff_arg - expected_cutoff).total_seconds()) < 5


@pytest.mark.asyncio
async def test_empty_view_returns_empty_list():
    """Empty result from view returns empty findings list."""
    source = _make_source()
    findings = await source.discover(lookback_minutes=15)
    assert findings == []


# ---------------------------------------------------------------------------
# Finding construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finding_constructed_from_row():
    """QaFinding is correctly constructed from a session row."""
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


@pytest.mark.asyncio
async def test_finding_uses_existing_healing_fingerprint():
    """When healing_fingerprint is present and valid, it is used as-is."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)

    known_fp = "a" * 64  # 64-char hex string
    row = _make_asyncpg_record(
        healing_fingerprint=known_fp,
        error="some error",
    )
    pool.fetch = AsyncMock(return_value=[row])

    source = SessionRecordsSource(pool=pool)
    findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    assert findings[0].fingerprint == known_fp


@pytest.mark.asyncio
async def test_finding_computes_fingerprint_when_healing_fingerprint_absent():
    """When healing_fingerprint is None, a fresh fingerprint is computed."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)

    row = _make_asyncpg_record(healing_fingerprint=None, error="ValueError: test")
    pool.fetch = AsyncMock(return_value=[row])

    source = SessionRecordsSource(pool=pool)
    findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    fp = findings[0].fingerprint
    assert len(fp) == 64
    # Should be deterministic for same input
    findings2 = await source.discover(lookback_minutes=15)
    assert findings2[0].fingerprint == fp


@pytest.mark.asyncio
async def test_finding_computes_fingerprint_when_healing_fingerprint_invalid():
    """When healing_fingerprint is wrong length, a fresh fingerprint is computed."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)

    row = _make_asyncpg_record(healing_fingerprint="short", error="test error")
    pool.fetch = AsyncMock(return_value=[row])

    source = SessionRecordsSource(pool=pool)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    # Should be 64 chars (not "short")
    assert len(findings[0].fingerprint) == 64


# ---------------------------------------------------------------------------
# Status-to-exception-type mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_status_maps_to_session_timeout_error():
    """Session with status='timeout' maps to SessionTimeoutError exception_type."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    row = _make_asyncpg_record(status="timeout", error=None, healing_fingerprint=None)
    pool.fetch = AsyncMock(return_value=[row])

    source = SessionRecordsSource(pool=pool)
    findings = await source.discover(lookback_minutes=15)
    assert findings[0].exception_type == "SessionTimeoutError"


@pytest.mark.asyncio
async def test_crash_status_maps_to_session_crash_error():
    """Session with status='crash' maps to SessionCrashError exception_type."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    row = _make_asyncpg_record(status="crash", error=None, healing_fingerprint=None)
    pool.fetch = AsyncMock(return_value=[row])

    source = SessionRecordsSource(pool=pool)
    findings = await source.discover(lookback_minutes=15)
    assert findings[0].exception_type == "SessionCrashError"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_fingerprint_aggregated():
    """Multiple rows with the same healing_fingerprint → one finding with occurrence_count > 1."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)

    now = datetime.now(UTC)
    shared_fp = "b" * 64
    rows = [
        _make_asyncpg_record(
            healing_fingerprint=shared_fp,
            completed_at=now - timedelta(minutes=10),
        ),
        _make_asyncpg_record(
            healing_fingerprint=shared_fp,
            completed_at=now - timedelta(minutes=5),
        ),
        _make_asyncpg_record(
            healing_fingerprint=shared_fp,
            completed_at=now,
        ),
    ]
    pool.fetch = AsyncMock(return_value=rows)

    source = SessionRecordsSource(pool=pool)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    assert findings[0].occurrence_count == 3


@pytest.mark.asyncio
async def test_different_fingerprints_not_aggregated():
    """Rows with different fingerprints produce separate findings."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)

    rows = [
        _make_asyncpg_record(healing_fingerprint="a" * 64, error="error A"),
        _make_asyncpg_record(healing_fingerprint="b" * 64, error="error B"),
    ]
    pool.fetch = AsyncMock(return_value=rows)

    source = SessionRecordsSource(pool=pool)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 2


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_error_from_query_propagates():
    """PostgresError from the main query propagates to caller."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(side_effect=asyncpg.PostgresError("query failed"))

    source = SessionRecordsSource(pool=pool)
    with pytest.raises(asyncpg.PostgresError):
        await source.discover(lookback_minutes=15)


# ---------------------------------------------------------------------------
# Anonymization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_summary_anonymized():
    """Event summary is passed through anonymize() to strip PII."""
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)

    # Error text contains an email address
    row = _make_asyncpg_record(
        error="Failed to process message from user@test.example.com",
        healing_fingerprint=None,
    )
    pool.fetch = AsyncMock(return_value=[row])

    source = SessionRecordsSource(pool=pool, repo_root=Path("/tmp"))
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    # Email should be redacted
    assert "user@test.example.com" not in findings[0].event_summary
