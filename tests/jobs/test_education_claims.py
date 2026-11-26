"""Education's aggregated daily review claim (bu-5ii4gj.13 S5b).

The pure rule, then the real job against real Postgres: only due reviews of active maps count,
overdue reviews land on today, a failed read leaves the envelopes in place flagged incomplete.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.core.state import state_get
from butlers.jobs import calendar_overlay as job

SGT = ZoneInfo("Asia/Singapore")
TODAY = date(2030, 3, 1)
NOW = datetime(2030, 3, 1, 4, 0, tzinfo=UTC)  # 12:00 SGT


def test_one_claim_per_day_with_a_count_and_no_row_of_its_own():
    claims = job.education_claims(
        {TODAY: 3, TODAY + timedelta(days=2): 1, TODAY + timedelta(days=3): 0},
        today=TODAY,
        zone=SGT,
    )

    assert sorted(claims) == ["2030-03-01", "2030-03-03"]
    today = claims["2030-03-01"][0]
    assert today["claim_key"] == "education:reviews:2030-03-01"
    assert today["label"] == "3 reviews due" and today["presence"] == "overlay"
    assert today["priority"] == "medium"
    later = claims["2030-03-03"][0]
    assert later["label"] == "1 review due" and later["priority"] == "low"


def test_a_changed_count_changes_the_version():
    one = job.education_claims({TODAY: 1}, today=TODAY, zone=SGT)["2030-03-01"][0]
    two = job.education_claims({TODAY: 2}, today=TODAY, zone=SGT)["2030-03-01"][0]
    assert one["record_version"] != two["record_version"]


def _e2e(fn):
    for mark in (
        pytest.mark.integration,
        pytest.mark.asyncio(loop_scope="session"),
        pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    ):
        fn = mark(fn)
    return fn


async def _world(db, monkeypatch):
    pool = await db("core", "education", schemas={"education": "education"}, schema="education")
    monkeypatch.setattr("butlers.jobs.briefing._now", lambda: NOW)
    monkeypatch.setattr(job, "_utcnow", lambda: NOW)
    await pool.execute("TRUNCATE TABLE education.mind_maps CASCADE")
    return pool


async def _active_map(pool, title="A", *, status="active"):
    """An active map needs a concept first (a DB check), so it is activated after one exists."""
    map_id = await pool.fetchval(
        "INSERT INTO education.mind_maps (title) VALUES ($1) RETURNING id", title
    )
    await _node(pool, map_id, due=NOW + timedelta(days=90), status="unseen")
    await pool.execute("UPDATE education.mind_maps SET status = $2 WHERE id = $1", map_id, status)
    return map_id


async def _node(pool, map_id, *, due, status="learning"):
    await pool.execute(
        "INSERT INTO education.mind_map_nodes (mind_map_id, label, mastery_status, next_review_at) "
        "VALUES ($1, 'n', $2, $3)",
        map_id,
        status,
        due,
    )


@_e2e
async def test_only_due_reviews_of_active_maps_count_and_overdue_lands_on_today(db, monkeypatch):
    pool = await _world(db, monkeypatch)
    active = await _active_map(pool)
    abandoned = await _active_map(pool, "B", status="abandoned")
    tomorrow = NOW + timedelta(days=1)
    await _node(pool, active, due=NOW - timedelta(days=3))  # overdue: today
    await _node(pool, active, due=NOW)  # today
    await _node(pool, active, due=tomorrow)
    await _node(pool, active, due=NOW + timedelta(days=30))  # beyond the window
    await _node(pool, active, due=NOW, status="mastered")  # not a review
    await _node(pool, abandoned, due=NOW)  # inactive map
    draft = await pool.fetchval("INSERT INTO education.mind_maps (title) VALUES ('C') RETURNING id")
    await _node(pool, draft, due=NOW)  # a draft map is not being studied yet

    result = await job.run_education_calendar_overlay_contribution(pool, None)

    today_env = await state_get(pool, job.overlay_key("2030-03-01"))
    next_env = await state_get(pool, job.overlay_key("2030-03-02"))
    assert [c["label"] for c in today_env["claims"]] == ["2 reviews due"]
    assert today_env["entries"][0]["meta"] == {"count": 2}
    assert [c["label"] for c in next_env["claims"]] == ["1 review due"]
    assert result["complete"] is True and result["review_entries"] == 2


@_e2e
async def test_a_failed_read_keeps_the_envelopes_and_flags_them_incomplete(db, monkeypatch):
    pool = await _world(db, monkeypatch)
    active = await _active_map(pool)
    await _node(pool, active, due=NOW)
    await job.run_education_calendar_overlay_contribution(pool, None)
    before = await state_get(pool, job.overlay_key("2030-03-01"))

    await pool.execute("ALTER TABLE education.mind_map_nodes RENAME TO nodes_gone")
    try:
        result = await job.run_education_calendar_overlay_contribution(pool, None)
    finally:
        await pool.execute("ALTER TABLE education.nodes_gone RENAME TO mind_map_nodes")

    after = await state_get(pool, job.overlay_key("2030-03-01"))
    assert result["complete"] is False
    assert after["complete"] is False and after["claims"] == before["claims"]
