"""The replan undo ledger against a migrated schema (RFC 0038 D13, bu-4c7nvp.1.12).

``ReplanUndoLedger`` keeps one open offer per goal and event in
``calendar_action_log`` and parks the owner's card through the shared approvals
admission path. These run as the butler's own role on real tables.
"""

from __future__ import annotations

import asyncio
import uuid
from urllib.parse import urlparse

import pytest

from butlers.db import Database
from butlers.modules.calendar.replan_undo import ReplanUndoLedger, undo_card_args
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
MOVED = {
    "etag": '"v1"',
    "start_at": "2026-02-20T16:00:00+00:00",
    "end_at": "2026-02-20T17:00:00+00:00",
}


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


async def test_one_offer_per_goal_and_event_keeps_the_earliest_pre_image(ledger):
    store, _ = ledger
    goal = uuid.uuid4()
    assert await store.available() is True
    first = await store.open(goal_id=goal, event_id="evt-1", calendar_id="primary", pre_state=PRE)
    retry_pre = {**PRE, "start_at": MOVED["start_at"], "end_at": MOVED["end_at"]}
    again = await store.open(
        goal_id=goal, event_id="evt-1", calendar_id="primary", pre_state=retry_pre
    )
    assert first.created and not again.created
    assert again.key == first.key and again.pre_state == PRE
    other_goal = await store.open(
        goal_id=uuid.uuid4(), event_id="evt-1", calendar_id="primary", pre_state=retry_pre
    )
    assert other_goal.key != first.key


async def test_commit_parks_exactly_one_card_with_the_inverse_call(ledger):
    store, pool = ledger
    goal = uuid.uuid4()
    offer = await store.open(goal_id=goal, event_id="evt-1", calendar_id="primary", pre_state=PRE)
    card = await store.commit(offer, moved=MOVED, conflict_policy="suggest")
    again = await store.open(goal_id=goal, event_id="evt-1", calendar_id="primary", pre_state=PRE)
    later = {**MOVED, "etag": '"v2"'}
    assert await store.commit(again, moved=later, conflict_policy="suggest") == card
    rows = await pool.fetch("SELECT id, tool_name, tool_args, status FROM pending_actions")
    assert len(rows) == 1
    assert rows[0]["id"] == card and rows[0]["status"] == "pending"
    assert rows[0]["tool_name"] == "calendar_update_event"
    assert rows[0]["tool_args"] == undo_card_args(PRE, conflict_policy="suggest")
    bound = await store.for_card(card)
    assert bound is not None and bound["post_move"]["etag"] == '"v2"'
    assert await store.for_card(uuid.uuid4()) is None


async def test_a_resolved_card_closes_its_offer(ledger):
    store, pool = ledger
    goal = uuid.uuid4()
    offer = await store.open(goal_id=goal, event_id="evt-1", calendar_id="primary", pre_state=PRE)
    card = await store.commit(offer, moved=MOVED, conflict_policy="suggest")
    await pool.execute("UPDATE pending_actions SET status = 'rejected' WHERE id = $1", card)
    fresh = await store.open(goal_id=goal, event_id="evt-1", calendar_id="primary", pre_state=PRE)
    assert fresh.created and fresh.key != offer.key


async def test_discard_drops_only_an_offer_this_attempt_opened(ledger):
    store, pool = ledger
    goal = uuid.uuid4()
    offer = await store.open(goal_id=goal, event_id="evt-1", calendar_id="primary", pre_state=PRE)
    reused = await store.open(goal_id=goal, event_id="evt-1", calendar_id="primary", pre_state=PRE)
    await store.discard(reused)  # not created by this attempt: kept
    count = "SELECT count(*) FROM calendar_action_log WHERE action_type = 'replan_undo'"
    assert await pool.fetchval(count) == 1
    await store.discard(offer)
    assert await pool.fetchval(count) == 0
