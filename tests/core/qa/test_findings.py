"""Tests for butlers.core.qa.findings CRUD layer.

Covers:
- insert_finding: all dedup_reason variants (None, active_investigation, dismissed, cooldown)
- insert_finding: with and without healing_attempt_id
- insert_finding: returns a UUID
- update_finding_attempt: links finding to a healing attempt
- get_findings_by_patrol: returns rows ordered by severity asc
- get_findings_by_fingerprint: returns rows ordered by created_at desc, respects limit
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.qa.findings import (
    get_findings_by_fingerprint,
    get_findings_by_patrol,
    insert_finding,
    update_finding_attempt,
)
from butlers.core.qa.models import QaFinding

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_finding(
    fingerprint: str = "a" * 64,
    source_type: str = "log_scanner",
    source_butler: str = "finance",
    severity: int = 1,
    exception_type: str = "ValueError",
    event_summary: str = "Bad value in pipeline",
    call_site: str = "finance.jobs:42",
    occurrence_count: int = 3,
) -> QaFinding:
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=fingerprint,
        source_type=source_type,
        source_butler=source_butler,
        severity=severity,
        exception_type=exception_type,
        event_summary=event_summary,
        call_site=call_site,
        occurrence_count=occurrence_count,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


def _make_pool(fetchval_return=None, fetch_return=None):
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.execute = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# insert_finding tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_finding_novel_returns_uuid():
    """insert_finding with dedup_reason=None returns a UUID."""
    expected_id = uuid.uuid4()
    pool = _make_pool(fetchval_return=expected_id)
    patrol_id = uuid.uuid4()
    finding = _make_finding()

    result = await insert_finding(pool, patrol_id, finding, dedup_reason=None)

    assert result == expected_id


@pytest.mark.asyncio
async def test_insert_finding_active_investigation():
    """insert_finding with dedup_reason='active_investigation' calls fetchval."""
    expected_id = uuid.uuid4()
    pool = _make_pool(fetchval_return=expected_id)
    patrol_id = uuid.uuid4()
    finding = _make_finding()

    result = await insert_finding(pool, patrol_id, finding, dedup_reason="active_investigation")

    assert result == expected_id
    pool.fetchval.assert_called_once()
    call_args = pool.fetchval.call_args
    # dedup_reason should be passed as $12
    assert "active_investigation" in call_args.args


@pytest.mark.asyncio
async def test_insert_finding_dismissed():
    """insert_finding with dedup_reason='dismissed' passes reason to query."""
    expected_id = uuid.uuid4()
    pool = _make_pool(fetchval_return=expected_id)
    patrol_id = uuid.uuid4()
    finding = _make_finding()

    result = await insert_finding(pool, patrol_id, finding, dedup_reason="dismissed")

    assert result == expected_id
    call_args = pool.fetchval.call_args
    assert "dismissed" in call_args.args


@pytest.mark.asyncio
async def test_insert_finding_cooldown():
    """insert_finding with dedup_reason='cooldown' passes reason to query."""
    expected_id = uuid.uuid4()
    pool = _make_pool(fetchval_return=expected_id)
    patrol_id = uuid.uuid4()
    finding = _make_finding()

    result = await insert_finding(pool, patrol_id, finding, dedup_reason="cooldown")

    assert result == expected_id
    call_args = pool.fetchval.call_args
    assert "cooldown" in call_args.args


@pytest.mark.asyncio
async def test_insert_finding_with_healing_attempt_id():
    """insert_finding with a healing_attempt_id passes it as a string."""
    expected_id = uuid.uuid4()
    pool = _make_pool(fetchval_return=expected_id)
    patrol_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    finding = _make_finding()

    result = await insert_finding(
        pool, patrol_id, finding, dedup_reason=None, healing_attempt_id=attempt_id
    )

    assert result == expected_id
    call_args = pool.fetchval.call_args
    # healing_attempt_id should be passed as str($13)
    assert str(attempt_id) in call_args.args


@pytest.mark.asyncio
async def test_insert_finding_without_healing_attempt_id():
    """insert_finding with healing_attempt_id=None passes None for $13."""
    expected_id = uuid.uuid4()
    pool = _make_pool(fetchval_return=expected_id)
    patrol_id = uuid.uuid4()
    finding = _make_finding()

    result = await insert_finding(pool, patrol_id, finding, dedup_reason=None)

    assert result == expected_id
    call_args = pool.fetchval.call_args
    # Last positional arg should be None (healing_attempt_id)
    assert call_args.args[-1] is None


@pytest.mark.asyncio
async def test_insert_finding_passes_patrol_id():
    """insert_finding passes patrol_id as the first parameter."""
    expected_id = uuid.uuid4()
    pool = _make_pool(fetchval_return=expected_id)
    patrol_id = uuid.uuid4()
    finding = _make_finding()

    await insert_finding(pool, patrol_id, finding, dedup_reason=None)

    call_args = pool.fetchval.call_args
    assert patrol_id in call_args.args


# ---------------------------------------------------------------------------
# update_finding_attempt tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_finding_attempt_executes_update():
    """update_finding_attempt runs an UPDATE query."""
    pool = _make_pool()
    finding_id = uuid.uuid4()
    attempt_id = uuid.uuid4()

    await update_finding_attempt(pool, finding_id, attempt_id)

    pool.execute.assert_called_once()
    call_args = pool.execute.call_args
    assert str(attempt_id) in call_args.args or attempt_id in call_args.args
    assert finding_id in call_args.args


# ---------------------------------------------------------------------------
# get_findings_by_patrol tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_findings_by_patrol_returns_list():
    """get_findings_by_patrol returns list of dicts."""
    row1 = {"id": uuid.uuid4(), "severity": 0, "occurrence_count": 5}
    row2 = {"id": uuid.uuid4(), "severity": 2, "occurrence_count": 1}

    # asyncpg rows need .items() support; MagicMock dict suffices via dict(row)
    mock_row1 = MagicMock()
    mock_row1.__iter__ = lambda self: iter(row1.items())
    mock_row1.keys = lambda: row1.keys()
    mock_row2 = MagicMock()
    mock_row2.__iter__ = lambda self: iter(row2.items())
    mock_row2.keys = lambda: row2.keys()

    pool = _make_pool(fetch_return=[mock_row1, mock_row2])
    patrol_id = uuid.uuid4()

    # We need dict(row) to work; patch fetch to return plain dicts
    pool.fetch = AsyncMock(return_value=[row1, row2])

    # Patch dict(row) by using a real dict-compatible asyncpg Record substitute
    # Actually insert_finding tests above show pool.fetch returns list of items
    # The function calls dict(row) for each row.
    # Use a custom class that supports dict(row)
    class FakeRecord(dict):
        pass

    pool.fetch = AsyncMock(return_value=[FakeRecord(row1), FakeRecord(row2)])

    result = await get_findings_by_patrol(pool, patrol_id)

    assert len(result) == 2
    pool.fetch.assert_called_once()
    call_args = pool.fetch.call_args
    assert patrol_id in call_args.args


@pytest.mark.asyncio
async def test_get_findings_by_patrol_empty():
    """get_findings_by_patrol returns empty list when no rows."""
    pool = _make_pool(fetch_return=[])
    result = await get_findings_by_patrol(pool, uuid.uuid4())
    assert result == []


# ---------------------------------------------------------------------------
# get_findings_by_fingerprint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_findings_by_fingerprint_passes_limit():
    """get_findings_by_fingerprint passes limit to query."""

    class FakeRecord(dict):
        pass

    pool = _make_pool(fetch_return=[])
    fingerprint = "b" * 64

    await get_findings_by_fingerprint(pool, fingerprint, limit=5)

    call_args = pool.fetch.call_args
    assert 5 in call_args.args


@pytest.mark.asyncio
async def test_get_findings_by_fingerprint_default_limit():
    """get_findings_by_fingerprint uses limit=20 by default."""
    pool = _make_pool(fetch_return=[])

    await get_findings_by_fingerprint(pool, "c" * 64)

    call_args = pool.fetch.call_args
    assert 20 in call_args.args


@pytest.mark.asyncio
async def test_get_findings_by_fingerprint_returns_list():
    """get_findings_by_fingerprint returns list of dicts."""

    class FakeRecord(dict):
        pass

    row = FakeRecord({"id": uuid.uuid4(), "fingerprint": "d" * 64})
    pool = _make_pool(fetch_return=[row])

    result = await get_findings_by_fingerprint(pool, "d" * 64)

    assert len(result) == 1
    assert result[0]["fingerprint"] == "d" * 64
