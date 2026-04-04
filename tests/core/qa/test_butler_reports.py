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
# Protocol compliance
# ---------------------------------------------------------------------------


def test_butler_reports_implements_protocol():
    """ButlerReportsSource implements the DiscoverySource protocol."""
    source = ButlerReportsSource()
    assert isinstance(source, DiscoverySource)
    assert source.name == "butler_reports"


def test_butler_reports_discover_is_async():
    """discover() is an async method."""
    import inspect

    source = ButlerReportsSource()
    assert inspect.iscoroutinefunction(source.discover)


# ---------------------------------------------------------------------------
# Buffer behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_buffer_returns_empty_list():
    """discover() on an empty buffer returns []."""
    source = ButlerReportsSource()
    findings = await source.discover(lookback_minutes=15)
    assert findings == []


@pytest.mark.asyncio
async def test_accept_then_discover():
    """Finding enqueued via accept() is returned by discover()."""
    source = ButlerReportsSource()
    await _accept(source, i=0)

    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 1
    f = findings[0]
    assert f.source_type == "butler_reports"
    assert f.fingerprint == _fp(0)
    assert f.exception_type == "Error0"
    assert f.source_butler == "butler0"
    assert f.occurrence_count == 1


@pytest.mark.asyncio
async def test_discover_drains_buffer():
    """Buffer is empty after discover()."""
    source = ButlerReportsSource()
    await _accept(source, i=0)
    await _accept(source, i=1)

    findings1 = await source.discover(lookback_minutes=15)
    assert len(findings1) == 2

    # Buffer should now be empty
    findings2 = await source.discover(lookback_minutes=15)
    assert len(findings2) == 0


@pytest.mark.asyncio
async def test_multiple_accepts_all_returned():
    """Multiple accepts produce multiple findings on discover()."""
    source = ButlerReportsSource()
    for i in range(5):
        await _accept(source, i=i)

    findings = await source.discover(lookback_minutes=15)
    assert len(findings) == 5
    fps = {f.fingerprint for f in findings}
    assert len(fps) == 5


@pytest.mark.asyncio
async def test_lookback_minutes_ignored():
    """lookback_minutes parameter is irrelevant for buffer-based source."""
    source = ButlerReportsSource()
    await _accept(source, i=0)

    # Same finding regardless of lookback
    f1 = (await source.discover(lookback_minutes=1))[0]
    await _accept(source, i=0)
    f2 = (await source.discover(lookback_minutes=999))[0]
    assert f1.fingerprint == f2.fingerprint


# ---------------------------------------------------------------------------
# Overflow behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overflow_drops_oldest_with_warning(caplog):
    """When buffer is full, the oldest entry is dropped with a WARNING."""
    source = ButlerReportsSource(max_buffer=3)

    # Fill to capacity
    for i in range(3):
        await _accept(source, i=i)

    assert source.buffer_size == 3

    # One more — should drop fingerprint 0 (oldest)
    with caplog.at_level(logging.WARNING):
        await _accept(source, i=99)

    warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    overflow_msgs = [m for m in warn_msgs if "buffer full" in m.lower() or "dropped" in m.lower()]
    assert len(overflow_msgs) >= 1

    # Buffer still at max_buffer, oldest dropped
    assert source.buffer_size == 3
    findings = await source.discover(lookback_minutes=15)
    fps = {f.fingerprint for f in findings}
    # fp(0) should have been dropped
    assert _fp(0) not in fps
    assert _fp(99) in fps


@pytest.mark.asyncio
async def test_overflow_successive_drops():
    """Successive overflows keep dropping the oldest entry each time."""
    source = ButlerReportsSource(max_buffer=2)

    await _accept(source, i=0)
    await _accept(source, i=1)
    await _accept(source, i=2)  # drops 0
    await _accept(source, i=3)  # drops 1

    findings = await source.discover(lookback_minutes=15)
    fps = {f.fingerprint for f in findings}
    assert _fp(0) not in fps
    assert _fp(1) not in fps
    assert _fp(2) in fps
    assert _fp(3) in fps


# ---------------------------------------------------------------------------
# Context field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_stored_in_finding():
    """Context parameter is stored in the QaFinding.context field."""
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


@pytest.mark.asyncio
async def test_context_none_when_not_provided():
    """Context is None when not passed."""
    source = ButlerReportsSource()
    await _accept(source, i=0)
    findings = await source.discover(lookback_minutes=15)
    assert findings[0].context is None


# ---------------------------------------------------------------------------
# buffer_size property
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_buffer_size_property():
    """buffer_size reflects the current number of buffered findings."""
    source = ButlerReportsSource()
    assert source.buffer_size == 0

    await _accept(source, i=0)
    assert source.buffer_size == 1

    await _accept(source, i=1)
    assert source.buffer_size == 2

    await source.discover(lookback_minutes=15)
    assert source.buffer_size == 0


# ---------------------------------------------------------------------------
# Finding fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finding_has_correct_source_type():
    """Finding source_type is 'butler_reports'."""
    source = ButlerReportsSource()
    await _accept(source, i=0)
    findings = await source.discover(lookback_minutes=15)
    assert findings[0].source_type == "butler_reports"


@pytest.mark.asyncio
async def test_finding_timestamps_are_populated():
    """QaFinding first_seen, last_seen, and timestamp are set."""
    source = ButlerReportsSource()
    before = datetime.now(UTC)
    await _accept(source, i=0)
    after = datetime.now(UTC)

    findings = await source.discover(lookback_minutes=15)
    f = findings[0]
    assert before <= f.first_seen <= after
    assert before <= f.last_seen <= after
    assert before <= f.timestamp <= after
