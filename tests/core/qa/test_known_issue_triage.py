"""The 'tracked' triage gate: a finding linked to a bead or PR is not re-investigated."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.models import QaFinding
from butlers.core.qa.triage import triage_findings

pytestmark = pytest.mark.unit


def _finding(fp: str = "a" * 64) -> QaFinding:
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=fp,
        source_type="log_scanner",
        source_butler="chronicler",
        severity=2,
        exception_type="ValueError",
        event_summary="boom",
        call_site="mod:1",
        occurrence_count=3,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


def _link(state: str) -> dict:
    return {"id": uuid.uuid4(), "state": state, "tracker_kind": "bead", "tracker_ref": "bu-2cgabc"}


async def _run(link, *, dismissed=False, recent=None, order=None):
    inserted: list[tuple] = []

    async def insert(pool, patrol_id, finding, dedup_reason, linked_attempt_id=None):
        inserted.append((finding, dedup_reason))
        return uuid.uuid4()

    regressed = AsyncMock()
    with (
        patch("butlers.core.qa.triage.insert_finding", side_effect=insert),
        patch("butlers.core.qa.triage.get_active_attempt", AsyncMock(return_value=None)),
        patch("butlers.core.qa.triage.is_dismissed", AsyncMock(return_value=dismissed)),
        patch("butlers.core.qa.triage.get_gating_link", AsyncMock(return_value=link)),
        patch("butlers.core.qa.triage.mark_link_regressed", regressed),
        patch("butlers.core.qa.triage.get_publication_hold", AsyncMock(return_value=False)),
        patch("butlers.core.qa.triage.get_recent_attempt", AsyncMock(return_value=recent)),
    ):
        result = await triage_findings(MagicMock(), uuid.uuid4(), [_finding()], 60)
    return result, inserted, regressed


@pytest.mark.parametrize("state", ["tracked", "fix_merged"])
async def test_a_live_link_marks_the_finding_tracked_and_dispatches_nothing(state) -> None:
    result, inserted, _ = await _run(_link(state))
    assert result.novel_findings == []
    assert result.dedup_counts == {"tracked": 1}
    assert [reason for _, reason in inserted] == ["tracked"]


async def test_no_link_leaves_the_finding_novel() -> None:
    result, _, _ = await _run(None)
    assert len(result.novel_findings) == 1 and result.dedup_counts == {None: 1}


async def test_a_dismissal_outranks_a_link() -> None:
    result, _, _ = await _run(_link("tracked"), dismissed=True)
    assert result.dedup_counts == {"dismissed": 1}


async def test_a_link_outranks_the_cooldown() -> None:
    result, _, _ = await _run(_link("tracked"), recent={"closed_at": datetime.now(UTC)})
    assert result.dedup_counts == {"tracked": 1}


async def test_a_recurrence_after_the_fix_is_a_regression_carrying_the_link() -> None:
    link = _link("awaiting_recovery")
    result, inserted, regressed = await _run(link)
    assert len(result.novel_findings) == 1
    finding, reason = inserted[0]
    assert reason is None
    assert finding.structured_evidence["regression_of"] == str(link["id"])
    assert finding.structured_evidence["tracker_ref"] == "bu-2cgabc"
    regressed.assert_awaited_once()
    assert regressed.await_args.args[1] == link["id"]
