"""Real-Postgres coverage for parking an overlap-resolution move (bu-5ii4gj.11 R2b)."""

from __future__ import annotations

import shutil

import pytest

from butlers.modules.approvals.execution_context import approval_tool_args_digest
from butlers.modules.calendar.resolution_move import (
    offer_for_card,
    park_resolution_move,
    resolution_card_args,
    resolution_request_id,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

CALL = {
    "event_id": "evt-1",
    "start_at": "2026-12-01T16:00:00+00:00",
    "end_at": "2026-12-01T17:00:00+00:00",
    "calendar_id": "primary",
}


async def _park(pool, *, digest="d1", etag='"v0"', option="move_block:a", authority="no_park_undo"):
    return await park_resolution_move(
        pool,
        butler_name="general",
        pair_id="pair-1",
        option_id=option,
        issue_digest=digest,
        call_args=CALL,
        etag=etag,
        authority=authority,
    )


async def test_the_card_is_the_exact_call_and_the_etag_lives_outside_it(db) -> None:
    pool = await db("core", "approvals")
    action_id, created = await _park(pool)

    row = await pool.fetchrow(
        "SELECT tool_name, tool_args, status FROM pending_actions WHERE id = $1", action_id
    )
    expected = resolution_card_args(
        CALL, request_id=resolution_request_id("pair-1", "move_block:a", "d1")
    )
    assert created and row["status"] == "pending" and row["tool_name"] == "calendar_update_event"
    assert approval_tool_args_digest(row["tool_args"]) == approval_tool_args_digest(expected)
    assert "etag" not in row["tool_args"]  # bound outside the arguments
    offer = await offer_for_card(pool, action_id)
    assert offer is not None and offer["etag"] == '"v0"'


async def test_a_repeat_returns_the_card_already_parked(db) -> None:
    pool = await db("core", "approvals")
    first, _ = await _park(pool)

    again, created = await _park(pool)

    assert again == first and not created
    count = await pool.fetchval("SELECT count(*) FROM pending_actions")
    assert count == 1


async def test_a_changed_overlap_is_a_new_card_bound_to_its_own_etag(db) -> None:
    pool = await db("core", "approvals")
    first, _ = await _park(pool)

    second, created = await _park(pool, digest="d2", etag='"v1"')

    assert second != first and created
    assert (await offer_for_card(pool, first))["etag"] == '"v0"'
    assert (await offer_for_card(pool, second))["etag"] == '"v1"'


async def test_any_other_card_has_no_binding(db) -> None:
    pool = await db("core", "approvals")
    import uuid

    assert await offer_for_card(pool, uuid.uuid4()) is None
