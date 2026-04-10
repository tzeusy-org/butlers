"""Tests for QA provenance propagation and dedup linkage (bu-0025a.12).

Covers:
- butler_reports source: trigger_source propagated to source_session_trigger_source
- session_records source: trigger_source read from view row and set on finding
- session_records aggregation: trigger_source from most recent (last_seen) row wins
- log_scanner source: trigger_source extracted from JSON log field
- report_finding MCP tool: trigger_source accepted and forwarded to accept()
- triage active-investigation dedup: insert_finding called with linked_attempt_id
- triage active-investigation dedup: linked_attempt_id set on TriagedFinding
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.models import QaFinding
from butlers.core.qa.sources.butler_reports import ButlerReportsSource
from butlers.core.qa.sources.log_scanner import LogScannerSource, _parse_log_line
from butlers.core.qa.sources.session_records import SessionRecordsSource
from butlers.core.qa.triage import triage_findings

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fp(seed: int = 0) -> str:
    return hex(seed)[2:].zfill(64)[:64]


def _make_finding(fingerprint=None, severity=2, occurrence_count=1, trigger_source=None):
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=fingerprint or uuid.uuid4().hex * 2,
        source_type="log_scanner",
        source_butler="finance",
        severity=severity,
        exception_type="ValueError",
        event_summary="Something went wrong",
        call_site="module:func",
        occurrence_count=occurrence_count,
        first_seen=now,
        last_seen=now,
        timestamp=now,
        source_session_trigger_source=trigger_source,
    )


def _make_asyncpg_record(
    source_butler: str = "finance",
    error: str | None = "ValueError: something went wrong",
    healing_fingerprint: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    status: str = "error",
    trigger_source: str | None = None,
) -> MagicMock:
    """Build a mock asyncpg Record with trigger_source for v_qa_recent_failures."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: {
        "source_butler": source_butler,
        "session_id": uuid.uuid4(),
        "error": error,
        "healing_fingerprint": healing_fingerprint,
        "started_at": started_at or (datetime.now(UTC) - timedelta(minutes=5)),
        "completed_at": completed_at or datetime.now(UTC),
        "status": status,
        "trigger_source": trigger_source,
    }[key]
    return record


# ---------------------------------------------------------------------------
# butler_reports: trigger_source propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_butler_reports_trigger_source_propagated():
    """accept() with trigger_source sets source_session_trigger_source on the finding."""
    source = ButlerReportsSource()
    await source.accept(
        fingerprint=_fp(1),
        exception_type="RuntimeError",
        call_site="core.qa:dispatch",
        severity=1,
        event_summary="Dispatch failed",
        source_butler="qa",
        trigger_source="healing",
    )
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    assert findings[0].source_session_trigger_source == "healing"


@pytest.mark.asyncio
async def test_butler_reports_trigger_source_none_when_absent():
    """accept() without trigger_source leaves source_session_trigger_source as None."""
    source = ButlerReportsSource()
    await source.accept(
        fingerprint=_fp(2),
        exception_type="RuntimeError",
        call_site="core.qa:dispatch",
        severity=2,
        event_summary="Some error",
        source_butler="finance",
    )
    findings = await source.discover(lookback_minutes=15)
    assert findings[0].source_session_trigger_source is None


@pytest.mark.asyncio
async def test_butler_reports_trigger_source_qa_investigation():
    """accept() with trigger_source='qa' is stored correctly (QA self-recursion scenario)."""
    source = ButlerReportsSource()
    await source.accept(
        fingerprint=_fp(3),
        exception_type="ValueError",
        call_site="qa.dispatch:run",
        severity=1,
        event_summary="QA investigation failed",
        source_butler="qa",
        trigger_source="qa",
    )
    findings = await source.discover(lookback_minutes=15)
    assert findings[0].source_session_trigger_source == "qa"


@pytest.mark.asyncio
async def test_butler_reports_accept_sync_trigger_source():
    """accept_sync() with trigger_source propagates to source_session_trigger_source."""
    source = ButlerReportsSource()
    source.accept_sync(
        fingerprint=_fp(4),
        exception_type="IOError",
        call_site="mod:func",
        severity=2,
        event_summary="IO error",
        source_butler="home",
        trigger_source="healing",
    )
    findings = await source.discover(lookback_minutes=15)
    assert findings[0].source_session_trigger_source == "healing"


# ---------------------------------------------------------------------------
# session_records: trigger_source propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_records_trigger_source_from_view():
    """SessionRecordsSource._process_row reads trigger_source from view row."""
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=None)

    row = _make_asyncpg_record(trigger_source="healing")
    mock_pool.fetch = AsyncMock(return_value=[row])

    source = SessionRecordsSource(pool=mock_pool, repo_root=Path("/tmp"))
    findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    assert findings[0].source_session_trigger_source == "healing"


@pytest.mark.asyncio
async def test_session_records_trigger_source_none_when_absent():
    """SessionRecordsSource._process_row sets trigger_source to None when view column is NULL."""
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=None)

    row = _make_asyncpg_record(trigger_source=None)
    mock_pool.fetch = AsyncMock(return_value=[row])

    source = SessionRecordsSource(pool=mock_pool, repo_root=Path("/tmp"))
    findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    assert findings[0].source_session_trigger_source is None


@pytest.mark.asyncio
async def test_session_records_trigger_source_aggregation_prefers_last_seen():
    """When multiple rows share a fingerprint, the trigger_source from the most recent row is used."""
    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock(return_value=None)

    now = datetime.now(UTC)
    fp = "a" * 64

    # Two rows with the same fingerprint: one older (None trigger_source), one newer ("healing")
    row_old = _make_asyncpg_record(
        healing_fingerprint=fp,
        completed_at=now - timedelta(minutes=5),
        trigger_source=None,
    )
    row_new = _make_asyncpg_record(
        healing_fingerprint=fp,
        completed_at=now - timedelta(minutes=1),
        trigger_source="healing",
    )
    mock_pool.fetch = AsyncMock(return_value=[row_old, row_new])

    source = SessionRecordsSource(pool=mock_pool, repo_root=Path("/tmp"))
    findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    assert findings[0].occurrence_count == 2
    assert findings[0].source_session_trigger_source == "healing"


# ---------------------------------------------------------------------------
# log_scanner: trigger_source extraction from JSON
# ---------------------------------------------------------------------------


def test_log_scanner_parse_log_line_extracts_trigger_source():
    """_parse_log_line extracts trigger_source from JSON log field when present."""
    now = datetime.now(UTC)
    line = json.dumps(
        {
            "level": "error",
            "event": "Dispatch failed",
            "timestamp": now.isoformat(),
            "butler_name": "qa",
            "trigger_source": "qa",
        }
    )
    entry = _parse_log_line(line, "qa")
    assert entry is not None
    assert entry.trigger_source == "qa"


def test_log_scanner_parse_log_line_trigger_source_absent():
    """_parse_log_line returns None trigger_source when field is absent from JSON."""
    now = datetime.now(UTC)
    line = json.dumps(
        {
            "level": "error",
            "event": "Some error",
            "timestamp": now.isoformat(),
            "butler_name": "finance",
        }
    )
    entry = _parse_log_line(line, "finance")
    assert entry is not None
    assert entry.trigger_source is None


@pytest.mark.asyncio
async def test_log_scanner_trigger_source_in_finding(tmp_path):
    """LogScannerSource propagates trigger_source from log entry to QaFinding."""
    log_dir = tmp_path / "butlers"
    log_dir.mkdir()

    now = datetime.now(UTC)
    log_line = json.dumps(
        {
            "level": "error",
            "event": "QA investigation failed with exception",
            "timestamp": now.isoformat(),
            "butler_name": "qa",
            "trigger_source": "healing",
            "exception": "ValueError",
        }
    )
    (log_dir / "qa.log").write_text(log_line + "\n")

    # qa.log is excluded by the scanner — use a different butler log
    (log_dir / "qa.log").unlink()
    (log_dir / "finance.log").write_text(
        log_line.replace('"butler_name": "qa"', '"butler_name": "finance"').replace(
            '"trigger_source": "healing"', '"trigger_source": "healing"'
        )
        + "\n"
    )

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    assert findings[0].source_session_trigger_source == "healing"


@pytest.mark.asyncio
async def test_log_scanner_trigger_source_none_when_absent_in_log(tmp_path):
    """LogScannerSource sets source_session_trigger_source=None when log has no trigger_source."""
    log_dir = tmp_path / "butlers"
    log_dir.mkdir()

    now = datetime.now(UTC)
    log_line = json.dumps(
        {
            "level": "error",
            "event": "Unexpected failure",
            "timestamp": now.isoformat(),
            "butler_name": "finance",
            "exception": "RuntimeError",
        }
    )
    (log_dir / "finance.log").write_text(log_line + "\n")

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    assert findings[0].source_session_trigger_source is None


# ---------------------------------------------------------------------------
# triage: active-investigation dedup linkage persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triage_active_investigation_dedup_persists_linked_attempt_id():
    """When Gate 1 fires (active investigation), insert_finding is called with linked_attempt_id."""
    pool = MagicMock()
    patrol_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    finding_id = uuid.uuid4()

    insert_calls = []

    async def capture_insert(p, pid, finding, dedup_reason, healing_attempt_id):
        insert_calls.append(
            {
                "dedup_reason": dedup_reason,
                "healing_attempt_id": healing_attempt_id,
            }
        )
        return finding_id

    with (
        patch("butlers.core.qa.triage.insert_finding", side_effect=capture_insert),
        patch(
            "butlers.core.qa.triage.get_active_attempt",
            new_callable=AsyncMock,
            return_value={"id": attempt_id},
        ),
        patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock),
        patch("butlers.core.qa.triage.get_recent_attempt", new_callable=AsyncMock),
    ):
        result = await triage_findings(pool, patrol_id, [_make_finding()])

    assert len(insert_calls) == 1
    assert insert_calls[0]["dedup_reason"] == "active_investigation"
    # linked_attempt_id must be passed — not None
    assert insert_calls[0]["healing_attempt_id"] == attempt_id

    # TriagedFinding.linked_attempt_id is also set correctly
    assert result.all_findings[0].linked_attempt_id == attempt_id
    assert result.all_findings[0].dedup_reason == "active_investigation"


@pytest.mark.asyncio
async def test_triage_novel_finding_insert_called_with_none_attempt_id():
    """Novel findings are inserted with healing_attempt_id=None (dispatcher sets it later)."""
    pool = MagicMock()
    patrol_id = uuid.uuid4()
    finding_id = uuid.uuid4()

    insert_calls = []

    async def capture_insert(p, pid, finding, dedup_reason, healing_attempt_id):
        insert_calls.append(healing_attempt_id)
        return finding_id

    with (
        patch("butlers.core.qa.triage.insert_finding", side_effect=capture_insert),
        patch(
            "butlers.core.qa.triage.get_active_attempt", new_callable=AsyncMock, return_value=None
        ),
        patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False),
        patch(
            "butlers.core.qa.triage.get_recent_attempt", new_callable=AsyncMock, return_value=None
        ),
    ):
        result = await triage_findings(pool, patrol_id, [_make_finding()])

    assert len(insert_calls) == 1
    assert insert_calls[0] is None  # dispatcher updates later
    assert result.novel_findings[0].is_novel is True


# ---------------------------------------------------------------------------
# report_finding MCP tool: trigger_source end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_finding_tool_passes_trigger_source():
    """QaModule._handle_report_finding forwards trigger_source to ButlerReportsSource.accept()."""
    # Import here to avoid module-level side effects in test collection
    from butlers.modules.qa import QaModule

    module = QaModule()
    source = ButlerReportsSource()
    module._butler_reports_source = source

    await module._handle_report_finding(
        fingerprint=_fp(10),
        exception_type="RuntimeError",
        call_site="mod:func",
        severity=1,
        event_summary="Some error",
        source_butler="qa",
        context=None,
        trigger_source="healing",
    )

    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    assert findings[0].source_session_trigger_source == "healing"


@pytest.mark.asyncio
async def test_report_finding_tool_trigger_source_none_default():
    """QaModule._handle_report_finding with no trigger_source sets source_session_trigger_source=None."""
    from butlers.modules.qa import QaModule

    module = QaModule()
    source = ButlerReportsSource()
    module._butler_reports_source = source

    await module._handle_report_finding(
        fingerprint=_fp(11),
        exception_type="ValueError",
        call_site="mod:func",
        severity=2,
        event_summary="Some error",
        source_butler="finance",
        context=None,
    )

    findings = await source.discover(lookback_minutes=15)
    assert findings[0].source_session_trigger_source is None


# ---------------------------------------------------------------------------
# Recursion-barrier integration: non-QA findings remain unsuppressed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_butler_reports_non_qa_butler_not_suppressed():
    """Findings from non-QA butlers are not affected by trigger_source (no self-recursion concern)."""
    source = ButlerReportsSource()

    # Even with trigger_source="healing", source_butler != "qa" means no self-recursion path
    await source.accept(
        fingerprint=_fp(20),
        exception_type="DatabaseError",
        call_site="db:query",
        severity=1,
        event_summary="DB query failed",
        source_butler="finance",  # not "qa"
        trigger_source="healing",
    )
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    # trigger_source is stored — the dispatch layer (Gate 0) checks source_butler separately
    assert findings[0].source_session_trigger_source == "healing"
    assert findings[0].source_butler == "finance"
