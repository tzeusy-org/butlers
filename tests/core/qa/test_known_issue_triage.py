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


async def test_a_tracked_link_marks_the_finding_tracked_and_dispatches_nothing() -> None:
    result, inserted, _ = await _run(_link("tracked"))
    assert result.novel_findings == []
    assert result.dedup_counts == {"tracked": 1}
    assert [reason for _, reason in inserted] == ["tracked"]


async def test_a_fix_merged_link_annotates_and_never_hides_a_recurrence() -> None:
    link = _link("fix_merged")
    result, inserted, regressed = await _run(link)
    assert len(result.novel_findings) == 1 and result.dedup_counts == {None: 1}
    evidence = result.novel_findings[0].finding.structured_evidence
    assert evidence["fix_merged_link"] == str(link["id"])
    assert evidence["tracker_ref"] == "bu-2cgabc"
    assert [reason for _, reason in inserted] == [None]
    regressed.assert_not_awaited()  # the link stays fix_merged until deployment evidence


async def test_an_unreadable_link_store_fails_open_with_a_warning_and_a_count(caplog) -> None:
    counter = MagicMock()
    with (
        patch("butlers.core.qa.triage.get_gating_link", AsyncMock(side_effect=OSError("db down"))),
        patch("butlers.core.qa.triage._link_failures", counter),
        patch("butlers.core.qa.triage.insert_finding", AsyncMock(return_value=uuid.uuid4())),
        patch("butlers.core.qa.triage.get_active_attempt", AsyncMock(return_value=None)),
        patch("butlers.core.qa.triage.is_dismissed", AsyncMock(return_value=False)),
        patch("butlers.core.qa.triage.get_publication_hold", AsyncMock(return_value=False)),
        patch("butlers.core.qa.triage.get_recent_attempt", AsyncMock(return_value=None)),
        caplog.at_level("WARNING"),
    ):
        result = await triage_findings(MagicMock(), uuid.uuid4(), [_finding()], 60)
    assert len(result.novel_findings) == 1 and result.dedup_counts == {None: 1}
    assert "failing open" in caplog.text
    counter.add.assert_called_once_with(1)


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


# --- QA's own pull requests keep their link in step (S2) ------------------------------------

from butlers.core.qa.known_issue_links import (  # noqa: E402
    is_valid_tracker_ref,
    sync_qa_pr_link,
)

PR = "https://github.com/o/r/pull/12"


@pytest.mark.parametrize(
    ("kind", "ref", "ok"),
    [
        ("bead", "bu-2cgabc", True),
        ("bead", "bu-ejow5p.9", True),
        ("bead", "bu-ejow5p.9.1.2", True),
        ("bead", "BU-1", False),
        ("bead", "see the ticket", False),
        ("github_pr", PR, True),
        ("github_pr", "http://github.com/o/r/pull/12", False),
        ("github_pr", "https://github.com/o/r/pull/12/files", False),
        ("github_issue", "https://github.com/o/r/issues/3", True),
        ("github_issue", PR, False),
        ("github_pr", "https://github.com/o/r/pu\tll/12", False),
        ("github_pr", "https://github.com/o/r/pull/12\n", False),
        ("github_pr", " https://github.com/o/r/pull/12", False),
        ("bead", "bu-2c\tgabc", False),
        ("email", "x", False),
    ],
)
def test_tracker_refs_must_match_their_kind(kind: str, ref: str, ok: bool) -> None:
    assert is_valid_tracker_ref(kind, ref) is ok


async def test_an_open_qa_pr_is_linked_and_a_merged_one_holds_the_fingerprint() -> None:
    pool = MagicMock(execute=AsyncMock())
    await sync_qa_pr_link(pool, fingerprint="f" * 64, pr_url=PR, pr_state="open")
    open_sql = pool.execute.await_args.args[0]
    assert "INSERT INTO public.qa_known_issue_links" in open_sql and "'qa_auto'" in open_sql
    assert "WHERE public.qa_known_issue_links.state = 'tracked'" in open_sql  # never reopens
    assert "linked_at + make_interval" in open_sql  # never extended past the cap

    pool.execute.reset_mock()
    await sync_qa_pr_link(pool, fingerprint="f" * 64, pr_url=PR, pr_state="merged")
    merged_sql = pool.execute.await_args.args[0]
    assert "state = 'fix_merged'" in merged_sql
    assert "linked_at + make_interval" in merged_sql  # never past the cap from linked_at

    pool.execute.reset_mock()
    await sync_qa_pr_link(pool, fingerprint="f" * 64, pr_url=PR, pr_state="closed")
    closed_sql = pool.execute.await_args.args[0]
    assert "state = 'closed'" in closed_sql and "linked_by = 'qa_auto'" in closed_sql


async def test_a_malformed_pr_url_gets_no_link() -> None:
    pool = MagicMock(execute=AsyncMock())
    await sync_qa_pr_link(pool, fingerprint="f" * 64, pr_url="not a url", pr_state="open")
    pool.execute.assert_not_awaited()
