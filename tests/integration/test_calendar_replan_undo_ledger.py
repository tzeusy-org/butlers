"""The replan undo ledger against a migrated schema (RFC 0038 D13, bu-4c7nvp.1.12).

``ReplanUndoLedger`` keeps one open offer per goal and event in
``calendar_action_log`` and parks the owner's card, before the write, through the
shared approvals admission path. These run as the butler's own role on real
tables.
"""

from __future__ import annotations

import asyncio
import uuid
from urllib.parse import urlparse

import pytest

from butlers.db import Database
from butlers.modules.calendar.replan_undo import (
    ReplanUndoLedger,
    undo_card_args,
    undo_card_refused_summary,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

PRE = {
    "event_id": "evt-1",
    "calendar_id": "primary",
    "title": "BUTLER: Gym",
    "start_at": "2026-02-20T14:00:00+00:00",
    "end_at": "2026-02-20T15:00:00+00:00",
    "timezone": "UTC",
}
INTENDED = {"start_at": "2026-02-20T16:00:00+00:00", "end_at": "2026-02-20T17:00:00+00:00"}
MOVED = {"etag": '"v1"', **INTENDED}
COUNT = "SELECT count(*) FROM calendar_action_log WHERE action_type = 'replan_undo'"


@pytest.fixture
async def ledger(postgres_container):
    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core", "approvals"],
        {"core": "messenger", "approvals": "messenger"},
    )
    parsed = urlparse(db_url)
    assert parsed.hostname and parsed.port and parsed.username and parsed.password
    database = Database(
        db_name=parsed.path.lstrip("/"),
        schema="messenger",
        role="butler_messenger_rw",
        host=parsed.hostname,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        min_pool_size=1,
        max_pool_size=4,
    )
    await database.connect()
    try:
        yield ReplanUndoLedger(database.pool, butler_name="messenger"), database.pool
    finally:
        await database.close()


async def _open(store, goal, pre=PRE, intended=INTENDED):
    return await store.open(
        goal_id=goal,
        event_id="evt-1",
        calendar_id="primary",
        pre_state=pre,
        intended=intended,
        conflict_policy="suggest",
    )


async def test_open_parks_one_card_and_keeps_the_earliest_pre_image(ledger):
    store, pool = ledger
    goal = uuid.uuid4()
    assert await store.available() is True
    first = await _open(store, goal)
    retry_pre = {**PRE, "start_at": MOVED["start_at"], "end_at": MOVED["end_at"]}
    again = await _open(store, goal, pre=retry_pre)
    assert first.created and not again.created
    assert again.key == first.key and again.pre_state == PRE
    assert again.action_id == first.action_id
    rows = await pool.fetch("SELECT id, tool_name, tool_args, status FROM pending_actions")
    assert len(rows) == 1
    assert rows[0]["id"] == first.action_id and rows[0]["status"] == "pending"
    assert rows[0]["tool_name"] == "calendar_update_event"
    assert rows[0]["tool_args"] == undo_card_args(PRE, conflict_policy="suggest")
    other_goal = await _open(store, uuid.uuid4(), pre=retry_pre)
    assert other_goal.key != first.key


async def test_commit_binds_the_moved_etag_and_resolves_the_attempt(ledger):
    store, _ = ledger
    offer = await _open(store, uuid.uuid4())
    pending = await store.for_card(offer.action_id)
    assert pending is not None and pending["intended"] == INTENDED
    assert await store.commit(offer, moved=MOVED) == offer.action_id
    bound = await store.for_card(offer.action_id)
    assert bound is not None
    assert bound["post_move"]["etag"] == '"v1"' and bound["intended"] is None
    assert await store.for_card(uuid.uuid4()) is None


async def test_a_resolved_card_closes_its_offer(ledger):
    store, pool = ledger
    goal = uuid.uuid4()
    offer = await _open(store, goal)
    await store.commit(offer, moved=MOVED)
    await pool.execute(
        "UPDATE pending_actions SET status = 'rejected' WHERE id = $1", offer.action_id
    )
    fresh = await _open(store, goal)
    assert fresh.created and fresh.key != offer.key and fresh.action_id != offer.action_id


async def test_discard_cancels_the_card_it_opened_and_says_nothing_happened(ledger):
    store, pool = ledger
    offer = await _open(store, uuid.uuid4())
    await store.discard(offer)
    row = await pool.fetchrow(
        "SELECT status, agent_summary FROM pending_actions WHERE id = $1", offer.action_id
    )
    assert row["status"] == "cancelled"
    assert row["agent_summary"] == undo_card_refused_summary()
    assert await pool.fetchval(COUNT) == 0


async def test_discard_of_a_reused_offer_keeps_its_card(ledger):
    store, pool = ledger
    goal = uuid.uuid4()
    offer = await _open(store, goal)
    await store.commit(offer, moved=MOVED)
    reused = await _open(store, goal)
    await store.discard(reused)
    assert await pool.fetchval(COUNT) == 1
    status = "SELECT status FROM pending_actions WHERE id = $1"
    assert await pool.fetchval(status, offer.action_id) == "pending"
    kept = await store.for_card(offer.action_id)
    assert kept is not None and kept["intended"] is None and kept["post_move"]["etag"] == '"v1"'


async def test_discard_after_the_owner_decided_keeps_the_offer(ledger):
    store, pool = ledger
    offer = await _open(store, uuid.uuid4())
    await pool.execute(
        "UPDATE pending_actions SET status = 'approved' WHERE id = $1", offer.action_id
    )
    await store.discard(offer)
    assert await pool.fetchval(COUNT) == 1  # the approved undo still finds it and declines
    assert await store.for_card(offer.action_id) is not None
