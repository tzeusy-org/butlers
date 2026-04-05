"""Tests for butlers.core.qa.sources.log_scanner.LogScannerSource — condensed.

Covers:
- DiscoverySource protocol compliance
- Log file discovery: butlers/, connectors/, uvicorn/ subdirs; qa.log excluded
- Missing log directory is non-fatal
- JSON-lines parsing: valid, malformed, missing fields
- Temporal filtering
- Severity filtering: ERROR/CRITICAL included; WARNING with crash patterns; INFO excluded
- Finding structure and fingerprint stability
- Finding aggregation
- Performance caps
- compute_fingerprint_from_log_entry compatibility
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


def test_log_scanner_protocol(tmp_path) -> None:
    """LogScannerSource implements DiscoverySource with an async discover() method."""
    import inspect

    source = LogScannerSource(log_root=tmp_path)
    assert isinstance(source, DiscoverySource)
    assert source.name == "log_scanner"
    assert inspect.iscoroutinefunction(source.discover)


# ---------------------------------------------------------------------------
# Log file discovery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subdir,filename,butler_name,log_kwargs",
    [
        ("butlers", "finance.log", "finance", {}),
        ("connectors", "telegram.log", None, {"butler_name": "connector_telegram"}),
        ("uvicorn", "server.log", None, {"butler_name": "uvicorn"}),
    ],
)
@pytest.mark.asyncio
async def test_discover_finds_logs_in_subdirs(tmp_path, subdir, filename, butler_name, log_kwargs):
    """Scanner reads from butlers/, connectors/, and uvicorn/ subdirs."""
    now = datetime.now(UTC)
    line = _make_log_line(ts=now, **log_kwargs)
    _make_log_file(tmp_path / subdir / filename, [line])

    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    if butler_name:
        assert findings[0].source_butler == butler_name


@pytest.mark.asyncio
async def test_discover_excludes_qa_log(tmp_path) -> None:
    """logs/butlers/qa.log is excluded from scanning."""
    now = datetime.now(UTC)
    _make_log_file(tmp_path / "butlers" / "qa.log", [_make_log_line(ts=now)])
    findings = await LogScannerSource(log_root=tmp_path).discover(lookback_minutes=15)
    assert len(findings) == 0


@pytest.mark.asyncio
async def test_missing_subdir_is_nonfatal(tmp_path, caplog) -> None:
    """Missing subdirectories are skipped with a DEBUG log."""
    now = datetime.now(UTC)
    _make_log_file(tmp_path / "butlers" / "finance.log", [_make_log_line(ts=now)])

    source = LogScannerSource(log_root=tmp_path)
    with caplog.at_level(logging.DEBUG):
        findings = await source.discover(lookback_minutes=15)

    assert len(findings) == 1
    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("skipping missing directory" in m for m in debug_msgs)


# ---------------------------------------------------------------------------
# JSON-lines parsing
# ---------------------------------------------------------------------------


def test_parse_log_line() -> None:
    """Valid line → LogEntry; malformed/missing-fields → None; filename used as fallback name."""
    now = datetime.now(UTC)
    # Valid
    line = _make_log_line(ts=now, event="DB connection failed", exception="asyncpg.PostgresError")
    entry = _parse_log_line(line, "finance")
    assert entry is not None
    assert entry.level == "error"
    assert entry.butler_name == "finance"
    assert entry.exception == "asyncpg.PostgresError"

    # Malformed JSON
    assert _parse_log_line("not valid json {{{", "butler") is None

    # Missing event
    missing_event = json.dumps({"level": "error", "timestamp": now.isoformat()})
    assert _parse_log_line(missing_event, "b") is None

    # Missing timestamp
    assert _parse_log_line(json.dumps({"level": "error", "event": "test"}), "b") is None

    # Filename as butler_name fallback
    data = {"level": "error", "event": "fail", "timestamp": now.isoformat()}
    entry2 = _parse_log_line(json.dumps(data), "travel")
    assert entry2 is not None
    assert entry2.butler_name == "travel"


# ---------------------------------------------------------------------------
# Temporal filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_temporal_filtering(tmp_path) -> None:
    """Recent entries included; old entries excluded."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)

    _make_log_file(
        butlers_dir / "finance.log",
        [
            _make_log_line(ts=now - timedelta(minutes=5)),     # recent → included
            _make_log_line(ts=now - timedelta(minutes=30)),    # old → excluded
        ],
    )
    findings = await LogScannerSource(log_root=tmp_path).discover(lookback_minutes=15)
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# Severity filtering
# ---------------------------------------------------------------------------


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
def test_severity_filtering(level, event, exception, expected) -> None:
    """Severity filter: ERROR/CRITICAL always in; WARNING with crash pattern; INFO/DEBUG out."""
    now = datetime.now(UTC)
    entry = LogEntry(level=level, event=event, timestamp=now, butler_name="b", exception=exception)
    assert _should_include_entry(entry) is expected


# ---------------------------------------------------------------------------
# Finding structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finding_structure(tmp_path) -> None:
    """QaFinding fields are populated correctly and PII is stripped."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    _make_log_file(
        butlers_dir / "finance.log",
        [
            _make_log_line(
                event="Failed to connect to database",
                ts=now,
                logger_name="butlers.core.db",
                exception="asyncpg.PostgresConnectionError",
            )
        ],
    )

    findings = await LogScannerSource(log_root=tmp_path, repo_root=tmp_path).discover(
        lookback_minutes=15
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.source_type == "log_scanner"
    assert f.source_butler == "finance"
    assert len(f.fingerprint) == 64
    assert f.occurrence_count == 1
    assert f.exception_type == "asyncpg.PostgresConnectionError"
    assert f.source_file == "finance.log"

    # PII stripped
    _make_log_file(
        butlers_dir / "health.log",
        [_make_log_line(event="Error processing email user@example.com", ts=now)],
    )
    findings2 = await LogScannerSource(log_root=tmp_path, repo_root=tmp_path).discover(
        lookback_minutes=15
    )
    health_findings = [f for f in findings2 if f.source_file == "health.log"]
    assert health_findings
    assert "user@example.com" not in health_findings[0].event_summary


# ---------------------------------------------------------------------------
# Fingerprint stability and compute_fingerprint_from_log_entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fingerprint_properties(tmp_path) -> None:
    """Same entry → same fingerprint across scans; different errors → different fingerprints."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    line1 = _make_log_line(
        event="DB down", exception="ConnectionError", ts=now, logger_name="mod.a"
    )
    line2 = _make_log_line(
        event="File not found", exception="FileNotFoundError", ts=now, logger_name="mod.b"
    )
    _make_log_file(butlers_dir / "finance.log", [line1, line2])

    source = LogScannerSource(log_root=tmp_path)
    findings1 = await source.discover(lookback_minutes=15)
    findings2 = await source.discover(lookback_minutes=15)

    # Stability
    fps1 = {f.fingerprint for f in findings1}
    fps2 = {f.fingerprint for f in findings2}
    assert fps1 == fps2

    # Different errors → different fingerprints
    assert len(fps1) == 2


def test_compute_fingerprint_from_log_entry() -> None:
    """compute_fingerprint_from_log_entry uses traceback for call_site; falls back to <unknown>."""
    now = datetime.now(UTC)

    # With traceback
    traceback_str = (
        "Traceback (most recent call last):\n"
        '  File "src/butlers/core/db.py", line 42, in connect\n'
        "    raise e\nasyncpg.exceptions.PostgresConnectionError: could not connect\n"
    )
    result = compute_fingerprint_from_log_entry(
        {
            "level": "error",
            "event": "Connection failed",
            "timestamp": now.isoformat(),
            "logger": "butlers.core.db",
            "exception": "asyncpg.exceptions.PostgresConnectionError",
            "traceback": traceback_str,
        }
    )
    assert "src/butlers/core/db.py" in result.call_site

    # Fallback
    result2 = compute_fingerprint_from_log_entry(
        {"level": "error", "event": "Something bad", "timestamp": now.isoformat()}
    )
    assert result2.call_site == "<unknown>:<unknown>"
    assert len(result2.fingerprint) == 64


@pytest.mark.asyncio
async def test_compute_fingerprint_compatible_with_scanner(tmp_path) -> None:
    """compute_fingerprint_from_log_entry produces same fingerprint as log scanner."""
    now = datetime.now(UTC)
    long_event = "DB connection failed: " + "x" * 250
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

    _make_log_file(tmp_path / "butlers" / "finance.log", [json.dumps(entry_dict)])
    source = LogScannerSource(log_root=tmp_path)
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    assert findings[0].fingerprint == result.fingerprint


# ---------------------------------------------------------------------------
# Finding aggregation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finding_aggregation(tmp_path) -> None:
    """Same fingerprint entries are aggregated; first_seen/last_seen tracked correctly."""
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
    assert f.occurrence_count == 3
    assert abs((f.first_seen - t1).total_seconds()) < 2
    assert abs((f.last_seen - t3).total_seconds()) < 2


# ---------------------------------------------------------------------------
# Performance caps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_performance_caps(tmp_path, caplog) -> None:
    """max_entries_per_scan and max_findings_per_scan emit WARNING when hit."""
    butlers_dir = tmp_path / "butlers"
    now = datetime.now(UTC)
    lines = [
        _make_log_line(
            event=f"Error event {i}", exception=f"Error{i}", ts=now, logger_name=f"mod.sub{i}"
        )
        for i in range(10)
    ]
    _make_log_file(butlers_dir / "finance.log", lines)

    # max_entries_per_scan
    with caplog.at_level(logging.WARNING):
        await LogScannerSource(log_root=tmp_path, max_entries_per_scan=3).discover(
            lookback_minutes=15
        )
    assert any(
        "truncated" in r.message.lower() or "max_entries" in r.message.lower()
        for r in caplog.records if r.levelno == logging.WARNING
    )

    caplog.clear()
    # max_findings_per_scan
    with caplog.at_level(logging.WARNING):
        findings = await LogScannerSource(log_root=tmp_path, max_findings_per_scan=3).discover(
            lookback_minutes=15
        )
    assert len(findings) <= 3
    assert any(
        "cap" in r.message.lower() or "finding" in r.message.lower()
        for r in caplog.records if r.levelno == logging.WARNING
    )
