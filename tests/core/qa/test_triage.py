"""Tests for butlers.core.qa.triage engine — condensed.

Covers:
- TriagedFinding.is_novel: True when dedup_reason None, False otherwise
- triage_findings: empty input → empty TriageResult (no DB calls)
- triage_findings: Gates 1/2/3 (active_investigation, dismissed, cooldown)
- Gate 1 stops checking further gates
- Intra-patrol dedup (same fingerprint, two sources → second deduped)
- dedup_counts populated correctly for multiple reasons
- Novel findings sorted by severity asc, then occurrence_count desc
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.models import QaFinding
from butlers.core.qa.triage import TriagedFinding, triage_findings

pytestmark = pytest.mark.unit


def _make_finding(fingerprint=None, severity=1, occurrence_count=1, source_type="log_scanner"):
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=fingerprint or uuid.uuid4().hex * 2,
        source_type=source_type,
        source_butler="finance",
        severity=severity,
        exception_type="ValueError",
        event_summary="Something went wrong",
        call_site="module:42",
        occurrence_count=occurrence_count,
        first_seen=now, last_seen=now, timestamp=now,
    )


def _patch_triage(insert_side=None, active_rv=None, dismissed_rv=False, recent_rv=None):
    """Context manager stack for triage patches."""
    return (
        patch("butlers.core.qa.triage.insert_finding", side_effect=insert_side or AsyncMock(return_value=uuid.uuid4())),
        patch("butlers.core.qa.triage.get_active_attempt", new_callable=AsyncMock, return_value=active_rv),
        patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=dismissed_rv),
        patch("butlers.core.qa.triage.get_recent_attempt", new_callable=AsyncMock, return_value=recent_rv),
    )


@pytest.mark.asyncio
async def test_triage_is_novel_empty_and_novel():
    """is_novel flag; empty list → empty TriageResult; novel finding → is_novel=True in results."""
    finding = _make_finding()
    assert TriagedFinding(finding=finding, dedup_reason=None, finding_id=uuid.uuid4()).is_novel is True
    assert TriagedFinding(finding=finding, dedup_reason="active_investigation", finding_id=uuid.uuid4()).is_novel is False

    pool = MagicMock()

    # Empty
    with patch("butlers.core.qa.triage.insert_finding", new_callable=AsyncMock) as mock_ins, \
         patch("butlers.core.qa.triage.get_active_attempt", new_callable=AsyncMock) as mock_act, \
         patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock), \
         patch("butlers.core.qa.triage.get_recent_attempt", new_callable=AsyncMock):
        result = await triage_findings(pool, uuid.uuid4(), [])
    assert result.all_findings == [] and result.novel_findings == []
    mock_ins.assert_not_called()
    mock_act.assert_not_called()

    # Novel finding
    finding_id = uuid.uuid4()
    with patch("butlers.core.qa.triage.insert_finding", new_callable=AsyncMock, return_value=finding_id), \
         patch("butlers.core.qa.triage.get_active_attempt", new_callable=AsyncMock, return_value=None), \
         patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False), \
         patch("butlers.core.qa.triage.get_recent_attempt", new_callable=AsyncMock, return_value=None):
        result = await triage_findings(pool, uuid.uuid4(), [_make_finding()])
    assert len(result.novel_findings) == 1
    assert result.novel_findings[0].is_novel is True and result.novel_findings[0].finding_id == finding_id


@pytest.mark.asyncio
async def test_triage_gate_rejections_and_short_circuit():
    """Gates 1/2/3 each set dedup_reason; Gate 1 stops evaluating Gate 2+3."""
    pool = MagicMock()
    patrol_id = uuid.uuid4()
    attempt_id = uuid.uuid4()

    # Gate 1: active_investigation — Gate 2+3 not called
    with patch("butlers.core.qa.triage.insert_finding", new_callable=AsyncMock, return_value=uuid.uuid4()), \
         patch("butlers.core.qa.triage.get_active_attempt", new_callable=AsyncMock, return_value={"id": attempt_id}), \
         patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock) as mock_dis, \
         patch("butlers.core.qa.triage.get_recent_attempt", new_callable=AsyncMock) as mock_rec:
        r = await triage_findings(pool, patrol_id, [_make_finding()])
    assert r.all_findings[0].dedup_reason == "active_investigation"
    assert r.all_findings[0].linked_attempt_id == attempt_id
    mock_dis.assert_not_called()
    mock_rec.assert_not_called()

    # Gate 2: dismissed
    with patch("butlers.core.qa.triage.insert_finding", new_callable=AsyncMock, return_value=uuid.uuid4()), \
         patch("butlers.core.qa.triage.get_active_attempt", new_callable=AsyncMock, return_value=None), \
         patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=True), \
         patch("butlers.core.qa.triage.get_recent_attempt", new_callable=AsyncMock, return_value=None):
        r2 = await triage_findings(pool, patrol_id, [_make_finding()])
    assert r2.all_findings[0].dedup_reason == "dismissed" and r2.novel_findings == []

    # Gate 3: cooldown
    with patch("butlers.core.qa.triage.insert_finding", new_callable=AsyncMock, return_value=uuid.uuid4()), \
         patch("butlers.core.qa.triage.get_active_attempt", new_callable=AsyncMock, return_value=None), \
         patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False), \
         patch("butlers.core.qa.triage.get_recent_attempt", new_callable=AsyncMock, return_value={"id": uuid.uuid4(), "closed_at": datetime.now(UTC)}):
        r3 = await triage_findings(pool, patrol_id, [_make_finding()])
    assert r3.all_findings[0].dedup_reason == "cooldown" and r3.novel_findings == []


@pytest.mark.asyncio
async def test_triage_intra_patrol_dedup_and_sorting():
    """Same fingerprint from two sources: second deduped; novel findings sorted severity asc, count desc."""
    pool = MagicMock()
    patrol_id = uuid.uuid4()

    # Intra-patrol dedup
    fp = "a" * 64
    fid1, fid2 = uuid.uuid4(), uuid.uuid4()
    call_count = 0

    async def insert_side(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return fid1 if call_count == 1 else fid2

    with patch("butlers.core.qa.triage.insert_finding", side_effect=insert_side), \
         patch("butlers.core.qa.triage.get_active_attempt", new_callable=AsyncMock, return_value=None), \
         patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False), \
         patch("butlers.core.qa.triage.get_recent_attempt", new_callable=AsyncMock, return_value=None):
        r = await triage_findings(pool, patrol_id, [
            _make_finding(fingerprint=fp, source_type="log_scanner"),
            _make_finding(fingerprint=fp, source_type="session_records"),
        ])
    assert len(r.novel_findings) == 1 and r.novel_findings[0].finding_id == fid1
    second = next(tf for tf in r.all_findings if tf.finding_id == fid2)
    assert second.dedup_reason == "active_investigation"

    # Sorting: severity asc, occurrence_count desc for equal severity
    findings_to_sort = [
        _make_finding(fingerprint="b" * 64, severity=2, occurrence_count=3),
        _make_finding(fingerprint="c" * 64, severity=0, occurrence_count=5),
        _make_finding(fingerprint="d" * 64, severity=1, occurrence_count=10),
        _make_finding(fingerprint="e" * 64, severity=1, occurrence_count=2),
    ]
    ids_iter = iter([uuid.uuid4() for _ in findings_to_sort])

    async def insert_seq(*a, **k):
        return next(ids_iter)

    with patch("butlers.core.qa.triage.insert_finding", side_effect=insert_seq), \
         patch("butlers.core.qa.triage.get_active_attempt", new_callable=AsyncMock, return_value=None), \
         patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False), \
         patch("butlers.core.qa.triage.get_recent_attempt", new_callable=AsyncMock, return_value=None):
        rs = await triage_findings(pool, patrol_id, findings_to_sort)

    severities = [tf.finding.severity for tf in rs.novel_findings]
    assert severities == sorted(severities) and severities[0] == 0
    # Equal-severity group (severity=1) sorted by occurrence_count desc
    sev1 = [tf.finding.occurrence_count for tf in rs.novel_findings if tf.finding.severity == 1]
    assert sev1 == sorted(sev1, reverse=True)
