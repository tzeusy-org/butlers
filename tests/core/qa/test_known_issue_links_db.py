"""Real-Postgres proof of the 'tracked' triage gate and the link store (bu-ejow5p.9)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from butlers.core.qa.known_issue_links import close_link, get_gating_link, upsert_link
from butlers.core.qa.models import QaFinding
from butlers.core.qa.triage import triage_findings

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _finding(fp: str) -> QaFinding:
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=fp,
        source_type="log_scanner",
        source_butler="chronicler",
        severity=2,
        exception_type="ValueError",
        event_summary="Synthetic failure",
        call_site="module:1",
        occurrence_count=4,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


async def _patrol(pool) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.qa_patrols (status) VALUES ('running') RETURNING id"
    )


def _in_a_day() -> datetime:
    return datetime.now(UTC) + timedelta(days=1)


async def test_a_linked_fingerprint_is_tracked_and_inserts_no_attempt(db_pool) -> None:
    fp = uuid.uuid4().hex * 2
    await upsert_link(
        db_pool,
        fingerprint=fp,
        tracker_kind="bead",
        tracker_ref="bu-2cgabc",
        linked_by="owner",
        expires_at=_in_a_day(),
    )
    attempts_before = await db_pool.fetchval("SELECT count(*) FROM public.healing_attempts")

    result = await triage_findings(db_pool, await _patrol(db_pool), [_finding(fp)], 60)

    assert result.novel_findings == [] and result.dedup_counts == {"tracked": 1}
    assert await db_pool.fetchval("SELECT count(*) FROM public.healing_attempts") == attempts_before
    stored = await db_pool.fetchval(
        "SELECT dedup_reason FROM public.qa_findings WHERE fingerprint = $1", fp
    )
    assert stored == "tracked"


async def test_awaiting_recovery_plus_a_recurrence_is_a_regression_of_the_link(db_pool) -> None:
    fp = uuid.uuid4().hex * 2
    link, _ = await upsert_link(
        db_pool,
        fingerprint=fp,
        tracker_kind="github_pr",
        tracker_ref="https://github.com/o/r/pull/9",
        linked_by="owner",
        expires_at=_in_a_day(),
    )
    await db_pool.execute(
        "UPDATE public.qa_known_issue_links SET state = 'awaiting_recovery' WHERE id = $1",
        link["id"],
    )

    result = await triage_findings(db_pool, await _patrol(db_pool), [_finding(fp)], 60)

    assert len(result.novel_findings) == 1
    evidence = await db_pool.fetchval(
        "SELECT structured_evidence FROM public.qa_findings WHERE fingerprint = $1", fp
    )
    assert str(link["id"]) in str(evidence)
    assert await get_gating_link(db_pool, fp) is None  # now regressed


async def test_expired_and_closed_links_do_not_gate_and_a_repeat_link_is_idempotent(
    db_pool,
) -> None:
    fp = uuid.uuid4().hex * 2
    kwargs = dict(
        fingerprint=fp,
        tracker_kind="bead",
        tracker_ref="bu-1",
        linked_by="owner",
    )
    _, created = await upsert_link(db_pool, expires_at=_in_a_day(), **kwargs)
    again, created_again = await upsert_link(db_pool, expires_at=_in_a_day(), **kwargs)
    assert created is True and created_again is False and again["state"] == "tracked"
    assert await get_gating_link(db_pool, fp) is not None

    assert await close_link(db_pool, fp, "bu-1") is True
    assert await get_gating_link(db_pool, fp) is None
    assert await close_link(db_pool, fp, "bu-1") is False

    await upsert_link(db_pool, expires_at=datetime.now(UTC) - timedelta(seconds=1), **kwargs)
    assert await get_gating_link(db_pool, fp) is None  # reopened but already expired


async def test_qa_pr_lifecycle_moves_its_link_and_never_reopens_an_owner_close(db_pool) -> None:
    from butlers.core.qa.known_issue_links import sync_qa_pr_link

    fp = uuid.uuid4().hex * 2
    pr = "https://github.com/o/r/pull/501"

    await sync_qa_pr_link(db_pool, fingerprint=fp, pr_url=pr, pr_state="open")
    link = await get_gating_link(db_pool, fp)
    assert link is not None and link["linked_by"] == "qa_auto" and link["state"] == "tracked"

    await sync_qa_pr_link(db_pool, fingerprint=fp, pr_url=pr, pr_state="merged")
    link = await get_gating_link(db_pool, fp)
    assert link is not None and link["state"] == "fix_merged" and link["fix_ref"] == pr

    # The owner closes it; a later open-PR sync must not bring it back.
    assert await close_link(db_pool, fingerprint=fp, tracker_ref=pr) is not None
    await sync_qa_pr_link(db_pool, fingerprint=fp, pr_url=pr, pr_state="open")
    assert await get_gating_link(db_pool, fp) is None


async def test_a_closed_unmerged_qa_pr_releases_its_fingerprint(db_pool) -> None:
    from butlers.core.qa.known_issue_links import sync_qa_pr_link

    fp = uuid.uuid4().hex * 2
    pr = "https://github.com/o/r/pull/502"
    await sync_qa_pr_link(db_pool, fingerprint=fp, pr_url=pr, pr_state="open")
    await sync_qa_pr_link(db_pool, fingerprint=fp, pr_url=pr, pr_state="closed")
    assert await get_gating_link(db_pool, fp) is None
