"""Tests for butlers.core.qa.sources.log_scanner.LogScannerSource — condensed.

Covers:
- DiscoverySource protocol compliance
- Log file discovery: butlers/, connectors/, uvicorn/ subdirs; qa.log excluded;
  missing subdirs non-fatal (DEBUG log)
- JSON-lines parsing: valid, malformed JSON, missing fields, butler_name fallback
- Temporal filtering: recent in, old out
- Severity filtering: ERROR/CRITICAL included; WARNING with crash patterns; INFO/debug excluded
- Finding structure: fields populated, PII stripped
- Fingerprint stability and compute_fingerprint_from_log_entry compatibility
- Finding aggregation: occurrence_count, first_seen, last_seen
- Performance caps: max_entries_per_scan, max_findings_per_scan emit WARNING
- New caps: max_total_lines, max_scan_seconds — partial results + telemetry
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from butlers.core.healing.fingerprint import compute_fingerprint_from_log_entry
from butlers.core.qa.sources.log_scanner import (
    LogEntry,
    LogScannerSource,
    _parse_log_line,
    _should_include_entry,
)
from butlers.core.qa.sources.protocol import DiscoverySource


def _line(
    level="error",
    event="Something went wrong",
    ts=None,
    butler_name="finance",
    logger_name="butlers.modules.finance",
    exception="ValueError",
    **extra,
):
    if ts is None:
        ts = datetime.now(UTC)
    data = {
        "level": level,
        "event": event,
        "timestamp": ts.isoformat(),
        "butler_name": butler_name,
        "logger": logger_name,
        **extra,
    }
    if exception:
        data["exception"] = exception
    return json.dumps(data)


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def test_log_scanner_protocol_parse_and_fingerprint(tmp_path):
    """Protocol compliance; _parse_log_line behavior; compute_fingerprint_from_log_entry call_site extraction."""
    import inspect

    source = LogScannerSource(log_root=tmp_path)
    assert isinstance(source, DiscoverySource)
    assert source.name == "log_scanner"
    assert inspect.iscoroutinefunction(source.discover)

    # _parse_log_line: valid, malformed JSON, missing fields, butler_name fallback
    now = datetime.now(UTC)
    entry = _parse_log_line(
        _line(ts=now, event="DB fail", exception="asyncpg.PostgresError"), "finance"
    )
    assert entry is not None and entry.level == "error" and entry.butler_name == "finance"
    assert _parse_log_line("not valid json {{{", "butler") is None
    assert (
        _parse_log_line(json.dumps({"level": "error", "timestamp": now.isoformat()}), "b") is None
    )
    assert _parse_log_line(json.dumps({"level": "error", "event": "test"}), "b") is None
    entry2 = _parse_log_line(
        json.dumps({"level": "error", "event": "fail", "timestamp": now.isoformat()}), "travel"
    )
    assert entry2 is not None and entry2.butler_name == "travel"

    # compute_fingerprint_from_log_entry: call_site from traceback; fallback <unknown>
    tb = (
        'Traceback (most recent call last):\n  File "src/butlers/core/db.py", line 42, in connect\n'
        "    raise e\nasyncpg.exceptions.PostgresConnectionError: could not connect\n"
    )
    r = compute_fingerprint_from_log_entry(
        {
            "level": "error",
            "event": "Connection failed",
            "timestamp": now.isoformat(),
            "logger": "butlers.core.db",
            "exception": "asyncpg.exceptions.PostgresConnectionError",
            "traceback": tb,
        }
    )
    assert "src/butlers/core/db.py" in r.call_site
    r2 = compute_fingerprint_from_log_entry(
        {"level": "error", "event": "Something bad", "timestamp": now.isoformat()}
    )
    assert r2.call_site == "<unknown>:<unknown>" and len(r2.fingerprint) == 64


@pytest.mark.parametrize(
    "subdir,filename,butler_name,log_kwargs",
    [
        ("butlers", "finance.log", "finance", {}),
        ("connectors", "telegram.log", None, {"butler_name": "connector_telegram"}),
        ("uvicorn", "server.log", None, {"butler_name": "uvicorn"}),
    ],
)
@pytest.mark.asyncio
async def test_discover_subdirs_and_exclusions(tmp_path, subdir, filename, butler_name, log_kwargs):
    """Scanner reads butlers/, connectors/, uvicorn/ subdirs; excludes qa.log; missing subdirs non-fatal."""
    now = datetime.now(UTC)
    _write(tmp_path / subdir / filename, [_line(ts=now, **log_kwargs)])
    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    if butler_name:
        assert findings[0].source_butler == butler_name

    # qa.log excluded
    _write(tmp_path / "butlers" / "qa.log", [_line(ts=now)])
    all_findings = await source.discover(lookback_minutes=15)
    sources = [f.source_file for f in all_findings]
    assert "qa.log" not in sources


@pytest.mark.asyncio
async def test_missing_subdir_nonfatal(tmp_path, caplog):
    """Missing subdirs skipped with DEBUG log."""
    now = datetime.now(UTC)
    _write(tmp_path / "butlers" / "finance.log", [_line(ts=now)])
    source = LogScannerSource(log_root=tmp_path)
    with caplog.at_level(logging.DEBUG):
        findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    assert any(
        "skipping missing directory" in r.message
        for r in caplog.records
        if r.levelno == logging.DEBUG
    )


@pytest.mark.asyncio
async def test_temporal_filtering(tmp_path):
    """Recent entries included; old entries excluded."""
    now = datetime.now(UTC)
    _write(
        tmp_path / "butlers" / "finance.log",
        [
            _line(ts=now - timedelta(minutes=5)),
            _line(ts=now - timedelta(minutes=30)),
        ],
    )
    findings = await LogScannerSource(log_root=tmp_path).discover(lookback_minutes=15)
    assert len(findings) == 1


@pytest.mark.parametrize(
    "level,event,exception,expected",
    [
        ("error", "fail", None, True),
        ("critical", "crash", None, True),
        ("warning", "OOM detected in process", None, True),
        ("warning", "something", "TimeoutError", True),
        ("warning", "some minor issue", None, False),
        ("info", "started up", None, False),
        ("debug", "verbose detail", None, False),
    ],
)
def test_severity_filtering(level, event, exception, expected):
    """ERROR/CRITICAL included; WARNING with crash pattern; INFO/debug out."""
    entry = LogEntry(
        level=level, event=event, timestamp=datetime.now(UTC), butler_name="b", exception=exception
    )
    assert _should_include_entry(entry) is expected


def test_codex_mcp_discovery_exhaustion_excluded_from_log_scanner():
    """Recovered Codex MCP-discovery guard logs should not page QA via log_scanner."""
    entry = LogEntry(
        level="error",
        event="MCP discovery failed after 3 attempts — aborting session to prevent runaway token usage from bash-only fallback",
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.runtimes.codex",
    )
    assert _should_include_entry(entry) is False


def test_opencode_subprocess_timeout_warning_excluded_from_log_scanner():
    """OpenCode adapter timeout warnings are duplicate session-timeout evidence."""
    entry = LogEntry(
        level="warning",
        event="OpenCode CLI timed out after 30s",
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.runtimes.opencode",
    )
    assert _should_include_entry(entry) is False


def test_switchboard_classification_timeout_excluded_from_log_scanner():
    """Expected switchboard classification caps are fallback telemetry, not QA cases."""
    entry = LogEntry(
        level="error",
        event=(
            "Runtime invocation failed: TimeoutError: Session timed out after 30s "
            "(model=gpt-5.4-mini, butler=switchboard)"
        ),
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.spawner",
        exception="TimeoutError",
        trigger_source="tick",
        raw={"trigger_source": "tick", "timeout_s": 30},
    )
    assert _should_include_entry(entry) is False


def test_legacy_switchboard_classification_timeout_uses_event_duration():
    """Legacy spawner logs without timeout_s still suppress only short tick timeouts."""
    entry = LogEntry(
        level="error",
        event=(
            "Runtime invocation failed: TimeoutError: Session timed out after 30s "
            "(model=gpt-5.4-mini, butler=switchboard)"
        ),
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.spawner",
        exception="TimeoutError",
        trigger_source="tick",
        raw={"trigger_source": "tick"},
    )
    assert _should_include_entry(entry) is False


def test_non_classification_switchboard_timeout_still_included():
    """Long switchboard runtime timeouts remain actionable."""
    entry = LogEntry(
        level="error",
        event=(
            "Runtime invocation failed: TimeoutError: Session timed out after 1800s "
            "(model=gpt-5.4-mini, butler=switchboard)"
        ),
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.spawner",
        exception="TimeoutError",
        trigger_source="trigger",
        raw={"trigger_source": "trigger", "timeout_s": 1800},
    )
    assert _should_include_entry(entry) is True


def test_codex_cli_timeout_included_without_session_records_coverage():
    """Codex timeout logs stay visible when session_records cannot cover them."""
    entry = LogEntry(
        level="error",
        event="Codex CLI timed out after 1800s",
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.runtimes.codex",
    )
    assert _should_include_entry(entry) is True


def test_codex_cli_timeout_excluded_when_session_records_covers_it():
    """Codex adapter timeout logs are duplicate evidence when session_records is enabled."""
    entry = LogEntry(
        level="error",
        event="Codex CLI timed out after 1800s",
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.runtimes.codex",
    )
    assert _should_include_entry(entry, suppress_session_duplicate_timeouts=True) is False


def test_spawner_runtime_timeout_included_without_session_records():
    """Log-scanner-only deployments must keep timeout coverage."""
    entry = LogEntry(
        level="error",
        event="Runtime invocation failed: TimeoutError: Codex CLI timed out after 30 seconds",
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.spawner",
    )
    assert _should_include_entry(entry) is True


def test_manual_short_switchboard_timeout_still_included():
    """Only tick-triggered classification timeouts are treated as fallback telemetry."""
    entry = LogEntry(
        level="error",
        event=(
            "Runtime invocation failed: TimeoutError: Session timed out after 30s "
            "(model=gpt-5.4-mini, butler=switchboard)"
        ),
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.spawner",
        exception="TimeoutError",
        trigger_source="trigger",
        raw={"trigger_source": "trigger", "timeout_s": 30},
    )
    assert _should_include_entry(entry) is True


def test_spawner_runtime_timeout_excluded_when_session_records_covers_it():
    """Spawner timeout logs are duplicate evidence when session_records is enabled."""
    entry = LogEntry(
        level="error",
        event="Runtime invocation failed: TimeoutError: Codex CLI timed out after 30 seconds",
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.spawner",
    )
    assert _should_include_entry(entry, suppress_session_duplicate_timeouts=True) is False


def test_opencode_empty_response_adapter_warning_excluded_without_session_records_coverage():
    """Recovered OpenCode empty-response attempts are not QA findings."""
    entry = LogEntry(
        level="warning",
        event=("OpenCode CLI returned no response: no result, tool calls, or token usage"),
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.runtimes.opencode",
    )
    assert _should_include_entry(entry) is False


def test_opencode_empty_response_warning_with_stderr_sentinel_excluded():
    """Recovered OpenCode empty-response warnings stay excluded even with stderr details."""
    entry = LogEntry(
        level="warning",
        event=(
            "OpenCode CLI returned no response: no result, tool calls, or token usage; "
            "stderr=TimeoutError: upstream request timed out"
        ),
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.runtimes.opencode",
    )
    assert _should_include_entry(entry) is False


def test_opencode_empty_response_error_included_without_session_records_coverage():
    """Degraded deployments keep ERROR-level OpenCode empty-response coverage."""
    entry = LogEntry(
        level="error",
        event=("OpenCode CLI returned no response: no result, tool calls, or token usage"),
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.runtimes.opencode",
    )
    assert _should_include_entry(entry) is True


def test_spawner_opencode_empty_response_included_without_session_records_coverage():
    """Terminal OpenCode empty-response failures stay visible without session records."""
    entry = LogEntry(
        level="error",
        event=(
            "Runtime invocation failed: RuntimeError: OpenCode CLI returned no response: "
            "no result, tool calls, or token usage"
        ),
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.spawner",
    )
    assert _should_include_entry(entry) is True


def test_opencode_empty_response_excluded_when_session_records_covers_it():
    """OpenCode empty-response adapter logs are duplicate evidence with session records."""
    entry = LogEntry(
        level="error",
        event=("OpenCode CLI returned no response: no result, tool calls, token usage, or stderr"),
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.runtimes.opencode",
    )
    assert _should_include_entry(entry, suppress_session_duplicate_timeouts=True) is False


def test_spawner_opencode_empty_response_excluded_when_session_records_covers_it():
    """Spawner wrapper logs for terminal OpenCode empty responses are session duplicates."""
    entry = LogEntry(
        level="error",
        event=(
            "Runtime invocation failed: RuntimeError: OpenCode CLI returned no response: "
            "no result, tool calls, or token usage"
        ),
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.spawner",
    )
    assert _should_include_entry(entry, suppress_session_duplicate_timeouts=True) is False


def test_spawner_non_timeout_errors_remain_in_log_scanner():
    """Only runtime timeout duplicates are suppressed from spawner logs."""
    entry = LogEntry(
        level="error",
        event="Runtime invocation failed: RuntimeError: adapter crashed before session create",
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.spawner",
    )
    assert _should_include_entry(entry) is True


@pytest.mark.parametrize(
    "event",
    [
        "codex_refresh_lock: lock held >30s by another process — proceeding unlocked to avoid deadlock (lock_path=/tmp/.codex/butlers.refresh.lock)",
        "codex_refresh_lock: waiting >5s for cross-process refresh lock — possible contention (lock_path=/tmp/.codex/butlers.refresh.lock)",
    ],
)
def test_codex_refresh_lock_contention_excluded_from_log_scanner(event):
    """Benign Codex refresh-lock contention should not page QA."""
    entry = LogEntry(
        level="warning",
        event=event,
        timestamp=datetime.now(UTC),
        butler_name="general",
        logger="butlers.core.runtimes.codex",
    )
    assert _should_include_entry(entry) is False


@pytest.mark.parametrize(
    "logger_name,event",
    [
        (
            "butlers.core.runtimes.opencode",
            "OpenCode CLI timed out after 1800s",
        ),
        (
            "butlers.core.spawner",
            "Runtime invocation failed: TimeoutError: OpenCode CLI timed out after 1800 seconds",
        ),
    ],
)
def test_runtime_session_timeout_logs_excluded_from_log_scanner(logger_name, event):
    """Handled runtime session timeouts should be discovered from session_records."""
    entry = LogEntry(
        level="error",
        event=event,
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger=logger_name,
        exception="TimeoutError",
    )
    assert _should_include_entry(entry) is False


def test_generic_spawner_timeout_log_still_included():
    """Only adapter-managed OpenCode timeout duplicates are excluded."""
    entry = LogEntry(
        level="error",
        event="Runtime invocation failed: TimeoutError: adapter timed out before startup",
        timestamp=datetime.now(UTC),
        butler_name="switchboard",
        logger="butlers.core.spawner",
        exception="TimeoutError",
    )
    assert _should_include_entry(entry) is True


@pytest.mark.asyncio
async def test_discover_excludes_runtime_session_timeout_logs(tmp_path):
    """OpenCode timeout ERROR lines do not create duplicate log_scanner findings."""
    now = datetime.now(UTC)
    _write(
        tmp_path / "butlers" / "switchboard.log",
        [
            _line(
                event="OpenCode CLI timed out after 1800s",
                ts=now,
                logger_name="butlers.core.runtimes.opencode",
                exception="TimeoutError",
                butler_name="switchboard",
            ),
            _line(
                event=(
                    "Runtime invocation failed: TimeoutError: "
                    "OpenCode CLI timed out after 1800 seconds"
                ),
                ts=now,
                logger_name="butlers.core.spawner",
                exception="TimeoutError",
                butler_name="switchboard",
            ),
        ],
    )

    findings = await LogScannerSource(log_root=tmp_path).discover(lookback_minutes=15)

    assert findings == []


@pytest.mark.asyncio
async def test_finding_structure_and_pii(tmp_path):
    """QaFinding fields populated; PII stripped from event_summary."""
    now = datetime.now(UTC)
    _write(
        tmp_path / "butlers" / "finance.log",
        [
            _line(
                event="Failed to connect to database",
                ts=now,
                logger_name="butlers.core.db",
                exception="asyncpg.PostgresConnectionError",
            ),
        ],
    )
    findings = await LogScannerSource(log_root=tmp_path, repo_root=tmp_path).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.source_type == "log_scanner" and f.source_butler == "finance"
    assert len(f.fingerprint) == 64 and f.occurrence_count == 1
    assert f.exception_type == "asyncpg.PostgresConnectionError"

    _write(
        tmp_path / "butlers" / "health.log",
        [_line(event="Error processing email user@example.com", ts=now)],
    )
    all_f = await LogScannerSource(log_root=tmp_path, repo_root=tmp_path).discover(
        lookback_minutes=15
    )
    health_f = [f for f in all_f if f.source_file == "health.log"]
    assert health_f and "user@example.com" not in health_f[0].event_summary


@pytest.mark.asyncio
async def test_fingerprint_stability_and_aggregation(tmp_path):
    """Same entry → same fingerprint across scans; aggregation: occurrence_count, first_seen, last_seen."""
    now = datetime.now(UTC)
    t1 = now - timedelta(minutes=10)
    t2 = now - timedelta(minutes=5)
    t3 = now - timedelta(minutes=2)

    _write(
        tmp_path / "butlers" / "finance.log",
        [
            _line(event="DB down", exception="ConnectionError", ts=t2),
            _line(event="DB down", exception="ConnectionError", ts=t1),
            _line(event="DB down", exception="ConnectionError", ts=t3),
        ],
    )
    source = LogScannerSource(log_root=tmp_path)
    f1 = await source.discover(lookback_minutes=15)
    f2 = await source.discover(lookback_minutes=15)
    assert {f.fingerprint for f in f1} == {f.fingerprint for f in f2}
    assert len(f1) == 1 and f1[0].occurrence_count == 3
    assert abs((f1[0].first_seen - t1).total_seconds()) < 2
    assert abs((f1[0].last_seen - t3).total_seconds()) < 2


@pytest.mark.asyncio
async def test_performance_caps(tmp_path, caplog):
    """max_entries_per_scan and max_findings_per_scan emit WARNING when hit."""
    now = datetime.now(UTC)
    lines = [
        _line(event=f"Error {i}", exception=f"Err{i}", ts=now, logger_name=f"mod.sub{i}")
        for i in range(10)
    ]
    _write(tmp_path / "butlers" / "finance.log", lines)

    with caplog.at_level(logging.WARNING):
        await LogScannerSource(log_root=tmp_path, max_entries_per_scan=3).discover(
            lookback_minutes=15
        )
    assert any(
        "truncated" in r.message.lower() or "max_entries" in r.message.lower()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        findings = await LogScannerSource(log_root=tmp_path, max_findings_per_scan=3).discover(
            lookback_minutes=15
        )
    assert len(findings) <= 3
    assert any(
        "cap" in r.message.lower() or "finding" in r.message.lower()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


@pytest.mark.asyncio
async def test_benign_volume_does_not_starve_errors(tmp_path):
    """Entry budget is NOT consumed by benign INFO lines; real errors are still found.

    The error line is written at the TOP of the file (oldest position) and
    benign INFO lines follow it.  _read_file_tail() reads from the END of the
    file, so benign lines are encountered before the error.  With the old
    fixed-budget approach, 5 INFO entries would exhaust the budget and the
    error at the top would never be reached.  With the new candidate-only
    budget, INFO lines do not count against max_entries_per_scan, so the error
    is always found.
    """
    now = datetime.now(UTC)
    # All benign lines within the 30-minute lookback window (ts varies between
    # 1 s and 100 s ago — well within the 1800 s cutoff).
    benign_lines = [
        json.dumps(
            {
                "level": "info",
                "event": f"Request processed {i}",
                "timestamp": (now - timedelta(seconds=i + 1)).isoformat(),
                "butler_name": "finance",
                "logger": "butlers.http",
            }
        )
        for i in range(100)
    ]
    error_line = _line(
        level="error",
        event="Critical connector failure",
        exception="ConnectionRefusedError",
        # Oldest timestamp so it ends up at the top of the file.
        ts=now - timedelta(seconds=101),
    )
    # Error at TOP (beginning of file), benign lines at BOTTOM (end of file).
    # _read_file_tail reads from the end, so benign lines are processed before
    # the error — exercising that they do not consume the candidate budget.
    _write(tmp_path / "butlers" / "finance.log", [error_line] + benign_lines)

    # With a budget of 5, the old code would be exhausted by INFO lines before
    # reaching the error.  With the new code, INFO lines are free and the error
    # must always be found.
    source = LogScannerSource(log_root=tmp_path, max_entries_per_scan=5)
    findings = await source.discover(lookback_minutes=30)

    assert len(findings) == 1
    assert "connector failure" in findings[0].event_summary.lower()
    # No truncation: only 1 candidate entry (the error), well under budget of 5.
    assert source.last_truncated is None


@pytest.mark.asyncio
async def test_later_file_not_starved(tmp_path):
    """Errors in a later-sorted log file must be discoverable when shuffle puts it first.

    With a candidate budget of 3 and 4 distinct errors in aaa.log, the budget
    is exhausted before zzz.log is reached when files are processed in
    alphabetical order.  The scanner randomises file order; this test uses
    monkeypatching to exercise both orderings deterministically, verifying:

    - zzz-first: zzz.log error is found (budget not yet exhausted).
    - aaa-first: zzz.log error is not found (budget exhausted by aaa.log).
    """
    now = datetime.now(UTC)

    # "aaa.log" — has 4 distinct errors (exceeds a budget of 3)
    aaa_lines = [
        _line(event=f"Error aaa {i}", exception=f"ErrA{i}", ts=now, logger_name=f"mod.a{i}")
        for i in range(4)
    ]
    # "zzz.log" — has a unique error that should be reachable when ordered first
    zzz_lines = [
        _line(
            event="ZZZ unique transport error",
            exception="TransportError",
            ts=now,
            logger_name="mod.transport",
        )
    ]
    subdir = tmp_path / "butlers"
    _write(subdir / "aaa.log", aaa_lines)
    _write(subdir / "zzz.log", zzz_lines)

    aaa_path = subdir / "aaa.log"
    zzz_path = subdir / "zzz.log"

    source = LogScannerSource(log_root=tmp_path, max_entries_per_scan=3)

    # --- zzz-first: zzz.log scanned before aaa.log exhausts the budget ---
    with patch("butlers.core.qa.sources.log_scanner.random.shuffle") as mock_shuffle:
        mock_shuffle.side_effect = lambda lst: lst.__setitem__(slice(None), [zzz_path, aaa_path])
        findings_zzz_first = await source.discover(lookback_minutes=15)

    assert any("transport" in f.event_summary.lower() for f in findings_zzz_first), (
        "zzz.log error must be found when zzz.log is scanned first"
    )

    # --- aaa-first: aaa.log exhausts the budget before zzz.log is reached ---
    with patch("butlers.core.qa.sources.log_scanner.random.shuffle") as mock_shuffle:
        mock_shuffle.side_effect = lambda lst: lst.__setitem__(slice(None), [aaa_path, zzz_path])
        findings_aaa_first = await source.discover(lookback_minutes=15)

    assert not any("transport" in f.event_summary.lower() for f in findings_aaa_first), (
        "zzz.log error must NOT be found when aaa.log exhausts the budget first"
    )


@pytest.mark.asyncio
async def test_max_total_lines_cap(tmp_path, caplog):
    """max_total_lines cap returns partial results, emits WARNING, and sets truncation telemetry."""
    now = datetime.now(UTC)
    # Write 20 lines: mix of INFO (benign) and ERROR lines, all recent
    lines = []
    for i in range(20):
        level = "error" if i % 5 == 0 else "info"
        lines.append(_line(level=level, event=f"Event {i}", ts=now, exception=f"Err{i}"))
    _write(tmp_path / "butlers" / "noisy.log", lines)

    source = LogScannerSource(log_root=tmp_path, max_total_lines=5)
    assert source.last_truncated is None
    assert source.last_truncated_reason is None

    with caplog.at_level(logging.WARNING):
        findings = await source.discover(lookback_minutes=15)

    # Should have returned gracefully with partial results
    assert isinstance(findings, list)
    # Cap was hit — truncation telemetry updated
    assert source.last_truncated is not None
    assert source.last_truncated_reason == "max_total_lines"
    # WARNING log emitted
    assert any(
        "total-lines" in r.message.lower() or "max_total_lines" in r.message.lower()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


@pytest.mark.asyncio
async def test_truncation_telemetry(tmp_path):
    """last_truncated and last_truncated_reason reflect cap-hit state after discover()."""
    now = datetime.now(UTC)

    # No truncation case
    source_clean = LogScannerSource(log_root=tmp_path, max_entries_per_scan=100)
    _write(
        tmp_path / "butlers" / "clean.log",
        [_line(ts=now)],
    )
    await source_clean.discover(lookback_minutes=15)
    assert source_clean.last_truncated is None
    assert source_clean.last_truncated_reason is None

    # Entry cap hit
    lines = [
        _line(event=f"Error {i}", exception=f"Err{i}", ts=now, logger_name=f"mod.x{i}")
        for i in range(10)
    ]
    _write(tmp_path / "butlers" / "busy.log", lines)
    source_entry_cap = LogScannerSource(log_root=tmp_path, max_entries_per_scan=3)
    await source_entry_cap.discover(lookback_minutes=15)
    assert source_entry_cap.last_truncated is not None
    assert source_entry_cap.last_truncated_reason == "max_entries_per_scan"

    # Finding cap hit
    source_finding_cap = LogScannerSource(log_root=tmp_path, max_findings_per_scan=2)
    await source_finding_cap.discover(lookback_minutes=15)
    assert source_finding_cap.last_truncated is not None
    assert source_finding_cap.last_truncated_reason == "max_findings_per_scan"


@pytest.mark.asyncio
async def test_max_scan_seconds_cap(tmp_path, caplog):
    """max_scan_seconds wall-clock cap returns partial results and sets truncation telemetry."""
    now = datetime.now(UTC)
    # Write enough error lines that the scanner would normally process them all
    lines = [
        _line(event=f"Error {i}", exception=f"Err{i}", ts=now, logger_name=f"mod.sub{i}")
        for i in range(20)
    ]
    _write(tmp_path / "butlers" / "finance.log", lines)

    # Patch time.monotonic so the cap fires on the first line
    call_count = 0
    original_monotonic = __import__("time").monotonic
    start_time = original_monotonic()

    def _fast_clock():
        nonlocal call_count
        call_count += 1
        # First call returns start_time; subsequent calls return start_time + 60s
        # so the cap fires as soon as the loop checks it after the first line
        if call_count == 1:
            return start_time
        return start_time + 60.0

    source = LogScannerSource(log_root=tmp_path, max_scan_seconds=30.0)
    assert source.last_truncated is None

    with patch("butlers.core.qa.sources.log_scanner.time.monotonic", side_effect=_fast_clock):
        with caplog.at_level(logging.WARNING):
            findings = await source.discover(lookback_minutes=15)

    assert isinstance(findings, list)
    assert source.last_truncated is not None
    assert source.last_truncated_reason == "max_scan_seconds"
    assert any(
        "wall-clock" in r.message.lower() or "max_scan_seconds" in r.message.lower()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


@pytest.mark.asyncio
async def test_max_total_lines_does_not_break_candidate_budget(tmp_path):
    """max_total_lines cap is independent of max_entries_per_scan (candidate budget).

    A large benign log should not starve the candidate budget when max_total_lines
    fires first.  If max_total_lines is generous, the candidate budget still applies.
    """
    now = datetime.now(UTC)
    # 5 error lines mixed with 5 info lines (10 total)
    lines = []
    for i in range(5):
        lines.append(_line(level="info", event=f"Info {i}", ts=now))
        lines.append(
            _line(
                level="error",
                event=f"DB down {i}",
                exception="ConnErr",
                ts=now,
                logger_name=f"mod{i}",
            )
        )
    _write(tmp_path / "butlers" / "mixed.log", lines)

    # Generous total-lines cap: does not interfere; candidate budget applies
    source = LogScannerSource(
        log_root=tmp_path,
        max_total_lines=1_000,
        max_entries_per_scan=3,
    )
    findings = await source.discover(lookback_minutes=15)
    # candidate entries cap fired — partial results with ≤3 unique findings
    assert len(findings) <= 3
    assert source.last_truncated_reason == "max_entries_per_scan"


@pytest.mark.asyncio
async def test_truncation_telemetry_updated_across_scans(tmp_path):
    """last_truncated is updated on each scan that hits the cap."""
    now = datetime.now(UTC)
    lines = [
        _line(event=f"E{i}", exception=f"Er{i}", ts=now, logger_name=f"m{i}") for i in range(10)
    ]
    _write(tmp_path / "butlers" / "f.log", lines)

    source = LogScannerSource(log_root=tmp_path, max_total_lines=3)
    await source.discover(lookback_minutes=15)
    first_truncated = source.last_truncated

    assert first_truncated is not None
    assert source.last_truncated_reason == "max_total_lines"

    # Run again — telemetry should be refreshed (not stale from first scan)
    await source.discover(lookback_minutes=15)
    assert source.last_truncated is not None
    assert source.last_truncated_reason == "max_total_lines"


@pytest.mark.asyncio
async def test_structured_evidence_populated(tmp_path):
    """structured_evidence is populated with source, log_file, and level for log_scanner findings."""
    now = datetime.now(UTC)
    line = _line(level="error", event="Database connection refused", ts=now)
    _write(tmp_path / "butlers" / "finance.log", [line])

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    ev = findings[0].structured_evidence
    assert ev is not None
    assert ev["source"] == "log_scanner"
    assert ev["log_file"] == "finance"
    assert ev["level"] == "error"
    # trigger_source absent from this log line — key should be missing or None
    assert ev.get("trigger_source") is None


@pytest.mark.asyncio
async def test_structured_evidence_with_trigger_source(tmp_path):
    """trigger_source from raw log JSON is captured in structured_evidence."""
    import json as _json

    now = datetime.now(UTC)
    log_data = {
        "level": "error",
        "event": "Task failed unexpectedly",
        "timestamp": now.isoformat(),
        "butler_name": "travel",
        "logger": "butlers.travel.jobs",
        "exception": "RuntimeError",
        "trigger_source": "schedule",
    }
    log_line = _json.dumps(log_data)
    _write(tmp_path / "butlers" / "travel.log", [log_line])

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    ev = findings[0].structured_evidence
    assert ev is not None
    assert ev.get("trigger_source") == "schedule"


@pytest.mark.asyncio
async def test_discover_skips_codex_mcp_discovery_exhaustion_logs(tmp_path):
    """Raw Codex MCP-discovery exhaustion logs are suppressed in favor of session_records."""
    now = datetime.now(UTC)
    _write(
        tmp_path / "butlers" / "switchboard.log",
        [
            _line(
                level="error",
                event="MCP discovery failed after 3 attempts — aborting session to prevent runaway token usage from bash-only fallback",
                ts=now,
                butler_name="switchboard",
                logger_name="butlers.core.runtimes.codex",
                exception=None,
            ),
            _line(
                level="error",
                event="Database connection refused",
                ts=now,
                butler_name="switchboard",
                logger_name="butlers.core.db",
                exception="ConnectionError",
            ),
        ],
    )

    findings = await LogScannerSource(log_root=tmp_path).discover(lookback_minutes=15)

    assert len(findings) == 1
    assert "database connection refused" in findings[0].event_summary.lower()


@pytest.mark.asyncio
async def test_discover_includes_spawner_runtime_timeout_logs_without_session_records(tmp_path):
    """Log-scanner-only source config keeps timeout findings discoverable."""
    now = datetime.now(UTC)
    _write(
        tmp_path / "butlers" / "switchboard.log",
        [
            _line(
                level="error",
                event="Runtime invocation failed: TimeoutError: Codex CLI timed out after 30 seconds",
                ts=now,
                butler_name="switchboard",
                logger_name="butlers.core.spawner",
                exception=None,
            ),
        ],
    )

    findings = await LogScannerSource(log_root=tmp_path).discover(lookback_minutes=15)

    assert len(findings) == 1
    assert "timed out" in findings[0].event_summary.lower()


@pytest.mark.asyncio
async def test_discover_skips_spawner_runtime_timeout_logs(tmp_path):
    """Raw spawner timeout logs are suppressed in favor of session_records."""
    now = datetime.now(UTC)
    _write(
        tmp_path / "butlers" / "switchboard.log",
        [
            _line(
                level="error",
                event="Runtime invocation failed: TimeoutError: Codex CLI timed out after 30 seconds",
                ts=now,
                butler_name="switchboard",
                logger_name="butlers.core.spawner",
                exception=None,
            ),
            _line(
                level="error",
                event="Database connection refused",
                ts=now,
                butler_name="switchboard",
                logger_name="butlers.core.db",
                exception="ConnectionError",
            ),
        ],
    )

    findings = await LogScannerSource(
        log_root=tmp_path,
        suppress_session_duplicate_timeouts=True,
    ).discover(lookback_minutes=15)

    assert len(findings) == 1
    assert "database connection refused" in findings[0].event_summary.lower()


@pytest.mark.asyncio
async def test_discover_includes_codex_cli_timeout_log_without_session_records(tmp_path):
    """Log scanner preserves Codex timeout coverage when session_records is unavailable."""
    now = datetime.now(UTC)
    _write(
        tmp_path / "butlers" / "switchboard.log",
        [
            _line(
                ts=now,
                butler_name="switchboard",
                logger_name="butlers.core.runtimes.codex",
                event="Codex CLI timed out after 1800s",
                exception=None,
            )
        ],
    )

    findings = await LogScannerSource(log_root=tmp_path, repo_root=tmp_path).discover(
        lookback_minutes=15
    )

    assert len(findings) == 1
    assert findings[0].event_summary == "Codex CLI timed out after <ID>"


@pytest.mark.asyncio
async def test_discover_skips_codex_cli_timeout_log_with_session_records(tmp_path):
    """Generic Codex timeout logs are suppressed when session_records covers them."""
    now = datetime.now(UTC)
    _write(
        tmp_path / "butlers" / "switchboard.log",
        [
            _line(
                ts=now,
                butler_name="switchboard",
                logger_name="butlers.core.runtimes.codex",
                event="Codex CLI timed out after 1800s",
                exception=None,
            )
        ],
    )

    findings = await LogScannerSource(
        log_root=tmp_path,
        repo_root=tmp_path,
        suppress_session_duplicate_timeouts=True,
    ).discover(lookback_minutes=15)

    assert findings == []
