"""Tests for butlers.core.qa.sources.tool_call_failures.ToolCallFailuresSource.

Covers:
- DiscoverySource protocol compliance
- Health check runs before the main query; failure propagates
- A success=true session containing an outcome='error' tool call yields a finding
- A clean success session (no error tool calls) yields nothing
- Same-fingerprint rows aggregate into one finding with occurrence_count
- Dedup vs log_scanner: the finding fingerprint EQUALS the fingerprint
  log_scanner computes for the matching "MCP tool call failed (...)" log entry,
  so the triage layer coalesces them (no double-reporting)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.core.healing.fingerprint import (
    _compute_hash,
    _sanitize_message,
)
from butlers.core.qa.sources.protocol import DiscoverySource
from butlers.core.qa.sources.tool_call_failures import ToolCallFailuresSource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(
    source_butler: str = "finance",
    session_id: uuid.UUID | None = None,
    session_success: bool = True,
    tool_name: str = "search_transactions",
    module_name: str | None = "finance",
    error: str | None = "ValueError: bad query",
    trigger_source: str | None = "tick",
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> MagicMock:
    """Build a mock asyncpg Record for v_qa_tool_call_failures."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: {
        "source_butler": source_butler,
        "session_id": session_id or uuid.uuid4(),
        "session_success": session_success,
        "tool_name": tool_name,
        "module_name": module_name,
        "error": error,
        "trigger_source": trigger_source,
        "started_at": started_at or (datetime.now(UTC) - timedelta(minutes=5)),
        "completed_at": completed_at or datetime.now(UTC),
    }[key]
    return record


def _pool(rows: list | None = None) -> AsyncMock:
    pool = AsyncMock(spec=asyncpg.Pool)
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=rows or [])
    return pool


# ---------------------------------------------------------------------------
# Protocol + health check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_protocol_and_health_check():
    import inspect

    source = ToolCallFailuresSource(pool=MagicMock())
    assert isinstance(source, DiscoverySource)
    assert source.name == "tool_call_failures"
    assert inspect.iscoroutinefunction(source.discover)

    # Health check runs first and references the view.
    pool = _pool()
    await ToolCallFailuresSource(pool=pool).discover(lookback_minutes=15)
    assert pool.execute.called
    assert "v_qa_tool_call_failures" in pool.execute.call_args[0][0]
    assert "LIMIT 0" in pool.execute.call_args[0][0]

    # Health-check failure propagates and skips the main query.
    pool2 = AsyncMock(spec=asyncpg.Pool)
    pool2.execute = AsyncMock(side_effect=asyncpg.PostgresError("permission denied"))
    with pytest.raises(asyncpg.PostgresError):
        await ToolCallFailuresSource(pool=pool2).discover(lookback_minutes=15)
    assert not pool2.fetch.called

    # Lookback passed as timestamp param.
    pool3 = _pool()
    await ToolCallFailuresSource(pool=pool3).discover(lookback_minutes=30)
    cutoff_arg = pool3.fetch.call_args[0][1]
    expected = datetime.now(UTC) - timedelta(minutes=30)
    assert abs((cutoff_arg - expected).total_seconds()) < 5


# ---------------------------------------------------------------------------
# Core behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_true_session_with_error_tool_call_surfaces_finding():
    """An outcome='error' tool call inside a success=true session yields a finding."""
    row = _make_row(
        source_butler="travel",
        session_success=True,
        tool_name="lookup_flight",
        module_name="travel",
        error="ConnectionError: upstream 503",
    )
    findings = await ToolCallFailuresSource(pool=_pool([row]), repo_root=Path("/tmp")).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.source_type == "tool_call_failures"
    assert f.source_butler == "travel"
    assert f.exception_type == "ConnectionError"
    assert f.call_site == "butlers.mcp_wrappers"
    assert len(f.fingerprint) == 64
    assert f.occurrence_count == 1
    assert f.structured_evidence["source"] == "tool_call_failures"
    assert len(f.structured_evidence["session_ids"]) == 1


@pytest.mark.asyncio
async def test_clean_success_session_yields_nothing():
    """No error tool calls (empty view result) → no findings."""
    findings = await ToolCallFailuresSource(pool=_pool([])).discover(lookback_minutes=15)
    assert findings == []


@pytest.mark.asyncio
async def test_same_fingerprint_aggregates():
    """Identical failures across sessions collapse to one finding with a count."""
    rows = [
        _make_row(session_id=uuid.uuid4(), error="ValueError: bad query"),
        _make_row(session_id=uuid.uuid4(), error="ValueError: bad query"),
        _make_row(session_id=uuid.uuid4(), error="ValueError: bad query"),
    ]
    findings = await ToolCallFailuresSource(pool=_pool(rows), repo_root=Path("/tmp")).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    assert findings[0].occurrence_count == 3


@pytest.mark.asyncio
async def test_fingerprint_matches_log_scanner_for_same_tool_failure():
    """The finding fingerprint equals the log_scanner fingerprint for the SAME
    caught tool exception, so triage's source-agnostic dedup coalesces them.

    This reconstructs exactly what ``log_scanner`` computes for the structured
    ``"MCP tool call failed (...)"`` log line emitted by
    ``mcp_wrappers._log_tool_call_failure`` for the same failure.
    """
    butler = "finance"
    module = "finance"
    tool = "search_transactions"
    error = "ValueError: bad query"

    # --- tool_call_failures source produces a fingerprint from the view row ---
    row = _make_row(
        source_butler=butler,
        module_name=module,
        tool_name=tool,
        error=error,
    )
    findings = await ToolCallFailuresSource(pool=_pool([row]), repo_root=Path("/tmp")).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    tcf_fp = findings[0].fingerprint

    # --- recompute the log_scanner fingerprint for the matching log entry ---
    # log_scanner: exception_type = entry.exception (= type(exc).__name__),
    # call_site = entry.logger (no traceback) = "butlers.mcp_wrappers",
    # event = the _MCP_TOOL_CALL_FAILED_LOG_LINE-formatted string.
    log_event = f"MCP tool call failed (butler={butler} module={module} tool={tool}): {error}"
    exception_type = "ValueError"  # type(exc).__name__ as stored in log "exception" extra

    # log_scanner._extract_call_site falls back to the entry's logger name when
    # the log entry carries no traceback (the tool-failure log line does not).
    log_scanner_call_site = "butlers.mcp_wrappers"

    log_scanner_fp = _compute_hash(
        exception_type,
        log_scanner_call_site,
        _sanitize_message(log_event),
    )

    assert tcf_fp == log_scanner_fp, (
        "tool_call_failures fingerprint must match log_scanner's for the same "
        "caught tool exception so triage dedup coalesces them"
    )
