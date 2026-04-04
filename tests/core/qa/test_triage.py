"""Tests for butlers.core.qa.triage engine.

Covers:
- triage_findings: empty input returns empty TriageResult
- triage_findings: novel finding (no dedup) marked is_novel=True
- triage_findings: Gate 1 — active investigation deduplication
- triage_findings: Gate 2 — dismissed fingerprint deduplication
- triage_findings: Gate 3 — cooldown deduplication
- triage_findings: intra-patrol dedup (same fingerprint, different sources)
- triage_findings: dedup_counts dict populated correctly
- triage_findings: novel findings sorted by severity asc, occurrence_count desc
- triage_findings: multi-source same fingerprint only one novel
- TriagedFinding.is_novel property
- TriageResult.novel_findings is subset of all_findings
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.models import QaFinding
from butlers.core.qa.triage import (
    TriagedFinding,
    triage_findings,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(
    fingerprint: str | None = None,
    severity: int = 1,
    occurrence_count: int = 1,
    source_type: str = "log_scanner",
    source_butler: str = "finance",
) -> QaFinding:
    now = datetime.now(UTC)
    if fingerprint is None:
        fingerprint = uuid.uuid4().hex * 2  # 64 chars
    return QaFinding(
        fingerprint=fingerprint,
        source_type=source_type,
        source_butler=source_butler,
        severity=severity,
        exception_type="ValueError",
        event_summary="Something went wrong",
        call_site="module:42",
        occurrence_count=occurrence_count,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


def _make_pool():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# TriagedFinding.is_novel tests
# ---------------------------------------------------------------------------


def test_triaged_finding_is_novel_none_dedup():
    """TriagedFinding.is_novel is True when dedup_reason is None."""
    finding = _make_finding()
    triaged = TriagedFinding(finding=finding, dedup_reason=None, finding_id=uuid.uuid4())
    assert triaged.is_novel is True


def test_triaged_finding_is_not_novel_with_dedup_reason():
    """TriagedFinding.is_novel is False when dedup_reason is set."""
    finding = _make_finding()
    triaged = TriagedFinding(
        finding=finding, dedup_reason="active_investigation", finding_id=uuid.uuid4()
    )
    assert triaged.is_novel is False


# ---------------------------------------------------------------------------
# triage_findings tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triage_empty_findings():
    """triage_findings with empty list returns empty TriageResult."""
    pool = _make_pool()

    with (
        patch("butlers.core.qa.triage.insert_finding", new_callable=AsyncMock) as mock_insert,
        patch("butlers.core.qa.triage.get_active_attempt", new_callable=AsyncMock) as mock_active,
        patch("butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock) as mock_dismissed,
        patch(
            "butlers.core.qa.triage.get_recent_attempt", new_callable=AsyncMock
        ) as mock_recent,
    ):
        result = await triage_findings(pool, uuid.uuid4(), [])

    assert result.all_findings == []
    assert result.novel_findings == []
    assert result.dedup_counts == {}
    mock_insert.assert_not_called()
    mock_active.assert_not_called()
    mock_dismissed.assert_not_called()
    mock_recent.assert_not_called()


@pytest.mark.asyncio
async def test_triage_novel_finding():
    """A finding with no active/dismissed/cooldown match is marked novel."""
    pool = _make_pool()
    finding = _make_finding()
    finding_id = uuid.uuid4()
    patrol_id = uuid.uuid4()

    with (
        patch(
            "butlers.core.qa.triage.insert_finding",
            new_callable=AsyncMock,
            return_value=finding_id,
        ),
        patch(
            "butlers.core.qa.triage.get_active_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False
        ),
        patch(
            "butlers.core.qa.triage.get_recent_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await triage_findings(pool, patrol_id, [finding])

    assert len(result.all_findings) == 1
    assert len(result.novel_findings) == 1
    assert result.novel_findings[0].is_novel is True
    assert result.novel_findings[0].finding_id == finding_id
    assert result.dedup_counts.get(None) == 1


@pytest.mark.asyncio
async def test_triage_gate1_active_investigation():
    """Gate 1: finding deduplicated against an active healing attempt."""
    pool = _make_pool()
    finding = _make_finding()
    finding_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    patrol_id = uuid.uuid4()

    with (
        patch(
            "butlers.core.qa.triage.insert_finding",
            new_callable=AsyncMock,
            return_value=finding_id,
        ),
        patch(
            "butlers.core.qa.triage.get_active_attempt",
            new_callable=AsyncMock,
            return_value={"id": attempt_id},
        ),
        patch(
            "butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False
        ),
        patch(
            "butlers.core.qa.triage.get_recent_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await triage_findings(pool, patrol_id, [finding])

    assert len(result.all_findings) == 1
    assert result.all_findings[0].dedup_reason == "active_investigation"
    assert result.all_findings[0].linked_attempt_id == attempt_id
    assert result.novel_findings == []
    assert result.dedup_counts.get("active_investigation") == 1


@pytest.mark.asyncio
async def test_triage_gate2_dismissed():
    """Gate 2: finding deduplicated because fingerprint is dismissed."""
    pool = _make_pool()
    finding = _make_finding()
    finding_id = uuid.uuid4()
    patrol_id = uuid.uuid4()

    with (
        patch(
            "butlers.core.qa.triage.insert_finding",
            new_callable=AsyncMock,
            return_value=finding_id,
        ),
        patch(
            "butlers.core.qa.triage.get_active_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=True
        ),
        patch(
            "butlers.core.qa.triage.get_recent_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await triage_findings(pool, patrol_id, [finding])

    assert len(result.all_findings) == 1
    assert result.all_findings[0].dedup_reason == "dismissed"
    assert result.novel_findings == []
    assert result.dedup_counts.get("dismissed") == 1


@pytest.mark.asyncio
async def test_triage_gate3_cooldown():
    """Gate 3: finding deduplicated because within cooldown window."""
    pool = _make_pool()
    finding = _make_finding()
    finding_id = uuid.uuid4()
    patrol_id = uuid.uuid4()

    with (
        patch(
            "butlers.core.qa.triage.insert_finding",
            new_callable=AsyncMock,
            return_value=finding_id,
        ),
        patch(
            "butlers.core.qa.triage.get_active_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False
        ),
        patch(
            "butlers.core.qa.triage.get_recent_attempt",
            new_callable=AsyncMock,
            return_value={"id": uuid.uuid4(), "closed_at": datetime.now(UTC)},
        ),
    ):
        result = await triage_findings(pool, patrol_id, [finding])

    assert len(result.all_findings) == 1
    assert result.all_findings[0].dedup_reason == "cooldown"
    assert result.novel_findings == []
    assert result.dedup_counts.get("cooldown") == 1


@pytest.mark.asyncio
async def test_triage_intra_patrol_dedup():
    """Same fingerprint from two sources within one patrol → second is deduped."""
    pool = _make_pool()
    fp = "a" * 64
    finding1 = _make_finding(fingerprint=fp, source_type="log_scanner")
    finding2 = _make_finding(fingerprint=fp, source_type="session_records")
    patrol_id = uuid.uuid4()
    fid1 = uuid.uuid4()
    fid2 = uuid.uuid4()

    call_count = 0

    async def insert_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return fid1 if call_count == 1 else fid2

    with (
        patch("butlers.core.qa.triage.insert_finding", side_effect=insert_side_effect),
        patch(
            "butlers.core.qa.triage.get_active_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False
        ),
        patch(
            "butlers.core.qa.triage.get_recent_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await triage_findings(pool, patrol_id, [finding1, finding2])

    # First finding is novel; second is deduplicated
    assert len(result.all_findings) == 2
    assert len(result.novel_findings) == 1
    assert result.novel_findings[0].finding_id == fid1

    # Second finding has intra-patrol dedup reason
    second = next(tf for tf in result.all_findings if tf.finding_id == fid2)
    assert second.dedup_reason == "active_investigation"
    assert result.dedup_counts.get("active_investigation") == 1


@pytest.mark.asyncio
async def test_triage_dedup_counts_mixed():
    """dedup_counts tracks multiple reasons across findings."""
    pool = _make_pool()
    patrol_id = uuid.uuid4()

    finding_novel = _make_finding(fingerprint="a" * 64)
    finding_dismissed = _make_finding(fingerprint="b" * 64)
    finding_cooldown = _make_finding(fingerprint="c" * 64)

    fids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    fid_iter = iter(fids)

    async def insert_side_effect(*args, **kwargs):
        return next(fid_iter)

    async def active_side_effect(pool, fp):
        return None

    async def dismissed_side_effect(pool, fp):
        return fp == "b" * 64

    async def recent_side_effect(pool, fp, cooldown_minutes):
        if fp == "c" * 64:
            return {"id": uuid.uuid4()}
        return None

    with (
        patch("butlers.core.qa.triage.insert_finding", side_effect=insert_side_effect),
        patch("butlers.core.qa.triage.get_active_attempt", side_effect=active_side_effect),
        patch("butlers.core.qa.triage.is_dismissed", side_effect=dismissed_side_effect),
        patch("butlers.core.qa.triage.get_recent_attempt", side_effect=recent_side_effect),
    ):
        result = await triage_findings(
            pool, patrol_id, [finding_novel, finding_dismissed, finding_cooldown]
        )

    assert result.dedup_counts.get(None) == 1
    assert result.dedup_counts.get("dismissed") == 1
    assert result.dedup_counts.get("cooldown") == 1
    assert len(result.novel_findings) == 1


@pytest.mark.asyncio
async def test_triage_novel_findings_sorted_severity_asc():
    """Novel findings are sorted by severity ascending (critical=0 first)."""
    pool = _make_pool()
    patrol_id = uuid.uuid4()

    finding_medium = _make_finding(fingerprint="a" * 64, severity=2)
    finding_critical = _make_finding(fingerprint="b" * 64, severity=0)
    finding_high = _make_finding(fingerprint="c" * 64, severity=1)

    findings = [finding_medium, finding_critical, finding_high]
    fids = [uuid.uuid4() for _ in findings]
    fid_iter = iter(fids)

    async def insert_side_effect(*args, **kwargs):
        return next(fid_iter)

    with (
        patch("butlers.core.qa.triage.insert_finding", side_effect=insert_side_effect),
        patch(
            "butlers.core.qa.triage.get_active_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False
        ),
        patch(
            "butlers.core.qa.triage.get_recent_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await triage_findings(pool, patrol_id, findings)

    severities = [tf.finding.severity for tf in result.novel_findings]
    assert severities == sorted(severities), "Novel findings must be sorted by severity asc"
    assert severities[0] == 0  # critical first


@pytest.mark.asyncio
async def test_triage_novel_findings_sorted_occurrence_count_desc():
    """Equal severity findings are sorted by occurrence_count descending."""
    pool = _make_pool()
    patrol_id = uuid.uuid4()

    finding_rare = _make_finding(fingerprint="d" * 64, severity=1, occurrence_count=2)
    finding_frequent = _make_finding(fingerprint="e" * 64, severity=1, occurrence_count=10)
    finding_medium_freq = _make_finding(fingerprint="f" * 64, severity=1, occurrence_count=5)

    findings = [finding_rare, finding_frequent, finding_medium_freq]
    fids = [uuid.uuid4() for _ in findings]
    fid_iter = iter(fids)

    async def insert_side_effect(*args, **kwargs):
        return next(fid_iter)

    with (
        patch("butlers.core.qa.triage.insert_finding", side_effect=insert_side_effect),
        patch(
            "butlers.core.qa.triage.get_active_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False
        ),
        patch(
            "butlers.core.qa.triage.get_recent_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await triage_findings(pool, patrol_id, findings)

    counts = [tf.finding.occurrence_count for tf in result.novel_findings]
    assert counts == sorted(counts, reverse=True), "Equal severity findings sorted by count desc"
    assert counts[0] == 10


@pytest.mark.asyncio
async def test_triage_gate1_stops_checking_further_gates():
    """When Gate 1 triggers, Gates 2 and 3 are not evaluated."""
    pool = _make_pool()
    finding = _make_finding()
    finding_id = uuid.uuid4()
    patrol_id = uuid.uuid4()

    with (
        patch(
            "butlers.core.qa.triage.insert_finding",
            new_callable=AsyncMock,
            return_value=finding_id,
        ),
        patch(
            "butlers.core.qa.triage.get_active_attempt",
            new_callable=AsyncMock,
            return_value={"id": uuid.uuid4()},
        ),
        patch(
            "butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False
        ) as mock_dismissed,
        patch(
            "butlers.core.qa.triage.get_recent_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_recent,
    ):
        result = await triage_findings(pool, patrol_id, [finding])

    assert result.all_findings[0].dedup_reason == "active_investigation"
    # Gate 2 and 3 should NOT have been called
    mock_dismissed.assert_not_called()
    mock_recent.assert_not_called()


@pytest.mark.asyncio
async def test_triage_insert_called_for_all_findings():
    """insert_finding is called once per finding, including deduplicated ones."""
    pool = _make_pool()
    patrol_id = uuid.uuid4()

    findings = [_make_finding(fingerprint=f"{i}" * 64) for i in range(3)]
    fids = [uuid.uuid4() for _ in findings]
    fid_iter = iter(fids)

    async def insert_side_effect(*args, **kwargs):
        return next(fid_iter)

    with (
        patch("butlers.core.qa.triage.insert_finding", side_effect=insert_side_effect) as mock_ins,
        patch(
            "butlers.core.qa.triage.get_active_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False
        ),
        patch(
            "butlers.core.qa.triage.get_recent_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        result = await triage_findings(pool, patrol_id, findings)

    assert mock_ins.call_count == 3
    assert len(result.all_findings) == 3
