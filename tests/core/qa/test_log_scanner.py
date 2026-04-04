"""Tests for butlers.core.qa.sources.log_scanner.LogScannerSource.

Covers:
- DiscoverySource protocol compliance (name, discover signature)
- Log file discovery: butlers/, connectors/, uvicorn/ subdirs; qa.log excluded
- Missing log directory is non-fatal (skipped with DEBUG log)
- JSON-lines parsing: valid lines → LogEntry; malformed lines → skipped
- Temporal filtering: only entries within lookback window are included
- Severity filtering: ERROR/CRITICAL always included; WARNING with crash patterns; INFO excluded
- Finding extraction: QaFinding fields populated correctly
- Fingerprint computation: stable across calls, sanitized
- Finding aggregation: multiple entries with same fingerprint → one finding with occurrence_count
- Performance caps: max_entries_per_scan, max_findings_per_scan with WARNING logs
- compute_fingerprint_from_log_entry: compatible with log_scanner fingerprints
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from butlers.core.healing.fingerprint import compute_fingerprint_from_log_entry
from butlers.core.qa.sources.log_scanner import (
    LogEntry,
    LogScannerSource,
    _parse_log_line,
    _should_include_entry,
)
from butlers.core.qa.sources.protocol import DiscoverySource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_log_line(
    level: str = "error",
    event: str = "Something went wrong",
    ts: datetime | None = None,
    butler_name: str = "finance",
    logger_name: str = "butlers.modules.finance",
    exception: str | None = "ValueError",
    **extra,
) -> str:
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


def _make_log_file(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_log_scanner_implements_protocol(tmp_path):
    """LogScannerSource implements the DiscoverySource protocol."""
    source = LogScannerSource(log_root=tmp_path)
    assert isinstance(source, DiscoverySource)
    assert source.name == "log_scanner"


def test_log_scanner_has_discover_method(tmp_path):
    """discover() is an async method that returns a list."""
    import inspect

    source = LogScannerSource(log_root=tmp_path)
    assert inspect.iscoroutinefunction(source.discover)


# ---------------------------------------------------------------------------
# Log file discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_finds_butler_logs(tmp_path):
    """Scanner reads from logs/butlers/*.log."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    line = _make_log_line(ts=now)
    _make_log_file(butlers_dir / "finance.log", [line])

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    assert findings[0].source_butler == "finance"


@pytest.mark.asyncio
async def test_discover_finds_connector_logs(tmp_path):
    """Scanner reads from logs/connectors/*.log."""
    conn_dir = tmp_path / "connectors"
    now = datetime.now(UTC)
    line = _make_log_line(
        ts=now, butler_name="connector_telegram", logger_name="connector.telegram"
    )
    _make_log_file(conn_dir / "telegram.log", [line])

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1


@pytest.mark.asyncio
async def test_discover_finds_uvicorn_logs(tmp_path):
    """Scanner reads from logs/uvicorn/*.log."""
    uv_dir = tmp_path / "uvicorn"
    now = datetime.now(UTC)
    line = _make_log_line(ts=now, butler_name="uvicorn")
    _make_log_file(uv_dir / "server.log", [line])

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1


@pytest.mark.asyncio
async def test_discover_excludes_qa_log(tmp_path):
    """logs/butlers/qa.log is excluded from scanning."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    qa_line = _make_log_line(ts=now, butler_name="qa", exception="InternalQaError")
    _make_log_file(butlers_dir / "qa.log", [qa_line])

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 0


@pytest.mark.asyncio
async def test_missing_subdir_is_nonfatal(tmp_path, caplog):
    """Missing subdirectories are skipped with a DEBUG log."""
    # Only create butlers/, not connectors/ or uvicorn/
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    line = _make_log_line(ts=now)
    _make_log_file(butlers_dir / "finance.log", [line])

    source = LogScannerSource(log_root=tmp_path)
    with caplog.at_level(logging.DEBUG):
        findings = await source.discover(lookback_minutes=15)

    # Should still find the butler log
    assert len(findings) == 1
    # Should log DEBUG for missing connectors/ and uvicorn/
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    skipped = [m for m in debug_msgs if "skipping missing directory" in m]
    assert len(skipped) >= 1


# ---------------------------------------------------------------------------
# JSON-lines parsing
# ---------------------------------------------------------------------------


def test_parse_valid_log_line():
    """Valid JSON log line is parsed into a LogEntry."""
    now = datetime.now(UTC)
    line = _make_log_line(ts=now, event="DB connection failed", exception="asyncpg.PostgresError")
    entry = _parse_log_line(line, "finance")
    assert entry is not None
    assert entry.level == "error"
    assert "DB connection failed" in entry.event
    assert entry.butler_name == "finance"
    assert entry.exception == "asyncpg.PostgresError"
    assert entry.timestamp is not None


def test_parse_malformed_json_returns_none():
    """Malformed JSON line returns None (no error raised)."""
    entry = _parse_log_line("not valid json {{{", "butler")
    assert entry is None


def test_parse_line_missing_required_fields():
    """JSON line missing level or event returns None."""
    # Missing event
    data = {"level": "error", "timestamp": datetime.now(UTC).isoformat()}
    entry = _parse_log_line(json.dumps(data), "butler")
    assert entry is None

    # Missing level
    data = {"event": "something", "timestamp": datetime.now(UTC).isoformat()}
    entry = _parse_log_line(json.dumps(data), "butler")
    assert entry is None


def test_parse_line_missing_timestamp_returns_none():
    """JSON line without a valid timestamp returns None (can't filter by time)."""
    data = {"level": "error", "event": "test"}
    entry = _parse_log_line(json.dumps(data), "butler")
    assert entry is None


def test_parse_line_uses_filename_as_butler_name_fallback():
    """If butler_name is not in the JSON, the filename stem is used."""
    now = datetime.now(UTC)
    data = {"level": "error", "event": "fail", "timestamp": now.isoformat()}
    entry = _parse_log_line(json.dumps(data), "travel")
    assert entry is not None
    assert entry.butler_name == "travel"


# ---------------------------------------------------------------------------
# Malformed line count logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_lines_logged_at_debug(tmp_path, caplog):
    """Malformed JSON lines are counted and logged at DEBUG level."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    lines = [
        "not json",
        _make_log_line(ts=now),
        "{bad: json}",
    ]
    _make_log_file(butlers_dir / "finance.log", lines)

    source = LogScannerSource(log_root=tmp_path)
    with caplog.at_level(logging.DEBUG):
        await source.discover(lookback_minutes=15)

    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    malformed_msgs = [m for m in debug_msgs if "malformed" in m.lower()]
    assert len(malformed_msgs) >= 1


# ---------------------------------------------------------------------------
# Temporal filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entries_within_lookback_window_included(tmp_path):
    """Entries within lookback window are included."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    recent = now - timedelta(minutes=5)
    line = _make_log_line(ts=recent)
    _make_log_file(butlers_dir / "finance.log", [line])

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1


@pytest.mark.asyncio
async def test_entries_outside_lookback_window_excluded(tmp_path):
    """Entries older than lookback window are excluded."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    old = now - timedelta(minutes=30)
    line = _make_log_line(ts=old)
    _make_log_file(butlers_dir / "finance.log", [line])

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# Severity filtering
# ---------------------------------------------------------------------------


def test_error_level_included():
    """ERROR entries pass the severity filter."""
    now = datetime.now(UTC)
    entry = LogEntry(level="error", event="fail", timestamp=now, butler_name="b")
    assert _should_include_entry(entry) is True


def test_critical_level_included():
    """CRITICAL entries pass the severity filter."""
    now = datetime.now(UTC)
    entry = LogEntry(level="critical", event="crash", timestamp=now, butler_name="b")
    assert _should_include_entry(entry) is True


def test_warning_with_crash_pattern_included():
    """WARNING entries with crash sentinel patterns are included."""
    now = datetime.now(UTC)
    # OOM in event
    entry = LogEntry(
        level="warning", event="OOM detected in process", timestamp=now, butler_name="b"
    )
    assert _should_include_entry(entry) is True

    # TimeoutError in exception
    entry2 = LogEntry(
        level="warning",
        event="something happened",
        timestamp=now,
        butler_name="b",
        exception="TimeoutError",
    )
    assert _should_include_entry(entry2) is True


def test_warning_without_crash_pattern_excluded():
    """WARNING entries without crash sentinels are excluded."""
    now = datetime.now(UTC)
    entry = LogEntry(level="warning", event="some minor issue", timestamp=now, butler_name="b")
    assert _should_include_entry(entry) is False


def test_info_level_excluded():
    """INFO entries are excluded."""
    now = datetime.now(UTC)
    entry = LogEntry(level="info", event="started up", timestamp=now, butler_name="b")
    assert _should_include_entry(entry) is False


def test_debug_level_excluded():
    """DEBUG entries are excluded."""
    now = datetime.now(UTC)
    entry = LogEntry(level="debug", event="verbose detail", timestamp=now, butler_name="b")
    assert _should_include_entry(entry) is False


# ---------------------------------------------------------------------------
# Finding structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finding_structure(tmp_path):
    """QaFinding fields are populated correctly from a log entry."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    line = _make_log_line(
        level="error",
        event="Failed to connect to database",
        ts=now,
        butler_name="finance",
        logger_name="butlers.core.db",
        exception="asyncpg.PostgresConnectionError",
    )
    _make_log_file(butlers_dir / "finance.log", [line])

    source = LogScannerSource(log_root=tmp_path, repo_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1

    f = findings[0]
    assert f.source_type == "log_scanner"
    assert f.source_butler == "finance"
    assert len(f.fingerprint) == 64  # SHA-256 hex
    assert f.occurrence_count == 1
    assert f.severity >= 0
    assert f.exception_type == "asyncpg.PostgresConnectionError"
    assert "connect to database" in f.event_summary.lower() or "connect" in f.event_summary.lower()
    assert f.call_site != ""
    assert f.source_file == "finance.log"


@pytest.mark.asyncio
async def test_finding_does_not_contain_raw_log_content(tmp_path):
    """Raw log line content is not stored — only computed fields."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    # Include PII in event that should be stripped
    line = _make_log_line(
        event="Error processing email user@example.com",
        ts=now,
    )
    _make_log_file(butlers_dir / "health.log", [line])

    source = LogScannerSource(log_root=tmp_path, repo_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    f = findings[0]
    # Raw email should be stripped by anonymizer
    assert "user@example.com" not in f.event_summary


# ---------------------------------------------------------------------------
# Fingerprint computation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fingerprint_is_stable_across_calls(tmp_path):
    """Two scans of the same log entry produce the same fingerprint."""
    butlers_dir = tmp_path / "butlers"
    ts = datetime.now(UTC)
    line = _make_log_line(event="DB down", ts=ts, exception="ConnectionError")
    _make_log_file(butlers_dir / "finance.log", [line])

    source = LogScannerSource(log_root=tmp_path)
    findings1 = await source.discover(lookback_minutes=15)
    findings2 = await source.discover(lookback_minutes=15)
    assert len(findings1) == 1
    assert len(findings2) == 1
    assert findings1[0].fingerprint == findings2[0].fingerprint


@pytest.mark.asyncio
async def test_different_errors_produce_different_fingerprints(tmp_path):
    """Semantically different errors produce different fingerprints."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    line1 = _make_log_line(
        event="DB connection failed", exception="ConnectionError", ts=now, logger_name="mod.a"
    )
    line2 = _make_log_line(
        event="File not found", exception="FileNotFoundError", ts=now, logger_name="mod.b"
    )
    _make_log_file(butlers_dir / "finance.log", [line1, line2])

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    fps = {f.fingerprint for f in findings}
    assert len(fps) == 2


@pytest.mark.asyncio
async def test_compute_fingerprint_from_log_entry_compatible_with_log_scanner(tmp_path):
    """compute_fingerprint_from_log_entry produces same fingerprint as log scanner.

    Verifies that both the public API function and the internal scanner logic
    produce identical fingerprints for the same log entry — including for events
    longer than _MAX_SUMMARY_LEN (200 chars) to catch truncation mismatches.
    """
    now = datetime.now(UTC)
    # Use a long event that exceeds the 200-char display truncation limit
    long_event = "DB connection failed: " + "x" * 250  # 272 chars total, > 200
    entry_dict = {
        "level": "error",
        "event": long_event,
        "timestamp": now.isoformat(),
        "butler_name": "finance",
        "logger": "butlers.core.db",
        "exception": "asyncpg.PostgresConnectionError",
    }

    result = compute_fingerprint_from_log_entry(entry_dict)
    assert len(result.fingerprint) == 64
    assert result.exception_type == "asyncpg.PostgresConnectionError"
    assert result.call_site == "butlers.core.db"

    # Also verify against the scanner's own fingerprint for the same entry
    butlers_dir = tmp_path / "butlers"
    import json as _json

    _make_log_file(butlers_dir / "finance.log", [_json.dumps(entry_dict)])
    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    assert findings[0].fingerprint == result.fingerprint


def test_compute_fingerprint_from_log_entry_uses_traceback_for_call_site():
    """compute_fingerprint_from_log_entry prefers traceback over logger field."""
    now = datetime.now(UTC)
    traceback_str = (
        "Traceback (most recent call last):\n"
        '  File "src/butlers/core/db.py", line 42, in connect\n'
        "    raise e\n"
        "asyncpg.exceptions.PostgresConnectionError: could not connect\n"
    )
    entry_dict = {
        "level": "error",
        "event": "Connection failed",
        "timestamp": now.isoformat(),
        "logger": "butlers.core.db",
        "exception": "asyncpg.exceptions.PostgresConnectionError",
        "traceback": traceback_str,
    }
    result = compute_fingerprint_from_log_entry(entry_dict)
    assert "src/butlers/core/db.py" in result.call_site


def test_compute_fingerprint_from_log_entry_fallback_unknown():
    """compute_fingerprint_from_log_entry falls back to <unknown> with no call site info."""
    entry_dict = {
        "level": "error",
        "event": "Something bad",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    result = compute_fingerprint_from_log_entry(entry_dict)
    assert result.call_site == "<unknown>:<unknown>"
    assert len(result.fingerprint) == 64


# ---------------------------------------------------------------------------
# Finding aggregation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_fingerprint_aggregated(tmp_path):
    """Multiple entries with the same fingerprint are aggregated into one finding."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    # Three identical-fingerprint entries
    lines = [
        _make_log_line(
            event="DB down", exception="ConnectionError", ts=now - timedelta(seconds=10)
        ),
        _make_log_line(event="DB down", exception="ConnectionError", ts=now - timedelta(seconds=5)),
        _make_log_line(event="DB down", exception="ConnectionError", ts=now),
    ]
    _make_log_file(butlers_dir / "finance.log", lines)

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    f = findings[0]
    assert f.occurrence_count == 3
    assert f.first_seen <= f.last_seen


@pytest.mark.asyncio
async def test_aggregation_first_last_seen_timestamps(tmp_path):
    """Aggregated finding tracks first_seen and last_seen correctly."""
    butlers_dir = tmp_path / "butlers"
    t1 = datetime.now(UTC) - timedelta(minutes=10)
    t2 = datetime.now(UTC) - timedelta(minutes=5)
    t3 = datetime.now(UTC) - timedelta(minutes=2)

    lines = [
        _make_log_line(event="DB down", exception="ConnectionError", ts=t2),
        _make_log_line(event="DB down", exception="ConnectionError", ts=t1),
        _make_log_line(event="DB down", exception="ConnectionError", ts=t3),
    ]
    _make_log_file(butlers_dir / "finance.log", lines)

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    f = findings[0]
    # first_seen should be t1 (earliest), last_seen should be t3 (latest)
    assert abs((f.first_seen - t1).total_seconds()) < 2
    assert abs((f.last_seen - t3).total_seconds()) < 2


# ---------------------------------------------------------------------------
# Performance caps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_entries_per_scan_truncates(tmp_path, caplog):
    """Scanner stops and logs WARNING when max_entries_per_scan is reached."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    # Create 10 unique error lines
    lines = [
        _make_log_line(
            event=f"Error event {i}", exception=f"Error{i}", ts=now, logger_name=f"mod.sub{i}"
        )
        for i in range(10)
    ]
    _make_log_file(butlers_dir / "finance.log", lines)

    source = LogScannerSource(log_root=tmp_path, max_entries_per_scan=3)
    with caplog.at_level(logging.WARNING):
        await source.discover(lookback_minutes=15)

    warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    truncated = [m for m in warn_msgs if "truncated" in m.lower() or "max_entries" in m.lower()]
    assert len(truncated) >= 1


@pytest.mark.asyncio
async def test_max_findings_per_scan_caps_findings(tmp_path, caplog):
    """Scanner caps unique findings at max_findings_per_scan and logs WARNING."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    # Create 10 unique error fingerprints
    lines = [
        _make_log_line(
            event=f"Unique error {i}",
            exception=f"UniqueError{i}",
            ts=now,
            logger_name=f"unique.mod{i}",
        )
        for i in range(10)
    ]
    _make_log_file(butlers_dir / "finance.log", lines)

    source = LogScannerSource(log_root=tmp_path, max_findings_per_scan=3)
    with caplog.at_level(logging.WARNING):
        findings = await source.discover(lookback_minutes=15)

    assert len(findings) <= 3
    warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    cap_msgs = [m for m in warn_msgs if "cap" in m.lower() or "finding" in m.lower()]
    assert len(cap_msgs) >= 1
