"""The decision set view, from the real producer's rows (bu-jv1moa.27 P1).

A real broker parks real effects, the real ledger proposes the set, and the view is built from the
parked ``pending_actions`` rows through the same loader the API uses. No row is hand-filled.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from butlers.core.decision_sets import DecisionSetLedger
from butlers.modules.approvals.decision_set_view import load_set_view
from tests.capability_broker.test_broker_ledger import (
    NOTIFY,
    RULES,
    _grant,
    docker_available,
    world,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

PINNED = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
CLASSES = {NOTIFY.identity: False, RULES.identity: True}


def _note(text: str) -> dict:
    return {"channel": "telegram", "recipient": "12345", "message": text}


async def _parked_set(w, texts=("one", "two")):
    grants = (_grant(NOTIFY), _grant(RULES))
    goal = await w.goal(*grants, max_calls=20)
    lead, token = await w.invocation(goal, *grants)
    effects = []
    for text in texts:
        result = await w.broker.call(goal, token, NOTIFY.identity, _note(text))
        assert result.status == "parked", result
        effects.append(result.effect_id)
    async with w.owner.acquire() as connection, connection.transaction():
        proposed = await DecisionSetLedger(connection).create(goal, lead, effects, CLASSES)
    return goal, proposed, effects, lead, token


async def _view(w, proposed, **kw):
    # Pin the instant from the set itself (a minute before it elapses), never the live clock.
    async with w.owner.acquire() as connection:
        shown = await DecisionSetLedger(connection).read(proposed.set_id, proposed.version)
    assert shown is not None
    return await load_set_view(
        proposed.set_id,
        proposed.version,
        ledger_pool=w.owner,
        action_pools=[("messenger", w.domain)],
        now=shown.expires_at - timedelta(minutes=1),
        **kw,
    )


async def test_a_real_parked_set_is_shown_exactly_with_the_brokers_lineage(broker_db_url):
    async with world(broker_db_url) as w:
        _, proposed, effects, *_ = await _parked_set(w)
        view = await _view(w, proposed)
        assert view is not None and view.decidable and view.reason is None
        assert [i.effect_id for i in view.items] == effects
        assert [
            {f.value for f in i.facts} >= {t}
            for i, t in zip(view.items, ("one", "two"), strict=True)
        ] == [
            True,
            True,
        ]
        lineage = view.items[0].lineage
        assert lineage is not None and lineage.origin == "owner" and lineage.exposed is False
        assert view.digest == proposed.digest and view.version == proposed.version


async def test_a_call_changed_after_parking_makes_that_item_and_the_set_open_only(broker_db_url):
    async with world(broker_db_url) as w:
        _, proposed, effects, *_ = await _parked_set(w)
        await w.domain.execute(
            "UPDATE pending_actions SET tool_args = $2::jsonb WHERE id = $1",
            effects[1],
            '{"channel":"telegram","recipient":"12345","message":"edited behind the card"}',
        )
        view = await _view(w, proposed)
        assert view is not None and not view.decidable and view.reason == "items"
        assert [(i.exact, i.reason) for i in view.items] == [(True, None), (False, "changed")]
        assert view.items[1].facts == ()


async def test_an_item_decided_elsewhere_or_a_missing_row_is_open_only(broker_db_url):
    async with world(broker_db_url) as w:
        _, proposed, effects, *_ = await _parked_set(w)
        await w.domain.execute(
            "UPDATE pending_actions SET status = 'rejected' WHERE id = $1", effects[0]
        )
        await w.domain.execute("DELETE FROM pending_actions WHERE id = $1", effects[1])
        view = await _view(w, proposed)
        assert view is not None and not view.decidable
        assert [i.reason for i in view.items] == ["not_pending", "missing"]


async def test_an_unreadable_pool_marks_items_unavailable_and_is_named(broker_db_url):
    class Down:
        def acquire(self):
            raise RuntimeError("pool down")

    async with world(broker_db_url) as w:
        _, proposed, *_ = await _parked_set(w)
        named: list[str] = []
        view = await load_set_view(
            proposed.set_id,
            proposed.version,
            ledger_pool=w.owner,
            action_pools=[("messenger", Down())],
            now=PINNED,
            on_pool_failure=named.append,
        )
        assert view is not None and named == ["messenger"]
        assert {i.reason for i in view.items} == {"unavailable"} and not view.decidable


async def test_a_superseded_version_is_shown_but_never_decidable_and_unknown_is_none(
    broker_db_url,
):
    async with world(broker_db_url) as w:
        goal, first, effects, lead, token = await _parked_set(w)
        extra = await w.broker.call(goal, token, NOTIFY.identity, _note("three"))
        assert extra.status == "parked"
        async with w.owner.acquire() as connection, connection.transaction():
            second = await DecisionSetLedger(connection).create(
                goal, lead, [*effects, extra.effect_id], CLASSES
            )
        assert second.version == first.version + 1
        old = await _view(w, first)
        assert old is not None and not old.decidable and old.reason == "state"
        new = await _view(w, second)
        assert new is not None and new.decidable
        assert (
            await load_set_view(uuid4(), None, ledger_pool=w.owner, action_pools=[], now=PINNED)
            is None
        )
