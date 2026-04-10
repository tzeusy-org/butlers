"""Tests for butlers.core.qa.findings CRUD layer — condensed.

Covers:
- insert_finding: all dedup_reason variants; returns UUID; passes patrol_id;
  healing_attempt_id passed as str when set, None otherwise
- update_finding_attempt: runs UPDATE with finding_id and attempt_id
- get_findings_by_patrol: returns list of dicts; empty list
- get_findings_by_fingerprint: respects limit; default limit=20; returns list of dicts
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

pytestmark = pytest.mark.unit


class FakeRecord(dict):
    pass


def _make_finding(**kwargs) -> QaFinding:
    now = datetime.now(UTC)
    defaults = dict(
        fingerprint="a" * 64,
        source_type="log_scanner",
        source_butler="finance",
        severity=1,
        exception_type="ValueError",
        event_summary="Bad value",
        call_site="finance.jobs:42",
        occurrence_count=3,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )
    defaults.update(kwargs)
    return QaFinding(**defaults)


def _pool(fetchval=None, fetch=None):
    p = MagicMock()
    p.fetchval = AsyncMock(return_value=fetchval)
    p.fetch = AsyncMock(return_value=fetch or [])
    p.execute = AsyncMock()
    return p


@pytest.mark.asyncio
async def test_insert_finding():
    """All dedup variants return UUID; passes patrol_id; healing_attempt_id as str or None."""
    expected_id = uuid.uuid4()
    patrol_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    finding = _make_finding()

    for reason in (None, "active_investigation", "dismissed", "cooldown"):
        pool = _pool(fetchval=expected_id)
        result = await insert_finding(pool, patrol_id, finding, dedup_reason=reason)
        assert result == expected_id
        args = pool.fetchval.call_args.args
        assert patrol_id in args
        if reason is not None:
            assert reason in args

    # healing_attempt_id passed as str
    pool2 = _pool(fetchval=expected_id)
    await insert_finding(
        pool2, patrol_id, finding, dedup_reason=None, healing_attempt_id=attempt_id
    )
    assert str(attempt_id) in pool2.fetchval.call_args.args

    # healing_attempt_id=None passes None
    pool3 = _pool(fetchval=expected_id)
    await insert_finding(pool3, patrol_id, finding, dedup_reason=None)
    assert pool3.fetchval.call_args.args[-1] is None


@pytest.mark.asyncio
async def test_update_finding_attempt():
    """update_finding_attempt runs UPDATE with both IDs."""
    pool = _pool()
    finding_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    await update_finding_attempt(pool, finding_id, attempt_id)
    pool.execute.assert_called_once()
    args = pool.execute.call_args.args
    assert finding_id in args or str(finding_id) in args
    assert attempt_id in args or str(attempt_id) in args


@pytest.mark.asyncio
async def test_get_findings_by_patrol():
    """Returns list of dicts; empty list when no rows."""
    row1 = FakeRecord({"id": uuid.uuid4(), "severity": 0})
    row2 = FakeRecord({"id": uuid.uuid4(), "severity": 2})
    patrol_id = uuid.uuid4()

    pool = _pool(fetch=[row1, row2])
    result = await get_findings_by_patrol(pool, patrol_id)
    assert len(result) == 2
    assert patrol_id in pool.fetch.call_args.args

    assert await get_findings_by_patrol(_pool(fetch=[]), uuid.uuid4()) == []


@pytest.mark.asyncio
async def test_get_findings_by_fingerprint():
    """Passes limit; default limit=20; returns list of dicts."""
    fp = "b" * 64
    row = FakeRecord({"id": uuid.uuid4(), "fingerprint": fp})

    pool = _pool(fetch=[row])
    result = await get_findings_by_fingerprint(pool, fp)
    assert len(result) == 1 and result[0]["fingerprint"] == fp
    assert 20 in pool.fetch.call_args.args

    pool2 = _pool(fetch=[])
    await get_findings_by_fingerprint(pool2, fp, limit=5)
    assert 5 in pool2.fetch.call_args.args
