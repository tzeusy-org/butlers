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
    """ERROR/CRITICAL always in; WARNING with crash pattern; INFO/debug out."""
    entry = LogEntry(
        level=level, event=event, timestamp=datetime.now(UTC), butler_name="b", exception=exception
    )
    assert _should_include_entry(entry) is expected


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

    A log file with 10 000 INFO lines followed by a single ERROR must still
    produce a finding when max_entries_per_scan is set to a small value (e.g. 5),
    because INFO lines do not count against the budget.
    """
    now = datetime.now(UTC)
    benign_lines = [
        json.dumps(
            {
                "level": "info",
                "event": f"Request processed {i}",
                "timestamp": (now - timedelta(seconds=i)).isoformat(),
                "butler_name": "finance",
                "logger": "butlers.http",
            }
        )
        for i in range(10_000)
    ]
    error_line = _line(
        level="error",
        event="Critical connector failure",
        exception="ConnectionRefusedError",
        ts=now,
    )
    _write(tmp_path / "butlers" / "finance.log", benign_lines + [error_line])

    # Even with a tiny candidate budget (5), the error must be discovered
    # because INFO lines are not counted.
    source = LogScannerSource(log_root=tmp_path, max_entries_per_scan=5)
    findings = await source.discover(lookback_minutes=30)

    assert len(findings) == 1
    assert "connector failure" in findings[0].event_summary.lower()
    # No truncation should have occurred (only 1 candidate entry)
    assert not source.last_truncated


@pytest.mark.asyncio
async def test_later_file_not_starved(tmp_path):
    """Errors in a later-sorted log file must be discoverable despite an earlier
    file containing many error entries.

    The scanner shuffles file order, so we run multiple times and assert that
    the later file's finding is discovered in at least some runs (i.e. it is not
    systematically excluded).  With a budget cap of 3 and 4 errors in the first
    file (alphabetically), the second file would never be reached under the old
    deterministic ordering.
    """
    now = datetime.now(UTC)

    # "aaa.log" — has 4 distinct errors (fills a budget of 3)
    aaa_lines = [
        _line(event=f"Error aaa {i}", exception=f"ErrA{i}", ts=now, logger_name=f"mod.a{i}")
        for i in range(4)
    ]
    # "zzz.log" — has a unique error that should be reachable
    zzz_lines = [
        _line(
            event="ZZZ unique transport error",
            exception="TransportError",
            ts=now,
            logger_name="mod.transport",
        )
    ]
    _write(tmp_path / "butlers" / "aaa.log", aaa_lines)
    _write(tmp_path / "butlers" / "zzz.log", zzz_lines)

    # Run 20 iterations: with random shuffle, zzz.log must appear first sometimes.
    source = LogScannerSource(log_root=tmp_path, max_entries_per_scan=3)
    zzz_found_count = 0
    for _ in range(20):
        findings = await source.discover(lookback_minutes=15)
        if any(
            "zzz" in f.event_summary.lower() or "transport" in f.event_summary.lower()
            for f in findings
        ):
            zzz_found_count += 1

    # At least 1 of 20 runs must find the zzz error (probability of never
    # picking zzz first in 20 shuffles is (1/2)^20 ≈ 1e-6; acceptable).
    assert zzz_found_count > 0, (
        "zzz.log error was never discovered in 20 runs — file ordering is likely deterministic"
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
    assert not source_clean.last_truncated
    assert source_clean.last_truncated_reason is None

    # Entry cap hit
    lines = [
        _line(event=f"Error {i}", exception=f"Err{i}", ts=now, logger_name=f"mod.x{i}")
        for i in range(10)
    ]
    _write(tmp_path / "butlers" / "busy.log", lines)
    source_entry_cap = LogScannerSource(log_root=tmp_path, max_entries_per_scan=3)
    await source_entry_cap.discover(lookback_minutes=15)
    assert source_entry_cap.last_truncated
    assert source_entry_cap.last_truncated_reason in ("entries", "entries_and_findings")

    # Finding cap hit
    source_finding_cap = LogScannerSource(log_root=tmp_path, max_findings_per_scan=2)
    await source_finding_cap.discover(lookback_minutes=15)
    assert source_finding_cap.last_truncated
    assert source_finding_cap.last_truncated_reason in ("findings", "entries_and_findings")
