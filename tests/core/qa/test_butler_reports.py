"""Tests for butlers.core.qa.sources.butler_reports.ButlerReportsSource.

Covers:
- DiscoverySource protocol compliance
- accept(): enqueues a finding
- discover(): drains the buffer (buffer empty after call)
- Overflow: oldest entries dropped with WARNING when buffer exceeds max_buffer
- Multiple accepts followed by discover: all findings returned
- lookback_minutes parameter ignored (buffer is already in-window)
- Context field stored in finding
- buffer_size property
"""

from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime

import pytest

from butlers.core.qa.sources.butler_reports import ButlerReportsSource
from butlers.core.qa.sources.protocol import DiscoverySource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fp(i: int = 0) -> str:
    """Return a 64-char hex fingerprint string."""
    return hex(i)[2:].zfill(64)[:64]


async def _accept(source: ButlerReportsSource, i: int = 0) -> None:
    await source.accept(
        fingerprint=_fp(i),
        exception_type=f"Error{i}",
        call_site=f"mod.sub:func{i}",
        severity=2,
        event_summary=f"Error event {i}",
        source_butler=f"butler{i}",
    )


# ---------------------------------------------------------------------------
# Protocol compliance, buffer behaviour, overflow, context
# ---------------------------------------------------------------------------


def test_butler_reports_protocol_and_discover_is_async():
    """ButlerReportsSource implements DiscoverySource; discover() is async."""
    source = ButlerReportsSource()
    assert isinstance(source, DiscoverySource)
    assert source.name == "butler_reports"
    assert inspect.iscoroutinefunction(source.discover)


@pytest.mark.asyncio
async def test_buffer_lifecycle_fields_and_lookback():
    """Empty buffer → []; accept→discover roundtrip with correct fields; buffer drains; lookback ignored; multiple accepts all returned."""
    source = ButlerReportsSource()
    # Empty buffer
    assert await source.discover(lookback_minutes=15) == []

    # Single accept → discover has correct fields and drains
    before = datetime.now(UTC)
    await _accept(source, i=0)
    after = datetime.now(UTC)

    assert source.buffer_size == 1
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    f = findings[0]
    assert f.source_type == "butler_reports"
    assert f.fingerprint == _fp(0)
    assert f.exception_type == "Error0"
    assert f.source_butler == "butler0"
    assert f.occurrence_count == 1
    assert before <= f.first_seen <= after
    assert before <= f.last_seen <= after
    assert before <= f.timestamp <= after

    # Buffer now empty
    assert source.buffer_size == 0
    assert await source.discover(lookback_minutes=15) == []

    # lookback_minutes irrelevant for buffer-based source
    await _accept(source, i=0)
    f1 = (await source.discover(lookback_minutes=1))[0]
    await _accept(source, i=0)
    f2 = (await source.discover(lookback_minutes=999))[0]
    assert f1.fingerprint == f2.fingerprint

    # Multiple accepts all returned
    for i in range(5):
        await _accept(source, i=i)
    assert source.buffer_size == 5
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 5
    assert len({f.fingerprint for f in findings}) == 5


@pytest.mark.asyncio
async def test_overflow_drops_oldest_with_warning_and_successive(caplog):
    """Overflow drops oldest with WARNING; successive overflows keep dropping oldest."""
    # Single overflow
    source = ButlerReportsSource(max_buffer=3)
    for i in range(3):
        await _accept(source, i=i)
    assert source.buffer_size == 3
    with caplog.at_level(logging.WARNING):
        await _accept(source, i=99)
    warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("buffer full" in m.lower() or "dropped" in m.lower() for m in warn_msgs)
    assert source.buffer_size == 3
    findings = await source.discover(lookback_minutes=15)
    fps = {f.fingerprint for f in findings}
    assert _fp(0) not in fps
    assert _fp(99) in fps

    # Successive overflows
    source2 = ButlerReportsSource(max_buffer=2)
    await _accept(source2, i=0)
    await _accept(source2, i=1)
    await _accept(source2, i=2)  # drops 0
    await _accept(source2, i=3)  # drops 1
    findings2 = await source2.discover(lookback_minutes=15)
    fps2 = {f.fingerprint for f in findings2}
    assert _fp(0) not in fps2
    assert _fp(1) not in fps2
    assert _fp(2) in fps2
    assert _fp(3) in fps2


@pytest.mark.asyncio
async def test_context_field():
    """Context parameter is stored in finding; None when not provided."""
    source = ButlerReportsSource()

    await source.accept(
        fingerprint=_fp(0),
        exception_type="ValueError",
        call_site="mod:func",
        severity=2,
        event_summary="some error",
        source_butler="finance",
        context="agent reasoning context here",
    )
    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    assert findings[0].context == "agent reasoning context here"

    # Without context
    await _accept(source, i=1)
    findings2 = await source.discover(lookback_minutes=15)
    assert findings2[0].context is None
